"""LIVE-mode engine against a fake Kraken account: budget cap, other holdings untouched, ambiguous orders."""
import pytest

from crew import strategy as st
from crew.engine import Engine, Settings
from crew.exchange import Exchange
from crew.store import FileStore
from tests.fakes import LiveKraken, breakout_market
from tests.test_engine import Clock


class LiveWorld:
    def __init__(self, tmp_path, balances=None, live_capital=1000.0, locked=""):
        self.client = LiveKraken(balances or {"EUR": 5000.0})
        r4, rd, now = breakout_market(seed=3)
        self.client.c4["BTC/EUR"], self.client.cd["BTC/EUR"] = r4, rd
        self.client.prices["BTC/EUR"] = float(r4[-2, 4])
        self.ex = Exchange("kraken", client=self.client)
        self.clock = Clock(now)
        self.path = str(tmp_path / "live.json")
        self.settings = Settings(mode="live", symbols=("BTC/EUR",), live_capital=live_capital, live_locked_reason=locked, tick_sec=5.0)
        self.eng = Engine(self.ex, FileStore(self.path), st.Params(), self.settings, clock=self.clock)
        self.eng.broker._sleep = lambda s: None

    def run(self, n=1, dt=5.0):
        for _ in range(n):
            self.clock.advance(dt)
            self.eng.step()
        return self.eng.snapshot()

    @property
    def pf(self):
        return self.eng.pf


def test_live_stays_locked_without_prerequisites(tmp_path):
    w = LiveWorld(tmp_path, locked="Live-Handel ist gesperrt: Setze zuerst ein DASHBOARD_PASSWORD (Secrets).")
    res = w.eng.command("start")
    snap = w.run(3)
    assert res["ok"] is False and "DASHBOARD_PASSWORD" in res["message"]
    assert snap["state"] == "paused" and snap["locked"] and w.client.create_calls == []


def test_live_budget_is_capped_and_the_first_buy_hits_the_exchange(tmp_path):
    w = LiveWorld(tmp_path)
    w.pf.running = True                                     # as restored from a saved "running" state
    snap = w.run(3)
    assert w.eng.live_ready and w.pf.start_capital == pytest.approx(1000.0)        # min(LIVE_CAPITAL, free balance)
    assert len(w.client.create_calls) == 1 and len(snap["positions"]) == 1
    call = w.client.create_calls[0]
    pos = w.pf.positions["BTC/EUR"]
    assert call["side"] == "buy" and call["params"]["clientOrderId"]
    assert pos.qty == pytest.approx(w.client.balances["BTC"])                      # ledger == exchange
    assert pos.cost <= 1000.0 * 0.30 * 1.15 + 1e-6                                 # never more than the cap per order
    assert w.client.balances["EUR"] == pytest.approx(5000.0 - pos.cost, abs=0.05)  # the rest of the account is untouched
    assert snap["trades"][0]["order_id"].startswith("O")


def test_live_budget_is_limited_by_the_actual_balance(tmp_path):
    w = LiveWorld(tmp_path, balances={"EUR": 300.0}, live_capital=1000.0)
    w.run(1)
    assert w.pf.start_capital == pytest.approx(300.0)


def test_live_refuses_when_the_account_is_nearly_empty(tmp_path):
    w = LiveWorld(tmp_path, balances={"EUR": 4.0})
    w.run(1)
    assert not w.eng.live_ready and "mindestens 10" in w.eng.live_error
    assert w.eng.command("start")["ok"] is False


def test_api_key_that_can_withdraw_is_refused(tmp_path):
    w = LiveWorld(tmp_path)
    w.ex.check_credentials = lambda: {"ok": True, "withdraw_enabled": True, "detail": "ok"}
    w.run(2)
    assert not w.eng.live_ready and "AUSZAHLEN" in w.eng.live_error
    assert w.eng.command("start")["ok"] is False and w.client.create_calls == []


def test_invalid_api_key_is_reported_without_crashing(tmp_path):
    import ccxt
    w = LiveWorld(tmp_path)
    def bad(params=None):
        raise ccxt.AuthenticationError("kraken EAPI:Invalid key")
    w.client.fetch_balance = bad
    snap = w.run(2)
    assert not w.eng.live_ready and snap["live_error"] and snap["state"] == "paused"


