import numpy as np
import pytest

import config
from crew import strategy as st
from crew.util import floor_to_step
from tests.conftest import D1, H4, T0, candles, trending


def P(**kw):
    return st.Params(**kw)


# ───────────────────────── configuration wiring ─────────────────────────
def test_paper_trading_is_the_first_assignment_in_config():
    import ast
    src = open(config.__file__, encoding="utf-8").read()
    first = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign))
    assert first.targets[0].id == "PAPER_TRADING" and first.value.value is True
    assert first.lineno < 12            # really "ganz oben"


def test_params_from_config_match_defaults():
    p = st.Params.from_config()
    assert p.mode == "breakout" and p.timeframe == "4h" and p.stop_atr_mult == 5.0
    assert p.risk_per_trade == pytest.approx(0.01) and p.fee_rate == pytest.approx(0.0026)
    assert p.warmup < config.CANDLE_HISTORY
    assert p.bars_per_day == 6.0


# ───────────────────────── regime without look-ahead ─────────────────────────
def test_htf_regime_only_sees_closed_daily_candles():
    p = P(ema_fast=3, ema_slow=5)
    # 12 daily candles: flat for 8 days, then a big jump on day index 9
    d_close = np.r_[np.full(9, 100.0), 200.0, 200.0, 200.0]
    daily = candles(d_close, tf_ms=D1)
    main = candles(np.linspace(100, 200, 72), tf_ms=H4)          # 12 days of 4h candles
    f = st.compute_features(main, p, daily)
    # day 9 (the jump) closes at T0 + 10 days = 4h-candle index 60 closes at T0 + 10d + 4h... candle 59 closes exactly at day 10
    first_up = int(np.argmax(f.trend_up))
    close_of_jump_day = T0 + 10 * D1
    assert f.ts[first_up] + H4 >= close_of_jump_day         # not visible before that daily candle closed
    assert not f.trend_up[: first_up].any()


def test_features_are_causal():
    """Indicators at index i must not change when future candles are appended/removed."""
    p = P()
    c = candles(trending(500, noise=0.01))
    d = candles(trending(120, noise=0.02), tf_ms=D1, start=T0 - 40 * D1)
    full = st.compute_features(c, p, d)
    for k in (260, 333, 499):
        cut = st.compute_features(c[:k], p, d)         # daily history may extend further: still no future leakage
        for name in ("rsi", "macd_hist", "atr", "ema_fast", "ema_slow"):
            a, b = getattr(full, name)[:k], getattr(cut, name)
            assert np.allclose(a, b, equal_nan=True), name
        assert np.array_equal(full.trend_up[:k], cut.trend_up)


# ───────────────────────── Tech-Analyst: breakout mode ─────────────────────────
def _breakout_series():
    base = np.r_[np.linspace(100, 130, 220), 130 + np.sin(np.linspace(0, 6, 60)) * 3]   # uptrend then range
    spike = np.r_[base, 143.0, 144.5]            # breakout candle(s)
    return spike


def _feat(closes, p, up=True):
    f = st.compute_features(candles(closes), p)
    f.trend_up[:] = up
    f.trend_down[:] = not up
    return f


def test_breakout_buy_when_all_conditions_hold():
    p = P(breakout_rsi_max=100.0)
    f = _feat(_breakout_series(), p)
    i = f.n - 2
    sig = st.entry_signal(f, i, p)
    assert sig.action == "BUY", [(c.key, c.ok, c.detail) for c in sig.checks]
    assert all(c.ok for c in sig.checks) and sig.score == 100.0
    assert st.wants_entry(f, i, p) is True


def test_breakout_blocked_without_uptrend_and_explains_why():
    p = P(breakout_rsi_max=100.0)
    f = _feat(_breakout_series(), p, up=False)
    sig = st.entry_signal(f, f.n - 2, p)
    assert sig.action == "WAIT" and "Aufwärtstrend" in sig.summary
    assert next(c for c in sig.checks if c.key == "trend").ok is False
    assert st.wants_entry(f, f.n - 2, p) is False


def test_breakout_blocked_when_rsi_overheated():
    p = P(breakout_rsi_max=50.5)
    f = _feat(_breakout_series(), p)
    sig = st.entry_signal(f, f.n - 2, p)
    assert next(c for c in sig.checks if c.key == "room").ok is False and sig.action == "WAIT"


