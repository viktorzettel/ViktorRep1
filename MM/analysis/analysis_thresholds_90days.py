import os
from datetime import timedelta

import numpy as np
import pandas as pd
from scipy.stats import norm
try:
    import matplotlib.pyplot as plt
    HAS_MPL = True
except Exception:
    HAS_MPL = False

# =========================
# CONFIG
# =========================
FILENAME = "btcusdt_1m_90days_historical.csv"
OUTPUT_DIR = "analysis_output_90days"
DELTA_BINS = np.arange(-3000, 3101, 100)
TIME_LEFT_VALUES = [1, 2, 3, 4, 5]
CONFIDENCE_LEVELS = [0.80, 0.85, 0.90, 0.95]
ATR_PERIOD = 28  # smoother for 15m markets; ~2x candle length
MIN_COUNT_FOR_THRESHOLD = 10

SPECIFIC_DELTAS = [50, 75, 100, 125, 150, 200, 250, 300, 400, 500, 600, 700]


# =========================
# LOAD AND PREPROCESS DATA
# =========================
def load_data(filename: str) -> pd.DataFrame:
    df = pd.read_csv(filename, parse_dates=["open_time_iso"])
    df.set_index("open_time_iso", inplace=True)
    df = df.sort_index()
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    print(f"Loaded {len(df)} 1-min candles from {df.index.min()} to {df.index.max()}")
    return df


# =========================
# COMPUTE ATR AND VOL REGIMES
# =========================
def add_atr_and_regimes(df: pd.DataFrame) -> pd.DataFrame:
    df["high_low"] = df["high"] - df["low"]
    df["high_close"] = np.abs(df["high"] - df["close"].shift())
    df["low_close"] = np.abs(df["low"] - df["close"].shift())
    df["tr"] = df[["high_low", "high_close", "low_close"]].max(axis=1)

    df["atr"] = df["tr"].rolling(window=ATR_PERIOD).mean()
    df["atr_pct"] = df["atr"] / df["close"]
    df = df.dropna(subset=["atr"])

    percentiles = df["atr_pct"].quantile([0.33, 0.67])
    low_threshold = percentiles.iloc[0]
    high_threshold = percentiles.iloc[1]

    def classify_regime(atr_val: float) -> str:
        if atr_val <= low_threshold:
            return "Low"
        if atr_val <= high_threshold:
            return "Med"
        return "High"

    df["vol_regime"] = df["atr_pct"].apply(classify_regime)
    print(
        "ATR calculated (28-period rolling). Regimes: "
        f"Low <= {low_threshold*100:.3f}%, Med {low_threshold*100:.3f}%–{high_threshold*100:.3f}%, "
        f"High > {high_threshold*100:.3f}%"
    )
    return df


