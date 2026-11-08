import numpy as np
import pytest

from backtest import data as D
from backtest import engine as E
from crew import strategy as st
from tests.conftest import H4, T0, candles, trending

# fast settings: short trend EMAs and no RSI ceiling so synthetic ramps produce breakouts
FAST = dict(ema_fast=10, ema_slow=30, breakout_bars=20, breakout_rsi_max=100.0, cooldown_bars=3)


def run(series: dict, **kw):
    p = st.Params(**{**FAST, **kw.pop("p", {})})
    return E.simulate(series, p, **kw), p


def zigzag_uptrend(n=500, seed=2):
    return trending(n, drift=0.0015, noise=0.012, seed=seed)


def test_no_trades_on_a_dead_flat_market():
    res, _ = run({"AAA": candles(np.full(300, 100.0))})
    assert res.trades == [] and np.allclose(res.equity, 10_000.0)


def test_accounting_is_consistent_and_limits_hold():
    series = {s: candles(zigzag_uptrend(seed=k)) for k, s in enumerate(("AAA", "BBB", "CCC"), start=1)}
    res, p = run(series, capital=10_000.0)
    assert len(res.trades) >= 3
    assert res.equity[-1] == pytest.approx(10_000.0 + sum(t.pnl for t in res.trades), rel=1e-9)
    assert all(t.exit_ts > t.entry_ts and t.qty > 0 and t.fees > 0 for t in res.trades)
    assert np.all(res.equity > 0) and np.all(res.invested <= p.max_total_exposure + 0.02)
    # never more simultaneous positions than allowed
    edges = sorted([(t.entry_ts, 1) for t in res.trades] + [(t.exit_ts, -1) for t in res.trades], key=lambda e: (e[0], e[1]))
    open_now = peak = 0
    for _, d in edges:
        open_now += d
        peak = max(peak, open_now)
    assert peak <= p.max_open_positions


def test_frictionless_trade_pnl_equals_price_move():
    series = {"AAA": candles(zigzag_uptrend())}
    res, _ = run(series, p=dict(fee_rate=0.0, slippage=0.0))
    for t in res.trades:
        assert t.pnl == pytest.approx(t.qty * (t.exit_px - t.entry_px), rel=1e-9, abs=1e-6)
        assert t.fees == 0.0


def test_fees_and_slippage_reduce_the_result():
    series = {"AAA": candles(zigzag_uptrend())}
    free, _ = run(series, p=dict(fee_rate=0.0, slippage=0.0))
    costly, _ = run(series, p=dict(fee_rate=0.0026, slippage=0.0005))
    assert free.trades and costly.metrics["final_equity"] < free.metrics["final_equity"]


def test_gap_below_the_stop_fills_at_the_open_not_at_the_stop():
    c = candles(zigzag_uptrend(260, seed=4))
    res0, _ = run({"AAA": c}, p=dict(fee_rate=0.0, slippage=0.0))
    first = res0.trades[0]
    k = int(np.searchsorted(c[:, 0], first.exit_ts))            # candle where the first trade ended
    # rebuild: same market until just before the exit, then a violent gap down
    c2 = c[:k + 1].copy()
    prev_close = c2[k - 1, 4]
    c2[k, 1] = prev_close * 0.6                                 # open 40 % lower
    c2[k, 2] = c2[k, 1]; c2[k, 3] = c2[k, 1] * 0.99; c2[k, 4] = c2[k, 1]
    res, _ = run({"AAA": c2}, p=dict(fee_rate=0.0, slippage=0.0))
    stopped = [t for t in res.trades if t.reason == "stop" and t.exit_ts == c2[k, 0]]
    assert stopped, "expected a stop-out on the gap candle"
    assert stopped[0].exit_px == pytest.approx(c2[k, 1])        # filled at the (much lower) open


def test_simulation_is_causal():
    series_full = {"AAA": candles(trending(700, drift=0.0008, noise=0.015, seed=9))}
    cut = 520
    series_cut = {"AAA": series_full["AAA"][:cut]}
    full, _ = run(series_full)
    part, _ = run(series_cut)
    k = cut - 5
    a = [(t.entry_ts, t.exit_ts, round(t.qty, 9)) for t in full.trades if t.exit_ts < series_cut["AAA"][k, 0]]
    b = [(t.entry_ts, t.exit_ts, round(t.qty, 9)) for t in part.trades if t.reason != "end" and t.exit_ts < series_cut["AAA"][k, 0]]
    assert a == b and len(a) > 0
    n = np.searchsorted(full.ts, series_cut["AAA"][k, 0])
    assert np.allclose(full.equity[:n], part.equity[:n])


WHIPSAW = dict(risk_per_trade=0.5, stop_atr_mult=0.5, max_position_pct=1.0, max_total_exposure=1.0, cooldown_bars=1)


def test_kill_switch_stops_trading_and_limits_the_damage():
    market = {"AAA": candles(trending(600, drift=0.0004, noise=0.012, seed=5))}
    guarded, _ = run(market, p=WHIPSAW, kill_dd_pct=5.0, daily_loss_pct=50.0)
    naked, _ = run(market, p=WHIPSAW, enable_breakers=False)
    assert guarded.halted_at is not None and naked.halted_at is None
    assert all(t.entry_ts <= guarded.halted_at for t in guarded.trades)           # nothing bought after the halt
    assert guarded.metrics["max_drawdown_pct"] > -8.0                              # the brake caught the slide at ~5 %
    assert guarded.metrics["final_equity"] > naked.metrics["final_equity"] + 1_000
    assert guarded.equity[-1] == pytest.approx(guarded.equity[guarded.ts >= guarded.halted_at][-1])   # flat (cash) afterwards


def test_daily_loss_limit_pauses_entries_until_the_next_day():
    market = {"AAA": candles(trending(600, drift=0.0004, noise=0.012, seed=5))}
    res, _ = run(market, p=WHIPSAW, kill_dd_pct=90.0, daily_loss_pct=1.0)
    free, _ = run(market, p=WHIPSAW, enable_breakers=False)
    assert res.halted_at is None and len(res.trades) < len(free.trades)


def test_misaligned_series_are_rejected():
    a, b = candles(trending(100)), candles(trending(100), start=T0 + H4)
    with pytest.raises(ValueError):
        run({"AAA": a, "BBB": b})


# ───────────────────────── data helpers ─────────────────────────
def test_resample_1h_to_4h_and_drops_incomplete_buckets():
    one_h = candles(np.arange(1, 11, dtype=float), tf_ms=3_600_000, start=T0 + 3_600_000, wick=0.0)   # starts 1h into a 4h bucket
    out = D.resample(one_h, "4h")
    # first bucket is incomplete (3 candles), last bucket incomplete (2 candles) -> only the full middle bucket survives
    assert len(out) == 1
    assert out[0, 0] == T0 + H4 and out[0, 1] == one_h[3, 1] == 3.0 and out[0, 4] == 7.0 and out[0, 5] == 400.0
    assert out[0, 2] == one_h[3:7, 2].max() and out[0, 3] == one_h[3:7, 3].min()


def test_align_keeps_only_common_timestamps():
    a = candles(trending(10))
    b = candles(trending(10), start=T0 + 3 * H4)
    out = D.align({"A": a, "B": b})
    assert len(out["A"]) == len(out["B"]) == 7 and np.array_equal(out["A"][:, 0], out["B"][:, 0])


def test_metrics_basic_sanity():
    eq = np.array([100.0, 110.0, 99.0, 120.0])
    assert E._max_drawdown(eq) == pytest.approx(99.0 / 110.0 - 1.0)
    assert E._max_drawdown(np.array([])) == 0.0
