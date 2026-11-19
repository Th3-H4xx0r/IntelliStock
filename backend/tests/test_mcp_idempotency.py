"""Idempotency keys for MCP writes (plan Task 14, contract §2/§3).

Every non-read call on the MCP listener carries an Idempotency-Key, scoped to
the calling client. The first call with a key claims it (one McpIdempotency
row, "in_progress") and stores its completed response, whatever the status,
including the 202 of a held approval. A repeat with the identical request
replays that response with X-MCP-Idempotent-Replay: true and runs nothing; the
same key on a different request, or while the first is still running, is a
409. A route that raises releases the key, so a retry can run. A record lives
24 h, and a response past 1 MiB replays as its status with a fixed detail.
"""
from __future__ import annotations

import hashlib
import threading
from datetime import timedelta
from unittest.mock import Mock

import anyio
import pytest
from fastapi import Body

import apns_sender
import mcp_approvals
import mcp_idempotency
import notifications
from api.mcp_gate import _ResponseCapture
from mcp_helpers import (
    IDEM, make_probe_app, mcp_client, mcp_env, seed_money_fixture, seed_operator, use_store,
)
from mcp_idempotency import (
    IDEMPOTENCY_TABLE, KEY_RE, RESPONSE_CAP_BYTES, TTL_S, IdemLookup, claim, finalize, lookup, record_id,
    release,
)
from mcp_policy import canonical_request_sha256, iso_utc, mcp_policy, utc_now

REQUIRED = {"detail": "mcp: Idempotency-Key required"}
REUSED = {"detail": "mcp: Idempotency-Key reused with a different request"}
IN_PROGRESS = {"detail": "mcp: Idempotency-Key request still in progress"}
TOO_LARGE = {"detail": "mcp: original response too large to replay"}
REPLAY = "X-MCP-Idempotent-Replay"
LIVE_ACT = "/probe/instances/alpaca-main/act"
WRITE_SHA = canonical_request_sha256("POST", "/probe/write", {}, {"n": 1})


@pytest.fixture(autouse=True)
def _mcp(monkeypatch, store):
    use_store(monkeypatch, store)
    seed_operator(store)
    seed_money_fixture(store)
    mcp_env(monkeypatch)
    monkeypatch.setattr(apns_sender, "send_to_user", Mock())              # never reach APNs
    monkeypatch.setattr(notifications, "notify", Mock())                  # never reach Discord
    monkeypatch.setattr(mcp_approvals, "notify_approval_async", Mock())   # no push thread


@pytest.fixture
def probe():
    app, calls = make_probe_app()
    return app, mcp_client(app), calls


def _rows(store) -> list[dict]:
    return store.run(IDEMPOTENCY_TABLE)


def _outcomes(store) -> list[tuple]:
    return [(r["outcome"], r["status"]) for r in store.run("McpAudit")]


# -- the key is required on every non-read call -----------------------------------

@pytest.mark.parametrize("headers", [
    {},
    {"Idempotency-Key": "k" * 129},
    {"Idempotency-Key": "has space"},
    {"Idempotency-Key": ""},
    [("Idempotency-Key", "k-a"), ("Idempotency-Key", "k-b")],          # duplicated
], ids=["missing", "129_chars", "space", "empty", "duplicated"])
def test_non_read_without_key_is_428(probe, store, headers):
    _, c, calls = probe
    res = c.post("/probe/write", json={"n": 1}, headers=headers)
    assert (res.status_code, res.json()) == (428, REQUIRED)
    assert calls["count"].get("probe_write", 0) == 0 and _rows(store) == []
    assert _outcomes(store) == [("idempotency_required", 428)]


@pytest.mark.parametrize("method,path,operation_id", [
    ("POST", "/probe/instances/paper-1/act", "probe_act"),       # money on paper: would run
    ("POST", LIVE_ACT, "probe_act"),                             # money on live: would be held
    ("DELETE", "/probe/things/1", "probe_delete"),               # irreversible: would be held
])
def test_every_non_read_class_needs_the_key(probe, store, method, path, operation_id):
    _, c, calls = probe
    res = c.request(method, path, json={"qty": 1} if method == "POST" else None)
    assert (res.status_code, res.json()) == (428, REQUIRED)
    assert calls["count"].get(operation_id, 0) == 0 and store.run("McpApprovals") == []


