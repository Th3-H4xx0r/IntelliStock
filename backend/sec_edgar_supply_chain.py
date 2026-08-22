"""
SEC EDGAR supply chain scraper: extract supplier->customer relationships from 10-K and 8-K filings.
Uses sec-edgar-downloader to fetch 10-Ks and parses significant customer/supplier disclosures.

Improvements over original:
- spaCy NER (optional, falls back to regex if not installed)
- rapidfuzz fuzzy name matching (optional, falls back to word-overlap)
- Revenue percentage extraction from disclosure context
- Edge confidence scoring based on source signal strength
- Bidirectional edge cross-validation
- 8-K Item 1.01 material agreement scraping (strategic partnerships, supply deals)
- EdgeRecord TypedDict for structured edge metadata (confidence, source, revenue_pct)
- All edge-returning functions now yield EdgeRecord dicts
"""
from __future__ import annotations

import os
import re
import time
import csv
import threading
from html import unescape
from datetime import datetime, timezone, timedelta
from typing import Callable, TypedDict

# Load .env
try:
    from dotenv import load_dotenv
    _backend = os.path.dirname(os.path.abspath(__file__))
    load_dotenv(os.path.join(_backend, ".env"))
    load_dotenv(os.path.join(os.path.dirname(_backend), ".env"))
except Exception:
    pass


def _log(msg: str, color: str = "white"):
    try:
        from intellistock_logger import intellistock_logger
        intellistock_logger.log(msg, color, service="SEC_EDGAR_SUPPLY_CHAIN")
    except Exception:
        print(f"[sec-edgar-supply-chain] {msg}", flush=True)


# ──────────────────────────────────────────────────────────────────────────────
# EdgeRecord: standard structure for all edge-returning functions in this module
# ──────────────────────────────────────────────────────────────────────────────
class EdgeRecord(TypedDict, total=False):
    sup: str                  # Supplier ticker (the entity flowing goods/services outward)
    cust: str                 # Customer / destination ticker
    confidence: float         # 0.0–1.0 edge confidence
    source: str               # Human-readable source label ("10-K", "8-K", etc.)
    revenue_pct: float | None # Revenue concentration percentage when disclosed
    edge_type: str            # "SUPPLIER_OF", "STRATEGIC_PARTNER", "BOARD_OVERLAP", etc.
    last_confirmed: str       # ISO date of the filing that confirmed this edge
    active_after: str         # ISO date when the source first made this edge usable for backtests


# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
SEC_EDGAR_COMPANY_NAME = os.environ.get("SEC_EDGAR_COMPANY_NAME", "IntelliStock").strip()
SEC_EDGAR_EMAIL        = os.environ.get("SEC_EDGAR_EMAIL", "contact@intellistock.local").strip()
SEC_EDGAR_RATE_LIMIT_DELAY      = float(os.environ.get("SEC_EDGAR_RATE_LIMIT_DELAY", "0.2"))
SEC_EDGAR_MAX_COMPANIES         = int(os.environ.get("SEC_EDGAR_SUPPLY_CHAIN_MAX_COMPANIES", "8000"))
SEC_EDGAR_DOWNLOAD_TIMEOUT_SEC  = int(os.environ.get("SEC_EDGAR_DOWNLOAD_TIMEOUT_SEC", "60"))
# Parallel workers: each worker downloads one 10-K then parses it. On multi-core machines,
# 8–16 workers often improve throughput; default to available CPU headroom while keeping a sane cap.
_DEFAULT_SEC_EDGAR_PARALLEL_WORKERS = min(16, max(8, int(os.cpu_count() or 8)))
SEC_EDGAR_PARALLEL_WORKERS      = int(os.environ.get("SEC_EDGAR_PARALLEL_WORKERS", str(_DEFAULT_SEC_EDGAR_PARALLEL_WORKERS)))
# Cap bytes read per 10-K to speed up parsing. Supply-chain disclosures appear in Item 1 (Business),
# Item 7 (MD&A), and Notes to Financials (Item 8); 5MB usually covers these; increase if you see missed relationships.
SEC_EDGAR_MAX_PARSE_BYTES       = int(os.environ.get("SEC_EDGAR_MAX_PARSE_BYTES", "15000000"))
# Max phrase-occurrence windows to process per 10-K (avoids runaway spaCy/resolve on huge filings)
SEC_EDGAR_MAX_PHRASE_WINDOWS    = int(os.environ.get("SEC_EDGAR_MAX_PHRASE_WINDOWS", "80"))
SEC_EDGAR_PROGRESS_SAVE_EVERY   = int(os.environ.get("SEC_EDGAR_PROGRESS_SAVE_EVERY", "10"))
SEC_EDGAR_SIGNAL_SEGMENT_BEFORE = int(os.environ.get("SEC_EDGAR_SIGNAL_SEGMENT_BEFORE", "1800"))
SEC_EDGAR_SIGNAL_SEGMENT_AFTER  = int(os.environ.get("SEC_EDGAR_SIGNAL_SEGMENT_AFTER", "20000"))
# Use spaCy only as a fallback when regex extraction finds no company names in a context window.
SEC_EDGAR_CONTEXT_SPACY_FALLBACK = os.environ.get(
    "SEC_EDGAR_CONTEXT_SPACY_FALLBACK", "false"
).strip().lower() in ("1", "true", "yes")
SEC_EDGAR_ALLOW_LIVE_FILING_DATE_LOOKUP = os.environ.get(
    "SEC_EDGAR_ALLOW_LIVE_FILING_DATE_LOOKUP", "false"
).strip().lower() in ("1", "true", "yes")
# Minimum confidence to emit an EdgeRecord from this module (avoids pumping noise into Neo4j)
SEC_EDGAR_MIN_CONFIDENCE        = float(os.environ.get("GRAPH_NEXUS_SUPPLY_CHAIN_MIN_CONFIDENCE", "0.85"))
SEC_EDGAR_PARSER_CACHE_VERSION  = int(os.environ.get("SEC_EDGAR_PARSER_CACHE_VERSION", "5"))
SEC_COMPANY_TICKERS_CACHE_MAX_AGE_SEC = int(os.environ.get("SEC_COMPANY_TICKERS_CACHE_MAX_AGE_SEC", "604800"))
SEC_EDGAR_ARCHIVES_MAX_RETRIES = max(1, int(os.environ.get("SEC_EDGAR_ARCHIVES_MAX_RETRIES", "4")))
SEC_EDGAR_ARCHIVES_429_BACKOFF_BASE_SEC = float(os.environ.get("SEC_EDGAR_ARCHIVES_429_BACKOFF_BASE_SEC", "15"))
SEC_EDGAR_GLOBAL_MIN_INTERVAL_SEC = float(
    os.environ.get("SEC_EDGAR_GLOBAL_MIN_INTERVAL_SEC", str(max(0.11, SEC_EDGAR_RATE_LIMIT_DELAY)))
)
SEC_EDGAR_GLOBAL_COOLDOWN_MAX_SEC = float(os.environ.get("SEC_EDGAR_GLOBAL_COOLDOWN_MAX_SEC", "180"))
SEC_EDGAR_CACHE_DIR  = os.environ.get("GRAPH_NEXUS_CACHE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache"))
SEC_EDGAR_FILINGS_DIR = os.path.join(SEC_EDGAR_CACHE_DIR, "sec_edgar_filings")

# Progress persistence (resume on restart)
PROGRESS_DB     = os.environ.get("RETHINKDB_DB", "IntelliStock")
PROGRESS_TABLE  = "GraphNexusProgress"
PROGRESS_ID_SCRAPER = "sec_edgar_scraper"

# Optional NLP libs — imported lazily so the module still works without them
_SPACY_NLP  = None   # loaded on first use
_SPACY_LOCK = threading.Lock()  # spaCy pipeline is not thread-safe; serialize nlp() calls
_SPACY_WARN = False  # warn once
_FUZZY_WARN = False  # warn once if rapidfuzz unavailable
_SEC_REQUEST_STATE_LOCK = threading.Lock()
_SEC_GLOBAL_COOLDOWN_UNTIL = 0.0
_SEC_CONSECUTIVE_429S = 0
_SEC_LAST_REQUEST_AT = 0.0


