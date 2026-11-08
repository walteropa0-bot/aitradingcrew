# =============================================================================
#  TREND·CREW  –  alle Einstellungen an einem Ort
# =============================================================================
#  ▼▼▼  DER WICHTIGSTE SCHALTER  ▼▼▼
PAPER_TRADING = True    # True  = DEMO mit Spielgeld (sicher, Standard)
                        # False = LIVE mit ECHTEM GELD über deine Börsen-API-Keys
#  ▲▲▲  Nur auf False stellen, wenn du die Anleitung (Schritt 9) gelesen hast!  ▲▲▲
# =============================================================================
"""Alles Wichtige steht hier.  Die API-Schlüssel stehen NICHT hier, sondern werden
als Umgebungsvariablen ("Secrets" in Replit) hinterlegt – siehe ANLEITUNG_REPLIT.md.
Jede Einstellung in diesem Block kann auch per Umgebungsvariable überschrieben werden."""
import os
import sys


# ───────────────────────── kleine Hilfsfunktionen ─────────────────────────
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_float(name: str, default: float, lo: float = float("-inf"), hi: float = float("inf")) -> float:
    raw = _env(name)
    if not raw:
        return default
    try:
        val = float(raw.replace(",", "."))
    except ValueError:
        print(f"[config] {name}={raw!r} ist keine Zahl – nehme {default}", file=sys.stderr)
        return default
    return min(max(val, lo), hi)


def _env_int(name: str, default: int, lo: int = -10**9, hi: int = 10**9) -> int:
    return int(_env_float(name, float(default), float(lo), float(hi)))


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "ja", "on")


APP_NAME = "TREND·CREW"
VERSION = "1.0.0"

# ─────────────────────────────── 1) BÖRSE ────────────────────────────────
# "kraken" ist voreingestellt: funktioniert von Replit/Render (USA-Server) und in Deutschland.
# "binance" (binance.com) blockiert Server in den USA (Fehler 451) – nur wählen, wenn dein Server woanders steht.
EXCHANGE = _env("EXCHANGE", "kraken").lower()
QUOTE_CURRENCY = _env("QUOTE_CURRENCY", "EUR").upper()          # EUR, USD, USDT, USDC ...
ASSETS = tuple(a.strip().upper() for a in _env("ASSETS", "BTC,ETH,SOL").split(",") if a.strip())
SYMBOLS = tuple(f"{a}/{QUOTE_CURRENCY}" for a in ASSETS)

# Zugangsdaten – kommen NUR aus Umgebungsvariablen (Replit: Secrets). Niemals hier eintragen!
API_KEY = _env("EXCHANGE_API_KEY")
API_SECRET = _env("EXCHANGE_API_SECRET")
API_PASSWORD = _env("EXCHANGE_API_PASSWORD")      # nur für Börsen mit Passphrase (z. B. OKX); Kraken/Binance: leer
EXCHANGE_SANDBOX = _env_bool("EXCHANGE_SANDBOX", False)   # Testnetz der Börse (falls vorhanden, z. B. Binance)

# ─────────────────────────── 2) KAPITAL & MODUS ───────────────────────────
PAPER_START_CAPITAL = _env_float("PAPER_START_CAPITAL", 10_000.0, 10.0, 1e9)   # Spielgeld in QUOTE_CURRENCY
# LIVE: Höchstbetrag (in QUOTE_CURRENCY), den der Bot einsetzen darf. 0 = Live-Handel gesperrt.
# Der Bot fasst nie mehr an als diesen Betrag – auch wenn auf dem Konto mehr liegt.
LIVE_CAPITAL = _env_float("LIVE_CAPITAL", 0.0, 0.0, 1e9)

# ───────────────────── 3) TAKT (wie oft der Bot arbeitet) ─────────────────────
TIMEFRAME = _env("TIMEFRAME", "4h")        # Kerzenlänge für die Signale: 1h | 4h | 1d  (4h = im Backtest am besten)
HTF_TIMEFRAME = "1d"                       # Tageskerzen liefern den großen Trend (Trend-Scout)
CANDLE_HISTORY = 400                       # so viele Kerzen lädt der Bot für die Indikatoren
HTF_HISTORY = 320                          # so viele Tageskerzen (EMA200 braucht ~250)
TICK_SEC = _env_float("TICK_SEC", 5.0, 2.0, 120.0)            # Kurs-Abfrage + Stop-Kontrolle
CANDLE_REFRESH_SEC = _env_float("CANDLE_REFRESH_SEC", 30.0, 10.0, 600.0)
HTF_REFRESH_SEC = 600.0

