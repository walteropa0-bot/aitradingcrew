"""Shared, pure market metrics used by Sentiment + Tech."""
from __future__ import annotations

from ..models import Pair


def volume_spike(p: Pair) -> float:
    """Volume of the last 5 minutes relative to the average 5-minute volume.

    >1 means activity is accelerating. For pools younger than an hour the
    average is taken over the pool's lifetime (so 'first minutes' pumps register).
    """
    age = p.age_min()
    if age >= 60:
        baseline = p.vol_h1 / 12.0
    else:
        baseline = p.vol_h1 / max(age / 5.0, 1.0)
    if baseline <= 0:
        return 2.0 if p.vol_m5 > 0 else 0.0
    return p.vol_m5 / baseline


def txn_velocity(p: Pair) -> float:
    age = p.age_min()
    base = (p.buys_h1 + p.sells_h1) / (12.0 if age >= 60 else max(age / 5.0, 1.0))
    return (p.txns_m5 / base) if base > 0 else 0.0
