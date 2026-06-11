"""Wiring tests for the /notification-preferences endpoints.

We call the endpoint functions directly (httpx/TestClient is not in the venv,
mirroring the other api tests) and monkeypatch the store actions. The store
logic itself is covered by test_notification_prefs_store.py.
"""
from __future__ import annotations

import os
import sys

import pytest

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def test_get_notification_prefs_returns_store_result(monkeypatch):
    from api import main
    from interactive_utils import _default_notification_categories
    captured = {}

    def _fake_get(conn, uid):
        captured["uid"] = uid
        return {"user_id": uid, "categories": _default_notification_categories()}

    monkeypatch.setattr(main, "action_get_notification_preferences", _fake_get)
    res = main.api_get_notification_prefs(conn=None, current_user={"id": "u1"})
    assert captured["uid"] == "u1"
    assert len(res["categories"]) == 9
    assert res["categories"]["order_fill"] == {"discord": True, "push": False}


def test_put_notification_prefs_persists(monkeypatch):
    from api import main
    saved = {}
    monkeypatch.setattr(
        main, "action_set_notification_preferences",
        lambda conn, uid, cats: saved.update(uid=uid, cats=cats) or {"user_id": uid, "categories": cats},
    )
    body = main.NotificationPrefsBody(categories={"order_fill": {"discord": False, "push": True}})
    res = main.api_put_notification_prefs(body=body, conn=None, current_user={"id": "u1"})
    assert saved["uid"] == "u1"
    assert saved["cats"]["order_fill"] == {"discord": False, "push": True}
    assert res["categories"]["order_fill"] == {"discord": False, "push": True}


def test_put_unknown_category_is_400(monkeypatch):
    from api import main
    from fastapi import HTTPException

    def _raise(conn, uid, cats):
        from interactive_utils import _validate_notification_categories
        return _validate_notification_categories(cats)  # raises ValueError on unknown

    monkeypatch.setattr(main, "action_set_notification_preferences", _raise)
    body = main.NotificationPrefsBody(categories={"not_a_category": {"discord": True, "push": False}})
    with pytest.raises(HTTPException) as ei:
        main.api_put_notification_prefs(body=body, conn=None, current_user={"id": "u1"})
    assert ei.value.status_code == 400
