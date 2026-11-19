"""The gate's principal path on the MCP listener (plan Task 11, contract §3).

An admitted call is buffered whole first: past MCP_MAX_BODY_BYTES it is a 413,
and a non-empty body that is not JSON sent as application/json is a 415. Then
it is either held or run. A money call whose target is live, unknown or
protected, and every irreversible call, becomes a pending McpApprovals row, a
push and a 202 pending_approval, and the handler does not run. Anything else
runs as the MCP principal on the exact bytes the client sent. Every call gets
one McpAudit row with the canonical request hash.
"""
from __future__ import annotations

from unittest.mock import Mock

import anyio
import pytest

import apns_sender
import mcp_approvals
from mcp_helpers import (
    IDEM, MCP_TOKEN, PEER_IN, human_headers, make_probe_app, mcp_client, mcp_env, public_client,
    seed_money_fixture, seed_operator, use_store,
)
from mcp_policy import canonical_request_sha256

JSON = {"Content-Type": "application/json"}
UNPROCESSABLE = {"detail": "mcp: request body must be JSON the gate can process"}


@pytest.fixture(autouse=True)
def notify_mock(monkeypatch, store):
    use_store(monkeypatch, store)
    seed_operator(store)
    seed_money_fixture(store)
    mcp_env(monkeypatch)
    monkeypatch.setattr(apns_sender, "send_to_user", Mock())   # never reach APNs
    mock = Mock()
    monkeypatch.setattr(mcp_approvals, "notify_approval_async", mock)
    return mock


@pytest.fixture
def probe():
    app, calls = make_probe_app()
    return mcp_client(app), calls


def _held(res, calls, operation_id: str, money_state: str) -> dict:
    """Assert the call was held for approval without running; its pending_approval."""
    assert res.status_code == 202, res.text
    assert calls["count"].get(operation_id, 0) == 0
    pa = res.json()["pending_approval"]
    assert set(pa) == {"id", "summary", "money_state", "expires_at"} and pa["money_state"] == money_state
    return pa


def _audit_rows(store) -> list[dict]:
    return store.run("McpAudit")


# -- runs ---------------------------------------------------------------------

def test_read_runs(probe):
    c, calls = probe
    assert c.get("/probe/read").status_code == 200 and calls["count"]["probe_read"] == 1


def test_write_runs_and_sees_the_exact_body(probe):
    c, calls = probe
    res = c.post("/probe/write", content='{"x": "é"}'.encode("utf-8"), headers={**JSON, **IDEM()})
    assert res.status_code == 200 and res.json() == {"x": "é"}
    assert calls["body"] == {"x": "é"} and calls["count"]["probe_write"] == 1
    assert calls["principal"] == {"id": "u-op", "username": "operator", "via": "mcp", "client": "jarvis"}


def test_json_content_type_parameters_are_accepted(probe):
    c, calls = probe
    res = c.post("/probe/write", content=b'{"n": 1}',
                 headers={"Content-Type": "Application/JSON; charset=utf-8", **IDEM()})
    assert res.status_code == 200 and calls["body"] == {"n": 1}


def test_money_on_paper_runs(probe):
    c, calls = probe
    res = c.post("/probe/instances/paper-1/act", json={"qty": 1}, headers=IDEM())
    assert res.status_code == 200 and calls["count"]["probe_act"] == 1
    assert res.json() == {"instance_id": "paper-1", "body": {"qty": 1}}


def test_money_on_none_runs(probe):
    c, calls = probe
    assert c.post("/probe/instances/no-broker/act", json={}, headers=IDEM()).status_code == 200
    assert calls["count"]["probe_act"] == 1


def test_public_listener_runs_money_routes_directly(monkeypatch, store):
    """A human on :8011 never meets the approval path."""
    app, calls = make_probe_app()
    res = public_client(app).post("/probe/instances/alpaca-main/act", json={"qty": 1},
                                  headers=human_headers(monkeypatch))
    assert res.status_code == 200 and calls["count"]["probe_act"] == 1
    assert calls["principal"] == {"id": "u-op", "username": "operator"}
    assert store.run("McpApprovals") == [] and _audit_rows(store) == []


# -- held for approval --------------------------------------------------------

