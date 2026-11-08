"""Optional integration test against a REAL PostgreSQL server.
Run with:  CREW_TEST_PG_URL=postgresql://user@host:5432/db pytest tests/test_pg_real.py"""
import json
import os
import time

import pytest

import config
from crew.trading.store import PgStore, make_store

URL = os.environ.get("CREW_TEST_PG_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="set CREW_TEST_PG_URL to run against a real PostgreSQL")


def test_pgstore_against_real_postgres(monkeypatch):
    key = f"state_test_{int(time.time())}"
    st = PgStore(URL, key)
    assert st.load() is None
    payload = {"cash_sol": 9.5, "positions": [{"id": "p1", "qty": 1.5}], "trades": list(range(500)), "equity": [[1.0, 2.0]] * 3000}
    st.save(payload)
    assert st.load() == payload
    st.save({"cash_sol": 1.0})                                       # ON CONFLICT ... DO UPDATE
    assert st.load() == {"cash_sol": 1.0}
    other = PgStore(URL, key + "_live")
    assert other.load() is None                                       # modes are isolated
    other.save({"x": 1})
    assert st.load() == {"cash_sol": 1.0} and other.load() == {"x": 1}
    monkeypatch.setattr(config, "DATABASE_URL", URL)
    s3 = make_store(key.replace("state_", ""))
    assert isinstance(s3, PgStore) and s3.describe() == "Postgres-Datenbank"
    assert len(json.dumps(payload)) > 30_000


def test_unreachable_database_falls_back_to_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db?connect_timeout=2")
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    t = time.time()
    store = make_store("paper")
    assert store.describe() == "Lokale Datei" and time.time() - t < 12
