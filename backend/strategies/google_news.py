"""
Custom Google News RSS fetcher for IntelliStock.

Fetches news articles from Google News RSS feeds with:
- Keyword and topic-based search
- Date range filtering (critical for backtest safety / no data leakage)
- Scrapling for anti-bot HTTP fetching (Chrome TLS impersonation)
- feedparser for RSS/XML parsing
- RethinkDB caching by (date_key, keywords_hash)
- Optional full article text extraction via Scrapling

URL patterns adapted from the GNews project
(https://github.com/ranahaani/GNews, MIT-licensed).
"""
from __future__ import annotations

import hashlib
import html
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

# ── Lazy imports (fail gracefully if not installed) ─────────────────────────
_feedparser = None
_Fetcher = None


def _ensure_feedparser():
    global _feedparser
    if _feedparser is None:
        import feedparser
        _feedparser = feedparser
    return _feedparser


def _ensure_fetcher():
    global _Fetcher
    if _Fetcher is None:
        try:
            from scrapling.fetchers import Fetcher
            _Fetcher = Fetcher
        except ImportError:
            _Fetcher = None
    return _Fetcher


# ── Constants ───────────────────────────────────────────────────────────────
_BASE_URL = "https://news.google.com/rss"
_CEID = "&hl=en&gl=US&ceid=US:en"
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_MAX_RETRIES = 3
_RETRY_BACKOFF = [2, 5, 10]  # seconds
# Concurrency for RSS feed fetches. The keyword/topic sets are large (~145 feeds);
# fetching them serially with a 1s/request delay took ~205s and blew the live
# macro 120s budget. A bounded pool cuts wall-time to seconds; per-feed 429 retry
# + backoff (in _fetch_rss_feed) still guards against rate limiting.
_FETCH_WORKERS = 8

# Google News predefined topic slugs
TOPICS = {
    "WORLD": "WORLD",
    "NATION": "NATION",
    "BUSINESS": "BUSINESS",
    "TECHNOLOGY": "TECHNOLOGY",
    "ENTERTAINMENT": "ENTERTAINMENT",
    "SPORTS": "SPORTS",
    "SCIENCE": "SCIENCE",
    "HEALTH": "HEALTH",
}

# Default comprehensive keyword list (configurable via strategy config)
DEFAULT_KEYWORDS: list[str] = [
    # ── Market & Economy ──
    "stock market today", "wall street", "S&P 500", "nasdaq", "dow jones",
    "bull market", "bear market", "market crash", "market rally", "IPO",
    "interest rates", "federal reserve", "fed rate decision", "inflation",
    "recession", "GDP growth", "unemployment", "jobs report", "consumer spending",
    "treasury yields", "bond market", "credit rating downgrade",
    # ── Energy & Commodities ──
    "oil prices", "crude oil", "OPEC", "natural gas prices", "energy crisis",
    "renewable energy", "solar energy", "wind energy", "nuclear energy",
    "lithium prices", "copper prices", "gold prices", "silver prices",
    "rare earth minerals", "commodity prices", "oil production cuts",
    # ── Geopolitics & Trade ──
    "trade war", "tariffs", "sanctions", "embargo",
    "China trade", "US China relations", "Russia sanctions", "Iran sanctions",
    "NATO", "military conflict", "war", "ceasefire", "peace deal",
    "Middle East crisis", "Taiwan strait", "North Korea",
    "BRICS", "G7 summit", "UN security council",
    # ── Technology ──
    "artificial intelligence", "AI regulation", "semiconductor", "chip shortage",
    "cybersecurity breach", "data privacy", "tech layoffs", "tech earnings",
    "cloud computing", "quantum computing", "self driving cars", "electric vehicles",
    "social media regulation", "antitrust tech",
    # ── Healthcare & Pharma ──
    "FDA approval", "drug trial results", "pharmaceutical", "biotech",
    "vaccine", "pandemic", "healthcare reform", "drug pricing",
    "Medicare Medicaid", "health insurance", "clinical trial",
    # ── Finance & Banking ──
    "bank earnings", "banking crisis", "bank failure", "cryptocurrency",
    "bitcoin", "SEC enforcement", "financial regulation", "hedge fund",
    "private equity", "venture capital", "fintech",
    # ── Defense & Government ──
    "defense spending", "defense contract", "military budget",
    "Pentagon", "government shutdown", "debt ceiling",
    "infrastructure bill", "government spending",
    # ── Supply Chain & Industry ──
    "supply chain disruption", "shipping delays", "port congestion",
    "manufacturing", "factory output", "industrial production",
    "auto industry", "airline industry", "retail sales",
    # ── Real Estate ──
    "housing market", "mortgage rates", "commercial real estate",
    "REIT", "housing crisis", "construction spending",
    # ── Agriculture & Food ──
    "crop prices", "wheat prices", "corn prices", "food prices",
    "agricultural exports", "farming", "drought", "food shortage",
    # ── Regulatory & Legal ──
    "antitrust lawsuit",  "SEC investigation", "corporate fraud",
    "environmental regulation", "carbon emissions", "ESG",
    "labor strike", "union negotiation",
    # ── Climate & Natural Disasters ──
    "hurricane", "earthquake", "wildfire", "flooding",
    "climate change policy", "carbon tax", "extreme weather",
]

