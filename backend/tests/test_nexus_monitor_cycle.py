"""
Monitor-cycle behavior tests.

The monitor cycle:
  - Iterates currently held positions only.
  - NEVER emits score=1 (buys suppressed).
  - Skips sells when _hold_days_ny(sym) == 0 (same-day-entry block).
  - Skips sells when _hold_days_ny(sym) is None (unknown entry).
  - Updates peak HWM via _resolve_position_peak_state even when no exit fires.
  - Does NOT write _fast_loser_blacklist (side_effect_mode='monitor').
  - Returns the same _nexus_* metadata keys the broker reads.
"""

import os
import sys
from datetime import datetime, timezone, timedelta

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies import graph_nexus_analysis as gna  # noqa: E402
from portfolio_emulator import PortfolioEmulator  # noqa: E402


def _utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _strategy():
    return gna.GraphNexusAnalysis()


def _emulator_with_position(sym, shares, price, entry_ts):
    """Build a portfolio_emulator with a single open buy."""
    pe = PortfolioEmulator(initial_cash=100000.0)
    pe.buy(sym, shares, price, timestamp=entry_ts)
    return pe


@pytest.fixture
def base_config():
    return {
        "_nexus_is_live_mode": True,
        "nexus_dual_cadence_enabled": True,
        "nexus_dual_cadence_monitor_block_same_day_exit": True,
        "use_llm_sentiment": False,
        "max_hold_days": 90,
        "trailing_stop_pct": 8.0,
        "trailing_stop_activation_pct": 10.0,
        "fast_loser_cut_pct": -10.0,
        "max_open_loss_pct": -15.0,
        "cash_reserve_floor_pct": 0.10,
    }


def test_monitor_returns_metadata_keys(base_config):
    """Monitor must populate _nexus_* metadata so broker allocator stays consistent."""
    strat = _strategy()
    pe = _emulator_with_position("AAPL", 10, 150.0, entry_ts=_utc(2026, 4, 28, 14, 30))
    out = strat._run_monitor_cycle(
        symbols_list=["AAPL"],
        prices={"AAPL": 155.0},
        price_history={},
        config=base_config,
        portfolio_emulator=pe,
        strategy_cache={},
        date_key="2026-05-01",
        current_time=_utc(2026, 5, 1, 15, 0),
    )
    assert "_nexus_discovered" in out
    assert "_nexus_position_sizes" in out
    assert out["_nexus_discovered"] == []


def test_monitor_no_buys_emitted(base_config):
    """Monitor must NEVER emit score=1 — only -1 (sell) or 0 (hold)."""
    strat = _strategy()
    pe = _emulator_with_position("AAPL", 10, 150.0, entry_ts=_utc(2026, 4, 25, 14, 30))
    out = strat._run_monitor_cycle(
        symbols_list=["AAPL"],
        prices={"AAPL": 200.0},  # big winner — full cycle might want to add
        price_history={},
        config=base_config,
        portfolio_emulator=pe,
        strategy_cache={},
        date_key="2026-05-01",
        current_time=_utc(2026, 5, 1, 15, 0),
    )
    for sym, entry in out.items():
        if sym.startswith("_nexus_"):
            continue
        assert isinstance(entry, dict)
        assert entry.get("score") in (-1, 0), f"{sym} got score={entry.get('score')}"


def test_same_day_entry_blocks_exit(base_config):
    """Position bought today + price drops past trailing stop → hold (no sell)."""
    strat = _strategy()
    today_ny_morning = _utc(2026, 5, 1, 14, 0)  # 10 AM ET, market open
    # Bought 30 min after open at $150
    pe = _emulator_with_position("AAPL", 10, 150.0, entry_ts=today_ny_morning)
    cache = {}
    out = strat._run_monitor_cycle(
        symbols_list=["AAPL"],
        prices={"AAPL": 100.0},  # -33% drop, would normally trigger every exit
        price_history={},
        config=base_config,
        portfolio_emulator=pe,
        strategy_cache=cache,
        date_key="2026-05-01",
        current_time=_utc(2026, 5, 1, 18, 0),  # 2 PM ET same day
    )
    assert out["AAPL"]["score"] == 0
    assert "same-day" in out["AAPL"]["reason"].lower()


def test_unknown_entry_blocks_exit(base_config):
    """Position with no known entry trade → skip exit (treat as same-day-block)."""
    strat = _strategy()
    pe = PortfolioEmulator(initial_cash=100000.0)
    # Manual injection: position exists but no buy in _trades.
    pe._positions["XYZ"] = 5.0
    out = strat._run_monitor_cycle(
        symbols_list=["XYZ"],
        prices={"XYZ": 50.0},
        price_history={},
        config=base_config,
        portfolio_emulator=pe,
        strategy_cache={},
        date_key="2026-05-01",
        current_time=_utc(2026, 5, 1, 15, 0),
    )
    assert out["XYZ"]["score"] == 0
    assert "unknown" in out["XYZ"]["reason"].lower()


