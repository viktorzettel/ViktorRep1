#!/usr/bin/env python3
"""Keep the local Kou dashboard open in headless Chromium.

This is intentionally operational glue, not trading logic. The current validated
Poly/Chainlink source is browser-forwarded, so the page must stay open.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from urllib.request import urlopen

from playwright.async_api import async_playwright


def snapshot_age(api_url: str) -> float | None:
    try:
        with urlopen(api_url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    assets = payload.get("assets")
    if isinstance(assets, list) and assets:
        asset = assets[0] if isinstance(assets[0], dict) else {}
    else:
        asset = payload.get("asset") or {}
    age = asset.get("model_age_s") or asset.get("age_s")
    try:
        return None if age is None else float(age)
    except (TypeError, ValueError):
        return None


async def main() -> None:
    parser = argparse.ArgumentParser(description="Headless browser keeper for Kou dashboard")
    parser.add_argument("--url", default="http://127.0.0.1:8071/")
    parser.add_argument("--api-url", default="http://127.0.0.1:8071/api/snapshot")
    parser.add_argument("--check-seconds", type=float, default=10.0)
    parser.add_argument("--reload-source-age-s", type=float, default=0.0, help="0 disables source-age reloads")
    parser.add_argument("--max-runtime-seconds", type=float, default=0.0, help="0 means run until killed")
    args = parser.parse_args()

    deadline = None if args.max_runtime_seconds <= 0 else time.time() + args.max_runtime_seconds
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(args.url, wait_until="domcontentloaded")
        print(f"headless dashboard open: {args.url}", flush=True)
        while deadline is None or time.time() < deadline:
            await asyncio.sleep(max(1.0, args.check_seconds))
            age = snapshot_age(args.api_url)
            print(f"source_age_s={age}", flush=True)
            if args.reload_source_age_s > 0 and age is not None and age > args.reload_source_age_s:
                print(f"source age {age:.1f}s > {args.reload_source_age_s:.1f}s; reloading page", flush=True)
                await page.reload(wait_until="domcontentloaded")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
