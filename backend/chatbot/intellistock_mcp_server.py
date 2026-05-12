"""IntelliStock MCP server — bridges Claude Code CLI tool calls back into
IntelliStock's chatbot tool registry.

CC spawns this script as a subprocess via ``--mcp-config``; it speaks MCP
(JSON-RPC 2.0 over stdio). When CC wants to call an IntelliStock tool the
server forwards the call to the IntelliStock backend over HTTP, where the
tool is dispatched against the same registry the other LLM providers use.

The script gets all the context it needs from environment variables that
the parent ``claude_cli_provider`` set at spawn time:

  * ``INTELLISTOCK_MCP_URL``       — IntelliStock backend base URL
                                     (e.g. ``http://api:8011``)
  * ``INTELLISTOCK_MCP_TOKEN``     — ephemeral per-session token
  * ``INTELLISTOCK_CONVERSATION_ID``
  * ``INTELLISTOCK_USER_ID``

Run standalone for sanity-checking:

  echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \\
      | INTELLISTOCK_MCP_URL=http://localhost:8011 \\
        INTELLISTOCK_MCP_TOKEN=xxx \\
        INTELLISTOCK_CONVERSATION_ID=test \\
        INTELLISTOCK_USER_ID=u1 \\
        python intellistock_mcp_server.py
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

import urllib.error
import urllib.request


# ── Module-level config (env-driven) ──────────────────────────────────────

_MCP_URL = (os.environ.get("INTELLISTOCK_MCP_URL") or "").rstrip("/")
_MCP_TOKEN = os.environ.get("INTELLISTOCK_MCP_TOKEN") or ""
_CONV_ID = os.environ.get("INTELLISTOCK_CONVERSATION_ID") or ""
_USER_ID = os.environ.get("INTELLISTOCK_USER_ID") or ""
# How long to block waiting for a non-safe tool to be confirmed by the
# user (or rejected). CC's own per-turn timeout caps the upper bound.
_CONFIRM_WAIT_TIMEOUT_SEC = int(os.environ.get("INTELLISTOCK_MCP_CONFIRM_WAIT_SEC", "120"))


SERVER_INFO = {
    "name": "intellistock",
    "version": "0.1.0",
}

PROTOCOL_VERSION = "2024-11-05"  # MCP protocol version we speak


# ── HTTP helpers ──────────────────────────────────────────────────────────


def _http_post(path: str, payload: Dict[str, Any], *, timeout: float = 30.0) -> Dict[str, Any]:
    if not _MCP_URL:
        raise RuntimeError("INTELLISTOCK_MCP_URL is not set")
    if not _MCP_TOKEN:
        raise RuntimeError("INTELLISTOCK_MCP_TOKEN is not set")
    url = f"{_MCP_URL}{path}"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-IntelliStock-MCP-Token": _MCP_TOKEN,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            raw = e.read().decode("utf-8")
            parsed = json.loads(raw)
            detail = parsed.get("detail") or raw
        except Exception:
            detail = str(e)
        raise RuntimeError(f"IntelliStock {path} HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"IntelliStock {path} unreachable: {e}") from e
    try:
        return json.loads(raw) if raw else {}
    except Exception as e:
        raise RuntimeError(f"IntelliStock {path} returned non-JSON: {raw[:200]!r}") from e


# ── MCP method handlers ───────────────────────────────────────────────────


def _handle_initialize(params: Dict[str, Any]) -> Dict[str, Any]:
    """Standard MCP handshake. Advertise tool support."""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "serverInfo": SERVER_INFO,
        "capabilities": {
            "tools": {},          # we offer tools
        },
    }


def _openai_to_mcp_tool(t: Dict[str, Any]) -> Dict[str, Any]:
    """Translate one OpenAI-style ``{type: function, function: {...}}``
    entry to the MCP ``{name, description, inputSchema}`` shape."""
    fn = t.get("function") or {}
    params = fn.get("parameters") or {"type": "object", "properties": {}}
    return {
        "name": fn.get("name") or "",
        "description": fn.get("description") or "",
        "inputSchema": params,
    }


def _handle_tools_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch the live tool catalog from IntelliStock and return MCP-shaped tools.

    We don't cache: the registry is fixed at process boot on the
    backend side, but tools may be safety-classified differently per
    conversation in the future. Refetching keeps that future-safe with
    negligible cost (the call is local).
    """
    payload = {"conversation_id": _CONV_ID, "user_id": _USER_ID}
    response = _http_post("/chatbot/internal/mcp-tools-list", payload, timeout=10.0)
    raw_tools: List[Dict[str, Any]] = response.get("tools") or []
    return {"tools": [_openai_to_mcp_tool(t) for t in raw_tools]}


