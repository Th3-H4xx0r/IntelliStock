"""Guard 3 — assert an input actually varies before learning from it.

The selection signal in this project was a CONSTANT for months: 717 of 723 buy
candidates scored exactly +1.000, which made every A/B ever run a measurement of
noise, because a 97%-equal-weight allocator returns the same book whatever is
tuned. Nothing detected it. This module is the detector, and a saturated field
raises a DEFECT FINDING rather than becoming a feature the learner trusts.

Two normalisations exist because without them the guard INVERTS — it reports a
perfectly constant field as healthy, which is worse than not having the guard:

* **NaN is missing, not a value.** Distinct NaN objects never compare equal, so
  a `Counter` gives each its own key: 50 NaN scores read as "50 distinct values,
  top_share 0.02, healthy". `float("nan")` is reachable — `observers._score()`
  does a bare `float(raw)`, and `broker.py:7578` does not validate a
  tuple-returning strategy's score.
* **`True`, `1` and `1.0` are one key to `Counter`** (their hashes are equal),
  so `distinct` undercounts and `top_value` becomes whichever type appeared
  first — rendering a numeric field as `True` in operator-facing text. Numbers
  are canonicalised to float and bools kept distinct from them.
"""
from __future__ import annotations

import math
from collections import Counter

from self_learning.types import VarianceReport


def _canonical(value):
    """Return a `(kind, value)` counting key, or None when the value is missing.

    The kind tag is not decoration. `True == 1 == 1.0` and their hashes are
    equal, so a bare `Counter` merges them into one entry whose `top_value` is
    whichever type appeared first — rendering a numeric field as `True` in
    operator-facing text and undercounting `distinct`. Tagging separates them.

    Non-finite floats (NaN, ±inf) are missing: they are the absence of a score,
    not a score.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, (int, float)):
        numeric = float(value)
        if not math.isfinite(numeric):
            return None
        return ("num", numeric)
    return ("other", value)


def assess_variance(values, *, field_name: str, threshold: float = 0.95,
                    min_n: int = 30) -> VarianceReport:
    """`saturated` when one value holds >= `threshold` of a sample of at least
    `min_n`. The floor matters: five identical values is a small sample, not a
    constant signal, and declaring it saturated would fire on every fresh run.
    """
    present = []
    for raw in (values or []):
        try:
            canonical = _canonical(raw)
        except (TypeError, ValueError):
            continue
        if canonical is None:
            continue
        try:
            hash(canonical)
        except TypeError:
            # An unhashable score (a list, say) would blow up Counter and take
            # the whole pass down. Skip it rather than crash the observer.
            continue
        present.append(canonical)

    n = len(present)
    if n == 0:
        return VarianceReport(field_name=field_name, n=0, distinct=0,
                              top_value=None, top_share=0.0, saturated=False)
    counts = Counter(present)
    # Deterministic tie-break: `most_common` returns the first-inserted key
    # among equal counts, so two runs of the same data could report different
    # top_values — and top_value reaches the finding title, whose hash is the
    # thread identity.
    top_key, top_count = max(counts.items(),
                             key=lambda kv: (kv[1], repr(kv[0])))
    top_share = top_count / float(n)
    return VarianceReport(
        field_name=field_name, n=n, distinct=len(counts), top_value=top_key[1],
        top_share=top_share, saturated=bool(n >= min_n and top_share >= threshold),
    )


def assess_observations(observations, *, field_name: str = "normalized_score",
                        **kwargs) -> VarianceReport:
    """Run the assertion over an attribute of a list of `Observation`s."""
    values = [getattr(o, field_name, None) for o in (observations or [])]
    return assess_variance(values, field_name=field_name, **kwargs)
