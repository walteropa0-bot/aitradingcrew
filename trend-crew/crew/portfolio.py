"""The ledger: cash, open positions, trade history, equity curve, statistics.

It only records what the broker confirmed (a Fill). In LIVE mode it is the bot's own book-keeping:
the bot manages exactly these positions and never touches other coins on the exchange account.
Everything is JSON-serialisable so it can be persisted after every change.
"""
from __future__ import annotations

import math
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Optional

SCHEMA = 1
MAX_EQUITY_POINTS = 6000
MAX_TRADES = 1000


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str                 # buy | sell
    qty: float                # base amount actually bought (net of base-currency fees) / sold
    price: float              # average execution price
    fee: float                # fee in quote currency
    quote_amount: float       # buy: total quote paid (incl. fee) | sell: total quote received (after fee)
    ts: float
    order_id: str = ""
    simulated: bool = False


@dataclass
class Position:
    id: str
    symbol: str
    qty: float
    entry_price: float        # effective price per unit incl. fee
    entry_ts: float
    entry_bar: float
    cost: float               # quote paid in total (incl. fee)
    fee_in: float
    stop: float
    initial_stop: float
    highest: float
    atr: float
    risk_amount: float

    def value(self, mark: float) -> float:
        return self.qty * mark


@dataclass
class TradeRec:
    id: str
    ts: float
    symbol: str
    side: str
    qty: float
    price: float
    fee: float
    notional: float
    pnl: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""
    mode: str = "paper"
    order_id: str = ""
    position_id: str = ""


class EquityHistory:
    """(ts, equity) points with bounded memory: when full, the older half is thinned out."""

    def __init__(self, points: Optional[list] = None, min_gap: float = 10.0) -> None:
        self.points: list[list[float]] = [list(map(float, p)) for p in (points or [])]
        self.min_gap = min_gap

    def add(self, ts: float, equity: float, force: bool = False) -> None:
        if not (math.isfinite(ts) and math.isfinite(equity)):
            return
        if self.points:
            last_ts, last_eq = self.points[-1]
            if ts <= last_ts:
                return
            if not force and ts - last_ts < self.min_gap and abs(equity - last_eq) < max(1e-9, abs(last_eq) * 1e-4):
                return
        self.points.append([ts, equity])
        if len(self.points) > MAX_EQUITY_POINTS:
            half = len(self.points) // 2
            self.points = self.points[:half:2] + self.points[half:]

    def tail(self, since_ts: float = 0.0) -> list[list[float]]:
        return [p for p in self.points if p[0] >= since_ts]


