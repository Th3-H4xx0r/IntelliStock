"""backtest_prices_cursor.latest_price_at must return EXACTLY what the naive
full-scan in _get_prices_at_time returns (latest bar close <= current_utc,
non-daily), but with a per-symbol monotonic cursor so it's O(new_bars)/call
instead of O(bars-so-far). Self-heals on time rewind and bar-list growth."""

import datetime

import backtest_prices_cursor as C


def _btd(t):
    if t is None:
        return None
    try:
        return datetime.datetime.fromisoformat(t.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _naive(data, syms, cutoff):
    out = {}
    for s in syms:
        best = None
        for b in data.get(s) or []:
            bt = _btd(b.get("t"))
            if bt is None:
                continue
            if bt <= cutoff:
                best = b
            else:
                break
        if best is not None and "c" in best:
            out[s] = float(best["c"])
    return out


def test_cursor_matches_naive_across_steps_and_rewind():
    bars = [{"t": f"2026-04-13T{h:02d}:00:00Z", "c": 100.0 + h} for h in range(10)]
    data = {"BTC/USD": bars}
    C.invalidate()
    for h in range(10):
        cut = datetime.datetime(2026, 4, 13, h, 30, 0)   # between bars
        got = C.latest_price_at(data, ["BTC/USD"], cut, bar_time_to_datetime=_btd)
        assert got == _naive(data, ["BTC/USD"], cut), (h, got)
    # rewind to an earlier time -> must self-heal and still match
    cut = datetime.datetime(2026, 4, 13, 2, 30, 0)
    assert C.latest_price_at(data, ["BTC/USD"], cut, bar_time_to_datetime=_btd) == \
        _naive(data, ["BTC/USD"], cut)


def test_cursor_handles_growing_bars_and_malformed():
    C.invalidate()
    data = {"X/USD": [{"t": "2026-04-13T00:00:00Z", "c": 1.0}]}
    cut0 = datetime.datetime(2026, 4, 13, 0, 0, 0)
    assert C.latest_price_at(data, ["X/USD"], cut0, bar_time_to_datetime=_btd) == {"X/USD": 1.0}
    # pagination appends more bars, including a malformed one
    data["X/USD"].append({"t": "bad", "c": 9.0})
    data["X/USD"].append({"t": "2026-04-13T01:00:00Z", "c": 2.0})
    cut1 = datetime.datetime(2026, 4, 13, 1, 0, 0)
    assert C.latest_price_at(data, ["X/USD"], cut1, bar_time_to_datetime=_btd) == {"X/USD": 2.0}


def test_before_first_bar_returns_empty():
    C.invalidate()
    data = {"X/USD": [{"t": "2026-04-13T05:00:00Z", "c": 1.0}]}
    cut = datetime.datetime(2026, 4, 13, 0, 0, 0)
    assert C.latest_price_at(data, ["X/USD"], cut, bar_time_to_datetime=_btd) == {}
