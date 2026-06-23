"""OddsPapi ingestion — the load-bearing odds source (Pinnacle sharp anchor +
Kalshi/prediction-market lines). The free tier is ~250 requests/month, so a
budget guard throttles the daily scan and refuses to overspend; what gets
skipped is logged, never silently dropped (feature #5).

The OddsPapi key is a QUERY PARAM, not a header. The exact response schema must
be confirmed in Phase 0; `parse_three_way` works on a normalized shape that the
client maps raw responses into.
"""
from __future__ import annotations

from typing import Any, Optional

from kalshi.models import OddsQuote

DEFAULT_MONTHLY_LIMIT = 250


def budget_remaining(used: int, limit: int = DEFAULT_MONTHLY_LIMIT) -> int:
    return max(0, limit - used)


def should_scan(used: int, cost: int, limit: int = DEFAULT_MONTHLY_LIMIT) -> bool:
    """Allow a scan only if it fits within the remaining monthly budget."""
    return cost > 0 and budget_remaining(used, limit) >= cost


def fixtures_per_day_budget(used: int, days_left_in_month: int, limit: int = DEFAULT_MONTHLY_LIMIT) -> int:
    """Adaptive throttle: how many fixtures we can afford to scan per remaining
    day without blowing the month's budget."""
    if days_left_in_month <= 0:
        return 0
    return budget_remaining(used, limit) // days_left_in_month


def parse_three_way(raw: dict, book: str = "pinnacle") -> Optional[OddsQuote]:
    """Extract a 3-way (home/draw/away) decimal-odds quote for `book` from a
    normalized response: {"books": {"pinnacle": {"home": 2.0, "draw": 3.5,
    "away": 4.0}}}. Returns None if the book or a price is missing."""
    books = (raw or {}).get("books", {})
    b = books.get(book) or books.get(book.lower())
    if not b:
        return None
    try:
        return OddsQuote(book=book, home=float(b["home"]), draw=float(b["draw"]), away=float(b["away"]))
    except (KeyError, TypeError, ValueError):
        return None


class OddsPapiClient:
    def __init__(self, api_key: str, session: Any = None, base_url: str = "https://api.oddspapi.io"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        if session is None:
            import requests
            session = requests.Session()
        self._session = session

    def fetch_raw(self, path: str, params: dict | None = None) -> dict:
        p = dict(params or {})
        p["apiKey"] = self.api_key  # query param, not a header
        resp = self._session.request("GET", f"{self.base_url}{path}", params=p, timeout=20.0)
        resp.raise_for_status()
        return resp.json() if resp.content else {}