class Portfolio:
    def __init__(self, start_capital: float, mode: str) -> None:
        self.mode = mode
        self.start_capital = float(start_capital)
        self.cash = float(start_capital)
        self.positions: dict[str, Position] = {}
        self.trades: deque[TradeRec] = deque(maxlen=MAX_TRADES)
        self.history = EquityHistory()
        self.cooldowns: dict[str, float] = {}          # symbol -> unix ts until which no new entry
        self.last_bar: dict[str, float] = {}           # symbol -> candle ts last evaluated
        self.peak = float(start_capital)
        self.day_key = ""
        self.day_start_equity = float(start_capital)
        self.running = False
        self.halted = ""                               # reason text while the bot is stopped by Not-Aus / kill-switch
        self.created_ts = 0.0
        self.realized = 0.0
        self.fees = 0.0
        self.wins = 0
        self.losses = 0
        self.gross_win = 0.0
        self.gross_loss = 0.0
        self.best = 0.0
        self.worst = 0.0
        self.pending: dict[str, dict] = {}             # ambiguous live orders awaiting reconciliation

    # ───────────── valuation ─────────────
    def equity(self, marks: dict[str, float]) -> float:
        total = self.cash
        for sym, pos in self.positions.items():
            total += pos.qty * marks.get(sym, pos.entry_price)
        return total

    def exposure(self, marks: dict[str, float]) -> float:
        return sum(pos.qty * marks.get(sym, pos.entry_price) for sym, pos in self.positions.items())

    # ───────────── bookkeeping ─────────────
    def open_position(self, fill: Fill, *, stop: float, atr: float, risk_amount: float, bar_ts: float, reason: str = "signal") -> Position:
        if fill.side != "buy" or fill.qty <= 0:
            raise ValueError("open_position braucht einen Kauf")
        if fill.symbol in self.positions:
            raise ValueError(f"{fill.symbol}: Position existiert bereits")
        self.cash -= fill.quote_amount
        self.fees += fill.fee
        pos = Position(id=uuid.uuid4().hex[:10], symbol=fill.symbol, qty=fill.qty, entry_price=fill.quote_amount / fill.qty,
                       entry_ts=fill.ts, entry_bar=bar_ts, cost=fill.quote_amount, fee_in=fill.fee, stop=stop,
                       initial_stop=stop, highest=fill.price, atr=atr, risk_amount=risk_amount)
        self.positions[fill.symbol] = pos
        self.trades.append(TradeRec(uuid.uuid4().hex[:10], fill.ts, fill.symbol, "buy", fill.qty, fill.price, fill.fee,
                                    fill.qty * fill.price, reason=reason, mode=self.mode, order_id=fill.order_id, position_id=pos.id))
        return pos

    def close_position(self, fill: Fill, reason: str, cooldown_until: float = 0.0) -> TradeRec:
        """Apply a sell. A partial sell reduces the position proportionally; a full sell removes it."""
        pos = self.positions.get(fill.symbol)
        if pos is None or fill.side != "sell":
            raise ValueError("close_position braucht eine Position und einen Verkauf")
        sold = min(fill.qty, pos.qty)
        share = sold / pos.qty
        cost_part = pos.cost * share
        proceeds = fill.quote_amount * (sold / fill.qty) if fill.qty > 0 else fill.quote_amount
        pnl = proceeds - cost_part
        self.cash += fill.quote_amount
        self.fees += fill.fee
        self.realized += pnl
        if pnl > 0:
            self.wins += 1
            self.gross_win += pnl
        else:
            self.losses += 1
            self.gross_loss += -pnl
        self.best, self.worst = max(self.best, pnl), min(self.worst, pnl)
        rec = TradeRec(uuid.uuid4().hex[:10], fill.ts, fill.symbol, "sell", sold, fill.price, fill.fee, sold * fill.price,
                       pnl=pnl, pnl_pct=(pnl / cost_part * 100.0) if cost_part > 0 else 0.0, reason=reason,
                       mode=self.mode, order_id=fill.order_id, position_id=pos.id)
        self.trades.append(rec)
        if share >= 0.999999:
            del self.positions[fill.symbol]
            if cooldown_until:
                self.cooldowns[fill.symbol] = cooldown_until
        else:
            pos.qty -= sold
            pos.cost -= cost_part
            pos.entry_price = pos.cost / pos.qty if pos.qty > 0 else pos.entry_price
        return rec

    def drop_position(self, symbol: str, price: float, ts: float, reason: str) -> Optional[TradeRec]:
        """Remove a position WITHOUT an order (dust below the exchange minimum, or coins that vanished
        from the account). Booked at `price` with no fee so the books stay honest."""
        pos = self.positions.get(symbol)
        if pos is None:
            return None
        fill = Fill(symbol, "sell", pos.qty, price, 0.0, pos.qty * price, ts, simulated=True)
        return self.close_position(fill, reason)

    # ───────────── time-based state ─────────────
    def roll_day(self, day_key: str, equity: float) -> bool:
        if day_key != self.day_key:
            self.day_key = day_key
            self.day_start_equity = equity
            return True
        return False

    def mark(self, ts: float, equity: float, force: bool = False) -> None:
        self.peak = max(self.peak, equity)
        self.history.add(ts, equity, force)

    # ───────────── statistics ─────────────
    def stats(self) -> dict[str, Any]:
        n = self.wins + self.losses
        return {
            "trades": n, "wins": self.wins, "losses": self.losses,
            "win_rate": (self.wins / n * 100.0) if n else 0.0,
            "profit_factor": (self.gross_win / self.gross_loss) if self.gross_loss > 0 else (float("inf") if self.gross_win > 0 else 0.0),
            "avg_win": (self.gross_win / self.wins) if self.wins else 0.0,
            "avg_loss": (-self.gross_loss / self.losses) if self.losses else 0.0,
            "best": self.best, "worst": self.worst, "fees": self.fees, "realized": self.realized,
        }

    # ───────────── persistence ─────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA, "mode": self.mode, "start_capital": self.start_capital, "cash": self.cash,
            "positions": [asdict(p) for p in self.positions.values()],
            "trades": [asdict(t) for t in self.trades], "history": self.history.points,
            "cooldowns": self.cooldowns, "last_bar": self.last_bar, "peak": self.peak, "day_key": self.day_key,
            "day_start_equity": self.day_start_equity, "running": self.running, "halted": self.halted,
            "created_ts": self.created_ts, "realized": self.realized, "fees": self.fees, "wins": self.wins,
            "losses": self.losses, "gross_win": self.gross_win, "gross_loss": self.gross_loss, "best": self.best,
            "worst": self.worst, "pending": self.pending,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any], mode: str) -> "Portfolio":
        if not isinstance(d, dict) or d.get("schema") != SCHEMA or d.get("mode") != mode:
            raise ValueError("Gespeicherter Stand passt nicht (anderes Format oder anderer Modus)")
        pf = cls(float(d["start_capital"]), mode)
        pf.cash = float(d["cash"])
        for raw in d.get("positions", []):
            pos = Position(**raw)
            if not all(math.isfinite(x) for x in (pos.qty, pos.entry_price, pos.stop, pos.highest)) or pos.qty <= 0:
                raise ValueError(f"Position {pos.symbol} ist beschädigt")
            pf.positions[pos.symbol] = pos
        for raw in d.get("trades", []):
            pf.trades.append(TradeRec(**raw))
        pf.history = EquityHistory(d.get("history", []))
        pf.cooldowns = {k: float(v) for k, v in d.get("cooldowns", {}).items()}
        pf.last_bar = {k: float(v) for k, v in d.get("last_bar", {}).items()}
        for k in ("peak", "day_start_equity", "created_ts", "realized", "fees", "gross_win", "gross_loss", "best", "worst"):
            setattr(pf, k, float(d.get(k, getattr(pf, k))))
        pf.day_key = str(d.get("day_key", ""))
        pf.running = bool(d.get("running", False))
        pf.halted = str(d.get("halted", ""))
        pf.wins, pf.losses = int(d.get("wins", 0)), int(d.get("losses", 0))
        pf.pending = dict(d.get("pending", {}))
        if not math.isfinite(pf.cash):
            raise ValueError("Kassenstand ist beschädigt")
        return pf
