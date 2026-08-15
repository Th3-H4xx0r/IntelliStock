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

UNATTRIBUTED = "unattributed"


def _dominant_strategy(observations) -> str:
    """The strategy a run is attributed to.

    Two details are load-bearing:

    * **The tie-break is explicit.** `Counter.most_common` returns the
      first-INSERTED key among ties, and insertion order here is the order
      symbols appear in `backtest_decisions` — which `broker.py:14690` re-sorts
      by conviction every tick. A 20/20 split between two strategies would
      therefore pick a different target run to run, and since a finding's id
      hashes its target, one defect would fragment into two threads. Sorting by
      (count, name) makes it deterministic.
    * **Blanks are counted, not dropped.** `primary_strategy` is None whenever
      `strategy_summary` is empty (`broker.py:17337`), which is the normal shape
      for run_once-only strategies. Dropping blanks from the vote lets a single
      attributed decision out of forty win outright and mislabel the whole run.
    """
    names = Counter((o.strategy_id or UNATTRIBUTED) for o in (observations or []))
    if not names:
        return UNATTRIBUTED
    return max(names.items(), key=lambda kv: (kv[1], kv[0]))[0]


def process_backtest_document(doc, *, detected_at: str, venue: str = "equity",
                              variance_threshold: float = 0.95,
                              variance_min_n: int = 30) -> dict:
    observations = observations_from_backtest(doc or {}, venue=venue)
    # Pass the observations in: recomputing them inside funnel_summary doubled
    # the work over a list that reaches 7-15k entries on a long run.
    summary = funnel_summary(doc or {}, observations, venue=venue)
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
