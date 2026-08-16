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

import math
import re
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
        """Identity = WHAT CHANGES, not how it was worded.

        Hashing the raw `claim` made dedup cosmetic: a trailing full stop, a
        capital letter, or a paraphrase produced a different id, and the
        generator is shown the exact rejected claim in its prompt and runs at
        temperature 0.6 — i.e. it is being asked to reword. The levers plus the
        direction ARE the change, so they are the identity, and the claim is
        normalised rather than trusted verbatim.
        """
        return content_id("hypothesis", {
            "target": self.target,
            "lever_keys": sorted(str(k) for k in self.lever_keys),
            "predicted_direction": self.predicted_direction,
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


def normalise_claim(text) -> str:
    """Casefold, collapse whitespace, strip trailing punctuation."""
    cleaned = re.sub(r"\s+", " ", str(text or "").strip().casefold())
    return cleaned.rstrip(" .!;:,")


def build(payload: dict, *, finding_id: str, target: str, author_model: str = "",
          prompt_hash: str = "", cost_usd: float = 0.0, created_at: str = "",
          noise_floor=None, known_levers=None) -> Hypothesis:
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
    if not (math.isfinite(low) and math.isfinite(high)):
        raise HypothesisError(
            "predicted magnitudes must be finite — NaN survives every "
            "comparison as False and is not valid JSON, so the row would "
            "either be silently ignored or fail to write at all")
    if high < low:
        low, high = high, low

    # The levers must EXIST. Without this the subsystem can autonomously write
    # a key no strategy reads — a self-authored inert lever, in this repo, on a
    # live document.
    if known_levers is not None:
        known = {str(k) for k in known_levers}
        unknown = [k for k in levers if k not in known]
        if unknown:
            raise HypothesisError(
                f"levers not present in the strategy schema: "
                f"{', '.join(sorted(unknown))}. A lever that does not exist "
                f"cannot be tested, and writing it would author an inert key.")

    # The prediction must be DETECTABLE. A hypothesis predicting 0.1-0.3pp
    # against a 4.1pp floor is unfalsifiable by construction: the experiment
    # cannot resolve it either way, so running it only spends money.
    if noise_floor is not None and getattr(noise_floor, "measured", False):
        floor_pp = float(getattr(noise_floor, "floor_pp", 0.0) or 0.0)
        if max(abs(low), abs(high)) <= floor_pp:
            raise HypothesisError(
                f"predicted {low}..{high}pp is inside the measured "
                f"{floor_pp:.2f}pp noise floor — the experiment could not "
                f"detect this effect even if it were real")

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