def _normalize_iso_date_str(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) >= 10:
        raw = raw[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except Exception:
        return ""


def _get_sec_request_headers() -> dict[str, str]:
    return {"User-Agent": f"{SEC_EDGAR_COMPANY_NAME} {SEC_EDGAR_EMAIL}"}


def _respect_sec_global_cooldown() -> None:
    global _SEC_LAST_REQUEST_AT

    log_wait = None
    wait = 0.0
    with _SEC_REQUEST_STATE_LOCK:
        now = time.time()
        target = max(
            _SEC_GLOBAL_COOLDOWN_UNTIL,
            _SEC_LAST_REQUEST_AT + max(0.0, SEC_EDGAR_GLOBAL_MIN_INTERVAL_SEC),
        )
        wait = max(0.0, target - now)
        _SEC_LAST_REQUEST_AT = max(now, target)
        if wait >= 1.0:
            log_wait = wait
    if log_wait is not None:
        _log(f"SEC cooldown active; sleeping {log_wait:.0f}s before next request...", "yellow")
    if wait > 0:
        time.sleep(wait)


def _record_sec_rate_limit(wait_seconds: float) -> float:
    global _SEC_GLOBAL_COOLDOWN_UNTIL, _SEC_CONSECUTIVE_429S

    with _SEC_REQUEST_STATE_LOCK:
        _SEC_CONSECUTIVE_429S += 1
        scaled_wait = max(
            float(wait_seconds or 0.0),
            SEC_EDGAR_ARCHIVES_429_BACKOFF_BASE_SEC * min(_SEC_CONSECUTIVE_429S, 4),
        )
        scaled_wait = min(SEC_EDGAR_GLOBAL_COOLDOWN_MAX_SEC, scaled_wait)
        _SEC_GLOBAL_COOLDOWN_UNTIL = max(_SEC_GLOBAL_COOLDOWN_UNTIL, time.time() + scaled_wait)
        return scaled_wait


def _clear_sec_rate_limit_state() -> None:
    global _SEC_CONSECUTIVE_429S, _SEC_GLOBAL_COOLDOWN_UNTIL

    with _SEC_REQUEST_STATE_LOCK:
        _SEC_CONSECUTIVE_429S = 0
        _SEC_GLOBAL_COOLDOWN_UNTIL = 0.0


def _sec_rate_limited_get(
    url: str,
    *,
    timeout: int | float,
    headers: dict[str, str] | None = None,
    max_retries: int | None = None,
    label: str = "SEC request",
    apply_base_delay: bool = True,
):
    import requests

    retries = max(1, int(max_retries or SEC_EDGAR_ARCHIVES_MAX_RETRIES))
    headers = headers or _get_sec_request_headers()
    last_error: Exception | None = None

    for attempt in range(retries):
        if apply_base_delay and SEC_EDGAR_RATE_LIMIT_DELAY > 0:
            time.sleep(SEC_EDGAR_RATE_LIMIT_DELAY)
        _respect_sec_global_cooldown()
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            if response.status_code == 429:
                retry_after_raw = (response.headers or {}).get("Retry-After")
                try:
                    retry_after = float(retry_after_raw) if retry_after_raw else 0.0
                except Exception:
                    retry_after = 0.0
                wait = _record_sec_rate_limit(
                    max(retry_after, SEC_EDGAR_ARCHIVES_429_BACKOFF_BASE_SEC * (attempt + 1))
                )
                if attempt < retries - 1:
                    _log(
                        f"{label} hit SEC 429; retrying in {wait:.0f}s "
                        f"(attempt {attempt + 2}/{retries}).",
                        "yellow",
                    )
                    time.sleep(wait)
                    continue
            if response.status_code in (500, 502, 503, 504) and attempt < retries - 1:
                wait = max(5.0, SEC_EDGAR_ARCHIVES_429_BACKOFF_BASE_SEC * (attempt + 1))
                _log(
                    f"{label} got HTTP {response.status_code}; retrying in {wait:.0f}s "
                    f"(attempt {attempt + 2}/{retries}).",
                    "yellow",
                )
                time.sleep(wait)
                continue
            response.raise_for_status()
            _clear_sec_rate_limit_state()
            return response
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            if attempt < retries - 1:
                wait = max(5.0, SEC_EDGAR_RATE_LIMIT_DELAY * 10, 5.0 * (attempt + 1))
                _log(
                    f"{label} transient error: {e}; retrying in {wait:.0f}s "
                    f"(attempt {attempt + 2}/{retries}).",
                    "yellow",
                )
                time.sleep(wait)
                continue
            break
        except requests.HTTPError as e:
            last_error = e
            status = getattr(getattr(e, "response", None), "status_code", 0)
            if status in (429, 500, 502, 503, 504) and attempt < retries - 1:
                wait = max(SEC_EDGAR_ARCHIVES_429_BACKOFF_BASE_SEC * (attempt + 1), 5.0)
                _log(
                    f"{label} HTTP {status}; retrying in {wait:.0f}s "
                    f"(attempt {attempt + 2}/{retries}).",
                    "yellow",
                )
                time.sleep(wait)
                continue
            break

    raise last_error or RuntimeError(f"{label} failed after {retries} attempts")


_NON_OPERATING_SECURITY_RE = re.compile(
    r"\b("
    r"depositary\s+shares?|american\s+depositary(?:\s+shares?|\s+receipt)?|"
    r"preferred\s+stock|preferred\s+shares?|perpetual\s+preferred|"
    r"senior\s+notes?|subordinated\s+notes?|notes?\s+due|debentures?|"
    r"junior\s+subordinated|mandatory\s+convertible|trust\s+preferred|"
    r"warrants?|rights?"
    r")\b",
    re.I,
)
_COMMON_EQUITY_TRAILING_RE = re.compile(
    r"\s+(?:class\s+[a-z]\s+)?(?:common\s+stock|common\s+shares?|ordinary\s+shares?)\b.*$",
    re.I,
)
_SECURITY_TRAILING_RE = re.compile(
    r"\s+(?:"
    r"(?:series|tranche)\s+[a-z0-9\-]+|"
    r"\d+(?:\.\d+)?%.*|"
    r"floating\s+rate.*|fixed(?:-|\s)?rate.*|"
    r"depositary\s+shares?.*|preferred\s+stock.*|preferred\s+shares?.*|"
    r"senior\s+notes?.*|subordinated\s+notes?.*|notes?\s+due.*|debentures?.*|"
    r"trust\s+preferred.*|warrants?.*|rights?.*"
    r")$",
    re.I,
)
_CORP_SUFFIX_RE = re.compile(
    r"(?:\s+|\s*[,\.]\s*)(incorporated|corporation|company|limited|"
    r"inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|l\.l\.c\.?|plc\.?|"
    r"holdings?|group|enterprises?|partners?|lp|l\.p\.|sa|ag|nv|se|oyj|oy)\s*$",
    re.I,
)
_NON_COUNTERPARTY_RE = re.compile(
    r"\b(transfer\s+agent|registrar|trust\s+company|indenture\s+trustee|paying\s+agent|"
    r"rights\s+agent|exchange\s+agent|warrant\s+agent|filing\s+agent|information\s+agent)\b",
    re.I,
)
_KNOWN_VENDOR_KEYS = {
    "donnelley financial solutions",
    "broadridge financial solutions",
    "computershare",
    "equiniti trust",
    "continental stock transfer trust",
    "american stock transfer trust",
    "wilmington trust",
    "us bank trust",
}
_REAL_ESTATE_TENANT_CONTEXT_RE = re.compile(
    r"\b("
    r"tenant|tenants|lease|leases|leased|leasing|lessee|sublease|subtenant|"
    r"annualized\s+base\s+rent|base\s+rent|rental\s+revenue|rental\s+income|"
    r"rentable\s+square\s+feet|square\s+feet|occupancy|leased\s+rate|"
    r"property|properties|office\s+portfolio|retail\s+portfolio|"
    r"multifamily|industrial\s+portfolio|same\s+store"
    r")\b",
    re.I,
)
_REVENUE_SHARE_PATTERN = re.compile(
    r"(?:account(?:ed)?\s+for|represent(?:ed)?|comprise(?:d)?|constitute(?:d)?)\s+"
    r"(?:approximately\s+|about\s+|roughly\s+)?"
    r"(\d{1,3}(?:\.\d{1,2})?)\s*%\s+of\s+"
    r"(?:our|their|its|the\s+company'?s|total|net|combined|consolidated)?\s*"
    r"(?:total\s+|net\s+)?(?:revenue|revenues|sales|net\s+sales)\b",
    re.I,
)
_PERCENT_OF_REVENUE_PATTERN = re.compile(
    r"(\d{1,3}(?:\.\d{1,2})?)\s*%\s+of\s+"
    r"(?:our|their|its|the\s+company'?s|total|net|combined|consolidated)?\s*"
    r"(?:total\s+|net\s+)?(?:revenue|revenues|sales|net\s+sales)\b",
    re.I,
)


def _is_non_operating_security_name(name: str) -> bool:
    return bool(name and _NON_OPERATING_SECURITY_RE.search(name))


def _canonical_company_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    s = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
    s = _COMMON_EQUITY_TRAILING_RE.sub("", s).strip()
    if _is_non_operating_security_name(s):
        s = _SECURITY_TRAILING_RE.sub("", s).strip()
    return re.sub(r"\s+", " ", s).strip(" ,.-") or raw


def _company_identity_key(name: str) -> str:
    s = _canonical_company_name(name).lower().strip()
    if not s:
        return ""
    s = re.sub(r"^the\s+", "", s)
    s = re.sub(r"[,\.&'/\-]+", " ", s)
    for _ in range(4):
        new = _CORP_SUFFIX_RE.sub("", s).strip()
        if new == s:
            break
        s = new
    return re.sub(r"\s+", " ", s).strip()


def _is_blocked_counterparty_name(name: str) -> bool:
    key = _company_identity_key(name)
    if not key:
        return True
    if _is_non_operating_security_name(name):
        return True
    if _NON_COUNTERPARTY_RE.search(name):
        return True
    return key in _KNOWN_VENDOR_KEYS


def _context_is_real_estate_tenant_disclosure(context: str, company_name: str | None = None) -> bool:
    text = re.sub(r"\s+", " ", context or "").strip()
    if not text:
        return False
    spans = [text]
    if company_name:
        local = _local_company_context(text, company_name, radius=180)
        if local:
            spans = [local]
    return any(_REAL_ESTATE_TENANT_CONTEXT_RE.search(span) for span in spans)


def _local_company_context(text: str, company_name: str | None, radius: int = 140) -> str:
    raw = re.sub(r"\s+", " ", text or "").strip()
    if not raw or not company_name:
        return raw
    lower_text = raw.lower()
    key = company_name.lower().strip()
    idx = lower_text.find(key)
    if idx == -1:
        stripped_key = re.sub(r"[^a-z0-9 ]+", "", key)
        stripped_text = re.sub(r"[^a-z0-9 ]+", "", lower_text)
        idx = stripped_text.find(stripped_key) if stripped_key else -1
        if idx == -1:
            return raw
        return raw
    start = max(0, idx - radius)
    end = min(len(raw), idx + len(company_name) + radius)
    return raw[start:end]


def _get_spacy_nlp():
    """Return spaCy NLP pipeline or None. Warns once if unavailable. Load is serialized for thread safety."""
    global _SPACY_NLP, _SPACY_WARN
    if _SPACY_NLP is not None:
        return _SPACY_NLP if _SPACY_NLP else None
    with _SPACY_LOCK:
        if _SPACY_NLP is not None:
            return _SPACY_NLP if _SPACY_NLP else None
        try:
            import spacy
            _SPACY_NLP = spacy.load("en_core_web_lg")
        except Exception:
            try:
                import spacy
                _SPACY_NLP = spacy.load("en_core_web_sm")
            except Exception:
                if not _SPACY_WARN:
                    _log("spaCy not installed or model missing — falling back to regex NER. "
                         "pip install spacy && python -m spacy download en_core_web_lg", "yellow")
                    _SPACY_WARN = True
                _SPACY_NLP = False  # sentinel: tried and failed
    return _SPACY_NLP if _SPACY_NLP else None


def _fuzzy_score(a: str, b: str) -> float:
    """Return 0–100 similarity using rapidfuzz if available, else 0."""
    global _FUZZY_WARN
    try:
        from rapidfuzz import fuzz
        return fuzz.token_sort_ratio(a, b)
    except ImportError:
        if not _FUZZY_WARN:
            _log("rapidfuzz not installed — fuzzy name matching disabled. pip install rapidfuzz", "yellow")
            _FUZZY_WARN = True
        return 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Progress helpers
# ──────────────────────────────────────────────────────────────────────────────
def _store_now() -> str:
    """R24 replacement for ``r.now()``: an ISO-8601 UTC string, which is what
    every reader of this document already turned the driver's datetime into
    (interactive_utils' stage-doc normaliser, the API, the Nexus card)."""
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


class _StoreHandle:
    """Truthy stand-in for the old driver connection (R26): the progress
    writers are all gated on ``if conn`` / ``if conn is None``."""

    def close(self, *_a, **_k):
        return None


def _get_rethink_conn():
    try:
        from db import pool as db_pool
        if not db_pool.health().get("ok"):
            raise RuntimeError("Postgres pool is not answering")
        return _StoreHandle()
    except Exception as e:
        try:
            from db import pool as db_pool
            db_pool.close_pool()
        except Exception:
            pass
        _log(f"Postgres progress unavailable: {e}", "yellow")
        return None


def _ensure_progress_table(conn):
    try:
        from db import schema as db_schema
        db_schema.ensure_table(PROGRESS_TABLE)               # R25
    except Exception as e:
        _log(f"Ensure progress table: {e}", "yellow")


def _load_scraper_progress(conn) -> dict | None:
    try:
        from db import store
        doc = store.get(PROGRESS_TABLE, PROGRESS_ID_SCRAPER)
        return doc if doc else None
    except Exception:
        return None


def _save_scraper_progress(conn, last_ticker_index: int, total_tickers: int,
                            edges_count: int, progress_pct: float, message: str, status: str):
    try:
        from db import store
        doc = {
            "id": PROGRESS_ID_SCRAPER,
            "last_ticker_index": last_ticker_index,
            "total_tickers": total_tickers,
            "edges_count": edges_count,
            "progress_pct": progress_pct,
            "message": message,
            "last_updated": _store_now(),
            "status": status,
        }
        store.insert(PROGRESS_TABLE, doc, conflict="replace")
    except Exception as e:
        _log(f"Save scraper progress: {e}", "yellow")


# ──────────────────────────────────────────────────────────────────────────────
# SEC ticker ↔ name mapping
# ──────────────────────────────────────────────────────────────────────────────
def _sec_company_ticker_cache_paths() -> list[str]:
    return [
        os.path.join(SEC_EDGAR_CACHE_DIR, "phase2", "sec_company_ticker_rows.json"),
        os.path.join(SEC_EDGAR_CACHE_DIR, "sec_company_ticker_rows.json"),
    ]


def _load_cached_sec_company_ticker_rows(*, allow_stale: bool = False) -> dict[str, dict[str, str]]:
    import json

    for path in _sec_company_ticker_cache_paths():
        if not os.path.isfile(path):
            continue
        try:
            if not allow_stale:
                age = time.time() - os.path.getmtime(path)
                if age > SEC_COMPANY_TICKERS_CACHE_MAX_AGE_SEC:
                    continue
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        rows: dict[str, dict[str, str]] = {}
        for ticker, entry in payload.items():
            if not ticker or not isinstance(entry, dict):
                continue
            cik = entry.get("cik")
            title = entry.get("title")
            if not cik or not title:
                continue
            rows[str(ticker).strip().upper()] = {
                "cik": str(cik).zfill(10),
                "title": str(title).strip(),
            }
        if rows:
            return rows
    return {}


def _save_sec_company_ticker_rows(rows: dict[str, dict[str, str]]) -> None:
    import json

    if not rows:
        return
    payload = {
        str(ticker).strip().upper(): {
            "cik": str((entry or {}).get("cik") or "").zfill(10),
            "title": str((entry or {}).get("title") or "").strip(),
        }
        for ticker, entry in rows.items()
        if ticker and isinstance(entry, dict) and entry.get("cik") and entry.get("title")
    }
    if not payload:
        return
    for path in _sec_company_ticker_cache_paths():
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception:
            pass


def _build_sec_company_ticker_maps(rows: dict[str, dict[str, str]]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    ticker_to_title: dict[str, str] = {}
    title_to_ticker: dict[str, str] = {}
    cik_to_ticker:   dict[str, str] = {}
    best_by_issuer: dict[str, dict] = {}
    entries = list(rows.items())
    for ticker, entry in entries:
        title = (entry.get("title") or "").strip()
        cik = entry.get("cik")
        if not ticker or not title or not cik or _is_non_operating_security_name(title):
            continue
        issuer_key = _company_identity_key(title)
        if not issuer_key:
            continue
        candidate = {
            "ticker": str(ticker).strip().upper(),
            "title": title,
            "canonical_name": _canonical_company_name(title),
            "cik": str(cik).zfill(10),
        }
        prev = best_by_issuer.get(issuer_key)
        if prev is None or (
            (1 if candidate["cik"] else 0, -len(candidate["title"]), candidate["ticker"]) >
            (1 if prev.get("cik") else 0, -len(prev.get("title", "")), prev.get("ticker", ""))
        ):
            best_by_issuer[issuer_key] = candidate

    for rec in best_by_issuer.values():
        ticker = rec["ticker"]
        title = rec["title"]
        ticker_to_title[ticker] = title
        if rec.get("cik"):
            cik_to_ticker[rec["cik"]] = ticker
        title_to_ticker[title.lower()] = ticker
        norm = _company_identity_key(title)
        if norm:
            title_to_ticker[norm] = ticker
        canonical_name = (rec.get("canonical_name") or "").lower()
        if canonical_name:
            title_to_ticker[canonical_name] = ticker

    return ticker_to_title, title_to_ticker, cik_to_ticker


def _fetch_sec_company_tickers() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Fetch SEC company_tickers.json → (ticker->title, title_normalized->ticker, cik->ticker)."""
    cached_rows = _load_cached_sec_company_ticker_rows()
    if cached_rows:
        _log(f"Using cached SEC company_tickers.json ({len(cached_rows)} rows).", "white")
        ticker_to_title, title_to_ticker, cik_to_ticker = _build_sec_company_ticker_maps(cached_rows)
        _log(f"Loaded {len(ticker_to_title)} tickers for name resolution.", "cyan")
        return ticker_to_title, title_to_ticker, cik_to_ticker

    _log("Fetching SEC company_tickers.json...", "white")
    last_error: Exception | None = None
    data = None
    for attempt in range(3):
        try:
            r = _sec_rate_limited_get(
                "https://www.sec.gov/files/company_tickers.json",
                timeout=30,
                headers=_get_sec_request_headers(),
                max_retries=1,
                label="SEC company_tickers.json",
            )
            _log("Downloaded, parsing company list...", "white")
            data = r.json()
            break
        except Exception as e:
            last_error = e
            if attempt < 2:
                time.sleep(5 * (attempt + 1))

    if not isinstance(data, dict):
        stale_rows = _load_cached_sec_company_ticker_rows(allow_stale=True)
        if stale_rows:
            _log(
                f"SEC company_tickers live fetch failed ({last_error}); using stale cached rows instead.",
                "yellow",
            )
            ticker_to_title, title_to_ticker, cik_to_ticker = _build_sec_company_ticker_maps(stale_rows)
            _log(f"Loaded {len(ticker_to_title)} tickers for name resolution.", "cyan")
            return ticker_to_title, title_to_ticker, cik_to_ticker
        _log(f"SEC company_tickers error: {last_error}", "red")
        return {}, {}, {}

    live_rows: dict[str, dict[str, str]] = {}
    entries = list((data.values() if isinstance(data, dict) else []))
    n_entries = len(entries)
    for idx, entry in enumerate(entries):
        if n_entries > 5000 and (idx + 1) % 5000 == 0:
            _log(f"  Parsing tickers {idx + 1}/{n_entries}...", "white")
        ticker = (entry.get("ticker") or "").strip().upper()
        title = (entry.get("title") or "").strip()
        cik = entry.get("cik_str") or entry.get("cik")
        if not ticker or not title or cik is None or _is_non_operating_security_name(title):
            continue
        live_rows[ticker] = {"cik": str(cik).zfill(10), "title": title}

    _save_sec_company_ticker_rows(live_rows)
    ticker_to_title, title_to_ticker, cik_to_ticker = _build_sec_company_ticker_maps(live_rows)
    _log(f"Loaded {len(ticker_to_title)} tickers for name resolution.", "cyan")
    return ticker_to_title, title_to_ticker, cik_to_ticker


# ──────────────────────────────────────────────────────────────────────────────
# Name normalisation & resolution
# ──────────────────────────────────────────────────────────────────────────────
def _normalize_company_name(name: str) -> str:
    if not name or len(name) < 2:
        return ""
    return _company_identity_key(name)


_LOW_SIGNAL_COMPANY_TOKENS = frozenset({
    "company", "companies", "inc", "corp", "corporation", "co", "llc", "ltd",
    "limited", "holdings", "holding", "group", "partners", "partner", "resources",
    "ventures", "industries", "industry", "technologies", "technology", "systems",
    "services", "solutions", "consumer", "products", "commerce", "therapeutics",
    "therapies", "bio", "digital",
})


def _counterparty_signal_tokens(name: str) -> list[str]:
    norm = _normalize_company_name(name)
    if not norm:
        return []
    return [
        tok for tok in re.findall(r"\b[a-z0-9]{3,}\b", norm)
        if tok not in _LOW_SIGNAL_COMPANY_TOKENS
    ]


def _context_has_explicit_counterparty_anchor(ctx: str, company_name: str) -> bool:
    """
    Require a direct textual tie between the signal phrase and the resolved counterparty
    for low-confidence partial-name matches.
    """
    text = re.sub(r"\s+", " ", ctx or "").strip()
    cleaned_name = re.sub(r"\s+", " ", (company_name or "").strip())
    if not text or not cleaned_name:
        return False

    local = _local_company_context(text, cleaned_name, radius=140) or text
    escaped = re.escape(cleaned_name)
    patterns = (
        rf"(?:sales to|revenue from|purchases from|purchased from|procurement from|agreement with)\s+{escaped}\b",
        rf"(?:license agreement with|supply agreement with|purchase agreement with|manufacturing agreement with|distribution agreement with|framework agreement with|strategic partnership with|partnership with|collaboration with|joint venture with|joint development with)\s+{escaped}\b",
        rf"(?:license agreement|supply agreement|purchase agreement|manufacturing agreement|distribution agreement|framework agreement|collaboration agreement|strategic partnership|partnership|joint venture|joint development)[^.]{{0,200}}\bwith\s+{escaped}\b",
        rf"(?:joint venture|strategic partnership|collaboration agreement)[^.]{{0,200}}\bbetween\b[^.]{{0,200}}\b{escaped}\b",
        rf"(?:joint venture)[^.]{{0,200}}\b(?:includes|including|member|members)\b[^.]{{0,200}}\b{escaped}\b",
        rf"(?:together with|along with)[\s\S]{{0,120}}\b{escaped}\b[\s\S]{{0,200}}\bformed\s+(?:a\s+)?joint venture\b",
        rf"(?:customer|customers|supplier|suppliers)\b[^.]{{0,80}}\b{escaped}\b",
        rf"\b{escaped}\b[^.]{{0,40}}\b(?:accounted for|represented|compris(?:ed|es)|constitut(?:ed|es))\b",
        rf"\bwe\b[^.]{{0,80}}\bcustomer of\s+{escaped}\b",
        rf"\b(?:with|from|to)\s+{escaped}\b[^.]{{0,200}}\b(?:supply agreement|purchase agreement|distribution agreement|manufacturing agreement|license agreement|collaboration agreement|strategic partnership|partnership|collaboration|joint venture|joint development|framework agreement)\b",
    )
    return any(re.search(pattern, local, re.I) for pattern in patterns)


def _resolve_customer_to_ticker(
    customer_name: str,
    title_to_ticker: dict[str, str],
    min_fuzzy_score: float = 88.0,
) -> tuple[str | None, float]:
    """
    Resolve a company name to a ticker.
    Returns (ticker | None, confidence 0-1).

    Resolution order:
    1. Direct / normalised exact match → confidence 1.0
    2. Tightened substring containment  → confidence 0.85
       - Both strings must be ≥ 10 chars
       - Shorter must be ≥ 65% the length of the longer (prevents short words
         matching long company names, e.g. "ford" matching "Stanford")
       - Returns BEST match (longest ticker name that contains the query), not first
    3. Word-overlap ≥ 2 matching words AND ≥ 60% of name words → confidence 0.75
       - Filters out common non-discriminating words (american, national, etc.)
       - Both names must have ≥ 3 significant words to avoid 1-word matches
    4. rapidfuzz token_sort_ratio ≥ min_fuzzy_score → confidence scaled 0.60–0.80
    """
    if not customer_name or len(customer_name) < 4:
        return None, 0.0
    if _is_blocked_counterparty_name(customer_name):
        return None, 0.0

    c_lower = customer_name.strip().lower()
    norm    = _normalize_company_name(customer_name)
    allow_fuzzy = os.environ.get("SEC_EDGAR_ENABLE_FUZZY_RESOLUTION", "false").strip().lower() in ("1", "true", "yes")

    if not norm or len(norm) < 4:
        return None, 0.0

    # 1. Exact / normalised exact
    if c_lower in title_to_ticker:
        return title_to_ticker[c_lower], 1.0
    if norm in title_to_ticker:
        return title_to_ticker[norm], 1.0

    # 2. Substring containment — tightened
    # Both strings ≥ 10 chars; shorter ≥ 65% of longer to prevent
    # short common words matching long company names.
    if len(norm) >= 10:
        best_match_ticker: str | None = None
        best_match_len: int = 0
        for title_norm, ticker in title_to_ticker.items():
            if len(title_norm) < 10:
                continue
            shorter = norm if len(norm) <= len(title_norm) else title_norm
            longer  = title_norm if len(norm) <= len(title_norm) else norm
            # Shorter string must be a substantial portion of longer string
            if len(shorter) < 0.65 * len(longer):
                continue
            if title_norm in norm or norm in title_norm:
                # Prefer the most specific (longest ticker name) match
                if len(title_norm) > best_match_len:
                    best_match_len = len(title_norm)
                    best_match_ticker = ticker
        if best_match_ticker:
            return best_match_ticker, 0.85

    # 3. Word-overlap — require ≥ 2 matching significant words AND ≥ 60% coverage
    # Filter out very common words that aren't discriminating.
    _COMMON_NONDISCRIMINATING = frozenset({
        "american", "national", "global", "international", "united", "general",
        "first", "allied", "standard", "western", "eastern", "northern", "southern",
        "new", "old", "great", "pacific", "atlantic", "continental", "federal",
        "state", "capital", "central", "community", "premier", "advanced", "strategic",
        "digital", "tech", "technologies", "systems", "services", "solutions",
        "group", "holdings", "partners", "associates", "resources", "ventures",
    })

    def _sig_words(s: str) -> set[str]:
        return {
            w for w in re.findall(r'\b[a-z]{3,}\b', s)
            if w not in _COMMON_NONDISCRIMINATING
        }

    name_sig  = _sig_words(norm)
    # Need ≥ 2 significant (non-generic) words to even attempt word-overlap
    if len(name_sig) >= 2:
        best_ticker, best_score = None, 0.0
        for title_norm, ticker in title_to_ticker.items():
            title_sig = _sig_words(title_norm)
            if len(title_sig) < 2:
                continue
            overlap = name_sig & title_sig
            if len(overlap) < 2:  # require at least 2 matching significant words
                continue
            score = len(overlap) / min(len(name_sig), len(title_sig))
            if score >= 0.60 and score > best_score:
                best_score = score
                best_ticker = ticker
        if best_ticker:
            return best_ticker, 0.75

    # 4. rapidfuzz — only attempt for names ≥ 8 chars
    if allow_fuzzy and len(norm) >= 8:
        best_ticker, best_fscore = None, 0.0
        sample = list(title_to_ticker.items())
        if len(sample) > 5000:
            # Pre-filter: first char must match
            sample = [(t, v) for t, v in sample if t and t[0] == norm[0]]
        for title_norm, ticker in sample:
            if len(title_norm) < 8:
                continue
            fs = _fuzzy_score(norm, title_norm)
            if fs >= min_fuzzy_score and fs > best_fscore:
                best_fscore = fs
                best_ticker = ticker
        if best_ticker:
            conf = 0.60 + 0.20 * (best_fscore - min_fuzzy_score) / (100.0 - min_fuzzy_score)
            return best_ticker, round(min(conf, 0.80), 3)

    return None, 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Revenue-percentage extraction helper
# ──────────────────────────────────────────────────────────────────────────────
_PCT_PATTERN = re.compile(
    r'(\d{1,3}(?:\.\d{1,2})?)\s*%',
    re.I
)


def _extract_revenue_pct(context: str, company_name: str | None = None) -> float | None:
    """
    Try to extract a revenue/sales concentration percentage from a disclosure context window.
    Returns the first plausible percentage (5–80 %) that is explicitly tied to revenue/sales.
    """
    text = re.sub(r"\s+", " ", context or "").strip()
    if not text:
        return None
    spans = [text]
    if company_name:
        spans = [_local_company_context(text, company_name, radius=160)]
    for span in spans:
        if _context_is_real_estate_tenant_disclosure(span, company_name=company_name):
            continue
        for pattern in (_REVENUE_SHARE_PATTERN, _PERCENT_OF_REVENUE_PATTERN):
            for m in pattern.finditer(span):
                val = float(m.group(1))
                if 5.0 <= val <= 80.0:
                    return round(val, 1)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Filer-as-customer direction detection
# ──────────────────────────────────────────────────────────────────────────────
# Some customer-phrase contexts describe the FILER as a customer of the named
# company rather than the named company being a customer of the filer. Examples:
#   "We are a significant customer of Allison Transmission"
#   "We represent approximately 55% of their revenues"
#   "We accounted for approximately 30% of Supplier Corp's net sales"
# In these cases the direction must be reversed: named company = supplier,
# filer = customer.
_FILER_AS_CUSTOMER_RE = re.compile(
    r'\bwe\s+(?:are|were|have\s+been|represent(?:ed)?|account(?:ed)?\s+for|comprise(?:d)?)\s+'
    r'(?:a\s+|one\s+of\s+(?:the\s+)?)?'
    r'(?:their\s+(?:largest|significant|major|primary|key|principal)\s+|'
    r'(?:a\s+)?(?:significant|major|key|primary|principal|large|substantial)\s+)'
    r'(?:customer|purchaser|buyer)',
    re.I,
)
# Catches "X% of their/its revenue/revenues/net revenues/net sales/sales"
# or "X% of [Company Name]'s revenue" — the percentage belongs to named entity.
_NAMED_REVENUE_RE = re.compile(
    r'(?:their|its|(?:[A-Z][a-z]+\s+){1,4}(?:Inc|Corp|LLC|Ltd|Co)?\'s)\s+'
    r'(?:total\s+)?(?:net\s+)?(?:revenue|revenues|sales|net\s+sales)',
    re.I,
)
# "we represent/accounted for approximately X% of [their/its/Company's] ..."
_FILER_REPRESENTS_PCT_RE = re.compile(
    r'\bwe\s+(?:represent(?:ed)?|account(?:ed)?\s+for)\s+approximately\s+\d',
    re.I,
)


def _context_filer_is_customer(ctx: str, rev_pct: float | None) -> bool:
    """
    Return True when the context window indicates the FILER is the customer
    (not the supplier) of the named company.

    Heuristics (any one is sufficient):
    1. Explicit "we are a significant/major/key customer of …" language.
    2. "we represent approximately X% of their revenue" language.
    3. High revenue_pct (> 15 %) combined with possessive language pointing
       to the named company's revenue ("their revenues", "its net sales",
       "CompanyName's revenues") — the filer is describing its share of the
       supplier's book, not the other way around.
    """
    if _FILER_AS_CUSTOMER_RE.search(ctx):
        return True
    if _FILER_REPRESENTS_PCT_RE.search(ctx):
        return True
    if rev_pct is not None and rev_pct > 15.0 and _NAMED_REVENUE_RE.search(ctx):
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Phrase lists (customer / supplier signals)
# ──────────────────────────────────────────────────────────────────────────────
_CUSTOMER_PHRASES = [
    "significant customer", "major customer", "customer concentration",
    "one customer accounted for", "single customer accounted for",
    "customer accounted for approximately", "customers accounted for",
    "10% of revenue", "10% of net sales", "10% of total revenue",
    "10% of sales", "greater than 10%", "revenue from one customer",
    "sales to one customer", "sales to a single customer",
    "largest customer", "top customer", "principal customer",
    "key customer", "primary customer", "customer represents",
    "customer comprises", "customer constituted",
]
_SUPPLIER_PHRASES = [
    # Explicit concentration disclosures — high precision
    "significant supplier", "major supplier", "supplier concentration",
    "supplier accounted for", "suppliers accounted for",
    "key supplier", "principal supplier", "primary supplier",
    "supplier represents", "supplier comprises", "supplier constituted",
    # Single/sole source — high precision (almost always a named entity follows)
    "single source supplier", "sole source supplier",
    "sole-source supplier", "single-source supplier",
    "our sole supplier", "our only supplier",
    # Explicit procurement disclosures — scoped narrowly
    "procurement from a single", "purchased exclusively from",
    # NOTE: "rely on", "dependent on", "purchases from", "purchased from",
    # "procurement from", "sole source", "single source" (without "supplier")
    # have been REMOVED. Those phrases appear in virtually every 10-K in
    # generic risk-factor language ("we rely on third-party manufacturers",
    # "assets purchased from subsidiaries") and caused massive false-positive
    # extraction of unrelated company names.
]
_AGREEMENT_PHRASES = [
    "supply agreement", "supply contract", "purchase agreement",
    "manufacturing agreement", "license agreement", "collaboration agreement", "strategic partnership",
    "joint venture", "joint development", "distribution agreement",
    "master supply", "framework agreement",
]
_CUSTOMER_PHRASE_SET = frozenset(_CUSTOMER_PHRASES)
_SUPPLIER_PHRASE_SET = frozenset(_SUPPLIER_PHRASES)
_AGREEMENT_PHRASE_SET = frozenset(_AGREEMENT_PHRASES)
_ALL_DISCLOSURE_PHRASES = tuple(
    sorted(_CUSTOMER_PHRASES + _SUPPLIER_PHRASES + _AGREEMENT_PHRASES, key=len, reverse=True)
)
_DISCLOSURE_SIGNAL_RE = re.compile(
    "|".join(re.escape(p) for p in _ALL_DISCLOSURE_PHRASES),
    re.I,
)


# ──────────────────────────────────────────────────────────────────────────────
# Entity extraction (spaCy NER + regex fallback)
# ──────────────────────────────────────────────────────────────────────────────
_SKIP_WORDS = frozenset({
    "the", "and", "for", "are", "was", "were", "this", "that",
    "with", "from", "accounted", "represented", "customer",
    "supplier", "company", "entity", "party", "none", "revenue",
    "sales", "income", "agreement",
})
_GENERIC_TERMS = re.compile(
    r"^(customer|supplier|company|entity|party|vendor|client|n/a|none)\s*[a-z1-9]?$",
    re.I,
)


def _has_any_disclosure_signal(text: str) -> bool:
    return bool(text and _DISCLOSURE_SIGNAL_RE.search(text))


def _html_to_plain_text_fast(html_text: str) -> str:
    """Cheap HTML-to-text conversion for signal-centered parsing."""
    if not html_text:
        return ""
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html_text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _extract_signal_centered_plain_text(html_text: str) -> str:
    """
    Extract only the HTML regions around disclosure phrases, then strip tags.
    This avoids building full-document DOM trees for filings where only a few
    phrase-centered windows are relevant.
    """
    if not html_text:
        return ""
    html_lower = html_text.lower()
    spans: list[tuple[int, int]] = []
    for match in _DISCLOSURE_SIGNAL_RE.finditer(html_lower):
        start = max(0, match.start() - SEC_EDGAR_SIGNAL_SEGMENT_BEFORE)
        end = min(len(html_text), match.end() + SEC_EDGAR_SIGNAL_SEGMENT_AFTER)
        spans.append((start, end))
        if len(spans) >= SEC_EDGAR_MAX_PHRASE_WINDOWS:
            break
    if not spans:
        return _html_to_plain_text_fast(html_text)
    spans.sort()
    merged: list[list[int]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    parts = [
        _html_to_plain_text_fast(html_text[start:end])
        for start, end in merged
    ]
    return " ".join(part for part in parts if part).strip()


def _focus_phrase_context(ctx: str, phrase: str, before: int = 180, after: int = 260) -> str:
    """Trim a phrase window to the nearest local span so unrelated later names do not leak in."""
    text = re.sub(r"\s+", " ", ctx or "").strip()
    marker = (phrase or "").strip().lower()
    if not text or not marker:
        return text
    idx = text.lower().find(marker)
    if idx == -1:
        return text
    start = max(0, idx - before)
    end = min(len(text), idx + len(marker) + after)
    return text[start:end]


def _extract_ctx_candidate_names(ctx: str) -> list[str]:
    """Extract likely company names from a small phrase-centered context window."""
    if not ctx:
        return []

    names: list[str] = []
    seen: set[str] = set()

    def _push(name: str) -> None:
        cleaned = re.sub(r"\s+", " ", (name or "").strip()).strip(" ,.;:")
        if not cleaned:
            return
        key = cleaned.lower()
        if key in seen or len(cleaned) < 5:
            return
        if _GENERIC_TERMS.match(cleaned):
            return
        words = cleaned.split()
        has_suffix = bool(re.search(
            r"\b(Inc|Corp|LLC|Ltd|Co\.?|Company|Incorporated|Corporation|Technologies|Systems|Group|Holdings|Partners)\b",
            cleaned,
            re.I,
        ))
        if len(words) < 2 and not has_suffix:
            return
        first_word = words[0].lower()
        if first_word in _SKIP_WORDS:
            return
        seen.add(key)
        names.append(cleaned)

    for m in re.finditer(r'["\']([^"\']{3,80})["\']', ctx):
        quoted = m.group(1).strip()
        if re.search(r"customer\s+[a-z1-9]|supplier\s+[a-z1-9]", quoted, re.I):
            continue
        _push(quoted)

    for m in re.finditer(
        r"(?:sales to|revenue from|purchases from|purchased from|"
        r"procurement from|customer|supplier|agreement with)\s+"
        r"([A-Z][a-zA-Z\s,&.\-]{2,80}?)"
        r"(?:\s+(?:accounted|represented|comprised|constituted|represents|under)|\.|,|\d|$)",
        ctx,
    ):
        _push(m.group(1))

    for m in re.finditer(
        r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4}"
        r"(?:\s+(?:Inc|Corp|LLC|Ltd|Co|Company|Incorporated|Corporation|Technologies|Systems|Group|Holdings|Partners))?)\b",
        ctx,
    ):
        _push(m.group(1))

    if names or not SEC_EDGAR_CONTEXT_SPACY_FALLBACK:
        return names

    nlp = _get_spacy_nlp()
    if not nlp:
        return names
    try:
        with _SPACY_LOCK:
            doc = nlp(ctx[:3_000])
        for ent in doc.ents:
            if ent.label_ == "ORG":
                _push(ent.text)
    except Exception:
        pass
    return names


_8K_CONTEXT_ANCHOR_PHRASES = (
    "item 1.01",
    "material definitive agreement",
    "entered into",
    "agreement with",
    "agreement by and between",
    "collaboration agreement",
    "commercial agreement",
    "distribution agreement",
    "joint venture",
    "license agreement",
    "manufacturing agreement",
    "master services agreement",
    "partnership agreement",
    "purchase agreement",
    "reseller agreement",
    "services agreement",
    "supply agreement",
)


def _extract_8k_agreement_contexts(text: str) -> list[str]:
    plain_text = _extract_signal_centered_plain_text(text) or _html_to_plain_text_fast(text)
    text_lower = plain_text.lower()
    if "1.01" not in text_lower and "material definitive agreement" not in text_lower:
        return []
    contexts: list[str] = []
    seen: set[str] = set()
    for phrase in _8K_CONTEXT_ANCHOR_PHRASES:
        start = 0
        while True:
            idx = text_lower.find(phrase, start)
            if idx == -1:
                break
            raw_ctx = plain_text[max(0, idx - 260):min(len(plain_text), idx + len(phrase) + 520)]
            focus_ctx = _focus_phrase_context(raw_ctx, phrase, before=220, after=360) or raw_ctx
            key = focus_ctx.lower().strip()
            if key and key not in seen:
                seen.add(key)
                contexts.append(focus_ctx)
            start = idx + len(phrase)
            if len(contexts) >= SEC_EDGAR_MAX_PHRASE_WINDOWS:
                return contexts
    return contexts


def _context_has_8k_counterparty_anchor(ctx: str, company_name: str) -> bool:
    text = re.sub(r"\s+", " ", ctx or "").strip()
    cleaned_name = re.sub(r"\s+", " ", (company_name or "").strip())
    if not text or not cleaned_name:
        return False
    local = _local_company_context(text, cleaned_name, radius=180) or text
    escaped = re.escape(cleaned_name)
    patterns = (
        rf"(?:entered into|entry into|execution of)\s+(?:a\s+|an\s+)?(?:material\s+definitive\s+)?"
        rf"(?:agreement|arrangement|amendment)[^.]{{0,120}}\bwith\s+{escaped}\b",
        rf"(?:license|supply|distribution|manufacturing|services|master services|commercial|collaboration|purchase|reseller|framework|strategic partnership|partnership|joint venture)"
        rf"[^.]{{0,160}}\bwith\s+{escaped}\b",
        rf"(?:agreement|arrangement|transaction|joint venture)[^.]{{0,160}}\bbetween\b[^.]{{0,180}}\b{escaped}\b",
        rf"\b{escaped}\b[^.]{{0,180}}\b(?:entered into|executed|signed)\b[^.]{{0,120}}\b(?:agreement|arrangement|joint venture)\b",
        rf"\bparty\b[^.]{{0,80}}\b{escaped}\b",
    )
    return any(re.search(pattern, local, re.I) for pattern in patterns) or _context_has_explicit_counterparty_anchor(local, cleaned_name)


def _resolved_8k_counterparty_supported(
    ctx: str,
    extracted_name: str,
    resolved_ticker: str,
    ticker_to_title: dict[str, str] | None,
) -> bool:
    def _loosely_match(left: str, right: str) -> bool:
        left_key = _company_identity_key(left)
        right_key = _company_identity_key(right)
        if left_key and right_key and left_key == right_key:
            return True
        return _normalize_company_name(left) == _normalize_company_name(right)

    extracted_name = re.sub(r"\s+", " ", (extracted_name or "").strip())
    resolved_ticker = str(resolved_ticker or "").strip().upper()
    resolved_title = re.sub(r"\s+", " ", str((ticker_to_title or {}).get(resolved_ticker, resolved_ticker) or "").strip())
    if not extracted_name or not resolved_ticker or not resolved_title:
        return False
    if _loosely_match(extracted_name, resolved_title):
        return True
    resolved_canonical = _canonical_company_name(resolved_title)
    if resolved_canonical and _loosely_match(extracted_name, resolved_canonical):
        return True
    if _context_has_8k_counterparty_anchor(ctx, resolved_title):
        return True
    if resolved_canonical and resolved_canonical != resolved_title and _context_has_8k_counterparty_anchor(ctx, resolved_canonical):
        return True
    flat_ctx = re.sub(r"\s+", " ", ctx or "")
    if re.search(rf"\b{re.escape(resolved_ticker)}\b", flat_ctx, re.I):
        return True
    return False


def _extract_8k_counterparty_candidates(text: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for ctx in _extract_8k_agreement_contexts(text):
        names = _extract_ctx_candidate_names(ctx)
        if not names:
            names = _extract_org_names_regex(ctx) or _extract_org_names_spacy(ctx)
        for name in names:
            cleaned = re.sub(r"\s+", " ", (name or "").strip())
            key = _normalize_company_name(cleaned) or cleaned.lower()
            if not cleaned or key in seen:
                continue
            seen.add(key)
            candidates.append((cleaned, ctx))
    return candidates


def _extract_org_names_spacy(text: str) -> list[str]:
    """Extract ORG entities using spaCy, deduplicated."""
    nlp = _get_spacy_nlp()
    if not nlp:
        return []
    try:
        with _SPACY_LOCK:
            doc = nlp(text[:100_000])  # cap to avoid memory issues
        orgs = []
        seen = set()
        for ent in doc.ents:
            if ent.label_ == "ORG":
                name = ent.text.strip()
                if len(name) >= 3 and name.lower() not in seen:
                    if not _GENERIC_TERMS.match(name):
                        seen.add(name.lower())
                        orgs.append(name)
        return orgs
    except Exception:
        return []


def _extract_org_names_regex(text: str, html_soup=None) -> list[str]:
    """Regex-based ORG extraction from plain text (and optionally a BeautifulSoup object for tables)."""
    candidates: set[str] = set()

    # Table cells — structured data is often the highest quality signal
    if html_soup is not None:
        for table in html_soup.find_all("table"):
            hdr = table.get_text().lower()
            if any(w in hdr for w in ("customer", "supplier", "vendor", "client", "revenue", "sales", "purchases")):
                for cell in table.find_all(["td", "th"]):
                    ct = cell.get_text(strip=True)
                    if re.match(r'^[A-Z][a-zA-Z\s,&.\-]{3,60}$', ct):
                        if not re.search(r"^(customer|supplier|revenue|sales|percent|%|total|amount)", ct, re.I):
                            if len(ct) >= 4:
                                candidates.add(ct)

    text_lower = text.lower()

    for phrase in _ALL_DISCLOSURE_PHRASES:
        if phrase not in text_lower:
            continue
        start = text_lower.find(phrase)
        if start == -1:
            continue
        ctx_start = max(0, start - 150)
        ctx_end   = min(len(text), start + 600)
        context   = text[ctx_start:ctx_end]

        # Quoted names
        for m in re.finditer(r'["\']([^"\']{3,80})["\']', context):
            name = m.group(1).strip()
            if _GENERIC_TERMS.match(name):
                continue
            if re.search(r"customer\s+[a-z1-9]|supplier\s+[a-z1-9]", name, re.I):
                continue
            if 4 <= len(name) <= 80:
                candidates.add(name)

        # "sales to X" / "purchases from X" patterns
        for m in re.finditer(
            r"(?:sales to|revenue from|purchases from|purchased from|"
            r"procurement from|customer|supplier)\s+"
            r"([A-Z][a-zA-Z\s,&.\-]{2,60}?)"
            r"(?:\s+(?:accounted|represented|comprised|constituted|represents)|\.|,|\d|$)",
            context,
        ):
            name = re.sub(r"[.,;:]+$", "", m.group(1).strip())
            name = re.sub(r"\s+", " ", name).strip()
            if 3 <= len(name) <= 60 and not re.search(r"^(customer|supplier|company|entity|party)\s+[a-z1-9]", name, re.I):
                candidates.add(name)

        # Capitalised multi-word names near the phrase — require ≥ 2 words
        phrase_pos = text_lower.find(phrase, start)
        if phrase_pos != -1:
            after = text[phrase_pos: phrase_pos + 200]
            for m in re.finditer(
                r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4}'
                r'(?:\s+(?:Inc|Corp|LLC|Ltd|Co|Company|Incorporated|Corporation))?)\b',
                after,
            ):
                name = m.group(1).strip()
                first = name.split()[0].lower()
                if first not in _SKIP_WORDS and 6 <= len(name) <= 60:
                    candidates.add(name)

        # Sentence-level extraction — require ≥ 2 words minimum
        for sentence in re.split(r'[.!?]\s+', context):
            if phrase not in sentence.lower():
                continue
            for m in re.finditer(
                r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,4}'
                r'(?:\s+(?:Inc|Corp|LLC|Ltd|Co|Company|Incorporated|Corporation|Technologies|Systems|Group|Holdings))?)\b',
                sentence,
            ):
                name = m.group(1).strip()
                first = name.split()[0].lower()
                if first not in {w.title() for w in _SKIP_WORDS} and first not in _SKIP_WORDS and 6 <= len(name) <= 60:
                    candidates.add(name)

    return list(candidates)


def _extract_all_org_names(html_text: str) -> list[str]:
    """
    Extract organization names from 10-K HTML text.
    Uses spaCy NER when available; supplements / falls back with regex.
    Deduplicates and merges results from both approaches.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_text, "html.parser")
        plain_text = soup.get_text(separator=" ", strip=True)
    except Exception:
        soup = None
        plain_text = re.sub(r"<[^>]+>", " ", html_text)

    # spaCy pass (fast on truncated text)
    spacy_orgs = _extract_org_names_spacy(plain_text)

    # Regex pass (best around signal phrases and tables)
    regex_orgs = _extract_org_names_regex(plain_text, html_soup=soup)

    # Merge, deduplicate (case-insensitive)
    seen: set[str] = set()
    merged: list[str] = []
    for name in spacy_orgs + regex_orgs:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            merged.append(name)

    return merged


