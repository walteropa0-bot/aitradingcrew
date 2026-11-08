"""A scriptable fake of the CCXT client surface used by crew.exchange.Exchange.

It subclasses the real ccxt Kraken class so market rules and amount_to_precision() are the REAL ones
(loaded from a fixture captured from Kraken); only network access and the order book are faked."""
from __future__ import annotations

import json
import os
import uuid

import ccxt

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def kraken_markets() -> dict:
    with open(os.path.join(FIX, "kraken_markets.json")) as fh:
        return json.load(fh)


class FakeKraken(ccxt.kraken):
    def __init__(self, balances=None, prices=None):
        super().__init__({"apiKey": "k", "secret": "s"})
        self.set_markets(self._complete(kraken_markets()))
        self.balances = dict(balances or {"EUR": 10_000.0})
        self.prices = dict(prices or {"BTC/EUR": 68_000.0, "ETH/EUR": 2_000.0, "SOL/EUR": 80.0})
        self.orders: dict[str, dict] = {}
        self.create_calls: list[dict] = []
        self.cancel_calls: list[str] = []
        # behaviour switches ------------------------------------------------
        self.script: list[str] = []            # consumed one per create_order: ok | timeout_after | timeout_before | reject_cid | insufficient | partial | open_only | base_fee | stuck
        self.fee_rate = 0.0026
        self.fetch_order_fail = 0              # number of fetch_order calls that raise a network error first
        self.cancel_fails = False
        self.clock_ms = 1_800_000_000_000

    # fixture markets lack some fields ccxt wants -------------------------------
    @staticmethod
    def _complete(markets: dict) -> dict:
        out = {}
        for sym, m in markets.items():
            base, quote = sym.split("/")
            m = dict(m)
            m.update(baseId=base, quoteId=quote, type="spot", contract=False, linear=None, inverse=None, settle=None,
                     settleId=None, expiry=None, strike=None, optionType=None, contractSize=None, margin=False,
                     swap=False, future=False, option=False, percentage=True, tierBased=True, feeSide="get", info={})
            m["precision"] = {"amount": m["precision"]["amount"], "price": m["precision"]["price"]}
            out[sym] = m
        return out

    # public --------------------------------------------------------------------
    def load_markets(self, reload=False, params=None):
        return self.markets

    def fetch_tickers(self, symbols=None, params=None):
        return {s: self._ticker(s) for s in (symbols or self.prices)}

    def fetch_ticker(self, symbol, params=None):
        return self._ticker(symbol)

    def _ticker(self, s):
        px = self.prices[s]
        return {"symbol": s, "bid": px * 0.9999, "ask": px * 1.0001, "last": px}

    # private -------------------------------------------------------------------
    def fetch_balance(self, params=None):
        free = {k: v for k, v in self.balances.items()}
        return {"free": free, "used": {k: 0.0 for k in free}, "total": dict(free)}

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        params = params or {}
        self.create_calls.append({"symbol": symbol, "side": side, "amount": amount, "params": dict(params)})
        step = self.script.pop(0) if self.script else "ok"
        if step == "reject_cid" and params.get("clientOrderId"):
            raise ccxt.BadRequest("kraken EGeneral:Invalid arguments:cl_ord_id")
        if step == "reject_cid":
            step = "ok"
        if step == "timeout_before":
            raise ccxt.RequestTimeout("kraken POST AddOrder timed out")
        if step == "insufficient":
            raise ccxt.InsufficientFunds("kraken EOrder:Insufficient funds")
        order = self._make_order(symbol, side, amount, params.get("clientOrderId"), step)
        if step == "timeout_after":
            raise ccxt.RequestTimeout("kraken POST AddOrder timed out")
        return {"id": order["id"], "status": "open", "symbol": symbol, "side": side, "amount": amount,
                "filled": 0.0, "clientOrderId": order["clientOrderId"], "info": {}}

    def _make_order(self, symbol, side, amount, cid, step):
        oid = "O" + uuid.uuid4().hex[:10].upper()
        base, quote = symbol.split("/")
        px = self.prices[symbol] * (1.0001 if side == "buy" else 0.9999)
        filled = amount * (0.5 if step == "partial" else 0.0 if step in ("open_only", "stuck") else 1.0)
        cost = filled * px
        fee = cost * self.fee_rate
        o = {"id": oid, "clientOrderId": cid, "symbol": symbol, "side": side, "amount": amount, "filled": filled,
             "average": px if filled else None, "cost": cost, "status": "closed" if filled else "open",
             "timestamp": self.clock_ms, "fee": ({"cost": (fee / px if step == "base_fee" else fee),
                                                  "currency": base if step == "base_fee" else quote} if filled else None),
             "info": {}}
        self.orders[oid] = o
        if filled:
            if side == "buy":
                self.balances[quote] = self.balances.get(quote, 0.0) - cost - (0 if step == "base_fee" else fee)
                self.balances[base] = self.balances.get(base, 0.0) + filled - (fee / px if step == "base_fee" else 0)
            else:
                self.balances[base] = self.balances.get(base, 0.0) - filled
                self.balances[quote] = self.balances.get(quote, 0.0) + cost - fee
        if step == "open_only":                 # becomes filled on the next fetch_order
            o["_fill_on_fetch"] = True
        return o

    def fetch_order(self, id, symbol=None, params=None):
        params = params or {}
        if self.fetch_order_fail > 0:
            self.fetch_order_fail -= 1
            raise ccxt.NetworkError("kraken connection reset")
        if not id and params.get("clientOrderId"):
            for o in self.orders.values():
                if o.get("clientOrderId") == params["clientOrderId"]:
                    return dict(o)
            raise ccxt.OrderNotFound("kraken EOrder:Unknown order")
        o = self.orders.get(id)
        if o is None:
            raise ccxt.OrderNotFound("kraken EOrder:Unknown order")
        if o.pop("_fill_on_fetch", False):
            self.orders[id] = self._fill(o)
        return dict(self.orders[id])

    def _fill(self, o):
        px = self.prices[o["symbol"]]
        o.update(filled=o["amount"], average=px, cost=o["amount"] * px, status="closed")
        o["fee"] = {"cost": o["cost"] * self.fee_rate, "currency": o["symbol"].split("/")[1]}
        return o

    def fetch_closed_orders(self, symbol=None, since=None, limit=None, params=None):
        return [dict(o) for o in self.orders.values() if o["status"] == "closed" and (since is None or o["timestamp"] >= since)]

    def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        return [dict(o) for o in self.orders.values() if o["status"] == "open" and (since is None or o["timestamp"] >= since)]

    def cancel_order(self, id, symbol=None, params=None):
        self.cancel_calls.append(id)
        if self.cancel_fails:
            raise ccxt.NetworkError("cancel failed")
        self.orders[id]["status"] = "canceled"
        return {"id": id, "status": "canceled"}


