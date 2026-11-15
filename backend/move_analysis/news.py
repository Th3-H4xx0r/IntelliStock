"""News for explaining one day's move: articles about the company, and
articles about its peers, its industry and the market that day.

Company articles must be stamped between the previous close and that day's
close, so a release that came out after the move cannot be handed to the
model as its cause. Peer and market articles may run a few hours past the
close, because that is when the day's recaps are written; they are marked so
the model reads them as accounts of the session, not as new events. Those from
the evening before are marked too: they may report something released after
that close, which matters today, or only recap the earlier session.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
MAX_ARTICLES = 25
MAX_CONTEXT = 20
SUMMARY_CHARS = 500
#: How long after the close a market or peer recap is still accepted.
RECAP_HOURS = 4
MARKET_SYMBOLS = ("SPY", "QQQ")
ALPACA_PAGES = 3
MIN_CONTEXT_SCORE = 2


def news_window(prev_date: Optional[str], day: str) -> tuple:
    """``(start, end)`` in UTC: previous session's 16:00 close to this
    session's 16:00 close, New York time. Anything published after the close
    (an earnings release at 16:05) belongs to the next session's move."""
    this_day = date.fromisoformat(day)
    prev_day = date.fromisoformat(prev_date) if prev_date else this_day - timedelta(days=3)
    start = datetime.combine(prev_day, dtime(16, 0), tzinfo=NY)
    end = datetime.combine(this_day, dtime(16, 0), tzinfo=NY)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _parse_stamp(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _iso(stamp: datetime) -> str:
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(text, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _http_url(value) -> str:
    url = str(value or "").strip()
    return url if url.startswith(("https://", "http://")) else ""


def alpaca_credentials(store, brokerage_id: Optional[str] = None) -> Optional[tuple]:
    """Market-data keys from a linked Alpaca account: the named one, else a
    live account. Other paper accounts are never borrowed, because some are
    reserved for a single instance. None when there is none."""
    from stock_credential_boundary import resolve_alpaca_brokerage_credentials

    try:
        rows = [row for row in store.run("BrokerageAccounts")
                if str((row or {}).get("brokerage_type") or "").strip().lower() == "alpaca"]
    except Exception:
        return None
    named = [row for row in rows if str(row.get("id")) == str(brokerage_id or "")]
    live = [row for row in rows if row.get("alpaca_paper") is False and row not in named]
    for row in named + live:
        try:
            creds = resolve_alpaca_brokerage_credentials(
                row, expected_brokerage_id=str(row.get("id")))
        except Exception:
            continue
        if creds.key and creds.secret:
            return creds.key, creds.secret
    return None


def fetch_alpaca(symbol: str, start: datetime, end: datetime, key: str,
                 secret: str, *, http_get: Optional[Callable] = None) -> list:
    """Articles tagged with ``symbol`` (or any of several, comma separated).
    A several-symbol request reads up to ``ALPACA_PAGES`` pages."""
    if http_get is None:
        import requests

        http_get = requests.get
    params = {
        # Oldest first: when a busy day exceeds the page, what is cut is the
        # late recap of the move, not the overnight news that caused it.
        # Alpaca writes share classes with a dot (BRK.B).
        "symbols": symbol.replace("-", "."), "start": _iso(start),
        "end": _iso(end), "limit": 50,
        # Several symbols are a search for the day's recaps, which come last.
        "sort": "desc" if "," in symbol else "asc",
        "include_content": "false"}
    out = []
    for page in range(ALPACA_PAGES if "," in symbol else 1):
        try:
            response = http_get(
                ALPACA_NEWS_URL,
                headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret,
                         "accept": "application/json"},
                params=dict(params), timeout=20)
            response.raise_for_status()
            body = response.json() or {}
        except Exception:
            if page == 0:
                raise
            break       # keep the pages already read
        raw = body.get("news") or []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            stamp = _parse_stamp(item.get("created_at"))
            title = _clean(item.get("headline"), 300)
            if stamp is None or not title or not (start <= stamp <= end):
                continue
            symbols = item.get("symbols") if isinstance(item.get("symbols"), list) else []
            out.append({
                "title": title,
                "summary": _clean(item.get("summary"), SUMMARY_CHARS),
                "url": _http_url(item.get("url")),
                "source": _clean(item.get("source") or "Benzinga", 60),
                "published_at": _iso(stamp),
                "_breadth": len(symbols) or 99,
            })
        token = body.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    return out


def fetch_google(query: str, start: datetime, end: datetime) -> list:
    from strategies.google_news import fetch_google_news

    raw = fetch_google_news(
        # The helper only escapes spaces; a bare "&" (AT&T) would end the query.
        keywords=[query.replace("&", "%26")],
        start_date=start.replace(tzinfo=None),
        # Google's "before:" excludes the date it names, so ask through the
        # next day; the stamp check below still enforces the real window.
        end_date=(end + timedelta(days=1)).replace(tzinfo=None),
        max_results_per_keyword=20,
        max_total=20,
    )
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        stamp = _parse_stamp(item.get("published_date"))
        title = _clean(item.get("title") or item.get("headline"), 300)
        if stamp is None or not title or not (start <= stamp <= end):
            continue
        out.append({
            "title": title,
            "summary": _clean(item.get("description"), SUMMARY_CHARS),
            "url": _http_url(item.get("url")),
            "source": _clean(item.get("source") or "Google News", 60),
            "published_at": _iso(stamp),
            "_breadth": 50,
        })
    return out


_RECAP_WORDS = ("stock market today", "stocks", "wall street", "nasdaq", "s&p",
                "dow ", "fed ", "yields", "treasury", "selloff", "sell-off",
                "rally", "rebound", "rotation", "inflation", "jobs report")
_MOVE_WORDS = ("falls", "fall ", "drops", "plunge", "slides", "sinks", "tumble",
               "slump", "crash", "jumps", "surges", "soars", "rallies", "climbs",
               "rises", "gains", "moving", "trading", "higher", "lower", "why ")
_EVENT_WORDS = ("earnings", "results", "guidance", "forecast", "outlook",
                "launch", "unveil", "beats", "misses", "upgrade", "downgrade",
                "acquire", "acquisition")
_NOISE_WORDS = ("price target", "maintains", "reiterates", "initiates coverage",
                "investigation", "class action", "shareholder alert", "deadline")


def _names(title: str, symbol: str) -> bool:
    """True when the title names the ticker itself. Case matters: NOW, IT, ON
    and ALL are also ordinary words."""
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])", title) is not None


