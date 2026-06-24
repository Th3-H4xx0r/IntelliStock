"""Closing-line value — the ONLY real success metric. CLV measures whether you
got a better price than Kalshi's own close. Positive CLV over several hundred
fixtures is the gate that decides if the edge is real; backtest ROI is not.

For a YES buy, CLV = implied(close) - implied(entry): if the price rose toward
resolution after you bought, you got in cheaper than the market settled to and
captured value.
"""
from __future__ import annotations


def _implied(cents: float) -> float:
    return cents / 100.0


def compute_clv(*, entry_cents: float, close_cents: float) -> float:
    """CLV for a YES position, in probability points."""
    return _implied(close_cents) - _implied(entry_cents)


def summarize_clv(rows: list[dict]) -> dict:
    """rows: [{'league': str, 'clv': float}, ...] -> overall + per-league
    averages. Empty -> zeroed summary."""
    if not rows:
        return {"overall": {"avg_clv": 0.0, "n": 0}, "by_league": {}}

    overall_sum = 0.0
    by: dict[str, list[float]] = {}
    for r in rows:
        c = float(r.get("clv", 0.0))
        overall_sum += c
        by.setdefault(r.get("league", "?"), []).append(c)

    by_league = {
        lg: {"avg_clv": sum(vals) / len(vals), "n": len(vals)}
        for lg, vals in by.items()
    }
    return {
        "overall": {"avg_clv": overall_sum / len(rows), "n": len(rows)},
        "by_league": by_league,
    }
