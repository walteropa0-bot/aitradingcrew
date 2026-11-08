"""RealMarket: one facade over Dexscreener, GeckoTerminal, RugCheck, Solana RPC
and Jupiter. The engine only talks to this interface, so the synthetic demo
market (simulator.py) can be dropped in without touching any agent code.

Interface (duck-typed):
    name, is_simulated
    now() -> float
    async discover() -> list[Candidate]
    async pairs(mints) -> dict[mint, Pair]
    async sol_usd() -> float
    async contract_check(mint, on_step) -> ContractReport
    async marks(mints) -> dict[mint, float]          # real-time SOL price per token
    health() -> list[dict]
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import config

from ..chain.jupiter import JupiterClient
from ..models import Candidate, ContractReport, Pair
from ..util import sf
from .contracts import ContractChecker, StepCb
from .dexscreener import DexscreenerClient
from .gecko import GeckoClient, GeckoPool
from .rugcheck import RugCheckClient
from .solana_rpc import SolanaRPC

log = logging.getLogger("crew.market")

_RANK = {"gecko-trending": 5, "boost": 4, "gecko-new": 3, "boost-top": 3, "profile": 2, "rugcheck-new": 1}


class RealMarket:
    name = "real"
    is_simulated = False

    def __init__(
        self,
        dex: Optional[DexscreenerClient] = None,
        gecko: Optional[GeckoClient] = None,
        rugcheck: Optional[RugCheckClient] = None,
        rpc: Optional[SolanaRPC] = None,
        jupiter: Optional[JupiterClient] = None,
    ) -> None:
        self.dex = dex or DexscreenerClient(allowed_quotes=tuple(config.ALLOWED_QUOTES))
        self.gecko = gecko or GeckoClient()
        self.rugcheck = rugcheck or RugCheckClient()
        self.rpc = rpc or SolanaRPC()
        self.jupiter = jupiter or JupiterClient()
        self.checker = ContractChecker(self.rpc, self.rugcheck)
        self.gecko_cache: dict[str, GeckoPool] = {}
        self._sol_usd = 0.0
        self._sol_usd_ts = 0.0
        self._disc_n = 0
        self.last_data_ok = 0.0          # last time Dexscreener answered

    def now(self) -> float:
        return time.time()

    # ── discovery ───────────────────────────────────────────────────────
    async def discover(self) -> list[Candidate]:
        self._disc_n += 1
        jobs: dict[str, "asyncio.Future"] = {
            "profiles": asyncio.ensure_future(self.dex.latest_profiles()),
            "boosts": asyncio.ensure_future(self.dex.boosted()),
            "gecko_new": asyncio.ensure_future(self.gecko.new_pools()),
            "gecko_trend": asyncio.ensure_future(self.gecko.trending_pools()),
            "rc_new": asyncio.ensure_future(self.rugcheck.new_tokens(limit=25)),
        }
        results = dict(zip(jobs.keys(), await asyncio.gather(*jobs.values(), return_exceptions=True)))
        merged: dict[str, Candidate] = {}

        def add(c: Candidate) -> None:
            cur = merged.get(c.mint)
            if cur is None or _RANK.get(c.source, 0) > _RANK.get(cur.source, 0):
                hint = max(c.hint, cur.hint if cur else 0.0)
                merged[c.mint] = Candidate(mint=c.mint, source=c.source, hint=hint)

        errors = []
        for key, res in results.items():
            if isinstance(res, Exception):
                errors.append(f"{key}: {res}")
                continue
            if key in ("gecko_new", "gecko_trend"):
                for gp in res:
                    self.gecko_cache[gp.mint] = gp
                for c in GeckoClient.to_candidates(res):
                    add(c)
            else:
                for c in res:
                    add(c)
        if errors:
            log.info("discovery partial failures: %s", "; ".join(errors)[:400])
        if len(self.gecko_cache) > 600:      # keep memory bounded
            for k in list(self.gecko_cache)[:200]:
                self.gecko_cache.pop(k, None)
        return sorted(merged.values(), key=lambda c: (_RANK.get(c.source, 0), c.hint), reverse=True)

    # ── prices / pairs ──────────────────────────────────────────────────
    async def sol_usd(self) -> float:
        if self._sol_usd and time.time() - self._sol_usd_ts < 20:
            return self._sol_usd
        value = 0.0
        try:
            value = await self.dex.sol_usd()
        except Exception as exc:
            log.debug("sol_usd via dexscreener failed: %s", exc)
        if value <= 0:
            try:
                res = await self.jupiter.prices([config.WSOL_MINT], reserve=4)
                value = sf(((res or {}).get(config.WSOL_MINT) or {}).get("usdPrice"))
            except Exception as exc:
                log.debug("sol_usd via jupiter failed: %s", exc)
        if value > 0:
            self._sol_usd, self._sol_usd_ts = value, time.time()
        return self._sol_usd

    async def pairs(self, mints: list[str]) -> dict[str, Pair]:
        got = await self.dex.pairs(mints)
        if got:
            self.last_data_ok = time.time()
        sol = await self.sol_usd()
        for m, p in got.items():
            if p.price_sol <= 0 and p.price_usd > 0 and sol > 0:
                p.price_sol = p.price_usd / sol
            gp = self.gecko_cache.get(m)
            if gp:
                p.buyers_m5, p.sellers_m5 = gp.buyers_m5, gp.sellers_m5
                p.buyers_h1, p.sellers_h1 = gp.buyers_h1, gp.sellers_h1
        return got

    async def marks(self, mints: list[str]) -> dict[str, float]:
        """Near real-time SOL price per token from Jupiter's price API (block level).
        Returns {} when there is no rate-limit headroom or the API fails."""
        if not mints:
            return {}
        try:
            res = await self.jupiter.prices(list(mints) + [config.WSOL_MINT])
        except Exception as exc:
            log.debug("jupiter price failed: %s", exc)
            return {}
        if not res:
            return {}
        sol = sf((res.get(config.WSOL_MINT) or {}).get("usdPrice"))
        if sol <= 0:
            return {}
        self._sol_usd, self._sol_usd_ts = sol, time.time()
        out: dict[str, float] = {}
        for m in mints:
            usd = sf((res.get(m) or {}).get("usdPrice"))
            if usd > 0:
                out[m] = usd / sol
        return out

    async def contract_check(self, mint: str, on_step: StepCb = None) -> ContractReport:
        return await self.checker.check(mint, on_step)

    def health(self) -> list[dict]:
        return [s.health() for s in (self.dex.svc, self.gecko.svc, self.rugcheck.svc, self.rpc.svc, self.jupiter.svc)]

    async def aclose(self) -> None:
        for s in (self.dex.svc, self.gecko.svc, self.rugcheck.svc, self.rpc.svc, self.jupiter.svc):
            await s.aclose()
