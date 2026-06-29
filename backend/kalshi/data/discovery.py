"""Match + market discovery. Classifies Kalshi markets into our internal market
types so the strategy layer can map them to model probabilities. The exact
Kalshi ticker/subtitle format must be confirmed in Phase 0; the classifier is
keyword-based and easy to extend. The classifier is pure + unit-tested; the
live listing (KalshiClient.get_markets per event) is integration."""
from __future__ import annotations

import re


# Confirmed Kalshi soccer series tickers. World Cup = KXWCGAME (from the market
# URL demo.kalshi.co/markets/kxwcgame/...). Add more as the discovery logs reveal
# them (EPL, La Liga, MLS, Serie A, ... each have their own series).
DEFAULT_SOCCER_SERIES = ["KXWCGAME"]

# Series whose teams are national sides (price via the national-Elo table, with
# the national average as the default for unknowns — NOT the club default).
NATIONAL_TEAM_SERIES = {"KXWCGAME"}

# Words a Kalshi title appends after the away team to label the market
# ("Argentina vs Austria Winner"). They contaminate the extracted away-team name,
# so strip them before any team lookup.
_MARKET_SUFFIX_TOKENS = {
    "winner", "moneyline", "result", "h2h", "btts", "total", "totals", "goals",
    "over", "under", "draw", "tie", "corners", "cards", "halftime", "ht",
    "scorer", "anytime", "score", "to", "win", "double", "chance", "correct",
    "both", "teams", "match", "outcome",
}


def _strip_market_suffix(name: str) -> str:
    """Drop a trailing market label ('Winner', 'Total Goals', 'BTTS', ...) from a
    team name extracted off a market title."""
    words = (name or "").split()
    while words and words[-1].lower().strip("?.:") in _MARKET_SUFFIX_TOKENS:
        words.pop()
    return " ".join(words).strip(" ?.")


def market_type_from_ticker(ticker: str = "", subtitle: str = "") -> str:
    """Best-effort classification of a Kalshi soccer market into an internal type."""
    t = f"{ticker} {subtitle}".lower()
    if "double chance" in t:
        return "double_chance"
    # BTTS must be checked before player "to score" (it contains "to score").
    if "btts" in t or "both teams" in t or "both to score" in t:
        return "btts"
    if "to score" in t or "scorer" in t or "anytime" in t:
        return "player_score"
    if "correct score" in t or "exact" in t:
        return "exact_score"
    if "over" in t or "under" in t or "total" in t:
        return "over_under"
    if "half" in t or "ht" in t:
        return "halftime"
    if "corner" in t:
        return "corners"
    if "card" in t:
        return "cards"
    if "winner" in t or "moneyline" in t or "to win" in t or "h2h" in t or "result" in t or "beat" in t:
        return "winner"
    return "other"


_TEAM_SEPS = [" vs. ", " vs ", " v. ", " v ", " @ ", " at ", " against ", " beats ", " — ", " - "]


def extract_teams(text: str) -> tuple:
    """Best-effort (home, away) from a Kalshi market/event title. None when it
    can't be parsed — the engine then skips/logs the market."""
    t = (text or "").strip()
    for sep in _TEAM_SEPS:
        if sep in t.lower():
            i = t.lower().index(sep)
            home = _strip_market_suffix(t[:i].strip(" ?."))
            away = _strip_market_suffix(t[i + len(sep):].strip(" ?."))
            return (home or None, away or None)
    return (None, None)


# Knockout-stage winner markets label the side with a regulation/extra-time
# qualifier — "Reg Time: Argentina", "Argentina (Reg. Time)". Strip these so the
# bare team name is left to match against home/away. (Group-stage markets have no
# qualifier; the bot mapped those fine — knockout games are where it went blind.)
_SIDE_QUALIFIER = re.compile(
    r"\b(regulation\s*time|reg\.?\s*time|full\s*time|90\s*min(?:ute)?s?"
    r"|after\s*extra\s*time|incl\.?\s*extra\s*time|extra\s*time|a\.?e\.?t\.?"
    r"|\bf\.?t\.?\b)\b",
    re.IGNORECASE,
)