def test_bot_only_sells_what_it_bought_and_never_the_users_other_coins(tmp_path):
    w = LiveWorld(tmp_path, balances={"EUR": 5000.0, "BTC": 0.5, "ETH": 3.0})      # the user already owns BTC and ETH
    w.pf.running = True
    w.run(3)
    bought = w.pf.positions["BTC/EUR"].qty
    w.eng.command("panic")
    w.run(3)
    assert w.pf.positions == {}
    sells = [c for c in w.client.create_calls if c["side"] == "sell"]
    assert len(sells) == 1 and sells[0]["amount"] == pytest.approx(bought, rel=1e-6)
    assert w.client.balances["BTC"] == pytest.approx(0.5, abs=1e-6) and w.client.balances["ETH"] == 3.0


def test_unknown_order_outcome_that_filled_late_is_adopted_with_a_stop(tmp_path):
    w = LiveWorld(tmp_path)
    w.client.script = ["timeout_before"]                    # the exchange never saw the request ... or did it?
    w.pf.running = True
    snap = w.run(1)
    assert w.pf.positions == {} and "BTC/EUR" in w.pf.pending and len(w.client.create_calls) == 1
    qty = w.pf.pending["BTC/EUR"]["qty"]
    w.client.balances["BTC"] = qty                          # ... turns out it DID fill, we just never got the answer
    w.client.balances["EUR"] -= qty * w.client.prices["BTC/EUR"]
    snap = w.run(2)
    assert "BTC/EUR" in w.pf.positions and w.pf.pending == {} and w.pf.positions["BTC/EUR"].stop > 0
    assert any("übernommen" in j["text"] for j in snap["journal"])
    assert len(w.client.create_calls) == 1, "must never re-send an order of unknown outcome"


def test_unknown_order_outcome_that_never_filled_is_cleared_after_three_minutes(tmp_path):
    w = LiveWorld(tmp_path)
    w.client.script = ["timeout_before"]
    w.pf.running = True
    w.run(1)
    assert "BTC/EUR" in w.pf.pending
    w.run(1, dt=200.0)
    assert w.pf.pending == {} and w.pf.positions == {} and len(w.client.create_calls) == 1


def test_missing_coins_on_the_exchange_are_corrected_in_the_ledger(tmp_path):
    w = LiveWorld(tmp_path)
    w.pf.running = True
    w.run(3)
    pos = w.pf.positions["BTC/EUR"]
    qty = pos.qty
    w.client.balances["BTC"] = qty * 0.4                    # the user moved 60 % of the coins away by hand
    w.eng._reconcile_balances(w.clock())
    assert pos.qty == pytest.approx(qty * 0.4)
    w.client.balances["BTC"] = 0.0
    w.eng._reconcile_balances(w.clock())
    assert w.pf.positions == {}                             # nothing left: book closed, no order attempted
    assert [c for c in w.client.create_calls if c["side"] == "sell"] == []


def test_live_budget_can_be_reset_only_when_flat_and_paused(tmp_path):
    w = LiveWorld(tmp_path, live_capital=1000.0)
    w.pf.running = True
    w.run(3)
    assert w.pf.positions
    assert "Positionen schließen" in w.eng.command("rebase_budget")["message"]          # refused while something is open
    w.eng.command("panic")
    w.run(3)
    assert w.pf.positions == {}
    w.settings.live_capital = 500.0                         # the user lowered LIVE_CAPITAL and restarted the app
    res = w.eng.command("rebase_budget")
    assert res["ok"] and w.pf.start_capital == pytest.approx(500.0) and w.pf.cash == pytest.approx(500.0)
    assert w.pf.history.points[-1][1] == pytest.approx(500.0)
    w.eng.command("start")
    assert "pausieren" in w.eng.command("rebase_budget")["message"]


def test_paper_mode_has_no_rebase_command(tmp_path):
    from tests.test_engine import World
    assert World(tmp_path).eng.command("rebase_budget")["ok"] is False
