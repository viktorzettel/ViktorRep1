import json
import os
import time
import threading
import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import requests
try:
    import websockets
    HAS_WS = True
except Exception:
    HAS_WS = False

# =========================
# CONFIG
# =========================
BINANCE_BASE = "https://api.binance.com"
BINANCE_SYMBOL = "BTCUSDT"
BINANCE_INTERVAL_1M = "1m"
BINANCE_INTERVAL_15M = "15m"
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade"

MODEL_PATHS = ["analysis_output_model/model.json", "model.json"]
SUMMARY_PATHS = ["analysis_output_model/test_summary_by_regime_time.csv", "test_summary_by_regime_time.csv"]

UPDATE_INTERVAL = 30  # seconds
LAST_N_MINUTES = 5
PRICE_SOURCE = "open"  # "open" aligns with training; use "last" only if retrained
USE_LAST_COMPLETED_MINUTE = True  # avoids using partial in-progress minute data
MANUAL_STRIKE = True  # paste Polymarket "price to beat" each 15m candle
MANUAL_STRIKE_PROMPT = "Paste price-to-beat for this 15m candle (e.g., 75705.97): "
MODEL_DELTA_SOURCE = "snapshot"  # keep model features aligned to training
DECISION_DELTA_SOURCE = "current"  # use live price for decisions

USE_DELTA_THRESHOLDS = True  # require delta thresholds from historical CI rules (if available)
THRESHOLD_PATHS = [
    "analysis_output_90days/full_regime_reversal_probs.csv",
    "full_regime_reversal_probs.csv",
]
ENABLE_85_RULE = True  # show a riskier 85% decision (prob <= 15%)
ENABLE_80_RULE = True  # show a riskier 80% decision (prob <= 20%)

# Live price feed
PRICE_PROVIDER = "coinbase"  # binance_ws, binance_rest, coinbase, kraken, bitstamp, median
USE_BINANCE_WS = True
WS_STALE_SEC = 5
WS_RECONNECT_DELAY = 2
SHOW_PRICE_SOURCE = True

TICK_BUFFER_SEC = 20 * 60
TICK_STATS_WINDOW_SEC = 60
ER_TREND = 0.35
ER_CHOP = 0.20
MIN_TICK_POINTS = 5
MICRO_GATES = True
BLOCK_AGAINST_TREND = True
CHOP_POS_YES_MIN = 0.6
CHOP_POS_NO_MAX = 0.4
PULLBACK_MAX_FRAC = 0.6
BOUNCE_MAX_FRAC = 0.6
MIN_RANGE_ATR_MULT = 0.5
SHOW_PRICE_SANITY = True

# Logging
LOG_MODE = "compact"  # "compact" or "full"

# Time-left handling for 30s updates
# "floor": keep the minute bucket until the next minute starts (most conservative, training-aligned)
# "next_on_half": when >=30s into the minute, shift to the next minute bucket (more aggressive)
TIME_LEFT_MODE = "floor"

ATR_PERIOD = 28
RET_VOL_WINDOW = 60
VOL_Z_WINDOW = 60

# Tick-level monitoring
TICK_MODE = True
TICK_INTERVAL_SEC = 1
TICK_WINDOW_MINUTES = 5
USE_TIME_LEFT_NOW_BUCKET = True
TIME_LEFT_BUCKET_MODE = "ceil"  # "ceil" is more conservative than "floor"
MIN_CONFIRM_SECONDS = 3

SCAN_POLYMARKET = True
POLYMARKET_EVENTS_URL = "https://gamma-api.polymarket.com/events"
POLYMARKET_KEYWORDS = ["bitcoin", "up or down"]
POLYMARKET_MAX_MINUTES_AHEAD = 20  # only consider markets expiring soon

MIN_TRADES_FOR_ALLOW = 30  # for summary-based gating


# =========================
# MODEL LOADING
# =========================
def load_model() -> Dict[str, Any]:
    for p in MODEL_PATHS:
        if os.path.exists(p):
            with open(p, "r") as f:
                model = json.load(f)
            print(f"Loaded model from {p}")
            return model
    raise FileNotFoundError(f"No model.json found. Tried: {MODEL_PATHS}")


def load_summary_rules() -> Dict[float, set]:
    for p in SUMMARY_PATHS:
        if os.path.exists(p):
            df = pd.read_csv(p)
            thresholds = sorted({float(x) for x in df["threshold"].dropna().unique()})
            rules: Dict[float, set] = {t: set() for t in thresholds}
            for _, row in df.iterrows():
                thr = float(row["threshold"])
                if thr not in rules:
                    continue
                if int(row.get("trades", 0)) < MIN_TRADES_FOR_ALLOW:
                    continue
                if float(row["reversal_rate"]) <= thr:
                    rules[thr].add((row["regime"], int(row["time_left"])))
            print(f"Loaded summary rules from {p}")
            return rules
    print("No summary rules file found. Allowing all regimes/time_left.")
    return {0.05: set(), 0.10: set(), 0.15: set(), 0.20: set()}


