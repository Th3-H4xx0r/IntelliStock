"""Login rate limiting, a 24-hour token, and revocation on password change.

Three gaps in one flow. POST /auth/login accepted unlimited attempts, so a
password was only as strong as the time an attacker was willing to spend.
Tokens lived 720 hours by default and renewed themselves past half-life, so a
stolen one was good for a month and then some. And nothing revoked a token
when the password behind it changed -- the one action an operator takes
*because* they think a credential leaked.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


@pytest.fixture(autouse=True)
def _clean_limiter():
    import auth_utils
    auth_utils.reset_login_rate_limit()
    yield
    auth_utils.reset_login_rate_limit()


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-signing-secret")
    monkeypatch.delenv("JWT_EXPIRE_HOURS", raising=False)


# --- 1. rate limit --------------------------------------------------------


def test_the_limit_is_ten_attempts_in_fifteen_minutes_by_default(monkeypatch):
    import auth_utils
    monkeypatch.delenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", raising=False)
    assert auth_utils.login_rate_limit_config() == (10, 900)


def test_env_overrides_the_limit(monkeypatch):
    import auth_utils
    monkeypatch.setenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")
    assert auth_utils.login_rate_limit_config() == (3, 60)


def test_the_eleventh_failure_in_the_window_is_limited():
    import auth_utils
    t0 = 1_000_000.0
    for i in range(10):
        assert not auth_utils.login_is_rate_limited("root", "1.2.3.4", now=t0 + i)
        auth_utils.record_failed_login("root", "1.2.3.4", now=t0 + i)
    assert auth_utils.login_is_rate_limited("root", "1.2.3.4", now=t0 + 10)


def test_the_bucket_is_per_username_and_per_client_host():
    import auth_utils
    t0 = 1_000_000.0
    for i in range(10):
        auth_utils.record_failed_login("root", "1.2.3.4", now=t0 + i)
    assert auth_utils.login_is_rate_limited("root", "1.2.3.4", now=t0 + 10)
    assert not auth_utils.login_is_rate_limited("root", "5.6.7.8", now=t0 + 10)
    assert not auth_utils.login_is_rate_limited("bob", "1.2.3.4", now=t0 + 10)


def test_attempts_age_out_of_the_window():
    import auth_utils
    t0 = 1_000_000.0
    for i in range(10):
        auth_utils.record_failed_login("root", "1.2.3.4", now=t0 + i)
    assert auth_utils.login_is_rate_limited("root", "1.2.3.4", now=t0 + 10)
    assert not auth_utils.login_is_rate_limited("root", "1.2.3.4", now=t0 + 901)


def test_a_successful_login_clears_the_bucket():
    import auth_utils
    t0 = 1_000_000.0
    for i in range(9):
        auth_utils.record_failed_login("root", "1.2.3.4", now=t0 + i)
    auth_utils.clear_login_attempts("root", "1.2.3.4")
    for i in range(10):
        assert not auth_utils.login_is_rate_limited("root", "1.2.3.4", now=t0 + 20 + i)
        auth_utils.record_failed_login("root", "1.2.3.4", now=t0 + 20 + i)


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, host="1.2.3.4"):
        self.client = _FakeClient(host)


def test_login_returns_429_once_the_bucket_is_full(monkeypatch):
    from api import main

    monkeypatch.setattr(main, "get_user_by_username", lambda conn, u: None)
    # Users exist -- this is a wrong password, not an empty deployment.
    monkeypatch.setattr(main, "count_users", lambda conn: 3)
    body = main.LoginBody(username="root", password="wrong-password")

    for _ in range(10):
        with pytest.raises(HTTPException) as ei:
            main.api_login(body=body, request=_FakeRequest(), conn=None)
        assert ei.value.status_code == 401

    with pytest.raises(HTTPException) as ei:
        main.api_login(body=body, request=_FakeRequest(), conn=None)
    assert ei.value.status_code == 429


def test_a_correct_password_still_works_from_a_different_host(monkeypatch):
    from api import main
    import auth_utils

    user = {"id": "u1", "username": "root", "password_hash": "h"}
    monkeypatch.setattr(main, "get_user_by_username", lambda conn, u: user)
    monkeypatch.setattr(main, "verify_password", lambda p, h: p == "right")
    for i in range(10):
        auth_utils.record_failed_login("root", "1.2.3.4", now=1_000_000.0 + i)

    body = main.LoginBody(username="root", password="right")
    out = main.api_login(body=body, request=_FakeRequest("9.9.9.9"), conn=None)
    assert out["access_token"]


# --- 2. token lifetime ----------------------------------------------------


def test_default_token_lifetime_is_twenty_four_hours(monkeypatch):
    import auth_utils
    import jwt

    token = auth_utils.create_access_token("u1", "root")
    claims = jwt.decode(token, "test-signing-secret", algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 24 * 3600


def test_env_still_overrides_the_lifetime(monkeypatch):
    import auth_utils
    import jwt

    monkeypatch.setenv("JWT_EXPIRE_HOURS", "72")
    token = auth_utils.create_access_token("u1", "root")
    claims = jwt.decode(token, "test-signing-secret", algorithms=["HS256"])
    assert claims["exp"] - claims["iat"] == 72 * 3600


# --- 3. token_version revocation -----------------------------------------


def test_the_token_carries_the_users_token_version():
    import auth_utils
    import jwt

    token = auth_utils.create_access_token("u1", "root", token_version=4)
    claims = jwt.decode(token, "test-signing-secret", algorithms=["HS256"])
    assert claims["token_version"] == 4


def test_a_password_change_bumps_the_users_token_version(store, monkeypatch):
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    doc = auth_utils.create_user(None, "root", "hunter2-long-enough")
    assert auth_utils.get_user_by_id(None, doc["id"]).get("token_version", 0) == 0

    auth_utils.update_user(None, doc["id"], password="a-new-password")
    assert auth_utils.get_user_by_id(None, doc["id"])["token_version"] == 1

    auth_utils.update_user(None, doc["id"], password="another-password")
    assert auth_utils.get_user_by_id(None, doc["id"])["token_version"] == 2


def test_an_email_change_does_not_bump_the_token_version(store, monkeypatch):
    import auth_utils

    monkeypatch.setattr(auth_utils, "store", store)
    monkeypatch.setattr(auth_utils, "ensure_users_table", lambda conn=None: None)
    doc = auth_utils.create_user(None, "root", "hunter2-long-enough")
    auth_utils.update_user(None, doc["id"], email="root@example.com")
    assert auth_utils.get_user_by_id(None, doc["id"]).get("token_version", 0) == 0


def test_a_stale_token_version_is_rejected_by_get_current_user(monkeypatch):
    from api import main
    import auth_utils

    token = auth_utils.create_access_token("u1", "root", token_version=1)
    monkeypatch.setattr(
        main, "get_user_by_id",
        lambda conn, uid: {"id": "u1", "username": "root",
                           "token_version": 2},
    )

    class _Creds:
        credentials = token

    with pytest.raises(HTTPException) as ei:
        main.get_current_user(response=_Response(), credentials=_Creds(), conn=None)
    assert ei.value.status_code == 401


class _Response:
    def __init__(self):
        self.headers = {}


def test_a_current_token_version_is_accepted(monkeypatch):
    from api import main
    import auth_utils

    token = auth_utils.create_access_token("u1", "root", token_version=2)
    monkeypatch.setattr(
        main, "get_user_by_id",
        lambda conn, uid: {"id": "u1", "username": "root",
                           "token_version": 2},
    )

    class _Creds:
        credentials = token

    got = main.get_current_user(response=_Response(), credentials=_Creds(), conn=None)
    assert got["id"] == "u1" and got["username"] == "root"


def test_tokens_and_rows_from_before_this_change_still_match(monkeypatch):
    """No token_version claim and no token_version field both read as 0, so
    existing sessions survive the deploy."""
    from api import main
    import auth_utils
    import jwt

    legacy = jwt.encode(
        {"sub": "u1", "username": "root",
         "iat": datetime.utcnow(),
         "exp": datetime.utcnow() + timedelta(hours=1)},
        "test-signing-secret", algorithm="HS256",
    )
    monkeypatch.setattr(
        main, "get_user_by_id",
        lambda conn, uid: {"id": "u1", "username": "root"},
    )

    class _Creds:
        credentials = legacy

    assert main.get_current_user(
        response=_Response(), credentials=_Creds(), conn=None)["id"] == "u1"


def test_the_renewed_token_keeps_the_token_version():
    import auth_utils
    import jwt

    old = auth_utils.create_access_token("u1", "root", token_version=3)
    payload = jwt.decode(old, "test-signing-secret", algorithms=["HS256"])
    # Past half-life: 20 of the token's 24 hours have burned.
    past_half_life = datetime.utcfromtimestamp(payload["iat"] + 20 * 3600)
    renewed = auth_utils.renewed_token_if_stale(payload, now=past_half_life)
    assert renewed is not None
    assert jwt.decode(renewed, "test-signing-secret",
                      algorithms=["HS256"])["token_version"] == 3


def test_login_mints_a_token_carrying_the_rows_token_version(monkeypatch):
    from api import main
    import jwt

    user = {"id": "u1", "username": "root",
            "password_hash": "h", "token_version": 7}
    monkeypatch.setattr(main, "get_user_by_username", lambda conn, u: user)
    monkeypatch.setattr(main, "verify_password", lambda p, h: True)
    out = main.api_login(body=main.LoginBody(username="root", password="x"),
                         request=_FakeRequest(), conn=None)
    claims = jwt.decode(out["access_token"], "test-signing-secret",
                        algorithms=["HS256"])
    assert claims["token_version"] == 7