# ──────────────────────────────────────────────────────────────────────────────
# Context-aware phrase classification
# ──────────────────────────────────────────────────────────────────────────────
def _classify_disclosure(text_lower: str) -> tuple[bool, bool]:
    """Return (has_customer_signal, has_supplier_signal)."""
    has_cust = any(p in text_lower for p in _CUSTOMER_PHRASE_SET)
    has_sup  = any(p in text_lower for p in _SUPPLIER_PHRASE_SET)
    return has_cust, has_sup


# ──────────────────────────────────────────────────────────────────────────────
# 10-K parsing → EdgeRecords
# ──────────────────────────────────────────────────────────────────────────────
def _parse_10k_and_extract_relationships(
    ticker: str,
    html_path: str,
    title_to_ticker: dict[str, str],
    ticker_to_title: dict[str, str] | None = None,
    filing_date: str | None = None,
) -> list[EdgeRecord]:
    """
    Parse 10-K file and return a list of EdgeRecords.

    Key design: instead of extracting ALL org names from the whole document and
    then applying document-level direction signals (which causes massive noise),
    we iterate over each *signal phrase occurrence* and extract a tight context
    window around it. Direction is determined per-occurrence:

    - Customer phrases ("significant customer", "X accounted for Y% of revenue"):
        The FILER is the SUPPLIER → sup=ticker, cust=resolved_name
    - Supplier phrases ("purchased from", "sole source", "significant supplier"):
        The NAMED company is the SUPPLIER → sup=resolved_name, cust=ticker

    Only names extracted from near a signal phrase are considered. This
    dramatically reduces false positives from risk-factor, competitor, and
    investment mentions in unrelated sections of the filing.
    """
    edges: list[EdgeRecord] = []
    try:
        with open(html_path, "r", encoding="utf-8", errors="replace") as f:
            html_text = f.read(SEC_EDGAR_MAX_PARSE_BYTES)
    except Exception as e:
        _log(f"Read {html_path}: {e}", "yellow")
        return []

    # Cheap prefilter: most filings never mention the target disclosure phrases.
    # Skip expensive HTML parsing entirely when the raw filing text has no signal.
    if not _has_any_disclosure_signal(html_text):
        return []

    plain_text = _extract_signal_centered_plain_text(html_text)
    if not plain_text:
        return []

    text_lower = plain_text.lower()
    if not _has_any_disclosure_signal(text_lower):
        return []

    source_date = _normalize_iso_date_str(filing_date)
    seen_pairs: set[tuple[str, str, str]] = set()
    # Cache name -> (ticker, confidence) within this parse to avoid repeated full lookups
    resolution_cache: dict[str, tuple[str | None, float]] = {}
    filer_identity = _company_identity_key((ticker_to_title or {}).get(ticker, ticker))

    # ── Context window size around each phrase occurrence ──────────────────────
    # We look 300 chars before the phrase and 600 chars after it.
    CTX_BEFORE = 300
    CTX_AFTER  = 600

    def _is_customer_phrase(phrase: str) -> bool:
        return phrase in _CUSTOMER_PHRASE_SET

    def _is_supplier_phrase(phrase: str) -> bool:
        return phrase in _SUPPLIER_PHRASE_SET

    def _is_agreement_phrase(phrase: str) -> bool:
        return phrase in _AGREEMENT_PHRASE_SET

    all_phrases = _ALL_DISCLOSURE_PHRASES

    # ── Main loop: one context window per phrase occurrence ────────────────────
    # Track processed windows to avoid processing overlapping regions twice
    processed_windows: list[tuple[int, int]] = []

    def _overlaps(start: int, end: int) -> bool:
        for ws, we in processed_windows:
            if start < we and end > ws:
                return True
        return False

    pos = 0
    while pos < len(text_lower):
        # Find the next signal phrase
        best_phrase: str | None = None
        best_pos: int = len(text_lower)

        for phrase in all_phrases:
            idx = text_lower.find(phrase, pos)
            if idx != -1 and idx < best_pos:
                best_pos = idx
                best_phrase = phrase

        if best_phrase is None:
            break

        # Cap windows per filing to avoid runaway cost on huge 10-Ks
        if len(processed_windows) >= SEC_EDGAR_MAX_PHRASE_WINDOWS:
            break

        ctx_start = max(0, best_pos - CTX_BEFORE)
        ctx_end   = min(len(plain_text), best_pos + CTX_AFTER)

        # Skip if this window overlaps a previously processed one (avoid duplicates)
        if not _overlaps(ctx_start, ctx_end):
            processed_windows.append((ctx_start, ctx_end))
            ctx        = plain_text[ctx_start:ctx_end]
            focus_ctx  = _focus_phrase_context(ctx, best_phrase)
            candidate_ctx = focus_ctx or ctx
            # Per-occurrence direction: customer phrase → filer is supplier
            #                           supplier phrase → named company is supplier
            is_cust_signal = _is_customer_phrase(best_phrase)

            candidate_names = _extract_ctx_candidate_names(candidate_ctx)
            if not candidate_names and candidate_ctx != ctx:
                candidate_names = _extract_ctx_candidate_names(ctx)
            for name in candidate_names:
                # Quality gate: reject before even attempting resolution
                if _GENERIC_TERMS.match(name):
                    continue
                if _is_blocked_counterparty_name(name):
                    continue
                if ticker.lower() in name.lower():
                    continue
                # Require at least 2 words OR a corporate suffix on a single word
                parts = name.strip().split()
                has_suffix = bool(re.search(
                    r'\b(Inc|Corp|LLC|Ltd|Co\.?|Company|Incorporated|Corporation|Technologies|Systems|Group|Holdings|Partners)\b',
                    name, re.I
                ))
                if len(parts) < 2 and not has_suffix:
                    continue
                # Require minimum meaningful length (avoids "It Corp", "Go Inc", etc.)
                norm_candidate = _normalize_company_name(name)
                if len(norm_candidate) < 5:
                    continue

                cache_key = name.strip().lower()
                if cache_key in resolution_cache:
                    resolved, conf = resolution_cache[cache_key]
                else:
                    resolved, conf = _resolve_customer_to_ticker(name, title_to_ticker)
                    resolution_cache[cache_key] = (resolved, conf)
                if not resolved or resolved == ticker or conf < SEC_EDGAR_MIN_CONFIDENCE:
                    continue
                resolved_identity = _company_identity_key((ticker_to_title or {}).get(resolved, resolved))
                if filer_identity and resolved_identity and filer_identity == resolved_identity:
                    continue
                is_real_estate_tenant_ctx = _context_is_real_estate_tenant_disclosure(candidate_ctx, company_name=name)
                if not is_real_estate_tenant_ctx and candidate_ctx != ctx:
                    is_real_estate_tenant_ctx = _context_is_real_estate_tenant_disclosure(ctx, company_name=name)
                if is_real_estate_tenant_ctx and is_cust_signal:
                    continue
                rev_pct = _extract_revenue_pct(candidate_ctx, company_name=name)
                if rev_pct is None and candidate_ctx != ctx:
                    rev_pct = _extract_revenue_pct(ctx, company_name=name)
                has_explicit_anchor = _context_has_explicit_counterparty_anchor(candidate_ctx, name)
                if not has_explicit_anchor and candidate_ctx != ctx:
                    has_explicit_anchor = _context_has_explicit_counterparty_anchor(ctx, name)
                if (
                    conf < 1.0
                    and len(_counterparty_signal_tokens(name)) < 2
                    and rev_pct is None
                    and not has_explicit_anchor
                ):
                    continue

                # Determine direction based on which type of phrase triggered this window
                if is_cust_signal:
                    # Customer phrase — default: filer is the supplier, named is the customer.
                    # Exception: if the context reveals the FILER is actually the customer
                    # of the named company (e.g. "we are a significant customer of X",
                    # "we represent ~55% of their revenues"), flip the direction so the
                    # named company becomes the supplier.
                    if _context_filer_is_customer(candidate_ctx, rev_pct):
                        sup_tick, cust_tick = resolved, ticker
                    else:
                        sup_tick, cust_tick = ticker, resolved
                    etype = "SUPPLIER_OF"
                    key = (sup_tick, cust_tick, etype)
                elif _is_agreement_phrase(best_phrase):
                    # Agreement phrases (supply agreement, joint venture, etc.) →
                    # relationship is symmetric; normalise key so we don't double-add
                    if conf < 0.95 or not has_explicit_anchor:
                        continue
                    sup_tick  = min(ticker, resolved)
                    cust_tick = max(ticker, resolved)
                    etype = "STRATEGIC_PARTNER"
                    key = (sup_tick, cust_tick, etype)
                else:
                    # "purchased from" / "sole source" / "significant supplier" →
                    # the named company is a SUPPLIER to the filer
                    sup_tick, cust_tick = resolved, ticker
                    etype = "SUPPLIER_OF"
                    key = (sup_tick, cust_tick, etype)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)

                rec: EdgeRecord = {
                    "sup":            sup_tick,
                    "cust":           cust_tick,
                    "confidence":     round(conf, 3),
                    "source":         "10-K",
                    "edge_type":      etype,
                    "last_confirmed": source_date,
                    "active_after":   source_date,
                }
                if rev_pct is not None:
                    rec["revenue_pct"] = rev_pct
                edges.append(rec)

        # Advance past this phrase occurrence
        pos = best_pos + len(best_phrase)

    return edges


