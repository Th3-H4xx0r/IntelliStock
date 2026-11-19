"""End to end: enrollment, Jarvis's real OAuth client, and tool calls over the real Streamable HTTP endpoint.

Jarvis is played by the SDK's own client: mcp.client.Client over
streamable_http_client, whose httpx2 client carries OAuthClientProvider. The
provider discovers the protected-resource and authorization-server metadata,
runs PKCE with the pre-registered client (its storage refuses dynamic
registration), and hands the authorization URL to a redirect handler that plays
the operator's browser through /authorize, /login and /login/second. Every
request reaches the app build_app returns, lifespan entered, over httpx2's
ASGITransport. The API is FakeApi; tokens, sessions, TOTP and passkeys (against
SoftAuthenticator) are all real.
"""
import asyncio
import contextlib
import json
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
import pyotp
import pytest
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

from fake_api import BASE_URL, SERVICE_TOKEN, FakeApi
from mcp_server.admin import create_enrollment
from mcp_server.app import build_app, build_deps
from mcp_server.config import load_settings
from mcp_server.oauth_provider import jarvis_client
from mcp_server.results import REDACTED
from mcp_server.tools import BUILTIN_TOOLS
from webauthn_fake import SoftAuthenticator

PUBLIC_BASE = "https://mcp.test:8443"
OTHER_BASE = "https://other.test:8443"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PASSWORD = "correct horse"
USER = "u-123"
KEY = "e2e-backtest-1"
BACKTEST_BODY = {"instance_id": "abc", "window": {"start": "2025-01-01", "end": "2025-06-30"}}
DONE = "Enrollment complete. You can close this page."
SECRET = "<code id=\"totp-secret\">"
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
TOOLS_CALL = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "get_instance", "arguments": {"instance_id": "abc"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _json_fixture(name):
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    data.pop("_source", None)
    return data


@pytest.fixture
def app_env(env):
    return {**env, "MCP_PUBLIC_BASE": PUBLIC_BASE, "MCP_RP_ID": "mcp.test", "MCP_API_BASE": BASE_URL,
            "MCP_SERVICE_TOKEN": SERVICE_TOKEN}


@pytest.fixture
def settings(app_env):
    return load_settings(app_env)


@pytest.fixture
def fake():
    return FakeApi(openapi=_json_fixture("openapi_sample.json"))


@pytest.fixture
async def deps(settings, clock, fake):
    built = build_deps(settings, clock=clock, api_transport=fake.transport())
    yield built
    await built.api.aclose()
    built.db.close()


@pytest.fixture
async def app(deps):
    async with _serving(build_app(deps)) as built:
        with anyio.fail_after(5):
            while deps.registry.catalog is None:
                await anyio.sleep(0.01)
        yield built


@contextlib.asynccontextmanager
async def _serving(app):
    """app's lifespan, held open in a task of its own until the block ends.

    anyio cancel scopes must be exited by the task that entered them, and
    pytest-asyncio may tear an async fixture down in a different task.
    """
    started, stop = asyncio.Event(), asyncio.Event()

    async def hold():
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    waiter = asyncio.create_task(started.wait())
    await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    if not started.is_set():
        waiter.cancel()
        task.result()  # raises what the lifespan raised on startup
    try:
        yield app
    finally:
        stop.set()
        await task


def _http(app, **kwargs):
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=PUBLIC_BASE, **kwargs)


def _origin():
    return {"Origin": PUBLIC_BASE}


def _query(url):
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


