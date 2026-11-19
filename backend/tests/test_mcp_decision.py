"""Signed decisions and the exactly-once in-process replay (plan Task 13, contract §5).

A held MCP call runs only when its own user approves it from an enrolled approval
device: a P-256 signature over the exact message, a ts within 60 s of the server
clock, a device that is the user's, active and not revoked, and a body_sha256
that matches the row. The approval and the claim on its one execution are one
compare-and-swap, so a crash after approving leaves a claimed row that later
reads "failed", never one approved forever. The decision route then replays the
call in-process as the approver's MCP principal, through the gate's replay
branch, and records the outcome: executed for a 2xx, failed for anything else,
both terminal and scrubbed of secret-named body fields.
"""
from __future__ import annotations

import base64
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import anyio
import pytest

import apns_sender
import mcp_approvals
import notifications
from api.mcp_gate import NOT_AVAILABLE_DETAIL, REPLAY_SCOPE_KEY
from api.mcp_replay import RESULT_CAP_BYTES, ReplayResult, replay_approved_request
from mcp_approvals import (
    APPROVALS_TABLE, SIGNATURE_MAX_SKEW_S, DecisionError, approval_public, claim_execution, decide,
    record_result, register_device, revoke_device, signed_message, verify_signature,
)
from mcp_helpers import (
    CFG, IDEM, PRINCIPAL, T0, human_headers, human_token, make_probe_app, mcp_client, mcp_env,
    p256_keypair, public_client, seed_money_fixture, seed_operator, sign_decision, use_store,
)
from mcp_policy import iso_utc

OPERATOR = {"id": "u-op", "username": "operator"}
LIVE_ACT = "/probe/instances/alpaca-main/act"
NOT_EXECUTABLE = {"detail": "mcp: approval is not executable"}
NOT_FOUND = {"detail": "approval not found"}


@pytest.fixture(autouse=True)
def _mcp(monkeypatch, store):
    use_store(monkeypatch, store)
    seed_operator(store)
    store.insert("Users", {"id": "u-2", "username": "someone", "password_hash": "x", "token_version": 0})
    seed_money_fixture(store)
    mcp_env(monkeypatch)
    monkeypatch.setattr(apns_sender, "send_to_user", Mock())                  # never reach APNs
    monkeypatch.setattr(notifications, "notify", Mock())                      # never reach Discord
    monkeypatch.setattr(mcp_approvals, "notify_approval_async", Mock())       # no push thread
    _at(monkeypatch, 0)


@pytest.fixture
def human(monkeypatch) -> dict:
    return human_headers(monkeypatch)


@pytest.fixture
def probe():
    return make_probe_app()


@pytest.fixture
def device(store):
    """The operator's iPhone, enrolled 601 s before T0, so it went active at T0 - 1 s."""
    key, pub = p256_keypair()
    row = register_device(user=OPERATOR, name="iPhone", public_key_b64=pub, now=T0 - timedelta(seconds=601))
    assert row["active_at"] == iso_utc(T0 - timedelta(seconds=1))
    return SimpleNamespace(key=key, id=row["id"])


def _at(monkeypatch, seconds: float) -> None:
    """Freeze mcp_approvals' clock (creation, decision, execution) at T0 + seconds."""
    monkeypatch.setattr(mcp_approvals, "utc_now", lambda: T0 + timedelta(seconds=seconds))


def _ts(seconds: float = 0) -> int:
    return int((T0 + timedelta(seconds=seconds)).timestamp())


def _hold(app, store, path: str = LIVE_ACT, body: object = None, client: str = "jarvis") -> dict:
    """Send a call the gate holds on :8012; the pending McpApprovals row."""
    res = mcp_client(app, client=client).post(path, json={"qty": 1} if body is None else body, headers=IDEM())
    assert res.status_code == 202, res.text
    return store.get(APPROVALS_TABLE, res.json()["pending_approval"]["id"])


