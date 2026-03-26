#!/usr/bin/env python3
"""
Asset Uncertainty Ranking for Binary-Option Prediction
=======================================================

Ranks 6 crypto assets (BTC, SOL, XRP, ETH, BNB, HYPE) by how much their
5m/15m return distributions deviate from normality — identifying where
Black-Scholes fails hardest and jump-diffusion models (Kou, SVJ) gain edge.

Methodology (grounded in Kończal 2024, option_paper.pdf):
  - Fetches 2 months of 1m candles from Binance
  - Aggregates to 5m and 15m log returns
  - Computes: excess kurtosis, skewness, Jarque-Bera, 2σ/3σ jump rates,
    jump kurtosis, sign-sequence entropy, vol-of-vol, relative jump size,
    ARCH LM, Hurst exponent
  - Rank-normalises scoring metrics to [0,1] across assets
  - Produces a weighted uncertainty score

Higher score = more chaotic jumps → Kou/Empirical models recommended.

Run:
    python3 asset_uncertainty_ranking.py
"""

from __future__ import annotations

import json
import math
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats
from statsmodels.stats.diagnostic import het_arch

# ── Configuration ─────────────────────────────────────────────────────────────

ASSETS = {
    "BTC":  "BTCUSDT",
    "ETH":  "ETHUSDT",
    "SOL":  "SOLUSDT",
    "XRP":  "XRPUSDT",
    "BNB":  "BNBUSDT",
    "HYPE": "HYPEUSDT",
}

HORIZONS = [5, 15]                # aggregation horizons in minutes
LOOKBACK_DAYS = 60                # 2 months of 1m candles
SIGMA_WINDOW_MIN = 360            # 6h rolling σ window (in 1m candles)
JUMP_THRESHOLDS = [2.0, 3.0]     # σ multiples for jump detection

# Scoring weights (applied to rank-normalised metrics)
SCORE_WEIGHTS = {
    "jump_rate_2s":     0.20,
    "jump_rate_3s":     0.20,
    "jump_kurtosis":    0.20,
    "sign_entropy":     0.15,
    "vol_of_vol":       0.15,
    "rel_jump_size":    0.10,
}

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_BATCH_LIMIT = 1000

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


# ── Data fetching ─────────────────────────────────────────────────────────────

def _binance_get(url: str, params: dict, timeout: float = 15.0) -> list:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode())


def fetch_1m_candles(symbol: str, days: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """Fetch `days` of 1-minute candles from Binance. Handles pagination."""
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (days * 24 * 60 * 60 * 1000)

    all_rows: list[list] = []
    cursor_ms = start_ms

    while cursor_ms < end_ms:
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": cursor_ms,
            "endTime": end_ms,
            "limit": BINANCE_BATCH_LIMIT,
        }
        batch = _binance_get(BINANCE_KLINES_URL, params)
        if not batch:
            break
        all_rows.extend(batch)
        last_close_time = int(batch[-1][6])
        cursor_ms = last_close_time + 1

        # Rate-limit politeness
        if len(batch) == BINANCE_BATCH_LIMIT:
            time.sleep(0.12)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_buy_vol",
        "taker_buy_quote_vol", "ignore",
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["volume"] = df["volume"].astype(float)
    df = df.set_index("open_time").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df


def aggregate_log_returns(df_1m: pd.DataFrame, horizon_min: int) -> pd.Series:
    """Resample 1m closes to `horizon_min` and compute log returns."""
    resampled = df_1m["close"].resample(f"{horizon_min}min").last().dropna()
    log_ret = np.log(resampled / resampled.shift(1)).dropna()
    return log_ret


# ── Metrics ───────────────────────────────────────────────────────────────────

