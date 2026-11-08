"""Agent 4 - THE RISK MANAGER.

Owns every number that can lose money:
  * exact position size (risk-per-trade, hard SOL cap, % of capital, pool share,
    total exposure, price-impact limit)
  * slippage limits for entries / exits
  * HARD trailing stop that only ever ratchets upwards (tiered, tightens with profit)
  * partial take-profit, stale-position exit, rug-pull / crash alarms
  * circuit breakers: daily loss limit, loss streak pause, drawdown kill-switch
All decision methods are pure functions of their inputs -> unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import config

from ..models import ExitOrder, Pair, Position, RiskDecision
from ..trading.executor import model_impact_pct
from ..trading.portfolio import Portfolio
from ..util import clamp, next_utc_midnight
from .base import Agent


@dataclass
class Halt:
    reason: str
    until: float
    hard: bool = False      # hard = needs a manual restart (kill switch)
    kind: str = "daily"     # daily | drawdown | streak


class RiskManager(Agent):
    id = "risk"
    name = "Risikomanager"
    role = "Positionsgröße, Slippage & Trailing-Stop"
    icon = "shield"

    # ── stops ───────────────────────────────────────────────────────────
    @staticmethod
    def trail_pct_for(peak_gain_pct: float) -> float:
        tiers = sorted(config.TRAILING_TIERS, key=lambda t: t[0])
        trail = float(tiers[0][1])
        for thr, pct in tiers:
            if peak_gain_pct >= thr:
                trail = float(pct)
        return trail

    @staticmethod
    def initial_stop(entry_price: float) -> float:
        return entry_price * (1.0 - config.INITIAL_STOP_LOSS_PCT / 100.0)

    def update_stop(self, pos: Position) -> float:
        """Ratchet the stop upward: max(old stop, hard initial stop, peak - trail)."""
        entry = pos.entry_price_sol
        if entry <= 0 or pos.peak_price_sol <= 0:
            return pos.stop_price_sol
        peak_gain = (pos.peak_price_sol / entry - 1.0) * 100.0
        trail = self.trail_pct_for(peak_gain)
        cand = pos.peak_price_sol * (1.0 - trail / 100.0)
        pos.stop_price_sol = max(pos.stop_price_sol, self.initial_stop(entry), cand)
        return pos.stop_price_sol

    def is_trailing(self, pos: Position) -> bool:
        return pos.stop_price_sol > self.initial_stop(pos.entry_price_sol) * 1.0005

    # ── sizing + approval ───────────────────────────────────────────────
    def size_position(self, *, capital: float, cash: float, cost_exposure: float, pair: Pair, sol_usd: float) -> tuple[float, str]:
        stop_frac = max(0.01, config.INITIAL_STOP_LOSS_PCT / 100.0)
        limits = {
            "Risiko pro Trade": capital * config.RISK_PER_TRADE_PCT / 100.0 / stop_frac,
            "Positions-Obergrenze": min(config.MAX_POSITION_SOL, capital * config.MAX_POSITION_PCT / 100.0),
            "Gesamt-Exposure": capital * config.MAX_EXPOSURE_PCT / 100.0 - cost_exposure,
            "freies Kapital": min(cash - config.MIN_SOL_RESERVE, capital - cost_exposure),
        }
        if pair.liquidity_usd > 0 and sol_usd > 0:
            limits["Pool-Anteil"] = pair.liquidity_usd / 2.0 / sol_usd * config.MAX_POOL_SHARE_PCT / 100.0
        name, size = min(limits.items(), key=lambda kv: kv[1])
        return max(0.0, int(size * 10_000) / 10_000.0), name

    def approve_entry(
        self, *, pf: Portfolio, capital: float, cash: float, pair: Pair, sol_usd: float,
        live_price: Optional[float], signal_price: float, ts: float, pending: int = 0, reserved: float = 0.0,
    ) -> RiskDecision:
        cfg = config
        if pf.is_blacklisted(pair.mint, ts):
            return RiskDecision(False, "Token gesperrt (Cooldown nach Verlust)")
        if any(p.mint == pair.mint for p in pf.open_positions()):
            return RiskDecision(False, "bereits investiert")
        if len(pf.open_positions()) + pending >= cfg.MAX_OPEN_POSITIONS:
            return RiskDecision(False, f"Maximal {cfg.MAX_OPEN_POSITIONS} Positionen gleichzeitig")
        ref = live_price if (live_price and live_price > 0) else signal_price
        if ref <= 0:
            return RiskDecision(False, "kein verlässlicher Preis")
        if live_price and signal_price > 0:
            drift = (live_price / signal_price - 1.0) * 100.0
            if drift > cfg.MAX_ENTRY_DRIFT_PCT:
                return RiskDecision(False, f"Preis schon {drift:+.1f}% davongelaufen (Datenverzug) – kein Hinterherlaufen")
            if drift < -cfg.MAX_ENTRY_DRIFT_PCT:
                return RiskDecision(False, f"Preis fällt gerade ({drift:+.1f}%) – Einstieg abgesagt")
        cost_exposure = pf.cost_exposure() + reserved      # capital already committed to in-flight buys
        size, limiter = self.size_position(capital=capital, cash=cash, cost_exposure=cost_exposure, pair=pair, sol_usd=sol_usd)
        impact = model_impact_pct(size, pair.liquidity_usd, sol_usd)
        if impact > cfg.MAX_PRICE_IMPACT_PCT and pair.liquidity_usd > 0 and sol_usd > 0:
            pool_sol = pair.liquidity_usd / 2.0 / sol_usd
            lim = cfg.MAX_PRICE_IMPACT_PCT
            size = max(0.0, int(lim * pool_sol / (100.0 - lim) * 10_000) / 10_000.0)
            impact = model_impact_pct(size, pair.liquidity_usd, sol_usd)
            limiter = "Price-Impact-Limit"
        if size < cfg.MIN_TRADE_SOL:
            return RiskDecision(False, f"Positionsgröße {size:.3f} SOL zu klein ({limiter} begrenzt)")
        slip = int(clamp((impact * 1.5 + 2.0) * 100.0, 150, cfg.BUY_SLIPPAGE_BPS))
        return RiskDecision(True, f"{size:.3f} SOL · Stop −{cfg.INITIAL_STOP_LOSS_PCT:.0f}% · Slippage ≤{slip / 100:.1f}% (begrenzt durch: {limiter})",
                            sol_amount=size, slippage_bps=slip, stop_price=self.initial_stop(ref),
                            est_impact_pct=impact, stop_loss_pct=cfg.INITIAL_STOP_LOSS_PCT)

    # ── position supervision ────────────────────────────────────────────
    def evaluate_position(self, pos: Position, price: float, liquidity_usd: float, prev_price: float, ts: float) -> Optional[ExitOrder]:
        """Return an exit order (or None). Priority: rug/crash > stop > take-profit > stale."""
        cfg = config
        if price <= 0:
            return None
        # 1) rug-pull / crash alarms -> panic exit (accept high slippage, speed matters)
        if pos.peak_liquidity_usd > 0 and liquidity_usd > 0 and liquidity_usd <= pos.peak_liquidity_usd * (1 - cfg.RUG_LIQUIDITY_DROP_PCT / 100.0):
            drop = (1 - liquidity_usd / pos.peak_liquidity_usd) * 100
            return ExitOrder(pos.id, 1.0, f"RUG-ALARM: Liquidität −{drop:.0f}%", panic=True)
        if prev_price > 0 and price <= prev_price * (1 - cfg.CRASH_TICK_DROP_PCT / 100.0):
            drop = (1 - price / prev_price) * 100
            return ExitOrder(pos.id, 1.0, f"CRASH-ALARM: −{drop:.0f}% in einem Schritt", panic=True)
        # 2) hard / trailing stop
        if price <= pos.stop_price_sol:
            return ExitOrder(pos.id, 1.0, "Trailing-Stop" if self.is_trailing(pos) else "Stop-Loss")
        # 3) partial take-profit
        gain = pos.gain_pct()
        for idx, (thr, frac) in enumerate(cfg.PARTIAL_TAKE_PROFIT):
            if idx not in pos.tp_done and gain >= thr:
                return ExitOrder(pos.id, clamp(frac / 100.0, 0.01, 1.0), f"Take-Profit +{thr:.0f}%", kind="take_profit", stage=idx)
        # 4) stale position
        if ts - pos.opened_at >= cfg.STALE_AFTER_MIN * 60.0 and gain < cfg.STALE_MAX_GAIN_PCT:
            return ExitOrder(pos.id, 1.0, f"Zeit-Stop ({cfg.STALE_AFTER_MIN} Min ohne Bewegung)")
        return None

    @staticmethod
    def exit_slippage_bps(order: ExitOrder, attempts: int) -> int:
        if order.panic:
            return config.PANIC_SLIPPAGE_BPS
        bps = config.SELL_SLIPPAGE_BPS * (1.3 ** max(0, attempts))
        return int(min(bps, config.PANIC_SLIPPAGE_BPS))

    # ── circuit breakers ────────────────────────────────────────────────
    @staticmethod
    def risk_base(ref_equity: float, pf: Portfolio) -> float:
        if config.PAPER_TRADING:
            return ref_equity
        return min(ref_equity, config.LIVE_TRADING_CAPITAL_SOL + max(0.0, pf.realized_pnl_sol))

    def check_halts(self, pf: Portfolio, equity: float, ts: float) -> Optional[Halt]:
        cfg = config
        base = max(1e-9, self.risk_base(pf.day_start_equity, pf))
        day_pnl = equity - pf.day_start_equity
        if day_pnl <= -base * cfg.DAILY_LOSS_LIMIT_PCT / 100.0:
            return Halt(f"Tageslimit erreicht: {day_pnl / base * 100:.1f}% (Limit −{cfg.DAILY_LOSS_LIMIT_PCT:.0f}%) – Pause bis 00:00 UTC",
                        next_utc_midnight(ts), kind="daily")
        dd_base = max(1e-9, self.risk_base(pf.peak_equity, pf))
        dd = (pf.peak_equity - equity) / dd_base * 100.0
        if dd >= cfg.KILL_SWITCH_DRAWDOWN_PCT:
            return Halt(f"KILL-SWITCH: Drawdown {dd:.0f}% vom Hoch (Limit {cfg.KILL_SWITCH_DRAWDOWN_PCT:.0f}%) – neue Käufe gestoppt",
                        ts + 10 * 365 * 86400, hard=True, kind="drawdown")
        if pf.consecutive_losses >= cfg.MAX_CONSECUTIVE_LOSSES:
            return Halt(f"{pf.consecutive_losses} Verluste in Folge – {cfg.PAUSE_AFTER_LOSSES_MIN} Min Pause",
                        ts + cfg.PAUSE_AFTER_LOSSES_MIN * 60.0, kind="streak")
        return None
