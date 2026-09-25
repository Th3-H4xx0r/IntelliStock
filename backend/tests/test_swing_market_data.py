"""market_data.py (ST tests/test_market_data.py, ported) plus the live price
fallback (app.py:_fetch_live_price) and the backtest frame builder."""
import os
import sys
import types
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import market_data  # noqa: E402


class FakeBar:
    def __init__(self, ts, o=10.0, h=11.0, lo=9.0, c=10.5, v=1000):
        self.timestamp = ts
        self.open, self.high, self.low, self.close, self.volume = o, h, lo, c, v


class FakeBarSet:
    def __init__(self, data):
        self.data = data


class FakeTrade:
    def __init__(self, price, ts):
        self.price = price
        self.timestamp = ts


def _bars(n, start_close=100.0):
    base = datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc)
    return [FakeBar(base + timedelta(days=i), c=start_close + i) for i in range(n)]


class FakeClient:
    def __init__(self, responses=None, error=None):
        self.calls = []
        self.responses = responses or []
        self.error = error

    def get_stock_bars(self, req):
        self.calls.append(req)
        if self.error is not None:
            raise self.error
        idx = len(self.calls) - 1
        if idx < len(self.responses):
            return self.responses[idx]
        return FakeBarSet({})

    def get_stock_latest_trade(self, req):
        if self.error is not None:
            raise self.error
        return self.responses[0]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(market_data.time, "sleep", lambda s: None)


def test_batch_splitting():
    symbols = [f"SYM{i}" for i in range(250)]
    client = FakeClient(responses=[
        FakeBarSet({s: _bars(2) for s in symbols[0:100]}),
        FakeBarSet({s: _bars(2) for s in symbols[100:200]}),
        FakeBarSet({s: _bars(2) for s in symbols[200:250]}),
    ])
    result = market_data.get_daily_bars(symbols, days=10, client=client)
    assert [len(c.symbol_or_symbols) for c in client.calls] == [100, 100, 50]
    assert len(result) == 250


def test_request_params():
    client = FakeClient(responses=[FakeBarSet({"SPY": _bars(3)})])
    market_data.get_daily_bars(["SPY"], days=300, client=client)
    req = client.calls[0]
    assert req.feed == market_data.DataFeed.SIP
    assert req.adjustment == market_data.Adjustment.ALL
    assert (req.timeframe.amount_value, req.timeframe.unit_value) == \
           (market_data.TimeFrame.Day.amount_value, market_data.TimeFrame.Day.unit_value)
    expected_start = datetime.now(timezone.utc) - timedelta(days=300)
    req_start = req.start if req.start.tzinfo else req.start.replace(tzinfo=timezone.utc)
    assert abs((req_start - expected_start).total_seconds()) < 60


def test_column_and_index_shape():
    client = FakeClient(responses=[FakeBarSet({"AAPL": _bars(5)})])
    df = market_data.get_daily_bars(["AAPL"], days=10, client=client)["AAPL"]
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(df.index, pd.DatetimeIndex) and df.index.tz is None
    assert df.index[0] == pd.Timestamp("2026-07-01")
    assert (df.index == df.index.normalize()).all()
    assert df.index.is_monotonic_increasing
    assert df["Close"].iloc[-1] == 104.0


def test_empty_response_and_empty_symbol_list():
    assert market_data.get_daily_bars(
        ["ZZZZ"], days=10, client=FakeClient(responses=[FakeBarSet({})])) == {}
    client = FakeClient()
    assert market_data.get_daily_bars([], days=10, client=client) == {}
    assert client.calls == []


def test_partial_and_empty_symbol_responses_are_absent():
    client = FakeClient(responses=[FakeBarSet({"AAPL": _bars(3), "EMPTY": []})])
    result = market_data.get_daily_bars(["AAPL", "MISSING", "EMPTY"], days=10, client=client)
    assert set(result) == {"AAPL"}


def test_batch_failure_continues():
    calls = {"n": 0}

    class Flaky(FakeClient):
        def get_stock_bars(self, req):
            self.calls.append(req)
            calls["n"] += 1
            if req.symbol_or_symbols[0] == "BAD0":
                raise RuntimeError("boom")
            return FakeBarSet({s: _bars(2) for s in req.symbol_or_symbols})

    bad = [f"BAD{i}" for i in range(100)]
    good = [f"GOOD{i}" for i in range(50)]
    result = market_data.get_daily_bars(bad + good, days=10, client=Flaky())
    assert calls["n"] == market_data.RETRY_ATTEMPTS + 1
    assert len(result) == 50 and all(s.startswith("GOOD") for s in result)


