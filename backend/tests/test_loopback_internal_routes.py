"""The chatbot-internal routes answer only the MCP server in the api container.

``/chatbot/internal/mcp-tools-list`` and ``/chatbot/internal/mcp-tool-call``
are driven by ``chatbot/intellistock_mcp_server.py``, which runs inside the api
container and calls back over loopback. They were reachable through Traefik,
guarded only by the MCP session token. Now ``_require_loopback`` runs first: the
peer must be loopback, and the request must carry no forwarding header. The
header check matters because uvicorn, behind a trusted proxy, rewrites the
client address from X-Forwarded-For, so a forged "127.0.0.1" from the proxy's
side would otherwise look like loopback.
"""
from __future__ import annotations

import logging.config
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from api import main  # noqa: E402
from api.server_config import build_uvicorn_config  # noqa: E402

HDR, BODY = {"X-IntelliStock-MCP-Token": "tok"}, {"conversation_id": "c1"}


class _Req:
    """Just what _require_loopback reads off a Starlette request."""

    def __init__(self, host, headers=None):
        self.client = None if host is None else SimpleNamespace(host=host)
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}


@pytest.fixture(autouse=True)
def _no_database():
    # The internal routes resolve conn_dependency before their body runs.
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        yield
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)


@pytest.fixture
def mcp_session(monkeypatch):
    """A live MCP session for token "tok"; returns every token looked up."""
    looked_up: list = []

    class _Manager:
        def lookup_by_token(self, token):
            looked_up.append(token)
            if token == "tok":
                return SimpleNamespace(conversation_id="c1", user_id="u1", mcp_token="tok")
            return None

    monkeypatch.setattr("chatbot.claude_cli_provider.get_session_manager", lambda: _Manager())
    monkeypatch.setattr("chatbot.tools.openai_tool_definitions", lambda: [])
    return looked_up


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_without_forwarding_headers_passes(host):
    main._require_loopback(_Req(host))


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
def test_any_forwarding_header_is_refused_even_from_loopback(header):
    with pytest.raises(HTTPException) as e:
        main._require_loopback(_Req("127.0.0.1", {header: "127.0.0.1"}))
    assert e.value.status_code == 403


@pytest.mark.parametrize("host", ["10.0.1.7", "testclient", "", None])
def test_non_loopback_peers_are_refused(host):
    with pytest.raises(HTTPException):
        main._require_loopback(_Req(host))


def test_tools_list_still_works_for_the_in_container_mcp_server(mcp_session):
    r = TestClient(main.app, client=("127.0.0.1", 50000)).post(
        "/chatbot/internal/mcp-tools-list", json=BODY, headers=HDR)
    assert r.status_code == 200 and r.json() == {"tools": []}


def test_tools_list_refuses_a_forwarded_request_before_reading_the_token(mcp_session):
    r = TestClient(main.app, client=("127.0.0.1", 50000)).post(
        "/chatbot/internal/mcp-tools-list", json=BODY, headers={**HDR, "X-Forwarded-For": "203.0.113.9"})
    assert r.status_code == 403 and mcp_session == []


def test_internal_routes_refuse_a_non_loopback_peer(mcp_session, monkeypatch):
    dispatched: list = []
    monkeypatch.setattr("chatbot.mcp_bridge.dispatch_mcp_tool_call", lambda **kw: dispatched.append(kw) or {})
    c = TestClient(main.app)                                   # peer "testclient"
    assert c.post("/chatbot/internal/mcp-tools-list", json=BODY, headers=HDR).status_code == 403
    assert c.post("/chatbot/internal/mcp-tool-call", json={**BODY, "tool_name": "get_status"},
                  headers=HDR).status_code == 403
    assert mcp_session == [] and dispatched == []


def test_forged_forwarded_for_through_a_trusted_proxy_cannot_reach_internal_routes(mcp_session, monkeypatch):
    # Building a uvicorn.Config runs dictConfig, which would reconfigure
    # logging for every later test file; the listener needs none of it.
    monkeypatch.setattr(logging.config, "dictConfig", lambda *_a, **_k: None)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1,10.0.0.0/8")
    cfg = build_uvicorn_config(main.app, host="127.0.0.1", port=8011, proxy_headers=True)
    cfg.load()
    r = TestClient(cfg.loaded_app, client=("10.0.1.7", 50000)).post(
        "/chatbot/internal/mcp-tools-list", json=BODY, headers={**HDR, "X-Forwarded-For": "127.0.0.1"})
    assert r.status_code == 403 and mcp_session == []          # uvicorn rewrote client to 127.0.0.1; the header check refuses


def test_llm_outputs_still_accepts_in_container_posts(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_LLM_OUTPUT_LOG_DIR", str(tmp_path))
    c = TestClient(main.app, client=("127.0.0.1", 50000))
    assert c.post("/llm/outputs", json={"raw_output": "x"}).status_code == 200
    assert c.post("/llm/outputs", json={"raw_output": "x"}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403


def test_the_chatbot_mcp_server_is_pointed_at_loopback(monkeypatch):   # pins the §7.2 PR's file
    from chatbot.claude_cli_provider import _mcp_callback_url
    monkeypatch.delenv("CLAUDE_CLI_MCP_CALLBACK_URL", raising=False)
    monkeypatch.setenv("API_PORT", "8011")
    assert _mcp_callback_url() == "http://127.0.0.1:8011"
