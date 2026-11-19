"""The API client for the MCP listener, driven through the fake API over httpx.MockTransport."""
import json
import uuid

import httpx
import pytest

from fake_api import SERVICE_TOKEN, FakeApi, schema_hash
from mcp_server import __version__
from mcp_server.api_client import (
    ApiClient,
    ApiUnavailable,
    PathParamError,
    ResponseTooLarge,
    build_path,
)
from mcp_server.config import API_MAX_RESPONSE_BYTES

# httpx adds these on its own; every other header on the wire is the client's choice.
TRANSPORT_HEADERS = {"host", "accept-encoding", "connection", "content-length", "content-type"}
MIB = 1024 * 1024


@pytest.fixture
def fake():
    return FakeApi()


@pytest.fixture
async def api(fake):
    client = fake.client()
    yield client
    await client.aclose()


def _sent_headers(request):
    return {key: value for key, value in request.headers.items() if key not in TRANSPORT_HEADERS}


def _raw_path(request):
    return request.url.raw_path.split(b"?")[0].decode("ascii")


def _connect_error(request):
    raise httpx.ConnectError("connection refused", request=request)


async def test_headers_on_every_request(fake, api):
    read = await api.request("GET", "/health", acting_user=None)
    write = await api.request("POST", "/instances", acting_user="u-123", json_body={"name": "x"},
                              idempotency_key="key-1")

    first, second = fake.requests
    base = {
        "authorization": "Bearer " + SERVICE_TOKEN,
        "x-mcp-client": "jarvis",
        "accept": "application/json",
        "user-agent": "intellistock-mcp/" + __version__,
    }
    read_cid = first.headers["x-mcp-correlation-id"]
    write_cid = second.headers["x-mcp-correlation-id"]
    assert _sent_headers(first) == {**base, "x-mcp-correlation-id": read_cid}
    assert _sent_headers(second) == {**base, "x-mcp-correlation-id": write_cid,
                                     "x-mcp-acting-user": "u-123", "idempotency-key": "key-1"}
    for cid in (read_cid, write_cid):
        assert str(uuid.UUID(cid)) == cid and uuid.UUID(cid).version == 4
    assert read_cid != write_cid
    assert (read.correlation_id, write.correlation_id) == (read_cid, write_cid)
    assert json.loads(second.content) == {"name": "x"}


async def test_idempotency_key_sent_and_validated(fake, api):
    for bad in ("k 1", "", "a" * 129, "k1\n", "k/1", "ключ", 7):
        with pytest.raises(ValueError):
            await api.request("POST", "/instances", acting_user="u-123", idempotency_key=bad)
    assert fake.requests == []

    for good in ("a" * 128, "Az09._:-"):
        await api.request("POST", "/instances", acting_user="u-123", idempotency_key=good)
    assert [request.headers["idempotency-key"] for request in fake.requests] == ["a" * 128, "Az09._:-"]


async def test_request_path_must_stay_on_the_api(fake, api):
    for bad in ("http://evil.example/x", "//evil.example/x", "instances", ""):
        with pytest.raises(ValueError):
            await api.request("GET", bad, acting_user=None)
    assert fake.requests == []


async def test_environment_proxies_ignored(monkeypatch):
    # httpx skips environment proxies whenever a transport is supplied, so the
    # fake cannot show this; inspect the production client (no transport, no I/O).
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    api = ApiClient("http://api:8012", SERVICE_TOKEN)
    try:
        assert api._client.trust_env is False
        assert api._client._mounts == {}  # no proxy transport mounted from the environment
        assert api._client.follow_redirects is False
    finally:
        await api.aclose()


async def test_no_redirect_follow(fake, api):
    fake.set_route("GET", "/instances", 307, headers={"Location": "http://evil.example/steal"})

    response = await api.request("GET", "/instances", acting_user="u-123")

    assert response.status == 307
    assert response.headers["location"] == "http://evil.example/steal"
    assert len(fake.requests) == 1


async def test_response_fields(fake, api):
    fake.set_route("GET", "/json", 200, {"a": [1, 2]}, headers={"X-MCP-Idempotent-Replay": "true"})
    fake.set_route("GET", "/text", 500, "Internal Server Error")
    fake.set_route("DELETE", "/empty", 204)

    as_json = await api.request("GET", "/json", acting_user="u-123")
    as_text = await api.request("GET", "/text", acting_user="u-123")
    empty = await api.request("DELETE", "/empty", acting_user="u-123", idempotency_key="k")

    assert (as_json.status, as_json.body, json.loads(as_json.text)) == (200, {"a": [1, 2]}, {"a": [1, 2]})
    assert as_json.headers["x-mcp-idempotent-replay"] == "true"
    assert all(key == key.lower() for key in as_json.headers)
    assert (as_text.status, as_text.body, as_text.text) == (500, None, "Internal Server Error")
    assert "Internal Server Error" not in repr(as_text)  # bodies stay out of reprs, and so out of logs
    assert (empty.status, empty.body, empty.text) == (204, None, "")


