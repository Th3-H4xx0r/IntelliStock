"""MCP principal and the listener-scoped gate skeleton (plan Task 9, contract §2).

The api serves one app on two listeners. On the public one (:8011) the gate must
be invisible: the same scope, receive and send reach the app, and a human JWT
caller sees exactly what an app without the gate would show, the same principal
dict, the same 401s and the same X-Refreshed-Token. The one exception the
contract mandates is the service token, which gets the bad-JWT 401 there.

On the MCP listener (:8012) a request is the MCP principal only when the switch,
the token, the socket peer and the acting user all check out; anything else is a
401. get_current_user returns the principal the gate put in the scope, and
nothing a client sends can put it there.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import anyio
import jwt
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from api.mcp_gate import (
    PRINCIPAL_SCOPE_KEY, SERVICE_SCOPE_KEY, McpGateMiddleware, principal_from_scope,
    require_mcp_service, service_auth_failure,
)
from mcp_helpers import (
    MCP_TOKEN, PEER_IN, PEER_OUT, human_token, make_probe_app, mcp_client, mcp_env,
    public_client, seed_operator, use_store,
)
from mcp_policy import McpConfig


@pytest.fixture(autouse=True)
def _mcp(monkeypatch, store):
    use_store(monkeypatch, store)
    seed_operator(store)
    mcp_env(monkeypatch)


def test_public_listener_is_a_pure_passthrough():
    seen = {}
    async def inner(scope, receive, send): seen.update(scope=scope, receive=receive, send=send)
    mw = McpGateMiddleware(inner, config_loader=lambda: McpConfig.from_env({"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": MCP_TOKEN}))
    scope = {"type": "http", "method": "GET", "path": "/instances", "server": ("10.0.0.2", 8011),
             "client": (PEER_IN, 1), "headers": [(b"x-mcp-acting-user", b"u-op")]}
    recv, snd = AsyncMock(), AsyncMock()
    anyio.run(mw, scope, recv, snd)
    assert seen["scope"] is scope and seen["receive"] is recv and seen["send"] is snd
    assert PRINCIPAL_SCOPE_KEY not in scope and SERVICE_SCOPE_KEY not in scope


def test_service_token_on_public_listener_is_401_even_with_forged_headers():
    app, _ = make_probe_app()
    res = public_client(app).get("/probe/read", headers={"Authorization": f"Bearer {MCP_TOKEN}",
        "X-Forwarded-For": PEER_IN, "X-Forwarded-Port": "8012", "Host": "api:8012", "X-MCP-Acting-User": "u-op"})
    assert res.status_code == 401 and res.json() == {"detail": "Invalid or expired token"}


def test_service_token_on_public_listener_is_401_even_when_the_switch_is_off(monkeypatch):
    mcp_env(monkeypatch, MCP_ENABLED="false")
    app, calls = make_probe_app()
    res = public_client(app).get("/probe/read", headers={"Authorization": f"bearer {MCP_TOKEN}"})
    assert res.status_code == 401 and res.json() == {"detail": "Invalid or expired token"}
    assert calls["count"].get("probe_read", 0) == 0


@pytest.mark.parametrize("enabled", ["true", "false"])
def test_mcp_port_equal_to_the_public_port_leaves_every_request_public(monkeypatch, store, enabled):
    """MCP_API_PORT == API_PORT is a misconfiguration: serve() starts no MCP
    listener, and the gate must not gate the one listener there is, or every
    human would get a 401. Every request on that port takes the public path."""
    import interactive_utils
    from api import main
    mcp_env(monkeypatch, MCP_ENABLED=enabled, MCP_API_PORT="8011")
    monkeypatch.setenv("API_PORT", "8011")
    monkeypatch.setattr(interactive_utils, "ensure_instances_table", lambda conn=None: None)
    store.insert("Instances", {"id": "alpaca-main", "runCommand": True})
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        # From inside MCP_ALLOWED_CIDR: without the port rule the second call
        # would be a fully authenticated MCP call, and the first a 401.
        client = TestClient(main.app, base_url="http://api:8011", client=(PEER_IN, 50000))
        human = client.get("/instances", headers={"Authorization": f"Bearer {human_token(monkeypatch)}"})
        assert human.status_code == 200
        assert [row["id"] for row in human.json()["instances"]] == ["alpaca-main"]
        service = client.get("/instances", headers={"Authorization": f"Bearer {MCP_TOKEN}",
                                                     "X-MCP-Acting-User": "u-op"})
        assert service.status_code == 401 and service.json() == {"detail": "Invalid or expired token"}
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)
    assert list(store.run("McpAudit")) == []                      # the gate saw no MCP call


def test_mcp_headers_are_ignored_on_public_listener(monkeypatch):
    app, _ = make_probe_app()
    res = public_client(app).get("/whoami", headers={"Authorization": f"Bearer {human_token(monkeypatch)}",
                                                     "X-MCP-Acting-User": "u-op", "X-MCP-Client": "jarvis"})
    assert res.status_code == 200 and res.json() == {"id": "u-op", "username": "operator"}


def _ungated_twin() -> FastAPI:
    """The probe's /whoami on an app without the gate: what :8011 showed before it."""
    from api import main
    app = FastAPI()

    @app.get("/whoami")
    def whoami(user: dict = Depends(main.get_current_user)):
        return user
    return app


