#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import statistics as st
import sys
from collections import Counter
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - k) + values[hi] * (k - lo)


def stats(values: list[float]) -> dict:
    values = [v for v in values if isinstance(v, (int, float)) and not math.isnan(v)]
    if not values:
        return {}
    return {
        "n": len(values),
        "mean": round(st.mean(values), 4),
        "median": round(pct(values, 0.5), 4),
        "p95": round(pct(values, 0.95), 4),
        "p99": round(pct(values, 0.99), 4),
        "max": round(max(values), 4),
    }


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: summarize_session.py <session_dir>", file=sys.stderr)
        return 2
    session = Path(sys.argv[1])
    quotes = load_jsonl(session / "polymarket_quotes.jsonl")
    orders = load_jsonl(session / "shadow_orders.jsonl")
    settlements = load_jsonl(session / "shadow_order_settlements.jsonl")
    signals = load_jsonl(session / "sniper_signals.jsonl")
    plans = load_jsonl(session / "sniper_plans.jsonl")
    ledger = load_jsonl(session / "sniper_live_ledger.jsonl")

    print(f"session_dir: {session}")
    print(f"quote_rows: {len(quotes)}")
    print(f"shadow_orders: {len(orders)}")
    print(f"settlements: {len(settlements)}")
    print(f"sniper_signals: {len(signals)}")
    print(f"sniper_plans: {len(plans)}")
    print(f"sniper_ledger_rows: {len(ledger)}")

    if quotes:
        source_ages = [r.get("kou_ref", {}).get("source_age_s") for r in quotes]
        quote_lats = [r.get("quote_fetch", {}).get("latency_s") for r in quotes]
        alignments = Counter(r.get("polymarket_market", {}).get("alignment_status") for r in quotes)
        sources = sorted({r.get("kou_ref", {}).get("model_source") for r in quotes if r.get("kou_ref", {}).get("model_source")})
        print(f"sources: {sources}")
        print(f"source_age: {json.dumps(stats(source_ages), sort_keys=True)}")
        print(f"quote_latency: {json.dumps(stats(quote_lats), sort_keys=True)}")
        print(f"alignments: {dict(alignments)}")
        stale_10s = sum(1 for x in source_ages if isinstance(x, (int, float)) and x > 10)
        print(f"source_age_gt_10s: {stale_10s}")

    if settlements:
        wins = [bool(r.get("result", {}).get("win")) for r in settlements if r.get("result", {}).get("known_outcome")]
        pnl = sum(float(r.get("result", {}).get("paper_pnl_requested_size") or 0.0) for r in settlements)
        print(f"settled_wins: {sum(wins)}")
        print(f"settled_losses: {len(wins) - sum(wins)}")
        print(f"paper_pnl_requested_size: {round(pnl, 6)}")

    if plans:
        reasons = Counter(r.get("plan", {}).get("reason") for r in plans)
        allows = Counter(r.get("plan", {}).get("allow_submit") for r in plans)
        print(f"sniper_plan_reasons: {dict(reasons)}")
        print(f"sniper_plan_allow_submit: {dict(allows)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
