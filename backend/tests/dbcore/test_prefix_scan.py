"""The LIKE form and the >=/< range form must return identical rows.

clear_instance_state.py:100-104 records the 2026-05-25 regression: exact-only
matching found ZERO scoped rows, so a full clear was a silent no-op. Rows are
written under a config-hash-scoped id ("main|<hash>"), not the bare instance
id, and NexusRuntimeState uses a COLON suffix while everything else uses a
pipe.
"""
import pytest

from db import pool as dbpool
from db import schema, store

from .conftest import requires_pg

# The 18 full_instance targets' prefix shapes, from clear_instance_state.py.
_PREFIX_CASES = [
    ("GraphNexusTradeContexts", "instance_id", "main|"),
    ("GraphNexusOutcomes", "instance_id", "main|"),
    ("GraphNexusDiscoveredStocks", "instance_id", "main|"),
    ("GraphNexusMarketTrends", "instance_id", "main|"),
    ("GraphNexusActiveEvents", "instance_id", "main|"),
    ("GraphNexusActiveEventHistory", "instance_id", "main|"),
    ("GraphNexusActiveEventMaintenance", "instance_id", "main|"),
    ("GraphNexusOutcomeSeries", "instance_id", "main|"),
    ("GraphNexusAnalystPanel", "instance_id", "main|"),
    ("GraphNexusTradeOutcomes", "instance_id", "main|"),
    ("GraphNexusRotationCooldown", "id", "main|"),
    ("GraphNexusLearningCache", "id", "cleanup_done|main|"),
    ("GraphNexusDiscoverySnapshots", "id", "main|"),
    ("NexusRuntimeState", "id", "main:"),          # COLON, not pipe
    ("LiveBootAudit", "id", "main|"),
]


def test_escape_like_escapes_the_three_like_metacharacters():
    assert store.escape_like(r"a%b_c\d") == r"a\%b\_c\\d"


def test_escape_like_leaves_pipe_alone():
    # Today's code escapes | as [|] for the REGEX form; LIKE needs no escape.
    assert store.escape_like("main|") == "main|"


@requires_pg
@pytest.mark.parametrize("table,field,prefix", _PREFIX_CASES)
def test_like_form_and_range_form_agree(pg_schema, table, field, prefix):
    schema.ensure_schema(tables=[table])
    scoped = prefix + "3df5616cacc43b413a6eaf21|2026-03-02|AACI"
    others = ["maintenance|x", "other|y", prefix.rstrip("|:")]  # near-misses
    with dbpool.cursor() as cur:
        for i, value in enumerate([scoped] + others):
            rid = "r%d" % i if field != "id" else value
            cur.execute('INSERT INTO %s (id, doc) VALUES (%%s, %%s)'
                        % schema.quoted(table),
                        (rid, '{"id":"%s","%s":"%s"}' % (rid, field, value)))
    like_rows = store.run(store.filter(table, store.P.field(field).starts_with(prefix)))
    range_rows = store.run(store.between(table, prefix, prefix + "￿",
                                         index=field, right_bound="closed"))
    assert {r["id"] for r in like_rows} == {r["id"] for r in range_rows}
    assert len(like_rows) == 1, "the scoped row must match: %r" % prefix


@requires_pg
def test_a_prefix_containing_like_metacharacters_is_escaped(pg_schema):
    schema.ensure_schema(tables=["NexusRuntimeState"])
    with dbpool.cursor() as cur:
        for rid in ("100%|a", "100X|a"):
            cur.execute('INSERT INTO "NexusRuntimeState" (id, doc) VALUES (%s,%s)',
                        (rid, '{"id":"%s"}' % rid))
    got = store.run(store.filter("NexusRuntimeState",
                                 store.P.field("id").starts_with("100%")))
    assert [r["id"] for r in got] == ["100%|a"]
