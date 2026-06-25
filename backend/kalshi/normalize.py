"""Team-ID crosswalk. Every source spells team names differently (OddsPapi vs
football-data.org vs soccerdata vs Kalshi). Normalize to a canonical name so
fixtures, odds, and Kalshi markets join correctly. Mirrors soccerdata's
teamname_replacements mechanism — extend the map as new sources appear.
"""
from __future__ import annotations

# Canonical-name replacements, keyed by a normalized lookup form
# (lowercase, collapsed whitespace, punctuation stripped).
_REPLACEMENTS: dict[str, str] = {
    # Club teams
    "man utd": "Manchester United",
    "man united": "Manchester United",
    "manchester utd": "Manchester United",
    "man city": "Manchester City",
    "spurs": "Tottenham Hotspur",
    "tottenham": "Tottenham Hotspur",
    "wolves": "Wolverhampton Wanderers",
    "brighton": "Brighton & Hove Albion",
    "newcastle": "Newcastle United",
    "west ham": "West Ham United",
    "nottm forest": "Nottingham Forest",
    "sheffield utd": "Sheffield United",
    "leeds": "Leeds United",
    "psg": "Paris Saint-Germain",
    "inter": "Internazionale",
    "atletico": "Atletico Madrid",
    # National teams — "&" vs "and" and other common variants that break
    # substring matching between ESPN and Kalshi/FIFA spellings.
    "bosnia herzegovina": "Bosnia and Herzegovina",
    "bosnia and herzegovina": "Bosnia and Herzegovina",
    "bosnia herzeg": "Bosnia and Herzegovina",
    "czech republic": "Czech Republic",
    "czechia": "Czech Republic",
    "republic of ireland": "Republic of Ireland",
    "north macedonia": "North Macedonia",
    "north macedonia republic": "North Macedonia",
    "ivory coast": "Ivory Coast",
    "cote divoire": "Ivory Coast",
    "cotedivoire": "Ivory Coast",
    "united states": "United States",
    "usa": "United States",
    "us": "United States",
    "korea republic": "South Korea",
    "south korea": "South Korea",
    "republic of korea": "South Korea",
    "democratic republic of congo": "DR Congo",
    "dr congo": "DR Congo",
    "cape verde": "Cape Verde",
    "trinidad tobago": "Trinidad and Tobago",
    "trinidad and tobago": "Trinidad and Tobago",
    "antigua barbuda": "Antigua and Barbuda",
    "saint kitts nevis": "St. Kitts and Nevis",
}


def _key(name: str) -> str:
    cleaned = "".join(ch for ch in name.lower() if ch.isalnum() or ch.isspace())
    return " ".join(cleaned.split())


def normalize_team(name: str) -> str:
    """Return the canonical team name. Known aliases map to their canonical
    form; unknown names pass through title-cased and whitespace-collapsed."""
    if not name:
        return ""
    k = _key(name)
    if k in _REPLACEMENTS:
        return _REPLACEMENTS[k]
    return " ".join(name.split()).title()


class TeamCrosswalk:
    """Stateful crosswalk allowing per-source overrides on top of the base map."""

    def __init__(self, extra: dict[str, str] | None = None):
        self._map = dict(_REPLACEMENTS)
        if extra:
            for alias, canonical in extra.items():
                self._map[_key(alias)] = canonical

    def normalize(self, name: str) -> str:
        if not name:
            return ""
        k = _key(name)
        return self._map.get(k, " ".join(name.split()).title())
