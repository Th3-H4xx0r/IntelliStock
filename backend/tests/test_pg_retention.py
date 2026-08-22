"""scripts/pg_retention.py -- off by default, indexed, and bounded.

``backend/tests/dbcore/test_partitions_retention.py`` already covers the
sweeper's behaviour (cutoff, dry run, unparseable timestamps, no spec). This
file covers the two properties that file does not assert, and that are the
reason the module was reworked: every DELETE is BOUNDED, and every DELETE has
an index path. A predicate on ``doc ->> 'cached_at'`` has no index path at all
-- it is a sequential scan of 392,157 rows per batch -- and nothing failed
when it was one.
"""
import importlib.util
import os
import pathlib
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "pg_retention.py"

requires_pg = pytest.mark.skipif(
    not os.environ.get("PG_TEST_DSN"),
    reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")


def _load():
    """Load the repo-root ``scripts/`` module by path.

    ``from scripts.pg_retention import ...`` resolves to ``backend/scripts``:
    conftest puts ``backend/`` at sys.path[0] to mirror production and
    ``backend/scripts/`` is a real package, so the top-level directory is
    unreachable by name.
    """
    name = "pg_retention"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(_SCRIPT))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ret = _load()


# --------------------------------------------------------------------------
# off by default
# --------------------------------------------------------------------------

def test_retention_is_off_until_the_operator_sets_the_env_var(monkeypatch):
    from db import schema

    spec = schema.spec("GraphNexusLLMPromptCache")
    monkeypatch.delenv(spec.retention.days_env, raising=False)
    assert ret.retention_days(spec) is None


def test_env_var_enables_it(monkeypatch):
    from db import schema

    spec = schema.spec("GraphNexusLLMPromptCache")
    monkeypatch.setenv(spec.retention.days_env, "30")
    assert ret.retention_days(spec) == 30


def test_a_registry_default_needs_no_env_var(monkeypatch):
    """LearningObservations is the one window the registry ships enabled."""
    from db import schema

    spec = schema.spec("LearningObservations")
    monkeypatch.delenv(spec.retention.days_env, raising=False)
    assert ret.retention_days(spec) == 90


def test_a_table_with_no_retention_spec_is_off():
    from db import schema

    assert ret.retention_days(schema.spec("Instances")) is None


def test_prune_of_an_unconfigured_table_is_a_noop():
    assert ret.prune_table("Instances") == {"table": "Instances", "deleted": 0,
                                            "skipped": "no retention spec"}


def test_configured_tables_are_exactly_the_ones_with_a_spec():
    from db import schema

    assert ret.configured_tables() == sorted(
        n for n, s in schema.TABLES.items() if s.retention is not None)
    assert "GraphNexusLLMPromptCache" in ret.configured_tables()
    assert "Instances" not in ret.configured_tables()


def test_sweep_is_still_the_name_the_rest_of_the_tree_calls():
    assert ret.sweep("Instances") == ret.prune_table("Instances")


# --------------------------------------------------------------------------
# indexed and bounded
# --------------------------------------------------------------------------

def test_prune_is_a_ranged_delete_on_the_indexed_column(monkeypatch):
    from db import schema

    spec = schema.spec("GraphNexusLLMPromptCache")
    monkeypatch.setenv(spec.retention.days_env, "1")
    statements = []
    monkeypatch.setattr(ret, "_execute",
                        lambda sql, params: statements.append(sql) or 1)
    ret.prune_table("GraphNexusLLMPromptCache")
    assert statements
    assert all("DELETE FROM" in s for s in statements)
    assert all('"cached_at"' in s for s in statements), "must filter the indexed column"
    assert not any("doc->>" in s or "doc ->>" in s for s in statements), \
        "unindexed DELETE"
    assert all("LIMIT" in s for s in statements), "unbounded DELETE"


def test_the_batch_subquery_names_the_primary_key_not_ctid(monkeypatch):
    """ctid is unique within a table but NOT across a partitioned table's
    children, so the ctid form could delete out of a sibling partition."""
    from db import schema

    monkeypatch.setenv(schema.spec("PriceHistory").retention.days_env, "1")
    statements = []
    monkeypatch.setattr(ret, "_execute",
                        lambda sql, params: statements.append(sql) or 1)
    ret.prune_table("PriceHistory")
    assert statements
    assert all("ctid" not in s for s in statements)
    assert all('("ticker", "ts", "id") IN' in s for s in statements)


def test_the_exact_timestamptz_comparison_still_decides():
    """The text range is a prefilter for the index; it must never be the only
    predicate, or LLMUsage's epoch-millisecond ts ('1780059356069') sorts below
    every ISO cutoff and the whole table goes."""
    from db import schema

    where, params = ret._predicate(schema.spec("GraphNexusLLMPromptCache"),
                                   "2026-08-01T00:00:00+00:00")
    assert "pg_input_is_valid" in where
    assert "::timestamptz" in where
    assert params[1] == "2026-08-01T00:00:00+00:00"
    # the text bound is WIDER than the cutoff, so the prefilter is a superset
    assert params[0] > params[1]


