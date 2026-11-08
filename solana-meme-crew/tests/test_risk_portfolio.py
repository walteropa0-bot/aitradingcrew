import pytest

import config
from crew.agents.risk import RiskManager
from crew.events import EventBus
from crew.models import ExitOrder, Fill, Pair, Position
from crew.trading.portfolio import EQUITY_MAX, Portfolio
from crew.util import next_utc_midnight

NOW = 1_800_000_000.0


def mk_risk():
    return RiskManager(EventBus())


def mk_pos(entry=1.0, qty=100.0, age_min=1.0, **kw):
    cost = entry * qty
    p = Position(id="p1", mint="M", symbol="MEME", entry_price_sol=entry, qty=qty, cost_sol=cost, initial_cost_sol=cost,
                 peak_price_sol=entry, last_price_sol=entry, stop_price_sol=entry * 0.8, opened_at=NOW - age_min * 60,
                 entry_liquidity_usd=50_000, peak_liquidity_usd=50_000, last_liquidity_usd=50_000)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def pair(liq=60_000, mint="M"):
    return Pair(mint=mint, symbol="MEME", liquidity_usd=liq, price_sol=1e-6)


# ───────────── trailing stop ─────────────
@pytest.mark.parametrize("peak_gain,expected", [(0, 20.0), (39.9, 20.0), (40, 16.0), (99, 16.0), (100, 12.0), (299, 12.0), (300, 8.0), (1000, 8.0)])
def test_trailing_tiers_tighten_with_profit(peak_gain, expected):
    assert RiskManager.trail_pct_for(peak_gain) == expected


def test_stop_only_ever_ratchets_up():
    r, p = mk_risk(), mk_pos()
    seen = []
    for price in [1.0, 1.2, 1.5, 1.8, 1.6, 1.3, 1.9, 1.1]:
        p.last_price_sol = price
        p.peak_price_sol = max(p.peak_price_sol, price)
        seen.append(r.update_stop(p))
    assert seen == sorted(seen)                                       # monotonic non-decreasing
    assert seen[0] == pytest.approx(0.8)                              # hard initial stop = -20 %
    assert seen[-1] == pytest.approx(1.9 * (1 - 0.16))                # peak 1.9 (+90 %) -> 16 % trail


def test_stop_loss_then_trailing_stop_reasons():
    r = mk_risk()
    p = mk_pos()
    r.update_stop(p)
    o = r.evaluate_position(p, 0.79, 50_000, 0.8, NOW)
    assert o and o.reason == "Stop-Loss" and o.fraction == 1.0 and not o.panic
    p2 = mk_pos(peak_price_sol=1.6, last_price_sol=1.6)
    r.update_stop(p2)
    o2 = r.evaluate_position(p2, p2.stop_price_sol - 0.001, 50_000, p2.stop_price_sol + 0.01, NOW)
    assert o2 and o2.reason == "Trailing-Stop"


def test_no_exit_while_above_the_stop():
    r, p = mk_risk(), mk_pos()
    r.update_stop(p)
    assert r.evaluate_position(p, 0.95, 50_000, 0.96, NOW) is None


def test_partial_take_profit_fires_once():
    r, p = mk_risk(), mk_pos(last_price_sol=2.05, peak_price_sol=2.05)
    r.update_stop(p)
    o = r.evaluate_position(p, 2.05, 50_000, 2.04, NOW)
    assert o and o.kind == "take_profit" and o.fraction == pytest.approx(0.5) and o.stage == 0
    p.tp_done.append(0)
    assert r.evaluate_position(p, 2.05, 50_000, 2.04, NOW) is None


def test_stale_position_is_closed_by_time_stop():
    r = mk_risk()
    old = mk_pos(age_min=config.STALE_AFTER_MIN + 5, last_price_sol=1.02)
    assert r.evaluate_position(old, 1.02, 50_000, 1.02, NOW).reason.startswith("Zeit-Stop")
    winner = mk_pos(age_min=config.STALE_AFTER_MIN + 5, last_price_sol=1.4, peak_price_sol=1.4)
    r.update_stop(winner)
    assert r.evaluate_position(winner, 1.4, 50_000, 1.4, NOW) is None


