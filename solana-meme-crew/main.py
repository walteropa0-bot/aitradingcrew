"""Start-Datei:  python main.py

Startet das SOL·CREW-System (Backend + Dashboard) auf Port 8000 bzw. $PORT.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _check_dependencies() -> None:
    missing = []
    for mod, pip_name in (("fastapi", "fastapi"), ("uvicorn", "uvicorn"), ("httpx", "httpx"),
                          ("solders", "solders"), ("plotly", "plotly")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    if missing:
        print("\n❌ Es fehlen Python-Pakete:", ", ".join(missing))
        print("   Lösung:  pip install -r requirements.txt")
        print("   (In Replit: unten im Reiter 'Shell' diesen Befehl eingeben und Enter drücken.)\n")
        sys.exit(1)


def main() -> None:
    _check_dependencies()
    import uvicorn

    import config
    from crew.web.app import create_app

    mode = "DEMO-MODUS (Spielgeld)" if config.PAPER_TRADING else "LIVE-MODUS (ECHTES GELD!)"
    print(f"\n=== {config.APP_NAME} v{config.VERSION} · {mode} · Port {config.PORT} ===\n", flush=True)
    uvicorn.run(
        create_app(),
        host=config.HOST,
        port=config.PORT,
        log_level="warning",
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips="*",       # only reachable through the platform proxy
        timeout_keep_alive=30,
    )


if __name__ == "__main__":
    main()
