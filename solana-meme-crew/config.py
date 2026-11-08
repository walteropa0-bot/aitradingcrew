"""
=============================================================================
  SOL·CREW  –  Autonomes Solana-Meme-Coin-Handelssystem
  KONFIGURATION  (diese Datei darfst du bearbeiten)
=============================================================================

  WICHTIG – bitte zuerst lesen
  ----------------------------
  * PAPER_TRADING = True   ->  DEMO-MODUS. Spielgeld, es wird NICHTS gekauft.
  * PAPER_TRADING = False  ->  LIVE-MODUS. Es wird mit ECHTEM GELD aus deiner
                               Wallet gehandelt (Jupiter-DEX auf Solana).

  Starte IMMER mit True und lass das System mehrere Tage im Demo-Modus laufen.
  Meme-Coins sind extrem riskant: Du kannst Geld verlieren – auch alles.
  Das ist keine Finanzberatung und es gibt keine Gewinn-Garantie.

  Nach jeder Änderung an dieser Datei: Programm neu starten
  (Replit: "Stop" und wieder "Run" bzw. erneut "Republish").
"""

# ╔═══════════════════════════════════════════════════════════════════════╗
# ║   DER SCHALTER                                                          ║
# ╚═══════════════════════════════════════════════════════════════════════╝
PAPER_TRADING = True      # True = Demo mit Spielgeld  |  False = ECHTES GELD

import os


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None else value.strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "ja", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