# =========================
# SIMULATE 15-MIN CANDLES
# =========================
def get_15min_periods(df: pd.DataFrame) -> pd.DataFrame:
    df_15m = (
        df.resample("15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    df_15m["outcome"] = np.where(df_15m["close"] > df_15m["open"], "YES", "NO")
    print(f"Simulated {len(df_15m)} 15-min periods")
    return df_15m


# =========================
# COLLECT INTRA-CANDLE DATA WITH VOL REGIME
# =========================
def collect_intra_data(df: pd.DataFrame, df_15m: pd.DataFrame) -> pd.DataFrame:
    data = []
    for idx, row in df_15m.iterrows():
        start = idx
        end = start + timedelta(minutes=15)
        intra = df.loc[start : end - timedelta(seconds=1)]
        if len(intra) < 15:
            continue

        strike = row["open"]
        final_outcome = row["outcome"]

        # Use ATR/regime at the same minute snapshot (avoid future leakage)
        for m in range(11, 16):
            snap = intra.iloc[m - 1]
            current_atr = snap["atr"]
            regime = snap["vol_regime"]

            current_price = snap["close"]
            delta = current_price - strike
            time_left = 16 - m
            reversed_flag = (
                (delta > 0 and final_outcome == "NO") or (delta < 0 and final_outcome == "YES")
            )
            data.append(
                {
                    "period_start": start,
                    "atr": current_atr,
                    "vol_regime": regime,
                    "strike": strike,
                    "delta": delta,
                    "time_left": time_left,
                    "reversed": reversed_flag,
                    "final_outcome": final_outcome,
                }
            )
    intra_df = pd.DataFrame(data)
    print(f"Collected {len(intra_df)} intra-candle observations (minutes 11-15)")
    return intra_df


# =========================
# COMPUTE REVERSAL PROBS + WILSON CI (per regime)
# =========================
def compute_reversal_probs(intra_df: pd.DataFrame) -> pd.DataFrame:
    intra_df["delta_bin"] = pd.cut(
        intra_df["delta"], bins=DELTA_BINS, labels=DELTA_BINS[:-1] + 50
    )
    intra_df["delta_bin"] = pd.to_numeric(intra_df["delta_bin"], errors="coerce")

    grouped = intra_df.groupby(["vol_regime", "delta_bin", "time_left"], observed=False)

    probs = grouped["reversed"].agg(["mean", "count", "sum"]).rename(
        columns={"mean": "reversal_prob"}
    )
    probs["hold_prob"] = 1 - probs["reversal_prob"]

    for conf in CONFIDENCE_LEVELS:
        z = norm.ppf(1 - (1 - conf) / 2)
        ci_low = []
        ci_high = []
        for _, row in probs.iterrows():
            n = row["count"]
            if n == 0:
                ci_low.append(np.nan)
                ci_high.append(np.nan)
                continue
            p = row["reversal_prob"]
            denom = 1 + z**2 / n
            center = (p + z**2 / (2 * n)) / denom
            margin = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
            ci_low.append(max(0, center - margin))
            ci_high.append(min(1, center + margin))
        probs[f"ci_low_{int(conf * 100)}"] = ci_low
        probs[f"ci_high_{int(conf * 100)}"] = ci_high

    probs = probs.reset_index()
    print("Computed reversal probabilities per vol regime with Wilson CIs")
    return probs


# =========================
# REGIME-SPECIFIC THRESHOLDS (CI-based)
# =========================
def compute_regime_thresholds(probs: pd.DataFrame) -> dict:
    thresholds = {}
    for regime in probs["vol_regime"].unique():
        for tl in TIME_LEFT_VALUES:
            df_slice = probs[
                (probs["vol_regime"] == regime)
                & (probs["time_left"] == tl)
                & (probs["count"] >= MIN_COUNT_FOR_THRESHOLD)
            ]
            for conf in CONFIDENCE_LEVELS:
                max_rev = 1 - conf
                pos = df_slice[
                    (df_slice["delta_bin"] > 0)
                    & (df_slice[f"ci_high_{int(conf * 100)}"] <= max_rev)
                ]
                yes_thresh = pos["delta_bin"].min() if not pos.empty else np.nan

                neg = df_slice[
                    (df_slice["delta_bin"] < 0)
                    & (df_slice[f"ci_high_{int(conf * 100)}"] <= max_rev)
                ]
                no_thresh = neg["delta_bin"].max() if not neg.empty else np.nan

                key = f"{regime}_{tl}min"
                thresholds[f"{key}_YES_{int(conf * 100)}pct"] = yes_thresh
                thresholds[f"{key}_NO_{int(conf * 100)}pct"] = no_thresh
    return thresholds


# =========================
# SPECIFIC DELTA SUMMARY
# =========================
def specific_delta_summary(intra_df: pd.DataFrame, probs: pd.DataFrame) -> str:
    summary_lines = []
    for regime in ["Low", "Med", "High"]:
        summary_lines.append(f"\n=== {regime} Volatility Regime ===")
        for tl in TIME_LEFT_VALUES:
            summary_lines.append(f"\n--- {tl} Minute(s) Left ---")
            for d in SPECIFIC_DELTAS:
                pos = probs[
                    (probs["vol_regime"] == regime)
                    & (probs["time_left"] == tl)
                    & (np.abs(probs["delta_bin"] - d) < 50)
                ]
                neg = probs[
                    (probs["vol_regime"] == regime)
                    & (probs["time_left"] == tl)
                    & (np.abs(probs["delta_bin"] + d) < 50)
                ]
                pos_count = pos["count"].sum() if not pos.empty else 0
                pos_rev = pos["reversal_prob"].mean() if not pos.empty else np.nan
                neg_count = neg["count"].sum() if not neg.empty else 0
                neg_rev = neg["reversal_prob"].mean() if not neg.empty else np.nan
                summary_lines.append(
                    f"+/-{d}$: +delta count={int(pos_count)}, rev_prob={pos_rev:.3f} | "
                    f"-delta count={int(neg_count)}, rev_prob={neg_rev:.3f}"
                )
    return "\n".join(summary_lines)


# =========================
# PLOTS PER REGIME
# =========================
def plot_reversal_probs(probs: pd.DataFrame, output_dir: str) -> None:
    if not HAS_MPL:
        plot_reversal_probs_svg(probs, output_dir)
        return
    os.makedirs(output_dir, exist_ok=True)
    for regime in probs["vol_regime"].unique():
        for tl in TIME_LEFT_VALUES:
            df_plot = probs[(probs["vol_regime"] == regime) & (probs["time_left"] == tl)].copy()
            df_plot = df_plot.sort_values("delta_bin").dropna(
                subset=["delta_bin", "ci_low_95", "ci_high_95"]
            )
            if df_plot.empty:
                continue

            lower_err = np.maximum(0, df_plot["reversal_prob"] - df_plot["ci_low_95"])
            upper_err = np.maximum(0, df_plot["ci_high_95"] - df_plot["reversal_prob"])

            plt.figure(figsize=(14, 7))
            plt.errorbar(
                df_plot["delta_bin"],
                df_plot["reversal_prob"],
                yerr=[lower_err.to_numpy(), upper_err.to_numpy()],
                fmt="o-",
                capsize=5,
                label="Reversal Prob ± 95% Wilson CI",
            )
            plt.axhline(0.10, color="green", linestyle="--", label="10% Reversal")
            plt.axhline(0.05, color="red", linestyle="--", label="5% Reversal")
            plt.xlabel("Current Price − Strike ($)")
            plt.ylabel("Reversal Probability")
            plt.title(f"{regime} Vol Regime - {tl} Min Left")
            plt.legend()
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig(os.path.join(output_dir, f"reversal_prob_{regime}_{tl}min.png"), dpi=200)
            plt.close()
    print("Saved regime-specific plots")


def plot_reversal_probs_svg(probs: pd.DataFrame, output_dir: str) -> None:
    out_dir = os.path.join(output_dir, "plots_svg")
    os.makedirs(out_dir, exist_ok=True)

    width = 900
    height = 420
    pad_left = 60
    pad_right = 20
    pad_top = 20
    pad_bottom = 40

    def scale_x(x, x_min, x_max):
        if x_max == x_min:
            return pad_left
        return pad_left + (x - x_min) * (width - pad_left - pad_right) / (x_max - x_min)

    def scale_y(y, y_min, y_max):
        if y_max == y_min:
            return height - pad_bottom
        return height - pad_bottom - (y - y_min) * (height - pad_top - pad_bottom) / (y_max - y_min)

    for regime in probs["vol_regime"].unique():
        for tl in TIME_LEFT_VALUES:
            df_plot = probs[(probs["vol_regime"] == regime) & (probs["time_left"] == tl)].copy()
            df_plot = df_plot.sort_values("delta_bin").dropna(
                subset=["delta_bin", "reversal_prob", "ci_low_95", "ci_high_95"]
            )
            if df_plot.empty:
                continue

            x_vals = df_plot["delta_bin"].to_numpy()
            y_vals = df_plot["reversal_prob"].to_numpy()
            y_low = df_plot["ci_low_95"].to_numpy()
            y_high = df_plot["ci_high_95"].to_numpy()

            x_min, x_max = float(np.min(x_vals)), float(np.max(x_vals))
            y_min = 0.0
            y_max = max(0.2, float(np.max(y_high)) + 0.02)

            # Build SVG paths
            def path_from(xa, ya):
                pts = [f"{scale_x(x, x_min, x_max):.2f},{scale_y(y, y_min, y_max):.2f}" for x, y in zip(xa, ya)]
                return "M " + " L ".join(pts)

            path_prob = path_from(x_vals, y_vals)
            path_low = path_from(x_vals, y_low)
            path_high = path_from(x_vals, y_high)

            # Horizontal reference lines
            y05 = scale_y(0.05, y_min, y_max)
            y10 = scale_y(0.10, y_min, y_max)

            svg = [
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
                f'<rect width="{width}" height="{height}" fill="white" />',
                # Axes
                f'<line x1="{pad_left}" y1="{pad_top}" x2="{pad_left}" y2="{height - pad_bottom}" stroke="#333" />',
                f'<line x1="{pad_left}" y1="{height - pad_bottom}" x2="{width - pad_right}" y2="{height - pad_bottom}" stroke="#333" />',
                # Reference lines
                f'<line x1="{pad_left}" y1="{y05:.2f}" x2="{width - pad_right}" y2="{y05:.2f}" stroke="#d00" stroke-dasharray="4,4" />',
                f'<line x1="{pad_left}" y1="{y10:.2f}" x2="{width - pad_right}" y2="{y10:.2f}" stroke="#0a0" stroke-dasharray="4,4" />',
                # CI bounds
                f'<path d="{path_low}" fill="none" stroke="#999" stroke-width="1" />',
                f'<path d="{path_high}" fill="none" stroke="#999" stroke-width="1" />',
                # Main line
                f'<path d="{path_prob}" fill="none" stroke="#1f77b4" stroke-width="2" />',
                # Labels
                f'<text x="{pad_left}" y="{pad_top - 4}" font-size="12" fill="#333">{regime} Regime - {tl} Min Left</text>',
                f'<text x="{width/2 - 60}" y="{height - 6}" font-size="12" fill="#333">Delta (price - strike)</text>',
                f'<text x="10" y="{height/2}" font-size="12" fill="#333" transform="rotate(-90 10,{height/2})">Reversal Prob</text>',
                "</svg>",
            ]

            out_path = os.path.join(out_dir, f"reversal_prob_{regime}_{tl}min.svg")
            with open(out_path, "w") as f:
                f.write("\n".join(svg))

    print(f"Saved SVG plots to {out_dir}")


# =========================
# REPORT
# =========================
def generate_report(
    probs: pd.DataFrame,
    thresholds: dict,
    specific_summary: str,
    output_dir: str,
    data_start,
    data_end,
) -> None:
    os.makedirs(output_dir, exist_ok=True)
    report_file = os.path.join(output_dir, "analysis_report.txt")
    with open(report_file, "w") as f:
        f.write("=== Polymarket 15-Min BTC Strategy - 90-Day Analysis with Vol Regimes ===\n\n")
        f.write(f"Data period: {data_start} to {data_end}\n\n")
        f.write("Volatility Regime Calculation:\n")
        f.write("- True Range per 1-min bar\n")
        f.write("- 28-period rolling ATR (standard)\n")
        f.write("- Regimes: Low/Med/High based on 33rd/67th percentiles of all ATR values\n\n")

        f.write("CI-Based Safe Thresholds (min_count=10):\n")
        f.write("YES: delta ≥ threshold (upper CI reversal ≤5%/10%)\n")
        f.write("NO: delta ≤ threshold\n")
        for k, v in sorted(thresholds.items()):
            if np.isnan(v):
                v = "N/A"
            else:
                v = f"{v:.0f}$"
            f.write(f"{k}: {v}\n")
        f.write("\n")

        f.write("Specific Delta Summary (counts and avg reversal prob):\n")
        f.write(specific_summary)

    probs.to_csv(os.path.join(output_dir, "full_regime_reversal_probs.csv"), index=False)
    print(f"Report saved to {report_file}")


# =========================
# MAIN
# =========================
def main() -> None:
    df = load_data(FILENAME)
    df = add_atr_and_regimes(df)
    data_start = df.index.min()
    data_end = df.index.max()

    df_15m = get_15min_periods(df)
    intra_df = collect_intra_data(df, df_15m)
    if intra_df.empty:
        print("No data collected")
        return

    probs = compute_reversal_probs(intra_df)
    thresholds = compute_regime_thresholds(probs)
    specific_summary = specific_delta_summary(intra_df, probs)

    plot_reversal_probs(probs, OUTPUT_DIR)
    generate_report(probs, thresholds, specific_summary, OUTPUT_DIR, data_start, data_end)

    print("\nAnalysis complete! Outputs:")
    print(f"  - Plots in {OUTPUT_DIR}/")
    print("  - full_regime_reversal_probs.csv")
    print("  - analysis_report.txt (includes specific delta summary)")


if __name__ == "__main__":
    main()
