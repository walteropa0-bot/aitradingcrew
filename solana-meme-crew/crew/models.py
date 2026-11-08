"""Plain data containers. Everything that is persisted can round-trip via
``to_dict`` / ``from_dict`` (unknown keys are ignored -> forward compatible)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any, Optional, TypeVar

T = TypeVar("T")


def from_dict(cls: type[T], data: dict[str, Any]) -> T:
    names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    return cls(**{k: v for k, v in (data or {}).items() if k in names})  # type: ignore[call-arg]


# ───────────────────────────── market data ──────────────────────────────
@dataclass
class Pair:
    """Snapshot of the best trading pool of one token."""
    mint: str
    symbol: str = ""
    name: str = ""
    pair_address: str = ""
    dex: str = ""
    quote_symbol: str = ""
    quote_mint: str = ""
    price_native: float = 0.0     # price in units of the quote token
    price_usd: float = 0.0
    price_sol: float = 0.0        # price in SOL, 0 = unknown
    liquidity_usd: float = 0.0
    fdv: float = 0.0
    market_cap: float = 0.0
    vol_m5: float = 0.0
    vol_h1: float = 0.0
    vol_h6: float = 0.0
    vol_h24: float = 0.0
    buys_m5: int = 0
    sells_m5: int = 0
    buys_h1: int = 0
    sells_h1: int = 0
    buys_h6: int = 0
    sells_h6: int = 0
    buys_h24: int = 0
    sells_h24: int = 0
    chg_m5: float = 0.0           # percent
    chg_h1: float = 0.0
    chg_h6: float = 0.0
    chg_h24: float = 0.0
    created_ms: int = 0
    image_url: str = ""
    websites: list[str] = field(default_factory=list)
    socials: list[str] = field(default_factory=list)   # types: twitter, telegram, ...
    boosts: int = 0
    url: str = ""
    ts: float = 0.0               # observation time (seconds)
    # optional extras from GeckoTerminal (unique wallets)
    buyers_m5: int = 0
    sellers_m5: int = 0
    buyers_h1: int = 0
    sellers_h1: int = 0

    def age_min(self, at: Optional[float] = None) -> float:
        t = self.ts if at is None else at
        if not self.created_ms or not t:
            return 1e9
        return max(0.0, (t * 1000.0 - self.created_ms) / 60000.0)

    @property
    def txns_m5(self) -> int:
        return self.buys_m5 + self.sells_m5

    @property
    def buy_ratio_m5(self) -> float:
        n = self.txns_m5
        return self.buys_m5 / n if n else 0.5

    @property
    def buy_ratio_h1(self) -> float:
        n = self.buys_h1 + self.sells_h1
        return self.buys_h1 / n if n else 0.5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ContractReport:
    """Result of the Scout's contract / rug-pull checks."""
    mint: str
    ok: bool = False
    blocked_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mint_authority_disabled: Optional[bool] = None
    freeze_authority_disabled: Optional[bool] = None
    token_program: str = ""
    extensions: list[str] = field(default_factory=list)
    decimals: Optional[int] = None
    lp_locked_pct: Optional[float] = None
    rug_score: Optional[float] = None          # 0 safe .. 100 scam (RugCheck, normalised)
    risks: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    safety_score: int = 0                       # 0..100, higher = safer
    checked_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Candidate:
    mint: str
    source: str
    hint: float = 0.0     # discovery strength (e.g. boost amount)


# ───────────────────────────── agent reports ─────────────────────────────
@dataclass
class SentimentReport:
    mint: str
    score: float = 0.0
    verdict: str = "neutral"          # bullish | neutral | bearish
    summary: str = ""
    warnings: list[str] = field(default_factory=list)
    parts: dict[str, float] = field(default_factory=dict)


@dataclass
class TechReport:
    mint: str
    score: float = 0.0
    signal: str = "WAIT"              # BUY | WAIT | AVOID
    summary: str = ""
    vol_spike: float = 0.0
    buy_pressure: float = 0.5
    momentum_pct: float = 0.0
    ema_gap_pct: float = 0.0
    pullback_pct: float = 0.0
    samples: int = 0
    reasons: list[str] = field(default_factory=list)


@dataclass
class RiskDecision:
    approved: bool
    reason: str = ""
    sol_amount: float = 0.0
    slippage_bps: int = 0
    stop_price: float = 0.0
    est_impact_pct: float = 0.0
    stop_loss_pct: float = 0.0


@dataclass
class ExitOrder:
    position_id: str
    fraction: float        # 0..1 of the *remaining* quantity
    reason: str
    panic: bool = False
    slippage_bps: int = 0
    kind: str = "exit"     # exit | take_profit
    stage: int = -1        # index into PARTIAL_TAKE_PROFIT for take_profit orders


# ───────────────────────────── trading ledger ────────────────────────────
@dataclass
class Fill:
    """Outcome of one executed (or simulated) swap."""
    ok: bool
    side: str = "BUY"                 # BUY | SELL
    mint: str = ""
    symbol: str = ""
    sol_amount: float = 0.0           # SOL spent (BUY) / received (SELL); excludes network fees
    token_amount: float = 0.0         # UI units
    price_sol: float = 0.0
    fee_sol: float = 0.0              # network + platform fees (estimate / actual)
    slippage_pct: float = 0.0
    signature: str = ""
    simulated: bool = True
    source: str = ""                  # jupiter | jupiter-quote | model
    error: str = ""
    ts: float = 0.0


@dataclass
class Position:
    id: str
    mint: str
    symbol: str = ""
    name: str = ""
    decimals: int = 6
    pair_address: str = ""
    image_url: str = ""
    opened_at: float = 0.0
    entry_price_sol: float = 0.0
    qty: float = 0.0                  # UI units still held
    cost_sol: float = 0.0             # cost basis of the remaining qty
    initial_cost_sol: float = 0.0
    peak_price_sol: float = 0.0
    stop_price_sol: float = 0.0
    last_price_sol: float = 0.0
    entry_liquidity_usd: float = 0.0
    peak_liquidity_usd: float = 0.0
    last_liquidity_usd: float = 0.0
    tp_done: list[int] = field(default_factory=list)
    realized_sol: float = 0.0         # net proceeds of partial sells
    fees_sol: float = 0.0
    entry_signature: str = ""
    status: str = "open"              # open | closed
    recovered: bool = False
    simulated: bool = True
    exit_attempts: int = 0
    last_exit_try: float = 0.0
    exiting: bool = False             # runtime only
    entry_scores: dict[str, float] = field(default_factory=dict)
    closed_at: float = 0.0
    close_reason: str = ""

    def value_sol(self) -> float:
        return self.qty * self.last_price_sol

    def gain_pct(self) -> float:
        if self.entry_price_sol <= 0:
            return 0.0
        return (self.last_price_sol / self.entry_price_sol - 1.0) * 100.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("exiting", None)
        return d


@dataclass
class TradeRecord:
    id: str
    ts: float
    side: str                         # BUY | SELL
    mint: str
    symbol: str
    price_sol: float
    qty: float
    sol_amount: float
    fee_sol: float = 0.0
    pnl_sol: float = 0.0              # realised (SELL only)
    pnl_pct: float = 0.0
    reason: str = ""
    signature: str = ""
    simulated: bool = True
    position_id: str = ""
    image_url: str = ""
    entry_price_sol: float = 0.0
