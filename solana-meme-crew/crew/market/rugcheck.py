"""RugCheck public API (no key). The *summary* endpoint is tiny (the full report
can be >2 MB), so only the summary is used.

Verified shape: {tokenProgram, tokenType, risks:[{name,value,description,score,level}],
score, score_normalised, lpLockedPct}.  level in {"info","warn","danger"}.
/v1/stats/new_tokens -> [{mint, symbol, creator, mintAuthority, freezeAuthority, createAt,...}]
"""
from __future__ import annotations

from typing import Any, Optional

from ..http import HttpService, RateLimiter
from ..models import Candidate

BASE = "https://api.rugcheck.xyz"


class RugCheckClient:
    def __init__(self, svc: Optional[HttpService] = None) -> None:
        self.svc = svc or HttpService(
            "rugcheck", BASE, limiter=RateLimiter(max_calls=8, window=10.0, min_interval=0.7), timeout=15.0, retries=1
        )

    async def new_tokens(self, limit: int = 40) -> list[Candidate]:
        data = await self.svc.get_json("/v1/stats/new_tokens")
        out: list[Candidate] = []
        for it in (data if isinstance(data, list) else [])[:limit]:
            if isinstance(it, dict) and it.get("mint"):
                out.append(Candidate(mint=it["mint"], source="rugcheck-new"))
        return out

    async def summary(self, mint: str) -> dict[str, Any]:
        data = await self.svc.get_json(f"/v1/tokens/{mint}/report/summary")
        return data if isinstance(data, dict) else {}
