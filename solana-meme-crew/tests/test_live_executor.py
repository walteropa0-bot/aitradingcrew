"""LiveExecutor against a fake Jupiter Swap-V2 API and a fake Solana RPC.
This exercises the real order -> guard -> sign -> execute -> fill pipeline."""
import base64

import httpx
import pytest
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

import config
from crew.chain.wallet import Wallet
from crew.models import Position
from crew.trading.executor import NO_BALANCE, BuyRequest, ExecutorError, LiveExecutor, SellRequest

from .fakes import LAMPORTS, MINT, FakeJupiter, FakeRpc


def make(balance=5 * LAMPORTS):
    kp = Keypair()
    wallet = Wallet(kp)
    rpc = FakeRpc(wallet.address, balance)
    jup = FakeJupiter(kp.pubkey())
    ex = LiveExecutor(wallet, rpc.client(), jup.client())
    ex.POLL_SEC = 0.001
    ex.RETRY_PAUSE_SEC = 0.001
    return ex, rpc, jup, kp


def buy_req(sol=0.5, max_impact=4.0, slip=500):
    return BuyRequest(mint=MINT, symbol="MEME", decimals=6, sol_amount=sol, slippage_bps=slip, ref_price_sol=1e-6,
                      liquidity_usd=50_000, sol_usd=90.0, max_impact_pct=max_impact)


def sell_req(qty=10.0, full=True, panic=False):
    pos = Position(id="p1", mint=MINT, symbol="MEME", decimals=6, qty=qty, last_price_sol=1e-6)
    return SellRequest(position=pos, qty=qty, full=full, slippage_bps=1200, panic=panic, reason="Stop-Loss",
                       ref_price_sol=1e-6, liquidity_usd=50_000, sol_usd=90.0)


async def test_prepare_ok_and_refuses_low_balance():
    ex, rpc, jup, _ = make()
    notes = await ex.prepare()
    assert any("Jupiter" in n for n in notes)
    ex2, _, _, _ = make(balance=int(0.05 * LAMPORTS))
    with pytest.raises(ExecutorError) as ei:
        await ex2.prepare()
    assert "mindestens" in str(ei.value)


async def test_buy_success_builds_fill_from_execute_response():
    ex, rpc, jup, kp = make()
    fill = await ex.buy(buy_req(0.5))
    assert fill.ok and not fill.simulated and fill.source == "jupiter"
    assert fill.sol_amount == pytest.approx(0.5)
    assert fill.token_amount == pytest.approx(15.5)                    # 500_000_000/1000*31 raw / 1e6
    assert fill.price_sol == pytest.approx(0.5 / 15.5)
    assert fill.fee_sol == pytest.approx(25_000 / LAMPORTS)            # signature + priority fee
    # exactly one order + one execute, and the transaction we submitted carries OUR valid signature
    assert len(jup.order_calls) == 1 and len(jup.execute_calls) == 1
    sent = VersionedTransaction.from_bytes(base64.b64decode(jup.execute_calls[0]["signedTransaction"]))
    assert sent.signatures[0].verify(kp.pubkey(), to_bytes_versioned(sent.message))
    assert jup.execute_calls[0]["requestId"] == "req-0"
    # the order asked for the right swap with our slippage cap
    o = jup.order_calls[0]
    assert o["inputMint"] == config.WSOL_MINT and o["outputMint"] == MINT and o["amount"] == str(500_000_000)
    assert o["slippageBps"] == "500" and o["taker"] == ex.wallet_address


async def test_guard_stops_a_transaction_that_would_drain_the_wallet():
    ex, rpc, jup, _ = make()
    rpc.sim_post_lamports = lambda pre: pre - 4 * LAMPORTS            # simulation: wallet would lose 4 SOL on a 0.5 SOL buy
    fill = await ex.buy(buy_req(0.5))
    assert not fill.ok and "Sicherheitsprüfung" in fill.error
    assert jup.execute_calls == []                                      # NEVER sent


async def test_simulation_error_aborts_the_trade():
    ex, rpc, jup, _ = make()
    rpc.sim_error = {"InstructionError": [3, {"Custom": 6001}]}
    fill = await ex.buy(buy_req())
    assert not fill.ok and jup.execute_calls == []


async def test_buy_is_aborted_when_the_safety_simulation_is_unavailable():
    ex, rpc, jup, _ = make()
    rpc.fail_methods = {"simulateTransaction"}
    fill = await ex.buy(buy_req())
    assert not fill.ok and "Simulation" in fill.error and jup.execute_calls == []


async def test_sell_still_goes_through_when_only_the_simulation_is_down():
    ex, rpc, jup, _ = make()
    rpc.token_raw = 10_000_000
    rpc.fail_methods = {"simulateTransaction"}
    fill = await ex.sell(sell_req(10.0))
    assert fill.ok and len(jup.execute_calls) == 1                      # exits have priority over the extra check


