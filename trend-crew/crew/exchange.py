"""Thin, defensive wrapper around CCXT.

* classifies every failure (network / rate limit / auth / funds / bad request / geo-block ...)
* validates what comes back (prices > 0, sorted unique candles, bid <= ask)
* separates CLOSED candles from the still-forming one
* never retries order creation blindly (that is the LiveBroker's job, with reconciliation)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

try:                                    # ccxt is only needed for the real exchange, not for the simulator
    import ccxt
except ImportError:                     # pragma: no cover
    ccxt = None                         # type: ignore

from . import strategy as st
from .util import timeframe_seconds

log = logging.getLogger("crew.exchange")
_sleep = time.sleep           # indirection so tests can run without real waiting


class ExchangeError(Exception):
    """kind: network | rate | auth | funds | bad_request | geo | maintenance | not_found | exchange"""

    def __init__(self, kind: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.kind = kind
        self.hint = hint

    @property
    def retryable(self) -> bool:
        return self.kind in ("network", "rate", "maintenance")


class AmbiguousOrder(ExchangeError):
    """The request may or may not have reached the exchange (timeout / dropped connection)."""

    def __init__(self, message: str) -> None:
        super().__init__("network", message)


GEO_HINT = ("Die Börse blockiert den Standort dieses Servers (HTTP 451 / 'restricted location'). "
            "Nutze EXCHANGE=kraken oder einen Server außerhalb der USA.")


def classify(exc: BaseException) -> ExchangeError:
    """Map any ccxt / network exception to an ExchangeError."""
    if isinstance(exc, ExchangeError):
        return exc
    msg = f"{type(exc).__name__}: {exc}"
    low = msg.lower()
    if "451" in low or "restricted location" in low or "eligibility" in low:
        return ExchangeError("geo", msg[:300], GEO_HINT)
    if ccxt is not None:
        E = ccxt
        if isinstance(exc, (E.AuthenticationError, E.PermissionDenied, E.AccountSuspended)):
            return ExchangeError("auth", msg[:300], "API-Key oder Berechtigungen prüfen (Handeln erlauben, Auszahlen NICHT erlauben).")
        if isinstance(exc, E.InsufficientFunds):
            return ExchangeError("funds", msg[:300])
        if isinstance(exc, (E.RateLimitExceeded, E.DDoSProtection)):
            return ExchangeError("rate", msg[:300])
        if isinstance(exc, (E.OnMaintenance, E.ExchangeNotAvailable)):
            return ExchangeError("maintenance", msg[:300])
        if isinstance(exc, E.OrderNotFound):
            return ExchangeError("not_found", msg[:300])
        if isinstance(exc, (E.InvalidOrder, E.BadRequest, E.BadSymbol, E.ArgumentsRequired)):
            return ExchangeError("bad_request", msg[:300])
        if isinstance(exc, E.NetworkError):          # RequestTimeout, ExchangeNotAvailable, ...
            return ExchangeError("network", msg[:300])
        if isinstance(exc, E.ExchangeError):
            return ExchangeError("exchange", msg[:300])
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return ExchangeError("network", msg[:300])
    return ExchangeError("exchange", msg[:300])


@dataclass(frozen=True)
class Tick:
    symbol: str
    bid: float
    ask: float
    last: float
    ts: float                       # local time the quote was fetched

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid * 100.0 if self.mid > 0 else float("inf")


def _finite_pos(x) -> bool:
    return isinstance(x, (int, float)) and np.isfinite(x) and x > 0


def clean_candles(rows) -> np.ndarray:
    """rows -> float array [ts, o, h, l, c, v]; sorted, de-duplicated, invalid rows dropped."""
    if not rows:
        return np.empty((0, 6))
    a = np.asarray([[float(x) if x is not None else np.nan for x in r[:6]] for r in rows], dtype=np.float64)
    ok = np.isfinite(a).all(axis=1) & (a[:, 1:5] > 0).all(axis=1) & (a[:, 2] >= a[:, 3] - 1e-12)
    a = a[ok]
    if len(a) == 0:
        return a
    a = a[np.argsort(a[:, 0], kind="stable")]
    _, idx = np.unique(a[:, 0], return_index=True)
    return a[np.sort(idx)]


CLOSE_GRACE_SEC = 2.0            # a candle counts as closed only a moment after its period ended


def split_closed(candles: np.ndarray, timeframe: str, now_ms: float) -> tuple[np.ndarray, Optional[np.ndarray]]:
    """(closed candles, forming candle or None). A candle is closed once its period has fully elapsed."""
    if len(candles) == 0:
        return candles, None
    tf_ms = timeframe_seconds(timeframe) * 1000
    closed_mask = candles[:, 0] + tf_ms + CLOSE_GRACE_SEC * 1000 <= now_ms
    closed = candles[closed_mask]
    forming = candles[~closed_mask]
    return closed, (forming[-1] if len(forming) else None)


class Exchange:
    """Real exchange through CCXT (or any object with the same small surface, e.g. the simulator)."""

    def __init__(self, exchange_id: str = "kraken", *, api_key: str = "", secret: str = "", password: str = "",
                 sandbox: bool = False, client: Any = None, timeout_ms: int = 15_000) -> None:
        self.id = exchange_id
        self.authenticated = bool(api_key and secret) or (client is not None and getattr(client, "apiKey", ""))
        if client is None:
            if ccxt is None:
                raise ExchangeError("exchange", "Das Paket 'ccxt' fehlt (pip install -r requirements.txt)")
            if not hasattr(ccxt, exchange_id):
                raise ExchangeError("bad_request", f"Unbekannte Börse: {exchange_id!r}")
            opts: dict[str, Any] = {"enableRateLimit": True, "timeout": timeout_ms}
            if api_key and secret:
                opts.update(apiKey=api_key, secret=secret)
                if password:
                    opts["password"] = password
            if any(os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")):
                opts["requests_trust_env"] = True       # CCXT ignores proxy variables unless told to listen
            client = getattr(ccxt, exchange_id)(opts)
            if sandbox:
                client.set_sandbox_mode(True)
        self.client = client
        self.markets_loaded = False
        self._rules: dict[str, st.Rules] = {}

    # ────────────────────────── plumbing ──────────────────────────
    def _call(self, fn, *args, retries: int = 2, **kwargs):
        delay = 0.6
        for attempt in range(retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:                     # noqa: BLE001 - classified below
                err = classify(exc)
                if err.retryable and attempt < retries:
                    _sleep(delay * (3 if err.kind == "rate" else 1))
                    delay *= 2.2
                    continue
                raise err from exc

    def load_markets(self) -> None:
        if self.markets_loaded:
            return
        self._call(self.client.load_markets, retries=3)
        self.markets_loaded = True
        self._rules.clear()

    def has_symbol(self, symbol: str) -> bool:
        return symbol in (getattr(self.client, "markets", None) or {})

    def rules(self, symbol: str) -> st.Rules:
        self.load_markets()
        if symbol in self._rules:
            return self._rules[symbol]
        m = self.client.markets.get(symbol)
        if not m:
            raise ExchangeError("bad_request", f"Markt {symbol} gibt es auf {self.id} nicht")
        lim = m.get("limits") or {}
        prec = m.get("precision") or {}
        amount_step = prec.get("amount")
        price_step = prec.get("price")
        mode = getattr(self.client, "precisionMode", 4)
        if mode == 2:                                   # DECIMAL_PLACES -> number of digits
            amount_step = 10 ** -int(amount_step) if amount_step is not None else 1e-8
            price_step = 10 ** -int(price_step) if price_step is not None else 1e-2
        rules = st.Rules(
            min_amount=float(((lim.get("amount") or {}).get("min")) or 0.0),
            min_cost=float(((lim.get("cost") or {}).get("min")) or 0.0),
            amount_step=float(amount_step or 1e-8),
            price_step=float(price_step or 1e-2),
        )
        self._rules[symbol] = rules
        return rules

    def fee_rates(self, symbol: str) -> tuple[float, float]:
        """(taker, maker) as fractions, from the exchange's market data."""
        self.load_markets()
        m = self.client.markets.get(symbol) or {}
        return float(m.get("taker") or 0.0), float(m.get("maker") or 0.0)

    def amount_to_precision(self, symbol: str, amount: float) -> float:
        return float(self.client.amount_to_precision(symbol, amount))

    # ────────────────────────── market data (public) ──────────────────────────
    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 400) -> np.ndarray:
        rows = self._call(self.client.fetch_ohlcv, symbol, timeframe, None, limit)
        c = clean_candles(rows)
        if len(c) == 0:
            raise ExchangeError("exchange", f"Keine Kerzen für {symbol} {timeframe} erhalten")
        return c[-limit:]

    def fetch_ticks(self, symbols: list[str]) -> dict[str, Tick]:
        now = time.time()
        try:
            raw = self._call(self.client.fetch_tickers, symbols)
        except ExchangeError as exc:
            if exc.kind not in ("bad_request", "exchange"):
                raise
            raw = {s: self._call(self.client.fetch_ticker, s) for s in symbols}
        out: dict[str, Tick] = {}
        for s in symbols:
            t = (raw or {}).get(s)
            if not t:
                continue
            bid, ask, last = t.get("bid"), t.get("ask"), t.get("last")
            if not _finite_pos(last):
                last = bid if _finite_pos(bid) else ask
            if not _finite_pos(bid):
                bid = last
            if not _finite_pos(ask):
                ask = last
            if not (_finite_pos(bid) and _finite_pos(ask) and _finite_pos(last)) or ask < bid:
                log.warning("Unbrauchbarer Kurs für %s: bid=%s ask=%s last=%s", s, bid, ask, last)
                continue
            out[s] = Tick(s, float(bid), float(ask), float(last), now)
        return out

    # ────────────────────────── account (private) ──────────────────────────
    def fetch_balances(self) -> dict[str, dict[str, float]]:
        """{currency: {'free': x, 'total': y}} for non-zero balances."""
        bal = self._call(self.client.fetch_balance)
        out: dict[str, dict[str, float]] = {}
        free, total = bal.get("free") or {}, bal.get("total") or {}
        for cur in set(free) | set(total):
            f, t = free.get(cur) or 0.0, total.get(cur) or 0.0
            if f or t:
                out[cur] = {"free": float(f), "total": float(t)}
        return out

    def create_market_order(self, symbol: str, side: str, amount: float, client_id: Optional[str] = None) -> dict:
        """One single attempt. Network trouble raises AmbiguousOrder (the order MAY exist)."""
        params = {"clientOrderId": client_id} if client_id else {}
        try:
            return self.client.create_order(symbol, "market", side, amount, None, params)
        except Exception as exc:                          # noqa: BLE001
            err = classify(exc)
            if err.kind == "network" or (err.kind == "maintenance" and not isinstance(exc, getattr(ccxt, "OnMaintenance", ()))):
                raise AmbiguousOrder(str(err)) from exc
            raise err from exc

    def fetch_order(self, order_id: str, symbol: str, params: Optional[dict] = None) -> dict:
        return self._call(self.client.fetch_order, order_id, symbol, params or {})

    def cancel_order(self, order_id: str, symbol: str) -> None:
        self._call(self.client.cancel_order, order_id, symbol, {}, retries=1)

    def fetch_recent_orders(self, symbol: str, since_ms: int) -> list[dict]:
        out: list[dict] = []
        for fn in (getattr(self.client, "fetch_closed_orders", None), getattr(self.client, "fetch_open_orders", None)):
            if fn is None:
                continue
            try:
                out.extend(self._call(fn, symbol, since_ms, 50) or [])
            except ExchangeError as exc:
                log.warning("Orderliste nicht lesbar (%s): %s", getattr(fn, "__name__", "?"), exc)
        return out

    def check_credentials(self) -> dict[str, Any]:
        """Read-only sanity check of the API key. Never trades. Returns info for the dashboard."""
        info: dict[str, Any] = {"ok": False, "withdraw_enabled": None, "detail": ""}
        if not self.authenticated:
            info["detail"] = "Keine API-Keys hinterlegt"
            return info
        self.fetch_balances()                              # raises auth errors
        info["ok"] = True
        info["detail"] = "API-Key gültig (Guthaben lesbar)"
        if self.id == "binance" and hasattr(self.client, "sapi_get_account_apirestrictions"):
            try:                                           # best effort: is withdrawal switched on for this key?
                r = self.client.sapi_get_account_apirestrictions()
                info["withdraw_enabled"] = bool(r.get("enableWithdrawals"))
            except Exception:                              # noqa: BLE001
                pass
        return info
