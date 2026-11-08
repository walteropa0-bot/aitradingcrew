"""Plotly performance chart: green above zero (profit), red below (drawdown)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import plotly.graph_objects as go

GREEN, RED, GREY = "#00f5a0", "#ff3b5c", "#5b6377"
RANGES = {"1H": 3600, "24H": 86400, "7T": 7 * 86400, "ALLES": 10 ** 12}
CONFIG = {"displayModeBar": False, "scrollZoom": False, "doubleClick": False, "showTips": False}


def _split_at_zero(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Insert the exact zero crossings so green and red areas meet at the baseline."""
    xs, ys = [x[0]], [y[0]]
    for i in range(1, len(x)):
        if (y[i - 1] < 0 < y[i]) or (y[i - 1] > 0 > y[i]):
            t = y[i - 1] / (y[i - 1] - y[i])
            xs.append(x[i - 1] + t * (x[i] - x[i - 1]))
            ys.append(0.0)
        xs.append(x[i])
        ys.append(y[i])
    return np.asarray(xs), np.asarray(ys)


def _labels(ts: np.ndarray, tz: ZoneInfo) -> list[str]:
    return [datetime.fromtimestamp(float(t), tz).strftime("%Y-%m-%d %H:%M:%S") for t in ts]


def empty_figure(text: str = "Sammle Daten …") -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=text, showarrow=False, font=dict(color="#586075", size=13))
    fig.update_layout(height=230, margin=dict(l=0, r=0, t=0, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(visible=False, fixedrange=True), yaxis=dict(visible=False, fixedrange=True), dragmode=False)
    return fig


def pnl_figure(points: list, start_capital: float, range_key: str, now: float, tz: ZoneInfo, currency: str = "€") -> go.Figure:
    span = RANGES.get(range_key, RANGES["24H"])
    pts = [p for p in points if p[0] >= now - span]
    if len(pts) < 2 and len(points) >= 2:
        pts = points[-2:]
    if len(pts) < 2:
        return empty_figure()
    arr = np.asarray(pts, dtype=float)
    ts, pnl = arr[:, 0], arr[:, 1] - start_capital
    xs, ys = _split_at_zero(ts, pnl)
    flat = float(np.max(np.abs(ys))) < 1e-9
    is_pos, is_neg = ys > 0, ys < 0
    near_pos = is_pos | np.r_[is_pos[1:], False] | np.r_[False, is_pos[:-1]]
    near_neg = is_neg | np.r_[is_neg[1:], False] | np.r_[False, is_neg[:-1]]
    # NaN where a colour does not apply, so there is no stray line along the baseline; the exact zero crossings belong to both
    pos = np.where(is_pos | ((ys == 0) & near_pos), ys, np.nan)
    neg = np.where(is_neg | ((ys == 0) & near_neg), ys, np.nan)
    labels = _labels(xs, tz)
    last = float(ys[-1])
    colour = GREEN if last > 1e-9 else (RED if last < -1e-9 else GREY)
    fig = go.Figure()
    if flat:
        fig.add_trace(go.Scatter(x=labels, y=ys, mode="lines", line=dict(color=GREY, width=2), hovertemplate="%{y:+,.2f} " + currency + "<extra></extra>"))
    else:
        fig.add_trace(go.Scatter(x=labels, y=pos, mode="lines", line=dict(color=GREEN, width=2.4, shape="spline", smoothing=0.35),
                                 fill="tozeroy", fillcolor="rgba(0,245,160,0.16)", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=labels, y=neg, mode="lines", line=dict(color=RED, width=2.4, shape="spline", smoothing=0.35),
                                 fill="tozeroy", fillcolor="rgba(255,59,92,0.18)", hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=labels, y=ys, mode="lines", line=dict(color="rgba(0,0,0,0)", width=14),
                                 hovertemplate="%{y:+,.2f} " + currency + "<extra></extra>"))
    fig.add_trace(go.Scatter(x=[labels[-1]], y=[last], mode="markers", hoverinfo="skip", marker=dict(size=20, color=colour, opacity=0.22)))
    fig.add_trace(go.Scatter(x=[labels[-1]], y=[last], mode="markers", hoverinfo="skip", marker=dict(size=8, color=colour, line=dict(color="#05060a", width=2))))
    lo, hi = float(min(ys.min(), 0.0)), float(max(ys.max(), 0.0))
    pad = max((hi - lo) * 0.18, start_capital * 0.0008, 0.5)
    data_span = float(xs[-1] - xs[0])
    tickfmt = "%H:%M:%S" if data_span < 3 * 3600 else ("%H:%M" if data_span < 2 * 86400 else "%d.%m.")
    fig.update_layout(
        height=230, margin=dict(l=2, r=4, t=6, b=0), showlegend=False, dragmode=False, hovermode="x",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color="#8d95a9", size=11),
        hoverlabel=dict(bgcolor="#121723", bordercolor="rgba(255,255,255,.15)", font=dict(color="#eef1f6", size=13)),
        xaxis=dict(showgrid=False, zeroline=False, fixedrange=True, tickformat=tickfmt, nticks=4, showline=False, ticks="", tickfont=dict(size=10, color="#586075")),
        yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.045)", zeroline=True, zerolinecolor="rgba(255,255,255,0.22)", zerolinewidth=1,
                   fixedrange=True, range=[lo - pad, hi + pad], tickformat=",.0f", ticks="", side="right", tickfont=dict(size=10, color="#586075")),
    )
    return fig
