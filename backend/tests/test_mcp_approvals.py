"""MCP approval records, summaries, push and the read routes (plan Task 10, contract §5).

The gate holds a live-money, unknown, protected or irreversible MCP call as one
McpApprovals row and pushes its summary to the operator's iPhone. These tests pin
the row (status, expiry, the canonical body_sha256 the iOS app recomputes), the
lazy transitions a read applies (pending past expiry -> expired, a claimed
execution that never recorded -> failed, each scrubbed of secrets), who may see
a row (its own user; on :8012 only its own client), and the two read routes.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import timedelta
from unittest.mock import Mock

import pytest

import apns_sender
import mcp_approvals
from mcp_approvals import (
    APPROVALS_TABLE, PUBLIC_FIELDS, approval_public, build_summary, cas_status, create_approval,
    get_visible, list_visible, send_approval_push,
)
from mcp_helpers import (
    CFG, PRINCIPAL, T0, human_headers, make_probe_app, mcp_client, mcp_env, public_client,
    seed_operator, use_store,
)
from mcp_policy import canonical_request_sha256, iso_utc

HUMAN_VIEWER = {"id": "u-op", "username": "operator"}


@pytest.fixture(autouse=True)
def _mcp(monkeypatch, store):
    use_store(monkeypatch, store)
    seed_operator(store)
    mcp_env(monkeypatch)
    monkeypatch.setattr(apns_sender, "send_to_user", Mock())   # never reach APNs


def _at(monkeypatch, seconds: float) -> None:
    """Freeze the module's clock at T0 + seconds."""
    monkeypatch.setattr(mcp_approvals, "utc_now", lambda: T0 + timedelta(seconds=seconds))


def _make(**over) -> dict:
    kwargs = dict(principal=PRINCIPAL, operation_id="live_command", method="POST", path="/p", query={},
                  body={"a": 1}, money_state="live", summary="s", idempotency_key=None,
                  correlation_id=None, cfg=CFG, now=T0)
    kwargs.update(over)
    return create_approval(**kwargs)


# -- summaries ----------------------------------------------------------------

def test_summary_formats():
    assert build_summary("live_command", "POST", "/instances/alpaca-main/live-command", {"instance_id": "alpaca-main"},
        {"type": "submit_order", "payload": {"symbol": "TQQQ", "side": "buy", "qty": 10}}, "live") == \
        "submit_order BUY 10 TQQQ on alpaca-main — LIVE"
    assert build_summary("delete_instance", "DELETE", "/instances/x", {"instance_id": "x"}, None, "irreversible") == \
        "delete_instance: DELETE /instances/x — IRREVERSIBLE"
    assert len(build_summary("edit_strategy", "PUT", "/strategies/1" + "9" * 300, {}, None, "protected")) <= 200


@pytest.mark.parametrize("args,expected", [
    (("live_command", "POST", "/i/a/live-command", {"instance_id": "a"},
      {"type": "close_position", "payload": {"symbol": "GLD"}}, "unknown"),
     "close_position GLD on a — MONEY STATE UNKNOWN"),
    (("live_command", "POST", "/i/a/live-command", {"instance_id": "a"}, {"type": "halt"}, "live"),
     "halt on a — LIVE"),
    (("brokerage_place_order", "POST", "/brokerages/b1/orders", {"brokerage_id": "b1"},
      {"symbol": "SPY", "side": "sell", "qty": "2", "type": "limit"}, "live"),
     "SELL 2 SPY limit on brokerage b1 — LIVE"),
    (("brokerage_place_order", "POST", "/brokerages/b1/orders", {"brokerage_id": "b1"},
      {"symbol": "SPY", "side": "buy", "notional": 50}, "protected"),
     "BUY $50 SPY market on brokerage b1 — PROTECTED PAPER"),
    (("start_instance", "POST", "/instances/f14-paper/start", {"instance_id": "f14-paper"}, None, "protected"),
     "start instance f14-paper — PROTECTED PAPER"),
    (("stop_instance", "POST", "/instances/alpaca-main/stop", {"instance_id": "alpaca-main"}, None, "live"),
     "stop instance alpaca-main — LIVE"),
    (("swing_decide_signal", "POST", "/instances/s/swing/signals/g1/decision",
      {"instance_id": "s", "signal_id": "g1"}, {"decision": "approve_half"}, "live"),
     "approve_half swing signal g1 on s — LIVE"),
    (("edit_strategy", "put", "/strategies/7", {"strategy_id": "7"}, {}, "paper"),
     "edit_strategy: PUT /strategies/7"),
])
def test_summary_format_per_operation(args, expected):
    assert build_summary(*args) == expected


