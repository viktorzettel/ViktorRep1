#!/usr/bin/env python3
"""Ultra-safe near-strike candidate plus stale-source and executable-quote guard."""

from __future__ import annotations

from typing import Any, Mapping

from analysis.autoresearch_kou.candidates import xrp_only_ultra_safe_v1 as ultra_base
from analysis.autoresearch_kou.candidates import xrp_only_ultra_safe_v1_near_strike_conservative as base
from analysis.autoresearch_kou.candidates._execution_guards import executable_quote_veto


CANDIDATE_NAME = "xrp_only_ultra_safe_v1_near_strike_exec_guard"
CANDIDATE_DESCRIPTION = (
    "Ultra-safe plus conservative near-strike veto, stale-source veto, and "
    "book-visible executable quote guard."
)
MAX_SOURCE_AGE_S = 3.0
MAX_BOOK_ENDPOINT_DELTA = 0.03


def score_first_signal(row: Mapping[str, Any]) -> dict[str, Any]:
    return dict(base.score_first_signal(row))


def score_grid_event(row: Mapping[str, Any]) -> dict[str, Any]:
    decision = dict(base.score_grid_event(row))
    if not bool(decision.get("allow_trade")):
        return decision

    veto = executable_quote_veto(
        row,
        max_source_age_s=MAX_SOURCE_AGE_S,
        max_book_endpoint_delta=MAX_BOOK_ENDPOINT_DELTA,
        fallback_entry_cap=ultra_base.MAX_ENTRY_PRICE,
        entry_cap_fn=ultra_base._entry_cap,
    )
    if veto is not None:
        return veto

    decision["execution_entry_price"] = row.get("book_ask_price")
    decision["execution_entry_price_source"] = "book_ask_exec_guard"
    decision["reason"] = f"{decision.get('reason')}_exec_guard_clear"
    return decision
