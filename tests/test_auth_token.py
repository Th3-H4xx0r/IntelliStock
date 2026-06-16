"""Tests for JWT lifetime + sliding-renewal helpers in backend/auth_utils.py."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

pytest.importorskip("jwt")  # PyJWT

import auth_utils  # noqa: E402


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-key")
    monkeypatch.delenv("JWT_EXPIRE_HOURS", raising=False)


def _decode(token: str) -> dict:
    import jwt
    return jwt.decode(token, "test-secret-key", algorithms=["HS256"])


def test_default_lifetime_is_30_days_and_has_iat():
    token = auth_utils.create_access_token("u1", "alice", "user")
    payload = _decode(token)
    assert "iat" in payload and "exp" in payload
    lifetime_hours = (payload["exp"] - payload["iat"]) / 3600
    assert 719 <= lifetime_hours <= 721  # ~720h / 30 days


def test_lifetime_honors_env_override(monkeypatch):
    monkeypatch.setenv("JWT_EXPIRE_HOURS", "48")
    payload = _decode(auth_utils.create_access_token("u1", "alice", "user"))
    assert 47 <= (payload["exp"] - payload["iat"]) / 3600 <= 49


def test_needs_refresh_true_past_halflife():
    now = datetime(2026, 6, 16, 12, 0, 0)
    iat = now - timedelta(days=20)          # 20 of 30 days elapsed -> past half-life
    exp = iat + timedelta(days=30)
    payload = {"sub": "u1", "username": "alice", "role": "user",
               "iat": int(iat.timestamp()), "exp": int(exp.timestamp())}
    assert auth_utils.token_needs_refresh(payload, now=now) is True


def test_needs_refresh_false_when_fresh():
    now = datetime(2026, 6, 16, 12, 0, 0)
    iat = now - timedelta(days=2)           # only 2 of 30 days elapsed
    exp = iat + timedelta(days=30)
    payload = {"iat": int(iat.timestamp()), "exp": int(exp.timestamp())}
    assert auth_utils.token_needs_refresh(payload, now=now) is False


def test_needs_refresh_false_without_iat():
    # Legacy 24h tokens have no iat -> never slide.
    assert auth_utils.token_needs_refresh({"exp": 9999999999}) is False


def test_renewed_token_if_stale_mints_when_past_halflife():
    now = datetime(2026, 6, 16, 12, 0, 0)
    iat = now - timedelta(days=20)
    exp = iat + timedelta(days=30)
    payload = {"sub": "u1", "username": "alice", "role": "admin",
               "iat": int(iat.timestamp()), "exp": int(exp.timestamp())}
    fresh = auth_utils.renewed_token_if_stale(payload, now=now)
    assert fresh is not None
    new_payload = _decode(fresh)
    assert new_payload["sub"] == "u1" and new_payload["role"] == "admin"


def test_renewed_token_if_stale_returns_none_when_fresh():
    now = datetime(2026, 6, 16, 12, 0, 0)
    iat = now - timedelta(days=1)
    exp = iat + timedelta(days=30)
    payload = {"sub": "u1", "username": "alice",
               "iat": int(iat.timestamp()), "exp": int(exp.timestamp())}
    assert auth_utils.renewed_token_if_stale(payload, now=now) is None
