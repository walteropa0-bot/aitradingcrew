import json
import os
import sys
import types

import pytest

import config
from crew.agents.scout import Scout, prefilter
from crew.agents.sentiment import SentimentAnalyst
from crew.agents.technical import TechAnalyst
from crew.events import EventBus
from crew.market.simulator import SimMarket
from crew.models import Pair
from crew.trading.store import FileStore, PgStore, make_store

T0 = 1_800_000_000.0


def pr(**kw) -> Pair:
    base = dict(mint="M", symbol="MEME", quote_symbol="SOL", liquidity_usd=40_000, fdv=200_000, vol_m5=8_000, vol_h1=20_000,
                buys_m5=70, sells_m5=40, buys_h1=200, sells_h1=150, chg_m5=18.0, chg_h1=40.0, created_ms=int((T0 - 20 * 60) * 1000),
                price_sol=1e-6, ts=T0, socials=["twitter", "telegram"], websites=["https://x.io"], buyers_m5=45, sellers_m5=25)
    base.update(kw)
    return Pair(**base)


# ───────────── scout prefilter ─────────────
def test_prefilter_accepts_a_healthy_pool():
    assert prefilter(pr(), T0) == []


@pytest.mark.parametrize("kw,needle", [
    ({"liquidity_usd": 3_000}, "Liquidität"), ({"quote_symbol": "WBTC"}, "Quote"), ({"buys_m5": 2, "sells_m5": 1}, "Aktivität"),
    ({"created_ms": int((T0 - 30 * 3600) * 1000)}, "zu alt"), ({"created_ms": int((T0 - 30) * 1000)}, "zu neu"),
    ({"fdv": 90_000_000}, "FDV"), ({"price_sol": 0.0}, "Preis"),
])
def test_prefilter_rejects(kw, needle):
    assert any(needle in r for r in prefilter(pr(**kw), T0))


async def test_scout_never_vets_unsafe_contracts_in_the_demo_market():
    sim = SimMarket(seed=11, auto=False, now_fn=lambda: T0)
    sim.advance(1800)
    scout = Scout(EventBus(), sim, step_delay=0.0)
    seen_kinds = set()
    for _ in range(6):
        res = await scout.scan(exclude=set(), is_blacklisted=lambda m: False)
        for p, rep in res.vetted:
            seen_kinds.add(sim.tokens[p.mint].kind)
            assert rep.ok and rep.mint_authority_disabled and rep.freeze_authority_disabled
        sim.advance(120)
    assert not (seen_kinds & {"mint_auth", "freeze", "lp_unlocked"})
    assert scout.stats["checked"] > 0


# ───────────── sentiment ─────────────
def test_sentiment_rewards_hype_and_penalises_bot_activity():
    s = SentimentAnalyst(EventBus())
    hype = s.analyze(pr(vol_m5=40_000, vol_h1=60_000, buys_m5=300, sells_m5=120, buyers_m5=140, sellers_m5=60, boosts=1))
    quiet = s.analyze(pr(vol_m5=500, vol_h1=60_000, buys_m5=20, sells_m5=25, buyers_m5=10, socials=[], websites=[]))
    assert hype.score > 60 and hype.verdict in ("bullish", "neutral") and quiet.score < 40 and quiet.verdict == "bearish"
    wash = s.analyze(pr(buys_m5=400, sells_m5=300, buyers_m5=12, sellers_m5=9))
    assert any("Wash" in w or "Bot" in w for w in wash.warnings)
    normal = s.analyze(pr(buys_m5=400, sells_m5=300, buyers_m5=300, sellers_m5=250))
    assert wash.score < normal.score


# ───────────── tech ─────────────
def feed(t: TechAnalyst, base: Pair, series):
    """series: [(dt_seconds, price, vol_m5)] -> samples into the analyst; returns last pair."""
    p = base
    for dt, price, vol in series:
        p = pr(**{**base.__dict__, "ts": T0 + dt, "price_sol": price, "vol_m5": vol})
        t.update(p)
    return p


