#!/usr/bin/env python3
"""
Analyze ETH/XRP 5-minute binary-option microstructure from 1-second OHLC data.

This is the higher-resolution successor to the 1-minute safety analyzer. It is
designed to answer the questions that 1m data cannot:

- what happens inside the last 90s / 60s / 30s?
- where do near-strike reversals cluster?
- how do volatility, jumps, and chop behave by hour and time-left?
- what 10s/15s/30s structures are relevant to the live Kou bot?
"""

from __future__ import annotations

import argparse
import json
import math
import os
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

EPS = 1e-12
DEFAULT_INPUT_TIME_COLUMNS = ("open_time_iso", "open_time", "timestamp", "ts")
DEFAULT_PRICE_COLUMNS = ("open", "high", "low", "close")


@dataclass
class AssetConfig:
    asset: str
    path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze ETH/XRP 5m microstructure from 1s candles")
    parser.add_argument(
        "--inputs",
        required=True,
        help="Comma-separated asset=path pairs, e.g. ethusdt=data/eth_1s.csv.gz,xrpusdt=data/xrp_1s.csv.gz",
    )
    parser.add_argument(
        "--output-dir",
        default="data/analysis_output_5m_microstructure",
        help="Directory for generated tables and summaries",
    )
    parser.add_argument(
        "--bucket-seconds",
        type=int,
        default=300,
        help="Binary market bucket length in seconds (default: 300)",
    )
    parser.add_argument(
        "--snapshot-step-seconds",
        type=int,
        default=15,
        help="Snapshot spacing within each 5m bucket (default: 15)",
    )
    parser.add_argument(
        "--near-strike-bps",
        type=float,
        default=10.0,
        help="Near-strike threshold in bps for heatmaps (default: 10)",
    )
    parser.add_argument(
        "--delta-bin-bps",
        type=float,
        default=5.0,
        help="Delta bin width in bps for reversal tables (default: 5)",
    )
    parser.add_argument(
        "--local-sigma-window",
        type=int,
        default=300,
        help="1s rolling window for robust sigma / jump detection (default: 300)",
    )
    parser.add_argument(
        "--jump-sigma",
        type=float,
        default=4.0,
        help="Jump-like threshold in rolling robust sigma units on 1s returns (default: 4.0)",
    )
    parser.add_argument(
        "--rv-short-window",
        type=int,
        default=30,
        help="Short microstructure window in seconds (default: 30)",
    )
    parser.add_argument(
        "--rv-long-window",
        type=int,
        default=60,
        help="Long microstructure window in seconds (default: 60)",
    )
    parser.add_argument(
        "--min-cell-samples",
        type=int,
        default=50,
        help="Minimum grouped samples for output rows (default: 50)",
    )
    parser.add_argument(
        "--veto-reversal-rate",
        type=float,
        default=0.28,
        help="Reversal-rate threshold for micro veto candidates (default: 0.28)",
    )
    parser.add_argument(
        "--veto-jump-any-rate",
        type=float,
        default=0.18,
        help="Jump-any rate threshold for micro veto candidates (default: 0.18)",
    )
    parser.add_argument(
        "--veto-flip-rate",
        type=float,
        default=0.52,
        help="Flip-rate threshold for micro veto candidates (default: 0.52)",
    )
    parser.add_argument(
        "--late-window-s",
        type=int,
        default=90,
        help="Late decision window in seconds for focused policy tables (default: 90)",
    )
    parser.add_argument(
        "--margin-z-bin",
        type=float,
        default=0.5,
        help="Bin width for volatility-scaled distance-to-strike tables (default: 0.5)",
    )
    parser.add_argument(
        "--danger-min-samples",
        type=int,
        default=150,
        help="Minimum samples for late-window danger cells (default: 150)",
    )
    parser.add_argument(
        "--danger-reversal-rate",
        type=float,
        default=0.20,
        help="Minimum reversal rate for late-window danger cells (default: 0.20)",
    )
    parser.add_argument(
        "--danger-adverse-cross-rate",
        type=float,
        default=0.28,
        help="Minimum adverse-cross rate for late-window danger cells (default: 0.28)",
    )
    parser.add_argument(
        "--danger-future-adverse-bps",
        type=float,
        default=2.5,
        help="Minimum median future adverse excursion for danger cells in bps (default: 2.5)",
    )
    parser.add_argument(
        "--danger-max-margin-z",
        type=float,
        default=1.5,
        help="Maximum margin-z bin to treat as near-strike late danger (default: 1.5)",
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

    if "trades" not in df.columns:
        df["trades"] = 0.0
    else:
        df["trades"] = df["trades"].astype(float)

    df["asset"] = config.asset
    return df[["asset", "open", "high", "low", "close", "volume", "trades"]]


def normalize_to_1s(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_index().copy()
    full_index = pd.date_range(df.index.min().floor("1s"), df.index.max().floor("1s"), freq="1s", tz="UTC")
    out = df.reindex(full_index)
    out["asset"] = out["asset"].ffill().bfill()
    missing_close = out["close"].isna()

    out["close"] = out["close"].ffill()
    out["open"] = out["open"].fillna(out["close"].shift())
    out["open"] = out["open"].fillna(out["close"])
    out["high"] = out["high"].fillna(np.maximum(out["open"], out["close"]))
    out["low"] = out["low"].fillna(np.minimum(out["open"], out["close"]))
    out["volume"] = out["volume"].fillna(0.0)
    out["trades"] = out["trades"].fillna(0.0)
    out["synthetic_1s"] = missing_close.astype(int)
    return out


def rolling_mad_sigma(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    def _mad(values: np.ndarray) -> float:
        center = np.median(values)
        mad = np.median(np.abs(values - center))
        return float(1.4826 * mad)

    return series.rolling(window, min_periods=min_periods).apply(_mad, raw=True)


def rolling_sign_flip_rate(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    def _flip(values: np.ndarray) -> float:
        signs = np.sign(values)
        signs = signs[signs != 0.0]
        if signs.size < 3:
            return np.nan
        return float(np.mean(signs[1:] != signs[:-1]))

    return series.rolling(window, min_periods=min_periods).apply(_flip, raw=True)


def rolling_efficiency_ratio(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    def _eff(values: np.ndarray) -> float:
        denom = np.sum(np.abs(values))
        if denom <= EPS:
            return np.nan
        return float(abs(np.sum(values)) / denom)

    return series.rolling(window, min_periods=min_periods).apply(_eff, raw=True)


def add_micro_features(
    df: pd.DataFrame,
    *,
    bucket_seconds: int,
    snapshot_step_seconds: int,
    local_sigma_window: int,
    jump_sigma: float,
    rv_short_window: int,
    rv_long_window: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = normalize_to_1s(df)
    df["log_close"] = np.log(df["close"])
    df["log_ret_1s"] = df["log_close"].diff()
    df["abs_ret_1s_bp"] = df["log_ret_1s"].abs() * 10000.0

    min_periods = max(30, local_sigma_window // 5)
    df["ret_center"] = df["log_ret_1s"].rolling(local_sigma_window, min_periods=min_periods).median()
    df["ret_sigma_robust"] = rolling_mad_sigma(df["log_ret_1s"], local_sigma_window, min_periods)
    fallback_sigma = df["log_ret_1s"].rolling(local_sigma_window, min_periods=min_periods).std()
    df["ret_sigma_robust"] = df["ret_sigma_robust"].where(df["ret_sigma_robust"] > EPS, fallback_sigma)
    df["jump_like_1s"] = (
        (df["ret_sigma_robust"] > EPS)
        & ((df["log_ret_1s"] - df["ret_center"]).abs() > jump_sigma * df["ret_sigma_robust"])
    ).astype(float)

    short_min_periods = max(10, rv_short_window // 2)
    long_min_periods = max(15, rv_long_window // 2)
    df["rv_30s_bp"] = np.sqrt(
        df["log_ret_1s"].pow(2).rolling(rv_short_window, min_periods=short_min_periods).sum()
    ) * 10000.0
    df["rv_60s_bp"] = np.sqrt(
        df["log_ret_1s"].pow(2).rolling(rv_long_window, min_periods=long_min_periods).sum()
    ) * 10000.0
    df["flip_rate_30s"] = rolling_sign_flip_rate(df["log_ret_1s"], rv_short_window, short_min_periods)
    df["flip_rate_60s"] = rolling_sign_flip_rate(df["log_ret_1s"], rv_long_window, long_min_periods)
    df["eff_30s"] = rolling_efficiency_ratio(df["log_ret_1s"], rv_short_window, short_min_periods)
    df["eff_60s"] = rolling_efficiency_ratio(df["log_ret_1s"], rv_long_window, long_min_periods)
    df["jump_any_30s"] = (df["jump_like_1s"].rolling(rv_short_window, min_periods=short_min_periods).sum() > 0).astype(float)
    df["jump_count_30s"] = df["jump_like_1s"].rolling(rv_short_window, min_periods=short_min_periods).sum()
    df["trend_30s_bp"] = df["log_close"].diff(rv_short_window) * 10000.0
    df["trend_60s_bp"] = df["log_close"].diff(rv_long_window) * 10000.0
    df["active_1s"] = (df["synthetic_1s"] == 0).astype(float)
    df["active_30s_share"] = df["active_1s"].rolling(rv_short_window, min_periods=short_min_periods).mean()
    df["trades_30s_sum"] = df["trades"].rolling(rv_short_window, min_periods=short_min_periods).sum()

    df["period_start"] = df.index.floor(f"{bucket_seconds}s")
    df["sec_in_bucket"] = df.groupby("period_start").cumcount()
    df["elapsed_s"] = df["sec_in_bucket"] + 1
    df["time_left_s"] = (bucket_seconds - df["elapsed_s"]).clip(lower=0)
    df["strike"] = df.groupby("period_start")["open"].transform("first")
    df["final_close"] = df.groupby("period_start")["close"].transform("last")
    df["price_snap"] = df["close"]
    df["delta_bps"] = (df["price_snap"] - df["strike"]) / df["strike"] * 10000.0
    df["delta_abs_bps"] = df["delta_bps"].abs()
    df["current_side"] = np.where(df["delta_bps"] > 0.0, "yes", np.where(df["delta_bps"] < 0.0, "no", "flat"))
    df["final_outcome_yes"] = (df["final_close"] > df["strike"]).astype(int)
    df["reversed"] = (
        ((df["delta_bps"] > 0.0) & (df["final_outcome_yes"] == 0))
        | ((df["delta_bps"] < 0.0) & (df["final_outcome_yes"] == 1))
    ).astype(int)
    df["hour_utc"] = df.index.hour
    df["hour_of_week"] = (df.index.dayofweek * 24) + df.index.hour
    df["snapshot_ok"] = (df["elapsed_s"] % snapshot_step_seconds == 0) & (df["elapsed_s"] < bucket_seconds)

    sigma_1s_60 = (df["rv_60s_bp"] / 10000.0) / math.sqrt(max(rv_long_window, 1))
    horizon_sigma = sigma_1s_60 * np.sqrt(df["time_left_s"].clip(lower=1.0))
    log_distance = np.abs(np.log(df["price_snap"] / df["strike"]))
    df["margin_z"] = np.where(horizon_sigma > EPS, log_distance / horizon_sigma, np.nan)

    adverse_bps = np.full(len(df), np.nan, dtype=float)
    favorable_bps = np.full(len(df), np.nan, dtype=float)
    adverse_cross = np.full(len(df), np.nan, dtype=float)
    time_to_first_adverse_cross_s = np.full(len(df), np.nan, dtype=float)
    low_values = df["low"].to_numpy(dtype=float)
    high_values = df["high"].to_numpy(dtype=float)
    close_values = df["close"].to_numpy(dtype=float)
    delta_values = df["delta_bps"].to_numpy(dtype=float)

    for positions in df.groupby("period_start", sort=False).indices.values():
        pos = np.asarray(positions, dtype=int)
        lows = low_values[pos]
        highs = high_values[pos]
        closes = close_values[pos]
        future_min = np.minimum.accumulate(lows[::-1])[::-1]
        future_max = np.maximum.accumulate(highs[::-1])[::-1]
        adverse_up = np.maximum(0.0, (closes - future_min) / closes * 10000.0)
        favorable_up = np.maximum(0.0, (future_max - closes) / closes * 10000.0)
        adverse_down = np.maximum(0.0, (future_max - closes) / closes * 10000.0)
        favorable_down = np.maximum(0.0, (closes - future_min) / closes * 10000.0)
        deltas = delta_values[pos]

        n_bucket = len(pos)
        next_neg = np.full(n_bucket, -1, dtype=int)
        next_pos = np.full(n_bucket, -1, dtype=int)
        seen_neg = -1
        seen_pos = -1
        for idx in range(n_bucket - 1, -1, -1):
            next_neg[idx] = seen_neg
            next_pos[idx] = seen_pos
            if deltas[idx] < 0.0:
                seen_neg = idx
            elif deltas[idx] > 0.0:
                seen_pos = idx

        adverse_bps[pos] = np.where(deltas > 0.0, adverse_up, np.where(deltas < 0.0, adverse_down, np.nan))
        favorable_bps[pos] = np.where(deltas > 0.0, favorable_up, np.where(deltas < 0.0, favorable_down, np.nan))
        for idx in range(n_bucket):
            if deltas[idx] > 0.0:
                adverse_idx = next_neg[idx]
            elif deltas[idx] < 0.0:
                adverse_idx = next_pos[idx]
            else:
                adverse_idx = -1
            if adverse_idx != -1:
                adverse_cross[pos[idx]] = 1.0
                time_to_first_adverse_cross_s[pos[idx]] = float(adverse_idx - idx)
            elif deltas[idx] != 0.0:
                adverse_cross[pos[idx]] = 0.0

    df["future_adverse_bps"] = adverse_bps
    df["future_favorable_bps"] = favorable_bps
    df["future_adverse_cross"] = adverse_cross
    df["time_to_first_adverse_cross_s"] = time_to_first_adverse_cross_s

    candles_10s = (
        df.resample("10s")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "trades": "sum",
                "synthetic_1s": "sum",
            }
        )
        .dropna(subset=["open", "high", "low", "close"])
    )
    candles_10s["synthetic_share"] = candles_10s["synthetic_1s"] / 10.0
    return df, candles_10s


def aggregate_near_strike_heatmap(df: pd.DataFrame, *, near_strike_bps: float, min_cell_samples: int) -> pd.DataFrame:
    near = df[df["delta_abs_bps"] <= near_strike_bps].copy()
    grouped = (
        near.groupby(["asset", "hour_utc", "time_left_s"], observed=False)
        .agg(
            samples=("reversed", "size"),
            reversal_rate=("reversed", "mean"),
            rv_30s_bp_median=("rv_30s_bp", "median"),
            rv_60s_bp_median=("rv_60s_bp", "median"),
            jump_any_30s_rate=("jump_any_30s", "mean"),
            jump_count_30s_mean=("jump_count_30s", "mean"),
            flip_rate_30s_mean=("flip_rate_30s", "mean"),
            flip_rate_60s_mean=("flip_rate_60s", "mean"),
            eff_30s_mean=("eff_30s", "mean"),
            eff_60s_mean=("eff_60s", "mean"),
            trend_30s_abs_bp_median=("trend_30s_bp", lambda s: float(np.nanmedian(np.abs(s)))),
            future_adverse_bps_median=("future_adverse_bps", "median"),
            future_favorable_bps_median=("future_favorable_bps", "median"),
        )
        .reset_index()
    )
    return grouped[grouped["samples"] >= max(1, int(min_cell_samples))].sort_values(["asset", "hour_utc", "time_left_s"])


def aggregate_delta_timeleft(df: pd.DataFrame, *, delta_bin_bps: float, min_cell_samples: int) -> pd.DataFrame:
    out = df.copy()
    out["delta_bin_bps"] = (np.round(out["delta_bps"] / delta_bin_bps) * delta_bin_bps).round(4)
    grouped = (
        out.groupby(["asset", "time_left_s", "delta_bin_bps"], observed=False)
        .agg(
            samples=("reversed", "size"),
            reversal_rate=("reversed", "mean"),
            future_adverse_bps_median=("future_adverse_bps", "median"),
            rv_30s_bp_median=("rv_30s_bp", "median"),
            jump_any_30s_rate=("jump_any_30s", "mean"),
            flip_rate_30s_mean=("flip_rate_30s", "mean"),
        )
        .reset_index()
    )
    return grouped[grouped["samples"] >= max(1, int(min_cell_samples))].sort_values(["asset", "time_left_s", "delta_bin_bps"])


def aggregate_timeleft_summary(df: pd.DataFrame, *, near_strike_bps: float, min_cell_samples: int) -> pd.DataFrame:
    near = df[df["delta_abs_bps"] <= near_strike_bps].copy()
    grouped = (
        near.groupby(["asset", "time_left_s"], observed=False)
        .agg(
            samples=("reversed", "size"),
            reversal_rate=("reversed", "mean"),
            rv_30s_bp_median=("rv_30s_bp", "median"),
            jump_any_30s_rate=("jump_any_30s", "mean"),
            flip_rate_30s_mean=("flip_rate_30s", "mean"),
            eff_30s_mean=("eff_30s", "mean"),
            future_adverse_bps_median=("future_adverse_bps", "median"),
        )
        .reset_index()
    )
    return grouped[grouped["samples"] >= max(1, int(min_cell_samples))].sort_values(["asset", "time_left_s"])


def aggregate_late_window_policy(
    df: pd.DataFrame,
    *,
    late_window_s: int,
    margin_z_bin: float,
    min_cell_samples: int,
) -> pd.DataFrame:
    data = df[
        (df["time_left_s"] > 0)
        & (df["time_left_s"] <= late_window_s)
        & df["margin_z"].notna()
        & df["current_side"].isin(["yes", "no"])
    ].copy()
    clipped_margin_z = data["margin_z"].clip(lower=0.0, upper=5.0 - EPS)
    data["margin_z_bin"] = (np.floor(clipped_margin_z / margin_z_bin) * margin_z_bin).round(4)

    grouped = (
        data.groupby(["asset", "time_left_s", "current_side", "margin_z_bin"], observed=False)
        .agg(
            samples=("reversed", "size"),
            reversal_rate=("reversed", "mean"),
            adverse_cross_rate=("future_adverse_cross", "mean"),
            time_to_first_adverse_cross_s_median=("time_to_first_adverse_cross_s", "median"),
            future_adverse_bps_median=("future_adverse_bps", "median"),
            rv_30s_bp_median=("rv_30s_bp", "median"),
            jump_any_30s_rate=("jump_any_30s", "mean"),
            flip_rate_30s_mean=("flip_rate_30s", "mean"),
            eff_30s_mean=("eff_30s", "mean"),
            active_30s_share_mean=("active_30s_share", "mean"),
            trades_30s_sum_median=("trades_30s_sum", "median"),
        )
        .reset_index()
    )
    return grouped[grouped["samples"] >= max(1, int(min_cell_samples))].sort_values(
        ["asset", "time_left_s", "current_side", "margin_z_bin"]
    )


def build_late_window_danger(
    policy_df: pd.DataFrame,
    *,
    min_samples: int,
    reversal_rate: float,
    adverse_cross_rate: float,
    future_adverse_bps: float,
    max_margin_z: float,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for row in policy_df.itertuples(index=False):
        if int(row.samples) < min_samples:
            continue
        if float(row.margin_z_bin) > max_margin_z:
            continue

        reasons: list[str] = []
        if float(row.reversal_rate) >= reversal_rate:
            reasons.append("fragile reversal")
        if not pd.isna(row.adverse_cross_rate) and float(row.adverse_cross_rate) >= adverse_cross_rate:
            reasons.append("high adverse-cross")
        if not pd.isna(row.future_adverse_bps_median) and float(row.future_adverse_bps_median) >= future_adverse_bps:
            reasons.append("large adverse excursion")
        if not reasons:
            continue

        rows.append(
            {
                "asset": row.asset,
                "time_left_s": int(row.time_left_s),
                "current_side": row.current_side,
                "margin_z_bin": float(row.margin_z_bin),
                "samples": int(row.samples),
                "reversal_rate": float(row.reversal_rate),
                "adverse_cross_rate": float(row.adverse_cross_rate) if not pd.isna(row.adverse_cross_rate) else np.nan,
                "time_to_first_adverse_cross_s_median": float(row.time_to_first_adverse_cross_s_median)
                if not pd.isna(row.time_to_first_adverse_cross_s_median)
                else np.nan,
                "future_adverse_bps_median": float(row.future_adverse_bps_median)
                if not pd.isna(row.future_adverse_bps_median)
                else np.nan,
                "rv_30s_bp_median": float(row.rv_30s_bp_median) if not pd.isna(row.rv_30s_bp_median) else np.nan,
                "jump_any_30s_rate": float(row.jump_any_30s_rate) if not pd.isna(row.jump_any_30s_rate) else np.nan,
                "flip_rate_30s_mean": float(row.flip_rate_30s_mean) if not pd.isna(row.flip_rate_30s_mean) else np.nan,
                "active_30s_share_mean": float(row.active_30s_share_mean) if not pd.isna(row.active_30s_share_mean) else np.nan,
                "trades_30s_sum_median": float(row.trades_30s_sum_median) if not pd.isna(row.trades_30s_sum_median) else np.nan,
                "reason": ", ".join(reasons),
            }
        )
    columns = [
        "asset",
        "time_left_s",
        "current_side",
        "margin_z_bin",
        "samples",
        "reversal_rate",
        "adverse_cross_rate",
        "time_to_first_adverse_cross_s_median",
        "future_adverse_bps_median",
        "rv_30s_bp_median",
        "jump_any_30s_rate",
        "flip_rate_30s_mean",
        "active_30s_share_mean",
        "trades_30s_sum_median",
        "reason",
    ]
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["asset", "time_left_s", "current_side", "margin_z_bin"]
    ) if rows else pd.DataFrame(columns=columns)


def build_micro_veto(df: pd.DataFrame, *, min_cell_samples: int, reversal_rate: float, jump_any_rate: float, flip_rate: float) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for row in df.itertuples(index=False):
        reasons: list[str] = []
        if float(row.reversal_rate) >= reversal_rate:
            reasons.append("fragile reversal")
        if not pd.isna(row.jump_any_30s_rate) and float(row.jump_any_30s_rate) >= jump_any_rate:
            reasons.append("jumpy")
        if not pd.isna(row.flip_rate_30s_mean) and float(row.flip_rate_30s_mean) >= flip_rate:
            reasons.append("choppy")
        if int(row.samples) < min_cell_samples or not reasons:
            continue
        rows.append(
            {
                "asset": row.asset,
                "hour_utc": int(row.hour_utc),
                "time_left_s": int(row.time_left_s),
                "samples": int(row.samples),
                "reversal_rate": float(row.reversal_rate),
                "rv_30s_bp_median": float(row.rv_30s_bp_median) if not pd.isna(row.rv_30s_bp_median) else np.nan,
                "jump_any_30s_rate": float(row.jump_any_30s_rate) if not pd.isna(row.jump_any_30s_rate) else np.nan,
                "flip_rate_30s_mean": float(row.flip_rate_30s_mean) if not pd.isna(row.flip_rate_30s_mean) else np.nan,
                "future_adverse_bps_median": float(row.future_adverse_bps_median) if not pd.isna(row.future_adverse_bps_median) else np.nan,
                "reason": ", ".join(reasons),
            }
        )
    return pd.DataFrame(rows).sort_values(["asset", "time_left_s", "hour_utc"]) if rows else pd.DataFrame(
        columns=[
            "asset",
            "hour_utc",
            "time_left_s",
            "samples",
            "reversal_rate",
            "rv_30s_bp_median",
            "jump_any_30s_rate",
            "flip_rate_30s_mean",
            "future_adverse_bps_median",
            "reason",
        ]
    )


def write_summary(
    *,
    output_dir: str,
    asset_frames: dict[str, pd.DataFrame],
    candles_10s_by_asset: dict[str, pd.DataFrame],
    snapshot_df: pd.DataFrame,
    heatmap_df: pd.DataFrame,
    micro_veto_df: pd.DataFrame,
    late_window_policy_df: pd.DataFrame,
    late_window_danger_df: pd.DataFrame,
    args: argparse.Namespace,
) -> None:
    summary: dict[str, object] = {
        "bucket_seconds": int(args.bucket_seconds),
        "snapshot_step_seconds": int(args.snapshot_step_seconds),
        "near_strike_bps": float(args.near_strike_bps),
        "jump_sigma": float(args.jump_sigma),
        "rv_short_window": int(args.rv_short_window),
        "rv_long_window": int(args.rv_long_window),
        "late_window_s": int(args.late_window_s),
        "margin_z_bin": float(args.margin_z_bin),
        "assets": {},
    }

    for asset, df in asset_frames.items():
        asset_snap = snapshot_df[snapshot_df["asset"] == asset]
        asset_heat = heatmap_df[heatmap_df["asset"] == asset]
        asset_veto = micro_veto_df[micro_veto_df["asset"] == asset]
        asset_late = late_window_policy_df[late_window_policy_df["asset"] == asset]
        asset_danger = late_window_danger_df[late_window_danger_df["asset"] == asset]
        late90 = asset_snap[(asset_snap["delta_abs_bps"] <= float(args.near_strike_bps)) & (asset_snap["time_left_s"] <= 90)]
        late90_vol = asset_snap[(asset_snap["time_left_s"] <= int(args.late_window_s)) & asset_snap["margin_z"].notna()]
        worst_late = asset_late.sort_values("reversal_rate", ascending=False).iloc[0] if not asset_late.empty else None
        summary["assets"][asset] = {
            "rows_1s": int(len(df)),
            "rows_10s": int(len(candles_10s_by_asset[asset])),
            "start": df.index.min().isoformat(),
            "end": df.index.max().isoformat(),
            "synthetic_1s_share": float(df["synthetic_1s"].mean()),
            "snapshot_rows": int(len(asset_snap)),
            "near_strike_snapshot_rows": int(np.sum(asset_snap["delta_abs_bps"] <= float(args.near_strike_bps))),
            "late90_near_strike_rows": int(len(late90)),
            "late90_near_strike_reversal_rate": float(late90["reversed"].mean()) if len(late90) else np.nan,
            "micro_veto_rows": int(len(asset_veto)),
            "late_window_policy_rows": int(len(asset_late)),
            "late_window_danger_rows": int(len(asset_danger)),
            "late_window_margin_z_median": float(late90_vol["margin_z"].median()) if len(late90_vol) else np.nan,
            "worst_reversal_cell": None
            if asset_heat.empty
            else {
                "hour_utc": int(asset_heat.sort_values("reversal_rate", ascending=False).iloc[0]["hour_utc"]),
                "time_left_s": int(asset_heat.sort_values("reversal_rate", ascending=False).iloc[0]["time_left_s"]),
                "reversal_rate": float(asset_heat.sort_values("reversal_rate", ascending=False).iloc[0]["reversal_rate"]),
                "samples": int(asset_heat.sort_values("reversal_rate", ascending=False).iloc[0]["samples"]),
            },
            "worst_late_window_policy_cell": None
            if worst_late is None
            else {
                "time_left_s": int(worst_late["time_left_s"]),
                "current_side": str(worst_late["current_side"]),
                "margin_z_bin": float(worst_late["margin_z_bin"]),
                "reversal_rate": float(worst_late["reversal_rate"]),
                "adverse_cross_rate": float(worst_late["adverse_cross_rate"]),
                "samples": int(worst_late["samples"]),
            },
        }

    with open(os.path.join(output_dir, "summary.json"), "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)


def main() -> int:
    args = parse_args()
    inputs = parse_inputs(args.inputs)
    os.makedirs(args.output_dir, exist_ok=True)

    asset_frames: dict[str, pd.DataFrame] = {}
    candles_10s_by_asset: dict[str, pd.DataFrame] = {}
    snapshot_frames: list[pd.DataFrame] = []

    for config in inputs:
        frame = load_asset_frame(config)
        full_df, candles_10s = add_micro_features(
            frame,
            bucket_seconds=max(60, int(args.bucket_seconds)),
            snapshot_step_seconds=max(1, int(args.snapshot_step_seconds)),
            local_sigma_window=max(60, int(args.local_sigma_window)),
            jump_sigma=float(args.jump_sigma),
            rv_short_window=max(10, int(args.rv_short_window)),
            rv_long_window=max(20, int(args.rv_long_window)),
        )
        asset_frames[config.asset] = full_df
        candles_10s_by_asset[config.asset] = candles_10s
        full_df.to_csv(os.path.join(args.output_dir, f"{config.asset}_1s_enriched.csv.gz"), index=True, compression="gzip")
        candles_10s.to_csv(os.path.join(args.output_dir, f"{config.asset}_10s_candles.csv.gz"), index=True, compression="gzip")
        snapshot_frames.append(
            full_df[
                full_df["snapshot_ok"]
                & (full_df["time_left_s"] > 0)
                & (full_df["delta_bps"] != 0.0)
            ].copy()
        )

    snapshot_df = pd.concat(snapshot_frames).sort_index()
    snapshot_df.to_csv(os.path.join(args.output_dir, "snapshot_15s_features.csv.gz"), index=True, compression="gzip")

    heatmap_df = aggregate_near_strike_heatmap(
        snapshot_df,
        near_strike_bps=float(args.near_strike_bps),
        min_cell_samples=max(1, int(args.min_cell_samples)),
    )
    delta_timeleft_df = aggregate_delta_timeleft(
        snapshot_df,
        delta_bin_bps=max(0.5, float(args.delta_bin_bps)),
        min_cell_samples=max(1, int(args.min_cell_samples)),
    )
    timeleft_summary_df = aggregate_timeleft_summary(
        snapshot_df,
        near_strike_bps=float(args.near_strike_bps),
        min_cell_samples=max(1, int(args.min_cell_samples)),
    )
    late_window_policy_df = aggregate_late_window_policy(
        snapshot_df,
        late_window_s=max(15, int(args.late_window_s)),
        margin_z_bin=max(0.1, float(args.margin_z_bin)),
        min_cell_samples=max(1, int(args.min_cell_samples)),
    )
    late_window_danger_df = build_late_window_danger(
        late_window_policy_df,
        min_samples=max(1, int(args.danger_min_samples)),
        reversal_rate=float(args.danger_reversal_rate),
        adverse_cross_rate=float(args.danger_adverse_cross_rate),
        future_adverse_bps=float(args.danger_future_adverse_bps),
        max_margin_z=float(args.danger_max_margin_z),
    )
    micro_veto_df = build_micro_veto(
        heatmap_df,
        min_cell_samples=max(1, int(args.min_cell_samples)),
        reversal_rate=float(args.veto_reversal_rate),
        jump_any_rate=float(args.veto_jump_any_rate),
        flip_rate=float(args.veto_flip_rate),
    )

    heatmap_df.to_csv(os.path.join(args.output_dir, "near_strike_heatmap.csv"), index=False)
    delta_timeleft_df.to_csv(os.path.join(args.output_dir, "delta_timeleft_reversal.csv"), index=False)
    timeleft_summary_df.to_csv(os.path.join(args.output_dir, "near_strike_timeleft_summary.csv"), index=False)
    late_window_policy_df.to_csv(os.path.join(args.output_dir, "late_window_policy.csv"), index=False)
    late_window_danger_df.to_csv(os.path.join(args.output_dir, "late_window_danger_zones.csv"), index=False)
    micro_veto_df.to_csv(os.path.join(args.output_dir, "micro_veto_zones.csv"), index=False)

    write_summary(
        output_dir=args.output_dir,
        asset_frames=asset_frames,
        candles_10s_by_asset=candles_10s_by_asset,
        snapshot_df=snapshot_df,
        heatmap_df=heatmap_df,
        micro_veto_df=micro_veto_df,
        late_window_policy_df=late_window_policy_df,
        late_window_danger_df=late_window_danger_df,
        args=args,
    )

    print("Saved outputs:")
    for config in inputs:
        print(f"- {os.path.join(args.output_dir, f'{config.asset}_1s_enriched.csv.gz')}")
        print(f"- {os.path.join(args.output_dir, f'{config.asset}_10s_candles.csv.gz')}")
    print(f"- {os.path.join(args.output_dir, 'snapshot_15s_features.csv.gz')}")
    print(f"- {os.path.join(args.output_dir, 'near_strike_heatmap.csv')}")
    print(f"- {os.path.join(args.output_dir, 'delta_timeleft_reversal.csv')}")
    print(f"- {os.path.join(args.output_dir, 'near_strike_timeleft_summary.csv')}")
    print(f"- {os.path.join(args.output_dir, 'late_window_policy.csv')}")
    print(f"- {os.path.join(args.output_dir, 'late_window_danger_zones.csv')}")
    print(f"- {os.path.join(args.output_dir, 'micro_veto_zones.csv')}")
    print(f"- {os.path.join(args.output_dir, 'summary.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