def _stale_token() -> str:
    """A valid token for u-op that is past half-life, so it renews."""
    import os
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": "u-op", "username": "operator", "token_version": 0,
                       "iat": now - timedelta(hours=20), "exp": now + timedelta(hours=4)},
                      os.environ["JWT_SECRET"], algorithm="HS256")


@pytest.mark.parametrize("case", ["no_header", "garbage", "unknown_user", "revoked", "fresh", "stale"])
def test_public_listener_matches_an_ungated_app(monkeypatch, case):
    """Same status, same body, same X-Refreshed-Token behaviour as without the gate."""
    import auth_utils
    fresh = human_token(monkeypatch)
    token = {"no_header": None, "garbage": "not-a-jwt", "fresh": fresh, "stale": _stale_token(),
             "unknown_user": auth_utils.create_access_token("ghost", "ghost", 0),
             "revoked": auth_utils.create_access_token("u-op", "operator", 1)}[case]
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    gated_app, _ = make_probe_app()
    gated = public_client(gated_app).get("/whoami", headers=headers)
    plain = TestClient(_ungated_twin(), base_url="http://api:8011").get("/whoami", headers=headers)
    assert (gated.status_code, gated.json()) == (plain.status_code, plain.json())
    assert gated.headers.get("www-authenticate") == plain.headers.get("www-authenticate")
    assert ("x-refreshed-token" in gated.headers) == ("x-refreshed-token" in plain.headers) == (case == "stale")
    if case == "stale":
        renewed = jwt.decode(gated.headers["x-refreshed-token"], options={"verify_signature": False})
        assert renewed["sub"] == "u-op" and renewed["token_version"] == 0
    if case in ("fresh", "stale"):
        assert gated.json() == {"id": "u-op", "username": "operator"}
    else:
        assert gated.status_code == 401


def test_main_app_runs_behind_the_gate():
    from api import main
    assert any(m.cls is McpGateMiddleware for m in main.app.user_middleware)
    # /health needs no auth, so only the gate can turn the service token away.
    res = public_client(main.app).get("/health", headers={"Authorization": f"Bearer {MCP_TOKEN}"})
    assert res.status_code == 401 and res.json() == {"detail": "Invalid or expired token"}


def test_principal_shape():
    app, _ = make_probe_app()
    assert mcp_client(app).get("/whoami").json() == {"id": "u-op", "username": "operator", "via": "mcp", "client": "jarvis"}
    assert mcp_client(app, client=None).get("/whoami").json()["client"] == "jarvis"


def test_principal_carries_the_client_header():
    app, calls = make_probe_app()
    assert mcp_client(app, client="jarvis-ios.2").get("/probe/read").status_code == 200
    assert calls["principal"] == {"id": "u-op", "username": "operator", "via": "mcp", "client": "jarvis-ios.2"}