async def test_price_impact_limit_rejects_the_entry():
    ex, rpc, jup, _ = make()
    jup.price_impact = -9.0
    fill = await ex.buy(buy_req(max_impact=4.0))
    assert not fill.ok and "Price-Impact" in fill.error and jup.execute_calls == []


async def test_insufficient_funds_is_reported_not_executed():
    ex, rpc, jup, _ = make()
    jup.error_code = 1
    fill = await ex.buy(buy_req())
    assert not fill.ok and "Insufficient" in fill.error and jup.execute_calls == []


async def test_expired_transaction_is_retried_with_a_fresh_quote():
    ex, rpc, jup, _ = make()
    jup.execute_queue = [{"status": "Failed", "code": -1005, "error": "Transaction expired", "signature": ""}, None]
    fill = await ex.buy(buy_req())
    assert fill.ok
    assert len(jup.order_calls) == 2 and len(jup.execute_calls) == 2
    assert jup.execute_calls[1]["requestId"] == "req-1"                 # a NEW order, not a re-send of the old one


async def test_non_retryable_failure_is_not_retried():
    ex, rpc, jup, _ = make()
    jup.execute_queue = [{"status": "Failed", "code": -1002, "error": "Invalid transaction", "signature": ""}]
    fill = await ex.buy(buy_req())
    assert not fill.ok and "Invalid" in fill.error and len(jup.execute_calls) == 1


async def test_unclear_execute_result_never_causes_a_second_buy(monkeypatch):
    ex, rpc, jup, _ = make()
    jup.execute_queue = [httpx.ReadTimeout("slow")]
    async def unknown(*a, **k):
        return None
    monkeypatch.setattr(ex, "_wait_for_signature", unknown)
    fill = await ex.buy(buy_req())
    assert not fill.ok and "unklar" in fill.error
    assert len(jup.order_calls) == 1 and len(jup.execute_calls) == 1


async def test_timeout_but_landed_on_chain_is_accepted_without_resending():
    ex, rpc, jup, _ = make()
    jup.execute_queue = [httpx.ReadTimeout("slow")]
    # the signature is our own (fee payer) -> RPC reports it as confirmed
    orig = jup.order_body
    def order_body(params):
        body = orig(params)
        return body
    class Always:
        def get(self, k):
            return {"confirmationStatus": "confirmed", "err": None}
    rpc.sig_status = type("D", (dict,), {"get": lambda self, k, d=None: {"confirmationStatus": "confirmed", "err": None}})()
    fill = await ex.buy(buy_req())
    assert fill.ok and fill.source == "jupiter-est"
    assert len(jup.execute_calls) == 1


async def test_blockhash_expired_without_trace_allows_one_clean_retry():
    ex, rpc, jup, _ = make()
    jup.execute_queue = [httpx.ReadTimeout("slow"), None]
    rpc.block_height = 5000                                              # > lastValidBlockHeight(2000): the first tx can never land
    fill = await ex.buy(buy_req())
    assert fill.ok and len(jup.execute_calls) == 2


async def test_sell_without_token_balance_reports_no_balance():
    ex, rpc, jup, _ = make()
    rpc.token_raw = 0
    fill = await ex.sell(sell_req())
    assert not fill.ok and fill.error == NO_BALANCE and jup.order_calls == []


async def test_full_sell_uses_the_exact_onchain_balance():
    ex, rpc, jup, _ = make()
    rpc.token_raw = 12_345_678
    fill = await ex.sell(sell_req(qty=10.0, full=True))
    assert fill.ok and jup.order_calls[0]["amount"] == "12345678"
    assert jup.order_calls[0]["inputMint"] == MINT and jup.order_calls[0]["outputMint"] == config.WSOL_MINT
    assert fill.token_amount == pytest.approx(12.345678) and fill.sol_amount > 0


async def test_partial_sell_is_capped_by_the_wallet_balance():
    ex, rpc, jup, _ = make()
    rpc.token_raw = 4_000_000
    fill = await ex.sell(sell_req(qty=10.0, full=False))
    assert fill.ok and jup.order_calls[0]["amount"] == "4000000"


async def test_profit_sweep_to_usdc_swaps_sol_into_usdc():
    ex, rpc, jup, _ = make()
    fill = await ex.sweep_usdc(0.2)
    assert fill.ok and fill.mint == config.USDC_MINT
    assert jup.order_calls[0]["outputMint"] == config.USDC_MINT and jup.order_calls[0]["amount"] == str(200_000_000)
    assert fill.sol_amount == pytest.approx(0.2) and fill.token_amount > 0


async def test_the_executor_has_no_way_to_send_funds_elsewhere():
    """The only network write paths are Jupiter's /execute (swap tx) - there is no transfer builder."""
    import inspect
    from crew.chain import wallet as w
    from crew.trading import executor as e
    src = inspect.getsource(w) + inspect.getsource(e)
    for forbidden in ("TransferParams", "system_program", "SystemProgram", "sendTransaction", "transfer("):
        assert forbidden not in src, forbidden
