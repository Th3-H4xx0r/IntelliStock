"""discover_universe_cached throttles the (expensive, networked) discovery call
so a crypto backtest/live run doesn't re-discover the universe on every step."""
from strategies.crypto import core


def test_cached_discovery_throttles_to_once_per_interval(monkeypatch):
    calls = []

    def _fake(*a, **k):
        calls.append(1)
        return ["BTC/USD", "ETH/USD"]

    monkeypatch.setattr(core, "discover_universe", _fake)
    cache = {}
    # 25 calls with every=24 -> discovers only on call 0 and call 24.
    for _ in range(25):
        assert core.discover_universe_cached("low", 5, {}, "1Hour", cache, every=24) == ["BTC/USD", "ETH/USD"]
    assert len(calls) == 2


def test_cached_discovery_reuses_between_intervals(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "discover_universe", lambda *a, **k: (calls.append(1), ["SOL/USD"])[1])
    cache = {}
    for _ in range(10):
        core.discover_universe_cached("high", 5, {}, "1Hour", cache, every=24)
    assert len(calls) == 1                       # one discovery, nine cache hits


def test_cached_discovery_caches_empty_to_avoid_retry_storms(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "discover_universe", lambda *a, **k: (calls.append(1), [])[1])
    cache = {}
    for _ in range(10):
        assert core.discover_universe_cached("low", 5, {}, "1Hour", cache, every=24) == []
    assert len(calls) == 1                       # empty result cached -> no per-step retry


def test_cached_discovery_falls_through_without_persistent_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(core, "discover_universe", lambda *a, **k: (calls.append(1), ["X/USD"])[1])
    for _ in range(3):
        assert core.discover_universe_cached("low", 5, {}, "1Hour", None) == ["X/USD"]
    assert len(calls) == 3                        # no cache dict -> behaves like uncached
