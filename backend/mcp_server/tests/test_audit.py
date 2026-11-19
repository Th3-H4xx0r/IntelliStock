"""The audit sink: auth events recorded locally, filtered, and delivered to the API."""
import asyncio
import inspect
import json
import logging
import sqlite3

import httpx
import pytest

from fake_api import AUDIT_EVENT_PATH, BASE_URL, SERVICE_TOKEN, FakeApi
from mcp_server.api_client import ApiClient
from mcp_server.audit import (
    ALERT_EVENTS,
    AUTH_EVENTS,
    FORBIDDEN_DETAIL_SUBSTRINGS,
    LOCAL_ONLY_EVENTS,
    AuditSink,
)
from mcp_server.authdb import AuthDB

DAY_S = 86_400
REUSE_DETAIL = {"session_id": "fam-1", "user_id": "u-123", "username": "operator", "client_id": "jarvis"}


@pytest.fixture
def db(tmp_path):
    store = AuthDB(str(tmp_path / "mcp_auth.sqlite3"))
    yield store
    store.close()


@pytest.fixture
def fake():
    return FakeApi()


@pytest.fixture
async def api(fake):
    client = fake.client()
    yield client
    await client.aclose()


@pytest.fixture
def sink(db, api, clock):
    return AuditSink(db, api, clock=clock)


def _rows(db):
    return [dict(row) for row in db.query("SELECT * FROM auth_events ORDER BY id")]


def _warning_lines(caplog):
    return [record.getMessage() for record in caplog.records
            if record.name == "mcp_server.audit" and record.levelno == logging.WARNING]


def _reject_poison(fake, status):
    """Answer status to every event whose detail username is "poison"; accept the rest as the real API does."""
    def handler(request):
        body = json.loads(request.content)
        if body["detail"].get("username") == "poison":
            return httpx.Response(status, json={"detail": "rejected"})
        fake.audit_events.append(body)
        return httpx.Response(204)

    fake.set_route("POST", AUDIT_EVENT_PATH, 0, handler)


def test_event_sets_match_the_contract():
    assert AUTH_EVENTS == {"login_ok", "login_failed", "passkey_failed", "totp_failed", "lockout",
                           "refresh_reuse_revoked", "session_revoked", "enrolled", "disabled", "enabled"}
    assert LOCAL_ONLY_EVENTS == {"signing_key_rotated"}
    assert ALERT_EVENTS == {"refresh_reuse_revoked", "lockout", "disabled"}
    assert ALERT_EVENTS <= AUTH_EVENTS
    assert not LOCAL_ONLY_EVENTS & AUTH_EVENTS
    assert FORBIDDEN_DETAIL_SUBSTRINGS == ("password", "token", "secret", "code", "credential",
                                           "assertion", "cookie", "otp")


def test_unknown_event_rejected(sink, db):
    for bad in ("login_succeeded", "", "LOGIN_OK", "signing_key_rotated "):
        with pytest.raises(ValueError):
            sink.record(bad, username="op")
    assert _rows(db) == []


def test_forbidden_detail_keys_dropped(sink, db, clock):
    sink.record("login_failed", username="op", password="x", totp_code="1")

    (row,) = _rows(db)
    assert row["event"] == "login_failed"
    assert json.loads(row["detail"]) == {"username": "op"}
    assert row["at"] == clock()
    assert (row["delivered_at"], row["attempts"]) == (None, 0)


def test_forbidden_substrings_matched_in_any_case_and_nested(sink, db):
    sink.record("login_failed", username="op", reason="bad", Refresh_Token="r", clientSecret="s",
                AuthCode="c", credential_id="i", ASSERTION="a", cookie_hash="h", OTP="o",
                extra={"method": "totp", "TOTP_value": "123456", "items": [{"api_token": "t", "n": 1}]})

    (row,) = _rows(db)
    assert json.loads(row["detail"]) == {"username": "op", "reason": "bad",
                                         "extra": {"method": "totp", "items": [{"n": 1}]}}
    for secret in ('"r"', '"s"', '"c"', '"i"', '"a"', '"h"', '"o"', "123456", '"t"'):
        assert secret not in row["detail"]