def test_retry_then_success():
    class OnceFlaky(FakeClient):
        def get_stock_bars(self, req):
            self.calls.append(req)
            if len(self.calls) == 1:
                raise RuntimeError("transient")
            return FakeBarSet({"SPY": _bars(2)})

    client = OnceFlaky()
    assert "SPY" in market_data.get_daily_bars(["SPY"], days=10, client=client)
    assert len(client.calls) == 2


def test_large_single_symbol_history_passthrough():
    client = FakeClient(responses=[FakeBarSet({"SPY": _bars(1300)})])
    assert len(market_data.get_daily_bars(["SPY"], days=2000, client=client)["SPY"]) == 1300


def test_latest_price_success_failure_zero_and_missing():
    ts = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)
    ok = FakeClient(responses=[{"AAPL": FakeTrade(212.34, ts)}])
    assert market_data.get_latest_price("AAPL", client=ok) == 212.34
    assert market_data.get_latest_trade("AAPL", client=ok) == (212.34, ts)
    down = FakeClient(error=RuntimeError("api down"))
    assert market_data.get_latest_price("AAPL", client=down) is None
    assert market_data.get_latest_trade("AAPL", client=down) == (None, None)
    zero = FakeClient(responses=[{"AAPL": FakeTrade(0.0, ts)}])
    assert market_data.get_latest_price("AAPL", client=zero) is None
    assert market_data.get_latest_price("AAPL", client=FakeClient(responses=[{}])) is None


def test_dash_symbols_translated_and_mapped_back():
    client = FakeClient(responses=[FakeBarSet({"BRK.B": _bars(3), "AAPL": _bars(3)})])
    result = market_data.get_daily_bars(["BRK-B", "AAPL"], days=10, client=client)
    assert client.calls[0].symbol_or_symbols == ["BRK.B", "AAPL"]
    assert set(result) == {"BRK-B", "AAPL"}
    ts = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)
    trade = FakeClient(responses=[{"BRK.B": FakeTrade(480.0, ts)}])
    assert market_data.get_latest_price("BRK-B", client=trade) == 480.0


def test_data_client_refuses_missing_credentials():
    with pytest.raises(RuntimeError):
        market_data.data_client("", "secret")


# -- app.py:_fetch_live_price -------------------------------------------------

def _yf_price(price):
    return types.SimpleNamespace(Ticker=lambda s: types.SimpleNamespace(
        fast_info={"last_price": price}))


def test_a_stale_iex_print_in_market_hours_falls_back_to_yfinance(monkeypatch):
    monkeypatch.setattr(market_data, "yf", _yf_price(99.0))
    now = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)      # 11:00 ET
    stale = FakeClient(responses=[{"AAPL": FakeTrade(210.0, now - timedelta(minutes=20))}])
    assert market_data.fetch_live_price("AAPL", client=stale, now=now) == 99.0
    fresh = FakeClient(responses=[{"AAPL": FakeTrade(210.0, now - timedelta(minutes=2))}])
    assert market_data.fetch_live_price("AAPL", client=fresh, now=now) == 210.0
    night = datetime(2026, 7, 7, 23, 0, tzinfo=timezone.utc)    # 19:00 ET
    old = FakeClient(responses=[{"AAPL": FakeTrade(210.0, night - timedelta(hours=3))}])
    assert market_data.fetch_live_price("AAPL", client=old, now=night) == 210.0


def test_live_prices_prefers_the_adapter_and_falls_back(monkeypatch):
    monkeypatch.setattr(market_data, "yf", _yf_price(50.0))
    now = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)

    class Adapter:
        def get_latest_trades(self, symbols):
            return {"AAA": (101.0, (now - timedelta(minutes=1)).isoformat()),
                    "BBB": (7.0, (now - timedelta(hours=1)).isoformat())}

    out = market_data.live_prices(["AAA", "BBB", "CCC"], adapter=Adapter(), now=now)
    assert out == {"AAA": 101.0, "BBB": 50.0, "CCC": 50.0}

    class Broken:
        def get_latest_trades(self, symbols):
            raise NotImplementedError

    assert market_data.live_prices(["AAA"], adapter=Broken(), now=now) == {"AAA": 50.0}


