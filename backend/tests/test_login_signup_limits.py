"""Per-IP login limit (only once the proxy is named) and a signup limiter.

The login limiter keyed on (username, client host) alone, so one address
could try every username ten times each. POST /auth/signup, which checks
SECRET_AUTH_KEY, took unlimited guesses. Behind Traefik with no
FORWARDED_ALLOW_IPS every client arrives from Traefik's one address, so the
per-IP count is skipped for a non-loopback peer there: it would be a single
global count that locks everyone out, the operator included.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi import HTTPException

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import auth_utils  # noqa: E402
from api import main  # noqa: E402

T0 = 1_000_000.0

# api.main loads .env at import, so a developer's limits would leak in.
_LIMIT_ENV = (
    "LOGIN_RATE_LIMIT_MAX_ATTEMPTS",
    "LOGIN_RATE_LIMIT_WINDOW_SECONDS",
    "LOGIN_RATE_LIMIT_PER_IP_MAX_ATTEMPTS",
    "SIGNUP_RATE_LIMIT_MAX_ATTEMPTS",
    "SIGNUP_RATE_LIMIT_WINDOW_SECONDS",
)


@pytest.fixture(autouse=True)
def _clean_limiter():
    auth_utils.reset_login_rate_limit()
    yield
    auth_utils.reset_login_rate_limit()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-signing-secret")
    monkeypatch.delenv("JWT_EXPIRE_HOURS", raising=False)
    for name in _LIMIT_ENV:
        monkeypatch.delenv(name, raising=False)


class _FakeClient:
    def __init__(self, host):
        self.host = host


class _FakeRequest:
    def __init__(self, host="1.2.3.4"):
        self.client = _FakeClient(host)


# --- auth_utils -------------------------------------------------------------


def test_per_ip_default_is_thirty(monkeypatch):
    monkeypatch.delenv("LOGIN_RATE_LIMIT_PER_IP_MAX_ATTEMPTS", raising=False)
    assert auth_utils.login_ip_rate_limit_max() == 30


def test_thirty_failures_across_usernames_from_one_ip_are_limited():
    for i in range(30):
        auth_utils.record_failed_login(f"user{i}", "1.2.3.4", now=T0 + i)
    assert auth_utils.login_is_rate_limited("someone-else", "1.2.3.4", now=T0 + 30)
    assert not auth_utils.login_is_rate_limited("someone-else", "5.6.7.8", now=T0 + 30)
    assert not auth_utils.login_is_rate_limited("someone-else", "1.2.3.4", now=T0 + 29 + 901)


def test_a_success_clears_only_the_user_bucket():
    for i in range(29):
        auth_utils.record_failed_login(f"user{i}", "1.2.3.4", now=T0 + i)
    auth_utils.clear_login_attempts("user0", "1.2.3.4")
    auth_utils.record_failed_login("user0", "1.2.3.4", now=T0 + 29)
    assert auth_utils.login_is_rate_limited("root", "1.2.3.4", now=T0 + 30)


def test_the_mcp_source_key_is_one_shared_bucket():          # contract §4: (username, "mcp")
    for i in range(30):
        auth_utils.record_failed_login(f"u{i}", "mcp", now=T0 + i)
    assert auth_utils.login_is_rate_limited("anyone", "mcp", now=T0 + 30)


def test_signup_defaults(monkeypatch):
    monkeypatch.delenv("SIGNUP_RATE_LIMIT_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("SIGNUP_RATE_LIMIT_WINDOW_SECONDS", raising=False)
    assert auth_utils.signup_rate_limit_config() == (5, 3600)


def test_signup_attempts_age_out_of_the_hour():
    for i in range(5):
        assert not auth_utils.signup_is_rate_limited("1.2.3.4", now=T0 + i)
        assert auth_utils.record_signup_attempt("1.2.3.4", now=T0 + i) == i + 1
    assert auth_utils.signup_is_rate_limited("1.2.3.4", now=T0 + 5)
    assert not auth_utils.signup_is_rate_limited("5.6.7.8", now=T0 + 5)
    assert not auth_utils.signup_is_rate_limited("1.2.3.4", now=T0 + 4 + 3601)


def test_per_ip_false_neither_checks_nor_records_the_host_bucket():
    for i in range(30):
        auth_utils.record_failed_login(f"user{i}", "1.2.3.4", now=T0 + i, per_ip=False)
    assert not auth_utils.login_is_rate_limited("someone-else", "1.2.3.4", now=T0 + 30)


def test_reset_clears_all_three_buckets():
    for i in range(30):
        auth_utils.record_failed_login("root" if i < 10 else f"user{i}", "1.2.3.4", now=T0 + i)
    for i in range(5):
        auth_utils.record_signup_attempt("1.2.3.4", now=T0 + i)
    assert auth_utils.login_is_rate_limited("root", "1.2.3.4", now=T0 + 30, per_ip=False)
    assert auth_utils.login_is_rate_limited("someone-else", "1.2.3.4", now=T0 + 30)
    assert auth_utils.signup_is_rate_limited("1.2.3.4", now=T0 + 30)
    auth_utils.reset_login_rate_limit()
    assert not auth_utils.login_is_rate_limited("root", "1.2.3.4", now=T0 + 30)
    assert not auth_utils.login_is_rate_limited("someone-else", "1.2.3.4", now=T0 + 30)
    assert not auth_utils.signup_is_rate_limited("1.2.3.4", now=T0 + 30)


# --- main: the client address ---------------------------------------------


class _NoClient:
    client = None


class _BrokenClient:
    @property
    def client(self):
        raise RuntimeError("no transport")


def test_client_host_is_unknown_without_a_usable_request():
    assert main._client_host(_FakeRequest("203.0.113.5")) == "203.0.113.5"
    assert main._client_host(None) == "unknown"
    assert main._client_host(_NoClient()) == "unknown"
    assert main._client_host(_BrokenClient()) == "unknown"


# --- main: the routes -------------------------------------------------------


@pytest.fixture
def root_login(monkeypatch):
    user = {"id": "u1", "username": "root", "password_hash": "h"}
    monkeypatch.setattr(main, "get_user_by_username", lambda conn, u: user if u == "root" else None)
    monkeypatch.setattr(main, "verify_password", lambda p, h: p == "right")
    monkeypatch.setattr(main, "count_users", lambda conn: 1)

    def login(username, password, host):
        return main.api_login(body=main.LoginBody(username=username, password=password),
                              request=_FakeRequest(host), conn=None)
    return login


def _spray(login, host, n=30):
    for i in range(n):
        with pytest.raises(HTTPException):
            login(f"user{i}", "x", host)


def test_without_a_trusted_proxy_the_per_ip_limit_is_off_for_the_proxy_address(root_login, monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)       # mode 1: not configured yet
    _spray(root_login, "10.0.1.2")                                  # Traefik's address, shared by everyone
    assert root_login("root", "right", "10.0.1.2")["access_token"]  # no global lockout
    for _ in range(10):
        with pytest.raises(HTTPException):
            root_login("root", "wrong", "10.0.1.2")
    with pytest.raises(HTTPException) as e:                         # the per-(username, ip) limit still holds
        root_login("root", "right", "10.0.1.2")
    assert e.value.status_code == 429


def test_without_a_trusted_proxy_a_loopback_peer_still_gets_the_per_ip_limit(root_login, monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    _spray(root_login, "127.0.0.1")
    with pytest.raises(HTTPException) as e:
        root_login("root", "right", "127.0.0.1")
    assert e.value.status_code == 429


def test_with_a_trusted_proxy_the_per_ip_limit_keys_on_the_real_client(root_login, monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8")         # mode 2: uvicorn now hands the route the real client
    _spray(root_login, "203.0.113.5")
    with pytest.raises(HTTPException) as e:
        root_login("root", "right", "203.0.113.5")
    assert e.value.status_code == 429
    assert root_login("root", "right", "198.51.100.7")["access_token"]


def test_signup_route_limits_attempts_per_ip(monkeypatch):
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)      # the signup limit applies in both modes
    monkeypatch.setenv("SECRET_AUTH_KEY", "right-key")
    monkeypatch.setattr(main, "create_user", lambda conn, u, p: {"id": "n1", "username": u})
    body = lambda key: main.SignupBody(username="new", password="long-enough", secret_auth_key=key)  # noqa: E731
    for _ in range(5):
        with pytest.raises(HTTPException) as e:
            main.api_signup(body=body("wrong"), request=_FakeRequest("1.2.3.4"), conn=None)
        assert e.value.status_code == 403
    with pytest.raises(HTTPException) as e:
        main.api_signup(body=body("right-key"), request=_FakeRequest("1.2.3.4"), conn=None)
    assert e.value.status_code == 429 and e.value.detail == "Too many signup attempts. Try again later."
    assert main.api_signup(body=body("right-key"), request=_FakeRequest("9.9.9.9"), conn=None)["access_token"]
