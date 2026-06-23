"""Fixtures + stats ingestion. football-data.org is the rock-stable official
schedule/results backbone; soccerdata supplies xG/Elo for the statistical
fallback. Team names are crosswalked so fixtures, odds, and Kalshi markets join.
The fixture parser is pure and unit-tested; the network clients are exercised
against the live APIs in Phase 1.
"""
from __future__ import annotations

from typing import Any

from kalshi.models import Fixture
from kalshi.normalize import normalize_team


def normalize_fixture(raw: dict, *, sport: str = "soccer") -> Fixture:
    """Map a football-data.org match dict into our canonical Fixture, with
    crosswalked team names."""
    comp = raw.get("competition", {}) or {}
    home = raw.get("homeTeam", {}) or {}
    away = raw.get("awayTeam", {}) or {}
    return Fixture(
        fixture_id=str(raw.get("id", "")),
        sport=sport,
        league=comp.get("name", raw.get("league", "")) or "",
        home=normalize_team(home.get("name", raw.get("home", ""))),
        away=normalize_team(away.get("name", raw.get("away", ""))),
        kickoff_utc=raw.get("utcDate", raw.get("kickoff_utc", "")) or "",
    )


class FootballDataClient:
    def __init__(self, api_key: str, session: Any = None, base_url: str = "https://api.football-data.org/v4"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        if session is None:
            import requests
            session = requests.Session()
        self._session = session

    def fetch_matches(self, competition: str, date_from: str, date_to: str) -> list[Fixture]:
        resp = self._session.request(
            "GET",
            f"{self.base_url}/competitions/{competition}/matches",
            headers={"X-Auth-Token": self.api_key},
            params={"dateFrom": date_from, "dateTo": date_to},
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        return [normalize_fixture(m) for m in data.get("matches", [])]