def test_long_strings_truncated_to_256_chars(sink, db, caplog):
    # The login form's username reaches login_failed as typed.
    sink.record("login_failed", username="x" * 5000, reason="password", kept="z" * 256,
                extra={"notes": ["y" * 300, "short"], "count": 3})
    sink.record("lockout", scope="user", username="q" * 5000, seconds=60)

    first, second = (json.loads(row["detail"]) for row in _rows(db))
    assert first == {"username": "x" * 255 + "\u2026", "reason": "password", "kept": "z" * 256,
                     "extra": {"notes": ["y" * 255 + "\u2026", "short"], "count": 3}}
    assert len(first["username"]) == 256
    assert second["username"] == "q" * 255 + "\u2026"
    # The alert log line carries the bounded detail too.
    assert "q" * 255 in caplog.text and "q" * 256 not in caplog.text


def test_oversized_detail_replaced_by_marker(sink, db, caplog):
    near_cap = {f"k{i:02d}": "v" * 200 for i in range(35)}  # about 7.6 KB serialized
    sink.record("login_failed", username="op", **near_cap)
    sink.record("lockout", scope="user", username="op", seconds=60,
                **{f"k{i:02d}": "v" * 200 for i in range(60)})  # about 13 KB serialized

    kept, cut = _rows(db)
    assert json.loads(kept["detail"]) == {"username": "op", **near_cap}
    assert len(kept["detail"].encode("utf-8")) <= 8192
    marker = json.loads(cut["detail"])
    assert set(marker) == {"truncated", "detail_bytes"}
    assert marker["truncated"] is True and marker["detail_bytes"] > 8192
    assert _warning_lines(caplog) == ["mcp auth alert: lockout " + cut["detail"]]


async def test_flush_delivers_and_marks(sink, db, fake, clock):
    sink.record("login_ok", username="operator", method="passkey", tailnet_user="op@example.com")
    clock.advance(3)

    assert await sink.flush() == 1

    assert fake.audit_events == [{"kind": "auth", "event": "login_ok",
                                  "detail": {"username": "operator", "method": "passkey",
                                             "tailnet_user": "op@example.com"}}]
    (row,) = _rows(db)
    assert (row["delivered_at"], row["attempts"]) == (clock(), 1)
    # A delivered row is never sent again.
    assert await sink.flush() == 0
    assert len(fake.requests) == 1


def _failing_503(request):
    return httpx.Response(503, json={"detail": "unavailable"})


def _failing_connect(request):
    raise httpx.ConnectError("connection refused", request=request)


def _failing_with(status):
    def answer(request):
        return httpx.Response(status, json={"detail": "not now"})
    return answer


# 401 (MCP disabled or a token mismatch), 403, 404/405 (route not deployed yet), 408 and 429
# fail every row alike: they are retried, never treated as a rejection of the row.
@pytest.mark.parametrize(
    "failure",
    [_failing_503, _failing_connect] + [_failing_with(status) for status in (401, 403, 404, 405, 408, 429)],
    ids=["503", "connect-error", "401", "403", "404", "405", "408", "429"],
)
async def test_flush_retries_after_api_failure(sink, db, fake, clock, failure):
    delivered = []

    def flaky(request):
        if len(fake.requests) == 1:
            return failure(request)
        delivered.append(json.loads(request.content))
        return httpx.Response(204)

    fake.set_route("POST", AUDIT_EVENT_PATH, 0, flaky)
    sink.record("session_revoked", session_id="fam-9", source="admin_cli")

    assert await sink.flush() == 0
    (row,) = _rows(db)
    assert (row["delivered_at"], row["dead_at"], row["attempts"]) == (None, None, 1)

    clock.advance(5)
    assert await sink.flush() == 1
    assert delivered == [{"kind": "auth", "event": "session_revoked",
                          "detail": {"session_id": "fam-9", "source": "admin_cli"}}]
    (row,) = _rows(db)
    assert (row["delivered_at"], row["attempts"]) == (clock(), 2)


