#!/usr/bin/env python3
"""
Print the current ETH/XRP 5-minute Polymarket markets and Kou alignment.

This is a read-only verifier. It does not write capture files and does not place
or cancel orders. By default it checks Gamma discovery plus a slug-probe fallback
so you can see whether the bot is locking onto the current live market.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
from typing import Any, Optional

from kou_polymarket_live_capture import (
    ASSET_ALIASES,
    build_book_payload,
    build_market_payload,
    discover_current_and_next_5m_markets,
    fetch_book_top,
    fetch_snapshot,
    load_poly_settings,
    login_clob_client,
    market_status,
    parse_assets,
    safe_float,
    selected_snapshot_assets,
    utc_iso,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify current Polymarket 5-minute ETH/XRP markets")
    parser.add_argument("--assets", default="eth,xrp", help="Comma-separated assets, default eth,xrp")
    parser.add_argument("--url", default="http://127.0.0.1:8071/api/snapshot", help="Optional Kou snapshot API URL")
    parser.add_argument("--skip-kou", action="store_true", help="Do not fetch local Kou snapshot alignment")
    parser.add_argument("--market-limit", type=int, default=500, help="Gamma markets list limit")
    parser.add_argument(
        "--deep-probe",
        action="store_true",
        help="Always combine Gamma list discovery with slug-probed markets",
    )
    parser.add_argument(
        "--with-books",
        action="store_true",
        help="Also authenticate read-only CLOB and show YES/NO top-of-book",
    )
    parser.add_argument("--env-file", default=".env", help="Polymarket credentials env file for --with-books")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser.parse_args()


def _asset_snapshot_map(payload: Optional[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not payload:
        return {}
    return {asset: snapshot for asset, snapshot in selected_snapshot_assets(payload, None)}


def _fetch_kou_snapshot(url: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    try:
        return fetch_snapshot(url), None
    except urllib.error.URLError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, str(exc)


def _format_ts(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return utc_iso(float(value))


def _format_delta(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:+.1f}s"


def _market_url(slug: Optional[str]) -> Optional[str]:
    if not slug:
        return None
    return f"https://polymarket.com/event/{slug}"


def _build_asset_report(
    *,
    asset: str,
    now_ts: float,
    market_limit: int,
    deep_probe: bool,
    kou_snapshot: Optional[dict[str, Any]],
    kou_by_asset: dict[str, dict[str, Any]],
    clob_client: Optional[Any],
) -> dict[str, Any]:
    discovery = discover_current_and_next_5m_markets(
        asset,
        now_ts,
        market_limit=market_limit,
        allow_slug_probe=True,
        force_slug_probe=deep_probe,
    )
    kou_ref = kou_by_asset.get(asset, {})
    kou_bucket_end = kou_ref.get("bucket_end")
    if kou_bucket_end is None and kou_snapshot is not None:
        kou_bucket_end = kou_snapshot.get("bucket_end")

    current_payload = build_market_payload(
        asset=asset,
        market=discovery.current,
        next_market=discovery.next_market,
        kou_bucket_end=kou_bucket_end,
        now_ts=now_ts,
    )

    book_payload = None
    book_error = None
    if clob_client is not None and discovery.current is not None and market_status(discovery.current, now_ts) == "LIVE":
        try:
            yes_top = fetch_book_top(clob_client, discovery.current.token_yes)
            no_top = fetch_book_top(clob_client, discovery.current.token_no)
            book_payload = build_book_payload(yes_top, no_top)
        except Exception as exc:
            book_error = str(exc)

    return {
        "asset": asset,
        "discovery": {
            "list_count": discovery.list_count,
            "probe_count": discovery.probe_count,
            "used_slug_probe": discovery.used_slug_probe,
            "deep_probe": deep_probe,
        },
        "kou": {
            "symbol": kou_ref.get("symbol"),
            "bucket_end": safe_float(kou_bucket_end, 3),
            "bucket_end_iso": None if kou_bucket_end is None else utc_iso(float(kou_bucket_end)),
            "time_left_s": kou_ref.get("time_left_s", None if kou_snapshot is None else kou_snapshot.get("time_left_s")),
            "price": kou_ref.get("price"),
            "strike": kou_ref.get("strike"),
            "signal": kou_ref.get("signal"),
        },
        "current_market": {**current_payload, "url": _market_url(current_payload.get("slug"))},
        "next_market": None
        if discovery.next_market is None
        else {
            "slug": discovery.next_market.slug,
            "url": _market_url(discovery.next_market.slug),
            "start_ts": safe_float(discovery.next_market.start_ts, 3),
            "start_iso": utc_iso(discovery.next_market.start_ts),
            "end_ts": safe_float(discovery.next_market.end_ts, 3),
            "end_iso": utc_iso(discovery.next_market.end_ts),
        },
        "book": book_payload,
        "book_error": book_error,
    }


def _print_human(report: dict[str, Any]) -> None:
    print(f"Now UTC: {report['now_iso']} ({report['now_ts']})")
    if report["kou_error"]:
        print(f"Kou snapshot: unavailable ({report['kou_error']})")
    elif report["kou_snapshot_seen"]:
        print("Kou snapshot: available")
    else:
        print("Kou snapshot: skipped")
    print()

    for item in report["assets"]:
        asset = item["asset"].upper()
        market = item["current_market"]
        kou = item["kou"]
        discovery = item["discovery"]
        print(f"{asset}")
        print(f"  status: {market['status']}")
        print(f"  slug:   {market['slug'] or '-'}")
        if market["url"]:
            print(f"  url:    {market['url']}")
        print(f"  window: {_format_ts(market['start_ts'])} -> {_format_ts(market['end_ts'])}")
        print(f"  t-left: {max(0.0, float(market['end_ts']) - report['now_ts']):.1f}s" if market["end_ts"] else "  t-left: -")
        print(
            "  flags:  "
            f"active={market['active']} accepting_orders={market['accepting_orders']} closed={market['closed']}"
        )
        print(f"  tokens: YES={market['token_yes'] or '-'} NO={market['token_no'] or '-'}")
        print(
            "  discovery: "
            f"list_count={discovery['list_count']} probe_count={discovery['probe_count']} "
            f"used_slug_probe={discovery['used_slug_probe']}"
        )

        if kou["bucket_end"] is not None:
            print(
                "  Kou:    "
                f"symbol={kou['symbol']} bucket_end={kou['bucket_end_iso']} "
                f"time_left={kou['time_left_s']} signal={kou['signal']}"
            )
            print(
                "  align:  "
                f"{market['alignment_status']} "
                f"(kou_end - poly_end = {_format_delta(market['kou_market_end_delta_s'])})"
            )
        else:
            print("  Kou:    no matching local snapshot asset")

        if item["book_error"]:
            print(f"  book:   unavailable ({item['book_error']})")
        elif item["book"]:
            book = item["book"]
            print(
                "  book:   "
                f"YES bid/ask={book['yes']['bid']}/{book['yes']['ask']} "
                f"size={book['yes']['bid_size']}/{book['yes']['ask_size']} | "
                f"NO bid/ask={book['no']['bid']}/{book['no']['ask']} "
                f"size={book['no']['bid_size']}/{book['no']['ask_size']} | "
                f"mid_sum={book['mid_sum']}"
            )

        next_market = item["next_market"]
        if next_market:
            print(f"  next:   {next_market['slug']} starts {next_market['start_iso']}")
        print()


def main() -> int:
    args = parse_args()
    requested_assets = parse_assets(args.assets)
    assets = sorted(requested_assets or set(ASSET_ALIASES))
    now_ts = time.time()

    kou_snapshot = None
    kou_error = None
    if not args.skip_kou:
        kou_snapshot, kou_error = _fetch_kou_snapshot(args.url)
    kou_by_asset = _asset_snapshot_map(kou_snapshot)

    clob_client = None
    if args.with_books:
        settings = load_poly_settings(args.env_file)
        clob_client, _address = login_clob_client(settings)

    report = {
        "now_ts": safe_float(now_ts, 3),
        "now_iso": utc_iso(now_ts),
        "kou_snapshot_seen": kou_snapshot is not None,
        "kou_error": kou_error,
        "assets": [
            _build_asset_report(
                asset=asset,
                now_ts=now_ts,
                market_limit=args.market_limit,
                deep_probe=args.deep_probe,
                kou_snapshot=kou_snapshot,
                kou_by_asset=kou_by_asset,
                clob_client=clob_client,
            )
            for asset in assets
        ],
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
