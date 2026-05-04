#!/usr/bin/env python3
"""
Compact dual-asset Kou monitor for localhost browser use.

Default layout is a narrow stacked dashboard for ETHUSDT and XRPUSDT with:
  - shared bucket countdown at the top
  - one compact card per asset
  - Kou probability and a small BS comparison
  - compact vol/jump metrics tuned for limited screen space

Run:
    python3 kou_dual_compact_web.py
Then open:
    http://127.0.0.1:8071
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import datetime as dt
import json
import logging
import math
import threading
import time
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import numpy as np
import websockets

from kou_dual_compact_monitor import (
    CALIB_WINDOW_S,
    CANDLE_INTERVAL_S,
    FULL_CALIB_CANDLES,
    MC_PATHS,
    MIN_CALIB_CANDLES,
    BinanceTradeStream,
    KouCalibrator,
    KouProbability,
    bs_prob_yes,
)

EPS = 1e-12
POLYMARKET_CHAINLINK_WS = "wss://ws-live-data.polymarket.com"
COINBASE_ADVANCED_WS = "wss://advanced-trade-ws.coinbase.com"
PYTH_HERMES_REST = "https://hermes.pyth.network/api/latest_price_feeds"
DEFAULT_CLOSE_OFFSET_S = 1.0
POLYMARKET_WS_HEADERS = {
    "Origin": "https://polymarket.com",
    "User-Agent": "Mozilla/5.0",
}
COINBASE_PRODUCTS = {
    "ethusdt": "ETH-USD",
    "xrpusdt": "XRP-USD",
    "btcusdt": "BTC-USD",
    "solusdt": "SOL-USD",
}
KRAKEN_PAIRS = {
    "ethusdt": "ETHUSD",
}
GEMINI_SYMBOLS = {
    "ethusdt": "ETHUSD",
}
BITSTAMP_MARKETS = {
    "ethusdt": "ethusd",
}
OKX_INSTRUMENTS = {
    "ethusdt": "ETH-USDT",
}
PYTH_PRICE_FEED_IDS = {
    "ethusdt": "0xff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
}
SIGNAL_WINDOW_S = 90.0
SIGNAL_HOLD_S = 4.0
SIGNAL_YES_THRESHOLD = 0.91
SIGNAL_NO_THRESHOLD = 0.09
SAFETY_MIN_CANDLES = MIN_CALIB_CANDLES + 1
JUMP_RATE_THRESHOLD_SIGMA = 3.0
JUMP_SWEEP_THRESHOLDS = (2.0, 2.5, 3.0, 3.5)
LATE_WINDOW_POLICY_PATH = "data/analysis_output_5m_microstructure/late_window_safety_thresholds.csv"
LATE_WINDOW_POLICY_BUCKETS = (15, 30, 45, 60, 75, 90)
POLICY_DISAGREEMENT_LOG_PATH = "data/live_policy_disagreements.jsonl"
TRADE_SCORE_VOL_BANDS = {
    "ethusdt": (0.8, 1.8, 5.5, 9.0),
    "xrpusdt": (1.0, 2.2, 6.5, 10.5),
    "default": (0.8, 1.8, 5.5, 9.0),
}
DISPLAY_SOURCES = {
    "ethusdt": "coinbase-advanced-usd",
    "xrpusdt": "coinbase-advanced-usd",
}
MODEL_SOURCES = {
    "ethusdt": "coinbase-advanced-usd",
    "xrpusdt": "coinbase-advanced-usd",
}
SUPPORTED_LIVE_SOURCES = (
    "browser-poly-chainlink",
    "poly-chainlink",
    "polymarket-chainlink",
    "pyth-usd",
    "coinbase-advanced-usd",
    "coinbase-usd",
    "kraken-usd",
    "gemini-usd",
    "bitstamp-usd",
    "okx-usdt",
    "binance-fallback",
)


def _window_log_returns(candles: list, window_s: int) -> np.ndarray:
    if not candles:
        return np.array([], dtype=float)
    latest_bucket = candles[-1].bucket_ts
    cutoff = latest_bucket - window_s
    closes = [c.close for c in candles if c.bucket_ts >= cutoff and c.close > 0.0]
    if len(closes) < 2:
        return np.array([], dtype=float)
    return np.diff(np.log(np.array(closes, dtype=float)))


def _window_grouped_returns(candles: list, window_s: int, step_s: int) -> np.ndarray:
    if not candles or step_s < CANDLE_INTERVAL_S or step_s % CANDLE_INTERVAL_S != 0:
        return np.array([], dtype=float)
    latest_end = candles[-1].bucket_ts + CANDLE_INTERVAL_S
    cutoff_end = latest_end - window_s
    closes = [
        candle.close
        for candle in candles
        if candle.close > 0.0
        and (candle.bucket_ts + CANDLE_INTERVAL_S) % step_s == 0
        and (candle.bucket_ts + CANDLE_INTERVAL_S) >= cutoff_end
    ]
    if len(closes) < 2:
        return np.array([], dtype=float)
    return np.diff(np.log(np.array(closes, dtype=float)))


def _sigma_to_1m_bp(sigma_10s: Optional[float]) -> Optional[float]:
    if sigma_10s is None or sigma_10s <= EPS:
        return None
    return sigma_10s * math.sqrt(60.0 / CANDLE_INTERVAL_S) * 10000.0


def _safe_float(value: Optional[float], digits: int = 6) -> Optional[float]:
    if value is None:
        return None
    return float(f"{value:.{digits}f}")


def _jump_rate(returns: np.ndarray, threshold_sigma: float = JUMP_RATE_THRESHOLD_SIGMA) -> tuple[Optional[float], Optional[int]]:
    if returns.size < 8:
        return None, None
    center = float(np.median(returns))
    sigma = float(np.std(returns, ddof=1))
    if sigma <= EPS:
        return 0.0, 0
    mask = np.abs(returns - center) > threshold_sigma * sigma
    return float(np.mean(mask)), int(np.sum(mask))


def _jump_sweep(
    returns: np.ndarray,
    thresholds: tuple[float, ...] = JUMP_SWEEP_THRESHOLDS,
) -> dict[str, dict[str, Optional[float]]]:
    sweep: dict[str, dict[str, Optional[float]]] = {}
    for threshold in thresholds:
        rate, count = _jump_rate(returns, threshold_sigma=threshold)
        sweep[f"{threshold:.1f}"] = {
            "rate": _safe_float(rate, 4),
            "count": None if count is None else int(count),
        }
    return sweep


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _efficiency_ratio(returns: np.ndarray) -> Optional[float]:
    if returns.size < 8:
        return None
    net = abs(float(np.sum(returns)))
    gross = float(np.sum(np.abs(returns)))
    if gross <= EPS:
        return None
    return net / gross


def _robust_sigma(returns: np.ndarray) -> Optional[float]:
    if returns.size < 6:
        return None
    center = float(np.median(returns))
    mad = float(np.median(np.abs(returns - center)))
    sigma = 1.4826 * mad
    if sigma <= EPS:
        sigma = float(np.std(returns, ddof=1))
    return None if sigma <= EPS else sigma


def _sign_flip_rate(returns: np.ndarray) -> Optional[float]:
    if returns.size < 4:
        return None
    signs = np.sign(returns)
    signs = signs[signs != 0.0]
    if signs.size < 4:
        return None
    return float(np.mean(signs[1:] != signs[:-1]))


def _adverse_share(returns: np.ndarray, side: int) -> Optional[float]:
    if returns.size < 4 or side == 0:
        return None
    gross = float(np.sum(np.abs(returns)))
    if gross <= EPS:
        return None
    adverse = -returns[returns < 0.0] if side > 0 else returns[returns > 0.0]
    return float(np.sum(adverse) / gross)


def _margin_safety_score(
    current_price: float,
    strike_price: float,
    time_left_s: float,
    sigma_anchor: Optional[float],
) -> tuple[float, Optional[float]]:
    if current_price <= 0.0 or strike_price <= 0.0 or time_left_s <= 0.0:
        return 0.0, None

    if sigma_anchor is not None and sigma_anchor > EPS:
        horizon_steps = max(time_left_s / CANDLE_INTERVAL_S, 0.5)
        margin_z = abs(math.log(current_price / strike_price)) / max(
            sigma_anchor * math.sqrt(horizon_steps),
            EPS,
        )
        return _clamp01((margin_z - 0.5) / 1.5), margin_z

    margin_bps = abs((current_price - strike_price) / strike_price) * 10000.0
    return _clamp01((margin_bps - 5.0) / 20.0), None


def _kou_blend_weight(sample_count: int) -> float:
    start = MIN_CALIB_CANDLES + 1
    span = max(1, FULL_CALIB_CANDLES - MIN_CALIB_CANDLES)
    return _clamp01((sample_count - start) / span)


def _parse_optional_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _parse_source_overrides(raw: Optional[str]) -> dict[str, str]:
    if not raw:
        return {}
    overrides: dict[str, str] = {}
    for part in str(raw).split(","):
        piece = part.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ValueError(f"Invalid source override {piece!r}; expected symbol=source")
        symbol, source = piece.split("=", 1)
        symbol_key = symbol.strip().lower()
        source_key = source.strip().lower()
        if source_key not in SUPPORTED_LIVE_SOURCES:
            raise ValueError(
                f"Unsupported source {source_key!r} for {symbol_key}; supported: {', '.join(SUPPORTED_LIVE_SOURCES)}"
            )
        overrides[symbol_key] = source_key
    return overrides


def _load_late_window_policy(path: str = LATE_WINDOW_POLICY_PATH) -> dict[tuple[str, int, str], dict[str, Optional[float]]]:
    policy_path = Path(path)
    if not policy_path.exists():
        return {}

    table: dict[tuple[str, int, str], dict[str, Optional[float]]] = {}
    try:
        with policy_path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                asset = str(row.get("asset", "")).strip().lower()
                side = str(row.get("current_side", "")).strip().lower()
                time_left_raw = _parse_optional_float(row.get("time_left_s"))
                if not asset or side not in {"yes", "no"} or time_left_raw is None:
                    continue
                key = (asset, int(time_left_raw), side)
                table[key] = {
                    "caution_max_margin_z_bin": _parse_optional_float(row.get("caution_max_margin_z_bin")),
                    "hard_no_go_max_margin_z_bin": _parse_optional_float(row.get("hard_no_go_max_margin_z_bin")),
                }
    except Exception as exc:
        logging.warning("Failed to load late-window policy from %s: %s", policy_path, exc)
        return {}

    logging.info("Loaded %d late-window policy rows from %s", len(table), policy_path)
    return table


LATE_WINDOW_POLICY = _load_late_window_policy()


def _late_window_policy_bucket(time_left_s: Optional[float]) -> Optional[int]:
    if time_left_s is None or time_left_s <= 0.0 or time_left_s > SIGNAL_WINDOW_S:
        return None
    for bucket in LATE_WINDOW_POLICY_BUCKETS:
        if time_left_s <= float(bucket):
            return bucket
    return None


def _late_window_policy_eval(
    *,
    symbol: str,
    time_left_s: Optional[float],
    side: int,
    margin_z: Optional[float],
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    if not LATE_WINDOW_POLICY or side == 0 or margin_z is None:
        return None, None, None

    bucket = _late_window_policy_bucket(time_left_s)
    if bucket is None:
        return None, None, None

    side_key = "yes" if side > 0 else "no"
    policy = LATE_WINDOW_POLICY.get((symbol, bucket, side_key))
    if not policy:
        return None, None, bucket

    hard_no_go = policy.get("hard_no_go_max_margin_z_bin")
    caution = policy.get("caution_max_margin_z_bin")
    if hard_no_go is not None and margin_z <= hard_no_go + EPS:
        return "HARD_NO_GO", f"late {bucket}s no-go", bucket
    if caution is not None and margin_z <= caution + EPS:
        return "CAUTION", f"late {bucket}s caution", bucket
    return "CLEAR", f"late {bucket}s clear", bucket


def _append_jsonl(path: str, payload: dict[str, Any]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _band_score(
    value: Optional[float],
    *,
    low_bad: float,
    low_good: float,
    high_good: float,
    high_bad: float,
    missing: float = 0.45,
) -> float:
    if value is None:
        return missing
    if value <= low_bad or value >= high_bad:
        return 0.0
    if value < low_good:
        return _clamp01((value - low_bad) / max(EPS, low_good - low_bad))
    if value <= high_good:
        return 1.0
    return _clamp01((high_bad - value) / max(EPS, high_bad - high_good))


def _fetch_binance_1m_klines(symbol: str, minutes: int = 61) -> list[float]:
    params = urllib.parse.urlencode(
        {"symbol": symbol.upper(), "interval": "1m", "limit": max(2, min(1000, int(minutes)))}
    )
    url = f"https://api.binance.com/api/v3/klines?{params}"
    with urllib.request.urlopen(url, timeout=10) as response:
        rows = json.loads(response.read().decode("utf-8"))
    closes: list[float] = []
    for row in rows:
        close = float(row[4])
        if close > 0.0:
            closes.append(close)
    return closes


def _bootstrap_vol_metrics(symbol: str) -> dict[str, Optional[float]]:
    try:
        closes = _fetch_binance_1m_klines(symbol, minutes=61)
    except Exception:
        return {"vol_30m_bp_1m": None, "vol_1h_bp_1m": None}

    if len(closes) < 3:
        return {"vol_30m_bp_1m": None, "vol_1h_bp_1m": None}

    log_ret = np.diff(np.log(np.array(closes, dtype=float)))
    vol_30m = float(np.std(log_ret[-30:], ddof=1)) * 10000.0 if log_ret.size >= 30 else None
    vol_1h = float(np.std(log_ret[-60:], ddof=1)) * 10000.0 if log_ret.size >= 60 else None
    return {
        "vol_30m_bp_1m": _safe_float(vol_30m, 1),
        "vol_1h_bp_1m": _safe_float(vol_1h, 1),
    }


def _build_live_stream(
    source_name: str,
    symbol: str,
    history_seconds: int,
    poll_seconds: float = 1.0,
) -> BinanceTradeStream:
    if source_name == "browser-poly-chainlink":
        return InjectedTickStream(symbol=symbol, history_seconds=history_seconds)
    if source_name in {"poly-chainlink", "polymarket-chainlink"}:
        return PolymarketChainlinkStream(symbol=symbol, history_seconds=history_seconds)
    if source_name == "pyth-usd":
        return PythPollingStream(symbol=symbol, history_seconds=history_seconds, poll_seconds=poll_seconds)
    if source_name == "coinbase-advanced-usd":
        return CoinbaseAdvancedTickerStream(symbol=symbol, history_seconds=history_seconds)
    if source_name == "coinbase-usd":
        return CoinbasePollingStream(symbol=symbol, history_seconds=history_seconds, poll_seconds=poll_seconds)
    if source_name == "kraken-usd":
        return KrakenPollingStream(symbol=symbol, history_seconds=history_seconds, poll_seconds=poll_seconds)
    if source_name == "gemini-usd":
        return GeminiPollingStream(symbol=symbol, history_seconds=history_seconds, poll_seconds=poll_seconds)
    if source_name == "bitstamp-usd":
        return BitstampPollingStream(symbol=symbol, history_seconds=history_seconds, poll_seconds=poll_seconds)
    if source_name == "okx-usdt":
        return OkxPollingStream(symbol=symbol, history_seconds=history_seconds, poll_seconds=poll_seconds)
    return BinanceTradeStream(symbol=symbol, history_seconds=history_seconds)


class WebSymbolMonitor:
    def __init__(
        self,
        *,
        symbol: str,
        bucket_seconds: int,
        close_offset_s: float,
        sigma_fallback: float,
        stale_after_s: float,
        mc_paths: int,
        calib_window_s: int,
        poll_seconds: float = 1.0,
        display_source_name: Optional[str] = None,
        model_source_name: Optional[str] = None,
    ) -> None:
        self.symbol = symbol.lower()
        self.bucket_seconds = bucket_seconds
        self.close_offset_s = max(0.0, float(close_offset_s))
        self.sigma_fallback = sigma_fallback
        self.stale_after_s = stale_after_s
        self.calib_window_s = calib_window_s
        self.poll_seconds = max(0.5, float(poll_seconds))
        history_seconds = calib_window_s + 120
        self.display_source_name = display_source_name or DISPLAY_SOURCES.get(self.symbol, "binance-fallback")
        self.model_source_name = model_source_name or MODEL_SOURCES.get(self.symbol, self.display_source_name)
        self.display_stream = _build_live_stream(
            self.display_source_name,
            self.symbol,
            history_seconds=history_seconds,
            poll_seconds=self.poll_seconds,
        )
        self.model_stream = (
            self.display_stream
            if self.model_source_name == self.display_source_name
            else _build_live_stream(
                self.model_source_name,
                self.symbol,
                history_seconds=history_seconds,
                poll_seconds=self.poll_seconds,
            )
        )
        self.mc = KouProbability(n_paths=max(1, mc_paths))
        self.kou_params = None
        self.bucket_start: Optional[int] = None
        self.strike_price: Optional[float] = None
        self._last_calibrated_bucket: Optional[int] = None
        self._last_calibrated_source: Optional[str] = None
        self._signal_yes_since: Optional[float] = None
        self._signal_no_since: Optional[float] = None
        self._signal_state: Optional[str] = None
        self._last_policy_disagreement_key: Optional[tuple[Any, ...]] = None
        self._bootstrap_metrics = _bootstrap_vol_metrics(self.symbol)

    def runtime_streams(self) -> list[BinanceTradeStream]:
        unique: list[BinanceTradeStream] = []
        seen: set[int] = set()
        for stream in (self.display_stream, self.model_stream):
            key = id(stream)
            if key in seen:
                continue
            seen.add(key)
            unique.append(stream)
        return unique

    def push_browser_tick(self, ts: float, price: float) -> bool:
        pushed = False
        for stream in self.runtime_streams():
            if isinstance(stream, InjectedTickStream):
                stream.push_tick(ts, price)
                pushed = True
        return pushed

    def _current_bucket_start(self, now_ts: float) -> int:
        shifted_now = now_ts + self.close_offset_s
        return (int(shifted_now) // self.bucket_seconds) * self.bucket_seconds

    def _roll_bucket_if_needed(self, now_ts: float) -> None:
        bucket_start = self._current_bucket_start(now_ts)
        if self.bucket_start is None or bucket_start != self.bucket_start:
            self.bucket_start = bucket_start
            self.strike_price = None
            self._signal_yes_since = None
            self._signal_no_since = None
            self._signal_state = None

    def _bucket_boundary_ts(self) -> Optional[float]:
        if self.bucket_start is None:
            return None
        return self.bucket_start - self.close_offset_s

    def _ensure_strike(self, source_stream: BinanceTradeStream, price: Optional[float]) -> None:
        if self.strike_price is not None:
            return

        boundary_price = self._boundary_price_for_stream(source_stream)
        if boundary_price is not None and boundary_price > 0.0:
            self.strike_price = boundary_price
            return

        if price is not None and price > 0.0:
            self.strike_price = price

    def _boundary_price_for_stream(self, source_stream: BinanceTradeStream) -> Optional[float]:
        boundary_ts = self._bucket_boundary_ts()
        if boundary_ts is None:
            return None
        boundary_price = source_stream.last_price_at_or_before(boundary_ts, max_age_s=2.0)
        if boundary_price is None:
            boundary_price = source_stream.first_price_at_or_after(boundary_ts, max_delay_s=1.0)
        return boundary_price

    def _refresh_calibration(self, source_stream: BinanceTradeStream, source_name: str) -> list:
        candles = source_stream.completed_candles(self.calib_window_s)
        latest_bucket = candles[-1].bucket_ts if candles else None
        if latest_bucket is not None and (
            latest_bucket != self._last_calibrated_bucket or source_name != self._last_calibrated_source
        ):
            self.kou_params = KouCalibrator.calibrate(candles)
            self._last_calibrated_bucket = latest_bucket
            self._last_calibrated_source = source_name
        return candles

    def _update_signal(
        self,
        now_ts: float,
        *,
        state: str,
        signal_ready: bool,
        kou_yes: Optional[float],
        time_left_s: Optional[float],
    ) -> tuple[Optional[str], Optional[float]]:
        signal_ok = (
            state == "LIVE"
            and signal_ready
            and kou_yes is not None
            and time_left_s is not None
            and time_left_s <= SIGNAL_WINDOW_S
        )
        if not signal_ok:
            self._signal_yes_since = None
            self._signal_no_since = None
            self._signal_state = None
            return None, None

        if kou_yes >= SIGNAL_YES_THRESHOLD:
            if self._signal_yes_since is None:
                self._signal_yes_since = now_ts
            self._signal_no_since = None
            held_s = max(0.0, now_ts - self._signal_yes_since)
            self._signal_state = "BUY_YES" if held_s >= SIGNAL_HOLD_S else None
            return self._signal_state, held_s

        if kou_yes <= SIGNAL_NO_THRESHOLD:
            if self._signal_no_since is None:
                self._signal_no_since = now_ts
            self._signal_yes_since = None
            held_s = max(0.0, now_ts - self._signal_no_since)
            self._signal_state = "BUY_NO" if held_s >= SIGNAL_HOLD_S else None
            return self._signal_state, held_s

        self._signal_yes_since = None
        self._signal_no_since = None
        self._signal_state = None
        return None, None

    def _log_policy_disagreement(
        self,
        *,
        now_ts: float,
        state: str,
        time_left_s: Optional[float],
        current_price: Optional[float],
        strike_price: Optional[float],
        sample_count: int,
        base_score: int,
        base_label: str,
        base_reason: str,
        final_score: int,
        final_label: str,
        final_reason: str,
        late_policy_level: Optional[str],
        late_policy_reason: Optional[str],
        late_policy_bucket: Optional[int],
        margin_z: Optional[float],
    ) -> None:
        if late_policy_level not in {"CAUTION", "HARD_NO_GO"}:
            return
        if base_label == final_label and base_reason == final_reason and base_score == final_score:
            return

        event_key = (
            late_policy_level,
            late_policy_bucket,
            None if time_left_s is None else int(round(time_left_s)),
            base_label,
            final_label,
            None if margin_z is None else round(float(margin_z), 2),
            None if current_price is None else round(float(current_price), 6),
            None if strike_price is None else round(float(strike_price), 6),
        )
        if event_key == self._last_policy_disagreement_key:
            return
        self._last_policy_disagreement_key = event_key

        payload = {
            "ts": _safe_float(now_ts, 3),
            "iso_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts)),
            "symbol": self.symbol,
            "state": state,
            "time_left_s": _safe_float(time_left_s, 1),
            "price": _safe_float(current_price, 8),
            "strike": _safe_float(strike_price, 8),
            "sample_count": sample_count,
            "base_trade_score": base_score,
            "base_trade_score_label": base_label,
            "base_trade_score_reason": base_reason,
            "final_trade_score": final_score,
            "final_trade_score_label": final_label,
            "final_trade_score_reason": final_reason,
            "late_policy_level": late_policy_level,
            "late_policy_reason": late_policy_reason,
            "late_policy_bucket_s": late_policy_bucket,
            "late_policy_margin_z": _safe_float(margin_z, 3),
        }
        try:
            _append_jsonl(POLICY_DISAGREEMENT_LOG_PATH, payload)
        except Exception as exc:
            logging.warning("Failed to append policy disagreement log for %s: %s", self.symbol, exc)

    def _trade_score(
        self,
        *,
        now_ts: float,
        state: str,
        sample_count: int,
        age_s: Optional[float],
        model_age_s: Optional[float],
        time_left_s: Optional[float],
        current_price: Optional[float],
        strike_price: Optional[float],
        candles: list,
    ) -> tuple[
        int,
        str,
        str,
        Optional[str],
        Optional[str],
        Optional[int],
        Optional[float],
        int,
        str,
        str,
        bool,
        dict[str, Any],
    ]:
        if (
            state != "LIVE"
            or time_left_s is None
            or current_price is None
            or strike_price is None
            or current_price <= 0.0
            or strike_price <= 0.0
        ):
            return 8, "WAIT", "waiting", None, None, None, None, 8, "WAIT", "waiting", False, {}

        if sample_count < SAFETY_MIN_CANDLES:
            return 14, "WAIT", "warming", None, None, None, None, 14, "WAIT", "warming", False, {}

        freshness_display = 0.2 if age_s is None else _clamp01(1.0 - age_s / max(self.stale_after_s, 1.0))
        freshness_model = 0.2 if model_age_s is None else _clamp01(1.0 - model_age_s / max(self.stale_after_s, 1.0))
        freshness = 0.5 * (freshness_display + freshness_model)
        readiness = 0.35 + 0.65 * _kou_blend_weight(sample_count)

        low_bad, low_good, high_good, high_bad = TRADE_SCORE_VOL_BANDS.get(
            self.symbol, TRADE_SCORE_VOL_BANDS["default"]
        )
        returns_60s = _window_log_returns(candles, 60)
        returns_90s = _window_log_returns(candles, 90)
        returns_3m = _window_log_returns(candles, 3 * 60)
        returns_5m = _window_log_returns(candles, 5 * 60)
        returns_15m = _window_log_returns(candles, 15 * 60)
        returns_30m = _window_log_returns(candles, 30 * 60)

        vol_5m_bp = _sigma_to_1m_bp(float(np.std(returns_5m, ddof=1))) if returns_5m.size >= 30 else None
        vol_15m_bp = _sigma_to_1m_bp(float(np.std(returns_15m, ddof=1))) if returns_15m.size >= 90 else None
        vol_30m_bp = _sigma_to_1m_bp(float(np.std(returns_30m, ddof=1))) if returns_30m.size >= 180 else None
        vol_scores = [
            _band_score(
                value,
                low_bad=low_bad,
                low_good=low_good,
                high_good=high_good,
                high_bad=high_bad,
                missing=0.40,
            )
            for value in (vol_5m_bp, vol_15m_bp, vol_30m_bp)
        ]
        vol_regime = float(sum(vol_scores) / len(vol_scores))

        available_vols = [value for value in (vol_5m_bp, vol_15m_bp, vol_30m_bp) if value is not None and value > EPS]
        if len(available_vols) >= 2:
            vol_ratio = max(available_vols) / min(available_vols)
            if vol_ratio <= 1.35:
                vol_stability = 1.0
            elif vol_ratio >= 2.40:
                vol_stability = 0.0
            else:
                vol_stability = _clamp01(1.0 - (vol_ratio - 1.35) / 1.05)
        else:
            vol_stability = 0.50

        jump_10s_5m = _window_grouped_returns(candles, 5 * 60, 10)
        jump_10s_15m = _window_grouped_returns(candles, 15 * 60, 10)
        jump_30s_30m = _window_grouped_returns(candles, 30 * 60, 30)
        jump_rate_5m, _ = _jump_rate(jump_10s_5m, threshold_sigma=JUMP_RATE_THRESHOLD_SIGMA)
        jump_rate_15m, _ = _jump_rate(jump_10s_15m, threshold_sigma=JUMP_RATE_THRESHOLD_SIGMA)
        jump_rate_30m, _ = _jump_rate(jump_30s_30m, threshold_sigma=JUMP_RATE_THRESHOLD_SIGMA)
        jump_values = [value for value in (jump_rate_5m, jump_rate_15m, jump_rate_30m) if value is not None]
        if not jump_values:
            jump_calm = 0.55
        else:
            effective_jump = float(sum(jump_values) / len(jump_values))
            if effective_jump <= 0.01:
                jump_calm = 1.0
            elif effective_jump >= 0.10:
                jump_calm = 0.0
            else:
                jump_calm = _clamp01(1.0 - (effective_jump - 0.01) / 0.09)

        side = 1 if current_price > strike_price else (-1 if current_price < strike_price else 0)
        sigma_anchor = _robust_sigma(returns_5m) or _robust_sigma(returns_15m) or _robust_sigma(returns_30m)
        margin_safety, margin_z = _margin_safety_score(current_price, strike_price, time_left_s, sigma_anchor)
        late_policy_level, late_policy_reason, late_policy_bucket = _late_window_policy_eval(
            symbol=self.symbol,
            time_left_s=time_left_s,
            side=side,
            margin_z=margin_z,
        )

        flip_values = [value for value in (_sign_flip_rate(returns_90s), _sign_flip_rate(returns_3m)) if value is not None]
        if not flip_values:
            flip_calm = 0.45
        else:
            effective_flip = float(sum(flip_values) / len(flip_values))
            if effective_flip <= 0.20:
                flip_calm = 1.0
            elif effective_flip >= 0.65:
                flip_calm = 0.0
            else:
                flip_calm = _clamp01(1.0 - (effective_flip - 0.20) / 0.45)

        reversal_values = [
            value
            for value in (
                _adverse_share(returns_60s, side),
                _adverse_share(returns_90s, side),
                _adverse_share(returns_3m, side),
            )
            if value is not None
        ]
        if not reversal_values:
            reversal_safety = 0.40 if side == 0 else 0.50
        else:
            effective_reversal = float(sum(reversal_values) / len(reversal_values))
            if effective_reversal <= 0.22:
                reversal_safety = 1.0
            elif effective_reversal >= 0.60:
                reversal_safety = 0.0
            else:
                reversal_safety = _clamp01(1.0 - (effective_reversal - 0.22) / 0.38)

        eff_values = [value for value in (_efficiency_ratio(returns_90s), _efficiency_ratio(returns_3m)) if value is not None]
        trend_clean = float(sum(eff_values) / len(eff_values)) if eff_values else 0.45

        score_raw = (
            0.14 * freshness
            + 0.10 * readiness
            + 0.20 * margin_safety
            + 0.14 * vol_regime
            + 0.12 * vol_stability
            + 0.16 * jump_calm
            + 0.08 * trend_clean
            + 0.10 * flip_calm
            + 0.16 * reversal_safety
        )
        score = int(round(100.0 * score_raw))

        weakest = min(
            [
                ("freshness", freshness),
                ("readiness", readiness),
                ("margin", margin_safety),
                ("vol", vol_regime),
                ("stability", vol_stability),
                ("jumps", jump_calm),
                ("flips", flip_calm),
                ("reversal", reversal_safety),
                ("trend", trend_clean),
            ],
            key=lambda item: item[1],
        )[0]

        if score >= 78:
            label = "GOOD"
        elif score >= 58:
            label = "OK"
        elif score >= 38:
            label = "CAREFUL"
        else:
            label = "AVOID"

        if weakest == "freshness":
            reason = "stale"
        elif weakest == "readiness":
            reason = "warming"
        elif weakest == "margin":
            reason = "near strike" if margin_z is None or margin_z < 1.0 else "thin edge"
        elif weakest == "vol":
            reason = "bad vol"
        elif weakest == "stability":
            reason = "unstable"
        elif weakest == "jumps":
            reason = "jump risk"
        elif weakest == "flips":
            reason = "flip risk"
        elif weakest == "reversal":
            reason = "reversal risk"
        else:
            reason = "messy tape"

        if score >= 78 and margin_safety >= 0.75 and reversal_safety >= 0.65 and jump_calm >= 0.60:
            reason = "safe now"
        elif score >= 78:
            reason = "good now"

        base_score = score
        base_label = label
        base_reason = reason

        if late_policy_level == "HARD_NO_GO":
            score = min(score, 24)
            label = "AVOID"
            reason = "policy no-go"
        elif late_policy_level == "CAUTION":
            score = min(score, 57)
            if score >= 38:
                label = "CAREFUL"
            else:
                label = "AVOID"
            reason = "policy caution"

        policy_override = score != base_score or label != base_label or reason != base_reason
        sigma_anchor_bp_1m = _sigma_to_1m_bp(sigma_anchor)
        safety_components: dict[str, Any] = {
            "freshness": _safe_float(freshness, 4),
            "freshness_display": _safe_float(freshness_display, 4),
            "freshness_model": _safe_float(freshness_model, 4),
            "readiness": _safe_float(readiness, 4),
            "margin_safety": _safe_float(margin_safety, 4),
            "margin_z": _safe_float(margin_z, 4),
            "vol_regime": _safe_float(vol_regime, 4),
            "vol_stability": _safe_float(vol_stability, 4),
            "jump_calm": _safe_float(jump_calm, 4),
            "jump_rate_5m": _safe_float(jump_rate_5m, 4),
            "jump_rate_15m": _safe_float(jump_rate_15m, 4),
            "jump_rate_30m": _safe_float(jump_rate_30m, 4),
            "flip_calm": _safe_float(flip_calm, 4),
            "flip_rate_90s": _safe_float(_sign_flip_rate(returns_90s), 4),
            "flip_rate_3m": _safe_float(_sign_flip_rate(returns_3m), 4),
            "reversal_safety": _safe_float(reversal_safety, 4),
            "adverse_share_60s": _safe_float(_adverse_share(returns_60s, side), 4),
            "adverse_share_90s": _safe_float(_adverse_share(returns_90s, side), 4),
            "adverse_share_3m": _safe_float(_adverse_share(returns_3m, side), 4),
            "trend_clean": _safe_float(trend_clean, 4),
            "efficiency_90s": _safe_float(_efficiency_ratio(returns_90s), 4),
            "efficiency_3m": _safe_float(_efficiency_ratio(returns_3m), 4),
            "sigma_anchor_bp_1m": _safe_float(sigma_anchor_bp_1m, 4),
            "side": side,
            "weakest_component": weakest,
            "score_raw": _safe_float(score_raw, 4),
        }
        self._log_policy_disagreement(
            now_ts=now_ts,
            state=state,
            time_left_s=time_left_s,
            current_price=current_price,
            strike_price=strike_price,
            sample_count=sample_count,
            base_score=base_score,
            base_label=base_label,
            base_reason=base_reason,
            final_score=score,
            final_label=label,
            final_reason=reason,
            late_policy_level=late_policy_level,
            late_policy_reason=late_policy_reason,
            late_policy_bucket=late_policy_bucket,
            margin_z=margin_z,
        )

        return (
            score,
            label,
            reason,
            late_policy_level,
            late_policy_reason,
            late_policy_bucket,
            margin_z,
            base_score,
            base_label,
            base_reason,
            policy_override,
            safety_components,
        )

    def snapshot(self, now_ts: float) -> dict[str, Any]:
        self._roll_bucket_if_needed(now_ts)
        current_price, display_ts = self.display_stream.latest()
        model_price, model_ts = self.model_stream.latest()
        candles = self._refresh_calibration(self.model_stream, self.model_source_name)
        self._ensure_strike(self.display_stream, current_price)

        age_s = None if display_ts is None else max(0.0, now_ts - display_ts)
        model_age_s = None if model_ts is None else max(0.0, now_ts - model_ts)
        bucket_end = (
            None if self.bucket_start is None else self.bucket_start + self.bucket_seconds - self.close_offset_s
        )
        time_left_s = None if bucket_end is None else max(0.0, bucket_end - now_ts)

        state = "BOOT"
        if current_price is not None and self.strike_price is not None:
            state = "LIVE"
        if (
            (age_s is not None and age_s > self.stale_after_s)
            or (model_age_s is not None and model_age_s > self.stale_after_s)
        ):
            state = "STALE"

        returns_30m = _window_log_returns(candles, 30 * 60)
        returns_1h = _window_log_returns(candles, 60 * 60)
        jump_10s_10m = _window_grouped_returns(candles, 10 * 60, 10)
        jump_30s_15m = _window_grouped_returns(candles, 15 * 60, 30)

        live_vol_30m = _sigma_to_1m_bp(float(np.std(returns_30m, ddof=1))) if returns_30m.size >= 180 else None
        live_vol_1h = _sigma_to_1m_bp(float(np.std(returns_1h, ddof=1))) if returns_1h.size >= 360 else None
        vol_30m_bp_1m = live_vol_30m if live_vol_30m is not None else self._bootstrap_metrics["vol_30m_bp_1m"]
        vol_1h_bp_1m = live_vol_1h if live_vol_1h is not None else self._bootstrap_metrics["vol_1h_bp_1m"]

        jump_rate_10s, jump_count_10s = _jump_rate(jump_10s_10m, threshold_sigma=JUMP_RATE_THRESHOLD_SIGMA)
        jump_rate_30s, jump_count_30s = _jump_rate(jump_30s_15m, threshold_sigma=JUMP_RATE_THRESHOLD_SIGMA)
        jump_sweep_10s_10m = _jump_sweep(jump_10s_10m)
        jump_sweep_30s_15m = _jump_sweep(jump_30s_15m)

        kou_yes = None
        raw_kou_yes = None
        bs_yes = None
        edge_pp = None
        delta_bps = None
        model = "BS"
        kou_weight = 0.0
        signal_ready = False
        if (
            current_price is not None
            and self.strike_price is not None
            and current_price > 0.0
            and self.strike_price > 0.0
            and time_left_s is not None
        ):
            sigma_bs = self.sigma_fallback
            bs_yes = bs_prob_yes(current_price, self.strike_price, time_left_s, sigma_bs)
            if self.kou_params is not None:
                sigma_bs = self.kou_params.sigma_per_sqrt_second
                bs_yes = bs_prob_yes(current_price, self.strike_price, time_left_s, sigma_bs)
                raw_kou_yes = self.mc.prob_yes(current_price, self.strike_price, time_left_s, self.kou_params)
                kou_weight = _kou_blend_weight(len(candles))
                kou_yes = (kou_weight * raw_kou_yes) + ((1.0 - kou_weight) * bs_yes)
                signal_ready = kou_weight >= 0.999
                model = "KOU" if signal_ready else "KOU-WARM"
            else:
                kou_yes = bs_yes
            edge_pp = (kou_yes - bs_yes) * 100.0 if kou_yes is not None and bs_yes is not None else None
            delta_bps = (current_price - self.strike_price) / self.strike_price * 10000.0

        synthetic_count = sum(1 for candle in candles if candle.synthetic)
        sample_count = len(candles)
        if self.kou_params is None:
            kou_phase = "calibrating"
        else:
            kou_phase = "full" if signal_ready else "warm"
        signal_state, signal_hold_s = self._update_signal(
            now_ts,
            state=state,
            signal_ready=signal_ready,
            kou_yes=kou_yes,
            time_left_s=time_left_s,
        )
        (
            trade_score,
            trade_score_label,
            trade_score_reason,
            late_policy_level,
            late_policy_reason,
            late_policy_bucket,
            late_policy_margin_z,
            base_trade_score,
            base_trade_score_label,
            base_trade_score_reason,
            policy_override,
            safety_components,
        ) = self._trade_score(
            now_ts=now_ts,
            state=state,
            sample_count=sample_count,
            age_s=age_s,
            model_age_s=model_age_s,
            time_left_s=time_left_s,
            current_price=current_price,
            strike_price=self.strike_price,
            candles=candles,
        )

        footer = (
            (
                f"px {self.display_source_name} | vol {self.model_source_name} | warm kou {kou_weight * 100:.0f}% | lambda {self.kou_params.lam:.3f} | p(up) {self.kou_params.p_up:.2f} | {sample_count}/{FULL_CALIB_CANDLES + 1}"
                if kou_phase == "warm"
                else f"px {self.display_source_name} | vol {self.model_source_name} | full kou | lambda {self.kou_params.lam:.3f} | p(up) {self.kou_params.p_up:.2f} | {sample_count} candles"
            )
            if self.kou_params is not None
            else f"px {self.display_source_name} | vol {self.model_source_name} | calibrating {sample_count}/{MIN_CALIB_CANDLES + 1} candles"
        )

        return {
            "symbol": self.symbol,
            "name": self.symbol.replace("usdt", "").upper(),
            "state": state,
            "display_source": self.display_source_name,
            "model_source": self.model_source_name,
            "model": model,
            "kou_phase": kou_phase,
            "sample_count": sample_count,
            "price": _safe_float(current_price, 8),
            "strike": _safe_float(self.strike_price, 8),
            "delta_bps": _safe_float(delta_bps, 1),
            "age_s": _safe_float(age_s, 1),
            "model_age_s": _safe_float(model_age_s, 1),
            "model_price": _safe_float(model_price, 8),
            "time_left_s": _safe_float(time_left_s, 1),
            "bucket_end": bucket_end,
            "kou_yes": _safe_float(kou_yes, 4),
            "raw_kou_yes": _safe_float(raw_kou_yes, 4),
            "kou_weight": _safe_float(kou_weight, 3),
            "bs_yes": _safe_float(bs_yes, 4),
            "edge_pp": _safe_float(edge_pp, 1),
            "lam": _safe_float(None if self.kou_params is None else self.kou_params.lam, 4),
            "p_up": _safe_float(None if self.kou_params is None else self.kou_params.p_up, 4),
            "sigma_model_bp_1m": _safe_float(
                None
                if self.kou_params is None
                else _sigma_to_1m_bp(self.kou_params.sigma),
                2,
            ),
            "signal": signal_state,
            "signal_hold_s": _safe_float(signal_hold_s, 1),
            "trade_score": trade_score,
            "trade_score_label": trade_score_label,
            "trade_score_reason": trade_score_reason,
            "base_trade_score": base_trade_score,
            "base_trade_score_label": base_trade_score_label,
            "base_trade_score_reason": base_trade_score_reason,
            "late_policy_level": late_policy_level,
            "late_policy_reason": late_policy_reason,
            "late_policy_bucket_s": late_policy_bucket,
            "late_policy_margin_z": _safe_float(late_policy_margin_z, 3),
            "policy_override": policy_override,
            "safety_components": safety_components,
            "vol_30m_bp_1m": _safe_float(vol_30m_bp_1m, 1),
            "vol_1h_bp_1m": _safe_float(vol_1h_bp_1m, 1),
            "jump_10s_10m_rate": _safe_float(jump_rate_10s, 4),
            "jump_10s_10m_count": jump_count_10s,
            "jump_30s_15m_rate": _safe_float(jump_rate_30s, 4),
            "jump_30s_15m_count": jump_count_30s,
            "jump_sweep_10s_10m": jump_sweep_10s_10m,
            "jump_sweep_30s_15m": jump_sweep_30s_15m,
            "synthetic_count": synthetic_count,
            "footer": footer,
        }


class PolymarketChainlinkStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        base = symbol.lower().replace("usdt", "")
        self.chainlink_symbol = f"{base}/usd"
        self.chainlink_symbol_upper = self.chainlink_symbol.upper()
        self.rtds_symbol_upper = self.symbol.upper()
        self.url = POLYMARKET_CHAINLINK_WS

    @staticmethod
    def _payload_ts(payload: dict[str, Any], now_ts: float) -> float:
        ts_raw = payload.get("timestamp") or payload.get("updatedAt")
        if ts_raw is None:
            return now_ts
        try:
            ts_value = float(ts_raw)
        except Exception:
            return now_ts
        return ts_value / 1000.0 if ts_value > 10_000_000_000 else ts_value

    def _update_from_chainlink_payload(self, payload: dict[str, Any], now_ts: float) -> bool:
        symbol = str(payload.get("symbol") or payload.get("asset") or "").lower()
        if symbol and symbol not in {self.chainlink_symbol, self.symbol}:
            return False

        data = payload.get("data")
        if isinstance(data, list):
            updated = False
            for item in data:
                if not isinstance(item, dict):
                    continue
                try:
                    price = float(item.get("value"))
                except Exception:
                    continue
                self._update(self._payload_ts(item, now_ts), price)
                updated = True
            return updated

        try:
            price = float(payload.get("value"))
        except Exception:
            return False
        self._update(self._payload_ts(payload, now_ts), price)
        return True

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    additional_headers=POLYMARKET_WS_HEADERS,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                    max_size=2_000_000,
                ) as ws:
                    logging.warning("chainlink stream connected %s", self.chainlink_symbol)
                    backoff = 1.0
                    subscribe = {
                        "action": "subscribe",
                        "subscriptions": [
                            {
                                "topic": "crypto_prices_chainlink",
                                "type": "*",
                                "filters": json.dumps({"symbol": self.chainlink_symbol}),
                            },
                            {
                                "topic": "crypto_prices_chainlink",
                                "type": "*",
                                "filters": json.dumps({"symbol": self.chainlink_symbol_upper}),
                            },
                            {
                                "topic": "crypto_prices",
                                "type": "update",
                                "filters": json.dumps({"symbol": self.symbol}),
                            },
                            {
                                "topic": "crypto_prices",
                                "type": "update",
                                "filters": json.dumps({"symbol": self.rtds_symbol_upper}),
                            },
                        ],
                    }
                    await ws.send(json.dumps(subscribe))
                    last_ping = time.time()

                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
                        except asyncio.TimeoutError:
                            try:
                                await ws.send("PING")
                            except Exception:
                                pass
                            last_ping = time.time()
                            continue
                        if self._stop.is_set():
                            break
                        now_ts = time.time()
                        if now_ts - last_ping >= 5.0:
                            try:
                                await ws.send("PING")
                            except Exception:
                                pass
                            last_ping = now_ts
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue

                        payload = msg.get("payload") or {}
                        if not isinstance(payload, dict):
                            continue
                        if msg.get("topic") not in {None, "crypto_prices_chainlink", "crypto_prices"}:
                            continue
                        self._update_from_chainlink_payload(payload, now_ts)
            except Exception as exc:
                logging.warning("chainlink reconnect %s: %s", self.chainlink_symbol, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)


class PolymarketBinanceStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        self.binance_symbol = symbol.lower()
        self.url = POLYMARKET_CHAINLINK_WS

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    additional_headers=POLYMARKET_WS_HEADERS,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                    max_size=2_000_000,
                ) as ws:
                    logging.warning("polymarket binance stream connected %s", self.binance_symbol)
                    backoff = 1.0
                    subscribe = {
                        "action": "subscribe",
                        "subscriptions": [
                            {
                                "topic": "crypto_prices",
                                "type": "update",
                                "filters": json.dumps({"symbol": self.binance_symbol}),
                            }
                        ],
                    }
                    await ws.send(json.dumps(subscribe))
                    last_ping = time.time()

                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        now_ts = time.time()
                        if now_ts - last_ping >= 5.0:
                            try:
                                await ws.send("PING")
                            except Exception:
                                pass
                            last_ping = now_ts
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue

                        payload = msg.get("payload") or {}
                        if msg.get("topic") != "crypto_prices":
                            continue
                        symbol = (payload.get("symbol") or payload.get("asset") or "").lower()
                        if symbol != self.binance_symbol:
                            continue
                        try:
                            price = float(payload.get("value"))
                        except Exception:
                            continue
                        ts_ms = payload.get("timestamp") or payload.get("updatedAt")
                        ts = float(ts_ms) / 1000.0 if ts_ms is not None else now_ts
                        self._update(ts, price)
            except Exception as exc:
                logging.warning("polymarket binance reconnect %s: %s", self.binance_symbol, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)


class InjectedTickStream(BinanceTradeStream):
    def push_tick(self, ts: float, price: float) -> None:
        if self.last_ts is not None and ts <= self.last_ts + 1e-9:
            return
        self._update(ts, price)

    async def run(self) -> None:
        await self._stop.wait()


class CoinbaseAdvancedTickerStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        self.product = COINBASE_PRODUCTS[symbol.lower()]
        self.url = COINBASE_ADVANCED_WS

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                    max_size=2_000_000,
                ) as ws:
                    logging.warning("coinbase advanced stream connected %s", self.product)
                    backoff = 1.0
                    await ws.send(
                        json.dumps(
                            {
                                "type": "subscribe",
                                "product_ids": [self.product],
                                "channel": "ticker",
                            }
                        )
                    )
                    await ws.send(
                        json.dumps(
                            {
                                "type": "subscribe",
                                "product_ids": [self.product],
                                "channel": "heartbeats",
                            }
                        )
                    )

                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue

                        if msg.get("channel") != "ticker":
                            continue
                        events = msg.get("events") or []
                        for event in events:
                            tickers = event.get("tickers") or []
                            for ticker in tickers:
                                if str(ticker.get("product_id", "")).upper() != self.product:
                                    continue
                                price_raw = ticker.get("price") or ticker.get("last_price")
                                if price_raw is None:
                                    continue
                                try:
                                    price = float(price_raw)
                                except Exception:
                                    continue
                                ts_str = ticker.get("time") or msg.get("timestamp")
                                ts = time.time()
                                if isinstance(ts_str, str):
                                    try:
                                        ts = dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                                    except Exception:
                                        ts = time.time()
                                self._update(ts, price)
            except Exception as exc:
                logging.warning("coinbase advanced reconnect %s: %s", self.product, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)


class CoinbasePollingStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120, poll_seconds: float = 1.0) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        self.product = COINBASE_PRODUCTS[symbol.lower()]
        self.poll_seconds = max(0.5, poll_seconds)

    def _fetch_price(self) -> Optional[float]:
        url = f"https://api.coinbase.com/v2/prices/{self.product}/spot"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        amount = data.get("data", {}).get("amount")
        return float(amount) if amount is not None else None

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                price = await asyncio.to_thread(self._fetch_price)
                if price is not None and price > 0.0:
                    self._update(time.time(), price)
            except Exception:
                pass
            await asyncio.sleep(self.poll_seconds)


class KrakenPollingStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120, poll_seconds: float = 1.0) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        self.pair = KRAKEN_PAIRS[symbol.lower()]
        self.poll_seconds = max(0.5, poll_seconds)

    def _fetch_price(self) -> Optional[float]:
        url = f"https://api.kraken.com/0/public/Ticker?pair={urllib.parse.quote(self.pair)}"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        result = data.get("result") or {}
        if not result:
            return None
        ticker = next(iter(result.values()))
        close = ticker.get("c")
        if isinstance(close, list) and close:
            return float(close[0])
        return None

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                price = await asyncio.to_thread(self._fetch_price)
                if price is not None and price > 0.0:
                    self._update(time.time(), price)
            except Exception:
                pass
            await asyncio.sleep(self.poll_seconds)


class GeminiPollingStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120, poll_seconds: float = 1.0) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        self.market = GEMINI_SYMBOLS[symbol.lower()]
        self.poll_seconds = max(0.5, poll_seconds)

    def _fetch_price(self) -> Optional[float]:
        url = f"https://api.gemini.com/v1/pubticker/{urllib.parse.quote(self.market)}"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        last = data.get("last")
        return float(last) if last is not None else None

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                price = await asyncio.to_thread(self._fetch_price)
                if price is not None and price > 0.0:
                    self._update(time.time(), price)
            except Exception:
                pass
            await asyncio.sleep(self.poll_seconds)


class BitstampPollingStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120, poll_seconds: float = 1.0) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        self.market = BITSTAMP_MARKETS[symbol.lower()]
        self.poll_seconds = max(0.5, poll_seconds)

    def _fetch_price(self) -> Optional[float]:
        url = f"https://www.bitstamp.net/api/v2/ticker/{urllib.parse.quote(self.market)}/"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        last = data.get("last")
        return float(last) if last is not None else None

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                price = await asyncio.to_thread(self._fetch_price)
                if price is not None and price > 0.0:
                    self._update(time.time(), price)
            except Exception:
                pass
            await asyncio.sleep(self.poll_seconds)


class OkxPollingStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120, poll_seconds: float = 1.0) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        self.instrument = OKX_INSTRUMENTS[symbol.lower()]
        self.poll_seconds = max(0.5, poll_seconds)

    def _fetch_price(self) -> Optional[float]:
        url = f"https://www.okx.com/api/v5/market/ticker?instId={urllib.parse.quote(self.instrument)}"
        with urllib.request.urlopen(url, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        rows = data.get("data") or []
        if not rows:
            return None
        last = rows[0].get("last")
        return float(last) if last is not None else None

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                price = await asyncio.to_thread(self._fetch_price)
                if price is not None and price > 0.0:
                    self._update(time.time(), price)
            except Exception:
                pass
            await asyncio.sleep(self.poll_seconds)


class PythPollingStream(BinanceTradeStream):
    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120, poll_seconds: float = 1.0) -> None:
        super().__init__(symbol=symbol, history_seconds=history_seconds)
        self.feed_id = PYTH_PRICE_FEED_IDS[symbol.lower()]
        self.poll_seconds = max(0.5, poll_seconds)

    def _fetch_price(self) -> Optional[tuple[float, float]]:
        params = urllib.parse.urlencode({"ids[]": self.feed_id, "parsed": "true"})
        url = f"{PYTH_HERMES_REST}?{params}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json,text/plain,*/*",
                "Origin": "https://insights.pyth.network",
                "Referer": "https://insights.pyth.network/",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            rows = json.loads(response.read().decode("utf-8"))
        if isinstance(rows, list):
            parsed = rows
        elif isinstance(rows, dict):
            parsed = rows.get("parsed")
        else:
            parsed = None
        if not parsed:
            return None
        item = parsed[0]
        price_info = item.get("price") or {}
        price_raw = price_info.get("price")
        expo_raw = price_info.get("expo")
        publish_time = price_info.get("publish_time") or item.get("publish_time")
        if price_raw is None or expo_raw is None:
            return None
        price = float(price_raw) * (10 ** int(expo_raw))
        ts = float(publish_time) if publish_time is not None else time.time()
        return price, ts

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                result = await asyncio.to_thread(self._fetch_price)
                if result is not None:
                    price, ts = result
                    if price > 0.0:
                        self._update(ts, price)
            except Exception:
                pass
            await asyncio.sleep(self.poll_seconds)


class SharedState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._payload: dict[str, Any] = {
            "title": "loading",
            "bucket_seconds": 300,
            "time_left_s": None,
            "progress": 0.0,
            "bucket_end": None,
            "assets": [],
            "updated_at": None,
        }

    def set_payload(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._payload = payload

    def get_payload(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._payload))


def make_handler(state: SharedState, monitors_by_symbol: dict[str, WebSymbolMonitor]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/snapshot":
                payload = state.get_payload()
                body = json.dumps(payload).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            if self.path in ("/", "/index.html"):
                body = DASHBOARD_HTML.encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/push_tick":
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(max(0, length))
                payload = json.loads(raw.decode("utf-8"))
                symbol = str(payload.get("symbol", "")).strip().lower()
                price = float(payload.get("price"))
                ts = float(payload.get("ts", time.time()))
            except Exception:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return

            monitor = monitors_by_symbol.get(symbol)
            if monitor is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            if price > 0.0:
                pushed = monitor.push_browser_tick(ts, price)
                if not pushed:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return

            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def run_server(host: str, port: int, state: SharedState, monitors: list[WebSymbolMonitor]) -> ThreadingHTTPServer:
    monitor_map = {monitor.symbol: monitor for monitor in monitors}
    server = ThreadingHTTPServer((host, port), make_handler(state, monitor_map))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="http-server", daemon=True)
    thread.start()
    return server


async def publish_loop(
    state: SharedState,
    monitors: list[WebSymbolMonitor],
    bucket_seconds: int,
    refresh_seconds: float,
    close_offset_s: float,
) -> None:
    while True:
        now_ts = time.time()
        shifted_now = now_ts + close_offset_s
        bucket_start = (int(shifted_now) // bucket_seconds) * bucket_seconds
        bucket_end = bucket_start + bucket_seconds - close_offset_s
        time_left_s = max(0.0, bucket_end - now_ts)
        assets = [monitor.snapshot(now_ts) for monitor in monitors]
        state.set_payload(
            {
                "title": "Kou compact dual monitor",
                "bucket_seconds": bucket_seconds,
                "time_left_s": round(time_left_s, 1),
                "progress": round(time_left_s / bucket_seconds, 4) if bucket_seconds > 0 else 0.0,
                "bucket_end": bucket_end,
                "refresh_seconds": refresh_seconds,
                "assets": assets,
                "updated_at": int(now_ts),
            }
        )
        await asyncio.sleep(refresh_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compact dual-asset Kou localhost dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8071, help="HTTP port (default: 8071)")
    parser.add_argument(
        "--symbols",
        default="ethusdt,xrpusdt",
        help="Comma-separated Binance symbols, stacked vertically (default: ethusdt,xrpusdt)",
    )
    parser.add_argument("--bucket-seconds", type=int, default=300, help="Shared bucket length in seconds")
    parser.add_argument(
        "--close-offset-seconds",
        type=float,
        default=DEFAULT_CLOSE_OFFSET_S,
        help="Shift bucket close earlier by this many seconds to match venue timing (default: 1.0)",
    )
    parser.add_argument("--refresh-seconds", type=float, default=1.0, help="UI refresh cadence in seconds")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=1.0,
        help="Polling cadence in seconds for REST-based live sources like Coinbase/Kraken/Gemini/Bitstamp/OKX",
    )
    parser.add_argument("--sigma-fallback", type=float, default=0.0003, help="Fallback sigma per sqrt(second)")
    parser.add_argument("--stale-seconds", type=float, default=15.0, help="Mark stream stale after this many seconds")
    parser.add_argument("--mc-paths", type=int, default=MC_PATHS, help=f"Monte Carlo paths (default: {MC_PATHS})")
    parser.add_argument(
        "--calib-hours",
        type=float,
        default=CALIB_WINDOW_S / 3600.0,
        help="Hours of completed 10s candles used for Kou calibration",
    )
    parser.add_argument(
        "--display-source-overrides",
        default="",
        help="Comma-separated symbol=source overrides for display feed, e.g. ethusdt=coinbase-usd",
    )
    parser.add_argument(
        "--model-source-overrides",
        default="",
        help="Comma-separated symbol=source overrides for model feed, e.g. ethusdt=coinbase-usd",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable stream reconnect logs")
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    raw_symbols = [item.strip().lower() for item in args.symbols.split(",") if item.strip()]
    symbols = raw_symbols or ["ethusdt", "xrpusdt"]
    bucket_seconds = max(30, int(args.bucket_seconds))
    close_offset_s = max(0.0, float(args.close_offset_seconds))
    refresh_seconds = max(0.2, float(args.refresh_seconds))
    poll_seconds = max(0.5, float(args.poll_seconds))
    stale_seconds = max(1.0, float(args.stale_seconds))
    calib_window_s = max(1800, int(float(args.calib_hours) * 3600.0))
    mc_paths = max(1, int(args.mc_paths))
    display_overrides = _parse_source_overrides(args.display_source_overrides)
    model_overrides = _parse_source_overrides(args.model_source_overrides)

    monitors = [
        WebSymbolMonitor(
            symbol=symbol,
            bucket_seconds=bucket_seconds,
            close_offset_s=close_offset_s,
            sigma_fallback=float(args.sigma_fallback),
            stale_after_s=stale_seconds,
            mc_paths=mc_paths,
            calib_window_s=calib_window_s,
            poll_seconds=poll_seconds,
            display_source_name=display_overrides.get(symbol),
            model_source_name=model_overrides.get(symbol),
        )
        for symbol in symbols
    ]
    state = SharedState()
    server = run_server(args.host, int(args.port), state, monitors)
    logging.warning("dashboard ready at http://%s:%d", args.host, int(args.port))

    stream_tasks = []
    for monitor in monitors:
        for idx, stream in enumerate(monitor.runtime_streams()):
            stream_name = monitor.display_source_name if idx == 0 else monitor.model_source_name
            stream_tasks.append(asyncio.create_task(stream.run(), name=f"{stream_name}:{monitor.symbol}:{idx}"))
    publish_task = asyncio.create_task(
        publish_loop(
            state,
            monitors,
            bucket_seconds=bucket_seconds,
            refresh_seconds=refresh_seconds,
            close_offset_s=close_offset_s,
        ),
        name="publisher",
    )

    try:
        await publish_task
    finally:
        for monitor in monitors:
            for stream in monitor.runtime_streams():
                stream.stop()
        for task in stream_tasks:
            task.cancel()
        await asyncio.gather(*stream_tasks, return_exceptions=True)
        server.shutdown()
        server.server_close()

    return 0


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.verbose else logging.ERROR,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        return 0


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Kou Compact Dual Monitor</title>
  <style>
    :root {
      --bg: #f2efe7;
      --ink: #18202a;
      --muted: #6d7682;
      --panel: rgba(255, 255, 255, 0.74);
      --border: rgba(24, 32, 42, 0.12);
      --shadow: 0 16px 38px rgba(24, 32, 42, 0.10);
      --teal: #0f766e;
      --teal-soft: rgba(15, 118, 110, 0.10);
      --amber: #b45309;
      --amber-soft: rgba(180, 83, 9, 0.10);
      --red: #b42318;
      --red-soft: rgba(180, 35, 24, 0.10);
      --blue: #0f4c81;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top, rgba(15, 118, 110, 0.10), transparent 28%),
        radial-gradient(circle at bottom right, rgba(15, 76, 129, 0.08), transparent 22%),
        linear-gradient(180deg, #f5f1ea 0%, #efe9df 100%);
    }
    .app {
      width: min(100vw, 430px);
      min-height: 100vh;
      margin: 0 auto;
      padding: 14px 12px 20px;
    }
    .shell {
      min-height: calc(100vh - 28px);
      border: 1px solid rgba(24, 32, 42, 0.08);
      border-radius: 28px;
      padding: 14px;
      background: rgba(255, 255, 255, 0.45);
      backdrop-filter: blur(18px);
      box-shadow: 0 20px 48px rgba(24, 32, 42, 0.10);
    }
    .top {
      padding: 14px;
      border-radius: 20px;
      background: linear-gradient(135deg, rgba(15, 118, 110, 0.12), rgba(15, 76, 129, 0.10));
      border: 1px solid rgba(15, 76, 129, 0.12);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.45);
    }
    .eyebrow {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 10px;
      font-size: 11px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .countdown {
      display: flex;
      align-items: end;
      justify-content: space-between;
      margin-top: 8px;
      gap: 10px;
    }
    .countdown-value {
      font-size: 48px;
      line-height: 0.95;
      font-weight: 700;
      letter-spacing: -0.05em;
    }
    .countdown-meta {
      text-align: right;
      font-size: 12px;
      color: var(--muted);
    }
    .progress {
      height: 8px;
      border-radius: 999px;
      margin-top: 10px;
      overflow: hidden;
      background: rgba(24, 32, 42, 0.08);
    }
    .progress > span {
      display: block;
      height: 100%;
      width: 0%;
      border-radius: inherit;
      background: linear-gradient(90deg, #0f766e, #16a34a);
      transition: width 0.4s ease;
    }
    .stack {
      display: grid;
      gap: 12px;
      margin-top: 14px;
    }
    .asset-card {
      padding: 14px;
      border-radius: 22px;
      background: var(--panel);
      border: 1px solid var(--border);
      box-shadow: var(--shadow);
    }
    .asset-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: start;
    }
    .asset-name {
      font-size: 22px;
      line-height: 1;
      font-weight: 800;
      letter-spacing: -0.04em;
    }
    .asset-sub {
      margin-top: 4px;
      font-size: 12px;
      color: var(--muted);
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 11px;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      border: 1px solid rgba(24, 32, 42, 0.10);
      background: rgba(255, 255, 255, 0.55);
      white-space: nowrap;
    }
    .state-live { color: var(--teal); background: var(--teal-soft); }
    .state-stale { color: var(--amber); background: var(--amber-soft); }
    .state-boot { color: var(--muted); }
    .state-boot, .state-bs { background: rgba(24, 32, 42, 0.05); }
    .state-red { color: var(--red); background: var(--red-soft); }
    .price-row {
      display: flex;
      justify-content: space-between;
      align-items: end;
      margin-top: 12px;
      gap: 10px;
    }
    .trade-score {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin-top: 12px;
      padding: 10px 12px;
      border-radius: 16px;
      border: 1px solid rgba(24, 32, 42, 0.08);
      background: rgba(255, 255, 255, 0.60);
    }
    .trade-score.good {
      background: linear-gradient(135deg, rgba(15, 118, 110, 0.16), rgba(22, 163, 74, 0.10));
      border-color: rgba(15, 118, 110, 0.20);
    }
    .trade-score.ok {
      background: linear-gradient(135deg, rgba(15, 76, 129, 0.12), rgba(255, 255, 255, 0.60));
      border-color: rgba(15, 76, 129, 0.16);
    }
    .trade-score.careful {
      background: linear-gradient(135deg, rgba(180, 83, 9, 0.14), rgba(255, 255, 255, 0.58));
      border-color: rgba(180, 83, 9, 0.18);
    }
    .trade-score.avoid {
      background: linear-gradient(135deg, rgba(180, 35, 24, 0.15), rgba(255, 255, 255, 0.58));
      border-color: rgba(180, 35, 24, 0.18);
    }
    .trade-score-label {
      font-size: 11px;
      letter-spacing: 0.11em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .trade-score-meta {
      margin-top: 4px;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: -0.02em;
    }
    .trade-score-value {
      text-align: right;
      font-size: 28px;
      line-height: 0.95;
      font-weight: 800;
      letter-spacing: -0.05em;
    }
    .trade-score-sub {
      margin-top: 3px;
      text-align: right;
      font-size: 11px;
      letter-spacing: 0.09em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .price {
      font-size: 34px;
      line-height: 0.95;
      font-weight: 800;
      letter-spacing: -0.05em;
    }
    .delta {
      padding: 6px 10px;
      border-radius: 12px;
      font-size: 13px;
      font-weight: 700;
      background: rgba(24, 32, 42, 0.05);
    }
    .delta.up { color: var(--teal); background: var(--teal-soft); }
    .delta.down { color: var(--red); background: var(--red-soft); }
    .prob-grid {
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 10px;
      margin-top: 14px;
    }
    .prob-box, .compare-box {
      position: relative;
      padding: 12px;
      border-radius: 18px;
      background: rgba(24, 32, 42, 0.04);
      border: 1px solid rgba(24, 32, 42, 0.06);
    }
    .prob-box.signal-yes {
      border: 3px solid rgba(15, 118, 110, 0.78);
      background: linear-gradient(180deg, rgba(15, 118, 110, 0.14), rgba(24, 32, 42, 0.04));
      box-shadow:
        0 0 0 3px rgba(15, 118, 110, 0.16),
        0 10px 24px rgba(15, 118, 110, 0.18),
        inset 0 0 0 1px rgba(255, 255, 255, 0.34);
    }
    .prob-box.signal-no {
      border: 3px solid rgba(180, 35, 24, 0.78);
      background: linear-gradient(180deg, rgba(180, 35, 24, 0.14), rgba(24, 32, 42, 0.04));
      box-shadow:
        0 0 0 3px rgba(180, 35, 24, 0.16),
        0 10px 24px rgba(180, 35, 24, 0.18),
        inset 0 0 0 1px rgba(255, 255, 255, 0.34);
    }
    .prob-label {
      font-size: 11px;
      letter-spacing: 0.11em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .prob-value {
      margin-top: 4px;
      font-size: 30px;
      line-height: 1;
      font-weight: 800;
      letter-spacing: -0.05em;
    }
    .prob-meta {
      margin-top: 6px;
      font-size: 12px;
      color: var(--muted);
    }
    .prob-track {
      height: 8px;
      margin-top: 10px;
      border-radius: 999px;
      overflow: hidden;
      background: rgba(24, 32, 42, 0.08);
    }
    .prob-track > span {
      display: block;
      height: 100%;
      width: 50%;
      border-radius: inherit;
      background: linear-gradient(90deg, #d97706, #0f766e);
      transition: width 0.35s ease;
    }
    .signal-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      margin-top: 8px;
    }
    .signal-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 8px;
      border-radius: 999px;
      font-size: 10px;
      letter-spacing: 0.10em;
      text-transform: uppercase;
      border: 1px solid rgba(24, 32, 42, 0.08);
      background: rgba(255, 255, 255, 0.68);
      color: var(--muted);
    }
    .signal-badge.buy-yes {
      color: var(--teal);
      background: var(--teal-soft);
      border-color: rgba(15, 118, 110, 0.18);
    }
    .signal-badge.buy-no {
      color: var(--red);
      background: var(--red-soft);
      border-color: rgba(180, 35, 24, 0.18);
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
      margin-top: 12px;
    }
    .metric {
      padding: 10px 11px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.52);
      border: 1px solid rgba(24, 32, 42, 0.06);
    }
    .metric span {
      display: block;
      font-size: 11px;
      letter-spacing: 0.09em;
      text-transform: uppercase;
      color: var(--muted);
    }
    .metric strong {
      display: block;
      margin-top: 5px;
      font-size: 17px;
      line-height: 1.1;
      font-weight: 700;
      letter-spacing: -0.03em;
    }
    .footer-note {
      margin-top: 12px;
      font-size: 12px;
      color: var(--muted);
    }
    .meta-footer {
      margin-top: 14px;
      text-align: center;
      font-size: 11px;
      color: var(--muted);
    }
  </style>
</head>
<body>
  <div class="app">
    <div class="shell">
      <section class="top">
        <div class="eyebrow">
          <span>Kou Live Dual</span>
          <span id="bucketLabel">5m bucket</span>
        </div>
        <div class="countdown">
          <div class="countdown-value" id="countdownValue">--:--</div>
          <div class="countdown-meta">
            <div>shared expiry</div>
            <div id="bucketEnd">--:--:-- UTC</div>
          </div>
        </div>
        <div class="progress"><span id="topProgress"></span></div>
      </section>
      <main class="stack" id="assetStack"></main>
      <div class="meta-footer" id="footerMeta">waiting for stream...</div>
    </div>
  </div>
  <script>
    const stack = document.getElementById('assetStack');
    const countdownValue = document.getElementById('countdownValue');
    const bucketLabel = document.getElementById('bucketLabel');
    const bucketEnd = document.getElementById('bucketEnd');
    const topProgress = document.getElementById('topProgress');
    const footerMeta = document.getElementById('footerMeta');
    let browserChainlinkWS = null;
    let browserChainlinkReconnect = null;
    const browserChainlinkSymbols = new Set();
    const BROWSER_POLY_WATCHDOG_MS = 6000;
    const BROWSER_POLY_WATCHDOG_POLL_MS = 1000;
    const BROWSER_POLY_RECONNECT_MS = 500;
    const BROWSER_POLY_STALE_CLOSE_S = 3.0;

    function fmtClock(ts) {
      if (!ts) return '--:--:-- UTC';
      return new Date(ts * 1000).toISOString().slice(11, 19) + ' UTC';
    }

    function fmtCountdown(seconds) {
      if (seconds == null) return '--:--';
      const s = Math.max(0, Math.round(seconds));
      const mm = String(Math.floor(s / 60)).padStart(2, '0');
      const ss = String(s % 60).padStart(2, '0');
      return `${mm}:${ss}`;
    }

    function fmtPrice(value) {
      if (value == null) return '-';
      const abs = Math.abs(value);
      if (abs >= 1000) return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
      if (abs >= 100) return value.toLocaleString(undefined, { minimumFractionDigits: 3, maximumFractionDigits: 3 });
      if (abs >= 1) return value.toLocaleString(undefined, { minimumFractionDigits: 4, maximumFractionDigits: 4 });
      if (abs >= 0.1) return value.toLocaleString(undefined, { minimumFractionDigits: 5, maximumFractionDigits: 5 });
      if (abs >= 0.01) return value.toLocaleString(undefined, { minimumFractionDigits: 6, maximumFractionDigits: 6 });
      return value.toLocaleString(undefined, { minimumFractionDigits: 8, maximumFractionDigits: 8 });
    }

    function fmtPct01(value) {
      if (value == null) return '-';
      return `${(value * 100).toFixed(1)}%`;
    }

    function fmtPct100(value) {
      if (value == null) return '-';
      return `${value.toFixed(1)}%`;
    }

    function fmtSigned(value, suffix = '') {
      if (value == null) return '-';
      const sign = value >= 0 ? '+' : '';
      return `${sign}${value.toFixed(1)}${suffix}`;
    }

    function fmtAge(value) {
      if (value == null) return '-';
      return `${value.toFixed(1)}s`;
    }

    function stateClass(state, model) {
      if (state === 'LIVE') return 'state-live';
      if (state === 'STALE') return 'state-stale';
      if (state === 'BOOT') return 'state-boot';
      if (model === 'BS') return 'state-bs';
      return 'state-boot';
    }

    function modelLabel(model) {
      if (model === 'KOU-WARM') return 'KOU~';
      return model || '-';
    }

    function deltaClass(deltaBps) {
      if (deltaBps == null) return '';
      return deltaBps >= 0 ? 'up' : 'down';
    }

    function tradeScoreClass(label) {
      if (label === 'GOOD') return 'trade-score good';
      if (label === 'OK') return 'trade-score ok';
      if (label === 'CAREFUL') return 'trade-score careful';
      return 'trade-score avoid';
    }

    function signalClass(signal) {
      if (signal === 'BUY_YES') return 'signal-yes';
      if (signal === 'BUY_NO') return 'signal-no';
      return '';
    }

    function signalBadgeClass(signal) {
      if (signal === 'BUY_YES') return 'signal-badge buy-yes';
      if (signal === 'BUY_NO') return 'signal-badge buy-no';
      return 'signal-badge';
    }

    function signalText(asset) {
      if (asset.signal === 'BUY_YES') return 'Buy Yes';
      if (asset.signal === 'BUY_NO') return 'Buy No';
      return 'No Signal';
    }

    function chainlinkSymbolForAsset(symbol) {
      if (!symbol) return null;
      return String(symbol).toLowerCase().replace('usdt', '/usd');
    }

    async function pushTick(symbol, price, ts) {
      try {
        await fetch('/api/push_tick', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          cache: 'no-store',
          keepalive: true,
          body: JSON.stringify({ symbol, price, ts }),
        });
      } catch (_) {}
    }

    function binanceSymbolForAsset(symbol) {
      if (!symbol) return null;
      return String(symbol).toLowerCase().replace('/usd', 'usdt');
    }

    function subscribeBrowserPolymarket(symbols) {
      if (!browserChainlinkWS || browserChainlinkWS.readyState !== WebSocket.OPEN || !symbols.length) return;
      const subscriptions = symbols.flatMap(symbol => {
        const binanceSym = symbol.replace('/usd', 'usdt');
        return [
          { topic: 'crypto_prices_chainlink', type: '*', filters: `{\\"symbol\\":\\"${symbol}\\"}` },
          { topic: 'crypto_prices_chainlink', type: '*', filters: `{\\"symbol\\":\\"${symbol.toUpperCase()}\\"}` },
          { topic: 'crypto_prices', type: 'update', filters: `{\\"symbol\\":\\"${binanceSym}\\"}` },
          { topic: 'crypto_prices', type: 'update', filters: `{\\"symbol\\":\\"${binanceSym.toUpperCase()}\\"}` },
        ];
      });
      browserChainlinkWS.send(JSON.stringify({ action: 'subscribe', subscriptions }));
    }

    function ensureBrowserChainlink(symbol) {
      const chainlinkSymbol = chainlinkSymbolForAsset(symbol);
      if (!chainlinkSymbol) return;
      const wasKnown = browserChainlinkSymbols.has(chainlinkSymbol);
      browserChainlinkSymbols.add(chainlinkSymbol);
      if (browserChainlinkWS && browserChainlinkWS.readyState === WebSocket.OPEN && !wasKnown) {
        subscribeBrowserPolymarket([chainlinkSymbol]);
        return;
      }
      if (browserChainlinkWS) return;

      browserChainlinkWS = new WebSocket('wss://ws-live-data.polymarket.com');
      let pingTimer = null;
      let watchdogTimer = null;
      let lastMsgAt = Date.now();

      function resetWatchdog() {
        lastMsgAt = Date.now();
        if (watchdogTimer) clearInterval(watchdogTimer);
        watchdogTimer = setInterval(() => {
          if (Date.now() - lastMsgAt > BROWSER_POLY_WATCHDOG_MS) {
            console.warn('[polymarket] watchdog: no message for 6s, force-closing');
            try { browserChainlinkWS.close(); } catch (_) {}
          }
        }, BROWSER_POLY_WATCHDOG_POLL_MS);
      }

      browserChainlinkWS.addEventListener('open', () => {
        subscribeBrowserPolymarket(Array.from(browserChainlinkSymbols));
        resetWatchdog();
        pingTimer = setInterval(() => { try { browserChainlinkWS.send('PING'); } catch (_) {} }, 5000);
      });
      browserChainlinkWS.addEventListener('message', event => {
        lastMsgAt = Date.now();
        try {
          const data = JSON.parse(event.data);
          if (!data.payload) return;
          const topic = data.topic;
          let outSymbol = null;
          if (topic === 'crypto_prices_chainlink') {
            const sym = String(data.payload.symbol || data.payload.asset || '').toLowerCase();
            if (!browserChainlinkSymbols.has(sym)) return;
            outSymbol = sym.replace('/usd', 'usdt');
          } else if (topic === 'crypto_prices') {
            const sym = String(data.payload.symbol || data.payload.asset || '').toLowerCase();
            const chainSym = sym.replace('usdt', '/usd');
            if (!browserChainlinkSymbols.has(chainSym)) return;
            outSymbol = sym;
          } else {
            return;
          }
          const rows = Array.isArray(data.payload.data) ? data.payload.data : [data.payload];
          let latest = null;
          for (const row of rows) {
            if (!row || row.value == null) continue;
            const price = parseFloat(row.value);
            if (Number.isNaN(price) || price <= 0) continue;
            const rawTs = row.timestamp ?? row.updatedAt;
            let ts = Date.now() / 1000;
            if (rawTs != null) {
              const parsedTs = Number(rawTs);
              if (Number.isFinite(parsedTs)) ts = parsedTs > 10000000000 ? parsedTs / 1000 : parsedTs;
            }
            if (!latest || ts >= latest.ts) latest = { price, ts };
          }
          if (!latest || !outSymbol) return;
          pushTick(outSymbol, latest.price, latest.ts);
        } catch (_) {}
      });
      browserChainlinkWS.addEventListener('close', () => {
        if (pingTimer) clearInterval(pingTimer);
        if (watchdogTimer) clearInterval(watchdogTimer);
        browserChainlinkWS = null;
        if (browserChainlinkReconnect) clearTimeout(browserChainlinkReconnect);
        if (browserChainlinkSymbols.size) {
          browserChainlinkReconnect = setTimeout(
            () => ensureBrowserChainlink(Array.from(browserChainlinkSymbols)[0].replace('/usd', 'usdt')),
            BROWSER_POLY_RECONNECT_MS
          );
        }
      });
      browserChainlinkWS.addEventListener('error', () => { try { browserChainlinkWS.close(); } catch (_) {} });
    }

    window.addEventListener('online', () => {
      if (browserChainlinkWS) {
        try { browserChainlinkWS.close(); } catch (_) {}
      } else if (browserChainlinkSymbols.size) {
        browserChainlinkReconnect = setTimeout(
          () => ensureBrowserChainlink(Array.from(browserChainlinkSymbols)[0].replace('/usd', 'usdt')),
          BROWSER_POLY_RECONNECT_MS
        );
      }
    });

    function renderAssetCard(asset) {
      const fill = asset.kou_yes == null ? 50 : Math.max(0, Math.min(100, asset.kou_yes * 100));
      const delta = fmtSigned(asset.delta_bps, 'bps');
      const sub = `K ${fmtPrice(asset.strike)} · age ${asset.age_s == null ? '-' : asset.age_s.toFixed(1) + 's'}`;
      return `
        <section class="asset-card">
          <div class="asset-head">
            <div>
              <div class="asset-name">${asset.name}</div>
              <div class="asset-sub">${sub}</div>
            </div>
            <div class="pill ${stateClass(asset.state, asset.model)}">${asset.state} · ${modelLabel(asset.model)}</div>
          </div>
          <div class="price-row">
            <div class="price">${fmtPrice(asset.price)}</div>
            <div class="delta ${deltaClass(asset.delta_bps)}">${delta}</div>
          </div>
          <div class="${tradeScoreClass(asset.trade_score_label)}">
            <div>
              <div class="trade-score-label">Safety</div>
              <div class="trade-score-meta">${asset.trade_score_label || 'WAIT'}</div>
            </div>
            <div>
              <div class="trade-score-value">${asset.trade_score == null ? '-' : asset.trade_score}</div>
              <div class="trade-score-sub">${asset.trade_score_reason || 'waiting'}</div>
            </div>
          </div>
          <div class="prob-grid">
            <div class="prob-box ${signalClass(asset.signal)}">
              <div class="prob-label">Kou yes</div>
              <div class="prob-value">${fmtPct01(asset.kou_yes)}</div>
              <div class="prob-meta">NO ${asset.kou_yes == null ? '-' : fmtPct100((1 - asset.kou_yes) * 100)}</div>
              <div class="prob-track"><span style="width:${fill}%"></span></div>
              <div class="signal-row">
                <div class="${signalBadgeClass(asset.signal)}">${signalText(asset)}</div>
                <div class="prob-meta">${asset.signal_hold_s == null ? '' : asset.signal_hold_s.toFixed(1) + 's'}</div>
              </div>
            </div>
            <div class="compare-box">
              <div class="prob-label">BS compare</div>
              <div class="prob-value" style="font-size:22px">${fmtPct01(asset.bs_yes)}</div>
              <div class="prob-meta">edge ${fmtSigned(asset.edge_pp, 'pp')}</div>
            </div>
          </div>
          <div class="metrics">
            <div class="metric"><span>Late policy</span><strong>${asset.late_policy_level == null ? '-' : asset.late_policy_level.replaceAll('_', ' ') + (asset.late_policy_bucket_s == null ? '' : ' · ' + asset.late_policy_bucket_s + 's')}</strong></div>
            <div class="metric"><span>Policy z</span><strong>${asset.late_policy_margin_z == null ? '-' : asset.late_policy_margin_z.toFixed(2)}</strong></div>
            <div class="metric"><span>Heuristic</span><strong>${asset.base_trade_score == null ? '-' : asset.base_trade_score + ' · ' + (asset.base_trade_score_label || 'WAIT')}</strong></div>
            <div class="metric"><span>Override</span><strong>${asset.policy_override ? 'YES' : 'no'}</strong></div>
            <div class="metric"><span>Vol 30m</span><strong>${asset.vol_30m_bp_1m == null ? '-' : asset.vol_30m_bp_1m.toFixed(1) + ' bp/m'}</strong></div>
            <div class="metric"><span>Vol 1h</span><strong>${asset.vol_1h_bp_1m == null ? '-' : asset.vol_1h_bp_1m.toFixed(1) + ' bp/m'}</strong></div>
            <div class="metric"><span>Jump 10s · 10m</span><strong>${asset.jump_10s_10m_rate == null ? '-' : fmtPct01(asset.jump_10s_10m_rate) + ' · ' + asset.jump_10s_10m_count}</strong></div>
            <div class="metric"><span>Jump 30s · 15m</span><strong>${asset.jump_30s_15m_rate == null ? '-' : fmtPct01(asset.jump_30s_15m_rate) + ' · ' + asset.jump_30s_15m_count}</strong></div>
          </div>
          <div class="footer-note">${asset.footer}</div>
        </section>
      `;
    }

    let pollMs = 700;

    function bucketText(bucketSeconds) {
      if (!bucketSeconds) return 'bucket';
      if (bucketSeconds % 60 === 0) return `${Math.round(bucketSeconds / 60)}m bucket`;
      return `${bucketSeconds}s bucket`;
    }

    async function refresh() {
      try {
        const res = await fetch('/api/snapshot', { cache: 'no-store' });
        const data = await res.json();
        for (const asset of (data.assets || [])) {
          if (asset.display_source === 'browser-poly-chainlink') {
            ensureBrowserChainlink(asset.symbol);
            if (
              asset.age_s != null &&
              asset.age_s > BROWSER_POLY_STALE_CLOSE_S &&
              browserChainlinkWS &&
              browserChainlinkWS.readyState === WebSocket.OPEN
            ) {
              try { browserChainlinkWS.close(); } catch (_) {}
            }
          }
        }
        countdownValue.textContent = fmtCountdown(data.time_left_s);
        bucketLabel.textContent = bucketText(data.bucket_seconds || 300);
        bucketEnd.textContent = fmtClock(data.bucket_end);
        topProgress.style.width = `${Math.max(0, Math.min(100, (data.progress || 0) * 100))}%`;
        stack.innerHTML = (data.assets || []).map(renderAssetCard).join('');
        pollMs = Math.max(250, Math.round((data.refresh_seconds || 0.7) * 1000));
        footerMeta.textContent = data.updated_at
          ? `updated ${fmtClock(data.updated_at).replace(' UTC', '')} · ${location.host}`
          : 'waiting for stream...';
      } catch (err) {
        footerMeta.textContent = 'connection lost';
      }
    }

    async function loop() {
      await refresh();
      window.setTimeout(loop, pollMs);
    }

    loop();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
