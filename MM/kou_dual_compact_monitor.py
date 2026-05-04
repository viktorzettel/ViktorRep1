#!/usr/bin/env python3
"""
Compact dual-symbol Kou jump-diffusion monitor for small terminal windows.

Default view tracks ETHUSDT and XRPUSDT in parallel, stacked vertically.
The mathematical flow keeps returns on a fixed 10-second grid by inserting
flat synthetic candles across missing buckets, which is important for Kou
calibration on live websocket data.

Run:
    python3 kou_dual_compact_monitor.py
    python3 kou_dual_compact_monitor.py --symbols ethusdt,xrpusdt
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import shutil
import ssl
import sys
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np
import websockets

try:
    import certifi

    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()


BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"
CANDLE_INTERVAL_S = 10
CALIB_WINDOW_S = 2 * 3600
MIN_CALIB_CANDLES = 30
FULL_CALIB_CANDLES = 60
# A fixed 2-sigma rule over-flags jumps on short crypto windows. Use a more
# conservative interim cutoff until we replace this heuristic with a full
# jump-robust estimator/backtest-calibrated threshold.
JUMP_THRESHOLD_SIGMA = 3.0
MC_PATHS = 5_000
STALE_AFTER_S = 15.0
RECENT_TICK_LOOKBACK_S = 900.0
EPS = 1e-12


def _now_utc() -> str:
    return time.strftime("%H:%M:%S", time.gmtime())


def _fmt_clock(ts: Optional[float]) -> str:
    if ts is None:
        return "--:--:--"
    return time.strftime("%H:%M:%S", time.gmtime(ts))


def _fmt_price(value: Optional[float]) -> str:
    if value is None:
        return "-"
    abs_value = abs(value)
    if abs_value >= 1000:
        return f"{value:,.2f}"
    if abs_value >= 100:
        return f"{value:,.3f}"
    if abs_value >= 1:
        return f"{value:,.4f}"
    if abs_value >= 0.1:
        return f"{value:,.5f}"
    if abs_value >= 0.01:
        return f"{value:,.6f}"
    return f"{value:,.8f}"


def _fmt_num(value: Optional[float], decimals: int = 4) -> str:
    if value is None:
        return "-"
    return f"{value:.{decimals}f}"


def _fmt_signed(value: Optional[float], decimals: int = 1, suffix: str = "") -> str:
    if value is None:
        return "-"
    return f"{value:+.{decimals}f}{suffix}"


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass
class Candle10s:
    bucket_ts: int
    open: float
    high: float
    low: float
    close: float
    n_ticks: int
    synthetic: bool = False


@dataclass
class KouParams:
    sigma: float
    lam: float
    p_up: float
    eta1: float
    eta2: float
    sigma_park: float
    mu_diffusive: float
    jump_count: int
    sample_count: int

    @property
    def xi(self) -> float:
        up_term = self.p_up * self.eta1 / (self.eta1 - 1.0) if self.eta1 > 1.0 else 0.0
        down_term = (1.0 - self.p_up) * self.eta2 / (self.eta2 + 1.0)
        return up_term + down_term - 1.0

    @property
    def sigma_per_sqrt_second(self) -> float:
        return self.sigma / math.sqrt(CANDLE_INTERVAL_S)


class BinanceTradeStream:
    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120) -> None:
        self.symbol = symbol.lower()
        self.url = f"{BINANCE_WS_BASE}/{self.symbol}@trade"
        self.history_seconds = max(300, history_seconds)
        self.last_price: Optional[float] = None
        self.last_ts: Optional[float] = None
        self._current_candle: Optional[Candle10s] = None
        self._completed: deque[Candle10s] = deque()
        self._recent_ticks: deque[tuple[float, float]] = deque()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> tuple[Optional[float], Optional[float]]:
        return self.last_price, self.last_ts

    def last_price_at_or_before(self, ts: float, max_age_s: Optional[float] = None) -> Optional[float]:
        for tick_ts, tick_price in reversed(self._recent_ticks):
            if tick_ts <= ts:
                if max_age_s is None or (ts - tick_ts) <= max_age_s:
                    return tick_price
                return None
        return None

    def first_price_at_or_after(self, ts: float, max_delay_s: Optional[float] = None) -> Optional[float]:
        for tick_ts, tick_price in self._recent_ticks:
            if tick_ts >= ts:
                if max_delay_s is None or (tick_ts - ts) <= max_delay_s:
                    return tick_price
                return None
        return None

    def latest_completed_bucket(self) -> Optional[int]:
        if not self._completed:
            return None
        return self._completed[-1].bucket_ts

    def completed_candles(self, window_s: int = CALIB_WINDOW_S) -> list[Candle10s]:
        if not self._completed:
            return []
        cutoff = self._completed[-1].bucket_ts - window_s
        return [c for c in self._completed if c.bucket_ts >= cutoff]

    def _bucket(self, ts: float) -> int:
        return (int(ts) // CANDLE_INTERVAL_S) * CANDLE_INTERVAL_S

    def _append_completed(self, candle: Candle10s, now_ts: float) -> None:
        self._completed.append(candle)
        cutoff = now_ts - self.history_seconds
        while self._completed and self._completed[0].bucket_ts < cutoff:
            self._completed.popleft()

    def _start_candle(self, bucket_ts: int, price: float) -> Candle10s:
        return Candle10s(
            bucket_ts=bucket_ts,
            open=price,
            high=price,
            low=price,
            close=price,
            n_ticks=1,
            synthetic=False,
        )

    def _update(self, ts: float, price: float) -> None:
        if price <= 0.0:
            return
        if self.last_ts is not None and ts + 1e-6 < self.last_ts:
            return

        self.last_price = price
        self.last_ts = ts
        self._recent_ticks.append((ts, price))
        while self._recent_ticks and self._recent_ticks[0][0] < ts - RECENT_TICK_LOOKBACK_S:
            self._recent_ticks.popleft()
        bucket = self._bucket(ts)

        if self._current_candle is None:
            self._current_candle = self._start_candle(bucket, price)
            return

        current = self._current_candle
        if bucket == current.bucket_ts:
            current.high = max(current.high, price)
            current.low = min(current.low, price)
            current.close = price
            current.n_ticks += 1
            return

        if bucket < current.bucket_ts:
            return

        last_close = current.close
        self._append_completed(current, ts)

        gap_bucket = current.bucket_ts
        while gap_bucket + CANDLE_INTERVAL_S < bucket:
            gap_bucket += CANDLE_INTERVAL_S
            synthetic = Candle10s(
                bucket_ts=gap_bucket,
                open=last_close,
                high=last_close,
                low=last_close,
                close=last_close,
                n_ticks=0,
                synthetic=True,
            )
            self._append_completed(synthetic, ts)

        self._current_candle = self._start_candle(bucket, price)

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url,
                    ssl=_SSL_CTX,
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                    max_size=2_000_000,
                ) as ws:
                    logging.warning("stream connected %s", self.symbol.upper())
                    backoff = 1.0
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        try:
                            msg = json.loads(raw)
                            price = float(msg.get("p", 0.0))
                            event_ms = msg.get("E") or msg.get("T")
                            ts = float(event_ms) / 1000.0 if event_ms else time.time()
                            self._update(ts, price)
                        except Exception:
                            continue
            except Exception as exc:
                logging.warning("stream reconnect %s: %s", self.symbol.upper(), exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)


def parkinson_sigma(candles: list[Candle10s]) -> float:
    valid = [(c.high, c.low) for c in candles if c.high > c.low]
    if len(valid) < 10:
        return 0.0
    log_hl_sq = np.array([math.log(high / low) ** 2 for high, low in valid], dtype=float)
    return float(np.sqrt(np.sum(log_hl_sq) / (4.0 * len(valid) * math.log(2.0))))


class KouCalibrator:
    @staticmethod
    def calibrate(candles: list[Candle10s]) -> Optional[KouParams]:
        if len(candles) < MIN_CALIB_CANDLES + 1:
            return None

        closes = np.array([c.close for c in candles], dtype=float)
        if np.any(closes <= 0.0):
            return None

        log_ret = np.diff(np.log(closes))
        if log_ret.size < MIN_CALIB_CANDLES:
            return None

        center = float(np.median(log_ret))
        mad = float(np.median(np.abs(log_ret - center)))
        sigma_robust = 1.4826 * mad
        abs_centered = np.abs(log_ret - center)
        core_cutoff = float(np.quantile(abs_centered, 0.7))
        core = log_ret[abs_centered <= core_cutoff]
        sigma_core = float(np.std(core, ddof=1)) if core.size >= 20 else 0.0
        sigma_std = float(np.std(log_ret, ddof=1))
        sigma_seed = sigma_core if sigma_core > EPS else sigma_robust
        if sigma_seed <= EPS:
            sigma_seed = sigma_std
        if sigma_seed <= EPS:
            return None

        jump_mask = np.abs(log_ret - center) > JUMP_THRESHOLD_SIGMA * sigma_seed
        non_jump = log_ret[~jump_mask]

        if non_jump.size >= 20:
            mu_diffusive = float(np.mean(non_jump))
            sigma = float(np.std(non_jump, ddof=1))
            if sigma <= EPS:
                sigma = sigma_seed
        else:
            mu_diffusive = center
            sigma = sigma_seed

        if sigma <= EPS:
            return None

        jump_mask = np.abs(log_ret - mu_diffusive) > JUMP_THRESHOLD_SIGMA * sigma
        jump_residuals = log_ret[jump_mask] - mu_diffusive
        jump_count = int(jump_residuals.size)
        sigma_park = parkinson_sigma(candles)

        if jump_count < 3:
            return KouParams(
                sigma=sigma,
                lam=1e-6,
                p_up=0.5,
                eta1=12.0,
                eta2=12.0,
                sigma_park=sigma_park,
                mu_diffusive=mu_diffusive,
                jump_count=jump_count,
                sample_count=int(log_ret.size),
            )

        up_jumps = jump_residuals[jump_residuals > 0.0]
        down_jumps = -jump_residuals[jump_residuals < 0.0]
        if up_jumps.size == 0 or down_jumps.size == 0:
            return KouParams(
                sigma=sigma,
                lam=jump_count / float(log_ret.size),
                p_up=0.5 if up_jumps.size == down_jumps.size else (0.98 if up_jumps.size > down_jumps.size else 0.02),
                eta1=12.0,
                eta2=12.0,
                sigma_park=sigma_park,
                mu_diffusive=mu_diffusive,
                jump_count=jump_count,
                sample_count=int(log_ret.size),
            )

        p_up = min(0.98, max(0.02, up_jumps.size / jump_count))
        eta1 = min(200.0, max(1.01, 1.0 / float(np.mean(up_jumps))))
        eta2 = min(200.0, max(0.1, 1.0 / float(np.mean(down_jumps))))

        return KouParams(
            sigma=sigma,
            lam=jump_count / float(log_ret.size),
            p_up=p_up,
            eta1=eta1,
            eta2=eta2,
            sigma_park=sigma_park,
            mu_diffusive=mu_diffusive,
            jump_count=jump_count,
            sample_count=int(log_ret.size),
        )


class KouProbability:
    def __init__(self, n_paths: int = MC_PATHS, seed: Optional[int] = None) -> None:
        self.n_paths = max(1, int(n_paths))
        self.rng = np.random.default_rng(seed)

    def prob_yes(self, current: float, strike: float, time_left_s: float, params: KouParams) -> float:
        if current <= 0.0 or strike <= 0.0:
            return 0.5
        if time_left_s <= 0.0:
            return 1.0 if current >= strike else 0.0

        horizon = time_left_s / CANDLE_INTERVAL_S
        sigma2_t = params.sigma * params.sigma * horizon
        lam_t = params.lam * horizon
        drift = params.mu_diffusive * horizon - 0.5 * sigma2_t
        diffusion = math.sqrt(max(sigma2_t, 0.0))

        diffusion_draws = self.rng.standard_normal(self.n_paths)
        n_jumps = self.rng.poisson(lam_t, size=self.n_paths)
        total_jump = np.zeros(self.n_paths, dtype=float)

        max_jumps = int(n_jumps.max()) if self.n_paths > 0 else 0
        for jump_idx in range(max_jumps):
            active_idx = np.flatnonzero(n_jumps > jump_idx)
            if active_idx.size == 0:
                break
            up_mask = self.rng.random(active_idx.size) < params.p_up
            jump_sizes = np.empty(active_idx.size, dtype=float)
            n_up = int(np.sum(up_mask))
            if n_up:
                jump_sizes[up_mask] = self.rng.exponential(1.0 / params.eta1, size=n_up)
            if n_up != active_idx.size:
                jump_sizes[~up_mask] = -self.rng.exponential(
                    1.0 / params.eta2, size=active_idx.size - n_up
                )
            total_jump[active_idx] += jump_sizes

        terminal_log_return = drift + diffusion * diffusion_draws + total_jump
        threshold = math.log(strike / current)
        return float(np.clip(np.mean(terminal_log_return > threshold), 0.0, 1.0))


def bs_prob_yes(current: float, strike: float, time_left_s: float, sigma_per_sqrt_second: float) -> float:
    if current <= 0.0 or strike <= 0.0:
        return 0.5
    if time_left_s <= 0.0:
        return 1.0 if current >= strike else 0.0
    if sigma_per_sqrt_second <= EPS:
        return 1.0 if current >= strike else 0.0
    sigma_t = sigma_per_sqrt_second * math.sqrt(time_left_s)
    if sigma_t <= EPS:
        return 1.0 if current >= strike else 0.0
    d2 = (math.log(current / strike) - 0.5 * sigma_t * sigma_t) / sigma_t
    return float(np.clip(_normal_cdf(d2), 0.0, 1.0))


@dataclass
class SymbolSnapshot:
    symbol: str
    state: str
    price: Optional[float]
    strike: Optional[float]
    time_left_s: Optional[float]
    price_age_s: Optional[float]
    bucket_end: Optional[float]
    kou_yes: Optional[float]
    bs_yes: Optional[float]
    params: Optional[KouParams]
    candle_count: int
    synthetic_count: int


class SymbolMonitor:
    def __init__(
        self,
        *,
        symbol: str,
        bucket_seconds: int,
        calib_window_s: int,
        sigma_fallback: float,
        stale_after_s: float,
        mc_paths: int,
    ) -> None:
        self.symbol = symbol.lower()
        self.bucket_seconds = bucket_seconds
        self.calib_window_s = calib_window_s
        self.sigma_fallback = sigma_fallback
        self.stale_after_s = stale_after_s
        self.stream = BinanceTradeStream(symbol=self.symbol, history_seconds=calib_window_s + 120)
        self.mc = KouProbability(n_paths=mc_paths)
        self.kou_params: Optional[KouParams] = None
        self.strike_price: Optional[float] = None
        self.bucket_start: Optional[int] = None
        self._last_calibrated_bucket: Optional[int] = None
        self._synthetic_count = 0
        self._candle_count = 0

    def _bucket_start(self, ts: float) -> int:
        return (int(ts) // self.bucket_seconds) * self.bucket_seconds

    def _roll_bucket_if_needed(self, now_ts: float) -> None:
        bucket_start = self._bucket_start(now_ts)
        if self.bucket_start is None or bucket_start != self.bucket_start:
            self.bucket_start = bucket_start
            self.strike_price = None

    def _ensure_strike(self, current_price: Optional[float]) -> None:
        if self.strike_price is None and current_price is not None and current_price > 0.0:
            self.strike_price = current_price

    def _refresh_calibration(self) -> None:
        latest_completed = self.stream.latest_completed_bucket()
        if latest_completed is None or latest_completed == self._last_calibrated_bucket:
            return

        candles = self.stream.completed_candles(self.calib_window_s)
        self._candle_count = len(candles)
        self._synthetic_count = sum(1 for candle in candles if candle.synthetic)
        self.kou_params = KouCalibrator.calibrate(candles)
        self._last_calibrated_bucket = latest_completed

    def snapshot(self, now_ts: float) -> SymbolSnapshot:
        self._roll_bucket_if_needed(now_ts)
        self._refresh_calibration()

        current_price, last_ts = self.stream.latest()
        self._ensure_strike(current_price)

        price_age = None if last_ts is None else max(0.0, now_ts - last_ts)
        state = "WAIT"
        if current_price is not None and self.strike_price is not None and self.bucket_start is not None:
            state = "LIVE"
        if price_age is not None and price_age > self.stale_after_s:
            state = "STALE"

        time_left = None
        bucket_end = None
        kou_yes = None
        bs_yes = None
        if self.bucket_start is not None:
            bucket_end = self.bucket_start + self.bucket_seconds
            time_left = max(0.0, bucket_end - now_ts)

        if current_price and self.strike_price and time_left is not None:
            bs_sigma = self.sigma_fallback
            if self.kou_params is not None:
                bs_sigma = self.kou_params.sigma_per_sqrt_second
                kou_yes = self.mc.prob_yes(current_price, self.strike_price, time_left, self.kou_params)
            bs_yes = bs_prob_yes(current_price, self.strike_price, time_left, bs_sigma)
            if kou_yes is None:
                kou_yes = bs_yes

        if current_price is None:
            state = "BOOT"

        return SymbolSnapshot(
            symbol=self.symbol,
            state=state,
            price=current_price,
            strike=self.strike_price,
            time_left_s=time_left,
            price_age_s=price_age,
            bucket_end=bucket_end,
            kou_yes=kou_yes,
            bs_yes=bs_yes,
            params=self.kou_params,
            candle_count=self._candle_count,
            synthetic_count=self._synthetic_count,
        )


class CompactTerminalView:
    def __init__(self, *, bucket_seconds: int, refresh_seconds: float) -> None:
        self.bucket_seconds = bucket_seconds
        self.refresh_seconds = refresh_seconds

    def _clip(self, text: str, width: int) -> str:
        if len(text) <= width:
            return text
        if width <= 3:
            return text[:width]
        return text[: width - 3] + "..."

    def _render_symbol(self, snap: SymbolSnapshot, width: int) -> list[str]:
        symbol = snap.symbol.replace("usdt", "").upper()
        delta_bps = None
        if snap.price is not None and snap.strike is not None and snap.strike > 0.0:
            delta_bps = (snap.price - snap.strike) / snap.strike * 10000.0

        line1 = (
            f"{symbol:<4} {snap.state:<5} "
            f"px {_fmt_price(snap.price):>10} "
            f"K {_fmt_price(snap.strike):>10} "
            f"d {_fmt_signed(delta_bps, 1, 'bps'):>9} "
            f"tl {_fmt_num(snap.time_left_s, 1):>6}s "
            f"age {_fmt_num(snap.price_age_s, 1):>5}s"
        )

        edge_pp = None
        if snap.kou_yes is not None and snap.bs_yes is not None:
            edge_pp = (snap.kou_yes - snap.bs_yes) * 100.0
        model = "KOU" if snap.params is not None else "BS"
        line2 = (
            f"     y {_fmt_num(snap.kou_yes, 3):>6} "
            f"n {_fmt_num(None if snap.kou_yes is None else 1.0 - snap.kou_yes, 3):>6} "
            f"bs {_fmt_num(snap.bs_yes, 3):>6} "
            f"ed {_fmt_signed(edge_pp, 1, 'pp'):>8} "
            f"mdl {model:<3} "
            f"end {_fmt_clock(snap.bucket_end)}"
        )

        if snap.params is None:
            line3 = (
                f"     cal {snap.candle_count:>4}/{MIN_CALIB_CANDLES + 1:<4} "
                f"syn {snap.synthetic_count:>4} "
                f"sig {_fmt_num(None, 5):>7} "
                f"lam {_fmt_num(None, 3):>5}"
            )
        else:
            line3 = (
                f"     sig {_fmt_num(snap.params.sigma, 5):>7} "
                f"pk {_fmt_num(snap.params.sigma_park, 5):>7} "
                f"lam {_fmt_num(snap.params.lam, 3):>5} "
                f"p+ {_fmt_num(snap.params.p_up, 2):>4} "
                f"e1 {_fmt_num(snap.params.eta1, 1):>5} "
                f"e2 {_fmt_num(snap.params.eta2, 1):>5} "
                f"syn {snap.synthetic_count:>4}"
            )

        return [self._clip(line1, width), self._clip(line2, width), self._clip(line3, width)]

    def render(self, snapshots: list[SymbolSnapshot]) -> str:
        width = max(60, shutil.get_terminal_size((96, 24)).columns)
        lines = [
            self._clip(
                f"Kou dual monitor  {_now_utc()} UTC  bucket {self.bucket_seconds}s  refresh {self.refresh_seconds:.1f}s",
                width,
            ),
            self._clip("-" * min(width, 96), width),
        ]
        for idx, snap in enumerate(snapshots):
            if idx:
                lines.append("")
            lines.extend(self._render_symbol(snap, width))
        return "\n".join(lines)


async def _display_loop(monitors: list[SymbolMonitor], refresh_seconds: float, bucket_seconds: int) -> None:
    renderer = CompactTerminalView(bucket_seconds=bucket_seconds, refresh_seconds=refresh_seconds)
    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()
    try:
        while True:
            now_ts = time.time()
            snapshots = [monitor.snapshot(now_ts) for monitor in monitors]
            screen = renderer.render(snapshots)
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.write(screen)
            sys.stdout.write("\n")
            sys.stdout.flush()
            await asyncio.sleep(refresh_seconds)
    finally:
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compact dual-symbol Kou terminal monitor")
    parser.add_argument(
        "--symbols",
        default="ethusdt,xrpusdt",
        help="Comma-separated Binance symbols to stack vertically (default: ethusdt,xrpusdt)",
    )
    parser.add_argument(
        "--bucket-seconds",
        type=int,
        default=300,
        help="Decision bucket size in seconds (default: 300)",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=float,
        default=0.5,
        help="Terminal refresh interval in seconds (default: 0.5)",
    )
    parser.add_argument(
        "--stale-seconds",
        type=float,
        default=STALE_AFTER_S,
        help="Mark a symbol stale if no new trade arrives for this many seconds (default: 15)",
    )
    parser.add_argument(
        "--sigma-fallback",
        type=float,
        default=0.0003,
        help="Fallback sigma per sqrt(second) before Kou calibration is ready",
    )
    parser.add_argument(
        "--mc-paths",
        type=int,
        default=MC_PATHS,
        help=f"Monte Carlo paths per symbol (default: {MC_PATHS})",
    )
    parser.add_argument(
        "--calib-hours",
        type=float,
        default=CALIB_WINDOW_S / 3600.0,
        help="Hours of completed 10s candles used for calibration (default: 6)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable websocket reconnect logs")
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    raw_symbols = [piece.strip().lower() for piece in args.symbols.split(",") if piece.strip()]
    symbols = raw_symbols or ["ethusdt", "xrpusdt"]
    bucket_seconds = max(30, int(args.bucket_seconds))
    refresh_seconds = max(0.2, float(args.refresh_seconds))
    calib_window_s = max(1800, int(args.calib_hours * 3600.0))
    stale_seconds = max(1.0, float(args.stale_seconds))
    mc_paths = max(1, int(args.mc_paths))

    monitors = [
        SymbolMonitor(
            symbol=symbol,
            bucket_seconds=bucket_seconds,
            calib_window_s=calib_window_s,
            sigma_fallback=float(args.sigma_fallback),
            stale_after_s=stale_seconds,
            mc_paths=mc_paths,
        )
        for symbol in symbols
    ]

    stream_tasks = [asyncio.create_task(monitor.stream.run(), name=f"stream:{monitor.symbol}") for monitor in monitors]
    display_task = asyncio.create_task(
        _display_loop(monitors, refresh_seconds=refresh_seconds, bucket_seconds=bucket_seconds),
        name="display",
    )

    try:
        await display_task
    finally:
        for monitor in monitors:
            monitor.stream.stop()
        for task in stream_tasks:
            task.cancel()
        await asyncio.gather(*stream_tasks, return_exceptions=True)

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
        sys.stdout.write("\n")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
