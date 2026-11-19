"""The MCP-only listener beside the public one (contract §2, plan Decision 15).

One process serves the app on two uvicorn servers. The public one (API_PORT)
is unchanged: Traefik-facing, proxy headers from FORWARDED_ALLOW_IPS, the
signal handlers and the app's startup and shutdown hooks. The MCP one
(MCP_API_PORT) runs only when MCP is usable and never on the public port. It
binds IPv4 only, because the gate checks the raw socket peer against
MCP_ALLOWED_CIDR and refuses an IPv4-mapped IPv6 peer. It never believes
X-Forwarded-For, runs with lifespan off so startup hooks run once, installs no
signal handlers, and stops when the public server does.
"""
from __future__ import annotations

import asyncio
import logging.config
import os
import signal
import sys
from unittest.mock import Mock

import pytest
import uvicorn
from uvicorn.lifespan.off import LifespanOff
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from api import server_config  # noqa: E402
from api.server_config import (  # noqa: E402
    _NoSignalServer,
    _serve_both,
    build_mcp_uvicorn_config,
    build_uvicorn_config,
    serve,
)
from mcp_helpers import MCP_TOKEN  # noqa: E402

MCP_ON = {"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": MCP_TOKEN}


async def tiny_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


@pytest.fixture(autouse=True)
def _no_logging_reconfiguration(monkeypatch):
    # Building a uvicorn.Config runs dictConfig, which would reconfigure
    # logging for every later test file; these listeners need none of it.
    monkeypatch.setattr(logging.config, "dictConfig", lambda *_a, **_k: None)
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)


@pytest.fixture
def log(monkeypatch):
    log = Mock()
    monkeypatch.setattr(server_config, "_log", log)
    return log


def _public(port=8011):
    return build_uvicorn_config(tiny_app, host="0.0.0.0", port=port, proxy_headers=True)


def _must_not_run(*_a, **_k):
    raise AssertionError("must not be called")


# --- the MCP listener's config -------------------------------------------------


def test_mcp_config_has_proxy_headers_off_and_lifespan_off():
    c = build_mcp_uvicorn_config(tiny_app, port=8012)
    assert (c.host, c.port, c.proxy_headers, c.lifespan) == ("0.0.0.0", 8012, False, "off")
    assert c.server_header is False


def test_mcp_config_serves_no_websockets():
    # The gate checks http scopes; with ws "none" no websocket scope reaches :8012,
    # so every request there is one the gate checked (contract §2).
    assert build_mcp_uvicorn_config(tiny_app, port=8012).ws == "none"


@pytest.mark.filterwarnings("ignore::DeprecationWarning")   # uvicorn's websockets import, on load()
def test_mcp_listener_never_rewrites_the_peer_and_runs_no_lifespan(monkeypatch):
    # Even with the MCP subnet named as a proxy, X-Forwarded-For is not believed:
    # the gate's CIDR check must see the raw socket peer.
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "172.31.250.0/24")
    c = build_mcp_uvicorn_config(tiny_app, port=8012)
    c.load()
    assert not isinstance(c.loaded_app, ProxyHeadersMiddleware)
    assert c.lifespan_class is LifespanOff
    assert b"server" not in dict(c.encoded_headers)


def test_no_signal_server_leaves_handlers_alone():
    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    with _NoSignalServer(build_mcp_uvicorn_config(tiny_app, port=8012)).capture_signals():
        assert {sig: signal.getsignal(sig) for sig in before} == before
    # A plain uvicorn server does take them, so the check above means something.
    with uvicorn.Server(_public()).capture_signals():
        assert signal.getsignal(signal.SIGINT) is not before[signal.SIGINT]
    assert signal.getsignal(signal.SIGINT) is before[signal.SIGINT]


# --- serve(): which listeners run ------------------------------------------------


def test_serve_runs_public_only_when_mcp_is_off(monkeypatch, log):
    ran = []
    monkeypatch.setattr(server_config, "run_server", ran.append)
    monkeypatch.setattr(server_config, "_serve_both", _must_not_run)
    public = _public()
    serve(tiny_app, public, environ={})
    assert ran == [public]
    assert log.info.call_args.args == ("MCP listener off",)
    log.error.assert_not_called()


@pytest.mark.parametrize("environ", [
    {"MCP_ENABLED": "true"},                                              # no token
    {"MCP_ENABLED": "true", "MCP_SERVICE_TOKEN": "t" * 31},              # token under 32 bytes
    {**MCP_ON, "MCP_ALLOWED_CIDR": "not-a-cidr"},                         # CIDR that does not parse
])
def test_serve_warns_when_mcp_is_enabled_but_unusable(monkeypatch, log, environ):
    ran = []
    monkeypatch.setattr(server_config, "run_server", ran.append)
    monkeypatch.setattr(server_config, "_serve_both", _must_not_run)
    public = _public()
    serve(tiny_app, public, environ=environ)
    assert ran == [public]
    message = log.warning.call_args.args[0]
    assert "MCP listener off" in message
    assert all(MCP_TOKEN not in str(c) for c in log.mock_calls)