# ───────────────────────── scripted market for engine tests ─────────────────────────
import numpy as np                                                      # noqa: E402

from crew import strategy as _st                                         # noqa: E402
from crew.exchange import ExchangeError, Tick                            # noqa: E402

H4_MS = 4 * 3600 * 1000
D1_MS = 24 * 3600 * 1000


class ScriptedExchange:
    """Deterministic stand-in for crew.exchange.Exchange: the test sets candles and prices."""
    id = "scripted"
    authenticated = False
    markets_loaded = True

    def __init__(self, symbols):
        self.symbols = list(symbols)
        self.c4: dict[str, np.ndarray] = {}
        self.cd: dict[str, np.ndarray] = {}
        self.px: dict[str, float] = {}
        self.spread = 0.0002
        self.fail: str = ""                    # "" | kind of ExchangeError to raise from every call
        self.calls = {"ticks": 0, "candles": 0}

    def load_markets(self):
        return None

    def has_symbol(self, s):
        return s in self.symbols

    def rules(self, s):
        return _st.Rules({"BTC": 6e-5, "ETH": 2e-3, "SOL": 0.05}[s.split("/")[0]], 0.45, 1e-8)

    def fee_rates(self, s):
        return 0.0026, 0.0016

    def amount_to_precision(self, s, a):
        return float(int(a * 1e8) / 1e8)

    def _maybe_fail(self):
        if self.fail:
            raise ExchangeError(self.fail, f"scripted {self.fail} failure")

    def fetch_candles(self, symbol, timeframe, limit=400):
        self._maybe_fail()
        self.calls["candles"] += 1
        return (self.c4 if timeframe == "4h" else self.cd)[symbol][-limit:].copy()

    def fetch_ticks(self, symbols):
        self._maybe_fail()
        self.calls["ticks"] += 1
        return {s: Tick(s, self.px[s] * (1 - self.spread / 2), self.px[s] * (1 + self.spread / 2), self.px[s], 0.0)
                for s in symbols if s in self.px}

    def fetch_balances(self):
        raise ExchangeError("auth", "no account")


