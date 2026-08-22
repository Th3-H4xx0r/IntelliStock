"""ORDER BY must be bytewise.

graph_nexus_analysis.py:11843-11858 orders by
(latest_observation_date DESC, id DESC) and takes .limit(80); by mid-window
_update_indefinite_outcomes has rewritten latest_observation_date to the same
value on most rows, so `id` alone decides MEMBERSHIP of the window that feeds
an LLM prompt. A non-bytewise collation silently changes results.
"""
import pytest

from db import pool as dbpool
from db import schema, store

from .conftest import requires_pg

# Real scope-suffixed id shapes: instance | config-hash | date | ticker.
_IDS = [
    "alpaca-main|3df5616cacc43b413a6eaf21|2026-03-02|AACI",
    "alpaca-main|3df5616cacc43b413a6eaf21|2026-03-02|AA",
    "alpaca-main|3df5616cacc43b413a6eaf21|2026-03-02|aaci",
    "alpaca-main|3df5616cacc43b413a6eaf21|2026-03-02|ZZ",
    "alpaca-main|3df5616cacc43b413a6eaf21|2026-03-10|AACI",
    "alpaca_main|3df5616cacc43b413a6eaf21|2026-03-02|AACI",
    "alpaca-main2|3df5616cacc43b413a6eaf21|2026-03-02|AACI",
    "Alpaca-Main|3df5616cacc43b413a6eaf21|2026-03-02|AACI",
]


@pytest.fixture
def contexts(pg_schema):
    schema.ensure_schema(tables=["GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        for rid in _IDS:
            cur.execute(
                'INSERT INTO "GraphNexusTradeContexts" (id, doc) VALUES (%s,%s)',
                (rid, '{"id":"%s","instance_id":"alpaca-main|h",'
                      '"latest_observation_date":"2026-08-01"}' % rid))
    return pg_schema


@requires_pg
def test_order_by_id_desc_is_bytewise(contexts):
    got = store.sql('SELECT id FROM "GraphNexusTradeContexts" '
                    'ORDER BY id COLLATE "C" DESC')
    assert [r["id"] for r in got] == sorted(_IDS, reverse=True)


@requires_pg
def test_pipe_sorts_before_lowercase_and_after_uppercase(contexts):
    """'|' is 0x7C: after 'Z' (0x5A) and after digits, before 'a' (0x61)? No --
    0x7C > 0x61, so '|' sorts AFTER lowercase letters. A locale-aware
    collation would ignore punctuation entirely and reorder these."""
    got = [r["id"] for r in store.sql(
        'SELECT id FROM "GraphNexusTradeContexts" ORDER BY id COLLATE "C" ASC')]
    assert got.index("Alpaca-Main|3df5616cacc43b413a6eaf21|2026-03-02|AACI") == 0
    assert got.index("alpaca_main|3df5616cacc43b413a6eaf21|2026-03-02|AACI") == len(got) - 1


@requires_pg
def test_the_graph_nexus_window_tiebreak_reproduces_python_byte_order(contexts):
    """The exact query shape from graph_nexus_analysis.py:11843-11858."""
    sel = store.order_by(
        store.filter("GraphNexusTradeContexts",
                     store.P.field("instance_id").eq("alpaca-main|h")),
        fields=(store.desc("latest_observation_date"), store.desc("id")))
    got = [r["id"] for r in store.run(store.limit(sel, 80))]
    assert got == sorted(_IDS, key=lambda s: s.encode("utf-8"), reverse=True)


@requires_pg
def test_generated_columns_order_bytewise_without_an_explicit_collate(contexts):
    # COLLATE "C" is on the column itself, so an index scan agrees with the
    # explicit ORDER BY ... COLLATE "C".
    a = [r["id"] for r in store.sql(
        'SELECT id FROM "GraphNexusTradeContexts" ORDER BY id')]
    b = [r["id"] for r in store.sql(
        'SELECT id FROM "GraphNexusTradeContexts" ORDER BY id COLLATE "C"')]
    assert a == b