@pytest.mark.parametrize("kwargs,env", [
    ({}, {"MCP_ENABLED": "false"}),                       # kill switch
    ({"peer": PEER_OUT}, {}),
    ({"token": "wrong" * 8}, {}), ({"token": None}, {}),
    ({"acting_user": "nobody"}, {}), ({"acting_user": None}, {}),
    ({"client": "bad client!"}, {}),
    ({}, {"MCP_SERVICE_TOKEN": "short"}),
])
def test_mcp_listener_refuses(monkeypatch, kwargs, env):
    for k, v in env.items(): monkeypatch.setenv(k, v)
    app, calls = make_probe_app()
    res = mcp_client(app, **kwargs).get("/probe/read")
    assert res.status_code == 401 and calls["count"].get("probe_read", 0) == 0


def test_mcp_refusal_body_and_challenge():
    app, _ = make_probe_app()
    res = mcp_client(app, peer=PEER_OUT).get("/probe/read")
    assert res.status_code == 401 and res.json() == {"detail": "mcp: unauthenticated"}
    assert res.headers["www-authenticate"] == "Bearer"


def test_two_authorization_headers_are_refused():
    app, calls = make_probe_app()
    c = mcp_client(app, token=None)
    res = c.get("/probe/read", headers=[("Authorization", f"Bearer {MCP_TOKEN}"),
                                        ("Authorization", f"Bearer {MCP_TOKEN}")])
    assert res.status_code == 401 and calls["count"].get("probe_read", 0) == 0


def test_human_jwt_is_refused_on_mcp_listener(monkeypatch):
    app, _ = make_probe_app()
    assert mcp_client(app, token=human_token(monkeypatch)).get("/whoami").status_code == 401


def test_never_and_unannotated_are_403():
    app, _ = make_probe_app()
    c = mcp_client(app)
    for res in (c.post("/probe/never", json={}), c.get("/probe/unannotated")):
        assert res.status_code == 403 and res.json() == {"detail": "mcp: operation not available"}


def test_get_current_user_human_path_unchanged(monkeypatch, store):
    from api import main
    token = human_token(monkeypatch)
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    plain = Request({"type": "http", "headers": []})
    a = main.get_current_user(response=Response(), credentials=creds, conn=None)
    b = main.get_current_user(response=Response(), credentials=creds, conn=None, request=plain)
    assert a == b == {"id": "u-op", "username": "operator"}


def test_get_current_user_ignores_a_scope_principal_not_via_mcp(monkeypatch):
    from api import main
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not-a-jwt")
    forged = Request({"type": "http", "headers": [],
                      PRINCIPAL_SCOPE_KEY: {"id": "u-op", "username": "operator", "via": "human"}})
    with pytest.raises(HTTPException) as ei:
        main.get_current_user(response=Response(), credentials=creds, conn=None, request=forged)
    assert ei.value.status_code == 401


def test_principal_from_scope_returns_a_copy_only_via_mcp():
    principal = {"id": "u-op", "username": "operator", "via": "mcp", "client": "jarvis"}
    scope = {PRINCIPAL_SCOPE_KEY: principal}
    got = principal_from_scope(scope)
    assert got == principal and got is not principal
    assert principal_from_scope({}) is None
    assert principal_from_scope({PRINCIPAL_SCOPE_KEY: {"id": "u-op", "via": "jwt"}}) is None
    assert principal_from_scope({PRINCIPAL_SCOPE_KEY: "u-op"}) is None


def _mcp_scope(**over) -> dict:
    scope = {"type": "http", "method": "GET", "path": "/probe/read", "query_string": b"",
             "server": ("api", 8012), "client": (PEER_IN, 40000),
             "headers": [(b"authorization", f"Bearer {MCP_TOKEN}".encode())]}
    scope.update(over)
    return scope


