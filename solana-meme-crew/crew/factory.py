"""Assembles the whole system from config.py + environment variables."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import config

from .chain.wallet import Wallet, WalletError
from .engine import Engine
from .events import EventBus
from .logging_setup import register_secret
from .market.provider import RealMarket
from .market.simulator import SimMarket
from .trading.executor import LiveExecutor, PaperExecutor
from .trading.portfolio import Portfolio
from .trading.store import make_store
from .util import now

log = logging.getLogger("crew.factory")


class ConfigError(Exception):
    """Fatal, human-readable configuration problem."""


@dataclass
class Built:
    engine: Engine
    market: object
    problems: list[str]


def validate_environment() -> list[str]:
    """Non-fatal warnings shown on the dashboard (fatal ones raise ConfigError)."""
    problems: list[str] = []
    if config.PAPER_TRADING:
        if not config.DASHBOARD_PASSWORD:
            problems.append("Kein DASHBOARD_PASSWORD gesetzt – jeder mit dem Link kann das Dashboard sehen und bedienen!")
    else:
        if config.USE_SIMULATED_MARKET:
            raise ConfigError("Der Demo-Markt ist im Live-Modus verboten (PAPER_TRADING = False). "
                              "Setze USE_SIMULATED_MARKET auf False.")
        if not config.DASHBOARD_PASSWORD:
            problems.append("LIVE-Modus ohne DASHBOARD_PASSWORD – der Zugriff ist gesperrt, bis ein Passwort gesetzt ist.")
        if not config.SOLANA_PRIVATE_KEY:
            problems.append("LIVE-Modus: SOLANA_PRIVATE_KEY fehlt (Replit → Secrets).")
        if config.SOLANA_RPC_URL.startswith("https://api.mainnet-beta.solana.com"):
            problems.append("Öffentlicher Solana-RPC: für Live-Handel besser einen eigenen (kostenlosen) Helius-Key nutzen.")
    return problems


def build(bus: Optional[EventBus] = None) -> Built:
    problems = validate_environment()
    for secret in (config.SOLANA_PRIVATE_KEY, config.DASHBOARD_PASSWORD, config.SESSION_SECRET,
                   config.JUPITER_API_KEY, config.SOLANA_RPC_URL, config.DATABASE_URL):
        register_secret(secret)

    sim = config.PAPER_TRADING and config.USE_SIMULATED_MARKET
    market = SimMarket() if sim else RealMarket()
    mode = "paper" if config.PAPER_TRADING else "live"
    store = make_store(mode + ("_demo" if sim else ""))

    if config.PAPER_TRADING:
        executor = PaperExecutor(market, jupiter=None if sim else market.jupiter)      # type: ignore[attr-defined]
        start = config.PAPER_START_BALANCE_SOL
    else:
        try:
            wallet = Wallet.from_secret(config.SOLANA_PRIVATE_KEY)
        except WalletError as exc:
            # keep the dashboard alive so the user can see WHY live trading is blocked
            problems.append(f"Live-Wallet nicht ladbar: {exc}")
            wallet = None
        if wallet is None:
            executor = _BlockedExecutor(str(problems[-1]))
        else:
            executor = LiveExecutor(wallet, market.rpc, market.jupiter)               # type: ignore[attr-defined]
        start = 0.0

    saved = None
    try:
        saved = store.load()
    except Exception as exc:
        log.error("State konnte nicht geladen werden: %s", exc)
    if saved and saved.get("mode") == mode:
        pf = Portfolio.from_dict(saved)
        log.info("State geladen: %d offene Position(en), %d Trades", len(pf.open_positions()), len(pf.trades))
    else:
        pf = Portfolio(mode, start, now())
    engine = Engine(market=market, executor=executor, store=store, portfolio=pf, bus=bus)
    if isinstance(executor, _BlockedExecutor):
        engine.ready, engine.ready_error = False, executor.reason
    return Built(engine=engine, market=market, problems=problems)


class _BlockedExecutor(PaperExecutor):
    """Placeholder when the live wallet cannot be loaded: refuses everything."""
    simulated = False
    name = "blocked"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.market = None
        self.jupiter = None

    async def prepare(self):
        from .trading.executor import ExecutorError
        raise ExecutorError(self.reason)

    async def cash_sol(self):
        return 0.0

    async def buy(self, req):          # pragma: no cover - never reachable (engine not ready)
        from .models import Fill
        return Fill(ok=False, error=self.reason)

    async def sell(self, req):         # pragma: no cover
        from .models import Fill
        return Fill(ok=False, error=self.reason)