def load_delta_thresholds() -> Optional[Dict[str, Dict[int, Dict[str, float]]]]:
    for p in THRESHOLD_PATHS:
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p)
        required_cols = {"vol_regime", "delta_bin", "time_left"}
        if not required_cols.issubset(df.columns):
            continue

        thresholds: Dict[str, Dict[int, Dict[str, float]]] = {}
        for regime in df["vol_regime"].dropna().unique():
            thresholds[regime] = {}
            for tl in range(1, LAST_N_MINUTES + 1):
                slice_df = df[(df["vol_regime"] == regime) & (df["time_left"] == tl)]
                if slice_df.empty:
                    continue

                def find_thresh(conf: int, side: str) -> Optional[float]:
                    ci_col = f"ci_high_{conf}"
                    if ci_col not in slice_df.columns:
                        return None
                    max_rev = 1 - (conf / 100)
                    if side == "YES":
                        pos = slice_df[(slice_df["delta_bin"] > 0) & (slice_df[ci_col] <= max_rev)]
                        return float(pos["delta_bin"].min()) if not pos.empty else None
                    neg = slice_df[(slice_df["delta_bin"] < 0) & (slice_df[ci_col] <= max_rev)]
                    return float(neg["delta_bin"].max()) if not neg.empty else None

                thresholds[regime][tl] = {
                    "YES_95": find_thresh(95, "YES"),
                    "YES_90": find_thresh(90, "YES"),
                    "YES_85": find_thresh(85, "YES"),
                    "YES_80": find_thresh(80, "YES"),
                    "NO_95": find_thresh(95, "NO"),
                    "NO_90": find_thresh(90, "NO"),
                    "NO_85": find_thresh(85, "NO"),
                    "NO_80": find_thresh(80, "NO"),
                }

        print(f"Loaded delta thresholds from {p}")
        return thresholds

    print("No delta threshold file found. Skipping delta threshold gating.")
    return None


# =========================
# POLYMARKET MARKET SCAN
# =========================
def fetch_active_btc_market() -> Optional[Dict[str, Any]]:
    try:
        resp = requests.get(
            POLYMARKET_EVENTS_URL,
            params={"active": "true", "limit": 200},
            timeout=10,
        )
        resp.raise_for_status()
        events = resp.json()
    except Exception as e:
        print(f"[Polymarket] Error fetching events: {e}")
        return None

    now = datetime.now(timezone.utc)
    candidates = []
    for e in events:
        title = str(e.get("title", "")).lower()
        if not all(k in title for k in POLYMARKET_KEYWORDS):
            continue
        markets = e.get("markets", [])
        for m in markets:
            end_date = m.get("endDate")
            if not end_date:
                continue
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            except Exception:
                continue
            mins_left = (end_dt - now).total_seconds() / 60.0
            if mins_left < 0 or mins_left > POLYMARKET_MAX_MINUTES_AHEAD:
                continue
            tokens_raw = m.get("clobTokenIds", [])
            if isinstance(tokens_raw, str):
                try:
                    tokens = json.loads(tokens_raw)
                except Exception:
                    tokens = []
            else:
                tokens = tokens_raw
            yes_token = tokens[0] if len(tokens) > 0 else None
            no_token = tokens[1] if len(tokens) > 1 else None
            candidates.append(
                {
                    "title": e.get("title"),
                    "slug": e.get("slug"),
                    "end_time": end_dt,
                    "minutes_left": mins_left,
                    "yes_token": yes_token,
                    "no_token": no_token,
                }
            )

    if not candidates:
        return None
    candidates.sort(key=lambda x: x["minutes_left"])
    return candidates[0]