def candle_rows(closes, tf_ms, end_ts_ms, wick=0.002):
    """OHLCV rows whose LAST candle opens at end_ts_ms - tf_ms ... i.e. the series ends right at end_ts_ms."""
    closes = np.asarray(closes, float)
    n = len(closes)
    ts = end_ts_ms - (n - np.arange(n)) * tf_ms
    opens = np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) * (1 + wick)
    lows = np.minimum(opens, closes) * (1 - wick)
    return np.column_stack([ts, opens, highs, lows, closes, np.full(n, 10.0)])


def breakout_market(base_price=100.0, seed=3, n4=380, nd=330, end_ts_ms=1_800_000_000_000 // H4_MS * H4_MS,
                    side_level=0.985, side_noise=0.006, jump=1.012):
    """4h + daily candles that end with a valid BREAKOUT BUY signal on the last CLOSED 4h candle.
    Returns (rows4h incl. one forming candle, rows1d, now_seconds)."""
    rng = np.random.default_rng(seed)
    daily = base_price * np.exp(np.cumsum(rng.normal(0.003, 0.01, nd)))
    daily = daily / daily[-1] * base_price * 0.97
    c = base_price * 0.97 * np.exp(np.cumsum(rng.normal(0.0004, 0.004, n4 - 45)))
    c = c / c[-1] * base_price * side_level
    side = base_price * side_level * (1 + rng.normal(0, side_noise, 44))
    last = np.array([base_price * jump])
    closes = np.r_[c, side, last]
    rows4 = candle_rows(closes, H4_MS, end_ts_ms)                      # last candle = last CLOSED one
    forming_open = end_ts_ms
    forming = np.array([[forming_open, closes[-1], closes[-1] * 1.001, closes[-1] * 0.999, closes[-1], 1.0]])
    rows4 = np.vstack([rows4, forming])
    day_end = (end_ts_ms // D1_MS) * D1_MS
    rowsd = candle_rows(daily, D1_MS, day_end)
    rowsd = np.vstack([rowsd, [[day_end, daily[-1], daily[-1], daily[-1], daily[-1], 1.0]]])
    now = (end_ts_ms + 60_000) / 1000.0                                # one minute after the last closed candle
    return rows4, rowsd, now


class LiveKraken(FakeKraken):
    """FakeKraken that also serves scripted candles (so a LIVE-mode engine can run against it)."""

    def __init__(self, balances=None, prices=None):
        super().__init__(balances, prices)
        self.c4: dict = {}
        self.cd: dict = {}

    def fetch_ohlcv(self, symbol, timeframe="1m", since=None, limit=None, params=None):
        return (self.c4 if timeframe == "4h" else self.cd)[symbol].tolist()
