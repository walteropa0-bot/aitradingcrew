import pytest

from crew import strategy as st
from crew.brokers import BrokerError, LiveBroker, PaperBroker
from crew.exchange import Exchange, Tick
from tests.fakes import FakeKraken

P = st.Params()
RULES = st.Rules(6e-5, 0.45, 1e-8)


# ───────────────────────── paper ─────────────────────────
def _paper(ask=68_001.0, bid=67_999.0):
    tick = Tick("BTC/EUR", bid, ask, (bid + ask) / 2, 0.0)
    return PaperBroker(lambda s: tick if s == "BTC/EUR" else None, lambda s: RULES, P, clock=lambda: 123.0)


def test_paper_buy_fills_at_ask_plus_slippage_with_fee():
    f = _paper().buy("BTC/EUR", 0.01, 0)
    assert f.price == pytest.approx(68_001.0 * (1 + P.slippage))
    assert f.fee == pytest.approx(0.01 * f.price * P.fee_rate)
    assert f.quote_amount == pytest.approx(0.01 * f.price + f.fee) and f.simulated and f.ts == 123.0


def test_paper_sell_fills_at_bid_minus_slippage():
    f = _paper().sell("BTC/EUR", 0.01, 0)
    assert f.price == pytest.approx(67_999.0 * (1 - P.slippage))
    assert f.quote_amount == pytest.approx(0.01 * f.price - f.fee)


def test_paper_refuses_tiny_orders_and_unknown_markets():
    b = _paper()
    with pytest.raises(BrokerError) as e:
        b.buy("BTC/EUR", 1e-6, 0)
    assert e.value.kind == "min_size"
    with pytest.raises(BrokerError) as e2:
        b.buy("DOGE/EUR", 1.0, 0)
    assert e2.value.kind == "no_price"


# ───────────────────────── live ─────────────────────────
def live(client=None, cap=5_000.0):
    client = client or FakeKraken()
    br = LiveBroker(Exchange("kraken", client=client), P, fill_timeout=5.0, poll_interval=0.01,
                    sleep=lambda s: None, clock=client_clock(client))
    br.order_cap = cap
    return br, client


def client_clock(client):
    ticks = {"t": 1_800_000_000.0}

    def now():
        ticks["t"] += 0.5
        return ticks["t"]
    return now


def test_live_buy_happy_path_matches_exchange_numbers():
    br, c = live()
    f = br.buy("BTC/EUR", 0.0123456789, 68_000.0)
    assert len(c.create_calls) == 1 and c.create_calls[0]["params"]["clientOrderId"]
    assert not f.simulated and f.qty == pytest.approx(0.01234567)          # rounded DOWN to the lot size
    assert f.quote_amount == pytest.approx(f.qty * f.price + f.fee)
    assert c.balances["EUR"] == pytest.approx(10_000.0 - f.quote_amount)
    assert c.balances["BTC"] == pytest.approx(f.qty)


def test_live_buy_with_fee_in_base_currency_reduces_the_quantity():
    br, c = live()
    c.script = ["base_fee"]
    f = br.buy("BTC/EUR", 0.01, 68_000.0)
    assert f.qty < 0.01 and f.qty == pytest.approx(c.balances["BTC"])          # we hold exactly what we booked
    assert c.balances["EUR"] == pytest.approx(10_000.0 - f.quote_amount, abs=0.02)


def test_live_partial_fill_is_booked_as_filled():
    br, c = live()
    c.script = ["partial"]
    f = br.buy("BTC/EUR", 0.01, 68_000.0)
    assert f.qty == pytest.approx(0.005)


def test_timeout_after_the_order_was_placed_is_found_again_and_never_resent():
    br, c = live()
    c.script = ["timeout_after"]
    f = br.buy("BTC/EUR", 0.01, 68_000.0)
    assert len(c.create_calls) == 1, "must NOT send a second order"
    assert f.qty == pytest.approx(0.01) and c.balances["BTC"] == pytest.approx(0.01)


def test_timeout_before_the_order_reached_the_exchange_reports_unknown_and_never_resends():
    br, c = live()
    c.script = ["timeout_before"]
    with pytest.raises(BrokerError) as e:
        br.buy("BTC/EUR", 0.01, 68_000.0)
    assert e.value.kind == "unknown_outcome" and len(c.create_calls) == 1
    assert c.balances["EUR"] == 10_000.0


