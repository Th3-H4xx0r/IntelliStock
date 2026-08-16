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

from dataclasses import dataclass, fields, replace

from self_learning.permissions import LIVE_RUNGS, RUNGS
from self_learning.timeline import to_naive_utc
from self_learning.types import content_id

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED_AUTO = "auto_approved"
CANCELLED = "cancelled"

OPEN = frozenset({PENDING})
ALL_STATUSES = frozenset({PENDING, APPROVED, REJECTED, EXPIRED_AUTO, CANCELLED})

# LIVE_RUNGS is IMPORTED, not redeclared. Two copies of the one constant that
# stands between silence and real money is one copy too many — and a lowercase
# or whitespace-padded rung slipped past the local set entirely, so
# `rung="live_full"` auto-proceeded.


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

    def __post_init__(self):
        # An unknown rung is not a rung. Without this, `live_full` (lowercase)
        # read as sub-live and auto-proceeded on a real-money change.
        if self.rung not in RUNGS:
            raise ValueError(
                f"unknown rung {self.rung!r} — must be one of {sorted(RUNGS)}. "
                f"An unrecognised rung would default to sub-live and could "
                f"auto-proceed.")
        if self.status not in ALL_STATUSES:
            raise ValueError(f"unknown approval status {self.status!r}")

    @property
    def id(self) -> str:
        # `action_class` and `document_id` participate. Without them two
        # proposals from one hypothesis — say a config lever on 179 and a
        # universe change on 404 — collapsed to one row under
        # conflict="update", and a single operator click answered a question
        # they were never shown, on a live rung.
        return content_id("approval", {
            "hypothesis_id": self.hypothesis_id,
            "experiment_id": self.experiment_id,
            "rung": self.rung,
            "action_class": self.action_class,
            "document_id": self.document_id,
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
    return replace(approval, status=decision, decided_at=str(now_iso),
                   decided_by=str(actor), reason=str(reason or ""))


def auto_proceed(approval, *, now_iso) -> Approval:
    """Mark a sub-live approval as having auto-proceeded on timeout."""
    if approval.holds_forever:
        raise ValueError(
            "a live-rung approval never auto-proceeds — silence is not consent "
            "for real money")
    return replace(approval, status=EXPIRED_AUTO, decided_at=str(now_iso),
                   decided_by="timeout",
                   reason="no answer before the configured timeout")


def normalise_status(value) -> str:
    """Fold a stored status into the canonical set.

    `''`, None and `'Pending'` all meant pending to a human and none of them
    matched. The `'Pending'` case was the worst: a LIVE row that held forever
    AND never appeared in the queue, so the operator could neither see nor
    clear it.
    """
    text = str(value or "").strip().lower()
    return text if text in ALL_STATUSES else PENDING


def from_doc(row) -> "Approval":
    """Rebuild an Approval from a stored document, tolerating extra keys.

    Filtering to the declared fields rather than blacklisting `id` and
    `holds_forever` means a future `schema_version` (which every other record
    here emits) cannot break the operator's only approve path.
    """
    row = dict(row or {})
    row["status"] = normalise_status(row.get("status"))
    row["changes"] = tuple(tuple(c) for c in (row.get("changes") or []))
    allowed = {f.name for f in fields(Approval)}
    return Approval(**{k: v for k, v in row.items() if k in allowed})


def pending(rows) -> list:
    return [row for row in (rows or [])
            if normalise_status((row or {}).get("status")) in OPEN]


def queue_view(rows) -> dict:
    """What the tab's first section renders. Live-rung items are pinned because
    they are the ones that will wait indefinitely for you."""
    open_rows = pending(rows)
    live = [r for r in open_rows if str(r.get("rung") or "") in LIVE_RUNGS]
    other = [r for r in open_rows if str(r.get("rung") or "") not in LIVE_RUNGS]
    live.sort(key=lambda r: str(r.get("requested_at") or ""))
    other.sort(key=lambda r: str(r.get("requested_at") or ""))
    return {
        "pending": live + other,
        "pending_count": len(open_rows),
        "live_pending_count": len(live),
        "blocking": bool(live),
    }
