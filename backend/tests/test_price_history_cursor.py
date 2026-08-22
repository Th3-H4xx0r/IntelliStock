"""Tests for the 2026-05-08 backtest perf optimization.

`get_price_history_up_to_current` was rebuilt fresh on every backtest
bar — for a 60-symbol × 5000-bar/symbol backtest at 1200s granularity,
that's ~300k iterations × ~3000 bars = ~900M pure dict-lookup ops.

The fix uses a monotonic cursor per symbol: since backtest time only
advances forward, we keep an index of where the previous call ended
and advance it only as far as the new current_time requires.

Cursor state + logic live in `backtest_price_history` so they can be
imported without dragging in broker.py's argparse + main-path side
effects.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import backtest_price_history as bph  # noqa: E402


def _bar_time_to_datetime(t):
    """Test stub mirroring broker.py's helper for ISO-string bars."""
    if not t:
        return None
    if hasattr(t, "tzinfo"):
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    try:
        s = str(t).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _current_time_to_utc(t):
    if t is None:
        return None
    return _bar_time_to_datetime(t)


def _make_bar(ts_iso: str, close: float = 100.0) -> dict:
    return {"t": ts_iso, "o": close, "h": close, "l": close, "c": close, "v": 1000}


def _build_data(start_iso: str, count: int, sym: str = "AAPL") -> dict:
    """Build a synthetic dict of {sym: [bars]} with hourly bars from start."""
    start = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    bars = []
    for i in range(count):
        t = start + timedelta(hours=i)
        bars.append(_make_bar(t.isoformat(), close=100.0 + i))
    return {sym: bars}