def test_a_real_column_window_compares_as_a_timestamp_not_as_text():
    """PriceHistory.ts is a timestamptz COLUMN, so COLLATE "C" would be a type
    error, not a nicety. RetentionSpec.is_column is what says which."""
    from db import schema

    where, params = ret._predicate(schema.spec("PriceHistory"),
                                   "2026-08-01T00:00:00+00:00")
    assert where == '"ts" IS NOT NULL AND "ts" < %s::timestamptz'
    assert params == ("2026-08-01T00:00:00+00:00",)


@requires_pg
def test_the_delete_predicate_has_an_index_path(store, monkeypatch):
    """The property the doc->> form could never have. With seqscan disabled the
    planner must still find a way in -- an index scan on the generated column.
    On a two-row table it would otherwise choose a seq scan regardless."""
    from db import pool as dbpool
    from db import schema

    spec = schema.spec("GraphNexusLLMPromptCache")
    monkeypatch.setenv(spec.retention.days_env, "1")
    store.insert("GraphNexusLLMPromptCache",
                 {"id": "old", "cached_at": "2020-01-01T00:00:00+00:00"})
    where, params = ret._predicate(spec, ret._cutoff_iso(1))
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SET LOCAL enable_seqscan = off")
            cur.execute('EXPLAIN SELECT 1 FROM "GraphNexusLLMPromptCache" '
                        "WHERE " + where, params)
            text = " ".join(r["QUERY PLAN"] for r in cur.fetchall())
        conn.rollback()
    assert "GraphNexusLLMPromptCache_cached_at_idx" in text, text


@requires_pg
def test_prune_deletes_the_old_row_and_keeps_the_new_one(store, monkeypatch):
    from db import schema

    spec = schema.spec("GraphNexusLLMPromptCache")
    monkeypatch.setenv(spec.retention.days_env, "1")
    store.insert("GraphNexusLLMPromptCache", [
        {"id": "old", "cached_at": "2020-01-01T00:00:00+00:00"},
        {"id": "new", "cached_at": "2999-01-01T00:00:00+00:00"},
    ])
    assert ret.prune_table("GraphNexusLLMPromptCache", batch=1)["deleted"] == 1
    assert [r["id"] for r in store.sql(
        'SELECT id FROM "GraphNexusLLMPromptCache" ORDER BY id COLLATE "C"')] == ["new"]


@requires_pg
def test_an_epoch_millisecond_timestamp_is_never_swept(store, monkeypatch):
    """LLMUsage.ts is epoch milliseconds on all 300,291 live rows. The window
    is inert there rather than catastrophic, which is the right way round --
    and it is a finding, not a feature."""
    from db import schema

    spec = schema.spec("LLMUsage")
    monkeypatch.setenv(spec.retention.days_env, "1")
    store.insert("LLMUsage", [{"id": "u1", "ts": "1780059356069"},
                              {"id": "u2", "ts": "2020-01-01T00:00:00+00:00"}])
    assert ret.prune_table("LLMUsage")["deleted"] == 1
    assert store.get("LLMUsage", "u1") is not None


@requires_pg
def test_a_row_with_no_retention_field_at_all_is_never_deleted(store, monkeypatch):
    """The generated column is NULL there, and every comparison against NULL is
    unknown -- the same rows RethinkDB's between() skipped."""
    from db import schema

    monkeypatch.setenv(schema.spec("GraphNexusLLMPromptCache").retention.days_env, "1")
    store.insert("GraphNexusLLMPromptCache", [
        {"id": "old", "cached_at": "2020-01-01T00:00:00+00:00"},
        {"id": "undated", "body": "no cached_at key"},
    ])
    assert ret.prune_table("GraphNexusLLMPromptCache")["deleted"] == 1
    assert store.get("GraphNexusLLMPromptCache", "undated") is not None


@requires_pg
def test_dry_run_counts_and_deletes_nothing(store, monkeypatch):
    from db import schema

    monkeypatch.setenv(schema.spec("GraphNexusLLMPromptCache").retention.days_env, "1")
    store.insert("GraphNexusLLMPromptCache", [
        {"id": "a", "cached_at": "2020-01-01T00:00:00+00:00"},
        {"id": "b", "cached_at": "2020-01-02T00:00:00+00:00"},
        {"id": "c", "cached_at": "2999-01-01T00:00:00+00:00"},
    ])
    assert ret.prune_table("GraphNexusLLMPromptCache",
                           dry_run=True)["would_delete"] == 2
    assert store.count("GraphNexusLLMPromptCache") == 3


@requires_pg
def test_main_leaves_every_window_alone_by_default(store, monkeypatch):
    """No env vars set: the only window that fires is the one the registry
    ships with a default, and this table is not it."""
    from db import schema

    for name in ret.configured_tables():
        monkeypatch.delenv(schema.spec(name).retention.days_env, raising=False)
    store.insert("GraphNexusLLMPromptCache",
                 {"id": "old", "cached_at": "2020-01-01T00:00:00+00:00"})
    assert ret.main(["--tables", "GraphNexusLLMPromptCache"]) == 0
    assert store.count("GraphNexusLLMPromptCache") == 1
