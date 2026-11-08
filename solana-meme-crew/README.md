# SOL·CREW – autonomes Solana-Meme-Coin-Handelssystem

Ein eigenständiges Python-Projekt (FastAPI + Vanilla-JS-Dashboard), das neue Solana-Meme-Coins scannt, auf Scams prüft,
über ein Komitee aus vier spezialisierten Agenten entscheidet und über die Jupiter-DEX handelt – wahlweise im **Demo-Modus
(Spielgeld)** oder **live mit echtem Geld**. Optimiert für 24/7-Hosting auf Replit (läuft aber überall, wo Python läuft).

<p align="center">
  <img src="docs/screenshots/1-uebersicht.png" width="230" alt="Übersicht">
  <img src="docs/screenshots/2-steuerung-crew.png" width="230" alt="Kontrollzentrum und Live-Crew">
  <img src="docs/screenshots/3-positionen-feed.png" width="230" alt="Positionen und Transaktions-Feed">
</p>

> ⚠️ **Risiko-Hinweis:** Meme-Coins sind hochspekulativ; Totalverlust ist möglich. Keine Finanzberatung, keine Gewinn-Garantie.
> Der Standardmodus ist **`PAPER_TRADING = True`** (Spielgeld). **→ Einsteiger-Anleitung: [`ANLEITUNG_REPLIT.md`](ANLEITUNG_REPLIT.md)**

## Schnellstart (lokal)

```bash
pip install -r requirements.txt
python main.py                 # Dashboard auf http://localhost:8000
python selftest.py             # prüft alle externen Dienste (nur lesend)
USE_SIMULATED_MARKET=1 python main.py   # künstlicher Demo-Markt, funktioniert ohne Internet
pip install -r requirements-dev.txt && pytest      # 140 Tests
```

Der Schalter steht **ganz oben in [`config.py`](config.py)**: `PAPER_TRADING = True` (Demo) bzw. `False` (live).

## Die Agenten-Crew

| # | Agent | Aufgabe | Arbeitet mit |
|---|---|---|---|
| 1 | **Scout** | Findet neue Pools (Dexscreener Profile/Boosts, GeckoTerminal New/Trending, RugCheck-Feed), filtert nach Liquidität/Alter/Aktivität und prüft den **Contract**: Mint-Authority aus? Freeze-Authority aus? Gefährliche Token-2022-Erweiterungen? LP gesperrt? RugCheck-Risiko-Flags? | Solana-RPC (on-chain) + RugCheck. **Fail-closed**: Ist eine Quelle nicht erreichbar, wird nicht gehandelt. |
| 2 | **Sentiment-Analyst** | *Simulierter* Hype-Filter: Volumen-Spike, Kaufdominanz, Zahl eindeutiger Käufer-Wallets, Website/Socials, bezahlte Promo; erkennt Bot-/Wash-Trading. Kein Social-Media-Scraping. | Marktdaten + Metadaten |
| 3 | **Tech-Analyst** | Rechnet Momentum (ROC, Beschleunigung), zeitkonstante EMAs, Volumen-Beschleunigung, Käuferdruck und Rücksetzer vom 5-Min-Hoch auf einer selbst aufgezeichneten Kurs-Serie. Kauf nur bei bestätigtem Ausbruch – **kein Hinterherlaufen** (>+70 %/5 Min) und **kein fallendes Messer**. | eigene Zeitreihe + Börsenstatistik |
| 4 | **Risikomanager** | Exakte Positionsgröße (Risiko pro Trade, SOL-Obergrenze, % vom Kapital, Pool-Anteil, Gesamt-Exposure, Price-Impact-Limit), Slippage-Limits, **hartes Trailing-Stop** (nur aufwärts, wird mit Gewinn enger), Teilgewinn bei +100 %, Zeit-Stop, Rug-/Crash-Alarm, Tagesverlust-Bremse, Verlustserien-Pause, Drawdown-Kill-Switch. | Portfolio + Preis-Feed |

Das **Komitee** (Scout-Freigabe + Sentiment-Score + Tech-Signal + Risiko-Freigabe, gewichteter Gesamtscore ≥ 65) entscheidet über jeden Kauf;
das Dashboard zeigt jede Abstimmung live.

## Architektur

```
 Dexscreener ─┐                    ┌─ Scout ──► Sentiment ──► Tech ──► Risiko ─┐   Komitee
 GeckoTerminal ├─► RealMarket ─────┤                                          ├──► Engine ──► Executor ──► Jupiter Swap V2 ─► Solana
 RugCheck ────┤   (Fassade)        └─ Positions-Loop: Echtzeit-Marks, Stops ──┘     │ (Paper | Live)        (order → sign → execute)
 Solana RPC ──┤   SimMarket (Demo)                                                  ▼
 Jupiter Price┘                                     Portfolio/Ledger ◄── Persistenz (Datei | Postgres)    FastAPI ◄── Dashboard (Safari)
```

