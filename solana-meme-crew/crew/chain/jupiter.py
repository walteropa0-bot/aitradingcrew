"""Jupiter *Swap API V2* client  (https://api.jup.ag/swap/v2).

Flow (verified against the official OpenAPI spec, Nov 2026):
    GET  /swap/v2/order?inputMint&outputMint&amount&taker&slippageBps -> quote + base64 tx + requestId
    (sign the tx locally - partial signature)
    POST /swap/v2/execute {signedTransaction, requestId}            -> managed landing + result
    GET  /price/v3?ids=a,b,c                                         -> USD prices (<=50)

Auth: optional header ``x-api-key`` (free key from developers.jup.ag/portal).
Keyless access is limited to ~0.5 req/s, a free key doubles that.
Note: /order charges Jupiter's platform fee (feeBps, ~10 bps) - it is part of the
amounts returned by /execute, so the ledger sees the real cost.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

import config

from ..http import ApiError, HttpService, RateLimiter
from ..util import sf, si

log = logging.getLogger("crew.jupiter")


class JupiterError(Exception):
    def __init__(self, message: str, *, code: Optional[int] = None, status: int = 0, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.retryable = retryable


@dataclass
class Order:
    request_id: str = ""
    in_mint: str = ""
    out_mint: str = ""
    in_amount: int = 0
    out_amount: int = 0
    min_out_amount: int = 0
    slippage_bps: int = 0
    price_impact_pct: float = 0.0     # positive number = percent lost to impact
    fee_bps: int = 0
    router: str = ""
    transaction: Optional[str] = None  # None: no taker | "": could not build | base64
    last_valid_block_height: int = 0
    gasless: bool = False
    signature_fee_payer: Optional[str] = None
    network_fee_lamports: int = 0     # signature + priority + rent (estimate)
    rent_lamports: int = 0
    error_code: Optional[int] = None
    error_message: str = ""
    in_usd: float = 0.0
    out_usd: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def can_sign(self) -> bool:
        return bool(self.transaction) and self.error_code is None


@dataclass
class ExecResult:
    status: str = ""                 # Success | Failed
    signature: str = ""
    code: int = 0
    error: str = ""
    total_in: int = 0                # raw units deducted from the wallet (incl. fee in input mint)
    total_out: int = 0               # raw units that arrived in the wallet
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "Success" and self.code == 0


def parse_order(data: dict[str, Any]) -> Order:
    impact = data.get("priceImpact")
    if impact is None:                         # deprecated ratio string, e.g. "-0.00085"
        impact = sf(data.get("priceImpactPct")) * 100.0
    ec = data.get("errorCode")
    return Order(
        request_id=str(data.get("requestId") or ""),
        in_mint=str(data.get("inputMint") or ""),
        out_mint=str(data.get("outputMint") or ""),
        in_amount=si(data.get("inAmount")),
        out_amount=si(data.get("outAmount")),
        min_out_amount=si(data.get("otherAmountThreshold")),
        slippage_bps=si(data.get("slippageBps")),
        price_impact_pct=abs(sf(impact)),
        fee_bps=si(data.get("feeBps")),
        router=str(data.get("router") or ""),
        transaction=data.get("transaction"),
        last_valid_block_height=si(data.get("lastValidBlockHeight")),
        gasless=bool(data.get("gasless")),
        signature_fee_payer=data.get("signatureFeePayer"),
        network_fee_lamports=si(data.get("signatureFeeLamports")) + si(data.get("prioritizationFeeLamports")) + si(data.get("rentFeeLamports")),
        rent_lamports=si(data.get("rentFeeLamports")),
        error_code=None if ec is None else si(ec),
        error_message=str(data.get("errorMessage") or data.get("error") or ""),
        in_usd=sf(data.get("inUsdValue")),
        out_usd=sf(data.get("outUsdValue")),
        raw=data,
    )


class JupiterClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, svc: Optional[HttpService] = None) -> None:
        self.api_key = config.JUPITER_API_KEY if api_key is None else api_key
        base = (base_url or config.JUPITER_BASE_URL).rstrip("/")
        if svc is None:
            if self.api_key:   # free key: 1 rps  -> stay safely below
                limiter = RateLimiter(max_calls=26, window=30.0, min_interval=0.6)
            else:              # keyless: 0.5 rps
                limiter = RateLimiter(max_calls=14, window=30.0, min_interval=1.0)
            headers = {"x-api-key": self.api_key} if self.api_key else {}
            svc = HttpService("jupiter", base, headers=headers, limiter=limiter, timeout=15.0, retries=1,
                              response_hook=self._on_response)
            self._own_limiter = limiter
        else:
            self._own_limiter = svc.limiter
        self.svc = svc

    # honour Jupiter's own rate-limit headers (sent on 200 and 429)
    def _on_response(self, resp: httpx.Response) -> None:
        lim = self._own_limiter
        if lim is None:
            return
        rem, reset = resp.headers.get("x-ratelimit-remaining"), resp.headers.get("x-ratelimit-reset")
        if rem is None or reset is None:
            return
        try:
            if int(rem) <= 0:
                lim.penalize(max(0.3, min(float(reset) - time.time(), 30.0)))
        except ValueError:
            pass

    def has_budget(self, reserve: int) -> bool:
        return self._own_limiter is None or self._own_limiter.free_slots() > reserve

    # ── /order ──────────────────────────────────────────────────────────
    async def order(
        self,
        input_mint: str,
        output_mint: str,
        amount_raw: int,
        *,
        taker: Optional[str] = None,
        slippage_bps: Optional[int] = None,
        priority_fee_lamports: int = 0,
    ) -> Order:
        params: dict[str, Any] = {"inputMint": input_mint, "outputMint": output_mint, "amount": str(int(amount_raw))}
        if taker:
            params["taker"] = taker
        if slippage_bps is not None:
            params["slippageBps"] = int(slippage_bps)
        if priority_fee_lamports:
            params["priorityFeeLamports"] = int(priority_fee_lamports)
            params["broadcastFeeType"] = "maxCap"
        try:
            resp = await self.svc.request("GET", "/swap/v2/order", params=params, raise_on_error=False)
        except ApiError as exc:
            raise JupiterError(f"Jupiter nicht erreichbar: {exc}", retryable=True) from exc
        body = _json(resp)
        if resp.status_code == 200 and isinstance(body, dict):
            return parse_order(body)
        msg = (body or {}).get("error") if isinstance(body, dict) else None
        if resp.status_code in (401, 403):
            raise JupiterError("Jupiter lehnt den API-Key ab (JUPITER_API_KEY prüfen).", status=resp.status_code)
        if resp.status_code == 429:
            raise JupiterError("Jupiter Rate-Limit erreicht – bitte kurz warten.", status=429, retryable=True)
        raise JupiterError(str(msg or f"HTTP {resp.status_code}"), status=resp.status_code, retryable=resp.status_code >= 500)

    # ── /execute ────────────────────────────────────────────────────────
    async def execute(self, signed_tx_b64: str, request_id: str, last_valid_block_height: int = 0) -> ExecResult:
        payload: dict[str, Any] = {"signedTransaction": signed_tx_b64, "requestId": request_id}
        if last_valid_block_height:
            payload["lastValidBlockHeight"] = str(last_valid_block_height)
        # NEVER auto-retry an execute call: the outcome is checked by the executor
        resp = await self.svc.request("POST", "/swap/v2/execute", json=payload, raise_on_error=False, retries=0, timeout=45.0)
        body = _json(resp)
        if not isinstance(body, dict):
            raise JupiterError(f"Unerwartete Antwort von /execute (HTTP {resp.status_code})", status=resp.status_code, retryable=True)
        return ExecResult(
            status=str(body.get("status") or ("Failed" if resp.status_code >= 400 else "")),
            signature=str(body.get("signature") or ""),
            code=si(body.get("code")),
            error=str(body.get("error") or ""),
            total_in=si(body.get("totalInputAmount")),
            total_out=si(body.get("totalOutputAmount")),
            raw=body,
        )

    # ── /price/v3 ───────────────────────────────────────────────────────
    async def prices(self, mints: list[str], *, reserve: int = 6, block: bool = False) -> Optional[dict[str, dict[str, Any]]]:
        """USD prices (<=50 mints). By default returns None when there is no rate-limit
        headroom (price polling must never starve trade orders); ``block=True`` waits instead."""
        mints = list(dict.fromkeys(m for m in mints if m))[:50]
        if not mints:
            return {}
        lim = self._own_limiter
        if lim is not None:
            if block:
                await lim.acquire(reserve)
            elif not lim.try_acquire(reserve):
                return None
        data = await self.svc.get_json("/price/v3", params={"ids": ",".join(mints)}, pre_acquired=lim is not None)
        return data if isinstance(data, dict) else {}


def _json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None
