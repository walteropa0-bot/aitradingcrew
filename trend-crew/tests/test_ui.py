import time

import numpy as np
import pytest

from crew.ui import auth, charts
from crew.ui import components as C
from tests.test_engine import World

TZ = C.tzinfo("Europe/Berlin")


@pytest.fixture
def snap(tmp_path):
    w = World(tmp_path)
    w.eng.command("start")
    w.run(2)
    w.set_price(w.ex.px["BTC/EUR"] * 1.03)
    return w.run(2)


def test_hero_colours_follow_profit_and_loss(snap):
    assert 'hero up' in C.hero_html({**snap, "pnl": 120.0, "pnl_pct": 1.2})
    assert 'hero down' in C.hero_html({**snap, "pnl": -50.0, "pnl_pct": -0.5})
    flat = C.hero_html({**snap, "pnl": 0.0, "pnl_pct": 0.0, "day_pnl": 0.0})
    assert "hero up" not in flat and "hero down" not in flat
    assert "▲" in C.hero_html({**snap, "pnl": 5.0}) and "▼" in C.hero_html({**snap, "pnl": -5.0})


def test_numbers_use_german_format(snap):
    html = C.hero_html({**snap, "equity": 12345.678, "pnl": 2345.678, "pnl_pct": 23.45})
    assert "12.345,68" in html and "+2.345,68" in html and "+23,45" in html


def test_everything_user_controlled_is_escaped(snap):
    evil = '<img src=x onerror=alert(1)>'
    bad = {**snap, "halt_reason": evil, "day_blocked": evil, "pending": [evil],
           "system": {**snap["system"], "ok": False, "error": evil, "hint": evil, "kind": "network", "tick_age": 12.0,
                      "save_error": evil, "internal_error": evil},
           "journal": [{"seq": 1, "ts": time.time(), "agent": "risk", "level": "warn", "kind": "agent", "text": evil}],
           "trades": [{**snap["trades"][0], "reason": evil, "symbol": "BTC/EUR"}],
           "state": "halted"}
    out = "".join(C.banners(bad)) + C.journal_html(bad, TZ) + C.feed_html(bad, TZ, set())
    assert "<img" not in out and "&lt;img" in out


def test_banners_cover_the_failure_modes(snap):
    assert C.banners({**snap, "system": {**snap["system"], "ok": True, "save_error": "", "internal_error": ""}, "day_blocked": "", "pending": [], "state": "running"}) == []
    live_locked = C.banners({**snap, "mode": "live", "locked": "Live-Handel ist gesperrt: Setze DASHBOARD_PASSWORD"})
    assert "gesperrt" in live_locked[0]
    geo = C.banners({**snap, "system": {**snap["system"], "ok": False, "kind": "geo", "error": "451", "hint": "Nutze EXCHANGE=kraken", "tick_age": None}})
    assert "Keine Verbindung" in geo[0] and "kraken" in geo[0]
    halted = C.banners({**snap, "state": "halted", "halt_reason": "Not-Aus durch Benutzer", "liquidating": True})
    assert any("Positionen werden verkauft" in b for b in halted)
    assert any("Tageslimit" in b for b in C.banners({**snap, "day_blocked": "Verlust heute −3,1 %"}))
    assert any("Unklare Order" in b for b in C.banners({**snap, "pending": ["BTC/EUR"]}))


def test_agent_card_shows_work_text_only_while_working():
    a = {"id": "scout", "name": "Trend-Scout", "role": "r", "icon": "radar", "state": "ok", "text": "Fertig: BTC 68.000 €", "detail": "Handelbar: BTC",
         "work_text": "Scout liest Live-Kurse …", "activity_ts": 1000.0, "updated": 1000.0, "runs": 1}
    busy = C.agent_html(a, 1001.0, TZ, typing=True)
    assert "working" in busy and "Scout liest Live-Kurse" in busy and "typing" in busy and "Fertig" not in busy
    calm = C.agent_html(a, 1010.0, TZ, typing=True)
    assert "working" not in calm and "Fertig: BTC" in calm and "typing" not in calm and 'class="tc agent ok"' in calm
    err = C.agent_html({**a, "state": "error"}, 1010.0, TZ, False)
    assert 'agent error' in err
    never = C.agent_html({**a, "activity_ts": 0.0, "state": "idle"}, 1010.0, TZ, False)
    assert "working" not in never


def test_market_card_with_buy_signal_position_and_missing_data(snap):
    m = dict(snap["markets"][0])
    html = C.market_html({**m, "has_position": False, "signal": {**m["signal"], "action": "BUY"}}, snap)
    assert "KAUFSIGNAL" in html and "gauge" in html
    held = C.market_html({**m, "has_position": True}, snap)
    assert "in Position" in held and "KAUFSIGNAL" not in held
    empty = C.market_html({**m, "price": None, "rsi": None, "rsi_live": None, "macd_up": None, "change_pct": None, "next_bar_in": None,
                           "trend": "unknown", "signal": {"action": "WAIT", "score": 0, "summary": "Sammle Kursdaten …", "checks": []}}, snap)
    assert "–" in empty and "gauge" not in empty


