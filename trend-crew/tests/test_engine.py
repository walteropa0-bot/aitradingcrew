import json

import pytest

from crew import strategy as st
from crew.brokers import BrokerError
from crew.engine import Engine, Settings
from crew.store import FileStore
from tests.fakes import ScriptedExchange, breakout_market

SYMS = ("BTC/EUR", "ETH/EUR")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


class World:
    def __init__(self, tmp_path, symbols=("BTC/EUR",), **settings):
        self.ex = ScriptedExchange(symbols)
        r4, rd, now = breakout_market(seed=3)
        for s in symbols:
            self.ex.c4[s], self.ex.cd[s], self.ex.px[s] = r4.copy(), rd.copy(), float(r4[-2, 4])
        self.clock = Clock(now)
        self.path = str(tmp_path / "state.json")
        self.settings = Settings(symbols=tuple(symbols), tick_sec=5.0, **settings)
        self.eng = self.new_engine()

    def new_engine(self, store=None):
        return Engine(self.ex, store or FileStore(self.path), st.Params(), self.settings, clock=self.clock)

    def run(self, n=1, dt=5.0):
        for _ in range(n):
            self.clock.advance(dt)
            self.eng.step()
        return self.eng.snapshot()

    def set_price(self, price, sym="BTC/EUR"):
        self.ex.px[sym] = price

    @property
    def pf(self):
        return self.eng.pf


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def started(w):
    assert w.eng.command("start")["ok"]
    return w.run(2)


# ───────────────────────── scenario sanity ─────────────────────────
def test_scenario_really_is_a_breakout_buy(w):
    snap = w.run(2)
    m = snap["markets"][0]
    assert m["signal"]["action"] == "BUY" and m["trend"] == "up" and m["tradable"]
    assert all(c["ok"] for c in m["signal"]["checks"])


# ───────────────────────── entries ─────────────────────────
def test_paused_bot_never_buys(w):
    snap = w.run(3)
    assert snap["state"] == "paused" and snap["positions"] == [] and snap["trades"] == []
    assert any("pausiert" in j["text"] for j in snap["journal"])


def test_started_bot_buys_the_signal_with_exact_risk_and_a_stop(w):
    snap = started(w)
    assert snap["state"] == "running" and len(snap["positions"]) == 1
    pos = snap["positions"][0]
    assert pos["stop"] < pos["entry"] and pos["stop_dist_pct"] > 1.0
    assert pos["risk"] == pytest.approx(snap["start_capital"] * 0.01, rel=0.12)        # 1 % risk per trade
    assert pos["value"] <= snap["equity"] * 0.30 + 1e-6
    assert snap["trades"][0]["side"] == "buy" and snap["trades"][0]["fee"] > 0
    assert snap["cash"] == pytest.approx(snap["start_capital"] - w.pf.positions["BTC/EUR"].cost)
    assert any(j["kind"] == "trade" and "KAUF" in j["text"] for j in snap["journal"])


def test_the_same_candle_never_buys_twice(w):
    started(w)
    snap = w.run(30)
    assert len(snap["positions"]) == 1 and len([t for t in snap["trades"] if t["side"] == "buy"]) == 1


def test_stale_signal_is_skipped(w):
    w.clock.advance(1200)                                  # engine wakes up 20 min after the candle closed
    w.eng.command("start")
    snap = w.run(2)
    assert snap["positions"] == [] and any("zu alt" in j["text"] for j in snap["journal"])


def test_signal_waits_for_price_data_and_buys_when_it_returns(w):
    w.eng.command("start")
    w.ex.fail = "network"
    w.run(2)
    assert w.pf.positions == {} and w.eng.snapshot()["system"]["ok"] is False
    w.ex.fail = ""
    snap = w.run(3)
    assert len(snap["positions"]) == 1                     # the (still fresh) signal was kept and executed


def test_entry_is_refused_when_the_scout_says_not_tradable(w):
    w.ex.spread = 0.02                                     # 2 % spread
    w.eng.command("start")
    snap = w.run(3)
    assert snap["positions"] == [] and any("nicht handelbar" in j["text"] or "Spread" in j["text"] for j in snap["journal"])


