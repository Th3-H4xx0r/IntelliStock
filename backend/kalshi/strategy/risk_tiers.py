"""Risk tier -> allowed market types + max bets per game (pure). Higher tiers
open up the exotic markets that need the model, not the sharp line."""
from __future__ import annotations

_ALLOWED = {
    "low": {"winner", "double_chance"},
    "medium": {"winner", "double_chance", "over_under", "btts"},
    "high": {"winner", "double_chance", "over_under", "btts", "exact_score", "player_score", "halftime"},
    "max": {"winner", "double_chance", "over_under", "btts", "exact_score", "player_score",
            "halftime", "corners", "cards"},
}
_MAX_BETS = {"low": 1, "medium": 2, "high": 4, "max": 8}


def allowed_markets(tier: str) -> set:
    return set(_ALLOWED.get((tier or "medium").lower(), _ALLOWED["medium"]))


def max_bets_per_game(tier: str) -> int:
    return _MAX_BETS.get((tier or "medium").lower(), 2)
