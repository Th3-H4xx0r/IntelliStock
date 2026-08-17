"""Two arms that did not start from the same state cannot be compared, whatever they returned.

Measured on this system: bt 333727 vs bt 453789 shared 4 of 20 traded names (20% overlap) on
the same document, window, instance and cash, ONE config flag apart. bt 453789 vs bt 749060
scored 23%. Both carried the isolation recipe the handoffs recommend. The recipe was not
enough, because per-instance Nexus rows survive between runs and seed the next discovery.

These tests exercise the real `frozen_paired_state` primitives through `paired_state_attest` —
which is what finally gives that module a caller.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from frozen_paired_state import FrozenStateError  # noqa: E402
from paired_state_attest import (  # noqa: E402
    ATTESTED_TABLES,
    compare_arm_starts,
    is_cold,
    state_fingerprint,
    table_fingerprint,
)


def _cold_tables():
    return {name: [] for name in ATTESTED_TABLES}


def _warm_tables(ticker="AGQ"):
    t = _cold_tables()
    t["GraphNexusDiscoveredStocks"] = [
        {"id": f"inst_{ticker}", "ticker": ticker, "source": "momentum"}
    ]
    return t


# --------------------------------------------------------------------------
# fingerprinting
# --------------------------------------------------------------------------
def test_identical_rows_hash_identically_regardless_of_order():
    """Row order out of RethinkDB is not stable; the digest must not depend on it."""
    a = [{"id": "b", "v": 2}, {"id": "a", "v": 1}]
    b = [{"id": "a", "v": 1}, {"id": "b", "v": 2}]
    assert table_fingerprint(a) == table_fingerprint(b)


def test_a_changed_value_changes_the_digest():
    assert (table_fingerprint([{"id": "a", "v": 1}])
            != table_fingerprint([{"id": "a", "v": 2}]))


def test_volatile_bookkeeping_is_ignored():
    """Wall-clock stamps differ between every pair of runs and steer nothing.

    Attesting them would make every comparison DIVERGED for a reason that carries no
    information, which is the fastest way to get a safety check switched off.
    """
    a = [{"id": "x", "v": 1, "updated_at": "2026-08-16T10:00:00Z", "size_bytes": 10}]
    b = [{"id": "x", "v": 1, "updated_at": "2026-08-16T19:00:00Z", "size_bytes": 99}]
    assert table_fingerprint(a) == table_fingerprint(b)


def test_a_real_state_difference_is_NOT_ignored():
    """Guard against over-stripping: the AGQ-class carried discovery must still show."""
    a = [{"id": "i_AGQ", "ticker": "AGQ", "updated_at": "2026-08-16T10:00:00Z"}]
    b = [{"id": "i_AGQ", "ticker": "SNDK", "updated_at": "2026-08-16T10:00:00Z"}]
    assert table_fingerprint(a) != table_fingerprint(b)


def test_absent_and_empty_are_not_the_same_claim():
    """"never read" and "read and cold" are different evidence."""
    absent = state_fingerprint({})
    empty = state_fingerprint(_cold_tables())
    assert absent["tables"]["LiveState"] == {"absent": True}
    assert empty["tables"]["LiveState"]["rows"] == 0
    assert absent["bundle_sha256"] != empty["bundle_sha256"]
    assert is_cold(empty) is True
    assert is_cold(absent) is False


def test_duplicate_primary_keys_fail_closed():
    with pytest.raises(FrozenStateError):
        table_fingerprint([{"id": "a", "v": 1}, {"id": "a", "v": 2}])


# --------------------------------------------------------------------------
# the verdict
# --------------------------------------------------------------------------
def test_two_cold_arms_are_comparable():
    r = compare_arm_starts(state_fingerprint(_cold_tables()),
                           state_fingerprint(_cold_tables()))
    assert r["verdict"] == "IDENTICAL_COLD"
    assert r["diverged_tables"] == []


def test_carried_discovery_state_is_caught():
    """The AGQ case: one arm inherits a discovered name, the other does not.

    That single row is worth ~10pp of return on this system — bt 873929 (+16.41%) vs
    bt 523085 (+6.00%), identical config, the whole gap one name.
    """
    r = compare_arm_starts(state_fingerprint(_cold_tables()),
                           state_fingerprint(_warm_tables()))
    assert r["verdict"] == "DIVERGED"
    assert "GraphNexusDiscoveredStocks" in r["diverged_tables"]


def test_arms_warm_with_DIFFERENT_content_diverge():
    r = compare_arm_starts(state_fingerprint(_warm_tables("AGQ")),
                           state_fingerprint(_warm_tables("SNDK")))
    assert r["verdict"] == "DIVERGED"


def test_identical_WARM_arms_are_still_refused_by_default():
    """Byte-identical is not enough if it is not cold.

    Two runs sharing a warm state share whatever that state biased, and the match will not
    survive the next run — so it is not reproducible evidence.
    """
    warm = state_fingerprint(_warm_tables())
    assert compare_arm_starts(warm, warm)["verdict"] == "DIVERGED"
    assert compare_arm_starts(warm, warm, require_cold=False)["verdict"] == "IDENTICAL_WARM"


def test_the_divergence_reason_names_the_tables():
    """An unexplained refusal gets overridden; a specific one gets fixed."""
    r = compare_arm_starts(state_fingerprint(_cold_tables()),
                           state_fingerprint(_warm_tables()))
    assert "GraphNexusDiscoveredStocks" in r["reason"]


def test_bad_input_fails_closed():
    with pytest.raises(FrozenStateError):
        state_fingerprint([])
    with pytest.raises(FrozenStateError):
        compare_arm_starts({}, None)


# --------------------------------------------------------------------------
# the decision-state projection (added after the live run hit `manifest_too_large`)
# --------------------------------------------------------------------------
def test_a_large_blob_is_digested_not_embedded():
    """`frozen_paired_state` caps a manifest at 16MB and REJECTED the raw rows.

    A whole NexusStrategyCache.cache_json body blows that cap outright, so big strings are
    replaced by their own digest — identity preserved, payload dropped.
    """
    big = "x" * 5000
    fp = table_fingerprint([{"id": "a", "cache_json": big}])
    assert fp["sha256"].startswith("sha256:")

    # identity is preserved: a DIFFERENT blob must still change the fingerprint
    other = table_fingerprint([{"id": "a", "cache_json": "y" * 5000}])
    assert fp != other


def test_nested_payloads_are_digested_too():
    fp = table_fingerprint([{"id": "a", "trends": [{"t": i} for i in range(200)]}])
    other = table_fingerprint([{"id": "a", "trends": [{"t": i} for i in range(199)]}])
    assert fp != other


def test_telemetry_tables_are_deliberately_NOT_attested():
    """Recorded outputs steer nothing and would fail pairs for no reason.

    GraphNexusTradeContexts alone reached 269k rows on this system; attesting it also
    blew the manifest cap on the first live run.
    """
    for name in ("GraphNexusTradeContexts", "GraphNexusOutcomes",
                 "GraphNexusOutcomeSeries", "GraphNexusTradeOutcomes",
                 "GraphNexusAnalystPanel"):
        assert name not in ATTESTED_TABLES, name


def test_a_realistic_strategy_cache_row_does_not_blow_the_cap():
    """Regression for the live failure: 8 rows x ~1MB each must fingerprint fine."""
    rows = [{"id": f"inst|hash|{i}", "instance_id": "inst",
             "cache_json": "z" * 1_000_000} for i in range(8)]
    fp = table_fingerprint(rows)
    assert fp["rows"] == 8


# --------------------------------------------------------------------------
# mode-aware coldness (added after clear_instance_state was found to PRESERVE
# origin="backtest" snapshots in every scope, by design)
# --------------------------------------------------------------------------
def _tables_with_backtest_snapshots():
    t = _cold_tables()
    t["NexusStrategyCache"] = [
        {"id": f"inst|graph_nexus_analysis|hash|backtest|2026-0{i}-01",
         "instance_id": "inst", "origin": "backtest", "cache_json": "x" * 900}
        for i in range(1, 9)
    ]
    return t


def test_backtest_snapshots_do_not_block_a_cold_backtest_verdict():
    """clear_instance_state PRESERVES these in every scope, so without this the
    instance attests cold=False forever and the check becomes unusable.

    Justified empirically, not by taste: the snapshot boot path logs
    `[snapshot] decision:` / `[snapshot] hydrated`, and neither appears in ANY backtest
    log examined — it is a live-boot mechanism.
    """
    fp = state_fingerprint(_tables_with_backtest_snapshots(), for_mode="backtest")
    assert fp["tables"]["NexusStrategyCache"]["rows"] == 0
    assert is_cold(fp) is True


def test_the_same_rows_DO_count_for_a_live_boot():
    """A live boot is exactly where that path runs, so there they steer the run."""
    fp = state_fingerprint(_tables_with_backtest_snapshots(), for_mode="live")
    assert fp["tables"]["NexusStrategyCache"]["rows"] == 8
    assert is_cold(fp) is False


def test_a_LIVE_origin_cache_row_still_blocks_a_backtest_cold_verdict():
    """Only origin=backtest is exempt. A live/legacy row is real carried state."""
    t = _cold_tables()
    t["NexusStrategyCache"] = [{"id": "inst|x", "instance_id": "inst", "origin": "live"}]
    assert is_cold(state_fingerprint(t, for_mode="backtest")) is False


def test_an_unknown_mode_fails_closed():
    with pytest.raises(FrozenStateError):
        state_fingerprint(_cold_tables(), for_mode="paper")