# ──────────────────────────────────────────────────────────────────────────────
# Bidirectional cross-validation
# ──────────────────────────────────────────────────────────────────────────────
def _supplier_edge_strength(rec: EdgeRecord) -> tuple:
    try:
        conf = float(rec.get("confidence", 0.0) or 0.0)
    except Exception:
        conf = 0.0
    src = str(rec.get("source") or "").lower()
    cross = 1 if ("cross-validated" in src or "direction-resolved" in src) else 0
    rev_raw = rec.get("revenue_pct")
    try:
        rev = float(rev_raw) if rev_raw not in (None, "") else None
    except Exception:
        rev = None
    has_rev = 1 if rev is not None else 0
    last_confirmed = str(rec.get("last_confirmed") or "")
    return (conf, cross, has_rev, rev if rev is not None else -1.0, last_confirmed)


def cross_validate_edges(edges: list[EdgeRecord]) -> list[EdgeRecord]:
    """
    Reconcile reciprocal supplier edges conservatively.

    A reverse SUPPLIER_OF pair is a direction conflict, not a confirmation.
    Keep only the stronger direction; if both directions are equally strong,
    drop both instead of persisting contradictory edges.
    """
    supplier_index: dict[tuple[str, str], EdgeRecord] = {}
    other_edges: list[EdgeRecord] = []
    for e in edges:
        if (e.get("edge_type") or "SUPPLIER_OF") != "SUPPLIER_OF":
            other_edges.append(e.copy())
            continue
        key = (e["sup"], e["cust"])
        prev = supplier_index.get(key)
        if prev is None or _supplier_edge_strength(e) > _supplier_edge_strength(prev):
            supplier_index[key] = e

    result: list[EdgeRecord] = other_edges
    seen: set[tuple[str, str]] = set()
    for key, edge in supplier_index.items():
        if key in seen:
            continue
        rev_key = (key[1], key[0])
        seen.add(key)
        reverse = supplier_index.get(rev_key)
        if reverse is None:
            result.append(edge.copy())
            continue
        seen.add(rev_key)
        forward_strength = _supplier_edge_strength(edge)
        reverse_strength = _supplier_edge_strength(reverse)
        if forward_strength > reverse_strength:
            chosen = edge.copy()
            chosen["source"] = str(chosen.get("source") or "10-K") + "+direction-resolved"
            result.append(chosen)
        elif reverse_strength > forward_strength:
            chosen = reverse.copy()
            chosen["source"] = str(chosen.get("source") or "10-K") + "+direction-resolved"
            result.append(chosen)
        # Exact ties are dropped intentionally.

    return result


