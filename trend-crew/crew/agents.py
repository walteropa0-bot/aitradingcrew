"""Live status of the three "agents" and the activity journal the dashboard animates.

The agents are the three mathematical stages (Trend-Scout, Tech-Analyst, Risk manager). Each one has a
state that the dashboard shows as a pulsing dot plus a live text line. "working" is shown while
the stage is really busy (or was a moment ago), then it settles to ok / warn / error with its result.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Callable, Optional

STATES = ("idle", "working", "ok", "warn", "error")


class AgentStatus:
    def __init__(self, id_: str, name: str, role: str, icon: str, clock: Callable[[], float] = time.time) -> None:
        self.id, self.name, self.role, self.icon = id_, name, role, icon
        self._clock = clock
        self.state = "idle"
        self.text = "Wartet auf Start"
        self.detail = ""
        self.work_text = ""             # what the agent was doing the last time it started working
        self.activity_ts = 0.0          # when the agent last started working
        self.updated = clock()
        self.runs = 0

    def work(self, text: str, detail: str = "") -> None:
        now = self._clock()
        self.state, self.text, self.detail = "working", text, detail
        self.work_text = text
        self.activity_ts = self.updated = now
        self.runs += 1

    def done(self, state: str, text: str, detail: str = "") -> None:
        assert state in STATES
        self.state, self.text, self.detail = state, text, detail
        self.updated = self._clock()

    def snapshot(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "role": self.role, "icon": self.icon, "state": self.state,
                "text": self.text, "detail": self.detail, "work_text": self.work_text, "activity_ts": self.activity_ts, "updated": self.updated,
                "runs": self.runs}


class Journal:
    """Bounded activity log: (ts, agent, level, kind, text)."""

    def __init__(self, maxlen: int = 300, clock: Callable[[], float] = time.time) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._clock = clock
        self.seq = 0

    def add(self, agent: str, text: str, level: str = "info", kind: str = "agent") -> None:
        self.seq += 1
        self._items.append({"seq": self.seq, "ts": self._clock(), "agent": agent, "level": level, "kind": kind, "text": text})

    def recent(self, n: int = 40, kinds: Optional[set] = None) -> list[dict[str, Any]]:
        items = [i for i in self._items if kinds is None or i["kind"] in kinds]
        return items[-n:][::-1]
