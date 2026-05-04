#!/usr/bin/env python3
"""
Selective reversal-regime skip candidate.

This is a capital-preservation experiment based on
data/live_capture/forensic_analysis/reversal_regimes. It leaves the first
signal layer unchanged and tests whether a strict pre-trigger cleanliness rule
improves Polymarket cluster outcomes.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


CANDIDATE_NAME = "regime_skip_v1"
CANDIDATE_DESCRIPTION = (
    "Skip grid entries unless the prior 60s path is clean: adverse share <= 5%, "
    "margin-z improved by at least 1, and at least 5s remain."
)


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) or math.isinf(number) else number


def score_first_signal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "allow_trade": True,
        "adjusted_prob_yes": _float(row.get("kou_yes"), _float(row.get("bs_yes"))),
        "reason": "first_signal_unchanged",
    }


def score_grid_event(row: Mapping[str, Any]) -> dict[str, Any]:
    adverse_60s = _float(row.get("path_60s_adverse_share"), 0.0)
    z_change_60s = _float(row.get("path_60s_margin_z_change"), 999.0)
    time_left = _float(row.get("time_left_s"), 999.0)

    if time_left is not None and time_left < 5.0:
        return {"allow_trade": False, "reason": "too_late_execution_risk"}

    if adverse_60s is not None and adverse_60s > 0.05:
        return {"allow_trade": False, "reason": "prior_60s_adverse_exposure"}

    if z_change_60s is not None and z_change_60s < 1.0:
        return {"allow_trade": False, "reason": "weak_prior_60s_margin_improvement"}

    return {"allow_trade": True, "reason": "clean_prior_60s_path"}
