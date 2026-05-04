#!/usr/bin/env python3
"""
Pre-registered ultra-safe XRP-only shadow candidate.

This candidate is intentionally more selective than xrp_only_cap98_required_context_v2.
It was motivated before the 20260430T214102Z fresh 6h shadow session and then
checked on that session as a strict subset rule.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


CANDIDATE_NAME = "xrp_only_ultra_safe_v1"
CANDIDATE_DESCRIPTION = (
    "XRP-only ultra-safe challenger: required clean-path context, global max "
    "entry 0.98, stronger 60s margin improvement, and safety_score >= 87."
)
MAX_ENTRY_PRICE = 0.98
MIN_60S_MARGIN_Z_CHANGE = 1.25
MIN_SAFETY_SCORE = 87.0

ENTRY_CAPS = {
    "no": {
        "0.90": 0.94,
        "0.91": 0.99,
        "0.92": 1.0,
        "0.93": 1.0,
        "0.94": 0.99,
        "0.95": 0.99,
        "0.96": 1.0,
    },
    "yes": {
        "0.90": 0.94,
        "0.91": 0.94,
        "0.92": 0.96,
        "0.93": 0.96,
        "0.94": 0.96,
        "0.95": 1.0,
        "0.96": 1.0,
    },
}


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) or math.isinf(number) else number


def _entry_cap(side: str, threshold: float | None) -> float:
    threshold_key = f"{0.90 if threshold is None else round(float(threshold), 2):.2f}"
    trained_cap = ENTRY_CAPS.get(side, {}).get(threshold_key, 1.0)
    return min(trained_cap, MAX_ENTRY_PRICE)


def _is_xrp_signal(row: Mapping[str, Any]) -> bool:
    symbol = str(row.get("symbol") or row.get("asset") or "").lower()
    return symbol in {"xrp", "xrpusdt", "xrp-usd"}


def score_first_signal(row: Mapping[str, Any]) -> dict[str, Any]:
    if not _is_xrp_signal(row):
        return {
            "allow_trade": False,
            "adjusted_prob_yes": _float(row.get("kou_yes"), _float(row.get("bs_yes"))),
            "reason": "eth_blocked_xrp_only",
        }

    return {
        "allow_trade": True,
        "adjusted_prob_yes": _float(row.get("kou_yes"), _float(row.get("bs_yes"))),
        "reason": "xrp_first_signal_allowed",
    }


def score_grid_event(row: Mapping[str, Any]) -> dict[str, Any]:
    asset = str(row.get("asset") or "").lower()
    side = str(row.get("side") or "").lower()
    threshold = _float(row.get("threshold"))
    adverse_60s = _float(row.get("path_60s_adverse_share"))
    z_change_60s = _float(row.get("path_60s_margin_z_change"))
    safety_score = _float(row.get("safety_score"))
    time_left = _float(row.get("time_left_s"))
    entry_price = _float(row.get("entry_price"))

    if asset != "xrp":
        return {"allow_trade": False, "reason": "eth_blocked_xrp_only"}

    if side not in {"yes", "no"}:
        return {"allow_trade": False, "reason": "missing_or_invalid_side"}

    if threshold is None:
        return {"allow_trade": False, "reason": "missing_threshold"}

    if time_left is None:
        return {"allow_trade": False, "reason": "missing_time_left"}
    if time_left < 5.0:
        return {"allow_trade": False, "reason": "too_late_execution_risk"}

    if adverse_60s is None:
        return {"allow_trade": False, "reason": "missing_prior_60s_adverse_exposure"}
    if adverse_60s > 0.05:
        return {"allow_trade": False, "reason": "prior_60s_adverse_exposure"}

    if z_change_60s is None:
        return {"allow_trade": False, "reason": "missing_prior_60s_margin_improvement"}
    if z_change_60s < MIN_60S_MARGIN_Z_CHANGE:
        return {"allow_trade": False, "reason": "insufficient_ultra_safe_60s_margin_improvement"}

    if safety_score is None:
        return {"allow_trade": False, "reason": "missing_safety_score"}
    if safety_score < MIN_SAFETY_SCORE:
        return {"allow_trade": False, "reason": "insufficient_ultra_safe_safety_score"}

    if entry_price is None:
        return {"allow_trade": False, "reason": "missing_entry_price"}

    cap = _entry_cap(side, threshold)
    if entry_price > cap:
        return {"allow_trade": False, "reason": f"entry_price_above_{cap:.2f}"}

    return {"allow_trade": True, "reason": "xrp_ultra_safe_clean_path_entry_cap98"}
