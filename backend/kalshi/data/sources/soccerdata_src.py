"""soccerdata adapter — team/player/xG/Elo/form/lineup data for the feature store.

Lazy-imports `soccerdata` so the package is optional: if it's not installed (or a
scrape fails), the engine degrades to sharp-only pricing. The mapping helpers
that turn soccerdata rows into our typed DTOs are pure and unit-testable; the
network scrape (`load_*`) is integration.
"""
from __future__ import annotations

from kalshi.feature_models import TeamForm, PlayerRate


def team_form_from_row(row: dict) -> TeamForm:
    """Map a normalized team-stats row -> TeamForm. Keys are tolerant so the same
    helper works across FBref/Understat/ClubElo shapes after normalization."""
    return TeamForm(
        elo=float(row.get("elo", 0.0) or 0.0),
        xg_for=float(row.get("xg_for", row.get("xg", 0.0)) or 0.0),
        xg_against=float(row.get("xg_against", row.get("xga", 0.0)) or 0.0),
        form_pts=float(row.get("form_pts", 0.0) or 0.0),
        home_xg=float(row.get("home_xg", 0.0) or 0.0),
        away_xg=float(row.get("away_xg", 0.0) or 0.0),
    )


def player_rate_from_row(row: dict) -> PlayerRate:
    return PlayerRate(
        name=str(row.get("player", row.get("name", ""))),
        minutes=float(row.get("minutes", row.get("min", 0.0)) or 0.0),
        shots=float(row.get("shots", row.get("sh", 0.0)) or 0.0),
        xg_per90=float(row.get("xg_per90", row.get("npxg_per90", 0.0)) or 0.0),
        scored_last_n=int(row.get("scored_last_n", 0) or 0),
    )


def soccerdata_available() -> bool:
    try:
        import soccerdata  # noqa: F401
        return True
    except Exception:
        return False


def load_clubelo(league: str):  # pragma: no cover - integration scrape
    """Return a ClubElo dataframe for a league, or None if unavailable."""
    try:
        import soccerdata as sd
        return sd.ClubElo().read_by_date()
    except Exception:
        return None
