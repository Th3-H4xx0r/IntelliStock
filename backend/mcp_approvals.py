"""MCP approvals: the held-call records, their summaries, the push and the reads (contract §5).

The gate holds a live-money, unknown, protected or irreversible MCP call as one
McpApprovals row (create_approval) and pushes its summary to the user's iPhone
(notify_approval_async). body_sha256 is the contract's canonical request hash
over the exact method, path, query and body stored on the row, so the iOS app
can recompute it from the Approval object before it signs.

A row changes status only by compare-and-swap on that status (cas_status), so a
lazy expiry, a decision and an execution can never all win. Reads refresh
lazily: a pending row past expires_at becomes "expired", and an approved row
whose execution was claimed but never recorded becomes "failed" (plan
Decision 8). Every terminal transition scrubs secret-named body fields
(Decision 9); a pending row is never altered, so it always re-hashes to
body_sha256.

A human sees only their own approvals; an MCP principal sees only the ones its
own client created for its acting user. Anything else, a malformed id included,
reads as not found.

An approval device is a P-256 signing key the human enrols from the iOS app
(register_device). It can sign a decision only from active_at, 600 s after
enrolment, and every enrolment alerts Discord (alert_device_registered), so a
key added from a stolen session is announced before it can approve anything
and can be revoked in that window. A device is visible only to its own user.
"""
from __future__ import annotations

import base64
import logging
import re
import threading
import unicodedata
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import apns_sender
import notifications
from db import P, store
from mcp_audit import redact_secrets
from mcp_policy import McpConfig, canonical_request_sha256, iso_utc, parse_iso_utc, utc_now

_log = logging.getLogger("uvicorn.error")

# Contract §5. Matched with fullmatch: `$` alone would accept a trailing newline.
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

APPROVALS_TABLE = "McpApprovals"
PUBLIC_FIELDS = ("id", "created_at", "expires_at", "status", "summary", "money_state", "client",
                 "method", "path", "query", "body", "body_sha256", "result_status", "result",
                 "decided_at", "device_id")
LIST_STATUSES = ("pending", "all")

SUMMARY_MAX_CHARS = 200
STALE_EXECUTION_DETAIL = "mcp: execution outcome unknown (the API restarted mid-execution); check orders"
PUSH_TITLE = "Jarvis wants to act"
PUSH_CATEGORY = "MCP_APPROVAL"

_SUFFIXES = {
    "live": " — LIVE",
    "unknown": " — MONEY STATE UNKNOWN",
    "protected": " — PROTECTED PAPER",
    "irreversible": " — IRREVERSIBLE",
}


def is_approval_id(value: object) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


# -- summaries ----------------------------------------------------------------

def _text(value: object) -> str:
    """One display-safe word run for the push: control, format and separator
    characters (newlines, bidi overrides) become single spaces, so an agent's
    value cannot fake a line or reorder the text around it. Missing is "?"."""
    if value is None:
        return "?"
    flat = "".join(" " if unicodedata.category(ch)[0] in "CZ" else ch for ch in str(value))
    return " ".join(flat.split()) or "?"


def _summary_core(operation_id: str, method: str, path: str, path_params: Mapping[str, str],
                  body: object) -> str:
    fields = body if isinstance(body, dict) else {}
    instance = _text(path_params.get("instance_id"))
    if operation_id == "live_command":
        payload = fields.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        command = _text(fields.get("type"))
        if fields.get("type") == "submit_order":
            return (f"submit_order {_text(payload.get('side')).upper()} {_text(payload.get('qty'))} "
                    f"{_text(payload.get('symbol'))} on {instance}")
        symbol = payload.get("symbol")
        target = f" {_text(symbol)}" if symbol not in (None, "") else ""
        return f"{command}{target} on {instance}"
    if operation_id == "brokerage_place_order":
        qty, notional = fields.get("qty"), fields.get("notional")
        if qty not in (None, ""):
            amount = _text(qty)
        elif notional not in (None, ""):
            amount = "$" + _text(notional)
        else:
            amount = "?"
        order_type = fields.get("type")
        return (f"{_text(fields.get('side')).upper()} {amount} {_text(fields.get('symbol'))} "
                f"{'market' if order_type is None else _text(order_type)} "
                f"on brokerage {_text(path_params.get('brokerage_id'))}")
    if operation_id in ("start_instance", "stop_instance"):
        return f"{operation_id.split('_', 1)[0]} instance {instance}"
    if operation_id == "swing_decide_signal":
        return (f"{_text(fields.get('decision'))} swing signal {_text(path_params.get('signal_id'))} "
                f"on {instance}")
    return f"{_text(operation_id)}: {_text(method.upper())} {_text(path)}"


