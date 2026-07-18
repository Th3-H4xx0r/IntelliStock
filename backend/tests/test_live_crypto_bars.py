"""Unit tests for live_crypto_bars — the live-mode bars provisioning that
closes the "live passes data=None" gap for crypto run_once strategies.

The safety contract under test: never hand strategies a data dict that would
make exit_blind_held dump held positions on a transient feed failure; return
None (=> caller skips the tick) only when there is truly nothing to serve.
"""
import datetime

from live_crypto_bars import build_live_crypto_data, lookback_start

NOW = datetime.datetime(2026, 7, 18, 12, 0, tzinfo=datetime.timezone.utc)
MAJORS = ["BTC/USD", "ETH/USD"]


def _bars(n=3, px=100.0):
    return [{"t": i, "o": px, "h": px, "l": px, "c": px, "v": 1.0} for i in range(n)]


def test_lookback_start_covers_requested_bars():
    start = lookback_start(NOW, 3600, 5040)
    hours = (NOW - start).total_seconds() / 3600.0
    assert hours >= 5040                      # at least the requested window
    assert hours <= 5042                      # plus only the one-bar slack


def test_happy_path_fetches_union_universe():
    calls = {}
    def fetch(syms, start, end):
        calls["syms"] = list(syms)
        return {s: _bars() for s in syms}
    out = build_live_crypto_data(fetch, ["BTC/USD"], ["ETH/USD"], ["SOL/USD"],
                                 MAJORS, NOW, 3600, 100)
    assert sorted(calls["syms"]) == ["BTC/USD", "ETH/USD", "SOL/USD"]
    assert sorted(out.keys()) == ["BTC/USD", "ETH/USD", "SOL/USD"]
    assert all(out[s] for s in out)


def test_cold_start_falls_back_to_majors():
    def fetch(syms, start, end):
        return {s: _bars() for s in syms}
    out = build_live_crypto_data(fetch, [], [], [], MAJORS, NOW, 3600, 100)
    assert sorted(out.keys()) == sorted(MAJORS)


def test_no_universe_and_no_majors_returns_empty_dict():
    out = build_live_crypto_data(lambda *a: {}, [], [], [], [], NOW, 3600, 100)
    assert out == {}                          # nothing to fetch != failure


def test_partial_fetch_backfills_held_from_last_good():
    # ETH (held) comes back empty this tick -> reuse last-good bars so
    # exit_blind_held does NOT see a held coin with no data.
    def fetch(syms, start, end):
        return {"BTC/USD": _bars(), "ETH/USD": []}
    last_good = {"ETH/USD": _bars(5, 200.0)}
    out = build_live_crypto_data(fetch, ["BTC/USD"], ["ETH/USD"], [],
                                 MAJORS, NOW, 3600, 100, last_good=last_good)
    assert out["ETH/USD"] == last_good["ETH/USD"]
    assert out["BTC/USD"]


def test_total_failure_reuses_last_good_snapshot():
    def fetch(syms, start, end):
        raise RuntimeError("network down")
    last_good = {"BTC/USD": _bars(), "ETH/USD": _bars()}
    out = build_live_crypto_data(fetch, ["BTC/USD"], [], [], MAJORS,
                                 NOW, 3600, 100, last_good=last_good)
    assert out == last_good


def test_total_failure_without_last_good_returns_none():
    def fetch(syms, start, end):
        return {}
    out = build_live_crypto_data(fetch, ["BTC/USD"], [], [], MAJORS,
                                 NOW, 3600, 100, last_good=None)
    assert out is None                        # caller must SKIP the tick


def test_fetch_exception_never_propagates():
    def fetch(syms, start, end):
        raise ValueError("boom")
    assert build_live_crypto_data(fetch, ["BTC/USD"], [], [], MAJORS,
                                  NOW, 3600, 100) is None


def test_symbol_dedup_and_normalization():
    calls = {}
    def fetch(syms, start, end):
        calls["syms"] = list(syms)
        return {s: _bars() for s in syms}
    build_live_crypto_data(fetch, ["btc/usd ", "BTC/USD"], ["BTC/USD"], [],
                           MAJORS, NOW, 3600, 100)
    assert calls["syms"] == ["BTC/USD"]
