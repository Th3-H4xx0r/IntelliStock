"""Turn guard results into findings — the thread roots of the subsystem.

A finding's identity is (kind, target, title) and deliberately excludes the run
that detected it: re-detecting the same defect on the same target must update
ONE thread rather than spawn a new thread per run, or the feed becomes a
scrolling wall of the same fact.
"""
from __future__ import annotations

from self_learning.types import Finding
from self_learning.variance import assess_observations

# A run that decides a lot of buys and executes almost none is the documented
# failure of this codebase: in one window 100% of the 52 names that moved 30%+
# were discovered and 0% were bought. Thresholds are deliberately loose — this
# raises a QUESTION, it does not diagnose.
_MIN_BUY_DECIDED = 20
_CONVERSION_FLOOR = 0.25


def finding_from_variance(report, *, target: str, run_id: str,
                          detected_at: str):
    if report is None or not report.saturated:
        return None
    pct = report.top_share * 100.0
    differing = report.n - int(round(report.top_share * report.n))
    return Finding(
        kind="constant_signal",
        target=target,
        severity="high",
        title=f"`{report.field_name}` has no variance on {target}",
        detail=(
            f"{differing} of {report.n} samples differ: {pct:.1f}% take the "
            f"single value {report.top_value!r}. A field this saturated cannot "
            f"rank anything, so any A/B tuned against it measures noise rather "
            f"than the lever."
        ),
        evidence=report.to_doc(),
        detected_at=detected_at,
        run_id=str(run_id),
    )


def finding_from_funnel(summary: dict, *, target: str, run_id: str,
                        detected_at: str, min_buy_decided: int = _MIN_BUY_DECIDED):
    summary = summary or {}
    decided = int(summary.get("buy_decided") or 0)
    executed = int(summary.get("buy_executed") or 0)
    if decided < min_buy_decided:
        return None
    rate = executed / float(decided) if decided else 0.0
    if rate >= _CONVERSION_FLOOR:
        return None
    return Finding(
        kind="buy_conversion",
        target=target,
        severity="high" if rate < 0.1 else "medium",
        title=f"Buy decisions are not converting into fills on {target}",
        detail=(
            f"{executed} of {decided} decided buys executed ({rate * 100:.1f}%). "
            f"The names it declined are recorded; their forward outcomes are "
            f"what tell you whether the refusals cost anything."
        ),
        evidence=dict(summary),
        detected_at=detected_at,
        run_id=str(run_id),
    )


def findings_for_run(observations, summary, *, target: str, run_id: str,
                     detected_at: str) -> list:
    """Every finding a single run supports. Order is stable for the UI."""
    out = []
    variance = finding_from_variance(
        assess_observations(observations), target=target, run_id=run_id,
        detected_at=detected_at)
    if variance is not None:
        out.append(variance)
    funnel = finding_from_funnel(summary, target=target, run_id=run_id,
                                 detected_at=detected_at)
    if funnel is not None:
        out.append(funnel)
    return out
