"""The judge — and the floor it cannot climb over.

The operator asked for the LLM to judge promotions. That is workable, but only
under one constraint, and this module IS the constraint: **the statistical
verdict is a floor the judge may lower and never raise.**

So the judge can veto, hold, or demote. It cannot promote something
`noise.acceptance` rejected, and it cannot resurrect a change whose treatment
proved inert. Without that, "LLM as judge" becomes a mechanism for laundering a
failed experiment into a shipped one — which is the exact failure mode that
produced this project's max-of-N "SPY-beating" headline, only automated.

The judge also never sees the generator's reasoning. It is given the
PRE-REGISTERED prediction and the measured result, so the question it answers is
"did this do what was forecast", not "is this argument persuasive".
"""
from __future__ import annotations

from dataclasses import dataclass

CONFIRM = "confirm"
HOLD = "hold"
DEMOTE = "demote"
VETO = "veto"

VERDICTS = frozenset({CONFIRM, HOLD, DEMOTE, VETO})

# Anything the judge says that is not one of these is treated as HOLD: an
# unparseable judgement must not be read as approval.
_SAFE_DEFAULT = HOLD


@dataclass(frozen=True)
class Judgement:
    verdict: str
    reason: str
    statistical_accepted: bool
    llm_verdict: str
    overridden: bool
    model: str = ""

    def to_doc(self) -> dict:
        return {
            "verdict": self.verdict, "reason": self.reason,
            "statistical_accepted": self.statistical_accepted,
            "llm_verdict": self.llm_verdict, "overridden": self.overridden,
            "model": self.model,
        }


def judge_prompt_context(*, hypothesis, statistical_verdict, proof, summary):
    """Exactly what the judge is allowed to see.

    Deliberately excludes the generator's `mechanism` prose and any of its
    reasoning: shown its own argument, a model agrees with it.
    """
    return {
        "target": getattr(hypothesis, "target", ""),
        "claim": getattr(hypothesis, "claim", ""),
        "predicted_direction": getattr(hypothesis, "predicted_direction", ""),
        "predicted_min_pp": getattr(hypothesis, "predicted_min_pp", None),
        "predicted_max_pp": getattr(hypothesis, "predicted_max_pp", None),
        "measured_effect_pp": getattr(statistical_verdict, "effect_pp", None),
        "noise_floor_pp": getattr(statistical_verdict, "floor_pp", None),
        "bar_pp": getattr(statistical_verdict, "bar_pp", None),
        "sign_consistent": getattr(statistical_verdict, "sign_consistent", None),
        "windows": getattr(statistical_verdict, "n", None),
        "execution_proof": getattr(proof, "status", None),
        "run_summary": dict(summary or {}),
    }


def decide(*, statistical_verdict, proof, hypothesis, llm_verdict=None,
           llm_reason="", model="") -> Judgement:
    """Combine the statistics, the execution proof, and the LLM's opinion.

    Order matters and is not negotiable:
      1. An inert or unprovable treatment ends it. Nothing was tested, so there
         is nothing to judge — and calling that "rejected" would retire a
         hypothesis that never ran.
      2. If the statistics rejected it, the best available outcome is HOLD. The
         LLM's opinion can only make that worse.
      3. If the statistics accepted it, the LLM may still veto, hold or demote.
    """
    raw = str(llm_verdict or "").strip().lower()
    llm = raw if raw in VERDICTS else _SAFE_DEFAULT
    llm_unparseable = bool(raw) and raw not in VERDICTS

    # Ask the proof module whether this result may be scored at all, rather
    # than listing statuses here. Listing them meant AMBIGUOUS — added later,
    # for a treatment whose stream moved no more than control-vs-control churn
    # — fell through this check and could be promoted.
    from self_learning.execution_proof import EXECUTED, blocks_scoring

    # An ALLOW-list, not a deny-list. `blocks_scoring` names the statuses that
    # block, so the next status added to execution_proof would silently
    # re-open the hole that check was written to close.
    if getattr(proof, "status", None) != EXECUTED or blocks_scoring(proof):
        proof_status = getattr(proof, "status", None) or "missing"
        return Judgement(
            verdict=HOLD,
            reason=(f"execution proof is {proof_status} — the treatment cannot "
                    f"be attributed to the lever, so this is not testable as "
                    f"specified rather than disproved"),
            statistical_accepted=False, llm_verdict=llm, overridden=False,
            model=model)

    accepted = bool(getattr(statistical_verdict, "accepted", False))

    # Second line of defence on direction. `noise.acceptance` takes an
    # `expected_direction`, but a caller that forgets to pass it yields a
    # two-sided verdict, and a lever that moved hard the WRONG way would then
    # arrive here already "accepted".
    predicted = str(getattr(hypothesis, "predicted_direction", "") or "").lower()
    effect = getattr(statistical_verdict, "effect_pp", None)
    if accepted and predicted not in ("increase", "decrease"):
        # `hypothesis` is required, but a malformed one must not silently
        # disable the check — that is the same forgettable shape one layer up.
        return Judgement(
            verdict=HOLD,
            reason=("the hypothesis carries no predicted direction, so the "
                    "two-sided statistical verdict cannot be checked for sign"),
            statistical_accepted=True, llm_verdict=llm, overridden=True,
            model=model)
    if accepted and effect is not None:
        try:
            effect_value = float(effect)
        except (TypeError, ValueError):
            # A verdict function must not be able to throw.
            return Judgement(
                verdict=HOLD,
                reason=f"measured effect {effect!r} is not numeric",
                statistical_accepted=True, llm_verdict=llm, overridden=True,
                model=model)
        moved_up = effect_value > 0
        if moved_up != (predicted == "increase"):
            return Judgement(
                verdict=HOLD,
                reason=(f"the hypothesis predicted {predicted} but the measured "
                        f"effect was {effect_value:+.2f}pp — accepted by a "
                        f"two-sided test, refused here"),
                statistical_accepted=True, llm_verdict=llm, overridden=True,
                model=model)

    if not accepted:
        stat_reason = getattr(statistical_verdict, "reason", "rejected")
        if llm == CONFIRM:
            # This is the case the whole module exists for.
            return Judgement(
                verdict=HOLD,
                reason=(f"the judge said confirm, but the statistics did not: "
                        f"{stat_reason}. The statistical verdict is a floor — "
                        f"the judge may lower it, never raise it."),
                statistical_accepted=False, llm_verdict=llm, overridden=True,
                model=model)
        return Judgement(
            verdict=llm if llm in (HOLD, DEMOTE, VETO) else HOLD,
            reason=f"statistics rejected: {stat_reason}"
                   + (f"; judge: {llm_reason}" if llm_reason else ""),
            statistical_accepted=False, llm_verdict=llm, overridden=False,
            model=model)

    if llm == CONFIRM:
        return Judgement(
            verdict=CONFIRM,
            reason=(getattr(statistical_verdict, "reason", "accepted")
                    + (f"; judge: {llm_reason}" if llm_reason else "")),
            statistical_accepted=True, llm_verdict=llm, overridden=False,
            model=model)

    note = " (judge response was unparseable, defaulting to hold)" if llm_unparseable else ""
    return Judgement(
        verdict=llm,
        reason=(f"statistics accepted but the judge returned {llm}"
                + (f": {llm_reason}" if llm_reason else "") + note),
        statistical_accepted=True, llm_verdict=llm, overridden=False,
        model=model)


def promotes(judgement: Judgement) -> bool:
    return bool(judgement is not None and judgement.verdict == CONFIRM)
