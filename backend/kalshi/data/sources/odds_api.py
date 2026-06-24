"""Sharp bookmaker odds (The-Odds-API v4) — the fair-value anchor.

Real bookmaker odds are the sharpest available estimate of true probability. We
fetch them, de-vig the sharpest book, and use that as fair value; the edge is then
"sharp consensus vs the Kalshi price" — a genuinely exploitable signal. The odds
are environment-agnostic (same on demo + live); only the Kalshi price we compare
against differs. Parsing + matching + sport-key mapping are pure/tested; the HTTP
fetch is integration and degrade-safe (empty on failure)."""
from __future__ import annotations

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


def match_event(events, home: str, away: str):
    """Find the odds event for a Kalshi fixture by EXACT canonical team name (incl.
    national aliases). Exact-only is deliberate: substring matching confuses
    Guinea/Guinea-Bissau, the two Koreas, the two Congos — binding a fixture to the
    WRONG match's odds. A miss just degrades to model-only (safe); a wrong match
    would bet a fabricated edge. Handles the book listing the sides flipped."""
    nh, na = canonical_team(home), canonical_team(away)
    if not nh or not na:
        return None

    for ev in events or []:
        eh, ea = canonical_team(ev.get("home", "")), canonical_team(ev.get("away", ""))
        if eh == nh and ea == na:
            return ev
        if eh == na and ea == nh:           # sides flipped -> swap books to our home/away
            swapped = dict(ev)
            swapped["home"], swapped["away"] = ev.get("away", ""), ev.get("home", "")
            swapped["books"] = {k: {"home": v["away"], "draw": v["draw"], "away": v["home"]}
                                for k, v in (ev.get("books") or {}).items()}
            return swapped
    return None


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