def test_a_128_char_key_of_every_allowed_character_runs(probe):
    _, c, calls = probe
    key = ("Az09._:-" * 16)[:128]
    assert KEY_RE.fullmatch(key) and not KEY_RE.fullmatch(key + "\n")
    assert c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": key}).status_code == 200
    assert calls["count"]["probe_write"] == 1


def test_read_without_key_runs(probe, store):
    _, c, calls = probe
    assert c.get("/probe/read").status_code == 200 and c.get("/probe/read").status_code == 200
    assert calls["count"]["probe_read"] == 2 and _rows(store) == []


# -- replay -------------------------------------------------------------------------

def test_replay_returns_stored_response(probe, store):
    _, c, calls = probe
    a = c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-1"})
    b = c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-1"})
    assert (b.status_code, b.json()) == (a.status_code, a.json()) and b.headers[REPLAY] == "true"
    assert b.headers["content-type"] == a.headers["content-type"] == "application/json"
    assert REPLAY not in a.headers
    assert calls["count"]["probe_write"] == 1
    (row,) = _rows(store)
    assert row["id"] == record_id("jarvis", "k-1")
    assert (row["state"], row["status"], row["client"], row["key"], row["method"], row["path"]) == \
        ("done", 200, "jarvis", "k-1", "POST", "/probe/write")
    assert row["request_sha256"] == WRITE_SHA and row["body_text"] == '{"n":1}' and row["too_large"] is False
    assert _outcomes(store) == [("ok", 200), ("idempotent_replay", 200)]
    assert [r["idempotency_key"] for r in store.run("McpAudit")] == ["k-1", "k-1"]


def test_an_error_response_is_stored_and_replayed(probe):
    _, c, calls = probe
    a = c.post("/probe/refuse", json={}, headers={"Idempotency-Key": "k-400"})
    b = c.post("/probe/refuse", json={}, headers={"Idempotency-Key": "k-400"})
    assert (a.status_code, a.json()) == (b.status_code, b.json()) == (400, {"detail": "nope"})
    assert b.headers[REPLAY] == "true" and calls["count"]["probe_refuse"] == 1


def test_same_key_different_body_is_409(probe, store):
    _, c, calls = probe
    c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-1"})
    res = c.post("/probe/write", json={"n": 2}, headers={"Idempotency-Key": "k-1"})
    assert (res.status_code, res.json()) == (409, REUSED) and REPLAY not in res.headers
    assert calls["count"]["probe_write"] == 1 and calls["body"] == {"n": 1}
    assert _outcomes(store) == [("ok", 200), ("idempotency_conflict", 409)]


def test_same_key_different_path_is_409(probe):
    _, c, calls = probe
    c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-1"})
    res = c.post("/probe/refuse", json={"n": 1}, headers={"Idempotency-Key": "k-1"})
    assert (res.status_code, res.json()) == (409, REUSED)
    assert calls["count"].get("probe_refuse", 0) == 0


def test_same_key_different_query_is_409(probe):
    _, c, calls = probe
    c.delete("/probe/things/1", params={"force": "true"}, headers={"Idempotency-Key": "k-del"})
    res = c.delete("/probe/things/1", params={"force": "false"}, headers={"Idempotency-Key": "k-del"})
    assert (res.status_code, res.json()) == (409, REUSED)


def test_concurrent_duplicate_key_is_409_and_runs_once(probe, store):
    _, c, calls = probe
    assert claim("jarvis", "k-2", method="POST", path="/probe/write", request_sha256=WRITE_SHA) is True
    res = c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-2"})
    assert (res.status_code, res.json()) == (409, IN_PROGRESS)
    assert calls["count"].get("probe_write", 0) == 0
    (row,) = _rows(store)
    assert row["state"] == "in_progress"            # the loser leaves the winner's claim alone
    assert _outcomes(store) == [("idempotency_conflict", 409)]


