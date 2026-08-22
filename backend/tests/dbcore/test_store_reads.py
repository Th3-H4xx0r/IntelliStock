import pytest

from db import store
from db import schema
from db import pool as dbpool
from db.errors import StoreError

from .conftest import requires_pg


@pytest.fixture
def seeded(pg_schema):
    schema.ensure_schema(tables=["Instances", "DiscordOutbox", "BacktestResults"])
    with dbpool.cursor() as cur:
        for rid, doc in (("1", '{"id":1,"name":"main","stocks":["A","B"]}'),
                         ("2", '{"id":2,"name":"test"}')):
            cur.execute('INSERT INTO "Instances" (id, doc) VALUES (%s,%s)', (rid, doc))
        for rid, doc in (("a", '{"id":"a","kind":"x"}'),
                         ("b", '{"id":"b","kind":"y"}'),
                         ("c", '{"id":"c","kind":"x"}')):
            cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)', (rid, doc))
    return pg_schema


def test_asc_and_desc_build_order_objects():
    assert store.asc("timestamp") == store.Order("timestamp", False, False)
    assert store.desc("pnl", numeric=True) == store.Order("pnl", True, True)


def test_coerce_id_round_trips_int_tables():
    assert store.coerce_id("BacktestResults", 460555) == "460555"
    assert store.coerce_id("BacktestResults", "460555") == "460555"


def test_coerce_id_rejects_a_non_integer_for_an_int_table():
    with pytest.raises(StoreError):
        store.coerce_id("BacktestResults", "not-a-number")


def test_coerce_id_leaves_text_tables_alone():
    assert store.coerce_id("DiscordOutbox", "alpaca-main|abc") == "alpaca-main|abc"


@requires_pg
def test_get_returns_the_document(seeded):
    assert store.get("Instances", 1) == {"id": 1, "name": "main", "stocks": ["A", "B"]}


@requires_pg
def test_get_accepts_int_and_string_ids_interchangeably(seeded):
    assert store.get("Instances", 1) == store.get("Instances", "1")


@requires_pg
def test_get_missing_row_is_none_never_empty_dict(seeded):
    assert store.get("Instances", 999) is None


@requires_pg
def test_get_all_returns_rows_in_key_order(seeded):
    got = store.get_all("DiscordOutbox", "c", "a")
    assert [r["id"] for r in got] == ["c", "a"]


@requires_pg
def test_get_all_does_not_dedupe(seeded):
    # ReQL returns 3 rows for get_all("a","a","b"); = ANY() would collapse them.
    got = store.get_all("DiscordOutbox", "a", "a", "b")
    assert [r["id"] for r in got] == ["a", "a", "b"]


@requires_pg
def test_get_all_with_no_keys_is_a_valid_empty_result(seeded):
    # The "__no_match_sentinel__" trick in clear_instance_state.py is deleted.
    assert store.get_all("DiscordOutbox") == []


@requires_pg
def test_get_all_on_a_secondary_index_field(seeded):
    schema.ensure_schema(tables=["GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        cur.execute('INSERT INTO "GraphNexusTradeContexts" (id, doc) VALUES (%s,%s)',
                    ("k1", '{"id":"k1","instance_id":"main|h"}'))
    got = store.get_all("GraphNexusTradeContexts", "main|h", index="instance_id")
    assert [r["id"] for r in got] == ["k1"]


@requires_pg
def test_count_of_a_table(seeded):
    assert store.count("DiscordOutbox") == 3


@requires_pg
def test_run_raises_above_pg_max_rows(seeded, monkeypatch):
    """ReQL fails loudly above 100k array elements; nothing here defends
    against it today, so run() preserves the loud failure."""
    monkeypatch.setattr(store, "PG_MAX_ROWS", 2)
    with pytest.raises(StoreError):
        store.run(store.Selection("DiscordOutbox"))


@requires_pg
def test_iter_is_the_explicit_unbounded_path(seeded, monkeypatch):
    monkeypatch.setattr(store, "PG_MAX_ROWS", 2)
    assert len(list(store.iter(store.Selection("DiscordOutbox")))) == 3


@requires_pg
def test_limit_and_slice(seeded):
    sel = store.Selection("DiscordOutbox").ordered((store.asc("id"),))
    assert [r["id"] for r in store.run(store.limit(sel, 2))] == ["a", "b"]
    assert [r["id"] for r in store.run(store.slice(sel, 1, 3))] == ["b", "c"]


def test_pluck_omits_missing_keys_it_never_emits_null():
    rows = [{"id": 1, "status": "ok"}, {"id": 2}]
    assert store.pluck(rows, "id", "status") == [{"id": 1, "status": "ok"}, {"id": 2}]


def test_pluck_recurses_into_a_nested_spec():
    rows = [{"new_val": {"id": 5, "status": "running", "logs": [1]}, "old_val": None}]
    assert store.pluck(rows, {"new_val": ["id", "status"]}) == \
        [{"new_val": {"id": 5, "status": "running"}}]


@requires_pg
def test_table_list_and_table_create(pg_schema):
    assert store.table_create("DiscordOutbox") is True
    assert store.table_create("DiscordOutbox") is False     # already existed
    assert "DiscordOutbox" in store.table_list()


@requires_pg
def test_index_list_reports_the_reql_index_names(pg_schema):
    schema.ensure_schema(tables=["BacktestResults"])
    names = set(store.index_list("BacktestResults"))
    assert {"instance_or_instance_id", "list_ts", "instance_ts", "status"} <= names


@requires_pg
def test_time_fields_decode_back_to_aware_datetimes(pg_schema):
    import datetime as dt
    schema.TABLES["DiscordOutbox"] = schema.TableSpec(
        "DiscordOutbox", time_fields=("sent_at",))
    try:
        schema.ensure_schema(tables=["DiscordOutbox"])
        with dbpool.cursor() as cur:
            cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                        ("t", '{"id":"t","sent_at":"2026-08-22T03:37:00.123456+00:00"}'))
        got = store.get("DiscordOutbox", "t")
        assert got["sent_at"] == dt.datetime(2026, 8, 22, 3, 37, 0, 123456,
                                             tzinfo=dt.timezone.utc)
    finally:
        del schema.TABLES["DiscordOutbox"]


@requires_pg
def test_sql_is_the_escape_hatch_for_hand_written_statements(seeded):
    rows = store.sql('SELECT id FROM "DiscordOutbox" WHERE doc->>%s = %s ORDER BY id',
                     ("kind", "x"))
    assert [r["id"] for r in rows] == ["a", "c"]


@requires_pg
def test_sql_accepts_named_parameters_as_a_mapping(seeded):
    rows = store.sql('SELECT id FROM "DiscordOutbox" WHERE doc->>%(k)s = %(v)s '
                     "ORDER BY id", {"k": "kind", "v": "y"})
    assert [r["id"] for r in rows] == ["b"]
