"""The promotion ladder, and the asymmetry that makes it safe.

Rungs, in order: BACKTEST -> SHADOW -> PAPER -> LIVE_CAPPED -> LIVE_FULL.

Promotion is slow and conditional. Demotion is fast and unconditional:

* **Promotion** needs a judge CONFIRM, an execution proof that does not block
  scoring, and — at every live rung — a measured noise floor for the target.
* **Demotion** needs only D consecutive evaluations below the bar, and it never
  waits for permission. Reverting is a safety action.

One rung at a time, in both directions. A change that has only ever been seen in
backtest does not jump to paper because it looked good, and a change that starts
failing on live drops to paper rather than being switched off entirely — unless
the breaker fires, which unwinds the whole live tier at once.
"""
from __future__ import annotations

from dataclasses import dataclass

from self_learning.permissions import (
    BACKTEST, LIVE_CAPPED, LIVE_FULL, LIVE_RUNGS, PAPER, RUNGS, SHADOW,
)

PROPOSED = "PROPOSED"
ORDER = (PROPOSED,) + RUNGS

# Default dwell requirements per rung, all operator-editable.
DEFAULT_REQUIREMENTS = {
    BACKTEST: {"windows": 4, "repeats": 2, "min_pass": 3},
    SHADOW: {"sessions": 10},
    PAPER: {"sessions": 20},
    LIVE_CAPPED: {"sessions": 20},
    LIVE_FULL: {"sessions": 0},
}

DEFAULT_DEMOTE_AFTER = 3


@dataclass(frozen=True)
class Transition:
    from_rung: str
    to_rung: str
    direction: str          # "promote" | "demote" | "hold"
    reason: str
    gated: bool = False

    def to_doc(self) -> dict:
        return {"from_rung": self.from_rung, "to_rung": self.to_rung,
                "direction": self.direction, "reason": self.reason,
                "gated": self.gated}


def next_rung(rung):
    try:
        index = ORDER.index(rung)
    except ValueError:
        return None
    return ORDER[index + 1] if index + 1 < len(ORDER) else None


def previous_rung(rung):
    try:
        index = ORDER.index(rung)
    except ValueError:
        return None
    return ORDER[index - 1] if index > 0 else None


def is_live(rung) -> bool:
    return rung in LIVE_RUNGS


def promote(*, current_rung, judgement, proof, floor, sessions_observed=0,
            requirements=None) -> Transition:
    """One rung up, if everything says so. Otherwise hold, with the reason."""
    from self_learning.execution_proof import blocks_scoring
    from self_learning.judge import promotes as judge_promotes
    from self_learning.noise import can_promote

    requirements = requirements or DEFAULT_REQUIREMENTS
    target = next_rung(current_rung)
    if target is None:
        return Transition(current_rung, current_rung, "hold",
                          "already at the top of the ladder")

    if blocks_scoring(proof):
        status = getattr(proof, "status", None) or "missing"
        return Transition(current_rung, current_rung, "hold",
                          f"execution proof is {status} — nothing to promote on")

    if not judge_promotes(judgement):
        verdict = getattr(judgement, "verdict", "missing")
        return Transition(current_rung, current_rung, "hold",
                          f"judge returned {verdict}: "
                          f"{getattr(judgement, 'reason', '')}")

    # The structural rule: an unmeasured target cannot promote anything. Applied
    # at EVERY rung, not just the live ones — promoting into paper on an
    # unmeasured floor just moves the unfounded belief closer to money.
    if not can_promote(floor):
        return Transition(current_rung, current_rung, "hold",
                          f"no measured noise floor for this target "
                          f"({getattr(floor, 'reason', '') or 'never measured'})")

    needed = int((requirements.get(current_rung) or {}).get("sessions", 0) or 0)
    if needed and int(sessions_observed or 0) < needed:
        return Transition(current_rung, current_rung, "hold",
                          f"{sessions_observed} of {needed} session(s) observed "
                          f"at {current_rung}")

    return Transition(current_rung, target, "promote",
                      f"promoted to {target}", gated=is_live(target))


def demote(*, current_rung, consecutive_failures, demote_after=DEFAULT_DEMOTE_AFTER
           ) -> Transition:
    """One rung down after D consecutive sub-bar evaluations.

    Never gated. Permission modes govern applying a change; reverting one is a
    safety action that must not wait for a human to wake up.
    """
    if int(consecutive_failures or 0) < int(demote_after):
        return Transition(current_rung, current_rung, "hold",
                          f"{consecutive_failures} of {demote_after} "
                          f"consecutive failures")
    target = previous_rung(current_rung)
    if target is None:
        return Transition(current_rung, current_rung, "hold",
                          "already at the bottom of the ladder")
    return Transition(current_rung, target, "demote",
                      f"{consecutive_failures} consecutive evaluations below "
                      f"the bar — demoted to {target} and reverted",
                      gated=False)


def breaker(*, drawdown_pct, limit_pct) -> Transition:
    """The automatic circuit breaker.

    Unwinds the ENTIRE live tier at once rather than one rung at a time. A
    breaker that stepped down gracefully would still be holding real money at
    LIVE_CAPPED while the drawdown continued.
    """
    try:
        drawdown = float(drawdown_pct or 0.0)
        limit = float(limit_pct or 0.0)
    except (TypeError, ValueError):
        return Transition(LIVE_FULL, LIVE_FULL, "hold",
                          "drawdown or limit is not numeric")
    if limit <= 0 or drawdown < limit:
        return Transition(LIVE_FULL, LIVE_FULL, "hold",
                          f"attributable drawdown {drawdown:.2f}% is within the "
                          f"{limit:.2f}% limit")
    return Transition(LIVE_FULL, PAPER, "demote",
                      f"BREAKER: attributable drawdown {drawdown:.2f}% breached "
                      f"the {limit:.2f}% limit — the entire live tier is "
                      f"reverted at once", gated=False)