async def _enroll(app, deps, authenticator):
    """The operator enrolls from a CLI link through the /enroll pages: the TOTP secret the page showed."""
    link, _ = create_enrollment(deps.db, deps.settings, "operator", clock=deps.clock)
    async with _http(app) as browser:
        page = await browser.get(link)
        assert page.status_code == 200, page.text
        opened = await browser.post("/enroll/open", data={"t": _query(link)["t"]}, headers=_origin())
        assert (opened.status_code, opened.headers["location"]) == (303, "/enroll")
        password = await browser.post("/enroll/password", data={"password": PASSWORD}, headers=_origin())
        assert password.status_code == 303, password.text
        options = await browser.post("/enroll/passkey/options", json={}, headers=_origin())
        assert options.status_code == 200, options.text
        registered = await browser.post("/enroll/passkey", json={"credential": authenticator.create(options.text)},
                                        headers=_origin())
        assert registered.status_code == 200, registered.text
        totp_page = await browser.get("/enroll")
        start = totp_page.text.index(SECRET) + len(SECRET)
        secret = totp_page.text[start:totp_page.text.index("<", start)]
        done = await browser.post("/enroll/totp", data={"code": pyotp.TOTP(secret).at(deps.clock())},
                                  headers=_origin())
        assert done.status_code == 200 and DONE in done.text, done.text
    return secret


class _Storage:
    """Jarvis's token file: the pre-registered client, and every token pair the server issued."""

    def __init__(self, settings):
        self.client = jarvis_client(settings)
        self.issued = []

    async def get_tokens(self):
        return self.issued[-1] if self.issued else None

    async def set_tokens(self, tokens):
        self.issued.append(tokens)

    async def get_client_info(self):
        return self.client

    async def set_client_info(self, client_info):
        raise AssertionError("the client tried dynamic client registration")


class _Browser:
    """The operator's browser: follows the authorization URL to Jarvis's callback, finishing with passkey or TOTP."""

    def __init__(self, app, *, authenticator=None, totp=None):
        self.app = app
        self.authenticator = authenticator
        self.totp = totp  # a callable returning the current code
        self.visited = []
        self.result: Optional[AuthorizationCodeResult] = None

    async def redirect(self, authorization_url):
        self.visited.append(authorization_url)
        async with _http(self.app) as browser:
            authorize = await browser.get(authorization_url)
            assert authorize.status_code == 302, authorize.text
            login_url = authorize.headers["location"]
            txn = _query(login_url)["txn"]
            assert (await browser.get(login_url)).status_code == 200
            password = await browser.post("/login", data={"txn": txn, "username": "operator", "password": PASSWORD},
                                          headers=_origin())
            assert password.status_code == 303, password.text
            assert (await browser.get(password.headers["location"])).status_code == 200
            if self.authenticator is not None:
                options = await browser.post("/login/passkey/options", json={"txn": txn}, headers=_origin())
                assert options.status_code == 200, options.text
                done = await browser.post("/login/passkey", headers=_origin(),
                                          json={"txn": txn, "credential": self.authenticator.get(options.text)})
                assert done.status_code == 200, done.text
                callback = done.json()["redirect"]
            else:
                done = await browser.post("/login/totp", data={"txn": txn, "code": self.totp()}, headers=_origin())
                assert done.status_code == 303, done.text
                callback = done.headers["location"]
        query = _query(callback)
        self.result = AuthorizationCodeResult(code=query["code"], state=query["state"])

    async def callback(self):
        assert self.result is not None
        return self.result


def _jarvis(app, settings, storage, browser):
    """Jarvis's MCP client over the real Streamable HTTP endpoint, signing in through browser."""
    auth = OAuthClientProvider(
        server_url=settings.resource_url,
        client_metadata=OAuthClientMetadata(
            redirect_uris=[settings.jarvis_redirect_uri], token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"], response_types=["code"], scope="mcp",
            client_name="Jarvis"),
        storage=storage,
        redirect_handler=browser.redirect,
        callback_handler=browser.callback,
    )
    hc = _http(app, auth=auth)
    return hc, Client(streamable_http_client(settings.resource_url, http_client=hc))


def _payload(result):
    assert len(result.content) == 1 and result.content[0].type == "text"
    return json.loads(result.content[0].text)