@pytest.mark.parametrize("env,over,expected", [
    ({"MCP_ENABLED": "false"}, {"client": (PEER_OUT, 1)}, "disabled"),
    ({"MCP_SERVICE_TOKEN": "short"}, {"client": (PEER_OUT, 1)}, "no_token"),
    ({"MCP_ALLOWED_CIDR": "not-a-cidr"}, {"client": (PEER_OUT, 1)}, "no_cidr"),
    ({}, {"client": (PEER_OUT, 1), "headers": []}, "peer"),
    ({}, {"client": None}, "peer"),
    ({}, {"client": ("not-an-ip", 1)}, "peer"),
    ({}, {"headers": []}, "token"),
    ({}, {"headers": [(b"authorization", f"bearer {MCP_TOKEN}".encode())]}, "token"),
    ({}, {"headers": [(b"authorization", MCP_TOKEN.encode())]}, "token"),
    ({}, {}, None),
])
def test_service_auth_failure_order(env, over, expected):
    cfg = McpConfig.from_env({"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": MCP_TOKEN, **env})
    assert service_auth_failure(_mcp_scope(**over), cfg) == expected


def test_service_level_route_needs_no_acting_user(store):
    seen = {}
    async def inner(scope, receive, send): seen["scope"] = scope
    mw = McpGateMiddleware(inner, config_loader=lambda: McpConfig.from_env({"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": MCP_TOKEN}))
    anyio.run(mw, _mcp_scope(path="/health"), AsyncMock(), AsyncMock())
    assert seen["scope"][SERVICE_SCOPE_KEY] is True and PRINCIPAL_SCOPE_KEY not in seen["scope"]
    assert store.run("McpAudit") == []


def test_service_level_route_still_needs_service_auth():
    inner = AsyncMock()
    mw = McpGateMiddleware(inner, config_loader=lambda: McpConfig.from_env({"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": MCP_TOKEN}))
    sent = []
    async def send(message): sent.append(message)
    anyio.run(mw, _mcp_scope(path="/health", client=(PEER_OUT, 1)), AsyncMock(), send)
    inner.assert_not_called()
    assert sent[0]["status"] == 401


def test_require_mcp_service():
    require_mcp_service(Request({"type": "http", "headers": [], SERVICE_SCOPE_KEY: True}))
    with pytest.raises(HTTPException) as ei:
        require_mcp_service(Request({"type": "http", "headers": []}))
    assert ei.value.status_code == 401 and ei.value.detail == "mcp: unauthenticated"


def test_audit_rows_for_calls_and_refusals(store):
    app, _ = make_probe_app()
    mcp_client(app).get("/probe/read"); mcp_client(app, peer=PEER_OUT).get("/probe/read")
    outcomes = sorted((r["outcome"], r["status"]) for r in store.run("McpAudit"))
    assert outcomes == [("ok", 200), ("unauthenticated", 401)]
    assert all("Bearer" not in json.dumps(r) and MCP_TOKEN not in json.dumps(r) for r in store.run("McpAudit"))


def test_audit_row_fields(store):
    app, _ = make_probe_app()
    mcp_client(app).get("/probe/read", params={"symbol": "TQQQ", "api_key": "AK-1"},
                        headers={"X-MCP-Correlation-Id": "corr-1"})
    mcp_client(app).post("/probe/never", json={})
    rows = {r["outcome"]: r for r in store.run("McpAudit")}
    ok = rows["ok"]
    assert (ok["operation_id"], ok["method"], ok["path"], ok["user_id"], ok["username"], ok["client"],
            ok["peer"], ok["correlation_id"]) == ("probe_read", "GET", "/probe/read", "u-op", "operator",
                                                  "jarvis", PEER_IN, "corr-1")
    assert isinstance(ok["latency_ms"], int) and ok["latency_ms"] >= 0
    assert "TQQQ" in ok["args_summary"] and "AK-1" not in ok["args_summary"]
    assert (rows["forbidden"]["status"], rows["forbidden"]["operation_id"]) == (403, "probe_never")


def test_public_listener_writes_no_audit(store, monkeypatch):
    app, _ = make_probe_app()
    public_client(app).get("/whoami", headers={"Authorization": f"Bearer {human_token(monkeypatch)}"})
    public_client(app).get("/whoami", headers={"Authorization": f"Bearer {MCP_TOKEN}"})
    assert store.run("McpAudit") == []
