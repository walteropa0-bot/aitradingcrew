import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="crew-test-"))

import pytest  # noqa: E402

import config  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch, tmp_path):
    """Every test starts from the safe defaults, whatever the environment says."""
    monkeypatch.setattr(config, "PAPER_TRADING", True)
    monkeypatch.setattr(config, "DASHBOARD_PASSWORD", "")
    monkeypatch.setattr(config, "SOLANA_PRIVATE_KEY", "")
    monkeypatch.setattr(config, "USE_SIMULATED_MARKET", True)
    monkeypatch.setattr(config, "DATABASE_URL", "")
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "UI_STEP_DELAY_SEC", 0.0)
    yield
