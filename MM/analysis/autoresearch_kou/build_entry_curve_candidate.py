#!/usr/bin/env python3
"""
Build a train-only entry-cap candidate for the Kou autoresearch harness.

The builder learns simple entry-price caps from chronological train sessions
only, writes a generated candidate module, and emits a report comparing risk
profiles on train/validation/test splits.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GRID_EVENTS = ROOT / "data/live_capture/forensic_analysis/polymarket_grid/polymarket_grid_events_enriched.csv"
DEFAULT_OUTPUT_DIR = ROOT / "analysis/autoresearch_kou/generated_entry_curves"
DEFAULT_CANDIDATE = ROOT / "analysis/autoresearch_kou/candidates/generated_entry_curve_safety_first_v1.py"
CAP_GRID = [0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98, 0.99, 1.00]
THRESHOLDS = [0.90, 0.91, 0.92, 0.93, 0.94, 0.95, 0.96]
PROFILES = ["safety_first", "balanced", "roi_seek"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate train-only entry cap candidate")
    parser.add_argument("--grid-events", default=str(DEFAULT_GRID_EVENTS), help="Enriched Polymarket grid events CSV")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for builder reports")
    parser.add_argument("--candidate-out", default=str(DEFAULT_CANDIDATE), help="Generated candidate path")
    parser.add_argument("--profile", choices=PROFILES, default="safety_first", help="Profile to write as candidate")
    parser.add_argument("--train-share", type=float, default=0.60, help="Chronological train session share")
    parser.add_argument("--validation-share", type=float, default=0.20, help="Chronological validation session share")
    parser.add_argument("--min-group-clusters", type=int, default=12, help="Minimum train clusters before a group-specific cap can be learned")
    parser.add_argument("--min-allowed-frac", type=float, default=0.25, help="Minimum allowed fraction inside a train group")
    parser.add_argument("--min-allowed-clusters", type=int, default=8, help="Minimum allowed train clusters inside a group")
    return parser.parse_args()


def clean_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n"}:
        return False
    return None


def session_split_map(sessions: Iterable[str], train_share: float, validation_share: float) -> dict[str, str]:
    ordered = sorted(str(s) for s in sessions if str(s) and str(s) != "nan")
    n = len(ordered)
    if n == 0:
        return {}
    if n == 1:
        return {ordered[0]: "test"}
    if n == 2:
        return {ordered[0]: "train", ordered[1]: "test"}
    train_n = max(1, int(math.floor(n * train_share)))
    validation_n = max(1, int(math.floor(n * validation_share)))
    if train_n + validation_n >= n:
        overflow = train_n + validation_n - (n - 1)
        train_n = max(1, train_n - overflow)
    test_start = train_n + validation_n
    if test_start >= n:
        test_start = n - 1
    out: dict[str, str] = {}
    for idx, session in enumerate(ordered):
        if idx < train_n:
            out[session] = "train"
        elif idx < test_start:
            out[session] = "validation"
        else:
            out[session] = "test"
    return out


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    phat = wins / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * n)) / n) / denom
    return max(0.0, center - half), min(1.0, center + half)


def load_grid(path: Path, train_share: float, validation_share: float) -> tuple[pd.DataFrame, dict[str, str]]:
    rows = pd.read_csv(path, low_memory=False)
    if rows.empty:
        raise RuntimeError(f"No grid rows found in {path}")
    rows = rows[rows["known_outcome"].map(lambda value: clean_bool(value) is True)].copy()
    numeric_cols = [
        "time_left_s",
        "threshold",
        "hold_seconds",
        "side_probability",
        "entry_price",
        "estimated_cost",
        "estimated_pnl",
        "pnl_per_share",
        "path_60s_adverse_share",
        "path_60s_margin_z_change",
    ]
    for col in numeric_cols:
        if col in rows:
            rows[col] = pd.to_numeric(rows[col], errors="coerce")
    rows["win_bool"] = rows["win"].map(clean_bool)
    rows = rows[rows["win_bool"].notna()].copy()
    rows["win_bool"] = rows["win_bool"].astype(bool)
    rows["filled"] = rows.get("fill_status", "").astype(str).isin({"full", "partial"})
    rows["captured_at_sort"] = pd.to_datetime(rows.get("captured_at_iso"), errors="coerce")
    rows["asset"] = rows["asset"].astype(str).str.lower()
    rows["side"] = rows["side"].astype(str).str.lower()
    rows["threshold"] = rows["threshold"].round(2)
    split_map = session_split_map(rows["session_id"].dropna().astype(str).unique(), train_share, validation_share)
    rows["split"] = rows["session_id"].astype(str).map(split_map)
    rows = rows[rows["split"].isin({"train", "validation", "test"})].copy()
    return rows, split_map


def clean_regime_mask(rows: pd.DataFrame) -> pd.Series:
    return (
        (rows["time_left_s"].fillna(999.0) >= 5.0)
        & (rows["path_60s_adverse_share"].fillna(0.0) <= 0.05)
        & (rows["path_60s_margin_z_change"].fillna(999.0) >= 1.0)
    )


def representative_clusters(rows: pd.DataFrame) -> pd.DataFrame:
    if rows.empty:
        return rows.copy()
    group_cols = ["session_id", "asset", "bucket_end", "side"]
    return (
        rows.sort_values(["captured_at_sort", "threshold", "hold_seconds"], na_position="last")
        .groupby(group_cols, dropna=False)
        .first()
        .reset_index()
    )


def summarize_allowed(rows: pd.DataFrame) -> dict[str, Any]:
    clusters = representative_clusters(rows)
    if clusters.empty:
        return {
            "allowed": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "ci_low": None,
            "ci_high": None,
            "avg_entry": None,
            "ci_low_minus_avg_entry": None,
            "paper_cost": 0.0,
            "paper_pnl": 0.0,
            "paper_roi": None,
        }

    wins = int(clusters["win_bool"].sum())
    n = int(len(clusters))
    ci_low, ci_high = wilson_ci(wins, n)
    avg_entry = None if clusters["entry_price"].dropna().empty else float(clusters["entry_price"].mean())
    filled = clusters["filled"].astype(bool)
    filled_cost = float(clusters.loc[filled, "estimated_cost"].fillna(0.0).sum())
    filled_pnl = float(clusters.loc[filled, "estimated_pnl"].fillna(0.0).sum())
    if filled_cost > 0:
        paper_cost = filled_cost
        paper_pnl = filled_pnl
    else:
        paper_cost = float(clusters["entry_price"].fillna(0.0).sum())
        paper_pnl = float(clusters["pnl_per_share"].fillna(0.0).sum())

    return {
        "allowed": n,
        "wins": wins,
        "losses": n - wins,
        "win_rate": wins / n,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "avg_entry": avg_entry,
        "ci_low_minus_avg_entry": None if ci_low is None or avg_entry is None else ci_low - avg_entry,
        "paper_cost": paper_cost,
        "paper_pnl": paper_pnl,
        "paper_roi": None if paper_cost <= 0 else paper_pnl / paper_cost,
    }


def apply_cap_table(rows: pd.DataFrame, cap_table: dict[str, dict[str, dict[str, float]]]) -> pd.DataFrame:
    if rows.empty:
        return rows.copy()

    def cap_for(row: pd.Series) -> float:
        asset = str(row["asset"]).lower()
        side = str(row["side"]).lower()
        threshold = f"{float(row['threshold']):.2f}"
        return cap_table.get(asset, {}).get(side, {}).get(threshold, 1.0)

    caps = rows.apply(cap_for, axis=1)
    return rows[rows["entry_price"].fillna(0.0).to_numpy() <= caps.to_numpy()].copy()


def profile_score(profile: str, metrics: dict[str, Any], base_allowed: int) -> float:
    if metrics["allowed"] <= 0 or base_allowed <= 0:
        return -999.0
    allowed_frac = metrics["allowed"] / base_allowed
    roi = metrics["paper_roi"] if metrics["paper_roi"] is not None else -1.0
    ci_entry = metrics["ci_low_minus_avg_entry"]
    ci_entry = ci_entry if ci_entry is not None else -1.0
    loss_rate = metrics["losses"] / metrics["allowed"]
    if profile == "safety_first":
        return ci_entry + 0.025 * allowed_frac + 0.20 * roi - 0.08 * loss_rate
    if profile == "balanced":
        return 0.50 * ci_entry + 0.50 * roi + 0.025 * allowed_frac - 0.08 * loss_rate
    if profile == "roi_seek":
        return roi + 0.020 * allowed_frac - 0.10 * loss_rate
    raise ValueError(f"Unknown profile: {profile}")


def learn_cap_tables(
    rows: pd.DataFrame,
    *,
    min_group_clusters: int,
    min_allowed_clusters: int,
    min_allowed_frac: float,
) -> tuple[dict[str, dict[str, dict[str, float]]], dict[str, list[dict[str, Any]]]]:
    train = rows[(rows["split"] == "train") & clean_regime_mask(rows)].copy()
    learned: dict[str, dict[str, dict[str, float]]] = {
        profile: {"eth": {"yes": {}, "no": {}}, "xrp": {"yes": {}, "no": {}}}
        for profile in PROFILES
    }
    diagnostics: dict[str, list[dict[str, Any]]] = {profile: [] for profile in PROFILES}

    for asset in ["eth", "xrp"]:
        for side in ["yes", "no"]:
            for threshold in THRESHOLDS:
                group = train[
                    (train["asset"] == asset)
                    & (train["side"] == side)
                    & (train["threshold"] == threshold)
                ].copy()
                base = summarize_allowed(group)
                base_allowed = int(base["allowed"])
                for profile in PROFILES:
                    selected_cap = 1.0
                    selected_metrics = summarize_allowed(group[group["entry_price"].fillna(0.0) <= selected_cap])
                    selected_score = profile_score(profile, selected_metrics, max(base_allowed, 1))
                    if base_allowed >= min_group_clusters:
                        for cap in CAP_GRID:
                            capped = group[group["entry_price"].fillna(0.0) <= cap].copy()
                            metrics = summarize_allowed(capped)
                            if metrics["allowed"] < min_allowed_clusters:
                                continue
                            if metrics["allowed"] < math.ceil(base_allowed * min_allowed_frac):
                                continue
                            score = profile_score(profile, metrics, base_allowed)
                            if score > selected_score:
                                selected_cap = cap
                                selected_metrics = metrics
                                selected_score = score
                    learned[profile][asset][side][f"{threshold:.2f}"] = selected_cap
                    diagnostics[profile].append(
                        {
                            "asset": asset,
                            "side": side,
                            "threshold": threshold,
                            "base_allowed": base_allowed,
                            "selected_cap": selected_cap,
                            "score": selected_score,
                            **{f"selected_{key}": value for key, value in selected_metrics.items()},
                        }
                    )
    return learned, diagnostics


def evaluate_profile(rows: pd.DataFrame, cap_table: dict[str, dict[str, dict[str, float]]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    clean = rows[clean_regime_mask(rows)].copy()
    for split in ["train", "validation", "test"]:
        split_rows = clean[clean["split"] == split].copy()
        total_clusters = int(summarize_allowed(split_rows)["allowed"])
        allowed_rows = apply_cap_table(split_rows, cap_table)
        metrics = summarize_allowed(allowed_rows)
        metrics["total_clean_clusters"] = total_clusters
        metrics["abstained_after_clean"] = total_clusters - int(metrics["allowed"])
        out[split] = metrics
    return out


def literal_caps(cap_table: dict[str, dict[str, dict[str, float]]]) -> str:
    return json.dumps(cap_table, indent=4, sort_keys=True)


def write_candidate(
    path: Path,
    *,
    profile: str,
    cap_table: dict[str, dict[str, dict[str, float]]],
    split_map: dict[str, str],
    generated_at: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = f'''#!/usr/bin/env python3
"""
Generated train-only entry-curve candidate.

