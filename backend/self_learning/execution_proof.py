"""Guard 2 — did the change actually run?

Thirteen levers have shipped in this codebase without ever executing, and every
one was scored as "no effect". That conclusion compounds: it retires a
hypothesis that was never tested, and teaches whatever reads the ledger that
the idea was tried and failed. So an unexecuted treatment is `INERT` — "not
testable as specified" — and never "no effect".

────────────────────────────────────────────────────────────────────────────
WHY EXACT EQUALITY IS NOT ENOUGH
────────────────────────────────────────────────────────────────────────────
The first version returned INERT only when the two arms' decision streams were
BIT-IDENTICAL. But the founding observation of this whole subsystem is that two
runs of the same window under an identical config differ by ~16pp. If the
returns differ, the streams differ — so a genuinely inert lever produces a
different fingerprint and was reported EXECUTED. One cent of difference on one
bar out of five thousand was indistinguishable from a real lever, on precisely
the target the thirteen inert levers shipped against.

So the proof needs a baseline: run control against ANOTHER control and measure
how much the stream churns on its own. A treatment counts as executed only when
it moves the stream materially more than control-vs-control does, or when a
NAMED COUNTER says so outright. Without a baseline the verdict is explicitly
`AMBIGUOUS` rather than a confident "it ran".
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

INERT = "inert"
EXECUTED = "executed"
UNPROVABLE = "unprovable"
AMBIGUOUS = "ambiguous"

# A treatment must churn the stream this many times more than control-vs-control
# before the difference is attributed to the lever rather than to rerun noise.
DEFAULT_BASELINE_MARGIN = 3.0

_MISSING = "\x00missing"


@dataclass(frozen=True)
class ExecutionProof:
    status: str
    detail: str
    control_fingerprint: str
    treatment_fingerprint: str
    changed_decisions: int
    control_n: int
    treatment_n: int
    changed_fraction: float = 0.0
    baseline_fraction: float | None = None
    counter_evidence: str = ""

    @property
    def executed(self) -> bool:
        return self.status == EXECUTED

    def to_doc(self) -> dict:
        return {
            "status": self.status, "detail": self.detail,
            "control_fingerprint": self.control_fingerprint,
            "treatment_fingerprint": self.treatment_fingerprint,
            "changed_decisions": self.changed_decisions,
            "control_n": self.control_n, "treatment_n": self.treatment_n,
            "changed_fraction": round(self.changed_fraction, 6),
            "baseline_fraction": (None if self.baseline_fraction is None
                                  else round(self.baseline_fraction, 6)),
            "counter_evidence": self.counter_evidence,
        }


def _key(observation):
    """What identifies a decision point across two arms."""
    return (getattr(observation, "as_of", ""),
            getattr(observation, "symbol", ""),
            getattr(observation, "decision", 0))


def _num(value):
    """None and 0.0 must not collapse — 'no fill' is not 'a zero fill'."""
    if value is None:
        return _MISSING
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return _MISSING


def _shape(observation):
    """The part of a decision a lever could plausibly move."""
    return (
        getattr(observation, "decision", 0),
        getattr(observation, "executed", False),
        getattr(observation, "refusal_reason", None),
        _num(getattr(observation, "normalized_score", None)),
        _num(getattr(observation, "filled_notional", None)),
    )


def fingerprint(observations) -> str:
    """A stable digest of an arm's decision stream."""
    rows = sorted((_key(o), _shape(o)) for o in (observations or []))
    digest = hashlib.sha256()
    for key, shape in rows:
        digest.update(repr((key, shape)).encode("utf-8"))
    return digest.hexdigest()


def changed_fraction(control, treatment) -> float:
    """Share of decision points that differ between two arms."""
    control_map = {_key(o): _shape(o) for o in (control or [])}
    treatment_map = {_key(o): _shape(o) for o in (treatment or [])}
    keys = set(control_map) | set(treatment_map)
    if not keys:
        return 0.0
    changed = sum(1 for k in keys if control_map.get(k) != treatment_map.get(k))
    return changed / float(len(keys))


