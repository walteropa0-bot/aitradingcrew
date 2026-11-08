"""The Engine: orchestrates the crew.

Loops (all supervised, one failing iteration never kills the others):
    scan        every SCAN_INTERVAL_SEC   Scout finds + vets fresh pools -> watchlist
    watch       every WATCH_REFRESH_SEC   refresh watchlist data, committee decides entries
    positions   every POSITION_TICK_SEC   live marks, trailing stops, exits (ALWAYS on)
    housekeeping every 5 s                equity curve, circuit breakers, balance sync, persistence

"Hunting" (new entries) can be paused; open positions stay protected by the
position loop regardless. NOT-AUS (panic) sells everything the bot manages.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import config

from .agents.risk import RiskManager
from .agents.scout import Scout
from .agents.sentiment import SentimentAnalyst
from .agents.technical import TechAnalyst
from .events import EventBus
from .models import ContractReport, ExitOrder, Pair, Position, SentimentReport, TechReport
from .trading.executor import NO_BALANCE, BuyRequest, Executor, ExecutorError, SellRequest
from .trading.portfolio import Portfolio
from .trading.store import StateStore
from .util import short_addr

log = logging.getLogger("crew.engine")


@dataclass
class WatchItem:
    mint: str
    symbol: str
    name: str
    added_at: float
    contract: ContractReport
    decimals: int
    image_url: str = ""
    sent: Optional[SentimentReport] = None
    tech: Optional[TechReport] = None
    score: float = 0.0
    next_try: float = 0.0
    last_seen: float = 0.0
    signal_price: float = 0.0


class Engine:
    def __init__(self, *, market, executor: Executor, store: StateStore, portfolio: Portfolio,
                 bus: Optional[EventBus] = None, step_delay: Optional[float] = None) -> None:
        self.market = market
        self.executor = executor
        self.store = store
        self.pf = portfolio
        self.bus = bus or EventBus()
        delay = config.UI_STEP_DELAY_SEC if step_delay is None else step_delay
        self.scout = Scout(self.bus, market, delay)
        self.sentiment = SentimentAnalyst(self.bus, delay)
        self.tech = TechAnalyst(self.bus, delay)
        self.risk = RiskManager(self.bus, delay)
        self.agents = [self.scout, self.sentiment, self.tech, self.risk]
        self.mode = "paper" if executor.simulated else "live"
        self.watch: dict[str, WatchItem] = {}
        self.pairs_cache: dict[str, Pair] = {}
        self.sol_usd = 0.0
        self.hunting = False
        self.panic_mode = False
        self.halt_reason = ""
        self.halt_until = 0.0
        self.hard_halt = False
        self.ready = True
        self.ready_error = ""
        self.last_error = ""
        self.committee: dict[str, Any] = {}
        self.foreign_tokens: list[dict[str, Any]] = []
        self.started_at = time.time()
        self.scan_count = 0
        self._tasks: set[asyncio.Task] = set()
        self._entry_inflight: dict[str, float] = {}
        self._committee_busy = False
        self._stop = asyncio.Event()
        self._dirty = True
        self._last_persist = 0.0
        self._last_sample = 0.0
        self._last_sync = 0.0
        self._stuck_alerted: set[str] = set()
        self._price_missing_since: dict[str, float] = {}
        self._loop_tasks: list[asyncio.Task] = []

    # ═══════════════════════════ helpers ═════════════════════════════════
    def now(self) -> float:
        return self.market.now()

    def _spawn(self, coro: Awaitable[Any], name: str = "task") -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                log.error("Task %s crashed: %r", name, t.exception(), exc_info=t.exception())

        task.add_done_callback(_done)

    def emit(self, text: str, level: str = "info", kind: str = "system", agent: str = "system", **kw: Any) -> None:
        self.bus.emit(agent, text, level=level, kind=kind, **kw)

    def capital(self, equity: Optional[float] = None) -> float:
        """Capital the risk model may work with (live: capped, paper: whole account)."""
        eq = self.pf.equity_sol(self.sol_usd) if equity is None else equity
        if self.mode == "paper":
            return eq
        return max(0.0, min(eq, config.LIVE_TRADING_CAPITAL_SOL + max(0.0, self.pf.realized_pnl_sol)))

    @property
    def halted(self) -> bool:
        return bool(self.halt_reason) and self.now() < self.halt_until

    @property
    def state(self) -> str:
        if self.panic_mode:
            return "panic"
        if self.hunting:
            return "halted" if self.halted else "running"
        return "stopped"

    # ═══════════════════════════ lifecycle ═══════════════════════════════
    async def startup(self) -> None:
        t = self.now()
        self.sol_usd = await self._safe(self.market.sol_usd(), 0.0)
        if self.mode == "live":
            try:
                notes = await self.executor.prepare()
                for n in notes:
                    self.emit(n, "ok")
                await self._sync_cash()
                await self._reconcile_live()
                self.ready, self.ready_error = True, ""
            except ExecutorError as exc:
                self.ready, self.ready_error = False, str(exc)
                self.emit(f"Live-Start blockiert: {exc}", "bad", kind="alert")
            except Exception as exc:       # never crash the app on a bad config
                self.ready, self.ready_error = False, f"Start-Check fehlgeschlagen: {exc}"
                log.exception("live startup failed")
                self.emit(self.ready_error, "bad", kind="alert")
        self.pf.sample_equity(t, self.pf.equity_sol(self.sol_usd))
        open_n = len(self.pf.open_positions())
        if open_n:
            self.emit(f"{open_n} offene Position(en) wiederhergestellt – Trailing-Stops sind aktiv", "ok")
        if self.pf.desired_running and config.AUTO_RESUME and self.ready:
            self.hunting = True
            self.emit("System nach Neustart automatisch fortgesetzt", "ok")
        self._set_idle_statuses()

    async def run(self) -> None:
        try:
            await self.startup()
        except Exception as exc:               # a failed start-up check must never stop the loops
            self.last_error = f"startup: {exc}"
            log.exception("startup failed - continuing with the loops")
        specs = [("scan", self._scan_tick, config.SCAN_INTERVAL_SEC), ("watch", self._watch_tick, config.WATCH_REFRESH_SEC),
                 ("positions", self._position_tick, config.POSITION_TICK_SEC), ("housekeeping", self._housekeeping_tick, 5.0)]
        self._loop_tasks = [asyncio.ensure_future(self._supervise(n, f, i)) for n, f, i in specs]
        try:
            await self._stop.wait()
        finally:
            for t in self._loop_tasks:
                t.cancel()
            await asyncio.gather(*self._loop_tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        self._stop.set()
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)
        await self._persist(force=True)

    async def _supervise(self, name: str, fn: Callable[[], Awaitable[None]], interval: float) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                await fn()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{name}: {exc}"
                log.exception("loop %s failed", name)
                await asyncio.sleep(min(30.0, backoff))
                backoff *= 2
            elapsed = time.monotonic() - t0
            await asyncio.sleep(max(0.2, interval - elapsed))

    @staticmethod
    async def _safe(coro: Awaitable[Any], default: Any) -> Any:
        try:
            return await coro
        except Exception as exc:
            log.debug("safe call failed: %s", exc)
            return default

    # ═══════════════════════════ controls ════════════════════════════════
    async def start(self) -> dict[str, Any]:
        if self.panic_mode:
            return {"ok": False, "message": "NOT-AUS läuft noch – bitte warten, bis alles verkauft ist."}
        if self.mode == "live":
            await self.startup_retry()              # re-validate RPC, balance and Jupiter on EVERY start
            if not self.ready:
                return {"ok": False, "message": self.ready_error or "Live-Modus nicht bereit."}
        if self.hard_halt:
            self.hard_halt = False
            self.pf.peak_equity = self.pf.equity_sol(self.sol_usd)
        self.halt_reason, self.halt_until = "", 0.0
        self.hunting = True
        self.pf.desired_running = True
        self._dirty = True
        self.emit("System gestartet – Agenten gehen an die Arbeit", "ok")
        self._set_idle_statuses()
        await self._persist(force=True)
        return {"ok": True, "message": "System gestartet"}

    async def startup_retry(self) -> None:
        try:
            notes = await self.executor.prepare()
            await self._sync_cash()
            await self._reconcile_live()
            self.ready, self.ready_error = True, ""
            for n in notes:
                self.emit(n, "ok")
        except ExecutorError as exc:
            self.ready, self.ready_error = False, str(exc)
        except Exception as exc:
            self.ready, self.ready_error = False, f"Start-Check fehlgeschlagen: {exc}"

    async def pause(self) -> dict[str, Any]:
        self.hunting = False
        self.pf.desired_running = False
        self._dirty = True
        n = len(self.pf.open_positions())
        self.emit("Jagd pausiert – offene Positionen bleiben durch Trailing-Stops geschützt" if n else "System pausiert", "warn")
        self._set_idle_statuses()
        await self._persist(force=True)
        return {"ok": True, "message": "Pausiert", "open_positions": n}

    async def panic(self) -> dict[str, Any]:
        """NOT-AUS: stop hunting and liquidate every managed position at market."""
        self.hunting = False
        self.pf.desired_running = False
        self.panic_mode = True
        n = len(self.pf.open_positions())
        self.emit(f"NOT-AUS ausgelöst – verkaufe {n} Position(en) sofort", "bad", kind="alert")
        self.risk.set("blocked", f"NOT-AUS: liquidiere {n} Position(en) …", level="bad")
        for pos in self.pf.open_positions():
            if not pos.exiting:
                self._spawn(self._do_exit(pos.id, ExitOrder(pos.id, 1.0, "NOT-AUS", panic=True)), "panic-exit")
        if n == 0:
            self.panic_mode = False
            self._set_idle_statuses()
        self._dirty = True
        await self._persist(force=True)
        return {"ok": True, "message": f"NOT-AUS: {n} Position(en) werden verkauft", "open_positions": n}

    async def reset_paper(self) -> dict[str, Any]:
        if self.mode != "paper":
            return {"ok": False, "message": "Zurücksetzen gibt es nur im Demo-Modus."}
        if self.pf.open_positions() or self._entry_inflight:
            return {"ok": False, "message": "Bitte zuerst alle Positionen schließen (NOT-AUS)."}
        self.hunting = False
        start = config.PAPER_START_BALANCE_SOL
        fresh = Portfolio("paper", start, self.now())
        self.pf.__dict__.update(fresh.__dict__)
        self.pf.sample_equity(self.now(), start)
        self.watch.clear()
        self.halt_reason, self.halt_until, self.hard_halt = "", 0.0, False
        self.committee = {}
        self.emit(f"Demo-Konto zurückgesetzt: {start:g} SOL Spielgeld", "warn")
        self._set_idle_statuses()
        await self._persist(force=True)
        return {"ok": True, "message": "Demo-Konto zurückgesetzt"}

    def _set_idle_statuses(self) -> None:
        if not self.hunting:
            msg = "Pausiert – Positionen bleiben geschützt" if self.pf.open_positions() else "Bereit – warte auf Start"
            for a in (self.scout, self.sentiment, self.tech):
                a.set("idle", msg)
            self.risk.set("confirmed" if self.pf.open_positions() else "idle",
                          f"Überwache {len(self.pf.open_positions())} Position(en)" if self.pf.open_positions() else msg)
        else:
            self.scout.set("waiting", "Starte ersten Scan …")
            self.sentiment.set("waiting", "Warte auf Kandidaten vom Scout")
            self.tech.set("waiting", "Warte auf Kandidaten vom Scout")
            self.risk.set("waiting", f"Bereit · Kapital {self.capital():.2f} SOL · max. {config.MAX_POSITION_SOL:g} SOL/Trade")

    # ═══════════════════════════ scan loop ═══════════════════════════════
    async def _scan_tick(self) -> None:
        if not self.hunting or self.panic_mode or self.hard_halt:
            return
        t = self.now()
        exclude = set(self.watch) | {p.mint for p in self.pf.open_positions()} | set(self._entry_inflight)
        res = await self.scout.scan(exclude=exclude, is_blacklisted=lambda m: self.pf.is_blacklisted(m, t))
        self.scan_count += 1
        for pair, rep in res.vetted:
            if pair.mint in self.watch:
                continue
            self.watch[pair.mint] = WatchItem(
                mint=pair.mint, symbol=pair.symbol or pair.mint[:5], name=pair.name, added_at=t, contract=rep,
                decimals=rep.decimals if rep.decimals is not None else 6, image_url=pair.image_url,
                last_seen=t, signal_price=pair.price_sol,
            )
            self.pairs_cache[pair.mint] = pair
            self.tech.update(pair)
            self.emit(f"${pair.symbol} → Watchlist (Sicherheits-Score {rep.safety_score})", "ok")
        self._trim_watch()

    def _trim_watch(self) -> None:
        if len(self.watch) <= config.WATCHLIST_MAX:
            return
        held = {p.mint for p in self.pf.open_positions()}
        victims = sorted((w for w in self.watch.values() if w.mint not in held), key=lambda w: w.score)
        for w in victims[: len(self.watch) - config.WATCHLIST_MAX]:
            self.watch.pop(w.mint, None)
            self.tech.forget(w.mint)

    # ═══════════════════════════ watch loop ══════════════════════════════
    async def _watch_tick(self) -> None:
        held = [p.mint for p in self.pf.open_positions()]
        mints = list(dict.fromkeys(list(self.watch) + held))
        if not mints:
            return
        t = self.now()
        pairs = await self.market.pairs(mints)
        sol = await self._safe(self.market.sol_usd(), self.sol_usd)
        if sol:
            self.sol_usd = sol
        for m, p in pairs.items():
            self.pairs_cache[m] = p
            self.tech.update(p)
            if m in self.watch:
                self.watch[m].last_seen = t
        # expire stale / dead watchlist entries
        for w in list(self.watch.values()):
            if w.mint in held or w.mint in self._entry_inflight:
                continue
            p = pairs.get(w.mint)
            ttl = t - w.added_at > config.WATCH_TTL_MIN * 60
            gone = p is None and t - w.last_seen > 300
            dead = p is not None and p.liquidity_usd < config.MIN_LIQUIDITY_USD * 0.5
            if ttl or gone or dead:
                self.watch.pop(w.mint, None)
                self.tech.forget(w.mint)
                self.scout.forget(w.mint)
        if self.hunting and not self.panic_mode and not self.hard_halt:
            await self._evaluate_entries()

    async def _evaluate_entries(self) -> None:
        t = self.now()
        if self._committee_busy:
            return
        if not self.watch:
            self.sentiment.set("waiting", "Watchlist leer – Scout sucht noch")
            self.tech.set("waiting", "Watchlist leer – Scout sucht noch")
            return
        analyzed: list[WatchItem] = []
        for w in self.watch.values():
            p = self.pairs_cache.get(w.mint)
            if p is None or t - p.ts > 240 or p.price_sol <= 0:
                continue
            sent = self.sentiment.analyze(p)
            tech = self.tech.analyze(p)
            w.sent, w.tech = sent, tech
            w.signal_price = p.price_sol
            w.score = (config.WEIGHT_SAFETY * w.contract.safety_score + config.WEIGHT_SENTIMENT * sent.score
                       + config.WEIGHT_TECH * tech.score)
            analyzed.append(w)
        if not analyzed:
            return
        self.sentiment.stats["checked"] += len(analyzed)
        self.tech.stats["checked"] += len(analyzed)
        best_s = max(analyzed, key=lambda w: w.sent.score if w.sent else 0)
        best_t = max(analyzed, key=lambda w: w.tech.score if w.tech else 0)
        self.sentiment.set("confirmed" if best_s.sent.score >= config.SENTIMENT_MIN_SCORE else "waiting",
                           f"{len(analyzed)} bewertet · Top ${best_s.symbol}: {best_s.sent.summary}", target=best_s.symbol)
        self.tech.set("confirmed" if best_t.tech.signal == "BUY" else "waiting",
                      f"{len(analyzed)} Charts · Top ${best_t.symbol}: {best_t.tech.summary}", target=best_t.symbol)

        ready = [w for w in analyzed
                 if w.tech.signal == "BUY" and w.sent.score >= config.SENTIMENT_MIN_SCORE
                 and w.score >= config.ENTRY_SCORE_MIN and w.next_try <= t and w.mint not in self._entry_inflight
                 and not any(p.mint == w.mint for p in self.pf.open_positions())]
        if not ready:
            best = max(analyzed, key=lambda w: w.score)
            self.risk.set("waiting", f"Kein Einstieg freigegeben · bester Kandidat ${best.symbol} Score {best.score:.0f} "
                                     f"(Ziel ≥ {config.ENTRY_SCORE_MIN}) · {len(self.pf.open_positions())} Position(en) offen")
            return
        if self.halted:
            self.risk.set("blocked", f"Einstiege pausiert: {self.halt_reason}")
            return
        await self._committee(max(ready, key=lambda w: w.score))

    async def _committee(self, w: WatchItem) -> None:
        """Paced committee session for the best candidate: all four agents vote."""
        self._committee_busy = True
        try:
            sym, t = w.symbol, self.now()
            pair = self.pairs_cache[w.mint]
            votes = {"scout": {"ok": True, "label": f"Sicher {w.contract.safety_score}"}}
            self.sentiment.set("analyzing", f"${sym}: prüfe Hype & Käufer-Wallets …", target=sym)
            await self.sentiment.beat()
            self.sentiment.set("confirmed", f"${sym}: {w.sent.summary}", target=sym)
            self.sentiment.stats["passed"] += 1
            votes["sentiment"] = {"ok": True, "label": f"Hype {w.sent.score:.0f}"}
            self.tech.set("analyzing", f"${sym}: berechne Momentum & Einstiegszeitpunkt …", target=sym)
            await self.tech.beat()
            self.tech.set("confirmed", f"${sym}: BUY-Signal · {w.tech.summary}", target=sym)
            self.tech.stats["passed"] += 1
            votes["tech"] = {"ok": True, "label": f"Tech {w.tech.score:.0f}"}

            self.risk.set("analyzing", f"${sym}: prüfe Slippage, Echtzeit-Preis & Positionsgröße …", target=sym)
            await self.risk.beat()
            live_price = None
            marks = await self._safe(self.market.marks([w.mint]), {})
            if marks.get(w.mint):
                live_price = marks[w.mint]
            equity = self.pf.equity_sol(self.sol_usd)
            reserved = sum(self._entry_inflight.values())
            decision = self.risk.approve_entry(
                pf=self.pf, capital=self.capital(equity), cash=self.pf.cash_sol - reserved, pair=pair, sol_usd=self.sol_usd,
                live_price=live_price, signal_price=pair.price_sol, ts=t, pending=len(self._entry_inflight), reserved=reserved,
            )
            self.risk.stats["checked"] += 1
            record = {"ts": t, "symbol": sym, "mint": w.mint, "score": round(w.score, 1), "votes": votes, "image": w.image_url}
            if not decision.approved:
                self.risk.stats["blocked"] += 1
                votes["risk"] = {"ok": False, "label": "Blockiert"}
                self.risk.set("blocked", f"${sym}: {decision.reason}", target=sym)
                record.update(decision="ABGELEHNT", detail=decision.reason)
                self.committee = record
                w.next_try = t + 90
                self.emit(f"Komitee: ${sym} abgelehnt – {decision.reason}", "warn", agent="risk")
                await self.risk.beat()
                return
            self.risk.stats["passed"] += 1
            votes["risk"] = {"ok": True, "label": f"{decision.sol_amount:.2f} SOL"}
            self.risk.set("confirmed", f"${sym}: freigegeben · {decision.reason}", target=sym)
            record.update(decision="KAUF", detail=decision.reason)
            self.committee = record
            ref = live_price or pair.price_sol
            self._entry_inflight[w.mint] = decision.sol_amount
            self._spawn(self._do_entry(w, pair, decision, ref), "entry")
        finally:
            self._committee_busy = False

    # ═══════════════════════════ entries / exits ═════════════════════════
    async def _do_entry(self, w: WatchItem, pair: Pair, decision, ref_price: float) -> None:
        sym = w.symbol
        try:
            req = BuyRequest(mint=w.mint, symbol=sym, decimals=w.decimals, sol_amount=decision.sol_amount,
                             slippage_bps=decision.slippage_bps, ref_price_sol=ref_price, liquidity_usd=pair.liquidity_usd,
                             sol_usd=self.sol_usd, max_impact_pct=config.MAX_PRICE_IMPACT_PCT)
            self.risk.set("analyzing", f"${sym}: sende Kauf ({decision.sol_amount:.3f} SOL) …", target=sym)
            fill = await self.executor.buy(req)
            t = self.now()
            if not fill.ok:
                w.next_try = t + 180
                self.risk.set("blocked", f"${sym}: Kauf nicht ausgeführt – {fill.error}", target=sym)
                self.emit(f"Kauf ${sym} fehlgeschlagen: {fill.error}", "warn", kind="alert", agent="risk")
                return
            stop = self.risk.initial_stop(fill.price_sol if fill.price_sol > 0 else ref_price)
            scores = {"safety": w.contract.safety_score, "sentiment": round(w.sent.score if w.sent else 0, 1),
                      "tech": round(w.tech.score if w.tech else 0, 1)}
            pos = self.pf.record_buy(fill, pair, decimals=w.decimals, stop_price=stop, scores=scores, ts=t)
            self.pf.mark(pos, pos.entry_price_sol, pair.liquidity_usd)
            self._dirty = True
            tag = "SIMULIERT" if fill.simulated else "LIVE"
            self.emit(f"KAUF ${sym}: {fill.sol_amount:.3f} SOL @ {fill.price_sol:.3e} SOL · Stop {stop:.3e} [{tag}]", "ok", kind="trade", agent="risk",
                      data={"side": "BUY", "mint": w.mint})
            self.risk.set("confirmed", f"${sym}: gekauft ✓ {fill.sol_amount:.3f} SOL · Trailing-Stop aktiv ab {stop:.3e}", target=sym)
            await self._sync_cash()
            if self.panic_mode and not pos.exiting:
                self._spawn(self._do_exit(pos.id, ExitOrder(pos.id, 1.0, "NOT-AUS", panic=True)), "panic-exit")
        finally:
            self._entry_inflight.pop(w.mint, None)

    async def _do_exit(self, pos_id: str, order: ExitOrder) -> None:
        pos = self.pf.positions.get(pos_id)
        if pos is None or pos.exiting or pos.status != "open":
            return
        pos.exiting = True
        pos.last_exit_try = self.now()
        sym = pos.symbol
        try:
            full = order.fraction >= 0.999
            qty = pos.qty if full else pos.qty * order.fraction
            pair = self.pairs_cache.get(pos.mint)
            slip = order.slippage_bps or self.risk.exit_slippage_bps(order, pos.exit_attempts)
            req = SellRequest(position=pos, qty=qty, full=full, slippage_bps=slip, panic=order.panic, reason=order.reason,
                              ref_price_sol=pos.last_price_sol, liquidity_usd=pair.liquidity_usd if pair else pos.last_liquidity_usd,
                              sol_usd=self.sol_usd)
            self.risk.set("analyzing", f"${sym}: {order.reason} → verkaufe {order.fraction * 100:.0f}% …", target=sym)
            fill = await self.executor.sell(req)
            t = self.now()
            if not fill.ok:
                if fill.error == NO_BALANCE:
                    self.emit(f"${sym}: Token nicht mehr in der Wallet – Position als extern geschlossen entfernt", "warn", kind="alert")
                    self.pf.positions.pop(pos.id, None)
                    self._dirty = True
                    return
                pos.exit_attempts += 1
                self.emit(f"Verkauf ${sym} fehlgeschlagen ({fill.error}) – Versuch {pos.exit_attempts}", "warn", kind="alert", agent="risk")
                if pos.exit_attempts >= 5 and pos.id not in self._stuck_alerted:
                    self._stuck_alerted.add(pos.id)
                    self.emit(f"ACHTUNG: ${sym} lässt sich nicht verkaufen (kaum Liquidität?). Der Bot versucht es weiter.", "bad", kind="alert")
                return
            pos.exit_attempts = 0
            trade, closed, total = self.pf.record_sell(pos, fill, order.reason, t)
            if order.kind == "take_profit" and order.stage >= 0 and order.stage not in pos.tp_done:
                pos.tp_done.append(order.stage)
            self._dirty = True
            tag = "SIMULIERT" if fill.simulated else "LIVE"
            lvl = "ok" if trade.pnl_sol >= 0 else "bad"
            self.emit(f"VERKAUF ${sym} ({order.reason}): {fill.sol_amount:.3f} SOL · {trade.pnl_pct:+.1f}% [{tag}]", lvl,
                      kind="trade", agent="risk", data={"side": "SELL", "mint": pos.mint})
            if closed:
                self.risk.set("confirmed" if total >= 0 else "blocked",
                              f"${sym}: Position geschlossen · {total:+.4f} SOL ({order.reason})", target=sym)
                await self._after_close(pos, total, order, t)
            else:
                self.risk.set("confirmed", f"${sym}: Teilverkauf ✓ {order.reason} · Rest läuft mit Trailing-Stop", target=sym)
            await self._sync_cash()
        finally:
            pos.exiting = False
            if self.panic_mode and not self.pf.open_positions():
                self.panic_mode = False
                self.emit("NOT-AUS abgeschlossen – alle Positionen verkauft", "ok", kind="alert")
                self._set_idle_statuses()

    async def _after_close(self, pos: Position, total: float, order: ExitOrder, t: float) -> None:
        self._stuck_alerted.discard(pos.id)
        self.tech.forget(pos.mint)
        self.watch.pop(pos.mint, None)
        cool = config.REENTRY_COOLDOWN_MIN * 60.0
        self.pf.blacklist_mint(pos.mint, t + (cool if total < 0 or order.panic else cool / 3))
        if total > 0 and config.PROFIT_CURRENCY.upper() == "USDC" and total >= config.MIN_PROFIT_SWEEP_SOL:
            fill = await self._safe(self.executor.sweep_usdc(total), None)
            if fill is not None and fill.ok:
                self.pf.cash_sol -= fill.sol_amount
                self.pf.secured_usdc += fill.token_amount
                self.emit(f"Gewinn gesichert: {fill.sol_amount:.3f} SOL → {fill.token_amount:.2f} USDC in deiner Wallet", "ok", kind="trade")

    # ═══════════════════════════ position loop ═══════════════════════════
    async def _position_tick(self) -> None:
        positions = [p for p in self.pf.open_positions()]
        if not positions:
            return
        t = self.now()
        marks = await self._safe(self.market.marks([p.mint for p in positions]), {})
        for pos in positions:
            if pos.status != "open":
                continue
            pair = self.pairs_cache.get(pos.mint)
            price = marks.get(pos.mint) or (pair.price_sol if pair and pair.price_sol > 0 else 0.0)
            if price <= 0:
                first = self._price_missing_since.setdefault(pos.id, t)
                if t - first > 90 and pos.id not in self._stuck_alerted:
                    self._stuck_alerted.add(pos.id)
                    self.emit(f"${pos.symbol}: seit über 90 s kein Kurs verfügbar – bitte Position prüfen", "warn", kind="alert")
                continue
            self._price_missing_since.pop(pos.id, None)
            prev = pos.last_price_sol
            self.pf.mark(pos, price, pair.liquidity_usd if pair else 0.0)
            self.risk.update_stop(pos)
            if pos.exiting:
                continue
            if self.panic_mode:
                if t - pos.last_exit_try >= config.EXIT_RETRY_AFTER_SEC:
                    self._spawn(self._do_exit(pos.id, ExitOrder(pos.id, 1.0, "NOT-AUS", panic=True)), "panic-exit")
                continue
            if t - pos.last_exit_try < config.EXIT_RETRY_AFTER_SEC:
                continue
            order = self.risk.evaluate_position(pos, price, pos.last_liquidity_usd, prev, t)
            if order is not None:
                self._spawn(self._do_exit(pos.id, order), "exit")
        open_now = [p for p in self.pf.open_positions()]
        if open_now and not self._committee_busy and not any(p.exiting for p in open_now):
            tight = min(((p.last_price_sol / p.stop_price_sol - 1) * 100 for p in open_now if p.stop_price_sol > 0), default=0)
            self.risk.set("confirmed", f"Überwache {len(open_now)} Position(en) · engster Stop-Abstand {tight:.1f}% · "
                                       f"Trailing-Stops aktiv", progress=None)

    # ═══════════════════════════ housekeeping ════════════════════════════
    async def _housekeeping_tick(self) -> None:
        t = self.now()
        sol = await self._safe(self.market.sol_usd(), self.sol_usd)
        if sol:
            self.sol_usd = sol
        if self.mode == "live" and t - self._last_sync >= 20:
            await self._sync_cash()
        equity = self.pf.equity_sol(self.sol_usd)
        if self.pf.roll_day(t, equity):
            self.emit("Neuer UTC-Tag – Tageslimit zurückgesetzt", "info")
            if self.halt_reason and not self.hard_halt:
                self.halt_reason, self.halt_until = "", 0.0
        if t - self._last_sample >= config.EQUITY_SAMPLE_SEC:
            self.pf.sample_equity(t, equity)
            self._last_sample = t
            self._dirty = True
        self._check_halts(equity, t)
        if self.hunting and self.market.is_simulated is False:
            last_ok = getattr(self.market, "last_data_ok", 0.0)
            if last_ok and t - last_ok > 180 and not self.halt_reason:
                self.halt_reason, self.halt_until = "Marktdaten seit 3 Min nicht erreichbar – Einstiege pausiert", t + 60
                self.emit(self.halt_reason, "warn", kind="alert")
        await self._persist()

    def _check_halts(self, equity: float, t: float) -> None:
        if self.halt_reason and not self.hard_halt and t >= self.halt_until:
            self.emit("Pause beendet – Einstiege wieder erlaubt", "ok")
            self.halt_reason, self.halt_until = "", 0.0
        if self.hard_halt or self.halted:
            return
        h = self.risk.check_halts(self.pf, equity, t)
        if h is None:
            return
        if h.kind == "streak":
            self.pf.consecutive_losses = 0
        self.halt_reason, self.halt_until, self.hard_halt = h.reason, h.until, h.hard
        self.emit(h.reason, "bad" if h.hard else "warn", kind="alert", agent="risk")
        self.risk.set("blocked", h.reason, level="bad" if h.hard else "warn")
        if h.hard:
            self.hunting = False
            self.pf.desired_running = False
        self._dirty = True

    async def _sync_cash(self) -> None:
        if self.executor.simulated:
            return
        bal = await self._safe(self.executor.cash_sol(), None)
        self._last_sync = time.time()
        if bal is not None:
            self.pf.cash_sol = bal
            if self.pf.is_fresh and bal > 0:                  # first sync of a new live account: set the baselines
                self.pf.rebase(bal + self.pf.positions_value(), self.now())
                self._dirty = True

    async def _persist(self, force: bool = False) -> None:
        t = time.time()
        if not force and (not self._dirty or t - self._last_persist < config.PERSIST_INTERVAL_SEC):
            return
        try:
            state = self.pf.to_dict()
            await asyncio.to_thread(self.store.save, state)
            self._last_persist = t
            self._dirty = False
        except Exception as exc:
            self.last_error = f"Speichern fehlgeschlagen: {exc}"
            log.error("persist failed: %s", exc)

    # ═══════════════════════════ live reconcile ══════════════════════════
    async def _reconcile_live(self) -> None:
        """Compare the ledger with the wallet. The chain is the source of truth."""
        rpc = getattr(self.executor, "rpc", None)
        addr = self.executor.wallet_address
        if rpc is None or not addr:
            return
        balances = await rpc.get_token_balances(addr)
        for pos in list(self.pf.open_positions()):
            bal = balances.get(pos.mint)
            if bal is None or bal.ui <= 0:
                self.emit(f"${pos.symbol}: nicht mehr in der Wallet (extern verkauft?) – aus der Verwaltung entfernt", "warn", kind="alert")
                self.pf.positions.pop(pos.id, None)
            elif pos.qty > 0 and abs(bal.ui - pos.qty) / pos.qty > 0.02:
                self.emit(f"${pos.symbol}: Menge laut Wallet {bal.ui:g} (Ledger {pos.qty:g}) – übernommen", "warn")
                pos.qty = bal.ui
        managed = {p.mint for p in self.pf.open_positions()}
        skip = {config.WSOL_MINT, config.USDC_MINT, config.USDT_MINT}
        foreign = [(m, b) for m, b in balances.items() if m not in managed and m not in skip and b.raw > 0]
        marks = await self._safe(self.market.marks([m for m, _ in foreign][:50]), {}) if foreign else {}
        sol = self.sol_usd
        self.foreign_tokens = []
        for m, b in foreign:
            price = marks.get(m, 0.0)
            val = b.ui * price
            if price <= 0 or val * sol < 1.0:
                continue
            if config.ADOPT_UNKNOWN_TOKENS:
                pos = Position(id=f"adopt_{m[:8]}", mint=m, symbol=m[:5], decimals=b.decimals, opened_at=self.now(),
                               entry_price_sol=price, qty=b.ui, cost_sol=val, initial_cost_sol=val, peak_price_sol=price,
                               stop_price_sol=self.risk.initial_stop(price), last_price_sol=price, recovered=True, simulated=False)
                self.pf.positions[pos.id] = pos
                self.emit(f"Fremden Token {m[:6]}… übernommen (Wert {val:.3f} SOL) – Trailing-Stop aktiv", "warn")
            else:
                self.foreign_tokens.append({"mint": m, "amount": b.ui, "value_sol": val})
        if self.foreign_tokens:
            self.emit(f"{len(self.foreign_tokens)} fremde Token in der Wallet werden NICHT angefasst", "info")

    # ═══════════════════════════ views for the UI ════════════════════════
    def _position_view(self, p: Position, t: float) -> dict[str, Any]:
        stop, peak, price = p.stop_price_sol, p.peak_price_sol, p.last_price_sol
        span = peak - stop
        gauge = 0.0 if span <= 0 else max(0.0, min(1.0, (price - stop) / span))
        return {
            "id": p.id, "mint": p.mint, "symbol": p.symbol, "name": p.name, "image": p.image_url,
            "qty": p.qty, "entry": p.entry_price_sol, "price": price, "peak": peak, "stop": stop,
            "gain_pct": p.gain_pct(), "value_sol": p.qty * price, "cost_sol": p.cost_sol,
            "pnl_sol": p.qty * price - p.cost_sol + p.realized_sol - (p.initial_cost_sol - p.cost_sol),
            "stop_dist_pct": (price / stop - 1) * 100 if stop > 0 else 0.0, "gauge": gauge,
            "trailing": self.risk.is_trailing(p), "age_s": max(0, t - p.opened_at), "tp_done": list(p.tp_done),
            "simulated": p.simulated, "exiting": p.exiting, "recovered": p.recovered,
            "solscan": f"{config.SOLSCAN_BASE}/token/{p.mint}",
        }

    def snapshot(self, since: int = 0) -> dict[str, Any]:
        t = self.now()
        pf = self.pf
        sol = self.sol_usd
        equity = pf.equity_sol(sol)
        invested = pf.positions_value()
        pnl = equity - pf.initial_equity_sol
        day_pnl = equity - pf.day_start_equity
        base = max(1e-9, pf.initial_equity_sol)
        day_base = max(1e-9, pf.day_start_equity)
        open_positions = pf.open_positions()
        trades = []
        pos_by_id = {p.id: p for p in open_positions}
        sell_pnl: dict[str, float] = {}
        for tr in pf.trades:
            if tr.side == "SELL":
                sell_pnl[tr.position_id] = sell_pnl.get(tr.position_id, 0.0) + tr.pnl_sol
        for tr in reversed(pf.trades[-60:]):
            live = pos_by_id.get(tr.position_id)
            cost = tr.sol_amount + tr.fee_sol
            result_pct = None
            if tr.side == "BUY" and live is None and tr.position_id in sell_pnl and cost > 0:
                result_pct = sell_pnl[tr.position_id] / cost * 100.0
            item = {
                "id": tr.id, "ts": tr.ts, "side": tr.side, "symbol": tr.symbol, "mint": tr.mint, "image": tr.image_url,
                "price": tr.price_sol, "entry": tr.entry_price_sol, "qty": tr.qty, "sol": tr.sol_amount, "fee": tr.fee_sol,
                "pnl_sol": tr.pnl_sol, "pnl_pct": tr.pnl_pct, "reason": tr.reason, "simulated": tr.simulated,
                "tx": f"{config.SOLSCAN_BASE}/tx/{tr.signature}" if tr.signature else "",
                "token": f"{config.SOLSCAN_BASE}/token/{tr.mint}",
                "open": live is not None, "live_pct": live.gain_pct() if (live and tr.side == "BUY") else None,
                "result_pct": result_pct,
            }
            trades.append(item)
        watch = sorted(self.watch.values(), key=lambda w: w.score, reverse=True)[:8]
        banners: list[dict[str, str]] = []
        if self.market.is_simulated:
            banners.append({"level": "info", "text": "DEMO-MARKT: Kurse sind künstlich erzeugt (kein echter Markt)."})
        if self.mode == "live":
            banners.append({"level": "warn", "text": "LIVE-MODUS: Es darf nur EINE Instanz dieses Bots laufen "
                                                      "(nicht gleichzeitig im Replit-Editor und veröffentlicht)."})
        if not self.ready:
            banners.append({"level": "bad", "text": self.ready_error or "Live-Modus nicht bereit."})
        if self.halt_reason:
            banners.append({"level": "bad" if self.hard_halt else "warn", "text": self.halt_reason})
        return {
            "v": config.VERSION, "app": config.APP_NAME, "now": t,
            "mode": self.mode, "demo_market": bool(self.market.is_simulated),
            "engine": {
                "state": self.state, "hunting": self.hunting, "protect": bool(open_positions), "halt_reason": self.halt_reason,
                "halt_until": self.halt_until if self.halted else 0, "ready": self.ready, "ready_error": self.ready_error,
                "uptime_s": time.time() - self.started_at, "scans": self.scan_count, "last_scan": self.scout.last_scan_ts,
                "storage": self.store.describe(), "last_error": self.last_error,
            },
            "wallet": {"address": self.executor.wallet_address, "short": short_addr(self.executor.wallet_address, 4),
                       "sol": pf.cash_sol,
                       "explorer": f"{config.SOLSCAN_BASE}/account/{self.executor.wallet_address}" if self.executor.wallet_address else ""},
            "money": {
                "sol_usd": sol, "equity_sol": equity, "cash_sol": pf.cash_sol, "invested_sol": invested,
                "invested_pct": (invested / equity * 100.0) if equity > 0 else 0.0, "secured_usdc": pf.secured_usdc,
                "initial_sol": pf.initial_equity_sol, "pnl_sol": pnl, "pnl_pct": pnl / base * 100.0,
                "day_pnl_sol": day_pnl, "day_pnl_pct": day_pnl / day_base * 100.0, "realized_sol": pf.realized_pnl_sol,
                "fees_sol": pf.fees_sol, "capital_sol": self.capital(equity),
            },
            "agents": [a.snapshot() for a in self.agents],
            "committee": self.committee,
            "positions": [self._position_view(p, t) for p in sorted(open_positions, key=lambda p: -p.opened_at)],
            "trades": trades,
            "stats": pf.stats(),
            "watch": [{"mint": w.mint, "symbol": w.symbol, "score": round(w.score, 1),
                       "sent": round(w.sent.score, 1) if w.sent else None, "tech": round(w.tech.score, 1) if w.tech else None,
                       "signal": w.tech.signal if w.tech else "-", "safety": w.contract.safety_score, "image": w.image_url,
                       "age_s": t - w.added_at} for w in watch],
            "foreign": self.foreign_tokens[:8],
            "sources": self.market.health(),
            "banners": banners,
            "limits": {"max_position_sol": config.MAX_POSITION_SOL, "max_open": config.MAX_OPEN_POSITIONS,
                       "stop_pct": config.INITIAL_STOP_LOSS_PCT, "daily_loss_pct": config.DAILY_LOSS_LIMIT_PCT,
                       "entry_score": config.ENTRY_SCORE_MIN, "profit_currency": config.PROFIT_CURRENCY,
                       "trailing": [list(x) for x in config.TRAILING_TIERS], "take_profit": [list(x) for x in config.PARTIAL_TAKE_PROFIT]},
            "events": self.bus.since(since) if since else self.bus.recent(40),
            "last_seq": self.bus.last_seq,
        }

    def equity_points(self, range_key: str, max_points: int = 360) -> dict[str, Any]:
        t = self.now()
        spans = {"1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400}
        pts = self.pf.equity
        if range_key in spans:
            cutoff = t - spans[range_key]
            pts = [p for p in pts if p[0] >= cutoff] or pts[-2:]
        if len(pts) > max_points:                       # bucket downsample, always keep the last point
            step = len(pts) / max_points
            pts = [pts[int(i * step)] for i in range(max_points)] + [pts[-1]]
        return {"points": pts, "range": range_key, "initial": self.pf.initial_equity_sol}