def decide_json(row: dict, device, decision: str = "approve", *, ts: int | None = None, key=None,
                body_sha256: str | None = None, device_id: str | None = None) -> dict:
    """The body the iOS app sends, signed per the contract by mcp_helpers.sign_decision."""
    ts = _ts() if ts is None else ts
    body_sha256 = row["body_sha256"] if body_sha256 is None else body_sha256
    return {"decision": decision, "body_sha256": body_sha256, "ts": ts,
            "device_id": device.id if device_id is None else device_id,
            "signature": sign_decision(key or device.key, row["id"], body_sha256, decision, ts)}


def _post(app, row: dict, payload: dict, headers: dict):
    return public_client(app).post(f"/mcp/approvals/{row['id']}/decision", json=payload, headers=headers)


def _status(store, row: dict) -> str:
    return store.get(APPROVALS_TABLE, row["id"])["status"]


def _audit_rows(store) -> list[dict]:
    return store.run("McpAudit")


# -- approve ----------------------------------------------------------------------

def test_approve_executes_exactly_once(store, probe, device, human):
    app, calls = probe
    row = _hold(app, store)
    res = _post(app, row, decide_json(row, device), human)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "executed" and body["result_status"] == 200
    assert body["result"] == {"instance_id": "alpaca-main", "body": {"qty": 1}}
    assert (body["decided_at"], body["device_id"]) == (iso_utc(T0), device.id)
    assert tuple(body) == mcp_approvals.PUBLIC_FIELDS
    assert calls["count"]["probe_act"] == 1
    again = _post(app, row, decide_json(row, device), human)
    assert again.status_code == 409 and calls["count"]["probe_act"] == 1
    assert _status(store, row) == "executed"


def test_replay_runs_under_the_approvers_mcp_principal(store, probe, device, human):
    app, calls = probe
    row = _hold(app, store, body={"qty": 3, "note": "é"})
    assert _post(app, row, decide_json(row, device), human).status_code == 200
    assert calls["principal"] == {"id": "u-op", "username": "operator", "via": "mcp", "client": "jarvis"}
    assert calls["body"] == {"qty": 3, "note": "é"}


def test_replay_keeps_the_client_that_asked(store, probe, device, human):
    app, calls = probe
    row = _hold(app, store, client="other-agent")
    assert _post(app, row, decide_json(row, device), human).json()["status"] == "executed"
    assert calls["principal"]["client"] == "other-agent"


def test_ts_at_the_skew_bound_is_accepted(store, probe, device, human, monkeypatch):
    app, calls = probe
    row = _hold(app, store)
    _at(monkeypatch, 100)
    res = _post(app, row, decide_json(row, device, ts=_ts(100 - SIGNATURE_MAX_SKEW_S)), human)
    assert res.status_code == 200 and res.json()["status"] == "executed"
    assert SIGNATURE_MAX_SKEW_S == 60


def test_approval_and_execution_claim_are_one_write(store, probe, device, monkeypatch):
    """A crash right after approving (before the replay runs) must not strand the
    row as "approved" forever: it is claimed in the same write, so a read past the
    stale window marks it failed."""
    app, calls = probe
    row = _hold(app, store)
    _at(monkeypatch, 5)
    approved = decide(row["id"], decide_json(row, device, ts=_ts(5)), user=OPERATOR, cfg=CFG)
    assert approved["status"] == "approved"
    assert approved["exec_claimed_at"] == approved["decided_at"] == iso_utc(T0 + timedelta(seconds=5))
    assert store.get(APPROVALS_TABLE, row["id"]) == approved
    assert calls["count"].get("probe_act", 0) == 0
    later = mcp_approvals.get_visible(row["id"], viewer=OPERATOR, cfg=CFG,
                                      now=T0 + timedelta(seconds=5 + CFG.approval_exec_stale_s + 1))
    assert later["status"] == "failed" and "outcome unknown" in later["result"]["detail"]


# -- deny -------------------------------------------------------------------------

