"""Player-to-score model (pure). P(player scores >=1) via a Poisson on
minutes-adjusted xG. Kalshi/Pinnacle rarely price these well, so the model is
the primary signal for player props (gated by lineup confirmation)."""
from __future__ import annotations

import math

UNCONFIRMED_FACTOR = 0.6  # discount when the starter isn't confirmed


def p_player_scores(xg_per90: float, expected_minutes: float, confirmed_starter: bool = True) -> float:
    """Probability the player scores at least once."""
    start_factor = 1.0 if confirmed_starter else UNCONFIRMED_FACTOR
    lam = max(0.0, xg_per90) * (max(0.0, expected_minutes) / 90.0) * start_factor
    return 1.0 - math.exp(-lam)
