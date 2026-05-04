from pathlib import Path
from types import ModuleType

import pandas as pd

from analysis.autoresearch_kou.evaluate_candidate import (
    Candidate,
    evaluate_first_signals,
    evaluate_grid_clusters,
    evaluate_grid_events,
)


def _candidate() -> Candidate:
    def score_first_signal(row):
        return {
            "allow_trade": float(row.get("margin_z") or 0.0) >= 2.0,
            "adjusted_prob_yes": row.get("kou_yes"),
        }

    def score_grid_event(row):
        return {"allow_trade": float(row.get("policy_margin_z") or 0.0) >= 2.0}

    return Candidate(
        name="test_margin_veto",
        description="test",
        path=Path("candidate.py"),
        sha256="test",
        module=ModuleType("candidate"),
        score_first_signal=score_first_signal,
        score_grid_event=score_grid_event,
    )


def test_first_signal_veto_accounting() -> None:
    df = pd.DataFrame(
        [
            {
                "session_id": "s1",
                "win": True,
                "margin_z": 3.0,
                "kou_yes": 0.95,
                "settled_yes_num": 1.0,
                "signal_decision_yes": True,
            },
            {
                "session_id": "s1",
                "win": False,
                "margin_z": 1.2,
                "kou_yes": 0.94,
                "settled_yes_num": 0.0,
                "signal_decision_yes": True,
            },
            {
                "session_id": "s2",
                "win": True,
                "margin_z": 2.1,
                "kou_yes": 0.08,
                "settled_yes_num": 0.0,
                "signal_decision_yes": False,
            },
        ]
    )

    result = evaluate_first_signals(df, _candidate(), split_name="test", group="ALL")

    assert result["n"] == 3
    assert result["allowed"] == 2
    assert result["wins"] == 2
    assert result["losses_avoided"] == 1
    assert result["winners_blocked"] == 0


def test_grid_veto_accounting() -> None:
    df = pd.DataFrame(
        [
            {
                "session_id": "s1",
                "known_outcome": True,
                "win": True,
                "policy_margin_z": 3.0,
                "entry_price": 0.90,
                "pnl_per_share": 0.10,
                "fill_status": "full",
                "estimated_cost": 0.90,
                "estimated_pnl": 0.10,
            },
            {
                "session_id": "s1",
                "known_outcome": True,
                "win": False,
                "policy_margin_z": 1.1,
                "entry_price": 0.93,
                "pnl_per_share": -0.93,
                "fill_status": "full",
                "estimated_cost": 0.93,
                "estimated_pnl": -0.93,
            },
            {
                "session_id": "s2",
                "known_outcome": True,
                "win": True,
                "policy_margin_z": 2.2,
                "entry_price": 0.92,
                "pnl_per_share": 0.08,
                "fill_status": "full",
                "estimated_cost": 0.92,
                "estimated_pnl": 0.08,
            },
        ]
    )

    result = evaluate_grid_events(df, _candidate(), split_name="test", group="ALL")

    assert result["n"] == 3
    assert result["allowed"] == 2
    assert result["wins"] == 2
    assert result["losses_avoided"] == 1
    assert result["winners_blocked"] == 0
    assert result["paper_pnl"] == 0.18


def test_grid_cluster_veto_accounting_counts_bucket_once() -> None:
    df = pd.DataFrame(
        [
            {
                "session_id": "s1",
                "asset": "eth",
                "bucket_end": 100.0,
                "side": "yes",
                "captured_at_iso": "2026-01-01T00:00:01Z",
                "threshold": 0.90,
                "hold_seconds": 2,
                "known_outcome": True,
                "win": False,
                "policy_margin_z": 1.1,
                "entry_price": 0.93,
                "pnl_per_share": -0.93,
                "fill_status": "full",
                "estimated_cost": 0.93,
                "estimated_pnl": -0.93,
            },
            {
                "session_id": "s1",
                "asset": "eth",
                "bucket_end": 100.0,
                "side": "yes",
                "captured_at_iso": "2026-01-01T00:00:02Z",
                "threshold": 0.91,
                "hold_seconds": 2,
                "known_outcome": True,
                "win": False,
                "policy_margin_z": 1.2,
                "entry_price": 0.94,
                "pnl_per_share": -0.94,
                "fill_status": "full",
                "estimated_cost": 0.94,
                "estimated_pnl": -0.94,
            },
            {
                "session_id": "s2",
                "asset": "xrp",
                "bucket_end": 200.0,
                "side": "no",
                "captured_at_iso": "2026-01-01T00:05:01Z",
                "threshold": 0.90,
                "hold_seconds": 2,
                "known_outcome": True,
                "win": True,
                "policy_margin_z": 3.0,
                "entry_price": 0.90,
                "pnl_per_share": 0.10,
                "fill_status": "full",
                "estimated_cost": 0.90,
                "estimated_pnl": 0.10,
            },
        ]
    )

    result = evaluate_grid_clusters(df, _candidate(), split_name="test", group="ALL")

    assert result["n"] == 2
    assert result["allowed"] == 1
    assert result["wins"] == 1
    assert result["losses"] == 0
    assert result["losses_avoided"] == 1
    assert result["winners_blocked"] == 0
