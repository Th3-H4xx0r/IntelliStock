"""Guard 1 — the measured noise floor, and the acceptance rule built on it.

This project ran A/Bs for months without ever measuring its own dispersion. Two
runs of the SAME window under the same config differed by ~16pp. Against that,
almost every "lever" it shipped was inside the noise, which is how thirteen
inert levers and one max-of-N "SPY-beating" artifact happened.

Four rules, each closing a hole an adversarial sweep found in the first cut:

* A target with **no measured floor cannot promote anything.**
* A floor of **zero is not a floor.** Three identical repeats made
  `floor_pp = 0.0`, `bar = 0.0`, and every effect above zero "cleared" it. In
  this codebase identical repeats have a known cause — a forgotten
  `history_scope_salt`, which produced a documented BYTE-IDENTICAL rerun — so a
  suspiciously tight floor means the measurement is broken, not that the system
  is quiet.
* Acceptance is **directional.** `abs(effect) > bar` with a consistent sign
  happily accepted a lever that lost 8pp in every window. The caller states
  which way it expected the effect to go, and a lever that moves the other way
  is rejected however cleanly it did so.
* **Repeats are checked, not averaged.** The founding observation of this whole
  subsystem is same-window dispersion; collapsing each window's repeats to a
  mean before the check makes it invisible.

Pure: no I/O, no clock.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_MARGIN = 1.5
MIN_FLOOR_SAMPLES = 3

# A dispersion tighter than this is not credible for a strategy backtest here,
# and its known cause is a measurement fault rather than a quiet system.
MIN_CREDIBLE_FLOOR_PP = 0.05

INCREASE = "increase"
DECREASE = "decrease"


@dataclass(frozen=True)
class NoiseFloor:
    target: str
    window_class: str
    n: int
    floor_pp: float
    mean_pp: float
    measured: bool
    reason: str = ""

    def to_doc(self) -> dict:
        return {
            "id": f"{self.target}|{self.window_class}",
            "target": self.target, "window_class": self.window_class,
            "n": self.n, "floor_pp": round(self.floor_pp, 6),
            "mean_pp": round(self.mean_pp, 6), "measured": self.measured,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    reason: str
    effect_pp: float
    floor_pp: float
    bar_pp: float
    sign_consistent: int
    n: int

    def to_doc(self) -> dict:
        return {
            "accepted": self.accepted, "reason": self.reason,
            "effect_pp": round(self.effect_pp, 6),
            "floor_pp": round(self.floor_pp, 6),
            "bar_pp": round(self.bar_pp, 6),
            "sign_consistent": self.sign_consistent, "n": self.n,
        }


def _finite(values):
    out = []
    for value in (values or []):
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            out.append(number)
    return out


def estimate_floor(returns_pp, *, target: str, window_class: str,
                   run_ids=None) -> NoiseFloor:
    """The dispersion of repeat runs of an IDENTICAL config.

    The floor is the full observed spread (max - min), not a standard
    deviation: with three or four repeats a sample sigma is itself extremely
    noisy and reads far too small.

    `run_ids`, when supplied, must be DISTINCT. Reading one cached result three
    times produces a perfect zero floor and unlocks every lever, and this repo
    has shipped byte-identical reruns before.
    """
    samples = _finite(returns_pp)
    n = len(samples)
    mean = (sum(samples) / n) if n else 0.0

    if n < MIN_FLOOR_SAMPLES:
        return NoiseFloor(target=target, window_class=window_class, n=n,
                          floor_pp=0.0, mean_pp=mean, measured=False,
                          reason=f"{n} repeat(s); {MIN_FLOOR_SAMPLES} required")

    if run_ids is not None:
        ids = [str(i) for i in run_ids if str(i)]
        if len(set(ids)) < n:
            return NoiseFloor(
                target=target, window_class=window_class, n=n, floor_pp=0.0,
                mean_pp=mean, measured=False,
                reason=("repeat run ids are not distinct — the same result was "
                        "counted more than once, which fabricates a tight floor"))

    spread = max(samples) - min(samples)
    if spread < MIN_CREDIBLE_FLOOR_PP:
        return NoiseFloor(
            target=target, window_class=window_class, n=n, floor_pp=spread,
            mean_pp=mean, measured=False,
            reason=(f"repeats differed by only {spread:.4f}pp, which is not a "
                    f"credible dispersion for this system — check that each "
                    f"repeat used a fresh history_scope_salt; a shared salt "
                    f"produces byte-identical reruns"))

    return NoiseFloor(target=target, window_class=window_class, n=n,
                      floor_pp=spread, mean_pp=mean, measured=True)


def _flatten_effects(effects_pp):
    """Accept per-window scalars OR per-window lists of repeats.

    Returns (per_window_means, worst_within_window_spread, n_windows).
    """
    windows, worst_spread = [], 0.0
    for entry in (effects_pp or []):
        if isinstance(entry, (list, tuple)):
            repeats = _finite(entry)
            if not repeats:
                continue
            windows.append(sum(repeats) / len(repeats))
            worst_spread = max(worst_spread, max(repeats) - min(repeats))
        else:
            value = _finite([entry])
            if value:
                windows.append(value[0])
    return windows, worst_spread, len(windows)


def acceptance(effects_pp, floor: NoiseFloor, *, margin: float = DEFAULT_MARGIN,
               sign_k=None, expected_direction=None) -> Verdict:
    """Does a set of paired per-window effects clear this target's floor?

    `effects_pp` is one paired delta per window (treatment minus control), or a
    LIST of repeat deltas per window — pass the repeats when you have them, so
    same-window dispersion is checked rather than averaged away.

    `expected_direction` is what the hypothesis predicted. Supplying it is what
    keeps this from being a two-sided test consumed as a promotion gate.
    """
    try:
        margin = float(margin)
    except (TypeError, ValueError):
        margin = DEFAULT_MARGIN
    if not math.isfinite(margin) or margin <= 0:
        return Verdict(False,
                       f"margin must be positive (got {margin!r}) — a margin of "
                       f"zero or less disables the guard entirely",
                       0.0, getattr(floor, "floor_pp", 0.0), 0.0, 0, 0)

    windows, worst_within, n = _flatten_effects(effects_pp)

    if floor is None or not floor.measured:
        why = getattr(floor, "reason", "") or "no dispersion has been measured"
        return Verdict(False,
                       f"no usable noise floor for this target ({why}) — the "
                       f"only permitted experiment is the dispersion "
                       f"measurement", 0.0, getattr(floor, "floor_pp", 0.0),
                       0.0, 0, n)
    if n == 0:
        return Verdict(False, "no effects supplied", 0.0, floor.floor_pp,
                       0.0, 0, 0)

    bar = floor.floor_pp * margin
    mean_effect = sum(windows) / float(n)
    positive = sum(1 for e in windows if e > 0)
    negative = sum(1 for e in windows if e < 0)
    consistent = max(positive, negative)

    if sign_k is None:
        required = n
    else:
        try:
            required = int(sign_k)
        except (TypeError, ValueError):
            return Verdict(False, f"sign_k must be an integer (got {sign_k!r})",
                           mean_effect, floor.floor_pp, bar, consistent, n)
        if required < 1:
            return Verdict(False,
                           "sign_k must be at least 1 — a sign requirement of "
                           "zero disables the consistency check",
                           mean_effect, floor.floor_pp, bar, consistent, n)
        required = min(required, n)

    # Same-window repeats that disagree by more than the floor mean the effect
    # is not separable from the run-to-run noise, whatever the mean says.
    if worst_within > floor.floor_pp:
        return Verdict(False,
                       f"repeats of a single window disagreed by "
                       f"{worst_within:.2f}pp, which exceeds the {floor.floor_pp:.2f}pp "
                       f"floor — the effect is not separable from rerun noise",
                       mean_effect, floor.floor_pp, bar, consistent, n)

    if expected_direction in (INCREASE, DECREASE):
        moved_up = mean_effect > 0
        wanted_up = expected_direction == INCREASE
        if moved_up != wanted_up:
            return Verdict(False,
                           f"predicted {expected_direction} but measured "
                           f"{mean_effect:+.2f}pp — a lever that moves the "
                           f"wrong way is not accepted however consistently "
                           f"it does so",
                           mean_effect, floor.floor_pp, bar, consistent, n)

    if abs(mean_effect) <= bar:
        return Verdict(False,
                       f"effect {mean_effect:+.2f}pp is inside the noise floor "
                       f"({floor.floor_pp:.2f}pp x {margin} = {bar:.2f}pp)",
                       mean_effect, floor.floor_pp, bar, consistent, n)
    if consistent < required:
        return Verdict(False,
                       f"sign held in only {consistent} of {n} windows "
                       f"(need {required}) — a lever that helps some windows "
                       f"and hurts others has not been shown to work",
                       mean_effect, floor.floor_pp, bar, consistent, n)
    # A STRICT majority must agree with the mean, or one outlier is carrying it.
    # A tie is not agreement.
    if mean_effect > 0 and positive <= negative:
        return Verdict(False,
                       "the mean is positive but no majority of windows is "
                       "— one outlier is carrying the result",
                       mean_effect, floor.floor_pp, bar, consistent, n)
    if mean_effect < 0 and negative <= positive:
        return Verdict(False,
                       "the mean is negative but no majority of windows is "
                       "— one outlier is carrying the result",
                       mean_effect, floor.floor_pp, bar, consistent, n)
    return Verdict(True,
                   f"effect {mean_effect:+.2f}pp clears {bar:.2f}pp and held "
                   f"its sign in {consistent}/{n} windows",
                   mean_effect, floor.floor_pp, bar, consistent, n)


def can_promote(floor: NoiseFloor) -> bool:
    """Structural precondition: unmeasured target, nothing promotable."""
    return bool(floor is not None and floor.measured)
