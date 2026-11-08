"""SimMarket - a synthetic meme-coin market with the same interface as RealMarket.

Purpose: try the whole system (agents, trailing stops, dashboard) without
internet or real data, and drive deterministic tests. It is NEVER allowed with
PAPER_TRADING = False.

Tokens follow regime-switching price paths (quiet start -> pump -> plateau ->
bleed), with pump&dump, hidden rug pulls, duds and tokens with unsafe contracts
(mint authority / freeze authority / unlocked LP) that the Scout must block.
"""
from __future__ import annotations

import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import config

from ..models import Candidate, ContractReport, Pair
from ..util import clamp
from .contracts import StepCb, evaluate
from .solana_rpc import MintInfo

_SYL = ["PEPE", "DOGE", "WIF", "BONK", "MOON", "CAT", "FROG", "SHIB", "GIGA", "CHAD", "BASED", "HODL", "WOJAK",
        "TURBO", "NEKO", "APE", "SOL", "PUMP", "RUG", "KEK", "MEME", "ZOOM", "LAMBO", "DEGEN", "WAGMI", "GM", "FOMO",
        "BOBO", "MOG", "POPCAT"]
_SUF = ["", "", "2", "AI", "INU", "COIN", "X", "PRIME", "HAT", "CULT"]
_KINDS = [("gem", 0.22), ("moon", 0.05), ("pump_dump", 0.20), ("rug_hidden", 0.08), ("dud", 0.22),
          ("mint_auth", 0.07), ("freeze", 0.04), ("lp_unlocked", 0.12)]
SUPPLY = 1_000_000_000.0
RUG = "RUG"


@dataclass
class SimToken:
    mint: str
    symbol: str
    created: float
    kind: str
    price: float
    liq_usd: float
    phases: list
    phase_idx: int = 0
    phase_end: float = 0.0
    avg_trade_usd: float = 90.0
    socials: list = field(default_factory=list)
    boosts: int = 0
    dead: bool = False
    t: float = 0.0
    flow: deque = field(default_factory=lambda: deque(maxlen=3600))      # (buys, sells, vol_usd) per second
    hist: deque = field(default_factory=lambda: deque(maxlen=3700))      # price per second
    tot_buys: int = 0
    tot_sells: int = 0
    tot_vol: float = 0.0
    dex: str = "pumpswap"


def _phases(kind: str, rng: random.Random) -> list:
    u = rng.uniform
    if kind in ("gem", "mint_auth", "freeze", "lp_unlocked"):
        return [(u(50, 110), 0.0003, 0.008, 0.00, 0.35), (u(180, 420), u(0.0009, 0.0020), 0.012, 0.10, 3.0),
                (u(150, 360), 0.0, 0.014, 0.0, 1.8), (1e9, -0.0007, 0.012, -0.08, 1.1)]
    if kind == "moon":
        return [(70, 0.0006, 0.009, 0.05, 0.9), (u(300, 520), u(0.0020, 0.0034), 0.015, 0.13, 5.0),
                (u(150, 300), -0.0002, 0.018, 0.0, 2.5), (1e9, -0.0012, 0.016, -0.12, 1.1)]
    if kind == "pump_dump":
        return [(u(50, 100), 0.0003, 0.008, 0.0, 0.5), (u(100, 180), u(0.0018, 0.0032), 0.016, 0.13, 4.0),
                (u(80, 150), -0.0040, 0.022, -0.18, 3.5), (1e9, -0.0008, 0.014, -0.10, 0.9)]
    if kind == "rug_hidden":
        return [(u(50, 100), 0.0003, 0.008, 0.0, 0.5), (u(100, 200), u(0.0016, 0.0030), 0.014, 0.11, 3.5), (1.0, RUG, 0, 0, 0),
                (1e9, 0.0, 0.03, -0.3, 0.3)]
    return [(1e9, -0.0003, 0.012, -0.02, 0.6)]      # dud