def test_build_path_encodes_and_rejects():
    assert build_path("/instances/{id}", {"id": "a/b"}) == "/instances/a%2Fb"
    assert build_path("/x/{id}", {"id": "%2e%2e"}) == "/x/%252e%252e"
    assert build_path("/x/{a}/y/{b}", {"a": 5, "b": "c d?e#f"}) == "/x/5/y/c%20d%3Fe%23f"
    assert build_path("/x/{id}", {"id": "..."}) == "/x/..."
    for bad in ("", ".", "..", True, False, None, 1.5, ["a"]):
        pytest.raises(PathParamError, build_path, "/x/{id}", {"id": bad})
    pytest.raises(PathParamError, build_path, "/x/{id}", {})
    assert issubclass(PathParamError, ValueError)


async def test_encoded_path_reaches_api_verbatim(fake, api):
    await api.request("GET", build_path("/instances/{id}", {"id": "a/b"}), acting_user="u-123")
    await api.request("GET", build_path("/x/{id}", {"id": "%2e%2e"}), acting_user="u-123")

    assert [_raw_path(request) for request in fake.requests] == ["/instances/a%2Fb", "/x/%252e%252e"]


async def test_oversized_response_aborted(fake, api):
    pulled = []

    async def endless():
        for _ in range(64):
            pulled.append(1)
            yield b"x" * MIB

    fake.set_route("GET", "/huge", 200, lambda request: httpx.Response(200, content=endless()))
    fake.set_route("GET", "/at-cap", 200, b"x" * API_MAX_RESPONSE_BYTES)

    with pytest.raises(ResponseTooLarge) as caught:
        await api.request("GET", "/huge", acting_user="u-123")

    assert isinstance(caught.value, ApiUnavailable)
    assert caught.value.correlation_id == fake.requests[0].headers["x-mcp-correlation-id"]
    assert API_MAX_RESPONSE_BYTES == 10 * MIB
    assert len(pulled) == 11  # stopped at the first chunk past the cap, not after all 64 MiB
    at_cap = await api.request("GET", "/at-cap", acting_user="u-123")
    assert len(at_cap.text) == API_MAX_RESPONSE_BYTES


async def test_transport_error_raises_unavailable_with_correlation_id(fake, api):
    def read_timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    fake.set_route("GET", "/down", 200, _connect_error)
    fake.set_route("GET", "/slow", 200, read_timeout)

    for path in ("/down", "/slow"):
        with pytest.raises(ApiUnavailable) as caught:
            await api.request("GET", path, acting_user="u-123")
        assert caught.value.correlation_id == fake.requests[-1].headers["x-mcp-correlation-id"]
        assert isinstance(caught.value.__cause__, httpx.TransportError)
        assert SERVICE_TOKEN not in str(caught.value)


async def test_verify_password_outcomes(fake, api):
    ok = await api.verify_password("operator", "correct horse")
    assert (ok.outcome, ok.user_id, ok.username, ok.retry_after_s) == ("ok", "u-123", "operator", None)
    request = fake.requests[0]
    assert (request.method, _raw_path(request)) == ("POST", "/internal/mcp/verify-password")
    assert json.loads(request.content) == {"username": "operator", "password": "correct horse"}
    assert "x-mcp-acting-user" not in request.headers
    assert b"correct" not in request.url.raw_path  # the password travels in the body only

    assert (await api.verify_password("operator", "wrong")).outcome == "invalid"
    assert (await api.verify_password("nobody", "correct horse")).outcome == "invalid"

    route = ("POST", "/internal/mcp/verify-password")
    fake.set_route(*route, 429, {"detail": "too many attempts", "retry_after_s": 30})
    throttled = await api.verify_password("operator", "correct horse")
    assert (throttled.outcome, throttled.retry_after_s, throttled.user_id) == ("throttled", 30, None)

    fake.set_route(*route, 429, {"detail": "too many attempts"}, headers={"Retry-After": "45"})
    assert (await api.verify_password("operator", "correct horse")).retry_after_s == 45

    for status, body in ((500, {"detail": "boom"}), (503, None), (200, {"user_id": 5}), (200, "ok")):
        fake.set_route(*route, status, body)
        assert (await api.verify_password("operator", "correct horse")).outcome == "unavailable"

    fake.set_route(*route, 200, _connect_error)
    assert (await api.verify_password("operator", "correct horse")).outcome == "unavailable"


