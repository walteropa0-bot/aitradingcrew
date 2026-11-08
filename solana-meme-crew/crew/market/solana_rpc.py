"""Thin async Solana JSON-RPC client (only the calls this bot needs).

Intentionally *not* solana-py: fewer moving parts, no version drift, and every
call goes through the shared resilient HTTP layer. ``solders`` is used for keys
and transaction signing (see chain/wallet.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import config

from ..http import ApiError, HttpService, RateLimiter
from ..util import si

log = logging.getLogger("crew.rpc")


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"RPC {code}: {message}")
        self.code = code
        self.message = message


@dataclass
class MintInfo:
    mint: str
    decimals: int
    supply: int
    program: str
    mint_authority: Optional[str]
    freeze_authority: Optional[str]
    extensions: list[str] = field(default_factory=list)
    extension_states: dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenBalance:
    mint: str
    raw: int
    decimals: int

    @property
    def ui(self) -> float:
        return self.raw / (10 ** self.decimals) if self.decimals >= 0 else 0.0


class SolanaRPC:
    def __init__(self, url: Optional[str] = None, svc: Optional[HttpService] = None) -> None:
        self.url = url or config.SOLANA_RPC_URL
        self.svc = svc or HttpService(
            "solana-rpc", self.url, limiter=RateLimiter(max_calls=40, window=10.0, min_interval=0.05), timeout=15.0, retries=2
        )
        self._id = 0

    async def call(self, method: str, params: Optional[list[Any]] = None, *, retries: Optional[int] = None) -> Any:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
        data = await self.svc.post_json("", json=payload, retries=retries)
        if not isinstance(data, dict):
            raise ApiError("solana-rpc", "unexpected RPC response")
        if "error" in data and data["error"]:
            err = data["error"]
            raise RpcError(si(err.get("code")), str(err.get("message", err))[:300])
        return data.get("result")

    # ── account / balance helpers ───────────────────────────────────────
    async def get_balance_sol(self, pubkey: str) -> float:
        res = await self.call("getBalance", [pubkey, {"commitment": "confirmed"}])
        return si((res or {}).get("value")) / 1e9

    async def get_block_height(self) -> int:
        return si(await self.call("getBlockHeight", [{"commitment": "confirmed"}]))

    async def get_mint_info(self, mint: str) -> MintInfo:
        res = await self.call("getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        value = (res or {}).get("value")
        if not value:
            raise RpcError(-1, "Mint-Account nicht gefunden")
        data = value.get("data") or {}
        parsed = data.get("parsed") if isinstance(data, dict) else None
        if not parsed or parsed.get("type") != "mint":
            raise RpcError(-2, "Account ist kein SPL-Token-Mint")
        info = parsed.get("info") or {}
        exts = info.get("extensions") or []
        ext_names = [str(e.get("extension")) for e in exts if isinstance(e, dict) and e.get("extension")]
        states = {str(e.get("extension")): e.get("state") for e in exts if isinstance(e, dict)}
        return MintInfo(
            mint=mint,
            decimals=si(info.get("decimals")),
            supply=si(info.get("supply")),
            program=str(value.get("owner") or ""),
            mint_authority=info.get("mintAuthority") or None,
            freeze_authority=info.get("freezeAuthority") or None,
            extensions=ext_names,
            extension_states=states,
        )

    async def get_token_balances(self, owner: str) -> dict[str, TokenBalance]:
        """All SPL balances of a wallet (Token + Token-2022 programs), summed per mint."""
        out: dict[str, TokenBalance] = {}
        for program in (config.TOKEN_PROGRAM, config.TOKEN_2022_PROGRAM):
            res = await self.call(
                "getTokenAccountsByOwner",
                [owner, {"programId": program}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
            )
            for acc in (res or {}).get("value", []):
                try:
                    info = acc["account"]["data"]["parsed"]["info"]
                    mint = info["mint"]
                    ta = info["tokenAmount"]
                    raw, dec = int(ta["amount"]), int(ta["decimals"])
                except (KeyError, TypeError, ValueError):
                    continue
                if mint in out:
                    out[mint].raw += raw
                else:
                    out[mint] = TokenBalance(mint, raw, dec)
        return out

    async def get_token_balance(self, owner: str, mint: str) -> Optional[TokenBalance]:
        return (await self.get_token_balances(owner)).get(mint)

    async def get_signature_statuses(self, sigs: list[str]) -> list[Optional[dict[str, Any]]]:
        res = await self.call("getSignatureStatuses", [sigs, {"searchTransactionHistory": True}])
        return list((res or {}).get("value", []))

    async def simulate(self, tx_b64: str, *, sig_verify: bool = False) -> dict[str, Any]:
        res = await self.call(
            "simulateTransaction",
            [tx_b64, {"encoding": "base64", "commitment": "processed", "sigVerify": sig_verify,
                      "replaceRecentBlockhash": not sig_verify}],
        )
        return (res or {}).get("value", {})

    async def healthy(self) -> bool:
        try:
            return (await self.call("getHealth", retries=0)) == "ok"
        except Exception:
            return False
