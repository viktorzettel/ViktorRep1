#!/usr/bin/env python3
"""
Kou vs BS Mid-Bucket Backtest
==============================

Tests whether Kou outperforms BS at predicting binary option outcomes
when measured at different time points WITHIN each 5m bucket.

Previous test measured at t=0 (price==strike → always ~50/50, Brier ≈ 0.25).
This test measures at t+60s, t+120s, t+180s, t+240s when the price has
moved away from strike — which is where the models should actually diverge.

For each time point, we ask: "Given the current price and strike,
what P(close > strike) does each model predict?" and compare to the
actual outcome.

Usage:
    python3 kou_midpoint_backtest.py
    python3 kou_midpoint_backtest.py --symbols ETHUSDT SOLUSDT --days 5
"""

from __future__ import annotations

import argparse
import json
import math
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

# ── SSL ───────────────────────────────────────────────────────────────────────

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

# ── Config ────────────────────────────────────────────────────────────────────

CANDLE_INTERVAL_S = 10
BUCKET_S = 300                   # 5m binary option
CALIB_WINDOW_S = 6 * 3600       # 6h calibration window
MC_PATHS = 10_000
JUMP_THRESHOLD = 2.0
MIN_CALIB_CANDLES = 60

# Time offsets within each 5m bucket where we measure predictions
# (seconds after bucket start)
MEASURE_POINTS = [60, 120, 180, 240]

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


# ── Data fetching ─────────────────────────────────────────────────────────────

def _binance_get(url: str, params: dict) -> list:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode())