def test_rug_alarm_on_liquidity_collapse_is_a_panic_exit():
    r, p = mk_risk(), mk_pos()
    o = r.evaluate_position(p, 0.99, 50_000 * 0.55, 1.0, NOW)         # -45 % liquidity, price still fine
    assert o and o.panic and o.reason.startswith("RUG-ALARM")


def test_crash_alarm_on_single_step_drop_is_a_panic_exit():
    r, p = mk_risk(), mk_pos(last_price_sol=1.5, peak_price_sol=1.5)
    r.update_stop(p)
    o = r.evaluate_position(p, 1.5 * 0.6, 50_000, 1.5, NOW)
    assert o and o.panic and o.reason.startswith("CRASH-ALARM")


def test_exit_slippage_escalates_but_is_capped():
    o = ExitOrder("p", 1.0, "Stop-Loss")
    a0, a3, a20 = (RiskManager.exit_slippage_bps(o, n) for n in (0, 3, 20))
    assert a0 == config.SELL_SLIPPAGE_BPS and a0 < a3 <= a20 == config.PANIC_SLIPPAGE_BPS
    assert RiskManager.exit_slippage_bps(ExitOrder("p", 1.0, "x", panic=True), 0) == config.PANIC_SLIPPAGE_BPS


# ───────────── sizing + approval ─────────────
def test_position_size_respects_every_limit():
    r = mk_risk()
    size, why = r.size_position(capital=10.0, cash=10.0, cost_exposure=0.0, pair=pair(), sol_usd=90.0)
    assert size == pytest.approx(config.MAX_POSITION_SOL) and "Obergrenze" in why
    size, why = r.size_position(capital=1.0, cash=1.0, cost_exposure=0.0, pair=pair(), sol_usd=90.0)
    assert size == pytest.approx(1.0 * 0.015 / 0.20, abs=1e-4) and "Risiko" in why       # 1.5 % risk / 20 % stop
    size, why = r.size_position(capital=10.0, cash=10.0, cost_exposure=0.0, pair=pair(liq=2_000), sol_usd=90.0)
    assert size < 0.2 and "Pool" in why                                                 # tiny pool -> tiny position
    size, why = r.size_position(capital=10.0, cash=10.0, cost_exposure=4.9, pair=pair(), sol_usd=90.0)
    assert size == pytest.approx(0.1, abs=2e-4) and size <= 0.1 and "Exposure" in why    # 50 % exposure cap (rounded DOWN)
    size, _ = r.size_position(capital=10.0, cash=0.06, cost_exposure=0.0, pair=pair(), sol_usd=90.0)
    assert size <= 0.011                                                                # keeps the SOL reserve


def approve(r, pf=None, **kw):
    pf = pf or Portfolio("paper", 10.0, NOW)
    args = dict(pf=pf, capital=10.0, cash=10.0, pair=pair(), sol_usd=90.0, live_price=1e-6, signal_price=1e-6, ts=NOW)
    args.update(kw)
    return r.approve_entry(**args), pf


def test_entry_approved_with_stop_and_bounded_slippage():
    d, _ = approve(mk_risk())
    assert d.approved and 0 < d.sol_amount <= config.MAX_POSITION_SOL
    assert 150 <= d.slippage_bps <= config.BUY_SLIPPAGE_BPS
    assert d.stop_price == pytest.approx(1e-6 * 0.8)


def test_entry_rejected_when_price_ran_away_or_is_falling():
    d, _ = approve(mk_risk(), live_price=1.12e-6)
    assert not d.approved and "davongelaufen" in d.reason
    d, _ = approve(mk_risk(), live_price=0.88e-6)
    assert not d.approved and "fällt" in d.reason