def test_tech_signals_buy_on_a_confirmed_breakout():
    t = TechAnalyst(EventBus())
    base = pr(vol_h1=20_000, chg_m5=22.0, chg_h1=45.0, buys_m5=140, sells_m5=70)
    last = feed(t, base, [(0, 1.00e-6, 3_000), (60, 1.06e-6, 5_000), (120, 1.13e-6, 8_000), (180, 1.20e-6, 12_000), (240, 1.26e-6, 17_000)])
    r = t.analyze(last)
    assert r.signal == "BUY" and r.score >= config.TECH_MIN_SCORE and r.vol_spike >= config.MIN_VOLUME_SPIKE and r.samples >= 4


def test_tech_refuses_to_chase_an_overextended_pump():
    t = TechAnalyst(EventBus())
    r = t.analyze(pr(chg_m5=config.MAX_CHASE_M5_PCT + 30, vol_h1=5_000, vol_m5=40_000))
    assert r.signal == "AVOID" and any("hinterherlaufen" in x for x in r.reasons)


def test_tech_avoids_a_falling_knife():
    t = TechAnalyst(EventBus())
    base = pr(chg_m5=-12.0, buys_m5=30, sells_m5=90)
    last = feed(t, base, [(0, 1.5e-6, 9_000), (60, 1.4e-6, 9_000), (120, 1.2e-6, 9_000), (180, 1.05e-6, 9_000)])
    assert t.analyze(last).signal == "AVOID"


def test_tech_waits_when_volume_is_not_accelerating():
    t = TechAnalyst(EventBus())
    r = t.analyze(pr(vol_m5=1_000, vol_h1=60_000, chg_m5=4.0))
    assert r.signal == "WAIT"


def test_tech_ignores_repeated_cached_samples():
    t = TechAnalyst(EventBus())
    for i in range(10):
        t.update(pr(ts=T0 + i * 2))               # same numbers every 2 s (API cache) -> only the first counts
    assert len(t.samples["M"]) == 1


# ───────────── persistence ─────────────
def test_file_store_roundtrip_and_backup_recovery(tmp_path):
    path = str(tmp_path / "s.json")
    st = FileStore(path)
    assert st.load() is None
    st.save({"a": 1})
    st.save({"a": 2})
    assert st.load() == {"a": 2}
    with open(path, "w") as fh:                   # corrupt the main file
        fh.write("{not json")
    assert st.load() in ({"a": 1}, {"a": 2})      # falls back to the rolling backup
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


def test_file_store_failed_save_leaves_old_state_untouched(tmp_path):
    path = str(tmp_path / "s.json")
    st = FileStore(path)
    st.save({"ok": True})
    with pytest.raises(TypeError):
        st.save({"bad": object()})
    assert st.load() == {"ok": True}
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]


class _FakePgConn:
    DB: dict = {}

    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=()):
        s = " ".join(sql.split()).upper()
        if s.startswith("CREATE TABLE"):
            return self
        if s.startswith("INSERT"):
            _FakePgConn.DB[params[0]] = params[1]
            return self
        if s.startswith("SELECT"):
            self._row = (_FakePgConn.DB[params[0]],) if params[0] in _FakePgConn.DB else None
            return self
        raise AssertionError(sql)
    def fetchone(self): return self._row


def test_postgres_store_logic_with_a_fake_driver(monkeypatch):
    fake = types.SimpleNamespace(connect=lambda *a, **k: _FakePgConn())
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    _FakePgConn.DB.clear()
    st = PgStore("postgresql://x", "state_paper")
    assert st.load() is None
    st.save({"cash_sol": 9.5, "positions": []})
    assert st.load() == {"cash_sol": 9.5, "positions": []}
    assert json.loads(_FakePgConn.DB["state_paper"])["cash_sol"] == 9.5


def test_make_store_falls_back_to_a_file_if_postgres_is_unusable(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://nowhere.invalid/db")
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "psycopg", types.SimpleNamespace(connect=lambda *a, **k: (_ for _ in ()).throw(OSError("down"))))
    store = make_store("paper")
    assert isinstance(store, FileStore) and store.path.endswith("state_paper.json")
