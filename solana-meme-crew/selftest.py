"""Verbindungstest:   python selftest.py            (nur lesen, kostet nichts)
                    python selftest.py --dry-run  (Live-Modus: baut + signiert + SIMULIERT einen Swap, sendet nichts)
"""
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


async def main(dry_run: bool) -> int:
    import config
    from crew.factory import ConfigError, build
    from crew.selftest import run_checks, summarize

    mode = "DEMO (Spielgeld)" if config.PAPER_TRADING else "LIVE (ECHTES GELD)"
    print(f"\n{config.APP_NAME} – Verbindungstest · Modus: {mode}\n" + "─" * 62)
    try:
        built = build()
    except ConfigError as exc:
        print(f"❌ Konfiguration: {exc}")
        return 1
    for p in built.problems:
        print(f"⚠️  {p}")
    checks = await run_checks(built.market, built.engine.executor, dry_run=dry_run)
    for c in checks:
        mark = "✅" if c.ok else ("❌" if c.critical else "⚠️ ")
        print(f"{mark} {c.name:<42} {c.detail}  ({c.ms} ms)")
    summary = summarize(checks)
    print("─" * 62 + f"\n{'✅' if summary['ok'] else '❌'} {summary['message']}\n")
    close = getattr(built.market, "aclose", None)
    if close:
        await close()
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--dry-run" in sys.argv)))