def test_deny_does_not_execute(store, probe, device, human):
    app, calls = probe
    row = _hold(app, store, body={"qty": 1, "api_key": "AK"})
    res = _post(app, row, decide_json(row, device, "deny"), human)
    assert res.status_code == 200 and res.json()["status"] == "denied"
    assert (res.json()["decided_at"], res.json()["device_id"], res.json()["result_status"]) == \
        (iso_utc(T0), device.id, None)
    assert calls["count"].get("probe_act", 0) == 0
    stored = store.get(APPROVALS_TABLE, row["id"])
    assert stored["body"] == {"qty": 1, "api_key": "[redacted]"} and stored["body_sha256"] == row["body_sha256"]
    assert stored["exec_claimed_at"] is None
    assert _post(app, row, decide_json(row, device), human).status_code == 409
    assert calls["count"].get("probe_act", 0) == 0


# -- refusals ---------------------------------------------------------------------

def _refused(app, store, calls, row, payload, headers, status: int):
    res = _post(app, row, payload, headers)
    assert res.status_code == status, res.text
    assert _status(store, row) == "pending" and calls["count"].get("probe_act", 0) == 0
    return res


def test_bad_signature_is_403(store, probe, device, human):
    app, calls = probe
    row = _hold(app, store)
    other_key, _ = p256_keypair()
    _refused(app, store, calls, row, decide_json(row, device, key=other_key), human, 403)
    # A valid signature over another decision, or another approval, does not carry over.
    tampered = decide_json(row, device, "deny") | {"decision": "approve"}
    _refused(app, store, calls, row, tampered, human, 403)
    other = _hold(app, store)                        # same body, so the same body_sha256
    assert other["body_sha256"] == row["body_sha256"]
    _refused(app, store, calls, row, decide_json(other, device), human, 403)


def test_unknown_device_is_403(store, probe, device, human):
    app, calls = probe
    row = _hold(app, store)
    _refused(app, store, calls, row, decide_json(row, device, device_id="0" * 32), human, 403)
    # Another user's device, even an active one, is unknown to this user.
    key, pub = p256_keypair()
    theirs = register_device(user={"id": "u-2"}, name="theirs", public_key_b64=pub, now=T0 - timedelta(days=1))
    _refused(app, store, calls, row, decide_json(row, device, key=key, device_id=theirs["id"]), human, 403)


def test_device_not_yet_active_is_403(store, probe, human):
    app, calls = probe
    row = _hold(app, store)
    key, pub = p256_keypair()
    fresh = register_device(user=OPERATOR, name="new", public_key_b64=pub, now=T0)
    store.update("McpApprovalDevices", fresh["id"], {"active_at": iso_utc(T0 + timedelta(seconds=60))})
    dev = SimpleNamespace(key=key, id=fresh["id"])
    res = _refused(app, store, calls, row, decide_json(row, dev), human, 403)
    assert "device" in res.json()["detail"]


def test_revoked_device_is_403(store, probe, device, human):
    app, calls = probe
    row = _hold(app, store)
    assert revoke_device(user_id="u-op", device_id=device.id, now=T0) is True
    _refused(app, store, calls, row, decide_json(row, device), human, 403)


def test_stale_ts_is_403(store, probe, device, human, monkeypatch):
    app, calls = probe
    row = _hold(app, store)
    _at(monkeypatch, 100)
    _refused(app, store, calls, row, decide_json(row, device, ts=_ts(100 - 61)), human, 403)
    _refused(app, store, calls, row, decide_json(row, device, ts=_ts(100 + 61)), human, 403)
    _refused(app, store, calls, row, decide_json(row, device, ts=10 ** 30), human, 403)


def test_body_sha_mismatch_is_400(store, probe, device, human):
    app, calls = probe
    row = _hold(app, store)
    res = _refused(app, store, calls, row, decide_json(row, device, body_sha256="a" * 64), human, 400)
    assert "body_sha256" in res.json()["detail"]