def _write_edges_csv(path: str, edges: list[EdgeRecord], append: bool = False) -> None:
    """Write edges to CSV, replacing the file by default so final confidence corrections persist."""
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    mode = "a" if append and os.path.isfile(path) else "w"
    with open(path, mode, newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if mode == "w":
            w.writerow(["sup", "cust", "confidence", "source", "revenue_pct", "edge_type", "last_confirmed", "active_after"])
        for rec in edges:
            w.writerow([
                rec["sup"], rec["cust"],
                rec.get("confidence", 0.7),
                rec.get("source", "10-K"),
                rec.get("revenue_pct", ""),
                rec.get("edge_type", "SUPPLIER_OF"),
                rec.get("last_confirmed", ""),
                rec.get("active_after", rec.get("last_confirmed", "")),
            ])


# ──────────────────────────────────────────────────────────────────────────────
# Recent 10-K/10-K-A filing detection
# ──────────────────────────────────────────────────────────────────────────────
def _safe_cache_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())[:180] or "cache_key"


def _parsed_edges_cache_path_for_key(cache_key: str) -> str:
    return os.path.join(_PARSED_EDGES_CACHE_DIR, f"{_safe_cache_key(cache_key)}.json")


def _load_parsed_edges_for_key(cache_key: str) -> list[EdgeRecord] | None:
    import json

    path = _parsed_edges_cache_path_for_key(cache_key)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and int(data.get("version") or 0) == SEC_EDGAR_PARSER_CACHE_VERSION:
            edges = data.get("edges")
            if isinstance(edges, list):
                return edges
    except Exception:
        pass
    return None


def _save_parsed_edges_for_key(cache_key: str, edges: list[EdgeRecord]) -> None:
    import json

    os.makedirs(_PARSED_EDGES_CACHE_DIR, exist_ok=True)
    path = _parsed_edges_cache_path_for_key(cache_key)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "version": SEC_EDGAR_PARSER_CACHE_VERSION,
                "edges": edges,
            }, f)
    except Exception:
        pass


def _submission_cache_path(filename: str) -> str:
    return os.path.join(SEC_EDGAR_CACHE_DIR, "submissions", os.path.basename(filename))


def _is_submission_payload(data: dict | None) -> bool:
    if not isinstance(data, dict):
        return False
    filings = data.get("filings") or {}
    if not isinstance(filings, dict):
        return False
    recent = filings.get("recent") or {}
    if not isinstance(recent, dict):
        return False
    forms = recent.get("form")
    filing_dates = recent.get("filingDate")
    return isinstance(forms, list) and isinstance(filing_dates, list)