def test_serve_refuses_mcp_on_the_public_port(monkeypatch, log):
    ran = []
    monkeypatch.setattr(server_config, "run_server", ran.append)
    monkeypatch.setattr(server_config, "_serve_both", _must_not_run)
    public = _public(port=8012)
    serve(tiny_app, public, environ={**MCP_ON, "MCP_API_PORT": "8012"})
    assert ran == [public]
    log.error.assert_called_once()
    message, *ports = log.error.call_args.args
    assert ports == [8012, 8012] and "MCP_API_PORT" in message and "API_PORT" in message
    assert "MCP listener off" in message and "public" in message


def test_serve_runs_both_when_enabled(monkeypatch, log):
    seen = {}

    async def both(public, mcp):
        seen.update(public=public, mcp=mcp)
        public.started = True

    monkeypatch.setattr(server_config, "_serve_both", both)
    monkeypatch.setattr(server_config, "run_server", _must_not_run)
    public = _public()
    assert serve(tiny_app, public, environ=MCP_ON) is None
    assert type(seen["public"]) is uvicorn.Server and seen["public"].config is public
    mcp = seen["mcp"]
    assert isinstance(mcp, _NoSignalServer)
    assert (mcp.config.app, mcp.config.host, mcp.config.port) == (tiny_app, "0.0.0.0", 8012)
    assert (mcp.config.proxy_headers, mcp.config.lifespan) == (False, "off")


def test_serve_uses_the_event_loop_uvicorn_would(monkeypatch):
    # uvicorn[standard] runs on uvloop; the public listener keeps that loop
    # when the MCP listener runs beside it.
    made = []

    def factory():
        made.append(asyncio.new_event_loop())
        return made[-1]

    async def both(public, mcp):
        assert asyncio.get_running_loop() is made[0]
        public.started = True

    monkeypatch.setattr(server_config, "_serve_both", both)
    public = _public()
    monkeypatch.setattr(public, "get_loop_factory", lambda: factory, raising=False)
    serve(tiny_app, public, environ=MCP_ON)
    assert len(made) == 1


def test_serve_exits_3_when_the_public_listener_never_started(monkeypatch):
    async def both(public, mcp):
        return None                                                       # never sets started

    monkeypatch.setattr(server_config, "_serve_both", both)
    with pytest.raises(SystemExit) as e:
        serve(tiny_app, _public(), environ=MCP_ON)
    assert e.value.code == 3


def test_serve_swallows_the_interrupt_after_a_graceful_shutdown(monkeypatch):
    # uvicorn re-raises a caught SIGINT once it has shut down; run_server
    # swallows it, and so does serve.
    async def both(public, mcp):
        public.started = True
        raise KeyboardInterrupt

    monkeypatch.setattr(server_config, "_serve_both", both)
    assert serve(tiny_app, _public(), environ=MCP_ON) is None


# --- _serve_both: the MCP listener lives as long as the public one ----------------


class _FakeMcp:
    def __init__(self, events, fail=None):
        self.events, self.fail, self.should_exit = events, fail, False

    async def serve(self):
        if self.fail is not None:
            raise self.fail
        self.events.append("mcp up")
        while not self.should_exit:
            await asyncio.sleep(0.005)
        self.events.append("mcp stopped")


class _FakePublic:
    def __init__(self, events, fail=None):
        self.events, self.fail = events, fail

    async def serve(self):
        await asyncio.sleep(0.05)                                         # the MCP listener is up meanwhile
        if self.fail is not None:
            raise self.fail
        self.events.append("public done")


def test_serve_both_stops_mcp_when_public_exits():
    events = []
    mcp = _FakeMcp(events)
    asyncio.run(_serve_both(_FakePublic(events), mcp))
    assert mcp.should_exit is True
    assert events == ["mcp up", "public done", "mcp stopped"]


def test_serve_both_stops_mcp_when_public_raises():
    events = []
    with pytest.raises(RuntimeError):
        asyncio.run(_serve_both(_FakePublic(events, fail=RuntimeError("boom")), _FakeMcp(events)))
    assert events == ["mcp up", "mcp stopped"]


@pytest.mark.parametrize("fail", [SystemExit(1), OSError("address in use")])
def test_an_mcp_listener_that_cannot_start_leaves_the_public_one_serving(log, fail):
    # uvicorn calls sys.exit(1) when it cannot bind its port. Uncaught in the
    # task, that would stop the event loop and take the public listener down.
    events = []
    asyncio.run(_serve_both(_FakePublic(events), _FakeMcp(events, fail=fail)))
    assert events == ["public done"]
    assert "MCP listener" in log.error.call_args.args[0]


# --- the entry point ----------------------------------------------------------------


def test_main_tail_calls_serve():
    src = open(os.path.join(BACKEND, "api", "main.py"), encoding="utf-8").read()
    tail = src[src.index('if __name__ == "__main__":'):]
    assert "serve(app, build_uvicorn_config(" in tail and "proxy_headers=True" in tail
    assert "run_server(" not in tail and "uvicorn.run(" not in tail
