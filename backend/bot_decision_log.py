"""Pure helpers for the per-instance bot trade-decision log.

These build the record (and pick the "primary driver" strategy + reason) that
broker.py writes to the BotTradeDecisions table on each confirmed live buy/sell,
and that the mobile "Bot activity" view reads back per position. Kept free of
any DB / threading so they can be unit-tested in isolation (broker.py itself is
a CLI script and isn't importable).
"""

TABLE = "BotTradeDecisions"


def primary_decision(strategy_summary, post_decision_trace, pre_override_decision, decision):
    """Derive ``(primary_strategy, action_intent, final_reason, override_applied)``
    from the per-symbol decision context — mirrors broker.py's backtest playback
    logic so the live log surfaces the same primary driver the playback UI would.

    A post-decision override (e.g. the LLM trading-decision strategy) wins when it
    flipped the decision or left a reason; otherwise the highest-weight strategy
    that *voted this decision* is the driver.
    """
    ss = strategy_summary or []
    aligned = [s for s in ss if s.get("decision") == decision]
    pool = aligned or list(ss)
    primary_entry = None
    if pool:
        primary_entry = max(pool, key=lambda item: (
            float(item.get("weight") or 0.0),
            1 if (item.get("reason") or "").strip() else 0,
            len(str(item.get("reason") or "")),
        ))
    post_primary = post_decision_trace[-1] if post_decision_trace else None
    override_applied = pre_override_decision != decision
    primary_strategy = None
    primary_action_intent = None
    final_reason = ""
    if post_primary and (override_applied or (post_primary.get("reason") or "").strip()):
        primary_strategy = post_primary.get("strategy")
        final_reason = str(post_primary.get("reason") or "").strip()[:1500]
    elif primary_entry:
        primary_strategy = primary_entry.get("strategy")
        primary_action_intent = primary_entry.get("action_intent")
        final_reason = str(primary_entry.get("reason") or "").strip()[:1500]
    return primary_strategy, primary_action_intent, final_reason, override_applied


def decision_contributors(strategy_summary, decision, limit=4):
    """Top aligned strategies (by weight) that voted this decision, trimmed for
    storage — the "who else agreed" detail behind the primary driver."""
    aligned = [s for s in (strategy_summary or []) if s.get("decision") == decision]
    aligned.sort(key=lambda s: float(s.get("weight") or 0.0), reverse=True)
    out = []
    for s in aligned[:limit]:
        out.append({
            "strategy": s.get("strategy"),
            "weight": s.get("weight"),
            "decision": s.get("decision"),
            "reason": str(s.get("reason") or "").strip()[:300],
        })
    return out


def build_decision_doc(
    instance_id,
    brokerage_id,
    symbol,
    decision,
    price,
    ts,
    strategy_summary,
    post_decision_trace,
    pre_override_decision,
    normalized,
    doc_id=None,
    now_iso=None,
):
    """Build the BotTradeDecisions row for one confirmed live trade. ``decision``
    is +1 (buy) / -1 (sell). ``doc_id`` / ``now_iso`` are injectable for tests."""
    primary_strategy, action_intent, final_reason, override_applied = primary_decision(
        strategy_summary, post_decision_trace, pre_override_decision, decision
    )
    ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts else None)
    score = round(float(normalized), 4) if isinstance(normalized, (int, float)) else None
    if doc_id is None:
        import uuid
        doc_id = str(uuid.uuid4())
    if now_iso is None:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat() + "Z"
    return {
        "id": doc_id,
        "instance_id": str(instance_id or ""),
        "brokerage_id": str(brokerage_id or ""),
        "symbol": str(symbol or "").upper(),
        "side": "buy" if decision == 1 else "sell",
        "price": float(price) if price is not None else None,
        "ts": ts_iso or now_iso,
        "created_at": now_iso,
        "strategy": primary_strategy,
        "action_intent": action_intent,
        "reason": final_reason,
        "score": score,
        "override_applied": bool(override_applied),
        "contributors": decision_contributors(strategy_summary, decision),
    }
