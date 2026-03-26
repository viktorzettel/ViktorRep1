#!/usr/bin/env python3
"""
Compare BTC/ETH/SOL/XRP for short-term binary predictability.

- Downloads 1m OHLC data (Binance) for last N days
- Computes volatility stability, jump rates, trend persistence
- Computes near-expiry stickiness for 15m markets (t-3, t-2)
- Outputs ranking + CSVs
"""

import argparse
import math
import os
import time
from datetime import datetime, timezone, timedelta

import httpx
import numpy as np
import pandas as pd
from statistics import NormalDist

BINANCE_URL = "https://api.binance.com/api/v3/klines"


def fetch_binance_1m(symbol: str, start_ms: int, end_ms: int, limit: int = 1000) -> list:
    rows = []
    params = {
        "symbol": symbol,
        "interval": "1m",
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": limit,
    }
    with httpx.Client(timeout=10.0) as client:
        while start_ms < end_ms:
            params["startTime"] = start_ms
            resp = client.get(BINANCE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            rows.extend(data)
            start_ms = data[-1][0] + 60_000
            if len(data) < limit:
                break
            time.sleep(0.05)
    return rows


def download_or_load(symbol: str, out_dir: str, days: int, refresh: bool) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{symbol}_1m_{days}d.csv")
    if os.path.exists(path) and not refresh:
        return path

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000
    rows = fetch_binance_1m(symbol, start_ms, end_ms)

    if not rows:
        raise RuntimeError(f"No data returned for {symbol}")

    df = pd.DataFrame(
        rows,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_base",
            "taker_quote",
            "ignore",
        ],
    )
    df = df[["open_time", "open", "high", "low", "close", "volume"]].copy()
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df.to_csv(path, index=False)
    return path


def compute_er(series: pd.Series) -> float:
    if len(series) < 2:
        return np.nan
    diff = series.diff().dropna()
    net = series.iloc[-1] - series.iloc[0]
    denom = diff.abs().sum()
    if denom == 0:
        return 0.0
    return abs(net) / denom


