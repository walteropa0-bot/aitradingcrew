"""Start-Datei:   python main.py

Startet (1) den Handels-Motor als Hintergrund-Thread – er läuft rund um die Uhr, auch wenn
niemand das Dashboard offen hat – und (2) das Dashboard (Streamlit) auf Port 5000 bzw. $PORT.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
os.chdir(HERE)                         # so ./.streamlit/config.toml and relative paths are always found


def _check_dependencies() -> None:
    missing = []
    for mod, pip_name in (("streamlit", "streamlit"), ("ccxt", "ccxt"), ("numpy", "numpy"), ("plotly", "plotly")):
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
    import config
    from crew import runtime

    problems = runtime.validate_config()
    if problems:
        print("\n❌ Mit den Einstellungen stimmt etwas nicht:")
        for p in problems:
            print("   •", p)
        print()
        sys.exit(1)

    mode = "DEMO-MODUS (Spielgeld)" if config.PAPER_TRADING else "LIVE-MODUS (ECHTES GELD!)"
    print(f"\n=== {config.APP_NAME} v{config.VERSION} · {mode} · Börse {config.EXCHANGE} · Port {config.PORT} ===\n", flush=True)

    runtime.get_engine()                      # starts the trading thread BEFORE the web server

    from streamlit.web import bootstrap

    flags = {
        "server.port": config.PORT,
        "server.address": config.HOST,
        "server.headless": True,
        "server.fileWatcherType": "none",     # never re-import modules (would create a second engine)
        "server.runOnSave": False,
        "server.enableCORS": False,           # platform proxies rewrite origins; the password gate protects the app
        "server.enableXsrfProtection": False,
        "server.enableStaticServing": True,
        "browser.gatherUsageStats": False,
        "global.developmentMode": False,
        "client.toolbarMode": "minimal",
        "theme.base": "dark",
        "theme.primaryColor": "#00f5a0",
        "theme.backgroundColor": "#05060a",
        "theme.secondaryBackgroundColor": "#0d1017",
        "theme.textColor": "#eef1f6",
    }
    bootstrap.load_config_options(flag_options=flags)
    bootstrap.run(os.path.join(HERE, "app.py"), False, [], flags)


if __name__ == "__main__":
    main()
