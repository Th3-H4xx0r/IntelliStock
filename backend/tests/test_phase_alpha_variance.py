"""Phase α variance-containment tests (BT109429 follow-up, 2026-05-18).

Covers the four Phase α changes that precede the Phase β per-fix flag
validation gate Φ.1, plus the bug-sweep fixups:

  * α.4 — mcap pre-seed visibility patch + yfinance retry/backoff
  * α.1 — mandatory sentiment cache in backtest mode
  * α.3 — deterministic RNG seeding (derive_backtest_seed helper)
  * α.2 — Neo4j weekly snapshot for propagation determinism
  * Bug-sweep: idempotency-on-success, LRU eviction, institutional bg
    snapshot, propagation silent-zero, extracted helpers.

Reference: docs/superpowers/plans/2026-05-18-bt136708-fix-implementation.md §11
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch

import pytest

# Allow direct import from backend/ regardless of pytest cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_BACKEND, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.strategies.graph_nexus_analysis as gna
from backend._phase_alpha_helpers import (
    derive_backtest_seed,
    neo4j_snapshot_key,
    resolve_use_sentiment_cache,
)


# ──────────────────────────────────────────────────────────────────────────
# Shared fixtures — protect cross-test global state
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_gn_live_flag():
    """Bug-sweep follow-up: ensure no test leaves _GN_LIVE_MODE_FLAG=True."""
    prev = gna._GN_LIVE_MODE_FLAG
    gna._GN_LIVE_MODE_FLAG = False
    yield
    gna._GN_LIVE_MODE_FLAG = prev


@pytest.fixture(autouse=True)
def _isolate_neo4j_mcap_cache():
    """Bug-sweep follow-up: snapshot+restore the module-level neo4j mcap
    cache so a test populating SNDK doesn't leak into the next test's
    'cache empty' assumption."""
    snapshot = dict(gna._neo4j_market_cap_cache)
    yield
    gna._neo4j_market_cap_cache.clear()
    gna._neo4j_market_cap_cache.update(snapshot)


def _capture_logs():
    """Return (logs_list, patcher). logs_list collects (msg, color) tuples."""
    logs: list[tuple[str, str]] = []

    def _fake_log(msg, color=""):
        logs.append((str(msg), str(color or "")))

    return logs, patch.object(gna, "_log", side_effect=_fake_log)


def _fake_yfinance_module(ticker_factory):
    """Bug-sweep follow-up: use types.SimpleNamespace so it walks-and-quacks
    like a module (clearer intent than a bare instance)."""
    return types.SimpleNamespace(Ticker=ticker_factory)


# ──────────────────────────────────────────────────────────────────────────
# α.4 — mcap pre-seed visibility patch + retry/backoff
# ──────────────────────────────────────────────────────────────────────────


def test_alpha4_preseed_always_logs_even_when_populated_zero():
    """The prior `if populated: _log(...)` gate hid populated=0 — α.4 fix."""
    cache: dict = {}
    logs, patcher = _capture_logs()
    with patcher:
        populated = gna._preseed_mcap_cache_from_universe(
            ["SNDK", "ADBE", "JNJ"],
            cache,
            {"mcap_preseed_use_yfinance": False},
        )
    assert populated == 0
    summary_logs = [m for m, _ in logs if "mcap pre-seed:" in m and "populated" in m]
    assert summary_logs, "α.4 visibility patch did not emit summary line for populated=0"
    alert_logs = [m for m, c in logs if "0 tickers populated" in m and c == "red"]
    assert alert_logs, "α.4 visibility patch did not emit red populated=0 alert"


def test_alpha4_preseed_counters_track_neo4j_hits():
    cache: dict = {}
    gna._neo4j_market_cap_cache["SNDK"] = 36e9
    logs, patcher = _capture_logs()
    with patcher:
        populated = gna._preseed_mcap_cache_from_universe(
            ["SNDK", "ADBE"],
            cache,
            {"mcap_preseed_use_yfinance": False},
        )
    assert populated == 1
    summary = next(m for m, _ in logs if "mcap pre-seed:" in m and "populated" in m)
    assert "neo4j_hits=1" in summary
    assert "yf_hits=0" in summary


def test_alpha4_preseed_yfinance_retry_loop_fires():
    cache: dict = {}
    attempts = {"count": 0}

    class _FailingTicker:
        def __init__(self, sym):
            self.sym = sym

        @property
        def info(self):
            attempts["count"] += 1
            raise RuntimeError("simulated rate limit")

    fake_yf = _fake_yfinance_module(lambda sym: _FailingTicker(sym))
    logs, log_patcher = _capture_logs()
    with patch.dict(sys.modules, {"yfinance": fake_yf}), log_patcher:
        populated = gna._preseed_mcap_cache_from_universe(
            ["SNDK"],
            cache,
            {
                "mcap_preseed_use_yfinance": True,
                "mcap_preseed_yfinance_max_attempts": 3,
                "mcap_preseed_yfinance_base_delay_sec": 0.0,
            },
        )
    assert populated == 0
    assert attempts["count"] == 3, f"expected 3 retries, got {attempts['count']}"
    summary = next(m for m, _ in logs if "mcap pre-seed:" in m and "populated" in m)
    assert "yf_failures=1" in summary
    retry_logs = [m for m, _ in logs if "attempt" in m and "retrying" in m]
    assert retry_logs, "α.4 retry loop did not log per-attempt failures"


def test_alpha4_preseed_yfinance_constructor_raise_also_retries():
    """Bug-sweep follow-up: failures in Ticker() construction (not just
    .info access) should also exercise the retry loop."""
    cache: dict = {}
    attempts = {"count": 0}

    def _ticker_factory(sym):
        attempts["count"] += 1
        raise RuntimeError("constructor blew up")

    fake_yf = _fake_yfinance_module(_ticker_factory)
    with patch.dict(sys.modules, {"yfinance": fake_yf}):
        populated = gna._preseed_mcap_cache_from_universe(
            ["SNDK"],
            cache,
            {
                "mcap_preseed_use_yfinance": True,
                "mcap_preseed_yfinance_max_attempts": 2,
                "mcap_preseed_yfinance_base_delay_sec": 0.0,
            },
        )
    assert populated == 0
    assert attempts["count"] == 2


def test_alpha4_preseed_yfinance_returns_zero_marketCap_is_distinct_from_failure():
    cache: dict = {}

    class _ZeroMCapTicker:
        def __init__(self, sym):
            self.info = {"marketCap": 0}

    fake_yf = _fake_yfinance_module(lambda sym: _ZeroMCapTicker(sym))
    logs, log_patcher = _capture_logs()
    with patch.dict(sys.modules, {"yfinance": fake_yf}), log_patcher:
        populated = gna._preseed_mcap_cache_from_universe(
            ["SNDK", "ADBE"],
            cache,
            {"mcap_preseed_use_yfinance": True, "mcap_preseed_yfinance_max_attempts": 1},
        )
    assert populated == 0
    summary = next(m for m, _ in logs if "mcap pre-seed:" in m and "populated" in m)
    assert "yf_zeros=2" in summary
    assert "yf_failures=0" in summary


def test_alpha4_preseed_does_not_lock_idempotency_flag_after_all_failures():
    """Bug-sweep fixup: if yfinance fails on EVERY ticker, the
    `_yf_market_cap_cache_preseeded` flag must NOT be set — that would
    re-create the BT109429 silent-fail mode where the strategy is locked
    into no-mcap-data for the whole backtest after a transient rate limit
    at minute 0."""
    cache: dict = {}

    class _AlwaysFailTicker:
        def __init__(self, sym):
            pass

        @property
        def info(self):
            raise RuntimeError("rate limited")

    fake_yf = _fake_yfinance_module(lambda sym: _AlwaysFailTicker(sym))
    with patch.dict(sys.modules, {"yfinance": fake_yf}):
        populated = gna._preseed_mcap_cache_from_universe(
            ["SNDK", "ADBE"],
            cache,
            {
                "mcap_preseed_use_yfinance": True,
                "mcap_preseed_yfinance_max_attempts": 1,
                "mcap_preseed_yfinance_base_delay_sec": 0.0,
            },
        )
    assert populated == 0
    assert cache.get("_yf_market_cap_cache_preseeded") is not True, (
        "idempotency flag should NOT lock when all yfinance fetches failed"
    )


def test_alpha4_preseed_locks_idempotency_flag_when_yfinance_disabled():
    """If yfinance is disabled (no failures possible), the flag DOES lock so
    repeat calls within the run are cheap."""
    cache: dict = {}
    gna._preseed_mcap_cache_from_universe(
        ["SNDK"], cache, {"mcap_preseed_use_yfinance": False},
    )
    assert cache.get("_yf_market_cap_cache_preseeded") is True


def test_alpha4_preseed_does_not_write_zero_to_cache_on_hard_failure():
    """Bug-sweep fixup: when yfinance exhausts retries, do NOT pollute the
    cache with 0.0 — leaves the slot open for a later code path that may
    successfully resolve mcap via Benzinga or another probe."""
    cache: dict = {}

    class _AlwaysFailTicker:
        def __init__(self, sym):
            pass

        @property
        def info(self):
            raise RuntimeError("rate limited")

    fake_yf = _fake_yfinance_module(lambda sym: _AlwaysFailTicker(sym))
    with patch.dict(sys.modules, {"yfinance": fake_yf}):
        gna._preseed_mcap_cache_from_universe(
            ["SNDK"],
            cache,
            {
                "mcap_preseed_use_yfinance": True,
                "mcap_preseed_yfinance_max_attempts": 1,
                "mcap_preseed_yfinance_base_delay_sec": 0.0,
            },
        )
    mcap_cache = cache.get("_yf_market_cap_cache", {})
    assert "SNDK" not in mcap_cache, "SNDK should NOT be in cache after hard failure"


def test_alpha4_preseed_logs_skip_reason_on_invalid_strategy_cache():
    """Bug-sweep follow-up: early-return paths now emit yellow log."""
    logs, patcher = _capture_logs()
    with patcher:
        gna._preseed_mcap_cache_from_universe(["SNDK"], None, {})
    assert any("strategy_cache not a dict" in m for m, _ in logs)


def test_alpha4_preseed_logs_skip_reason_on_empty_universe():
    logs, patcher = _capture_logs()
    with patcher:
        gna._preseed_mcap_cache_from_universe([], {}, {})
    assert any("empty symbols_list" in m for m, _ in logs)


# ──────────────────────────────────────────────────────────────────────────
# α.1 — mandatory sentiment cache (now via extracted helper)
# ──────────────────────────────────────────────────────────────────────────


def test_alpha1_force_enables_in_backtest_even_when_user_disabled():
    cfg = {
        "use_sentiment_cache": False,
        "_nexus_is_live_mode": False,
        "historical_lookback_mode": False,
    }
    use_cache, forced = resolve_use_sentiment_cache(cfg)
    assert use_cache is True
    assert forced is True


def test_alpha1_force_enables_in_lookback_mode():
    cfg = {
        "use_sentiment_cache": False,
        "_nexus_is_live_mode": False,
        "historical_lookback_mode": True,
    }
    use_cache, forced = resolve_use_sentiment_cache(cfg)
    assert use_cache is True
    assert forced is True


def test_alpha1_live_mode_honors_operator_disable():
    cfg = {
        "use_sentiment_cache": False,
        "_nexus_is_live_mode": True,
        "historical_lookback_mode": False,
    }
    use_cache, forced = resolve_use_sentiment_cache(cfg)
    assert use_cache is False
    assert forced is False


def test_alpha1_live_with_lookback_pre_pass_uses_cache():
    """The (live=True, lookback=True) path = pre-pass; should still cache.
    This is what the agent flagged: gate logic gives correct result via OR
    short-circuit but the intent should be locked by a test."""
    cfg = {
        "use_sentiment_cache": False,
        "_nexus_is_live_mode": True,
        "historical_lookback_mode": True,
    }
    use_cache, forced = resolve_use_sentiment_cache(cfg)
    assert use_cache is True


def test_alpha1_opt_out_knob_disables_force():
    cfg = {
        "use_sentiment_cache": False,
        "_nexus_is_live_mode": False,
        "historical_lookback_mode": False,
        "nexus_sentiment_cache_force_in_backtest": False,
    }
    use_cache, forced = resolve_use_sentiment_cache(cfg)
    assert use_cache is False


def test_alpha1_force_applied_only_when_flipping_false_to_true():
    """If operator config was already True, force shouldn't claim it 'flipped'."""
    cfg = {
        "use_sentiment_cache": True,
        "_nexus_is_live_mode": False,
        "historical_lookback_mode": False,
    }
    _, forced = resolve_use_sentiment_cache(cfg)
    assert forced is False


