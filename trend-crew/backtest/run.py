"""Backtest the live strategy on real historical candles (Binance public data, no key needed).

    python -m backtest.run                       # default settings from config.py, 4h candles, since 2021
    python -m backtest.run --mode dip            # the "buy oversold dips" variant
    python -m backtest.run --stop-mult 4 --risk 2
    python -m backtest.run --no-breakers         # without daily-loss brake / drawdown kill-switch

Prices are USDT pairs from Binance (long public history). The strategy only looks at price
shape, so EUR vs USDT does not matter for the signals.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config                                    # noqa: E402
from backtest import data as D                  # noqa: E402
from backtest import engine as E                # noqa: E402
from crew import strategy as st                 # noqa: E402
from crew.util import de                        # noqa: E402

DAY_MS = 86_400_000


def load_all(assets, start: str, timeframe: str, cache_dir: str = D.DEFAULT_CACHE):
    one_h = {a: D.load_or_download(a, start, cache_dir) for a in assets}
    main = D.align({a: D.resample(v, timeframe) for a, v in one_h.items()})
    daily = {a: D.load_daily(a, "2019-01-01", cache_dir) for a in assets}
    return main, daily


def yearly(ts: np.ndarray, eq: np.ndarray) -> list[tuple[int, float, float]]:
    years = np.array([time.gmtime(t / 1000).tm_year for t in ts])
    out = []
    prev_end = eq[0]
    for y in sorted(set(years.tolist())):
        seg = eq[years == y]
        out.append((y, (seg[-1] / prev_end - 1) * 100, E._max_drawdown(np.insert(seg, 0, prev_end)) * 100))
        prev_end = seg[-1]
    return out


def report(res: E.Result, main: dict, p: st.Params, capital: float) -> str:
    m = res.metrics
    start_idx = int(np.searchsorted(next(iter(main.values()))[:, 0], res.ts[0]))
    bh_ts, bh_eq = E.buy_and_hold(main, start_idx, capital, p.fee_rate)
    bh_ret = (bh_eq[-1] / capital - 1) * 100
    bh_mdd = E._max_drawdown(bh_eq) * 100
    lines = []
    a = lines.append
    first = time.strftime("%Y-%m-%d", time.gmtime(res.ts[0] / 1000))
    last = time.strftime("%Y-%m-%d", time.gmtime(res.ts[-1] / 1000))
    a(f"Backtest {first} → {last} ({m['years']:.1f} Jahre) · Modus {p.mode} · {p.timeframe}-Kerzen · Stop {p.stop_atr_mult:g}×ATR · Risiko {p.risk_per_trade * 100:g} %/Trade")
    a(f"Gebühr {de(p.fee_rate * 100, 2)} % je Order + Slippage {de(p.slippage * 100, 2)} % · Startkapital {de(capital, 0)}")
    a("─" * 78)
    a(f"{'':26s}{'Bot':>14s}{'Kaufen & Halten*':>20s}")
    a(f"{'Gesamtrendite':26s}{de(m['total_return_pct'], 1, True) + ' %':>14s}{de(bh_ret, 1, True) + ' %':>20s}")
    a(f"{'Max. Verlust (Drawdown)':26s}{de(m['max_drawdown_pct'], 1) + ' %':>14s}{de(bh_mdd, 1) + ' %':>20s}")
    a(f"{'Rendite pro Jahr (CAGR)':26s}{de(m['cagr_pct'], 1, True) + ' %':>14s}")
    a(f"{'Sharpe-Ratio':26s}{de(m['sharpe'], 2):>14s}")
    a("─" * 78)
    a(f"Trades: {m['trades']} · Gewinner-Quote {de(m['win_rate_pct'], 0)} % · Ø Gewinn {de(m['avg_win_pct'], 1, True)} % · "
      f"Ø Verlust {de(m['avg_loss_pct'], 1, True)} % · Profit-Faktor {de(m['profit_factor'], 2)}")
    a(f"Ø Haltedauer {de(m['avg_bars'] * 24 / p.bars_per_day, 0)} h · im Markt {de(m['exposure_pct'], 0)} % der Zeit · Gebühren gesamt {de(m['fees'], 0)}")
    if res.halted_at:
        a(f"⚠ Notbremse (Drawdown-Limit) löste am {time.strftime('%Y-%m-%d', time.gmtime(res.halted_at / 1000))} aus – danach nur noch Cash.")
    a("─" * 78)
    a("Jahr      Bot-Rendite   Bot-Drawdown")
    for y, r, d in yearly(res.ts, res.equity):
        a(f"{y}   {de(r, 1, True):>10s} %   {de(d, 1):>10s} %")
    a("")
    a("*gleichmäßig auf BTC/ETH/SOL verteilt, ohne Stopp. Vergangene Ergebnisse sind KEINE Garantie.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", default=",".join(config.ASSETS))
    ap.add_argument("--from", dest="start", default="2021-01-01")
    ap.add_argument("--timeframe", default=config.TIMEFRAME, choices=["1h", "2h", "4h", "6h", "12h", "1d"])
    ap.add_argument("--mode", default=config.STRATEGY_MODE, choices=["breakout", "dip"])
    ap.add_argument("--stop-mult", type=float, default=config.STOP_ATR_MULT)
    ap.add_argument("--risk", type=float, default=config.RISK_PER_TRADE_PCT, help="Risiko je Trade in %% des Kapitals")
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--no-breakers", action="store_true")
    ap.add_argument("--cache", default=D.DEFAULT_CACHE)
    args = ap.parse_args(argv)

    assets = [a.strip().upper() for a in args.assets.split(",") if a.strip()]
    p = st.Params.from_config().with_(mode=args.mode, timeframe=args.timeframe, stop_atr_mult=args.stop_mult,
                                      risk_per_trade=args.risk / 100.0)
    print(f"Lade Kursdaten für {', '.join(assets)} (einmalig, wird zwischengespeichert) …", flush=True)
    main_data, daily = load_all(assets, args.start, args.timeframe, args.cache)
    res = E.simulate(main_data, p, htf_data=daily, capital=args.capital, enable_breakers=not args.no_breakers,
                     daily_loss_pct=config.DAILY_LOSS_LIMIT_PCT, kill_dd_pct=config.MAX_DRAWDOWN_KILL_PCT)
    print(report(res, main_data, p, args.capital))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
