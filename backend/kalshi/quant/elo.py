"""Elo -> expected goals (pure). Gives the Dixon-Coles model a fair value from
free ClubElo ratings (no API key, no odds feed needed), so the engine can price
markets and trade on demo without OddsPapi. Tunable; the CLV gate is what
decides whether the mapping is good enough to scale."""
from __future__ import annotations

HOME_FIELD_ADVANTAGE = 65.0   # Elo points — a REAL home game (club leagues, a
# host nation). Kept at the genuine ~65 value.
NEUTRAL_HFA = 5.0             # Neutral-site game (World Cup group/knockout): the
# "home" label is essentially arbitrary, so home advantage is ~0. Callers pick
# NEUTRAL_HFA vs HOME_FIELD_ADVANTAGE by whether the fixture is neutral-site.
BASE_TOTAL_GOALS = 2.6        # league-ish average total. 2.7 -> 2.6: calibration
# fit on settled WC games (see below).
SUPREMACY_PER_100 = 0.60      # goal supremacy per 100 Elo of (adjusted) diff.
# 0.45 -> 0.60: the model was UNDER-confident (its ~50% picks won ~67% of the
# time). Sharpening the Elo->supremacy mapping fixed that — a +4% out-of-sample
# log-loss AND Brier improvement on a train/test split of the settled WC slate.
# Deliberately moderate, not the grid optimum (which overfit 79 games).


def win_prob(home_elo: float, away_elo: float, hfa: float = HOME_FIELD_ADVANTAGE) -> float:
    """Elo win probability for the home side (excl. draws)."""
    return 1.0 / (1.0 + 10 ** (-((home_elo + hfa - away_elo) / 400.0)))


def elo_to_expected_goals(home_elo: float, away_elo: float, *, base_total: float = BASE_TOTAL_GOALS, hfa: float = HOME_FIELD_ADVANTAGE):
    """(home_xg, away_xg) from the two Elo ratings + home advantage."""
    diff = home_elo + hfa - away_elo
    supremacy = (diff / 100.0) * SUPREMACY_PER_100
    home_xg = max(0.2, (base_total + supremacy) / 2.0)
    away_xg = max(0.2, (base_total - supremacy) / 2.0)
    return (home_xg, away_xg)
