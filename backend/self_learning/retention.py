"""Retention and rollup for LearningObservations — the only large table.

RethinkDB is already this deployment's bottleneck: PriceHistory at ~2.3M rows
drove 17 restarts in 12 days on a memory-starved VM. So raw observations expire
and a daily rollup keeps the learning value permanently.

Two rules learned the hard way:

* **A row whose timestamp cannot be parsed is NEVER deleted.** A parse bug must
  not become data loss. (Which is why `timeline.to_naive_utc` has to handle the
  double `+00:00Z` suffix `bot_decision_log` emits — otherwise "never delete"
  quietly turns into "never expires", and the table this module exists to bound
  grows forever.)
* **`retain_days` has a floor.** A stored `0` would delete the entire table on
  the next sweep, and `store.merge_config` only discards `None`, so a zero in
  the config document reaches here intact.

`cutoff_iso` exists so the caller can push the delete into the DATABASE as a
`between(...).delete()` rather than loading millions of rows into Python to
decide. `expired_ids` remains for the small/in-memory case and for tests.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta

from self_learning.timeline import to_naive_utc
from self_learning.variance import assess_variance

MIN_RETAIN_DAYS = 1


def _safe_retain_days(retain_days) -> int:
    try:
        days = int(retain_days)
    except (TypeError, ValueError):
        return 90
    return max(MIN_RETAIN_DAYS, days)


def cutoff_iso(*, now_iso: str, retain_days: int = 90):
    """The naive-UTC instant before which rows are expired, as an ISO string.

    Returned rather than applied so the caller can hand it to a server-side
    range delete; nothing here should ever materialise the table.
    """
    now = to_naive_utc(now_iso)
    if now is None:
        return None
    return (now - timedelta(days=_safe_retain_days(retain_days))).isoformat()


def expired_ids(docs, *, now_iso: str, retain_days: int = 90) -> list:
    now = to_naive_utc(now_iso)
    if now is None:
        return []
    cutoff = now - timedelta(days=_safe_retain_days(retain_days))
    out = []
    for doc in (docs or []):
        stamp = to_naive_utc((doc or {}).get("as_of"))
        if stamp is None:
            continue        # unparseable is kept, never deleted
        if stamp < cutoff:
            out.append(str(doc.get("id")))
    return out


def rollup(docs) -> list:
    """One aggregate per (run_id, strategy_id, date).

    `score_n` travels with `score_top_share`: without it a share of 0.0 (the
    field was entirely missing) reads BETTER than a healthy 0.005, and every
    single-row bucket reads as maximum saturation. The share is uninterpretable
    on its own.
    """
    buckets = defaultdict(list)
    for doc in (docs or []):
        stamp = to_naive_utc((doc or {}).get("as_of"))
        if stamp is None:
            continue
        key = (str(doc.get("run_id") or ""), str(doc.get("strategy_id") or ""),
               stamp.strftime("%Y-%m-%d"))
        buckets[key].append(doc)
    out = []
    for (run_id, strategy_id, date), rows in sorted(buckets.items()):
        scores = [r.get("normalized_score") for r in rows]
        report = assess_variance(scores, field_name="normalized_score", min_n=1)
        out.append({
            # Percent-escaped separators: a raw "|" join collapses
            # ("a|b","c") and ("a","b|c") onto one key, and this repo's
            # instance ids really do contain "|" (scope suffixes).
            "id": "|".join(part.replace("%", "%25").replace("|", "%7C")
                           for part in (run_id, strategy_id, date)),
            "run_id": run_id, "strategy_id": strategy_id, "date": date,
            "decided": len(rows),
            "executed": sum(1 for r in rows if r.get("executed")),
            "refused": sum(1 for r in rows if r.get("refusal_reason")),
            "score_n": report.n,
            "score_distinct": report.distinct,
            "score_top_share": round(report.top_share, 6),
        })
    return out
