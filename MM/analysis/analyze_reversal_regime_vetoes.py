#!/usr/bin/env python3
"""
Cluster-level reversal-regime audit for captured Polymarket grid events.

The row-level grid data contains many correlated rows per market bucket. This
script collapses those rows to one observable bucket-side cluster, then scans
simple hard-veto rules that could skip entire regimes before trading.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GRID_EVENTS = ROOT / "data/live_capture/forensic_analysis/polymarket_grid/polymarket_grid_events_enriched.csv"
DEFAULT_OUTPUT_DIR = ROOT / "data/live_capture/forensic_analysis/reversal_regimes"


@dataclass(frozen=True)
class Rule:
    min_margin_z: float
    max_adverse_30s: float
    max_adverse_60s: float
    max_cross_30s: float
    max_cross_60s: float
    min_margin_z_change_60s: float
    min_time_left_s: float
    min_side_probability: float

    def label(self) -> str:
        parts = []
        if self.min_margin_z > 0:
            parts.append(f"margin_z>={self.min_margin_z:g}")
        if self.max_adverse_30s < 1:
            parts.append(f"adv30<={self.max_adverse_30s:g}")
        if self.max_adverse_60s < 1:
            parts.append(f"adv60<={self.max_adverse_60s:g}")
        if self.max_cross_30s < 90:
            parts.append(f"cross30<={self.max_cross_30s:g}")
        if self.max_cross_60s < 90:
            parts.append(f"cross60<={self.max_cross_60s:g}")
        if self.min_margin_z_change_60s > -90:
            parts.append(f"zchg60>={self.min_margin_z_change_60s:g}")
        if self.min_time_left_s > 0:
            parts.append(f"time_left>={self.min_time_left_s:g}")
        if self.min_side_probability > 0.90:
            parts.append(f"prob>={self.min_side_probability:g}")
        return " and ".join(parts) if parts else "allow_all"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze observable reversal regimes and hard-veto candidates")
    parser.add_argument("--grid-events", default=str(DEFAULT_GRID_EVENTS), help="Enriched Polymarket grid events CSV")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory")
    parser.add_argument("--min-clusters", type=int, default=20, help="Minimum clusters for regime matrix rows")
    parser.add_argument("--min-allowed-clusters", type=int, default=100, help="Minimum allowed clusters for recommended rules")
    parser.add_argument("--top-rules", type=int, default=250, help="Number of rule-scan rows to keep")
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


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    phat = wins / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * n)) / n) / denom
    return max(0.0, center - half), min(1.0, center + half)


def pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{100.0 * float(value):.2f}%"


def markdown_table(df: pd.DataFrame) -> str:
    if df.empty:
        return ""

    def fmt_cell(value: Any) -> str:
        if value is None or pd.isna(value):
            return ""
        if isinstance(value, float):
            return f"{value:.4f}"
        text = str(value)
        return text.replace("|", "\\|").replace("\n", " ")

    cols = list(df.columns)
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(fmt_cell(row[col]) for col in cols) + " |")
    return "\n".join(lines)


def load_cluster_frame(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Grid events file not found: {path}")

    rows = pd.read_csv(path, low_memory=False)
    if rows.empty:
        return rows

    if "known_outcome" in rows:
        rows = rows[rows["known_outcome"].map(lambda value: clean_bool(value) is True)].copy()

    numeric_cols = [
        "time_left_s",
        "threshold",
        "hold_seconds",
        "side_probability",
        "kou_yes",
        "entry_price",
        "estimated_cost",
        "estimated_pnl",
        "pnl_per_share",
        "policy_margin_z",
        "path_15s_cross_count",
        "path_15s_adverse_share",
        "path_15s_margin_z_change",
        "path_30s_cross_count",
        "path_30s_adverse_share",
        "path_30s_margin_z_change",
        "path_60s_cross_count",
        "path_60s_adverse_share",
        "path_60s_margin_z_change",
    ]
    for col in numeric_cols:
        if col in rows:
            rows[col] = pd.to_numeric(rows[col], errors="coerce")

    rows["_captured_at_sort"] = pd.to_datetime(rows.get("captured_at_iso"), errors="coerce")
    rows["_win_bool"] = rows.get("win", pd.Series(index=rows.index, dtype=object)).map(clean_bool)
    rows = rows[rows["_win_bool"].notna()].copy()
    fill_status = rows.get("fill_status", pd.Series("", index=rows.index))
    rows["_filled"] = fill_status.astype(str).isin({"full", "partial"})

    group_cols = ["session_id", "asset", "bucket_end", "side"]
    clusters = (
        rows.sort_values(["_captured_at_sort", "threshold", "hold_seconds"], na_position="last")
        .groupby(group_cols, dropna=False)
        .first()
        .reset_index()
    )
    trigger_counts = rows.groupby(group_cols, dropna=False).size().reset_index(name="trigger_rows")
    clusters = clusters.merge(trigger_counts, on=group_cols, how="left")
    clusters["win_bool"] = clusters["_win_bool"].astype(bool)
    clusters["loss_bool"] = ~clusters["win_bool"]
    return clusters


def add_regime_bins(clusters: pd.DataFrame) -> pd.DataFrame:
    out = clusters.copy()
    out["time_left_band"] = pd.cut(
        out["time_left_s"].fillna(999),
        bins=[-0.1, 10, 30, 60, 90, 9999],
        labels=["00-10s", "10-30s", "30-60s", "60-90s", "90s+"],
    )
    out["margin_z_band"] = pd.cut(
        out["policy_margin_z"].fillna(999),
        bins=[-999, 1.0, 1.5, 2.0, 3.0, 9999],
        labels=["<1.0", "1.0-1.5", "1.5-2.0", "2.0-3.0", ">=3.0"],
    )
    for horizon in ["30s", "60s"]:
        out[f"adverse_{horizon}_band"] = pd.cut(
            out[f"path_{horizon}_adverse_share"].fillna(0),
            bins=[-0.001, 0.05, 0.20, 0.35, 0.50, 1.001],
            labels=["0-5%", "5-20%", "20-35%", "35-50%", ">50%"],
        )
        out[f"cross_{horizon}_band"] = pd.cut(
            out[f"path_{horizon}_cross_count"].fillna(0),
            bins=[-0.1, 0, 1, 3, 999],
            labels=["0", "1", "2-3", "4+"],
        )
        out[f"z_change_{horizon}_band"] = pd.cut(
            out[f"path_{horizon}_margin_z_change"].fillna(999),
            bins=[-999, 0, 0.5, 1.0, 2.0, 999],
            labels=["<0", "0-0.5", "0.5-1.0", "1.0-2.0", ">=2.0"],
        )
    return out


def summarize_group(df: pd.DataFrame, group_cols: list[str], min_clusters: int) -> pd.DataFrame:
    grouped = (
        df.groupby(group_cols, dropna=False, observed=False)
        .agg(
            clusters=("win_bool", "size"),
            wins=("win_bool", "sum"),
            losses=("loss_bool", "sum"),
            avg_entry=("entry_price", "mean"),
            avg_margin_z=("policy_margin_z", "mean"),
            avg_adverse_30s=("path_30s_adverse_share", "mean"),
            avg_adverse_60s=("path_60s_adverse_share", "mean"),
            avg_cross_60s=("path_60s_cross_count", "mean"),
            avg_z_change_60s=("path_60s_margin_z_change", "mean"),
        )
        .reset_index()
    )
    grouped = grouped[grouped["clusters"] >= min_clusters].copy()
    if grouped.empty:
        return grouped
    grouped["win_rate"] = grouped["wins"] / grouped["clusters"]
    grouped["loss_rate"] = grouped["losses"] / grouped["clusters"]
    ci = grouped.apply(lambda row: wilson_ci(int(row["wins"]), int(row["clusters"])), axis=1, result_type="expand")
    grouped["ci_low"] = ci[0]
    grouped["ci_high"] = ci[1]
    return grouped.sort_values(["loss_rate", "losses", "clusters"], ascending=[False, False, False])


def scan_rules(clusters: pd.DataFrame, top_rules: int, min_allowed_clusters: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    base_n = len(clusters)
    base_wins = int(clusters["win_bool"].sum())
    base_losses = base_n - base_wins
    base_win_rate = base_wins / base_n if base_n else 0.0

    mz = clusters["policy_margin_z"].fillna(999).to_numpy(float)
    adv30 = clusters["path_30s_adverse_share"].fillna(0).to_numpy(float)
    adv60 = clusters["path_60s_adverse_share"].fillna(0).to_numpy(float)
    cross30 = clusters["path_30s_cross_count"].fillna(0).to_numpy(float)
    cross60 = clusters["path_60s_cross_count"].fillna(0).to_numpy(float)
    zchg60 = clusters["path_60s_margin_z_change"].fillna(999).to_numpy(float)
    time_left = clusters["time_left_s"].fillna(999).to_numpy(float)
    side_prob = clusters["side_probability"].fillna(1).to_numpy(float)
    wins_arr = clusters["win_bool"].to_numpy(bool)
    entry_raw = clusters["entry_price"].to_numpy(float)
    entry_valid = np.isfinite(entry_raw)
    entry_zero = np.nan_to_num(entry_raw, nan=0.0, posinf=0.0, neginf=0.0)
    pnl_per_share = np.nan_to_num(clusters["pnl_per_share"].to_numpy(float), nan=0.0, posinf=0.0, neginf=0.0)
    estimated_cost = np.nan_to_num(clusters["estimated_cost"].to_numpy(float), nan=0.0, posinf=0.0, neginf=0.0)
    estimated_pnl = np.nan_to_num(clusters["estimated_pnl"].to_numpy(float), nan=0.0, posinf=0.0, neginf=0.0)
    filled_arr = clusters.get("_filled", pd.Series(False, index=clusters.index)).astype(bool).to_numpy()

    rules: list[dict[str, Any]] = []
    for rule in (
        Rule(min_margin_z, max_adv30, max_adv60, max_cross30, max_cross60, min_zchg60, min_time_left, min_prob)
        for min_margin_z in [0.0, 1.0, 1.5, 2.0, 2.5, 3.0]
        for max_adv30 in [1.01, 0.75, 0.50, 0.35, 0.20, 0.05]
        for max_adv60 in [1.01, 0.75, 0.50, 0.35, 0.20, 0.05]
        for max_cross30 in [99.0, 2.0, 1.0, 0.0]
        for max_cross60 in [99.0, 4.0, 2.0, 1.0, 0.0]
        for min_zchg60 in [-99.0, 0.0, 0.5, 1.0]
        for min_time_left in [0.0, 5.0, 10.0]
        for min_prob in [0.90, 0.92, 0.94, 0.96]
    ):
        allow = (
            (mz >= rule.min_margin_z)
            & (adv30 <= rule.max_adverse_30s)
            & (adv60 <= rule.max_adverse_60s)
            & (cross30 <= rule.max_cross_30s)
            & (cross60 <= rule.max_cross_60s)
            & (zchg60 >= rule.min_margin_z_change_60s)
            & (time_left >= rule.min_time_left_s)
            & (side_prob >= rule.min_side_probability)
        )
        allowed = int(allow.sum())
        if allowed < 1:
            continue
        wins = int(wins_arr[allow].sum())
        losses = allowed - wins
        ci_low, ci_high = wilson_ci(wins, allowed)
        filled_allow = allow & filled_arr
        filled_cost = float(estimated_cost[filled_allow].sum())
        filled_pnl = float(estimated_pnl[filled_allow].sum())
        if filled_cost > 0:
            cost = filled_cost
            pnl = filled_pnl
        else:
            cost = float(entry_zero[allow & entry_valid].sum())
            pnl = float(pnl_per_share[allow].sum())
        roi = None if cost <= 0 else pnl / cost
        valid_entry_allow = allow & entry_valid
        avg_entry = None if not valid_entry_allow.any() else float(entry_raw[valid_entry_allow].mean())
        losses_avoided = base_losses - losses
        winners_blocked = base_wins - wins
        allowed_frac = allowed / base_n if base_n else 0.0
        win_rate = wins / allowed if allowed else None
        lift = None if win_rate is None else win_rate - base_win_rate
        score = (
            (lift or 0.0) * 100.0
            + (losses_avoided / max(base_losses, 1)) * 10.0
            - (winners_blocked / max(base_wins, 1)) * 3.0
        )
        rules.append(
            {
                "rule": rule.label(),
                "allowed": allowed,
                "abstained": base_n - allowed,
                "allowed_frac": allowed_frac,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "avg_entry": avg_entry,
                "ci_low_minus_avg_entry": None if ci_low is None or avg_entry is None else ci_low - avg_entry,
                "paper_cost": cost,
                "paper_pnl": pnl,
                "paper_roi": roi,
                "losses_avoided": losses_avoided,
                "winners_blocked": winners_blocked,
                "base_win_rate_lift": lift,
                "score": score,
                **rule.__dict__,
            }
        )

    all_rules = pd.DataFrame(rules)
    if all_rules.empty:
        return all_rules, all_rules

    out = all_rules.sort_values(["score", "ci_low", "win_rate"], ascending=[False, False, False]).head(top_rules).copy()

    recommendations: list[pd.DataFrame] = []
    profiles = [
        ("balanced", 0.65, 0.90),
        ("selective_halfish", 0.40, 0.65),
        ("very_selective", 0.20, 0.40),
    ]
    for profile, lo, hi in profiles:
        sub = all_rules[
            (all_rules["allowed"] >= min_allowed_clusters)
            & (all_rules["allowed_frac"] >= lo)
            & (all_rules["allowed_frac"] <= hi)
            & (all_rules["losses_avoided"] > 0)
            & (all_rules["base_win_rate_lift"] > 0)
        ].copy()
        if sub.empty:
            continue
        sub = sub.sort_values(
            ["ci_low_minus_avg_entry", "ci_low", "win_rate", "losses_avoided", "paper_roi"],
            ascending=[False, False, False, False, False],
        ).head(5)
        sub.insert(0, "profile", profile)
        recommendations.append(sub)

    rec = pd.concat(recommendations, ignore_index=True) if recommendations else pd.DataFrame()
    return out, rec


def write_report(
    path: Path,
    clusters: pd.DataFrame,
    feature_risk: pd.DataFrame,
    matrix: pd.DataFrame,
    recommendations: pd.DataFrame,
) -> None:
    total = len(clusters)
    wins = int(clusters["win_bool"].sum())
    losses = total - wins
    ci_low, ci_high = wilson_ci(wins, total)
    loss_subset = clusters[~clusters["win_bool"]]
    lines = [
        "# Reversal Regime Veto Audit",
        "",
        "This report collapses Polymarket grid rows to one observable `session x asset x bucket x side` cluster before evaluating regime risk.",
        "",
        "## Baseline",
        "",
        f"- clusters: `{total}`",
        f"- wins: `{wins}`",
        f"- losses: `{losses}`",
        f"- cluster win rate: `{pct(wins / total if total else None)}`",
        f"- Wilson low/high: `{pct(ci_low)}` / `{pct(ci_high)}`",
        "",
        "## Loss Shape",
        "",
        f"- loss clusters with prior 60s crossing: `{int((loss_subset['path_60s_cross_count'].fillna(0) > 0).sum())}` / `{losses}`",
        f"- loss clusters with prior 60s adverse share above 35%: `{int((loss_subset['path_60s_adverse_share'].fillna(0) > 0.35).sum())}` / `{losses}`",
        f"- loss clusters with margin_z below 2.0: `{int((loss_subset['policy_margin_z'].fillna(999) < 2.0).sum())}` / `{losses}`",
        "",
        "Interpretation: reversal regimes are partly observable through prior crossing, adverse-share, and low margin-z. A minority of losses still looked clean before failure, so a veto layer can reduce loss frequency but cannot eliminate all losses.",
        "",
        "## Top Single-Feature Risk Cells",
        "",
    ]
    if feature_risk.empty:
        lines.append("No feature cells met the minimum sample threshold.")
    else:
        show_cols = ["feature", "value", "clusters", "losses", "loss_rate", "win_rate", "ci_low"]
        lines.append(markdown_table(feature_risk[show_cols].head(12)))

    lines.extend(["", "## Top Multi-Feature Regime Cells", ""])
    if matrix.empty:
        lines.append("No regime cells met the minimum sample threshold.")
    else:
        show_cols = [
            "asset",
            "side",
            "time_left_band",
            "margin_z_band",
            "cross_60s_band",
            "adverse_60s_band",
            "clusters",
            "losses",
            "loss_rate",
        ]
        lines.append(markdown_table(matrix[show_cols].head(12)))

    lines.extend(["", "## Candidate Hard-Veto Profiles", ""])
    if recommendations.empty:
        lines.append("No recommended rules met the configured filters.")
    else:
        show_cols = [
            "profile",
            "rule",
            "allowed",
            "allowed_frac",
            "losses",
            "win_rate",
            "ci_low",
            "paper_roi",
            "losses_avoided",
            "winners_blocked",
        ]
        lines.append(markdown_table(recommendations[show_cols]))

    lines.extend(
        [
            "",
            "## Production Reading",
            "",
            "- It is reasonable to skip whole regimes, even if that removes many markets.",
            "- The safest next research path is to validate a selective veto candidate out-of-sample, not to trade live.",
            "- Rules that skip roughly half the clusters should be treated as capital-preservation candidates, not final production logic.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    clusters = add_regime_bins(load_cluster_frame(Path(args.grid_events)))
    if clusters.empty:
        raise RuntimeError("No known-outcome grid clusters found")

    clusters.to_csv(output_dir / "cluster_events.csv", index=False)
    clusters[~clusters["win_bool"]].to_csv(output_dir / "loss_clusters.csv", index=False)

    feature_frames = []
    for feature in [
        "asset",
        "side",
        "time_left_band",
        "margin_z_band",
        "cross_30s_band",
        "adverse_30s_band",
        "cross_60s_band",
        "adverse_60s_band",
        "z_change_60s_band",
    ]:
        one = summarize_group(clusters, [feature], args.min_clusters)
        if not one.empty:
            one.insert(0, "feature", feature)
            one = one.rename(columns={feature: "value"})
            feature_frames.append(one)
    feature_risk = pd.concat(feature_frames, ignore_index=True) if feature_frames else pd.DataFrame()
    feature_risk.to_csv(output_dir / "feature_risk_matrix.csv", index=False)

    matrix = summarize_group(
        clusters,
        ["asset", "side", "time_left_band", "margin_z_band", "cross_60s_band", "adverse_60s_band"],
        args.min_clusters,
    )
    matrix.to_csv(output_dir / "cluster_regime_matrix.csv", index=False)

    rules, recommendations = scan_rules(clusters, args.top_rules, args.min_allowed_clusters)
    rules.to_csv(output_dir / "simple_veto_scan.csv", index=False)
    recommendations.to_csv(output_dir / "recommended_veto_candidates.csv", index=False)

    summary = {
        "clusters": int(len(clusters)),
        "wins": int(clusters["win_bool"].sum()),
        "losses": int((~clusters["win_bool"]).sum()),
        "cluster_win_rate": float(clusters["win_bool"].mean()),
        "feature_risk_rows": int(len(feature_risk)),
        "regime_matrix_rows": int(len(matrix)),
        "rule_scan_rows_kept": int(len(rules)),
        "recommended_rules": int(len(recommendations)),
        "outputs": {
            "cluster_events": str((output_dir / "cluster_events.csv").resolve()),
            "loss_clusters": str((output_dir / "loss_clusters.csv").resolve()),
            "feature_risk_matrix": str((output_dir / "feature_risk_matrix.csv").resolve()),
            "cluster_regime_matrix": str((output_dir / "cluster_regime_matrix.csv").resolve()),
            "simple_veto_scan": str((output_dir / "simple_veto_scan.csv").resolve()),
            "recommended_veto_candidates": str((output_dir / "recommended_veto_candidates.csv").resolve()),
            "report": str((output_dir / "reversal_regime_report.md").resolve()),
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_report(output_dir / "reversal_regime_report.md", clusters, feature_risk, matrix, recommendations)

    print(f"Wrote reversal-regime audit to {output_dir}")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
