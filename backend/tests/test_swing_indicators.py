"""Indicator parity with ST, and ST's own NaN-close tests
(ST tests/test_indicators_nan.py, ported)."""
import importlib.util
import math
import os
import sys

import numpy as np
import pandas as pd

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import indicators as ind  # noqa: E402

_ST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "swing_trader_st")


def _st(name):
    spec = importlib.util.spec_from_file_location(
        f"_swing_st_{name}", os.path.join(_ST_DIR, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ohlcv(seed, n=260, nan_tail=False):
    """A seeded daily OHLCV frame shaped like market_data.get_daily_bars."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, n)))
    high = close * (1 + rng.uniform(0.0, 0.02, n))
    low = close * (1 - rng.uniform(0.0, 0.02, n))
    vol = rng.integers(500_000, 2_000_000, n).astype(float)
    if nan_tail:
        close = close.copy()
        close[-1] = np.nan
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame({"Open": close, "High": high, "Low": low,
                         "Close": close, "Volume": vol}, index=idx)


def _same(a, b):
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-12)


def test_get_latest_indicators_matches_st_on_seeded_frames():
    st = _st("paper_trader_pure")
    frames = {f"S{i}": _ohlcv(i, nan_tail=(i == 3)) for i in range(6)}
    long = ind.bars_to_long_frame(frames)
    ours = ind.get_latest_indicators(long)
    theirs = st.get_latest_indicators(long)
    assert set(ours) == set(theirs) == set(frames)
    for sym in frames:
        for key, value in theirs[sym].items():
            assert _same(ours[sym][key], value), (sym, key)


def test_indicators_for_frames_equals_the_long_frame_path():
    frames = {f"S{i}": _ohlcv(10 + i) for i in range(4)}
    one_by_one = ind.indicators_for_frames(frames, {})
    all_at_once = ind.get_latest_indicators(ind.bars_to_long_frame(frames))
    assert one_by_one == all_at_once


def test_indicators_for_frames_reads_the_periods_from_config():
    frames = {"S1": _ohlcv(7)}
    base = ind.indicators_for_frames(frames, {})
    fast = ind.indicators_for_frames(frames, {"rsi_period": 7})
    assert base["S1"]["rsi"] != fast["S1"]["rsi"]
    assert base["S1"]["sma200"] == fast["S1"]["sma200"]


def test_primitive_indicators_are_verbatim():
    st = _st("paper_trader_pure")
    wst = _st("wheel_trader_pure")
    f = _ohlcv(42)
    c, h, lo = f["Close"], f["High"], f["Low"]
    pd.testing.assert_series_equal(ind._rsi(c, 14), st._rsi(c, 14))
    pd.testing.assert_series_equal(ind._macd(c)[0], st._macd(c)[0])
    pd.testing.assert_series_equal(ind._macd(c)[1], st._macd(c)[1])
    pd.testing.assert_series_equal(ind._sma(c, 200), st._sma(c, 200))
    pd.testing.assert_series_equal(ind._adx(h, lo, c, 14), st._adx(h, lo, c, 14))
    pd.testing.assert_series_equal(ind._atr(h, lo, c), wst._atr(h, lo, c))
    pd.testing.assert_series_equal(ind._rsi(c), wst._rsi(c))


def test_rsi_last_is_ai_analysts_scalar_rsi():
    c = _ohlcv(3)["Close"]
    assert math.isclose(ind.rsi_last(c), float(ind._rsi(c, 14).iloc[-1]))
    assert ind.rsi_last(c.iloc[:10]) is None


# -- ST tests/test_indicators_nan.py, ported ---------------------------------

def _bars(symbol, closes):
    n = len(closes)
    return pd.DataFrame({
        "symbol": [symbol] * n,
        "date": pd.date_range("2024-01-01", periods=n),
        "close": [float(c) for c in closes],
        "high": [(float(c) + 1.0) if not pd.isna(c) else c for c in closes],
        "low": [(float(c) - 1.0) if not pd.isna(c) else c for c in closes],
        "volume": [1_000_000] * n,
    })


def test_trailing_nan_close_falls_back_to_last_completed_bar():
    closes = [100.0 + i for i in range(250)] + [np.nan]
    out = ind.get_latest_indicators(_bars("AMGN", closes))
    assert out["AMGN"]["close"] == 349.0
    assert not pd.isna(out["AMGN"]["close"])


def test_symbol_with_no_usable_close_is_skipped():
    out = ind.get_latest_indicators(_bars("ZZZ", [np.nan, np.nan, np.nan]))
    assert "ZZZ" not in out


def test_mixed_symbols_only_drops_the_unusable_one():
    df = pd.concat([_bars("GOOD", [100.0 + i for i in range(250)]),
                    _bars("BAD", [np.nan, np.nan])], ignore_index=True)
    out = ind.get_latest_indicators(df)
    assert "GOOD" in out and "BAD" not in out
    assert out["GOOD"]["close"] == 349.0


def test_bars_to_long_frame_shape():
    long = ind.bars_to_long_frame({"AAA": _ohlcv(1, n=5)})
    assert {"open", "high", "low", "close", "volume", "symbol", "date"} <= set(long.columns)
    assert list(long["symbol"].unique()) == ["AAA"]
    assert ind.bars_to_long_frame({}).empty
