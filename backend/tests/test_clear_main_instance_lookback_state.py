"""Tests for scripts/clear_main_instance_lookback_state.py (Phase 1 extensions)."""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts/ to sys.path so we can import the module under test
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import clear_main_instance_lookback_state as cleaner  # noqa: E402


def test_build_targets_covers_all_14_phase1_tables():
    """Phase 1 design requires all 14 per-instance tables be cleared."""
    targets = cleaner._build_targets("main")
    table_names = {t[0] for t in targets}
    expected = {
        # Original 4 (already cleared by this script before Phase 1)
        "GraphNexusTradeContexts", "GraphNexusOutcomes",
        "NexusRuntimeState", "LiveState",
        # Phase 1 additions
        "NexusStrategyCache", "LiveOrderWAL",
        "GraphNexusDiscoveredStocks", "GraphNexusMarketTrends",
        "GraphNexusRotationCooldown", "GraphNexusTradeOutcomes",
        "GraphNexusLearningCache",
        "GraphNexusDiscoverySnapshots", "GraphNexusOutcomeSeries",
        "GraphNexusAnalystPanel",
    }
    missing = expected - table_names
    assert not missing, f"missing tables in TARGETS: {missing}"


def test_nexus_strategy_cache_target_filters_origin_live_only():
    """The cleanup must NOT delete backtest-origin snapshots."""
    targets = cleaner._build_targets("main")
    nsc = [t for t in targets if t[0] == "NexusStrategyCache"]
    assert len(nsc) == 1, "NexusStrategyCache should appear exactly once"
    criteria = nsc[0][1]
    # Expect at least one filter referencing "origin"="live"
    has_origin_filter = False
    for field, value, mode in criteria:
        if field == "origin" and value == "live":
            has_origin_filter = True
            break
    assert has_origin_filter, f"NexusStrategyCache must filter on origin=live; got {criteria}"


def test_build_targets_substitutes_instance_id():
    """_build_targets must use the passed instance_id, not hardcode 'main'."""
    targets = cleaner._build_targets("my-test-instance")
    # Look at any criterion that references INSTANCE_ID, confirm it picked up the param
    found_instance_ref = False
    for table_name, criteria in targets:
        for field, value, mode in criteria:
            if "my-test-instance" in str(value):
                found_instance_ref = True
                break
        if found_instance_ref:
            break
    assert found_instance_ref, "Targets should reference the passed instance_id"


def test_instance_id_module_constant_is_main():
    """Default INSTANCE_ID constant should still be 'main' for back-compat."""
    assert cleaner.INSTANCE_ID == "main"
