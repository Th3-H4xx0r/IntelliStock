"""The clear_instance_state prefix scan, ported off the "￿" sentinel.

`clear_instance_state.py:100-104` records the 2026-05-25 regression: exact-only
matching found ZERO scoped rows and turned a full clear into a silent no-op.
These tests pin the replacement (`P.field(f).default("").starts_with(v)` and the
`id`-column range form) against that.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from db.store import P                                   # noqa: E402
import clear_instance_state as cis                       # noqa: E402


def test_scoped_prefix_matches_suffixed_ids(store):
    store.insert("GraphNexusTradeContexts", [
        {"id": "alpaca-main|h1|2026-08-01|AAPL", "instance_id": "alpaca-main|h1"},
        {"id": "alpaca-main|h2|2026-08-01|MSFT", "instance_id": "alpaca-main|h2"},
        {"id": "test|h3|2026-08-01|NVDA", "instance_id": "test|h3"},
        {"id": "alpaca-main", "instance_id": "alpaca-main"},
    ])
    sel = store.filter("GraphNexusTradeContexts",
                       P.field("instance_id").default("").starts_with("alpaca-main|"))
    got = sorted(row["id"] for row in store.run(sel))
    assert len(got) == 2                     # NOT zero, NOT one
    assert "test|h3|2026-08-01|NVDA" not in got
    assert "alpaca-main" not in got          # the bare id is the "exact" criterion


def test_full_clear_targets_the_scoped_rows_the_bug_missed(store):
    """The regression itself: the two criteria together must find all 3 rows."""
    store.insert("GraphNexusTradeContexts", [
        {"id": "a1", "instance_id": "alpaca-main|h1", "base_instance_id": "alpaca-main"},
        {"id": "a2", "instance_id": "alpaca-main|h2", "base_instance_id": "alpaca-main"},
        {"id": "a3", "instance_id": "alpaca-main", "base_instance_id": "alpaca-main"},
        {"id": "b1", "instance_id": "test|h3", "base_instance_id": "test"},
    ])
    table, criteria = cis.build_targets("alpaca-main", scope="lookback_only")[0]
    sel = cis._indexed_selection(None, None, table, criteria, "or")
    assert store.count(sel) == 3
    assert store.delete(table, sel)["deleted"] == 3
    assert store.count(table) == 1


def test_like_form_and_range_form_agree_on_every_prefix_target(store):
    """Under COLLATE "C" the LIKE form and the old "￿" range form are the
    same row set. Asserted on every prefix criterion the 18 targets carry."""
    rows_by_table = {}
    for entry in cis.build_targets("alpaca-main", scope="full_instance"):
        table, criteria = entry[0], entry[1]
        for field, val, mode in criteria:
            if mode != "prefix":
                continue
            rows_by_table.setdefault(table, []).extend([
                {"id": "%s|%d" % (val, n), field: "%s%d" % (val, n)}
                for n in range(2)
            ] + [{"id": "zzz-%s-%s" % (table, field), field: "unrelated"}])
    for table, rows in rows_by_table.items():
        deduped = {r["id"]: r for r in rows}
        store.insert(table, list(deduped.values()), conflict="replace")

    for entry in cis.build_targets("alpaca-main", scope="full_instance"):
        table, criteria = entry[0], entry[1]
        for field, val, mode in criteria:
            if mode != "prefix":
                continue
            like_rows = {row["id"] for row in store.run(
                store.filter(table,
                             P.field(field).default("").starts_with(str(val))))}
            range_rows = {row["id"] for row in store.run(
                store.between(table, str(val), str(val) + "￿",
                              index=(None if field == "id" else field),
                              right_bound="closed"))}
            assert like_rows == range_rows, (table, field)


def test_empty_id_set_yields_a_valid_empty_selection(store):
    """The "__no_match_sentinel__" trick is gone: an empty set still counts 0
    and deletes nothing."""
    sel = store.filter("GraphNexusTradeContexts", P.field("id").is_in([]))
    assert store.count(sel) == 0
    assert store.delete("GraphNexusTradeContexts", sel)["deleted"] == 0


def test_prefix_special_chars_are_escaped(store):
    store.insert("GraphNexusTradeContexts", [
        {"id": "1", "instance_id": "a_b|x"},
        {"id": "2", "instance_id": "axb|x"},
        {"id": "3", "instance_id": "100%|x"},
    ])
    sel = store.filter("GraphNexusTradeContexts",
                       P.field("instance_id").default("").starts_with("a_b"))
    assert [row["id"] for row in store.run(sel)] == ["1"]     # `_` is literal
    sel = store.filter("GraphNexusTradeContexts",
                       P.field("instance_id").default("").starts_with("100%"))
    assert [row["id"] for row in store.run(sel)] == ["3"]     # `%` is literal


def test_pipe_needs_no_escaping_in_the_like_form(store):
    """The regex form escaped `|` as `[|]`; LIKE has no such metacharacter."""
    store.insert("GraphNexusTradeContexts", [
        {"id": "1", "instance_id": "main|h"},
        {"id": "2", "instance_id": "mainXh"},
    ])
    sel = store.filter("GraphNexusTradeContexts",
                       P.field("instance_id").default("").starts_with("main|"))
    assert [row["id"] for row in store.run(sel)] == ["1"]


def test_origin_not_backtest_preserves_backtest_snapshots(store):
    store.insert("NexusStrategyCache", [
        {"id": "s1", "instance_id": "alpaca-main", "origin": "live"},
        {"id": "s2", "instance_id": "alpaca-main", "origin": "backtest"},
        {"id": "s3", "instance_id": "alpaca-main"},          # legacy, no origin
        {"id": "s4", "instance_id": "other", "origin": "live"},
    ])
    table, criteria, combine = cis.build_targets(
        "alpaca-main", scope="strategy_cache_only")[0]
    sel = cis._indexed_selection(None, None, table, criteria, combine)
    assert sorted(r["id"] for r in store.run(sel)) == ["s1", "s3"]


def test_build_targets_inventory_is_unchanged():
    """The full-instance target inventory and the (table, field, kind) tuple
    shape are part of the contract, not an implementation detail."""
    targets = cis.build_targets("main", scope="full_instance")
    assert len(targets) == 17
    for entry in targets:
        assert len(entry) in (2, 3)
        for field, _val, mode in entry[1]:
            assert isinstance(field, str)
            assert mode in ("exact", "prefix", "contains", "special")
