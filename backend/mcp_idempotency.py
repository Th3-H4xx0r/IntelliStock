"""McpIdempotency: the Idempotency-Key records of MCP writes (contract §2/§3, spec §5.1).

Every write, money and irreversible call from the MCP principal carries an
Idempotency-Key. Its record is keyed by record_id(client, key), so two clients
never share a key. The gate claims the record before the call runs (claim:
one insert that only one request can win, state "in_progress") and finalizes
it with the completed response, whatever its status (finalize, state "done");
a call whose route raised releases it instead (release), so a retry can run.

lookup reads what a repeat of the key should get: "replay" (the stored
response), "conflict" (the key was used for a different request: method, path,
query or body), "in_progress" (the first call has not finished), or "absent".
A record older than TTL_S counts as absent, and the next claim clears it.
request_sha256 is the gate's canonical request hash (mcp_policy).

A response body over RESPONSE_CAP_BYTES is not stored: the record keeps its
status and too_large, and replays as that status with a fixed detail. Records
are written only here; retention (RETAIN_MCP_IDEMPOTENCY_DAYS, schema.py)
purges them after a day.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from db import P, store
from mcp_policy import iso_utc, parse_iso_utc, utc_now

IDEMPOTENCY_TABLE = "McpIdempotency"
# Contract §2. Match with fullmatch: `$` alone would accept a trailing newline.
KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
TTL_S = 86400
RESPONSE_CAP_BYTES = 1048576

LOOKUP_KINDS = ("absent", "replay", "conflict", "in_progress")


@dataclass
class IdemLookup:
    kind: str                # one of LOOKUP_KINDS
    record: dict | None


def record_id(client: str, key: str) -> str:
    """The record's id: sha256 hex of client, NUL, key."""
    return hashlib.sha256(f"{client}\x00{key}".encode("utf-8")).hexdigest()


def _expired(row: dict, now: datetime) -> bool:
    """Older than TTL_S. A stamp that cannot be read counts as live: a call is
    never re-run on a record that cannot be dated."""
    try:
        created_at = parse_iso_utc(row.get("created_at"))
    except (TypeError, ValueError):
        return False
    return now - created_at > timedelta(seconds=TTL_S)


def lookup(client: str, key: str, request_sha256: str, *, now: datetime | None = None) -> IdemLookup:
    """What a call with this key and request hash should get (see the module doc)."""
    row = store.get(IDEMPOTENCY_TABLE, record_id(client, key))
    if row is None or _expired(row, now or utc_now()):
        return IdemLookup("absent", None)
    if row.get("request_sha256") != request_sha256:
        return IdemLookup("conflict", row)
    if row.get("state") != "done":
        return IdemLookup("in_progress", row)
    return IdemLookup("replay", row)


def claim(client: str, key: str, *, method: str, path: str, request_sha256: str,
          now: datetime | None = None) -> bool:
    """Claim the key for one call; True only for the request whose insert won.

    An expired record is deleted first, and only while it still carries the
    stamp read here, so a claim made meanwhile by another request survives.
    """
    now = now or utc_now()
    rid = record_id(client, key)
    old = store.get(IDEMPOTENCY_TABLE, rid)
    if old is not None and _expired(old, now):
        store.delete(IDEMPOTENCY_TABLE, store.filter(
            IDEMPOTENCY_TABLE, P.field("id").eq(rid) & P.field("created_at").eq(old["created_at"])))
    result = store.insert(IDEMPOTENCY_TABLE, {
        "id": rid,
        "client": client,
        "key": key,
        "method": method,
        "path": path,
        "request_sha256": request_sha256,
        "state": "in_progress",
        "created_at": iso_utc(now),
    }, conflict="error")
    return result["inserted"] == 1


def finalize(client: str, key: str, *, status: int, content_type: str | None, body: bytes,
             now: datetime | None = None) -> None:
    """Store the call's completed response on its claimed record (state "done").

    The body is kept as UTF-8 text (undecodable bytes replaced); one over
    RESPONSE_CAP_BYTES is dropped and the record marked too_large.
    """
    too_large = len(body) > RESPONSE_CAP_BYTES
    store.update(IDEMPOTENCY_TABLE, record_id(client, key), {
        "state": "done",
        "status": status,
        "content_type": content_type,
        "body_text": None if too_large else body.decode("utf-8", errors="replace"),
        "too_large": too_large,
        "completed_at": iso_utc(now or utc_now()),
    })


def release(client: str, key: str) -> None:
    """Delete the key's record, so a retry can run."""
    store.delete(IDEMPOTENCY_TABLE, record_id(client, key))