async def test_post_audit_event_body_shape(fake, api):
    detail = {"username": "operator", "user_id": "u-123"}

    assert await api.post_audit_event("login_ok", detail) == 204

    assert fake.audit_events == [{"kind": "auth", "event": "login_ok", "detail": detail}]
    request = fake.requests[0]
    assert (request.method, _raw_path(request)) == ("POST", "/internal/mcp/audit-event")
    assert "x-mcp-acting-user" not in request.headers

    # The status goes back as is: the caller decides what is retried.
    for status in (503, 413, 401):
        fake.set_route("POST", "/internal/mcp/audit-event", status, {"detail": "no"})
        assert await api.post_audit_event("login_ok", detail) == status
    fake.set_route("POST", "/internal/mcp/audit-event", 204, _connect_error)
    assert await api.post_audit_event("login_ok", detail) is None
    assert len(fake.audit_events) == 1


async def test_health_schema_hash_none_on_401():
    paths = {"/instances": {"get": {"operationId": "list_instances", "x-mcp": {"class": "read"}}}}
    fake = FakeApi(openapi={"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": paths})
    good = fake.client()
    wrong_token = ApiClient("http://api:8012", "w" * 40, transport=fake.transport())
    try:
        assert await good.health_schema_hash() == schema_hash(paths)
        assert await wrong_token.health_schema_hash() is None
        assert fake.requests[-1].headers["authorization"] == "Bearer " + "w" * 40

        fake.set_route("GET", "/health", 200, {"status": "ok"})
        assert await good.health_schema_hash() is None
        fake.set_route("GET", "/health", 200, _connect_error)
        assert await good.health_schema_hash() is None
    finally:
        await good.aclose()
        await wrong_token.aclose()


async def test_get_openapi_returns_doc_or_raises(fake, api):
    paths = {"/x": {"get": {"operationId": "get_x", "x-mcp": {"class": "read"}}}}
    fake.set_openapi({"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": paths})

    doc = await api.get_openapi()

    assert doc["paths"] == paths
    assert doc["x-mcp-schema-hash"] == schema_hash(paths) == await api.health_schema_hash()
    assert _raw_path(fake.requests[0]) == "/internal/mcp/openapi.json"
    assert "x-mcp-acting-user" not in fake.requests[0].headers

    for status, body in ((503, {"detail": "starting"}), (200, ["not", "a", "document"])):
        fake.set_route("GET", "/internal/mcp/openapi.json", status, body)
        with pytest.raises(ApiUnavailable) as caught:
            await api.get_openapi()
        assert caught.value.correlation_id == fake.requests[-1].headers["x-mcp-correlation-id"]


async def test_fake_api_checks_service_headers_and_routes(fake):
    good = {"Authorization": "Bearer " + SERVICE_TOKEN, "X-MCP-Client": "jarvis",
            "X-MCP-Correlation-Id": str(uuid.uuid4())}
    variants = [
        {**good, "Authorization": "Bearer " + "w" * 40},
        {**good, "X-MCP-Client": "other"},
        {**good, "X-MCP-Correlation-Id": str(uuid.uuid1())},
        {**good, "X-MCP-Correlation-Id": "not-a-uuid"},
        {key: value for key, value in good.items() if key != "X-MCP-Correlation-Id"},
    ]
    async with httpx.AsyncClient(transport=fake.transport(), base_url="http://api:8012") as raw:
        assert (await raw.get("/health", headers=good)).status_code == 200
        for headers in variants:
            assert (await raw.get("/health", headers=headers)).status_code == 401
        missing = await raw.get("/nowhere", headers=good)
        bad_event = await raw.post("/internal/mcp/audit-event", headers=good,
                                   json={"kind": "auth", "event": "signing_key_rotated", "detail": {}})

    assert (missing.status_code, missing.json()) == (404, {"detail": "Not Found"})
    assert bad_event.status_code == 422 and fake.audit_events == []
    assert len(fake.requests) == 1 + len(variants) + 2


async def test_query_encoding(fake, api):
    await api.request("GET", "/q", acting_user="u-123", query={"a": True, "b": [1, 2], "c": None})
    await api.request("GET", "/q", acting_user="u-123", query={"d": False, "e": "x y&z", "f": 0})

    first, second = fake.requests
    assert first.url.query == b"a=true&b=1&b=2"
    assert second.url.params.multi_items() == [("d", "false"), ("e", "x y&z"), ("f", "0")]