@pytest.mark.parametrize("change", [
    {"decision": "maybe"},
    {"decision": None},
    {"ts": "x"},
    {"ts": True},
    {"ts": 1.5},
    {"signature": None},
    {"signature": "!!not-base64!!"},
    {"signature": ""},
    {"signature": "A" * 204},
    {"body_sha256": "A" * 64},
    {"body_sha256": "a" * 63},
    {"body_sha256": 7},
    {"device_id": "bad id!!"},
    {"device_id": "a" * 129},
], ids=["decision-maybe", "decision-null", "ts-str", "ts-bool", "ts-float", "sig-null", "sig-not-b64",
        "sig-empty", "sig-long", "sha-upper", "sha-short", "sha-int", "device-bad", "device-long"])
def test_bad_input_is_400(store, probe, device, human, change):
    app, calls = probe
    row = _hold(app, store)
    payload = {**decide_json(row, device), **change}
    payload = {k: v for k, v in payload.items() if v is not None}
    _refused(app, store, calls, row, payload, human, 400)


def test_missing_field_is_400_and_a_non_object_is_422(store, probe, device, human):
    app, calls = probe
    row = _hold(app, store)
    for name in ("decision", "body_sha256", "ts", "device_id", "signature"):
        payload = {k: v for k, v in decide_json(row, device).items() if k != name}
        _refused(app, store, calls, row, payload, human, 400)
    _refused(app, store, calls, row, [decide_json(row, device)], human, 422)


def test_expired_pending_is_410_then_409(store, probe, device, human, monkeypatch):
    app, calls = probe
    row = _hold(app, store, body={"qty": 1, "password": "pw"})
    _at(monkeypatch, 901)
    res = _post(app, row, decide_json(row, device, ts=_ts(901)), human)
    assert res.status_code == 410 and calls["count"].get("probe_act", 0) == 0
    stored = store.get(APPROVALS_TABLE, row["id"])
    assert stored["status"] == "expired" and stored["body"] == {"qty": 1, "password": "[redacted]"}
    again = _post(app, row, decide_json(row, device, ts=_ts(901)), human)
    assert again.status_code == 409 and calls["count"].get("probe_act", 0) == 0


def test_other_user_gets_404(store, probe, device, monkeypatch):
    app, calls = probe
    row = _hold(app, store)
    other = {"Authorization": f"Bearer {human_token(monkeypatch, 'u-2', 'someone')}"}
    res = _refused(app, store, calls, row, decide_json(row, device), other, 404)
    assert res.json() == NOT_FOUND


def test_unknown_or_malformed_id_is_404(probe, device, human):
    app, _ = probe
    c = public_client(app)
    ghost = {"id": "0" * 32, "body_sha256": "a" * 64}
    for path in (f"/mcp/approvals/{'0' * 32}/decision", "/mcp/approvals/bad%20id!!/decision",
                 f"/mcp/approvals/{'a' * 129}/decision"):
        res = c.post(path, json=decide_json(ghost, device), headers=human)
        assert res.status_code == 404 and res.json() == NOT_FOUND


def test_decision_needs_a_session(store, probe, device):
    app, calls = probe
    row = _hold(app, store)
    assert _post(app, row, decide_json(row, device), {}).status_code == 401
    assert _post(app, row, decide_json(row, device), {"Authorization": "Bearer not-a-jwt"}).status_code == 401
    assert _status(store, row) == "pending"


def test_mcp_principal_cannot_decide(store, probe, device):
    from api import main

    app, calls = probe
    row = _hold(app, store)
    res = mcp_client(app).post(f"/mcp/approvals/{row['id']}/decision", json=decide_json(row, device))
    assert res.status_code == 403 and res.json() == {"detail": NOT_AVAILABLE_DETAIL}
    # Defense in depth: a principal that reached the route anyway is refused by decide().
    app.dependency_overrides[main.get_current_user] = lambda: dict(PRINCIPAL)
    res = _refused(app, store, calls, row, decide_json(row, device), {}, 403)
    assert res.json() == {"detail": NOT_AVAILABLE_DETAIL}
    with pytest.raises(DecisionError) as err:
        decide(row["id"], decide_json(row, device), user=dict(PRINCIPAL), cfg=CFG)
    assert (err.value.status, err.value.detail) == (403, NOT_AVAILABLE_DETAIL)


