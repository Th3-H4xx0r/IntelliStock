import pytest

from db import schema, store
from db.errors import StoreError
from db.merge import Literal
from db.store import P

from .conftest import requires_pg


@pytest.fixture
def tables(pg_schema):
    schema.ensure_schema(tables=["Instances", "DiscordOutbox", "kalshi_markets",
                                 "NexusRuntimeState"])
    return pg_schema


@requires_pg
def test_insert_then_get(tables):
    res = store.insert("Instances", {"id": 1, "name": "main"})
    assert res.inserted == 1 and res.errors == 0
    assert store.get("Instances", 1) == {"id": 1, "name": "main"}


@requires_pg
def test_insert_result_supports_dict_access(tables):
    res = store.insert("Instances", {"id": 1})
    assert res["inserted"] == 1 and res.get("errors", 0) == 0


@requires_pg
def test_durability_is_accepted_and_ignored(tables):
    assert store.insert("Instances", {"id": 3}, durability="soft").inserted == 1


@requires_pg
def test_update_deep_merges_objects_and_replaces_arrays(tables):
    store.insert("Instances", {"id": 1, "cfg": {"a": 1, "b": 2}, "syms": ["A", "B"]})
    store.update("Instances", 1, {"cfg": {"b": 9, "c": 3}, "syms": ["Z"]})
    assert store.get("Instances", 1) == {
        "id": 1, "cfg": {"a": 1, "b": 9, "c": 3}, "syms": ["Z"]}


@requires_pg
def test_update_creates_missing_intermediates(tables):
    store.insert("Instances", {"id": 1})
    store.update("Instances", 1, {"a": {"b": {"c": 1}}})
    assert store.get("Instances", 1)["a"] == {"b": {"c": 1}}


@requires_pg
def test_update_none_sets_json_null_it_does_not_delete(tables):
    store.insert("Instances", {"id": 1, "k": 5})
    store.update("Instances", 1, {"k": None})
    doc = store.get("Instances", 1)
    assert "k" in doc and doc["k"] is None


@requires_pg
def test_update_with_literal_blanks_a_subtree(tables):
    store.insert("Instances", {"id": 1, "secrets": {"k": "v", "j": "w"}})
    store.update("Instances", 1, {"secrets": Literal({})})
    assert store.get("Instances", 1)["secrets"] == {}


@requires_pg
def test_update_over_a_selection_is_one_statement(tables):
    for i in (1, 2, 3):
        store.insert("DiscordOutbox", {"id": str(i), "kind": "x" if i < 3 else "y"})
    res = store.update("DiscordOutbox", store.filter("DiscordOutbox", {"kind": "x"}),
                       {"sent": True})
    assert res.replaced == 2
    assert store.get("DiscordOutbox", "3").get("sent") is None


@requires_pg
def test_update_of_a_missing_row_reports_skipped_not_replaced(tables):
    res = store.update("Instances", 999, {"a": 1})
    assert res.replaced == 0 and res.skipped == 1


@requires_pg
def test_replace_swaps_the_whole_document(tables):
    store.insert("Instances", {"id": 1, "a": 1, "b": 2})
    store.replace("Instances", 1, {"id": 1, "c": 3})
    assert store.get("Instances", 1) == {"id": 1, "c": 3}


@requires_pg
def test_replace_if_writes_when_the_predicate_holds(tables):
    store.insert("Instances", {"id": 1, "status": "paused_llm_critical"})
    got = store.replace_if(
        "Instances", 1,
        when=P.field("status").default("").eq("paused_llm_critical"),
        doc={"id": 1, "status": "running"})
    assert got == {"id": 1, "status": "running"}
    assert store.get("Instances", 1)["status"] == "running"


@requires_pg
def test_replace_if_returns_none_when_the_predicate_does_not_hold(tables):
    store.insert("Instances", {"id": 1, "status": "running"})
    got = store.replace_if("Instances", 1,
                           when=P.field("status").default("").eq("paused_llm_critical"),
                           doc={"id": 1, "status": "x"})
    assert got is None
    assert store.get("Instances", 1)["status"] == "running"


@requires_pg
def test_replace_if_distinguishes_missing_row_from_failed_predicate(tables):
    with pytest.raises(StoreError):
        store.replace_if("Instances", 404, when=P.field("status").eq("x"),
                         doc={"id": 404})
    got = store.replace_if("Instances", 404, when=None, doc={"id": 404},
                           insert_if_absent=True)
    assert got == {"id": 404}


@requires_pg
def test_delete_by_id(tables):
    store.insert("Instances", {"id": 1})
    assert store.delete("Instances", 1).deleted == 1
    assert store.get("Instances", 1) is None


@requires_pg
def test_delete_over_a_selection_is_one_statement(tables):
    for i in (1, 2, 3):
        store.insert("DiscordOutbox", {"id": str(i), "kind": "x" if i < 3 else "y"})
    assert store.delete("DiscordOutbox",
                        store.filter("DiscordOutbox", {"kind": "x"})).deleted == 2
    assert store.count("DiscordOutbox") == 1


@requires_pg
def test_non_id_primary_key_is_copied_from_its_doc_field(tables):
    store.insert("kalshi_markets", {"market_ticker": "KXM-26", "yes_bid": 40})
    assert store.get("kalshi_markets", "KXM-26") == {
        "market_ticker": "KXM-26", "yes_bid": 40}


@requires_pg
def test_writing_a_document_without_an_id_generates_one(tables):
    """An ``id``-keyed table mints, as ReQL did."""
    res = store.insert("DiscordOutbox", {"body": "hi"})
    assert res.inserted == 1 and len(res.generated_keys) == 1


@requires_pg
def test_writing_a_document_without_its_CUSTOM_pk_field_raises(tables):
    """kalshi_markets keys on market_ticker, and ReQL minted only for ``id``
    -- minting here writes a row under a key nothing ever looks up."""
    with pytest.raises(StoreError):
        store.insert("kalshi_markets", {"yes_bid": 40})
    assert store.count("kalshi_markets") == 0


@requires_pg
def test_nan_is_rejected_at_the_client(tables):
    with pytest.raises(ValueError):
        store.insert("Instances", {"id": 1, "rsi": float("nan")})


@requires_pg
def test_int_table_rejects_a_non_integer_id(tables):
    with pytest.raises(StoreError):
        store.insert("Instances", {"id": "abc"})


@requires_pg
def test_updated_at_advances_on_every_write(tables):
    store.insert("Instances", {"id": 1})
    first = store.sql('SELECT updated_at FROM "Instances" WHERE id=%s', ("1",))[0]
    store.update("Instances", 1, {"a": 1})
    second = store.sql('SELECT updated_at FROM "Instances" WHERE id=%s', ("1",))[0]
    assert second["updated_at"] >= first["updated_at"]
