"""The assembled app: middleware, the bearer-protected /mcp, the unconfigured idle app, lifespan and housekeeping.

Every request goes through httpx2 over ASGITransport into the app build_app
returns, with its lifespan entered, so /mcp is the SDK's real stateless
Streamable HTTP endpoint behind the SDK's bearer middleware. The API is
FakeApi; tokens, sessions and the auth store are real.
"""
import asyncio
import contextlib
import json
import logging
from pathlib import Path

import anyio
import httpx2
import pytest
import uvicorn
from starlette.applications import Starlette

from fake_api import BASE_URL, SERVICE_TOKEN, FakeApi
import mcp_server.__main__ as entry
from mcp_server.app import build_app, build_deps, build_unconfigured_app, housekeeping
from mcp_server.config import SettingsError, load_settings
from mcp_server.crypto import hash_token

PUBLIC_BASE = "https://mcp.test:8443"
PRM_URL = PUBLIC_BASE + "/.well-known/oauth-protected-resource/mcp"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
TOOLS_CALL = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": "get_instance", "arguments": {"instance_id": "abc"}}}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
DISABLED = {"error": "disabled"}


def _sample():
    return json.loads((FIXTURES / "openapi_sample.json").read_text(encoding="utf-8"))


@pytest.fixture
def app_env(env):
    return {**env, "MCP_PUBLIC_BASE": PUBLIC_BASE, "MCP_RP_ID": "mcp.test", "MCP_API_BASE": BASE_URL,
            "MCP_SERVICE_TOKEN": SERVICE_TOKEN}


@pytest.fixture
def settings(app_env):
    return load_settings(app_env)


@pytest.fixture
def fake():
    return FakeApi(openapi=_sample())


async def _deps(settings, clock, fake):
    return build_deps(settings, clock=clock, api_transport=fake.transport())


@pytest.fixture
async def deps(settings, clock, fake):
    built = await _deps(settings, clock, fake)
    yield built
    await built.api.aclose()
    built.db.close()


@pytest.fixture
async def app(deps):
    """build_app's app with its lifespan entered: the SDK session manager and the background tasks run."""
    async with _serving(build_app(deps)) as built:
        yield built


@pytest.fixture
async def http(app):
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=PUBLIC_BASE) as client:
        yield client


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


def _token(deps, user_id="u-123"):
    """A real access token for a fresh session (refresh family) of user_id."""
    _, pair = deps.sessions.start(user_id=user_id, client_id=deps.settings.jarvis_client_id)
    return pair.access_token


def _bearer(token, **extra):
    return {**MCP_HEADERS, "Authorization": "Bearer " + token, **extra}


async def _until(predicate, timeout_s=5.0):
    with anyio.fail_after(timeout_s):
        while not predicate():
            await anyio.sleep(0.01)


def _api_calls(fake, path):
    return [request for request in fake.requests if request.url.path == path]


async def test_healthz_any_host(http):
    for host in (None, "evil.test", "127.0.0.1:8090", ""):
        headers = {} if host is None else {"Host": host}
        response = await http.get("/healthz", headers=headers)
        assert response.status_code == 200, host
        assert response.json() == {"ok": True}


async def test_unknown_host_421_and_hint_logged(http, caplog):
    with caplog.at_level(logging.WARNING, logger="mcp_server.app"):
        for host in ("evil.test", "evil.test", "mcp.test", "mcp.test:8443.evil.test"):
            for method, path in (("GET", "/.well-known/oauth-authorization-server"), ("POST", "/mcp"),
                                 ("GET", "/login"), ("GET", "/static/mcp-auth.js")):
                response = await http.request(method, path, headers={"Host": host})
                assert response.status_code == 421, (host, path)
    hints = [record.getMessage() for record in caplog.records if "MCP_EXTRA_ALLOWED_HOSTS" in record.getMessage()]
    # Logged once per distinct Host, naming it and the hosts that are allowed.
    assert hints == [
        "Rejected Host header 'evil.test'; allowed: mcp.test:8443. "
        "Set MCP_EXTRA_ALLOWED_HOSTS if tailscale serve rewrites Host.",
        "Rejected Host header 'mcp.test'; allowed: mcp.test:8443. "
        "Set MCP_EXTRA_ALLOWED_HOSTS if tailscale serve rewrites Host.",
        "Rejected Host header 'mcp.test:8443.evil.test'; allowed: mcp.test:8443. "
        "Set MCP_EXTRA_ALLOWED_HOSTS if tailscale serve rewrites Host.",
    ]
    assert (await http.get("/.well-known/oauth-authorization-server")).status_code == 200


