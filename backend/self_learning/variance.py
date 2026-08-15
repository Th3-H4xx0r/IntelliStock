"""Guard 3 — assert an input actually varies before learning from it.

The selection signal in this project was a CONSTANT for months: 717 of 723 buy
candidates scored exactly +1.000, which made every A/B ever run a measurement of
noise, because a 97%-equal-weight allocator returns the same book whatever is
tuned. Nothing detected it. This module is the detector, and a saturated field
raises a DEFECT FINDING rather than becoming a feature the learner trusts.
"""
from __future__ import annotations

from collections import Counter

from self_learning.types import VarianceReport


def assess_variance(values, *, field_name: str, threshold: float = 0.95,
                    min_n: int = 30) -> VarianceReport:
    """`saturated` when one value holds >= `threshold` of a sample of at least
    `min_n`. The floor matters: five identical values is a small sample, not a
    constant signal, and declaring it saturated would fire on every fresh run.
    """
    present = [v for v in (values or []) if v is not None]
    n = len(present)
    if n == 0:
        return VarianceReport(field_name=field_name, n=0, distinct=0,
                              top_value=None, top_share=0.0, saturated=False)
    counts = Counter(present)
    top_value, top_count = counts.most_common(1)[0]
    top_share = top_count / float(n)
    return VarianceReport(
        field_name=field_name, n=n, distinct=len(counts), top_value=top_value,
        top_share=top_share, saturated=bool(n >= min_n and top_share >= threshold),
    )


def assess_observations(observations, *, field_name: str = "normalized_score",
                        **kwargs) -> VarianceReport:
    """Run the assertion over an attribute of a list of `Observation`s."""
    values = [getattr(o, field_name, None) for o in (observations or [])]
    return assess_variance(values, field_name=field_name, **kwargs)
