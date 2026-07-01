"""OddsPapi as a FALLBACK live sharp source.

The primary live sharp source is The-Odds-API (`data.sources.odds_api`). When it
returns no data for the current slate (no key, monthly quota exhausted, or the
service is down) and an OddsPapi key is configured, the engine falls back here.

Events are returned in the SAME normalized shape as `odds_api.parse_events`
(`{home, away, commence_time, books: {bookkey: {home, draw, away}}}`, decimal
odds) so `odds_api.match_event` / `quote_for_event` consume them unchanged.
Never raises; returns [] on any failure so the caller degrades to model-only.
"""
from __future__ import annotations

_SOCCER_SPORT_ID = 10


def fetch_events(oddspapi_key: str, date_from: str, date_to: str, *,
                 client=None, sport_id: int = _SOCCER_SPORT_ID) -> list:
    """Current OddsPapi soccer fixtures with a 3-way price in [date_from, date_to]
    (YYYY-MM-DD), normalized to odds_api's event shape. The latest historical-odds
    snapshot per fixture is used as the current line."""
    if not oddspapi_key:
        return []
    if client is None:
        from kalshi.ingest_odds import OddsPapiClient
        client = OddsPapiClient(oddspapi_key)
    try:
        fixtures = client.list_fixtures(sport_id, date_from, date_to)
    except Exception:
        return []
    out = []
    for fx in fixtures or []:
        if not fx.get("has_odds"):
            continue
        fid = fx.get("fixture_id")
        if not fid:
            continue
        try:
            snaps = client.historical_odds(fid)
        except Exception:
            continue
        if not snaps:
            continue
        last = snaps[-1]  # snapshots are ascending by ts -> latest ≈ current line
        try:
            book = {"home": float(last["home"]), "draw": float(last["draw"]),
                    "away": float(last["away"])}
        except (KeyError, TypeError, ValueError):
            continue
        if any(v <= 1.0 for v in book.values()):  # decimal odds must be > 1.0
            continue
        out.append({"home": fx.get("home", ""), "away": fx.get("away", ""),
                    "commence_time": "", "books": {"oddspapi": book}})
    return out
