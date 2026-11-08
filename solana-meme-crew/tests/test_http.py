import time

import httpx
import pytest

from crew.http import ApiError, HttpService, RateLimiter, SourceDown


def svc(handler, **kw):
    kw.setdefault("retries", 2)
    kw.setdefault("backoff", 0.001)
    return HttpService("t", "https://api.test", transport=httpx.MockTransport(handler), **kw)


async def test_retries_on_5xx_then_succeeds():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json={"ok": True})

    s = svc(h)
    assert await s.get_json("/x") == {"ok": True}
    assert len(calls) == 3 and s.health()["status"] == "ok"


async def test_4xx_is_not_retried_and_raises():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(404, json={"error": "nope"})

    s = svc(h)
    with pytest.raises(ApiError) as ei:
        await s.get_json("/x")
    assert len(calls) == 1 and ei.value.status == 404 and ei.value.body == {"error": "nope"}


async def test_raise_on_error_false_returns_the_response():
    s = svc(lambda r: httpx.Response(400, json={"error": "bad"}))
    resp = await s.request("GET", "/x", raise_on_error=False)
    assert resp.status_code == 400 and resp.json() == {"error": "bad"}


async def test_429_waits_and_retries():
    calls = []

    def h(req):
        calls.append(time.monotonic())
        return httpx.Response(429, headers={"retry-after": "0.5"}) if len(calls) == 1 else httpx.Response(200, json={"a": 1})

    s = svc(h)
    assert await s.get_json("/x") == {"a": 1}
    assert len(calls) == 2 and calls[1] - calls[0] >= 0.45


async def test_network_error_retries_then_raises_apierror():
    def h(req):
        raise httpx.ConnectError("boom")

    s = svc(h, retries=1)
    with pytest.raises(ApiError):
        await s.get_json("/x")
    assert s.failures == 2


async def test_circuit_breaker_opens_and_fast_fails():
    s = svc(lambda r: httpx.Response(500), retries=0, failure_threshold=3, cooldown=30)
    for _ in range(3):
        with pytest.raises(ApiError):
            await s.get_json("/x")
    assert s.is_down and s.health()["status"] == "down"
    with pytest.raises(SourceDown):
        await s.get_json("/x")


async def test_invalid_json_raises_apierror():
    s = svc(lambda r: httpx.Response(200, content=b"<html>"))
    with pytest.raises(ApiError):
        await s.get_json("/x")


async def test_empty_path_keeps_rpc_url_with_query_intact():
    seen = []

    def h(req):
        seen.append(str(req.url))
        return httpx.Response(200, json={})

    s = HttpService("rpc", "https://rpc.test/?api-key=abc", transport=httpx.MockTransport(h), retries=0)
    await s.post_json("", json={})
    assert seen == ["https://rpc.test/?api-key=abc"]


async def test_rate_limiter_spaces_calls_and_respects_window():
    lim = RateLimiter(max_calls=2, window=0.4, min_interval=0.0)
    t0 = time.monotonic()
    for _ in range(3):
        await lim.acquire()
    assert time.monotonic() - t0 >= 0.38           # the 3rd call must wait for the window to slide


async def test_rate_limiter_reserve_headroom_and_try_acquire():
    lim = RateLimiter(max_calls=4, window=5.0)
    assert lim.try_acquire(reserve=2) and lim.try_acquire(reserve=2)
    assert not lim.try_acquire(reserve=2)           # low-priority callers stop at 2 of 4
    assert lim.try_acquire(reserve=0)               # high-priority callers may still use the headroom
    assert lim.free_slots() == 1