def test_unknown_entry_logs_only_once(base_config):
    """The yellow-log dedup uses (instance, sym, date) tuple in strategy_cache."""
    strat = _strategy()
    pe = PortfolioEmulator(initial_cash=100000.0)
    pe._positions["XYZ"] = 5.0
    cache = {}
    # Two consecutive monitor runs same day — only one log entry expected.
    for _ in range(2):
        strat._run_monitor_cycle(
            symbols_list=["XYZ"],
            prices={"XYZ": 50.0},
            price_history={},
            config=base_config,
            portfolio_emulator=pe,
            strategy_cache=cache,
            date_key="2026-05-01",
            current_time=_utc(2026, 5, 1, 15, 0),
        )
    logged = cache.get("_nexus_unknown_entry_logged")
    assert logged is not None
    # Exactly one entry for (instance, "XYZ", "2026-05-01")
    assert len(logged) == 1


def test_peak_hwm_updates_on_rising_price(base_config):
    """Even with no exit firing, monitor must update peak HWM for trailing stop."""
    strat = _strategy()
    pe = _emulator_with_position("AAPL", 10, 100.0, entry_ts=_utc(2026, 4, 25, 14, 30))
    cache = {}
    strat._run_monitor_cycle(
        symbols_list=["AAPL"],
        prices={"AAPL": 130.0},  # +30% — sets peak
        price_history={},
        config=base_config,
        portfolio_emulator=pe,
        strategy_cache=cache,
        date_key="2026-05-01",
        current_time=_utc(2026, 5, 1, 15, 0),
    )
    peak_keys = [k for k in cache.keys() if k.startswith("_peak_AAPL_")]
    assert peak_keys, "monitor must write at least one _peak_AAPL_<marker> key"
    assert any(float(cache[k]) >= 130.0 for k in peak_keys)


def test_monitor_writes_last_tick_timestamp(base_config):
    """Diagnostic key for operator visibility."""
    strat = _strategy()
    pe = _emulator_with_position("AAPL", 10, 100.0, entry_ts=_utc(2026, 4, 25, 14, 30))
    cache = {}
    now = _utc(2026, 5, 1, 15, 0)
    strat._run_monitor_cycle(
        symbols_list=["AAPL"],
        prices={"AAPL": 105.0},
        price_history={},
        config=base_config,
        portfolio_emulator=pe,
        strategy_cache=cache,
        date_key="2026-05-01",
        current_time=now,
    )
    assert "_nexus_last_monitor_tick_utc" in cache
    assert cache["_nexus_last_monitor_tick_utc"].startswith("2026-05-01T15:00")


def test_monitor_does_not_write_fast_loser_blacklist(base_config):
    """side_effect_mode='monitor' must suppress FLC blacklist writes
    (the post-_finalize_scores decrement loop never runs in monitor mode,
    so any new entries would leak indefinitely)."""
    strat = _strategy()
    # Big loss that would trigger fast-loser in full mode.
    pe = _emulator_with_position("AAPL", 10, 200.0, entry_ts=_utc(2026, 4, 28, 14, 30))
    cache = {}
    strat._run_monitor_cycle(
        symbols_list=["AAPL"],
        prices={"AAPL": 150.0},  # -25% loss
        price_history={"AAPL": [200.0] * 20},
        config=base_config,
        portfolio_emulator=pe,
        strategy_cache=cache,
        date_key="2026-05-01",
        current_time=_utc(2026, 5, 1, 15, 0),
    )
    # No new blacklist entry written by monitor.
    blacklist = cache.get("_fast_loser_blacklist", {}) or {}
    assert "AAPL" not in blacklist


def test_old_position_exit_allowed_when_not_same_day(base_config):
    """Position bought 3 days ago + bad price drop → not blocked by same-day."""
    strat = _strategy()
    three_days_ago = _utc(2026, 4, 28, 14, 30)
    pe = _emulator_with_position("AAPL", 10, 200.0, entry_ts=three_days_ago)
    cache = {}
    out = strat._run_monitor_cycle(
        symbols_list=["AAPL"],
        prices={"AAPL": 100.0},  # -50% from entry — well past stop loss
        price_history={},
        config=base_config,
        portfolio_emulator=pe,
        strategy_cache=cache,
        date_key="2026-05-01",
        current_time=_utc(2026, 5, 1, 15, 0),
    )
    # The same-day-block must NOT fire (position is 3 days old).
    assert out["AAPL"]["score"] in (-1, 0)
    assert "same-day" not in out["AAPL"]["reason"].lower()
    # Also unknown-entry block must NOT fire (we have a real entry trade).
    assert "unknown" not in out["AAPL"]["reason"].lower()


