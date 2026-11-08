"""The ledger: cash, positions, trade history, equity curve and statistics.

All amounts are in SOL (USD is only a display conversion). Costs include fees,
so the PnL shown on the dashboard is what really arrived in the wallet.
"""
from __future__ import annotations

from typing import Any, Optional

from ..models import Fill, Pair, Position, TradeRecord, from_dict
from ..util import new_id, utc_day

TRADES_MAX = 600
EQUITY_MAX = 6000
EQUITY_KEEP_FINE = 2000


class Portfolio:
    def __init__(self, mode: str, start_equity_sol: float, ts: float) -> None:
        self.mode = mode
        self.created_at = ts
        self.cash_sol = float(start_equity_sol)
        self.initial_equity_sol = float(start_equity_sol)
        self.positions: dict[str, Position] = {}
        self.trades: list[TradeRecord] = []
        self.equity: list[list[float]] = []          # [[ts, equity_sol], ...]
        self.realized_pnl_sol = 0.0
        self.fees_sol = 0.0
        self.wins = 0
        self.losses = 0
        self.best_pct = 0.0
        self.worst_pct = 0.0
        self.closed_count = 0
        self.consecutive_losses = 0
        self.day_key = utc_day(ts)
        self.day_start_equity = float(start_equity_sol)
        self.day_realized = 0.0
        self.peak_equity = float(start_equity_sol)
        self.max_drawdown_pct = 0.0
        self.blacklist: dict[str, float] = {}
        self.secured_usdc = 0.0
        self.desired_running = False
        self.pause_until = 0.0
        self.pause_reason = ""
        self.hold_seconds_total = 0.0

    def rebase(self, equity_sol: float, ts: float) -> None:
        """Set the PnL / daily-limit / drawdown baselines (used for a brand-new live
        portfolio on its first wallet sync - otherwise 'total profit' would be vs. 0)."""
        self.initial_equity_sol = float(equity_sol)
        self.day_start_equity = float(equity_sol)
        self.peak_equity = float(equity_sol)
        self.created_at = ts
        self.equity = []

    @property
    def is_fresh(self) -> bool:
        return not self.trades and not self.positions and self.initial_equity_sol <= 0

    # ── valuation ───────────────────────────────────────────────────────
    def open_positions(self) -> list[Position]:
        return [p for p in self.positions.values() if p.status == "open"]

    def positions_value(self) -> float:
        return sum(p.qty * p.last_price_sol for p in self.open_positions())

    def cost_exposure(self) -> float:
        return sum(p.cost_sol for p in self.open_positions())

    def equity_sol(self, sol_usd: float = 0.0) -> float:
        usdc = self.secured_usdc / sol_usd if (sol_usd and sol_usd > 0) else 0.0
        return self.cash_sol + self.positions_value() + usdc

    # ── bookkeeping ─────────────────────────────────────────────────────
    def mark(self, pos: Position, price_sol: float, liquidity_usd: float = 0.0) -> None:
        if price_sol and price_sol > 0:
            pos.last_price_sol = price_sol
            if price_sol > pos.peak_price_sol:
                pos.peak_price_sol = price_sol
        if liquidity_usd and liquidity_usd > 0:
            pos.last_liquidity_usd = liquidity_usd
            if liquidity_usd > pos.peak_liquidity_usd:
                pos.peak_liquidity_usd = liquidity_usd

    def record_buy(self, fill: Fill, pair: Pair, *, decimals: int, stop_price: float,
                   scores: Optional[dict[str, float]] = None, ts: float) -> Position:
        cost = fill.sol_amount + fill.fee_sol
        price = fill.price_sol if fill.price_sol > 0 else (fill.sol_amount / fill.token_amount if fill.token_amount else 0.0)
        pos = Position(
            id=new_id("p_"), mint=pair.mint, symbol=pair.symbol or pair.mint[:5], name=pair.name,
            decimals=decimals, pair_address=pair.pair_address, image_url=pair.image_url,
            opened_at=ts, entry_price_sol=price, qty=fill.token_amount, cost_sol=cost, initial_cost_sol=cost,
            peak_price_sol=price, stop_price_sol=stop_price, last_price_sol=price,
            entry_liquidity_usd=pair.liquidity_usd, peak_liquidity_usd=pair.liquidity_usd,
            last_liquidity_usd=pair.liquidity_usd, fees_sol=fill.fee_sol,
            entry_signature=fill.signature, simulated=fill.simulated, entry_scores=dict(scores or {}),
        )
        self.positions[pos.id] = pos
        self.cash_sol -= cost
        self.fees_sol += fill.fee_sol
        self._add_trade(TradeRecord(
            id=new_id("t_"), ts=ts, side="BUY", mint=pos.mint, symbol=pos.symbol, price_sol=price,
            qty=fill.token_amount, sol_amount=fill.sol_amount, fee_sol=fill.fee_sol, reason="Einstieg",
            signature=fill.signature, simulated=fill.simulated, position_id=pos.id, image_url=pos.image_url,
            entry_price_sol=price,
        ))
        return pos

    def record_sell(self, pos: Position, fill: Fill, reason: str, ts: float) -> tuple[TradeRecord, bool, float]:
        """Returns (trade, position_closed, total_position_pnl_sol or 0 while still open)."""
        qty_before = pos.qty
        sold = min(fill.token_amount, qty_before) if qty_before > 0 else 0.0
        frac = (sold / qty_before) if qty_before > 0 else 1.0
        cost_part = pos.cost_sol * frac
        proceeds_net = fill.sol_amount - fill.fee_sol
        pnl = proceeds_net - cost_part
        pnl_pct = (pnl / cost_part * 100.0) if cost_part > 0 else 0.0
        pos.qty = max(0.0, qty_before - sold)
        pos.cost_sol = max(0.0, pos.cost_sol - cost_part)
        pos.realized_sol += proceeds_net
        pos.fees_sol += fill.fee_sol
        self.cash_sol += proceeds_net
        self.realized_pnl_sol += pnl
        self.day_realized += pnl
        self.fees_sol += fill.fee_sol
        price = fill.price_sol if fill.price_sol > 0 else (fill.sol_amount / fill.token_amount if fill.token_amount else 0.0)
        trade = TradeRecord(
            id=new_id("t_"), ts=ts, side="SELL", mint=pos.mint, symbol=pos.symbol, price_sol=price,
            qty=sold, sol_amount=fill.sol_amount, fee_sol=fill.fee_sol, pnl_sol=pnl, pnl_pct=pnl_pct,
            reason=reason, signature=fill.signature, simulated=fill.simulated, position_id=pos.id,
            image_url=pos.image_url, entry_price_sol=pos.entry_price_sol,
        )
        self._add_trade(trade)
        closed = pos.qty <= 1e-12 or frac >= 0.999
        total = 0.0
        if closed:
            pos.qty = 0.0
            pos.status = "closed"
            pos.closed_at = ts
            pos.close_reason = reason
            total = pos.realized_sol - pos.initial_cost_sol
            total_pct = (total / pos.initial_cost_sol * 100.0) if pos.initial_cost_sol > 0 else 0.0
            self.closed_count += 1
            self.hold_seconds_total += max(0.0, ts - pos.opened_at)
            if total > 0:
                self.wins += 1
                self.consecutive_losses = 0
            else:
                self.losses += 1
                self.consecutive_losses += 1
            self.best_pct = max(self.best_pct, total_pct)
            self.worst_pct = min(self.worst_pct, total_pct)
            self.positions.pop(pos.id, None)
        return trade, closed, total

    def _add_trade(self, t: TradeRecord) -> None:
        self.trades.append(t)
        if len(self.trades) > TRADES_MAX:
            del self.trades[: len(self.trades) - TRADES_MAX]

    # ── time-based state ────────────────────────────────────────────────
    def sample_equity(self, ts: float, value: float, min_gap: float = 4.0) -> None:
        if self.equity and ts - self.equity[-1][0] < min_gap:
            self.equity[-1] = [round(ts, 1), round(value, 6)]
        else:
            self.equity.append([round(ts, 1), round(value, 6)])
        if len(self.equity) > EQUITY_MAX:
            older = self.equity[:-EQUITY_KEEP_FINE][::2]
            self.equity = older + self.equity[-EQUITY_KEEP_FINE:]
        if value > self.peak_equity:
            self.peak_equity = value
        if self.peak_equity > 0:
            dd = (self.peak_equity - value) / self.peak_equity * 100.0
            self.max_drawdown_pct = max(self.max_drawdown_pct, dd)

    def drawdown_pct(self, equity: float) -> float:
        return max(0.0, (self.peak_equity - equity) / self.peak_equity * 100.0) if self.peak_equity > 0 else 0.0

    def roll_day(self, ts: float, equity: float) -> bool:
        key = utc_day(ts)
        if key != self.day_key:
            self.day_key = key
            self.day_start_equity = equity
            self.day_realized = 0.0
            return True
        return False

    def blacklist_mint(self, mint: str, until: float) -> None:
        self.blacklist[mint] = max(until, self.blacklist.get(mint, 0.0))

    def is_blacklisted(self, mint: str, ts: float) -> bool:
        until = self.blacklist.get(mint)
        if until is None:
            return False
        if ts >= until:
            self.blacklist.pop(mint, None)
            return False
        return True

    # ── stats ───────────────────────────────────────────────────────────
    def stats(self) -> dict[str, Any]:
        n = self.wins + self.losses
        return {
            "closed": self.closed_count,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": (self.wins / n * 100.0) if n else 0.0,
            "realized_pnl_sol": self.realized_pnl_sol,
            "fees_sol": self.fees_sol,
            "best_pct": self.best_pct,
            "worst_pct": self.worst_pct,
            "avg_hold_s": (self.hold_seconds_total / self.closed_count) if self.closed_count else 0.0,
            "max_drawdown_pct": self.max_drawdown_pct,
            "consecutive_losses": self.consecutive_losses,
        }

    # ── persistence ─────────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "mode": self.mode,
            "created_at": self.created_at,
            "cash_sol": self.cash_sol,
            "initial_equity_sol": self.initial_equity_sol,
            "positions": [p.to_dict() for p in self.open_positions()],
            "trades": [t.__dict__ for t in self.trades],
            "equity": self.equity,
            "realized_pnl_sol": self.realized_pnl_sol,
            "fees_sol": self.fees_sol,
            "wins": self.wins, "losses": self.losses,
            "best_pct": self.best_pct, "worst_pct": self.worst_pct,
            "closed_count": self.closed_count,
            "consecutive_losses": self.consecutive_losses,
            "day_key": self.day_key, "day_start_equity": self.day_start_equity, "day_realized": self.day_realized,
            "peak_equity": self.peak_equity, "max_drawdown_pct": self.max_drawdown_pct,
            "blacklist": self.blacklist,
            "secured_usdc": self.secured_usdc,
            "desired_running": self.desired_running,
            "pause_until": self.pause_until, "pause_reason": self.pause_reason,
            "hold_seconds_total": self.hold_seconds_total,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Portfolio":
        pf = cls(d.get("mode", "paper"), float(d.get("initial_equity_sol", 0.0)), float(d.get("created_at", 0.0)))
        pf.cash_sol = float(d.get("cash_sol", pf.cash_sol))
        pf.positions = {}
        for pd in d.get("positions", []):
            pos = from_dict(Position, pd)
            pos.exiting = False
            if pos.status == "open" and pos.qty > 0:
                pf.positions[pos.id] = pos
        pf.trades = [from_dict(TradeRecord, td) for td in d.get("trades", [])]
        pf.equity = [list(map(float, row)) for row in d.get("equity", []) if isinstance(row, (list, tuple)) and len(row) == 2]
        for k in ("realized_pnl_sol", "fees_sol", "best_pct", "worst_pct", "day_start_equity", "day_realized",
                  "peak_equity", "max_drawdown_pct", "secured_usdc", "pause_until", "hold_seconds_total"):
            if k in d:
                setattr(pf, k, float(d[k]))
        for k in ("wins", "losses", "closed_count", "consecutive_losses"):
            if k in d:
                setattr(pf, k, int(d[k]))
        pf.day_key = d.get("day_key", pf.day_key)
        pf.blacklist = {str(k): float(v) for k, v in (d.get("blacklist") or {}).items()}
        pf.desired_running = bool(d.get("desired_running", False))
        pf.pause_reason = str(d.get("pause_reason", ""))
        return pf
