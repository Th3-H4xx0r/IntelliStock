"""Being signed in is the authorization model.

There used to be a second tier: ``require_admin``, on every route that could
move money or change who may. The operator's decision (2026-09-11) is that
this deployment has exactly one class of user -- everyone who can log in is
the operator -- so the role tier is gone. A role column still exists and is
still reported, but nothing reads it to decide anything.

That makes authentication the *whole* model, which is why the inverse test
below is the important one in this file: every non-GET route in the app must
refuse an unauthenticated caller with 401, and the only exceptions are the
handful named in PUBLIC_ROUTES with a reason. A new mutating route that
forgets ``current_user`` is a test failure, not a quiet hole.

What did NOT change: a token is still validated on every request, a password
change still revokes every token minted against the old one, and the
DEFAULT_ADMIN_USERNAME account is still provisioned on first boot.
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


# Read-only routes are open to any authenticated user, and always were.
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


def _mutating_routes():
    """(method, path) for every non-GET route in the app."""
    from api import main

    out = []
    for route in main.app.routes:
        methods = set(getattr(route, "methods", ()) or ())
        for method in sorted(methods - {"GET", "HEAD", "OPTIONS"}):
            out.append((method, route.path))
    return out


# --- the dependency -------------------------------------------------------


def test_there_is_no_role_tier_left_to_gate_on():
    """``require_admin`` is gone, not merely unused: an import of it is the
    cheapest way for a future route to reintroduce the tier by accident."""
    from api import main

    assert not hasattr(main, "require_admin")


# --- route wiring ---------------------------------------------------------


@pytest.mark.parametrize("method,path", READ_ONLY_ROUTES)
def test_read_only_routes_stay_open_to_any_authenticated_user(method, path):
    from api.main import get_current_user

    assert _guard_of(method, path) is get_current_user


def test_put_auth_user_is_wired_to_the_authenticated_user_dependency():
    from api.main import get_current_user

    assert _guard_of("PUT", "/auth/users/{user_id}") is get_current_user


# ---------------------------------------------------------------------------
# The inverse test.
#
# Listing the routes that ARE protected can only ever prove what it lists.
# This goes the other way, and over the HTTP surface rather than the
# signature: an unauthenticated request to every non-GET route must come back
# 401. The five below are the deliberate exceptions -- each is reachable
# without a user session on purpose, and says why.
# ---------------------------------------------------------------------------

PUBLIC_ROUTES = {
    ("POST", "/auth/login"):
        "public: this IS the authentication, and it is rate limited",
    ("POST", "/auth/signup"):
        "public: gated by SECRET_AUTH_KEY, not by a session",
    ("POST", "/llm/outputs"):
        "loopback-only (_require_loopback): llm_utils posts to it in-process",
    ("POST", "/chatbot/internal/mcp-tools-list"):
        "authenticated by the MCP session token, not a user session",
    ("POST", "/chatbot/internal/mcp-tool-call"):
        "authenticated by the MCP session token, not a user session",
}

# A token that is syntactically a bearer token and cryptographically nothing.
# Sending one exercises the same refusal path a stolen or expired token takes.
BAD_TOKEN = {"Authorization": "Bearer not-a-real-token"}


@pytest.fixture
def anon(monkeypatch):
    """A client with no session, and no database behind it.

    ``get_current_user`` refuses before it ever reaches the connection, so
    overriding ``conn_dependency`` is only here to keep a route that resolves
    it first from dialling a database this test does not need.
    """
    from fastapi.testclient import TestClient

    from api import main

    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        # No context manager: startup would provision the default user.
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)


def _concrete(path: str) -> str:
    """Fill path params with a placeholder -- authentication is refused long
    before anything looks at what the id means."""
    out = []
    for part in path.split("/"):
        out.append("x" if part.startswith("{") and part.endswith("}") else part)
    return "/".join(out)


@pytest.mark.parametrize(
    "method,path",
    [r for r in _mutating_routes() if r not in PUBLIC_ROUTES],
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_every_mutating_route_refuses_an_unauthenticated_caller(anon, method, path):
    res = anon.request(method, _concrete(path), json={}, headers=BAD_TOKEN)
    assert res.status_code == 401, (
        f"{method} {path} answered {res.status_code} to a caller with no "
        f"valid session: {res.text[:200]}"
    )


def test_the_public_route_list_has_no_dead_entries():
    """An entry that no longer names a live route is stale -- it hides the
    next route that needs classifying."""
    live = set(_mutating_routes())
    stale = sorted(PUBLIC_ROUTES.keys() - live)
    assert not stale, f"public routes that are gone: {stale}"


def test_every_public_route_carries_a_real_reason():
    thin = {k: v for k, v in PUBLIC_ROUTES.items()
            if not isinstance(v, str) or len(v.strip()) < 20}
    assert not thin, f"public routes without a usable reason: {sorted(thin)}"


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


def test_a_user_may_change_their_own_email(monkeypatch):
    _res, calls = _put(monkeypatch, target="bob-1", actor=BOB, email="bob@example.com")
    assert calls["email"] == "bob@example.com"


def test_any_signed_in_user_may_change_another_users_password(monkeypatch):
    """Operator decision 2026-09-11: one class of user, so user administration
    is not a separate privilege."""
    _res, calls = _put(monkeypatch, target="admin-1", actor=BOB, password="reset-password")
    assert calls["user_id"] == "admin-1" and calls["password"] == "reset-password"


def test_any_signed_in_user_may_change_a_role(monkeypatch):
    """The role field is still writable; nothing reads it to decide anything."""
    _res, calls = _put(monkeypatch, target="bob-1", actor=BOB, role="admin")
    assert calls["role"] == "admin"


def test_deleting_yourself_is_still_refused(monkeypatch):
    """Not an authorization rule -- it is the one way to leave a deployment
    with no accounts at all."""
    from api import main

    monkeypatch.setattr(main, "delete_user", lambda conn, user_id: None)
    with pytest.raises(HTTPException) as ei:
        main.api_delete_auth_user(user_id="bob-1", conn=None, current_user=BOB)
    assert ei.value.status_code == 400


def test_any_signed_in_user_may_delete_another_account(monkeypatch):
    from api import main

    seen = {}
    monkeypatch.setattr(main, "delete_user",
                        lambda conn, user_id: seen.update(user_id=user_id))
    got = main.api_delete_auth_user(user_id="admin-1", conn=None, current_user=BOB)
    assert got["deleted"] is True and seen["user_id"] == "admin-1"


def test_the_create_user_route_still_mints_a_user_for_any_signed_in_caller(monkeypatch):
    from api import main

    monkeypatch.setattr(
        main, "create_user",
        lambda conn, username, password, role="user", email=None: {
            "id": "new", "username": username, "role": role},
    )
    body = main.CreateUserBody(username="second-operator", password="hunter22", role="admin")
    got = main.api_create_auth_user(body=body, conn=None, current_user=BOB)
    assert got["role"] == "admin"


# --- first boot provisions an account ------------------------------------


def test_the_default_account_is_created_on_first_boot(store, monkeypatch):
    """DEFAULT_ADMIN_USERNAME is now just the name of the first account, but
    it still has to exist or nobody can log in at all."""
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", "operator")
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "a-sufficiently-long-password")

    auth_utils.ensure_default_admin(None)
    row = auth_utils.get_user_by_username(None, "operator")
    assert row is not None
    assert auth_utils.verify_password("a-sufficiently-long-password",
                                      row["password_hash"])


# ---------------------------------------------------------------------------
# The session round trip, end to end.
#
# The tests above call route bodies with a hand-built dict. That proves the
# body's rules; it does NOT prove that a real login mints a token that
# survives login -> token_version claim -> get_current_user, or that a
# password change kills the tokens minted before it.
# ---------------------------------------------------------------------------

OPERATOR = "operator"
OPERATOR_PASSWORD = "a-sufficiently-long-password"
AUTHED_ROUTE = "/instances/eb/stop"


@pytest.fixture
def live_api(store, monkeypatch):
    """The real app over the test store, with the DB-touching bits caged.

    Only auth is exercised: the action itself is stubbed so the assertion is
    about the status code AUTHENTICATION produced, not about whether an
    instance exists.
    """
    from fastapi.testclient import TestClient
    import auth_utils
    from api import main

    monkeypatch.setenv("JWT_SECRET", "test-signing-secret-for-the-round-trip")
    monkeypatch.delenv("JWT_EXPIRE_HOURS", raising=False)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", OPERATOR)
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", OPERATOR_PASSWORD)
    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setattr(main, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setattr(main, "action_stop_instance",
                        lambda conn, instance_id: {"stopped": True, "id": instance_id})
    auth_utils.reset_login_rate_limit()

    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        with TestClient(main.app) as client:
            yield client, auth_utils
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)
        auth_utils.reset_login_rate_limit()


def _login(client, username, password):
    return client.post("/auth/login", json={"username": username, "password": password})


def test_a_plain_user_can_log_in_and_use_a_mutating_route_end_to_end(live_api):
    """login -> token (with token_version) -> get_current_user -> the route.
    The account is deliberately role 'user': the role decides nothing."""
    client, auth_utils = live_api
    auth_utils.create_user(None, "bob", "bobs-long-password", role="user")

    res = _login(client, "bob", "bobs-long-password")
    assert res.status_code == 200, res.text

    got = client.post(AUTHED_ROUTE,
                      headers={"Authorization": f"Bearer {res.json()['access_token']}"})
    assert got.status_code == 200, got.text


def test_a_password_change_kills_the_old_token_and_a_fresh_login_works(live_api):
    """The revocation, proven through the HTTP surface."""
    client, auth_utils = live_api
    auth_utils.ensure_default_admin(None)

    old_token = _login(client, OPERATOR, OPERATOR_PASSWORD).json()["access_token"]
    old_auth = {"Authorization": f"Bearer {old_token}"}
    assert client.post(AUTHED_ROUTE, headers=old_auth).status_code == 200

    row = auth_utils.get_user_by_username(None, OPERATOR)
    changed = client.put(
        f"/auth/users/{row['id']}",
        headers=old_auth,
        json={"password": "an-even-longer-new-password"},
    )
    assert changed.status_code == 200, changed.text

    # The token minted against the old password is dead...
    assert client.post(AUTHED_ROUTE, headers=old_auth).status_code == 401
    # ...and the operator is not locked out: the new password works.
    again = _login(client, OPERATOR, "an-even-longer-new-password")
    assert again.status_code == 200, again.text
    fresh = {"Authorization": f"Bearer {again.json()['access_token']}"}
    assert client.post(AUTHED_ROUTE, headers=fresh).status_code == 200


def test_the_old_password_no_longer_logs_in(live_api):
    client, auth_utils = live_api
    auth_utils.ensure_default_admin(None)
    row = auth_utils.get_user_by_username(None, OPERATOR)
    auth_utils.update_user(None, row["id"], password="an-even-longer-new-password")

    assert _login(client, OPERATOR, OPERATOR_PASSWORD).status_code == 401


# --- ensure_default_admin leaves an existing account alone ----------------


def test_ensure_default_admin_does_not_reset_an_existing_account(store, monkeypatch):
    """It is first-boot provisioning, not a recurring reset: an account that
    already exists keeps its password and its sessions."""
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", OPERATOR)
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", "a-completely-different-password")

    auth_utils.create_user(None, OPERATOR, OPERATOR_PASSWORD, role="user")
    before = auth_utils.get_user_by_username(None, OPERATOR)

    auth_utils.ensure_default_admin(None)

    after = auth_utils.get_user_by_username(None, OPERATOR)
    assert after["password_hash"] == before["password_hash"]
    assert auth_utils.token_version_of(after) == auth_utils.token_version_of(before)
    assert auth_utils.verify_password(OPERATOR_PASSWORD, after["password_hash"])


def test_ensure_default_admin_is_quiet_about_an_account_it_leaves_alone(
    store, monkeypatch, capsys
):
    """Nothing about the role decides anything any more, so provisioning must
    not print something that reads like a security event."""
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", OPERATOR)
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", OPERATOR_PASSWORD)
    auth_utils.create_user(None, OPERATOR, OPERATOR_PASSWORD, role="user")
    capsys.readouterr()

    auth_utils.ensure_default_admin(None)

    captured = capsys.readouterr()
    printed = (captured.err + captured.out).lower()
    for alarming in ("promot", "must be able to administer", "security"):
        assert alarming not in printed, printed


def test_a_second_account_is_never_touched(store, monkeypatch):
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setenv("DEFAULT_ADMIN_USERNAME", OPERATOR)
    monkeypatch.setenv("DEFAULT_ADMIN_PASSWORD", OPERATOR_PASSWORD)
    auth_utils.create_user(None, OPERATOR, OPERATOR_PASSWORD, role="admin")
    auth_utils.create_user(None, "bob", "bobs-long-password", role="user")
    before = auth_utils.get_user_by_username(None, "bob")

    auth_utils.ensure_default_admin(None)

    assert auth_utils.get_user_by_username(None, "bob") == before
