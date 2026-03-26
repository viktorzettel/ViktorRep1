import json
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm

# =========================
# CONFIG
# =========================
FILENAME = "btcusdt_1m_90days_historical.csv"
OUTPUT_DIR = "analysis_output_model"

SNAPSHOT_PRICE = "open"  # "open" aligns with time_left at minute start; use "close" for minute-end
LAST_N_MINUTES = 5  # Use last N minutes of each 15-min candle

ATR_PERIOD = 28  # smoother for 15m markets; ~2x candle length
RET_VOL_WINDOW = 60
VOL_Z_WINDOW = 60

TRAIN_RATIO = 0.8  # time-based split
CALIB_BINS = 20
L2_REG = 1.0

MIN_COUNT_FOR_STATS = 50
CONF_LEVELS = [0.90, 0.95]
THRESHOLDS_EVAL = [0.05, 0.10, 0.15, 0.20]


# =========================
# DATA HELPERS
# =========================
def load_data(filename: str) -> pd.DataFrame:
    df = pd.read_csv(filename, parse_dates=["open_time_iso"])
    df["open_time_iso"] = pd.to_datetime(df["open_time_iso"], utc=True)
    df = df.set_index("open_time_iso").sort_index()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    print(f"Loaded {len(df)} rows from {df.index.min()} to {df.index.max()}")
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    # Returns
    df["log_close"] = np.log(df["close"])
    df["log_ret"] = df["log_close"].diff()
    df["ret_1m"] = df["log_ret"]
    df["ret_3m"] = df["log_ret"].rolling(3).sum()
    df["ret_5m"] = df["log_ret"].rolling(5).sum()
    df["ret_std_60"] = df["log_ret"].rolling(RET_VOL_WINDOW).std()

    # True Range / ATR
    prev_close = df["close"].shift()
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()
    df["atr_pct"] = df["atr"] / df["close"]

    # Volume features
    df["log_vol"] = np.log(df["volume"].replace(0, np.nan))
    vol_mean = df["log_vol"].rolling(VOL_Z_WINDOW).mean()
    vol_std = df["log_vol"].rolling(VOL_Z_WINDOW).std()
    df["vol_z"] = (df["log_vol"] - vol_mean) / vol_std

    # 15-minute grouping
    df["period_start"] = df.index.floor("15min")
    df["strike"] = df.groupby("period_start")["open"].transform("first")
    df["minutes_in"] = df.groupby("period_start").cumcount()  # 0..14
    df["time_left"] = 15 - df["minutes_in"]  # 15..1 (minute start basis)

    # Snapshot price aligned to time_left definition
    if SNAPSHOT_PRICE not in ("open", "close"):
        raise ValueError("SNAPSHOT_PRICE must be 'open' or 'close'")
    df["price_snap"] = df[SNAPSHOT_PRICE]

    # Delta and normalized deltas
    df["delta"] = df["price_snap"] - df["strike"]
    df["delta_norm_atr"] = df["delta"] / df["atr"]
    df["delta_norm_std"] = df["delta"] / (df["ret_std_60"] * df["close"])

    # Intra-candle range and VWAP since open
    df["cum_high"] = df.groupby("period_start")["high"].cummax()
    df["cum_low"] = df.groupby("period_start")["low"].cummin()
    df["range_since_open"] = df["cum_high"] - df["cum_low"]
    df["range_norm_atr"] = df["range_since_open"] / df["atr"]

    df["cum_vol"] = df.groupby("period_start")["volume"].cumsum()
    df["cum_vwap"] = (df["close"] * df["volume"]).groupby(df["period_start"]).cumsum() / df["cum_vol"]
    df["vwap_delta"] = df["price_snap"] - df["cum_vwap"]
    df["vwap_delta_norm_atr"] = df["vwap_delta"] / df["atr"]

    # Final outcome for the 15-min candle
    df["final_close"] = df.groupby("period_start")["close"].transform("last")
    df["final_outcome_yes"] = (df["final_close"] > df["strike"]).astype(int)

    # Reversal label
    df["reversed"] = (
        ((df["delta"] > 0) & (df["final_outcome_yes"] == 0))
        | ((df["delta"] < 0) & (df["final_outcome_yes"] == 1))
    ).astype(int)

    return df


