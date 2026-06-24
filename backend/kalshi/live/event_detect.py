"""Material-event detection from Kalshi mid-price action (Kalshi-price-only mode).

A sharp move in a winner side's mid-price between polls is our proxy for an
in-match event (goal, red card, big chance). That move is what wakes the LLM to
read commentary and what unlocks opening/adding when the clock is UNKNOWN. Pure +
unit-tested."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PriceMove:
    moved: bool
    delta_cents: float      # signed: + = YES got richer (side more likely)
    direction: str          # 'up' | 'down' | 'flat'


def detect_move(history, *, threshold_cents: float = 8.0, lookback: int = 3) -> PriceMove:
    """history: mid-prices in cents, oldest..newest. A material move = the newest
    price differs from the reference (up to `lookback` points back) by at least
    `threshold_cents`. Returns the signed delta + direction. <2 points -> flat."""
    pts = [float(x) for x in (history or []) if x is not None]
    if len(pts) < 2:
        return PriceMove(False, 0.0, "flat")
    newest = pts[-1]
    ref = pts[max(0, len(pts) - 1 - max(1, lookback))]
    delta = newest - ref
    if abs(delta) >= threshold_cents:
        return PriceMove(True, delta, "up" if delta > 0 else "down")
    return PriceMove(False, delta, "flat")
