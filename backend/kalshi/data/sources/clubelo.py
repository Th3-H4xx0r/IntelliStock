"""ClubElo source — free, no API key (http://api.clubelo.com/<date> returns CSV).
Gives the engine team strength so the Dixon-Coles model can price markets without
an odds feed. CSV parsing + team lookup are pure/tested; the HTTP fetch is
integration."""
from __future__ import annotations

import csv
import io

from kalshi.normalize import normalize_team


def parse_clubelo_csv(text: str) -> dict:
    """CSV -> {normalized_club_name: elo}."""
    out = {}
    for row in csv.DictReader(io.StringIO(text or "")):
        club = (row.get("Club") or "").strip()
        if not club:
            continue
        try:
            out[normalize_team(club)] = float(row.get("Elo") or 0.0)
        except (TypeError, ValueError):
            continue
    return out


def elo_for(table: dict, team_name: str, default: float = 1500.0) -> float:
    """Look up a team's Elo via the normalized name; default for unknown teams."""
    return table.get(normalize_team(team_name), default)


def fetch_elo_table(date_iso: str | None = None) -> dict:  # pragma: no cover - integration
    """Fetch the current ClubElo table. Returns {} on any failure (engine then
    falls back to default Elo / sharp-only)."""
    try:
        import requests
        url = f"http://api.clubelo.com/{date_iso}" if date_iso else "http://api.clubelo.com/"
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return parse_clubelo_csv(resp.text)
    except Exception:
        return {}