class SimMarket:
    name = "simulated"
    is_simulated = True

    def __init__(self, seed: Optional[int] = None, auto: bool = True, now_fn=None, initial_tokens: int = 14) -> None:
        self.rng = random.Random(seed)
        self.auto = auto
        self._now_fn = now_fn or time.time
        self._t = self._now_fn()
        self.tokens: dict[str, SimToken] = {}
        self.sol_price = 90.0
        self._n = 0
        self.last_data_ok = self._t
        self.jupiter = None
        for _ in range(initial_tokens):
            age = self.rng.uniform(60, 3000)
            self._spawn(self._t - age, prewarm_to=self._t)

    # ── time ────────────────────────────────────────────────────────────
    def now(self) -> float:
        if self.auto:
            self._advance_to(self._now_fn())
        return self._t

    def advance(self, seconds: float) -> None:
        self._advance_to(self._t + seconds)

    def _advance_to(self, target: float) -> None:
        steps = 0
        while self._t < target and steps < 7200:
            self._t += 1.0
            steps += 1
            for tok in list(self.tokens.values()):
                self._step(tok, self._t)
            self.sol_price = clamp(self.sol_price * math.exp(self.rng.gauss(0, 0.0003)), 60, 150)
            if self.rng.random() < 1 / 38.0:
                self._spawn(self._t)
        if self._t < target:           # long sleep (e.g. machine suspended): jump
            self._t = target
        self.last_data_ok = self._t
        if len(self.tokens) > 60:
            old = sorted(self.tokens.values(), key=lambda k: k.created)[:15]
            for k in old:
                self.tokens.pop(k.mint, None)

    # ── token life ──────────────────────────────────────────────────────
    def _spawn(self, created: float, prewarm_to: Optional[float] = None) -> SimToken:
        r = self.rng
        kind = r.choices([k for k, _ in _KINDS], [w for _, w in _KINDS])[0]
        self._n += 1
        mint = "SIM" + "".join(r.choice("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz") for _ in range(38)) + "pump"
        symbol = (r.choice(_SYL) + r.choice(_SUF))[:10]
        fdv0 = math.exp(r.uniform(math.log(24_000), math.log(160_000)))
        price0 = fdv0 / (SUPPLY * self.sol_price)
        tok = SimToken(
            mint=mint, symbol=symbol, created=created, kind=kind, price=price0,
            liq_usd=math.exp(r.uniform(math.log(14_000), math.log(110_000))), phases=_phases(kind, r),
            avg_trade_usd=r.uniform(50, 140), socials=r.sample(["twitter", "telegram", "website"], r.choice([0, 1, 1, 2, 3])),
            boosts=1 if r.random() < 0.08 else 0, dex=r.choice(["pumpswap", "raydium", "meteora"]), t=created,
        )
        tok.phase_end = created + tok.phases[0][0]
        tok.hist.append(price0)
        self.tokens[mint] = tok
        if prewarm_to:
            t = created
            while t < prewarm_to:
                t += 1.0
                self._step(tok, t)
        return tok

    def _step(self, tok: SimToken, t: float) -> None:
        r = self.rng
        tok.t = t
        while t >= tok.phase_end and tok.phase_idx < len(tok.phases) - 1:
            tok.phase_idx += 1
            tok.phase_end = t + tok.phases[tok.phase_idx][0]
        dur, drift, vol, bias, act = tok.phases[tok.phase_idx]
        if drift == RUG:
            tok.price *= 0.04
            tok.liq_usd *= 0.05
            tok.dead = True
            tok.flow.append((0, 14, tok.avg_trade_usd * 14))
            tok.tot_sells += 14
            tok.hist.append(tok.price)
            return
        ret = drift + vol * r.gauss(0, 1)
        tok.price *= math.exp(ret)
        tok.liq_usd *= math.exp(0.5 * ret)
        n_f = act * math.exp(r.gauss(0, 0.35))
        n = int(n_f) + (1 if r.random() < n_f - int(n_f) else 0)
        p_buy = clamp(0.5 + bias + 5.0 * ret, 0.05, 0.95)
        buys = sum(1 for _ in range(n) if r.random() < p_buy)
        vol_usd = n * tok.avg_trade_usd * math.exp(r.gauss(0, 0.4))
        tok.flow.append((buys, n - buys, vol_usd))
        tok.tot_buys += buys
        tok.tot_sells += n - buys
        tok.tot_vol += vol_usd
        tok.hist.append(tok.price)

    # ── interface: discovery / pairs / prices ───────────────────────────
    async def discover(self) -> list[Candidate]:
        self.now()
        out = [Candidate(mint=k.mint, source="sim-new", hint=k.flow[-1][2] if k.flow else 0.0)
               for k in self.tokens.values() if self._t - k.created < 3 * 3600]
        out.sort(key=lambda c: c.hint, reverse=True)
        return out

    def _pair(self, tok: SimToken) -> Pair:
        def win(sec: int):
            items = list(tok.flow)[-sec:]
            return (sum(i[0] for i in items), sum(i[1] for i in items), sum(i[2] for i in items))

        def chg(sec: int) -> float:
            h = list(tok.hist)
            if not h:
                return 0.0
            ref = h[-sec - 1] if len(h) > sec else h[0]
            return (tok.price / ref - 1.0) * 100.0 if ref > 0 else 0.0

        b5, s5, v5 = win(300)
        b1, s1, v1 = win(3600)
        usd = tok.price * self.sol_price
        return Pair(
            mint=tok.mint, symbol=tok.symbol, name=tok.symbol + " Coin", pair_address="SIMPAIR" + tok.mint[3:12],
            dex=tok.dex, quote_symbol="SOL", quote_mint=config.WSOL_MINT, price_native=tok.price, price_usd=usd,
            price_sol=tok.price, liquidity_usd=tok.liq_usd, fdv=usd * SUPPLY, market_cap=usd * SUPPLY,
            vol_m5=v5, vol_h1=v1, vol_h6=tok.tot_vol, vol_h24=tok.tot_vol,
            buys_m5=b5, sells_m5=s5, buys_h1=b1, sells_h1=s1, buys_h6=tok.tot_buys, sells_h6=tok.tot_sells,
            buys_h24=tok.tot_buys, sells_h24=tok.tot_sells,
            chg_m5=chg(300), chg_h1=chg(3600), chg_h6=chg(10_000), chg_h24=chg(10_000),
            created_ms=int(tok.created * 1000), socials=list(tok.socials),
            websites=["https://example.org"] if "website" in tok.socials else [], boosts=tok.boosts,
            url=f"https://dexscreener.com/solana/{tok.mint}", ts=self._t,
            buyers_m5=int(b5 * 0.7), sellers_m5=int(s5 * 0.7),
        )

    async def pairs(self, mints: list[str]) -> dict[str, Pair]:
        self.now()
        return {m: self._pair(self.tokens[m]) for m in mints if m in self.tokens}

    async def sol_usd(self) -> float:
        self.now()
        return self.sol_price

    async def marks(self, mints: list[str]) -> dict[str, float]:
        self.now()
        return {m: self.tokens[m].price for m in mints if m in self.tokens}

    async def contract_check(self, mint: str, on_step: StepCb = None) -> ContractReport:
        self.now()
        tok = self.tokens.get(mint)
        if on_step:
            await on_step("Mint- & Freeze-Authority on-chain prüfen (RPC)", 0.2)
            await on_step("RugCheck: Risiko-Flags, Top-Holder, LP-Lock", 0.65)
            await on_step("Contract-Verdikt berechnen", 0.95)
        if tok is None:
            return evaluate(mint, None, None)
        k = tok.kind
        mi = MintInfo(mint=mint, decimals=6, supply=int(SUPPLY * 1e6), program=config.TOKEN_PROGRAM,
                      mint_authority="MintAuth111" if k == "mint_auth" else None,
                      freeze_authority="Freeze111" if k == "freeze" else None, extensions=[])
        risks, lp, score = [], 100.0, self.rng.randint(1, 14)
        if k == "lp_unlocked":
            lp, score = self.rng.uniform(0, 20), self.rng.randint(45, 70)
            risks.append({"name": "Large Amount of LP Unlocked", "level": "danger"})
        if k in ("mint_auth", "freeze"):
            score = self.rng.randint(30, 60)
        if k == "gem" and self.rng.random() < 0.3:
            risks.append({"name": "Low amount of LP Providers", "level": "warn"})
        return evaluate(mint, mi, {"lpLockedPct": lp, "score_normalised": score, "risks": risks})

    def health(self) -> list[dict]:
        return [{"name": "demo-markt", "status": "ok", "calls": 0, "failures": 0, "last_ok": self._t,
                 "last_error": "", "latency_ms": 0}]

    async def aclose(self) -> None:
        return None
