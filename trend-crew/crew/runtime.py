"""Process-wide singleton: builds the engine from config.py / environment variables and keeps its thread alive.

Streamlit re-runs the dashboard script for every browser session, but this module is imported only once per
process, so there is exactly ONE engine no matter how many phones are looking at the dashboard.
"""
from __future__ import annotations

import atexit
import logging
import os
import threading
import time
from typing import Optional

import config
from . import strategy as st
from .engine import Engine, Settings
from .exchange import Exchange
from .logging_setup import register_secret, setup_logging
from .store import make_store
from .util import TIMEFRAME_SECONDS

log = logging.getLogger("crew.runtime")

_lock = threading.Lock()
_engine: Optional[Engine] = None
_supervisor: Optional[threading.Thread] = None

KNOWN_EXCHANGES = ("kraken", "binance", "coinbase", "bitstamp", "bitvavo", "okx", "bybit", "kucoin", "gemini")


def validate_config() -> list[str]:
    """Human-readable problems with the settings (empty list = all fine)."""
    problems: list[str] = []
    if config.TIMEFRAME not in ("1h", "2h", "4h", "6h", "12h", "1d"):
        problems.append(f"TIMEFRAME={config.TIMEFRAME!r} ist ungültig. Erlaubt: 1h, 2h, 4h, 6h, 12h, 1d.")
    if config.HTF_TIMEFRAME not in TIMEFRAME_SECONDS:
        problems.append(f"HTF_TIMEFRAME={config.HTF_TIMEFRAME!r} ist ungültig.")
    if config.STRATEGY_MODE not in ("breakout", "dip"):
        problems.append(f"STRATEGY_MODE={config.STRATEGY_MODE!r} ist ungültig. Erlaubt: breakout oder dip.")
    if not config.ASSETS:
        problems.append("ASSETS ist leer. Beispiel: BTC,ETH,SOL")
    if config.EXCHANGE not in KNOWN_EXCHANGES and not _ccxt_has(config.EXCHANGE):
        problems.append(f"EXCHANGE={config.EXCHANGE!r} ist unbekannt. Empfohlen: kraken.")
    if config.CANDLE_HISTORY < st.Params.from_config().warmup + 20:
        problems.append("CANDLE_HISTORY ist zu klein für die Indikatoren.")
    return problems


def _ccxt_has(name: str) -> bool:
    try:
        import ccxt
        return hasattr(ccxt, name)
    except ImportError:
        return False


def build_engine() -> Engine:
    setup_logging(config.LOG_LEVEL, secrets=())
    for secret in (config.API_KEY, config.API_SECRET, config.API_PASSWORD, config.DASHBOARD_PASSWORD, config.DATABASE_URL):
        register_secret(secret)
    settings = Settings.from_config()
    params = st.Params.from_config()
    clock = time.time
    if os.environ.get("USE_SIMULATED_MARKET", "").strip() in ("1", "true", "yes", "ja") and config.PAPER_TRADING:
        from .simexchange import SimExchange, VirtualClock
        clock = VirtualClock(float(os.environ.get("SIM_SPEED", "1") or 1))
        exchange = SimExchange(list(settings.symbols), clock=clock)
        settings.exchange_name = "demo-markt"
        log.warning("DEMO-MARKT: künstliche Kurse (kein Internet nötig)")
    else:
        exchange = Exchange(config.EXCHANGE, api_key=config.API_KEY, secret=config.API_SECRET,
                            password=config.API_PASSWORD, sandbox=config.EXCHANGE_SANDBOX)
    store = make_store(settings.mode)
    return Engine(exchange, store, params, settings, clock=clock)


def _shutdown() -> None:
    """Process is ending (Replit stop / redeploy): save the books one last time."""
    eng = _engine
    if eng is None:
        return
    try:
        eng.stop_thread(timeout=3.0)
        eng._dirty = True
        eng._save(eng.clock())
        log.info("Beendet – Stand gespeichert")
    except Exception:                                            # noqa: BLE001
        log.exception("Speichern beim Beenden fehlgeschlagen")


def get_engine() -> Engine:
    global _engine, _supervisor
    with _lock:
        if _engine is None:
            _engine = build_engine()
            atexit.register(_shutdown)
        if not _engine.is_alive():
            _engine.start_thread()
        if _supervisor is None or not _supervisor.is_alive():
            _supervisor = threading.Thread(target=_watchdog, name="trendcrew-watchdog", daemon=True)
            _supervisor.start()
        return _engine


def _watchdog() -> None:
    """If the engine thread ever dies, bring it back (positions must never be left without stop control)."""
    while True:
        time.sleep(15)
        eng = _engine
        if eng is not None and not eng.is_alive():
            log.error("Engine-Thread war beendet – wird neu gestartet")
            eng.journal.add("system", "Engine-Thread wurde neu gestartet", "warn", "alert")
            try:
                eng.start_thread()
            except Exception:                                        # noqa: BLE001
                log.exception("Neustart des Engine-Threads fehlgeschlagen")
