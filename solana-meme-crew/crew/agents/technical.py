"""Agent 3 - THE TECH ANALYST: momentum + entry timing.

Maths on a self-recorded price/volume series (one sample per fresh data
version) combined with the exchange's own 5m/1h statistics:
  * rate of change (ROC) and acceleration
  * time-constant EMAs (fast 60 s / slow 240 s) -> trend gap
  * volume spike vs. lifetime/hourly average and volume acceleration
  * buy pressure (share of buys), pullback from the local 5-min peak
BUY only when momentum, volume and pressure agree AND the move is not already
over-extended or a falling knife.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional

import config

from ..events import EventBus
from ..models import Pair, TechReport
from ..util import pct_change, scale
from .base import Agent
from .metrics import volume_spike


@dataclass
class Sample:
    ts: float
    price: float
    vol_m5: float
    tx5: int
    liq: float


def _ema(samples: list[Sample], tau: float) -> float:
    ema = samples[0].price
    prev_t = samples[0].ts
    for s in samples[1:]:
        dt = max(0.0, s.ts - prev_t)
        alpha = 1.0 - math.exp(-dt / tau) if dt > 0 else 0.0
        ema += alpha * (s.price - ema)
        prev_t = s.ts
    return ema


class TechAnalyst(Agent):
    id = "tech"
    name = "Tech-Analyst"
    role = "Momentum & Kaufzeitpunkt"
    icon = "chart"

    def __init__(self, bus: EventBus, step_delay: float = 0.0) -> None:
        super().__init__(bus, step_delay)
        self.samples: dict[str, deque[Sample]] = {}

    def forget(self, mint: str) -> None:
        self.samples.pop(mint, None)

    def update(self, p: Pair) -> None:
        price = p.price_sol
        if price <= 0:
            return
        buf = self.samples.setdefault(p.mint, deque(maxlen=160))
        if buf:
            last = buf[-1]
            if p.ts - last.ts < 1.0:
                return
            unchanged = price == last.price and p.vol_m5 == last.vol_m5 and p.txns_m5 == last.tx5
            if unchanged and p.ts - last.ts < 25.0:      # cached API response - not a new data point
                return
        buf.append(Sample(p.ts, price, p.vol_m5, p.txns_m5, p.liquidity_usd))

    def analyze(self, p: Pair) -> TechReport:
        cfg = config
        buf = list(self.samples.get(p.mint, ()))
        n = len(buf)
        price = p.price_sol
        reasons: list[str] = []

        # momentum: exchange 5m change, refined by own samples (~2 min back)
        mom = p.chg_m5
        roc_s: Optional[float] = None
        if n >= 2:
            ref = next((s for s in reversed(buf) if p.ts - s.ts >= 100.0), None)
            if ref is not None and ref.price > 0:
                roc_s = pct_change(price, ref.price)
        mom_used = max(mom, roc_s) if roc_s is not None else mom

        # trend: EMA gap
        gap = 0.0
        if n >= 4:
            fast, slow = _ema(buf, 60.0), _ema(buf, 240.0)
            gap = pct_change(fast, slow)
            trend = scale(gap, 0.0, 4.0)
        else:
            trend = 0.5 if (p.chg_m5 > 0 and p.chg_h1 > 0) else 0.2

        # volume: spike vs average and acceleration vs ~1-2 min ago
        spike = volume_spike(p)
        accel = 0.0
        ref_v = next((s for s in reversed(buf) if p.ts - s.ts >= 50.0 and s.vol_m5 > 0), None)
        if ref_v is not None:
            accel = p.vol_m5 / ref_v.vol_m5
        spike_eff = max(spike, accel)

        # pressure + pullback + liquidity trend
        bp = p.buy_ratio_m5
        recent = [s.price for s in buf if p.ts - s.ts <= 300.0] + [price]
        peak = max(recent) if recent else price
        pullback = (peak - price) / peak * 100.0 if peak > 0 else 0.0
        liq_peak = max([s.liq for s in buf] + [p.liquidity_usd]) if buf else p.liquidity_usd
        liq_falling = liq_peak > 0 and p.liquidity_usd < 0.8 * liq_peak

        score = 100.0 * (0.30 * scale(mom_used, 2.0, 30.0) + 0.28 * scale(spike_eff, 1.0, 4.0)
                         + 0.22 * scale(bp, 0.50, 0.72) + 0.20 * trend)
        overext = p.chg_m5 > cfg.MAX_CHASE_M5_PCT
        knife = pullback > cfg.MAX_PULLBACK_FROM_PEAK_PCT
        falling = p.chg_m5 < -3.0
        if overext:
            score -= 30
            reasons.append(f"schon +{p.chg_m5:.0f}% in 5 Min – nicht hinterherlaufen")
        if knife:
            score -= 35
            reasons.append(f"{pullback:.0f}% unter dem 5-Min-Hoch (fallendes Messer)")
        if falling:
            score -= 25
            reasons.append(f"Kurs fällt ({p.chg_m5:+.1f}% / 5 Min)")
        if liq_falling:
            score -= 20
            reasons.append("Liquidität sinkt")
        score = max(0.0, min(100.0, score))

        if overext or knife or liq_falling or p.chg_m5 < -8.0:
            signal = "AVOID"
        elif (score >= cfg.TECH_MIN_SCORE and spike_eff >= cfg.MIN_VOLUME_SPIKE and bp >= cfg.MIN_BUY_PRESSURE
              and p.chg_m5 > 0 and not falling):
            signal = "BUY"
            reasons.append("Momentum + Volumen + Käuferdruck bestätigt")
        else:
            signal = "WAIT"
            if spike_eff < cfg.MIN_VOLUME_SPIKE:
                reasons.append(f"Volumen-Spike ×{spike_eff:.1f} < ×{cfg.MIN_VOLUME_SPIKE}")
            if bp < cfg.MIN_BUY_PRESSURE:
                reasons.append(f"Käuferdruck {bp * 100:.0f}% < {cfg.MIN_BUY_PRESSURE * 100:.0f}%")
            if score < cfg.TECH_MIN_SCORE:
                reasons.append(f"Score {score:.0f} < {cfg.TECH_MIN_SCORE}")
        summary = (f"Tech {score:.0f} · Momentum {mom_used:+.0f}% · Vol ×{spike_eff:.1f} · "
                   f"Buy {bp * 100:.0f}% · EMA {gap:+.1f}%")
        return TechReport(mint=p.mint, score=score, signal=signal, summary=summary, vol_spike=spike_eff,
                          buy_pressure=bp, momentum_pct=mom_used, ema_gap_pct=gap, pullback_pct=pullback,
                          samples=n, reasons=reasons)
