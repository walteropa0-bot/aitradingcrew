"""Agent base class: owns a live status that the dashboard animates.

States:  idle | analyzing (blue) | confirmed (green) | blocked (red) | waiting (amber) | error
"""
from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Optional

from ..events import EventBus
from ..util import now

_LEVEL = {"idle": "info", "analyzing": "info", "confirmed": "ok", "blocked": "bad", "waiting": "warn", "error": "bad"}


class Agent:
    id = "agent"
    name = "Agent"
    role = ""
    icon = "bot"

    def __init__(self, bus: EventBus, step_delay: float = 0.0) -> None:
        self.bus = bus
        self.step_delay = step_delay
        self.state = "idle"
        self.message = "Wartet auf Start"
        self.progress: Optional[float] = None
        self.target: Optional[str] = None
        self.updated = now()
        self.stats: dict[str, int] = {"checked": 0, "passed": 0, "blocked": 0}
        self.history: deque[dict[str, Any]] = deque(maxlen=8)

    def set(self, state: str, message: str, *, progress: Optional[float] = None, target: Optional[str] = None,
            level: Optional[str] = None, emit: bool = True) -> None:
        self.state = state
        self.message = message
        self.progress = progress
        self.target = target
        self.updated = now()
        self.history.append({"ts": round(self.updated, 2), "state": state, "message": message, "target": target})
        if emit:
            self.bus.emit(self.id, message, level=level or _LEVEL.get(state, "info"), state=state,
                          target=target, progress=progress)

    async def beat(self, factor: float = 1.0) -> None:
        """Cosmetic pause so a human can follow what the agent is doing."""
        if self.step_delay > 0:
            await asyncio.sleep(self.step_delay * factor)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "role": self.role, "icon": self.icon,
            "state": self.state, "message": self.message, "progress": self.progress, "target": self.target,
            "updated": self.updated, "stats": dict(self.stats), "history": list(self.history)[-6:],
        }
