"""Resilient async HTTP layer: retries, rate limiting, circuit breaker, health.

Every external API (Dexscreener, GeckoTerminal, RugCheck, Jupiter, Solana RPC)
goes through ``HttpService`` so that a flaky provider can never freeze or crash
the trading loop - and so the dashboard can show per-provider health.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from typing import Any, Callable, Optional

import httpx

from .util import redact_url

log = logging.getLogger("crew.http")

USER_AGENT = "sol-crew/1.0 (+personal trading bot)"


class SourceDown(Exception):
    """The provider is temporarily disabled by the circuit breaker."""


class ApiError(Exception):
    def __init__(self, source: str, message: str, status: int = 0, body: Any = None) -> None:
        super().__init__(f"[{source}] {redact_url(message)}")
        self.source = source
        self.status = status
        self.body = body


class RateLimiter:
    """Sliding-window limiter + minimum spacing, with priority headroom.

    ``acquire(reserve=k)`` only proceeds while at least k slots stay free for
    more important callers (e.g. price polling must never starve trade orders).
    """

    def __init__(self, max_calls: int, window: float = 60.0, min_interval: float = 0.0) -> None:
        self.max_calls = max(1, int(max_calls))
        self.window = float(window)
        self.min_interval = float(min_interval)
        self._calls: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = asyncio.Lock()

    def _prune(self, t: float) -> None:
        while self._calls and t - self._calls[0] >= self.window:
            self._calls.popleft()

    def _wait_time(self, t: float, reserve: int) -> float:
        self._prune(t)
        wait = max(0.0, self._blocked_until - t)
        if self._calls and self.min_interval:
            wait = max(wait, self._calls[-1] + self.min_interval - t)
        limit = max(1, self.max_calls - reserve)
        if len(self._calls) >= limit:
            wait = max(wait, self._calls[len(self._calls) - limit] + self.window - t)
        return wait

    def free_slots(self) -> int:
        self._prune(time.monotonic())
        return self.max_calls - len(self._calls)

    async def acquire(self, reserve: int = 0) -> None:
        async with self._lock:
            while True:
                t = time.monotonic()
                wait = self._wait_time(t, reserve)
                if wait <= 0:
                    self._calls.append(t)
                    return
                await asyncio.sleep(min(wait, 5.0))

    def try_acquire(self, reserve: int = 0) -> bool:
        t = time.monotonic()
        if self._wait_time(t, reserve) > 0:
            return False
        self._calls.append(t)
        return True

    def penalize(self, seconds: float) -> None:
        self._blocked_until = max(self._blocked_until, time.monotonic() + max(0.0, seconds))


class HttpService:
    def __init__(
        self,
        name: str,
        base_url: str = "",
        *,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 12.0,
        limiter: Optional[RateLimiter] = None,
        retries: int = 2,
        backoff: float = 0.6,
        failure_threshold: int = 6,
        cooldown: float = 25.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        response_hook: Optional[Callable[[httpx.Response], None]] = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
        self.timeout = timeout
        self.limiter = limiter
        self.retries = retries
        self.backoff = backoff
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._hook = response_hook
        # health
        self.calls = 0
        self.failures = 0
        self.consecutive_failures = 0
        self.last_ok: float = 0.0
        self.last_error: str = ""
        self.last_error_ts: float = 0.0
        self.last_latency_ms: float = 0.0
        self._lat: deque[float] = deque(maxlen=30)
        self._down_until = 0.0

    # ── client lifecycle ────────────────────────────────────────────────
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=min(6.0, self.timeout)),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=8),
                transport=self._transport,
                follow_redirects=False,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── health ──────────────────────────────────────────────────────────
    @property
    def is_down(self) -> bool:
        return time.monotonic() < self._down_until

    def health(self) -> dict[str, Any]:
        avg = sum(self._lat) / len(self._lat) if self._lat else 0.0
        if self.is_down:
            status = "down"
        elif self.calls == 0:
            status = "idle"
        elif self.consecutive_failures >= 2:
            status = "degraded"
        else:
            status = "ok"
        return {
            "name": self.name,
            "status": status,
            "calls": self.calls,
            "failures": self.failures,
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "latency_ms": round(avg),
        }

    def _record_ok(self, latency_ms: float) -> None:
        self.consecutive_failures = 0
        self.last_ok = time.time()
        self.last_latency_ms = latency_ms
        self._lat.append(latency_ms)

    def _record_fail(self, err: str) -> None:
        self.failures += 1
        self.consecutive_failures += 1
        self.last_error = redact_url(err)[:240]
        self.last_error_ts = time.time()
        if self.consecutive_failures >= self.failure_threshold:
            self._down_until = time.monotonic() + self.cooldown
            self.consecutive_failures = 0
            log.warning("%s: too many failures -> paused for %.0fs (%s)", self.name, self.cooldown, self.last_error)

    # ── requests ────────────────────────────────────────────────────────
    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path:                       # e.g. JSON-RPC endpoints whose URL carries ?api-key=...
            return self.base_url
        return f"{self.base_url}/{path.lstrip('/')}" if self.base_url else path

    async def request(
        self,
        method: str,
        path: str = "",
        *,
        params: Optional[dict[str, Any]] = None,
        json: Any = None,
        headers: Optional[dict[str, str]] = None,
        retries: Optional[int] = None,
        timeout: Optional[float] = None,
        reserve: int = 0,
        raise_on_error: bool = True,
        force: bool = False,
        pre_acquired: bool = False,
    ) -> httpx.Response:
        if self.is_down and not force:
            raise SourceDown(f"{self.name} pausiert (zu viele Fehler)")
        attempts = (self.retries if retries is None else retries) + 1
        last_err = "unknown error"
        last_status = 0
        for attempt in range(attempts):
            if self.limiter is not None and not (pre_acquired and attempt == 0):
                await self.limiter.acquire(reserve)
            t0 = time.monotonic()
            self.calls += 1
            try:
                resp = await self._http().request(
                    method,
                    self._url(path),
                    params=params,
                    json=json,
                    headers={**self.headers, **(headers or {})},
                    timeout=timeout,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                last_status = 0
                self._record_fail(last_err)
                if attempt + 1 < attempts:
                    await asyncio.sleep(self.backoff * (2 ** attempt) + random.random() * 0.25)
                continue
            latency = (time.monotonic() - t0) * 1000.0
            if self._hook is not None:
                try:
                    self._hook(resp)
                except Exception:  # hooks must never break requests
                    log.debug("response hook failed", exc_info=True)
            status = resp.status_code
            if status == 429:
                wait = _retry_after(resp)
                last_err, last_status = f"HTTP 429 rate limited (wait {wait:.1f}s)", 429
                if self.limiter is not None:
                    self.limiter.penalize(wait)
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(wait, 8.0))
                    continue
                self._record_fail(last_err)
                break
            if status >= 500:
                last_err, last_status = f"HTTP {status}", status
                self._record_fail(last_err)
                if attempt + 1 < attempts:
                    await asyncio.sleep(self.backoff * (2 ** attempt) + random.random() * 0.25)
                    continue
                if not raise_on_error:
                    return resp
                break
            # 2xx / 3xx / 4xx -> the server answered properly
            self._record_ok(latency)
            if status >= 400 and raise_on_error:
                raise ApiError(self.name, f"HTTP {status} {_short_body(resp)}", status, _try_json(resp))
            return resp
        raise ApiError(self.name, last_err, last_status)

    async def get_json(self, path: str = "", *, params: Optional[dict[str, Any]] = None, **kw: Any) -> Any:
        resp = await self.request("GET", path, params=params, **kw)
        try:
            return resp.json()
        except ValueError as exc:
            self._record_fail("invalid JSON")
            raise ApiError(self.name, f"invalid JSON ({exc})", resp.status_code) from exc

    async def post_json(self, path: str = "", *, json: Any = None, **kw: Any) -> Any:
        resp = await self.request("POST", path, json=json, **kw)
        try:
            return resp.json()
        except ValueError as exc:
            self._record_fail("invalid JSON")
            raise ApiError(self.name, f"invalid JSON ({exc})", resp.status_code) from exc


def _retry_after(resp: httpx.Response) -> float:
    for key in ("retry-after",):
        v = resp.headers.get(key)
        if v:
            try:
                return max(0.5, min(float(v), 30.0))
            except ValueError:
                pass
    reset = resp.headers.get("x-ratelimit-reset")  # unix seconds (Jupiter)
    if reset:
        try:
            return max(0.5, min(float(reset) - time.time(), 30.0))
        except ValueError:
            pass
    return 2.0


def _try_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None


def _short_body(resp: httpx.Response) -> str:
    try:
        return resp.text[:200].replace("\n", " ")
    except Exception:
        return ""
