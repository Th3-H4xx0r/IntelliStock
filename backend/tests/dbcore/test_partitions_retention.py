import datetime as dt

import pytest

from db import pool as dbpool
from db import schema

from .conftest import requires_pg


def _load_retention_module():
    import importlib.util
    import os
    repo = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    path = os.path.join(repo, "scripts", "pg_retention.py")
    spec_ = importlib.util.spec_from_file_location("pg_retention", path)
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)
    return mod


def test_partition_name_is_month_stamped():
    assert schema.partition_name("PriceHistory", dt.date(2026, 8, 1)) == \
        "PriceHistory_p2026_08"


@requires_pg
def test_ensure_partitions_creates_one_partition_per_month(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    made = schema.ensure_partitions(
        "PriceHistory",
        lo=dt.datetime(2026, 1, 15, tzinfo=dt.timezone.utc),
        hi=dt.datetime(2026, 3, 2, tzinfo=dt.timezone.utc))
    assert len(made) == 3            # Jan, Feb, Mar
    with dbpool.cursor() as cur:
        # relkind='r': pg_class also holds each partition's primary-key
        # INDEX, whose name starts with the partition name.
        cur.execute("SELECT relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relkind = 'r' "
                    "AND c.relname LIKE 'PriceHistory\\_p%%'", (pg_schema,))
        names = {r["relname"] for r in cur.fetchall()}
    # ensure_schema premakes the rolling window, so only assert these three.
    assert {"PriceHistory_p2026_01", "PriceHistory_p2026_02",
            "PriceHistory_p2026_03"} <= names


@requires_pg
def test_ensure_partitions_is_idempotent(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    args = dict(lo=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                hi=dt.datetime(2026, 1, 31, tzinfo=dt.timezone.utc))
    assert len(schema.ensure_partitions("PriceHistory", **args)) == 1
    assert schema.ensure_partitions("PriceHistory", **args) == []


@requires_pg
def test_ensure_partitions_rejects_an_unpartitioned_table(pg_schema):
    from db.errors import StoreError
    with pytest.raises(StoreError):
        schema.ensure_partitions("DiscordOutbox",
                                 lo=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                                 hi=dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc))


@requires_pg
def test_there_is_no_default_partition(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    with dbpool.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relname = 'PriceHistory_default'",
                    (pg_schema,))
        assert cur.fetchone()["n"] == 0


@requires_pg
def test_autovacuum_is_tuned_on_the_leaf_partitions_not_the_parent(pg_schema):
    # PG rejects storage parameters on a partitioned parent (it has no storage
    # of its own), so ensure_partitions applies them to each leaf it creates.
    schema.ensure_schema(tables=["PriceHistory"])
    schema.ensure_partitions("PriceHistory",
                             lo=dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc),
                             hi=dt.datetime(2026, 2, 2, tzinfo=dt.timezone.utc))
    with dbpool.cursor() as cur:
        cur.execute("SELECT relname, reloptions FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relname IN "
                    "('PriceHistory', 'PriceHistory_p2026_02')", (pg_schema,))
        opts = {r["relname"]: r["reloptions"] for r in cur.fetchall()}
    assert opts["PriceHistory"] is None
    assert "autovacuum_vacuum_scale_factor=0.02" in opts["PriceHistory_p2026_02"]


@requires_pg
def test_rows_route_to_the_right_partition(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    schema.ensure_partitions("PriceHistory",
                             lo=dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc),
                             hi=dt.datetime(2026, 2, 28, tzinfo=dt.timezone.utc))
    with dbpool.cursor() as cur:
        cur.execute(
            'INSERT INTO "PriceHistory" (ticker, ts, id, doc) VALUES '
            "(%s, %s, %s, %s)",
            ("AAPL", dt.datetime(2026, 2, 10, tzinfo=dt.timezone.utc), "u1",
             '{"ticker":"AAPL","price":1.5}'))
        cur.execute('SELECT tableoid::regclass::text AS part FROM "PriceHistory"')
        # regclass::text quotes a mixed-case identifier.
        assert cur.fetchone()["part"].strip('"').endswith("PriceHistory_p2026_02")