# =========================
# BINANCE HELPERS
# =========================
def binance_klines(symbol: str, interval: str, limit: int = 200, start_time: Optional[int] = None) -> list:
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_time is not None:
        params["startTime"] = int(start_time)
    resp = requests.get(f"{BINANCE_BASE}/api/v3/klines", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def binance_ticker(symbol: str) -> float:
    resp = requests.get(f"{BINANCE_BASE}/api/v3/ticker/price", params={"symbol": symbol}, timeout=10)
    resp.raise_for_status()
    return float(resp.json()["price"])


def coinbase_spot() -> float:
    resp = requests.get("https://api.coinbase.com/v2/prices/BTC-USD/spot", timeout=10)
    resp.raise_for_status()
    return float(resp.json()["data"]["amount"])


def kraken_ticker() -> float:
    resp = requests.get("https://api.kraken.com/0/public/Ticker", params={"pair": "XBTUSD"}, timeout=10)
    resp.raise_for_status()
    data = resp.json()["result"]
    pair = next(iter(data.keys()))
    return float(data[pair]["c"][0])


def bitstamp_ticker() -> float:
    resp = requests.get("https://www.bitstamp.net/api/v2/ticker/btcusd", timeout=10)
    resp.raise_for_status()
    return float(resp.json()["last"])


def get_price_from_provider(provider: str, stream: Optional["BinanceTradeStream"]) -> tuple[float, str]:
    if provider == "binance_ws":
        if stream is not None:
            p = stream.get_price()
            if p is not None:
                return p, "ws"
        return binance_ticker(BINANCE_SYMBOL), "rest"
    if provider == "binance_rest":
        return binance_ticker(BINANCE_SYMBOL), "rest"
    if provider == "coinbase":
        return coinbase_spot(), "coinbase"
    if provider == "kraken":
        return kraken_ticker(), "kraken"
    if provider == "bitstamp":
        return bitstamp_ticker(), "bitstamp"
    if provider == "median":
        prices = []
        for fn in (coinbase_spot, kraken_ticker, bitstamp_ticker):
            try:
                prices.append(fn())
            except Exception:
                continue
        if prices:
            return float(np.median(prices)), "median"
        return binance_ticker(BINANCE_SYMBOL), "rest"
    return binance_ticker(BINANCE_SYMBOL), "rest"


def get_price_sanity_snapshot(stream: Optional["BinanceTradeStream"]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {"binance_ws": None, "binance_rest": None, "coinbase": None, "kraken": None, "bitstamp": None}
    try:
        if stream is not None:
            out["binance_ws"] = stream.get_price()
    except Exception:
        pass
    try:
        out["binance_rest"] = binance_ticker(BINANCE_SYMBOL)
    except Exception:
        pass
    try:
        out["coinbase"] = coinbase_spot()
    except Exception:
        pass
    try:
        out["kraken"] = kraken_ticker()
    except Exception:
        pass
    try:
        out["bitstamp"] = bitstamp_ticker()
    except Exception:
        pass
    return out


class BinanceTradeStream:
    def __init__(self, url: str) -> None:
        self.url = url
        self.latest_price: Optional[float] = None
        self.latest_ts: Optional[float] = None
        self.ticks: deque = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        price = data.get("p")
                        ts = data.get("T")
                        if price is not None and ts is not None:
                            self.latest_price = float(price)
                            self.latest_ts = float(ts) / 1000.0
                            self._add_tick(self.latest_ts, self.latest_price)
            except Exception:
                await asyncio.sleep(WS_RECONNECT_DELAY)

    def start(self) -> None:
        if not HAS_WS:
            return
        if self._thread and self._thread.is_alive():
            return

        def _thread_main():
            asyncio.run(self._run())

        self._thread = threading.Thread(target=_thread_main, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def get_price(self) -> Optional[float]:
        if self.latest_price is None or self.latest_ts is None:
            return None
        if time.time() - self.latest_ts > WS_STALE_SEC:
            return None
        return self.latest_price

    def _add_tick(self, ts: float, price: float) -> None:
        with self._lock:
            self.ticks.append((ts, price))
            cutoff = ts - TICK_BUFFER_SEC
            while self.ticks and self.ticks[0][0] < cutoff:
                self.ticks.popleft()

    def get_ticks_snapshot(self) -> list:
        with self._lock:
            return list(self.ticks)


def get_current_15m_open() -> tuple[float, datetime]:
    now = datetime.now(timezone.utc)
    interval_ms = 15 * 60 * 1000
    now_ms = int(now.timestamp() * 1000)
    open_time_ms = now_ms - (now_ms % interval_ms)
    klines = binance_klines(BINANCE_SYMBOL, BINANCE_INTERVAL_15M, limit=2, start_time=open_time_ms)
    open_price = None
    for k in klines:
        if int(k[0]) == open_time_ms:
            open_price = float(k[1])
            break
    if open_price is None and klines:
        open_price = float(klines[-1][1])
        open_time_ms = int(klines[-1][0])
    open_time = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc)
    return open_price, open_time


# =========================
# FEATURE ENGINEERING (LIVE)
# =========================
def build_feature_row(
    strike_override: Optional[float] = None,
    open_time_override: Optional[datetime] = None,
    current_price_override: Optional[float] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)

    # 15m open (strike)
    if strike_override is not None and open_time_override is not None:
        strike = float(strike_override)
        open_time = open_time_override
        strike_source = "manual"
    else:
        strike, open_time = get_current_15m_open()
        strike_source = "binance"

    # Current price
    if current_price_override is not None:
        current_price = float(current_price_override)
    else:
        current_price = binance_ticker(BINANCE_SYMBOL)

    # 1m data
    klines_1m = binance_klines(BINANCE_SYMBOL, BINANCE_INTERVAL_1M, limit=200)
    df = pd.DataFrame(
        klines_1m,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_base",
            "taker_quote",
            "ignore",
        ],
    )
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    # Identify minute row
    minute_start = now.replace(second=0, microsecond=0)
    if USE_LAST_COMPLETED_MINUTE:
        minute_ref = minute_start - timedelta(minutes=1)
        # If we are in the very first minute of the 15m candle, fall back to the candle open minute
        if minute_ref < open_time:
            minute_ref = open_time
    else:
        minute_ref = minute_start

    if minute_ref in df.index:
        row = df.loc[minute_ref]
    else:
        idx = df.index[df.index <= minute_ref].max()
        row = df.loc[idx]

    # Basic returns / vol
    df["log_close"] = np.log(df["close"])
    df["log_ret"] = df["log_close"].diff()
    df["ret_1m"] = df["log_ret"]
    df["ret_3m"] = df["log_ret"].rolling(3).sum()
    df["ret_5m"] = df["log_ret"].rolling(5).sum()
    df["ret_std_60"] = df["log_ret"].rolling(RET_VOL_WINDOW).std()

    prev_close = df["close"].shift()
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df["atr"] = tr.rolling(ATR_PERIOD).mean()
    df["atr_pct"] = df["atr"] / df["close"]

    df["log_vol"] = np.log(df["volume"].replace(0, np.nan))
    vol_mean = df["log_vol"].rolling(VOL_Z_WINDOW).mean()
    vol_std = df["log_vol"].rolling(VOL_Z_WINDOW).std()
    df["vol_z"] = (df["log_vol"] - vol_mean) / vol_std

    # Intra-candle features from 15m open
    df["period_start"] = df.index.floor("15min")
    df["cum_high"] = df.groupby("period_start")["high"].cummax()
    df["cum_low"] = df.groupby("period_start")["low"].cummin()
    df["range_since_open"] = df["cum_high"] - df["cum_low"]

    df["cum_vol"] = df.groupby("period_start")["volume"].cumsum()
    df["cum_vwap"] = (df["close"] * df["volume"]).groupby(df["period_start"]).cumsum() / df["cum_vol"]

    # Pull row values
    row = df.loc[row.name]

    # Time left (aligned with training: minute index within candle)
    minutes_in = int((row.name - open_time).total_seconds() / 60)
    time_left_snapshot = max(1, min(15, 15 - minutes_in))
    seconds_into_minute = now.second + now.microsecond / 1_000_000
    if TIME_LEFT_MODE == "next_on_half" and seconds_into_minute >= 30:
        time_left_effective = max(1, time_left_snapshot - 1)
    else:
        time_left_effective = time_left_snapshot
    # Real-time time_left for display (not used in model)
    time_left_now = max(0.0, (open_time + timedelta(minutes=15) - now).total_seconds() / 60.0)

    # Snapshot price (used for model features)
    if PRICE_SOURCE == "open":
        price_snap = float(row["open"])
    else:
        price_snap = current_price

    # Delta + normalized
    delta_snap = price_snap - strike
    delta_current = current_price - strike
    delta = delta_snap if DECISION_DELTA_SOURCE == "snapshot" else delta_current
    delta_model = delta_snap if MODEL_DELTA_SOURCE == "snapshot" else delta_current
    atr = float(row["atr"])
    ret_std = float(row["ret_std_60"])
    price_for_model = price_snap if MODEL_DELTA_SOURCE == "snapshot" else current_price
    delta_norm_atr = delta_model / atr if atr and not np.isnan(atr) else np.nan
    delta_norm_std = (
        delta_model / (ret_std * price_for_model) if ret_std and not np.isnan(ret_std) else np.nan
    )

    # Range/VWAP normalized
    range_norm_atr = float(row["range_since_open"]) / atr if atr and not np.isnan(atr) else np.nan
    vwap_delta = price_snap - float(row["cum_vwap"])
    vwap_delta_norm_atr = vwap_delta / atr if atr and not np.isnan(atr) else np.nan

    return {
        "now": now,
        "open_time": open_time,
        "strike": strike,
        "strike_source": strike_source,
        "current_price": current_price,
        "snapshot_price": price_snap,
        "minute_ref": row.name,
        "time_left_snapshot": time_left_snapshot,
        "time_left_effective": time_left_effective,
        "time_left_now": time_left_now,
        "delta": delta,
        "delta_snap": delta_snap,
        "delta_current": delta_current,
        "delta_model": delta_model,
        "atr": atr,
        "atr_pct": float(row["atr_pct"]),
        "ret_1m": float(row["ret_1m"]),
        "ret_3m": float(row["ret_3m"]),
        "ret_5m": float(row["ret_5m"]),
        "vol_z": float(row["vol_z"]),
        "delta_norm_atr": float(delta_norm_atr),
        "delta_norm_std": float(delta_norm_std),
        "range_norm_atr": float(range_norm_atr),
        "vwap_delta_norm_atr": float(vwap_delta_norm_atr),
    }


def regime_from_quantiles(val: float, q_low: float, q_high: float) -> str:
    if val <= q_low:
        return "Low"
    if val <= q_high:
        return "Med"
    return "High"


def predict_reversal(model: Dict[str, Any], features: Dict[str, Any]) -> float:
    feat_order = model["features"]
    mean = np.array(model["mean"])
    std = np.array(model["std"])
    intercept = float(model["intercept"])
    weights = np.array(model["weights"])

    x = np.array([features.get(f, 0.0) for f in feat_order], dtype=float)
    std = np.where(std == 0, 1.0, std)
    x_s = (x - mean) / std
    z = intercept + np.dot(x_s, weights)
    pred = 1.0 / (1.0 + np.exp(-z))

    calib_x = np.array(model["calibration"]["x"])
    calib_y = np.array(model["calibration"]["y"])
    pred_cal = np.interp(pred, calib_x, calib_y, left=calib_y[0], right=calib_y[-1])
    return float(pred_cal)


def decision_for_conf(
    conf: int,
    pred_reversal: float,
    delta: float,
    time_left: int,
    regime: str,
    allowed: Dict[float, set],
    delta_thresholds: Optional[Dict[str, Dict[int, Dict[str, float]]]] = None,
    micro_stats: Optional[Dict[str, Any]] = None,
    atr_val: Optional[float] = None,
) -> str:
    if time_left < 1 or time_left > LAST_N_MINUTES:
        return "Outside window"
    if delta == 0:
        return "No trade (delta=0)"

    side = "YES" if delta > 0 else "NO"
    prob_map = {95: 0.05, 90: 0.10, 85: 0.15, 80: 0.20}
    prob_cutoff = prob_map.get(conf)
    if prob_cutoff is None:
        return "No trade (unsupported confidence)"

    allow_set = None
    if conf in (95, 90, 85, 80):
        allow_set = allowed.get(prob_cutoff)
    allow = True if allow_set is None else ((not allow_set) or ((regime, time_left) in allow_set))

    if not allow:
        return "No trade (regime/time blocked)"
    if pred_reversal > prob_cutoff:
        return f"No trade (prob>{prob_cutoff * 100:.0f}%)"

    def passes_micro_gates() -> tuple[bool, Optional[str]]:
        if not MICRO_GATES or micro_stats is None:
            return True, None
        rng = micro_stats.get("range_since_open")
        pos = micro_stats.get("position_in_range")
        pullback = micro_stats.get("pullback_from_high")
        bounce = micro_stats.get("bounce_from_low")
        state = micro_stats.get("state")
        if rng is None or np.isnan(rng) or rng <= 0:
            return True, None
        if atr_val is not None and not np.isnan(atr_val):
            if rng < atr_val * MIN_RANGE_ATR_MULT:
                return True, None
        if BLOCK_AGAINST_TREND:
            if state == "TREND_UP" and side == "NO":
                return False, "trend_up"
            if state == "TREND_DOWN" and side == "YES":
                return False, "trend_down"
        if state == "CHOP":
            if side == "YES" and pos is not None and pos < CHOP_POS_YES_MIN:
                return False, "chop_pos"
            if side == "NO" and pos is not None and pos > CHOP_POS_NO_MAX:
                return False, "chop_pos"
        if side == "YES" and pullback is not None and pullback > PULLBACK_MAX_FRAC * rng:
            return False, "pullback"
        if side == "NO" and bounce is not None and bounce > BOUNCE_MAX_FRAC * rng:
            return False, "bounce"
        return True, None

    ok_micro, reason = passes_micro_gates()
    if not ok_micro:
        return f"No trade (micro:{reason})"

    def passes_delta_threshold() -> tuple[bool, Optional[float]]:
        if conf not in (80, 85, 90, 95):
            return True, None
        if not USE_DELTA_THRESHOLDS or not delta_thresholds:
            return True, None
        reg = delta_thresholds.get(regime, {})
        tl = reg.get(time_left, {})
        if not tl:
            return False, None
        key = f"{side}_{conf}"
        thresh = tl.get(key)
        if thresh is None or np.isnan(thresh):
            return False, None
        if side == "YES":
            return delta >= thresh, float(thresh)
        return delta <= thresh, float(thresh)

    ok, thresh = passes_delta_threshold()
    if not ok:
        if thresh is None:
            return f"No trade (no {conf}% threshold)"
        return f"No trade (delta below {conf}% threshold {thresh:.0f})"

    return f"BUY {side} ({conf}% rule)"


def time_left_bucket(now: datetime, open_time: datetime) -> int:
    minutes_left = max(0.0, (open_time + timedelta(minutes=15) - now).total_seconds() / 60.0)
    if TIME_LEFT_BUCKET_MODE == "ceil":
        bucket = int(np.ceil(minutes_left))
    else:
        bucket = int(np.floor(minutes_left))
    bucket = max(1, min(LAST_N_MINUTES, bucket))
    return bucket


def confirm_decision(key: str, decision: str, state: Dict[str, Dict[str, Any]]) -> str:
    if not decision.startswith("BUY"):
        state.pop(key, None)
        return decision
    entry = state.get(key)
    if entry and entry["decision"] == decision:
        entry["count"] += 1
    else:
        entry = {"decision": decision, "count": 1}
        state[key] = entry
    if entry["count"] >= MIN_CONFIRM_SECONDS:
        return f"{decision} (confirmed)"
    return f"{decision} (confirm {entry['count']}/{MIN_CONFIRM_SECONDS})"


def compute_tick_stats(
    ticks: list,
    now_ts: float,
    open_ts: float,
    current_price: float,
) -> Dict[str, Any]:
    # Filter ticks for window
    window_start = now_ts - TICK_STATS_WINDOW_SEC
    window_ticks = [(t, p) for t, p in ticks if t >= window_start]
    stats = {
        "tick_count": len(window_ticks),
        "er_60": np.nan,
        "trend_strength_60": np.nan,
        "sigma_60": np.nan,
        "new_highs_60": 0,
        "new_lows_60": 0,
        "net_change_60": np.nan,
        "range_60": np.nan,
        "high_since_open": np.nan,
        "low_since_open": np.nan,
        "range_since_open": np.nan,
        "position_in_range": np.nan,
        "pullback_from_high": np.nan,
        "bounce_from_low": np.nan,
        "state": "UNKNOWN",
    }

    if len(window_ticks) >= MIN_TICK_POINTS:
        prices = np.array([p for _, p in window_ticks], dtype=float)
        net_change = prices[-1] - prices[0]
        path = np.sum(np.abs(np.diff(prices)))
        er = abs(net_change) / path if path > 0 else 0.0
        sigma = np.std(np.diff(np.log(prices))) if len(prices) > 1 else 0.0
        trend_strength = net_change / (np.std(prices) + 1e-9)
        range_ = np.max(prices) - np.min(prices)

        # New highs/lows count inside window
        max_so_far = -np.inf
        min_so_far = np.inf
        nh = 0
        nl = 0
        for p in prices:
            if p > max_so_far:
                max_so_far = p
                nh += 1
            if p < min_so_far:
                min_so_far = p
                nl += 1

        stats.update(
            {
                "er_60": er,
                "trend_strength_60": trend_strength,
                "sigma_60": sigma,
                "new_highs_60": nh,
                "new_lows_60": nl,
                "net_change_60": net_change,
                "range_60": range_,
            }
        )

        if er < ER_CHOP:
            stats["state"] = "CHOP"
        elif er > ER_TREND and net_change > 0:
            stats["state"] = "TREND_UP"
        elif er > ER_TREND and net_change < 0:
            stats["state"] = "TREND_DOWN"
        else:
            stats["state"] = "NEUTRAL"

    # Since open: local high/low
    since_open_ticks = [p for t, p in ticks if t >= open_ts]
    if since_open_ticks:
        high = float(np.max(since_open_ticks))
        low = float(np.min(since_open_ticks))
        rng = high - low
        pos = (current_price - low) / rng if rng > 0 else 0.5
        stats.update(
            {
                "high_since_open": high,
                "low_since_open": low,
                "range_since_open": rng,
                "position_in_range": pos,
                "pullback_from_high": high - current_price,
                "bounce_from_low": current_price - low,
            }
        )

    return stats


def short_decision(decision: str) -> str:
    if decision.startswith("BUY"):
        side = "YES" if "YES" in decision else "NO"
        conf = "95" if "95%" in decision else "90" if "90%" in decision else "85" if "85%" in decision else "80"
        if "confirmed" in decision:
            status = "C"
        elif "confirm" in decision:
            status = "P"
        else:
            status = ""
        return f"{side}{conf}{status}"
    if decision.startswith("No trade"):
        return "NO"
    return decision


# =========================
# MAIN LOOP
# =========================
def run():
    model = load_model()
    allowed = load_summary_rules()
    delta_thresholds = load_delta_thresholds() if USE_DELTA_THRESHOLDS else None
    stream = None
    if PRICE_PROVIDER == "binance_ws" and USE_BINANCE_WS and HAS_WS:
        stream = BinanceTradeStream(BINANCE_WS_URL)
        stream.start()

    atr_q = model.get("atr_quantiles", [0.0, 0.0])
    vol_q = model.get("vol_z_quantiles", [0.0, 0.0])

    print("Starting BTC 15m decision scanner...")
    manual_strike = None
    manual_open_time = None
    feature_cache = None
    cached_minute_ref = None
    confirm_state: Dict[str, Dict[str, Any]] = {}
    fallback_ticks: deque = deque()
    while True:
        try:
            market = fetch_active_btc_market() if SCAN_POLYMARKET else None

            current_price, price_source = get_price_from_provider(PRICE_PROVIDER, stream)
            sanity_prices = None
            if SHOW_PRICE_SANITY:
                sanity_prices = get_price_sanity_snapshot(stream)

            # Update fallback tick buffer
            now_ts = time.time()
            fallback_ticks.append((now_ts, current_price))
            cutoff = now_ts - TICK_BUFFER_SEC
            while fallback_ticks and fallback_ticks[0][0] < cutoff:
                fallback_ticks.popleft()

            if MANUAL_STRIKE:
                now = datetime.now(timezone.utc)
                open_time = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
                if manual_open_time is None or open_time != manual_open_time:
                    raw = input(f"{MANUAL_STRIKE_PROMPT}").strip()
                    raw = raw.replace("$", "").replace(",", "").strip()
                    manual_strike = float(raw)
                    manual_open_time = open_time
            # Refresh cached features once per minute
            if manual_open_time is None:
                continue

            minute_ref = now.replace(second=0, microsecond=0)
            if USE_LAST_COMPLETED_MINUTE:
                minute_ref = minute_ref - timedelta(minutes=1)
            if cached_minute_ref != minute_ref:
                feature_cache = build_feature_row(
                    strike_override=manual_strike,
                    open_time_override=manual_open_time,
                    current_price_override=current_price,
                )
                cached_minute_ref = feature_cache["minute_ref"]

            if feature_cache is None:
                time.sleep(UPDATE_INTERVAL)
                continue

            # Build live row using cached features + current price
            row = dict(feature_cache)
            row["snapshot_price"] = feature_cache.get("snapshot_price", row.get("snapshot_price"))
            row["now"] = now
            row["current_price"] = current_price
            row["delta_current"] = current_price - row["strike"]
            row["delta"] = row["delta_current"] if DECISION_DELTA_SOURCE == "current" else row["delta_snap"]
            row["time_left_now"] = max(
                0.0, (row["open_time"] + timedelta(minutes=15) - now).total_seconds() / 60.0
            )
            if USE_TIME_LEFT_NOW_BUCKET:
                time_left_for_decision = time_left_bucket(now, row["open_time"])
            else:
                time_left_for_decision = (
                    row["time_left_effective"] if TIME_LEFT_MODE == "next_on_half" else row["time_left_snapshot"]
                )

            vol_regime = regime_from_quantiles(row["atr_pct"], atr_q[0], atr_q[1])
            volu_regime = regime_from_quantiles(row["vol_z"], vol_q[0], vol_q[1])

            # Use effective time_left if we are shifting buckets mid-minute
            time_left_for_model = (
                time_left_for_decision
                if USE_TIME_LEFT_NOW_BUCKET
                else (row["time_left_effective"] if TIME_LEFT_MODE == "next_on_half" else row["time_left_snapshot"])
            )

            # Build model features in required order
            model_features = {
                "delta_norm_atr": row["delta_norm_atr"],
                "delta_norm_std": row["delta_norm_std"],
                "ret_1m": row["ret_1m"],
                "ret_3m": row["ret_3m"],
                "ret_5m": row["ret_5m"],
                "range_norm_atr": row["range_norm_atr"],
                "vwap_delta_norm_atr": row["vwap_delta_norm_atr"],
                "vol_z": row["vol_z"],
                "atr_pct": row["atr_pct"],
                "time_left": time_left_for_model,
                "vol_regime_Low": 1.0 if vol_regime == "Low" else 0.0,
                "vol_regime_Med": 1.0 if vol_regime == "Med" else 0.0,
                "volu_regime_Low": 1.0 if volu_regime == "Low" else 0.0,
                "volu_regime_Med": 1.0 if volu_regime == "Med" else 0.0,
            }

            pred = predict_reversal(model, model_features)
            # Tick stats (trend/chop diagnostics)
            ticks = stream.get_ticks_snapshot() if stream is not None else list(fallback_ticks)
            if not ticks:
                ticks = list(fallback_ticks)
            stats = compute_tick_stats(
                ticks,
                now_ts=now_ts,
                open_ts=row["open_time"].timestamp(),
                current_price=current_price,
            )

            decision_95 = decision_for_conf(
                95,
                pred,
                row["delta"],
                time_left_for_decision,
                vol_regime,
                allowed,
                delta_thresholds,
                stats,
                row.get("atr"),
            )
            decision_90 = decision_for_conf(
                90,
                pred,
                row["delta"],
                time_left_for_decision,
                vol_regime,
                allowed,
                delta_thresholds,
                stats,
                row.get("atr"),
            )
            decision_85 = None
            if ENABLE_85_RULE:
                decision_85 = decision_for_conf(
                    85,
                    pred,
                    row["delta"],
                    time_left_for_decision,
                    vol_regime,
                    allowed,
                    delta_thresholds,
                    stats,
                    row.get("atr"),
                )
            decision_80 = None
            if ENABLE_80_RULE:
                decision_80 = decision_for_conf(
                    80,
                    pred,
                    row["delta"],
                    time_left_for_decision,
                    vol_regime,
                    allowed,
                    delta_thresholds,
                    stats,
                    row.get("atr"),
                )

            if LOG_MODE == "full":
                print("=" * 80)
                print(
                    f"Now: {row['now'].isoformat()} | Open time: {row['open_time'].isoformat()} | "
                    f"Minute ref: {row['minute_ref'].isoformat()}"
                )
                print(
                    "Strike (15m open): "
                    f"{row['strike']:.2f} | Current: {row['current_price']:.2f} | "
                    f"Snapshot: {row['snapshot_price']:.2f}"
                )
                if SHOW_PRICE_SOURCE:
                    print(f"Price source: {price_source}")
                print(
                    f"Delta(snapshot): {row['delta_snap']:.2f} | Delta(current): {row['delta_current']:.2f} | "
                    f"Decision delta: {row['delta']:.2f}"
                )
                print(
                    "Tick stats 60s: "
                    f"state={stats['state']} | er={stats['er_60']:.2f} | "
                    f"trend={stats['trend_strength_60']:.2f} | sigma={stats['sigma_60']:.6f} | "
                    f"highs={stats['new_highs_60']} lows={stats['new_lows_60']}"
                )
                if not np.isnan(stats["high_since_open"]):
                    print(
                        "Since open: "
                        f"high={stats['high_since_open']:.2f} low={stats['low_since_open']:.2f} "
                        f"range={stats['range_since_open']:.2f} pos={stats['position_in_range']:.2f} "
                        f"pullback={stats['pullback_from_high']:.2f} bounce={stats['bounce_from_low']:.2f}"
                    )
            else:
                t_now = row["now"].strftime("%H:%M:%S")
                t_open = row["open_time"].strftime("%H:%M")
                parts = [
                    f"{t_now} open={t_open}",
                    f"tleft={row['time_left_now']:.2f}m",
                    f"px={row['current_price']:.2f}",
                    f"d={row['delta_current']:.2f}",
                    f"reg={vol_regime}/{volu_regime}",
                    f"state={stats['state']}",
                    f"er={stats['er_60']:.2f}",
                    f"pos={stats['position_in_range']:.2f}" if not np.isnan(stats['position_in_range']) else "pos=NA",
                    f"prob={pred*100:.2f}%",
                    f"95:{short_decision(decision_95)}",
                    f"90:{short_decision(decision_90)}",
                ]
                if decision_85 is not None:
                    parts.append(f"85:{short_decision(decision_85)}")
                if decision_80 is not None:
                    parts.append(f"80:{short_decision(decision_80)}")
                if SHOW_PRICE_SOURCE:
                    parts.append(f"src={price_source}")
                if SHOW_PRICE_SANITY and sanity_prices:
                    sp = sanity_prices
                    prices = [
                        f"cb={sp.get('coinbase'):.2f}" if sp.get("coinbase") else "cb=NA",
                        f"bn={sp.get('binance_rest'):.2f}" if sp.get("binance_rest") else "bn=NA",
                        f"kw={sp.get('kraken'):.2f}" if sp.get("kraken") else "kw=NA",
                        f"bs={sp.get('bitstamp'):.2f}" if sp.get("bitstamp") else "bs=NA",
                    ]
                    parts.append(" ".join(prices))
                print(" | ".join(parts))
            print(f"Strike source: {row['strike_source']}")
            if LOG_MODE == "full":
                print(
                    f"Time left: now={row['time_left_now']:.2f}m | snap={row['time_left_snapshot']}m | "
                    f"effective={row['time_left_effective']}m | Mode={TIME_LEFT_MODE} | "
                    f"Vol regime: {vol_regime} | Volu regime: {volu_regime}"
                )
            # Also show raw prob (before calibration) for debugging
            raw_pred = predict_reversal(
                {**model, "calibration": {"x": [0.0, 1.0], "y": [0.0, 1.0]}},
                model_features,
            )
            decision_95 = confirm_decision("95", decision_95, confirm_state)
            decision_90 = confirm_decision("90", decision_90, confirm_state)
            if decision_85 is not None:
                decision_85 = confirm_decision("85", decision_85, confirm_state)
            if decision_80 is not None:
                decision_80 = confirm_decision("80", decision_80, confirm_state)

            if LOG_MODE == "full":
                print(f"Predicted reversal: raw={raw_pred * 100:.2f}% | calibrated={pred * 100:.2f}%")
                print(f"Decision 95%: {decision_95}")
                print(f"Decision 90%: {decision_90}")
                if decision_85 is not None:
                    print(f"Decision 85%: {decision_85}")
                if decision_80 is not None:
                    print(f"Decision 80%: {decision_80}")

            if market:
                print(
                    f"Polymarket: {market['title']} | minutes_left={market['minutes_left']:.1f} | slug={market['slug']}"
                )
            else:
                print("Polymarket: no active market match (or scan disabled)")

        except Exception as e:
            print(f"Error: {e}")

        if TICK_MODE and row["time_left_now"] <= TICK_WINDOW_MINUTES:
            time.sleep(TICK_INTERVAL_SEC)
        else:
            time.sleep(UPDATE_INTERVAL)


if __name__ == "__main__":
    run()
