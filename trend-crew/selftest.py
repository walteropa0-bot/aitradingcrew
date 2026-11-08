"""Verbindungstest:   python selftest.py        (in Replit: Reiter "Shell")

Prüft, ob der Bot von DIESEM Server aus alles erreicht, was er braucht. Es werden keine Orders erteilt.
Mit gesetzten API-Keys wird zusätzlich nur das Guthaben GELESEN (kein Handel, keine Überweisung).
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OK, BAD, WARN = "✅", "❌", "⚠️ "


def line(mark: str, text: str) -> None:
    print(f"{mark} {text}", flush=True)


def main() -> int:
    import config
    from crew import runtime
    from crew.util import de
    from crew.exchange import Exchange, ExchangeError, split_closed

    failures = 0
    mode = "DEMO (Spielgeld)" if config.PAPER_TRADING else "LIVE (ECHTES GELD)"
    print(f"\n{config.APP_NAME} v{config.VERSION} – Verbindungstest · Modus: {mode}\n" + "─" * 64)

    problems = runtime.validate_config()
    for p in problems:
        line(BAD, p)
        failures += 1
    if not problems:
        line(OK, f"Einstellungen gültig · Börse {config.EXCHANGE} · Märkte {', '.join(config.SYMBOLS)} · {config.TIMEFRAME}-Kerzen")
    if config.DASHBOARD_PASSWORD:
        line(OK, "DASHBOARD_PASSWORD ist gesetzt")
    else:
        line(BAD if not config.PAPER_TRADING else WARN, "DASHBOARD_PASSWORD fehlt" + (" – im LIVE-Modus Pflicht!" if not config.PAPER_TRADING else " (im Demo-Modus erlaubt, aber nicht empfohlen)"))
        failures += 0 if config.PAPER_TRADING else 1

    ex = Exchange(config.EXCHANGE, api_key=config.API_KEY, secret=config.API_SECRET, password=config.API_PASSWORD, sandbox=config.EXCHANGE_SANDBOX)
    try:
        t0 = time.time()
        ex.load_markets()
        line(OK, f"Börse erreichbar ({int((time.time() - t0) * 1000)} ms) – Marktdaten geladen")
    except ExchangeError as exc:
        line(BAD, f"Börse nicht erreichbar: {exc}")
        if exc.hint:
            print(f"   → {exc.hint}")
        print("\nOhne Börsen-Verbindung kann der Bot nicht arbeiten. Siehe ANLEITUNG_REPLIT.md, Abschnitt „Probleme“.")
        return 1

    for sym in config.SYMBOLS:
        try:
            if not ex.has_symbol(sym):
                line(BAD, f"{sym}: Diesen Markt gibt es auf {config.EXCHANGE} nicht (QUOTE_CURRENCY prüfen)")
                failures += 1
                continue
            tick = ex.fetch_ticks([sym])[sym]
            rules = ex.rules(sym)
            c4, _ = split_closed(ex.fetch_candles(sym, config.TIMEFRAME, config.CANDLE_HISTORY), config.TIMEFRAME, time.time() * 1000)
            cd, _ = split_closed(ex.fetch_candles(sym, config.HTF_TIMEFRAME, config.HTF_HISTORY), config.HTF_TIMEFRAME, time.time() * 1000)
            enough = len(c4) >= 220 and len(cd) >= 205
            line(OK if enough else BAD, f"{sym}: Kurs {de(tick.last, 2)} · Spread {de(tick.spread_pct, 3)} % · {len(c4)} {config.TIMEFRAME}- und {len(cd)} Tageskerzen · "
                 f"Mindestorder {rules.min_amount} / {rules.min_cost} {config.QUOTE_CURRENCY}")
            failures += 0 if enough else 1
        except ExchangeError as exc:
            line(BAD, f"{sym}: {exc}")
            failures += 1

    if config.API_KEY and config.API_SECRET:
        try:
            info = ex.check_credentials()
            line(OK if info["ok"] else BAD, f"API-Key: {info['detail']}")
            if info.get("withdraw_enabled"):
                line(BAD, "Dieser API-Key darf Geld AUSZAHLEN! Erstelle einen neuen Key OHNE Auszahlungsrecht.")
                failures += 1
            elif info.get("withdraw_enabled") is False:
                line(OK, "API-Key hat kein Auszahlungsrecht")
            else:
                line(WARN, "Ob der Key auszahlen darf, lässt sich von hier nicht prüfen – kontrolliere es in den Key-Einstellungen deiner Börse (Auszahlen AUS).")
            bal = ex.fetch_balances()
            free = bal.get(config.QUOTE_CURRENCY, {}).get("free", 0.0)
            line(OK if free > 0 else WARN, f"Freies Guthaben {config.QUOTE_CURRENCY}: {de(free, 2)}" + (f" · Einsatzlimit LIVE_CAPITAL: {de(config.LIVE_CAPITAL, 2)}" if not config.PAPER_TRADING else ""))
            if not config.PAPER_TRADING and config.LIVE_CAPITAL <= 0:
                line(BAD, "LIVE_CAPITAL ist 0 – setze den maximalen Einsatz als Secret/Umgebungsvariable.")
                failures += 1
        except ExchangeError as exc:
            line(BAD, f"API-Key-Test fehlgeschlagen: {exc}")
            failures += 1
    elif not config.PAPER_TRADING:
        line(BAD, "LIVE-Modus, aber EXCHANGE_API_KEY / EXCHANGE_API_SECRET fehlen.")
        failures += 1
    else:
        line(OK, "Demo-Modus: keine API-Keys nötig")

    print("─" * 64)
    print("Alles bereit ✓" if failures == 0 else f"{failures} Problem(e) gefunden – bitte oben lesen.")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
