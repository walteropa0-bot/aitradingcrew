"""GeckoTerminal public API (free): new + trending pools and *unique wallet* counts.

Verified shape (Nov 2026): data[].attributes{address,name,pool_created_at,fdv_usd,
reserve_in_usd, transactions{m5,h1..:{buys,sells,buyers,sellers}}, volume_usd{...}},
data[].relationships.base_token.data.id == "solana_<mint>". Cached ~30-60 s.
"""
from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from typing import Any, Optional

from ..http import HttpService, RateLimiter
from ..models import Candidate
from ..util import sf, si

log = logging.getLogger("crew.gecko")

BASE = "https://api.geckoterminal.com/api/v2"
STABLES_AND_SOL = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


@dataclass
class GeckoPool:
    mint: str
    pool: str = ""
    name: str = ""
    dex: str = ""
    created_s: float = 0.0
    reserve_usd: float = 0.0
    fdv_usd: float = 0.0
    vol_m5: float = 0.0
    vol_h1: float = 0.0
    buys_m5: int = 0
    sells_m5: int = 0
    buyers_m5: int = 0
    sellers_m5: int = 0
    buyers_h1: int = 0
    sellers_h1: int = 0
    source: str = "new"


def _ts(value: Any) -> float:
    try:
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def _token_id(rel: Any) -> str:
    try:
        tid = str(rel["data"]["id"])
    except (KeyError, TypeError):
        return ""
    return tid.split("_", 1)[1] if tid.startswith("solana_") else tid


def parse_pool(item: Any, source: str) -> Optional[GeckoPool]:
    if not isinstance(item, dict):
        return None
    attrs = item.get("attributes") or {}
    rel = item.get("relationships") or {}
    base = _token_id(rel.get("base_token"))
    quote = _token_id(rel.get("quote_token"))
    # the meme coin is whichever side is not SOL / a stablecoin
    mint = base if base and base not in STABLES_AND_SOL else (quote if quote not in STABLES_AND_SOL else "")
    if not mint:
        return None
    tx = attrs.get("transactions") or {}
    m5 = tx.get("m5") or {}
    h1 = tx.get("h1") or {}
    vol = attrs.get("volume_usd") or {}
    return GeckoPool(
        mint=mint,
        pool=str(attrs.get("address") or ""),
        name=str(attrs.get("name") or ""),
        dex=str(((rel.get("dex") or {}).get("data") or {}).get("id") or ""),
        created_s=_ts(attrs.get("pool_created_at")),
        reserve_usd=sf(attrs.get("reserve_in_usd")),
        fdv_usd=sf(attrs.get("fdv_usd")),
        vol_m5=sf(vol.get("m5")),
        vol_h1=sf(vol.get("h1")),
        buys_m5=si(m5.get("buys")), sells_m5=si(m5.get("sells")),
        buyers_m5=si(m5.get("buyers")), sellers_m5=si(m5.get("sellers")),
        buyers_h1=si(h1.get("buyers")), sellers_h1=si(h1.get("sellers")),
        source=source,
    )


class GeckoClient:
    def __init__(self, svc: Optional[HttpService] = None) -> None:
        self.svc = svc or HttpService(
            "geckoterminal",
            BASE,
            headers={"Accept": "application/json;version=20230302"},
            limiter=RateLimiter(max_calls=18, window=60.0, min_interval=2.2),
            timeout=14.0,
            retries=1,
        )

    async def new_pools(self, page: int = 1) -> list[GeckoPool]:
        data = await self.svc.get_json("/networks/solana/new_pools", params={"page": page})
        return [p for p in (parse_pool(i, "new") for i in (data or {}).get("data", [])) if p]

    async def trending_pools(self, duration: str = "1h") -> list[GeckoPool]:
        data = await self.svc.get_json("/networks/solana/trending_pools", params={"duration": duration})
        return [p for p in (parse_pool(i, "trending") for i in (data or {}).get("data", [])) if p]

    @staticmethod
    def to_candidates(pools: list[GeckoPool]) -> list[Candidate]:
        return [Candidate(mint=p.mint, source=f"gecko-{p.source}", hint=p.vol_m5) for p in pools]
