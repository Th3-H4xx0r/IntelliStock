"""A secondary index omits the documents that have no value for it.

RethinkDB builds a secondary index by running the index function over every
document: a document that lacks the field -- or whose index function returns
null -- is simply not in the index. ``order_by(index=...)``,
``between(index=...)`` and ``get_all(index=...)`` read THROUGH the index, so
those documents are invisible to all three, at either sort direction.

Postgres has no such rule. A generated column is NULL for both cases and the
row is still in the table, so an index-driven read returned it -- at the FRONT
of a DESC scan, because Postgres sorts NULLs first descending. That is a
silent, ordering-dependent extra row in every ported call site.
"""
import pytest

from db import schema
from db import pool as dbpool
from db.fake import FakeStore

from .conftest import PG_TEST_DSN, requires_pg

_TABLES = ["Users", "BacktestResults"]

# id -> doc. "u3" has no username key at all; "u4" has it explicitly null.
_USERS = (
    ("u1", '{"id":"u1","username":"alice"}'),
    ("u2", '{"id":"u2","username":"bob"}'),
    ("u3", '{"id":"u3"}'),
    ("u4", '{"id":"u4","username":null}'),
)

_USER_DOCS = (
    {"id": "u1", "username": "alice"},
    {"id": "u2", "username": "bob"},
    {"id": "u3"},
    {"id": "u4", "username": None},
)


@pytest.fixture
def seeded(pg_schema):
    schema.ensure_schema(tables=_TABLES)
    with dbpool.cursor() as cur:
        for rid, doc in _USERS:
            cur.execute('INSERT INTO "Users" (id, doc) VALUES (%s,%s)', (rid, doc))
    return pg_schema


@pytest.fixture(params=["fake", "real"])
def s(request):
    """The same assertions against FakeStore and against real Postgres."""
    if request.param == "fake":
        store = FakeStore()
        store.insert("Users", [dict(d) for d in _USER_DOCS])
        return store
    if not PG_TEST_DSN:
        pytest.skip("PG_TEST_DSN not set")
    request.getfixturevalue("seeded")
    from db import store as real
    return real


def _ids(rows):
    return [r["id"] for r in rows]


def test_order_by_index_desc_omits_missing_and_null(s):
    rows = s.run(s.order_by("Users", index="username", desc=True))
    assert _ids(rows) == ["u2", "u1"]


def test_order_by_index_asc_omits_missing_and_null(s):
    rows = s.run(s.order_by("Users", index="username"))
    assert _ids(rows) == ["u1", "u2"]


def test_order_by_index_count_omits_missing_and_null(s):
    assert s.count(s.order_by("Users", index="username")) == 2


def test_unbounded_between_on_an_index_omits_missing_and_null(s):
    sel = s.between("Users", s.MINVAL, s.MAXVAL, index="username")
    assert sorted(_ids(s.run(sel))) == ["u1", "u2"]


def test_half_bounded_between_on_an_index_omits_missing_and_null(s):
    sel = s.between("Users", "a", s.MAXVAL, index="username")
    assert sorted(_ids(s.run(sel))) == ["u1", "u2"]


def test_get_all_on_an_index_never_matches_a_missing_field(s):
    assert s.get_all("Users", "alice", index="username") == [
        {"id": "u1", "username": "alice"}]
    assert s.get_all("Users", None, index="username") == []


def test_order_by_index_still_orders_present_values_bytewise(s):
    s.insert("Users", {"id": "u5", "username": "Zeta"})
    rows = s.run(s.order_by("Users", index="username"))
    # COLLATE "C": uppercase 'Z' (0x5A) sorts before lowercase 'a' (0x61).
    assert _ids(rows) == ["u5", "u1", "u2"]


def test_iter_over_an_index_ordered_selection_omits_them(s):
    assert _ids(list(s.iter(s.order_by("Users", index="username")))) == ["u1", "u2"]


@requires_pg
def test_compound_index_order_omits_a_row_missing_the_indexed_key(seeded):
    """BacktestResults' "list_ts" index is coalesce(doc->>'timestamp',''): the
    coalesce exists so the SQL expression matches the index, and it made a
    document with NO timestamp sort as the empty string instead of vanishing.
    """
    from db import store
    store.insert("BacktestResults", [
        {"id": 1, "timestamp": "2026-01-02T00:00:00Z"},
        {"id": 2, "timestamp": "2026-01-01T00:00:00Z"},
        {"id": 3},
        {"id": 4, "timestamp": None},
    ])
    rows = store.run(store.order_by("BacktestResults", index="list_ts", desc=True))
    assert [r["id"] for r in rows] == [1, 2]


@requires_pg
def test_the_rows_are_still_there_by_primary_key(seeded):
    """Omission is an INDEX property, not a delete: get() still finds them."""
    from db import store
    assert store.get("Users", "u3") == {"id": "u3"}
    assert store.get("Users", "u4") == {"id": "u4", "username": None}
    assert store.count("Users") == 4


@requires_pg
def test_the_presence_filter_still_uses_the_index(seeded):
    """An IS NOT NULL guard must not turn the ordered read into a seq scan."""
    from db import store
    with dbpool.cursor() as cur:
        cur.execute("SET LOCAL enable_seqscan = off")
        sel = store.order_by("Users", index="username", desc=True)
        sql_, params = sel.to_sql()
        cur.execute("EXPLAIN " + sql_, params)
        plan = "\n".join(r["QUERY PLAN"] for r in cur.fetchall())
    assert "Users_username_idx" in plan, plan


def test_a_non_indexed_field_order_is_unaffected(s):
    """order_by(fields=...) is ReQL's client-side sort, not an index read."""
    rows = s.run(s.order_by("Users", fields=[s.asc("id")]))
    assert _ids(rows) == ["u1", "u2", "u3", "u4"]
