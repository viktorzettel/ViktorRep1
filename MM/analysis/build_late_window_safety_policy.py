#!/usr/bin/env python3
"""
Build draft live safety-policy tables from late-window microstructure outputs.

This converts per-cell late-window analysis into two practical policy artifacts:

1. A per-cell classification table with `clear`, `caution`, or `hard_no_go`
2. A threshold table that the live bot can eventually consume by
   `asset x time_left_s x current_side`

The logic here is intentionally conservative and transparent. It is a draft
policy builder, not the final production veto layer.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build draft late-window safety-policy tables")
    parser.add_argument(
        "--policy-csv",
        default="data/analysis_output_5m_microstructure/late_window_policy.csv",
        help="Path to late_window_policy.csv",
    )
    parser.add_argument(
        "--output-dir",
        default="data/analysis_output_5m_microstructure",
        help="Directory for generated policy tables",
    )
    parser.add_argument(
        "--hard-no-go-min-samples",
        type=int,
        default=200,
        help="Minimum samples for hard no-go cells",
    )
    parser.add_argument(
        "--hard-no-go-max-margin-z",
        type=float,
        default=0.5,
        help="Maximum margin-z bin for hard no-go cells",
    )
    parser.add_argument(
        "--hard-no-go-reversal-rate",
        type=float,
        default=0.33,
        help="Hard no-go reversal-rate threshold",
    )
    parser.add_argument(
        "--hard-no-go-adverse-cross-rate",
        type=float,
        default=0.45,
        help="Hard no-go adverse-cross threshold",
    )
    parser.add_argument(
        "--caution-min-samples",
        type=int,
        default=200,
        help="Minimum samples for caution cells",
    )
    parser.add_argument(
        "--caution-max-margin-z",
        type=float,
        default=1.0,
        help="Maximum margin-z bin for caution cells",
    )
    parser.add_argument(
        "--caution-reversal-rate",
        type=float,
        default=0.18,
        help="Caution reversal-rate threshold",
    )
    parser.add_argument(
        "--caution-adverse-cross-rate",
        type=float,
        default=0.28,
        help="Caution adverse-cross threshold",
    )
    parser.add_argument(
        "--caution-future-adverse-bps",
        type=float,
        default=2.0,
        help="Caution future-adverse-excursion threshold in bps",
    )
    return parser.parse_args()


def classify_rows(df: pd.DataFrame, args: argparse.Namespace) -> pd.DataFrame:
    out = df.copy()

    hard_mask = (
        (out["samples"] >= args.hard_no_go_min_samples)
        & (out["margin_z_bin"] <= args.hard_no_go_max_margin_z)
        & (
            (out["reversal_rate"] >= args.hard_no_go_reversal_rate)
            | (out["adverse_cross_rate"] >= args.hard_no_go_adverse_cross_rate)
        )
    )

    caution_mask = (
        (out["samples"] >= args.caution_min_samples)
        & (out["margin_z_bin"] <= args.caution_max_margin_z)
        & (
            (out["reversal_rate"] >= args.caution_reversal_rate)
            | (out["adverse_cross_rate"] >= args.caution_adverse_cross_rate)
            | (out["future_adverse_bps_median"] >= args.caution_future_adverse_bps)
        )
    )

    out["policy_level"] = "clear"
    out.loc[caution_mask, "policy_level"] = "caution"
    out.loc[hard_mask, "policy_level"] = "hard_no_go"

    reasons: list[str] = []
    for _, row in out.iterrows():
        parts: list[str] = []
        if row["reversal_rate"] >= args.hard_no_go_reversal_rate:
            parts.append("reversal_extreme")
        elif row["reversal_rate"] >= args.caution_reversal_rate:
            parts.append("reversal_elevated")

        if row["adverse_cross_rate"] >= args.hard_no_go_adverse_cross_rate:
            parts.append("adverse_cross_extreme")
        elif row["adverse_cross_rate"] >= args.caution_adverse_cross_rate:
            parts.append("adverse_cross_elevated")

        if row["future_adverse_bps_median"] >= args.caution_future_adverse_bps:
            parts.append("future_adverse_large")

        reasons.append(", ".join(parts))

    out["policy_reason"] = reasons
    return out


def build_thresholds(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    group_cols = ["asset", "time_left_s", "current_side"]
    for keys, group in df.groupby(group_cols, sort=True):
        asset, time_left_s, current_side = keys
        caution_group = group[group["policy_level"].isin(["caution", "hard_no_go"])]
        hard_group = group[group["policy_level"] == "hard_no_go"]

        rows.append(
            {
                "asset": asset,
                "time_left_s": int(time_left_s),
                "current_side": current_side,
                "caution_max_margin_z_bin": (
                    float(caution_group["margin_z_bin"].max()) if not caution_group.empty else None
                ),
                "hard_no_go_max_margin_z_bin": (
                    float(hard_group["margin_z_bin"].max()) if not hard_group.empty else None
                ),
                "caution_cells": int(len(caution_group)),
                "hard_no_go_cells": int(len(hard_group)),
            }
        )

    return pd.DataFrame(rows).sort_values(group_cols).reset_index(drop=True)


def build_summary(cells: pd.DataFrame, thresholds: pd.DataFrame) -> dict[str, object]:
    assets: dict[str, object] = {}
    for asset, group in cells.groupby("asset", sort=True):
        threshold_group = thresholds[thresholds["asset"] == asset]
        assets[asset] = {
            "clear_rows": int((group["policy_level"] == "clear").sum()),
            "caution_rows": int((group["policy_level"] == "caution").sum()),
            "hard_no_go_rows": int((group["policy_level"] == "hard_no_go").sum()),
            "max_caution_margin_z_bin": (
                None
                if threshold_group["caution_max_margin_z_bin"].dropna().empty
                else float(threshold_group["caution_max_margin_z_bin"].dropna().max())
            ),
            "max_hard_no_go_margin_z_bin": (
                None
                if threshold_group["hard_no_go_max_margin_z_bin"].dropna().empty
                else float(threshold_group["hard_no_go_max_margin_z_bin"].dropna().max())
            ),
        }

    return {
        "assets": assets,
        "policy_levels": ["clear", "caution", "hard_no_go"],
    }


def main() -> None:
    args = parse_args()
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    policy_df = pd.read_csv(args.policy_csv)
    cells = classify_rows(policy_df, args)
    thresholds = build_thresholds(cells)
    summary = build_summary(cells, thresholds)

    cells_path = out_dir / "late_window_policy_levels.csv"
    thresholds_path = out_dir / "late_window_safety_thresholds.csv"
    summary_path = out_dir / "late_window_safety_policy_summary.json"

    cells.to_csv(cells_path, index=False)
    thresholds.to_csv(thresholds_path, index=False)
    summary_path.write_text(json.dumps(summary, indent=2))

    print("Saved:")
    print(f"- {cells_path}")
    print(f"- {thresholds_path}")
    print(f"- {summary_path}")


if __name__ == "__main__":
    main()