def test_money_on_live_waits_for_approval(probe, store, notify_mock):
    c, calls = probe
    res = c.post("/probe/instances/alpaca-main/act", json={"qty": 1},
                 headers={"Idempotency-Key": "k-live-1", "X-MCP-Correlation-Id": "corr-9"})
    assert res.status_code == 202 and calls["count"].get("probe_act", 0) == 0
    pa = res.json()["pending_approval"]
    assert set(pa) == {"id", "summary", "money_state", "expires_at"} and pa["money_state"] == "live"
    row = store.get("McpApprovals", pa["id"])
    assert row["status"] == "pending" and row["body"] == {"qty": 1} and row["client"] == "jarvis"
    assert row["body_sha256"] == canonical_request_sha256("POST", "/probe/instances/alpaca-main/act", {}, {"qty": 1})
    assert (row["user_id"], row["operation_id"], row["idempotency_key"], row["correlation_id"]) == \
        ("u-op", "probe_act", "k-live-1", "corr-9")
    assert (pa["summary"], pa["expires_at"]) == (row["summary"], row["expires_at"])
    assert pa["summary"].endswith("— LIVE")
    notify_mock.assert_called_once()
    assert notify_mock.call_args.args[0]["id"] == pa["id"]


def test_money_with_unknown_target_waits_for_approval(probe):
    c, calls = probe
    _held(c.post("/probe/instances/ghost/act", json={}, headers=IDEM()), calls, "probe_act", "unknown")


def test_protected_paper_waits(probe):
    c, calls = probe
    _held(c.post("/probe/instances/f14-paper/act", json={}, headers=IDEM()), calls, "probe_act", "protected")


def test_escalation_by_body_brokerage(probe):
    c, calls = probe
    res = c.post("/probe/instances/paper-1/act", json={"brokerage_id": "b-live"}, headers=IDEM())
    _held(res, calls, "probe_act", "live")


def test_brokerage_target(probe):
    c, calls = probe
    _held(c.post("/probe/brokerages/b-live/order", json={"qty": 1}, headers=IDEM()), calls, "probe_order", "live")
    assert c.post("/probe/brokerages/b-paper/order", json={"qty": 1}, headers=IDEM()).status_code == 200
    assert calls["count"]["probe_order"] == 1


def test_irreversible_always_waits(probe, store):
    c, calls = probe
    res = c.delete("/probe/things/1", params=[("force", "true"), ("tag", "a"), ("tag", "b")], headers=IDEM())
    pa = _held(res, calls, "probe_delete", "irreversible")
    row = store.get("McpApprovals", pa["id"])
    query = {"force": "true", "tag": ["a", "b"]}
    assert (row["method"], row["path"], row["query"], row["body"]) == ("DELETE", "/probe/things/1", query, None)
    assert row["body_sha256"] == canonical_request_sha256("DELETE", "/probe/things/1", query, None)


def test_replay_marker_header_is_ignored(probe):
    c, calls = probe
    res = c.post("/probe/instances/alpaca-main/act", json={"qty": 1},
                 headers={"intellistock.mcp_replay": "1", **IDEM()})
    _held(res, calls, "probe_act", "live")


def test_push_failure_still_answers_202(probe, store, monkeypatch):
    monkeypatch.setattr(mcp_approvals, "notify_approval_async", Mock(side_effect=RuntimeError("no thread")))
    c, calls = probe
    pa = _held(c.post("/probe/instances/alpaca-main/act", json={}, headers=IDEM()), calls, "probe_act", "live")
    assert store.get("McpApprovals", pa["id"])["status"] == "pending"


def test_approval_insert_failure_fails_closed(store, monkeypatch):
    monkeypatch.setattr(mcp_approvals, "create_approval", Mock(side_effect=RuntimeError("store down")))
    app, calls = make_probe_app()
    res = mcp_client(app, raise_server_exceptions=False).post(
        "/probe/instances/alpaca-main/act", json={"qty": 1}, headers=IDEM())
    assert res.status_code == 500 and calls["count"].get("probe_act", 0) == 0
    assert [(r["outcome"], r["status"]) for r in _audit_rows(store)] == [("error", 500)]


