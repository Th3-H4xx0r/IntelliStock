import threading

import pytest

from db import schema
from db import pool as dbpool

from .conftest import requires_pg


# Tables that are NOT in the 2026-08-22 live table_list because the code that
# writes them created them lazily at first use. The port deleted those
# ensure-blocks (recipe R25: schema.py owns DDL), so the registry must declare
# them or a bare ensure_schema() would not create them.
_LAZILY_CREATED = (
    # benchmark_alpha/pg_store.py + records.py
    "AlphaEvents", "AlphaExperiments", "AlphaPredictions", "AlphaGates",
    "AlphaAllocations", "AlphaOrderIntents", "AlphaBrokerOrders", "AlphaFills",
    "AlphaPortfolioSnapshots", "AlphaCashActivities", "AlphaOutcomes",
    "AlphaIncidents",
)


def test_all_tables_has_the_125_live_tables_plus_the_two_split_tables():
    assert len(schema.ALL_TABLES) == 127 + len(_LAZILY_CREATED)
    assert len(set(schema.ALL_TABLES)) == len(schema.ALL_TABLES)
    for name in ("BacktestResults", "PriceHistory", "GraphNexusTradeContexts",
                 "kalshi_decisions", "sports_fixtures", "Users",
                 # both have specs, so a bare ensure_schema() must create them
                 "BacktestSteps", "BacktestProgress"):
        assert name in schema.ALL_TABLES
    for name in _LAZILY_CREATED:
        assert name in schema.ALL_TABLES


def test_spec_of_an_unregistered_table_is_the_default_template():
    s = schema.spec("DiscordOutbox")
    assert s.name == "DiscordOutbox" and s.id_type == "text"
    assert s.pk_field == "id" and s.indexed_fields == () and s.notify is True


def test_non_id_primary_keys_match_the_live_table_config():
    live = {
        "KalshiHistFixtures": "fixture_key",
        "kalshi_capital_plan": "instance_id",
        "kalshi_market_listings": "fixture_id",
        "kalshi_markets": "market_ticker",
        "kalshi_orders": "client_order_id",
        "kalshi_scan_budget": "window",
        "lineups": "fixture_id",
        "match_features": "fixture_id",
        "sports_fixtures": "fixture_id",
    }
    for table, pk_field in live.items():
        assert schema.spec(table).pk_field == pk_field
    others = {n: s.pk_field for n, s in schema.TABLES.items()
              if s.pk_field != "id" and n not in live}
    assert others == {}, "unexpected non-id primary key: %r" % others


def test_the_eight_high_volume_tables_have_notifications_off():
    for name in ("PriceHistory", "AlpacaBarsCache", "GraphNexusLLMPromptCache",
                 "LLMUsage", "GraphNexusNewsLLMMacro", "GraphNexusNewsLLMCompany",
                 "GraphNexusNewsDayFeatures", "GraphNexusOutcomeSeries"):
        assert schema.spec(name).notify is False


def test_int_id_tables_are_declared():
    for name in ("BacktestResults", "BacktestInstances", "Instances", "Strategies"):
        assert schema.spec(name).id_type == "int"


def test_backtest_results_compound_indexes_reproduce_the_reql_lambdas():
    ci = schema.spec("BacktestResults").compound_indexes
    assert set(ci) == {"instance_or_instance_id", "list_ts", "instance_ts"}
    for expr in ci.values():
        assert 'COLLATE "C"' in expr


def test_cache_tables_index_the_iso_string_not_a_timestamp():
    # A text->timestamptz cast is STABLE, not IMMUTABLE, so PG rejects it in a
    # generated column: the caches index the ISO string cached_at instead.
    for name in ("GraphNexusLLMPromptCache", "AlpacaBarsCache"):
        s = schema.spec(name)
        assert s.indexed_fields == ("cached_at",)
        assert s.generated == {}
        assert s.retention is not None and s.retention.field == "cached_at"