# -- failed executions --------------------------------------------------------------

def test_failed_replay_is_terminal(store, probe, device, human, monkeypatch):
    app, calls = probe
    boom = _hold(app, store, path="/probe/instances/alpaca-main/boom", body={"qty": 1, "token": "tk"})
    res = _post(app, boom, decide_json(boom, device), human)
    assert res.status_code == 200, res.text
    assert (res.json()["status"], res.json()["result_status"]) == ("failed", 500)
    assert calls["count"]["probe_act_boom"] == 1
    assert _post(app, boom, decide_json(boom, device), human).status_code == 409
    assert calls["count"]["probe_act_boom"] == 1
    stored = store.get(APPROVALS_TABLE, boom["id"])
    assert stored["body"] == {"qty": 1, "token": "[redacted]"}

    refuse = _hold(app, store, path="/probe/instances/alpaca-main/refuse")
    res = _post(app, refuse, decide_json(refuse, device), human)
    assert res.status_code == 200
    got = res.json()
    assert (got["status"], got["result_status"], got["result"]) == ("failed", 400, {"detail": "nope"})
    assert calls["count"]["probe_act_refuse"] == 1

    # Terminal: the stale-execution rule never touches a recorded outcome.
    _at(monkeypatch, 3600)
    for row in (boom, refuse):
        assert mcp_approvals.get_visible(row["id"], viewer=OPERATOR, cfg=CFG)["status"] == "failed"


def test_record_result_maps_status_codes(store, probe, device):
    app, _ = probe
    for code, expected in ((200, "executed"), (202, "executed"), (299, "executed"), (199, "failed"),
                           (300, "failed"), (404, "failed"), (503, "failed")):
        row = _hold(app, store)
        decide(row["id"], decide_json(row, device), user=OPERATOR, cfg=CFG)
        done = record_result(row["id"], status_code=code, result={"n": code})
        assert (done["status"], done["result_status"], done["result"]) == (expected, code, {"n": code})
        assert done["executed_at"] == iso_utc(T0)
        assert record_result(row["id"], status_code=200, result=None) is None    # terminal
    pending = _hold(app, store)
    assert record_result(pending["id"], status_code=200, result=None) is None     # never approved
    assert _status(store, pending) == "pending"


# -- the replay -----------------------------------------------------------------------

def _approved(app, store, device, **hold) -> dict:
    """An approved, claimed row whose replay has not run (decide() without the route)."""
    row = _hold(app, store, **hold)
    return decide(row["id"], decide_json(row, device), user=OPERATOR, cfg=CFG)


def test_replay_marker_is_one_shot(store, probe, device):
    app, calls = probe
    row = _approved(app, store, device)
    principal = dict(PRINCIPAL)
    first = anyio.run(replay_approved_request, app, row, principal)
    second = anyio.run(replay_approved_request, app, row, principal)
    assert first.status == 200 and second.status == 403 and calls["count"]["probe_act"] == 1
    assert first.body == {"instance_id": "alpaca-main", "body": {"qty": 1}}
    assert second.body == NOT_EXECUTABLE
    outcomes = sorted((r["outcome"], r["status"], r["approval_id"]) for r in _audit_rows(store)
                      if r["outcome"] in ("executed_approved", "replay_refused"))
    assert outcomes == [("executed_approved", 200, row["id"]), ("replay_refused", 403, row["id"])]


def test_replay_of_a_row_that_is_not_approved_is_refused(store, probe, device):
    app, calls = probe
    pending = _hold(app, store)
    res = anyio.run(replay_approved_request, app, pending, dict(PRINCIPAL))
    assert (res.status, res.body) == (403, NOT_EXECUTABLE)
    assert _status(store, pending) == "pending"
    denied = _hold(app, store)
    decide(denied["id"], decide_json(denied, device, "deny"), user=OPERATOR, cfg=CFG)
    assert anyio.run(replay_approved_request, app, denied, dict(PRINCIPAL)).status == 403
    assert calls["count"].get("probe_act", 0) == 0