async def test_flush_stops_at_first_failure(sink, db, fake):
    fake.set_route("POST", AUDIT_EVENT_PATH, 503)
    sink.record("login_ok", username="operator")
    sink.record("login_failed", username="operator", reason="password")

    assert await sink.flush() == 0

    # One failed request per flush, not one per pending row: a down or hung API
    # costs a flush (or the admin CLI) a single timeout.
    assert len(fake.requests) == 1
    assert [(row["delivered_at"], row["attempts"]) for row in _rows(db)] == [(None, 1), (None, 0)]


@pytest.mark.parametrize("status", [400, 413, 422])
async def test_rejected_row_does_not_block_later_rows(sink, db, fake, clock, caplog, status):
    _reject_poison(fake, status)
    sink.record("login_failed", username="poison", reason="password")
    for n in range(3):
        sink.record("login_ok", username=f"op{n}")

    assert [await sink.flush() for _ in range(5)] == [3, 0, 0, 0, 0]

    assert [event["detail"]["username"] for event in fake.audit_events] == ["op0", "op1", "op2"]
    assert len(fake.requests) == 4  # the rejected row is tried once and never again
    dead, *sent = _rows(db)
    assert (dead["dead_at"], dead["delivered_at"], dead["attempts"]) == (clock(), None, 1)
    assert [(row["delivered_at"], row["dead_at"]) for row in sent] == [(clock(), None)] * 3
    assert _warning_lines(caplog) == [
        f"audit event {dead['id']} (login_failed) rejected by the API with HTTP {status}; it will not be retried"]
    assert "poison" not in caplog.text


async def test_alerts_after_a_rejected_alert_still_go_out(sink, db, fake):
    _reject_poison(fake, 422)
    sink.record("refresh_reuse_revoked", session_id="fam-1", user_id="u-9", username="poison", client_id="jarvis")
    sink.record("login_ok", username="operator")
    sink.record("lockout", scope="user", username="operator", seconds=900)

    assert await sink.flush() == 2

    assert [event["event"] for event in fake.audit_events] == ["lockout", "login_ok"]
    assert [row["dead_at"] is not None for row in _rows(db)] == [True, False, False]


async def test_local_only_event_not_sent(sink, db, fake):
    sink.record("signing_key_rotated", kid="k-2")
    sink.record("enabled", source="admin_cli")

    assert await sink.flush() == 1
    assert await sink.flush() == 0

    assert [event["event"] for event in fake.audit_events] == ["enabled"]
    assert len(fake.requests) == 1
    local, sent = _rows(db)
    assert local["event"] == "signing_key_rotated"
    assert json.loads(local["detail"]) == {"kid": "k-2"}
    assert (local["delivered_at"], local["attempts"]) == (None, 0)
    assert sent["delivered_at"] is not None


async def test_alert_events_logged_and_sent_first(sink, db, fake, clock, caplog):
    caplog.set_level(logging.INFO, logger="mcp_server.audit")
    sink.record("login_ok", username="operator", method="totp")
    clock.advance(1)
    sink.record("refresh_reuse_revoked", **REUSE_DETAIL)
    clock.advance(1)
    sink.record("lockout", scope="global", username=None, seconds=900, password="hunter2")

    lines = _warning_lines(caplog)
    assert len(lines) == 2
    prefix = "mcp auth alert: refresh_reuse_revoked "
    assert lines[0].startswith(prefix)
    assert json.loads(lines[0][len(prefix):]) == REUSE_DETAIL
    prefix = "mcp auth alert: lockout "
    assert lines[1].startswith(prefix)
    assert json.loads(lines[1][len(prefix):]) == {"scope": "global", "username": None, "seconds": 900}
    assert "hunter2" not in caplog.text and "password" not in caplog.text

    # Alerts go before the older login_ok, oldest alert first.
    assert await sink.flush(limit=1) == 1
    assert [event["event"] for event in fake.audit_events] == ["refresh_reuse_revoked"]
    assert await sink.flush() == 2
    assert [event["event"] for event in fake.audit_events] == ["refresh_reuse_revoked", "lockout", "login_ok"]
    assert fake.audit_events[0]["detail"] == REUSE_DETAIL


