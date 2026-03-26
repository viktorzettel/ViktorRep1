#!/usr/bin/env python3
"""
BTC Time-of-Day / Day-of-Week Volatility Analysis
==================================================
Analyzes 1m OHLC data to find intraday/weekly volatility patterns,
session effects (NY/London/Asia), and jump/chop behavior.

Usage:
  python vol_time_patterns.py --in btcusd_1m_6m.csv --out analysis_time_patterns
"""

import argparse
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd


TZ_NY = "America/New_York"
TZ_LON = "Europe/London"
TZ_TOK = "Asia/Tokyo"


def fmt_pct(x: float) -> str:
    return f"{x*100:.2f}%"


def fmt_bp(x: float) -> str:
    return f"{x*10000:.2f} bp"


def compute_efficiency_ratio(close: pd.Series) -> float:
    if len(close) < 2:
        return np.nan
    diff = close.diff().dropna()
    net = close.iloc[-1] - close.iloc[0]
    denom = diff.abs().sum()
    if denom == 0:
        return 0.0
    return abs(net) / denom


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", required=True, help="Input CSV (1m OHLC).")
    parser.add_argument("--out", dest="out", default="analysis_time_patterns", help="Output dir.")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    df = pd.read_csv(args.inp)
    # Normalize datetimes
    if "open_time_iso" in df.columns:
        dt = pd.to_datetime(df["open_time_iso"], utc=True)
    else:
        dt = pd.to_datetime(df["open_time"], unit="s", utc=True)
    df["dt_utc"] = dt
    df = df.sort_values("dt_utc").reset_index(drop=True)

    # Price columns
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Returns and ranges
    df["ret"] = np.log(df["close"]).diff()
    df["abs_ret"] = df["ret"].abs()
    df["hl_range"] = (df["high"] - df["low"]) / df["close"].shift(1)
    df["hl_range"] = df["hl_range"].replace([np.inf, -np.inf], np.nan)

    # Global sigma for jump thresholds
    sigma = df["ret"].std(skipna=True)
    df["jump_2"] = df["abs_ret"] >= 2 * sigma
    df["jump_3"] = df["abs_ret"] >= 3 * sigma

    # Timezone conversions
    df["dt_ny"] = df["dt_utc"].dt.tz_convert(TZ_NY)
    df["dt_lon"] = df["dt_utc"].dt.tz_convert(TZ_LON)
    df["dt_tok"] = df["dt_utc"].dt.tz_convert(TZ_TOK)

    # Primary: NY time
    df["ny_hour"] = df["dt_ny"].dt.hour
    df["ny_wday"] = df["dt_ny"].dt.day_name()
    df["ny_is_weekend"] = df["dt_ny"].dt.weekday >= 5

    # Half-hour stats (NY)
    df["ny_halfhour"] = df["dt_ny"].dt.hour * 2 + (df["dt_ny"].dt.minute // 30)
    halfhour = df.groupby("ny_halfhour").agg(
        mean_abs_ret=("abs_ret", "mean"),
        median_abs_ret=("abs_ret", "median"),
        mean_hl_range=("hl_range", "mean"),
        jump2_rate=("jump_2", "mean"),
        jump3_rate=("jump_3", "mean"),
        samples=("abs_ret", "count"),
    )
    halfhour.to_csv(os.path.join(args.out, "halfhour_stats_ny.csv"))

    # Half-hour stats by weekday (NY) for weekend-aware baselines
    halfhour_dow = df.groupby(["ny_wday", "ny_halfhour"]).agg(
        mean_abs_ret=("abs_ret", "mean"),
        median_abs_ret=("abs_ret", "median"),
        mean_hl_range=("hl_range", "mean"),
        jump2_rate=("jump_2", "mean"),
        jump3_rate=("jump_3", "mean"),
        samples=("abs_ret", "count"),
    )
    halfhour_dow.to_csv(os.path.join(args.out, "halfhour_stats_ny_dow.csv"))

    # 30m heatmap strip (NY) from mean abs returns
    try:
        import matplotlib.pyplot as plt

        half_full = halfhour.reindex(range(48))
        vals = half_full["mean_abs_ret"].to_numpy()
        vals_bp = vals * 10000
        labels = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]

        fig, ax = plt.subplots(figsize=(13, 2.1))
        im = ax.imshow(vals_bp[np.newaxis, :], aspect="auto", cmap="viridis")
        ax.set_yticks([])
        ax.set_xticks(range(48))
        ax.set_xticklabels(labels, rotation=90, fontsize=7)
        ax.set_xlabel("Half-hour (NY)")
        ax.set_title("Mean 1m Abs Return by NY Half-hour (bp)")
        cbar = fig.colorbar(im, ax=ax, orientation="vertical", pad=0.02)
        cbar.set_label("bp")
        fig.tight_layout()
        fig.savefig(os.path.join(args.out, "halfhour_heatmap_ny.png"), dpi=160)
        plt.close(fig)
    except Exception:
        pass

    # Weekday stats (NY)
    weekday = df.groupby("ny_wday").agg(
        mean_abs_ret=("abs_ret", "mean"),
        median_abs_ret=("abs_ret", "median"),
        mean_hl_range=("hl_range", "mean"),
        jump2_rate=("jump_2", "mean"),
        jump3_rate=("jump_3", "mean"),
        samples=("abs_ret", "count"),
    )
    weekday.to_csv(os.path.join(args.out, "weekday_stats_ny.csv"))

    # Heatmap hour x weekday (NY) - CSV (1h)
    heat = df.pivot_table(
        index="ny_wday",
        columns="ny_hour",
        values="abs_ret",
        aggfunc="mean",
    )
    heat.to_csv(os.path.join(args.out, "heatmap_ny_hour_wday.csv"))

    # Heatmap image: weekday x hour (NY) (1h)
    try:
        import matplotlib.pyplot as plt

        order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        heat_img = heat.reindex(order)
        vals = heat_img.to_numpy() * 10000  # bp
        fig, ax = plt.subplots(figsize=(11, 3.8))
        im = ax.imshow(vals, aspect="auto", cmap="viridis")
        ax.set_yticks(range(len(order)))
        ax.set_yticklabels(order)
        ax.set_xticks(range(24))
        ax.set_xticklabels([f"{h:02d}" for h in range(24)], rotation=0)
        ax.set_xlabel("Hour of Day (NY)")
        ax.set_title("Mean 1m Abs Return by NY Hour × Weekday (bp)")
        cbar = fig.colorbar(im, ax=ax, orientation="vertical", pad=0.02)
        cbar.set_label("bp")
        fig.tight_layout()
        fig.savefig(os.path.join(args.out, "heatmap_ny_hour_wday.png"), dpi=160)
        plt.close(fig)
    except Exception:
        pass

    # Heatmap half-hour x weekday (NY) - CSV
    heat_half = df.pivot_table(
        index="ny_wday",
        columns="ny_halfhour",
        values="abs_ret",
        aggfunc="mean",
    )
    heat_half.to_csv(os.path.join(args.out, "heatmap_ny_halfhour_wday.csv"))

    # Heatmap image: weekday x half-hour (NY)
    try:
        import matplotlib.pyplot as plt

        order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        heat_img = heat_half.reindex(order)
        vals = heat_img.to_numpy() * 10000
        labels = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
        fig, ax = plt.subplots(figsize=(13, 4.2))
        im = ax.imshow(vals, aspect="auto", cmap="viridis")
        ax.set_yticks(range(len(order)))
        ax.set_yticklabels(order)
        ax.set_xticks(range(48))
        ax.set_xticklabels(labels, rotation=90, fontsize=7)
        ax.set_xlabel("Half-hour (NY)")
        ax.set_title("Mean 1m Abs Return by NY Half-hour × Weekday (bp)")
        cbar = fig.colorbar(im, ax=ax, orientation="vertical", pad=0.02)
        cbar.set_label("bp")
        fig.tight_layout()
        fig.savefig(os.path.join(args.out, "heatmap_ny_halfhour_wday.png"), dpi=160)
        plt.close(fig)
    except Exception:
        pass

    # Jump-rate heatmap (2σ) by half-hour × weekday (NY)
    heat_jump = df.pivot_table(
        index="ny_wday",
        columns="ny_halfhour",
        values="jump_2",
        aggfunc="mean",
    )
    heat_jump.to_csv(os.path.join(args.out, "heatmap_ny_halfhour_wday_jump2.csv"))
    try:
        import matplotlib.pyplot as plt

        order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        heat_img = heat_jump.reindex(order)
        vals = heat_img.to_numpy() * 100  # %
        labels = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
        fig, ax = plt.subplots(figsize=(13, 4.2))
        im = ax.imshow(vals, aspect="auto", cmap="magma")
        ax.set_yticks(range(len(order)))
        ax.set_yticklabels(order)
        ax.set_xticks(range(48))
        ax.set_xticklabels(labels, rotation=90, fontsize=7)
        ax.set_xlabel("Half-hour (NY)")
        ax.set_title("Jump Rate (≥2σ) by NY Half-hour × Weekday (%)")
        cbar = fig.colorbar(im, ax=ax, orientation="vertical", pad=0.02)
        cbar.set_label("%")
        fig.tight_layout()
        fig.savefig(os.path.join(args.out, "heatmap_ny_halfhour_wday_jump2.png"), dpi=160)
        plt.close(fig)
    except Exception:
        pass

    # Session analysis by UTC windows
    # Asia 00-08, London 07-16, NY 13-22 (UTC)
    df["utc_hour"] = df["dt_utc"].dt.hour
    def in_range(h, start, end):
        return (h >= start) & (h < end)

    df["sess_asia"] = in_range(df["utc_hour"], 0, 8)
    df["sess_lon"] = in_range(df["utc_hour"], 7, 16)
    df["sess_ny"] = in_range(df["utc_hour"], 13, 22)

    session_rows = []
    for name, mask in [("Asia", df["sess_asia"]), ("London", df["sess_lon"]), ("NY", df["sess_ny"])]:
        sub = df[mask]
        session_rows.append(
            {
                "session": name,
                "mean_abs_ret": sub["abs_ret"].mean(),
                "median_abs_ret": sub["abs_ret"].median(),
                "mean_hl_range": sub["hl_range"].mean(),
                "jump2_rate": sub["jump_2"].mean(),
                "jump3_rate": sub["jump_3"].mean(),
                "samples": sub["abs_ret"].count(),
            }
        )
    pd.DataFrame(session_rows).to_csv(os.path.join(args.out, "session_stats_utc.csv"), index=False)

    # Efficiency ratio by hour block, then aggregate by hour-of-day (NY)
    df["ny_hour_block"] = df["dt_ny"].dt.floor("h", ambiguous="NaT", nonexistent="shift_forward")
    er_block = df.groupby("ny_hour_block")["close"].apply(compute_efficiency_ratio).rename("er")
    er_df = er_block.reset_index()
    er_df["ny_hour"] = er_df["ny_hour_block"].dt.hour
    er_hour = er_df.groupby("ny_hour").agg(
        mean_er=("er", "mean"),
        trend_frac=("er", lambda s: (s >= 0.6).mean()),
        chop_frac=("er", lambda s: (s <= 0.2).mean()),
        samples=("er", "count"),
    )
    er_hour.to_csv(os.path.join(args.out, "er_hourly_ny.csv"))

    # Summary report
    with open(os.path.join(args.out, "summary.txt"), "w", encoding="utf-8") as f:
        f.write("BTC Time-of-Day Volatility Analysis\n")
        f.write(f"Rows: {len(df)}\n")
        f.write(f"Global sigma (1m returns): {fmt_bp(sigma)}\n\n")
        f.write("Top 5 volatile NY half-hours (mean abs ret):\n")
        top_half = halfhour.sort_values("mean_abs_ret", ascending=False).head(5)
        for hh, row in top_half.iterrows():
            h = hh // 2
            m = "00" if hh % 2 == 0 else "30"
            f.write(f"  {h:02d}:{m} NY | mean abs {fmt_bp(row['mean_abs_ret'])} | jump2 {fmt_pct(row['jump2_rate'])}\n")
        f.write("\nTop 5 calm NY half-hours (mean abs ret):\n")
        low_half = halfhour.sort_values("mean_abs_ret", ascending=True).head(5)
        for hh, row in low_half.iterrows():
            h = hh // 2
            m = "00" if hh % 2 == 0 else "30"
            f.write(f"  {h:02d}:{m} NY | mean abs {fmt_bp(row['mean_abs_ret'])} | jump2 {fmt_pct(row['jump2_rate'])}\n")
        f.write("\nSession summary (UTC windows):\n")
        for r in session_rows:
            f.write(
                f"  {r['session']}: mean abs {fmt_bp(r['mean_abs_ret'])}, "
                f"jump2 {fmt_pct(r['jump2_rate'])}, samples {int(r['samples'])}\n"
            )

    print(f"Saved outputs to {args.out}/")


if __name__ == "__main__":
    main()