def test_summary_truncates_the_core_and_keeps_the_suffix():
    got = build_summary("edit_strategy", "PUT", "/strategies/" + "9" * 300, {}, None, "protected")
    assert len(got) == 200 and got.endswith("… — PROTECTED PAPER") and got.startswith("edit_strategy: PUT /strategies/9")


def test_summary_flattens_control_characters_an_agent_could_hide_text_with():
    got = build_summary("live_command", "POST", "/p", {"instance_id": "alpaca-main"},
                        {"type": "submit_order", "payload": {"symbol": "TQQQ\n\nnothing to see", "side": "buy",
                                                             "qty": "1\u202e"}}, "live")
    assert got == "submit_order BUY 1 TQQQ nothing to see on alpaca-main — LIVE"


# -- the row ------------------------------------------------------------------

def test_create_approval_row(store, monkeypatch):
    row = create_approval(principal=PRINCIPAL, operation_id="live_command", method="POST", path="/p", query={},
                          body={"a": 1}, money_state="live", summary="s", idempotency_key="k1",
                          correlation_id="c1", cfg=CFG, now=T0)
    assert row["status"] == "pending" and row["expires_at"] == iso_utc(T0 + timedelta(seconds=900))
    assert row["body_sha256"] == canonical_request_sha256("POST", "/p", {}, {"a": 1})
    assert set(approval_public(row)) == set(PUBLIC_FIELDS)


def test_create_approval_stores_every_field(store):
    row = _make(method="post", idempotency_key="k1", correlation_id="c1", query={"q": "1"})
    stored = store.get(APPROVALS_TABLE, row["id"])
    assert stored == row
    assert mcp_approvals.ID_RE.fullmatch(row["id"]) and len(row["id"]) == 32
    assert (row["created_at"], row["user_id"], row["username"], row["client"], row["operation_id"]) == \
        (iso_utc(T0), "u-op", "operator", "jarvis", "live_command")
    assert (row["method"], row["query"], row["money_state"], row["idempotency_key"], row["correlation_id"]) == \
        ("POST", {"q": "1"}, "live", "k1", "c1")
    assert all(row[k] is None for k in ("result_status", "result", "decided_at", "device_id",
                                        "exec_claimed_at", "executed_at"))


def test_approval_public_is_exactly_the_contract_fields(store):
    public = approval_public(_make(idempotency_key="k1"))
    assert tuple(public) == PUBLIC_FIELDS
    assert "user_id" not in public and "idempotency_key" not in public and "exec_claimed_at" not in public