def _load_submission_payload_by_filename(filename: str) -> dict | None:
    import json

    path = _submission_cache_path(filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if _is_submission_payload(data) else None
    except Exception:
        return None


def _save_submission_payload_by_filename(filename: str, data: dict) -> None:
    import json

    path = _submission_cache_path(filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass


def _fetch_submission_payload_by_filename(filename: str, allow_live_lookup: bool = True) -> dict | None:
    cached = _load_submission_payload_by_filename(filename)
    if cached is not None:
        return cached
    if not allow_live_lookup:
        return None
    try:
        r = _sec_rate_limited_get(
            f"https://data.sec.gov/submissions/{os.path.basename(filename)}",
            timeout=20,
            headers=_get_sec_request_headers(),
            label=f"SEC submissions {os.path.basename(filename)}",
        )
        data = r.json()
        if _is_submission_payload(data):
            _save_submission_payload_by_filename(filename, data)
            return data
    except Exception:
        return None
    return None


def _load_all_submission_payloads(cik: str, allow_live_lookup: bool = True) -> list[dict]:
    cik_pad = str(cik or "").strip().zfill(10)
    if not cik_pad or cik_pad == "0000000000":
        return []
    primary = _load_cached_submission_payload(cik_pad)
    if primary is None and allow_live_lookup:
        primary = _fetch_submission_payload_by_filename(f"CIK{cik_pad}.json", allow_live_lookup=True)
    if not isinstance(primary, dict):
        return []
    payloads: list[dict] = [primary]
    for file_info in ((primary.get("filings") or {}).get("files") or []):
        filename = os.path.basename(str((file_info or {}).get("name") or "").strip())
        if not filename:
            continue
        payload = _fetch_submission_payload_by_filename(filename, allow_live_lookup=allow_live_lookup)
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _collect_submission_filing_records(
    cik: str,
    *,
    form_types: tuple[str, ...],
    start_date: str,
    end_date: str | None = None,
    require_item: str | None = None,
    allow_live_lookup: bool = True,
) -> list[dict]:
    start_iso = _normalize_iso_date_str(start_date)
    end_iso = _normalize_iso_date_str(end_date) or datetime.now(timezone.utc).date().isoformat()
    if not start_iso:
        return []
    cik_pad = str(cik or "").strip().zfill(10)
    cik_int = str(int(cik_pad)) if cik_pad and cik_pad != "0000000000" else ""
    out: list[dict] = []
    seen_accessions: set[str] = set()
    for payload in _load_all_submission_payloads(cik_pad, allow_live_lookup=allow_live_lookup):
        recent = (payload.get("filings") or {}).get("recent") if isinstance(payload, dict) else None
        if not isinstance(recent, dict):
            continue
        forms = recent.get("form") or []
        accessions = recent.get("accessionNumber") or []
        primary_docs = recent.get("primaryDocument") or []
        filing_dates = recent.get("filingDate") or []
        items = recent.get("items") or []
        n = min(len(forms), len(accessions), len(primary_docs), len(filing_dates))
        for i in range(n):
            form = str(forms[i] or "").strip()
            if form not in form_types:
                continue
            filing_date = _normalize_iso_date_str(filing_dates[i] if i < len(filing_dates) else "")
            if not filing_date or filing_date < start_iso or filing_date > end_iso:
                continue
            if require_item:
                item_text = str(items[i] if i < len(items) else "")
                if require_item not in item_text:
                    continue
            accession_raw = str(accessions[i] or "").strip()
            accession_compact = accession_raw.replace("-", "")
            primary_document = str(primary_docs[i] or "").strip()
            if not accession_raw or not accession_compact or not primary_document:
                continue
            if accession_raw in seen_accessions:
                continue
            seen_accessions.add(accession_raw)
            out.append({
                "cik": cik_pad,
                "form": form,
                "filing_date": filing_date,
                "accession_number": accession_raw,
                "accession_compact": accession_compact,
                "primary_document": primary_document,
                "filing_url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_compact}/{primary_document}",
            })
    out.sort(key=lambda item: (item["filing_date"], item["accession_number"]))
    return out


def _download_sec_filing_document(
    *,
    filing_url: str,
    cache_subdir: str,
    ticker: str,
    cik: str,
    accession_compact: str,
    primary_document: str,
) -> tuple[str | None, bool]:
    def _invalid_sec_filing_content_reason(content: bytes) -> str | None:
        if not content or len(content) < 256:
            return "response too small"
        sample = content[:16384].decode("utf-8", errors="ignore").lower()
        bad_markers = (
            "request rate threshold exceeded",
            "your request originates from an undeclared automated tool",
            "access denied",
            "forbidden",
            "temporarily unavailable",
            "service unavailable",
            "sec.gov | request rate threshold exceeded",
        )
        for marker in bad_markers:
            if marker in sample:
                return marker
        return None

    filename = os.path.basename(primary_document) or "filing.html"
    cache_dir = os.path.join(
        SEC_EDGAR_CACHE_DIR,
        cache_subdir,
        str(cik or "").strip().zfill(10),
        accession_compact,
    )
    cache_path = os.path.join(cache_dir, filename)
    if os.path.isfile(cache_path):
        try:
            with open(cache_path, "rb") as f:
                cached_content = f.read()
            invalid_reason = _invalid_sec_filing_content_reason(cached_content)
            if invalid_reason is None:
                return cache_path, True
            try:
                os.remove(cache_path)
            except Exception:
                pass
            _log(
                f"Historical filing cache invalid for {ticker} {accession_compact} ({invalid_reason}); refetching.",
                "yellow",
            )
        except Exception:
            pass
    os.makedirs(cache_dir, exist_ok=True)
    try:
        last_invalid_reason = None
        for attempt in range(3):
            r = _sec_rate_limited_get(
                filing_url,
                headers=_get_sec_request_headers(),
                timeout=30,
                label=f"Historical filing download {ticker} {accession_compact}",
            )
            invalid_reason = _invalid_sec_filing_content_reason(r.content)
            if invalid_reason is None:
                with open(cache_path, "wb") as f:
                    f.write(r.content)
                return cache_path, False
            last_invalid_reason = invalid_reason
            if attempt < 2:
                backoff = 5 * (attempt + 1)
                _log(
                    f"Historical filing download {ticker} {accession_compact} returned invalid SEC content ({invalid_reason}); retrying in {backoff}s.",
                    "yellow",
                )
                time.sleep(backoff)
        raise ValueError(f"invalid SEC filing content: {last_invalid_reason or 'unknown response'}")
    except Exception as e:
        _log(f"Historical filing download failed for {ticker} {accession_compact}: {e}", "yellow")
        return None, False


def _merge_edge_record_maps(target: dict[tuple, EdgeRecord], edges: list[EdgeRecord]) -> None:
    for rec in edges or []:
        sup = str(rec.get("sup") or "").strip().upper()
        cust = str(rec.get("cust") or "").strip().upper()
        edge_type = str(rec.get("edge_type") or "SUPPLIER_OF").strip() or "SUPPLIER_OF"
        if not sup or not cust or sup == cust:
            continue
        normalized = dict(rec)
        if edge_type == "STRATEGIC_PARTNER":
            ordered = sorted([sup, cust])
            normalized["sup"], normalized["cust"] = ordered[0], ordered[1]
            key = (edge_type, ordered[0], ordered[1])
        else:
            normalized["sup"], normalized["cust"] = sup, cust
            key = (edge_type, sup, cust)
        prev = target.get(key)
        if prev is None:
            target[key] = normalized
            continue
        merged = dict(prev)
        try:
            merged["confidence"] = max(float(prev.get("confidence") or 0.0), float(normalized.get("confidence") or 0.0))
        except Exception:
            pass
        prev_lc = _normalize_iso_date_str(prev.get("last_confirmed"))
        new_lc = _normalize_iso_date_str(normalized.get("last_confirmed"))
        if new_lc and (not prev_lc or new_lc > prev_lc):
            merged["last_confirmed"] = new_lc
            merged["source"] = normalized.get("source") or merged.get("source")
        prev_aa = _normalize_iso_date_str(prev.get("active_after"))
        new_aa = _normalize_iso_date_str(normalized.get("active_after"))
        if new_aa and (not prev_aa or new_aa < prev_aa):
            merged["active_after"] = new_aa
        if normalized.get("revenue_pct") not in (None, ""):
            prev_rev = prev.get("revenue_pct")
            try:
                merged["revenue_pct"] = max(
                    float(prev_rev) if prev_rev not in (None, "") else 0.0,
                    float(normalized.get("revenue_pct") or 0.0),
                )
            except Exception:
                merged["revenue_pct"] = normalized.get("revenue_pct")
        target[key] = merged


def run_sec_edgar_supply_chain_historical(
    *,
    start_date: str,
    end_date: str | None = None,
    tickers: list[str] | None = None,
    ticker_to_cik: dict[str, str] | None = None,
    output_csv_path: str | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
    edge_cb: Callable[[list[EdgeRecord]], None] | None = None,
    edge_cb_batch_size: int = 100,
    summary_cb: Callable[[dict], None] | None = None,
    ignore_parsed_edge_cache: bool = False,
    ignore_existing_output_csv: bool = False,
) -> list[EdgeRecord]:
    start_iso = _normalize_iso_date_str(start_date)
    end_iso = _normalize_iso_date_str(end_date) or datetime.now(timezone.utc).date().isoformat()
    if not start_iso:
        raise ValueError("start_date must be in YYYY-MM-DD format")

    ticker_to_title, title_to_ticker, cik_to_ticker = _fetch_sec_company_tickers()
    if tickers is None:
        tickers = sorted(ticker_to_title)
    else:
        tickers = sorted({str(t or "").strip().upper() for t in tickers if t})
    ticker_to_cik = dict(ticker_to_cik or {})
    sec_ticker_to_cik = {ticker: cik for cik, ticker in cik_to_ticker.items()}
    for ticker in tickers:
        if ticker not in ticker_to_cik and ticker in sec_ticker_to_cik:
            ticker_to_cik[ticker] = sec_ticker_to_cik[ticker]

    merged_by_key: dict[tuple, EdgeRecord] = {}
    if not ignore_existing_output_csv and output_csv_path and os.path.isfile(output_csv_path):
        try:
            with open(output_csv_path, "r", newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    sup = (row.get("sup") or "").strip().upper()
                    cust = (row.get("cust") or "").strip().upper()
                    if not sup or not cust:
                        continue
                    rec: EdgeRecord = {
                        "sup": sup,
                        "cust": cust,
                        "confidence": float(row.get("confidence") or 0.7),
                        "source": row.get("source") or "10-K",
                        "edge_type": row.get("edge_type") or "SUPPLIER_OF",
                        "last_confirmed": row.get("last_confirmed") or "",
                        "active_after": row.get("active_after") or row.get("last_confirmed") or "",
                    }
                    if row.get("revenue_pct"):
                        try:
                            rec["revenue_pct"] = float(row.get("revenue_pct") or 0.0)
                        except Exception:
                            pass
                    _merge_edge_record_maps(merged_by_key, [rec])
        except Exception:
            pass

    pending_edges: list[EdgeRecord] = []
    total = len(tickers)
    filings_processed = 0
    latest_filing_date = ""
    for idx, ticker in enumerate(tickers):
        cik = ticker_to_cik.get(ticker)
        if not cik:
            continue
        filings = _collect_submission_filing_records(
            cik,
            form_types=("10-K", "10-K/A"),
            start_date=start_iso,
            end_date=end_iso,
            allow_live_lookup=True,
        )
        for filing in filings:
            filings_processed += 1
            filing_date = _normalize_iso_date_str(filing.get("filing_date"))
            if filing_date and (not latest_filing_date or filing_date > latest_filing_date):
                latest_filing_date = filing_date
            cache_key = f"10k_{ticker}_{filing['accession_compact']}"
            parsed = None if ignore_parsed_edge_cache else _load_parsed_edges_for_key(cache_key)
            if parsed is None:
                filing_path, _ = _download_sec_filing_document(
                    filing_url=filing["filing_url"],
                    cache_subdir="historical_10k_filings",
                    ticker=ticker,
                    cik=filing["cik"],
                    accession_compact=filing["accession_compact"],
                    primary_document=filing["primary_document"],
                )
                if not filing_path:
                    continue
                parsed = _parse_10k_and_extract_relationships(
                    ticker,
                    filing_path,
                    title_to_ticker,
                    ticker_to_title=ticker_to_title,
                    filing_date=filing["filing_date"],
                )
                _save_parsed_edges_for_key(cache_key, parsed)
            if not parsed:
                continue
            _merge_edge_record_maps(merged_by_key, parsed)
            if edge_cb:
                pending_edges.extend(parsed)
                if len(pending_edges) >= edge_cb_batch_size:
                    edge_cb(pending_edges[:])
                    pending_edges.clear()
        if progress_cb:
            progress_cb(idx + 1, total, f"Historical 10-K {idx + 1}/{total} — {len(merged_by_key)} merged edges")

    if edge_cb and pending_edges:
        edge_cb(pending_edges[:])
        pending_edges.clear()

    all_edges = cross_validate_edges(list(merged_by_key.values()))
    if output_csv_path:
        _write_edges_csv(output_csv_path, all_edges, append=False)
    if summary_cb:
        summary_cb({
            "start_date": start_iso,
            "end_date": end_iso,
            "filings_processed": filings_processed,
            "latest_filing_date": latest_filing_date or "",
            "edge_count": len(all_edges),
        })
    return all_edges


def scrape_8k_agreements_since(
    *,
    tickers: list[str],
    title_to_ticker: dict[str, str],
    start_date: str,
    end_date: str | None = None,
    ticker_to_cik: dict[str, str] | None = None,
    progress_cb: Callable[[int, int, str], None] | None = None,
    edge_cb: Callable[[list[EdgeRecord]], None] | None = None,
    edge_cb_batch_size: int = 100,
    summary_cb: Callable[[dict], None] | None = None,
) -> list[EdgeRecord]:
    start_iso = _normalize_iso_date_str(start_date)
    end_iso = _normalize_iso_date_str(end_date) or datetime.now(timezone.utc).date().isoformat()
    if not start_iso:
        raise ValueError("start_date must be in YYYY-MM-DD format")

    ticker_to_title, _, cik_to_ticker = _fetch_sec_company_tickers()
    cik_from_ticker = {v: k for k, v in cik_to_ticker.items()}
    if ticker_to_cik:
        for ticker, cik in ticker_to_cik.items():
            if ticker and cik:
                cik_from_ticker[str(ticker).strip().upper()] = str(cik).strip().zfill(10)

    merged_by_key: dict[tuple, EdgeRecord] = {}
    pending_edges: list[EdgeRecord] = []
    total = len(tickers)
    filings_processed = 0
    latest_filing_date = ""
    for idx, ticker in enumerate(tickers):
        cik = cik_from_ticker.get(str(ticker or "").strip().upper())
        if not cik:
            continue
        filer_identity = _company_identity_key(ticker_to_title.get(ticker, ticker))
        filings = _collect_submission_filing_records(
            cik,
            form_types=("8-K",),
            start_date=start_iso,
            end_date=end_iso,
            require_item="1.01",
            allow_live_lookup=True,
        )
        for filing in filings:
            filings_processed += 1
            filing_date = _normalize_iso_date_str(filing.get("filing_date"))
            if filing_date and (not latest_filing_date or filing_date > latest_filing_date):
                latest_filing_date = filing_date
            filing_path, _ = _download_sec_filing_document(
                filing_url=filing["filing_url"],
                cache_subdir="historical_8k_filings",
                ticker=ticker,
                cik=filing["cik"],
                accession_compact=filing["accession_compact"],
                primary_document=filing["primary_document"],
            )
            if not filing_path:
                continue
            try:
                with open(filing_path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except Exception:
                continue
            text_lower = text.lower()
            if "1.01" not in text_lower and "material definitive agreement" not in text_lower:
                continue
            if not any(phrase in text_lower for phrase in _AGREEMENT_PHRASES):
                continue
            filing_edges: list[EdgeRecord] = []
            for name, ctx in _extract_8k_counterparty_candidates(text):
                if _is_blocked_counterparty_name(name):
                    continue
                resolved, conf = _resolve_customer_to_ticker(name, title_to_ticker)
                if not resolved or resolved == ticker or conf < max(0.95, SEC_EDGAR_MIN_CONFIDENCE):
                    continue
                if not _context_has_8k_counterparty_anchor(ctx, name):
                    continue
                if not _resolved_8k_counterparty_supported(ctx, name, resolved, ticker_to_title):
                    continue
                resolved_identity = _company_identity_key(ticker_to_title.get(resolved, resolved))
                if filer_identity and resolved_identity and filer_identity == resolved_identity:
                    continue
                ordered = sorted([str(ticker).strip().upper(), str(resolved).strip().upper()])
                filing_edges.append({
                    "sup": ordered[0],
                    "cust": ordered[1],
                    "confidence": round(max(conf, _8K_AGREEMENT_CONFIDENCE), 3),
                    "source": "8-K Item 1.01",
                    "edge_type": "STRATEGIC_PARTNER",
                    "last_confirmed": filing["filing_date"],
                    "active_after": filing["filing_date"],
                })
            if not filing_edges:
                continue
            _merge_edge_record_maps(merged_by_key, filing_edges)
            if edge_cb:
                pending_edges.extend(filing_edges)
                if len(pending_edges) >= edge_cb_batch_size:
                    edge_cb(pending_edges[:])
                    pending_edges.clear()
        if progress_cb:
            progress_cb(idx + 1, total, f"Historical 8-K {idx + 1}/{total} — {len(merged_by_key)} merged edges")

    if edge_cb and pending_edges:
        edge_cb(pending_edges[:])
        pending_edges.clear()
    if summary_cb:
        summary_cb({
            "start_date": start_iso,
            "end_date": end_iso,
            "filings_processed": filings_processed,
            "latest_filing_date": latest_filing_date or "",
            "edge_count": len(merged_by_key),
        })
    return list(merged_by_key.values())


def get_recent_10k_filing_dates(hours: int = 24) -> dict[str, str]:
    """Return ticker -> filing date for 10-K / 10-K-A filings in the last `hours`."""
    import requests
    import xml.etree.ElementTree as ET

    _, _, cik_to_ticker = _fetch_sec_company_tickers()
    if not cik_to_ticker:
        _log("No CIK mapping; cannot get recent filings.", "yellow")
        return {}

    utc = timezone.utc
    cutoff = datetime.now(utc) - timedelta(hours=hours)
    headers = _get_sec_request_headers()
    filing_dates: dict[str, str] = {}

    for form_type in ("10-K", "10-K/A"):
        try:
            url = (
                "https://www.sec.gov/cgi-bin/browse-edgar"
                "?action=getcurrent&type=" + form_type.replace("/", "%2F") +
                "&company=&dateb=&owner=include&start=0&count=100&output=atom"
            )
            r = _sec_rate_limited_get(
                url,
                headers=headers,
                timeout=30,
                label=f"Recent filings feed {form_type}",
            )
            root = ET.fromstring(r.text)
        except Exception as e:
            _log(f"Recent filings feed ({form_type}): {e}", "yellow")
            continue

        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns) or root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            filing_date = ""
            updated_el = entry.find("atom:updated", ns) or entry.find("{http://www.w3.org/2005/Atom}updated")
            if updated_el is not None and updated_el.text:
                try:
                    dt = datetime.fromisoformat(updated_el.text.replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=utc)
                    else:
                        dt = dt.astimezone(utc)
                    if dt < cutoff:
                        continue
                    filing_date = dt.date().isoformat()
                except Exception:
                    filing_date = ""
            for link in entry.findall("atom:link", ns) or entry.findall("{http://www.w3.org/2005/Atom}link"):
                href = (link.get("href") or "").strip()
                if "CIK=" in href.upper():
                    m = re.search(r"CIK=(\d+)", href, re.I)
                    if m:
                        cik = m.group(1).zfill(10)
                        ticker = cik_to_ticker.get(cik)
                        if ticker:
                            prev = filing_dates.get(ticker, "")
                            if filing_date and (not prev or filing_date > prev):
                                filing_dates[ticker] = filing_date
                            elif ticker not in filing_dates:
                                filing_dates[ticker] = filing_date
                        break

    return filing_dates


def get_tickers_with_recent_10k_filings(hours: int = 24) -> list[str]:
    """Return tickers that filed a 10-K or 10-K/A in the last `hours` (SEC Atom feed)."""
    out = sorted(get_recent_10k_filing_dates(hours=hours))
    _log(f"Recent 10-K/10-K/A in last {hours}h: {len(out)} companies.", "cyan")
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 8-K Item 1.01 material agreement scraper
# ──────────────────────────────────────────────────────────────────────────────
_8K_AGREEMENT_CONFIDENCE = 0.80   # 8-K agreements are explicitly disclosed → high confidence

def scrape_8k_agreements(
    tickers: list[str],
    title_to_ticker: dict[str, str],
    hours: int = 24,
    max_per_ticker: int = 3,
    progress_cb: Callable[[int, int, str], None] | None = None,
    ticker_to_cik: dict[str, str] | None = None,
    edge_cb: Callable[[list], None] | None = None,
    edge_cb_batch_size: int = 100,
) -> list[EdgeRecord]:
    """
    Scrape 8-K Item 1.01 (Entry into a Material Definitive Agreement) filings for
    companies in `tickers` filed within `hours`.

    Returns EdgeRecords with edge_type = "STRATEGIC_PARTNER" for supply, license,
    joint-venture, and strategic partnership agreements.

    progress_cb(current_index, total_tickers, message) is called every 50 tickers if provided.
    ticker_to_cik: optional map from ticker to CIK (e.g. from graph); merged with SEC list.
    """
    ticker_to_title, _, cik_to_ticker = _fetch_sec_company_tickers()
    cik_from_ticker = {v: k for k, v in cik_to_ticker.items()}
    if ticker_to_cik:
        for t, cik in ticker_to_cik.items():
            if t and cik:
                cik_from_ticker[t] = str(cik).strip().zfill(10)

    headers = _get_sec_request_headers()
    utc = timezone.utc
    cutoff = datetime.now(utc) - timedelta(hours=hours)
    edges: list[EdgeRecord] = []
    seen: set[tuple[str, str]] = set()

    # Cache SEC submissions JSON per CIK (shared across phases, reusable for 24h)
    _submissions_cache_dir = os.path.join(SEC_EDGAR_CACHE_DIR, "submissions")
    os.makedirs(_submissions_cache_dir, exist_ok=True)

    def _get_cached_submissions(cik: str) -> dict | None:
        """Load cached SEC submissions JSON for a CIK (24h TTL)."""
        import json
        path = os.path.join(_submissions_cache_dir, f"CIK{str(cik).strip().zfill(10)}.json")
        if not os.path.isfile(path):
            return None
        try:
            age = time.time() - os.path.getmtime(path)
            if age > 86400:  # 24h
                return None
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def _save_submissions_cache(cik: str, data: dict) -> None:
        import json
        path = os.path.join(_submissions_cache_dir, f"CIK{str(cik).strip().zfill(10)}.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _get_8k_urls(cik: str) -> list[tuple[str, str]]:
        """Return recent 8-K Item 1.01 primary document URLs for a CIK using data.sec.gov Submissions API.
        Pre-filters on the 'items' field so we only download filings that contain Item 1.01."""
        try:
            cik_pad = str(cik).strip().zfill(10)
            # Try cached submission first
            data = _get_cached_submissions(cik)
            if data is None:
                url = f"https://data.sec.gov/submissions/CIK{cik_pad}.json"
                r = _sec_rate_limited_get(
                    url,
                    headers=headers,
                    timeout=15,
                    label=f"8-K submissions {cik_pad}",
                )
                data = r.json()
                _save_submissions_cache(cik, data)
        except Exception:
            return []
        filings = (data.get("filings") or {}).get("recent") if isinstance(data, dict) else None
        if not isinstance(filings, dict):
            return []
        forms = filings.get("form") or []
        accession_numbers = filings.get("accessionNumber") or []
        primary_docs = filings.get("primaryDocument") or []
        filing_dates = filings.get("filingDate") or []
        items_list = filings.get("items") or []
        cutoff_date = cutoff.date() if hasattr(cutoff, "date") else cutoff
        # Company CIK (without leading zeros) for the Archives URL path
        cik_int = str(int(cik))
        out: list[tuple[str, str]] = []
        for i in range(min(len(forms), len(accession_numbers))):
            if forms[i] != "8-K":
                continue
            # Pre-filter: only download 8-Ks that report Item 1.01 (material agreements)
            item_str = items_list[i] if i < len(items_list) else ""
            if item_str and "1.01" not in item_str:
                continue
            try:
                fd = filing_dates[i] if i < len(filing_dates) else ""
                if fd:
                    doc_date = datetime.strptime(fd, "%Y-%m-%d").date()
                    if doc_date < cutoff_date:
                        continue
            except Exception:
                pass
            acc_raw = accession_numbers[i] or ""
            acc = acc_raw.replace("-", "")
            prim = primary_docs[i] if i < len(primary_docs) else ""
            if not acc or not prim:
                continue
            out.append((f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{prim}", _normalize_iso_date_str(fd)))
            if len(out) >= max_per_ticker:
                break
        return out

    total_tickers = len(tickers)
    _edge_cb_pending: list = []  # edges not yet flushed via edge_cb
    def _maybe_flush_edges():
        nonlocal _edge_cb_pending
        if edge_cb and len(_edge_cb_pending) >= edge_cb_batch_size:
            edge_cb(_edge_cb_pending[:])
            _edge_cb_pending.clear()

    _log(f"8-K scraper: checking {total_tickers} tickers for recent material agreements...", "cyan")
    filings_checked = 0
    consecutive_errors = 0
    for idx, ticker in enumerate(tickers):
        if progress_cb and ((idx + 1) % 25 == 0 or idx == 0):
            progress_cb(idx + 1, total_tickers, f"Checking 8-K... {idx + 1}/{total_tickers} ({len(edges)} edges, {filings_checked} filings)")
        if (idx + 1) % 250 == 0:
            _log(f"8-K scraper: {idx + 1}/{total_tickers} tickers checked, {len(edges)} edges found, {filings_checked} filings read...", "white")
        # Cooldown every 100 tickers to stay under SEC rate limits
        if idx > 0 and idx % 100 == 0:
            time.sleep(5)
        # Dynamic backoff on consecutive errors (likely 429)
        if consecutive_errors >= 10:
            _log(f"8-K scraper: {consecutive_errors} consecutive errors, backing off 60s...", "yellow")
            time.sleep(60)
            consecutive_errors = 0
        cik = cik_from_ticker.get(ticker)
        if not cik:
            continue
        filer_identity = _company_identity_key(ticker_to_title.get(ticker, ticker))
        filing_urls = _get_8k_urls(cik)
        for filing_url, filing_date in filing_urls:
            try:
                r = _sec_rate_limited_get(
                    filing_url,
                    headers=headers,
                    timeout=15,
                    label=f"8-K filing {ticker}",
                )
                text = r.text
                filings_checked += 1
                consecutive_errors = 0
            except Exception:
                consecutive_errors += 1
                continue

            # Only process if Item 1.01 is mentioned
            text_lower = text.lower()
            if "1.01" not in text_lower and "material definitive agreement" not in text_lower:
                continue
            if not any(p in text_lower for p in _AGREEMENT_PHRASES):
                continue

            for name, ctx in _extract_8k_counterparty_candidates(text):
                if _is_blocked_counterparty_name(name):
                    continue
                resolved, conf = _resolve_customer_to_ticker(name, title_to_ticker)
                if not resolved or resolved == ticker or conf < max(0.95, SEC_EDGAR_MIN_CONFIDENCE):
                    continue
                if not _context_has_8k_counterparty_anchor(ctx, name):
                    continue
                if not _resolved_8k_counterparty_supported(ctx, name, resolved, ticker_to_title):
                    continue
                resolved_identity = _company_identity_key(ticker_to_title.get(resolved, resolved))
                if filer_identity and resolved_identity and filer_identity == resolved_identity:
                    continue
                key = (min(ticker, resolved), max(ticker, resolved))
                if key in seen:
                    continue
                seen.add(key)
                rec: EdgeRecord = {
                    "sup":            ticker,
                    "cust":           resolved,
                    "confidence":     round(max(conf, _8K_AGREEMENT_CONFIDENCE), 3),
                    "source":         "8-K Item 1.01",
                    "edge_type":      "STRATEGIC_PARTNER",
                    "last_confirmed": filing_date,
                    "active_after":   filing_date,
                }
                edges.append(rec)
                _edge_cb_pending.append(rec)
                _maybe_flush_edges()

    # Flush remaining edges
    if edge_cb and _edge_cb_pending:
        edge_cb(_edge_cb_pending[:])
        _edge_cb_pending.clear()
    if progress_cb:
        progress_cb(total_tickers, total_tickers, f"8-K complete: {len(edges)} edges")
    _log(f"8-K scraper: found {len(edges)} strategic partner edges.", "green")
    return edges


def _extract_latest_10k_filing_date_from_submission_payload(data: dict | None) -> str:
    """Return the most recent 10-K / 10-K-A filing date from a SEC submissions payload."""
    filings = (data.get("filings") or {}).get("recent") if isinstance(data, dict) else None
    if not isinstance(filings, dict):
        return ""
    forms = filings.get("form") or []
    filing_dates = filings.get("filingDate") or []
    best = ""
    for i in range(min(len(forms), len(filing_dates))):
        if forms[i] not in ("10-K", "10-K/A"):
            continue
        candidate = _normalize_iso_date_str(filing_dates[i] if i < len(filing_dates) else "")
        if candidate and (not best or candidate > best):
            best = candidate
    return best


def _iter_submission_cache_paths(cik_pad: str) -> list[str]:
    filename = f"CIK{cik_pad}.json"
    return [
        os.path.join(SEC_EDGAR_CACHE_DIR, "submissions", filename),
        os.path.join(SEC_EDGAR_CACHE_DIR, "phase2_sec_submissions", filename),
    ]


def _load_cached_submission_payload(cik_pad: str) -> dict | None:
    import json

    for cache_path in _iter_submission_cache_paths(cik_pad):
        if not os.path.isfile(cache_path):
            continue
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if _is_submission_payload(data):
                return data
        except Exception:
            continue
    return None


def _fetch_latest_10k_filing_date(cik: str | None, allow_live_lookup: bool | None = None) -> str:
    """Best-effort latest 10-K / 10-K-A filing date for a CIK from SEC submissions."""
    import json

    allow_live = SEC_EDGAR_ALLOW_LIVE_FILING_DATE_LOOKUP if allow_live_lookup is None else bool(allow_live_lookup)
    cik_pad = str(cik or "").strip().zfill(10)
    if not cik_pad or cik_pad == "0000000000":
        return ""
    data = _load_cached_submission_payload(cik_pad)
    if data is None and allow_live:
        try:
            cache_dir = os.path.join(SEC_EDGAR_CACHE_DIR, "submissions")
            cache_path = os.path.join(cache_dir, f"CIK{cik_pad}.json")
            r = _sec_rate_limited_get(
                f"https://data.sec.gov/submissions/CIK{cik_pad}.json",
                headers=_get_sec_request_headers(),
                timeout=20,
                label=f"Latest 10-K submissions {cik_pad}",
            )
            data = r.json()
            os.makedirs(cache_dir, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            return ""
    return _extract_latest_10k_filing_date_from_submission_payload(data)


# ──────────────────────────────────────────────────────────────────────────────
# Parsed-edges cache: skip re-parsing 10-K HTML when edges already extracted
# ──────────────────────────────────────────────────────────────────────────────
_PARSED_EDGES_CACHE_DIR = os.path.join(SEC_EDGAR_CACHE_DIR, "parsed_edges")


def _parsed_edges_cache_path(ticker: str) -> str:
    return os.path.join(_PARSED_EDGES_CACHE_DIR, f"{ticker.upper()}.json")


def _load_parsed_edges(ticker: str) -> list[EdgeRecord] | None:
    """Load previously parsed edges for a ticker. Returns None on miss."""
    import json
    path = _parsed_edges_cache_path(ticker)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if int(data.get("version") or 0) != SEC_EDGAR_PARSER_CACHE_VERSION:
                return None
            edges = data.get("edges")
            if isinstance(edges, list):
                return edges
        # Reject legacy list caches so parser hardening takes effect on the next run.
    except Exception:
        pass
    return None


def _save_parsed_edges(ticker: str, edges: list[EdgeRecord]) -> None:
    """Cache parsed edges for a ticker so re-parsing is skipped on next run."""
    import json
    os.makedirs(_PARSED_EDGES_CACHE_DIR, exist_ok=True)
    path = _parsed_edges_cache_path(ticker)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "version": SEC_EDGAR_PARSER_CACHE_VERSION,
                "edges": edges,
            }, f)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# File helpers (10-K download / cache)
# ──────────────────────────────────────────────────────────────────────────────
def _find_latest_10k_html_in_dir(filing_dir: str) -> str | None:
    if not os.path.isdir(filing_dir):
        return None
    full_submission = os.path.join(filing_dir, "full-submission.txt")
    if os.path.isfile(full_submission):
        return full_submission
    candidates = []
    for f in os.listdir(filing_dir):
        if not (f.endswith(".htm") or f.endswith(".html")):
            continue
        path = os.path.join(filing_dir, f)
        if "exhibit" in f.lower() or (f.lower().startswith("ex") and "-" in f):
            continue
        candidates.append(path)
    if candidates:
        return candidates[0]
    for f in os.listdir(filing_dir):
        if f.endswith(".htm") or f.endswith(".html"):
            return os.path.join(filing_dir, f)
    return None


def _do_download_10k_in_subprocess(company_name: str, email: str, download_dir: str, identifier: str) -> None:
    try:
        from sec_edgar_downloader import Downloader
        dl = Downloader(company_name, email, download_dir)
        dl.get("10-K", identifier, limit=1)
    except Exception:
        pass


def _cached_10k_path(download_dir: str, identifier: str, debug: bool = False) -> str | None:
    root = os.path.abspath(download_dir)
    base = os.path.join(root, "sec-edgar-filings", identifier, "10-K")
    if not os.path.isdir(base):
        return None
    subdirs = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    if subdirs:
        subdirs.sort(reverse=True)
        found = _find_latest_10k_html_in_dir(os.path.join(base, subdirs[0]))
        if found:
            return found
    return _find_latest_10k_html_in_dir(base)


def _download_10k_for_ticker(
    ticker: str, download_dir: str, cik: str | None = None, debug: bool = False
) -> tuple[str | None, bool]:
    try:
        from sec_edgar_downloader import Downloader  # noqa: F401 (import check)
    except ImportError:
        _log("sec-edgar-downloader not installed. pip install sec-edgar-downloader", "red")
        return None, False

    os.makedirs(download_dir, exist_ok=True)
    identifier = str(cik).zfill(10) if cik else ticker.upper()
    cached = _cached_10k_path(download_dir, identifier, debug=debug)
    if cached:
        return cached, True

    timeout_sec = max(30, SEC_EDGAR_DOWNLOAD_TIMEOUT_SEC)
    import multiprocessing
    proc = multiprocessing.Process(
        target=_do_download_10k_in_subprocess,
        args=(SEC_EDGAR_COMPANY_NAME, SEC_EDGAR_EMAIL, download_dir, identifier),
    )
    proc.start()
    proc.join(timeout=timeout_sec)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=5)
        if proc.is_alive():
            proc.kill()
        _log(f"Download 10-K for {ticker}: timeout after {timeout_sec}s (skipping)", "yellow")
        return None, False

    base = os.path.join(download_dir, "sec-edgar-filings", identifier, "10-K")
    if os.path.isdir(base):
        subdirs = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
        if subdirs:
            subdirs.sort(reverse=True)
            found = _find_latest_10k_html_in_dir(os.path.join(base, subdirs[0]))
            if found:
                return found, False
    return None, False


# ──────────────────────────────────────────────────────────────────────────────
# Main scraper loop
# ──────────────────────────────────────────────────────────────────────────────
def run_sec_edgar_supply_chain_scraper(
    tickers: list[str] | None = None,
    output_csv_path: str | None = None,
    max_companies: int | None = None,
    ticker_to_cik: dict[str, str] | None = None,
    filings_dir: str | None = None,
    progress_callback: Callable[[], None] | None = None,
    edges_callback: Callable[[list[EdgeRecord]], None] | None = None,
) -> list[EdgeRecord]:
    """
    Scrape 10-K filings for significant customer/supplier disclosures.
    Returns list of EdgeRecords (with confidence, source, revenue_pct metadata).
    Also writes to CSV if output_csv_path is set (columns: sup, cust, confidence, source, revenue_pct).
    Progress is persisted in the store; resume on restart.
    If progress_callback is set, it is called every 50 companies (for ETA logging).
    If edges_callback is set, it is called every 50 companies with the new edges batch for that chunk (for incremental Neo4j merge).
    """
    download_dir  = os.path.abspath(filings_dir) if filings_dir else SEC_EDGAR_FILINGS_DIR
    max_companies = max_companies or SEC_EDGAR_MAX_COMPANIES

    _log("Loading SEC company tickers for name resolution...", "cyan")
    ticker_to_title, title_to_ticker, cik_to_ticker = _fetch_sec_company_tickers()
    if not title_to_ticker:
        _log("No company tickers loaded; name resolution will be limited.", "yellow")

    if tickers is None:
        tickers = sorted(list(ticker_to_title.keys())[:max_companies])
    else:
        tickers = sorted([t.upper() for t in tickers if t][:max_companies])

    if not tickers:
        _log("No tickers to process.", "yellow")
        return []

    # Ensure every ticker has a CIK for cache lookup (cache is keyed by CIK only)
    ticker_to_cik = dict(ticker_to_cik or {})
    if cik_to_ticker:
        sec_ticker_to_cik = {t: c for c, t in cik_to_ticker.items()}
        for t in tickers:
            if t not in ticker_to_cik and t in sec_ticker_to_cik:
                ticker_to_cik[t] = sec_ticker_to_cik[t]

    total = len(tickers)
    _log(f"Processing {total} companies (cache: {download_dir})...", "cyan")
    os.makedirs(download_dir, exist_ok=True)

    _log("Checking resume state...", "white")
    conn = _get_rethink_conn()
    if conn:
        _ensure_progress_table(conn)
    prog = _load_scraper_progress(conn) if conn else None
    resume_from = -1
    all_edges: list[EdgeRecord] = []
    seen: set[tuple[str, str]] = set()
    csv_file   = None
    csv_writer = None

    if (
        output_csv_path and conn and prog
        and prog.get("status") == "running"
        and prog.get("total_tickers") == total
    ):
        last_idx = int(prog.get("last_ticker_index", -1))
        if 0 <= last_idx < total - 1 and os.path.isfile(output_csv_path):
            try:
                with open(output_csv_path, "r", newline="", encoding="utf-8") as f:
                    rdr = csv.DictReader(f)
                    for row in rdr:
                        sup  = (row.get("sup")  or row.get("supplier_ticker") or "").strip().upper()
                        cust = (row.get("cust") or row.get("customer_ticker") or "").strip().upper()
                        if sup and cust and (sup, cust) not in seen:
                            seen.add((sup, cust))
                            rec: EdgeRecord = {
                                "sup": sup, "cust": cust,
                                "confidence": float(row.get("confidence") or 0.7),
                                "source": row.get("source") or "10-K",
                                "edge_type": row.get("edge_type") or "SUPPLIER_OF",
                                "last_confirmed": row.get("last_confirmed") or "",
                                "active_after": row.get("active_after") or row.get("last_confirmed") or "",
                            }
                            if row.get("revenue_pct"):
                                try:
                                    rec["revenue_pct"] = float(row["revenue_pct"])
                                except Exception:
                                    pass
                            all_edges.append(rec)
                resume_from = last_idx
                _log(f"Resuming from ticker {resume_from + 1}/{total} ({len(all_edges)} edges)", "cyan")
            except Exception as e:
                _log(f"Resume read CSV failed: {e}; starting fresh", "yellow")

    if resume_from < 0:
        if conn:
            _save_scraper_progress(conn, -1, total, 0, 0.0, "Running", "running")
        if output_csv_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_csv_path)) or ".", exist_ok=True)
            csv_file   = open(output_csv_path, "w", newline="", encoding="utf-8")
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(["sup", "cust", "confidence", "source", "revenue_pct", "edge_type", "last_confirmed", "active_after"])
        start_idx = 0
    else:
        if output_csv_path:
            csv_file   = open(output_csv_path, "a", newline="", encoding="utf-8")
            csv_writer = csv.writer(csv_file)
        start_idx = resume_from + 1

    # ticker_to_cik already merged with SEC fill-in above; ensure not None for workers
    ticker_to_cik = ticker_to_cik or {}
    cache_hits = downloads = 0
    parse_cache_hits = 0
    batch_for_callback: list[EdgeRecord] = []  # edges since last edges_callback

    # Parallel download+parse: each worker does one ticker (download then parse). Results consumed in order for resume/CSV.
    from concurrent.futures import ThreadPoolExecutor
    import threading
    _download_rate_lock = threading.Lock()
    num_workers = max(1, min(16, SEC_EDGAR_PARALLEL_WORKERS))

    def process_one_ticker(idx: int) -> tuple[int, str, bool, bool, bool, list]:
        """Download and parse one ticker. Returns (idx, ticker, from_cache, parse_cache_hit, path_ok, edges)."""
        ticker = tickers[idx]
        cik = ticker_to_cik.get(ticker)
        path, from_cache = _download_10k_for_ticker(ticker, download_dir, cik=cik, debug=False)
        if not from_cache and path:
            with _download_rate_lock:
                time.sleep(SEC_EDGAR_RATE_LIMIT_DELAY)
        if not path:
            return (idx, ticker, from_cache, False, False, [])
        cached = _load_parsed_edges(ticker)
        if cached is not None:
            return (idx, ticker, from_cache, True, True, cached)
        filing_date = _fetch_latest_10k_filing_date(cik, allow_live_lookup=False)
        parsed = _parse_10k_and_extract_relationships(
            ticker,
            path,
            title_to_ticker,
            ticker_to_title=ticker_to_title,
            filing_date=filing_date or None,
        )
        _save_parsed_edges(ticker, parsed)
        return (idx, ticker, from_cache, False, True, parsed)

    try:
        chunk_size = 50
        rows_since_flush = 0
        save_every = max(1, SEC_EDGAR_PROGRESS_SAVE_EVERY)
        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            for chunk_start in range(start_idx, total, chunk_size):
                chunk_end = min(chunk_start + chunk_size, total)

                if chunk_start == start_idx:
                    _log(f"Starting 10-K processing ({num_workers} parallel download+parse): 1/{total}...", "white")

                # Progress: show we're working on this chunk
                if conn:
                    pct_dl = (chunk_start / total * 100.0) if total else 0
                    _save_scraper_progress(conn, max(-1, chunk_start - 1), total, len(all_edges), round(pct_dl, 1),
                                       f"Downloading & parsing 10-Ks ({chunk_start + 1}–{chunk_end} of {total})...", "running")

                # Submit one task per ticker in chunk; up to num_workers run in parallel (download+parse each)
                futures = [pool.submit(process_one_ticker, i) for i in range(chunk_start, chunk_end)]
                # Consume results in index order so resume and CSV stay correct
                for i in range(chunk_start, chunk_end):
                    idx, ticker, from_cache, parse_cache_hit, path_ok, parsed = futures[i - chunk_start].result()
                    if from_cache and path_ok:
                        cache_hits += 1
                    elif path_ok:
                        downloads += 1
                    if parse_cache_hit:
                        parse_cache_hits += 1

                    new_this_ticker: list[EdgeRecord] = []
                    for rec in parsed:
                        key = (rec["sup"], rec["cust"])
                        if key not in seen:
                            seen.add(key)
                            all_edges.append(rec)
                            new_this_ticker.append(rec)
                            batch_for_callback.append(rec)

                    if csv_writer and new_this_ticker:
                        for rec in new_this_ticker:
                            csv_writer.writerow([
                                rec["sup"], rec["cust"],
                                rec.get("confidence", 0.7),
                                rec.get("source", "10-K"),
                                rec.get("revenue_pct", ""),
                                rec.get("edge_type", "SUPPLIER_OF"),
                                rec.get("last_confirmed", ""),
                                rec.get("active_after", rec.get("last_confirmed", "")),
                            ])
                        rows_since_flush += len(new_this_ticker)

                    if conn and (
                        ((i - start_idx + 1) % save_every == 0)
                        or i == chunk_end - 1
                        or i == total - 1
                    ):
                        pct = (i + 1) / total * 100.0
                        _save_scraper_progress(conn, i, total, len(all_edges), round(pct, 1),
                                               f"{i + 1}/{total} {ticker}", "running")

                    if csv_file and (
                        rows_since_flush >= save_every
                        or i == chunk_end - 1
                        or i == total - 1
                    ):
                        csv_file.flush()
                        rows_since_flush = 0

                # Progress + edge callback at end of each chunk
                _log(f"[{chunk_end}/{total}] {cache_hits} cached, {downloads} downloaded, {parse_cache_hits} parse-cached | edges: {len(all_edges)}", "cyan")
                if progress_callback:
                    try:
                        progress_callback()
                    except Exception:
                        pass
                if edges_callback and batch_for_callback:
                    try:
                        edges_callback(batch_for_callback)
                    except Exception:
                        pass
                    batch_for_callback = []

    except Exception as e:
        _log(f"Scraper error: {e}", "red")
        if conn:
            try:
                prog2 = _load_scraper_progress(conn)
                last  = int(prog2.get("last_ticker_index", -1)) if prog2 else -1
                _save_scraper_progress(conn, last, total, len(all_edges), 0.0, str(e)[:200], "failed")
            except Exception:
                pass
        raise
    finally:
        if csv_file:
            csv_file.close()

    # Flush last edge batch to callback (e.g. Neo4j) if any remain
    if edges_callback and batch_for_callback:
        try:
            edges_callback(batch_for_callback)
        except Exception:
            pass

    # Reconcile reciprocal supplier directions before the final CSV rewrite.
    all_edges = cross_validate_edges(all_edges)
    if edges_callback:
        final_edges = [
            rec for rec in all_edges
            if "+direction-resolved" in str(rec.get("source") or "")
            or "+cross-validated" in str(rec.get("source") or "")
        ]
        if final_edges:
            try:
                edges_callback(final_edges)
            except Exception:
                pass

    if conn:
        _save_scraper_progress(conn, total - 1, total, len(all_edges), 100.0,
                               f"Completed {len(all_edges)} edges", "completed")

    _log(f"10-K summary: {cache_hits} cached, {downloads} downloaded, {parse_cache_hits} parse-cached | {len(all_edges)} total edges.", "cyan")
    if output_csv_path:
        _write_edges_csv(output_csv_path, all_edges, append=False)
        _log(f"Wrote {len(all_edges)} edges to {output_csv_path}", "green")

    return all_edges


# ──────────────────────────────────────────────────────────────────────────────
# Incremental update (daily)
# ──────────────────────────────────────────────────────────────────────────────
def run_sec_edgar_incremental(
    hours: int = 24,
    output_csv_path: str | None = None,
    append_to_existing_csv: bool = False,
) -> list[EdgeRecord]:
    """
    Run SEC EDGAR scraper only for companies that filed 10-K/10-K/A in the last `hours`.
    Also scrapes 8-K Item 1.01 agreements filed in the same window.
    Returns new EdgeRecords. Optionally appends to output_csv_path.
    """
    filing_dates = get_recent_10k_filing_dates(hours=hours)
    tickers = sorted(filing_dates)
    if not tickers:
        _log(f"No recent 10-K/10-K/A filings in the last {hours}h; nothing to scrape.", "white")
        return []

    _log("Loading SEC company tickers for name resolution...", "cyan")
    ticker_to_title, title_to_ticker, _ = _fetch_sec_company_tickers()
    os.makedirs(SEC_EDGAR_FILINGS_DIR, exist_ok=True)

    all_edges: list[EdgeRecord] = []
    seen: set[tuple[str, str]] = set()

    # 10-K edges
    for i, ticker in enumerate(tickers):
        _log(f"Incremental {i + 1}/{len(tickers)}: {ticker}...", "white")
        path, from_cache = _download_10k_for_ticker(ticker, SEC_EDGAR_FILINGS_DIR)
        if not from_cache and path:
            time.sleep(SEC_EDGAR_RATE_LIMIT_DELAY)
        if not path:
            continue
        for rec in _parse_10k_and_extract_relationships(
            ticker,
            path,
            title_to_ticker,
            ticker_to_title=ticker_to_title,
            filing_date=filing_dates.get(ticker) or None,
        ):
            key = (rec["sup"], rec["cust"])
            if key not in seen:
                seen.add(key)
                all_edges.append(rec)

    # 8-K agreement edges
    eightk_edges = scrape_8k_agreements(tickers, title_to_ticker, hours=hours)
    for rec in eightk_edges:
        key = (rec["sup"], rec["cust"])
        if key not in seen:
            seen.add(key)
            all_edges.append(rec)

    all_edges = cross_validate_edges(all_edges)

    if output_csv_path and all_edges:
        _write_edges_csv(output_csv_path, all_edges, append=append_to_existing_csv)
        _log(f"Incremental: wrote {len(all_edges)} edges to {output_csv_path}", "green")

    return all_edges


# ──────────────────────────────────────────────────────────────────────────────
# Public helper used by graph_nexus_service
# ──────────────────────────────────────────────────────────────────────────────
def get_supply_chain_csv_from_sec_edgar(
    cache_dir: str,
    max_companies: int | None = None,
    tickers_from_neo4j: list[str] | None = None,
    ticker_to_cik: dict[str, str] | None = None,
    progress_callback: Callable[[], None] | None = None,
    edges_callback: Callable[[list], None] | None = None,
) -> str | None:
    """
    Run SEC EDGAR scraper and write result to cache CSV.
    Returns path to CSV or None.
    progress_callback: called every 50 companies (e.g. for ETA logging).
    edges_callback: called every 50 companies with new edges batch (e.g. for incremental Neo4j merge).
    """
    csv_path     = os.path.join(cache_dir, "supply_chain_sec_edgar.csv")
    filings_dir  = os.path.join(os.path.abspath(cache_dir), "sec_edgar_filings")
    _log(f"Using 10-K filings cache dir: {filings_dir}", "white")
    max_companies = max_companies or SEC_EDGAR_MAX_COMPANIES
    tickers = tickers_from_neo4j

    run_sec_edgar_supply_chain_scraper(
        tickers=tickers,
        output_csv_path=csv_path,
        max_companies=max_companies if tickers is None else None,
        ticker_to_cik=ticker_to_cik,
        filings_dir=filings_dir,
        progress_callback=progress_callback,
        edges_callback=edges_callback,
    )
    return csv_path if os.path.isfile(csv_path) else None


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    out = os.environ.get(
        "SEC_EDGAR_SUPPLY_CHAIN_OUTPUT_CSV",
        os.path.join(SEC_EDGAR_CACHE_DIR, "supply_chain_sec_edgar.csv"),
    )
    _log("Starting SEC EDGAR supply chain scraper...", "green")
    edges = run_sec_edgar_supply_chain_scraper(output_csv_path=out)
    _log(f"Done. Total edges: {len(edges)}", "green")
    sys.exit(0 if edges else 1)
