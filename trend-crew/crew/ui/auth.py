"""Password gate for the dashboard (+ optional 30-day 'stay signed in' cookie).

No password configured  -> open (DEMO only; the engine refuses LIVE trading without a password).
Password configured     -> login form; brute-force protection (5 wrong tries -> 5 min pause).
The cookie holds only an expiry time and an HMAC signature, never the password.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from typing import Optional

import streamlit as st

import config
from . import components as C

COOKIE = "tc_auth"
MAX_FAILS, WINDOW_SEC, LOCK_SEC = 5, 300.0, 300.0
_lock = threading.Lock()
_fails: dict[str, list[float]] = {}
_locked_until: dict[str, float] = {}


def _key() -> bytes:
    return hashlib.sha256(("trendcrew|" + config.DASHBOARD_PASSWORD).encode()).digest()


def make_token(now: Optional[float] = None, days: int = 30) -> str:
    exp = int((now or time.time()) + days * 86400)
    return f"{exp}.{hmac.new(_key(), str(exp).encode(), hashlib.sha256).hexdigest()}"


def valid_token(token: Optional[str], now: Optional[float] = None) -> bool:
    if not token or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    good = hmac.new(_key(), exp_s.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and exp > (now or time.time())


def password_ok(candidate: str) -> bool:
    return bool(config.DASHBOARD_PASSWORD) and hmac.compare_digest(candidate.encode(), config.DASHBOARD_PASSWORD.encode())


def lock_remaining(client: str, now: Optional[float] = None) -> int:
    now = now or time.time()
    with _lock:
        return max(0, int(_locked_until.get(client, 0.0) - now))


def register_fail(client: str, now: Optional[float] = None) -> None:
    now = now or time.time()
    with _lock:
        recent = [t for t in _fails.get(client, []) if now - t < WINDOW_SEC] + [now]
        _fails[client] = recent
        if len(recent) >= MAX_FAILS:
            _locked_until[client] = now + LOCK_SEC
            _fails[client] = []


def _client_id() -> str:
    try:
        return st.context.ip_address or "unknown"
    except Exception:                                    # noqa: BLE001
        return "unknown"


def _cookie_script(value: str, max_age: int) -> None:
    js = (f"<script>document.cookie='{COOKIE}={value}; max-age={max_age}; path=/; SameSite=Strict'"
          "+(location.protocol==='https:'?'; Secure':'');</script>")
    try:
        st.html(js, unsafe_allow_javascript=True)
    except TypeError:                                    # older Streamlit: no JS support -> just no cookie
        pass


def apply_pending_cookie() -> None:
    """Called on every authenticated run: writes the 'stay signed in' cookie once after a login."""
    if st.session_state.pop("cookie_action", None) == "set":
        _cookie_script(make_token(), 30 * 86400)


def logout() -> None:
    """Forget this session; the login screen then also wipes the 'stay signed in' cookie."""
    st.session_state["authed"] = False
    st.session_state["logged_out"] = True
    st.session_state["cookie_action"] = "clear"


def require_login() -> bool:
    """True if the visitor may see the dashboard; otherwise renders the login screen and returns False."""
    if not config.DASHBOARD_PASSWORD:
        if not config.PAPER_TRADING:
            st.html(C.banner("bad", "<b>Live-Modus ohne Passwort?</b> Setze zuerst das Secret <b>DASHBOARD_PASSWORD</b> "
                                    "(Anleitung Schritt 3) und starte die App neu."))
            return False
        return True
    if st.session_state.get("authed"):
        return True
    token = None
    if not st.session_state.get("logged_out"):           # after an explicit logout the old cookie must not log us in again
        try:
            token = st.context.cookies.get(COOKIE)
        except Exception:                                # noqa: BLE001
            token = None
    if valid_token(token):
        st.session_state["authed"] = True
        return True
    if st.session_state.pop("cookie_action", None) == "clear":
        _cookie_script("", 0)
    client = _client_id()
    wait = lock_remaining(client)
    st.html('<div class="tc loginwrap login"><div class="logo-mark" style="margin:0 auto;width:54px;height:54px;border-radius:18px"><span class="ico logo" style="width:28px;height:28px"></span></div>'
            '<h1>TREND·CREW</h1><p>Dein Dashboard ist geschützt.<br>Bitte Passwort eingeben.</p></div>')
    with st.form("login", border=False):
        pw = st.text_input("Passwort", type="password", placeholder="Passwort", label_visibility="collapsed")
        go = st.form_submit_button("ENTSPERREN", key="btn_login", width="stretch")
    if wait:
        st.html(C.banner("warn", f"Zu viele Fehlversuche. Bitte in {max(1, wait // 60 + (1 if wait % 60 else 0))} Minute(n) erneut versuchen."))
    elif go:
        if password_ok(pw):
            st.session_state["authed"] = True
            st.session_state["logged_out"] = False
            st.session_state["cookie_action"] = "set"
            st.rerun()
        else:
            register_fail(client)
            st.html(C.banner("bad", "Falsches Passwort."))
    return False