# -- the body -------------------------------------------------------------------

def test_body_too_large_is_413(monkeypatch, probe, store):
    mcp_env(monkeypatch, MCP_MAX_BODY_BYTES="10")
    c, calls = probe
    res = c.post("/probe/write", content=b'{"a":12345}', headers={**JSON, **IDEM()})
    assert res.status_code == 413 and res.json() == {"detail": "mcp: request body too large"}
    assert calls["count"].get("probe_write", 0) == 0
    assert [(r["outcome"], r["status"]) for r in _audit_rows(store)] == [("too_large", 413)]


def _asgi_post(app, chunks: list[bytes], *, complete: bool = True) -> list[dict]:
    """POST /probe/write on :8012 straight through ASGI, the body in 'chunks' and
    no Content-Length (a chunked upload); the messages sent back. With
    complete=False the client hangs up after the last chunk instead of ending it."""
    messages = [{"type": "http.request", "body": chunk,
                 "more_body": not complete or i < len(chunks) - 1}
                for i, chunk in enumerate(chunks)]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": "/probe/write", "raw_path": b"/probe/write", "root_path": "",
             "query_string": b"", "server": ("api", 8012), "client": (PEER_IN, 40000),
             "headers": [(b"authorization", f"Bearer {MCP_TOKEN}".encode()), (b"x-mcp-acting-user", b"u-op"),
                         (b"content-type", b"application/json"), (b"idempotency-key", b"k-chunked")]}
    anyio.run(app, scope, receive, send)
    return sent


def test_chunked_body_is_reassembled_for_the_route():
    app, calls = make_probe_app()
    sent = _asgi_post(app, [b'{"a": ', b'123456', b'}'])
    assert sent[0]["status"] == 200 and calls["body"] == {"a": 123456}


def test_chunked_body_over_the_cap_is_413(monkeypatch):
    mcp_env(monkeypatch, MCP_MAX_BODY_BYTES="10")
    app, calls = make_probe_app()
    sent = _asgi_post(app, [b'{"a": ', b'123456}'])
    assert sent[0]["status"] == 413 and calls["count"].get("probe_write", 0) == 0


def test_client_gone_mid_body_runs_nothing(store):
    app, calls = make_probe_app()
    sent = _asgi_post(app, [b'{"a": 1}'], complete=False)
    assert sent == [] and calls["count"].get("probe_write", 0) == 0
    assert [(r["outcome"], r["status"]) for r in _audit_rows(store)] == [("error", 499)]


@pytest.mark.parametrize("content,headers", [
    (b"a=1", {"Content-Type": "application/x-www-form-urlencoded"}),
    (b'{"a": 1}', {}),                                          # no content-type at all
    (b'{"a": 1', JSON),                                         # broken JSON
    (b'{"a": NaN}', JSON),                                      # not JSON, though Python parses it
    (b'{"a": 1}', {"Content-Type": "application/vnd.api+json"}),
])
def test_non_json_body_is_415(probe, store, content, headers):
    c, calls = probe
    res = c.post("/probe/write", content=content, headers={**headers, **IDEM()})
    assert res.status_code == 415 and res.json() == {"detail": "mcp: JSON body required"}
    assert calls["count"].get("probe_write", 0) == 0
    assert [(r["outcome"], r["status"]) for r in _audit_rows(store)] == [("unsupported_media", 415)]


def _nested(depth: int) -> bytes:
    """A JSON object nested 'depth' levels deep."""
    return ('{"a":' * depth + "1" + "}" * depth).encode()


@pytest.mark.parametrize("content", [
    b'{"x": "\\ud800"}',      # a lone surrogate: it parses, but cannot be UTF-8 encoded or hashed
    _nested(65),               # one level past the bound
    _nested(100_000),          # deep enough for the parser itself to give up
], ids=["lone_surrogate", "depth_65", "depth_100k"])
def test_json_the_gate_cannot_process_is_415_with_one_audit_row(probe, store, content):
    c, calls = probe
    res = c.post("/probe/write", content=content, headers={**JSON, **IDEM()})
    assert res.status_code == 415 and res.json() == UNPROCESSABLE
    assert calls["count"].get("probe_write", 0) == 0
    (row,) = _audit_rows(store)
    assert (row["outcome"], row["status"], row["args_summary"], row["operation_id"]) == \
        ("rejected", 415, "[unprocessable]", "probe_write")