# ───────────────────────── stops ─────────────────────────
def test_trailing_stop_ratchets_up_and_never_down(w):
    started(w)
    pos = w.pf.positions["BTC/EUR"]
    stop0, p0 = pos.stop, w.ex.px["BTC/EUR"]
    w.set_price(p0 * 1.04); w.run(2)
    stop1 = pos.stop
    assert stop1 > stop0 and pos.highest >= p0 * 1.04 * 0.999
    w.set_price(p0 * 1.02); w.run(3)
    assert pos.stop == stop1 and "BTC/EUR" in w.pf.positions       # retreat: stop unchanged, still open


def test_stop_out_sells_books_the_loss_and_starts_a_cooldown(w):
    started(w)
    pos = w.pf.positions["BTC/EUR"]
    w.set_price(pos.stop * 0.98)
    snap = w.run(2)
    assert snap["positions"] == [] and snap["trades"][0]["side"] == "sell"
    t = snap["trades"][0]
    assert t["pnl"] < 0 and "Stop" in t["reason"] and snap["stats"]["losses"] == 1
    assert w.pf.cooldowns["BTC/EUR"] > w.clock()
    # the loss is bounded by the planned risk (+ gap/slippage/fees tolerance)
    assert -t["pnl"] < snap["start_capital"] * 0.02
    assert snap["equity"] == pytest.approx(snap["cash"])


def test_sell_failures_are_retried_and_the_position_is_not_forgotten(w):
    started(w)
    pos = w.pf.positions["BTC/EUR"]
    real, calls = w.eng.broker.sell, {"n": 0}

    def flaky(sym, qty, ref):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise BrokerError("network", "boom")
        return real(sym, qty, ref)
    w.eng.broker.sell = flaky
    w.set_price(pos.stop * 0.97)
    w.run(1)
    assert "BTC/EUR" in w.pf.positions and "BTC/EUR" in w.eng._exits           # still open, retry scheduled
    snap = w.run(12, dt=5.0)
    assert snap["positions"] == [] and calls["n"] == 3


def test_soft_stop_retry_is_cancelled_if_the_price_recovers_but_not_for_panic(w):
    started(w)
    pos = w.pf.positions["BTC/EUR"]
    w.eng.broker.sell = lambda *a: (_ for _ in ()).throw(BrokerError("network", "down"))
    w.set_price(pos.stop * 0.97)
    w.run(1)
    assert "BTC/EUR" in w.eng._exits
    w.set_price(pos.stop * 1.05)
    w.run(6)
    assert "BTC/EUR" not in w.eng._exits and "BTC/EUR" in w.pf.positions       # false alarm, position kept
    w.eng.command("panic")
    w.set_price(pos.stop * 1.05)
    w.run(6)
    assert "BTC/EUR" in w.eng._exits                                             # panic never gives up


def test_dust_position_is_dropped_from_the_book(w):
    started(w)
    w.eng.broker.sell = lambda *a: (_ for _ in ()).throw(BrokerError("dust", "tiny"))
    w.set_price(w.pf.positions["BTC/EUR"].stop * 0.97)
    snap = w.run(2)
    assert snap["positions"] == []


def test_wild_price_spike_is_not_believed_until_confirmed(w):
    started(w)
    p0 = w.ex.px["BTC/EUR"]
    w.set_price(p0 * 0.6)                                   # absurd one-off tick
    w.run(1)
    assert "BTC/EUR" in w.pf.positions and w.eng.ticks["BTC/EUR"].last == pytest.approx(p0)
    w.run(1)                                                # still there next time -> now it counts
    assert w.pf.positions == {} or w.eng.ticks["BTC/EUR"].last == pytest.approx(p0 * 0.6)


# ───────────────────────── Not-Aus & breakers ─────────────────────────
def test_panic_liquidates_everything_halts_and_blocks_new_buys(w):
    started(w)
    res = w.eng.command("panic")
    assert res["ok"] and "NOT-AUS" in res["message"]
    snap = w.run(2)
    assert snap["positions"] == [] and snap["state"] == "halted" and "Not-Aus" in snap["halt_reason"]
    assert snap["trades"][0]["side"] == "sell" and "NOT-AUS" in snap["trades"][0]["reason"]
    # a fresh signal on a new candle must not be bought while halted
    w.pf.last_bar.clear()
    w.eng.data["BTC/EUR"].dirty = True
    snap = w.run(3)
    assert snap["positions"] == []