def split_train_test(df: pd.DataFrame, train_ratio: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    split_idx = int(len(df) * train_ratio)
    split_time = df.index[split_idx]
    train_df = df[df.index <= split_time].copy()
    test_df = df[df.index > split_time].copy()
    print(f"Train rows: {len(train_df)} | Test rows: {len(test_df)}")
    return train_df, test_df


def assign_regimes(train_df: pd.DataFrame, full_df: pd.DataFrame) -> pd.DataFrame:
    # Use ATR normalized by price for regime stability across price levels
    atr_q = train_df["atr_pct"].quantile([0.33, 0.67]).values
    vol_q = train_df["vol_z"].quantile([0.33, 0.67]).values

    def vol_regime(x, low, high):
        if x <= low:
            return "Low"
        if x <= high:
            return "Med"
        return "High"

    full_df["vol_regime"] = full_df["atr_pct"].apply(lambda x: vol_regime(x, atr_q[0], atr_q[1]))
    full_df["volu_regime"] = full_df["vol_z"].apply(lambda x: vol_regime(x, vol_q[0], vol_q[1]))
    return full_df, atr_q, vol_q


def filter_last_minutes(df: pd.DataFrame, last_n_minutes: int) -> pd.DataFrame:
    df = df[(df["time_left"] <= last_n_minutes) & (df["time_left"] >= 1)].copy()
    return df


# =========================
# MODELING
# =========================
@dataclass
class Standardizer:
    mean_: np.ndarray
    std_: np.ndarray

    def transform(self, X: np.ndarray) -> np.ndarray:
        std = np.where(self.std_ == 0, 1.0, self.std_)
        return (X - self.mean_) / std


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def fit_logistic(X: np.ndarray, y: np.ndarray, l2_reg: float) -> np.ndarray:
    # Add intercept
    X_ = np.hstack([np.ones((X.shape[0], 1)), X])

    def nll(w):
        z = X_ @ w
        p = sigmoid(z)
        eps = 1e-9
        ll = y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)
        reg = 0.5 * l2_reg * np.sum(w[1:] ** 2)
        return -np.sum(ll) + reg

    def grad(w):
        z = X_ @ w
        p = sigmoid(z)
        g = X_.T @ (p - y)
        g[1:] += l2_reg * w[1:]
        return g

    w0 = np.zeros(X_.shape[1])
    res = minimize(nll, w0, jac=grad, method="L-BFGS-B")
    if not res.success:
        raise RuntimeError(f"Optimization failed: {res.message}")
    return res.x  # includes intercept


def fit_calibration(preds: np.ndarray, y: np.ndarray, n_bins: int):
    bins = np.linspace(0, 1, n_bins + 1)
    bin_ids = np.digitize(preds, bins[1:-1], right=True)
    bin_pred = []
    bin_true = []
    for i in range(n_bins):
        mask = bin_ids == i
        if mask.sum() == 0:
            continue
        bin_pred.append(preds[mask].mean())
        bin_true.append(y[mask].mean())
    if len(bin_pred) < 2:
        return np.array([0.0, 1.0]), np.array([0.0, 1.0])
    order = np.argsort(bin_pred)
    x = np.array(bin_pred)[order]
    y = np.array(bin_true)[order]
    # Enforce monotonicity
    y = np.maximum.accumulate(y)
    return x, y


def apply_calibration(preds: np.ndarray, calib_x: np.ndarray, calib_y: np.ndarray) -> np.ndarray:
    return np.interp(preds, calib_x, calib_y, left=calib_y[0], right=calib_y[-1])


