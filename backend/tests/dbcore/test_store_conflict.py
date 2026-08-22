import pytest

from db import schema, store

from .conftest import requires_pg


@pytest.fixture
def outbox(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    return pg_schema


@requires_pg
def test_conflict_error_records_the_conflict_without_aborting_the_batch(outbox):
    store.insert("DiscordOutbox", {"id": "a", "n": 1})
    res = store.insert("DiscordOutbox", [{"id": "a", "n": 2}, {"id": "b", "n": 3}])
    assert res.inserted == 1
    assert res.errors == 1
    assert "Duplicate primary key `id`" in res.first_error
    assert store.get("DiscordOutbox", "a")["n"] == 1     # unchanged
    assert store.get("DiscordOutbox", "b")["n"] == 3     # the batch continued


@requires_pg
def test_conflict_replace_drops_keys_the_new_document_lacks(outbox):
    store.insert("DiscordOutbox", {"id": "a", "keep": 1, "drop": 2})
    res = store.insert("DiscordOutbox", {"id": "a", "keep": 9}, conflict="replace")
    assert res.replaced == 1
    assert store.get("DiscordOutbox", "a") == {"id": "a", "keep": 9}


@requires_pg
def test_conflict_update_deep_merges_it_does_not_use_shallow_concat(outbox):
    store.insert("DiscordOutbox", {"id": "a", "cfg": {"x": 1, "y": 2}})
    store.insert("DiscordOutbox", {"id": "a", "cfg": {"y": 9}}, conflict="update")
    # `||` would have dropped "x". jsonb_deep_merge must keep it.
    assert store.get("DiscordOutbox", "a")["cfg"] == {"x": 1, "y": 9}


@requires_pg
def test_a_500_doc_chunk_with_two_duplicates_is_partial_success(outbox):
    store.insert("DiscordOutbox", [{"id": "d%03d" % i} for i in (7, 400)])
    docs = [{"id": "d%03d" % i, "n": i} for i in range(500)]
    res = store.insert("DiscordOutbox", docs)
    assert res.inserted == 498
    assert res.errors == 2
    assert "Duplicate primary key `id`" in res.first_error
    assert store.count("DiscordOutbox") == 500


@requires_pg
def test_inserts_are_chunked_at_500(outbox):
    assert store.WRITE_CHUNK == 500
    res = store.insert("DiscordOutbox", [{"id": "x%04d" % i} for i in range(1200)])
    assert res.inserted == 1200 and res.errors == 0


@requires_pg
def test_unchanged_is_reported_when_a_replace_writes_identical_bytes(outbox):
    store.insert("DiscordOutbox", {"id": "a", "n": 1})
    res = store.insert("DiscordOutbox", {"id": "a", "n": 1}, conflict="replace")
    assert res.unchanged == 1 and res.replaced == 0
