"""Order execution: PaperExecutor (simulated, pessimistic) and LiveExecutor
(real swaps through Jupiter Swap V2, signed locally with solders).

LiveExecutor safety layers
  1. Only swaps built by Jupiter are signed; there is no code path that creates
     transfers to other addresses.
  2. Before signing is used, the transaction is *simulated* on the RPC and the
     wallet's SOL outflow must not exceed input + fees + rent tolerance.
  3. Price-impact and slippage limits are enforced per trade.
  4. The result of a swap is verified (execute response, falling back to the
     chain) - an ambiguous result never triggers a blind second buy.
"""
from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import config

from ..chain.jupiter import JupiterClient, JupiterError, Order
from ..chain.wallet import Wallet, WalletError
from ..market.solana_rpc import SolanaRPC
from ..models import Fill, Position
from ..util import now, short_addr

log = logging.getLogger("crew.exec")

NO_BALANCE = "NO_BALANCE"
LAMPORTS = 1_000_000_000


class ExecutorError(Exception):
    pass


@dataclass
class BuyRequest:
    mint: str
    symbol: str
    decimals: int
    sol_amount: float
    slippage_bps: int
    ref_price_sol: float
    liquidity_usd: float
    sol_usd: float
    max_impact_pct: float


@dataclass
class SellRequest:
    position: Position
    qty: float
    full: bool
    slippage_bps: int
    panic: bool
    reason: str
    ref_price_sol: float
    liquidity_usd: float
    sol_usd: float


class Executor(ABC):
    simulated = True
    name = "executor"

    async def prepare(self) -> list[str]:
        return []

    async def cash_sol(self) -> Optional[float]:
        """Authoritative SOL balance (live) or None (paper: the ledger is the truth)."""
        return None

    @abstractmethod
    async def buy(self, req: BuyRequest) -> Fill: ...

    @abstractmethod
    async def sell(self, req: SellRequest) -> Fill: ...

    async def sweep_usdc(self, sol_amount: float) -> Optional[Fill]:
        return None

    @property
    def wallet_address(self) -> str:
        return ""


def model_impact_pct(trade_sol: float, liquidity_usd: float, sol_usd: float) -> float:
    """Constant-product estimate of the price impact in percent (pessimistic if unknown)."""
    if trade_sol <= 0:
        return 0.0
    if liquidity_usd <= 0 or sol_usd <= 0:
        return 25.0
    pool_sol_side = liquidity_usd / 2.0 / sol_usd
    return trade_sol / (pool_sol_side + trade_sol) * 100.0


