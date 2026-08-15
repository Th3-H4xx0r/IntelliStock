"""Retention and rollup for LearningObservations — the only large table.

RethinkDB is already this deployment's bottleneck: PriceHistory at ~2.3M rows
drove 17 restarts in 12 days on a memory-starved VM. So raw observations expire
and a daily rollup keeps the learning value permanently. A row whose timestamp
cannot be parsed is NEVER deleted — a parse bug must not become data loss.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from self_learning.variance import assess_variance


def _parse(value):
    raw = str(value or "").strip().replace("Z", "+00:00")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=None)


def expired_ids(docs, *, now_iso: str, retain_days: int = 90) -> list:
    now = _parse(now_iso)
    if now is None:
        return []
    cutoff = now - timedelta(days=int(retain_days))
    out = []
    for doc in (docs or []):
        stamp = _parse((doc or {}).get("as_of"))
        if stamp is None:
            continue        # unparseable is kept, never deleted
        if stamp < cutoff:
            out.append(str(doc.get("id")))
    return out


def rollup(docs) -> list:
    """One aggregate per (run_id, strategy_id, date). Keeps the counts and the
    saturation share, which is what later phases actually read."""
    buckets = defaultdict(list)
    for doc in (docs or []):
        stamp = _parse((doc or {}).get("as_of"))
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
            "id": f"{run_id}|{strategy_id}|{date}",
            "run_id": run_id, "strategy_id": strategy_id, "date": date,
            "decided": len(rows),
            "executed": sum(1 for r in rows if r.get("executed")),
            "refused": sum(1 for r in rows if r.get("refusal_reason")),
            "score_distinct": report.distinct,
            "score_top_share": round(report.top_share, 6),
        })
    return out
