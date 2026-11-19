"""The httpx client for the API's MCP listener (contract sections 2 and 4), and build_path.

Every request carries the service token, X-MCP-Client and a fresh uuid4
correlation id; the acting user and Idempotency-Key are added when given.
Redirects are never followed (a Location could point anywhere, and the service
token would follow it), proxies from the environment are ignored, and a body
past API_MAX_RESPONSE_BYTES is abandoned mid-stream. Transport failures surface
as ApiUnavailable carrying the correlation id, so a tool result can quote it.
"""
import json
import re
import types
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional
from urllib.parse import quote

import httpx

from mcp_server import __version__
from mcp_server.config import API_MAX_RESPONSE_BYTES, CLIENT_HEADER_VALUE

IDEMPOTENCY_KEY_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_PATH_PARAM = re.compile(r"\{([^{}/]+)\}")
_RESERVED_SEGMENTS = ("", ".", "..")

OPENAPI_PATH = "/internal/mcp/openapi.json"
HEALTH_PATH = "/health"
VERIFY_PASSWORD_PATH = "/internal/mcp/verify-password"
AUDIT_EVENT_PATH = "/internal/mcp/audit-event"


@dataclass(frozen=True)
class ApiResponse:
    status: int
    # Kept out of repr: API bodies can hold secrets, and a repr ends up in logs.
    body: Any = field(repr=False)  # parsed JSON, or None when the text is empty or not JSON
    text: str = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)  # lower-cased names, read-only
    correlation_id: str


@dataclass(frozen=True)
class PasswordCheck:
    outcome: Literal["ok", "invalid", "throttled", "unavailable"]
    user_id: Optional[str] = None
    username: Optional[str] = None
    retry_after_s: Optional[int] = None


class ApiUnavailable(Exception):
    """The API could not be reached or did not answer usably."""

    def __init__(self, message: str, correlation_id: str) -> None:
        super().__init__(message)
        self.correlation_id = correlation_id


class ResponseTooLarge(ApiUnavailable):
    """The response body passed API_MAX_RESPONSE_BYTES and was abandoned."""


class PathParamError(ValueError):
    """A path parameter is missing or would not stay one path segment."""