def test_approval_object_rehashes_to_body_sha256(store, monkeypatch):   # iOS recomputes it (contract §5)
    a = approval_public(create_approval(principal=PRINCIPAL, operation_id="x", method="POST", path="/instances/my bot/act",
        query={"q": ["1", "2"]}, body={"note": "é", "n": 1.5}, money_state="live", summary="s",
        idempotency_key=None, correlation_id=None, cfg=CFG, now=T0))
    canon = json.dumps({"method": a["method"], "path": a["path"], "query": a["query"], "body": a["body"]},
                       sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert hashlib.sha256(canon).hexdigest() == a["body_sha256"]


def test_stored_approval_rehashes_to_body_sha256(store, monkeypatch):    # the object the routes serve
    _at(monkeypatch, 1)
    row = _make(method="post", path="/instances/my bot/act", query={"q": ["1", "2"]}, body={"note": "é", "n": 1.5})
    a = public_client(make_probe_app()[0]).get(f"/mcp/approvals/{row['id']}", headers=human_headers(monkeypatch)).json()
    assert canonical_request_sha256(a["method"], a["path"], a["query"], a["body"]) == a["body_sha256"]
    assert a["method"] == "POST"


# -- lazy transitions ---------------------------------------------------------

def test_pending_past_expiry_reads_expired_and_is_scrubbed(store, monkeypatch):
    row = _make(body={"secret": "s", "symbol": "TQQQ"})
    (early,) = list_visible(viewer=HUMAN_VIEWER, status="all", limit=50, cfg=CFG, now=T0 + timedelta(seconds=899))
    assert early["status"] == "pending" and early["body"] == {"secret": "s", "symbol": "TQQQ"}
    (late,) = list_visible(viewer=HUMAN_VIEWER, status="all", limit=50, cfg=CFG, now=T0 + timedelta(seconds=901))
    assert late["status"] == "expired" and late["body"] == {"secret": "[redacted]", "symbol": "TQQQ"}
    stored = store.get(APPROVALS_TABLE, row["id"])
    assert stored["status"] == "expired" and stored["body"] == {"secret": "[redacted]", "symbol": "TQQQ"}
    assert stored["body_sha256"] == row["body_sha256"]
    assert list_visible(viewer=HUMAN_VIEWER, status="pending", limit=50, cfg=CFG,
                        now=T0 + timedelta(seconds=901)) == []


def test_pending_route_hides_an_expired_row(store, monkeypatch):
    row = _make()
    _at(monkeypatch, 901)
    c = public_client(make_probe_app()[0])
    assert c.get("/mcp/approvals", headers=human_headers(monkeypatch)).json() == {"approvals": []}
    got = c.get(f"/mcp/approvals/{row['id']}", headers=human_headers(monkeypatch)).json()
    assert got["status"] == "expired"


def test_stale_execution_reads_failed(store, monkeypatch):
    row = _make(body={"api_key": "AK", "qty": 1})
    store.update(APPROVALS_TABLE, row["id"], {"status": "approved", "exec_claimed_at": iso_utc(T0)})
    fresh = get_visible(row["id"], viewer=HUMAN_VIEWER, cfg=CFG, now=T0 + timedelta(seconds=599))
    assert fresh["status"] == "approved" and fresh["result"] is None
    stale = get_visible(row["id"], viewer=HUMAN_VIEWER, cfg=CFG, now=T0 + timedelta(seconds=601))
    assert stale["status"] == "failed" and "outcome unknown" in stale["result"]["detail"]
    assert stale["result_status"] is None and stale["body"] == {"api_key": "[redacted]", "qty": 1}
    assert store.get(APPROVALS_TABLE, row["id"])["status"] == "failed"


def test_recorded_or_unclaimed_execution_is_not_failed(store):
    done = _make()
    store.update(APPROVALS_TABLE, done["id"], {"status": "approved", "exec_claimed_at": iso_utc(T0),
                                               "result_status": 200})
    unclaimed = _make()
    store.update(APPROVALS_TABLE, unclaimed["id"], {"status": "approved"})
    later = T0 + timedelta(seconds=3600)
    assert get_visible(done["id"], viewer=HUMAN_VIEWER, cfg=CFG, now=later)["status"] == "approved"
    assert get_visible(unclaimed["id"], viewer=HUMAN_VIEWER, cfg=CFG, now=later)["status"] == "approved"


def test_terminal_rows_are_left_alone(store):
    row = _make(body={"password": "p"})
    store.update(APPROVALS_TABLE, row["id"], {"status": "denied"})
    got = get_visible(row["id"], viewer=HUMAN_VIEWER, cfg=CFG, now=T0 + timedelta(days=2))
    assert got["status"] == "denied" and got["body"] == {"password": "p"}


def test_cas_status_needs_the_expected_status(store):
    row = _make()
    assert cas_status(row["id"], expect="approved", patch={"status": "executed"}) is None
    assert store.get(APPROVALS_TABLE, row["id"])["status"] == "pending"
    new = cas_status(row["id"], expect="pending", patch={"status": "denied", "device_id": "d1"})
    assert (new["status"], new["device_id"], new["body"]) == ("denied", "d1", {"a": 1})
    assert store.get(APPROVALS_TABLE, row["id"]) == new
    assert cas_status("missing", expect="pending", patch={"status": "denied"}) is None


def test_lost_race_returns_the_winner(store, monkeypatch):
    row = _make()
    store.update(APPROVALS_TABLE, row["id"], {"status": "approved"})     # a decision landed after our read
    got = mcp_approvals.refresh_lazily(row, cfg=CFG, now=T0 + timedelta(seconds=901))
    assert got["status"] == "approved"


# -- visibility and the routes --------------------------------------------------

def test_human_sees_only_own_approvals(store, monkeypatch):       # GET /mcp/approvals/{id} for another user's row -> 404
    _at(monkeypatch, 1)
    mine = _make(principal={**PRINCIPAL, "client": "other"})
    theirs = _make(principal={"id": "u-2", "username": "someone", "via": "mcp", "client": "jarvis"})
    c = public_client(make_probe_app()[0])
    res = c.get(f"/mcp/approvals/{theirs['id']}", headers=human_headers(monkeypatch))
    assert res.status_code == 404 and res.json() == {"detail": "approval not found"}
    got = c.get(f"/mcp/approvals/{mine['id']}", headers=human_headers(monkeypatch))
    assert got.status_code == 200 and got.json() == approval_public(mine)
    listed = c.get("/mcp/approvals", headers=human_headers(monkeypatch)).json()["approvals"]
    assert [a["id"] for a in listed] == [mine["id"]]


def test_mcp_principal_sees_only_its_client(store, monkeypatch):  # on :8012, client "jarvis" vs a row with client "other" -> 404
    _at(monkeypatch, 1)
    own = _make()
    other = _make(principal={**PRINCIPAL, "client": "other"})
    c = mcp_client(make_probe_app()[0])
    res = c.get(f"/mcp/approvals/{other['id']}")
    assert res.status_code == 404 and res.json() == {"detail": "approval not found"}
    assert c.get(f"/mcp/approvals/{own['id']}").json() == approval_public(own)
    assert [a["id"] for a in c.get("/mcp/approvals").json()["approvals"]] == [own["id"]]
    assert [a["id"] for a in mcp_client(make_probe_app()[0], client="other").get("/mcp/approvals")
            .json()["approvals"]] == [other["id"]]


def test_list_params(store, monkeypatch):                         # status=bogus -> 400; limit=0 -> 1 row max; limit=999 -> clamps to 200
    for i in range(201):
        _make(now=T0 + timedelta(milliseconds=i))
    _at(monkeypatch, 5)
    c, h = public_client(make_probe_app()[0]), human_headers(monkeypatch)
    res = c.get("/mcp/approvals", params={"status": "bogus"}, headers=h)
    assert res.status_code == 400
    assert len(c.get("/mcp/approvals", params={"limit": 0}, headers=h).json()["approvals"]) == 1
    assert len(c.get("/mcp/approvals", params={"limit": -5}, headers=h).json()["approvals"]) == 1
    assert len(c.get("/mcp/approvals", params={"limit": 999}, headers=h).json()["approvals"]) == 200
    assert len(c.get("/mcp/approvals", headers=h).json()["approvals"]) == 50


def test_list_is_newest_first_and_status_all_includes_decided(store, monkeypatch):
    first = _make(now=T0)
    second = _make(now=T0 + timedelta(seconds=1))
    third = _make(now=T0 + timedelta(seconds=2))
    store.update(APPROVALS_TABLE, second["id"], {"status": "denied"})
    _at(monkeypatch, 5)
    c, h = public_client(make_probe_app()[0]), human_headers(monkeypatch)
    pending = c.get("/mcp/approvals", headers=h).json()["approvals"]
    assert [a["id"] for a in pending] == [third["id"], first["id"]]
    everything = c.get("/mcp/approvals", params={"status": "all"}, headers=h).json()["approvals"]
    assert [a["id"] for a in everything] == [third["id"], second["id"], first["id"]]
    assert all(set(a) == set(PUBLIC_FIELDS) for a in everything)


def test_routes_need_a_session():
    c = public_client(make_probe_app()[0])
    assert c.get("/mcp/approvals").status_code == 401
    assert c.get("/mcp/approvals/abc", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_malformed_id_is_404(monkeypatch):                         # GET /mcp/approvals/bad%20id!! -> 404
    res = public_client(make_probe_app()[0]).get("/mcp/approvals/bad%20id!!", headers=human_headers(monkeypatch))
    assert res.status_code == 404 and res.json() == {"detail": "approval not found"}
    for bad in ("bad id!!", "a" * 129, "abc\n", ""):
        assert get_visible(bad, viewer=HUMAN_VIEWER, cfg=CFG, now=T0) is None


# -- push -----------------------------------------------------------------------

def test_push_payload(monkeypatch):
    sent = Mock(); monkeypatch.setattr(apns_sender, "send_to_user", sent)
    send_approval_push({"id": "a1", "user_id": "u-op", "summary": "s"})
    sent.assert_called_once_with("u-op", title="Jarvis wants to act", body="s", category="MCP_APPROVAL", data={"approval_id": "a1"})


def test_push_failure_is_swallowed(monkeypatch):
    monkeypatch.setattr(apns_sender, "send_to_user", Mock(side_effect=RuntimeError("apns down")))
    send_approval_push({"id": "a1", "user_id": "u-op", "summary": "s"})


def test_notify_runs_the_push_in_a_daemon_thread(monkeypatch):
    done, seen = threading.Event(), {}

    def fake(row):
        seen.update(row=row, daemon=threading.current_thread().daemon)
        done.set()

    monkeypatch.setattr(mcp_approvals, "send_approval_push", fake)
    mcp_approvals.notify_approval_async({"id": "a1", "user_id": "u-op", "summary": "s"})
    assert done.wait(5) and seen["daemon"] is True and seen["row"]["id"] == "a1"