def test_panic_keeps_selling_after_a_partial_fill_and_start_waits_until_flat(w):
    started(w)
    real, state = w.eng.broker.sell, {"n": 0}

    def half_then_rest(sym, qty, ref):
        state["n"] += 1
        return real(sym, qty * 0.5 if state["n"] == 1 else qty, ref)         # first call fills only half
    w.eng.broker.sell = half_then_rest
    w.eng.command("panic")
    w.run(1)
    assert "BTC/EUR" in w.pf.positions and "BTC/EUR" in w.eng._exits           # half is left, intent stays
    assert w.eng.command("start")["ok"] is False                               # restart refused while still liquidating
    snap = w.run(3)
    assert snap["positions"] == [] and w.eng._exits == {} and state["n"] == 2
    assert w.eng.command("start")["ok"] is True


def test_start_after_panic_resets_the_reference_points(w):
    started(w)
    w.set_price(w.ex.px["BTC/EUR"] * 0.97); w.run(1)
    w.eng.command("panic"); w.run(2)
    w.pf.peak = 99_999.0                                    # pretend an old, unreachable high-water mark
    assert w.eng.command("start")["ok"]
    assert w.pf.peak == pytest.approx(w.pf.equity(w.eng._marks())) and w.pf.halted == ""


def test_kill_switch_drawdown_liquidates_and_halts(tmp_path):
    w = World(tmp_path, kill_dd_pct=0.3, daily_loss_limit_pct=50.0)
    started(w)
    w.set_price(w.ex.px["BTC/EUR"] * 0.97)                  # position of ~12 % of equity loses ~0.35 % -> brake at 0.3 %
    snap = w.run(3)
    assert snap["state"] == "halted" and "Drawdown" in snap["halt_reason"] and snap["positions"] == []
    assert any("NOTBREMSE" in j["text"] for j in snap["journal"])


def test_daily_loss_limit_blocks_entries_but_keeps_managing_positions(tmp_path):
    w = World(tmp_path, symbols=SYMS, daily_loss_limit_pct=0.2, kill_dd_pct=50.0)
    w.ex.c4["ETH/EUR"] = w.ex.c4["BTC/EUR"].copy()
    w.eng.command("start")
    w.pf.last_bar.clear()
    w.eng.data["ETH/EUR"].closed = None
    w.run(1)
    held = list(w.pf.positions)
    assert held, "expected at least one position"
    sym = held[0]
    w.set_price(w.ex.px[sym] * 0.975, sym)
    snap = w.run(2)
    assert snap["day_blocked"] and "Tagesverlust" in snap["journal"][0]["text"] + " ".join(j["text"] for j in snap["journal"])
    other = [s for s in SYMS if s != sym][0]
    assert "Tagesverlust" in w.eng._entry_block(other, w.clock())


def test_cooldown_blocks_a_re_entry_after_a_stop_out(w):
    started(w)
    w.set_price(w.pf.positions["BTC/EUR"].stop * 0.97)
    w.run(2)
    w.pf.last_bar.clear()                                   # same signal shows up again
    w.set_price(w.ex.c4["BTC/EUR"][-2, 4])
    w.eng.data["BTC/EUR"].dirty = True
    snap = w.run(3)
    assert snap["positions"] == [] and any("Abkühlphase" in j["text"] for j in snap["journal"])


# ───────────────────────── resilience ─────────────────────────
def test_exchange_outage_is_reported_and_recovers(w):
    started(w)
    w.ex.fail = "network"
    snap = w.run(3)
    assert snap["system"]["ok"] is False and snap["agents"][0]["state"] in ("warn", "error")
    assert len(snap["positions"]) == 1                      # nothing is sold blindly during an outage
    w.ex.fail = ""
    snap = w.run(4)
    assert snap["system"]["ok"] is True and snap["agents"][0]["state"] == "ok"


def test_stale_prices_stop_stop_checks_and_entries(w):
    started(w)
    pos = w.pf.positions["BTC/EUR"]
    w.ex.fail = "network"
    w.set_price(pos.stop * 0.9)
    w.run(1, dt=400.0)                                      # prices older than MAX_DATA_AGE
    assert "BTC/EUR" in w.pf.positions and "BTC/EUR" not in w.eng._exits


