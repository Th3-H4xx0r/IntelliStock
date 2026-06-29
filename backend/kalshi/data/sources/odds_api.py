"""Sharp bookmaker odds (The-Odds-API v4) — the fair-value anchor.

Real bookmaker odds are the sharpest available estimate of true probability. We
fetch them, de-vig the sharpest book, and use that as fair value; the edge is then
"sharp consensus vs the Kalshi price" — a genuinely exploitable signal. The odds
are environment-agnostic (same on demo + live); only the Kalshi price we compare
against differs. Parsing + matching + sport-key mapping are pure/tested; the HTTP
fetch is integration and degrade-safe (empty on failure)."""
from __future__ import annotations

import re

from kalshi.models import OddsQuote
from kalshi.quant.national_elo import canonical_team

# Kalshi series ticker -> The-Odds-API sport key.
SPORT_KEY_BY_SERIES = {
    "KXWCGAME": "soccer_fifa_world_cup",
}

# League label -> sport key (best effort; extend as leagues are added).
SPORT_KEY_BY_LEAGUE = {
    "world cup": "soccer_fifa_world_cup", "epl": "soccer_epl",
    "premier league": "soccer_epl", "la liga": "soccer_spain_la_liga",
    "serie a": "soccer_italy_serie_a", "serie b": "soccer_italy_serie_b",
    "bundesliga": "soccer_germany_bundesliga", "ligue 1": "soccer_france_ligue_one",
    "ligue 2": "soccer_france_ligue_two", "mls": "soccer_usa_mls",
    "champions league": "soccer_uefa_champs_league", "eredivisie": "soccer_netherlands_eredivisie",
    "primeira liga": "soccer_portugal_primeira_liga",
}

# Sharpest books first; we take the first complete 3-way quote we find.
DEFAULT_BOOK_PRIORITY = ("pinnacle", "betfair_ex_eu", "betfair", "marathonbet",
                         "williamhill", "betclic", "onexbet", "unibet_eu")


def sport_key_for_series(series: str):
    return SPORT_KEY_BY_SERIES.get((series or "").strip().upper())


def sport_key_for_league(league: str):
    return SPORT_KEY_BY_LEAGUE.get(" ".join((league or "").strip().lower().split()))


def parse_events(payload) -> list:
    """The-Odds-API v4 odds payload -> normalized events:
    [{home, away, commence_time, books: {bookkey: {home, draw, away}}}]. Each book's
    h2h outcomes are matched by team name (== home_team / == away_team / 'Draw').
    Never raises; skips events/books without a complete 3-way price."""
    out = []
    for ev in payload or []:
        try:
            home = (ev.get("home_team") or "").strip()
            away = (ev.get("away_team") or "").strip()
            if not home or not away:
                continue
            books = {}
            for bk in ev.get("bookmakers") or []:
                key = (bk.get("key") or "").strip().lower()
                h2h = next((m for m in (bk.get("markets") or []) if m.get("key") == "h2h"), None)
                if not key or not h2h:
                    continue
                prices = {}
                for oc in h2h.get("outcomes") or []:
                    name = (oc.get("name") or "").strip()
                    try:
                        price = float(oc.get("price"))
                    except (TypeError, ValueError):
                        continue
                    if price <= 1.0:
                        continue
                    if name == home:
                        prices["home"] = price
                    elif name == away:
                        prices["away"] = price
                    elif name.lower() in ("draw", "tie"):
                        prices["draw"] = price
                if {"home", "draw", "away"} <= set(prices):
                    books[key] = prices
            if books:
                out.append({"home": home, "away": away,
                            "commence_time": ev.get("commence_time", ""), "books": books})
        except Exception:
            continue
    return out


def quote_for_event(event, *, book_priority=DEFAULT_BOOK_PRIORITY):
    """Pick the sharpest available book and return its OddsQuote. None if no book
    has a complete 3-way price."""
    books = (event or {}).get("books") or {}
    if not books:
        return None
    chosen_key = next((b for b in book_priority if b in books), None) or next(iter(books))
    p = books[chosen_key]
    try:
        return OddsQuote(book=chosen_key, home=float(p["home"]),
                         draw=float(p["draw"]), away=float(p["away"]))
    except (KeyError, TypeError, ValueError):
        return None


