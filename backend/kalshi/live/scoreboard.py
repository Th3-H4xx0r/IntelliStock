"""Live scoreline for DISPLAY ONLY (ESPN's free, key-less soccer scoreboard).

Trading stays Kalshi-price-only; this just lets the live cards show the real
score/clock when we can match the fixture by team name. Parsing + matching are
pure/tested; the HTTP fetch is integration and degrade-safe (empty on failure)."""
from __future__ import annotations

import re

from kalshi.normalize import normalize_team

# ESPN soccer league slugs to poll for live scores. World Cup = fifa.world.
DEFAULT_SCOREBOARD_LEAGUES = ("fifa.world",)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_scoreboard(data) -> list[dict]:
    """ESPN scoreboard JSON -> [{home, away, home_score, away_score, clock, state, detail}].
    state is ESPN's 'pre' | 'in' | 'post'. Never raises."""
    out = []
    for ev in (data or {}).get("events", []) or []:
        try:
            comp = (ev.get("competitions") or [ev])[0]
            cs = comp.get("competitors") or []
            home = next((c for c in cs if c.get("homeAway") == "home"), None)
            away = next((c for c in cs if c.get("homeAway") == "away"), None)
            if not home or not away:
                continue
            status = comp.get("status") or ev.get("status") or {}
            st = status.get("type") or {}
            out.append({
                "home": (home.get("team") or {}).get("displayName") or "",
                "away": (away.get("team") or {}).get("displayName") or "",
                "home_score": _int(home.get("score")),
                "away_score": _int(away.get("score")),
                "clock": status.get("displayClock") or "",
                "state": st.get("state") or "",
                "detail": st.get("shortDetail") or st.get("detail") or "",
            })
        except Exception:
            continue
    return out


def _sim(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a == b or a in b or b in a


def match_score(scoreboard, home: str, away: str):
    """Find the scoreboard entry for (home, away) by normalized team name. Handles
    ESPN listing the sides in the opposite order (returns it home-normalized)."""
    nh, na = normalize_team(home), normalize_team(away)
    for e in scoreboard or []:
        eh, ea = normalize_team(e.get("home", "")), normalize_team(e.get("away", ""))
        if _sim(eh, nh) and _sim(ea, na):
            return e
        if _sim(eh, na) and _sim(ea, nh):   # sides flipped -> swap back to our orientation
            return {**e, "home": e["away"], "away": e["home"],
                    "home_score": e["away_score"], "away_score": e["home_score"]}
    return None


def clock_minutes(clock: str):
    """ESPN display clock -> elapsed minutes. "67'" -> 67.0; "45'+7'" -> 52.0;
    empty/unparseable -> None."""
    nums = re.findall(r"\d+", clock or "")
    if not nums:
        return None
    return float(sum(int(n) for n in nums[:2]))   # base minute + stoppage


def live_status(entry):
    """ESPN scoreboard entry -> (phase, elapsed_min) using its state + clock.
    state 'in' -> ('live', minute); 'post' -> ('ended', None); 'pre' ->
    ('pregame', None); unknown -> (None, None). Phase strings match match_clock's."""
    if not entry:
        return (None, None)
    st = (entry.get("state") or "").lower()
    if st == "in":
        return ("live", clock_minutes(entry.get("clock", "")))
    if st == "post":
        return ("ended", None)
    if st == "pre":
        return ("pregame", None)
    return (None, None)


def fetch_scoreboard(leagues=DEFAULT_SCOREBOARD_LEAGUES) -> list[dict]:  # pragma: no cover - integration
    """Live scores from ESPN's public soccer scoreboard. Display-only; never raises."""
    out = []
    try:
        import requests
        for lg in leagues:
            try:
                r = requests.get(
                    f"https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/scoreboard",
                    timeout=8,
                )
                r.raise_for_status()
                out.extend(parse_scoreboard(r.json()))
            except Exception:
                continue
    except Exception:
        return []
    return out
