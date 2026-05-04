#!/usr/bin/env python3
"""
Exploratory cluster-aware veto candidate.

This candidate is deliberately conservative in scope: it leaves the first
signal layer unchanged and only tests a few Polymarket-grid vetoes that showed
some loss density in the current forensic analysis.
"""

from __future__ import annotations

from typing import Any, Mapping


CANDIDATE_NAME = "cluster_guard_v1"
CANDIDATE_DESCRIPTION = (
    "Veto low-margin late low-threshold grid candidates plus final-seconds "
    "cross/adverse traps; leave first-signal probability unchanged."
)


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def score_first_signal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "allow_trade": True,
        "adjusted_prob_yes": _float(row.get("kou_yes"), _float(row.get("bs_yes"))),
        "reason": "first_signal_unchanged",
    }


def score_grid_event(row: Mapping[str, Any]) -> dict[str, Any]:
    asset = str(row.get("asset") or "").lower()
    threshold = _float(row.get("threshold"), 1.0)
    time_left = _float(row.get("time_left_s"), 999.0)
    margin_z = _float(row.get("policy_margin_z"), 999.0)
    cross30 = _float(row.get("path_30s_cross_count"), 0.0) or 0.0
    adverse30 = _float(row.get("path_30s_adverse_share"), 0.0) or 0.0

    if threshold is None or time_left is None or margin_z is None:
        return {"allow_trade": True, "reason": "missing_context_allow"}

    if time_left <= 15.0 and threshold <= 0.91 and margin_z < 2.1:
        return {"allow_trade": False, "reason": "late_low_threshold_low_margin"}

    if asset == "xrp" and threshold <= 0.91 and margin_z < 2.0:
        return {"allow_trade": False, "reason": "xrp_low_threshold_low_margin"}

    if time_left <= 5.0 and cross30 >= 1.0 and adverse30 > 0.50:
        return {"allow_trade": False, "reason": "final_cross_adverse"}

    return {"allow_trade": True, "reason": "allow"}

