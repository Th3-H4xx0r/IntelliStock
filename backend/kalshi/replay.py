"""Order-book / odds replay simulator (feature #8). Replays recorded fixtures
through the SAME decision functions the live engine uses (plan_orders) and
reports hypothetical fills + CLV. It is a LOGIC CHECK, not a forecast: it does
not model queue position, latency, market impact, or partial-fill dynamics. The
real test is forward CLV in paper mode.
"""
from __future__ import annotations

from kalshi.engine import plan_orders
from kalshi.clv import compute_clv, summarize_clv
from kalshi.risk import RiskCaps

LIMITATIONS = "no queue position / latency / market impact / partial fills modeled"


def replay_fixtures(records: list[dict], *, caps: RiskCaps, fee_rate: float) -> dict:
    """records: [{fixture_id, league, fair, markets, close_cents:{ticker:cents}}].
    Returns hypothetical orders (with CLV vs the recorded close) and a CLV
    summary."""
    hypothetical: list[dict] = []
    clv_rows: list[dict] = []

    for rec in records:
        intents, _reason = plan_orders(
            fixture_id=rec["fixture_id"],
            league=rec.get("league", "?"),
            fair=rec["fair"],
            markets=rec["markets"],
            caps=caps,
            fee_rate=fee_rate,
        )
        closes = rec.get("close_cents", {}) or {}
        for i in intents:
            close = closes.get(i.market_ticker)
            clv = None if close is None else compute_clv(entry_cents=i.limit_cents, close_cents=close)
            hypothetical.append(
                {
                    "fixture_id": rec["fixture_id"],
                    "market_ticker": i.market_ticker,
                    "contracts": i.contracts,
                    "entry_cents": i.limit_cents,
                    "close_cents": close,
                    "edge": i.edge,
                    "clv": clv,
                }
            )
            if clv is not None:
                clv_rows.append({"league": rec.get("league", "?"), "clv": clv})

    return {
        "orders": hypothetical,
        "clv": summarize_clv(clv_rows),
        "n_orders": len(hypothetical),
        "limitations": LIMITATIONS,
    }