* **Engine** (`crew/engine.py`): vier überwachte Loops – *scan* (30 s), *watch* (12 s), *positions* (4 s, **immer aktiv**), *housekeeping* (5 s). Ein Fehler in einem Durchlauf stoppt keinen anderen.
* **„Jagd“ ≠ „Schutz“:** *Pausieren* stoppt nur neue Käufe; offene Positionen bleiben durch Trailing-Stops geschützt. **NOT-AUS** verkauft alles (mit hoher Slippage-Toleranz) und stoppt das System.
* **Neustart-sicher:** Ledger + offene Positionen werden atomar gespeichert (Datei mit Backup oder Postgres via `DATABASE_URL`); im Live-Modus wird beim Start gegen die Wallet abgeglichen (*die Chain ist die Wahrheit*).
* **Echte Datenlatenz ist eingeplant:** Dexscreener/GeckoTerminal liefern per CDN-Cache nur alle ~30–60 s neue Werte (gemessen). Darum nutzt der Bot für offene Positionen **Jupiters Price-API (blocknah)** und prüft vor jedem Einstieg den **Echtzeitpreis gegen das Signal** (`MAX_ENTRY_DRIFT_PCT`).

## Handels-Modi

| | `PAPER_TRADING = True` (Standard) | `PAPER_TRADING = False` |
|---|---|---|
| Geld | Spielgeld (`PAPER_START_BALANCE_SOL`) | echte SOL aus deiner Wallet |
| Marktdaten | echt (oder `USE_SIMULATED_MARKET=1`) | echt (Demo-Markt **verboten**) |
| Ausführung | simuliert, **bewusst pessimistisch**: echte Jupiter-Quotes (inkl. 10 bps Gebühr + Impact) + 1 % Extra-Slippage + Netzwerkgebühr | echte Swaps: Jupiter `order` → lokal signieren → `execute` |
| Pflicht | – | `SOLANA_PRIVATE_KEY`, `DASHBOARD_PASSWORD` (sonst **komplett gesperrt**) |
| Kapital | gesamtes Spielgeld | höchstens `LIVE_TRADING_CAPITAL_SOL` (+ realisierte Gewinne) |

## Sicherheitsdesign

* **Keine Fremd-Überweisungen möglich:** Der Code enthält *keine* Transfer-Instruktionen. Der Bot signiert ausschließlich Swap-Transaktionen der Jupiter-API – und **simuliert jede vor dem Senden** auf dem RPC: zieht sie mehr SOL ab als Einsatz + Gebühren (+0,015 SOL Toleranz), wird sie verworfen. (Test: `test_guard_stops_a_transaction_that_would_drain_the_wallet`, `test_code_has_no_fund_transfer_primitives`.)
* **Kein doppelter Kauf:** Ein unklares `execute`-Ergebnis wird über die Signatur auf der Chain geprüft, nie blind wiederholt. Wiederholt wird nur nach nachweislich abgelaufener Transaktion – mit *frischer* Quote.
* **Private Key** nur aus der Umgebungsvariable; nie geloggt (alle Logs laufen durch einen Redaktions-Formatter), nie ausgeliefert, nie gespeichert.
* **Dashboard:** Passwort (konstante-Zeit-Vergleich), signierter Session-Cookie (HttpOnly, SameSite=Lax, Secure hinter HTTPS), Login-Bremse (8 Versuche/10 Min), CSRF-Schutz per Custom-Header, strikte CSP, `X-Frame-Options: DENY`, keine API-Doku-Endpunkte.
* **Live-Start** validiert bei *jedem* Klick neu: RPC gesund, Guthaben ≥ Minimum, Jupiter erreichbar. Im Live-Modus braucht „SYSTEM STARTEN“ eine zusätzliche Bestätigung.
* **Fremde Token** in der Wallet werden nicht angefasst (`ADOPT_UNKNOWN_TOKENS = False`).

## Wichtige Einstellungen (`config.py`)

