"""Pure, DB-free helpers for nexus strategy telemetry endpoints.

Kept separate from api/main.py and the strategy so the shaping/aggregation
logic is unit-testable without RethinkDB or a running strategy.
"""
from __future__ import annotations


def _is_long(intent: str) -> bool:
    s = (intent or "").lower()
    return "buy" in s or "long" in s


def _is_short(intent: str) -> bool:
    s = (intent or "").lower()
    return "sell" in s or "short" in s


def summarize_outcomes(docs: list) -> dict:
    """Aggregate GraphNexusTradeOutcomes docs into a scorecard.

    A signal is "correct" when its direction matched the realized return:
    long & return>0, or short & return<0.
    """
    docs = [d for d in (docs or []) if isinstance(d, dict)]
    n = len(docs)
    if n == 0:
        return {"hit_rate": 0.0, "n": 0, "n_correct": 0, "avg_return": 0.0,
                "recent": [], "data_status": "legacy_untrusted"}
    n_correct = 0
    total_ret = 0.0
    for d in docs:
        intent = str(d.get("action_intent") or "")
        ret = float(d.get("latest_return") or 0.0)
        total_ret += ret
        if (_is_long(intent) and ret > 0) or (_is_short(intent) and ret < 0):
            n_correct += 1
    recent = sorted(
        docs,
        key=lambda d: str(d.get("latest_observation_date") or d.get("entry_date") or ""),
        reverse=True,
    )[:8]
    recent_view = [
        {
            "symbol": str(d.get("symbol") or "").upper(),
            "action_intent": str(d.get("action_intent") or ""),
            "latest_return": float(d.get("latest_return") or 0.0),
            "dominant_event_type": str(d.get("dominant_event_type") or ""),
            "entry_date": str(d.get("entry_date") or ""),
        }
        for d in recent
    ]
    return {
        "hit_rate": n_correct / n,
        "n": n,
        "n_correct": n_correct,
        "avg_return": total_ret / n,
        "recent": recent_view,
        # Task 8 (benchmark-alpha review E04): the legacy corpus carries
        # unknown intents, inverted sell semantics, zero-return rows, and
        # unadjusted corporate actions. This scorecard is explicitly
        # untrusted while any invalid historical rows remain and is never
        # promotion evidence.
        "data_status": "legacy_untrusted",
    }


def normalize_backfill_item(raw: dict) -> dict:
    """Map a _backfill_queue item to a stable view.

    The live queue (graph_nexus_analysis._enqueue_backfill_candidate) stores
    `raw_net_score`, `signal_source`, and the priority flags
    `is_watchlist_priority` / `is_propagation_expansion`. We read those exact
    keys and also tolerate the simpler `score` / `source` / `priority` forms.
    """
    raw = raw or {}
    score = raw.get("raw_net_score")
    if score is None:
        score = raw.get("score")
    return {
        "ticker": str(raw.get("ticker") or "").upper(),
        "score": float(score or 0.0),
        "n_paths": int(raw.get("n_paths") or 0),
        "source": str(raw.get("source") or raw.get("signal_source") or ""),
        "priority": bool(
            raw.get("priority")
            or raw.get("is_priority")
            or raw.get("is_watchlist_priority")
            or raw.get("is_propagation_expansion")
        ),
    }


def newest_watchlist(watchlist: dict, limit: int = 12) -> list:
    """Newest entries of the _momentum_watchlist dict, by first_seen_bar desc.

    The strategy stores only `first_seen_bar` and `first_seen_price` per symbol
    (no return field), so we surface the entry price the bot first saw."""
    if not isinstance(watchlist, dict):
        return []
    rows = []
    for sym, meta in watchlist.items():
        meta = meta if isinstance(meta, dict) else {}
        rows.append({
            "symbol": str(sym).upper(),
            "first_seen_bar": int(meta.get("first_seen_bar") or 0),
            "first_seen_price": float(meta.get("first_seen_price") or 0.0),
        })
    rows.sort(key=lambda e: e["first_seen_bar"], reverse=True)
    return rows[: max(0, int(limit))]


def dedupe_latest_contexts(docs: list, limit: int = 40) -> list:
    """Latest GraphNexusTradeContexts doc per symbol (docs assumed newest-first),
    shaped for the rationale card with a truncated reason."""
    seen = set()
    out = []
    for d in (docs or []):
        if not isinstance(d, dict):
            continue
        sym = str(d.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append({
            "symbol": sym,
            "reason": str(d.get("reason") or "")[:240],
            "dominant_event_type": str(d.get("dominant_event_type") or ""),
            "action_intent": str(d.get("action_intent") or ""),
            "score": float(d.get("score") or 0.0),
            "date_key": str(d.get("date_key") or d.get("entry_date") or ""),
        })
        if len(out) >= max(1, int(limit)):
            break
    return out
