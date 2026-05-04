#!/usr/bin/env python3
"""
Selective reversal-regime skip candidate with a simple entry-price guard.

This extends regime_skip_v1 by refusing expensive observed taker entries. It is
only an offline research candidate, not production trading logic.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


CANDIDATE_NAME = "regime_skip_entry_v1"
CANDIDATE_DESCRIPTION = (
    "Skip unclean prior-60s paths and observed entries above 96c."
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
    entry_price = _float(row.get("entry_price"))

    if time_left is not None and time_left < 5.0:
        return {"allow_trade": False, "reason": "too_late_execution_risk"}

    if adverse_60s is not None and adverse_60s > 0.05:
        return {"allow_trade": False, "reason": "prior_60s_adverse_exposure"}

    if z_change_60s is not None and z_change_60s < 1.0:
        return {"allow_trade": False, "reason": "weak_prior_60s_margin_improvement"}

    if entry_price is not None and entry_price > 0.96:
        return {"allow_trade": False, "reason": "entry_price_above_96c"}

    return {"allow_trade": True, "reason": "clean_path_and_price"}