def _call(data, symbols, current_time, daily_mode=False):
    def _available(bar):
        timestamp = _bar_time_to_datetime(bar.get("t"))
        if timestamp is None:
            return None
        if daily_mode:
            return datetime.combine(
                timestamp.date() + timedelta(days=1),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
        return timestamp

    return bph.get_price_history_up_to_current(
        data, symbols, current_time,
        bar_time_to_datetime=_bar_time_to_datetime,
        current_time_to_utc=_current_time_to_utc,
        bar_available_at=_available,
    )


@pytest.fixture(autouse=True)
def _reset_cursor():
    """Each test starts with a fresh cursor cache."""
    bph.invalidate_cursor()
    yield


def test_returns_empty_dict_on_no_symbols():
    out = _call({}, [], "2026-01-01T10:00:00+00:00")
    assert out == {}


def test_returns_empty_dict_on_none_current_time():
    data = _build_data("2026-01-01T00:00:00+00:00", 10)
    out = _call(data, ["AAPL"], None)
    assert out == {"AAPL": []}


def test_single_call_returns_correct_prefix():
    data = _build_data("2026-01-01T00:00:00+00:00", 24)
    out = _call(data, ["AAPL"], "2026-01-01T05:00:00+00:00")
    assert len(out["AAPL"]) == 6
    assert out["AAPL"][0]["t"].startswith("2026-01-01T00:00:00")
    assert out["AAPL"][-1]["t"].startswith("2026-01-01T05:00:00")


def test_monotonic_advance_extends_slice():
    data = _build_data("2026-01-01T00:00:00+00:00", 24)
    out1 = _call(data, ["AAPL"], "2026-01-01T05:00:00+00:00")
    assert len(out1["AAPL"]) == 6
    out2 = _call(data, ["AAPL"], "2026-01-01T08:00:00+00:00")
    assert len(out2["AAPL"]) == 9
    assert out2["AAPL"][-1]["t"].startswith("2026-01-01T08:00:00")
    out3 = _call(data, ["AAPL"], "2026-01-02T00:00:00+00:00")
    assert len(out3["AAPL"]) == 24


def test_repeated_call_same_time_no_advance():
    data = _build_data("2026-01-01T00:00:00+00:00", 24)
    out1 = _call(data, ["AAPL"], "2026-01-01T05:00:00+00:00")
    out2 = _call(data, ["AAPL"], "2026-01-01T05:00:00+00:00")
    assert len(out2["AAPL"]) == len(out1["AAPL"]) == 6


def test_rewind_invalidates_cursor():
    data = _build_data("2026-01-01T00:00:00+00:00", 24)
    out1 = _call(data, ["AAPL"], "2026-01-01T20:00:00+00:00")
    assert len(out1["AAPL"]) == 21
    out2 = _call(data, ["AAPL"], "2026-01-01T05:00:00+00:00")
    assert len(out2["AAPL"]) == 6
    assert out2["AAPL"][-1]["t"].startswith("2026-01-01T05:00:00")


def test_new_symbol_mid_backtest_starts_at_zero():
    data = _build_data("2026-01-01T00:00:00+00:00", 24, sym="AAPL")
    _call(data, ["AAPL"], "2026-01-01T10:00:00+00:00")
    msft = _build_data("2026-01-01T00:00:00+00:00", 24, sym="MSFT")
    data["MSFT"] = msft["MSFT"]
    out = _call(data, ["AAPL", "MSFT"], "2026-01-01T10:00:00+00:00")
    assert len(out["AAPL"]) == 11
    assert len(out["MSFT"]) == 11


def test_equivalence_with_linear_scan_reference():
    """Cursor result must match a from-scratch linear scan over a
    series of monotonic time advances. This is the load-bearing
    behavioral test — if the cursor ever drifts, this catches it.
    """
    data = _build_data("2026-01-01T00:00:00+00:00", 100)

    def _linear_scan(d, symbols, current_iso):
        cur = _bar_time_to_datetime(current_iso)
        out = {}
        for s in symbols:
            past = []
            for b in d.get(s, []):
                bt = _bar_time_to_datetime(b.get("t"))
                if bt <= cur:
                    past.append(b)
                else:
                    break
            out[s] = past
        return out

    times = [
        "2026-01-01T05:00:00+00:00",
        "2026-01-01T10:00:00+00:00",
        "2026-01-01T15:00:00+00:00",
        "2026-01-02T00:00:00+00:00",
        "2026-01-04T00:00:00+00:00",
    ]
    for t_iso in times:
        cursor_out = _call(data, ["AAPL"], t_iso)
        linear_out = _linear_scan(data, ["AAPL"], t_iso)
        assert len(cursor_out["AAPL"]) == len(linear_out["AAPL"]), (
            f"mismatch at {t_iso}: cursor={len(cursor_out['AAPL'])} "
            f"linear={len(linear_out['AAPL'])}"
        )
        if cursor_out["AAPL"]:
            assert cursor_out["AAPL"][0]["t"] == linear_out["AAPL"][0]["t"]
            assert cursor_out["AAPL"][-1]["t"] == linear_out["AAPL"][-1]["t"]


def test_daily_mode_excludes_same_day_bars():
    """Daily-mode same-day-leakage guard must still apply: bars from
    today's date are excluded even if their timestamp <= current_time.
    """
    data = _build_data("2026-01-01T00:00:00+00:00", 48)  # 2 days
    out = _call(
        data, ["AAPL"], "2026-01-02T10:00:00+00:00",
        daily_mode=True,
    )
    # Daily mode: only bars with date < 2026-01-02 should appear.
    for b in out["AAPL"]:
        bt = _bar_time_to_datetime(b["t"])
        assert bt.date() < datetime(2026, 1, 2).date()
    assert len(out["AAPL"]) == 24  # exactly the first day


def test_malformed_bars_skipped_not_appended():
    """Bars whose `t` is unparseable should be skipped silently
    (matches original semantics — `continue` after None check).
    """
    data = {
        "AAPL": [
            _make_bar("2026-01-01T00:00:00+00:00", 100),
            {"t": "garbage", "c": 999},  # malformed
            _make_bar("2026-01-01T01:00:00+00:00", 101),
            _make_bar("2026-01-01T02:00:00+00:00", 102),
        ]
    }
    out = _call(data, ["AAPL"], "2026-01-01T03:00:00+00:00")
    closes = [b["c"] for b in out["AAPL"]]
    assert 999 not in closes  # malformed skipped
    assert closes == [100, 101, 102]


def test_cursor_is_materially_faster_than_rescan():
    """The cursor path must cost materially less PER CALL than the
    from-scratch rescan it replaced.

    Measured as a RATIO of two timings taken on the same machine, in the
    same process, moments apart — not against a fixed wall-clock budget.
    An absolute threshold measures how busy the host is: this file's own
    workload runs in ~0.9s idle, so a machine under parallel load fails a
    2s budget while the cursor is working perfectly. The ratio cancels
    machine speed out, and it is the property the test's name claims.

    The rescan reference is the same function with the cursor dropped
    before every call, which is exactly the pre-2026-05-08 behaviour:
    invalidate_cursor() clears both the cursor and the parsed-bar cache,
    so each call re-scans every bar of every symbol from index 0.
    """
    data = {}
    for i in range(50):
        sym = f"SYM{i:03d}"
        data[sym] = _build_data("2025-01-01T00:00:00+00:00", 5000, sym)[sym]
    symbols = list(data.keys())
    base = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def _time_at(i):
        return (base + timedelta(hours=i * 25)).isoformat()

    cursor_calls = 200
    bph.invalidate_cursor()
    t_start = time.perf_counter()
    for i in range(cursor_calls):
        _call(data, symbols, _time_at(i))
    cursor_per_call = (time.perf_counter() - t_start) / cursor_calls

    # A rescan call is ~2 orders of magnitude dearer, so a handful is a
    # sufficient sample and keeps the test itself quick.
    rescan_calls = 5
    t_start = time.perf_counter()
    for i in range(rescan_calls):
        bph.invalidate_cursor()
        _call(data, symbols, _time_at(i * (cursor_calls // rescan_calls)))
    rescan_per_call = (time.perf_counter() - t_start) / rescan_calls

    assert cursor_per_call * 10 < rescan_per_call, (
        f"Cursor path costs {cursor_per_call * 1e3:.2f}ms/call vs "
        f"{rescan_per_call * 1e3:.2f}ms/call for a full rescan "
        f"({rescan_per_call / cursor_per_call:.1f}x) — the monotonic cursor "
        f"looks disabled or defeated."
    )


def test_invalidate_clears_cursor():
    data = _build_data("2026-01-01T00:00:00+00:00", 24)
    _call(data, ["AAPL"], "2026-01-01T10:00:00+00:00")
    assert "AAPL" in bph.CACHE["cursors"]
    bph.invalidate_cursor()
    assert bph.CACHE["cursors"] == {}
    assert bph.CACHE["last_current_utc"] is None


def test_intraday_history_waits_for_bar_close():
    data = {
        "SPY": [_make_bar("2026-03-02T14:00:00+00:00", 123.0)],
    }

    def _hour_available(bar):
        return _bar_time_to_datetime(bar["t"]) + timedelta(hours=1)

    before_close = bph.get_price_history_up_to_current(
        data,
        ["SPY"],
        "2026-03-02T14:59:59+00:00",
        bar_time_to_datetime=_bar_time_to_datetime,
        current_time_to_utc=_current_time_to_utc,
        bar_available_at=_hour_available,
    )
    at_close = bph.get_price_history_up_to_current(
        data,
        ["SPY"],
        "2026-03-02T15:00:00+00:00",
        bar_time_to_datetime=_bar_time_to_datetime,
        current_time_to_utc=_current_time_to_utc,
        bar_available_at=_hour_available,
    )

    assert before_close == {"SPY": []}
    assert at_close == {"SPY": data["SPY"]}


def test_daily_history_becomes_visible_at_session_close():
    data = {
        "SPY": [_make_bar("2026-03-02T00:00:00+00:00", 123.0)],
    }

    def _session_close(_bar):
        return datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc)

    before_close = bph.get_price_history_up_to_current(
        data,
        ["SPY"],
        "2026-03-02T20:59:59+00:00",
        bar_time_to_datetime=_bar_time_to_datetime,
        current_time_to_utc=_current_time_to_utc,
        bar_available_at=_session_close,
    )
    at_close = bph.get_price_history_up_to_current(
        data,
        ["SPY"],
        "2026-03-02T21:00:00+00:00",
        bar_time_to_datetime=_bar_time_to_datetime,
        current_time_to_utc=_current_time_to_utc,
        bar_available_at=_session_close,
    )

    assert before_close == {"SPY": []}
    assert at_close == {"SPY": data["SPY"]}
