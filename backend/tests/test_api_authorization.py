"""Order authority and user administration are admin-only.

Before this, every authenticated user was an administrator in all but name:
any account could rewrite another account's password and role, mint a new
admin, start or stop a live instance, relink its brokerage, edit the strategy
it trades, or submit an order through /live-command. On a real-money Alpaca
instance behind a public API that is the whole security model.

These tests pin two things: the dependency each mutating route is wired to,
and the behaviour of the dependency itself. They also pin that the operator
cannot lock themselves out -- the DEFAULT_ADMIN_USERNAME account is created
with role admin and passes require_admin.
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest
from fastapi import HTTPException

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


# Every mutating route that can move money, change what trades, or change who
# may do either.
ADMIN_ONLY_ROUTES = [
    ("POST", "/instances"),
    ("PATCH", "/instances/{instance_id}"),
    ("DELETE", "/instances/{instance_id}"),
    ("POST", "/instances/{instance_id}/stocks"),
    ("DELETE", "/instances/{instance_id}/stocks/{symbol}"),
    ("POST", "/instances/{instance_id}/start"),
    ("POST", "/instances/{instance_id}/stop"),
    ("POST", "/instances/{instance_id}/clear-state"),
    ("POST", "/instances/{instance_id}/link-strategy"),
    ("POST", "/instances/{instance_id}/unlink-strategy"),
    ("POST", "/instances/{instance_id}/link-brokerage"),
    ("POST", "/instances/{instance_id}/link-data-brokerage"),
    ("POST", "/instances/{instance_id}/live-command"),
    ("PATCH", "/instances/{instance_id}/kalshi/config"),
    ("POST", "/strategies"),
    ("PUT", "/strategies/{strategy_id}"),
    ("DELETE", "/strategies/{strategy_id}"),
    ("POST", "/brokerages"),
    ("PUT", "/brokerages/{brokerage_id}"),
    ("DELETE", "/brokerages/{brokerage_id}"),
    ("POST", "/brokerages/ensure-ai-alpaca"),
    ("POST", "/brokerages/{brokerage_id}/kalshi/kill"),
    ("POST", "/brokerages/{brokerage_id}/kalshi/instances"),
    ("POST", "/brokerages/{brokerage_id}/kalshi/backtests"),
    ("POST", "/auth/users"),
    ("DELETE", "/auth/users/{user_id}"),
]

# Read-only routes stay open to any authenticated user.
READ_ONLY_ROUTES = [
    ("GET", "/instances"),
    ("GET", "/instances/{instance_id}"),
    ("GET", "/strategies"),
    ("GET", "/brokerages"),
    ("GET", "/auth/users"),
    ("GET", "/auth/me"),
]


def _endpoint(method: str, path: str):
    from api import main

    for route in main.app.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", ()):
            return route.endpoint
    raise AssertionError(f"no route for {method} {path}")


def _guard_of(method: str, path: str):
    """The dependency behind the endpoint's ``current_user`` parameter."""
    param = inspect.signature(_endpoint(method, path)).parameters.get("current_user")
    assert param is not None, f"{method} {path} has no current_user parameter"
    return getattr(param.default, "dependency", None)


# --- the dependency -------------------------------------------------------


def test_require_admin_accepts_an_admin():
    from api.main import require_admin

    user = {"id": "u1", "username": "root", "role": "admin"}
    assert require_admin(user) is user


def test_require_admin_is_case_and_whitespace_insensitive():
    from api.main import require_admin

    assert require_admin({"id": "u1", "username": "root", "role": " Admin "})


@pytest.mark.parametrize("role", ["user", "", None, "administrator", "Admin ", "superuser"])
def test_require_admin_rejects_everything_that_is_not_the_admin_role(role):
    from api.main import require_admin

    if role == "Admin ":
        pytest.skip("covered by the case-insensitivity test")
    with pytest.raises(HTTPException) as ei:
        require_admin({"id": "u2", "username": "bob", "role": role})
    assert ei.value.status_code == 403


def test_require_admin_rejects_a_user_with_no_role_key_at_all():
    from api.main import require_admin

    with pytest.raises(HTTPException) as ei:
        require_admin({"id": "u2", "username": "bob"})
    assert ei.value.status_code == 403


# --- route wiring ---------------------------------------------------------


@pytest.mark.parametrize("method,path", ADMIN_ONLY_ROUTES)
def test_mutating_routes_require_admin(method, path):
    from api.main import require_admin

    assert _guard_of(method, path) is require_admin, (
        f"{method} {path} is not behind require_admin"
    )


