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
import sys
from dataclasses import dataclass, field

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


class AlphaWatchdog:
    def __init__(self, *, probe, rethink_store, thresholds, instance_id,
                 reduce_executor):
        self._probe = probe
        self._store = rethink_store
        self._thresholds = {**_DEFAULT_THRESHOLDS, **(thresholds or {})}
        self._instance_id = str(instance_id)
        self._reduce_executor = reduce_executor
        self._consecutive = 0

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
        if persisted:
            persisted_equity = float(persisted.get("equity") or 0.0)
            if persisted_equity > 0:
                gap = abs(broker_equity - persisted_equity) / persisted_equity
                if gap > self._thresholds["equity_pct"]:
                    mismatches.append({
                        "kind": "equity", "broker": broker_equity,
                        "persisted": persisted_equity, "relative_gap": gap})
            persisted_marks = persisted.get("marks") or {}
            for sym, price in persisted_marks.items():
                broker_qty = broker_positions.get(sym)
                if broker_qty is None or not price:
                    continue

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
            self._reduce_executor(episode_id, targets)
            result = WatchdogResult(
                status="CRITICAL_ACTION", mismatches=tuple(mismatches),
                actions=("cancel_entries", "halt", "reduce_only"),
                degraded_audit=degraded,
                consecutive_mismatches=self._consecutive)
            self._consecutive = 0
            return result
        if mismatches:
            return WatchdogResult(
                status="ALERT", mismatches=tuple(mismatches),
                degraded_audit=degraded,
                consecutive_mismatches=self._consecutive)
        return WatchdogResult(status="OK", degraded_audit=degraded)


def build_watchdog_command(instance_id, python_executable=None):
    """Pure command builder for the watchdog subprocess. Secrets are NEVER on
    the command line — the subprocess resolves credentials from its own
    environment/instance row, mirroring start_broker's contract."""
    return [
        python_executable or sys.executable,
        "-m", "benchmark_alpha.watchdog_main",
        "--instance-id", str(instance_id),
    ]
