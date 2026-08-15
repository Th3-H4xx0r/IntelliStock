"""The per-run pipeline, as a pure function.

Keeping this pure is what lets the engine be a thin I/O shell: every branch that
decides what gets recorded and what gets raised is tested without a database or
a changefeed.
"""
from __future__ import annotations

from collections import Counter

from self_learning.findings import finding_from_funnel, finding_from_variance
from self_learning.observers import funnel_summary, observations_from_backtest
from self_learning.variance import assess_observations


def _dominant_strategy(observations) -> str:
    names = Counter(o.strategy_id for o in observations if o.strategy_id)
    return names.most_common(1)[0][0] if names else "unknown"


def process_backtest_document(doc, *, detected_at: str, venue: str = "equity",
                              variance_threshold: float = 0.95,
                              variance_min_n: int = 30) -> dict:
    observations = observations_from_backtest(doc or {}, venue=venue)
    summary = funnel_summary(doc or {})
    run_id = str((doc or {}).get("id") or "")
    target = f"{venue}/{_dominant_strategy(observations)}" if observations else ""

    findings = []
    if observations:
        variance = finding_from_variance(
            assess_observations(observations, threshold=variance_threshold,
                                min_n=variance_min_n),
            target=target, run_id=run_id, detected_at=detected_at)
        if variance is not None:
            findings.append(variance)
        funnel = finding_from_funnel(summary, target=target, run_id=run_id,
                                     detected_at=detected_at)
        if funnel is not None:
            findings.append(funnel)

    return {"observations": observations, "findings": findings,
            "summary": summary, "target": target, "run_id": run_id}