async def test_extra_allowed_hosts_accepted(app_env, clock, fake):
    settings = load_settings({**app_env, "MCP_EXTRA_ALLOWED_HOSTS": "s7.tail.ts.net, mcp:8090"})
    deps = await _deps(settings, clock, fake)
    app = build_app(deps)
    try:
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=PUBLIC_BASE) as http:
                token = _token(deps)
                for host in ("mcp.test:8443", "s7.tail.ts.net", "mcp:8090"):
                    metadata = await http.get("/.well-known/oauth-authorization-server", headers={"Host": host})
                    assert metadata.status_code == 200, host
                    # The SDK's own Host check on /mcp accepts the extras too.
                    listed = await http.post("/mcp", json=TOOLS_LIST, headers=_bearer(token, Host=host))
                    assert listed.status_code == 200, (host, listed.text)
                    assert listed.json()["result"]["tools"]
                assert (await http.get("/healthz", headers={"Host": "mcp"})).status_code == 200
                assert (await http.get("/.well-known/oauth-authorization-server",
                                       headers={"Host": "mcp"})).status_code == 421
    finally:
        await deps.api.aclose()
        deps.db.close()


async def test_mcp_requires_bearer_and_points_to_prm(http, deps, fake):
    expected = ('Bearer error="invalid_token", error_description="Authentication required", '
                f'resource_metadata="{PRM_URL}"')
    for body in (TOOLS_LIST, TOOLS_CALL):
        for headers in (MCP_HEADERS, _bearer("not-a-token"), _bearer(""),
                        {**MCP_HEADERS, "Authorization": "Basic " + SERVICE_TOKEN}):
            response = await http.post("/mcp", json=body, headers=headers)
            assert response.status_code == 401, (body["method"], headers)
            assert response.headers["www-authenticate"] == expected
            assert response.json() == {"error": "invalid_token", "error_description": "Authentication required"}
    assert (await http.get("/mcp", headers={"Accept": "text/event-stream"})).status_code == 401
    assert _api_calls(fake, "/instances/abc") == []

    # The same calls with a real token get through.
    token = _token(deps)
    fake.set_route("GET", "/instances/abc", 200, {"id": "abc"})
    listed = await http.post("/mcp", json=TOOLS_LIST, headers=_bearer(token))
    assert listed.status_code == 200, listed.text
    assert "get_instance" in [tool["name"] for tool in listed.json()["result"]["tools"]]
    called = await http.post("/mcp", json=TOOLS_CALL, headers=_bearer(token))
    assert called.status_code == 200, called.text
    assert called.json()["result"]["isError"] is False
    assert [request.headers["x-mcp-acting-user"] for request in _api_calls(fake, "/instances/abc")] == ["u-123"]


async def test_prm_document_exact(http):
    response = await http.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    assert response.json() == {"resource": PUBLIC_BASE + "/mcp", "authorization_servers": [PUBLIC_BASE],
                               "scopes_supported": ["mcp"], "bearer_methods_supported": ["header"]}
    metadata = (await http.get("/.well-known/oauth-authorization-server")).json()
    assert metadata["issuer"] == PUBLIC_BASE
    assert (await http.post("/register", json={})).status_code in (404, 405)


async def test_session_header_is_not_authentication(http, deps):
    for session_id in ("abc", "0" * 32):
        response = await http.post("/mcp", json=TOOLS_LIST, headers={**MCP_HEADERS, "Mcp-Session-Id": session_id})
        assert response.status_code == 401
    # A real token whose session is revoked is no better than none.
    token = _token(deps)
    family_id = deps.sessions.list()[0].family_id
    assert (await http.post("/mcp", json=TOOLS_LIST, headers=_bearer(token))).status_code == 200
    deps.sessions.revoke(family_id, "admin")
    assert (await http.post("/mcp", json=TOOLS_LIST, headers=_bearer(token))).status_code == 401


async def test_bad_origin_on_mcp_rejected(http, deps):
    token = _token(deps)
    rejected = await http.post("/mcp", json=TOOLS_LIST, headers=_bearer(token, Origin="https://evil.test"))
    assert rejected.status_code == 403
    for origin in ("http://mcp.test:8443", "https://mcp.test", "null"):
        assert (await http.post("/mcp", json=TOOLS_LIST, headers=_bearer(token, Origin=origin))).status_code == 403
    allowed = await http.post("/mcp", json=TOOLS_LIST, headers=_bearer(token, Origin=PUBLIC_BASE))
    assert allowed.status_code == 200, allowed.text


