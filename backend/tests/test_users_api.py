"""The Users API: who may create an account, and who may delete one.

There is no admin tier and no bootstrap password in the environment any more
(operator decision 2026-09-11). Two consequences have to be tested, because
either one getting this wrong locks a deployment out of itself:

1. **First run.** With an empty Users table, ``POST /auth/users`` is reachable
   without a session -- that is the only way to create the very first account.
   The moment one row exists the exception closes, and it must stay closed for
   every existing deployment, which already has users.
2. **Last account.** Deleting yourself, or deleting the last remaining user,
   is refused. Not as an authorization rule -- as the one call that can leave
   a deployment with nobody able to log in.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


BOB = {"id": "bob-1", "username": "bob"}


@pytest.fixture
def users_app(store, monkeypatch):
    """The real app over the test store, with auth wired to it.

    Yields a TestClient with no session. Requests that need one carry a token
    minted by logging in through the app itself.
    """
    from fastapi.testclient import TestClient
    import auth_utils
    from api import main

    monkeypatch.setenv("JWT_SECRET", "test-signing-secret-for-the-users-api")
    monkeypatch.delenv("JWT_EXPIRE_HOURS", raising=False)
    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    monkeypatch.setattr(main, "ensure_users_table", lambda conn=None: None)
    auth_utils.reset_login_rate_limit()

    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        with TestClient(main.app) as client:
            yield client, auth_utils
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)
        auth_utils.reset_login_rate_limit()


def _token(client, username, password):
    res = client.post("/auth/login",
                      json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


# --- first-run bootstrap --------------------------------------------------


def test_an_empty_table_lets_an_unauthenticated_caller_create_the_first_account(users_app):
    client, auth_utils = users_app

    res = client.post("/auth/users",
                      json={"username": "first-operator", "password": "a-long-password"})

    assert res.status_code == 200, res.text
    assert res.json()["username"] == "first-operator"
    assert auth_utils.get_user_by_username(None, "first-operator") is not None


def test_the_first_account_can_immediately_log_in(users_app):
    client, _auth_utils = users_app
    client.post("/auth/users",
                json={"username": "first-operator", "password": "a-long-password"})

    assert _token(client, "first-operator", "a-long-password")


def test_the_bootstrap_exception_closes_as_soon_as_one_user_exists(users_app):
    """The important half. Every existing deployment already has users, so the
    unauthenticated path must be inert there."""
    client, auth_utils = users_app
    auth_utils.create_user(None, "someone", "an-existing-password")

    res = client.post("/auth/users",
                      json={"username": "intruder", "password": "a-long-password"})

    assert res.status_code == 401, res.text
    assert auth_utils.get_user_by_username(None, "intruder") is None


def test_a_signed_in_caller_creates_users_once_the_table_is_populated(users_app):
    client, auth_utils = users_app
    auth_utils.create_user(None, "someone", "an-existing-password")
    auth = _token(client, "someone", "an-existing-password")

    res = client.post("/auth/users", headers=auth,
                      json={"username": "colleague", "password": "another-password"})

    assert res.status_code == 200, res.text
    assert res.json()["username"] == "colleague"


def test_an_invalid_token_is_refused_even_when_the_table_is_empty(users_app):
    """A caller who presents a token presents it to be checked. Falling back
    to the bootstrap path for a bad token would make the exception reachable
    with a forged header instead of only on a genuinely empty deployment."""
    client, _auth_utils = users_app

    res = client.post("/auth/users",
                      headers={"Authorization": "Bearer not-a-real-token"},
                      json={"username": "intruder", "password": "a-long-password"})

    assert res.status_code == 401, res.text


def test_login_tells_the_client_when_no_users_exist(users_app):
    """The login page cannot call GET /auth/users before there is a session,
    so the 401 body carries the signal that turns on 'create the first
    account'."""
    client, _auth_utils = users_app

    res = client.post("/auth/login",
                      json={"username": "anyone", "password": "anything"})

    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "no_users"


def test_a_normal_failed_login_does_not_carry_the_bootstrap_signal(users_app):
    client, auth_utils = users_app
    auth_utils.create_user(None, "someone", "an-existing-password")

    res = client.post("/auth/login",
                      json={"username": "someone", "password": "the-wrong-password"})

    assert res.status_code == 401
    detail = res.json()["detail"]
    assert not (isinstance(detail, dict) and detail.get("code") == "no_users")


# --- create: validation ---------------------------------------------------


def test_a_duplicate_username_is_a_409(users_app):
    client, auth_utils = users_app
    auth_utils.create_user(None, "someone", "an-existing-password")
    auth = _token(client, "someone", "an-existing-password")

    res = client.post("/auth/users", headers=auth,
                      json={"username": "someone", "password": "another-password"})

    assert res.status_code == 409, res.text


