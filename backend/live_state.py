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
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Callable, Optional

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
TERMINAL_COMMAND_STATUSES = frozenset({"completed", "failed"})


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

def ensure_tables(r, conn) -> None:
    """Create LiveState + LiveCommands tables if missing. Safe to call often."""
    try:
        existing = set(r.db(DB_NAME).table_list().run(conn))
    except Exception:
        return
    if LIVE_STATE_TABLE not in existing:
        try:
            r.db(DB_NAME).table_create(LIVE_STATE_TABLE).run(conn)
        except Exception:
            pass
    if LIVE_COMMANDS_TABLE not in existing:
        try:
            r.db(DB_NAME).table_create(LIVE_COMMANDS_TABLE).run(conn)
        except Exception:
            pass
        # index by instance_id so the broker's changefeed filter is cheap.
        try:
            r.db(DB_NAME).table(LIVE_COMMANDS_TABLE).index_create("instance_id").run(conn)
            r.db(DB_NAME).table(LIVE_COMMANDS_TABLE).index_wait("instance_id").run(conn)
        except Exception:
            pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── LiveState helpers ───────────────────────────────────────────────────────

def upsert_live_state(r, conn, instance_id: str, payload: dict) -> None:
    """Upsert this instance's live state row. Payload fields are merged onto
    whatever's already there; missing fields don't reset existing values.

    Caller typically rebuilds the full snapshot each tick (positions, trades,
    etc.) and passes it whole; we don't try to be clever about diffing.
    """
    doc = dict(payload or {})
    doc["id"] = str(instance_id)
    doc["last_updated"] = r.now()
    doc["last_updated_iso"] = _now_iso()
    try:
        # The broker boot path creates LiveState before this hot-path loop
        # starts. Do not re-run table_list()/table_create() here: the Python
        # driver has no query read timeout, and a stalled RethinkDB response
        # can otherwise leave the snapshot watchdog with a zombie worker.
        #
        # Snapshot rows are best-effort/latest-wins telemetry, so avoid the
        # response read entirely. If the connection is bad, the next broker
        # tick reconnects and sends a fresh full snapshot.
        (
            r.db(DB_NAME)
            .table(LIVE_STATE_TABLE)
            .insert(doc, conflict="replace")
            .run(conn, noreply=True)
        )
    except Exception:
        pass


def get_live_state(r, conn, instance_id: str) -> Optional[dict]:
    """Return this instance's LiveState row or None."""
    ensure_tables(r, conn)
    try:
        return r.db(DB_NAME).table(LIVE_STATE_TABLE).get(str(instance_id)).run(conn)
    except Exception:
        return None


def clear_live_state(r, conn, instance_id: str) -> None:
    """Remove this instance's LiveState row. Called when the broker shuts down."""
    try:
        r.db(DB_NAME).table(LIVE_STATE_TABLE).get(str(instance_id)).delete().run(conn)
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
        "created_at": r.now(),
        "created_at_iso": _now_iso(),
        "started_at": None,
        "completed_at": None,
        "error": None,
        "result": None,
        "submitted_by": submitted_by,
    }
    # Narrow the duplicate-id fallback to ReqlOpFailedError so a transport or
    # auth error doesn't get silently "recovered" via conflict=replace (which
    # could overwrite a row that actually landed on the server after a retry).
    try:
        from rethinkdb.errors import ReqlOpFailedError as _ReqlOpFailedError  # type: ignore
    except Exception:
        _ReqlOpFailedError = Exception  # type: ignore[assignment]
    try:
        r.db(DB_NAME).table(LIVE_COMMANDS_TABLE).insert(doc, conflict="error").run(conn)
    except _ReqlOpFailedError:
        try:
            r.db(DB_NAME).table(LIVE_COMMANDS_TABLE).insert(doc, conflict="replace").run(conn)
        except _ReqlOpFailedError:
            pass
    return doc["id"]


def get_command(r, conn, command_id: str) -> Optional[dict]:
    ensure_tables(r, conn)
    try:
        return r.db(DB_NAME).table(LIVE_COMMANDS_TABLE).get(str(command_id)).run(conn)
    except Exception:
        return None


def claim_next_pending(r, conn, instance_id: str) -> Optional[dict]:
    """Broker side: atomically find the oldest pending command for this
    instance and mark it running. Returns the command doc (with the new
    status) or None if no pending work.

    Race-safe: we select by `(status=='pending', instance_id)`, sort by
    `created_at`, pick the first, and run a conditional update that only
    succeeds if the row is still pending. If the update touched 0 rows we
    lost the race and try the next one.
    """
    ensure_tables(r, conn)
    tbl = r.db(DB_NAME).table(LIVE_COMMANDS_TABLE)
    try:
        pending = list(
            tbl
            .get_all(str(instance_id), index="instance_id")
            .filter({"status": "pending"})
            .order_by("created_at")
            .limit(5)
            .run(conn)
        )
    except Exception:
        return None
    for cmd in pending:
        cmd_id = cmd.get("id")
        if not cmd_id:
            continue
        try:
            res = tbl.get(cmd_id).update(
                lambda row: r.branch(
                    row["status"].eq("pending"),
                    {"status": "running", "started_at": r.now(), "started_at_iso": _now_iso()},
                    {},
                )
            ).run(conn)
            if int(res.get("replaced", 0) or 0) > 0:
                fresh = tbl.get(cmd_id).run(conn)
                return fresh
        except Exception:
            continue
    return None