def test_alpha1_knob_surfaced_in_effective_config():
    eff = gna._get_effective_nexus_config({})
    assert eff["nexus_sentiment_cache_force_in_backtest"] is True


# ──────────────────────────────────────────────────────────────────────────
# α.3 — RNG seed derivation (extracted helper)
# ──────────────────────────────────────────────────────────────────────────


def test_alpha3_seed_is_deterministic():
    seed1, _ = derive_backtest_seed(109429, ["ADBE", "AIQ", "ARKQ"])
    seed2, _ = derive_backtest_seed(109429, ["ADBE", "AIQ", "ARKQ"])
    assert seed1 == seed2


def test_alpha3_seed_is_universe_order_invariant():
    seed1, _ = derive_backtest_seed(109429, ["ADBE", "AIQ", "ARKQ"])
    seed2, _ = derive_backtest_seed(109429, ["ARKQ", "ADBE", "AIQ"])
    assert seed1 == seed2


def test_alpha3_seed_differs_for_different_universes():
    seed_a, _ = derive_backtest_seed(109429, ["ADBE", "AIQ"])
    seed_b, _ = derive_backtest_seed(109429, ["JNJ", "NKE"])
    assert seed_a != seed_b


def test_alpha3_seed_differs_for_different_backtest_ids():
    seed_a, _ = derive_backtest_seed(109429, ["ADBE", "AIQ"])
    seed_b, _ = derive_backtest_seed(136708, ["ADBE", "AIQ"])
    assert seed_a != seed_b


