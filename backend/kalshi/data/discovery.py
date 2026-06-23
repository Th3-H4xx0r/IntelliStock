"""Match + market discovery. Classifies Kalshi markets into our internal market
types so the strategy layer can map them to model probabilities. The exact
Kalshi ticker/subtitle format must be confirmed in Phase 0; the classifier is
keyword-based and easy to extend. The classifier is pure + unit-tested; the
live listing (KalshiClient.get_markets per event) is integration."""
from __future__ import annotations


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
    if "winner" in t or "moneyline" in t or "to win" in t or "h2h" in t or "result" in t:
        return "winner"
    return "other"
