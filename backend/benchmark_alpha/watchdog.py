"""Independent broker/strategy mark and equity watchdog (Task 6).

Runs in a separate process from the broker. Compares DIRECT broker
positions/equity with the broker process's RethinkDB-persisted view. It has
read and cancel-entry authority only — no general order-submit method. After
``consecutive_critical`` mismatches (or a KILL risk state) it cancels entry
orders, halts the instance, and invokes ONLY the injected reduce-only
executor over broker-reported holdings. During a RethinkDB outage it keeps
monitoring from direct broker state and flags the audit interval degraded —
broker order history is temporarily authoritative (B04/B06).
"""
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import timezone

from benchmark_alpha.rethink_store import AlphaUnavailableError

_DEFAULT_THRESHOLDS = {
    "equity_pct": 0.02,       # broker vs persisted equity relative mismatch
    "mark_pct": 0.05,         # per-symbol broker vs persisted mark mismatch
    "consecutive_critical": 3,
}


@dataclass(frozen=True)
class WatchdogResult:
    status: str                       # OK | ALERT | CRITICAL_ACTION
    mismatches: tuple = ()
    actions: tuple = ()
    degraded_audit: bool = False
    consecutive_mismatches: int = 0


@dataclass(frozen=True)
class ControlHealth:
    """Durable watchdog evidence consumed by the unified live-order gate."""

    instance_id: str
    status: str
    observed_at: object
    result_status: str
    degraded_audit: bool
    evidence_hash: str = ""

    def __post_init__(self):
        instance_id = str(self.instance_id or "").strip()
        status = str(self.status or "").strip().lower()
        if not instance_id:
            raise ValueError("instance_id is required")
        if status not in {"healthy", "unhealthy"}:
            raise ValueError("status must be healthy or unhealthy")
        observed = self.observed_at
        if getattr(observed, "tzinfo", None) is None:
            raise ValueError("observed_at must be timezone-aware")
        observed = observed.astimezone(timezone.utc)
        payload = {
            "instance_id": instance_id,
            "status": status,
            "observed_at": observed.isoformat(),
            "result_status": str(self.result_status or ""),
            "degraded_audit": bool(self.degraded_audit),
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        supplied = str(self.evidence_hash or "")
        if supplied and supplied != digest:
            raise ValueError("control-health evidence hash mismatch")
        object.__setattr__(self, "instance_id", instance_id)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "observed_at", observed)
        object.__setattr__(self, "result_status", payload["result_status"])
        object.__setattr__(self, "degraded_audit", payload["degraded_audit"])
        object.__setattr__(self, "evidence_hash", digest)

    def to_doc(self):
        return {
            "instance_id": self.instance_id,
            "status": self.status,
            "observed_at": self.observed_at.isoformat(),
            "result_status": self.result_status,
            "degraded_audit": self.degraded_audit,
            "evidence_hash": self.evidence_hash,
        }


