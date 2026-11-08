"""Event-driven portfolio backtest using the live strategy functions.

Execution model (deliberately conservative):
  * Signals are computed on CLOSED candles only. Orders fill at the NEXT candle's open
    (buy: + slippage, sell: - slippage) and pay the taker fee on every fill.
  * Trailing stop = chandelier (highest price - N x ATR), ratchets up only. Inside a candle the
    simulation assumes the worst order of events: if the candle's high would have raised the stop
    above that candle's low, the stop is considered hit. A gap below the stop fills at the open.
  * Same risk rules as the live bot: risk-based sizing, position/exposure caps, cooldown after
    a stop-out, daily-loss brake, drawdown kill-switch (stays in cash afterwards).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from crew import strategy as st

DAY_MS = 86_400_000


@dataclass
class Trade:
    symbol: str
    entry_ts: int
    exit_ts: int
    entry_px: float
    exit_px: float
    qty: float
    pnl: float
    pnl_pct: float
    fees: float
    reason: str
    bars: int


@dataclass
class Result:
    params: st.Params
    capital: float
    ts: np.ndarray
    equity: np.ndarray
    invested: np.ndarray                 # share of equity in positions per bar
    trades: list[Trade]
    halted_at: Optional[int] = None      # timestamp when the kill-switch fired (None = never)
    metrics: dict = field(default_factory=dict)


def _max_drawdown(eq: np.ndarray) -> float:
    if len(eq) == 0:
        return 0.0
    peak = np.maximum.accumulate(eq)
    return float(np.min(eq / peak - 1.0))


def metrics(ts: np.ndarray, eq: np.ndarray, invested: np.ndarray, trades: list[Trade], capital: float,
            bars_per_day: float) -> dict:
    if len(eq) < 2:
        return {}
    years = (ts[-1] - ts[0]) / (365.25 * DAY_MS)
    total = eq[-1] / capital - 1.0
    cagr = (eq[-1] / capital) ** (1 / years) - 1.0 if years > 0 and eq[-1] > 0 else float("nan")
    # daily returns for Sharpe
    day = (ts // DAY_MS).astype(np.int64)
    last_idx = np.r_[np.flatnonzero(np.diff(day)), len(day) - 1]
    d_eq = eq[last_idx]
    d_ret = np.diff(d_eq) / d_eq[:-1] if len(d_eq) > 2 else np.array([0.0])
    sharpe = float(np.mean(d_ret) / np.std(d_ret) * math.sqrt(365)) if np.std(d_ret) > 0 else 0.0
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_w = sum(t.pnl for t in wins)
    gross_l = -sum(t.pnl for t in losses)
    mdd = _max_drawdown(eq)
    return {
        "total_return_pct": total * 100.0, "cagr_pct": cagr * 100.0, "max_drawdown_pct": mdd * 100.0,
        "calmar": (cagr / abs(mdd)) if mdd < 0 and not math.isnan(cagr) else float("nan"),
        "sharpe": sharpe, "trades": len(trades),
        "win_rate_pct": 100.0 * len(wins) / len(trades) if trades else 0.0,
        "avg_win_pct": float(np.mean([t.pnl_pct for t in wins])) if wins else 0.0,
        "avg_loss_pct": float(np.mean([t.pnl_pct for t in losses])) if losses else 0.0,
        "profit_factor": gross_w / gross_l if gross_l > 0 else float("inf") if gross_w > 0 else 0.0,
        "avg_bars": float(np.mean([t.bars for t in trades])) if trades else 0.0,
        "exposure_pct": float(np.mean(invested)) * 100.0,
        "fees": sum(t.fees for t in trades), "years": years, "final_equity": float(eq[-1]),
    }


def simulate(data: dict[str, np.ndarray], p: st.Params, *, htf_data: Optional[dict[str, np.ndarray]] = None,
             capital: float = 10_000.0, rules: Optional[dict[str, st.Rules]] = None,
             enable_breakers: bool = True, daily_loss_pct: float = 3.0, kill_dd_pct: float = 15.0) -> Result:
    symbols = list(data)
    n = len(data[symbols[0]])
    for s in symbols:
        if len(data[s]) != n or not np.array_equal(data[s][:, 0], data[symbols[0]][:, 0]):
            raise ValueError("Zeitreihen müssen auf demselben Raster liegen (backtest.data.align)")
    feats = {s: st.compute_features(data[s], p, (htf_data or {}).get(s)) for s in symbols}
    ts_all = data[symbols[0]][:, 0].astype(np.int64)
    rules = rules or {s: st.Rules(0.0, 0.0, 1e-8) for s in symbols}

    cash = capital
    pos: dict[str, dict] = {}
    pending_buy: list[str] = []
    pending_sell: dict[str, str] = {}
    cooldown: dict[str, int] = {s: -1 for s in symbols}
    trades: list[Trade] = []
    eq_curve = np.full(n, np.nan)
    inv_curve = np.zeros(n)
    peak = capital
    day_id = -1
    day_start_eq = capital
    halted_at: Optional[int] = None
    slip, fee = p.slippage, p.fee_rate
    start = max(p.warmup, 2)

    def close_pos(sym: str, px: float, i: int, reason: str) -> None:
        nonlocal cash
        ps = pos.pop(sym)
        proceeds = ps["qty"] * px
        fee_out = proceeds * fee
        cash += proceeds - fee_out
        cost = ps["qty"] * ps["entry"] + ps["fee_in"]
        pnl = proceeds - fee_out - cost
        trades.append(Trade(sym, int(ts_all[ps["i"]]), int(ts_all[i]), ps["entry"], px, ps["qty"], pnl,
                            pnl / cost * 100.0, ps["fee_in"] + fee_out, reason, i - ps["i"]))
        if pnl < 0 or reason == "stop":
            cooldown[sym] = i + p.cooldown_bars

    for i in range(start, n):
        o = {s: feats[s].open[i] for s in symbols}
        # 1) fill orders decided at the previous close, at this candle's open
        for sym, why in list(pending_sell.items()):
            if sym in pos:
                close_pos(sym, o[sym] * (1.0 - slip), i, why)
        pending_sell.clear()
        if halted_at is None:
            equity_open = cash + sum(ps["qty"] * o[s] for s, ps in pos.items())
            exposure = sum(ps["qty"] * o[s] for s, ps in pos.items())
            for sym in pending_buy:
                if sym in pos or len(pos) >= p.max_open_positions:
                    continue
                f = feats[sym]
                fill = o[sym] * (1.0 + slip)
                size = st.position_size(equity=equity_open, cash=cash, exposure=exposure, price=fill,
                                        atr=float(f.atr[i - 1]), p=p, rules=rules[sym])
                if size.qty <= 0:
                    continue
                cost = size.qty * fill
                fee_in = cost * fee
                cash -= cost + fee_in
                exposure += cost
                pos[sym] = {"qty": size.qty, "entry": fill, "stop": size.stop, "highest": fill, "i": i, "fee_in": fee_in}
        pending_buy.clear()

        # 2) stop checks inside this candle (worst-case ordering, see module docstring)
        for sym in list(pos):
            f = feats[sym]
            ps = pos[sym]
            atr_prev = float(f.atr[i - 1])
            hi = max(ps["highest"], float(f.high[i]))
            stop_eff = st.trail_stop(ps["stop"], hi, atr_prev, p)
            if float(f.low[i]) <= stop_eff:
                fill = min(stop_eff, float(f.open[i])) * (1.0 - slip)
                close_pos(sym, fill, i, "stop")
            else:
                ps["highest"] = hi
                ps["stop"] = st.trail_stop(ps["stop"], hi, float(f.atr[i]), p)

        # 3) mark to market at the close
        eq = cash + sum(ps["qty"] * float(feats[s].close[i]) for s, ps in pos.items())
        eq_curve[i] = eq
        inv_curve[i] = (eq - cash) / eq if eq > 0 else 0.0
        peak = max(peak, eq)
        d = int(ts_all[i] // DAY_MS)
        if d != day_id:
            day_id, day_start_eq = d, eq

        # 4) circuit breakers
        entries_blocked = False
        if enable_breakers:
            if halted_at is None and st.drawdown_hit(peak, eq, kill_dd_pct):
                halted_at = int(ts_all[i])
                for sym in list(pos):
                    pending_sell[sym] = "kill_switch"
            entries_blocked = halted_at is not None or st.daily_loss_hit(day_start_eq, eq, daily_loss_pct)

        # 5) decide at the close (orders fill at the next open)
        for sym in symbols:
            f = feats[sym]
            if sym in pos:
                why = st.wants_exit(f, i, p)
                if why and sym not in pending_sell:
                    pending_sell[sym] = why
        if not entries_blocked:
            cands = []
            for sym in symbols:
                if sym in pos or cooldown[sym] > i:
                    continue
                f = feats[sym]
                if st.wants_entry(f, i, p):
                    rep = st.scout_report(sym, f, i, p)
                    if rep.tradable:
                        cands.append((rep.rank, sym))
            cands.sort(reverse=True)
            pending_buy = [s for _, s in cands]

    # liquidate whatever is still open at the last close (so the result is fully realised)
    last = n - 1
    for sym in list(pos):
        close_pos(sym, float(feats[sym].close[last]) * (1.0 - slip), last, "end")
    eq_curve[last] = cash
    valid = ~np.isnan(eq_curve)
    res = Result(p, capital, ts_all[valid], eq_curve[valid], inv_curve[valid], trades, halted_at)
    res.metrics = metrics(res.ts, res.equity, res.invested, trades, capital, p.bars_per_day)
    return res


def buy_and_hold(data: dict[str, np.ndarray], start_idx: int, capital: float = 10_000.0, fee: float = 0.0026) -> tuple:
    """Equal-weight buy&hold over the same period (fees on entry and exit). Returns (ts, equity)."""
    symbols = list(data)
    n = len(data[symbols[0]])
    per = capital / len(symbols)
    qty = {s: per * (1 - fee) / data[s][start_idx, 1] for s in symbols}      # buy at the open of the first bar
    eq = np.zeros(n - start_idx)
    for s in symbols:
        eq += qty[s] * data[s][start_idx:, 4]
    eq[-1] *= (1 - fee)
    return data[symbols[0]][start_idx:, 0], eq