async def test_disabled_returns_503(http, deps):
    token = _token(deps)
    gated = (("POST", "/mcp"), ("GET", "/mcp"), ("GET", "/authorize"), ("POST", "/token"), ("GET", "/login"),
             ("POST", "/login"), ("GET", "/login/second"), ("POST", "/login/passkey/options"), ("POST", "/login/totp"),
             ("GET", "/enroll"), ("POST", "/enroll/open"), ("POST", "/enroll/totp"))
    open_paths = ("/healthz", "/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource/mcp",
                  "/static/mcp-auth.js")
    deps.db.set_flag("disabled", True)
    # The Host check runs first: a foreign Host learns nothing, not even that the server is disabled.
    assert (await http.post("/mcp", headers={"Host": "evil.test"})).status_code == 421
    for method, path in gated:
        response = await http.request(method, path, headers=_bearer(token))
        assert response.status_code == 503, (method, path)
        assert response.json() == DISABLED
    for path in open_paths:
        assert (await http.get(path)).status_code == 200, path

    deps.db.set_flag("disabled", False)
    assert (await http.post("/mcp", json=TOOLS_LIST, headers=_bearer(token))).status_code == 200
    assert (await http.get("/authorize")).status_code != 503
    assert (await http.get("/login")).status_code != 503


async def test_unconfigured_app_503_and_healthz(env, caplog):
    secret = "short-secret-value"
    with pytest.raises(SettingsError) as raised:
        load_settings({**env, "MCP_PUBLIC_BASE": "", "MCP_SERVICE_TOKEN": secret, "MCP_TOTP_ENC_KEY": "!" + secret})
    errors = raised.value.errors
    assert len(errors) == 3
    app = build_unconfigured_app(errors)
    with caplog.at_level(logging.INFO, logger="mcp_server.app"):
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://mcp:8090") as http:
                healthz = await http.get("/healthz", headers={"Host": "anything"})
                assert (healthz.status_code, healthz.json()) == (200, {"ok": True})
                for method, path in (("POST", "/mcp"), ("GET", "/mcp"), ("GET", "/authorize"), ("POST", "/token"),
                                     ("GET", "/login"), ("GET", "/enroll"), ("GET", "/"),
                                     ("GET", "/.well-known/oauth-protected-resource/mcp"), ("DELETE", "/x/y")):
                    response = await http.request(method, path)
                    assert (response.status_code, response.json()) == (503, {"error": "not configured"}), path
    messages = [record.getMessage() for record in caplog.records]
    for error in errors:
        assert sum(error in message for message in messages) == 1, error
    assert not any(secret in message for message in messages)


async def test_lifespan_loads_catalog(deps, fake):
    deps.audit.record("enabled", source="admin_cli")
    app = build_app(deps)
    assert deps.registry.catalog is None
    async with app.router.lifespan_context(app):
        await _until(lambda: deps.registry.catalog is not None)
        assert "get_instance" in deps.registry.catalog.operations
        assert deps.watcher.current is deps.registry.catalog
        # The audit flush loop runs beside it.
        await _until(lambda: fake.audit_events)
        assert fake.audit_events[0]["event"] == "enabled"
    # On exit the background tasks stop and the API client is closed.
    with pytest.raises(RuntimeError):
        await deps.api.request("GET", "/health", acting_user=None)


async def test_lifespan_runs_housekeeping(deps, clock):
    _insert_enrollment(deps.db, "stale", expires_at=clock() - 86_400 - 1)
    app = build_app(deps)
    async with app.router.lifespan_context(app):
        await _until(lambda: _enrollment_names(deps.db) == [])


def _insert_enrollment(db, name, *, expires_at, completed_at=None):
    db.query("INSERT INTO enrollments (token_hash, username, created_at, expires_at, stage, completed_at) "
             "VALUES (?, ?, ?, ?, 'password', ?)", (hash_token("mcpen_" + name), name, expires_at - 600, expires_at,
                                                    completed_at))


def _enrollment_names(db):
    return [row["username"] for row in db.query("SELECT username FROM enrollments ORDER BY username")]


async def test_housekeeping_one_pass(deps, clock, caplog, monkeypatch):
    now = clock()
    _insert_enrollment(deps.db, "old-open", expires_at=now - 86_400 - 1)
    _insert_enrollment(deps.db, "old-done", expires_at=now - 2 * 86_400, completed_at=now - 2 * 86_400)
    _insert_enrollment(deps.db, "expired-today", expires_at=now - 3600)
    _insert_enrollment(deps.db, "live", expires_at=now + 600)

    calls = []

    def spy(name, fail=False):
        def purge():
            calls.append(name)
            if fail:
                raise RuntimeError("purge failed: secret=hunter2")
        return purge

    monkeypatch.setattr(deps.sessions, "purge", spy("sessions", fail=True))
    monkeypatch.setattr(deps.provider, "purge", spy("provider"))
    monkeypatch.setattr(deps.throttle, "purge", spy("throttle"))
    monkeypatch.setattr(deps.audit, "purge", spy("audit"))
    with caplog.at_level(logging.WARNING, logger="mcp_server.app"):
        await housekeeping(deps)
    # A failing step is logged by type only, and the rest of the pass still runs.
    assert calls == ["sessions", "provider", "throttle", "audit"]
    assert _enrollment_names(deps.db) == ["expired-today", "live"]
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in logged and "hunter2" not in logged


