#!/usr/bin/env python3
"""
XRP-only safety-first candidate for the Kou autoresearch harness.

This is the current safety-first generated entry curve narrowed to XRP. ETH is
blocked because recent validation shows ETH still needs a stricter entry-price
guard before it should be considered for paper or live sizing.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


CANDIDATE_NAME = "xrp_only_safety_first_v1"
CANDIDATE_DESCRIPTION = (
    "XRP-only paper candidate using clean prior-60s regime filter and train-only "
    "XRP entry caps from generated_entry_curve_safety_first_v1."
)

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
    return ENTRY_CAPS.get(side, {}).get(threshold_key, 1.0)


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
    threshold = _float(row.get("threshold"), 0.90)
    adverse_60s = _float(row.get("path_60s_adverse_share"), 0.0)
    z_change_60s = _float(row.get("path_60s_margin_z_change"), 999.0)
    time_left = _float(row.get("time_left_s"), 999.0)
    entry_price = _float(row.get("entry_price"))

    if asset != "xrp":
        return {"allow_trade": False, "reason": "eth_blocked_xrp_only"}

    if time_left is not None and time_left < 5.0:
        return {"allow_trade": False, "reason": "too_late_execution_risk"}

    if adverse_60s is not None and adverse_60s > 0.05:
        return {"allow_trade": False, "reason": "prior_60s_adverse_exposure"}

    if z_change_60s is not None and z_change_60s < 1.0:
        return {"allow_trade": False, "reason": "weak_prior_60s_margin_improvement"}

    cap = _entry_cap(side, threshold)
    if entry_price is not None and entry_price > cap:
        return {"allow_trade": False, "reason": f"entry_price_above_{cap:.2f}"}

    return {"allow_trade": True, "reason": "xrp_clean_path_entry_curve"}