Generated by analysis/autoresearch_kou/build_entry_curve_candidate.py.
Do not hand-edit this file; regenerate it when the training data changes.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


CANDIDATE_NAME = "generated_entry_curve_{profile}_v1"
CANDIDATE_DESCRIPTION = (
    "Train-only {profile} entry caps with clean prior-60s regime filter. "
    "Generated at {generated_at}."
)
TRAINING_SPLIT = {json.dumps(split_map, indent=4, sort_keys=True)}
ENTRY_CAPS = {literal_caps(cap_table)}


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) or math.isinf(number) else number


def _entry_cap(asset: str, side: str, threshold: float | None) -> float:
    threshold_key = f"{{0.90 if threshold is None else round(float(threshold), 2):.2f}}"
    return ENTRY_CAPS.get(asset, {{}}).get(side, {{}}).get(threshold_key, 1.0)


def score_first_signal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {{
        "allow_trade": True,
        "adjusted_prob_yes": _float(row.get("kou_yes"), _float(row.get("bs_yes"))),
        "reason": "first_signal_unchanged",
    }}


def score_grid_event(row: Mapping[str, Any]) -> dict[str, Any]:
    asset = str(row.get("asset") or "").lower()
    side = str(row.get("side") or "").lower()
    threshold = _float(row.get("threshold"), 0.90)
    adverse_60s = _float(row.get("path_60s_adverse_share"), 0.0)
    z_change_60s = _float(row.get("path_60s_margin_z_change"), 999.0)
    time_left = _float(row.get("time_left_s"), 999.0)
    entry_price = _float(row.get("entry_price"))

    if time_left is not None and time_left < 5.0:
        return {{"allow_trade": False, "reason": "too_late_execution_risk"}}

    if adverse_60s is not None and adverse_60s > 0.05:
        return {{"allow_trade": False, "reason": "prior_60s_adverse_exposure"}}

    if z_change_60s is not None and z_change_60s < 1.0:
        return {{"allow_trade": False, "reason": "weak_prior_60s_margin_improvement"}}

    cap = _entry_cap(asset, side, threshold)
    if entry_price is not None and entry_price > cap:
        return {{"allow_trade": False, "reason": f"entry_price_above_{{cap:.2f}}"}}

    return {{"allow_trade": True, "reason": "clean_path_generated_entry_curve"}}
