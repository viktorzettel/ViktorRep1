#!/usr/bin/env python3
"""
Build historical safety priors for 5-minute binary crypto trading.

This script is intended as the next step after the current dashboard
heuristics. It ingests historical candles for ETH/XRP, derives snapshot-level
 features within each 5-minute bucket, and outputs grouped reversal / regime
 tables that can later be wired into the live safety layer.

Example:
    python3 analysis/eth_xrp_5m_safety_analyzer.py \
      --inputs ethusdt=/path/to/eth_1m.csv,xrpusdt=/path/to/xrp_1m.csv \
      --output-dir data/analysis_output_5m_safety
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
import pandas as pd

EPS = 1e-12
DEFAULT_INPUT_TIME_COLUMNS = ("open_time_iso", "open_time", "timestamp", "ts")
DEFAULT_PRICE_COLUMNS = ("open", "high", "low", "close")
MIN_KOU_CALIB_CANDLES = 30
FULL_KOU_CALIB_CANDLES = 60
DEFAULT_KOU_HISTORY_SECONDS = 2 * 3600


@dataclass
class AssetConfig:
    asset: str
    path: str


@dataclass
class LocalKouParams:
    sigma: float
    lam: float
    p_up: float
    eta1: float
    eta2: float
    mu_diffusive: float
    jump_count: int
    sample_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build ETH/XRP 5m safety priors from historical candles")
    parser.add_argument(
        "--inputs",
        required=True,
        help="Comma-separated asset=path pairs, e.g. ethusdt=data/eth.csv,xrpusdt=data/xrp.csv",
    )
    parser.add_argument(
        "--output-dir",
        default="data/analysis_output_5m_safety",
        help="Directory for generated tables and summary files",
    )
    parser.add_argument(
        "--bucket-seconds",
        type=int,
        default=300,
        help="Binary market bucket length in seconds (default: 300)",
    )
    parser.add_argument(
        "--snapshot-price",
        choices=("open", "close"),
        default="close",
        help="Snapshot price convention inside each bucket",
    )
    parser.add_argument(
        "--train-ratio",
        type=float,
        default=0.8,
        help="Time-based train ratio used for regime cutoffs (default: 0.8)",
    )
    parser.add_argument(
        "--atr-bars",
        type=int,
        default=30,
        help="Rolling ATR window in bars (default: 30)",
    )
    parser.add_argument(
        "--jump-window-bars",
        type=int,
        default=36,
        help="Rolling robust sigma window for jump-like detection in bars (default: 36)",
    )
    parser.add_argument(
        "--flip-window-bars",
        type=int,
        default=9,
        help="Rolling sign-flip window in bars (default: 9)",
    )
    parser.add_argument(
        "--jump-sigma",
        type=float,
        default=3.0,
        help="Interim jump-like threshold in local robust sigma units (default: 3.0)",
    )
    parser.add_argument(
        "--delta-bin-bps",
        type=int,
        default=10,
        help="Bin width in bps for reversal tables (default: 10)",
    )
    parser.add_argument(
        "--min-count",
        type=int,
        default=30,
        help="Minimum samples for no-go candidate rows (default: 30)",
    )
    parser.add_argument(
        "--reversal-upper",
        type=float,
        default=0.15,
        help="Upper 95%% CI threshold for candidate no-go zones (default: 0.15)",
    )
    parser.add_argument(
        "--jump-rate-upper",
        type=float,
        default=0.10,
        help="Jump-like rate threshold for candidate no-go zones (default: 0.10)",
    )
    parser.add_argument(
        "--flip-rate-upper",
        type=float,
        default=0.55,
        help="Sign-flip rate threshold for candidate no-go zones (default: 0.55)",
    )
    parser.add_argument(
        "--strong-min-count",
        type=int,
        default=500,
        help="Minimum samples for strong veto zones (default: 500)",
    )
    parser.add_argument(
        "--strong-reversal-rate",
        type=float,
        default=0.30,
        help="Minimum reversal rate for strong veto zones (default: 0.30)",
    )
    parser.add_argument(
        "--strong-reversal-upper",
        type=float,
        default=0.34,
        help="Minimum upper 95%% reversal bound for strong veto zones (default: 0.34)",
    )
    parser.add_argument(
        "--strong-max-median-delta-bps",
        type=float,
        default=8.0,
        help="Maximum median absolute delta in bps for strong veto zones (default: 8.0)",
    )
    parser.add_argument(
        "--strong-jump-rate",
        type=float,
        default=0.08,
        help="Jump-like rate threshold for strong jump/chop vetoes (default: 0.08)",
    )
    parser.add_argument(
        "--strong-flip-rate",
        type=float,
        default=0.54,
        help="Flip-rate threshold for strong jump/chop vetoes (default: 0.54)",
    )
    parser.add_argument(
        "--kou-window-bars",
        type=int,
        default=0,
        help="Trailing bar window for Kou proxy calibration; 0 means infer ~2h from bar size",
    )
    parser.add_argument(
        "--kou-mc-paths",
        type=int,
        default=256,
        help="Monte Carlo paths for historical Kou proxy probabilities (default: 256)",
    )
    parser.add_argument(
        "--kou-seed",
        type=int,
        default=7,
        help="Random seed for Kou proxy calibration (default: 7)",
    )
    parser.add_argument(
        "--prob-bin-width",
        type=float,
        default=0.05,
        help="Probability bin width for calibration tables (default: 0.05)",
    )
    parser.add_argument(
        "--calibration-min-count",
        type=int,
        default=50,
        help="Minimum samples per probability bin in calibration tables (default: 50)",
    )
    return parser.parse_args()


def parse_inputs(raw: str) -> list[AssetConfig]:
    items: list[AssetConfig] = []
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ValueError(f"Invalid input pair: {piece}")
        asset, path = piece.split("=", 1)
        items.append(AssetConfig(asset=asset.strip().lower(), path=path.strip()))
    if not items:
        raise ValueError("No input files provided")
    return items


def choose_time_column(columns: Iterable[str]) -> str:
    lowered = {col.lower(): col for col in columns}
    for candidate in DEFAULT_INPUT_TIME_COLUMNS:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    raise ValueError(f"Could not find a supported time column in {list(columns)}")


def infer_bar_seconds(index: pd.DatetimeIndex) -> int:
    if len(index) < 2:
        raise ValueError("Need at least 2 rows to infer bar size")
    diffs = index.to_series().diff().dt.total_seconds().to_numpy()
    diffs = diffs[np.isfinite(diffs)]
    diffs = diffs[diffs > 0]
    if diffs.size == 0:
        raise ValueError("Could not infer positive bar size")
    return int(round(float(np.median(diffs))))


def load_asset_frame(config: AssetConfig) -> pd.DataFrame:
    df = pd.read_csv(config.path)
    time_col = choose_time_column(df.columns)
    df[time_col] = pd.to_datetime(df[time_col], utc=True)
    df = df.sort_values(time_col).set_index(time_col)

    for col in DEFAULT_PRICE_COLUMNS:
        if col not in df.columns:
            raise ValueError(f"Missing required column {col!r} in {config.path}")
        df[col] = df[col].astype(float)

    if "volume" not in df.columns:
        df["volume"] = 0.0
    else:
        df["volume"] = df["volume"].astype(float)

    df["asset"] = config.asset
    return df[["asset", "open", "high", "low", "close", "volume"]]


def rolling_mad_sigma(series: pd.Series, window: int) -> pd.Series:
    def _mad(values: np.ndarray) -> float:
        center = np.median(values)
        mad = np.median(np.abs(values - center))
        sigma = 1.4826 * mad
        return float(sigma)

    return series.rolling(window).apply(_mad, raw=True)


def rolling_sign_flip_rate(series: pd.Series, window: int) -> pd.Series:
    def _flip_rate(values: np.ndarray) -> float:
        signs = np.sign(values)
        signs = signs[signs != 0.0]
        if signs.size < 3:
            return np.nan
        return float(np.mean(signs[1:] != signs[:-1]))

    return series.rolling(window).apply(_flip_rate, raw=True)


def wilson_interval(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        return np.nan, np.nan
    p = k / n
    denom = 1.0 + (z * z) / n
    center = (p + (z * z) / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) / n) + ((z * z) / (4.0 * n * n))) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _clamp01(value: float) -> float:
    return float(min(1.0, max(0.0, value)))


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _kou_blend_weight(sample_count: int) -> float:
    start = MIN_KOU_CALIB_CANDLES + 1
    span = max(1, FULL_KOU_CALIB_CANDLES - MIN_KOU_CALIB_CANDLES)
    return _clamp01((sample_count - start) / span)


def _sigma_seed_from_returns(log_ret: np.ndarray) -> Optional[float]:
    if log_ret.size == 0:
        return None
    center = float(np.median(log_ret))
    mad = float(np.median(np.abs(log_ret - center)))
    sigma_robust = 1.4826 * mad
    abs_centered = np.abs(log_ret - center)
    core_cutoff = float(np.quantile(abs_centered, 0.7))
    core = log_ret[abs_centered <= core_cutoff]
    sigma_core = float(np.std(core, ddof=1)) if core.size >= 20 else 0.0
    sigma_std = float(np.std(log_ret, ddof=1)) if log_ret.size >= 2 else 0.0
    sigma_seed = sigma_core if sigma_core > EPS else sigma_robust
    if sigma_seed <= EPS:
        sigma_seed = sigma_std
    return float(sigma_seed) if sigma_seed > EPS else None


def fit_local_kou_params(
    closes: np.ndarray,
    *,
    jump_threshold_sigma: float,
) -> tuple[Optional[LocalKouParams], Optional[float]]:
    if closes.size < 2 or np.any(closes <= 0.0):
        return None, None

    log_ret = np.diff(np.log(closes))
    sigma_seed = _sigma_seed_from_returns(log_ret)
    if sigma_seed is None:
        return None, None
    if closes.size < MIN_KOU_CALIB_CANDLES + 1 or log_ret.size < MIN_KOU_CALIB_CANDLES:
        return None, sigma_seed

    center = float(np.median(log_ret))
    jump_mask = np.abs(log_ret - center) > jump_threshold_sigma * sigma_seed
    non_jump = log_ret[~jump_mask]

    if non_jump.size >= 20:
        mu_diffusive = float(np.mean(non_jump))
        sigma = float(np.std(non_jump, ddof=1))
        if sigma <= EPS:
            sigma = sigma_seed
    else:
        mu_diffusive = center
        sigma = sigma_seed

    if sigma <= EPS:
        return None, sigma_seed

    jump_mask = np.abs(log_ret - mu_diffusive) > jump_threshold_sigma * sigma
    jump_residuals = log_ret[jump_mask] - mu_diffusive
    jump_count = int(jump_residuals.size)

    if jump_count < 3:
        return (
            LocalKouParams(
                sigma=float(sigma),
                lam=1e-6,
                p_up=0.5,
                eta1=12.0,
                eta2=12.0,
                mu_diffusive=float(mu_diffusive),
                jump_count=jump_count,
                sample_count=int(log_ret.size),
            ),
            sigma_seed,
        )

    up_jumps = jump_residuals[jump_residuals > 0.0]
    down_jumps = -jump_residuals[jump_residuals < 0.0]
    if up_jumps.size == 0 or down_jumps.size == 0:
        return (
            LocalKouParams(
                sigma=float(sigma),
                lam=jump_count / float(log_ret.size),
                p_up=0.5 if up_jumps.size == down_jumps.size else (0.98 if up_jumps.size > down_jumps.size else 0.02),
                eta1=12.0,
                eta2=12.0,
                mu_diffusive=float(mu_diffusive),
                jump_count=jump_count,
                sample_count=int(log_ret.size),
            ),
            sigma_seed,
        )

    p_up = min(0.98, max(0.02, up_jumps.size / jump_count))
    eta1 = min(200.0, max(1.01, 1.0 / float(np.mean(up_jumps))))
    eta2 = min(200.0, max(0.1, 1.0 / float(np.mean(down_jumps))))

    return (
        LocalKouParams(
            sigma=float(sigma),
            lam=jump_count / float(log_ret.size),
            p_up=float(p_up),
            eta1=float(eta1),
            eta2=float(eta2),
            mu_diffusive=float(mu_diffusive),
            jump_count=jump_count,
            sample_count=int(log_ret.size),
        ),
        sigma_seed,
    )


def bs_prob_yes_generic(current: float, strike: float, time_left_s: float, sigma_per_bar: float, bar_seconds: int) -> float:
    if current <= 0.0 or strike <= 0.0:
        return 0.5
    if time_left_s <= 0.0:
        return 1.0 if current >= strike else 0.0
    if sigma_per_bar <= EPS or bar_seconds <= 0:
        return 1.0 if current >= strike else 0.0
    sigma_per_sqrt_second = sigma_per_bar / math.sqrt(bar_seconds)
    sigma_t = sigma_per_sqrt_second * math.sqrt(time_left_s)
    if sigma_t <= EPS:
        return 1.0 if current >= strike else 0.0
    d2 = (math.log(current / strike) - 0.5 * sigma_t * sigma_t) / sigma_t
    return float(np.clip(_normal_cdf(d2), 0.0, 1.0))


def kou_prob_yes_mc(
    current: float,
    strike: float,
    time_left_s: float,
    params: LocalKouParams,
    *,
    bar_seconds: int,
    n_paths: int,
    rng: np.random.Generator,
) -> float:
    if current <= 0.0 or strike <= 0.0:
        return 0.5
    if time_left_s <= 0.0:
        return 1.0 if current >= strike else 0.0

    horizon = time_left_s / max(float(bar_seconds), 1.0)
    sigma2_t = params.sigma * params.sigma * horizon
    lam_t = params.lam * horizon
    drift = params.mu_diffusive * horizon - 0.5 * sigma2_t
    diffusion = math.sqrt(max(sigma2_t, 0.0))

    diffusion_draws = rng.standard_normal(n_paths)
    n_jumps = rng.poisson(lam_t, size=n_paths)
    total_jump = np.zeros(n_paths, dtype=float)

    max_jumps = int(n_jumps.max()) if n_paths > 0 else 0
    for jump_idx in range(max_jumps):
        active_idx = np.flatnonzero(n_jumps > jump_idx)
        if active_idx.size == 0:
            break
        up_mask = rng.random(active_idx.size) < params.p_up
        jump_sizes = np.empty(active_idx.size, dtype=float)
        n_up = int(np.sum(up_mask))
        if n_up:
            jump_sizes[up_mask] = rng.exponential(1.0 / params.eta1, size=n_up)
        if n_up != active_idx.size:
            jump_sizes[~up_mask] = -rng.exponential(1.0 / params.eta2, size=active_idx.size - n_up)
        total_jump[active_idx] += jump_sizes

    terminal_log_return = drift + diffusion * diffusion_draws + total_jump
    threshold = math.log(strike / current)
    return float(np.clip(np.mean(terminal_log_return > threshold), 0.0, 1.0))


def add_snapshot_features(
    df: pd.DataFrame,
    *,
    bucket_seconds: int,
    snapshot_price: str,
    atr_bars: int,
    jump_window_bars: int,
    flip_window_bars: int,
    jump_sigma: float,
) -> tuple[pd.DataFrame, int]:
    df = df.copy()
    bar_seconds = infer_bar_seconds(df.index)
    if bucket_seconds % bar_seconds != 0:
        raise ValueError(f"Bucket {bucket_seconds}s is not divisible by bar size {bar_seconds}s")

    df["log_close"] = np.log(df["close"])
    df["log_ret"] = df["log_close"].diff()
    df["abs_ret_bp"] = df["log_ret"].abs() * 10000.0

    prev_close = df["close"].shift()
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.rolling(atr_bars).mean()
    df["atr_pct"] = df["atr"] / df["close"]

    df["ret_center"] = df["log_ret"].rolling(jump_window_bars).median()
    df["ret_sigma_robust"] = rolling_mad_sigma(df["log_ret"], jump_window_bars)
    fallback_sigma = df["log_ret"].rolling(jump_window_bars).std()
    df["ret_sigma_robust"] = df["ret_sigma_robust"].where(df["ret_sigma_robust"] > EPS, fallback_sigma)
    df["jump_like"] = (
        (df["ret_sigma_robust"] > EPS)
        & ((df["log_ret"] - df["ret_center"]).abs() > jump_sigma * df["ret_sigma_robust"])
    ).astype(float)

    df["flip_rate_recent"] = rolling_sign_flip_rate(df["log_ret"], flip_window_bars)

    bucket_freq = f"{bucket_seconds}s"
    df["period_start"] = df.index.floor(bucket_freq)
    df["bar_in_period"] = df.groupby("period_start").cumcount()
    df["strike"] = df.groupby("period_start")["open"].transform("first")
    df["final_close"] = df.groupby("period_start")["close"].transform("last")
    df["price_snap"] = df[snapshot_price]

    elapsed = df["bar_in_period"] * bar_seconds
    if snapshot_price == "close":
        elapsed = elapsed + bar_seconds
    df["time_left_s"] = bucket_seconds - elapsed
    df["time_left_s"] = df["time_left_s"].clip(lower=0)

    df["final_outcome_yes"] = (df["final_close"] > df["strike"]).astype(int)
    df["delta_bps"] = (df["price_snap"] - df["strike"]) / df["strike"] * 10000.0
    df["delta_abs_bps"] = df["delta_bps"].abs()
    df["delta_sign"] = np.sign(df["delta_bps"])
    df["reversed"] = (
        ((df["delta_bps"] > 0.0) & (df["final_outcome_yes"] == 0))
        | ((df["delta_bps"] < 0.0) & (df["final_outcome_yes"] == 1))
    ).astype(int)

    hour_utc = df.index.hour
    weekday_utc = df.index.dayofweek
    df["hour_utc"] = hour_utc
    df["hour_of_week"] = weekday_utc * 24 + hour_utc

    return df, bar_seconds


def assign_regimes(df: pd.DataFrame, train_ratio: float) -> tuple[pd.DataFrame, dict[str, tuple[float, float]]]:
    out_frames: list[pd.DataFrame] = []
    thresholds: dict[str, tuple[float, float]] = {}

    for asset, asset_df in df.groupby("asset", sort=True):
        asset_df = asset_df.sort_index().copy()
        split_idx = max(1, int(len(asset_df) * train_ratio))
        train_part = asset_df.iloc[:split_idx]
        low, high = train_part["atr_pct"].dropna().quantile([0.33, 0.67]).tolist()
        thresholds[asset] = (float(low), float(high))

        def classify(value: float) -> str:
            if pd.isna(value):
                return "Unknown"
            if value <= low:
                return "Low"
            if value <= high:
                return "Med"
            return "High"

        asset_df["vol_regime"] = asset_df["atr_pct"].apply(classify)
        out_frames.append(asset_df)

    return pd.concat(out_frames).sort_index(), thresholds


def aggregate_reversal_table(df: pd.DataFrame, delta_bin_bps: int) -> pd.DataFrame:
    data = df.copy()
    data["delta_bin_bps"] = (np.round(data["delta_bps"] / delta_bin_bps) * delta_bin_bps).astype(int)

    grouped = (
        data.groupby(["asset", "vol_regime", "time_left_s", "delta_bin_bps"], observed=False)["reversed"]
        .agg(["mean", "count", "sum"])
        .reset_index()
        .rename(columns={"mean": "reversal_prob", "count": "samples", "sum": "reversal_count"})
    )

    ci_low: list[float] = []
    ci_high: list[float] = []
    for row in grouped.itertuples(index=False):
        low, high = wilson_interval(int(row.reversal_count), int(row.samples))
        ci_low.append(low)
        ci_high.append(high)
    grouped["ci_low_95"] = ci_low
    grouped["ci_high_95"] = ci_high
    grouped["hold_prob"] = 1.0 - grouped["reversal_prob"]
    return grouped.sort_values(["asset", "vol_regime", "time_left_s", "delta_bin_bps"])


def aggregate_hour_priors(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(["asset", "hour_of_week"], observed=False)
        .agg(
            samples=("reversed", "size"),
            reversal_rate=("reversed", "mean"),
            jump_like_rate=("jump_like", "mean"),
            flip_rate=("flip_rate_recent", "mean"),
            mean_abs_ret_bp=("abs_ret_bp", "mean"),
            atr_pct_mean=("atr_pct", "mean"),
        )
        .reset_index()
        .sort_values(["asset", "hour_of_week"])
    )
    return grouped


def aggregate_hour_timeleft_priors(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(["asset", "hour_of_week", "time_left_s", "vol_regime"], observed=False)
        .agg(
            samples=("reversed", "size"),
            reversal_count=("reversed", "sum"),
            reversal_rate=("reversed", "mean"),
            jump_like_rate=("jump_like", "mean"),
            flip_rate=("flip_rate_recent", "mean"),
            median_abs_delta_bps=("delta_abs_bps", "median"),
        )
        .reset_index()
        .sort_values(["asset", "hour_of_week", "time_left_s", "vol_regime"])
    )
    ci_low: list[float] = []
    ci_high: list[float] = []
    for row in grouped.itertuples(index=False):
        low, high = wilson_interval(int(row.reversal_count), int(row.samples))
        ci_low.append(low)
        ci_high.append(high)
    grouped["ci_low_95"] = ci_low
    grouped["ci_high_95"] = ci_high
    return grouped


def aggregate_hour_of_day_timeleft_priors(df: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df.groupby(["asset", "hour_utc", "time_left_s", "vol_regime"], observed=False)
        .agg(
            samples=("reversed", "size"),
            reversal_count=("reversed", "sum"),
            reversal_rate=("reversed", "mean"),
            jump_like_rate=("jump_like", "mean"),
            flip_rate=("flip_rate_recent", "mean"),
            median_abs_delta_bps=("delta_abs_bps", "median"),
        )
        .reset_index()
        .sort_values(["asset", "hour_utc", "time_left_s", "vol_regime"])
    )
    ci_low: list[float] = []
    ci_high: list[float] = []
    for row in grouped.itertuples(index=False):
        low, high = wilson_interval(int(row.reversal_count), int(row.samples))
        ci_low.append(low)
        ci_high.append(high)
    grouped["ci_low_95"] = ci_low
    grouped["ci_high_95"] = ci_high
    return grouped


def build_no_go_candidates(
    priors: pd.DataFrame,
    *,
    min_count: int,
    reversal_upper: float,
    jump_rate_upper: float,
    flip_rate_upper: float,
) -> pd.DataFrame:
    columns = [
        "asset",
        "hour_of_week",
        "time_left_s",
        "vol_regime",
        "samples",
        "reversal_rate",
        "reversal_upper_95",
        "jump_like_rate",
        "flip_rate",
        "median_abs_delta_bps",
        "reason",
    ]
    rows: list[dict[str, object]] = []
    for row in priors.itertuples(index=False):
        if int(row.samples) < min_count:
            continue
        reasons: list[str] = []
        if not pd.isna(row.ci_high_95) and float(row.ci_high_95) >= reversal_upper:
            reasons.append("high reversal")
        if not pd.isna(row.jump_like_rate) and float(row.jump_like_rate) >= jump_rate_upper:
            reasons.append("jumpy")
        if not pd.isna(row.flip_rate) and float(row.flip_rate) >= flip_rate_upper:
            reasons.append("choppy")
        if not reasons:
            continue
        rows.append(
            {
                "asset": row.asset,
                "hour_of_week": int(row.hour_of_week),
                "time_left_s": int(row.time_left_s),
                "vol_regime": row.vol_regime,
                "samples": int(row.samples),
                "reversal_rate": float(row.reversal_rate),
                "reversal_upper_95": None if pd.isna(row.ci_high_95) else float(row.ci_high_95),
                "jump_like_rate": None if pd.isna(row.jump_like_rate) else float(row.jump_like_rate),
                "flip_rate": None if pd.isna(row.flip_rate) else float(row.flip_rate),
                "median_abs_delta_bps": float(row.median_abs_delta_bps),
                "reason": ", ".join(reasons),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def build_strong_veto_zones(
    priors: pd.DataFrame,
    *,
    min_count: int,
    reversal_rate_min: float,
    reversal_upper_min: float,
    max_median_delta_bps: float,
    jump_rate_min: float,
    flip_rate_min: float,
) -> pd.DataFrame:
    columns = [
        "asset",
        "hour_utc",
        "time_left_s",
        "vol_regime",
        "samples",
        "reversal_rate",
        "reversal_upper_95",
        "jump_like_rate",
        "flip_rate",
        "median_abs_delta_bps",
        "reason",
    ]
    rows: list[dict[str, object]] = []
    for row in priors.itertuples(index=False):
        if int(row.samples) < min_count:
            continue

        fragile_reversal = (
            not pd.isna(row.reversal_rate)
            and not pd.isna(row.ci_high_95)
            and float(row.reversal_rate) >= reversal_rate_min
            and float(row.ci_high_95) >= reversal_upper_min
            and float(row.median_abs_delta_bps) <= max_median_delta_bps
        )
        jump_chop = (
            not pd.isna(row.jump_like_rate)
            and not pd.isna(row.flip_rate)
            and not pd.isna(row.ci_high_95)
            and float(row.jump_like_rate) >= jump_rate_min
            and float(row.flip_rate) >= flip_rate_min
            and float(row.ci_high_95) >= max(reversal_upper_min - 0.04, 0.0)
        )

        reasons: list[str] = []
        if fragile_reversal:
            reasons.append("fragile reversal")
        if jump_chop:
            reasons.append("jump-chop")
        if not reasons:
            continue

        rows.append(
            {
                "asset": row.asset,
                "hour_utc": int(row.hour_utc),
                "time_left_s": int(row.time_left_s),
                "vol_regime": row.vol_regime,
                "samples": int(row.samples),
                "reversal_rate": float(row.reversal_rate),
                "reversal_upper_95": float(row.ci_high_95),
                "jump_like_rate": None if pd.isna(row.jump_like_rate) else float(row.jump_like_rate),
                "flip_rate": None if pd.isna(row.flip_rate) else float(row.flip_rate),
                "median_abs_delta_bps": float(row.median_abs_delta_bps),
                "reason": ", ".join(reasons),
            }
        )
    return pd.DataFrame(rows, columns=columns)


def compute_pipeline_probabilities(
    asset_df: pd.DataFrame,
    *,
    bar_seconds: int,
    kou_window_bars: int,
    jump_sigma: float,
    kou_mc_paths: int,
    seed: int,
) -> pd.DataFrame:
    data = asset_df.sort_index().copy()
    closes = data["close"].to_numpy(dtype=float)
    price_snap = data["price_snap"].to_numpy(dtype=float)
    strike = data["strike"].to_numpy(dtype=float)
    time_left_s = data["time_left_s"].to_numpy(dtype=float)
    final_outcome_yes = data["final_outcome_yes"].to_numpy(dtype=int)
    vol_regime = data["vol_regime"].astype(str).to_numpy()

    bs_yes: list[float] = []
    raw_kou_yes: list[float] = []
    blended_kou_yes: list[float] = []
    kou_weight_values: list[float] = []
    kou_sample_counts: list[int] = []
    kou_jump_counts: list[float] = []
    kou_lambdas: list[float] = []
    kou_p_up_values: list[float] = []
    kou_sigmas: list[float] = []

    rng = np.random.default_rng(seed)

    for idx in range(len(data)):
        px = float(price_snap[idx])
        k = float(strike[idx])
        tl = float(time_left_s[idx])
        regime = vol_regime[idx]
        if tl <= 0.0 or px <= 0.0 or k <= 0.0 or regime == "Unknown":
            bs_yes.append(np.nan)
            raw_kou_yes.append(np.nan)
            blended_kou_yes.append(np.nan)
            kou_weight_values.append(np.nan)
            kou_sample_counts.append(0)
            kou_jump_counts.append(np.nan)
            kou_lambdas.append(np.nan)
            kou_p_up_values.append(np.nan)
            kou_sigmas.append(np.nan)
            continue

        start = max(0, idx + 1 - kou_window_bars)
        window_closes = closes[start : idx + 1]
        sample_count = int(window_closes.size)
        params, sigma_seed = fit_local_kou_params(window_closes, jump_threshold_sigma=jump_sigma)

        sigma_for_bs = params.sigma if params is not None else sigma_seed
        bs = (
            bs_prob_yes_generic(px, k, tl, float(sigma_for_bs), bar_seconds)
            if sigma_for_bs is not None and sigma_for_bs > EPS
            else np.nan
        )

        if params is not None:
            raw = kou_prob_yes_mc(
                px,
                k,
                tl,
                params,
                bar_seconds=bar_seconds,
                n_paths=max(32, int(kou_mc_paths)),
                rng=rng,
            )
            weight = _kou_blend_weight(sample_count)
            blended = raw * weight + bs * (1.0 - weight) if not pd.isna(bs) else raw
            kou_jump_counts.append(float(params.jump_count))
            kou_lambdas.append(float(params.lam))
            kou_p_up_values.append(float(params.p_up))
            kou_sigmas.append(float(params.sigma))
        else:
            raw = np.nan
            weight = 0.0
            blended = bs
            kou_jump_counts.append(np.nan)
            kou_lambdas.append(np.nan)
            kou_p_up_values.append(np.nan)
            kou_sigmas.append(np.nan)

        bs_yes.append(bs)
        raw_kou_yes.append(raw)
        blended_kou_yes.append(blended)
        kou_weight_values.append(float(weight))
        kou_sample_counts.append(sample_count)

    out = data[
        [
            "asset",
            "time_left_s",
            "delta_bps",
            "delta_abs_bps",
            "vol_regime",
            "hour_of_week",
            "hour_utc",
            "final_outcome_yes",
            "reversed",
            "jump_like",
            "flip_rate_recent",
            "atr_pct",
        ]
    ].copy()
    out["bs_yes"] = bs_yes
    out["raw_kou_yes"] = raw_kou_yes
    out["blended_kou_yes"] = blended_kou_yes
    out["kou_weight"] = kou_weight_values
    out["kou_sample_count"] = kou_sample_counts
    out["kou_jump_count"] = kou_jump_counts
    out["kou_lambda"] = kou_lambdas
    out["kou_p_up"] = kou_p_up_values
    out["kou_sigma_per_bar"] = kou_sigmas
    out["model_proxy"] = np.where(out["raw_kou_yes"].notna(), "kou_proxy", "bs_only")
    out["pred_yes_bs"] = (out["bs_yes"] >= 0.5).astype(float)
    out["pred_yes_blended"] = (out["blended_kou_yes"] >= 0.5).astype(float)
    out["pred_yes_raw_kou"] = np.where(out["raw_kou_yes"].notna(), (out["raw_kou_yes"] >= 0.5).astype(float), np.nan)
    out["correct_bs"] = np.where(out["bs_yes"].notna(), out["pred_yes_bs"] == final_outcome_yes, np.nan)
    out["correct_blended"] = np.where(
        out["blended_kou_yes"].notna(),
        out["pred_yes_blended"] == final_outcome_yes,
        np.nan,
    )
    out["correct_raw_kou"] = np.where(
        out["raw_kou_yes"].notna(),
        out["pred_yes_raw_kou"] == final_outcome_yes,
        np.nan,
    )
    return out


def _probability_bins(values: pd.Series, width: float) -> tuple[pd.Series, pd.Series]:
    clipped = values.clip(lower=0.0, upper=1.0 - EPS)
    left = (np.floor(clipped / width) * width).round(6)
    right = (left + width).clip(upper=1.0).round(6)
    return left, right


def aggregate_probability_calibration(
    df: pd.DataFrame,
    *,
    prob_col: str,
    model_name: str,
    prob_bin_width: float,
    min_count: int,
    by_regime: bool = False,
) -> pd.DataFrame:
    required_cols = ["asset", prob_col, "final_outcome_yes"]
    if by_regime:
        required_cols.extend(["vol_regime", "time_left_s"])

    data = df[required_cols].copy()
    data = data.dropna(subset=[prob_col])
    if data.empty:
        return pd.DataFrame()

    left, right = _probability_bins(data[prob_col], max(prob_bin_width, 0.01))
    data["prob_bin_left"] = left
    data["prob_bin_right"] = right
    data["model"] = model_name
    data["brier"] = (data[prob_col] - data["final_outcome_yes"]) ** 2
    data["pred_yes"] = (data[prob_col] >= 0.5).astype(int)
    data["directional_win"] = (data["pred_yes"] == data["final_outcome_yes"]).astype(int)

    group_cols = ["asset", "model", "prob_bin_left", "prob_bin_right"]
    if by_regime:
        group_cols = ["asset", "model", "vol_regime", "time_left_s", "prob_bin_left", "prob_bin_right"]

    grouped = (
        data.groupby(group_cols, observed=False)
        .agg(
            samples=("final_outcome_yes", "size"),
            mean_pred=(prob_col, "mean"),
            realized_yes_rate=("final_outcome_yes", "mean"),
            directional_win_rate=("directional_win", "mean"),
            brier_mean=("brier", "mean"),
        )
        .reset_index()
    )
    grouped = grouped[grouped["samples"] >= max(1, int(min_count))].copy()
    if grouped.empty:
        return grouped
    grouped["calibration_gap"] = grouped["realized_yes_rate"] - grouped["mean_pred"]
    grouped["abs_calibration_gap"] = grouped["calibration_gap"].abs()
    return grouped.sort_values(group_cols)


def build_calibration_summary(calibration_by_bin: pd.DataFrame) -> pd.DataFrame:
    if calibration_by_bin.empty:
        return pd.DataFrame(
            columns=[
                "asset",
                "model",
                "samples",
                "weighted_mean_pred",
                "weighted_realized_yes_rate",
                "ece_abs_gap",
                "brier_mean",
                "directional_win_rate",
            ]
        )

    rows: list[dict[str, object]] = []
    for (asset, model), group in calibration_by_bin.groupby(["asset", "model"], sort=True):
        weights = group["samples"].to_numpy(dtype=float)
        total = float(weights.sum())
        rows.append(
            {
                "asset": asset,
                "model": model,
                "samples": int(total),
                "weighted_mean_pred": float(np.average(group["mean_pred"], weights=weights)),
                "weighted_realized_yes_rate": float(np.average(group["realized_yes_rate"], weights=weights)),
                "ece_abs_gap": float(np.average(group["abs_calibration_gap"], weights=weights)),
                "brier_mean": float(np.average(group["brier_mean"], weights=weights)),
                "directional_win_rate": float(np.average(group["directional_win_rate"], weights=weights)),
            }
        )
    return pd.DataFrame(rows).sort_values(["asset", "model"])


def write_summary(
    *,
    output_dir: str,
    dataset: pd.DataFrame,
    thresholds: dict[str, tuple[float, float]],
    reversal_table: pd.DataFrame,
    no_go: pd.DataFrame,
    strong_veto: pd.DataFrame,
    calibration_summary: pd.DataFrame,
    bar_seconds_by_asset: dict[str, int],
    args: argparse.Namespace,
) -> None:
    summary: dict[str, object] = {
        "bucket_seconds": int(args.bucket_seconds),
        "snapshot_price": args.snapshot_price,
        "atr_bars": int(args.atr_bars),
        "jump_window_bars": int(args.jump_window_bars),
        "flip_window_bars": int(args.flip_window_bars),
        "jump_sigma": float(args.jump_sigma),
        "kou_window_bars": int(max(0, args.kou_window_bars)),
        "kou_mc_paths": int(args.kou_mc_paths),
        "prob_bin_width": float(args.prob_bin_width),
        "historical_pipeline_proxy": "1m bar proxy, not exact 10s live replay",
        "assets": {},
    }

    for asset, asset_df in dataset.groupby("asset", sort=True):
        low, high = thresholds[asset]
        subset = reversal_table[reversal_table["asset"] == asset]
        asset_calibration = calibration_summary[calibration_summary["asset"] == asset]
        calibration_blob: dict[str, object] = {}
        for row in asset_calibration.itertuples(index=False):
            calibration_blob[str(row.model)] = {
                "samples": int(row.samples),
                "weighted_mean_pred": float(row.weighted_mean_pred),
                "weighted_realized_yes_rate": float(row.weighted_realized_yes_rate),
                "ece_abs_gap": float(row.ece_abs_gap),
                "brier_mean": float(row.brier_mean),
                "directional_win_rate": float(row.directional_win_rate),
            }
        summary["assets"][asset] = {
            "rows": int(len(asset_df)),
            "bar_seconds": int(bar_seconds_by_asset[asset]),
            "start": asset_df.index.min().isoformat(),
            "end": asset_df.index.max().isoformat(),
            "atr_pct_regime_cutoffs": {"low": float(low), "high": float(high)},
            "reversal_rows": int(len(subset)),
            "candidate_no_go_rows": int(len(no_go[no_go["asset"] == asset])),
            "strong_veto_rows": int(len(strong_veto[strong_veto["asset"] == asset])),
            "probability_calibration": calibration_blob,
        }

    with open(os.path.join(output_dir, "summary.json"), "w") as handle:
        json.dump(summary, handle, indent=2)


def main() -> int:
    args = parse_args()
    inputs = parse_inputs(args.inputs)
    os.makedirs(args.output_dir, exist_ok=True)

    frames: list[pd.DataFrame] = []
    bar_seconds_by_asset: dict[str, int] = {}

    for config in inputs:
        frame = load_asset_frame(config)
        frame, bar_seconds = add_snapshot_features(
            frame,
            bucket_seconds=max(10, int(args.bucket_seconds)),
            snapshot_price=args.snapshot_price,
            atr_bars=max(5, int(args.atr_bars)),
            jump_window_bars=max(8, int(args.jump_window_bars)),
            flip_window_bars=max(4, int(args.flip_window_bars)),
            jump_sigma=float(args.jump_sigma),
        )
        frames.append(frame)
        bar_seconds_by_asset[config.asset] = bar_seconds

    dataset = pd.concat(frames).sort_index()
    dataset = dataset.replace([np.inf, -np.inf], np.nan)
    dataset, thresholds = assign_regimes(dataset, max(0.5, min(0.95, float(args.train_ratio))))

    snapshot_df = dataset[
        (dataset["time_left_s"] > 0)
        & (dataset["delta_bps"] != 0.0)
        & dataset["vol_regime"].ne("Unknown")
    ].copy()

    reversal_table = aggregate_reversal_table(snapshot_df, max(1, int(args.delta_bin_bps)))
    hour_priors = aggregate_hour_priors(snapshot_df)
    hour_timeleft_priors = aggregate_hour_timeleft_priors(snapshot_df)
    hour_of_day_timeleft_priors = aggregate_hour_of_day_timeleft_priors(snapshot_df)
    no_go = build_no_go_candidates(
        hour_timeleft_priors,
        min_count=max(1, int(args.min_count)),
        reversal_upper=float(args.reversal_upper),
        jump_rate_upper=float(args.jump_rate_upper),
        flip_rate_upper=float(args.flip_rate_upper),
    )
    strong_veto = build_strong_veto_zones(
        hour_of_day_timeleft_priors,
        min_count=max(1, int(args.strong_min_count)),
        reversal_rate_min=float(args.strong_reversal_rate),
        reversal_upper_min=float(args.strong_reversal_upper),
        max_median_delta_bps=float(args.strong_max_median_delta_bps),
        jump_rate_min=float(args.strong_jump_rate),
        flip_rate_min=float(args.strong_flip_rate),
    )

    pipeline_frames: list[pd.DataFrame] = []
    for asset, asset_snapshots in snapshot_df.groupby("asset", sort=True):
        asset_snapshots = asset_snapshots.sort_index()
        bar_seconds = int(bar_seconds_by_asset[asset])
        inferred_window = max(
            MIN_KOU_CALIB_CANDLES + 1,
            int(round(DEFAULT_KOU_HISTORY_SECONDS / max(bar_seconds, 1))),
        )
        kou_window_bars = max(
            MIN_KOU_CALIB_CANDLES + 1,
            int(args.kou_window_bars) if int(args.kou_window_bars) > 0 else inferred_window,
        )
        pipeline_frames.append(
            compute_pipeline_probabilities(
                asset_snapshots,
                bar_seconds=bar_seconds,
                kou_window_bars=kou_window_bars,
                jump_sigma=float(args.jump_sigma),
                kou_mc_paths=max(32, int(args.kou_mc_paths)),
                seed=int(args.kou_seed) + len(pipeline_frames) * 1000,
            )
        )

    pipeline_df = pd.concat(pipeline_frames).sort_index()

    calibration_tables: list[pd.DataFrame] = []
    calibration_regime_tables: list[pd.DataFrame] = []
    for prob_col, model_name in (
        ("bs_yes", "bs"),
        ("raw_kou_yes", "raw_kou_proxy"),
        ("blended_kou_yes", "blended_kou_proxy"),
    ):
        table = aggregate_probability_calibration(
            pipeline_df,
            prob_col=prob_col,
            model_name=model_name,
            prob_bin_width=float(args.prob_bin_width),
            min_count=max(1, int(args.calibration_min_count)),
            by_regime=False,
        )
        if not table.empty:
            calibration_tables.append(table)
        regime_table = aggregate_probability_calibration(
            pipeline_df,
            prob_col=prob_col,
            model_name=model_name,
            prob_bin_width=float(args.prob_bin_width),
            min_count=max(1, int(args.calibration_min_count)),
            by_regime=True,
        )
        if not regime_table.empty:
            calibration_regime_tables.append(regime_table)

    calibration_by_bin = pd.concat(calibration_tables).sort_values(["asset", "model", "prob_bin_left"])
    calibration_by_regime = pd.concat(calibration_regime_tables).sort_values(
        ["asset", "model", "vol_regime", "time_left_s", "prob_bin_left"]
    )
    calibration_summary = build_calibration_summary(calibration_by_bin)

    snapshot_df.to_csv(os.path.join(args.output_dir, "snapshot_features.csv"), index=True)
    reversal_table.to_csv(os.path.join(args.output_dir, "reversal_by_delta_regime.csv"), index=False)
    hour_priors.to_csv(os.path.join(args.output_dir, "hour_of_week_priors.csv"), index=False)
    hour_timeleft_priors.to_csv(os.path.join(args.output_dir, "hour_timeleft_regime_priors.csv"), index=False)
    hour_of_day_timeleft_priors.to_csv(
        os.path.join(args.output_dir, "hour_of_day_timeleft_regime_priors.csv"),
        index=False,
    )
    no_go.to_csv(os.path.join(args.output_dir, "candidate_no_go_zones.csv"), index=False)
    strong_veto.to_csv(os.path.join(args.output_dir, "strong_veto_zones.csv"), index=False)
    pipeline_df.to_csv(os.path.join(args.output_dir, "pipeline_snapshot_probs.csv"), index=True)
    calibration_by_bin.to_csv(os.path.join(args.output_dir, "probability_calibration_by_bin.csv"), index=False)
    calibration_by_regime.to_csv(
        os.path.join(args.output_dir, "probability_calibration_by_regime.csv"),
        index=False,
    )
    calibration_summary.to_csv(os.path.join(args.output_dir, "probability_calibration_summary.csv"), index=False)

    write_summary(
        output_dir=args.output_dir,
        dataset=dataset,
        thresholds=thresholds,
        reversal_table=reversal_table,
        no_go=no_go,
        strong_veto=strong_veto,
        calibration_summary=calibration_summary,
        bar_seconds_by_asset=bar_seconds_by_asset,
        args=args,
    )

    print("Saved outputs:")
    print(f"- {os.path.join(args.output_dir, 'snapshot_features.csv')}")
    print(f"- {os.path.join(args.output_dir, 'reversal_by_delta_regime.csv')}")
    print(f"- {os.path.join(args.output_dir, 'hour_of_week_priors.csv')}")
    print(f"- {os.path.join(args.output_dir, 'hour_timeleft_regime_priors.csv')}")
    print(f"- {os.path.join(args.output_dir, 'hour_of_day_timeleft_regime_priors.csv')}")
    print(f"- {os.path.join(args.output_dir, 'candidate_no_go_zones.csv')}")
    print(f"- {os.path.join(args.output_dir, 'strong_veto_zones.csv')}")
    print(f"- {os.path.join(args.output_dir, 'pipeline_snapshot_probs.csv')}")
    print(f"- {os.path.join(args.output_dir, 'probability_calibration_by_bin.csv')}")
    print(f"- {os.path.join(args.output_dir, 'probability_calibration_by_regime.csv')}")
    print(f"- {os.path.join(args.output_dir, 'probability_calibration_summary.csv')}")
    print(f"- {os.path.join(args.output_dir, 'summary.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