def build_summary(operation_id: str, method: str, path: str, path_params: Mapping[str, str],
                  body: object, money_state: str) -> str:
    """The push body: what the call does, then a suffix naming what is at stake.

    At most SUMMARY_MAX_CHARS characters: a long core is cut with "…", and the
    suffix ("— LIVE" and the like) is always kept whole.
    """
    suffix = _SUFFIXES.get(money_state, "")
    core = _summary_core(operation_id, method, path, path_params, body)
    room = SUMMARY_MAX_CHARS - len(suffix)
    if len(core) > room:
        core = core[:room - 1] + "…"
    return core + suffix


# -- the row ------------------------------------------------------------------

def create_approval(*, principal: dict, operation_id: str, method: str, path: str, query: dict,
                    body: object, money_state: str, summary: str, idempotency_key: str | None,
                    correlation_id: str | None, cfg: McpConfig, now: datetime | None = None) -> dict:
    """Insert a pending approval for the principal's call and return the row."""
    now = now or utc_now()
    method = method.upper()          # the iOS app hashes the method as stored
    query = dict(query)
    row = {
        "id": uuid.uuid4().hex,
        "created_at": iso_utc(now),
        "expires_at": iso_utc(now + timedelta(seconds=cfg.approval_ttl_s)),
        "status": "pending",
        "user_id": principal["id"],
        "username": principal.get("username"),
        "client": principal.get("client"),
        "operation_id": operation_id,
        "method": method,
        "path": path,
        "query": query,
        "body": body,
        "body_sha256": canonical_request_sha256(method, path, query, body),
        "summary": summary,
        "money_state": money_state,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "result_status": None,
        "result": None,
        "decided_at": None,
        "device_id": None,
        "exec_claimed_at": None,
        "executed_at": None,
    }
    result = store.insert(APPROVALS_TABLE, row)
    if getattr(result, "errors", 0):
        # ReQL-style partial success: no row means no approval to promise the caller.
        raise RuntimeError("mcp: approval insert rejected")
    return row


def send_approval_push(row: dict) -> None:
    """Push the approval to the user's iPhones; best-effort, never raises."""
    try:
        apns_sender.send_to_user(row["user_id"], title=PUSH_TITLE, body=row["summary"],
                                 category=PUSH_CATEGORY, data={"approval_id": row["id"]})
    except Exception as exc:
        # The type only: a message can quote the summary.
        _log.warning("mcp: approval push failed (%s)", type(exc).__name__)


def notify_approval_async(row: dict) -> None:
    """send_approval_push on a daemon thread, so APNs never delays the gate's 202."""
    threading.Thread(target=send_approval_push, args=(dict(row),), name="mcp-approval-push",
                     daemon=True).start()


def approval_public(row: dict) -> dict:
    """The contract's Approval object: exactly PUBLIC_FIELDS, in that order."""
    return {name: row.get(name) for name in PUBLIC_FIELDS}


def terminal_scrub(row: dict) -> dict:
    """A copy of the row with every secret-named body field "[redacted]" (Decision 9)."""
    out = dict(row)
    out["body"] = redact_secrets(row.get("body"))
    return out


def cas_status(approval_id: str, *, expect: str, patch: dict) -> dict | None:
    """Apply the patch only while the row's status is still `expect`.

    Returns the new row, or None when the row is missing or another writer moved
    its status first. The patch replaces whole top-level fields.
    """
    row = store.get(APPROVALS_TABLE, approval_id)
    if row is None or row.get("status") != expect:
        return None
    new = {**row, **patch, "id": row["id"]}
    return store.replace_if(APPROVALS_TABLE, approval_id, when=P.field("status").eq(expect), doc=new)


# -- lazy transitions ---------------------------------------------------------

def _stamp(value: object) -> datetime | None:
    try:
        return parse_iso_utc(value)
    except (TypeError, ValueError):
        return None


def _expired(row: dict, now: datetime) -> bool:
    """Past expires_at; a missing or unreadable stamp counts as expired (fail closed)."""
    expires_at = _stamp(row.get("expires_at"))
    return expires_at is None or now > expires_at


