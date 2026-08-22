"""Live trading state persistence + command queue + per-instance log files.

Mirrors the NexusGraphBuilds pattern (see backend/nexus_graph_builds.py):
  - `LiveState` table: one row per instance, upserted every ~3s by the broker.
  - `LiveCommands` table: append-only command queue; broker watches via
    changefeed and writes results back.
  - Per-instance log file under a named volume (live_trading_logs) so the UI
    can tail broker stdout.

The API only READS LiveState / writes LiveCommands. Only broker.py writes
LiveState and mutates LiveCommands.status. No strategy code should touch
these tables directly.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any, Callable, Optional

from db import store
from db import schema
from live_orders import (
    DependencySnapshot,
    GateDecision,
    OrderIntent,
    UnifiedOrderGate,
)


LIVE_STATE_TABLE = "LiveState"
LIVE_COMMANDS_TABLE = "LiveCommands"
DB_NAME = "IntelliStock"

DEFAULT_LOG_DIR = os.environ.get("LIVE_TRADING_LOG_DIR", "/app/live_trading_logs")
DEFAULT_RETENTION_DAYS = int(os.environ.get("LIVE_TRADING_LOG_RETENTION_DAYS", "14"))
DEFAULT_MAX_FILES = int(os.environ.get("LIVE_TRADING_LOG_MAX_FILES", "50"))

# Max entries kept inside an upserted LiveState row. These arrays are
# re-uploaded in full on every snapshot, so keep them tight.
MAX_RECENT_TRADES = int(os.environ.get("LIVE_STATE_MAX_RECENT_TRADES", "100"))
MAX_PORTFOLIO_HISTORY = int(os.environ.get("LIVE_STATE_MAX_PORTFOLIO_HISTORY", "500"))

VALID_COMMAND_TYPES = frozenset({"halt", "close_position", "submit_order"})
TERMINAL_COMMAND_STATUSES = frozenset(
    {"completed", "failed", "reconciliation_required"}
)


def append_current_equity_point(portfolio_history, equity, now_iso, max_ph=MAX_PORTFOLIO_HISTORY):
    """Append the current live equity as the latest equity-curve point.

    The in-memory ``_portfolio_snapshots`` list only grows on live (non-IDLE)
    ticks, so it freezes at RTH close while Alpaca equity keeps moving overnight
    (24/5). Appending the fresh account equity here keeps the served curve
    current at any hour. Ephemeral — apply to the SERVED history each snapshot;
    do not persist (the next 3s tick recomputes it from fresh equity).

    Skips when ``equity`` is non-positive or the series already ends at that
    value (avoids a duplicate point). Returns a NEW list, truncated to max_ph.
    """
    try:
        eq = float(equity)
    except (TypeError, ValueError):
        return portfolio_history
    if eq <= 0:
        return portfolio_history
    out = list(portfolio_history or [])
    last_val = None
    if out:
        try:
            last_val = float(out[-1].get("value"))
        except (TypeError, ValueError, AttributeError):
            last_val = None
    if last_val is None or abs(last_val - eq) > 1e-6:
        out.append({"ts": now_iso, "value": eq})
    if len(out) > max_ph:
        out = out[-max_ph:]
    return out


# ── Table bootstrap ─────────────────────────────────────────────────────────

def ensure_tables(r=None, conn=None) -> None:
    """Create LiveState + LiveCommands tables if missing. Safe to call often.

    ``r``/``conn`` are accepted and ignored: the store takes its own pooled
    connection per operation. The parameters stay so the ~20 call sites (and
    the test doubles that pass a stub pair) keep their arity.
    """
    for table in (LIVE_STATE_TABLE, LIVE_COMMANDS_TABLE):
        try:
            schema.ensure_table(table)
        except Exception:
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value):
    """Datetimes -> ISO-8601 strings, the wire form for a jsonb document.

    ``_command_claim_transition`` is a pure function shared with the tests and
    still returns aware datetimes; ``LiveCommands.time_fields`` decodes them
    back on read, so a round trip yields the same Python type RethinkDB's
    driver produced.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


# ── LiveState helpers ───────────────────────────────────────────────────────