# ───────────────────── 4) TECH-ANALYST (RSI + MACD + Trend) ─────────────────────
# "breakout" (Standard): Kauf, wenn der Kurs im Tages-Aufwärtstrend auf ein neues 50-Kerzen-Hoch ausbricht,
#                        der MACD positiv ist und der RSI stark, aber nicht überhitzt ist.
# "dip":                 Kauf, wenn der RSI aus dem überverkauften Bereich nach oben dreht und der MACD mitzieht.
#                        (Im Backtest verlor diese Variante nach Gebühren – siehe README.)
STRATEGY_MODE = _env("STRATEGY_MODE", "breakout").lower()
BREAKOUT_BARS = 50
BREAKOUT_RSI_MIN, BREAKOUT_RSI_MAX = 50.0, 80.0
REQUIRE_MACD_POSITIVE = True
RSI_PERIOD = 14
RSI_BUY_ZONE = 45.0        # dip-Modus: RSI war zuletzt unter diesem Wert ("überverkauft")
RSI_LOOKBACK = 10          # ... innerhalb der letzten N Kerzen
RSI_MAX_ENTRY = 60.0       # dip-Modus: nicht kaufen, wenn der RSI schon darüber liegt
RSI_OVERBOUGHT = 80.0      # "überhitzt"
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
EMA_FAST, EMA_SLOW = 50, 200            # Trendfilter auf Tageskerzen: Kurs > EMA200 und EMA50 > EMA200
USE_TREND_FILTER = True
EXIT_ON_OVERBOUGHT = False              # optional: bei Überhitzung + MACD-Abwärtsdreh verkaufen (im Backtest schlechter)
EXIT_ON_TREND_BREAK = False             # optional: bei Abwärtstrend verkaufen (im Backtest neutral)

# ───────────────────── 5) RISIKOMANAGER ─────────────────────
RISK_PER_TRADE_PCT = _env_float("RISK_PER_TRADE_PCT", 1.0, 0.1, 5.0)       # max. Verlust je Trade (% vom Gesamtkapital)
ATR_PERIOD = 14
STOP_ATR_MULT = _env_float("STOP_ATR_MULT", 5.0, 1.0, 12.0)                # Abstand des Trailing-Stops in ATR (Schwankungsbreite)
MAX_POSITION_PCT = _env_float("MAX_POSITION_PCT", 30.0, 5.0, 100.0)        # max. Anteil des Kapitals in EINEM Coin
MAX_TOTAL_EXPOSURE_PCT = _env_float("MAX_TOTAL_EXPOSURE_PCT", 80.0, 10.0, 100.0)   # max. insgesamt investiert
MAX_OPEN_POSITIONS = _env_int("MAX_OPEN_POSITIONS", 3, 1, 10)
COOLDOWN_BARS = 6                        # nach einem Verlust-Stopp so viele Kerzen Pause für diesen Coin
DAILY_LOSS_LIMIT_PCT = _env_float("DAILY_LOSS_LIMIT_PCT", 3.0, 0.5, 50.0)  # Tagesverlust-Bremse (keine neuen Käufe)
MAX_DRAWDOWN_KILL_PCT = _env_float("MAX_DRAWDOWN_KILL_PCT", 15.0, 2.0, 90.0)  # Notbremse: Rückgang vom Höchststand
MAX_SPREAD_PCT = 0.5                     # kein Kauf, wenn Kauf-/Verkaufskurs mehr als 0,5 % auseinanderliegen
MAX_DATA_AGE_SEC = 180                   # ältere Kursdaten = keine neuen Käufe

# ───────────────────── 6) GEBÜHREN & SIMULATION (Demo-Modus) ─────────────────────
FEE_RATE = _env_float("FEE_RATE", 0.0026, 0.0, 0.02)       # Kraken "Taker" 0,26 % je Order (Marktorder)
SLIPPAGE_PCT = _env_float("SLIPPAGE_PCT", 0.05, 0.0, 2.0)   # Demo: Preisabweichung beim Ausführen in %

# ───────────────────── 7) DASHBOARD, SPEICHER, SERVER ─────────────────────
DASHBOARD_PASSWORD = _env("DASHBOARD_PASSWORD")             # Pflicht im LIVE-Modus
HOST = _env("HOST", "0.0.0.0")
PORT = _env_int("PORT", 5000, 1, 65535)
DATA_DIR = _env("DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
DATABASE_URL = _env("DATABASE_URL")                          # optional: Postgres für dauerhaften Speicher (Replit)
LOG_LEVEL = _env("LOG_LEVEL", "INFO")
AUTOSTART = _env_bool("AUTOSTART", True)                     # nach Neustart automatisch weiterlaufen, wenn der Bot lief


def is_live() -> bool:
    return not PAPER_TRADING


def mode_name() -> str:
    return "paper" if PAPER_TRADING else "live"