def test_positions_and_feed_blocks(snap):
    html = C.positions_html(snap, TZ)
    assert "BTC" in html and "Stop" in html and "Offene Positionen" in html
    assert "Keine offene Position" in C.positions_html({**snap, "positions": []}, TZ)
    feed = C.feed_html(snap, TZ, {snap["trades"][0]["id"]})
    assert "feed-i new" in feed and "KAUF" in feed
    assert "feed-i new" not in C.feed_html(snap, TZ, set())
    assert "Noch keine Trades" in C.feed_html({**snap, "trades": []}, TZ, set())


def test_stats_and_system_blocks_render_without_trades(snap):
    html = C.stats_html({**snap, "stats": {**snap["stats"], "trades": 0, "wins": 0, "losses": 0, "profit_factor": 0.0}})
    assert "Gewinn-Quote" in html and "–" in html
    assert "∞" in C.stats_html({**snap, "stats": {**snap["stats"], "trades": 3, "wins": 3, "profit_factor": float("inf")}})
    assert "Trend-Ausbruch" in C.system_html(snap, TZ)


# ───────────────────────── chart ─────────────────────────
def _pts(vals, step=60.0, t0=1_800_000_000.0):
    return [[t0 + i * step, 10_000.0 + v] for i, v in enumerate(vals)]


def test_chart_splits_profit_and_loss_at_the_zero_crossing():
    pts = _pts([0, 50, 100, 40, -30, -80, -20, 60])
    fig = charts.pnl_figure(pts, 10_000.0, "ALLES", pts[-1][0], TZ)
    pos, neg = fig.data[0], fig.data[1]
    assert pos.line.color == charts.GREEN and neg.line.color == charts.RED
    y_pos, y_neg = np.array(pos.y, dtype=float), np.array(neg.y, dtype=float)
    assert np.nanmin(y_pos) >= 0 and np.nanmax(y_neg) <= 0
    assert len(pos.x) == 8 + 2                                  # two exact zero crossings were inserted
    assert (y_pos == 0).sum() >= 2 and (y_neg == 0).sum() >= 2     # crossings belong to both colours -> areas meet
    assert not np.isnan(y_pos[:5]).all() and np.isnan(y_pos[6:8]).any()   # no green line while in loss


def test_chart_only_green_when_always_profitable_and_grey_when_flat():
    up = charts.pnl_figure(_pts([0, 10, 30, 60]), 10_000.0, "ALLES", 1_800_000_200.0, TZ)
    assert np.isnan(np.array(up.data[1].y, dtype=float)).all() or np.nanmax(np.array(up.data[1].y, dtype=float)) == 0
    flat = charts.pnl_figure(_pts([0, 0, 0, 0]), 10_000.0, "ALLES", 1_800_000_200.0, TZ)
    assert flat.data[0].line.color == charts.GREY and len(flat.data) == 3


def test_chart_handles_too_little_data_and_ranges():
    assert len(charts.pnl_figure([], 10_000.0, "24H", 1.0, TZ).data) == 0
    assert len(charts.pnl_figure(_pts([0]), 10_000.0, "24H", 1_800_000_000.0, TZ).data) == 0
    pts = _pts([0, 5, 10, 15, 20], step=3600.0 * 12)
    last = pts[-1][0]
    short = charts.pnl_figure(pts, 10_000.0, "1H", last, TZ)          # nothing within the last hour -> falls back to last 2 points
    assert len(short.data[0].x) >= 2
    assert charts.pnl_figure(pts, 10_000.0, "7T", last, TZ).data


# ───────────────────────── auth ─────────────────────────
@pytest.fixture
def pw(monkeypatch):
    monkeypatch.setattr("config.DASHBOARD_PASSWORD", "correct horse battery staple")


def test_cookie_token_roundtrip_and_tamper_protection(pw):
    t = auth.make_token(now=1000.0, days=30)
    assert auth.valid_token(t, now=1500.0)
    assert not auth.valid_token(t, now=1000.0 + 31 * 86400)           # expired
    exp, sig = t.split(".")
    assert not auth.valid_token(f"{int(exp) + 99999}.{sig}", now=1500.0)   # extended expiry with old signature
    assert not auth.valid_token(f"{exp}.{'0' * len(sig)}", now=1500.0)
    assert not auth.valid_token("", now=1.0) and not auth.valid_token("garbage", now=1.0) and not auth.valid_token("x.y", now=1.0)


def test_token_is_bound_to_the_password(monkeypatch, pw):
    t = auth.make_token(now=1000.0)
    monkeypatch.setattr("config.DASHBOARD_PASSWORD", "another password")
    assert not auth.valid_token(t, now=1500.0)                         # changing the password logs everyone out


def test_password_check(pw):
    assert auth.password_ok("correct horse battery staple") and not auth.password_ok("correct horse") and not auth.password_ok("")


def test_brute_force_lock(pw):
    client = f"test-{time.time()}"
    for i in range(auth.MAX_FAILS - 1):
        auth.register_fail(client, now=100.0 + i)
        assert auth.lock_remaining(client, now=105.0) == 0
    auth.register_fail(client, now=110.0)
    assert 0 < auth.lock_remaining(client, now=111.0) <= auth.LOCK_SEC
    assert auth.lock_remaining(client, now=110.0 + auth.LOCK_SEC + 1) == 0
    other = f"other-{time.time()}"
    assert auth.lock_remaining(other) == 0                              # one client's lock does not affect others
