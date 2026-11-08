"""Agent 1 - THE SCOUT: finds fresh pools and filters scams / rug pulls.

Pipeline per scan: discover (Dexscreener, GeckoTerminal, RugCheck feeds) ->
cheap market prefilter (liquidity, age, activity) -> contract check
(mint/freeze authority, Token-2022 extensions, LP lock, RugCheck flags).
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

import config

from ..events import EventBus
from ..models import ContractReport, Pair
from .base import Agent

log = logging.getLogger("crew.scout")

_TTL = {"blocked": 7200.0, "ok": 5400.0, "filtered": 150.0, "nopair": 90.0}


def prefilter(p: Pair, at: float, cfg=config) -> list[str]:
    """Cheap market-metric filter. Returns the reasons for rejection (empty = pass)."""
    why: list[str] = []
    if p.quote_symbol.upper() not in cfg.ALLOWED_QUOTES:
        why.append(f"Quote {p.quote_symbol or '?'} nicht erlaubt")
    age = p.age_min(at)
    if age < cfg.MIN_PAIR_AGE_MIN:
        why.append("Pool zu neu")
    if age > cfg.MAX_PAIR_AGE_HOURS * 60:
        why.append("Pool zu alt")
    if p.liquidity_usd < cfg.MIN_LIQUIDITY_USD:
        why.append(f"Liquidität ${p.liquidity_usd:,.0f} < ${cfg.MIN_LIQUIDITY_USD:,.0f}")
    cap = p.fdv or p.market_cap
    if cap and (cap < cfg.MIN_FDV_USD or cap > cfg.MAX_FDV_USD):
        why.append(f"FDV ${cap:,.0f} außerhalb des Fensters")
    if p.txns_m5 < cfg.MIN_TXNS_M5:
        why.append(f"zu wenig Aktivität ({p.txns_m5} Trades/5 Min)")
    if p.price_sol <= 0:
        why.append("kein SOL-Preis")
    return why


@dataclass
class ScanResult:
    vetted: list[tuple[Pair, ContractReport]] = field(default_factory=list)
    considered: int = 0
    filtered: int = 0
    nopair: int = 0
    blocked: int = 0
    rejections: Counter = field(default_factory=Counter)


class Scout(Agent):
    id = "scout"
    name = "Scout"
    role = "Pool-Scanner & Contract-Check"
    icon = "radar"

    def __init__(self, bus: EventBus, market, step_delay: float = 0.0) -> None:
        super().__init__(bus, step_delay)
        self.market = market
        self._seen: dict[str, tuple[float, str]] = {}
        self.last_scan_ts = 0.0
        self.total_scans = 0

    def _skip(self, mint: str, t: float) -> bool:
        rec = self._seen.get(mint)
        return bool(rec and t - rec[0] < _TTL.get(rec[1], 120.0))

    def forget(self, mint: str) -> None:
        self._seen.pop(mint, None)

    def _trim(self, t: float) -> None:
        if len(self._seen) > 3000:
            for m in [m for m, (ts, st) in self._seen.items() if t - ts > _TTL.get(st, 120.0)]:
                self._seen.pop(m, None)

    async def scan(self, *, exclude: set[str], is_blacklisted: Callable[[str], bool]) -> ScanResult:
        res = ScanResult()
        t = self.market.now()
        self.total_scans += 1
        self.set("analyzing", "Scanne neue Pools: Dexscreener · GeckoTerminal · RugCheck", progress=0.05)
        try:
            cands = await self.market.discover()
        except Exception as exc:
            log.warning("discover failed: %s", exc)
            self.set("error", f"Datenquellen nicht erreichbar: {str(exc)[:80]}")
            return res
        fresh = [c for c in cands if c.mint not in exclude and not is_blacklisted(c.mint) and not self._skip(c.mint, t)]
        res.considered = len(fresh)
        self.last_scan_ts = t
        if not fresh:
            self.set("waiting", f"{len(cands)} Pools gesehen – nichts Neues zu prüfen")
            return res

        self.set("analyzing", f"{len(fresh)} neue Pools gefunden – lade Marktdaten …", progress=0.15)
        try:
            pairs = await self.market.pairs([c.mint for c in fresh][:90])
        except Exception as exc:
            log.warning("pairs failed: %s", exc)
            self.set("error", f"Marktdaten nicht erreichbar: {str(exc)[:80]}")
            return res

        passed: list[tuple] = []
        for c in fresh[:90]:
            p = pairs.get(c.mint)
            if p is None:
                self._seen[c.mint] = (t, "nopair")
                res.nopair += 1
                continue
            reasons = prefilter(p, t)
            if reasons:
                self._seen[c.mint] = (t, "filtered")
                res.filtered += 1
                res.rejections[reasons[0].split(" ")[0]] += 1
                continue
            passed.append((c, p))
        passed.sort(key=lambda cp: cp[1].vol_m5 + 25.0 * cp[1].txns_m5 + 0.01 * cp[0].hint, reverse=True)
        batch = passed[: config.MAX_VET_PER_CYCLE]
        self.stats["checked"] += res.considered

        if not batch:
            top = ", ".join(f"{k} ×{v}" for k, v in res.rejections.most_common(2))
            parts = [f"{res.nopair} noch ohne Marktdaten"] if res.nopair else []
            if res.filtered:
                parts.append(f"{res.filtered} ausgefiltert ({top})")
            self.set("waiting", f"{res.considered} neue Pools gesehen – " + (", ".join(parts) or "keiner erfüllt die Filter"))
            self._trim(t)
            return res

        for i, (c, p) in enumerate(batch):
            sym = f"${p.symbol or p.mint[:5]}"

            async def on_step(msg: str, frac: float, sym: str = sym, i: int = i) -> None:
                self.set("analyzing", f"{sym}: {msg}", progress=(i + frac) / len(batch), target=p.symbol)
                await self.beat(0.7)

            try:
                rep = await self.market.contract_check(p.mint, on_step)
            except Exception as exc:
                log.warning("contract check crashed for %s: %s", p.mint, exc)
                self._seen[p.mint] = (t, "filtered")
                continue
            if rep.ok:
                self._seen[p.mint] = (t, "ok")
                self.stats["passed"] += 1
                res.vetted.append((p, rep))
                lp = "n/a" if rep.lp_locked_pct is None else f"{rep.lp_locked_pct:.0f}%"
                self.set("confirmed", f"{sym} sicher ✓  Score {rep.safety_score} · Mint & Freeze aus · LP {lp}",
                         progress=(i + 1) / len(batch), target=p.symbol)
            else:
                self._seen[p.mint] = (t, "blocked")
                self.stats["blocked"] += 1
                res.blocked += 1
                self.set("blocked", f"{sym} blockiert ✗  {rep.blocked_reasons[0] if rep.blocked_reasons else 'Risiko'}",
                         progress=(i + 1) / len(batch), target=p.symbol)
            await self.beat()

        if res.vetted:
            names = ", ".join(f"${p.symbol}" for p, _ in res.vetted[:4])
            self.set("confirmed", f"{len(res.vetted)} sichere Kandidaten → Watchlist: {names}", progress=1.0)
        else:
            self.set("waiting", f"{len(batch)} Tokens geprüft – alle als riskant blockiert", progress=1.0)
        self._trim(t)
        return res
