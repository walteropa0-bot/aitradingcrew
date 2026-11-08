"""End-to-end engine behaviour in the demo market (no network)."""
import asyncio

import pytest
from solders.keypair import Keypair

import config
from crew.chain.wallet import Wallet
from crew.engine import Engine
from crew.factory import ConfigError, build
from crew.models import Position
from crew.trading.executor import LiveExecutor
from crew.trading.portfolio import Portfolio
from crew.trading.store import FileStore

from .fakes import LAMPORTS, MINT, FakeJupiter, FakeRpc
from .helpers import drive, make_engine


async def test_paper_run_keeps_the_books_straight(tmp_path):
    sim, pf, eng = make_engine(tmp_path, seed=5)
    await eng.startup()
    await eng.start()
    await drive(sim, eng, 2.5 * 3600)
    assert pf.trades, "the crew should have traded at least once in 2.5 simulated hours"
    # accounting identity: cash == start - buys(+fees) + sells(-fees)
    flows = 10.0
    for t in pf.trades:
        flows += (-(t.sol_amount + t.fee_sol)) if t.side == "BUY" else (t.sol_amount - t.fee_sol)
    assert pf.cash_sol == pytest.approx(flows, abs=1e-9) and pf.cash_sol > 0
    # every closed round trip: pnl of its sells + cost == proceeds, and nothing negative
    assert all(p.qty > 0 and p.cost_sol >= 0 for p in pf.open_positions())
    # stops of all open positions only ever sit between the hard stop and the current price
    for p in pf.open_positions():
        assert p.stop_price_sol >= p.entry_price_sol * (1 - config.INITIAL_STOP_LOSS_PCT / 100) - 1e-15
    assert eng.last_error == ""


async def test_every_exit_has_a_reason_and_trailing_stop_protects_profit(tmp_path):
    sim, pf, eng = make_engine(tmp_path, seed=21)
    await eng.startup()
    await eng.start()
    await drive(sim, eng, 3 * 3600)
    sells = [t for t in pf.trades if t.side == "SELL"]
    assert sells and all(t.reason for t in sells)
    stops = [t for t in sells if t.reason in ("Stop-Loss", "Trailing-Stop")]
    for t in stops:                                           # a hard stop never loses (much) more than stop + slippage
        assert t.pnl_pct > -(config.INITIAL_STOP_LOSS_PCT + 15), t


async def test_panic_liquidates_everything_and_stops_the_system(tmp_path):
    sim, pf, eng = make_engine(tmp_path, seed=21)
    await eng.startup()
    await eng.start()
    await drive(sim, eng, 3 * 3600, stop_when=lambda: len(pf.open_positions()) >= 1)
    assert pf.open_positions(), "need an open position for this test"
    n = len(pf.open_positions())
    res = await eng.panic()
    assert res["ok"] and res["open_positions"] == n and not eng.hunting      # (paper sells are instant)
    for _ in range(30):
        sim.advance(2)
        await eng._position_tick()
        await asyncio.sleep(0)
        if not pf.open_positions():
            break
    assert not pf.open_positions() and not eng.panic_mode and eng.state == "stopped"
    assert any(t.reason == "NOT-AUS" for t in pf.trades if t.side == "SELL")
    assert not pf.desired_running                                 # must not auto-resume after a panic


async def test_no_new_entries_while_panic_or_paused(tmp_path):
    sim, pf, eng = make_engine(tmp_path, seed=21)
    await eng.startup()
    await eng.start()
    await eng.pause()
    n0 = len(pf.trades)
    await drive(sim, eng, 1800)
    assert len(pf.trades) == n0 and eng.state == "stopped"


async def test_state_survives_a_restart_and_resumes(tmp_path):
    sim, pf, eng = make_engine(tmp_path, seed=21)
    await eng.startup()
    await eng.start()
    await drive(sim, eng, 3 * 3600, stop_when=lambda: len(pf.open_positions()) >= 1)
    await eng._persist(force=True)
    saved = FileStore(str(tmp_path / "state.json")).load()
    pf2 = Portfolio.from_dict(saved)
    assert pf2.cash_sol == pytest.approx(pf.cash_sol) and set(pf2.positions) == set(pf.positions) and len(pf2.trades) == len(pf.trades)
    eng2 = Engine(market=sim, executor=eng.executor, store=eng.store, portfolio=pf2, step_delay=0.0)
    await eng2.startup()
    assert eng2.hunting and eng2.state == "running"                # AUTO_RESUME
    assert any("wiederhergestellt" in e["message"] for e in eng2.bus.recent(50))
    for p in pf2.open_positions():
        assert not p.exiting