def baseline_from_controls(control_a, control_b) -> float:
    """How much the decision stream churns on its own between two CONTROL runs.

    This is the decision-stream analogue of the return noise floor, and without
    it Guard 2 cannot distinguish a lever from rerun churn.
    """
    return changed_fraction(control_a, control_b)


def prove(control, treatment, *, baseline_fraction=None,
          margin: float = DEFAULT_BASELINE_MARGIN, counters=None) -> ExecutionProof:
    """Compare two arms' observation streams.

    `counters` is `(control_counters, treatment_counters)` — named counters the
    lever itself increments. A differing counter is POSITIVE proof and settles
    the question regardless of stream churn; it is the evidence the spec asks
    adapters to supply ("a differing decision, a named counter, a log line").
    """
    control = list(control or [])
    treatment = list(treatment or [])
    control_fp = fingerprint(control)
    treatment_fp = fingerprint(treatment)

    if not control or not treatment:
        return ExecutionProof(
            UNPROVABLE,
            "one arm produced no observations — nothing ran, so the lever is "
            "neither proven nor disproven",
            control_fp, treatment_fp, 0, len(control), len(treatment))

    control_map = {_key(o): _shape(o) for o in control}
    treatment_map = {_key(o): _shape(o) for o in treatment}
    keys = set(control_map) | set(treatment_map)
    changed = sum(1 for k in keys
                  if control_map.get(k) != treatment_map.get(k))
    fraction = changed / float(len(keys)) if keys else 0.0

    # Positive proof first: a named counter that moved is unambiguous.
    counter_evidence = ""
    if counters:
        control_counters, treatment_counters = counters
        for name in sorted(set(control_counters or {}) | set(treatment_counters or {})):
            before = (control_counters or {}).get(name)
            after = (treatment_counters or {}).get(name)
            if before != after:
                counter_evidence = f"{name}: {before!r} -> {after!r}"
                break
    if counter_evidence:
        return ExecutionProof(
            EXECUTED,
            f"a named counter moved ({counter_evidence}) — positive proof the "
            f"lever executed, independent of stream churn",
            control_fp, treatment_fp, changed, len(control), len(treatment),
            fraction, baseline_fraction, counter_evidence)

    if control_fp == treatment_fp:
        return ExecutionProof(
            INERT,
            "the treatment's decision stream is identical to control — the "
            "lever did not reach the decision path. This is NOT 'no effect': "
            "it is not testable as specified.",
            control_fp, treatment_fp, 0, len(control), len(treatment),
            0.0, baseline_fraction)

    if baseline_fraction is None:
        return ExecutionProof(
            AMBIGUOUS,
            f"{changed} of {len(keys)} decision points differ ({fraction:.1%}), "
            f"but with no control-vs-control baseline this cannot be "
            f"distinguished from ordinary rerun churn — two runs of one window "
            f"differ by ~16pp in this system",
            control_fp, treatment_fp, changed, len(control), len(treatment),
            fraction, None)

    threshold = float(baseline_fraction) * float(margin)
    if fraction <= threshold:
        return ExecutionProof(
            AMBIGUOUS,
            f"the stream moved {fraction:.1%}, within {margin}x the "
            f"{baseline_fraction:.1%} control-vs-control churn — not "
            f"attributable to the lever",
            control_fp, treatment_fp, changed, len(control), len(treatment),
            fraction, baseline_fraction)

    return ExecutionProof(
        EXECUTED,
        f"the stream moved {fraction:.1%} against {baseline_fraction:.1%} "
        f"control-vs-control churn — the lever executed",
        control_fp, treatment_fp, changed, len(control), len(treatment),
        fraction, baseline_fraction)


def blocks_scoring(proof: ExecutionProof) -> bool:
    """True when a result must NOT be scored as evidence about the lever."""
    return proof is None or proof.status in (INERT, UNPROVABLE, AMBIGUOUS)
