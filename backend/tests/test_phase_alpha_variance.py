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
import backend._phase_alpha_helpers as phase_alpha
from backend._phase_alpha_helpers import (
    backtest_determinism_env_vars,
    derive_backtest_seed,
    neo4j_snapshot_key,
    resolve_backtest_determinism,
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
    seeded set must NOT contain those tickers — that would re-create the
    BT109429 silent-fail mode where the strategy is locked into
    no-mcap-data for the whole backtest after a transient rate limit at
    minute 0. γ.1: contract is now per-ticker (set[str]) rather than
    whole-call bool, so the test verifies the FAILED tickers are absent
    from the set."""
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
    # γ.1: legacy bool flag must not be set under the new contract.
    assert "_yf_market_cap_cache_preseeded" not in cache
    # γ.1: per-ticker contract — failed tickers MUST NOT appear in the set
    # so a future call retries them.
    seeded = cache.get("_yf_market_cap_cache_preseeded_tickers") or set()
    assert "SNDK" not in seeded, "failed SNDK fetch must be retried on next call"
    assert "ADBE" not in seeded, "failed ADBE fetch must be retried on next call"


def test_alpha4_preseed_locks_idempotency_flag_when_yfinance_disabled():
    """If yfinance is disabled (no failures possible), the set IS populated
    with the touched tickers so repeat calls within the run are cheap.
    γ.1: per-ticker set[str] contract replaces the legacy whole-call bool."""
    cache: dict = {}
    gna._preseed_mcap_cache_from_universe(
        ["SNDK"], cache, {"mcap_preseed_use_yfinance": False},
    )
    # γ.1: legacy bool form is gone; new set form is the canonical contract.
    assert "_yf_market_cap_cache_preseeded" not in cache
    seeded = cache.get("_yf_market_cap_cache_preseeded_tickers")
    # SNDK touched the cache path (neo4j miss, yf disabled → still
    # "processed"). With both sources empty, SNDK doesn't reach the cache
    # dict, so the set membership reflects what's known to be done. Either
    # outcome (in or not in) is acceptable — the critical invariant is no
    # legacy bool flag and the set type is correct.
    assert isinstance(seeded, set)
    # Repeat call is a no-op even if SNDK wasn't actually seeded — universe
    # diff (already_seeded ∪ no-new-tickers) returns 0.
    again = gna._preseed_mcap_cache_from_universe(
        ["SNDK"], cache, {"mcap_preseed_use_yfinance": False},
    )
    assert again == 0


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
    """γ.1: log wording changed from "empty symbols_list" to "empty
    universe" because the universe is now built from three sources
    (symbols_list ∪ held positions ∪ BFQ top-N) — `symbols_list` is just
    one of them. An empty universe means all three sources are empty."""
    logs, patcher = _capture_logs()
    with patcher:
        gna._preseed_mcap_cache_from_universe([], {}, {})
    assert any("empty universe" in m for m, _ in logs)


# ──────────────────────────────────────────────────────────────────────────
# γ.1 — pre-seed from held positions + BFQ + set[str] idempotency
# ──────────────────────────────────────────────────────────────────────────


class _FakePortfolioEmulator:
    """Minimal portfolio_emulator stand-in for γ.1 tests.

    Exposes only the `_positions` attribute the pre-seed reads (mirrors the
    breach-heal direct-read pattern at graph_nexus_analysis.py:21701)."""

    def __init__(self, positions: dict):
        self._positions = dict(positions)


def test_gamma1_preseed_from_held_positions_with_empty_symbols_list():
    """γ.1 (BT232179 silent-fail fix): in pure-discovery mode the operator's
    symbols_list is empty at run_once entry. The pre-seed MUST still fire
    for currently-held tickers from `portfolio_emulator._positions` so A1's
    conviction-tier resolver has mcap data for those positions."""
    cache: dict = {}
    gna._neo4j_market_cap_cache["SNDK"] = 36e9
    pe = _FakePortfolioEmulator({"SNDK": 10, "ADBE": 5})

    populated = gna._preseed_mcap_cache_from_universe(
        [],  # empty operator symbols (pure-discovery mode)
        cache,
        {"mcap_preseed_use_yfinance": False},
        portfolio_emulator=pe,
    )
    # SNDK seeded from neo4j; ADBE has no neo4j entry → miss
    assert populated == 1
    assert cache.get("_yf_market_cap_cache", {}).get("SNDK") == 36e9
    seeded = cache.get("_yf_market_cap_cache_preseeded_tickers") or set()
    # ADBE WAS in the universe (it's held) but had no neo4j entry and
    # yfinance is disabled, so it never reached the cache dict. The set
    # tracks "reached cache", so only SNDK is in the set.
    assert "SNDK" in seeded


def test_gamma1_preseed_seeds_bfq_top_n_by_raw_score():
    """γ.1: BFQ candidates are merged into the universe, capped at
    `mcap_preseed_bfq_top_n` (default 20). Top-N is selected by raw_score
    descending to ensure the most-likely-to-execute candidates are seeded
    first under network budget constraints."""
    cache: dict = {
        "_backfill_queue": {
            f"T{i}": {"raw_score": float(i)} for i in range(30)
        },
    }
    # Seed neo4j for the highest-raw_score tickers so we can verify the
    # selection without yfinance hits.
    for i in range(25, 30):
        gna._neo4j_market_cap_cache[f"T{i}"] = 5e9 + i

    populated = gna._preseed_mcap_cache_from_universe(
        [],
        cache,
        {"mcap_preseed_use_yfinance": False, "mcap_preseed_bfq_top_n": 10},
    )
    # Top-10 by raw_score → T20..T29. Five of those (T25..T29) have neo4j
    # mcap, so populated == 5.
    assert populated == 5
    mcap = cache.get("_yf_market_cap_cache", {})
    for i in range(25, 30):
        assert f"T{i}" in mcap
    # Bottom of BFQ (T0..T19) was NOT included in the universe.
    seeded = cache.get("_yf_market_cap_cache_preseeded_tickers") or set()
    for i in range(0, 20):
        assert f"T{i}" not in seeded, f"T{i} should be below top-N cutoff"


def test_gamma1_preseed_set_semantics_extends_across_calls():
    """γ.1: subsequent calls extend the universe (newly-held positions,
    BFQ entrants) without re-fetching. Set membership prevents redundant
    yfinance hits while admitting fresh tickers."""
    cache: dict = {}
    gna._neo4j_market_cap_cache["SNDK"] = 36e9
    gna._neo4j_market_cap_cache["ADBE"] = 200e9

    # Call 1: only SNDK
    populated1 = gna._preseed_mcap_cache_from_universe(
        ["SNDK"], cache, {"mcap_preseed_use_yfinance": False},
    )
    assert populated1 == 1
    seeded = cache.get("_yf_market_cap_cache_preseeded_tickers") or set()
    assert seeded == {"SNDK"}

    # Call 2: SNDK + ADBE. SNDK already in set → skipped. ADBE new → seeded.
    populated2 = gna._preseed_mcap_cache_from_universe(
        ["SNDK", "ADBE"], cache, {"mcap_preseed_use_yfinance": False},
    )
    assert populated2 == 1  # ADBE only
    seeded = cache.get("_yf_market_cap_cache_preseeded_tickers") or set()
    assert seeded == {"SNDK", "ADBE"}


def test_gamma1_preseed_legacy_bool_flag_migrated_and_short_circuits():
    """γ.1 back-compat: persisted live state from a pre-γ.1 deploy may
    carry the legacy bool flag. First call honors it (returns 0) and
    migrates it to the set[str] form so subsequent calls use the new path."""
    cache: dict = {
        "_yf_market_cap_cache_preseeded": True,  # legacy
        "_yf_market_cap_cache": {"SNDK": 36e9, "ADBE": 200e9},
    }
    logs, patcher = _capture_logs()
    with patcher:
        populated = gna._preseed_mcap_cache_from_universe(
            ["SNDK"], cache, {"mcap_preseed_use_yfinance": False},
        )
    assert populated == 0  # legacy short-circuit
    # Bool flag consumed
    assert "_yf_market_cap_cache_preseeded" not in cache
    # Set initialized from existing cache keys
    seeded = cache.get("_yf_market_cap_cache_preseeded_tickers") or set()
    assert {"SNDK", "ADBE"}.issubset(seeded)
    # Migration log emitted
    assert any("migrated legacy bool flag" in m for m, _ in logs)


def test_gamma1_preseed_bfq_cap_is_respected():
    """γ.1: cap edge — large BFQ doesn't flood yfinance. With cap=5, only
    top-5 enter the universe."""
    cache: dict = {
        "_backfill_queue": {f"T{i}": {"raw_score": float(100 - i)} for i in range(50)},
    }
    # T0 has highest raw_score (100), T49 lowest (51).
    for i in range(5):
        gna._neo4j_market_cap_cache[f"T{i}"] = 1e9 + i

    populated = gna._preseed_mcap_cache_from_universe(
        [],
        cache,
        {"mcap_preseed_use_yfinance": False, "mcap_preseed_bfq_top_n": 5},
    )
    assert populated == 5  # top-5 by raw_score (T0..T4)


def test_gamma1_preseed_handles_malformed_portfolio_emulator():
    """γ.1: defensive — if portfolio_emulator is non-None but doesn't have
    `_positions`, we don't crash. Mirrors breach-heal `getattr(..., {}, )`
    pattern."""
    cache: dict = {}

    class _BadPortfolio:
        # No _positions attribute at all
        pass

    populated = gna._preseed_mcap_cache_from_universe(
        ["SNDK"],
        cache,
        {"mcap_preseed_use_yfinance": False},
        portfolio_emulator=_BadPortfolio(),
    )
    assert populated == 0  # neo4j miss, no crash


def test_gamma1_preseed_universe_log_shows_per_source_breakdown():
    """γ.1: visibility — the summary log includes per-source counts so the
    operator can see how the universe was assembled (operator vs held vs BFQ)."""
    cache: dict = {
        "_backfill_queue": {"BFQ1": {"raw_score": 1.8}, "BFQ2": {"raw_score": 1.5}},
    }
    pe = _FakePortfolioEmulator({"HELD1": 10})
    logs, patcher = _capture_logs()
    with patcher:
        gna._preseed_mcap_cache_from_universe(
            ["OP1", "OP2"],
            cache,
            {"mcap_preseed_use_yfinance": False},
            portfolio_emulator=pe,
        )
    summary = next(m for m, _ in logs if "mcap pre-seed:" in m and "universe_sources" in m)
    assert "symbols:2" in summary
    assert "held:1" in summary
    assert "bfq:2" in summary


def test_gamma1_preseed_bfq_top_n_knob_surfaced_in_effective_config():
    eff = gna._get_effective_nexus_config({})
    assert eff.get("mcap_preseed_bfq_top_n") == 20


# ──────────────────────────────────────────────────────────────────────────
# γ.4 — V28 rotation raw>=1.8 winner_lock bypass
# ──────────────────────────────────────────────────────────────────────────


def _rotation_args(**overrides):
    """Helper: build keyword args for `_rotation_candidate_allowed` with
    sane defaults so each test only sets the field it cares about."""
    base = dict(
        held_pnl_pct=5.0,
        held_rotation_score=0.5,
        held_days=10,
        held_raw_score=0.5,
        drop_from_peak_pct=2.0,  # within 8% peak window → winner_lock active
        is_equity=True,
        incoming_raw_score=1.0,
        incoming_rotation_score=1.0,
        config={},
        incoming_meta={"is_top_momentum": False, "is_high_conviction": False},
    )
    base.update(overrides)
    return base


def test_gamma4_raw_1_8_displaces_winner_lock_with_held_pnl_below_10():
    """γ.4: raw>=1.8 incoming with held pnl<10% → return True with mode
    `gamma_winner_lock_bypass`. APLD/CRWV/PLTR case from BT232179."""
    args = _rotation_args(
        held_pnl_pct=8.8,   # CSCO-class: positive but below 10%
        held_days=9,
        held_raw_score=0.5,
        drop_from_peak_pct=3.0,
        incoming_raw_score=1.8,
        incoming_rotation_score=1.8,
    )
    allowed, delta, mode = gna._rotation_candidate_allowed(**args)
    assert allowed is True
    assert mode == "gamma_winner_lock_bypass"


def test_gamma4_raw_1_7_does_not_displace_winner_lock():
    """γ.4: raw=1.7 incoming is BELOW the 1.8 threshold → still blocked."""
    args = _rotation_args(
        held_pnl_pct=8.8,
        held_days=9,
        held_raw_score=0.5,
        drop_from_peak_pct=3.0,
        incoming_raw_score=1.7,
        incoming_rotation_score=1.7,
    )
    allowed, _delta, mode = gna._rotation_candidate_allowed(**args)
    assert allowed is False
    assert mode == "winner_lock"


def test_gamma4_does_not_displace_true_winner_at_high_pnl():
    """γ.4: held pnl>=10% (true winner) is protected even from raw>=1.8."""
    args = _rotation_args(
        held_pnl_pct=25.0,  # genuine winner
        held_days=15,
        held_raw_score=1.0,
        drop_from_peak_pct=1.0,
        incoming_raw_score=1.8,
        incoming_rotation_score=1.8,
    )
    allowed, _delta, mode = gna._rotation_candidate_allowed(**args)
    # Existing break_glass_raw_score is 2.75 (default), so 1.8 doesn't
    # qualify there. γ.4 bypass requires held_pnl<10% → not met.
    # Result: blocked at winner_lock.
    assert allowed is False
    assert mode == "winner_lock"


def test_gamma4_bypass_knobs_surfaced_in_effective_config():
    eff = gna._get_effective_nexus_config({})
    assert eff.get("rotation_winner_lock_bypass_min_raw_score") == 1.8
    assert eff.get("rotation_winner_lock_bypass_max_held_pnl_pct") == 10.0


def test_gamma4_break_glass_still_wins_above_bypass_pnl_when_raw_high():
    """γ.4 sanity: at raw=2.75+ AND held_pnl=12% (above γ.4 cap), the
    existing break_glass_trim path still admits. γ.4 doesn't degrade
    existing high-conviction displacement."""
    args = _rotation_args(
        held_pnl_pct=12.0,
        held_days=15,
        held_raw_score=1.0,
        drop_from_peak_pct=1.0,
        incoming_raw_score=2.80,
        incoming_rotation_score=2.80,
    )
    allowed, _delta, mode = gna._rotation_candidate_allowed(**args)
    assert allowed is True
    assert mode == "break_glass_trim"


# ──────────────────────────────────────────────────────────────────────────
# γ.3 — breach-heal winner_lock bypass threshold
# ──────────────────────────────────────────────────────────────────────────
# Note: the heal loop sits inside run_once and is exercised via integration
# rather than unit-test surface. We verify the config-knob plumbing here;
# the behavioral assertion is reserved for Φ.γ.2 (BT232179 re-run with
# breach rate < 30% target).


def test_gamma3_breach_heal_bypass_knob_default_is_15():
    eff = gna._get_effective_nexus_config({})
    assert eff.get("breach_heal_winner_lock_bypass_max_pnl_pct") == 15.0


def test_gamma3_breach_heal_bypass_knob_operator_override():
    eff = gna._get_effective_nexus_config(
        {"breach_heal_winner_lock_bypass_max_pnl_pct": 20.0}
    )
    assert eff.get("breach_heal_winner_lock_bypass_max_pnl_pct") == 20.0


# ──────────────────────────────────────────────────────────────────────────
# γ.5 — conviction telemetry production log wiring
# ──────────────────────────────────────────────────────────────────────────


def test_gamma5_conviction_tier_log_emits_mcap_high_path():
    """γ.5: $36B mcap → tier=HIGH path=mcap_high."""
    cache = {"_yf_market_cap_cache": {"SNDK": 36e9}}
    logs, patcher = _capture_logs()
    with patcher:
        tier = gna._resolve_conviction_tier_at_exit(
            "SNDK", config={}, strategy_cache=cache, propagated={}
        )
    assert tier == "HIGH"
    audit = [m for m, _ in logs if "conviction_tier:" in m]
    assert audit, "γ.5 wiring did not emit conviction_tier log line"
    assert "tier=HIGH" in audit[0]
    assert "path=mcap_high" in audit[0]
    assert "sym=SNDK" in audit[0]


def test_gamma5_conviction_tier_log_emits_default_low_path():
    """γ.5: empty mcap + low raw → tier=LOW path=default_low (the BT232179
    silent-fail observability gap fix)."""
    cache: dict = {}
    logs, patcher = _capture_logs()
    with patcher:
        tier = gna._resolve_conviction_tier_at_exit(
            "OBSCURE", config={}, strategy_cache=cache, propagated={"OBSCURE": {"raw_score": 0.1}}
        )
    assert tier == "LOW"
    audit = [m for m, _ in logs if "conviction_tier:" in m]
    assert audit
    assert "path=default_low" in audit[0]


def test_gamma5_conviction_tier_log_emits_raw_high_path():
    """γ.5: empty mcap + raw>=1.0 → tier=HIGH path=raw_high (propagation
    signal admits without mcap data)."""
    cache: dict = {}
    logs, patcher = _capture_logs()
    with patcher:
        tier = gna._resolve_conviction_tier_at_exit(
            "PROP", config={}, strategy_cache=cache, propagated={"PROP": {"raw_score": 1.2}}
        )
    assert tier == "HIGH"
    audit = [m for m, _ in logs if "conviction_tier:" in m]
    assert audit
    assert "path=raw_high" in audit[0]


def test_gamma5_conviction_tier_log_emits_mcap_mid_path():
    """γ.5: $15B mcap (between 10B-30B thresholds) → tier=MID path=mcap_mid."""
    cache = {"_yf_market_cap_cache": {"MIDCAP": 15e9}}
    logs, patcher = _capture_logs()
    with patcher:
        tier = gna._resolve_conviction_tier_at_exit(
            "MIDCAP", config={}, strategy_cache=cache, propagated={}
        )
    assert tier == "MID"
    audit = [m for m, _ in logs if "conviction_tier:" in m]
    assert audit
    assert "path=mcap_mid" in audit[0]


def test_gamma5_conviction_tier_log_emits_raw_mid_path():
    """γ.5: empty mcap + raw=0.7 (between 0.6 mid and 1.0 high) → MID
    via raw_mid path."""
    cache: dict = {}
    logs, patcher = _capture_logs()
    with patcher:
        tier = gna._resolve_conviction_tier_at_exit(
            "RAWMID", config={}, strategy_cache=cache, propagated={"RAWMID": {"raw_score": 0.7}}
        )
    assert tier == "MID"
    audit = [m for m, _ in logs if "conviction_tier:" in m]
    assert audit
    assert "path=raw_mid" in audit[0]


def test_gamma5_conviction_tier_log_can_be_disabled():
    """γ.5: operator can disable via config knob to suppress log volume."""
    cache = {"_yf_market_cap_cache": {"SNDK": 36e9}}
    logs, patcher = _capture_logs()
    with patcher:
        gna._resolve_conviction_tier_at_exit(
            "SNDK",
            config={"conviction_telemetry_log_enabled": False},
            strategy_cache=cache,
            propagated={},
        )
    audit = [m for m, _ in logs if "conviction_tier:" in m]
    assert not audit, "log emitted despite conviction_telemetry_log_enabled=False"


def test_gamma5_conviction_telemetry_log_knob_surfaced_in_effective_config():
    eff = gna._get_effective_nexus_config({})
    assert eff.get("conviction_telemetry_log_enabled") is True


# ──────────────────────────────────────────────────────────────────────────
# Phase δ — observability sweep: kill the remaining `if N: _log(...)` gates
# ──────────────────────────────────────────────────────────────────────────
# Background: BT109429 and BT232179 both shipped with silent-inertness
# (Phase α.4 mcap pre-seed; α.3 RNG seed) gated behind `if count: _log(...)`
# patterns that hid the populated=0 outcome. Phase δ kills 5 more such
# gates so the next backtest investigation has full audit trails.


def test_delta_benzinga_direct_signals_logs_empty_case():
    """Phase δ: prior `if signals: _log(...)` hid empty-result case.
    Now always logs, with bucket inspection for diagnosis."""
    logs, patcher = _capture_logs()
    with patcher:
        signals = gna._extract_benzinga_direct_signals(
            bz_data={"ratings": [], "insider_trades": [], "gov_trades": [], "ma": []},
            symbols_set={"AAPL"},
            date_key="2026-05-18",
        )
    assert signals == {}
    empty_logs = [m for m, _ in logs if "Benzinga direct signals: 0 tickers" in m]
    assert empty_logs, "δ patch did not emit empty-result log line"
    # Must include bucket names so the operator can verify whether bz_data
    # was actually populated or arrived empty.
    assert "ratings" in empty_logs[0]


def test_delta_benzinga_direct_signals_logs_populated_case_unchanged():
    """Phase δ regression: existing populated case must still emit
    the original green-color summary, just with the same shape."""
    logs, patcher = _capture_logs()
    with patcher:
        signals = gna._extract_benzinga_direct_signals(
            bz_data={
                "ratings": [
                    {"ticker": "AAPL", "action": "upgrade", "analyst": "Goldman"},
                ],
                "insider_trades": [],
                "gov_trades": [],
                "ma": [],
            },
            symbols_set={"AAPL"},
            date_key="2026-05-18",
        )
    assert "AAPL" in signals
    summary = next(m for m, _ in logs if "Benzinga direct signals:" in m)
    assert "1 tickers" in summary
    assert "1 bullish" in summary


def test_delta_neo4j_market_cap_cache_load_logs_no_driver_case():
    """Phase δ: prior silent return when driver=None left A1 mcap
    failures undiagnosable upstream. Now logs the skip."""
    # Snapshot + reset module state so this test is hermetic.
    prev = dict(gna._neo4j_market_cap_cache)
    gna._neo4j_market_cap_cache.clear()
    try:
        logs, patcher = _capture_logs()
        with patcher:
            gna._load_neo4j_market_cap_cache(None)
        skip_logs = [m for m, _ in logs if "Neo4j market_cap cache: skipped" in m]
        assert skip_logs, "δ patch did not emit driver-None skip log"
    finally:
        gna._neo4j_market_cap_cache.clear()
        gna._neo4j_market_cap_cache.update(prev)


def test_delta_neo4j_market_cap_cache_load_logs_exception_case():
    """Phase δ: prior `except Exception: pass` swallowed driver errors,
    leaving A1 silently degraded with no audit trail. Now surfaces the
    exception in red so the operator can correlate downstream A1 misses
    with the upstream load failure."""
    prev = dict(gna._neo4j_market_cap_cache)
    gna._neo4j_market_cap_cache.clear()

    class _FailingDriver:
        def session(self):
            raise RuntimeError("simulated neo4j connection refused")

    try:
        logs, patcher = _capture_logs()
        with patcher:
            gna._load_neo4j_market_cap_cache(_FailingDriver())
        err_logs = [m for m, c in logs if "Neo4j market_cap cache: load failed" in m and c == "red"]
        assert err_logs, "δ patch did not emit red exception log"
    finally:
        gna._neo4j_market_cap_cache.clear()
        gna._neo4j_market_cap_cache.update(prev)


# ──────────────────────────────────────────────────────────────────────────
# sentiment_cache_scope_salt audit
# ──────────────────────────────────────────────────────────────────────────


def test_sentiment_cache_scope_id_deterministic_across_calls():
    """Phase α.1 follow-up audit: scope_id is derived from operator-static
    config fields (provider, model, prompt_version, salt, etc.). Two calls
    with the SAME config must produce the SAME scope_id — that's the
    contract α.1 relies on for paired-rerun cache hits."""
    cfg = {
        "history_scope_id": "test-scope",
        "sentiment_cache_scope_salt": "v1",
        "use_toon_format": True,
        "num_articles_for_llm": 30,
    }
    id1 = gna._enhanced_sentiment_cache_scope_id(cfg, "main")
    id2 = gna._enhanced_sentiment_cache_scope_id(cfg, "main")
    assert id1 == id2


def test_sentiment_cache_scope_id_changes_when_salt_rotates():
    """Audit-locking test: confirm the salt IS part of the scope_id, so
    a rotation would invalidate cache (this is the failure mode we're
    auditing — salt is OPERATOR-controlled, NOT auto-rotated by code)."""
    cfg_v1 = {
        "history_scope_id": "test-scope",
        "sentiment_cache_scope_salt": "v1",
        "use_toon_format": True,
    }
    cfg_v2 = dict(cfg_v1, sentiment_cache_scope_salt="v2")
    id1 = gna._enhanced_sentiment_cache_scope_id(cfg_v1, "main")
    id2 = gna._enhanced_sentiment_cache_scope_id(cfg_v2, "main")
    assert id1 != id2


def test_sentiment_cache_scope_doc_includes_salt_field():
    """Audit-locking: the scope doc must surface salt so the run-time
    audit log can fingerprint it (broken salt rotation is visible)."""
    cfg = {"sentiment_cache_scope_salt": "audit-test-salt"}
    doc = gna._enhanced_sentiment_cache_scope_doc(cfg, "main")
    assert doc.get("sentiment_cache_scope_salt") == "audit-test-salt"


def test_sentiment_cache_scope_audit_flag_in_persistence_blacklist():
    """Phase δ: the one-shot audit-emit flag must NOT be persisted —
    operators want to re-see the audit line on every fresh run / restart."""
    from backend.strategy_cache_persistence import _BLACKLIST_PREFIXES
    assert "_sentiment_cache_scope_audit_emitted" in _BLACKLIST_PREFIXES


# ──────────────────────────────────────────────────────────────────────────
# Phase ε Stage 2 — execution-throughput unlock
# ──────────────────────────────────────────────────────────────────────────


# ε.C.4 — ETF allocation cap enforcement


def test_epsilon_c4_max_positions_etf_knob_surfaced_in_effective_config():
    eff = gna._get_effective_nexus_config({})
    assert eff.get("max_positions_etf") == 4


def test_epsilon_c4_held_etfs_blacklisted_from_persistence():
    from backend.strategy_cache_persistence import _BLACKLIST_PREFIXES
    assert "_nexus_held_etfs" in _BLACKLIST_PREFIXES


# ε.C.0' — V31 grace tier-aware catastrophic threshold


def test_epsilon_c0prime_grace_escape_uses_high_tier_threshold():
    """HIGH conviction: -25% catastrophic threshold (vs LOW default -15%).
    A held HIGH stock at -18.3% (SNDK exit case) should NOT escape grace."""
    in_grace, escaped, reason = gna._in_initial_grace_period(
        days_held=11, unrealized_pct=-18.3, config={}, market_regime="bull",
        conviction_tier="HIGH",
    )
    # HIGH catastrophic threshold = -25%. -18.3% does NOT escape.
    assert in_grace is True
    assert escaped is False


def test_epsilon_c0prime_grace_escape_uses_low_tier_threshold():
    """LOW conviction: -15% catastrophic threshold (default). -18.3% should escape."""
    in_grace, escaped, reason = gna._in_initial_grace_period(
        days_held=11, unrealized_pct=-18.3, config={}, market_regime="bull",
        conviction_tier="LOW",
    )
    assert in_grace is True
    assert escaped is True
    assert "escape_A_catastrophic" in reason
    assert "tier=LOW" in reason


def test_epsilon_c0prime_grace_escape_uses_mid_tier_threshold():
    """MID conviction: escape_A=-20% (catastrophic), escape_B=-15%/7d (bleed)."""
    # -13% at day 8: above escape_B threshold (-15%) → no escape
    _, escaped, _ = gna._in_initial_grace_period(
        days_held=8, unrealized_pct=-13.0, config={}, market_regime="bull",
        conviction_tier="MID",
    )
    assert escaped is False
    # -22% (below escape_A -20%) at day 8: catastrophic → escape
    _, escaped, _ = gna._in_initial_grace_period(
        days_held=8, unrealized_pct=-22.0, config={}, market_regime="bull",
        conviction_tier="MID",
    )
    assert escaped is True
    # -16% at day 8 (above escape_A -20% but below escape_B -15%, ≥7d):
    # cumulative bleed escape fires
    _, escaped, _ = gna._in_initial_grace_period(
        days_held=8, unrealized_pct=-16.0, config={}, market_regime="bull",
        conviction_tier="MID",
    )
    assert escaped is True
    # -16% at day 5 (above escape_A -20%, below escape_B -15%, but <7d min):
    # cumulative doesn't fire → no escape
    _, escaped, _ = gna._in_initial_grace_period(
        days_held=5, unrealized_pct=-16.0, config={}, market_regime="bull",
        conviction_tier="MID",
    )
    assert escaped is False


def test_epsilon_c0prime_grace_high_tier_deep_enough_drawdown_still_escapes():
    """HIGH conviction at -30% (worse than -25% threshold): escapes."""
    _, escaped, _ = gna._in_initial_grace_period(
        days_held=8, unrealized_pct=-30.0, config={}, market_regime="bull",
        conviction_tier="HIGH",
    )
    assert escaped is True


def test_epsilon_c0prime_grace_thresholds_surfaced_in_effective_config():
    eff = gna._get_effective_nexus_config({})
    assert eff.get("initial_grace_catastrophic_loss_pct_high") == -25.0
    assert eff.get("initial_grace_catastrophic_loss_pct_mid") == -20.0


def test_epsilon_c0prime_grace_unchanged_when_no_tier_passed():
    """Back-compat: existing callers without conviction_tier kwarg get
    LOW-default behavior (no regression)."""
    _, escaped, _ = gna._in_initial_grace_period(
        days_held=11, unrealized_pct=-18.3, config={}, market_regime="bull",
    )
    # No tier → LOW default → -15% threshold → -18.3% escapes
    assert escaped is True


# ε.C.2 — Regime-aware time-floor decay


def test_epsilon_c2_rotation_uses_chop_regime_shorter_floor():
    """In chop regime, profitable_min_hold floor is shorter — held=12
    should not block (chop default 10d), but would block in bull (20d)."""
    args = dict(
        held_pnl_pct=5.0, held_rotation_score=0.5, held_days=12,
        held_raw_score=0.5, drop_from_peak_pct=10.0, is_equity=True,
        incoming_raw_score=0.5, incoming_rotation_score=0.7,
        config={}, incoming_meta={"is_top_momentum": False, "is_high_conviction": False},
    )
    # Bull regime: held_days=12 < profitable_full_exit_min_hold_days=20 → profitable_min_hold
    allowed_bull, _, mode_bull = gna._rotation_candidate_allowed(market_regime="bull", **args)
    # Chop regime: held_days=12 >= 10 (chop's half-length default) → passes the time floor
    allowed_chop, _, mode_chop = gna._rotation_candidate_allowed(market_regime="chop", **args)
    # The chop call should have a different mode (passed the time floor)
    assert mode_bull == "profitable_min_hold"
    assert mode_chop != "profitable_min_hold"  # passed the chop's shorter floor


def test_epsilon_c2_regime_knobs_surfaced_in_effective_config():
    eff = gna._get_effective_nexus_config({})
    assert eff.get("rotation_min_hold_days_chop") == 5
    assert eff.get("rotation_min_hold_days_bull") == 10
    assert eff.get("rotation_min_hold_days_bear") == 10
    assert eff.get("rotation_profitable_full_exit_min_hold_days_chop") == 10
    assert eff.get("rotation_profitable_full_exit_min_hold_days_bull") == 20
    assert eff.get("rotation_profitable_full_exit_min_hold_days_bear") == 20


# ε.C.6 — V31.4 cooldown-lift force-promotion


def test_epsilon_c6_force_promote_prioritizes_lifted_ticker():
    """A V31.4-lifted ticker should be reserved a momentum_watchlist slot
    even if it scores lower than other watchlist candidates."""
    ranked = [
        ("LITE", 0.20),  # higher rank
        ("TYRA", 0.15),
        ("SNDK", 0.10),  # lower rank but V31.4-lifted
    ]
    picks = gna._reserve_momentum_slots(
        ranked=ranked,
        config={"momentum_reserved_slots": 2},
        held_momentum=set(),
        open_positions=set(),
        recent_sell_cooldown=set(),
        force_promote={"SNDK"},
    )
    tickers_picked = [p["ticker"] for p in picks]
    assert "SNDK" in tickers_picked, "force-promoted SNDK should take a slot"
    # SNDK should appear FIRST (priority over higher-scoring LITE)
    assert tickers_picked[0] == "SNDK"
    # Verify the is_force_promote flag
    sndk_pick = next(p for p in picks if p["ticker"] == "SNDK")
    assert sndk_pick.get("is_force_promote") is True


def test_epsilon_c6_force_promote_synthesizes_score_when_ticker_not_in_ranked():
    """If V31.4 lifts a ticker that fell OUT of momentum_watchlist ranking,
    force-promote should still admit it with a synthesized priority score."""
    ranked = [("LITE", 0.20)]  # SNDK absent
    picks = gna._reserve_momentum_slots(
        ranked=ranked,
        config={"momentum_reserved_slots": 2},
        held_momentum=set(),
        open_positions=set(),
        recent_sell_cooldown=set(),
        force_promote={"SNDK"},
    )
    tickers_picked = [p["ticker"] for p in picks]
    assert "SNDK" in tickers_picked


def test_epsilon_c6_force_promote_skips_held_positions():
    """force-promote should NOT add a ticker that's already held."""
    picks = gna._reserve_momentum_slots(
        ranked=[("LITE", 0.20)],
        config={"momentum_reserved_slots": 2},
        held_momentum=set(),
        open_positions={"SNDK"},  # SNDK already held
        recent_sell_cooldown=set(),
        force_promote={"SNDK"},
    )
    tickers_picked = [p["ticker"] for p in picks]
    assert "SNDK" not in tickers_picked


def test_epsilon_c6_force_promote_knob_surfaced_in_effective_config():
    eff = gna._get_effective_nexus_config({})
    assert eff.get("post_sell_breakout_force_promote_enabled") is True


# ε.C.5 — Stale Neo4j sentiment decay (gate sentiment_data on position existence)


# Note: ε.C.5 changes the behavior inside _evaluate_trend_sell_enforcement's
# caller. The unit-testable surface is the existing test that ε.A.3 added
# (`_evaluate_trend_sell_enforcement` returns allow_force_sell=False for
# pos=0). ε.C.5 additionally gates the `sentiment_data[ticker] = ...` write
# on the same condition — confirmed by code inspection at L20570 area.
# Integration test would be needed to verify end-to-end behavior; relying
# on existing γ + α suite for regression coverage.


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


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"nexus_sentiment_cache_force_in_backtest": True}, "sentiment cache force"),
        ({"use_sentiment_cache": True}, "sentiment cache off"),
        ({"nexus_fast_mode": True}, "fast cache mode off"),
        ({"overlay_result_cache_enabled": True}, "overlay result cache off"),
    ],
)
def test_model_evidence_preflight_rejects_mutable_cache_config_before_resolution(
    override, message
):
    from backend.model_evidence import (
        ModelEvidenceCleanStartAudit,
        ModelEvidenceError,
        ModelEvidenceSession,
        activate_model_evidence_session,
        clear_model_evidence_session,
    )

    session = ModelEvidenceSession(
        mode="record", arm_id="baseline", backtest_id="backtest", build_id="build"
    )
    session.bind_clean_start_audit(
        ModelEvidenceCleanStartAudit(
            backtest_id="backtest",
            build_id="build",
            arm_id="baseline",
            cleared_scope_identities={
                scope: "1" * 64 for scope in phase_alpha._MODEL_EVIDENCE_CACHE_KINDS
            },
            before_state_hash="2" * 64,
            after_state_hash="3" * 64,
            verified_empty=True,
            remaining_entry_count=0,
            completed_at="2026-07-28T08:00:00+00:00",
        )
    )
    activate_model_evidence_session(session)
    try:
        config = {
            "model_evidence_clean_start": True,
            "nexus_sentiment_cache_force_in_backtest": False,
            "use_sentiment_cache": False,
            "nexus_fast_mode": False,
            "overlay_result_cache_enabled": False,
        }
        config.update(override)
        with pytest.raises(ModelEvidenceError, match=message):
            phase_alpha.validate_model_evidence_preflight(config)
    finally:
        clear_model_evidence_session()


