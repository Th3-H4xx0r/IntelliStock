"""Per-match feature assembly (pure). Combines team/player/h2h/lineup data into a
MatchFeatures bundle and derives the expected goals per side that feed the
Dixon-Coles model. Degrades gracefully: missing team data -> incomplete bundle
-> the orchestrator falls back to sharp-only pricing."""
from __future__ import annotations

from kalshi.feature_models import MatchFeatures

HOME_ADVANTAGE = 1.15  # multiplicative bump to the home side's expected goals


def assemble_features(
    *,
    fixture_id: str,
    home: str,
    away: str,
    home_form=None,
    away_form=None,
    h2h=None,
    players=None,
    lineup_confirmed: bool = False,
    days_rest_home: int = 7,
    days_rest_away: int = 7,
) -> MatchFeatures:
    return MatchFeatures(
        fixture_id=fixture_id,
        home=home,
        away=away,
        home_form=home_form,
        away_form=away_form,
        h2h=list(h2h or []),
        players=list(players or []),
        lineup_confirmed=lineup_confirmed,
        days_rest_home=days_rest_home,
        days_rest_away=days_rest_away,
    )


def expected_goals(features: MatchFeatures):
    """(home_xg, away_xg) for the scoreline model: each side's attack blended with
    the opponent's defence, plus home advantage. None when features are
    incomplete (so the caller falls back to the sharp line)."""
    if not features.is_complete():
        return None
    hf, af = features.home_form, features.away_form
    home_xg = max(0.1, ((hf.xg_for + af.xg_against) / 2.0) * HOME_ADVANTAGE)
    away_xg = max(0.1, (af.xg_for + hf.xg_against) / 2.0)
    return (home_xg, away_xg)
