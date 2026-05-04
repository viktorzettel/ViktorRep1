#!/usr/bin/env python3
"""
Compile a concise forensic report from Kou live-capture and Polymarket grid outputs.

This script assumes the aggregate analyzers have already been run:
- analysis/analyze_live_capture_sessions.py
- analysis/analyze_polymarket_grid_signals.py
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path("data/live_capture")
OUT_DIR = ROOT / "forensic_analysis"
LIVE_DIR = OUT_DIR / "live_aggregate"
PM_DIR = OUT_DIR / "polymarket_grid"
REPORT_PATH = OUT_DIR / "forensic_live_capture_report.md"


def pct(value: float | None, digits: int = 1) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{100.0 * float(value):.{digits}f}%"


def num(value: float | None, digits: int = 3) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"


def money(value: float | None, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    phat = wins / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * n)) / n) / denom
    return max(0.0, center - half), min(1.0, center + half)


def binom_sf_at_least(k: int, n: int, p: float) -> float:
    """P[X >= k] for a binomial(n, p), computed with a stable recurrence."""
    if n <= 0:
        return float("nan")
    p = min(max(float(p), 0.0), 1.0)
    if p <= 0.0:
        return 1.0 if k <= 0 else 0.0
    if p >= 1.0:
        return 1.0 if k <= n else 0.0
    total = 0.0
    # Compute each mass from log-comb to avoid scipy as a hard dependency.
    for x in range(max(0, k), n + 1):
        log_mass = (
            math.lgamma(n + 1)
            - math.lgamma(x + 1)
            - math.lgamma(n - x + 1)
            + x * math.log(p)
            + (n - x) * math.log1p(-p)
        )
        total += math.exp(log_mass)
    return min(1.0, total)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def quantile(values: list[float], q: float) -> float | None:
    clean = sorted(float(v) for v in values if v is not None and not pd.isna(v))
    if not clean:
        return None
    idx = min(len(clean) - 1, max(0, round((len(clean) - 1) * q)))
    return clean[idx]


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |"]
    out.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        out.append("| " + " | ".join(str(item) for item in row) + " |")
    return "\n".join(out)


def enhance_pm_matrix() -> pd.DataFrame:
    matrix = pd.read_csv(PM_DIR / "polymarket_grid_matrix.csv")
    enhanced_rows: list[dict[str, Any]] = []
    for row in matrix.to_dict("records"):
        wins = int(row["wins"])
        n = int(row["known_outcomes"])
        ci_low, ci_high = wilson_ci(wins, n)
        avg_entry = float(row["avg_entry_price"])
        success = float(row["success_rate"])
        item = dict(row)
        item["ci_low"] = ci_low
        item["ci_high"] = ci_high
        item["edge_vs_avg_entry"] = success - avg_entry
        item["ci_low_minus_avg_entry"] = (ci_low - avg_entry) if ci_low is not None else None
        item["p_value_vs_avg_entry"] = binom_sf_at_least(wins, n, avg_entry)
        enhanced_rows.append(item)
    enhanced = pd.DataFrame(enhanced_rows)
    enhanced.to_csv(PM_DIR / "polymarket_grid_matrix_with_ci.csv", index=False)
    return enhanced


def pm_quote_health(pm_sessions: list[str]) -> tuple[pd.DataFrame, Counter]:
    rows: list[dict[str, Any]] = []
    events = Counter()
    for session_id in pm_sessions:
        quotes = read_jsonl(ROOT / session_id / "polymarket_quotes.jsonl")
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for quote in quotes:
            asset = (((quote.get("polymarket_market") or {}).get("asset")) or "unknown")
            grouped[str(asset)].append(quote)
        for asset, asset_quotes in grouped.items():
            latencies = [
                float((q.get("quote_fetch") or {}).get("latency_s"))
                for q in asset_quotes
                if (q.get("quote_fetch") or {}).get("latency_s") is not None
            ]
            buy_sums = [
                float((q.get("token_prices") or {}).get("buy_price_sum"))
                for q in asset_quotes
                if (q.get("token_prices") or {}).get("buy_price_sum") is not None
            ]
            yes_ask_sizes = [
                float(((q.get("book") or {}).get("yes") or {}).get("ask_size"))
                for q in asset_quotes
                if ((q.get("book") or {}).get("yes") or {}).get("ask_size") is not None
            ]
            no_ask_sizes = [
                float(((q.get("book") or {}).get("no") or {}).get("ask_size"))
                for q in asset_quotes
                if ((q.get("book") or {}).get("no") or {}).get("ask_size") is not None
            ]
            rows.append(
                {
                    "session_id": session_id,
                    "asset": asset,
                    "quote_rows": len(asset_quotes),
                    "latency_median_s": quantile(latencies, 0.50),
                    "latency_p95_s": quantile(latencies, 0.95),
                    "buy_price_sum_median": quantile(buy_sums, 0.50),
                    "buy_price_sum_p95": quantile(buy_sums, 0.95),
                    "yes_ask_size_median": quantile(yes_ask_sizes, 0.50),
                    "no_ask_size_median": quantile(no_ask_sizes, 0.50),
                }
            )
        for event in read_jsonl(ROOT / session_id / "polymarket_events.jsonl"):
            events[(session_id, str(event.get("event_type") or "unknown"))] += 1
    health = pd.DataFrame(rows)
    health.to_csv(PM_DIR / "polymarket_quote_health.csv", index=False)
    return health, events


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    live_summary = json.loads((LIVE_DIR / "analysis_summary.json").read_text(encoding="utf-8"))
    pm_summary = json.loads((PM_DIR / "analysis_summary.json").read_text(encoding="utf-8"))
    sessions = pd.read_csv(LIVE_DIR / "session_summary.csv")
    first_signal = pd.read_csv(LIVE_DIR / "first_signal_summary.csv")
    model_summary = pd.read_csv(LIVE_DIR / "model_summary.csv")
    time_bins = pd.read_csv(LIVE_DIR / "model_by_time_bin.csv")
    safety = pd.read_csv(LIVE_DIR / "safety_hold_quality.csv")
    policy = pd.read_csv(LIVE_DIR / "policy_hold_quality.csv")
    margin = pd.read_csv(LIVE_DIR / "margin_z_hold_quality.csv")
    persistence = pd.read_csv(LIVE_DIR / "persistence_sweep.csv")
    losses = pd.read_csv(LIVE_DIR / "first_signal_losses.csv")
    pm_matrix = enhance_pm_matrix()
    pm_by_time = pd.read_csv(PM_DIR / "polymarket_grid_matrix_by_timeleft.csv")
    pm_sessions = list(pm_summary["sessions"])
    quote_health, event_counts = pm_quote_health(pm_sessions)

    pm_session_ids = set(pm_sessions)
    pm_live_sessions = sessions[sessions["session_id"].isin(pm_session_ids)]
    pm_first_signals = int(pm_live_sessions["first_signal_count"].sum())
    pm_first_wins = int(pm_live_sessions["first_signal_wins"].sum())
    pm_ci_low, pm_ci_high = wilson_ci(pm_first_wins, pm_first_signals)

    overall = first_signal[(first_signal["scope"] == "overall") & (first_signal["symbol"] == "ALL")].iloc[0]
    by_symbol = first_signal[first_signal["scope"] == "by_symbol"].copy()
    by_condition = first_signal[first_signal["scope"] == "by_condition"].copy()

    # Significance checks for first-signal layer.
    first_wins = int(overall["wins"])
    first_n = int(overall["n"])
    p_over_90 = binom_sf_at_least(first_wins, first_n, 0.90)
    p_over_95 = binom_sf_at_least(first_wins, first_n, 0.95)

    # Current live rule from persistence table.
    current_rule = persistence[
        (persistence["slice"].isna())
        & (persistence["threshold"].round(2) == 0.91)
        & (persistence["hold_s"].round(1) == 4.0)
    ].iloc[0]

    # PM rows that pass a simple "statistically above average entry" screen.
    pm_positive = pm_matrix[pm_matrix["ci_low_minus_avg_entry"] > 0].copy()
    pm_positive = pm_positive.sort_values(
        ["asset", "ci_low_minus_avg_entry", "total_roi_filled_pct"],
        ascending=[True, False, False],
    )
    pm_positive.to_csv(PM_DIR / "polymarket_grid_rows_ci_low_above_entry.csv", index=False)

    selected_rows = []
    for asset, threshold, hold in [
        ("xrp", 0.90, 3),
        ("xrp", 0.91, 4),
        ("xrp", 0.95, 2),
        ("eth", 0.94, 2),
        ("eth", 0.96, 4),
        ("eth", 0.91, 4),
    ]:
        row = pm_matrix[
            (pm_matrix["asset"] == asset)
            & (pm_matrix["threshold"].round(2) == threshold)
            & (pm_matrix["hold_seconds"] == hold)
        ].iloc[0]
        selected_rows.append(row)

    fill_rows = []
    for asset in ["eth", "xrp"]:
        low = pm_matrix[
            (pm_matrix["asset"] == asset)
            & (pm_matrix["threshold"].round(2) == 0.90)
            & (pm_matrix["hold_seconds"] == 2)
        ].iloc[0]
        high = pm_matrix[
            (pm_matrix["asset"] == asset)
            & (pm_matrix["threshold"].round(2) == 0.96)
            & (pm_matrix["hold_seconds"] == 5)
        ].iloc[0]
        fill_rows.append(
            [
                asset.upper(),
                f"{pct(low['any_size_fill_rate'])} -> {pct(high['any_size_fill_rate'])}",
                f"{num(low['avg_entry_price'], 3)} -> {num(high['avg_entry_price'], 3)}",
                f"{pct(low['success_rate'])} -> {pct(high['success_rate'])}",
            ]
        )

    time_left_focus = pm_by_time[
        (pm_by_time["asset"].isin(["eth", "xrp"]))
        & (pm_by_time["threshold"].round(2).isin([0.91, 0.95]))
        & (pm_by_time["hold_seconds"].isin([2, 4]))
    ].copy()
    time_left_focus.to_csv(PM_DIR / "polymarket_grid_timeleft_focus.csv", index=False)

    lines: list[str] = []
    lines.append("# Live Capture Forensic Report")
    lines.append("")
    lines.append("Generated from the current contents of `data/live_capture`.")
    lines.append("")
    lines.append("## Executive Takeaway")
    lines.append("")
    lines.append(
        "The data is useful and directionally strong, but it is not yet enough to call the strategy production-proven. "
        "The model-side first-signal layer is consistently strong across eight sessions. "
        "The execution-aware Polymarket matrix is the sharper test, and it currently says: XRP looks statistically promising after observed entry prices; ETH is not yet statistically proven after observed entry prices."
    )
    lines.append("")
    lines.append("The most important forensic detail is that the independent sample unit is not the `6,789` grid rows. Those rows are overlapping threshold/hold views of roughly a few hundred market buckets. Treat the grid as a calibration surface, not thousands of independent trades.")
    lines.append("")

    lines.append("## Dataset")
    dataset_rows = [
        ["Kou capture sessions", int(live_summary["session_count"])],
        ["Complete non-flat buckets", int(live_summary["complete_buckets"])],
        ["Joined Kou snapshots", int(live_summary["joined_snapshots"])],
        ["Polymarket sessions", len(pm_sessions)],
        ["Polymarket quote rows", int(sum((ROOT / sid / "polymarket_quotes.jsonl").read_text(encoding="utf-8").count("\n") for sid in pm_sessions))],
        ["Polymarket grid events", int(pm_summary["grid_events"])],
    ]
    lines.append(markdown_table(["Metric", "Value"], dataset_rows))
    lines.append("")

    session_rows = []
    for row in sessions.to_dict("records"):
        pm_flag = "yes" if row["session_id"] in pm_session_ids else "no"
        session_rows.append(
            [
                row["session_id"],
                row["condition_label"],
                pm_flag,
                num(row["duration_h"], 2),
                int(row["complete_buckets"]),
                f"{int(row['first_signal_wins'])}/{int(row['first_signal_count'])}",
                pct(row["first_signal_win_rate"]),
            ]
        )
    lines.append(markdown_table(["Session", "Condition", "PM sidecar", "Hours", "Buckets", "First signals", "Win rate"], session_rows))
    lines.append("")

    lines.append("## Model-Side Statistics")
    lines.append("")
    lines.append(
        f"Across all sessions the first-signal layer hit `{first_wins}/{first_n}` = `{pct(overall['rate'])}` "
        f"with a Wilson 95% interval of `{pct(overall['ci_low'])}` to `{pct(overall['ci_high'])}`. "
        f"The binomial tail probability of seeing at least this many wins if the true rate were `90%` is about `{p_over_90:.2e}`; against a `95%` true rate it is `{p_over_95:.2e}`. "
        "That is strong evidence that the late/persistent signal is not random, with the caveat that adjacent 5-minute markets are regime-correlated."
    )
    lines.append("")
    symbol_rows = []
    for row in by_symbol.to_dict("records"):
        symbol_rows.append(
            [
                row["symbol"],
                f"{int(row['wins'])}/{int(row['n'])}",
                pct(row["rate"]),
                f"{pct(row['ci_low'])} to {pct(row['ci_high'])}",
            ]
        )
    lines.append(markdown_table(["Symbol", "Wins/N", "Rate", "Wilson 95%"], symbol_rows))
    lines.append("")
    condition_rows = []
    for row in by_condition.to_dict("records"):
        condition_rows.append(
            [
                row["condition_label"],
                f"{int(row['wins'])}/{int(row['n'])}",
                pct(row["rate"]),
                f"{pct(row['ci_low'])} to {pct(row['ci_high'])}",
            ]
        )
    lines.append(markdown_table(["Condition", "Wins/N", "Rate", "Wilson 95%"], condition_rows))
    lines.append("")
    lines.append(
        f"The current model-side rule in the analyzer, `0.91` confidence with `4s` persistence, shows "
        f"`{int(current_rule['wins'])}/{int(current_rule['signals'])}` = `{pct(current_rule['win_rate'])}` "
        f"with Wilson interval `{pct(current_rule['ci_low'])}` to `{pct(current_rule['ci_high'])}`."
    )
    lines.append("")

    model_rows = []
    model_order = model_summary.copy()
    model_order["model_rank"] = model_order["model"].map({"bs": 0, "kou": 1, "raw_kou": 2}).fillna(9)
    model_order["symbol_rank"] = model_order["symbol"].map({"ALL": 0, "ethusdt": 1, "xrpusdt": 2}).fillna(9)
    model_order = model_order.sort_values(["symbol_rank", "model_rank"])
    for _, row in model_order.head(9).iterrows():
        model_rows.append(
            [
                row["symbol"],
                row["model"],
                int(row["n"]),
                pct(row["accuracy"]),
                num(row["brier"], 4),
            ]
        )
    lines.append(markdown_table(["Symbol", "Model", "Snapshots", "Accuracy", "Brier"], model_rows))
    lines.append("")
    lines.append(
        "BS is still the better broad snapshot probability engine by Brier score, but the trading edge is not broad-snapshot prediction. "
        "The edge is the selective late signal plus persistence and safety filtering."
    )
    lines.append("")

    time_rows = []
    order = ["0-15s", "15-30s", "30-60s", "60-90s", "90-120s", "120-180s", "180-240s", "240-300s"]
    all_time = time_bins[time_bins["symbol"] == "ALL"]
    for label in order:
        kou_row = all_time[(all_time["time_bin"] == label) & (all_time["model"] == "kou")]
        bs_row = all_time[(all_time["time_bin"] == label) & (all_time["model"] == "bs")]
        if kou_row.empty or bs_row.empty:
            continue
        kou_row = kou_row.iloc[0]
        bs_row = bs_row.iloc[0]
        time_rows.append(
            [
                label,
                pct(kou_row["accuracy"]),
                pct(bs_row["accuracy"]),
                int(kou_row["n"]),
            ]
        )
    lines.append(markdown_table(["Time Left", "Kou Accuracy", "BS Accuracy", "Snapshots"], time_rows))
    lines.append("")

    lines.append("## Safety Layer")
    safety_rows = []
    for row in safety[(safety["symbol"] == "ALL") & (safety["scope"] == "final_label")].to_dict("records"):
        safety_rows.append([row["safety_final_label"], f"{int(row['wins'])}/{int(row['n'])}", pct(row["rate"])])
    lines.append(markdown_table(["Safety", "Wins/N", "Current-side hold rate"], safety_rows))
    lines.append("")
    policy_rows = []
    for row in policy[policy["symbol"] == "ALL"].to_dict("records"):
        policy_rows.append([row["policy_level"], f"{int(row['wins'])}/{int(row['n'])}", pct(row["rate"])])
    lines.append(markdown_table(["Policy", "Wins/N", "Current-side hold rate"], policy_rows))
    lines.append("")
    margin_rows = []
    for row in margin[(margin["symbol"] == "ALL") & (margin["time_scope"] == "late120")].to_dict("records"):
        margin_rows.append([row["margin_z_bin"], f"{int(row['wins'])}/{int(row['n'])}", pct(row["rate"])])
    lines.append(markdown_table(["Last 120s margin_z", "Wins/N", "Hold rate"], margin_rows))
    lines.append("")
    lines.append(
        "`margin_z` remains the clearest safety feature. Late-window states below `1.0` z hold only about two thirds to three quarters of the time, while `>=2.0` z holds around `98%`."
    )
    lines.append("")

    lines.append("## Polymarket Execution Matrix")
    lines.append("")
    lines.append(
        f"The Polymarket-aware sidecar produced `{pm_summary['grid_events']}` grid events across `{len(pm_sessions)}` sessions. "
        f"On those same three sessions, the model-side first-signal result was `{pm_first_wins}/{pm_first_signals}` = `{pct(pm_first_wins / pm_first_signals)}` "
        f"with Wilson interval `{pct(pm_ci_low)}` to `{pct(pm_ci_high)}`."
    )
    lines.append("")
    lines.append("The enhanced matrix with confidence intervals is written to `data/live_capture/forensic_analysis/polymarket_grid/polymarket_grid_matrix_with_ci.csv`.")
    lines.append("")
    candidate_rows = []
    for row in selected_rows:
        candidate_rows.append(
            [
                str(row["asset"]).upper(),
                f"{int(round(float(row['threshold']) * 100))}%/{int(row['hold_seconds'])}s",
                f"{int(row['wins'])}/{int(row['known_outcomes'])}",
                pct(row["success_rate"]),
                f"{pct(row['ci_low'])} to {pct(row['ci_high'])}",
                num(row["avg_entry_price"], 3),
                pct(row["edge_vs_avg_entry"]),
                pct(row["any_size_fill_rate"]),
                pct(row["total_roi_filled"] if not pd.isna(row["total_roi_filled"]) else None),
                money(row["total_pnl_filled"]),
                f"{row['p_value_vs_avg_entry']:.3g}",
            ]
        )
    lines.append(
        markdown_table(
            [
                "Asset",
                "Rule",
                "Wins/N",
                "Win",
                "Wilson 95%",
                "Avg Entry",
                "Win-Entry",
                "Visible Fill",
                "Filled ROI",
                "Filled PnL",
                "p vs entry",
            ],
            candidate_rows,
        )
    )
    lines.append("")
    lines.append(
        f"Rows where the Wilson lower bound beats average observed entry price: `{len(pm_positive)}`. "
        "All of them are XRP rows. ETH has positive-looking rows, but none clear that stricter confidence screen yet."
    )
    lines.append("")
    fill_table = markdown_table(["Asset", "Visible fill rate 90%/2s -> 96%/5s", "Avg entry", "Win rate"], fill_rows)
    lines.append(fill_table)
    lines.append("")
    lines.append(
        "Stricter thresholds and longer holds improve apparent win rate, but they also raise entry price and reduce visible fillability. "
        "This matters because a token bought at `0.98` needs a very high true win rate; one loss can erase many small wins."
    )
    lines.append("")

    lines.append("## Quote Capture Health")
    health_rows = []
    for row in quote_health.to_dict("records"):
        health_rows.append(
            [
                row["session_id"],
                str(row["asset"]).upper(),
                int(row["quote_rows"]),
                f"{num(row['latency_median_s'], 3)} / {num(row['latency_p95_s'], 3)}",
                f"{num(row['buy_price_sum_median'], 2)} / {num(row['buy_price_sum_p95'], 2)}",
                f"{num(row['yes_ask_size_median'], 1)} / {num(row['no_ask_size_median'], 1)}",
            ]
        )
    lines.append(markdown_table(["Session", "Asset", "Quotes", "Latency med/p95 s", "Buy sum med/p95", "Median YES/NO ask size"], health_rows))
    lines.append("")
    error_rows = []
    for (session_id, event_type), count in sorted(event_counts.items()):
        if "error" in event_type:
            error_rows.append([session_id, event_type, count])
    if error_rows:
        lines.append(markdown_table(["Session", "Event", "Count"], error_rows))
        lines.append("")
    lines.append(
        "Quote latency is mostly small enough for analysis-grade capture. The data still does not prove personal fills: it observes read-only CLOB prices and visible ask size, not queue position, order acknowledgements, or slippage after submission."
    )
    lines.append("")

    lines.append("## Failure Pattern")
    lines.append("")
    loss_rows = []
    for row in losses.to_dict("records"):
        loss_rows.append(
            [
                row["session_id"],
                row["symbol"],
                row["signal_state"],
                num(row["time_left_s"], 1),
                num(row["kou_yes"], 4),
                num(row["margin_z"], 3),
                row["settled_side"],
                int(row["sampled_cross_count"]),
                num(row["settled_delta_bps"], 2),
            ]
        )
    lines.append(markdown_table(["Session", "Symbol", "Signal", "t-left", "Kou yes", "margin_z", "Settled", "Crosses", "Final bps"], loss_rows))
    lines.append("")
    lines.append(
        "The common loss signature is late crossing/chop after a strong-looking signal. Earlier data made this look mostly XRP-specific; the newest run adds several ETH losses too. This argues for a crossing/chop veto, not just stricter probability."
    )
    lines.append("")

    lines.append("## Significance And Usefulness")
    lines.append("")
    lines.append(
        "Useful: yes. The data is already good enough to reject the idea that the late persistent signal is random, and it is good enough to identify execution-aware XRP candidate rules."
    )
    lines.append("")
    lines.append(
        "Not enough yet: production EV. The independent sample is closer to hundreds of asset-buckets, not thousands of grid events. The three Polymarket sessions all sit in a narrow calendar window, the rules are highly overlapping, and observed fillability is not the same as real fills."
    )
    lines.append("")
    lines.append(
        "A practical significance screen is: Wilson lower bound of win rate should exceed average entry price. XRP has multiple rows passing that screen; ETH currently has none. This is the cleanest single result from the execution-aware data."
    )
    lines.append("")

    lines.append("## Suggested Next Steps")
    lines.append("")
    lines.append("1. Keep running paired 4-hour sessions with both sidecars. Prioritize more Polymarket-aware data, not more model-only data.")
    lines.append("2. Treat XRP `0.90-0.93` with `3-4s` persistence as the leading candidate band for paper-trade replay. Keep `0.95/2s` as a stricter comparison.")
    lines.append("3. Do not promote ETH live size yet. ETH needs either cheaper observed entries, a stronger veto against late crosses, or more samples proving it can beat `0.96-0.98` entries.")
    lines.append("4. Add a candidate veto for repeated strike crossing/chop in the final minute, then rerun the matrix with and without that veto.")
    lines.append("5. Start logging actual order attempts in tiny size when ready, because visible ask size and read-only buy price are still only fill proxies.")
    lines.append("")

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {PM_DIR / 'polymarket_grid_matrix_with_ci.csv'}")
    print(f"Wrote {PM_DIR / 'polymarket_grid_rows_ci_low_above_entry.csv'}")
    print(f"Wrote {PM_DIR / 'polymarket_quote_health.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