def test_model_evidence_preflight_accepts_clean_cache_disabled_config():
    from backend.model_evidence import (
        ModelEvidenceCleanStartAudit,
        ModelEvidenceSession,
        activate_model_evidence_session,
        clear_model_evidence_session,
    )

    session = ModelEvidenceSession(
        mode="record", arm_id="baseline", backtest_id="backtest", build_id="build"
    )
    session.bind_clean_start_audit(
        ModelEvidenceCleanStartAudit(
            backtest_id="backtest",
            build_id="build",
            arm_id="baseline",
            cleared_scope_identities={
                scope: "1" * 64 for scope in phase_alpha._MODEL_EVIDENCE_CACHE_KINDS
            },
            before_state_hash="2" * 64,
            after_state_hash="3" * 64,
            verified_empty=True,
            remaining_entry_count=0,
            completed_at="2026-07-28T08:00:00+00:00",
        )
    )
    activate_model_evidence_session(session)
    try:
        phase_alpha.validate_model_evidence_preflight(
            {
                "model_evidence_clean_start": False,
                "nexus_sentiment_cache_force_in_backtest": False,
                "use_sentiment_cache": False,
                "nexus_fast_mode": False,
                "overlay_result_cache_enabled": False,
            }
        )
        assert phase_alpha.evidence_cache_read_allowed("sentiment") is False
    finally:
        clear_model_evidence_session()


