#!/usr/bin/env python3
"""
Locked evaluator for offline Kou autoresearch candidates.

The evaluator reads existing captured-data analysis artifacts, loads exactly
one candidate module, and scores it on chronological session splits. During an
autoresearch run, keep this file fixed and edit only candidate.py.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable, Mapping

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIRST_SIGNALS = ROOT / "data/live_capture/forensic_analysis/live_aggregate/first_signals.csv"
DEFAULT_GRID_EVENTS = ROOT / "data/live_capture/forensic_analysis/polymarket_grid/polymarket_grid_events_enriched.csv"
DEFAULT_OUTPUT_DIR = ROOT / "analysis/autoresearch_kou/runs"
DEFAULT_RESULTS_LOG = ROOT / "analysis/autoresearch_kou/results.tsv"
DEFAULT_CANDIDATE = ROOT / "analysis/autoresearch_kou/candidate.py"
EPS = 1e-12


RESULT_FIELDS = [
    "run_id",
    "timestamp_utc",
    "candidate_name",
    "candidate_sha256",
    "evaluator_sha256",
    "dataset",
    "split",
    "group",
    "sessions",
    "n",
    "allowed",
    "abstained",
    "wins",
    "losses",
    "win_rate",
    "ci_low",
    "ci_high",
    "brier_yes",
    "brier_side",
    "log_loss_yes",
    "avg_entry",
    "ci_low_minus_avg_entry",
    "paper_cost",
    "paper_pnl",
    "paper_roi",
    "losses_avoided",
    "winners_blocked",
    "candidate_errors",
    "report_path",
]


@dataclass(frozen=True)
class Candidate:
    name: str
    description: str
    path: Path
    sha256: str
    module: ModuleType
    score_first_signal: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    score_grid_event: Callable[[Mapping[str, Any]], Mapping[str, Any]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a Kou autoresearch candidate")
    parser.add_argument("--candidate", default=str(DEFAULT_CANDIDATE), help="Candidate module path")
    parser.add_argument("--first-signals", default=str(DEFAULT_FIRST_SIGNALS), help="first_signals.csv path")
    parser.add_argument("--grid-events", default=str(DEFAULT_GRID_EVENTS), help="polymarket_grid_events_enriched.csv path")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for JSON run reports")
    parser.add_argument("--results-log", default=str(DEFAULT_RESULTS_LOG), help="TSV experiment log")
    parser.add_argument("--train-share", type=float, default=0.60, help="Chronological train session share")
    parser.add_argument("--validation-share", type=float, default=0.20, help="Chronological validation session share")
    parser.add_argument("--min-group-n", type=int, default=20, help="Minimum rows for asset/symbol subgroup metrics")
    parser.add_argument("--max-rows", type=int, default=None, help="Optional quick-smoke row cap per dataset")
    parser.add_argument("--no-log", action="store_true", help="Do not append to results.tsv")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_candidate(path: Path) -> Candidate:
    if not path.exists():
        raise FileNotFoundError(f"Candidate file not found: {path}")
    spec = importlib.util.spec_from_file_location("kou_autoresearch_candidate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import candidate module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    score_first_signal = getattr(module, "score_first_signal", None)
    score_grid_event = getattr(module, "score_grid_event", None)
    if not callable(score_first_signal):
        raise AttributeError("Candidate must define score_first_signal(row)")
    if not callable(score_grid_event):
        raise AttributeError("Candidate must define score_grid_event(row)")

    return Candidate(
        name=str(getattr(module, "CANDIDATE_NAME", path.stem)),
        description=str(getattr(module, "CANDIDATE_DESCRIPTION", "")),
        path=path,
        sha256=sha256_file(path),
        module=module,
        score_first_signal=score_first_signal,
        score_grid_event=score_grid_event,
    )


def clean_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def clean_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not pd.isna(value):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return None


def finite_or_none(value: Any) -> float | None:
    number = clean_float(value)
    return number if number is not None and math.isfinite(number) else None


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n <= 0:
        return None, None
    phat = wins / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2.0 * n)) / denom
    half = z * math.sqrt((phat * (1.0 - phat) + z * z / (4.0 * n)) / n) / denom
    return max(0.0, center - half), min(1.0, center + half)


def safe_rate(numer: float, denom: float) -> float | None:
    if denom <= 0:
        return None
    return numer / denom


def pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{100.0 * float(value):.2f}%"


def fmt(value: float | None, digits: int = 4) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.{digits}f}"


def session_split_map(sessions: Iterable[str], train_share: float, validation_share: float) -> dict[str, str]:
    ordered = sorted(str(s) for s in sessions if str(s) and str(s) != "nan")
    n = len(ordered)
    if n == 0:
        return {}
    if n == 1:
        return {ordered[0]: "test"}
    if n == 2:
        return {ordered[0]: "train", ordered[1]: "test"}

    train_n = max(1, int(math.floor(n * train_share)))
    validation_n = max(1, int(math.floor(n * validation_share)))
    if train_n + validation_n >= n:
        overflow = train_n + validation_n - (n - 1)
        train_n = max(1, train_n - overflow)
    test_start = train_n + validation_n
    if test_start >= n:
        test_start = n - 1
    split: dict[str, str] = {}
    for idx, session in enumerate(ordered):
        if idx < train_n:
            split[session] = "train"
        elif idx < test_start:
            split[session] = "validation"
        else:
            split[session] = "test"
    return split


def apply_session_splits(df: pd.DataFrame, train_share: float, validation_share: float) -> tuple[pd.DataFrame, dict[str, str]]:
    if df.empty or "session_id" not in df.columns:
        out = df.copy()
        out["_split"] = "test"
        return out, {}
    split_map = session_split_map(df["session_id"].dropna().unique(), train_share, validation_share)
    out = df.copy()
    out["_split"] = out["session_id"].astype(str).map(split_map).fillna("ignored")
    return out[out["_split"] != "ignored"].copy(), split_map


def row_mapping(series: pd.Series) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in series.to_dict().items():
        if isinstance(value, np.generic):
            value = value.item()
        if isinstance(value, float) and math.isnan(value):
            value = None
        out[str(key)] = value
    return out


def call_candidate(func: Callable[[Mapping[str, Any]], Mapping[str, Any]], row: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    try:
        result = func(row)
    except Exception as exc:  # noqa: BLE001 - candidates should not crash the evaluator
        return {"allow_trade": False, "reason": f"candidate_error:{type(exc).__name__}:{exc}"}, True
    if not isinstance(result, Mapping):
        return {"allow_trade": False, "reason": "candidate_returned_non_mapping"}, True
    return dict(result), False


def group_label(df: pd.DataFrame, group_col: str | None, group_value: Any | None) -> tuple[pd.DataFrame, str]:
    if group_col is None:
        return df, "ALL"
    sub = df[df[group_col].astype(str) == str(group_value)].copy()
    return sub, f"{group_col}={group_value}"


def evaluate_first_signals(
    df: pd.DataFrame,
    candidate: Candidate,
    *,
    split_name: str,
    group: str,
) -> dict[str, Any]:
    rows = df.copy()
    total = int(len(rows))
    sessions = ",".join(sorted(rows["session_id"].astype(str).dropna().unique())) if total and "session_id" in rows else ""
    decisions: list[dict[str, Any]] = []
    candidate_errors = 0

    for _, series in rows.iterrows():
        row = row_mapping(series)
        result, errored = call_candidate(candidate.score_first_signal, row)
        candidate_errors += int(errored)
        allow = clean_bool(result.get("allow_trade"))
        allow = True if allow is None else allow
        win = clean_bool(row.get("win"))
        if win is None:
            continue

        prob_yes = finite_or_none(result.get("adjusted_prob_yes"))
        if prob_yes is None:
            prob_yes = finite_or_none(result.get("prob_yes"))
        if prob_yes is None:
            prob_yes = finite_or_none(row.get("kou_yes"))
        if prob_yes is not None:
            prob_yes = min(1.0 - EPS, max(EPS, prob_yes))

        settled_yes = finite_or_none(row.get("settled_yes_num"))
        if settled_yes is None:
            settled_yes = 1.0 if str(row.get("settled_side")) == "yes" else 0.0

        decision_yes = clean_bool(row.get("signal_decision_yes"))
        if decision_yes is None:
            decision_yes = str(row.get("signal_state")) == "BUY_YES"
        side_prob = None if prob_yes is None else (prob_yes if decision_yes else 1.0 - prob_yes)

        decisions.append(
            {
                "allow": allow,
                "win": bool(win),
                "prob_yes": prob_yes,
                "settled_yes": settled_yes,
                "side_prob": side_prob,
            }
        )

    allowed = [row for row in decisions if row["allow"]]
    wins = sum(1 for row in allowed if row["win"])
    losses = len(allowed) - wins
    ci_low, ci_high = wilson_ci(wins, len(allowed))
    yes_briers = [
        (row["prob_yes"] - row["settled_yes"]) ** 2
        for row in allowed
        if row["prob_yes"] is not None and row["settled_yes"] is not None
    ]
    side_briers = [
        (row["side_prob"] - float(row["win"])) ** 2
        for row in allowed
        if row["side_prob"] is not None
    ]
    log_losses = [
        -(row["settled_yes"] * math.log(row["prob_yes"]) + (1.0 - row["settled_yes"]) * math.log(1.0 - row["prob_yes"]))
        for row in allowed
        if row["prob_yes"] is not None and row["settled_yes"] is not None
    ]
    losses_avoided = sum(1 for row in decisions if not row["allow"] and not row["win"])
    winners_blocked = sum(1 for row in decisions if not row["allow"] and row["win"])

    return {
        "dataset": "first_signals",
        "split": split_name,
        "group": group,
        "sessions": sessions,
        "n": total,
        "allowed": len(allowed),
        "abstained": len(decisions) - len(allowed),
        "wins": wins,
        "losses": losses,
        "win_rate": safe_rate(wins, len(allowed)),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "brier_yes": None if not yes_briers else float(np.mean(yes_briers)),
        "brier_side": None if not side_briers else float(np.mean(side_briers)),
        "log_loss_yes": None if not log_losses else float(np.mean(log_losses)),
        "avg_entry": None,
        "ci_low_minus_avg_entry": None,
        "paper_cost": None,
        "paper_pnl": None,
        "paper_roi": None,
        "losses_avoided": losses_avoided,
        "winners_blocked": winners_blocked,
        "candidate_errors": candidate_errors,
    }


def evaluate_grid_events(
    df: pd.DataFrame,
    candidate: Candidate,
    *,
    split_name: str,
    group: str,
) -> dict[str, Any]:
    rows = df.copy()
    if "known_outcome" in rows.columns:
        rows = rows[rows["known_outcome"].map(lambda value: clean_bool(value) is True)].copy()
    total = int(len(rows))
    sessions = ",".join(sorted(rows["session_id"].astype(str).dropna().unique())) if total and "session_id" in rows else ""
    decisions: list[dict[str, Any]] = []
    candidate_errors = 0

    for _, series in rows.iterrows():
        row = row_mapping(series)
        result, errored = call_candidate(candidate.score_grid_event, row)
        candidate_errors += int(errored)
        allow = clean_bool(result.get("allow_trade"))
        allow = True if allow is None else allow
        win = clean_bool(row.get("win"))
        if win is None:
            continue
        entry = finite_or_none(row.get("entry_price"))
        pnl_per_share = finite_or_none(row.get("pnl_per_share"))
        estimated_cost = finite_or_none(row.get("estimated_cost")) or 0.0
        estimated_pnl = finite_or_none(row.get("estimated_pnl")) or 0.0
        fill_status = str(row.get("fill_status") or "")
        filled = fill_status in {"full", "partial"}
        decisions.append(
            {
                "allow": allow,
                "win": bool(win),
                "entry": entry,
                "pnl_per_share": pnl_per_share,
                "estimated_cost": estimated_cost,
                "estimated_pnl": estimated_pnl,
                "filled": filled,
            }
        )

    allowed = [row for row in decisions if row["allow"]]
    wins = sum(1 for row in allowed if row["win"])
    losses = len(allowed) - wins
    ci_low, ci_high = wilson_ci(wins, len(allowed))
    entries = [row["entry"] for row in allowed if row["entry"] is not None]
    avg_entry = None if not entries else float(np.mean(entries))
    share_cost = sum(row["entry"] or 0.0 for row in allowed if row["entry"] is not None)
    share_pnl = sum(row["pnl_per_share"] or 0.0 for row in allowed if row["pnl_per_share"] is not None)
    filled_cost = sum(row["estimated_cost"] for row in allowed if row["filled"])
    filled_pnl = sum(row["estimated_pnl"] for row in allowed if row["filled"])
    paper_cost = filled_cost if filled_cost > 0 else share_cost
    paper_pnl = filled_pnl if filled_cost > 0 else share_pnl
    losses_avoided = sum(1 for row in decisions if not row["allow"] and not row["win"])
    winners_blocked = sum(1 for row in decisions if not row["allow"] and row["win"])

    return {
        "dataset": "polymarket_grid",
        "split": split_name,
        "group": group,
        "sessions": sessions,
        "n": total,
        "allowed": len(allowed),
        "abstained": len(decisions) - len(allowed),
        "wins": wins,
        "losses": losses,
        "win_rate": safe_rate(wins, len(allowed)),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "brier_yes": None,
        "brier_side": None,
        "log_loss_yes": None,
        "avg_entry": avg_entry,
        "ci_low_minus_avg_entry": None if ci_low is None or avg_entry is None else ci_low - avg_entry,
        "paper_cost": paper_cost,
        "paper_pnl": paper_pnl,
        "paper_roi": safe_rate(paper_pnl, paper_cost),
        "losses_avoided": losses_avoided,
        "winners_blocked": winners_blocked,
        "candidate_errors": candidate_errors,
    }


def _grid_decisions(df: pd.DataFrame, candidate: Candidate) -> tuple[list[dict[str, Any]], int]:
    decisions: list[dict[str, Any]] = []
    candidate_errors = 0
    for row_idx, series in df.iterrows():
        row = row_mapping(series)
        result, errored = call_candidate(candidate.score_grid_event, row)
        candidate_errors += int(errored)
        allow = clean_bool(result.get("allow_trade"))
        allow = True if allow is None else allow
        win = clean_bool(row.get("win"))
        if win is None:
            continue
        entry = finite_or_none(row.get("entry_price"))
        pnl_per_share = finite_or_none(row.get("pnl_per_share"))
        estimated_cost = finite_or_none(row.get("estimated_cost")) or 0.0
        estimated_pnl = finite_or_none(row.get("estimated_pnl")) or 0.0
        fill_status = str(row.get("fill_status") or "")
        filled = fill_status in {"full", "partial"}
        captured_at = str(row.get("captured_at_iso") or "")
        threshold = finite_or_none(row.get("threshold"))
        hold_seconds = finite_or_none(row.get("hold_seconds"))
        decisions.append(
            {
                "row_idx": int(row_idx) if isinstance(row_idx, (int, np.integer)) else len(decisions),
                "session_id": str(row.get("session_id") or ""),
                "asset": str(row.get("asset") or "unknown"),
                "bucket_end": str(row.get("bucket_end") or ""),
                "side": str(row.get("side") or "unknown"),
                "allow": allow,
                "win": bool(win),
                "entry": entry,
                "pnl_per_share": pnl_per_share,
                "estimated_cost": estimated_cost,
                "estimated_pnl": estimated_pnl,
                "filled": filled,
                "captured_at": captured_at,
                "threshold": threshold if threshold is not None else math.inf,
                "hold_seconds": hold_seconds if hold_seconds is not None else math.inf,
            }
        )
    return decisions, candidate_errors


def evaluate_grid_clusters(
    df: pd.DataFrame,
    candidate: Candidate,
    *,
    split_name: str,
    group: str,
) -> dict[str, Any]:
    """Evaluate Polymarket grid events as bucket-side clusters.

    The row-level grid matrix is useful for threshold economics, but rows are
    highly correlated: one bucket can emit many threshold/hold candidates. This
    cluster view counts one outcome for each `session_id x asset x bucket_end x
    side` group, and uses the first allowed row as the representative entry.
    """

    rows = df.copy()
    if "known_outcome" in rows.columns:
        rows = rows[rows["known_outcome"].map(lambda value: clean_bool(value) is True)].copy()
    sessions = ",".join(sorted(rows["session_id"].astype(str).dropna().unique())) if len(rows) and "session_id" in rows else ""
    decisions, candidate_errors = _grid_decisions(rows, candidate)

    clusters: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for decision in decisions:
        key = (
            decision["session_id"],
            decision["asset"],
            decision["bucket_end"],
            decision["side"],
        )
        clusters.setdefault(key, []).append(decision)

    cluster_rows: list[dict[str, Any]] = []
    for items in clusters.values():
        items = sorted(items, key=lambda row: (row["captured_at"], row["threshold"], row["hold_seconds"], row["row_idx"]))
        allowed_items = [row for row in items if row["allow"]]
        cluster_win = bool(items[0]["win"])
        if allowed_items:
            selected = allowed_items[0]
            entry = selected["entry"]
            pnl_per_share = selected["pnl_per_share"]
            if selected["filled"]:
                paper_cost = selected["estimated_cost"]
                paper_pnl = selected["estimated_pnl"]
            else:
                paper_cost = entry or 0.0
                paper_pnl = pnl_per_share or 0.0
            cluster_rows.append(
                {
                    "allow": True,
                    "win": cluster_win,
                    "entry": entry,
                    "paper_cost": paper_cost,
                    "paper_pnl": paper_pnl,
                }
            )
        else:
            cluster_rows.append(
                {
                    "allow": False,
                    "win": cluster_win,
                    "entry": None,
                    "paper_cost": 0.0,
                    "paper_pnl": 0.0,
                }
            )

    allowed = [row for row in cluster_rows if row["allow"]]
    wins = sum(1 for row in allowed if row["win"])
    losses = len(allowed) - wins
    ci_low, ci_high = wilson_ci(wins, len(allowed))
    entries = [row["entry"] for row in allowed if row["entry"] is not None]
    avg_entry = None if not entries else float(np.mean(entries))
    paper_cost = sum(row["paper_cost"] for row in allowed)
    paper_pnl = sum(row["paper_pnl"] for row in allowed)
    losses_avoided = sum(1 for row in cluster_rows if not row["allow"] and not row["win"])
    winners_blocked = sum(1 for row in cluster_rows if not row["allow"] and row["win"])

    return {
        "dataset": "polymarket_clusters",
        "split": split_name,
        "group": group,
        "sessions": sessions,
        "n": len(cluster_rows),
        "allowed": len(allowed),
        "abstained": len(cluster_rows) - len(allowed),
        "wins": wins,
        "losses": losses,
        "win_rate": safe_rate(wins, len(allowed)),
        "ci_low": ci_low,
        "ci_high": ci_high,
        "brier_yes": None,
        "brier_side": None,
        "log_loss_yes": None,
        "avg_entry": avg_entry,
        "ci_low_minus_avg_entry": None if ci_low is None or avg_entry is None else ci_low - avg_entry,
        "paper_cost": paper_cost,
        "paper_pnl": paper_pnl,
        "paper_roi": safe_rate(paper_pnl, paper_cost),
        "losses_avoided": losses_avoided,
        "winners_blocked": winners_blocked,
        "candidate_errors": candidate_errors,
    }


def metric_rows_for_dataset(
    df: pd.DataFrame,
    candidate: Candidate,
    *,
    dataset: str,
    group_col: str,
    min_group_n: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    evaluators: list[Callable[..., dict[str, Any]]]
    if dataset == "first_signals":
        evaluators = [evaluate_first_signals]
    elif dataset == "polymarket_grid":
        evaluators = [evaluate_grid_events, evaluate_grid_clusters]
    else:
        raise ValueError(f"Unknown dataset: {dataset}")
    for split_name in ["train", "validation", "test"]:
        split_df = df[df["_split"] == split_name].copy()
        if split_df.empty:
            continue
        for evaluator in evaluators:
            rows.append(evaluator(split_df, candidate, split_name=split_name, group="ALL"))
        for group_value in sorted(split_df[group_col].dropna().astype(str).unique()) if group_col in split_df else []:
            sub, label = group_label(split_df, group_col, group_value)
            if len(sub) >= min_group_n:
                for evaluator in evaluators:
                    rows.append(evaluator(sub, candidate, split_name=split_name, group=label))
    return rows


def load_csv(path: Path, max_rows: int | None) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame()
    df = pd.read_csv(path, low_memory=False)
    if max_rows is not None and max_rows > 0:
        df = df.head(max_rows).copy()
    return df


def compact_metric_row(row: dict[str, Any], *, run_id: str, timestamp_utc: str, candidate: Candidate, evaluator_hash: str, report_path: Path) -> dict[str, str]:
    out: dict[str, str] = {
        "run_id": run_id,
        "timestamp_utc": timestamp_utc,
        "candidate_name": candidate.name,
        "candidate_sha256": candidate.sha256,
        "evaluator_sha256": evaluator_hash,
        "report_path": str(report_path),
    }
    for field in RESULT_FIELDS:
        if field in out:
            continue
        value = row.get(field)
        if isinstance(value, float):
            out[field] = f"{value:.12g}"
        elif value is None:
            out[field] = ""
        else:
            out[field] = str(value)
    return out


def append_results_log(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, delimiter="\t")
        if needs_header:
            writer.writeheader()
        writer.writerows(rows)


def print_summary(metric_rows: list[dict[str, Any]], report_path: Path) -> None:
    print(f"Wrote report: {report_path}")
    print("")
    for dataset in ["first_signals", "polymarket_grid", "polymarket_clusters"]:
        print(f"== {dataset} ==")
        for split in ["train", "validation", "test"]:
            row = next((r for r in metric_rows if r["dataset"] == dataset and r["split"] == split and r["group"] == "ALL"), None)
            if row is None:
                continue
            extras = ""
            if dataset == "first_signals":
                extras = f" brier_yes={fmt(row['brier_yes'])} brier_side={fmt(row['brier_side'])}"
            else:
                extras = (
                    f" avg_entry={fmt(row['avg_entry'])}"
                    f" ci_low-entry={fmt(row['ci_low_minus_avg_entry'])}"
                    f" paper_roi={pct(row['paper_roi'])}"
                )
            print(
                f"{split:10s} allowed={row['allowed']}/{row['n']} "
                f"wins={row['wins']} losses={row['losses']} "
                f"win_rate={pct(row['win_rate'])} ci_low={pct(row['ci_low'])}{extras}"
            )
        print("")


def main() -> int:
    args = parse_args()
    candidate = load_candidate(Path(args.candidate).resolve())
    evaluator_hash = sha256_file(Path(__file__).resolve())
    timestamp_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "_" + candidate.name

    first = load_csv(Path(args.first_signals), args.max_rows)
    grid = load_csv(Path(args.grid_events), args.max_rows)
    metric_rows: list[dict[str, Any]] = []
    split_maps: dict[str, dict[str, str]] = {}

    if not first.empty:
        first, split_maps["first_signals"] = apply_session_splits(first, args.train_share, args.validation_share)
        metric_rows.extend(
            metric_rows_for_dataset(
                first,
                candidate,
                dataset="first_signals",
                group_col="symbol",
                min_group_n=args.min_group_n,
            )
        )
    if not grid.empty:
        grid, split_maps["polymarket_grid"] = apply_session_splits(grid, args.train_share, args.validation_share)
        metric_rows.extend(
            metric_rows_for_dataset(
                grid,
                candidate,
                dataset="polymarket_grid",
                group_col="asset",
                min_group_n=args.min_group_n,
            )
        )

    report = {
        "run_id": run_id,
        "timestamp_utc": timestamp_utc,
        "candidate": {
            "name": candidate.name,
            "description": candidate.description,
            "path": str(candidate.path),
            "sha256": candidate.sha256,
        },
        "evaluator": {
            "path": str(Path(__file__).resolve()),
            "sha256": evaluator_hash,
            "train_share": args.train_share,
            "validation_share": args.validation_share,
            "min_group_n": args.min_group_n,
        },
        "inputs": {
            "first_signals": str(Path(args.first_signals).resolve()),
            "grid_events": str(Path(args.grid_events).resolve()),
        },
        "session_splits": split_maps,
        "metrics": metric_rows,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{run_id}.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    if not args.no_log:
        log_rows = [
            compact_metric_row(
                row,
                run_id=run_id,
                timestamp_utc=timestamp_utc,
                candidate=candidate,
                evaluator_hash=evaluator_hash,
                report_path=report_path,
            )
            for row in metric_rows
        ]
        append_results_log(Path(args.results_log), log_rows)

    print_summary(metric_rows, report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
