"""24/7 scheduler timing (pure). The Kalshi engine runs around the clock (soccer
resolves at kickoff times worldwide), so there is NO market-hours gating — just a
base cadence with an earlier wake when a settlement is imminent (capital frees up)."""
from __future__ import annotations

FLOOR_SECONDS = 5


def next_wake_seconds(poll_seconds: int, pending_settlement_in: int | None = None, floor: int = FLOOR_SECONDS) -> int:
    """Seconds until the next tick: the base cadence, or sooner if a settlement is
    expected before then. Floored so we never busy-spin."""
    base = max(floor, int(poll_seconds))
    if pending_settlement_in is not None and pending_settlement_in >= 0:
        return max(floor, min(base, int(pending_settlement_in)))
    return base