def _execution_stale(row: dict, cfg: McpConfig, now: datetime) -> bool:
    """Claimed for execution, nothing recorded, and the claim is older than the
    stale window; an unreadable claim stamp counts as stale."""
    if row.get("exec_claimed_at") is None or row.get("result_status") is not None:
        return False
    claimed_at = _stamp(row.get("exec_claimed_at"))
    return claimed_at is None or now - claimed_at > timedelta(seconds=cfg.approval_exec_stale_s)


def refresh_lazily(row: dict, *, cfg: McpConfig, now: datetime | None = None) -> dict:
    """Apply the transitions time alone decides, and return the current row.

    pending past expires_at -> "expired"; approved with a stale execution claim
    and no result -> "failed" with STALE_EXECUTION_DETAIL. Both are scrubbed. A
    lost race returns the row as the winner left it.
    """
    now = now or utc_now()
    status = row.get("status")
    if status == "pending" and _expired(row, now):
        patch = {"status": "expired", "body": terminal_scrub(row)["body"]}
    elif status == "approved" and _execution_stale(row, cfg, now):
        patch = {"status": "failed", "result": {"detail": STALE_EXECUTION_DETAIL},
                 "body": terminal_scrub(row)["body"]}
    else:
        return row
    new = cas_status(row["id"], expect=status, patch=patch)
    if new is not None:
        return new
    return store.get(APPROVALS_TABLE, row["id"]) or row


# -- reads ----------------------------------------------------------------------

def _visible_to(row: dict, viewer: dict) -> bool:
    user_id = viewer.get("id")
    if not user_id or row.get("user_id") != user_id:
        return False
    return viewer.get("via") != "mcp" or row.get("client") == viewer.get("client")


def get_visible(approval_id: str, *, viewer: dict, cfg: McpConfig,
                now: datetime | None = None) -> dict | None:
    """The refreshed row, or None when the id is malformed, unknown or not the viewer's."""
    if not is_approval_id(approval_id):
        return None
    row = store.get(APPROVALS_TABLE, approval_id)
    if row is None or not _visible_to(row, viewer):
        return None
    return refresh_lazily(row, cfg=cfg, now=now)


def list_visible(*, viewer: dict, status: str, limit: int, cfg: McpConfig,
                 now: datetime | None = None) -> list[dict]:
    """The viewer's approvals, newest first, refreshed; "pending" keeps only rows
    still pending after the refresh, "all" keeps every status."""
    if status not in LIST_STATUSES:
        raise ValueError("status must be pending or all")
    if not viewer.get("id"):
        return []
    now = now or utc_now()
    match: dict = {"user_id": viewer["id"]}
    if viewer.get("via") == "mcp":
        match["client"] = viewer.get("client")
    if status == "pending":
        match["status"] = "pending"
    newest = store.order_by(store.filter(APPROVALS_TABLE, match), index="created_at", desc=True)
    rows = [refresh_lazily(row, cfg=cfg, now=now) for row in store.run(store.limit(newest, limit))]
    return [row for row in rows if status == "all" or row.get("status") == "pending"]


# -- approval devices -----------------------------------------------------------

DEVICES_TABLE = "McpApprovalDevices"
DEVICE_ACTIVATION_DELAY_S = 600
DEVICE_NAME_MAX_CHARS = 64
DEVICE_ALERT_TITLE = "MCP approval device registered"

_P256_POINT_BYTES = 65            # X9.62 uncompressed: 0x04, then X and Y (32 bytes each)
_PUBLIC_KEY_MAX_CHARS = 128       # 65 bytes are 88 base64 characters; refuse a huge string unread
_BAD_PUBLIC_KEY = "public_key must be base64 of a 65-byte uncompressed P-256 point"


def decode_public_key(b64: str) -> ec.EllipticCurvePublicKey:
    """The P-256 key in base64 of an X9.62 uncompressed point (65 bytes, 0x04 first).

    Strict base64 (standard alphabet, no whitespace). ValueError on anything
    else, a point that is not on the curve included.
    """
    if not isinstance(b64, str) or len(b64) > _PUBLIC_KEY_MAX_CHARS:
        raise ValueError(_BAD_PUBLIC_KEY)
    try:
        raw = base64.b64decode(b64, validate=True)    # binascii.Error is a ValueError
    except ValueError:
        raise ValueError(_BAD_PUBLIC_KEY) from None
    if len(raw) != _P256_POINT_BYTES or raw[0] != 0x04:
        raise ValueError(_BAD_PUBLIC_KEY)
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError:
        raise ValueError(_BAD_PUBLIC_KEY) from None


