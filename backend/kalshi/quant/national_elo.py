"""National-team strength (pure). ClubElo covers clubs only, so World Cup /
international markets (Argentina, Austria, ...) need their own ratings. This is a
built-in approximate World-Football-Elo table — good enough to differentiate
strong vs weak sides for the model; the CLV gate decides if it's good enough to
scale, and the LLM analyst + news refine it per match."""
from __future__ import annotations

from kalshi.normalize import normalize_team

# Approximate World Football Elo (~2026). Not exact; relative strength is what
# matters for the scoreline model. Extend freely.
_NATIONAL_ELO = {
    "Argentina": 2100, "France": 2055, "Brazil": 2025, "England": 1985, "Spain": 1975,
    "Portugal": 1950, "Netherlands": 1935, "Belgium": 1905, "Germany": 1905, "Italy": 1890,
    "Croatia": 1855, "Uruguay": 1825, "Colombia": 1810, "Morocco": 1795, "Switzerland": 1765,
    "Denmark": 1770, "Mexico": 1750, "United States": 1745, "Japan": 1755, "Senegal": 1745,
    "Austria": 1735, "Serbia": 1715, "Ecuador": 1720, "Poland": 1705, "Nigeria": 1700,
    "Egypt": 1690, "Peru": 1690, "South Korea": 1695, "Australia": 1685, "Canada": 1700,
    "Iran": 1700, "Ukraine": 1730, "Sweden": 1720, "Wales": 1690, "Scotland": 1700,
    "Norway": 1740, "Turkey": 1720, "Ghana": 1660, "Cameroon": 1670, "Ivory Coast": 1680,
    "Tunisia": 1650, "Algeria": 1700, "Costa Rica": 1640, "Paraguay": 1670, "Chile": 1700,
    "Saudi Arabia": 1620, "Qatar": 1600, "Iraq": 1560, "Greece": 1700, "Czechia": 1690,
    "Hungary": 1700, "Romania": 1670, "Slovakia": 1660, "Slovenia": 1650, "New Zealand": 1560,
}
DEFAULT_NATIONAL_ELO = 1600.0

# Common alternate spellings Kalshi/feeds may emit, keyed on the normalize_team
# (title-cased) output, mapped to a canonical _NATIONAL_ELO key. Without this,
# "USA", "Czech Republic", "IR Iran", etc. silently fall through to the default.
_NATIONAL_ALIASES = {
    "Usa": "United States", "Us": "United States", "United States Of America": "United States",
    "Czech Republic": "Czechia", "Korea Republic": "South Korea", "Republic Of Korea": "South Korea",
    "Korea": "South Korea", "South Korea Republic": "South Korea",
    "Cote D'Ivoire": "Ivory Coast", "Côte D'Ivoire": "Ivory Coast",
    "Ir Iran": "Iran", "Turkiye": "Turkey", "Türkiye": "Turkey",
    "Holland": "Netherlands", "England Team": "England",
}


def _canon(name: str) -> str:
    n = normalize_team(name)
    return _NATIONAL_ALIASES.get(n, n)


def is_national_team(name: str) -> bool:
    return _canon(name) in _NATIONAL_ELO


def national_elo(name: str, default: float = DEFAULT_NATIONAL_ELO) -> float:
    return _NATIONAL_ELO.get(_canon(name), default)
