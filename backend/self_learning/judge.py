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


def decide(*, statistical_verdict, proof, llm_verdict=None, llm_reason="",
           model="") -> Judgement:
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

    proof_status = getattr(proof, "status", None)
    if proof_status in ("inert", "unprovable"):
        return Judgement(
            verdict=HOLD,
            reason=(f"execution proof is {proof_status} — the treatment did not "
                    f"reach the decision path, so this is not testable as "
                    f"specified rather than disproved"),
            statistical_accepted=False, llm_verdict=llm, overridden=False,
            model=model)

    accepted = bool(getattr(statistical_verdict, "accepted", False))

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
