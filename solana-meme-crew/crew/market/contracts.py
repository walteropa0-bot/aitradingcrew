"""Scout's contract / rug-pull checks.

Two independent sources are combined and the check *fails closed*:
  1. On-chain truth via Solana RPC: mint authority, freeze authority,
     Token-2022 extensions (transfer fee / permanent delegate / hooks ...).
  2. RugCheck summary: risk flags, LP-lock percentage, normalised risk score.
If a source cannot be reached the token is NOT traded ("unknown = unsafe").
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

import config

from ..models import ContractReport
from ..util import clamp, now, sf
from .rugcheck import RugCheckClient
from .solana_rpc import MintInfo, SolanaRPC

log = logging.getLogger("crew.contracts")

StepCb = Optional[Callable[[str, float], Awaitable[None]]]


def evaluate(mint: str, mi: Optional[MintInfo], rc: Optional[dict], *, cfg=config) -> ContractReport:
    """Pure function (unit-testable): combine raw source data into a verdict."""
    rep = ContractReport(mint=mint, checked_at=now())
    blocked: list[str] = []
    warns: list[str] = []

    # ── on-chain data ───────────────────────────────────────────────────
    if mi is not None:
        rep.sources.append("rpc")
        rep.decimals = mi.decimals
        rep.token_program = mi.program
        rep.extensions = list(mi.extensions)
        rep.mint_authority_disabled = mi.mint_authority is None
        rep.freeze_authority_disabled = mi.freeze_authority is None
        if cfg.REQUIRE_MINT_AUTH_DISABLED and not rep.mint_authority_disabled:
            blocked.append("Mint-Authority aktiv (Entwickler kann beliebig neue Token drucken)")
        if cfg.REQUIRE_FREEZE_AUTH_DISABLED and not rep.freeze_authority_disabled:
            blocked.append("Freeze-Authority aktiv (Wallets können eingefroren werden)")
        bad = [e for e in mi.extensions if e in cfg.BLOCKED_TOKEN2022_EXTENSIONS]
        if bad:
            blocked.append(f"Token-2022-Erweiterung gefährlich: {', '.join(bad)}")
    else:
        blocked.append("On-Chain-Prüfung nicht möglich (Solana-RPC nicht erreichbar)")

    # ── RugCheck ────────────────────────────────────────────────────────
    if rc:
        rep.sources.append("rugcheck")
        lp = rc.get("lpLockedPct")
        rep.lp_locked_pct = None if lp is None else sf(lp)
        norm = rc.get("score_normalised")
        rep.rug_score = None if norm is None else sf(norm)
        risks = [r for r in (rc.get("risks") or []) if isinstance(r, dict)]
        rep.risks = [{"name": r.get("name"), "level": r.get("level"), "description": r.get("description")} for r in risks]
        for r in risks:
            name = str(r.get("name") or "")
            level = str(r.get("level") or "").lower()
            if name in cfg.RUGCHECK_IGNORE_RISKS:
                continue
            if level in cfg.RUGCHECK_BLOCK_LEVELS:
                blocked.append(f"RugCheck-Warnung: {name}")
            elif level == "warn":
                warns.append(name)
        if rep.lp_locked_pct is not None and rep.lp_locked_pct < cfg.MIN_LP_LOCKED_PCT:
            blocked.append(f"Liquidität nur zu {rep.lp_locked_pct:.0f}% gesperrt (min. {cfg.MIN_LP_LOCKED_PCT}%)")
        if rep.lp_locked_pct is None:
            warns.append("LP-Lock unbekannt")
        if rep.rug_score is not None and rep.rug_score > cfg.MAX_RUGCHECK_SCORE:
            blocked.append(f"RugCheck-Risikoscore {rep.rug_score:.0f} > {cfg.MAX_RUGCHECK_SCORE}")
    else:
        blocked.append("RugCheck-Analyse nicht verfügbar")

    rep.blocked_reasons = blocked
    rep.warnings = warns
    rep.ok = not blocked

    score = 100.0
    if rep.rug_score is not None:
        score -= rep.rug_score * 0.6
    score -= min(24.0, 6.0 * len(warns))
    if rep.lp_locked_pct is not None and rep.lp_locked_pct < 100:
        score -= (100.0 - rep.lp_locked_pct) * 0.15
    if mi is None or not rc:
        score -= 15.0
    score = clamp(score, 0.0, 100.0)
    rep.safety_score = int(round(min(score, 35.0) if blocked else score))
    return rep


class ContractChecker:
    def __init__(self, rpc: SolanaRPC, rugcheck: RugCheckClient) -> None:
        self.rpc = rpc
        self.rugcheck = rugcheck

    async def check(self, mint: str, on_step: StepCb = None) -> ContractReport:
        async def step(msg: str, p: float) -> None:
            if on_step:
                await on_step(msg, p)

        mi: Optional[MintInfo] = None
        rc: Optional[dict] = None
        await step("Mint- & Freeze-Authority on-chain prüfen (RPC)", 0.2)
        try:
            mi = await self.rpc.get_mint_info(mint)
        except Exception as exc:
            log.info("RPC mint check failed for %s: %s", mint, exc)
        await step("RugCheck: Risiko-Flags, Top-Holder, LP-Lock", 0.65)
        try:
            rc = await self.rugcheck.summary(mint)
        except Exception as exc:
            log.info("RugCheck failed for %s: %s", mint, exc)
        await step("Contract-Verdikt berechnen", 0.95)
        return evaluate(mint, mi, rc)
