"""McpAudit writer, secret redaction and the argument summary (contract §6).

One McpAudit row per MCP call, allowed or not. Secrets never reach a row or a
log line: the argument summary is built from a redacted copy of the query and
body (every value under a secret-named key becomes "[redacted]") before it is
cut, write_audit drops any Authorization key, and a failed write logs only the
exception's type, since a message can quote the row.

write_audit never raises: losing an audit row must not fail the call it records.
"""
from __future__ import annotations

import copy
import logging
import re
import uuid
from collections.abc import Mapping

from db import store
from mcp_policy import canonical_json, iso_utc, utc_now

_log = logging.getLogger("uvicorn.error")

AUDIT_TABLE = "McpAudit"
REDACTED = "[redacted]"

# Matched against the normalized name (see _normalize_key). "_private_key" covers
# kalshi_private_key, the PEM key in POST /brokerages; "_auth_key" covers
# secret_auth_key in POST /auth/signup.
SECRET_KEY_NAMES = frozenset({
    "api_key", "secret", "secret_key", "key", "token", "access_token", "refresh_token",
    "oauth_token", "password", "password_hash", "passphrase", "private_key", "privatekey",
    "dsn", "odds_api_key", "oddspapi_api_key",
})
SECRET_KEY_SUFFIXES = ("_api_key", "_secret", "_token", "_private_key", "_auth_key")

_CAMEL_ACRONYM = re.compile(r"([A-Z]+)([A-Z][a-z])")   # APIKey -> API_Key
_CAMEL_WORD = re.compile(r"([a-z0-9])([A-Z])")         # apiKey -> api_Key

OUTCOMES = frozenset({
    "ok", "error", "unauthenticated", "forbidden", "approval_required",
    "idempotency_required", "idempotency_conflict", "idempotent_replay", "capped",
    "too_large", "unsupported_media", "executed_approved", "replay_refused", "auth_event",
    "verify_password_ok", "verify_password_failed", "verify_password_limited",
})


class _InsertRejected(Exception):
    """The store answered errors > 0 instead of raising (ReQL partial-success)."""


def _normalize_key(name: str) -> str:
    """snake_case, lower: kalshiPrivateKey, kalshi-private-key and X-Api-Key become
    kalshi_private_key, kalshi_private_key and x_api_key."""
    snake = _CAMEL_WORD.sub(r"\1_\2", _CAMEL_ACRONYM.sub(r"\1_\2", name))
    return snake.replace("-", "_").lower()


def is_secret_key(name: str) -> bool:
    """True for a secret-named key: after normalizing case, camelCase and hyphens,
    an exact name in SECRET_KEY_NAMES or a suffix in SECRET_KEY_SUFFIXES."""
    normalized = _normalize_key(name)
    return normalized in SECRET_KEY_NAMES or normalized.endswith(SECRET_KEY_SUFFIXES)


def redact_secrets(value: object) -> object:
    """A deep copy of ``value`` with every dict value under a secret key replaced
    by "[redacted]", at any depth (a whole subtree under such a key included)."""
    if isinstance(value, Mapping):
        return {k: REDACTED if isinstance(k, str) and is_secret_key(k) else redact_secrets(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_secrets(v) for v in value)
    return copy.deepcopy(value)


def summarize_args(query: Mapping, body: object, limit: int = 500) -> str:
    """The canonical JSON of {"query", "body"} after redaction, cut to ``limit``
    characters with "…" appended when cut."""
    text = canonical_json({"query": redact_secrets(dict(query)),
                           "body": redact_secrets(body)}).decode("utf-8")
    return text if len(text) <= limit else text[:limit] + "…"


def write_audit(row: dict) -> None:
    """Insert one McpAudit row; never raises.

    Fills ``id`` (uuid4 hex) and ``ts`` when absent, refuses an outcome outside
    OUTCOMES and drops any key named authorization (in any case). The caller's
    dict is not modified.
    """
    try:
        doc = {k: v for k, v in row.items()
               if not (isinstance(k, str) and k.lower() == "authorization")}
        if doc.get("outcome") not in OUTCOMES:
            raise ValueError("unknown audit outcome")
        if not doc.get("id"):
            doc["id"] = uuid.uuid4().hex
        if not doc.get("ts"):
            doc["ts"] = iso_utc(utc_now())
        result = store.insert(AUDIT_TABLE, doc)
        if getattr(result, "errors", 0):
            raise _InsertRejected()
    except Exception as e:
        # The type only: a message (a store error included) can quote the row.
        _log.warning("mcp audit write failed: %s", type(e).__name__)
