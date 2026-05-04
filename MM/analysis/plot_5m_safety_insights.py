#!/usr/bin/env python3
"""
Render simple visuals from the 5m safety analysis outputs.

This script is intentionally lightweight and only depends on matplotlib/pandas.
"""

from __future__ import annotations

import argparse
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot visuals from 5m safety analysis outputs")
    parser.add_argument(
        "--input-dir",
        default="data/analysis_output_5m_safety",
        help="Directory containing analyzer CSV outputs",
    )
    parser.add_argument(
        "--output-dir",
        default="data/analysis_output_5m_safety/visuals",
        help="Directory for generated PNGs",
    )
    return parser.parse_args()


def _asset_label(asset: str) -> str:
    return {"ethusdt": "ETH", "xrpusdt": "XRP"}.get(asset, asset.upper())


def plot_calibration_curves(calibration_by_bin: pd.DataFrame, output_path: str) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 6), sharex=True, sharey=True)
    assets = ["ethusdt", "xrpusdt"]
    colors = {"bs": "#d55e00", "blended_kou_proxy": "#0072b2"}
    labels = {"bs": "BS", "blended_kou_proxy": "Blended Kou Proxy"}

    for ax, asset in zip(axes, assets):
        sub = calibration_by_bin[
            (calibration_by_bin["asset"] == asset)
            & (calibration_by_bin["model"].isin(["bs", "blended_kou_proxy"]))
        ].copy()
        ax.plot([0, 1], [0, 1], linestyle="--", color="#777777", linewidth=1)
        for model in ["bs", "blended_kou_proxy"]:
            model_df = sub[sub["model"] == model].sort_values("mean_pred")
            ax.plot(
                model_df["mean_pred"],
                model_df["realized_yes_rate"],
                marker="o",
                linewidth=2,
                markersize=4,
                color=colors[model],
                label=labels[model],
            )
        ax.set_title(f"{_asset_label(asset)} Calibration")
        ax.set_xlabel("Predicted YES Probability")
        ax.set_ylabel("Realized YES Rate")
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.grid(alpha=0.25)
        ax.legend(frameon=False, loc="upper left")

    fig.suptitle("Calibration Curves: BS vs Blended Kou Proxy", fontsize=14)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_reversal_heatmaps(hour_timeleft: pd.DataFrame, strong_veto: pd.DataFrame, output_path: str) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(16, 8), sharey=True)
    assets = ["ethusdt", "xrpusdt"]

    for ax, asset in zip(axes, assets):
        sub = hour_timeleft[
            (hour_timeleft["asset"] == asset) & (hour_timeleft["median_abs_delta_bps"] <= 8.0)
        ].copy()
        grouped = (
            sub.groupby(["hour_utc", "time_left_s"], as_index=False)
            .apply(lambda x: pd.Series({
                "weighted_reversal": np.average(x["reversal_rate"], weights=x["samples"]),
                "samples": x["samples"].sum(),
            }))
            .reset_index(drop=True)
        )
        pivot = grouped.pivot(index="hour_utc", columns="time_left_s", values="weighted_reversal")
        pivot = pivot.reindex(index=range(24), columns=sorted(grouped["time_left_s"].unique()))

        im = ax.imshow(pivot.values, aspect="auto", origin="lower", cmap="magma", vmin=0.0, vmax=0.40)
        ax.set_title(f"{_asset_label(asset)} Near-Strike Reversal Risk")
        ax.set_xlabel("Time Left (s)")
        ax.set_ylabel("UTC Hour")
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([str(int(c)) for c in pivot.columns])
        ax.set_yticks(range(24))
        ax.set_yticklabels([str(i) for i in range(24)])

        veto = strong_veto[strong_veto["asset"] == asset]
        col_map = {int(c): i for i, c in enumerate(pivot.columns)}
        for row in veto.itertuples(index=False):
            if int(row.time_left_s) in col_map:
                ax.scatter(col_map[int(row.time_left_s)], int(row.hour_utc), s=28, facecolors="none", edgecolors="cyan", linewidths=1.2)

    cbar = fig.colorbar(im, ax=axes.ravel().tolist(), shrink=0.86)
    cbar.set_label("Weighted Reversal Rate")
    fig.suptitle("Near-Strike Reversal Risk Heatmap (cyan circles = strong veto zones)", fontsize=14)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def plot_model_scorecard(calibration_summary: pd.DataFrame, output_path: str) -> None:
    metrics = [
        ("ece_abs_gap", "Calibration Gap (lower is better)", "#0072b2"),
        ("brier_mean", "Brier Score (lower is better)", "#cc79a7"),
        ("directional_win_rate", "Directional Win Rate (higher is better)", "#009e73"),
    ]
    models = ["bs", "blended_kou_proxy"]
    assets = ["ethusdt", "xrpusdt"]

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    x = np.arange(len(assets))
    width = 0.34

    for ax, (metric, title, color) in zip(axes, metrics):
        for idx, model in enumerate(models):
            vals = []
            for asset in assets:
                row = calibration_summary[
                    (calibration_summary["asset"] == asset) & (calibration_summary["model"] == model)
                ].iloc[0]
                vals.append(float(row[metric]))
            offset = (-0.5 + idx) * width
            ax.bar(x + offset, vals, width=width, color=color if idx == 0 else "#555555", alpha=0.8, label="BS" if idx == 0 else "Blended Kou")
        ax.set_title(title)
        ax.set_xticks(x)
        ax.set_xticklabels([_asset_label(a) for a in assets])
        ax.grid(axis="y", alpha=0.25)
        if metric == "directional_win_rate":
            ax.set_ylim(0.70, 0.80)
        else:
            ax.set_ylim(0, max(ax.get_ylim()[1], 0.18))

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles[:2], labels[:2], loc="upper center", ncol=2, frameon=False)
    fig.suptitle("Model Scorecard on Historical 1m Proxy", fontsize=14)
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    args = parse_args()
    os.makedirs(args.output_dir, exist_ok=True)

    calibration_by_bin = pd.read_csv(os.path.join(args.input_dir, "probability_calibration_by_bin.csv"))
    hour_timeleft = pd.read_csv(os.path.join(args.input_dir, "hour_of_day_timeleft_regime_priors.csv"))
    strong_veto = pd.read_csv(os.path.join(args.input_dir, "strong_veto_zones.csv"))
    calibration_summary = pd.read_csv(os.path.join(args.input_dir, "probability_calibration_summary.csv"))

    plot_calibration_curves(calibration_by_bin, os.path.join(args.output_dir, "calibration_curves.png"))
    plot_reversal_heatmaps(hour_timeleft, strong_veto, os.path.join(args.output_dir, "reversal_heatmaps.png"))
    plot_model_scorecard(calibration_summary, os.path.join(args.output_dir, "model_scorecard.png"))

    print("Saved visuals:")
    print(f"- {os.path.join(args.output_dir, 'calibration_curves.png')}")
    print(f"- {os.path.join(args.output_dir, 'reversal_heatmaps.png')}")
    print(f"- {os.path.join(args.output_dir, 'model_scorecard.png')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