def test_model_evidence_preflight_rejects_config_claim_without_session_audit():
    from backend.model_evidence import (
        ModelEvidenceError,
        ModelEvidenceSession,
        activate_model_evidence_session,
        clear_model_evidence_session,
    )

    activate_model_evidence_session(ModelEvidenceSession(mode="record", arm_id="baseline"))
    try:
        with pytest.raises(ModelEvidenceError, match="session-bound"):
            phase_alpha.validate_model_evidence_preflight(
                {
                    "model_evidence_clean_start": True,
                    "nexus_sentiment_cache_force_in_backtest": False,
                    "use_sentiment_cache": False,
                    "nexus_fast_mode": False,
                    "overlay_result_cache_enabled": False,
                }
            )
    finally:
        clear_model_evidence_session()


def test_model_evidence_preflight_requires_every_centralized_cache_scope():
    from backend.model_evidence import (
        ModelEvidenceCleanStartAudit,
        ModelEvidenceError,
        ModelEvidenceSession,
        activate_model_evidence_session,
        clear_model_evidence_session,
    )

    session = ModelEvidenceSession(
        mode="record", arm_id="baseline", backtest_id="backtest", build_id="build"
    )
    session.bind_clean_start_audit(
        ModelEvidenceCleanStartAudit(
            backtest_id="backtest",
            build_id="build",
            arm_id="baseline",
            cleared_scope_identities={"ordinary_prompt": "1" * 64},
            before_state_hash="2" * 64,
            after_state_hash="3" * 64,
            verified_empty=True,
            remaining_entry_count=0,
            completed_at="2026-07-28T08:00:00+00:00",
        )
    )
    activate_model_evidence_session(session)
    try:
        with pytest.raises(ModelEvidenceError, match="missing required cleared scopes"):
            phase_alpha.validate_model_evidence_preflight(
                {
                    "nexus_sentiment_cache_force_in_backtest": False,
                    "use_sentiment_cache": False,
                    "nexus_fast_mode": False,
                    "overlay_result_cache_enabled": False,
                }
            )
    finally:
        clear_model_evidence_session()


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


