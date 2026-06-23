"""Pure response-shapers for the /kalshi/* read endpoints. Takes RethinkDB rows
(plain dicts) and returns the JSON each endpoint serves, so the FastAPI handlers
in main.py stay thin and this stays unit-testable with no DB.
"""
from __future__ import annotations

from kalshi.telemetry import portfolio_series
from kalshi.clv import summarize_clv
from kalshi.ingest_odds import budget_remaining, fixtures_per_day_budget


def portfolio_payload(
    snapshot_rows: list[dict],
    *,
    value_cents: int,
    cash_cents: int,
    prev_value_cents: int | None = None,
) -> dict:
    """Account value + equity curve. day_change is value - prev_value_cents (the
    ~24h-ago baseline the caller resolves from raw snapshots). When no baseline
    is given, falls back to the window delta (first->last snapshot)."""
    series = portfolio_series(snapshot_rows)
    if prev_value_cents is not None:
        day_change = round((value_cents - prev_value_cents) / 100.0, 2)
    elif len(series) >= 2:
        day_change = round(series[-1]["value"] - series[0]["value"], 2)
    else:
        day_change = 0.0
    return {
        "value": round(value_cents / 100.0, 2),
        "cash": round(cash_cents / 100.0, 2),
        "day_change": day_change,
        "series": series,
    }


def edges_payload(edge_rows: list[dict], limit: int = 10) -> dict:
    """Edge Radar — top mispriced contracts, strongest first."""
    rows = sorted(edge_rows, key=lambda r: r.get("edge", 0.0), reverse=True)[: max(1, limit)]
    out = [
        {
            "market_ticker": r.get("market_ticker"),
            "fixture_id": r.get("fixture_id"),
            "side": r.get("side"),
            "edge": round(float(r.get("edge", 0.0)), 4),
            "fair_prob": round(float(r.get("fair_prob", 0.0)), 4),
            "kalshi_implied": round(float(r.get("kalshi_implied", 0.0)), 4),
        }
        for r in rows
    ]
    return {"edges": out, "count": len(out)}


def positions_payload(position_rows: list[dict]) -> dict:
    out = []
    for p in position_rows:
        cur = p.get("current_price_cents")
        avg = p.get("avg_price_cents", 0.0)
        qty = p.get("contracts", 0)
        unreal = None if cur is None else round((cur - avg) * qty, 2)
        out.append(
            {
                "market_ticker": p.get("market_ticker"),
                "side": p.get("side"),
                "contracts": qty,
                "avg_price_cents": avg,
                "current_price_cents": cur,
                "unrealized_cents": unreal,
                "settlement_eta": p.get("settlement_eta"),
            }
        )
    return {"positions": out, "count": len(out)}


def clv_payload(clv_rows: list[dict]) -> dict:
    return summarize_clv(clv_rows)


def scan_budget_payload(used: int, *, days_left_in_month: int, limit: int = 250) -> dict:
    return {
        "used": used,
        "limit": limit,
        "remaining": budget_remaining(used, limit),
        "fixtures_per_day": fixtures_per_day_budget(used, days_left_in_month, limit),
    }