def test_breakout_needs_a_new_high():
    p = P(breakout_rsi_max=100.0)
    f = _feat(_breakout_series(), p)
    sig = st.entry_signal(f, 200, p)           # somewhere in the middle of the trend: no new 50-bar high this candle? (check flag)
    flags = st._entry_flags(f, 200, p)
    assert sig.action == ("BUY" if all(flags[:5]) else "WAIT")
    # a falling market never produces a breakout
    f2 = _feat(np.linspace(200, 100, 300), p)
    assert not any(st.wants_entry(f2, i, p) for i in range(f2.n))


def test_not_enough_data_waits_gracefully():
    p = P()
    f = st.compute_features(candles(trending(20)), p)
    sig = st.entry_signal(f, 19, p)
    assert sig.action == "WAIT" and sig.checks[0].key == "data"
    assert st.wants_entry(f, 19, p) is False and st.wants_exit(f, 19, p) is None
    empty = st.compute_features(np.empty((0, 6)), p)
    assert empty.n == 0 and st.entry_signal(empty, 0, p).action == "WAIT"


# ───────────────────────── Tech-Analyst: dip mode ─────────────────────────
def _dip_series():
    up = np.linspace(100, 160, 230)
    dip = np.linspace(160, 140, 15)              # pullback pushes RSI down
    rebound = np.linspace(140, 148, 6)           # turning up
    return np.r_[up, dip, rebound]


def test_dip_mode_buys_the_turn_after_an_oversold_pullback():
    p = P(mode="dip", rsi_buy_zone=45.0, rsi_lookback=12, rsi_max_entry=70.0)
    f = _feat(_dip_series(), p)
    hits = [i for i in range(f.n) if st.wants_entry(f, i, p)]
    assert hits, "expected at least one dip entry"
    i = hits[0]
    sig = st.entry_signal(f, i, p)
    assert sig.action == "BUY" and "Rücksetzer" in sig.summary
    assert f.rsi[i] > f.rsi[i - 1] and np.nanmin(f.rsi[i - 11:i + 1]) <= 45.0


def test_wants_entry_matches_entry_signal_everywhere(rng):
    for mode in ("breakout", "dip"):
        p = P(mode=mode, breakout_rsi_max=100.0)
        f = _feat(trending(400, drift=0.0015, noise=0.015, seed=3), p)
        for i in range(f.n):
            assert (st.entry_signal(f, i, p).action == "BUY") == st.wants_entry(f, i, p), (mode, i)


# ───────────────────────── exits ─────────────────────────
def test_signal_exits_are_off_by_default_and_work_when_enabled():
    c = np.r_[np.linspace(100, 200, 200), np.linspace(200, 196, 6)]
    p_off = P()
    f = _feat(c, p_off)
    assert all(st.wants_exit(f, i, p_off) is None for i in range(f.n))
    assert st.exit_signal(f, f.n - 1, p_off).action == "HOLD"
    p_on = P(exit_on_overbought=True, rsi_overbought=70.0)
    reasons = [st.wants_exit(f, i, p_on) for i in range(f.n)]
    assert "overbought" in reasons
    j = reasons.index("overbought")
    assert st.exit_signal(f, j, p_on).action == "SELL"
    p_tb = P(exit_on_trend_break=True)
    f.trend_down[-1] = True
    assert st.wants_exit(f, f.n - 1, p_tb) == "trend_break"


# ───────────────────────── Trend-Scout ─────────────────────────
def test_scout_classifies_and_gates():
    p = P()
    f = _feat(trending(300, drift=0.002), p)
    i = f.n - 1
    r = st.scout_report("BTC/EUR", f, i, p, price=float(f.close[i]) * 1.001, spread_pct=0.02)
    assert r.trend == "up" and r.tradable and r.rank == pytest.approx((f.close[i] / f.close[i - 180] - 1) * 100)
    wide = st.scout_report("BTC/EUR", f, i, p, spread_pct=2.0)
    assert not wide.tradable and any("Spread" in n for n in wide.notes)
    old = st.scout_report("BTC/EUR", f, i, p, data_age_sec=999, max_age_sec=180)
    assert not old.tradable
    f.trend_up[i], f.trend_down[i] = False, True
    assert st.scout_report("X", f, i, p).trend == "down"
    f.trend_down[i] = False
    assert st.scout_report("X", f, i, p).trend == "side"
    short = st.compute_features(candles(trending(5)), p)
    assert st.scout_report("X", short, 4, p).trend == "unknown"


# ───────────────────────── Risk manager ─────────────────────────
RULES = st.Rules(min_amount=6e-5, min_cost=0.45, amount_step=1e-8)


