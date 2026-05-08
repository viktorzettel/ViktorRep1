#!/usr/bin/env python3
"""Small latency probe for comparing Mac vs VPS network paths.

Run the same command locally and on the VPS, then compare the JSON summaries.
It does not use secrets and does not place orders.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import statistics as st
import time
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any

import websockets


HTTP_TARGETS = {
    "clob_health": "https://clob.polymarket.com/health",
    "clob_markets": "https://clob.polymarket.com/markets?limit=1",
    "gamma_markets": "https://gamma-api.polymarket.com/markets?closed=false&limit=1",
    "geoblock": "https://polymarket.com/api/geoblock",
}

WS_TARGETS = {
    "polymarket_live_data": "wss://ws-live-data.polymarket.com",
    "coinbase_advanced": "wss://advanced-trade-ws.coinbase.com",
}

POLY_WS_HEADERS = {
    "Origin": "https://polymarket.com",
    "User-Agent": "Mozilla/5.0",
}


@dataclass
class ProbeStats:
    ok: int
    failed: int
    mean_ms: float | None
    median_ms: float | None
    p95_ms: float | None
    min_ms: float | None
    max_ms: float | None
    errors: list[str]


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    k = (len(values) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(values) - 1)
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - k) + values[hi] * (k - lo)


def summarize(values: list[float], errors: list[str]) -> ProbeStats:
    if not values:
        return ProbeStats(0, len(errors), None, None, None, None, None, errors[:5])
    return ProbeStats(
        ok=len(values),
        failed=len(errors),
        mean_ms=round(st.mean(values), 3),
        median_ms=round(percentile(values, 0.5) or 0.0, 3),
        p95_ms=round(percentile(values, 0.95) or 0.0, 3),
        min_ms=round(min(values), 3),
        max_ms=round(max(values), 3),
        errors=errors[:5],
    )


def http_probe(name: str, url: str, samples: int, timeout: float) -> tuple[str, ProbeStats, Any]:
    values: list[float] = []
    errors: list[str] = []
    last_payload: Any = None
    for _ in range(samples):
        req = urllib.request.Request(
            url,
            headers={"accept": "application/json,text/plain,*/*", "user-agent": "kou-latency-probe/0.1"},
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read(4096)
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                values.append(elapsed_ms)
                if name == "geoblock":
                    try:
                        last_payload = json.loads(raw.decode("utf-8"))
                    except Exception:
                        last_payload = raw.decode("utf-8", errors="replace")[:200]
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{exc}")
        time.sleep(0.1)
    return name, summarize(values, errors), last_payload


async def ws_probe(name: str, url: str, samples: int, timeout: float) -> tuple[str, ProbeStats]:
    values: list[float] = []
    errors: list[str] = []
    ssl_context = ssl.create_default_context()
    for _ in range(samples):
        headers = POLY_WS_HEADERS if name == "polymarket_live_data" else None
        start = time.perf_counter()
        try:
            async with websockets.connect(
                url,
                additional_headers=headers,
                ssl=ssl_context,
                open_timeout=timeout,
                ping_interval=None,
                close_timeout=1,
            ):
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                values.append(elapsed_ms)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{exc}")
        await asyncio.sleep(0.1)
    return name, summarize(values, errors)


async def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Polymarket/Coinbase latency from Mac and VPS")
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only")
    args = parser.parse_args()

    started = time.time()
    http_results: dict[str, Any] = {}
    geoblock_payload = None
    for name, url in HTTP_TARGETS.items():
        result_name, stat, payload = http_probe(name, url, args.samples, args.timeout)
        http_results[result_name] = asdict(stat)
        if result_name == "geoblock":
            geoblock_payload = payload

    ws_results = {}
    for name, url in WS_TARGETS.items():
        result_name, stat = await ws_probe(name, url, max(3, min(args.samples, 10)), args.timeout)
        ws_results[result_name] = asdict(stat)

    payload = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "samples": args.samples,
        "http": http_results,
        "websocket_handshake": ws_results,
        "geoblock_payload": geoblock_payload,
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"started_at: {payload['started_at']}")
        if geoblock_payload is not None:
            print(f"geoblock: {geoblock_payload}")
        print("\nHTTP:")
        for name, stat in http_results.items():
            print(f"  {name}: mean={stat['mean_ms']}ms median={stat['median_ms']}ms p95={stat['p95_ms']}ms max={stat['max_ms']}ms ok={stat['ok']} fail={stat['failed']}")
        print("\nWebSocket handshake:")
        for name, stat in ws_results.items():
            print(f"  {name}: mean={stat['mean_ms']}ms median={stat['median_ms']}ms p95={stat['p95_ms']}ms max={stat['max_ms']}ms ok={stat['ok']} fail={stat['failed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