# ═══════════════════════════ paper trading ═══════════════════════════════
class PaperExecutor(Executor):
    simulated = True
    name = "paper"

    def __init__(self, market, jupiter: Optional[JupiterClient] = None, rng: Optional[random.Random] = None) -> None:
        self.market = market
        self.jupiter = jupiter if not getattr(market, "is_simulated", False) else None
        self.rng = rng or random.Random()

    async def _quote(self, in_mint: str, out_mint: str, raw: int, slippage_bps: int) -> Optional[Order]:
        if self.jupiter is None or not self.jupiter.has_budget(reserve=8):
            return None
        try:
            o = await self.jupiter.order(in_mint, out_mint, raw, slippage_bps=slippage_bps)
            return o if o.out_amount > 0 else None
        except Exception as exc:
            log.debug("paper quote failed: %s", exc)
            return None

    async def buy(self, req: BuyRequest) -> Fill:
        ref = req.ref_price_sol
        if ref <= 0 or req.sol_amount <= 0:
            return Fill(ok=False, side="BUY", mint=req.mint, symbol=req.symbol, error="Kein gültiger Preis", ts=now())
        impact = model_impact_pct(req.sol_amount, req.liquidity_usd, req.sol_usd)
        fee = config.PAPER_SWAP_FEE_PCT
        extra = config.PAPER_EXTRA_SLIPPAGE_PCT
        price = ref * (1 + (impact + fee + extra) / 100.0)
        source = "model"
        order = await self._quote(config.WSOL_MINT, req.mint, int(req.sol_amount * LAMPORTS), req.slippage_bps)
        if order is not None:
            tokens = order.out_amount / (10 ** req.decimals)
            if tokens > 0:
                price = (req.sol_amount / tokens) * (1 + extra / 100.0)   # real route incl. fee + impact
                impact = order.price_impact_pct
                source = "jupiter-quote"
        if impact > req.max_impact_pct:
            return Fill(ok=False, side="BUY", mint=req.mint, symbol=req.symbol,
                        error=f"Price-Impact {impact:.1f}% über Limit {req.max_impact_pct:.1f}%", ts=now())
        slip = (price / ref - 1.0) * 100.0
        if slip > req.slippage_bps / 100.0 + 1e-9:
            return Fill(ok=False, side="BUY", mint=req.mint, symbol=req.symbol,
                        error=f"Slippage {slip:.1f}% über Limit {req.slippage_bps / 100:.1f}%", ts=now())
        tokens = req.sol_amount / price
        return Fill(ok=True, side="BUY", mint=req.mint, symbol=req.symbol, sol_amount=req.sol_amount,
                    token_amount=tokens, price_sol=price, fee_sol=config.PAPER_NETWORK_FEE_SOL,
                    slippage_pct=slip, simulated=True, source=source, ts=now())

    async def sell(self, req: SellRequest) -> Fill:
        pos = req.position
        ref = req.ref_price_sol if req.ref_price_sol > 0 else pos.last_price_sol
        if ref <= 0 or req.qty <= 0:
            return Fill(ok=False, side="SELL", mint=pos.mint, symbol=pos.symbol, error="Kein gültiger Preis", ts=now())
        value = req.qty * ref
        impact = model_impact_pct(value, req.liquidity_usd, req.sol_usd)
        extra = config.PAPER_EXTRA_SLIPPAGE_PCT
        price = ref * max(0.005, 1 - (impact + config.PAPER_SWAP_FEE_PCT + extra) / 100.0)
        source = "model"
        order = await self._quote(pos.mint, config.WSOL_MINT, int(req.qty * (10 ** pos.decimals)), req.slippage_bps)
        if order is not None and order.out_amount > 0:
            price = (order.out_amount / LAMPORTS) / req.qty * (1 - extra / 100.0)
            source = "jupiter-quote"
        slip = (1.0 - price / ref) * 100.0
        if slip > req.slippage_bps / 100.0 + 1e-9 and not req.panic:
            return Fill(ok=False, side="SELL", mint=pos.mint, symbol=pos.symbol,
                        error=f"Slippage {slip:.1f}% über Limit {req.slippage_bps / 100:.1f}%", ts=now())
        proceeds = req.qty * price
        return Fill(ok=True, side="SELL", mint=pos.mint, symbol=pos.symbol, sol_amount=proceeds,
                    token_amount=req.qty, price_sol=price, fee_sol=config.PAPER_NETWORK_FEE_SOL,
                    slippage_pct=slip, simulated=True, source=source, ts=now())

    async def sweep_usdc(self, sol_amount: float) -> Optional[Fill]:
        sol_usd = await self.market.sol_usd()
        if sol_usd <= 0:
            return None                       # price unknown -> keep the profit in SOL
        usd = sol_amount * sol_usd
        return Fill(ok=True, side="SWEEP", mint=config.USDC_MINT, symbol="USDC", sol_amount=sol_amount,
                    token_amount=usd * (1 - config.PAPER_SWAP_FEE_PCT / 100.0), simulated=True, source="model", ts=now())


# ═══════════════════════════ live trading ════════════════════════════════
class GuardError(Exception):
    pass