def test_a_retry_while_the_first_call_runs_is_409(store):
    """Two real requests: the second arrives while the first is still in its route."""
    app, calls = make_probe_app()
    entered, release_route = threading.Event(), threading.Event()

    @app.post("/probe/slow", operation_id="probe_slow", openapi_extra=mcp_policy("write"))
    def probe_slow(body: dict = Body(default=None)):
        calls["count"]["probe_slow"] = calls["count"].get("probe_slow", 0) + 1
        entered.set()
        assert release_route.wait(10)
        return {"done": True}

    first: dict = {}
    worker = threading.Thread(target=lambda: first.setdefault(
        "res", mcp_client(app).post("/probe/slow", json={}, headers={"Idempotency-Key": "k-slow"})))
    worker.start()
    try:
        assert entered.wait(10)
        second = mcp_client(app).post("/probe/slow", json={}, headers={"Idempotency-Key": "k-slow"})
    finally:
        release_route.set()
        worker.join(10)
    assert (second.status_code, second.json()) == (409, IN_PROGRESS)
    assert first["res"].status_code == 200 and calls["count"]["probe_slow"] == 1
    third = mcp_client(app).post("/probe/slow", json={}, headers={"Idempotency-Key": "k-slow"})
    assert (third.status_code, third.json(), third.headers[REPLAY]) == (200, {"done": True}, "true")


def test_a_lost_claim_answers_from_a_fresh_lookup(probe, store, monkeypatch):
    """The key is free at lookup, but another request claims it before this one does."""
    _, c, calls = probe
    real_lookup = mcp_idempotency.lookup
    seen = []

    def racing_lookup(client, key, request_sha256, **kwargs):
        seen.append(key)
        if len(seen) == 1:
            assert claim(client, key, method="POST", path="/probe/write", request_sha256=request_sha256)
            return IdemLookup("absent", None)
        return real_lookup(client, key, request_sha256, **kwargs)

    monkeypatch.setattr(mcp_idempotency, "lookup", racing_lookup)
    res = c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-race"})
    assert (res.status_code, res.json()) == (409, IN_PROGRESS) and seen == ["k-race", "k-race"]
    assert calls["count"].get("probe_write", 0) == 0
    assert [r["state"] for r in _rows(store)] == ["in_progress"]


def test_expired_record_is_ignored(probe, store):
    _, c, calls = probe
    created = iso_utc(utc_now() - timedelta(hours=25))
    store.insert(IDEMPOTENCY_TABLE, {
        "id": record_id("jarvis", "k-old"), "client": "jarvis", "key": "k-old", "method": "POST",
        "path": "/probe/write", "request_sha256": WRITE_SHA, "state": "done", "status": 201,
        "content_type": "application/json", "body_text": '{"stale":true}', "too_large": False,
        "created_at": created, "completed_at": created})
    res = c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-old"})
    assert (res.status_code, res.json()) == (200, {"n": 1}) and REPLAY not in res.headers
    assert calls["count"]["probe_write"] == 1
    (row,) = _rows(store)
    assert row["created_at"] > created and row["body_text"] == '{"n":1}'


def test_pending_approval_is_replayed_not_duplicated(probe, store):
    _, c, calls = probe
    a = c.post(LIVE_ACT, json={"qty": 1}, headers={"Idempotency-Key": "k-live"})
    b = c.post(LIVE_ACT, json={"qty": 1}, headers={"Idempotency-Key": "k-live"})
    assert a.status_code == b.status_code == 202 and b.headers[REPLAY] == "true"
    assert a.json() == b.json() and a.json()["pending_approval"]["id"]
    assert len(store.run("McpApprovals")) == 1 and calls["count"].get("probe_act", 0) == 0
    mcp_approvals.notify_approval_async.assert_called_once()
    assert _outcomes(store) == [("approval_required", 202), ("idempotent_replay", 202)]


# -- release ------------------------------------------------------------------------

