"""Internal MCP endpoints and the /health schema hash (plan Task 16, contract §4).

The MCP server (build D) reaches three service-level routes on :8012 with the
service token alone, no acting user: the filtered OpenAPI it builds its tools
from, a password check over the login limiter keyed (username, "mcp"), and an
auth-event sink that stores every event and relays the security ones to
Discord. /health carries the same schema hash, so D can poll it and refetch
the schema when it changes. On the public listener every internal route is a
401, and so is any of them once the kill switch is off.
"""
from __future__ import annotations

import hashlib
import json
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from pydantic import BaseModel

import apns_sender
import auth_utils
import notifications
from api import main, mcp_routes
from api.mcp_routes import AUTH_EVENTS, DISCORD_RELAYED_EVENTS, build_internal_openapi, schema_hash
from mcp_helpers import (
    PEER_OUT, make_probe_app, mcp_client, mcp_env, public_client, seed_operator, use_store,
)
from mcp_policy import mcp_policy

OPENAPI = "/internal/mcp/openapi.json"
VERIFY = "/internal/mcp/verify-password"
AUDIT_EVENT = "/internal/mcp/audit-event"
PASSWORD = "correct horse battery staple"
UNAUTHENTICATED = {"detail": "mcp: unauthenticated"}
SCHEMA_PREFIX = "#/components/schemas/"


@pytest.fixture(autouse=True)
def notify_mock(monkeypatch, store):
    use_store(monkeypatch, store)
    seed_operator(store)
    mcp_env(monkeypatch)
    auth_utils.reset_login_rate_limit()
    monkeypatch.setattr(apns_sender, "send_to_user", Mock())   # never reach APNs
    mock = Mock()
    monkeypatch.setattr(notifications, "notify", mock)         # never reach Discord
    yield mock
    auth_utils.reset_login_rate_limit()


@pytest.fixture
def healthy_db(monkeypatch):
    monkeypatch.setattr(main.db_pool, "health", lambda: {"ok": True, "size": 1, "dsn_host": "pg"})


@pytest.fixture
def pw_user(store) -> dict:
    """A user whose password is PASSWORD, hashed with the real bcrypt."""
    row = {"id": "u-pw", "username": "pwuser", "password_hash": auth_utils.hash_password(PASSWORD),
           "token_version": 0}
    store.insert("Users", row)
    return row


def _svc(**kwargs):
    """The MCP server's client for the service-level routes: no acting user."""
    return mcp_client(main.app, acting_user=None, **kwargs)


