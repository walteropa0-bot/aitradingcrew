"""A simulated exchange with the same surface as crew.exchange.Exchange - for the offline demo, tests and
screenshots. Prices follow a regime-switching random walk on a 1-minute grid that is generated
lazily up to the (possibly accelerated, virtual) clock.  No orders, no account: paper trading only."""
from __future__ import annotations

import math
import time
from typing import Callable, Optional

import numpy as np

from . import strategy as st
from .exchange import ExchangeError, Tick
from .util import timeframe_seconds

START = {"BTC": 68_000.0, "ETH": 2_000.0, "SOL": 80.0}


class VirtualClock:
    """Wall clock x speed. speed=1 is real time; 600 compresses 10 minutes into one second."""

    def __init__(self, speed: float = 1.0, start: Optional[float] = None) -> None:
        self.speed = max(0.1, float(speed))
        self._t0_wall = time.time()
        self._t0 = start if start is not None else self._t0_wall

    def __call__(self) -> float:
        return self._t0 + (time.time() - self._t0_wall) * self.speed


class SimExchange:
    id = "simulator"
    authenticated = False
    markets_loaded = True

    def __init__(self, symbols: list[str], clock: Callable[[], float] = time.time, seed: int = 11,
                 history_days: int = 400) -> None:
        self.symbols = list(symbols)
        self._clock = clock
        self._rng = {s: np.random.default_rng(seed + i * 101) for i, s in enumerate(symbols)}
        self._t_start = (math.floor(clock() / 86400) - history_days) * 86400
        self._path: dict[str, list[float]] = {s: [] for s in symbols}
        self._drift: dict[str, float] = {s: 0.0 for s in symbols}
        self._regime_left: dict[str, int] = {s: 0 for s in symbols}
        self.fail_next = 0                                   # tests: number of upcoming calls that raise a network error

    # ───────── path generation (1-minute closes) ─────────
    def _extend(self, symbol: str, upto_ts: float) -> None:
        need = int((upto_ts - self._t_start) // 60) + 2
        path = self._path[symbol]
        rng = self._rng[symbol]
        base = symbol.split("/")[0]
        px = path[-1] if path else START.get(base, 100.0)
        while len(path) < need:
            if self._regime_left[symbol] <= 0:                # new regime every 1-6 days: up / down / sideways
                self._drift[symbol] = rng.choice([0.00002, 0.00001, 0.0, -0.00001, -0.00002], p=[.25, .2, .1, .2, .25])
                self._regime_left[symbol] = int(rng.integers(1440, 8640))
            self._regime_left[symbol] -= 1
            px *= math.exp(self._drift[symbol] + rng.normal(0, 0.0005))
            path.append(px)

    def _price(self, symbol: str, ts: float) -> float:
        self._extend(symbol, ts)
        return self._path[symbol][max(0, int((ts - self._t_start) // 60))]

    def _maybe_fail(self) -> None:
        if self.fail_next > 0:
            self.fail_next -= 1
            raise ExchangeError("network", "Simulierter Netzwerkfehler")

    # ───────── Exchange surface ─────────
    def load_markets(self) -> None:
        return None

    def has_symbol(self, symbol: str) -> bool:
        return symbol in self.symbols

    def rules(self, symbol: str) -> st.Rules:
        base = symbol.split("/")[0]
        return st.Rules(min_amount={"BTC": 6e-5, "ETH": 2e-3, "SOL": 0.05}.get(base, 0.01), min_cost=0.45,
                        amount_step=1e-8, price_step=0.01)

    def fee_rates(self, symbol: str) -> tuple[float, float]:
        return 0.0026, 0.0016

    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 400) -> np.ndarray:
        self._maybe_fail()
        tf = timeframe_seconds(timeframe)
        now = self._clock()
        last_open = math.floor(now / tf) * tf                # forming candle
        opens = last_open - np.arange(limit - 1, -1, -1) * tf
        rows = []
        for t0 in opens:
            if t0 < self._t_start:
                continue
            self._extend(symbol, t0 + tf)
            i0 = int((t0 - self._t_start) // 60)
            i1 = min(int((min(t0 + tf, now) - self._t_start) // 60) + 1, len(self._path[symbol]))
            seg = self._path[symbol][max(0, i0 - 1):max(i1, i0 + 1)]
            if not seg:
                continue
            rows.append([t0 * 1000.0, seg[0], max(seg) * 1.0003, min(seg) * 0.9997, seg[-1], 100.0 + 50 * abs(seg[-1] / seg[0] - 1) * 100])
        return np.asarray(rows, dtype=np.float64)

    def fetch_ticks(self, symbols: list[str]) -> dict[str, Tick]:
        self._maybe_fail()
        now = self._clock()
        out = {}
        for s in symbols:
            px = self._price(s, now)
            half = px * 0.00008
            out[s] = Tick(s, px - half, px + half, px, now)
        return out

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return math.floor(amount * 1e8) / 1e8

    def fetch_balances(self):
        raise ExchangeError("auth", "Der Demo-Markt hat kein Konto")

    def check_credentials(self):
        return {"ok": False, "withdraw_enabled": None, "detail": "Demo-Markt"}
