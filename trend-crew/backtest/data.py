"""Historical candle data: download from Binance's public market-data host, cache as JSON, resample."""
from __future__ import annotations

import json
import os
import time
import urllib.request

import numpy as np

BINANCE_PUBLIC = "https://data-api.binance.vision/api/v3/klines"   # public data, no key, works from US servers
TF_MS = {"1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000}
DEFAULT_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")


def download(symbol: str, start_ms: int, end_ms: int, interval: str = "1h", pause: float = 0.15) -> list[list[float]]:
    """symbol like 'BTCUSDT'. Returns rows [ts, o, h, l, c, v]."""
    out: list[list[float]] = []
    cur = start_ms
    while cur < end_ms:
        url = f"{BINANCE_PUBLIC}?symbol={symbol}&interval={interval}&startTime={cur}&endTime={end_ms}&limit=1000"
        rows = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(url, timeout=20) as resp:
                    rows = json.load(resp)
                break
            except Exception:                                  # network hiccup -> retry with back-off
                time.sleep(1.5 * (attempt + 1))
        if not rows:
            break
        out.extend([[r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows])
        cur = rows[-1][0] + 1
        if len(rows) < 1000:
            break
        time.sleep(pause)
    return out


def load_or_download(asset: str, start: str, cache_dir: str = DEFAULT_CACHE, quote: str = "USDT") -> np.ndarray:
    """Cached 1h candles for e.g. BTC -> BTCUSDT. `start` is YYYY-MM-DD (UTC)."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{asset}{quote}_1h.json")
    start_ms = int(time.mktime(time.strptime(start, "%Y-%m-%d")) * 1000) - time.timezone * 1000
    rows = None
    if os.path.exists(path):
        with open(path) as fh:
            rows = json.load(fh)
        if rows and rows[0][0] > start_ms + 7 * 86_400_000:       # cache starts too late -> refetch
            rows = None
        elif rows and time.time() * 1000 - rows[-1][0] > 3 * 86_400_000:   # stale tail -> extend
            more = download(f"{asset}{quote}", rows[-1][0] + 1, int(time.time() * 1000))
            rows = rows + more
            with open(path, "w") as fh:
                json.dump(rows, fh)
    if rows is None:
        rows = download(f"{asset}{quote}", start_ms, int(time.time() * 1000))
        if not rows:
            raise RuntimeError(f"Keine Daten für {asset}{quote} erhalten (Netzwerk/Binance-Datenserver erreichbar?)")
        with open(path, "w") as fh:
            json.dump(rows, fh)
    arr = np.asarray(rows, dtype=np.float64)
    return arr[arr[:, 0] >= start_ms]


def load_daily(asset: str, start: str, cache_dir: str = DEFAULT_CACHE, quote: str = "USDT") -> np.ndarray:
    """Cached native daily candles (UTC) – used for the trend regime, needs history BEFORE the test start."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{asset}{quote}_1d.json")
    start_ms = int(time.mktime(time.strptime(start, "%Y-%m-%d")) * 1000) - time.timezone * 1000
    rows = None
    if os.path.exists(path):
        with open(path) as fh:
            rows = json.load(fh)
        if rows and time.time() * 1000 - rows[-1][0] > 3 * 86_400_000:
            rows = rows + download(f"{asset}{quote}", rows[-1][0] + 1, int(time.time() * 1000), "1d")
            with open(path, "w") as fh:
                json.dump(rows, fh)
    if rows is None:
        rows = download(f"{asset}{quote}", start_ms, int(time.time() * 1000), "1d")
        if not rows:
            raise RuntimeError(f"Keine Tagesdaten für {asset}{quote} erhalten")
        with open(path, "w") as fh:
            json.dump(rows, fh)
    arr = np.asarray(rows, dtype=np.float64)
    return arr[arr[:, 0] >= start_ms]


def resample(candles: np.ndarray, timeframe: str) -> np.ndarray:
    """Aggregate 1h candles into `timeframe` buckets aligned to UTC; the unfinished last bucket is dropped."""
    if timeframe == "1h":
        return candles
    step = TF_MS[timeframe]
    base = TF_MS["1h"]
    bucket = (candles[:, 0] // step).astype(np.int64)
    out = []
    uniq, idx = np.unique(bucket, return_index=True)
    bounds = list(idx) + [len(candles)]
    per = step // base
    for k in range(len(uniq)):
        chunk = candles[bounds[k]:bounds[k + 1]]
        if len(chunk) != per:                                  # incomplete bucket (start/end/gap) -> skip
            continue
        out.append([uniq[k] * step, chunk[0, 1], chunk[:, 2].max(), chunk[:, 3].min(), chunk[-1, 4], chunk[:, 5].sum()])
    return np.asarray(out, dtype=np.float64)


def align(series: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Keep only timestamps present in every series (so all coins share one time grid)."""
    common = None
    for arr in series.values():
        ts = set(arr[:, 0].tolist())
        common = ts if common is None else common & ts
    keep = np.array(sorted(common or []))
    return {k: v[np.isin(v[:, 0], keep)] for k, v in series.items()}