def _canonical_sha256(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


def _schema_refs(node) -> set[str]:
    """The component schema names every $ref under node points at."""
    found, stack = set(), [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            ref = item.get("$ref")
            if isinstance(ref, str) and ref.startswith(SCHEMA_PREFIX):
                found.add(ref[len(SCHEMA_PREFIX):])
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found


def _audit_rows(store, outcome: str | None = None) -> list[dict]:
    rows = sorted(store.run("McpAudit"), key=lambda r: r["ts"])
    return [r for r in rows if outcome is None or r["outcome"] == outcome]


# -- service auth -------------------------------------------------------------------

def test_internal_routes_refuse_the_public_listener(store, notify_mock, pw_user):
    c = public_client(main.app)
    assert c.get(OPENAPI).status_code == 401
    # The dependency fires before body validation: a malformed body is still a 401.
    for body in ({"username": "pwuser", "password": PASSWORD}, {}):
        res = c.post(VERIFY, json=body)
        assert res.status_code == 401 and res.json() == UNAUTHENTICATED
    res = c.post(AUDIT_EVENT, json={"kind": "auth", "event": "lockout", "detail": {}})
    assert res.status_code == 401 and res.json() == UNAUTHENTICATED
    assert store.run("McpAudit") == []
    assert not auth_utils.login_is_rate_limited("pwuser", "mcp")
    notify_mock.assert_not_called()


def test_internal_routes_still_need_the_service_auth():
    assert _svc(peer=PEER_OUT).get(OPENAPI).status_code == 401
    assert _svc(token="w" * 40).get(OPENAPI).status_code == 401
    assert _svc(token=None).post(AUDIT_EVENT, json={"kind": "auth", "event": "login_ok",
                                                    "detail": {}}).status_code == 401


def test_internal_routes_need_no_acting_user():
    assert mcp_client(main.app, acting_user=None).get(OPENAPI).status_code == 200


def test_kill_switch_closes_internal_routes(monkeypatch, store, pw_user, healthy_db):
    mcp_env(monkeypatch, MCP_ENABLED="false")
    c = _svc()
    assert c.get(OPENAPI).status_code == 401
    assert c.post(VERIFY, json={"username": "pwuser", "password": PASSWORD}).status_code == 401
    assert c.post(AUDIT_EVENT, json={"kind": "auth", "event": "login_ok", "detail": {}}).status_code == 401
    assert c.get("/health").status_code == 401
    assert _audit_rows(store, "auth_event") == [] and _audit_rows(store, "verify_password_ok") == []


# -- the internal OpenAPI -------------------------------------------------------------

def test_internal_openapi_excludes_never_and_keeps_x_mcp():
    doc = mcp_client(main.app, acting_user=None).get(OPENAPI).json()
    assert "/auth/login" not in doc["paths"] and "/app-updates/upload" not in doc["paths"] and OPENAPI not in doc["paths"]
    assert doc["paths"]["/instances/{instance_id}/live-command"]["post"]["x-mcp"] == {
        "class": "money", "tool": "live_command", "target": {"instance": "instance_id"}}
    assert "LoginBody" not in doc.get("components", {}).get("schemas", {})
    # Contract §4: the hash covers paths AND components, so a model change is seen.
    expected = hashlib.sha256(json.dumps({"paths": doc["paths"], "components": doc.get("components") or {}},
                                         sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=False).encode("utf-8")).hexdigest()
    assert doc["x-mcp-schema-hash"] == expected
    assert schema_hash(main.app) == expected


def test_internal_openapi_keeps_only_exposed_operations():
    doc = _svc().get(OPENAPI).json()
    classes = {op["x-mcp"]["class"] for item in doc["paths"].values() for op in item.values()}
    assert classes and "never" not in classes
    assert all(item for item in doc["paths"].values())


def test_internal_openapi_schemas_are_exactly_the_reachable_closure():
    doc = _svc().get(OPENAPI).json()
    schemas = doc["components"]["schemas"]
    reachable, frontier = set(), _schema_refs(doc["paths"])
    while frontier:
        name = frontier.pop()
        if name not in reachable:
            reachable.add(name)
            frontier |= _schema_refs(schemas.get(name, {}))
    assert reachable and set(schemas) == reachable


def test_build_internal_openapi_filters_a_probe_app():
    app, _ = make_probe_app()
    doc = build_internal_openapi(app)
    assert doc["paths"]["/probe/read"]["get"]["x-mcp"] == {"class": "read"}
    assert doc["paths"]["/probe/instances/{instance_id}/act"]["post"]["x-mcp"]["class"] == "money"
    for gone in ("/probe/never", "/probe/unannotated", "/mcp/approval-devices", OPENAPI, VERIFY, AUDIT_EVENT):
        assert gone not in doc["paths"]
    assert doc["x-mcp-schema-hash"] == _canonical_sha256({"paths": doc["paths"],
                                                          "components": doc.get("components") or {}})


def _app_with_body(model) -> FastAPI:
    """A fresh app with one write route whose body is model. The annotation is
    set by hand: under the __future__ import a local class name would stay a
    string FastAPI cannot resolve."""
    def make_thing(body):
        """Make a thing."""
        return {}

    make_thing.__annotations__ = {"body": model}
    app = FastAPI()
    app.add_api_route("/things", make_thing, methods=["POST"], operation_id="make_thing",
                      openapi_extra=mcp_policy("write"))
    return app


def test_a_body_model_change_alone_changes_the_hash():
    class ThingBody(BaseModel):
        name: str

    v1 = build_internal_openapi(_app_with_body(ThingBody))

    class ThingBody(BaseModel):  # noqa: F811 - the same schema name with one more field
        name: str
        size: int

    v2 = build_internal_openapi(_app_with_body(ThingBody))
    assert v1["paths"] == v2["paths"]                   # the $ref is the same
    assert v1["components"]["schemas"]["ThingBody"] != v2["components"]["schemas"]["ThingBody"]
    assert v1["x-mcp-schema-hash"] != v2["x-mcp-schema-hash"]


def test_build_internal_openapi_is_cached_and_leaves_the_app_schema_alone():
    app, _ = make_probe_app()
    first = build_internal_openapi(app)
    assert build_internal_openapi(app) is first and app.state.mcp_internal_openapi is first
    assert "/probe/never" in app.openapi()["paths"]
    assert schema_hash(app) == first["x-mcp-schema-hash"]


# -- /health ---------------------------------------------------------------------------

def test_health_carries_the_same_hash(healthy_db):
    body = public_client(main.app).get("/health").json()
    assert set(body) == {"status", "postgres", "host", "code", "mcp_schema_hash"}
    assert body["mcp_schema_hash"] == build_internal_openapi(main.app)["x-mcp-schema-hash"]


def test_degraded_health_carries_the_hash_too(monkeypatch):
    def down():
        raise RuntimeError("db down")
    monkeypatch.setattr(main.db_pool, "health", down)
    res = public_client(main.app).get("/health")
    assert res.status_code == 503
    assert set(res.json()) == {"status", "postgres", "host", "code", "mcp_schema_hash"}
    assert res.json()["mcp_schema_hash"] == schema_hash(main.app)


def test_health_answers_without_a_hash_when_the_schema_fails(monkeypatch, healthy_db):
    def broken(app):
        raise RuntimeError("schema build failed")
    monkeypatch.setattr(mcp_routes, "schema_hash", broken)
    res = public_client(main.app).get("/health")
    assert res.status_code == 200 and res.json()["mcp_schema_hash"] is None


def test_health_on_mcp_listener_needs_no_acting_user(healthy_db):
    res = _svc().get("/health")
    assert res.status_code == 200
    assert res.json()["mcp_schema_hash"] == schema_hash(main.app)


# -- verify-password ----------------------------------------------------------------------

def test_verify_password_ok_401_429(monkeypatch, store, pw_user):
    monkeypatch.setenv("LOGIN_RATE_LIMIT_MAX_ATTEMPTS", "3")
    c = _svc()
    wrong = {"username": "pwuser", "password": "wrong"}
    invalid = {"detail": "invalid credentials"}

    for _ in range(2):
        res = c.post(VERIFY, json=wrong)
        assert res.status_code == 401 and res.json() == invalid
    # Username lowercased and stripped; a success clears the (username, "mcp") bucket.
    res = c.post(VERIFY, json={"username": "  PwUser ", "password": PASSWORD})
    assert res.status_code == 200 and res.json() == {"user_id": "u-pw", "username": "pwuser"}
    for _ in range(3):
        res = c.post(VERIFY, json=wrong)
        assert res.status_code == 401 and res.json() == invalid

    res = c.post(VERIFY, json={"username": "pwuser", "password": PASSWORD})
    assert res.status_code == 429
    assert res.json() == {"detail": "mcp: too many attempts", "retry_after_s": 900}
    assert res.headers["Retry-After"] == "900"
    # The limiter key is (username, "mcp"): the human login from any host is untouched.
    assert auth_utils.login_is_rate_limited("pwuser", "mcp")
    assert not auth_utils.login_is_rate_limited("pwuser", "127.0.0.1")

    rows = _audit_rows(store)
    assert [r["outcome"] for r in rows] == (["verify_password_failed"] * 2 + ["verify_password_ok"]
                                            + ["verify_password_failed"] * 3 + ["verify_password_limited"])
    assert [r["status"] for r in rows] == [401, 401, 200, 401, 401, 401, 429]
    ok = rows[2]
    assert ok["user_id"] == "u-pw" and ok["username"] == "pwuser" and ok["client"] == "jarvis"
    assert ok["operation_id"] == "mcp_internal_verify_password" and ok["path"] == VERIFY
    dumped = json.dumps(rows)
    assert PASSWORD not in dumped and '"wrong"' not in dumped and pw_user["password_hash"] not in dumped


def test_verify_password_spray_does_not_lock_out_the_operator(monkeypatch, pw_user):
    """Every MCP user arrives through the one MCP server, so failures do not count
    per host across usernames: a spray of made-up names cannot lock out a real one."""
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_IP_MAX_ATTEMPTS", "2")
    c = _svc()
    for name in ("ghost1", "ghost2", "ghost3"):
        assert c.post(VERIFY, json={"username": name, "password": "x"}).status_code == 401
    assert c.post(VERIFY, json={"username": "pwuser", "password": PASSWORD}).status_code == 200


def test_verify_password_unknown_user_still_hashes(monkeypatch, store):
    monkeypatch.setattr(mcp_routes, "_DUMMY_BCRYPT_HASH", None)
    hash_spy = Mock(wraps=auth_utils.hash_password)
    verify_spy = Mock(wraps=auth_utils.verify_password)
    monkeypatch.setattr(auth_utils, "hash_password", hash_spy)
    monkeypatch.setattr(auth_utils, "verify_password", verify_spy)
    store.insert("Users", {"id": "u-nohash", "username": "nohash", "token_version": 0})
    c = _svc()

    res = c.post(VERIFY, json={"username": "nobody", "password": "guess"})
    assert res.status_code == 401 and res.json() == {"detail": "invalid credentials"}
    verify_spy.assert_called_once()
    plain, hashed = verify_spy.call_args.args
    assert plain == "guess" and hashed.startswith("$2")

    # A user with no stored hash is checked against the same dummy, and still fails.
    assert c.post(VERIFY, json={"username": "nohash", "password": "guess"}).status_code == 401
    assert verify_spy.call_count == 2 and verify_spy.call_args.args[1] == hashed
    hash_spy.assert_called_once()   # the dummy hash is computed once


# -- audit-event -----------------------------------------------------------------------------

def test_audit_event_stores_every_event(store, notify_mock):
    quiet = sorted(AUTH_EVENTS - DISCORD_RELAYED_EVENTS)
    c = _svc()
    for event in quiet:
        res = c.post(AUDIT_EVENT, json={"kind": "auth", "event": event, "detail": {"username": "operator"}})
        assert res.status_code == 204 and res.content == b""
    rows = _audit_rows(store)
    assert sorted(r["operation_id"] for r in rows) == [f"auth_event:{e}" for e in quiet]
    assert {r["outcome"] for r in rows} == {"auth_event"}
    assert {r["client"] for r in rows} == {"jarvis"}
    assert {r["args_summary"] for r in rows} == {'{"username":"operator"}'}
    notify_mock.assert_not_called()


def test_audit_event_accepts_session_revoked_with_only_a_count(store):
    res = _svc().post(AUDIT_EVENT, json={"kind": "auth", "event": "session_revoked", "detail": {"count": 3}})
    assert res.status_code == 204
    (row,) = _audit_rows(store)
    assert row["operation_id"] == "auth_event:session_revoked" and row["args_summary"] == '{"count":3}'


def test_audit_event_redacts_and_cuts_the_detail(store):
    detail = {"username": "operator", "password": "hunter2", "nested": {"refresh_token": "rt-1"},
              "note": "n" * 5000}
    assert _svc().post(AUDIT_EVENT, json={"kind": "auth", "event": "login_failed",
                                          "detail": detail}).status_code == 204
    (row,) = _audit_rows(store)
    assert "hunter2" not in row["args_summary"] and "rt-1" not in row["args_summary"]
    assert row["args_summary"].startswith('{"nested":{"refresh_token":"[redacted]"},"note":"nnn')
    assert len(row["args_summary"]) == 2000


def test_audit_event_relays_security_events(monkeypatch, store, notify_mock):
    c = _svc()
    for event in sorted(DISCORD_RELAYED_EVENTS):
        notify_mock.reset_mock()
        res = c.post(AUDIT_EVENT, json={"kind": "auth", "event": event,
                                        "detail": {"username": "operator", "password": "hunter2"}})
        assert res.status_code == 204
        notify_mock.assert_called_once()
        kwargs = notify_mock.call_args.kwargs
        assert kwargs["category"] == "other" and kwargs["instance_id"] == "mcp"
        assert kwargs["title"] == f"MCP security: {event}" and kwargs["discord_channel"] == "notifications"
        assert kwargs["body"] == f'MCP SECURITY [{event}] {{"password":"[redacted]","username":"operator"}}'
    assert len(_audit_rows(store, "auth_event")) == len(DISCORD_RELAYED_EVENTS)


def test_audit_event_relay_fits_a_discord_message(notify_mock):
    detail = {"note": "n" * 5000}
    assert _svc().post(AUDIT_EVENT, json={"kind": "auth", "event": "lockout", "detail": detail}).status_code == 204
    assert len(notify_mock.call_args.kwargs["body"]) <= 2000


def test_audit_event_with_an_unencodable_detail_is_still_stored_and_relayed(store, notify_mock):
    # A lone surrogate parses as JSON but cannot be UTF-8 encoded for the summary.
    raw = b'{"kind": "auth", "event": "lockout", "detail": {"username": "\\ud800"}}'
    res = _svc().post(AUDIT_EVENT, content=raw, headers={"Content-Type": "application/json"})
    assert res.status_code == 204
    (row,) = _audit_rows(store)
    assert row["operation_id"] == "auth_event:lockout" and row["args_summary"] == "[unprocessable]"
    assert notify_mock.call_args.kwargs["body"] == "MCP SECURITY [lockout] [unprocessable]"


def test_audit_event_relay_failure_still_stores_the_event(store, notify_mock):
    notify_mock.side_effect = RuntimeError("outbox down")
    res = _svc().post(AUDIT_EVENT, json={"kind": "auth", "event": "disabled", "detail": {}})
    assert res.status_code == 204
    assert [r["operation_id"] for r in _audit_rows(store)] == ["auth_event:disabled"]


@pytest.mark.parametrize("payload", [
    {"kind": "x", "event": "login_ok", "detail": {}},
    {"event": "login_ok", "detail": {}},
    {"kind": "auth", "event": "nope", "detail": {}},
    {"kind": "auth", "event": ["lockout"], "detail": {}},
    {"kind": "auth", "event": "lockout", "detail": []},
    {"kind": "auth", "event": "lockout", "detail": "x"},
    {"kind": "auth", "event": "lockout"},
])
def test_audit_event_rejects_bad_input(store, notify_mock, payload):
    res = _svc().post(AUDIT_EVENT, json=payload)
    assert res.status_code == 400
    assert store.run("McpAudit") == []
    notify_mock.assert_not_called()
