"""Dashboard (Streamlit). Läuft NICHT selbst den Handel – der Handels-Motor steckt in crew/engine.py und
läuft als Hintergrund-Thread (gestartet von main.py). Diese Seite zeigt nur an und sendet Befehle."""
import os
import time

import streamlit as st

import config
from crew import runtime
from crew.ui import auth, charts, components as C
from crew.ui.styles import CSS

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, "static", "icon-192.png")
st.set_page_config(page_title="TREND·CREW", page_icon=ICON if os.path.exists(ICON) else "📈", layout="centered",
                   initial_sidebar_state="collapsed")

engine = runtime.get_engine()
# A leading <div> keeps the HTML sanitiser from dropping the <style> block; blank lines are removed on purpose.
_CSS = " ".join(line.strip() for line in CSS.splitlines() if line.strip())
st.html(f'<div style="display:none"></div><style>{_CSS}</style>')

HEAD_JS = """<script>(function(){try{var d=window.parent.document,h=d.head;
function meta(n,c){var m=d.querySelector('meta[name="'+n+'"]');if(!m){m=d.createElement('meta');m.name=n;h.appendChild(m)}m.content=c}
meta('apple-mobile-web-app-capable','yes');meta('mobile-web-app-capable','yes');meta('apple-mobile-web-app-status-bar-style','black-translucent');
meta('apple-mobile-web-app-title','TREND·CREW');meta('theme-color','#05060a');meta('color-scheme','dark');
var vp=d.querySelector('meta[name="viewport"]');if(vp&&vp.content.indexOf('viewport-fit')<0){vp.content+=',viewport-fit=cover'}
if(!d.querySelector('link[rel="apple-touch-icon"]')){var l=d.createElement('link');l.rel='apple-touch-icon';l.href='/app/static/icon-180.png';h.appendChild(l)}
}catch(e){}})();</script>"""
try:
    st.html(HEAD_JS, unsafe_allow_javascript=True)
except TypeError:
    pass

if not auth.require_login():
    st.stop()
auth.apply_pending_cookie()

TZ = C.tzinfo(getattr(st.context, "timezone", None))
state = st.session_state
state.setdefault("agent_text", {})
state.setdefault("seen_trades", None)


def safe(fn):
    """A rendering bug must never blank the whole dashboard."""
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as exc:                                  # noqa: BLE001
            st.html(C.banner("warn", f"Anzeige-Fehler: {type(exc).__name__}: {exc}"))
    wrapper.__name__ = fn.__name__
    return wrapper


# ───────────────────────── commands ─────────────────────────
def _flash(res: dict) -> None:
    """Callbacks must not draw elements: remember the message, the next fragment run shows it as a toast."""
    state["flash"] = res


def do_start() -> None:
    state["confirm"] = None
    _flash(engine.command("start", timeout=1.5))


def ask_start() -> None:
    if engine.snapshot().get("mode") == "live":
        state["confirm"] = ("start", time.time())
    else:
        do_start()


def do_pause() -> None:
    _flash(engine.command("pause", timeout=1.5))


def ask_panic() -> None:
    state["confirm"] = ("panic", time.time())


def do_panic() -> None:
    state["confirm"] = None
    _flash(engine.command("panic", timeout=1.5))


def cancel_confirm() -> None:
    state["confirm"] = None


def do_rebase() -> None:
    _flash(engine.command("rebase_budget", timeout=5.0))


def do_reset() -> None:
    state["confirm"] = None
    _flash(engine.command("reset_paper", timeout=1.5))