def test_entry_rejected_for_blacklist_max_positions_and_duplicates():
    r = mk_risk()
    pf = Portfolio("paper", 10.0, NOW)
    pf.blacklist_mint("M", NOW + 100)
    assert "gesperrt" in approve(r, pf)[0].reason
    pf2 = Portfolio("paper", 10.0, NOW)
    assert not approve(r, pf2, pending=config.MAX_OPEN_POSITIONS)[0].approved
    pf3 = Portfolio("paper", 10.0, NOW)
    pf3.positions["x"] = mk_pos()
    assert "bereits investiert" in approve(r, pf3)[0].reason


def test_entry_size_is_reduced_to_respect_the_price_impact_limit():
    r = mk_risk()
    d, _ = approve(r, capital=100.0, cash=100.0, pair=pair(liq=9_000))
    assert d.approved is False or d.est_impact_pct <= config.MAX_PRICE_IMPACT_PCT + 1e-6


def test_in_flight_buys_reserve_capital():
    r = mk_risk()
    d, _ = approve(r, capital=10.0, cash=10.0, reserved=4.9)
    assert d.approved and d.sol_amount <= 0.1 + 1e-9


# ───────────── circuit breakers ─────────────
def test_daily_loss_limit_halts_until_midnight():
    r, pf = mk_risk(), Portfolio("paper", 10.0, NOW)
    assert r.check_halts(pf, 9.5, NOW) is None
    h = r.check_halts(pf, 8.9, NOW)                                    # -11 % > 10 % limit
    assert h and h.kind == "daily" and not h.hard and h.until == next_utc_midnight(NOW)


def test_kill_switch_on_drawdown_is_hard():
    r, pf = mk_risk(), Portfolio("paper", 10.0, NOW)
    pf.peak_equity = 20.0
    pf.day_start_equity = 6.0                                          # (daily limit not the trigger)
    h = r.check_halts(pf, 12.0, NOW)                                   # -40 % from peak
    assert h and h.hard and h.kind == "drawdown"


def test_loss_streak_pauses_hunting():
    r, pf = mk_risk(), Portfolio("paper", 10.0, NOW)
    pf.consecutive_losses = config.MAX_CONSECUTIVE_LOSSES
    h = r.check_halts(pf, 10.0, NOW)
    assert h and h.kind == "streak" and h.until == pytest.approx(NOW + config.PAUSE_AFTER_LOSSES_MIN * 60)


# ───────────── portfolio accounting ─────────────
def buy_fill(sol=1.0, tokens=1000.0, fee=0.001):
    return Fill(ok=True, side="BUY", mint="M", symbol="MEME", sol_amount=sol, token_amount=tokens, price_sol=sol / tokens, fee_sol=fee, simulated=True)


def sell_fill(sol, tokens, fee=0.001):
    return Fill(ok=True, side="SELL", mint="M", symbol="MEME", sol_amount=sol, token_amount=tokens, price_sol=sol / tokens, fee_sol=fee, simulated=True)


def test_buy_then_full_sell_accounting_is_exact():
    pf = Portfolio("paper", 10.0, NOW)
    pos = pf.record_buy(buy_fill(), pair(), decimals=6, stop_price=0.0008e-3, ts=NOW)
    assert pf.cash_sol == pytest.approx(10.0 - 1.001) and pos.cost_sol == pytest.approx(1.001)
    tr, closed, total = pf.record_sell(pos, sell_fill(1.5, 1000.0), "Trailing-Stop", NOW + 60)
    assert closed and pos.status == "closed" and not pf.positions
    assert tr.pnl_sol == pytest.approx(1.5 - 0.001 - 1.001) and total == pytest.approx(tr.pnl_sol)
    assert pf.cash_sol == pytest.approx(10.0 - 1.001 + 1.499) and pf.wins == 1 and pf.losses == 0
    assert pf.realized_pnl_sol == pytest.approx(total)


