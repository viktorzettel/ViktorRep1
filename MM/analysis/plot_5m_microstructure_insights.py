#!/usr/bin/env python3
"""
Render visuals from the 5m microstructure analyzer outputs.
"""

from __future__ import annotations

import argparse
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot ETH/XRP 5m microstructure insights")
    parser.add_argument(
        "--input-dir",
        default="data/analysis_output_5m_microstructure",
        help="Directory containing analyzer outputs",
    )
    parser.add_argument(
        "--output-dir",
        default="data/analysis_output_5m_microstructure/visuals",
        help="Directory for generated visuals",
    )
    return parser.parse_args()


def _asset_label(asset: str) -> str:
    return {"ethusdt": "ETH", "xrpusdt": "XRP"}.get(asset, asset.upper())


def _pivot(df: pd.DataFrame, asset: str, value_col: str) -> pd.DataFrame:
    sub = df[df["asset"] == asset].copy()
    pivot = sub.pivot(index="hour_utc", columns="time_left_s", values=value_col)
    pivot = pivot.reindex(index=range(24))
    return pivot.reindex(sorted(pivot.columns), axis=1)


def plot_hour_timeleft_heatmap(
    df: pd.DataFrame,
    veto_df: pd.DataFrame,
    *,
    value_col: str,
    title: str,
    cbar_label: str,
    cmap: str,
    output_path: str,
    vmin: float | None = None,
    vmax: float | None = None,
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(16, 8), sharey=True)
    assets = ["ethusdt", "xrpusdt"]

    for ax, asset in zip(axes, assets):
        pivot = _pivot(df, asset, value_col)
        im = ax.imshow(pivot.values, aspect="auto", origin="lower", cmap=cmap, vmin=vmin, vmax=vmax)
        ax.set_title(f"{_asset_label(asset)}")
        ax.set_xlabel("Time Left (s)")
        ax.set_ylabel("UTC Hour")
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([str(int(c)) for c in pivot.columns])
        ax.set_yticks(range(24))
        ax.set_yticklabels([str(i) for i in range(24)])

        asset_veto = veto_df[veto_df["asset"] == asset]
        col_map = {int(col): idx for idx, col in enumerate(pivot.columns)}
        for row in asset_veto.itertuples(index=False):
            if int(row.time_left_s) in col_map:
                ax.scatter(
                    col_map[int(row.time_left_s)],
                    int(row.hour_utc),
                    s=26,
                    facecolors="none",
                    edgecolors="cyan",
                    linewidths=1.2,
                )

    cbar = fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.86)
    cbar.set_label(cbar_label)
    fig.suptitle(title, fontsize=14)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_delta_timeleft_heatmap(df: pd.DataFrame, output_path: str) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(16, 7), sharey=True)
    assets = ["ethusdt", "xrpusdt"]
    for ax, asset in zip(axes, assets):
        sub = df[df["asset"] == asset].copy()
        pivot = sub.pivot(index="delta_bin_bps", columns="time_left_s", values="reversal_rate")
        pivot = pivot.sort_index().reindex(sorted(pivot.columns), axis=1)
        im = ax.imshow(pivot.values, aspect="auto", origin="lower", cmap="magma", vmin=0.0, vmax=0.5)
        ax.set_title(f"{_asset_label(asset)} Reversal Risk by Delta and Time Left")
        ax.set_xlabel("Time Left (s)")
        ax.set_ylabel("Delta to Strike (bps)")
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([str(int(c)) for c in pivot.columns])
        y_ticks = np.linspace(0, max(len(pivot.index) - 1, 0), num=min(11, max(len(pivot.index), 1)), dtype=int)
        y_ticks = np.unique(y_ticks)
        ax.set_yticks(y_ticks)
        ax.set_yticklabels([f"{pivot.index[i]:.0f}" for i in y_ticks])

    cbar = fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.86)
    cbar.set_label("Reversal Rate")
    fig.suptitle("Reversal Risk by Distance to Strike and Time Left", fontsize=14)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_timeleft_summary(df: pd.DataFrame, output_path: str) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(14, 10), sharex=True)
    assets = ["ethusdt", "xrpusdt"]
    metrics = [
        ("reversal_rate", "Near-Strike Reversal Rate"),
        ("rv_30s_bp_median", "Median 30s Realized Vol (bp)"),
        ("jump_any_30s_rate", "Jump-In-Last-30s Rate"),
        ("flip_rate_30s_mean", "30s Flip Rate"),
    ]
    colors = {"ethusdt": "#0072b2", "xrpusdt": "#d55e00"}

    for ax, (metric, title) in zip(axes.ravel(), metrics):
        for asset in assets:
            sub = df[df["asset"] == asset].sort_values("time_left_s")
            ax.plot(sub["time_left_s"], sub[metric], marker="o", linewidth=2, markersize=4, label=_asset_label(asset), color=colors[asset])
        ax.set_title(title)
        ax.grid(alpha=0.25)
        ax.invert_xaxis()
        ax.set_xlabel("Time Left (s)")
    axes[0, 0].legend(frameon=False)
    fig.suptitle("Near-Strike Time-Left Summary", fontsize=14)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_late_window_margin_heatmap(df: pd.DataFrame, danger_df: pd.DataFrame, output_path: str) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(16, 7), sharey=True)
    assets = ["ethusdt", "xrpusdt"]
    for ax, asset in zip(axes, assets):
        sub = df[(df["asset"] == asset) & (df["current_side"] == "yes")].copy()
        pivot = sub.pivot(index="margin_z_bin", columns="time_left_s", values="adverse_cross_rate")
        pivot = pivot.sort_index().reindex(sorted(pivot.columns), axis=1)
        im = ax.imshow(pivot.values, aspect="auto", origin="lower", cmap="magma", vmin=0.0, vmax=0.6)
        ax.set_title(f"{_asset_label(asset)} Late-Window Adverse-Cross Rate")
        ax.set_xlabel("Time Left (s)")
        ax.set_ylabel("Margin z-bin")
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([str(int(c)) for c in pivot.columns])
        y_ticks = np.arange(len(pivot.index))
        ax.set_yticks(y_ticks)
        ax.set_yticklabels([f"{pivot.index[i]:.1f}" for i in y_ticks])

        asset_danger = danger_df[(danger_df["asset"] == asset) & (danger_df["current_side"] == "yes")]
        col_map = {int(col): idx for idx, col in enumerate(pivot.columns)}
        row_map = {float(row): idx for idx, row in enumerate(pivot.index)}
        for row in asset_danger.itertuples(index=False):
            mz = round(float(row.margin_z_bin), 4)
            if int(row.time_left_s) in col_map and mz in row_map:
                ax.scatter(
                    col_map[int(row.time_left_s)],
                    row_map[mz],
                    s=30,
                    facecolors="none",
                    edgecolors="cyan",
                    linewidths=1.2,
                )

    cbar = fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.86)
    cbar.set_label("Adverse Cross Rate")
    fig.suptitle("Late-Window Policy Heatmap (YES side, cyan circles = danger cells)", fontsize=14)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    heatmap = pd.read_csv(os.path.join(args.input_dir, "near_strike_heatmap.csv"))
    delta_timeleft = pd.read_csv(os.path.join(args.input_dir, "delta_timeleft_reversal.csv"))
    timeleft_summary = pd.read_csv(os.path.join(args.input_dir, "near_strike_timeleft_summary.csv"))
    veto_df = pd.read_csv(os.path.join(args.input_dir, "micro_veto_zones.csv"))
    late_window_policy = pd.read_csv(os.path.join(args.input_dir, "late_window_policy.csv"))
    late_window_danger = pd.read_csv(os.path.join(args.input_dir, "late_window_danger_zones.csv"))

    plot_hour_timeleft_heatmap(
        heatmap,
        veto_df,
        value_col="reversal_rate",
        title="Near-Strike Reversal Risk (cyan circles = micro veto zones)",
        cbar_label="Reversal Rate",
        cmap="magma",
        output_path=os.path.join(args.output_dir, "micro_reversal_heatmap.png"),
        vmin=0.0,
        vmax=0.45,
    )
    plot_hour_timeleft_heatmap(
        heatmap,
        veto_df,
        value_col="rv_30s_bp_median",
        title="Near-Strike 30s Realized Volatility",
        cbar_label="Median 30s Realized Vol (bp)",
        cmap="viridis",
        output_path=os.path.join(args.output_dir, "micro_volatility_heatmap.png"),
    )
    plot_hour_timeleft_heatmap(
        heatmap,
        veto_df,
        value_col="jump_any_30s_rate",
        title="Near-Strike Jump-In-Last-30s Rate",
        cbar_label="Jump Any Rate",
        cmap="plasma",
        output_path=os.path.join(args.output_dir, "micro_jump_heatmap.png"),
        vmin=0.0,
        vmax=0.35,
    )
    plot_hour_timeleft_heatmap(
        heatmap,
        veto_df,
        value_col="flip_rate_30s_mean",
        title="Near-Strike Chop / Flip Rate",
        cbar_label="30s Flip Rate",
        cmap="cividis",
        output_path=os.path.join(args.output_dir, "micro_chop_heatmap.png"),
        vmin=0.35,
        vmax=0.65,
    )
    plot_delta_timeleft_heatmap(delta_timeleft, os.path.join(args.output_dir, "micro_delta_timeleft_heatmap.png"))
    plot_timeleft_summary(timeleft_summary, os.path.join(args.output_dir, "micro_timeleft_summary.png"))
    plot_late_window_margin_heatmap(
        late_window_policy,
        late_window_danger,
        os.path.join(args.output_dir, "late_window_margin_heatmap.png"),
    )

    print("Saved visuals:")
    for name in [
        "micro_reversal_heatmap.png",
        "micro_volatility_heatmap.png",
        "micro_jump_heatmap.png",
        "micro_chop_heatmap.png",
        "micro_delta_timeleft_heatmap.png",
        "micro_timeleft_summary.png",
        "late_window_margin_heatmap.png",
    ]:
        print(f"- {os.path.join(args.output_dir, name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
