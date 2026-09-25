"""The SwingDailyBars cache (swing_trader.backtest_bars): one row per symbol
per year, one batched read, exact gap fill with multi-symbol requests, and a
split check on every overlap. No network: HTTP is a stub serving a fixed
synthetic tape, the store is the FakeStore (or Postgres under PG_TEST_DSN).
"""
from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)
_tests = os.path.dirname(os.path.abspath(__file__))
if _tests not in sys.path:
    sys.path.insert(0, _tests)

import swing_broker_harness as harness  # noqa: E402
from swing_trader import backtest_bars as bb  # noqa: E402

TODAY = date(2026, 9, 25)


def sessions(lo, hi):
    d, out = lo, []
    while d <= hi:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class Tape:
    """A multi-symbol /v2/stocks/bars stub over a deterministic tape.
    `scale[sym]` multiplies every close (a split rewrites history)."""

    def __init__(self, page=None):
        self.calls, self.scale, self.page = [], {}, page

    def bar(self, sym, d):
        c = round((10 + (sum(map(ord, sym)) % 50) + d.toordinal() % 17) * self.scale.get(sym, 1.0), 4)
        return {"t": f"{d.isoformat()}T04:00:00Z", "o": c, "h": c, "l": c, "c": c, "v": 100}

    def __call__(self, url, *, headers, params, timeout):
        self.calls.append((url, dict(params)))
        assert url.endswith("/stocks/bars")
        syms = params["symbols"].split(",")
        lo = date.fromisoformat(params["start"][:10])
        hi = date.fromisoformat(params["end"][:10])
        rows = [(s, d) for s in syms for d in sessions(lo, min(hi, TODAY - timedelta(days=1)))]
        start = int(params.get("page_token") or 0)
        size = self.page or len(rows) or 1
        chunk = rows[start:start + size]
        body = {"bars": {}}
        for s, d in chunk:
            body["bars"].setdefault(s, []).append(self.bar(s, d))
        if start + size < len(rows):
            body["next_page_token"] = str(start + size)
        return Resp(body)


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.headers = body, status, {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.body


class Logs(list):
    def __call__(self, message, color="white"):
        self.append((color, str(message)))

    def text(self, color=None):
        return "\n".join(m for c, m in self if color in (None, c))


def load(store, http, symbols, lo, hi, log=None):
    return bb.fetch_daily_bars(symbols, lo, hi, key="k", secret="s", feed="sip",
                               http_get=http, sleep=lambda s: None,
                               log=log if log is not None else Logs(),
                               store=store, today=TODAY)


SYMS = ["AAA", "BBB", "CCC"]


def test_a_cold_fill_writes_one_row_per_symbol_per_year(store):
    tape = Tape()
    got = load(store, tape, SYMS, date(2024, 6, 3), date(2025, 3, 31))
    assert set(got) == set(SYMS)
    assert [b["t"][:10] for b in got["AAA"]] == [d.isoformat() for d in
                                                   sessions(date(2024, 6, 3), date(2025, 3, 31))]
    row = store.get("SwingDailyBars", "AAA|2024")
    assert (row["symbol"], row["year"], row["feed"], row["adjustment"]) == (
        "AAA", 2024, "sip", "split")
    assert (row["covered_from"], row["covered_through"]) == ("2024-06-03", "2024-12-31")
    assert store.get("SwingDailyBars", "AAA|2025")["covered_through"] == "2025-03-31"
    assert len(tape.calls) == 1 and tape.calls[0][1]["symbols"] == "AAA,BBB,CCC"


def test_a_warm_run_makes_no_request_and_returns_the_same_bars(store):
    lo, hi = date(2024, 6, 3), date(2025, 3, 31)
    first = load(store, Tape(), SYMS, lo, hi)
    tape, logs = Tape(), Logs()
    assert load(store, tape, SYMS, lo, hi, log=logs) == first
    assert tape.calls == []
    assert "3 fully covered, 0 need gap fill" in logs.text()


def test_a_window_shifted_a_week_fills_only_the_tail(store):
    load(store, Tape(), SYMS, date(2025, 1, 6), date(2025, 6, 30))
    tape = Tape()
    got = load(store, tape, SYMS, date(2025, 1, 13), date(2025, 7, 7))
    assert len(tape.calls) == 1                      # one batched tail request
    params = tape.calls[0][1]
    assert params["start"] == "2025-06-24T00:00:00Z"  # 7 days of overlap
    assert params["end"] == "2025-07-07T23:59:59Z"
    assert got["AAA"][-1]["t"].startswith("2025-07-07")
    assert got["AAA"][0]["t"].startswith("2025-01-13")
    assert store.get("SwingDailyBars", "AAA|2025")["covered_from"] == "2025-01-06"
    assert store.get("SwingDailyBars", "AAA|2025")["covered_through"] == "2025-07-07"


def test_a_window_that_starts_earlier_fills_only_the_head(store):
    load(store, Tape(), SYMS, date(2025, 3, 3), date(2025, 6, 30))
    tape = Tape()
    got = load(store, tape, SYMS, date(2025, 1, 6), date(2025, 6, 30))
    assert len(tape.calls) == 1
    params = tape.calls[0][1]
    assert params["start"] == "2025-01-06T00:00:00Z"
    assert params["end"] == "2025-03-09T23:59:59Z"   # 7 days into the cached span
    assert got["AAA"][0]["t"].startswith("2025-01-06")
    assert store.get("SwingDailyBars", "AAA|2025")["covered_from"] == "2025-01-06"


def test_a_missing_middle_year_is_filled(store):
    load(store, Tape(), SYMS, date(2023, 1, 2), date(2023, 12, 29))
    load(store, Tape(), SYMS, date(2025, 1, 2), date(2025, 6, 30))
    tape = Tape()
    got = load(store, tape, SYMS, date(2023, 1, 2), date(2025, 6, 30))
    ranges = {(p["start"][:10], p["end"][:10]) for _u, p in tape.calls}
    assert ranges == {("2023-12-23", "2025-01-08")}
    assert len(got["AAA"]) == len(sessions(date(2023, 1, 2), date(2025, 6, 30)))


def test_a_split_on_the_overlap_refreshes_the_whole_history(store):
    lo = date(2025, 1, 6)
    load(store, Tape(), SYMS, lo, date(2025, 6, 30))
    tape, logs = Tape(), Logs()
    tape.scale["BBB"] = 0.5                          # a 2-for-1 split, back-adjusted
    got = load(store, tape, SYMS, lo, date(2025, 7, 7), log=logs)
    assert "split/adjustment change detected for BBB — history refreshed" in logs.text("yellow")
    full = [p for _u, p in tape.calls if p["symbols"] == "BBB"]
    assert [(p["start"][:10], p["end"][:10]) for p in full] == [("2025-01-06", "2025-07-07")]
    fresh = Tape()
    fresh.scale["BBB"] = 0.5
    assert got["BBB"] == [fresh.bar("BBB", d) for d in sessions(lo, date(2025, 7, 7))]
    assert store.get("SwingDailyBars", "BBB|2025")["bars"][0]["c"] == fresh.bar("BBB", lo)["c"]
    assert got["AAA"][0] == Tape().bar("AAA", lo)       # untouched names keep their rows


def test_symbols_are_batched_100_a_request_and_pages_are_followed(store):
    syms = [f"S{i:03d}" for i in range(250)]
    tape = Tape(page=3000)
    got = load(store, tape, syms, date(2025, 1, 6), date(2025, 3, 28))
    first_pages = [p for _u, p in tape.calls if "page_token" not in p]
    assert [len(p["symbols"].split(",")) for p in first_pages] == [100, 100, 50]
    assert len(tape.calls) > 3                       # 100 x 60 sessions > one 3000-bar page
    assert all(len(got[s]) == len(sessions(date(2025, 1, 6), date(2025, 3, 28))) for s in syms)


def test_the_multi_symbol_params_are_the_engine_loaders():
    ns = harness.extract(["_historical_bars_request_params"])
    params = bb._multi_params(date(2025, 1, 2), date(2025, 3, 31), "sip", ["AAPL", "MSFT"])
    assert params.pop("symbols") == "AAPL,MSFT"
    assert params == ns["_historical_bars_request_params"](
        is_crypto=False, symbol="AAPL", start_iso="2025-01-02T00:00:00Z",
        end_iso="2025-03-31T23:59:59Z", timeframe="1Day", feed="sip", adjustment="split")


def test_an_unreadable_cache_falls_back_to_the_per_symbol_path(monkeypatch):
    import price_utils
    monkeypatch.setattr(price_utils, "get_bars_chunk_cached",
                        lambda conn, sym, a, b, tf, feed, fetch_fn, adjustment="raw":
                        (fetch_fn(), False))

    class Unreadable:
        def get_all(self, *_a, **_k):
            raise RuntimeError("server7 unreachable")

    urls, logs = [], Logs()

    def http(url, *, headers, params, timeout):
        urls.append(url)
        return Resp({"bars": [{"t": "2025-01-02T05:00:00Z", "c": 1.0}]})

    got = bb.fetch_daily_bars(["AAA", "BBB"], date(2025, 1, 2), date(2025, 1, 3), key="k",
                              secret="s", feed="iex", http_get=http, sleep=lambda s: None,
                              log=logs, store=Unreadable(), today=TODAY)
    assert set(got) == {"AAA", "BBB"}
    assert all(u.endswith(("/stocks/AAA/bars", "/stocks/BBB/bars")) for u in urls)
    assert "SwingDailyBars unreadable" in logs.text("yellow")


def test_progress_is_logged_every_10_symbols_as_they_complete(store):
    logs = Logs()
    load(store, Tape(), [f"P{i:02d}" for i in range(25)], date(2025, 1, 6),
         date(2025, 1, 31), log=logs)
    progress = [m for _c, m in logs if "symbols, " in m and "/s, ETA" in m]
    assert [m.split()[1] for m in progress] == ["10/25", "20/25", "25/25"]


def test_a_warm_510_symbol_two_year_run_is_store_bound_and_fast(store):
    syms = [f"W{i:03d}" for i in range(510)]
    lo, hi = date(2024, 1, 2), date(2025, 12, 31)
    load(store, Tape(), syms, lo, hi)
    tape = Tape()
    t0 = time.perf_counter()
    got = load(store, tape, syms, lo, hi)
    took = time.perf_counter() - t0
    assert tape.calls == [] and len(got) == 510
    if store.__class__.__name__ == "FakeStore":
        assert took < 1.0, took