def upsert_live_state(r, conn, instance_id: str, payload: dict) -> None:
    """Upsert this instance's live state row. Payload fields are merged onto
    whatever's already there; missing fields don't reset existing values.

    Caller typically rebuilds the full snapshot each tick (positions, trades,
    etc.) and passes it whole; we don't try to be clever about diffing.
    """
    doc = dict(payload or {})
    doc["id"] = str(instance_id)
    # r.now() was a server-side time; an ISO-8601 string in the document is
    # decoded back to a tz-aware datetime by LiveState.time_fields, so readers
    # see the same Python type.
    doc["last_updated"] = _now_iso()
    doc["last_updated_iso"] = _now_iso()
    try:
        # The broker boot path creates LiveState before this hot-path loop
        # starts. Do not re-run ensure_table() here.
        #
        # Snapshot rows are best-effort/latest-wins telemetry. RethinkDB's
        # noreply=True skipped the response read to keep the snapshot watchdog
        # from parking on a stalled server; Postgres has a statement timeout
        # and the pool has a checkout timeout, so the write is issued normally
        # and a failure is swallowed here exactly as it was before.
        store.insert(LIVE_STATE_TABLE, _jsonable(doc), conflict="replace")
    except Exception:
        pass


def get_live_state(r, conn, instance_id: str) -> Optional[dict]:
    """Return this instance's LiveState row or None."""
    ensure_tables(r, conn)
    try:
        return store.get(LIVE_STATE_TABLE, str(instance_id))
    except Exception:
        return None


def clear_live_state(r, conn, instance_id: str) -> None:
    """Remove this instance's LiveState row. Called when the broker shuts down."""
    try:
        store.delete(LIVE_STATE_TABLE, str(instance_id))
    except Exception:
        pass


# ── LiveCommands helpers ────────────────────────────────────────────────────

def _make_command_id() -> str:
    return uuid.uuid4().hex


def submit_command(
    r,
    conn,
    *,
    instance_id: str,
    type: str,
    payload: dict,
    submitted_by: Optional[str] = None,
) -> str:
    """Enqueue a command for the broker to execute. Returns the command id.

    Called by the API layer (see interactive_utils.action_live_command).
    Validates the type and normalizes payload shape; detailed payload
    validation lives in the broker's handler since the broker owns the adapter.
    """
    ensure_tables(r, conn)
    t = str(type or "").strip().lower()
    if t not in VALID_COMMAND_TYPES:
        raise ValueError(f"unsupported command type: {type!r}")
    doc = {
        "id": _make_command_id(),
        "instance_id": str(instance_id),
        "type": t,
        "payload": dict(payload or {}),
        "status": "pending",
        "created_at": _now_iso(),
        "created_at_iso": _now_iso(),
        "started_at": None,
        "completed_at": None,
        "error": None,
        "result": None,
        "submitted_by": submitted_by,
        "lease_owner": None,
        "lease_expires_at": None,
        "lease_expires_at_iso": None,
        "attempt_count": 0,
    }
    # A duplicate id is the ONLY failure the conflict=replace fallback is for:
    # a transport or auth error must not get silently "recovered" that way
    # (it could overwrite a row that actually landed after a retry). The store
    # reports a duplicate primary key in ``errors``/``first_error`` rather than
    # by raising, so the two cases are separable without a driver exception
    # class. A genuine outage raises StoreError and propagates, as it did.
    res = store.insert(LIVE_COMMANDS_TABLE, doc, conflict="error")
    if res["errors"]:
        store.insert(LIVE_COMMANDS_TABLE, doc, conflict="replace")
    return doc["id"]


def get_command(r, conn, command_id: str) -> Optional[dict]:
    ensure_tables(r, conn)
    try:
        return store.get(LIVE_COMMANDS_TABLE, str(command_id))
    except Exception:
        return None


