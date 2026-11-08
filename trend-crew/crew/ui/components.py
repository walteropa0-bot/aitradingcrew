"""HTML for every dashboard block. Pure functions: engine snapshot in, HTML string out (all text escaped)."""
from __future__ import annotations

from datetime import datetime
from html import escape as esc
from typing import Optional
from zoneinfo import ZoneInfo

import config
from crew.util import clamp, de, duration

LOGO = '<span class="ico logo"></span>'


def tzinfo(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/Berlin")
    except Exception:                                   # noqa: BLE001
        return ZoneInfo("Europe/Berlin")


def fmt_time(ts: float, tz: ZoneInfo, seconds: bool = False) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M:%S" if seconds else "%H:%M")


def fmt_dt(ts: float, tz: ZoneInfo, now: float) -> str:
    d, today = datetime.fromtimestamp(ts, tz), datetime.fromtimestamp(now, tz)
    return ("heute " if d.date() == today.date() else d.strftime("%d.%m. ")) + d.strftime("%H:%M")


def money(x: Optional[float], cur: str, nd: int = 2, sign: bool = False) -> str:
    return f"{de(x, nd, sign)}&nbsp;{esc(cur)}"


def sgn(x: float) -> str:
    return "up" if x > 0.005 else ("down" if x < -0.005 else "flat")


def cls_sign(x: float) -> str:
    return "pos" if x > 0.005 else ("neg" if x < -0.005 else "mut")


# ───────────────────────────── header / hero ─────────────────────────────
def header_html(snap: dict) -> str:
    live = snap["mode"] == "live"
    state = snap["state"]
    st_badge = {"running": '<span class="badge run">LÄUFT</span>', "paused": '<span class="badge pause">PAUSE</span>',
                "halted": '<span class="badge halt">GESTOPPT</span>'}[state]
    mode = '<span class="badge live">● LIVE</span>' if live else '<span class="badge paper">DEMO</span>'
    return (f'<div class="tc hdr"><div class="logo"><span class="logo-mark">{LOGO}</span><b>TREND·CREW</b></div>'
            f'<div class="badges">{mode}{st_badge}</div></div>')


def hero_html(snap: dict) -> str:
    cur = snap["currency"]
    pnl, pnl_pct = snap["pnl"], snap["pnl_pct"]
    k = sgn(pnl)
    arrow = {"up": "▲", "down": "▼", "flat": "•"}[k]
    day = snap["day_pnl"]
    return (f'<div class="tc hero {k if k != "flat" else ""}"><div class="lbl">Gesamtwert · {"Live-Konto" if snap["mode"] == "live" else "Demo-Konto"}</div>'
            f'<div class="big num">{de(snap["equity"], 2)}<small>{esc(cur)}</small></div>'
            f'<span class="chip {k} num">{arrow} {de(pnl, 2, True)}&nbsp;{esc(cur)} · {de(pnl_pct, 2, True)}&nbsp;%</span>'
            f'<div class="sub num"><span>Heute <b class="{cls_sign(day)}">{de(day, 2, True)}&nbsp;{esc(cur)}</b></span>'
            f'<span>Investiert <b>{de(snap["exposure_pct"], 0)}&nbsp;%</b></span>'
            f'<span>Frei <b>{de(snap["cash"], 2)}&nbsp;{esc(cur)}</b></span></div></div>')


def banner(kind: str, text: str) -> str:
    icon = {"bad": "⛔", "warn": "⚠️", "ok": "✅"}.get(kind, "ℹ️")
    return f'<div class="tc banner {kind}"><span>{icon}</span><div>{text}</div></div>'


def banners(snap: dict) -> list[str]:
    out: list[str] = []
    sysd = snap["system"]
    if snap["mode"] == "live" and snap["locked"]:
        out.append(banner("bad", f"<b>Live-Handel gesperrt.</b> {esc(snap['locked'])}"))
    elif snap["mode"] == "live" and snap["live_error"]:
        out.append(banner("bad", f"<b>Live-Handel nicht bereit.</b> {esc(snap['live_error'])}"))
    if not sysd["ok"]:
        hint = sysd["hint"] or "Der Bot versucht es automatisch erneut. Offene Positionen werden erst wieder geprüft, wenn Kurse da sind."
        age = sysd["tick_age"]
        since = f" (letzte Kurse vor {duration(age)})" if age else ""
        out.append(banner("bad" if sysd["kind"] in ("geo", "auth") else "warn",
                          f"<b>Keine Verbindung zur Börse{since}.</b> {esc(sysd['error'][:160])}<br>{esc(hint)}"))
    if snap["state"] == "halted":
        extra = " Positionen werden verkauft …" if snap["liquidating"] else " Zum Fortfahren <b>[BOT STARTEN]</b> drücken."
        out.append(banner("bad", f"<b>Gestoppt:</b> {esc(snap['halt_reason'])}.{extra}"))
    if snap["day_blocked"]:
        out.append(banner("warn", f"<b>Tageslimit:</b> {esc(snap['day_blocked'])}"))
    if snap["pending"]:
        out.append(banner("warn", f"<b>Unklare Order</b> für {esc(', '.join(snap['pending']))} wird mit der Börse abgeglichen."))
    if sysd["save_error"]:
        out.append(banner("warn", f"<b>Speichern fehlgeschlagen:</b> {esc(sysd['save_error'][:120])}"))
    if sysd["internal_error"]:
        out.append(banner("warn", f"Interner Hinweis: {esc(sysd['internal_error'][:140])}"))
    return out


def status_line(snap: dict) -> str:
    st_ = snap["state"]
    if st_ == "running":
        txt = "<b class='pos'>●</b> Bot läuft – Stopps bewachen jede Position, neue Käufe nur bei Signal."
    elif st_ == "paused":
        txt = "<b class='amb'>●</b> Pausiert – keine neuen Käufe. Offene Positionen bleiben durch Stopps geschützt."
    else:
        txt = "<b class='neg'>●</b> Gestoppt – es wird nichts mehr gekauft."
    return f'<div class="tc ctrl-note">{txt}</div>'


# ───────────────────────────── agents ─────────────────────────────
WORKING_WINDOW = 3.5


def agent_html(a: dict, now: float, tz: ZoneInfo, typing: bool) -> str:
    working = (now - a["activity_ts"]) < WORKING_WINDOW and a["activity_ts"] > 0
    if working:
        cls, text, detail = "working", a["work_text"] or a["text"], "arbeitet …"
    else:
        cls = a["state"] if a["state"] in ("ok", "warn", "error", "idle") else "ok"
        text, detail = a["text"], a["detail"]
    stamp = fmt_time(a["updated"], tz, True) if a["updated"] else ""
    return (f'<div class="tc agent {cls}"><div class="ic"><span class="ico {esc(a["icon"])}"></span><span class="dot"></span></div>'
            f'<div class="body"><div class="nm">{esc(a["name"])}<small>{esc(a["role"])}</small></div>'
            f'<div class="tx{" typing" if typing and working else ""}">{esc(text)}</div>'
            f'<div class="dt">{esc(detail)}{" · " + stamp if (detail and not working) else ""}</div></div></div>')


# ───────────────────────────── markets ─────────────────────────────
def _fmt_wait(sec: Optional[float]) -> str:
    if sec is None:
        return ""
    m = int(sec // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def market_html(m: dict, snap: dict) -> str:
    cur = snap["currency"]
    price = m["price"]
    ch = m["change_pct"]
    trend = {"up": ("up", "▲ Aufwärtstrend"), "down": ("down", "▼ Abwärtstrend"), "side": ("", "◆ Seitwärts"),
             "unknown": ("", "… sammelt Daten")}[m["trend"]]
    rsi = m["rsi_live"] if m["rsi_live"] is not None else m["rsi"]
    macd_up = m.get("macd_live_up") if m.get("macd_live_up") is not None else m["macd_up"]
    sig = m["signal"]
    tags = [f'<span class="tag {trend[0]}">{trend[1]}</span>']
    if macd_up is not None:
        tags.append(f'<span class="tag {"up" if macd_up else "down"}">MACD {"↑" if macd_up else "↓"}</span>')
    if rsi is not None:
        zone = "überkauft" if rsi >= 70 else ("überverkauft" if rsi <= 30 else "neutral")
        tags.append(f'<span class="tag">RSI {de(rsi, 0)} · {zone}</span>')
    if m["has_position"]:
        tags.append('<span class="tag held">● in Position</span>')
    elif sig["action"] == "BUY":
        tags.append('<span class="tag buy">KAUFSIGNAL</span>')
    gauge = ""
    if rsi is not None:
        gauge = (f'<div class="gauge"><i style="left:{clamp(rsi, 0, 100):.1f}%"></i></div>'
                 f'<div class="gl"><span>überverkauft ≤ 30</span><span>RSI {de(rsi, 1)}</span><span>≥ 70 überkauft</span></div>')
    checks = "".join(f'<span class="ck {"ok" if c["ok"] else "no"}" title="{esc(c["detail"])}">{"✓" if c["ok"] else "✗"} {esc(c["label"])}</span>'
                     for c in sig["checks"])
    wait = _fmt_wait(m["next_bar_in"])
    notes = (" · " + esc(", ".join(m["notes"]))) if m["notes"] else ""
    foot = esc(sig["summary"]) + (f" · nächste Kerze in {wait}" if wait else "") + notes
    chg = f'<div class="ch num {cls_sign(ch or 0)}">{de(ch, 2, True)}&nbsp;% / 24h</div>' if ch is not None else ""
    return (f'<div class="tc card mk"><div class="row"><div><div class="sym">{esc(m["base"])}<small>/{esc(snap["quote"])}</small></div></div>'
            f'<div><div class="px num">{money(price, cur) if price else "–"}</div>{chg}</div></div>'
            f'<div class="tags">{"".join(tags)}</div>{gauge}<div class="checks">{checks}</div><div class="sig">{foot}</div></div>')


# ───────────────────────────── positions / feed ─────────────────────────────
def positions_html(snap: dict, tz: ZoneInfo) -> str:
    cur = snap["currency"]
    items = []
    for p in snap["positions"]:
        k = cls_sign(p["pnl"])
        dist = p["stop_dist_pct"]
        fill = clamp((dist or 0) / 12.0 * 100.0, 3, 100)
        note = " · wird verkauft …" if p["exiting"] else ""
        items.append(
            f'<div class="pos-i"><div class="t"><span>{esc(p["base"])} <small class="mut">{de(p["qty"], 6)}</small></span>'
            f'<span class="num {k}">{de(p["pnl"], 2, True)}&nbsp;{esc(cur)} <small>({de(p["pnl_pct"], 2, True)}&nbsp;%)</small></span></div>'
            f'<div class="s num"><span>Einstieg {de(p["entry"], 2)} → jetzt {de(p["price"], 2)}</span><span>Wert {de(p["value"], 2)}&nbsp;{esc(cur)}</span></div>'
            f'<div class="s num"><span>🛡 Stop {de(p["stop"], 2)}&nbsp;{esc(cur)}</span><span>{de(dist, 1)}&nbsp;% Abstand · Risiko {de(p["risk"], 2)}&nbsp;{esc(cur)}{note}</span></div>'
            f'<div class="bar"><i style="width:{fill:.0f}%"></i></div></div>')
    body = "".join(items) or '<div class="empty">Keine offene Position.<br>Der Bot wartet auf ein bestätigtes Signal.</div>'
    return f'<div class="tc card"><h4>Offene Positionen <small>{len(snap["positions"])} / {snap["config"]["max_positions"]}</small></h4>{body}</div>'


def feed_html(snap: dict, tz: ZoneInfo, new_ids: set) -> str:
    cur = snap["currency"]
    rows = []
    for t in snap["trades"][:12]:
        buy = t["side"] == "buy"
        sub = f'{de(t["qty"], 6)} @ {de(t["price"], 2)}&nbsp;{esc(cur)}'
        if buy:
            right = f'<div class="r num">{de(t["notional"], 2)}&nbsp;{esc(cur)}<small>Gebühr {de(t["fee"], 2)}</small></div>'
        else:
            k = cls_sign(t["pnl"])
            right = f'<div class="r num {k}">{de(t["pnl"], 2, True)}&nbsp;{esc(cur)}<small>{de(t["pnl_pct"], 2, True)}&nbsp;%</small></div>'
        why = fmt_dt(t["ts"], tz, snap["ts"]) + " · " + esc((t.get("reason") or "")[:70])
        rows.append(f'<div class="feed-i{" new" if t["id"] in new_ids else ""}"><div class="fi {"buy" if buy else "sell"}">{"▲" if buy else "▼"}</div>'
                    f'<div class="m"><b>{"KAUF" if buy else "VERKAUF"} {esc(t["symbol"].split("/")[0])}</b><div>{sub}</div><div>{why}</div></div>{right}</div>')
    body = "".join(rows) or '<div class="empty">Noch keine Trades.<br>Sobald der Bot kauft oder verkauft, erscheint es hier.</div>'
    return f'<div class="tc card"><h4>Transaktionen <small>{snap["stats"]["trades"]} abgeschlossen</small></h4>{body}</div>'


def journal_html(snap: dict, tz: ZoneInfo) -> str:
    tags = {"scout": ("Scout", "blu"), "analyst": ("Analyst", "amb"), "risk": ("Risiko", "pos"), "trade": ("Handel", "pos"), "system": ("System", "mut")}
    rows = []
    for j in snap["journal"][:7]:
        name, colour = tags.get(j["agent"], (j["agent"], "mut"))
        rows.append(f'<div><time>{fmt_time(j["ts"], tz, True)}</time><em class="{colour}">{name}</em><span class="{j["level"]}">{esc(j["text"][:170])}</span></div>')
    return f'<div class="tc card"><h4>Live-Aktivität</h4><div class="log">{"".join(rows) or "<div>…</div>"}</div></div>'


# ───────────────────────────── stats / system ─────────────────────────────
def stats_html(snap: dict) -> str:
    s, cur = snap["stats"], snap["currency"]
    pf = s["profit_factor"]
    pf_txt = "∞" if pf == float("inf") else de(pf, 2)
    kp = [("Trades", f'{s["trades"]}', ""), ("Gewinn-Quote", f'{de(s["win_rate"], 0)}&nbsp;%' if s["trades"] else "–", ""),
          ("Profit-Faktor", pf_txt if s["trades"] else "–", ""),
          ("Ø Gewinn", f'{de(s["avg_win"], 2, True)}' if s["wins"] else "–", "pos"), ("Ø Verlust", f'{de(s["avg_loss"], 2, True)}' if s["losses"] else "–", "neg"),
          ("Gebühren", f'{de(s["fees"], 2)}&nbsp;{esc(cur)}', ""),
          ("Investiert", f'{de(snap["exposure_pct"], 0)}&nbsp;%', ""), ("Rückgang vom Hoch", f'{de(snap["drawdown_pct"], 1)}&nbsp;%', cls_sign(snap["drawdown_pct"])),
          ("Heute", f'{de(snap["day_pnl_pct"], 2, True)}&nbsp;%', cls_sign(snap["day_pnl_pct"]))]
    tiles = "".join(f'<div class="kpi"><small>{a}</small><b class="num {c}">{b}</b></div>' for a, b, c in kp)
    cfg = snap["config"]
    return (f'<div class="tc card"><h4>Kennzahlen <small>Limits: Tag −{de(cfg["daily_limit"], 1)} % · Notbremse −{de(cfg["kill_dd"], 0)} %</small></h4>'
            f'<div class="grid">{tiles}</div></div>')


def system_html(snap: dict, tz: ZoneInfo) -> str:
    sysd, cfg = snap["system"], snap["config"]
    ok = '<span class="pos">● verbunden</span>' if sysd["ok"] else '<span class="neg">● Störung</span>'
    strat = "Trend-Ausbruch" if cfg["strategy"] == "breakout" else "Überverkauft-Einstieg (Dip)"
    rows = [("Börse", f'{esc(sysd["exchange"].capitalize())} · {ok}'), ("Antwortzeit", f'{sysd["latency_ms"]} ms'),
            ("Letzte Kurse", fmt_time(sysd["last_tick"], tz, True) if sysd["last_tick"] else "–"),
            ("Strategie", f'{strat} · {cfg["timeframe"]}-Kerzen'),
            ("Risiko", f'{de(cfg["risk_pct"], 1)} % je Trade · Stop {de(cfg["stop_mult"], 1)}×ATR'),
            ("Gebühr (Annahme)", f'{de(cfg["fee_pct"], 2)} % je Order'), ("Speicher", esc(sysd["store"])),
            ("Motor", ("läuft" if sysd["alive"] else "<span class='neg'>gestoppt</span>") + f" · seit {duration(sysd['uptime'])}"),
            ("Version", f"{config.APP_NAME} {config.VERSION}")]
    body = "".join(f'<div class="sysrow"><span>{a}</span><b>{b}</b></div>' for a, b in rows)
    return f'<div class="tc card"><h4>System</h4>{body}</div>'


def footer_html(snap: dict) -> str:
    return ('<div class="tc foot">Kein Anlagetipp. Kryptowährungen können stark schwanken – du kannst Geld verlieren.<br>'
            + ('Der Bot handelt in diesem Modus nur mit Spielgeld.' if snap["mode"] == "paper" else
               '<b>LIVE-MODUS:</b> Der Bot handelt mit deinem echten Börsen-Guthaben, überweist aber nie Geld an Dritte.') + '</div>')
