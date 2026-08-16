"""The hypothesis ledger — pre-registered claims with predicted effects.

A hypothesis is registered with its PREDICTION before the experiment runs, and
the prediction is part of the content hash. That ordering is what makes the
ledger evidence rather than a narrative: you cannot discover the direction after
seeing the result and call it a forecast.

Rejected hypotheses are never deleted. They are the generator's memory — without
them it re-proposes the entry-gate idea this project has already disproved
twice, and the operator has no way to see that it did.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from self_learning.types import content_id

STATUS_PROPOSED = "proposed"
STATUS_TESTING = "testing"
STATUS_CONFIRMED = "confirmed"
STATUS_REJECTED = "rejected"
STATUS_INERT = "inert"
STATUS_WITHDRAWN = "withdrawn"

# Statuses that mean "do not re-propose this".
CLOSED = frozenset({STATUS_CONFIRMED, STATUS_REJECTED, STATUS_INERT,
                    STATUS_WITHDRAWN})

DIRECTIONS = frozenset({"increase", "decrease"})


@dataclass(frozen=True)
class Hypothesis:
    finding_id: str
    target: str
    claim: str
    mechanism: str
    lever_keys: tuple
    predicted_direction: str        # "increase" | "decrease"
    predicted_min_pp: float
    predicted_max_pp: float
    author_model: str = ""
    author_role: str = "generator"
    prompt_hash: str = ""
    cost_usd: float = 0.0
    created_at: str = ""
    status: str = STATUS_PROPOSED
    status_reason: str = ""
    experiment_ids: tuple = field(default_factory=tuple)

    @property
    def id(self) -> str:
        # The PREDICTION is inside the identity. Change what you predicted and
        # it is a different hypothesis, which is what stops a forecast being
        # edited to match the outcome.
        return content_id("hypothesis", {
            "target": self.target,
            "lever_keys": sorted(str(k) for k in self.lever_keys),
            "predicted_direction": self.predicted_direction,
            "claim": self.claim,
        })

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED

    def to_doc(self) -> dict:
        return {
            "id": self.id, "finding_id": self.finding_id,
            "target": self.target, "claim": self.claim,
            "mechanism": self.mechanism,
            "lever_keys": list(self.lever_keys),
            "predicted_direction": self.predicted_direction,
            "predicted_min_pp": self.predicted_min_pp,
            "predicted_max_pp": self.predicted_max_pp,
            "author_model": self.author_model, "author_role": self.author_role,
            "prompt_hash": self.prompt_hash,
            "cost_usd": round(self.cost_usd, 6),
            "created_at": self.created_at, "status": self.status,
            "status_reason": self.status_reason,
            "experiment_ids": list(self.experiment_ids),
        }


class HypothesisError(ValueError):
    pass


def build(payload: dict, *, finding_id: str, target: str, author_model: str = "",
          prompt_hash: str = "", cost_usd: float = 0.0,
          created_at: str = "") -> Hypothesis:
    """Validate an LLM's proposal into a Hypothesis, or refuse it.

    Refusal is the point. A proposal without a direction and a magnitude is not
    a hypothesis, it is an opinion — and an opinion cannot be wrong, which makes
    it useless as evidence. The generator is required to commit.
    """
    payload = payload or {}
    claim = str(payload.get("claim") or "").strip()
    mechanism = str(payload.get("mechanism") or "").strip()
    levers = [str(k).strip() for k in (payload.get("lever_keys") or []) if str(k).strip()]
    direction = str(payload.get("predicted_direction") or "").strip().lower()

    if not claim:
        raise HypothesisError("a hypothesis needs a claim")
    if not mechanism:
        raise HypothesisError(
            "a hypothesis needs a MECHANISM — 'it will help' is not testable, "
            "and without a mechanism a null result teaches nothing")
    if not levers:
        raise HypothesisError("a hypothesis needs at least one lever to change")
    if direction not in DIRECTIONS:
        raise HypothesisError(
            f"predicted_direction must be one of {sorted(DIRECTIONS)}; a "
            f"prediction without a direction cannot be wrong")

    try:
        low = float(payload.get("predicted_min_pp"))
        high = float(payload.get("predicted_max_pp"))
    except (TypeError, ValueError):
        raise HypothesisError(
            "predicted_min_pp and predicted_max_pp are required — a magnitude "
            "is what makes the prediction falsifiable against the noise floor")
    if high < low:
        low, high = high, low

    return Hypothesis(
        finding_id=str(finding_id), target=str(target), claim=claim,
        mechanism=mechanism, lever_keys=tuple(levers),
        predicted_direction=direction, predicted_min_pp=low,
        predicted_max_pp=high, author_model=str(author_model),
        prompt_hash=str(prompt_hash), cost_usd=float(cost_usd or 0.0),
        created_at=str(created_at),
    )


def already_proposed(hypothesis: Hypothesis, ledger) -> str:
    """Reason to skip, or empty string.

    Deduplication is against the WHOLE ledger, not just the confirmed part. If
    it only checked survivors, every rejected idea would come back round after
    round and the loop would never converge — the same trap that makes a
    find-then-verify sweep run forever.
    """
    for row in (ledger or []):
        if str((row or {}).get("id")) != hypothesis.id:
            continue
        status = str(row.get("status") or STATUS_PROPOSED)
        if status in CLOSED:
            return (f"already {status}: {row.get('status_reason') or 'no reason recorded'}")
        return f"already in the ledger with status {status}"
    return ""


def prior_rejections(ledger, *, target=None, limit: int = 20) -> list:
    """What the generator must be shown so it stops re-proposing the disproved."""
    rows = [row for row in (ledger or [])
            if str((row or {}).get("status")) in CLOSED
            and (target is None or str(row.get("target")) == str(target))]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return rows[:max(1, int(limit))]
