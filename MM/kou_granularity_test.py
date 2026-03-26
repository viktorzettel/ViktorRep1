#!/usr/bin/env python3
"""
Kou Granularity Backtest
========================

Empirically tests which candle interval (1s, 5s, 10s, 30s, 60s) produces the
best-calibrated Kou binary-option probabilities for 5m horizons.

Methodology:
  1. Fetches 3 days of 1s klines from Binance
  2. Aggregates into OHLC candles at 1s, 5s, 10s, 30s, 60s
  3. For each 5m bucket on the test day:
     - Calibrate Kou from preceding 6h of candles (at each granularity)
     - Predict P(close_end > strike)
     - Compare to actual outcome
  4. Reports Brier Score, Log-Loss, and Parkinson vs close-to-close σ

Usage:
    python3 kou_granularity_test.py
    python3 kou_granularity_test.py --symbols ETHUSDT SOLUSDT
"""

from __future__ import annotations

import argparse
import json
import math
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
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

GRANULARITIES = [1, 5, 10, 30, 60]   # seconds
LOOKBACK_DAYS = 3                     # total days to fetch
CALIB_WINDOW_S = 6 * 3600            # 6h calibration window
BUCKET_S = 300                        # 5m binary option
MC_PATHS = 10_000
JUMP_THRESHOLD = 2.0
MIN_CALIB_POINTS = 30
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


# ── Data fetching ─────────────────────────────────────────────────────────────

def _binance_get(url: str, params: dict, timeout: float = 15.0) -> list:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{url}?{query}", headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode())


