"""Ordering and set-membership pins for the graph_nexus_analysis port.

The (latest_observation_date DESC, id DESC) window at
``_retrieve_historical_analogs`` feeds an LLM prompt. Its `id` tiebreak
decides MEMBERSHIP, not just order, so a collation that differs from
RethinkDB's bytewise comparison silently changes which analogs reach the
prompt. Only real Postgres can prove a collation; FakeStore cannot.
"""
import os

import pytest

TABLE = "GraphNexusTradeOutcomes"

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="collation can only be proven against real Postgres",
)


def test_tiebreak_order_is_bytewise_desc(store):
    """Every row shares one latest_observation_date, so `id DESC` alone decides
    which 3 of 5 survive .limit(3), i.e. the collation decides MEMBERSHIP.

    Bytewise, '|' is 0x7C: after every digit, after 'Z' (0x5A), and after
    lowercase 'b' (0x62) too. Under en_US.UTF-8 punctuation is weighted
    differently and 'alpaca-mainb' would win the window instead — which is
    exactly the silent failure this test exists to catch.
    """
    from db import P

    ids = ["alpaca-main|a", "alpaca-mainZ", "alpaca-main9",
           "alpaca-main|Z", "alpaca-mainb"]
    for rid in ids:
        store.insert(TABLE, {"id": rid, "instance_id": "x",
                             "latest_observation_date": "2026-08-01",
                             "entry_date": "2026-07-01"})
    sel = store.order_by(
        store.filter(TABLE, P.field("entry_date").lt("2026-08-22")),
        fields=(store.desc("latest_observation_date"), store.desc("id")))
    got = [row["id"] for row in store.run(store.limit(sel, 3))]
    assert got == ["alpaca-main|a", "alpaca-main|Z", "alpaca-mainb"]
    # ... which is exactly Python's bytewise descending sort of the same keys.
    assert got == sorted(ids, key=lambda s: s.encode("utf-8"), reverse=True)[:3]


def test_is_in_on_empty_list_is_false_not_error(store):
    from db import P

    store.insert(TABLE, {"id": "1", "instance_id": "x", "event": "merger"})
    assert store.run(store.filter(TABLE, P.field("event").is_in([]))) == []
    assert len(store.run(store.filter(TABLE, P.field("event").is_in(["merger"])))) == 1


def test_undefaulted_entry_date_filter_excludes_rows_lacking_the_key(store):
    """ReQL raised on a row missing `entry_date` (aborting the whole query);
    `doc->>'entry_date' < x` is NULL -> false, so the row is excluded instead.
    Proven safe against live data on 2026-08-22: 0 of 8,967 rows in
    GraphNexusTradeOutcomes lack `entry_date`. This pins the semantics so a
    future writer that omits the key is caught by a test, not by a prompt.
    """
    from db import P

    store.insert(TABLE, {"id": "has", "instance_id": "x",
                         "entry_date": "2026-07-01"})
    store.insert(TABLE, {"id": "lacks", "instance_id": "x"})
    got = [r["id"] for r in
           store.run(store.filter(TABLE, P.field("entry_date").lt("2026-08-01")))]
    assert got == ["has"]