def test_alpha3_env_seed_takes_precedence():
    seed, source = derive_backtest_seed(109429, ["ADBE"], env_seed="42")
    assert seed == 42
    assert "BACKTEST_SEED env" in source


def test_alpha3_invalid_env_seed_falls_back_to_derivation():
    seed, source = derive_backtest_seed(109429, ["ADBE"], env_seed="not-an-int")
    assert seed != 42
    assert "sha256" in source


def test_alpha3_empty_id_and_universe_uses_uuid_fallback():
    """Bug-sweep fixup: without backtest_row_id AND universe, the prior
    sha256(\"|\") would have returned a CONSTANT for all no-id ad-hoc runs.
    The fix: UUID-derived non-deterministic seed (unique per process)."""
    seed_a, source_a = derive_backtest_seed(None, [])
    seed_b, source_b = derive_backtest_seed(None, [])
    assert "uuid fallback" in source_a
    # Different UUIDs each call → different seeds.
    assert seed_a != seed_b


def test_alpha3_seed_fits_in_numpy_uint32_after_mask():
    seed, _ = derive_backtest_seed(109429, ["ADBE"])
    assert 0 <= (seed & 0xFFFFFFFF) <= 0xFFFFFFFF


# ──────────────────────────────────────────────────────────────────────────
# α.2 — Neo4j weekly snapshot helper + key derivation
# ──────────────────────────────────────────────────────────────────────────