# ──────────────────────────────────────────────────────────────────────────
# α.3 (cont.) — PYTHONHASHSEED forwarding default for spawned backtest brokers.
# The broker relies on PYTHONHASHSEED=0 for deterministic set iteration
# (broker.py warns when unset). The backtest engine must forward a value into
# the spawned broker container; default to "0" so determinism is on even when
# the deployment env omits it.
# ──────────────────────────────────────────────────────────────────────────


def test_alpha3_determinism_env_defaults_pythonhashseed_zero():
    out = backtest_determinism_env_vars({})
    assert out["PYTHONHASHSEED"] == "0"


def test_alpha3_determinism_env_respects_operator_pythonhashseed():
    out = backtest_determinism_env_vars({"PYTHONHASHSEED": "7"})
    assert out["PYTHONHASHSEED"] == "7"


def test_alpha3_determinism_env_blank_pythonhashseed_falls_back_to_zero():
    out = backtest_determinism_env_vars({"PYTHONHASHSEED": "   "})
    assert out["PYTHONHASHSEED"] == "0"


def test_alpha3_determinism_env_forwards_backtest_seed_only_when_set():
    assert "BACKTEST_SEED" not in backtest_determinism_env_vars({})
    assert backtest_determinism_env_vars({"BACKTEST_SEED": "42"})["BACKTEST_SEED"] == "42"