async def test_daily_loss_limit_blocks_new_entries(tmp_path):
    sim, pf, eng = make_engine(tmp_path, seed=21)
    await eng.startup()
    await eng.start()
    pf.day_start_equity = pf.equity_sol(sim.sol_price) * 1.25         # we are 20 % below the day's start
    await eng._housekeeping_tick()
    assert eng.halted and "Tageslimit" in eng.halt_reason and eng.state == "halted"
    n0 = len(pf.trades)
    await drive(sim, eng, 1800)
    assert len(pf.trades) == n0


async def test_drawdown_kill_switch_stops_hunting_but_keeps_protection(tmp_path):
    sim, pf, eng = make_engine(tmp_path, seed=21)
    await eng.startup()
    await eng.start()
    pf.peak_equity = 100.0
    pf.day_start_equity = 0.1                                          # isolate the kill switch from the daily limit
    await eng._housekeeping_tick()
    assert eng.hard_halt and not eng.hunting and not pf.desired_running
    res = await eng.start()                                            # manual restart re-arms (peak reset)
    assert res["ok"] and eng.hunting and not eng.hard_halt


async def test_loss_streak_pause_resets_the_counter(tmp_path):
    sim, pf, eng = make_engine(tmp_path, seed=21)
    await eng.startup()
    pf.consecutive_losses = config.MAX_CONSECUTIVE_LOSSES
    eng._check_halts(pf.equity_sol(sim.sol_price), eng.now())
    assert eng.halted and pf.consecutive_losses == 0


async def test_reset_paper_is_refused_with_open_positions(tmp_path):
    sim, pf, eng = make_engine(tmp_path, seed=21)
    await eng.startup()
    pf.positions["x"] = Position(id="x", mint="M", qty=1.0, cost_sol=1.0, last_price_sol=1.0, entry_price_sol=1.0)
    assert not (await eng.reset_paper())["ok"]
    pf.positions.clear()
    assert (await eng.reset_paper())["ok"] and pf.cash_sol == config.PAPER_START_BALANCE_SOL and not pf.trades


async def test_snapshot_has_everything_the_dashboard_needs(tmp_path):
    sim, pf, eng = make_engine(tmp_path, seed=21)
    await eng.startup()
    await eng.start()
    await drive(sim, eng, 3 * 3600, stop_when=lambda: len(pf.open_positions()) >= 1)
    s = eng.snapshot()
    for key in ("mode", "engine", "wallet", "money", "agents", "committee", "positions", "trades", "stats", "watch", "sources",
                "banners", "limits", "events", "last_seq"):
        assert key in s
    assert [a["id"] for a in s["agents"]] == ["scout", "sentiment", "tech", "risk"]
    assert s["money"]["equity_sol"] == pytest.approx(pf.equity_sol(sim.sol_price))
    for p in s["positions"]:
        assert p["solscan"].startswith("https://solscan.io/token/") and 0.0 <= p["gauge"] <= 1.0 and p["stop"] > 0
    assert s["trades"][0]["token"].startswith("https://solscan.io/token/")
    pts = eng.equity_points("1h")["points"]
    assert pts and all(len(p) == 2 for p in pts)


# ───────────── live-mode safety gates ─────────────
def live(monkeypatch, *, key="", password="pw"):
    monkeypatch.setattr(config, "PAPER_TRADING", False)
    monkeypatch.setattr(config, "USE_SIMULATED_MARKET", False)
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", password)
    monkeypatch.setattr(config, "SOLANA_PRIVATE_KEY", key)


async def test_live_mode_without_a_key_stays_blocked(monkeypatch):
    live(monkeypatch, key="")
    built = build()
    eng = built.engine
    assert not eng.ready and any("SOLANA_PRIVATE_KEY" in p for p in built.problems)
    res = await eng.start()
    assert not res["ok"] and not eng.hunting
    await built.market.aclose()


def test_demo_market_is_forbidden_in_live_mode(monkeypatch):
    live(monkeypatch, key=str(Keypair()))
    monkeypatch.setattr(config, "USE_SIMULATED_MARKET", True)
    with pytest.raises(ConfigError):
        build()