def test_internal_errors_in_one_stage_do_not_kill_the_loop(w):
    started(w)
    def boom(now): raise RuntimeError("kaputt")
    w.eng._analyze_all = boom
    snap = w.run(3)
    assert "kaputt" in snap["system"]["internal_error"] and snap["state"] == "running"
    assert any("Interner Fehler" in j["text"] for j in snap["journal"])


def test_thread_runs_and_commands_work_across_threads(tmp_path):
    import time
    w = World(tmp_path)
    w.eng.s.tick_sec = 0.05
    assert w.eng.command("start")["ok"]                     # no thread yet -> handled inline
    w.eng.start_thread()
    try:
        t0 = time.time()
        while time.time() - t0 < 5 and not w.pf.positions:
            w.clock.advance(1)
            time.sleep(0.05)
        assert w.eng.is_alive() and w.pf.positions
        res = w.eng.command("panic", timeout=3.0)           # command crosses the thread boundary
        assert res["ok"] and "NOT-AUS" in res["message"]
        t0 = time.time()
        while time.time() - t0 < 5 and w.pf.positions:
            w.clock.advance(1)
            time.sleep(0.05)
        assert w.pf.positions == {} and w.pf.halted
    finally:
        w.eng.stop_thread()
    assert not w.eng.is_alive()


# ───────────────────────── persistence ─────────────────────────
def test_state_survives_a_restart_and_keeps_protecting_positions(w):
    started(w)
    pos = w.pf.positions["BTC/EUR"]
    w.set_price(w.ex.px["BTC/EUR"] * 1.05); w.run(2)
    stop_before, qty_before, cash_before = pos.stop, pos.qty, w.pf.cash
    w.eng._save(w.clock()); w.eng._dirty = True; w.eng._save(w.clock())
    e2 = w.new_engine()
    assert e2.pf.running and e2.pf.positions["BTC/EUR"].qty == pytest.approx(qty_before)
    assert e2.pf.positions["BTC/EUR"].stop == pytest.approx(stop_before) and e2.pf.cash == pytest.approx(cash_before)
    e2.step()
    assert "BTC/EUR" in e2.pf.positions and len(e2.pf.trades) == len(w.pf.trades)     # no duplicate buy after restart
    # the restarted engine still stops the position out
    w.clock.advance(5); w.set_price(stop_before * 0.97)
    e2.step()
    assert e2.pf.positions == {}


def test_corrupt_or_foreign_state_falls_back_to_a_fresh_account(tmp_path):
    w = World(tmp_path)
    with open(w.path, "w") as fh:
        fh.write("{not json")
    e = w.new_engine()
    assert e.pf.cash == w.settings.start_capital and e.pf.positions == {}
    with open(w.path, "w") as fh:
        json.dump({"schema": 1, "mode": "live", "start_capital": 5, "cash": 5}, fh)       # state of the other mode
    e = w.new_engine()
    assert e.pf.start_capital == w.settings.start_capital
    assert any("unbrauchbar" in j["text"] for j in e.journal.recent(10))


def test_autostart_off_resumes_paused(tmp_path):
    w = World(tmp_path)
    started(w)
    w.eng._dirty = True; w.eng._save(w.clock())
    w.settings.autostart = False
    assert w.new_engine().pf.running is False


def test_reset_paper_restores_the_start_capital(w):
    started(w)
    assert w.eng.command("reset_paper")["ok"]
    snap = w.run(1)
    assert snap["positions"] == [] and snap["equity"] == pytest.approx(w.settings.start_capital) and snap["trades"] == []


def test_unknown_command_is_rejected_cleanly(w):
    assert w.eng.command("self_destruct")["ok"] is False


# ───────────────────────── snapshot contract used by the dashboard ─────────────────────────
def test_snapshot_is_complete_and_json_serialisable(w):
    snap = started(w)
    json.dumps(snap, allow_nan=False)
    for key in ("equity", "pnl", "pnl_pct", "day_pnl", "state", "agents", "markets", "positions", "trades", "stats", "curve",
                "journal", "system", "config", "quote", "currency", "locked"):
        assert key in snap
    assert [a["id"] for a in snap["agents"]] == ["scout", "analyst", "risk"]
    m = snap["markets"][0]
    assert m["base"] == "BTC" and m["rsi"] is not None and m["next_bar_in"] is not None and m["signal"]["checks"]
    assert len(snap["curve"]) >= 2 and snap["curve"][-1][1] == pytest.approx(snap["equity"], rel=1e-3)
