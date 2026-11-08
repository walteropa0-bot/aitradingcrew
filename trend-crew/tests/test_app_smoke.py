"""Runs the real app.py headlessly (Streamlit AppTest) against a scripted engine."""
import os

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest                                # noqa: E402

from crew import runtime                                                # noqa: E402
from tests.test_engine import World                                     # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    w = World(tmp_path)
    w.eng.command("start")
    w.run(3)
    monkeypatch.setattr(runtime, "get_engine", lambda: w.eng)
    at = AppTest.from_file(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py"), default_timeout=30)
    return at, w


def test_dashboard_renders_without_errors(app):
    at, w = app
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    labels = [b.label for b in at.button]
    assert "[BOT PAUSIEREN]" in labels and "[NOT-AUS: ALLES LIQUIDIEREN]" in labels      # bot is running
    assert "[BOT STARTEN]" not in labels


def test_panic_button_asks_for_confirmation_then_liquidates(app):
    at, w = app
    at.run()
    next(b for b in at.button if b.label == "[NOT-AUS: ALLES LIQUIDIEREN]").click().run()
    assert any(b.label == "[JA – JETZT ALLES VERKAUFEN]" for b in at.button)
    assert w.pf.positions, "nothing may be sold before the user confirms"
    next(b for b in at.button if b.label == "[JA – JETZT ALLES VERKAUFEN]").click().run()
    w.run(2)
    assert w.pf.positions == {} and w.pf.halted and not at.exception


def test_pause_then_start_buttons_toggle_the_bot(app):
    at, w = app
    at.run()
    next(b for b in at.button if b.label == "[BOT PAUSIEREN]").click().run()
    assert w.pf.running is False
    at.run()
    assert any(b.label == "[BOT STARTEN]" for b in at.button)
    next(b for b in at.button if b.label == "[BOT STARTEN]").click().run()
    assert w.pf.running is True


def test_password_gate_blocks_the_dashboard(app, monkeypatch):
    at, w = app
    monkeypatch.setattr("config.DASHBOARD_PASSWORD", "s3cret-pass")
    at.run()
    assert not at.exception
    assert not any(b.label == "[NOT-AUS: ALLES LIQUIDIEREN]" for b in at.button)        # nothing of the dashboard is rendered
    assert at.text_input and at.text_input[0].label == "Passwort"
    at.text_input[0].set_value("wrong")                                   # form widgets only count together with the submit click
    next(b for b in at.button if b.label == "ENTSPERREN").click().run()
    assert not any(b.label == "[NOT-AUS: ALLES LIQUIDIEREN]" for b in at.button)
    assert any("Falsches Passwort" in h.value for h in at.get("html"))
    at.text_input[0].set_value("s3cret-pass")
    next(b for b in at.button if b.label == "ENTSPERREN").click().run()
    assert any(b.label == "[NOT-AUS: ALLES LIQUIDIEREN]" for b in at.button)


def test_live_mode_without_password_shows_nothing(app, monkeypatch):
    at, w = app
    monkeypatch.setattr("config.PAPER_TRADING", False)
    monkeypatch.setattr("config.DASHBOARD_PASSWORD", "")
    at.run()
    assert not at.exception
    assert not any(b.label.startswith("[") for b in at.button)
