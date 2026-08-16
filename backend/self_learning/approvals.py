"""The approval queue.

A proposal waits here when its action class and rung are set to "ask first".
The operator's rule, from the design conversation:

    unanswered approvals **hold forever at live rungs, auto-proceed below live**

So the timeout is rung-dependent, and the live rungs have no timeout at all.
That asymmetry is the whole safety property: a missed notification while you
sleep should let a paper-instance nudge proceed, and must never let a
real-money change proceed.

Pure — the caller supplies "now" and the stored rows.
"""
from __future__ import annotations

from dataclasses import dataclass

from self_learning.timeline import to_naive_utc
from self_learning.types import content_id

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED_AUTO = "auto_approved"
CANCELLED = "cancelled"

OPEN = frozenset({PENDING})

# Rungs at which silence is NEVER consent.
LIVE_RUNGS = frozenset({"LIVE_CAPPED", "LIVE_FULL"})


@dataclass(frozen=True)
class Approval:
    hypothesis_id: str
    experiment_id: str
    target: str
    rung: str
    action_class: str
    summary: str
    document_id: str = ""
    changes: tuple = ()
    requested_at: str = ""
    status: str = PENDING
    decided_at: str = ""
    decided_by: str = ""
    reason: str = ""

    @property
    def id(self) -> str:
        return content_id("approval", {
            "hypothesis_id": self.hypothesis_id,
            "experiment_id": self.experiment_id,
            "rung": self.rung,
        })

    @property
    def holds_forever(self) -> bool:
        return self.rung in LIVE_RUNGS

    def to_doc(self) -> dict:
        return {
            "id": self.id, "hypothesis_id": self.hypothesis_id,
            "experiment_id": self.experiment_id, "target": self.target,
            "rung": self.rung, "action_class": self.action_class,
            "summary": self.summary, "document_id": self.document_id,
            "changes": [list(c) for c in self.changes],
            "requested_at": self.requested_at, "status": self.status,
            "decided_at": self.decided_at, "decided_by": self.decided_by,
            "reason": self.reason, "holds_forever": self.holds_forever,
        }


def is_expired(approval, *, now_iso, timeout_hours) -> bool:
    """Whether an unanswered SUB-LIVE approval may auto-proceed.

    A live-rung approval is never expired, whatever the timeout says. If the
    timeout is unset or non-positive, nothing auto-proceeds — silence defaults
    to holding, not to consent.
    """
    if approval is None or approval.status != PENDING:
        return False
    if approval.holds_forever:
        return False
    try:
        hours = float(timeout_hours)
    except (TypeError, ValueError):
        return False
    if hours <= 0:
        return False
    requested = to_naive_utc(approval.requested_at)
    now = to_naive_utc(now_iso)
    if requested is None or now is None:
        return False              # an unparseable request time is not consent
    return (now - requested).total_seconds() >= hours * 3600.0


def resolve(approval, *, decision, now_iso, actor="operator", reason="") -> Approval:
    """Record a human decision. Only a PENDING approval can be decided."""
    if approval is None:
        raise ValueError("no approval to resolve")
    if approval.status != PENDING:
        raise ValueError(
            f"approval is already {approval.status} — re-deciding a closed "
            f"approval would silently reverse an operator's answer")
    decision = str(decision or "").strip().lower()
    if decision not in (APPROVED, REJECTED, CANCELLED):
        raise ValueError(f"decision must be one of "
                         f"{sorted((APPROVED, REJECTED, CANCELLED))}")
    return Approval(**{**approval.__dict__, "status": decision,
                       "decided_at": str(now_iso), "decided_by": str(actor),
                       "reason": str(reason or "")})


def auto_proceed(approval, *, now_iso) -> Approval:
    """Mark a sub-live approval as having auto-proceeded on timeout."""
    if approval.holds_forever:
        raise ValueError(
            "a live-rung approval never auto-proceeds — silence is not consent "
            "for real money")
    return Approval(**{**approval.__dict__, "status": EXPIRED_AUTO,
                       "decided_at": str(now_iso), "decided_by": "timeout",
                       "reason": "no answer before the configured timeout"})


def pending(rows) -> list:
    return [row for row in (rows or [])
            if str((row or {}).get("status") or PENDING) in OPEN]


def queue_view(rows) -> dict:
    """What the tab's first section renders. Live-rung items are pinned because
    they are the ones that will wait indefinitely for you."""
    open_rows = pending(rows)
    live = [r for r in open_rows if r.get("rung") in LIVE_RUNGS]
    other = [r for r in open_rows if r.get("rung") not in LIVE_RUNGS]
    live.sort(key=lambda r: str(r.get("requested_at") or ""))
    other.sort(key=lambda r: str(r.get("requested_at") or ""))
    return {
        "pending": live + other,
        "pending_count": len(open_rows),
        "live_pending_count": len(live),
        "blocking": bool(live),
    }