def test_partial_sell_keeps_the_rest_open_with_proportional_cost():
    pf = Portfolio("paper", 10.0, NOW)
    pos = pf.record_buy(buy_fill(), pair(), decimals=6, stop_price=0.0, ts=NOW)
    tr, closed, _ = pf.record_sell(pos, sell_fill(1.1, 500.0), "Take-Profit +100%", NOW + 30)
    assert not closed and pos.qty == pytest.approx(500.0) and pos.cost_sol == pytest.approx(1.001 / 2)
    assert tr.pnl_sol == pytest.approx((1.1 - 0.001) - 1.001 / 2)
    _, closed2, total = pf.record_sell(pos, sell_fill(0.3, 500.0), "Stop-Loss", NOW + 90)
    assert closed2 and total == pytest.approx((1.1 - 0.001) + (0.3 - 0.001) - 1.001)


def test_losing_position_counts_as_loss_and_streak():
    pf = Portfolio("paper", 10.0, NOW)
    pos = pf.record_buy(buy_fill(), pair(), decimals=6, stop_price=0.0, ts=NOW)
    pf.record_sell(pos, sell_fill(0.7, 1000.0), "Stop-Loss", NOW + 10)
    assert pf.losses == 1 and pf.consecutive_losses == 1 and pf.worst_pct < 0


def test_ledger_identity_cash_equals_flows_over_many_random_trades():
    import random
    rng = random.Random(1)
    pf = Portfolio("paper", 10.0, NOW)
    flows = 10.0
    for _ in range(60):
        sol = rng.uniform(0.05, 0.5)
        fee = 0.0005
        pos = pf.record_buy(buy_fill(sol, 1000.0, fee), pair(), decimals=6, stop_price=0.0, ts=NOW)
        flows -= sol + fee
        for part in (0.4, 1.0):
            qty = pos.qty * part if part < 1 else pos.qty
            proceeds = sol * rng.uniform(0.3, 2.5) * part
            pf.record_sell(pos, sell_fill(proceeds, qty, fee), "x", NOW)
            flows += proceeds - fee
    assert pf.cash_sol == pytest.approx(flows) and not pf.positions
    assert pf.equity_sol() == pytest.approx(pf.cash_sol)


def test_persistence_roundtrip_keeps_everything_important():
    pf = Portfolio("paper", 10.0, NOW)
    pos = pf.record_buy(buy_fill(), pair(), decimals=6, stop_price=0.0001, ts=NOW)
    pos.tp_done.append(0)
    pf.blacklist_mint("Z", NOW + 500)
    pf.desired_running = True
    pf.sample_equity(NOW, 10.0)
    back = Portfolio.from_dict(pf.to_dict())
    assert back.cash_sol == pytest.approx(pf.cash_sol) and list(back.positions) == list(pf.positions)
    q = next(iter(back.positions.values()))
    assert q.tp_done == [0] and q.stop_price_sol == pytest.approx(0.0001) and q.qty == pytest.approx(1000.0)
    assert back.blacklist == pf.blacklist and back.desired_running and len(back.trades) == 1 and back.equity == pf.equity


def test_blacklist_expires():
    pf = Portfolio("paper", 10.0, NOW)
    pf.blacklist_mint("M", NOW + 10)
    assert pf.is_blacklisted("M", NOW + 5) and not pf.is_blacklisted("M", NOW + 11) and "M" not in pf.blacklist


def test_equity_series_stays_bounded_and_keeps_recent_resolution():
    pf = Portfolio("paper", 10.0, NOW)
    for i in range(EQUITY_MAX + 500):
        pf.sample_equity(NOW + i * 10, 10 + i * 0.001)
    assert len(pf.equity) <= EQUITY_MAX and pf.equity[-1][0] == NOW + (EQUITY_MAX + 499) * 10


def test_day_roll_resets_daily_baseline():
    pf = Portfolio("paper", 10.0, NOW)
    pf.day_realized = 1.0
    assert not pf.roll_day(NOW + 60, 11.0)
    assert pf.roll_day(NOW + 86400 * 2, 11.0) and pf.day_start_equity == 11.0 and pf.day_realized == 0.0