def _device_name(name: object) -> str:
    if not isinstance(name, str):
        raise ValueError("name must be a string")
    name = name.strip()
    if not 1 <= len(name) <= DEVICE_NAME_MAX_CHARS:
        raise ValueError(f"name must be 1-{DEVICE_NAME_MAX_CHARS} characters")
    return name


def register_device(*, user: dict, name: str, public_key_b64: str, now: datetime | None = None) -> dict:
    """Enrol a signing key for the user and return the row; it can approve from
    active_at = now + DEVICE_ACTIVATION_DELAY_S. ValueError on a bad name or key,
    and nothing is stored then. The key is stored re-encoded from the point."""
    name = _device_name(name)
    point = decode_public_key(public_key_b64).public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    now = now or utc_now()
    row = {
        "id": uuid.uuid4().hex,
        "user_id": user["id"],
        "name": name,
        "public_key": base64.b64encode(point).decode("ascii"),
        "created_at": iso_utc(now),
        "active_at": iso_utc(now + timedelta(seconds=DEVICE_ACTIVATION_DELAY_S)),
        "revoked_at": None,
    }
    result = store.insert(DEVICES_TABLE, row)
    if getattr(result, "errors", 0):
        raise RuntimeError("mcp: device insert rejected")
    return row


def device_public(row: dict) -> dict:
    """The contract's Device object: exactly device_id (the row id), name, created_at
    and active_at, so user_id, the key and revoked_at stay server-side."""
    return {"device_id": row.get("id"), "name": row.get("name"), "created_at": row.get("created_at"),
            "active_at": row.get("active_at")}


def list_devices(user_id: str) -> list[dict]:
    """The user's devices that are not revoked, oldest first; one still waiting
    for active_at is listed too."""
    if not user_id:
        return []
    rows = store.run(store.filter(DEVICES_TABLE, {"user_id": user_id}))
    live = [row for row in rows if row.get("revoked_at") is None]
    # Bytewise, like COLLATE "C": the stamps are fixed-width, so this is chronological.
    return sorted(live, key=lambda row: (row.get("created_at") or "", row.get("id") or ""))


def _own_device(user_id: str, device_id: str) -> dict | None:
    """The row when the id is well formed (device ids share the approval-id
    shape, contract §5), it exists, it is the user's and it is not revoked."""
    if not user_id or not is_approval_id(device_id):
        return None
    row = store.get(DEVICES_TABLE, device_id)
    if row is None or row.get("user_id") != user_id or row.get("revoked_at") is not None:
        return None
    return row


def revoke_device(*, user_id: str, device_id: str, now: datetime | None = None) -> bool:
    """Stamp revoked_at on the user's device. False when the id is malformed or
    unknown, the device is another user's, or it is already revoked."""
    if _own_device(user_id, device_id) is None:
        return False
    now = now or utc_now()
    result = store.update(DEVICES_TABLE, device_id, {"revoked_at": iso_utc(now)})
    if getattr(result, "errors", 0):
        # A revoke the user believes happened must not leave the key able to sign.
        raise RuntimeError("mcp: device revoke rejected")
    return True


def active_device(*, user_id: str, device_id: str, now: datetime | None = None) -> dict | None:
    """The row when it is the user's, not revoked, and active_at <= now; an
    unreadable active_at counts as not active (fail closed)."""
    row = _own_device(user_id, device_id)
    if row is None:
        return None
    active_at = _stamp(row.get("active_at"))
    if active_at is None or (now or utc_now()) < active_at:
        return None
    return row


def alert_device_registered(row: dict, username: str) -> None:
    """Announce an enrolment on Discord; best-effort, never raises.

    The name and username are flattened like a push summary, so a name cannot
    fake a line of the alert.
    """
    body = (f"MCP APPROVAL DEVICE registered: '{_text(row.get('name'))}' for {_text(username)}; "
            f"it can approve from {row.get('active_at')}. If this was not you, revoke it in Settings now.")
    try:
        notifications.notify(category="other", instance_id="mcp", title=DEVICE_ALERT_TITLE, body=body,
                             discord_channel="notifications")
    except Exception as exc:
        _log.warning("mcp: device registration alert failed (%s)", type(exc).__name__)