def _api_calls(fake, path):
    return [request for request in fake.requests if request.url.path == path]


def _carries(request, secret):
    return secret in str(request.url) or secret in request.content.decode("utf-8", "replace") or any(
        secret in value for value in request.headers.values())


async def test_e2e_enroll_sign_in_and_use_tools(app, deps, settings, fake):
    authenticator = SoftAuthenticator(settings.rp_id, settings.public_origin)
    await _enroll(app, deps, authenticator)

    approval = _json_fixture("approval_executed.json")
    pending = {"pending_approval": {"id": "ap_1", "summary": "halt on alpaca-main \u2014 LIVE", "money_state": "live",
                                    "expires_at": "2026-11-19T15:17:11Z"}}
    fake.set_route("GET", "/instances/abc", 200, {"id": "abc", "name": "alpaca-paper"})
    fake.set_route("POST", "/backtests", 201, {"id": 812345, "status": "queued"})
    fake.set_route("POST", "/instances/abc/live-command", 202, pending)
    fake.set_route("GET", "/mcp/approvals/" + approval["id"], 200, approval)
    fake.set_route("GET", "/instances/broken", 500, {"detail": "Traceback: KeyError 'x'"})

    storage = _Storage(settings)
    browser = _Browser(app, authenticator=authenticator)
    hc, jarvis = _jarvis(app, settings, storage, browser)
    async with hc, jarvis as client:
        assert len(browser.visited) == 1  # one sign-in, through the authorization endpoint the metadata named
        authorize = urlsplit(browser.visited[0])
        assert (authorize.scheme + "://" + authorize.netloc + authorize.path) == PUBLIC_BASE + "/authorize"
        assert _query(browser.visited[0])["resource"] == settings.resource_url

        names = [tool.name for tool in (await client.list_tools()).tools]
        assert set(BUILTIN_TOOLS) | {"get_instance", "create_backtest", "live_command"} <= set(names)

        # get_instance: the acting user is the verified token's subject; the API sees only the service token.
        instance = await client.call_tool("get_instance", {"instance_id": "abc"})
        assert instance.is_error is False, instance.content
        assert _payload(instance)["data"] == {"id": "abc", "name": "alpaca-paper"}
        (seen,) = _api_calls(fake, "/instances/abc")
        assert seen.headers["authorization"] == "Bearer " + SERVICE_TOKEN
        assert seen.headers["x-mcp-acting-user"] == USER

        # A write without an idempotency key is refused before any request; with one it goes through.
        refused = await client.call_tool("create_backtest", {"body": BACKTEST_BODY})
        assert refused.is_error is True
        assert "idempotency_key" in _payload(refused)["error"]["message"]
        assert _api_calls(fake, "/backtests") == []
        created = await client.call_tool("create_backtest", {"body": BACKTEST_BODY, "idempotency_key": KEY})
        assert created.is_error is False, created.content
        assert [request.headers["idempotency-key"] for request in _api_calls(fake, "/backtests")] == [KEY]

        # A live-money command waits for the operator's approval: not an error.
        live = await client.call_tool("live_command", {"instance_id": "abc", "body": {"type": "halt"},
                                                       "idempotency_key": "e2e-halt-1"})
        assert live.is_error is False, live.content
        assert _payload(live)["pending_approval"] == pending["pending_approval"]

        # An approval's raw result is redacted on the way out.
        checked = await client.call_tool("get_approval", {"id": approval["id"]})
        assert checked.is_error is False
        assert REDACTED in checked.content[0].text
        assert approval["result"]["raw"]["access_token"] not in checked.content[0].text

        # A server error comes back as text naming the correlation id the API received.
        broken = await client.call_tool("get_instance", {"instance_id": "broken"})
        assert broken.is_error is True
        (failed,) = _api_calls(fake, "/instances/broken")
        assert failed.headers["x-mcp-correlation-id"] in broken.content[0].text
        assert "KeyError" not in broken.content[0].text

    # Jarvis's own tokens reached the MCP server only, never the API.
    assert storage.issued and storage.issued[0].refresh_token.startswith("mcprt_")
    for tokens in storage.issued:
        for request in fake.requests:
            assert not _carries(request, tokens.access_token)
            assert not _carries(request, tokens.refresh_token)
    events = [row["event"] for row in deps.db.query("SELECT event FROM auth_events ORDER BY id")]
    assert events[:2] == ["enrolled", "login_ok"]