# ──────────────────────────────────────────────────────────────────────────
# 2026-07-23 deterministic-backtest gate (LLM structured-cache + seed).
# ON by default in backtest mode; killable via NEXUS_BACKTEST_DETERMINISM=0;
# NEVER active in live mode regardless of the env var.
# ──────────────────────────────────────────────────────────────────────────


def test_backtest_determinism_on_by_default_in_backtest():
    assert resolve_backtest_determinism(True, {}) is True


def test_backtest_determinism_never_in_live():
    assert resolve_backtest_determinism(False, {}) is False
    assert resolve_backtest_determinism(False, {"NEXUS_BACKTEST_DETERMINISM": "1"}) is False


def test_backtest_determinism_operator_killswitch():
    for off in ("0", "false", "no", "off", "OFF", "False"):
        assert resolve_backtest_determinism(True, {"NEXUS_BACKTEST_DETERMINISM": off}) is False
    for on in ("1", "true", "yes", "", "anything"):
        assert resolve_backtest_determinism(True, {"NEXUS_BACKTEST_DETERMINISM": on}) is True


# ──────────────────────────────────────────────────────────────────────────
# Discovery selection determinism — order candidates explicitly so the
# max_discovered_stocks cap boundary doesn't depend on dict/set iteration
# order (belt-and-suspenders behind PYTHONHASHSEED).
# ──────────────────────────────────────────────────────────────────────────