def test_retention_is_off_until_the_env_var_is_set(monkeypatch):
    monkeypatch.delenv("RETAIN_PROMPT_CACHE_DAYS", raising=False)
    assert schema.spec("GraphNexusLLMPromptCache").retention.days() is None
    monkeypatch.setenv("RETAIN_PROMPT_CACHE_DAYS", "30")
    assert schema.spec("GraphNexusLLMPromptCache").retention.days() == 30


def test_learning_observations_retention_defaults_to_90_days(monkeypatch):
    monkeypatch.delenv("RETAIN_LEARNING_OBSERVATIONS_DAYS", raising=False)
    assert schema.spec("LearningObservations").retention.days() == 90


def test_retention_days_floor_is_one(monkeypatch):
    # retention.py:34 MIN_RETAIN_DAYS: a stored 0 would delete the table.
    monkeypatch.setenv("RETAIN_PROMPT_CACHE_DAYS", "0")
    assert schema.spec("GraphNexusLLMPromptCache").retention.days() == 1


@requires_pg
def test_sweep_is_a_noop_when_retention_is_unset(pg_schema, monkeypatch):
    monkeypatch.delenv("RETAIN_PROMPT_CACHE_DAYS", raising=False)
    schema.ensure_schema(tables=["GraphNexusLLMPromptCache"])
    mod = _load_retention_module()
    assert mod.sweep("GraphNexusLLMPromptCache") == {
        "table": "GraphNexusLLMPromptCache", "deleted": 0, "skipped": "retention off"}


@requires_pg
def test_sweep_deletes_only_rows_past_the_cutoff(pg_schema, monkeypatch):
    monkeypatch.setenv("RETAIN_PROMPT_CACHE_DAYS", "10")
    schema.ensure_schema(tables=["GraphNexusLLMPromptCache"])
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=40)).isoformat()
    new = dt.datetime.now(dt.timezone.utc).isoformat()
    with dbpool.cursor() as cur:
        for rid, when in (("a", old), ("b", new)):
            cur.execute('INSERT INTO "GraphNexusLLMPromptCache" (id, doc) VALUES (%s,%s)',
                        (rid, '{"cached_at":"%s"}' % when))
    mod = _load_retention_module()
    assert mod.sweep("GraphNexusLLMPromptCache")["deleted"] == 1
    with dbpool.cursor() as cur:
        cur.execute('SELECT id FROM "GraphNexusLLMPromptCache"')
        assert [r["id"] for r in cur.fetchall()] == ["b"]


@requires_pg
def test_sweep_dry_run_counts_without_deleting(pg_schema, monkeypatch):
    monkeypatch.setenv("RETAIN_PROMPT_CACHE_DAYS", "10")
    schema.ensure_schema(tables=["GraphNexusLLMPromptCache"])
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=40)).isoformat()
    with dbpool.cursor() as cur:
        cur.execute('INSERT INTO "GraphNexusLLMPromptCache" (id, doc) VALUES (%s,%s)',
                    ("a", '{"cached_at":"%s"}' % old))
    mod = _load_retention_module()
    assert mod.sweep("GraphNexusLLMPromptCache", dry_run=True)["would_delete"] == 1
    with dbpool.cursor() as cur:
        cur.execute('SELECT count(*) AS n FROM "GraphNexusLLMPromptCache"')
        assert cur.fetchone()["n"] == 1


@requires_pg
def test_sweep_never_deletes_a_row_whose_timestamp_will_not_parse(pg_schema, monkeypatch):
    # retention.py's rule: an unparseable timestamp is NEVER deleted.
    monkeypatch.setenv("RETAIN_PROMPT_CACHE_DAYS", "1")
    schema.ensure_schema(tables=["GraphNexusLLMPromptCache"])
    with dbpool.cursor() as cur:
        cur.execute('INSERT INTO "GraphNexusLLMPromptCache" (id, doc) VALUES (%s,%s)',
                    ("junk", '{"cached_at":"not-a-date"}'))
    mod = _load_retention_module()
    assert mod.sweep("GraphNexusLLMPromptCache")["deleted"] == 0


@requires_pg
def test_sweep_of_a_table_with_no_retention_spec_is_a_noop(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    mod = _load_retention_module()
    assert mod.sweep("DiscordOutbox")["skipped"] == "no retention spec"
