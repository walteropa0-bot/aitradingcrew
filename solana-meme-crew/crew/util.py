"""Small, dependency-free helpers shared across the project."""
from __future__ import annotations

import datetime as _dt
import math
import re
import time
import uuid
from typing import Any, Optional


def now() -> float:
    return time.time()


def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


def scale(x: Optional[float], lo: float, hi: float) -> float:
    """Linearly map x from [lo, hi] to [0, 1] (clamped). None -> 0."""
    if x is None or hi == lo:
        return 0.0
    return clamp((x - lo) / (hi - lo), 0.0, 1.0)


def sf(x: Any, default: float = 0.0) -> float:
    """Safe float: tolerant of None / '' / 'abc' / NaN / inf."""
    try:
        if x is None or x == "":
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except (TypeError, ValueError):
        return default


def si(x: Any, default: int = 0) -> int:
    try:
        if x is None or x == "":
            return default
        return int(float(x))
    except (TypeError, ValueError, OverflowError):
        return default


def pct_change(new: float, old: float) -> float:
    """Percent change old -> new (0 when old is not positive)."""
    if old is None or old <= 0:
        return 0.0
    return (new / old - 1.0) * 100.0


def short_addr(addr: str, n: int = 4) -> str:
    if not addr:
        return ""
    return addr if len(addr) <= 2 * n + 1 else f"{addr[:n]}…{addr[-n:]}"


def utc_day(ts: Optional[float] = None) -> str:
    ts = now() if ts is None else ts
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d")


def next_utc_midnight(ts: Optional[float] = None) -> float:
    ts = now() if ts is None else ts
    d = _dt.datetime.fromtimestamp(ts, _dt.timezone.utc)
    nxt = (d + _dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return nxt.timestamp()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


def fmt_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 90:
        return f"{seconds}s"
    m = seconds // 60
    if m < 120:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h {m}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"


_SECRET_QUERY = re.compile(r"((?:api[-_]?key|apikey|token|key|secret|auth)=)[^&\s'\"]+", re.I)


def redact_url(text: str) -> str:
    """Mask API keys that live in query strings of URLs (never log those)."""
    return _SECRET_QUERY.sub(r"\1***", text or "")


def ui_amount(raw: int | float, decimals: int) -> float:
    return float(raw) / (10 ** decimals)


def raw_amount(ui: float, decimals: int) -> int:
    return int(round(ui * (10 ** decimals)))