def test_a_short_password_is_refused_before_it_reaches_the_store(users_app):
    client, auth_utils = users_app
    auth_utils.create_user(None, "someone", "an-existing-password")
    auth = _token(client, "someone", "an-existing-password")

    res = client.post("/auth/users", headers=auth,
                      json={"username": "colleague", "password": "short"})

    assert res.status_code == 422, res.text
    assert auth_utils.get_user_by_username(None, "colleague") is None


def test_the_bootstrap_path_enforces_the_same_password_rule(users_app):
    client, auth_utils = users_app

    res = client.post("/auth/users",
                      json={"username": "first-operator", "password": "short"})

    assert res.status_code == 422, res.text
    assert auth_utils.get_user_by_username(None, "first-operator") is None


# --- read -----------------------------------------------------------------


def test_listing_users_never_returns_a_password_hash(users_app):
    client, auth_utils = users_app
    auth_utils.create_user(None, "someone", "an-existing-password")
    auth_utils.create_user(None, "colleague", "another-password")
    auth = _token(client, "someone", "an-existing-password")

    body = client.get("/auth/users", headers=auth).json()

    assert {u["username"] for u in body["users"]} == {"someone", "colleague"}
    for user in body["users"]:
        assert "password_hash" not in user
        assert user["id"] and user["created_at"]


def test_listing_users_needs_a_session(users_app):
    client, auth_utils = users_app
    auth_utils.create_user(None, "someone", "an-existing-password")

    assert client.get("/auth/users").status_code == 401


# --- delete ---------------------------------------------------------------


def test_deleting_yourself_is_refused(users_app):
    client, auth_utils = users_app
    auth_utils.create_user(None, "someone", "an-existing-password")
    auth_utils.create_user(None, "colleague", "another-password")
    auth = _token(client, "someone", "an-existing-password")
    me = auth_utils.get_user_by_username(None, "someone")

    res = client.delete(f"/auth/users/{me['id']}", headers=auth)

    assert res.status_code == 400, res.text
    assert auth_utils.get_user_by_id(None, me["id"]) is not None


def test_deleting_the_last_user_is_refused(users_app):
    """Reachable the moment a two-account deployment deletes one: the
    remaining account must not be able to delete the other one and then
    itself into an empty table."""
    client, auth_utils = users_app
    auth_utils.create_user(None, "someone", "an-existing-password")
    auth_utils.create_user(None, "colleague", "another-password")
    auth = _token(client, "someone", "an-existing-password")
    colleague = auth_utils.get_user_by_username(None, "colleague")

    assert client.delete(f"/auth/users/{colleague['id']}",
                         headers=auth).status_code == 200

    # Only one row left, and it is the caller's -- both guards agree.
    me = auth_utils.get_user_by_username(None, "someone")
    res = client.delete(f"/auth/users/{me['id']}", headers=auth)
    assert res.status_code == 400, res.text
    assert auth_utils.count_users(None) == 1


def test_the_last_user_guard_holds_even_for_someone_elses_row(monkeypatch):
    """The self-check and the last-user check are separate rules. Prove the
    second one on its own, with a caller who is not the row being deleted."""
    from api import main

    monkeypatch.setattr(main, "count_users", lambda conn: 1)
    monkeypatch.setattr(main, "delete_user",
                        lambda conn, user_id: pytest.fail("should not delete"))

    with pytest.raises(HTTPException) as ei:
        main.api_delete_auth_user(user_id="someone-else", conn=None, current_user=BOB)
    assert ei.value.status_code == 400


def test_any_signed_in_user_may_delete_another_account(users_app):
    client, auth_utils = users_app
    auth_utils.create_user(None, "someone", "an-existing-password")
    auth_utils.create_user(None, "colleague", "another-password")
    auth = _token(client, "someone", "an-existing-password")
    colleague = auth_utils.get_user_by_username(None, "colleague")

    res = client.delete(f"/auth/users/{colleague['id']}", headers=auth)

    assert res.status_code == 200 and res.json()["deleted"] is True
    assert auth_utils.get_user_by_id(None, colleague["id"]) is None


# --- roles are gone -------------------------------------------------------


def test_the_create_body_no_longer_accepts_a_role(users_app):
    """An ignored-but-accepted ``role`` is how the tier comes back: a caller
    keeps sending it and someone eventually reads it."""
    from api import main

    assert "role" not in main.CreateUserBody.model_fields
    assert "role" not in main.UpdateUserBody.model_fields


def test_a_new_user_row_carries_no_role(store, monkeypatch):
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    auth_utils.create_user(None, "someone", "an-existing-password")

    assert "role" not in auth_utils.get_user_by_username(None, "someone")


def test_the_token_carries_no_role_claim(monkeypatch):
    import auth_utils
    import jwt

    monkeypatch.setenv("JWT_SECRET", "test-signing-secret-for-the-users-api")
    claims = jwt.decode(auth_utils.create_access_token("u1", "someone"),
                        "test-signing-secret-for-the-users-api",
                        algorithms=["HS256"])

    assert "role" not in claims
