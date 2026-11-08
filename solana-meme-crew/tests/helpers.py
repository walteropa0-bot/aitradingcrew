from __future__ import annotations

import json
import os
import random

from crew.engine import Engine
from crew.market.simulator import SimMarket
from crew.trading.executor import PaperExecutor
from crew.trading.portfolio import Portfolio
from crew.trading.store import FileStore

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(name: str):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as fh:
        return json.load(fh)


def make_engine(tmp_path, seed: int = 5, auto: bool = False, start: float = 10.0):
    sim = SimMarket(seed=seed, auto=auto, now_fn=lambda: 1_800_000_000.0)
    pf = Portfolio("paper", start, sim.now())
    store = FileStore(os.path.join(str(tmp_path), "state.json"))
    eng = Engine(market=sim, executor=PaperExecutor(sim, rng=random.Random(seed)), store=store, portfolio=pf, step_delay=0.0)
    return sim, pf, eng


async def drive(sim, eng, seconds: float, *, step: float = 4.0, stop_when=None) -> int:
    """Advance the simulated market and run all engine ticks, like the real loops would."""
    import asyncio
    steps = int(seconds / step)
    for i in range(steps):
        sim.advance(step)
        await eng._position_tick()
        if i % 3 == 0:
            await eng._watch_tick()
        if i % 8 == 0:
            await eng._scan_tick()
        if i % 2 == 0:
            await eng._housekeeping_tick()
        for _ in range(3):
            await asyncio.sleep(0)
        if stop_when is not None and stop_when():
            return i
    return steps