def fetch_1s_klines(symbol: str, days: int) -> pd.DataFrame:
    """Fetch `days` of 1-second klines from Binance."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 86400 * 1000)
    all_rows = []
    cursor_ms = start_ms
    batch_count = 0

    print(f"    Fetching ~{days * 86400:,} 1s candles ({days} days)...", end="", flush=True)

    while cursor_ms < end_ms:
        batch = _binance_get(BINANCE_KLINES_URL, {
            "symbol": symbol, "interval": "1s",
            "startTime": cursor_ms, "endTime": end_ms, "limit": 1000,
        })
        if not batch:
            break
        all_rows.extend(batch)
        cursor_ms = int(batch[-1][6]) + 1
        batch_count += 1
        if batch_count % 100 == 0:
            print(f" {len(all_rows):,}", end="", flush=True)
        time.sleep(0.05)

    print(f" → {len(all_rows):,}")

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore",
    ])
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def aggregate_ohlc(df_1s: pd.DataFrame, interval_s: int) -> pd.DataFrame:
    if interval_s == 1:
        return df_1s[["open", "high", "low", "close"]].copy()
    return df_1s.resample(f"{interval_s}s").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
    }).dropna()


# ── Kou calibration (from kou_decision_bot.py) ───────────────────────────────

@dataclass
class KouParams:
    sigma: float       # per √candle
    lam: float         # per candle
    p_up: float
    eta1: float
    eta2: float
    sigma_park: float  # Parkinson σ per √candle

    @property
    def xi(self) -> float:
        t1 = self.p_up * self.eta1 / (self.eta1 - 1.0) if self.eta1 > 1.0 else 0.0
        t2 = (1.0 - self.p_up) * self.eta2 / (self.eta2 + 1.0)
        return t1 + t2 - 1.0

    @property
    def sigma_per_sqrt_s(self) -> float:
        return self.sigma / math.sqrt(CANDLE_INTERVAL_S)


def parkinson_sigma(candles: pd.DataFrame) -> float:
    valid = candles[candles["high"] > candles["low"]]
    if len(valid) < 10:
        return 0.0
    log_hl_sq = np.log(valid["high"].values / valid["low"].values) ** 2
    return float(np.sqrt(np.sum(log_hl_sq) / (4 * len(valid) * np.log(2))))


def calibrate_kou(candles: pd.DataFrame) -> Optional[KouParams]:
    closes = candles["close"].values
    if len(closes) < MIN_CALIB_CANDLES + 1:
        return None

    log_ret = np.diff(np.log(closes))
    if len(log_ret) < MIN_CALIB_CANDLES:
        return None

    sigma_rough = float(np.std(log_ret))
    if sigma_rough <= 1e-12:
        return None

    jump_mask = np.abs(log_ret) > JUMP_THRESHOLD * sigma_rough
    non_jump = log_ret[~jump_mask]
    sigma = float(np.std(non_jump)) if len(non_jump) >= 20 else sigma_rough
    if sigma <= 1e-12:
        sigma = sigma_rough

    jump_mask = np.abs(log_ret) > JUMP_THRESHOLD * sigma
    n_jumps = int(jump_mask.sum())
    sigma_pk = parkinson_sigma(candles)

    if n_jumps < 3:
        return KouParams(sigma=sigma, lam=1e-6, p_up=0.5, eta1=10.0, eta2=10.0, sigma_park=sigma_pk)

    lam = n_jumps / len(log_ret)
    jump_returns = log_ret[jump_mask]
    up_j = jump_returns[jump_returns > 0]
    dn_j = jump_returns[jump_returns < 0]

    p_up = max(0.05, min(0.95, len(up_j) / len(jump_returns)))
    eta1 = max(1.01, 1.0 / float(np.mean(up_j))) if len(up_j) >= 2 else 10.0
    eta2 = max(0.1, 1.0 / float(np.mean(np.abs(dn_j)))) if len(dn_j) >= 2 else 10.0

    return KouParams(sigma=sigma, lam=lam, p_up=p_up, eta1=eta1, eta2=eta2, sigma_park=sigma_pk)


# ── Probability models ───────────────────────────────────────────────────────

def kou_prob_yes(
    current: float, strike: float, time_left_s: float,
    params: KouParams, rng: np.random.Generator,
) -> float:
    if time_left_s <= 0:
        return 1.0 if current >= strike else 0.0
    n_periods = time_left_s / CANDLE_INTERVAL_S
    sigma_T = params.sigma * math.sqrt(n_periods)
    lam_T = params.lam * n_periods
    xi = params.xi

    drift = -0.5 * sigma_T ** 2 - lam_T * xi
    Z = rng.standard_normal(MC_PATHS)
    n_jumps = rng.poisson(lam_T, size=MC_PATHS)
    total_jump = np.zeros(MC_PATHS)
    max_j = int(n_jumps.max()) if n_jumps.max() > 0 else 0
    for j in range(max_j):
        active = n_jumps > j
        is_up = rng.random(MC_PATHS) < params.p_up
        up_sz = rng.exponential(1.0 / params.eta1, MC_PATHS)
        dn_sz = -rng.exponential(1.0 / params.eta2, MC_PATHS)
        total_jump += np.where(active, np.where(is_up, up_sz, dn_sz), 0.0)

    log_ratio = drift + sigma_T * Z + total_jump
    threshold = math.log(strike / current)
    return float(np.clip(np.mean(log_ratio > threshold), 0.0, 1.0))


def bs_prob_yes(current: float, strike: float, time_left_s: float, sigma_ps: float) -> float:
    if time_left_s <= 0:
        return 1.0 if current >= strike else 0.0
    if sigma_ps <= 1e-12:
        return 1.0 if current >= strike else 0.0
    sigma_T = sigma_ps * math.sqrt(time_left_s)
    d = (math.log(current / strike) - 0.5 * sigma_T ** 2) / sigma_T
    return float(np.clip(0.5 * (1.0 + math.erf(d / math.sqrt(2.0))), 0.0, 1.0))


# ── Scoring ───────────────────────────────────────────────────────────────────

def brier_score(probs: list[float], outcomes: list[int]) -> float:
    return float(np.mean([(p - y) ** 2 for p, y in zip(probs, outcomes)]))


def log_loss_score(probs: list[float], outcomes: list[int], eps: float = 1e-7) -> float:
    return float(np.mean([
        -(y * math.log(max(eps, min(1 - eps, p))) + (1 - y) * math.log(max(eps, min(1 - eps, 1 - p))))
        for p, y in zip(probs, outcomes)
    ]))


def accuracy(probs: list[float], outcomes: list[int], threshold: float = 0.5) -> float:
    """Fraction of correct directional calls."""
    correct = sum(1 for p, y in zip(probs, outcomes) if (p >= threshold) == (y == 1))
    return correct / len(probs) if probs else 0.0


# ── Backtest ──────────────────────────────────────────────────────────────────

@dataclass
class TimePointResult:
    offset_s: int
    n_samples: int
    brier_kou: float
    brier_bs: float
    logloss_kou: float
    logloss_bs: float
    accuracy_kou: float
    accuracy_bs: float
    avg_abs_bps: float     # average |price - strike| in bps at this time point


def run_backtest(
    df_1s: pd.DataFrame,
    test_start: pd.Timestamp,
    rng: np.random.Generator,
) -> list[TimePointResult]:
    """Run backtest measuring at each MEASURE_POINT within 5m buckets."""
    candles_10s = aggregate_ohlc(df_1s, CANDLE_INTERVAL_S)

    # Build 5m bucket boundaries in test period
    bucket_starts = pd.date_range(
        start=test_start.ceil(f"{BUCKET_S}s"),
        end=candles_10s.index[-1] - pd.Timedelta(seconds=BUCKET_S),
        freq=f"{BUCKET_S}s", tz="UTC",
    )

    results_by_offset: dict[int, dict] = {
        off: {"kou_p": [], "bs_p": [], "outcomes": [], "abs_bps": []}
        for off in MEASURE_POINTS
    }

    n_calibrated = 0

    for bs in bucket_starts:
        be = bs + pd.Timedelta(seconds=BUCKET_S)

        # Get 1s data within bucket for precise price lookups
        bucket_1s = df_1s[(df_1s.index >= bs) & (df_1s.index < be)]
        if len(bucket_1s) < BUCKET_S * 0.5:  # skip sparse buckets
            continue

        # Strike = first close
        strike = float(bucket_1s["close"].iloc[0])
        # Actual outcome = last close > strike
        end_price = float(bucket_1s["close"].iloc[-1])
        outcome = 1 if end_price > strike else 0

        # Calibrate from preceding 6h of 10s candles
        calib_start = bs - pd.Timedelta(seconds=CALIB_WINDOW_S)
        calib_candles = candles_10s[(candles_10s.index >= calib_start) & (candles_10s.index < bs)]

        params = calibrate_kou(calib_candles)
        if params is None:
            continue
        n_calibrated += 1

        sigma_bs = params.sigma_per_sqrt_s

        # Measure at each time point
        for offset in MEASURE_POINTS:
            target_time = bs + pd.Timedelta(seconds=offset)
            time_left = float(BUCKET_S - offset)

            # Find the closest 1s candle to this time point
            nearby = bucket_1s[bucket_1s.index <= target_time]
            if nearby.empty:
                continue
            current_price = float(nearby["close"].iloc[-1])

            if current_price <= 0 or strike <= 0:
                continue

            # Predict
            k_p = kou_prob_yes(current_price, strike, time_left, params, rng)
            b_p = bs_prob_yes(current_price, strike, time_left, sigma_bs)

            abs_bps = abs(current_price - strike) / strike * 10000

            results_by_offset[offset]["kou_p"].append(k_p)
            results_by_offset[offset]["bs_p"].append(b_p)
            results_by_offset[offset]["outcomes"].append(outcome)
            results_by_offset[offset]["abs_bps"].append(abs_bps)

    # Compile results
    output: list[TimePointResult] = []
    for offset in MEASURE_POINTS:
        d = results_by_offset[offset]
        if not d["kou_p"]:
            continue
        output.append(TimePointResult(
            offset_s=offset,
            n_samples=len(d["kou_p"]),
            brier_kou=brier_score(d["kou_p"], d["outcomes"]),
            brier_bs=brier_score(d["bs_p"], d["outcomes"]),
            logloss_kou=log_loss_score(d["kou_p"], d["outcomes"]),
            logloss_bs=log_loss_score(d["bs_p"], d["outcomes"]),
            accuracy_kou=accuracy(d["kou_p"], d["outcomes"]),
            accuracy_bs=accuracy(d["bs_p"], d["outcomes"]),
            avg_abs_bps=float(np.mean(d["abs_bps"])),
        ))

    return output


# ── Output ────────────────────────────────────────────────────────────────────

def print_results(symbol: str, results: list[TimePointResult]) -> None:
    sep = "=" * 110
    print(f"\n{sep}")
    print(f"  KOU vs BS — MID-BUCKET BACKTEST — {symbol} — 5m binary options — 10s calibration")
    print(f"{sep}\n")

    print(f"  {'Time Point':<14}{'Samples':<10}{'|Δ| bps':<10}"
          f"{'Brier(Kou)':<14}{'Brier(BS)':<14}{'Kou better':<13}"
          f"{'Acc(Kou)':<12}{'Acc(BS)':<12}")
    print(f"  {'─'*14}{'─'*10}{'─'*10}{'─'*14}{'─'*14}{'─'*13}{'─'*12}{'─'*12}")

    for r in results:
        t_left = BUCKET_S - r.offset_s
        label = f"t+{r.offset_s}s ({t_left}s left)"
        brier_diff = r.brier_bs - r.brier_kou
        better = "YES" if brier_diff > 0 else "NO"
        pct = abs(brier_diff) / r.brier_bs * 100 if r.brier_bs > 0 else 0

        print(
            f"  {label:<14}{r.n_samples:<10}{r.avg_abs_bps:<10.1f}"
            f"{r.brier_kou:<14.6f}{r.brier_bs:<14.6f}"
            f"{better} ({pct:.1f}%){'':>3}"
            f"{r.accuracy_kou*100:<12.1f}{r.accuracy_bs*100:<12.1f}"
        )

    # Summary
    print(f"\n  Log-Loss comparison:")
    print(f"  {'Time Point':<14}{'LogL(Kou)':<14}{'LogL(BS)':<14}{'Kou better':<12}")
    print(f"  {'─'*14}{'─'*14}{'─'*14}{'─'*12}")
    for r in results:
        t_left = BUCKET_S - r.offset_s
        label = f"t+{r.offset_s}s ({t_left}s left)"
        better = "YES" if r.logloss_kou < r.logloss_bs else "NO"
        print(f"  {label:<14}{r.logloss_kou:<14.6f}{r.logloss_bs:<14.6f}{better:<12}")

    # Interpretation
    if results:
        best = min(results, key=lambda r: r.brier_kou)
        t_left = BUCKET_S - best.offset_s
        print(f"\n  Best Kou accuracy: {best.accuracy_kou*100:.1f}% at t+{best.offset_s}s ({t_left}s left)")
        print(f"  Average price displacement: {best.avg_abs_bps:.1f} bps from strike")

        # Check if Kou consistently beats BS
        kou_wins = sum(1 for r in results if r.brier_kou < r.brier_bs)
        print(f"  Kou beats BS at {kou_wins}/{len(results)} time points")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Kou vs BS mid-bucket backtest")
    p.add_argument("--symbols", nargs="+", default=["ETHUSDT", "SOLUSDT", "XRPUSDT"])
    p.add_argument("--days", type=int, default=3, help="Days of 1s data to fetch")
    args = p.parse_args()

    rng = np.random.default_rng(42)

    print("\n" + "=" * 110)
    print("  KOU vs BS MID-BUCKET BACKTEST")
    print(f"  Measuring predictions at t+{MEASURE_POINTS}s within each 5m bucket")
    print(f"  Calibration: 10s candles, 6h window, {MC_PATHS:,} MC paths")
    print("=" * 110)

    for symbol in args.symbols:
        print(f"\n  ── {symbol} ──")
        df_1s = fetch_1s_klines(symbol, args.days)
        if df_1s.empty:
            print(f"    SKIP: no data")
            continue

        test_start = df_1s.index[-1] - pd.Timedelta(days=1)
        print(f"    Data: {df_1s.index[0]} → {df_1s.index[-1]}")
        print(f"    Test: {test_start} → {df_1s.index[-1]}")
        print(f"    Running backtest...", flush=True)

        t0 = time.time()
        results = run_backtest(df_1s, test_start, rng)
        elapsed = time.time() - t0
        print(f"    Done in {elapsed:.1f}s")

        print_results(symbol, results)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