def test_nesting_at_the_bound_runs(probe):
    c, calls = probe
    res = c.post("/probe/write", content=_nested(64), headers={**JSON, **IDEM()})
    assert res.status_code == 200 and calls["count"]["probe_write"] == 1


# -- route outcomes and audit -----------------------------------------------------

def test_route_exception_is_audited_and_raised(store):
    app, calls = make_probe_app()
    res = mcp_client(app, raise_server_exceptions=False).post("/probe/boom", json={}, headers=IDEM())
    assert res.status_code == 500 and calls["count"]["probe_boom"] == 1
    (row,) = _audit_rows(store)
    assert (row["outcome"], row["status"], row["operation_id"]) == ("error", 500, "probe_boom")
    with pytest.raises(RuntimeError, match="boom"):
        mcp_client(app).post("/probe/boom", json={}, headers=IDEM())


def test_route_4xx_passes_through(probe, store):
    c, calls = probe
    res = c.post("/probe/refuse", json={}, headers=IDEM())
    assert res.status_code == 400 and res.json() == {"detail": "nope"} and calls["count"]["probe_refuse"] == 1
    assert [(r["outcome"], r["status"]) for r in _audit_rows(store)] == [("error", 400)]


def test_audit_rows_carry_the_hash_summary_and_money_state(probe, store):
    c, _ = probe
    body = {"qty": 2, "api_key": "AK-9"}
    c.post("/probe/instances/paper-1/act", json=body, headers={"Idempotency-Key": "k-paper"})
    pa = c.post("/probe/instances/alpaca-main/act", json={"qty": 1}, headers=IDEM()).json()["pending_approval"]
    rows = {r["outcome"]: r for r in _audit_rows(store)}
    ok, held = rows["ok"], rows["approval_required"]
    assert ok["args_sha256"] == canonical_request_sha256("POST", "/probe/instances/paper-1/act", {}, body)
    assert '"qty":2' in ok["args_summary"] and "AK-9" not in ok["args_summary"]
    assert (ok["status"], ok["money_state"], ok["approval_id"], ok["idempotency_key"]) == (200, "paper", None, "k-paper")
    assert (held["status"], held["money_state"], held["approval_id"], held["operation_id"]) == \
        (202, "live", pa["id"], "probe_act")
    assert held["args_sha256"] == canonical_request_sha256("POST", "/probe/instances/alpaca-main/act", {}, {"qty": 1})


def test_kalshi_patch_that_would_turn_real_orders_on_is_held(monkeypatch, store):
    """Final review I1, through the real route. PATCH .../kalshi/config re-derives
    the mode on every call, so a rename sent to a live-brokerage bot stored
    paper_mode false + live_enabled false would write live_enabled true and place
    real orders. The gate must hold it, and the stored config must not change."""
    from api import main
    stored = {"paper_mode": False, "live_enabled": False}
    store.insert("Instances", {"id": "kalshi-off", "brokerage_id": "b-kalshi-live", "kind": "kalshi",
                               "name": "bot", "kalshi_config": dict(stored)})
    monkeypatch.setattr(main, "db_store", store)
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        res = mcp_client(main.app).patch("/instances/kalshi-off/kalshi/config",
                                         json={"name": "renamed"}, headers=IDEM())
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)
    assert res.status_code == 202, res.text
    assert res.json()["pending_approval"]["money_state"] == "live"
    row = store.get("Instances", "kalshi-off")
    assert (row["name"], row["kalshi_config"]) == ("bot", stored)


def test_gate_covers_routes_without_get_current_user(monkeypatch):
    from api import main
    c = mcp_client(main.app)
    assert c.get("/help").status_code == 200                     # read, no get_current_user
    for m, p in (("GET", "/kalshi/img"), ("POST", "/auth/login"), ("GET", "/ota/m/x.plist")):
        assert c.request(m, p, json={}).json() == {"detail": "mcp: operation not available"}
    assert mcp_client(main.app, token=None).get("/help").status_code == 401
