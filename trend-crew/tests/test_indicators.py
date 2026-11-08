import json
import os

import numpy as np
import pytest

from crew import indicators as ind

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _candles():
    """Real Kraken BTC/EUR 1h candles (fetched with CCXT)."""
    with open(os.path.join(FIX, "kraken_btceur_1h.json")) as fh:
        rows = json.load(fh)["BTC/EUR"]
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5]


def test_sma_matches_numpy():
    x = np.arange(1.0, 21.0)
    out = ind.sma(x, 5)
    assert np.isnan(out[:4]).all() and out[4] == 3.0 and out[-1] == 18.0


def test_ema_seed_and_recursion():
    x = np.array([1.0, 2, 3, 4, 5, 6, 7, 8])
    out = ind.ema(x, 3)
    assert np.isnan(out[:2]).all()
    assert out[2] == pytest.approx(2.0)            # SMA seed
    assert out[3] == pytest.approx(3.0)            # (4-2)*0.5+2
    assert out[4] == pytest.approx(4.0)


def test_ema_skips_leading_nans():
    x = np.array([np.nan, np.nan, 1.0, 2, 3, 4, 5])
    out = ind.ema(x, 3)
    assert np.isnan(out[:4]).all() and out[4] == pytest.approx(2.0)


def test_short_inputs_give_nan_not_errors():
    for fn in (lambda x: ind.rsi(x, 14), lambda x: ind.ema(x, 20), lambda x: ind.macd(x)[0],
               lambda x: ind.atr(x, x, x, 14)):
        out = fn(np.array([1.0, 2.0, 3.0]))
        assert np.isnan(out).all()
    assert ind.rsi(np.array([])).size == 0


def test_rsi_bounds_and_extremes():
    up = np.linspace(1, 100, 60)
    down = up[::-1].copy()
    flat = np.full(60, 5.0)
    assert ind.rsi(up)[-1] == pytest.approx(100.0)
    assert ind.rsi(down)[-1] == pytest.approx(0.0)
    assert ind.rsi(flat)[-1] == 50.0
    r = ind.rsi(np.random.default_rng(1).normal(0, 3, 500).cumsum() + 1000)
    assert np.nanmin(r) >= 0 and np.nanmax(r) <= 100 and np.nanstd(r) > 5


def test_macd_histogram_is_line_minus_signal():
    _, _, _, _, c, _ = _candles()
    line, sig, hist = ind.macd(c)
    ok = ~np.isnan(hist)
    assert ok.sum() > 100
    assert np.allclose(hist[ok], (line - sig)[ok])
    assert np.isnan(line[:25]).all() and not np.isnan(line[25])
    assert np.isnan(sig[:33]).all() and not np.isnan(sig[33])


def test_atr_is_positive_and_starts_at_period():
    _, o, h, l, c, _ = _candles()
    a = ind.atr(h, l, c, 14)
    assert np.isnan(a[:14]).all() and not np.isnan(a[14])
    assert (a[14:] > 0).all()


talib = pytest.importorskip("talib", reason="TA-Lib nur als optionale Referenz")


def test_matches_talib_reference():
    _, o, h, l, c, _ = _candles()
    for period in (7, 14, 21):
        assert np.allclose(ind.rsi(c, period), talib.RSI(c, period), equal_nan=True, rtol=1e-9, atol=1e-9)
    for period in (10, 50, 200):
        a, b = ind.ema(c, period), talib.EMA(c, period)
        assert np.allclose(a, b, equal_nan=True, rtol=1e-9, atol=1e-9)
    assert np.allclose(ind.atr(h, l, c, 14), talib.ATR(h, l, c, 14), equal_nan=True, rtol=1e-9, atol=1e-9)
    line, sig, hist = ind.macd(c)
    tl, ts_, th = talib.MACD(c, 12, 26, 9)
    # TA-Lib seeds the fast EMA at a later bar than the textbook definition; the difference
    # decays exponentially and is gone after ~300 bars.
    warm = slice(300, None)
    assert np.allclose(line[warm], tl[warm], rtol=1e-6, atol=1e-6)
    assert np.allclose(sig[warm], ts_[warm], rtol=1e-6, atol=1e-6)
    assert np.allclose(hist[warm], th[warm], rtol=1e-6, atol=1e-6)
