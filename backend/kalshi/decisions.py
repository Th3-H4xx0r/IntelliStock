"""Decision-log row builder (pure). Every bet placed AND every candidate
considered-and-skipped becomes a kalshi_decisions row capturing model vs sharp
vs LLM reasoning — the audit trail that powers the instance detail view's
LLM-reasoned decision log."""
from __future__ import annotations


def decision_doc(
    *,
    instance_id: str,
    brokerage_id: str,
    ts: str,
    fixture_id: str,
    market_ticker: str,
    side: str,
    model_prob: float | None = None,
    sharp_prob: float | None = None,
    llm_adjustment: float | None = None,
    llm_rationale: str = "",
    fused_fair: float | None = None,
    edge: float | None = None,
    fee: float | None = None,
    size: int = 0,
    opportunity_score: float | None = None,
    decision: str = "skipped",   # placed | skipped | queued | blocked
    block_reason: str = "",
    league: str = "",
    sharp_close_prob: float | None = None,
    entry_avg_cents: int | None = None,
) -> dict:
    return {
        "id": f"{instance_id}|{market_ticker}|{ts}",
        "instance_id": instance_id,
        "brokerage_id": brokerage_id,
        "ts": ts,
        "fixture_id": fixture_id,
        "market_ticker": market_ticker,
        "side": side,
        "model_prob": model_prob,
        "sharp_prob": sharp_prob,
        "llm_adjustment": llm_adjustment,
        "llm_rationale": llm_rationale,
        "fused_fair": fused_fair,
        "edge": edge,
        "fee": fee,
        "size": size,
        "entry_avg_cents": entry_avg_cents,   # actual ask paid — ground-truth entry for reconcile
        "opportunity_score": opportunity_score,
        "decision": decision,
        "block_reason": block_reason,
        "league": league,
        # filled on settlement by reconcile.settle_decisions (the feedback loop)
        "outcome": None,
        "realized_pnl_cents": None,
        "clv": None,
        "pre_settle_mid_cents": None,   # last observed Kalshi mid before settlement (CLV close)
        "sharp_close_prob": sharp_close_prob,  # sharp book's prob at entry (CLV reference)
    }


def summarize_decisions(rows: list[dict]) -> dict:
    out = {"total": len(rows), "placed": 0, "skipped": 0, "queued": 0, "blocked": 0}
    for r in rows:
        d = r.get("decision")
        if d in out:
            out[d] += 1
    return out
