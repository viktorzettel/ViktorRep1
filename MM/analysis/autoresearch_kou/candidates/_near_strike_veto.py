"""Shared late-window XRP near-strike hard veto for replay candidates."""

from __future__ import annotations

import math
from typing import Any, Mapping


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def near_strike_veto(
    row: Mapping[str, Any],
    *,
    threshold_30s: float,
    threshold_10s: float,
) -> dict[str, Any] | None:
    """Return a skip decision when a late XRP event is too close to strike."""

    asset = str(row.get("asset") or row.get("symbol") or "").lower()
    if "xrp" not in asset:
        return None

    time_left = _float(row.get("time_left_s"))
    if time_left is None or time_left > 30.0:
        return None

    price = _float(row.get("price"))
    strike = _float(row.get("strike"))
    if price is None or strike is None:
        return {
            "allow_trade": False,
            "reason": "missing_price_or_strike_for_near_strike_veto",
        }

    threshold = threshold_10s if time_left <= 10.0 else threshold_30s
    distance = abs(price - strike)
    if distance < threshold:
        window = "10s" if time_left <= 10.0 else "30s"
        return {
            "allow_trade": False,
            "reason": f"near_strike_{window}_hard_veto_dist_{distance:.6f}_lt_{threshold:.6f}",
        }
    return None
