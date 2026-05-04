#!/usr/bin/env python3
"""
Paper inspector for Kou dashboard signals.

Watches the local /api/snapshot feed, records the first BUY_YES/BUY_NO signal
per asset/bucket, resolves the eventual outcome after expiry, and annotates
likely miss reasons such as late reversals, warm-model decisions, choppy tape,
or display/model source divergence.

Run alongside kou_dual_compact_web.py:
    python3 kou_signal_inspector.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    asset_name TEXT NOT NULL,
    bucket_end REAL NOT NULL,
    bucket_seconds INTEGER,
    decision_time REAL NOT NULL,
    decision TEXT NOT NULL,
    signal TEXT NOT NULL,
    decision_price REAL,
    strike REAL,
    kou_yes REAL,
    bs_yes REAL,
    edge_pp REAL,
    delta_bps REAL,
    time_left_s REAL,
    signal_hold_s REAL,
    trade_score INTEGER,
    trade_score_label TEXT,
    trade_score_reason TEXT,
    model TEXT,
    kou_phase TEXT,
    sample_count INTEGER,
    lam REAL,
    p_up REAL,
    sigma_model_bp_1m REAL,
    display_source TEXT,
    model_source TEXT,
    age_s REAL,
    model_age_s REAL,
    vol_30m_bp_1m REAL,
    vol_1h_bp_1m REAL,
    jump_10s_10m_rate REAL,
    jump_30s_15m_rate REAL,
    resolved_time REAL,
    final_price REAL,
    final_outcome TEXT,
    is_correct INTEGER,
    miss_reason TEXT,
    miss_notes TEXT,
    price_path_json TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_inspections_symbol_bucket
ON inspections(symbol, bucket_end);
"""


@dataclass
class ActiveDecision:
    row_id: int
    symbol: str
    asset_name: str
    bucket_end: float
    decision: str
    strike: float | None
    decision_time: float
    last_snapshot: dict[str, Any] = field(default_factory=dict)
    price_points: list[tuple[float, float]] = field(default_factory=list)


def fetch_snapshot(base_url: str) -> dict[str, Any] | None:
    url = f"{base_url.rstrip('/')}/api/snapshot"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def coerce_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def coerce_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def setup_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(DB_SCHEMA)
    conn.commit()
    return conn


def load_existing_keys(conn: sqlite3.Connection) -> set[tuple[str, float]]:
    rows = conn.execute("SELECT symbol, bucket_end FROM inspections").fetchall()
    return {(str(symbol), float(bucket_end)) for symbol, bucket_end in rows}