def test_replay_refuses_a_mismatched_method_path_or_user(store, probe, device):
    app, calls = probe
    for change in ({"path": "/probe/instances/paper-1/act"}, {"method": "PUT"}):
        row = _approved(app, store, device)
        res = anyio.run(replay_approved_request, app, {**row, **change}, dict(PRINCIPAL))
        assert (res.status, res.body) == (403, NOT_EXECUTABLE)
    row = _approved(app, store, device)
    stranger = {"id": "u-2", "username": "someone", "via": "mcp", "client": "jarvis"}
    assert anyio.run(replay_approved_request, app, row, stranger).status == 403
    human = {"id": "u-op", "username": "operator"}                     # not an MCP principal
    assert anyio.run(replay_approved_request, app, _approved(app, store, device), human).status == 403
    assert calls["count"].get("probe_act", 0) == 0


def test_replay_preserves_percent_encoded_path(store, probe, device, human):
    app, calls = probe
    store.insert("Instances", {"id": "my bot", "brokerage_id": "b-live", "strategy_id": 200})
    row = _hold(app, store, path="/probe/instances/my%20bot/act")
    assert row["path"] == "/probe/instances/my bot/act" and row["money_state"] == "live"
    res = _post(app, row, decide_json(row, device), human)
    assert res.json()["status"] == "executed"
    assert res.json()["result"]["instance_id"] == "my bot" and calls["count"]["probe_act"] == 1


def test_irreversible_delete_with_a_query_executes(store, probe, device, human):
    app, calls = probe
    res = mcp_client(app).delete("/probe/things/1", params=[("force", "true"), ("tag", "a"), ("tag", "b")],
                                 headers=IDEM())
    assert res.status_code == 202
    row = store.get(APPROVALS_TABLE, res.json()["pending_approval"]["id"])
    assert (row["method"], row["query"], row["body"]) == ("DELETE", {"force": "true", "tag": ["a", "b"]}, None)
    got = _post(app, row, decide_json(row, device), human).json()
    assert (got["status"], got["result_status"], got["result"]) == ("executed", 200, {"deleted": "1"})
    assert calls["count"]["probe_delete"] == 1


def test_executed_approval_scrubs_secret_body_keys(store, probe, device, human):
    app, calls = probe
    row = _hold(app, store, body={"key": "AK", "qty": 1})
    assert _post(app, row, decide_json(row, device), human).json()["status"] == "executed"
    assert calls["body"] == {"key": "AK", "qty": 1}                     # the route saw the real value
    stored = store.get(APPROVALS_TABLE, row["id"])
    assert stored["body"] == {"key": "[redacted]", "qty": 1} and stored["body_sha256"] == row["body_sha256"]


def test_replay_is_audited_with_the_approval_id(store, probe, device, human):
    app, _ = probe
    row = _hold(app, store, body={"qty": 1, "secret": "s3"})
    assert _post(app, row, decide_json(row, device), human).status_code == 200
    (audit,) = [r for r in _audit_rows(store) if r["outcome"] == "executed_approved"]
    assert (audit["approval_id"], audit["status"], audit["operation_id"], audit["money_state"]) == \
        (row["id"], 200, "probe_act", "live")
    assert (audit["user_id"], audit["username"], audit["client"]) == ("u-op", "operator", "jarvis")
    assert audit["args_sha256"] == row["body_sha256"] and "s3" not in audit["args_summary"]
    assert isinstance(audit["latency_ms"], int)


def test_failed_replay_is_audited_too(store, probe, device, human):
    app, _ = probe
    row = _hold(app, store, path="/probe/instances/alpaca-main/boom")
    assert _post(app, row, decide_json(row, device), human).status_code == 200
    (audit,) = [r for r in _audit_rows(store) if r["outcome"] == "executed_approved"]
    assert (audit["approval_id"], audit["status"]) == (row["id"], 500)