def _command_claim_transition(
    command: dict,
    *,
    now: datetime,
    worker_id: str,
    lease_seconds: float,
) -> tuple[Optional[dict], bool]:
    """Pure lease transition used by the RethinkDB command claimant.

    An expired ``halt`` can be retried because it is idempotent and cannot add
    exposure.  Every expired order-affecting command is quarantined for broker
    reconciliation instead of being blindly re-submitted.
    """

    observed = now
    if observed.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    observed = observed.astimezone(timezone.utc)
    owner = str(worker_id or "").strip()
    if not owner:
        raise ValueError("worker_id is required")
    lease = float(lease_seconds)
    if lease <= 0:
        raise ValueError("lease_seconds must be > 0")
    cmd = dict(command or {})
    status = str(cmd.get("status") or "").strip().lower()
    if status == "running":
        raw_expiry = cmd.get("lease_expires_at_iso")
        try:
            expiry = datetime.fromisoformat(str(raw_expiry))
            if expiry.tzinfo is None:
                raise ValueError
            expiry = expiry.astimezone(timezone.utc)
        except Exception:
            expiry = datetime.fromtimestamp(0, timezone.utc)
        if expiry > observed:
            return None, False
        if str(cmd.get("type") or "").strip().lower() != "halt":
            cmd.update(
                {
                    "status": "reconciliation_required",
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "lease_expires_at_iso": None,
                    "error": (
                        "worker lease expired after possible broker side "
                        "effect; reconcile lifecycle and broker history "
                        "before issuing a replacement command"
                    ),
                    "completed_at": observed,
                    "completed_at_iso": observed.isoformat(),
                }
            )
            return cmd, False
    elif status != "pending":
        return None, False

    expiry = observed + timedelta(seconds=lease)
    cmd.update(
        {
            "status": "running",
            "started_at": observed,
            "started_at_iso": observed.isoformat(),
            "lease_owner": owner,
            "lease_expires_at": expiry,
            "lease_expires_at_iso": expiry.isoformat(),
            "attempt_count": int(cmd.get("attempt_count") or 0) + 1,
            "error": None,
        }
    )
    return cmd, True


def claim_next_pending(
    r,
    conn,
    instance_id: str,
    *,
    worker_id: Optional[str] = None,
    lease_seconds: float = 30.0,
) -> Optional[dict]:
    """Atomically lease the oldest command eligible for execution.

    Expired non-halt commands are atomically moved to
    ``reconciliation_required`` and never returned for execution.  This
    prevents a crash-after-submit from becoming a duplicate live order.
    """
    ensure_tables(r, conn)
    owner = str(worker_id or f"broker-{os.getpid()}").strip()
    now = datetime.now(timezone.utc)
    try:
        # get_all(instance_id, index="instance_id") + filter + order_by is one
        # selection here: the instance_id generated column carries the index,
        # created_at is an ISO-8601 string so its bytewise ASC order is the
        # chronological order the datetime field had.
        sel = store.filter(
            LIVE_COMMANDS_TABLE,
            store.P.field("instance_id").eq(str(instance_id))
            & (store.P.field("status").eq("pending")
               | store.P.field("status").eq("running")))
        candidates = store.run(
            store.limit(store.order_by(sel, fields=(store.asc("created_at"),)), 20))
    except Exception:
        return None
    for cmd in candidates:
        cmd_id = cmd.get("id")
        if not cmd_id:
            continue
        updated, executable = _command_claim_transition(
            cmd,
            now=now,
            worker_id=owner,
            lease_seconds=lease_seconds,
        )
        if updated is None:
            continue
        patch = {
            key: value
            for key, value in updated.items()
            if key not in {"id", "instance_id", "type", "payload", "created_at"}
            and value != cmd.get(key)
        }
        prior_status = str(cmd.get("status") or "")
        prior_owner = str(cmd.get("lease_owner") or "")
        try:
            # The ReQL form was update(lambda row: r.branch(cond, patch, {})):
            # a compare-and-swap that DEEP MERGES patch on match. A Selection
            # carrying both the row identity and the CAS condition makes that
            # one server-side statement with the same merge semantics.
            rid = store.coerce_id(LIVE_COMMANDS_TABLE, cmd_id)
            guard = store.filter(
                LIVE_COMMANDS_TABLE,
                store.P.field("status").eq(prior_status)
                & store.P.field("lease_owner").default("").eq(prior_owner),
            ).where("id = %s", (rid,))
            res = store.update(LIVE_COMMANDS_TABLE, guard, _jsonable(patch))
            if int(res.get("replaced", 0) or 0) > 0:
                if executable:
                    return store.get(LIVE_COMMANDS_TABLE, cmd_id)
        except Exception:
            continue
    return None


def complete_command(r, conn, command_id: str, *, result: Optional[dict] = None) -> None:
    try:
        store.update(LIVE_COMMANDS_TABLE, str(command_id), {
            "status": "completed",
            "completed_at": _now_iso(),
            "completed_at_iso": _now_iso(),
            "result": result or {},
            "lease_owner": None,
            "lease_expires_at": None,
            "lease_expires_at_iso": None,
        })
    except Exception:
        pass


def fail_command(r, conn, command_id: str, *, error: str) -> None:
    try:
        store.update(LIVE_COMMANDS_TABLE, str(command_id), {
            "status": "failed",
            "completed_at": _now_iso(),
            "completed_at_iso": _now_iso(),
            "error": str(error)[:2000],
            "lease_owner": None,
            "lease_expires_at": None,
            "lease_expires_at_iso": None,
        })
    except Exception:
        pass


