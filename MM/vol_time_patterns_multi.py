#!/usr/bin/env python3
"""
Generate time-of-day/week heatmaps for multiple assets (SOL/XRP/etc).
Uses same methodology as vol_time_patterns.py.
"""

import argparse
import os
import numpy as np
import pandas as pd

TZ_NY = "America/New_York"
TZ_LON = "Europe/London"
TZ_TOK = "Asia/Tokyo"


def process_asset(csv_path: str, out_dir: str, asset: str):
    os.makedirs(out_dir, exist_ok=True)
    df = pd.read_csv(csv_path)
    # Normalize datetimes
    if "open_time_iso" in df.columns:
        dt = pd.to_datetime(df["open_time_iso"], utc=True)
    else:
        dt = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["dt_utc"] = dt
    df = df.sort_values("dt_utc").reset_index(drop=True)

    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["ret"] = np.log(df["close"]).diff()
    df["abs_ret"] = df["ret"].abs()
    df["hl_range"] = (df["high"] - df["low"]) / df["close"].shift(1)
    df["hl_range"] = df["hl_range"].replace([np.inf, -np.inf], np.nan)

    sigma = df["ret"].std(skipna=True)
    df["jump_2"] = df["abs_ret"] >= 2 * sigma
    df["jump_3"] = df["abs_ret"] >= 3 * sigma

    df["dt_ny"] = df["dt_utc"].dt.tz_convert(TZ_NY)
    df["ny_hour"] = df["dt_ny"].dt.hour
    df["ny_wday"] = df["dt_ny"].dt.day_name()
    df["ny_halfhour"] = df["dt_ny"].dt.hour * 2 + (df["dt_ny"].dt.minute // 30)

    # Half-hour stats
    halfhour = df.groupby("ny_halfhour").agg(
        mean_abs_ret=("abs_ret", "mean"),
        median_abs_ret=("abs_ret", "median"),
        mean_hl_range=("hl_range", "mean"),
        jump2_rate=("jump_2", "mean"),
        jump3_rate=("jump_3", "mean"),
        samples=("abs_ret", "count"),
    )
    halfhour.to_csv(os.path.join(out_dir, f"{asset}_halfhour_stats_ny.csv"))

    # Half-hour x weekday (NY)
    heat_half = df.pivot_table(index="ny_wday", columns="ny_halfhour", values="abs_ret", aggfunc="mean")
    heat_half.to_csv(os.path.join(out_dir, f"{asset}_heatmap_ny_halfhour_wday.csv"))

    heat_jump = df.pivot_table(index="ny_wday", columns="ny_halfhour", values="jump_2", aggfunc="mean")
    heat_jump.to_csv(os.path.join(out_dir, f"{asset}_heatmap_ny_halfhour_wday_jump2.csv"))

    # Render heatmaps
    try:
        import matplotlib.pyplot as plt

        order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        labels = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]

        # Vol heatmap
        heat_img = heat_half.reindex(order)
        vals = heat_img.to_numpy() * 10000
        fig, ax = plt.subplots(figsize=(13, 4.2))
        im = ax.imshow(vals, aspect="auto", cmap="viridis")
        ax.set_yticks(range(len(order)))
        ax.set_yticklabels(order)
        ax.set_xticks(range(48))
        ax.set_xticklabels(labels, rotation=90, fontsize=7)
        ax.set_xlabel("Half-hour (NY)")
        ax.set_title(f"{asset} Mean 1m Abs Return by NY Half-hour × Weekday (bp)")
        cbar = fig.colorbar(im, ax=ax, orientation="vertical", pad=0.02)
        cbar.set_label("bp")
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, f"{asset}_heatmap_ny_halfhour_wday.png"), dpi=160)
        plt.close(fig)

        # Jump heatmap
        heat_img = heat_jump.reindex(order)
        vals = heat_img.to_numpy() * 100
        fig, ax = plt.subplots(figsize=(13, 4.2))
        im = ax.imshow(vals, aspect="auto", cmap="magma")
        ax.set_yticks(range(len(order)))
        ax.set_yticklabels(order)
        ax.set_xticks(range(48))
        ax.set_xticklabels(labels, rotation=90, fontsize=7)
        ax.set_xlabel("Half-hour (NY)")
        ax.set_title(f"{asset} Jump Rate (≥2σ) by NY Half-hour × Weekday (%)")
        cbar = fig.colorbar(im, ax=ax, orientation="vertical", pad=0.02)
        cbar.set_label("%")
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, f"{asset}_heatmap_ny_halfhour_wday_jump2.png"), dpi=160)
        plt.close(fig)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="analysis_time_patterns_assets", help="Output directory")
    parser.add_argument("--asset", action="append", nargs=2, metavar=("SYMBOL", "CSV"), help="Asset symbol and CSV path")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    if not args.asset:
        raise SystemExit("Provide at least one --asset SYMBOL CSV")

    for sym, csv_path in args.asset:
        sym = sym.upper()
        asset_dir = os.path.join(args.out, sym)
        process_asset(csv_path, asset_dir, sym)
        print(f"Saved {sym} outputs to {asset_dir}")


if __name__ == "__main__":
    main()