# ─────────────────────────────────────────────────────────────────────────
#  1) ZUGANGSDATEN  –  kommen NIE hier hinein, sondern als "Secrets"
#     (Umgebungsvariablen) in Replit. Siehe ANLEITUNG_REPLIT.md
# ─────────────────────────────────────────────────────────────────────────
SOLANA_PRIVATE_KEY = _env("SOLANA_PRIVATE_KEY")      # nur für LIVE nötig
SOLANA_RPC_URL = _env("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
DASHBOARD_PASSWORD = _env("DASHBOARD_PASSWORD")      # schützt dein Dashboard
JUPITER_API_KEY = _env("JUPITER_API_KEY")            # optional, kostenlos (portal)
DATABASE_URL = _env("DATABASE_URL")                  # optional: Postgres (Replit-DB)
SESSION_SECRET = _env("SESSION_SECRET")              # optional (sonst abgeleitet)

# ─────────────────────────────────────────────────────────────────────────
#  2) BETRIEB
# ─────────────────────────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = _env_int("PORT", 8000)
DATA_DIR = _env("DATA_DIR", "data")                  # lokaler Speicherort (Dateien)
LOG_LEVEL = _env("LOG_LEVEL", "INFO")

# Künstlicher Test-Markt (nur mit PAPER_TRADING = True erlaubt). Praktisch,
# um das Dashboard ohne Internet/Marktdaten zu sehen. Echtes Geld: immer False.
USE_SIMULATED_MARKET = _env_bool("USE_SIMULATED_MARKET", False)

# Nach einem Neustart automatisch weitermachen, wenn das System vorher lief.
AUTO_RESUME = True

# ─────────────────────────────────────────────────────────────────────────
#  3) KAPITAL
# ─────────────────────────────────────────────────────────────────────────
PAPER_START_BALANCE_SOL = 10.0     # Spielgeld im Demo-Modus

# LIVE: Mit höchstens so viel SOL (inkl. Gewinne) arbeitet der Bot – egal wie
# viel in der Wallet liegt. Starte klein (z. B. 0.5 – 1 SOL)!
LIVE_TRADING_CAPITAL_SOL = 1.0
MIN_SOL_RESERVE = 0.05             # bleibt immer für Gebühren in der Wallet
LIVE_MIN_SOL_TO_START = 0.15       # darunter startet das Live-System nicht

# Gewinne absichern: "SOL" = alles bleibt in SOL | "USDC" = realisierter Gewinn
# wird nach jedem profitablen Trade in USDC getauscht (in DEINER Wallet).
PROFIT_CURRENCY = "SOL"
MIN_PROFIT_SWEEP_SOL = 0.05

# Fremde Token in der Wallet (nicht vom Bot gekauft) werden NICHT angefasst.
# True = Bot übernimmt sie nach einem Neustart zur Verwaltung (Trailing-Stop).
ADOPT_UNKNOWN_TOKENS = False

# ─────────────────────────────────────────────────────────────────────────
#  4) AGENT 1 – SCOUT  (Pools finden + Scam-/Rugpull-Filter)
# ─────────────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SEC = 30             # wie oft nach neuen Tokens gesucht wird
WATCH_REFRESH_SEC = 12             # wie oft die Watchlist aktualisiert wird
POSITION_TICK_SEC = 4              # wie oft offene Positionen (Stops) geprüft werden
MAX_VET_PER_CYCLE = 8              # so viele neue Tokens pro Durchlauf prüfen
WATCHLIST_MAX = 20
WATCH_TTL_MIN = 90                 # so lange wird ein Token beobachtet

ALLOWED_QUOTES = ("SOL", "USDC", "USDT")
MIN_LIQUIDITY_USD = 15_000         # Mindest-Liquidität des Pools
MIN_PAIR_AGE_MIN = 2               # nicht in den allerersten Sekunden kaufen
MAX_PAIR_AGE_HOURS = 24            # nur "frische" Pools
MIN_FDV_USD = 20_000
MAX_FDV_USD = 30_000_000
MIN_TXNS_M5 = 15                   # Mindestaktivität (Transaktionen in 5 Min)

# Contract-Checks
REQUIRE_MINT_AUTH_DISABLED = True      # Mint-Authority muss deaktiviert sein
REQUIRE_FREEZE_AUTH_DISABLED = True    # Freeze-Authority muss deaktiviert sein
MIN_LP_LOCKED_PCT = 80                 # Liquidität gesperrt/verbrannt (in %)
MAX_RUGCHECK_SCORE = 40                # RugCheck-Risiko 0 (sicher) – 100 (Scam)
RUGCHECK_BLOCK_LEVELS = ("danger",)    # diese Warnstufen blockieren den Token
RUGCHECK_IGNORE_RISKS = ("Mutable metadata",)
# Token-2022-Erweiterungen, die einen Verkauf verhindern/verteuern können:
BLOCKED_TOKEN2022_EXTENSIONS = (
    "transferFeeConfig", "permanentDelegate", "transferHook", "nonTransferable",
    "defaultAccountState", "confidentialTransferMint", "pausable",
)

# ─────────────────────────────────────────────────────────────────────────
#  5) AGENT 2 + 3 – SENTIMENT & TECH  (Mindestwerte 0-100)
# ─────────────────────────────────────────────────────────────────────────
SENTIMENT_MIN_SCORE = 55
TECH_MIN_SCORE = 60
ENTRY_SCORE_MIN = 65               # gewichteter Gesamtscore des Komitees
WEIGHT_SAFETY = 0.25
WEIGHT_SENTIMENT = 0.30
WEIGHT_TECH = 0.45
MIN_VOLUME_SPIKE = 1.6             # Volumen (5 Min) vs. Durchschnitt
MIN_BUY_PRESSURE = 0.54            # Anteil Käufe an allen Trades (5 Min)
MAX_CHASE_M5_PCT = 70              # schon +70 % in 5 Min? Nicht hinterherlaufen
MAX_PULLBACK_FROM_PEAK_PCT = 15    # fallendes Messer: Kauf nur nahe am Hoch
MAX_ENTRY_DRIFT_PCT = 8.0          # Echtzeitpreis vs. Signalpreis (Datenverzug)

# ─────────────────────────────────────────────────────────────────────────
#  6) AGENT 4 – RISIKOMANAGER
# ─────────────────────────────────────────────────────────────────────────
RISK_PER_TRADE_PCT = 1.5           # % vom Kapital, die beim Stop-Loss weg sein dürfen
MAX_POSITION_SOL = 0.5             # harte Obergrenze pro Trade (in SOL)
MAX_POSITION_PCT = 10.0            # max. % vom Kapital pro Trade
MAX_OPEN_POSITIONS = 4
MAX_EXPOSURE_PCT = 50.0            # max. % vom Kapital gleichzeitig investiert
MAX_POOL_SHARE_PCT = 1.5           # nie mehr als x % der Pool-Liquidität bewegen
MIN_TRADE_SOL = 0.02

DAILY_LOSS_LIMIT_PCT = 10.0        # Tagesverlust-Bremse (UTC-Tag)
KILL_SWITCH_DRAWDOWN_PCT = 35.0    # Gesamt-Drawdown vom Hoch -> Jagd stoppt
MAX_CONSECUTIVE_LOSSES = 4
PAUSE_AFTER_LOSSES_MIN = 30
REENTRY_COOLDOWN_MIN = 180         # gleicher Token nach Verlust: Sperrzeit

# Stop-Loss / Trailing-Stop  (alle Werte in Prozent)
INITIAL_STOP_LOSS_PCT = 20.0
# (Spitzengewinn ab …%, Abstand des Trailing-Stops vom Hoch in %)
TRAILING_TIERS = ((0, 20.0), (40, 16.0), (100, 12.0), (300, 8.0))
# (Gewinn …%, verkaufe …% der Restposition) – "Einsatz rausnehmen"
PARTIAL_TAKE_PROFIT = ((100, 50),)
STALE_AFTER_MIN = 90               # Zeit-Stop: nach x Min ohne Bewegung raus
STALE_MAX_GAIN_PCT = 5.0
RUG_LIQUIDITY_DROP_PCT = 35.0      # Liquidität bricht ein -> sofort raus
CRASH_TICK_DROP_PCT = 30.0         # Kurs -30 % zwischen zwei Messungen -> raus

# Ausführung
BUY_SLIPPAGE_BPS = 500             # 100 bps = 1 %
SELL_SLIPPAGE_BPS = 1200
PANIC_SLIPPAGE_BPS = 2500
MAX_PRICE_IMPACT_PCT = 4.0         # Kauf wird abgelehnt, wenn Impact höher
PRIORITY_FEE_LAMPORTS = 0          # 0 = Jupiter wählt automatisch
EXIT_RETRY_AFTER_SEC = 6

# Demo-Modus: bewusst pessimistisch, damit Ergebnisse nicht zu schön sind
PAPER_EXTRA_SLIPPAGE_PCT = 1.0
PAPER_SWAP_FEE_PCT = 0.10          # entspricht Jupiter-Gebühr (10 bps)
PAPER_NETWORK_FEE_SOL = 0.0005

# ─────────────────────────────────────────────────────────────────────────
#  7) OBERFLÄCHE
# ─────────────────────────────────────────────────────────────────────────
APP_NAME = "SOL·CREW"
# Kosmetische Pause (Sek.) zwischen Analyse-Schritten, damit man den Agenten
# zusehen kann. Im Live-Modus sinnvoll klein halten.
UI_STEP_DELAY_SEC = 0.35 if PAPER_TRADING else 0.05
EQUITY_SAMPLE_SEC = 5
PERSIST_INTERVAL_SEC = 20

# ─────────────────────────────────────────────────────────────────────────
#  8) KONSTANTEN  (nicht ändern)
# ─────────────────────────────────────────────────────────────────────────
WSOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
JUPITER_BASE_URL = _env("JUPITER_BASE_URL", "https://api.jup.ag")
SOLSCAN_BASE = "https://solscan.io"
VERSION = "1.0.0"
