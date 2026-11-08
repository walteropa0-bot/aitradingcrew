"""Technical indicators in plain NumPy (no TA-Lib needed).

Conventions follow TA-Lib / TradingView so results can be cross-checked:
  * EMA is seeded with the SMA of the first `period` values.
  * RSI and ATR use Wilder's smoothing, seeded with a simple average.
  * Every function returns an array as long as its input; positions before the
    first valid value are NaN.
"""
from __future__ import annotations

import numpy as np


def _as_float(a) -> np.ndarray:
    return np.asarray(a, dtype=np.float64)


def sma(values, period: int) -> np.ndarray:
    v = _as_float(values)
    out = np.full(v.shape, np.nan)
    if period < 1 or len(v) < period:
        return out
    csum = np.cumsum(np.insert(v, 0, 0.0))
    out[period - 1:] = (csum[period:] - csum[:-period]) / period
    return out


def ema(values, period: int) -> np.ndarray:
    """Exponential moving average; leading NaNs in the input are skipped."""
    v = _as_float(values)
    out = np.full(v.shape, np.nan)
    valid = np.flatnonzero(~np.isnan(v))
    if period < 1 or len(valid) < period:
        return out
    first = int(valid[0])
    start = first + period - 1
    if start >= len(v):
        return out
    k = 2.0 / (period + 1.0)
    prev = float(np.mean(v[first:first + period]))
    out[start] = prev
    for i in range(start + 1, len(v)):
        prev = (v[i] - prev) * k + prev
        out[i] = prev
    return out


def rsi(close, period: int = 14) -> np.ndarray:
    """Wilder RSI. A completely flat series yields 50 (TA-Lib would yield 0)."""
    c = _as_float(close)
    out = np.full(c.shape, np.nan)
    if period < 1 or len(c) <= period:
        return out
    delta = np.diff(c)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = float(np.mean(gain[:period]))
    avg_loss = float(np.mean(loss[:period]))
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period
        out[i + 1] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    total = avg_gain + avg_loss
    if total <= 0.0:
        return 50.0
    return min(100.0, max(0.0, 100.0 * avg_gain / total))


def macd(close, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (macd line, signal line, histogram)."""
    c = _as_float(close)
    line = ema(c, fast) - ema(c, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def true_range(high, low, close) -> np.ndarray:
    h, l, c = _as_float(high), _as_float(low), _as_float(close)
    tr = np.full(h.shape, np.nan)
    if len(h) == 0:
        return tr
    tr[0] = h[0] - l[0]
    if len(h) > 1:
        prev = c[:-1]
        tr[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - prev), np.abs(l[1:] - prev)])
    return tr


def atr(high, low, close, period: int = 14) -> np.ndarray:
    """Wilder's Average True Range (first value at index `period`)."""
    tr = true_range(high, low, close)
    out = np.full(tr.shape, np.nan)
    if period < 1 or len(tr) <= period:
        return out
    prev = float(np.mean(tr[1:period + 1]))
    out[period] = prev
    for i in range(period + 1, len(tr)):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out
