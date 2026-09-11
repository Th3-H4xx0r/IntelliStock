"""The clean-room baseline is editable through the API.

``instance.py:_assert_clean_room_initial_value`` refuses to launch a broker
when clean-room mode is on and ``Instances.<id>.initial_value`` is missing --
the guard that kept ``alpaca-main`` from crash-looping six times on a single
absent field. But there was no way to *set* that field: ``EditInstanceBody``
carried name/granularity/max_usage/brokerage/crypto_config/stocks and nothing
else, so the only route to a baseline was a hand-written database write.

These tests pin the two fields the live-start preflight reads, and pin that
omitting them never clears what is already stored -- a PATCH that renames an
instance must not silently erase the baseline its drawdown circuit measures
against.
"""
from __future__ import annotations

import os
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

ADMIN = {"id": "admin-1", "username": "root", "role": "admin"}


@pytest.fixture
def patched(monkeypatch):
    """A PATCH client whose writes land in a dict instead of Postgres."""
    from fastapi.testclient import TestClient

    from api import main
    import interactive_utils as iu

    seen: dict = {}

    monkeypatch.setattr(iu, "ensure_instances_table", lambda conn: None)
    monkeypatch.setattr(
        iu, "_resolve_instance_doc",
        lambda conn, iid: {"id": iid, "name": "old", "initial_value": 1234.0})
    monkeypatch.setattr(
        iu.store, "update",
        lambda table, key, patch: seen.update(key=key, patch=dict(patch)))
    monkeypatch.setattr(iu.store, "get", lambda table, key: {"id": key})

    main.app.dependency_overrides[main.require_admin] = lambda: ADMIN
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        yield TestClient(main.app), seen
    finally:
        main.app.dependency_overrides.clear()


def test_patch_persists_initial_value(patched):
    client, seen = patched
    res = client.patch("/instances/alpaca-main", json={"initial_value": 6000.0})
    assert res.status_code == 200, res.text
    assert seen["key"] == "alpaca-main"
    assert seen["patch"]["initial_value"] == 6000.0


def test_patch_persists_clean_room_mode(patched):
    client, seen = patched
    res = client.patch("/instances/alpaca-main", json={"clean_room_mode": True})
    assert res.status_code == 200, res.text
    assert seen["patch"]["clean_room_mode"] is True


def test_clean_room_mode_false_is_a_value_not_an_omission(patched):
    """``False`` must reach the row. A truthiness test here would make
    turning clean-room mode OFF impossible through the API."""
    client, seen = patched
    res = client.patch("/instances/alpaca-main", json={"clean_room_mode": False})
    assert res.status_code == 200, res.text
    assert seen["patch"]["clean_room_mode"] is False


@pytest.mark.parametrize("bad", [0, 0.0, -1, -6000.0])
def test_a_non_positive_baseline_is_rejected_at_the_boundary(patched, bad):
    """Zero is the value the preflight already treats as 'unset'; a negative
    baseline would invert every drawdown measurement taken against it."""
    client, seen = patched
    res = client.patch("/instances/alpaca-main", json={"initial_value": bad})
    assert res.status_code == 422, res.text
    assert seen == {}, "a rejected body still wrote to the row"


def test_omitting_the_new_fields_leaves_them_untouched(patched):
    client, seen = patched
    res = client.patch("/instances/alpaca-main", json={"name": "EB live"})
    assert res.status_code == 200, res.text
    assert seen["patch"] == {"name": "EB live"}


def test_the_action_itself_rejects_a_non_positive_baseline(monkeypatch):
    """The CLI and the chatbot reach the action without a Pydantic model."""
    import interactive_utils as iu

    monkeypatch.setattr(iu, "ensure_instances_table", lambda conn: None)
    monkeypatch.setattr(iu, "_resolve_instance_doc",
                        lambda conn, iid: {"id": iid})
    monkeypatch.setattr(iu.store, "update",
                        lambda *a, **k: pytest.fail("wrote a bad baseline"))

    with pytest.raises(ValueError, match="initial_value"):
        iu.action_edit_instance(None, "alpaca-main", initial_value=0)
    with pytest.raises(ValueError, match="initial_value"):
        iu.action_edit_instance(None, "alpaca-main", initial_value="not a number")
