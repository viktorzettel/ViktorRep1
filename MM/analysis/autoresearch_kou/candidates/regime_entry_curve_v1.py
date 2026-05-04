#!/usr/bin/env python3
"""
Balanced asset/threshold-specific entry curve candidate.

This keeps the same prior-60s regime skip as regime_skip_v1, then applies a
small entry-price curve instead of one global cap. The goal is to reduce
expensive tail-risk entries without collapsing the sample size.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


CANDIDATE_NAME = "regime_entry_curve_v1"
CANDIDATE_DESCRIPTION = (
    "Clean prior-60s path plus asset/threshold-specific entry caps: "
    "ETH <=98c at low thresholds and <=99c at high thresholds; "
    "XRP <=98c at low thresholds and no cap at high thresholds."
)


ENTRY_CAPS = {
    "eth": {
        0.90: 0.98,
        0.91: 0.98,
        0.92: 0.98,
        0.93: 0.98,
        0.94: 0.99,
        0.95: 0.99,
        0.96: 0.99,
    },
    "xrp": {
        0.90: 0.98,
        0.91: 0.98,
        0.92: 0.98,
        0.93: 0.98,
        0.94: 1.00,
        0.95: 1.00,
        0.96: 1.00,
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


def _entry_cap(asset: str, threshold: float | None) -> float:
    rounded_threshold = 0.90 if threshold is None else round(float(threshold), 2)
    return ENTRY_CAPS.get(asset, {}).get(rounded_threshold, 1.00)


def score_first_signal(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "allow_trade": True,
        "adjusted_prob_yes": _float(row.get("kou_yes"), _float(row.get("bs_yes"))),
        "reason": "first_signal_unchanged",
    }


def score_grid_event(row: Mapping[str, Any]) -> dict[str, Any]:
    asset = str(row.get("asset") or "").lower()
    threshold = _float(row.get("threshold"), 0.90)
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

    cap = _entry_cap(asset, threshold)
    if entry_price is not None and entry_price > cap:
        return {"allow_trade": False, "reason": f"entry_price_above_{cap:.2f}"}

    return {"allow_trade": True, "reason": "clean_path_and_entry_curve"}