def complete_command(r, conn, command_id: str, *, result: Optional[dict] = None) -> None:
    try:
        r.db(DB_NAME).table(LIVE_COMMANDS_TABLE).get(str(command_id)).update({
            "status": "completed",
            "completed_at": r.now(),
            "completed_at_iso": _now_iso(),
            "result": result or {},
        }).run(conn)
    except Exception:
        pass


def fail_command(r, conn, command_id: str, *, error: str) -> None:
    try:
        r.db(DB_NAME).table(LIVE_COMMANDS_TABLE).get(str(command_id)).update({
            "status": "failed",
            "completed_at": r.now(),
            "completed_at_iso": _now_iso(),
            "error": str(error)[:2000],
        }).run(conn)
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

@dataclass(frozen=True)
class OrderSubmission:
    """Result of passing one intent through the gate and transport seam."""

    decision: GateDecision
    reference: Any = None

    @property
    def accepted(self) -> bool:
        return bool(self.decision.allowed and self.reference is not None)


class LiveOrderService:
    """The sole stock transport seam, ready for Task 8 persistence injection.

    Task 7 deliberately does not create another WAL or database write. The
    existing adapter transport remains injected here, after the pure gate.
    """

    def __init__(
        self,
        *,
        account_id: str,
        instance_id: str,
        snapshot_provider: Callable[[OrderIntent], DependencySnapshot],
        transport: Callable[..., Any],
        gate: Optional[UnifiedOrderGate] = None,
    ) -> None:
        self.account_id = str(account_id)
        self.instance_id = str(instance_id)
        self.risk_snapshot_id = ""
        self._snapshot_provider = snapshot_provider
        self._transport = transport
        self._gate = gate or UnifiedOrderGate()
        self._lock = threading.Lock()
        self._open_idempotency_keys: set[str] = set()

    def enqueue(self, intent: OrderIntent) -> OrderSubmission:
        """Evaluate and synchronously hand an approved intent to the adapter.

        The method name is the stable seam Task 8 can replace with durable
        lifecycle persistence without changing any order source.
        """

        if not isinstance(intent, OrderIntent):
            raise TypeError("LiveOrderService.enqueue requires an OrderIntent")
        try:
            snapshot = self._snapshot_provider(intent)
        except Exception:
            return OrderSubmission(
                decision=GateDecision(
                    allowed=False,
                    approved_quantity=0,
                    reason_codes=("dependency.snapshot.unavailable",),
                    idempotency_key=intent.idempotency_key,
                )
            )
        if not isinstance(snapshot, DependencySnapshot):
            return OrderSubmission(
                decision=GateDecision(
                    allowed=False,
                    approved_quantity=0,
                    reason_codes=("dependency.snapshot.invalid",),
                    idempotency_key=intent.idempotency_key,
                )
            )
        with self._lock:
            if intent.idempotency_key in self._open_idempotency_keys:
                return OrderSubmission(
                    decision=GateDecision(
                        allowed=False,
                        approved_quantity=0,
                        reason_codes=("idempotency.open_order_exists",),
                        idempotency_key=intent.idempotency_key,
                    )
                )
        decision = self._gate.evaluate(intent, snapshot)
        if not decision.allowed:
            return OrderSubmission(decision=decision)
        with self._lock:
            if intent.idempotency_key in self._open_idempotency_keys:
                return OrderSubmission(
                    decision=GateDecision(
                        allowed=False,
                        approved_quantity=0,
                        reason_codes=("idempotency.open_order_exists",),
                        idempotency_key=intent.idempotency_key,
                    )
                )
            self._open_idempotency_keys.add(intent.idempotency_key)
        try:
            reference = self._transport(
                symbol=intent.symbol,
                side=intent.side.value,
                qty=float(decision.approved_quantity),
                notional=None,
                order_type=intent.order_type,
                limit_price=(
                    float(intent.limit_price)
                    if intent.limit_price is not None
                    else None
                ),
                tif=intent.tif,
                extended_hours=intent.extended_hours,
                client_order_id=intent.idempotency_key,
            )
        except Exception:
            with self._lock:
                self._open_idempotency_keys.discard(intent.idempotency_key)
            raise
        return OrderSubmission(decision=decision, reference=reference)

    submit_intent = enqueue


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