def _team_tokens(name: str) -> set:
    """Lowercased alphanumeric token set for a team name, dropping only generic
    noise words. Critically KEEPS disambiguators like 'dr'/'republic'/'north' so
    'DR Congo' never collapses into 'Congo' (two-Congos problem)."""
    toks = [t for t in re.findall(r"[a-z0-9]+", (name or "").lower())
            if t not in _TEAM_NOISE_WORDS]
    return set(toks or re.findall(r"[a-z0-9]+", (name or "").lower()))


_TEAM_NOISE_WORDS = {"fc", "national", "team", "and", "of", "the", "&"}


def _team_similarity(a: str, b: str) -> float:
    """Jaccard token overlap in [0,1]. 'Congo Dr' vs 'Dr Congo' -> 1.0; 'Cape
    Verde' vs 'Cape Verde Islands' -> 0.67; 'Guinea' vs 'Equatorial Guinea' -> 0.5."""
    ta, tb = _team_tokens(a), _team_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _swap_sides(ev: dict) -> dict:
    """Flip an event's home/away (and its books) to match our home/away orientation."""
    swapped = dict(ev)
    swapped["home"], swapped["away"] = ev.get("away", ""), ev.get("home", "")
    swapped["books"] = {k: {"home": v["away"], "draw": v["draw"], "away": v["home"]}
                        for k, v in (ev.get("books") or {}).items()}
    return swapped


def match_event(events, home: str, away: str, *, min_similarity: float = 0.6):
    """Find the odds event for a Kalshi fixture. Tries EXACT canonical-name match
    first (fast, safe), then a DYNAMIC fuzzy fallback so spelling variants join
    without a hand-maintained alias per team (DR Congo / Congo DR, Cape Verde /
    Cape Verde Islands, Bosnia & / and Herzegovina, ...).

    The fuzzy step is safe — it only binds when BOTH teams clear the similarity
    threshold AND exactly ONE event matches. That pair-plus-uniqueness rule is
    what prevents the wrong-binding the old exact-only code guarded against
    (Guinea/Guinea-Bissau, the two Koreas, the two Congos): a near-miss on one
    name can't latch on because the other team must also match and the result
    must be unambiguous. A miss/ambiguity degrades to model-only (safe)."""
    nh, na = canonical_team(home), canonical_team(away)
    if not nh or not na:
        return None
    events = events or []

    # 1) Exact canonical match (straight or sides-flipped).
    for ev in events:
        eh, ea = canonical_team(ev.get("home", "")), canonical_team(ev.get("away", ""))
        if eh == nh and ea == na:
            return ev
        if eh == na and ea == nh:
            return _swap_sides(ev)

    # 2) Dynamic fuzzy fallback — both teams must match; bind only if unique.
    candidates = []
    for ev in events:
        eh, ea = ev.get("home", ""), ev.get("away", "")
        straight = min(_team_similarity(home, eh), _team_similarity(away, ea))
        flipped = min(_team_similarity(home, ea), _team_similarity(away, eh))
        score = max(straight, flipped)
        if score >= min_similarity:
            candidates.append((score, ev, flipped > straight))
    if len(candidates) == 1:
        _, ev, is_flipped = candidates[0]
        return _swap_sides(ev) if is_flipped else ev
    return None  # 0 matches, or ambiguous -> model-only


def fetch_events(sport_key: str, api_key: str, *, regions: str = "eu,uk,us",
                 markets: str = "h2h"):  # pragma: no cover - integration
    """Fetch + parse upcoming events with bookmaker odds for a sport. Returns
    (events, quota_remaining|None). Degrade-safe: ([], None) on any failure."""
    if not sport_key or not api_key:
        return ([], None)
    try:
        import requests
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
            params={"apiKey": api_key, "regions": regions, "markets": markets,
                    "oddsFormat": "decimal"},
            timeout=15,
        )
        r.raise_for_status()
        quota = None
        try:
            quota = int(r.headers.get("x-requests-remaining"))
        except (TypeError, ValueError):
            quota = None
        return (parse_events(r.json()), quota)
    except Exception:
        return ([], None)
