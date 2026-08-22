"""G5 store semantics behind engines/nexus_graph_engine.py.

The engine writes its progress document with ``insert(conflict='replace')``
(whole-document replace, which DROPS keys) and its control document with
``update()`` (deep merge, which does not). Getting those two the wrong way
round silently wipes the ``stages`` array the Nexus UI renders, so each is
pinned here.

The brief named a ``GraphNexusState`` table; the shipped registry
(db/schema.py ALL_TABLES) has no such table, and the conftest fixture only
creates registered tables. These use the tables the engine actually writes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROGRESS = "GraphNexusProgress"
CONTROL = "EngineControl"


def test_replace_drops_keys_update_does_not(store):
    store.insert(PROGRESS, {"id": "s1", "a": 1, "b": 2})
    store.update(PROGRESS, "s1", {"a": 9})
    assert store.get(PROGRESS, "s1") == {"id": "s1", "a": 9, "b": 2}
    store.replace(PROGRESS, "s1", {"id": "s1", "a": 9})
    assert store.get(PROGRESS, "s1") == {"id": "s1", "a": 9}


def test_insert_conflict_replace_drops_keys(store):
    """The engine's progress writer uses conflict='replace', not update."""
    store.insert(PROGRESS, {"id": "s1b", "a": 1, "stages": [{"stage_index": 0}]})
    store.insert(PROGRESS, {"id": "s1b", "a": 2}, conflict="replace")
    assert store.get(PROGRESS, "s1b") == {"id": "s1b", "a": 2}


def test_update_replaces_arrays_wholesale(store):
    store.insert(PROGRESS, {"id": "s2", "stages": [1, 2, 3]})
    store.update(PROGRESS, "s2", {"stages": [7]})
    assert store.get(PROGRESS, "s2")["stages"] == [7]


def test_update_none_sets_json_null_it_does_not_delete(store):
    store.insert(PROGRESS, {"id": "s3", "k": "v"})
    store.update(PROGRESS, "s3", {"k": None})
    got = store.get(PROGRESS, "s3")
    assert "k" in got and got["k"] is None


def test_missing_control_row_reads_as_none_not_empty_dict(store):
    """_nexus_control_want_stop() must not treat a missing row as running."""
    assert store.get(CONTROL, "nexus_graph_engine") is None


def test_nexus_control_want_stop_reads_the_store(store, monkeypatch):
    import engines.nexus_graph_engine as gns

    monkeypatch.setattr(gns, "store", store)
    store.insert(CONTROL, {"id": gns.NEXUS_CONTROL_ID, "running": True})
    assert gns._nexus_control_want_stop(object()) is False
    store.update(CONTROL, gns.NEXUS_CONTROL_ID, {"running": False})
    assert gns._nexus_control_want_stop(object()) is True


def test_stock_cache_round_trip(store, monkeypatch):
    import engines.nexus_graph_engine as gns

    monkeypatch.setattr(gns, "store", store)
    conn = object()
    assert gns._get_cached_stock(conn, "aapl") is None
    gns._upsert_stock_cache(conn, {"id": "AAPL", "sector": "Technology"})
    assert gns._get_cached_stock(conn, " aapl ")["sector"] == "Technology"
