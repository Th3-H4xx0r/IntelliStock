"""scripts/pg_retention.py -- off by default, indexed, and bounded.

Most of these need no database: they read the registry and inspect the SQL the
module builds, which is where the "never unindexed, never unbounded" guarantee
actually lives. The four marked ``requires_pg`` need a real Postgres, because
"the DELETE removed the old row and left the new one" is not provable against
a dict.
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


def test_a_table_with_no_retention_spec_is_off(monkeypatch):
    from db import schema

    assert ret.retention_days(schema.spec("Instances")) is None


def test_prune_of_an_unconfigured_table_is_a_noop():
    assert ret.prune_table("Instances") == {"table": "Instances", "deleted": 0,
                                            "skipped": "retention not configured"}


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
    assert not any("doc->>" in s for s in statements), "unindexed DELETE"
    assert all("LIMIT" in s and "ctid IN" in s for s in statements), "unbounded DELETE"


def test_a_real_column_window_compares_as_a_timestamp_not_as_text(monkeypatch):
    """PriceHistory.ts is a timestamptz COLUMN, so COLLATE "C" would be a type
    error, not a nicety. RetentionSpec.is_column is what says which."""
    from db import schema

    assert ret._predicate(schema.spec("PriceHistory")) == '"ts" < %s'
    assert ret._predicate(schema.spec("GraphNexusLLMPromptCache")) == \
        '"cached_at" COLLATE "C" < %s'
    assert isinstance(ret._cutoff(schema.spec("GraphNexusLLMPromptCache"), 1), str)


def test_a_partitioned_table_is_skipped(monkeypatch):
    """ctid is unique within a table but NOT across a partitioned table's
    children, so the batching form could delete out of the wrong partition.
    Partitions are dropped whole, by pg_partman."""
    from db import schema

    spec = schema.spec("PriceHistory")
    monkeypatch.setenv(spec.retention.days_env, "1")
    report = ret.prune_table("PriceHistory")
    assert report["deleted"] == 0
    assert "partitioned" in report["skipped"]


def test_configured_tables_are_exactly_the_ones_with_a_spec():
    from db import schema

    assert set(ret.configured_tables()) == {
        name for name, spec in schema.TABLES.items() if spec.retention is not None}
    assert "GraphNexusLLMPromptCache" in ret.configured_tables()
    assert "Instances" not in ret.configured_tables()


@requires_pg
def test_prune_deletes_the_old_row_and_keeps_the_new_one(store, monkeypatch):
    from db import schema

    spec = schema.spec("GraphNexusLLMPromptCache")
    monkeypatch.setenv(spec.retention.days_env, "1")
    store.insert("GraphNexusLLMPromptCache", [
        {"id": "old", "cached_at": "2020-01-01T00:00:00+00:00"},
        {"id": "new", "cached_at": "2999-01-01T00:00:00+00:00"},
    ])
    report = ret.prune_table("GraphNexusLLMPromptCache", batch=1)
    assert report["deleted"] == 1
    assert [r["id"] for r in store.sql(
        'SELECT id FROM "GraphNexusLLMPromptCache" ORDER BY id COLLATE "C"')] == ["new"]


@requires_pg
def test_a_row_with_no_retention_field_at_all_is_never_deleted(store, monkeypatch):
    """The generated column is NULL there, and ``NULL < cutoff`` is unknown, so
    the row is not matched -- the same rows RethinkDB's between() skipped."""
    from db import schema

    spec = schema.spec("GraphNexusLLMPromptCache")
    monkeypatch.setenv(spec.retention.days_env, "1")
    store.insert("GraphNexusLLMPromptCache", [
        {"id": "old", "cached_at": "2020-01-01T00:00:00+00:00"},
        {"id": "undated", "body": "no cached_at key"},
    ])
    assert ret.prune_table("GraphNexusLLMPromptCache")["deleted"] == 1
    assert store.get("GraphNexusLLMPromptCache", "undated") is not None


@requires_pg
def test_dry_run_counts_and_deletes_nothing(store, monkeypatch):
    from db import schema

    spec = schema.spec("GraphNexusLLMPromptCache")
    monkeypatch.setenv(spec.retention.days_env, "1")
    store.insert("GraphNexusLLMPromptCache", [
        {"id": "a", "cached_at": "2020-01-01T00:00:00+00:00"},
        {"id": "b", "cached_at": "2020-01-02T00:00:00+00:00"},
        {"id": "c", "cached_at": "2999-01-01T00:00:00+00:00"},
    ])
    report = ret.prune_table("GraphNexusLLMPromptCache", dry_run=True)
    assert report == {"table": "GraphNexusLLMPromptCache", "deleted": 0,
                      "would_delete": 2, "cutoff": report["cutoff"],
                      "dry_run": True}
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
