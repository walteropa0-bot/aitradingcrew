"""Logging that can never leak secrets (API key, secret, password)."""
from __future__ import annotations

import logging
import sys
from typing import Iterable

from .util import redact_url

_SECRETS: set[str] = set()


def register_secret(value: str | None) -> None:
    """Every occurrence of this value will be replaced by *** in all log output."""
    if value and len(value) >= 6:
        _SECRETS.add(value)


def redact(text: str) -> str:
    out = redact_url(text)
    for s in sorted(_SECRETS, key=len, reverse=True):
        if s in out:
            out = out.replace(s, "***")
    return out


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # message + traceback
        return redact(super().format(record))


def setup_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> None:
    for s in secrets:
        register_secret(s)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"))
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for noisy in ("urllib3", "asyncio", "tornado.access", "watchdog", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