@pytest.mark.parametrize("method,path", READ_ONLY_ROUTES)
def test_read_only_routes_stay_open_to_any_authenticated_user(method, path):
    from api.main import get_current_user

    assert _guard_of(method, path) is get_current_user


def test_put_auth_user_stays_self_service_at_the_dependency_level():
    """It is the ONE mutating auth route that a non-admin may call -- to
    change their own password or email. The role check is inside the body."""
    from api.main import get_current_user

    assert _guard_of("PUT", "/auth/users/{user_id}") is get_current_user


# --- PUT /auth/users/{id} ------------------------------------------------


def _put(monkeypatch, *, target, actor, **body_kwargs):
    from api import main

    calls = {}

    def _fake_update(conn, user_id, password=None, role=None, email=None):
        calls.update(user_id=user_id, password=password, role=role, email=email)
        return {"id": user_id, "username": "target", "role": role or "user"}

    monkeypatch.setattr(main, "update_user", _fake_update)
    body = main.UpdateUserBody(**body_kwargs)
    res = main.api_update_auth_user(
        user_id=target, body=body, conn=None, current_user=actor)
    return res, calls


ADMIN = {"id": "admin-1", "username": "root", "role": "admin"}
BOB = {"id": "bob-1", "username": "bob", "role": "user"}


def test_a_user_may_change_their_own_password(monkeypatch):
    _res, calls = _put(monkeypatch, target="bob-1", actor=BOB, password="newpassword")
    assert calls["user_id"] == "bob-1" and calls["password"] == "newpassword"


def test_a_user_may_not_change_someone_elses_password(monkeypatch):
    with pytest.raises(HTTPException) as ei:
        _put(monkeypatch, target="admin-1", actor=BOB, password="pwned-password")
    assert ei.value.status_code == 403


def test_a_user_may_not_grant_themselves_the_admin_role(monkeypatch):
    with pytest.raises(HTTPException) as ei:
        _put(monkeypatch, target="bob-1", actor=BOB, role="admin")
    assert ei.value.status_code == 403


def test_a_user_may_not_change_their_own_role_to_anything(monkeypatch):
    with pytest.raises(HTTPException) as ei:
        _put(monkeypatch, target="bob-1", actor=BOB, role="user")
    assert ei.value.status_code == 403


def test_an_admin_may_change_any_users_password_and_role(monkeypatch):
    _res, calls = _put(monkeypatch, target="bob-1", actor=ADMIN,
                       password="resetpassword", role="admin")
    assert calls["user_id"] == "bob-1"
    assert calls["role"] == "admin"


def test_a_user_may_change_their_own_email(monkeypatch):
    _res, calls = _put(monkeypatch, target="bob-1", actor=BOB, email="bob@example.com")
    assert calls["email"] == "bob@example.com"


# --- the operator is not locked out --------------------------------------


def test_the_default_admin_is_created_with_the_admin_role_and_passes_require_admin(
    store, monkeypatch
):
    """DEFAULT_ADMIN_USERNAME must still hold admin authority after this
    change, or the operator cannot administer their own deployment."""
    import auth_utils
    from api.main import require_admin

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", "operator")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "a-sufficiently-long-password")

    auth_utils.ensure_default_admin(None)
    row = auth_utils.get_user_by_username(None, "operator")
    assert row is not None and row["role"] == "admin"

    # Exactly the dict get_current_user hands the dependency.
    current_user = {
        "id": row.get("id"),
        "username": row.get("username"),
        "role": row.get("role", "user"),
    }
    assert require_admin(current_user) is current_user


def test_the_default_admin_can_still_reach_every_admin_only_route(store, monkeypatch):
    """One assertion per route, using the real dependency, so a future route
    added to the admin set cannot silently exclude the operator."""
    import auth_utils
    from api.main import require_admin

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", "operator")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "a-sufficiently-long-password")
    auth_utils.ensure_default_admin(None)
    row = auth_utils.get_user_by_username(None, "operator")
    operator = {"id": row["id"], "username": row["username"], "role": row["role"]}

    for method, path in ADMIN_ONLY_ROUTES:
        guard = _guard_of(method, path)
        assert guard(operator) is operator, f"operator refused by {method} {path}"


def test_create_user_route_is_admin_only_and_still_mints_admins_for_an_admin(monkeypatch):
    from api import main

    monkeypatch.setattr(
        main, "create_user",
        lambda conn, username, password, role="user", email=None: {
            "id": "new", "username": username, "role": role},
    )
    body = main.CreateUserBody(username="second-admin", password="hunter22", role="admin")
    got = main.api_create_auth_user(body=body, conn=None, current_user=ADMIN)
    assert got["role"] == "admin"