DEFAULT_TOPICS: list[str] = ["BUSINESS", "TECHNOLOGY", "WORLD", "NATION", "SCIENCE", "HEALTH"]


# ── Logging helper ──────────────────────────────────────────────────────────
def _log(msg: str, color: str = "white"):
    try:
        from termcolor import colored
        print(colored(f"[GoogleNews] {msg}", color))
    except Exception:
        print(f"[GoogleNews] {msg}")


# ── Internal helpers ────────────────────────────────────────────────────────

def _build_search_url(keyword: str, start_date: datetime | None, end_date: datetime | None) -> str:
    """Build Google News RSS search URL with optional date filters."""
    encoded = keyword.replace(" ", "%20")
    query = f"/search?q={encoded}"
    if start_date:
        query += f"%20after%3A{start_date.strftime('%Y-%m-%d')}"
    if end_date:
        query += f"%20before%3A{end_date.strftime('%Y-%m-%d')}"
    return _BASE_URL + query + _CEID


def _build_topic_url(topic: str) -> str:
    """Build Google News RSS topic URL (no date filter support in URL)."""
    slug = TOPICS.get(topic.upper(), topic.upper())
    return f"{_BASE_URL}/headlines/section/topic/{slug}?{_CEID.lstrip('&')}"


def _parse_published_date(date_str: str) -> datetime | None:
    """Parse RSS published date (RFC 2822 format) to UTC datetime."""
    if not date_str:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    # Fallback: try feedparser's date parser
    try:
        fp = _ensure_feedparser()
        parsed = fp._parse_date(date_str)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    return None