def test_replay_runs_through_the_real_app(store):
    """The real app's whole middleware stack, not just the probe: GET /help (class read)."""
    from api import main

    row = mcp_approvals.create_approval(principal=PRINCIPAL, operation_id="help", method="GET", path="/help",
                                        query={}, body=None, money_state="irreversible", summary="s",
                                        idempotency_key=None, correlation_id=None, cfg=CFG, now=T0)
    store.update(APPROVALS_TABLE, row["id"], {"status": "approved", "exec_claimed_at": iso_utc(T0)})
    first = anyio.run(replay_approved_request, main.app, row, dict(PRINCIPAL))
    assert first.status == 200 and first.body
    assert anyio.run(replay_approved_request, main.app, row, dict(PRINCIPAL)) == ReplayResult(403, NOT_EXECUTABLE)


def test_claim_execution_is_one_shot_and_needs_an_approved_claimed_row(store, probe, device):
    app, _ = probe
    row = _approved(app, store, device)
    first = claim_execution(row["id"], now=T0 + timedelta(seconds=1))
    assert first["exec_started_at"] == iso_utc(T0 + timedelta(seconds=1))
    assert claim_execution(row["id"]) is None
    pending = _hold(app, store)
    assert claim_execution(pending["id"]) is None
    unclaimed = _hold(app, store)
    store.update(APPROVALS_TABLE, unclaimed["id"], {"status": "approved"})   # approved without the claim
    assert claim_execution(unclaimed["id"]) is None
    for bad in ("missing", "bad id!!", ""):
        assert claim_execution(bad) is None


# -- the replay transport, on a bare ASGI app --------------------------------------------

def _bare(*, status: int = 200, body: bytes = b'{"ok":true}', fail: str | None = None):
    """An ASGI app that records the scope and body it got and answers with status
    and body; fail="before" raises before answering, "after" after the start."""
    seen: dict = {}

    async def app(scope, receive, send):
        seen["scope"] = scope
        seen["request"] = await receive()
        early: list = []

        async def listen():
            early.append(await receive())

        async with anyio.create_task_group() as tg:
            tg.start_soon(listen)
            await anyio.sleep(0.01)
            seen["before_response"] = list(early)
            if fail == "before":
                raise RuntimeError("boom")
            await send({"type": "http.response.start", "status": status, "headers": []})
            if fail == "after":
                raise RuntimeError("boom")
            await send({"type": "http.response.body", "body": body})
        seen["after_response"] = list(early)

    return app, seen


APPROVAL = {"id": "a" * 32, "method": "POST", "path": "/instances/my bot/act",
            "query": {"tag": ["a", "b"], "note": "x y"}, "body": {"qty": 1, "note": "é"}, "client": "jarvis"}


def test_replay_scope_is_built_from_the_row():
    app, seen = _bare()
    result = anyio.run(replay_approved_request, app, APPROVAL, dict(PRINCIPAL))
    assert result == ReplayResult(status=200, body={"ok": True})
    scope = seen["scope"]
    assert (scope["type"], scope["method"], scope["scheme"], scope["http_version"]) == ("http", "POST", "http", "1.1")
    assert (scope["path"], scope["raw_path"], scope["root_path"]) == \
        ("/instances/my bot/act", b"/instances/my%20bot/act", "")
    assert scope["query_string"] == b"tag=a&tag=b&note=x+y"
    assert scope["client"] == ("mcp-replay", 0) and scope["server"] == ("mcp-replay", 0)
    assert scope[REPLAY_SCOPE_KEY] == {"approval_id": "a" * 32, "principal": PRINCIPAL}
    headers = dict(scope["headers"])
    assert len(headers) == len(scope["headers"])
    assert headers[b"host"] == b"mcp-replay" and headers[b"content-type"] == b"application/json"
    assert headers[b"authorization"] == b"Bearer mcp-replay" and headers[b"x-mcp-client"] == b"jarvis"
    request = seen["request"]
    assert request["type"] == "http.request" and request["more_body"] is False
    assert json.loads(request["body"]) == APPROVAL["body"]
    assert headers[b"content-length"] == str(len(request["body"])).encode()


