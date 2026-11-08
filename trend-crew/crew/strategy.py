"""The three mathematical "agents" as PURE functions (no I/O, no clock, no randomness).

The live engine and the backtester call exactly the same functions, so what you
backtest is what the bot trades.

  Trend-Scout   -> scout_report()   daily trend regime, volatility, ranking
  Tech-Analyst  -> entry_signal()   RSI + MACD confluence, two modes:
                     "breakout": new N-candle high, MACD momentum positive, RSI strong but not overheated
                     "dip":      RSI recovers from oversold, MACD turns up (buy the dip)
                   exit_signal()    optional: RSI overbought + MACD turn, trend break
  Risk manager  -> position_size()  exact size so a stop-out costs ~RISK_PER_TRADE
                   initial_stop(), trail_stop()  (a stop only ever moves UP)
                   daily_loss_hit(), drawdown_hit()  circuit breakers

Why "breakout" is the default: see backtest/README (the oversold-dip rule lost money after fees).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Optional

import numpy as np

import config
from . import indicators as ind
from .util import de, floor_to_step, timeframe_seconds


# ─────────────────────────────── parameters ───────────────────────────────
@dataclass(frozen=True)
class Params:
    mode: str = "breakout"                # breakout | dip
    timeframe: str = "4h"
    htf_timeframe: str = "1d"             # higher timeframe used for the trend regime (Trend-Scout)
    # Tech-Analyst
    rsi_period: int = 14
    breakout_bars: int = 50               # breakout mode: close above the highest high of the previous N candles
    breakout_rsi_min: float = 50.0
    breakout_rsi_max: float = 80.0
    require_macd_positive: bool = True
    rsi_buy_zone: float = 45.0            # dip mode: RSI must have been <= this recently ...
    rsi_lookback: int = 10                # ... within the last N candles, and now be rising
    rsi_max_entry: float = 60.0
    rsi_overbought: float = 80.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    ema_fast: int = 50                    # trend regime (daily EMAs when daily candles are available)
    ema_slow: int = 200
    use_trend_filter: bool = True
    exit_on_overbought: bool = False
    exit_on_trend_break: bool = False
    # Risk manager
    atr_period: int = 14
    stop_atr_mult: float = 5.0
    risk_per_trade: float = 0.01          # fraction of equity lost if the initial stop is hit
    max_position_pct: float = 0.30        # fraction of equity in one coin
    max_total_exposure: float = 0.80      # fraction of equity invested overall
    max_open_positions: int = 3
    cooldown_bars: int = 6
    min_atr_pct: float = 0.002            # ignore dead markets (ATR < 0.2 % of price)
    max_atr_pct: float = 0.12             # ... and absurdly wild ones
    max_spread_pct: float = 0.5
    # Costs (used for sizing and by the paper broker / backtest)
    fee_rate: float = 0.0026
    slippage: float = 0.0005

    @classmethod
    def from_config(cls) -> "Params":
        return cls(
            mode=config.STRATEGY_MODE, timeframe=config.TIMEFRAME, htf_timeframe=config.HTF_TIMEFRAME,
            rsi_period=config.RSI_PERIOD, breakout_bars=config.BREAKOUT_BARS,
            breakout_rsi_min=config.BREAKOUT_RSI_MIN, breakout_rsi_max=config.BREAKOUT_RSI_MAX,
            require_macd_positive=config.REQUIRE_MACD_POSITIVE,
            rsi_buy_zone=config.RSI_BUY_ZONE, rsi_lookback=config.RSI_LOOKBACK, rsi_max_entry=config.RSI_MAX_ENTRY,
            rsi_overbought=config.RSI_OVERBOUGHT,
            macd_fast=config.MACD_FAST, macd_slow=config.MACD_SLOW, macd_signal=config.MACD_SIGNAL,
            ema_fast=config.EMA_FAST, ema_slow=config.EMA_SLOW, use_trend_filter=config.USE_TREND_FILTER,
            exit_on_overbought=config.EXIT_ON_OVERBOUGHT, exit_on_trend_break=config.EXIT_ON_TREND_BREAK,
            atr_period=config.ATR_PERIOD, stop_atr_mult=config.STOP_ATR_MULT,
            risk_per_trade=config.RISK_PER_TRADE_PCT / 100.0, max_position_pct=config.MAX_POSITION_PCT / 100.0,
            max_total_exposure=config.MAX_TOTAL_EXPOSURE_PCT / 100.0, max_open_positions=config.MAX_OPEN_POSITIONS,
            cooldown_bars=config.COOLDOWN_BARS, max_spread_pct=config.MAX_SPREAD_PCT,
            fee_rate=config.FEE_RATE, slippage=config.SLIPPAGE_PCT / 100.0,
        )

    def with_(self, **kw) -> "Params":
        return replace(self, **kw)

    @property
    def warmup(self) -> int:
        """Candles needed before every indicator of the main timeframe is valid."""
        return max(self.macd_slow + self.macd_signal, self.rsi_period + self.rsi_lookback, self.atr_period,
                   self.breakout_bars, 60) + 2

    @property
    def bars_per_day(self) -> float:
        return 86400.0 / timeframe_seconds(self.timeframe)


# ─────────────────────────────── features ───────────────────────────────
@dataclass
class Features:
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    volume: np.ndarray
    rsi: np.ndarray
    macd: np.ndarray
    macd_signal: np.ndarray
    macd_hist: np.ndarray
    ema_fast: np.ndarray
    ema_slow: np.ndarray
    atr: np.ndarray
    trend_up: np.ndarray           # bool per candle: regime allows long entries
    trend_down: np.ndarray         # bool per candle: regime is a downtrend
    htf: bool = False              # True if the regime comes from the higher-timeframe (daily) candles

    @property
    def n(self) -> int:
        return len(self.close)


def _regime(ts: np.ndarray, tf_ms: int, htf_candles, p: Params) -> tuple[np.ndarray, np.ndarray]:
    """Daily trend regime mapped onto the main candles WITHOUT look-ahead: a main candle may only
    see higher-timeframe candles that were already closed when the main candle closed."""
    h = np.asarray(htf_candles, dtype=np.float64)
    n = len(ts)
    up = np.zeros(n, dtype=bool)
    down = np.zeros(n, dtype=bool)
    if h.ndim != 2 or len(h) < p.ema_slow + 1 or n == 0:
        return up, down
    hc = h[:, 4]
    e_fast, e_slow = ind.ema(hc, p.ema_fast), ind.ema(hc, p.ema_slow)
    ok = ~np.isnan(e_slow)
    h_up = ok & (hc > e_slow) & (e_fast > e_slow)
    h_down = ok & (hc < e_slow) & (e_fast < e_slow)
    htf_ms = timeframe_seconds(p.htf_timeframe) * 1000
    closed_at = h[:, 0] + htf_ms
    idx = np.searchsorted(closed_at, ts + tf_ms, side="right") - 1
    valid = idx >= 0
    safe = np.clip(idx, 0, None)
    return valid & h_up[safe], valid & h_down[safe]


def compute_features(candles, p: Params, htf_candles=None) -> Features:
    """candles: rows [ts_ms, open, high, low, close, volume] of CLOSED candles.
    htf_candles: daily candles for the trend regime (optional; without them the regime is computed
    from EMA fast/slow of the main candles)."""
    a = np.asarray(candles, dtype=np.float64)
    if a.ndim != 2 or a.shape[1] < 6:
        a = np.empty((0, 6))
    ts, o, h, l, c, v = (a[:, k] for k in range(6))
    line, sig, hist = ind.macd(c, p.macd_fast, p.macd_slow, p.macd_signal)
    e_fast, e_slow = ind.ema(c, p.ema_fast), ind.ema(c, p.ema_slow)
    if htf_candles is not None and len(htf_candles) > 0:
        up, down = _regime(ts, timeframe_seconds(p.timeframe) * 1000, htf_candles, p)
        use_htf = True
    else:
        ok = ~np.isnan(e_slow) & ~np.isnan(e_fast)
        up = ok & (c > e_slow) & (e_fast > e_slow)
        down = ok & (c < e_slow) & (e_fast < e_slow)
        use_htf = False
    return Features(ts=ts, open=o, high=h, low=l, close=c, volume=v,
                    rsi=ind.rsi(c, p.rsi_period), macd=line, macd_signal=sig, macd_hist=hist,
                    ema_fast=e_fast, ema_slow=e_slow, atr=ind.atr(h, l, c, p.atr_period),
                    trend_up=up, trend_down=down, htf=use_htf)


# ─────────────────────────────── signals ───────────────────────────────
@dataclass(frozen=True)
class Check:
    key: str
    label: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class Signal:
    action: str                   # BUY | SELL | HOLD | WAIT
    score: float                  # 0..100 = share of conditions met
    checks: tuple = ()
    summary: str = ""


def _valid(f: Features, i: int, p: Params) -> bool:
    if i < 1 or i >= f.n:
        return False
    vals = (f.rsi[i], f.rsi[i - 1], f.macd_hist[i], f.macd_hist[i - 1], f.atr[i], f.close[i])
    return not any(math.isnan(v) for v in vals)


def _entry_flags(f: Features, i: int, p: Params):
    """(trend_ok, setup_ok, momentum_ok, room_ok, vol_ok, info) for candle i; caller checks _valid first.
    `info` carries the numbers used for the human-readable explanation."""
    c = float(f.close[i])
    trend_ok = bool(f.trend_up[i]) if p.use_trend_filter else True
    atr_pct = float(f.atr[i]) / c if c > 0 else float("nan")
    vol_ok = bool((not math.isnan(atr_pct)) and p.min_atr_pct <= atr_pct <= p.max_atr_pct)
    if p.mode == "dip":
        lo_idx = max(0, i - p.rsi_lookback + 1)
        window = f.rsi[lo_idx:i + 1]
        rsi_low = float(np.nanmin(window)) if not np.isnan(window).all() else float("nan")
        setup_ok = bool((not math.isnan(rsi_low)) and rsi_low <= p.rsi_buy_zone and f.rsi[i] > f.rsi[i - 1])
        momentum_ok = bool(f.macd_hist[i] > f.macd_hist[i - 1])
        room_ok = bool(f.rsi[i] <= p.rsi_max_entry)
        info = {"rsi_low": rsi_low}
    else:
        n = p.breakout_bars
        prior_high = float(np.max(f.high[i - n:i])) if i >= n else float("nan")
        setup_ok = bool(i >= n and c > prior_high)
        momentum_ok = bool(f.macd_hist[i] > 0) if p.require_macd_positive else True
        room_ok = bool(p.breakout_rsi_min <= f.rsi[i] <= p.breakout_rsi_max)
        info = {"prior_high": prior_high}
    info["atr_pct"] = atr_pct
    return trend_ok, setup_ok, momentum_ok, room_ok, vol_ok, info


def wants_entry(f: Features, i: int, p: Params) -> bool:
    """Fast boolean used by the backtester (identical rule to entry_signal)."""
    if not _valid(f, i, p):
        return False
    t, s_, m, r, v, _ = _entry_flags(f, i, p)
    return t and s_ and m and r and v


def wants_exit(f: Features, i: int, p: Params) -> Optional[str]:
    """Fast version used by the backtester: a reason or None. (Both exits are optional and OFF by default:
    in backtests the ATR trailing stop alone worked better than RSI/trend exits.)"""
    if not _valid(f, i, p):
        return None
    if p.exit_on_overbought and f.rsi[i] >= p.rsi_overbought and f.macd_hist[i] < f.macd_hist[i - 1]:
        return "overbought"
    if p.exit_on_trend_break and bool(f.trend_down[i]):
        return "trend_break"
    return None


def entry_signal(f: Features, i: int, p: Params) -> Signal:
    """Tech-Analyst: should we BUY at the close of candle i? Returns a human-readable checklist."""
    if not _valid(f, i, p):
        return Signal("WAIT", 0.0, (Check("data", "Genug Kursdaten", False, f"Es werden mind. {p.warmup} Kerzen gebraucht"),),
                      "Sammle noch Kursdaten für die Indikatoren")
    trend_ok, setup_ok, momentum_ok, room_ok, vol_ok, info = _entry_flags(f, i, p)
    c = float(f.close[i])
    regime = "Tages-" if f.htf else ""
    trend_detail = (f"{regime}Trend aufwärts (EMA{p.ema_fast} über EMA{p.ema_slow})" if trend_ok
                    else (f"{regime}Trend abwärts" if f.trend_down[i] else f"{regime}Trend nicht eindeutig")) if p.use_trend_filter else "Trendfilter aus"
    vol_check = Check("vol", "Schwankung im Rahmen", vol_ok, f"ATR {de(info['atr_pct'] * 100, 2)} % vom Kurs")
    if p.mode == "dip":
        checks = (
            Check("trend", "Aufwärtstrend", trend_ok, trend_detail),
            Check("dip", "RSI aus Überverkauft", setup_ok,
                  f"RSI {de(f.rsi[i], 1)} (Tief {de(info['rsi_low'], 1)}, Zone ≤ {de(p.rsi_buy_zone, 0)}), "
                  f"{'steigt' if f.rsi[i] > f.rsi[i - 1] else 'fällt'}"),
            Check("macd", "MACD dreht nach oben", momentum_ok,
                  f"Histogramm {de(f.macd_hist[i], 2, True)} (vorher {de(f.macd_hist[i - 1], 2, True)})"),
            Check("room", "Nicht überkauft", room_ok, f"RSI {de(f.rsi[i], 1)} ≤ {de(p.rsi_max_entry, 0)}"),
            vol_check,
        )
        buy_text = "Kaufsignal: Rücksetzer im Aufwärtstrend dreht nach oben"
    else:
        ph = info["prior_high"]
        checks = (
            Check("trend", "Aufwärtstrend", trend_ok, trend_detail),
            Check("breakout", f"Neues {p.breakout_bars}-Kerzen-Hoch", setup_ok,
                  f"Kurs {de(c, 2)} vs. Hoch {de(ph, 2)} ({de((c / ph - 1) * 100, 2, True)} %)" if ph == ph else "Zu wenig Kerzen"),
            Check("macd", "MACD-Momentum positiv", momentum_ok,
                  f"Histogramm {de(f.macd_hist[i], 2, True)}" + ("" if p.require_macd_positive else " (nicht verlangt)")),
            Check("room", "RSI stark, nicht überhitzt", room_ok,
                  f"RSI {de(f.rsi[i], 1)} (Bereich {de(p.breakout_rsi_min, 0)}–{de(p.breakout_rsi_max, 0)})"),
            vol_check,
        )
        buy_text = f"Kaufsignal: Ausbruch auf neues {p.breakout_bars}-Kerzen-Hoch im Aufwärtstrend"
    met = sum(1 for k in checks if k.ok)
    score = 100.0 * met / len(checks)
    if met == len(checks):
        return Signal("BUY", score, checks, buy_text)
    missing = next(k for k in checks if not k.ok)
    return Signal("WAIT", score, checks, f"Wartet: {missing.label} fehlt")


def exit_signal(f: Features, i: int, p: Params) -> Signal:
    """Tech-Analyst: should an open position be closed at the close of candle i? (Only if the optional
    signal exits are switched on; otherwise the trailing stop alone protects the position.)"""
    if not _valid(f, i, p):
        return Signal("HOLD", 0.0, (), "Zu wenig Daten")
    ob = bool(f.rsi[i] >= p.rsi_overbought)
    turning = bool(f.macd_hist[i] < f.macd_hist[i - 1])
    broken = bool(f.trend_down[i])
    checks = (
        Check("overbought", "Überkauft + MACD dreht runter", p.exit_on_overbought and ob and turning,
              f"RSI {de(f.rsi[i], 1)} (ab {de(p.rsi_overbought, 0)}), Histogramm {'fällt' if turning else 'steigt'}"
              + ("" if p.exit_on_overbought else " – Regel aus")),
        Check("trend_break", "Trend gebrochen", p.exit_on_trend_break and broken,
              ("Abwärtstrend" if broken else "Trend intakt") + ("" if p.exit_on_trend_break else " – Regel aus")),
    )
    reason = wants_exit(f, i, p)
    if reason == "overbought":
        return Signal("SELL", 100.0, checks, "Verkaufssignal: überkauft und der MACD dreht nach unten")
    if reason == "trend_break":
        return Signal("SELL", 100.0, checks, "Verkaufssignal: Trend gebrochen")
    return Signal("HOLD", 0.0, checks, "Position halten – der Trailing-Stop schützt")


# ─────────────────────────────── scout ───────────────────────────────
@dataclass(frozen=True)
class ScoutReport:
    symbol: str
    price: float
    trend: str                    # up | down | side | unknown
    trend_pct: float              # EMA fast vs slow in %
    atr_pct: float
    change_pct: float             # move over the last ~24h in %
    spread_pct: Optional[float]
    rank: float                   # higher = stronger relative strength (decides who gets scarce capital first)
    tradable: bool
    notes: tuple = ()


def scout_report(symbol: str, f: Features, i: int, p: Params, *, price: float | None = None,
                 spread_pct: float | None = None, data_age_sec: float | None = None,
                 max_age_sec: float = 1e9) -> ScoutReport:
    """Trend-Scout: classify the market regime and decide whether the coin may be traded at all."""
    bpd = p.bars_per_day
    if i < 1 or i >= f.n or math.isnan(f.atr[i]):
        px = price if price else (float(f.close[i]) if 0 <= i < f.n else float("nan"))
        return ScoutReport(symbol, px, "unknown", 0.0, 0.0, 0.0, spread_pct, -1e9, False, ("zu wenig Kursdaten",))
    notes: list[str] = []
    c = float(f.close[i])
    px = price if (price and price > 0) else c
    if bool(f.trend_up[i]):
        trend = "up"
    elif bool(f.trend_down[i]):
        trend = "down"
    else:
        trend = "side"
    ef, es = float(f.ema_fast[i]), float(f.ema_slow[i])
    trend_pct = (ef / es - 1.0) * 100.0 if es == es and ef == ef and es > 0 else 0.0
    back = max(1, int(round(bpd)))
    change = (c / float(f.close[i - back]) - 1.0) * 100.0 if i - back >= 0 else 0.0
    month = max(1, int(round(bpd * 30)))
    rank = (c / float(f.close[i - month]) - 1.0) * 100.0 if i - month >= 0 else change
    atr_pct = float(f.atr[i]) / c * 100.0 if c > 0 else 0.0
    tradable = True
    if spread_pct is not None and spread_pct > p.max_spread_pct:
        tradable = False
        notes.append(f"Spread {de(spread_pct, 2)} % zu groß")
    if data_age_sec is not None and data_age_sec > max_age_sec:
        tradable = False
        notes.append("Kursdaten veraltet")
    if not (p.min_atr_pct * 100 <= atr_pct <= p.max_atr_pct * 100):
        tradable = False
        notes.append("Schwankung außerhalb des Rahmens")
    return ScoutReport(symbol, px, trend, trend_pct, atr_pct, change, spread_pct, rank, tradable, tuple(notes))


# ─────────────────────────────── risk manager ───────────────────────────────
@dataclass(frozen=True)
class Rules:
    """Trading rules of one market on the exchange."""
    min_amount: float = 0.0
    min_cost: float = 0.0
    amount_step: float = 1e-8
    price_step: float = 0.01


@dataclass(frozen=True)
class Size:
    qty: float
    notional: float
    stop: float
    risk_amount: float            # expected loss (incl. fees) if the initial stop is hit
    capped_by: str                # risk | position | exposure | cash | none
    reason: str                   # empty if qty > 0, otherwise why not


def initial_stop(entry: float, atr: float, p: Params) -> float:
    return entry - p.stop_atr_mult * atr


def trail_stop(prev_stop: float, highest: float, atr: float, p: Params) -> float:
    """Chandelier trailing stop: highest price since entry minus N x ATR. NEVER moves down."""
    if not (math.isfinite(highest) and math.isfinite(atr)) or atr <= 0:
        return prev_stop
    return max(prev_stop, highest - p.stop_atr_mult * atr)


def position_size(*, equity: float, cash: float, exposure: float, price: float, atr: float,
                  p: Params, rules: Rules) -> Size:
    """Exact quantity for a new position.

    qty = (equity x risk%) / (stop distance + round-trip fees per unit)
    then capped by max position size, total exposure and available cash,
    rounded DOWN to the exchange lot size, and checked against its minimums.
    """
    def none(reason: str) -> Size:
        return Size(0.0, 0.0, 0.0, 0.0, "none", reason)

    equity, cash, exposure, price, atr = (float(x) for x in (equity, cash, exposure, price, atr))
    if not all(math.isfinite(x) for x in (equity, cash, exposure, price, atr)) or price <= 0 or equity <= 0:
        return none("Ungültige Preis-/Kontodaten")
    if atr <= 0:
        return none("Keine Schwankungsdaten (ATR)")
    stop = initial_stop(price, atr, p)
    if stop <= 0:
        return none("Stop läge unter null")
    dist = price - stop
    fee_per_unit = p.fee_rate * price + p.fee_rate * stop
    risk_budget = equity * p.risk_per_trade
    q_risk = risk_budget / (dist + fee_per_unit)
    q_pos = equity * p.max_position_pct / price
    q_exp = max(0.0, equity * p.max_total_exposure - exposure) / price
    q_cash = cash / (price * (1.0 + p.fee_rate))
    qty, capped = min((q_risk, "risk"), (q_pos, "position"), (q_exp, "exposure"), (q_cash, "cash"), key=lambda t: t[0])
    qty = floor_to_step(qty, rules.amount_step)
    if qty <= 0:
        return none({"exposure": "Maximales Gesamt-Investment erreicht", "cash": "Nicht genug freies Guthaben"}.get(capped, "Menge rundet auf 0"))
    if qty < rules.min_amount:
        return none(f"Menge {de(qty, 6)} unter dem Börsen-Minimum {de(rules.min_amount, 6)}")
    if qty * price < rules.min_cost:
        return none(f"Ordergröße {de(qty * price, 2)} unter dem Börsen-Minimum {de(rules.min_cost, 2)}")
    notional = qty * price
    risk_amount = qty * (dist + fee_per_unit)
    return Size(float(qty), float(notional), float(stop), float(risk_amount), capped, "")


def daily_loss_hit(day_start_equity: float, equity: float, limit_pct: float) -> bool:
    return day_start_equity > 0 and (equity / day_start_equity - 1.0) * 100.0 <= -abs(limit_pct)


def drawdown_hit(peak_equity: float, equity: float, limit_pct: float) -> bool:
    return peak_equity > 0 and (equity / peak_equity - 1.0) * 100.0 <= -abs(limit_pct)
