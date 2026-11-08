"""Agent 2 - THE SENTIMENT ANALYST.

Simulated hype filter: there is no social-media scraping. The score is derived
from observable *market* behaviour (volume spike, buy dominance, number of
unique buyers) and *metadata* (socials/website, paid promotion), exactly the
signals human degens eyeball. It also flags suspicious bot/wash trading.
"""
from __future__ import annotations

from ..models import Pair, SentimentReport
from ..util import scale
from .base import Agent
from .metrics import volume_spike


class SentimentAnalyst(Agent):
    id = "sentiment"
    name = "Sentiment-Analyst"
    role = "Hype, Volumen-Spikes & Social-Signale"
    icon = "pulse"

    def analyze(self, p: Pair) -> SentimentReport:
        spike = volume_spike(p)
        age = p.age_min()
        buyers = p.buyers_m5 or 0
        crowd_raw = buyers if buyers > 0 else p.buys_m5 * 0.5
        n_social = len(set(p.socials)) + (1 if p.websites else 0)
        dominance = 0.6 * scale(p.buy_ratio_m5, 0.50, 0.72) + 0.4 * scale(p.buy_ratio_h1, 0.50, 0.62)
        if 3 <= age <= 180:
            fresh = 1.0
        elif age <= 720:
            fresh = 0.6
        else:
            fresh = 0.3
        parts = {
            "spike": scale(spike, 1.0, 4.0),
            "crowd": scale(crowd_raw, 5, 60),
            "dominance": dominance,
            "social": scale(n_social, 0, 3),
            "promo": 1.0 if p.boosts > 0 else 0.0,
            "fresh": fresh,
        }
        score = 100.0 * (0.22 * parts["spike"] + 0.20 * parts["crowd"] + 0.25 * parts["dominance"]
                         + 0.15 * parts["social"] + 0.08 * parts["promo"] + 0.10 * parts["fresh"])
        warnings: list[str] = []
        if buyers > 0 and p.txns_m5 >= 60 and (buyers + p.sellers_m5) < 0.12 * p.txns_m5:
            score *= 0.6
            warnings.append("Verdacht auf Bot-/Wash-Trading (wenige Wallets, sehr viele Trades)")
        if n_social == 0:
            warnings.append("keine Website/Socials hinterlegt")
        score = max(0.0, min(100.0, score))
        verdict = "bullish" if score >= 65 else "neutral" if score >= 50 else "bearish"
        who = f"{buyers} Wallets" if buyers else f"{p.buys_m5} Käufe"
        summary = (f"Hype {score:.0f} · Buy-Druck {p.buy_ratio_m5 * 100:.0f}% · Vol ×{spike:.1f} · {who}"
                   + (" · Promo" if p.boosts > 0 else ""))
        return SentimentReport(mint=p.mint, score=score, verdict=verdict, summary=summary, warnings=warnings, parts=parts)