def test_client_id_rejected_falls_back_to_a_plain_order_once():
    br, c = live()
    c.script = ["reject_cid"]
    f = br.buy("BTC/EUR", 0.01, 68_000.0)
    assert len(c.create_calls) == 2 and "clientOrderId" not in c.create_calls[1]["params"] and f.qty == pytest.approx(0.01)
    assert c.balances["BTC"] == pytest.approx(0.01)                           # exactly ONE order executed


def test_order_open_at_first_fills_on_polling():
    br, c = live()
    c.script = ["open_only"]
    f = br.buy("BTC/EUR", 0.01, 68_000.0)
    assert f.qty == pytest.approx(0.01)


def test_order_that_never_fills_is_cancelled_and_reported_rejected():
    br, c = live()
    c.script = ["stuck"]
    with pytest.raises(BrokerError) as e:
        br.buy("BTC/EUR", 0.01, 68_000.0)
    assert e.value.kind == "rejected" and len(c.cancel_calls) == 1


def test_stuck_order_that_cannot_be_cancelled_is_unknown():
    br, c = live()
    c.script, c.cancel_fails = ["stuck"], True
    with pytest.raises(BrokerError) as e:
        br.buy("BTC/EUR", 0.01, 68_000.0)
    assert e.value.kind == "unknown_outcome"


def test_transient_fetch_order_errors_are_survived():
    br, c = live()
    c.script, c.fetch_order_fail = ["open_only"], 2
    assert br.buy("BTC/EUR", 0.01, 68_000.0).qty == pytest.approx(0.01)


def test_insufficient_free_balance_blocks_before_any_order():
    br, c = live(FakeKraken(balances={"EUR": 100.0}))
    with pytest.raises(BrokerError) as e:
        br.buy("BTC/EUR", 0.01, 68_000.0)
    assert e.value.kind == "funds" and c.create_calls == []


def test_hard_order_cap_blocks_oversized_orders():
    br, c = live(cap=200.0)
    with pytest.raises(BrokerError) as e:
        br.buy("BTC/EUR", 0.01, 68_000.0)                  # 680 EUR > 200
    assert e.value.kind == "cap" and c.create_calls == []
    br.order_cap = 0.0                                      # engine forgot to set a limit -> refuse everything
    with pytest.raises(BrokerError):
        br.buy("BTC/EUR", 0.001, 68_000.0)


def test_exchange_minimums_are_enforced():
    br, c = live()
    with pytest.raises(BrokerError) as e:
        br.buy("BTC/EUR", 1e-6, 68_000.0)
    assert e.value.kind == "min_size" and c.create_calls == []


def test_insufficient_funds_error_from_the_exchange_is_reported():
    br, c = live()
    c.script = ["insufficient"]
    with pytest.raises(BrokerError) as e:
        br.buy("BTC/EUR", 0.01, 68_000.0)
    assert e.value.kind == "funds"


def test_sell_is_limited_to_the_free_balance_and_never_sells_more_than_owned():
    br, c = live(FakeKraken(balances={"EUR": 0.0, "BTC": 0.004}))
    f = br.sell("BTC/EUR", 0.01, 68_000.0)                  # ledger thinks 0.01, account only has 0.004
    assert f.qty == pytest.approx(0.004) and c.balances["BTC"] == pytest.approx(0.0)
    assert f.quote_amount == pytest.approx(f.qty * f.price - f.fee)


def test_sell_of_dust_is_reported_not_attempted():
    br, c = live(FakeKraken(balances={"EUR": 0.0, "BTC": 0.00001}))
    with pytest.raises(BrokerError) as e:
        br.sell("BTC/EUR", 0.00001, 68_000.0)
    assert e.value.kind == "dust" and c.create_calls == []


def test_exchange_wrapper_validates_quotes():
    c = FakeKraken()
    ex = Exchange("kraken", client=c)
    ticks = ex.fetch_ticks(["BTC/EUR", "ETH/EUR"])
    assert set(ticks) == {"BTC/EUR", "ETH/EUR"} and ticks["BTC/EUR"].spread_pct < 0.05
    c.fetch_tickers = lambda symbols=None, params=None: {"BTC/EUR": {"bid": 100.0, "ask": 99.0, "last": 99.5},
                                                         "ETH/EUR": {"bid": None, "ask": None, "last": float("nan")}}
    assert ex.fetch_ticks(["BTC/EUR", "ETH/EUR"]) == {}       # crossed book and NaN prices are dropped
