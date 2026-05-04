"""Shared executable-quote and stale-source guards for shadow candidates."""

from __future__ import annotations

import math
from typing import Any, Callable, Mapping


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) or math.isinf(number) else number


def _entry_cap(
    row: Mapping[str, Any],
    entry_cap_fn: Callable[[str, float | None], float] | None,
    fallback: float,
) -> float:
    side = str(row.get("side") or "").lower()
    threshold = _float(row.get("threshold"))
    if entry_cap_fn is None:
        return fallback
    try:
        return float(entry_cap_fn(side, threshold))
    except Exception:
        return fallback


def executable_quote_veto(
    row: Mapping[str, Any],
    *,
    max_source_age_s: float = 3.0,
    max_book_endpoint_delta: float = 0.03,
    fallback_entry_cap: float = 0.98,
    entry_cap_fn: Callable[[str, float | None], float] | None = None,
) -> dict[str, Any] | None:
    source_age = _float(row.get("source_age_s"))
    model_age = _float(row.get("model_age_s"))
    if source_age is None:
        return {"allow_trade": False, "reason": "missing_source_age_for_exec_guard"}
    if model_age is None:
        return {"allow_trade": False, "reason": "missing_model_age_for_exec_guard"}

    max_age = max(source_age, model_age)
    if max_age > max_source_age_s:
        return {"allow_trade": False, "reason": f"source_stale_age_{max_age:.1f}s"}

    fill_status = str(row.get("fill_status") or "").lower()
    if fill_status != "full":
        reason = fill_status or "missing"
        return {"allow_trade": False, "reason": f"non_executable_fill_status_{reason}"}

    book_ask = _float(row.get("book_ask_price"))
    if book_ask is None or book_ask <= 0.0:
        return {"allow_trade": False, "reason": "missing_book_ask_for_exec_guard"}

    book_ask_size = _float(row.get("book_ask_size"))
    requested_size = _float(row.get("requested_size"), 5.0)
    if book_ask_size is None:
        return {"allow_trade": False, "reason": "missing_book_ask_size_for_exec_guard"}
    if requested_size is not None and book_ask_size < requested_size:
        return {
            "allow_trade": False,
            "reason": f"visible_ask_size_below_{requested_size:.2f}",
        }

    cap = _entry_cap(row, entry_cap_fn, fallback_entry_cap)
    if book_ask > cap:
        return {"allow_trade": False, "reason": f"book_ask_above_exec_cap_{cap:.2f}"}

    endpoint_buy = _float(row.get("endpoint_buy_price"))
    if endpoint_buy is not None:
        book_endpoint_delta = book_ask - endpoint_buy
        if book_endpoint_delta > max_book_endpoint_delta:
            return {
                "allow_trade": False,
                "reason": f"book_endpoint_delta_above_{max_book_endpoint_delta:.2f}",
            }

    return None
