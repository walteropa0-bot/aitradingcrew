"""Event bus: a bounded, sequence-numbered stream the dashboard can replay.

Every status change of an agent, every trade and every warning becomes an
event. The browser polls with ``since=<last seq>`` and plays the new events
back one by one -> the animated "live work" text of the agent tracker.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any, Optional

from .util import now

log = logging.getLogger("crew.events")


class EventBus:
    def __init__(self, maxlen: int = 800) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._seq = 0

    @property
    def last_seq(self) -> int:
        return self._seq

    def emit(
        self,
        agent: str,
        message: str,
        *,
        level: str = "info",          # info | ok | warn | bad
        state: Optional[str] = None,  # idle | analyzing | confirmed | blocked | waiting | error
        target: Optional[str] = None,
        progress: Optional[float] = None,
        kind: str = "agent",          # agent | trade | system | alert
        data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self._seq += 1
        ev = {
            "seq": self._seq,
            "ts": round(now(), 3),
            "kind": kind,
            "agent": agent,
            "level": level,
            "state": state,
            "message": message,
            "target": target,
            "progress": progress,
        }
        if data:
            ev["data"] = data
        self._events.append(ev)
        return ev

    def since(self, seq: int, limit: int = 250) -> list[dict[str, Any]]:
        if seq >= self._seq:
            return []
        out = [e for e in self._events if e["seq"] > seq]
        return out[-limit:]

    def recent(self, n: int = 60, kinds: Optional[set[str]] = None) -> list[dict[str, Any]]:
        evs = [e for e in self._events if kinds is None or e["kind"] in kinds]
        return evs[-n:]
