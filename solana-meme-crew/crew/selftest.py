"""Connection self-test: verifies every external dependency from the machine the
bot actually runs on (Replit, not my sandbox). Read-only; the optional dry run
builds + signs a real Jupiter swap and only *simulates* it - no funds move."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable

import config

BONK = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    ms: int = 0
    critical: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def _timed(name: str, fn: Callable[[], Awaitable[str]], critical: bool = True) -> Check:
    t0 = time.monotonic()
    try:
        detail = await fn()
        return Check(name, True, detail, int((time.monotonic() - t0) * 1000), critical)
    except Exception as exc:                                  # a check must never crash the test run
        msg = f"{type(exc).__name__}: {exc}"
        return Check(name, False, msg[:220], int((time.monotonic() - t0) * 1000), critical)


async def run_checks(market, executor=None, *, dry_run: bool = False) -> list[Check]:
    if getattr(market, "is_simulated", False):
        return [Check("Demo-Markt", True, "Künstlicher Markt aktiv – keine externen Dienste nötig", 0, False)]
    out: list[Check] = []

    async def dex() -> str:
        p = (await market.dex.pairs([config.WSOL_MINT])).get(config.WSOL_MINT)
        if not p or p.price_usd <= 0:
            raise RuntimeError("keine Preisdaten erhalten")
        return f"SOL = {p.price_usd:,.2f} $ (Pool {p.dex})"

    async def gecko() -> str:
        pools = await market.gecko.new_pools()
        if not pools:
            raise RuntimeError("leere Antwort")
        return f"{len(pools)} neue Pools gelesen"

    async def rugcheck() -> str:
        s = await market.rugcheck.summary(BONK)
        if "score_normalised" not in s:
            raise RuntimeError("unerwartetes Antwortformat")
        return f"Test-Token geprüft (Risiko-Score {s.get('score_normalised')}, LP {s.get('lpLockedPct', 0):.0f}% gesperrt)"

    async def rpc() -> str:
        if not await market.rpc.healthy():
            raise RuntimeError("getHealth meldet nicht 'ok'")
        mi = await market.rpc.get_mint_info(BONK)
        h = await market.rpc.get_block_height()
        return f"Node gesund · Blockhöhe {h:,} · Mint-Abfrage ok (Dezimalstellen {mi.decimals})"

    async def jup_quote() -> str:
        o = await market.jupiter.order(config.WSOL_MINT, config.USDC_MINT, 10_000_000)
        if o.out_amount <= 0:
            raise RuntimeError("keine Quote")
        key = "mit API-Key" if market.jupiter.api_key else "ohne API-Key (langsamer, ok)"
        return f"0,01 SOL = {o.out_amount / 1e6:.3f} USDC · Router {o.router} · Gebühr {o.fee_bps} bps · {key}"

    async def jup_price() -> str:
        res = await market.jupiter.prices([config.WSOL_MINT], reserve=0, block=True)
        usd = ((res or {}).get(config.WSOL_MINT) or {}).get("usdPrice")
        if not usd:
            raise RuntimeError("kein Preis")
        return f"Echtzeit-Preis SOL = {usd:,.2f} $"

    out.append(await _timed("Dexscreener (Marktdaten)", dex))
    out.append(await _timed("GeckoTerminal (neue Pools)", gecko, critical=False))
    out.append(await _timed("RugCheck (Scam-Prüfung)", rugcheck))
    out.append(await _timed("Solana-RPC (Blockchain)", rpc))
    out.append(await _timed("Jupiter (Swap-Quote)", jup_quote))
    out.append(await _timed("Jupiter (Echtzeit-Preis)", jup_price, critical=False))

    wallet = getattr(executor, "wallet", None)
    if not config.PAPER_TRADING:
        if wallet is None:
            out.append(Check("Wallet", False, "Private Key fehlt oder ist ungültig (SOLANA_PRIVATE_KEY)", 0))
        else:
            async def balance() -> str:
                bal = await market.rpc.get_balance_sol(wallet.address)
                if bal < config.LIVE_MIN_SOL_TO_START:
                    raise RuntimeError(f"nur {bal:.4f} SOL – mindestens {config.LIVE_MIN_SOL_TO_START} nötig")
                return f"{wallet.address[:4]}…{wallet.address[-4:]} · {bal:.4f} SOL"
            out.append(await _timed("Wallet & Guthaben", balance))

            if dry_run:
                async def dry() -> str:
                    o = await market.jupiter.order(config.WSOL_MINT, config.USDC_MINT, 1_000_000,
                                                   taker=wallet.address, slippage_bps=100)
                    if not o.can_sign:
                        raise RuntimeError(o.error_message or f"keine signierbare Transaktion (Code {o.error_code})")
                    signed = wallet.partial_sign(o.transaction or "")
                    sim = await market.rpc.simulate(signed.b64)
                    if sim.get("err"):
                        raise RuntimeError(f"Simulation meldet {sim['err']}")
                    return f"Swap gebaut, signiert und simuliert ✓ (Rechenlast {sim.get('unitsConsumed', '?')}) – es wurde NICHTS gesendet"
                out.append(await _timed("Live-Trockenlauf (signieren + simulieren)", dry))
    return out


def summarize(checks: list[Check]) -> dict[str, Any]:
    failed = [c for c in checks if not c.ok and c.critical]
    return {"ok": not failed, "checks": [c.to_dict() for c in checks],
            "message": "Alles bereit ✓" if not failed else f"{len(failed)} kritische(r) Fehler – siehe Liste"}