# -- fix (G2-I1): a naive clock or an unparseable trade time is never fresh ---

def test_a_naive_now_is_read_as_utc_and_still_judges_staleness():
    now_naive = datetime(2026, 7, 7, 15, 0)                     # 11:00 ET as naive UTC
    old = datetime(2026, 7, 7, 14, 40, tzinfo=timezone.utc)     # 20 min before
    fresh = datetime(2026, 7, 7, 14, 58, tzinfo=timezone.utc)   # 2 min before
    assert market_data._is_stale(old, now_naive) is True
    assert market_data._is_stale(fresh, now_naive) is False
    assert market_data.fresh_trade_price(210.0, old.isoformat(), now=now_naive) is None
    assert market_data.fresh_trade_price(210.0, fresh.isoformat(), now=now_naive) == 210.0


def test_an_unparseable_or_missing_trade_time_counts_as_stale():
    in_hours = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)   # 11:00 ET
    at_night = datetime(2026, 7, 7, 23, 0, tzinfo=timezone.utc)   # 19:00 ET
    for now in (in_hours, at_night):
        assert market_data._is_stale("garbage", now) is True
        assert market_data._is_stale(None, now) is True
        assert market_data.fresh_trade_price(210.0, "garbage", now=now) is None
        assert market_data.fresh_trade_price(210.0, None, now=now) is None


def test_an_iex_print_without_a_time_falls_back_to_yfinance(monkeypatch):
    monkeypatch.setattr(market_data, "yf", _yf_price(99.0))
    now = datetime(2026, 7, 7, 15, 0, tzinfo=timezone.utc)
    blank = FakeClient(responses=[{"AAPL": FakeTrade(210.0, None)}])
    assert market_data.fetch_live_price("AAPL", client=blank, now=now) == 99.0


# -- engine bars -> ST frames (backtest) --------------------------------------

def _daily(day, c, o=None, h=None, lo=None, v=1000.0):
    return {"t": f"{day}T04:00:00Z", "o": o or c, "h": h or c + 1, "l": lo or c - 1,
            "c": c, "v": v}


def test_daily_engine_bars_stop_strictly_before_the_ny_date():
    data = {"AAA": {"bars": [_daily("2026-05-28", 10.0), _daily("2026-05-29", 11.0),
                             _daily("2026-06-01", 12.0)]}}
    as_of = datetime(2026, 6, 1, 13, 20, tzinfo=timezone.utc)
    df = market_data.frames_from_engine_bars(data, ["AAA"], as_of)["AAA"]
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert df.index[-1] == pd.Timestamp("2026-05-29") and df["Close"].iloc[-1] == 11.0
    assert df.index.tz is None


def test_intraday_engine_bars_roll_up_by_ny_session():
    bars = [
        {"t": "2026-05-29T13:30:00Z", "o": 10.0, "h": 10.5, "l": 9.8, "c": 10.2, "v": 100},
        {"t": "2026-05-29T19:45:00Z", "o": 10.2, "h": 11.0, "l": 10.1, "c": 10.9, "v": 300},
        {"t": "2026-06-01T13:30:00Z", "o": 11.0, "h": 11.5, "l": 10.9, "c": 11.2, "v": 50},
    ]
    as_of = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
    df = market_data.frames_from_engine_bars({"AAA": bars}, ["AAA"], as_of)["AAA"]
    assert len(df) == 1
    row = df.iloc[0]
    assert (row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]) == (
        10.0, 11.0, 9.8, 10.9, 400.0)


def test_engine_frames_keep_a_nan_close_and_trim_to_the_live_window():
    old = (datetime(2026, 6, 1) - timedelta(days=500)).date().isoformat()
    data = {"AAA": [_daily(old, 5.0), _daily("2026-05-28", 10.0),
                    {"t": "2026-05-29T04:00:00Z", "o": 11, "h": 12, "l": 10,
                     "c": None, "v": 1}]}
    as_of = datetime(2026, 6, 1, 13, 20, tzinfo=timezone.utc)
    df = market_data.frames_from_engine_bars(data, ["AAA", "NOBARS"], as_of)
    assert set(df) == {"AAA"}
    assert len(df["AAA"]) == 2 and pd.isna(df["AAA"]["Close"].iloc[-1])
