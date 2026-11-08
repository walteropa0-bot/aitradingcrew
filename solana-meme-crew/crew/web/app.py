"""FastAPI application: serves the dashboard and the JSON API."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import config

from ..factory import Built, ConfigError, build
from ..logging_setup import setup_logging
from . import auth

log = logging.getLogger("crew.web")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

LOCKED_HTML = """<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SOL·CREW – Einrichtung nötig</title><body style="margin:0;background:#07080a;color:#e8ecf1;font:16px/1.5 -apple-system,system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;padding:24px">
<div style="max-width:460px;border:1px solid #2a1418;background:#12090b;border-radius:18px;padding:28px">
<div style="color:#ff4d6d;font-weight:700;letter-spacing:.08em;font-size:13px">LIVE-MODUS GESPERRT</div>
<h1 style="font-size:22px;margin:.4em 0">Dashboard-Passwort fehlt</h1>
<p style="color:#9aa4b2">Mit <b>PAPER_TRADING = False</b> wird echtes Geld bewegt. Deshalb ist das Dashboard erst nutzbar, wenn ein Passwort gesetzt ist.</p>
<p style="color:#9aa4b2">Replit → <b>Secrets</b> → neues Secret <code>DASHBOARD_PASSWORD</code> mit einem langen, einzigartigen Passwort anlegen und die App neu starten.</p>
</div></body></html>"""


def create_app(built: Optional[Built] = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(config.LOG_LEVEL)
        b = built
        if b is None:
            try:
                b = build()
            except ConfigError as exc:
                log.error("Konfigurationsfehler: %s", exc)
                raise SystemExit(f"\n❌ {exc}\n")
        app.state.built = b
        app.state.engine = b.engine
        app.state.started = time.time()
        task = asyncio.create_task(b.engine.run(), name="engine")

        def _engine_done(t: asyncio.Task) -> None:        # never let the engine die silently
            if not t.cancelled() and t.exception() is not None:
                log.error("ENGINE-TASK ABGESTÜRZT: %r", t.exception(), exc_info=t.exception())
                b.engine.last_error = f"Engine-Task abgestürzt: {t.exception()}"

        task.add_done_callback(_engine_done)
        app.state.engine_task = task
        mode = "DEMO (Spielgeld)" if config.PAPER_TRADING else "LIVE (ECHTES GELD)"
        log.warning("%s gestartet · Modus: %s · Persistenz: %s", config.APP_NAME, mode, b.engine.store.describe())
        for p in b.problems:
            log.warning("Hinweis: %s", p)
        try:
            yield
        finally:
            await b.engine.shutdown()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            close = getattr(b.market, "aclose", None)
            if close:
                await close()

    app = FastAPI(title=config.APP_NAME, version=config.VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(GZipMiddleware, minimum_size=800)

    # ── security headers ────────────────────────────────────────────────
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp: Response = await call_next(request)
        h = resp.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        h["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data: https:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        if auth.is_https(request):
            h["Strict-Transport-Security"] = "max-age=31536000"
        path = request.url.path
        if path.startswith("/api/") or path in ("/", "/login"):
            h["Cache-Control"] = "no-store"
        return resp

    def engine_of(request: Request):
        return request.app.state.engine

    def deny(request: Request, status: int = 401) -> Optional[Response]:
        if auth.locked():
            return JSONResponse({"ok": False, "message": "Live-Modus gesperrt: DASHBOARD_PASSWORD fehlt."}, status_code=503)
        if not auth.is_authenticated(request):
            return JSONResponse({"ok": False, "message": "Bitte anmelden."}, status_code=status)
        return None

    def guard_post(request: Request) -> Optional[Response]:
        d = deny(request)
        if d is not None:
            return d
        if not auth.csrf_ok(request):
            return JSONResponse({"ok": False, "message": "Ungültige Anfrage (CSRF-Schutz)."}, status_code=403)
        return None

    # ── pages ───────────────────────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    async def index(request: Request):
        if auth.locked():
            return HTMLResponse(LOCKED_HTML, status_code=503)
        if not auth.is_authenticated(request):
            return RedirectResponse("/login", status_code=303)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"), media_type="text/html")

    @app.get("/login", include_in_schema=False)
    async def login_page(request: Request):
        if auth.locked():
            return HTMLResponse(LOCKED_HTML, status_code=503)
        if auth.is_authenticated(request) and auth.auth_enabled():
            return RedirectResponse("/", status_code=303)
        if not auth.auth_enabled():
            return RedirectResponse("/", status_code=303)
        return FileResponse(os.path.join(STATIC_DIR, "login.html"), media_type="text/html")

    @app.get("/healthz", include_in_schema=False)
    async def healthz(request: Request):
        eng = getattr(request.app.state, "engine", None)
        return {"ok": eng is not None, "mode": "paper" if config.PAPER_TRADING else "live",
                "state": eng.state if eng else "starting"}

    @app.get("/manifest.webmanifest", include_in_schema=False)
    async def manifest():
        return JSONResponse({
            "name": config.APP_NAME, "short_name": "SOL·CREW", "start_url": "/", "display": "standalone",
            "background_color": "#07080a", "theme_color": "#07080a",
            "icons": [{"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}],
        }, media_type="application/manifest+json")

    @app.get("/vendor/plotly.min.js", include_in_schema=False)
    async def plotly_js():
        js = _plotly_bundle()
        if js is None:
            return Response("/* plotly not installed */", media_type="application/javascript", status_code=404)
        return Response(js, media_type="application/javascript", headers={"Cache-Control": "public, max-age=86400"})

    # ── auth API ────────────────────────────────────────────────────────
    @app.post("/api/login")
    async def api_login(request: Request):
        if auth.locked():
            return JSONResponse({"ok": False, "message": "Live-Modus gesperrt: DASHBOARD_PASSWORD fehlt."}, status_code=503)
        if not auth.csrf_ok(request):
            return JSONResponse({"ok": False, "message": "Ungültige Anfrage."}, status_code=403)
        ip = auth.client_ip(request)
        wait = auth.rate_limited(ip)
        if wait > 0:
            return JSONResponse({"ok": False, "message": f"Zu viele Versuche. Bitte in {int(wait // 60) + 1} Min. erneut versuchen."},
                                status_code=429, headers={"Retry-After": str(int(wait))})
        try:
            data = await request.json()
        except Exception:
            data = {}
        if not auth.check_password(str((data or {}).get("password", ""))):
            auth.register_failure(ip)
            await asyncio.sleep(0.6)
            return JSONResponse({"ok": False, "message": "Falsches Passwort."}, status_code=401)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(auth.COOKIE, auth.make_token(), max_age=auth.COOKIE_MAX_AGE, httponly=True, samesite="lax",
                        secure=auth.is_https(request), path="/")
        return resp

    @app.post("/api/logout")
    async def api_logout(request: Request):
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.COOKIE, path="/")
        return resp

    # ── data API ────────────────────────────────────────────────────────
    @app.get("/api/state")
    async def api_state(request: Request, since: int = 0):
        d = deny(request)
        if d is not None:
            return d
        eng = engine_of(request)
        snap = eng.snapshot(max(0, since))
        snap["auth"] = {"protected": auth.auth_enabled()}
        for text in request.app.state.built.problems:
            snap["banners"].append({"level": "warn", "text": text})
        return JSONResponse(snap)

    @app.get("/api/equity")
    async def api_equity(request: Request, range: str = "6h"):
        d = deny(request)
        if d is not None:
            return d
        if range not in ("1h", "6h", "24h", "7d", "all"):
            range = "6h"
        return JSONResponse(engine_of(request).equity_points(range))

    # ── control API ─────────────────────────────────────────────────────
    @app.post("/api/control/{action}")
    async def api_control(request: Request, action: str):
        g = guard_post(request)
        if g is not None:
            return g
        eng = engine_of(request)
        if action == "start":
            return JSONResponse(await eng.start())
        if action == "pause":
            return JSONResponse(await eng.pause())
        if action == "panic":
            return JSONResponse(await eng.panic())
        if action == "reset":
            return JSONResponse(await eng.reset_paper())
        if action == "selftest":
            cached = getattr(request.app.state, "selftest", None)
            if cached and time.time() - cached[0] < 15:          # at most one run per 15 s (rate limits!)
                return JSONResponse(cached[1])
            from ..selftest import run_checks, summarize
            result = summarize(await run_checks(eng.market, eng.executor))
            request.app.state.selftest = (time.time(), result)
            return JSONResponse(result)
        return JSONResponse({"ok": False, "message": "Unbekannte Aktion"}, status_code=404)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


_PLOTLY_CACHE: dict[str, Any] = {}


def _plotly_bundle() -> Optional[str]:
    if "js" not in _PLOTLY_CACHE:
        try:
            from plotly.offline import get_plotlyjs
            _PLOTLY_CACHE["js"] = get_plotlyjs()
        except Exception as exc:
            log.warning("plotly.js nicht verfügbar (%s) – Dashboard nutzt den eingebauten Ersatz-Chart", exc)
            _PLOTLY_CACHE["js"] = None
    return _PLOTLY_CACHE["js"]