class _CallCounter:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.result


def test_alpha2_snapshot_caches_first_call():
    cache: dict = {}
    fn = _CallCounter(result=["edge_a", "edge_b"])
    out1 = gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK", "ADBE"}, "2026-05-18", fn)
    out2 = gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK", "ADBE"}, "2026-05-18", fn)
    assert out1 == ["edge_a", "edge_b"]
    assert out2 == ["edge_a", "edge_b"]
    # Bug-sweep follow-up: lock in the no-copy contract.
    assert out2 is out1
    assert fn.calls == 1


def test_alpha2_snapshot_separates_by_query_name():
    cache: dict = {}
    fn_out = _CallCounter(result=["out_edge"])
    fn_in = _CallCounter(result=["in_edge"])
    out = gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "2026-05-18", fn_out)
    inp = gna._neo4j_cached_query(cache, {}, "1hop_in", {"SNDK"}, "2026-05-18", fn_in)
    assert out == ["out_edge"]
    assert inp == ["in_edge"]
    assert fn_out.calls == 1
    assert fn_in.calls == 1


def test_alpha2_snapshot_separates_by_seeds():
    cache: dict = {}
    fn = _CallCounter(result=["x"])
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "2026-05-18", fn)
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"ADBE"}, "2026-05-18", fn)
    assert fn.calls == 2


def test_alpha2_snapshot_seed_order_invariant():
    cache: dict = {}
    fn = _CallCounter(result=["x"])
    gna._neo4j_cached_query(cache, {}, "1hop_out", ["SNDK", "ADBE"], "2026-05-18", fn)
    gna._neo4j_cached_query(cache, {}, "1hop_out", ["ADBE", "SNDK"], "2026-05-18", fn)
    assert fn.calls == 1


