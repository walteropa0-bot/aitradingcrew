"""The trading engine: one background thread that runs 24/7, independent of any browser.

Per cycle (every ~5 s):  Scout reads live prices  ->  Risk manager guards stops & limits  ->  on every newly
CLOSED candle the Scout classifies the trend and the Tech-Analyst evaluates RSI/MACD  ->  the Risk manager
sizes and the broker executes.  The dashboard only reads `snapshot()` and sends commands.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np

import config
from . import indicators as ind
from . import strategy as st
from .agents import AgentStatus, Journal
from .brokers import Broker, BrokerError, LiveBroker, PaperBroker
from .exchange import Exchange, ExchangeError, Tick, split_closed
from .portfolio import EquityHistory, Fill, Portfolio
from .store import StateStore
from .util import de, eur, pct, timeframe_seconds

log = logging.getLogger("crew.engine")


@dataclass
class Settings:
    mode: str = "paper"                       # paper | live
    symbols: tuple = ("BTC/EUR", "ETH/EUR", "SOL/EUR")
    exchange_name: str = "kraken"
    quote: str = "EUR"
    start_capital: float = 10_000.0           # paper: play money
    live_capital: float = 0.0                 # live: maximum the bot may use (0 = live locked)
    live_locked_reason: str = ""              # e.g. no dashboard password
    tick_sec: float = 5.0
    candle_refresh_sec: float = 120.0
    htf_refresh_sec: float = 600.0
    candle_history: int = 400
    htf_history: int = 320
    daily_loss_limit_pct: float = 3.0
    kill_dd_pct: float = 15.0
    max_data_age_sec: float = 180.0
    entry_max_delay_sec: float = 900.0
    entry_max_slip_pct: float = 5.0           # skip if the live price is this far from the signal candle's close
    autostart: bool = True
    reconcile_sec: float = 600.0

    @classmethod
    def from_config(cls) -> "Settings":
        live = not config.PAPER_TRADING
        locked = ""
        if live and not config.DASHBOARD_PASSWORD:
            locked = "Live-Handel ist gesperrt: Setze zuerst ein DASHBOARD_PASSWORD (Secrets)."
        elif live and not (config.API_KEY and config.API_SECRET):
            locked = "Live-Handel ist gesperrt: EXCHANGE_API_KEY und EXCHANGE_API_SECRET fehlen (Secrets)."
        elif live and config.LIVE_CAPITAL <= 0:
            locked = "Live-Handel ist gesperrt: Setze LIVE_CAPITAL (maximaler Einsatz in " + config.QUOTE_CURRENCY + ")."
        return cls(mode="live" if live else "paper", symbols=tuple(config.SYMBOLS), exchange_name=config.EXCHANGE,
                   quote=config.QUOTE_CURRENCY, start_capital=config.PAPER_START_CAPITAL, live_capital=config.LIVE_CAPITAL,
                   live_locked_reason=locked, tick_sec=config.TICK_SEC, candle_refresh_sec=config.CANDLE_REFRESH_SEC,
                   htf_refresh_sec=config.HTF_REFRESH_SEC, candle_history=config.CANDLE_HISTORY, htf_history=config.HTF_HISTORY,
                   daily_loss_limit_pct=config.DAILY_LOSS_LIMIT_PCT, kill_dd_pct=config.MAX_DRAWDOWN_KILL_PCT,
                   max_data_age_sec=config.MAX_DATA_AGE_SEC, autostart=config.AUTOSTART)


@dataclass
class SymbolData:
    closed: Optional[np.ndarray] = None
    forming: Optional[np.ndarray] = None
    htf: Optional[np.ndarray] = None
    next_fetch: float = 0.0
    next_htf: float = 0.0
    dirty: bool = False
    feats: Optional[st.Features] = None
    scout: Optional[st.ScoutReport] = None
    entry: Optional[st.Signal] = None
    exit: Optional[st.Signal] = None
    fetch_errors: int = 0
    live: Optional[dict] = None


class Command:
    def __init__(self, name: str, args: dict) -> None:
        self.name, self.args = name, args
        self.done = threading.Event()
        self.result: dict[str, Any] = {"ok": False, "message": "Nicht verarbeitet"}


class Engine:
    def __init__(self, exchange: Exchange, store: StateStore, params: st.Params, settings: Settings, *,
                 clock: Callable[[], float] = time.time, broker: Optional[Broker] = None) -> None:
        self.ex, self.store, self.p, self.s = exchange, store, params, settings
        self.clock = clock
        self.tf_s = timeframe_seconds(params.timeframe)
        self.symbols = list(settings.symbols)
        self.data: dict[str, SymbolData] = {s: SymbolData() for s in self.symbols}
        self.ticks: dict[str, Tick] = {}
        self._suspect: dict[str, Tick] = {}
        self._next_tick = 0.0
        self._next_live = 0.0
        self._rr = 0
        self._last_risk_work = 0.0
        self._exits: dict[str, dict] = {}
        self._candidates: list[tuple] = []
        self._next_ready_try = 0.0
        self._next_reconcile = 0.0
        self._last_save = 0.0
        self._dirty = True
        self._day_blocked = ""
        self._waiting_logged: set = set()
        self._stop_log: dict[str, float] = {}
        self.markets_ready = False
        self.live_ready = settings.mode == "paper"
        self.live_error = settings.live_locked_reason
        self.cred_info: dict[str, Any] = {}
        self.ex_status: dict[str, Any] = {"ok": True, "last_ok": 0.0, "error": "", "kind": "", "hint": "", "consecutive": 0, "latency_ms": 0}
        self.save_error = ""
        self.internal_error = ""
        self._err_log_ts: dict[str, float] = {}
        self.commands: "queue.Queue[Command]" = queue.Queue()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.loop_ts = 0.0
        self.started_ts = clock()
        self.journal = Journal(clock=clock)
        self.scout_a = AgentStatus("scout", "Trend-Scout", "Live-Kurse & Trend", "radar", clock)
        self.analyst_a = AgentStatus("analyst", "Tech-Analyst", "RSI · MACD · Signale", "chart", clock)
        self.risk_a = AgentStatus("risk", "Risikomanager", "Größe · Stopps · Limits", "shield", clock)
        self.agents = [self.scout_a, self.analyst_a, self.risk_a]
        self.pf = self._load_portfolio()
        if not self.pf.history.points:                                # baseline so the chart starts at the starting capital
            self.pf.history.add(clock(), self.pf.cash, force=True)
        self.broker: Broker = broker or self._make_broker()
        self._snapshot: dict[str, Any] = {}
        self.journal.add("system", f"Start im {'DEMO' if settings.mode == 'paper' else 'LIVE'}-Modus · {settings.exchange_name} · "
                         f"{params.timeframe}-Kerzen · {', '.join(s.split('/')[0] for s in self.symbols)}", "info", "system")
        self._publish(clock())

    # ───────────────────────────── setup ─────────────────────────────
    def _load_portfolio(self) -> Portfolio:
        mode = self.s.mode
        try:
            raw = self.store.load()
            if raw:
                pf = Portfolio.from_dict(raw, mode)
                if not self.s.autostart:
                    pf.running = False
                log.info("Gespeicherter Stand geladen (%d Positionen, %d Trades)", len(pf.positions), len(pf.trades))
                return pf
        except Exception as exc:                                  # corrupt/incompatible state must not stop the bot
            log.error("Gespeicherter Stand unbrauchbar (%s) – starte neu", exc)
            self.journal.add("system", "Gespeicherter Stand war unbrauchbar – es wird mit einem neuen Konto gestartet", "warn", "system")
        capital = self.s.start_capital if self.s.mode == "paper" else self.s.live_capital
        pf = Portfolio(capital, mode)
        pf.created_ts = self.clock() if self.s.mode == "paper" else 0.0
        return pf

    def _make_broker(self) -> Broker:
        if self.s.mode == "paper":
            return PaperBroker(lambda sym: self.ticks.get(sym), self.ex.rules, self.p, self.clock)
        return LiveBroker(self.ex, self.p)

    # ───────────────────────────── thread control ─────────────────────────────
    def start_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="trendcrew-engine", daemon=True)
        self._thread.start()

    def stop_thread(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self) -> None:
        log.info("Engine-Thread gestartet")
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self.step()
            except Exception:                                     # last line of defence: the loop must survive anything
                self.internal_error = traceback.format_exc(limit=3).strip().splitlines()[-1]
                log.exception("Unerwarteter Fehler im Engine-Durchlauf")
            speed = getattr(self.clock, "speed", 1.0)
            wait = max(0.2, self.s.tick_sec / speed - (time.time() - t0))
            self._wake.wait(wait)
            self._wake.clear()
        log.info("Engine-Thread beendet")

    # ───────────────────────────── commands (called from the UI thread) ─────────────────────────────
    def command(self, name: str, timeout: float = 4.0, **args) -> dict[str, Any]:
        cmd = Command(name, args)
        if not self.is_alive():                                   # no thread (tests): run inline
            self._handle(cmd)
            return cmd.result
        self.commands.put(cmd)
        self._wake.set()
        if not cmd.done.wait(timeout):
            return {"ok": True, "message": "Befehl angenommen – wird gleich ausgeführt"}
        return cmd.result

    def snapshot(self) -> dict[str, Any]:
        return self._snapshot

    # ───────────────────────────── main cycle ─────────────────────────────
    def step(self) -> None:
        now = self.clock()
        self.loop_ts = now
        for name, fn in (("commands", self._process_commands), ("ready", self._ensure_ready), ("ticks", self._refresh_ticks),
                         ("candles", self._refresh_candles), ("analyze", self._analyze_all), ("live", self._live_indicators), ("breakers", self._circuit_breakers),
                         ("positions", self._manage_positions), ("exits", self._run_exits), ("entries", self._maybe_enter),
                         ("pending", self._reconcile_pending), ("books", self._bookkeeping)):
            try:
                fn(now)
            except Exception as exc:                              # noqa: BLE001 - isolate stages from each other
                self.internal_error = f"{name}: {type(exc).__name__}: {exc}"
                if now - self._err_log_ts.get(name, 0) > 30:
                    self._err_log_ts[name] = now
                    log.exception("Fehler in Stufe %s", name)
                    self.journal.add("system", f"Interner Fehler ({name}): {type(exc).__name__}: {exc}", "bad", "alert")
        self._save(now)
        self._publish(self.clock())

    # ───────────────────────────── readiness ─────────────────────────────
    def _ensure_ready(self, now: float) -> None:
        if self.markets_ready and (self.live_ready or self.s.mode == "paper"):
            return
        if now < self._next_ready_try:
            return
        self._next_ready_try = now + 20
        if not self.markets_ready:
            self.scout_a.work(f"Scout verbindet mit {self.s.exchange_name} …")
            try:
                self.ex.load_markets()
                missing = [s for s in self.symbols if not self.ex.has_symbol(s)]
                if missing:
                    raise ExchangeError("bad_request", f"Diese Märkte gibt es auf {self.s.exchange_name} nicht: {', '.join(missing)}")
                self.markets_ready = True
                self.journal.add("scout", f"Verbunden mit {self.s.exchange_name} · {len(self.symbols)} Märkte geladen", "ok")
            except ExchangeError as exc:
                self._note_error(exc, "markets", now)
                return
        if self.s.mode == "live" and not self.live_ready:
            if self.s.live_locked_reason:
                self.live_error = self.s.live_locked_reason
                return
            try:
                info = self.ex.check_credentials()
                self.cred_info = info
                if not info.get("ok"):
                    self.live_error = info.get("detail") or "API-Key ungültig"
                    return
                if info.get("withdraw_enabled"):
                    self.live_error = "Dein API-Key darf Geld AUSZAHLEN. Erstelle einen neuen Key ohne Auszahlungsrecht."
                    self.journal.add("system", self.live_error, "bad", "alert")
                    return
                balances = self.ex.fetch_balances()
                free_quote = balances.get(self.s.quote, {}).get("free", 0.0)
                if self.pf.created_ts == 0.0:                     # first live start: fix the bot's budget
                    cap = min(self.s.live_capital, free_quote)
                    if cap < 10:
                        self.live_error = f"Zu wenig freies {self.s.quote} auf der Börse ({de(free_quote)}) – mindestens 10 nötig."
                        return
                    self.pf.start_capital = self.pf.cash = self.pf.peak = self.pf.day_start_equity = cap
                    self.pf.created_ts = now
                    self.pf.history = EquityHistory()
                    self.pf.history.add(now, cap, force=True)
                    self._dirty = True
                    self.journal.add("system", f"Live-Budget festgelegt: {eur(cap, self.s.quote)} (von {eur(free_quote, self.s.quote)} frei)", "ok", "system")
                self.live_ready, self.live_error = True, ""
                self._reconcile_balances(now, startup=True)
                self.journal.add("system", "API-Key geprüft · Live-Handel bereit", "ok", "system")
            except ExchangeError as exc:
                self.live_error = f"{exc} {exc.hint}".strip()
                self._note_error(exc, "credentials", now)

    # ───────────────────────────── market data ─────────────────────────────
    def _note_error(self, exc: ExchangeError, what: str, now: float) -> None:
        es = self.ex_status
        es.update(ok=False, error=str(exc)[:200], kind=exc.kind, hint=exc.hint, consecutive=es["consecutive"] + 1)
        if now - self._err_log_ts.get(what, 0) > 60 or es["consecutive"] == 1:
            self._err_log_ts[what] = now
            self.journal.add("scout", f"Börsen-Fehler ({what}): {str(exc)[:140]}", "bad" if exc.kind in ("auth", "geo") else "warn")
            log.warning("Börsen-Fehler (%s, %s): %s", what, exc.kind, exc)

    def _note_ok(self, now: float, latency_ms: float) -> None:
        self.ex_status.update(ok=True, last_ok=now, error="", kind="", hint="", consecutive=0, latency_ms=round(latency_ms))

    def _refresh_ticks(self, now: float) -> None:
        if not self.markets_ready or now < self._next_tick:
            return
        self.scout_a.work(f"Scout liest Live-Kurse von {self.s.exchange_name.capitalize()} …")
        t0 = time.perf_counter()
        try:
            fresh = self.ex.fetch_ticks(self.symbols)
        except ExchangeError as exc:
            self._note_error(exc, "ticks", now)
            self._next_tick = now + min(60.0, self.s.tick_sec * (2 ** min(self.ex_status["consecutive"], 4)))
            age = now - max((t.ts for t in self.ticks.values()), default=now)
            self.scout_a.done("error" if exc.kind in ("auth", "geo") else "warn",
                              "Keine Kursdaten von der Börse" + (f" seit {int(age)} s" if self.ticks else ""), (exc.hint or str(exc))[:140])
            return
        self._note_ok(now, (time.perf_counter() - t0) * 1000)
        self._next_tick = now + self.s.tick_sec
        for sym, t in fresh.items():
            prev = self.ticks.get(sym)
            t = dataclasses.replace(t, ts=now)
            if prev and prev.mid > 0 and abs(t.mid / prev.mid - 1.0) > 0.15 and sym not in self._suspect:
                self._suspect[sym] = t                            # one wild tick: wait for confirmation before believing it
                self.journal.add("scout", f"{sym}: Kurssprung {de((t.mid / prev.mid - 1) * 100, 1, True)} % – warte auf Bestätigung", "warn")
                continue
            self._suspect.pop(sym, None)
            self.ticks[sym] = t
        parts = [f"{s.split('/')[0]} {de(self.ticks[s].last, 2 if self.ticks[s].last < 1000 else 0)} {self._cur()}" for s in self.symbols if s in self.ticks]
        self.scout_a.done("ok", " · ".join(parts) or "Keine Kurse", f"Verzögerung {self.ex_status['latency_ms']} ms")

    def _cur(self) -> str:
        return {"EUR": "€", "USD": "$", "USDT": "$", "USDC": "$"}.get(self.s.quote, self.s.quote)

    def _refresh_candles(self, now: float) -> None:
        if not self.markets_ready:
            return
        budget = 2                                                # keep each cycle short: stops must keep ticking
        tf_ms = self.tf_s * 1000
        self._rr = (self._rr + 1) % max(1, len(self.symbols))     # round-robin so no symbol can starve the others
        fetched_any, failed = False, ""
        for sym in self.symbols[self._rr:] + self.symbols[:self._rr]:
            sd = self.data[sym]
            if budget <= 0:
                break
            if sd.closed is None or now >= sd.next_fetch:
                budget -= 1
                fetched_any = True
                self.scout_a.work(f"Scout analysiert Kerzen {sym} …")
                try:
                    arr = self.ex.fetch_candles(sym, self.p.timeframe, self.s.candle_history)
                except ExchangeError as exc:
                    sd.fetch_errors += 1
                    sd.next_fetch = now + min(120.0, 10.0 * sd.fetch_errors)
                    self._note_error(exc, f"candles {sym}", now)
                    failed = f"{sym}: {exc}"[:120]
                    continue
                sd.fetch_errors = 0
                closed, forming = split_closed(arr, self.p.timeframe, now * 1000)
                changed = sd.closed is None or len(closed) != len(sd.closed) or (len(closed) and closed[-1, 0] != sd.closed[-1, 0])
                sd.closed, sd.forming = closed, forming
                sd.dirty = sd.dirty or bool(changed)
                if len(closed) == 0:
                    sd.next_fetch = now + 30
                    continue
                next_close = (closed[-1, 0] + 2 * tf_ms) / 1000.0 + 4.0       # when the forming candle will have closed
                sd.next_fetch = min(now + self.s.candle_refresh_sec, next_close) if next_close > now else now + 10.0
            if sd.htf is None or now >= sd.next_htf:
                try:
                    h = self.ex.fetch_candles(sym, self.p.htf_timeframe, self.s.htf_history)
                    hc, _ = split_closed(h, self.p.htf_timeframe, now * 1000)
                    if sd.htf is None or len(hc) != len(sd.htf) or (len(hc) and hc[-1, 0] != sd.htf[-1, 0]):
                        sd.dirty = True
                    sd.htf = hc
                    sd.next_htf = now + self.s.htf_refresh_sec
                except ExchangeError as exc:
                    sd.next_htf = now + 60
                    self._note_error(exc, f"htf {sym}", now)

        if failed and self.scout_a.state == "working":
            self.scout_a.done("warn", "Kerzen-Abruf fehlgeschlagen – neuer Versuch folgt", failed)
        elif fetched_any and self.scout_a.state == "working":
            self.scout_a.done("ok", "Kerzen aktuell · " + self._scout_trend_text(), "Tageskerzen steuern den Trend-Filter")

    def _scout_trend_text(self) -> str:
        parts = [f"{s.split('/')[0]} {self._arrow(self.data[s].scout)}" for s in self.symbols if self.data[s].scout]
        return ("Trend " + " · ".join(parts)) if parts else "sammelt Daten"

    # ───────────────────────────── analysis ─────────────────────────────
    def _analyze_all(self, now: float) -> None:
        any_new = False
        for sym in self.symbols:
            sd = self.data[sym]
            if sd.dirty and sd.closed is not None and len(sd.closed) > 5:
                sd.dirty = False
                self._analyze(sym, now)
                any_new = True
        if any_new:
            trend = self._scout_trend_text().replace("Trend ", "", 1)
            ok_list = [s.split("/")[0] for s in self.symbols if self.data[s].scout and self.data[s].scout.tradable]
            self.scout_a.done("ok", f"Tages-Trend: {trend}", f"Handelbar: {', '.join(ok_list) or 'keiner'}")

    @staticmethod
    def _arrow(rep: Optional[st.ScoutReport]) -> str:
        return {"up": "↑", "down": "↓", "side": "→"}.get(rep.trend if rep else "", "?")

    def _analyze(self, sym: str, now: float) -> None:
        sd = self.data[sym]
        self.analyst_a.work(f"Tech-Analyst berechnet RSI & MACD ({sym}) …")
        f = st.compute_features(sd.closed, self.p, sd.htf)
        i = f.n - 1
        tick = self.ticks.get(sym)
        sd.feats = f
        sd.scout = st.scout_report(sym, f, i, self.p, price=tick.last if tick else None,
                                   spread_pct=tick.spread_pct if tick else None,
                                   data_age_sec=(now - tick.ts) if tick else None, max_age_sec=self.s.max_data_age_sec)
        sd.entry = st.entry_signal(f, i, self.p)
        sd.exit = st.exit_signal(f, i, self.p)
        pos = self.pf.positions.get(sym)
        if pos is not None and math.isfinite(f.atr[i]):
            pos.atr = float(f.atr[i])
            self._dirty = True
        bar_ts = float(f.ts[i])
        if bar_ts > self.pf.last_bar.get(sym, 0.0):                # a candle closed since we last looked
            self.pf.last_bar[sym] = bar_ts
            self._dirty = True
            bar_age = now - (bar_ts / 1000.0 + self.tf_s)
            base = sym.split("/")[0]
            self.journal.add("analyst", f"{base}: neue {self.p.timeframe}-Kerze · RSI {de(f.rsi[i], 0)} · "
                             f"MACD {'↑' if f.macd_hist[i] > f.macd_hist[i - 1] else '↓'} · "
                             + ("Position offen – " + sd.exit.summary if pos is not None else sd.entry.summary),
                             "ok" if (sd.entry.action == "BUY" and pos is None) else "info")
            if pos is not None:
                if sd.exit.action == "SELL":
                    self._exits.setdefault(sym, self._intent(now, sd.exit.summary, hard=True))
            elif sd.entry.action == "BUY":
                if bar_age > self.s.entry_max_delay_sec:
                    self.journal.add("analyst", f"{base}: Kaufsignal zu alt ({int(bar_age / 60)} min nach Kerzenschluss) – übersprungen", "warn")
                else:
                    self._candidates.append((sd.scout.rank, sym, bar_ts))
        txt = sd.entry.summary
        state = "ok"
        self.analyst_a.done(state, f"{sym.split('/')[0]}: RSI {de(f.rsi[i], 0)} · MACD {'↑' if f.macd_hist[i] > f.macd_hist[i - 1] else '↓'}", txt)

    def _live_indicators(self, now: float) -> None:
        """RSI / MACD on the still-forming candle (price = latest tick). Display only - signals use closed candles."""
        if now < self._next_live:
            return
        self._next_live = now + 15.0
        parts_rsi, parts_macd = [], []
        started = False
        for sym in self.symbols:
            sd, t = self.data[sym], self.ticks.get(sym)
            if sd.closed is None or len(sd.closed) < 40 or t is None:
                continue
            if not started:
                started = True
                self.analyst_a.work("Tech-Analyst berechnet Live-RSI & MACD …")
            closes = np.append(sd.closed[:, 4], t.last)
            rsi = float(ind.rsi(closes, self.p.rsi_period)[-1])
            _, _, hist = ind.macd(closes, self.p.macd_fast, self.p.macd_slow, self.p.macd_signal)
            sd.live = {"rsi": rsi if rsi == rsi else None, "hist": float(hist[-1]) if hist[-1] == hist[-1] else None,
                       "up": bool(hist[-1] > hist[-2]) if hist[-1] == hist[-1] and hist[-2] == hist[-2] else None}
            base = sym.split("/")[0]
            parts_rsi.append(f"{base} {de(rsi, 0)}")
            parts_macd.append(f"{base} {'↑' if sd.live['up'] else '↓'}")
        if started:
            self.analyst_a.done("ok", "RSI live: " + " · ".join(parts_rsi), "MACD: " + " · ".join(parts_macd) + " · Signale nur bei Kerzenschluss")

    # ───────────────────────────── positions & stops ─────────────────────────────
    def _intent(self, now: float, reason: str, hard: bool) -> dict:
        return {"reason": reason, "since": now, "next_try": now, "attempts": 0, "hard": hard}

    def _fresh_tick(self, sym: str, now: float) -> Optional[Tick]:
        t = self.ticks.get(sym)
        return t if t is not None and now - t.ts <= self.s.max_data_age_sec else None

    def _manage_positions(self, now: float) -> None:
        if not self.markets_ready or not self.ticks:
            return
        if now - self._last_risk_work >= self.s.tick_sec * 0.9:
            self._last_risk_work = now
            self.risk_a.work("Risikomanager prüft Stopps & Trend …" if self.pf.positions else "Risikomanager prüft Trend & Tageslimit …")
        if not self.pf.positions:
            self._risk_summary(now)
            return
        for sym, pos in list(self.pf.positions.items()):
            t = self._fresh_tick(sym, now)
            if t is None:
                continue
            mark = t.bid
            if mark > pos.highest:
                pos.highest = mark
            new_stop = st.trail_stop(pos.stop, pos.highest, pos.atr, self.p)
            if new_stop > pos.stop + 1e-12:
                moved = (new_stop / pos.stop - 1.0) * 100.0
                pos.stop = new_stop
                self._dirty = True
                if moved >= 0.5 or now - self._stop_log.get(sym, 0) > 3600:
                    self._stop_log[sym] = now
                    self.journal.add("risk", f"{sym.split('/')[0]}: Trailing-Stop auf {de(new_stop, 2)} {self._cur()} nachgezogen", "info")
            if mark <= pos.stop and sym not in self._exits:
                self._exits[sym] = self._intent(now, f"Trailing-Stop {de(pos.stop, 2)} {self._cur()} ausgelöst", hard=False)
                self.journal.add("risk", f"{sym.split('/')[0]}: STOPP ausgelöst bei {de(mark, 2)} {self._cur()}", "bad")
        self._risk_summary(now)

    def _risk_summary(self, now: float) -> None:
        marks = self._marks()
        eq = self.pf.equity(marks)
        day = (eq / self.pf.day_start_equity - 1.0) * 100.0 if self.pf.day_start_equity > 0 else 0.0
        n = len(self.pf.positions)
        if self.pf.halted:
            self.risk_a.done("error", f"Gestoppt: {self.pf.halted}", "Zum Fortfahren [BOT STARTEN] drücken")
        elif self._day_blocked:
            self.risk_a.done("warn", "Tageslimit erreicht – keine neuen Käufe", self._day_blocked)
        else:
            self.risk_a.done("ok", f"Alles im Rahmen · {n} Position{'en' if n != 1 else ''} offen",
                             f"Tag {pct(day)} (Limit −{de(self.s.daily_loss_limit_pct, 1)} %)")

    def _run_exits(self, now: float) -> None:
        for sym in list(self._exits):
            it = self._exits[sym]
            pos = self.pf.positions.get(sym)
            if pos is None:
                del self._exits[sym]
                continue
            if now < it["next_try"]:
                continue
            t = self._fresh_tick(sym, now)
            if not it["hard"] and t is not None and t.bid > pos.stop * 1.0005 and it["attempts"] > 0:
                del self._exits[sym]                                # stop condition vanished while retrying: keep the position
                self.journal.add("risk", f"{sym.split('/')[0]}: Kurs über dem Stopp – Verkauf abgebrochen", "info")
                continue
            ref = t.bid if t else pos.entry_price
            self.risk_a.work(f"Risikomanager verkauft {sym.split('/')[0]} …", it["reason"])
            ok = self._execute_sell(sym, it["reason"], ref, now)
            if ok:
                if it["hard"] and sym in self.pf.positions:         # partial fill: keep selling until nothing is left
                    it["next_try"], it["attempts"] = now + 1.0, 0
                else:
                    del self._exits[sym]
            else:
                it["attempts"] += 1
                it["next_try"] = now + min(60.0, 2.0 ** it["attempts"])

    def _execute_sell(self, sym: str, reason: str, ref: float, now: float) -> bool:
        pos = self.pf.positions[sym]
        base = sym.split("/")[0]
        try:
            fill = self.broker.sell(sym, pos.qty, ref)
        except BrokerError as exc:
            if exc.kind == "dust":
                self.pf.drop_position(sym, ref, now, "Staub unter Börsen-Minimum")
                self.journal.add("risk", f"{base}: Rest unter dem Börsen-Minimum – aus dem Bot-Buch entfernt", "warn", "trade")
                self._dirty = True
                return True
            if exc.kind == "unknown_outcome":
                self.pf.pending[sym] = {"side": "sell", "qty": pos.qty, "ts": now, "ref": ref, "base_before": pos.qty}
                self._dirty = True
            self.journal.add("risk", f"{base}: Verkauf fehlgeschlagen ({exc.kind}): {str(exc)[:120]}", "bad", "alert")
            return False
        cool = now + self.p.cooldown_bars * self.tf_s
        cost_part = pos.cost * (min(fill.qty, pos.qty) / pos.qty)
        losing = fill.quote_amount < cost_part
        rec = self.pf.close_position(fill, reason, cooldown_until=cool if (losing or "Stop" in reason) else 0.0)
        self._dirty = True
        self.pf.mark(now, self.pf.equity(self._marks()), force=True)
        self.journal.add("trade", f"VERKAUF {base} {de(rec.qty, 6)} @ {de(rec.price, 2)} {self._cur()} · {pct(rec.pnl_pct)} "
                         f"({eur(rec.pnl, self.s.quote, 2, True)}) · {reason}", "ok" if rec.pnl >= 0 else "bad", "trade")
        return True

    # ───────────────────────────── circuit breakers ─────────────────────────────
    def _marks(self) -> dict[str, float]:
        return {s: t.bid for s, t in self.ticks.items() if t.bid > 0}

    def _circuit_breakers(self, now: float) -> None:
        marks = self._marks()
        eq = self.pf.equity(marks)
        day_key = time.strftime("%Y-%m-%d", time.gmtime(now))
        if self.pf.roll_day(day_key, eq):
            self._day_blocked = ""
            self._dirty = True
        if self.pf.halted or not self.ticks:
            return
        if st.drawdown_hit(self.pf.peak, eq, self.s.kill_dd_pct):
            self.journal.add("risk", f"NOTBREMSE: {de((eq / self.pf.peak - 1) * 100, 1)} % vom Höchststand – alles wird verkauft", "bad", "alert")
            self._panic(f"Drawdown-Limit −{de(self.s.kill_dd_pct, 0)} % erreicht", now)
        elif not self._day_blocked and st.daily_loss_hit(self.pf.day_start_equity, eq, self.s.daily_loss_limit_pct):
            self._day_blocked = f"Verlust heute {de((eq / self.pf.day_start_equity - 1) * 100, 1)} % – Kaufpause bis 00:00 UTC"
            self.journal.add("risk", f"Tagesverlust-Limit erreicht ({self._day_blocked}). Keine neuen Käufe bis morgen.", "warn", "alert")

    def _panic(self, reason: str, now: float) -> None:
        self.pf.running = False
        self.pf.halted = reason
        self._candidates.clear()
        for sym in self.pf.positions:
            self._exits[sym] = self._intent(now, f"NOT-AUS: {reason}", hard=True)
        self._dirty = True
        self.journal.add("system", f"NOT-AUS · {reason} · {len(self.pf.positions)} Position(en) werden verkauft", "bad", "alert")

    # ───────────────────────────── entries ─────────────────────────────
    TRANSIENT = ("keine aktuellen Kursdaten", "Live-Handel noch nicht bereit", "eine unklare Order")

    def _maybe_enter(self, now: float) -> None:
        cands, self._candidates = sorted(self._candidates, reverse=True), []
        for rank, sym, bar_ts in cands:
            base = sym.split("/")[0]
            why = self._entry_block(sym, now)
            if why:
                fresh = now - (bar_ts / 1000.0 + self.tf_s) <= self.s.entry_max_delay_sec
                if why.startswith(self.TRANSIENT) and fresh:      # temporary trouble: retry while the signal is still fresh
                    self._candidates.append((rank, sym, bar_ts))
                    self.risk_a.done("warn", f"{base}: Kauf wartet", why)
                    if (sym, bar_ts) not in self._waiting_logged:
                        self._waiting_logged.add((sym, bar_ts))
                        self.journal.add("risk", f"{base}: Kaufsignal wartet – {why}", "warn")
                    continue
                self.journal.add("risk", f"{base}: Kauf abgelehnt – {why}", "warn")
                self.risk_a.done("warn", f"{base}: Kauf abgelehnt", why)
                continue
            self._enter(sym, bar_ts, now)

    def _entry_block(self, sym: str, now: float) -> str:
        if not self.pf.running:
            return "Bot ist pausiert"
        if self.pf.halted:
            return f"gestoppt ({self.pf.halted})"
        if self.s.mode == "live" and not self.live_ready:
            return "Live-Handel noch nicht bereit"
        if self._day_blocked:
            return "Tagesverlust-Limit aktiv"
        if sym in self.pf.positions:
            return "Position besteht bereits"
        if sym in self.pf.pending:
            return "eine unklare Order wird noch abgeglichen"
        if len(self.pf.positions) >= self.p.max_open_positions:
            return f"maximal {self.p.max_open_positions} Positionen gleichzeitig"
        if self.pf.cooldowns.get(sym, 0.0) > now:
            return f"Abkühlphase nach Verlust (noch {int((self.pf.cooldowns[sym] - now) / 60)} min)"
        sd = self.data[sym]
        if sd.scout is None or not sd.scout.tradable:
            return "Scout: nicht handelbar (" + ", ".join(sd.scout.notes if sd.scout else ()) + ")"
        t = self._fresh_tick(sym, now)
        if t is None:
            return "keine aktuellen Kursdaten"
        if sd.feats is not None and sd.feats.close[-1] > 0 and abs(t.ask / float(sd.feats.close[-1]) - 1.0) * 100 > self.s.entry_max_slip_pct:
            return "Kurs weicht stark von der Signal-Kerze ab"
        return ""

    def _enter(self, sym: str, bar_ts: float, now: float) -> None:
        base = sym.split("/")[0]
        sd, t = self.data[sym], self.ticks[sym]
        f = sd.feats
        atr = float(f.atr[-1])
        marks = self._marks()
        equity = self.pf.equity(marks)
        self.risk_a.work(f"Risikomanager berechnet Positionsgröße {base} …")
        size = st.position_size(equity=equity, cash=self.pf.cash, exposure=self.pf.exposure(marks), price=t.ask * (1 + self.p.slippage),
                                atr=atr, p=self.p, rules=self.ex.rules(sym))
        if size.qty <= 0:
            self.journal.add("risk", f"{base}: Kauf abgelehnt – {size.reason}", "warn")
            self.risk_a.done("warn", f"{base}: keine Position möglich", size.reason)
            return
        if isinstance(self.broker, LiveBroker):
            self.broker.order_cap = max(equity, self.pf.start_capital) * self.p.max_position_pct * 1.15
        try:
            fill = self.broker.buy(sym, size.qty, t.ask)
        except BrokerError as exc:
            self.journal.add("risk", f"{base}: Kauf fehlgeschlagen ({exc.kind}): {str(exc)[:140]}", "bad", "alert")
            self.risk_a.done("error" if exc.kind in ("auth", "exchange") else "warn", f"{base}: Kauf fehlgeschlagen", str(exc)[:100])
            if exc.kind == "unknown_outcome":
                free = self.broker.free_balances() if isinstance(self.broker, LiveBroker) else {}
                self.pf.pending[sym] = {"side": "buy", "qty": size.qty, "ts": now, "ref": t.ask, "atr": atr,
                                        "base_before": free.get(base, 0.0), "bar_ts": bar_ts}
                self._dirty = True
            return
        stop = st.initial_stop(fill.price, atr, self.p)
        risk_amount = fill.qty * (fill.price - stop) + fill.fee * 2
        self.pf.open_position(fill, stop=stop, atr=atr, risk_amount=risk_amount, bar_ts=bar_ts, reason=sd.entry.summary)
        self._dirty = True
        self.pf.mark(now, self.pf.equity(self._marks()), force=True)
        slip = (fill.price / t.ask - 1.0) * 100.0
        self.journal.add("trade", f"KAUF {base} {de(fill.qty, 6)} @ {de(fill.price, 2)} {self._cur()} · Stop {de(stop, 2)} · "
                         f"Risiko {eur(risk_amount, self.s.quote)}" + (f" · Slippage {de(slip, 2, True)} %" if abs(slip) > 0.3 else ""), "ok", "trade")
        self.risk_a.done("ok", f"{de(fill.qty, 6)} {base} · Stop {de(stop, 2)} {self._cur()}",
                         f"Risiko {eur(risk_amount, self.s.quote)} ({de(risk_amount / equity * 100, 2)} % vom Konto) · begrenzt durch {size.capped_by}")
        self.analyst_a.done("ok", f"Kaufsignal {base} ausgeführt", sd.entry.summary)

    # ───────────────────────────── live reconciliation ─────────────────────────────
    def _reconcile_pending(self, now: float) -> None:
        if not self.pf.pending or not isinstance(self.broker, LiveBroker) or not self.live_ready:
            return
        free = self.broker.free_balances()
        for sym, p in list(self.pf.pending.items()):
            base = sym.split("/")[0]
            held = free.get(base, 0.0)
            delta = held - p["base_before"]
            age = now - p["ts"]
            if p["side"] == "buy":
                if delta >= 0.9 * p["qty"]:
                    t = self.ticks.get(sym)
                    px = t.ask if t else p["ref"]
                    qty = min(delta, p["qty"])
                    fill = Fill(sym, "buy", qty, px, qty * px * self.p.fee_rate, qty * px * (1 + self.p.fee_rate), now, "reconciled")
                    stop = st.initial_stop(px, p["atr"], self.p)
                    self.pf.open_position(fill, stop=stop, atr=p["atr"], risk_amount=qty * (px - stop), bar_ts=p.get("bar_ts", 0.0), reason="abgeglichen")
                    self.journal.add("risk", f"{base}: unklare Order war ausgeführt – Position übernommen (Stop {de(stop, 2)})", "warn", "alert")
                    del self.pf.pending[sym]
                elif age > 180:
                    self.journal.add("risk", f"{base}: unklare Kauf-Order wurde NICHT ausgeführt – Eintrag gelöscht", "info")
                    del self.pf.pending[sym]
            else:
                pos = self.pf.positions.get(sym)
                if pos is None:
                    del self.pf.pending[sym]
                elif held <= p["base_before"] - 0.9 * p["qty"] or held < pos.qty * 0.1:
                    t = self.ticks.get(sym)
                    self.pf.drop_position(sym, (t.bid if t else p["ref"]), now, "abgeglichen (Verkauf war ausgeführt)")
                    self.journal.add("risk", f"{base}: unklarer Verkauf war ausgeführt – Position geschlossen", "warn", "alert")
                    del self.pf.pending[sym]
                elif age > 180:
                    del self.pf.pending[sym]
            self._dirty = True

    def _reconcile_balances(self, now: float, startup: bool = False) -> None:
        """Live: the bot's book must never claim more coins than the account holds."""
        if not isinstance(self.broker, LiveBroker) or not self.pf.positions:
            return
        bal = self.ex.fetch_balances()
        for sym, pos in list(self.pf.positions.items()):
            base = sym.split("/")[0]
            held = bal.get(base, {}).get("total", 0.0)
            if held < pos.qty * 0.995:
                self.journal.add("system", f"{base}: Bot-Buch {de(pos.qty, 6)}, Börse {de(held, 6)} – Bestand angepasst", "warn", "alert")
                rules = self.ex.rules(sym)
                if held < max(rules.min_amount, 1e-12):
                    t = self.ticks.get(sym)
                    self.pf.drop_position(sym, t.bid if t else pos.entry_price, now, "Bestand auf der Börse fehlt")
                else:
                    share = held / pos.qty
                    pos.cost *= share
                    pos.qty = held
                self._dirty = True

    # ───────────────────────────── bookkeeping ─────────────────────────────
    def _bookkeeping(self, now: float) -> None:
        if self.s.mode == "live" and self.live_ready and now >= self._next_reconcile:
            self._next_reconcile = now + self.s.reconcile_sec
            try:
                self._reconcile_balances(now)
            except ExchangeError as exc:
                self._note_error(exc, "reconcile", now)
        if not self.ticks:
            return
        eq = self.pf.equity(self._marks())
        self.pf.mark(now, eq)

    def _save(self, now: float) -> None:
        if not self._dirty and now - self._last_save < 60:
            return
        try:
            self.store.save(self.pf.to_dict())
            self._dirty = False
            self._last_save = now
            self.save_error = ""
        except Exception as exc:                                  # noqa: BLE001
            self.save_error = f"{type(exc).__name__}: {exc}"
            if now - self._err_log_ts.get("save", 0) > 60:
                self._err_log_ts["save"] = now
                log.error("Speichern fehlgeschlagen: %s", exc)
                self.journal.add("system", f"Speichern fehlgeschlagen: {exc}", "bad", "alert")

    # ───────────────────────────── commands ─────────────────────────────
    def _process_commands(self, now: float) -> None:
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                return
            self._handle(cmd)

    def _handle(self, cmd: Command) -> None:
        now = self.clock()
        try:
            res = getattr(self, f"_cmd_{cmd.name}")(now, **cmd.args)
        except AttributeError:
            res = {"ok": False, "message": f"Unbekannter Befehl: {cmd.name}"}
        except Exception as exc:                                  # noqa: BLE001
            log.exception("Befehl %s fehlgeschlagen", cmd.name)
            res = {"ok": False, "message": f"Fehler: {exc}"}
        cmd.result = res
        self._dirty = True
        cmd.done.set()
        self._publish(now)

    def _cmd_start(self, now: float) -> dict[str, Any]:
        if self.s.mode == "live":
            if self.s.live_locked_reason:
                return {"ok": False, "message": self.s.live_locked_reason}
            if not self.live_ready:
                return {"ok": False, "message": self.live_error or "Live-Handel ist noch nicht bereit (API-Key wird geprüft)"}
        if self.pf.running:
            return {"ok": True, "message": "Der Bot läuft bereits"}
        if any(it["hard"] for it in self._exits.values()):
            return {"ok": False, "message": "Der Not-Aus wird noch ausgeführt – bitte einen Moment warten"}
        was_halted = self.pf.halted
        self.pf.running, self.pf.halted = True, ""
        if was_halted:                                            # explicit restart after Not-Aus / kill-switch: fresh reference points
            eq = self.pf.equity(self._marks())
            self.pf.peak = eq
            self.pf.day_start_equity = eq
        self._day_blocked = self._day_blocked if not was_halted else ""
        self.journal.add("system", "Bot gestartet" + (" (nach Not-Aus)" if was_halted else ""), "ok", "system")
        self._risk_summary(now)
        return {"ok": True, "message": "Bot gestartet"}

    def _cmd_pause(self, now: float) -> dict[str, Any]:
        self.pf.running = False
        self.journal.add("system", "Bot pausiert – offene Positionen bleiben durch ihre Stopps geschützt", "warn", "system")
        return {"ok": True, "message": "Pausiert – Stopps bleiben aktiv"}

    def _cmd_panic(self, now: float) -> dict[str, Any]:
        self._panic("Not-Aus durch Benutzer", now)
        n = len(self.pf.positions)
        return {"ok": True, "message": f"NOT-AUS: {n} Position(en) werden verkauft" if n else "NOT-AUS: Bot gestoppt (keine Positionen offen)"}

    def _cmd_rebase_budget(self, now: float) -> dict[str, Any]:
        """LIVE: set the bot's budget again from LIVE_CAPITAL (and the free balance). Only while nothing is open."""
        if self.s.mode != "live":
            return {"ok": False, "message": "Das gibt es nur im Live-Modus"}
        if self.pf.positions or self.pf.pending:
            return {"ok": False, "message": "Erst alle Positionen schließen (NOT-AUS), dann das Budget neu festlegen"}
        if self.pf.running:
            return {"ok": False, "message": "Bitte den Bot zuerst pausieren"}
        if not self.live_ready:
            return {"ok": False, "message": self.live_error or "Live-Handel ist noch nicht bereit"}
        try:
            free = self.ex.fetch_balances().get(self.s.quote, {}).get("free", 0.0)
        except ExchangeError as exc:
            return {"ok": False, "message": f"Guthaben nicht lesbar: {exc}"}
        cap = min(self.s.live_capital, free)
        if cap < 10:
            return {"ok": False, "message": f"Zu wenig freies {self.s.quote} ({de(free)}) – mindestens 10 nötig"}
        self.pf.start_capital = self.pf.cash = self.pf.peak = self.pf.day_start_equity = cap
        self.pf.history = EquityHistory()
        self.pf.history.add(now, cap, force=True)
        self._day_blocked = ""
        self.journal.add("system", f"Live-Budget neu festgelegt: {eur(cap, self.s.quote)}", "ok", "system")
        return {"ok": True, "message": f"Budget: {eur(cap, self.s.quote)}"}

    def _cmd_reset_paper(self, now: float) -> dict[str, Any]:
        if self.s.mode != "paper":
            return {"ok": False, "message": "Zurücksetzen gibt es nur im Demo-Modus"}
        self.pf = Portfolio(self.s.start_capital, "paper")
        self.pf.created_ts = now
        self.pf.history.add(now, self.pf.cash, force=True)
        self._exits.clear()
        self._candidates.clear()
        self._day_blocked = ""
        self.journal.add("system", f"Demo-Konto zurückgesetzt auf {eur(self.s.start_capital, self.s.quote)}", "info", "system")
        return {"ok": True, "message": "Demo-Konto zurückgesetzt"}

    # ───────────────────────────── snapshot for the dashboard ─────────────────────────────
    def _curve(self, now: float) -> list[list[float]]:
        pts = self.pf.history.points
        if len(pts) <= 1200:
            return [list(p) for p in pts]
        recent = [p for p in pts if p[0] >= now - 86400]
        old = [p for p in pts if p[0] < now - 86400]
        step = max(1, len(old) // 500)
        return [list(p) for p in old[::step]] + [list(p) for p in recent[::max(1, len(recent) // 700)]] + [list(pts[-1])]

    def _market_view(self, sym: str, now: float) -> dict[str, Any]:
        sd, t = self.data[sym], self.ticks.get(sym)
        view: dict[str, Any] = {"symbol": sym, "base": sym.split("/")[0], "price": t.last if t else None, "bid": t.bid if t else None,
                                "ask": t.ask if t else None, "spread_pct": t.spread_pct if t else None,
                                "tick_age": (now - t.ts) if t else None, "has_position": sym in self.pf.positions,
                                "trend": "unknown", "trend_pct": 0.0, "atr_pct": None, "change_pct": None, "rsi": None,
                                "rsi_live": None, "macd_hist": None, "macd_up": None, "tradable": False, "notes": [],
                                "signal": {"action": "WAIT", "score": 0, "summary": "Sammle Kursdaten …", "checks": []},
                                "next_bar_in": None, "rank": None}
        f = sd.feats
        if f is not None and f.n > 2:
            i = f.n - 1
            view.update(rsi=float(f.rsi[i]) if f.rsi[i] == f.rsi[i] else None,
                        macd_hist=float(f.macd_hist[i]) if f.macd_hist[i] == f.macd_hist[i] else None,
                        macd_up=bool(f.macd_hist[i] > f.macd_hist[i - 1]) if f.macd_hist[i] == f.macd_hist[i] else None)
            if sd.live:
                view["rsi_live"] = sd.live["rsi"]
                view["macd_live_up"] = sd.live["up"]
        if sd.scout:
            r = sd.scout
            view.update(trend=r.trend, trend_pct=r.trend_pct, atr_pct=r.atr_pct, change_pct=r.change_pct, tradable=r.tradable,
                        notes=list(r.notes), rank=r.rank)
        if sd.entry:
            e = sd.entry
            view["signal"] = {"action": e.action, "score": e.score, "summary": e.summary,
                              "checks": [{"key": c.key, "label": c.label, "ok": bool(c.ok), "detail": c.detail} for c in e.checks]}
        if sd.closed is not None and len(sd.closed):
            nxt = (sd.closed[-1, 0] / 1000.0) + 2 * self.tf_s
            view["next_bar_in"] = max(0.0, nxt - now)
        return view

    def _publish(self, now: float) -> None:
        marks = self._marks()
        pf = self.pf
        equity = pf.equity(marks)
        pos_view = []
        for sym, p in pf.positions.items():
            mark = marks.get(sym, p.entry_price)
            value = p.qty * mark
            pos_view.append({"symbol": sym, "base": sym.split("/")[0], "qty": p.qty, "entry": p.entry_price, "price": mark, "value": value,
                             "pnl": value * (1 - self.p.fee_rate) - p.cost,
                             "pnl_pct": (value * (1 - self.p.fee_rate) / p.cost - 1.0) * 100.0 if p.cost > 0 else 0.0,
                             "stop": p.stop, "initial_stop": p.initial_stop, "stop_dist_pct": (mark / p.stop - 1.0) * 100.0 if p.stop > 0 else None,
                             "highest": p.highest, "since": p.entry_ts, "risk": p.risk_amount, "exiting": sym in self._exits})
        es = self.ex_status
        last_tick = max((t.ts for t in self.ticks.values()), default=0.0)
        state = "halted" if pf.halted else ("running" if pf.running else "paused")
        snap = {
            "ts": now, "mode": self.s.mode, "state": state, "halt_reason": pf.halted, "liquidating": bool(self._exits) and bool(pf.halted),
            "locked": self.s.live_locked_reason, "live_error": self.live_error if self.s.mode == "live" else "",
            "quote": self.s.quote, "currency": self._cur(),
            "equity": equity, "cash": pf.cash, "start_capital": pf.start_capital, "pnl": equity - pf.start_capital,
            "pnl_pct": (equity / pf.start_capital - 1.0) * 100.0 if pf.start_capital > 0 else 0.0,
            "day_pnl": equity - pf.day_start_equity, "day_pnl_pct": (equity / pf.day_start_equity - 1.0) * 100.0 if pf.day_start_equity > 0 else 0.0,
            "peak": pf.peak, "drawdown_pct": (equity / pf.peak - 1.0) * 100.0 if pf.peak > 0 else 0.0,
            "exposure": pf.exposure(marks), "exposure_pct": pf.exposure(marks) / equity * 100.0 if equity > 0 else 0.0,
            "day_blocked": self._day_blocked,
            "agents": [a.snapshot() for a in self.agents],
            "markets": [self._market_view(s, now) for s in self.symbols],
            "positions": pos_view,
            "trades": [dataclasses.asdict(t) for t in list(pf.trades)[-40:]][::-1],
            "stats": pf.stats(),
            "curve": self._curve(now),
            "journal": self.journal.recent(40),
            "pending": list(pf.pending),
            "system": {"exchange": self.s.exchange_name, "ok": es["ok"], "error": es["error"], "kind": es["kind"], "hint": es["hint"],
                       "latency_ms": es["latency_ms"], "last_tick": last_tick, "tick_age": (now - last_tick) if last_tick else None,
                       "markets_ready": self.markets_ready, "save_error": self.save_error, "internal_error": self.internal_error,
                       "store": self.store.describe(), "uptime": now - self.started_ts, "loop_ts": self.loop_ts, "alive": self.is_alive(),
                       "withdraw_enabled": self.cred_info.get("withdraw_enabled")},
            "config": {"timeframe": self.p.timeframe, "strategy": self.p.mode, "risk_pct": self.p.risk_per_trade * 100.0,
                       "stop_mult": self.p.stop_atr_mult, "fee_pct": self.p.fee_rate * 100.0, "max_positions": self.p.max_open_positions,
                       "daily_limit": self.s.daily_loss_limit_pct, "kill_dd": self.s.kill_dd_pct, "symbols": self.symbols},
        }
        self._snapshot = snap