def _handle_tools_call(params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute one tool call. Safe tools run synchronously; non-safe tools
    block until the user confirms via the IntelliStock UI (or until the
    confirm-wait timeout fires)."""
    name = (params.get("name") or "").strip()
    if not name:
        raise ValueError("tools/call missing 'name'")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("tools/call 'arguments' must be an object")
    payload = {
        "conversation_id": _CONV_ID,
        "user_id": _USER_ID,
        "tool_name": name,
        "arguments": arguments,
        "confirm_wait_timeout_sec": _CONFIRM_WAIT_TIMEOUT_SEC,
    }
    # Upper bound on the HTTP call is the confirm-wait + a small buffer.
    timeout = float(_CONFIRM_WAIT_TIMEOUT_SEC + 10)
    response = _http_post("/chatbot/internal/mcp-tool-call", payload, timeout=timeout)

    # The backend returns a dict with either:
    #   {ok: true, result: <serialisable payload>}
    #   {ok: false, error: "..."}              ← model should see this
    #   {ok: false, pending: true, ...}        ← user-declined or timeout
    #
    # MCP returns a ``content`` array of blocks plus optional ``isError``.
    is_error = not response.get("ok", False)
    if response.get("pending"):
        # Confirmation timed out OR was rejected — surface clearly so CC
        # can apologise and move on.
        msg = response.get("error") or "Tool execution requires user confirmation; the user did not confirm in time."
        return {
            "content": [{"type": "text", "text": msg}],
            "isError": True,
        }
    if is_error:
        msg = response.get("error") or "tool execution failed"
        return {
            "content": [{"type": "text", "text": msg}],
            "isError": True,
        }
    # Happy path: serialise the result back as text for CC. CC will
    # consume it as the tool result and continue its turn.
    raw_result = response.get("result")
    if isinstance(raw_result, (dict, list)):
        try:
            text = json.dumps(raw_result, ensure_ascii=False, default=str)
        except Exception:
            text = str(raw_result)
    else:
        text = str(raw_result) if raw_result is not None else ""
    return {
        "content": [{"type": "text", "text": text or "(empty result)"}],
        "isError": False,
    }


_METHODS = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
}


# ── JSON-RPC framing ──────────────────────────────────────────────────────


def _send(message: Dict[str, Any]) -> None:
    """Write one JSON-RPC message to stdout, framed as a single line.
    CC's MCP client expects line-delimited JSON over stdio (one object
    per line) for stdio transports."""
    line = json.dumps(message, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _result(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _dispatch(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC message. Returns the response message, or
    ``None`` for notifications (which MCP rarely uses)."""
    request_id = request.get("id")
    method = request.get("method") or ""
    params = request.get("params") or {}
    if not isinstance(params, dict):
        return _error(request_id, -32602, "params must be an object")
    handler = _METHODS.get(method)
    if handler is None:
        # MCP also has well-known no-op methods we just acknowledge.
        if method in ("notifications/initialized",):
            return None
        return _error(request_id, -32601, f"method not found: {method!r}")
    try:
        return _result(request_id, handler(params))
    except ValueError as e:
        return _error(request_id, -32602, str(e))
    except RuntimeError as e:
        return _error(request_id, -32000, str(e))
    except Exception as e:
        tb = traceback.format_exc()
        # Log to stderr so CC can surface it in --debug; do NOT leak the
        # traceback over the wire.
        print(f"[intellistock-mcp] uncaught: {tb}", file=sys.stderr, flush=True)
        return _error(request_id, -32603, f"internal error: {e}")


def main() -> None:
    """Read line-delimited JSON-RPC messages from stdin until EOF."""
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            _send({"jsonrpc": "2.0", "id": None, "error": {
                "code": -32700, "message": f"parse error: {e}",
            }})
            continue
        if not isinstance(request, dict):
            continue
        response = _dispatch(request)
        if response is not None:
            _send(response)


if __name__ == "__main__":
    main()
