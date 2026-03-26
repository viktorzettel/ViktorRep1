#!/usr/bin/env python3
"""
Kou Jump-Diffusion Decision Bot — generic for any Binance-listed asset.

Replaces the Black-Scholes probability model with Kou's double-exponential
jump-diffusion model (Kou 2002), calibrated live from 10-second OHLC candles
aggregated from Binance tick data.

The Kou model captures:
  - Fat tails via double-exponential jump distribution
  - Asymmetric up/down jumps (different decay rates)
  - Jump clustering via Poisson intensity

Calibration uses 10s candle close-to-close returns (backtested as optimal
for balancing noise vs signal). Parkinson high-low σ estimator provides a
cross-check on diffusion volatility.

Probability P(S_T > K) is estimated via Monte Carlo (10,000 paths, ~2ms).

Usage:
    python3 kou_decision_bot.py --symbol ethusdt
    python3 kou_decision_bot.py --symbol solusdt
    python3 kou_decision_bot.py --symbol xrpusdt --verbose

Based on: Kończal (2024) — "Pricing options on cryptocurrency futures contracts"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import ssl
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import websockets

# ── SSL ───────────────────────────────────────────────────────────────────────

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

# ── Constants ─────────────────────────────────────────────────────────────────

BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"
MC_PATHS = 5_000                # Monte Carlo paths (±0.7% noise, ~1ms per eval)
CALIB_WINDOW_S = 6 * 3600      # 6 hours of history for calibration
CANDLE_INTERVAL_S = 10          # 10-second candle aggregation (backtested optimal)
JUMP_THRESHOLD_SIGMA = 2.0      # |return| > 2σ = jump
MIN_CALIB_CANDLES = 30          # minimum 10s candles for calibration (~5 min boot)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _round4(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return float(f"{v:.4f}")


def _fmt4(v: Optional[float]) -> str:
    return f"{v:.4f}" if v is not None else "-"


def _fmt2(v: Optional[float]) -> str:
    return f"{v:.2f}" if v is not None else "-"


def _fmt_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ── 10s OHLC candle ──────────────────────────────────────────────────────────

@dataclass
class Candle10s:
    """One 10-second OHLC candle, built from aggregated ticks."""
    bucket_ts: float    # floor(ts / 10) * 10 — candle start timestamp
    open: float
    high: float
    low: float
    close: float
    n_ticks: int = 0


class BinancePriceStream:
    """
    Real-time trade stream from Binance.

    Aggregates raw ticks into 10-second OHLC candles for Kou calibration.
    Also stores the raw latest price for real-time display.
    """

    def __init__(self, symbol: str, history_seconds: int = CALIB_WINDOW_S + 120) -> None:
        self.symbol = symbol.lower()
        self.url = f"{BINANCE_WS_BASE}/{self.symbol}@trade"
        self.history_seconds = max(120, history_seconds)

        # Raw latest price
        self.last_price: Optional[float] = None
        self.last_ts: Optional[float] = None

        # 10s candle history
        self.candles: deque[Candle10s] = deque()
        self._current_candle: Optional[Candle10s] = None

        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> tuple[Optional[float], Optional[float]]:
        return self.last_price, self.last_ts

    def _candle_bucket(self, ts: float) -> float:
        """Floor timestamp to 10s boundary."""
        return (int(ts) // CANDLE_INTERVAL_S) * CANDLE_INTERVAL_S

    def _update(self, ts: float, price: float) -> None:
        self.last_price = price
        self.last_ts = ts

        bucket = self._candle_bucket(ts)

        # Check if we need to start a new candle
        if self._current_candle is None or bucket != self._current_candle.bucket_ts:
            # Finalise previous candle
            if self._current_candle is not None and self._current_candle.n_ticks > 0:
                self.candles.append(self._current_candle)

            # Start new candle
            self._current_candle = Candle10s(
                bucket_ts=bucket, open=price, high=price, low=price, close=price, n_ticks=1
            )

            # Prune old candles
            min_ts = ts - self.history_seconds
            while self.candles and self.candles[0].bucket_ts < min_ts:
                self.candles.popleft()
        else:
            # Update current candle
            c = self._current_candle
            c.high = max(c.high, price)
            c.low = min(c.low, price)
            c.close = price
            c.n_ticks += 1

    def get_candles(self, window_s: int = CALIB_WINDOW_S) -> list[Candle10s]:
        """Return completed 10s candles within the last `window_s` seconds."""
        if not self.candles:
            return []
        now_ts = self.candles[-1].bucket_ts
        cutoff = now_ts - window_s
        return [c for c in self.candles if c.bucket_ts >= cutoff]

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
                    logging.info("Binance stream connected: %s", self.symbol.upper())
                    backoff = 1.0
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        try:
                            msg = json.loads(raw)
                            price = float(msg.get("p", 0.0))
                            if price <= 0.0:
                                continue
                            evt = msg.get("E") or msg.get("T")
                            ts = float(evt) / 1000.0 if evt is not None else time.time()
                            if ts <= 0:
                                ts = time.time()
                            self._update(ts, price)
                        except Exception:
                            continue
            except Exception as exc:
                logging.warning("Binance reconnecting: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2.0, 10.0)


# ── Kou calibration ──────────────────────────────────────────────────────────

@dataclass
class KouParams:
    """Calibrated Kou model parameters (per 10s candle interval)."""
    sigma: float       # diffusion σ per candle period (per √10s)
    lam: float         # jump intensity per candle period (probability of jump in one 10s candle)
    p_up: float        # probability of upward jump
    eta1: float        # upward jump decay rate (η₁ > 1)
    eta2: float        # downward jump decay rate (η₂ > 0)
    sigma_park: float  # Parkinson σ per candle period (cross-check)

    @property
    def xi(self) -> float:
        """E[e^Y - 1] — mean relative jump compensation."""
        t1 = self.p_up * self.eta1 / (self.eta1 - 1.0) if self.eta1 > 1.0 else 0.0
        t2 = (1.0 - self.p_up) * self.eta2 / (self.eta2 + 1.0)
        return t1 + t2 - 1.0

    @property
    def sigma_per_sqrt_s(self) -> float:
        """σ per √second (for BS comparison)."""
        return self.sigma / math.sqrt(CANDLE_INTERVAL_S)

    def __str__(self) -> str:
        return (
            f"σ={self.sigma:.6f}/√10s  σ_park={self.sigma_park:.6f}/√10s  "
            f"λ={self.lam:.4f}/candle  "
            f"p_up={self.p_up:.3f}  η₁={self.eta1:.1f}  η₂={self.eta2:.1f}  "
            f"ξ={self.xi:.6f}"
        )


def parkinson_sigma(candles: list[Candle10s]) -> float:
    """
    Parkinson (1980) high-low range volatility estimator.
    σ_P = √(1/(4·n·ln2) · Σ ln(H_i/L_i)²)

    Returns σ per candle period. ~5x more efficient than close-to-close.
    """
    valid = [(c.high, c.low) for c in candles if c.high > c.low]
    if len(valid) < 10:
        return 0.0
    log_hl_sq = np.array([math.log(h / l) ** 2 for h, l in valid])
    return float(np.sqrt(np.sum(log_hl_sq) / (4 * len(valid) * math.log(2))))


class KouCalibrator:
    """
    Estimates Kou parameters from 10s OHLC candle close-to-close returns.

    Strategy:
      1. Compute close-to-close log returns (fixed 10s intervals)
      2. Rough σ → identify jumps → refined σ from non-jump returns
      3. Estimate jump intensity λ, up-probability p, decay rates η₁, η₂
      4. Parkinson σ from high-low range as cross-check
    """

    @staticmethod
    def calibrate(candles: list[Candle10s]) -> Optional[KouParams]:
        """Calibrate Kou parameters from 10s candles."""
        if len(candles) < MIN_CALIB_CANDLES + 1:
            return None

        closes = np.array([c.close for c in candles])
        log_ret = np.diff(np.log(closes))

        if len(log_ret) < MIN_CALIB_CANDLES:
            return None

        # Pass 1: rough σ from all returns (per candle period)
        sigma_rough = float(np.std(log_ret))
        if sigma_rough <= 1e-12:
            return None

        # Pass 2: identify jumps at 2σ threshold
        jump_mask = np.abs(log_ret) > JUMP_THRESHOLD_SIGMA * sigma_rough

        # Pass 3: refined σ from non-jump returns only
        non_jump = log_ret[~jump_mask]
        if len(non_jump) >= 20:
            sigma = float(np.std(non_jump))
            if sigma <= 1e-12:
                sigma = sigma_rough
        else:
            sigma = sigma_rough

        # Re-identify jumps with refined σ
        jump_mask = np.abs(log_ret) > JUMP_THRESHOLD_SIGMA * sigma
        n_jumps = int(jump_mask.sum())

        # Parkinson σ
        sigma_pk = parkinson_sigma(candles)

        if n_jumps < 3:
            return KouParams(
                sigma=sigma, lam=1e-6, p_up=0.5,
                eta1=10.0, eta2=10.0, sigma_park=sigma_pk,
            )

        # Jump intensity: fraction of candles with a jump
        lam = n_jumps / len(log_ret)

        # Extract jump returns
        jump_returns = log_ret[jump_mask]
        up_jumps = jump_returns[jump_returns > 0]
        down_jumps = jump_returns[jump_returns < 0]

        # Up probability
        p_up = max(0.05, min(0.95, len(up_jumps) / len(jump_returns)))

        # Decay rates: η = 1 / mean(|jump size|)
        if len(up_jumps) >= 2:
            eta1 = 1.0 / float(np.mean(up_jumps))
            eta1 = max(1.01, eta1)  # enforce η₁ > 1
        else:
            eta1 = 10.0

        if len(down_jumps) >= 2:
            eta2 = 1.0 / float(np.mean(np.abs(down_jumps)))
            eta2 = max(0.1, eta2)
        else:
            eta2 = 10.0

        return KouParams(
            sigma=sigma, lam=lam, p_up=p_up,
            eta1=eta1, eta2=eta2, sigma_park=sigma_pk,
        )


# ── Kou probability (Monte Carlo) ────────────────────────────────────────────

class KouProbability:
    """Monte Carlo estimation of P(S_T > K) under the Kou model."""

    def __init__(self, n_paths: int = MC_PATHS, seed: Optional[int] = None) -> None:
        self.n_paths = n_paths
        self.rng = np.random.default_rng(seed)

    def prob_yes(
        self,
        current_price: float,
        strike: float,
        time_left_s: float,
        params: KouParams,
    ) -> float:
        """
        Estimate P(S_T > K) under the Kou model via Monte Carlo.

        Converts time_left from seconds to number of 10s candle periods,
        then simulates:
            log(S_T / S_0) = (−0.5σ² − λξ)·n + σ·√n · Z + Σ Yᵢ

        Where n = number of remaining 10s candle periods.
        """
        if time_left_s <= 0:
            return 1.0 if current_price >= strike else 0.0

        if current_price <= 0 or strike <= 0:
            return 0.5

        # Convert to candle periods
        n_periods = time_left_s / CANDLE_INTERVAL_S

        sigma = params.sigma     # σ per √candle
        lam = params.lam         # jump prob per candle
        xi = params.xi

        # Scale to full horizon
        sigma_T = sigma * math.sqrt(n_periods)
        lam_T = lam * n_periods   # expected number of jumps over horizon

        drift = -0.5 * sigma_T * sigma_T - lam_T * xi
        diffusion = sigma_T

        # Draw diffusion: Z ~ N(0,1)
        Z = self.rng.standard_normal(self.n_paths)

        # Draw number of jumps: N ~ Poisson(λ·n)
        n_jumps = self.rng.poisson(lam_T, size=self.n_paths)

        # Draw jump sizes
        total_jump = np.zeros(self.n_paths)
        max_j = int(n_jumps.max()) if n_jumps.max() > 0 else 0

        if max_j > 0:
            for j in range(max_j):
                active = n_jumps > j
                is_up = self.rng.random(self.n_paths) < params.p_up
                up_sz = self.rng.exponential(1.0 / params.eta1, self.n_paths)
                dn_sz = -self.rng.exponential(1.0 / params.eta2, self.n_paths)
                jump_size = np.where(is_up, up_sz, dn_sz)
                total_jump += np.where(active, jump_size, 0.0)

        log_ratio = drift + diffusion * Z + total_jump
        threshold = math.log(strike / current_price)

        return float(np.clip(np.mean(log_ratio > threshold), 0.0, 1.0))


# ── BS probability (for comparison) ──────────────────────────────────────────

def bs_prob_yes(current: float, strike: float, time_left_s: float, sigma_per_sqrt_s: float) -> float:
    """Black-Scholes P(S_T > K) using d₂, with σ per √second."""
    if time_left_s <= 0:
        return 1.0 if current >= strike else 0.0
    if sigma_per_sqrt_s <= 1e-12:
        return 1.0 if current >= strike else 0.0
    sigma_T = sigma_per_sqrt_s * math.sqrt(time_left_s)
    d = (math.log(current / strike) - 0.5 * sigma_T * sigma_T) / sigma_T
    return float(np.clip(_normal_cdf(d), 0.0, 1.0))


# ── Decision bot ──────────────────────────────────────────────────────────────

class DecisionBot:
    def __init__(
        self,
        *,
        symbol: str,
        bucket_seconds: int,
        poll_seconds: float,
        sigma_fallback: float,
    ) -> None:
        self.symbol = symbol
        self.bucket_seconds = bucket_seconds
        self.poll_seconds = max(0.2, poll_seconds)
        self.sigma_fallback = sigma_fallback

        self.stream = BinancePriceStream(symbol=symbol, history_seconds=CALIB_WINDOW_S + 120)
        self.calibrator = KouCalibrator()
        self.mc = KouProbability()

        self.strike_price: Optional[float] = None
        self.bucket_start: Optional[float] = None
        self.kou_params: Optional[KouParams] = None

    def _current_bucket_start(self, now_ts: float) -> float:
        return (int(now_ts) // self.bucket_seconds) * self.bucket_seconds

    def _roll_bucket_if_needed(self, now_ts: float) -> None:
        bucket_start = self._current_bucket_start(now_ts)
        if self.bucket_start is None or bucket_start != self.bucket_start:
            self.bucket_start = bucket_start
            self.strike_price = None
            logging.info(
                "New bucket: start=%s end=%s",
                _fmt_ts(bucket_start),
                _fmt_ts(bucket_start + self.bucket_seconds),
            )
            self._calibrate()
        elif self.kou_params is None:
            # Haven't calibrated yet — try as soon as enough candles exist
            self._calibrate()

    def _calibrate(self) -> None:
        """Calibrate Kou parameters from 10s candle history."""
        candles = self.stream.get_candles()
        if len(candles) < MIN_CALIB_CANDLES:
            logging.warning(
                "Insufficient 10s candles for Kou calibration (%d, need %d) — BS fallback",
                len(candles), MIN_CALIB_CANDLES,
            )
            self.kou_params = None
            return

        params = self.calibrator.calibrate(candles)
        if params is None:
            logging.warning("Kou calibration failed — BS fallback")
            self.kou_params = None
            return

        self.kou_params = params
        logging.info("Kou calibrated: %s  (from %d candles)", params, len(candles))

    def _ensure_strike(self, current_price: Optional[float]) -> None:
        if self.strike_price is not None:
            return
        if current_price is None:
            return
        self.strike_price = _round4(current_price)
        logging.info("Strike set: %s", _fmt4(self.strike_price))

    def _compute_probabilities(
        self, current: float, strike: float, time_left: float
    ) -> tuple[float, float, float, float, str]:
        """Returns (kou_yes, kou_no, bs_yes, bs_no, model_used)."""
        # BS (always computed)
        sigma_bs = self.sigma_fallback
        if self.kou_params:
            sigma_bs = self.kou_params.sigma_per_sqrt_s
        bs_yes = bs_prob_yes(current, strike, time_left, sigma_bs)
        bs_no = 1.0 - bs_yes

        # Kou
        if self.kou_params:
            kou_yes = self.mc.prob_yes(current, strike, time_left, self.kou_params)
            kou_no = 1.0 - kou_yes
            model = "KOU"
        else:
            kou_yes = bs_yes
            kou_no = bs_no
            model = "BS-FALLBACK"

        return kou_yes, kou_no, bs_yes, bs_no, model

    async def run(self) -> None:
        stream_task = asyncio.create_task(self.stream.run(), name="binance_stream")
        try:
            while True:
                cycle_start = time.time()
                self._roll_bucket_if_needed(cycle_start)

                current_price, _ = self.stream.latest()
                self._ensure_strike(current_price)

                if self.bucket_start is None:
                    await asyncio.sleep(self.poll_seconds)
                    continue

                expiry = self.bucket_start + self.bucket_seconds
                time_left = max(0.0, expiry - cycle_start)

                if current_price is None or self.strike_price is None:
                    logging.info(
                        "state=WAITING price=%s strike=%s t_left_s=%s",
                        _fmt4(_round4(current_price)),
                        _fmt4(self.strike_price),
                        _fmt2(time_left),
                    )
                else:
                    c4 = _round4(current_price)
                    s4 = _round4(self.strike_price)
                    kou_y, kou_n, bs_y, bs_n, model = self._compute_probabilities(
                        c4, s4, time_left
                    )
                    diff_bps = round((c4 - s4) / s4 * 10000, 1) if s4 else 0.0

                    logging.info(
                        "state=LIVE  model=%s  price=%s  strike=%s  "
                        "t_left=%s  kou_yes=%.4f  kou_no=%.4f  "
                        "bs_yes=%.4f  bs_no=%.4f  Δ=%+.1fbps",
                        model,
                        _fmt4(c4),
                        _fmt4(s4),
                        _fmt2(time_left),
                        kou_y,
                        kou_n,
                        bs_y,
                        bs_n,
                        diff_bps,
                    )

                elapsed = time.time() - cycle_start
                await asyncio.sleep(max(0.0, self.poll_seconds - elapsed))
        finally:
            self.stream.stop()
            stream_task.cancel()
            await asyncio.gather(stream_task, return_exceptions=True)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Kou jump-diffusion decision bot (10s candle calibration)"
    )
    p.add_argument(
        "--symbol", default="ethusdt",
        help="Binance symbol, e.g. ethusdt, solusdt, xrpusdt (default: ethusdt)",
    )
    p.add_argument(
        "--bucket-seconds", type=int, default=300,
        help="Bucket size in seconds (5m=300, 15m=900, default: 300)",
    )
    p.add_argument(
        "--poll-seconds", type=float, default=1.0,
        help="Decision loop interval in seconds (default: 1.0)",
    )
    p.add_argument(
        "--sigma-fallback", type=float, default=0.0003,
        help="Fallback σ per √second if calibration unavailable (default: 0.0003)",
    )
    p.add_argument(
        "--mc-paths", type=int, default=MC_PATHS,
        help=f"Monte Carlo paths (default: {MC_PATHS})",
    )
    p.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return p


async def _main_async(args: argparse.Namespace) -> int:
    bot = DecisionBot(
        symbol=args.symbol,
        bucket_seconds=args.bucket_seconds,
        poll_seconds=args.poll_seconds,
        sigma_fallback=args.sigma_fallback,
    )
    bot.mc.n_paths = args.mc_paths
    await bot.run()
    return 0


def main() -> int:
    args = build_parser().parse_args()
    _setup_logging(args.verbose)
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        logging.info("Stopped by user.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
