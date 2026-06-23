"""Match + market discovery. Classifies Kalshi markets into our internal market
types so the strategy layer can map them to model probabilities. The exact
Kalshi ticker/subtitle format must be confirmed in Phase 0; the classifier is
keyword-based and easy to extend. The classifier is pure + unit-tested; the
live listing (KalshiClient.get_markets per event) is integration."""
from __future__ import annotations


# Confirmed Kalshi soccer series tickers. World Cup = KXWCGAME (from the market
# URL demo.kalshi.co/markets/kxwcgame/...). Add more as the discovery logs reveal
# them (EPL, La Liga, MLS, Serie A, ... each have their own series).
DEFAULT_SOCCER_SERIES = ["KXWCGAME"]


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
            return (t[:i].strip(" ?."), t[i + len(sep):].strip(" ?."))
    return (None, None)


def parse_kalshi_market(m: dict) -> dict:
    """Normalize a raw Kalshi market dict into the fields the strategy needs."""
    ticker = m.get("ticker", "")
    subtitle = m.get("yes_sub_title", m.get("subtitle", "")) or ""
    title = m.get("title", "") or ""
    home, away = extract_teams(title or m.get("event_ticker", ""))
    mt = market_type_from_ticker(ticker, f"{subtitle} {title}")
    sub_l = subtitle.strip().lower()
    side = sub_l or "yes"

    # A market whose YES side is a team name is a winner market; map the side to
    # home/away/draw so the scoreline model (home/draw/away) can price it.
    if home and away:
        if sub_l == home.lower():
            mt, side = "winner", "home"
        elif sub_l == away.lower():
            mt, side = "winner", "away"
        elif "draw" in sub_l or "tie" in sub_l:
            mt, side = "winner", "draw"

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