def insert_decision(
    conn: sqlite3.Connection,
    asset: dict[str, Any],
    *,
    bucket_seconds: int | None,
    decision_time: float,
) -> int:
    decision = "YES" if asset["signal"] == "BUY_YES" else "NO"
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO inspections (
            symbol, asset_name, bucket_end, bucket_seconds, decision_time,
            decision, signal, decision_price, strike, kou_yes, bs_yes, edge_pp,
            delta_bps, time_left_s, signal_hold_s, trade_score, trade_score_label,
            trade_score_reason, model, kou_phase, sample_count, lam, p_up,
            sigma_model_bp_1m, display_source, model_source, age_s, model_age_s,
            vol_30m_bp_1m, vol_1h_bp_1m, jump_10s_10m_rate, jump_30s_15m_rate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            asset["symbol"],
            asset.get("name") or asset["symbol"].upper(),
            coerce_float(asset.get("bucket_end")),
            bucket_seconds,
            decision_time,
            decision,
            asset["signal"],
            coerce_float(asset.get("price")),
            coerce_float(asset.get("strike")),
            coerce_float(asset.get("kou_yes")),
            coerce_float(asset.get("bs_yes")),
            coerce_float(asset.get("edge_pp")),
            coerce_float(asset.get("delta_bps")),
            coerce_float(asset.get("time_left_s")),
            coerce_float(asset.get("signal_hold_s")),
            coerce_int(asset.get("trade_score")),
            asset.get("trade_score_label"),
            asset.get("trade_score_reason"),
            asset.get("model"),
            asset.get("kou_phase"),
            coerce_int(asset.get("sample_count")),
            coerce_float(asset.get("lam")),
            coerce_float(asset.get("p_up")),
            coerce_float(asset.get("sigma_model_bp_1m")),
            asset.get("display_source"),
            asset.get("model_source"),
            coerce_float(asset.get("age_s")),
            coerce_float(asset.get("model_age_s")),
            coerce_float(asset.get("vol_30m_bp_1m")),
            coerce_float(asset.get("vol_1h_bp_1m")),
            coerce_float(asset.get("jump_10s_10m_rate")),
            coerce_float(asset.get("jump_30s_15m_rate")),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def nearest_price(points: list[tuple[float, float]], target_ts: float) -> float | None:
    if not points:
        return None
    return min(points, key=lambda item: abs(item[0] - target_ts))[1]


def crossed_late(decision: str, strike: float, points: list[tuple[float, float]], bucket_end: float) -> bool:
    if not points:
        return False
    winning_side = (lambda p: p >= strike) if decision == "YES" else (lambda p: p < strike)
    cross_ts = None
    for ts, price in points:
        if winning_side(price):
            cross_ts = ts
            break
    if cross_ts is None:
        return False
    return (bucket_end - cross_ts) <= 45.0


def classify_miss(row: sqlite3.Row, points: list[tuple[float, float]]) -> tuple[str, str]:
    reasons: list[str] = []

    strike = coerce_float(row["strike"])
    if strike is not None and strike > 0.0 and crossed_late(str(row["decision"]), strike, points, float(row["bucket_end"])):
        reasons.append("late_reversal")
    if str(row["kou_phase"] or "") != "full":
        reasons.append("warm_model")
    if str(row["trade_score_label"] or "") in {"CAREFUL", "AVOID"}:
        reasons.append("poor_regime")
    if (row["display_source"] or "") != (row["model_source"] or ""):
        reasons.append("source_split")
    if (coerce_float(row["jump_10s_10m_rate"]) or 0.0) >= 0.08 or (coerce_float(row["jump_30s_15m_rate"]) or 0.0) >= 0.08:
        reasons.append("choppy")
    if (coerce_float(row["signal_hold_s"]) or 0.0) < 4.5:
        reasons.append("short_persistence")
    kou_yes = coerce_float(row["kou_yes"])
    if kou_yes is not None:
        if row["decision"] == "YES" and kou_yes < 0.95:
            reasons.append("thin_confidence")
        if row["decision"] == "NO" and kou_yes > 0.05:
            reasons.append("thin_confidence")
    if (coerce_float(row["model_age_s"]) or 0.0) > 2.5 or (coerce_float(row["age_s"]) or 0.0) > 2.5:
        reasons.append("staleish")
    if not reasons:
        reasons.append("unclear")
    return reasons[0], ",".join(reasons)


def resolve_decision(conn: sqlite3.Connection, active: ActiveDecision, now_ts: float) -> None:
    final_price = nearest_price(active.price_points, active.bucket_end)
    if final_price is None and active.last_snapshot:
        final_price = coerce_float(active.last_snapshot.get("price"))

    final_outcome = None
    is_correct = None
    miss_reason = None
    miss_notes = None
    if final_price is not None and active.strike is not None:
        final_outcome = "YES" if final_price >= active.strike else "NO"
        is_correct = 1 if final_outcome == active.decision else 0

    if is_correct == 0:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM inspections WHERE id = ?", (active.row_id,)).fetchone()
        assert row is not None
        miss_reason, miss_notes = classify_miss(row, active.price_points)
        conn.row_factory = None

    conn.execute(
        """
        UPDATE inspections
        SET resolved_time = ?, final_price = ?, final_outcome = ?,
            is_correct = ?, miss_reason = ?, miss_notes = ?, price_path_json = ?
        WHERE id = ?
        """,
        (
            now_ts,
            final_price,
            final_outcome,
            is_correct,
            miss_reason,
            miss_notes,
            json.dumps(active.price_points),
            active.row_id,
        ),
    )
    conn.commit()

    verdict = "correct" if is_correct == 1 else "wrong" if is_correct == 0 else "unresolved"
    reason_text = f" | {miss_reason}" if miss_reason else ""
    strike_text = "-" if active.strike is None else f"{active.strike:.6f}"
    final_text = "-" if final_price is None else f"{final_price:.6f}"
    print(
        f"{active.asset_name:>4} {time.strftime('%H:%M:%S', time.localtime(active.bucket_end))} "
        f"{active.decision:<3} {verdict:<9} strike {strike_text} final {final_text}{reason_text}"
    )


def print_summary(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT symbol, COUNT(*) AS n, SUM(is_correct = 1) AS correct, SUM(is_correct = 0) AS wrong
        FROM inspections
        WHERE resolved_time IS NOT NULL
        GROUP BY symbol
        ORDER BY symbol
        """
    ).fetchall()
    if not rows:
        return
    parts = []
    for symbol, n, correct, wrong in rows:
        n = int(n or 0)
        correct = int(correct or 0)
        wrong = int(wrong or 0)
        hit = 100.0 * correct / n if n else 0.0
        parts.append(f"{str(symbol).upper()}: {correct}/{n} ({hit:.1f}%)")
        if wrong:
            worst = conn.execute(
                """
                SELECT miss_reason, COUNT(*)
                FROM inspections
                WHERE symbol = ? AND is_correct = 0
                GROUP BY miss_reason
                ORDER BY COUNT(*) DESC, miss_reason
                LIMIT 1
                """,
                (symbol,),
            ).fetchone()
            if worst and worst[0]:
                parts[-1] += f" worst={worst[0]}"
    print("summary | " + " | ".join(parts))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspector for Kou dashboard signals")
    parser.add_argument("--url", default="http://127.0.0.1:8071", help="Base dashboard URL")
    parser.add_argument("--db", default="kou_signal_inspector.sqlite3", help="SQLite output path")
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="Snapshot poll cadence")
    parser.add_argument("--resolve-grace", type=float, default=2.0, help="Seconds to wait after expiry before resolving")
    parser.add_argument("--summary-every", type=float, default=60.0, help="Print summary cadence in seconds")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    conn = setup_db(Path(args.db))
    existing_keys = load_existing_keys(conn)
    active: dict[tuple[str, float], ActiveDecision] = {}
    last_summary = 0.0

    try:
        while True:
            now_ts = time.time()
            payload = fetch_snapshot(args.url)
            if payload is None:
                time.sleep(max(0.2, float(args.poll_seconds)))
                continue

            bucket_seconds = coerce_int(payload.get("bucket_seconds"))
            seen_current_keys: set[tuple[str, float]] = set()

            for asset in payload.get("assets", []):
                symbol = str(asset.get("symbol") or "").lower()
                bucket_end = coerce_float(asset.get("bucket_end"))
                price = coerce_float(asset.get("price"))
                if not symbol or bucket_end is None:
                    continue

                key = (symbol, bucket_end)
                seen_current_keys.add(key)

                if asset.get("signal") in {"BUY_YES", "BUY_NO"} and key not in existing_keys:
                    row_id = insert_decision(conn, asset, bucket_seconds=bucket_seconds, decision_time=now_ts)
                    if row_id:
                        existing_keys.add(key)
                        active[key] = ActiveDecision(
                            row_id=row_id,
                            symbol=symbol,
                            asset_name=str(asset.get("name") or symbol.upper()),
                            bucket_end=bucket_end,
                            decision="YES" if asset["signal"] == "BUY_YES" else "NO",
                            strike=coerce_float(asset.get("strike")),
                            decision_time=now_ts,
                            last_snapshot=dict(asset),
                        )
                        print(
                            f"armed   {str(asset.get('name') or symbol.upper()):>4} "
                            f"{time.strftime('%H:%M:%S', time.localtime(bucket_end))} "
                            f"{active[key].decision:<3} kou {asset.get('kou_yes')} score {asset.get('trade_score')}"
                        )

                if key in active and price is not None:
                    active[key].price_points.append((now_ts, price))
                    active[key].last_snapshot = dict(asset)

            for key, decision in list(active.items()):
                if now_ts < decision.bucket_end + float(args.resolve_grace):
                    continue
                hard_timeout = now_ts >= decision.bucket_end + 30.0
                if key in seen_current_keys and not hard_timeout:
                    # Still same bucket on current snapshot; wait one more poll.
                    continue
                resolve_decision(conn, decision, now_ts)
                del active[key]

            if now_ts - last_summary >= float(args.summary_every):
                print_summary(conn)
                last_summary = now_ts

            time.sleep(max(0.2, float(args.poll_seconds)))
    except KeyboardInterrupt:
        print("Stopped inspector.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
