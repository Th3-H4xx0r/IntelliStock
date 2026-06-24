"""Typed feature-bundle DTOs for the per-match feature store. Extensible: adding a
new factor is a new field/group + a source adapter, no re-architecture."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TeamForm:
    elo: float = 0.0
    xg_for: float = 0.0
    xg_against: float = 0.0
    form_pts: float = 0.0      # points from last N matches
    home_xg: float = 0.0
    away_xg: float = 0.0


@dataclass(frozen=True)
class PlayerRate:
    name: str
    minutes: float = 0.0
    shots: float = 0.0
    xg_per90: float = 0.0
    scored_last_n: int = 0


@dataclass(frozen=True)
class MatchFeatures:
    fixture_id: str
    home: str
    away: str
    home_form: TeamForm | None = None
    away_form: TeamForm | None = None
    h2h: list = field(default_factory=list)        # recent meetings
    players: list = field(default_factory=list)    # list[PlayerRate]
    days_rest_home: int = 7
    days_rest_away: int = 7
    lineup_confirmed: bool = False

    def is_complete(self) -> bool:
        """Enough to price goal-based markets: both team forms present."""
        return self.home_form is not None and self.away_form is not None