def test_alpha2_snapshot_separates_by_iso_week():
    cache: dict = {}
    fn = _CallCounter(result=["x"])
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "2026-05-12", fn)
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "2026-05-19", fn)
    assert fn.calls == 2


def test_alpha2_snapshot_off_granularity_bypasses_cache():
    cache: dict = {}
    fn = _CallCounter(result=["x"])
    cfg = {"nexus_neo4j_snapshot_granularity": "off"}
    gna._neo4j_cached_query(cache, cfg, "1hop_out", {"SNDK"}, "2026-05-18", fn)
    gna._neo4j_cached_query(cache, cfg, "1hop_out", {"SNDK"}, "2026-05-18", fn)
    assert fn.calls == 2
    assert "_neo4j_snapshot" not in cache


def test_alpha2_snapshot_once_granularity_uses_single_key():
    cache: dict = {}
    fn = _CallCounter(result=["x"])
    cfg = {"nexus_neo4j_snapshot_granularity": "once"}
    gna._neo4j_cached_query(cache, cfg, "1hop_out", {"SNDK"}, "2026-01-01", fn)
    gna._neo4j_cached_query(cache, cfg, "1hop_out", {"SNDK"}, "2026-12-31", fn)
    assert fn.calls == 1


def test_alpha2_snapshot_live_mode_bypasses_cache():
    cache: dict = {}
    fn = _CallCounter(result=["x"])
    gna._GN_LIVE_MODE_FLAG = True
    try:
        gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "2026-05-18", fn)
        gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "2026-05-18", fn)
    finally:
        gna._GN_LIVE_MODE_FLAG = False
    assert fn.calls == 2
    assert "_neo4j_snapshot" not in cache


def test_alpha2_snapshot_none_cache_bypasses():
    fn = _CallCounter(result=["x"])
    out1 = gna._neo4j_cached_query(None, {}, "1hop_out", {"SNDK"}, "2026-05-18", fn)
    out2 = gna._neo4j_cached_query(None, {}, "1hop_out", {"SNDK"}, "2026-05-18", fn)
    assert out1 == ["x"]
    assert out2 == ["x"]
    assert fn.calls == 2


def test_alpha2_snapshot_handles_none_seeds():
    cache: dict = {}
    fn = _CallCounter(result={"REL": True})
    out1 = gna._neo4j_cached_query(cache, {}, "available_rels", None, "2026-05-18", fn)
    out2 = gna._neo4j_cached_query(cache, {}, "available_rels", None, "2026-05-18", fn)
    assert out1 == {"REL": True}
    assert out2 == {"REL": True}
    assert fn.calls == 1


def test_alpha2_snapshot_empty_date_key_collapses_to_UNK_bucket():
    """Bug-sweep fixup: empty/None date_key share the UNK bucket; second
    call should hit cache."""
    cache: dict = {}
    fn = _CallCounter(result=["x"])
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "", fn)
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "", fn)
    assert fn.calls == 1
    assert "_neo4j_snapshot" in cache


def test_alpha2_snapshot_lru_eviction_caps_at_configured_size():
    """Bug-sweep fixup: snapshot must not grow unbounded — LRU caps."""
    cache: dict = {}
    cfg = {"nexus_neo4j_snapshot_lru_cap": 8}
    fn = _CallCounter(result=["x"])
    for i in range(20):
        # Each different seed_hash → different cache key.
        gna._neo4j_cached_query(cache, cfg, "1hop_out", {f"T{i}"}, "2026-05-18", fn)
    snapshot = cache.get("_neo4j_snapshot", {})
    assert 8 == len(snapshot), f"snapshot should have evicted to cap=8 but has {len(snapshot)}"


def test_alpha2_snapshot_stats_tracks_hits_and_misses():
    cache: dict = {}
    fn = _CallCounter(result=["x"])
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "2026-05-18", fn)
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "2026-05-18", fn)
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"ADBE"}, "2026-05-18", fn)
    stats = cache.get("_neo4j_snapshot_stats", {})
    assert stats.get("hits") == 1
    assert stats.get("misses") == 2