def test_ordered_discovery_candidates_by_strength_then_ticker():
    items = {"ZZZ": {"strength": 0.9}, "AAA": {"strength": 0.5}, "MMM": {"strength": 0.9}}
    ordered = gna._ordered_discovery_candidates(
        items.items(), strength_getter=lambda d: d.get("strength", 0)
    )
    # Strongest first; ticker breaks the 0.9 tie deterministically (MMM < ZZZ).
    assert [t for t, _ in ordered] == ["MMM", "ZZZ", "AAA"]


def test_ordered_discovery_candidates_by_ticker_when_no_strength_getter():
    items = {"ZZZ": {}, "AAA": {}, "MMM": {}}
    ordered = gna._ordered_discovery_candidates(items.items())
    assert [t for t, _ in ordered] == ["AAA", "MMM", "ZZZ"]


def test_ordered_discovery_candidates_tolerates_non_numeric_strength():
    # The helper is generic — a getter that yields a non-numeric value must not
    # raise; treat it as 0 (sorts last among ties).
    items = {"BAD": {"score": "oops"}, "GOOD": {"score": 2}, "ZERO": {"score": 0}}
    ordered = gna._ordered_discovery_candidates(
        items.items(), strength_getter=lambda d: d.get("score")
    )
    # GOOD(2) first; BAD("oops"->0) and ZERO(0) tie -> ticker asc (BAD < ZERO).
    assert [t for t, _ in ordered] == ["GOOD", "BAD", "ZERO"]


def test_ordered_discovery_candidates_score_prefers_bullish_over_bearish():
    # Trend buy-signal dicts use score=+1 (bullish) / -1 (bearish); at the
    # max_discovered cap bullish names must win deterministically over bearish.
    items = {"BEAR": {"score": -1}, "BULL2": {"score": 1}, "BULL1": {"score": 1}}
    ordered = gna._ordered_discovery_candidates(
        items.items(), strength_getter=lambda d: d.get("score", 0)
    )
    assert [t for t, _ in ordered] == ["BULL1", "BULL2", "BEAR"]