def build_path(template: str, values: Mapping[str, Any]) -> str:
    """Fill each {name} with its value percent-encoded as a single segment.

    httpx removes dot segments, so a raw "..", "." or "a/b" would reach a
    different route; those are rejected or encoded before any request is built.
    """
    def substitute(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in values:
            raise PathParamError(f"missing path parameter '{name}'")
        value = values[name]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise PathParamError(f"path parameter '{name}' must be a string or an integer")
        text = str(value)
        if text in _RESERVED_SEGMENTS:
            raise PathParamError(f"path parameter '{name}' must not be empty, '.' or '..'")
        return quote(text, safe="")

    return _PATH_PARAM.sub(substitute, template)


def _query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _query_items(query: Mapping[str, Any]) -> list[tuple[str, str]]:
    """bool -> "true"/"false", list -> one pair per item, None -> dropped."""
    items = []
    for key, value in query.items():
        for item in value if isinstance(value, (list, tuple)) else (value,):
            if item is not None:
                items.append((str(key), _query_value(item)))
    return items


def _parse_json(text: str) -> Any:
    """The parsed JSON, or None when the text is empty, not JSON, or nested too deeply for json.loads."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def _retry_after(response: ApiResponse) -> Optional[int]:
    """retry_after_s from the body, else a delta-seconds Retry-After header."""
    body = response.body
    if isinstance(body, dict):
        value = body.get("retry_after_s")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    header = response.headers.get("retry-after", "").strip()
    return int(header) if header.isdigit() else None


class ApiClient:
    def __init__(self, base_url: str, service_token: str, *, timeout_s: float = 60.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": "Bearer " + service_token,
                "X-MCP-Client": CLIENT_HEADER_VALUE,
                "Accept": "application/json",
                "User-Agent": "intellistock-mcp/" + __version__,
            },
            timeout=httpx.Timeout(timeout_s),
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    async def request(self, method: str, path: str, *, acting_user: Optional[str],
                      query: Optional[Mapping[str, Any]] = None, json_body: Any = None,
                      idempotency_key: Optional[str] = None) -> ApiResponse:
        """One call to the API; ValueError on bad input before any I/O, ApiUnavailable on failure."""
        if idempotency_key is not None and not (
                isinstance(idempotency_key, str) and IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key)):
            raise ValueError("Idempotency-Key must be 1-128 characters from A-Z a-z 0-9 . _ : -")
        if not path.startswith("/") or path.startswith("//"):
            # An absolute or scheme-relative URL would take the service token off the API.
            raise ValueError("path must be an absolute path on the API, starting with a single '/'")
        correlation_id = str(uuid.uuid4())
        headers = {"X-MCP-Correlation-Id": correlation_id}
        if acting_user is not None:
            headers["X-MCP-Acting-User"] = acting_user
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        extra = {} if json_body is None else {"json": json_body}
        params = None if query is None else _query_items(query)
        try:
            async with self._client.stream(method, path, params=params, headers=headers, **extra) as resp:
                content = bytearray()
                async for chunk in resp.aiter_bytes():
                    content += chunk
                    if len(content) > API_MAX_RESPONSE_BYTES:
                        raise ResponseTooLarge(
                            f"IntelliStock API response exceeded {API_MAX_RESPONSE_BYTES} bytes", correlation_id)
                text = bytes(content).decode(resp.encoding or "utf-8", errors="replace")
                return ApiResponse(
                    status=resp.status_code,
                    body=_parse_json(text),
                    text=text,
                    headers=types.MappingProxyType({k.lower(): v for k, v in resp.headers.items()}),
                    correlation_id=correlation_id,
                )
        except httpx.RequestError as exc:  # TransportError, plus a body that fails to decode
            raise ApiUnavailable(
                f"IntelliStock API unreachable ({type(exc).__name__})", correlation_id) from exc

    async def get_openapi(self) -> dict:
        """The MCP-visible OpenAPI document; ApiUnavailable unless the API answers 200 with an object."""
        resp = await self.request("GET", OPENAPI_PATH, acting_user=None)
        if resp.status != 200 or not isinstance(resp.body, dict):
            raise ApiUnavailable(f"IntelliStock API schema fetch returned {resp.status}", resp.correlation_id)
        return resp.body

    async def health_schema_hash(self) -> Optional[str]:
        """The schema hash /health reports, or None when it cannot be had."""
        try:
            resp = await self.request("GET", HEALTH_PATH, acting_user=None)
        except ApiUnavailable:
            return None
        value = resp.body.get("mcp_schema_hash") if resp.status == 200 and isinstance(resp.body, dict) else None
        return value if isinstance(value, str) else None

    async def verify_password(self, username: str, password: str) -> PasswordCheck:
        try:
            resp = await self.request("POST", VERIFY_PASSWORD_PATH, acting_user=None,
                                      json_body={"username": username, "password": password})
        except ApiUnavailable:
            return PasswordCheck("unavailable")
        if resp.status == 200 and isinstance(resp.body, dict):
            user_id, name = resp.body.get("user_id"), resp.body.get("username")
            if isinstance(user_id, str) and isinstance(name, str):
                return PasswordCheck("ok", user_id=user_id, username=name)
        elif resp.status == 401:
            return PasswordCheck("invalid")
        elif resp.status == 429:
            return PasswordCheck("throttled", retry_after_s=_retry_after(resp))
        return PasswordCheck("unavailable")

    async def post_audit_event(self, event: str, detail: Mapping[str, Any]) -> Optional[int]:
        """The HTTP status the API answered, or None when it could not be reached.

        What to retry is the caller's decision (AuditSink.flush): a 5xx is not a
        rejection, and some 4xx answers reject every event alike.
        """
        try:
            resp = await self.request("POST", AUDIT_EVENT_PATH, acting_user=None,
                                      json_body={"kind": "auth", "event": event, "detail": dict(detail)})
        except ApiUnavailable:
            return None
        return resp.status

    async def aclose(self) -> None:
        await self._client.aclose()
