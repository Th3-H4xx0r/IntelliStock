"""National-team Elo source — eloratings.net (free, no API key). ClubElo covers
clubs only, so World Cup / international markets need their own LIVE ratings. The
built-in table in quant/national_elo.py is a frozen ~2026 snapshot with no
recent-form; this fetches the current World Football Elo (which updates after every
international) so the model anchor reflects real, current strength.

Two header-less TSV files (same plain-GET shape as ClubElo):
  * en.teams.tsv  -> "<code>\t<full name>\t<aliases...>"   (col 0 = code, col 1 = name)
  * World.tsv     -> rank cols then "<code>...<rating>..."  (col 2 = code, col 3 = rating)
Parsing is pure/tested; the HTTP fetch is integration. Degrade-safe: any failure
returns {} so the caller falls back to the static table (national_elo_from)."""
from __future__ import annotations

ELORATINGS_BASE = "https://www.eloratings.net"


def parse_team_codes(text: str) -> dict:
    """en.teams.tsv -> {2-letter country code: full team name}."""
    out: dict = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code, name = parts[0].strip(), parts[1].strip()
        if code and name:
            out[code] = name
    return out


def parse_world_elo(text: str, code_to_name: dict, canon=None) -> dict:
    """World.tsv -> {canonical_team_name: elo}. Code is column index 2, rating is
    column index 3. `canon` maps a full name to the key space used by the lookup
    (pass national_elo.canonical_team so keys match the static table's aliases)."""
    norm = canon or (lambda s: s)
    out: dict = {}
    for line in (text or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name = (code_to_name or {}).get(parts[2].strip())
        if not name:
            continue
        try:
            elo = float(parts[3].strip())
        except (TypeError, ValueError):
            continue
        if elo > 0:
            out[norm(name)] = elo
    return out


def fetch_national_elo_table(base_url: str = ELORATINGS_BASE) -> dict:  # pragma: no cover - integration
    """Fetch the current World Football Elo table keyed by canonical team name.
    Returns {} on any failure so the caller falls back to the static table."""
    try:
        import requests
        from kalshi.quant.national_elo import canonical_team
        base = (base_url or ELORATINGS_BASE).rstrip("/")
        teams = requests.get(f"{base}/en.teams.tsv", timeout=20)
        teams.raise_for_status()
        world = requests.get(f"{base}/World.tsv", timeout=20)
        world.raise_for_status()
        return parse_world_elo(world.text, parse_team_codes(teams.text), canon=canonical_team)
    except Exception:
        return {}
