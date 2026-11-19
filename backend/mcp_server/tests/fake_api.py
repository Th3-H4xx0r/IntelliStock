"""An in-process stand-in for the API's MCP listener (contract sections 2 and 4), over httpx.MockTransport.

FakeApi checks the service headers the real listener checks, serves the internal
endpoints the MCP server uses, and lets a test override any route. Every request
it receives, accepted or not, is appended to .requests.
"""
import copy
import hashlib
import json
import uuid
from typing import Any, Callable, Mapping, Optional, Union

import httpx

from mcp_server.api_client import ApiClient

SERVICE_TOKEN = "s" * 40
BASE_URL = "http://api:8012"
DEFAULT_USERS = {"operator": ("correct horse", "u-123")}  # username -> (password, user id)
AUDIT_EVENTS = frozenset({"login_ok", "login_failed", "passkey_failed", "totp_failed", "lockout",
                          "refresh_reuse_revoked", "session_revoked", "enrolled", "disabled", "enabled"})
DEFAULT_OPENAPI = {"openapi": "3.1.0", "info": {"title": "IntelliStock API", "version": "test"}, "paths": {}}
# The gate's own refusal on :8012 (contract section 2): MCP_ENABLED unset, a service token mismatch, or a
# peer outside MCP_ALLOWED_CIDR. verify-password's wrong password is a different 401, "invalid credentials".
UNAUTHENTICATED = {"detail": "mcp: unauthenticated"}

OPENAPI_PATH = "/internal/mcp/openapi.json"
VERIFY_PASSWORD_PATH = "/internal/mcp/verify-password"
AUDIT_EVENT_PATH = "/internal/mcp/audit-event"
HEALTH_PATH = "/health"

Handler = Callable[[httpx.Request], httpx.Response]


def schema_hash(paths: Any, components: Any = None) -> str:
    """The contract's x-mcp-schema-hash of an OpenAPI document's "paths" and "components" objects."""
    canonical = json.dumps({"paths": paths, "components": components or {}}, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_uuid4(value: Optional[str]) -> bool:
    try:
        parsed = uuid.UUID(value or "")
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value


def _json_body(request: httpx.Request) -> Any:
    try:
        return json.loads(request.content)
    except ValueError:
        return None


def _response(status: int, body: Any = None, headers: Optional[Mapping[str, str]] = None) -> httpx.Response:
    """bytes go out as is, str as text/plain, None as an empty body, anything else as JSON."""
    if body is None:
        return httpx.Response(status, headers=headers)
    if isinstance(body, bytes):
        return httpx.Response(status, content=body, headers=headers)
    if isinstance(body, str):
        return httpx.Response(status, text=body, headers=headers)
    return httpx.Response(status, json=body, headers=headers)


class FakeApi:
    def __init__(self, openapi: Optional[dict] = None,
                 users: Optional[Mapping[str, tuple[str, str]]] = None) -> None:
        self.requests: list[httpx.Request] = []
        self.audit_events: list[dict] = []
        self.users = dict(DEFAULT_USERS if users is None else users)
        # False is the api with MCP_ENABLED unset: the gate refuses every request, the right token included.
        self.service_enabled = True
        self._routes: dict[tuple[str, str], Handler] = {}
        self._openapi: dict = {}
        self.set_openapi(DEFAULT_OPENAPI if openapi is None else openapi)

    def set_openapi(self, doc: dict) -> None:
        """Serve doc, adding x-mcp-schema-hash unless doc already carries one (to plant a mismatch)."""
        served = copy.deepcopy(doc)
        served.setdefault("x-mcp-schema-hash", schema_hash(served.get("paths", {}), served.get("components")))
        self._openapi = served

    def set_route(self, method: str, path: str, status: int,
                  body: Union[Any, Handler] = None, headers: Optional[Mapping[str, str]] = None) -> None:
        """Answer method + path (the encoded path, no query) with status/body/headers.

        A callable body is the whole handler: it gets the request and returns the
        response (status and headers are then unused), or raises, as a transport would.
        Routes set here take precedence over the built-in ones.
        """
        if callable(body):
            handler = body
        else:
            def handler(request: httpx.Request) -> httpx.Response:
                return _response(status, body, headers)
        self._routes[(method.upper(), path)] = handler

    def clear_route(self, method: str, path: str) -> None:
        """Drop a set_route override, so the built-in route answers again."""
        self._routes.pop((method.upper(), path), None)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self) -> ApiClient:
        return ApiClient(BASE_URL, SERVICE_TOKEN, transport=self.transport())

    def _authorized(self, request: httpx.Request) -> bool:
        return (self.service_enabled
                and request.headers.get("authorization") == "Bearer " + SERVICE_TOKEN
                and request.headers.get("x-mcp-client") == "jarvis"
                and _is_uuid4(request.headers.get("x-mcp-correlation-id")))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._authorized(request):
            return _response(401, UNAUTHENTICATED)
        key = (request.method, request.url.raw_path.split(b"?")[0].decode("ascii"))
        if key in self._routes:
            return self._routes[key](request)
        if key == ("GET", OPENAPI_PATH):
            return _response(200, self._openapi)
        if key == ("GET", HEALTH_PATH):
            return _response(200, {"status": "ok", "mcp_schema_hash": self._openapi["x-mcp-schema-hash"]})
        if key == ("POST", VERIFY_PASSWORD_PATH):
            return self._verify_password(_json_body(request))
        if key == ("POST", AUDIT_EVENT_PATH):
            return self._audit_event(_json_body(request))
        return _response(404, {"detail": "Not Found"})

    def _verify_password(self, body: Any) -> httpx.Response:
        if not (isinstance(body, dict) and isinstance(body.get("username"), str)
                and isinstance(body.get("password"), str)):
            return _response(422, {"detail": "username and password are required strings"})
        known = self.users.get(body["username"])
        if known is None or known[0] != body["password"]:
            return _response(401, {"detail": "invalid credentials"})
        return _response(200, {"user_id": known[1], "username": body["username"]})

    def _audit_event(self, body: Any) -> httpx.Response:
        if not (isinstance(body, dict) and body.get("kind") == "auth" and body.get("event") in AUDIT_EVENTS
                and isinstance(body.get("detail"), dict)):
            return _response(422, {"detail": "invalid audit event"})
        self.audit_events.append(body)
        return _response(204)
