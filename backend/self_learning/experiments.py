"""Pre-registered experiments.

The spec is hashed and stored BEFORE anything runs, and the acceptance rule is
part of what gets hashed. That ordering is the whole mechanism: a rule chosen
after seeing the result is not a rule, and "max of N runs, reported as the
result" is precisely how this project produced a SPY-beating headline that was
an artifact.

A registered spec is immutable. Stopped and failed runs stay visible so they
still count toward the trial count — a spec that quietly disappears when it
fails turns the ledger into a highlight reel.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from self_learning.noise import DEFAULT_MARGIN
from self_learning.types import content_id
from self_learning.windows import InSampleRegistry, effective_n, is_malformed

STATUS_REGISTERED = "registered"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"
STATUS_STOPPED = "stopped"
STATUS_REFUSED = "refused"

TERMINAL = frozenset({STATUS_COMPLETE, STATUS_FAILED, STATUS_STOPPED,
                      STATUS_REFUSED})


@dataclass(frozen=True)
class ExperimentSpec:
    hypothesis_id: str
    target: str
    window_class: str
    windows: tuple            # ((start, end), ...)
    repeats: int
    treatment_keys: tuple     # the config keys this arm changes
    margin: float = DEFAULT_MARGIN
    sign_k: int | None = None
    registered_at: str = ""
    status: str = STATUS_REGISTERED
    refusal_reason: str = ""
    estimated_cost_usd: float = 0.0
    run_ids: tuple = field(default_factory=tuple)

    @property
    def id(self) -> str:
        # The acceptance rule is INSIDE the identity. Change the rule and it is
        # a different experiment, which is what stops a rule being retrofitted
        # to a result.
        return content_id("experiment", {
            "hypothesis_id": self.hypothesis_id,
            "target": self.target,
            # `window_class` participates: granularity and regime are NOT
            # derivable from the dates, and a floor is never substituted across
            # classes — so a 15m run and a 1h run over the same dates are two
            # experiments, not one, and must not share an id.
            "window_class": self.window_class,
            "windows": [list(w) for w in self.windows],
            "repeats": self.repeats,
            "treatment_keys": list(self.treatment_keys),
            "margin": self.margin,
            "sign_k": self.sign_k,
        })

    @property
    def arm_count(self) -> int:
        """Control + treatment, per window, per repeat."""
        return 2 * len(self.windows) * max(1, self.repeats)

    def to_doc(self) -> dict:
        return {
            "id": self.id, "hypothesis_id": self.hypothesis_id,
            "target": self.target, "window_class": self.window_class,
            "windows": [list(w) for w in self.windows],
            "repeats": self.repeats,
            "treatment_keys": list(self.treatment_keys),
            "margin": self.margin, "sign_k": self.sign_k,
            "registered_at": self.registered_at, "status": self.status,
            "refusal_reason": self.refusal_reason,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "arm_count": self.arm_count,
            "effective_n": effective_n(self.windows),
            "run_ids": list(self.run_ids),
        }


def design(*, hypothesis_id, target, window_class, windows, treatment_keys,
           repeats=2, margin=DEFAULT_MARGIN, sign_k=None, registered_at="",
           cost_per_run_usd=0.70, registry: InSampleRegistry = None,
           record: bool = True) -> ExperimentSpec:
    """Build a spec, REFUSING contaminated windows rather than silently using
    them. A refused spec is still registered — a refusal that leaves no trace
    is one a later agent will repeat.

    Windows are checked against the registry AND against EACH OTHER. Four
    windows one day apart pass every pairwise registry check individually while
    being one window wearing four hats; this project shipped a four-window
    sweep that shared 37 days and reported n=4.

    On success the accepted windows are RECORDED into the registry, so the next
    experiment cannot reuse them as fresh evidence. That obligation used to sit
    on an unnamed caller, which is the same as nowhere.
    """
    if not math.isfinite(float(margin)) or float(margin) <= 0:
        raise ValueError(
            f"margin must be positive (got {margin!r}) — a margin of zero or "
            f"less pre-registers an experiment whose primary gate is disabled")
    if sign_k is not None and int(sign_k) < 1:
        raise ValueError(
            f"sign_k must be at least 1 (got {sign_k!r}) — zero disables the "
            f"sign-consistency check")

    registry = registry or InSampleRegistry()
    clean, refusals = [], []
    for window in (windows or []):
        if not isinstance(window, (list, tuple)) or len(window) < 2:
            refusals.append(f"{window!r}: not a (start, end) pair")
            continue
        start, end = str(window[0]), str(window[1])
        if is_malformed(start, end):
            refusals.append(f"{start}..{end}: malformed window")
            continue
        reason = registry.is_contaminated(start, end)
        if reason:
            refusals.append(f"{start}..{end}: {reason}")
            continue
        # Against the ones already accepted in THIS call, not just the registry.
        overlap = InSampleRegistry(clean).is_contaminated(start, end)
        if overlap:
            refusals.append(f"{start}..{end}: {overlap}")
            continue
        clean.append((start, end))

    status = STATUS_REGISTERED
    refusal_reason = ""
    if not clean:
        status = STATUS_REFUSED
        refusal_reason = ("no usable window remains — " + "; ".join(refusals))
    elif refusals:
        refusal_reason = "dropped windows: " + "; ".join(refusals)

    spec = ExperimentSpec(
        hypothesis_id=str(hypothesis_id), target=str(target),
        window_class=str(window_class), windows=tuple(clean),
        repeats=max(1, int(repeats)), treatment_keys=tuple(treatment_keys or ()),
        margin=float(margin), sign_k=sign_k, registered_at=str(registered_at),
        status=status, refusal_reason=refusal_reason,
    )
    spec = replace(spec,
                   estimated_cost_usd=spec.arm_count * float(cost_per_run_usd))

    if record and status == STATUS_REGISTERED:
        for start, end in clean:
            registry.record(start, end)
    return spec


def is_terminal(status) -> bool:
    return str(status or "") in TERMINAL