class LiveExecutor(Executor):
    simulated = False
    name = "live"
    FEE_TOLERANCE_LAMPORTS = 15_000_000        # 0.015 SOL: fees + ATA rent headroom
    POLL_SEC = 2.0                             # signature status polling interval
    RETRY_PAUSE_SEC = 0.8                      # pause before a retry with a fresh quote

    def __init__(self, wallet: Wallet, rpc: SolanaRPC, jupiter: JupiterClient) -> None:
        self.wallet = wallet
        self.rpc = rpc
        self.jupiter = jupiter

    @property
    def wallet_address(self) -> str:
        return self.wallet.address

    async def prepare(self) -> list[str]:
        if not await self.rpc.healthy():
            raise ExecutorError("Solana-RPC nicht erreichbar. Prüfe SOLANA_RPC_URL (am besten eigener Helius-/QuickNode-Key).")
        bal = await self.rpc.get_balance_sol(self.wallet.address)
        if bal < config.LIVE_MIN_SOL_TO_START:
            raise ExecutorError(f"Die Wallet {short_addr(self.wallet.address)} hat nur {bal:.3f} SOL – mindestens "
                                f"{config.LIVE_MIN_SOL_TO_START} SOL werden benötigt (Handel + Gebühren).")
        try:
            o = await self.jupiter.order(config.WSOL_MINT, config.USDC_MINT, 1_000_000)
        except JupiterError as exc:
            raise ExecutorError(f"Jupiter nicht nutzbar: {exc.message}") from exc
        if o.out_amount <= 0:
            raise ExecutorError("Jupiter lieferte keine gültige Quote.")
        return [f"Wallet {short_addr(self.wallet.address)} · {bal:.3f} SOL", "Solana-RPC ✓", "Jupiter-Swap-API ✓"]

    async def cash_sol(self) -> Optional[float]:
        return await self.rpc.get_balance_sol(self.wallet.address)

    # ── public API ──────────────────────────────────────────────────────
    async def buy(self, req: BuyRequest) -> Fill:
        raw_in = int(round(req.sol_amount * LAMPORTS))
        return await self._swap(
            side="BUY", in_mint=config.WSOL_MINT, out_mint=req.mint, amount_raw=raw_in,
            slippage_bps=req.slippage_bps, decimals=req.decimals, mint=req.mint, symbol=req.symbol,
            max_impact_pct=req.max_impact_pct, ref_price=req.ref_price_sol,
        )

    async def sell(self, req: SellRequest) -> Fill:
        pos = req.position
        try:
            bal = await self.rpc.get_token_balance(self.wallet.address, pos.mint)
        except Exception as exc:
            return Fill(ok=False, side="SELL", mint=pos.mint, symbol=pos.symbol,
                        error=f"Token-Guthaben nicht lesbar: {exc}", ts=now())
        if bal is None or bal.raw <= 0:
            return Fill(ok=False, side="SELL", mint=pos.mint, symbol=pos.symbol, error=NO_BALANCE, ts=now())
        raw = bal.raw if req.full else min(bal.raw, int(req.qty * (10 ** bal.decimals)))
        if raw <= 0:
            return Fill(ok=False, side="SELL", mint=pos.mint, symbol=pos.symbol, error="Menge zu klein", ts=now())
        return await self._swap(
            side="SELL", in_mint=pos.mint, out_mint=config.WSOL_MINT, amount_raw=raw,
            slippage_bps=req.slippage_bps, decimals=bal.decimals, mint=pos.mint, symbol=pos.symbol,
            max_impact_pct=100.0 if req.panic else 35.0, ref_price=req.ref_price_sol,
        )

    async def sweep_usdc(self, sol_amount: float) -> Optional[Fill]:
        raw_in = int(round(sol_amount * LAMPORTS))
        fill = await self._swap(side="SWEEP", in_mint=config.WSOL_MINT, out_mint=config.USDC_MINT, amount_raw=raw_in,
                                slippage_bps=100, decimals=6, mint=config.USDC_MINT, symbol="USDC",
                                max_impact_pct=2.0, ref_price=0.0)
        return fill

    # ── internals ───────────────────────────────────────────────────────
    async def _swap(self, *, side: str, in_mint: str, out_mint: str, amount_raw: int, slippage_bps: int,
                    decimals: int, mint: str, symbol: str, max_impact_pct: float, ref_price: float) -> Fill:
        def fail(msg: str) -> Fill:
            return Fill(ok=False, side=side, mint=mint, symbol=symbol, error=msg, simulated=False, source="jupiter", ts=now())

        last_error = "unbekannter Fehler"
        for attempt in range(2):
            try:
                order = await self.jupiter.order(
                    in_mint, out_mint, amount_raw, taker=self.wallet.address,
                    slippage_bps=slippage_bps, priority_fee_lamports=config.PRIORITY_FEE_LAMPORTS,
                )
            except JupiterError as exc:
                last_error = exc.message
                if exc.retryable and attempt == 0:
                    await asyncio.sleep(self.RETRY_PAUSE_SEC)
                    continue
                return fail(last_error)
            if not order.can_sign:
                return fail(order.error_message or f"Keine Route (Fehlercode {order.error_code})")
            if side == "BUY" and order.price_impact_pct > max_impact_pct:
                return fail(f"Price-Impact {order.price_impact_pct:.1f}% über Limit {max_impact_pct:.1f}%")
            if order.out_amount <= 0:
                return fail("Jupiter-Quote ohne Ausgabemenge")

            try:
                signed = self.wallet.partial_sign(order.transaction or "")
            except WalletError as exc:
                return fail(str(exc))
            try:
                await self._guard(signed.b64, side, amount_raw, order)
            except GuardError as exc:
                log.error("Sicherheitsprüfung hat die Transaktion gestoppt: %s", exc)
                return fail(f"Sicherheitsprüfung: {exc}")

            result = None
            try:
                result = await self.jupiter.execute(signed.b64, order.request_id, order.last_valid_block_height)
            except Exception as exc:
                log.warning("execute-Aufruf unklar (%s) – prüfe On-Chain-Status", exc)
                landed = await self._wait_for_signature(signed.signature, order.last_valid_block_height)
                if landed is True:
                    return self._fill_from_order(side, order, decimals, mint, symbol, signed.signature, "Bestätigung per RPC")
                if landed is None:   # still unknown -> never risk a duplicate swap
                    return fail("Ergebnis des Swaps unklar – bitte Wallet prüfen (kein zweiter Versuch)")
                last_error = "Transaktion nicht gelandet"
                if attempt == 0:
                    continue
                return fail(last_error)

            if result.ok:
                return self._fill_from_result(side, order, result, decimals, mint, symbol)
            last_error = result.error or f"Swap fehlgeschlagen (Code {result.code})"
            if result.signature:
                landed = await self._wait_for_signature(result.signature, order.last_valid_block_height, max_wait=6.0)
                if landed is True:
                    return self._fill_from_order(side, order, decimals, mint, symbol, result.signature, "On-Chain bestätigt")
            retryable = result.code in (-1000, -1001, -1005) or "expired" in last_error.lower() or "blockhash" in last_error.lower()
            if retryable and attempt == 0:
                log.info("Swap nicht gelandet (%s) – neuer Versuch mit frischer Quote", last_error)
                await asyncio.sleep(self.RETRY_PAUSE_SEC)
                continue
            return fail(last_error)
        return fail(last_error)

    async def _guard(self, tx_b64: str, side: str, amount_raw: int, order: Order) -> None:
        """Simulate the exact transaction; the wallet must not lose more SOL than
        input + fees + tolerance. Protects against any malformed/malicious tx."""
        try:
            pre = await self.rpc.call("getBalance", [self.wallet.address, {"commitment": "processed"}])
            pre_lamports = int((pre or {}).get("value", 0))
            sim = await self.rpc.call("simulateTransaction", [tx_b64, {
                "encoding": "base64", "commitment": "processed", "sigVerify": False, "replaceRecentBlockhash": True,
                "accounts": {"encoding": "base64", "addresses": [self.wallet.address]},
            }])
        except Exception as exc:
            if side == "BUY":
                raise GuardError(f"Simulation nicht möglich ({exc}) – Kauf aus Sicherheitsgründen abgebrochen")
            log.warning("Simulation vor Verkauf nicht möglich (%s) – Verkauf läuft trotzdem (Ausstieg hat Vorrang)", exc)
            return
        value = (sim or {}).get("value") or {}
        if value.get("err"):
            logs = " | ".join((value.get("logs") or [])[-3:])
            raise GuardError(f"Simulation meldet Fehler {value['err']} {logs[:160]}")
        accounts = value.get("accounts") or []
        if not accounts or not accounts[0]:
            return
        post_lamports = int(accounts[0].get("lamports", pre_lamports))
        spent = pre_lamports - post_lamports
        allowed = (amount_raw if side in ("BUY", "SWEEP") else 0) + order.network_fee_lamports + self.FEE_TOLERANCE_LAMPORTS
        if spent > allowed:
            raise GuardError(f"SOL-Abfluss {spent / LAMPORTS:.4f} übersteigt erlaubte {allowed / LAMPORTS:.4f} SOL")

    async def _wait_for_signature(self, signature: str, last_valid_height: int, max_wait: float = 75.0) -> Optional[bool]:
        """True = landed OK, False = definitely not landed (blockhash expired), None = unknown."""
        if not signature:
            return None
        t0 = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - t0 < max_wait:
            try:
                st = (await self.rpc.get_signature_statuses([signature]))[0]
                if st:
                    if st.get("err"):
                        return False
                    if st.get("confirmationStatus") in ("confirmed", "finalized"):
                        return True
                elif last_valid_height:
                    if await self.rpc.get_block_height() > last_valid_height:
                        return False
            except Exception as exc:
                log.debug("signature poll failed: %s", exc)
            await asyncio.sleep(self.POLL_SEC)
        return None

    # ── fill builders ───────────────────────────────────────────────────
    def _fill_from_result(self, side: str, order: Order, res, decimals: int, mint: str, symbol: str) -> Fill:
        total_in = res.total_in or order.in_amount
        total_out = res.total_out or order.out_amount
        fee_sol = order.network_fee_lamports / LAMPORTS
        return self._build_fill(side, total_in, total_out, decimals, mint, symbol, res.signature, fee_sol, "jupiter")

    def _fill_from_order(self, side: str, order: Order, decimals: int, mint: str, symbol: str, sig: str, note: str) -> Fill:
        f = self._build_fill(side, order.in_amount, order.out_amount, decimals, mint, symbol, sig,
                             order.network_fee_lamports / LAMPORTS, "jupiter-est")
        log.info("Fill aus Quote geschätzt (%s)", note)
        return f

    @staticmethod
    def _build_fill(side: str, total_in: int, total_out: int, decimals: int, mint: str, symbol: str,
                    sig: str, fee_sol: float, source: str) -> Fill:
        if side == "BUY":
            sol, tokens = total_in / LAMPORTS, total_out / (10 ** decimals)
        elif side == "SELL":
            tokens, sol = total_in / (10 ** decimals), total_out / LAMPORTS
        else:  # SWEEP: SOL -> USDC
            sol, tokens = total_in / LAMPORTS, total_out / 1e6
        price = (sol / tokens) if (tokens > 0 and side in ("BUY", "SELL")) else 0.0
        return Fill(ok=True, side=side, mint=mint, symbol=symbol, sol_amount=sol, token_amount=tokens, price_sol=price,
                    fee_sol=fee_sol, signature=sig, simulated=False, source=source, ts=now())
