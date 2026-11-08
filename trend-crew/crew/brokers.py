"""Order execution.

PaperBroker  - simulated fills at the real bid/ask (+ slippage + fee). Play money.
LiveBroker   - real market orders through CCXT on the user's own exchange account.

Safety rules of the LiveBroker (it handles REAL money):
  * only market orders for the bot's own positions; it never withdraws or transfers anything
  * the quantity is rounded DOWN to the exchange lot size and checked against its minimums
  * hard cap per order (`order_cap`) and a free-balance check BEFORE sending
  * an order that may or may not have reached the exchange (timeout) is never re-sent blindly:
    it is looked up again by its client id / recent order list; if it cannot be found the
    outcome is reported as UNKNOWN and the engine reconciles against the account balances
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Callable, Optional, Protocol

from . import strategy as st
from .exchange import AmbiguousOrder, Exchange, ExchangeError, Tick
from .portfolio import Fill
from .util import floor_to_step

log = logging.getLogger("crew.broker")


class BrokerError(Exception):
    """kind: no_price | funds | min_size | cap | rejected | unknown_outcome | network | auth | exchange | dust"""

    def __init__(self, kind: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.hint = hint


class Broker(Protocol):
    mode: str

    def buy(self, symbol: str, qty: float, ref_price: float) -> Fill: ...
    def sell(self, symbol: str, qty: float, ref_price: float) -> Fill: ...


# ──────────────────────────────── paper ────────────────────────────────
class PaperBroker:
    mode = "paper"

    def __init__(self, ticks: Callable[[str], Optional[Tick]], rules: Callable[[str], st.Rules], p: st.Params,
                 clock: Callable[[], float] = time.time) -> None:
        self._ticks, self._rules, self.p, self._clock = ticks, rules, p, clock

    def _tick(self, symbol: str) -> Tick:
        t = self._ticks(symbol)
        if t is None:
            raise BrokerError("no_price", f"Kein aktueller Kurs für {symbol}")
        return t

    def buy(self, symbol: str, qty: float, ref_price: float = 0.0) -> Fill:
        t = self._tick(symbol)
        rules = self._rules(symbol)
        qty = floor_to_step(qty, rules.amount_step)
        px = t.ask * (1.0 + self.p.slippage)
        if qty <= 0 or qty < rules.min_amount or qty * px < rules.min_cost:
            raise BrokerError("min_size", f"Order unter dem Börsen-Minimum ({symbol})")
        fee = qty * px * self.p.fee_rate
        return Fill(symbol, "buy", qty, px, fee, qty * px + fee, self._clock(), f"paper-{uuid.uuid4().hex[:8]}", True)

    def sell(self, symbol: str, qty: float, ref_price: float = 0.0) -> Fill:
        t = self._tick(symbol)
        rules = self._rules(symbol)
        qty = floor_to_step(qty, rules.amount_step) if qty > rules.amount_step else qty
        if qty <= 0:
            raise BrokerError("min_size", f"Keine Menge zum Verkaufen ({symbol})")
        px = t.bid * (1.0 - self.p.slippage)
        fee = qty * px * self.p.fee_rate
        return Fill(symbol, "sell", qty, px, fee, qty * px - fee, self._clock(), f"paper-{uuid.uuid4().hex[:8]}", True)


# ──────────────────────────────── live ────────────────────────────────
def _split_symbol(symbol: str) -> tuple[str, str]:
    base, quote = symbol.split("/")
    return base, quote.split(":")[0]


class LiveBroker:
    mode = "live"

    def __init__(self, exchange: Exchange, p: st.Params, *, fill_timeout: float = 25.0, poll_interval: float = 0.8,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time) -> None:
        self.ex = exchange
        self.p = p
        self.fill_timeout = fill_timeout
        self.poll_interval = poll_interval
        self._sleep, self._clock = sleep, clock
        self.order_cap: float = 0.0           # max quote value of ONE order (0 = refuse everything); set by the engine

    # ───────────── public ─────────────
    def free_balances(self) -> dict[str, float]:
        try:
            return {cur: v["free"] for cur, v in self.ex.fetch_balances().items()}
        except ExchangeError as exc:
            raise BrokerError(exc.kind, str(exc), exc.hint) from exc

    def buy(self, symbol: str, qty: float, ref_price: float) -> Fill:
        base, quote = _split_symbol(symbol)
        rules = self.ex.rules(symbol)
        qty = floor_to_step(qty, rules.amount_step)
        value = qty * ref_price
        if qty <= 0 or qty < rules.min_amount or value < rules.min_cost:
            raise BrokerError("min_size", f"Order unter dem Börsen-Minimum ({symbol}: {qty} / {value:.2f} {quote})")
        if self.order_cap <= 0 or value > self.order_cap:
            raise BrokerError("cap", f"Order {value:.2f} {quote} über dem Sicherheitslimit {self.order_cap:.2f} {quote}")
        free = self.free_balances()
        need = value * (1.0 + max(self.p.fee_rate, 0.0)) * 1.005
        if free.get(quote, 0.0) < need:
            raise BrokerError("funds", f"Zu wenig freies {quote} auf der Börse ({free.get(quote, 0.0):.2f} < {need:.2f})")
        return self._execute(symbol, "buy", qty, ref_price)

    def sell(self, symbol: str, qty: float, ref_price: float) -> Fill:
        base, quote = _split_symbol(symbol)
        rules = self.ex.rules(symbol)
        free_base = self.free_balances().get(base, 0.0)
        qty = floor_to_step(min(qty, free_base), rules.amount_step)
        if qty <= 0 or qty < rules.min_amount or qty * ref_price < rules.min_cost:
            raise BrokerError("dust", f"Menge {qty} {base} liegt unter dem Börsen-Minimum (Staub)")
        return self._execute(symbol, "sell", qty, ref_price)

    # ───────────── internals ─────────────
    def _execute(self, symbol: str, side: str, qty: float, ref_price: float) -> Fill:
        amount = self.ex.amount_to_precision(symbol, qty)
        cid = uuid.uuid4().hex                       # 32 hex chars: accepted as client order id by Kraken
        t0 = int(self._clock() * 1000)
        try:
            order = self._send(symbol, side, amount, cid, t0)
        except BrokerError:
            raise
        order = self._await_fill(order, symbol, cid, amount)
        return self._to_fill(order, symbol, side, ref_price)

    def _send(self, symbol: str, side: str, amount: float, cid: str, t0: int) -> dict:
        try:
            return self.ex.create_market_order(symbol, side, amount, cid)
        except AmbiguousOrder:
            log.warning("Order %s %s: Antwort unklar – suche die Order auf der Börse", side, symbol)
            return self._find_after_ambiguity(symbol, side, amount, cid, t0)
        except ExchangeError as exc:
            text = str(exc).lower()
            if exc.kind == "bad_request" and ("cl_ord_id" in text or "clientorderid" in text or "client order" in text):
                log.warning("Börse lehnt die Client-ID ab – sende ohne (%s)", exc)
                try:
                    return self.ex.create_market_order(symbol, side, amount, None)
                except AmbiguousOrder:
                    return self._find_after_ambiguity(symbol, side, amount, "", t0)
                except ExchangeError as exc2:
                    raise BrokerError(exc2.kind, str(exc2), exc2.hint) from exc2
            raise BrokerError(exc.kind, str(exc), exc.hint) from exc

    def _find_after_ambiguity(self, symbol: str, side: str, amount: float, cid: str, t0: int) -> dict:
        for attempt in range(4):
            self._sleep(1.5 * (attempt + 1))
            if cid:
                try:
                    o = self.ex.fetch_order("", symbol, {"clientOrderId": cid})
                    if o and o.get("id"):
                        return o
                except ExchangeError:
                    pass
            try:
                for o in self.ex.fetch_recent_orders(symbol, t0 - 5000):
                    if cid and o.get("clientOrderId") == cid:
                        return o
                    same = o.get("side") == side and abs((o.get("amount") or 0.0) - amount) <= amount * 1e-6
                    if same and (o.get("timestamp") or 0) >= t0 - 3000:
                        return o
            except ExchangeError:
                pass
        raise BrokerError("unknown_outcome", f"Ob die {side}-Order für {symbol} ausgeführt wurde, ist unklar. "
                          "Der Bot gleicht beim nächsten Durchlauf mit dem Börsen-Guthaben ab.")

    def _await_fill(self, order: dict, symbol: str, cid: str, amount: float) -> dict:
        oid = order.get("id")
        deadline = self._clock() + self.fill_timeout
        last = order

        def done(o: dict) -> bool:
            status = (o.get("status") or "").lower()
            filled = o.get("filled") or 0.0
            return status in ("closed", "canceled", "cancelled", "expired", "rejected") or (filled >= amount * 0.99999)

        while not done(last):
            if self._clock() >= deadline:
                break
            self._sleep(self.poll_interval)
            try:
                last = self.ex.fetch_order(oid, symbol, {}) if oid else last
            except ExchangeError as exc:
                log.warning("fetch_order(%s) fehlgeschlagen: %s", oid, exc)
        if not done(last):                                  # market order still open after the timeout: cancel it
            try:
                self.ex.cancel_order(oid, symbol)
                last = self.ex.fetch_order(oid, symbol, {})
            except ExchangeError as exc:
                raise BrokerError("unknown_outcome", f"Order {oid} hängt und ließ sich nicht stornieren: {exc}") from exc
        return last

    def _to_fill(self, order: dict, symbol: str, side: str, ref_price: float) -> Fill:
        base, quote = _split_symbol(symbol)
        filled = float(order.get("filled") or 0.0)
        if filled <= 0:
            status = order.get("status")
            raise BrokerError("rejected", f"Die Order wurde nicht ausgeführt (Status: {status})")
        cost = float(order.get("cost") or 0.0)
        avg = float(order.get("average") or 0.0)
        if avg <= 0:
            avg = cost / filled if cost > 0 else ref_price
        if cost <= 0 or abs(cost - avg * filled) > 0.01 * avg * filled:      # trust price x amount over odd 'cost' fields
            cost = avg * filled
        fee_q, fee_b = 0.0, 0.0
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        for f in fees:
            c, cur = float((f or {}).get("cost") or 0.0), (f or {}).get("currency")
            if not c:
                continue
            if cur == quote:
                fee_q += c
            elif cur == base:
                fee_b += c
                fee_q += c * avg
            else:                                            # fee paid in some other token: estimate with the taker rate
                fee_q += cost * self.p.fee_rate
        if not fees:
            fee_q = cost * self.p.fee_rate                   # exchange reported nothing: assume the standard rate
        ts = (order.get("timestamp") or self._clock() * 1000) / 1000.0
        oid = str(order.get("id") or "")
        if side == "buy":
            return Fill(symbol, "buy", filled - fee_b, avg, fee_q, cost + (fee_q - fee_b * avg), ts, oid, False)
        return Fill(symbol, "sell", filled, avg, fee_q, cost - fee_q, ts, oid, False)
