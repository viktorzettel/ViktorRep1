#!/usr/bin/env python3
"""
Active editable candidate policy for the Kou autoresearch harness.

The current active research direction is XRP-only forward validation. Keep ETH
blocked until a separate ETH entry-price guard is validated. Missing live
clean-path context is treated as a skip, not as safe.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


_CANDIDATE_PATH = Path(__file__).resolve().parent / "candidates" / "xrp_only_cap98_required_context_v2.py"
_SPEC = importlib.util.spec_from_file_location("xrp_only_cap98_v1", _CANDIDATE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"Could not load active candidate: {_CANDIDATE_PATH}")

_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

CANDIDATE_DESCRIPTION = _MODULE.CANDIDATE_DESCRIPTION
CANDIDATE_NAME = _MODULE.CANDIDATE_NAME
score_first_signal = _MODULE.score_first_signal
score_grid_event = _MODULE.score_grid_event


__all__ = [
    "CANDIDATE_DESCRIPTION",
    "CANDIDATE_NAME",
    "score_first_signal",
    "score_grid_event",
]
