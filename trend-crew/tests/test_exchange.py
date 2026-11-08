import ccxt
import numpy as np
import pytest

from crew.exchange import CLOSE_GRACE_SEC, ExchangeError, classify, clean_candles, split_closed

H4 = 4 * 3600 * 1000


def test_clean_candles_sorts_dedupes_and_drops_garbage():
    rows = [[3 * H4, 10, 11, 9, 10.5, 1], [1 * H4, 10, 11, 9, 10.5, 1], [1 * H4, 99, 99, 99, 99, 9],      # duplicate ts
            [2 * H4, 10, 11, 9, None, 1], [4 * H4, 0, 0, 0, 0, 0], [5 * H4, 10, 9, 11, 10, 1],              # None / zero / high<low
            [6 * H4, float("nan"), 1, 1, 1, 1], [7 * H4, 10, 12, 8, 11, 2]]
    out = clean_candles(rows)
    assert out[:, 0].tolist() == [1 * H4, 3 * H4, 7 * H4]
    assert out[0, 4] == 10.5                                   # first of the duplicates wins
    assert clean_candles([]).shape == (0, 6) and clean_candles(None).shape == (0, 6)


def test_split_closed_respects_the_period_and_a_short_grace():
    c = np.array([[0, 1, 1, 1, 1, 1], [H4, 1, 1, 1, 1, 1], [2 * H4, 1, 1, 1, 1, 1]], dtype=float)
    now = 2 * H4 + 1000                                          # 1 s after the 2nd candle ended (inside the grace period)
    closed, forming = split_closed(c, "4h", now)
    assert len(closed) == 1 and forming[0] == 2 * H4            # candle 2 is still inside the grace period, candle 3 is forming
    closed, forming = split_closed(c, "4h", 2 * H4 + CLOSE_GRACE_SEC * 1000)
    assert len(closed) == 2 and forming[0] == 2 * H4
    closed, forming = split_closed(c, "4h", 3 * H4 + 5000)
    assert len(closed) == 3 and forming is None
    assert split_closed(np.empty((0, 6)), "4h", 1.0)[1] is None


@pytest.mark.parametrize("exc,kind", [
    (ccxt.AuthenticationError("bad key"), "auth"), (ccxt.PermissionDenied("no right"), "auth"),
    (ccxt.InsufficientFunds("x"), "funds"), (ccxt.RateLimitExceeded("slow down"), "rate"), (ccxt.DDoSProtection("x"), "rate"),
    (ccxt.RequestTimeout("t"), "network"), (ccxt.NetworkError("n"), "network"), (ccxt.OnMaintenance("m"), "maintenance"),
    (ccxt.ExchangeNotAvailable("e"), "maintenance"), (ccxt.OrderNotFound("o"), "not_found"), (ccxt.InvalidOrder("i"), "bad_request"),
    (ccxt.BadSymbol("s"), "bad_request"), (ccxt.ExchangeError("generic"), "exchange"), (TimeoutError("t"), "network"),
    (ConnectionResetError("r"), "network"), (RuntimeError("???"), "exchange"),
])
def test_error_classification(exc, kind):
    assert classify(exc).kind == kind


def test_geo_block_is_recognised_and_explained():
    err = classify(ccxt.ExchangeNotAvailable('binance GET https://api.binance.com/... 451 {"msg":"Service unavailable from a restricted location"}'))
    assert err.kind == "geo" and "kraken" in err.hint.lower() and not err.retryable
    again = classify(err)
    assert again is err                                          # already classified errors pass through unchanged


def test_retryable_kinds():
    assert classify(ccxt.RequestTimeout("x")).retryable and classify(ccxt.RateLimitExceeded("x")).retryable
    assert not classify(ccxt.AuthenticationError("x")).retryable and not isinstance(classify(ccxt.BadRequest("x")), type(None))
    assert isinstance(classify(ccxt.BadRequest("x")), ExchangeError)
