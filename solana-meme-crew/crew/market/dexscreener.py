"""Dexscreener public API client (no key needed).

Verified against the live API (Nov 2026):
  * GET /token-profiles/latest/v1      -> list[{chainId, tokenAddress, ...}]
  * GET /token-boosts/latest|top/v1    -> list[{chainId, tokenAddress, amount, totalAmount, ...}]
  * GET /tokens/v1/solana/{a,b,...}    -> list[pair]   (max 30 addresses, best pool per token)
  * Responses are cached by Cloudflare for ~30 s (max-age=30) -> prices are 0-30 s old.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from ..http import HttpService, RateLimiter
from ..models import Candidate, Pair
from ..util import now, sf, si

log = logging.getLogger("crew.dex")

WSOL = "So11111111111111111111111111111111111111112"
BASE = "https://api.dexscreener.com"


def parse_pair(raw: Any, ts: float, wanted: Optional[set[str]] = None) -> Optional[Pair]:
    """Tolerant parser: fresh pools often lack liquidity / priceUsd / info."""
    if not isinstance(raw, dict) or raw.get("chainId") != "solana":
        return None
    base = raw.get("baseToken") or {}
    quote = raw.get("quoteToken") or {}
    mint = base.get("address") or ""
    if not mint or (wanted is not None and mint not in wanted):
        return None
    txns = raw.get("txns") or {}

    def tx(win: str, key: str) -> int:
        return si((txns.get(win) or {}).get(key))

    vol = raw.get("volume") or {}
    chg = raw.get("priceChange") or {}
    liq = raw.get("liquidity") or {}
    info = raw.get("info") or {}
    quote_mint = quote.get("address") or ""
    price_native = sf(raw.get("priceNative"))
    price_usd = sf(raw.get("priceUsd"))
    return Pair(
        mint=mint,
        symbol=str(base.get("symbol") or "")[:24],
        name=str(base.get("name") or "")[:60],
        pair_address=raw.get("pairAddress") or "",
        dex=str(raw.get("dexId") or ""),
        quote_symbol=str(quote.get("symbol") or ""),
        quote_mint=quote_mint,
        price_native=price_native,
        price_usd=price_usd,
        price_sol=price_native if quote_mint == WSOL else 0.0,
        liquidity_usd=sf(liq.get("usd")),
        fdv=sf(raw.get("fdv")),
        market_cap=sf(raw.get("marketCap")),
        vol_m5=sf(vol.get("m5")), vol_h1=sf(vol.get("h1")), vol_h6=sf(vol.get("h6")), vol_h24=sf(vol.get("h24")),
        buys_m5=tx("m5", "buys"), sells_m5=tx("m5", "sells"),
        buys_h1=tx("h1", "buys"), sells_h1=tx("h1", "sells"),
        buys_h6=tx("h6", "buys"), sells_h6=tx("h6", "sells"),
        buys_h24=tx("h24", "buys"), sells_h24=tx("h24", "sells"),
        chg_m5=sf(chg.get("m5")), chg_h1=sf(chg.get("h1")), chg_h6=sf(chg.get("h6")), chg_h24=sf(chg.get("h24")),
        created_ms=si(raw.get("pairCreatedAt")),
        image_url=str(info.get("imageUrl") or ""),
        websites=[str(w.get("url")) for w in (info.get("websites") or []) if isinstance(w, dict) and w.get("url")],
        socials=[str(s.get("type")) for s in (info.get("socials") or []) if isinstance(s, dict) and s.get("type")],
        boosts=si((raw.get("boosts") or {}).get("active")),
        url=str(raw.get("url") or ""),
        ts=ts,
    )


def pick_best(pairs: Iterable[Pair], allowed_quotes: tuple[str, ...]) -> Optional[Pair]:
    """Highest-liquidity pool, preferring an allowed quote token."""
    best: Optional[Pair] = None
    for p in pairs:
        key = (p.quote_symbol.upper() in allowed_quotes, p.liquidity_usd)
        if best is None or key > (best.quote_symbol.upper() in allowed_quotes, best.liquidity_usd):
            best = p
    return best


class DexscreenerClient:
    def __init__(self, svc: Optional[HttpService] = None, allowed_quotes: tuple[str, ...] = ("SOL", "USDC", "USDT")) -> None:
        self.svc = svc or HttpService(
            "dexscreener", BASE, limiter=RateLimiter(max_calls=120, window=60.0, min_interval=0.2), timeout=12.0, retries=2
        )
        self.allowed_quotes = tuple(q.upper() for q in allowed_quotes)

    async def latest_profiles(self) -> list[Candidate]:
        data = await self.svc.get_json("/token-profiles/latest/v1")
        out: list[Candidate] = []
        for it in data if isinstance(data, list) else []:
            if isinstance(it, dict) and it.get("chainId") == "solana" and it.get("tokenAddress"):
                out.append(Candidate(mint=it["tokenAddress"], source="profile"))
        return out

    async def boosted(self) -> list[Candidate]:
        out: dict[str, Candidate] = {}
        for path, tag in (("/token-boosts/latest/v1", "boost"), ("/token-boosts/top/v1", "boost-top")):
            try:
                data = await self.svc.get_json(path)
            except Exception as exc:
                log.debug("boosts %s failed: %s", path, exc)
                continue
            for it in data if isinstance(data, list) else []:
                if isinstance(it, dict) and it.get("chainId") == "solana" and it.get("tokenAddress"):
                    m = it["tokenAddress"]
                    hint = sf(it.get("totalAmount") or it.get("amount"))
                    if m not in out or hint > out[m].hint:
                        out[m] = Candidate(mint=m, source=tag, hint=hint)
        return list(out.values())

    async def pairs(self, mints: list[str]) -> dict[str, Pair]:
        """Best pool for each mint (batched, 30 per request)."""
        result: dict[str, Pair] = {}
        uniq = list(dict.fromkeys(m for m in mints if m))
        ts = now()
        for i in range(0, len(uniq), 30):
            chunk = uniq[i:i + 30]
            data = await self.svc.get_json(f"/tokens/v1/solana/{','.join(chunk)}")
            if not isinstance(data, list):
                continue
            wanted = set(chunk)
            by_mint: dict[str, list[Pair]] = {}
            for raw in data:
                p = parse_pair(raw, ts, wanted)
                if p:
                    by_mint.setdefault(p.mint, []).append(p)
            for m, plist in by_mint.items():
                best = pick_best(plist, self.allowed_quotes)
                if best:
                    result[m] = best
        return result

    async def sol_usd(self) -> float:
        got = await self.pairs([WSOL])
        p = got.get(WSOL)
        return p.price_usd if p else 0.0