class AlphaWatchdog:
    def __init__(self, *, probe, rethink_store, thresholds, instance_id,
                 reduce_executor=None, health_writer=None):
        self._probe = probe
        self._store = rethink_store
        self._thresholds = {**_DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._instance_id = str(instance_id)
        self._reduce_executor = reduce_executor
        self._health_writer = health_writer
        self._consecutive = 0
        self._missing_view_polls = 0

    def _finish(self, now, result):
        if self._health_writer is not None:
            healthy = result.status == "OK" and not result.degraded_audit
            self._health_writer(ControlHealth(
                instance_id=self._instance_id,
                status="healthy" if healthy else "unhealthy",
                observed_at=now,
                result_status=result.status,
                degraded_audit=result.degraded_audit,
            ))
        return result

    def record_failure(self, now, exc):
        """Persist a failed poll so broker-side freshness fails closed."""
        if self._health_writer is None:
            return
        self._health_writer(ControlHealth(
            instance_id=self._instance_id,
            status="unhealthy",
            observed_at=now,
            result_status=f"ERROR:{type(exc).__name__}",
            degraded_audit=True,
        ))

    def _persisted_view(self):
        """Broker process's persisted equity/marks; None + degraded on outage."""
        try:
            record = self._store.get_state(f"live_snapshot:{self._instance_id}")
        except AlphaUnavailableError:
            return None, True
        if record is None:
            return None, False
        return record.payload or {}, False

    def poll_once(self, now):
        broker_equity = float(self._probe.broker_equity())
        broker_positions = self._probe.broker_positions() or {}
        persisted, degraded = self._persisted_view()

        mismatches = []
        if persisted is None and not degraded:
            # The broker process is expected to persist live_snapshot:<id>
            # every snapshot tick. A healthy store with NO view means the
            # comparison is fail-open — escalate to ALERT after N polls
            # instead of silently reporting OK forever (audit 2026-07-18).
            self._missing_view_polls += 1
            if self._missing_view_polls >= self._thresholds["consecutive_critical"]:
                return self._finish(now, WatchdogResult(
                    status="ALERT",
                    mismatches=({"kind": "missing_persisted_view",
                                 "polls": self._missing_view_polls},),
                    degraded_audit=True,
                    consecutive_mismatches=self._consecutive))
            return self._finish(
                now, WatchdogResult(status="OK", degraded_audit=True)
            )
        if persisted is not None:
            self._missing_view_polls = 0

        if persisted:
            persisted_equity = float(persisted.get("equity") or 0.0)
            if persisted_equity > 0:
                gap = abs(broker_equity - persisted_equity) / persisted_equity
                if gap > self._thresholds["equity_pct"]:
                    mismatches.append({
                        "kind": "equity", "broker": broker_equity,
                        "persisted": persisted_equity, "relative_gap": gap})
            # Position-set divergence between the persisted view and DIRECT
            # broker truth (audit 2026-07-18: the old per-symbol loop was
            # dead code and no mark check ever fired).
            persisted_marks = persisted.get("marks") or {}
            held = {str(s).upper() for s, q in broker_positions.items()
                    if float(q or 0.0) > 0}
            marked = {str(s).upper() for s, p in persisted_marks.items() if p}
            for sym in sorted(held - marked):
                mismatches.append({"kind": "unmarked_position", "symbol": sym})
            for sym in sorted(marked - held):
                mismatches.append({"kind": "mark_without_position",
                                   "symbol": sym})

        if mismatches:
            self._consecutive += 1
        else:
            self._consecutive = 0

        if mismatches and self._consecutive >= self._thresholds["consecutive_critical"]:
            # Confirmed critical divergence: cancel entries, halt, reduce —
            # over broker-reported holdings ONLY.
            self._probe.cancel_entry_orders()
            self._probe.halt_instance()
            targets = {sym: 0.0 for sym, qty in broker_positions.items()
                       if float(qty or 0.0) > 0}
            episode_id = f"wd-{self._instance_id}-{now.strftime('%Y%m%dT%H%M%SZ')}"
            reduced = False
            if self._reduce_executor is not None:
                self._reduce_executor(episode_id, targets)
                reduced = True
            result = WatchdogResult(
                status="CRITICAL_ACTION", mismatches=tuple(mismatches),
                actions=(
                    ("cancel_entries", "halt", "reduce_only")
                    if reduced
                    else ("cancel_entries", "halt")
                ),
                degraded_audit=degraded,
                consecutive_mismatches=self._consecutive)
            self._consecutive = 0
            return self._finish(now, result)
        if mismatches:
            return self._finish(now, WatchdogResult(
                status="ALERT", mismatches=tuple(mismatches),
                degraded_audit=degraded,
                consecutive_mismatches=self._consecutive))
        return self._finish(
            now, WatchdogResult(status="OK", degraded_audit=degraded)
        )


def build_watchdog_command(instance_id, python_executable=None):
    """Pure command builder for the watchdog subprocess. Secrets are NEVER on
    the command line — the subprocess resolves credentials from its own
    environment/instance row, mirroring start_broker's contract."""
    return [
        python_executable or sys.executable,
        "-m", "benchmark_alpha.watchdog_main",
        "--instance-id", str(instance_id),
    ]