def compute_rolling_sigma(df_1m: pd.DataFrame, window: int = SIGMA_WINDOW_MIN) -> pd.Series:
    """6h rolling σ of 1-minute log returns."""
    lr_1m = np.log(df_1m["close"] / df_1m["close"].shift(1)).dropna()
    return lr_1m.rolling(window, min_periods=max(60, window // 2)).std()


def jump_rates(
    log_returns: pd.Series,
    rolling_sigma_1m: pd.Series,
    horizon_min: int,
    thresholds: list[float] = JUMP_THRESHOLDS,
) -> dict[str, float]:
    """
    Fraction of periods where |return| > threshold × σ_horizon.
    σ_horizon = σ_1m × √horizon_min (scaling by √time).
    """
    # Align rolling σ to the horizon timestamps
    sigma_at_horizon = rolling_sigma_1m.resample(f"{horizon_min}min").last().dropna()
    sigma_scaled = sigma_at_horizon * math.sqrt(horizon_min)

    # Align
    common = log_returns.index.intersection(sigma_scaled.index)
    lr = log_returns.loc[common]
    sig = sigma_scaled.loc[common]

    # Drop zeros / NaN
    valid = (sig > 0) & sig.notna() & lr.notna()
    lr = lr[valid]
    sig = sig[valid]

    if len(lr) < 30:
        return {f"jump_rate_{t}s": np.nan for t in thresholds}

    out = {}
    for t in thresholds:
        jumps = (lr.abs() > t * sig).sum()
        out[f"jump_rate_{t}s"] = float(jumps) / len(lr)
    return out


def jump_kurtosis(
    log_returns: pd.Series,
    rolling_sigma_1m: pd.Series,
    horizon_min: int,
    threshold: float = 2.0,
) -> float:
    """Excess kurtosis of returns that qualify as jumps (|r| > 2σ)."""
    sigma_at_horizon = rolling_sigma_1m.resample(f"{horizon_min}min").last().dropna()
    sigma_scaled = sigma_at_horizon * math.sqrt(horizon_min)

    common = log_returns.index.intersection(sigma_scaled.index)
    lr = log_returns.loc[common]
    sig = sigma_scaled.loc[common]

    valid = (sig > 0) & sig.notna() & lr.notna()
    lr, sig = lr[valid], sig[valid]

    jump_mask = lr.abs() > threshold * sig
    jump_returns = lr[jump_mask]

    if len(jump_returns) < 10:
        return np.nan

    return float(sp_stats.kurtosis(jump_returns, fisher=True))


def sign_entropy(log_returns: pd.Series, block_len: int = 3) -> float:
    """
    Shannon entropy of sign-sequences (block_len consecutive up/down signs).
    1.0 = maximum randomness (pure coin flip), 0.0 = fully predictable.
    Normalised to [0, 1].
    """
    signs = np.sign(log_returns.values)
    signs = signs[signs != 0]  # drop exact zeros

    if len(signs) < block_len + 10:
        return np.nan

    # Build blocks of consecutive signs
    blocks = []
    for i in range(len(signs) - block_len + 1):
        blocks.append(tuple(signs[i:i + block_len]))

    # Count frequencies
    counts: dict[tuple, int] = defaultdict(int)
    for b in blocks:
        counts[b] += 1

    total = len(blocks)
    probs = np.array([c / total for c in counts.values()])

    # Shannon entropy, normalised by max possible entropy
    H = -np.sum(probs * np.log2(probs))
    H_max = block_len  # log2(2^block_len) = block_len
    return float(H / H_max) if H_max > 0 else 0.0


def vol_of_vol(log_returns: pd.Series, window: int = 20) -> float:
    """
    Coefficient of variation of rolling volatility.
    Higher = more unstable volatility regime.
    """
    rolling_vol = log_returns.rolling(window, min_periods=max(5, window // 2)).std().dropna()
    if len(rolling_vol) < 10 or rolling_vol.mean() <= 0:
        return np.nan
    return float(rolling_vol.std() / rolling_vol.mean())


def relative_jump_size(
    log_returns: pd.Series,
    rolling_sigma_1m: pd.Series,
    horizon_min: int,
    threshold: float = 2.0,
) -> float:
    """Mean |jump return| / σ for returns exceeding the threshold."""
    sigma_at_horizon = rolling_sigma_1m.resample(f"{horizon_min}min").last().dropna()
    sigma_scaled = sigma_at_horizon * math.sqrt(horizon_min)

    common = log_returns.index.intersection(sigma_scaled.index)
    lr = log_returns.loc[common]
    sig = sigma_scaled.loc[common]

    valid = (sig > 0) & sig.notna() & lr.notna()
    lr, sig = lr[valid], sig[valid]

    jump_mask = lr.abs() > threshold * sig
    if jump_mask.sum() < 5:
        return np.nan

    return float((lr[jump_mask].abs() / sig[jump_mask]).mean())


def hurst_exponent(series: pd.Series, max_lag: int = 100) -> float:
    """Rescaled range (R/S) Hurst exponent estimate. H≈0.5 = random walk."""
    vals = series.dropna().values
    if len(vals) < max_lag * 2:
        return np.nan

    lags = range(10, max_lag + 1)
    rs_values = []

    for lag in lags:
        n_blocks = len(vals) // lag
        if n_blocks < 1:
            continue
        rs_block = []
        for j in range(n_blocks):
            block = vals[j * lag : (j + 1) * lag]
            mean_b = np.mean(block)
            devs = np.cumsum(block - mean_b)
            R = np.max(devs) - np.min(devs)
            S = np.std(block, ddof=1)
            if S > 0:
                rs_block.append(R / S)
        if rs_block:
            rs_values.append((np.log(lag), np.log(np.mean(rs_block))))

    if len(rs_values) < 5:
        return np.nan

    x = np.array([r[0] for r in rs_values])
    y = np.array([r[1] for r in rs_values])
    slope, _, _, _, _ = sp_stats.linregress(x, y)
    return float(slope)


# ── Per-asset analysis ────────────────────────────────────────────────────────

@dataclass
class HorizonMetrics:
    horizon_min: int
    n_returns: int = 0
    excess_kurtosis: float = np.nan
    skewness: float = np.nan
    jb_stat: float = np.nan
    jb_pvalue: float = np.nan
    jump_rate_2s: float = np.nan
    jump_rate_3s: float = np.nan
    jump_kurt: float = np.nan
    sign_ent: float = np.nan
    vov: float = np.nan
    rel_jump_sz: float = np.nan
    arch_lm_stat: float = np.nan
    arch_lm_pvalue: float = np.nan
    hurst: float = np.nan
    score: float = np.nan


@dataclass
class AssetResult:
    asset: str
    symbol: str
    n_candles: int = 0
    horizons: dict[int, HorizonMetrics] = field(default_factory=dict)
    avg_score: float = np.nan


def analyse_asset(asset: str, symbol: str) -> AssetResult:
    """Fetch data + compute all metrics for one asset."""
    print(f"  Fetching {asset} ({symbol})...", end=" ", flush=True)

    df = fetch_1m_candles(symbol)
    if df.empty or len(df) < 500:
        print(f"SKIP (only {len(df)} candles)")
        return AssetResult(asset=asset, symbol=symbol, n_candles=len(df))

    print(f"{len(df):,} candles", end=" ", flush=True)

    # Rolling σ on 1m data
    rolling_sig = compute_rolling_sigma(df)

    result = AssetResult(asset=asset, symbol=symbol, n_candles=len(df))

    for h in HORIZONS:
        lr = aggregate_log_returns(df, h)
        m = HorizonMetrics(horizon_min=h, n_returns=len(lr))

        if len(lr) < 50:
            result.horizons[h] = m
            continue

        # Distribution shape
        m.excess_kurtosis = float(sp_stats.kurtosis(lr, fisher=True))
        m.skewness = float(sp_stats.skew(lr))
        jb_s, jb_p = sp_stats.jarque_bera(lr)
        m.jb_stat = float(jb_s)
        m.jb_pvalue = float(jb_p)

        # Jump metrics
        jr = jump_rates(lr, rolling_sig, h)
        m.jump_rate_2s = jr.get("jump_rate_2.0s", np.nan)
        m.jump_rate_3s = jr.get("jump_rate_3.0s", np.nan)
        m.jump_kurt = jump_kurtosis(lr, rolling_sig, h)
        m.sign_ent = sign_entropy(lr)
        m.vov = vol_of_vol(lr)
        m.rel_jump_sz = relative_jump_size(lr, rolling_sig, h)

        # ARCH LM test (volatility clustering)
        try:
            arch_stat, arch_p, _, _ = het_arch(lr.values, nlags=min(10, len(lr) // 5))
            m.arch_lm_stat = float(arch_stat)
            m.arch_lm_pvalue = float(arch_p)
        except Exception:
            pass

        # Hurst exponent
        m.hurst = hurst_exponent(lr)

        result.horizons[h] = m

    print("✓")
    return result


# ── Scoring ───────────────────────────────────────────────────────────────────

def rank_normalise(values: list[float]) -> list[float]:
    """
    Rank-normalise to [0, 1]. Higher original value → higher normalised value.
    NaN stays NaN. Ties get average rank.
    """
    arr = np.array(values, dtype=float)
    valid = ~np.isnan(arr)

    if valid.sum() <= 1:
        return [0.5 if v else np.nan for v in valid]

    ranks = np.full_like(arr, np.nan)
    order = np.argsort(arr[valid])
    n = valid.sum()

    # Average-rank for ties
    sorted_vals = arr[valid][order]
    rank_arr = np.zeros(n)
    i = 0
    while i < n:
        j = i
        while j < n and sorted_vals[j] == sorted_vals[i]:
            j += 1
        avg_rank = (i + j - 1) / 2.0
        for k in range(i, j):
            rank_arr[k] = avg_rank
        i = j

    # Normalise ranks to [0, 1]
    valid_indices = np.where(valid)[0]
    for idx, rank in zip(valid_indices[order], rank_arr):
        ranks[idx] = rank / (n - 1) if n > 1 else 0.5

    return ranks.tolist()


def compute_scores(results: list[AssetResult]) -> list[AssetResult]:
    """Rank-normalise scoring metrics across assets, compute weighted scores."""
    scoring_keys = list(SCORE_WEIGHTS.keys())

    for h in HORIZONS:
        # Collect raw metric values per asset
        raw: dict[str, list[float]] = {k: [] for k in scoring_keys}
        for r in results:
            m = r.horizons.get(h)
            if m is None:
                for k in scoring_keys:
                    raw[k].append(np.nan)
                continue
            raw["jump_rate_2s"].append(m.jump_rate_2s)
            raw["jump_rate_3s"].append(m.jump_rate_3s)
            raw["jump_kurtosis"].append(m.jump_kurt)
            raw["sign_entropy"].append(m.sign_ent)
            raw["vol_of_vol"].append(m.vov)
            raw["rel_jump_size"].append(m.rel_jump_sz)

        # Rank-normalise each metric
        normalised: dict[str, list[float]] = {}
        for k in scoring_keys:
            normalised[k] = rank_normalise(raw[k])

        # Weighted sum per asset
        for i, r in enumerate(results):
            m = r.horizons.get(h)
            if m is None:
                continue
            score = 0.0
            total_w = 0.0
            for k in scoring_keys:
                v = normalised[k][i]
                if not np.isnan(v):
                    score += SCORE_WEIGHTS[k] * v
                    total_w += SCORE_WEIGHTS[k]
            m.score = score / total_w if total_w > 0 else np.nan

    # Average score across horizons
    for r in results:
        scores = [r.horizons[h].score for h in HORIZONS if h in r.horizons and not np.isnan(r.horizons[h].score)]
        r.avg_score = float(np.mean(scores)) if scores else np.nan

    # Sort by avg_score descending
    results.sort(key=lambda r: r.avg_score if not np.isnan(r.avg_score) else -1, reverse=True)
    return results


# ── Output ────────────────────────────────────────────────────────────────────

def _f(v: float, digits: int = 4) -> str:
    if np.isnan(v):
        return "—"
    return f"{v:.{digits}f}"

def _fp(v: float) -> str:
    """Format as percentage."""
    if np.isnan(v):
        return "—"
    return f"{v*100:.2f}%"

def _fe(v: float) -> str:
    """Format scientific."""
    if np.isnan(v):
        return "—"
    return f"{v:.2e}"


def print_results(results: list[AssetResult]) -> None:
    sep = "=" * 100

    # ── Ranking table ──
    print(f"\n{sep}")
    print("  ASSET UNCERTAINTY RANKING — Binary Option Prediction Difficulty")
    print(f"  Methodology: Kończal (2024) framework | 6h rolling σ | rank-normalised scoring")
    print(f"{sep}\n")

    print(f"  {'Rank':<6}{'Asset':<8}{'Score':<10}{'Verdict':<35}{'BS fit?':<15}")
    print(f"  {'─'*6}{'─'*8}{'─'*10}{'─'*35}{'─'*15}")

    for i, r in enumerate(results, 1):
        s = r.avg_score
        if np.isnan(s):
            verdict = "Insufficient data"
            bs_fit = "—"
        elif s >= 0.70:
            verdict = "Most chaotic → Kou/Empirical"
            bs_fit = "WORST"
        elif s >= 0.45:
            verdict = "Moderately chaotic → Kou helpful"
            bs_fit = "POOR"
        elif s >= 0.25:
            verdict = "Somewhat predictable → BS okay-ish"
            bs_fit = "FAIR"
        else:
            verdict = "Most normal → BS reasonable"
            bs_fit = "BEST"

        print(f"  #{i:<5}{r.asset:<8}{_f(s, 4):<10}{verdict:<35}{bs_fit:<15}")

    # ── Per-asset detail ──
    for r in results:
        print(f"\n{'─'*100}")
        print(f"  {r.asset} ({r.symbol}) — {r.n_candles:,} 1m candles | Avg Score: {_f(r.avg_score, 4)}")
        print(f"{'─'*100}")

        for h in HORIZONS:
            m = r.horizons.get(h)
            if m is None:
                print(f"    {h}m: No data")
                continue

            print(f"\n    ── {h}-minute horizon ({m.n_returns:,} returns) ──")
            print(f"    Distribution:   kurtosis={_f(m.excess_kurtosis, 2)}  skewness={_f(m.skewness, 3)}  JB={_fe(m.jb_stat)}  JB_p={_fe(m.jb_pvalue)}")
            print(f"    Jumps:          2σ_rate={_fp(m.jump_rate_2s)}  3σ_rate={_fp(m.jump_rate_3s)}  jump_kurt={_f(m.jump_kurt, 2)}  rel_size={_f(m.rel_jump_sz, 2)}")
            print(f"    Randomness:     sign_entropy={_f(m.sign_ent, 4)}  vol_of_vol={_f(m.vov, 4)}")
            print(f"    Clustering:     ARCH_LM={_fe(m.arch_lm_stat)}  ARCH_p={_fe(m.arch_lm_pvalue)}")
            print(f"    Hurst:          H={_f(m.hurst, 3)} {'(random walk)' if m.hurst and 0.45 < m.hurst < 0.55 else '(trending)' if m.hurst and m.hurst > 0.55 else '(mean-reverting)' if m.hurst and m.hurst < 0.45 else ''}")
            print(f"    Score ({h}m):    {_f(m.score, 4)}")

    # ── Interpretation ──
    valid = [r for r in results if not np.isnan(r.avg_score)]
    if len(valid) >= 2:
        most = valid[0]
        least = valid[-1]
        print(f"\n{sep}")
        print(f"  INTERPRETATION")
        print(f"{sep}")
        print(f"  Most  chaotic:  {most.asset} (score={_f(most.avg_score, 4)}) — least predictable for binary options,")
        print(f"                  BS fails hardest, Kou/SVJ models gain biggest edge")
        print(f"  Least chaotic:  {least.asset} (score={_f(least.avg_score, 4)}) — most normal return distribution,")
        print(f"                  BS is most defensible, simpler models may suffice")
        print(f"\n  Context (Kończal 2024): Kou model reduced BTC option RMSE by 45% vs BS,")
        print(f"  SVJ reduced ETH MAPE from 10.5% to 1.9%. Assets with higher scores here")
        print(f"  would show even larger improvements from jump-diffusion models.")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    print("\n" + "=" * 100)
    print("  ASSET UNCERTAINTY RANKING")
    print(f"  Fetching {LOOKBACK_DAYS} days of 1m candles for {len(ASSETS)} assets...")
    print("=" * 100 + "\n")

    results: list[AssetResult] = []
    for asset, symbol in ASSETS.items():
        try:
            result = analyse_asset(asset, symbol)
            results.append(result)
        except Exception as exc:
            print(f"  {asset}: ERROR — {exc}")
            results.append(AssetResult(asset=asset, symbol=symbol))

    results = compute_scores(results)
    print_results(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