def test_position_size_loses_exactly_the_risk_budget_at_the_stop():
    p = P(max_position_pct=1.0, max_total_exposure=1.0)
    s = st.position_size(equity=10_000, cash=10_000, exposure=0, price=50_000, atr=500, p=p, rules=RULES)
    assert s.qty > 0 and s.capped_by == "risk" and s.reason == ""
    loss_at_stop = s.qty * (50_000 - s.stop) + s.qty * 50_000 * p.fee_rate + s.qty * s.stop * p.fee_rate
    assert loss_at_stop == pytest.approx(100.0, rel=1e-6)         # 1 % of 10 000
    assert s.stop == pytest.approx(50_000 - 5 * 500)
    assert s.risk_amount == pytest.approx(loss_at_stop)


@pytest.mark.parametrize("kw,capped", [
    (dict(equity=10_000, cash=10_000, exposure=0, price=100, atr=0.1), "position"),      # tiny ATR -> position cap 30 %
    (dict(equity=10_000, cash=10_000, exposure=7_900, price=100, atr=0.1), "exposure"),   # only 100 left of the 80 % cap
    (dict(equity=10_000, cash=50, exposure=0, price=100, atr=0.1), "cash"),
])
def test_position_size_caps(kw, capped):
    s = st.position_size(p=P(), rules=st.Rules(0, 0, 1e-8), **kw)
    assert s.capped_by == capped and s.qty > 0
    if capped == "position":
        assert s.notional <= 3000 + 1e-6
    if capped == "exposure":
        assert s.notional <= 100 + 1e-6
    if capped == "cash":
        assert s.notional * (1 + P().fee_rate) <= 50 + 1e-6


def test_position_size_rounds_down_and_respects_exchange_minimums():
    s = st.position_size(equity=1_000, cash=1_000, exposure=0, price=68_000, atr=1_500, p=P(),
                         rules=st.Rules(6e-5, 0.45, 1e-5))
    assert s.qty > 0 and abs(s.qty / 1e-5 - round(s.qty / 1e-5)) < 1e-6 and s.qty * 68_000 <= 1_000 * 0.30 + 1e-6
    tiny = st.position_size(equity=20, cash=20, exposure=0, price=68_000, atr=1_500, p=P(), rules=RULES)
    assert tiny.qty == 0 and "Minimum" in tiny.reason
    nocash = st.position_size(equity=1_000, cash=0, exposure=0, price=100, atr=2, p=P(), rules=RULES)
    assert nocash.qty == 0 and "Guthaben" in nocash.reason


@pytest.mark.parametrize("kw", [dict(price=float("nan")), dict(atr=float("nan")), dict(atr=0.0), dict(price=-5.0),
                                dict(equity=0.0), dict(atr=1e9)])
def test_position_size_refuses_bad_inputs(kw):
    base = dict(equity=10_000, cash=10_000, exposure=0, price=100.0, atr=2.0)
    base.update(kw)
    s = st.position_size(p=P(), rules=RULES, **base)
    assert s.qty == 0 and s.reason


def test_trailing_stop_only_ratchets_up():
    p = P(stop_atr_mult=5.0)
    stop = st.initial_stop(100, 2, p)
    assert stop == 90
    stop = st.trail_stop(stop, 110, 2, p)          # high 110 -> 100
    assert stop == 100
    assert st.trail_stop(stop, 105, 2, p) == 100    # price retreats -> unchanged
    assert st.trail_stop(stop, 120, 5, p) == 100    # ATR exploded -> would be 95 -> never lowered
    assert st.trail_stop(stop, float("nan"), 2, p) == 100 and st.trail_stop(stop, 120, 0, p) == 100


def test_circuit_breakers():
    assert st.daily_loss_hit(10_000, 9_690, 3.0) is True and st.daily_loss_hit(10_000, 9_710, 3.0) is False
    assert st.drawdown_hit(12_000, 10_100, 15.0) is True and st.drawdown_hit(12_000, 10_300, 15.0) is False
    assert st.daily_loss_hit(0, 100, 3.0) is False and st.drawdown_hit(0, 100, 15.0) is False


def test_floor_to_step_has_no_float_noise():
    assert floor_to_step(0.123456789, 1e-8) == 0.12345678
    assert floor_to_step(0.3, 0.1) == 0.3                     # 0.3/0.1 = 2.9999999999999996 in floats
    assert floor_to_step(1.999, 0.01) == 1.99
    assert floor_to_step(5, 0) == 5.0
