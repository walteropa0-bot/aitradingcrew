import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

H4 = 4 * 3600 * 1000
D1 = 24 * 3600 * 1000
T0 = 1_700_006_400_000        # a Wednesday 00:00 UTC, aligned to 4h and 1d


def candles(closes, tf_ms=H4, start=T0, wick=0.004, vol=100.0):
    """Synthetic OHLCV rows [ts, o, h, l, c, v] from a close series (open = previous close)."""
    closes = np.asarray(closes, dtype=float)
    opens = np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) * (1 + wick)
    lows = np.minimum(opens, closes) * (1 - wick)
    ts = start + np.arange(len(closes)) * tf_ms
    return np.column_stack([ts, opens, highs, lows, closes, np.full(len(closes), vol)])


def trending(n, start=100.0, drift=0.002, noise=0.004, seed=1):
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(rng.normal(drift, noise, n)))


@pytest.fixture
def rng():
    return np.random.default_rng(7)


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """Retry back-offs must not slow the test-suite down."""
    import crew.exchange as cx
    monkeypatch.setattr(cx, "_sleep", lambda s: None)