# Subtitles for tournament-level / aggregate markets that contain a team name but
# are NOT a single-match 1X2 winner ("Argentina to advance", "... to win the World
# Cup"). market_type_from_ticker maps "to win" to winner, so these must be excluded
# explicitly or they'd be mispriced with the match-winner probability.
_NON_WINNER_SUBTITLE = (
    "advance", "qualif", "champion", "world cup", "to lift", "trophy",
    "group", "to win the", "wins the", "reach", "finalist", "final four",
)


def _normalize_side_label(s: str) -> str:
    """Reduce a winner-market subtitle to a bare lowercased team label, dropping
    parentheticals, punctuation, and regulation/extra-time qualifiers so both
    'Reg Time: Argentina' and 'Argentina (Reg. Time)' collapse to 'argentina'."""
    s = re.sub(r"\([^)]*\)", " ", s or "")        # drop "(Reg. Time)"
    s = s.replace(":", " ").replace("-", " ")
    s = _SIDE_QUALIFIER.sub(" ", s)
    return " ".join(s.lower().split())


def winner_side(subtitle: str, home: str, away: str):
    """Map a winner-market subtitle to 'home' / 'away' / 'draw'. Tolerates knockout
    'Reg Time:' qualifiers and minor name spelling differences via containment, and
    resolves substring-overlapping country names (Guinea / Equatorial Guinea) by
    preferring an exact match. Returns None when the subtitle is not a 1X2 side."""
    sub_l = (subtitle or "").strip().lower()
    if not sub_l:
        return None
    if any(k in sub_l for k in _NON_WINNER_SUBTITLE):
        return None
    if "draw" in sub_l or "tie" in sub_l:
        return "draw"
    label = _normalize_side_label(subtitle)
    if not label:
        return None
    h, a = (home or "").strip().lower(), (away or "").strip().lower()
    # 1) exact normalized match wins — disambiguates e.g. Guinea vs Equatorial Guinea.
    if label == h:
        return "home"
    if label == a:
        return "away"
    # 2) containment either direction (handles "Reg Time: X" leftovers + name drift).
    h_hit = bool(h) and (h in label or label in h)
    a_hit = bool(a) and (a in label or label in a)
    if h_hit and not a_hit:
        return "home"
    if a_hit and not h_hit:
        return "away"
    if h_hit and a_hit:
        # Both names appear as substrings — prefer the one fully named in the label.
        if h in label and a not in label:
            return "home"
        if a in label and h not in label:
            return "away"
        return "home" if len(h) >= len(a) else "away"
    return None


def parse_kalshi_market(m: dict) -> dict:
    """Normalize a raw Kalshi market dict into the fields the strategy needs."""
    ticker = m.get("ticker", "")
    subtitle = m.get("yes_sub_title", m.get("subtitle", "")) or ""
    title = m.get("title", "") or ""
    home, away = extract_teams(title or m.get("event_ticker", ""))
    mt = market_type_from_ticker(ticker, f"{subtitle} {title}")
    sub_l = subtitle.strip().lower()
    side = sub_l or "yes"

    # A market whose YES side is a team name (or "Draw") is a match-winner market;
    # map it to home/away/draw so the scoreline model can price it. Only override
    # when the ticker classifier didn't already pin a specific non-winner type
    # (totals, BTTS, scorer, ...) so those keep their classification.
    if home and away and mt in ("winner", "other"):
        ws = winner_side(subtitle, home, away)
        if ws:
            mt, side = "winner", ws

    return {
        "market_ticker": ticker,
        "event_ticker": m.get("event_ticker", ""),
        "market_type": mt,
        "side": side,
        "yes_ask_cents": int(m.get("yes_ask", 0) or 0),
        "title": title,
        "home": home,
        "away": away,
    }


def group_by_event(parsed_markets: list[dict]) -> dict:
    """Group parsed markets by event_ticker (one match)."""
    out: dict = {}
    for p in parsed_markets:
        out.setdefault(p.get("event_ticker", ""), []).append(p)
    return out
