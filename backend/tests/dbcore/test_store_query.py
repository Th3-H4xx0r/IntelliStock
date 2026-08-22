import pytest

from db import pool as dbpool
from db import schema, store
from db.store import P

from .conftest import requires_pg


@pytest.fixture
def rows(pg_schema):
    schema.ensure_schema(tables=["NexusStrategyCache", "GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        for rid, doc in (
            ("main|gna|h1|backtest|2026-05-23", '{"id":"main|gna|h1|backtest|2026-05-23","instance_id":"main","origin":"backtest","score":10}'),
            ("main|gna|h2|live|2026-05-24", '{"id":"main|gna|h2|live|2026-05-24","instance_id":"main","origin":"live","score":2}'),
            ("alpaca-main|gna|h3|live|2026-05-25", '{"id":"alpaca-main|gna|h3|live|2026-05-25","instance_id":"alpaca-main","score":7}'),
        ):
            cur.execute('INSERT INTO "NexusStrategyCache" (id, doc) VALUES (%s,%s)',
                        (rid, doc))
    return pg_schema


def test_predicate_never_interpolates_values():
    frag, params = P.field("k").eq("O'Brien").to_sql()
    assert "O'Brien" not in frag and params == ("O'Brien",)


def test_split_nth_is_one_based_in_sql():
    # The implementation binds the separator and the index as parameters
    # rather than interpolating them, so assert on the shape AND the bound
    # 1-based index. test_split_nth_selects_the_base_instance proves it
    # against a real database.
    frag, params = P.field("instance_id").split_nth("|", 0).eq("main").to_sql()
    assert "split_part(doc->>'instance_id', %s, %s)" in frag
    assert params == ("|", 1, "main")


def test_default_compiles_to_coalesce():
    frag, params = P.field("origin").default("").ne("backtest").to_sql()
    assert "coalesce(doc->>'origin', %s)" in frag
    assert params == ("", "backtest")


@requires_pg
def test_filter_dict_matches_on_equality(rows):
    got = store.run(store.filter("NexusStrategyCache", {"instance_id": "main"}))
    assert {r["id"] for r in got} == {
        "main|gna|h1|backtest|2026-05-23", "main|gna|h2|live|2026-05-24"}


@requires_pg
def test_filter_dict_none_means_json_null_not_absent(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.cursor() as cur:
        cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                    ("null_row", '{"id":"null_row","err":null}'))
        cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                    ("absent_row", '{"id":"absent_row"}'))
    got = store.run(store.filter("DiscordOutbox", {"err": None}))
    assert [r["id"] for r in got] == ["null_row"]


@requires_pg
def test_origin_not_backtest_criterion(rows):
    # clear_instance_state.py:377's "special" criterion, name preserved.
    pred = P.field("instance_id").eq("main") & P.field("origin").default("").ne("backtest")
    got = store.run(store.filter("NexusStrategyCache", pred))
    assert [r["id"] for r in got] == ["main|gna|h2|live|2026-05-24"]


@requires_pg
def test_undefaulted_field_comparison_is_false_on_a_missing_key(rows):
    # ReQL raises on row["k"] for a missing key; every ported site writes
    # .default(). Undefaulted access is SQL NULL, so the comparison is false.
    got = store.run(store.filter("NexusStrategyCache", P.field("origin").ne("backtest")))
    assert [r["id"] for r in got] == ["main|gna|h2|live|2026-05-24"]


@requires_pg
def test_is_in_matches_any_of_a_list(rows):
    got = store.run(store.filter("NexusStrategyCache",
                                 P.field("instance_id").is_in(["main", "nope"])))
    assert len(got) == 2


@requires_pg
def test_is_in_with_an_empty_list_is_false_not_an_error(rows):
    assert store.run(store.filter("NexusStrategyCache",
                                  P.field("instance_id").is_in([]))) == []


@requires_pg
def test_combinators(rows):
    pred = (P.field("instance_id").eq("main") | P.field("instance_id").eq("alpaca-main"))
    assert len(store.run(store.filter("NexusStrategyCache", pred))) == 3
    assert len(store.run(store.filter("NexusStrategyCache",
                                      ~P.field("instance_id").eq("main")))) == 1


@requires_pg
def test_split_nth_selects_the_base_instance(rows):
    pred = P.field("id").split_nth("|", 0).eq("alpaca-main")
    got = store.run(store.filter("NexusStrategyCache", pred))
    assert [r["id"] for r in got] == ["alpaca-main|gna|h3|live|2026-05-25"]


@requires_pg
def test_coerce_to_string_stringifies_a_json_number(pg_schema):
    schema.ensure_schema(tables=["BacktestResults"])
    with dbpool.cursor() as cur:
        cur.execute('INSERT INTO "BacktestResults" (id, doc) VALUES (%s,%s)',
                    ("1", '{"id":1,"instance_id":5}'))
    pred = P.field("instance_id").default("").coerce_to_string().eq("5")
    assert len(store.run(store.filter("BacktestResults", pred))) == 1


@requires_pg
def test_between_is_half_open_by_default(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.cursor() as cur:
        for rid in ("a", "b", "c"):
            cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                        (rid, '{"id":"%s"}' % rid))
    sel = store.between("DiscordOutbox", "a", "c")
    assert sorted(r["id"] for r in store.run(sel)) == ["a", "b"]


@requires_pg
def test_between_right_bound_closed_includes_the_upper_key(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.cursor() as cur:
        for rid in ("a", "b", "c"):
            cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                        (rid, '{"id":"%s"}' % rid))
    sel = store.between("DiscordOutbox", "a", "c", right_bound="closed")
    assert sorted(r["id"] for r in store.run(sel)) == ["a", "b", "c"]


@requires_pg
def test_between_left_bound_open_excludes_the_lower_key(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.cursor() as cur:
        for rid in ("a", "b"):
            cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                        (rid, '{"id":"%s"}' % rid))
    sel = store.between("DiscordOutbox", "a", "z", left_bound="open")
    assert [r["id"] for r in store.run(sel)] == ["b"]


@requires_pg
def test_minval_and_maxval_omit_the_bound(rows):
    sel = store.between("NexusStrategyCache", "main", store.MAXVAL, index="instance_id")
    assert len(store.run(sel)) == 2
    frag = sel.where_sql()[0]
    assert frag.count(">=") == 1 and "<" not in frag.replace("<=", "")


@requires_pg
def test_order_by_numeric_field_casts_explicitly(rows):
    sel = store.order_by(store.Selection("NexusStrategyCache"),
                         fields=(store.desc("score", numeric=True),))
    assert [r["doc"]["score"] if "doc" in r else r["score"]
            for r in store.run(sel)] == [10, 7, 2]