async def test_live_start_requires_a_funded_wallet(monkeypatch, tmp_path):
    live(monkeypatch, key=str(Keypair()))
    built = build()
    kp = Keypair()
    w = Wallet(kp)
    rpc, jup = FakeRpc(w.address, int(0.01 * LAMPORTS)), FakeJupiter(kp.pubkey())
    ex = LiveExecutor(w, rpc.client(), jup.client())
    eng = Engine(market=built.market, executor=ex, store=FileStore(str(tmp_path / "s.json")),
                 portfolio=Portfolio("live", 0.0, 1.0), step_delay=0.0)
    res = await eng.start()
    assert not res["ok"] and "mindestens" in res["message"]
    await built.market.aclose()


async def test_new_live_account_gets_its_baselines_from_the_first_wallet_sync(monkeypatch, tmp_path):
    live(monkeypatch, key=str(Keypair()))
    built = build()
    kp = Keypair()
    w = Wallet(kp)
    rpc = FakeRpc(w.address, int(2.5 * LAMPORTS))
    ex = LiveExecutor(w, rpc.client(), FakeJupiter(kp.pubkey()).client())
    pf = Portfolio("live", 0.0, 1.0)
    eng = Engine(market=built.market, executor=ex, store=FileStore(str(tmp_path / "s.json")), portfolio=pf, step_delay=0.0)
    assert pf.initial_equity_sol == 0.0
    await eng._sync_cash()
    assert pf.cash_sol == pytest.approx(2.5) and pf.initial_equity_sol == pytest.approx(2.5)
    assert pf.day_start_equity == pytest.approx(2.5) and pf.peak_equity == pytest.approx(2.5)
    assert eng.snapshot()["money"]["pnl_sol"] == pytest.approx(0.0)       # not +2.5 "profit" vs. a zero baseline
    rpc.balance = int(2.0 * LAMPORTS)                                      # later syncs must NOT move the baseline
    await eng._sync_cash()
    assert pf.initial_equity_sol == pytest.approx(2.5) and eng.snapshot()["money"]["pnl_sol"] == pytest.approx(-0.5)
    await built.market.aclose()


async def test_live_reconcile_trusts_the_chain(monkeypatch, tmp_path):
    live(monkeypatch, key=str(Keypair()))
    built = build()
    kp = Keypair()
    w = Wallet(kp)
    rpc = FakeRpc(w.address, 3 * LAMPORTS)
    rpc.token_raw = 5_000_000                                          # 5 tokens of MINT on chain
    jup = FakeJupiter(kp.pubkey())
    ex = LiveExecutor(w, rpc.client(), jup.client())
    pf = Portfolio("live", 3.0, 1.0)
    ghost = Position(id="g", mint="GhostMint", symbol="GHOST", qty=100.0, cost_sol=1.0, entry_price_sol=0.01, last_price_sol=0.01)
    held = Position(id="h", mint=MINT, symbol="HELD", qty=9.0, cost_sol=1.0, entry_price_sol=0.1, last_price_sol=0.1)
    pf.positions = {"g": ghost, "h": held}
    eng = Engine(market=built.market, executor=ex, store=FileStore(str(tmp_path / "s.json")), portfolio=pf, step_delay=0.0)
    await eng._reconcile_live()
    assert "g" not in pf.positions                                     # not in the wallet any more -> removed
    assert pf.positions["h"].qty == pytest.approx(5.0)                 # quantity corrected from chain
    await built.market.aclose()


async def test_foreign_tokens_are_listed_but_not_adopted_by_default(monkeypatch, tmp_path):
    live(monkeypatch, key=str(Keypair()))
    built = build()
    kp = Keypair()
    w = Wallet(kp)
    rpc = FakeRpc(w.address, 3 * LAMPORTS)
    rpc.token_raw = 50_000_000_000                                      # foreign token the bot never bought
    ex = LiveExecutor(w, rpc.client(), FakeJupiter(kp.pubkey()).client())
    eng = Engine(market=built.market, executor=ex, store=FileStore(str(tmp_path / "s.json")), portfolio=Portfolio("live", 3.0, 1.0), step_delay=0.0)
    eng.sol_usd = 90.0

    async def marks(mints):
        return {m: 0.001 for m in mints}
    monkeypatch.setattr(built.market, "marks", marks)
    await eng._reconcile_live()
    assert not eng.pf.positions and eng.foreign_tokens and eng.foreign_tokens[0]["mint"] == MINT
    monkeypatch.setattr(config, "ADOPT_UNKNOWN_TOKENS", True)
    await eng._reconcile_live()
    assert len(eng.pf.positions) == 1 and next(iter(eng.pf.positions.values())).recovered
    await built.market.aclose()
