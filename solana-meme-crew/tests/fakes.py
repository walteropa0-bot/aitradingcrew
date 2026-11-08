"""Test doubles: a fake Solana JSON-RPC node and a fake Jupiter Swap V2 API, both
served through httpx.MockTransport so the real client code paths are exercised."""
from __future__ import annotations

import base64
import json
from typing import Any, Callable, Optional

import httpx
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

import config
from crew.chain.jupiter import JupiterClient
from crew.http import HttpService
from crew.market.solana_rpc import SolanaRPC

LAMPORTS = 1_000_000_000
MINT = "MemeMint1111111111111111111111111111111111pump"


def unsigned_tx_b64(payer: Pubkey, extra_signer: Optional[Pubkey] = None, lamports: int = 1) -> str:
    """A v0 transaction with an empty signature slot for `payer` (like Jupiter returns it)."""
    ix = transfer(TransferParams(from_pubkey=payer, to_pubkey=Pubkey.new_unique(), lamports=lamports))
    msg = MessageV0.try_compile(payer, [ix], [], Hash.default())
    n = msg.header.num_required_signatures
    tx = VersionedTransaction.populate(msg, [Signature.default()] * n)
    return base64.b64encode(bytes(tx)).decode()


class FakeRpc:
    def __init__(self, wallet: str, balance_lamports: int = 5 * LAMPORTS) -> None:
        self.wallet = wallet
        self.balance = balance_lamports
        self.token_raw = 0
        self.token_decimals = 6
        self.mint = MINT
        self.sim_post_lamports: Optional[Callable[[int], int]] = None     # pre -> post
        self.sim_error: Any = None
        self.sig_status: dict[str, dict] = {}
        self.block_height = 1000
        self.fail_methods: set[str] = set()
        self.calls: list[str] = []

    def result(self, method: str, params: list) -> Any:
        if method == "getHealth":
            return "ok"
        if method == "getBalance":
            return {"context": {"slot": 1}, "value": self.balance}
        if method == "getBlockHeight":
            return self.block_height
        if method == "getTokenAccountsByOwner":
            if params[1].get("programId") != config.TOKEN_PROGRAM or not self.token_raw:
                return {"context": {"slot": 1}, "value": []}
            return {"context": {"slot": 1}, "value": [{"pubkey": "TokAcc", "account": {"data": {"parsed": {"info": {
                "mint": self.mint, "tokenAmount": {"amount": str(self.token_raw), "decimals": self.token_decimals}}}}}}]}
        if method == "simulateTransaction":
            post = self.sim_post_lamports(self.balance) if self.sim_post_lamports else self.balance - 20_000
            return {"context": {"slot": 1}, "value": {"err": self.sim_error, "logs": ["log1", "log2"],
                                                       "accounts": [{"lamports": post, "owner": "111", "data": ["", "base64"]}]}}
        if method == "getSignatureStatuses":
            return {"context": {"slot": 1}, "value": [self.sig_status.get(s) for s in params[0]]}
        if method == "getAccountInfo":
            return {"context": {"slot": 1}, "value": {"owner": config.TOKEN_PROGRAM, "data": {"program": "spl-token", "parsed": {
                "type": "mint", "info": {"decimals": 6, "supply": "1000", "mintAuthority": None, "freezeAuthority": None}}}}}
        raise AssertionError(f"unexpected rpc method {method}")

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        self.calls.append(method)
        if method in self.fail_methods:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32005, "message": "node is behind"}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": self.result(method, body["params"])})

    def client(self) -> SolanaRPC:
        svc = HttpService("solana-rpc", "https://rpc.test", transport=httpx.MockTransport(self.handler), limiter=None, retries=0)
        return SolanaRPC(url="https://rpc.test", svc=svc)


class FakeJupiter:
    def __init__(self, wallet_pubkey: Pubkey) -> None:
        self.wallet = wallet_pubkey
        self.order_calls: list[dict] = []
        self.execute_calls: list[dict] = []
        self.order_override: Optional[Callable[[dict], tuple[int, dict]]] = None
        self.execute_queue: list[Any] = []            # dicts (200 body) or Exception instances
        self.price_impact = -0.4
        self.in_amount_factor = 1.0
        self.out_per_in = 3_000_000                    # raw out per lamport-ish (arbitrary)
        self.error_code: Optional[int] = None

    def order_body(self, params: dict) -> dict:
        amount = int(params["amount"])
        body = {
            "requestId": f"req-{len(self.order_calls) - 1}", "inputMint": params["inputMint"], "outputMint": params["outputMint"],
            "inAmount": str(amount), "outAmount": str(int(amount / 1000 * 31)), "otherAmountThreshold": "1", "slippageBps": int(params.get("slippageBps", 50)),
            "priceImpact": self.price_impact, "priceImpactPct": str(self.price_impact / 100), "feeBps": 10, "router": "metis",
            "signatureFeeLamports": 5000, "prioritizationFeeLamports": 20000, "rentFeeLamports": 0, "gasless": False,
            "lastValidBlockHeight": "2000", "transaction": None, "taker": params.get("taker"), "routePlan": [],
        }
        if params.get("taker"):
            if self.error_code is not None:
                body.update(transaction="", errorCode=self.error_code, errorMessage="Insufficient funds", error="Insufficient funds")
            else:
                body["transaction"] = unsigned_tx_b64(Pubkey.from_string(params["taker"]))
        return body

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/swap/v2/order"):
            params = dict(request.url.params)
            self.order_calls.append(params)
            if self.order_override:
                status, body = self.order_override(params)
                return httpx.Response(status, json=body)
            return httpx.Response(200, json=self.order_body(params))
        if path.endswith("/swap/v2/execute"):
            payload = json.loads(request.content)
            self.execute_calls.append(payload)
            item = self.execute_queue.pop(0) if self.execute_queue else None
            if isinstance(item, Exception):
                raise item
            if item is None:
                o = self.order_calls[-1]
                amount = int(o["amount"])
                item = {"status": "Success", "signature": "5" * 87, "code": 0, "totalInputAmount": str(amount),
                        "totalOutputAmount": str(int(amount / 1000 * 31)), "slot": "123"}
            return httpx.Response(200, json=item)
        if path.endswith("/price/v3"):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": "not found"})

    def client(self) -> JupiterClient:
        svc = HttpService("jupiter", "https://api.jup.ag", transport=httpx.MockTransport(self.handler), limiter=None, retries=0)
        return JupiterClient(api_key="", svc=svc)


def new_keypair() -> Keypair:
    return Keypair()