def test_buy_late_evening_et_then_monitor_next_morning_not_same_day(base_config):
    """Buy at 22:00 ET day N (= 02:00 UTC day N+1 EST / 03:00 UTC day N+1 EDT).
    Monitor at 10:00 ET day N+1 → _hold_days_ny=1 → NOT same-day-block."""
    strat = _strategy()
    # 22:00 ET on Apr 30 (EDT, UTC-4) = 02:00 UTC May 1.
    bought_late_evening_et = _utc(2026, 5, 1, 2, 0)
    pe = _emulator_with_position("AAPL", 10, 100.0, entry_ts=bought_late_evening_et)
    cache = {}
    out = strat._run_monitor_cycle(
        symbols_list=["AAPL"],
        prices={"AAPL": 70.0},  # -30% drop, would normally trigger exit
        price_history={},
        config=base_config,
        portfolio_emulator=pe,
        strategy_cache=cache,
        date_key="2026-05-01",
        current_time=_utc(2026, 5, 1, 14, 0),  # 10:00 ET May 1
    )
    # NY-local: bought day N (Apr 30), monitoring day N+1 (May 1) → 1 day held.
    # Must NOT be same-day-blocked.
    assert "same-day" not in out["AAPL"]["reason"].lower()


def test_unknown_entry_dedup_uses_stable_instance_id(base_config):
    """Dedup key uses runtime_instance_id (stable across restarts), not id(self)."""
    pe = PortfolioEmulator(initial_cash=100000.0)
    pe._positions["XYZ"] = 5.0
    config = dict(base_config)
    config["runtime_instance_id"] = "test-instance-123"
    cache = {}
    # Two separate strategy instances (simulating restarts) using same config.
    for _ in range(2):
        gna.GraphNexusAnalysis()._run_monitor_cycle(
            symbols_list=["XYZ"],
            prices={"XYZ": 50.0},
            price_history={},
            config=config,
            portfolio_emulator=pe,
            strategy_cache=cache,
            date_key="2026-05-01",
            current_time=_utc(2026, 5, 1, 15, 0),
        )
    logged = cache.get("_nexus_unknown_entry_logged")
    # Dedup key uses runtime_instance_id, so both calls produce SAME key
    # (otherwise post-restart re-log spam would happen). One entry total.
    assert len(logged) == 1
    # Key format is "instance|sym|date" string (NOT a tuple — survives RethinkDB).
    key = next(iter(logged))
    assert isinstance(key, str)
    assert key == "test-instance-123|XYZ|2026-05-01"


# --- 2026-07-31: risk-exit EXECUTION gate ---
#
# The monitor computing SELL is not enough — the symbol must reach
# _nexus_sell_enforcement or the broker never acts on it. In the doc-179
# production config the enabling flag lived only in regime_profiles.bull/
# .recovery, so on the 2026-04-22 CAR cascade the monitor logged SELL on six
# consecutive bars and every one was dropped here; the position sold 22h later
# at $253 instead of $619. nexus_monitor_risk_exit_always_enabled is base-only
# (see test_regime_profile.py) so a regime overlay cannot switch stops off.

def _forced_exit_monitor(strat, cfg):
    """A position deep enough through its trailing stop to force an exit."""
    pe = _emulator_with_position("CAR", 10, 300.0, entry_ts=_utc(2026, 4, 13, 14, 0))
    cache = {}
    # Establish the peak, then collapse the price.
    strat._run_monitor_cycle(
        symbols_list=["CAR"], prices={"CAR": 752.62}, price_history={},
        config=cfg, portfolio_emulator=pe, strategy_cache=cache,
        date_key="2026-04-22", current_time=_utc(2026, 4, 22, 13, 0),
    )
    return strat._run_monitor_cycle(
        symbols_list=["CAR"], prices={"CAR": 535.55}, price_history={},
        config=cfg, portfolio_emulator=pe, strategy_cache=cache,
        date_key="2026-04-22", current_time=_utc(2026, 4, 22, 17, 0),
    )


def test_risk_exit_not_enforced_by_default(base_config):
    """Default OFF: the sell may be computed but must not be enforced."""
    out = _forced_exit_monitor(_strategy(), dict(base_config))
    assert out.get("_nexus_sell_enforcement") == []


def test_risk_exit_enforced_by_the_always_flag(base_config):
    """The regime-independent flag enforces it even with the legacy
    (overlay-scoped) flag absent — which is the whole point of the fix."""
    cfg = dict(base_config)
    cfg["nexus_monitor_risk_exit_always_enabled"] = True
    out = _forced_exit_monitor(_strategy(), cfg)
    forced = [s for s, e in out.items()
              if not s.startswith("_nexus_") and isinstance(e, dict)
              and e.get("score") == -1 and e.get("_forced_exit")]
    if forced:
        assert "CAR" in out.get("_nexus_sell_enforcement", []), (
            "monitor forced an exit but it never reached sell enforcement")


def test_legacy_flag_still_works(base_config):
    """The pre-existing flag keeps its behaviour — this is additive."""
    cfg = dict(base_config)
    cfg["nexus_monitor_risk_exit_execution_enabled"] = True
    out = _forced_exit_monitor(_strategy(), cfg)
    forced = [s for s, e in out.items()
              if not s.startswith("_nexus_") and isinstance(e, dict)
              and e.get("score") == -1 and e.get("_forced_exit")]
    if forced:
        assert "CAR" in out.get("_nexus_sell_enforcement", [])
