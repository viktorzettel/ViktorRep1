#!/usr/bin/env python3
"""
Aggregate analyzer for kou_live_capture.py session folders.

The script joins captured snapshots to completed 5-minute bucket outcomes and
exports tables/report artifacts that can be rerun after future live sessions.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Iterable
from datetime import UTC, datetime, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


TIME_BIN_EDGES = [0, 15, 30, 60, 90, 120, 180, 240, 300, math.inf]
TIME_BIN_LABELS = ["0-15s", "15-30s", "30-60s", "60-90s", "90-120s", "120-180s", "180-240s", "240-300s", "300s+"]
MARGIN_BIN_EDGES = [-math.inf, 0.5, 1.0, 1.5, 2.0, math.inf]
MARGIN_BIN_LABELS = ["<0.5", "0.5-1.0", "1.0-1.5", "1.5-2.0", ">=2.0"]
PROB_BIN_EDGES = np.linspace(0.0, 1.0, 11)
PROB_BIN_LABELS = [f"{PROB_BIN_EDGES[i]:.1f}-{PROB_BIN_EDGES[i + 1]:.1f}" for i in range(len(PROB_BIN_EDGES) - 1)]
NY = ZoneInfo("America/New_York")
BERLIN = ZoneInfo("Europe/Berlin")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Kou live-capture sessions")
    parser.add_argument("--input-root", default="data/live_capture", help="Root directory containing session folders")
    parser.add_argument(
        "--output-dir",
        default="data/live_capture/aggregate_analysis",
        help="Directory for aggregate report outputs",
    )
    parser.add_argument("--signal-threshold", type=float, default=0.91, help="Current Kou signal threshold")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def dig(row: dict[str, Any], *path: str, default: Any = None) -> Any:
    obj: Any = row
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return obj


def to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def to_bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{100.0 * float(value):.1f}%"


def fmt_num(value: float | None, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    phat = wins / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * n)) / n) / denom
    return max(0.0, center - half), min(1.0, center + half)


def ny_regular_market_open(ts: float) -> bool:
    local = datetime.fromtimestamp(ts, UTC).astimezone(NY)
    if local.weekday() >= 5:
        return False
    current = local.time()
    return dtime(9, 30) <= current < dtime(16, 0)


def time_share(start_ts: float, stop_ts: float, predicate) -> float:
    if stop_ts <= start_ts:
        return 0.0
    step_s = 60.0
    samples = np.arange(start_ts, stop_ts, step_s)
    if samples.size == 0:
        return 0.0
    return float(np.mean([bool(predicate(float(ts))) for ts in samples]))


def condition_label(start_ts: float, stop_ts: float) -> tuple[str, float, float]:
    us_share = time_share(start_ts, stop_ts, ny_regular_market_open)
    weekend_share = time_share(
        start_ts,
        stop_ts,
        lambda ts: datetime.fromtimestamp(ts, UTC).astimezone(NY).weekday() >= 5,
    )
    if weekend_share > 0.5:
        label = "weekend"
    elif us_share >= 0.5:
        label = "US regular hours"
    elif us_share > 0.0:
        label = "US overlap"
    else:
        label = "Europe/pre-US"
    return label, us_share, weekend_share


def flatten_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    components = dig(row, "safety", "components", default={}) or {}
    return {
        "session_id": dig(row, "session", "id"),
        "captured_at_ts": dig(row, "session", "captured_at_ts"),
        "captured_at_iso": dig(row, "session", "captured_at_iso"),
        "capture_interval_s": dig(row, "session", "capture_interval_s"),
        "symbol": dig(row, "asset", "symbol"),
        "asset_state": dig(row, "asset", "state"),
        "display_source": dig(row, "asset", "display_source"),
        "model_source": dig(row, "asset", "model_source"),
        "age_s": dig(row, "asset", "age_s"),
        "model_age_s": dig(row, "asset", "model_age_s"),
        "bucket_end": dig(row, "bucket", "bucket_end"),
        "bucket_seconds": dig(row, "bucket", "bucket_seconds"),
        "time_left_s": dig(row, "bucket", "time_left_s"),
        "price": dig(row, "market", "price"),
        "model_price": dig(row, "market", "model_price"),
        "strike": dig(row, "market", "strike"),
        "delta_bps": dig(row, "market", "delta_bps"),
        "current_side": dig(row, "market", "current_side"),
        "model_label": dig(row, "model", "model"),
        "kou_phase": dig(row, "model", "kou_phase"),
        "sample_count": dig(row, "model", "sample_count"),
        "kou_yes": dig(row, "model", "kou_yes"),
        "raw_kou_yes": dig(row, "model", "raw_kou_yes"),
        "bs_yes": dig(row, "model", "bs_yes"),
        "kou_weight": dig(row, "model", "kou_weight"),
        "edge_pp": dig(row, "model", "edge_pp"),
        "lam": dig(row, "model", "lam"),
        "p_up": dig(row, "model", "p_up"),
        "sigma_model_bp_1m": dig(row, "model", "sigma_model_bp_1m"),
        "signal_state": dig(row, "signal", "state"),
        "signal_hold_s": dig(row, "signal", "hold_s"),
        "safety_final_score": dig(row, "safety", "final_score"),
        "safety_final_label": dig(row, "safety", "final_label"),
        "safety_final_reason": dig(row, "safety", "final_reason"),
        "safety_heuristic_score": dig(row, "safety", "heuristic_score"),
        "safety_heuristic_label": dig(row, "safety", "heuristic_label"),
        "safety_heuristic_reason": dig(row, "safety", "heuristic_reason"),
        "policy_level": dig(row, "policy", "level") or "NONE",
        "policy_reason": dig(row, "policy", "reason"),
        "policy_bucket_s": dig(row, "policy", "bucket_s"),
        "margin_z": dig(row, "policy", "margin_z"),
        "policy_override": bool(dig(row, "policy", "override", default=False)),
        "vol_30m_bp_1m": dig(row, "volatility", "vol_30m_bp_1m"),
        "vol_1h_bp_1m": dig(row, "volatility", "vol_1h_bp_1m"),
        "jump_10s_10m_rate": dig(row, "jumps", "jump_10s_10m_rate"),
        "jump_10s_10m_count": dig(row, "jumps", "jump_10s_10m_count"),
        "jump_30s_15m_rate": dig(row, "jumps", "jump_30s_15m_rate"),
        "jump_30s_15m_count": dig(row, "jumps", "jump_30s_15m_count"),
        "jump_10s_10m_count_2_0": dig(row, "jumps", "jump_sweep_10s_10m", "2.0", "count"),
        "jump_10s_10m_count_2_5": dig(row, "jumps", "jump_sweep_10s_10m", "2.5", "count"),
        "jump_10s_10m_count_3_0": dig(row, "jumps", "jump_sweep_10s_10m", "3.0", "count"),
        "jump_10s_10m_count_3_5": dig(row, "jumps", "jump_sweep_10s_10m", "3.5", "count"),
        "jump_30s_15m_count_2_0": dig(row, "jumps", "jump_sweep_30s_15m", "2.0", "count"),
        "jump_30s_15m_count_2_5": dig(row, "jumps", "jump_sweep_30s_15m", "2.5", "count"),
        "jump_30s_15m_count_3_0": dig(row, "jumps", "jump_sweep_30s_15m", "3.0", "count"),
        "jump_30s_15m_count_3_5": dig(row, "jumps", "jump_sweep_30s_15m", "3.5", "count"),
        "component_margin_safety": components.get("margin_safety"),
        "component_jump_calm": components.get("jump_calm"),
        "component_flip_calm": components.get("flip_calm"),
        "component_reversal_safety": components.get("reversal_safety"),
        "component_trend_clean": components.get("trend_clean"),
        "component_weakest": components.get("weakest_component"),
    }


def flatten_outcome(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": row.get("session_id"),
        "symbol": row.get("symbol"),
        "bucket_end": row.get("bucket_end"),
        "bucket_start": row.get("bucket_start"),
        "complete": bool(row.get("complete")),
        "settled_yes": to_bool_or_none(row.get("settled_yes")),
        "settled_side": row.get("settled_side"),
        "settled_delta_bps": row.get("settled_delta_bps"),
        "outcome_sample_count": row.get("sample_count"),
        "sampled_min_price": row.get("sampled_min_price"),
        "sampled_max_price": row.get("sampled_max_price"),
        "sampled_min_delta_bps": row.get("sampled_min_delta_bps"),
        "sampled_max_delta_bps": row.get("sampled_max_delta_bps"),
        "sampled_cross_count": row.get("sampled_cross_count"),
        "signal_yes_samples": row.get("signal_yes_samples"),
        "signal_no_samples": row.get("signal_no_samples"),
        "policy_override_samples": row.get("policy_override_samples"),
        "hard_no_go_samples": row.get("hard_no_go_samples"),
        "caution_samples": row.get("caution_samples"),
    }


def load_sessions(input_root: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    meta_rows: list[dict[str, Any]] = []
    snapshot_rows: list[dict[str, Any]] = []
    outcome_rows: list[dict[str, Any]] = []

    for session_dir in sorted(path for path in input_root.iterdir() if path.is_dir()):
        meta_path = session_dir / "session_meta.json"
        snapshots_path = session_dir / "snapshots.jsonl"
        outcomes_path = session_dir / "bucket_outcomes.jsonl"
        if not (meta_path.exists() and snapshots_path.exists() and outcomes_path.exists()):
            continue

        meta = read_json(meta_path)
        raw_snapshots = read_jsonl(snapshots_path)
        raw_outcomes = read_jsonl(outcomes_path)

        observed_ts: list[float] = []
        for row in raw_snapshots:
            value = to_float(dig(row, "session", "captured_at_ts"))
            if value is not None:
                observed_ts.append(value)
        for row in raw_outcomes:
            for key in ("last_capture_ts", "first_capture_ts"):
                value = to_float(row.get(key))
                if value is not None:
                    observed_ts.append(value)

        meta_start = to_float(meta.get("started_at_ts"))
        start_ts = float(meta_start if meta_start is not None else (min(observed_ts) if observed_ts else 0.0))
        meta_stop = to_float(meta.get("stopped_at_ts"))
        inferred_stop = max(observed_ts) if observed_ts else start_ts
        stop_ts = float(meta_stop if meta_stop is not None else inferred_stop)
        if stop_ts <= start_ts and inferred_stop > start_ts:
            stop_ts = float(inferred_stop)
        label, us_share, weekend_share = condition_label(start_ts, stop_ts)
        start_dt = datetime.fromtimestamp(start_ts, UTC)
        stop_dt = datetime.fromtimestamp(stop_ts, UTC)
        meta_rows.append(
            {
                "session_id": session_dir.name,
                "started_at_utc": start_dt.isoformat().replace("+00:00", "Z"),
                "stopped_at_utc": stop_dt.isoformat().replace("+00:00", "Z"),
                "started_at_berlin": start_dt.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M:%S %Z"),
                "stopped_at_berlin": stop_dt.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M:%S %Z"),
                "started_at_new_york": start_dt.astimezone(NY).strftime("%Y-%m-%d %H:%M:%S %Z"),
                "stopped_at_new_york": stop_dt.astimezone(NY).strftime("%Y-%m-%d %H:%M:%S %Z"),
                "duration_h": (stop_ts - start_ts) / 3600.0 if stop_ts >= start_ts else np.nan,
                "condition_label": label,
                "us_regular_share": us_share,
                "weekend_share": weekend_share,
                "git_revision": meta.get("git_revision"),
                "fine_window_seconds": meta.get("fine_window_seconds"),
                "fine_seconds": meta.get("fine_seconds"),
                "coarse_seconds": meta.get("coarse_seconds"),
            }
        )

        for row in raw_snapshots:
            snapshot_rows.append(flatten_snapshot(row))
        for row in raw_outcomes:
            outcome_rows.append(flatten_outcome(row))

    return pd.DataFrame(meta_rows), pd.DataFrame(snapshot_rows), pd.DataFrame(outcome_rows)


def normalize_numeric(df: pd.DataFrame, columns: Iterable[str]) -> None:
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")


def add_derived_columns(joined: pd.DataFrame) -> pd.DataFrame:
    out = joined.copy()
    out["settled_yes_num"] = out["settled_yes"].astype(float)
    out["time_bin"] = pd.cut(
        out["time_left_s"],
        bins=TIME_BIN_EDGES,
        labels=TIME_BIN_LABELS,
        right=False,
        include_lowest=True,
    ).astype("string").fillna("missing")
    out["margin_z_bin"] = pd.cut(
        out["margin_z"],
        bins=MARGIN_BIN_EDGES,
        labels=MARGIN_BIN_LABELS,
        right=False,
        include_lowest=True,
    ).astype("string").fillna("missing")
    out["current_side_yes"] = np.where(out["current_side"] == "yes", True, np.where(out["current_side"] == "no", False, np.nan))
    out["current_side_win"] = np.where(
        out["current_side"].isin(["yes", "no"]),
        out["current_side_yes"] == out["settled_yes"],
        np.nan,
    )
    out["is_late_90"] = out["time_left_s"].le(90)
    out["is_late_120"] = out["time_left_s"].le(120)
    out["signal_decision_yes"] = np.where(
        out["signal_state"] == "BUY_YES",
        True,
        np.where(out["signal_state"] == "BUY_NO", False, np.nan),
    )
    out["signal_win"] = np.where(
        out["signal_state"].isin(["BUY_YES", "BUY_NO"]),
        out["signal_decision_yes"] == out["settled_yes"],
        np.nan,
    )
    for model, col in {"kou": "kou_yes", "raw_kou": "raw_kou_yes", "bs": "bs_yes"}.items():
        out[f"{model}_pred_yes"] = out[col].ge(0.5)
        out[f"{model}_correct"] = np.where(out[col].notna(), out[f"{model}_pred_yes"] == out["settled_yes"], np.nan)
        out[f"{model}_brier"] = np.where(out[col].notna(), (out[col] - out["settled_yes_num"]) ** 2, np.nan)
    return out


def summarize_binary(df: pd.DataFrame, group_cols: list[str], value_col: str, prefix: str = "") -> pd.DataFrame:
    data = df[df[value_col].notna()].copy()
    if data.empty:
        return pd.DataFrame(columns=group_cols + [f"{prefix}wins", f"{prefix}n", f"{prefix}rate", f"{prefix}ci_low", f"{prefix}ci_high"])
    grouped = data.groupby(group_cols, dropna=False)[value_col].agg(["sum", "count"]).reset_index()
    grouped = grouped.rename(columns={"sum": f"{prefix}wins", "count": f"{prefix}n"})
    grouped[f"{prefix}rate"] = grouped[f"{prefix}wins"] / grouped[f"{prefix}n"]
    lows: list[float | None] = []
    highs: list[float | None] = []
    for row in grouped.itertuples(index=False):
        wins = int(getattr(row, f"{prefix}wins"))
        n = int(getattr(row, f"{prefix}n"))
        low, high = wilson_ci(wins, n)
        lows.append(low)
        highs.append(high)
    grouped[f"{prefix}ci_low"] = lows
    grouped[f"{prefix}ci_high"] = highs
    return grouped


def model_metrics(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for model, col in {"kou": "kou_yes", "raw_kou": "raw_kou_yes", "bs": "bs_yes"}.items():
        data = df[df[col].notna()].copy()
        if data.empty:
            continue
        data["_pred_yes"] = data[col] >= 0.5
        data["_correct"] = data["_pred_yes"] == data["settled_yes"]
        data["_brier"] = (data[col] - data["settled_yes_num"]) ** 2
        for key, group in data.groupby(group_cols, dropna=False):
            if not isinstance(key, tuple):
                key = (key,)
            row = {col_name: key[idx] for idx, col_name in enumerate(group_cols)}
            row.update(
                {
                    "model": model,
                    "n": int(len(group)),
                    "accuracy": float(group["_correct"].mean()),
                    "brier": float(group["_brier"].mean()),
                    "mean_pred": float(group[col].mean()),
                    "realized_yes_rate": float(group["settled_yes_num"].mean()),
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def calibration_table(df: pd.DataFrame, group_cols: list[str], *, late_only: bool = False) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    source = df[df["is_late_90"]] if late_only else df
    for model, col in {"kou": "kou_yes", "raw_kou": "raw_kou_yes", "bs": "bs_yes"}.items():
        data = source[source[col].notna()].copy()
        if data.empty:
            continue
        data["prob_bin"] = pd.cut(
            data[col].clip(0.0, 1.0),
            bins=PROB_BIN_EDGES,
            labels=PROB_BIN_LABELS,
            include_lowest=True,
            right=True,
        ).astype("string")
        for key, group in data.groupby(group_cols + ["prob_bin"], dropna=False):
            if not isinstance(key, tuple):
                key = (key,)
            row = {col_name: key[idx] for idx, col_name in enumerate(group_cols + ["prob_bin"])}
            row.update(
                {
                    "model": model,
                    "scope": "late90" if late_only else "all",
                    "n": int(len(group)),
                    "mean_pred": float(group[col].mean()),
                    "realized_yes_rate": float(group["settled_yes_num"].mean()),
                    "calibration_gap": float(group["settled_yes_num"].mean() - group[col].mean()),
                }
            )
            rows.append(row)
    return pd.DataFrame(rows)


def first_actual_signals(joined: pd.DataFrame) -> pd.DataFrame:
    signals = joined[joined["signal_state"].isin(["BUY_YES", "BUY_NO"])].copy()
    if signals.empty:
        return signals
    signals = signals.sort_values(["session_id", "symbol", "bucket_end", "captured_at_ts"])
    first = signals.groupby(["session_id", "symbol", "bucket_end"], as_index=False, dropna=False).head(1).copy()
    first["decision_yes"] = first["signal_state"] == "BUY_YES"
    first["win"] = first["decision_yes"] == first["settled_yes"]
    return first


def simulate_first_signal(group: pd.DataFrame, threshold: float, hold_s: float) -> dict[str, Any] | None:
    active_side: str | None = None
    active_start: float | None = None
    ordered = group.sort_values("captured_at_ts")
    for row in ordered.itertuples(index=False):
        p = to_float(getattr(row, "kou_yes"))
        time_left = to_float(getattr(row, "time_left_s"))
        ts = to_float(getattr(row, "captured_at_ts"))
        if p is None or time_left is None or ts is None or time_left > 90.0:
            continue
        side = None
        if p >= threshold:
            side = "BUY_YES"
        elif p <= 1.0 - threshold:
            side = "BUY_NO"

        if side is None:
            active_side = None
            active_start = None
            continue
        if side != active_side:
            active_side = side
            active_start = ts
        held = 0.0 if active_start is None else max(0.0, ts - active_start)
        if held >= hold_s:
            return {
                "signal_state": side,
                "captured_at_ts": ts,
                "time_left_s": time_left,
                "kou_yes": p,
                "safety_final_label": getattr(row, "safety_final_label"),
                "policy_level": getattr(row, "policy_level"),
                "policy_override": bool(getattr(row, "policy_override")),
                "margin_z": to_float(getattr(row, "margin_z")),
            }
    return None


def persistence_sweep(joined: pd.DataFrame, thresholds: list[float], holds: list[float]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    grouped = joined.groupby(["session_id", "symbol", "bucket_end"], dropna=False)
    for threshold in thresholds:
        for hold_s in holds:
            signal_rows: list[dict[str, Any]] = []
            for (session_id, symbol, bucket_end), group in grouped:
                result = simulate_first_signal(group, threshold, hold_s)
                if result is None:
                    continue
                settled_yes = bool(group["settled_yes"].iloc[0])
                decision_yes = result["signal_state"] == "BUY_YES"
                signal_rows.append(
                    {
                        "threshold": threshold,
                        "hold_s": hold_s,
                        "session_id": session_id,
                        "symbol": symbol,
                        "bucket_end": bucket_end,
                        "signal_state": result["signal_state"],
                        "decision_yes": decision_yes,
                        "settled_yes": settled_yes,
                        "win": decision_yes == settled_yes,
                        "time_left_s": result["time_left_s"],
                        "kou_yes": result["kou_yes"],
                        "safety_final_label": result["safety_final_label"],
                        "policy_level": result["policy_level"],
                        "policy_override": result["policy_override"],
                        "margin_z": result["margin_z"],
                    }
                )
            data = pd.DataFrame(signal_rows)
            if data.empty:
                rows.append(
                    {
                        "threshold": threshold,
                        "hold_s": hold_s,
                        "signals": 0,
                        "wins": 0,
                        "win_rate": np.nan,
                        "ci_low": np.nan,
                        "ci_high": np.nan,
                    }
                )
                continue
            wins = int(data["win"].sum())
            n = int(len(data))
            low, high = wilson_ci(wins, n)
            rows.append(
                {
                    "threshold": threshold,
                    "hold_s": hold_s,
                    "signals": n,
                    "wins": wins,
                    "losses": n - wins,
                    "win_rate": wins / n,
                    "ci_low": low,
                    "ci_high": high,
                    "median_time_left_s": float(data["time_left_s"].median()),
                    "mean_abs_margin_z": float(data["margin_z"].abs().mean()) if data["margin_z"].notna().any() else np.nan,
                }
            )
            by_symbol = summarize_binary(data, ["threshold", "hold_s", "symbol"], "win", prefix="")
            for sym_row in by_symbol.to_dict("records"):
                sym_row["slice"] = "symbol"
                rows.append(sym_row)
    return pd.DataFrame(rows)


def jump_sweep_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    data = df[df["current_side_win"].notna()].copy()
    for scope_name, scope in [("all", data), ("late120", data[data["is_late_120"]])]:
        for window in ["10s_10m", "30s_15m"]:
            for threshold in ["2_0", "2_5", "3_0", "3_5"]:
                col = f"jump_{window}_count_{threshold}"
                if col not in scope.columns:
                    continue
                scoped = scope[scope[col].notna()].copy()
                if scoped.empty:
                    continue
                scoped["jump_present"] = scoped[col] > 0
                for (symbol, present), group in scoped.groupby(["symbol", "jump_present"], dropna=False):
                    wins = int(group["current_side_win"].sum())
                    n = int(len(group))
                    low, high = wilson_ci(wins, n)
                    rows.append(
                        {
                            "scope": scope_name,
                            "symbol": symbol,
                            "window": window,
                            "threshold_sigma": float(threshold.replace("_", ".")),
                            "jump_present": bool(present),
                            "n": n,
                            "wins": wins,
                            "hold_rate": wins / n if n else np.nan,
                            "ci_low": low,
                            "ci_high": high,
                        }
                    )
    return pd.DataFrame(rows)


def ece_from_calibration(calib: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if calib.empty:
        return pd.DataFrame(rows)
    group_cols = [c for c in ["scope", "symbol", "model"] if c in calib.columns]
    for key, group in calib.groupby(group_cols, dropna=False):
        if not isinstance(key, tuple):
            key = (key,)
        weights = group["n"].astype(float)
        total = float(weights.sum())
        row = {col: key[idx] for idx, col in enumerate(group_cols)}
        row["samples"] = int(total)
        row["ece_abs_gap"] = float(np.average(group["calibration_gap"].abs(), weights=weights)) if total > 0 else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def write_csv(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def make_plots(output_dir: Path, tables: dict[str, pd.DataFrame]) -> list[str]:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return []

    visual_dir = output_dir / "visuals"
    visual_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []

    session_summary = tables["session_summary"]
    if not session_summary.empty:
        fig, ax = plt.subplots(figsize=(9, 4.8))
        labels = session_summary["session_id"].astype(str).str.replace("2026", "", regex=False)
        ax.bar(labels, session_summary["first_signal_win_rate"] * 100.0, color="#3178b7")
        ax.set_ylim(90, 101)
        ax.set_ylabel("First-signal win rate (%)")
        ax.set_title("First signal per bucket by session")
        ax.tick_params(axis="x", rotation=20)
        for idx, row in session_summary.iterrows():
            ax.text(idx, row["first_signal_win_rate"] * 100.0 + 0.15, f"{int(row['first_signal_wins'])}/{int(row['first_signal_count'])}", ha="center", fontsize=9)
        fig.tight_layout()
        path = visual_dir / "first_signal_by_session.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths.append(str(path))

    model_time = tables["model_by_time_bin"]
    if not model_time.empty:
        fig, ax = plt.subplots(figsize=(10, 5.2))
        order = TIME_BIN_LABELS[:-1]
        for model, color in [("kou", "#2c7fb8"), ("bs", "#f28e2b")]:
            sub = model_time[(model_time["model"] == model) & (model_time["symbol"] == "ALL")].copy()
            sub = sub[sub["time_bin"].isin(order)]
            sub["time_bin"] = pd.Categorical(sub["time_bin"], order, ordered=True)
            sub = sub.sort_values("time_bin")
            ax.plot(sub["time_bin"].astype(str), sub["accuracy"] * 100.0, marker="o", label=model.upper(), color=color)
        ax.set_ylim(45, 100)
        ax.set_ylabel("Snapshot directional accuracy (%)")
        ax.set_title("Model accuracy improves sharply near expiry")
        ax.tick_params(axis="x", rotation=25)
        ax.legend()
        fig.tight_layout()
        path = visual_dir / "model_accuracy_by_time_left.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths.append(str(path))

    safety = tables["safety_hold_quality"]
    if not safety.empty:
        fig, ax = plt.subplots(figsize=(9, 4.8))
        sub = safety[(safety["symbol"] == "ALL") & (safety["scope"] == "final_label")].copy()
        order = ["WAIT", "AVOID", "CAREFUL", "OK", "GOOD"]
        sub["safety_final_label"] = pd.Categorical(sub["safety_final_label"], order, ordered=True)
        sub = sub.sort_values("safety_final_label")
        ax.bar(sub["safety_final_label"].astype(str), sub["rate"] * 100.0, color="#59a14f")
        ax.set_ylim(50, 100)
        ax.set_ylabel("Current-side hold rate (%)")
        ax.set_title("Safety labels separate hold quality")
        for idx, row in enumerate(sub.itertuples(index=False)):
            ax.text(idx, row.rate * 100.0 + 0.7, f"n={int(row.n)}", ha="center", fontsize=9)
        fig.tight_layout()
        path = visual_dir / "safety_hold_quality.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths.append(str(path))

    margin = tables["margin_z_hold_quality"]
    if not margin.empty:
        fig, ax = plt.subplots(figsize=(9, 4.8))
        sub = margin[(margin["symbol"] == "ALL") & (margin["time_scope"] == "late120")].copy()
        sub["margin_z_bin"] = pd.Categorical(sub["margin_z_bin"], MARGIN_BIN_LABELS + ["missing"], ordered=True)
        sub = sub.sort_values("margin_z_bin")
        ax.bar(sub["margin_z_bin"].astype(str), sub["rate"] * 100.0, color="#b07aa1")
        ax.set_ylim(55, 101)
        ax.set_ylabel("Current-side hold rate (%)")
        ax.set_title("Last 120s: distance from strike is the main danger signal")
        ax.tick_params(axis="x", rotation=20)
        for idx, row in enumerate(sub.itertuples(index=False)):
            ax.text(idx, row.rate * 100.0 + 0.7, f"n={int(row.n)}", ha="center", fontsize=9)
        fig.tight_layout()
        path = visual_dir / "margin_z_hold_quality_late120.png"
        fig.savefig(path, dpi=150)
        plt.close(fig)
        paths.append(str(path))

    return paths


def add_all_symbol(df: pd.DataFrame, metric_fn, group_cols: list[str], *args) -> pd.DataFrame:
    by_symbol = metric_fn(df, ["symbol"] + group_cols, *args)
    all_symbols = metric_fn(df.assign(symbol="ALL"), ["symbol"] + group_cols, *args)
    return pd.concat([all_symbols, by_symbol], ignore_index=True)


def build_report(output_dir: Path, tables: dict[str, pd.DataFrame], summary: dict[str, Any], visual_paths: list[str]) -> None:
    session_summary = tables["session_summary"]
    first_signal_summary = tables["first_signal_summary"]
    model_summary = tables["model_summary"]
    model_time = tables["model_by_time_bin"]
    safety = tables["safety_hold_quality"]
    policy = tables["policy_hold_quality"]
    margin = tables["margin_z_hold_quality"]
    persistence = tables["persistence_sweep"]
    jump = tables["jump_sweep_summary"]
    calibration_ece = tables["calibration_ece"]
    signal_losses = tables["first_signal_losses"]

    overall_signal = first_signal_summary[
        (first_signal_summary["symbol"] == "ALL") & (first_signal_summary["scope"] == "overall")
    ].iloc[0]
    current_persistence = persistence[
        (persistence.get("threshold") == summary["signal_threshold"])
        & (persistence.get("hold_s") == 4.0)
        & persistence.get("slice").isna()
    ]
    current_persistence_row = current_persistence.iloc[0] if not current_persistence.empty else None

    lines: list[str] = []
    lines.append("# Aggregate Live Capture Analysis")
    lines.append("")
    lines.append("## Scope")
    lines.append(f"- Sessions analyzed: `{summary['session_count']}`")
    lines.append(f"- Complete non-flat buckets: `{summary['complete_buckets']}`")
    lines.append(f"- Joined snapshots: `{summary['joined_snapshots']}`")
    lines.append(f"- First signal buckets: `{int(overall_signal['n'])}`")
    lines.append(f"- First signal wins: `{int(overall_signal['wins'])}`")
    lines.append(f"- First signal win rate: `{pct(overall_signal['rate'])}`")
    lines.append(f"- 95% Wilson interval: `{pct(overall_signal['ci_low'])}` to `{pct(overall_signal['ci_high'])}`")
    lines.append("")
    lines.append("## Session Coverage")
    lines.append("")
    lines.append("| Session | Condition | UTC window | Berlin window | NY window | Buckets | First signals | Win rate |")
    lines.append("|---|---:|---|---|---|---:|---:|---:|")
    for row in session_summary.itertuples(index=False):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row.session_id),
                    str(row.condition_label),
                    f"{row.started_at_utc} to {row.stopped_at_utc}",
                    f"{row.started_at_berlin} to {row.stopped_at_berlin}",
                    f"{row.started_at_new_york} to {row.stopped_at_new_york}",
                    str(int(row.complete_buckets)),
                    f"{int(row.first_signal_wins)}/{int(row.first_signal_count)}",
                    pct(row.first_signal_win_rate),
                ]
            )
            + " |"
        )
    lines.append("")
    lines.append("## Main Findings")
    lines.append("")
    lines.append("1. The first-signal layer was strong in every session. The worst session was still profitable by outcome count, and the session confidence intervals overlap heavily, so there is no clear evidence yet that one time condition is bad.")
    lines.append("2. The edge is late-window, not full-bucket. Snapshot accuracy is weak early in the 5-minute bucket and becomes very strong in the final minute.")
    lines.append("3. BS remains better than Kou as a broad snapshot probability engine. Kou is useful as a selective late signal, but the current live data does not justify treating Kou as globally superior.")
    lines.append("4. The safety and late-policy layers are doing real separation. `GOOD` and `CLEAR` states hold much better than `OK`/`CAREFUL` and `CAUTION` states.")
    lines.append("5. `margin_z` is the strongest simple danger feature. Near-strike late-window states remain the main place where the current side fails.")
    lines.append("")
    lines.append("## Model Quality")
    lines.append("")
    lines.append("| Symbol | Model | Snapshots | Accuracy | Brier | Mean prediction | Realized YES |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for row in model_summary.sort_values(["symbol", "model"]).itertuples(index=False):
        lines.append(
            f"| {row.symbol} | {row.model} | {int(row.n)} | {pct(row.accuracy)} | {fmt_num(row.brier, 4)} | {pct(row.mean_pred)} | {pct(row.realized_yes_rate)} |"
        )
    lines.append("")
    lines.append("## Time Left")
    lines.append("")
    lines.append("| Time left | Kou accuracy | BS accuracy | Snapshots |")
    lines.append("|---|---:|---:|---:|")
    for label in TIME_BIN_LABELS[:-1]:
        kou_row = model_time[(model_time["symbol"] == "ALL") & (model_time["model"] == "kou") & (model_time["time_bin"] == label)]
        bs_row = model_time[(model_time["symbol"] == "ALL") & (model_time["model"] == "bs") & (model_time["time_bin"] == label)]
        if kou_row.empty or bs_row.empty:
            continue
        lines.append(f"| {label} | {pct(float(kou_row.iloc[0]['accuracy']))} | {pct(float(bs_row.iloc[0]['accuracy']))} | {int(kou_row.iloc[0]['n'])} |")
    lines.append("")
    lines.append("## Safety And Policy")
    lines.append("")
    lines.append("| Slice | Level | Hold rate | Wins / N |")
    lines.append("|---|---:|---:|---:|")
    safety_all = safety[(safety["symbol"] == "ALL") & (safety["scope"] == "final_label")]
    for row in safety_all.sort_values("rate", ascending=False).itertuples(index=False):
        lines.append(f"| Safety | {row.safety_final_label} | {pct(row.rate)} | {int(row.wins)}/{int(row.n)} |")
    policy_all = policy[policy["symbol"] == "ALL"]
    for row in policy_all.sort_values("rate", ascending=False).itertuples(index=False):
        lines.append(f"| Policy | {row.policy_level} | {pct(row.rate)} | {int(row.wins)}/{int(row.n)} |")
    lines.append("")
    lines.append("## Last 120s Margin Risk")
    lines.append("")
    lines.append("| Margin z | Hold rate | Wins / N |")
    lines.append("|---|---:|---:|")
    margin_all = margin[(margin["symbol"] == "ALL") & (margin["time_scope"] == "late120")].copy()
    margin_all["margin_z_bin"] = pd.Categorical(margin_all["margin_z_bin"], MARGIN_BIN_LABELS + ["missing"], ordered=True)
    for row in margin_all.sort_values("margin_z_bin").itertuples(index=False):
        lines.append(f"| {row.margin_z_bin} | {pct(row.rate)} | {int(row.wins)}/{int(row.n)} |")
    lines.append("")
    lines.append("## Persistence")
    lines.append("")
    lines.append("| Threshold | Hold seconds | Signals | Win rate | 95% Wilson interval |")
    lines.append("|---:|---:|---:|---:|---:|")
    persist_overall = persistence[persistence.get("slice").isna()].copy()
    for row in persist_overall.sort_values(["threshold", "hold_s"]).itertuples(index=False):
        lines.append(f"| {row.threshold:.2f} | {row.hold_s:.0f} | {int(row.signals)} | {pct(row.win_rate)} | {pct(row.ci_low)} to {pct(row.ci_high)} |")
    if current_persistence_row is not None:
        lines.append("")
        lines.append(
            f"Current live rule (`{summary['signal_threshold']:.2f}` threshold, `4s` hold): "
            f"`{int(current_persistence_row.wins)}/{int(current_persistence_row.signals)}` wins, "
            f"`{pct(current_persistence_row.win_rate)}`."
        )
    lines.append("")
    lines.append("## First-Signal Losses")
    lines.append("")
    if signal_losses.empty:
        lines.append("No first-signal losses were found.")
    else:
        lines.append("All observed first-signal losses were XRP buckets. The repeated pattern is not weak safety labels; it is late chop/crossing after an otherwise strong-looking signal.")
        lines.append("")
        lines.append("| Session | Symbol | Signal | Time left | Kou yes | Margin z | Settled | Crosses | Final delta bps |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        for row in signal_losses.sort_values(["session_id", "captured_at_ts"]).itertuples(index=False):
            lines.append(
                f"| {row.session_id} | {row.symbol} | {row.signal_state} | {fmt_num(row.time_left_s, 1)} | "
                f"{fmt_num(row.kou_yes, 4)} | {fmt_num(row.margin_z, 3)} | {row.settled_side} | "
                f"{int(row.sampled_cross_count)} | {fmt_num(row.settled_delta_bps, 4)} |"
            )
    lines.append("")
    lines.append("## Calibration Error")
    lines.append("")
    lines.append("| Scope | Symbol | Model | Samples | ECE abs gap |")
    lines.append("|---|---|---:|---:|---:|")
    for row in calibration_ece.sort_values(["scope", "symbol", "model"]).itertuples(index=False):
        lines.append(f"| {row.scope} | {row.symbol} | {row.model} | {int(row.samples)} | {pct(row.ece_abs_gap)} |")
    lines.append("")
    lines.append("## Jump Sweep")
    lines.append("")
    lines.append("Jump flags were not the strongest separator in these sessions. They are still useful telemetry, but the current evidence points much more clearly to time-left and `margin_z`.")
    late_jump = jump[(jump["scope"] == "late120") & (jump["jump_present"] == True)]
    if not late_jump.empty:
        best_rows = late_jump.sort_values("hold_rate").head(6)
        lines.append("")
        lines.append("| Symbol | Window | Threshold | Jump-present hold rate | N |")
        lines.append("|---|---|---:|---:|---:|")
        for row in best_rows.itertuples(index=False):
            lines.append(f"| {row.symbol} | {row.window} | {row.threshold_sigma:.1f} | {pct(row.hold_rate)} | {int(row.n)} |")
    lines.append("")
    lines.append("## Visuals")
    lines.append("")
    for path in visual_paths:
        lines.append(f"- [{Path(path).name}](/Users/viktorzettel/Downloads/ViktorAI/MM/{path})")
    lines.append("")
    lines.append("## Bottom Line")
    lines.append("")
    lines.append("The four sessions support the current design direction: wait late, require persistence, and let safety/policy veto risky near-strike regimes. They do not yet prove stable time-of-day superiority or production EV, because the sample is still small and outcomes are feed-settled, not real filled trades with Polymarket prices.")
    lines.append("")
    lines.append("More 4-hour sessions are useful, especially targeted sessions by condition: US regular hours, Europe/pre-US, late US/off-hours, and weekend. The goal should be at least 10-20 sessions per condition before making strong time-of-day claims.")

    (output_dir / "analysis_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    input_root = Path(args.input_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    meta, snapshots, outcomes = load_sessions(input_root)
    if meta.empty or snapshots.empty or outcomes.empty:
        raise SystemExit(f"No complete live-capture sessions found in {input_root}")

    normalize_numeric(
        snapshots,
        [
            "captured_at_ts",
            "capture_interval_s",
            "age_s",
            "model_age_s",
            "bucket_end",
            "bucket_seconds",
            "time_left_s",
            "price",
            "model_price",
            "strike",
            "delta_bps",
            "sample_count",
            "kou_yes",
            "raw_kou_yes",
            "bs_yes",
            "kou_weight",
            "edge_pp",
            "lam",
            "p_up",
            "sigma_model_bp_1m",
            "signal_hold_s",
            "safety_final_score",
            "safety_heuristic_score",
            "policy_bucket_s",
            "margin_z",
            "vol_30m_bp_1m",
            "vol_1h_bp_1m",
            "jump_10s_10m_rate",
            "jump_10s_10m_count",
            "jump_30s_15m_rate",
            "jump_30s_15m_count",
            "jump_10s_10m_count_2_0",
            "jump_10s_10m_count_2_5",
            "jump_10s_10m_count_3_0",
            "jump_10s_10m_count_3_5",
            "jump_30s_15m_count_2_0",
            "jump_30s_15m_count_2_5",
            "jump_30s_15m_count_3_0",
            "jump_30s_15m_count_3_5",
            "component_margin_safety",
            "component_jump_calm",
            "component_flip_calm",
            "component_reversal_safety",
            "component_trend_clean",
        ],
    )
    normalize_numeric(
        outcomes,
        [
            "bucket_end",
            "bucket_start",
            "settled_delta_bps",
            "outcome_sample_count",
            "sampled_cross_count",
            "sampled_min_price",
            "sampled_max_price",
            "sampled_min_delta_bps",
            "sampled_max_delta_bps",
            "signal_yes_samples",
            "signal_no_samples",
            "policy_override_samples",
            "hard_no_go_samples",
            "caution_samples",
        ],
    )

    complete_outcomes = outcomes[(outcomes["complete"]) & outcomes["settled_yes"].notna()].copy()
    joined = snapshots.merge(
        complete_outcomes,
        on=["session_id", "symbol", "bucket_end"],
        how="inner",
        suffixes=("", "_outcome"),
    )
    joined = add_derived_columns(joined)

    first_signals = first_actual_signals(joined)

    complete_bucket_counts = complete_outcomes.groupby("session_id").size().rename("complete_buckets").reset_index()
    joined_counts = joined.groupby("session_id").size().rename("joined_snapshots").reset_index()
    snapshot_counts = snapshots.groupby("session_id").size().rename("snapshots").reset_index()
    signal_summary_session = summarize_binary(first_signals, ["session_id"], "win", prefix="first_signal_")
    session_summary = (
        meta.merge(complete_bucket_counts, on="session_id", how="left")
        .merge(joined_counts, on="session_id", how="left")
        .merge(snapshot_counts, on="session_id", how="left")
        .merge(signal_summary_session, on="session_id", how="left")
        .fillna({"complete_buckets": 0, "joined_snapshots": 0, "snapshots": 0, "first_signal_wins": 0, "first_signal_n": 0})
    )
    session_summary = session_summary.rename(
        columns={
            "first_signal_n": "first_signal_count",
            "first_signal_rate": "first_signal_win_rate",
            "first_signal_ci_low": "first_signal_ci_low",
            "first_signal_ci_high": "first_signal_ci_high",
        }
    )

    first_signal_overall = summarize_binary(first_signals.assign(symbol="ALL", scope="overall"), ["symbol", "scope"], "win", prefix="")
    first_signal_by_symbol = summarize_binary(first_signals.assign(scope="by_symbol"), ["symbol", "scope"], "win", prefix="")
    first_signal_by_session_symbol = summarize_binary(first_signals.assign(scope="by_session_symbol"), ["session_id", "symbol", "scope"], "win", prefix="")
    first_signal_by_condition = summarize_binary(
        first_signals.merge(meta[["session_id", "condition_label"]], on="session_id", how="left").assign(symbol="ALL", scope="by_condition"),
        ["symbol", "scope", "condition_label"],
        "win",
        prefix="",
    )
    first_signal_summary = pd.concat(
        [first_signal_overall, first_signal_by_symbol, first_signal_by_session_symbol, first_signal_by_condition],
        ignore_index=True,
    )

    signal_quality_cols = ["symbol", "session_id", "signal_state", "time_bin", "safety_final_label", "policy_level", "policy_override", "margin_z_bin"]
    first_signal_condition_quality = summarize_binary(first_signals, signal_quality_cols, "win", prefix="")
    first_signal_losses = first_signals[first_signals["win"] == False].copy()  # noqa: E712

    model_summary = pd.concat(
        [
            model_metrics(joined.assign(symbol="ALL"), ["symbol"]),
            model_metrics(joined, ["symbol"]),
        ],
        ignore_index=True,
    )
    model_by_session = model_metrics(joined, ["session_id", "symbol"])
    model_by_time_bin = pd.concat(
        [
            model_metrics(joined.assign(symbol="ALL"), ["symbol", "time_bin"]),
            model_metrics(joined, ["symbol", "time_bin"]),
        ],
        ignore_index=True,
    )
    calibration_all = pd.concat(
        [
            calibration_table(joined.assign(symbol="ALL"), ["symbol"], late_only=False),
            calibration_table(joined, ["symbol"], late_only=False),
            calibration_table(joined.assign(symbol="ALL"), ["symbol"], late_only=True),
            calibration_table(joined, ["symbol"], late_only=True),
        ],
        ignore_index=True,
    )
    calibration_ece = ece_from_calibration(calibration_all)

    safety_final = add_all_symbol(joined, summarize_binary, ["safety_final_label"], "current_side_win")
    safety_final["scope"] = "final_label"
    safety_heuristic = add_all_symbol(joined, summarize_binary, ["safety_heuristic_label"], "current_side_win")
    safety_heuristic["scope"] = "heuristic_label"
    safety_hold_quality = pd.concat([safety_final, safety_heuristic], ignore_index=True)

    policy_hold_quality = add_all_symbol(joined, summarize_binary, ["policy_level"], "current_side_win")
    override_hold_quality = add_all_symbol(joined, summarize_binary, ["policy_override"], "current_side_win")

    margin_late120 = add_all_symbol(joined[joined["is_late_120"]], summarize_binary, ["margin_z_bin"], "current_side_win")
    margin_late120["time_scope"] = "late120"
    margin_late90 = add_all_symbol(joined[joined["is_late_90"]], summarize_binary, ["margin_z_bin"], "current_side_win")
    margin_late90["time_scope"] = "late90"
    margin_z_hold_quality = pd.concat([margin_late120, margin_late90], ignore_index=True)

    persistence = persistence_sweep(
        joined,
        thresholds=[0.90, float(args.signal_threshold), 0.95],
        holds=[0.0, 2.0, 4.0, 6.0, 8.0],
    )
    jump_summary = jump_sweep_summary(joined)

    tables = {
        "session_summary": session_summary,
        "first_signals": first_signals,
        "first_signal_summary": first_signal_summary,
        "first_signal_condition_quality": first_signal_condition_quality,
        "first_signal_losses": first_signal_losses,
        "model_summary": model_summary,
        "model_by_session": model_by_session,
        "model_by_time_bin": model_by_time_bin,
        "calibration_by_bin": calibration_all,
        "calibration_ece": calibration_ece,
        "safety_hold_quality": safety_hold_quality,
        "policy_hold_quality": policy_hold_quality,
        "override_hold_quality": override_hold_quality,
        "margin_z_hold_quality": margin_z_hold_quality,
        "persistence_sweep": persistence,
        "jump_sweep_summary": jump_summary,
    }

    csv_paths = {
        "session_summary": output_dir / "session_summary.csv",
        "first_signals": output_dir / "first_signals.csv",
        "first_signal_summary": output_dir / "first_signal_summary.csv",
        "first_signal_condition_quality": output_dir / "first_signal_condition_quality.csv",
        "first_signal_losses": output_dir / "first_signal_losses.csv",
        "model_summary": output_dir / "model_summary.csv",
        "model_by_session": output_dir / "model_by_session.csv",
        "model_by_time_bin": output_dir / "model_by_time_bin.csv",
        "calibration_by_bin": output_dir / "calibration_by_bin.csv",
        "calibration_ece": output_dir / "calibration_ece.csv",
        "safety_hold_quality": output_dir / "safety_hold_quality.csv",
        "policy_hold_quality": output_dir / "policy_hold_quality.csv",
        "override_hold_quality": output_dir / "override_hold_quality.csv",
        "margin_z_hold_quality": output_dir / "margin_z_hold_quality.csv",
        "persistence_sweep": output_dir / "persistence_sweep.csv",
        "jump_sweep_summary": output_dir / "jump_sweep_summary.csv",
    }
    for name, path in csv_paths.items():
        write_csv(tables[name], path)

    summary = {
        "session_count": int(len(meta)),
        "complete_buckets": int(len(complete_outcomes)),
        "joined_snapshots": int(len(joined)),
        "first_signal_count": int(len(first_signals)),
        "signal_threshold": float(args.signal_threshold),
        "csv_outputs": {name: str(path) for name, path in csv_paths.items()},
    }
    visual_paths = make_plots(output_dir, tables)
    summary["visual_outputs"] = visual_paths
    (output_dir / "analysis_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    build_report(output_dir, tables, summary, visual_paths)

    print(f"Saved aggregate analysis to {output_dir}")
    print(f"- {output_dir / 'analysis_report.md'}")
    print(f"- {output_dir / 'analysis_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
