#!/usr/bin/env python3
"""Replay-only ultra-safe candidate plus conservative late-window near-strike veto."""

from __future__ import annotations

from typing import Any, Mapping

from analysis.autoresearch_kou.candidates import xrp_only_ultra_safe_v1 as base
from analysis.autoresearch_kou.candidates._near_strike_veto import near_strike_veto


CANDIDATE_NAME = "xrp_only_ultra_safe_v1_near_strike_conservative"
CANDIDATE_DESCRIPTION = (
    "Replay-only ultra-safe plus hard XRP near-strike veto: 30s<0.0004, 10s<0.0006."
)


def score_first_signal(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(base.score_first_signal(row))


def score_grid_event(row: Mapping[str, Any]) -> dict[str, Any]:
    decision = dict(base.score_grid_event(row))
    if not bool(decision.get("allow_trade")):
        return decision
    veto = near_strike_veto(row, threshold_30s=0.0004, threshold_10s=0.0006)
    if veto is not None:
        return veto
    decision["reason"] = f"{decision.get('reason')}_near_strike_conservative_clear"
    return decision