# ── Client-order-id idempotency ─────────────────────────────────────────────
# Re-export the canonical utility so operator-submitted and strategy-submitted
# orders for the same (instance, symbol, bar_iso, side) share an id and Alpaca
# treats them as idempotent. Do NOT define a local copy — previous attempts at
# that produced divergent hash payloads (uppercasing symbol) that broke
# idempotency between operator and strategy paths.
from broker_adapters._client_order_id import make_client_order_id  # noqa: E402,F401


# ── Unified live stock-order service seam ───────────────────────────────────

from live_orders.service import LiveOrderService, OrderSubmission  # noqa: E402,F401


# ── Log file helpers (mirror nexus_graph_builds) ────────────────────────────

def ensure_log_dir(log_dir: Optional[str] = None) -> str:
    d = log_dir or os.environ.get("LIVE_TRADING_LOG_DIR", DEFAULT_LOG_DIR)
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _safe_instance_id(instance_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(instance_id))[:64] or "instance"


def log_file_path_for(instance_id: str, log_dir: Optional[str] = None) -> str:
    return os.path.join(ensure_log_dir(log_dir), f"instance_{_safe_instance_id(instance_id)}.log")


def open_live_log(instance_id: str, log_dir: Optional[str] = None) -> tuple[object, str]:
    """Open a fresh log file for this instance. Returns (file_obj, path).

    Rotation on restart: if a file from a prior session exists, it is
    renamed to ``instance_<id>.<YYYYMMDDTHHMMSSZ>.log`` before the new
    one opens in truncate mode. This gives operators a clean live-tail
    per container boot while preserving crash tracebacks from the prior
    boot as a separate archived file (picked up by rotate_old_logs'
    retention cap). The canonical live path is always ``instance_<id>.log``
    so the UI's tail endpoint doesn't need to know about archives.
    """
    path = log_file_path_for(instance_id, log_dir)
    # Archive the prior session's file if it has content.
    try:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archive_path = os.path.join(
                os.path.dirname(path),
                f"instance_{_safe_instance_id(instance_id)}.{ts}.log",
            )
            try:
                os.replace(path, archive_path)
            except Exception:
                # If the rename fails (permissions, cross-device), fall back
                # to truncating in-place — the user's stated preference is
                # "fresh log per restart", so losing the archive is better
                # than mixing old + new lines.
                try:
                    open(path, "w", encoding="utf-8").close()
                except Exception:
                    pass
    except Exception:
        pass
    f = open(path, "w", buffering=1, encoding="utf-8")
    header = (
        "# live_trading\n"
        f"# instance_id: {instance_id}\n"
        f"# started_at: {_now_iso()}\n"
        f"# log_file: {path}\n"
        "# ---\n"
    )
    try:
        f.write(header)
    except Exception:
        pass
    return f, path


def close_live_log(file_obj, reason: str = "shutdown") -> None:
    if file_obj is None:
        return
    try:
        file_obj.write("# ---\n")
        file_obj.write(f"# closed_at: {_now_iso()}\n")
        file_obj.write(f"# reason: {reason}\n")
    except Exception:
        pass
    try:
        file_obj.flush()
        file_obj.close()
    except Exception:
        pass


def read_live_log_file(instance_id: str, log_dir: Optional[str] = None) -> Optional[str]:
    path = log_file_path_for(instance_id, log_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def rotate_old_logs(log_dir: Optional[str] = None) -> int:
    """Age + count based cleanup. Safe to skip; best-effort."""
    log_dir = log_dir or ensure_log_dir()
    if not os.path.isdir(log_dir):
        return 0
    try:
        entries = []
        for name in os.listdir(log_dir):
            if not name.startswith("instance_") or not name.endswith(".log"):
                continue
            path = os.path.join(log_dir, name)
            try:
                entries.append((os.path.getmtime(path), path))
            except Exception:
                pass
        entries.sort(reverse=True)
        cutoff_sec = time.time() - DEFAULT_RETENTION_DAYS * 86400
        deleted = 0
        for i, (mt, path) in enumerate(entries):
            if i >= DEFAULT_MAX_FILES or mt < cutoff_sec:
                try:
                    os.remove(path)
                    deleted += 1
                except Exception:
                    pass
        return deleted
    except Exception:
        return 0