def wilson_upper_bound(k: int, n: int, conf: float) -> float:
    if n == 0:
        return np.nan
    z = norm.ppf(1 - (1 - conf) / 2)
    p = k / n
    denom = 1 + z ** 2 / n
    center = (p + z ** 2 / (2 * n)) / denom
    margin = z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return min(1.0, center + margin)


def evaluate_thresholds(df: pd.DataFrame, prob_col: str, targets=(0.05, 0.10, 0.15, 0.20)):
    rows = []
    for t in targets:
        mask = (df[prob_col] <= t) & (df["delta"] != 0)
        n = mask.sum()
        k = df.loc[mask, "reversed"].sum() if n > 0 else 0
        rate = k / n if n > 0 else np.nan
        rows.append({"threshold": t, "trades": int(n), "reversal_rate": rate})
    return pd.DataFrame(rows)


# =========================
# MAIN
# =========================
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    df = load_data(FILENAME)
    df = add_features(df)

    # Basic filtering for usable rows
    df = df.replace([np.inf, -np.inf], np.nan).dropna()

    # Train/test split by time
    train_df, test_df = split_train_test(df, TRAIN_RATIO)

    # Assign regimes using train quantiles only
    full_df = pd.concat([train_df, test_df]).sort_index()
    full_df, atr_q, vol_q = assign_regimes(train_df, full_df)

    # Use last N minutes
    full_df = filter_last_minutes(full_df, LAST_N_MINUTES)

    # Keep only rows with meaningful delta
    full_df = full_df[full_df["delta"] != 0].copy()

    # Re-split after filtering
    split_time = train_df.index.max()
    train_df = full_df[full_df.index <= split_time].copy()
    test_df = full_df[full_df.index > split_time].copy()

    print(f"Filtered train rows: {len(train_df)} | Filtered test rows: {len(test_df)}")

    # Feature matrix
    base_features = [
        "delta_norm_atr",
        "delta_norm_std",
        "ret_1m",
        "ret_3m",
        "ret_5m",
        "range_norm_atr",
        "vwap_delta_norm_atr",
        "vol_z",
        "atr_pct",
        "time_left",
    ]

    # Preserve regime labels for reporting, then one-hot encode
    full_df["vol_regime_label"] = full_df["vol_regime"]
    full_df["volu_regime_label"] = full_df["volu_regime"]
    full_df = pd.get_dummies(full_df, columns=["vol_regime", "volu_regime"], drop_first=True)
    train_df = full_df[full_df.index <= split_time].copy()
    test_df = full_df[full_df.index > split_time].copy()

    regime_features = [
        c
        for c in full_df.columns
        if (c.startswith("vol_regime_") or c.startswith("volu_regime_")) and not c.endswith("_label")
    ]
    feature_cols = base_features + regime_features

    # Ensure a pure numeric matrix (pandas may otherwise return object dtype due to bool cols)
    X_train = train_df[feature_cols].astype(float).to_numpy()
    y_train = train_df["reversed"].to_numpy()
    X_test = test_df[feature_cols].astype(float).to_numpy()
    y_test = test_df["reversed"].to_numpy()

    # Standardize
    mean_ = X_train.mean(axis=0)
    std_ = X_train.std(axis=0)
    scaler = Standardizer(mean_, std_)
    X_train_s = scaler.transform(X_train)
    X_test_s = scaler.transform(X_test)

    # Fit logistic regression
    w = fit_logistic(X_train_s, y_train, L2_REG)
    intercept = w[0]
    weights = w[1:]

    # Predictions
    train_pred = sigmoid(intercept + X_train_s @ weights)
    test_pred = sigmoid(intercept + X_test_s @ weights)

    # Calibration on train
    calib_x, calib_y = fit_calibration(train_pred, y_train, CALIB_BINS)
    train_pred_cal = apply_calibration(train_pred, calib_x, calib_y)
    test_pred_cal = apply_calibration(test_pred, calib_x, calib_y)

    train_df["pred_reversal"] = train_pred
    train_df["pred_reversal_cal"] = train_pred_cal
    test_df["pred_reversal"] = test_pred
    test_df["pred_reversal_cal"] = test_pred_cal

    # Evaluate thresholds
    train_eval = evaluate_thresholds(train_df, "pred_reversal_cal", THRESHOLDS_EVAL)
    test_eval = evaluate_thresholds(test_df, "pred_reversal_cal", THRESHOLDS_EVAL)

    # Per regime/time_left summary (test)
    summary_rows = []
    for (reg_label, tl), sub in test_df.groupby(["vol_regime_label", "time_left"], observed=False):
        for thr in THRESHOLDS_EVAL:
            mask = (sub["pred_reversal_cal"] <= thr) & (sub["delta"] != 0)
            n = mask.sum()
            k = sub.loc[mask, "reversed"].sum() if n > 0 else 0
            rate = k / n if n > 0 else np.nan
            summary_rows.append(
                {
                    "regime": reg_label,
                    "time_left": int(tl),
                    "threshold": thr,
                    "trades": int(n),
                    "reversal_rate": rate,
                }
            )
    summary_df = pd.DataFrame(summary_rows)

    # Save outputs
    with open(os.path.join(OUTPUT_DIR, "model.json"), "w") as f:
        json.dump(
            {
                "features": feature_cols,
                "mean": mean_.tolist(),
                "std": std_.tolist(),
                "intercept": float(intercept),
                "weights": weights.tolist(),
                "calibration": {"x": calib_x.tolist(), "y": calib_y.tolist()},
                "thresholds": {"p05": 0.05, "p10": 0.10, "p15": 0.15, "p20": 0.20},
                "atr_quantiles": atr_q.tolist(),
                "vol_z_quantiles": vol_q.tolist(),
                "snapshot_price": SNAPSHOT_PRICE,
                "last_n_minutes": LAST_N_MINUTES,
            },
            f,
            indent=2,
        )

    train_eval.to_csv(os.path.join(OUTPUT_DIR, "train_threshold_eval.csv"), index=False)
    test_eval.to_csv(os.path.join(OUTPUT_DIR, "test_threshold_eval.csv"), index=False)
    summary_df.to_csv(os.path.join(OUTPUT_DIR, "test_summary_by_regime_time.csv"), index=False)

    # Simple report
    report_path = os.path.join(OUTPUT_DIR, "report.txt")
    with open(report_path, "w") as f:
        f.write("=== Reversal Model Report ===\n\n")
        f.write(f"Snapshot price: {SNAPSHOT_PRICE}\n")
        f.write(f"Last minutes used: {LAST_N_MINUTES}\n")
        f.write(f"ATR quantiles (33%, 67%): {atr_q}\n")
        f.write(f"Vol_z quantiles (33%, 67%): {vol_q}\n\n")
        f.write(f"Thresholds evaluated: {THRESHOLDS_EVAL}\n\n")
        f.write("Train threshold eval (calibrated):\n")
        f.write(train_eval.to_string(index=False))
        f.write("\n\nTest threshold eval (calibrated):\n")
        f.write(test_eval.to_string(index=False))
        f.write("\n\nTest summary by regime/time_left:\n")
        f.write(summary_df.to_string(index=False))
        f.write("\n")

    print("Saved outputs:")
    print(f"- {os.path.join(OUTPUT_DIR, 'model.json')}")
    print(f"- {os.path.join(OUTPUT_DIR, 'train_threshold_eval.csv')}")
    print(f"- {os.path.join(OUTPUT_DIR, 'test_threshold_eval.csv')}")
    print(f"- {os.path.join(OUTPUT_DIR, 'test_summary_by_regime_time.csv')}")
    print(f"- {report_path}")


if __name__ == "__main__":
    main()
