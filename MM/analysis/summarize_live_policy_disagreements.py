#!/usr/bin/env python3
"""
Summarize live late-policy disagreement logs from the web bot.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize live policy disagreement events")
    parser.add_argument(
        "--input",
        default="data/live_policy_disagreements.jsonl",
        help="Path to disagreement JSONL log",
    )
    parser.add_argument(
        "--output-dir",
        default="data/live_policy_disagreement_summary",
        help="Directory for generated summaries",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> pd.DataFrame:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return pd.DataFrame(rows)


def main() -> None:
    args = parse_args()
    in_path = Path(args.input)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not in_path.exists():
        raise SystemExit(f"Missing input log: {in_path}")

    df = load_jsonl(in_path)
    if df.empty:
        raise SystemExit(f"No rows in input log: {in_path}")

    for col in ("time_left_s", "base_trade_score", "final_trade_score", "late_policy_bucket_s", "sample_count"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    by_asset_level = (
        df.groupby(["symbol", "late_policy_level"], dropna=False)
        .size()
        .rename("events")
        .reset_index()
        .sort_values(["symbol", "events"], ascending=[True, False])
    )
    by_bucket = (
        df.groupby(["symbol", "late_policy_bucket_s", "late_policy_level"], dropna=False)
        .size()
        .rename("events")
        .reset_index()
        .sort_values(["symbol", "late_policy_bucket_s", "events"], ascending=[True, True, False])
    )
    label_transitions = (
        df.groupby(["symbol", "base_trade_score_label", "final_trade_score_label"], dropna=False)
        .size()
        .rename("events")
        .reset_index()
        .sort_values(["symbol", "events"], ascending=[True, False])
    )

    summary = {
        "rows": int(len(df)),
        "symbols": sorted(str(v) for v in df["symbol"].dropna().unique()),
        "time_left_s_range": [
            None if df["time_left_s"].dropna().empty else float(df["time_left_s"].min()),
            None if df["time_left_s"].dropna().empty else float(df["time_left_s"].max()),
        ],
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    by_asset_level.to_csv(out_dir / "by_asset_policy_level.csv", index=False)
    by_bucket.to_csv(out_dir / "by_bucket.csv", index=False)
    label_transitions.to_csv(out_dir / "label_transitions.csv", index=False)

    print("Saved:")
    print(f"- {out_dir / 'summary.json'}")
    print(f"- {out_dir / 'by_asset_policy_level.csv'}")
    print(f"- {out_dir / 'by_bucket.csv'}")
    print(f"- {out_dir / 'label_transitions.csv'}")


if __name__ == "__main__":
    main()