def test_replay_receive_waits_for_the_response_before_disconnecting():
    app, seen = _bare()
    anyio.run(replay_approved_request, app, APPROVAL, dict(PRINCIPAL))
    assert seen["before_response"] == []
    assert seen["after_response"] == [{"type": "http.disconnect"}]


def test_replay_of_an_empty_body_sends_no_bytes():
    app, seen = _bare()
    anyio.run(replay_approved_request, app, {**APPROVAL, "body": None, "query": {}}, dict(PRINCIPAL))
    assert seen["request"]["body"] == b"" and dict(seen["scope"]["headers"])[b"content-length"] == b"0"
    assert seen["scope"]["query_string"] == b""


@pytest.mark.parametrize("fail,status", [("before", 500), ("after", 201)])
def test_replay_catches_an_app_exception(fail, status):
    app, _ = _bare(status=201, fail=fail)
    result = anyio.run(replay_approved_request, app, APPROVAL, dict(PRINCIPAL))
    assert result.status == status and result.body == {"text": ""}


def test_replay_result_that_is_not_json_is_text():
    app, _ = _bare(status=502, body=b"Bad Gateway")
    assert anyio.run(replay_approved_request, app, APPROVAL, dict(PRINCIPAL)) == \
        ReplayResult(status=502, body={"text": "Bad Gateway"})


def test_replay_result_is_capped():
    raw = json.dumps({"x": "a" * RESULT_CAP_BYTES})
    app, _ = _bare(body=raw.encode())
    result = anyio.run(replay_approved_request, app, APPROVAL, dict(PRINCIPAL))
    assert result.status == 200 and result.body == {"truncated": True, "partial": raw[:RESULT_CAP_BYTES]}
    assert RESULT_CAP_BYTES == 262144
    fits = json.dumps({"x": "a" * (RESULT_CAP_BYTES - 20)})
    app, _ = _bare(body=fits.encode())
    assert anyio.run(replay_approved_request, app, APPROVAL, dict(PRINCIPAL)).body == json.loads(fits)


# -- the signature primitives -----------------------------------------------------------

def test_signed_message_is_the_contract_bytes():
    assert signed_message("a1", "f" * 64, "approve", 1_700_000_000) == \
        b"a1|" + b"f" * 64 + b"|approve|1700000000"


def test_verify_signature():
    key, pub = p256_keypair()
    message = signed_message("a1", "f" * 64, "deny", 1_700_000_000)
    good = sign_decision(key, "a1", "f" * 64, "deny", 1_700_000_000)
    assert verify_signature(pub, message, good) is True
    assert verify_signature(pub, message + b"x", good) is False
    assert verify_signature(p256_keypair()[1], message, good) is False
    for bad_sig in ("!!", base64.b64encode(b"\x30\x00").decode(), "", None):
        assert verify_signature(pub, message, bad_sig) is False
    for bad_key in ("!!", base64.b64encode(b"\x04" + b"\0" * 64).decode(), None):
        assert verify_signature(bad_key, message, good) is False


def test_decision_error_carries_status_and_detail():
    err = DecisionError(409, "approval is no longer pending")
    assert (err.status, err.detail, str(err)) == (409, "approval is no longer pending", "approval is no longer pending")


def test_approval_public_of_an_executed_row_hides_the_claim_stamps(store, probe, device, human):
    app, _ = probe
    row = _hold(app, store)
    body = _post(app, row, decide_json(row, device), human).json()
    stored = store.get(APPROVALS_TABLE, row["id"])
    assert body == approval_public(stored)
    assert stored["exec_claimed_at"] == iso_utc(T0) and stored["exec_started_at"] == iso_utc(T0)
    assert "exec_claimed_at" not in body and "exec_started_at" not in body
