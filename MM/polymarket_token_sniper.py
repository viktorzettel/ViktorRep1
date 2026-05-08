#!/usr/bin/env python3
"""
Foundation for the future Polymarket token sniper.

This file is intentionally not a live trading bot yet. It validates a Kou signal,
resolves the current XRP 5m Polymarket market, inspects YES/NO token quotes, and
produces a dry-run execution plan. The live CLOB V2 order submit path is left
disabled until wallet, pUSD, geoblock, and tiny-size execution tests are ready.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request

import kou_polymarket_live_capture as pm


SUPPORTED_SYMBOLS = {"xrp", "xrpusdt", "xrp-usd"}
SUPPORTED_SIDES = {"yes", "no"}


@dataclass(frozen=True)
class KouBuySignal:
    symbol: str
    side: str
    max_entry_price: float
    market_slug: Optional[str] = None
    bucket_end: Optional[float] = None
    reason: Optional[str] = None
    expires_at: Optional[float] = None
    source_age_s: Optional[float] = None
    model_age_s: Optional[float] = None
    time_left_s: Optional[float] = None
    price: Optional[float] = None
    strike: Optional[float] = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "KouBuySignal":
        side = str(payload.get("side") or "").strip().lower()
        symbol = str(payload.get("symbol") or "").strip().lower()
        return cls(
            symbol=symbol,
            side=side,
            max_entry_price=_required_float(payload, "max_entry_price"),
            market_slug=_optional_str(payload.get("market_slug")),
            bucket_end=_optional_float(payload.get("bucket_end")),
            reason=_optional_str(payload.get("reason")),
            expires_at=_optional_float(payload.get("expires_at")),
            source_age_s=_optional_float(payload.get("source_age_s")),
            model_age_s=_optional_float(payload.get("model_age_s")),
            time_left_s=_optional_float(payload.get("time_left_s")),
            price=_optional_float(payload.get("price")),
            strike=_optional_float(payload.get("strike")),
        )


@dataclass(frozen=True)
class SniperLimits:
    order_size: float = 1.0
    max_order_cost: float = 1.0
    max_session_cost: float = 4.0
    max_session_orders: int = 4
    min_market_buy_amount: float = 1.0
    max_entry_price: float = 0.98
    min_time_left_s: float = 5.0
    max_source_age_s: float = 3.0
    max_model_age_s: float = 3.0
    max_book_endpoint_delta: float = 0.03
    min_visible_ask_size: float = 1.0
    market_end_tolerance_s: float = 2.0
    require_geoblock_clear_for_live: bool = True


@dataclass(frozen=True)
class TokenQuote:
    token_id: str
    side: str
    buy_price: Optional[float]
    book_ask_price: Optional[float]
    book_ask_size: Optional[float]
    book_endpoint_delta: Optional[float]
    entry_price: Optional[float]
    entry_price_source: Optional[str]


@dataclass(frozen=True)
class ExecutionPlan:
    allow_submit: bool
    mode: str
    reason: str
    signal: KouBuySignal
    market_slug: Optional[str]
    token_quote: Optional[TokenQuote]
    requested_size: float
    estimated_cost: Optional[float]
    checks: dict[str, Any]


@dataclass(frozen=True)
class LiveOrderResult:
    submitted: bool
    status: str
    reason: str
    order_type: str
    token_id: Optional[str]
    amount: Optional[float]
    response: Optional[dict[str, Any]]
    ledger_path: Optional[str]
    counts_as_successful_buy: bool


@dataclass(frozen=True)
class GeoblockStatus:
    checked: bool
    blocked: Optional[bool]
    country: Optional[str]
    region: Optional[str]
    ip: Optional[str]
    reason: str


@dataclass(frozen=True)
class SessionRiskState:
    ledger_path: Optional[str]
    submitted_order_count: int
    submitted_cost: float
    planned_order_count: int
    planned_cost: float
    bucket_already_used: bool


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _required_float(payload: dict[str, Any], key: str) -> float:
    value = _optional_float(payload.get(key))
    if value is None:
        raise ValueError(f"Signal field {key!r} is required and must be numeric")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def append_ledger_event(path_raw: str, event: dict[str, Any]) -> None:
    path = Path(path_raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def _jsonable_response(response: Any) -> dict[str, Any]:
    if response is None:
        return {}
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        dumped = response.model_dump()
        return dumped if isinstance(dumped, dict) else {"raw": str(dumped)}
    if hasattr(response, "__dict__"):
        return dict(response.__dict__)
    return {"raw": str(response)}


def check_geoblock(timeout_s: float = 5.0) -> GeoblockStatus:
    req = urllib_request.Request(
        "https://polymarket.com/api/geoblock",
        headers={"accept": "application/json", "user-agent": "kou-sniper-preflight/0.1"},
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib_error.URLError, json.JSONDecodeError) as exc:
        return GeoblockStatus(
            checked=False,
            blocked=None,
            country=None,
            region=None,
            ip=None,
            reason=f"geoblock_check_failed:{type(exc).__name__}",
        )
    if not isinstance(payload, dict):
        return GeoblockStatus(
            checked=False,
            blocked=None,
            country=None,
            region=None,
            ip=None,
            reason="geoblock_response_not_object",
        )
    blocked = payload.get("blocked")
    return GeoblockStatus(
        checked=True,
        blocked=bool(blocked) if blocked is not None else None,
        country=_optional_str(payload.get("country")),
        region=_optional_str(payload.get("region")),
        ip=_optional_str(payload.get("ip")),
        reason="geoblock_clear" if blocked is False else "geoblock_blocked_or_unknown",
    )


def session_risk_state(ledger_path: Optional[str], signal: KouBuySignal) -> SessionRiskState:
    if not ledger_path:
        return SessionRiskState(None, 0, 0.0, 0, 0.0, False)
    path = Path(ledger_path)
    submitted_order_count = 0
    submitted_cost = 0.0
    planned_orders: dict[tuple[Any, Any, Any], float] = {}
    bucket_already_used = False
    for idx, row in enumerate(_read_jsonl(path)):
        event_type = row.get("event_type")
        if bool(row.get("real_order_submitted")):
            submitted_order_count += 1
            submitted_cost += _optional_float(row.get("estimated_cost")) or 0.0
        row_bucket = _optional_float(row.get("bucket_end"))
        row_slug = _optional_str(row.get("market_slug"))
        row_side = _optional_str(row.get("side"))
        if event_type in {"dry_run_order_plan", "live_order_plan", "live_order_submitted"}:
            key = (
                round(row_bucket, 3) if row_bucket is not None else f"row:{idx}",
                row_slug or f"row:{idx}",
                row_side or f"row:{idx}",
            )
            planned_orders[key] = max(planned_orders.get(key, 0.0), _optional_float(row.get("estimated_cost")) or 0.0)
        if signal.bucket_end is not None and row_bucket is not None and abs(signal.bucket_end - row_bucket) <= 0.01:
            bucket_already_used = True
        if signal.market_slug and row_slug == signal.market_slug:
            bucket_already_used = True
    planned_cost = sum(planned_orders.values())
    return SessionRiskState(
        ledger_path=str(path),
        submitted_order_count=submitted_order_count,
        submitted_cost=pm.safe_float(submitted_cost, 6) or 0.0,
        planned_order_count=len(planned_orders),
        planned_cost=pm.safe_float(planned_cost, 6) or 0.0,
        bucket_already_used=bucket_already_used,
    )


def load_signal(path: Optional[str]) -> KouBuySignal:
    if path is None or path == "-":
        payload = json.loads(input())
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Signal JSON must be an object")
    return KouBuySignal.from_mapping(payload)


def validate_signal(signal: KouBuySignal, limits: SniperLimits, now_ts: float) -> tuple[bool, str, dict[str, Any]]:
    checks: dict[str, Any] = {
        "symbol": signal.symbol,
        "side": signal.side,
        "max_entry_price": signal.max_entry_price,
        "expires_at": signal.expires_at,
        "source_age_s": signal.source_age_s,
        "model_age_s": signal.model_age_s,
        "time_left_s": signal.time_left_s,
    }

    if signal.symbol not in SUPPORTED_SYMBOLS:
        return False, "unsupported_symbol", checks
    if signal.side not in SUPPORTED_SIDES:
        return False, "unsupported_side", checks
    if not (0.0 < signal.max_entry_price <= 1.0):
        return False, "invalid_signal_max_entry_price", checks
    if signal.max_entry_price > limits.max_entry_price:
        return False, "signal_max_entry_above_sniper_limit", checks
    if signal.expires_at is not None and now_ts >= signal.expires_at:
        return False, "signal_expired", checks
    if signal.source_age_s is None:
        return False, "signal_source_age_missing", checks
    if signal.source_age_s > limits.max_source_age_s:
        return False, "signal_source_stale", checks
    if signal.model_age_s is None:
        return False, "signal_model_age_missing", checks
    if signal.model_age_s > limits.max_model_age_s:
        return False, "signal_model_stale", checks
    if signal.time_left_s is not None and signal.time_left_s < limits.min_time_left_s:
        return False, "signal_too_late", checks
    return True, "signal_valid", checks


def resolve_market(signal: KouBuySignal, now_ts: float) -> tuple[Optional[pm.MarketCandidate], dict[str, Any]]:
    discovery = pm.discover_current_and_next_5m_markets(
        "xrp",
        now_ts,
        market_limit=50,
        allow_slug_probe=True,
        force_slug_probe=True,
        gamma_timeout=8.0,
        slug_timeout=4.0,
        broad_slug_probe=True,
    )
    market = discovery.current
    checks = {
        "requested_market_slug": signal.market_slug,
        "resolved_market_slug": None if market is None else market.slug,
        "market_status": pm.market_status(market, now_ts),
        "used_slug_probe": discovery.used_slug_probe,
        "list_count": discovery.list_count,
        "probe_count": discovery.probe_count,
    }
    if market is None:
        return None, checks
    if signal.market_slug and signal.market_slug != market.slug:
        checks["market_slug_mismatch"] = True
        return None, checks
    if signal.bucket_end is not None:
        delta = float(signal.bucket_end) - float(market.end_ts)
        checks["market_end_delta_s"] = pm.safe_float(delta, 3)
        if abs(delta) > 2.0:
            checks["market_bucket_mismatch"] = True
            return None, checks
    return market, checks


def fetch_side_quote(client: Any, market: pm.MarketCandidate, side: str) -> TokenQuote:
    token_id = market.token_yes if side == "yes" else market.token_no
    book_top = pm.fetch_book_top(client, token_id)
    buy_price_payload = pm.fetch_token_buy_price(client, token_id)
    buy_price = None if buy_price_payload is None else buy_price_payload.get("buy_price")
    buy_price_f = pm.safe_float(buy_price, 6)
    book_ask = None if book_top is None else pm.safe_float(book_top.ask, 6)
    book_ask_size = None if book_top is None else pm.safe_float(book_top.ask_size, 6)
    book_endpoint_delta = None
    if book_ask is not None and buy_price_f is not None:
        book_endpoint_delta = pm.safe_float(book_ask - buy_price_f, 6)
    entry_price = book_ask
    source = "book_ask" if book_ask else None
    return TokenQuote(
        token_id=token_id,
        side=side,
        buy_price=buy_price_f,
        book_ask_price=book_ask,
        book_ask_size=book_ask_size,
        book_endpoint_delta=book_endpoint_delta,
        entry_price=pm.safe_float(entry_price, 6),
        entry_price_source=source,
    )


def live_submit_response_is_final(response: dict[str, Any]) -> bool:
    if not response:
        return False
    status_text = " ".join(str(value).lower() for value in response.values() if value is not None)
    if any(bad in status_text for bad in ("error", "failed", "rejected", "partial", "cancelled", "canceled")):
        return False
    if any(good in status_text for good in ("filled", "success", "matched")):
        return True
    return bool(response.get("success") is True or response.get("orderID") or response.get("orderId") or response.get("id"))


def non_executable_result(plan: ExecutionPlan, *, ledger_path: Optional[str] = None) -> LiveOrderResult:
    return LiveOrderResult(
        submitted=False,
        status="not_submitted_non_executable",
        reason=plan.reason,
        order_type="-",
        token_id=None if plan.token_quote is None else plan.token_quote.token_id,
        amount=plan.estimated_cost,
        response=None,
        ledger_path=ledger_path,
        counts_as_successful_buy=False,
    )


def _load_v2_sdk() -> dict[str, Any]:
    try:
        from py_clob_client_v2 import ApiCreds, ClobClient, MarketOrderArgs, OrderType, PartialCreateOrderOptions, Side
    except ImportError as exc:
        raise RuntimeError(
            "py-clob-client-v2 is not installed. Install it with: python3 -m pip install py-clob-client-v2"
        ) from exc
    return {
        "ApiCreds": ApiCreds,
        "ClobClient": ClobClient,
        "MarketOrderArgs": MarketOrderArgs,
        "OrderType": OrderType,
        "PartialCreateOrderOptions": PartialCreateOrderOptions,
        "Side": Side,
    }


def login_v2_clob_client(settings: pm.PolyEnvSettings) -> Any:
    sdk = _load_v2_sdk()
    ApiCreds = sdk["ApiCreds"]
    ClobClient = sdk["ClobClient"]
    creds = None
    if settings.has_saved_credentials():
        creds = ApiCreds(
            api_key=settings.poly_api_key or "",
            api_secret=settings.poly_api_secret or "",
            api_passphrase=settings.poly_api_passphrase or "",
        )
    kwargs: dict[str, Any] = {
        "host": settings.poly_host,
        "chain_id": settings.poly_chain_id,
        "key": settings.poly_private_key,
        "creds": creds,
    }
    if settings.poly_proxy_address:
        kwargs["signature_type"] = 2
        kwargs["funder"] = settings.poly_proxy_address
    client = ClobClient(**kwargs)
    if creds is None:
        derived = client.create_or_derive_api_key()
        client = ClobClient(**{**kwargs, "creds": derived})
    return client


def build_dry_run_plan(
    signal: KouBuySignal,
    limits: SniperLimits,
    *,
    env_file: str,
    ledger_path: Optional[str] = None,
    require_geoblock_clear: bool = False,
) -> ExecutionPlan:
    now_ts = time.time()
    ok, reason, checks = validate_signal(signal, limits, now_ts)
    if not ok:
        return ExecutionPlan(False, "dry_run_no_order", reason, signal, None, None, limits.order_size, None, checks)

    risk_state = session_risk_state(ledger_path, signal)
    checks["session_risk"] = asdict(risk_state)
    if risk_state.bucket_already_used:
        return ExecutionPlan(False, "dry_run_no_order", "bucket_already_used_in_session", signal, None, None, limits.order_size, None, checks)
    effective_order_count = max(risk_state.submitted_order_count, risk_state.planned_order_count)
    effective_cost = max(risk_state.submitted_cost, risk_state.planned_cost)
    checks["session_risk"]["effective_order_count"] = effective_order_count
    checks["session_risk"]["effective_cost"] = pm.safe_float(effective_cost, 6)
    if effective_order_count >= limits.max_session_orders:
        return ExecutionPlan(False, "dry_run_no_order", "max_session_orders_reached", signal, None, None, limits.order_size, None, checks)
    if effective_cost >= limits.max_session_cost:
        return ExecutionPlan(False, "dry_run_no_order", "max_session_cost_reached", signal, None, None, limits.order_size, None, checks)

    if require_geoblock_clear:
        geoblock = check_geoblock()
        checks["geoblock"] = asdict(geoblock)
        if not geoblock.checked:
            return ExecutionPlan(False, "dry_run_no_order", "geoblock_check_failed", signal, None, None, limits.order_size, None, checks)
        if geoblock.blocked is not False:
            return ExecutionPlan(False, "dry_run_no_order", "geoblock_not_clear", signal, None, None, limits.order_size, None, checks)

    market, market_checks = resolve_market(signal, now_ts)
    checks.update(market_checks)
    if market is None:
        return ExecutionPlan(False, "dry_run_no_order", "market_resolution_failed", signal, None, None, limits.order_size, None, checks)
    if pm.market_status(market, now_ts) != "LIVE":
        return ExecutionPlan(False, "dry_run_no_order", "market_not_live", signal, market.slug, None, limits.order_size, None, checks)
    if not market.accepting_orders:
        return ExecutionPlan(False, "dry_run_no_order", "market_not_accepting_orders", signal, market.slug, None, limits.order_size, None, checks)

    settings = pm.load_poly_settings(env_file)
    client, address = pm.login_clob_client(settings)
    checks["client_address"] = address

    quote = fetch_side_quote(client, market, signal.side)
    if quote.book_ask_price is None or quote.book_ask_price <= 0.0:
        return ExecutionPlan(False, "dry_run_no_order", "missing_book_ask", signal, market.slug, quote, limits.order_size, None, checks)
    if quote.book_ask_size is None:
        return ExecutionPlan(False, "dry_run_no_order", "missing_book_ask_size", signal, market.slug, quote, limits.order_size, None, checks)
    if quote.entry_price is None or quote.entry_price <= 0.0:
        return ExecutionPlan(False, "dry_run_no_order", "no_executable_entry_price", signal, market.slug, quote, limits.order_size, None, checks)
    if quote.entry_price > signal.max_entry_price:
        return ExecutionPlan(False, "dry_run_no_order", "quote_above_signal_max_entry", signal, market.slug, quote, limits.order_size, None, checks)
    if quote.entry_price > limits.max_entry_price:
        return ExecutionPlan(False, "dry_run_no_order", "quote_above_sniper_max_entry", signal, market.slug, quote, limits.order_size, None, checks)
    checks["book_endpoint_delta"] = quote.book_endpoint_delta
    checks["book_endpoint_delta_limit_diagnostic"] = limits.max_book_endpoint_delta

    raw_estimated_cost = limits.order_size * quote.entry_price
    submit_amount = max(raw_estimated_cost, limits.min_market_buy_amount)
    required_visible_size = submit_amount / quote.entry_price
    checks["raw_estimated_cost_from_requested_size"] = pm.safe_float(raw_estimated_cost, 6)
    checks["clob_market_buy_amount"] = pm.safe_float(submit_amount, 6)
    checks["required_visible_ask_size_for_amount"] = pm.safe_float(required_visible_size, 6)
    if quote.book_ask_size < limits.min_visible_ask_size:
        return ExecutionPlan(False, "dry_run_no_order", "visible_ask_too_small", signal, market.slug, quote, limits.order_size, None, checks)
    if quote.book_ask_size < required_visible_size:
        return ExecutionPlan(False, "dry_run_no_order", "visible_ask_below_order_size", signal, market.slug, quote, limits.order_size, None, checks)
    if submit_amount > limits.max_order_cost:
        return ExecutionPlan(
            False,
            "dry_run_no_order",
            "estimated_cost_above_limit",
            signal,
            market.slug,
            quote,
            limits.order_size,
            pm.safe_float(submit_amount, 6),
            checks,
        )
    if effective_cost + submit_amount > limits.max_session_cost:
        checks["session_risk"]["projected_effective_cost"] = pm.safe_float(effective_cost + submit_amount, 6)
        return ExecutionPlan(
            False,
            "dry_run_no_order",
            "projected_session_cost_above_limit",
            signal,
            market.slug,
            quote,
            limits.order_size,
            pm.safe_float(submit_amount, 6),
            checks,
        )

    return ExecutionPlan(
        True,
        "dry_run_no_order",
        "dry_run_ready_live_submit_disabled",
        signal,
        market.slug,
        quote,
        limits.order_size,
        pm.safe_float(submit_amount, 6),
        checks,
    )


def record_dry_run_order_plan(
    plan: ExecutionPlan,
    *,
    ledger_path: str,
    session_id: Optional[str] = None,
    shadow_order_id: Optional[str] = None,
) -> None:
    if not plan.allow_submit:
        return
    if plan.estimated_cost is None or plan.estimated_cost <= 0.0:
        return
    if plan.token_quote is None:
        return
    event = {
        "event_type": "dry_run_order_plan",
        "session_id": session_id,
        "shadow_order_id": shadow_order_id,
        "iso_utc": pm.utc_iso(time.time()),
        "ts": time.time(),
        "market_slug": plan.market_slug,
        "bucket_end": plan.signal.bucket_end,
        "side": plan.signal.side,
        "token_id": plan.token_quote.token_id,
        "estimated_cost": plan.estimated_cost,
        "requested_size": plan.requested_size,
        "entry_price": plan.token_quote.entry_price,
        "source_age_s": plan.signal.source_age_s,
        "model_age_s": plan.signal.model_age_s,
        "real_order_submitted": False,
        "counts_as_successful_buy": False,
        "reason": plan.reason,
    }
    append_ledger_event(ledger_path, event)


def submit_live_order(
    plan: ExecutionPlan,
    *,
    env_file: str,
    ledger_path: str,
    order_type: str = "FOK",
    live_ack: bool = False,
    client: Any = None,
    sdk: Optional[dict[str, Any]] = None,
) -> LiveOrderResult:
    if not live_ack:
        raise RuntimeError("live_ack_required")
    if not plan.allow_submit:
        raise RuntimeError(f"plan_not_submittable:{plan.reason}")
    if plan.token_quote is None or not plan.token_quote.token_id:
        raise RuntimeError("missing_token_quote")
    if plan.estimated_cost is None or plan.estimated_cost <= 0.0:
        raise RuntimeError("missing_estimated_cost")
    if plan.estimated_cost < 1.0:
        raise RuntimeError("market_buy_amount_below_clob_minimum")
    if not ledger_path:
        raise RuntimeError("session_ledger_required_for_live")

    event_base = {
        "event_type": "live_order_plan",
        "iso_utc": pm.utc_iso(time.time()),
        "ts": time.time(),
        "market_slug": plan.market_slug,
        "bucket_end": plan.signal.bucket_end,
        "side": plan.signal.side,
        "token_id": plan.token_quote.token_id,
        "estimated_cost": plan.estimated_cost,
        "requested_size": plan.requested_size,
        "entry_price": plan.token_quote.entry_price,
        "source_age_s": plan.signal.source_age_s,
        "model_age_s": plan.signal.model_age_s,
        "real_order_submitted": False,
    }
    append_ledger_event(ledger_path, event_base)

    sdk = sdk or _load_v2_sdk()
    MarketOrderArgs = sdk["MarketOrderArgs"]
    OrderType = sdk["OrderType"]
    PartialCreateOrderOptions = sdk["PartialCreateOrderOptions"]
    Side = sdk["Side"]

    order_type_value = getattr(OrderType, order_type.upper())
    side_buy = getattr(Side, "BUY")
    if client is None:
        settings = pm.load_poly_settings(env_file)
        client = login_v2_clob_client(settings)

    response = client.create_and_post_market_order(
        order_args=MarketOrderArgs(
            token_id=plan.token_quote.token_id,
            amount=plan.estimated_cost,
            side=side_buy,
            order_type=order_type_value,
        ),
        options=PartialCreateOrderOptions(tick_size="0.01"),
        order_type=order_type_value,
    )
    response_payload = _jsonable_response(response)
    final = live_submit_response_is_final(response_payload)
    result = LiveOrderResult(
        submitted=True,
        status="submitted_final_or_accepted" if final else "submitted_ambiguous_stop_required",
        reason="live_order_submitted" if final else "live_order_response_ambiguous",
        order_type=order_type.upper(),
        token_id=plan.token_quote.token_id,
        amount=plan.estimated_cost,
        response=response_payload,
        ledger_path=ledger_path,
        counts_as_successful_buy=final,
    )
    append_ledger_event(
        ledger_path,
        {
            **event_base,
            "event_type": "live_order_submitted",
            "iso_utc": pm.utc_iso(time.time()),
            "ts": time.time(),
            "real_order_submitted": True,
            "submit_status": result.status,
            "submit_reason": result.reason,
            "order_type": result.order_type,
            "response": response_payload,
            "stop_required": not final,
            "counts_as_successful_buy": final,
        },
    )
    if not final:
        raise RuntimeError("live_order_response_ambiguous_stop_required")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run foundation for future Kou Polymarket token sniper")
    parser.add_argument("--signal-json", default="-", help="Path to signal JSON, or '-' for one JSON line on stdin")
    parser.add_argument("--env-file", default=".env", help="Env file with read/auth credentials for quote checks")
    parser.add_argument("--order-size", type=float, default=1.0, help="Intended token size for dry-run planning")
    parser.add_argument("--max-order-cost", type=float, default=1.0, help="Max pUSD cost for dry-run planning")
    parser.add_argument("--max-session-cost", type=float, default=4.0, help="Max pUSD cost for a supervised live session")
    parser.add_argument("--max-session-orders", type=int, default=4, help="Max submitted orders for a supervised live session")
    parser.add_argument("--max-entry-price", type=float, default=0.98, help="Sniper hard max entry price")
    parser.add_argument("--max-source-age-s", type=float, default=3.0, help="Max allowed signal source age")
    parser.add_argument("--max-model-age-s", type=float, default=3.0, help="Max allowed signal model age")
    parser.add_argument("--max-book-endpoint-delta", type=float, default=0.03, help="Max allowed book ask minus endpoint buy price")
    parser.add_argument("--min-visible-ask-size", type=float, default=1.0, help="Minimum visible ask size")
    parser.add_argument("--session-ledger", help="JSONL ledger used for one-order-per-bucket and session spend caps")
    parser.add_argument("--require-geoblock-clear", action="store_true", help="Require Polymarket geoblock endpoint to return blocked=false before planning")
    parser.add_argument("--order-type", choices=("FOK", "FAK"), default="FOK", help="CLOB V2 market order type for live submit")
    parser.add_argument("--i-understand-real-money", action="store_true", help="Required with --live; confirms this can spend real pUSD")
    parser.add_argument("--live", action="store_true", help="Attempt live submit with CLOB V2 after all preflight checks")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    signal = load_signal(args.signal_json)
    limits = SniperLimits(
        order_size=max(0.0, float(args.order_size)),
        max_order_cost=max(0.0, float(args.max_order_cost)),
        max_session_cost=max(0.0, float(args.max_session_cost)),
        max_session_orders=max(0, int(args.max_session_orders)),
        max_entry_price=max(0.0, min(1.0, float(args.max_entry_price))),
        max_source_age_s=max(0.0, float(args.max_source_age_s)),
        max_model_age_s=max(0.0, float(args.max_model_age_s)),
        max_book_endpoint_delta=max(0.0, float(args.max_book_endpoint_delta)),
        min_visible_ask_size=max(0.0, float(args.min_visible_ask_size)),
    )
    plan = build_dry_run_plan(
        signal,
        limits,
        env_file=args.env_file,
        ledger_path=args.session_ledger,
        require_geoblock_clear=bool(args.require_geoblock_clear or args.live),
    )
    print(json.dumps(asdict(plan), indent=2, sort_keys=True))
    if args.live:
        result = submit_live_order(
            plan,
            env_file=args.env_file,
            ledger_path=args.session_ledger or "",
            order_type=args.order_type,
            live_ack=bool(args.i_understand_real_money),
        )
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
    return 0 if plan.allow_submit else 2


if __name__ == "__main__":
    raise SystemExit(main())