def make_bucket_df(df: pd.DataFrame) -> pd.DataFrame:
    # Expect df with dt_utc, open, close
    df = df.copy()
    df["bucket"] = df["dt_utc"].dt.floor("15min")
    df["min_in_bucket"] = ((df["dt_utc"] - df["bucket"]).dt.total_seconds() // 60).astype(int)

    # strike at minute 0 from open
    strike = df[df["min_in_bucket"] == 0].set_index("bucket")["open"].rename("strike")
    close_map = df.pivot_table(index="bucket", columns="min_in_bucket", values="close", aggfunc="last")

    data = pd.DataFrame(index=close_map.index)
    data["strike"] = strike
    data["p12"] = close_map.get(12)
    data["p13"] = close_map.get(13)
    data["p14"] = close_map.get(14)
    data = data.dropna()

    if data.empty:
        return pd.DataFrame()

    data["t3_diff"] = data["p12"] - data["strike"]
    data["t2_diff"] = data["p13"] - data["strike"]
    data["close_diff"] = data["p14"] - data["strike"]
    data["delta_t3_bp"] = (data["t3_diff"] / data["strike"]) * 10000
    data["delta_t2_bp"] = (data["t2_diff"] / data["strike"]) * 10000

    def stay(diff_col):
        side = np.sign(data[diff_col])
        close_side = np.sign(data["close_diff"])
        valid = (side != 0) & (close_side != 0)
        stay = (side == close_side) & valid
        return stay, side, valid

    stay_t3, side_t3, valid_t3 = stay("t3_diff")
    stay_t2, side_t2, valid_t2 = stay("t2_diff")

    data["stay_t3"] = stay_t3
    data["stay_t2"] = stay_t2
    data["side_t3"] = side_t3
    data["side_t2"] = side_t2
    data["valid_t3"] = valid_t3
    data["valid_t2"] = valid_t2
    return data


def stickiness_summary(bucket_df: pd.DataFrame) -> dict:
    out = {}
    if bucket_df.empty:
        return {
            "stick_above_t3": np.nan,
            "stick_below_t3": np.nan,
            "stick_above_t2": np.nan,
            "stick_below_t2": np.nan,
            "n_above_t3": 0,
            "n_below_t3": 0,
            "n_above_t2": 0,
            "n_below_t2": 0,
        }

    def calc(prefix: str, side_col: str, stay_col: str, valid_col: str):
        side = bucket_df[side_col]
        stay = bucket_df[stay_col]
        valid = bucket_df[valid_col]
        above = (side > 0) & valid
        below = (side < 0) & valid
        out[f"stick_above_{prefix}"] = stay[above].mean() if above.any() else np.nan
        out[f"stick_below_{prefix}"] = stay[below].mean() if below.any() else np.nan
        out[f"n_above_{prefix}"] = int(above.sum())
        out[f"n_below_{prefix}"] = int(below.sum())

    calc("t3", "side_t3", "stay_t3", "valid_t3")
    calc("t2", "side_t2", "stay_t2", "valid_t2")
    return out


def stickiness_bins(bucket_df: pd.DataFrame, bins_bp: list[float]) -> pd.DataFrame:
    if bucket_df.empty:
        return pd.DataFrame()

    edges = bins_bp + [float("inf")]
    rows = []
    for i in range(len(edges) - 1):
        lo = edges[i]
        hi = edges[i + 1]
        mask_t3 = (bucket_df["valid_t3"]) & (bucket_df["delta_t3_bp"].abs() >= lo) & (bucket_df["delta_t3_bp"].abs() < hi)
        mask_t2 = (bucket_df["valid_t2"]) & (bucket_df["delta_t2_bp"].abs() >= lo) & (bucket_df["delta_t2_bp"].abs() < hi)
        rows.append(
            {
                "bin_low_bp": lo,
                "bin_high_bp": hi,
                "n_t3": int(mask_t3.sum()),
                "stick_t3": bucket_df.loc[mask_t3, "stay_t3"].mean() if mask_t3.any() else np.nan,
                "n_t2": int(mask_t2.sum()),
                "stick_t2": bucket_df.loc[mask_t2, "stay_t2"].mean() if mask_t2.any() else np.nan,
            }
        )
    return pd.DataFrame(rows)


def analyze_asset(symbol: str, csv_path: str) -> tuple[dict, pd.DataFrame]:
    df = pd.read_csv(csv_path)
    df["dt_utc"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.sort_values("dt_utc")
    df["ret"] = np.log(df["close"]).diff()
    df["abs_ret"] = df["ret"].abs()

    returns = df["ret"].dropna()
    abs_returns = df["abs_ret"].dropna()

    global_sigma = returns.std()
    jump2_rate = (abs_returns >= 2 * global_sigma).mean()
    jump3_rate = (abs_returns >= 3 * global_sigma).mean()

    # Rolling 60m sigma stability
    roll60 = returns.rolling(60).std()
    mean_sigma60 = roll60.mean()
    std_sigma60 = roll60.std()
    sigma60_cv = std_sigma60 / mean_sigma60 if mean_sigma60 and mean_sigma60 > 0 else np.nan

    # ER on rolling 15m windows
    er_series = df["close"].rolling(15).apply(compute_er, raw=False)
    er_series = er_series.dropna()
    er_median = er_series.median() if not er_series.empty else np.nan
    trend_frac = (er_series >= 0.6).mean() if not er_series.empty else np.nan
    chop_frac = (er_series <= 0.2).mean() if not er_series.empty else np.nan

    bucket_df = make_bucket_df(df)
    stick = stickiness_summary(bucket_df)

    out = {
        "symbol": symbol,
        "rows": int(len(df)),
        "global_sigma_bp": global_sigma * 10000,
        "jump2_rate": jump2_rate,
        "jump3_rate": jump3_rate,
        "mean_sigma60_bp": mean_sigma60 * 10000 if mean_sigma60 is not None else np.nan,
        "sigma60_cv": sigma60_cv,
        "er_median": er_median,
        "trend_frac": trend_frac,
        "chop_frac": chop_frac,
        **stick,
    }
    return out, bucket_df


def bootstrap_scores(asset_rows: list[dict], bucket_map: dict, n_boot: int = 200) -> tuple[pd.DataFrame, pd.DataFrame]:
    assets = [r["symbol"] for r in asset_rows]
    scores = {a: [] for a in assets}
    top_counts = {a: 0 for a in assets}

    for i in range(n_boot):
        boot_rows = []
        for r in asset_rows:
            bdf = bucket_map.get(r["symbol"])
            if bdf is None or bdf.empty:
                stick_t2 = np.nan
                stick_t3 = np.nan
            else:
                sample = bdf.sample(n=len(bdf), replace=True)
                stick_sum = stickiness_summary(sample)
                stick_t2 = np.nanmean([stick_sum["stick_above_t2"], stick_sum["stick_below_t2"]])
                stick_t3 = np.nanmean([stick_sum["stick_above_t3"], stick_sum["stick_below_t3"]])

            boot_rows.append(
                {
                    "symbol": r["symbol"],
                    "stick_t2": stick_t2,
                    "stick_t3": stick_t3,
                    "jump2_rate": r["jump2_rate"],
                    "sigma60_cv": r["sigma60_cv"],
                    "trend_frac": r["trend_frac"],
                }
            )

        tmp = pd.DataFrame(boot_rows)
        # Normalize per replicate using z-score CDF
        stick_t2_n = norm_cdf_scores(tmp["stick_t2"], higher_better=True)
        stick_t3_n = norm_cdf_scores(tmp["stick_t3"], higher_better=True)
        jump2_n = norm_cdf_scores(tmp["jump2_rate"], higher_better=False)
        sigma_cv_n = norm_cdf_scores(tmp["sigma60_cv"], higher_better=False)
        trend_n = norm_cdf_scores(tmp["trend_frac"], higher_better=True)

        tmp["score"] = (
            0.40 * stick_t2_n
            + 0.20 * stick_t3_n
            + 0.20 * jump2_n
            + 0.10 * sigma_cv_n
            + 0.10 * trend_n
        ) * 100.0

        best = tmp.sort_values("score", ascending=False).iloc[0]["symbol"]
        top_counts[best] += 1
        for _, row in tmp.iterrows():
            scores[row["symbol"]].append(row["score"])

    summary_rows = []
    for a in assets:
        arr = np.array(scores[a])
        summary_rows.append(
            {
                "symbol": a,
                "score_mean": float(np.nanmean(arr)),
                "score_p05": float(np.nanpercentile(arr, 5)),
                "score_p50": float(np.nanpercentile(arr, 50)),
                "score_p95": float(np.nanpercentile(arr, 95)),
            }
        )
    rank_rows = []
    for a in assets:
        rank_rows.append(
            {
                "symbol": a,
                "rank1_prob": top_counts[a] / n_boot,
            }
        )
    return pd.DataFrame(summary_rows), pd.DataFrame(rank_rows)


def minmax(series: pd.Series) -> pd.Series:
    lo = series.min()
    hi = series.max()
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        return pd.Series([0.5] * len(series), index=series.index)
    return (series - lo) / (hi - lo)


def norm_cdf_scores(series: pd.Series, higher_better: bool = True) -> pd.Series:
    if series.isna().all():
        return pd.Series([0.5] * len(series), index=series.index)
    mean = series.mean()
    std = series.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series([0.5] * len(series), index=series.index)
    z = (series - mean) / std
    cdf = z.apply(lambda v: NormalDist().cdf(v))
    return cdf if higher_better else (1 - cdf)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="analysis_asset_compare", help="Output directory")
    parser.add_argument("--days", type=int, default=30, help="Lookback days")
    parser.add_argument("--refresh", action="store_true", help="Force re-download")
    parser.add_argument("--assets", default="BTC,ETH,SOL,XRP", help="Comma-separated assets")
    parser.add_argument("--quote", default="USDT", help="Quote asset (default USDT)")
    parser.add_argument("--bins", default="5,10,20,40,80,160", help="Delta bins (bp) for stickiness buckets")
    parser.add_argument("--bootstrap", type=int, default=200, help="Bootstrap iterations for score confidence")
    args = parser.parse_args()

    out_dir = args.out
    data_dir = os.path.join(out_dir, "data")
    os.makedirs(out_dir, exist_ok=True)

    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]
    results = []
    bucket_map = {}

    for a in assets:
        symbol = f"{a}{args.quote}"
        print(f"Downloading {symbol}..." )
        csv_path = download_or_load(symbol, data_dir, args.days, args.refresh)
        print(f"Analyzing {symbol}...")
        res, bucket_df = analyze_asset(symbol, csv_path)
        results.append(res)
        bucket_map[symbol] = bucket_df

    df = pd.DataFrame(results)

    # Stickiness averages
    df["stick_t3"] = df[["stick_above_t3", "stick_below_t3"]].mean(axis=1)
    df["stick_t2"] = df[["stick_above_t2", "stick_below_t2"]].mean(axis=1)

    # Normalize for scoring using z-score CDF (more stable with small asset sets)
    stick_t2_n = norm_cdf_scores(df["stick_t2"], higher_better=True)
    stick_t3_n = norm_cdf_scores(df["stick_t3"], higher_better=True)
    jump2_n = norm_cdf_scores(df["jump2_rate"], higher_better=False)
    sigma_cv_n = norm_cdf_scores(df["sigma60_cv"], higher_better=False)
    trend_n = norm_cdf_scores(df["trend_frac"], higher_better=True)

    df["predictability_score"] = (
        0.40 * stick_t2_n
        + 0.20 * stick_t3_n
        + 0.20 * jump2_n
        + 0.10 * sigma_cv_n
        + 0.10 * trend_n
    ) * 100.0

    df = df.sort_values("predictability_score", ascending=False)

    # Delta-bucket stickiness (per asset)
    bins = [float(x) for x in args.bins.split(",") if x.strip()]
    bins_dir = os.path.join(out_dir, "stickiness_bins")
    os.makedirs(bins_dir, exist_ok=True)
    for r in results:
        sym = r["symbol"]
        bdf = bucket_map.get(sym)
        if bdf is None or bdf.empty:
            continue
        binned = stickiness_bins(bdf, bins)
        binned.to_csv(os.path.join(bins_dir, f"{sym}_bins.csv"), index=False)

    # Bootstrap confidence
    boot_summary, boot_rank = bootstrap_scores(results, bucket_map, n_boot=args.bootstrap)
    boot_summary.to_csv(os.path.join(out_dir, "bootstrap_summary.csv"), index=False)
    boot_rank.to_csv(os.path.join(out_dir, "bootstrap_rank_probs.csv"), index=False)

    # Save outputs
    summary_path = os.path.join(out_dir, "summary.csv")
    df.to_csv(summary_path, index=False)

    # Report
    report_path = os.path.join(out_dir, "report.txt")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"Asset Predictability Comparison ({args.days}d, 1m, Binance)\n")
        f.write("================================================\n\n")
        f.write("Scoring uses z-score CDF normalization (stable with 2 assets).\n\n")
        f.write("Ranking (higher = more predictable near expiry)\n")
        for i, row in df.iterrows():
            f.write(f"  {row['symbol']}: {row['predictability_score']:.1f}\n")
        f.write("\nKey Metrics\n")
        for i, row in df.iterrows():
            f.write(f"\n{row['symbol']}\n")
            f.write(f"  Stickiness t-2: {row['stick_t2']:.2%} (above {row['stick_above_t2']:.2%}, below {row['stick_below_t2']:.2%})\n")
            f.write(f"  Stickiness t-3: {row['stick_t3']:.2%} (above {row['stick_above_t3']:.2%}, below {row['stick_below_t3']:.2%})\n")
            f.write(f"  Jump ≥2σ: {row['jump2_rate']:.2%} | Jump ≥3σ: {row['jump3_rate']:.2%}\n")
            f.write(f"  Rolling 60m σ mean: {row['mean_sigma60_bp']:.2f} bp | σ stability (CV): {row['sigma60_cv']:.2f}\n")
            f.write(f"  ER median: {row['er_median']:.2f} | Trend frac: {row['trend_frac']:.2%} | Chop frac: {row['chop_frac']:.2%}\n")

        f.write("\nBootstrap score confidence (p05/p50/p95)\n")
        for _, row in boot_summary.iterrows():
            f.write(f"  {row['symbol']}: {row['score_p05']:.1f} / {row['score_p50']:.1f} / {row['score_p95']:.1f}\n")
        f.write("\nRank-1 probability (bootstrap)\n")
        for _, row in boot_rank.iterrows():
            f.write(f"  {row['symbol']}: {row['rank1_prob']:.2%}\n")

    print(f"Saved summary to {summary_path}")
    print(f"Saved report to {report_path}")


if __name__ == "__main__":
    main()