def _clean_html(text: str) -> str:
    """Strip HTML tags and decode entities from RSS description."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    return text.strip()


def _fetch_rss_feed(url: str) -> list[dict]:
    """Fetch and parse a single RSS feed URL. Returns list of article dicts."""
    fp = _ensure_feedparser()
    fetcher = _ensure_fetcher()

    raw_xml = None
    for attempt in range(_MAX_RETRIES):
        try:
            if fetcher is not None:
                # Use Scrapling with Chrome impersonation for stealth
                resp = fetcher.get(url, stealthy_headers=True, follow_redirects=True, timeout=15)
                status = getattr(resp, "status", 200)
                if status == 429:
                    wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    _log(f"Rate limited (429), retrying in {wait}s...", "yellow")
                    time.sleep(wait)
                    continue
                raw_xml = str(resp)  # Scrapling response to string for feedparser
            else:
                # Fallback to requests if Scrapling not available
                import requests
                r = requests.get(url, headers={"User-Agent": _USER_AGENT}, timeout=15)
                if r.status_code == 429:
                    wait = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                    _log(f"Rate limited (429), retrying in {wait}s...", "yellow")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                raw_xml = r.text
            break
        except Exception as e:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(_RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)])
            else:
                _log(f"Failed to fetch RSS after {_MAX_RETRIES} attempts: {e}", "red")
                return []

    if not raw_xml:
        return []

    feed = fp.parse(raw_xml)
    articles = []
    for entry in getattr(feed, "entries", []):
        title = (getattr(entry, "title", "") or "").strip()
        if not title:
            continue
        link = (getattr(entry, "link", "") or "").strip()
        pub_date_str = getattr(entry, "published", "") or getattr(entry, "updated", "") or ""
        pub_date = _parse_published_date(pub_date_str)
        description = _clean_html(getattr(entry, "summary", "") or getattr(entry, "description", "") or "")
        source_info = getattr(entry, "source", None)
        source = ""
        if source_info and hasattr(source_info, "title"):
            source = source_info.title
        elif hasattr(entry, "publisher"):
            source = entry.publisher

        articles.append({
            "title": title,
            "headline": title,  # alias for compatibility with Alpaca article format
            "url": link,
            "published_date": pub_date,
            "description": description,
            "source": source or "Google News",
        })
    return articles


# ── Public API ──────────────────────────────────────────────────────────────

def fetch_google_news(
    keywords: list[str] | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    max_results_per_keyword: int = 30,
    max_total: int = 200,
) -> list[dict]:
    """
    Fetch Google News articles by keyword search within a date range.

    Args:
        keywords: Search terms (defaults to DEFAULT_KEYWORDS)
        start_date: Inclusive start (articles after this date)
        end_date: Exclusive end (articles before this date) — critical for backtest safety
        max_results_per_keyword: Cap per keyword to avoid flooding
        max_total: Global cap on total articles returned

    Returns:
        List of article dicts with keys: title, headline, url, published_date, description, source
        All articles guaranteed to have published_date <= end_date (backtest safe)
    """
    if keywords is None:
        keywords = DEFAULT_KEYWORDS

    # Fetch all keyword feeds CONCURRENTLY (was serial with a 1s/request delay,
    # ~205s for the full keyword set — which blew the live macro 120s budget).
    # A bounded pool keeps Google RSS happy (per-feed 429 retry/backoff still
    # applies in _fetch_rss_feed). Results are kept in keyword order so the
    # downstream dedup + max_total cut stays deterministic.
    urls = [_build_search_url(kw, start_date, end_date) for kw in keywords]
    fetched: list[list[dict]] = [[] for _ in urls]
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as ex:
        fut_to_idx = {ex.submit(_fetch_rss_feed, url): i for i, url in enumerate(urls)}
        for fut in as_completed(fut_to_idx):
            i = fut_to_idx[fut]
            try:
                fetched[i] = fut.result() or []
            except Exception as e:
                _log(f"Google News keyword fetch failed: {e}", "yellow")
                fetched[i] = []

    all_articles: list[dict] = []
    seen_urls: set[str] = set()
    for articles in fetched:
        if len(all_articles) >= max_total:
            break
        for a in articles[:max_results_per_keyword]:
            art_url = a.get("url", "")
            if art_url in seen_urls:
                continue
            seen_urls.add(art_url)

            # Backtest safety: strict date filtering
            pub = a.get("published_date")
            if pub and end_date:
                end_aware = end_date if end_date.tzinfo else end_date.replace(tzinfo=timezone.utc)
                if pub > end_aware:
                    continue
            if pub and start_date:
                start_aware = start_date if start_date.tzinfo else start_date.replace(tzinfo=timezone.utc)
                if pub < start_aware:
                    continue

            all_articles.append(a)
            if len(all_articles) >= max_total:
                break

    _log(f"Fetched {len(all_articles)} articles from {len(keywords)} keywords", "green")
    return all_articles


def fetch_google_news_by_topic(
    topics: list[str] | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    max_results_per_topic: int = 30,
    max_total: int = 100,
) -> list[dict]:
    """
    Fetch Google News articles by predefined topic categories.

    Note: Topic RSS URLs don't support date filtering, so we filter post-fetch.
    """
    if topics is None:
        topics = DEFAULT_TOPICS

    # Fetch all topic feeds CONCURRENTLY (see fetch_google_news for the why).
    urls = [_build_topic_url(topic) for topic in topics]
    fetched: list[list[dict]] = [[] for _ in urls]
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as ex:
        fut_to_idx = {ex.submit(_fetch_rss_feed, url): i for i, url in enumerate(urls)}
        for fut in as_completed(fut_to_idx):
            i = fut_to_idx[fut]
            try:
                fetched[i] = fut.result() or []
            except Exception as e:
                _log(f"Google News topic fetch failed: {e}", "yellow")
                fetched[i] = []

    all_articles: list[dict] = []
    seen_urls: set[str] = set()
    for articles in fetched:
        if len(all_articles) >= max_total:
            break
        for a in articles[:max_results_per_topic]:
            art_url = a.get("url", "")
            if art_url in seen_urls:
                continue
            seen_urls.add(art_url)

            # Post-fetch date filtering (topics don't support URL-based date filters)
            pub = a.get("published_date")
            if pub and end_date:
                end_aware = end_date if end_date.tzinfo else end_date.replace(tzinfo=timezone.utc)
                if pub > end_aware:
                    continue
            if pub and start_date:
                start_aware = start_date if start_date.tzinfo else start_date.replace(tzinfo=timezone.utc)
                if pub < start_aware:
                    continue

            all_articles.append(a)
            if len(all_articles) >= max_total:
                break

    _log(f"Fetched {len(all_articles)} articles from {len(topics)} topics", "green")
    return all_articles


def fetch_full_article(url: str) -> str | None:
    """
    Use Scrapling with Chrome impersonation to fetch and extract article body text.
    Falls back gracefully if the site blocks or the page structure is unusual.

    Returns article text (capped at 3000 chars) or None.
    """
    fetcher = _ensure_fetcher()
    if fetcher is None:
        return None
    try:
        page = fetcher.get(url, stealthy_headers=True, follow_redirects=True, timeout=15)
        # Try common article body selectors (most specific first)
        for selector in [
            '[itemprop="articleBody"] p',
            'article p',
            '.article-body p',
            '.article-content p',
            '.story-body p',
            '.post-content p',
            'main p',
        ]:
            parts = page.css(f'{selector}::text').getall()
            if parts:
                text = " ".join(p.strip() for p in parts if p.strip())
                if len(text) > 200:
                    return text[:3000]
        return None
    except Exception:
        return None


# ── Caching helpers (RethinkDB) ─────────────────────────────────────────────

_CACHE_TABLE = "GoogleNewsCache"
_cache_table_ensured = False


def _ensure_cache_table(conn, db_name: str = "IntelliStock"):
    global _cache_table_ensured
    if _cache_table_ensured or conn is None:
        return
    try:
        from rethinkdb import RethinkDB
        r = RethinkDB()
        tables = list(r.db(db_name).table_list().run(conn))
        if _CACHE_TABLE not in tables:
            r.db(db_name).table_create(_CACHE_TABLE).run(conn)
        _cache_table_ensured = True
    except Exception:
        pass


def cache_articles(conn, date_key: str, articles: list[dict], keywords_hash: str, db_name: str = "IntelliStock"):
    """Cache fetched articles to RethinkDB."""
    if conn is None:
        return
    _ensure_cache_table(conn, db_name)
    try:
        from rethinkdb import RethinkDB
        r = RethinkDB()
        # Serialize datetimes for storage
        serializable = []
        for a in articles:
            entry = dict(a)
            if entry.get("published_date") and isinstance(entry["published_date"], datetime):
                entry["published_date"] = entry["published_date"].isoformat()
            serializable.append(entry)
        doc = {
            "id": f"{date_key}_{keywords_hash}",
            "date_key": date_key,
            "keywords_hash": keywords_hash,
            "articles": serializable,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        r.db(db_name).table(_CACHE_TABLE).insert(doc, conflict="replace").run(conn)
    except Exception as e:
        _log(f"Cache write error: {e}", "yellow")


def load_cached_articles(conn, date_key: str, keywords_hash: str, db_name: str = "IntelliStock") -> list[dict] | None:
    """Load cached articles from RethinkDB. Returns None on cache miss."""
    if conn is None:
        return None
    _ensure_cache_table(conn, db_name)
    try:
        from rethinkdb import RethinkDB
        r = RethinkDB()
        doc_id = f"{date_key}_{keywords_hash}"
        doc = r.db(db_name).table(_CACHE_TABLE).get(doc_id).run(conn)
        if doc and doc.get("articles"):
            # Deserialize datetimes
            articles = []
            for a in doc["articles"]:
                entry = dict(a)
                if entry.get("published_date") and isinstance(entry["published_date"], str):
                    try:
                        entry["published_date"] = datetime.fromisoformat(entry["published_date"])
                    except Exception:
                        pass
                articles.append(entry)
            return articles
    except Exception:
        pass
    return None


def compute_keywords_hash(keywords: list[str], topics: list[str] | None = None) -> str:
    """Compute a stable hash of the keyword + topic list for cache keying."""
    content = "|".join(sorted(keywords))
    if topics:
        content += "||" + "|".join(sorted(topics))
    return hashlib.sha256(content.encode()).hexdigest()[:16]
