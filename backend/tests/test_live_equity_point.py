"""Bug fix (2026-06-11): the live-trading equity chart froze after market close
because the in-memory snapshot list only grows on live ticks. The served
portfolio_history now gets the current account equity appended so it never
goes stale overnight.
"""
from __future__ import annotations


def test_appends_current_equity_when_series_stale():
    from live_state import append_current_equity_point
    hist = [{"ts": "2026-06-11T20:00:00+00:00", "value": 5937.90}]
    out = append_current_equity_point(hist, 5994.38, "2026-06-11T08:00:00+00:00")
    assert len(out) == 2
    assert out[-1] == {"ts": "2026-06-11T08:00:00+00:00", "value": 5994.38}
    # original list not mutated
    assert len(hist) == 1


def test_skips_when_series_already_current():
    from live_state import append_current_equity_point
    hist = [{"ts": "t", "value": 5994.38}]
    out = append_current_equity_point(hist, 5994.38, "now")
    assert len(out) == 1  # no duplicate point


def test_skips_when_equity_nonpositive_or_garbage():
    from live_state import append_current_equity_point
    hist = [{"ts": "t", "value": 100.0}]
    assert append_current_equity_point(hist, 0, "now") == hist
    assert append_current_equity_point(hist, -5, "now") == hist
    assert append_current_equity_point(hist, None, "now") == hist
    assert append_current_equity_point(hist, "x", "now") == hist


def test_appends_to_empty_history():
    from live_state import append_current_equity_point
    out = append_current_equity_point([], 5994.38, "now")
    assert out == [{"ts": "now", "value": 5994.38}]


def test_truncates_to_max():
    from live_state import append_current_equity_point
    hist = [{"ts": str(i), "value": float(i)} for i in range(500)]
    out = append_current_equity_point(hist, 9999.0, "now", max_ph=500)
    assert len(out) == 500
    assert out[-1]["value"] == 9999.0
