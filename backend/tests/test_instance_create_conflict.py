"""Creating an instance that already exists is a conflict, not a silent wipe.

POST /instances wrote ``conflict="replace"``. Re-posting an id that already
existed therefore replaced the whole row: the linked brokerage, the strategy
id, the symbol list and the run flag of a live instance, gone, with a 200 and
no warning. On a real-money instance that is a one-request repoint of which
account trades what.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


@pytest.fixture
def iu(monkeypatch):
    import interactive_utils as _iu

    monkeypatch.setattr(_iu, "ensure_instances_table", lambda conn: None)
    return _iu


def _cage(monkeypatch, iu, existing=None):
    """Record inserts; answer ``get`` from ``existing``."""
    calls = {"inserts": []}
    rows = dict(existing or {})

    def _insert(table, doc, **kwargs):
        calls["inserts"].append((table, doc, kwargs))
        return {"inserted": 1, "errors": 0, "first_error": None}

    monkeypatch.setattr(iu.store, "insert", _insert)
    monkeypatch.setattr(iu.store, "get", lambda table, key: rows.get(key))
    return calls


def test_creating_a_brand_new_instance_still_works(monkeypatch, iu):
    calls = _cage(monkeypatch, iu)
    out = iu.action_create_instance(None, "strategy-eb", name="EB")
    assert out["id"] == "strategy-eb"
    assert calls["inserts"], "nothing was written"


def test_creating_an_instance_that_exists_raises_instance_exists(monkeypatch, iu):
    calls = _cage(monkeypatch, iu, existing={"strategy-eb": {"id": "strategy-eb"}})
    with pytest.raises(iu.InstanceExistsError):
        iu.action_create_instance(None, "strategy-eb", name="EB")
    assert calls["inserts"] == [], "the clobbering write still happened"


def test_the_write_itself_refuses_to_replace(monkeypatch, iu):
    """Belt and braces: the existence check races, the insert must not."""
    calls = _cage(monkeypatch, iu)
    iu.action_create_instance(None, "strategy-eb")
    _table, _doc, kwargs = calls["inserts"][0]
    assert kwargs.get("conflict") == "error"


def test_a_losing_race_is_reported_as_a_conflict_not_a_success(monkeypatch, iu):
    """Two creates in flight: the check passes for both, the insert fails for
    the second, and that failure must surface as the same conflict."""
    monkeypatch.setattr(iu, "ensure_instances_table", lambda conn: None)
    monkeypatch.setattr(iu.store, "get", lambda table, key: None)
    monkeypatch.setattr(
        iu.store, "insert",
        lambda table, doc, **kw: {"inserted": 0, "errors": 1,
                                  "first_error": "duplicate key value"},
    )
    with pytest.raises(iu.InstanceExistsError):
        iu.action_create_instance(None, "strategy-eb")


def test_the_api_maps_it_to_409(monkeypatch):
    from api import main
    import interactive_utils as iu

    def _boom(*_a, **_k):
        raise iu.InstanceExistsError("Instance already exists: strategy-eb")

    with pytest.raises(HTTPException) as ei:
        main._run(_boom, None, "strategy-eb")
    assert ei.value.status_code == 409
    assert "strategy-eb" in str(ei.value.detail)


def test_a_plain_value_error_is_still_a_400(monkeypatch):
    from api import main

    def _boom(*_a, **_k):
        raise ValueError("Instance ID required")

    with pytest.raises(HTTPException) as ei:
        main._run(_boom, None, "")
    assert ei.value.status_code == 400


def test_patch_remains_the_update_path(monkeypatch, iu):
    """The editor still works on an existing row — the conflict only closes
    the create door."""
    updates = {}
    monkeypatch.setattr(iu, "_resolve_instance_doc",
                        lambda conn, iid: {"id": iid, "name": "old"})
    monkeypatch.setattr(iu.store, "update",
                        lambda table, key, patch: updates.update(key=key, patch=patch))
    monkeypatch.setattr(iu.store, "get", lambda table, key: {"id": key, "name": "new"})
    iu.action_edit_instance(None, "strategy-eb", name="new")
    assert updates["key"] == "strategy-eb"
    assert updates["patch"]["name"] == "new"
