"""Single-flight backtest lease.

Backtests on this backend run ONE AT A TIME, and a second launch silently
preempts the first — no error, no warning, just a run that quietly stops being
the run you thought you were reading. An autonomous loop that launches whenever
it has an idea would corrupt its own evidence and a human's at the same time.

Two rules:

* One lease at a time, with an expiry so a crashed holder cannot wedge the queue
  forever.
* **A human always wins.** If a run the subsystem did not launch is in flight,
  the lease is refused. The operator's run is never the one that gets preempted.

Pure: the caller supplies "now" and the current state.
"""
from __future__ import annotations

from dataclasses import dataclass

from self_learning.timeline import to_naive_utc

HOLDER = "self_learning"

# A backtest can legitimately run for hours; the lease has to outlast that but
# must not be immortal, or one crash blocks the loop until someone notices.
DEFAULT_TTL_SECONDS = 6 * 3600


@dataclass(frozen=True)
class LeaseDecision:
    granted: bool
    reason: str
    lease: dict | None = None

    def to_doc(self) -> dict:
        return {"granted": self.granted, "reason": self.reason,
                "lease": self.lease}


def _expired(lease, now_iso, ttl_seconds) -> bool:
    """Fail CLOSED on a corrupt lease.

    An unparseable `acquired_at` means somebody IS holding it; treating that as
    expired and granting anyway produces exactly the silent preemption this
    module exists to prevent.
    """
    acquired = to_naive_utc((lease or {}).get("acquired_at"))
    now = to_naive_utc(now_iso)
    if acquired is None or now is None:
        return False
    return (now - acquired).total_seconds() > ttl_seconds


def acquire(*, current_lease, running_backtests, now_iso, experiment_id,
            ttl_seconds: int = DEFAULT_TTL_SECONDS) -> LeaseDecision:
    """`running_backtests` is a list of {id, origin} for runs in flight.

    `BacktestResults` rows carry NO `origin` field, so the subsystem cannot
    recognise its own launch from the row alone. It recognises it from the
    LEASE, which records the run ids it started. Without that the subsystem
    saw its own run, classified it as human, and refused its own next lease
    forever — and because the foreign check runs first, the TTL could never
    rescue it. One self-launched zombie row (this deployment purged 116) would
    have wedged the loop permanently.
    """
    current_lease = current_lease or {}
    expired = bool(current_lease) and _expired(current_lease, now_iso, ttl_seconds)
    own_runs = set()
    if current_lease and not expired:
        own_runs = {str(rid) for rid in (current_lease.get("run_ids") or [])}

    foreign = []
    for row in (running_backtests or []):
        row = row or {}
        run_id = str(row.get("id"))
        if run_id in own_runs:
            continue                       # our own in-flight run
        if str(row.get("origin") or "human") == HOLDER:
            continue                       # explicitly stamped as ours
        foreign.append(row)

    if foreign:
        ids = ", ".join(str((r or {}).get("id")) for r in foreign[:3])
        return LeaseDecision(
            False,
            f"a backtest not launched by the subsystem is in flight ({ids}) — "
            f"launching now would silently preempt it")

    if current_lease and not expired:
        held_by = current_lease.get("experiment_id")
        if held_by == str(experiment_id):
            return LeaseDecision(True, "already held by this experiment",
                                 dict(current_lease))
        return LeaseDecision(
            False, f"lease held by experiment {held_by} until it completes or "
                   f"expires")

    return LeaseDecision(True, "granted", {
        "id": "backtest_lease",
        "holder": HOLDER,
        "experiment_id": str(experiment_id),
        "acquired_at": str(now_iso),
        "ttl_seconds": int(ttl_seconds),
        # Populated as runs are launched, so the holder can recognise its own
        # in-flight work on the next call.
        "run_ids": [],
    })


def record_run(current_lease, run_id) -> dict:
    """Attach a launched run to the lease so the holder recognises it later."""
    lease_doc = dict(current_lease or {})
    runs = [str(r) for r in (lease_doc.get("run_ids") or [])]
    if str(run_id) not in runs:
        runs.append(str(run_id))
    lease_doc["run_ids"] = runs
    return lease_doc


def release(current_lease, *, experiment_id) -> bool:
    """Only the holder may release. Otherwise a stale worker finishing late
    would hand the queue to nobody mid-run."""
    if not current_lease:
        return True
    return str((current_lease or {}).get("experiment_id")) == str(experiment_id)
