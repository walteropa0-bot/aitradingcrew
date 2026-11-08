"""Small helpers shared by all modules."""
from __future__ import annotations

import math
import re
import time
from decimal import ROUND_DOWN, Decimal


def now() -> float:
    return time.time()


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def is_num(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def floor_to_step(value: float, step: float) -> float:
    """Round `value` DOWN to a multiple of `step` (exchange lot size), free of float noise."""
    if step <= 0:
        return float(value)
    d_step = Decimal(str(step))
    d_val = Decimal(str(value))
    return float((d_val / d_step).to_integral_value(rounding=ROUND_DOWN) * d_step)


def de(x: float | None, nd: int = 2, sign: bool = False) -> str:
    """German number format: 1.234,56 (None/NaN -> '–')."""
    if x is None or not isinstance(x, (int, float)) or not math.isfinite(x):
        return "–"
    s = f"{x:+,.{nd}f}" if sign else f"{x:,.{nd}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def eur(x: float | None, quote: str = "EUR", nd: int = 2, sign: bool = False) -> str:
    sym = {"EUR": "€", "USD": "$", "USDT": "$", "USDC": "$"}.get(quote, quote)
    return f"{de(x, nd, sign)} {sym}"


def pct(x: float | None, nd: int = 2, sign: bool = True) -> str:
    return f"{de(x, nd, sign)} %"


def ago(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or not math.isfinite(seconds):
        return "–"
    s = int(seconds)
    if s < 5:
        return "gerade eben"
    if s < 60:
        return f"vor {s} s"
    if s < 3600:
        return f"vor {s // 60} min"
    if s < 86400:
        return f"vor {s // 3600} h {s % 3600 // 60:02d} min"
    return f"vor {s // 86400} d"


def duration(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    if s < 86400:
        return f"{s // 3600} h {s % 3600 // 60:02d} min"
    return f"{s // 86400} d {s % 86400 // 3600} h"


_URL_SECRET = re.compile(r"((?:api[_-]?key|secret|token|signature|password|apikey)=)[^&\s]+", re.I)


def redact_url(text: str) -> str:
    return _URL_SECRET.sub(r"\1***", text)


TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
                     "6h": 21600, "8h": 28800, "12h": 43200, "1d": 86400}


def timeframe_seconds(tf: str) -> int:
    try:
        return TIMEFRAME_SECONDS[tf]
    except KeyError:
        raise ValueError(f"Unbekannter Zeitrahmen: {tf!r} (erlaubt: {', '.join(TIMEFRAME_SECONDS)})") from None