def _about_company(title: str, symbol: str, name: str) -> bool:
    return bool(name and name.lower() in title.lower()) or _names(title, symbol)


def _context_score(title: str, symbol: str, name: str, group: str,
                   peers: tuple = ()) -> int:
    """How useful a peer or market article is for explaining a group move."""
    text = f" {title.lower()} "
    score = 0
    if _about_company(title, symbol, name):
        score += 3
    if any(word in text for word in _RECAP_WORDS):
        score += 2
    if any(word in text for word in _MOVE_WORDS):
        score += 2
    # A peer's results or a launch is what usually moves a whole group.
    if any(word in text for word in _EVENT_WORDS):
        score += 2
    if any(_names(title, peer) for peer in peers):
        score += 1
    if group and group.replace("_", " ").split()[0] in text:
        score += 1
    if any(word in text for word in _NOISE_WORDS):
        score -= 3
    return score


def _safe(fetch: Callable, *args) -> list:
    try:
        return list(fetch(*args) or [])
    except Exception:
        return []


def _unique(found: list, seen: set) -> list:
    out = []
    for article in found:
        key = article["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(article)
    return out


def _public(article: dict, scope: str, after_close: bool = False,
            previous_evening: bool = False) -> dict:
    row = {k: v for k, v in article.items() if not k.startswith("_")}
    row["scope"] = scope
    if after_close:
        row["after_close"] = True
    if previous_evening:
        row["previous_evening"] = True
    return row


def articles_for(symbol: str, prev_date: Optional[str], day: str, *,
                 credentials: Optional[tuple] = None,
                 name: str = "", peers: tuple = (), etf: Optional[str] = None,
                 group: str = "",
                 alpaca: Callable = fetch_alpaca,
                 google: Callable = fetch_google) -> list:
    """Company articles (up to ``MAX_ARTICLES``, those about fewest other
    symbols first, then oldest first) followed by peer and market articles
    (up to ``MAX_CONTEXT``, most useful first). Each carries ``scope``:
    "company" or "context". Never raises: no news is an answer."""
    start, end = news_window(prev_date, day)
    recap_end = end + timedelta(hours=RECAP_HOURS)
    seen: set = set()

    company: list = []
    if credentials:
        company += _safe(alpaca, symbol, start, end, credentials[0], credentials[1])
    # Unquoted: Google's dated search returns almost nothing for a quoted name.
    company += _safe(google, f"{name or symbol} stock", start, end)
    company = _unique(company, seen)
    company.sort(key=lambda a: (a.get("_breadth", 99), a["published_at"]))
    company = company[:MAX_ARTICLES]

    context: list = []
    others = [s for s in list(peers)[:6] + [etf, *MARKET_SYMBOLS] if s and s != symbol]
    if credentials and others:
        context += _safe(alpaca, ",".join(dict.fromkeys(others)), start, recap_end,
                         credentials[0], credentials[1])
    label = group.replace("_", " ") if group else ""
    context += _safe(google, f"{label} stocks" if label else "stock market today",
                     start, recap_end)
    context = _unique(context, seen)
    end_iso = _iso(end)
    evening_iso = _iso(start + timedelta(hours=RECAP_HOURS))
    # A write-up about the company itself from after the close may carry
    # results or a deal announced after it, so it is left out.
    context = [a for a in context
               if not (a["published_at"] > end_iso
                       and _about_company(a["title"], symbol, name))]
    scored = [(_context_score(a["title"], symbol, name, group, tuple(peers)), a)
              for a in context]
    # 2 and up: a bare mention of a peer or the industry is not enough.
    scored = [pair for pair in scored if pair[0] >= MIN_CONTEXT_SCORE]
    # Best first; among equals the latest, which is the recap of this session.
    scored.sort(key=lambda pair: pair[1]["published_at"], reverse=True)
    scored.sort(key=lambda pair: -pair[0])

    return ([_public(a, "company") for a in company]
            + [_public(a, "context", a["published_at"] > end_iso,
                       a["published_at"] <= evening_iso)
               for _, a in scored[:MAX_CONTEXT]])
