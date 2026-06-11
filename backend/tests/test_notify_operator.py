"""Operator resolution for the live-alert push path.

The live path passes no user_id, so notify() must resolve WHO to push to. The
canonical signal is the user who registered a push device (their own account —
there is no special admin user). Env override wins; results are cached.
"""
from __future__ import annotations


def test_env_override_wins(monkeypatch):
    import notifications
    notifications._reset_operator_cache()
    monkeypatch.setenv("NOTIFY_OPERATOR_USER_ID", "env-user")
    assert notifications._operator_user_id() == "env-user"


def test_resolves_single_device_user(monkeypatch):
    import notifications
    notifications._reset_operator_cache()
    monkeypatch.delenv("NOTIFY_OPERATOR_USER_ID", raising=False)
    monkeypatch.setattr(notifications, "_resolve_operator", lambda: "device-user-1")
    assert notifications._operator_user_id() == "device-user-1"


def test_caches_resolution(monkeypatch):
    import notifications
    notifications._reset_operator_cache()
    monkeypatch.delenv("NOTIFY_OPERATOR_USER_ID", raising=False)
    n = {"calls": 0}
    def _resolve():
        n["calls"] += 1
        return "u1"
    monkeypatch.setattr(notifications, "_resolve_operator", _resolve)
    assert notifications._operator_user_id() == "u1"
    assert notifications._operator_user_id() == "u1"
    assert n["calls"] == 1  # second call hit the cache


def test_resolve_operator_uses_device_user(monkeypatch):
    import notifications
    import interactive_utils
    monkeypatch.setattr(interactive_utils, "get_conn", lambda *a, **k: object())
    monkeypatch.setattr(interactive_utils, "action_push_device_user_ids", lambda conn: ["user-xyz"])
    assert notifications._resolve_operator() == "user-xyz"


def test_resolve_operator_none_when_ambiguous(monkeypatch):
    import notifications
    import interactive_utils
    monkeypatch.setattr(interactive_utils, "get_conn", lambda *a, **k: object())
    monkeypatch.setattr(interactive_utils, "action_push_device_user_ids", lambda conn: ["a", "b"])
    assert notifications._resolve_operator() is None


def test_resolve_operator_falls_back_to_sole_user(monkeypatch):
    import notifications
    import interactive_utils
    import auth_utils
    monkeypatch.setattr(interactive_utils, "get_conn", lambda *a, **k: object())
    monkeypatch.setattr(interactive_utils, "action_push_device_user_ids", lambda conn: [])
    monkeypatch.setattr(auth_utils, "list_users", lambda conn: [{"id": "only-user"}])
    assert notifications._resolve_operator() == "only-user"