async def test_housekeeping_purges_for_real(deps, clock):
    """Each store's own purge runs: an expired login transaction and an old delivered audit row go."""
    deps.db.query("INSERT INTO auth_txns (txn_hash, client_id, redirect_uri, code_challenge, state, scopes, resource, "
                  "created_at, expires_at, stage, attempts) VALUES ('h', 'jarvis', 'r', 'c', 's', '[]', 'x', ?, ?, "
                  "'password', 0)", (clock() - 700, clock() - 100))
    deps.db.query("INSERT INTO auth_events (at, event, detail, delivered_at) VALUES (?, 'enabled', '{}', ?)",
                  (clock() - 91 * 86_400, clock() - 91 * 86_400))
    deps.db.query("INSERT INTO login_failures (username, kind, at) VALUES ('x', 'password', ?)",
                  (clock() - 2 * 86_400,))
    await housekeeping(deps)
    for table in ("auth_txns", "auth_events", "login_failures"):
        assert deps.db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0, table


def _events(db):
    rows = db.query("SELECT event, detail FROM auth_events ORDER BY id")
    return [(row["event"], json.loads(row["detail"])) for row in rows]


async def test_build_deps_wires_callbacks(deps, clock):
    deps.db.query("INSERT INTO users (user_id, username, enrolled_at, totp_secret_enc) VALUES (?, ?, ?, ?)",
                  ("u-123", "operator", clock(), b"sealed"))
    # A spent refresh token presented after its grace window revokes the family and is audited with the username.
    family_id, pair = deps.sessions.start(user_id="u-123", client_id="jarvis")
    assert deps.sessions.rotate(deps.sessions.lookup(pair.refresh_token)) is not None
    clock.advance(121)
    assert deps.sessions.lookup(pair.refresh_token) is None
    # Five failures lock the username; the lockout is audited.
    for _ in range(5):
        deps.throttle.record_failure(" Operator", "password")
    assert _events(deps.db) == [
        ("refresh_reuse_revoked", {"client_id": "jarvis", "session_id": family_id, "user_id": "u-123",
                                   "username": "operator"}),
        ("lockout", {"scope": "user", "seconds": 900, "username": "operator"}),
    ]
    # The verifier sees revocation and the kill switch.
    token = _token(deps)
    assert deps.verifier.verify(token).user_id == "u-123"
    deps.db.set_flag("disabled", True)
    assert deps.verifier.verify(token) is None
    deps.db.set_flag("disabled", False)
    deps.sessions.revoke_all("admin")
    assert deps.verifier.verify(token) is None


def _entry_env(monkeypatch, values):
    for name in [name for name in entry.os.environ if name.startswith("MCP_")]:
        monkeypatch.delenv(name)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def _fake_uvicorn(monkeypatch):
    runs = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **options: runs.append((app, options)))
    return runs


async def test_main_runs_uvicorn_with_fixed_options(app_env, monkeypatch):
    _entry_env(monkeypatch, {**app_env, "MCP_LOG_LEVEL": "warning"})
    runs = _fake_uvicorn(monkeypatch)
    built = []
    real_build_deps = entry.build_deps
    monkeypatch.setattr(entry, "build_deps", lambda settings: built.append(real_build_deps(settings)) or built[-1])
    entry.main()
    try:
        # httpx logs each request URL at INFO, tool paths and query arguments included.
        assert [logging.getLogger(name).level for name in ("httpx", "httpcore")] == [logging.WARNING] * 2
        ((app, options),) = runs
        assert options == {"host": "0.0.0.0", "port": 8090, "proxy_headers": False, "server_header": False,
                           "access_log": False, "log_level": "warning"}
        assert isinstance(app, Starlette)
        assert built[0].settings.public_base == PUBLIC_BASE
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=PUBLIC_BASE) as http:
            assert (await http.post("/mcp", json=TOOLS_LIST, headers=MCP_HEADERS)).status_code == 401
    finally:
        for deps in built:
            await deps.api.aclose()
            deps.db.close()


async def test_main_serves_the_unconfigured_app_on_bad_settings(app_env, monkeypatch):
    _entry_env(monkeypatch, {name: value for name, value in app_env.items() if name != "MCP_PUBLIC_BASE"})
    runs = _fake_uvicorn(monkeypatch)
    entry.main()
    ((app, options),) = runs
    assert (options["port"], options["access_log"], options["log_level"]) == (8090, False, "info")
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://mcp:8090") as http:
        assert (await http.get("/healthz")).json() == {"ok": True}
        assert (await http.post("/mcp")).json() == {"error": "not configured"}