'''
    path.write_text(candidate, encoding="utf-8")


def fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{100.0 * float(value):.2f}%"


def write_markdown_report(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Train-Only Entry Curve Builder",
        "",
        f"- generated at: `{report['generated_at_utc']}`",
        f"- selected profile: `{report['selected_profile']}`",
        f"- candidate: `{report['candidate_out']}`",
        "",
        "## Profile Comparison",
        "",
        "| profile | split | allowed | wins | losses | win_rate | avg_entry | paper_roi | ci_low_minus_avg_entry |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for profile, split_metrics in report["profile_metrics"].items():
        for split, metrics in split_metrics.items():
            allowed = f"{metrics['allowed']}/{metrics['total_clean_clusters']}"
            ci_entry = metrics.get("ci_low_minus_avg_entry")
            lines.append(
                "| "
                + " | ".join(
                    [
                        profile,
                        split,
                        allowed,
                        str(metrics["wins"]),
                        str(metrics["losses"]),
                        fmt_pct(metrics.get("win_rate")),
                        "-" if metrics.get("avg_entry") is None else f"{metrics['avg_entry']:.4f}",
                        fmt_pct(metrics.get("paper_roi")),
                        "-" if ci_entry is None else f"{100.0 * ci_entry:.2f}pp",
                    ]
                )
                + " |"
            )
    lines.extend(
        [
            "",
            "## Reading",
            "",
            "- `safety_first` preserves more sample and avoids overreacting to tiny cheap-entry cells.",
            "- `roi_seek` may look attractive when cheap contracts happen to win, but it is more sample-starved.",
            "- Treat all generated candidates as shadow/paper candidates until they survive future sessions.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    rows, split_map = load_grid(Path(args.grid_events), args.train_share, args.validation_share)
    cap_tables, diagnostics = learn_cap_tables(
        rows,
        min_group_clusters=args.min_group_clusters,
        min_allowed_clusters=args.min_allowed_clusters,
        min_allowed_frac=args.min_allowed_frac,
    )
    profile_metrics = {
        profile: evaluate_profile(rows, cap_table)
        for profile, cap_table in cap_tables.items()
    }
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_out = Path(args.candidate_out)
    write_candidate(
        candidate_out,
        profile=args.profile,
        cap_table=cap_tables[args.profile],
        split_map=split_map,
        generated_at=generated_at,
    )
    report = {
        "generated_at_utc": generated_at,
        "selected_profile": args.profile,
        "grid_events": str(Path(args.grid_events).resolve()),
        "candidate_out": str(candidate_out.resolve()),
        "split_map": split_map,
        "settings": {
            "train_share": args.train_share,
            "validation_share": args.validation_share,
            "min_group_clusters": args.min_group_clusters,
            "min_allowed_clusters": args.min_allowed_clusters,
            "min_allowed_frac": args.min_allowed_frac,
            "cap_grid": CAP_GRID,
        },
        "cap_tables": cap_tables,
        "diagnostics": diagnostics,
        "profile_metrics": profile_metrics,
    }
    json_path = output_dir / f"entry_curve_builder_report_{args.profile}.json"
    md_path = output_dir / f"entry_curve_builder_report_{args.profile}.md"
    latest_json_path = output_dir / "entry_curve_builder_report.json"
    latest_md_path = output_dir / "entry_curve_builder_report.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown_report(md_path, report)
    latest_json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown_report(latest_md_path, report)
    print(f"Wrote generated candidate: {candidate_out}")
    print(f"Wrote builder report: {json_path}")
    print(f"Wrote builder summary: {md_path}")
    for profile, split_metrics in profile_metrics.items():
        test = split_metrics["test"]
        print(
            f"{profile:12s} test allowed={test['allowed']}/{test['total_clean_clusters']} "
            f"wins={test['wins']} losses={test['losses']} "
            f"win_rate={fmt_pct(test['win_rate'])} roi={fmt_pct(test['paper_roi'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
