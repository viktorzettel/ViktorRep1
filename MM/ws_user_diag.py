#!/usr/bin/env python3
"""
User WS Diagnostics for Polymarket
==================================
Tries multiple auth/signature/subscription variants to determine why the
user WebSocket closes (e.g., auth mismatch, bad subscribe payload).

Run:
  python ws_user_diag.py
  python ws_user_diag.py --once --mode seconds --sub user
  python ws_user_diag.py --insecure
"""

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import ssl
import time
from dataclasses import dataclass
from typing import Optional

import websockets

from client_wrapper import PolymarketClient
from config import settings
from data_feed import BROWSER_HEADERS
from py_clob_client.clob_types import RequestArgs
from py_clob_client.headers.headers import create_level_1_headers, create_level_2_headers
from py_clob_client.signer import Signer


WS_BASE_URL = "wss://ws-subscriptions-clob.polymarket.com"


@dataclass
class Attempt:
    mode: str
    path: str
    sub: str
    auth: str


def _pm_signature(secret: str, message: str, use_b64: bool) -> str:
    key = secret
    if use_b64:
        key = base64.urlsafe_b64decode(secret)
    else:
        key = secret.encode()
    sig = hmac.new(key, message.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode()


def _pm_headers(creds, path: str, use_b64: bool) -> dict:
    ts = str(int(time.time()))
    msg = ts + "GET" + path
    sig = _pm_signature(creds.api_secret, msg, use_b64=use_b64)
    return {
        "PM-API-KEY": creds.api_key,
        "PM-API-PASSPHRASE": creds.api_passphrase,
        "PM-API-TIMESTAMP": ts,
        "PM-API-SIGN": sig,
    }


def _sign_headers(creds, path: str, mode: str, auth: str) -> dict:
    signer = Signer(settings.poly_private_key, settings.poly_chain_id)
    if auth == "l1":
        return create_level_1_headers(signer)
    if auth == "l2":
        req = RequestArgs(method="GET", request_path=path)
        return create_level_2_headers(signer, creds, req)
    if auth == "pm_b64":
        return _pm_headers(creds, path, use_b64=True)
    if auth == "pm_raw":
        return _pm_headers(creds, path, use_b64=False)
    return {}


def _auth_payload_from_headers(headers: dict) -> dict:
    # Normalize header keys to an auth message
    payload = {"type": "auth"}
    for k, v in headers.items():
        lk = k.lower()
        if lk in ("poly_address", "pm-api-key", "poly_api_key"):
            payload["apiKey"] = v
        elif lk in ("poly_signature", "pm-api-sign"):
            payload["signature"] = v
        elif lk in ("poly_timestamp", "pm-api-timestamp"):
            payload["timestamp"] = v
        elif lk in ("poly_passphrase", "pm-api-passphrase"):
            payload["passphrase"] = v
        elif lk == "poly_address":
            payload["address"] = v
    return payload


def _auth_payload_raw(creds) -> dict:
    return {
        "type": "auth",
        "apiKey": creds.api_key,
        "secret": creds.api_secret,
        "passphrase": creds.api_passphrase,
    }


def _sub_payload(kind: str, auth_msg: Optional[dict]) -> Optional[list[dict]]:
    if kind == "user":
        return [{"type": "user"}]
    if kind == "user_with_auth":
        if auth_msg and "apiKey" in auth_msg:
            return [{
                "type": "user",
                "auth": {
                    "apiKey": auth_msg.get("apiKey"),
                    "secret": auth_msg.get("secret"),
                    "passphrase": auth_msg.get("passphrase"),
                },
            }]
        return [{"type": "user"}]
    if kind == "subscribe_user":
        return [{"type": "subscribe", "channel": "user"}]
    if kind == "subscribe_channels":
        return [{"type": "subscribe", "channels": ["user"]}]
    if kind == "auth_then_user":
        return [auth_msg, {"type": "user"}] if auth_msg else [{"type": "user"}]
    if kind == "auth_then_subscribe_user":
        return [auth_msg, {"type": "subscribe", "channel": "user"}] if auth_msg else [{"type": "subscribe", "channel": "user"}]
    if kind == "subscribe_with_auth":
        msg = {"type": "subscribe", "channel": "user"}
        if auth_msg:
            msg["auth"] = auth_msg
        return [msg]
    if kind == "none":
        return None
    raise ValueError(f"unknown sub kind: {kind}")


async def _run_attempt(creds, attempt: Attempt, insecure: bool, timeout: float) -> None:
    headers = _sign_headers(creds, attempt.path, attempt.mode, attempt.auth)
    headers.update(BROWSER_HEADERS)

    ssl_ctx = None
    if insecure:
        ssl_ctx = ssl._create_unverified_context()

    tag = f"mode={attempt.mode} path={attempt.path} sub={attempt.sub} auth={attempt.auth}"
    print(f"\n=== Attempt: {tag} ===")
    try:
        url = WS_BASE_URL + attempt.path
        async with websockets.connect(
            url,
            additional_headers=headers,
            ssl=ssl_ctx,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ) as ws:
            if attempt.auth == "raw":
                auth_msg = _auth_payload_raw(creds)
            elif attempt.auth != "none":
                auth_msg = _auth_payload_from_headers(headers)
            else:
                auth_msg = None
            payloads = _sub_payload(attempt.sub, auth_msg)
            if payloads is not None:
                for p in payloads:
                    if p is None:
                        continue
                    await ws.send(json.dumps(p))

            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    print(f"No message within {timeout:.1f}s (socket still open)")
                    break
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    print(f"No message within {timeout:.1f}s (socket still open)")
                    break
                if msg == "ping":
                    await ws.send("pong")
                    continue
                print(f"Recv: {msg}")
                break

    except websockets.exceptions.InvalidStatus as e:
        print(f"InvalidStatus: {e}")
    except websockets.exceptions.InvalidStatusCode as e:
        print(f"InvalidStatusCode: code={e.status_code}")
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"ConnectionClosedError: code={e.code} reason={e.reason}")
    except websockets.exceptions.ConnectionClosedOK as e:
        print(f"ConnectionClosedOK: code={e.code} reason={e.reason}")
    except Exception as e:
        print(f"Exception: {type(e).__name__}: {e}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run a single attempt using --mode/--path/--sub.")
    parser.add_argument("--mode", choices=["seconds", "ms"], default="seconds")
    parser.add_argument("--path", default="/ws/user")
    parser.add_argument(
        "--sub",
        choices=[
            "user",
            "user_with_auth",
            "subscribe_user",
            "subscribe_channels",
            "auth_then_user",
            "auth_then_subscribe_user",
            "subscribe_with_auth",
            "none",
        ],
        default="user",
    )
    parser.add_argument("--auth", choices=["l1", "l2", "pm_b64", "pm_raw", "raw", "none"], default="l2")
    parser.add_argument("--insecure", action="store_true", help="Disable SSL verification (matches current bot).")
    parser.add_argument("--timeout", type=float, default=3.0, help="Seconds to wait for first message.")
    args = parser.parse_args()

    poly = PolymarketClient()
    creds = poly.get_credentials()
    if not creds:
        raise RuntimeError("Missing API credentials.")

    if args.once:
        await _run_attempt(creds, Attempt(args.mode, args.path, args.sub, args.auth), args.insecure, args.timeout)
        return

    # Light matrix of attempts (kept small to avoid hammering)
    attempts = [
        Attempt("seconds", "/ws/market", "subscribe_user", "l2"),
        Attempt("seconds", "/ws/user", "user_with_auth", "raw"),
        Attempt("seconds", "/ws/user", "user_with_auth", "l2"),
        Attempt("seconds", "/ws/user", "auth_then_user", "l2"),
        Attempt("seconds", "/ws/user", "auth_then_subscribe_user", "l2"),
        Attempt("seconds", "/ws/user", "subscribe_with_auth", "l2"),
        Attempt("seconds", "/ws/user", "auth_then_subscribe_user", "l1"),
        Attempt("seconds", "/ws/user", "auth_then_subscribe_user", "pm_b64"),
        Attempt("seconds", "/ws/user", "auth_then_subscribe_user", "pm_raw"),
        Attempt("seconds", "/ws/user", "auth_then_subscribe_user", "raw"),
        Attempt("seconds", "/ws/user", "none", "none"),
        Attempt("seconds", "/ws/user/", "auth_then_subscribe_user", "l2"),
        Attempt("seconds", "/ws/user/", "auth_then_subscribe_user", "pm_b64"),
        Attempt("seconds", "/ws/", "subscribe_user", "l2"),
    ]

    for a in attempts:
        await _run_attempt(creds, a, args.insecure, args.timeout)
        await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
