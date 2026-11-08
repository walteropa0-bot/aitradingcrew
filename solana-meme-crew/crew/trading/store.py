"""State persistence.

  * FileStore  - atomic JSON file (+ rolling backup). Default; fine on any host
                 with a persistent disk and when running locally.
  * PgStore    - one JSON row in Postgres (set DATABASE_URL). Recommended on
                 Replit: files written by a *published* app are not guaranteed
                 to survive a restart/republish, the database is.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import config

log = logging.getLogger("crew.store")


class StateStore(ABC):
    kind = "abstract"

    @abstractmethod
    def load(self) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def save(self, state: dict[str, Any]) -> None: ...

    def describe(self) -> str:
        return self.kind


class FileStore(StateStore):
    kind = "file"

    BACKUP_EVERY_SEC = 300.0

    def __init__(self, path: str) -> None:
        self.path = path
        self._last_backup = 0.0
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def load(self) -> Optional[dict[str, Any]]:
        for candidate in (self.path, self.path + ".bak"):
            try:
                with open(candidate, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    if candidate != self.path:
                        log.warning("Hauptdatei beschädigt - Backup geladen: %s", candidate)
                    return data
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                log.warning("State-Datei %s nicht lesbar: %s", candidate, exc)
        return None

    def save(self, state: dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            now_t = time.time()
            if os.path.exists(self.path) and now_t - self._last_backup >= self.BACKUP_EVERY_SEC:
                try:                                  # keep the previous good state as a rolling backup
                    shutil.copy2(self.path, self.path + ".bak")
                    self._last_backup = now_t
                except OSError:
                    pass
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def describe(self) -> str:
        return "Lokale Datei"


class PgStore(StateStore):
    kind = "postgres"

    def __init__(self, dsn: str, key: str) -> None:
        try:
            import psycopg  # type: ignore
        except ImportError as exc:                       # pragma: no cover
            raise RuntimeError("psycopg ist nicht installiert (pip install 'psycopg[binary]')") from exc
        self._psycopg = psycopg
        self.dsn = dsn
        self.key = key
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS crew_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at DOUBLE PRECISION NOT NULL)"
            )

    def _connect(self):
        return self._psycopg.connect(self.dsn, autocommit=True, connect_timeout=8)

    def load(self) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM crew_state WHERE key = %s", (self.key,)).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row[0])
            return data if isinstance(data, dict) else None
        except ValueError:
            log.warning("Datenbank-State beschädigt")
            return None

    def save(self, state: dict[str, Any]) -> None:
        payload = json.dumps(state, separators=(",", ":"))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO crew_state (key, value, updated_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at",
                (self.key, payload, time.time()),
            )

    def describe(self) -> str:
        return "Postgres-Datenbank"


def make_store(mode: str) -> StateStore:
    """Postgres if DATABASE_URL is set (and reachable), otherwise a local file."""
    if config.DATABASE_URL:
        try:
            store = PgStore(config.DATABASE_URL, f"state_{mode}")
            log.info("Persistenz: Postgres-Datenbank")
            return store
        except Exception as exc:
            log.error("Postgres nicht nutzbar (%s) - falle auf Datei zurück", exc)
    return FileStore(os.path.join(config.DATA_DIR, f"state_{mode}.json"))