# ───────────────────────── live panels ─────────────────────────
@st.fragment(run_every=2)
@safe
def top_panel() -> None:
    snap = engine.snapshot()
    flash = state.pop("flash", None)
    if flash:
        st.toast(flash.get("message", ""), icon="✅" if flash.get("ok") else "⚠️")
    st.html(C.header_html(snap))
    st.html(C.hero_html(snap))
    for b in C.banners(snap):
        st.html(b)
    conf = state.get("confirm")
    if conf and time.time() - conf[1] > 12:
        state["confirm"] = conf = None
    if conf and conf[0] == "panic":
        n = len(snap["positions"])
        st.html('<div class="tc confirm"><b>Wirklich ALLES verkaufen?</b><br>'
                f'{n} offene Position{"en" if n != 1 else ""} werden sofort zum Marktpreis verkauft, danach stoppt der Bot.</div>')
        st.button("[JA – JETZT ALLES VERKAUFEN]", key="btn_panic_yes", on_click=do_panic, width="stretch")
        st.button("Abbrechen", key="btn_ghost", on_click=cancel_confirm, width="stretch")
    elif conf and conf[0] == "start":
        st.html('<div class="tc confirm go"><b>Live-Handel mit ECHTEM GELD starten?</b><br>'
                f'Der Bot darf bis zu {C.de(snap["start_capital"], 2)} {snap["currency"]} auf deinem Börsenkonto einsetzen. '
                'Verluste sind möglich.</div>')
        st.button("[JA – LIVE STARTEN]", key="btn_start_yes", on_click=do_start, width="stretch")
        st.button("Abbrechen", key="btn_ghost", on_click=cancel_confirm, width="stretch")
    else:
        if snap["state"] == "running":
            st.button("[BOT PAUSIEREN]", key="btn_pause", on_click=do_pause, width="stretch")
        else:
            st.button("[BOT STARTEN]", key="btn_start", on_click=ask_start, width="stretch")
        st.button("[NOT-AUS: ALLES LIQUIDIEREN]", key="btn_panic", on_click=ask_panic, width="stretch")
        st.html(C.status_line(snap))


@st.fragment(run_every=5)
@safe
def chart_panel() -> None:
    snap = engine.snapshot()
    with st.container(key="chartcard"):
        st.html('<div class="tc"><h4 style="margin:0 0 6px;font-size:11px;font-weight:800;letter-spacing:.16em;color:#8d95a9">PERFORMANCE</h4></div>')
        rng = st.segmented_control("Zeitraum", list(charts.RANGES), default="24H", key="range", label_visibility="collapsed") or "24H"
        fig = charts.pnl_figure(snap["curve"], snap["start_capital"], rng, snap["ts"], TZ, snap["currency"])
        st.plotly_chart(fig, width="stretch", config=charts.CONFIG, key="pnl_chart")


@st.fragment(run_every=2)
@safe
def agents_panel() -> None:
    snap = engine.snapshot()
    now = time.time() if abs(time.time() - snap["ts"]) < 120 else snap["ts"]
    st.html('<div class="tc"><h4 style="margin:6px 2px 0;font-size:11px;font-weight:800;letter-spacing:.16em;color:#8d95a9">DIE CREW BEI DER ARBEIT</h4></div>')
    seen = state["agent_text"]
    for a in snap["agents"]:
        key = (a["work_text"] if (now - a["activity_ts"]) < C.WORKING_WINDOW else a["text"])
        typing = seen.get(a["id"]) != key
        seen[a["id"]] = key
        st.html(C.agent_html(a, now, TZ, typing))
    st.html(C.journal_html(snap, TZ))


@st.fragment(run_every=3)
@safe
def markets_panel() -> None:
    snap = engine.snapshot()
    st.html('<div class="tc"><h4 style="margin:6px 2px 0;font-size:11px;font-weight:800;letter-spacing:.16em;color:#8d95a9">MÄRKTE</h4></div>')
    for m in snap["markets"]:
        st.html(C.market_html(m, snap))
    st.html(C.positions_html(snap, TZ))
    ids = {t["id"] for t in snap["trades"]}
    if state["seen_trades"] is None:
        state["seen_trades"] = set(ids)
    new = ids - state["seen_trades"]
    st.html(C.feed_html(snap, TZ, new))
    state["seen_trades"] |= ids


@st.fragment(run_every=5)
@safe
def system_panel() -> None:
    snap = engine.snapshot()
    st.html(C.stats_html(snap))
    st.html(C.system_html(snap, TZ))
    st.html(C.footer_html(snap))
    c1, c2 = st.columns(2)
    if snap["mode"] == "paper":
        c1.button("Demo-Konto zurücksetzen", key="btn_ghost_reset", on_click=do_reset, width="stretch")
    elif snap["state"] != "running" and not snap["positions"]:
        c1.button("Live-Budget neu festlegen", key="btn_ghost_reset", on_click=do_rebase, width="stretch")
    if config.DASHBOARD_PASSWORD and c2.button("Abmelden", key="btn_ghost_logout", width="stretch"):
        auth.logout()
        st.rerun()                                              # full rerun so the login gate at the top runs again


top_panel()
chart_panel()
agents_panel()
markets_panel()
system_panel()
