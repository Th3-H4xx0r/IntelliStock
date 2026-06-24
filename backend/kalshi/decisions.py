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
        "opportunity_score": opportunity_score,
        "decision": decision,
        "block_reason": block_reason,
        # filled on settlement
        "outcome": None,
        "realized_pnl_cents": None,
        "clv": None,
    }


def summarize_decisions(rows: list[dict]) -> dict:
    out = {"total": len(rows), "placed": 0, "skipped": 0, "queued": 0, "blocked": 0}
    for r in rows:
        d = r.get("decision")
        if d in out:
            out[d] += 1
    return out