async def test_e2e_totp_backup_sign_in(app, deps, settings, fake, clock):
    secret = await _enroll(app, deps, SoftAuthenticator(settings.rp_id, settings.public_origin))
    clock.advance(30)  # the enrollment spent the current step's code
    fake.set_route("GET", "/instances/abc", 200, {"id": "abc"})
    storage = _Storage(settings)
    browser = _Browser(app, totp=lambda: pyotp.TOTP(secret).at(clock()))
    hc, jarvis = _jarvis(app, settings, storage, browser)
    async with hc, jarvis as client:
        result = await client.call_tool("get_instance", {"instance_id": "abc"})
        assert result.is_error is False, result.content
    assert len(browser.visited) == 1
    assert [request.headers["x-mcp-acting-user"] for request in _api_calls(fake, "/instances/abc")] == [USER]
    login = deps.db.query_one("SELECT detail FROM auth_events WHERE event = 'login_ok'")
    assert json.loads(login["detail"])["method"] == "totp"


async def test_e2e_tools_list_and_call_need_a_valid_bearer(app, deps, fake):
    fake.set_route("GET", "/instances/abc", 200, {"id": "abc"})
    _, pair = deps.sessions.start(user_id=USER, client_id=deps.settings.jarvis_client_id)
    async with _http(app) as http:
        for body in (TOOLS_LIST, TOOLS_CALL):
            for token in (None, "garbage", pair.access_token + "x", pair.refresh_token):
                headers = dict(MCP_HEADERS) if token is None else {**MCP_HEADERS, "Authorization": "Bearer " + token}
                response = await http.post("/mcp", json=body, headers=headers)
                assert response.status_code == 401, (body["method"], token)
                assert "result" not in response.text
        assert _api_calls(fake, "/instances/abc") == []
        ok = await http.post("/mcp", json=TOOLS_CALL,
                             headers={**MCP_HEADERS, "Authorization": "Bearer " + pair.access_token})
        assert ok.status_code == 200 and ok.json()["result"]["isError"] is False, ok.text
        assert [request.headers["x-mcp-acting-user"] for request in _api_calls(fake, "/instances/abc")] == [USER]


async def test_e2e_token_for_other_audience_rejected(app, deps, app_env, clock, fake):
    """Same auth store and signing key, another public base: iss and aud differ, so /mcp refuses it."""
    other = build_deps(load_settings({**app_env, "MCP_PUBLIC_BASE": OTHER_BASE, "MCP_RP_ID": "other.test"}),
                       clock=clock, api_transport=fake.transport())
    try:
        _, foreign = other.sessions.start(user_id=USER, client_id=other.settings.jarvis_client_id)
        _, own = deps.sessions.start(user_id=USER, client_id=deps.settings.jarvis_client_id)
        assert deps.keyring.current()[0] == other.keyring.current()[0]  # one key signed both
    finally:
        await other.api.aclose()
        other.db.close()
    async with _http(app) as http:
        rejected = await http.post("/mcp", json=TOOLS_LIST,
                                   headers={**MCP_HEADERS, "Authorization": "Bearer " + foreign.access_token})
        assert rejected.status_code == 401
        accepted = await http.post("/mcp", json=TOOLS_LIST,
                                   headers={**MCP_HEADERS, "Authorization": "Bearer " + own.access_token})
        assert accepted.status_code == 200, accepted.text
