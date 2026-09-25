"""The swing lane's own daily bars (swing_trader.backtest_bars).

The request is pinned against the engine's own loader, AST-extracted from
broker.py, so the lane's signals and the engine's fills price off the same
bars. No network: the HTTP call and the bars cache are injected.
"""
from __future__ import annotations

import ast
import os
import sys
from datetime import date, datetime

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)
_tests = os.path.dirname(os.path.abspath(__file__))
if _tests not in sys.path:
    sys.path.insert(0, _tests)

import swing_broker_harness as harness  # noqa: E402
from swing_trader import backtest_bars  # noqa: E402


class Response:
    def __init__(self, body=None, status=200, headers=None):
        self.body, self.status_code, self.headers = body or {}, status, headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.body


class Http:
    """Answers each request from `script` (a list, consumed in order) and
    records (url, headers, params)."""

    def __init__(self, script=None, default=None):
        self.script = list(script or [])
        self.default = default
        self.calls = []

    def __call__(self, url, *, headers, params, timeout):
        self.calls.append((url, dict(headers), dict(params)))
        if self.script:
            return self.script.pop(0)
        return self.default or Response({"bars": []})


class Logs(list):
    def __call__(self, message, color="white"):
        self.append((color, str(message)))


def bar(day, c=10.0):
    return {"t": f"{day}T05:00:00Z", "o": c, "h": c, "l": c, "c": c, "v": 100}


def fetch(symbols, start, end, http, **kw):
    kw.setdefault("log", Logs())
    return backtest_bars.fetch_daily_bars(symbols, start, end, key="k", secret="s",
                                          feed=kw.pop("feed", "sip"), http_get=http,
                                          cached=False, sleep=lambda s: None, **kw)


# -- the request is the engine loader's --------------------------------------

def test_the_request_matches_the_engine_loader():
    ns = harness.extract(["_historical_bars_request_params",
                          "_alpaca_chunk_days_for_timeframe"])
    http = Http()
    fetch(["AAPL"], date(2025, 1, 2), date(2025, 3, 31), http)
    [(url, headers, params)] = http.calls
    assert url == f"{harness.module_assign('ALPACA_DATA_BASE')}/stocks/AAPL/bars"
    assert params == ns["_historical_bars_request_params"](
        is_crypto=False, symbol="AAPL", start_iso="2025-01-02T00:00:00Z",
        end_iso="2025-03-31T23:59:59Z", timeframe="1Day", feed="sip",
        adjustment="split")
    assert headers == {"APCA-API-KEY-ID": "k", "APCA-API-SECRET-KEY": "s",
                       "accept": "application/json"}
    assert backtest_bars.CHUNK_DAYS == ns["_alpaca_chunk_days_for_timeframe"]("1Day")


def test_the_adjustment_is_the_engine_loaders_default():
    fn = next(n for n in harness.tree().body if isinstance(n, ast.FunctionDef)
              and n.name == "fetch_alpaca_historical_bars")
    defaults = dict(zip([a.arg for a in fn.args.args][-len(fn.args.defaults):],
                        fn.args.defaults))
    assert ast.literal_eval(defaults["adjustment"]) == backtest_bars.ADJUSTMENT == "split"


def test_the_window_is_cut_into_365_day_chunks():
    http = Http()
    fetch(["AAPL"], date(2023, 1, 2), date(2025, 1, 10), http)
    windows = [(p["start"], p["end"]) for _u, _h, p in http.calls]
    assert windows == [("2023-01-02T00:00:00Z", "2024-01-02T00:00:00Z"),
                       ("2024-01-02T00:00:00Z", "2025-01-01T00:00:00Z"),
                       ("2025-01-01T00:00:00Z", "2025-01-10T23:59:59Z")]


def test_pages_are_followed_and_bars_sorted_and_deduplicated():
    http = Http([Response({"bars": [bar("2025-01-03"), bar("2025-01-02")],
                           "next_page_token": "p2"}),
                 Response({"bars": [bar("2025-01-03"), bar("2025-01-06")]})])
    got = fetch(["AAPL"], date(2025, 1, 2), date(2025, 1, 31), http)
    assert [b["t"][:10] for b in got["AAPL"]] == ["2025-01-02", "2025-01-03", "2025-01-06"]
    second = http.calls[1][2]
    assert second["page_token"] == "p2" and "start" not in second and "end" not in second