def test_backtest_results_tickers_total_is_guarded_against_non_arrays():
    sql_type, expr = schema.spec("BacktestResults").generated["tickers_total"]
    assert sql_type == "integer"
    assert "jsonb_typeof" in expr and "ELSE 0" in expr


@requires_pg
def test_ensure_schema_creates_the_default_table_shape(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
            (pg_schema, "DiscordOutbox"))
        cols = [(r["column_name"], r["data_type"]) for r in cur.fetchall()]
    assert cols == [("id", "text"), ("doc", "jsonb"),
                    ("updated_at", "timestamp with time zone")]


@requires_pg
def test_ensure_schema_creates_generated_columns_and_btrees(pg_schema):
    schema.ensure_schema(tables=["GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        cur.execute(
            "SELECT column_name, is_generated FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s",
            (pg_schema, "GraphNexusTradeContexts"))
        gen = {r["column_name"]: r["is_generated"] for r in cur.fetchall()}
        cur.execute("SELECT indexname FROM pg_indexes "
                    "WHERE schemaname=%s AND tablename=%s",
                    (pg_schema, "GraphNexusTradeContexts"))
        idx = {r["indexname"] for r in cur.fetchall()}
    assert gen.get("instance_id") == "ALWAYS"
    assert gen.get("base_instance_id") == "ALWAYS"
    assert "GraphNexusTradeContexts_instance_id_idx" in idx
    assert "GraphNexusTradeContexts_instance_id_pfx" in idx
    assert "GraphNexusTradeContexts_instance_base_idx" in idx


@requires_pg
def test_generated_columns_are_stored_not_virtual(pg_schema):
    # PG18 defaults to VIRTUAL and virtual columns cannot be indexed.
    schema.ensure_schema(tables=["GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        cur.execute("SELECT attgenerated FROM pg_attribute "
                    "WHERE attrelid = %s::regclass AND attname = 'instance_id'",
                    ('"%s"."GraphNexusTradeContexts"' % pg_schema,))
        assert cur.fetchone()["attgenerated"] == "s"


@requires_pg
def test_generated_text_columns_use_collate_c(pg_schema):
    schema.ensure_schema(tables=["GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        cur.execute(
            "SELECT c.collname FROM pg_attribute a "
            "JOIN pg_collation c ON c.oid = a.attcollation "
            "WHERE a.attrelid = %s::regclass AND a.attname = 'instance_id'",
            ('"%s"."GraphNexusTradeContexts"' % pg_schema,))
        assert cur.fetchone()["collname"] == "C"


@requires_pg
def test_no_gin_index_is_ever_created(pg_schema):
    schema.ensure_schema()
    with dbpool.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_am am ON am.oid = c.relam "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND am.amname = 'gin'", (pg_schema,))
        assert cur.fetchone()["n"] == 0


@requires_pg
def test_notify_trigger_installed_only_where_notify_is_true(pg_schema):
    schema.ensure_schema(tables=["Instances", "LLMUsage"])
    with dbpool.cursor() as cur:
        cur.execute("SELECT tgname, tgrelid::regclass::text AS tbl FROM pg_trigger "
                    "WHERE NOT tgisinternal")
        names = {r["tgname"] for r in cur.fetchall()}
    assert "Instances_notify" in names
    assert "LLMUsage_notify" not in names


@requires_pg
def test_ensure_schema_is_idempotent(pg_schema):
    first = schema.ensure_schema()
    assert first, "first run must report created objects"
    second = schema.ensure_schema()
    assert second == [], "second run must create nothing: %r" % second


@requires_pg
def test_ensure_schema_is_concurrent_safe(pg_schema):
    errors = []

    def run():
        try:
            schema.ensure_schema()
        except Exception as exc:      # pragma: no cover - the failure we guard
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)
    assert errors == []


@requires_pg
def test_ensure_table_creates_one_table_on_demand(pg_schema):
    schema.ensure_table("GraphNexusLLMPromptCache")
    with dbpool.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS t",
                    ('"%s"."GraphNexusLLMPromptCache"' % pg_schema,))
        assert cur.fetchone()["t"] is not None