def test_route_exception_releases_the_key(store):
    app, calls = make_probe_app()
    c = mcp_client(app, raise_server_exceptions=False)
    assert c.post("/probe/boom", json={}, headers={"Idempotency-Key": "k-boom"}).status_code == 500
    assert _rows(store) == []
    assert c.post("/probe/boom", json={}, headers={"Idempotency-Key": "k-boom"}).status_code == 500
    assert calls["count"]["probe_boom"] == 2 and _rows(store) == []
    assert _outcomes(store) == [("error", 500), ("error", 500)]


def test_failed_approval_insert_releases_the_key(store, monkeypatch):
    monkeypatch.setattr(mcp_approvals, "create_approval", Mock(side_effect=RuntimeError("store down")))
    app, _ = make_probe_app()
    res = mcp_client(app, raise_server_exceptions=False).post(
        LIVE_ACT, json={"qty": 1}, headers={"Idempotency-Key": "k-held"})
    assert res.status_code == 500 and _rows(store) == []


def test_a_failed_release_still_audits_and_raises(store, monkeypatch):
    monkeypatch.setattr(mcp_idempotency, "release", Mock(side_effect=RuntimeError("store down")))
    app, _ = make_probe_app()
    res = mcp_client(app, raise_server_exceptions=False).post("/probe/boom", json={}, headers=IDEM())
    assert res.status_code == 500 and _outcomes(store) == [("error", 500)]


def test_a_failed_finalize_keeps_the_key_claimed(probe, store, monkeypatch):
    """The route ran; a retry must not run it again, so the claim stays in progress."""
    _, c, calls = probe
    monkeypatch.setattr(mcp_idempotency, "finalize", Mock(side_effect=RuntimeError("store down")))
    res = c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-fin"})
    assert (res.status_code, res.json()) == (200, {"n": 1}) and _outcomes(store) == [("ok", 200)]
    again = c.post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-fin"})
    assert (again.status_code, again.json()) == (409, IN_PROGRESS)
    assert calls["count"]["probe_write"] == 1


def test_a_failed_lookup_is_a_500_with_one_audit_row(store, monkeypatch):
    monkeypatch.setattr(mcp_idempotency, "lookup", Mock(side_effect=RuntimeError("store down")))
    app, calls = make_probe_app()
    res = mcp_client(app, raise_server_exceptions=False).post("/probe/write", json={}, headers=IDEM())
    assert res.status_code == 500 and calls["count"].get("probe_write", 0) == 0
    assert _outcomes(store) == [("error", 500)]


# -- scope ----------------------------------------------------------------------------

def test_keys_are_scoped_per_client(store):
    app, calls = make_probe_app()
    a = mcp_client(app).post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-1"})
    b = mcp_client(app, client="other").post("/probe/write", json={"n": 1}, headers={"Idempotency-Key": "k-1"})
    assert a.status_code == b.status_code == 200 and REPLAY not in b.headers
    assert calls["count"]["probe_write"] == 2
    assert sorted(r["client"] for r in _rows(store)) == ["jarvis", "other"]


# -- the 1 MiB response cap ---------------------------------------------------------

def test_an_oversized_response_replays_as_too_large(store):
    app, calls = make_probe_app()
    big = "x" * RESPONSE_CAP_BYTES

    @app.post("/probe/big", operation_id="probe_big", openapi_extra=mcp_policy("write"))
    def probe_big(body: dict = Body(default=None)):
        calls["count"]["probe_big"] = calls["count"].get("probe_big", 0) + 1
        return {"big": big}

    c = mcp_client(app)
    a = c.post("/probe/big", json={}, headers={"Idempotency-Key": "k-big"})
    assert a.status_code == 200 and a.json() == {"big": big}          # the caller still gets it all
    (row,) = _rows(store)
    assert row["too_large"] is True and row.get("body_text") is None and row["status"] == 200
    b = c.post("/probe/big", json={}, headers={"Idempotency-Key": "k-big"})
    assert (b.status_code, b.json(), b.headers[REPLAY]) == (200, TOO_LARGE, "true")
    assert calls["count"]["probe_big"] == 1


