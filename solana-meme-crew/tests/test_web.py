"""HTTP layer: pages, auth, CSRF, locked live mode, security headers, controls."""
import pytest
from fastapi.testclient import TestClient

import config
from crew.factory import Built
from crew.web import auth
from crew.web.app import create_app

from .helpers import make_engine

CSRF = {"X-Requested-With": "sol-crew"}


@pytest.fixture
def client(tmp_path):
    auth.reset_attempts()
    sim, pf, eng = make_engine(tmp_path, seed=21, auto=True)
    app = create_app(Built(engine=eng, market=sim, problems=[]))
    with TestClient(app) as c:
        yield c
    auth.reset_attempts()


def test_open_mode_serves_the_dashboard_and_state(client):
    r = client.get("/")
    assert r.status_code == 200 and "SOL·CREW" in r.text and "SYSTEM STARTEN" in r.text
    s = client.get("/api/state").json()
    assert s["mode"] == "paper" and s["engine"]["state"] == "stopped" and len(s["agents"]) == 4
    assert client.get("/healthz").json()["ok"] is True
    assert client.get("/api/equity?range=1h").json()["points"]


def test_security_headers_and_hidden_docs(client):
    r = client.get("/api/state")
    h = r.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in h["content-security-policy"] and "script-src 'self'" in h["content-security-policy"]
    assert h["cache-control"] == "no-store"
    assert client.get("/docs").status_code in (404, 405) and client.get("/openapi.json").status_code == 404


def test_static_assets_and_plotly_are_served(client):
    for path in ("/static/css/app.css", "/static/js/app.js", "/static/icons/apple-touch-icon.png", "/manifest.webmanifest"):
        assert client.get(path).status_code == 200, path
    js = client.get("/vendor/plotly.min.js")
    assert js.status_code == 200 and len(js.content) > 500_000 and "javascript" in js.headers["content-type"]


def test_control_actions_require_the_csrf_header(client):
    assert client.post("/api/control/start").status_code == 403
    r = client.post("/api/control/start", headers=CSRF)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert client.get("/api/state").json()["engine"]["state"] == "running"
    assert client.post("/api/control/pause", headers=CSRF).json()["ok"]
    assert client.get("/api/state").json()["engine"]["state"] == "stopped"
    p = client.post("/api/control/panic", headers=CSRF).json()
    assert p["ok"] and p["open_positions"] == 0
    assert client.post("/api/control/nonsense", headers=CSRF).status_code == 404


def test_password_protects_everything_but_login_and_health(client, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "s3cret-Pass")
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/api/state").status_code == 401
    assert client.post("/api/control/panic", headers=CSRF).status_code == 401
    assert client.get("/login").status_code == 200 and client.get("/healthz").status_code == 200


def test_login_flow_cookie_flags_and_logout(client, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "s3cret-Pass")
    assert client.post("/api/login", json={"password": "x"}).status_code == 403            # CSRF header missing
    bad = client.post("/api/login", json={"password": "wrong"}, headers=CSRF)
    assert bad.status_code == 401
    # behind the platform's https proxy the cookie is HttpOnly + SameSite=Lax + Secure
    https = client.post("/api/login", json={"password": "s3cret-Pass"}, headers={**CSRF, "x-forwarded-proto": "https"})
    assert https.status_code == 200
    cookie = https.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie
    client.cookies.clear()                       # (the test client talks plain http and would not send a Secure cookie back)
    ok = client.post("/api/login", json={"password": "s3cret-Pass"}, headers=CSRF)
    assert ok.status_code == 200 and "httponly" in ok.headers["set-cookie"].lower()
    assert client.get("/api/state").status_code == 200
    assert client.post("/api/control/start", headers=CSRF).status_code == 200
    client.post("/api/logout", headers=CSRF)
    client.cookies.clear()
    assert client.get("/api/state").status_code == 401


def test_forged_or_expired_session_cookies_are_rejected(client, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "s3cret-Pass")
    for bad in ("garbage", "9999999999.deadbeef", "1.abc", ".", ""):
        client.cookies.set(auth.COOKIE, bad)
        assert client.get("/api/state").status_code == 401, bad
    import time
    expired = f"{int(time.time()) - 10}.{auth._sign(int(time.time()) - 10)}"
    client.cookies.set(auth.COOKIE, expired)
    assert client.get("/api/state").status_code == 401
    assert auth.verify_token(auth.make_token())


def test_brute_force_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "s3cret-Pass")
    codes = [client.post("/api/login", json={"password": f"guess{i}"}, headers=CSRF).status_code for i in range(auth.MAX_ATTEMPTS + 2)]
    assert codes[:auth.MAX_ATTEMPTS] == [401] * auth.MAX_ATTEMPTS and codes[-1] == 429
    assert client.post("/api/login", json={"password": "s3cret-Pass"}, headers=CSRF).status_code == 429   # even the right one is blocked for now


def test_live_mode_without_password_is_completely_locked(client, monkeypatch):
    monkeypatch.setattr(config, "PAPER_TRADING", False)
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    r = client.get("/")
    assert r.status_code == 503 and "DASHBOARD_PASSWORD" in r.text
    assert client.get("/api/state").status_code == 503
    assert client.post("/api/control/start", headers=CSRF).status_code == 503
    assert client.get("/healthz").status_code == 200


def test_state_never_contains_secrets(client, monkeypatch):
    monkeypatch.setattr(config, "SOLANA_PRIVATE_KEY", "SUPERSECRETKEY" + "x" * 60)
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    text = client.get("/api/state").text + client.get("/").text
    assert "SUPERSECRETKEY" not in text


def test_selftest_endpoint_works_in_the_demo_market_and_is_rate_limited(client):
    assert client.post("/api/control/selftest").status_code == 403            # CSRF header required
    r = client.post("/api/control/selftest", headers=CSRF).json()
    assert r["ok"] is True and r["checks"][0]["name"] == "Demo-Markt" and "bereit" in r["message"]
    again = client.post("/api/control/selftest", headers=CSRF).json()          # served from the 15 s cache
    assert again == r
