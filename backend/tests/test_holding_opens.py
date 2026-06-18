"""Unit tests for holding_opens.derive_open_dates."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from holding_opens import derive_open_dates  # noqa: E402


def _fill(sym, side, qty, ts):
    return {"symbol": sym, "side": side, "qty": qty, "ts_iso": ts, "ts_sort": ts}


def test_simple_single_buy():
    fills = [_fill("AAPL", "buy", 10, "2026-03-01T15:00:00+00:00")]
    out = derive_open_dates(fills, {"AAPL": 10})
    assert out == {"AAPL": "2026-03-01T15:00:00+00:00"}


def test_accumulated_buys_open_at_first():
    fills = [
        _fill("AAPL", "buy", 4, "2026-03-01T15:00:00+00:00"),
        _fill("AAPL", "buy", 6, "2026-03-05T15:00:00+00:00"),
    ]
    out = derive_open_dates(fills, {"AAPL": 10})
    assert out["AAPL"] == "2026-03-01T15:00:00+00:00"


def test_sell_then_rebuy_uses_latest_episode():
    # Bought, fully sold, then re-bought — open date is the re-buy, not the first.
    fills = [
        _fill("MSFT", "buy", 5, "2026-01-01T15:00:00+00:00"),
        _fill("MSFT", "sell", 5, "2026-02-01T15:00:00+00:00"),
        _fill("MSFT", "buy", 3, "2026-04-10T15:00:00+00:00"),
    ]
    out = derive_open_dates(fills, {"MSFT": 3})
    assert out["MSFT"] == "2026-04-10T15:00:00+00:00"


def test_partial_sell_keeps_original_open():
    # Bought 10, sold 4 (still holding 6) — open date stays the original buy.
    fills = [
        _fill("NVDA", "buy", 10, "2026-02-01T15:00:00+00:00"),
        _fill("NVDA", "sell", 4, "2026-03-01T15:00:00+00:00"),
    ]
    out = derive_open_dates(fills, {"NVDA": 6})
    assert out["NVDA"] == "2026-02-01T15:00:00+00:00"


def test_incomplete_history_is_omitted():
    # Reported qty (10) doesn't match reconstructable fills (only a 3-share buy
    # seen) → the opening fills are outside the window, so we don't guess.
    fills = [_fill("TSLA", "buy", 3, "2026-05-01T15:00:00+00:00")]
    out = derive_open_dates(fills, {"TSLA": 10})
    assert "TSLA" not in out


def test_incomplete_history_approx_uses_episode_start():
    # Same incomplete window, but allow_approx → fall back to the reconstructed
    # current-episode start (we still hold shares).
    fills = [_fill("TSLA", "buy", 3, "2026-05-01T15:00:00+00:00")]
    out = derive_open_dates(fills, {"TSLA": 10}, allow_approx=True)
    assert out["TSLA"] == "2026-05-01T15:00:00+00:00"


def test_approx_uses_earliest_buy_when_episode_unknown():
    # Window misses earlier buys: reconstruction ends flat/negative even though
    # we still hold, so the current episode start is unknown → approx falls back
    # to the earliest buy seen.
    fills = [
        _fill("META", "buy", 3, "2026-04-10T15:00:00+00:00"),
        _fill("META", "sell", 5, "2026-04-20T15:00:00+00:00"),
    ]
    out = derive_open_dates(fills, {"META": 5}, allow_approx=True)
    assert out["META"] == "2026-04-10T15:00:00+00:00"


def test_unordered_fills_are_sorted():
    fills = [
        _fill("AAPL", "buy", 6, "2026-03-05T15:00:00+00:00"),
        _fill("AAPL", "buy", 4, "2026-03-01T15:00:00+00:00"),
    ]
    out = derive_open_dates(fills, {"AAPL": 10})
    assert out["AAPL"] == "2026-03-01T15:00:00+00:00"


def test_only_held_symbols_returned():
    fills = [
        _fill("AAPL", "buy", 10, "2026-03-01T15:00:00+00:00"),
        _fill("GONE", "buy", 5, "2026-03-01T15:00:00+00:00"),
        _fill("GONE", "sell", 5, "2026-03-02T15:00:00+00:00"),
    ]
    out = derive_open_dates(fills, {"AAPL": 10})
    assert out == {"AAPL": "2026-03-01T15:00:00+00:00"}


def test_fractional_qty_within_tolerance():
    fills = [_fill("AAPL", "buy", 10.0, "2026-03-01T15:00:00+00:00")]
    # Broker reports 10.0001 (rounding) — within tolerance, still matched.
    out = derive_open_dates(fills, {"AAPL": 10.0001})
    assert out["AAPL"] == "2026-03-01T15:00:00+00:00"