| Einstellung | Standard | Bedeutung |
|---|---|---|
| `PAPER_TRADING` | `True` | **Der Schalter** |
| `LIVE_TRADING_CAPITAL_SOL` | `1.0` | Mit höchstens so viel SOL arbeitet der Bot live |
| `MAX_POSITION_SOL` / `MAX_POSITION_PCT` | `0.5` / `10 %` | Obergrenzen pro Trade |
| `RISK_PER_TRADE_PCT` | `1.5 %` | Verlust bei Initial-Stop, bezogen auf das Kapital |
| `INITIAL_STOP_LOSS_PCT` | `20 %` | Harter Stop |
| `TRAILING_TIERS` | `(0,20) (40,16) (100,12) (300,8)` | Stop-Abstand vom Hoch; wird mit Gewinn enger |
| `PARTIAL_TAKE_PROFIT` | `((100, 50),)` | Bei +100 % die Hälfte verkaufen |
| `DAILY_LOSS_LIMIT_PCT` / `KILL_SWITCH_DRAWDOWN_PCT` | `10 %` / `35 %` | Automatische Bremsen |
| `MIN_LIQUIDITY_USD`, `MIN_LP_LOCKED_PCT`, `MAX_RUGCHECK_SCORE` | `15 000`, `80`, `40` | Scout-Filter |
| `PROFIT_CURRENCY` | `"SOL"` | `"USDC"`: realisierter Gewinn wird in USDC getauscht (in deiner Wallet) |

Secrets (nur als Umgebungsvariablen): `DASHBOARD_PASSWORD`, `SOLANA_PRIVATE_KEY`, `SOLANA_RPC_URL`, `JUPITER_API_KEY`, `DATABASE_URL` – siehe [`.env.example`](.env.example).

## Projektstruktur

```
config.py            ← Schalter + alle Einstellungen (PAPER_TRADING ganz oben)
main.py              ← Start (uvicorn)         selftest.py ← Verbindungstest (+ --dry-run)
crew/
  engine.py          ← Orchestrator (Loops, Komitee, Ein-/Ausstiege, NOT-AUS, Snapshot)
  agents/            ← scout.py · sentiment.py · technical.py · risk.py · base.py
  market/            ← dexscreener · gecko · rugcheck · solana_rpc · contracts · provider · simulator
  chain/             ← jupiter.py (Swap V2) · wallet.py (Schlüssel + Partial-Signing)
  trading/           ← executor.py (Paper/Live) · portfolio.py (Ledger) · store.py (Datei/Postgres)
  web/               ← app.py · auth.py · static/ (index.html, css, js, Icons)
tests/               ← 140 Tests (Parser gegen echte API-Antworten, Fake-Jupiter/RPC, Engine-Läufe, Web/Auth)
```

## Was getestet ist – und was nicht (ehrlich)

**Verifiziert:**
* API-Formate von Dexscreener, GeckoTerminal, RugCheck, Solana-RPC und **Jupiter Swap V2** wurden gegen die *echten* Dienste geprüft (Nov 2026); Parser-Tests laufen mit aufgezeichneten Original-Antworten (`tests/fixtures`).
* Der komplette Paper-Modus läuft mit **Live-Marktdaten** (Scout → Contract-Check → Watchlist → Komitee) und mit echten Jupiter-Quotes für simulierte Käufe/Verkäufe.
* Live-Executor-Kette (order → Guard-Simulation → Partial-Sign → execute → Fill, Retry- und Fehlerpfade) gegen einen simulierten Jupiter-/RPC-Server; Signaturen werden kryptografisch verifiziert.
* Browser-Abnahme (Chromium, iPhone-Format): keine Konsolenfehler, Touch-Scrollen über dem Chart, NOT-AUS-Ablauf, Passwort-Login.

**Nicht möglich / nicht verifiziert:**
* **Kein einziger echter On-Chain-Swap mit echtem Geld** wurde ausgeführt. Nutze `python selftest.py --dry-run` (baut + signiert + *simuliert*) und starte klein.
* **Profitabilität ist nicht belegt.** Die Strategie-Schwellen sind begründete Heuristiken, aber nicht an historischen Daten optimiert oder rückgetestet; der Demo-Markt ist synthetisch und sagt nichts über echte Renditen.
* Das Replit-Deployment selbst konnte ich nicht ausprobieren (Anleitung folgt Replits aktueller Doku, Stand Nov 2026).

## Bekannte Grenzen

* Marktdaten sind 15–60 s alt (CDN-Caches der Anbieter); schnelle Pump-&-Dump-Zyklen kann das System nicht „vorne“ treffen.
* Ohne Jupiter-API-Key gilt ein knappes Rate-Limit (≈ 0,5 Anfragen/s); der Preis-Poll weicht dann bei Engpässen auf Dexscreener aus.
* Pro neuem Token fallen ≈ 0,002 SOL Konto-Miete an (nicht automatisch zurückgeholt).
* Sentiment ist eine Marktverhaltens-Heuristik, keine echte Social-Media-Analyse.

## Lizenz & Haftung

Teil des Repositories (Apache-2.0). Nutzung auf eigene Gefahr. Die Autoren haften nicht für Verluste.
