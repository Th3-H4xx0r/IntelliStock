"""Live-match card assembly (pure + tested). Turns the in-play state the engine
already has — current market mids, the optional ESPN score, the detected event,
a news snippet, and the monitor's recent decisions — into the doc the live cards
read from the kalshi_live table."""
from __future__ import annotations


def market_probs_from_markets(markets) -> dict:
    """{side: de-vig implied prob} for the winner market (home/draw/away) from the
    current mid-prices. De-vig = normalize so the three sides sum to 1."""
    raw = {}
    for m in markets or []:
        if m.get("market_type") == "winner" and m.get("side") in ("home", "draw", "away"):
            mid = m.get("mid_cents")
            if mid:
                raw[m["side"]] = float(mid) / 100.0
    s = sum(raw.values())
    if s > 0:
        return {k: round(v / s, 3) for k, v in raw.items()}
    return raw


def build_live_card(*, instance_id: str, fixture_id: str, home: str, away: str,
                    market_probs: dict, score=None, elapsed_min=None, event: str = "",
                    news: str = "", decisions=None, ts: str = "",
                    home_logo: str = "", away_logo: str = "") -> dict:
    """Assemble the live-match card doc. id = '{instance_id}|{fixture_id}' so the
    engine can upsert one row per (instance, match). home_logo/away_logo are crest
    URLs (ESPN) for the country flags / club badges shown on the card."""
    return {
        "id": f"{instance_id}|{fixture_id}",
        "instance_id": instance_id,
        "fixture_id": fixture_id,
        "home": home,
        "away": away,
        "home_logo": home_logo or "",
        "away_logo": away_logo or "",
        "market_probs": market_probs or {},
        "score": score,                       # {home, away, clock, detail, state} or None
        "elapsed_min": (round(elapsed_min, 1) if isinstance(elapsed_min, (int, float)) else None),
        "event": event or "",
        "news": news or "",
        "decisions": list(decisions or [])[:8],
        "updated_at": ts,
    }