def test_alpha2_knob_surfaced_in_effective_config():
    eff = gna._get_effective_nexus_config({})
    assert eff["nexus_neo4j_snapshot_granularity"] == "weekly"
    assert eff["nexus_neo4j_snapshot_lru_cap"] == gna._NEO4J_SNAPSHOT_DEFAULT_LRU_CAP


def test_alpha2_mcap_preseed_knobs_surfaced_in_effective_config():
    eff = gna._get_effective_nexus_config({})
    assert eff["mcap_preseed_use_yfinance"] is True
    assert eff["mcap_preseed_yfinance_max_attempts"] == 3
    assert eff["mcap_preseed_yfinance_base_delay_sec"] == 0.5


# ──────────────────────────────────────────────────────────────────────────
# α.2 — snapshot key helper (used by both cached_query and inst bg path)
# ──────────────────────────────────────────────────────────────────────────


def test_alpha2_snapshot_key_off_granularity_returns_none():
    assert neo4j_snapshot_key("1hop_out", {"SNDK"}, "2026-05-18", {"nexus_neo4j_snapshot_granularity": "off"}) is None


def test_alpha2_snapshot_key_weekly_buckets_same_week_together():
    k1, _ = neo4j_snapshot_key("1hop_out", {"SNDK"}, "2026-05-12", {})  # Tue
    k2, _ = neo4j_snapshot_key("1hop_out", {"SNDK"}, "2026-05-14", {})  # Thu (same week)
    assert k1 == k2


def test_alpha2_snapshot_key_seed_order_invariant():
    k1, _ = neo4j_snapshot_key("1hop_out", ["A", "B"], "2026-05-18", {})
    k2, _ = neo4j_snapshot_key("1hop_out", ["B", "A"], "2026-05-18", {})
    assert k1 == k2


def test_alpha2_snapshot_key_broken_iterable_returns_none():
    """Bug-sweep fixup: a non-iterable seed input bypasses cache rather than
    colliding under a fixed empty-string hash."""
    class _NotIterable:
        def __iter__(self):
            raise TypeError("not iterable")
    assert neo4j_snapshot_key("1hop_out", _NotIterable(), "2026-05-18", {}) is None


# ──────────────────────────────────────────────────────────────────────────
# Persistence + migration-reset surface checks
# ──────────────────────────────────────────────────────────────────────────


def test_alpha2_neo4j_snapshot_in_persistence_blacklist():
    from backend.strategy_cache_persistence import _BLACKLIST_PREFIXES
    assert "_neo4j_snapshot" in _BLACKLIST_PREFIXES
    assert "_neo4j_snapshot_stats" in _BLACKLIST_PREFIXES


# ──────────────────────────────────────────────────────────────────────────
# Integration smoke — multiple α phases composed together
# ──────────────────────────────────────────────────────────────────────────


def test_alpha_composition_smoke():
    """Bug-sweep follow-up: confirm α.4 + α.1 + α.2 + α.3 surface coherently
    in a single effective_config snapshot and don't cross-pollute each
    other's state."""
    eff = gna._get_effective_nexus_config({})
    # α.4
    assert eff["mcap_preseed_use_yfinance"] is True
    assert eff["mcap_preseed_yfinance_max_attempts"] == 3
    # α.1
    assert eff["nexus_sentiment_cache_force_in_backtest"] is True
    # α.2
    assert eff["nexus_neo4j_snapshot_granularity"] == "weekly"
    assert eff["nexus_neo4j_snapshot_lru_cap"] >= 8
    # α.3 has no effective-config surface (it's broker-side); confirm
    # the helper is importable.
    seed, source = derive_backtest_seed(99999, ["SMOKE_TICKER"])
    assert isinstance(seed, int)
    assert seed > 0
    assert "sha256" in source


def test_alpha2_cross_bar_same_week_hits_cache():
    """Bug-sweep follow-up: two date_keys in the same ISO week should
    deduplicate across query calls, so the second bar in a week makes
    zero new Neo4j queries for that query+seeds combo."""
    cache: dict = {}
    fn = _CallCounter(result=["edge"])
    # Day 1 of ISO week.
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "2026-05-12", fn)
    # Day 2 of same ISO week (Tue → Wed).
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "2026-05-13", fn)
    # Day 3 of same ISO week (Thu).
    gna._neo4j_cached_query(cache, {}, "1hop_out", {"SNDK"}, "2026-05-14", fn)
    assert fn.calls == 1