def test_a_429_is_retried_after_its_retry_after():
    waits = []
    http = Http([Response(status=429, headers={"Retry-After": "3"}),
                 Response({"bars": [bar("2025-01-02")]})])
    got = backtest_bars.fetch_daily_bars(["AAPL"], date(2025, 1, 2), date(2025, 1, 3),
                                         key="k", secret="s", feed="iex", http_get=http,
                                         cached=False, sleep=waits.append, log=Logs())
    assert waits == [3.0] and len(got["AAPL"]) == 1


def test_a_failed_chunk_is_logged_and_skipped():
    logs = Logs()
    http = Http([Response(status=500), Response({"bars": [bar("2025-01-02")]})])
    got = fetch(["AAA", "BBB"], date(2025, 1, 2), date(2025, 1, 3), http, log=logs)
    assert list(got) == ["BBB"]
    assert any(c == "yellow" and "AAA" in m for c, m in logs)


def test_no_credentials_fetches_nothing():
    logs, http = Logs(), Http()
    got = backtest_bars.fetch_daily_bars(["AAPL"], date(2025, 1, 2), date(2025, 1, 3),
                                         key="", secret="", feed="iex", http_get=http,
                                         log=logs)
    assert got == {} and http.calls == []
    assert any(c == "yellow" and "credentials" in m for c, m in logs)


def test_chunks_go_through_the_engines_bars_cache(monkeypatch):
    import price_utils
    seen = []

    def cached(conn, sym, a, b, timeframe, feed, fetch_fn, adjustment="raw"):
        seen.append((conn is not None, sym, a, b, timeframe, feed, adjustment))
        return [bar("2025-01-02")], True

    monkeypatch.setattr(price_utils, "get_bars_chunk_cached", cached)
    http = Http()

    class Unreadable:
        def get_all(self, *_a, **_k):
            raise RuntimeError("server7 unreachable")

    # The per-symbol path is the fallback when SwingDailyBars cannot be read.
    got = backtest_bars.fetch_daily_bars(["AAPL"], date(2025, 1, 2), date(2025, 1, 3),
                                         key="k", secret="s", feed="sip", http_get=http,
                                         log=Logs(), store=Unreadable())
    assert http.calls == [] and len(got["AAPL"]) == 1
    assert seen == [(True, "AAPL", datetime(2025, 1, 2), datetime(2025, 1, 3, 23, 59, 59),
                     "1Day", "sip", "split")]


# -- no look-ahead, and the engine's bars win -----------------------------------------

def test_only_sessions_strictly_before_the_decision_are_handed_out():
    own = backtest_bars.OwnBars({"AAA": [bar(d) for d in (
        "2025-01-02", "2025-01-03", "2025-01-06", "2025-01-07", "2025-01-08")]})
    got = own.before("AAA", "2025-01-07", 4)
    assert [b["t"][:10] for b in got] == ["2025-01-03", "2025-01-06"]
    assert own.before("AAA", "2025-01-02", 400) == []
    assert own.before("NOPE", "2025-01-07", 400) == []


def test_the_engines_bars_win_and_own_bars_fill_the_rest():
    own = backtest_bars.OwnBars({"AAA": [bar("2025-01-02", 1.0)],
                                 "BBB": [bar("2025-01-02", 2.0)],
                                 "CCC": [bar("2025-01-02", 3.0)]})
    engine = {"AAA": [bar("2025-01-02", 9.0)], "BBB": [], "DDD": {"bars": [bar("2025-01-02")]}}
    view = backtest_bars.merged_view(engine, own, "2025-01-03", 450)
    assert view["AAA"] == engine["AAA"]                      # the engine's, untouched
    assert view["BBB"][0]["c"] == 2.0 and view["CCC"][0]["c"] == 3.0
    assert view["DDD"] is engine["DDD"]
    assert engine["BBB"] == []                               # the engine's dict is not mutated


@pytest.mark.parametrize("entry, expected", [
    ([], False), ([bar("2025-01-02")], True), ({"bars": []}, False),
    ({"bars": [bar("2025-01-02")]}, True), (None, False)])
def test_has_bars_reads_both_engine_shapes(entry, expected):
    assert backtest_bars.has_bars(entry) is expected
