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
    differing = report.n - int(round(report.top_share * report.n))
    pct = report.top_share * 100.0
    # Floor the display at 99.9 when some samples DO differ: `:.1f` rounds
    # 99.99 to "100.0", producing the self-contradictory sentence
    # "1 of 10000 samples differ: 100.0% take the single value".
    if differing > 0 and pct > 99.9:
        shown = "99.9+"
    else:
        shown = f"{pct:.1f}"
    return Finding(
        kind="constant_signal",
        target=target,
        severity="high",
        title=f"`{report.field_name}` has no variance on {target}",
        detail=(
            f"{differing} of {report.n} samples differ: {shown}% take the "
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
    """Low buy conversion — but only when the join is healthy enough to say so.

    The join-health gate is not decoration. The first version of the execution
    join could never match on the equity path, so every run reported 0 of N buys
    executed and this function fired a `severity="high"` finding on all of them.
    A subsystem built to stop this project chasing artifacts must not manufacture
    its own. If fills exist and NONE of them matched a decision, the defect is in
    the join, and that is what gets reported.
    """
    summary = summary or {}
    decided = int(summary.get("buy_decided") or 0)
    executed = int(summary.get("buy_executed") or 0)
    available = int(summary.get("trades_available") or 0)
    matched = int(summary.get("trades_matched") or 0)

    if available > 0 and matched == 0:
        return Finding(
            kind="join_failure",
            target=target,
            severity="high",
            title=f"Execution join matched nothing on {target}",
            detail=(
                f"The run recorded {available} fill(s) and {summary.get('decided', 0)} "
                f"decision(s), and not one fill could be matched to a decision. "
                f"Conversion cannot be measured from this run — treat any "
                f"refusal count from it as unusable, not as evidence of refusal."
            ),
            evidence=dict(summary),
            detected_at=detected_at,
            run_id=str(run_id),
        )

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
            f"This counts only buys that REACHED the execution path — names "
            f"refused at a gate (min-position floor, max_positions, fundamental "
            f"veto) are never written to the source table, so the true refusal "
            f"count is higher than this."
        ),
        evidence=dict(summary),
        detected_at=detected_at,
        run_id=str(run_id),
    )


def findings_for_run(observations, summary, *, target: str, run_id: str,
                     detected_at: str, variance_threshold: float = 0.95,
                     variance_min_n: int = 30) -> list:
    """Every finding a single run supports. Order is stable for the UI.

    The guard thresholds are parameters, not constants: hardcoding them here
    made this a divergent duplicate of the pipeline that silently ignored the
    operator's configured `variance_threshold` / `variance_min_n`.
    """
    out = []
    variance = finding_from_variance(
        assess_observations(observations, threshold=variance_threshold,
                            min_n=variance_min_n),
        target=target, run_id=run_id, detected_at=detected_at)
    if variance is not None:
        out.append(variance)
    funnel = finding_from_funnel(summary, target=target, run_id=run_id,
                                 detected_at=detected_at)
    if funnel is not None:
        out.append(funnel)
    return out
