"""The in-process replay of an approved MCP call (plan Task 13, contract §5).

POST /mcp/approvals/{id}/decision approves a held call and then runs it here,
once: the stored method, path, query and body go back into the app as a fresh
ASGI request, never over a socket. The scope carries REPLAY_SCOPE_KEY, which
only in-process code can set (no header or request field reaches a scope key),
so McpGateMiddleware takes its replay branch: it starts the approval's one
execution (a second replay of the same approval is a 403), puts the approving
user's MCP principal in the scope and runs the route, with no idempotency and
no caps.

The scope fails closed everywhere else. The client and server are "mcp-replay",
never loopback, so nothing that trusts a local peer trusts a replay. The bearer
is a dummy that only satisfies HTTPBearer's presence check; it is never a valid
JWT, so a route that does not take the principal from the scope gets a 401.

Never import api.main here: the app arrives as an argument (request.app).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import quote, urlencode

import anyio
from starlette.types import ASGIApp, Message

from api.mcp_gate import REPLAY_SCOPE_KEY

_log = logging.getLogger("uvicorn.error")

RESULT_CAP_BYTES = 262144
REPLAY_HOST = "mcp-replay"
_DUMMY_BEARER = b"Bearer mcp-replay"


@dataclass
class ReplayResult:
    status: int
    body: object


def _request_body(body: object) -> bytes:
    """The stored body as JSON bytes; empty for a call that had no body."""
    if body is None:
        return b""
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _reject_constant(name: str) -> object:
    raise ValueError(f"{name} is not JSON")


def _result_body(raw: bytes) -> object:
    """The response body parsed as JSON, else {"text": ...}; past RESULT_CAP_BYTES
    serialized, {"truncated": True, "partial": <the first RESULT_CAP_BYTES characters>}."""
    text = raw.decode("utf-8", errors="replace")
    try:
        body = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        body = {"text": text}
    if len(json.dumps(body, ensure_ascii=False).encode("utf-8")) > RESULT_CAP_BYTES:
        return {"truncated": True, "partial": text[:RESULT_CAP_BYTES]}
    return body


async def replay_approved_request(app: ASGIApp, approval: dict, principal: dict) -> ReplayResult:
    """Run the approval's stored call through the app in-process; its status and body.

    An exception from the app is caught and logged: the status is the one the
    app had sent, else 500.
    """
    path = approval["path"]
    body = _request_body(approval.get("body"))
    headers = [
        (b"host", REPLAY_HOST.encode("ascii")),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"authorization", _DUMMY_BEARER),
    ]
    if principal.get("client"):
        headers.append((b"x-mcp-client", str(principal["client"]).encode("latin-1")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": str(approval["method"]).upper(),
        "scheme": "http",
        "path": path,
        "raw_path": quote(path).encode("ascii"),
        "root_path": "",
        "query_string": urlencode(approval.get("query") or {}, doseq=True).encode("ascii"),
        "headers": headers,
        "client": (REPLAY_HOST, 0),
        "server": (REPLAY_HOST, 0),
        "state": {},
        REPLAY_SCOPE_KEY: {"approval_id": approval["id"], "principal": dict(principal)},
    }

    response_done = anyio.Event()
    body_delivered = False
    status: int | None = None
    chunks: list[bytes] = []

    async def receive() -> Message:
        # The body once; after that, the disconnect only once the response is
        # complete, so the app never mistakes a replay for a client that left.
        nonlocal body_delivered
        if not body_delivered:
            body_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await response_done.wait()
        return {"type": "http.disconnect"}

    async def send(message: Message) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                response_done.set()

    try:
        await app(scope, receive, send)
    except Exception:
        # Nobody above us logs it: the decision route turns it into a failed
        # approval. A traceback, like uvicorn's for any other route exception.
        _log.error("mcp: replay of approval %s raised", approval.get("id"), exc_info=True)
    finally:
        response_done.set()
    return ReplayResult(status=500 if status is None else status, body=_result_body(b"".join(chunks)))
