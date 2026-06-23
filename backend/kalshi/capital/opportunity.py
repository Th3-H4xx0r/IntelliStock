"""Opportunity scoring (pure). A single comparable score per candidate across the
forward calendar so the capital planner can prefer better/easier money and hold
reserve for it. score = edge × model_confidence × liquidity × time-to-kickoff."""
from __future__ import annotations


def liquidity_factor(liquidity: float) -> float:
    """Saturating 0..1 from order-book depth (contracts). Thin markets score low."""
    if liquidity <= 0:
        return 0.1
    return min(1.0, liquidity / 1000.0)


def time_factor(hours_to_kickoff: float) -> float:
    """Nearer kickoff scores higher (more certain); in-play scores lower."""
    if hours_to_kickoff <= 0:
        return 0.2
    return max(0.2, min(1.0, 48.0 / (hours_to_kickoff + 6.0)))


def score(*, edge: float, model_confidence: float, liquidity: float, hours_to_kickoff: float) -> float:
    conf = max(0.0, min(1.0, model_confidence))
    return max(0.0, edge) * conf * liquidity_factor(liquidity) * time_factor(hours_to_kickoff)