def test_the_capture_keeps_no_more_than_the_cap():
    forwarded: list[dict] = []

    async def send(message):
        forwarded.append(message)

    chunk = b"y" * (RESPONSE_CAP_BYTES // 2 + 7)
    messages = [{"type": "http.response.start", "status": 201,
                 "headers": [(b"content-type", b"text/plain; charset=utf-8")]}]
    messages += [{"type": "http.response.body", "body": chunk, "more_body": True} for _ in range(3)]
    messages += [{"type": "http.response.body", "body": b"", "more_body": False}]
    capture = _ResponseCapture(send, keep_body=True)

    async def run():
        for message in messages:
            await capture.send(message)

    anyio.run(run)
    assert forwarded == messages                                        # forwarded unchanged
    assert (capture.status, capture.content_type) == (201, "text/plain; charset=utf-8")
    assert capture.body == (chunk * 3)[:RESPONSE_CAP_BYTES + 1]


# -- the module ---------------------------------------------------------------------

def test_record_id_and_constants():
    assert record_id("jarvis", "k-1") == hashlib.sha256(b"jarvis\x00k-1").hexdigest()
    assert record_id("jarvis", "k-1") != record_id("jarvi", "sk-1")
    assert (IDEMPOTENCY_TABLE, TTL_S, RESPONSE_CAP_BYTES) == ("McpIdempotency", 86400, 1048576)
    assert KEY_RE.pattern == r"^[A-Za-z0-9._:-]{1,128}$"


def test_lookup_kinds_and_ttl(store):
    t0 = utc_now()
    assert lookup("jarvis", "k", "sha-a", now=t0) == IdemLookup("absent", None)
    assert claim("jarvis", "k", method="POST", path="/p", request_sha256="sha-a", now=t0) is True
    assert claim("jarvis", "k", method="POST", path="/p", request_sha256="sha-a", now=t0) is False
    assert lookup("jarvis", "k", "sha-a", now=t0).kind == "in_progress"
    assert lookup("jarvis", "k", "sha-b", now=t0).kind == "conflict"
    finalize("jarvis", "k", status=204, content_type=None, body=b"", now=t0)
    found = lookup("jarvis", "k", "sha-a", now=t0 + timedelta(seconds=TTL_S))
    assert found.kind == "replay" and (found.record["status"], found.record["body_text"]) == (204, "")
    assert lookup("jarvis", "k", "sha-a", now=t0 + timedelta(seconds=TTL_S + 1)).kind == "absent"
    # An expired row is cleared by the next claim, which then wins.
    later = t0 + timedelta(seconds=TTL_S + 1)
    assert claim("jarvis", "k", method="POST", path="/p", request_sha256="sha-b", now=later) is True
    assert lookup("jarvis", "k", "sha-b", now=later).kind == "in_progress"
    release("jarvis", "k")
    assert _rows(store) == []


def test_finalize_caps_the_stored_body(store):
    claim("jarvis", "at-cap", method="POST", path="/p", request_sha256="s")
    claim("jarvis", "over", method="POST", path="/p", request_sha256="s")
    finalize("jarvis", "at-cap", status=200, content_type="text/plain", body=b"z" * RESPONSE_CAP_BYTES)
    finalize("jarvis", "over", status=200, content_type="text/plain", body=b"z" * (RESPONSE_CAP_BYTES + 1))
    at_cap = store.get(IDEMPOTENCY_TABLE, record_id("jarvis", "at-cap"))
    over = store.get(IDEMPOTENCY_TABLE, record_id("jarvis", "over"))
    assert at_cap["too_large"] is False and len(at_cap["body_text"]) == RESPONSE_CAP_BYTES
    assert over["too_large"] is True and over.get("body_text") is None
    assert at_cap["state"] == over["state"] == "done" and at_cap["completed_at"]


def test_finalize_replaces_undecodable_bytes(store):
    claim("jarvis", "bin", method="POST", path="/p", request_sha256="s")
    finalize("jarvis", "bin", status=200, content_type="application/octet-stream", body=b"ok\xff")
    assert store.get(IDEMPOTENCY_TABLE, record_id("jarvis", "bin"))["body_text"] == "ok\ufffd"