def fetch_1s_klines(symbol: str, days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """Fetch `days` of 1-second klines from Binance."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 24 * 60 * 60 * 1000)

    all_rows: list[list] = []
    cursor_ms = start_ms
    batch_count = 0

    total_expected = days * 86400
    print(f"    Fetching ~{total_expected:,} 1s candles ({days} days)...", end="", flush=True)

    while cursor_ms < end_ms:
        params = {
            "symbol": symbol,
            "interval": "1s",
            "startTime": cursor_ms,
            "endTime": end_ms,
            "limit": 1000,
        }
        batch = _binance_get(BINANCE_KLINES_URL, params)
        if not batch:
            break
        all_rows.extend(batch)
        cursor_ms = int(batch[-1][6]) + 1
        batch_count += 1

        if batch_count % 100 == 0:
            print(f" {len(all_rows):,}", end="", flush=True)

        # Rate limiting — stay well under Binance limits
        time.sleep(0.05)

    print(f" → {len(all_rows):,} candles")

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def aggregate_ohlc(df_1s: pd.DataFrame, interval_s: int) -> pd.DataFrame:
    """Resample 1s candles to `interval_s`-second OHLC candles."""
    if interval_s == 1:
        return df_1s[["open", "high", "low", "close", "volume"]].copy()

    rule = f"{interval_s}s"
    agg = df_1s.resample(rule).agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna()
    return agg


# ── Kou calibration ──────────────────────────────────────────────────────────

@dataclass
class KouParams:
    sigma: float
    lam: float        # jumps per candle period (not per second)
    p_up: float
    eta1: float
    eta2: float
    interval_s: int   # candle interval in seconds

    @property
    def xi(self) -> float:
        t1 = self.p_up * self.eta1 / (self.eta1 - 1.0) if self.eta1 > 1.0 else 0.0
        t2 = (1.0 - self.p_up) * self.eta2 / (self.eta2 + 1.0)
        return t1 + t2 - 1.0

    @property
    def sigma_per_sqrt_s(self) -> float:
        """Convert σ per √interval to σ per √second."""
        return self.sigma / math.sqrt(self.interval_s) if self.interval_s > 0 else self.sigma

    @property
    def lam_per_s(self) -> float:
        """Jump intensity per second."""
        return self.lam / self.interval_s if self.interval_s > 0 else self.lam


def calibrate_kou(
    candles: pd.DataFrame, interval_s: int
) -> Optional[KouParams]:
    """Calibrate Kou from close-to-close log returns of OHLC candles."""
    closes = candles["close"].values
    if len(closes) < MIN_CALIB_POINTS + 1:
        return None

    log_ret = np.diff(np.log(closes))
    if len(log_ret) < MIN_CALIB_POINTS:
        return None

    # Pass 1: rough σ
    sigma_rough = float(np.std(log_ret))
    if sigma_rough <= 1e-12:
        return None

    # Pass 2: identify jumps
    jump_mask = np.abs(log_ret) > JUMP_THRESHOLD * sigma_rough

    # Pass 3: refined σ from non-jump returns
    non_jump = log_ret[~jump_mask]
    sigma = float(np.std(non_jump)) if len(non_jump) >= 20 else sigma_rough
    if sigma <= 1e-12:
        sigma = sigma_rough

    # Re-identify jumps with refined σ
    jump_mask = np.abs(log_ret) > JUMP_THRESHOLD * sigma
    n_jumps = int(jump_mask.sum())

    if n_jumps < 3:
        return KouParams(
            sigma=sigma, lam=1e-6, p_up=0.5, eta1=10.0, eta2=10.0,
            interval_s=interval_s,
        )

    # Jump intensity: jumps per candle period
    lam = n_jumps / len(log_ret)

    jump_returns = log_ret[jump_mask]
    up_jumps = jump_returns[jump_returns > 0]
    down_jumps = jump_returns[jump_returns < 0]

    p_up = max(0.05, min(0.95, len(up_jumps) / len(jump_returns)))

    # Decay rates
    if len(up_jumps) >= 2:
        eta1 = 1.0 / float(np.mean(up_jumps))
        eta1 = max(1.01, eta1)
    else:
        eta1 = 10.0

    if len(down_jumps) >= 2:
        eta2 = 1.0 / float(np.mean(np.abs(down_jumps)))
        eta2 = max(0.1, eta2)
    else:
        eta2 = 10.0

    return KouParams(
        sigma=sigma, lam=lam, p_up=p_up, eta1=eta1, eta2=eta2,
        interval_s=interval_s,
    )


# ── Parkinson volatility ─────────────────────────────────────────────────────

def parkinson_sigma(candles: pd.DataFrame) -> float:
    """
    Parkinson (1980) high-low range volatility estimator.
    σ_P = √(1/(4·n·ln2) · Σ ln(H_i/L_i)²)

    Returns σ per candle period.
    ~5x more efficient than close-to-close estimator.
    """
    highs = candles["high"].values
    lows = candles["low"].values

    # Filter out candles where H == L (no range)
    valid = highs > lows
    h = highs[valid]
    l = lows[valid]

    if len(h) < 10:
        return 0.0

    log_hl_sq = np.log(h / l) ** 2
    return float(np.sqrt(np.sum(log_hl_sq) / (4 * len(h) * np.log(2))))


# ── Monte Carlo probability ──────────────────────────────────────────────────

def kou_prob_yes(
    current: float, strike: float, time_left_s: float,
    params: KouParams, n_paths: int = MC_PATHS, rng: np.random.Generator = None,
) -> float:
    """Monte Carlo P(S_T > K) under Kou, time_left in seconds."""
    if time_left_s <= 0:
        return 1.0 if current >= strike else 0.0
    if rng is None:
        rng = np.random.default_rng()

    # Convert per-candle params to the simulation time horizon
    n_periods = time_left_s / params.interval_s
    sigma_T = params.sigma * math.sqrt(n_periods)
    lam_T = params.lam * n_periods
    xi = params.xi

    drift = -0.5 * sigma_T * sigma_T - lam_T * xi
    diffusion = sigma_T

    Z = rng.standard_normal(n_paths)
    n_jumps = rng.poisson(lam_T, size=n_paths)

    total_jump = np.zeros(n_paths)
    max_j = int(n_jumps.max()) if n_jumps.max() > 0 else 0

    for j in range(max_j):
        active = n_jumps > j
        is_up = rng.random(n_paths) < params.p_up
        up_sz = rng.exponential(1.0 / params.eta1, n_paths)
        dn_sz = -rng.exponential(1.0 / params.eta2, n_paths)
        total_jump += np.where(active, np.where(is_up, up_sz, dn_sz), 0.0)

    log_ratio = drift + diffusion * Z + total_jump
    threshold = math.log(strike / current)

    return float(np.mean(log_ratio > threshold))


def bs_prob_yes(current: float, strike: float, time_left_s: float, sigma_per_sqrt_s: float) -> float:
    """BS P(S_T > K) using d₂, with σ per √second."""
    if time_left_s <= 0:
        return 1.0 if current >= strike else 0.0
    if sigma_per_sqrt_s <= 1e-12:
        return 1.0 if current >= strike else 0.0
    sigma_T = sigma_per_sqrt_s * math.sqrt(time_left_s)
    d = (math.log(current / strike) - 0.5 * sigma_T * sigma_T) / sigma_T
    return max(0.0, min(1.0, 0.5 * (1.0 + math.erf(d / math.sqrt(2.0)))))


# ── Backtest ──────────────────────────────────────────────────────────────────

@dataclass
class BucketResult:
    bucket_start: float
    strike: float
    end_price: float
    outcome: int            # 1 if end_price > strike, else 0
    kou_prob: float
    bs_prob: float


@dataclass
class GranularityResult:
    interval_s: int
    n_buckets: int
    brier_kou: float
    brier_bs: float
    logloss_kou: float
    logloss_bs: float
    sigma_cc: float         # close-to-close σ (per √s)
    sigma_pk: float         # Parkinson σ (per √s)
    avg_lambda: float       # average jump rate (per second)
    avg_n_jumps_bucket: float  # expected jumps per 5m bucket


def brier_score(probs: list[float], outcomes: list[int]) -> float:
    return float(np.mean([(p - y) ** 2 for p, y in zip(probs, outcomes)]))


def log_loss(probs: list[float], outcomes: list[int], eps: float = 1e-7) -> float:
    ll = []
    for p, y in zip(probs, outcomes):
        p = max(eps, min(1 - eps, p))
        ll.append(-(y * math.log(p) + (1 - y) * math.log(1 - p)))
    return float(np.mean(ll))


def run_backtest_for_granularity(
    df_1s: pd.DataFrame,
    interval_s: int,
    test_start: pd.Timestamp,
    rng: np.random.Generator,
) -> GranularityResult:
    """Run full backtest for one granularity."""
    candles = aggregate_ohlc(df_1s, interval_s)

    # Identify 5m buckets in the test period
    test_candles = candles[candles.index >= test_start]
    if test_candles.empty:
        return GranularityResult(
            interval_s=interval_s, n_buckets=0,
            brier_kou=1.0, brier_bs=1.0, logloss_kou=10.0, logloss_bs=10.0,
            sigma_cc=0.0, sigma_pk=0.0, avg_lambda=0.0, avg_n_jumps_bucket=0.0,
        )

    # Build 5m bucket boundaries
    bucket_starts = pd.date_range(
        start=test_start.floor(f"{BUCKET_S}s"),
        end=candles.index[-1],
        freq=f"{BUCKET_S}s",
        tz="UTC",
    )

    results: list[BucketResult] = []
    all_lambdas: list[float] = []
    all_sigma_cc: list[float] = []
    all_sigma_pk: list[float] = []

    for bs in bucket_starts:
        be = bs + pd.Timedelta(seconds=BUCKET_S)

        # Get candles in this bucket
        bucket_candles = candles[(candles.index >= bs) & (candles.index < be)]
        if len(bucket_candles) < 2:
            continue

        strike = float(bucket_candles["close"].iloc[0])
        end_price = float(bucket_candles["close"].iloc[-1])
        outcome = 1 if end_price > strike else 0

        # Calibration window: preceding 6h
        calib_start = bs - pd.Timedelta(seconds=CALIB_WINDOW_S)
        calib_candles = candles[(candles.index >= calib_start) & (candles.index < bs)]

        if len(calib_candles) < MIN_CALIB_POINTS:
            continue

        # Calibrate Kou
        params = calibrate_kou(calib_candles, interval_s)
        if params is None:
            continue

        # Parkinson σ
        pk_sigma = parkinson_sigma(calib_candles)
        pk_sigma_per_sqrt_s = pk_sigma / math.sqrt(interval_s) if interval_s > 0 else pk_sigma

        all_lambdas.append(params.lam_per_s)
        all_sigma_cc.append(params.sigma_per_sqrt_s)
        all_sigma_pk.append(pk_sigma_per_sqrt_s)

        # Time left = full bucket
        time_left = float(BUCKET_S)

        # Kou P(YES)
        kou_p = kou_prob_yes(strike, strike, time_left, params, MC_PATHS, rng)
        # Note: at t=0 price == strike, so we're predicting from even money

        # Actually, use the strike as starting price (P(close > open))
        # The strike IS the open price, end_price is the close
        kou_p = kou_prob_yes(strike, strike, time_left, params, MC_PATHS, rng)

        # BS P(YES) using close-to-close σ
        bs_p = bs_prob_yes(strike, strike, time_left, params.sigma_per_sqrt_s)

        results.append(BucketResult(
            bucket_start=bs.timestamp(),
            strike=strike,
            end_price=end_price,
            outcome=outcome,
            kou_prob=kou_p,
            bs_prob=bs_p,
        ))

    if not results:
        return GranularityResult(
            interval_s=interval_s, n_buckets=0,
            brier_kou=1.0, brier_bs=1.0, logloss_kou=10.0, logloss_bs=10.0,
            sigma_cc=0.0, sigma_pk=0.0, avg_lambda=0.0, avg_n_jumps_bucket=0.0,
        )

    kou_probs = [r.kou_prob for r in results]
    bs_probs = [r.bs_prob for r in results]
    outcomes = [r.outcome for r in results]

    avg_lam = float(np.mean(all_lambdas)) if all_lambdas else 0.0

    return GranularityResult(
        interval_s=interval_s,
        n_buckets=len(results),
        brier_kou=brier_score(kou_probs, outcomes),
        brier_bs=brier_score(bs_probs, outcomes),
        logloss_kou=log_loss(kou_probs, outcomes),
        logloss_bs=log_loss(bs_probs, outcomes),
        sigma_cc=float(np.mean(all_sigma_cc)) if all_sigma_cc else 0.0,
        sigma_pk=float(np.mean(all_sigma_pk)) if all_sigma_pk else 0.0,
        avg_lambda=avg_lam,
        avg_n_jumps_bucket=avg_lam * BUCKET_S,
    )


# ── Output ────────────────────────────────────────────────────────────────────

def print_results(symbol: str, results: list[GranularityResult]) -> None:
    sep = "=" * 100

    print(f"\n{sep}")
    print(f"  GRANULARITY BACKTEST — {symbol} — 5m binary options")
    print(f"{sep}\n")

    # Main table
    print(f"  {'Interval':<10}{'Buckets':<10}{'Brier(Kou)':<14}{'Brier(BS)':<14}{'LogL(Kou)':<14}{'LogL(BS)':<14}{'Kou wins?':<12}")
    print(f"  {'─'*10}{'─'*10}{'─'*14}{'─'*14}{'─'*14}{'─'*14}{'─'*12}")

    best_brier = min(r.brier_kou for r in results if r.n_buckets > 0)

    for r in results:
        if r.n_buckets == 0:
            print(f"  {r.interval_s:>3}s{'':>6}{'No data':<14}")
            continue

        marker = " ◀ BEST" if r.brier_kou == best_brier else ""
        kou_wins = "YES" if r.brier_kou < r.brier_bs else "NO"

        print(
            f"  {r.interval_s:>3}s{'':>6}"
            f"{r.n_buckets:<10}"
            f"{r.brier_kou:<14.6f}"
            f"{r.brier_bs:<14.6f}"
            f"{r.logloss_kou:<14.6f}"
            f"{r.logloss_bs:<14.6f}"
            f"{kou_wins:<12}"
            f"{marker}"
        )

    # Volatility comparison
    print(f"\n  {'Interval':<10}{'σ_cc (√s)':<16}{'σ_Park (√s)':<16}{'Park/CC':<12}{'λ (/s)':<14}{'E[jumps/5m]':<14}")
    print(f"  {'─'*10}{'─'*16}{'─'*16}{'─'*12}{'─'*14}{'─'*14}")

    for r in results:
        if r.n_buckets == 0:
            continue
        ratio = r.sigma_pk / r.sigma_cc if r.sigma_cc > 0 else 0.0
        print(
            f"  {r.interval_s:>3}s{'':>6}"
            f"{r.sigma_cc:<16.8f}"
            f"{r.sigma_pk:<16.8f}"
            f"{ratio:<12.3f}"
            f"{r.avg_lambda:<14.6f}"
            f"{r.avg_n_jumps_bucket:<14.1f}"
        )

    # Interpretation
    valid = [r for r in results if r.n_buckets > 0]
    if valid:
        best = min(valid, key=lambda r: r.brier_kou)
        worst = max(valid, key=lambda r: r.brier_kou)
        print(f"\n  Best granularity:  {best.interval_s}s  (Brier={best.brier_kou:.6f})")
        print(f"  Worst granularity: {worst.interval_s}s  (Brier={worst.brier_kou:.6f})")
        improvement = (worst.brier_kou - best.brier_kou) / worst.brier_kou * 100
        print(f"  Improvement:       {improvement:.1f}% Brier reduction")

        # Parkinson insight
        cc_ratios = [r.sigma_pk / r.sigma_cc for r in valid if r.sigma_cc > 0]
        if cc_ratios:
            avg_ratio = float(np.mean(cc_ratios))
            print(f"\n  Parkinson/CC σ ratio (avg): {avg_ratio:.3f}")
            if avg_ratio > 1.2:
                print(f"  → Parkinson captures significant intra-candle moves missed by close-to-close")
            elif avg_ratio < 0.8:
                print(f"  → Close-to-close overestimates σ (likely microstructure noise)")
            else:
                print(f"  → Both estimators roughly agree (good sign)")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="Kou granularity backtest")
    p.add_argument(
        "--symbols", nargs="+", default=["ETHUSDT", "SOLUSDT", "XRPUSDT"],
        help="Binance symbols to test (default: ETHUSDT SOLUSDT XRPUSDT)",
    )
    p.add_argument("--days", type=int, default=LOOKBACK_DAYS, help="Days of 1s data")
    p.add_argument("--mc-paths", type=int, default=MC_PATHS, help="Monte Carlo paths")
    args = p.parse_args()

    mc_paths = args.mc_paths

    rng = np.random.default_rng(42)  # reproducible

    print("\n" + "=" * 100)
    print("  KOU GRANULARITY BACKTEST")
    print(f"  Testing intervals: {GRANULARITIES}s")
    print(f"  Fetching {args.days} days of 1s klines for {args.symbols}")
    print("=" * 100)

    for symbol in args.symbols:
        print(f"\n  ── {symbol} ──")
        df_1s = fetch_1s_klines(symbol, args.days)
        if df_1s.empty:
            print(f"    SKIP: no data for {symbol}")
            continue

        # Test day = last day
        total_span = df_1s.index[-1] - df_1s.index[0]
        test_start = df_1s.index[-1] - pd.Timedelta(days=1)
        print(f"    Data span: {df_1s.index[0]} → {df_1s.index[-1]}")
        print(f"    Test period: {test_start} → {df_1s.index[-1]}")
        print(f"    Running backtest...", flush=True)

        results: list[GranularityResult] = []
        for g in GRANULARITIES:
            print(f"      {g:>3}s...", end=" ", flush=True)
            t0 = time.time()
            r = run_backtest_for_granularity(df_1s, g, test_start, rng)
            elapsed = time.time() - t0
            print(f"{r.n_buckets} buckets, {elapsed:.1f}s")
            results.append(r)

        print_results(symbol, results)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