def test_record_does_no_network_io(db, clock):
    calls = []

    def boom(request):
        calls.append(request)
        raise AssertionError("record() must not touch the network")

    api = ApiClient(BASE_URL, SERVICE_TOKEN, transport=httpx.MockTransport(boom))
    sink = AuditSink(db, api, clock=clock)

    assert not inspect.iscoroutinefunction(AuditSink.record)
    for event in sorted(AUTH_EVENTS | LOCAL_ONLY_EVENTS):
        assert sink.record(event, username="operator") is None

    assert calls == []
    assert len(_rows(db)) == len(AUTH_EVENTS) + len(LOCAL_ONLY_EVENTS)


async def _run_flush_loop_until(sink, done):
    """Run flush_loop at a 10 ms interval until done() is true (5 s at most), then cancel it."""
    task = asyncio.create_task(sink.flush_loop(interval_s=0.01))
    try:
        for _ in range(500):
            if done():
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_flush_loop_delivers_and_survives_errors(sink, db, fake, caplog):
    attempts = []

    def explode_once(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise RuntimeError("unexpected failure carrying " + SERVICE_TOKEN)
        fake.audit_events.append(json.loads(request.content))
        return httpx.Response(204)

    fake.set_route("POST", AUDIT_EVENT_PATH, 0, explode_once)
    sink.record("disabled", source="admin_cli")

    await _run_flush_loop_until(sink, lambda: _rows(db)[0]["delivered_at"] is not None)

    assert [event["event"] for event in fake.audit_events] == ["disabled"]
    assert len(attempts) == 2
    (row,) = _rows(db)
    assert row["attempts"] == 2 and row["delivered_at"] is not None
    assert "RuntimeError" in caplog.text
    assert SERVICE_TOKEN not in caplog.text


async def test_flush_loop_outlives_a_failing_flush(sink, fake, caplog, monkeypatch):
    real_flush = sink.flush
    calls = []

    async def store_fails_once(limit=100):
        calls.append(limit)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked by /data/mcp_auth.sqlite3")
        return await real_flush(limit)

    monkeypatch.setattr(sink, "flush", store_fails_once)
    sink.record("enrolled", username="operator")

    await _run_flush_loop_until(sink, lambda: bool(fake.audit_events))

    assert [event["event"] for event in fake.audit_events] == ["enrolled"]
    assert len(calls) >= 2
    assert "audit flush failed (OperationalError)" in caplog.text
    assert "database is locked" not in caplog.text


async def test_purge_drops_delivered_and_rejected_rows_older_than_90_days(sink, db, clock):
    sink.record("login_ok", username="old-delivered")
    sink.record("login_failed", username="old-undelivered")
    sink.record("signing_key_rotated", kid="old-local")
    sink.record("login_failed", username="old-rejected")
    db.query("UPDATE auth_events SET delivered_at = at WHERE id = 1")
    db.query("UPDATE auth_events SET dead_at = at WHERE id = 4")
    clock.advance(DAY_S)
    sink.record("enrolled", username="recent-delivered")
    db.query("UPDATE auth_events SET delivered_at = at WHERE id = 5")

    clock.advance(90 * DAY_S - DAY_S)  # the first four rows are exactly 90 days old
    sink.purge()
    assert len(_rows(db)) == 5

    clock.advance(1)
    sink.purge()
    assert [json.loads(row["detail"]) for row in _rows(db)] == [
        {"username": "old-undelivered"}, {"kid": "old-local"}, {"username": "recent-delivered"}]
