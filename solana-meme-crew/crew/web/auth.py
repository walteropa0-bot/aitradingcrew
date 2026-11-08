"""Dashboard authentication.

  * One shared password (env DASHBOARD_PASSWORD), compared in constant time.
  * Signed, expiring session cookie (HMAC-SHA256), HttpOnly + SameSite=Lax (+Secure on https).
  * Brute-force protection: 8 failed attempts / 10 min per client IP -> 429.
  * CSRF: every state-changing request must carry the custom header
    ``X-Requested-With: sol-crew`` (browsers cannot send it cross-site without CORS).
  * LIVE mode without a password is *locked* completely.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict, deque

from fastapi import Request

import config

COOKIE = "crew_session"
COOKIE_MAX_AGE = 30 * 24 * 3600
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "sol-crew"
_ATTEMPTS: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
MAX_ATTEMPTS, WINDOW = 8, 600.0


def auth_enabled() -> bool:
    return bool(config.DASHBOARD_PASSWORD)


def locked() -> bool:
    """LIVE trading without a dashboard password is not allowed at all."""
    return (not config.PAPER_TRADING) and not config.DASHBOARD_PASSWORD


def _key() -> bytes:
    base = config.SESSION_SECRET or ("crew-session|" + config.DASHBOARD_PASSWORD)
    return hashlib.sha256(base.encode()).digest()


def _sign(exp: int) -> str:
    return hmac.new(_key(), f"v1.{exp}".encode(), hashlib.sha256).hexdigest()


def make_token(now: float | None = None) -> str:
    exp = int((now or time.time()) + COOKIE_MAX_AGE)
    return f"{exp}.{_sign(exp)}"


def verify_token(token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    exp_s, sig = token.split(".", 1)
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    if exp < (now or time.time()):
        return False
    return hmac.compare_digest(sig, _sign(exp))


def check_password(candidate: str) -> bool:
    a = hashlib.sha256((candidate or "").encode()).digest()
    b = hashlib.sha256(config.DASHBOARD_PASSWORD.encode()).digest()
    return hmac.compare_digest(a, b) and bool(config.DASHBOARD_PASSWORD)


def client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:                                   # last entry = the one added by the platform proxy
        return xff.split(",")[-1].strip() or "unknown"
    return request.client.host if request.client else "unknown"


def rate_limited(ip: str, now: float | None = None) -> float:
    """Seconds to wait (0 = allowed)."""
    t = now or time.time()
    dq = _ATTEMPTS[ip]
    while dq and t - dq[0] > WINDOW:
        dq.popleft()
    if len(dq) >= MAX_ATTEMPTS:
        return max(1.0, WINDOW - (t - dq[0]))
    return 0.0


def register_failure(ip: str, now: float | None = None) -> None:
    _ATTEMPTS[ip].append(now or time.time())


def reset_attempts() -> None:
    _ATTEMPTS.clear()


def is_authenticated(request: Request) -> bool:
    if locked():
        return False
    if not auth_enabled():
        return True                           # paper mode, no password -> open (banner warns)
    return verify_token(request.cookies.get(COOKIE))


def csrf_ok(request: Request) -> bool:
    return request.headers.get(CSRF_HEADER, "").lower() == CSRF_VALUE


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"
