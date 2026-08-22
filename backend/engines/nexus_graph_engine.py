"""
Graph Nexus Service: builds the Neo4j company-relationship graph once.
- Sets up Neo4j (constraints, indexes); then runs Phases 1-11 if graph not already built (board interlocks phase removed).
- Phase 1:  Company universe from Polygon.io (US equities, global, or sectors).
- Phase 2:  SEC EDGAR (CIK, subsidiary/Exhibit 21), index/ETF composition.
- Phase 3:  Supply chain — 10-K customer/supplier disclosures (with confidence/source metadata).
- Phase 4:  Competitive — SIC/NAICS at sub-sector granularity.
- Phase 5:  Macro/commodity and BEA Input-Output sector exposure.
- Phase 6:  GLEIF LEI corporate hierarchy (PARENT_OF / SUBSIDIARY_OF).
- Phase 7:  SEC 13F institutional ownership (HELD_BY).
- Phase 8:  USASpending.gov government contracts (CONTRACTS_WITH).
- Phase 9:  Wikidata SPARQL ownership relationships (OWNS_STAKE_IN).
- Phase 10: PatentsView co-assigned patents (PATENT_PARTNER).
- Phase 11: 8-K material agreements (STRATEGIC_PARTNER).

Run manually or once at deploy. If graph already built, exits without work.
GRAPH_NEXUS_LIVE_UPDATE=true (or --live): after build, runs daily at GRAPH_NEXUS_UPDATE_TIME.
"""
from __future__ import annotations

import atexit
import hashlib
import html
import json
import os
import re
import sys
import threading
import time
import unicodedata
from collections import deque
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, TypedDict
from uuid import uuid4

from pydantic import BaseModel, Field

# Run from backend/engines/; backend is parent for cwd and imports (must be before any backend imports)
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)
os.chdir(_backend)

from db import pool as db_pool          # noqa: E402  (needs _backend on sys.path)
from db import schema as db_schema      # noqa: E402
from db import store                    # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_backend, ".env"))
    load_dotenv(os.path.join(os.path.dirname(_backend), ".env"))
except Exception:
    pass

try:
    from intellistock_logger import intellistock_logger
    def _log(msg: str, color: str = "white"):
        intellistock_logger.log(msg, color, service="GRAPH_NEXUS")
except Exception:
    def _log(msg: str, color: str = "white"):
        print(f"[graph-nexus-service] {msg}", flush=True)

_EXIT_ERROR_DELAY_SEC = 60


def _delay_then_exit(code: int = 1):
    _log(f"Exiting in {_EXIT_ERROR_DELAY_SEC}s so you can read the logs...", "yellow")
    time.sleep(_EXIT_ERROR_DELAY_SEC)
    sys.exit(code)


_progress_pct = [0.0]
# Early init of globals used by _progress_persist_to_rethinkdb (re-declared later with full context)
_nexus_rethink_conn = None
_nexus_current_phase_label: str = ""
_nexus_current_phase_num: int = 0
_nexus_current_phase_index: int = -1


def _progress(pct: float, msg: str, color: str = "cyan"):
    _progress_pct[0] = min(100.0, max(0.0, pct))
    _log(f"[{_progress_pct[0]:.0f}%] {msg}", color)
    # Also persist to RethinkDB so nexus status always shows current progress
    _progress_persist_to_rethinkdb(pct, msg)


_progress_last_rethinkdb_write = [0.0]  # monotonic timestamp of last RethinkDB write
_PROGRESS_RETHINKDB_MIN_INTERVAL = 10.0  # write at most every 10 seconds


def _progress_persist_to_rethinkdb(pct: float, msg: str):
    """Best-effort write of current progress to RethinkDB for nexus status visibility. Throttled to every 10s."""
    global _nexus_rethink_conn, _nexus_current_phase_label, _nexus_current_phase_num, _nexus_current_phase_index
    conn = _nexus_rethink_conn
    if conn is None:
        return
    now = time.monotonic()
    if now - _progress_last_rethinkdb_write[0] < _PROGRESS_RETHINKDB_MIN_INTERVAL:
        return
    _progress_last_rethinkdb_write[0] = now
    try:
        phase_label = _nexus_current_phase_label or msg
        phase_num = _nexus_current_phase_num or 0
        phase_index = _nexus_current_phase_index if _nexus_current_phase_index >= 0 else 0
        _save_graph_nexus_progress(
            conn,
            max(0, phase_num - 1),
            pct,
            msg,
            "running",
            current_phase_number=phase_num,
            current_phase_label=phase_label,
            stage_update={"stage_index": phase_index, "message": msg} if phase_index > 0 else None,
        )
    except Exception:
        pass  # never let RethinkDB errors break the build


def _log_stage_error(stage_name: str, substep: str, error_message: str, exc: BaseException | None = None, color: str = "red"):
    """Log an error with stage and substep context for console visibility."""
    msg = f"[Stage: {stage_name}] [Substep: {substep}] Error: {error_message}"
    if exc is not None and type(exc).__name__ != "Exception":
        msg += f" ({type(exc).__name__})"
    _log(msg, color)


def _log_stage_unexpected(stage_name: str, substep: str, message: str, color: str = "yellow"):
    """Log an unexpected outcome (no data, skipped, etc.) with stage and substep context."""
    _log(f"[Stage: {stage_name}] [Substep: {substep}] Unexpected: {message}", color)


# ──────────────────────────────────────────────────────────────────────────────
# Environment config
# ──────────────────────────────────────────────────────────────────────────────
NEO4J_URI        = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER       = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD   = os.environ.get("NEO4J_PASSWORD", "intellistock")
POLYGON_API_KEY  = os.environ.get("POLYGON_API_KEY", "").strip()
GRAPH_NEXUS_SCOPE         = os.environ.get("GRAPH_NEXUS_SCOPE", "us").strip().lower()
GRAPH_NEXUS_MAX_COMPANIES = int(os.environ.get("GRAPH_NEXUS_MAX_COMPANIES", "10000"))
GRAPH_NEXUS_LLM_PROVIDER  = os.environ.get("GRAPH_NEXUS_LLM_PROVIDER", "")
GRAPH_NEXUS_LLM_MODEL     = os.environ.get("GRAPH_NEXUS_LLM_MODEL", "")
GRAPH_NEXUS_LLM_API_KEY   = os.environ.get("GRAPH_NEXUS_LLM_API_KEY", "")
GRAPH_NEXUS_HIERARCHY_LLM_PROVIDER = (os.environ.get("GRAPH_NEXUS_HIERARCHY_LLM_PROVIDER", "") or "deepseek").strip()
GRAPH_NEXUS_HIERARCHY_LLM_MODEL = (os.environ.get("GRAPH_NEXUS_HIERARCHY_LLM_MODEL", "") or "deepseek-reasoner").strip()
GRAPH_NEXUS_HIERARCHY_LLM_API_KEY = os.environ.get("GRAPH_NEXUS_HIERARCHY_LLM_API_KEY", "").strip()
GOOGLE_SEARCH_API_KEY = os.environ.get("GOOGLE_SEARCH_API_KEY", "").strip()
GOOGLE_SEARCH_ENGINE_ID = os.environ.get("GOOGLE_SEARCH_ENGINE_ID", "").strip()
GRAPH_NEXUS_CONTROLS_REQUIRE_VALIDATION = os.environ.get(
    "GRAPH_NEXUS_CONTROLS_REQUIRE_VALIDATION", "true"
).strip().lower() in ("1", "true", "yes")
GRAPH_NEXUS_CONTROLS_VALIDATION_CACHE_MAX_AGE = int(
    os.environ.get("GRAPH_NEXUS_CONTROLS_VALIDATION_CACHE_MAX_AGE", str(30 * 86400))
)
GRAPH_NEXUS_CONTROLS_LLM_BATCH_SIZE = max(
    1,
    int(os.environ.get("GRAPH_NEXUS_CONTROLS_LLM_BATCH_SIZE", "8")),
)
GRAPH_NEXUS_WIKIDATA_PHASE_ENABLED = os.environ.get(
    "GRAPH_NEXUS_WIKIDATA_PHASE_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")
# PatentsView LLM fallback for name resolution (falls back to GRAPH_NEXUS_LLM_* if unset)
PATENTSVIEW_LLM_PROVIDER  = os.environ.get("PATENTSVIEW_LLM_PROVIDER", "").strip() or GRAPH_NEXUS_LLM_PROVIDER
PATENTSVIEW_LLM_MODEL     = os.environ.get("PATENTSVIEW_LLM_MODEL", "").strip() or GRAPH_NEXUS_LLM_MODEL
PATENTSVIEW_LLM_API_KEY   = os.environ.get("PATENTSVIEW_LLM_API_KEY", "").strip() or GRAPH_NEXUS_LLM_API_KEY
# Some Gemini models can take >120s on large JSON outputs; raise timeout for PatentsView LLM batches.
# Defaults to 300s, but respects LLM_REQUEST_TIMEOUT when set (unless PATENTSVIEW_LLM_TIMEOUT_SEC is set explicitly).
PATENTSVIEW_LLM_TIMEOUT_SEC = int(os.environ.get("PATENTSVIEW_LLM_TIMEOUT_SEC", os.environ.get("LLM_REQUEST_TIMEOUT", "300")))
PATENTSVIEW_LLM_RETRIES = int(os.environ.get("PATENTSVIEW_LLM_RETRIES", "3"))
PATENTSVIEW_LLM_BATCH_SIZE = int(os.environ.get("PATENTSVIEW_LLM_BATCH_SIZE", "20"))
PATENTSVIEW_LLM_MAX_WORKERS = int(os.environ.get("PATENTSVIEW_LLM_MAX_WORKERS", "6"))
PATENTSVIEW_LLM_HEARTBEAT_SEC = int(os.environ.get("PATENTSVIEW_LLM_HEARTBEAT_SEC", "30"))
PATENTSVIEW_LLM_LOG_SAMPLE_NAMES = os.environ.get("PATENTSVIEW_LLM_LOG_SAMPLE_NAMES", "false").strip().lower() in ("1", "true", "yes")
# Stop paginating a batch after this many consecutive pages with 0 co-assigned patents (0 = disabled)
PATENTSVIEW_ZERO_PAGE_EXIT = int(os.environ.get("PATENTSVIEW_ZERO_PAGE_EXIT", "5"))
POLYGON_CALLS_PER_MINUTE  = int(os.environ.get("POLYGON_CALLS_PER_MINUTE", "5"))

GRAPH_NEXUS_UPDATE_TIME         = os.environ.get("GRAPH_NEXUS_UPDATE_TIME", "02:00").strip()
GRAPH_NEXUS_LIVE_UPDATE_HOURS   = int(os.environ.get("GRAPH_NEXUS_LIVE_UPDATE_HOURS", "24"))
# Minimum confidence required to write a SUPPLIER_OF edge. Edges below this threshold are discarded.
# Set GRAPH_NEXUS_SUPPLY_CHAIN_MIN_CONFIDENCE=0.75 to be more permissive, or 0.9 to be stricter.
SUPPLY_CHAIN_MIN_CONFIDENCE     = float(os.environ.get("GRAPH_NEXUS_SUPPLY_CHAIN_MIN_CONFIDENCE", "0.85"))

# External data sources — all free
BEA_API_KEY           = os.environ.get("BEA_API_KEY", "").strip()           # free at apps.bea.gov/API/signup
GLEIF_MAX_COMPANIES   = int(os.environ.get("GLEIF_MAX_COMPANIES", "10000"))    # GLEIF API calls per run
GLEIF_MAX_REQUESTS_PER_MIN = max(1, int(os.environ.get("GLEIF_MAX_REQUESTS_PER_MIN", "60")))  # within GLEIF's observed safe ceiling; the parallel cache-warm keeps this saturated
GLEIF_COOLDOWN_EVERY  = int(os.environ.get("GLEIF_COOLDOWN_EVERY", "100"))    # pause every N companies to avoid 429
GLEIF_COOLDOWN_SECONDS = int(os.environ.get("GLEIF_COOLDOWN_SECONDS", "60"))  # 1 min pause when cooldown triggers
GLEIF_REQUEST_DELAY = float(os.environ.get("GLEIF_REQUEST_DELAY", "0.0"))     # 0 => the per-min rate cap is the sole limiter
# Phase 6 cache-warm concurrency. Per-company GLEIF I/O is disk-cache-backed (read-first),
# so warm those caches with N threads through the SAME thread-safe rate gate, then the
# existing sequential resolve loop runs from warm cache. Workers do ONLY cached network I/O
# and mutate nothing shared (no Neo4j writes off the main thread). Set 1 to disable.
GLEIF_PREFETCH_WORKERS = max(1, min(12, int(os.environ.get("GLEIF_PREFETCH_WORKERS", "6"))))
GLEIF_SEARCH_CACHE_VERSION = max(1, int(os.environ.get("GRAPH_NEXUS_GLEIF_SEARCH_CACHE_VERSION", "2")))
PHASE6_EX21_MAX_FILINGS_PER_COMPANY = max(1, int(os.environ.get("PHASE6_EX21_MAX_FILINGS_PER_COMPANY", "12")))
PHASE6_EX21_LOOKBACK_DAYS = max(365, int(os.environ.get("PHASE6_EX21_LOOKBACK_DAYS", "3650")))
PHASE6_EX21_PARSE_CACHE_VERSION = max(5, int(os.environ.get("PHASE6_EX21_PARSE_CACHE_VERSION", "5")))
_PHASE6_EX21_DEFAULT_WORKERS = min(8, max(2, (os.cpu_count() or 8) // 2))
PHASE6_EX21_PREFETCH_WORKERS = max(
    1,
    min(16, int(os.environ.get("PHASE6_EX21_PREFETCH_WORKERS", str(_PHASE6_EX21_DEFAULT_WORKERS)))),
)
PHASE6_EX21_MAX_IN_FLIGHT = max(
    PHASE6_EX21_PREFETCH_WORKERS,
    int(os.environ.get("PHASE6_EX21_MAX_IN_FLIGHT", str(PHASE6_EX21_PREFETCH_WORKERS * 4))),
)
PHASE6_EX21_PROGRESS_LOG_EVERY = max(1, int(os.environ.get("PHASE6_EX21_PROGRESS_LOG_EVERY", "50")))
PHASE6_EX21_PROGRESS_HEARTBEAT_SEC = max(
    5.0,
    float(os.environ.get("PHASE6_EX21_PROGRESS_HEARTBEAT_SEC", "10")),
)
PHASE6_EX21_DOCUMENT_FETCH_RETRIES = max(
    1,
    int(os.environ.get("PHASE6_EX21_DOCUMENT_FETCH_RETRIES", "2")),
)
PHASE12_ETF_FETCH_LIMIT = max(0, int(os.environ.get("PHASE12_ETF_FETCH_LIMIT", "0")))
PHASE12_ETF_LIST_CACHE_VERSION = max(3, int(os.environ.get("PHASE12_ETF_LIST_CACHE_VERSION", "3")))
GLEIF_SKIP_HEALTHY_COOLDOWN = os.environ.get(
    "GLEIF_SKIP_HEALTHY_COOLDOWN", "true"
).strip().lower() in ("1", "true", "yes")
USASPENDING_MAX_ROWS  = int(os.environ.get("USASPENDING_MAX_ROWS", "600000"))  # GLOBAL ceiling across ALL windows (cursor pagination bypasses 50k offset limit)
# Per-window cap. The fetch is split into N 90d windows; previously the GLOBAL cap was
# checked inside every window, so window 1 (the OLDEST quarter) saturated it and windows
# 2..N never ran — silently dropping the entire recent contract history. Distribute the
# budget instead: each window pulls up to this many awards (sorted by Award Amount desc,
# i.e. the most material contracts), so all windows run and 13*40k ~= prior total volume
# (so runtime is unchanged) while covering the full ~3.25y span. Env-tunable.
USASPENDING_MAX_ROWS_PER_WINDOW = max(
    1, int(os.environ.get("USASPENDING_MAX_ROWS_PER_WINDOW", "40000"))
)
# Per-request retry: more attempts + capped exponential backoff so transient 500s /
# RemoteDisconnected bursts don't silently drop a page of awards.
USASPENDING_REQUEST_MAX_ATTEMPTS = max(3, int(os.environ.get("USASPENDING_REQUEST_MAX_ATTEMPTS", "4")))
USASPENDING_REQUEST_BACKOFF_CAP_SEC = max(5, int(os.environ.get("USASPENDING_REQUEST_BACKOFF_CAP_SEC", "60")))
USASPENDING_BREAK_EVERY = int(os.environ.get("USASPENDING_BREAK_EVERY", "3500"))   # pause for cooldown every N rows to avoid rate limits
USASPENDING_BREAK_SECONDS = int(os.environ.get("USASPENDING_BREAK_SECONDS", "5"))  # 5s pause when cooldown triggers
USASPENDING_REQUEST_WINDOW_SECONDS = max(
    1,
    int(os.environ.get("USASPENDING_REQUEST_WINDOW_SECONDS", "300")),
)
USASPENDING_REQUESTS_PER_WINDOW = max(
    1,
    int(os.environ.get("USASPENDING_REQUESTS_PER_WINDOW", "1050")),
)
USASPENDING_FAILURE_BURST_THRESHOLD = max(
    1,
    int(os.environ.get("USASPENDING_FAILURE_BURST_THRESHOLD", "3")),
)
USASPENDING_FAILURE_BURST_COOLDOWN_SECONDS = max(
    5,
    int(os.environ.get("USASPENDING_FAILURE_BURST_COOLDOWN_SECONDS", "60")),
)
USASPENDING_FAILURE_BURST_WINDOW_SECONDS = max(
    30,
    int(os.environ.get("USASPENDING_FAILURE_BURST_WINDOW_SECONDS", "180")),
)
USASPENDING_FAILURE_BURST_MAX_COOLDOWN_SECONDS = max(
    USASPENDING_FAILURE_BURST_COOLDOWN_SECONDS,
    int(os.environ.get("USASPENDING_FAILURE_BURST_MAX_COOLDOWN_SECONDS", "180")),
)
USASPENDING_WINDOW_DAYS = max(
    14,
    int(os.environ.get("USASPENDING_WINDOW_DAYS", "90")),
)
USASPENDING_PAGE_SIZE = 100  # API max per request (limit cannot exceed 100)
GRAPH_EDGE_INTERVAL_SYNC_BATCH_SIZE = max(
    100,
    int(os.environ.get("GRAPH_EDGE_INTERVAL_SYNC_BATCH_SIZE", "2000")),
)
GRAPH_EDGE_INTERVAL_SYNC_MIN_BATCH_SIZE = max(
    100,
    min(
        GRAPH_EDGE_INTERVAL_SYNC_BATCH_SIZE,
        int(os.environ.get("GRAPH_EDGE_INTERVAL_SYNC_MIN_BATCH_SIZE", "250")),
    ),
)
# 13F quarterly ZIP is ~80MB; slow connections need a long timeout (and retry)
PHASE7_13F_ZIP_TIMEOUT_SEC = int(os.environ.get("PHASE7_13F_ZIP_TIMEOUT_SEC", "300"))  # 10 min default
PHASE7_13F_ZIP_MAX_RETRIES = 2  # retry on timeout (total attempts = 1 + MAX_RETRIES)
PHASE7_13F_MAX_HISTORY_QUARTERS = max(1, int(os.environ.get("PHASE7_13F_MAX_HISTORY_QUARTERS", "120")))
PHASE7_HOLDS_WRITE_BATCH_SIZE = max(100, int(os.environ.get("PHASE7_HOLDS_WRITE_BATCH_SIZE", "1000")))
PHASE7_CLOSE_INTERVAL_BATCH_SIZE = max(100, int(os.environ.get("PHASE7_CLOSE_INTERVAL_BATCH_SIZE", "500")))
PHASE7_HOLDS_INTERVAL_SYNC_BATCH_SIZE = max(100, int(os.environ.get("PHASE7_HOLDS_INTERVAL_SYNC_BATCH_SIZE", "10000")))
PHASE7_HOLDS_INTERVAL_SYNC_PROGRESS_EVERY = max(
    50000,
    int(os.environ.get("PHASE7_HOLDS_INTERVAL_SYNC_PROGRESS_EVERY", "250000")),
)
PHASE7_INFLATION_FIX_BATCH_SIZE = max(100, int(os.environ.get("PHASE7_INFLATION_FIX_BATCH_SIZE", "5000")))
PHASE7_INFLATION_FIX_MIN_BATCH_SIZE = max(
    100,
    min(
        PHASE7_INFLATION_FIX_BATCH_SIZE,
        int(os.environ.get("PHASE7_INFLATION_FIX_MIN_BATCH_SIZE", "500")),
    ),
)
PHASE7_13F_HISTORY_QUARTERS_DEFAULT = max(
    1,
    min(
        PHASE7_13F_MAX_HISTORY_QUARTERS,
        int(os.environ.get("PHASE7_13F_HISTORY_QUARTERS", "1")),
    ),
)
WIKIDATA_MAX_ROWS     = int(os.environ.get("WIKIDATA_MAX_ROWS", "800000"))   # total SPARQL result rows (paginated)
WIKIDATA_CHUNK_SIZE   = 10000   # per-request limit to avoid timeout (~10k per WDQS limits)
WIKIDATA_COOLDOWN_SECONDS = int(os.environ.get("WIKIDATA_COOLDOWN_SECONDS", "20"))  # pause between chunks to avoid 504
WIKIDATA_MAX_RETRIES  = int(os.environ.get("WIKIDATA_MAX_RETRIES", "3"))     # retries per chunk on 504/timeout
PATENTSVIEW_MAX_ROWS  = int(os.environ.get("PATENTSVIEW_MAX_ROWS", "100000")) # PatentsView total (paginated 1000/page via after)
PATENTSVIEW_PAGE_SIZE = 1000   # API max per request
PATENTSVIEW_API_KEY   = os.environ.get("PATENTSVIEW_API_KEY", "").strip()   # required for Phase 10; request at patentsview.org
# Endpoint is configurable so a future source swap needs no code change. The legacy
# host below was decommissioned by USPTO in 2026 (PatentsView -> data.uspto.gov ODP).
PATENTSVIEW_BASE_URL  = os.environ.get("PATENTSVIEW_BASE_URL", "https://search.patentsview.org/api/v1/patent/").strip()
# Phase 10 is DISABLED by default: the legacy PatentsView Search API was decommissioned
# (no live REST replacement yet). Set PATENTSVIEW_ENABLED=true once a working
# PATENTSVIEW_BASE_URL + key are wired. Disabled = clean skip, existing edges untouched.
PATENTSVIEW_ENABLED   = (os.environ.get("PATENTSVIEW_ENABLED", "false").strip().lower() not in ("0", "false", "no", "off", ""))

_POLYGON_CALL_TIMES: list[float] = []
_EDGE_DENYLIST_CACHE: dict[str, set[tuple[str, str]]] | None = None
_GLEIF_RATE_LOCK = threading.Lock()
_GLEIF_RATE_NEXT_REQUEST_AT = [0.0]
_GLEIF_HTTP_SESSION_LOCK = threading.Lock()
_GLEIF_HTTP_SESSION = [None]
_USASPENDING_HTTP_SESSION_LOCK = threading.Lock()
_USASPENDING_HTTP_SESSION = [None]
_PHASE7_HISTORY_QUARTERS = [PHASE7_13F_HISTORY_QUARTERS_DEFAULT]
_NEXUS_TEMPORAL_STATE = [{
    "historical_mode_enabled": False,
    "historical_start_date": None,
    "force_bootstrap_rebuild": False,
    "historical_bootstrap_complete": False,
    "historical_coverage_end": None,
    "historical_phase_manifests": {},
}]
_NEXUS_REQUESTED_PHASE_RANGE = [{"start": None, "end": None}]
_NEXUS_REQUESTED_PHASE_SELECTION = [None]


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
_ADR_TRAILING_RE = re.compile(
    r"\s+american\s+depositary(?:\s+shares?|\s+receipt)\b.*$",
    re.I,
)
_SECURITY_DESC_TRAILING_RE = re.compile(
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
_CANONICAL_SUFFIX_RE = re.compile(
    r"(?:\s+|\s*[,\.]\s*)(incorporated|corporation|company|limited|"
    r"inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|l\.l\.c\.?|plc\.?|"
    r"holdings?|group|enterprises?|partners?|lp|l\.p\.|sa|ag|nv|se|oyj|oy|"
    r"sab\s+de\s+cv|s\.a\.b\.\s+de\s+c\.v\.)\s*$",
    re.I,
)
_COMPANY_LISTING_TYPE_SKIP = {
    "ETF", "ETN", "UNIT", "WARRANT", "RIGHT", "PFD", "PREFERREDS", "BOND", "NOTE",
    "FUND", "SP", "ETS", "ETV",
}
_AMBIGUOUS_ENTITY_ALIASES = {
    "ring",
    "beats",
    "square",
    "meta",
    "atlas",
    "signal",
    "unity",
    "sage",
    "prime",
}
_GENERIC_ENTITY_ALIASES = {
    "group",
    "holdings",
    "capital",
    "partners",
    "brands",
    "ventures",
    "solutions",
    "technologies",
    "international",
}


def _is_security_instrument_name(raw_name: str) -> bool:
    """Return True when the listing name clearly describes a financing instrument, not an issuer."""
    name = (raw_name or "").strip()
    if not name:
        return False
    return bool(_NON_OPERATING_SECURITY_RE.search(name))


def _canonical_company_name(raw_name: str) -> str:
    """Strip market security descriptors so different listings map back to the same issuer name."""
    name = (raw_name or "").strip()
    if not name:
        return ""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    name = _COMMON_EQUITY_TRAILING_RE.sub("", name).strip()
    if _is_security_instrument_name(name):
        name = _ADR_TRAILING_RE.sub("", name).strip()
        name = _SECURITY_DESC_TRAILING_RE.sub("", name).strip()
    name = re.sub(r"\s+", " ", name).strip(" ,.-")
    return name or (raw_name or "").strip()


def _company_identity_key(raw_name: str) -> str:
    """Heavy-normalized issuer key used to dedupe share classes and financing instruments."""
    name = _canonical_company_name(raw_name).lower().strip()
    if not name:
        return ""
    name = re.sub(r"^the\s+", "", name)
    name = re.sub(r"[,\.&'/\-]+", " ", name)
    for _ in range(4):
        new = _CANONICAL_SUFFIX_RE.sub("", name).strip()
        if new == name:
            break
        name = new
    return re.sub(r"\s+", " ", name).strip()


def _is_supported_company_listing(name: str, ticker_type: str = "") -> bool:
    """Filter out obvious non-company market instruments before they contaminate the graph."""
    tt = (ticker_type or "").strip().upper()
    if tt in _COMPANY_LISTING_TYPE_SKIP:
        return False
    if _is_security_instrument_name(name):
        return False
    return True


def _cypher_invalid_company_listing_predicate(alias: str) -> str:
    alias = (alias or "").strip() or "c"
    skip = ", ".join(f"'{t}'" for t in sorted(_COMPANY_LISTING_TYPE_SKIP))
    return (
        f"coalesce({alias}.is_security_instrument, false) = true "
        f"OR coalesce({alias}.listing_type, 'CS') IN [{skip}]"
    )


def _cypher_valid_company_listing_predicate(alias: str) -> str:
    return f"NOT ({_cypher_invalid_company_listing_predicate(alias)})"


def _is_company_pair_safe(left: dict | None, right: dict | None) -> bool:
    """Return False for same-issuer/self or security-instrument pairs that should not form graph edges."""
    left = left or {}
    right = right or {}
    if left.get("ticker") and right.get("ticker") and left.get("ticker") == right.get("ticker"):
        return False
    if left.get("is_security_instrument") or right.get("is_security_instrument"):
        return False
    lk = (left.get("issuer_key") or "").strip()
    rk = (right.get("issuer_key") or "").strip()
    if lk and rk and lk == rk:
        return False
    return True


def _company_resolution_priority(rec: dict) -> tuple:
    """Pick one representative ticker per issuer for entity-resolution-heavy phases."""
    name = (rec.get("name") or "").strip()
    canonical = (rec.get("canonical_name") or "").strip()
    ticker = (rec.get("ticker") or "").strip()
    return (
        1 if rec.get("cik") else 0,
        1 if rec.get("lei") else 0,
        1 if name and canonical and name == canonical else 0,
        -len(name or canonical),
        ticker,
    )


def _dedupe_company_records(records: list[dict]) -> list[dict]:
    """Collapse multiple tickers/share classes for the same issuer to one representative record."""
    by_key: dict[str, dict] = {}
    for rec in records:
        key = (rec.get("issuer_key") or "").strip() or _company_identity_key(rec.get("name") or "")
        if not key:
            continue
        current = dict(rec)
        current["issuer_key"] = key
        prev = by_key.get(key)
        if prev is None or _company_resolution_priority(current) > _company_resolution_priority(prev):
            by_key[key] = current
    return list(by_key.values())


def _sanitize_company_records(records: list[dict]) -> tuple[list[dict], int]:
    """Normalize cached/fetched company records and drop unsupported listings."""
    cleaned: list[dict] = []
    seen_tickers: set[str] = set()
    dropped = 0
    for rec in records or []:
        ticker = (rec.get("ticker") or "").strip().upper()
        if not ticker or ticker in seen_tickers:
            dropped += 1
            continue
        name = (rec.get("name") or ticker).strip()[:200]
        listing_type = (rec.get("type") or rec.get("listing_type") or "").strip().upper()
        if not _is_supported_company_listing(name, listing_type):
            dropped += 1
            continue
        canonical_name = (rec.get("canonical_name") or _canonical_company_name(name) or name)[:200]
        issuer_key = (rec.get("issuer_key") or _company_identity_key(canonical_name) or _company_identity_key(name))[:120]
        current = dict(rec)
        current["ticker"] = ticker
        current["name"] = name
        current["type"] = listing_type or None
        current["listing_type"] = listing_type or None
        current["canonical_name"] = canonical_name
        current["issuer_key"] = issuer_key
        current["is_security_instrument"] = False
        cleaned.append(current)
        seen_tickers.add(ticker)
    return cleaned, dropped


def _load_edge_denylist() -> dict[str, set[tuple[str, str]]]:
    """Load per-relationship denylisted issuer pairs from disk once, with safe built-in defaults."""
    global _EDGE_DENYLIST_CACHE
    if _EDGE_DENYLIST_CACHE is not None:
        return _EDGE_DENYLIST_CACHE

    built_in = {
        "CONTROLS": {
            ("AEP", "PEG"),
            ("ENB", "D"),
            ("MCD", "ARCO"),
            ("MRK", "OGN"),
            ("NWG", "CFG"),
            ("NWL", "GGG"),
            ("PFE", "COTY"),
            ("PFE", "ZTS"),
            ("PNC", "BLK"),
            ("SPGI", "IHS"),
        },
        "SUPPLIER_OF": {
            ("BVS", "BRTX"),
            ("CCC", "LUD"),
            ("CELZ", "NEGG"),
            ("JAKK", "REYN"),
            ("OLED", "LUD"),
            ("STKL", "ROAD"),
        },
    }
    path = os.environ.get(
        "GRAPH_NEXUS_EDGE_DENYLIST_FILE",
        os.path.join(_backend, "data", "graph_edge_denylist.json"),
    ).strip()
    denylist = {rel: set(pairs) for rel, pairs in built_in.items()}
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                for rel, pairs in raw.items():
                    if not isinstance(rel, str) or not isinstance(pairs, list):
                        continue
                    bucket = denylist.setdefault(rel.strip().upper(), set())
                    for pair in pairs:
                        if isinstance(pair, list) and len(pair) == 2:
                            bucket.add(((pair[0] or "").strip().upper(), (pair[1] or "").strip().upper()))
                        elif isinstance(pair, str) and "|" in pair:
                            left, right = pair.split("|", 1)
                            bucket.add((left.strip().upper(), right.strip().upper()))
        except Exception as e:
            _log(f"Edge denylist load failed: {e}", "yellow")
    _EDGE_DENYLIST_CACHE = denylist
    return _EDGE_DENYLIST_CACHE


def _edge_pair_denied(rel: str, node_a: str, node_b: str) -> bool:
    rel_key = (rel or "").strip().upper()
    a = (node_a or "").strip().upper()
    b = (node_b or "").strip().upper()
    if not rel_key or not a or not b:
        return False
    denylist = _load_edge_denylist()
    bucket = denylist.get(rel_key) or set()
    return (a, b) in bucket or (b, a) in bucket


def _controls_validation_cache_path() -> str:
    return _nexus_cache_path("phase9", "controls_validation_cache.json")


def _load_controls_validation_cache() -> dict[str, dict]:
    raw = _nexus_read_cached_json(
        _nexus_cache_lookup_path("phase9", "controls_validation_cache.json", "phase10"),
        NEXUS_CACHE_MAX_AGE_HISTORIC,
    )
    return raw if isinstance(raw, dict) else {}


def _save_controls_validation_cache(cache: dict[str, dict]) -> None:
    try:
        _nexus_write_cached_json(_controls_validation_cache_path(), cache)
    except Exception as e:
        _log(f"Phase 9: Failed to persist controls validation cache: {e}", "yellow")


def _controls_validation_cache_key(
    parent_ticker: str,
    child_ticker: str,
    provider: str,
    model: str,
    grounded: bool,
) -> str:
    mode = "grounded" if grounded else "plain"
    return "|".join([
        (provider or "").strip().lower(),
        (model or "").strip(),
        mode,
        (parent_ticker or "").strip().upper(),
        (child_ticker or "").strip().upper(),
    ])


def _controls_validation_cache_entry_fresh(entry: dict | None) -> bool:
    if not isinstance(entry, dict):
        return False
    if GRAPH_NEXUS_CONTROLS_VALIDATION_CACHE_MAX_AGE <= 0:
        return True
    checked_at = (entry.get("checked_at") or "").strip()
    if not checked_at:
        return False
    try:
        ts = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    age_sec = (datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()
    return age_sec <= GRAPH_NEXUS_CONTROLS_VALIDATION_CACHE_MAX_AGE


_SEC_CONTROLS_TEXT_CACHE: dict[str, str] = {}
_SEC_CONTROLS_PAIR_CACHE: dict[tuple[str, str, str, str], bool] = {}


@lru_cache(maxsize=65536)
def _control_name_variants(raw_name: str) -> tuple[str, ...]:
    variants: list[str] = []
    for candidate in (
        raw_name,
        _canonical_company_name(raw_name),
        re.sub(
            r"\s*[,.]?\s*(incorporated|corporation|company|limited|inc\.?|corp\.?|co\.?|ltd\.?|llc\.?|plc\.?)\s*$",
            "",
            str(raw_name or "").strip(),
            flags=re.I,
        ).strip(),
    ):
        cleaned = re.sub(r"\s+", " ", str(candidate or "").strip())
        if cleaned and cleaned.lower() not in {v.lower() for v in variants}:
            variants.append(cleaned)
    return tuple(variants)


@lru_cache(maxsize=65536)
def _wikidata_heavy_norm(name: str) -> str:
    """Aggressively normalize a company name for Wikidata label resolution."""
    n = str(name or "").lower().strip()
    if not n:
        return ""
    n = re.sub(r"^the\s+", "", n)
    n = re.sub(r"\s+class\s+[a-z]\b.*$", "", n)
    n = re.sub(r"\s+common\s+stock\b.*$", "", n)
    n = re.sub(r"\s+american\s+depositary\b.*$", "", n)
    n = re.sub(r"\s+ordinary\s+shares?\b.*$", "", n)
    n = re.sub(r"\s+preferred\s+stock\b.*$", "", n)
    n = re.sub(r"\s+\d+\.?\d*%.*$", "", n)
    n = re.sub(r"\s+new\s*$", "", n)
    n = re.sub(r"[,\.&'/]+", " ", n)
    sfx = re.compile(
        r"\s*[,\.]?\s*(incorporated|corporation|company|limited|"
        r"inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|l\.l\.c\.?|plc\.?|"
        r"holdings?|group|enterprises?)\s*$",
        re.I,
    )
    for _ in range(4):
        new = sfx.sub("", n).strip()
        if new == n:
            break
        n = new
    return re.sub(r"\s+", " ", n).strip()


def _build_wikidata_company_resolution_maps(records: list[dict]) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
    """Build lookup maps for resolving Wikidata org labels to current graph tickers."""
    title_to_ticker: dict[str, str] = {}
    ticker_to_name: dict[str, str] = {}
    ticker_to_ticker: dict[str, str] = {}
    norm_to_ticker: dict[str, str] = {}
    for rec in _dedupe_company_records(records):
        ticker = str(rec.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        ticker_to_ticker[ticker] = ticker
        best_name = _company_record_best_name(rec)
        if best_name:
            ticker_to_name[ticker] = best_name
        for candidate in (
            rec.get("name"),
            rec.get("canonical_name"),
            rec.get("listing_name"),
            rec.get("lei_legal_name"),
            best_name,
        ):
            for variant in _control_name_variants(str(candidate or "")):
                key = variant.lower().strip()
                if key:
                    title_to_ticker[key] = ticker
                heavy = _wikidata_heavy_norm(variant)
                if heavy and len(heavy) >= 2:
                    norm_to_ticker[heavy] = ticker
    return title_to_ticker, ticker_to_ticker, norm_to_ticker, ticker_to_name


def _control_text_windows(text: str, company_name: str, radius: int = 260) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text or not company_name:
        return []
    text_lower = text.lower()
    windows: list[str] = []
    seen: set[str] = set()
    for variant in _control_name_variants(company_name):
        variant_lower = variant.lower()
        start = 0
        while True:
            idx = text_lower.find(variant_lower, start)
            if idx == -1:
                break
            window = text[max(0, idx - radius):min(len(text), idx + len(variant) + radius)]
            key = window.lower()
            if key not in seen:
                seen.add(key)
                windows.append(window)
            start = idx + len(variant_lower)
    return windows


def _parent_filing_supports_control(text: str, child_name: str) -> bool:
    patterns = (
        r"\bsubsidiar(?:y|ies)\b",
        r"\bmajority[- ]owned\b",
        r"\bcontrolling interest\b",
        r"\bwe own\b",
        r"\bour ownership\b",
        r"\bcontrol(?:s|led)?\b",
    )
    return any(
        any(re.search(pattern, window, re.I) for pattern in patterns)
        for window in _control_text_windows(text, child_name)
    )


def _child_filing_supports_control(text: str, parent_name: str) -> bool:
    patterns = (
        r"\bsubsidiary of\b",
        r"\bcontrolled by\b",
        r"\bowned by\b",
        r"\bmajority[- ]owned by\b",
        r"\bour parent\b",
        r"\bparent company\b",
        r"\bcontrolling shareholder\b",
    )
    return any(
        any(re.search(pattern, window, re.I) for pattern in patterns)
        for window in _control_text_windows(text, parent_name)
    )


def _latest_sec_annual_filing_text(cik: str) -> str:
    cik_pad = str(cik or "").strip().zfill(10)
    if not cik_pad or cik_pad == "0000000000":
        return ""
    cached = _SEC_CONTROLS_TEXT_CACHE.get(cik_pad)
    if cached is not None:
        return cached
    try:
        import sec_edgar_supply_chain as sec_mod
        forms = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
        filings = sec_mod._collect_submission_filing_records(
            cik_pad,
            form_types=forms,
            start_date=(datetime.now(timezone.utc).date() - timedelta(days=3650)).isoformat(),
            end_date=datetime.now(timezone.utc).date().isoformat(),
            allow_live_lookup=True,
        )
        if not filings:
            _SEC_CONTROLS_TEXT_CACHE[cik_pad] = ""
            return ""
        filing = filings[-1]
        path, _ = sec_mod._download_sec_filing_document(
            filing_url=filing["filing_url"],
            cache_subdir="controls_annual_filings",
            ticker=cik_pad,
            cik=cik_pad,
            accession_compact=filing["accession_compact"],
            primary_document=filing["primary_document"],
        )
        if not path:
            _SEC_CONTROLS_TEXT_CACHE[cik_pad] = ""
            return ""
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = sec_mod._html_to_plain_text_fast(handle.read(2_000_000))
        _SEC_CONTROLS_TEXT_CACHE[cik_pad] = text or ""
        return text or ""
    except Exception:
        _SEC_CONTROLS_TEXT_CACHE[cik_pad] = ""
        return ""


def _sec_filing_confirms_control_pair(
    parent_cik: str | None,
    parent_name: str,
    child_cik: str | None,
    child_name: str,
) -> bool:
    parent_cik = str(parent_cik or "").strip().zfill(10)
    child_cik = str(child_cik or "").strip().zfill(10)
    cache_key = (parent_cik, parent_name or "", child_cik, child_name or "")
    cached = _SEC_CONTROLS_PAIR_CACHE.get(cache_key)
    if cached is not None:
        return cached

    supported = False
    parent_text = _latest_sec_annual_filing_text(parent_cik)
    if parent_text and _parent_filing_supports_control(parent_text, child_name):
        supported = True
    if not supported:
        child_text = _latest_sec_annual_filing_text(child_cik)
        if child_text and _child_filing_supports_control(child_text, parent_name):
            supported = True
    _SEC_CONTROLS_PAIR_CACHE[cache_key] = supported
    return supported


def _company_record_best_name(record: dict | None) -> str:
    record = record or {}
    for candidate in (
        record.get("lei_legal_name"),
        record.get("canonical_name"),
        record.get("name"),
        record.get("listing_name"),
        record.get("ticker"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def _company_record_public_name(record: dict | None) -> str:
    record = record or {}
    for candidate in (
        record.get("canonical_name"),
        record.get("name"),
        record.get("listing_name"),
        record.get("ticker"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return _company_record_best_name(record)


def _company_records_have_sec_control_support(parent_record: dict | None, child_record: dict | None) -> bool:
    parent_record = parent_record or {}
    child_record = child_record or {}
    parent_cik = str(parent_record.get("cik") or "").strip()
    child_cik = str(child_record.get("cik") or "").strip()
    if not parent_cik or not child_cik:
        return False
    parent_name = _company_record_best_name(parent_record)
    child_name = _company_record_best_name(child_record)
    child_text = _latest_sec_annual_filing_text(child_cik)
    if child_text and _child_filing_supports_control(child_text, parent_name):
        return True
    parent_text = _latest_sec_annual_filing_text(parent_cik)
    if parent_text and child_text and _parent_filing_supports_control(parent_text, child_name):
        return _child_filing_supports_control(child_text, parent_name)
    return False


def _phase6_ex21_is_non_entity_boilerplate(lower_text: str) -> bool:
    lower_text = str(lower_text or "").strip()
    if not lower_text:
        return True
    if re.fullmatch(r"\([^)]*\)", lower_text):
        return True
    if lower_text.startswith("incorporated in ") or lower_text.startswith("organized in ") or lower_text.startswith("organized under "):
        return True
    if lower_text.startswith("name under which") or lower_text.startswith("subsidiary and name under which"):
        return True
    if "sec.gov/archives/edgar/data/" in lower_text:
        return True
    if re.match(
        r"^at\s+(?:december|january|february|march|april|may|june|july|august|september|october|november)\s+\d{1,2}(?:,\s*\d{4})?$",
        lower_text,
    ):
        return True
    if re.match(
        r"^date:\s*(?:december|january|february|march|april|may|june|july|august|september|october|november)\s+\d{1,2}(?:,\s*\d{4})?$",
        lower_text,
    ):
        return True
    if re.match(r"^listing\s+[a-z0-9]+$", lower_text):
        return True
    if re.match(r"^subsidiary\s+list$", lower_text):
        return True
    if lower_text.endswith(" subsidiary list") or lower_text.endswith(" subsidiaries list"):
        return True
    if re.match(r"^subsidiary\s+or\s+affiliate$", lower_text):
        return True
    if re.match(r"^(?:unconsolidated\s+)?subsidiary\s+of\b", lower_text):
        return True
    if re.match(r"^subsidiary\s+level\b", lower_text):
        return True
    if re.match(r"^subsidiary\s+does\s+business\b", lower_text):
        return True
    if re.match(r"^all\s+subsidiaries\b", lower_text):
        return True
    if re.match(r"^name under which the subsidiary does business\b", lower_text):
        return True
    if re.match(r"^each\s+a\s+[^.]{0,120}\bsubsidiary\s+of\b", lower_text):
        return True
    if re.match(r"^wholly[- ]owned\s+bank\s+subsidiary\s+of\b", lower_text):
        return True
    if re.match(r"^\d+(?:\.\d+)?%-owned\s+subsidiary\s+of\b", lower_text):
        return True
    if re.match(r"^(?:direct\s+and\s+)?indirect\s+subsidiaries\s+of\s*$", lower_text):
        return True
    if re.match(r"^.*\bindirect subsidiaries\b.*\band dba'?s\s*$", lower_text):
        return True
    if re.match(r"^of each subsidiary is$", lower_text):
        return True
    if re.match(r"^(?:subsidiaries?|list(?:ing)?|names?)\s*\([^)]+\)$", lower_text):
        return True
    if re.match(r"^(?:first|second|third|fourth|upper|lower|mid)\s+tier\s+subsidiar(?:y|ies)\b", lower_text):
        return True
    if re.match(r"^the following\s+[^.]{0,160}\bsubsidiar(?:y|ies)\b.*$", lower_text):
        return True
    if re.match(r"^otherwise indicated,\s*all subsidiaries are\b", lower_text):
        return True
    if lower_text.endswith(" subsidiaries") or lower_text.endswith(" subsidiary"):
        return True
    if re.search(r"\bas of\s+(?:december|january|february|march|april|may|june|july|august|september|october|november)\b", lower_text):
        return True
    if re.search(r"\bsubsidiaries,\s+at\s+(?:december|january|february|march|april|may|june|july|august|september|october|november)\b", lower_text):
        return True
    if re.search(r"would not (?:in the aggregate constitute|be a .{0,80}?significant subsidiary)", lower_text):
        return True
    if re.search(r"\bbank'?s subsidiaries are\b", lower_text):
        return True
    if re.search(r"^\d+\s+owned\s+\d+(?:\.\d+)?%?\s+by\b", lower_text):
        return True
    if re.match(
        r"^(?:december|january|february|march|april|may|june|july|august|september|october|november)\s+\d{1,2}(?:,\s*\d{4})?$",
        lower_text,
    ):
        return True
    if lower_text.startswith("ex-21") or lower_text.startswith("ex 21") or lower_text.startswith("exhibit 21") or "document exhibit" in lower_text:
        return True
    if re.search(r"\bowns\s+\d+(?:\.\d+)?%?\s+of\s+the\s+following\s+subsidiar(?:y|ies)\b", lower_text):
        return True
    if re.search(r"\bsole subsidiary\b", lower_text):
        return True
    if "affiliate pursuant to rule 1-02" in lower_text:
        return True
    if "registrant" in lower_text:
        return True
    boilerplate_fragments = (
        "asian subsidiaries",
        "and its subsidiaries",
        "collectively own",
        "description of the registrant",
        "domestic subsidiaries",
        "european subsidiaries",
        "exhibit 21.1 to form 10-k",
        "exhibits 21.1",
        "foreign subsidiaries",
        "has the following wholly owned subsidiaries",
        "inactive subsidiaries",
        "in addition, we also had the following subsidiaries",
        "international subsidiaries",
        "list of subsidiaries",
        "list of significant subsidiaries",
        "list of registrant",
        "listing of subsidiaries",
        "majority-owned subsidiary of",
        "majority-owned subsidiaries",
        "organized under the laws of",
        "partially own the following subsidiary",
        "partially own the following subsidiaries",
        "owns the following subsidiary",
        "owns the following subsidiaries",
        "ownership of such subsidiary is less than 100%",
        "parent and registrant",
        "schedule of subsidiaries",
        "subsidiaries and related entities",
        "subsidiaries that are 100% owned",
        "subsidiaries that are 50% owned",
        "subsidiaries included in",
        "subsidiaries of ",
        "subsidiaries of registrant",
        "subsidiary of third-party",
        "subsidiary companies",
        "subsidiary legal name",
        "a subsidiary of",
        "all companies are incorporated in",
        "all subsidiaries listed above were incorporated in",
        "and subsidiaries",
        "affiliated companies",
        "affiliated subsidiaries",
        "banking subsidiaries",
        "consolidated subsidiaries",
        "direct and indirect wholly owned subsidiaries",
        "direct and indirect wholly-owned subsidiaries",
        "50% and greater",
        "50% and less ownership",
        "has no subsidiaries",
        "indicates the number of subsidiaries levels",
        "individual accounts opened in the name of the owner",
        "name and surname of shareholder",
        "name and surname of shareholder representative",
        "name of csdp or broker",
        "name under which subsidiary does business",
        "name of organization",
        "name of the entity",
        "name of the organization",
        "name of the subsidiary",
        "name of subsidiaries",
        "our joint venture partner",
        "principal subsidiaries",
        "prior consent of the regulator",
        "public subsidiaries",
        "private subsidiaries",
        "shares are held in dematerialised format",
        "significant subsidiaries",
        "subsidiary listing",
        "subsidiaries are listed below",
        "subsidiaries or affiliates",
        "subsidiaries consolidated on a line-by-line basis",
        "subsidiaries valued at cost",
        "the delisting of",
        "this entity has filed a petition for reorganization",
        "conducts business under the assumed name",
        "not for profit organization",
        "aggregate interest carries 50% or more",
        "wholly owned subsidiaries were",
        "would not be a significant subsidiary",
        "would not in the aggregate constitute",
        "brackets indicate state or country of incorporation",
        "effective as of the petition date",
        "state of incorporation",
        "state of organization",
        "the registrant has the following subsidiaries",
        "the following is a list of",
        "listing of subsidiaries as of",
        "title of each class",
        "jurisdiction of incorporation",
        "jurisdiction of organization",
        "the state of incorporation or organization of each subsidiary",
        "u.s. subsidiaries",
        "us subsidiaries",
        "wholly owned subsidiaries of",
        "wholly owned subsidiary of",
        "wholly-owned subsidiary of",
    )
    return any(fragment in lower_text for fragment in boilerplate_fragments)


_PHASE8_USASPENDING_RESOLUTION_PRIORITY = {
    "company_exact": 3,
    "legal_entity_ancestor": 2,
    "company_prefix": 1,
}


def _phase8_usaspending_normalize_name(raw_name: str) -> str:
    return _company_identity_key(raw_name)


def _phase8_usaspending_distinct_strings(values, *, limit: int = 5) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values or []:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
        if len(out) >= max(1, int(limit or 1)):
            break
    return out


def _phase8_apply_recipient_audit_metadata(properties: dict) -> None:
    recipient_names = _phase8_usaspending_distinct_strings(properties.get("_recipient_names") or [], limit=25)
    recipient_legal_names = _phase8_usaspending_distinct_strings(
        properties.get("_recipient_legal_entity_names") or [],
        limit=25,
    )
    recipient_legal_keys = _phase8_usaspending_distinct_strings(
        properties.get("_recipient_legal_entity_keys") or [],
        limit=25,
    )
    resolution_modes = _phase8_usaspending_distinct_strings(properties.get("_recipient_resolution_modes") or [], limit=25)

    properties["_recipient_names"] = recipient_names
    properties["_recipient_legal_entity_names"] = recipient_legal_names
    properties["_recipient_legal_entity_keys"] = recipient_legal_keys
    properties["_recipient_resolution_modes"] = resolution_modes

    properties["recipient_name_count"] = len(recipient_names)
    properties["recipient_name_examples"] = recipient_names[:5]
    # Keep a stable primary recipient name even when a Company/GovAgency edge
    # aggregates multiple recipient variants across awards. The examples/count
    # still preserve ambiguity for audit purposes.
    properties["recipient_name"] = recipient_names[0] if recipient_names else ""

    properties["recipient_legal_entity_name_count"] = len(recipient_legal_names)
    properties["recipient_legal_entity_name_examples"] = recipient_legal_names[:5]
    properties["recipient_legal_entity_name"] = (
        recipient_legal_names[0] if len(recipient_legal_names) == 1 else ""
    )

    if not resolution_modes:
        properties["recipient_resolution_mode"] = ""
    elif len(resolution_modes) == 1:
        properties["recipient_resolution_mode"] = resolution_modes[0]
    else:
        properties["recipient_resolution_mode"] = "mixed"

    if properties["recipient_resolution_mode"] == "legal_entity_ancestor" and len(recipient_legal_keys) == 1:
        properties["recipient_resolution_source"] = "legal_entity_alias"
        properties["recipient_legal_entity_key"] = recipient_legal_keys[0]
        if not str(properties.get("recipient_listed_ancestor_ticker") or "").strip():
            properties["recipient_listed_ancestor_ticker"] = str(properties.get("target_ticker") or "").strip()
    else:
        properties["recipient_resolution_source"] = ""
        properties["recipient_legal_entity_key"] = ""
        if properties["recipient_resolution_mode"] != "legal_entity_ancestor":
            properties["recipient_listed_ancestor_ticker"] = ""

    if properties["recipient_resolution_mode"] != "legal_entity_ancestor":
        properties["recipient_legal_entity_name"] = ""


def _phase8_record_recipient_audit_metadata(
    properties: dict,
    *,
    recipient_name: str = "",
    recipient_legal_entity_name: str = "",
    recipient_legal_entity_key: str = "",
    resolution_mode: str = "",
) -> None:
    recipient = str(recipient_name or "").strip()
    recipient_legal_name = str(recipient_legal_entity_name or "").strip()
    recipient_legal_key = str(recipient_legal_entity_key or "").strip()
    mode = str(resolution_mode or "").strip()
    if recipient:
        properties.setdefault("_recipient_names", []).append(recipient)
    if recipient_legal_name:
        properties.setdefault("_recipient_legal_entity_names", []).append(recipient_legal_name)
    if recipient_legal_key:
        properties.setdefault("_recipient_legal_entity_keys", []).append(recipient_legal_key)
    if mode:
        properties.setdefault("_recipient_resolution_modes", []).append(mode)
    _phase8_apply_recipient_audit_metadata(properties)


def _phase8_merge_resolved_usaspending_edge(existing: EdgeResult, incoming: EdgeResult) -> None:
    props = existing.setdefault("properties", {})
    incoming_props = incoming.get("properties") or {}
    existing["confidence"] = max(float(existing.get("confidence") or 0.0), float(incoming.get("confidence") or 0.0))
    props["total_obligation"] = float(props.get("total_obligation") or 0.0) + float(incoming_props.get("total_obligation") or 0.0)
    props["award_count"] = int(props.get("award_count") or 0) + int(incoming_props.get("award_count") or 0)

    current_active_after = _normalize_edge_date(props.get("active_after"))
    incoming_active_after = _normalize_edge_date(incoming_props.get("active_after"))
    if incoming_active_after and (not current_active_after or incoming_active_after < current_active_after):
        props["active_after"] = incoming_active_after

    current_last_confirmed = _normalize_edge_date(props.get("last_confirmed"))
    incoming_last_confirmed = _normalize_edge_date(incoming_props.get("last_confirmed"))
    if incoming_last_confirmed and (not current_last_confirmed or incoming_last_confirmed > current_last_confirmed):
        props["last_confirmed"] = incoming_last_confirmed

    current_valid_until = _normalize_edge_date(props.get("valid_until"))
    incoming_valid_until = _normalize_edge_date(incoming_props.get("valid_until"))
    incoming_is_open = str(incoming_props.get("edge_state") or "").strip().lower() == "open"
    current_is_open = str(props.get("edge_state") or "").strip().lower() == "open"
    props["edge_state"] = "open" if current_is_open or incoming_is_open else "closed"
    if props["edge_state"] == "open":
        props["valid_until"] = ""
        props["retired_reason"] = None
    elif incoming_valid_until and (not current_valid_until or incoming_valid_until > current_valid_until):
        props["valid_until"] = incoming_valid_until

    props["agency_name"] = str(incoming_props.get("agency_name") or props.get("agency_name") or "").strip()
    if str(incoming_props.get("recipient_listed_ancestor_ticker") or "").strip():
        props["recipient_listed_ancestor_ticker"] = str(incoming_props.get("recipient_listed_ancestor_ticker") or "").strip()

    _phase8_record_recipient_audit_metadata(
        props,
        recipient_name=str(incoming_props.get("recipient_name") or "").strip(),
        recipient_legal_entity_name=str(incoming_props.get("recipient_legal_entity_name") or "").strip(),
        recipient_legal_entity_key=str(incoming_props.get("recipient_legal_entity_key") or "").strip(),
        resolution_mode=str(incoming_props.get("recipient_resolution_mode") or "").strip(),
    )


def _phase8_usaspending_edge_to_write_param(edge: EdgeResult, today_iso: str) -> dict:
    props = dict(edge.get("properties") or {})
    raw_recipient_name = str(
        props.get("recipient_name")
        or props.get("recipient_display_name")
        or props.get("recipient_legal_entity_name")
        or ""
    ).strip()
    raw_legal_name = str(props.get("recipient_legal_entity_name") or "").strip()
    raw_legal_key = str(props.get("recipient_legal_entity_key") or "").strip()
    raw_resolution_mode = str(props.get("recipient_resolution_mode") or "").strip()
    if raw_recipient_name and not props.get("_recipient_names") and not props.get("recipient_name_examples"):
        props["_recipient_names"] = [raw_recipient_name]
    elif not props.get("_recipient_names") and props.get("recipient_name_examples"):
        props["_recipient_names"] = _phase8_usaspending_distinct_strings(props.get("recipient_name_examples") or [], limit=25)
    if raw_legal_name and not props.get("_recipient_legal_entity_names") and not props.get("recipient_legal_entity_name_examples"):
        props["_recipient_legal_entity_names"] = [raw_legal_name]
    elif not props.get("_recipient_legal_entity_names") and props.get("recipient_legal_entity_name_examples"):
        props["_recipient_legal_entity_names"] = _phase8_usaspending_distinct_strings(
            props.get("recipient_legal_entity_name_examples") or [],
            limit=25,
        )
    if raw_legal_key and not props.get("_recipient_legal_entity_keys"):
        props["_recipient_legal_entity_keys"] = [raw_legal_key]
    if raw_resolution_mode and not props.get("_recipient_resolution_modes"):
        props["_recipient_resolution_modes"] = [raw_resolution_mode]
    _phase8_apply_recipient_audit_metadata(props)
    recipient_name = str(
        props.get("recipient_name")
        or raw_recipient_name
        or props.get("recipient_legal_entity_name")
        or ""
    ).strip()
    return {
        "ticker": edge["node_a"],
        "agency_id": edge["node_b"],
        "agency_name": str(props.get("agency_name") or edge["node_b"] or "").strip(),
        "obligation": props.get("total_obligation", 0),
        "award_count": int(props.get("award_count") or 0),
        "active_after": _normalize_edge_date(props.get("active_after")),
        "last_confirmed": _normalize_edge_date(props.get("last_confirmed")),
        "valid_until": _normalize_edge_date(props.get("valid_until")),
        "edge_state": str(props.get("edge_state") or "open"),
        "recipient_name": recipient_name,
        "recipient_name_examples": _phase8_usaspending_distinct_strings(props.get("recipient_name_examples") or []),
        "recipient_name_count": int(props.get("recipient_name_count") or 0),
        "recipient_resolution_mode": str(props.get("recipient_resolution_mode") or "").strip(),
        "recipient_resolution_source": str(props.get("recipient_resolution_source") or "").strip(),
        "recipient_listed_ancestor_ticker": str(props.get("recipient_listed_ancestor_ticker") or "").strip(),
        "recipient_legal_entity_key": str(props.get("recipient_legal_entity_key") or "").strip(),
        "recipient_legal_entity_name": str(props.get("recipient_legal_entity_name") or "").strip(),
        "recipient_legal_entity_name_examples": _phase8_usaspending_distinct_strings(
            props.get("recipient_legal_entity_name_examples") or []
        ),
        "recipient_legal_entity_name_count": int(props.get("recipient_legal_entity_name_count") or 0),
        "today": today_iso,
    }


def _phase8_usaspending_bridge_alias_is_safe(alias: str) -> bool:
    text = re.sub(r"\s+", " ", str(alias or "").strip())
    if not text:
        return False
    normalized = _normalize_entity_alias(text)
    if not normalized or normalized in _AMBIGUOUS_ENTITY_ALIASES:
        return False
    if len(normalized.split()) < 2:
        return False
    return _phase6_ex21_alias_is_entity_like(text)


def _phase8_load_usaspending_legal_entity_bridge(session) -> dict[str, dict]:
    if session is None:
        return {}
    try:
        rows = session.run(
            """
            MATCH (le:LegalEntity)
            WHERE NOT le:Company
              AND size(coalesce(le.aliases, [])) > 0
              AND size(coalesce(le.listed_ancestor_tickers, [])) = 1
              AND coalesce(le.alias_requires_parent_confirmation, false) = false
              AND coalesce(le.alias_risk, 'low') = 'low'
            RETURN le.entity_key AS entity_key,
                   coalesce(le.display_name, le.legal_name, le.name, le.entity_key) AS display_name,
                   coalesce(le.aliases, []) AS aliases,
                   coalesce(le.normalized_aliases, []) AS normalized_aliases,
                   coalesce(le.listed_ancestor_tickers, []) AS ancestor_tickers
            """
        )
    except Exception as exc:
        _log(f"Phase 8: LegalEntity bridge index load error: {exc}", "yellow")
        return {}

    alias_groups: dict[str, list[dict]] = {}
    seen_alias_candidates: set[tuple[str, str, str]] = set()
    for row in rows:
        ancestors = [
            str(item or "").strip().upper()
            for item in (row.get("ancestor_tickers") or [])
            if str(item or "").strip()
        ]
        if len(ancestors) != 1:
            continue
        ancestor_ticker = ancestors[0]
        entity_key = str(row.get("entity_key") or "").strip()
        display_name = str(row.get("display_name") or "").strip()
        raw_aliases = [
            str(alias or "").strip()
            for alias in (row.get("aliases") or [])
            if str(alias or "").strip()
        ]
        normalized_aliases = [
            str(alias or "").strip()
            for alias in (row.get("normalized_aliases") or [])
            if str(alias or "").strip()
        ]
        for alias in raw_aliases + normalized_aliases:
            if not _phase8_usaspending_bridge_alias_is_safe(alias):
                continue
            normalized = _phase8_usaspending_normalize_name(alias)
            if not normalized:
                continue
            dedupe_key = (normalized, entity_key, ancestor_ticker)
            if dedupe_key in seen_alias_candidates:
                continue
            seen_alias_candidates.add(dedupe_key)
            alias_groups.setdefault(normalized, []).append(
                {
                    "ancestor_ticker": ancestor_ticker,
                    "entity_key": entity_key,
                    "display_name": display_name or entity_key,
                }
            )

    index: dict[str, dict] = {}
    for normalized_alias, candidates in alias_groups.items():
        ancestor_set = {
            str(candidate.get("ancestor_ticker") or "").strip().upper()
            for candidate in candidates
            if str(candidate.get("ancestor_ticker") or "").strip()
        }
        if len(ancestor_set) != 1:
            continue
        entity_keys = {
            str(candidate.get("entity_key") or "").strip()
            for candidate in candidates
            if str(candidate.get("entity_key") or "").strip()
        }
        entry = {
            "ancestor_ticker": next(iter(ancestor_set)),
            "match_count": len(candidates),
        }
        if len(entity_keys) == 1:
            only = candidates[0]
            entry["entity_key"] = str(only.get("entity_key") or "").strip()
            entry["display_name"] = str(only.get("display_name") or "").strip()
        index[normalized_alias] = entry
    return index


def _partition_controls_edges_by_confirmation(
    edges: list[EdgeResult],
    confirmed_pairs,
) -> tuple[list[EdgeResult], list[EdgeResult]]:
    """Mark cross-confirmed ownership edges and return (validated, ambiguous)."""
    validated: list[EdgeResult] = []
    ambiguous: list[EdgeResult] = []
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for edge in edges:
        key = ((edge.get("node_a") or "").upper(), (edge.get("node_b") or "").upper())
        validation_mode = None
        if isinstance(confirmed_pairs, dict):
            validation_mode = confirmed_pairs.get(key)
        elif key in confirmed_pairs:
            validation_mode = "gleif_parent"
        if validation_mode:
            updated = dict(edge)
            props = dict(updated.get("properties") or {})
            props["validation_mode"] = validation_mode
            props["validated_at"] = checked_at
            updated["properties"] = props
            validated.append(updated)
        else:
            ambiguous.append(edge)
    return validated, ambiguous


def _fetch_confirmed_controls_pairs(
    session,
    edges: list[EdgeResult],
    ticker_to_name: dict[str, str] | None = None,
) -> dict[tuple[str, str], str]:
    """Return CONTROLS pairs already supported by authoritative graph or SEC annual filings."""
    pairs = []
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        parent = (edge.get("node_a") or "").strip().upper()
        child = (edge.get("node_b") or "").strip().upper()
        if not parent or not child or parent == child:
            continue
        key = (parent, child)
        if key in seen:
            continue
        seen.add(key)
        pairs.append({"parent": parent, "child": child})
    if not pairs:
        return {}
    confirmed: dict[tuple[str, str], str] = {}
    batch_size = 500
    for start in range(0, len(pairs), batch_size):
        batch = pairs[start:start + batch_size]
        rows = session.run("""
            UNWIND $pairs AS p
            MATCH (parent:Company {ticker: p.parent})-[:PARENT_OF]->(child:Company {ticker: p.child})
            RETURN p.parent AS parent, p.child AS child
        """, pairs=batch)
        for row in rows:
            parent = (row.get("parent") or "").strip().upper()
            child = (row.get("child") or "").strip().upper()
            if parent and child:
                confirmed[(parent, child)] = "gleif_parent"
    if not ticker_to_name:
        return confirmed

    unresolved = [pair for pair in pairs if (pair["parent"], pair["child"]) not in confirmed]
    if not unresolved:
        return confirmed
    for start in range(0, len(unresolved), batch_size):
        batch = unresolved[start:start + batch_size]
        rows = session.run("""
            UNWIND $pairs AS p
            MATCH (parent:Company {ticker: p.parent})
            MATCH (child:Company {ticker: p.child})
            RETURN p.parent AS parent, p.child AS child,
                   parent.cik AS parent_cik, child.cik AS child_cik,
                   parent.name AS parent_name, child.name AS child_name
        """, pairs=batch)
        for row in rows:
            parent = (row.get("parent") or "").strip().upper()
            child = (row.get("child") or "").strip().upper()
            if not parent or not child:
                continue
            if _sec_filing_confirms_control_pair(
                row.get("parent_cik"),
                row.get("parent_name") or ticker_to_name.get(parent, parent),
                row.get("child_cik"),
                row.get("child_name") or ticker_to_name.get(child, child),
            ):
                confirmed[(parent, child)] = "sec_annual_filing"
    return confirmed


def _polygon_rate_limit(context: str | None = None):
    if POLYGON_CALLS_PER_MINUTE >= 60:
        return
    now = time.monotonic()
    _POLYGON_CALL_TIMES[:] = [t for t in _POLYGON_CALL_TIMES if now - t < 60.0]
    if len(_POLYGON_CALL_TIMES) >= POLYGON_CALLS_PER_MINUTE:
        wait = 60.0 - (now - _POLYGON_CALL_TIMES[0])
        if wait > 0:
            if wait >= 1.0:
                prefix = f"{context} — " if context else ""
                _log(f"{prefix}Polygon rate limit: waiting {wait:.1f}s...", "yellow")
            time.sleep(wait)
        _POLYGON_CALL_TIMES.pop(0)
    _POLYGON_CALL_TIMES.append(time.monotonic())


# ──────────────────────────────────────────────────────────────────────────────
# EdgeResult: standardised structure for all new edge providers
# ──────────────────────────────────────────────────────────────────────────────
class EdgeResult(TypedDict, total=False):
    node_a: str         # Ticker / ID of the "from" node
    rel:    str         # Relationship type label (e.g. "PARENT_OF", "HELD_BY")
    node_b: str         # Ticker / ID of the "to" node
    confidence: float   # 0.0–1.0
    source: str         # Human-readable source
    properties: dict    # Additional edge properties (revenue_pct, pct_ownership, etc.)


class _HierarchyValidationVerdict(BaseModel):
    keep: bool = False
    best_parent: str = ""
    best_entity_kind: str = "legal_entity"
    confidence_bucket: str = "low"
    reason: str = ""
    evidence_urls: list[str] = Field(default_factory=list)


class _AliasDisambiguationVerdict(BaseModel):
    keep: bool = False
    best_ticker: str = ""
    best_entity_kind: str = "legal_entity"
    confidence_bucket: str = "low"
    reason: str = ""
    evidence_urls: list[str] = Field(default_factory=list)


class _SearchEvidenceSelection(BaseModel):
    use_search: bool = False
    queries: list[str] = Field(default_factory=list)
    reason: str = ""
    selected_urls: list[str] = Field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# RethinkDB progress
# ──────────────────────────────────────────────────────────────────────────────
RETHINKDB_HOST             = os.environ.get("RETHINKDB_HOST", "localhost")
RETHINKDB_PORT             = int(os.environ.get("RETHINKDB_PORT", "28015"))
GRAPH_NEXUS_PROGRESS_DB    = os.environ.get("RETHINKDB_DB", "IntelliStock")
GRAPH_NEXUS_PROGRESS_TABLE = "GraphNexusProgress"
PROGRESS_ID_GRAPH_NEXUS    = "graph_nexus"
STOCKS_TABLE               = "Stocks"
# Control: single EngineControl table, one doc per engine
NEXUS_CONTROL_TABLE        = "EngineControl"
NEXUS_CONTROL_ID           = "nexus_graph_engine"


def _store_now() -> str:
    """R24 replacement for ``r.now()``.

    ReQL wrote a native TIME and the driver handed it back as a tz-aware
    datetime; every consumer of these documents (interactive_utils'
    stage-doc normaliser, the API, the Flutter Nexus card) immediately turns
    that into an ISO-8601 string. Writing the string is therefore identical
    at the consumer, and unlike a datetime it is JSON-serialisable for jsonb.
    """
    return datetime.now(timezone.utc).isoformat()


class _StoreHandle:
    """Stand-in for the old RethinkDB connection object.

    ``store`` takes its own pooled connection per operation (R26), so there is
    nothing left to hold open -- but every progress/control helper below is
    gated on ``if conn:`` and several call ``conn.close()``. Dropping the
    parameter would silently switch all of those guards on for good, so the
    handle stays: truthy while the database answers, ``close()`` a no-op.
    """

    def close(self, *args, **kwargs):
        return None


_STORE_REACHABLE = [False]     # True once this process has talked to Postgres


def _get_rethink_conn():
    try:
        if not db_pool.health().get("ok"):
            raise RuntimeError("Postgres pool is not answering")
        _STORE_REACHABLE[0] = True
        return _StoreHandle()
    except Exception as e:
        # Drop the pool: psycopg_pool otherwise keeps a background thread
        # retrying a dead server for 30s, where r.connect() failed at once.
        try:
            db_pool.close_pool()
        except Exception:
            pass
        _log(f"Postgres progress unavailable: {e}", "yellow")
        return None


def _ensure_progress_table(conn):
    try:
        db_schema.ensure_table(GRAPH_NEXUS_PROGRESS_TABLE)
    except Exception as e:
        _log_stage_error("Postgres", "ensure progress table", str(e), e, color="yellow")


def _ensure_stocks_table(conn):
    if not conn:
        return
    try:
        db_schema.ensure_table(STOCKS_TABLE)
    except Exception as e:
        _log(f"Ensure Stocks table: {e}", "yellow")


def _get_cached_stock(conn, ticker: str) -> dict | None:
    if not conn or not ticker:
        return None
    try:
        doc = store.get(STOCKS_TABLE, ticker.strip().upper())
        return doc if doc else None
    except Exception:
        return None


def _upsert_stock_cache(conn, doc: dict):
    if not conn or not doc or not doc.get("id"):
        return
    try:
        store.insert(STOCKS_TABLE, doc, conflict="replace")
    except Exception as e:
        _log(f"Stocks cache upsert: {e}", "yellow")


def _nexus_control_want_stop(conn) -> bool:
    if not conn:
        return False
    try:
        # A missing row raises AttributeError here, exactly as it did on the
        # ReQL path, and the except returns False: no doc means "keep going".
        doc = store.get(NEXUS_CONTROL_TABLE, NEXUS_CONTROL_ID)
        return not bool(doc.get("running", False))
    except Exception:
        return False


def _clear_nexus_control_on_exit():
    # Gated on _STORE_REACHABLE rather than re-probing: a dead server costs
    # the pool 3 x PG_RECONNECT_TIMEOUT before it gives up, where the old
    # r.connect() refused instantly. A process that never reached Postgres
    # has no control doc of its own to clear anyway.
    if not _STORE_REACHABLE[0]:
        return
    try:
        store.update(NEXUS_CONTROL_TABLE, NEXUS_CONTROL_ID, {"running": False})
    except Exception:
        pass


atexit.register(_clear_nexus_control_on_exit)


def _load_graph_nexus_progress(conn) -> dict | None:
    try:
        doc = store.get(GRAPH_NEXUS_PROGRESS_TABLE, PROGRESS_ID_GRAPH_NEXUS)
        return doc if doc else None
    except Exception:
        return None


def _save_graph_nexus_progress(
    conn,
    last_completed_phase: int,
    progress_pct: float,
    message: str,
    status: str,
    *,
    eta_remaining_sec: float | None = None,
    current_phase_number: int | None = None,
    current_phase_label: str | None = None,
    stage_update: dict | None = None,
):
    """Persist build progress to RethinkDB for CLI/API/Discord. ETA and phase label shown in nexus status.
    If stage_update is provided, merge it into doc['stages'] (by stage_index). When stage_update is None we merge
    into the existing doc so we never wipe the 'stages' array (e.g. periodic ETA save must preserve stages)."""
    try:
        import datetime
        now_iso = datetime.datetime.utcnow().isoformat() + "Z"
        updates = {
            "id": PROGRESS_ID_GRAPH_NEXUS,
            "last_completed_phase": last_completed_phase,
            "progress_pct": progress_pct,
            "message": message,
            "last_updated": _store_now(),
            "status": status,
        }
        if eta_remaining_sec is not None and eta_remaining_sec >= 0:
            updates["eta_remaining_sec"] = eta_remaining_sec
        if current_phase_number is not None:
            updates["current_phase_number"] = current_phase_number
        if current_phase_label:
            updates["current_phase_label"] = current_phase_label

        existing = _load_graph_nexus_progress(conn)
        stages = list(existing.get("stages") or []) if existing else []
        stages = [dict(s) for s in stages] if stages else []

        if stage_update is not None:
            idx = stage_update.get("stage_index")
            if idx is None:
                idx = -1
            total_substeps = NEXUS_STAGE_SUBSTEPS[idx] if 0 <= idx < len(NEXUS_STAGE_SUBSTEPS) else 1
            incoming_status = str(stage_update.get("status") or "").strip().lower()
            if incoming_status == "running":
                for other in stages:
                    other_idx = other.get("stage_index")
                    if other_idx == idx:
                        continue
                    other_status = str(other.get("status") or "").strip().lower()
                    if other_status != "running":
                        continue
                    if isinstance(other_idx, int) and other_idx < idx:
                        other["status"] = "completed"
                        other_total_substeps = NEXUS_STAGE_SUBSTEPS[other_idx] if 0 <= other_idx < len(NEXUS_STAGE_SUBSTEPS) else other.get("total_substeps") or 1
                        other["total_substeps"] = other_total_substeps
                        other["substeps_completed"] = other_total_substeps
                        other.setdefault("completed_at", now_iso)
                    else:
                        other["status"] = "stopped"
                        other.setdefault("message", "Stopped")
            entry = None
            for s in stages:
                if s.get("stage_index") == idx:
                    entry = s
                    break
            if entry is None:
                entry = {"stage_index": idx, "key": PHASE_KEYS[idx] if idx < len(PHASE_KEYS) else "", "label": "", "progress_pct": PHASE_PCT[idx] if idx < len(PHASE_PCT) else 0, "status": "pending"}
                for i, key, label, pct in NEXUS_STAGES:
                    if i == idx:
                        entry["key"] = key
                        entry["label"] = label
                        entry["progress_pct"] = pct
                        break
                stages.append(entry)
            for k, v in stage_update.items():
                if k != "stage_index" and v is not None:
                    entry[k] = v
            entry["total_substeps"] = total_substeps
            st = incoming_status
            if st == "running":
                entry["substeps_completed"] = stage_update.get("substeps_completed", 0)
            elif st in ("completed", "skipped", "failed"):
                entry["substeps_completed"] = stage_update.get("substeps_completed", total_substeps)
            if "started_at" not in entry and st == "running":
                entry["started_at"] = now_iso
            if "completed_at" not in entry and st in ("completed", "skipped", "failed"):
                entry["completed_at"] = now_iso

        doc = dict(existing) if existing else {}
        doc.update(updates)
        doc["stages"] = stages
        store.insert(GRAPH_NEXUS_PROGRESS_TABLE, doc, conflict="replace")
    except Exception as e:
        _log_stage_error("Postgres", "save progress", str(e), e, color="yellow")


# Phase 6 (corporate hierarchy) stage_index for live progress updates
PHASE6_STAGE_INDEX = 8
PHASE6B_STAGE_INDEX = 9


def _phase6_report_progress(conn, current: int, total: int, stage_message: str) -> None:
    """Update RethinkDB progress for Phase 6 GLEIF corporate hierarchy processing."""
    if conn is None or total <= 0:
        return
    try:
        progress_pct = PHASE_PCT[7] + (current / total) * (PHASE_PCT[8] - PHASE_PCT[7])
        pct_str = round(100.0 * current / total, 1)
        phase_label = f"Phase 6: GLEIF hierarchy — {pct_str}%"
        _save_graph_nexus_progress(
            conn,
            6,
            progress_pct,
            phase_label,
            "running",
            current_phase_number=7,
            current_phase_label=phase_label,
            stage_update={"stage_index": PHASE6_STAGE_INDEX, "message": stage_message},
        )
    except Exception:
        pass


def _phase6b_report_progress(conn, current: int, total: int, stage_message: str) -> None:
    """Update RethinkDB progress for Phase 6B SEC EX-21 hierarchy processing."""
    if conn is None or total <= 0:
        return
    try:
        progress_pct = PHASE_PCT[8] + (current / total) * (PHASE_PCT[9] - PHASE_PCT[8])
        pct_str = round(100.0 * current / total, 1)
        phase_label = f"Phase 6B: SEC EX-21 hierarchy — {pct_str}%"
        _save_graph_nexus_progress(
            conn,
            7,
            progress_pct,
            phase_label,
            "running",
            current_phase_number=8,
            current_phase_label=phase_label,
            stage_update={"stage_index": PHASE6B_STAGE_INDEX, "message": stage_message},
        )
    except Exception:
        pass


# Phase 8 (USASpending) stage_index for live progress updates
PHASE9_STAGE_INDEX = 11


def _phase9_report_progress(conn, current: int, total: int, stage_message: str, progress_pct_override: float | None = None) -> None:
    """Update RethinkDB progress for Phase 8 USASpending (fetch rows or edges written). total=0 for indeterminate."""
    if conn is None:
        return
    try:
        if total and total > 0:
            pct = current / total
            progress_pct = PHASE_PCT[10] + pct * (PHASE_PCT[11] - PHASE_PCT[10])
            pct_str = round(100.0 * pct, 1)
            phase_label = f"Phase 8: USASpending — {pct_str}% ({current}/{total})"
        else:
            progress_pct = progress_pct_override if progress_pct_override is not None else PHASE_PCT[10]
            phase_label = f"Phase 8: USASpending — {stage_message}"
        _save_graph_nexus_progress(
            conn,
            9,  # last_completed_phase (through phase 7)
            progress_pct,
            phase_label,
            "running",
            current_phase_number=10,
            current_phase_label=phase_label,
            stage_update={"stage_index": PHASE9_STAGE_INDEX, "message": stage_message},
        )
    except Exception:
        pass


# Phase 9 (Wikidata) stage_index for live progress updates
PHASE10_STAGE_INDEX = 12


def _phase10_report_progress(conn, current: int, total: int, stage_message: str, progress_pct_override: float | None = None) -> None:
    """Update RethinkDB progress for Phase 9 Wikidata. total=0 for indeterminate (e.g. fetching)."""
    if conn is None:
        return
    try:
        if total and total > 0:
            pct = current / total
            progress_pct = PHASE_PCT[11] + pct * (PHASE_PCT[12] - PHASE_PCT[11])
            pct_str = round(100.0 * pct, 1)
            phase_label = f"Phase 9: Wikidata — {pct_str}% ({current}/{total})"
        else:
            progress_pct = progress_pct_override if progress_pct_override is not None else PHASE_PCT[11]
            phase_label = f"Phase 9: Wikidata — {stage_message}"
        _save_graph_nexus_progress(
            conn,
            10,  # last_completed_phase (through phase 8)
            progress_pct,
            phase_label,
            "running",
            current_phase_number=11,
            current_phase_label=phase_label,
            stage_update={"stage_index": PHASE10_STAGE_INDEX, "message": stage_message},
        )
    except Exception:
        pass


def _nexus_report_stage_substep(conn, stage_index: int, substeps_completed: int) -> None:
    """Update only one stage's substeps_completed (for progress bar). Keeps rest of doc unchanged."""
    if conn is None or stage_index < 0 or stage_index >= len(NEXUS_STAGE_SUBSTEPS):
        return
    try:
        existing = _load_graph_nexus_progress(conn)
        if not existing:
            return
        stages = [dict(s) for s in (existing.get("stages") or [])]
        for s in stages:
            if s.get("stage_index") == stage_index:
                s["substeps_completed"] = max(0, min(substeps_completed, NEXUS_STAGE_SUBSTEPS[stage_index]))
                break
        doc = dict(existing)
        doc["stages"] = stages
        doc["last_updated"] = _store_now()
        store.insert(GRAPH_NEXUS_PROGRESS_TABLE, doc, conflict="replace")
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────────────
# SIC → sector mapping (range-based, accurate)
# ──────────────────────────────────────────────────────────────────────────────
# Ordered list of (lo, hi, sector) tuples. First match wins.
# Source: SEC SIC code list + GICS alignment.
_SIC_SECTOR_RANGES: list[tuple[int, int, str]] = [
    # Agriculture
    (100, 999, "Agriculture"),
    # Mining & Energy extraction
    (1000, 1299, "Materials"),      # Metal mining, coal
    (1300, 1499, "Energy"),         # Oil & gas extraction
    # Construction
    (1500, 1799, "Industrials"),
    # Manufacturing — non-durables
    (2000, 2099, "Consumer Staples"),   # Food
    (2100, 2199, "Consumer Staples"),   # Tobacco
    (2200, 2399, "Consumer Discretionary"),  # Textiles, Apparel
    (2400, 2499, "Materials"),          # Lumber/Wood
    (2500, 2599, "Consumer Discretionary"),  # Furniture
    (2600, 2699, "Materials"),          # Paper
    (2700, 2799, "Communication Services"),  # Printing/Publishing
    (2800, 2829, "Materials"),          # Industrial chemicals
    (2830, 2836, "Healthcare"),         # Pharmaceuticals
    (2837, 2899, "Materials"),          # Misc chemicals
    (2900, 2999, "Energy"),             # Petroleum refining
    # Manufacturing — durables
    (3000, 3099, "Materials"),          # Rubber/Plastics
    (3100, 3199, "Consumer Discretionary"),  # Leather
    (3200, 3299, "Materials"),          # Stone/Glass/Concrete
    (3300, 3399, "Materials"),          # Primary metals
    (3400, 3499, "Industrials"),        # Fabricated metals
    (3500, 3559, "Industrials"),        # Industrial/Commercial machinery
    (3560, 3569, "Industrials"),        # General industry machinery
    (3570, 3579, "Technology"),         # Computer hardware
    (3580, 3599, "Industrials"),        # Industrial machinery NEC
    (3600, 3621, "Industrials"),        # Electrical industry apparatus
    (3622, 3669, "Technology"),         # Electronic components & equipment
    (3670, 3679, "Technology"),         # Electronic components
    (3680, 3699, "Technology"),         # Electronic equipment NEC
    (3700, 3716, "Consumer Discretionary"),  # Motor vehicles
    (3717, 3799, "Industrials"),        # Transportation equipment
    (3800, 3826, "Healthcare"),         # Medical instruments
    (3827, 3899, "Technology"),         # Optical/scientific instruments
    (3900, 3999, "Industrials"),        # Misc manufacturing
    # Transportation & Utilities
    (4000, 4099, "Industrials"),        # Railroads
    (4100, 4499, "Industrials"),        # Transit/trucking/water transport
    (4500, 4599, "Industrials"),        # Air transportation
    (4600, 4699, "Energy"),             # Pipelines
    (4700, 4799, "Industrials"),        # Transportation services
    (4800, 4899, "Communication Services"),  # Communications
    (4900, 4999, "Utilities"),          # Electric/Gas/Water utilities
    # Trade
    (5000, 5099, "Industrials"),        # Wholesale durable goods
    (5100, 5199, "Consumer Staples"),   # Wholesale non-durable goods
    (5200, 5999, "Consumer Discretionary"),  # Retail
    # Finance, Insurance, Real Estate
    (6000, 6199, "Financials"),         # Banking & credit
    (6200, 6299, "Financials"),         # Security dealers/brokers
    (6300, 6499, "Financials"),         # Insurance
    (6500, 6552, "Real Estate"),        # Real estate
    (6553, 6799, "Financials"),         # Holding/investment companies
    # Services
    (7000, 7099, "Consumer Discretionary"),  # Hotels/lodging
    (7100, 7299, "Consumer Discretionary"),  # Personal services
    (7300, 7369, "Industrials"),        # Business services
    (7370, 7379, "Technology"),         # Computer programming/data processing
    (7380, 7399, "Industrials"),        # Misc business services
    (7400, 7999, "Consumer Discretionary"),  # Entertainment/recreation
    # Health, Education, Professional
    (8000, 8099, "Healthcare"),         # Health services
    (8100, 8199, "Industrials"),        # Legal services
    (8200, 8299, "Consumer Discretionary"),  # Educational services
    (8300, 8399, "Consumer Discretionary"),  # Social services
    (8700, 8742, "Technology"),         # Engineering/management consulting
    (8743, 8999, "Industrials"),        # Misc professional services
    # Government
    (9000, 9999, "Government"),
]

# All distinct sector names used by SIC mapping + Other. Ensures Neo4j always has these Sector nodes.
_ALL_SECTOR_NAMES = sorted(set(s for _, _, s in _SIC_SECTOR_RANGES) | {"Other"})


def _sic_to_sector(sic_code: str | None) -> str:
    """Map a 4-digit SIC code to a GICS-aligned sector label using range lookup."""
    if not sic_code:
        return "Other"
    try:
        code = int(str(sic_code).strip())
    except Exception:
        return "Other"
    for lo, hi, sector in _SIC_SECTOR_RANGES:
        if lo <= code <= hi:
            return sector
    return "Other"


_SECTOR_NAME_ALIASES = {
    "agriculture": "Agriculture",
    "communication services": "Communication Services",
    "communications": "Communication Services",
    "consumer cyclical": "Consumer Discretionary",
    "consumer cyclicals": "Consumer Discretionary",
    "consumer discretionary": "Consumer Discretionary",
    "consumer defensive": "Consumer Staples",
    "consumer staples": "Consumer Staples",
    "energy": "Energy",
    "financial": "Financials",
    "financial services": "Financials",
    "financials": "Financials",
    "government": "Government",
    "health care": "Healthcare",
    "healthcare": "Healthcare",
    "industrials": "Industrials",
    "information technology": "Technology",
    "materials": "Materials",
    "real estate": "Real Estate",
    "technology": "Technology",
    "utilities": "Utilities",
}
_INVALID_SEC_OWNER_ORG_SECTORS = frozenset({
    "corp fin",
    "corporation finance",
    "division of corporation finance",
    "trading and markets",
    "division of trading and markets",
    "investment management",
    "division of investment management",
    "enforcement",
    "division of enforcement",
    "economic and risk analysis",
    "division of economic and risk analysis",
})


def _normalize_sector_name(raw_sector: str | None, sic_code: str | None = None) -> str:
    """Normalize source-specific sector strings into the canonical Nexus sector set."""
    sic_sector = _sic_to_sector(sic_code)
    text = str(raw_sector or "").strip()
    if not text:
        return sic_sector if sic_sector != "Other" else "Other"
    if text in _ALL_SECTOR_NAMES:
        return text
    key = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    if not key:
        return sic_sector if sic_sector != "Other" else "Other"
    if key in _INVALID_SEC_OWNER_ORG_SECTORS:
        return sic_sector if sic_sector != "Other" else "Other"
    exact = _SECTOR_NAME_ALIASES.get(key)
    if exact:
        return exact
    for alias, sector in _SECTOR_NAME_ALIASES.items():
        if alias in key:
            return sector
    return sic_sector if sic_sector != "Other" else "Other"


# Fine-grained SIC → industry_group for COMPETES_WITH sub-sector matching
# Expanded to cover more meaningful peer groupings.
SIC_INDUSTRY_MAP = {
    # Technology — Semiconductors & Components (split from broad "Electronic Components")
    ("3674", "3674"): "Semiconductors",                # AMD, NVDA, MU, INTC, SNDK, MRVL, QCOM
    ("3672", "3672"): "Printed Circuit Boards",
    ("3670", "3671"): "Electronic Components Mfg",
    ("3675", "3679"): "Electronic Connectors & Passives",
    ("3661", "3661"): "Telecom Equipment",             # CIEN, NOK
    ("3669", "3669"): "Optical & Networking Equipment", # LITE-adjacent
    ("3660", "3660"): "Communications Equipment — General",
    ("3662", "3668"): "Communications Equipment — Other",
    ("3680", "3689"): "Electronic Instruments",
    ("3690", "3699"): "Electronic Equipment — Other",
    ("3827", "3829"): "Optical & Scientific Instruments",  # LITE
    ("3812", "3812"): "Defense Electronics",
    ("3825", "3826"): "Test & Measurement Instruments",
    # Technology — Software & Hardware
    ("7370", "7379"): "Software & IT Services",
    ("3570", "3579"): "Computer Hardware",
    ("3577", "3577"): "Computer Peripherals",
    # Healthcare — split Pharma from Biotech
    ("2830", "2833"): "Pharmaceuticals — General",
    ("2834", "2834"): "Pharmaceutical Preparations",     # Large pharma
    ("2835", "2835"): "Diagnostic Testing",
    ("2836", "2836"): "Biotech & Biologics",             # ARWR, MRNA
    ("8000", "8099"): "Health Services",
    ("3841", "3845"): "Medical Devices",
    # Materials & Chemicals — split for ALB-type specialty chemicals
    ("2800", "2819"): "Industrial Chemicals",            # ALB (Lithium/Specialty Chemicals)
    ("2820", "2829"): "Plastics & Synthetics",
    ("2860", "2869"): "Industrial Organics",
    ("3300", "3399"): "Primary Metals",
    ("1000", "1099"): "Metal Mining",
    ("1040", "1049"): "Gold Mining",                     # NGD, GOLD
    # Energy
    ("1311", "1311"): "Crude Petroleum & Natural Gas",
    ("1381", "1382"): "Oil & Gas Drilling Services",
    ("2911", "2911"): "Petroleum Refining",
    ("4600", "4699"): "Pipelines",
    # Financials
    ("6020", "6029"): "Commercial Banking",
    ("6035", "6036"): "Savings & Loan",
    ("6200", "6299"): "Securities & Investment",
    ("6310", "6399"): "Insurance",
    ("6500", "6552"): "Real Estate",
    # Industrials
    ("3500", "3559"): "Industrial Machinery",
    ("3720", "3729"): "Aircraft & Parts",
    ("3760", "3769"): "Guided Missiles & Space",
    ("4000", "4099"): "Railroads",
    ("4500", "4599"): "Air Transportation",
    # Consumer
    ("5000", "5099"): "Wholesale — Durable Goods",
    ("5200", "5399"): "Retail — General",
    ("5900", "5999"): "Retail — Specialty",
    ("2000", "2099"): "Food Manufacturing",
    ("5810", "5812"): "Restaurants",
    ("7000", "7099"): "Hotels & Lodging",
    # Communication & Media
    ("4810", "4899"): "Telecommunications",
    ("2700", "2799"): "Publishing & Media",
    ("4833", "4833"): "Television Broadcasting",
    ("7810", "7819"): "Entertainment & Media",
}


def _sic_to_industry_group(sic_code: str | None) -> str | None:
    """Map SIC code to a fine-grained industry group string, or None."""
    if not sic_code:
        return None
    try:
        code = int(sic_code.strip())
    except Exception:
        return None
    for (lo, hi), label in SIC_INDUSTRY_MAP.items():
        if int(lo) <= code <= int(hi):
            return label
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Neo4j helpers
# ──────────────────────────────────────────────────────────────────────────────
_NEO4J_WAIT_SEC          = 30
_NEO4J_RETRY_INTERVAL_SEC = 5


def get_driver():
    try:
        from neo4j import GraphDatabase
        return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    except Exception as e:
        _log(f"Neo4j driver error: {e}", "red")
        return None


def wait_for_neo4j():
    _log(f"Waiting for Neo4j to be ready (up to {_NEO4J_WAIT_SEC}s)...", "cyan")
    driver = None
    max_attempts = max(1, _NEO4J_WAIT_SEC // _NEO4J_RETRY_INTERVAL_SEC)
    for attempt in range(max_attempts):
        if attempt > 0:
            time.sleep(_NEO4J_RETRY_INTERVAL_SEC)
        driver = get_driver()
        quiet  = (attempt < max_attempts - 1)
        if driver and verify_neo4j_connection(driver, quiet=quiet):
            _log("Neo4j connected.", "green")
            return driver, True
        if driver:
            try:
                driver.close()
            except Exception:
                pass
            driver = None
        if attempt < max_attempts - 1:
            _log(f"Neo4j not ready, retrying in {_NEO4J_RETRY_INTERVAL_SEC}s...", "yellow")
    return None, False


def verify_neo4j_connection(driver, quiet: bool = False) -> bool:
    if not driver:
        return False
    try:
        with driver.session() as session:
            session.run("RETURN 1").consume()
        return True
    except Exception as e:
        if not quiet:
            err = str(e).lower()
            if "authentication" in err or "auth" in err:
                _log_stage_error("Init Neo4j", "authentication", "Neo4j authentication failed.", None)
            else:
                _log_stage_error("Init Neo4j", "connection check", str(e), e)
        return False


def ensure_neo4j_init(driver):
    """Create constraints and indexes for all node types used across all phases."""
    with driver.session() as session:
        try:
            session.run("DROP CONSTRAINT company_name_unique IF EXISTS")
        except Exception:
            pass

        constraints = [
            "CREATE CONSTRAINT company_ticker_unique  IF NOT EXISTS FOR (c:Company)    REQUIRE c.ticker IS UNIQUE",
            "CREATE CONSTRAINT legal_entity_key_unique IF NOT EXISTS FOR (l:LegalEntity) REQUIRE l.entity_key IS UNIQUE",
            "CREATE CONSTRAINT sector_name_unique     IF NOT EXISTS FOR (s:Sector)     REQUIRE s.name IS UNIQUE",
            "CREATE CONSTRAINT theme_name_unique      IF NOT EXISTS FOR (t:Theme)      REQUIRE t.name IS UNIQUE",
            "CREATE CONSTRAINT index_symbol_unique    IF NOT EXISTS FOR (i:Index)       REQUIRE i.symbol IS UNIQUE",
            "CREATE CONSTRAINT graph_build_status_uid IF NOT EXISTS FOR (g:GraphBuildStatus) REQUIRE g.id IS UNIQUE",
            "CREATE CONSTRAINT institution_id_unique  IF NOT EXISTS FOR (i:Institution) REQUIRE i.id IS UNIQUE",
            "CREATE CONSTRAINT lei_entity_unique      IF NOT EXISTS FOR (l:LEIEntity)   REQUIRE l.lei IS UNIQUE",
            "CREATE CONSTRAINT gov_agency_unique      IF NOT EXISTS FOR (a:GovAgency)   REQUIRE a.id IS UNIQUE",
            "CREATE CONSTRAINT commodity_unique       IF NOT EXISTS FOR (m:Commodity)   REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT graph_edge_interval_uid IF NOT EXISTS FOR (h:GraphEdgeInterval) REQUIRE h.history_id IS UNIQUE",
        ]
        for c in constraints:
            try:
                session.run(c)
            except Exception as e:
                if "EquivalentSchemaRuleAlreadyExists" not in str(e) and "already exists" not in str(e).lower():
                    _log_stage_error("Init Neo4j", "create constraint", str(e), e, color="yellow")

        indexes = [
            "CREATE INDEX company_sector_idx   IF NOT EXISTS FOR (c:Company) ON (c.sector)",
            "CREATE INDEX company_industry_idx IF NOT EXISTS FOR (c:Company) ON (c.industry)",
            "CREATE INDEX company_cik_idx      IF NOT EXISTS FOR (c:Company) ON (c.cik)",
            "CREATE INDEX company_sic_idx      IF NOT EXISTS FOR (c:Company) ON (c.sic_code)",
            "CREATE INDEX company_lei_idx      IF NOT EXISTS FOR (c:Company) ON (c.lei)",
            "CREATE INDEX company_name_idx     IF NOT EXISTS FOR (c:Company) ON (c.name)",
            "CREATE INDEX legal_entity_lei_idx IF NOT EXISTS FOR (l:LegalEntity) ON (l.lei)",
            "CREATE INDEX legal_entity_name_idx IF NOT EXISTS FOR (l:LegalEntity) ON (l.display_name)",
            "CREATE INDEX holds_scope_period_idx IF NOT EXISTS FOR ()-[r:HOLDS]-() ON (r.source_scope, r.filing_period)",
            "CREATE INDEX holds_scope_state_idx IF NOT EXISTS FOR ()-[r:HOLDS]-() ON (r.source_scope, r.edge_state)",
            "CREATE INDEX holds_run_token_idx IF NOT EXISTS FOR ()-[r:HOLDS]-() ON (r.current_run_token)",
            "CREATE INDEX holds_scope_run_token_idx IF NOT EXISTS FOR ()-[r:HOLDS]-() ON (r.source_scope, r.current_run_token)",
            "CREATE INDEX holds_scope_target_ticker_idx IF NOT EXISTS FOR ()-[r:HOLDS]-() ON (r.source_scope, r.target_ticker)",
            "CREATE INDEX graph_edge_interval_rel_type_idx IF NOT EXISTS FOR (h:GraphEdgeInterval) ON (h.rel_type)",
            "CREATE INDEX graph_edge_interval_scope_idx IF NOT EXISTS FOR (h:GraphEdgeInterval) ON (h.source_scope)",
            "CREATE INDEX graph_edge_interval_state_idx IF NOT EXISTS FOR (h:GraphEdgeInterval) ON (h.edge_state)",
            "CREATE INDEX graph_edge_interval_source_key_idx IF NOT EXISTS FOR (h:GraphEdgeInterval) ON (h.source_node_key)",
            "CREATE INDEX graph_edge_interval_target_key_idx IF NOT EXISTS FOR (h:GraphEdgeInterval) ON (h.target_node_key)",
        ]
        for idx in indexes:
            try:
                session.run(idx)
            except Exception as e:
                if "EquivalentSchemaRuleAlreadyExists" not in str(e) and "already exists" not in str(e).lower():
                    _log_stage_error("Init Neo4j", "create index", str(e), e, color="yellow")

    _log("Neo4j initialized (constraints/indexes for all phases).", "green")


def is_graph_built(driver) -> bool:
    """Return True if a fresh full build exists. Adds a freshness gate: even
    if `built=true`, a build older than NEXUS_FULL_REBUILD_MAX_AGE (default 7d)
    is treated as NOT-built so that `nexus start` with no phase overrides
    actually runs a refresh instead of silently short-circuiting.
    """
    with driver.session() as session:
        r   = session.run(
            "MATCH (g:GraphBuildStatus {id: 'graph_nexus'}) "
            "RETURN g.built AS built, g.built_at AS built_at LIMIT 1"
        )
        rec = r.single()
    if rec is None or not bool(rec.get("built")):
        return False
    built_at_raw = rec.get("built_at")
    if not built_at_raw:
        # Legacy row without timestamp - treat as stale so we refresh once,
        # then subsequent runs will have built_at set by mark_built().
        _log("is_graph_built: GraphBuildStatus has no built_at timestamp; treating as stale.", "yellow")
        return False
    try:
        if hasattr(built_at_raw, "to_native"):
            built_at_raw = built_at_raw.to_native()
        if isinstance(built_at_raw, str):
            built_at = datetime.fromisoformat(built_at_raw.replace("Z", "+00:00"))
        else:
            built_at = built_at_raw
        if built_at.tzinfo is None:
            built_at = built_at.replace(tzinfo=timezone.utc)
        age_sec = (datetime.now(timezone.utc) - built_at).total_seconds()
    except Exception as e:
        _log(f"is_graph_built: could not parse built_at={built_at_raw!r}: {e}; treating as stale.", "yellow")
        return False
    max_age = int(os.environ.get("NEXUS_FULL_REBUILD_MAX_AGE", str(7 * 86400)))
    # max_age <= 0 means "freshness check disabled" — any built row is fresh.
    # (Prior behavior would cause an infinite rebuild loop when set to 0.)
    if max_age > 0 and age_sec > max_age:
        _log(
            f"is_graph_built: build is {age_sec/86400:.1f}d old "
            f"(threshold {max_age/86400:.1f}d); treating as NOT-built so refresh runs.",
            "cyan",
        )
        return False
    return True


def _retire_relationships(
    session,
    rel_type: str,
    where_clause: str,
    *,
    params: dict | None = None,
    directed: bool = True,
    retired_reason: str,
    limit: int = 50000,
) -> int:
    """Close relationships in place so live reads stay online while history remains queryable."""
    total_retired = 0
    close_date = datetime.now(timezone.utc).date().isoformat()
    pattern = f"(a)-[r:{rel_type}]->(b)" if directed else f"(a)-[r:{rel_type}]-(b)"
    base_params = dict(params or {})
    base_params["close_date"] = close_date
    base_params["retired_reason"] = retired_reason
    while True:
        rec = session.run(f"""
            MATCH {pattern}
            WHERE ({where_clause})
              AND (
                    coalesce(r.edge_state, 'open') <> 'closed'
                 OR coalesce(r.retired_reason, '') <> $retired_reason
                 OR r.valid_until IS NULL
                 OR r.valid_until = ''
              )
            WITH DISTINCT r LIMIT {limit}
            SET r.history_id = coalesce(r.history_id, randomUUID()),
                r.edge_state = 'closed',
                r.valid_until = CASE
                    WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                    WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                    ELSE $close_date
                END,
                r.retired_reason = $retired_reason,
                r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
                r.current_run_token = NULL
            RETURN count(r) AS retired
        """, **base_params).single()
        retired = int((rec or {}).get("retired") or 0)
        total_retired += retired
        if retired < limit:
            break
    return total_retired


def _sync_graph_edge_intervals(
    session,
    rel_type: str,
    *,
    source_scope: str | None = None,
    relation_kind: str | None = None,
    directed: bool = True,
    run_token: str | None = None,
    batch_limit: int | None = None,
    log_label: str | None = None,
    expected_total: int | None = None,
    progress_every: int = 50000,
    consume_run_token: bool = False,
) -> int:
    """Persist a durable interval node for each relationship interval while keeping the live edge in place."""
    params = {
        "rel_type": rel_type,
        "source_scope": (source_scope or "").strip(),
        "relation_kind": (relation_kind or "").strip(),
        "run_token": (run_token or "").strip(),
        "directed": bool(directed),
        "synced_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "sync_token": uuid4().hex,
        "batch_limit": max(1, int(batch_limit or GRAPH_EDGE_INTERVAL_SYNC_BATCH_SIZE)),
    }
    params["consume_run_token"] = bool(consume_run_token and params["run_token"])
    filters: list[str] = []
    if not params["consume_run_token"]:
        filters.append("coalesce(r.history_sync_token, '') <> $sync_token")
    if params["run_token"]:
        filters.append("coalesce(r.current_run_token, '') = $run_token")
    if params["source_scope"]:
        filters.append("coalesce(r.source_scope, '') = $source_scope")
    if params["relation_kind"]:
        filters.append("coalesce(r.relation_kind, '') = $relation_kind")
    where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
    total_synced = 0
    current_batch_limit = params["batch_limit"]
    next_progress_log = max(1, int(progress_every))

    if directed:
        query_sql = f"""
            MATCH (src_node)-[r:{rel_type}]->(dst_node)
            {where_sql}
            WITH r, src_node, dst_node
            LIMIT $batch_limit
            SET r.history_id = coalesce(r.history_id, randomUUID()),
                r.history_last_synced_at = $synced_at,
                r.history_sync_token = $sync_token,
                r.current_run_token = CASE
                    WHEN $consume_run_token THEN NULL
                    ELSE r.current_run_token
                END
            MERGE (h:GraphEdgeInterval {{history_id: r.history_id}})
            SET h.rel_type = $rel_type,
                h.source_scope = coalesce(r.source_scope, ''),
                h.relation_kind = coalesce(r.relation_kind, ''),
                h.directionality = 'directed',
                h.edge_state = CASE
                    WHEN coalesce(r.edge_state, '') <> '' THEN r.edge_state
                    WHEN r.valid_until IS NOT NULL AND r.valid_until <> '' THEN 'closed'
                    ELSE 'open'
                END,
                h.source_node_key = coalesce(src_node.ticker, src_node.id, src_node.lei, src_node.symbol, src_node.name, elementId(src_node)),
                h.target_node_key = coalesce(dst_node.ticker, dst_node.id, dst_node.lei, dst_node.symbol, dst_node.name, elementId(dst_node)),
                h.source_labels = labels(src_node),
                h.target_labels = labels(dst_node),
                h.source_ticker = src_node.ticker,
                h.target_ticker = dst_node.ticker,
                h.source_id = coalesce(src_node.id, src_node.lei, src_node.symbol),
                h.target_id = coalesce(dst_node.id, dst_node.lei, dst_node.symbol),
                h.source_name = coalesce(src_node.name, src_node.ticker, src_node.id, src_node.lei, src_node.symbol),
                h.target_name = coalesce(dst_node.name, dst_node.ticker, dst_node.id, dst_node.lei, dst_node.symbol),
                h.active_after = r.active_after,
                h.valid_until = r.valid_until,
                h.retired_reason = r.retired_reason,
                h.closed_at = r.closed_at,
                h.since = r.since,
                h.source = r.source,
                h.confidence = r.confidence,
                h.last_confirmed = r.last_confirmed,
                h.last_updated = r.last_updated,
                h.revenue_pct = r.revenue_pct,
                h.total_obligation = r.total_obligation,
                h.pct_ownership = r.pct_ownership,
                h.flow_value = r.flow_value,
                h.position_pct = r.position_pct,
                h.value_usd = r.value_usd,
                h.shares = r.shares,
                h.filing_period = r.filing_period,
                h.validation_mode = r.validation_mode,
                h.validation_checked_at = r.validation_checked_at,
                h.resolution_mode = r.resolution_mode,
                h.industry_group = r.industry_group,
                h.rel_type_detail = r.rel_type,
                h.country = r.country,
                h.country_code = r.country_code,
                h.patent_ids = coalesce(r.patent_ids, []),
                h.patent_count = size(coalesce(r.patent_ids, [])),
                h.current_run_token = CASE
                    WHEN $consume_run_token THEN $run_token
                    ELSE r.current_run_token
                END,
                h.last_synced_at = $synced_at
            MERGE (src_node)-[:EDGE_INTERVAL_SOURCE]->(h)
            MERGE (h)-[:EDGE_INTERVAL_TARGET]->(dst_node)
            RETURN count(h) AS synced
        """
    else:
        query_sql = f"""
            MATCH (a)-[r:{rel_type}]-(b)
            {where_sql}
            WITH a, b, r,
                 coalesce(a.ticker, a.id, a.lei, a.symbol, a.name, elementId(a)) AS a_key,
                 coalesce(b.ticker, b.id, b.lei, b.symbol, b.name, elementId(b)) AS b_key
            WITH r,
                 CASE WHEN a_key <= b_key THEN a ELSE b END AS src_node,
                 CASE WHEN a_key <= b_key THEN b ELSE a END AS dst_node
            WITH DISTINCT r, src_node, dst_node
            LIMIT $batch_limit
            SET r.history_id = coalesce(r.history_id, randomUUID()),
                r.history_last_synced_at = $synced_at,
                r.history_sync_token = $sync_token,
                r.current_run_token = CASE
                    WHEN $consume_run_token THEN NULL
                    ELSE r.current_run_token
                END
            MERGE (h:GraphEdgeInterval {{history_id: r.history_id}})
            SET h.rel_type = $rel_type,
                h.source_scope = coalesce(r.source_scope, ''),
                h.relation_kind = coalesce(r.relation_kind, ''),
                h.directionality = 'undirected',
                h.edge_state = CASE
                    WHEN coalesce(r.edge_state, '') <> '' THEN r.edge_state
                    WHEN r.valid_until IS NOT NULL AND r.valid_until <> '' THEN 'closed'
                    ELSE 'open'
                END,
                h.source_node_key = coalesce(src_node.ticker, src_node.id, src_node.lei, src_node.symbol, src_node.name, elementId(src_node)),
                h.target_node_key = coalesce(dst_node.ticker, dst_node.id, dst_node.lei, dst_node.symbol, dst_node.name, elementId(dst_node)),
                h.source_labels = labels(src_node),
                h.target_labels = labels(dst_node),
                h.source_ticker = src_node.ticker,
                h.target_ticker = dst_node.ticker,
                h.source_id = coalesce(src_node.id, src_node.lei, src_node.symbol),
                h.target_id = coalesce(dst_node.id, dst_node.lei, dst_node.symbol),
                h.source_name = coalesce(src_node.name, src_node.ticker, src_node.id, src_node.lei, src_node.symbol),
                h.target_name = coalesce(dst_node.name, dst_node.ticker, dst_node.id, dst_node.lei, dst_node.symbol),
                h.active_after = r.active_after,
                h.valid_until = r.valid_until,
                h.retired_reason = r.retired_reason,
                h.closed_at = r.closed_at,
                h.since = r.since,
                h.source = r.source,
                h.confidence = r.confidence,
                h.last_confirmed = r.last_confirmed,
                h.last_updated = r.last_updated,
                h.revenue_pct = r.revenue_pct,
                h.total_obligation = r.total_obligation,
                h.pct_ownership = r.pct_ownership,
                h.flow_value = r.flow_value,
                h.position_pct = r.position_pct,
                h.value_usd = r.value_usd,
                h.shares = r.shares,
                h.filing_period = r.filing_period,
                h.validation_mode = r.validation_mode,
                h.validation_checked_at = r.validation_checked_at,
                h.resolution_mode = r.resolution_mode,
                h.industry_group = r.industry_group,
                h.rel_type_detail = r.rel_type,
                h.country = r.country,
                h.country_code = r.country_code,
                h.patent_ids = coalesce(r.patent_ids, []),
                h.patent_count = size(coalesce(r.patent_ids, [])),
                h.current_run_token = CASE
                    WHEN $consume_run_token THEN $run_token
                    ELSE r.current_run_token
                END,
                h.last_synced_at = $synced_at
            MERGE (src_node)-[:EDGE_INTERVAL_SOURCE]->(h)
            MERGE (h)-[:EDGE_INTERVAL_TARGET]->(dst_node)
            RETURN count(h) AS synced
        """

    if log_label:
        if expected_total:
            _log(
                f"{log_label} starting with batch size {current_batch_limit:,} for about {int(expected_total):,} edge interval(s)...",
                "cyan",
            )
        else:
            _log(
                f"{log_label} starting with batch size {current_batch_limit:,}...",
                "cyan",
            )

    while True:
        try:
            rec = session.run(query_sql, **{**params, "batch_limit": current_batch_limit}).single()
        except Exception as exc:
            if _neo4j_is_memory_error(exc) and current_batch_limit > GRAPH_EDGE_INTERVAL_SYNC_MIN_BATCH_SIZE:
                next_batch_limit = max(GRAPH_EDGE_INTERVAL_SYNC_MIN_BATCH_SIZE, current_batch_limit // 2)
                if next_batch_limit < current_batch_limit:
                    _log(
                        f"Interval sync for {rel_type} hit Neo4j transaction memory limits at batch size "
                        f"{current_batch_limit:,}; retrying with {next_batch_limit:,}.",
                        "yellow",
                    )
                    current_batch_limit = next_batch_limit
                    continue
            raise

        synced = int((rec or {}).get("synced") or 0)
        total_synced += synced
        while log_label and total_synced >= next_progress_log and synced > 0:
            if expected_total:
                pct = min(100.0, (total_synced / max(int(expected_total), 1)) * 100.0)
                _log(
                    f"{log_label} progressed to {total_synced:,}/{int(expected_total):,} interval(s) ({pct:.1f}%).",
                    "white",
                )
            else:
                _log(
                    f"{log_label} progressed to {total_synced:,} interval(s).",
                    "white",
                )
            next_progress_log += max(1, int(progress_every))
        if synced == 0 or synced < current_batch_limit:
            break

    return total_synced


def _neo4j_is_memory_error(exc: Exception) -> bool:
    text = str(exc)
    code = str(getattr(exc, "code", "") or getattr(exc, "neo4j_code", "") or "")
    return (
        "MemoryPoolOutOfMemoryError" in text
        or "dbms.memory.transaction.total.max" in text
        or "MemoryPoolOutOfMemoryError" in code
    )


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1: Company universe (Polygon.io)
# ──────────────────────────────────────────────────────────────────────────────
# Resources: Polygon.io REST API
#   - GET https://api.polygon.io/v3/reference/tickers
#     Params: market (e.g. "stocks"), active=true, limit, order=asc, sort=ticker
#     Response: { results: [{ ticker, name, type, market, primary_exchange, sic_code, currency_name, cik, ... }], next_url? }
#     Pagination: follow next_url (GET with same Bearer token) until no next_url or max_results reached.
# Flow: Fetch tickers (filter out ETF/ETN/UNIT/WARRANT/RIGHT; keep type=CS or market=stocks) → write Company nodes + Sector, IN_SECTOR.
# ──────────────────────────────────────────────────────────────────────────────
def _fetch_polygon_tickers(api_key: str, market: str, limit_per_page: int = 1000, max_results: int = 5000) -> list[dict]:
    """Fetch ticker list from Polygon v3 reference/tickers and keep only issuer-level company listings."""
    if not api_key:
        return []
    import requests
    base = "https://api.polygon.io/v3/reference/tickers"
    params = {"market": market, "active": "true", "limit": limit_per_page, "order": "asc", "sort": "ticker"}
    headers = {"Authorization": f"Bearer {api_key}"}
    out = []
    next_url = None
    while len(out) < max_results:
        _polygon_rate_limit()
        try:
            if next_url:
                r = requests.get(next_url, headers=headers, timeout=30)
            else:
                r = requests.get(base, params=params, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            _log_stage_error("Phase 1: Company universe", "fetch Polygon tickers", str(e), e)
            break
        results = data.get("results") or []
        if results:
            _log(f"Polygon: {len(out)} tickers so far (page batch {len(results)})", "white")
        for t in results:
            ticker_type = (t.get("type") or "").strip().upper()
            name = (t.get("name") or t.get("ticker") or "").strip()[:200]
            if not _is_supported_company_listing(name, ticker_type):
                continue
            if t.get("ticker") and (market == "stocks" and (t.get("market") == "stocks" or ticker_type in ("CS", "ADRC", "ADR"))):
                out.append({
                    "ticker":           (t.get("ticker") or "").strip(),
                    "name":             name,
                    "canonical_name":   _canonical_company_name(name)[:200],
                    "issuer_key":       _company_identity_key(name)[:120],
                    "is_security_instrument": _is_security_instrument_name(name),
                    "type":             ticker_type or None,
                    "primary_exchange": (t.get("primary_exchange") or "").strip() or None,
                    "sic_code":         (t.get("sic_code") or "").strip() or None,
                    "currency_name":    (t.get("currency_name") or "").strip() or None,
                    "cik":              str(t.get("cik")).zfill(10) if t.get("cik") is not None else None,
                })
                if len(out) >= max_results:
                    break
        next_url = data.get("next_url")
        if not next_url or not results:
            break
    return out


def phase1_company_universe(session):
    """Phase 1: Fetch company universe from Polygon.io v3/reference/tickers (stocks), create Company and Sector nodes, link IN_SECTOR."""
    _nexus_stage_reset()
    _progress(10.0, "Phase 1: Company universe — fetching from Polygon.io...", "cyan")
    if not POLYGON_API_KEY:
        _log_stage_error("Phase 1: Company universe", "config", "POLYGON_API_KEY not set", color="red")
        raise SystemExit(1)
    cache_path = _nexus_cache_path("phase1", "polygon_tickers.json")
    companies = _nexus_read_cached_json(cache_path, NEXUS_CACHE_DEFAULT_MAX_AGE)
    if companies is None:
        companies = _fetch_polygon_tickers(POLYGON_API_KEY, "stocks", limit_per_page=1000, max_results=GRAPH_NEXUS_MAX_COMPANIES)
        if companies:
            _nexus_write_cached_json(cache_path, companies)
            _nexus_stage_download(1)
        else:
            companies = []
    else:
        _nexus_stage_cache_hit(1)
        _log("Phase 1: Using cached Polygon tickers.", "white")
    companies, dropped_invalid = _sanitize_company_records(companies if isinstance(companies, list) else [])
    if dropped_invalid:
        _log(f"Phase 1: dropped {dropped_invalid} stale/unsupported cached listings before write.", "yellow")
        try:
            _nexus_write_cached_json(cache_path, companies)
        except Exception as e:
            _log(f"Phase 1: failed to refresh sanitized company cache: {e}", "yellow")
    if not companies:
        _log_stage_unexpected("Phase 1: Company universe", "fetch Polygon tickers", "No tickers returned from Polygon.")
        _progress(PHASE_PCT[2], "Phase 1: skipped (no data).", "yellow")
        return
    if _nexus_rethink_conn:
        _nexus_report_stage_substep(_nexus_rethink_conn, 2, 1)  # substep 1/2 done (fetch)
    _log(f"Phase 1: Writing {len(companies)} company nodes to Neo4j...", "cyan")
    sectors_seen = set()
    total = len(companies)
    # Build batch params; write in chunks to reduce round-trips
    company_batch: list[dict] = []
    for c in companies:
        ticker = c.get("ticker") or ""
        if not ticker:
            continue
        listing_name = (c.get("name") or ticker)[:200]
        canonical_name = (c.get("canonical_name") or _canonical_company_name(listing_name) or listing_name)[:200]
        name = canonical_name or listing_name
        issuer_key = (c.get("issuer_key") or _company_identity_key(canonical_name))[:120]
        sector   = _normalize_sector_name(None, c.get("sic_code")).strip()[:50] or "Other"
        industry = (str(c.get("sic_code") or "Other").strip()[:50] or "Other")
        exchange = c.get("primary_exchange") or ""
        cik      = c.get("cik")
        sectors_seen.add(sector)
        company_batch.append({
            "ticker": ticker, "name": name, "listing_name": listing_name, "sector": sector, "industry": industry,
            "exchange": exchange or None, "cik": cik,
            "canonical_name": canonical_name,
            "issuer_key": issuer_key,
            "is_security_instrument": bool(c.get("is_security_instrument")),
            "listing_type": c.get("type"),
        })
    written = 0
    phase1_batch_size = 100
    for start in range(0, len(company_batch), phase1_batch_size):
        chunk = company_batch[start:start + phase1_batch_size]
        session.run("""
            UNWIND $batch AS p
            MERGE (n:Company {ticker: p.ticker})
            SET n.name = p.name, n.sector = p.sector, n.industry = p.industry,
                n.primary_exchange = p.exchange, n.cik = p.cik,
                n.canonical_name = p.canonical_name, n.issuer_key = p.issuer_key,
                n.is_security_instrument = p.is_security_instrument,
                n.listing_type = p.listing_type,
                n.listing_name = p.listing_name
        """, batch=chunk).consume()
        written += len(chunk)
        if total > 0:
            pct = PHASE_PCT[1] + (PHASE_PCT[2] - PHASE_PCT[1]) * written / total
            _progress(pct, f"Phase 1: wrote {written}/{total} companies...", "white")
        _nexus_maybe_log_eta()
    try:
        pruned_companies = _prune_invalid_company_nodes(session)
        if pruned_companies:
            _log(f"Phase 1: pruned {pruned_companies} unsupported Company nodes from prior runs.", "green")
    except Exception as e:
        _log_stage_error("Phase 1: Company universe", "prune invalid Company nodes", str(e), e, color="yellow")

    # Create Sector nodes: all companies' sectors plus every standard GICS sector so the graph always has Technology, Healthcare, etc.
    for s in sectors_seen:
        session.run("MERGE (s:Sector {name: $name})", name=s).consume()
    for s in _ALL_SECTOR_NAMES:
        session.run("MERGE (s:Sector {name: $name})", name=s).consume()
    session.run("MATCH (c:Company) WHERE c.sector IS NULL OR trim(c.sector) = '' SET c.sector = 'Other'").consume()
    _progress(PHASE_PCT[2], f"Phase 1 done: {len(companies)} Company nodes, {len(sectors_seen)} sectors.", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2: SEC CIK attachment + index nodes
# ──────────────────────────────────────────────────────────────────────────────
# Resources:
#   - GET https://www.sec.gov/files/company_tickers.json
#     Response: JSON object keyed by index; each value has ticker, cik_str (or cik), title. We build ticker -> CIK (10-digit).
#   - GET https://api.polygon.io/v3/reference/tickers (market=indices, limit=50) for Index nodes.
# Flow: Fetch SEC tickers → set Company.cik for matches; optionally fetch Polygon indices → create Index nodes.
# ──────────────────────────────────────────────────────────────────────────────
_SEC_NAME_ALIGNMENT_SKIP_TOKENS = frozenset({
    "inc", "incorporated", "corp", "corporation", "company", "co", "limited", "ltd",
    "llc", "plc", "holdings", "holding", "group", "partners", "partner", "lp", "sa",
    "ag", "nv", "new", "class", "common", "stock", "ordinary", "share", "shares",
    "depositary", "american", "adr", "ads", "units", "unit", "interests", "interest",
    "representing", "ownership", "series", "preferred", "warrant", "warrants",
    "rights", "bank", "banco", "de", "ma", "pa", "va", "nj", "ny", "ca", "tx",
    "plcuk",
})


def _company_name_alignment_tokens(raw_name: str) -> tuple[str, ...]:
    text = _canonical_company_name(raw_name).lower().strip()
    if not text:
        return ()
    text = re.sub(r"/[a-z]{1,4}/", " ", text)
    tokens: list[str] = []
    seen: set[str] = set()
    for tok in re.findall(r"[a-z0-9]+", text):
        if tok.isdigit():
            continue
        if tok in _SEC_NAME_ALIGNMENT_SKIP_TOKENS:
            continue
        if len(tok) <= 2 and tok.isalpha():
            continue
        if tok in seen:
            continue
        seen.add(tok)
        tokens.append(tok)
    return tuple(tokens)


def _company_names_loosely_match(left: str, right: str) -> bool:
    left_key = _company_identity_key(left)
    right_key = _company_identity_key(right)
    if left_key and right_key and left_key == right_key:
        return True
    left_tokens = _company_name_alignment_tokens(left)
    right_tokens = _company_name_alignment_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    left_set = set(left_tokens)
    right_set = set(right_tokens)
    overlap = left_set & right_set
    shorter = min(len(left_set), len(right_set))
    if overlap and (overlap == left_set or overlap == right_set):
        return True
    if len(overlap) >= 2 and len(overlap) / shorter >= 0.67:
        return True
    if len(overlap) == 1 and shorter == 1:
        return True
    left_join = " ".join(left_tokens)
    right_join = " ".join(right_tokens)
    shorter_join, longer_join = (
        (left_join, right_join) if len(left_join) <= len(right_join) else (right_join, left_join)
    )
    return bool(shorter_join and len(shorter_join) >= 8 and shorter_join in longer_join)


def _fetch_sec_company_ticker_rows() -> dict[str, dict[str, str]]:
    """SEC company_tickers.json: ticker -> {cik, title}. SEC updates weekly."""
    cache_path = _nexus_cache_path("phase2", "sec_company_ticker_rows.json")
    cached = _nexus_read_cached_json(cache_path, _nexus_cache_max_age("phase2/sec_company_ticker_rows"))
    if isinstance(cached, dict):
        out: dict[str, dict[str, str]] = {}
        for ticker, payload in cached.items():
            if not ticker or not isinstance(payload, dict):
                continue
            cik = payload.get("cik")
            title = payload.get("title")
            if cik and title:
                out[str(ticker).strip().upper()] = {
                    "cik": str(cik).zfill(10),
                    "title": str(title).strip(),
                }
        if out:
            _nexus_stage_cache_hit(1)
            return out
    import requests
    sec_name = os.environ.get("SEC_EDGAR_COMPANY_NAME", "IntelliStock").strip()
    sec_email = os.environ.get("SEC_EDGAR_EMAIL", "contact@intellistock.local").strip()
    try:
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": f"{sec_name} {sec_email}"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        _log_stage_error("Phase 2: Easy relationships", "fetch SEC company_tickers", str(e), e, color="yellow")
        return {}
    out: dict[str, dict[str, str]] = {}
    for entry in (data.values() if isinstance(data, dict) else []):
        ticker = (entry.get("ticker") or "").strip().upper()
        title = (entry.get("title") or "").strip()
        cik = entry.get("cik_str") or entry.get("cik")
        if not ticker or not title or cik is None or _is_security_instrument_name(title):
            continue
        out[ticker] = {"cik": str(cik).zfill(10), "title": title}
    if out:
        _nexus_write_cached_json(cache_path, out)
        _nexus_stage_download(1)
    return out


def _fetch_sec_company_tickers() -> dict[str, str]:
    rows = _fetch_sec_company_ticker_rows()
    return {ticker: payload["cik"] for ticker, payload in rows.items() if payload.get("cik")}


def _classify_company_records_for_sec_identity(
    company_records: list[dict],
    sec_rows: dict[str, dict[str, str]],
) -> tuple[list[dict], list[dict]]:
    """Split graph companies into SEC-usable and clearly mismatched ticker identities."""
    accepted: list[dict] = []
    rejected: list[dict] = []
    for rec in company_records:
        ticker = (rec.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        current_cik = rec.get("cik")
        current_cik = str(current_cik).zfill(10) if current_cik else None
        sec_row = sec_rows.get(ticker)
        graph_name = (rec.get("canonical_name") or rec.get("name") or ticker).strip()
        if sec_row and sec_row.get("cik"):
            if _company_names_loosely_match(graph_name, sec_row.get("title") or ""):
                accepted.append({
                    "ticker": ticker,
                    "cik": sec_row["cik"],
                    "current_cik": current_cik,
                    "verified": True,
                    "sec_title": sec_row.get("title") or "",
                })
            elif current_cik:
                rejected.append({
                    "ticker": ticker,
                    "cik": current_cik,
                    "sec_title": sec_row.get("title") or "",
                })
        elif current_cik:
            accepted.append({
                "ticker": ticker,
                "cik": current_cik,
                "current_cik": current_cik,
                "verified": False,
                "sec_title": "",
            })
    return accepted, rejected


def _revalidate_company_ciks_against_sec(session, sec_rows: dict[str, dict[str, str]] | None = None) -> dict[str, int]:
    """Clear ticker/CIK mismatches before SEC-backed phases reuse the mapping."""
    sec_rows = sec_rows or _fetch_sec_company_ticker_rows()
    if not sec_rows:
        return {"accepted": 0, "verified": 0, "cleared": 0}

    records = [
        rec.data() for rec in session.run(
            "MATCH (c:Company) "
            "RETURN c.ticker AS ticker, c.name AS name, c.canonical_name AS canonical_name, c.cik AS cik"
        )
    ]
    accepted, rejected = _classify_company_records_for_sec_identity(records, sec_rows)
    verified_batch = [rec for rec in accepted if rec.get("verified")]
    batch_size = PHASE7_HOLDS_WRITE_BATCH_SIZE

    for start in range(0, len(verified_batch), batch_size):
        chunk = verified_batch[start:start + batch_size]
        session.run("""
            UNWIND $batch AS p
            MATCH (c:Company {ticker: p.ticker})
            SET c.cik = p.cik,
                c.sec_identity_verified = true,
                c.sec_title = p.sec_title
        """, batch=chunk).consume()

    for start in range(0, len(rejected), batch_size):
        chunk = rejected[start:start + batch_size]
        session.run("""
            UNWIND $batch AS p
            MATCH (c:Company {ticker: p.ticker})
            REMOVE c.cik
            SET c.sec_identity_verified = false,
                c.sec_title = p.sec_title
        """, batch=chunk).consume()

    return {
        "accepted": len(accepted),
        "verified": len(verified_batch),
        "cleared": len(rejected),
    }


def phase1b_market_cap_enrichment(session):
    """Phase 1b: Enrich Company nodes with market_cap from yfinance.
    Fetches yf.Ticker(symbol).info['marketCap'] in batches and stores as c.yf_market_cap.
    Cached per Company node — only fetches for nodes missing the property."""
    _nexus_stage_reset()
    # Phase 1b runs between Phase 1 (16%) and Phase 2 (22%), using that range for progress
    _p1b_start = PHASE_PCT[2]  # 16%
    _p1b_end = PHASE_PCT[3]    # 22%
    _progress(_p1b_start, "Phase 1b: Market cap enrichment (yfinance) starting...", "cyan")
    _nexus_maybe_log_eta()
    try:
        result = session.run(
            "MATCH (c:Company) WHERE c.yf_market_cap IS NULL AND c.ticker IS NOT NULL "
            "RETURN c.ticker AS ticker LIMIT 6000"
        )
        tickers = [str(rec.get("ticker") or "").strip().upper() for rec in result]
        tickers = [t for t in tickers if t]
    except Exception as e:
        _log(f"Phase 1b: Failed to query tickers: {e}", "yellow")
        return
    if not tickers:
        _progress(_p1b_end, "Phase 1b: All companies already enriched. Skipping.", "green")
        return
    _log(f"Phase 1b: Fetching market_cap for {len(tickers)} companies via yfinance...", "cyan")
    try:
        import yfinance as yf
    except ImportError:
        _log("Phase 1b: yfinance not available, skipping.", "yellow")
        _progress(_p1b_end, "Phase 1b: skipped (yfinance not available).", "yellow")
        return
    import time as _time
    import io as _io
    import sys as _sys
    batch_size = 50
    total_updated = 0
    failed = 0
    consecutive_failures = 0
    cooldown = 0.1  # seconds between tickers (starts low, adapts)
    max_consecutive_failures_before_backoff = 10
    max_backoff_pauses = 5  # give up after this many backoff pauses
    backoff_pauses = 0

    def _fetch_one(ticker):
        """Fetch market_cap for a single ticker with stderr suppression."""
        _old = _sys.stderr
        try:
            _sys.stderr = _io.StringIO()  # suppress yfinance 404/rate-limit noise
            info = yf.Ticker(ticker).info or {}
            return float(info.get("marketCap") or 0), float(info.get("averageVolume") or 0)
        except Exception:
            return -1, 0  # -1 = fetch error (vs 0 = no market_cap)
        finally:
            _sys.stderr = _old

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        updates = []
        batch_failures = 0
        for ticker in batch:
            mc, avg_vol = _fetch_one(ticker)
            if mc == -1:
                failed += 1
                batch_failures += 1
                consecutive_failures += 1
            else:
                consecutive_failures = 0
                updates.append({"ticker": ticker, "mc": mc, "av": avg_vol})
            if cooldown > 0:
                _time.sleep(cooldown)

        # Adaptive rate limiting: if too many failures, pause and slow down
        if consecutive_failures >= max_consecutive_failures_before_backoff:
            backoff_pauses += 1
            if backoff_pauses > max_backoff_pauses:
                _log(f"Phase 1b: Stopping after {backoff_pauses} backoff pauses — Yahoo rate limit sustained. "
                     f"Run Phase 1b again later to resume.", "yellow")
                break
            wait = min(30 * backoff_pauses, 120)  # 30s, 60s, 90s, 120s
            _log(f"Phase 1b: {consecutive_failures} consecutive failures — rate limited. "
                 f"Backing off {wait}s (pause {backoff_pauses}/{max_backoff_pauses})...", "yellow")
            _time.sleep(wait)
            cooldown = min(cooldown * 2, 1.0)  # double the per-ticker cooldown (max 1s)
            consecutive_failures = 0
        elif batch_failures == 0 and cooldown > 0.1:
            cooldown = max(cooldown * 0.8, 0.1)  # reduce cooldown on success

        if updates:
            try:
                session.run("""
                    UNWIND $batch AS p
                    MATCH (c:Company {ticker: p.ticker})
                    SET c.yf_market_cap = p.mc, c.yf_avg_volume = p.av
                """, batch=updates).consume()
                total_updated += sum(1 for u in updates if u["mc"] > 0)
            except Exception as e:
                _log(f"Phase 1b: Neo4j write error at batch {i}: {e}", "yellow")
        done = min(i + batch_size, len(tickers))
        pct = _p1b_start + (_p1b_end - _p1b_start) * done / len(tickers)
        _progress(pct, f"Phase 1b: {done}/{len(tickers)} tickers ({total_updated} with market_cap, "
                  f"cd={cooldown:.1f}s)...", "white")
        _nexus_maybe_log_eta()
    _progress(_p1b_end,
              f"Phase 1b: Market cap enrichment complete. {total_updated}/{len(tickers)} companies updated "
              f"({failed} fetch errors, {backoff_pauses} rate-limit pauses).", "green")


def phase2_easy_relationships(session):
    """Phase 2: Attach CIK from SEC company_tickers.json to Company nodes; create Index nodes from Polygon indices tickers."""
    _nexus_stage_reset()
    _progress(PHASE_PCT[2], "Phase 2: Easy relationships — fetching SEC company_tickers...", "cyan")
    sec_rows = _fetch_sec_company_ticker_rows()
    if sec_rows:
        stats = _revalidate_company_ciks_against_sec(session, sec_rows=sec_rows)
        _log(
            "Phase 2a: SEC identity verified for "
            f"{stats['verified']} companies; cleared {stats['cleared']} ticker/CIK mismatches.",
            "green",
        )

    if POLYGON_API_KEY:
        import requests
        cache_path = _nexus_cache_path("phase2", "polygon_indices.json")
        results = _nexus_read_cached_json(cache_path, NEXUS_CACHE_DEFAULT_MAX_AGE)
        if results is None or not isinstance(results, list):
            results = []
            try:
                _polygon_rate_limit()
                r = requests.get(
                    "https://api.polygon.io/v3/reference/tickers",
                    params={"market": "indices", "active": "true", "limit": 50},
                    headers={"Authorization": f"Bearer {POLYGON_API_KEY}"},
                    timeout=15,
                )
                if r.status_code == 200:
                    results = r.json().get("results") or []
                    if results:
                        _nexus_write_cached_json(cache_path, results)
                        _nexus_stage_download(1)
            except Exception as e:
                _log_stage_error("Phase 2: Easy relationships", "fetch Polygon indices", str(e), e, color="yellow")
        else:
            _nexus_stage_cache_hit(1)
            _log("Phase 2: Using cached Polygon indices.", "white")
        if results:
            for idx in (results if isinstance(results, list) else []):
                sym  = (idx.get("ticker") or "").strip()
                name = (idx.get("name") or sym)[:100]
                if sym:
                    session.run("MERGE (i:Index {symbol: $symbol}) SET i.name = $name", symbol=sym, name=name)
            _log(f"Phase 2b: Created {len(results)} Index nodes from Polygon.", "green")

    _progress(PHASE_PCT[3], "Phase 2 done.", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 2b: SEC submissions — SIC/sector/industry backfill
# ──────────────────────────────────────────────────────────────────────────────
SEC_EDGAR_SUBMISSIONS_DELAY = float(os.environ.get("SEC_EDGAR_RATE_LIMIT_DELAY", "0.11"))
SEC_SECTOR_BACKFILL_MAX     = int(os.environ.get("SEC_SECTOR_BACKFILL_MAX_COMPANIES", "0"))


def _fetch_sec_submissions_sic(cik: str):
    """Fetch SEC submissions for CIK. GET https://data.sec.gov/submissions/CIK{cik}.json. Response: sic, sicDescription, name, ownerOrg, exchanges (list). Returns (sic, sic_desc, owner_org_sector, owner_org_raw, name, exchange). Uses cache (historic)."""
    cik = str(cik).strip().zfill(10)
    empty = (None, None, None, None, None, None)
    cache_paths = [
        _nexus_cache_path("phase2_sec_profiles", f"CIK{cik}.json"),
        _nexus_cache_path("phase2_sec_submissions", f"CIK{cik}.json"),
    ]
    _submissions_max_age = _nexus_cache_max_age("phase2_sec_profiles")
    for cache_path in cache_paths:
        cached = _nexus_read_cached_json(cache_path, _submissions_max_age)
        if isinstance(cached, dict):
            _nexus_stage_cache_hit(1)
            return (
                cached.get("sic"),
                cached.get("sic_desc"),
                cached.get("owner_org_sector"),
                cached.get("owner_org_raw"),
                cached.get("name"),
                cached.get("exchange"),
            )
    import requests
    sec_name  = os.environ.get("SEC_EDGAR_COMPANY_NAME", "IntelliStock").strip()
    sec_email = os.environ.get("SEC_EDGAR_EMAIL", "contact@intellistock.local").strip()
    url       = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        r = requests.get(url, headers={"User-Agent": f"{sec_name} {sec_email}"}, timeout=15)
        if r.status_code != 200:
            return empty
        data     = r.json()
        sic      = str(data.get("sic") or "").strip() or None
        sic_desc = str(data.get("sicDescription") or "").strip() or None
        owner_org_raw = data.get("ownerOrg")
        owner_org_sector = None
        if owner_org_raw:
            s = str(owner_org_raw).strip()
            owner_org_sector = s.split(maxsplit=1)[-1].strip() if " " in s else s
        name      = str(data.get("name") or "").strip() or None
        exchanges = data.get("exchanges") or []
        exchange  = str(exchanges[0]).strip() if exchanges else None
        payload = {
            "sic": sic, "sic_desc": sic_desc, "owner_org_sector": owner_org_sector,
            "owner_org_raw": owner_org_raw, "name": name, "exchange": exchange,
        }
        profile_cache_path = _nexus_cache_path("phase2_sec_profiles", f"CIK{cik}.json")
        os.makedirs(os.path.dirname(profile_cache_path), exist_ok=True)
        _nexus_write_cached_json(profile_cache_path, payload)
        _nexus_stage_download(1)
        return sic, sic_desc, owner_org_sector, owner_org_raw, name, exchange
    except Exception as e:
        _log_stage_error("Phase 2b: SEC sector/industry", "fetch SEC submissions (per CIK)", str(e), e, color="yellow")
        return empty


def phase2b_sec_sector_industry(session):
    """Phase 2b: Backfill SIC/sector/industry from data.sec.gov/submissions/CIK{cik}.json per company; set sector, industry, industry_group on Company nodes."""
    _nexus_stage_reset()
    _progress(PHASE_PCT[3], "Phase 2b: SEC submissions / Stocks cache — SIC/sector/industry backfill...", "cyan")
    conn = _get_rethink_conn()
    if conn:
        _ensure_stocks_table(conn)

    r          = session.run("MATCH (c:Company) WHERE c.cik IS NOT NULL RETURN c.ticker AS ticker, c.cik AS cik")
    ticker_cik = [(rec["ticker"], str(rec["cik"]).zfill(10)) for rec in r if rec.get("ticker") and rec.get("cik")]
    if not ticker_cik:
        _log_stage_unexpected("Phase 2b: SEC sector/industry", "company list", "No companies with CIK; skipping.")
        _progress(PHASE_PCT[4], "Phase 2b skipped (no CIKs).", "green")
        return

    max_n = SEC_SECTOR_BACKFILL_MAX if SEC_SECTOR_BACKFILL_MAX > 0 else len(ticker_cik)
    ticker_cik = ticker_cik[:max_n]
    updated = sec_fetches = 0
    _neo4j_batch: list[dict] = []  # accumulate for batched UNWIND write
    _BATCH_SIZE = 200

    def _flush_neo4j_batch():
        nonlocal _neo4j_batch
        if not _neo4j_batch:
            return
        session.run("""
            UNWIND $batch AS p
            MATCH (c:Company {ticker: p.ticker})
            SET c.sector = p.sector, c.industry = p.industry,
                c.sic_code = p.sic_code, c.industry_group = p.industry_group
        """, batch=_neo4j_batch).consume()
        _neo4j_batch = []

    for idx, (ticker, cik) in enumerate(ticker_cik):
        ticker_upper = (ticker or "").strip().upper()
        cached = _get_cached_stock(conn, ticker_upper) if conn else None
        if cached:
            sic_val  = cached.get("sic_code")
            sector   = _normalize_sector_name(
                cached.get("sector") or cached.get("owner_org") or cached.get("owner_org_sector"),
                sic_val,
            ).strip()[:50] or "Other"
            industry = ((cached.get("industry") or cached.get("sic_description") or "Other").strip()[:50] or "Other")
        else:
            if idx > 0:
                time.sleep(SEC_EDGAR_SUBMISSIONS_DELAY)
            sic, sic_desc, owner_org_sector, owner_org_raw, name, exchange = _fetch_sec_submissions_sic(cik)
            sector   = _normalize_sector_name(owner_org_sector or owner_org_raw, sic).strip()[:50] or "Other"
            industry = ((sic_desc or sic or "Other").strip()[:50] or "Other")
            sic_val  = sic or None
            doc = {
                "id": ticker_upper, "ticker": ticker_upper,
                "name": (name or ticker_upper)[:200],
                "sector": sector, "industry": industry,
                "sic_code": sic_val,
                "sic_description": (sic_desc or "")[:200],
                "owner_org": (owner_org_raw or "")[:100],
                "cik": cik,
                "primary_exchange": (exchange or "")[:50],
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
            _upsert_stock_cache(conn, doc)
            sec_fetches += 1

        # Store fine-grained industry group from SIC
        industry_group = _sic_to_industry_group(sic_val)
        _neo4j_batch.append({
            "ticker": ticker, "sector": sector, "industry": industry,
            "sic_code": sic_val, "industry_group": industry_group,
        })
        updated += 1
        if len(_neo4j_batch) >= _BATCH_SIZE:
            _flush_neo4j_batch()
        if (idx + 1) % 100 == 0:
            _progress(PHASE_PCT[3] + (PHASE_PCT[4] - PHASE_PCT[3]) * (idx + 1) / len(ticker_cik),
                      f"Phase 2b: {idx + 1}/{len(ticker_cik)} (SEC: {sec_fetches}, cached: {updated - sec_fetches})...", "white")
            _nexus_maybe_log_eta()

    _flush_neo4j_batch()  # flush remaining

    session.run("MATCH (c:Company) WHERE c.sector IS NULL OR trim(c.sector)='' SET c.sector='Other'").consume()
    session.run("MATCH (c:Company) WHERE c.industry IS NULL OR trim(c.industry)='' SET c.industry='Other'").consume()
    # Ensure every standard GICS sector exists (so IN_SECTOR can link to Technology, Healthcare, etc., not only Other)
    for s in _ALL_SECTOR_NAMES:
        session.run("MERGE (s:Sector {name: $name})", name=s).consume()
    session.run("MATCH (c:Company) WITH DISTINCT c.sector AS name WHERE name IS NOT NULL AND trim(name)<>'' MERGE (s:Sector {name:name})").consume()
    phase_run_token = f"phase2b:{uuid4().hex}"
    session.run("""
        MATCH (c:Company), (s:Sector)
        WHERE c.sector = s.name
        MERGE (c)-[r:IN_SECTOR {source_scope: 'SEC_SECTOR', edge_state: 'open'}]->(s)
        ON CREATE SET r.active_after = $structural_start, r.last_confirmed = $today, r.valid_until = NULL, r.retired_reason = NULL
        ON MATCH  SET r.active_after = $structural_start, r.valid_until = NULL, r.retired_reason = NULL, r.edge_state = 'open',
                      r.last_confirmed = $today,
                      r.current_run_token = $run_token
        SET r.current_run_token = $run_token
    """, today=datetime.now(timezone.utc).date().isoformat(), structural_start="2000-01-01", run_token=phase_run_token).consume()
    session.run("""
        MATCH (:Company)-[r:IN_SECTOR {source_scope: 'SEC_SECTOR', edge_state: 'open'}]->(:Sector)
        WHERE coalesce(r.current_run_token, '') <> $run_token
        SET r.edge_state = 'closed',
            r.valid_until = CASE
                WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                ELSE $close_date
            END,
            r.retired_reason = 'superseded',
            r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
            r.current_run_token = NULL
    """, run_token=phase_run_token, close_date=datetime.now(timezone.utc).date().isoformat()).consume()
    try:
        _sync_graph_edge_intervals(session, "IN_SECTOR", source_scope="SEC_SECTOR", directed=True)
    except Exception as e:
        _log_stage_error("Phase 2b: SEC sector/industry", "sync IN_SECTOR interval history", str(e), e, color="yellow")
    try:
        retired_legacy = _retire_relationships(
            session,
            "IN_SECTOR",
            "coalesce(r.source_scope, '') = ''",
            directed=True,
            retired_reason="legacy_replaced",
        )
        if retired_legacy:
            _log(f"Phase 2b: retired {retired_legacy} legacy IN_SECTOR edge(s) after replacement.", "green")
    except Exception as e:
        _log_stage_error("Phase 2b: SEC sector/industry", "retire legacy IN_SECTOR", str(e), e, color="yellow")
    _progress(PHASE_PCT[4], f"Phase 2b done: {updated} companies ({sec_fetches} downloaded, {updated - sec_fetches} cached).", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3: Supply chain (10-K + edge metadata)
# ──────────────────────────────────────────────────────────────────────────────
SUPPLY_CHAIN_CACHE_DIR      = os.environ.get("GRAPH_NEXUS_CACHE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache"))
SUPPLY_CHAIN_CACHE_MAX_AGE  = int(os.environ.get("GRAPH_NEXUS_SUPPLY_CHAIN_CACHE_MAX_AGE", "86400"))
GRAPH_NEXUS_SUPPLY_CHAIN_SOURCE = (os.environ.get("GRAPH_NEXUS_SUPPLY_CHAIN_SOURCE", "") or "sec_edgar").strip().lower()
if GRAPH_NEXUS_SUPPLY_CHAIN_SOURCE not in ("csv", "url", "sec_edgar"):
    GRAPH_NEXUS_SUPPLY_CHAIN_SOURCE = "sec_edgar"
SUPPLY_CHAIN_BATCH_SIZE = int(os.environ.get("GRAPH_NEXUS_SUPPLY_CHAIN_BATCH_SIZE", "500"))

# Shared cache for all nexus stages (downloaded files)
NEXUS_CACHE_DIR = SUPPLY_CHAIN_CACHE_DIR
NEXUS_CACHE_DEFAULT_MAX_AGE = int(os.environ.get("GRAPH_NEXUS_CACHE_MAX_AGE", "86400"))
# Historic/persistent data (truly immutable: frozen-accession filings, patent
# grants, closed 13F quarters). Use cache whenever file exists.
NEXUS_CACHE_MAX_AGE_HISTORIC = 0  # 0 = use cache if file exists, never expire

# ---------------------------------------------------------------------------
# Centralized cache policy (V32 P5.1 + nexus-refresh-fix)
# ---------------------------------------------------------------------------
# Prior versions misclassified mutable data sources as "historic" and pinned
# them forever (SEC weekly ticker list, BEA annually-revised I-O tables, 13F
# amendments, rolling USASpending/8-K/Wikidata scrapes). The helper below
# maps cache subdirs to the correct TTL and supports NEXUS_CACHE_FORCE_REFRESH
# for operator-initiated refreshes without deleting cache files.

# Per-subdir TTL policy. Keys match the `subdir` arg of _nexus_cache_path().
# "*" in a key is NOT globbed; most entries are exact subdir matches. Entries
# with "callable" values run `policy(filename)` for per-file TTL decisions.
_DAY = 86400
_WEEK = 7 * _DAY
_MONTH = 30 * _DAY

NEXUS_CACHE_POLICY: dict[str, object] = {
    # Truly immutable (frozen-accession data). NOTE: the ex21 call sites pass
    # subdir="phase6" flat with filename-prefix "ex21_*_", so these nested
    # keys only resolve when callers explicitly request the ex21 subdir.
    # Keep for forward-compat if ex21 paths are ever rooted under
    # "phase6/ex21_*" directly.
    "phase6/ex21_index":      NEXUS_CACHE_MAX_AGE_HISTORIC,
    "phase6/ex21_parsed":     NEXUS_CACHE_MAX_AGE_HISTORIC,
    "phase3":                 NEXUS_CACHE_MAX_AGE_HISTORIC,   # 10-K filing bodies

    # "Latest/recent" endpoints — SHORT TTL
    "phase7_13f":             _WEEK,         # 13F quarterly ZIPs (45-day amendment window)
    "phase7_13f_atom":        _DAY,          # SEC EDGAR Atom feed (rolling)
    "phase8":                 _DAY,          # USASpending rolling window
    "phase12_8k":             _DAY,          # 8-K rolling 365-day scrape
    "phase2/sec_company_ticker_rows": _WEEK, # SEC weekly updates

    # Slowly mutable
    "phase2_sec_profiles":    _MONTH,        # per-CIK submissions
    "phase5":                 _MONTH,        # BEA I-O (annual revisions)
    "phase6":                 _MONTH,        # GLEIF LEI data (default for phase6/*)
    "phase9":                 _WEEK,         # Wikidata daily community edits

    # Defaults (documented TTL, already short):
    "phase1":                 _DAY,          # Polygon universe
    "phase2":                 _DAY,          # Polygon indices
    "phase10":                _DAY,          # PatentsView
    "phase12":                _WEEK,         # ETF universe (30d holdings handled elsewhere)
}


def _nexus_cache_force_refresh_matches(subdir: str) -> bool:
    """Return True if NEXUS_CACHE_FORCE_REFRESH is set for this subdir.
    Values: "1"/"true"/"yes"/"all" -> refresh ALL subdirs. Comma-separated
    subdir names -> refresh ONLY subdirs whose name equals the token or sits
    under it (token + "/"). Bare-prefix matching was intentionally removed
    because it collides (e.g. "phase1" would clobber "phase10"/"phase12_8k").
    """
    val = os.environ.get("NEXUS_CACHE_FORCE_REFRESH", "").strip().lower()
    if not val:
        return False
    if val in ("1", "true", "yes", "on", "all"):
        return True
    wanted = {v.strip() for v in val.split(",") if v.strip()}
    subdir_norm = subdir.strip().strip("/").lower()
    for w in wanted:
        if subdir_norm == w or subdir_norm.startswith(w + "/"):
            return True
    return False


def _nexus_cache_max_age(subdir: str, default: int | None = None) -> int:
    """Return TTL (seconds) for the given cache subdir, honoring force-refresh.

    Precedence:
      1. NEXUS_CACHE_FORCE_REFRESH -> returns 1 (effectively always miss)
      2. NEXUS_CACHE_POLICY lookup (exact match, then walk up by "/")
      3. `default` argument
      4. NEXUS_CACHE_DEFAULT_MAX_AGE
    """
    if _nexus_cache_force_refresh_matches(subdir):
        return 1
    # Walk up the subdir hierarchy for a policy match (e.g. "phase6/gleif_search" -> "phase6")
    parts = subdir.strip("/").split("/") if subdir else []
    while parts:
        key = "/".join(parts)
        if key in NEXUS_CACHE_POLICY:
            val = NEXUS_CACHE_POLICY[key]
            if callable(val):
                return int(val(subdir))
            return int(val)
        parts.pop()
    if default is not None:
        return int(default)
    return int(NEXUS_CACHE_DEFAULT_MAX_AGE)


def _nexus_cache_path(subdir: str, filename: str) -> str:
    """Return path to a cached file; ensures subdir exists."""
    d = os.path.join(NEXUS_CACHE_DIR, subdir)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, filename)


def _nexus_cache_lookup_path(subdir: str, filename: str, *legacy_subdirs: str) -> str:
    """Return the stage-aligned cache path, or an existing legacy path if present."""
    primary_path = _nexus_cache_path(subdir, filename)
    if os.path.isfile(primary_path):
        return primary_path
    for legacy_subdir in legacy_subdirs:
        legacy_path = os.path.join(NEXUS_CACHE_DIR, legacy_subdir, filename)
        if os.path.isfile(legacy_path):
            return legacy_path
    return primary_path


def _nexus_read_cached_json(path: str, max_age_sec: int) -> dict | list | None:
    """Return parsed JSON from cache if file exists and (when max_age_sec > 0) is fresh; else None.
    Use max_age_sec <= 0 (e.g. NEXUS_CACHE_MAX_AGE_HISTORIC) for persistent data: use cache whenever file exists."""
    if not os.path.isfile(path):
        return None
    if max_age_sec > 0 and time.time() - os.path.getmtime(path) > max_age_sec:
        return None
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        pass
    return None


def _nexus_write_cached_json(path: str, data: dict | list) -> None:
    """Write JSON to cache file atomically (temp file + os.replace) so concurrent writers
    — e.g. the Phase 6 GLEIF parallel cache-warm hitting the same path for a duplicate name
    or shared LEI — can't produce a torn/partial file, and concurrent readers see only the
    old or new complete file."""
    import json
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=0)
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def _nexus_read_cached_file(path: str, max_age_sec: int) -> bytes | None:
    """Return file bytes from cache if exists and (when max_age_sec > 0) is fresh; else None.
    Use max_age_sec <= 0 for persistent data: use cache whenever file exists."""
    if not os.path.isfile(path):
        return None
    if max_age_sec > 0 and time.time() - os.path.getmtime(path) > max_age_sec:
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except Exception:
        pass
    return None


def _nexus_write_cached_file(path: str, data: bytes) -> None:
    """Write bytes to cache file."""
    with open(path, "wb") as f:
        f.write(data)


_PHASE6_DISCARD_LOG_LOCK = threading.Lock()


def _phase6_discard_log_path() -> str:
    return _nexus_cache_path("phase6", "discarded_edges.log.txt")


def _phase6_reset_discard_log(run_token: str) -> str:
    path = _phase6_discard_log_path()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": "phase6",
        "event": "discard_log_started",
        "run_token": str(run_token or "").strip(),
    }
    with _PHASE6_DISCARD_LOG_LOCK:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def _phase6_log_discarded_edge(run_token: str, category: str, reason: str, payload: dict | None = None) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": "phase6",
        "event": "discarded_edge",
        "run_token": str(run_token or "").strip(),
        "category": str(category or "").strip(),
        "reason": str(reason or "").strip(),
    }
    if isinstance(payload, dict):
        for key, value in payload.items():
            if value is None:
                continue
            entry[str(key)] = value
    path = _phase6_discard_log_path()
    with _PHASE6_DISCARD_LOG_LOCK:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _clamp_phase7_history_quarters(value) -> int:
    try:
        quarters = int(value)
    except Exception:
        quarters = PHASE7_13F_HISTORY_QUARTERS_DEFAULT
    return max(1, min(PHASE7_13F_MAX_HISTORY_QUARTERS, quarters))


# ──────────────────────────────────────────────────────────────────────────────
# Stage stats (cached vs downloaded) and runtime / ETA
# ──────────────────────────────────────────────────────────────────────────────
def _normalize_historical_iso_date(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except Exception:
        return ""


def _nexus_temporal_state() -> dict:
    state = _NEXUS_TEMPORAL_STATE[0]
    if not isinstance(state, dict):
        state = {}
        _NEXUS_TEMPORAL_STATE[0] = state
    state.setdefault("historical_mode_enabled", False)
    state.setdefault("historical_start_date", None)
    state.setdefault("force_bootstrap_rebuild", False)
    state.setdefault("historical_bootstrap_complete", False)
    state.setdefault("historical_coverage_end", None)
    manifests = state.get("historical_phase_manifests")
    if not isinstance(manifests, dict):
        manifests = {}
        state["historical_phase_manifests"] = manifests
    return state


def _set_nexus_temporal_state_from_control(ctrl: dict | None) -> None:
    ctrl = ctrl or {}
    manifests = ctrl.get("historical_phase_manifests")
    _NEXUS_TEMPORAL_STATE[0] = {
        "historical_mode_enabled": bool(ctrl.get("historical_mode_enabled", False)),
        "historical_start_date": _normalize_historical_iso_date(ctrl.get("historical_start_date")) or None,
        "force_bootstrap_rebuild": bool(ctrl.get("force_bootstrap_rebuild", False)),
        "historical_bootstrap_complete": bool(ctrl.get("historical_bootstrap_complete", False)),
        "historical_coverage_end": _normalize_historical_iso_date(ctrl.get("historical_coverage_end")) or None,
        "historical_phase_manifests": dict(manifests or {}) if isinstance(manifests, dict) else {},
    }


def _nexus_historical_enabled() -> bool:
    state = _nexus_temporal_state()
    return bool(state.get("historical_mode_enabled") and state.get("historical_start_date"))


def _nexus_force_bootstrap_requested() -> bool:
    return bool(_nexus_temporal_state().get("force_bootstrap_rebuild", False))


def _nexus_bootstrap_considered_complete(state: dict | None = None) -> bool:
    state = dict(state or _nexus_temporal_state())
    if bool(state.get("historical_bootstrap_complete", False)):
        return True
    manifests = dict(state.get("historical_phase_manifests") or {})
    required = ("phase3", "phase7", "phase12")
    return bool(required) and all(bool((manifests.get(key) or {}).get("bootstrap_complete")) for key in required)


def _nexus_phase_rerun_requested(phase_num: int) -> bool:
    requested = _NEXUS_REQUESTED_PHASE_RANGE[0]
    start = requested.get("start")
    end = requested.get("end")
    if start is None or end is None:
        return False
    try:
        phase_num = int(phase_num)
        return int(start) <= phase_num <= int(end)
    except Exception:
        return False


def _nexus_historical_start_date() -> str:
    state = _nexus_temporal_state()
    return _normalize_historical_iso_date(state.get("historical_start_date")) or ""


def _nexus_today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _nexus_phase_manifest(phase_key: str) -> dict:
    manifests = _nexus_temporal_state().setdefault("historical_phase_manifests", {})
    manifest = manifests.get(phase_key)
    if not isinstance(manifest, dict):
        manifest = {}
        manifests[phase_key] = manifest
    return manifest


def _nexus_update_phase_manifest(
    phase_key: str,
    *,
    mode: str | None = None,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    last_incremental_start: str | None = None,
    last_incremental_end: str | None = None,
    bootstrap_complete: bool | None = None,
    extra: dict | None = None,
) -> dict:
    manifest = dict(_nexus_phase_manifest(phase_key))
    if mode is not None:
        manifest["mode"] = str(mode or "").strip() or manifest.get("mode") or "current_snapshot"
    for field, value in (
        ("coverage_start", coverage_start),
        ("coverage_end", coverage_end),
        ("last_incremental_start", last_incremental_start),
        ("last_incremental_end", last_incremental_end),
    ):
        if value is None:
            continue
        manifest[field] = _normalize_historical_iso_date(value) or value
    if bootstrap_complete is not None:
        manifest["bootstrap_complete"] = bool(bootstrap_complete)
    if extra:
        manifest.update(extra)
    _nexus_temporal_state().setdefault("historical_phase_manifests", {})[phase_key] = manifest
    return manifest


def _quarters_between(start_date: str, end_date: str | None = None) -> int:
    start_iso = _normalize_historical_iso_date(start_date)
    end_iso = _normalize_historical_iso_date(end_date) or _nexus_today_iso()
    if not start_iso:
        return 1
    start_dt = datetime.strptime(start_iso, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_iso, "%Y-%m-%d").date()
    if end_dt < start_dt:
        return 1
    return ((end_dt.year - start_dt.year) * 12 + (end_dt.month - start_dt.month)) // 3 + 1


def _next_historical_iso_date(value: str | None) -> str:
    current = _normalize_historical_iso_date(value)
    if not current:
        return ""
    try:
        return (datetime.strptime(current, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()
    except Exception:
        return ""


def _nexus_phase_fetch_window(phase_key: str) -> tuple[str, str]:
    if not _nexus_historical_enabled():
        return "", ""
    today = _nexus_today_iso()
    manifest = _nexus_phase_manifest(phase_key)
    start_iso = _nexus_historical_start_date()
    coverage_end = _normalize_historical_iso_date(
        manifest.get("last_incremental_end") or manifest.get("coverage_end")
    )
    if coverage_end:
        next_start = _next_historical_iso_date(coverage_end)
        if next_start and next_start <= today:
            start_iso = next_start
        elif next_start and next_start > today:
            return "", today
    return start_iso, today


def _ensure_default_phase_manifest_after_run(phase_key: str) -> None:
    if not _nexus_historical_enabled():
        return
    manifest = _nexus_phase_manifest(phase_key)
    if manifest.get("coverage_end"):
        return
    today = _nexus_today_iso()
    default_mode = {
        "phase1": "current_snapshot",
        "phase2": "current_snapshot",
        "phase2b": "current_snapshot",
        "phase4": "current_snapshot",
        "phase5": "annual_snapshot",
        "phase6": "current_snapshot",
        "phase6_ex21": "current_snapshot",
        "phase9": "current_snapshot",
        "phase10": "current_snapshot",
        "phase11": "current_snapshot",
        "phase_etf": "current_snapshot",
    }.get(phase_key, "current_snapshot")
    _nexus_update_phase_manifest(
        phase_key,
        mode=default_mode,
        coverage_start=today,
        coverage_end=today,
        bootstrap_complete=True,
    )


_nexus_stage_stats = {"cached": 0, "downloaded": 0}
_nexus_stage_stats_lock = threading.Lock()
_nexus_phase_start_time: float = 0.0
_nexus_build_start_time: float = 0.0
_nexus_last_eta_log_time: float = 0.0
# _nexus_rethink_conn, _nexus_current_phase_index, _nexus_current_phase_label,
# _nexus_current_phase_num are declared near the top of the file (early init for _progress)
_NEXUS_RUNTIMES_FILE = "nexus_runtimes.json"
_MAX_RUNTIMES_PER_PHASE = 20
NEXUS_ETA_LOG_INTERVAL_SEC = int(os.environ.get("GRAPH_NEXUS_ETA_LOG_INTERVAL_SEC", "300"))  # 5 min

# Linear progress: init 5%, check 10%, phase1 16%, phase2 22%, phase2b 28%, phase3 34%, phase4 40%,
# phase5 46%, phase6 52%, phase6_ex21 58%, phase7 64%, phase8 70%, phase9 76%, phase10 82%, phase11 88%,
# phase_etf 94%, mark 100%
PHASE_PCT = [5.0, 10.0, 16.0, 22.0, 28.0, 34.0, 40.0, 46.0, 52.0, 58.0, 64.0, 70.0, 76.0, 82.0, 88.0, 94.0, 100.0]
PHASE_KEYS = ["init", "check", "phase1", "phase2", "phase2b", "phase3", "phase4", "phase5", "phase6", "phase6_ex21", "phase7", "phase9", "phase10", "phase11", "phase12", "phase_etf", "mark_built"]
# Canonical stage labels for CLI/API/Discord (index, key, label, progress_pct)
NEXUS_STAGES = [
    (0,  "init",       "Init Neo4j",                    5.0),
    (1,  "check",      "Check if built",               10.0),
    (2,  "phase1",     "Phase 1: Company universe",    16.0),
    (3,  "phase2",     "Phase 2: Easy relationships",  22.0),
    (4,  "phase2b",    "Phase 2b: SEC sector/industry", 28.0),
    (5,  "phase3",     "Phase 3: Supply chain",        34.0),
    (6,  "phase4",     "Phase 4: Competitive",         40.0),
    (7,  "phase5",     "Phase 5: Macro/BEA",           46.0),
    (8,  "phase6",     "Phase 6: GLEIF hierarchy",      52.0),
    (9,  "phase6_ex21","Phase 6B: SEC EX-21 hierarchy", 58.0),
    (10, "phase7",     "Phase 7: 13F ownership",        64.0),
    (11, "phase9",     "Phase 8: USASpending",          70.0),
    (12, "phase10",    "Phase 9: Wikidata",             76.0),
    (13, "phase11",    "Phase 10: PatentsView",         82.0),
    (14, "phase12",    "Phase 11: 8-K agreements",      88.0),
    (15, "phase_etf",  "Phase 12: ETF universe",        94.0),
    (16, "mark_built", "Mark built",                   100.0),
]
# Substeps per stage (same index order). Persisted so CLI shows progress within each stage.
NEXUS_STAGE_SUBSTEPS = [1, 1, 2, 2, 2, 3, 1, 3, 1, 1, 2, 2, 2, 2, 2, 1, 1]

# Phase 10 (PatentsView) stage_index for live progress updates
PHASE11_STAGE_INDEX = 13


def _phase11_report_progress(conn, substep: int, current: int, total: int, stage_message: str, progress_pct_override: float | None = None) -> None:
    """Update RethinkDB progress for Phase 10 PatentsView. total=0 for indeterminate (e.g. fetching)."""
    if conn is None:
        return
    try:
        if total and total > 0:
            progress_pct = PHASE_PCT[12] + (current / total) * (PHASE_PCT[13] - PHASE_PCT[12])
            phase_label = f"Phase 10: PatentsView — {stage_message}"
        else:
            progress_pct = progress_pct_override if progress_pct_override is not None else PHASE_PCT[12]
            phase_label = f"Phase 10: PatentsView — {stage_message}"
        _save_graph_nexus_progress(
            conn,
            11,  # last_completed_phase (through phase 9)
            progress_pct,
            phase_label,
            "running",
            current_phase_number=12,
            current_phase_label=phase_label,
            stage_update={"stage_index": PHASE11_STAGE_INDEX, "message": stage_message},
        )
    except Exception:
        pass


# Phase 3 (Supply chain) stage_index for live progress updates
PHASE3_STAGE_INDEX = 5


def _phase3_report_progress(conn, current: int, total: int, stage_message: str) -> None:
    """Update RethinkDB stage message for Phase 3 historical 10-K scraping (ticker-by-ticker progress)."""
    if conn is None:
        return
    try:
        if total and total > 0:
            progress_pct = PHASE_PCT[5] + (current / total) * (PHASE_PCT[6] - PHASE_PCT[5])
            phase_label = f"Phase 3: Supply chain — {current}/{total} tickers"
        else:
            progress_pct = PHASE_PCT[5]
            phase_label = f"Phase 3: Supply chain — {stage_message}"
        _save_graph_nexus_progress(
            conn, 4, progress_pct, phase_label, "running",
            current_phase_number=3, current_phase_label=phase_label,
            stage_update={"stage_index": PHASE3_STAGE_INDEX, "status": "running", "message": stage_message},
        )
    except Exception:
        pass
    # Also update the sec_edgar_scraper progress doc (drives the SEC EDGAR Scraper card in the UI)
    try:
        import re as _re
        m = _re.search(r'(\d+)\s+merged\s+edges', stage_message)
        edges_count = int(m.group(1)) if m else 0
        scraper_pct = round((current / total) * 100, 1) if total > 0 else 0.0
        store.insert(GRAPH_NEXUS_PROGRESS_TABLE, {
            "id": "sec_edgar_scraper",
            "last_ticker_index": current,
            "total_tickers": total,
            "edges_count": edges_count,
            "progress_pct": scraper_pct,
            "message": stage_message,
            "last_updated": _store_now(),
            "status": "running",
        }, conflict="replace")
    except Exception:
        pass


# Phase 7 (13F ownership) stage_index for live progress updates
PHASE7_STAGE_INDEX = 10


def _phase7_report_progress(conn, current: int, total: int, stage_message: str) -> None:
    """Update RethinkDB stage message for Phase 7 bootstrap snapshot processing."""
    if conn is None:
        return
    try:
        if total and total > 0:
            progress_pct = PHASE_PCT[10] + (current / total) * (PHASE_PCT[11] - PHASE_PCT[10])
            phase_label = f"Phase 7: 13F ownership — snapshot {current}/{total}"
        else:
            progress_pct = PHASE_PCT[10]
            phase_label = f"Phase 7: 13F ownership — {stage_message}"
        _save_graph_nexus_progress(
            conn, 8, progress_pct, phase_label, "running",
            current_phase_number=9, current_phase_label=phase_label,
            stage_update={"stage_index": PHASE7_STAGE_INDEX, "status": "running", "message": stage_message},
        )
    except Exception:
        pass


# Phase 11 (8-K agreements) stage_index for live progress updates
PHASE12_STAGE_INDEX = 14


def _phase12_report_progress(conn, current: int, total: int, stage_message: str, progress_pct_override: float | None = None) -> None:
    """Update RethinkDB progress for Phase 11 8-K. total=0 for indeterminate."""
    if conn is None:
        return
    try:
        if total and total > 0:
            progress_pct = PHASE_PCT[13] + (current / total) * (PHASE_PCT[14] - PHASE_PCT[13])
            pct_str = round(100.0 * current / total, 1)
            phase_label = f"Phase 11: 8-K agreements — {pct_str}% ({current}/{total})"
        else:
            progress_pct = progress_pct_override if progress_pct_override is not None else PHASE_PCT[13]
            phase_label = f"Phase 11: 8-K agreements — {stage_message}"
        _save_graph_nexus_progress(
            conn,
            12,  # last_completed_phase (through phase 10)
            progress_pct,
            phase_label,
            "running",
            current_phase_number=13,
            current_phase_label=phase_label,
            stage_update={"stage_index": PHASE12_STAGE_INDEX, "message": stage_message},
        )
    except Exception:
        pass


PHASE13_STAGE_INDEX = 15


def _phase13_report_progress(conn, current: int, total: int, stage_message: str, progress_pct_override: float | None = None) -> None:
    """Update RethinkDB progress for Phase 12 (ETF universe). total=0 for indeterminate."""
    if conn is None:
        return
    try:
        if total and total > 0:
            progress_pct = PHASE_PCT[14] + (current / total) * (PHASE_PCT[15] - PHASE_PCT[14])
            pct_str = round(100.0 * current / total, 1)
            phase_label = f"Phase 12: ETF universe — {pct_str}% ({current}/{total})"
        else:
            progress_pct = progress_pct_override if progress_pct_override is not None else PHASE_PCT[14]
            phase_label = f"Phase 12: ETF universe — {stage_message}"
        _save_graph_nexus_progress(
            conn,
            13,  # last_completed_phase (through phase 11)
            progress_pct,
            phase_label,
            "running",
            current_phase_number=14,
            current_phase_label=phase_label,
            stage_update={"stage_index": PHASE13_STAGE_INDEX, "message": stage_message},
        )
    except Exception:
        pass


def _nexus_stage_reset() -> None:
    with _nexus_stage_stats_lock:
        _nexus_stage_stats["cached"] = 0
        _nexus_stage_stats["downloaded"] = 0


def _nexus_stage_cache_hit(n: int = 1) -> None:
    with _nexus_stage_stats_lock:
        _nexus_stage_stats["cached"] = _nexus_stage_stats.get("cached", 0) + n


def _nexus_stage_download(n: int = 1) -> None:
    with _nexus_stage_stats_lock:
        _nexus_stage_stats["downloaded"] = _nexus_stage_stats.get("downloaded", 0) + n


def _nexus_runtimes_path() -> str:
    return os.path.join(NEXUS_CACHE_DIR, _NEXUS_RUNTIMES_FILE)


def _nexus_runtimes_load() -> dict:
    """Load phase runtimes from cache (phase_key -> list of seconds)."""
    path = _nexus_runtimes_path()
    if not os.path.isfile(path):
        return {}
    try:
        import json
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _nexus_runtimes_save(runtimes: dict) -> None:
    path = _nexus_runtimes_path()
    try:
        import json
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(runtimes, f, indent=0)
    except Exception as e:
        _log(f"Could not save runtimes: {e}", "yellow")


def _nexus_phase_avg(phase_key: str) -> float | None:
    """Average runtime in seconds for a phase (from history). Returns None if no history."""
    runtimes = _nexus_runtimes_load()
    times = runtimes.get(phase_key)
    if not times or not isinstance(times, list):
        return None
    times = [float(t) for t in times[-_MAX_RUNTIMES_PER_PHASE:] if isinstance(t, (int, float))]
    if not times:
        return None
    return sum(times) / len(times)


def _nexus_estimate_remaining(phase_index: int) -> float | None:
    """Estimated seconds remaining from phase_index to end (sum of avg runtimes). None if no estimate."""
    runtimes = _nexus_runtimes_load()
    total = 0.0
    for i in range(phase_index, len(PHASE_KEYS)):
        key = PHASE_KEYS[i]
        avg = _nexus_phase_avg(key)
        if avg is None:
            return None  # no history for at least one phase
        total += avg
    return total


def _nexus_eta_remaining_given_current_phase(elapsed_in_current_sec: float) -> float | None:
    """ETA in seconds from now to end of build, given we're in current phase with elapsed_in_current_sec so far."""
    global _nexus_current_phase_index
    if _nexus_current_phase_index < 0 or _nexus_current_phase_index >= len(PHASE_KEYS):
        return _nexus_estimate_remaining(0)
    runtimes = _nexus_runtimes_load()
    remaining = 0.0
    key_cur = PHASE_KEYS[_nexus_current_phase_index]
    avg_cur = _nexus_phase_avg(key_cur)
    if avg_cur is not None and avg_cur > elapsed_in_current_sec:
        remaining += avg_cur - elapsed_in_current_sec
    for i in range(_nexus_current_phase_index + 1, len(PHASE_KEYS)):
        avg = _nexus_phase_avg(PHASE_KEYS[i])
        if avg is None:
            return None
        remaining += avg
    return remaining if (remaining > 0 or avg_cur is not None) else None


def _nexus_maybe_log_eta() -> None:
    """If at least NEXUS_ETA_LOG_INTERVAL_SEC have passed since last ETA log, log ETA and persist to RethinkDB."""
    global _nexus_last_eta_log_time, _nexus_rethink_conn, _nexus_current_phase_label, _nexus_current_phase_num
    now = time.monotonic()
    if now - _nexus_last_eta_log_time < NEXUS_ETA_LOG_INTERVAL_SEC:
        return
    _nexus_last_eta_log_time = now
    elapsed_in_phase = now - _nexus_phase_start_time if _nexus_phase_start_time else 0
    eta_sec = _nexus_eta_remaining_given_current_phase(elapsed_in_phase)
    total_elapsed = now - _nexus_build_start_time if _nexus_build_start_time else 0
    msg = f"Periodic ETA — elapsed: {_nexus_format_duration(total_elapsed)}"
    if eta_sec is not None and eta_sec > 0:
        msg += f" — estimated remaining: {_nexus_format_duration(eta_sec)}"
    _log(msg, "cyan")
    # Persist progress to RethinkDB so CLI/API/Discord show current phase and ETA
    if _nexus_rethink_conn and _nexus_current_phase_index >= 0:
        pct = PHASE_PCT[_nexus_current_phase_index - 1] if _nexus_current_phase_index <= len(PHASE_PCT) else 0
        last_done = _nexus_current_phase_num - 1 if _nexus_current_phase_num else 0
        _save_graph_nexus_progress(
            _nexus_rethink_conn,
            last_done,
            pct,
            _nexus_current_phase_label or f"Phase {_nexus_current_phase_num}",
            "running",
            eta_remaining_sec=eta_sec,
            current_phase_number=_nexus_current_phase_num,
            current_phase_label=_nexus_current_phase_label,
        )


def _nexus_format_duration(sec: float) -> str:
    if sec < 60:
        return f"{sec:.1f}s"
    m = int(sec // 60)
    s = int(sec % 60)
    if m < 60:
        return f"{m}m {s}s"
    h = m // 60
    m = m % 60
    return f"{h}h {m}m {s}s"


def _nexus_stage_log(phase_label: str, pct: float, elapsed_sec: float, phase_key: str, phase_index: int, extra: str = "") -> None:
    """Log stage completion with cached/downloaded counts, elapsed time, avg time, and estimated remaining."""
    c = _nexus_stage_stats.get("cached", 0)
    d = _nexus_stage_stats.get("downloaded", 0)
    total_elapsed = time.monotonic() - _nexus_build_start_time if _nexus_build_start_time else 0
    eta_sec = _nexus_estimate_remaining(phase_index + 1) if phase_index + 1 < len(PHASE_KEYS) else None
    avg_sec = _nexus_phase_avg(phase_key)
    msg_parts = [f"[{pct:.0f}%] {phase_label} completed in {_nexus_format_duration(elapsed_sec)}"]
    if avg_sec is not None:
        msg_parts.append(f" (avg {_nexus_format_duration(avg_sec)})")
    msg_parts.append(f" — cached: {c}, downloaded: {d}")
    if extra:
        msg_parts.append(f" — {extra}")
    msg_parts.append(f" — elapsed: {_nexus_format_duration(total_elapsed)}")
    if eta_sec is not None and eta_sec > 0:
        msg_parts.append(f" — ETA remaining: {_nexus_format_duration(eta_sec)}")
    _log("".join(msg_parts), "green")


def _get_company_tickers_from_graph(session) -> list[tuple[str, str | None]]:
    r = session.run("MATCH (c:Company) RETURN c.ticker AS ticker, c.cik AS cik")
    out = []
    for rec in r:
        ticker = rec.get("ticker")
        if not ticker:
            continue
        cik = rec.get("cik")
        if cik is not None:
            cik = str(cik).zfill(10) if cik else None
        out.append((ticker, cik))
    return out


def _phase3_live_supply_chain_counts(session) -> dict[str, int]:
    return {
        "supplier_edges": _count_live_scoped_relationships(
            session,
            rel_type="SUPPLIER_OF",
            source_scope="SEC_10K_SUPPLIER",
            directed=True,
        ),
        "partner_edges": _count_live_scoped_relationships(
            session,
            rel_type="STRATEGIC_PARTNER",
            source_scope="SEC_10K_STRATEGIC_PARTNER",
            directed=False,
        ),
    }


def _phase3_should_ignore_existing_coverage(session, phase_manifest: dict, history_start_date: str) -> bool:
    """Force a full historical 10-K rebuild when coverage metadata exists but Phase 3 graph output is gone."""
    if history_start_date and _nexus_force_bootstrap_requested():
        return True
    if not history_start_date:
        return False
    if not bool((phase_manifest or {}).get("bootstrap_complete")):
        return True
    try:
        live_counts = _phase3_live_supply_chain_counts(session)
        supplier_count = int(live_counts.get("supplier_edges") or 0)
        partner_count = int(live_counts.get("partner_edges") or 0)
        return supplier_count <= 0 and partner_count <= 0
    except Exception:
        return False


def _run_sec_edgar_supply_chain_scraper(session) -> tuple[str | None, bool, dict]:
    """Returns (csv_path or None, merged_incrementally, summary)."""
    try:
        from sec_edgar_supply_chain import (
            get_supply_chain_csv_from_sec_edgar,
            run_sec_edgar_supply_chain_historical,
        )
    except ImportError as e:
        _log(f"SEC EDGAR scraper not available: {e}", "red")
        return (None, False, {})
    try:
        sec_rows = _fetch_sec_company_ticker_rows()
        stats = _revalidate_company_ciks_against_sec(session, sec_rows=sec_rows)
        if stats["cleared"]:
            _log(
                "Phase 3: removed "
                f"{stats['cleared']} ticker/CIK mismatches before SEC filing downloads.",
                "yellow",
            )
    except Exception as e:
        _log_stage_error("Phase 3: Supply chain", "revalidate SEC ticker identities", str(e), e, color="yellow")
    ticker_cik_list = _get_company_tickers_from_graph(session)
    if not ticker_cik_list:
        return (None, False, {})
    max_co          = int(os.environ.get("SEC_EDGAR_SUPPLY_CHAIN_MAX_COMPANIES", "8000"))
    ticker_cik_list = [(t, c) for t, c in ticker_cik_list if c]
    sec_ticker_to_cik = _fetch_sec_company_tickers()
    cik_to_sec_ticker = {v: k for k, v in sec_ticker_to_cik.items()}
    by_cik: dict[str, str] = {}
    for ticker, cik in sorted(ticker_cik_list, key=lambda x: (x[1], x[0])):
        if cik in by_cik:
            continue
        primary = cik_to_sec_ticker.get(cik)
        by_cik[cik] = primary if primary else ticker
    ticker_cik_list = [(t, c) for c, t in sorted(by_cik.items(), key=lambda x: x[1])]
    if len(ticker_cik_list) > max_co:
        ticker_cik_list = ticker_cik_list[:max_co]
    tickers      = [t for t, _ in ticker_cik_list]
    ticker_to_cik = {t: c for t, c in ticker_cik_list}
    _log(f"Running SEC EDGAR supply chain scraper for {len(tickers)} companies.", "cyan")
    cache_path = os.path.join(SUPPLY_CHAIN_CACHE_DIR, "supply_chain_sec_edgar.csv")
    if _nexus_historical_enabled():
        history_start_date = _nexus_historical_start_date()
        phase3_manifest = _nexus_phase_manifest("phase3")
        ignore_existing_coverage = _phase3_should_ignore_existing_coverage(session, phase3_manifest, history_start_date)
        if ignore_existing_coverage:
            start_date, end_date = history_start_date, _nexus_today_iso()
        else:
            start_date, end_date = _nexus_phase_fetch_window("phase3")
        if not start_date and not os.path.isfile(cache_path):
            start_date = history_start_date
        if not start_date:
            _log("Phase 3: historical SEC coverage already up to date; no new 10-K windows to fetch.", "white")
            return (cache_path if os.path.isfile(cache_path) else None, True, {
                "start_date": "",
                "end_date": end_date or _nexus_today_iso(),
                "filings_processed": 0,
                "latest_filing_date": _normalize_historical_iso_date(
                    _nexus_phase_manifest("phase3").get("coverage_end")
                ),
                "edge_count": 0,
                "incremental_noop": True,
            })
        summary: dict = {}
        _p3_conn = _nexus_rethink_conn
        reparse_historical_filings = bool(ignore_existing_coverage or not bool(phase3_manifest.get("bootstrap_complete")))
        if ignore_existing_coverage:
            _log(
                "Phase 3: bootstrap coverage is incomplete in the current graph; rebuilding historical 10-K windows from cached filings.",
                "white",
            )
        elif reparse_historical_filings:
            _log(
                "Phase 3: bootstrap coverage is incomplete; reparsing historical 10-K filings from cached HTML and ignoring derived SEC caches.",
                "white",
            )

        def _p3_progress(cur: int, tot: int, msg: str) -> None:
            _nexus_maybe_log_eta()
            _phase3_report_progress(_p3_conn, cur, tot, msg)
            if cur % 50 == 0 or cur == tot:
                _log(f"Phase 3 historical 10-K: {msg}", "white")

        run_sec_edgar_supply_chain_historical(
            start_date=start_date,
            end_date=end_date,
            tickers=tickers,
            ticker_to_cik=ticker_to_cik,
            output_csv_path=cache_path,
            progress_cb=_p3_progress,
            edge_cb=lambda batch: apply_supply_chain_edges(session, batch) if batch else None,
            edge_cb_batch_size=50,
            summary_cb=lambda info: summary.update(info or {}),
            ignore_parsed_edge_cache=reparse_historical_filings,
            ignore_existing_output_csv=reparse_historical_filings,
        )
        summary.setdefault("start_date", start_date)
        summary.setdefault("end_date", end_date)
        return (cache_path if os.path.isfile(cache_path) else None, True, summary)
    path = get_supply_chain_csv_from_sec_edgar(
        cache_dir=SUPPLY_CHAIN_CACHE_DIR,
        tickers_from_neo4j=tickers,
        ticker_to_cik=ticker_to_cik,
        progress_callback=_nexus_maybe_log_eta,
        edges_callback=lambda batch: apply_supply_chain_edges(session, batch) if batch else None,
    )
    return (path, path is not None, {})


def _get_supply_chain_csv_path(session=None) -> tuple[str | None, bool, dict]:
    """Returns (csv_path or None, merged_incrementally, summary)."""
    if GRAPH_NEXUS_SUPPLY_CHAIN_SOURCE == "sec_edgar" and session is not None:
        path, merged_incrementally, summary = _run_sec_edgar_supply_chain_scraper(session)
        return (path if path and os.path.isfile(path) else None, merged_incrementally, summary)
    csv_path = os.environ.get("GRAPH_NEXUS_SUPPLY_CHAIN_CSV", "").strip()
    if csv_path and os.path.isfile(csv_path):
        return (csv_path, False, {})
    url = os.environ.get("GRAPH_NEXUS_SUPPLY_CHAIN_URL", "").strip()
    if not url:
        return (None, False, {})
    os.makedirs(SUPPLY_CHAIN_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(SUPPLY_CHAIN_CACHE_DIR, "supply_chain.csv")
    try:
        import urllib.request
        need_download = True
        if os.path.isfile(cache_path):
            # max_age <= 0: use cache whenever present (historic). Else use if within max_age.
            if SUPPLY_CHAIN_CACHE_MAX_AGE <= 0 or time.time() - os.path.getmtime(cache_path) < SUPPLY_CHAIN_CACHE_MAX_AGE:
                need_download = False
                _nexus_stage_cache_hit(1)
        if need_download:
            req = urllib.request.Request(url, headers={"User-Agent": "IntelliStock-GraphNexus/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            with open(cache_path, "wb") as f:
                f.write(data)
            _nexus_stage_download(1)
        return (cache_path, False, {})
    except Exception as e:
        _log_stage_error("Phase 3: Supply chain", "download supply chain CSV/URL", str(e), e)
        return (cache_path if os.path.isfile(cache_path) else None, False, {})


def _normalize_edge_date(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) >= 10:
        raw = raw[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
    except Exception:
        return ""


def _normalize_graph_ticker(value) -> str:
    return str(value or "").strip().upper()


def _company_ticker_pair_key(left, right, *, directed: bool = False) -> tuple[str, str] | tuple[()]:
    a = _normalize_graph_ticker(left)
    b = _normalize_graph_ticker(right)
    if not a or not b:
        return tuple()
    if directed:
        return (a, b)
    return tuple(sorted((a, b)))


def _phase11_prepare_8k_partner_batch(batch_edges: list[dict]) -> tuple[list[dict], set[tuple[str, str]], int]:
    aggregated: dict[tuple[str, str], dict] = {}
    raw_valid_count = 0
    for edge in batch_edges or []:
        sup = _normalize_graph_ticker(edge.get("sup"))
        cust = _normalize_graph_ticker(edge.get("cust"))
        if not sup or not cust or sup == cust:
            continue
        raw_valid_count += 1
        pair_key = _company_ticker_pair_key(sup, cust, directed=False)
        if not pair_key:
            continue
        try:
            confidence = float(edge.get("confidence", 0.80) or 0.80)
        except Exception:
            confidence = 0.80
        last_confirmed = _normalize_edge_date(edge.get("last_confirmed"))
        active_after = _normalize_edge_date(edge.get("active_after") or edge.get("last_confirmed"))
        existing = aggregated.get(pair_key)
        if existing is None:
            aggregated[pair_key] = {
                "sup": pair_key[0],
                "cust": pair_key[1],
                "conf": confidence,
                "last_confirmed": last_confirmed,
                "active_after": active_after,
            }
            continue
        if confidence > existing["conf"]:
            existing["conf"] = confidence
        if last_confirmed and (not existing["last_confirmed"] or last_confirmed > existing["last_confirmed"]):
            existing["last_confirmed"] = last_confirmed
        if active_after and (not existing["active_after"] or active_after < existing["active_after"]):
            existing["active_after"] = active_after
    return list(aggregated.values()), set(aggregated.keys()), raw_valid_count


def _phase12_prepare_etf_holding_params(etf_ticker: str, holdings: list[dict], *, snapshot_date: str, today_iso: str) -> list[dict]:
    etf = _normalize_graph_ticker(etf_ticker)
    if not etf or not holdings:
        return []
    deduped_weights: dict[str, float] = {}
    for holding in holdings:
        holding_ticker = _normalize_graph_ticker(holding.get("holding_ticker"))
        if not holding_ticker:
            continue
        try:
            weight_pct = float(holding.get("weight_pct") or 0.0)
        except Exception:
            continue
        if weight_pct <= 0:
            continue
        if holding_ticker not in deduped_weights or weight_pct > deduped_weights[holding_ticker]:
            deduped_weights[holding_ticker] = weight_pct
    return [
        {
            "etf": etf,
            "holding": holding_ticker,
            "weight": weight_pct,
            "snapshot_date": snapshot_date,
            "today": today_iso,
        }
        for holding_ticker, weight_pct in sorted(deduped_weights.items())
    ]


def _bea_row_active_after(row: dict) -> str:
    year = str(row.get("_source_year") or row.get("Year") or "").strip()
    if len(year) == 4 and year.isdigit():
        return f"{year}-12-31"
    return ""


def _normalize_supply_chain_row(row: dict) -> tuple[str, str, dict] | None:
    """Extract (sup, cust, metadata) from a CSV row. Supports old and new column formats."""
    keys_lower = {k.strip().lower(): k for k in row}
    sup_key  = next((keys_lower[k] for k in ("sup", "supplier", "supplier_ticker") if k in keys_lower), None)
    cust_key = next((keys_lower[k] for k in ("cust", "customer", "customer_ticker") if k in keys_lower), None)
    sup  = (row.get(sup_key)  or "").strip().upper()
    cust = (row.get(cust_key) or "").strip().upper()
    if not sup or not cust or sup == cust:
        return None
    meta = {
        "confidence":     float(row.get("confidence") or 0.7),
        "source":         row.get("source") or "10-K",
        "edge_type":      row.get("edge_type") or "SUPPLIER_OF",
        "last_confirmed": _normalize_edge_date(row.get("last_confirmed")),
        "active_after":   _normalize_edge_date(row.get("active_after") or row.get("last_confirmed")),
    }
    if row.get("revenue_pct"):
        try:
            meta["revenue_pct"] = float(row["revenue_pct"])
        except Exception:
            pass
    if _edge_pair_denied(meta["edge_type"], sup, cust):
        return None
    return sup, cust, meta


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3: Supply chain
# ──────────────────────────────────────────────────────────────────────────────
# No direct API calls in this file. Data from: (1) GRAPH_NEXUS_SUPPLY_CHAIN_SOURCE = path to CSV, or
# (2) "sec_edgar" → sec_edgar_supply_chain (company_tickers.json, data.sec.gov/submissions, 10-K fetch/parse).
# CSV columns: sup/supplier/supplier_ticker, cust/customer/customer_ticker, confidence, source, edge_type, last_confirmed.
# Flow: Resolve source to CSV path → read rows → normalize → MERGE SUPPLIER_OF edges.
# ──────────────────────────────────────────────────────────────────────────────
def phase3_supply_chain(session):
    """Phase 3: Load supply chain edges from CSV or SEC EDGAR (sec_edgar_supply_chain). Create SUPPLIER_OF edges between Companies."""
    _nexus_stage_reset()
    _progress(PHASE_PCT[4], "Phase 3: Supply chain — resolving source (CSV / URL / SEC EDGAR)...", "cyan")
    history_start_date = _nexus_historical_start_date()
    phase_manifest = _nexus_phase_manifest("phase3")
    supply_chain_csv, merged_incrementally, phase3_summary = _get_supply_chain_csv_path(session)
    if not supply_chain_csv or not os.path.isfile(supply_chain_csv):
        _log_stage_unexpected("Phase 3: Supply chain", "resolve source", "No supply chain source (set GRAPH_NEXUS_SUPPLY_CHAIN_SOURCE=sec_edgar or CSV/URL).")
        _progress(PHASE_PCT[5], "Phase 3 skipped (no data source).", "green")
        return
    try:
        pruned = _prune_invalid_supply_chain_edges(session)
        if pruned:
            _log(f"Phase 3: retired {pruned} stale/invalid SUPPLIER_OF edge interval(s) before merge.", "green")
    except Exception as e:
        _log_stage_error("Phase 3: Supply chain", "prune invalid SUPPLIER_OF", str(e), e, color="yellow")
    try:
        reconciled = _reconcile_reciprocal_supply_chain_edges(session)
        if reconciled["deleted_total"]:
            _log(
                "Phase 3: reconciled "
                f"{reconciled['pairs']} reciprocal SUPPLIER_OF pair(s) "
                f"({reconciled['resolved_directional']} directional, {reconciled['dropped_ambiguous']} ambiguous).",
                "green",
            )
    except Exception as e:
        _log_stage_error("Phase 3: Supply chain", "reconcile reciprocal SUPPLIER_OF", str(e), e, color="yellow")
    if merged_incrementally:
        import csv as csvlib
        try:
            with open(supply_chain_csv, newline="", encoding="utf-8", errors="replace") as f:
                total_rows = sum(1 for row in csvlib.DictReader(f) if _normalize_supply_chain_row(row))
        except Exception as e:
            _log_stage_error("Phase 3: Supply chain", "count CSV rows (incremental)", str(e), e, color="yellow")
            total_rows = 0
        try:
            reconciled = _reconcile_reciprocal_supply_chain_edges(session)
            if reconciled["deleted_total"]:
                _log(
                    "Phase 3: post-merge reciprocal cleanup removed "
                    f"{reconciled['deleted_total']} SUPPLIER_OF edge(s).",
                    "green",
                )
        except Exception as e:
            _log_stage_error("Phase 3: Supply chain", "post-merge reciprocal cleanup", str(e), e, color="yellow")
        try:
            _sync_graph_edge_intervals(session, "SUPPLIER_OF", source_scope="SEC_10K_SUPPLIER", directed=True)
            _sync_graph_edge_intervals(session, "STRATEGIC_PARTNER", source_scope="SEC_10K_STRATEGIC_PARTNER", directed=False)
            purged_conflicts = _purge_closed_supply_chain_direction_conflicts(session)
            if purged_conflicts:
                _log(
                    f"Phase 3: removed {purged_conflicts} closed direction-conflict SUPPLIER_OF edge(s) after history sync.",
                    "green",
                )
        except Exception as e:
            _log_stage_error("Phase 3: Supply chain", "sync SEC interval history", str(e), e, color="yellow")
        try:
            retired_legacy_suppliers = _retire_relationships(
                session,
                "SUPPLIER_OF",
                "coalesce(r.source_scope, '') = ''",
                directed=True,
                retired_reason="legacy_replaced",
            )
            retired_legacy_partners = _retire_relationships(
                session,
                "STRATEGIC_PARTNER",
                "coalesce(r.source_scope, '') = ''",
                directed=False,
                retired_reason="legacy_replaced",
            )
            if retired_legacy_suppliers or retired_legacy_partners:
                _log(
                    "Phase 3: retired "
                    f"{retired_legacy_suppliers} legacy SUPPLIER_OF and "
                    f"{retired_legacy_partners} legacy STRATEGIC_PARTNER edge(s) after replacement.",
                    "green",
                )
        except Exception as e:
            _log_stage_error("Phase 3: Supply chain", "retire legacy SEC edges", str(e), e, color="yellow")
        if history_start_date:
            scan_end = _normalize_historical_iso_date(phase3_summary.get("end_date")) or _nexus_today_iso()
            latest_filing_date = _normalize_historical_iso_date(phase3_summary.get("latest_filing_date"))
            coverage_end = scan_end or _normalize_historical_iso_date(phase_manifest.get("coverage_end")) or history_start_date
            coverage_start = _normalize_historical_iso_date(phase_manifest.get("coverage_start")) or history_start_date
            bootstrap_complete = bool(coverage_end and coverage_end >= _nexus_today_iso())
            _nexus_update_phase_manifest(
                "phase3",
                mode="event_history",
                coverage_start=coverage_start,
                coverage_end=coverage_end,
                last_incremental_start=_normalize_historical_iso_date(phase3_summary.get("start_date")) or coverage_end,
                last_incremental_end=coverage_end,
                bootstrap_complete=bootstrap_complete,
                extra={
                    "filings_processed": int(phase3_summary.get("filings_processed") or 0),
                    "edge_count": int(phase3_summary.get("edge_count") or total_rows),
                    "latest_event_date": latest_filing_date or None,
                },
            )
            if phase3_summary.get("incremental_noop"):
                _log("Phase 3: historical SEC event coverage already current.", "white")
        live_counts = _phase3_live_supply_chain_counts(session)
        _log(
            "Phase 3: live SEC 10-K graph now has "
            f"{live_counts['supplier_edges']} SUPPLIER_OF and "
            f"{live_counts['partner_edges']} STRATEGIC_PARTNER edges.",
            "green",
        )
        _log(
            f"Phase 3 done: SEC 10-K supply chain refresh completed from incremental scrape ({total_rows} rows in CSV).",
            "green",
        )
        _progress(
            PHASE_PCT[5],
            f"Phase 3 done: {live_counts['supplier_edges']} live supplier edges and {live_counts['partner_edges']} live 10-K partner edges.",
            "green",
        )
        return
    import csv as csvlib
    try:
        batch_size    = 50  # upload each 50-edge batch to Neo4j
        total_created = 0
        total_rows    = 0
        today         = datetime.now(timezone.utc).date().isoformat()
        seen: set[tuple[str, str]] = set()
        candidate_rows: list[dict] = []
        batch: list[dict] = []

        def _flush_batch() -> int:
            nonlocal total_created, batch
            if not batch:
                return 0
            # Filter to minimum confidence before writing — discard noisy low-confidence edges
            batch_params = [
                {
                    "sup":            str(edge.get("sup") or "").strip().upper(),
                    "cust":           str(edge.get("cust") or "").strip().upper(),
                    "confidence":     edge.get("confidence", 0.7),
                    "source":         edge.get("source", "10-K"),
                    "revenue_pct":    edge.get("revenue_pct"),
                    "last_confirmed": _normalize_edge_date(edge.get("last_confirmed")),
                    "active_after":   _normalize_edge_date(edge.get("active_after") or edge.get("last_confirmed")),
                    "edge_type":      edge.get("edge_type", "SUPPLIER_OF"),
                }
                for edge in batch
                if edge.get("confidence", 0.7) >= SUPPLY_CHAIN_MIN_CONFIDENCE
            ]
            if not batch_params:
                batch = []
                return 0
            valid_a = _cypher_valid_company_listing_predicate("a")
            valid_b = _cypher_valid_company_listing_predicate("b")
            # MATCH (not MERGE) for Company nodes: only create edges between companies that
            # already exist in the graph (from Phase 1). This prevents phantom Company nodes
            # being created for tickers extracted from noise in 10-K text.
            # Split by edge_type — Neo4j relationship types cannot be parameterised.
            from collections import defaultdict
            by_etype: dict[str, list[dict]] = defaultdict(list)
            for p in batch_params:
                by_etype[p["edge_type"]].append(p)

            n_written = 0
            for etype, typed_batch in by_etype.items():
                if etype == "STRATEGIC_PARTNER":
                    query = """
                        UNWIND $batch AS p
                        WITH p.sup AS sup, p.cust AS cust, p.confidence AS conf,
                             p.source AS src, p.last_confirmed AS lc, p.active_after AS aa
                        WHERE sup IS NOT NULL AND cust IS NOT NULL AND sup <> cust
                        MATCH (a:Company {ticker: sup})
                        MATCH (b:Company {ticker: cust})
                        WHERE
                            __VALID_A__
                            AND __VALID_B__
                            AND (a.issuer_key IS NULL OR b.issuer_key IS NULL OR a.issuer_key <> b.issuer_key)
                            AND
                            (a.sic_code IS NULL OR toInteger(a.sic_code) < 6000 OR toInteger(a.sic_code) > 6999)
                            AND (b.sic_code IS NULL OR toInteger(b.sic_code) < 6000 OR toInteger(b.sic_code) > 6999)
                            AND NOT EXISTS { (a)-[:SUPPLIER_OF]-(b) }
                        MERGE (a)-[r:STRATEGIC_PARTNER {source_scope: 'SEC_10K_STRATEGIC_PARTNER', edge_state: 'open'}]-(b)
                        ON CREATE SET r.confidence = conf, r.source = src,
                                      r.last_confirmed = lc, r.active_after = aa,
                                      r.created_at = CASE WHEN aa <> '' THEN aa WHEN lc <> '' THEN lc ELSE $today END,
                                      r.valid_until = NULL, r.retired_reason = NULL
                        ON MATCH  SET r.confidence = CASE WHEN conf > r.confidence THEN conf ELSE r.confidence END,
                                      r.last_confirmed = CASE
                                          WHEN lc = '' THEN r.last_confirmed
                                          WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR lc > r.last_confirmed THEN lc
                                          ELSE r.last_confirmed
                                      END,
                                      r.active_after = CASE
                                          WHEN aa = '' THEN r.active_after
                                          WHEN r.active_after IS NULL OR r.active_after = '' OR aa < r.active_after THEN aa
                                          ELSE r.active_after
                                      END,
                                      r.valid_until = NULL, r.retired_reason = NULL, r.edge_state = 'open'
                        RETURN count(r) AS created
                    """
                    query = query.replace("__VALID_A__", valid_a).replace("__VALID_B__", valid_b)
                    result = session.run(query, batch=typed_batch, today=today)
                else:
                    query = """
                        UNWIND $batch AS p
                        WITH p.sup AS sup, p.cust AS cust, p.confidence AS conf,
                             p.source AS src, p.revenue_pct AS rev, p.last_confirmed AS lc, p.active_after AS aa
                        WHERE sup IS NOT NULL AND cust IS NOT NULL AND sup <> cust
                        MATCH (a:Company {ticker: sup})
                        MATCH (b:Company {ticker: cust})
                        WHERE
                            __VALID_A__
                            AND __VALID_B__
                            AND (a.issuer_key IS NULL OR b.issuer_key IS NULL OR a.issuer_key <> b.issuer_key)
                            AND
                            (a.sic_code IS NULL OR toInteger(a.sic_code) < 6000 OR toInteger(a.sic_code) > 6999)
                            AND (b.sic_code IS NULL OR toInteger(b.sic_code) < 6000 OR toInteger(b.sic_code) > 6999)
                        MERGE (a)-[r:SUPPLIER_OF {source_scope: 'SEC_10K_SUPPLIER', edge_state: 'open'}]->(b)
                        ON CREATE SET r.confidence = conf, r.source = src,
                                      r.revenue_pct = rev, r.last_confirmed = lc, r.active_after = aa,
                                      r.created_at = CASE WHEN aa <> '' THEN aa WHEN lc <> '' THEN lc ELSE $today END,
                                      r.valid_until = NULL, r.retired_reason = NULL
                        ON MATCH  SET r.confidence = CASE WHEN conf > r.confidence THEN conf ELSE r.confidence END,
                                      r.last_confirmed = CASE
                                          WHEN lc = '' THEN r.last_confirmed
                                          WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR lc > r.last_confirmed THEN lc
                                          ELSE r.last_confirmed
                                      END,
                                      r.active_after = CASE
                                          WHEN aa = '' THEN r.active_after
                                          WHEN r.active_after IS NULL OR r.active_after = '' OR aa < r.active_after THEN aa
                                          ELSE r.active_after
                                      END,
                                      r.valid_until = NULL, r.retired_reason = NULL, r.edge_state = 'open'
                        RETURN count(r) AS created
                    """
                    query = query.replace("__VALID_A__", valid_a).replace("__VALID_B__", valid_b)
                    result = session.run(query, batch=typed_batch, today=today)
                rec = result.single()
                n_written += (rec.get("created") or 0) if rec else 0
                result.consume()

            batch = []
            total_created += n_written
            return n_written

        with open(supply_chain_csv, newline="", encoding="utf-8", errors="replace") as f:
            for row in csvlib.DictReader(f):
                parsed = _normalize_supply_chain_row(row)
                if parsed:
                    sup, cust, meta = parsed
                    key = (sup, cust)
                    if key not in seen:
                        seen.add(key)
                        candidate_rows.append(
                            {
                                "sup": sup,
                                "cust": cust,
                                "confidence": meta.get("confidence", 0.7),
                                "source": meta.get("source", "10-K"),
                                "revenue_pct": meta.get("revenue_pct"),
                                "last_confirmed": _normalize_edge_date(meta.get("last_confirmed")),
                                "active_after": _normalize_edge_date(meta.get("active_after") or meta.get("last_confirmed")),
                                "edge_type": meta.get("edge_type", "SUPPLIER_OF"),
                            }
                        )
                        total_rows += 1
                        if total_rows % 500 == 0:
                            _nexus_maybe_log_eta()
                        if len(batch) >= batch_size:
                            _flush_batch()
                            _log(f"Phase 3: wrote batch to Neo4j — {total_created} SUPPLIER_OF edges so far", "white")
                            _nexus_maybe_log_eta()
            for row in _dedupe_reciprocal_supply_chain_edge_candidates(
                [
                    {
                        "sup": candidate["sup"],
                        "cust": candidate["cust"],
                        "conf": candidate["confidence"],
                        "src": candidate["source"],
                        "rev": candidate.get("revenue_pct"),
                        "lc": candidate.get("last_confirmed"),
                        "aa": candidate.get("active_after"),
                        "etype": candidate.get("edge_type", "SUPPLIER_OF"),
                    }
                    for candidate in candidate_rows
                ]
            ):
                batch.append(
                    {
                        "sup": row["sup"],
                        "cust": row["cust"],
                        "confidence": row.get("conf", 0.7),
                        "source": row.get("src", "10-K"),
                        "revenue_pct": row.get("rev"),
                        "last_confirmed": row.get("lc"),
                        "active_after": row.get("aa"),
                        "edge_type": row.get("etype", "SUPPLIER_OF"),
                    }
                )
                if len(batch) >= batch_size:
                    _flush_batch()
                    _log(f"Phase 3: wrote batch to Neo4j â€” {total_created} SUPPLIER_OF edges so far", "white")
                    _nexus_maybe_log_eta()
            _flush_batch()

        if total_rows == 0:
            _log_stage_unexpected("Phase 3: Supply chain", "parse CSV", "No supply chain edges in CSV.")
            _progress(PHASE_PCT[5], "Phase 3 skipped (no edges).", "green")
            return
        try:
            reconciled = _reconcile_reciprocal_supply_chain_edges(session)
            if reconciled["deleted_total"]:
                _log(
                    "Phase 3: post-load reciprocal cleanup removed "
                    f"{reconciled['deleted_total']} SUPPLIER_OF edge(s).",
                    "green",
                )
        except Exception as e:
            _log_stage_error("Phase 3: Supply chain", "post-load reciprocal cleanup", str(e), e, color="yellow")
        try:
            _sync_graph_edge_intervals(session, "SUPPLIER_OF", source_scope="SEC_10K_SUPPLIER", directed=True)
            _sync_graph_edge_intervals(session, "STRATEGIC_PARTNER", source_scope="SEC_10K_STRATEGIC_PARTNER", directed=False)
            purged_conflicts = _purge_closed_supply_chain_direction_conflicts(session)
            if purged_conflicts:
                _log(
                    f"Phase 3: removed {purged_conflicts} closed direction-conflict SUPPLIER_OF edge(s) after history sync.",
                    "green",
                )
        except Exception as e:
            _log_stage_error("Phase 3: Supply chain", "sync SEC interval history", str(e), e, color="yellow")
        try:
            retired_legacy_suppliers = _retire_relationships(
                session,
                "SUPPLIER_OF",
                "coalesce(r.source_scope, '') = ''",
                directed=True,
                retired_reason="legacy_replaced",
            )
            retired_legacy_partners = _retire_relationships(
                session,
                "STRATEGIC_PARTNER",
                "coalesce(r.source_scope, '') = ''",
                directed=False,
                retired_reason="legacy_replaced",
            )
            if retired_legacy_suppliers or retired_legacy_partners:
                _log(
                    "Phase 3: retired "
                    f"{retired_legacy_suppliers} legacy SUPPLIER_OF and "
                    f"{retired_legacy_partners} legacy STRATEGIC_PARTNER edge(s) after replacement.",
                    "green",
                )
        except Exception as e:
            _log_stage_error("Phase 3: Supply chain", "retire legacy SEC edges", str(e), e, color="yellow")

        live_counts = _phase3_live_supply_chain_counts(session)
        _log(
            "Phase 3: live SEC 10-K graph now has "
            f"{live_counts['supplier_edges']} SUPPLIER_OF and "
            f"{live_counts['partner_edges']} STRATEGIC_PARTNER edges.",
            "green",
        )
        _progress(
            PHASE_PCT[5],
            f"Phase 3 done: {live_counts['supplier_edges']} live supplier edges and {live_counts['partner_edges']} live 10-K partner edges.",
            "green",
        )
    except Exception as e:
        _log_stage_error("Phase 3: Supply chain", "load/write CSV to Neo4j", str(e), e)
        _progress(PHASE_PCT[5], "Phase 3: CSV load failed, skipping.", "yellow")


def _prune_invalid_supply_chain_edges(session) -> int:
    invalid_a = _cypher_invalid_company_listing_predicate("a")
    invalid_b = _cypher_invalid_company_listing_predicate("b")
    total_retired = 0
    denylisted_pairs = [
        {"sup": left, "cust": right}
        for left, right in sorted(_load_edge_denylist().get("SUPPLIER_OF") or set())
        if left and right
    ]
    total_retired += _retire_relationships(
        session,
        "SUPPLIER_OF",
        f"any(p IN $denylisted_pairs WHERE "
        f"(a.ticker = p.sup AND b.ticker = p.cust) "
        f"OR (a.ticker = p.cust AND b.ticker = p.sup))",
        params={"denylisted_pairs": denylisted_pairs},
        directed=True,
        retired_reason="denylist",
    )
    total_retired += _retire_relationships(
        session,
        "SUPPLIER_OF",
        f"({invalid_a}) "
        f"OR ({invalid_b}) "
        f"OR (a.sic_code IS NOT NULL AND toInteger(a.sic_code) >= 6000 AND toInteger(a.sic_code) <= 6999) "
        f"OR (b.sic_code IS NOT NULL AND toInteger(b.sic_code) >= 6000 AND toInteger(b.sic_code) <= 6999)",
        directed=True,
        retired_reason="invalid_listing",
    )
    total_retired += _retire_relationships(
        session,
        "SUPPLIER_OF",
        "(a.issuer_key IS NOT NULL AND b.issuer_key IS NOT NULL AND a.issuer_key = b.issuer_key)",
        directed=True,
        retired_reason="entity_correction",
    )
    return total_retired


def _prune_invalid_company_nodes(session) -> int:
    invalid_c = _cypher_invalid_company_listing_predicate("c")
    total_deleted = 0
    while True:
        rec = session.run(f"""
            MATCH (c:Company)
            WHERE {invalid_c}
            WITH c LIMIT 5000
            DETACH DELETE c
            RETURN count(c) AS deleted
        """).single()
        deleted = int((rec or {}).get("deleted") or 0)
        total_deleted += deleted
        if deleted < 5000:
            break
    return total_deleted


def _prune_invalid_competes_edges(session) -> int:
    invalid_a = _cypher_invalid_company_listing_predicate("a")
    invalid_b = _cypher_invalid_company_listing_predicate("b")
    total_retired = 0
    total_retired += _retire_relationships(
        session,
        "COMPETES_WITH",
        f"({invalid_a}) OR ({invalid_b})",
        directed=True,
        retired_reason="invalid_listing",
    )
    total_retired += _retire_relationships(
        session,
        "COMPETES_WITH",
        "(a.issuer_key IS NOT NULL AND b.issuer_key IS NOT NULL AND a.issuer_key = b.issuer_key)",
        directed=True,
        retired_reason="entity_correction",
    )
    return total_retired


def _prune_invalid_strategic_partner_edges(session) -> int:
    invalid_a = _cypher_invalid_company_listing_predicate("a")
    invalid_b = _cypher_invalid_company_listing_predicate("b")
    total_retired = 0
    total_retired += _retire_relationships(
        session,
        "STRATEGIC_PARTNER",
        f"({invalid_a}) "
        f"OR ({invalid_b}) "
        f"OR (a.sic_code IS NOT NULL AND toInteger(a.sic_code) >= 6000 AND toInteger(a.sic_code) <= 6999) "
        f"OR (b.sic_code IS NOT NULL AND toInteger(b.sic_code) >= 6000 AND toInteger(b.sic_code) <= 6999)",
        directed=False,
        retired_reason="invalid_listing",
    )
    total_retired += _retire_relationships(
        session,
        "STRATEGIC_PARTNER",
        "(a.issuer_key IS NOT NULL AND b.issuer_key IS NOT NULL AND a.issuer_key = b.issuer_key)",
        directed=False,
        retired_reason="entity_correction",
    )
    return total_retired


def _supplier_edge_strength_key(props: dict | None) -> tuple:
    props = props or {}
    try:
        conf = float(props.get("confidence", props.get("conf")) or 0.0)
    except Exception:
        conf = 0.0
    src = str(props.get("source", props.get("src")) or "").lower()
    cross = 1 if ("cross-validated" in src or "direction-resolved" in src) else 0
    rev_raw = props.get("revenue_pct", props.get("rev"))
    try:
        rev = float(rev_raw) if rev_raw not in (None, "") else None
    except Exception:
        rev = None
    has_rev = 1 if rev is not None else 0
    last_confirmed = str(props.get("last_confirmed", props.get("lc")) or "")
    return (conf, cross, has_rev, rev if rev is not None else -1.0, last_confirmed)


def _dedupe_reciprocal_supply_chain_edge_candidates(edges: list[dict]) -> list[dict]:
    """Resolve reciprocal supplier candidates before they hit Neo4j."""
    supplier_index: dict[tuple[str, str], dict] = {}
    other_edges: list[dict] = []
    for edge in edges:
        candidate = dict(edge or {})
        etype = candidate.get("etype", "SUPPLIER_OF") or "SUPPLIER_OF"
        if etype != "SUPPLIER_OF":
            other_edges.append(candidate)
            continue
        key = (str(candidate.get("sup") or "").strip().upper(), str(candidate.get("cust") or "").strip().upper())
        if not key[0] or not key[1] or key[0] == key[1]:
            continue
        candidate["sup"] = key[0]
        candidate["cust"] = key[1]
        prev = supplier_index.get(key)
        if prev is None or _supplier_edge_strength_key(candidate) > _supplier_edge_strength_key(prev):
            supplier_index[key] = candidate

    result: list[dict] = other_edges
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
        forward_strength = _supplier_edge_strength_key(edge)
        reverse_strength = _supplier_edge_strength_key(reverse)
        if forward_strength > reverse_strength:
            chosen = edge.copy()
            chosen["src"] = str(chosen.get("src") or "10-K") + "+direction-resolved"
            result.append(chosen)
        elif reverse_strength > forward_strength:
            chosen = reverse.copy()
            chosen["src"] = str(chosen.get("src") or "10-K") + "+direction-resolved"
            result.append(chosen)
        # Exact ties are dropped intentionally.
    return result


def _reconcile_reciprocal_supply_chain_edges(session, tickers: list[str] | None = None) -> dict[str, int]:
    """Resolve reciprocal SUPPLIER_OF pairs by keeping the stronger direction or dropping ties."""
    tickers = sorted({(t or "").strip().upper() for t in (tickers or []) if t})
    if tickers:
        pairs = list(session.run("""
            MATCH (a:Company)-[ab:SUPPLIER_OF {source_scope: 'SEC_10K_SUPPLIER', edge_state: 'open'}]->(b:Company)
            MATCH (b)-[ba:SUPPLIER_OF {source_scope: 'SEC_10K_SUPPLIER', edge_state: 'open'}]->(a)
            WHERE a.ticker < b.ticker
              AND (a.ticker IN $tickers OR b.ticker IN $tickers)
            RETURN elementId(ab) AS ab_id, elementId(ba) AS ba_id,
                   properties(ab) AS ab_props, properties(ba) AS ba_props
        """, tickers=tickers))
    else:
        pairs = list(session.run("""
            MATCH (a:Company)-[ab:SUPPLIER_OF {source_scope: 'SEC_10K_SUPPLIER', edge_state: 'open'}]->(b:Company)
            MATCH (b)-[ba:SUPPLIER_OF {source_scope: 'SEC_10K_SUPPLIER', edge_state: 'open'}]->(a)
            WHERE a.ticker < b.ticker
            RETURN elementId(ab) AS ab_id, elementId(ba) AS ba_id,
                   properties(ab) AS ab_props, properties(ba) AS ba_props
        """))
    if not pairs:
        return {"pairs": 0, "deleted_total": 0, "resolved_directional": 0, "dropped_ambiguous": 0}

    retire_ids: list[str] = []
    resolved_directional = 0
    dropped_ambiguous = 0
    for rec in pairs:
        ab_strength = _supplier_edge_strength_key(rec.get("ab_props"))
        ba_strength = _supplier_edge_strength_key(rec.get("ba_props"))
        if ab_strength > ba_strength:
            retire_ids.append(rec["ba_id"])
            resolved_directional += 1
        elif ba_strength > ab_strength:
            retire_ids.append(rec["ab_id"])
            resolved_directional += 1
        else:
            retire_ids.extend([rec["ab_id"], rec["ba_id"]])
            dropped_ambiguous += 1

    close_date = datetime.now(timezone.utc).date().isoformat()
    for start in range(0, len(retire_ids), 500):
        chunk = retire_ids[start:start + 500]
        session.run("""
            MATCH ()-[r:SUPPLIER_OF]->()
            WHERE elementId(r) IN $rel_ids
            SET r.history_id = coalesce(r.history_id, randomUUID()),
                r.edge_state = 'closed',
                r.valid_until = CASE
                    WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                    ELSE $close_date
                END,
                r.retired_reason = 'direction_conflict',
                r.closed_at = $close_date,
                r.current_run_token = NULL
        """, rel_ids=chunk, close_date=close_date).consume()

    return {
        "pairs": len(pairs),
        "deleted_total": len(retire_ids),
        "resolved_directional": resolved_directional,
        "dropped_ambiguous": dropped_ambiguous,
    }


def _purge_closed_supply_chain_direction_conflicts(session, tickers: list[str] | None = None, batch_limit: int = 500) -> int:
    """Drop closed reciprocal-conflict relationships after interval history has been synced."""
    tickers = sorted({(t or "").strip().upper() for t in (tickers or []) if t})
    total_deleted = 0
    while True:
        rec = session.run(
            """
            MATCH (a:Company)-[r:SUPPLIER_OF {source_scope: 'SEC_10K_SUPPLIER', edge_state: 'closed'}]->(b:Company)
            WHERE coalesce(r.retired_reason, '') = 'direction_conflict'
              AND ($tickers = [] OR a.ticker IN $tickers OR b.ticker IN $tickers)
            WITH r LIMIT $batch_limit
            DELETE r
            RETURN count(r) AS deleted
            """,
            tickers=tickers,
            batch_limit=batch_limit,
        ).single()
        deleted = int((rec or {}).get("deleted") or 0)
        total_deleted += deleted
        if deleted < batch_limit:
            break
    return total_deleted


def apply_supply_chain_edges(session, edges) -> int:
    """Merge supply-chain edges (list[EdgeRecord] or list[tuple[str,str]]). Uploads each 50-edge batch to Neo4j.
    Only creates edges between Company nodes that already exist in the graph (MATCH, not MERGE).
    Edges below SUPPLY_CHAIN_MIN_CONFIDENCE are discarded to avoid noise."""
    today = datetime.now(timezone.utc).date().isoformat()
    batch: list[dict] = []
    batch_size = 50
    count = 0
    touched_supplier_tickers: set[str] = set()
    saw_supplier_edge = False

    def _flush(b: list[dict]) -> int:
        if not b:
            return 0
        # Filter to minimum confidence — discard noisy edges before writing
        filtered = [p for p in b if p.get("conf", 0.7) >= SUPPLY_CHAIN_MIN_CONFIDENCE]
        if not filtered:
            return 0
        valid_a = _cypher_valid_company_listing_predicate("a")
        valid_b = _cypher_valid_company_listing_predicate("b")
        # Split by edge_type — Neo4j relationship types cannot be parameterised.
        # MATCH (not MERGE) for Company nodes: only create edges between companies that
        # already exist in the graph (Phase 1). Prevents phantom Company nodes from noise.
        from collections import defaultdict
        by_etype: dict[str, list[dict]] = defaultdict(list)
        for p in filtered:
            by_etype[p.get("etype", "SUPPLIER_OF")].append(p)

        n_written = 0
        for etype, typed_batch in by_etype.items():
            if etype == "STRATEGIC_PARTNER":
                query = """
                    UNWIND $batch AS p
                    WITH p.sup AS sup, p.cust AS cust, p.conf AS conf,
                         p.src AS src, p.lc AS lc, p.aa AS aa
                    WHERE sup IS NOT NULL AND cust IS NOT NULL AND sup <> cust
                    MATCH (a:Company {ticker: sup})
                    MATCH (b:Company {ticker: cust})
                    WHERE
                        __VALID_A__
                        AND __VALID_B__
                        AND (a.issuer_key IS NULL OR b.issuer_key IS NULL OR a.issuer_key <> b.issuer_key)
                        AND
                        (a.sic_code IS NULL OR toInteger(a.sic_code) < 6000 OR toInteger(a.sic_code) > 6999)
                        AND (b.sic_code IS NULL OR toInteger(b.sic_code) < 6000 OR toInteger(b.sic_code) > 6999)
                        AND NOT EXISTS { (a)-[:SUPPLIER_OF]-(b) }
                    MERGE (a)-[r:STRATEGIC_PARTNER {source_scope: 'SEC_10K_STRATEGIC_PARTNER', edge_state: 'open'}]-(b)
                    ON CREATE SET r.confidence = conf, r.source = src,
                                  r.last_confirmed = lc, r.active_after = aa,
                                  r.created_at = CASE WHEN aa <> '' THEN aa WHEN lc <> '' THEN lc ELSE $today END,
                                  r.valid_until = NULL, r.retired_reason = NULL
                    ON MATCH  SET r.confidence = CASE WHEN conf > r.confidence THEN conf ELSE r.confidence END,
                                  r.last_confirmed = CASE
                                      WHEN lc = '' THEN r.last_confirmed
                                      WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR lc > r.last_confirmed THEN lc
                                      ELSE r.last_confirmed
                                  END,
                                  r.active_after = CASE
                                      WHEN aa = '' THEN r.active_after
                                      WHEN r.active_after IS NULL OR r.active_after = '' OR aa < r.active_after THEN aa
                                      ELSE r.active_after
                                  END,
                                  r.valid_until = NULL, r.retired_reason = NULL, r.edge_state = 'open'
                """
                query = query.replace("__VALID_A__", valid_a).replace("__VALID_B__", valid_b)
                session.run(query, batch=typed_batch, today=today).consume()
            else:
                query = """
                    UNWIND $batch AS p
                    WITH p.sup AS sup, p.cust AS cust, p.conf AS conf,
                         p.src AS src, p.rev AS rev, p.lc AS lc, p.aa AS aa
                    WHERE sup IS NOT NULL AND cust IS NOT NULL AND sup <> cust
                    MATCH (a:Company {ticker: sup})
                    MATCH (b:Company {ticker: cust})
                    WHERE
                        __VALID_A__
                        AND __VALID_B__
                        AND (a.issuer_key IS NULL OR b.issuer_key IS NULL OR a.issuer_key <> b.issuer_key)
                        AND
                        (a.sic_code IS NULL OR toInteger(a.sic_code) < 6000 OR toInteger(a.sic_code) > 6999)
                        AND (b.sic_code IS NULL OR toInteger(b.sic_code) < 6000 OR toInteger(b.sic_code) > 6999)
                    MERGE (a)-[r:SUPPLIER_OF {source_scope: 'SEC_10K_SUPPLIER', edge_state: 'open'}]->(b)
                    ON CREATE SET r.confidence = conf, r.source = src,
                                  r.revenue_pct = rev, r.last_confirmed = lc, r.active_after = aa,
                                  r.created_at = CASE WHEN aa <> '' THEN aa WHEN lc <> '' THEN lc ELSE $today END,
                                  r.valid_until = NULL, r.retired_reason = NULL
                    ON MATCH  SET r.confidence = CASE WHEN conf > r.confidence THEN conf ELSE r.confidence END,
                                  r.last_confirmed = CASE
                                      WHEN lc = '' THEN r.last_confirmed
                                      WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR lc > r.last_confirmed THEN lc
                                      ELSE r.last_confirmed
                                  END,
                                  r.active_after = CASE
                                      WHEN aa = '' THEN r.active_after
                                      WHEN r.active_after IS NULL OR r.active_after = '' OR aa < r.active_after THEN aa
                                      ELSE r.active_after
                                  END,
                                  r.valid_until = NULL, r.retired_reason = NULL, r.edge_state = 'open'
                """
                query = query.replace("__VALID_A__", valid_a).replace("__VALID_B__", valid_b)
                session.run(query, batch=typed_batch, today=today).consume()
                if typed_batch:
                    touched_supplier_tickers.update(
                        {str(p.get("sup") or "").strip().upper() for p in typed_batch}
                        | {str(p.get("cust") or "").strip().upper() for p in typed_batch}
                    )
            n_written += len(typed_batch)
        return n_written

    normalized_edges: list[dict] = []
    for edge in edges:
        if isinstance(edge, dict):
            sup  = (edge.get("sup") or "").strip().upper()
            cust = (edge.get("cust") or "").strip().upper()
            conf = edge.get("confidence", 0.7)
            src  = edge.get("source", "10-K")
            rev  = edge.get("revenue_pct")
            lc   = _normalize_edge_date(edge.get("last_confirmed"))
            aa   = _normalize_edge_date(edge.get("active_after") or edge.get("last_confirmed"))
            etype = edge.get("edge_type", "SUPPLIER_OF") or "SUPPLIER_OF"
        else:
            sup  = (edge[0] or "").strip().upper()
            cust = (edge[1] or "").strip().upper()
            conf, src, rev, lc, aa, etype = 0.7, "10-K", None, "", "", "SUPPLIER_OF"
        if not sup or not cust or sup == cust:
            continue
        if _edge_pair_denied(etype, sup, cust):
            continue
        normalized_edges.append({"sup": sup, "cust": cust, "conf": conf, "src": src, "rev": rev, "lc": lc, "aa": aa, "etype": etype})

    normalized_edges = _dedupe_reciprocal_supply_chain_edge_candidates(normalized_edges)
    for edge in normalized_edges:
        if edge.get("etype", "SUPPLIER_OF") == "SUPPLIER_OF":
            saw_supplier_edge = True
        batch.append(edge)
        if len(batch) >= batch_size:
            count += _flush(batch)
            batch = []
    count += _flush(batch)
    if saw_supplier_edge and touched_supplier_tickers:
        try:
            _reconcile_reciprocal_supply_chain_edges(session, tickers=sorted(touched_supplier_tickers))
        except Exception as e:
            _log(f"Supply chain reciprocal cleanup error: {e}", "yellow")
    try:
        _sync_graph_edge_intervals(session, "SUPPLIER_OF", source_scope="SEC_10K_SUPPLIER", directed=True)
        _sync_graph_edge_intervals(session, "STRATEGIC_PARTNER", source_scope="SEC_10K_STRATEGIC_PARTNER", directed=False)
        if saw_supplier_edge:
            _purge_closed_supply_chain_direction_conflicts(session, tickers=sorted(touched_supplier_tickers))
    except Exception as e:
        _log(f"Supply chain interval sync error: {e}", "yellow")
    return count


def run_daily_supply_chain_update():
    """Run incremental SEC EDGAR scraper (last 24h) and merge new supply-chain edges."""
    if GRAPH_NEXUS_SUPPLY_CHAIN_SOURCE != "sec_edgar":
        _log("Daily update skipped (not sec_edgar source).", "white")
        return
    try:
        from sec_edgar_supply_chain import run_sec_edgar_incremental
    except ImportError as e:
        _log(f"SEC EDGAR incremental not available: {e}", "red")
        return
    driver = get_driver()
    if not driver:
        return
    _log("Running daily supply chain update (recent 10-K/10-K-A filings)...", "cyan")
    edges = run_sec_edgar_incremental(
        hours=GRAPH_NEXUS_LIVE_UPDATE_HOURS,
        output_csv_path=os.path.join(SUPPLY_CHAIN_CACHE_DIR, "supply_chain_sec_edgar.csv"),
        append_to_existing_csv=True,
    )
    if edges:
        with driver.session() as session:
            n = apply_supply_chain_edges(session, edges)
            try:
                reconciled = _reconcile_reciprocal_supply_chain_edges(session)
                if reconciled["deleted_total"]:
                    _log(
                        "Daily update: reciprocal cleanup removed "
                        f"{reconciled['deleted_total']} SUPPLIER_OF edge(s).",
                        "green",
                    )
            except Exception as e:
                _log_stage_error("Daily update", "reconcile reciprocal SUPPLIER_OF", str(e), e, color="yellow")
        _log(f"Daily update: merged {n} supply chain edges.", "green")
    else:
        _log("No new supply chain edges from recent filings.", "white")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 4: Competitive — sector + SIC sub-sector
# ──────────────────────────────────────────────────────────────────────────────
# No external API. Uses graph: IN_SECTOR, industry_group. Creates COMPETES_WITH between companies
# in the same sector and same SIC-derived industry_group.
# ──────────────────────────────────────────────────────────────────────────────
def phase4_competitive(session):
    """Phase 4: Create COMPETES_WITH edges between companies sharing the same fine-grained SIC industry_group.

    Only industry_group-level competition is created. The previous broad sector-level query
    (all companies in the same sector) was removed because it produced O(n²) meaningless edges
    for large sectors — e.g. 1,000 Tech companies → ~500,000 edges that are semantically useless.

    The fine-grained query is rewritten to avoid a Cartesian product: it groups companies by
    industry_group in Python and issues one batched Cypher UNWIND per group.
    """
    _nexus_stage_reset()
    _progress(PHASE_PCT[5], "Phase 4: Competitive — COMPETES_WITH by SIC industry_group...", "cyan")
    try:
        pruned = _prune_invalid_competes_edges(session)
        if pruned:
            _log(f"Phase 4: retired {pruned} stale/invalid COMPETES_WITH interval(s) before rebuild.", "green")
    except Exception as e:
        _log_stage_error("Phase 4: Competitive", "prune invalid COMPETES_WITH", str(e), e, color="yellow")

    # Fetch all companies that have an industry_group set
    valid_c = _cypher_valid_company_listing_predicate("c")
    r = session.run(f"""
        MATCH (c:Company)
        WHERE c.industry_group IS NOT NULL AND c.industry_group <> ''
          AND {valid_c}
        RETURN c.ticker AS ticker, c.industry_group AS ig, c.issuer_key AS issuer_key
    """)
    group_to_tickers: dict[str, list[str]] = {}
    seen_group_keys: dict[str, set[str]] = {}
    for rec in r:
        ig     = rec.get("ig") or ""
        ticker = rec.get("ticker") or ""
        issuer_key = rec.get("issuer_key") or ticker
        if ig and ticker:
            seen_group_keys.setdefault(ig, set())
            if issuer_key in seen_group_keys[ig]:
                continue
            seen_group_keys[ig].add(issuer_key)
            group_to_tickers.setdefault(ig, []).append(ticker)

    # Cap: skip groups that are too large (> MAX_GROUP_SIZE companies) to avoid edge explosion.
    # A group of 500 companies would produce 124,750 pairs — we cap at 200 (19,900 pairs).
    MAX_GROUP_SIZE = int(os.environ.get("COMPETES_WITH_MAX_GROUP_SIZE", "200"))
    fine_count = 0
    batch_pairs: list[dict] = []
    batch_size = 500
    phase_run_token = f"phase4:{uuid4().hex}"
    closed = 0
    # Structural relationship — COMPETES_WITH is a fact derived from SIC codes, not a dated event.
    # Use historical anchor so edges are visible during backtesting (as_of_date < today).
    phase_active_after = "2000-01-01"

    def _flush_pairs(pairs: list[dict]) -> int:
        if not pairs:
            return 0
        session.run("""
            UNWIND $batch AS p
            MATCH (a:Company {ticker: p.ta}), (b:Company {ticker: p.tb})
            MERGE (a)-[r1:COMPETES_WITH {source_scope: 'SIC_COMPETITIVE', edge_state: 'open'}]->(b)
            ON CREATE SET r1.basis = 'industry_group', r1.source = 'SIC sub-sector',
                          r1.industry_group = p.ig, r1.active_after = $today, r1.last_confirmed = $today,
                          r1.valid_until = NULL, r1.retired_reason = NULL
            ON MATCH  SET r1.basis = 'industry_group', r1.industry_group = p.ig,
                          r1.active_after = $today,
                          r1.last_confirmed = $today,
                          r1.valid_until = NULL, r1.retired_reason = NULL,
                          r1.edge_state = 'open', r1.current_run_token = $run_token
            SET r1.current_run_token = $run_token
            MERGE (b)-[r2:COMPETES_WITH {source_scope: 'SIC_COMPETITIVE', edge_state: 'open'}]->(a)
            ON CREATE SET r2.basis = 'industry_group', r2.source = 'SIC sub-sector',
                          r2.industry_group = p.ig, r2.active_after = $today, r2.last_confirmed = $today,
                          r2.valid_until = NULL, r2.retired_reason = NULL
            ON MATCH  SET r2.basis = 'industry_group', r2.industry_group = p.ig,
                          r2.active_after = $today,
                          r2.last_confirmed = $today,
                          r2.valid_until = NULL, r2.retired_reason = NULL,
                          r2.edge_state = 'open', r2.current_run_token = $run_token
            SET r2.current_run_token = $run_token
        """, batch=pairs, run_token=phase_run_token, today=phase_active_after).consume()
        return len(pairs) * 2

    skipped_groups = 0
    for ig, tickers in group_to_tickers.items():
        if len(tickers) > MAX_GROUP_SIZE:
            skipped_groups += 1
            _log(f"Phase 4: industry_group '{ig}' has {len(tickers)} companies — skipping to avoid edge explosion (cap={MAX_GROUP_SIZE}). Raise COMPETES_WITH_MAX_GROUP_SIZE to include.", "yellow")
            continue
        for i in range(len(tickers)):
            for j in range(i + 1, len(tickers)):
                ta, tb = (tickers[i], tickers[j]) if tickers[i] < tickers[j] else (tickers[j], tickers[i])
                batch_pairs.append({"ta": ta, "tb": tb, "ig": ig})
                if len(batch_pairs) >= batch_size:
                    fine_count += _flush_pairs(batch_pairs)
                    batch_pairs = []

    fine_count += _flush_pairs(batch_pairs)
    if fine_count > 0:
        close_date = datetime.now(timezone.utc).date().isoformat()
        rec = session.run("""
            MATCH (:Company)-[r:COMPETES_WITH {source_scope: 'SIC_COMPETITIVE', edge_state: 'open'}]->(:Company)
            WHERE coalesce(r.current_run_token, '') <> $run_token
            SET r.edge_state = 'closed',
                r.valid_until = CASE
                    WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                    WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                    ELSE $close_date
                END,
                r.retired_reason = 'superseded',
                r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
                r.current_run_token = NULL
            RETURN count(r) AS closed
        """, run_token=phase_run_token, close_date=close_date).single()
        closed = int((rec or {}).get("closed") or 0)

    if skipped_groups:
        _log(f"Phase 4: skipped {skipped_groups} oversized industry groups.", "yellow")
    _log(f"Phase 4: {fine_count} COMPETES_WITH edges by SIC industry_group.", "white")
    if closed:
        _log(f"Phase 4: closed {closed} superseded COMPETES_WITH interval(s).", "green")
    try:
        _sync_graph_edge_intervals(session, "COMPETES_WITH", source_scope="SIC_COMPETITIVE", directed=True)
    except Exception as e:
        _log_stage_error("Phase 4: Competitive", "sync COMPETES_WITH interval history", str(e), e, color="yellow")
    try:
        retired_legacy = _retire_relationships(
            session,
            "COMPETES_WITH",
            "coalesce(r.source_scope, '') = ''",
            directed=True,
            retired_reason="legacy_replaced",
        )
        if retired_legacy:
            _log(f"Phase 4: retired {retired_legacy} legacy COMPETES_WITH edge(s) after replacement.", "green")
    except Exception as e:
        _log_stage_error("Phase 4: Competitive", "retire legacy COMPETES_WITH", str(e), e, color="yellow")
    _progress(PHASE_PCT[6], f"Phase 4 done: {fine_count} COMPETES_WITH edges.", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 5: Macro / commodity + BEA Input-Output
# ──────────────────────────────────────────────────────────────────────────────
# BEA API: https://apps.bea.gov/api/data (GET)
#   GetData params: UserID, method=GetData, datasetname=InputOutput, TableID=259, Year, ResultFormat=json
#   TableID 259 = Use table (after redefinitions), sector-level. Response: BEAAPI.Results is a list;
#   data rows are in Results[0].Data; each row has RowDescr, ColDescr, DataValue (and RowCode, ColCode, Year).
# ──────────────────────────────────────────────────────────────────────────────
def _fetch_bea_io_rows(table_id: str, cache_name: str, label: str) -> list[dict]:
    """
    Fetch a BEA Input-Output table via the BEA API.
    Returns list of row dicts with RowDescr, ColDescr, DataValue (and RowCode, ColCode, Year).
    Uses cache (historic fixed-year data: use whenever cached).
    """
    if not BEA_API_KEY:
        return []
    cache_path = _nexus_cache_path("phase5", cache_name)
    # BEA tables are annually revised - 30-day TTL (vs the old "never expire").
    cached = _nexus_read_cached_json(cache_path, _nexus_cache_max_age("phase5"))
    if isinstance(cached, list) and cached:
        _nexus_stage_cache_hit(1)
        return cached
    import requests
    rows = []
    # Newest-first iteration so a refresh picks up BEA's latest revision year.
    # Prior code tried a fixed list starting at 2024 but once cache was warm
    # at e.g. 2022, every subsequent run hit the cache and never re-attempted
    # newer years. Now with proper cache TTL, each refresh actually re-polls.
    bea_year_candidates = os.environ.get("NEXUS_BEA_YEARS", "2025,2024,2023,2022,2021").split(",")
    bea_min_year = int(os.environ.get("NEXUS_BEA_MIN_YEAR", "2021"))
    tried: list[str] = []
    for year_raw in bea_year_candidates:
        year = str(year_raw).strip()
        if not year or not year.isdigit():
            continue
        if int(year) < bea_min_year:
            continue
        tried.append(year)
        try:
            r = requests.get(
                "https://apps.bea.gov/api/data",
                params={
                    "UserID": BEA_API_KEY,
                    "method": "GetData",
                    "datasetname": "InputOutput",
                    "TableID": table_id,
                    "Year": year,
                    "ResultFormat": "json",
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            results = (data.get("BEAAPI") or {}).get("Results")
            if isinstance(results, list) and results:
                rows = (results[0].get("Data") or []) if isinstance(results[0], dict) else []
            else:
                rows = (results.get("Data") or []) if isinstance(results, dict) else []
            if rows:
                for row in rows:
                    if isinstance(row, dict):
                        row["_source_year"] = row.get("_source_year") or row.get("Year") or year
                _log(f"Phase 5: {label} year {year}, {len(rows)} rows (tried {'/'.join(tried)}).", "white")
                break
        except Exception as e:
            _log(f"Phase 5: {label} year {year} failed: {e}", "yellow")
            rows = []
    if not rows and tried:
        _log(f"Phase 5: {label} no data for any year in {','.join(tried)}; keeping prior cache if present.", "yellow")
    elif not rows and not tried:
        _log(
            f"Phase 5: {label} MISCONFIGURED — NEXUS_BEA_YEARS={bea_year_candidates!r} "
            f"has no years >= NEXUS_BEA_MIN_YEAR={bea_min_year}. Nothing was attempted. "
            f"Check env vars.",
            "red",
        )
    try:
        if rows:
            _nexus_write_cached_json(cache_path, rows)
            _nexus_stage_download(1)
        return rows
    except Exception as e:
        _log_stage_error("Phase 5: Macro/BEA", f"fetch {label}", str(e), e, color="yellow")
        return []


def _fetch_bea_io_table() -> list[dict]:
    return _fetch_bea_io_rows("259", "bea_io_table.json", "BEA I-O sector table")


def _fetch_bea_io_commodity_table() -> list[dict]:
    return _fetch_bea_io_rows("258", "bea_io_commodity_table_258.json", "BEA I-O commodity-use table")


def _phase5_bea_industry_like_code(code: str) -> bool:
    code = str(code or "").strip()
    if not code:
        return False
    upper = code.upper()
    if upper.startswith(("T", "V")):
        return False
    if re.match(r"^F\d", upper):
        return False
    return upper not in {"OTHER", "USED"}


def _phase5_bea_is_commodity_row(row: dict) -> bool:
    row_code = str(row.get("RowCode") or "").strip()
    row_descr = str(row.get("RowDescr") or row_code).strip().lower()
    if not _phase5_bea_industry_like_code(row_code):
        return False
    return not any(
        needle in row_descr
        for needle in (
            "noncomparable imports",
            "rest-of-the-world adjustment",
            "scrap",
            "used and secondhand",
            "total intermediate",
            "total industry output",
            "compensation of employees",
            "gross operating surplus",
            "value added",
            "taxes on products",
            "subsidies on production",
        )
    )


def _phase5_bea_is_sector_column(row: dict) -> bool:
    col_code = str(row.get("ColCode") or "").strip()
    col_descr = str(row.get("ColDescr") or col_code).strip().lower()
    if not _phase5_bea_industry_like_code(col_code):
        return False
    return not any(
        needle in col_descr
        for needle in (
            "total intermediate",
            "total use of products",
            "personal consumption expenditures",
            "private fixed investment",
            "change in private inventories",
            "exports of goods and services",
            "government consumption expenditures",
        )
    )


def _phase5_bea_is_sector_row(row: dict) -> bool:
    row_code = str(row.get("RowCode") or "").strip()
    row_descr = str(row.get("RowDescr") or row_code).strip().lower()
    if not _phase5_bea_industry_like_code(row_code):
        return False
    return not any(
        needle in row_descr
        for needle in (
            "noncomparable imports",
            "rest-of-the-world adjustment",
            "scrap",
            "used and secondhand",
            "total intermediate",
            "total industry output",
            "total use of products",
            "compensation of employees",
            "gross operating surplus",
            "value added",
            "taxes on products",
            "subsidies on production",
            "personal consumption expenditures",
            "private fixed investment",
            "change in private inventories",
            "exports of goods and services",
            "government consumption expenditures",
        )
    )


def _phase5_build_bea_commodity_exposure_batch(bea_rows: list[dict], sector_mapper) -> tuple[list[dict], list[dict], dict[str, int]]:
    commodity_nodes: dict[str, dict] = {}
    exposures: dict[tuple[str, str], dict] = {}
    stats = {
        "skipped_non_commodity_rows": 0,
        "skipped_non_sector_columns": 0,
        "skipped_other_sector": 0,
    }
    for row in bea_rows:
        if not isinstance(row, dict):
            continue
        if not _phase5_bea_is_commodity_row(row):
            stats["skipped_non_commodity_rows"] += 1
            continue
        if not _phase5_bea_is_sector_column(row):
            stats["skipped_non_sector_columns"] += 1
            continue
        row_code = str(row.get("RowCode") or "").strip()
        row_descr = (str(row.get("RowDescr") or row_code).strip() or row_code)[:100]
        col_descr = str(row.get("ColDescr") or row.get("ColCode") or "").strip()
        val_str = str(row.get("DataValue") or "0").replace(",", "")
        try:
            flow_value = float(val_str)
        except Exception:
            continue
        if flow_value < 1.0:
            continue
        sector_name = sector_mapper(col_descr)
        if sector_name == "Other":
            stats["skipped_other_sector"] += 1
            continue
        commodity_id = f"bea_io_commodity:{row_code}"
        active_after = _bea_row_active_after(row)
        commodity_nodes[commodity_id] = {
            "id": commodity_id,
            "name": row_descr,
            "source_code": row_code,
            "active_after": active_after,
        }
        key = (sector_name, commodity_id)
        prev = exposures.get(key)
        if prev is None:
            exposures[key] = {
                "sector": sector_name,
                "id": commodity_id,
                "val": flow_value,
                "active_after": active_after,
            }
        else:
            prev["val"] += flow_value
            if active_after and (not prev.get("active_after") or active_after < prev["active_after"]):
                prev["active_after"] = active_after
    return (
        sorted(commodity_nodes.values(), key=lambda item: item["id"]),
        sorted(exposures.values(), key=lambda item: (item["sector"], item["id"])),
        stats,
    )


def _phase5_build_sector_supply_batch(bea_rows: list[dict], sector_mapper) -> tuple[list[dict], int]:
    batch: list[dict] = []
    coarse_rows_skipped = 0
    for row in bea_rows:
        if not _phase5_bea_is_sector_row(row) or not _phase5_bea_is_sector_column(row):
            coarse_rows_skipped += 1
            continue
        from_bea = (row.get("RowDescr") or row.get("RowCode") or "").strip()[:100]
        to_bea = (row.get("ColDescr") or row.get("ColCode") or "").strip()[:100]
        val_str = str(row.get("DataValue") or "0").replace(",", "")
        try:
            flow_value = float(val_str)
        except Exception:
            continue
        if not from_bea or not to_bea or flow_value < 1.0:
            continue
        from_sector = sector_mapper(from_bea)
        to_sector = sector_mapper(to_bea)
        if from_sector == "Other" or to_sector == "Other":
            coarse_rows_skipped += 1
            continue
        if from_sector == to_sector:
            continue
        batch.append({
            "from_s": from_sector,
            "to_s": to_sector,
            "val": flow_value,
            "active_after": _bea_row_active_after(row),
        })

    rolled_up: dict[tuple[str, str], dict] = {}
    for item in batch:
        key = (item["from_s"], item["to_s"])
        prev = rolled_up.get(key)
        if prev is None:
            rolled_up[key] = dict(item)
            continue
        prev["val"] += item["val"]
        if item["active_after"] and (not prev.get("active_after") or item["active_after"] < prev["active_after"]):
            prev["active_after"] = item["active_after"]

    return (
        sorted(rolled_up.values(), key=lambda item: (item["from_s"], item["to_s"])),
        coarse_rows_skipped,
    )


def phase5_macro(session):
    _nexus_stage_reset()
    _progress(PHASE_PCT[6], "Phase 5: Macro — BEA commodity + I-O sector exposure...", "cyan")

    # BEA Input-Output sector linkages — batched UNWIND
    # BEA I-O uses long descriptive sector names (e.g. "Agriculture, Forestry, Fishing and Hunting")
    # while Phase 1 Sector nodes use short GICS-aligned names (e.g. "Agriculture").
    # We map BEA names to Phase 1 names so the SUPPLIES_TO_SECTOR edges connect to existing
    # Sector nodes instead of creating isolated BEA-only Sector islands.
    _BEA_TO_SECTOR: dict[str, str] = {
        "finance, insurance, real estate, rental, and leasing": "Financials",
        "educational services, health care, and social assistance": "Healthcare",
        "administrative and support": "Industrials",
        "agriculture": "Agriculture",
        "ambulatory": "Healthcare",
        "arts": "Consumer Discretionary",
        "forestry": "Agriculture",
        "fishing": "Agriculture",
        "credit intermediation": "Financials",
        "couriers": "Industrials",
        "data processing": "Technology",
        "depository credit": "Financials",
        "durable goods": "Industrials",
        "food services": "Consumer Discretionary",
        "funds trusts": "Financials",
        "housing": "Real Estate",
        "internet": "Communication Services",
        "leasing": "Industrials",
        "merchant wholesalers": "Industrials",
        "mining": "Materials",
        "miscellaneous store": "Consumer Discretionary",
        "nondurable goods": "Consumer Staples",
        "nursing": "Healthcare",
        "oil": "Energy",
        "gas": "Energy",
        "petroleum": "Energy",
        "pipeline": "Energy",
        "utilities": "Utilities",
        "electric": "Utilities",
        "construction": "Industrials",
        "manufacturing": "Industrials",
        "machinery": "Industrials",
        "transportation": "Industrials",
        "wholesale": "Industrials",
        "chemicals": "Materials",
        "metals": "Materials",
        "wood": "Materials",
        "paper": "Materials",
        "plastics": "Materials",
        "food": "Consumer Staples",
        "beverage": "Consumer Staples",
        "tobacco": "Consumer Staples",
        "retail": "Consumer Discretionary",
        "apparel": "Consumer Discretionary",
        "textile": "Consumer Discretionary",
        "furniture": "Consumer Discretionary",
        "entertainment": "Consumer Discretionary",
        "accommodation": "Consumer Discretionary",
        "motor vehicle": "Consumer Discretionary",
        "information": "Communication Services",
        "publishing": "Communication Services",
        "broadcasting": "Communication Services",
        "telecommunications": "Communication Services",
        "finance": "Financials",
        "insurance": "Financials",
        "banking": "Financials",
        "securities": "Financials",
        "real estate": "Real Estate",
        "rental": "Industrials",
        "repair": "Industrials",
        "restaurants": "Consumer Discretionary",
        "recreation": "Consumer Discretionary",
        "social assistance": "Healthcare",
        "health": "Healthcare",
        "pharmaceutical": "Healthcare",
        "medical": "Healthcare",
        "education": "Consumer Discretionary",
        "computer": "Technology",
        "software": "Technology",
        "electronic": "Technology",
        "semiconductor": "Technology",
        "government": "Government",
        "federal": "Government",
        "state": "Government",
        "management": "Industrials",
        "professional": "Industrials",
        "scientific": "Technology",
        "technical": "Technology",
        "transit": "Industrials",
        "truck": "Industrials",
        "warehousing": "Industrials",
        "water transportation": "Industrials",
        "waste": "Industrials",
        "support": "Industrials",
    }

    def _map_bea_to_sector(bea_name: str) -> str:
        """Map a BEA I-O sector description to a Phase 1 Sector node name."""
        lower = bea_name.lower()
        if re.sub(r"\s+", " ", lower).strip() == "other services, except government":
            return "Other"
        for keyword, sector in _BEA_TO_SECTOR.items():
            if keyword in lower:
                return sector
        return "Other"

    if BEA_API_KEY:
        _log("Phase 5: Fetching BEA commodity-use table for sector commodity exposures...", "cyan")
        commodity_rows = _fetch_bea_io_commodity_table()
        if commodity_rows:
            commodity_nodes, commodity_batch, commodity_stats = _phase5_build_bea_commodity_exposure_batch(
                commodity_rows,
                _map_bea_to_sector,
            )
            commodity_run_token = f"phase5:commodity:{uuid4().hex}"
            commodity_count = 0
            if commodity_nodes:
                for start in range(0, len(commodity_nodes), 200):
                    chunk = commodity_nodes[start:start + 200]
                    session.run("""
                        UNWIND $batch AS p
                        MERGE (m:Commodity {id: p.id})
                        SET m.name = p.name,
                            m.source = 'BEA I-O Commodity Use',
                            m.source_scope = 'BEA_IO_COMMODITY',
                            m.source_code = p.source_code,
                            m.last_confirmed = CASE
                                WHEN p.active_after IS NULL OR p.active_after = '' THEN m.last_confirmed
                                ELSE p.active_after
                            END
                    """, batch=chunk).consume()
            if commodity_batch:
                sector_names = sorted({item["sector"] for item in commodity_batch})
                for sector_name in sector_names:
                    session.run("MERGE (s:Sector {name: $name})", name=sector_name).consume()
                for start in range(0, len(commodity_batch), 200):
                    chunk = commodity_batch[start:start + 200]
                    session.run("""
                        UNWIND $batch AS p
                        MATCH (sec:Sector {name: p.sector})
                        MATCH (m:Commodity {id: p.id})
                        MERGE (sec)-[r:EXPOSED_TO {source_scope: 'BEA_IO_COMMODITY', edge_state: 'open'}]->(m)
                        ON CREATE SET r.flow_value = p.val,
                                      r.source = 'BEA I-O Commodity Use',
                                      r.active_after = p.active_after,
                                      r.last_confirmed = p.active_after
                        ON MATCH SET r.flow_value = CASE WHEN p.val > r.flow_value THEN p.val ELSE r.flow_value END,
                                      r.active_after = CASE
                                          WHEN p.active_after IS NULL OR p.active_after = '' THEN r.active_after
                                          WHEN r.active_after IS NULL OR r.active_after = '' OR p.active_after < r.active_after THEN p.active_after
                                          ELSE r.active_after
                                      END,
                                      r.last_confirmed = CASE
                                          WHEN p.active_after IS NULL OR p.active_after = '' THEN r.last_confirmed
                                          WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR p.active_after > r.last_confirmed THEN p.active_after
                                          ELSE r.last_confirmed
                                      END,
                                      r.valid_until = NULL,
                                      r.retired_reason = NULL,
                                      r.edge_state = 'open',
                                      r.current_run_token = $run_token
                        SET r.current_run_token = $run_token
                    """, batch=chunk, run_token=commodity_run_token).consume()
                    commodity_count += len(chunk)
                close_date = max(
                    (_normalize_edge_date(item.get("active_after")) for item in commodity_batch if _normalize_edge_date(item.get("active_after"))),
                    default=datetime.now(timezone.utc).date().isoformat(),
                )
                rec = session.run("""
                    MATCH (:Sector)-[r:EXPOSED_TO {source_scope: 'BEA_IO_COMMODITY', edge_state: 'open'}]->(:Commodity)
                    WHERE coalesce(r.current_run_token, '') <> $run_token
                    SET r.edge_state = 'closed',
                        r.valid_until = CASE
                            WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                            WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                            ELSE $close_date
                        END,
                        r.retired_reason = 'superseded',
                        r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
                        r.current_run_token = NULL
                    RETURN count(r) AS closed
                """, run_token=commodity_run_token, close_date=close_date).single()
                closed_commodity = int((rec or {}).get("closed") or 0)
                _log(
                    f"Phase 5: {len(commodity_nodes)} BEA commodity nodes ensured, {commodity_count} EXPOSED_TO edges created.",
                    "green",
                )
                if commodity_stats["skipped_non_commodity_rows"]:
                    _log(
                        f"Phase 5: skipped {commodity_stats['skipped_non_commodity_rows']} non-commodity BEA row(s).",
                        "white",
                    )
                if commodity_stats["skipped_non_sector_columns"]:
                    _log(
                        f"Phase 5: skipped {commodity_stats['skipped_non_sector_columns']} non-sector BEA column row(s).",
                        "white",
                    )
                if commodity_stats["skipped_other_sector"]:
                    _log(
                        f"Phase 5: skipped {commodity_stats['skipped_other_sector']} commodity row(s) that only mapped to Other.",
                        "white",
                    )
                if closed_commodity:
                    _log(f"Phase 5: closed {closed_commodity} superseded EXPOSED_TO interval(s).", "green")
                try:
                    _sync_graph_edge_intervals(session, "EXPOSED_TO", source_scope="BEA_IO_COMMODITY", directed=True)
                except Exception as e:
                    _log_stage_error("Phase 5: Macro/BEA", "sync EXPOSED_TO interval history", str(e), e, color="yellow")
                try:
                    retired_legacy_commodity = _retire_relationships(
                        session,
                        "EXPOSED_TO",
                        "coalesce(r.source_scope, '') IN ['', 'COMMODITIES_CSV']",
                        directed=True,
                        retired_reason="legacy_replaced",
                    )
                    if retired_legacy_commodity:
                        _log(
                            f"Phase 5: retired {retired_legacy_commodity} legacy EXPOSED_TO edge(s) after replacement.",
                            "green",
                        )
                except Exception as e:
                    _log_stage_error("Phase 5: Macro/BEA", "retire legacy EXPOSED_TO", str(e), e, color="yellow")
            else:
                _log_stage_unexpected("Phase 5: Macro/BEA", "BEA commodity exposure build", "BEA commodity-use table returned no usable sector/commodity rows.")
        else:
            _log_stage_unexpected("Phase 5: Macro/BEA", "BEA commodity fetch", "BEA commodity-use API returned no rows.")
    else:
        _log_stage_unexpected("Phase 5: Macro/BEA", "BEA commodity API", "BEA_API_KEY not set; skipping commodity exposures.")

    if BEA_API_KEY:
        _log("Phase 5: Fetching BEA Input-Output table for sector supply linkages...", "cyan")
        bea_rows = _fetch_bea_io_table()
        if bea_rows:
            batch, coarse_rows_skipped = _phase5_build_sector_supply_batch(
                bea_rows,
                _map_bea_to_sector,
            )
            phase_run_token = f"phase5:{uuid4().hex}"
            # Ensure all BEA-mapped sector names exist as Sector nodes (Phase 1 may have only a few)
            sector_names = set()
            for item in batch:
                sector_names.add(item["from_s"])
                sector_names.add(item["to_s"])
            for s in sector_names:
                session.run("MERGE (s:Sector {name: $name})", name=s).consume()
            batch_size = 200
            count = 0
            for start in range(0, len(batch), batch_size):
                chunk = batch[start:start + batch_size]
                session.run("""
                    UNWIND $batch AS p
                    MATCH (a:Sector {name: p.from_s})
                    MATCH (b:Sector {name: p.to_s})
                    MERGE (a)-[r:SUPPLIES_TO_SECTOR {source_scope: 'BEA_IO', edge_state: 'open'}]->(b)
                    ON CREATE SET r.flow_value = p.val, r.source = 'BEA I-O', r.active_after = p.active_after,
                                  r.last_confirmed = p.active_after
                    ON MATCH  SET r.flow_value = CASE WHEN p.val > r.flow_value THEN p.val ELSE r.flow_value END,
                                  r.active_after = CASE
                                      WHEN p.active_after IS NULL OR p.active_after = '' THEN r.active_after
                                      WHEN r.active_after IS NULL OR r.active_after = '' OR p.active_after < r.active_after THEN p.active_after
                                      ELSE r.active_after
                                  END,
                                  r.last_confirmed = CASE
                                      WHEN p.active_after IS NULL OR p.active_after = '' THEN r.last_confirmed
                                      WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR p.active_after > r.last_confirmed THEN p.active_after
                                      ELSE r.last_confirmed
                                  END,
                                  r.valid_until = NULL,
                                  r.retired_reason = NULL,
                                  r.edge_state = 'open',
                                  r.current_run_token = $run_token
                    SET r.current_run_token = $run_token
                """, batch=chunk, run_token=phase_run_token).consume()
                count += len(chunk)
            if count > 0:
                close_date = max(
                    (_normalize_edge_date(item.get("active_after")) for item in batch if _normalize_edge_date(item.get("active_after"))),
                    default=datetime.now(timezone.utc).date().isoformat(),
                )
                rec = session.run("""
                    MATCH (:Sector)-[r:SUPPLIES_TO_SECTOR {source_scope: 'BEA_IO', edge_state: 'open'}]->(:Sector)
                    WHERE coalesce(r.current_run_token, '') <> $run_token
                    SET r.edge_state = 'closed',
                        r.valid_until = CASE
                            WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                            WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                            ELSE $close_date
                        END,
                        r.retired_reason = 'superseded',
                        r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
                        r.current_run_token = NULL
                    RETURN count(r) AS closed
                """, run_token=phase_run_token, close_date=close_date).single()
                closed = int((rec or {}).get("closed") or 0)
            _log(f"Phase 5: {len(sector_names)} BEA sector nodes ensured, {count} SUPPLIES_TO_SECTOR edges created.", "green")
            if coarse_rows_skipped:
                _log(f"Phase 5: skipped {coarse_rows_skipped} coarse BEA row(s) that only mapped to Other.", "white")
            if count > 0 and closed:
                _log(f"Phase 5: closed {closed} superseded SUPPLIES_TO_SECTOR interval(s).", "green")
            try:
                _sync_graph_edge_intervals(session, "SUPPLIES_TO_SECTOR", source_scope="BEA_IO", directed=True)
            except Exception as e:
                _log_stage_error("Phase 5: Macro/BEA", "sync SUPPLIES_TO_SECTOR interval history", str(e), e, color="yellow")
            try:
                retired_legacy = _retire_relationships(
                    session,
                    "SUPPLIES_TO_SECTOR",
                    "coalesce(r.source_scope, '') = ''",
                    directed=True,
                    retired_reason="legacy_replaced",
                )
                if retired_legacy:
                    _log(f"Phase 5: retired {retired_legacy} legacy SUPPLIES_TO_SECTOR edge(s) after replacement.", "green")
            except Exception as e:
                _log_stage_error("Phase 5: Macro/BEA", "retire legacy SUPPLIES_TO_SECTOR", str(e), e, color="yellow")
        else:
            _log_stage_unexpected("Phase 5: Macro/BEA", "BEA I-O fetch", "BEA I-O API returned no rows.")
    else:
        _log_stage_unexpected("Phase 5: Macro/BEA", "BEA I-O", "BEA_API_KEY not set; skipping (get key at apps.bea.gov/API/signup).")

    _progress(PHASE_PCT[7], "Phase 5 done: macro/commodity/BEA I-O.", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 6: GLEIF LEI corporate hierarchy
# ──────────────────────────────────────────────────────────────────────────────
# Resources: GLEIF API (https://api.gleif.org/docs)
#   - Search: GET https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]=<name>&page[size]=1
#   - Direct parent: GET https://api.gleif.org/api/v1/lei-records/{lei}/direct-parent (404 = no parent)
#   - Ultimate parent: GET https://api.gleif.org/api/v1/lei-records/{lei}/ultimate-parent (404 = no parent)
# No API key required; rate-limit ~40 req/min. 404 on parent endpoints is normal for top-level entities.
# ──────────────────────────────────────────────────────────────────────────────
def _phase6_log_progress(
    i: int,
    total: int,
    companies: list,
    leis_found: int,
    gleif_no_parent_data_count: int,
    companies_with_parent_data: int,
    legal_entities_written: int,
    created: int,
    pending_edges: list,
    no_lei_count: int,
    batch_ok: int,
    batch_fail: int,
    total_calls_ok: int,
    total_calls_fail: int,
) -> tuple[int, int, int, int]:
    """Log Phase 6 progress every 20 companies: last 20 succeeded/failed and cumulative. Returns (0, 0, total_calls_ok, total_calls_fail) to reset batch counters."""
    pct = 100.0 * (i + 1) / total
    _log(
        f"Phase 6: [{i + 1}/{total}] {pct:.1f}% — LEIs: {leis_found}, no parent data: {gleif_no_parent_data_count}, "
        f"with parent data: {companies_with_parent_data}, "
        f"LegalEntity upserts: {legal_entities_written}, edges: {created}, pending: {len(pending_edges)}, no LEI: {no_lei_count}",
        "white",
    )
    _log(
        f"Phase 6: Last 20 companies — API calls: {batch_ok} succeeded, {batch_fail} failed  |  Total: {total_calls_ok} succeeded, {total_calls_fail} failed",
        "white",
    )
    return (0, 0, total_calls_ok, total_calls_fail)


def _gleif_sanitize_key(s: str) -> str:
    """Safe filename fragment from string."""
    import re
    return re.sub(r"[^\w\-]", "_", (s or "").strip())[:120] or "unknown"


# Regex to strip stock/security descriptions from company names before GLEIF search.
# Neo4j stores names like "Apple Inc. Common Stock" or "First Horizon Corporation Depositary
# Shares, Each Representing a 1/4000th Interest..." — GLEIF only knows legal entity names.
import re as _re_gleif
_GLEIF_STOCK_DESC_RE = _re_gleif.compile(
    r'\s+(?:Common\s+Stock|Class\s+[A-Z]\s+Common|Ordinary\s+Shares?|'
    r'American\s+Depositary\s+(?:Shares?|Receipt)|Depositary\s+Shares?|'
    r'Preferred\s+Stock|Units?|Warrants?|Rights?|Notes?\s+Due|'
    r'Senior\s+Notes?|Subordinated\s+Notes?|Fixed.Rate|'
    r'%\s+(?:Series|Senior|Subordinated|Fixed|Notes?)|'
    r'Each\s+Representing)\b.*$',
    _re_gleif.I
)
_GLEIF_TRAILING_PARENS_RE = _re_gleif.compile(r'\s*\([^)]*\)\s*$')
_GLEIF_ENTITY_FORM_RE = _re_gleif.compile(
    r'(?:\s+|\s*[,\.]\s*)(incorporated|corporation|company|limited|'
    r'inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|l\.l\.c\.?|plc\.?|lp|l\.p\.|'
    r'sa|ag|nv|se|oyj|oy|ab|bv|gmbh|sas|ltda|spa|srl|sl|a\.s\.|aps|kk)\s*$',
    _re_gleif.I,
)
_GLEIF_ENTITY_FORM_CANONICAL = {
    "incorporated": "inc",
    "inc": "inc",
    "corporation": "corp",
    "corp": "corp",
    "company": "co",
    "co": "co",
    "limited": "ltd",
    "ltd": "ltd",
    "llc": "llc",
    "plc": "plc",
    "lp": "lp",
    "sa": "sa",
    "ag": "ag",
    "nv": "nv",
    "se": "se",
    "oyj": "oyj",
    "oy": "oy",
    "ab": "ab",
    "bv": "bv",
    "gmbh": "gmbh",
    "sas": "sas",
    "ltda": "ltda",
    "spa": "spa",
    "srl": "srl",
    "sl": "sl",
    "as": "as",
    "aps": "aps",
    "kk": "kk",
}


def _gleif_clean_company_name(raw_name: str) -> str:
    """Strip stock/security descriptions from Neo4j company names for GLEIF search.
    'Apple Inc. Common Stock' → 'Apple Inc.'
    'First Horizon Corporation Depositary Shares...' → 'First Horizon Corporation'
    """
    name = (raw_name or "").strip()
    if not name:
        return name
    # Strip stock descriptions
    name = _GLEIF_STOCK_DESC_RE.sub('', name).strip()
    # Strip trailing parentheticals like "(DE)", "(NV)"
    name = _GLEIF_TRAILING_PARENS_RE.sub('', name).strip()
    # Strip trailing commas/periods
    name = name.rstrip(',. ')
    return name if len(name) >= 3 else raw_name.strip()


GLEIF_MIN_MATCH_SCORE = float(os.environ.get("GRAPH_NEXUS_GLEIF_MIN_MATCH_SCORE", "0.84"))
GLEIF_ALT_CHILD_CANDIDATE_LIMIT = max(0, int(os.environ.get("GRAPH_NEXUS_GLEIF_ALT_CHILD_CANDIDATE_LIMIT", "6")))


def _gleif_record_legal_name(record: dict | None) -> str:
    attrs = (record or {}).get("attributes") or {}
    entity = attrs.get("entity") or {}
    legal_name = entity.get("legalName")
    if isinstance(legal_name, dict):
        return str(legal_name.get("name") or "").strip()
    return str(legal_name or "").strip()


def _gleif_terminal_entity_form(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return ""
    match = _GLEIF_ENTITY_FORM_RE.search(raw)
    if not match:
        return ""
    token = _re_gleif.sub(r"[^a-z]", "", match.group(1).lower())
    return _GLEIF_ENTITY_FORM_CANONICAL.get(token, token)


def _gleif_name_match_score(company_name: str, gleif_name: str) -> float:
    left_key = _company_identity_key(company_name)
    right_key = _company_identity_key(gleif_name)
    if not left_key or not right_key:
        return 0.0
    if left_key == right_key:
        return 1.0
    left_tokens = set(_company_name_alignment_tokens(company_name))
    right_tokens = set(_company_name_alignment_tokens(gleif_name))
    overlap_ratio = 0.0
    if left_tokens and right_tokens:
        overlap_ratio = len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))
    seq_ratio = SequenceMatcher(None, left_key, right_key).ratio()
    if _company_names_loosely_match(company_name, gleif_name):
        # Single-token brand matches like "Apple" -> "Apple Ford" are admissible
        # for further parent-chain validation, but should not score as exact 1.0.
        if min(len(left_tokens), len(right_tokens)) == 1 and left_key != right_key:
            return max(0.90, seq_ratio)
        return max(0.90, seq_ratio, overlap_ratio)
    if overlap_ratio >= 0.80 and seq_ratio >= 0.78:
        return max(0.82, seq_ratio)
    return 0.0


def _gleif_search_candidate_payload(company_name: str, records: list[dict], publish_date: str) -> dict:
    candidates: list[dict] = []
    for rec in records or []:
        attrs = rec.get("attributes") or {}
        legal_name = _gleif_record_legal_name(rec)
        candidates.append({
            "lei": rec.get("id") or attrs.get("lei"),
            "legal_name": legal_name,
            "match_score": round(_gleif_name_match_score(company_name, legal_name), 4),
            "registration_status": str((attrs.get("registration") or {}).get("status") or ""),
            "conformity_flag": str(attrs.get("conformityFlag") or ""),
        })
    return {"publish_date": publish_date, "candidates": candidates}


def _gleif_select_best_candidate(company_name: str, candidates: list[dict]) -> dict:
    best_payload = {"lei": None, "publish_date": "", "legal_name": "", "match_score": 0.0}
    best_rank: tuple[float, int, int, int, int, int, float, int, int, int] | None = None
    company_form = _gleif_terminal_entity_form(company_name)
    saw_matching_form = False
    for candidate in candidates or []:
        legal_name = str(candidate.get("legal_name") or "")
        match_score = float(candidate.get("match_score") or 0.0)
        if company_form and match_score >= GLEIF_MIN_MATCH_SCORE and _gleif_terminal_entity_form(legal_name) == company_form:
            saw_matching_form = True
        rank = _gleif_search_candidate_rank(
            company_name,
            legal_name,
            match_score,
            registration_status=str(candidate.get("registration_status") or ""),
            conformity_flag=str(candidate.get("conformity_flag") or ""),
        )
        if best_rank is not None and rank <= best_rank:
            continue
        best_rank = rank
        best_payload = {
            "lei": candidate.get("lei"),
            "legal_name": legal_name,
            "match_score": round(match_score, 4),
        }
    best_form = _gleif_terminal_entity_form(str(best_payload.get("legal_name") or ""))
    if (
        best_payload["lei"]
        and company_form
        and best_form
        and best_form != company_form
        and not saw_matching_form
    ):
        return {"lei": None, "legal_name": "", "match_score": 0.0}
    if best_payload["lei"] and float(best_payload["match_score"] or 0.0) >= GLEIF_MIN_MATCH_SCORE:
        return best_payload
    return {"lei": None, "legal_name": "", "match_score": 0.0}


def _gleif_search_candidate_records(company_name: str) -> tuple[str, list[dict], str | None]:
    key = _gleif_sanitize_key(company_name)
    cache_path = _nexus_cache_path("phase6", f"gleif_search_candidates_v{GLEIF_SEARCH_CACHE_VERSION}_{key}.json")
    cached = _nexus_read_cached_json(cache_path, _nexus_cache_max_age("phase6"))
    if isinstance(cached, dict) and "candidates" in cached:
        _nexus_stage_cache_hit(1)
        return (
            _normalize_edge_date(cached.get("publish_date")),
            list(cached.get("candidates") or []),
            None,
        )
    import requests
    try:
        _gleif_wait_for_request_slot()
        r = _gleif_http_session().get(
            "https://api.gleif.org/api/v1/lei-records",
            params={"filter[entity.legalName]": company_name, "page[size]": 5},
            timeout=10,
        )
        if r.status_code == 429:
            _gleif_push_global_cooldown(GLEIF_COOLDOWN_SECONDS)
        r.raise_for_status()
        data = r.json()
        publish_date = _normalize_edge_date(((data.get("meta") or {}).get("goldenCopy") or {}).get("publishDate"))
        payload = _gleif_search_candidate_payload(company_name, list(data.get("data") or []), publish_date)
        _nexus_write_cached_json(cache_path, payload)
        _nexus_stage_download(1)
        return publish_date, list(payload.get("candidates") or []), None
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else 0
        if status == 429 or status >= 500:
            if status == 429:
                _gleif_push_global_cooldown(GLEIF_COOLDOWN_SECONDS)
            return ("", [], str(e))
        _nexus_write_cached_json(cache_path, {"publish_date": "", "candidates": []})
        return ("", [], str(e))
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        return ("", [], str(e))
    except Exception as e:
        _nexus_write_cached_json(cache_path, {"publish_date": "", "candidates": []})
        return ("", [], str(e))


def _gleif_split_alternate_child_candidates(company_name: str, primary_lei: str, candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    if GLEIF_ALT_CHILD_CANDIDATE_LIMIT <= 0:
        return [], [
            {
                "reason": "alt_child_candidates_disabled",
                "candidate_count": len(candidates or []),
            }
        ] if candidates else []
    company_key = _company_identity_key(company_name)
    ranked: list[tuple[tuple[float, int, int, int, int, int, float, int, int, int], dict]] = []
    rejected: list[dict] = []
    for candidate in candidates or []:
        lei = str(candidate.get("lei") or "").strip()
        legal_name = str(candidate.get("legal_name") or "").strip()
        match_score = float(candidate.get("match_score") or 0.0)
        registration_status = str(candidate.get("registration_status") or "").strip().upper()
        reason = ""
        if not lei:
            reason = "missing_candidate_lei"
        elif lei == primary_lei:
            reason = "same_as_primary_lei"
        elif not legal_name:
            reason = "missing_candidate_name"
        elif match_score < GLEIF_MIN_MATCH_SCORE:
            reason = "match_below_threshold"
        elif registration_status != "ISSUED":
            reason = "candidate_not_issued"
        elif company_key and _company_identity_key(legal_name) == company_key:
            reason = "same_as_company_identity"
        if reason:
            rejected.append({
                "reason": reason,
                "candidate_lei": lei,
                "candidate_name": legal_name,
                "match_score": round(match_score, 4),
                "registration_status": registration_status,
            })
            continue
        rank = _gleif_search_candidate_rank(
            company_name,
            legal_name,
            match_score,
            registration_status=str(candidate.get("registration_status") or ""),
            conformity_flag=str(candidate.get("conformity_flag") or ""),
        )
        ranked.append((rank, dict(candidate)))
    ranked.sort(key=lambda item: item[0], reverse=True)
    accepted = [candidate for _, candidate in ranked]
    return accepted, rejected


def _gleif_alternate_child_candidates(company_name: str, primary_lei: str, candidates: list[dict]) -> list[dict]:
    accepted, _rejected = _gleif_split_alternate_child_candidates(company_name, primary_lei, candidates)
    return accepted


def _gleif_search_candidate_rank(
    company_name: str,
    gleif_name: str,
    match_score: float,
    registration_status: str = "",
    conformity_flag: str = "",
) -> tuple[float, int, int, int, int, int, float, int, int, int]:
    left_key = _company_identity_key(company_name)
    right_key = _company_identity_key(gleif_name)
    seq_ratio = SequenceMatcher(None, left_key, right_key).ratio() if left_key and right_key else 0.0
    left_tokens = set(_company_name_alignment_tokens(company_name))
    right_tokens = set(_company_name_alignment_tokens(gleif_name))
    overlap = len(left_tokens & right_tokens) if left_tokens and right_tokens else 0
    missing_tokens = len(left_tokens - right_tokens) if left_tokens and right_tokens else len(left_tokens)
    extra_tokens = len(right_tokens - left_tokens) if left_tokens and right_tokens else len(right_tokens)
    exact_identity = 1 if left_key and right_key and left_key == right_key else 0
    query_form = _gleif_terminal_entity_form(company_name)
    candidate_form = _gleif_terminal_entity_form(gleif_name)
    form_match = 1 if query_form and candidate_form and query_form == candidate_form else 0
    form_conflict = 1 if query_form and candidate_form and query_form != candidate_form else 0
    reg_status = str(registration_status or "").strip().upper()
    reg_rank = 2 if reg_status == "ISSUED" else (1 if reg_status else 0)
    conformity_rank = 1 if str(conformity_flag or "").strip().upper() == "CONFORMING" else 0
    return (
        round(float(match_score or 0.0), 4),
        form_match,
        exact_identity,
        reg_rank,
        conformity_rank,
        -form_conflict,
        round(seq_ratio, 4),
        overlap,
        -missing_tokens,
        -extra_tokens,
    )


def _gleif_parent_ticker_matches_name(parent_record: dict | None, parent_name: str | None) -> bool:
    parent_name = (parent_name or "").strip()
    if not parent_name:
        return True
    parent_record = parent_record or {}
    for candidate in (
        parent_record.get("canonical_name"),
        parent_record.get("name"),
        parent_record.get("listing_name"),
        parent_record.get("lei_legal_name"),
    ):
        if candidate and _gleif_name_match_score(str(candidate), parent_name) >= GLEIF_MIN_MATCH_SCORE:
            return True
    return False


def _gleif_request_interval_seconds() -> float:
    return max(float(GLEIF_REQUEST_DELAY), 60.0 / float(max(1, GLEIF_MAX_REQUESTS_PER_MIN)))


def _gleif_wait_for_request_slot() -> None:
    interval = _gleif_request_interval_seconds()
    if interval <= 0:
        return
    while True:
        with _GLEIF_RATE_LOCK:
            now = time.monotonic()
            wait_secs = _GLEIF_RATE_NEXT_REQUEST_AT[0] - now
            if wait_secs <= 0:
                _GLEIF_RATE_NEXT_REQUEST_AT[0] = now + interval
                return
        time.sleep(min(wait_secs, 0.25))


def _gleif_push_global_cooldown(seconds: float) -> None:
    if seconds <= 0:
        return
    with _GLEIF_RATE_LOCK:
        now = time.monotonic()
        _GLEIF_RATE_NEXT_REQUEST_AT[0] = max(_GLEIF_RATE_NEXT_REQUEST_AT[0], now + float(seconds))


def _gleif_http_session():
    with _GLEIF_HTTP_SESSION_LOCK:
        session = _GLEIF_HTTP_SESSION[0]
        if session is not None:
            return session
        import requests
        from requests.adapters import HTTPAdapter

        session = requests.Session()
        session.headers.update({"Accept": "application/json"})
        adapter = HTTPAdapter(pool_connections=GLEIF_PREFETCH_WORKERS, pool_maxsize=GLEIF_PREFETCH_WORKERS, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _GLEIF_HTTP_SESSION[0] = session
        return session


def _usaspending_http_session():
    with _USASPENDING_HTTP_SESSION_LOCK:
        session = _USASPENDING_HTTP_SESSION[0]
        if session is not None:
            return session
        import requests
        from requests.adapters import HTTPAdapter

        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "IntelliStockV4/GraphNexus Phase8 USASpending Fetcher",
                "Connection": "close",
            }
        )
        adapter = HTTPAdapter(pool_connections=1, pool_maxsize=1, max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _USASPENDING_HTTP_SESSION[0] = session
        return session


def _usaspending_reset_http_session() -> None:
    with _USASPENDING_HTTP_SESSION_LOCK:
        session = _USASPENDING_HTTP_SESSION[0]
        _USASPENDING_HTTP_SESSION[0] = None
    if session is not None:
        try:
            session.close()
        except Exception:
            pass


def _usaspending_wait_for_request_window(
    request_timestamps: deque[float],
    *,
    window_seconds: float = USASPENDING_REQUEST_WINDOW_SECONDS,
    max_requests: int = USASPENDING_REQUESTS_PER_WINDOW,
) -> float:
    """
    Enforce a rolling USASpending request window before issuing the next request.

    This keeps the fetcher near the desired sustained request rate without relying on
    a separate degraded pacing mode after transient failures.
    """
    window = max(1.0, float(window_seconds or 0))
    cap = max(1, int(max_requests or 0))
    total_wait = 0.0
    while True:
        now = time.monotonic()
        while request_timestamps and (now - request_timestamps[0]) >= window:
            request_timestamps.popleft()
        if len(request_timestamps) < cap:
            request_timestamps.append(now)
            return total_wait
        wait = max(0.0, window - (now - request_timestamps[0]))
        if wait <= 0:
            request_timestamps.popleft()
            continue
        time.sleep(wait)
        total_wait += wait


def _gleif_search_lei(company_name: str) -> tuple[str | None, str, str, float, str | None]:
    """Search GLEIF API for a company by name. Returns (lei, publish_date, legal_name, match_score, error).
    lei is None if not found or on error; error is set only on API/request failure.
    Successful outcomes (found or genuinely not found) are cached. Transient errors (429, timeouts) are NOT cached so they can be retried."""
    key = _gleif_sanitize_key(company_name)
    cache_path = _nexus_cache_path("phase6", f"gleif_search_v{GLEIF_SEARCH_CACHE_VERSION}_{key}.json")
    cached = _nexus_read_cached_json(cache_path, _nexus_cache_max_age("phase6"))
    if isinstance(cached, dict) and "lei" in cached:
        _nexus_stage_cache_hit(1)
        return (
            cached.get("lei"),
            _normalize_edge_date(cached.get("publish_date")),
            str(cached.get("legal_name") or ""),
            float(cached.get("match_score") or 0.0),
            None,
        )
    publish_date, candidates, err = _gleif_search_candidate_records(company_name)
    if err:
        return (None, "", "", 0.0, err)
    best_payload = _gleif_select_best_candidate(company_name, candidates)
    cache_payload = {
        "lei": best_payload.get("lei"),
        "publish_date": publish_date,
        "legal_name": str(best_payload.get("legal_name") or ""),
        "match_score": float(best_payload.get("match_score") or 0.0),
    }
    _nexus_write_cached_json(cache_path, cache_payload)
    return (
        cache_payload["lei"],
        publish_date,
        cache_payload["legal_name"],
        cache_payload["match_score"],
        None,
    )
    import requests
    try:
        _gleif_wait_for_request_slot()
        r = _gleif_http_session().get(
            "https://api.gleif.org/api/v1/lei-records",
            params={"filter[entity.legalName]": company_name, "page[size]": 5},
            timeout=10,
        )
        if r.status_code == 429:
            _gleif_push_global_cooldown(GLEIF_COOLDOWN_SECONDS)
        r.raise_for_status()
        data = r.json()
        publish_date = _normalize_edge_date(((data.get("meta") or {}).get("goldenCopy") or {}).get("publishDate"))
        records = data.get("data") or []
        best_payload = {"lei": None, "publish_date": publish_date, "legal_name": "", "match_score": 0.0}
        best_rank: tuple[float, int, int, int, int, int, float, int, int, int] | None = None
        company_form = _gleif_terminal_entity_form(company_name)
        saw_matching_form = False
        if records:
            for rec in records:
                attrs = rec.get("attributes") or {}
                legal_name = _gleif_record_legal_name(rec)
                match_score = _gleif_name_match_score(company_name, legal_name)
                if company_form and match_score >= GLEIF_MIN_MATCH_SCORE and _gleif_terminal_entity_form(legal_name) == company_form:
                    saw_matching_form = True
                rank = _gleif_search_candidate_rank(
                    company_name,
                    legal_name,
                    match_score,
                    registration_status=((attrs.get("registration") or {}).get("status") or ""),
                    conformity_flag=attrs.get("conformityFlag") or "",
                )
                if best_rank is not None and rank <= best_rank:
                    continue
                best_rank = rank
                best_payload = {
                    "lei": rec.get("id") or (rec.get("attributes") or {}).get("lei"),
                    "publish_date": publish_date,
                    "legal_name": legal_name,
                    "match_score": round(match_score, 4),
                }
            best_form = _gleif_terminal_entity_form(str(best_payload.get("legal_name") or ""))
            if (
                best_payload["lei"]
                and company_form
                and best_form
                and best_form != company_form
                and not saw_matching_form
            ):
                best_payload = {"lei": None, "publish_date": publish_date, "legal_name": "", "match_score": 0.0}
            if best_payload["lei"] and best_payload["match_score"] >= GLEIF_MIN_MATCH_SCORE:
                _nexus_write_cached_json(cache_path, best_payload)
                _nexus_stage_download(1)
                return (
                    best_payload["lei"],
                    publish_date,
                    str(best_payload["legal_name"] or ""),
                    float(best_payload["match_score"] or 0.0),
                    None,
                )
        _nexus_write_cached_json(cache_path, best_payload)
        return (
            None,
            publish_date,
            str(best_payload.get("legal_name") or ""),
            float(best_payload.get("match_score") or 0.0),
            None,
        )  # no validated LEI found, call succeeded — cached so we don't call again
    except requests.exceptions.HTTPError as e:
        # Do NOT cache transient errors (429 rate limit, 5xx server errors) —
        # they should be retried on next run or in the retry queue
        status = e.response.status_code if e.response is not None else 0
        if status == 429 or status >= 500:
            if status == 429:
                _gleif_push_global_cooldown(GLEIF_COOLDOWN_SECONDS)
            return (None, "", "", 0.0, str(e))  # transient — do not cache
        _nexus_write_cached_json(cache_path, {"lei": None, "publish_date": "", "legal_name": "", "match_score": 0.0})
        return (None, "", "", 0.0, str(e))
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
        return (None, "", "", 0.0, str(e))  # transient — do not cache
    except Exception as e:
        _nexus_write_cached_json(cache_path, {"lei": None, "publish_date": "", "legal_name": "", "match_score": 0.0})
        return (None, "", "", 0.0, str(e))


def _gleif_fetch_relationships(lei: str) -> tuple[dict, str | None]:
    """Fetch GLEIF relationship data (direct parent + ultimate parent) for a LEI. Uses cache when present.
    404 on parent endpoints is normal for top-level entities. Returns (result, error); error set only on request failure.
    Successful outcomes are cached. Transient errors (429, timeouts) are NOT cached."""
    key = _gleif_sanitize_key(lei)
    cache_path = _nexus_cache_path("phase6", f"gleif_rels_{key}.json")
    cached = _nexus_read_cached_json(cache_path, _nexus_cache_max_age("phase6"))
    if isinstance(cached, dict):
        _nexus_stage_cache_hit(1)
        return (
            {
                "direct_parent_lei": cached.get("direct_parent_lei"),
                "ultimate_parent_lei": cached.get("ultimate_parent_lei"),
                "direct_parent_name": cached.get("direct_parent_name"),
                "ultimate_parent_name": cached.get("ultimate_parent_name"),
                "publish_date": _normalize_edge_date(cached.get("publish_date")),
            },
            None,
        )
    import requests
    result = {"direct_parent_lei": None, "ultimate_parent_lei": None, "publish_date": ""}
    last_error = None
    is_transient = False
    for endpoint_suffix, key_out in (
        ("direct-parent", "direct_parent_lei"),
        ("ultimate-parent", "ultimate_parent_lei"),
    ):
        try:
            _gleif_wait_for_request_slot()
            r = _gleif_http_session().get(
                f"https://api.gleif.org/api/v1/lei-records/{lei}/{endpoint_suffix}",
                timeout=10,
            )
            if r.status_code == 429 or r.status_code >= 500:
                if r.status_code == 429:
                    _gleif_push_global_cooldown(GLEIF_COOLDOWN_SECONDS)
                is_transient = True
                last_error = f"{r.status_code} {r.reason}"
                continue
            if r.status_code == 200:
                data = r.json()
                publish_date = _normalize_edge_date(((data.get("meta") or {}).get("goldenCopy") or {}).get("publishDate"))
                if publish_date and (not result["publish_date"] or publish_date < result["publish_date"]):
                    result["publish_date"] = publish_date
                parents = data.get("data")
                if parents is not None and (parents if isinstance(parents, list) else True):
                    p = parents[0] if isinstance(parents, list) and parents else parents
                    pid = (p.get("id") or (p.get("attributes") or {}).get("lei")) if isinstance(p, dict) else None
                    pname = _gleif_record_legal_name(p if isinstance(p, dict) else {})
                    if pid:
                        result[key_out] = pid
                    if pname:
                        result[f"{key_out.replace('_lei', '')}_name"] = pname
            # 404 is normal for no parent; other errors are logged by caller
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            is_transient = True
            last_error = str(e)
        except Exception as e:
            last_error = str(e)
    if not is_transient:
        _nexus_write_cached_json(cache_path, result)
    _nexus_stage_download(1)
    return (result, last_error)


def _hierarchy_llm_config() -> tuple[str, str, str]:
    provider = (GRAPH_NEXUS_HIERARCHY_LLM_PROVIDER or "deepseek").strip().lower()
    if provider not in ("gemini", "deepseek"):
        provider = "deepseek"
    model = (GRAPH_NEXUS_HIERARCHY_LLM_MODEL or "").strip()
    if not model:
        model = "gemini-3-flash-preview" if provider == "gemini" else "deepseek-reasoner"
    api_key = (GRAPH_NEXUS_HIERARCHY_LLM_API_KEY or "").strip()
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip() if provider == "gemini" else os.environ.get("DEEPSEEK_API_KEY", "").strip()
    return provider, model, api_key


def _provider_api_key_looks_valid(provider: str, api_key: str) -> bool:
    clean_provider = (provider or "").strip().lower()
    clean_key = (api_key or "").strip()
    if not clean_key:
        return False
    if clean_provider == "deepseek":
        return clean_key.startswith("sk-")
    if clean_provider == "gemini":
        return clean_key.startswith("AIza") or clean_key.startswith("ya29.")
    return True


def _resolve_provider_api_key(provider: str, configured_key: str = "") -> str:
    clean_provider = (provider or "").strip().lower()
    clean_key = (configured_key or "").strip()
    if _provider_api_key_looks_valid(clean_provider, clean_key):
        return clean_key
    if clean_provider == "gemini":
        fallback = os.environ.get("GEMINI_API_KEY", "").strip()
    elif clean_provider == "deepseek":
        fallback = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    else:
        fallback = ""
    return fallback or clean_key


def _graph_nexus_llm_config() -> tuple[str, str, str]:
    provider = (GRAPH_NEXUS_LLM_PROVIDER or "").strip().lower()
    if provider not in ("gemini", "deepseek"):
        provider = "deepseek" if os.environ.get("DEEPSEEK_API_KEY", "").strip() else "gemini"
    model = (GRAPH_NEXUS_LLM_MODEL or "").strip()
    if not model:
        model = "gemini-3-flash-preview" if provider == "gemini" else "deepseek-reasoner"
    api_key = _resolve_provider_api_key(provider, GRAPH_NEXUS_LLM_API_KEY)
    return provider, model, api_key


def _patentsview_llm_config() -> tuple[str, str, str]:
    provider = (PATENTSVIEW_LLM_PROVIDER or "").strip().lower()
    if provider not in ("gemini", "deepseek"):
        provider = "deepseek" if os.environ.get("DEEPSEEK_API_KEY", "").strip() else "gemini"
    model = (PATENTSVIEW_LLM_MODEL or "").strip()
    if not model:
        model = "gemini-3-flash-preview" if provider == "gemini" else "deepseek-reasoner"
    api_key = _resolve_provider_api_key(provider, PATENTSVIEW_LLM_API_KEY)
    return provider, model, api_key


def _normalize_entity_alias(raw_alias: str) -> str:
    alias = str(raw_alias or "").strip()
    if not alias:
        return ""
    return _company_identity_key(alias) or re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", alias.lower())).strip()


def _entity_alias_match(alias: str, text: str) -> bool:
    alias = str(alias or "").strip()
    text = str(text or "")
    if not alias or not text:
        return False
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", text, re.I))


def _build_legal_entity_aliases(legal_name: str, extra_aliases: list[str] | None = None) -> tuple[list[str], list[str], str, bool]:
    aliases: list[str] = []
    seen_display: set[str] = set()

    def _add(alias: str) -> None:
        text = re.sub(r"\s+", " ", str(alias or "").strip()).strip(" ,;:-")
        if not text:
            return
        key = text.casefold()
        if key in seen_display:
            return
        seen_display.add(key)
        aliases.append(text)

    base = re.sub(r"\s+", " ", str(legal_name or "").strip())
    core = _canonical_company_name(base)
    bare = re.sub(
        r"\b(incorporated|corporation|company|limited|inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|plc\.?|holdings?|group|enterprises?|technologies|technology|solutions|brands|ventures)\b\.?",
        "",
        core,
        flags=re.I,
    )
    bare = re.sub(r"\s+", " ", bare).strip(" ,")
    _add(base)
    if core and core != base:
        _add(core)
    if bare and bare not in (base, core):
        _add(bare)
    for alias in extra_aliases or []:
        _add(alias)
    tokens = bare.split()
    if len(tokens) >= 2 and len(tokens[0]) >= 4 and tokens[0].lower() not in _GENERIC_ENTITY_ALIASES:
        _add(tokens[0])
    normalized: list[str] = []
    seen_norm: set[str] = set()
    for alias in aliases:
        norm = _normalize_entity_alias(alias)
        if not norm or norm in seen_norm:
            continue
        seen_norm.add(norm)
        normalized.append(norm)
    alias_risk = "low"
    requires_confirmation = False
    for norm in normalized:
        word_count = len(norm.split())
        if norm in _AMBIGUOUS_ENTITY_ALIASES or (word_count == 1 and len(norm) <= 6):
            alias_risk = "high"
            requires_confirmation = True
            break
        if word_count == 1 or any(token in _GENERIC_ENTITY_ALIASES for token in norm.split()):
            alias_risk = "medium"
            requires_confirmation = True
    return aliases, normalized, alias_risk, requires_confirmation


_PHASE6_EX21_PRIVATE_STYLE_RE = re.compile(
    r"\b(?:"
    r"llc|l\.l\.c\.|gmbh|b\.v\.|s\.p\.a\.|pte\.?\s+ltd\.?|private\s+limited|"
    r"unlimited\s+company|aps|s\.a\.r\.l\.|sarl|luxco|holdings?"
    r")\b",
    re.I,
)


def _phase6_ex21_alias_is_entity_like(alias: str) -> bool:
    text = re.sub(r"\s+", " ", str(alias or "").strip())
    if not text:
        return False
    normalized = _normalize_entity_alias(text)
    if not normalized or normalized in _AMBIGUOUS_ENTITY_ALIASES:
        return False
    words = normalized.split()
    if len(words) <= 1:
        return False
    if _PHASE6_EX21_PRIVATE_STYLE_RE.search(text):
        return True
    informative_words = [token for token in words if token not in _GENERIC_ENTITY_ALIASES and len(token) >= 4]
    return len(informative_words) >= 2


def _phase6_ex21_informative_overlap(left: str, right: str) -> set[str]:
    left_tokens = set(_company_name_alignment_tokens(left))
    right_tokens = set(_company_name_alignment_tokens(right))
    return {token for token in (left_tokens & right_tokens) if token not in _GENERIC_ENTITY_ALIASES}


def _build_legal_entity_payload(
    *,
    entity_key: str,
    legal_name: str,
    display_name: str = "",
    aliases: list[str] | None = None,
    entity_kind: str = "legal_entity",
    lei: str = "",
    country: str = "",
    source_systems: list[str] | None = None,
    listed_ancestor_tickers: list[str] | None = None,
) -> dict:
    clean_legal_name = re.sub(r"\s+", " ", str(legal_name or "").strip())
    clean_display = re.sub(r"\s+", " ", str(display_name or clean_legal_name or entity_key).strip())
    alias_list, normalized_aliases, alias_risk, requires_confirmation = _build_legal_entity_aliases(
        clean_display or clean_legal_name,
        extra_aliases=aliases,
    )
    return {
        "entity_key": str(entity_key or "").strip(),
        "legal_name": clean_legal_name,
        "display_name": clean_display,
        "aliases": alias_list,
        "normalized_aliases": normalized_aliases,
        "entity_kind": str(entity_kind or "legal_entity").strip() or "legal_entity",
        "lei": str(lei or "").strip(),
        "country": str(country or "").strip(),
        "source_systems": sorted({str(item or "").strip() for item in (source_systems or []) if str(item or "").strip()}),
        "listed_ancestor_tickers": sorted({str(item or "").strip().upper() for item in (listed_ancestor_tickers or []) if str(item or "").strip()}),
        "alias_risk": alias_risk,
        "alias_requires_parent_confirmation": bool(requires_confirmation),
    }


def _phase6_write_legal_entities(session, batch: list[dict]) -> int:
    if not batch:
        return 0
    with_lei = [dict(item) for item in batch if str(item.get("lei") or "").strip()]
    without_lei = [dict(item) for item in batch if not str(item.get("lei") or "").strip()]
    if with_lei:
        session.run(
            """
            UNWIND $batch AS p
            MERGE (e:LEIEntity {lei: p.lei})
            SET e:LegalEntity,
                e.entity_key = coalesce(e.entity_key, p.entity_key),
                e.lei = p.lei,
                e.legal_name = CASE WHEN p.legal_name = '' THEN coalesce(e.legal_name, e.name, e.display_name) ELSE p.legal_name END,
                e.display_name = CASE WHEN p.display_name = '' THEN coalesce(e.display_name, e.legal_name, e.name, p.legal_name) ELSE p.display_name END,
                e.name = CASE WHEN p.display_name = '' THEN coalesce(e.name, e.display_name, e.legal_name, p.legal_name) ELSE p.display_name END,
                e.aliases = REDUCE(acc = coalesce(e.aliases, []), item IN coalesce(p.aliases, []) |
                    CASE WHEN item IS NULL OR item = '' OR item IN acc THEN acc ELSE acc + item END),
                e.normalized_aliases = REDUCE(acc = coalesce(e.normalized_aliases, []), item IN coalesce(p.normalized_aliases, []) |
                    CASE WHEN item IS NULL OR item = '' OR item IN acc THEN acc ELSE acc + item END),
                e.entity_kind = CASE WHEN p.entity_kind = '' THEN coalesce(e.entity_kind, 'legal_entity') ELSE p.entity_kind END,
                e.country = CASE WHEN p.country = '' THEN e.country ELSE p.country END,
                e.source_systems = REDUCE(acc = coalesce(e.source_systems, []), item IN coalesce(p.source_systems, []) |
                    CASE WHEN item IS NULL OR item = '' OR item IN acc THEN acc ELSE acc + item END),
                e.listed_ancestor_tickers = REDUCE(acc = coalesce(e.listed_ancestor_tickers, []), item IN coalesce(p.listed_ancestor_tickers, []) |
                    CASE WHEN item IS NULL OR item = '' OR item IN acc THEN acc ELSE acc + item END),
                e.alias_requires_parent_confirmation = coalesce(e.alias_requires_parent_confirmation, false) OR coalesce(p.alias_requires_parent_confirmation, false),
                e.alias_risk = CASE
                    WHEN coalesce(e.alias_risk, '') = 'high' OR p.alias_risk = 'high' THEN 'high'
                    WHEN (coalesce(e.alias_risk, '') IN ['', 'low']) AND p.alias_risk = 'medium' THEN 'medium'
                    WHEN coalesce(e.alias_risk, '') = '' THEN coalesce(p.alias_risk, 'low')
                    ELSE e.alias_risk
                END
            """,
            batch=with_lei,
        ).consume()
    if without_lei:
        session.run(
            """
            UNWIND $batch AS p
            MERGE (e:LegalEntity {entity_key: p.entity_key})
            SET e.legal_name = CASE WHEN p.legal_name = '' THEN coalesce(e.legal_name, e.name, e.display_name) ELSE p.legal_name END,
                e.display_name = CASE WHEN p.display_name = '' THEN coalesce(e.display_name, e.legal_name, e.name, p.legal_name) ELSE p.display_name END,
                e.name = CASE WHEN p.display_name = '' THEN coalesce(e.name, e.display_name, e.legal_name, p.legal_name) ELSE p.display_name END,
                e.aliases = REDUCE(acc = coalesce(e.aliases, []), item IN coalesce(p.aliases, []) |
                    CASE WHEN item IS NULL OR item = '' OR item IN acc THEN acc ELSE acc + item END),
                e.normalized_aliases = REDUCE(acc = coalesce(e.normalized_aliases, []), item IN coalesce(p.normalized_aliases, []) |
                    CASE WHEN item IS NULL OR item = '' OR item IN acc THEN acc ELSE acc + item END),
                e.entity_kind = CASE WHEN p.entity_kind = '' THEN coalesce(e.entity_kind, 'legal_entity') ELSE p.entity_kind END,
                e.country = CASE WHEN p.country = '' THEN e.country ELSE p.country END,
                e.source_systems = REDUCE(acc = coalesce(e.source_systems, []), item IN coalesce(p.source_systems, []) |
                    CASE WHEN item IS NULL OR item = '' OR item IN acc THEN acc ELSE acc + item END),
                e.listed_ancestor_tickers = REDUCE(acc = coalesce(e.listed_ancestor_tickers, []), item IN coalesce(p.listed_ancestor_tickers, []) |
                    CASE WHEN item IS NULL OR item = '' OR item IN acc THEN acc ELSE acc + item END),
                e.alias_requires_parent_confirmation = coalesce(e.alias_requires_parent_confirmation, false) OR coalesce(p.alias_requires_parent_confirmation, false),
                e.alias_risk = CASE
                    WHEN coalesce(e.alias_risk, '') = 'high' OR p.alias_risk = 'high' THEN 'high'
                    WHEN (coalesce(e.alias_risk, '') IN ['', 'low']) AND p.alias_risk = 'medium' THEN 'medium'
                    WHEN coalesce(e.alias_risk, '') = '' THEN coalesce(p.alias_risk, 'low')
                    ELSE e.alias_risk
                END
            """,
            batch=without_lei,
        ).consume()
    return len(batch)


def _phase6_promote_existing_lei_entities(session) -> int:
    rows = list(session.run(
        """
        MATCH (l:LEIEntity)
        WITH l, properties(l) AS props
        RETURN props['lei'] AS lei,
               coalesce(props['legal_name'], props['name'], props['display_name'], props['lei']) AS legal_name,
               coalesce(props['display_name'], props['name'], props['legal_name'], props['lei']) AS display_name,
               coalesce(props['aliases'], []) AS aliases,
               coalesce(props['country'], '') AS country,
               coalesce(props['source_systems'], []) AS source_systems,
               coalesce(props['listed_ancestor_tickers'], []) AS listed_ancestor_tickers
        """
    ))
    batch = [
        _build_legal_entity_payload(
            entity_key=f"lei:{str(row.get('lei') or '').strip()}",
            legal_name=str(row.get("legal_name") or ""),
            display_name=str(row.get("display_name") or ""),
            aliases=list(row.get("aliases") or []),
            entity_kind="legal_entity",
            lei=str(row.get("lei") or "").strip(),
            country=str(row.get("country") or ""),
            source_systems=list(row.get("source_systems") or []) or ["GLEIF"],
            listed_ancestor_tickers=list(row.get("listed_ancestor_tickers") or []),
        )
        for row in rows
        if str(row.get("lei") or "").strip()
    ]
    return _phase6_write_legal_entities(session, batch)


def _phase6_build_company_resolution_maps(records: list[dict]) -> tuple[dict[str, list[str]], dict[str, dict]]:
    alias_to_tickers: dict[str, list[str]] = {}
    ticker_to_record: dict[str, dict] = {}
    for rec in records or []:
        ticker = str(rec.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        ticker_to_record[ticker] = rec
        for candidate in (
            rec.get("name"),
            rec.get("canonical_name"),
            rec.get("listing_name"),
            rec.get("lei_legal_name"),
            _company_record_best_name(rec),
        ):
            alias = _normalize_entity_alias(str(candidate or ""))
            if not alias:
                continue
            existing = alias_to_tickers.setdefault(alias, [])
            if ticker not in existing:
                existing.append(ticker)
    return alias_to_tickers, ticker_to_record


def _phase6_rank_company_candidates(
    company_records: list[dict],
    subsidiary_name: str,
    aliases: list[str],
    issuer_key: str = "",
) -> list[tuple[str, float]]:
    subsidiary_name = re.sub(r"\s+", " ", str(subsidiary_name or "").strip())
    subsidiary_norm = _normalize_entity_alias(subsidiary_name)
    actionable_aliases = [
        re.sub(r"\s+", " ", str(alias or "").strip())
        for alias in aliases or []
        if _phase6_ex21_alias_is_entity_like(alias)
    ]
    actionable_alias_norms = {
        _normalize_entity_alias(alias)
        for alias in actionable_aliases
        if _normalize_entity_alias(alias)
    }
    ranked: list[tuple[str, float]] = []
    seen: set[str] = set()
    for rec in company_records or []:
        ticker = str(rec.get("ticker") or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        if issuer_key and str(rec.get("issuer_key") or "").strip() == issuer_key:
            continue
        public_names: list[str] = []
        for candidate in (
            _company_record_public_name(rec),
            rec.get("name"),
            rec.get("canonical_name"),
            rec.get("listing_name"),
        ):
            text = re.sub(r"\s+", " ", str(candidate or "").strip())
            if text and text not in public_names:
                public_names.append(text)
        if not public_names:
            continue
        norms: set[str] = set()
        for candidate in public_names:
            norm = _normalize_entity_alias(candidate)
            if norm:
                norms.add(norm)
        if not norms:
            continue
        score = 0.0
        if subsidiary_norm and subsidiary_norm in norms:
            score = 1.0
        else:
            for candidate_name in public_names:
                if not _phase6_ex21_informative_overlap(subsidiary_name, candidate_name):
                    continue
                score = max(score, _gleif_name_match_score(subsidiary_name, candidate_name))
        alias_score = 0.0
        if actionable_alias_norms & norms:
            alias_score = 0.94
        elif actionable_aliases:
            for alias in actionable_aliases:
                for candidate_name in public_names:
                    if not _phase6_ex21_informative_overlap(alias, candidate_name):
                        continue
                    alias_score = max(alias_score, min(_gleif_name_match_score(alias, candidate_name), 0.94))
        score = max(score, alias_score)
        if score >= 0.90:
            ranked.append((ticker, float(score)))
            seen.add(ticker)
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked


def _phase6_call_hierarchy_validator(
    prompt: str,
    output_type,
    *,
    system_prompt: str,
    log_label: str,
    allow_search: bool = True,
):
    from llm_utils import (
        call_structured_llm_by_provider,
        _call_structured_llm_with_critical_guard as _scl_guarded,
        get_last_structured_llm_call_metadata,
        google_cse_search,
    )

    provider, model, api_key = _hierarchy_llm_config()
    if not api_key:
        return None
    search_used = {"value": False}

    def google_search_tool(query: str) -> list[dict]:
        search_used["value"] = True
        return google_cse_search(
            query,
            api_key=GOOGLE_SEARCH_API_KEY,
            search_engine_id=GOOGLE_SEARCH_ENGINE_ID,
            num_results=5,
            fetch_result_excerpt=False,
            timeout_sec=30,
        )

    tools = [google_search_tool] if allow_search and GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_ENGINE_ID else None
    # TODO: thread `config` into _phase6_call_hierarchy_validator to attribute
    # critical-guard failures back to the originating backtest/instance.
    result = _scl_guarded(
        provider,
        api_key,
        model,
        prompt,
        output_type,
        attribution_keys={"call_site": "nexus_graph.phase6_hierarchy_validator"},
        system_prompt=system_prompt,
        tools=tools,
        max_output_tokens=0,
        timeout_sec=90,
        retries=2,
        output_retries=2,
        temperature=0.0,
    )
    meta = get_last_structured_llm_call_metadata()
    usage = dict(meta.get("usage") or {})
    usage_parts = []
    if usage.get("input_tokens") is not None:
        usage_parts.append(f"input_tokens={usage.get('input_tokens')}")
    if usage.get("output_tokens") is not None:
        usage_parts.append(f"output_tokens={usage.get('output_tokens')}")
    decision_parts = []
    if result:
        keep = getattr(result, "keep", None)
        best_ticker = str(getattr(result, "best_ticker", "") or "").strip().upper()
        best_parent = str(getattr(result, "best_parent", "") or "").strip().upper()
        best_entity_kind = str(getattr(result, "best_entity_kind", "") or "").strip().lower()
        confidence_bucket = str(getattr(result, "confidence_bucket", "") or "").strip().lower()
        if keep is not None:
            decision_parts.append(f"keep={bool(keep)}")
        if best_ticker:
            decision_parts.append(f"best_ticker={best_ticker}")
        elif best_parent:
            decision_parts.append(f"best_parent={best_parent}")
        if best_entity_kind:
            decision_parts.append(f"entity_kind={best_entity_kind}")
        if confidence_bucket:
            decision_parts.append(f"confidence={confidence_bucket}")
    _log(
        f"{log_label}: provider={meta.get('provider') or provider} model={meta.get('effective_model') or model} "
        f"fallback={bool(meta.get('fallback_used'))} search_used={search_used['value']} outcome={'validated' if result else 'unresolved'}"
        f"{(' ' + ' '.join(usage_parts)) if usage_parts else ''}"
        f"{(' ' + ' '.join(decision_parts)) if decision_parts else ''}",
        "white" if result else "yellow",
    )
    return result


def _phase6_choose_listed_company_for_subsidiary(
    company_records: list[dict],
    issuer_record: dict,
    subsidiary_name: str,
    aliases: list[str],
    filing_excerpt: str,
    filing_url: str,
    discard_logger=None,
    *,
    alias_to_tickers: dict[str, list[str]] | None = None,
    record_by_ticker: dict[str, dict] | None = None,
) -> str:
    issuer_key = str((issuer_record or {}).get("issuer_key") or "").strip()
    private_style_name = bool(_PHASE6_EX21_PRIVATE_STYLE_RE.search(str(subsidiary_name or "")))
    if record_by_ticker is None:
        record_by_ticker = {
            str(rec.get("ticker") or "").strip().upper(): rec
            for rec in company_records or []
            if str(rec.get("ticker") or "").strip()
        }
    if alias_to_tickers is None:
        alias_to_tickers, _ = _phase6_build_company_resolution_maps(company_records)
    subsidiary_norm = _normalize_entity_alias(subsidiary_name)
    actionable_aliases = [
        re.sub(r"\s+", " ", str(alias or "").strip())
        for alias in aliases or []
        if _phase6_ex21_alias_is_entity_like(alias)
    ]
    candidate_norms: list[str] = []
    for norm in (
        [subsidiary_norm] +
        [
            _normalize_entity_alias(alias)
            for alias in actionable_aliases
        ]
    ):
        if norm and norm not in candidate_norms:
            candidate_norms.append(norm)
    ranked_map: dict[str, float] = {}
    for norm in candidate_norms:
        for ticker in alias_to_tickers.get(norm, []) or []:
            rec = record_by_ticker.get(ticker) or {}
            if issuer_key and str(rec.get("issuer_key") or "").strip() == issuer_key:
                continue
            score = 1.0 if norm == subsidiary_norm and subsidiary_norm else 0.94
            prev = ranked_map.get(ticker, 0.0)
            if score > prev:
                ranked_map[ticker] = score
    ranked = sorted(ranked_map.items(), key=lambda item: (-item[1], item[0]))
    if not ranked:
        if callable(discard_logger):
            discard_logger(
                "ex21_promotion",
                "no_listed_company_candidates",
                {
                    "issuer_ticker": str((issuer_record or {}).get("ticker") or "").strip().upper(),
                    "subsidiary_name": subsidiary_name,
                    "aliases": list(aliases or []),
                    "filing_url": filing_url,
                },
            )
        return ""
    top_tickers = [ticker for ticker, _score in ranked[:5]]
    exact_name_tickers = []
    compact_exact_tickers = []
    subsidiary_compact = re.sub(r"[^a-z0-9]+", "", str(subsidiary_name or "").lower())
    for ticker in top_tickers:
        rec = record_by_ticker.get(ticker) or {}
        public_names = [
            _company_record_public_name(rec),
            str(rec.get("name") or ""),
            str(rec.get("canonical_name") or ""),
            str(rec.get("listing_name") or ""),
        ]
        public_norms = {
            _normalize_entity_alias(name)
            for name in public_names
        }
        public_norms.discard("")
        if subsidiary_norm and subsidiary_norm in public_norms:
            exact_name_tickers.append(ticker)
        if subsidiary_compact and any(
            subsidiary_compact == re.sub(r"[^a-z0-9]+", "", str(name or "").lower())
            for name in public_names
            if str(name or "").strip()
        ):
            compact_exact_tickers.append(ticker)
    if private_style_name:
        if compact_exact_tickers:
            exact_name_tickers = [ticker for ticker in exact_name_tickers if ticker in compact_exact_tickers]
        else:
            exact_name_tickers = []
    if not exact_name_tickers:
        if callable(discard_logger):
            discard_logger(
                "ex21_promotion",
                "no_exact_listed_company_match" if not private_style_name else "private_style_name_not_public_exact_match",
                {
                    "issuer_ticker": str((issuer_record or {}).get("ticker") or "").strip().upper(),
                    "subsidiary_name": subsidiary_name,
                    "aliases": list(aliases or []),
                    "candidate_tickers": top_tickers,
                    "filing_url": filing_url,
                },
            )
        return ""
    ranked = [item for item in ranked if item[0] in exact_name_tickers]
    top_tickers = [ticker for ticker, _score in ranked[:5]]
    supported_tickers: list[str] = []
    for ticker in top_tickers:
        candidate_record = record_by_ticker.get(ticker) or {}
        if _company_records_have_sec_control_support(issuer_record, candidate_record):
            supported_tickers.append(ticker)
    if not supported_tickers:
        if callable(discard_logger):
            discard_logger(
                "ex21_promotion",
                "exact_match_lacks_independent_support",
                {
                    "issuer_ticker": str((issuer_record or {}).get("ticker") or "").strip().upper(),
                    "subsidiary_name": subsidiary_name,
                    "aliases": list(aliases or []),
                    "candidate_tickers": top_tickers,
                    "filing_url": filing_url,
                },
            )
        return ""
    ranked = [item for item in ranked if item[0] in supported_tickers]
    top_tickers = [ticker for ticker, _score in ranked[:5]]
    if len(ranked) == 1 and ranked[0][1] >= 0.95:
        return ranked[0][0]
    candidate_payload = [
        {
            "ticker": ticker,
            "name": _company_record_public_name(record_by_ticker.get(ticker)),
        }
        for ticker in top_tickers
    ]
    prompt = (
        "Resolve whether an SEC EX-21 subsidiary should be promoted to a listed public company node.\n"
        "Return keep=true only when one candidate is clearly the same listed company named in the filing.\n"
        "If the EX-21 entry is just a private subsidiary, return keep=false and best_entity_kind='legal_entity'.\n\n"
        f"Issuer: {(issuer_record or {}).get('ticker', '')} — {_company_record_best_name(issuer_record)}\n"
        f"Subsidiary legal name: {subsidiary_name}\n"
        f"Known aliases: {json.dumps(aliases[:6], ensure_ascii=False)}\n"
        f"Candidate listed companies: {json.dumps(candidate_payload, ensure_ascii=False)}\n"
        f"Filing excerpt: {filing_excerpt[:1200]}\n"
        f"Filing URL: {filing_url}\n"
    )
    verdict = _phase6_call_hierarchy_validator(
        prompt,
        _AliasDisambiguationVerdict,
        system_prompt=(
            "Decide whether the SEC EX-21 entity is itself a listed public company candidate. "
            "Use the search tool only if the filing context is ambiguous. "
            "Return only the structured verdict fields."
        ),
        log_label="Phase 6 EX-21 validator",
        allow_search=len(ranked) > 1,
    )
    if not verdict:
        if callable(discard_logger):
            discard_logger(
                "ex21_promotion",
                "validator_unresolved",
                {
                    "issuer_ticker": str((issuer_record or {}).get("ticker") or "").strip().upper(),
                    "subsidiary_name": subsidiary_name,
                    "aliases": list(aliases or []),
                    "candidate_tickers": top_tickers,
                    "filing_url": filing_url,
                },
            )
        return ""
    if not verdict.keep:
        if callable(discard_logger):
            discard_logger(
                "ex21_promotion",
                "validator_rejected",
                {
                    "issuer_ticker": str((issuer_record or {}).get("ticker") or "").strip().upper(),
                    "subsidiary_name": subsidiary_name,
                    "aliases": list(aliases or []),
                    "candidate_tickers": top_tickers,
                    "best_ticker": str(verdict.best_ticker or "").strip().upper(),
                    "confidence_bucket": str(verdict.confidence_bucket or "").strip(),
                    "filing_url": filing_url,
                },
            )
        return ""
    if str(verdict.best_entity_kind or "").strip().lower() != "company":
        if callable(discard_logger):
            discard_logger(
                "ex21_promotion",
                "validator_kept_legal_entity",
                {
                    "issuer_ticker": str((issuer_record or {}).get("ticker") or "").strip().upper(),
                    "subsidiary_name": subsidiary_name,
                    "aliases": list(aliases or []),
                    "candidate_tickers": top_tickers,
                    "best_ticker": str(verdict.best_ticker or "").strip().upper(),
                    "best_entity_kind": str(verdict.best_entity_kind or "").strip(),
                    "filing_url": filing_url,
                },
            )
        return ""
    chosen = str(verdict.best_ticker or "").strip().upper()
    if chosen in top_tickers:
        return chosen
    if callable(discard_logger):
        discard_logger(
            "ex21_promotion",
            "validator_best_ticker_not_in_ranked_candidates",
            {
                "issuer_ticker": str((issuer_record or {}).get("ticker") or "").strip().upper(),
                "subsidiary_name": subsidiary_name,
                "aliases": list(aliases or []),
                "candidate_tickers": top_tickers,
                "best_ticker": chosen,
                "filing_url": filing_url,
            },
        )
    return ""


def _phase6_extract_ex21_jurisdiction_legacy(text: str) -> tuple[str, str]:
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        return "", ""
    raw = _phase6_strip_ex21_enumerator_prefix(raw)
    raw = re.sub(r"\s+\d{1,3}%\s*$", "", raw)
    header_markers = (
        "subsidiaries of the registrant",
        "name of subsidiary",
        "jurisdiction of incorporation",
        "jurisdiction",
        "subsidiary name",
    )
    if any(marker in raw.lower() for marker in header_markers):
        return "", ""
    sentence_entity = _phase6_extract_ex21_sentence_entity_name(raw)
    if sentence_entity:
        raw = sentence_entity
    jurisdiction = ""
    name = raw
    for pattern in (
        r"^(.*?)\s+\(([^()]{2,80})\)\s*$",
        r"^(.*?)\s+[–—-]\s+([^–—-]{2,80})\s*$",
        r"^(.*?)\s*,\s*(Delaware|Nevada|California|Texas|Florida|New York|Cayman Islands|Netherlands|Ireland|Canada|England and Wales|United Kingdom|Luxembourg|Singapore|Japan|Germany|France|Switzerland|Israel|Australia|India|Hong Kong)\s*$",
    ):
        match = re.match(pattern, raw, re.I)
        if match:
            name = re.sub(r"\s+", " ", match.group(1)).strip(" ,;:-")
            jurisdiction = re.sub(r"\s+", " ", match.group(2)).strip(" ,;:-")
            break
    name = re.sub(r"\s+", " ", name).strip(" ,;:-")
    if not name or len(name) < 3:
        return "", ""
    return name, jurisdiction


def _phase6_extract_ex21_jurisdiction(text: str) -> tuple[str, str]:
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        return "", ""
    raw = _phase6_clean_ex21_entity_name(raw)
    raw = re.sub(r"\s+\d{1,3}%\s*$", "", raw)
    header_markers = (
        "subsidiaries of the registrant",
        "name of subsidiary",
        "jurisdiction of incorporation",
        "jurisdiction",
        "subsidiary name",
    )
    if any(marker in raw.lower() for marker in header_markers):
        return "", ""
    sentence_entity = _phase6_extract_ex21_sentence_entity_name(raw)
    if sentence_entity:
        raw = sentence_entity

    jurisdiction = ""
    name = raw
    paren_match = re.match(r"^(.*?)\s+\(([^()]{2,80})\)\s*$", raw, re.I)
    if paren_match:
        maybe_name = re.sub(r"\s+", " ", paren_match.group(1)).strip(" ,;:-")
        maybe_jurisdiction = re.sub(r"\s+", " ", paren_match.group(2)).strip(" ,;:-")
        compact = re.sub(r"[^a-z]+", " ", maybe_jurisdiction.lower()).strip()
        compact_no_us = re.sub(r"\bu s\b", "", compact).strip()
        if compact in _PHASE6_EX21_JURISDICTION_TERMS or compact_no_us in _PHASE6_EX21_JURISDICTION_TERMS:
            name = maybe_name
            jurisdiction = maybe_jurisdiction
    if not jurisdiction:
        for pattern in (
            r"^(.*?)\s+[â€“â€”-]\s+([^â€“â€”-]{2,80})\s*$",
            r"^(.*?)\s*,\s*(Delaware|Nevada|California|Texas|Florida|New York|Cayman Islands|Netherlands|Ireland|Canada|England and Wales|United Kingdom|Luxembourg|Singapore|Japan|Germany|France|Switzerland|Israel|Australia|India|Hong Kong)\s*$",
        ):
            match = re.match(pattern, raw, re.I)
            if match:
                name = re.sub(r"\s+", " ", match.group(1)).strip(" ,;:-")
                jurisdiction = re.sub(r"\s+", " ", match.group(2)).strip(" ,;:-")
                break
    name = _phase6_clean_ex21_entity_name(name)
    if not name or len(name) < 3:
        return "", ""
    return name, jurisdiction


_PHASE6_EX21_NON_ENTITY_NAMES = {
    "name",
    "company",
    "entity",
    "parent",
    "subsidiary",
    "subsidiaries",
    "entity name",
    "company name",
    "legal name",
    "name of company",
    "name of entity",
    "name of organization",
    "name of the entity",
    "name of the organization",
    "name of the subsidiary",
    "name of subsidiaries",
    "of incorporation",
    "of the registrant",
    "jurisdiction",
    "jurisdiction of incorporation",
    "name of subsidiary",
    "subsidiary name",
    "none",
    "none.",
    "affiliate",
    "affiliates",
    "corporation",
    "corporations",
    "directors",
    "domestic",
    "europe",
    "foreign subsidiary",
    "guarantor",
    "incorporated",
    "incorporation",
    "incorporation organization",
    "international",
    "listofsubsidiaries",
    "organization",
    "operations",
    "or organization",
    "ownership",
    "registrant",
    "signature",
    "state of",
    "state of origin",
    "subsidiary*",
    "subsidiary legal name",
    "listing a",
    "of subsidiaries",
}
_PHASE6_EX21_DESIGNATOR_ONLY_TOKENS = {
    "ab",
    "ag",
    "aps",
    "bank",
    "bv",
    "co",
    "company",
    "corp",
    "corporation",
    "dac",
    "gmbh",
    "inc",
    "incorporated",
    "limited",
    "llc",
    "llp",
    "lp",
    "ltd",
    "nv",
    "oy",
    "oyj",
    "plc",
    "pte",
    "pty",
    "sa",
    "sas",
    "se",
    "sl",
    "spa",
    "srl",
    "trust",
}
_PHASE6_EX21_NAME_CONTINUATION_TOKENS = _PHASE6_EX21_DESIGNATOR_ONLY_TOKENS | {
    "capital",
    "distribution",
    "finance",
    "holdco",
    "holding",
    "holdings",
    "international",
    "investment",
    "luxco",
    "management",
    "midco",
    "payments",
    "properties",
    "services",
    "solutions",
    "technologies",
    "topco",
    "trading",
    "ventures",
}
_PHASE6_EX21_FORMAL_SUFFIX_TOKEN_RE = re.compile(
    r"\b(?:"
    r"inc|incorporated|corp|corporation|company|co|limited|ltd|llc|llp|lp|plc|"
    r"gmbh|ag|ab|sa|sas|bv|nv|se|oy|oyj|ltda|spa|srl|sl|aps|bank|trust|dac|"
    r"pte|pty"
    r")\b",
    re.I,
)
_PHASE6_EX21_FORMAL_SUFFIX_RE = re.compile(
    r"(?:"
    r"\b(?:inc|incorporated|corp|corporation|company|co|limited|ltd|llc|llp|lp|plc|"
    r"gmbh|ag|ab|sa|sas|bv|nv|se|oy|oyj|ltda|spa|srl|sl|aps|bank|trust|dac)\b|"
    r"\b(?:pte|pty)\s+ltd\b"
    r")\.?\s*$",
    re.I,
)
_PHASE6_EX21_EXHIBIT_LABEL_RE = re.compile(
    r"^(?:ex(?:hibit)?[-\s]?\d+(?:\.\d+)?(?:[-_][a-z0-9]+)?|exhibit\s+\d+(?:\.\d+)?)$",
    re.I,
)
_PHASE6_EX21_EXHIBIT_STEM_RE = re.compile(
    r"^[a-z0-9._-]*(?:ex(?:hibit)?[-_. ]?21(?:[-_. ]?1)?)[a-z0-9._-]*$",
    re.I,
)
_PHASE6_EX21_SECTION_HEADING_RE = re.compile(
    r"^(?:(?:[A-Z]|[IVX]+)[\.\)]\s+)?(?:direct|indirect|other|significant)\s+subsidiar(?:y|ies)\b",
    re.I,
)
_PHASE6_EX21_HTML_FILENAME_RE = re.compile(r"^[a-z0-9._-]+\.htm(?:l)?$", re.I)
_PHASE6_EX21_LIST_FILENAME_STEM_RE = re.compile(
    r"^(?:ex(?:hibit)?[-_ ]?\d+(?:\.\d+)?[-_ ]*)?listofsubsidiar(?:y|ies)(?:\.htm(?:l)?)?$",
    re.I,
)
_PHASE6_EX21_FOOTNOTE_ONLY_RE = re.compile(r"^\(\*?\d+\*?\)$")
_PHASE6_EX21_NUMERIC_ONLY_RE = re.compile(r"^\d+$")
_PHASE6_EX21_ENUMERATOR_PREFIX_RE = re.compile(r"^(?:\(\*?[0-9a-z]+\*?\)|[0-9]+[\.\)])\s+", re.I)
_PHASE6_EX21_JURISDICTION_ONLY_RE = re.compile(
    r"^(?:[A-Z][A-Za-z.'()/-]+(?:\s+[A-Z][A-Za-z.'()/-]+){0,4}(?:,\s*U\.S\.)?)$"
)
_PHASE6_EX21_JURISDICTION_TERMS = {
    "alabama",
    "alaska",
    "alberta",
    "argentina",
    "arizona",
    "arkansas",
    "australia",
    "austria",
    "bahamas",
    "belgium",
    "bermuda",
    "brazil",
    "british columbia",
    "british virgin islands",
    "california",
    "canada",
    "cayman islands",
    "chile",
    "china",
    "colombia",
    "colorado",
    "connecticut",
    "delaware",
    "district of columbia",
    "england",
    "england and wales",
    "finland",
    "florida",
    "france",
    "germany",
    "georgia",
    "guatemala",
    "hong kong",
    "hyderabad",
    "idaho",
    "illinois",
    "india",
    "indiana",
    "indonesia",
    "ireland",
    "israel",
    "italy",
    "iowa",
    "japan",
    "jersey",
    "kansas",
    "kentucky",
    "kazakhstan",
    "lithuania",
    "louisiana",
    "luxembourg",
    "maine",
    "malaysia",
    "maryland",
    "massachusetts",
    "mauritius",
    "michigan",
    "mexico",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nevada",
    "netherlands",
    "nebraska",
    "new hampshire",
    "new jersey",
    "new mexico",
    "new york",
    "new zealand",
    "north carolina",
    "north dakota",
    "nicaragua",
    "norway",
    "ohio",
    "oklahoma",
    "ontario",
    "oregon",
    "panama",
    "pennsylvania",
    "peru",
    "philippines",
    "poland",
    "puerto rico",
    "quebec",
    "rhode island",
    "saudi arabia",
    "singapore",
    "south korea",
    "south carolina",
    "south africa",
    "south dakota",
    "spain",
    "sweden",
    "switzerland",
    "taiwan",
    "thailand",
    "the netherlands",
    "tennessee",
    "texas",
    "turkey",
    "united arab emirates",
    "united kingdom",
    "united states",
    "uzbekistan",
    "utah",
    "vermont",
    "vietnam",
    "virginia",
    "washington",
    "west virginia",
    "wilmington",
    "wisconsin",
    "wyoming",
    "bangladesh",
    "portsmouth",
}
_PHASE6_EX21_SENTENCE_ENTITY_BREAK_RES = [
    re.compile(
        r",\s+(?:a|an)\s+[^,;]{0,160}\b(?:corporation|company|limited liability company|limited partnership|private limited company|branch|bank)\b",
        re.I,
    ),
    re.compile(
        r",\s+a\s+(?:(?:directly|drectly)\s+)?[^,;]{0,80}\bsubsidiary\b\s*,\s*which\s+was\s+(?:incorporated|formed|organized)\b",
        re.I,
    ),
    re.compile(r",\s+which\s+was\s+(?:incorporated|formed|organized)\b", re.I),
    re.compile(r",\s+(?:a|an)\s+wholly[- ]owned\s+subsidiary\s+of\b", re.I),
    re.compile(r",\s+wholly[- ]owned\s+subsidiary\s+of\b", re.I),
    re.compile(r",\s+(?:organized|incorporated)\s+under\s+the\s+laws\s+of\b", re.I),
    re.compile(r",\s+is\s+(?:a|an)\s+[^,;]{0,120}\b(?:subsidiary|corporation|company|limited liability company|private limited company|branch)\b", re.I),
    re.compile(r"\s+is\s+a\s+wholly[- ]owned\s+subsidiary\s+of\b", re.I),
    re.compile(r"\s+is\s+wholly[- ]owned\s+subsidiary\s+of\b", re.I),
    re.compile(r"\s+is\s+a\s+subsidiary\s+of\b", re.I),
    re.compile(r"\s+is\s+the\s+company(?:'|’)?s\s+sole\s+subsidiary\b", re.I),
    re.compile(r"\s+and\s+a\s+wholly[- ]owned\s+subsidiary\s+of\b", re.I),
    re.compile(r"\s+and\s+wholly[- ]owned\s+subsidiary\s+of\b", re.I),
    re.compile(r"\s+owns\s+the\s+following\s+subsidiar(?:y|ies)\b", re.I),
    re.compile(r"\s+has\s+the\s+following\s+wholly\s+owned\s+subsidiar(?:y|ies)\b", re.I),
    re.compile(r"\s+collectively\s+own(?:\s+\d+%?)?\s+of\s+the\s+following\s+subsidiar(?:y|ies)\b", re.I),
    re.compile(r"\s+each\s+partially\s+own\s+the\s+following\s+subsidiar(?:y|ies)\b", re.I),
]
_PHASE6_EX21_ENTITY_KEY_SLUG_MAX = 96
_PHASE6_EX21_ENTITY_KEY_MAX = 220
_PHASE6_SEC_TEXT_EXTENSIONS = {".htm", ".html", ".txt", ".xml"}
PHASE6_SEC_RAW_CACHE_SUBDIR = (os.environ.get("PHASE6_SEC_RAW_CACHE_SUBDIR", "sec_edgar_filings") or "sec_edgar_filings").strip()


def _phase6_is_textual_sec_document_name(name: str) -> bool:
    ext = os.path.splitext(str(name or "").strip().lower())[1]
    return ext in _PHASE6_SEC_TEXT_EXTENSIONS


def _phase6_strip_ex21_enumerator_prefix(text: str) -> str:
    return _PHASE6_EX21_ENUMERATOR_PREFIX_RE.sub("", str(text or "").strip(), count=1)


_PHASE6_EX21_TRAILING_OWNERSHIP_NOTE_RE = re.compile(
    r"\s*\((?:\d+(?:\.\d+)?%\s*(?:owned|interest|sub\b)?[^)]*|[^)]*\b(?:owned|interest|ownership|minority interest|sub of|subsidiary of|subsidiaries of)\b[^)]*)\)\s*$",
    re.I,
)
_PHASE6_EX21_TRAILING_STATE_NOTE_RE = re.compile(
    r"^(?P<name>.+?)(?:\s*[–—-]\s*|\s*,\s*|\s+)(?:incorporated|organized)\s+(?:in|under)\b.+$",
    re.I,
)


_PHASE6_EX21_TRAILING_NOTE_RE = re.compile(
    r"^(?P<name>.+?)\s*\((?P<note>[^()]{1,160})\)\s*$",
    re.I,
)
_PHASE6_EX21_INLINE_DBA_RE = re.compile(
    r"^(?P<name>.+?)\s+\b(?:d/?b/?a|doing business as)\b\s+.+$",
    re.I,
)
_PHASE6_EX21_TRAILING_NOTE_KEYWORDS = (
    "dba",
    "doing business as",
    "trade name",
    "f/k/a",
    "fka",
    "formerly known as",
    "including dba",
    "with branches in",
    "incorporated in",
    "incorporated under",
    "organized in",
    "organized under",
    "place of incorporation",
)


def _phase6_should_strip_ex21_trailing_note(note: str) -> bool:
    compact = re.sub(r"\s+", " ", str(note or "").strip())
    if not compact:
        return False
    lower = compact.casefold()
    if _PHASE6_EX21_FOOTNOTE_ONLY_RE.match(f"({compact})"):
        return True
    if re.fullmatch(r"[*\d.\- /]+", compact):
        return True
    if compact.startswith(("\"", "'", "“", "”")):
        return True
    return any(keyword in lower for keyword in _PHASE6_EX21_TRAILING_NOTE_KEYWORDS)


def _phase6_ex21_should_combine_name_fragments(left: str, right: str) -> str:
    left_clean = _phase6_clean_ex21_entity_name(left)
    right_raw = re.sub(r"\s+", " ", str(right or "").strip()).strip(" ,;:-?")
    right_clean = _phase6_clean_ex21_entity_name(right_raw)
    if not left_clean or not right_clean:
        return ""
    if _phase6_ex21_is_non_entity_boilerplate(left_clean.casefold()) or _phase6_ex21_is_non_entity_boilerplate(right_clean.casefold()):
        return ""
    if _phase6_ex21_name_has_formal_suffix(left_clean):
        return ""
    left_words = [token for token in re.split(r"\s+", left_clean) if token]
    if len(left_words) > 3:
        return ""
    if not (_phase6_ex21_next_text_indicates_name_continuation(right_clean) or _phase6_ex21_name_has_formal_suffix(right_clean)):
        return ""
    right_for_combine = right_raw if re.match(r"^\([^()]{1,80}\)\s+[A-Za-z].+$", right_raw) else right_clean
    combined = re.sub(r"\s+", " ", f"{left_clean} {right_for_combine}").strip(" ,;:-?")
    if combined == left_clean or combined == right_clean:
        return ""
    if not _phase6_is_probable_ex21_entity_name(combined):
        return ""
    return combined


def _phase6_clean_ex21_entity_name(text: str) -> str:
    clean = re.sub(r"\s+", " ", _phase6_strip_ex21_enumerator_prefix(str(text or "").strip())).strip(" ,;:-?")
    if not clean:
        return ""
    clean = clean.lstrip("\u200b\u2022\u25aa*[] ")
    for pattern in (
        re.compile(
            r"^(?P<name>.+),\s+a\s+(?:(?:directly|drectly)\s+)?[^,]{0,80}\bsubsidiary\b\s*,\s*which\s+was\s+(?:incorporated|formed|organized)\b.*$",
            re.I,
        ),
        re.compile(r"^(?P<name>.+),\s*which\s+was\s+(?:incorporated|formed|organized)\b.*$", re.I),
        re.compile(r"^.+?\bindirect subsidiaries\b\s+(?P<name>.+)$", re.I),
        re.compile(
            r"^the unconsolidated subsidiary of\s+.+?\s+is\s+(?P<name>.+?)(?:,\s+a\s+[^,]+)?\.?$",
            re.I,
        ),
        re.compile(
            r"^most significant subsidiary of\s+.+?\s+is\s+(?P<name>.+?)(?:,\s+a\s+[^,]+)?\.?$",
            re.I,
        ),
        re.compile(
            r"^(?P<name>.+),\s+a\s+\d+(?:\.\d+)?%-owned\s+subsidiary\s+of\b.*$",
            re.I,
        ),
        re.compile(
            r"^\.?\s*name and address of subsidiary incorporated\s+\d+\.\s+(?P<name>.+?)\s+\d{2,}\b.*$",
            re.I,
        ),
        re.compile(
            r"^(?P<name>.+?)\s+of\s+[^,]+,\s*[^,]+?\s+is\s+the\s+company(?:'|’)?s\s+sole\s+subsidiary\.?$",
            re.I,
        ),
        re.compile(
            r"^(?P<name>.+?)\s+is\s+the\s+company(?:'|’)?s\s+sole\s+subsidiary\.?$",
            re.I,
        ),
    ):
        match = pattern.match(clean)
        if match:
            clean = re.sub(r"\s+", " ", str(match.group("name") or "")).strip(" ,;:-?")
            break
    dba_match = _PHASE6_EX21_INLINE_DBA_RE.match(clean)
    if dba_match:
        clean = re.sub(r"\s+", " ", str(dba_match.group("name") or "")).strip(" ,;:-?")
    clean = _PHASE6_EX21_TRAILING_OWNERSHIP_NOTE_RE.sub("", clean).strip(" ,;:-?")
    state_match = _PHASE6_EX21_TRAILING_STATE_NOTE_RE.match(clean)
    if state_match:
        clean = re.sub(r"\s+", " ", state_match.group("name")).strip(" ,;:-?")
    while True:
        note_match = _PHASE6_EX21_TRAILING_NOTE_RE.match(clean)
        if note_match is None:
            break
        note = str(note_match.group("note") or "").strip()
        if not _phase6_should_strip_ex21_trailing_note(note):
            break
        clean = re.sub(r"\s+", " ", str(note_match.group("name") or "")).strip(" ,;:-?")
    return clean


def _phase6_extract_ex21_sentence_entity_name(text: str) -> str:
    clean = _phase6_clean_ex21_entity_name(text)
    if not clean:
        return ""
    break_at = None
    for pattern in _PHASE6_EX21_SENTENCE_ENTITY_BREAK_RES:
        match = pattern.search(clean)
        if match is None:
            continue
        if break_at is None or match.start() < break_at:
            break_at = match.start()
    if break_at is None or break_at <= 0:
        return ""
    candidate = re.sub(r"\s+", " ", clean[:break_at]).strip(" ,;:-")
    if len(candidate) < 3 or candidate == clean:
        return ""
    return candidate


def _phase6_extract_declared_sec_charset(content: bytes) -> str:
    head = bytes(content[:4096] or b"")
    if not head:
        return ""
    ascii_head = head.decode("ascii", errors="ignore")
    for pattern in (
        r"charset\s*=\s*['\"]?\s*([A-Za-z0-9._-]+)",
        r"encoding\s*=\s*['\"]\s*([A-Za-z0-9._-]+)\s*['\"]",
    ):
        match = re.search(pattern, ascii_head, re.I)
        if match:
            return str(match.group(1) or "").strip().lower()
    return ""


def _phase6_sec_bytes_are_binary_media(content: bytes) -> bool:
    raw = bytes(content or b"")
    return raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"GIF87a") or raw.startswith(b"GIF89a") or raw.startswith(b"BM")


def _phase6_decode_sec_document_bytes(content: bytes) -> str:
    raw = bytes(content or b"")
    if not raw or _phase6_sec_bytes_are_binary_media(raw):
        return ""

    def _score_text(text: str) -> tuple[int, int]:
        sample = text[:8192]
        lower = sample.casefold()
        markers = sum(
            1
            for marker in (
                "<html",
                "<document>",
                "<type>",
                "<table",
                "subsidiar",
                "jurisdiction",
                "name of subsidiary",
                "exhibit 21",
            )
            if marker in lower
        )
        replacement_count = sample.count("\ufffd")
        control_count = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\r\n\t")
        other_control_count = sum(
            1 for ch in sample if unicodedata.category(ch) in {"Cc", "Cf", "Cs"} and ch not in "\r\n\t"
        )
        printable_ratio = sum(1 for ch in sample if ch.isprintable() or ch in "\r\n\t") / max(1, len(sample))
        penalty = replacement_count * 2000 + control_count * 400 + other_control_count * 200
        if printable_ratio < 0.80:
            penalty += int((0.80 - printable_ratio) * 10000)
        return penalty - markers * 50, markers

    candidates: list[tuple[tuple[int, int], str]] = []
    seen_encodings: set[str] = set()
    for encoding in (
        _phase6_extract_declared_sec_charset(raw),
        "utf-8",
        "utf-16",
        "cp1252",
        "latin-1",
    ):
        enc = str(encoding or "").strip().lower()
        if not enc or enc in seen_encodings:
            continue
        seen_encodings.add(enc)
        try:
            text = raw.decode(enc, errors="strict")
        except Exception:
            continue
        candidates.append((_score_text(text), text))
    if not candidates:
        fallback = raw.decode("utf-8", errors="replace")
        score, markers = _score_text(fallback)
        if markers == 0 and score > 1500:
            return ""
        return fallback
    candidates.sort(key=lambda item: item[0])
    (score, markers), best_text = candidates[0]
    if markers == 0 and score > 1500:
        return ""
    return best_text


def _phase6_read_sec_document_text_from_path(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as handle:
            return _phase6_decode_sec_document_bytes(handle.read())
    except Exception:
        return ""


def _phase6_is_probable_ex21_entity_name(text: str) -> bool:
    raw = re.sub(r"\s+", " ", str(text or "").strip()).strip(" ,;:-")
    raw_lower = raw.casefold()
    if _phase6_ex21_is_non_entity_boilerplate(raw_lower):
        return False
    if "pursuant to item 601" in raw_lower or "would not constitute a significant subsidiary" in raw_lower:
        return False
    if "subsidiaries of the registrant" in raw_lower:
        return False
    clean = _phase6_clean_ex21_entity_name(raw)
    if not clean:
        return False
    if "\ufffd" in clean:
        return False
    if any(unicodedata.category(ch) in {"Cc", "Cf", "Cs"} and ch not in "\r\n\t" for ch in clean):
        return False
    lower = clean.casefold()
    lower_compact_words = re.sub(r"[^a-z]+", " ", lower).strip()
    if lower in _PHASE6_EX21_NON_ENTITY_NAMES:
        return False
    if lower_compact_words in _PHASE6_EX21_NON_ENTITY_NAMES:
        return False
    if _PHASE6_EX21_EXHIBIT_LABEL_RE.match(clean):
        return False
    if _PHASE6_EX21_EXHIBIT_STEM_RE.match(clean):
        return False
    if _PHASE6_EX21_SECTION_HEADING_RE.match(clean):
        return False
    if _PHASE6_EX21_HTML_FILENAME_RE.match(clean):
        return False
    if _PHASE6_EX21_LIST_FILENAME_STEM_RE.match(clean):
        return False
    if _PHASE6_EX21_FOOTNOTE_ONLY_RE.match(clean):
        return False
    if _PHASE6_EX21_NUMERIC_ONLY_RE.match(clean):
        return False
    # Reject numeric-only fragments like "27 29 29" or "12/31/25" that can leak out of
    # malformed EX-21 table rows or OCR'd coordinate/jurisdiction cells.
    if not re.search(r"[A-Za-z]", clean):
        return False
    if _phase6_ex21_is_non_entity_boilerplate(lower):
        return False
    if len(clean) > 160:
        return False
    if not re.sub(r"[^A-Za-z0-9]+", "", clean):
        return False
    if _phase6_ex21_name_is_designator_only(clean):
        return False
    compact_alnum = re.sub(r"[^a-z0-9]+", "", lower)
    if "listofsubsidiar" in compact_alnum:
        return False
    if re.fullmatch(r"[A-Za-z0-9._-]+", clean):
        if any(fragment in compact_alnum for fragment in ("subsidiar", "listingofs", "exhibit")):
            return False
    compact = re.sub(r"[^a-z]+", " ", lower).strip()
    compact_no_us = re.sub(r"\bu s\b", "", compact).strip()
    if compact in _PHASE6_EX21_JURISDICTION_TERMS:
        return False
    if compact_no_us in _PHASE6_EX21_JURISDICTION_TERMS:
        return False
    if _PHASE6_EX21_JURISDICTION_ONLY_RE.match(clean) and compact_no_us in _PHASE6_EX21_JURISDICTION_TERMS:
        return False
    words = [token for token in re.split(r"\s+", clean) if token]
    if len(words) == 1:
        token = re.sub(r"[^A-Za-z0-9]+", "", words[0]).strip()
        if token and len(token) <= 8:
            return False
    return True


def _phase6_ex21_name_is_designator_only(text: str) -> bool:
    tokens = [token for token in re.findall(r"[A-Za-z]+", str(text or "").casefold()) if token]
    return bool(tokens) and all(token in _PHASE6_EX21_DESIGNATOR_ONLY_TOKENS for token in tokens)


def _phase6_ex21_name_has_formal_suffix(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    return bool(_PHASE6_EX21_FORMAL_SUFFIX_RE.search(normalized)) or normalized.lower().endswith((" k.k.", " k.k"))


def _phase6_ex21_formal_suffix_token_count(text: str) -> int:
    normalized = re.sub(r"[^\w\s]+", " ", str(text or ""))
    return len(_PHASE6_EX21_FORMAL_SUFFIX_TOKEN_RE.findall(normalized))


def _phase6_ex21_next_text_indicates_name_continuation(text: str) -> bool:
    tokens = [token for token in re.findall(r"[A-Za-z]+", str(text or "").casefold()) if token]
    if not tokens:
        return False
    if len(tokens) >= 2 and " ".join(tokens[:2]) in {"pte ltd", "pty ltd"}:
        return True
    return tokens[0] in _PHASE6_EX21_NAME_CONTINUATION_TOKENS


def _phase6_extract_ex21_compact_jurisdiction_entries(text: str) -> list[dict]:
    clean = html.unescape(re.sub(r"\s+", " ", str(text or "").strip()))
    if not clean:
        return []
    lower = clean.casefold()
    header_match = re.search(
        r"jurisdiction of incorporation(?: or organization)?|jurisdiction of organization|state of incorporation(?: or organization)?",
        lower,
        re.I,
    )
    if header_match is None:
        return []
    working = clean[header_match.end():].strip(" :;-")
    if not working:
        return []
    working = re.split(
        r"\*\s+includes subsidiaries|includes subsidiaries that do not fall under|includes subsidiaries that do not constitute|none\.\s*$",
        working,
        maxsplit=1,
        flags=re.I,
    )[0]
    working = re.sub(r"\s+", " ", working).strip(" ,;:-")
    if not working:
        return []
    jurisdiction_terms = sorted(
        {term for term in _PHASE6_EX21_JURISDICTION_TERMS if " " in term or len(term) >= 4},
        key=len,
        reverse=True,
    )
    if not jurisdiction_terms:
        return []
    jurisdiction_re = re.compile(
        r"\b(" + "|".join(re.escape(term) for term in jurisdiction_terms) + r")\b",
        re.I,
    )
    entries: list[dict] = []
    remaining = working
    while remaining:
        remaining = remaining.lstrip(" ,;:-")
        if not remaining:
            break
        matches = list(jurisdiction_re.finditer(remaining))
        if not matches:
            break
        chosen_match = None
        chosen_name = ""
        chosen_jurisdiction = ""
        for match in matches[:8]:
            name = re.sub(r"\s+", " ", remaining[:match.start()]).strip(" ,;:-")
            jurisdiction = re.sub(r"\s+", " ", match.group(1)).strip(" ,;:-")
            if not name or not _phase6_is_probable_ex21_entity_name(name):
                continue
            if _phase6_ex21_name_is_designator_only(name):
                continue
            if _phase6_ex21_formal_suffix_token_count(name) > 2:
                continue
            if _phase6_ex21_next_text_indicates_name_continuation(remaining[match.end():]):
                continue
            chosen_match = match
            chosen_name = name
            chosen_jurisdiction = jurisdiction
            if _phase6_ex21_name_has_formal_suffix(name):
                break
        if chosen_match is None:
            break
        aliases, _norms, _risk, _needs_confirmation = _build_legal_entity_aliases(chosen_name)
        entries.append({"legal_name": chosen_name, "aliases": aliases, "jurisdiction": chosen_jurisdiction})
        remaining = remaining[chosen_match.end():]
    return entries


def _phase6_ex21_matches_issuer_name(issuer_record: dict | None, legal_name: str) -> bool:
    candidate = re.sub(r"[\s*†‡]+$", "", str(legal_name or "").strip())
    candidate_norm = _normalize_entity_alias(candidate)
    candidate_compact = re.sub(r"[^a-z0-9]+", "", _canonical_company_name(candidate).lower())
    if not candidate_norm:
        return False
    issuer_record = issuer_record or {}
    issuer_norms = {
        _normalize_entity_alias(str(value or "").strip())
        for value in (
            issuer_record.get("canonical_name"),
            issuer_record.get("name"),
            issuer_record.get("listing_name"),
            issuer_record.get("sec_title"),
            _company_record_public_name(issuer_record),
        )
        if str(value or "").strip()
    }
    issuer_norms.discard("")
    issuer_compacts = {
        re.sub(r"[^a-z0-9]+", "", _canonical_company_name(str(value or "")).lower())
        for value in (
            issuer_record.get("canonical_name"),
            issuer_record.get("name"),
            issuer_record.get("listing_name"),
            issuer_record.get("sec_title"),
            _company_record_public_name(issuer_record),
        )
        if str(value or "").strip()
    }
    issuer_compacts.discard("")
    return candidate_norm in issuer_norms or (candidate_compact and candidate_compact in issuer_compacts)


def _phase6_build_ex21_entity_key(issuer_ticker: str, legal_name: str, accession_compact: str = "") -> str:
    ticker = str(issuer_ticker or "").strip().upper() or "UNK"
    normalized = _normalize_entity_alias(legal_name)
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")[:_PHASE6_EX21_ENTITY_KEY_SLUG_MAX]
    digest = hashlib.sha1(
        f"{ticker}|{str(legal_name or '').strip()}".encode("utf-8")
    ).hexdigest()[:16]
    if slug:
        key = f"ex21:{ticker}:{slug}:{digest}"
    else:
        key = f"ex21:{ticker}:{digest}"
    return key[:_PHASE6_EX21_ENTITY_KEY_MAX]


def _phase6_merge_ex21_fragment_entries(entries: list[dict]) -> list[dict]:
    merged: list[dict] = []
    idx = 0
    source_entries = list(entries or [])
    while idx < len(source_entries):
        current = dict(source_entries[idx] or {})
        current_name = str(current.get("legal_name") or "").strip()
        if idx + 1 < len(source_entries):
            next_entry = dict(source_entries[idx + 1] or {})
            next_name = str(next_entry.get("legal_name") or "").strip()
            combined_name = _phase6_ex21_should_combine_name_fragments(current_name, next_name)
            if combined_name:
                aliases, _norms, _risk, _needs_confirmation = _build_legal_entity_aliases(combined_name)
                merged.append({
                    **current,
                    "legal_name": combined_name,
                    "aliases": aliases,
                    "jurisdiction": str(current.get("jurisdiction") or next_entry.get("jurisdiction") or "").strip(),
                })
                idx += 2
                continue
        merged.append(current)
        idx += 1
    return merged


def _phase6_dedupe_ex21_entries(entries: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for entry in _phase6_merge_ex21_fragment_entries(entries):
        name = re.sub(r"\s+", " ", str(entry.get("legal_name") or "").strip())
        if not name or not _phase6_is_probable_ex21_entity_name(name):
            continue
        key = _normalize_entity_alias(name) or name.casefold()
        prev = deduped.get(key)
        if prev is None:
            deduped[key] = dict(entry)
            continue
        if entry.get("jurisdiction") and not prev.get("jurisdiction"):
            prev["jurisdiction"] = entry.get("jurisdiction")
        aliases = list(prev.get("aliases") or [])
        for alias in entry.get("aliases") or []:
            if alias not in aliases:
                aliases.append(alias)
        prev["aliases"] = aliases
    return sorted(deduped.values(), key=lambda item: str(item.get("legal_name") or ""))


def _phase6_extract_ex21_entries_from_html(raw_text: str) -> list[dict]:
    html = str(raw_text or "")
    if not html.strip():
        return []
    entries: list[dict] = []
    try:
        from bs4 import BeautifulSoup
    except Exception:
        BeautifulSoup = None
    table_entries_found = False
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "lxml")
        except Exception:
            soup = BeautifulSoup(html, "html.parser")
        for row in soup.select("table tr"):
            cells = [re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip() for cell in row.find_all(["td", "th"])]
            if not cells:
                continue
            if any(marker in " ".join(cells).lower() for marker in (
                "name of subsidiary",
                "subsidiary name",
                "jurisdiction of incorporation",
            )):
                continue
            name, jurisdiction = _phase6_extract_ex21_jurisdiction(cells[0])
            if name and len(cells) >= 2 and not jurisdiction:
                tail = re.sub(r"\s+", " ", cells[-1]).strip(" ,;:-")
                if tail and len(tail) <= 80:
                    jurisdiction = tail
            if name and _phase6_is_probable_ex21_entity_name(name):
                aliases, _norms, _risk, _needs_confirmation = _build_legal_entity_aliases(name)
                entries.append({"legal_name": name, "aliases": aliases, "jurisdiction": jurisdiction})
                table_entries_found = True
        for item in soup.select("li"):
            text = re.sub(r"\s+", " ", item.get_text(" ", strip=True)).strip()
            name, jurisdiction = _phase6_extract_ex21_jurisdiction(text)
            if name and _phase6_is_probable_ex21_entity_name(name):
                aliases, _norms, _risk, _needs_confirmation = _build_legal_entity_aliases(name)
                entries.append({"legal_name": name, "aliases": aliases, "jurisdiction": jurisdiction})
    if table_entries_found:
        return _phase6_dedupe_ex21_entries(entries)
    plain_text_sources: list[str] = []
    try:
        import sec_edgar_supply_chain as sec_mod
        plain_text_sources.append(sec_mod._html_to_plain_text_fast(html))
    except Exception:
        plain_text_sources.append(re.sub(r"<[^>]+>", " ", html))
    if BeautifulSoup is not None:
        try:
            plain_text_sources.append(soup.get_text("\n"))
        except Exception:
            pass
    for plain_text in plain_text_sources:
        capture = False
        for line in plain_text.splitlines():
            clean = re.sub(r"\s+", " ", line).strip()
            if not clean:
                continue
            compact_entries = _phase6_extract_ex21_compact_jurisdiction_entries(clean)
            if compact_entries:
                entries.extend(compact_entries)
                capture = True
                continue
            lower = clean.lower()
            if "subsidiaries of the registrant" in lower or lower.startswith("subsidiaries"):
                capture = True
                continue
            if not capture and len(entries) >= 3:
                continue
            if capture and ("consent of independent" in lower or "signature" in lower or "exhibit 23" in lower or "exhibit 31" in lower):
                break
            if clean.count(",") >= 4 and len(clean) > 120:
                for segment in re.split(r"\s{2,}|;|•|·|â€¢", clean):
                    name, jurisdiction = _phase6_extract_ex21_jurisdiction(segment)
                    if name and _phase6_is_probable_ex21_entity_name(name):
                        aliases, _norms, _risk, _needs_confirmation = _build_legal_entity_aliases(name)
                        entries.append({"legal_name": name, "aliases": aliases, "jurisdiction": jurisdiction})
                continue
            name, jurisdiction = _phase6_extract_ex21_jurisdiction(clean)
            if name and _phase6_is_probable_ex21_entity_name(name):
                aliases, _norms, _risk, _needs_confirmation = _build_legal_entity_aliases(name)
                entries.append({"legal_name": name, "aliases": aliases, "jurisdiction": jurisdiction})
    return _phase6_dedupe_ex21_entries(entries)


_PHASE6_EX21_NAME_RE = re.compile(
    r"(?:"
    r"ex(?:hibit)?[-_a-z0-9]*21(?:1|[._-]?1)\b|"
    r"xexx211\b|"
    r"exx211\b"
    r")",
    re.I,
)


def _phase6_sec_accession_dirs(cik_pad: str, accession_compact: str) -> list[str]:
    clean_cik = str(cik_pad or "").strip().zfill(10)
    clean_accession = str(accession_compact or "").strip()
    if not clean_cik or clean_cik == "0000000000" or not clean_accession:
        return []
    out: list[str] = []
    seen: set[str] = set()
    ordered_subdirs = [PHASE6_SEC_RAW_CACHE_SUBDIR, "sec_edgar_filings", "historical_10k_filings", "phase6"]
    for subdir in ordered_subdirs:
        path = os.path.join(NEXUS_CACHE_DIR, subdir, clean_cik, clean_accession)
        if path in seen or not os.path.isdir(path):
            continue
        seen.add(path)
        out.append(path)
    return out


def _phase6_find_cached_sec_document(cik_pad: str, accession_compact: str, document_name: str = "") -> str:
    clean_name = os.path.basename(str(document_name or "").strip())
    if not clean_name:
        return ""
    for base_dir in _phase6_sec_accession_dirs(cik_pad, accession_compact):
        candidate = os.path.join(base_dir, clean_name)
        if os.path.isfile(candidate):
            return candidate
    return ""


def _phase6_cached_sec_document_names(cik_pad: str, accession_compact: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for base_dir in _phase6_sec_accession_dirs(cik_pad, accession_compact):
        try:
            for filename in os.listdir(base_dir):
                if filename in seen:
                    continue
                seen.add(filename)
                names.append(filename)
        except Exception:
            continue
    return sorted(names)


def _phase6_is_ex21_document_name(name: str) -> bool:
    lower = str(name or "").strip().lower()
    if not lower:
        return False
    if not _phase6_is_textual_sec_document_name(lower):
        return False
    compact = re.sub(r"[^a-z0-9]+", "", lower)
    if (
        _PHASE6_EX21_NAME_RE.search(lower)
        or "exhibit21" in lower
        or "exhibit211" in lower
        or "xexx211" in compact
        or "exx211" in compact
        or "exhibit211" in compact
    ):
        return True
    return "subsidiar" in lower and lower.endswith((".htm", ".html", ".txt", ".xml"))


def _phase6_fetch_sec_filing_index(sec_mod, filing: dict) -> dict | None:
    cik_pad = str(filing.get("cik") or "").strip().zfill(10)
    accession_compact = str(filing.get("accession_compact") or "").strip()
    if not cik_pad or not accession_compact:
        return None
    cache_path = _nexus_cache_path("phase6", f"ex21_index_{cik_pad}_{accession_compact}.json")
    cached = _nexus_read_cached_json(cache_path, NEXUS_CACHE_MAX_AGE_HISTORIC)
    if isinstance(cached, dict):
        _nexus_stage_cache_hit(1)
        return cached
    cik_int = str(int(cik_pad)) if cik_pad != "0000000000" else ""
    if not cik_int:
        return None
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_compact}/index.json"
    try:
        response = sec_mod._sec_rate_limited_get(
            index_url,
            headers=sec_mod._get_sec_request_headers(),
            timeout=30,
            label=f"Phase 6 EX-21 index {cik_pad} {accession_compact}",
        )
        payload = response.json() or {}
        _nexus_write_cached_json(cache_path, payload)
        _nexus_stage_download(1)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _phase6_submission_txt_name(accession_compact: str) -> str:
    accession = re.sub(r"[^0-9]+", "", str(accession_compact or "").strip())
    if len(accession) != 18:
        return ""
    return f"{accession[:10]}-{accession[10:12]}-{accession[12:]}.txt"


def _phase6_is_invalid_sec_text_response(content: bytes) -> bool:
    if not content or len(content) < 128:
        return True
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
    return any(marker in sample for marker in bad_markers)


def _phase6_fetch_sec_document_text_transient(sec_mod, filing_url: str, *, label: str) -> str:
    if not sec_mod or not filing_url:
        return ""
    try:
        response = sec_mod._sec_rate_limited_get(
            filing_url,
            headers=sec_mod._get_sec_request_headers(),
            timeout=30,
            label=label,
        )
    except Exception:
        return ""
    content = bytes(getattr(response, "content", b"") or b"")
    if _phase6_is_invalid_sec_text_response(content):
        return ""
    return _phase6_decode_sec_document_bytes(content)


def _phase6_fetch_sec_submission_text(sec_mod, filing: dict, index_payload: dict | None) -> str:
    cik_pad = str(filing.get("cik") or "").strip().zfill(10)
    accession_compact = str(filing.get("accession_compact") or "").strip()
    if not cik_pad or not accession_compact:
        return ""
    txt_name = ""
    directory = (index_payload or {}).get("directory") if isinstance(index_payload, dict) else None
    if isinstance(directory, dict):
        for item in list(directory.get("item") or []):
            name = str(item.get("name") or "").strip()
            if name.lower().endswith(".txt"):
                txt_name = name
                break
    if not txt_name:
        txt_name = _phase6_submission_txt_name(accession_compact)
    if not txt_name:
        return ""
    path = _phase6_find_cached_sec_document(cik_pad, accession_compact, txt_name)
    if not path:
        cik_int = str(int(cik_pad)) if cik_pad != "0000000000" else ""
        if not cik_int:
            return ""
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_compact}/{txt_name}"
        return _phase6_fetch_sec_document_text_transient(
            sec_mod,
            filing_url,
            label=f"Phase 6 EX-21 submission text {cik_pad} {accession_compact}",
        )
    if not path or not os.path.isfile(path):
        return ""
    return _phase6_read_sec_document_text_from_path(path)


def _phase6_ex21_document_names_from_submission_text(submission_text: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"<DOCUMENT>(.*?)</DOCUMENT>", str(submission_text or ""), re.I | re.S):
        block = match.group(1) or ""
        type_match = re.search(r"<TYPE>([^\n\r<]+)", block, re.I)
        filename_match = re.search(r"<FILENAME>([^\n\r<]+)", block, re.I)
        description_match = re.search(r"<DESCRIPTION>([^\n\r<]+)", block, re.I)
        doc_type = str(type_match.group(1) if type_match else "").strip().lower()
        filename = os.path.basename(str(filename_match.group(1) if filename_match else "").strip().split("?", 1)[0])
        description = str(description_match.group(1) if description_match else "").strip().lower()
        if not filename or filename in seen:
            continue
        if (
            re.search(r"\bex-?21(?:[.\-]?\d+)?\b", doc_type, re.I)
            or "significant subsidiar" in description
            or re.search(r"\bexhibit\s*21(?:[.\-]?\d+)?\b", description, re.I)
        ):
            seen.add(filename)
            names.append(filename)
    return names


def _phase6_ex21_candidate_document_names(sec_mod, filing: dict, index_payload: dict | None) -> list[str]:
    cik_pad = str(filing.get("cik") or "").strip().zfill(10)
    accession_compact = str(filing.get("accession_compact") or "").strip()
    if not accession_compact:
        return []

    names: list[str] = []
    seen: set[str] = set()

    def _push(raw_name: str) -> None:
        clean = os.path.basename(str(raw_name or "").strip().split("?", 1)[0])
        if not clean or clean in seen or not _phase6_is_textual_sec_document_name(clean):
            return
        seen.add(clean)
        names.append(clean)

    items = []
    directory = (index_payload or {}).get("directory") if isinstance(index_payload, dict) else None
    if isinstance(directory, dict):
        items = list(directory.get("item") or [])
    current_accession_names = {
        os.path.basename(str(item.get("name") or "").strip().split("?", 1)[0])
        for item in items
        if str(item.get("name") or "").strip()
    }

    for submission_name in _phase6_ex21_document_names_from_submission_text(
        _phase6_fetch_sec_submission_text(sec_mod, filing, index_payload)
    ):
        if not current_accession_names or submission_name in current_accession_names:
            _push(submission_name)

    if names:
        return names

    subsidiary_named = ""
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        if _phase6_is_ex21_document_name(name):
            _push(name)
            continue
        lower = name.lower()
        if not subsidiary_named and "subsidiar" in lower and lower.endswith((".htm", ".html", ".txt", ".xml")):
            subsidiary_named = name
    if subsidiary_named:
        _push(subsidiary_named)

    for cached_name in _phase6_cached_sec_document_names(cik_pad, accession_compact):
        if _phase6_is_ex21_document_name(cached_name):
            _push(cached_name)

    if names:
        return names

    primary_document = str(filing.get("primary_document") or "").strip()
    if not primary_document:
        return names
    try:
        primary_html = ""
        primary_path = _phase6_find_cached_sec_document(cik_pad, accession_compact, primary_document)
        if primary_path and os.path.isfile(primary_path):
            with open(primary_path, "r", encoding="utf-8", errors="replace") as handle:
                primary_html = handle.read()
        else:
            primary_html = _phase6_fetch_sec_document_text_transient(
                sec_mod,
                filing["filing_url"],
                label=f"Phase 6 EX-21 primary filing {cik_pad} {accession_compact}",
            )
        if primary_html:
            for match in re.findall(
                r'href="([^"]*(?:ex(?:hibit)?[-_a-z0-9]*21(?:1|[._-]?1)|xexx211|exx211|21[._-]?1|subsidiar[^"]*)\.(?:htm|html|txt|xml))"',
                primary_html,
                re.I,
            ):
                clean = os.path.basename(str(match or "").strip().split("?", 1)[0])
                if current_accession_names and clean not in current_accession_names:
                    continue
                if not _phase6_is_ex21_document_name(clean):
                    continue
                _push(clean)
    except Exception:
        return names
    return names


def _phase6_pick_ex21_document(sec_mod, filing: dict, index_payload: dict | None) -> str:
    document_names = _phase6_ex21_candidate_document_names(sec_mod, filing, index_payload)
    return document_names[0] if document_names else ""


def _phase6_load_ex21_entries_for_filing(
    sec_mod,
    issuer_ticker: str,
    filing: dict,
    *,
    ignore_parse_cache: bool = False,
) -> list[dict]:
    accession_compact = str(filing.get("accession_compact") or "").strip()
    if not accession_compact:
        return []
    cache_path = _nexus_cache_path("phase6", f"ex21_parsed_{issuer_ticker}_{accession_compact}_v{PHASE6_EX21_PARSE_CACHE_VERSION}.json")
    if not ignore_parse_cache:
        cached = _nexus_read_cached_json(cache_path, NEXUS_CACHE_MAX_AGE_HISTORIC)
        if isinstance(cached, list):
            _nexus_stage_cache_hit(1)
            return cached
    index_payload = _phase6_fetch_sec_filing_index(sec_mod, filing)
    document_names = _phase6_ex21_candidate_document_names(sec_mod, filing, index_payload)
    if not document_names:
        return []
    cik_pad = str(filing.get("cik") or "").strip().zfill(10)
    cik_int = str(int(cik_pad)) if cik_pad and cik_pad != "0000000000" else ""
    for idx, document_name in enumerate(document_names):
        filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_compact}/{document_name}"
        path = _phase6_find_cached_sec_document(cik_pad, accession_compact, document_name)
        if not path:
            # The SEC helper already retries transient 429/5xx/timeout failures internally.
            # Do not add a second outer retry loop here, or deterministic 404 exhibit paths
            # get logged repeatedly and look like duplicate parallel work for the same issuer.
            path, _cached = sec_mod._download_sec_filing_document(
                filing_url=filing_url,
                cache_subdir=PHASE6_SEC_RAW_CACHE_SUBDIR,
                ticker=issuer_ticker,
                cik=cik_pad,
                accession_compact=accession_compact,
                primary_document=document_name,
            )
        if not path or not os.path.isfile(path):
            continue
        if not _phase6_is_textual_sec_document_name(os.path.basename(path)):
            continue
        html = _phase6_read_sec_document_text_from_path(path)
        if not html:
            continue
        entries = _phase6_extract_ex21_entries_from_html(html)
        if entries:
            _nexus_write_cached_json(cache_path, entries)
            return entries
        if idx == 0 and len(document_names) > 1:
            _log(
                f"Phase 6B: EX-21 candidate {document_name} for {issuer_ticker} {accession_compact} parsed empty; trying alternate exhibit names.",
                "yellow",
            )
    return []


def _phase6_dedupe_parent_of_entity_batch(batch: list[dict]) -> list[dict]:
    if not batch:
        return []

    def _norm_text(value) -> str:
        return str(value or "").strip()

    def _min_date(left: str, right: str) -> str:
        if not left:
            return right
        if not right:
            return left
        return left if left <= right else right

    def _max_date(left: str, right: str) -> str:
        if not left:
            return right
        if not right:
            return left
        return left if left >= right else right

    deduped: dict[tuple[str, str, str, str], dict] = {}
    for raw_item in batch:
        item = dict(raw_item or {})
        parent_kind = "Company" if _norm_text(item.get("parent_kind")) == "Company" else "LegalEntity"
        child_kind = "Company" if _norm_text(item.get("child_kind")) == "Company" else "LegalEntity"
        parent_ref = _norm_text(item.get("parent_ref"))
        child_ref = _norm_text(item.get("child_ref"))
        if not parent_ref or not child_ref:
            continue
        key = (parent_kind, parent_ref, child_kind, child_ref)
        normalized_sources: list[str] = []
        seen_sources: set[str] = set()
        for source in list(item.get("evidence_sources") or []):
            source_text = _norm_text(source)
            if source_text and source_text not in seen_sources:
                seen_sources.add(source_text)
                normalized_sources.append(source_text)
        item["parent_kind"] = parent_kind
        item["child_kind"] = child_kind
        item["parent_ref"] = parent_ref
        item["child_ref"] = child_ref
        item["evidence_sources"] = normalized_sources
        item["active_after"] = _norm_text(item.get("active_after"))
        item["last_confirmed"] = _norm_text(item.get("last_confirmed"))
        item["gleif_supported"] = bool(item.get("gleif_supported"))
        item["sec_ex21_supported"] = bool(item.get("sec_ex21_supported"))
        item["gleif_last_run_token"] = _norm_text(item.get("gleif_last_run_token"))
        item["gleif_relation_kind"] = _norm_text(item.get("gleif_relation_kind"))
        item["gleif_parent_lei"] = _norm_text(item.get("gleif_parent_lei"))
        item["gleif_child_lei"] = _norm_text(item.get("gleif_child_lei"))
        item["sec_ex21_issuer_ticker"] = _norm_text(item.get("sec_ex21_issuer_ticker"))
        item["sec_ex21_latest_filing_date"] = _norm_text(item.get("sec_ex21_latest_filing_date"))
        item["sec_ex21_last_accession"] = _norm_text(item.get("sec_ex21_last_accession"))
        item["parent_legal_name"] = _norm_text(item.get("parent_legal_name"))
        item["child_legal_name"] = _norm_text(item.get("child_legal_name"))

        existing = deduped.get(key)
        if existing is None:
            deduped[key] = item
            continue

        existing["active_after"] = _min_date(existing.get("active_after", ""), item["active_after"])
        existing["last_confirmed"] = _max_date(existing.get("last_confirmed", ""), item["last_confirmed"])

        merged_sources = list(existing.get("evidence_sources") or [])
        merged_seen = set(merged_sources)
        for source_text in item["evidence_sources"]:
            if source_text not in merged_seen:
                merged_seen.add(source_text)
                merged_sources.append(source_text)
        existing["evidence_sources"] = merged_sources

        existing["gleif_supported"] = bool(existing.get("gleif_supported")) or item["gleif_supported"]
        existing["sec_ex21_supported"] = bool(existing.get("sec_ex21_supported")) or item["sec_ex21_supported"]

        for field in (
            "gleif_last_run_token",
            "gleif_relation_kind",
            "gleif_parent_lei",
            "gleif_child_lei",
            "sec_ex21_issuer_ticker",
            "parent_legal_name",
            "child_legal_name",
        ):
            if not _norm_text(existing.get(field)) and item[field]:
                existing[field] = item[field]

        existing_filing_date = _norm_text(existing.get("sec_ex21_latest_filing_date"))
        item_filing_date = item["sec_ex21_latest_filing_date"]
        if item_filing_date and (not existing_filing_date or item_filing_date > existing_filing_date):
            existing["sec_ex21_latest_filing_date"] = item_filing_date
            if item["sec_ex21_last_accession"]:
                existing["sec_ex21_last_accession"] = item["sec_ex21_last_accession"]
        elif not _norm_text(existing.get("sec_ex21_last_accession")) and item["sec_ex21_last_accession"]:
            existing["sec_ex21_last_accession"] = item["sec_ex21_last_accession"]

    return list(deduped.values())


def _phase6_write_parent_of_entity_edges(session, batch: list[dict]) -> int:
    if not batch:
        return 0
    batch = _phase6_dedupe_parent_of_entity_batch(batch)
    if not batch:
        return 0
    combos = {
        ("Company", "Company"): [],
        ("Company", "LegalEntity"): [],
        ("LegalEntity", "Company"): [],
        ("LegalEntity", "LegalEntity"): [],
    }
    for item in batch:
        parent_kind = "Company" if str(item.get("parent_kind") or "").strip() == "Company" else "LegalEntity"
        child_kind = "Company" if str(item.get("child_kind") or "").strip() == "Company" else "LegalEntity"
        combos[(parent_kind, child_kind)].append(dict(item))

    def _run_write(match_sql: str, items: list[dict]) -> None:
        if not items:
            return
        session.run(
            f"""
            UNWIND $batch AS p
            {match_sql}
            MERGE (parent)-[r:PARENT_OF_ENTITY {{source_scope: 'CORPORATE_HIERARCHY'}}]->(child)
            ON CREATE SET r.source = 'HybridHierarchy',
                          r.evidence_sources = coalesce(p.evidence_sources, []),
                          r.evidence_count = size(coalesce(p.evidence_sources, [])),
                          r.active_after = p.active_after,
                          r.last_confirmed = p.last_confirmed,
                          r.valid_until = NULL,
                          r.edge_state = 'open',
                          r.retired_reason = NULL
            ON MATCH  SET r.evidence_sources = REDUCE(acc = coalesce(r.evidence_sources, []), item IN coalesce(p.evidence_sources, []) |
                                CASE WHEN item IS NULL OR item = '' OR item IN acc THEN acc ELSE acc + item END),
                          r.evidence_count = size(REDUCE(acc = coalesce(r.evidence_sources, []), item IN coalesce(p.evidence_sources, []) |
                                CASE WHEN item IS NULL OR item = '' OR item IN acc THEN acc ELSE acc + item END)),
                          r.active_after = CASE
                              WHEN p.active_after IS NULL OR p.active_after = '' THEN r.active_after
                              WHEN r.active_after IS NULL OR r.active_after = '' OR p.active_after < r.active_after THEN p.active_after
                              ELSE r.active_after
                          END,
                          r.last_confirmed = CASE
                              WHEN p.last_confirmed IS NULL OR p.last_confirmed = '' THEN r.last_confirmed
                              WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR p.last_confirmed > r.last_confirmed THEN p.last_confirmed
                              ELSE r.last_confirmed
                          END,
                          r.valid_until = NULL,
                          r.edge_state = 'open',
                          r.retired_reason = NULL
            SET r.gleif_supported = CASE WHEN coalesce(p.gleif_supported, false) THEN true ELSE coalesce(r.gleif_supported, false) END,
                r.gleif_last_run_token = CASE WHEN coalesce(p.gleif_supported, false) THEN p.gleif_last_run_token ELSE r.gleif_last_run_token END,
                r.gleif_relation_kind = CASE WHEN p.gleif_relation_kind IS NULL OR p.gleif_relation_kind = '' THEN r.gleif_relation_kind ELSE p.gleif_relation_kind END,
                r.gleif_parent_lei = CASE WHEN p.gleif_parent_lei IS NULL OR p.gleif_parent_lei = '' THEN r.gleif_parent_lei ELSE p.gleif_parent_lei END,
                r.gleif_child_lei = CASE WHEN p.gleif_child_lei IS NULL OR p.gleif_child_lei = '' THEN r.gleif_child_lei ELSE p.gleif_child_lei END,
                r.sec_ex21_supported = CASE WHEN coalesce(p.sec_ex21_supported, false) THEN true ELSE coalesce(r.sec_ex21_supported, false) END,
                r.sec_ex21_issuer_ticker = CASE WHEN p.sec_ex21_issuer_ticker IS NULL OR p.sec_ex21_issuer_ticker = '' THEN r.sec_ex21_issuer_ticker ELSE p.sec_ex21_issuer_ticker END,
                r.sec_ex21_latest_filing_date = CASE
                    WHEN p.sec_ex21_latest_filing_date IS NULL OR p.sec_ex21_latest_filing_date = '' THEN r.sec_ex21_latest_filing_date
                    WHEN r.sec_ex21_latest_filing_date IS NULL OR r.sec_ex21_latest_filing_date = '' OR p.sec_ex21_latest_filing_date > r.sec_ex21_latest_filing_date THEN p.sec_ex21_latest_filing_date
                    ELSE r.sec_ex21_latest_filing_date
                END,
                r.sec_ex21_last_accession = CASE WHEN p.sec_ex21_last_accession IS NULL OR p.sec_ex21_last_accession = '' THEN r.sec_ex21_last_accession ELSE p.sec_ex21_last_accession END,
                r.sec_ex21_valid_until = CASE WHEN coalesce(p.sec_ex21_supported, false) THEN NULL ELSE r.sec_ex21_valid_until END,
                r.sec_ex21_retired_reason = CASE WHEN coalesce(p.sec_ex21_supported, false) THEN NULL ELSE r.sec_ex21_retired_reason END,
                r.parent_legal_name = CASE WHEN p.parent_legal_name IS NULL OR p.parent_legal_name = '' THEN r.parent_legal_name ELSE p.parent_legal_name END,
                r.child_legal_name = CASE WHEN p.child_legal_name IS NULL OR p.child_legal_name = '' THEN r.child_legal_name ELSE p.child_legal_name END
            """,
            batch=items,
        ).consume()

    _run_write(
        "MATCH (parent:Company {ticker: p.parent_ref}) MATCH (child:Company {ticker: p.child_ref})",
        combos[("Company", "Company")],
    )
    _run_write(
        "MATCH (parent:Company {ticker: p.parent_ref}) MATCH (child:LegalEntity {entity_key: p.child_ref})",
        combos[("Company", "LegalEntity")],
    )
    _run_write(
        "MATCH (parent:LegalEntity {entity_key: p.parent_ref}) MATCH (child:Company {ticker: p.child_ref})",
        combos[("LegalEntity", "Company")],
    )
    _run_write(
        "MATCH (parent:LegalEntity {entity_key: p.parent_ref}) MATCH (child:LegalEntity {entity_key: p.child_ref})",
        combos[("LegalEntity", "LegalEntity")],
    )
    return len(batch)


def _phase6_count_live_hierarchy_edges(
    session,
    *,
    rel_type: str,
    source_scope: str,
    evidence_source: str | None = None,
) -> int:
    return _count_live_scoped_relationships(
        session,
        rel_type=rel_type,
        source_scope=source_scope,
        directed=True,
        evidence_source=evidence_source,
    )


def _count_live_scoped_relationships(
    session,
    *,
    rel_type: str,
    source_scope: str,
    directed: bool,
    evidence_source: str | None = None,
) -> int:
    where = ["coalesce(r.edge_state, 'open') = 'open'", "coalesce(r.source_scope, '') = $source_scope"]
    params = {"source_scope": source_scope}
    if evidence_source:
        where.append("$evidence_source IN coalesce(r.evidence_sources, [])")
        params["evidence_source"] = evidence_source
    rel_pattern = f"()-[r:{rel_type}]->()" if directed else f"()-[r:{rel_type}]-()"
    rec = session.run(
        f"""
        MATCH {rel_pattern}
        WHERE {' AND '.join(where)}
        RETURN count(r) AS cnt
        """,
        **params,
    ).single()
    rec = rec or {}
    return int(rec.get("cnt") or rec.get("count") or 0)


def _count_live_scoped_relationship_sources(
    session,
    *,
    rel_type: str,
    source_scope: str,
) -> int:
    rec = session.run(
        f"""
        MATCH (src)-[r:{rel_type}]->()
        WHERE coalesce(r.edge_state, 'open') = 'open'
          AND coalesce(r.source_scope, '') = $source_scope
        RETURN count(DISTINCT src) AS cnt
        """,
        source_scope=source_scope,
    ).single()
    rec = rec or {}
    return int(rec.get("cnt") or rec.get("count") or 0)


def _phase6_close_stale_gleif_support(session, run_token: str) -> int:
    rec = session.run(
        """
        MATCH ()-[r:PARENT_OF_ENTITY {source_scope: 'CORPORATE_HIERARCHY'}]->()
        WHERE coalesce(r.gleif_supported, false) = true
          AND coalesce(r.gleif_last_run_token, '') <> $run_token
        SET r.gleif_supported = false
        WITH r
        WHERE coalesce(r.sec_ex21_supported, false) = false
        SET r.edge_state = 'closed',
            r.valid_until = CASE
                WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' THEN toString(date())
                ELSE r.last_confirmed
            END,
            r.retired_reason = 'superseded_snapshot'
        RETURN count(r) AS closed
        """,
        run_token=run_token,
    ).single()
    return int((rec or {}).get("closed") or 0)


def _phase6_close_omitted_ex21_edges_for_issuer(session, issuer_ticker: str, current_refs: list[str], filing_date: str) -> int:
    rec = session.run(
        """
        MATCH (:Company {ticker: $issuer_ticker})-[r:PARENT_OF_ENTITY {source_scope: 'CORPORATE_HIERARCHY'}]->(child)
        WHERE coalesce(r.sec_ex21_supported, false) = true
          AND coalesce(r.sec_ex21_issuer_ticker, '') = $issuer_ticker
          AND NOT coalesce(child.entity_key, child.ticker, '') IN $current_refs
        SET r.sec_ex21_supported = false,
            r.sec_ex21_valid_until = $filing_date,
            r.sec_ex21_retired_reason = 'superseded_snapshot'
        WITH r
        WHERE coalesce(r.gleif_supported, false) = false
        SET r.edge_state = 'closed',
            r.valid_until = CASE
                WHEN $filing_date = '' THEN coalesce(r.last_confirmed, toString(date()))
                ELSE $filing_date
            END,
            r.retired_reason = 'superseded_snapshot'
        RETURN count(r) AS closed
        """,
        issuer_ticker=issuer_ticker,
        current_refs=list(current_refs or []),
        filing_date=str(filing_date or ""),
    ).single()
    return int((rec or {}).get("closed") or 0)


def _phase6_refresh_listed_ancestor_tickers(session) -> None:
    session.run(
        """
        MATCH (le:LegalEntity)
        OPTIONAL MATCH path=(ancestor:Company)-[:PARENT_OF_ENTITY*1..6]->(le)
        WHERE all(rel IN relationships(path) WHERE coalesce(rel.edge_state, 'open') = 'open')
        WITH le, collect(DISTINCT ancestor.ticker) AS ancestors
        SET le.listed_ancestor_tickers = ancestors
        """
    ).consume()


def _phase6_project_company_hierarchy_edges(session, run_token: str) -> tuple[int, int]:
    written_rec = session.run(
        """
        MATCH (parent:Company)-[h:PARENT_OF_ENTITY {source_scope: 'CORPORATE_HIERARCHY'}]->(child:Company)
        WHERE coalesce(h.edge_state, 'open') = 'open'
        MERGE (parent)-[r:PARENT_OF {source_scope: 'CORPORATE_HIERARCHY_PROJECTION'}]->(child)
        ON CREATE SET r.source = 'HybridHierarchy',
                      r.confidence = 0.95,
                      r.active_after = h.active_after,
                      r.last_confirmed = h.last_confirmed,
                      r.evidence_sources = coalesce(h.evidence_sources, []),
                      r.evidence_count = coalesce(h.evidence_count, size(coalesce(h.evidence_sources, []))),
                      r.edge_state = 'open',
                      r.valid_until = NULL,
                      r.retired_reason = NULL
        ON MATCH  SET r.active_after = CASE
                          WHEN h.active_after IS NULL OR h.active_after = '' THEN r.active_after
                          WHEN r.active_after IS NULL OR r.active_after = '' OR h.active_after < r.active_after THEN h.active_after
                          ELSE r.active_after
                      END,
                      r.last_confirmed = CASE
                          WHEN h.last_confirmed IS NULL OR h.last_confirmed = '' THEN r.last_confirmed
                          WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR h.last_confirmed > r.last_confirmed THEN h.last_confirmed
                          ELSE r.last_confirmed
                      END,
                      r.evidence_sources = coalesce(h.evidence_sources, r.evidence_sources),
                      r.evidence_count = coalesce(h.evidence_count, r.evidence_count),
                      r.edge_state = 'open',
                      r.valid_until = NULL,
                      r.retired_reason = NULL,
                      r.current_run_token = $run_token
        SET r.current_run_token = $run_token
        RETURN count(r) AS written
        """,
        run_token=run_token,
    ).single()
    closed_rec = session.run(
        """
        MATCH ()-[r:PARENT_OF {source_scope: 'CORPORATE_HIERARCHY_PROJECTION'}]->()
        WHERE coalesce(r.current_run_token, '') <> $run_token
        SET r.edge_state = 'closed',
            r.valid_until = CASE
                WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' THEN toString(date())
                ELSE r.last_confirmed
            END,
            r.retired_reason = 'superseded_snapshot',
            r.current_run_token = NULL
        RETURN count(r) AS closed
        """,
        run_token=run_token,
    ).single()
    return int((written_rec or {}).get("written") or 0), int((closed_rec or {}).get("closed") or 0)


def _phase6_load_listed_company_records(session) -> tuple[dict[str, dict], list[dict], list[dict]]:
    valid_c = _cypher_valid_company_listing_predicate("c")
    rows = session.run(
        f"""
        MATCH (c:Company)
        WHERE c.name IS NOT NULL AND {valid_c}
        RETURN c.ticker AS ticker, c.name AS name, c.issuer_key AS issuer_key,
               c.cik AS cik, c.lei AS lei, c.canonical_name AS canonical_name,
               c.listing_name AS listing_name, properties(c)['lei_legal_name'] AS lei_legal_name
        LIMIT $lim
        """,
        lim=GRAPH_NEXUS_MAX_COMPANIES,
    )
    company_records = _dedupe_company_records([
        {
            "ticker": rec.get("ticker"),
            "name": rec.get("name"),
            "listing_name": rec.get("listing_name"),
            "issuer_key": rec.get("issuer_key"),
            "cik": rec.get("cik"),
            "lei": rec.get("lei"),
            "canonical_name": rec.get("canonical_name"),
            "lei_legal_name": rec.get("lei_legal_name"),
            "is_security_instrument": False,
        }
        for rec in rows
        if rec.get("ticker") and rec.get("name")
    ])
    company_records_by_ticker = {rec["ticker"]: rec for rec in sorted(company_records, key=lambda x: x["ticker"])}
    companies = [company_records_by_ticker[ticker] for ticker in sorted(company_records_by_ticker)]
    return company_records_by_ticker, companies, list(company_records_by_ticker.values())


def _phase6_ex21_fetch_window(phase_key: str, phase_manifest: dict, history_start_date: str) -> tuple[str, str, bool, str]:
    phase_start_date, phase_end_date = _nexus_phase_fetch_window(phase_key)
    today_iso = _nexus_today_iso()
    if not phase_end_date:
        phase_end_date = today_iso
    ignore_parse_cache = bool(history_start_date and not phase_manifest.get("bootstrap_complete"))
    if phase_start_date:
        ex21_start_date = phase_start_date
    elif history_start_date:
        ex21_start_date = (
            _normalize_historical_iso_date(phase_manifest.get("coverage_start"))
            or history_start_date
        )
    else:
        # Current-snapshot reruns should still scan a broad recent window and then keep only the latest
        # annual filing per issuer, rather than collapsing to a previous manifest coverage_start.
        ex21_start_date = (datetime.now(timezone.utc).date() - timedelta(days=PHASE6_EX21_LOOKBACK_DAYS)).isoformat()
    return ex21_start_date, phase_end_date, ignore_parse_cache, phase_start_date


def _phase6_prepare_ex21_issuer_payload(
    sec_mod,
    issuer_record: dict,
    *,
    ex21_start_date: str,
    phase_end_date: str,
    phase_start_date: str,
    ignore_parse_cache: bool,
) -> dict:
    issuer_ticker = str(issuer_record.get("ticker") or "").strip().upper()
    cik_pad = str(issuer_record.get("cik") or "").strip().zfill(10)
    payload = {
        "issuer_record": issuer_record,
        "issuer_ticker": issuer_ticker,
        "annual_filings_found": False,
        "prepared_filings": [],
        "error": "",
    }
    if not issuer_ticker or not cik_pad or cik_pad == "0000000000":
        return payload
    annual_forms = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")
    try:
        filings = sec_mod._collect_submission_filing_records(
            cik_pad,
            form_types=annual_forms,
            start_date=ex21_start_date,
            end_date=phase_end_date,
            allow_live_lookup=True,
        )
    except Exception as exc:
        payload["error"] = str(exc)
        return payload
    if not filings:
        return payload
    payload["annual_filings_found"] = True
    filings = sorted(filings, key=lambda item: (item.get("filing_date") or "", item.get("accession_number") or ""))
    if not phase_start_date:
        filings = filings[-1:]
    elif len(filings) > PHASE6_EX21_MAX_FILINGS_PER_COMPANY:
        filings = filings[-PHASE6_EX21_MAX_FILINGS_PER_COMPANY:]
    prepared_filings: list[dict] = []
    for filing in filings:
        try:
            entries = _phase6_load_ex21_entries_for_filing(
                sec_mod,
                issuer_ticker,
                filing,
                ignore_parse_cache=ignore_parse_cache,
            )
        except Exception:
            entries = []
        if not entries:
            continue
        prepared_filings.append({
            "filing": filing,
            "entries": entries,
        })
    payload["prepared_filings"] = prepared_filings
    return payload


def phase6_gleif_hierarchy(session):
    """
    Phase 6: GLEIF corporate hierarchy refresh.
    Maintains mixed public/private hierarchy edges on PARENT_OF_ENTITY and projects
    company-only PARENT_OF edges for the strategy layer from GLEIF-supported relationships.
    """
    _nexus_stage_reset()
    with _GLEIF_RATE_LOCK:
        _GLEIF_RATE_NEXT_REQUEST_AT[0] = 0.0
    _progress(PHASE_PCT[7], "Phase 6: GLEIF corporate hierarchy...", "cyan")
    company_records_by_ticker, companies, company_records_list = _phase6_load_listed_company_records(session)
    if not companies:
        _log_stage_unexpected("Phase 6: GLEIF hierarchy", "company list", "No listed companies available for hierarchy resolution.")
        _progress(PHASE_PCT[8], "Phase 6 skipped.", "green")
        return

    migrated_entities = _phase6_promote_existing_lei_entities(session)
    if migrated_entities:
        _log(f"Phase 6: promoted {migrated_entities} existing LEIEntity node(s) into LegalEntity.", "green")

    history_start_date = _nexus_historical_start_date()
    phase_manifest = _nexus_phase_manifest("phase6")
    phase_start_date, phase_end_date = _nexus_phase_fetch_window("phase6")
    today_iso = _nexus_today_iso()
    if not phase_end_date:
        phase_end_date = today_iso

    ticker_to_lei: dict[str, str] = {}
    lei_to_ticker: dict[str, str] = {}
    gleif_request_interval = _gleif_request_interval_seconds()
    total = len(companies)
    leis_found = 0
    gleif_no_parent_data_count = 0
    no_lei_count = 0
    entity_edges_written = 0
    legal_entities_written = 0
    batch_ok = 0
    batch_fail = 0
    total_calls_ok = 0
    total_calls_fail = 0
    retry_queue: list[dict] = []
    consecutive_429s = 0
    failures_since_cooldown = 0
    gleif_batch_size = 100
    phase_run_token = f"phase6_hierarchy:{uuid4().hex}"
    projection_run_token = f"phase6_projection:{uuid4().hex}"
    discard_log_path = _phase6_reset_discard_log(phase_run_token)
    logged_sparse_gleif_parent_note = False
    queued_legal_entity_keys: set[str] = set()
    gleif_companies_with_parent_data: set[str] = set()
    _lei_batch: list[dict] = []
    _legal_entity_batch: list[dict] = []
    _entity_edge_batch: list[dict] = []

    def _flush_lei_batch():
        nonlocal _lei_batch
        if not _lei_batch:
            return
        session.run("""
            UNWIND $batch AS p
            MATCH (c:Company {ticker: p.ticker})
            SET c.lei = p.lei,
                c.lei_legal_name = CASE
                    WHEN p.legal_name IS NULL OR p.legal_name = '' THEN c.lei_legal_name
                    ELSE p.legal_name
                END,
                c.lei_match_score = CASE
                    WHEN p.match_score IS NULL OR p.match_score <= 0 THEN c.lei_match_score
                    ELSE p.match_score
                END
        """, batch=_lei_batch).consume()
        _lei_batch = []

    def _queue_legal_entity(payload: dict) -> None:
        entity_key = str(payload.get("entity_key") or "").strip()
        if not entity_key or entity_key in queued_legal_entity_keys:
            return
        queued_legal_entity_keys.add(entity_key)
        _legal_entity_batch.append(dict(payload))

    def _append_gleif_entity_edge(
        *,
        parent_kind: str,
        parent_ref: str,
        child_kind: str,
        child_ref: str,
        active_after: str,
        rel_type: str,
        parent_lei: str,
        child_lei: str,
        parent_legal_name: str,
        child_legal_name: str,
    ) -> None:
        _entity_edge_batch.append({
            "parent_kind": parent_kind,
            "parent_ref": parent_ref,
            "child_kind": child_kind,
            "child_ref": child_ref,
            "active_after": active_after,
            "last_confirmed": active_after,
            "evidence_sources": [f"GLEIF_{rel_type}"],
            "gleif_supported": True,
            "gleif_last_run_token": phase_run_token,
            "gleif_relation_kind": rel_type,
            "gleif_parent_lei": parent_lei,
            "gleif_child_lei": child_lei,
            "parent_legal_name": parent_legal_name,
            "child_legal_name": child_legal_name,
        })

    def _process_gleif_subsidiary_candidates(
        *,
        ticker: str,
        company_record: dict,
        primary_lei: str,
        company_name_for_search: str,
        default_active_after: str,
        parent_display_name: str,
    ) -> bool:
        nonlocal batch_ok, batch_fail, total_calls_ok, total_calls_fail, consecutive_429s, failures_since_cooldown
        publish_date, candidates, search_err = _gleif_search_candidate_records(company_name_for_search)
        if search_err:
            if "429" in search_err:
                consecutive_429s += 1
            else:
                _log(f"Phase 6: GLEIF subsidiary search error [{ticker}] {company_name_for_search[:40]!r}: {search_err}", "yellow")
            batch_fail += 1
            total_calls_fail += 1
            failures_since_cooldown += 1
            return False
        batch_ok += 1
        total_calls_ok += 1
        parent_name = parent_display_name or _company_record_best_name(company_record)
        found = False
        accepted_candidates, rejected_candidates = _gleif_split_alternate_child_candidates(company_name_for_search, primary_lei, candidates)
        for rejected in rejected_candidates:
            reason = str(rejected.get("reason") or "").strip() or "filtered_candidate"
            if reason != "candidate_not_issued":
                continue
            _log_phase6_discard(
                "gleif_alternate_child",
                reason,
                {
                    "ticker": ticker,
                    "company_name": _company_record_best_name(company_record),
                    "primary_lei": primary_lei,
                    "candidate_lei": str(rejected.get("candidate_lei") or "").strip(),
                    "candidate_name": str(rejected.get("candidate_name") or "").strip(),
                    "match_score": rejected.get("match_score"),
                    "registration_status": str(rejected.get("registration_status") or "").strip(),
                },
            )
        overflow_candidates = accepted_candidates[GLEIF_ALT_CHILD_CANDIDATE_LIMIT:]
        for candidate in overflow_candidates:
            _log_phase6_discard(
                "gleif_alternate_child",
                "candidate_limit_exceeded",
                {
                    "ticker": ticker,
                    "company_name": _company_record_best_name(company_record),
                    "primary_lei": primary_lei,
                    "candidate_lei": str(candidate.get("lei") or "").strip(),
                    "candidate_name": str(candidate.get("legal_name") or "").strip(),
                    "match_score": round(float(candidate.get("match_score") or 0.0), 4),
                    "registration_status": str(candidate.get("registration_status") or "").strip().upper(),
                },
            )
        for candidate in accepted_candidates[:GLEIF_ALT_CHILD_CANDIDATE_LIMIT]:
            child_lei = str(candidate.get("lei") or "").strip()
            child_legal_name = str(candidate.get("legal_name") or "").strip()
            if not child_lei or not child_legal_name:
                continue
            rels, rels_err = _gleif_fetch_relationships(child_lei)
            if rels_err:
                _log_phase6_discard(
                    "gleif_alternate_child",
                    "relationships_fetch_error",
                    {
                        "ticker": ticker,
                        "company_name": _company_record_best_name(company_record),
                        "primary_lei": primary_lei,
                        "candidate_lei": child_lei,
                        "candidate_name": child_legal_name,
                        "error": str(rels_err),
                    },
                )
                if "429" in rels_err:
                    consecutive_429s += 1
                else:
                    _log(f"Phase 6: GLEIF subsidiary relationships error [{ticker}] LEI={child_lei}: {rels_err}", "yellow")
                batch_fail += 1
                total_calls_fail += 1
                failures_since_cooldown += 1
                continue
            consecutive_429s = 0
            batch_ok += 1
            total_calls_ok += 1
            direct_parent_lei = str(rels.get("direct_parent_lei") or "").strip()
            ultimate_parent_lei = str(rels.get("ultimate_parent_lei") or "").strip()
            if not direct_parent_lei and not ultimate_parent_lei:
                _log_phase6_discard(
                    "gleif_alternate_child",
                    "no_gleif_parent_data",
                    {
                        "ticker": ticker,
                        "company_name": _company_record_best_name(company_record),
                        "primary_lei": primary_lei,
                        "candidate_lei": child_lei,
                        "candidate_name": child_legal_name,
                    },
                )
                continue
            if primary_lei not in {direct_parent_lei, ultimate_parent_lei}:
                _log_phase6_discard(
                    "gleif_alternate_child",
                    "parent_chain_not_rooted_at_company_lei",
                    {
                        "ticker": ticker,
                        "company_name": _company_record_best_name(company_record),
                        "primary_lei": primary_lei,
                        "candidate_lei": child_lei,
                        "candidate_name": child_legal_name,
                        "direct_parent_lei": direct_parent_lei,
                        "ultimate_parent_lei": ultimate_parent_lei,
                    },
                )
                continue
            active_after = _normalize_edge_date(rels.get("publish_date") or publish_date or default_active_after)
            child_entity_key = f"lei:{child_lei}"
            _queue_legal_entity(_build_legal_entity_payload(
                entity_key=child_entity_key,
                legal_name=child_legal_name,
                display_name=child_legal_name,
                entity_kind="legal_entity",
                lei=child_lei,
                source_systems=["GLEIF"],
                listed_ancestor_tickers=[ticker],
            ))
            for parent_lei, rel_parent_name, rel_type in (
                (direct_parent_lei, str(rels.get("direct_parent_name") or ""), "DIRECT_PARENT"),
                (ultimate_parent_lei, str(rels.get("ultimate_parent_name") or ""), "ULTIMATE_PARENT"),
            ):
                if not parent_lei or parent_lei == child_lei:
                    continue
                if rel_type == "ULTIMATE_PARENT" and parent_lei == direct_parent_lei:
                    continue
                if parent_lei == primary_lei:
                    _append_gleif_entity_edge(
                        parent_kind="Company",
                        parent_ref=ticker,
                        child_kind="LegalEntity",
                        child_ref=child_entity_key,
                        active_after=active_after,
                        rel_type=rel_type,
                        parent_lei=parent_lei,
                        child_lei=child_lei,
                        parent_legal_name=parent_name,
                        child_legal_name=child_legal_name,
                    )
                    found = True
                    continue
                parent_entity_key = f"lei:{parent_lei}"
                _queue_legal_entity(_build_legal_entity_payload(
                    entity_key=parent_entity_key,
                    legal_name=rel_parent_name or parent_lei,
                    display_name=rel_parent_name or parent_lei,
                    entity_kind="legal_entity",
                    lei=parent_lei,
                    source_systems=["GLEIF"],
                    listed_ancestor_tickers=[ticker],
                ))
                _append_gleif_entity_edge(
                    parent_kind="LegalEntity",
                    parent_ref=parent_entity_key,
                    child_kind="LegalEntity",
                    child_ref=child_entity_key,
                    active_after=active_after,
                    rel_type=rel_type,
                    parent_lei=parent_lei,
                    child_lei=child_lei,
                    parent_legal_name=rel_parent_name or parent_lei,
                    child_legal_name=child_legal_name,
                )
                found = True
        return found

    def _flush_legal_entity_batch():
        nonlocal _legal_entity_batch, legal_entities_written
        if not _legal_entity_batch:
            return
        legal_entities_written += _phase6_write_legal_entities(session, _legal_entity_batch)
        _legal_entity_batch = []

    def _flush_entity_edge_batch():
        nonlocal _entity_edge_batch, entity_edges_written
        if not _entity_edge_batch:
            return
        _flush_legal_entity_batch()
        entity_edges_written += _phase6_write_parent_of_entity_edges(session, _entity_edge_batch)
        _entity_edge_batch = []

    est_mins = int(total * 2 * gleif_request_interval / 60) if total else 0
    cooldown_min = 0 if GLEIF_SKIP_HEALTHY_COOLDOWN else (
        (total // GLEIF_COOLDOWN_EVERY) * (GLEIF_COOLDOWN_SECONDS // 60) if GLEIF_COOLDOWN_SECONDS > 0 and total else 0
    )
    _log(
        f"Phase 6: GLEIF executing for {total} companies "
        f"(request limit ~{GLEIF_MAX_REQUESTS_PER_MIN}/min, interval {gleif_request_interval:.2f}s; "
        f"cooldown {GLEIF_COOLDOWN_SECONDS}s every {GLEIF_COOLDOWN_EVERY}; est. {est_mins + cooldown_min} min)...",
        "cyan",
    )
    first_company_name = companies[0].get("canonical_name") or companies[0].get("name") or companies[0]["ticker"]
    _log(
        f"Phase 6: First company: {companies[0]['ticker']} — {first_company_name[:50]}"
        f"{'...' if len(first_company_name) > 50 else ''}",
        "white",
    )

    _log(f"Phase 6: discarded edge audit log -> {discard_log_path}", "white")

    conn = _nexus_rethink_conn
    if conn:
        _phase6_report_progress(conn, 0, total, f"GLEIF lookup 0/{total} — starting")

    def _log_phase6_discard(category: str, reason: str, payload: dict | None = None) -> None:
        _phase6_log_discarded_edge(phase_run_token, category, reason, payload)

    def _process_gleif_company(company: dict) -> bool:
        nonlocal leis_found, gleif_no_parent_data_count, no_lei_count, batch_ok, batch_fail, total_calls_ok, total_calls_fail, consecutive_429s, failures_since_cooldown
        ticker = company["ticker"]
        name = company.get("canonical_name") or company.get("name") or ticker
        clean_name = _gleif_clean_company_name(name)
        lei, search_date, search_legal_name, search_match_score, search_err = _gleif_search_lei(clean_name)
        if search_err:
            is_429 = "429" in search_err
            is_transient = is_429 or "timeout" in search_err.lower() or "connection" in search_err.lower() or any(code in search_err for code in ("500", "502", "503", "504"))
            if is_429:
                consecutive_429s += 1
            if not is_transient:
                _log(f"Phase 6: GLEIF search error [{ticker}] {name[:40]!r}: {search_err}", "yellow")
            batch_fail += 1
            total_calls_fail += 1
            failures_since_cooldown += 1
            return not is_transient
        consecutive_429s = 0
        total_calls_ok += 1
        batch_ok += 1
        if not lei:
            no_lei_count += 1
            return True
        leis_found += 1
        ticker_to_lei[ticker] = lei
        lei_to_ticker[lei] = ticker
        _lei_batch.append({
            "ticker": ticker,
            "lei": lei,
            "legal_name": search_legal_name,
            "match_score": search_match_score,
        })
        rels, rels_err = _gleif_fetch_relationships(lei)
        if rels_err:
            if "429" in rels_err:
                consecutive_429s += 1
            else:
                _log(f"Phase 6: GLEIF relationships error [{ticker}] LEI={lei}: {rels_err}", "yellow")
            batch_fail += 1
            total_calls_fail += 1
            failures_since_cooldown += 1
            rels = rels or {}
        else:
            consecutive_429s = 0
            batch_ok += 1
            total_calls_ok += 1
        active_after = _normalize_edge_date(rels.get("publish_date") or search_date)
        has_parent_data = False
        for parent_lei, parent_name, rel_type in (
            (rels.get("direct_parent_lei"), str(rels.get("direct_parent_name") or ""), "DIRECT_PARENT"),
            (rels.get("ultimate_parent_lei"), str(rels.get("ultimate_parent_name") or ""), "ULTIMATE_PARENT"),
        ):
            if not parent_lei or parent_lei == lei:
                continue
            if rel_type == "ULTIMATE_PARENT" and parent_lei == rels.get("direct_parent_lei"):
                continue
            parent_ticker = lei_to_ticker.get(parent_lei)
            parent_record = company_records_by_ticker.get(parent_ticker)
            child_record = company_records_by_ticker.get(ticker)
            if parent_ticker and parent_ticker != ticker and _gleif_parent_ticker_matches_name(parent_record, parent_name) and _is_company_pair_safe(parent_record, child_record):
                _append_gleif_entity_edge(
                    parent_kind="Company",
                    parent_ref=parent_ticker,
                    child_kind="Company",
                    child_ref=ticker,
                    active_after=active_after,
                    rel_type=rel_type,
                    parent_lei=parent_lei,
                    child_lei=lei,
                    parent_legal_name=parent_name,
                    child_legal_name=search_legal_name or _company_record_best_name(child_record),
                )
                has_parent_data = True
            else:
                entity_key = f"lei:{parent_lei}"
                _queue_legal_entity(_build_legal_entity_payload(
                    entity_key=entity_key,
                    legal_name=parent_name or parent_lei,
                    display_name=parent_name or parent_lei,
                    entity_kind="legal_entity",
                    lei=parent_lei,
                    source_systems=["GLEIF"],
                ))
                _append_gleif_entity_edge(
                    parent_kind="LegalEntity",
                    parent_ref=entity_key,
                    child_kind="Company",
                    child_ref=ticker,
                    active_after=active_after,
                    rel_type=rel_type,
                    parent_lei=parent_lei,
                    child_lei=lei,
                    parent_legal_name=parent_name,
                    child_legal_name=search_legal_name or _company_record_best_name(child_record),
                )
                has_parent_data = True
        primary_lei = str(lei or "").strip()
        if not has_parent_data and primary_lei:
            has_parent_data = _process_gleif_subsidiary_candidates(
                ticker=ticker,
                company_record=company,
                primary_lei=primary_lei,
                company_name_for_search=clean_name,
                default_active_after=active_after,
                parent_display_name=search_legal_name,
            ) or has_parent_data
        if has_parent_data:
            gleif_companies_with_parent_data.add(ticker)
        elif not rels_err:
            gleif_no_parent_data_count += 1
        return True

    # ── Concurrent cache-warm (safe speedup) ──────────────────────────────────────────────
    # The per-company GLEIF I/O below (LEI search, relationships, subsidiary-candidate search)
    # is disk-cache-backed and read-cache-first. Warm those caches in parallel through the
    # SAME thread-safe rate gate, then the sequential resolve loop runs from warm cache
    # (near-instant, no network) with ZERO change to its stateful, graph-writing logic.
    # Workers do ONLY cached network I/O and mutate nothing shared — no Neo4j writes occur
    # off the main thread, so ordering/dedup/parent-resolution are byte-for-byte unchanged.
    if GLEIF_PREFETCH_WORKERS > 1 and len(companies) > 1:
        from concurrent.futures import ThreadPoolExecutor
        _pf_done = [0]
        _pf_lock = threading.Lock()
        _log(
            f"Phase 6: warming GLEIF caches with {GLEIF_PREFETCH_WORKERS} workers "
            f"({len(companies)} companies, ~{GLEIF_MAX_REQUESTS_PER_MIN}/min rate cap) "
            "before sequential resolve...",
            "cyan",
        )

        def _gleif_prefetch_company(company: dict) -> None:
            try:
                nm = company.get("canonical_name") or company.get("name") or company.get("ticker") or ""
                cn = _gleif_clean_company_name(nm)
                lei, _sd, _ln, _ms, serr = _gleif_search_lei(cn)
                if serr:
                    return
                has_parent = False
                if lei:
                    rels, rerr = _gleif_fetch_relationships(lei)
                    if not rerr and isinstance(rels, dict):
                        has_parent = bool(rels.get("direct_parent_lei") or rels.get("ultimate_parent_lei"))
                    # Mirror the loop: subsidiary-candidate search only runs when no parent was found.
                    if not has_parent:
                        _pub, cands, cerr = _gleif_search_candidate_records(cn)
                        if not cerr and cands:
                            # Mirror the loop exactly: it fetches relationships for the
                            # FILTERED + RANKED accepted set, not the raw candidate list, so warm
                            # the same child LEIs the loop will actually read from cache.
                            accepted, _rej = _gleif_split_alternate_child_candidates(cn, lei or "", cands)
                            for c in accepted[:GLEIF_ALT_CHILD_CANDIDATE_LIMIT]:
                                cl = str((c or {}).get("lei") or "").strip()
                                if cl:
                                    _gleif_fetch_relationships(cl)
            except Exception:
                return  # best-effort warm; the sequential loop below is the source of truth
            finally:
                with _pf_lock:
                    _pf_done[0] += 1
                    if conn and _pf_done[0] % 250 == 0:
                        _phase6_report_progress(conn, _pf_done[0], total, f"GLEIF cache warm {_pf_done[0]}/{total}")

        with ThreadPoolExecutor(max_workers=GLEIF_PREFETCH_WORKERS, thread_name_prefix="gleif-prefetch") as _pf_pool:
            list(_pf_pool.map(_gleif_prefetch_company, companies))
        _log("Phase 6: GLEIF cache warm complete; resolving from warm cache.", "green")

    for i, company in enumerate(companies):
        if i > 0 and i % GLEIF_COOLDOWN_EVERY == 0 and GLEIF_COOLDOWN_SECONDS > 0 and not (GLEIF_SKIP_HEALTHY_COOLDOWN and failures_since_cooldown == 0):
            _flush_lei_batch()
            _flush_entity_edge_batch()
            _log(f"Phase 6: Cooldown — pausing {GLEIF_COOLDOWN_SECONDS}s after {i} companies to avoid GLEIF rate limit...", "cyan")
            if conn:
                _phase6_report_progress(conn, i, total, f"Cooldown {GLEIF_COOLDOWN_SECONDS}s — {i}/{total} done")
            _gleif_push_global_cooldown(GLEIF_COOLDOWN_SECONDS)
            consecutive_429s = 0
            failures_since_cooldown = 0
        if consecutive_429s >= 5:
            backoff_secs = min(300, GLEIF_COOLDOWN_SECONDS * (1 + consecutive_429s // 5))
            _log(f"Phase 6: {consecutive_429s} consecutive 429s — backing off {backoff_secs}s...", "yellow")
            if conn:
                _phase6_report_progress(conn, i, total, f"Rate limit backoff {backoff_secs}s — {i}/{total}")
            _gleif_push_global_cooldown(backoff_secs)
            consecutive_429s = 0
            failures_since_cooldown = 0
        if not _process_gleif_company(company):
            retry_queue.append(company)
        if len(_lei_batch) >= gleif_batch_size:
            _flush_lei_batch()
        if len(_legal_entity_batch) >= gleif_batch_size:
            _flush_legal_entity_batch()
        if len(_entity_edge_batch) >= gleif_batch_size:
            _flush_entity_edge_batch()
        if (i + 1) % 20 == 0:
            _flush_lei_batch()
            _flush_entity_edge_batch()
            if conn:
                _phase6_report_progress(conn, i + 1, total, f"GLEIF lookup {i + 1}/{total} — {100.0 * (i + 1) / total:.1f}%")
            batch_ok, batch_fail, total_calls_ok, total_calls_fail = _phase6_log_progress(
                i,
                total,
                companies,
                leis_found,
                gleif_no_parent_data_count,
                len(gleif_companies_with_parent_data),
                legal_entities_written,
                entity_edges_written,
                _entity_edge_batch,
                no_lei_count,
                batch_ok,
                batch_fail,
                total_calls_ok,
                total_calls_fail,
            )
            if (
                not logged_sparse_gleif_parent_note
                and leis_found >= 100
                and gleif_no_parent_data_count >= max(50, int(leis_found * 0.85))
            ):
                _log(
                    "Phase 6: most matched LEIs currently have no direct/ultimate parent in GLEIF; this is expected for top-level issuers. "
                    "Phase 6B SEC EX-21 is the main source of private-subsidiary edges.",
                    "white",
                )
                logged_sparse_gleif_parent_note = True
            _nexus_maybe_log_eta()

    _flush_lei_batch()
    _flush_entity_edge_batch()
    if retry_queue:
        _log(f"Phase 6: Retrying {len(retry_queue)} companies that got transient errors...", "cyan")
        if conn:
            _phase6_report_progress(conn, total, total, f"Retrying {len(retry_queue)} failed companies...")
        _gleif_push_global_cooldown(min(GLEIF_COOLDOWN_SECONDS * 2, 300))
        consecutive_429s = 0
        retry_ok = 0
        retry_fail = 0
        for ri, company in enumerate(retry_queue):
            if consecutive_429s >= 10:
                _log(f"Phase 6: Retry giving up after {ri}/{len(retry_queue)} — still rate limited.", "yellow")
                break
            if ri > 0 and ri % 50 == 0:
                _log(f"Phase 6: Retry cooldown after {ri} retries ({retry_ok} OK, {retry_fail} still failing)...", "cyan")
                _flush_lei_batch()
                _flush_entity_edge_batch()
                _gleif_push_global_cooldown(GLEIF_COOLDOWN_SECONDS)
                consecutive_429s = 0
            if _process_gleif_company(company):
                retry_ok += 1
            else:
                retry_fail += 1
        _flush_lei_batch()
        _flush_entity_edge_batch()
        _log(f"Phase 6: Retry complete — {retry_ok} succeeded, {retry_fail} still failed.", "green" if retry_fail == 0 else "yellow")

    gleif_closed = _phase6_close_stale_gleif_support(session, phase_run_token)
    _phase6_refresh_listed_ancestor_tickers(session)
    projection_written, projection_closed = _phase6_project_company_hierarchy_edges(session, projection_run_token)

    _log(f"Phase 6: GLEIF API summary — {total_calls_ok} calls succeeded, {total_calls_fail} calls failed.", "green")
    _log(
        f"Phase 6: {len(lei_to_ticker)} company LEIs, {gleif_no_parent_data_count} with no GLEIF parent data, "
        f"{len(gleif_companies_with_parent_data)} companies with parent data, "
        f"{no_lei_count} companies with no LEI, "
        f"{legal_entities_written} LegalEntity upserts, {entity_edges_written} PARENT_OF_ENTITY writes, "
        f"{projection_written} projected PARENT_OF edges.",
        "green",
    )
    if gleif_closed or projection_closed:
        _log(
            f"Phase 6: closed {gleif_closed} stale GLEIF supports and {projection_closed} superseded company-only projections.",
            "green",
        )
    try:
        _sync_graph_edge_intervals(session, "PARENT_OF_ENTITY", source_scope="CORPORATE_HIERARCHY", directed=True)
        _sync_graph_edge_intervals(session, "PARENT_OF", source_scope="CORPORATE_HIERARCHY_PROJECTION", directed=True)
    except Exception as e:
        _log_stage_error("Phase 6: GLEIF hierarchy", "sync hierarchy interval history", str(e), e, color="yellow")
    try:
        retired_parent = _retire_relationships(
            session,
            "PARENT_OF",
            "coalesce(r.source_scope, '') = '' OR coalesce(r.source_scope, '') = 'GLEIF_PARENT'",
            directed=True,
            retired_reason="legacy_replaced",
        )
        retired_parent_lei = _retire_relationships(
            session,
            "PARENT_OF_LEI",
            "true",
            directed=True,
            retired_reason="legacy_replaced",
        )
        if retired_parent or retired_parent_lei:
            _log(
                f"Phase 6: retired {retired_parent} legacy PARENT_OF and {retired_parent_lei} legacy PARENT_OF_LEI edge(s) after GLEIF refresh.",
                "green",
            )
    except Exception as e:
        _log_stage_error("Phase 6: GLEIF hierarchy", "retire legacy hierarchy edges", str(e), e, color="yellow")
    if history_start_date:
        coverage_start = _normalize_historical_iso_date(phase_manifest.get("coverage_start")) or history_start_date
        coverage_end = _normalize_historical_iso_date(phase_end_date) or today_iso
        _nexus_update_phase_manifest(
            "phase6",
            mode="annual_snapshot",
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            last_incremental_start=_normalize_historical_iso_date(phase_start_date) or coverage_end,
            last_incremental_end=coverage_end,
            bootstrap_complete=bool(coverage_end >= today_iso),
            extra={
                "gleif_company_leis": len(lei_to_ticker),
                "entity_edge_writes": entity_edges_written,
            },
        )
    _progress(PHASE_PCT[8], f"Phase 6 done: {entity_edges_written} hierarchy edges and {projection_written} company projections.", "green")


def phase6b_sec_ex21_hierarchy(session):
    """
    Phase 6B: SEC EX-21 subsidiary ingestion.
    Maintains mixed public/private hierarchy edges on PARENT_OF_ENTITY from annual filing exhibits
    and projects company-only PARENT_OF edges for the strategy layer.
    """
    _nexus_stage_reset()
    _progress(PHASE_PCT[8], "Phase 6B: SEC EX-21 subsidiary hierarchy...", "cyan")
    company_records_by_ticker, companies, company_records_list = _phase6_load_listed_company_records(session)
    phase6_alias_to_tickers, phase6_record_by_ticker = _phase6_build_company_resolution_maps(company_records_list)
    if not companies:
        _log_stage_unexpected("Phase 6B: SEC EX-21 hierarchy", "company list", "No listed companies available for EX-21 resolution.")
        _progress(PHASE_PCT[9], "Phase 6B skipped.", "green")
        return

    migrated_entities = _phase6_promote_existing_lei_entities(session)
    if migrated_entities:
        _log(f"Phase 6B: promoted {migrated_entities} existing LEIEntity node(s) into LegalEntity.", "green")

    history_start_date = _nexus_historical_start_date()
    phase_manifest = _nexus_phase_manifest("phase6_ex21")
    ex21_start_date, phase_end_date, ignore_parse_cache, phase_start_date = _phase6_ex21_fetch_window(
        "phase6_ex21",
        phase_manifest,
        history_start_date,
    )
    today_iso = _nexus_today_iso()
    if ignore_parse_cache:
        _log(
            "Phase 6B: bootstrap coverage is incomplete; reparsing historical EX-21 exhibits from cached SEC filings and ignoring derived EX-21 parse caches.",
            "white",
        )

    phase_run_token = f"phase6_ex21:{uuid4().hex}"
    projection_run_token = f"phase6_ex21_projection:{uuid4().hex}"
    discard_log_path = _phase6_reset_discard_log(phase_run_token)
    _log(f"Phase 6B: discarded edge audit log -> {discard_log_path}", "white")

    legal_entities_written = 0
    entity_edges_written = 0
    ex21_filings_processed = 0
    ex21_edges_closed = 0
    issuers_with_annual_filings = 0
    issuers_with_ex21_hits = 0
    subsidiaries_seen = 0
    listed_company_promotions = 0
    queued_legal_entity_keys: set[str] = set()
    _legal_entity_batch: list[dict] = []
    _entity_edge_batch: list[dict] = []
    conn = _nexus_rethink_conn
    progress_log_every = PHASE6_EX21_PROGRESS_LOG_EVERY
    progress_log_interval_sec = PHASE6_EX21_PROGRESS_HEARTBEAT_SEC
    last_progress_log_at = time.monotonic()

    def _log_phase6b_discard(category: str, reason: str, payload: dict | None = None) -> None:
        _phase6_log_discarded_edge(phase_run_token, category, reason, payload)

    def _queue_legal_entity(payload: dict) -> None:
        entity_key = str(payload.get("entity_key") or "").strip()
        if not entity_key or entity_key in queued_legal_entity_keys:
            return
        queued_legal_entity_keys.add(entity_key)
        _legal_entity_batch.append(dict(payload))

    def _flush_legal_entity_batch() -> None:
        nonlocal _legal_entity_batch, legal_entities_written
        if not _legal_entity_batch:
            return
        legal_entities_written += _phase6_write_legal_entities(session, _legal_entity_batch)
        _legal_entity_batch = []

    def _flush_entity_edge_batch() -> None:
        nonlocal _entity_edge_batch, entity_edges_written
        if not _entity_edge_batch:
            return
        _flush_legal_entity_batch()
        entity_edges_written += _phase6_write_parent_of_entity_edges(session, _entity_edge_batch)
        _entity_edge_batch = []

    try:
        import sec_edgar_supply_chain as sec_mod
    except Exception as exc:
        sec_mod = None
        _log_stage_error("Phase 6B: SEC EX-21 hierarchy", "load SEC helper module", str(exc), exc, color="yellow")

    issuers_with_cik = [rec for rec in companies if str(rec.get("cik") or "").strip()]
    if conn and issuers_with_cik:
        _phase6b_report_progress(conn, 0, len(issuers_with_cik), f"Annual filing scan 0/{len(issuers_with_cik)} — starting")

    def _emit_phase6b_progress(current_idx: int, last_ticker: str = "", force: bool = False) -> None:
        nonlocal last_progress_log_at
        total = len(issuers_with_cik)
        if total <= 0:
            return
        now = time.monotonic()
        should_emit = force or current_idx == 0 or current_idx == total
        if not should_emit:
            should_emit = (current_idx % progress_log_every == 0) or ((now - last_progress_log_at) >= progress_log_interval_sec)
        if not should_emit:
            return
        pct = 100.0 * current_idx / total
        stage_message = (
            f"EX-21 issuers {current_idx}/{total} — annual filings: {issuers_with_annual_filings}, "
            f"issuers with exhibits: {issuers_with_ex21_hits}, EX-21 filings: {ex21_filings_processed}, subsidiaries: {subsidiaries_seen}, "
            f"listed promotions: {listed_company_promotions}, LegalEntity upserts: {legal_entities_written}, "
            f"edge upserts: {entity_edges_written}{f', last: {last_ticker}' if last_ticker else ''}"
        )
        if conn:
            _phase6b_report_progress(conn, current_idx, total, stage_message)
        _log(f"Phase 6B: [{current_idx}/{total}] {pct:.1f}% — {stage_message.split(' — ', 1)[1]}", "white")
        last_progress_log_at = now

    def _process_prepared_issuer(prepared: dict) -> str:
        nonlocal issuers_with_annual_filings, issuers_with_ex21_hits
        nonlocal ex21_filings_processed, ex21_edges_closed, subsidiaries_seen, listed_company_promotions
        issuer_record = prepared.get("issuer_record") or {}
        issuer_ticker = str(prepared.get("issuer_ticker") or issuer_record.get("ticker") or "").strip().upper()
        error = str(prepared.get("error") or "").strip()
        if error:
            _log_stage_error(
                "Phase 6B: SEC EX-21 hierarchy",
                f"collect annual filings for {issuer_ticker or 'unknown issuer'}",
                error,
                color="yellow",
            )
            return issuer_ticker
        if prepared.get("annual_filings_found"):
            issuers_with_annual_filings += 1
        prepared_filings = list(prepared.get("prepared_filings") or [])
        if prepared_filings:
            issuers_with_ex21_hits += 1
        for filing_bundle in prepared_filings:
            filing = dict(filing_bundle.get("filing") or {})
            entries = list(filing_bundle.get("entries") or [])
            if not filing or not entries:
                continue
            filing_date = _normalize_edge_date(filing.get("filing_date"))
            accession_compact = str(filing.get("accession_compact") or "").strip()
            ex21_filings_processed += 1
            current_child_refs: list[str] = []
            base_filing_url = str(filing.get("filing_url") or "")
            for entry in entries:
                raw_legal_name = str(entry.get("legal_name") or "")
                legal_name = _phase6_clean_ex21_entity_name(raw_legal_name)
                aliases = list(entry.get("aliases") or [])
                jurisdiction = str(entry.get("jurisdiction") or "").strip()
                if not legal_name:
                    continue
                if legal_name != raw_legal_name.strip():
                    aliases, _norms, _risk, _needs_confirmation = _build_legal_entity_aliases(legal_name)
                if not _phase6_is_probable_ex21_entity_name(legal_name):
                    _log_phase6b_discard(
                        "ex21_entry",
                        "invalid_entity_name",
                        {
                            "issuer_ticker": issuer_ticker,
                            "issuer_name": _company_record_best_name(issuer_record),
                            "subsidiary_name": legal_name,
                            "filing_url": base_filing_url,
                        },
                    )
                    continue
                if _phase6_ex21_matches_issuer_name(issuer_record, legal_name):
                    _log_phase6b_discard(
                        "ex21_entry",
                        "same_as_issuer",
                        {
                            "issuer_ticker": issuer_ticker,
                            "issuer_name": _company_record_best_name(issuer_record),
                            "subsidiary_name": legal_name,
                            "filing_url": base_filing_url,
                        },
                    )
                    continue
                subsidiaries_seen += 1
                chosen_ticker = _phase6_choose_listed_company_for_subsidiary(
                    company_records_list,
                    issuer_record,
                    legal_name,
                    aliases,
                    filing_excerpt=f"{legal_name} {jurisdiction}".strip(),
                    filing_url=base_filing_url,
                    discard_logger=_log_phase6b_discard,
                    alias_to_tickers=phase6_alias_to_tickers,
                    record_by_ticker=phase6_record_by_ticker,
                )
                if chosen_ticker:
                    child_record = company_records_by_ticker.get(chosen_ticker)
                    if not _is_company_pair_safe(issuer_record, child_record):
                        _log_phase6b_discard(
                            "ex21_promotion",
                            "unsafe_company_pair",
                            {
                                "issuer_ticker": issuer_ticker,
                                "issuer_name": _company_record_best_name(issuer_record),
                                "subsidiary_name": legal_name,
                                "chosen_ticker": chosen_ticker,
                                "chosen_name": _company_record_best_name(child_record),
                                "filing_url": base_filing_url,
                            },
                        )
                        continue
                    current_child_refs.append(chosen_ticker)
                    listed_company_promotions += 1
                    _entity_edge_batch.append({
                        "parent_kind": "Company",
                        "parent_ref": issuer_ticker,
                        "child_kind": "Company",
                        "child_ref": chosen_ticker,
                        "active_after": filing_date,
                        "last_confirmed": filing_date,
                        "evidence_sources": ["SEC_EX21"],
                        "sec_ex21_supported": True,
                        "sec_ex21_issuer_ticker": issuer_ticker,
                        "sec_ex21_latest_filing_date": filing_date,
                        "sec_ex21_last_accession": accession_compact,
                        "parent_legal_name": _company_record_best_name(issuer_record),
                        "child_legal_name": _company_record_best_name(child_record),
                    })
                    continue
                entity_key = _phase6_build_ex21_entity_key(issuer_ticker, legal_name, accession_compact)
                current_child_refs.append(entity_key)
                _queue_legal_entity(_build_legal_entity_payload(
                    entity_key=entity_key,
                    legal_name=legal_name,
                    display_name=legal_name,
                    aliases=aliases,
                    entity_kind="subsidiary",
                    country=jurisdiction,
                    source_systems=["SEC_EX21"],
                    listed_ancestor_tickers=[issuer_ticker],
                ))
                _entity_edge_batch.append({
                    "parent_kind": "Company",
                    "parent_ref": issuer_ticker,
                    "child_kind": "LegalEntity",
                    "child_ref": entity_key,
                    "active_after": filing_date,
                    "last_confirmed": filing_date,
                    "evidence_sources": ["SEC_EX21"],
                    "sec_ex21_supported": True,
                    "sec_ex21_issuer_ticker": issuer_ticker,
                    "sec_ex21_latest_filing_date": filing_date,
                    "sec_ex21_last_accession": accession_compact,
                    "parent_legal_name": _company_record_best_name(issuer_record),
                    "child_legal_name": legal_name,
                })
            _flush_entity_edge_batch()
            ex21_edges_closed += _phase6_close_omitted_ex21_edges_for_issuer(session, issuer_ticker, current_child_refs, filing_date)
        return issuer_ticker

    if sec_mod is not None:
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        total_issuers = len(issuers_with_cik)
        workers = max(1, min(PHASE6_EX21_PREFETCH_WORKERS, total_issuers or 1))
        max_in_flight = max(workers, min(total_issuers or 1, PHASE6_EX21_MAX_IN_FLIGHT))
        completed_issuers = 0

        if total_issuers > 1:
            _log(
                f"Phase 6B: prefetching SEC annual filings and EX-21 exhibits with {workers} worker(s) "
                f"(up to {max_in_flight} issuers in flight).",
                "cyan",
            )

        def _submit_prefetch(pool, issuer_record: dict):
            return pool.submit(
                _phase6_prepare_ex21_issuer_payload,
                sec_mod,
                issuer_record,
                ex21_start_date=ex21_start_date,
                phase_end_date=phase_end_date,
                phase_start_date=phase_start_date,
                ignore_parse_cache=ignore_parse_cache,
            )

        if workers == 1:
            for issuer_record in issuers_with_cik:
                prepared = _phase6_prepare_ex21_issuer_payload(
                    sec_mod,
                    issuer_record,
                    ex21_start_date=ex21_start_date,
                    phase_end_date=phase_end_date,
                    phase_start_date=phase_start_date,
                    ignore_parse_cache=ignore_parse_cache,
                )
                issuer_ticker = _process_prepared_issuer(prepared)
                completed_issuers += 1
                _emit_phase6b_progress(completed_issuers, issuer_ticker)
        else:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="phase6b-ex21") as pool:
                in_flight: dict[Any, dict] = {}
                next_idx = 0

                def _fill_in_flight() -> None:
                    nonlocal next_idx
                    while next_idx < total_issuers and len(in_flight) < max_in_flight:
                        issuer_record = issuers_with_cik[next_idx]
                        future = _submit_prefetch(pool, issuer_record)
                        in_flight[future] = issuer_record
                        next_idx += 1

                _fill_in_flight()
                while in_flight:
                    done, _ = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
                    for future in done:
                        issuer_record = in_flight.pop(future)
                        issuer_ticker = str(issuer_record.get("ticker") or "").strip().upper()
                        try:
                            prepared = future.result()
                        except Exception as exc:
                            _log_stage_error(
                                "Phase 6B: SEC EX-21 hierarchy",
                                f"prepare issuer payload for {issuer_ticker or 'unknown issuer'}",
                                str(exc),
                                exc,
                                color="yellow",
                            )
                            prepared = {
                                "issuer_record": issuer_record,
                                "issuer_ticker": issuer_ticker,
                                "annual_filings_found": False,
                                "prepared_filings": [],
                                "error": "",
                            }
                        issuer_ticker = _process_prepared_issuer(prepared)
                        completed_issuers += 1
                        _emit_phase6b_progress(completed_issuers, issuer_ticker)
                    _fill_in_flight()

    _flush_entity_edge_batch()
    _phase6_refresh_listed_ancestor_tickers(session)
    _emit_phase6b_progress(len(issuers_with_cik), force=True)
    projection_written, projection_closed = _phase6_project_company_hierarchy_edges(session, projection_run_token)
    live_ex21_edges = _phase6_count_live_hierarchy_edges(
        session,
        rel_type="PARENT_OF_ENTITY",
        source_scope="CORPORATE_HIERARCHY",
        evidence_source="SEC_EX21",
    )
    live_ex21_projections = _phase6_count_live_hierarchy_edges(
        session,
        rel_type="PARENT_OF",
        source_scope="CORPORATE_HIERARCHY_PROJECTION",
        evidence_source="SEC_EX21",
    )

    # Discovery-collapse guard: finding 0 annual filings AND 0 EX-21 filings across hundreds
    # of issuers is not a real empty result (thousands of these companies file EX-21 exhibits) —
    # it's a SEC/network outage (cold cache + failed data.sec.gov fetches) that previously went
    # silently "green" and even marked the phase bootstrap-complete. Make it loud and refuse to
    # advance the manifest so the next run retries; existing hierarchy edges are left intact.
    total_with_cik = len(issuers_with_cik)
    phase6b_discovery_collapsed = (
        total_with_cik >= 500
        and issuers_with_annual_filings == 0
        and ex21_filings_processed == 0
    )
    if phase6b_discovery_collapsed:
        _log_stage_error(
            "Phase 6B: SEC EX-21 hierarchy",
            "annual-filing discovery",
            f"Discovery collapsed: 0 annual filings and 0 EX-21 filings across {total_with_cik} "
            "issuers with a CIK — almost certainly a SEC/network outage, not a real empty result. "
            "Not marking the phase complete or advancing coverage so it retries next run; "
            "existing hierarchy edges are kept.",
            color="red",
        )

    _log(
        f"Phase 6B: {ex21_filings_processed} EX-21 filings processed, "
        f"{legal_entities_written} LegalEntity upserts, {entity_edges_written} PARENT_OF_ENTITY edge upserts, "
        f"{live_ex21_edges} live EX-21 hierarchy edges, {projection_written} projected PARENT_OF edge upserts, "
        f"{live_ex21_projections} live EX-21 company projections.",
        "green",
    )
    if ex21_edges_closed or projection_closed:
        _log(
            f"Phase 6B: closed {ex21_edges_closed} superseded EX-21 hierarchy intervals and "
            f"{projection_closed} superseded company-only projections.",
            "green",
        )
    try:
        _sync_graph_edge_intervals(session, "PARENT_OF_ENTITY", source_scope="CORPORATE_HIERARCHY", directed=True)
        _sync_graph_edge_intervals(session, "PARENT_OF", source_scope="CORPORATE_HIERARCHY_PROJECTION", directed=True)
    except Exception as e:
        _log_stage_error("Phase 6B: SEC EX-21 hierarchy", "sync hierarchy interval history", str(e), e, color="yellow")
    try:
        retired_parent = _retire_relationships(
            session,
            "PARENT_OF",
            "coalesce(r.source_scope, '') = '' OR coalesce(r.source_scope, '') = 'GLEIF_PARENT'",
            directed=True,
            retired_reason="legacy_replaced",
        )
        retired_parent_lei = _retire_relationships(
            session,
            "PARENT_OF_LEI",
            "true",
            directed=True,
            retired_reason="legacy_replaced",
        )
        if retired_parent or retired_parent_lei:
            _log(
                f"Phase 6B: retired {retired_parent} legacy PARENT_OF and {retired_parent_lei} legacy PARENT_OF_LEI edge(s) after EX-21 refresh.",
                "green",
            )
    except Exception as e:
        _log_stage_error("Phase 6B: SEC EX-21 hierarchy", "retire legacy hierarchy edges", str(e), e, color="yellow")
    if history_start_date and not phase6b_discovery_collapsed:
        coverage_start = _normalize_historical_iso_date(phase_manifest.get("coverage_start")) or history_start_date
        coverage_end = _normalize_historical_iso_date(phase_end_date) or today_iso
        _nexus_update_phase_manifest(
            "phase6_ex21",
            mode="annual_snapshot",
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            last_incremental_start=_normalize_historical_iso_date(phase_start_date) or coverage_end,
            last_incremental_end=coverage_end,
            bootstrap_complete=bool(coverage_end >= today_iso),
            extra={
                "ex21_filings_processed": ex21_filings_processed,
                "entity_edge_writes": entity_edges_written,
                "live_entity_edge_count": live_ex21_edges,
            },
        )
    _progress(
        PHASE_PCT[9],
        f"Phase 6B done: {ex21_filings_processed} EX-21 filings processed, {live_ex21_edges} live hierarchy edges and {live_ex21_projections} live company projections.",
        "green",
    )

_PHASE7_13F_MARKET_VALUE_DOLLAR_CUTOVER = "2023-01-03"


def _phase7_13f_market_value_scale(active_after: str) -> float:
    normalized = _normalize_historical_iso_date(active_after) or _normalize_edge_date(active_after) or ""
    if normalized and normalized >= _PHASE7_13F_MARKET_VALUE_DOLLAR_CUTOVER:
        return 1.0
    return 1000.0


def _phase7_parse_reported_market_value(active_after: str, raw_value) -> float:
    try:
        value = float(str(raw_value or "0").replace(",", ""))
    except Exception:
        return 0.0
    return value * _phase7_13f_market_value_scale(active_after)


_PHASE7_UNSUPPORTED_SECURITY_TITLE_RE = re.compile(
    r"\b("
    r"PUT|CALL|WARRANTS?|WT|RTS?|RIGHTS?|UNITS?|NOTES?|BONDS?|DEBENTURES?|"
    r"PREFERRED|PFD|PREF|CONV(?:ERTIBLE)?"
    r")\b",
    re.I,
)


def _phase7_is_supported_equity_holding(title_of_class: str, put_call: str) -> bool:
    put_call_text = str(put_call or "").strip().upper()
    if put_call_text in {"PUT", "CALL"}:
        return False
    title_text = re.sub(r"[^A-Z0-9]+", " ", str(title_of_class or "").upper()).strip()
    if not title_text:
        return True
    return _PHASE7_UNSUPPORTED_SECURITY_TITLE_RE.search(title_text) is None


def _phase7_accumulate_parsed_holding(
    accumulator: dict[tuple[str, str], EdgeResult],
    *,
    institution_id: str,
    institution_name: str,
    issuer_name: str,
    value_usd: float,
    shares: int,
    cusip: str,
    active_after: str,
    filing_period: str = "",
    snapshot_active_after: str = "",
) -> None:
    key = (str(institution_id or "").strip(), str(issuer_name or "").strip().upper()[:80])
    if not key[0] or not key[1]:
        return
    existing = accumulator.get(key)
    if existing is None:
        accumulator[key] = {
            "node_a": key[0],
            "rel": "HOLDS",
            "node_b": key[1],
            "confidence": 0.90,
            "source": "13F-HR",
            "properties": {
                "institution_name": institution_name,
                "value_usd": float(value_usd),
                "shares": int(shares),
                "cusip": str(cusip or "").strip(),
                "name_of_issuer": issuer_name,
                "active_after": active_after,
                "filing_period": filing_period,
                "snapshot_active_after": snapshot_active_after,
            },
        }
        return

    props = existing["properties"]
    props["value_usd"] = float(props.get("value_usd") or 0.0) + float(value_usd or 0.0)
    props["shares"] = int(props.get("shares") or 0) + int(shares or 0)
    incoming_cusip = str(cusip or "").strip()
    if incoming_cusip:
        existing_cusip = str(props.get("cusip") or "").strip()
        if existing_cusip and existing_cusip != incoming_cusip:
            props["cusip"] = ""
        elif not existing_cusip:
            props["cusip"] = incoming_cusip
    if active_after and (not props.get("active_after") or active_after < props["active_after"]):
        props["active_after"] = active_after
    if snapshot_active_after and (
        not props.get("snapshot_active_after") or snapshot_active_after > props["snapshot_active_after"]
    ):
        props["snapshot_active_after"] = snapshot_active_after


def _fetch_13f_holdings_from_quarterly_zip(
    cik_to_ticker: dict[str, str],
    headers: dict,
    max_edges: int = 5_000_000,
) -> list[EdgeResult]:
    """
    Primary source: SEC Form 13F quarterly data set ZIP (all filers, full quarter).
    https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets
    ZIP contains COVERPAGE (filer info) and INFOTABLE (holdings); column names may vary in casing.
    """
    import csv
    import io
    import re
    import zipfile
    import requests

    def _row_key(row: dict, key_aliases: list[str]) -> str:
        """Case-insensitive row lookup (SEC may use ACCESSION_NUMBER or Accession_Number)."""
        row_lower = {k.strip().lower(): v for k, v in (row or {}).items()}
        for k in key_aliases:
            v = row_lower.get(k.lower())
            if v is not None and str(v).strip():
                return str(v).strip()
        return ""

    def _zip_dataset_active_after(zip_name: str) -> str:
        m = re.search(r"-(\d{2}[A-Za-z]{3}\d{4})_form13f\.zip$", zip_name or "", re.I)
        if not m:
            return ""
        try:
            return datetime.strptime(m.group(1), "%d%b%Y").date().isoformat()
        except Exception:
            return ""

    aggregated_edges: dict[tuple[str, str], EdgeResult] = {}
    try:
        page = requests.get(
            "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets",
            headers=headers,
            timeout=15,
        )
        page.raise_for_status()
        text = page.text or ""
        # SEC page: links like https://www.sec.gov/files/structureddata/data/form-13f-data-sets/01sep2025-30nov2025_form13f.zip
        zip_m = re.search(r'href="(https://www\.sec\.gov/files/[^"]+form13f\.zip)"', text, re.I)
        if not zip_m:
            zip_m = re.search(r'href="(/files/[^"]+form13f\.zip)"', text, re.I)
        if not zip_m:
            zip_m = re.search(r'\((https://www\.sec\.gov/files/[^)]+form13f\.zip)\)', text, re.I)
        if not zip_m:
            # Markdown or inline: ](https://...form13f.zip) or first occurrence of full URL
            zip_m = re.search(r'(https://www\.sec\.gov/files/structureddata/[^)\s"]+form13f\.zip)', text, re.I)
        if not zip_m:
            zip_m = re.search(r'(/files/structureddata/[^)\s"]+form13f\.zip)', text, re.I)
        zip_url = zip_m.group(1).strip() if zip_m else None
        if not zip_url:
            _log("Phase 7: No 13F ZIP link found on SEC page.", "yellow")
            return []
        if zip_url.startswith("/"):
            zip_url = "https://www.sec.gov" + zip_url
        # Cache ZIP by quarter (filename from URL) to avoid re-downloading ~80MB
        zip_filename = zip_url.split("/")[-1].split("?")[0] or "quarterly_13f.zip"
        dataset_active_after = _zip_dataset_active_after(zip_filename)
        zip_cache_path = _nexus_cache_path("phase7_13f", zip_filename)
        # 7-day TTL covers the 45-day amendment window for current-quarter ZIPs
        # (SEC re-publishes ZIPs as late filings/amendments arrive).
        zip_bytes = _nexus_read_cached_file(zip_cache_path, _nexus_cache_max_age("phase7_13f", default=_WEEK))
        if zip_bytes is not None:
            zip_buf = io.BytesIO(zip_bytes)
            _nexus_stage_cache_hit(1)
            _log(f"Phase 7: Using cached 13F ZIP ({len(zip_bytes) / (1024*1024):.1f} MB).", "white")
        else:
            _log("Phase 7: Fetching 13F quarterly data set (full quarter, all filers)...", "cyan")
            zip_content = None
            last_err = None
            for attempt in range(1 + PHASE7_13F_ZIP_MAX_RETRIES):
                try:
                    r = requests.get(zip_url, headers=headers, timeout=PHASE7_13F_ZIP_TIMEOUT_SEC, stream=True)
                    r.raise_for_status()
                    zip_content = r.content
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    last_err = e
                    if attempt < PHASE7_13F_ZIP_MAX_RETRIES:
                        _log(f"Phase 7: 13F ZIP download attempt {attempt + 1} timed out; retrying in 10s...", "yellow")
                        time.sleep(10)
                    else:
                        raise
            if zip_content is None:
                raise last_err or RuntimeError("13F ZIP download failed")
            zip_buf = io.BytesIO(zip_content)
            _nexus_write_cached_file(zip_cache_path, zip_content)
            _nexus_stage_download(1)
            _log(f"Phase 7: ZIP downloaded ({len(zip_content) / (1024*1024):.1f} MB).", "white")
    except Exception as e:
        _log_stage_error("Phase 7: 13F ownership", "13F quarterly ZIP (download)", str(e), e, color="yellow")
        return []

    accession_to_name: dict[str, str] = {}
    with zipfile.ZipFile(zip_buf, "r") as zf:
        names = zf.namelist()
        def _is_tabular(n: str) -> bool:
            return n.lower().endswith((".txt", ".csv", ".tsv"))
        cover_name = next((n for n in names if "cover" in n.lower() and _is_tabular(n)), None)
        info_name  = next((n for n in names if "info" in n.lower() and "table" in n.lower() and _is_tabular(n)), None)
        if not info_name:
            info_name = next((n for n in names if "infotable" in n.lower() and _is_tabular(n)), None)
        if not cover_name or not info_name:
            _log("Phase 7: ZIP missing COVERPAGE or INFOTABLE file.", "yellow")
            return []
        try:
            with zf.open(cover_name) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), delimiter="\t")
                for row in reader:
                    acc  = _row_key(row, ["ACCESSION_NUMBER", "ACCESSION"])
                    name = _row_key(row, ["FILINGMANAGER_NAME", "FILER_NAME", "COMPANY_NAME"])
                    if acc and name:
                        accession_to_name[acc] = name
            _log(f"Phase 7: COVERPAGE: {len(accession_to_name)} filers.", "white")
        except Exception as e:
            _log(f"Phase 7: COVERPAGE parse error: {e}", "yellow")
        try:
            with zf.open(info_name) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace"), delimiter="\t")
                for row in reader:
                    if len(aggregated_edges) >= max_edges:
                        break
                    acc       = _row_key(row, ["ACCESSION_NUMBER", "ACCESSION"])
                    name_held = _row_key(row, ["NAMEOFISSUER", "NAME_OF_ISSUER", "ISSUER"])
                    title_of_class = _row_key(row, ["TITLEOFCLASS", "TITLE_OF_CLASS", "TITLE"])
                    put_call = _row_key(row, ["PUTCALL", "PUT_CALL"])
                    val_str   = _row_key(row, ["VALUE", "VALUE_XML"]) or "0"
                    shares_str = _row_key(row, ["SSHPRNAMT", "SHARES", "SHPRNAMT"]) or "0"
                    val_str = val_str.replace(",", "")
                    shares_str = shares_str.replace(",", "")
                    try:
                        value = _phase7_parse_reported_market_value(dataset_active_after, val_str)
                        shares = int(float(shares_str))         # SSHPRNAMT is actual shares (not thousands)
                    except Exception:
                        value = shares = 0
                    if not name_held or value < 50_000 or shares <= 0:  # include smaller positions to match more companies
                        continue
                    if not _phase7_is_supported_equity_holding(title_of_class, put_call):
                        continue
                    manager_name = accession_to_name.get(acc) or "Unknown Institution"
                    institution_id = re.sub(r"\W+", "_", manager_name.lower())[:80]
                    _phase7_accumulate_parsed_holding(
                        aggregated_edges,
                        institution_id=institution_id,
                        institution_name=manager_name,
                        issuer_name=name_held,
                        value_usd=value,
                        shares=shares,
                        cusip=_row_key(row, ["CUSIP"]) or "",
                        active_after=dataset_active_after,
                    )
        except Exception as e:
            _log_stage_error("Phase 7: 13F ownership", "13F ZIP INFOTABLE parse", str(e), e, color="yellow")
    edges = list(aggregated_edges.values())
    _log(f"Phase 7: Quarterly ZIP produced {len(edges)} holdings edges (before name resolution).", "green")
    return edges


def _fetch_13f_holdings(cik_to_ticker: dict[str, str]) -> list[EdgeResult]:
    """
    Legacy wrapper that returns raw holdings edges only.

    The active Phase 7 path uses the newer snapshot helpers. Keep this wrapper
    delegated to those helpers so older call sites inherit the same filtering,
    scaling, and aggregation safeguards.
    """
    sec_name = os.environ.get("SEC_EDGAR_COMPANY_NAME", "IntelliStock").strip()
    sec_email = os.environ.get("SEC_EDGAR_EMAIL", "contact@intellistock.local").strip()
    headers = {"User-Agent": f"{sec_name} {sec_email}"}

    edges = _fetch_13f_holdings_from_quarterly_zip(cik_to_ticker, headers)
    if edges:
        return edges

    atom_snapshot = _fetch_13f_holdings_from_atom_feed(cik_to_ticker, headers)
    if atom_snapshot:
        return list(atom_snapshot.get("edges") or [])
    return []


def _phase7_13f_row_key(row: dict, key_aliases: list[str]) -> str:
    """Case-insensitive row lookup for SEC 13F tabular data."""
    row_lower = {str(k).strip().lower(): v for k, v in (row or {}).items()}
    for key in key_aliases:
        value = row_lower.get(key.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _phase7_13f_period_bounds(zip_name: str) -> tuple[datetime | None, datetime | None]:
    match = re.search(
        r"(?P<start>\d{2}[A-Za-z]{3}\d{4})-(?P<end>\d{2}[A-Za-z]{3}\d{4})_form13f\.zip$",
        zip_name or "",
        re.I,
    )
    if not match:
        return None, None
    try:
        start_dt = datetime.strptime(match.group("start"), "%d%b%Y")
        end_dt = datetime.strptime(match.group("end"), "%d%b%Y")
        return start_dt, end_dt
    except Exception:
        return None, None


def _phase7_13f_dataset_active_after(zip_name: str) -> str:
    _, end_dt = _phase7_13f_period_bounds(zip_name)
    return end_dt.date().isoformat() if end_dt else ""


def _phase7_13f_dataset_period_key(zip_name: str) -> str:
    label = os.path.splitext(os.path.basename(zip_name or ""))[0]
    return label or f"13f_zip_{uuid4().hex[:12]}"


def _phase7_13f_atom_period_key(active_after: str) -> str:
    normalized = _normalize_edge_date(active_after)
    if normalized:
        return f"atom_{normalized}"
    return f"atom_{datetime.now(timezone.utc).date().isoformat()}"


def _phase7_13f_extract_all_zip_manifests(page_text: str) -> list[dict]:
    """Parse all quarterly 13F ZIP links from the SEC dataset page."""
    urls: list[str] = []
    for match in re.findall(r'https://www\.sec\.gov/files/[^"\')\s]+form13f\.zip', page_text or "", re.I):
        urls.append(match.strip())
    for match in re.findall(r'/files/[^"\')\s]+form13f\.zip', page_text or "", re.I):
        urls.append(f"https://www.sec.gov{match.strip()}")

    manifests_by_url: dict[str, dict] = {}
    for raw_url in urls:
        zip_url = raw_url.strip()
        zip_filename = zip_url.split("/")[-1].split("?")[0] or "quarterly_13f.zip"
        _, end_dt = _phase7_13f_period_bounds(zip_filename)
        if end_dt is None:
            continue
        manifests_by_url[zip_url] = {
            "zip_url": zip_url,
            "zip_filename": zip_filename,
            "period_key": _phase7_13f_dataset_period_key(zip_filename),
            "active_after": _phase7_13f_dataset_active_after(zip_filename),
            "end_dt": end_dt,
        }

    return sorted(
        manifests_by_url.values(),
        key=lambda item: (item["end_dt"], item["zip_filename"]),
    )


def _select_phase7_zip_manifests(
    manifests: list[dict],
    history_quarters: int,
    historical_start_date: str = "",
    last_incremental_end: str = "",
) -> list[dict]:
    start_iso = _normalize_historical_iso_date(historical_start_date)
    last_end_iso = _normalize_historical_iso_date(last_incremental_end)
    selected = list(manifests or [])
    if start_iso:
        selected = [
            item for item in selected
            if _normalize_historical_iso_date(item.get("active_after")) >= start_iso
        ]
        if last_end_iso:
            selected = [
                item for item in selected
                if _normalize_historical_iso_date(item.get("active_after")) > last_end_iso
            ]
    else:
        selected = selected[-_clamp_phase7_history_quarters(history_quarters):]
    return [
        {
            "zip_url": item["zip_url"],
            "zip_filename": item["zip_filename"],
            "period_key": item["period_key"],
            "active_after": item["active_after"],
            "complete_snapshot": True,
        }
        for item in selected
    ]


def _phase7_13f_extract_zip_manifests(page_text: str, history_quarters: int) -> list[dict]:
    """Parse quarterly 13F ZIP links and keep the most recent configured snapshots."""
    return _select_phase7_zip_manifests(
        _phase7_13f_extract_all_zip_manifests(page_text),
        history_quarters,
    )


def _fetch_13f_quarterly_zip_manifests(
    headers: dict,
    history_quarters: int,
    historical_start_date: str = "",
    phase_manifest: dict | None = None,
    ignore_existing_coverage: bool = False,
) -> list[dict]:
    import requests

    try:
        response = requests.get(
            "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets",
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
    except Exception as e:
        _log_stage_error("Phase 7: 13F ownership", "13F quarterly ZIP index", str(e), e, color="yellow")
        return []

    last_incremental_end = ""
    if isinstance(phase_manifest, dict) and not ignore_existing_coverage:
        last_incremental_end = phase_manifest.get("last_incremental_end") or phase_manifest.get("coverage_end") or ""
    manifests = _select_phase7_zip_manifests(
        _phase7_13f_extract_all_zip_manifests(response.text or ""),
        history_quarters,
        historical_start_date=historical_start_date,
        last_incremental_end=last_incremental_end if historical_start_date else "",
    )
    if not manifests:
        if last_incremental_end:
            _log("Phase 7: No new 13F ZIP snapshots beyond existing historical coverage.", "white")
        else:
            _log("Phase 7: No 13F ZIP links found on SEC data-set page.", "yellow")
        return []
    return manifests


def _fetch_13f_holdings_from_quarterly_zip_dataset(
    cik_to_ticker: dict[str, str],
    headers: dict,
    manifest: dict,
    max_edges: int = 5_000_000,
) -> dict | None:
    """
    Fetch one quarter's 13F snapshot from the SEC structured ZIP.
    Returns a snapshot dict with raw holdings and source dates.
    """
    del cik_to_ticker  # 13F issuer resolution is handled in phase7_13f_ownership.
    import csv
    import io
    import zipfile
    import requests

    zip_url = str(manifest.get("zip_url") or "").strip()
    zip_filename = str(manifest.get("zip_filename") or "").strip() or "quarterly_13f.zip"
    period_key = str(manifest.get("period_key") or _phase7_13f_dataset_period_key(zip_filename))
    dataset_active_after = _normalize_edge_date(
        manifest.get("active_after") or _phase7_13f_dataset_active_after(zip_filename)
    )
    if not zip_url:
        return None

    try:
        zip_cache_path = _nexus_cache_path("phase7_13f", zip_filename)
        # 7-day TTL covers the 45-day amendment window for current-quarter ZIPs
        # (SEC re-publishes ZIPs as late filings/amendments arrive).
        zip_bytes = _nexus_read_cached_file(zip_cache_path, _nexus_cache_max_age("phase7_13f", default=_WEEK))
        if zip_bytes is not None:
            zip_buf = io.BytesIO(zip_bytes)
            _nexus_stage_cache_hit(1)
            _log(
                f"Phase 7: Using cached 13F ZIP {zip_filename} ({len(zip_bytes) / (1024 * 1024):.1f} MB).",
                "white",
            )
        else:
            _log(f"Phase 7: Fetching 13F quarterly data set {zip_filename}...", "cyan")
            zip_content = None
            last_err = None
            for attempt in range(1 + PHASE7_13F_ZIP_MAX_RETRIES):
                try:
                    response = requests.get(
                        zip_url,
                        headers=headers,
                        timeout=PHASE7_13F_ZIP_TIMEOUT_SEC,
                        stream=True,
                    )
                    response.raise_for_status()
                    zip_content = response.content
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    last_err = e
                    if attempt < PHASE7_13F_ZIP_MAX_RETRIES:
                        _log(
                            f"Phase 7: 13F ZIP {zip_filename} attempt {attempt + 1} timed out; retrying in 10s...",
                            "yellow",
                        )
                        time.sleep(10)
                    else:
                        raise
            if zip_content is None:
                raise last_err or RuntimeError("13F ZIP download failed")
            zip_buf = io.BytesIO(zip_content)
            _nexus_write_cached_file(zip_cache_path, zip_content)
            _nexus_stage_download(1)
            _log(
                f"Phase 7: ZIP {zip_filename} downloaded ({len(zip_content) / (1024 * 1024):.1f} MB).",
                "white",
            )
    except Exception as e:
        _log_stage_error(
            "Phase 7: 13F ownership",
            f"13F quarterly ZIP download ({zip_filename})",
            str(e),
            e,
            color="yellow",
        )
        return None

    accession_to_meta: dict[str, dict] = {}
    aggregated_edges: dict[tuple[str, str], EdgeResult] = {}
    with zipfile.ZipFile(zip_buf, "r") as zf:
        names = zf.namelist()

        def _is_tabular(name: str) -> bool:
            return name.lower().endswith((".txt", ".csv", ".tsv"))

        cover_name = next((name for name in names if "cover" in name.lower() and _is_tabular(name)), None)
        info_name = next((name for name in names if "info" in name.lower() and "table" in name.lower() and _is_tabular(name)), None)
        if not info_name:
            info_name = next((name for name in names if "infotable" in name.lower() and _is_tabular(name)), None)
        if not cover_name or not info_name:
            _log(f"Phase 7: ZIP {zip_filename} missing COVERPAGE or INFOTABLE file.", "yellow")
            return None

        try:
            with zf.open(cover_name) as handle:
                reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8", errors="replace"), delimiter="\t")
                for row in reader:
                    accession = _phase7_13f_row_key(row, ["ACCESSION_NUMBER", "ACCESSION"])
                    manager_name = _phase7_13f_row_key(row, ["FILINGMANAGER_NAME", "FILER_NAME", "COMPANY_NAME"])
                    if not accession or not manager_name:
                        continue
                    manager_cik = re.sub(
                        r"\D+",
                        "",
                        _phase7_13f_row_key(
                            row,
                            ["FILINGMANAGER_CIK", "CIK", "CIK_NUMBER", "MANAGER_CIK"],
                        ),
                    )
                    filing_date = _normalize_edge_date(
                        _phase7_13f_row_key(
                            row,
                            ["FILED_AS_OF_DATE", "FILING_DATE", "DATE_FILED", "SUBMISSION_DATE", "FILE_DATE"],
                        )
                    ) or dataset_active_after
                    institution_id = (
                        f"cik_{manager_cik.zfill(10)}"
                        if manager_cik
                        else re.sub(r"\W+", "_", manager_name.lower())[:80]
                    )
                    accession_to_meta[accession] = {
                        "institution_name": manager_name,
                        "institution_id": institution_id,
                        "active_after": filing_date,
                    }
            _log(f"Phase 7: {zip_filename} COVERPAGE: {len(accession_to_meta)} filers.", "white")
        except Exception as e:
            _log(f"Phase 7: {zip_filename} COVERPAGE parse error: {e}", "yellow")

        try:
            with zf.open(info_name) as handle:
                reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8", errors="replace"), delimiter="\t")
                for row in reader:
                    if len(aggregated_edges) >= max_edges:
                        break
                    accession = _phase7_13f_row_key(row, ["ACCESSION_NUMBER", "ACCESSION"])
                    name_held = _phase7_13f_row_key(row, ["NAMEOFISSUER", "NAME_OF_ISSUER", "ISSUER"])
                    title_of_class = _phase7_13f_row_key(row, ["TITLEOFCLASS", "TITLE_OF_CLASS", "TITLE"])
                    put_call = _phase7_13f_row_key(row, ["PUTCALL", "PUT_CALL"])
                    val_str = (_phase7_13f_row_key(row, ["VALUE", "VALUE_XML"]) or "0").replace(",", "")
                    shares_str = (_phase7_13f_row_key(row, ["SSHPRNAMT", "SHARES", "SHPRNAMT"]) or "0").replace(",", "")
                    try:
                        value = _phase7_parse_reported_market_value(dataset_active_after, val_str)
                        shares = int(float(shares_str))
                    except Exception:
                        value = 0.0
                        shares = 0
                    if not name_held or value < 50_000 or shares <= 0:
                        continue
                    if not _phase7_is_supported_equity_holding(title_of_class, put_call):
                        continue
                    meta = accession_to_meta.get(accession) or {}
                    manager_name = meta.get("institution_name") or "Unknown Institution"
                    institution_id = meta.get("institution_id") or re.sub(r"\W+", "_", manager_name.lower())[:80]
                    _phase7_accumulate_parsed_holding(
                        aggregated_edges,
                        institution_id=institution_id,
                        institution_name=manager_name,
                        issuer_name=name_held,
                        value_usd=value,
                        shares=shares,
                        cusip=_phase7_13f_row_key(row, ["CUSIP"]) or "",
                        active_after=meta.get("active_after") or dataset_active_after,
                        filing_period=period_key,
                        snapshot_active_after=dataset_active_after,
                    )
        except Exception as e:
            _log_stage_error(
                "Phase 7: 13F ownership",
                f"13F ZIP INFOTABLE parse ({zip_filename})",
                str(e),
                e,
                color="yellow",
            )
            return None

    edges = list(aggregated_edges.values())
    _log(
        f"Phase 7: {zip_filename} produced {len(edges)} holdings edges (before name resolution).",
        "green",
    )
    return {
        "period_key": period_key,
        "active_after": dataset_active_after,
        "complete_snapshot": True,
        "label": zip_filename,
        "edges": edges,
    }


def _fetch_13f_holdings_from_atom_feed(cik_to_ticker: dict[str, str], headers: dict) -> dict | None:
    """
    Fallback: recent 13F filings via Atom feed. This is incomplete, so it only updates touched institutions.
    """
    del cik_to_ticker  # Name resolution is handled after raw holdings extraction.
    import requests
    import xml.etree.ElementTree as ET

    aggregated_edges: dict[tuple[str, str], EdgeResult] = {}
    # SEC EDGAR Atom feed is a rolling "latest" endpoint - 1-day TTL.
    max_age = _nexus_cache_max_age("phase7_13f_atom")

    _log("Phase 7: Quarterly ZIP produced no data; trying Atom feed fallback.", "cyan")
    feed_cache = _nexus_cache_path("phase7_13f", "feed.xml")
    feed_bytes = _nexus_read_cached_file(feed_cache, max_age)
    feed_failed = False
    if feed_bytes is None:
        try:
            url = (
                "https://www.sec.gov/cgi-bin/browse-edgar"
                "?action=getcurrent&type=13F-HR&dateb=&owner=include&count=100&output=atom"
            )
            response = requests.get(url, headers=headers, timeout=20)
            if response.status_code == 410:
                feed_failed = True
            else:
                response.raise_for_status()
                feed_bytes = response.text.encode("utf-8")
                _nexus_write_cached_file(feed_cache, feed_bytes)
                _nexus_stage_download(1)
        except Exception as e:
            _log_stage_error("Phase 7: 13F ownership", "13F Atom feed fetch", str(e), e, color="yellow")
            feed_failed = True
    else:
        _nexus_stage_cache_hit(1)

    if feed_failed or feed_bytes is None:
        return None

    try:
        root = ET.fromstring(feed_bytes.decode("utf-8"))
    except Exception as e:
        _log_stage_error("Phase 7: 13F ownership", "13F feed XML parse", str(e), e, color="yellow")
        return None

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    filings: list[dict] = []
    for entry in (root.findall(".//atom:entry", ns) or root.findall(".//{http://www.w3.org/2005/Atom}entry")):
        filing_url = ""
        for link in (entry.findall("atom:link", ns) or entry.findall("{http://www.w3.org/2005/Atom}link")):
            href = (link.get("href") or "").strip()
            if href and "Archives" in href:
                filing_url = href
                break
        if not filing_url:
            continue
        updated = ""
        updated_el = entry.find("atom:updated", ns) or entry.find("{http://www.w3.org/2005/Atom}updated")
        if updated_el is not None and (updated_el.text or "").strip():
            updated = _normalize_edge_date(str(updated_el.text).strip()[:10])
        filings.append({"url": filing_url, "active_after": updated})
        if len(filings) >= 100:
            break

    if not filings:
        _log("Phase 7: 13F feed returned no filings.", "yellow")
        return None

    _log(f"Phase 7: Atom feed: processing up to {len(filings)} filings.", "cyan")
    snapshot_active_after = ""
    for idx, filing in enumerate(filings):
        filing_url = filing["url"]
        filing_active_after = _normalize_edge_date(filing.get("active_after")) or ""
        if filing_active_after:
            snapshot_active_after = max(snapshot_active_after, filing_active_after) if snapshot_active_after else filing_active_after
        try:
            index_cache = _nexus_cache_path("phase7_13f", f"filing_{idx}_index.html")
            xml_cache = _nexus_cache_path("phase7_13f", f"filing_{idx}.xml")
            index_bytes = _nexus_read_cached_file(index_cache, max_age)
            xml_bytes = _nexus_read_cached_file(xml_cache, max_age)
            if index_bytes is None or xml_bytes is None:
                time.sleep(0.15)
                response = requests.get(filing_url, headers=headers, timeout=20)
                response.raise_for_status()
                index_bytes = response.text.encode("utf-8")
                _nexus_write_cached_file(index_cache, index_bytes)
                xml_links = re.findall(r'href="([^"]+\.xml)"', response.text, re.I)
                if not xml_links:
                    continue
                xml_url = xml_links[0] if xml_links[0].startswith("http") else f"https://www.sec.gov{xml_links[0]}"
                time.sleep(0.15)
                xml_response = requests.get(xml_url, headers=headers, timeout=20)
                xml_response.raise_for_status()
                xml_bytes = xml_response.text.encode("utf-8")
                _nexus_write_cached_file(xml_cache, xml_bytes)
                _nexus_stage_download(2)
            else:
                _nexus_stage_cache_hit(2)

            index_text = index_bytes.decode("utf-8", errors="replace")
            xml_text = xml_bytes.decode("utf-8", errors="replace")
            institution_match = re.search(r"<company-name>(.*?)</company-name>", index_text, re.I | re.S)
            institution_name = institution_match.group(1).strip() if institution_match else "Unknown Institution"
            institution_id = re.sub(r"\W+", "_", institution_name.lower())[:80]

            try:
                xroot = ET.fromstring(xml_text)
            except Exception:
                continue
            ns13f = {"ns": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}
            for info_table in xroot.findall(".//ns:infoTable", ns13f) or xroot.findall(".//infoTable"):
                def _txt(el, tag):
                    child = (el.find(f"ns:{tag}", ns13f) or el.find(tag))
                    return (child.text or "").strip() if child is not None else ""

                cusip = _txt(info_table, "cusip")
                name_held = _txt(info_table, "nameOfIssuer")
                title_of_class = _txt(info_table, "titleOfClass")
                put_call = _txt(info_table, "putCall")
                val_str = _txt(info_table, "value")
                shares_str = _txt(info_table, "sshPrnamt")
                try:
                    value = _phase7_parse_reported_market_value(filing_active_after, val_str)
                    shares = int(shares_str.replace(",", ""))
                except Exception:
                    value = 0.0
                    shares = 0
                if not name_held or value < 1_000_000 or shares <= 0:
                    continue
                if not _phase7_is_supported_equity_holding(title_of_class, put_call):
                    continue
                _phase7_accumulate_parsed_holding(
                    aggregated_edges,
                    institution_id=institution_id,
                    institution_name=institution_name,
                    issuer_name=name_held,
                    value_usd=value,
                    shares=shares,
                    cusip=cusip,
                    active_after=filing_active_after,
                    filing_period=_phase7_13f_atom_period_key(filing_active_after),
                    snapshot_active_after=filing_active_after,
                )
        except Exception as e:
            _log_stage_error("Phase 7: 13F ownership", "13F filing parse (per filing)", str(e), e, color="yellow")
            continue

    edges = list(aggregated_edges.values())
    if not edges:
        return None
    return {
        "period_key": _phase7_13f_atom_period_key(snapshot_active_after),
        "active_after": snapshot_active_after,
        "complete_snapshot": False,
        "label": "Atom feed fallback",
        "edges": edges,
    }


def _fetch_13f_holding_snapshot_specs(
    history_quarters: int,
    historical_start_date: str = "",
    phase_manifest: dict | None = None,
    ignore_existing_coverage: bool = False,
) -> tuple[dict, list[dict]]:
    """
    Return lightweight 13F snapshot specs in quarter order, oldest to newest.

    Specs intentionally omit holdings payloads so Phase 7 can process one quarter at a time
    instead of retaining every quarter's edge list in memory.
    """
    sec_name = os.environ.get("SEC_EDGAR_COMPANY_NAME", "IntelliStock").strip()
    sec_email = os.environ.get("SEC_EDGAR_EMAIL", "contact@intellistock.local").strip()
    headers = {"User-Agent": f"{sec_name} {sec_email}"}

    manifests = _fetch_13f_quarterly_zip_manifests(
        headers,
        history_quarters,
        historical_start_date=historical_start_date,
        phase_manifest=phase_manifest,
        ignore_existing_coverage=ignore_existing_coverage,
    )
    if manifests:
        return headers, [
            {
                "fetch_mode": "zip",
                "zip_url": manifest.get("zip_url"),
                "zip_filename": manifest.get("zip_filename"),
                "period_key": manifest.get("period_key"),
                "active_after": manifest.get("active_after"),
                "complete_snapshot": True,
                "label": manifest.get("zip_filename") or manifest.get("period_key") or "13F snapshot",
            }
            for manifest in manifests
        ]

    if historical_start_date and isinstance(phase_manifest, dict) and not ignore_existing_coverage:
        last_incremental_end = (
            phase_manifest.get("last_incremental_end")
            or phase_manifest.get("coverage_end")
            or ""
        )
        if _normalize_historical_iso_date(last_incremental_end):
            return headers, []

    return headers, [{"fetch_mode": "atom", "complete_snapshot": False, "label": "Atom feed fallback"}]


def _phase7_should_ignore_existing_coverage(session, phase_manifest: dict, history_start_date: str) -> bool:
    """Force a full historical 13F rebuild when coverage metadata exists but the graph is empty."""
    if history_start_date and _nexus_force_bootstrap_requested():
        return True
    if not history_start_date:
        return False
    if not bool((phase_manifest or {}).get("bootstrap_complete")):
        return True
    try:
        rec = session.run(
            "MATCH ()-[r:HOLDS {source_scope: '13F_HR'}]->() RETURN count(r) AS count"
        ).single()
        return int((rec or {}).get("count") or 0) <= 0
    except Exception:
        return False


def _phase12_should_ignore_existing_coverage(session, phase_manifest: dict, history_start_date: str) -> bool:
    """Force a full historical 8-K rebuild when coverage metadata exists but the graph is empty."""
    if history_start_date and _nexus_force_bootstrap_requested():
        return True
    if not history_start_date:
        return False
    if not bool((phase_manifest or {}).get("bootstrap_complete")):
        return True
    try:
        rec = session.run(
            "MATCH ()-[r:STRATEGIC_PARTNER {source_scope: 'SEC_8K_ITEM_1_01'}]->() RETURN count(r) AS count"
        ).single()
        return int((rec or {}).get("count") or 0) <= 0
    except Exception:
        return False


def _fetch_13f_holdings_snapshot_from_spec(
    cik_to_ticker: dict[str, str],
    headers: dict,
    spec: dict,
) -> dict | None:
    """Load a single 13F snapshot payload from a lightweight spec."""
    mode = str((spec or {}).get("fetch_mode") or "zip").strip().lower()
    if mode == "atom":
        return _fetch_13f_holdings_from_atom_feed(cik_to_ticker, headers)
    manifest = {
        "zip_url": spec.get("zip_url"),
        "zip_filename": spec.get("zip_filename"),
        "period_key": spec.get("period_key"),
        "active_after": spec.get("active_after"),
    }
    return _fetch_13f_holdings_from_quarterly_zip_dataset(cik_to_ticker, headers, manifest)


def _phase7_snapshot_valid_until_dates(snapshots: list[dict]) -> list[str]:
    """For complete quarterly snapshots, each snapshot remains current until the next snapshot becomes public."""
    normalized_dates = [
        _normalize_edge_date(snapshot.get("active_after"))
        for snapshot in snapshots
    ]
    valid_until_dates: list[str] = []
    for idx, snapshot in enumerate(snapshots):
        next_date = ""
        if bool(snapshot.get("complete_snapshot")):
            for later_idx in range(idx + 1, len(snapshots)):
                if not bool((snapshots[later_idx] or {}).get("complete_snapshot")):
                    continue
                candidate = normalized_dates[later_idx]
                if candidate:
                    next_date = candidate
                    break
        valid_until_dates.append(next_date)
    return valid_until_dates


def _phase7_snapshot_institution_value_totals(
    edges: list[dict] | None,
    default_period_key: str = "",
) -> dict[tuple[str, str], float]:
    """
    Compute per-institution/per-period 13F portfolio totals from the raw snapshot payload.

    We intentionally total every raw holding row before issuer-name resolution so that
    weight_pct remains anchored to the filing's actual portfolio even when some issuer
    names fail company resolution later in the write path.
    """
    totals: dict[tuple[str, str], float] = {}
    for edge in edges or []:
        props = edge.get("properties") or {}
        inst_id = str(edge.get("node_a") or "").strip()
        if not inst_id:
            continue
        period_key = str(props.get("filing_period") or default_period_key or "").strip()
        raw_value = props.get("value_usd")
        try:
            value_usd = float(raw_value or 0.0)
        except Exception:
            value_usd = 0.0
        try:
            shares = int(float(props.get("shares", 0) or 0))
        except Exception:
            shares = 0
        if value_usd <= 0 or shares <= 0:
            continue
        key = (inst_id, period_key)
        totals[key] = totals.get(key, 0.0) + value_usd
    return totals


def _phase7_snapshot_resolved_value_totals(
    edges: list[dict] | None,
    default_period_key: str = "",
    resolve_issuer=None,
) -> dict[tuple[str, str], float]:
    """
    Compute per-institution/per-period totals from the holdings that will actually be written.

    This mirrors the resolved-write path exactly, including issuer resolution and any
    value normalization. Doing it in Python keeps Phase 7 off the expensive Neo4j
    post-merge weight recompute path for every snapshot.
    """
    if not callable(resolve_issuer):
        return {}
    totals: dict[tuple[str, str], float] = {}
    for edge in edges or []:
        props = edge.get("properties") or {}
        inst_id = str(edge.get("node_a") or "").strip()
        if not inst_id:
            continue
        period_key = str(props.get("filing_period") or default_period_key or "").strip()
        issuer_name = str(props.get("name_of_issuer") or "").strip()
        raw_value = props.get("value_usd")
        try:
            value_usd = float(raw_value or 0.0)
        except Exception:
            value_usd = 0.0
        try:
            shares = int(float(props.get("shares", 0) or 0))
        except Exception:
            shares = 0
        if value_usd <= 0 or shares <= 0 or not issuer_name:
            continue
        company_ticker = resolve_issuer(issuer_name)
        if not company_ticker:
            continue
        value_usd, _corrected_1000x = _phase7_normalize_resolved_holding_value_usd(
            company_ticker,
            shares,
            value_usd,
        )
        if value_usd <= 0:
            continue
        key = (inst_id, period_key)
        totals[key] = totals.get(key, 0.0) + value_usd
    return totals


def _phase7_snapshot_weight_pct(
    inst_id: str,
    period_key: str,
    value_usd: float,
    totals: dict[tuple[str, str], float] | None,
) -> float | None:
    total_value = float((totals or {}).get((str(inst_id or "").strip(), str(period_key or "").strip())) or 0.0)
    if total_value <= 0 or value_usd <= 0:
        return None
    return round((float(value_usd) / total_value) * 100.0, 6)


def _phase7_backfill_snapshot_weight_pct(
    session,
    run_token: str,
    snapshot_value_totals: dict[tuple[str, str], float] | None,
) -> int:
    """
    Reapply current-snapshot weight_pct values from raw filing totals.

    The live merge path already computes `weight_pct` in Python, but this guarded
    Neo4j-side backfill ensures a future write-path regression cannot leave an
    otherwise valid 13F snapshot with null weights.
    """
    batch: list[dict] = []
    for (inst_id, period_key), total_value in (snapshot_value_totals or {}).items():
        inst = str(inst_id or "").strip()
        period = str(period_key or "").strip()
        try:
            total = float(total_value or 0.0)
        except Exception:
            total = 0.0
        if not inst or not period or total <= 0:
            continue
        batch.append({"inst_id": inst, "period_key": period, "total_value": total})
    if not batch:
        return 0
    rec = session.run(
        """
        UNWIND $batch AS p
        MATCH (:Institution {id: p.inst_id})-[r:HOLDS {source_scope: '13F_HR', filing_period: p.period_key}]->(:Company)
        WHERE coalesce(r.current_run_token, '') = $run_token
          AND coalesce(r.shares, 0) > 0
        SET r.weight_pct = CASE
            WHEN p.total_value <= 0 OR r.value_usd IS NULL OR toFloat(r.value_usd) <= 0 THEN NULL
            ELSE round((toFloat(r.value_usd) / p.total_value) * 100.0, 6)
        END
        RETURN count(r) AS updated
        """,
        batch=batch,
        run_token=run_token,
    ).single()
    return int((rec or {}).get("updated") or 0)


def _phase7_recompute_snapshot_weight_pct_from_graph(
    session,
    run_token: str,
    period_key: str,
) -> int:
    """
    Recompute weight_pct from the corrected values written for the current run.

    This is a safety net for write-path normalizations so snapshot weights stay
    consistent even when a subset of 13F rows needs value scale correction.
    """
    period = str(period_key or "").strip()
    if not period:
        return 0
    rec = session.run(
        """
        MATCH (:Institution)-[r:HOLDS {source_scope: '13F_HR', filing_period: $period_key}]->(:Company)
        WHERE coalesce(r.current_run_token, '') = $run_token
          AND coalesce(r.shares, 0) > 0
          AND coalesce(r.value_usd, 0) > 0
        WITH r.institution_id AS inst_id, sum(toFloat(r.value_usd)) AS total_value
        MATCH (:Institution)-[r:HOLDS {source_scope: '13F_HR', filing_period: $period_key}]->(:Company)
        WHERE coalesce(r.current_run_token, '') = $run_token
          AND coalesce(r.institution_id, '') = coalesce(inst_id, '')
          AND coalesce(r.shares, 0) > 0
        SET r.weight_pct = CASE
            WHEN total_value <= 0 OR r.value_usd IS NULL OR toFloat(r.value_usd) <= 0 THEN NULL
            ELSE round((toFloat(r.value_usd) / total_value) * 100.0, 6)
        END
        RETURN count(r) AS updated
        """,
        run_token=run_token,
        period_key=period,
    ).single()
    return int((rec or {}).get("updated") or 0)


def _phase7_delete_invalid_snapshot_rows(
    session,
    period_key: str,
) -> int:
    """
    Remove obviously invalid holdings rows for one filing period.

    Zero-share or nonpositive-value rows are not valid 13F positions and should not
    remain in the graph as either open or closed relationships.
    """
    if not period_key:
        return 0
    total_deleted = 0
    while True:
        rec = session.run(
            """
            MATCH (:Institution)-[r:HOLDS {source_scope: '13F_HR', filing_period: $period_key}]->(:Company)
            WHERE coalesce(r.shares, 0) <= 0 OR coalesce(r.value_usd, 0) <= 0
            WITH DISTINCT r LIMIT $batch_limit
            DELETE r
            RETURN count(r) AS deleted
            """,
            period_key=period_key,
            batch_limit=PHASE7_CLOSE_INTERVAL_BATCH_SIZE,
        ).single()
        deleted_now = int((rec or {}).get("deleted") or 0)
        total_deleted += deleted_now
        if deleted_now < PHASE7_CLOSE_INTERVAL_BATCH_SIZE:
            break
    return total_deleted


def _phase7_batched_holds_update(
    session,
    *,
    where_sql: str,
    set_sql: str,
    result_alias: str,
    log_label: str,
    extra_params: dict | None = None,
    batch_limit: int | None = None,
    min_batch_limit: int | None = None,
    expected_total: int | None = None,
    progress_every: int = 50000,
) -> int:
    total_changed = 0
    current_batch_limit = max(100, int(batch_limit or PHASE7_INFLATION_FIX_BATCH_SIZE))
    current_min_batch_limit = max(
        100,
        min(current_batch_limit, int(min_batch_limit or PHASE7_INFLATION_FIX_MIN_BATCH_SIZE)),
    )
    params = dict(extra_params or {})
    next_progress_log = max(1, int(progress_every))

    if expected_total:
        _log(
            f"Phase 7: {log_label} starting with batch size {current_batch_limit:,} for about {int(expected_total):,} edge(s)...",
            "cyan",
        )
    else:
        _log(
            f"Phase 7: {log_label} starting with batch size {current_batch_limit:,}...",
            "cyan",
        )

    while True:
        try:
            rec = session.run(
                f"""
                MATCH ()-[r:HOLDS {{source_scope: '13F_HR'}}]->()
                WHERE {where_sql}
                WITH DISTINCT r LIMIT $batch_limit
                {set_sql}
                RETURN count(r) AS {result_alias}
                """,
                **params,
                batch_limit=current_batch_limit,
            ).single()
        except Exception as exc:
            if _neo4j_is_memory_error(exc) and current_batch_limit > current_min_batch_limit:
                next_batch_limit = max(current_min_batch_limit, current_batch_limit // 2)
                if next_batch_limit < current_batch_limit:
                    _log(
                        f"Phase 7: {log_label} hit Neo4j transaction memory limits at batch size "
                        f"{current_batch_limit:,}; retrying with {next_batch_limit:,}.",
                        "yellow",
                    )
                    current_batch_limit = next_batch_limit
                    continue
            raise

        changed = int((rec or {}).get(result_alias) or 0)
        total_changed += changed
        while total_changed >= next_progress_log and changed > 0:
            if expected_total:
                pct = min(100.0, (total_changed / max(int(expected_total), 1)) * 100.0)
                _log(
                    f"Phase 7: {log_label} progressed to {total_changed:,}/{int(expected_total):,} edge(s) ({pct:.1f}%).",
                    "white",
                )
            else:
                _log(
                    f"Phase 7: {log_label} progressed to {total_changed:,} edge(s).",
                    "white",
                )
            next_progress_log += max(1, int(progress_every))
        if changed == 0 or changed < current_batch_limit:
            break
    return total_changed


_PHASE7_DEFAULT_IMPLIED_PRICE_OUTLIER_THRESHOLD = 100000.0
_PHASE7_HIGH_PRICE_TICKER_OUTLIER_THRESHOLDS = {
    "BRK.A": 10000000.0,
    "BRK/A": 10000000.0,
}


def _phase7_implied_price_outlier_threshold(ticker: str) -> float:
    normalized = str(ticker or "").strip().upper()
    return float(
        _PHASE7_HIGH_PRICE_TICKER_OUTLIER_THRESHOLDS.get(
            normalized,
            _PHASE7_DEFAULT_IMPLIED_PRICE_OUTLIER_THRESHOLD,
        )
    )


def _phase7_holds_value_looks_1000x_inflated(ticker: str, shares, value_usd) -> bool:
    try:
        shares_value = float(shares or 0.0)
        value = float(value_usd or 0.0)
    except Exception:
        return False
    if shares_value <= 0.0 or value <= 0.0:
        return False
    threshold = _phase7_implied_price_outlier_threshold(ticker)
    implied_price = value / shares_value
    if implied_price <= threshold:
        return False
    corrected_implied_price = (value / 1000.0) / shares_value
    return corrected_implied_price <= threshold


def _phase7_normalize_resolved_holding_value_usd(ticker: str, shares, value_usd) -> tuple[float, bool]:
    try:
        normalized_value = float(value_usd or 0.0)
    except Exception:
        return 0.0, False
    if _phase7_holds_value_looks_1000x_inflated(ticker, shares, normalized_value):
        return normalized_value / 1000.0, True
    return normalized_value, False


def _phase7_implied_price_outlier_threshold_case(rel_alias: str = "r") -> str:
    high_price_tickers = ", ".join(
        f"'{ticker}'" for ticker in sorted(_PHASE7_HIGH_PRICE_TICKER_OUTLIER_THRESHOLDS)
    )
    brka_threshold = int(_PHASE7_HIGH_PRICE_TICKER_OUTLIER_THRESHOLDS["BRK.A"])
    default_threshold = int(_PHASE7_DEFAULT_IMPLIED_PRICE_OUTLIER_THRESHOLD)
    return (
        f"(CASE WHEN coalesce({rel_alias}.target_ticker, '') IN [{high_price_tickers}] "
        f"THEN {brka_threshold} ELSE {default_threshold} END)"
    )


def _phase7_implied_price_outlier_predicate(rel_alias: str = "r") -> str:
    threshold_case = _phase7_implied_price_outlier_threshold_case(rel_alias)
    return f"toFloat({rel_alias}.value_usd) / toFloat({rel_alias}.shares) > {threshold_case}"


def _phase7_correctable_1000x_inflation_predicate(rel_alias: str = "r") -> str:
    threshold_case = _phase7_implied_price_outlier_threshold_case(rel_alias)
    return (
        f"{_phase7_implied_price_outlier_predicate(rel_alias)} "
        f"AND (toFloat({rel_alias}.value_usd) / 1000.0) / toFloat({rel_alias}.shares) <= {threshold_case}"
    )


def _phase7_bulk_correct_1000x_inflation(session, run_token: str, expected_total: int | None = None) -> int:
    return _phase7_batched_holds_update(
        session,
        where_sql=f"""
            coalesce(r.current_run_token, '') = $run_token
            AND r.shares > 0
            AND {_phase7_correctable_1000x_inflation_predicate("r")}
        """,
        set_sql="""
            SET r.value_usd = toFloat(r.value_usd) / 1000.0,
                r.corrected_from_1000x_inflation = true,
                r.data_quality_flag = NULL,
                r.implied_price_per_share = NULL,
                r.last_quality_checked = toString(date())
        """,
        result_alias="fixed",
        log_label="bulk 1000x inflation correction",
        extra_params={"run_token": run_token},
        expected_total=expected_total,
    )


def _phase7_repair_and_flag_implied_price_outliers(session, run_token: str) -> dict[str, int]:
    """
    Clean up residual 13F price outliers after the bulk 1000x correction.
    """
    result = {"target_fixed": 0, "remaining": 0, "flagged": 0}
    result["target_fixed"] = _phase7_batched_holds_update(
        session,
        where_sql=f"""
            coalesce(r.current_run_token, '') = $run_token
            AND r.shares > 0
            AND {_phase7_correctable_1000x_inflation_predicate("r")}
        """,
        set_sql="""
            SET r.value_usd = toFloat(r.value_usd) / 1000.0,
                r.corrected_from_1000x_inflation = true,
                r.data_quality_flag = NULL,
                r.implied_price_per_share = NULL,
                r.last_quality_checked = toString(date())
        """,
        result_alias="fixed",
        log_label="residual 1000x inflation correction",
        extra_params={"run_token": run_token},
    )

    _phase7_batched_holds_update(
        session,
        where_sql=f"""
            coalesce(r.current_run_token, '') = $run_token
            AND r.shares > 0
            AND NOT ({_phase7_implied_price_outlier_predicate("r")})
            AND (r.data_quality_flag IS NOT NULL OR r.implied_price_per_share IS NOT NULL)
        """,
        set_sql="""
            SET r.data_quality_flag = NULL,
                r.implied_price_per_share = NULL,
                r.last_quality_checked = toString(date())
        """,
        result_alias="cleared",
        log_label="clear resolved implied-price flags",
        extra_params={"run_token": run_token},
    )

    result["flagged"] = _phase7_batched_holds_update(
        session,
        where_sql=f"""
            coalesce(r.current_run_token, '') = $run_token
            AND r.shares > 0
            AND {_phase7_implied_price_outlier_predicate("r")}
        """,
        set_sql="""
            SET r.data_quality_flag = 'implied_price_outlier',
                r.implied_price_per_share = round(toFloat(r.value_usd) / toFloat(r.shares), 2),
                r.last_quality_checked = toString(date())
        """,
        result_alias="flagged",
        log_label="flag implied-price outliers",
        extra_params={"run_token": run_token},
    )
    result["remaining"] = result["flagged"]
    return result


def phase7_13f_ownership(session):
    """
    Phase 7: SEC 13F institutional ownership.
    Creates Institution nodes and HOLDS edges to Company.

    Fixes applied vs original:
    - Name resolution is done in Python using a pre-built normalized map (no per-edge Neo4j queries).
    - All Neo4j writes are batched (one round-trip per 200 edges instead of one per edge).
    - Value scale follows the SEC 2023 cutover: pre-2023 13F values are in thousands,
      while 2023+ filings report values in dollars.
    - Name matching uses a stricter normalized-exact + suffix-stripped approach instead of
      Neo4j CONTAINS (which was too loose — "First" matched "First Solar", "First Republic", etc.).
    """
    _nexus_stage_reset()
    _progress(PHASE_PCT[9], "Phase 7: SEC 13F institutional ownership...", "cyan")
    try:
        sec_rows = _fetch_sec_company_ticker_rows()
        stats = _revalidate_company_ciks_against_sec(session, sec_rows=sec_rows)
        if stats["cleared"]:
            _log(
                "Phase 7: removed "
                f"{stats['cleared']} ticker/CIK mismatches before 13F mapping.",
                "yellow",
            )
    except Exception as e:
        _log_stage_error("Phase 7: 13F ownership", "revalidate SEC ticker identities", str(e), e, color="yellow")
    valid_c = _cypher_valid_company_listing_predicate("c")
    r = session.run(f"MATCH (c:Company) WHERE c.cik IS NOT NULL AND {valid_c} RETURN c.cik AS cik, c.ticker AS ticker")
    cik_to_ticker = {rec["cik"]: rec["ticker"] for rec in r if rec.get("cik") and rec.get("ticker")}

    history_start_date = _nexus_historical_start_date()
    phase_manifest = _nexus_phase_manifest("phase7")
    history_quarters = (
        _clamp_phase7_history_quarters(_quarters_between(history_start_date))
        if history_start_date else _PHASE7_HISTORY_QUARTERS[0]
    )
    ignore_existing_coverage = _phase7_should_ignore_existing_coverage(
        session,
        phase_manifest,
        history_start_date,
    )
    if ignore_existing_coverage and history_start_date:
        _log(
            "Phase 7: bootstrap coverage is incomplete in the current graph; rebuilding historical 13F quarters from cached ZIP snapshots.",
            "white",
        )
    headers, snapshot_specs = _fetch_13f_holding_snapshot_specs(
        history_quarters,
        historical_start_date=history_start_date,
        phase_manifest=phase_manifest,
        ignore_existing_coverage=ignore_existing_coverage,
    )
    if not snapshot_specs:
        if history_start_date and phase_manifest.get("coverage_end"):
            _nexus_update_phase_manifest(
                "phase7",
                mode="snapshot_history",
                coverage_start=history_start_date,
                coverage_end=phase_manifest.get("coverage_end"),
                last_incremental_start=phase_manifest.get("coverage_end"),
                last_incremental_end=phase_manifest.get("coverage_end"),
                bootstrap_complete=bool(phase_manifest.get("bootstrap_complete")),
                extra={
                    "history_quarters": history_quarters,
                    "processed_snapshots": int(phase_manifest.get("processed_snapshots") or 0),
                    "last_period_key": phase_manifest.get("last_period_key"),
                },
            )
            _log("Phase 7: no new 13F quarters beyond existing historical coverage.", "white")
            _progress(PHASE_PCT[10], "Phase 7 skipped (no new 13F quarter data).", "green")
            return
        _log_stage_unexpected("Phase 7: 13F ownership", "fetch 13F holdings", "No 13F holdings extracted.")
        _progress(PHASE_PCT[10], "Phase 7 skipped (no 13F data).", "green")
        return
    _log(
        f"Phase 7: processing {len(snapshot_specs)} 13F snapshot(s) with history window {history_quarters} quarter(s).",
        "cyan",
    )
    snapshot_valid_until_dates = _phase7_snapshot_valid_until_dates(snapshot_specs)

    # Build a normalised name → ticker lookup from the graph for resolution in Python.
    # This avoids per-edge Neo4j CONTAINS queries.
    import re as _re
    _suffix_re = _re.compile(r'\s*(inc\.?|corp\.?|llc\.?|ltd\.?|co\.?\s*$|plc\.?\s*$)', _re.I)

    def _norm_name(s: str) -> str:
        return _suffix_re.sub("", s.strip()).strip().lower()

    name_r = session.run(f"MATCH (c:Company) WHERE c.name IS NOT NULL AND {valid_c} RETURN c.name AS name, c.ticker AS ticker")
    name_to_ticker: dict[str, str] = {}
    for rec in name_r:
        raw = rec.get("name") or ""
        tk  = rec.get("ticker") or ""
        if raw and tk:
            name_to_ticker[raw.lower()]    = tk
            name_to_ticker[_norm_name(raw)] = tk

    issuer_resolution_cache: dict[str, str | None] = {}

    def _resolve_issuer(name_of_issuer: str) -> str | None:
        """Resolve 13F issuer name to ticker using Python-side normalized exact match only.
        Strict: requires the normalized name to match exactly, preventing 'Ford' matching
        'Affordability Corp'. Minimum 5-char requirement prevents spurious short matches."""
        raw_name = str(name_of_issuer or "")
        if not raw_name or len(raw_name) < 5:
            return None
        if raw_name in issuer_resolution_cache:
            return issuer_resolution_cache[raw_name]
        exact = name_to_ticker.get(raw_name.lower())
        if exact:
            issuer_resolution_cache[raw_name] = exact
            return exact
        normed = _norm_name(raw_name)
        if len(normed) >= 5:
            resolved = name_to_ticker.get(normed)
            issuer_resolution_cache[raw_name] = resolved
            return resolved
        issuer_resolution_cache[raw_name] = None
        return None

    # Batch all edges; resolve issuer name → ticker in Python first
    batch_size = PHASE7_HOLDS_WRITE_BATCH_SIZE
    created = 0
    closed = 0
    phase_run_token = f"phase7:{uuid4().hex}"

    def _flush_13f_batch_v2(params: list[dict]) -> int:
        if not params:
            return 0
        session.run(
            """
            UNWIND $batch AS p
            MERGE (inst:Institution {id: p.inst_id})
            SET inst.name = p.inst_name
            WITH inst, p
            MATCH (c:Company {ticker: p.ticker})
            WHERE """ + valid_c + """
            MERGE (inst)-[r:HOLDS {source_scope: '13F_HR', filing_period: p.period_key}]->(c)
            ON CREATE SET r.value_usd = p.val,
                          r.shares = p.shares,
                          r.weight_pct = CASE WHEN p.weight_pct IS NULL THEN NULL ELSE p.weight_pct END,
                          r.corrected_from_1000x_inflation = CASE WHEN p.corrected_1000x THEN true ELSE false END,
                          r.source = '13F-HR',
                          r.confidence = 0.90,
                          r.active_after = p.active_after,
                          r.target_ticker = p.ticker,
                          r.institution_id = p.inst_id,
                          r.valid_until = CASE WHEN p.valid_until = '' THEN NULL ELSE p.valid_until END,
                          r.retired_reason = CASE WHEN p.valid_until = '' THEN NULL ELSE 'superseded' END,
                          r.closed_at = CASE WHEN p.valid_until = '' THEN NULL ELSE p.valid_until END,
                          r.edge_state = CASE WHEN p.valid_until = '' THEN 'open' ELSE 'closed' END
            ON MATCH  SET r.value_usd = p.val,
                          r.shares = p.shares,
                          r.weight_pct = CASE WHEN p.weight_pct IS NULL THEN r.weight_pct ELSE p.weight_pct END,
                          r.active_after = CASE
                              WHEN p.active_after IS NULL OR p.active_after = '' THEN r.active_after
                              WHEN r.active_after IS NULL OR r.active_after = '' OR p.active_after < r.active_after THEN p.active_after
                              ELSE r.active_after
                          END,
                          r.corrected_from_1000x_inflation = CASE WHEN p.corrected_1000x THEN true ELSE false END,
                          r.target_ticker = p.ticker,
                          r.institution_id = p.inst_id,
                          r.valid_until = CASE WHEN p.valid_until = '' THEN NULL ELSE p.valid_until END,
                          r.retired_reason = CASE WHEN p.valid_until = '' THEN NULL ELSE 'superseded' END,
                          r.closed_at = CASE WHEN p.valid_until = '' THEN NULL ELSE p.valid_until END,
                          r.edge_state = CASE WHEN p.valid_until = '' THEN 'open' ELSE 'closed' END
            SET r.current_run_token = $run_token,
                r.filing_period = p.period_key,
                r.data_quality_flag = NULL,
                r.implied_price_per_share = NULL,
                r.last_quality_checked = toString(date()),
                r.last_confirmed = CASE
                    WHEN p.snapshot_active_after IS NULL OR p.snapshot_active_after = '' THEN coalesce(r.last_confirmed, p.active_after)
                    ELSE p.snapshot_active_after
                END
            """,
            batch=params,
            run_token=phase_run_token,
        ).consume()
        return len(params)

    def _close_stale_period_edges_v2(period_key: str, snapshot_active_after: str) -> int:
        if not period_key:
            return 0
        total_closed = 0
        while True:
            rec = session.run("""
                MATCH (:Institution)-[r:HOLDS {source_scope: '13F_HR', filing_period: $period_key}]->(:Company)
                WHERE coalesce(r.current_run_token, '') <> $run_token
                  AND (
                        coalesce(r.edge_state, 'open') <> 'closed'
                     OR coalesce(r.retired_reason, '') <> 'entity_correction'
                     OR r.valid_until IS NULL
                     OR r.valid_until = ''
                  )
                WITH DISTINCT r LIMIT $batch_limit
                SET r.edge_state = 'closed',
                    r.current_run_token = $run_token,
                    r.valid_until = CASE
                        WHEN r.active_after IS NOT NULL AND r.active_after <> '' THEN r.active_after
                        WHEN $snapshot_active_after = '' THEN coalesce(r.valid_until, r.active_after)
                        ELSE $snapshot_active_after
                    END,
                    r.retired_reason = 'entity_correction',
                    r.closed_at = CASE
                        WHEN r.active_after IS NOT NULL AND r.active_after <> '' THEN r.active_after
                        WHEN $snapshot_active_after = '' THEN r.closed_at
                        ELSE $snapshot_active_after
                    END
                RETURN count(r) AS closed
            """,
                period_key=period_key,
                run_token=phase_run_token,
                snapshot_active_after=snapshot_active_after,
                batch_limit=PHASE7_CLOSE_INTERVAL_BATCH_SIZE,
            ).single()
            closed_now = int((rec or {}).get("closed") or 0)
            total_closed += closed_now
            if closed_now < PHASE7_CLOSE_INTERVAL_BATCH_SIZE:
                break
        return total_closed

    _p7_conn = _nexus_rethink_conn
    _total_snapshots = len(snapshot_specs)
    processed_snapshot_dates: list[str] = []
    last_period_key = ""
    for _snap_idx, snapshot_spec in enumerate(snapshot_specs):
        snapshot = _fetch_13f_holdings_snapshot_from_spec(cik_to_ticker, headers, snapshot_spec)
        if not snapshot or not snapshot.get("edges"):
            if str((snapshot_spec or {}).get("fetch_mode") or "").lower() == "atom":
                _log_stage_unexpected("Phase 7: 13F ownership", "fetch 13F holdings", "No 13F holdings extracted.")
            else:
                _log(
                    f"Phase 7: {snapshot_spec.get('label') or snapshot_spec.get('period_key') or '13F snapshot'} produced no holdings; skipping.",
                    "yellow",
                )
            continue
        snapshot_label = str(snapshot.get("label") or snapshot.get("period_key") or "13F snapshot")
        snapshot_period_key = str(snapshot.get("period_key") or _phase7_13f_atom_period_key(snapshot.get("active_after") or ""))
        snapshot_active_after = _normalize_edge_date(snapshot.get("active_after"))
        snapshot_valid_until = snapshot_valid_until_dates[_snap_idx] if _snap_idx < len(snapshot_valid_until_dates) else ""
        snapshot_edges = snapshot.get("edges") or []
        snapshot_total_edges = len(snapshot_edges)
        snapshot_value_totals = _phase7_snapshot_resolved_value_totals(
            snapshot_edges,
            snapshot_period_key,
            _resolve_issuer,
        )
        batch_params_v2: list[dict] = []
        snapshot_created = 0
        next_examined_log = 250000
        next_merged_log = 100000

        _log(
            f"Phase 7: {snapshot_label} writing {snapshot_total_edges:,} raw holdings rows into Neo4j interval batches...",
            "cyan",
        )

        for i, edge in enumerate(snapshot_edges):
            inst_id = edge["node_a"]
            props = edge.get("properties") or {}
            inst_name = props.get("institution_name", inst_id)
            issuer_name = props.get("name_of_issuer", "")
            raw_val = props.get("value_usd", 0)
            try:
                value_usd = float(raw_val) if raw_val else 0.0
            except Exception:
                value_usd = 0.0
            try:
                shares = int(float(props.get("shares", 0) or 0))
            except Exception:
                shares = 0
            if value_usd <= 0 or shares <= 0:
                continue
            company_ticker = _resolve_issuer(issuer_name)
            if not company_ticker:
                continue
            value_usd, corrected_1000x = _phase7_normalize_resolved_holding_value_usd(
                company_ticker,
                shares,
                value_usd,
            )
            if value_usd <= 0:
                continue

            edge_active_after = _normalize_edge_date(props.get("active_after")) or snapshot_active_after
            period_key = str(props.get("filing_period") or snapshot_period_key)
            weight_pct = _phase7_snapshot_weight_pct(inst_id, period_key, value_usd, snapshot_value_totals)
            batch_params_v2.append({
                "inst_id": inst_id,
                "inst_name": inst_name,
                "ticker": company_ticker,
                "val": value_usd,
                "shares": shares,
                "weight_pct": weight_pct,
                "active_after": edge_active_after,
                "snapshot_active_after": snapshot_active_after or edge_active_after,
                "period_key": period_key,
                "valid_until": snapshot_valid_until,
                "corrected_1000x": bool(corrected_1000x),
            })
            if len(batch_params_v2) >= batch_size:
                snapshot_created += _flush_13f_batch_v2(batch_params_v2)
                batch_params_v2 = []
                while snapshot_created >= next_merged_log:
                    _log(
                        f"Phase 7: {snapshot_label} merged {snapshot_created:,} Neo4j HOLDS rows so far...",
                        "white",
                    )
                    next_merged_log += 100000
            if (i + 1) % 500 == 0:
                _nexus_maybe_log_eta()
                while (i + 1) >= next_examined_log:
                    _log(
                        f"Phase 7: {snapshot_label} examined {(i + 1):,}/{snapshot_total_edges:,} holdings rows; "
                        f"{snapshot_created:,} merged so far.",
                        "white",
                    )
                    next_examined_log += 250000

        snapshot_created += _flush_13f_batch_v2(batch_params_v2)
        while snapshot_created >= next_merged_log:
            _log(
                f"Phase 7: {snapshot_label} merged {snapshot_created:,} Neo4j HOLDS rows so far...",
                "white",
            )
            next_merged_log += 100000
        if snapshot_created == 0:
            _log(f"Phase 7: {snapshot_label} produced no resolvable company holdings; skipping cleanup.", "yellow")
            continue

        _log(
            f"Phase 7: {snapshot_label} merged {snapshot_created:,} HOLDS rows; pruning invalid rows for {snapshot_period_key}...",
            "cyan",
        )
        invalid_deleted = _phase7_delete_invalid_snapshot_rows(session, snapshot_period_key)
        created += snapshot_created
        _log(f"Phase 7: {snapshot_label} reconciling stale edges for filing period {snapshot_period_key}...", "cyan")
        closed += _close_stale_period_edges_v2(snapshot_period_key, snapshot_active_after)
        _log(f"Phase 7: {snapshot_label} merged {snapshot_created:,} HOLDS intervals.", "green")
        if invalid_deleted:
            _log(
                f"Phase 7: {snapshot_label} removed {invalid_deleted:,} invalid HOLDS row(s) "
                f"with nonpositive shares/value for {snapshot_period_key}.",
                "yellow",
            )

        # Per-snapshot resume checkpoint: persist manifest to RethinkDB so that if the
        # process is interrupted, the next run skips already-completed snapshots.
        if snapshot_active_after and _nexus_historical_enabled():
            _nexus_update_phase_manifest(
                "phase7",
                last_incremental_end=snapshot_active_after,
                extra={
                    "processed_snapshots": _snap_idx + 1,
                    "last_period_key": snapshot_period_key,
                    "edge_count": created,
                },
            )
            if _p7_conn:
                _persist_nexus_temporal_state(_p7_conn)

        # Live progress update for the UI
        if _p7_conn:
            _phase7_report_progress(
                _p7_conn,
                _snap_idx + 1,
                _total_snapshots,
                f"{snapshot_label} — {created:,} HOLDS edges so far",
            )
        if snapshot_active_after:
            processed_snapshot_dates.append(snapshot_active_after)
        if snapshot_period_key:
            last_period_key = snapshot_period_key
        del snapshot

    _log(f"Phase 7 done: {created} institutional HOLDS edges created.", "green")
    if closed:
        _log(f"Phase 7: closed {closed} superseded 13F HOLDS interval(s).", "green")

    # Current interval set is complete; keep the legacy Phase 7 body below unreachable.
    batch_params: list[dict] = []
    try:
        stale_check = session.run(f"""
            MATCH ()-[r:HOLDS {{source_scope: '13F_HR'}}]->()
            WHERE coalesce(r.current_run_token, '') = $run_token
              AND r.shares > 0
              AND {_phase7_implied_price_outlier_predicate("r")}
            RETURN count(r) AS stale_count
        """, run_token=phase_run_token).single()
        total_check = session.run("""
            MATCH ()-[r:HOLDS {source_scope: '13F_HR'}]->()
            WHERE coalesce(r.current_run_token, '') = $run_token
              AND r.shares > 0
            RETURN count(r) AS total
        """, run_token=phase_run_token).single()
        stale_n = stale_check["stale_count"] if stale_check else 0
        total_n = total_check["total"] if total_check else 1
        if stale_n > 0 and stale_n / max(total_n, 1) > 0.05:
            _log(
                f"Phase 7: Detected {stale_n:,}/{total_n:,} HOLDS edges with implied-price outliers "
                "consistent with 1000x inflation - auto-correcting...",
                "yellow",
            )
            fixed_n = _phase7_bulk_correct_1000x_inflation(session, phase_run_token, expected_total=stale_n)
            _log(f"Phase 7: Auto-corrected value_usd on {fixed_n:,} HOLDS edges.", "green")
            quality_result = _phase7_repair_and_flag_implied_price_outliers(session, phase_run_token)
            if quality_result["target_fixed"] > 0:
                _log(
                    f"Phase 7: Auto-corrected {quality_result['target_fixed']:,} additional HOLDS edge(s) "
                    "with likely residual 1000x inflation.",
                    "green",
                )
            remaining = quality_result["remaining"]
            if remaining > 0:
                _log(
                    f"Phase 7 WARNING: {remaining:,} HOLDS edges still show implied-price outliers "
                    "after auto-correction - flagged with data_quality_flag=implied_price_outlier.",
                    "yellow",
                )
            else:
                _log("Phase 7: Inflation check passed after auto-correction.", "green")
        elif stale_n == 0:
            _log("Phase 7: Inflation check passed - all implied prices look reasonable.", "green")
        else:
            quality_result = _phase7_repair_and_flag_implied_price_outliers(session, phase_run_token)
            if quality_result["remaining"] > 0:
                _log(
                    f"Phase 7 WARNING: {quality_result['remaining']:,} HOLDS edges were flagged with "
                    "data_quality_flag=implied_price_outlier.",
                    "yellow",
                )
    except Exception as _e:
        _log(f"Phase 7: Inflation check error: {_e}", "yellow")

    try:
        _sync_graph_edge_intervals(
            session,
            "HOLDS",
            source_scope="13F_HR",
            directed=True,
            run_token=phase_run_token,
            batch_limit=PHASE7_HOLDS_INTERVAL_SYNC_BATCH_SIZE,
            log_label="Phase 7: HOLDS interval history sync",
            expected_total=created,
            progress_every=PHASE7_HOLDS_INTERVAL_SYNC_PROGRESS_EVERY,
            consume_run_token=True,
        )
    except Exception as e:
        _log_stage_error("Phase 7: 13F ownership", "sync HOLDS interval history", str(e), e, color="yellow")
    try:
        retired_legacy = _retire_relationships(
            session,
            "HOLDS",
            "coalesce(r.source_scope, '') = ''",
            directed=True,
            retired_reason="legacy_replaced",
        )
        if retired_legacy:
            _log(f"Phase 7: retired {retired_legacy} legacy HOLDS edge(s) after replacement.", "green")
    except Exception as e:
        _log_stage_error("Phase 7: 13F ownership", "retire legacy HOLDS", str(e), e, color="yellow")
    snapshot_dates = sorted(
        {
            _normalize_historical_iso_date(active_after)
            for active_after in processed_snapshot_dates
            if _normalize_historical_iso_date(active_after)
        }
    )
    if snapshot_dates:
        coverage_start = history_start_date or snapshot_dates[0]
        coverage_end = snapshot_dates[-1]
        _nexus_update_phase_manifest(
            "phase7",
            mode="snapshot_history",
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            last_incremental_start=snapshot_dates[0],
            last_incremental_end=coverage_end,
            bootstrap_complete=bool(history_start_date),
            extra={
                "history_quarters": history_quarters,
                "processed_snapshots": len(snapshot_dates),
                "last_period_key": last_period_key,
            },
        )
    _progress(PHASE_PCT[10], f"Phase 7 done: {created} 13F HOLDS edges.", "green")
    return
    batch_size = 200
    created = 0
    closed = 0
    phase_run_token = f"phase7:{uuid4().hex}"
    phase_close_date = ""

    def _flush_13f_batch(params: list[dict]) -> int:
        if not params:
            return 0
        session.run(
            """
            UNWIND $batch AS p
            MERGE (inst:Institution {id: p.inst_id})
            SET inst.name = p.inst_name
            WITH inst, p
            MATCH (c:Company {ticker: p.ticker})
            WHERE """ + valid_c + """
            MERGE (inst)-[r:HOLDS {source_scope: '13F_HR', edge_state: 'open'}]->(c)
            ON CREATE SET r.value_usd = p.val, r.shares = p.shares,
                          r.source = '13F-HR', r.confidence = 0.90, r.active_after = p.active_after,
                          r.valid_until = NULL, r.retired_reason = NULL
            ON MATCH  SET r.value_usd = p.val, r.shares = p.shares,
                          r.active_after = CASE
                              WHEN p.active_after IS NULL OR p.active_after = '' THEN r.active_after
                              WHEN r.active_after IS NULL OR r.active_after = '' OR p.active_after < r.active_after THEN p.active_after
                              ELSE r.active_after
                          END,
                          r.valid_until = NULL,
                          r.retired_reason = NULL,
                          r.edge_state = 'open',
                          r.current_run_token = $run_token
            SET r.current_run_token = $run_token
            """,
            batch=params,
            run_token=phase_run_token,
        ).consume()
        return len(params)

    for i, e in enumerate(edges):
        inst_id     = e["node_a"]
        props       = e.get("properties") or {}
        inst_name   = props.get("institution_name", inst_id)
        issuer_name = props.get("name_of_issuer", "")
        # 13F XML reports values in thousands — ensure consistent scaling regardless of data path
        raw_val     = props.get("value_usd", 0)
        # The Atom-feed path already multiplied by 1000; the ZIP fallback did not.
        # We normalise here: if value looks like it came from ZIP (small), scale it up.
        # In practice the EdgeResult already carries the correct value from the fetch functions,
        # but we re-apply a consistent minimum sanity check.
        value_usd   = float(raw_val) if raw_val else 0.0
        shares      = int(props.get("shares", 0))

        company_ticker = _resolve_issuer(issuer_name)
        if not company_ticker:
            continue

        batch_params.append({
            "inst_id":   inst_id,
            "inst_name": inst_name,
            "ticker":    company_ticker,
            "val":       value_usd,
            "shares":    shares,
            "active_after": _normalize_edge_date(props.get("active_after")),
        })
        if not phase_close_date:
            phase_close_date = _normalize_edge_date(props.get("active_after"))
        elif _normalize_edge_date(props.get("active_after")):
            phase_close_date = max(phase_close_date, _normalize_edge_date(props.get("active_after")))
        if len(batch_params) >= batch_size:
            created += _flush_13f_batch(batch_params)
            batch_params = []
        if (i + 1) % 500 == 0:
            _nexus_maybe_log_eta()

    created += _flush_13f_batch(batch_params)
    if created > 0:
        close_date = phase_close_date or datetime.now(timezone.utc).date().isoformat()
        rec = session.run("""
            MATCH (:Institution)-[r:HOLDS {source_scope: '13F_HR', edge_state: 'open'}]->(:Company)
            WHERE coalesce(r.current_run_token, '') <> $run_token
            SET r.edge_state = 'closed',
                r.valid_until = CASE
                    WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                    WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                    ELSE $close_date
                END,
                r.retired_reason = 'superseded',
                r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
                r.current_run_token = NULL
            RETURN count(r) AS closed
        """, run_token=phase_run_token, close_date=close_date).single()
        closed = int((rec or {}).get("closed") or 0)
    _log(f"Phase 7 done: {created} institutional HOLDS edges created.", "green")
    if closed:
        _log(f"Phase 7: closed {closed} superseded 13F HOLDS interval(s).", "green")

    # ── Sanity-check + auto-correct 1000x inflation ────────────────────────────
    # Implied price = value_usd / shares.  BRK.A (~$600K) legitimately exceeds $50K;
    # nothing else in the S&P 500 comes close.  Use $100K threshold to avoid false
    # positives even if stock prices surge dramatically.
    # If >5% of edges are inflated, auto-correct by dividing value_usd by 1000.
    try:
        stale_check = session.run(f"""
            MATCH (inst:Institution)-[r:HOLDS]->(c:Company)
            WHERE r.shares > 0 AND {_phase7_implied_price_outlier_predicate("r")}
            RETURN count(r) AS stale_count
        """).single()
        total_check = session.run("""
            MATCH ()-[r:HOLDS]->()
            WHERE r.shares > 0
            RETURN count(r) AS total
        """).single()
        stale_n = stale_check["stale_count"] if stale_check else 0
        total_n = total_check["total"] if total_check else 1
        if stale_n > 0 and stale_n / max(total_n, 1) > 0.05:
            _log(
                f"Phase 7: Detected {stale_n:,}/{total_n:,} HOLDS edges with implied-price outliers "
                "consistent with 1000x inflation — auto-correcting...",
                "yellow",
            )
            # Divide value_usd by 1000 for all affected edges
            fix_result = session.run(f"""
                MATCH (inst:Institution)-[r:HOLDS]->(c:Company)
                WHERE r.shares > 0 AND {_phase7_correctable_1000x_inflation_predicate("r")}
                SET r.value_usd = toFloat(r.value_usd) / 1000.0
                RETURN count(r) AS fixed
            """).single()
            fixed_n = fix_result["fixed"] if fix_result else 0
            _log(f"Phase 7: Auto-corrected value_usd on {fixed_n:,} HOLDS edges.", "green")
            # Verify correction worked
            verify = session.run(f"""
                MATCH (inst:Institution)-[r:HOLDS]->(c:Company)
                WHERE r.shares > 0 AND {_phase7_implied_price_outlier_predicate("r")}
                RETURN count(r) AS remaining
            """).single()
            remaining = verify["remaining"] if verify else 0
            if remaining > 0:
                _log(
                    f"Phase 7 WARNING: {remaining:,} HOLDS edges still show implied-price outliers "
                    "after auto-correction — may require manual inspection.",
                    "yellow",
                )
            else:
                _log("Phase 7: Inflation check passed after auto-correction.", "green")
        elif stale_n == 0:
            _log("Phase 7: Inflation check passed — all implied prices look reasonable.", "green")
    except Exception as _e:
        _log(f"Phase 7: Inflation check error: {_e}", "yellow")

    try:
        _sync_graph_edge_intervals(
            session,
            "HOLDS",
            source_scope="13F_HR",
            directed=True,
            batch_limit=PHASE7_HOLDS_INTERVAL_SYNC_BATCH_SIZE,
        )
    except Exception as e:
        _log_stage_error("Phase 7: 13F ownership", "sync HOLDS interval history", str(e), e, color="yellow")
    try:
        retired_legacy = _retire_relationships(
            session,
            "HOLDS",
            "coalesce(r.source_scope, '') = ''",
            directed=True,
            retired_reason="legacy_replaced",
        )
        if retired_legacy:
            _log(f"Phase 7: retired {retired_legacy} legacy HOLDS edge(s) after replacement.", "green")
    except Exception as e:
        _log_stage_error("Phase 7: 13F ownership", "retire legacy HOLDS", str(e), e, color="yellow")

    _progress(PHASE_PCT[10], f"Phase 7 done: {created} 13F HOLDS edges.", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 8: USASpending.gov government contracts
# ──────────────────────────────────────────────────────────────────────────────
# Resources: USASpending API v2
#   POST https://api.usaspending.gov/api/v2/search/spending_by_award/
#   Body: filters (award_type_codes A,B,C,D; time_period), fields, sort, order, limit (max 100 per request), page.
#   We paginate to collect up to USASPENDING_MAX_ROWS (default 500k) for resolution across 6000+ companies.
# Flow: Fetch awards (with progress) → resolve Recipient Name to Company ticker → batch-write CONTRACTS_WITH with progress.
# ──────────────────────────────────────────────────────────────────────────────
def _resolve_usaspending_to_edges(
    results: list,
    ticker_to_name: dict[str, str],
    legal_entity_bridge: dict[str, dict] | None = None,
    llm_provider: str = "",
    llm_model: str = "",
    llm_api_key: str = "",
) -> list[EdgeResult]:
    """Resolve raw USASpending results to Company CONTRACTS_WITH GovAgency edges. No API calls."""
    import re
    edges_by_key: dict[tuple[str, str], EdgeResult] = {}
    today_iso = datetime.now(timezone.utc).date().isoformat()
    legal_entity_bridge = legal_entity_bridge or {}

    # Heavy suffix pattern: handles full words AND abbreviations
    _sfx = re.compile(
        r'\s*[,\.]?\s*(incorporated|corporation|company|limited|'
        r'inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|l\.l\.c\.?|plc\.?|'
        r'holdings?|group|enterprises?)\s*$',
        re.I
    )
    # Patterns that flag a neo4j entry as a non-operating entity (ADR, debt, etc.)
    _non_operating_re = re.compile(
        r'american\s+depositary|ordinary\s+shares?|'
        r'\d+\.?\d*%|fixed\s+rate|senior\s+notes?|due\s+\d{4}|'
        r'class\s+[a-z]\s+preferred|warrant|depositary\s+receipt',
        re.I
    )

    def _normalize(name: str) -> str:
        normalized = _phase8_usaspending_normalize_name(name)
        if normalized:
            return normalized
        n = name.lower().strip()
        n = re.sub(r'^the\s+', '', n)
        n = re.sub(r'[,\.&\'/]+', ' ', n)
        for _ in range(4):
            new = _sfx.sub('', n).strip()
            if new == n:
                break
            n = new
        return re.sub(r'\s+', ' ', n).strip()

    # Build normalized lookup, excluding non-operating entities (ADRs, debt instruments)
    norm_to_ticker: dict[str, str] = {}
    for ticker, name in ticker_to_name.items():
        if _non_operating_re.search(name):
            continue  # skip foreign ADRs, preferred shares, debt instruments
        n = _normalize(name)
        if n and len(n) >= 2:
            norm_to_ticker[n] = ticker  # later entry wins (rare dups)

    # Inverted index: first_word → [(norm, words, ticker)] for O(1) prefix lookup
    _first_word_index: dict[str, list[tuple[str, list[str], str]]] = {}
    for norm, tk in norm_to_ticker.items():
        words = norm.split()
        if not words:
            continue
        fw = words[0]
        if fw not in _first_word_index:
            _first_word_index[fw] = []
        _first_word_index[fw].append((norm, words, tk))

    for row in results:
        recipient  = (row.get("Recipient Name") or row.get("recipient_name") or "").strip()
        agency     = (row.get("Awarding Agency") or row.get("awarding_agency_name") or "").strip()
        obligation = row.get("Award Amount") or row.get("total_obligation") or 0
        start_date = _normalize_edge_date(row.get("Start Date") or row.get("start_date"))
        end_date = _normalize_edge_date(row.get("End Date") or row.get("end_date"))
        if not recipient or not agency or obligation < 50_000:
            continue

        rec_norm = _normalize(recipient)
        rec_words = rec_norm.split() if rec_norm else []
        ticker = norm_to_ticker.get(rec_norm)
        resolution_mode = "company_exact" if ticker else ""
        bridge_match = None

        # Deterministic low-risk subsidiary bridge: exact LegalEntity alias -> single listed ancestor.
        if not ticker and rec_norm:
            bridge_match = legal_entity_bridge.get(rec_norm)
            if bridge_match:
                ticker = str(bridge_match.get("ancestor_ticker") or "").strip().upper()
                resolution_mode = "legal_entity_ancestor"

        # Fallback: word-prefix alignment using inverted index by first word (O(bucket) not O(n))
        # Single-word recipients are NOT allowed to match — too many false positives
        # (e.g. "BANC" matching "Banc of California" via government contractor "Banc" substring).
        match_confidence = 0.95
        if not ticker and rec_words:
            n_rec = len(rec_words)
            if n_rec >= 2:  # require at least 2 recipient words to attempt prefix match
                best_ticker: str | None = None
                best_len = 0
                candidates = _first_word_index.get(rec_words[0], [])
                for candidate_norm, c_words, tk in candidates:
                    min_w = min(n_rec, len(c_words))
                    if min_w >= 2 and rec_words[:min_w] == c_words[:min_w]:
                        if min_w > best_len:
                            best_len = min_w
                            best_ticker = tk
                if best_ticker and best_len >= 2:
                    ticker = best_ticker
                    resolution_mode = "company_prefix"
                    # Partial prefix match (recipient has more words than matched) — lower confidence
                    if best_len < n_rec:
                        match_confidence = 0.75

        # Token-set overlap fallback removed — too many false positives from matching
        # on just 2 shared common words (e.g. "Bath & Body Works" matching unrelated recipients).

        if not ticker:
            continue
        agency_id = re.sub(r'\W+', '_', agency.lower())[:80]
        key = (ticker, agency_id)
        last_confirmed = end_date or start_date
        has_open_award = not end_date or end_date >= today_iso
        current = edges_by_key.get(key)
        if current is None:
            properties = {
                "agency_name": agency,
                "total_obligation": float(obligation or 0.0),
                "award_count": 1,
                "award_type": "government_contract",
                "recipient_name": recipient,
                "active_after": start_date,
                "last_confirmed": last_confirmed,
                "_latest_end_date": end_date,
                "_has_open_award": has_open_award,
                "recipient_resolution_mode": resolution_mode or "company_exact",
                "target_ticker": ticker,
            }
            bridge_display_name = ""
            bridge_entity_key = ""
            if resolution_mode == "legal_entity_ancestor":
                properties["recipient_listed_ancestor_ticker"] = ticker
                if bridge_match:
                    bridge_entity_key = str(bridge_match.get("entity_key") or "").strip()
                    bridge_display_name = str(bridge_match.get("display_name") or recipient or "").strip()
            _phase8_record_recipient_audit_metadata(
                properties,
                recipient_name=recipient,
                recipient_legal_entity_name=bridge_display_name,
                recipient_legal_entity_key=bridge_entity_key,
                resolution_mode=resolution_mode or "company_exact",
            )
            edges_by_key[key] = {
                "node_a": ticker,
                "rel": "CONTRACTS_WITH",
                "node_b": agency_id,
                "confidence": match_confidence,
                "source": "USASpending.gov",
                "properties": properties,
            }
            continue
        current["confidence"] = max(float(current.get("confidence") or 0.0), float(match_confidence or 0.0))
        props = current.setdefault("properties", {})
        props["total_obligation"] = float(props.get("total_obligation") or 0.0) + float(obligation or 0.0)
        props["award_count"] = int(props.get("award_count") or 0) + 1
        if start_date and (not props.get("active_after") or start_date < props.get("active_after")):
            props["active_after"] = start_date
        if last_confirmed and (not props.get("last_confirmed") or last_confirmed > props.get("last_confirmed")):
            props["last_confirmed"] = last_confirmed
        if end_date and (not props.get("_latest_end_date") or end_date > props.get("_latest_end_date")):
            props["_latest_end_date"] = end_date
        props["_has_open_award"] = bool(props.get("_has_open_award")) or has_open_award
        bridge_display_name = ""
        bridge_entity_key = ""
        if resolution_mode == "legal_entity_ancestor":
            props["recipient_listed_ancestor_ticker"] = ticker
            if bridge_match:
                bridge_entity_key = str(bridge_match.get("entity_key") or "").strip()
                bridge_display_name = str(bridge_match.get("display_name") or recipient or "").strip()
        _phase8_record_recipient_audit_metadata(
            props,
            recipient_name=recipient,
            recipient_legal_entity_name=bridge_display_name,
            recipient_legal_entity_key=bridge_entity_key,
            resolution_mode=resolution_mode or "company_exact",
        )

    # LLM validation: verify low-confidence CONTRACTS_WITH matches (partial prefix matches)
    # Sends uncertain (recipient → ticker) pairs to LLM to confirm or reject.
    edges: list[EdgeResult] = list(edges_by_key.values())
    for edge in edges:
        props = dict(edge.get("properties") or {})
        has_open_award = bool(props.pop("_has_open_award", False))
        latest_end_date = _normalize_edge_date(props.pop("_latest_end_date", ""))
        edge_state = "open" if has_open_award else "closed"
        props["edge_state"] = edge_state
        props["valid_until"] = "" if edge_state == "open" else (
            latest_end_date or props.get("last_confirmed") or props.get("active_after") or ""
        )
        edge["properties"] = props
    if llm_provider and llm_model and llm_api_key:
        edges = _llm_validate_usaspending_edges(edges, ticker_to_name, llm_provider, llm_model, llm_api_key)

    return edges


def _llm_validate_usaspending_edges(
    edges: list[EdgeResult],
    ticker_to_name: dict[str, str],
    provider: str,
    model: str,
    api_key: str,
    batch_size: int = 30,
) -> list[EdgeResult]:
    """
    LLM validation pass for low-confidence USASpending CONTRACTS_WITH edges.
    Uses Gemini web search grounding (if provider=gemini) to verify each uncertain
    recipient→company match against current web information, then falls back to
    plain LLM for other providers. Returns the filtered/updated edge list.
    """
    import json
    import re as _re
    from llm_utils import call_llm_by_provider, call_gemini_with_grounding  # noqa: F401
    from llm_utils import _call_llm_with_critical_guard as _call_llm_guarded

    uncertain = [(i, e) for i, e in enumerate(edges) if e.get("confidence", 1.0) < 0.90]
    if not uncertain:
        return edges

    _log(f"Phase 8: LLM validation of {len(uncertain)} low-confidence CONTRACTS_WITH matches...", "cyan")
    use_grounding = (provider or "").strip().lower() == "gemini"

    items = []
    for i, e in uncertain:
        props = e.get("properties") or {}
        recipient = props.get("recipient_name", "")
        ticker = e["node_a"]
        company_name = ticker_to_name.get(ticker, ticker)
        items.append({"idx": i, "recipient": recipient, "ticker": ticker, "company": company_name})

    confirmed_indices: set[int] = set()

    if use_grounding:
        # Grounded mode: one LLM call per item with web search enabled.
        # Use a small sub-batch limit; grounding calls are slower.
        for x in items:
            prompt = (
                f"Does the government contractor '{x['recipient']}' refer to the publicly traded company "
                f"'{x['company']}' (ticker: {x['ticker']})? "
                "Answer YES or NO. A match is valid only if they are the same operating entity or a wholly-owned subsidiary. "
                "Search the web if needed to confirm. Reply with a single word: YES or NO."
            )
            raw = call_gemini_with_grounding(api_key, model, prompt, max_output_tokens=0)
            if not raw:
                confirmed_indices.add(x["idx"])  # conservative: keep on failure
            elif "YES" in raw.upper():
                confirmed_indices.add(x["idx"])
            else:
                _log(f"Phase 8: LLM(grounded) rejected CONTRACTS_WITH: '{x['recipient']}' -> {x['ticker']} ({x['company']})", "yellow")
    else:
        # Non-grounded batch mode for other providers
        batches = [items[s:s + batch_size] for s in range(0, len(items), batch_size)]
        for batch in batches:
            pairs_json = json.dumps(
                [{"recipient": x["recipient"], "ticker": x["ticker"], "company": x["company"]} for x in batch],
                ensure_ascii=False,
            )
            prompt = (
                "Task: validate whether each USASpending.gov contract recipient matches the listed company.\n"
                "A match is valid ONLY if the recipient is clearly the same operating entity as the company (or a wholly-owned subsidiary).\n"
                "Reject if: different industry, different company with similar name, or recipient is an unrelated entity.\n"
                "Return strict JSON only — an array of booleans in the same order as the input.\n\n"
                f"INPUT:\n{pairs_json}\n"
            )
            # Route through critical-guard so persistent Azure/OpenAI auth or
            # 5xx failures auto-abort the backtest. TODO: thread engine config
            # into this helper to populate backtest_id/instance_id.
            raw = _call_llm_guarded(provider, api_key, model, prompt,
                                    attribution_keys={"call_site": "nexus_graph_engine.usaspending_validation"},
                                    max_output_tokens=0, retries=1,
                                    response_mime_type="application/json")
            if not raw:
                for x in batch:
                    confirmed_indices.add(x["idx"])
                continue
            try:
                m = _re.search(r'\[.*\]', raw, _re.DOTALL)
                results_list = json.loads(m.group()) if m else []
                for x, keep in zip(batch, results_list):
                    if keep:
                        confirmed_indices.add(x["idx"])
                    else:
                        _log(f"Phase 8: LLM rejected CONTRACTS_WITH: '{x['recipient']}' -> {x['ticker']} ({x['company']})", "yellow")
            except Exception:
                for x in batch:
                    confirmed_indices.add(x["idx"])

    # Rebuild edge list: keep high-confidence edges + LLM-confirmed uncertain ones
    uncertain_idxs = {i for i, _ in uncertain}
    filtered = []
    for i, e in enumerate(edges):
        if i not in uncertain_idxs or i in confirmed_indices:
            filtered.append(e)
    removed = len(edges) - len(filtered)
    if removed:
        _log(f"Phase 8: LLM removed {removed} low-confidence CONTRACTS_WITH false positives.", "green")
    return filtered


def _usaspending_is_transient_fetch_error(exc) -> bool:
    text = str(exc or "").lower()
    return any(
        token in text
        for token in (
            "remote end closed connection",
            "remotedisconnected",
            "connection aborted",
            "read timed out",
            "timeout",
            "500 server error",
            "502",
            "503",
            "504",
        )
    )


def _usaspending_fetch_windows(start_date_iso: str, end_date_iso: str) -> list[tuple[str, str]]:
    start_dt = datetime.strptime(start_date_iso, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date_iso, "%Y-%m-%d").date()
    if end_dt < start_dt:
        return [(start_date_iso, end_date_iso)]
    out: list[tuple[str, str]] = []
    cursor = start_dt
    while cursor <= end_dt:
        window_end = min(cursor + timedelta(days=USASPENDING_WINDOW_DAYS - 1), end_dt)
        out.append((cursor.isoformat(), window_end.isoformat()))
        cursor = window_end + timedelta(days=1)
    return out


def phase9_usaspending(session):
    """Phase 8: Fetch USASpending.gov awards (up to USASPENDING_MAX_ROWS), resolve to companies, batch-write CONTRACTS_WITH with progress."""
    import requests

    _nexus_stage_reset()
    _progress(PHASE_PCT[10], "Phase 8: USASpending.gov government contracts...", "cyan")
    conn = _nexus_rethink_conn

    valid_c = _cypher_valid_company_listing_predicate("c")
    r = session.run(
        f"MATCH (c:Company) WHERE c.name IS NOT NULL AND {valid_c} RETURN c.ticker AS ticker, c.name AS name LIMIT $lim",
        lim=GRAPH_NEXUS_MAX_COMPANIES,
    )
    ticker_to_name = {rec["ticker"]: rec["name"] for rec in r if rec.get("ticker") and rec.get("name")}
    legal_entity_bridge = _phase8_load_usaspending_legal_entity_bridge(session)
    if not ticker_to_name:
        _log_stage_unexpected("Phase 8: USASpending", "company list", "No companies in graph (ticker/name) for recipient resolution.")
        _progress(PHASE_PCT[11], "Phase 8 skipped.", "green")
        return
    if legal_entity_bridge:
        _log(
            f"Phase 8: loaded {len(legal_entity_bridge)} deterministic low-risk LegalEntity alias bridge keys.",
            "white",
        )

    cache_path = _nexus_cache_path("phase9", "usaspending_contracts.json")
    _usa_upserts = [0]
    _usa_touched_pairs: set[tuple[str, str]] = set()
    _usa_last_resolved = None  # None = cached path, int = fetch path
    _usa_today = datetime.now(timezone.utc).date().isoformat()
    _usa_run_token = f"phase8:{uuid4().hex}"
    _usa_close_date = ""
    _usa_resolved_edges: dict[tuple[str, str], EdgeResult] = {}

    def _usa_merge_edges(edge_list: list[EdgeResult]) -> None:
        for edge in edge_list or []:
            key = (str(edge.get("node_a") or "").strip().upper(), str(edge.get("node_b") or "").strip())
            if not key[0] or not key[1]:
                continue
            current = _usa_resolved_edges.get(key)
            if current is None:
                _usa_resolved_edges[key] = edge
                continue
            _phase8_merge_resolved_usaspending_edge(current, edge)

    def _usa_write_edges(edge_list: list) -> None:
        nonlocal _usa_close_date
        for s in range(0, len(edge_list), 200):
            b = edge_list[s:s + 200]
            params = [_phase8_usaspending_edge_to_write_param(e, _usa_today) for e in b]
            session.run(
                """
                UNWIND $batch AS p
                MERGE (agency:GovAgency {id: p.agency_id})
                SET agency.name = p.agency_name
                WITH agency, p
                MATCH (c:Company {ticker: p.ticker})
                WHERE """
                + valid_c
                + """
                MERGE (c)-[r:CONTRACTS_WITH {source_scope: 'USASPENDING_AWARD'}]->(agency)
                ON CREATE SET r.total_obligation = p.obligation, r.source = 'USASpending.gov',
                              r.award_count = p.award_count,
                              r.confidence = 0.95, r.last_updated = p.today, r.active_after = p.active_after,
                              r.last_confirmed = p.last_confirmed,
                              r.recipient_name = CASE WHEN p.recipient_name = '' THEN NULL ELSE p.recipient_name END,
                              r.recipient_name_examples = CASE WHEN size(p.recipient_name_examples) = 0 THEN NULL ELSE p.recipient_name_examples END,
                              r.recipient_name_count = CASE WHEN p.recipient_name_count <= 0 THEN NULL ELSE p.recipient_name_count END,
                              r.recipient_resolution_mode = CASE WHEN p.recipient_resolution_mode = '' THEN NULL ELSE p.recipient_resolution_mode END,
                              r.recipient_resolution_source = CASE WHEN p.recipient_resolution_source = '' THEN NULL ELSE p.recipient_resolution_source END,
                              r.recipient_listed_ancestor_ticker = CASE WHEN p.recipient_listed_ancestor_ticker = '' THEN NULL ELSE p.recipient_listed_ancestor_ticker END,
                              r.recipient_legal_entity_key = CASE WHEN p.recipient_legal_entity_key = '' THEN NULL ELSE p.recipient_legal_entity_key END,
                              r.recipient_legal_entity_name = CASE WHEN p.recipient_legal_entity_name = '' THEN NULL ELSE p.recipient_legal_entity_name END,
                              r.recipient_legal_entity_name_examples = CASE WHEN size(p.recipient_legal_entity_name_examples) = 0 THEN NULL ELSE p.recipient_legal_entity_name_examples END,
                              r.recipient_legal_entity_name_count = CASE WHEN p.recipient_legal_entity_name_count <= 0 THEN NULL ELSE p.recipient_legal_entity_name_count END,
                              r.edge_state = p.edge_state,
                              r.valid_until = CASE WHEN p.edge_state = 'closed' THEN p.valid_until ELSE NULL END,
                              r.retired_reason = CASE WHEN p.edge_state = 'closed' THEN 'source_window_closed' ELSE NULL END
                ON MATCH  SET r.source = coalesce(r.source, 'USASpending.gov'),
                              r.confidence = coalesce(r.confidence, 0.95),
                              r.total_obligation = CASE
                                  WHEN coalesce(r.current_run_token, '') = $run_token THEN coalesce(r.total_obligation, 0.0) + p.obligation
                                  ELSE p.obligation
                              END,
                              r.last_updated = p.today,
                              r.award_count = CASE
                                  WHEN coalesce(r.current_run_token, '') = $run_token THEN coalesce(r.award_count, 0) + p.award_count
                                  ELSE p.award_count
                              END,
                              r.active_after = CASE
                                  WHEN p.active_after IS NULL OR p.active_after = '' THEN r.active_after
                                  WHEN r.active_after IS NULL OR r.active_after = '' OR p.active_after < r.active_after THEN p.active_after
                                  ELSE r.active_after
                              END,
                              r.last_confirmed = CASE
                                  WHEN p.last_confirmed IS NULL OR p.last_confirmed = '' THEN r.last_confirmed
                                  WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR p.last_confirmed > r.last_confirmed THEN p.last_confirmed
                                  ELSE r.last_confirmed
                              END,
                              r.valid_until = CASE
                                  WHEN coalesce(r.current_run_token, '') = $run_token THEN
                                      CASE
                                          WHEN p.edge_state = 'open' OR coalesce(r.edge_state, '') = 'open' THEN NULL
                                          WHEN p.valid_until IS NULL OR p.valid_until = '' THEN r.valid_until
                                          WHEN r.valid_until IS NULL OR r.valid_until = '' OR p.valid_until > r.valid_until THEN p.valid_until
                                          ELSE r.valid_until
                                      END
                                  ELSE CASE WHEN p.edge_state = 'closed' THEN p.valid_until ELSE NULL END
                              END,
                              r.retired_reason = CASE
                                  WHEN coalesce(r.current_run_token, '') = $run_token THEN
                                      CASE
                                          WHEN p.edge_state = 'open' OR coalesce(r.edge_state, '') = 'open' THEN NULL
                                          ELSE 'source_window_closed'
                                      END
                                  ELSE CASE WHEN p.edge_state = 'closed' THEN 'source_window_closed' ELSE NULL END
                              END,
                              r.recipient_name = CASE WHEN p.recipient_name = '' THEN NULL ELSE p.recipient_name END,
                              r.recipient_name_examples = CASE WHEN size(p.recipient_name_examples) = 0 THEN NULL ELSE p.recipient_name_examples END,
                              r.recipient_name_count = CASE WHEN p.recipient_name_count <= 0 THEN NULL ELSE p.recipient_name_count END,
                              r.recipient_resolution_mode = CASE WHEN p.recipient_resolution_mode = '' THEN NULL ELSE p.recipient_resolution_mode END,
                              r.recipient_resolution_source = CASE WHEN p.recipient_resolution_source = '' THEN NULL ELSE p.recipient_resolution_source END,
                              r.recipient_listed_ancestor_ticker = CASE WHEN p.recipient_listed_ancestor_ticker = '' THEN NULL ELSE p.recipient_listed_ancestor_ticker END,
                              r.recipient_legal_entity_key = CASE WHEN p.recipient_legal_entity_key = '' THEN NULL ELSE p.recipient_legal_entity_key END,
                              r.recipient_legal_entity_name = CASE WHEN p.recipient_legal_entity_name = '' THEN NULL ELSE p.recipient_legal_entity_name END,
                              r.recipient_legal_entity_name_examples = CASE WHEN size(p.recipient_legal_entity_name_examples) = 0 THEN NULL ELSE p.recipient_legal_entity_name_examples END,
                              r.recipient_legal_entity_name_count = CASE WHEN p.recipient_legal_entity_name_count <= 0 THEN NULL ELSE p.recipient_legal_entity_name_count END,
                              r.edge_state = CASE
                                  WHEN coalesce(r.current_run_token, '') = $run_token THEN
                                      CASE
                                          WHEN p.edge_state = 'open' OR coalesce(r.edge_state, '') = 'open' THEN 'open'
                                          ELSE 'closed'
                                      END
                                  ELSE p.edge_state
                              END,
                              r.current_run_token = $run_token
                SET r.current_run_token = $run_token
                """,
                batch=params,
                run_token=_usa_run_token,
            ).consume()
            _usa_upserts[0] += len(b)
            _usa_touched_pairs.update(
                (str(p.get("ticker") or "").strip().upper(), str(p.get("agency_id") or "").strip())
                for p in params
                if str(p.get("ticker") or "").strip() and str(p.get("agency_id") or "").strip()
            )
            for p in params:
                aa = _normalize_edge_date(p.get("last_confirmed") or p.get("valid_until") or p.get("active_after"))
                if not aa:
                    continue
                if not _usa_close_date:
                    _usa_close_date = aa
                else:
                    _usa_close_date = max(_usa_close_date, aa)
        if conn:
            unique_count = len(_usa_touched_pairs)
            _phase9_report_progress(
                conn,
                unique_count,
                0,
                f"Touched {unique_count} unique CONTRACTS_WITH relationships so far ({_usa_upserts[0]} upserts)",
            )

    # USASpending rolling 2-year window - 1-day TTL to pick up new awards.
    results = _nexus_read_cached_json(cache_path, _nexus_cache_max_age("phase8"))
    if results is not None and isinstance(results, list):
        _nexus_stage_cache_hit(1)
        if conn:
            _phase9_report_progress(
                conn,
                len(results),
                0,
                f"Using cached {len(results)} rows",
                progress_pct_override=PHASE_PCT[10] + 0.5 * (PHASE_PCT[11] - PHASE_PCT[10]),
            )
        _log(f"Phase 8: Using cached USASpending data ({len(results)} rows).", "white")

    if results is None or not isinstance(results, list):
        results = []
        fetch_start_date = f"{datetime.now(timezone.utc).year - 2}-10-01"
        fetch_end_date = f"{datetime.now(timezone.utc).year + 1}-09-30"
        fetch_windows = _usaspending_fetch_windows(fetch_start_date, fetch_end_date)
        _log(
            f"Phase 8: Fetching up to {USASPENDING_MAX_ROWS} rows from USASpending across {len(fetch_windows)} "
            f"time window(s) ({USASPENDING_WINDOW_DAYS}d each, cursor pagination, {USASPENDING_PAGE_SIZE}/page, "
            f"cooldown {USASPENDING_BREAK_SECONDS}s every {USASPENDING_BREAK_EVERY} rows, "
            f"rate window {USASPENDING_REQUESTS_PER_WINDOW}/{USASPENDING_REQUEST_WINDOW_SECONDS}s)...",
            "cyan",
        )
        request_num = 0
        report_every = 50
        _usa_resolve_batch = 10000
        _usa_last_resolved = 0
        consecutive_failures = 0
        max_consecutive_failures = 5
        transient_failure_times = []
        burst_cooldown_streak = 0
        rate_limit_cooldowns = 0
        max_rate_limit_cooldowns = 3
        abort_fetch = False
        fetch_complete = True
        dropped_requests = 0
        dropped_request_windows: set[int] = set()
        request_timestamps: deque[float] = deque()

        for window_index, (window_start, window_end) in enumerate(fetch_windows, start=1):
            if len(results) >= USASPENDING_MAX_ROWS or abort_fetch:
                _log(
                    f"Phase 8: global cap {USASPENDING_MAX_ROWS} reached or fetch aborted; "
                    f"skipping windows {window_index}..{len(fetch_windows)}.",
                    "yellow",
                )
                break
            last_record_unique_id = None
            last_record_sort_value = None
            rows_since_last_break = 0
            reached_end = False
            rows_before_window = len(results)
            # Per-window ceiling = min(per-window cap, remaining headroom under the global cap).
            # This is what lets every window run instead of window 1 eating the whole budget.
            window_row_target = len(results) + min(
                USASPENDING_MAX_ROWS_PER_WINDOW,
                max(0, USASPENDING_MAX_ROWS - len(results)),
            )
            _log(
                f"Phase 8: Window {window_index}/{len(fetch_windows)} {window_start} -> {window_end} starting "
                f"({len(results)} rows fetched so far).",
                "cyan",
            )
            while len(results) < window_row_target and not reached_end and not abort_fetch:
                active_break_every = USASPENDING_BREAK_EVERY
                active_break_seconds = USASPENDING_BREAK_SECONDS
                payload = {
                    "filters": {
                        "award_type_codes": ["A", "B", "C", "D"],
                        "time_period": [{"start_date": window_start, "end_date": window_end}],
                    },
                    "fields": ["Recipient Name", "Awarding Agency", "Award Amount", "Start Date", "End Date"],
                    "sort": "Award Amount",
                    "order": "desc",
                    "limit": min(USASPENDING_PAGE_SIZE, window_row_target - len(results)),
                }
                if last_record_unique_id is not None and last_record_sort_value is not None:
                    payload["last_record_unique_id"] = last_record_unique_id
                    payload["last_record_sort_value"] = last_record_sort_value
                else:
                    payload["page"] = 1
                _usaspending_wait_for_request_window(request_timestamps)
                request_num += 1
                resp_data = None
                for attempt in range(USASPENDING_REQUEST_MAX_ATTEMPTS):
                    try:
                        resp = _usaspending_http_session().post(
                            "https://api.usaspending.gov/api/v2/search/spending_by_award/",
                            json=payload,
                            timeout=30,
                        )
                        resp.raise_for_status()
                        resp_data = resp.json()
                        consecutive_failures = 0
                        break
                    except requests.HTTPError as e:
                        if e.response is not None and e.response.status_code == 422:
                            _log(
                                f"Phase 8: Window {window_index}/{len(fetch_windows)} request {request_num} - 422 "
                                "(API limit), stopping.",
                                "yellow",
                            )
                            consecutive_failures = max_consecutive_failures
                            rate_limit_cooldowns = max_rate_limit_cooldowns
                            break
                        if _usaspending_is_transient_fetch_error(e):
                            transient_failure_times.append(time.monotonic())
                            _usaspending_reset_http_session()
                        if attempt < USASPENDING_REQUEST_MAX_ATTEMPTS - 1:
                            wait = min(USASPENDING_REQUEST_BACKOFF_CAP_SEC, 5 * (2 ** attempt))
                            _log(
                                f"Phase 8: Window {window_index}/{len(fetch_windows)} request {request_num} "
                                f"attempt {attempt + 1} failed ({e}), retrying in {wait}s...",
                                "yellow",
                            )
                            time.sleep(wait)
                        else:
                            _log(
                                f"Phase 8: Window {window_index}/{len(fetch_windows)} request {request_num} "
                                f"failed after {USASPENDING_REQUEST_MAX_ATTEMPTS} attempts ({e}); dropping this page.",
                                "yellow",
                            )
                            consecutive_failures += 1
                            dropped_requests += 1
                            dropped_request_windows.add(window_index)
                    except Exception as e:
                        if _usaspending_is_transient_fetch_error(e):
                            transient_failure_times.append(time.monotonic())
                            _usaspending_reset_http_session()
                        if attempt < USASPENDING_REQUEST_MAX_ATTEMPTS - 1:
                            wait = min(USASPENDING_REQUEST_BACKOFF_CAP_SEC, 5 * (2 ** attempt))
                            _log(
                                f"Phase 8: Window {window_index}/{len(fetch_windows)} request {request_num} "
                                f"attempt {attempt + 1} failed ({e}), retrying in {wait}s...",
                                "yellow",
                            )
                            time.sleep(wait)
                        else:
                            _log(
                                f"Phase 8: Window {window_index}/{len(fetch_windows)} request {request_num} "
                                f"failed after {USASPENDING_REQUEST_MAX_ATTEMPTS} attempts ({e}); dropping this page.",
                                "yellow",
                            )
                            consecutive_failures += 1
                            dropped_requests += 1
                            dropped_request_windows.add(window_index)

                now_mono = time.monotonic()
                transient_failure_times = [
                    ts
                    for ts in transient_failure_times
                    if (now_mono - ts) <= USASPENDING_FAILURE_BURST_WINDOW_SECONDS
                ]
                if len(transient_failure_times) >= USASPENDING_FAILURE_BURST_THRESHOLD:
                    burst_cooldown_streak += 1
                    cooldown = min(
                        USASPENDING_FAILURE_BURST_COOLDOWN_SECONDS * burst_cooldown_streak,
                        USASPENDING_FAILURE_BURST_MAX_COOLDOWN_SECONDS,
                    )
                    _log(
                        f"Phase 8: transient failure burst ({len(transient_failure_times)} failed attempt(s) within "
                        f"{USASPENDING_FAILURE_BURST_WINDOW_SECONDS}s) - cooling down {cooldown}s before retrying "
                        f"USASpending (burst streak {burst_cooldown_streak}); normal pacing resumes after cooldown.",
                        "yellow",
                    )
                    if conn:
                        _phase9_report_progress(
                            conn,
                            len(results),
                            0,
                            f"Transient API failures; cooling down {cooldown}s...",
                            progress_pct_override=PHASE_PCT[10] + 0.5 * (PHASE_PCT[11] - PHASE_PCT[10]),
                        )
                    time.sleep(cooldown)
                    transient_failure_times = []
                if resp_data is None:
                    if consecutive_failures >= max_consecutive_failures:
                        if rate_limit_cooldowns < max_rate_limit_cooldowns:
                            rate_limit_cooldowns += 1
                            cooldown = 60
                            _log(
                                f"Phase 8: {max_consecutive_failures} consecutive failures - cooling down {cooldown}s "
                                f"(attempt {rate_limit_cooldowns}/{max_rate_limit_cooldowns})...",
                                "yellow",
                            )
                            if conn:
                                _phase9_report_progress(
                                    conn,
                                    len(results),
                                    0,
                                    f"Rate-limited, cooling down {cooldown}s...",
                                    progress_pct_override=PHASE_PCT[10] + 0.5 * (PHASE_PCT[11] - PHASE_PCT[10]),
                                )
                            time.sleep(cooldown)
                            consecutive_failures = 0
                            continue
                        _log(f"Phase 8: Cooldowns exhausted ({max_rate_limit_cooldowns}), stopping fetch.", "yellow")
                        abort_fetch = True
                        fetch_complete = False
                        break
                    continue

                batch = resp_data.get("results") or []
                page_meta = resp_data.get("page_metadata") or {}
                last_record_unique_id = page_meta.get("last_record_unique_id")
                last_record_sort_value = page_meta.get("last_record_sort_value")
                has_next = page_meta.get("hasNext", False)
                rows_since_last_break += len(batch)
                results.extend(batch)

                if len(results) - _usa_last_resolved >= _usa_resolve_batch:
                    new_rows = results[_usa_last_resolved:]
                    new_edges = _resolve_usaspending_to_edges(new_rows, ticker_to_name, legal_entity_bridge=legal_entity_bridge)
                    if new_edges:
                        _usa_merge_edges(new_edges)
                        _log(
                            f"Phase 8: Resolved {len(new_edges)} edge candidates from rows {_usa_last_resolved}-{len(results)} "
                            f"({len(_usa_resolved_edges)} unique relationships aggregated so far).",
                            "white",
                        )
                    _usa_last_resolved = len(results)

                if conn and (request_num % report_every == 0 or not has_next):
                    pct = min(1.0, len(results) / max(USASPENDING_MAX_ROWS, 1))
                    _phase9_report_progress(
                        conn,
                        len(results),
                        0,
                        f"Fetching... {len(results)} rows, {len(_usa_resolved_edges)} unique relationships aggregated",
                        progress_pct_override=PHASE_PCT[10] + pct * 0.5 * (PHASE_PCT[11] - PHASE_PCT[10]),
                    )
                if request_num % 100 == 0 or request_num == 1:
                    _log(
                        f"Phase 8: Fetched {len(results)} rows (request {request_num}, window {window_index}/{len(fetch_windows)}), "
                        f"{len(_usa_resolved_edges)} unique relationships aggregated...",
                        "white",
                    )

                if not has_next or len(batch) < USASPENDING_PAGE_SIZE:
                    reached_end = True
                    _log(
                        f"Phase 8: Window {window_index}/{len(fetch_windows)} reached end of data at "
                        f"{len(results) - rows_before_window} rows (hasNext={has_next}, batch={len(batch)}).",
                        "green",
                    )
                    break
                if last_record_unique_id is None or last_record_sort_value is None:
                    reached_end = True
                    _log(
                        f"Phase 8: Window {window_index}/{len(fetch_windows)} had no cursor in response; "
                        "reached end of data.",
                        "yellow",
                    )
                    break

                if active_break_seconds > 0 and rows_since_last_break >= active_break_every:
                    _log(
                        f"Phase 8: Fetched {rows_since_last_break} rows since last break ({len(results)} total). "
                        f"Pausing {active_break_seconds}s to avoid rate limits...",
                        "cyan",
                    )
                    if conn:
                        break_pct = min(1.0, len(results) / max(USASPENDING_MAX_ROWS, 1))
                        _phase9_report_progress(
                            conn,
                            len(results),
                            0,
                            f"Cooling down {active_break_seconds}s at {len(results)} rows...",
                            progress_pct_override=PHASE_PCT[10] + break_pct * 0.5 * (PHASE_PCT[11] - PHASE_PCT[10]),
                        )
                    time.sleep(active_break_seconds)
                    rows_since_last_break = 0
                    _log("Phase 8: Resuming fetch after break.", "cyan")

            _log(
                f"Phase 8: Window {window_index}/{len(fetch_windows)} completed with "
                f"{len(results) - rows_before_window} row(s) ({len(results)} total so far).",
                "green" if not abort_fetch else "yellow",
            )

        if dropped_requests:
            _log(
                f"Phase 8: WARNING {dropped_requests} page-fetch attempt(s) exhausted all "
                f"{USASPENDING_REQUEST_MAX_ATTEMPTS} retries (re-attempted in place, not skipped) across "
                f"{len(dropped_request_windows)} window(s) {sorted(dropped_request_windows)}; "
                "awards in those windows may be incomplete for this run.",
                "red",
            )
        if results and fetch_complete:
            _nexus_write_cached_json(cache_path, results)
            _nexus_stage_download(1)
        elif results:
            _log(
                f"Phase 8: Fetch stopped early after {len(results)} rows; not caching partial USASpending payload.",
                "yellow",
            )
        _log(f"Phase 8: Fetched {len(results)} rows total.", "green")

    resolve_from = _usa_last_resolved if isinstance(_usa_last_resolved, int) else 0
    if resolve_from < len(results):
        remaining = results[resolve_from:]
        llm_provider, llm_model, llm_api_key = _graph_nexus_llm_config()
        remaining_edges = _resolve_usaspending_to_edges(
            remaining,
            ticker_to_name,
            legal_entity_bridge=legal_entity_bridge,
            llm_provider=llm_provider,
            llm_model=llm_model,
            llm_api_key=llm_api_key,
        )
        if remaining_edges:
            _usa_merge_edges(remaining_edges)
            _log(
                f"Phase 8: Final batch - resolved {len(remaining_edges)} edge candidates from rows "
                f"{resolve_from}-{len(results)} ({len(_usa_resolved_edges)} unique relationships aggregated).",
                "white",
            )

    if not _usa_resolved_edges:
        _log_stage_unexpected("Phase 8: USASpending", "resolve recipients", "No USASpending contract edges resolved to companies.")
        _progress(PHASE_PCT[11], "Phase 8 skipped (no data resolved).", "green")
        return

    _usa_write_edges(list(_usa_resolved_edges.values()))

    close_date = _usa_close_date or _usa_today
    rec = session.run(
        """
        MATCH (:Company)-[r:CONTRACTS_WITH {source_scope: 'USASPENDING_AWARD'}]->(:GovAgency)
        WHERE coalesce(r.current_run_token, '') <> $run_token
          AND coalesce(r.edge_state, 'open') <> 'closed'
        SET r.edge_state = 'closed',
            r.valid_until = CASE
                WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                ELSE $close_date
            END,
            r.retired_reason = 'superseded',
            r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
            r.current_run_token = NULL
        RETURN count(r) AS closed
        """,
        run_token=_usa_run_token,
        close_date=close_date,
    ).single()
    usa_closed = int((rec or {}).get("closed") or 0)

    usa_state_counts: dict[str, int] = {}
    try:
        usa_state_counts = {
            str(row.get("state") or ""): int(row.get("n") or 0)
            for row in session.run(
                """
                MATCH ()-[r:CONTRACTS_WITH {source_scope: 'USASPENDING_AWARD'}]->()
                RETURN coalesce(r.edge_state, '') AS state, count(r) AS n
                """
            )
        }
    except Exception as e:
        _log_stage_error("Phase 8: USASpending", "count persisted CONTRACTS_WITH", str(e), e, color="yellow")

    unique_count = len(_usa_touched_pairs)
    _log(
        f"Phase 8 done: {unique_count} unique CONTRACTS_WITH relationships touched from USASpending "
        f"({_usa_upserts[0]} upserts processed).",
        "green",
    )
    if usa_closed:
        _log(f"Phase 8: closed {usa_closed} superseded CONTRACTS_WITH interval(s).", "green")
    if usa_state_counts:
        persisted_total = sum(usa_state_counts.values())
        state_parts = [
            f"{usa_state_counts[state]} {label}"
            for state, label in (("open", "open"), ("closed", "closed"), ("", "unset"))
            if usa_state_counts.get(state)
        ]
        suffix = f" ({', '.join(state_parts)})" if state_parts else ""
        _log(
            f"Phase 8: graph now has {persisted_total} persisted USASPENDING_AWARD CONTRACTS_WITH relationships{suffix}.",
            "white",
        )
    try:
        _sync_graph_edge_intervals(session, "CONTRACTS_WITH", source_scope="USASPENDING_AWARD", directed=True)
    except Exception as e:
        _log_stage_error("Phase 8: USASpending", "sync CONTRACTS_WITH interval history", str(e), e, color="yellow")
    try:
        retired_legacy = _retire_relationships(
            session,
            "CONTRACTS_WITH",
            "coalesce(r.source_scope, '') = ''",
            directed=True,
            retired_reason="legacy_replaced",
        )
        if retired_legacy:
            _log(f"Phase 8: retired {retired_legacy} legacy CONTRACTS_WITH edge(s) after replacement.", "green")
    except Exception as e:
        _log_stage_error("Phase 8: USASpending", "retire legacy CONTRACTS_WITH", str(e), e, color="yellow")
    _progress(PHASE_PCT[11], f"Phase 8 done: {unique_count} unique government contract relationships.", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 10: Wikidata corporate hierarchy (P749/P355)
# ──────────────────────────────────────────────────────────────────────────────
# Resources: Wikidata Query Service
#   POST https://query.wikidata.org/sparql — paginate with LIMIT/OFFSET (chunk 10k to avoid timeout).
#   P749 = parent org, P355 = subsidiary. Optional: P249 = ticker symbol for exact ticker matches.
# Flow: Fetch bindings in chunks (with progress) → resolve labels to tickers → batch-write CONTROLS with progress.
# ──────────────────────────────────────────────────────────────────────────────
def _resolve_wikidata_bindings_to_edges(bindings: list, title_to_ticker: dict[str, str], ticker_to_ticker: dict[str, str] | None = None, norm_to_ticker: dict[str, str] | None = None) -> list[EdgeResult]:
    """Resolve SPARQL bindings (parentLabel/childLabel or parentTicker/childTicker) to CONTROLS edges. No API."""
    import re
    edges: list[EdgeResult] = []
    seen: set[tuple[str, str]] = set()
    allow_prefix_match = os.environ.get("GRAPH_NEXUS_WIKIDATA_PREFIX_MATCH", "false").strip().lower() in ("1", "true", "yes")

    # Comprehensive suffix pattern: handles full words AND abbreviations
    _sfx = re.compile(
        r'\s*[,\.]?\s*(incorporated|corporation|company|limited|'
        r'inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|l\.l\.c\.?|plc\.?|'
        r'holdings?|group|enterprises?)\s*$',
        re.I
    )

    _norm_to_ticker = norm_to_ticker or {}

    def _binding_value(b: dict) -> str:
        if not b or not isinstance(b, dict):
            return ""
        if b.get("type") in ("literal", "uri"):
            return (b.get("value") or "").strip()
        return ""

    def _resolve_label(label: str) -> str | None:
        if not label or len(label) < 3:
            return None
        # 1. Exact raw name match (fastest, most reliable)
        exact = title_to_ticker.get(label.lower())
        if exact:
            return exact
        # 2. Suffix-normalized exact match
        norm = _sfx.sub("", label.strip()).strip().lower()
        exact = title_to_ticker.get(norm)
        if exact:
            return exact
        # 3. Heavy-normalized exact match (handles "Corporation" vs "Corp", "Company" vs "Co", etc.)
        heavy = _wikidata_heavy_norm(label)
        exact = _norm_to_ticker.get(heavy)
        if exact:
            return exact
        # 4. Word-prefix fuzzy match on heavy-normalized forms.
        # Requires: ≥2 matching prefix words AND the match must cover ≥60% of the
        # shorter name's words to avoid false positives like "LSI" matching "LSI Industries"
        # or "Ring" matching "Ring Energy" when we meant Amazon's Ring subsidiary.
        heavy_words = heavy.split()
        if allow_prefix_match and len(heavy_words) >= 3:
            best: str | None = None
            best_len = 0
            for k, v in _norm_to_ticker.items():
                k_words = k.split()
                min_w = min(len(heavy_words), len(k_words))
                shorter_len = min(len(heavy_words), len(k_words))
                if min_w >= 2 and heavy_words[:min_w] == k_words[:min_w] and min_w > best_len:
                    # Coverage check: matching prefix must cover ≥60% of the shorter name
                    if min_w == shorter_len:
                        best_len = min_w
                        best = v
            if best:
                return best
        # (Single-word matching removed — too ambiguous for corporate hierarchy)
        return None

    # Normalize ticker: strip exchange prefix (e.g. "NASDAQ:AAPL" -> "AAPL")
    def _norm_ticker(t: str) -> str:
        t = (t or "").strip().upper()
        if ":" in t:
            t = t.split(":")[-1]
        return t

    parent_only = 0
    child_only = 0
    neither = 0
    same_ticker = 0
    duplicate_rows = 0
    for row in bindings:
        parent_label = _binding_value(row.get("parentLabel") or row.get("parentlabel"))
        child_label  = _binding_value(row.get("childLabel") or row.get("childlabel"))
        parent_ticker = None
        child_ticker  = None
        if ticker_to_ticker is not None:
            pt = _binding_value(row.get("parentTicker") or row.get("parentticker"))
            ct = _binding_value(row.get("childTicker") or row.get("childticker"))
            if pt and ct:
                pt_u, ct_u = _norm_ticker(pt), _norm_ticker(ct)
                parent_ticker = ticker_to_ticker.get(pt_u) or (pt_u if pt_u in ticker_to_ticker else None)
                child_ticker  = ticker_to_ticker.get(ct_u) or (ct_u if ct_u in ticker_to_ticker else None)
            elif pt:
                pt_u = _norm_ticker(pt)
                parent_ticker = ticker_to_ticker.get(pt_u) or (pt_u if pt_u in ticker_to_ticker else None)
            elif ct:
                ct_u = _norm_ticker(ct)
                child_ticker  = ticker_to_ticker.get(ct_u) or (ct_u if ct_u in ticker_to_ticker else None)
        if not parent_ticker and parent_label:
            parent_ticker = _resolve_label(parent_label)
        if not child_ticker and child_label:
            child_ticker = _resolve_label(child_label)
        if not parent_ticker or not child_ticker or parent_ticker == child_ticker:
            if parent_ticker and child_ticker and parent_ticker == child_ticker:
                same_ticker += 1
            elif parent_ticker and not child_ticker:
                parent_only += 1
            elif child_ticker and not parent_ticker:
                child_only += 1
            elif not parent_ticker and not child_ticker:
                neither += 1
            continue
        key = (parent_ticker, child_ticker)
        if key in seen:
            duplicate_rows += 1
            continue
        seen.add(key)
        edges.append({
            "node_a": parent_ticker,
            "rel":    "CONTROLS",
            "node_b": child_ticker,
            "confidence": 0.80,
            "source": "Wikidata",
            "properties": {
                "parent_label": parent_label or "",
                "child_label": child_label or "",
                "resolution_mode": "ticker" if _binding_value(row.get("parentTicker") or row.get("parentticker")) and _binding_value(row.get("childTicker") or row.get("childticker")) else "label",
            },
        })
    if parent_only or child_only or neither or same_ticker or duplicate_rows:
        _log(
            f"Phase 9: Wikidata resolution stats — {len(edges)} matched, {parent_only} parent-only, "
            f"{child_only} child-only, {neither} neither, {same_ticker} same-ticker, "
            f"{duplicate_rows} duplicate rows (of {len(bindings)} bindings).",
            "white",
        )
    return edges


def _build_wikidata_controls_batch_param(edge: EdgeResult) -> dict[str, str | float | None]:
    props = dict((edge or {}).get("properties") or {})
    last_confirmed = _normalize_edge_date(props.get("last_confirmed")) or datetime.now(timezone.utc).date().isoformat()
    return {
        "parent": edge["node_a"],
        "child": edge["node_b"],
        "conf": edge.get("confidence", 0.80),
        "active_after": _normalize_edge_date(props.get("active_after") or last_confirmed),
        "last_confirmed": last_confirmed,
        "validation_mode": props.get("validation_mode"),
        "validated_at": props.get("validated_at"),
        "resolution_mode": props.get("resolution_mode"),
    }


def _llm_validate_controls_edges(
    edges: list[EdgeResult],
    ticker_to_name: dict[str, str],
    provider: str,
    model: str,
    api_key: str,
    validation_cache: dict[str, dict] | None = None,
    require_validation: bool = GRAPH_NEXUS_CONTROLS_REQUIRE_VALIDATION,
) -> list[EdgeResult]:
    """
    LLM validation pass for Wikidata CONTROLS edges.
    Verifies each resolved (parent→child) ownership edge is currently accurate using
    web search grounding (Gemini) or plain LLM reasoning for other providers.
    Catches stale spinoffs, name-collision false positives, and wrong Wikidata tickers.
    Returns the filtered edge list.
    """
    from pydantic import BaseModel, Field
    from llm_utils import call_gemini_with_grounding, call_llm_by_provider, call_structured_llm_by_provider  # noqa: F401
    from llm_utils import _call_llm_with_critical_guard as _call_llm_guarded
    from llm_utils import _call_structured_llm_with_critical_guard as _scl_guarded

    class _ControlsValidationDecision(BaseModel):
        keep: bool

    class _ControlsValidationBatch(BaseModel):
        results: list[_ControlsValidationDecision] = Field(default_factory=list)

    if not edges:
        return edges

    use_grounding = (provider or "").strip().lower() == "gemini"
    confirmed: list[EdgeResult] = []
    cache = validation_cache if validation_cache is not None else {}
    batch_size = max(1, GRAPH_NEXUS_CONTROLS_LLM_BATCH_SIZE)
    if use_grounding:
        batch_size = min(batch_size, 6)
    validation_mode = "llm_grounded" if use_grounding else "llm_model"
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _parse_keep_results(raw: str, expected_len: int) -> list[bool] | None:
        if not raw:
            return None
        try:
            m = re.search(r"\[.*\]", raw, re.DOTALL)
            parsed = json.loads(m.group()) if m else json.loads(raw)
        except Exception:
            return None
        if not isinstance(parsed, list):
            return None
        results: list[bool] = []
        for item in parsed:
            if isinstance(item, bool):
                results.append(item)
            elif isinstance(item, dict):
                keep = item.get("keep")
                if isinstance(keep, bool):
                    results.append(keep)
                elif isinstance(keep, str):
                    results.append(keep.strip().lower() in ("1", "true", "yes", "y"))
                else:
                    return None
            else:
                return None
        if len(results) != expected_len:
            return None
        return results

    def _structured_validate(prompt_text: str, expected_len: int) -> list[bool] | None:
        # TODO: thread `config` into _llm_validate_controls_edges to attribute
        # critical-guard failures back to the originating backtest/instance.
        structured = _scl_guarded(
            provider,
            api_key,
            model,
            prompt_text,
            _ControlsValidationBatch,
            attribution_keys={"call_site": "nexus_graph.controls_edges_validation"},
            system_prompt=(
                "Validate current corporate control relationships. "
                "Return a structured object with a single `results` list containing keep=true/false items in input order."
            ),
            max_output_tokens=max(128, expected_len * 16),
            timeout_sec=120,
            retries=2,
            output_retries=2,
            temperature=0.0,
        )
        if not structured:
            return None
        results = [bool(item.keep) for item in list(getattr(structured, "results", []) or [])]
        return results if len(results) == expected_len else None

    def _run_validation(batch_entries: list[tuple[EdgeResult, str]], depth: int = 0) -> list[tuple[tuple[EdgeResult, str], bool, bool]]:
        batch_edges = [item[0] for item in batch_entries]
        pairs = [
            {
                "parent_ticker": edge["node_a"],
                "parent_name": ticker_to_name.get(edge["node_a"], edge["node_a"]),
                "child_ticker": edge["node_b"],
                "child_name": ticker_to_name.get(edge["node_b"], edge["node_b"]),
            }
            for edge in batch_edges
        ]
        pairs_json = json.dumps(pairs, ensure_ascii=False)
        if use_grounding:
            prompt = (
                "Task: verify whether each corporate ownership relationship is currently valid as of today.\n"
                "Use web search grounding to check current ownership.\n"
                "A CONTROLS relationship is valid only if the parent currently holds majority ownership or operating control.\n"
                "Reject if the child was spun off, sold, is merely franchised, is just a minority investment, or the ticker mapping is wrong.\n"
                "Return strict JSON only in the same order as the input: "
                '[{"keep": true}, {"keep": false}]' "\n\n"
                f"INPUT:\n{pairs_json}\n"
            )
            raw = call_gemini_with_grounding(api_key, model, prompt, max_output_tokens=0)
            results_list = _parse_keep_results(raw, len(batch_edges))
            if results_list is None:
                fallback_prompt = (
                    "Task: verify whether each corporate ownership relationship is currently valid as of today.\n"
                    "A CONTROLS relationship is valid only if the parent currently holds majority ownership or operating control.\n"
                    "Reject if the child was spun off, sold, is merely franchised, is just a minority investment, or the ticker mapping is wrong.\n"
                    "Return a structured object with a single `results` list in the same order as the input.\n\n"
                    f"INPUT:\n{pairs_json}\n"
                )
                results_list = _structured_validate(fallback_prompt, len(batch_edges))
        else:
            prompt = (
                "Task: verify whether each corporate ownership relationship is currently valid as of today.\n"
                "A CONTROLS relationship is valid only if the parent currently holds majority ownership or operating control.\n"
                "Reject if the child was spun off, sold, is merely franchised, is just a minority investment, or the ticker mapping is wrong.\n"
                "Return strict JSON only in the same order as the input: "
                '[{"keep": true}, {"keep": false}]' "\n\n"
                f"INPUT:\n{pairs_json}\n"
            )
            results_list = _structured_validate(prompt, len(batch_edges))
            if results_list is None:
                # Route through critical-guard so persistent Azure/OpenAI auth or
                # 5xx failures auto-abort the backtest. TODO: thread engine config
                # into this helper to populate backtest_id/instance_id.
                raw = _call_llm_guarded(
                    provider,
                    api_key,
                    model,
                    prompt,
                    attribution_keys={"call_site": "nexus_graph_engine.controls_validation"},
                    max_output_tokens=0,
                    retries=1,
                    response_mime_type="application/json",
                )
                results_list = _parse_keep_results(raw, len(batch_edges))
        if results_list is None and len(batch_entries) > 1:
            split_at = max(1, len(batch_entries) // 2)
            return _run_validation(batch_entries[:split_at], depth + 1) + _run_validation(batch_entries[split_at:], depth + 1)
        if results_list is None:
            if require_validation:
                _log(
                    f"Phase 9: Validator returned no usable result for {len(batch_edges)} CONTROLS edges; "
                    f"dropping unresolved pair(s).",
                    "yellow",
                )
                return []
            return [(entry, True, False) for entry in batch_entries]
        return [(entry, bool(keep), True) for entry, keep in zip(batch_entries, results_list)]

    cached_rejections = 0
    pending: list[tuple[EdgeResult, str]] = []
    for edge in edges:
        cache_key = _controls_validation_cache_key(edge["node_a"], edge["node_b"], provider, model, use_grounding)
        cached = cache.get(cache_key)
        if _controls_validation_cache_entry_fresh(cached):
            if cached.get("keep") is True:
                updated = dict(edge)
                props = dict(updated.get("properties") or {})
                props["validation_mode"] = cached.get("validation_mode") or validation_mode
                props["validated_at"] = cached.get("checked_at") or now_iso
                updated["properties"] = props
                confirmed.append(updated)
            else:
                cached_rejections += 1
            continue
        pending.append((edge, cache_key))

    if not pending:
        if cached_rejections:
            _log(f"Phase 9: Controls validation cache rejected {cached_rejections} stale/invalid CONTROLS edges.", "green")
        return confirmed

    _log(f"Phase 9: LLM validation of {len(pending)} ambiguous CONTROLS edges (grounded={use_grounding})...", "cyan")

    for start in range(0, len(pending), batch_size):
        batch_entries = pending[start:start + batch_size]
        batch_results = _run_validation(batch_entries)
        for (edge, cache_key), keep, was_validated in batch_results:
            parent_name = ticker_to_name.get(edge["node_a"], edge["node_a"])
            child_name = ticker_to_name.get(edge["node_b"], edge["node_b"])
            if was_validated:
                cache[cache_key] = {
                    "keep": bool(keep),
                    "checked_at": now_iso,
                    "validation_mode": validation_mode,
                }
            if keep:
                updated = dict(edge)
                props = dict(updated.get("properties") or {})
                props["validation_mode"] = validation_mode if was_validated else props.get("validation_mode")
                if was_validated:
                    props["validated_at"] = now_iso
                updated["properties"] = props
                confirmed.append(updated)
            else:
                _log(f"Phase 9: LLM rejected CONTROLS: {edge['node_a']}({parent_name}) -> {edge['node_b']}({child_name})", "yellow")

    removed = len(edges) - len(confirmed)
    if removed:
        _log(f"Phase 9: LLM removed {removed} invalid CONTROLS edges (stale/false-positive).", "green")
    return confirmed


def phase10_wikidata(session):
    """Phase 9: Fetch Wikidata P749/P355 in chunks, resolve to tickers, batch-write CONTROLS with progress."""
    _nexus_stage_reset()
    _progress(PHASE_PCT[11], "Phase 9: Wikidata corporate hierarchy relationships...", "cyan")
    if not GRAPH_NEXUS_WIKIDATA_PHASE_ENABLED:
        _log(
            "Phase 9: DISABLED via GRAPH_NEXUS_WIKIDATA_PHASE_ENABLED=false; "
            "skipping Wikidata ownership ingestion. Set the env var to 'true' to enable.",
            "yellow",
        )
        _progress(PHASE_PCT[12], "Phase 9 skipped (explicitly disabled by env var).", "yellow")
        return
    import urllib.parse
    import requests
    conn = _nexus_rethink_conn

    r = session.run("""
        MATCH (c:Company)
        WHERE c.name IS NOT NULL
          AND NOT (
            coalesce(c.is_security_instrument, false) = true OR
            coalesce(c.listing_type, 'CS') IN ['BOND', 'ETF', 'ETN', 'ETS', 'ETV', 'FUND', 'NOTE', 'PFD', 'PREFERREDS', 'RIGHT', 'SP', 'UNIT', 'WARRANT']
          )
        RETURN c.name AS name, c.ticker AS ticker, c.canonical_name AS canonical_name,
               c.listing_name AS listing_name, properties(c)['lei_legal_name'] AS lei_legal_name,
               c.issuer_key AS issuer_key, c.cik AS cik, c.lei AS lei
        LIMIT $lim
    """, lim=GRAPH_NEXUS_MAX_COMPANIES)
    rep_records = [
        {
            "ticker": rec.get("ticker"),
            "name": rec.get("name"),
            "canonical_name": rec.get("canonical_name"),
            "listing_name": rec.get("listing_name"),
            "lei_legal_name": rec.get("lei_legal_name"),
            "issuer_key": rec.get("issuer_key"),
            "cik": rec.get("cik"),
            "lei": rec.get("lei"),
            "is_security_instrument": False,
        }
        for rec in r
        if rec.get("name") and rec.get("ticker")
    ]
    title_to_ticker, ticker_to_ticker, norm_to_ticker, ticker_to_name_p10 = _build_wikidata_company_resolution_maps(rep_records)

    sparql_url = "https://query.wikidata.org/sparql"
    headers = {
        "User-Agent": "IntelliStock-GraphNexus/1.0 (https://github.com/intellistock; contact@intellistock.local)",
        "Accept": "application/sparql-results+json",
    }
    cache_path = _nexus_cache_path("phase9", "wikidata_ownership.json")
    cache_read_path = _nexus_cache_lookup_path("phase9", "wikidata_ownership.json", "phase10")
    # Wikidata is crowd-edited daily - 7-day TTL.
    bindings = _nexus_read_cached_json(cache_read_path, _nexus_cache_max_age("phase9"))
    if bindings is not None and isinstance(bindings, list):
        _nexus_stage_cache_hit(1)
        if conn:
            _phase10_report_progress(conn, len(bindings), 0, f"Using cached {len(bindings)} bindings", progress_pct_override=PHASE_PCT[11] + 0.5 * (PHASE_PCT[12] - PHASE_PCT[11]))
        _log(f"Phase 9: Using cached Wikidata data ({len(bindings)} bindings).", "white")
    if bindings is None or not isinstance(bindings, list):
        bindings = []
        base_query = """
SELECT DISTINCT ?parentLabel ?childLabel ?parentTicker ?childTicker WHERE {
  VALUES ?type { wd:Q4830453 wd:Q43229 wd:Q783794 }
  { ?child wdt:P31 ?type . ?child wdt:P749 ?parent . ?parent wdt:P31 ?type . }
  UNION { ?parent wdt:P31 ?type . ?parent wdt:P355 ?child . ?child wdt:P31 ?type . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" . }
  OPTIONAL { ?parent wdt:P249 ?parentTicker . }
  OPTIONAL { ?child wdt:P249 ?childTicker . }
}
ORDER BY ?parentLabel ?childLabel
"""
        offset = 0
        chunk_num = 0
        try:
            _log(f"Phase 9: Fetching up to {WIKIDATA_MAX_ROWS} bindings (chunks of {WIKIDATA_CHUNK_SIZE}, cooldown {WIKIDATA_COOLDOWN_SECONDS}s, retries {WIKIDATA_MAX_RETRIES})...", "cyan")
            while offset < WIKIDATA_MAX_ROWS:
                # Cooldown between chunks to avoid 504 Gateway Timeout
                if chunk_num > 0 and WIKIDATA_COOLDOWN_SECONDS > 0:
                    _log(f"Phase 9: Cooldown {WIKIDATA_COOLDOWN_SECONDS}s before chunk {chunk_num + 1}...", "white")
                    if conn:
                        _phase10_report_progress(conn, len(bindings), 0, f"Cooldown {WIKIDATA_COOLDOWN_SECONDS}s...", progress_pct_override=PHASE_PCT[11] + 0.25 * (PHASE_PCT[12] - PHASE_PCT[11]))
                    time.sleep(WIKIDATA_COOLDOWN_SECONDS)
                query = base_query + f" LIMIT {WIKIDATA_CHUNK_SIZE} OFFSET {offset}"
                body = "query=" + urllib.parse.quote(query)
                chunk = []
                for attempt in range(WIKIDATA_MAX_RETRIES):
                    try:
                        resp = requests.post(
                            sparql_url,
                            data=body,
                            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
                            timeout=120,
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        chunk = (data.get("results") or {}).get("bindings", [])
                        break
                    except Exception as e:
                        # Catch ALL exceptions (HTTPError, Timeout, ConnectionError, etc.)
                        # to prevent any from escaping to the outer try-except
                        status = getattr(getattr(e, 'response', None), 'status_code', 0)
                        is_retryable = status in (429, 500, 502, 503, 504) or isinstance(e, (requests.Timeout, requests.ConnectionError))
                        if not is_retryable and isinstance(e, requests.HTTPError):
                            _log(f"Phase 9: Chunk {chunk_num + 1} — non-retryable HTTP {status}, skipping.", "yellow")
                            break
                        if attempt < WIKIDATA_MAX_RETRIES - 1:
                            wait = 60 * (attempt + 1)
                            _log(f"Phase 9: Chunk {chunk_num + 1} attempt {attempt + 1} — {e}; retrying in {wait}s...", "yellow")
                            time.sleep(wait)
                        else:
                            _log(f"Phase 9: Chunk {chunk_num + 1} failed after {WIKIDATA_MAX_RETRIES} attempts ({e}). Keeping {len(bindings)} bindings so far.", "yellow")
                            break
                if not chunk:
                    break
                bindings.extend(chunk)
                chunk_num += 1
                if conn:
                    pct = min(1.0, len(bindings) / max(WIKIDATA_MAX_ROWS, 1))
                    _phase10_report_progress(conn, len(bindings), 0, f"Fetching... {len(bindings)} bindings", progress_pct_override=PHASE_PCT[11] + pct * 0.5 * (PHASE_PCT[12] - PHASE_PCT[11]))
                if chunk_num % 2 == 1 or len(chunk) < WIKIDATA_CHUNK_SIZE:
                    _log(f"Phase 9: Fetched {len(bindings)} bindings (chunk {chunk_num})...", "white")
                if len(chunk) < WIKIDATA_CHUNK_SIZE:
                    break
                offset += WIKIDATA_CHUNK_SIZE
            if bindings:
                _nexus_write_cached_json(cache_path, bindings)
                _nexus_stage_download(1)
            _log(f"Phase 9: Fetched {len(bindings)} bindings total.", "green")
        except Exception as e:
            _log_stage_error("Phase 9: Wikidata", "SPARQL query", str(e), e, color="yellow")
            # Save what we have so far (even partial data is useful)
            if bindings:
                _nexus_write_cached_json(cache_path, bindings)
                _log(f"Phase 9: Saved {len(bindings)} bindings despite error.", "yellow")

    edges = _resolve_wikidata_bindings_to_edges(bindings, title_to_ticker, ticker_to_ticker, norm_to_ticker)
    if not edges:
        _log_stage_unexpected("Phase 9: Wikidata", "resolve to tickers", "No Wikidata ownership edges resolved to companies.")
        _progress(PHASE_PCT[12], "Phase 9 skipped (no data resolved).", "green")
        return

    confirmed_pairs = _fetch_confirmed_controls_pairs(session, edges, ticker_to_name_p10)
    validated_edges, ambiguous_edges = _partition_controls_edges_by_confirmation(edges, confirmed_pairs)
    if validated_edges:
        graph_confirmed = sum(1 for mode in confirmed_pairs.values() if mode == "gleif_parent")
        sec_confirmed = sum(1 for mode in confirmed_pairs.values() if mode == "sec_annual_filing")
        _log(
            f"Phase 9: Auto-confirmed {len(validated_edges)} CONTROLS edges "
            f"({graph_confirmed} from PARENT_OF, {sec_confirmed} from SEC annual filings).",
            "green",
        )

    # LLM validation is only used for ambiguous edges that were not already cross-confirmed.
    # This keeps cost down while preventing stale Wikidata relationships from auto-promoting.
    llm_provider, llm_model, llm_api_key = _graph_nexus_llm_config()
    if ambiguous_edges and llm_provider and llm_model and llm_api_key:
        validation_cache = _load_controls_validation_cache()
        validated_edges.extend(_llm_validate_controls_edges(
            ambiguous_edges,
            ticker_to_name_p10,
            llm_provider,
            llm_model,
            llm_api_key,
            validation_cache=validation_cache,
            require_validation=GRAPH_NEXUS_CONTROLS_REQUIRE_VALIDATION,
        ))
        _save_controls_validation_cache(validation_cache)
    elif ambiguous_edges and GRAPH_NEXUS_CONTROLS_REQUIRE_VALIDATION:
        _log(
            f"Phase 9: Dropping {len(ambiguous_edges)} ambiguous CONTROLS edges because no validator is configured.",
            "yellow",
        )
    else:
        validated_edges.extend(ambiguous_edges)
    edges = validated_edges

    if len(edges) < 50 and len(bindings) >= 500:
        _log(
            "Phase 9: Wikidata hierarchy is dominated by public-parent/private-child entities, so public-company CONTROLS recall is inherently low. "
            "Increasing WIKIDATA_MAX_ROWS alone is unlikely to produce thousands of additional public-company edges.",
            "yellow",
        )
    _log(f"Phase 9: Resolved {len(edges)} CONTROLS edges; writing to Neo4j in batches of 200...", "cyan")
    batch_size = 200
    created = 0
    closed = 0
    total_edges = len(edges)
    phase_run_token = f"phase9:{uuid4().hex}"
    for start in range(0, total_edges, batch_size):
        batch = edges[start:start + batch_size]
        params = [
            _build_wikidata_controls_batch_param(e)
            for e in batch
            if not _edge_pair_denied("CONTROLS", e["node_a"], e["node_b"])
        ]
        if not params:
            continue
        session.run("""
            UNWIND $batch AS p
            MATCH (parent:Company {ticker: p.parent}), (child:Company {ticker: p.child})
            WHERE coalesce(parent.is_security_instrument, false) = false
              AND coalesce(child.is_security_instrument, false) = false
              AND (parent.issuer_key IS NULL OR child.issuer_key IS NULL OR parent.issuer_key <> child.issuer_key)
            MERGE (parent)-[r:CONTROLS {source_scope: 'WIKIDATA_CONTROLS', edge_state: 'open'}]->(child)
            ON CREATE SET r.source = 'Wikidata',
                          r.confidence = p.conf,
                          r.rel_type = 'corporate_hierarchy',
                          r.active_after = p.active_after,
                          r.last_confirmed = p.last_confirmed,
                          r.validation_mode = p.validation_mode,
                          r.validation_checked_at = p.validated_at,
                          r.resolution_mode = p.resolution_mode,
                          r.valid_until = NULL,
                          r.retired_reason = NULL
            ON MATCH  SET r.confidence = CASE WHEN p.conf > r.confidence THEN p.conf ELSE r.confidence END,
                          r.active_after = CASE
                              WHEN p.active_after IS NULL OR p.active_after = '' THEN r.active_after
                              WHEN r.active_after IS NULL OR r.active_after = '' OR p.active_after < r.active_after THEN p.active_after
                              ELSE r.active_after
                          END,
                          r.last_confirmed = CASE
                              WHEN p.last_confirmed IS NULL OR p.last_confirmed = '' THEN r.last_confirmed
                              WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR p.last_confirmed > r.last_confirmed THEN p.last_confirmed
                              ELSE r.last_confirmed
                          END,
                          r.validation_mode = coalesce(p.validation_mode, r.validation_mode),
                          r.validation_checked_at = coalesce(p.validated_at, r.validation_checked_at),
                          r.resolution_mode = coalesce(p.resolution_mode, r.resolution_mode),
                          r.valid_until = NULL,
                          r.retired_reason = NULL,
                          r.edge_state = 'open',
                          r.current_run_token = $run_token
            SET r.current_run_token = $run_token
        """, batch=params, run_token=phase_run_token).consume()
        created += len(params)
        if conn:
            _phase10_report_progress(conn, created, total_edges, f"Writing... {created}/{total_edges} edges")
        if start + batch_size < total_edges:
            _nexus_maybe_log_eta()

    if created > 0:
        close_date = datetime.now(timezone.utc).date().isoformat()
        rec = session.run("""
            MATCH (:Company)-[r:CONTROLS {source_scope: 'WIKIDATA_CONTROLS', edge_state: 'open'}]->(:Company)
            WHERE coalesce(r.current_run_token, '') <> $run_token
            SET r.edge_state = 'closed',
                r.valid_until = CASE
                    WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                    WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                    ELSE $close_date
                END,
                r.retired_reason = 'superseded',
                r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
                r.current_run_token = NULL
            RETURN count(r) AS closed
        """, run_token=phase_run_token, close_date=close_date).single()
        closed = int((rec or {}).get("closed") or 0)

    _log(f"Phase 9 done: {created} CONTROLS edges from Wikidata.", "green")
    if closed:
        _log(f"Phase 9: closed {closed} superseded CONTROLS interval(s).", "green")
    try:
        _sync_graph_edge_intervals(session, "CONTROLS", source_scope="WIKIDATA_CONTROLS", directed=True)
    except Exception as e:
        _log_stage_error("Phase 9: Wikidata", "sync CONTROLS interval history", str(e), e, color="yellow")
    try:
        retired_legacy = _retire_relationships(
            session,
            "CONTROLS",
            "coalesce(r.source_scope, '') = ''",
            directed=True,
            retired_reason="legacy_replaced",
        )
        if retired_legacy:
            _log(f"Phase 9: retired {retired_legacy} legacy CONTROLS edge(s) after replacement.", "green")
    except Exception as e:
        _log_stage_error("Phase 9: Wikidata", "retire legacy CONTROLS", str(e), e, color="yellow")
    _progress(PHASE_PCT[12], f"Phase 9 done: {created} Wikidata corporate hierarchy edges.", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 10: PatentsView co-assignees
# ──────────────────────────────────────────────────────────────────────────────
# Resources:
#   PatentsView: GET https://search.patentsview.org/api/v1/patent/ (q, f, o as JSON query params). Header: X-Api-Key.
#     Response: { patents: [ { patent_id, assignees: [ { assignee_organization } ] } ] }. Co-assignees → PATENT_PARTNER.
# Flow: fetch patents with assignees → group by patent → pairs of assignees → resolve to tickers.
# ──────────────────────────────────────────────────────────────────────────────
_PATENTSVIEW_EXCLUDED_ENTITY_RE = re.compile(
    r"\b(university|universities|college|school\s+of|academy|institute\s+of\s+technology|"
    r"national\s+lab(?:oratory)?|research\s+institute)\b",
    re.I,
)


def _patentsview_is_excluded_entity_name(name: str) -> bool:
    return bool(_PATENTSVIEW_EXCLUDED_ENTITY_RE.search(str(name or "").strip()))


@lru_cache(maxsize=65536)
def _patentsview_heavy_name(name: str) -> str:
    _sfx_pv = re.compile(
        r'(?:\s+|\s*[,\.]\s*)(incorporated|corporation|company|limited|'
        r'inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|l\.l\.c\.?|plc\.?|holdings?|group|enterprises?|'
        r'gmbh|ag|sa|sas|ab|bv|nv|se|oy|oyj|ltda|spa|srl|sl|a\.s\.|aps|kk)\s*$',
        re.I,
    )
    n = str(name or "").lower().strip()
    if not n:
        return ""
    n = re.sub(r'^the\s+', '', n)
    n = re.sub(r'\s+common\s+stock\b.*$', '', n)
    n = re.sub(r'\s+american\s+depositary\b.*$', '', n)
    n = re.sub(r'\s+ordinary\s+shares?\b.*$', '', n)
    n = re.sub(r'\s*\([^)]*\)', ' ', n)
    n = re.sub(r'\b(kabushiki\s+kaisha|kaisha)\b', ' ', n)
    n = re.sub(r'[,\.&\'\+\/\-]+', ' ', n)
    for _ in range(4):
        new = _sfx_pv.sub('', n).strip()
        if new == n:
            break
        n = new
    return re.sub(r'\s+', ' ', n).strip()


def _build_patentsview_company_resolution_index(ticker_to_name: dict[str, str]) -> dict[str, Any]:
    exact_candidates: dict[str, set[str]] = {}
    norm_candidates: dict[str, set[str]] = {}
    company_name_candidates: dict[str, set[str]] = {}
    first_word_index: dict[str, list[tuple[str, list[str], str]]] = {}
    ticker_set = set(ticker_to_name.keys())
    debt_skip = re.compile(r'(\bsubordinated\b|notes\s+due)', re.I)

    def _add_candidate(mapping: dict[str, set[str]], key: str, ticker: str) -> None:
        if not key or not ticker:
            return
        mapping.setdefault(key, set()).add(ticker)

    for ticker, name in (ticker_to_name or {}).items():
        clean_name = str(name or "").strip()
        if not clean_name:
            continue
        if not debt_skip.search(clean_name):
            key = clean_name.lower()
            _add_candidate(exact_candidates, key, ticker)
            _add_candidate(company_name_candidates, key, ticker)
        norm = _patentsview_heavy_name(clean_name)
        if norm and len(norm) >= 2 and not debt_skip.search(clean_name):
            _add_candidate(norm_candidates, norm, ticker)
            words = norm.split()
            if words:
                first_word_index.setdefault(words[0], []).append((norm, words, ticker))
    for candidates in first_word_index.values():
        candidates.sort(key=lambda item: (-len(item[1]), item[0], item[2]))
    exact_to_ticker = {key: next(iter(values)) for key, values in exact_candidates.items() if len(values) == 1}
    norm_to_ticker = {key: next(iter(values)) for key, values in norm_candidates.items() if len(values) == 1}
    company_name_to_ticker = {key: next(iter(values)) for key, values in company_name_candidates.items() if len(values) == 1}
    return {
        "exact_to_ticker": exact_to_ticker,
        "norm_to_ticker": norm_to_ticker,
        "first_word_index": first_word_index,
        "company_name_to_ticker": company_name_to_ticker,
        "ticker_set": ticker_set,
    }


def _build_patentsview_legal_entity_resolution_index(legal_entity_records: list[dict] | None) -> dict[str, Any]:
    exact_candidates: dict[str, set[str]] = {}
    norm_candidates: dict[str, set[str]] = {}
    entity_key_to_display: dict[str, str] = {}
    entity_key_to_ancestor_tickers: dict[str, list[str]] = {}

    def _add(mapping: dict[str, set[str]], key: str, entity_key: str) -> None:
        if not key or not entity_key:
            return
        mapping.setdefault(key, set()).add(entity_key)

    for record in legal_entity_records or []:
        entity_key = str(record.get("entity_key") or "").strip()
        if not entity_key or record.get("has_public_company"):
            continue
        display_name = str(record.get("display_name") or record.get("legal_name") or entity_key).strip()
        legal_name = str(record.get("legal_name") or display_name or entity_key).strip()
        if _patentsview_is_excluded_entity_name(display_name) or _patentsview_is_excluded_entity_name(legal_name):
            continue
        entity_key_to_display[entity_key] = display_name or legal_name or entity_key
        entity_key_to_ancestor_tickers[entity_key] = sorted({
            str(ticker or "").strip().upper()
            for ticker in (record.get("listed_ancestor_tickers") or [])
            if str(ticker or "").strip()
        })
        candidate_names: list[str] = []
        for candidate in [display_name, legal_name]:
            text = re.sub(r"\s+", " ", str(candidate or "").strip())
            if text and text not in candidate_names:
                candidate_names.append(text)
        for alias in list(record.get("aliases") or []):
            text = re.sub(r"\s+", " ", str(alias or "").strip())
            if not text or text in candidate_names or _patentsview_is_excluded_entity_name(text):
                continue
            alias_norm = _normalize_entity_alias(text)
            if len(text.split()) < 2 and (len(text) < 8 or alias_norm in _AMBIGUOUS_ENTITY_ALIASES):
                continue
            candidate_names.append(text)
        for candidate in candidate_names:
            _add(exact_candidates, candidate.lower(), entity_key)
            norm = _patentsview_heavy_name(candidate)
            if norm:
                _add(norm_candidates, norm, entity_key)

    exact_to_entity = {key: next(iter(values)) for key, values in exact_candidates.items() if len(values) == 1}
    norm_to_entity = {key: next(iter(values)) for key, values in norm_candidates.items() if len(values) == 1}
    return {
        "exact_to_entity": exact_to_entity,
        "norm_to_entity": norm_to_entity,
        "entity_key_to_display": entity_key_to_display,
        "entity_key_to_ancestor_tickers": entity_key_to_ancestor_tickers,
    }


def _load_patentsview_legal_entities(session) -> list[dict]:
    if session is None:
        return []
    try:
        rows = session.run(
            """
            MATCH (le:LegalEntity)
            WHERE NOT le:Company
              AND coalesce(le.display_name, le.legal_name, le.name, '') <> ''
            RETURN le.entity_key AS entity_key,
                   coalesce(le.display_name, le.legal_name, le.name, le.entity_key) AS display_name,
                   coalesce(le.legal_name, le.display_name, le.name, le.entity_key) AS legal_name,
                   coalesce(le.aliases, []) AS aliases,
                   coalesce(le.listed_ancestor_tickers, []) AS listed_ancestor_tickers,
                   coalesce(le.entity_kind, 'legal_entity') AS entity_kind,
                   EXISTS { MATCH (:Company {lei: le.lei}) } AS has_public_company
            """
        )
    except Exception as exc:
        _log(f"Phase 10: LegalEntity assignee index load error: {exc}", "yellow")
        return []
    out: list[dict] = []
    for row in rows:
        entity_key = str(row.get("entity_key") or "").strip()
        if not entity_key:
            continue
        out.append({
            "entity_key": entity_key,
            "display_name": str(row.get("display_name") or "").strip(),
            "legal_name": str(row.get("legal_name") or "").strip(),
            "aliases": [str(alias or "").strip() for alias in (row.get("aliases") or []) if str(alias or "").strip()],
            "listed_ancestor_tickers": [str(ticker or "").strip().upper() for ticker in (row.get("listed_ancestor_tickers") or []) if str(ticker or "").strip()],
            "entity_kind": str(row.get("entity_kind") or "legal_entity").strip().lower(),
            "has_public_company": bool(row.get("has_public_company")),
        })
    return out


def _phase10_write_patent_partner_edges(session, batch_edges: list[dict], run_token: str) -> int:
    if not batch_edges:
        return 0
    combos: dict[tuple[str, str], list[dict]] = {
        ("Company", "Company"): [],
        ("Company", "LegalEntity"): [],
        ("LegalEntity", "LegalEntity"): [],
    }
    params: list[dict] = []
    for edge in batch_edges:
        props = dict(edge.get("properties") or {})
        a_kind = "LegalEntity" if str(props.get("node_a_kind") or "").strip() == "LegalEntity" else "Company"
        b_kind = "LegalEntity" if str(props.get("node_b_kind") or "").strip() == "LegalEntity" else "Company"
        a_ref = str(edge.get("node_a") or "").strip()
        b_ref = str(edge.get("node_b") or "").strip()
        if not a_ref or not b_ref or a_ref == b_ref:
            continue
        if a_kind == "LegalEntity" and b_kind == "Company":
            a_kind, b_kind = "Company", "LegalEntity"
            a_ref, b_ref = b_ref, a_ref
        combo = (a_kind, b_kind)
        if combo not in combos:
            continue
        param = {
            "a_ref": a_ref,
            "b_ref": b_ref,
            "conf": float(edge.get("confidence") or 0.75),
            "patent_ids": list(props.get("patent_ids") or []),
            "patent_count": int(props.get("patent_count") or 0),
            "active_after": _normalize_edge_date(props.get("active_after")),
            "last_confirmed": _normalize_edge_date(props.get("last_confirmed") or props.get("active_after")),
            "node_a_kind": a_kind,
            "node_b_kind": b_kind,
            "node_a_display_name": str(props.get("node_a_display_name") or a_ref),
            "node_b_display_name": str(props.get("node_b_display_name") or b_ref),
        }
        combos[combo].append(param)
        params.append(param)

    def _run_write(match_sql: str, items: list[dict]) -> None:
        if not items:
            return
        session.run(
            f"""
            UNWIND $batch AS p
            {match_sql}
            MERGE (a)-[r:PATENT_PARTNER {{source_scope: 'PATENTSVIEW_COASSIGNEE', edge_state: 'open'}}]-(b)
            ON CREATE SET r.source = 'PatentsView', r.confidence = p.conf
            ON MATCH  SET r.confidence = CASE WHEN p.conf > r.confidence THEN p.conf ELSE r.confidence END
            WITH r, p,
                 reduce(ids = coalesce(r.patent_ids, []), pid IN coalesce(p.patent_ids, []) |
                    CASE WHEN pid IN ids THEN ids ELSE ids + pid END
                 ) AS merged_patent_ids
            SET r.patent_ids = merged_patent_ids,
                r.patent_count = size(merged_patent_ids),
                r.node_a_kind = p.node_a_kind,
                r.node_b_kind = p.node_b_kind,
                r.node_a_display_name = p.node_a_display_name,
                r.node_b_display_name = p.node_b_display_name,
                r.active_after = CASE
                    WHEN p.active_after IS NULL OR p.active_after = '' THEN r.active_after
                    WHEN r.active_after IS NULL OR r.active_after = '' OR p.active_after < r.active_after THEN p.active_after
                    ELSE r.active_after
                END,
                r.last_confirmed = CASE
                    WHEN p.last_confirmed IS NULL OR p.last_confirmed = '' THEN r.last_confirmed
                    WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR p.last_confirmed > r.last_confirmed THEN p.last_confirmed
                    ELSE r.last_confirmed
                END,
                r.valid_until = NULL,
                r.retired_reason = NULL,
                r.edge_state = 'open',
                r.current_run_token = $run_token
            """,
            batch=items,
            run_token=run_token,
        ).consume()

    valid_a = _cypher_valid_company_listing_predicate("a")
    valid_b = _cypher_valid_company_listing_predicate("b")
    _run_write(
        f"MATCH (a:Company {{ticker: p.a_ref}}), (b:Company {{ticker: p.b_ref}}) WHERE {valid_a} AND {valid_b} "
        "AND (a.issuer_key IS NULL OR b.issuer_key IS NULL OR a.issuer_key <> b.issuer_key)",
        combos[("Company", "Company")],
    )
    _run_write(
        f"MATCH (a:Company {{ticker: p.a_ref}}), (b:LegalEntity {{entity_key: p.b_ref}}) WHERE {valid_a}",
        combos[("Company", "LegalEntity")],
    )
    _run_write(
        "MATCH (a:LegalEntity {entity_key: p.a_ref}), (b:LegalEntity {entity_key: p.b_ref}) "
        "WHERE a.entity_key <> b.entity_key",
        combos[("LegalEntity", "LegalEntity")],
    )
    return len(params)


def _llm_resolve_patent_assignees(
    unresolved_orgs: list[str],
    ticker_to_name: dict[str, str],
    provider: str,
    model: str,
    api_key: str,
    batch_size: int = 20,
    company_index: dict[str, Any] | None = None,
    legal_index: dict[str, Any] | None = None,
) -> dict[str, str]:
    """
    Batch-resolve unresolved patent assignee org names to graph tickers via LLM.
    Sends all names in batches of batch_size (one LLM call per batch).
    Returns dict: original_org_name -> ticker (only high-confidence matches).
    """
    if not unresolved_orgs or not provider or not model or not api_key:
        return {}
    import json
    import threading
    from pydantic import BaseModel, Field
    from llm_utils import call_structured_llm_by_provider
    from llm_utils import _call_structured_llm_with_critical_guard as _scl_guarded

    class _PatentAssigneeResolutionItem(BaseModel):
        assignee_name: str
        ticker: str | None = None

    class _PatentAssigneeBatchResolution(BaseModel):
        results: list[_PatentAssigneeResolutionItem] = Field(default_factory=list)

    eligible_orgs = [
        str(org or "").strip()
        for org in unresolved_orgs
        if str(org or "").strip() and not _patentsview_is_excluded_entity_name(org)
    ]
    excluded_count = len(unresolved_orgs) - len(eligible_orgs)
    if not eligible_orgs:
        if excluded_count:
            _log(
                f"Phase 10: PatentsView LLM skipped {excluded_count} excluded academic assignee name(s).",
                "white",
            )
        return {}

    company_index = company_index or _build_patentsview_company_resolution_index(ticker_to_name)
    exact_to_ticker = company_index["exact_to_ticker"]
    norm_to_ticker = company_index["norm_to_ticker"]
    first_word_index = company_index["first_word_index"]
    company_name_to_ticker = company_index["company_name_to_ticker"]
    ticker_set = company_index["ticker_set"]
    legal_index = legal_index or {}
    exact_to_entity = legal_index.get("exact_to_entity") or {}
    norm_to_entity = legal_index.get("norm_to_entity") or {}
    entity_key_to_ancestor_tickers = legal_index.get("entity_key_to_ancestor_tickers") or {}

    def _candidate_tickers_for_batch(batch: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        candidate_limit = min(40, max(16, len(batch) * 3))

        def _add(ticker: str) -> None:
            clean = str(ticker or "").strip().upper()
            if not clean or clean in seen or clean not in ticker_set:
                return
            seen.add(clean)
            out.append(clean)

        for org in batch:
            org_lower = str(org or "").strip().lower()
            if not org_lower:
                continue
            exact = exact_to_ticker.get(org_lower)
            if exact:
                _add(exact)
            norm = _patentsview_heavy_name(org)
            if norm:
                exact_norm = norm_to_ticker.get(norm)
                if exact_norm:
                    _add(exact_norm)
                words = norm.split()
                if words:
                    for candidate_norm, candidate_words, ticker in first_word_index.get(words[0], []):
                        prefix_len = min(len(words), len(candidate_words))
                        has_prefix = prefix_len >= 2 and words[:prefix_len] == candidate_words[:prefix_len]
                        token_overlap = len(set(words) & set(candidate_words))
                        same_first_word = bool(words and candidate_words and words[0] == candidate_words[0] and len(words[0]) >= 4)
                        if has_prefix or token_overlap >= 2 or same_first_word:
                            _add(ticker)
                        if len(out) >= candidate_limit:
                            return out
                entity_key = exact_to_entity.get(org_lower) or norm_to_entity.get(norm)
                if entity_key:
                    for ticker in entity_key_to_ancestor_tickers.get(entity_key, []):
                        _add(ticker)
                        if len(out) >= candidate_limit:
                            return out
        return out

    # Keep batches small to avoid long-context JSON truncation; 0/negative values would break range().
    max_batch = max(1, PATENTSVIEW_LLM_BATCH_SIZE)
    batch_size = max(1, min(int(batch_size), max_batch))
    if (provider or "").strip().lower() == "gemini":
        batch_size = min(batch_size, 8)

    result: dict[str, str] = {}
    batches: list[list[str]] = [
        eligible_orgs[i:i + batch_size]
        for i in range(0, len(eligible_orgs), batch_size)
    ]
    total_batches = len(batches)
    workers = max(1, min(int(PATENTSVIEW_LLM_MAX_WORKERS), total_batches))
    if (provider or "").strip().lower() == "gemini" and workers > 1:
        workers = 1
        _log(
            "Phase 10: PatentsView LLM forcing single-worker mode for Gemini structured output to avoid cross-thread event-loop errors.",
            "yellow",
        )
    heartbeat_sec = max(5, int(PATENTSVIEW_LLM_HEARTBEAT_SEC))
    llm_start = time.monotonic()
    _log(
        f"Phase 10: PatentsView LLM name resolution — {len(eligible_orgs)} eligible unresolved orgs, "
        f"{total_batches} LLM call(s) (batch_size={batch_size}, workers={workers}) "
        f"[provider={provider!r}, model={model!r}, timeout={PATENTSVIEW_LLM_TIMEOUT_SEC}s, retries={PATENTSVIEW_LLM_RETRIES}, "
        f"candidate_mode='dynamic', excluded={excluded_count}, heartbeat={heartbeat_sec}s]...",
        "cyan",
    )

    def _run_one(batch_num: int, batch: list[str], split_depth: int = 0) -> dict[str, str]:
        thread_name = threading.current_thread().name
        batch_start = time.monotonic()
        candidate_tickers = _candidate_tickers_for_batch(batch)
        if not candidate_tickers:
            _log(
                f"Phase 10: LLM batch {batch_num}/{total_batches} skipped (no plausible public-company candidates).",
                "white",
            )
            return {}
        target_companies_json = json.dumps(
            {ticker: ticker_to_name[ticker] for ticker in candidate_tickers if ticker in ticker_to_name},
            ensure_ascii=False,
        )
        company_ref_chars = len(target_companies_json)
        if PATENTSVIEW_LLM_LOG_SAMPLE_NAMES:
            sample = ", ".join(repr(x) for x in batch[:2])
            _log(
                f"Phase 10: LLM batch {batch_num}/{total_batches} start (thread={thread_name}, names={len(batch)}, "
                f"candidates={len(candidate_tickers)}, retry={split_depth}; sample={sample})",
                "white",
            )
        else:
            _log(
                f"Phase 10: LLM batch {batch_num}/{total_batches} start (thread={thread_name}, names={len(batch)}, "
                f"candidates={len(candidate_tickers)}, retry={split_depth})",
                "white",
            )
        assignees_json = json.dumps(batch, ensure_ascii=False)
        batch_name_set = set(batch)
        prompt = (
            "Task: map patent assignee organizations to stock tickers.\n"
            "Return a structured object with a single `results` list.\n"
            "Each item must contain `assignee_name` and `ticker`.\n"
            "Only use assignee names from ASSIGNEES_JSON.\n"
            "Only use ticker symbols from TARGET_COMPANIES_JSON as values, or null when uncertain.\n"
            "Do not add commentary.\n\n"
            f"TARGET_COMPANIES_JSON:\n{target_companies_json}\n\n"
            f"ASSIGNEES_JSON:\n{assignees_json}\n"
        )
        prompt_chars = len(prompt)
        call_start = time.monotonic()
        # TODO: thread `config` into _llm_resolve_patent_assignees to attribute
        # critical-guard failures back to the originating backtest/instance.
        structured = _scl_guarded(
            provider,
            api_key,
            model,
            prompt,
            _PatentAssigneeBatchResolution,
            attribution_keys={"call_site": "nexus_graph.patent_assignee_resolution"},
            system_prompt=(
                "You map patent assignee organizations to public-company stock tickers. "
                "Return a structured object with a single `results` list. "
                "Each result item must contain `assignee_name` from the batch and `ticker` as an exact target-company ticker or null."
            ),
            max_output_tokens=0,
            timeout_sec=PATENTSVIEW_LLM_TIMEOUT_SEC,
            retries=PATENTSVIEW_LLM_RETRIES,
            output_retries=max(1, PATENTSVIEW_LLM_RETRIES),
            temperature=0.0,
        )
        call_elapsed = time.monotonic() - call_start
        if not structured:
            _log(
                f"Phase 10: LLM batch {batch_num}/{total_batches} empty structured response (model={model!r}). "
                f"If this repeats, try increasing PATENTSVIEW_LLM_RETRIES, increasing "
                f"PATENTSVIEW_LLM_TIMEOUT_SEC, or lowering PATENTSVIEW_LLM_BATCH_SIZE.",
                "yellow",
            )
            total_elapsed = time.monotonic() - batch_start
            _log(
                f"Phase 10: LLM batch {batch_num}/{total_batches} end (thread={thread_name}) "
                f"[structured=1, call={call_elapsed:.1f}s, total={total_elapsed:.1f}s, prompt_chars={prompt_chars}]",
                "yellow",
            )
            return {}
        validate_start = time.monotonic()
        try:
            parsed = list(getattr(structured, "results", []) or [])
            if not isinstance(parsed, list):
                validate_elapsed = time.monotonic() - validate_start
                total_elapsed = time.monotonic() - batch_start
                _log(
                    f"Phase 10: LLM batch {batch_num}/{total_batches} end (thread={thread_name}) "
                    f"[structured=1, call={call_elapsed:.1f}s, validate={validate_elapsed:.2f}s, total={total_elapsed:.1f}s, "
                    f"prompt_chars={prompt_chars}, parsed_type={type(parsed).__name__}]",
                    "yellow",
                )
                return {}
            out: dict[str, str] = {}
            for item in parsed:
                if not isinstance(item, _PatentAssigneeResolutionItem):
                    continue
                org = str(item.assignee_name or "").strip()
                if org not in batch_name_set:
                    continue
                ticker = item.ticker
                if ticker and isinstance(ticker, str):
                    val = ticker.strip()
                    t = val.upper()
                    if t in ticker_set:
                        out[org] = t
                        continue
                    by_name = company_name_to_ticker.get(val.lower())
                    if by_name:
                        out[org] = by_name
                        continue
                    first_token = val.split()[0].strip("()[],.;:") if val.split() else ""
                    token_ticker = first_token.upper()
                    if token_ticker in ticker_set:
                        out[org] = token_ticker
            validate_elapsed = time.monotonic() - validate_start
            total_elapsed = time.monotonic() - batch_start
            output_chars = len(json.dumps([item.model_dump() for item in parsed], ensure_ascii=False))
            if not out and len(batch) > 1:
                split_point = max(1, len(batch) // 2)
                if split_point < len(batch):
                    _log(
                        f"Phase 10: LLM batch {batch_num}/{total_batches} returned no usable mappings "
                        f"(json_chars={output_chars}, parsed={len(parsed)}); retrying smaller sub-batches.",
                        "yellow",
                    )
                    merged: dict[str, str] = {}
                    merged.update(_run_one(batch_num, batch[:split_point], split_depth=split_depth + 1))
                    merged.update(_run_one(batch_num, batch[split_point:], split_depth=split_depth + 1))
                    return merged
            _log(
                f"Phase 10: LLM batch {batch_num}/{total_batches} end (thread={thread_name}) "
                f"[structured=1, call={call_elapsed:.1f}s, validate={validate_elapsed:.2f}s, total={total_elapsed:.1f}s, "
                f"prompt_chars={prompt_chars}, ref_chars={company_ref_chars}, json_chars={output_chars}, parsed={len(parsed)}, accepted={len(out)}]",
                "white" if out else "yellow",
            )
            return out
        except Exception:
            validate_elapsed = time.monotonic() - validate_start
            total_elapsed = time.monotonic() - batch_start
            _log(
                f"Phase 10: LLM batch {batch_num}/{total_batches} end (thread={thread_name}) "
                f"[structured=1, call={call_elapsed:.1f}s, validate={validate_elapsed:.2f}s, total={total_elapsed:.1f}s, "
                f"prompt_chars={prompt_chars}] (validation error)",
                "yellow",
            )
            return {}

    if workers <= 1:
        for i, batch in enumerate(batches, start=1):
            result.update(_run_one(i, batch))
    else:
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        completed = 0
        last_progress_log = llm_start
        last_rethink_update = llm_start

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pv-llm") as ex:
            futures = {ex.submit(_run_one, i, batch): i for i, batch in enumerate(batches, start=1)}
            pending = set(futures.keys())
            while pending:
                done, pending = wait(pending, timeout=heartbeat_sec, return_when=FIRST_COMPLETED)
                if not done:
                    elapsed = time.monotonic() - llm_start
                    _log(
                        f"Phase 10: LLM progress heartbeat — {completed}/{total_batches} done, "
                        f"{len(pending)} in-flight, {elapsed:.0f}s elapsed",
                        "white",
                    )
                    if _nexus_rethink_conn is not None and (time.monotonic() - last_rethink_update) >= 10.0:
                        last_rethink_update = time.monotonic()
                        _phase11_report_progress(
                            _nexus_rethink_conn,
                            substep=0,
                            current=completed,
                            total=total_batches,
                            stage_message=f"PatentsView LLM resolving... {completed}/{total_batches} batches",
                        )
                    continue

                for fut in done:
                    batch_num = futures.get(fut, 0)
                    try:
                        out = fut.result()
                    except Exception as e:
                        _log(f"Phase 10: LLM batch {batch_num}/{total_batches} raised: {e}", "yellow")
                        out = {}
                    if out:
                        result.update(out)
                    completed += 1

                now = time.monotonic()
                if (now - last_progress_log) >= 10.0 or completed == total_batches:
                    last_progress_log = now
                    elapsed = now - llm_start
                    rate = completed / elapsed if elapsed > 0 else 0.0
                    eta = (total_batches - completed) / rate if rate > 0 else 0.0
                    _log(
                        f"Phase 10: LLM progress — {completed}/{total_batches} batches done, "
                        f"{len(result)} matches so far, {elapsed:.0f}s elapsed, ~{eta:.0f}s remaining",
                        "white",
                    )
                    if _nexus_rethink_conn is not None and (now - last_rethink_update) >= 10.0:
                        last_rethink_update = now
                        _phase11_report_progress(
                            _nexus_rethink_conn,
                            substep=0,
                            current=completed,
                            total=total_batches,
                            stage_message=f"PatentsView LLM resolving... {completed}/{total_batches} batches",
                        )

    matched = len(result)
    total_elapsed = time.monotonic() - llm_start
    _log(
        f"Phase 10: PatentsView LLM resolved {matched}/{len(eligible_orgs)} eligible org names "
        f"in {total_elapsed:.0f}s (workers={workers}, batches={total_batches}).",
        "white",
    )
    return result


def _resolve_patentsview_to_edges(
    patents: list,
    ticker_to_name: dict[str, str],
    extra_resolution: dict[str, str] | None = None,
    near_miss_out: list | None = None,
) -> list[EdgeResult]:
    """Turn raw patents list (patent_id, assignees) into PATENT_PARTNER edges. No API.
    extra_resolution: org_name -> ticker dict from LLM fallback, checked last in _resolve().
    near_miss_out: if provided and extra_resolution is None, unresolved orgs from near-miss patents
    (patents with >=1 resolved assignee but other unresolved ones) are appended here for LLM follow-up.
    """
    import re
    pair_meta: dict[tuple[str, str], dict[str, object]] = {}
    # Require whitespace or comma/dot before suffix to avoid stripping mid-word
    # (e.g., "agco" must not match "co"; "archer-daniels" must not match "as")
    _sfx_pv = re.compile(
        r'(?:\s+|\s*[,\.]\s*)(incorporated|corporation|company|limited|'
        r'inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|l\.l\.c\.?|plc\.?|holdings?|group|enterprises?|'
        r'gmbh|ag|sa|sas|ab|bv|nv|se|oy|oyj|ltda|spa|srl|sl|a\.s\.|aps|kk)\s*$',
        re.I
    )
    def _pv_heavy(name: str) -> str:
        n = name.lower().strip()
        n = re.sub(r'^the\s+', '', n)
        n = re.sub(r'\s+common\s+stock\b.*$', '', n)
        n = re.sub(r'\s+american\s+depositary\b.*$', '', n)
        n = re.sub(r'\s+ordinary\s+shares?\b.*$', '', n)
        n = re.sub(r'\s*\([^)]*\)', ' ', n)                    # strip "(CHINA)", "(BEIJING)" etc.
        n = re.sub(r'\b(kabushiki\s+kaisha|kaisha)\b', ' ', n) # Japanese KK entity suffix
        n = re.sub(r'[,\.&\'\+\/\-]+', ' ', n)                 # punct/hyphens/plus → space (ARCHER-DANIELS-MIDLAND, BAUSCH+LOMB)
        for _ in range(4):
            new = _sfx_pv.sub('', n).strip()
            if new == n: break
            n = new
        return re.sub(r'\s+', ' ', n).strip()

    # Build heavy-norm lookup from graph companies
    norm_to_ticker: dict[str, str] = {}
    exact_to_ticker: dict[str, str] = {}
    # Narrow debt-instrument filter for norm/exact dicts: only excludes pure bonds/notes (e.g. GPJA
    # "Georgia Power...Subordinated Notes due 2077"). Does NOT exclude ADR or common-stock-appended
    # companies like HMC, TM, VIR that are legitimate operating companies.
    _debt_skip = re.compile(r'(\bsubordinated\b|notes\s+due)', re.I)
    # Non-corporate entity keywords: assignee names containing these are never public companies.
    _non_corp_re = re.compile(
        r'\b(university|universities|college|hospital|school\s+of|national\s+lab(?:oratory)?)\b',
        re.I,
    )
    for t, n in ticker_to_name.items():
        if not _debt_skip.search(n):
            exact_to_ticker[n.lower().strip()] = t
        h = _pv_heavy(n)
        if h and len(h) >= 2:
            if not _debt_skip.search(n):
                norm_to_ticker[h] = t

    _extra = extra_resolution or {}

    def _resolve(name: str) -> str | None:
        # Skip non-corporate entities (universities, hospitals, schools) — they're never listed companies.
        if _non_corp_re.search(name):
            return None
        nl = name.lower().strip()
        # exact match
        if nl in exact_to_ticker:
            return exact_to_ticker[nl]
        # heavy-norm exact
        h = _pv_heavy(name)
        if h in norm_to_ticker:
            return norm_to_ticker[h]
        # LLM fallback: check extra_resolution by original name (case-insensitive key lookup)
        if _extra:
            t = _extra.get(name) or _extra.get(name.upper()) or _extra.get(name.lower())
            if t:
                return t
        return None

    patent_assignees: dict[str, list[str]] = {}
    patent_dates: dict[str, str] = {}
    for pat in patents:
        pid = pat.get("patent_id", "")
        patent_dates[pid] = _normalize_edge_date(pat.get("patent_date"))
        for assignee in (pat.get("assignees") or []):
            org = (assignee.get("assignee_organization") or "").strip()
            if org:
                patent_assignees.setdefault(pid, []).append(org)

    # Diagnostic counters
    total_assignees = 0
    resolved_count = 0
    patents_with_2plus_resolved = 0
    sample_unresolved: list[str] = []
    # Near-miss: unresolved orgs from patents that have >=1 other resolved assignee
    # (candidates for LLM follow-up; only populated when extra_resolution is None)
    near_miss_unresolved: set[str] = set()

    for pid, orgs in patent_assignees.items():
        total_assignees += len(orgs)
        resolved = [(_resolve(o), o) for o in orgs]
        for t, o in resolved:
            if t:
                resolved_count += 1
            elif len(sample_unresolved) < 15:
                sample_unresolved.append(o)
        unresolved_orgs_here = [o for t, o in resolved if not t]
        resolved = [(t, o) for t, o in resolved if t]
        # Deduplicate resolved tickers (multiple assignee names can resolve to same ticker)
        unique_tickers = list(dict.fromkeys(t for t, _ in resolved))
        if len(unique_tickers) >= 2:
            patents_with_2plus_resolved += 1
        # Collect near-miss orgs for LLM (only on the base pass without extra_resolution)
        if not _extra and len(unique_tickers) >= 1 and unresolved_orgs_here:
            near_miss_unresolved.update(unresolved_orgs_here)
        for i in range(len(unique_tickers)):
            for j in range(i + 1, len(unique_tickers)):
                ta = unique_tickers[i]
                tb = unique_tickers[j]
                key = (min(ta, tb), max(ta, tb))
                meta = pair_meta.setdefault(key, {
                    "patent_ids": [],
                    "active_after": "",
                    "last_confirmed": "",
                })
                if pid and pid not in meta["patent_ids"]:
                    meta["patent_ids"].append(pid)
                patent_date = patent_dates.get(pid, "")
                if patent_date:
                    if not meta["active_after"] or patent_date < meta["active_after"]:
                        meta["active_after"] = patent_date
                    if not meta["last_confirmed"] or patent_date > meta["last_confirmed"]:
                        meta["last_confirmed"] = patent_date

    edges: list[EdgeResult] = []
    for (ta, tb), meta in sorted(pair_meta.items()):
        patent_ids = list(meta.get("patent_ids") or [])
        edges.append({
            "node_a": ta,
            "rel":    "PATENT_PARTNER",
            "node_b": tb,
            "confidence": 0.75,
            "source": "PatentsView",
            "properties": {
                "patent_ids": patent_ids,
                "patent_count": len(patent_ids),
                "active_after": meta.get("active_after") or "",
                "last_confirmed": meta.get("last_confirmed") or (meta.get("active_after") or ""),
            },
        })

    llm_tag = " (with LLM)" if _extra else ""
    _log(f"Phase 10: PatentsView resolution{llm_tag} — {len(patent_assignees)} patents, {total_assignees} assignees, "
         f"{resolved_count} resolved to graph tickers, {patents_with_2plus_resolved} patents with 2+ resolved → {len(edges)} edges.", "white")
    if len(edges) == 0 and sample_unresolved:
        _log(f"Phase 10: PatentsView unresolved assignee samples: {sample_unresolved[:10]}", "white")
    if near_miss_out is not None and not _extra:
        near_miss_out.extend(near_miss_unresolved)
    return edges


def _resolve_patentsview_to_entity_edges(
    patents: list,
    ticker_to_name: dict[str, str],
    legal_entity_records: list[dict] | None = None,
    extra_resolution: dict[str, str] | None = None,
    near_miss_out: list | None = None,
    company_index: dict[str, Any] | None = None,
    legal_index: dict[str, Any] | None = None,
) -> list[EdgeResult]:
    """Resolve PatentsView assignees to public companies and existing non-public LegalEntity nodes."""
    pair_meta: dict[tuple[str, str], dict[str, object]] = {}
    company_index = company_index or _build_patentsview_company_resolution_index(ticker_to_name)
    exact_to_ticker = company_index["exact_to_ticker"]
    norm_to_ticker = company_index["norm_to_ticker"]
    legal_index = legal_index or _build_patentsview_legal_entity_resolution_index(legal_entity_records)
    exact_to_entity = legal_index["exact_to_entity"]
    norm_to_entity = legal_index["norm_to_entity"]
    entity_key_to_display = legal_index["entity_key_to_display"]
    extra = extra_resolution or {}

    def _resolve(name: str) -> dict[str, str] | None:
        if _patentsview_is_excluded_entity_name(name):
            return None
        clean = str(name or "").strip()
        lower = clean.lower()
        if lower in exact_to_ticker:
            ticker = exact_to_ticker[lower]
            return {"kind": "Company", "ref": ticker, "display_name": ticker_to_name.get(ticker, ticker)}
        norm = _patentsview_heavy_name(clean)
        if norm in norm_to_ticker:
            ticker = norm_to_ticker[norm]
            return {"kind": "Company", "ref": ticker, "display_name": ticker_to_name.get(ticker, ticker)}
        if lower in exact_to_entity:
            entity_key = exact_to_entity[lower]
            return {"kind": "LegalEntity", "ref": entity_key, "display_name": entity_key_to_display.get(entity_key, entity_key)}
        if norm in norm_to_entity:
            entity_key = norm_to_entity[norm]
            return {"kind": "LegalEntity", "ref": entity_key, "display_name": entity_key_to_display.get(entity_key, entity_key)}
        if extra:
            ticker = str(extra.get(clean) or extra.get(clean.upper()) or extra.get(clean.lower()) or "").strip().upper()
            if ticker:
                return {"kind": "Company", "ref": ticker, "display_name": ticker_to_name.get(ticker, ticker)}
        return None

    def _endpoint_identity(endpoint: dict[str, str]) -> str:
        return f"{endpoint.get('kind', '')}:{endpoint.get('ref', '')}"

    def _endpoint_sort_key(endpoint: dict[str, str]) -> tuple[int, str]:
        return (0 if str(endpoint.get("kind") or "") == "Company" else 1, str(endpoint.get("ref") or ""))

    patent_assignees: dict[str, list[str]] = {}
    patent_dates: dict[str, str] = {}
    for pat in patents or []:
        pid = pat.get("patent_id", "")
        patent_dates[pid] = _normalize_edge_date(pat.get("patent_date"))
        for assignee in (pat.get("assignees") or []):
            org = (assignee.get("assignee_organization") or "").strip()
            if org:
                patent_assignees.setdefault(pid, []).append(org)

    total_assignees = 0
    resolved_count = 0
    patents_with_2plus_resolved = 0
    sample_unresolved: list[str] = []
    near_miss_unresolved: set[str] = set()

    for pid, orgs in patent_assignees.items():
        total_assignees += len(orgs)
        resolved_pairs = [(_resolve(org), org) for org in orgs]
        for endpoint, org in resolved_pairs:
            if endpoint:
                resolved_count += 1
            elif len(sample_unresolved) < 15:
                sample_unresolved.append(org)
        unresolved_orgs_here = [
            org for endpoint, org in resolved_pairs
            if not endpoint and not _patentsview_is_excluded_entity_name(org)
        ]
        unique_endpoint_map: dict[str, dict[str, str]] = {}
        for endpoint, _org in resolved_pairs:
            if endpoint:
                unique_endpoint_map.setdefault(_endpoint_identity(endpoint), endpoint)
        unique_endpoints = sorted(unique_endpoint_map.values(), key=_endpoint_sort_key)
        if len(unique_endpoints) >= 2:
            patents_with_2plus_resolved += 1
        if not extra and len(unique_endpoints) >= 1 and unresolved_orgs_here:
            near_miss_unresolved.update(unresolved_orgs_here)
        for i in range(len(unique_endpoints)):
            for j in range(i + 1, len(unique_endpoints)):
                left = unique_endpoints[i]
                right = unique_endpoints[j]
                key = (_endpoint_identity(left), _endpoint_identity(right))
                meta = pair_meta.setdefault(key, {
                    "patent_ids": [],
                    "active_after": "",
                    "last_confirmed": "",
                    "left": left,
                    "right": right,
                })
                if pid and pid not in meta["patent_ids"]:
                    meta["patent_ids"].append(pid)
                patent_date = patent_dates.get(pid, "")
                if patent_date:
                    if not meta["active_after"] or patent_date < meta["active_after"]:
                        meta["active_after"] = patent_date
                    if not meta["last_confirmed"] or patent_date > meta["last_confirmed"]:
                        meta["last_confirmed"] = patent_date

    edges: list[EdgeResult] = []
    for _pair_key, meta in sorted(pair_meta.items()):
        patent_ids = list(meta.get("patent_ids") or [])
        left = dict(meta.get("left") or {})
        right = dict(meta.get("right") or {})
        edges.append({
            "node_a": str(left.get("ref") or ""),
            "rel": "PATENT_PARTNER",
            "node_b": str(right.get("ref") or ""),
            "confidence": 0.75,
            "source": "PatentsView",
            "properties": {
                "patent_ids": patent_ids,
                "patent_count": len(patent_ids),
                "active_after": meta.get("active_after") or "",
                "last_confirmed": meta.get("last_confirmed") or (meta.get("active_after") or ""),
                "node_a_kind": str(left.get("kind") or "Company"),
                "node_b_kind": str(right.get("kind") or "Company"),
                "node_a_display_name": str(left.get("display_name") or left.get("ref") or ""),
                "node_b_display_name": str(right.get("display_name") or right.get("ref") or ""),
            },
        })

    llm_tag = " (with LLM)" if extra else ""
    _log(
        f"Phase 10: PatentsView resolution{llm_tag} - {len(patent_assignees)} patents, {total_assignees} assignees, "
        f"{resolved_count} resolved to graph entities, {patents_with_2plus_resolved} patents with 2+ resolved -> {len(edges)} edges.",
        "white",
    )
    if len(edges) == 0 and sample_unresolved:
        _log(f"Phase 10: PatentsView unresolved assignee samples: {sample_unresolved[:10]}", "white")
    if near_miss_out is not None and not extra:
        near_miss_out.extend(near_miss_unresolved)
    return edges


def _fetch_patentsview_coassignees(
    ticker_to_name: dict[str, str],
    legal_entity_records: list[dict] | None = None,
    progress_cb=None,
    edge_cb=None,
    edge_cb_batch_size: int = 50,
) -> list[EdgeResult]:
    """
    Query PatentsView PatentSearch API for patents co-assigned to multiple companies.
    Paginates with after/size (size=1000 max per page). Requires PATENTSVIEW_API_KEY.
    Uses concurrent requests (up to 6 in-flight) with rate limiting (45 req/min).
    edge_cb: called with a list of EdgeResult dicts each time edge_cb_batch_size edges accumulate.
    """
    import json
    import requests
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cache_path = _nexus_cache_path("phase10", "patentsview_coassigned.json")
    cache_read_path = _nexus_cache_lookup_path("phase10", "patentsview_coassigned.json", "phase11")
    patents = _nexus_read_cached_json(cache_read_path, NEXUS_CACHE_DEFAULT_MAX_AGE)
    if patents is not None and isinstance(patents, list):
        _nexus_stage_cache_hit(1)
        if progress_cb:
            progress_cb(len(patents), 0, "Using cached patents")
    elif not PATENTSVIEW_API_KEY:
        _log_stage_unexpected("Phase 10: PatentsView", "PatentsView", "PATENTSVIEW_API_KEY not set; skipping (request at patentsview.org/apis/api-endpoints).")
        return []
    if patents is None or not isinstance(patents, list):
        patents = []
        try:
            import re as _re_pv
            _sfx_strip = _re_pv.compile(
                r'(?:\s+|\s*[,\.]\s*)(incorporated|corporation|company|limited|'
                r'inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|l\.l\.c\.?|plc\.?|holdings?|group|enterprises?|'
                r'gmbh|ag|sa|sas|ab|bv|nv|se|oy|oyj|ltda|spa|srl|sl|a\.s\.|aps|kk)\s*$',
                _re_pv.I
            )
            company_search_names: list[str] = []
            _skip_pv = _re_pv.compile(
                r'(depositary|preferred|cumulative|%|common\s+stock|ordinary\s+shares|'
                r'american\s+depositary|subordinated|notes\s+due)',
                _re_pv.I,
            )
            seen_names: set[str] = set()
            for _name in ticker_to_name.values():
                if _skip_pv.search(_name):
                    continue
                stripped = _sfx_strip.sub('', _name).strip()
                stripped_lower = stripped.lower()
                if stripped and 3 <= len(stripped) <= 80 and stripped_lower not in seen_names:
                    seen_names.add(stripped_lower)
                    company_search_names.append(stripped)

            name_list = company_search_names
            NAMES_PER_QUERY = 20
            PV_CONCURRENT = 6
            PV_MIN_INTERVAL = 60.0 / 45.0  # 45 req/min rate limit
            _log(f"Phase 10: Fetching PatentsView for {len(name_list)} companies "
                 f"({NAMES_PER_QUERY}/query, {PV_CONCURRENT} concurrent)...", "cyan")

            # Thread-safe state
            seen_patent_ids: set[str] = set()
            seen_lock = threading.Lock()
            patents_lock = threading.Lock()
            rate_lock = threading.Lock()
            last_request_time = [0.0]
            total_patents_found = [0]
            batches_done = [0]
            total_requests = [0]
            total_batches = (len(name_list) + NAMES_PER_QUERY - 1) // NAMES_PER_QUERY
            pv_start_time = time.monotonic()

            def _rate_wait():
                """Enforce minimum interval between API requests (45 req/min)."""
                with rate_lock:
                    now = time.monotonic()
                    elapsed = now - last_request_time[0]
                    if elapsed < PV_MIN_INTERVAL:
                        time.sleep(PV_MIN_INTERVAL - elapsed)
                    last_request_time[0] = time.monotonic()
                    total_requests[0] += 1

            def _fetch_one_batch(batch_names: list[str], batch_idx: int = 0) -> list[dict]:
                """Fetch all paginated results for one batch of company names."""
                # _text_phrase matches the whole phrase (not word-OR like _text_any), avoiding
                # false positives like "United Technologies" matching any company with "Technologies"
                q_filter = {"_or": [{"_text_phrase": {"assignees.assignee_organization": n}} for n in batch_names]}
                q_filter = {"_and": [q_filter, {"_gte": {"patent_date": "2020-01-01"}}]}
                after = None
                batch_patents = []
                max_pages = 100
                page_num = 0
                consecutive_zero = 0

                for _ in range(max_pages):
                    if total_patents_found[0] >= PATENTSVIEW_MAX_ROWS:
                        break
                    _rate_wait()
                    o = {"size": PATENTSVIEW_PAGE_SIZE, "pad_patent_id": False, "exclude_withdrawn": True}
                    if after is not None:
                        o["after"] = after
                    params = {
                        "q": json.dumps(q_filter),
                        "f": json.dumps(["patent_id", "patent_date", "assignees"]),
                        "o": json.dumps(o),
                    }
                    data = None
                    for attempt in range(3):
                        try:
                            resp = requests.get(
                                PATENTSVIEW_BASE_URL,
                                params=params,
                                headers={"X-Api-Key": PATENTSVIEW_API_KEY, "Accept": "application/json"},
                                timeout=60,
                            )
                            resp.raise_for_status()
                            data = resp.json()
                            break
                        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                            # A vanished host (DNS NameResolutionError) is permanent — don't burn
                            # ~30s of backoff per batch sleeping through it across hundreds of
                            # batches (that wasted 23 min on the 2026-06-05 run). Fail fast on it.
                            _emsg = str(e).lower()
                            _dns_dead = (
                                "nameresolutionerror" in _emsg
                                or "name or service not known" in _emsg
                                or "failed to resolve" in _emsg
                                or "nodename nor servname" in _emsg
                            )
                            if _dns_dead:
                                _log(f"Phase 10: PatentsView host unreachable (DNS) — aborting batch without retry: {e}", "yellow")
                                return batch_patents
                            if attempt < 2:
                                time.sleep(10 * (attempt + 1))
                            else:
                                _log(f"Phase 10: PatentsView batch failed after 3 attempts: {e}", "yellow")
                                return batch_patents
                        except requests.HTTPError as e:
                            if e.response is not None and e.response.status_code == 429:
                                _log("Phase 10: PatentsView 429, backing off 30s...", "yellow")
                                time.sleep(30)
                            else:
                                raise

                    page_num += 1
                    if data is None:
                        break
                    page = data.get("patents") if isinstance(data, dict) else []
                    if not isinstance(page, list) or not page:
                        break

                    new_coassigned = 0
                    with seen_lock:
                        for pat in page:
                            pid = pat.get("patent_id", "")
                            assignees = pat.get("assignees") or []
                            if len(assignees) >= 2 and pid not in seen_patent_ids:
                                seen_patent_ids.add(pid)
                                batch_patents.append(pat)
                                new_coassigned += 1

                    if page_num > 1 or len(page) >= PATENTSVIEW_PAGE_SIZE:
                        elapsed = time.monotonic() - pv_start_time
                        _log(f"Phase 10: PatentsView batch {batch_idx+1}/{total_batches} "
                             f"page {page_num}: {len(page)} results, {new_coassigned} co-assigned "
                             f"({elapsed:.0f}s elapsed, {total_requests[0]} total reqs)", "white")

                    # Early exit: too many consecutive pages with no co-assigned patents
                    if new_coassigned == 0:
                        consecutive_zero += 1
                        if PATENTSVIEW_ZERO_PAGE_EXIT > 0 and consecutive_zero >= PATENTSVIEW_ZERO_PAGE_EXIT:
                            break
                    else:
                        consecutive_zero = 0

                    if len(page) < PATENTSVIEW_PAGE_SIZE:
                        break
                    last_id = page[-1].get("patent_id")
                    if last_id is None:
                        break
                    after = last_id

                return batch_patents

            # Launch concurrent batch workers
            with ThreadPoolExecutor(max_workers=PV_CONCURRENT) as pool:
                futures = {}
                for bi, batch_start in enumerate(range(0, len(name_list), NAMES_PER_QUERY)):
                    batch_names = name_list[batch_start:batch_start + NAMES_PER_QUERY]
                    fut = pool.submit(_fetch_one_batch, batch_names, bi)
                    futures[fut] = batch_start

                for fut in as_completed(futures):
                    batch_start = futures[fut]
                    try:
                        batch_patents = fut.result()
                    except Exception as e:
                        _log(f"Phase 10: PatentsView batch at {batch_start} failed: {e}", "yellow")
                        batch_patents = []

                    if batch_patents:
                        with patents_lock:
                            patents.extend(batch_patents)
                            total_patents_found[0] = len(patents)

                    batches_done[0] += 1
                    elapsed = time.monotonic() - pv_start_time
                    if batches_done[0] % 5 == 0 or batches_done[0] == total_batches:
                        companies_done = min(batches_done[0] * NAMES_PER_QUERY, len(name_list))
                        remaining_batches = total_batches - batches_done[0]
                        rate = batches_done[0] / elapsed if elapsed > 0 else 0
                        eta = remaining_batches / rate if rate > 0 else 0
                        _log(f"Phase 10: PatentsView progress: {companies_done}/{len(name_list)} "
                             f"companies ({batches_done[0]}/{total_batches} batches), "
                             f"{total_patents_found[0]} co-assigned patents, "
                             f"{total_requests[0]} API reqs, {elapsed:.0f}s elapsed, ~{eta:.0f}s remaining", "white")
                    if progress_cb:
                        progress_cb(total_patents_found[0], 0,
                                    f"Fetching... {total_patents_found[0]} co-assigned patents")

                    if total_patents_found[0] >= PATENTSVIEW_MAX_ROWS:
                        for f in futures:
                            f.cancel()
                        break

            if patents:
                _nexus_write_cached_json(cache_path, patents)
                _nexus_stage_download(1)
            pv_elapsed = time.monotonic() - pv_start_time
            _log(f"Phase 10: PatentsView fetched {len(patents)} patents total in {pv_elapsed:.0f}s "
                 f"({total_requests[0]} API requests).", "green")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 403:
                _log_stage_error("Phase 10: PatentsView", "PatentsView API",
                                 "403 Forbidden -- check PATENTSVIEW_API_KEY", None, color="yellow")
            else:
                _log_stage_error("Phase 10: PatentsView", "PatentsView fetch", str(e), e, color="yellow")
        except Exception as e:
            _log_stage_error("Phase 10: PatentsView", "PatentsView fetch", str(e), e, color="yellow")

    # Base resolution pass — also collects near-miss unresolved org names for LLM
    near_miss: list[str] = []
    company_index = _build_patentsview_company_resolution_index(ticker_to_name)
    legal_index = _build_patentsview_legal_entity_resolution_index(legal_entity_records)
    all_edges = _resolve_patentsview_to_entity_edges(
        patents,
        ticker_to_name,
        legal_entity_records=legal_entity_records,
        near_miss_out=near_miss,
        company_index=company_index,
        legal_index=legal_index,
    )
    if edge_cb and all_edges:
        for i in range(0, len(all_edges), edge_cb_batch_size):
            edge_cb(all_edges[i:i + edge_cb_batch_size])

    # LLM fallback: resolve org names that standard heuristics couldn't match
    llm_provider, llm_model, llm_api_key = _patentsview_llm_config()
    if near_miss and llm_provider and llm_model and llm_api_key:
        unique_near_miss = list(dict.fromkeys(near_miss))  # deduplicate, preserve order
        llm_extras = _llm_resolve_patent_assignees(
            unique_near_miss,
            ticker_to_name,
            llm_provider,
            llm_model,
            llm_api_key,
            batch_size=PATENTSVIEW_LLM_BATCH_SIZE,
            company_index=company_index,
            legal_index=legal_index,
        )
        if llm_extras:
            # Already-seen (ticker_a, ticker_b) pairs from the base pass
            base_seen: set[tuple[str, str]] = {
                (min(e["node_a"], e["node_b"]), max(e["node_a"], e["node_b"]))
                for e in all_edges
            }
            llm_edges = _resolve_patentsview_to_entity_edges(
                patents,
                ticker_to_name,
                legal_entity_records=legal_entity_records,
                extra_resolution=llm_extras,
                company_index=company_index,
                legal_index=legal_index,
            )
            new_llm_edges = [
                e for e in llm_edges
                if (min(e["node_a"], e["node_b"]), max(e["node_a"], e["node_b"])) not in base_seen
            ]
            if new_llm_edges:
                _log(f"Phase 10: PatentsView LLM added {len(new_llm_edges)} new edge(s).", "green")
                # Emit new edges via streaming callback so they're written to neo4j immediately
                if edge_cb:
                    for i in range(0, len(new_llm_edges), edge_cb_batch_size):
                        edge_cb(new_llm_edges[i:i + edge_cb_batch_size])
                all_edges = all_edges + new_llm_edges
    elif near_miss and not (llm_provider and llm_model and llm_api_key):
        _log(f"Phase 10: PatentsView — {len(set(near_miss))} near-miss org names could not be resolved "
             f"(set PATENTSVIEW_LLM_PROVIDER/MODEL/API_KEY to enable LLM fallback).", "white")

    return all_edges


def phase11_patents(session):
    """Phase 10: PatentsView (PATENT_PARTNER). Batch write + RethinkDB progress."""
    _nexus_stage_reset()
    _progress(PHASE_PCT[12], "Phase 10: PatentsView co-assigned patents...", "cyan")
    if not PATENTSVIEW_ENABLED:
        _log("Phase 10: PatentsView disabled (PATENTSVIEW_ENABLED off) — the legacy "
             "search.patentsview.org API was decommissioned by USPTO (2026) and has no live "
             "REST replacement. Skipping; existing PATENT_PARTNER edges are left untouched. "
             "Set PATENTSVIEW_ENABLED=true with a working PATENTSVIEW_BASE_URL to re-enable.",
             "yellow")
        _progress(PHASE_PCT[13], "Phase 10 skipped (PatentsView disabled).", "yellow")
        return
    conn = _nexus_rethink_conn
    valid_c = _cypher_valid_company_listing_predicate("c")

    r = session.run(f"""
        MATCH (c:Company)
        WHERE c.name IS NOT NULL AND {valid_c}
        RETURN c.ticker AS ticker, c.name AS name
        LIMIT $lim
    """, lim=GRAPH_NEXUS_MAX_COMPANIES)
    ticker_to_name = {
        rec.get("ticker"): rec.get("name")
        for rec in r
        if rec.get("ticker") and rec.get("name")
    }
    legal_entity_records = _load_patentsview_legal_entities(session)
    if legal_entity_records:
        _log(f"Phase 10: loaded {len(legal_entity_records)} existing LegalEntity candidate nodes for private patent assignees.", "white")

    def _pat_progress(n: int, _total: int, msg: str) -> None:
        if conn:
            _phase11_report_progress(conn, 0, n, 0, msg, progress_pct_override=PHASE_PCT[12] + 0.25 * (PHASE_PCT[13] - PHASE_PCT[12]))

    pat_created = [0]
    pat_closed = [0]
    pat_close_date = [""]
    pat_run_token = f"phase10_patent:{uuid4().hex}"

    def _pat_edge_cb(batch_edges: list) -> None:
        pat_created[0] += _phase10_write_patent_partner_edges(session, batch_edges, pat_run_token)
        for edge in batch_edges:
            aa = _normalize_edge_date((edge.get("properties") or {}).get("active_after"))
            if not aa:
                continue
            if not pat_close_date[0]:
                pat_close_date[0] = aa
            else:
                pat_close_date[0] = max(pat_close_date[0], aa)
        _log(f"Phase 10: Wrote {len(batch_edges)} PATENT_PARTNER edges ({pat_created[0]} total).", "white")
        if conn:
            _phase11_report_progress(conn, 0, pat_created[0], 0, f"PatentsView: {pat_created[0]} edges written")

    patent_edges = _fetch_patentsview_coassignees(
        ticker_to_name,
        legal_entity_records=legal_entity_records,
        progress_cb=_pat_progress,
        edge_cb=_pat_edge_cb,
        edge_cb_batch_size=50,
    )
    _log(f"Phase 10: {pat_created[0]} PATENT_PARTNER edges from PatentsView.", "white")
    if pat_created[0] > 0:
        pat_rec = session.run("""
            MATCH ()-[r:PATENT_PARTNER {source_scope: 'PATENTSVIEW_COASSIGNEE', edge_state: 'open'}]-()
            WHERE coalesce(r.current_run_token, '') <> $run_token
            SET r.edge_state = 'closed',
                r.valid_until = CASE
                    WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                    WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                    ELSE $close_date
                END,
                r.retired_reason = 'superseded',
                r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
                r.current_run_token = NULL
            RETURN count(r) AS closed
        """, run_token=pat_run_token, close_date=pat_close_date[0] or datetime.now(timezone.utc).date().isoformat()).single()
        pat_closed[0] = int((pat_rec or {}).get("closed") or 0)
    try:
        _sync_graph_edge_intervals(session, "PATENT_PARTNER", source_scope="PATENTSVIEW_COASSIGNEE", directed=False)
    except Exception as e:
        _log_stage_error("Phase 10: PatentsView", "sync interval history", str(e), e, color="yellow")
    try:
        retired_patent = _retire_relationships(
            session,
            "PATENT_PARTNER",
            "coalesce(r.source_scope, '') = ''",
            directed=False,
            retired_reason="legacy_replaced",
        )
        if retired_patent:
            _log(f"Phase 10: retired {retired_patent} legacy PATENT_PARTNER edge(s) after replacement.", "green")
    except Exception as e:
        _log_stage_error("Phase 10: PatentsView", "retire legacy interval edges", str(e), e, color="yellow")
    if pat_closed[0]:
        _log(f"Phase 10: closed {pat_closed[0]} superseded PATENT_PARTNER interval(s).", "green")

    _progress(PHASE_PCT[13], f"Phase 10 done: {pat_created[0]} patent edges.", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 11: 8-K material agreements
# ──────────────────────────────────────────────────────────────────────────────
# Resources: sec_edgar_supply_chain.scrape_8k_agreements. It uses:
#   GET https://data.sec.gov/submissions/CIK{cik}.json → filings.recent (form, accessionNumber, primaryDocument, filingDate).
#   Filter form=8-K, build document URL: https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{primaryDocument}.
#   GET each 8-K document HTML → parse for Item 1.01 / material agreement → extract org names → resolve to tickers.
# Flow: For each Company with CIK → get 8-K list from submissions → fetch 8-K docs → extract agreement parties → STRATEGIC_PARTNER edges.
# ──────────────────────────────────────────────────────────────────────────────
def phase12_8k_agreements(session):
    """Phase 11: Run 8-K scraper for all companies with CIK; batch-write STRATEGIC_PARTNER; report progress to RethinkDB."""
    _nexus_stage_reset()
    _progress(PHASE_PCT[13], "Phase 11: 8-K material agreement strategic partnerships...", "cyan")
    history_start_date = _nexus_historical_start_date()
    phase_manifest = _nexus_phase_manifest("phase12")
    ignore_existing_coverage = _phase12_should_ignore_existing_coverage(
        session,
        phase_manifest,
        history_start_date,
    )
    if ignore_existing_coverage and history_start_date:
        _log(
            "Phase 11: bootstrap coverage is incomplete in the current graph; rebuilding historical 8-K windows from cached filings.",
            "white",
        )
    try:
        sec_rows = _fetch_sec_company_ticker_rows()
        stats = _revalidate_company_ciks_against_sec(session, sec_rows=sec_rows)
        if stats["cleared"]:
            _log(
                "Phase 11: removed "
                f"{stats['cleared']} ticker/CIK mismatches before 8-K scraping.",
                "yellow",
            )
    except Exception as e:
        _log_stage_error("Phase 11: 8-K agreements", "revalidate SEC ticker identities", str(e), e, color="yellow")
    conn = _nexus_rethink_conn

    cache_path = _nexus_cache_path("phase12", "8k_edges.json")
    edges: list[dict] = []
    created = 0
    today = datetime.now(timezone.utc).date().isoformat()
    # Phase 12 8-K rolling 365-day scrape - 1-day TTL so new 8-Ks are discovered daily.
    cached = _nexus_read_cached_json(cache_path, _nexus_cache_max_age("phase12_8k"))
    stage_partner_pairs: set[tuple[str, str]] = set()
    raw_8k_matches = [0]
    # Observability holders (function-scope so the final log can't NameError): lets us tell
    # a legit "quiet incremental window" (filings scanned > 0, 0 matches) apart from a SILENT
    # SEC-fetch failure (0 filings scanned). None = non-historical / not captured.
    filings_scanned = [None]
    scan_window = [("", "")]

    def _write_batch_to_neo4j(batch_edges: list) -> tuple[set[tuple[str, str]], int]:
        """Write a batch of edges to Neo4j. Returns (matched unique pair keys, raw valid row count)."""
        params, _candidate_pair_keys, raw_valid_count = _phase11_prepare_8k_partner_batch(batch_edges)
        if not params:
            return set(), raw_valid_count
        valid_a = _cypher_valid_company_listing_predicate("a")
        valid_b = _cypher_valid_company_listing_predicate("b")
        query = """
            UNWIND $batch AS p
            MATCH (a:Company {ticker: p.sup}), (b:Company {ticker: p.cust})
            WHERE __VALID_A__
              AND __VALID_B__
              AND (a.issuer_key IS NULL OR b.issuer_key IS NULL OR a.issuer_key <> b.issuer_key)
              AND (a.sic_code IS NULL OR toInteger(a.sic_code) < 6000 OR toInteger(a.sic_code) > 6999)
              AND (b.sic_code IS NULL OR toInteger(b.sic_code) < 6000 OR toInteger(b.sic_code) > 6999)
            MERGE (a)-[r:STRATEGIC_PARTNER {source_scope: 'SEC_8K_ITEM_1_01', edge_state: 'open'}]-(b)
            ON CREATE SET r.source = '8-K Item 1.01', r.confidence = p.conf, r.since = $today,
                          r.last_confirmed = p.last_confirmed, r.active_after = p.active_after,
                          r.valid_until = NULL, r.retired_reason = NULL
            ON MATCH  SET r.confidence = CASE WHEN p.conf > r.confidence THEN p.conf ELSE r.confidence END,
                          r.last_confirmed = CASE
                              WHEN p.last_confirmed IS NULL OR p.last_confirmed = '' THEN r.last_confirmed
                              WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR p.last_confirmed > r.last_confirmed THEN p.last_confirmed
                              ELSE r.last_confirmed
                          END,
                          r.active_after = CASE
                              WHEN p.active_after IS NULL OR p.active_after = '' THEN r.active_after
                              WHEN r.active_after IS NULL OR r.active_after = '' OR p.active_after < r.active_after THEN p.active_after
                              ELSE r.active_after
                          END,
                          r.valid_until = NULL, r.retired_reason = NULL, r.edge_state = 'open'
            RETURN collect(DISTINCT CASE
                WHEN a.ticker < b.ticker THEN [a.ticker, b.ticker]
                ELSE [b.ticker, a.ticker]
            END) AS pair_keys
        """
        query = query.replace("__VALID_A__", valid_a).replace("__VALID_B__", valid_b)
        rec = session.run(query, batch=params, today=today).single() or {}
        pair_keys = {
            (str(row[0] or "").upper(), str(row[1] or "").upper())
            for row in (rec.get("pair_keys") or [])
            if isinstance(row, (list, tuple)) and len(row) == 2 and row[0] and row[1]
        }
        return pair_keys, raw_valid_count

    try:
        pruned = _prune_invalid_strategic_partner_edges(session)
        if pruned:
            _log(f"Phase 11: retired {pruned} stale/invalid STRATEGIC_PARTNER interval(s) before merge.", "green")
    except Exception as e:
        _log_stage_error("Phase 11: 8-K agreements", "prune invalid STRATEGIC_PARTNER", str(e), e, color="yellow")

    if history_start_date:
        try:
            from sec_edgar_supply_chain import scrape_8k_agreements_since, _fetch_sec_company_tickers as _sec_tickers
        except ImportError as e:
            _log_stage_error("Phase 11: 8-K agreements", "import sec_edgar_supply_chain", str(e), e, color="yellow")
            _progress(PHASE_PCT[14], "Phase 11 skipped.", "green")
            return

        valid_c = _cypher_valid_company_listing_predicate("c")
        r = session.run(f"""
            MATCH (c:Company)
            WHERE c.cik IS NOT NULL AND {valid_c}
            RETURN c.ticker AS ticker, c.cik AS cik, c.name AS name, c.canonical_name AS canonical_name,
                   c.issuer_key AS issuer_key, c.lei AS lei
            LIMIT $lim
        """, lim=GRAPH_NEXUS_MAX_COMPANIES)
        records = [
            {
                "ticker": rec.get("ticker"),
                "cik": rec.get("cik"),
                "name": rec.get("name"),
                "canonical_name": rec.get("canonical_name"),
                "issuer_key": rec.get("issuer_key"),
                "lei": rec.get("lei"),
                "is_security_instrument": False,
            }
            for rec in r
            if rec.get("ticker")
        ]
        records = _dedupe_company_records(records)
        tickers = [rec["ticker"] for rec in records]
        ticker_to_cik = {
            rec["ticker"]: str(rec["cik"]).strip().zfill(10)
            for rec in records
            if rec.get("ticker") and rec.get("cik") is not None
        }
        if not tickers:
            _log_stage_unexpected("Phase 11: 8-K agreements", "ticker list", "No tickers in graph for 8-K lookup.")
            _progress(PHASE_PCT[14], "Phase 11 skipped.", "green")
            return

        if ignore_existing_coverage:
            fetch_start, fetch_end = history_start_date, _nexus_today_iso()
        else:
            fetch_start, fetch_end = _nexus_phase_fetch_window("phase12")
        if not fetch_start:
            coverage_end = _normalize_historical_iso_date(phase_manifest.get("coverage_end")) or _nexus_today_iso()
            _nexus_update_phase_manifest(
                "phase12",
                mode="event_history",
                coverage_start=_normalize_historical_iso_date(phase_manifest.get("coverage_start")) or history_start_date,
                coverage_end=coverage_end,
                last_incremental_start=coverage_end,
                last_incremental_end=coverage_end,
                bootstrap_complete=bool(coverage_end >= _nexus_today_iso()),
                extra={
                    "filings_processed": int(phase_manifest.get("filings_processed") or 0),
                    "edge_count": int(phase_manifest.get("edge_count") or 0),
                    "latest_event_date": phase_manifest.get("latest_event_date"),
                },
            )
            _log("Phase 11: historical 8-K coverage already current.", "white")
            _progress(PHASE_PCT[14], "Phase 11 skipped (no new 8-K windows).", "green")
            return

        _8k_created = [0]
        _8k_tickers_done = [0]
        _8k_total_tickers = [len(tickers)]
        summary: dict = {}

        def _progress_cb(current: int, total: int, message: str) -> None:
            _8k_tickers_done[0] = current
            _8k_total_tickers[0] = total
            if conn:
                pct = min(1.0, current / max(total, 1))
                _phase12_report_progress(
                    conn, current, total,
                    f"Historical 8-K {current}/{total} — {_8k_created[0]} edges written",
                    progress_pct_override=PHASE_PCT[13] + pct * 0.5 * (PHASE_PCT[14] - PHASE_PCT[13]),
                )

        def _edge_cb(batch_edges: list) -> None:
            written_pair_keys, raw_valid_count = _write_batch_to_neo4j(batch_edges)
            raw_8k_matches[0] += raw_valid_count
            if written_pair_keys:
                stage_partner_pairs.update(written_pair_keys)
            _8k_created[0] = len(stage_partner_pairs)
            if conn:
                pct = min(1.0, _8k_tickers_done[0] / max(_8k_total_tickers[0], 1))
                _phase12_report_progress(
                    conn, _8k_tickers_done[0], _8k_total_tickers[0],
                    f"Written {_8k_created[0]} edges ({_8k_tickers_done[0]}/{_8k_total_tickers[0]} tickers)",
                    progress_pct_override=PHASE_PCT[13] + pct * 0.5 * (PHASE_PCT[14] - PHASE_PCT[13]),
                )

        try:
            _, title_to_ticker, _ = _sec_tickers()
            scrape_8k_agreements_since(
                tickers=tickers,
                title_to_ticker=title_to_ticker,
                start_date=fetch_start,
                end_date=fetch_end,
                ticker_to_cik=ticker_to_cik,
                progress_cb=_progress_cb,
                edge_cb=_edge_cb,
                edge_cb_batch_size=50,
                summary_cb=lambda info: summary.update(info or {}),
            )
        except Exception as e:
            _log_stage_error("Phase 11: 8-K agreements", "scrape_8k_agreements_since", str(e), e, color="yellow")

        created = _8k_created[0]
        filings_scanned[0] = int(summary.get("filings_processed") or 0)
        scan_window[0] = (fetch_start, fetch_end)
        scan_end = _normalize_historical_iso_date(summary.get("end_date")) or fetch_end or _nexus_today_iso()
        _nexus_update_phase_manifest(
            "phase12",
            mode="event_history",
            coverage_start=_normalize_historical_iso_date(phase_manifest.get("coverage_start")) or history_start_date,
            coverage_end=scan_end,
            last_incremental_start=_normalize_historical_iso_date(summary.get("start_date")) or fetch_start,
            last_incremental_end=scan_end,
            bootstrap_complete=bool(scan_end >= _nexus_today_iso()),
            extra={
                "filings_processed": int(summary.get("filings_processed") or 0),
                "edge_count": len(stage_partner_pairs),
                "latest_event_date": _normalize_historical_iso_date(summary.get("latest_filing_date")) or None,
            },
        )
        created = len(stage_partner_pairs)
    elif cached is not None and isinstance(cached, list):
        edges = cached
        _nexus_stage_cache_hit(1)
        _log("Phase 11: Using cached 8-K edges.", "white")
        if conn:
            _phase12_report_progress(conn, 0, 0, f"Using cached edges ({len(edges)} edges)", progress_pct_override=PHASE_PCT[13] + 0.5 * (PHASE_PCT[14] - PHASE_PCT[13]))
        # Write cached edges to Neo4j in batches
        valid = [e for e in edges if e.get("sup") and e.get("cust")]
        for start in range(0, len(valid), 200):
            written_pair_keys, raw_valid_count = _write_batch_to_neo4j(valid[start:start + 200])
            raw_8k_matches[0] += raw_valid_count
            if written_pair_keys:
                stage_partner_pairs.update(written_pair_keys)
            created = len(stage_partner_pairs)
            if conn:
                _phase12_report_progress(conn, created, len(valid), f"Writing... {created}/{len(valid)} edges")
    else:
        try:
            from sec_edgar_supply_chain import scrape_8k_agreements, _fetch_sec_company_tickers as _sec_tickers
        except ImportError as e:
            _log_stage_error("Phase 11: 8-K agreements", "import sec_edgar_supply_chain", str(e), e, color="yellow")
            _progress(PHASE_PCT[14], "Phase 11 skipped.", "green")
            return

        valid_c = _cypher_valid_company_listing_predicate("c")
        r = session.run(f"""
            MATCH (c:Company)
            WHERE c.cik IS NOT NULL AND {valid_c}
            RETURN c.ticker AS ticker, c.cik AS cik, c.name AS name, c.canonical_name AS canonical_name,
                   c.issuer_key AS issuer_key, c.lei AS lei
            LIMIT $lim
        """, lim=GRAPH_NEXUS_MAX_COMPANIES)
        records = [
            {
                "ticker": rec.get("ticker"),
                "cik": rec.get("cik"),
                "name": rec.get("name"),
                "canonical_name": rec.get("canonical_name"),
                "issuer_key": rec.get("issuer_key"),
                "lei": rec.get("lei"),
                "is_security_instrument": False,
            }
            for rec in r
            if rec.get("ticker")
        ]
        records = _dedupe_company_records(records)
        tickers = [rec["ticker"] for rec in records]
        ticker_to_cik = {
            rec["ticker"]: str(rec["cik"]).strip().zfill(10)
            for rec in records
            if rec.get("ticker") and rec.get("cik") is not None
        }
        if not tickers:
            _log_stage_unexpected("Phase 11: 8-K agreements", "ticker list", "No tickers in graph for 8-K lookup.")
            _progress(PHASE_PCT[14], "Phase 11 skipped.", "green")
            return

        _8k_created = [0]  # mutable counter for closure
        _8k_tickers_done = [0]
        _8k_total_tickers = [len(tickers)]

        def _progress_cb(current: int, total: int, message: str) -> None:
            _8k_tickers_done[0] = current
            _8k_total_tickers[0] = total
            if conn:
                pct = min(1.0, current / max(total, 1))
                _phase12_report_progress(
                    conn, current, total,
                    f"Checking tickers {current}/{total} — {_8k_created[0]} edges written",
                    progress_pct_override=PHASE_PCT[13] + pct * 0.5 * (PHASE_PCT[14] - PHASE_PCT[13]),
                )

        def _edge_cb(batch_edges: list) -> None:
            """Write edges to Neo4j as they are discovered during scraping."""
            written_pair_keys, raw_valid_count = _write_batch_to_neo4j(batch_edges)
            raw_8k_matches[0] += raw_valid_count
            if written_pair_keys:
                stage_partner_pairs.update(written_pair_keys)
            _8k_created[0] = len(stage_partner_pairs)
            _log(
                "Phase 11: Wrote "
                f"{len(written_pair_keys)} unique 8-K relationship(s) to Neo4j "
                f"({_8k_created[0]} total, {_8k_tickers_done[0]}/{_8k_total_tickers[0]} tickers).",
                "white",
            )
            if conn:
                pct = min(1.0, _8k_tickers_done[0] / max(_8k_total_tickers[0], 1))
                _phase12_report_progress(
                    conn, _8k_tickers_done[0], _8k_total_tickers[0],
                    f"Written {_8k_created[0]} edges ({_8k_tickers_done[0]}/{_8k_total_tickers[0]} tickers)",
                    progress_pct_override=PHASE_PCT[13] + pct * 0.5 * (PHASE_PCT[14] - PHASE_PCT[13]),
                )

        try:
            _, title_to_ticker, _ = _sec_tickers()
            scraped = scrape_8k_agreements(
                tickers=tickers,
                title_to_ticker=title_to_ticker,
                hours=365 * 24,
                max_per_ticker=10,
                progress_cb=_progress_cb,
                ticker_to_cik=ticker_to_cik,
                edge_cb=_edge_cb,
                edge_cb_batch_size=50,
            )
            if scraped:
                edges = scraped
                _nexus_write_cached_json(cache_path, edges)
                _nexus_stage_download(1)
        except Exception as e:
            _log_stage_error("Phase 11: 8-K agreements", "scrape_8k_agreements", str(e), e, color="yellow")

        created = len(stage_partner_pairs)

    try:
        _sync_graph_edge_intervals(session, "STRATEGIC_PARTNER", source_scope="SEC_8K_ITEM_1_01", directed=False)
    except Exception as e:
        _log_stage_error("Phase 11: 8-K agreements", "sync 8-K interval history", str(e), e, color="yellow")
    try:
        retired_legacy = _retire_relationships(
            session,
            "STRATEGIC_PARTNER",
            "coalesce(r.source_scope, '') = ''",
            directed=False,
            retired_reason="legacy_replaced",
        )
        if retired_legacy:
            _log(f"Phase 11: retired {retired_legacy} legacy STRATEGIC_PARTNER edge(s) after replacement.", "green")
    except Exception as e:
        _log_stage_error("Phase 11: 8-K agreements", "retire legacy STRATEGIC_PARTNER", str(e), e, color="yellow")
    live_8k_edges = _count_live_scoped_relationships(
        session,
        rel_type="STRATEGIC_PARTNER",
        source_scope="SEC_8K_ITEM_1_01",
        directed=False,
    )
    _fs = filings_scanned[0]
    if raw_8k_matches[0] == 0 and not stage_partner_pairs:
        if _fs == 0:
            _log(
                "Phase 11 WARNING: 0 raw 8-K matches AND 0 Item-1.01 filings scanned in window "
                f"[{scan_window[0][0]}..{scan_window[0][1]}] — this may be a SILENT SEC-fetch failure "
                "(check for data.sec.gov timeouts above), not a quiet period.",
                "red",
            )
        elif _fs is not None:
            _log(
                f"Phase 11: 0 new 8-K matches ({_fs} Item-1.01 filings scanned in window "
                f"[{scan_window[0][0]}..{scan_window[0][1]}]) — expected during a quiet incremental window.",
                "white",
            )
    _log(
        "Phase 11 done: "
        f"{live_8k_edges} live STRATEGIC_PARTNER edges from 8-K agreements "
        f"({len(stage_partner_pairs)} unique relationship upserts from {raw_8k_matches[0]} raw 8-K matches"
        + (f", {_fs} Item-1.01 filings scanned" if _fs is not None else "")
        + ").",
        "green",
    )
    _progress(PHASE_PCT[14], f"Phase 11 done: {live_8k_edges} live 8-K strategic partner edges.", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Phase 13: ETF Universe — fetch US ETFs, classify by theme, link to Sectors
# ──────────────────────────────────────────────────────────────────────────────

# Fallback keyword → (category, theme_keywords) for ETF name-based classification
# Used when neither etfdb category nor Polygon description is available
_FALLBACK_ETF_NAME_KEYWORDS: list[tuple[str, str, list[str]]] = [
    # Energy
    ("oil",               "Energy",                  ["oil", "energy", "petroleum"]),
    ("crude",             "Energy",                  ["oil", "crude", "energy"]),
    ("energy",            "Energy",                  ["energy", "oil"]),
    ("natural gas",       "Energy",                  ["natural gas", "energy"]),
    ("petroleum",         "Energy",                  ["oil", "petroleum", "energy"]),
    ("mlp",               "Energy",                  ["pipeline", "energy", "mlp"]),
    ("pipeline",          "Energy",                  ["pipeline", "energy"]),
    ("opec",              "Energy",                  ["oil", "opec", "energy"]),
    # Defense / Aerospace
    ("defense",           "Industrials",             ["defense", "aerospace", "military"]),
    ("aerospace",         "Industrials",             ["aerospace", "defense"]),
    ("military",          "Industrials",             ["defense", "military"]),
    # Technology
    ("technology",        "Technology",              ["technology", "tech"]),
    ("semiconductor",     "Technology",              ["semiconductor", "chip"]),
    ("semiconductor",     "Technology",              ["semiconductor"]),
    (" chip",             "Technology",              ["semiconductor", "chip"]),
    ("cloud",             "Technology",              ["cloud", "technology"]),
    ("cyber",             "Technology",              ["cybersecurity", "security"]),
    ("software",          "Technology",              ["software", "technology"]),
    ("artificial intel",  "Technology",              ["ai", "artificial intelligence", "technology"]),
    ("robotics",          "Technology",              ["robotics", "automation"]),
    ("blockchain",        "Technology",              ["blockchain", "crypto"]),
    ("crypto",            "Technology",              ["crypto", "blockchain"]),
    # Healthcare
    ("healthcare",        "Healthcare",              ["healthcare", "health"]),
    ("health",            "Healthcare",              ["healthcare", "health"]),
    ("biotech",           "Healthcare",              ["biotech", "pharma", "healthcare"]),
    ("pharma",            "Healthcare",              ["pharmaceutical", "healthcare"]),
    ("medical",           "Healthcare",              ["medical", "healthcare"]),
    ("genomic",           "Healthcare",              ["genomics", "biotech", "healthcare"]),
    # Financials
    ("financial",         "Financial Services",      ["financial", "banking"]),
    ("bank",              "Financial Services",      ["banking", "financial"]),
    ("insurance",         "Financial Services",      ["insurance", "financial"]),
    ("real estate",       "Real Estate",             ["real estate", "reit"]),
    ("reit",              "Real Estate",             ["reit", "real estate"]),
    ("mortgage",          "Real Estate",             ["mortgage", "real estate"]),
    # Commodities / Materials
    ("gold",              "Materials",               ["gold", "precious metals", "commodity"]),
    ("silver",            "Materials",               ["silver", "precious metals"]),
    ("mining",            "Materials",               ["mining", "materials"]),
    ("steel",             "Materials",               ["steel", "materials"]),
    ("copper",            "Materials",               ["copper", "mining", "materials"]),
    ("lithium",           "Materials",               ["lithium", "battery", "ev"]),
    ("battery",           "Materials",               ["battery", "lithium"]),
    ("commodity",         "Commodities",             ["commodity"]),
    ("agriculture",       "Commodities",             ["agriculture", "commodity"]),
    # Consumer
    ("consumer discret",  "Consumer Discretionary",  ["consumer", "retail"]),
    ("consumer staple",   "Consumer Staples",        ["consumer", "staples"]),
    ("retail",            "Consumer Discretionary",  ["retail", "consumer"]),
    ("e-commerce",        "Consumer Discretionary",  ["e-commerce", "retail"]),
    ("electric vehicle",  "Consumer Discretionary",  ["ev", "electric vehicle"]),
    (" ev ",              "Consumer Discretionary",  ["ev", "electric vehicle"]),
    # Utilities
    ("utilities",         "Utilities",               ["utilities", "power"]),
    ("utility",           "Utilities",               ["utilities", "power"]),
    ("clean energy",      "Utilities",               ["clean energy", "renewable", "utilities"]),
    ("solar",             "Utilities",               ["solar", "clean energy"]),
    ("wind",              "Utilities",               ["wind", "clean energy"]),
    # Infrastructure / Industrials
    ("infrastructure",    "Industrials",             ["infrastructure"]),
    ("industrial",        "Industrials",             ["industrial"]),
    ("transportation",    "Industrials",             ["transportation"]),
    ("construction",      "Industrials",             ["construction"]),
    # Bonds / Fixed Income
    ("bond",              "Bonds",                   ["bond", "fixed income"]),
    ("treasury",          "Bonds",                   ["treasury", "government", "bond"]),
    ("fixed income",      "Bonds",                   ["bond", "fixed income"]),
    ("high yield",        "Bonds",                   ["high yield", "bond"]),
    ("tips",              "Bonds",                   ["inflation", "bond", "tips"]),
    # International
    ("china",             "International",           ["china", "emerging markets"]),
    ("emerging",          "International",           ["emerging markets"]),
    ("international",     "International",           ["international"]),
    ("global",            "International",           ["global", "international"]),
    # Communications
    ("communication",     "Communications",          ["communications"]),
    ("media",             "Communications",          ["media", "communications"]),
    ("telecom",           "Communications",          ["telecom", "communications"]),
]

# Fallback map: Neo4j sector names → ETF theme keywords (for ETF_TRACKS_SECTOR relationships)
_FALLBACK_SECTOR_TO_ETF_KEYWORDS: dict[str, list[str]] = {
    "Energy":                 ["energy", "oil", "petroleum", "natural gas", "pipeline"],
    "Technology":             ["technology", "semiconductor", "chip", "cloud", "cybersecurity", "software", "ai", "robotics"],
    "Healthcare":             ["healthcare", "biotech", "pharmaceutical", "medical"],
    "Financial Services":     ["financial", "banking", "insurance"],
    "Consumer Discretionary": ["consumer", "retail", "ev", "electric vehicle"],
    "Consumer Staples":       ["consumer staples"],
    "Industrials":            ["industrial", "defense", "aerospace", "infrastructure", "transportation"],
    "Materials":              ["materials", "gold", "silver", "mining", "steel", "copper", "lithium", "commodity"],
    "Utilities":              ["utilities", "clean energy", "solar", "wind"],
    "Real Estate":            ["real estate", "reit"],
    "Communications":         ["communications", "media", "telecom"],
    "Commodities":            ["commodity", "agriculture"],
    "Bonds":                  ["bond", "treasury", "fixed income"],
    "International":          ["international", "emerging", "china", "global"],
}

_ETF_SPECIFIC_THEME_PATTERNS: list[tuple[str, str, str, list[str]]] = [
    ("natural gas",          "Energy",                 "Natural Gas",            ["natural gas", "energy"]),
    ("crude oil",            "Energy",                 "Oil",                    ["oil", "crude", "energy"]),
    ("oil",                  "Energy",                 "Oil",                    ["oil", "petroleum", "energy"]),
    ("petroleum",            "Energy",                 "Oil",                    ["oil", "petroleum", "energy"]),
    ("pipeline",             "Energy",                 "Pipelines & MLPs",       ["pipeline", "energy", "mlp"]),
    ("mlp",                  "Energy",                 "Pipelines & MLPs",       ["pipeline", "energy", "mlp"]),
    ("uranium",              "Energy",                 "Uranium",                ["uranium", "nuclear", "energy"]),
    ("solar",                "Utilities",              "Solar",                  ["solar", "clean energy", "utilities"]),
    ("wind",                 "Utilities",              "Wind",                   ["wind", "clean energy", "utilities"]),
    ("clean energy",         "Utilities",              "Clean Energy",           ["clean energy", "renewable", "utilities"]),
    ("semiconductor",        "Technology",             "Semiconductors",         ["semiconductor", "chip", "technology"]),
    ("chip",                 "Technology",             "Semiconductors",         ["semiconductor", "chip", "technology"]),
    ("cybersecurity",        "Technology",             "Cybersecurity",          ["cybersecurity", "security", "technology"]),
    ("cyber",                "Technology",             "Cybersecurity",          ["cybersecurity", "security", "technology"]),
    ("cloud",                "Technology",             "Cloud Computing",        ["cloud", "technology"]),
    ("software",             "Technology",             "Software",               ["software", "technology"]),
    ("artificial intelligence", "Technology",          "Artificial Intelligence",["ai", "artificial intelligence", "technology"]),
    ("robotics",             "Technology",             "Robotics",               ["robotics", "automation", "technology"]),
    ("blockchain",           "Technology",             "Blockchain",             ["blockchain", "crypto", "technology"]),
    ("crypto",               "Technology",             "Crypto",                 ["crypto", "blockchain", "technology"]),
    ("genomics",             "Healthcare",             "Genomics",               ["genomics", "biotech", "healthcare"]),
    ("biotech",              "Healthcare",             "Biotechnology",          ["biotech", "pharma", "healthcare"]),
    ("pharma",               "Healthcare",             "Pharmaceuticals",        ["pharmaceutical", "pharma", "healthcare"]),
    ("medical devices",      "Healthcare",             "Medical Devices",        ["medical devices", "healthcare"]),
    ("insurance",            "Financial Services",     "Insurance",              ["insurance", "financial"]),
    ("bank",                 "Financial Services",     "Banks",                  ["banking", "financial"]),
    ("fintech",              "Financial Services",     "Fintech",                ["fintech", "payments", "financial"]),
    ("payment",              "Financial Services",     "Fintech",                ["fintech", "payments", "financial"]),
    ("reit",                 "Real Estate",            "REITs",                  ["reit", "real estate"]),
    ("mortgage",             "Real Estate",            "Mortgage REITs",         ["mortgage", "real estate"]),
    ("gold",                 "Materials",              "Gold",                   ["gold", "precious metals", "materials"]),
    ("silver",               "Materials",              "Silver",                 ["silver", "precious metals", "materials"]),
    ("copper",               "Materials",              "Copper",                 ["copper", "materials", "mining"]),
    ("steel",                "Materials",              "Steel",                  ["steel", "materials"]),
    ("lithium",              "Materials",              "Lithium",                ["lithium", "battery", "materials"]),
    ("battery",              "Materials",              "Batteries",              ["battery", "lithium", "materials"]),
    ("agriculture",          "Commodities",            "Agriculture",            ["agriculture", "commodity"]),
    ("grain",                "Commodities",            "Grains",                 ["grain", "agriculture", "commodity"]),
    ("livestock",            "Commodities",            "Livestock",              ["livestock", "commodity"]),
    ("defense",              "Industrials",            "Defense",                ["defense", "aerospace", "military"]),
    ("aerospace",            "Industrials",            "Aerospace",              ["aerospace", "defense", "industrial"]),
    ("infrastructure",       "Industrials",            "Infrastructure",         ["infrastructure", "industrial"]),
    ("transportation",       "Industrials",            "Transportation",         ["transportation", "industrial"]),
    ("construction",         "Industrials",            "Construction",           ["construction", "industrial"]),
    ("electric vehicle",     "Consumer Discretionary", "Electric Vehicles",      ["electric vehicle", "ev", "consumer"]),
    ("retail",               "Consumer Discretionary", "Retail",                 ["retail", "consumer"]),
    ("consumer staples",     "Consumer Staples",       "Consumer Staples",       ["consumer staples"]),
    ("treasury",             "Bonds",                  "Treasuries",             ["treasury", "bond", "government"]),
    ("high yield",           "Bonds",                  "High Yield Bonds",       ["high yield", "bond"]),
    ("tips",                 "Bonds",                  "Inflation-Protected Bonds", ["tips", "inflation", "bond"]),
    ("government bond",      "Bonds",                  "Government Bonds",       ["government", "bond", "treasury"]),
    ("corporate bond",       "Bonds",                  "Corporate Bonds",        ["corporate", "bond"]),
    ("china",                "International",          "China",                  ["china", "international"]),
    ("europe",               "International",          "Europe",                 ["europe", "international"]),
    ("emerging market",      "International",          "Emerging Markets",       ["emerging markets", "international"]),
    ("communication",        "Communications",         "Communications",         ["communications"]),
    ("media",                "Communications",         "Media",                  ["media", "communications"]),
    ("telecom",              "Communications",         "Telecom",                ["telecom", "communications"]),
    ("dividend",             "Dividends",              "Dividend Income",        ["dividend", "income", "yield"]),
    ("growth",               "Growth",                 "Growth",                 ["growth"]),
    ("value",                "Value",                  "Value",                  ["value"]),
    ("leveraged",            "Leveraged",              "Leveraged",              ["leveraged"]),
    ("inverse",              "Inverse",                "Inverse",                ["inverse", "short"]),
]


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        value = (raw or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _extract_etf_specific_themes(text: str) -> tuple[str | None, list[str], list[str]]:
    text_lower = " " + (text or "").lower() + " "
    matched_category = None
    matched_themes: list[str] = []
    matched_keywords: list[str] = []
    for term, category, theme, keywords in _ETF_SPECIFIC_THEME_PATTERNS:
        if _etf_text_matches(text_lower, term):
            if matched_category is None:
                matched_category = category
            matched_themes.append(theme)
            matched_keywords.extend(keywords)
    return matched_category, _dedupe_preserve_order(matched_themes), _dedupe_preserve_order(matched_keywords)


def _etf_text_pattern_regex(pattern: str) -> str:
    pieces = [re.escape(piece) for piece in str(pattern or "").strip().split() if piece]
    if not pieces:
        return ""
    return r"(?<![a-z0-9])" + r"(?:[\s/&-]+)".join(pieces) + r"(?![a-z0-9])"


def _etf_text_matches(text: str, pattern: str) -> bool:
    regex = _etf_text_pattern_regex(pattern)
    if not regex:
        return False
    return re.search(regex, str(text or "").lower()) is not None


def _classify_etf_with_themes(category_text: str, name_text: str) -> tuple[str, list[str], str, list[str]]:
    broad_category, broad_keywords = _classify_from_etfdb_category(category_text, name_text)
    specific_category, themes, specific_keywords = _extract_etf_specific_themes(category_text or "")
    if not themes:
        specific_category, themes, specific_keywords = _extract_etf_specific_themes(name_text or "")
    final_category = specific_category or broad_category
    merged_keywords = _dedupe_preserve_order(list(specific_keywords) + list(broad_keywords))
    if themes:
        primary_theme = themes[0]
        theme_list = themes
    elif final_category and final_category != "Broad Market":
        primary_theme = final_category
        theme_list = [final_category]
    else:
        primary_theme = "Broad Market"
        theme_list = []
    return final_category, merged_keywords, primary_theme, theme_list


_ETF_SECTOR_CATEGORIES: set[str] = {
    "Energy",
    "Industrials",
    "Technology",
    "Healthcare",
    "Financial Services",
    "Real Estate",
    "Materials",
    "Consumer Discretionary",
    "Consumer Staples",
    "Utilities",
    "Communications",
}


def _is_etf_sector_category(category: str) -> bool:
    return str(category or "").strip() in _ETF_SECTOR_CATEGORIES


def _phase12_etf_sector_targets(etf: dict) -> list[str]:
    """
    Return the high-precision sector links for one ETF.

    We only emit ETF_TRACKS_SECTOR for equity ETFs whose resolved category is already
    a concrete sector. This intentionally avoids commodity, bond, currency, inverse,
    growth/value, and broad-market funds being cross-linked into unrelated sectors
    through loose keyword overlap.
    """
    category = str((etf or {}).get("category") or "").strip()
    if not _is_etf_sector_category(category):
        return []
    asset_class = str((etf or {}).get("asset_class") or "").strip().lower()
    if asset_class and asset_class not in {"equity", "stock"}:
        return []
    return [category]


def _etf_classify_by_name(name: str) -> tuple[str, list[str]]:
    """Classify an ETF by its name. Returns (category, keywords). Used as fallback."""
    name_lower = (" " + name.lower() + " ")
    for term, category, keywords in _FALLBACK_ETF_NAME_KEYWORDS:
        if _etf_text_matches(name_lower, term):
            return category, keywords
    # Broad market defaults
    for broad in ("s&p 500", "s&p500", "total market", "total stock", "nasdaq", "russell 2000", "wilshire"):
        if _etf_text_matches(name_lower, broad):
            return "Broad Market", ["broad market", "index", "us equity"]
    if _etf_text_matches(name_lower, "dividend"):
        return "Dividends", ["dividend", "income", "yield"]
    return "Broad Market", ["broad market", "index"]


# Fallback key ETFs to fetch holdings for (used when dynamic selection is unavailable)
_FALLBACK_KEY_ETF_TICKERS: set[str] = {
    "USO", "XLE", "OIH", "UCO", "VDE", "XOP", "UNG", "MLPA", "AMLP", "CRAK",
    "ITA", "XAR", "PPA", "DFEN",
    "XLK", "VGT", "QQQ", "SOXX", "SMH", "AIQ", "BOTZ", "ROBO", "SKYY", "CLOU", "WCLD", "IGV", "BUG", "CIBR", "HACK",
    "XLV", "VHT", "XBI", "IBB", "ARKG", "PPH", "XPH", "IHI", "XHE",
    "KBE", "KRE", "XLF", "VFH", "TBF", "KBWB", "TLT", "SHY", "HYG", "LQD", "TIP", "PDBC",
    "ITB", "XHB", "PKB", "VNQ", "IYR", "XLRE",
    "GLD", "IAU", "GDX", "SLV", "SIVR", "CPER", "COPX", "XME", "PICK", "SLX", "LIT", "BATT", "DRIV", "IDRV", "DJP",
    "XRT", "VCR", "XLP", "XLY", "IBUY", "ONLN",
    "XLU", "VPU", "PAVE", "IFRA", "IYT", "XTN",
    "EEM", "VWO", "FXI", "MCHI", "EWH",
    "BITO", "GBTC", "BLOK", "BKCH",
    "SPY", "QQQ", "IVV", "VOO", "VTI", "DIA", "IWM", "MDY",
}

# ── Dynamic ETF classification helpers ────────────────────────────────────────
# Compact pattern list for parsing etfdb.com's structured category strings
# (e.g. "Equity Energy", "Technology Equities", "Health & Biotech Equities")
# More specific patterns listed first. Each entry: (pattern, sector, keywords)
_ETFDB_CAT_PATTERNS: list[tuple[str, str, list[str]]] = [
    # Energy
    ("natural gas",            "Energy",                 ["natural gas", "energy"]),
    ("oil",                    "Energy",                 ["oil", "energy", "petroleum"]),
    ("energy",                 "Energy",                 ["energy", "oil"]),
    ("mlp",                    "Energy",                 ["pipeline", "energy", "mlp"]),
    ("pipeline",               "Energy",                 ["pipeline", "energy"]),
    # Defense / Aerospace
    ("defense",                "Industrials",            ["defense", "aerospace", "military"]),
    ("aerospace",              "Industrials",            ["aerospace", "defense"]),
    # Technology
    ("semiconductor",          "Technology",             ["semiconductor", "chip"]),
    ("artificial intelligence","Technology",             ["ai", "artificial intelligence", "technology"]),
    ("cybersecurity",          "Technology",             ["cybersecurity", "security", "technology"]),
    ("cyber",                  "Technology",             ["cybersecurity", "security", "technology"]),
    ("cloud",                  "Technology",             ["cloud", "technology"]),
    ("software",               "Technology",             ["software", "technology"]),
    ("robotics",               "Technology",             ["robotics", "automation"]),
    ("blockchain",             "Technology",             ["blockchain", "crypto"]),
    ("crypto",                 "Technology",             ["crypto", "blockchain"]),
    ("technology",             "Technology",             ["technology"]),
    # Healthcare
    ("health & biotech",       "Healthcare",             ["healthcare", "biotech", "pharma"]),
    ("genomics",               "Healthcare",             ["genomics", "biotech", "healthcare"]),
    ("biotech",                "Healthcare",             ["biotech", "pharma", "healthcare"]),
    ("pharma",                 "Healthcare",             ["pharmaceutical", "healthcare"]),
    ("medical",                "Healthcare",             ["medical", "healthcare"]),
    ("healthcare",             "Healthcare",             ["healthcare", "health"]),
    ("health",                 "Healthcare",             ["healthcare", "health"]),
    # Financials
    ("bank",                   "Financial Services",     ["banking", "financial"]),
    ("insurance",              "Financial Services",     ["insurance", "financial"]),
    ("financial",              "Financial Services",     ["financial", "banking"]),
    # Real Estate
    ("real estate",            "Real Estate",            ["real estate", "reit"]),
    ("reits",                  "Real Estate",            ["reit", "real estate"]),
    ("mortgage",               "Real Estate",            ["mortgage", "real estate"]),
    # Materials / Commodities
    ("gold",                   "Materials",              ["gold", "precious metals"]),
    ("silver",                 "Materials",              ["silver", "precious metals"]),
    ("lithium",                "Materials",              ["lithium", "battery", "ev"]),
    ("battery",                "Materials",              ["battery", "lithium"]),
    ("copper",                 "Materials",              ["copper", "mining", "materials"]),
    ("steel",                  "Materials",              ["steel", "materials"]),
    ("mining",                 "Materials",              ["mining", "materials"]),
    ("materials",              "Materials",              ["materials"]),
    ("agriculture",            "Commodities",            ["agriculture", "commodity"]),
    ("commodity",              "Commodities",            ["commodity"]),
    # Consumer
    ("electric vehicle",       "Consumer Discretionary", ["ev", "electric vehicle"]),
    ("consumer discretionary", "Consumer Discretionary", ["consumer", "retail"]),
    ("consumer staples",       "Consumer Staples",       ["consumer", "staples"]),
    ("retail",                 "Consumer Discretionary", ["retail", "consumer"]),
    # Utilities / Clean Energy
    ("clean energy",           "Utilities",              ["clean energy", "renewable"]),
    ("solar",                  "Utilities",              ["solar", "clean energy"]),
    ("wind",                   "Utilities",              ["wind", "clean energy"]),
    ("utilities",              "Utilities",              ["utilities"]),
    # Industrials / Infrastructure
    ("infrastructure",         "Industrials",            ["infrastructure"]),
    ("transportation",         "Industrials",            ["transportation"]),
    ("construction",           "Industrials",            ["construction"]),
    ("industrial",             "Industrials",            ["industrial"]),
    # Bonds / Fixed Income
    ("government bond",        "Bonds",                  ["government", "bond", "treasury"]),
    ("corporate bond",         "Bonds",                  ["corporate", "bond"]),
    ("high yield",             "Bonds",                  ["high yield", "bond"]),
    ("treasury",               "Bonds",                  ["treasury", "bond"]),
    ("tips",                   "Bonds",                  ["inflation", "tips", "bond"]),
    ("fixed income",           "Bonds",                  ["bond", "fixed income"]),
    ("bond",                   "Bonds",                  ["bond"]),
    # International
    ("china",                  "International",          ["china", "emerging markets"]),
    ("emerging market",        "International",          ["emerging markets"]),
    ("europe",                 "International",          ["europe", "international"]),
    ("pacific",                "International",          ["asia pacific", "international"]),
    ("international",          "International",          ["international"]),
    ("global",                 "International",          ["global", "international"]),
    # Communications / Media
    ("communication",          "Communications",         ["communications"]),
    ("media",                  "Communications",         ["media", "communications"]),
    ("telecom",                "Communications",         ["telecom", "communications"]),
    # Income / Dividends
    ("dividend",               "Dividends",              ["dividend", "income", "yield"]),
    # Broad Market / Style
    ("small cap",              "Broad Market",           ["small cap", "equity"]),
    ("mid cap",                "Broad Market",           ["mid cap", "equity"]),
    ("large cap",              "Broad Market",           ["large cap", "equity"]),
    ("total market",           "Broad Market",           ["broad market", "index"]),
    ("s&p 500",                "Broad Market",           ["broad market", "s&p", "index"]),
    ("growth",                 "Growth",                 ["growth"]),
    ("value",                  "Value",                  ["value"]),
    ("volatility",             "Alternatives",           ["volatility"]),
    ("alternative",            "Alternatives",           ["alternatives"]),
    ("leveraged",              "Leveraged",              ["leveraged"]),
    ("inverse",                "Inverse",                ["inverse", "short"]),
]


def _classify_from_etfdb_category(category: str, name: str) -> tuple[str, list[str]]:
    """
    Classify an ETF using etfdb's own category string (primary source),
    falling back to name-based matching (_etf_classify_by_name).
    Also tries Polygon description text if passed via the name parameter.
    Returns (canonical_sector, keywords).
    """
    cat_lower = (category or "").lower().strip()
    if cat_lower and cat_lower not in ("n/a", "none", "other", ""):
        for pattern, sector, kws in _ETFDB_CAT_PATTERNS:
            if _etf_text_matches(cat_lower, pattern):
                return sector, list(kws)
    # Fall back to name-based classification (covers Polygon-sourced ETFs)
    return _etf_classify_by_name(name)


_PHASE12_BOND_THEMES: set[str] = {
    "Bonds",
    "Corporate Bonds",
    "Government Bonds",
    "High Yield Bonds",
    "Inflation-Protected Bonds",
    "Treasuries",
}

_PHASE12_BOND_KEYWORD_TOKENS: tuple[str, ...] = (
    "bond",
    "fixed income",
    "treasury",
    "muni",
    "municipal",
    "government",
    "corporate",
    "tax-exempt",
    "high yield",
    "tips",
    "inflation-protected",
)


def _phase12_is_bond_fund(
    raw_category_text: str,
    name_text: str,
    asset_class: str,
    category: str,
    themes: list[str] | None,
) -> bool:
    if str(asset_class or "").strip().lower() == "bond":
        return True
    if str(category or "").strip() == "Bonds":
        return True
    if any(str(theme or "").strip() in _PHASE12_BOND_THEMES for theme in (themes or [])):
        return True
    combined = f"{raw_category_text or ''} {name_text or ''}".lower()
    return any(
        _etf_text_matches(combined, token)
        for token in (
            "bond",
            "bonds",
            "treasury",
            "municipal",
            "muni",
            "fixed income",
            "high yield",
            "corporate bond",
            "government bond",
            "tax-exempt",
            "tips",
        )
    )


def _phase12_sanitize_etf_classification(
    raw_category_text: str,
    name_text: str,
    asset_class: str,
    category: str,
    keywords: list[str],
    primary_theme: str,
    themes: list[str],
) -> tuple[str, list[str], str, list[str]]:
    out_category = str(category or "Broad Market").strip() or "Broad Market"
    out_keywords = _dedupe_preserve_order(list(keywords or []))
    out_themes = _dedupe_preserve_order(list(themes or []))
    out_primary = str(primary_theme or "").strip()

    if _phase12_is_bond_fund(raw_category_text, name_text, asset_class, out_category, out_themes):
        combined = f"{raw_category_text or ''} {name_text or ''}".lower()
        filtered_themes = [theme for theme in out_themes if theme in _PHASE12_BOND_THEMES]
        filtered_keywords = [
            keyword
            for keyword in out_keywords
            if any(token in str(keyword or "").lower() for token in _PHASE12_BOND_KEYWORD_TOKENS)
        ]
        if not filtered_themes:
            if _etf_text_matches(combined, "treasury"):
                filtered_themes = ["Treasuries"]
            elif _etf_text_matches(combined, "high yield"):
                filtered_themes = ["High Yield Bonds"]
            elif _etf_text_matches(combined, "corporate bond"):
                filtered_themes = ["Corporate Bonds"]
            elif _etf_text_matches(combined, "tips"):
                filtered_themes = ["Inflation-Protected Bonds"]
            elif _etf_text_matches(combined, "government bond"):
                filtered_themes = ["Government Bonds"]
            else:
                filtered_themes = ["Bonds"]
        out_category = "Bonds"
        out_themes = filtered_themes
        out_primary = filtered_themes[0]
        out_keywords = _dedupe_preserve_order((filtered_keywords or []) + ["bond", "fixed income"])

    if not out_themes and out_category and out_category != "Broad Market":
        out_primary = out_primary or out_category
    elif out_themes:
        out_primary = out_themes[0]
    else:
        out_primary = "Broad Market"
    return out_category, out_keywords, out_primary, out_themes


def _select_key_etfs_dynamic(etfs: list[dict], top_n: int = 150) -> set[str]:
    """
    Select ETFs to fetch holdings for dynamically.
    Primary: top_n by list position (etfdb screener returns sorted by AUM desc;
             Polygon list is alphabetical but still gives coverage).
    Fallback union: _FALLBACK_KEY_ETF_TICKERS ensures known theme-relevant ETFs
    are included even if they fall outside the top N.
    """
    dynamic = {e["ticker"] for e in etfs[:top_n] if e.get("ticker")}
    return dynamic | _FALLBACK_KEY_ETF_TICKERS


def _build_sector_kw_map_dynamic(etfs: list[dict]) -> dict[str, list[str]]:
    """
    Build sector → [keywords] mapping from classified ETF list.
    Used for ETF_TRACKS_SECTOR relationship matching.
    Starts from the live ETF data, then merges _FALLBACK_SECTOR_TO_ETF_KEYWORDS
    for any sectors not represented in the dynamic data.
    """
    sector_kws: dict[str, set[str]] = {}
    for e in etfs:
        sector = (e.get("category") or "Broad Market").strip()
        if not _is_etf_sector_category(sector):
            continue
        for kw in (e.get("keywords") or []):
            sector_kws.setdefault(sector, set()).add(kw)
    # Merge fallback: add sectors not seen dynamically; expand existing with fallback keywords
    result: dict[str, list[str]] = {}
    valid_fallback = {s for s in _FALLBACK_SECTOR_TO_ETF_KEYWORDS if _is_etf_sector_category(s)}
    for s in set(sector_kws) | valid_fallback:
        merged = sector_kws.get(s, set()) | set(_FALLBACK_SECTOR_TO_ETF_KEYWORDS.get(s, []))
        result[s] = sorted(merged)
    return result


_ETFDB_SCREENER_URL = "https://etfdb.com/api/screener/"
_ETFDB_ETF_BASE_URL = "https://etfdb.com/etf/"
_ETFDB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Origin": "https://etfdb.com",
    "Referer": "https://etfdb.com/etfs/",
    "X-Requested-With": "XMLHttpRequest",
}


def _etfdb_field(obj, field: str, default: str = "") -> str:
    """Safely extract text from etfdb.com screener response field (may be string or {text: ...} dict)."""
    val = obj.get(field) if isinstance(obj, dict) else None
    if val is None:
        return default
    if isinstance(val, dict):
        return str(val.get("text") or val.get("value") or default).strip()
    return str(val).strip()


def _fetch_etf_list_etfdb_with_total(max_etfs: int = 0) -> tuple[list[dict], int]:
    """
    Fetch US ETF list from etfdb.com screener API.
    Returns list of {ticker, name, category, asset_class} dicts.
    Respects rate limits with 1-second delay between pages.
    """
    import requests as _req
    out: list[dict] = []
    page = 1
    per_page = 250
    total = None
    fetch_limit = max_etfs if max_etfs and max_etfs > 0 else 0
    max_pages = ((fetch_limit // per_page) + 2) if fetch_limit else 1000000

    while page <= max_pages:
        payload = {
            "page": page,
            "per_page": per_page,
            "sort_by": "assets",
            "sort_direction": "desc",
            "only": ["meta", "data"],
        }
        try:
            r = _req.post(_ETFDB_SCREENER_URL, json=payload, headers=_ETFDB_HEADERS, timeout=30)
            if r.status_code == 429:
                _log("Phase 12 ETF: etfdb.com rate limited, waiting 10s...", "yellow")
                time.sleep(10)
                continue
            if r.status_code != 200:
                _log(f"Phase 12 ETF: etfdb.com HTTP {r.status_code} on page {page}; stopping.", "yellow")
                break
            data = r.json()
        except Exception as e:
            _log(f"Phase 12 ETF: etfdb.com fetch error on page {page}: {e}", "yellow")
            break

        meta = data.get("meta") or {}
        if total is None:
            total = int(meta.get("total_records") or fetch_limit or 0)
            _log(f"Phase 12 ETF: etfdb.com reports {total} total ETFs.", "white")

        records = data.get("data") or []
        for rec in records:
            ticker = _etfdb_field(rec, "symbol")
            name = _etfdb_field(rec, "name")
            # Try different possible category field names
            category = (
                _etfdb_field(rec, "etf_category")
                or _etfdb_field(rec, "etf-category")
                or _etfdb_field(rec, "category")
                or ""
            )
            asset_class = (
                _etfdb_field(rec, "asset_class")
                or _etfdb_field(rec, "asset-class")
            )
            # Capture AUM string for reference (etfdb field names vary)
            total_assets = (
                _etfdb_field(rec, "total-assets")
                or _etfdb_field(rec, "assets-under-management")
                or _etfdb_field(rec, "aum")
                or _etfdb_field(rec, "assets")
                or ""
            )
            if ticker:
                out.append({
                    "ticker": ticker,
                    "name": name or ticker,
                    "category": category,
                    "asset_class": asset_class,
                    "total_assets": total_assets,
                })
            if fetch_limit and len(out) >= fetch_limit:
                break

        if (fetch_limit and len(out) >= fetch_limit) or not records or (total and len(out) >= total):
            break
        page += 1
        time.sleep(1.0)  # Rate limit: 1 req/sec

    return out, int(total or len(out))


def _fetch_etf_list_etfdb(max_etfs: int = 0) -> list[dict]:
    rows, _total = _fetch_etf_list_etfdb_with_total(max_etfs=max_etfs)
    return rows


def _merge_etf_universe_rows(primary_rows: list[dict], supplemental_rows: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    ordered_tickers: list[str] = []
    for rows in (primary_rows or [], supplemental_rows or []):
        for row in rows or []:
            ticker = str((row or {}).get("ticker") or "").strip().upper()
            if not ticker:
                continue
            normalized = {
                "ticker": ticker,
                "name": str((row or {}).get("name") or ticker).strip() or ticker,
                "category": str((row or {}).get("category") or "").strip(),
                "asset_class": str((row or {}).get("asset_class") or "").strip(),
                "total_assets": str((row or {}).get("total_assets") or "").strip(),
            }
            if ticker not in merged:
                merged[ticker] = normalized
                ordered_tickers.append(ticker)
                continue
            existing = merged[ticker]
            if (not existing.get("name") or existing.get("name") == ticker) and normalized.get("name") and normalized.get("name") != ticker:
                existing["name"] = normalized["name"]
            if not existing.get("category") and normalized.get("category"):
                existing["category"] = normalized["category"]
            if not existing.get("asset_class") and normalized.get("asset_class"):
                existing["asset_class"] = normalized["asset_class"]
            if not existing.get("total_assets") and normalized.get("total_assets"):
                existing["total_assets"] = normalized["total_assets"]
    return [merged[ticker] for ticker in ordered_tickers]


def _fetch_etf_list_polygon(api_key: str, max_results: int = 0) -> list[dict]:
    """Fetch US ETF list from Polygon.io v3/reference/tickers with type=ETF (fallback)."""
    import requests as _req
    base = "https://api.polygon.io/v3/reference/tickers"
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {"market": "stocks", "type": "ETF", "active": "true", "limit": 1000, "order": "asc", "sort": "ticker"}
    out: list[dict] = []
    next_url = None
    fetch_limit = max_results if max_results and max_results > 0 else 0
    while not fetch_limit or len(out) < fetch_limit:
        _polygon_rate_limit()
        try:
            if next_url:
                r = _req.get(next_url, headers=headers, timeout=30)
            else:
                r = _req.get(base, params=params, headers=headers, timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            _log(f"Phase 12 ETF: Polygon fetch error: {e}", "yellow")
            break
        results = data.get("results") or []
        for t in results:
            ticker = (t.get("ticker") or "").strip()
            name = (t.get("name") or t.get("description") or "").strip()
            if ticker and name:
                out.append({"ticker": ticker, "name": name, "category": "", "asset_class": ""})
            if fetch_limit and len(out) >= fetch_limit:
                break
        next_url = data.get("next_url")
        if not next_url or not results:
            break
    return out


def _fetch_polygon_etf_details_batch(
    tickers: list[str],
    api_key: str,
    cache: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """
    Fetch individual ETF details from Polygon.io for classification enrichment.
    Endpoint: GET /v3/reference/tickers/{ticker}
    Returns {ticker: {"name": str, "description": str}} for tickers where Polygon
    provides a useful description (e.g. "tracks equity performance of energy sector").
    Rate-limited via _polygon_rate_limit(). Only called for ETFs with generic/blank
    category after etfdb classification, to keep API call count manageable.
    Pass a mutable dict as `cache` to accumulate results across calls.
    """
    import requests as _req
    if cache is None:
        cache = {}
    headers = {"Authorization": f"Bearer {api_key}"}
    base = "https://api.polygon.io/v3/reference/tickers"
    total = len(tickers)
    cache_hits = 0
    requests_made = 0
    populated = 0
    to_fetch = sum(1 for ticker in tickers if ticker not in cache)
    _log(
        f"Phase 12 ETF: Polygon enrichment starting for {total} ETF(s) "
        f"({total - to_fetch} cached, {to_fetch} to fetch, limit {POLYGON_CALLS_PER_MINUTE}/min).",
        "white",
    )
    for idx, ticker in enumerate(tickers, start=1):
        if ticker in cache:
            cache_hits += 1
            if idx % 10 == 0 or idx == total:
                _log(
                    f"Phase 12 ETF: Polygon enrichment progress {idx}/{total} "
                    f"(cache hits {cache_hits}, requests {requests_made}, populated {populated}).",
                    "white",
                )
            continue
        _polygon_rate_limit(f"Phase 12 ETF: {ticker} ({idx}/{total})")
        requests_made += 1
        try:
            r = _req.get(f"{base}/{ticker}", headers=headers, timeout=15)
            if r.status_code == 200:
                result = (r.json().get("results") or {})
                desc = (result.get("description") or "").strip()
                name = (result.get("name") or "").strip()
                if desc or name:
                    cache[ticker] = {"name": name, "description": desc}
                    populated += 1
            elif r.status_code == 429:
                _log(
                    f"Phase 12 ETF: Polygon rate-limited fetching {ticker} details at {idx}/{total}; "
                    f"stopping enrichment for the remaining ETFs.",
                    "yellow",
                )
                break
            if idx % 10 == 0 or idx == total:
                _log(
                    f"Phase 12 ETF: Polygon enrichment progress {idx}/{total} "
                    f"(cache hits {cache_hits}, requests {requests_made}, populated {populated}).",
                    "white",
                )
        except Exception as e:
            _log(f"Phase 12 ETF: Polygon details error for {ticker}: {e}", "yellow")
    _log(
        f"Phase 12 ETF: Polygon enrichment complete for {total} ETF(s) "
        f"(cache hits {cache_hits}, requests {requests_made}, populated {populated}, cache size {len(cache)}).",
        "green",
    )
    return cache


def _fetch_etf_holdings_etfdb(ticker: str) -> list[dict]:
    """
    Scrape top holdings for an ETF from etfdb.com using scrapling.
    Returns list of {holding_ticker, weight_pct} dicts.
    Only fetches for key ETFs to avoid excessive scraping.
    """
    try:
        from scrapling.fetchers import Fetcher as _ScraplingFetcher
        fetcher = _ScraplingFetcher(auto_match=False)
    except ImportError:
        try:
            import requests as _req
            # Fallback: plain requests
            url = f"{_ETFDB_ETF_BASE_URL}{ticker}/"
            headers = dict(_ETFDB_HEADERS)
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            headers.pop("X-Requested-With", None)
            r = _req.get(url, headers=headers, timeout=20)
            html = r.text
        except Exception:
            return []
        fetcher = None
    else:
        html = None

    try:
        url = f"{_ETFDB_ETF_BASE_URL}{ticker}/"
        if fetcher is not None:
            page = fetcher.get(url, stealthy_headers=True, follow_redirects=True, timeout=20)
            html = page.html if hasattr(page, "html") else (page.text if hasattr(page, "text") else str(page))

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html or "", "html.parser")

        holdings: list[dict] = []
        # etfdb.com holdings table: look for rows with stock links like /stock/{ticker}/
        for a_tag in soup.find_all("a", href=True):
            href = a_tag.get("href", "")
            if "/stock/" in href:
                # Extract ticker from link like /stock/XOM/
                parts = href.strip("/").split("/")
                stock_idx = parts.index("stock") if "stock" in parts else -1
                if stock_idx >= 0 and stock_idx + 1 < len(parts):
                    holding_ticker = parts[stock_idx + 1].upper()
                    # Find weight in parent row
                    row = a_tag.find_parent("tr")
                    weight_pct = 0.0
                    if row:
                        tds = row.find_all("td")
                        for td in tds:
                            text = td.get_text(strip=True)
                            if "%" in text:
                                try:
                                    weight_pct = float(text.replace("%", "").replace(",", "").strip())
                                    break
                                except ValueError:
                                    pass
                    if holding_ticker and weight_pct > 0:
                        holdings.append({"holding_ticker": holding_ticker, "weight_pct": weight_pct})

        # Deduplicate (keep highest weight if duplicate)
        seen: dict[str, float] = {}
        for h in holdings:
            ht = h["holding_ticker"]
            if ht not in seen or h["weight_pct"] > seen[ht]:
                seen[ht] = h["weight_pct"]
        return [{"holding_ticker": t, "weight_pct": w} for t, w in seen.items() if w > 0][:50]

    except Exception as e:
        _log(f"Phase 12 ETF: Holdings scrape error for {ticker}: {e}", "yellow")
        return []


def phase13_etf_universe(session):
    """
    Phase 12: Fetch US ETF universe from etfdb.com (primary) or Polygon.io (fallback),
    classify by theme/sector, create (:ETF) nodes, (:ETF)-[:ETF_TRACKS_SECTOR]->(:Sector),
    (:ETF)-[:ETF_TRACKS_THEME]->(:Theme) relationships, and (:ETF)-[:ETF_HOLDS]->(:Company)
    edges for key ETFs.
    Enables theme-based ETF lookup: 'find oil ETFs', 'find energy ETFs', etc.
    """
    _nexus_stage_reset()
    _progress(PHASE_PCT[14], "Phase 12: ETF universe — fetching ETF list from etfdb.com...", "cyan")
    conn = _nexus_rethink_conn
    batch_size = 200
    today_iso = datetime.now(timezone.utc).date().isoformat()
    phase_run_token = f"phase12:{uuid4().hex}"

    def _cache_snapshot_date(path: str, fallback: str = "") -> str:
        candidate = str(fallback or today_iso)
        try:
            if path and os.path.exists(path):
                candidate = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc).date().isoformat()
        except Exception:
            pass
        return _normalize_edge_date(candidate) or today_iso

    # ── 1) Fetch ETF list ──────────────────────────────────────────────────
    etf_cache_name = f"etf_list_v{PHASE12_ETF_LIST_CACHE_VERSION}.json"
    cache_path = _nexus_cache_path("phase12", etf_cache_name)
    etfs: list[dict] = []

    cache_read_path = _nexus_cache_lookup_path("phase12", etf_cache_name)
    cached = _nexus_read_cached_json(cache_read_path, NEXUS_CACHE_DEFAULT_MAX_AGE * 7)  # 7-day cache
    if cached is not None and isinstance(cached, list):
        etfs = cached
        _nexus_stage_cache_hit(1)
        _log(f"Phase 12 ETF: Using cached list ({len(etfs)} ETFs).", "white")
        if conn:
            _phase13_report_progress(conn, 0, 0, f"Using cached list ({len(etfs)} ETFs)",
                                     progress_pct_override=PHASE_PCT[14] + 0.15 * (PHASE_PCT[15] - PHASE_PCT[14]))
    else:
        # Primary: etfdb.com screener
        _log("Phase 12 ETF: Fetching from etfdb.com screener...", "white")
        etfs, etfdb_total = _fetch_etf_list_etfdb_with_total(max_etfs=PHASE12_ETF_FETCH_LIMIT)
        if etfs:
            expected_count = 0
            if PHASE12_ETF_FETCH_LIMIT and PHASE12_ETF_FETCH_LIMIT > 0:
                expected_count = min(int(etfdb_total or 0), PHASE12_ETF_FETCH_LIMIT) if etfdb_total else PHASE12_ETF_FETCH_LIMIT
            else:
                expected_count = int(etfdb_total or 0)
            if POLYGON_API_KEY and expected_count and len(etfs) < expected_count:
                _log(
                    f"Phase 12 ETF: etfdb.com returned {len(etfs)} of {expected_count} expected ETFs; "
                    "backfilling missing tickers from Polygon.",
                    "yellow",
                )
                polygon_rows = _fetch_etf_list_polygon(
                    POLYGON_API_KEY,
                    max_results=(PHASE12_ETF_FETCH_LIMIT if PHASE12_ETF_FETCH_LIMIT > 0 else 0),
                )
                if polygon_rows:
                    merged_rows = _merge_etf_universe_rows(etfs, polygon_rows)
                    backfilled = len(merged_rows) - len(etfs)
                    etfs = merged_rows
                    _log(
                        f"Phase 12 ETF: Polygon backfill added {max(backfilled, 0)} missing ETF ticker(s) "
                        f"({len(etfs)} total after merge).",
                        "green",
                    )
            _nexus_write_cached_json(cache_path, etfs)
            _nexus_stage_download(1)
            _log(f"Phase 12 ETF: Fetched {len(etfs)} ETFs from etfdb.com.", "green")
        else:
            # Fallback: Polygon.io
            _log("Phase 12 ETF: etfdb.com failed; falling back to Polygon.io...", "yellow")
            if POLYGON_API_KEY:
                etfs = _fetch_etf_list_polygon(POLYGON_API_KEY, max_results=PHASE12_ETF_FETCH_LIMIT)
                if etfs:
                    _nexus_write_cached_json(cache_path, etfs)
                    _nexus_stage_download(1)
                    _log(f"Phase 12 ETF: Fetched {len(etfs)} ETFs from Polygon.", "green")
                else:
                    _log("Phase 12 ETF: Polygon also returned no ETFs; skipping.", "yellow")
            else:
                _log("Phase 12 ETF: POLYGON_API_KEY not set; skipping ETF fetch.", "yellow")

    if not etfs:
        _progress(PHASE_PCT[15], "Phase 12 ETF: skipped (no ETF data).", "yellow")
        return
    etf_list_snapshot_date = _cache_snapshot_date(cache_path, today_iso)

    # ── 2) Enrich ETFs with category + keywords (dynamic first, fallback second) ──
    # Pass 1: classify every ETF using etfdb category (primary) or name (fallback)
    for etf in etfs:
        cat, kws, primary_theme, themes = _classify_etf_with_themes(
            etf.get("category", ""), etf.get("name", "")
        )
        cat, kws, primary_theme, themes = _phase12_sanitize_etf_classification(
            etf.get("category", ""),
            etf.get("name", ""),
            etf.get("asset_class", ""),
            cat,
            kws,
            primary_theme,
            themes,
        )
        etf["category"] = cat
        etf["keywords"] = kws
        etf["primary_theme"] = primary_theme
        etf["themes"] = themes

    # Pass 2 (optional): Polygon description enrichment for key ETFs still in "Broad Market"
    # Only runs if Polygon key is available and there are generic-category key ETFs to enrich
    if POLYGON_API_KEY:
        key_etfs_dynamic = _select_key_etfs_dynamic(etfs, top_n=150)
        generic_key = [
            e["ticker"] for e in etfs
            if e["ticker"] in key_etfs_dynamic
            and e.get("category") in ("Broad Market", "", None)
        ]
        if generic_key:
            _log(f"Phase 12 ETF: Enriching {len(generic_key)} generic-category key ETFs via Polygon...", "white")
            poly_details_cache_path = _nexus_cache_path("phase12", "polygon_details.json")
            poly_details: dict[str, dict] = {}
            poly_details_read_path = _nexus_cache_lookup_path("phase12", "polygon_details.json", "phase13_etf")
            cached_pd = _nexus_read_cached_json(poly_details_read_path, NEXUS_CACHE_DEFAULT_MAX_AGE * 14)
            if cached_pd and isinstance(cached_pd, dict):
                poly_details = cached_pd
                _nexus_stage_cache_hit(1)
                _log(f"Phase 12 ETF: Using cached Polygon ETF details ({len(poly_details)} entries).", "white")
            else:
                poly_details = _fetch_polygon_etf_details_batch(generic_key, POLYGON_API_KEY)
                if poly_details:
                    _nexus_write_cached_json(poly_details_cache_path, poly_details)
                    _nexus_stage_download(1)
            # Re-classify using Polygon description if it improves the category
            enriched = 0
            ticker_map = {e["ticker"]: e for e in etfs}
            for ticker, pd in poly_details.items():
                etf = ticker_map.get(ticker)
                if not etf:
                    continue
                # Try classifying from description first (richer text), then name
                desc = pd.get("description", "")
                poly_name = pd.get("name", etf.get("name", ""))
                # Combine description + name into a single classification text
                combined = f"{desc} {poly_name}".strip()
                cat, kws, primary_theme, themes = _classify_etf_with_themes(desc, combined)
                cat, kws, primary_theme, themes = _phase12_sanitize_etf_classification(
                    desc,
                    combined,
                    etf.get("asset_class", ""),
                    cat,
                    kws,
                    primary_theme,
                    themes,
                )
                if cat != "Broad Market":
                    etf["category"] = cat
                    etf["keywords"] = kws
                    etf["primary_theme"] = primary_theme
                    etf["themes"] = themes
                    enriched += 1
            if enriched:
                _log(f"Phase 12 ETF: Polygon enrichment upgraded category for {enriched} ETFs.", "green")

    if conn:
        _phase13_report_progress(conn, 1, 4, "Creating ETF nodes in Neo4j...",
                                 progress_pct_override=PHASE_PCT[14] + 0.2 * (PHASE_PCT[15] - PHASE_PCT[14]))

    # ── 3) Write ETF nodes ─────────────────────────────────────────────────
    # First ensure ETF node uniqueness constraint / index
    try:
        session.run("CREATE CONSTRAINT etf_ticker_unique IF NOT EXISTS FOR (e:ETF) REQUIRE e.ticker IS UNIQUE").consume()
    except Exception:
        try:
            session.run("CREATE INDEX etf_ticker_idx IF NOT EXISTS FOR (e:ETF) ON (e.ticker)").consume()
        except Exception:
            pass

    created_nodes = 0
    for start in range(0, len(etfs), batch_size):
        chunk = etfs[start:start + batch_size]
        params = [
            {
                "ticker":     e["ticker"],
                "name":       (e.get("name") or e["ticker"])[:200],
                "category":   e.get("category") or "Broad Market",
                "keywords":   e.get("keywords") or [],
                "primary_theme": e.get("primary_theme") or "",
                "themes":     e.get("themes") or [],
                "asset_class": e.get("asset_class") or "",
            }
            for e in chunk
        ]
        session.run("""
            UNWIND $batch AS p
            MERGE (e:ETF {ticker: p.ticker})
            SET e.name        = p.name,
                e.category    = p.category,
                e.keywords    = p.keywords,
                e.primary_theme = p.primary_theme,
                e.themes      = p.themes,
                e.asset_class = p.asset_class
        """, batch=params).consume()
        created_nodes += len(chunk)
        if conn:
            _phase13_report_progress(conn, created_nodes, len(etfs),
                                     f"Writing ETF nodes {created_nodes}/{len(etfs)}")

    _log(f"Phase 12 ETF: {created_nodes} ETF nodes written.", "green")
    if conn:
        _phase13_report_progress(conn, 2, 4, "Creating ETF_TRACKS_SECTOR relationships...",
                                 progress_pct_override=PHASE_PCT[14] + 0.5 * (PHASE_PCT[15] - PHASE_PCT[14]))

    # ── 4) Write ETF_TRACKS_SECTOR relationships ───────────────────────────
    sector_categories = sorted({(e.get("category") or "").strip() for e in etfs if _is_etf_sector_category(e.get("category") or "")})
    _log(
        f"Phase 12 ETF: sector linking uses resolved ETF category only ({len(sector_categories)} sector categories: {sector_categories[:6]}...).",
        "white",
    )

    created_sector_rels = 0
    for start in range(0, len(etfs), batch_size):
        chunk = etfs[start:start + batch_size]
        matched_pairs: set[tuple[str, str]] = set()
        for e in chunk:
            for sector in _phase12_etf_sector_targets(e):
                matched_pairs.add((e["ticker"], sector))
        pairs = [
            {"ticker": ticker, "sector": sector, "snapshot_date": etf_list_snapshot_date, "today": today_iso}
            for ticker, sector in sorted(matched_pairs)
        ]
        if pairs:
            rec = session.run("""
                UNWIND $pairs AS p
                MATCH (e:ETF {ticker: p.ticker})
                MERGE (s:Sector {name: p.sector})
                MERGE (e)-[r:ETF_TRACKS_SECTOR {source_scope: 'ETF_UNIVERSE_CLASSIFICATION', edge_state: 'open'}]->(s)
                ON CREATE SET r.source = 'ETF universe',
                              r.confidence = 1.0,
                              r.active_after = p.snapshot_date,
                              r.last_confirmed = p.snapshot_date,
                              r.created_at = p.today,
                              r.valid_until = NULL,
                              r.retired_reason = NULL
                ON MATCH  SET r.active_after = CASE
                                  WHEN p.snapshot_date IS NULL OR p.snapshot_date = '' THEN r.active_after
                                  WHEN r.active_after IS NULL OR r.active_after = '' OR p.snapshot_date < r.active_after THEN p.snapshot_date
                                  ELSE r.active_after
                              END,
                              r.last_confirmed = CASE
                                  WHEN p.snapshot_date IS NULL OR p.snapshot_date = '' THEN r.last_confirmed
                                  WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR p.snapshot_date > r.last_confirmed THEN p.snapshot_date
                                  ELSE r.last_confirmed
                              END,
                              r.valid_until = NULL,
                              r.retired_reason = NULL,
                              r.edge_state = 'open',
                              r.current_run_token = $run_token
                SET r.current_run_token = $run_token
                RETURN count(r) AS created
            """, pairs=pairs, run_token=phase_run_token).single()
            created_sector_rels += int((rec or {}).get("created") or 0)

    _log(f"Phase 12 ETF: {created_sector_rels} ETF_TRACKS_SECTOR relationships written.", "green")
    sector_close_date = etf_list_snapshot_date or today_iso
    rec = session.run("""
        MATCH (:ETF)-[r:ETF_TRACKS_SECTOR {source_scope: 'ETF_UNIVERSE_CLASSIFICATION', edge_state: 'open'}]->(:Sector)
        WHERE coalesce(r.current_run_token, '') <> $run_token
        SET r.edge_state = 'closed',
            r.valid_until = CASE
                WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                ELSE $close_date
            END,
            r.retired_reason = 'superseded',
            r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
            r.current_run_token = NULL
        RETURN count(r) AS closed
    """, run_token=phase_run_token, close_date=sector_close_date).single()
    sector_closed = int((rec or {}).get("closed") or 0)
    try:
        _sync_graph_edge_intervals(session, "ETF_TRACKS_SECTOR", source_scope="ETF_UNIVERSE_CLASSIFICATION", directed=True)
    except Exception as e:
        _log_stage_error("Phase 12: ETF universe", "sync ETF_TRACKS_SECTOR interval history", str(e), e, color="yellow")
    try:
        retired_legacy_sector = _retire_relationships(
            session,
            "ETF_TRACKS_SECTOR",
            "coalesce(r.source_scope, '') = ''",
            directed=True,
            retired_reason="legacy_replaced",
        )
        if retired_legacy_sector:
            _log(f"Phase 12 ETF: retired {retired_legacy_sector} legacy ETF_TRACKS_SECTOR edge(s).", "green")
    except Exception as e:
        _log_stage_error("Phase 12: ETF universe", "retire legacy ETF_TRACKS_SECTOR", str(e), e, color="yellow")
    if sector_closed:
        _log(f"Phase 12 ETF: closed {sector_closed} superseded ETF_TRACKS_SECTOR interval(s).", "green")

    created_theme_rels = 0
    for start in range(0, len(etfs), batch_size):
        chunk = etfs[start:start + batch_size]
        theme_pairs = [
            {
                "ticker": e["ticker"],
                "theme": theme,
                "snapshot_date": etf_list_snapshot_date,
                "today": today_iso,
            }
            for e in chunk
            for theme in (e.get("themes") or [])
            if theme and theme != "Broad Market"
        ]
        if theme_pairs:
            rec = session.run("""
                UNWIND $pairs AS p
                MATCH (e:ETF {ticker: p.ticker})
                MERGE (t:Theme {name: p.theme})
                MERGE (e)-[r:ETF_TRACKS_THEME {source_scope: 'ETF_UNIVERSE_THEME', edge_state: 'open'}]->(t)
                ON CREATE SET r.source = 'ETF universe',
                              r.confidence = 1.0,
                              r.active_after = p.snapshot_date,
                              r.last_confirmed = p.snapshot_date,
                              r.created_at = p.today,
                              r.valid_until = NULL,
                              r.retired_reason = NULL
                ON MATCH  SET r.active_after = CASE
                                  WHEN p.snapshot_date IS NULL OR p.snapshot_date = '' THEN r.active_after
                                  WHEN r.active_after IS NULL OR r.active_after = '' OR p.snapshot_date < r.active_after THEN p.snapshot_date
                                  ELSE r.active_after
                              END,
                              r.last_confirmed = CASE
                                  WHEN p.snapshot_date IS NULL OR p.snapshot_date = '' THEN r.last_confirmed
                                  WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR p.snapshot_date > r.last_confirmed THEN p.snapshot_date
                                  ELSE r.last_confirmed
                              END,
                              r.valid_until = NULL,
                              r.retired_reason = NULL,
                              r.edge_state = 'open',
                              r.current_run_token = $run_token
                SET r.current_run_token = $run_token
                RETURN count(r) AS created
            """, pairs=theme_pairs, run_token=phase_run_token).single()
            created_theme_rels += int((rec or {}).get("created") or 0)

    _log(f"Phase 12 ETF: {created_theme_rels} ETF_TRACKS_THEME relationships written.", "green")
    theme_close_date = etf_list_snapshot_date or today_iso
    rec = session.run("""
        MATCH (:ETF)-[r:ETF_TRACKS_THEME {source_scope: 'ETF_UNIVERSE_THEME', edge_state: 'open'}]->(:Theme)
        WHERE coalesce(r.current_run_token, '') <> $run_token
        SET r.edge_state = 'closed',
            r.valid_until = CASE
                WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                ELSE $close_date
            END,
            r.retired_reason = 'superseded',
            r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
            r.current_run_token = NULL
        RETURN count(r) AS closed
    """, run_token=phase_run_token, close_date=theme_close_date).single()
    theme_closed = int((rec or {}).get("closed") or 0)
    try:
        _sync_graph_edge_intervals(session, "ETF_TRACKS_THEME", source_scope="ETF_UNIVERSE_THEME", directed=True)
    except Exception as e:
        _log_stage_error("Phase 12: ETF universe", "sync ETF_TRACKS_THEME interval history", str(e), e, color="yellow")
    try:
        retired_legacy_theme = _retire_relationships(
            session,
            "ETF_TRACKS_THEME",
            "coalesce(r.source_scope, '') = ''",
            directed=True,
            retired_reason="legacy_replaced",
        )
        if retired_legacy_theme:
            _log(f"Phase 12 ETF: retired {retired_legacy_theme} legacy ETF_TRACKS_THEME edge(s).", "green")
    except Exception as e:
        _log_stage_error("Phase 12: ETF universe", "retire legacy ETF_TRACKS_THEME", str(e), e, color="yellow")
    if theme_closed:
        _log(f"Phase 12 ETF: closed {theme_closed} superseded ETF_TRACKS_THEME interval(s).", "green")
    if conn:
        _phase13_report_progress(conn, 3, 4, "Fetching holdings for key ETFs...",
                                 progress_pct_override=PHASE_PCT[14] + 0.7 * (PHASE_PCT[15] - PHASE_PCT[14]))

    # ── 5) Fetch & write ETF_HOLDS relationships for key ETFs ─────────────
    # Select key ETFs dynamically: top 150 by AUM position + fallback set
    key_etfs_dynamic = _select_key_etfs_dynamic(etfs, top_n=150)

    holdings_cache_path = _nexus_cache_path("phase12", "etf_holdings.json")
    holdings_cache: dict[str, list[dict]] = {}
    holdings_cache_read_path = _nexus_cache_lookup_path("phase12", "etf_holdings.json", "phase13_etf")
    cached_holdings = _nexus_read_cached_json(holdings_cache_read_path, NEXUS_CACHE_DEFAULT_MAX_AGE * 30)  # 30-day cache
    if cached_holdings and isinstance(cached_holdings, dict):
        holdings_cache = cached_holdings
        _nexus_stage_cache_hit(1)
        _log(f"Phase 12 ETF: Using cached holdings ({len(holdings_cache)} ETFs).", "white")
    else:
        key_etfs_in_graph = {e["ticker"] for e in etfs if e["ticker"] in key_etfs_dynamic}
        _log(f"Phase 12 ETF: Fetching holdings for {len(key_etfs_in_graph)} key ETFs from etfdb.com...", "cyan")
        for idx, etf_ticker in enumerate(sorted(key_etfs_in_graph)):
            _log(f"  Holdings [{idx+1}/{len(key_etfs_in_graph)}]: {etf_ticker}", "white")
            holdings = _fetch_etf_holdings_etfdb(etf_ticker)
            if holdings:
                holdings_cache[etf_ticker] = holdings
            time.sleep(2.0)  # Polite rate limit for HTML scraping
            if conn and (idx + 1) % 10 == 0:
                _phase13_report_progress(
                    conn, idx + 1, len(key_etfs_in_graph),
                    f"Holdings: {idx+1}/{len(key_etfs_in_graph)} ETFs",
                    progress_pct_override=PHASE_PCT[14] + (0.7 + 0.25 * (idx + 1) / max(len(key_etfs_in_graph), 1)) * (PHASE_PCT[15] - PHASE_PCT[14]),
                )
        if holdings_cache:
            _nexus_write_cached_json(holdings_cache_path, holdings_cache)
            _nexus_stage_download(1)
    holdings_snapshot_date = _cache_snapshot_date(holdings_cache_path, today_iso)

    # Write ETF_HOLDS relationships
    created_holds_rels = 0
    etfs_with_written_holdings: set[str] = set()
    valid_company = _cypher_valid_company_listing_predicate("c")
    for etf_ticker, holdings in holdings_cache.items():
        if not holdings:
            continue
        params = _phase12_prepare_etf_holding_params(
            etf_ticker,
            holdings,
            snapshot_date=holdings_snapshot_date,
            today_iso=today_iso,
        )
        if params:
            rec = session.run("""
                UNWIND $params AS p
                MATCH (e:ETF {ticker: p.etf})
                MATCH (c:Company {ticker: p.holding})
                WHERE """ + valid_company + """
                MERGE (e)-[r:ETF_HOLDS {source_scope: 'ETF_UNIVERSE_HOLDINGS', edge_state: 'open'}]->(c)
                ON CREATE SET r.weight_pct = p.weight,
                              r.source = 'ETF universe',
                              r.confidence = 1.0,
                              r.active_after = p.snapshot_date,
                              r.last_confirmed = p.snapshot_date,
                              r.created_at = p.today,
                              r.valid_until = NULL,
                              r.retired_reason = NULL
                ON MATCH  SET r.weight_pct = p.weight,
                              r.active_after = CASE
                                  WHEN p.snapshot_date IS NULL OR p.snapshot_date = '' THEN r.active_after
                                  WHEN r.active_after IS NULL OR r.active_after = '' OR p.snapshot_date < r.active_after THEN p.snapshot_date
                                  ELSE r.active_after
                              END,
                              r.last_confirmed = CASE
                                  WHEN p.snapshot_date IS NULL OR p.snapshot_date = '' THEN r.last_confirmed
                                  WHEN r.last_confirmed IS NULL OR r.last_confirmed = '' OR p.snapshot_date > r.last_confirmed THEN p.snapshot_date
                                  ELSE r.last_confirmed
                              END,
                              r.valid_until = NULL,
                              r.retired_reason = NULL,
                              r.edge_state = 'open',
                              r.current_run_token = $run_token
                SET r.current_run_token = $run_token
                RETURN count(r) AS created, count(DISTINCT c.ticker) AS matched_holdings
            """, params=params, run_token=phase_run_token).single()
            created_holds_rels += int((rec or {}).get("created") or 0)
            matched_holdings = int((rec or {}).get("matched_holdings") or 0)
            if matched_holdings > 0:
                etfs_with_written_holdings.add(_normalize_graph_ticker(etf_ticker))

    holdings_close_date = holdings_snapshot_date or today_iso
    rec = session.run("""
        MATCH (:ETF)-[r:ETF_HOLDS {source_scope: 'ETF_UNIVERSE_HOLDINGS', edge_state: 'open'}]->(:Company)
        WHERE coalesce(r.current_run_token, '') <> $run_token
        SET r.edge_state = 'closed',
            r.valid_until = CASE
                WHEN $close_date = '' THEN coalesce(r.active_after, r.valid_until)
                WHEN r.active_after IS NOT NULL AND r.active_after <> '' AND $close_date < r.active_after THEN r.active_after
                ELSE $close_date
            END,
            r.retired_reason = 'superseded',
            r.closed_at = CASE WHEN $close_date = '' THEN r.closed_at ELSE $close_date END,
            r.current_run_token = NULL
        RETURN count(r) AS closed
    """, run_token=phase_run_token, close_date=holdings_close_date).single()
    holds_closed = int((rec or {}).get("closed") or 0)
    try:
        _sync_graph_edge_intervals(session, "ETF_HOLDS", source_scope="ETF_UNIVERSE_HOLDINGS", directed=True)
    except Exception as e:
        _log_stage_error("Phase 12: ETF universe", "sync ETF_HOLDS interval history", str(e), e, color="yellow")
    try:
        retired_legacy_holds = _retire_relationships(
            session,
            "ETF_HOLDS",
            "coalesce(r.source_scope, '') = ''",
            directed=True,
            retired_reason="legacy_replaced",
        )
        if retired_legacy_holds:
            _log(f"Phase 12 ETF: retired {retired_legacy_holds} legacy ETF_HOLDS edge(s).", "green")
    except Exception as e:
        _log_stage_error("Phase 12: ETF universe", "retire legacy ETF_HOLDS", str(e), e, color="yellow")
    if holds_closed:
        _log(f"Phase 12 ETF: closed {holds_closed} superseded ETF_HOLDS interval(s).", "green")
    live_holds_rels = _count_live_scoped_relationships(
        session,
        rel_type="ETF_HOLDS",
        source_scope="ETF_UNIVERSE_HOLDINGS",
        directed=True,
    )
    live_holding_sources = _count_live_scoped_relationship_sources(
        session,
        rel_type="ETF_HOLDS",
        source_scope="ETF_UNIVERSE_HOLDINGS",
    )
    scraped_holding_sources = sum(1 for holdings in holdings_cache.values() if holdings)
    _log(
        "Phase 12 ETF: "
        f"{live_holds_rels} live ETF_HOLDS relationships written for {live_holding_sources} ETFs "
        f"with matched company holdings ({scraped_holding_sources} key ETFs scraped/cached).",
        "green",
    )
    unmatched_holding_etfs = sorted(
        _normalize_graph_ticker(etf_ticker)
        for etf_ticker, holdings in holdings_cache.items()
        if holdings and _normalize_graph_ticker(etf_ticker) not in etfs_with_written_holdings
    )
    if unmatched_holding_etfs:
        _log(
            "Phase 12 ETF: "
            f"{len(unmatched_holding_etfs)} scraped ETF holding set(s) had no listed-company matches "
            f"(samples: {', '.join(unmatched_holding_etfs[:10])}).",
            "white",
        )

    # ── 6) Secondary indexes ───────────────────────────────────────────────
    try:
        session.run("CREATE INDEX etf_category_idx IF NOT EXISTS FOR (e:ETF) ON (e.category)").consume()
        session.run("CREATE INDEX etf_asset_class_idx IF NOT EXISTS FOR (e:ETF) ON (e.asset_class)").consume()
    except Exception:
        pass

    _progress(PHASE_PCT[15], f"Phase 12 done: {created_nodes} ETF nodes, {created_sector_rels} sector links, {live_holds_rels} holdings.", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Mark built
# ──────────────────────────────────────────────────────────────────────────────
def mark_built(session):
    _nexus_stage_reset()
    _progress(PHASE_PCT[15], "Marking graph as built (GraphBuildStatus)...", "cyan")
    session.run(
        "MERGE (g:GraphBuildStatus {id: 'graph_nexus'}) "
        "SET g.built = true, g.built_at = $ts",
        ts=datetime.now(timezone.utc).isoformat(),
    )
    _progress(PHASE_PCT[16], "Graph marked as built. Build complete.", "green")


# ──────────────────────────────────────────────────────────────────────────────
# Main build runner
# ──────────────────────────────────────────────────────────────────────────────
def _run_build(driver, conn) -> bool:
    """Run full graph build (phases 1–14, with Phase 6B after GLEIF). Returns True if built/already built, False on failure."""
    global _nexus_build_start_time, _nexus_phase_start_time, _nexus_current_phase_index, _nexus_last_eta_log_time
    global _nexus_rethink_conn, _nexus_current_phase_label, _nexus_current_phase_num

    # Per-build log file + NexusGraphBuilds row lifecycle.
    # Lives in its own try/finally so even if setup fails the outer handler
    # still runs. Variables default to None so the finalize block is safe.
    _build_row_id: str | None = None
    _build_log_file = None
    _build_log_path: str | None = None
    _build_log_buffer: list = []
    _build_final_status = "failed"

    try:
        try:
            from nexus_graph_builds import (
                ensure_log_dir as _ngb_ensure_log_dir,
                rotate_old_logs as _ngb_rotate,
                open_build_log as _ngb_open_log,
                close_build_log as _ngb_close_log,
                _make_build_id as _ngb_make_id,
                build_row_start as _ngb_row_start,
                build_row_append_phase as _ngb_row_append_phase,
                build_row_append_warning as _ngb_row_append_warning,
                build_row_update_tail as _ngb_row_update_tail,
                build_row_finalize as _ngb_row_finalize,
                _neo4j_counts as _ngb_counts,
            )
            # nexus_graph_builds took (r, conn) and now ignores both: the
            # store takes its own pooled connection per operation. The handle
            # stays so the ~6 call sites below keep their arity. It must NOT
            # be None-by-accident: build_row_start's r.now() used to sit
            # OUTSIDE the try below, so a None here crashed a real build.
            _r_ngb = None
            _ngb_ensure_log_dir()
            _ngb_rotate()
            _build_row_id = _ngb_make_id()
            _build_log_file, _build_log_path = _ngb_open_log(_build_row_id)
            from intellistock_logger import intellistock_logger as _logger
            _logger.set_nexus_graph_log_file(_build_log_file)
            _logger.set_nexus_graph_log_buffer(_build_log_buffer, max_lines=500)
        except Exception as _e_setup:
            _log(f"Nexus build-log setup failed (non-fatal): {_e_setup}", "yellow")

        _NEXUS_REQUESTED_PHASE_RANGE[0] = {"start": None, "end": None}
        _nexus_rethink_conn = conn
        _progress(PHASE_PCT[0], "Initializing Neo4j (constraints/indexes)...", "cyan")
        if conn:
            _save_graph_nexus_progress(conn, -1, PHASE_PCT[0], "Initializing Neo4j...", "running", stage_update={"stage_index": 0, "status": "running", "message": "Initializing Neo4j (constraints/indexes)..."})
        t0 = time.monotonic()
        ensure_neo4j_init(driver)
        if conn:
            _save_graph_nexus_progress(conn, -1, PHASE_PCT[0], "Init done", "running", stage_update={"stage_index": 0, "status": "completed", "message": "Init done", "duration_sec": round(time.monotonic() - t0, 1)})
        _progress(PHASE_PCT[1], "Checking if graph already built...", "white")
        if conn:
            _save_graph_nexus_progress(conn, -1, PHASE_PCT[1], "Checking if built...", "running", stage_update={"stage_index": 1, "status": "running", "message": "Checking if graph already built..."})

        # Optional phase range from EngineControl (e.g. nexus start --start=1 --end=10)
        request_start_phase = None
        request_end_phase = None
        request_selected_phases = None
        if conn:
            try:
                ctrl = _load_nexus_control_doc(conn)
                if ctrl:
                    _set_nexus_temporal_state_from_control(ctrl)
                    _PHASE7_HISTORY_QUARTERS[0] = _clamp_phase7_history_quarters(ctrl.get("phase7_history_quarters"))
                    if _nexus_historical_enabled():
                        _PHASE7_HISTORY_QUARTERS[0] = _clamp_phase7_history_quarters(
                            _quarters_between(_nexus_historical_start_date())
                        )
                    sp = ctrl.get("start_phase")
                    ep = ctrl.get("end_phase")
                    selected = _normalize_nexus_control_phase_values(ctrl.get("selected_phases"))
                    if sp is not None and isinstance(sp, (int, float)):
                        request_start_phase = max(1, min(14, int(sp)))
                    if ep is not None and isinstance(ep, (int, float)):
                        request_end_phase = max(1, min(14, int(ep)))
                    if selected:
                        request_selected_phases = selected
                    # Only --start given: run from start through last phase
                    if request_start_phase is not None and request_end_phase is None:
                        request_end_phase = 14
                    # Only --end given: run from phase 1 through end
                    if request_end_phase is not None and request_start_phase is None:
                        request_start_phase = 1
                    if request_start_phase is not None and request_end_phase is not None and request_start_phase > request_end_phase:
                        request_start_phase, request_end_phase = request_end_phase, request_start_phase
            except Exception:
                pass
        else:
            _set_nexus_temporal_state_from_control({})

        # Non-destructive rebuild: if the graph is already built AND fresh AND
        # the caller didn't request a specific phase slice, we still walk phases
        # 1..14 so per-phase cursors + TTLs can pick up NEW data since the last
        # run. Phases with recent cache files will short-circuit their HTTP
        # fetches; phases with cursors (3, 7, 8, 10, 11, 12) will fetch deltas.
        # All writes are MERGE-based and therefore idempotent, so no duplicates.
        #
        # Honour an explicit opt-out via env (ops escape hatch) — default OFF
        # so the user's "non-destructive rebuild should see new data" contract
        # holds out of the box.
        _skip_when_built = os.environ.get("NEXUS_SKIP_WHEN_BUILT", "false").strip().lower() in ("1", "true", "yes", "on")
        if is_graph_built(driver) and request_start_phase is None and request_end_phase is None and not request_selected_phases:
            if _skip_when_built:
                _log("Graph already built and NEXUS_SKIP_WHEN_BUILT=1 — skipping full pipeline.", "yellow")
                _progress(100.0, "Skipped (already built).", "green")
                if conn:
                    _save_graph_nexus_progress(conn, 14, 100.0, "Already built.", "completed", current_phase_label="Already built", stage_update={"stage_index": 1, "status": "skipped", "message": "Already built"})
                _nexus_rethink_conn = None
                return True
            _log("Graph already built; running non-destructive incremental refresh (phases 1–14).", "cyan")
            _log("  Cached phases will short-circuit; cursor-based phases (3,7,8,10,11,12) fetch deltas; writes are MERGE/idempotent.", "cyan")
            # Fall through. _run_build proceeds with the full phase range.
            # request_start_phase / request_end_phase stay None, which the loop
            # interprets as "run all phases". mark_built will bump built_at at
            # the end of a phase-14 pass.

        if request_selected_phases:
            labels = ", ".join(str(phase) for phase in request_selected_phases)
            _log(f"Selected phases requested: {labels} (rerun even if built).", "cyan")
            _NEXUS_REQUESTED_PHASE_SELECTION[0] = list(request_selected_phases)
            _NEXUS_REQUESTED_PHASE_RANGE[0] = {"start": None, "end": None}
            request_start_phase = min(request_selected_phases)
            request_end_phase = max(request_selected_phases)
        if request_start_phase is not None and request_end_phase is not None and not request_selected_phases:
            _log(f"Phase range requested: {request_start_phase}–{request_end_phase} (rerun even if built).", "cyan")
            _NEXUS_REQUESTED_PHASE_RANGE[0] = {"start": request_start_phase, "end": request_end_phase}
        elif not request_selected_phases:
            _NEXUS_REQUESTED_PHASE_RANGE[0] = {"start": None, "end": None}

        if conn:
            _save_graph_nexus_progress(conn, 0, PHASE_PCT[1], "Not built, starting phases", "running", stage_update={"stage_index": 1, "status": "completed", "message": "Not built"})

        # Insert NexusGraphBuilds row now that we know we're actually building.
        if conn and _build_row_id:
            try:
                _build_mode = "non-destructive-refresh" if (
                    request_start_phase is not None or request_end_phase is not None or request_selected_phases
                ) else "full-rebuild"
                with driver.session() as _seed_session:
                    _ngb_row_start(
                        _r_ngb, conn,
                        mode=_build_mode,
                        selected_phases=request_selected_phases,
                        requested_range={"start": request_start_phase, "end": request_end_phase} if (request_start_phase or request_end_phase) else None,
                        session=_seed_session,
                        log_file_path=_build_log_path,
                        build_id=_build_row_id,
                    )
                _log(f"Nexus build started: build_id={_build_row_id} mode={_build_mode} log={_build_log_path}", "cyan")
            except Exception as _e_row:
                _log(f"NexusGraphBuilds row_start failed (non-fatal): {_e_row}", "yellow")

        _nexus_build_start_time = time.monotonic()
        _nexus_last_eta_log_time = time.monotonic()

        start_phase = 1
        if request_start_phase is not None:
            start_phase = request_start_phase
        elif conn:
            prog = _load_graph_nexus_progress(conn)
            # Resume from last completed phase when build was running or was stopped mid-build
            if prog and prog.get("status") in ("running", "stopped"):
                last = int(prog.get("last_completed_phase") or 0)
                if 0 <= last < 14:
                    start_phase = last + 1
                    _log(f"Resuming from phase {start_phase} (last completed: {last}).", "cyan")
                    # Backfill stage statuses for phases that completed in the previous session
                    # so the Build Stages UI shows them as completed rather than pending
                    stored_stages = list((prog or {}).get("stages") or [])
                    stored_idxs = {s.get("stage_index") for s in stored_stages if s.get("stage_index") is not None}
                    for skipped_num in range(1, start_phase):
                        skipped_idx = skipped_num + 1  # phase_num -> stage_index (same offset as main loop)
                        if skipped_idx not in stored_idxs:
                            _save_graph_nexus_progress(
                                conn, skipped_num, PHASE_PCT[skipped_idx], "...", "running",
                                stage_update={"stage_index": skipped_idx, "status": "completed",
                                              "message": "Completed in previous session"},
                            )
        end_phase = request_end_phase if request_end_phase is not None else 14
        selected_phase_set = set(request_selected_phases or [])

        phases = [
            (1,  phase1_company_universe,       "Phase 1: Company universe"),
            (2,  phase2_easy_relationships,     "Phase 2: Easy relationships"),
            (3,  phase2b_sec_sector_industry,   "Phase 2b: SEC sector/industry"),
            (4,  phase3_supply_chain,           "Phase 3: Supply chain"),
            (5,  phase4_competitive,            "Phase 4: Competitive"),
            (6,  phase5_macro,                  "Phase 5: Macro/BEA"),
            (7,  phase6_gleif_hierarchy,        "Phase 6: GLEIF hierarchy"),
            (8,  phase6b_sec_ex21_hierarchy,    "Phase 6B: SEC EX-21 hierarchy"),
            (9,  phase7_13f_ownership,          "Phase 7: 13F ownership"),
            (10, phase9_usaspending,            "Phase 8: USASpending"),
            (11, phase10_wikidata,              "Phase 9: Wikidata"),
            (12, phase11_patents,               "Phase 10: PatentsView"),
            (13, phase12_8k_agreements,         "Phase 11: 8-K agreements"),
            (14, phase13_etf_universe,          "Phase 12: ETF universe"),
        ]
        # Phase 1b (market cap enrichment) runs as a post-step of Phase 1,
        # or can be triggered explicitly via selected_phases containing 15.
        _PHASE1B_NUM = 15

        with driver.session() as session:
            for phase_num, phase_fn, phase_label in phases:
                if _nexus_control_want_stop(conn):
                    _log("NexusControl running=False; saving progress and exiting.", "yellow")
                    if conn:
                        _save_graph_nexus_progress(conn, phase_num - 1,
                                                   PHASE_PCT[min(phase_num, len(PHASE_PCT) - 1)],
                                                   "Stopped by control", "stopped", current_phase_label=phase_label)
                    _nexus_rethink_conn = None
                    return False
                if phase_num < start_phase:
                    continue
                if phase_num > end_phase:
                    continue
                if selected_phase_set and phase_num not in selected_phase_set:
                    continue
                phase_index = phase_num + 1  # 1-based phase_num -> index in PHASE_KEYS (init=0, check=1, phase1=2, ...)
                phase_key = PHASE_KEYS[phase_index]
                pct_start = PHASE_PCT[phase_index - 1]
                pct_after = PHASE_PCT[phase_index]
                _nexus_current_phase_label = phase_label
                _nexus_current_phase_num = phase_num
                if conn:
                    eta_sec = _nexus_estimate_remaining(phase_index)
                    _save_graph_nexus_progress(
                        conn, phase_num - 1, pct_start, f"{phase_label} — in progress", "running",
                        eta_remaining_sec=eta_sec, current_phase_number=phase_num, current_phase_label=phase_label,
                        stage_update={"stage_index": phase_index, "status": "running", "message": f"{phase_label} — in progress"},
                    )
                    # Reset the sec_edgar_scraper card when Phase 3: Supply chain starts
                    if phase_num == 4:
                        try:
                            store.insert(GRAPH_NEXUS_PROGRESS_TABLE, {
                                "id": "sec_edgar_scraper",
                                "last_ticker_index": 0,
                                "total_tickers": None,
                                "edges_count": 0,
                                "progress_pct": 0.0,
                                "message": "Historical 10-K scraping starting...",
                                "last_updated": _store_now(),
                                "status": "running",
                            }, conflict="replace")
                        except Exception:
                            pass
                _nexus_stage_reset()
                _nexus_current_phase_index = phase_index
                _nexus_phase_start_time = time.monotonic()
                eta_sec = _nexus_estimate_remaining(phase_index)
                eta_msg = f" — ETA remaining: {_nexus_format_duration(eta_sec)}" if eta_sec is not None and eta_sec > 0 else ""
                _progress(pct_start, f"{phase_label} started...{eta_msg}", "cyan")
                # Snapshot Neo4j counts + phase-start ISO for build history.
                _phase_started_iso = datetime.now(timezone.utc).isoformat()
                _phase_snap_before = {}
                try:
                    _phase_snap_before = _ngb_counts(session)
                except Exception:
                    pass
                _phase_status = "completed"
                _phase_error: str | None = None
                try:
                    phase_fn(session)
                except Exception as _e_phase:
                    _phase_status = "failed"
                    _phase_error = f"{type(_e_phase).__name__}: {_e_phase}"
                    raise
                finally:
                    _ensure_default_phase_manifest_after_run(phase_key)
                    _persist_nexus_temporal_state(conn)
                    elapsed = time.monotonic() - _nexus_phase_start_time
                    # Phase summary to NexusGraphBuilds row.
                    if conn and _build_row_id:
                        try:
                            # Only probe Neo4j for post-phase counts on successful
                            # phases. A failed phase may have left the session in
                            # a dirty-transaction state; reading counts would
                            # raise and corrupt nodes_delta/edges_delta with huge
                            # negative outliers. Keep before==after on failure.
                            if _phase_status == "completed":
                                _phase_snap_after = _ngb_counts(session)
                            else:
                                _phase_snap_after = dict(_phase_snap_before)
                            _phase_nodes_delta = int(_phase_snap_after.get("nodes", 0)) - int(_phase_snap_before.get("nodes", 0))
                            _phase_edges_delta = int(_phase_snap_after.get("edges", 0)) - int(_phase_snap_before.get("edges", 0))
                            _ngb_row_append_phase(
                                _r_ngb, conn, _build_row_id,
                                phase_key=phase_key,
                                phase_num=phase_num,
                                stage_index=phase_index,
                                label=phase_label,
                                started_at=_phase_started_iso,
                                duration_sec=elapsed,
                                status=_phase_status,
                                work_units={
                                    "cached": int(_nexus_stage_stats.get("cached", 0) or 0),
                                    "downloaded": int(_nexus_stage_stats.get("downloaded", 0) or 0),
                                },
                                nodes_delta=_phase_nodes_delta,
                                edges_delta=_phase_edges_delta,
                                error=_phase_error,
                            )
                            _ngb_row_update_tail(_r_ngb, conn, _build_row_id, _build_log_buffer)
                        except Exception as _e_append:
                            _log(f"NexusGraphBuilds append_phase failed (non-fatal): {_e_append}", "yellow")
                runtimes = _nexus_runtimes_load()
                runtimes.setdefault(phase_key, [])
                runtimes[phase_key].append(elapsed)
                runtimes[phase_key] = runtimes[phase_key][-_MAX_RUNTIMES_PER_PHASE:]
                _nexus_runtimes_save(runtimes)
                _nexus_stage_log(phase_label, pct_after, elapsed, phase_key, phase_index)
                if conn:
                    eta_next = _nexus_estimate_remaining(phase_index + 1) if phase_index + 1 < len(PHASE_KEYS) else None
                    next_phase_num = min(phase_num + 1, 14)
                    next_label = "Mark built" if phase_num == 14 else (phases[next_phase_num - 1][2] if next_phase_num <= len(phases) else "—")
                    _save_graph_nexus_progress(
                        conn, phase_num, pct_after, f"{phase_label} — done", "running",
                        eta_remaining_sec=eta_next, current_phase_number=next_phase_num, current_phase_label=next_label,
                        stage_update={
                            "stage_index": phase_index,
                            "status": "completed",
                            "message": f"{phase_label} — done",
                            "duration_sec": round(elapsed, 1),
                            "details": {"cached": _nexus_stage_stats.get("cached", 0), "downloaded": _nexus_stage_stats.get("downloaded", 0)},
                        },
                    )
                    # Mark sec_edgar_scraper card as completed when Phase 3: Supply chain finishes
                    if phase_num == 4:
                        try:
                            store.update(GRAPH_NEXUS_PROGRESS_TABLE, "sec_edgar_scraper", {
                                "status": "completed",
                                "message": f"{phase_label} — done",
                                "last_updated": _store_now(),
                            })
                        except Exception:
                            pass

            # Phase 1b: Market cap enrichment (runs after Phase 1, or when selected_phases contains 15)
            _run_1b = (1 in selected_phase_set or _PHASE1B_NUM in selected_phase_set or
                       (not selected_phase_set and start_phase <= 1 <= end_phase))
            if _run_1b and not _nexus_control_want_stop(conn):
                _log("Phase 1b: Market cap enrichment (yfinance)...", "cyan")
                if conn:
                    _save_graph_nexus_progress(conn, 1, PHASE_PCT[2], "Phase 1b: Market cap enrichment — in progress", "running")
                _nexus_stage_reset()
                _nexus_phase_start_time = time.monotonic()
                phase1b_market_cap_enrichment(session)
                elapsed_1b = time.monotonic() - _nexus_phase_start_time
                _log(f"Phase 1b: done in {_nexus_format_duration(elapsed_1b)}.", "green")
                if conn:
                    _save_graph_nexus_progress(conn, 1, PHASE_PCT[2], f"Phase 1b: Market cap enrichment — done ({_nexus_format_duration(elapsed_1b)})", "running")

            # Mark built (only if running through the final phase)
            if end_phase >= 14:
                if conn:
                    _save_graph_nexus_progress(conn, 14, PHASE_PCT[15], "Marking graph as built...", "running", stage_update={"stage_index": 16, "status": "running", "message": "Marking graph as built..."})
                _nexus_stage_reset()
                _nexus_phase_start_time = time.monotonic()
                mark_built(session)
                elapsed = time.monotonic() - _nexus_phase_start_time
                runtimes = _nexus_runtimes_load()
                runtimes.setdefault("mark_built", []).append(elapsed)
                runtimes["mark_built"] = runtimes["mark_built"][-_MAX_RUNTIMES_PER_PHASE:]
                _nexus_runtimes_save(runtimes)
                _nexus_stage_log("Mark built", PHASE_PCT[16], elapsed, "mark_built", 16)

        if conn:
            _mark_nexus_bootstrap_complete(True, start_phase, end_phase)
            _persist_nexus_temporal_state(conn)
            if selected_phase_set:
                final_label = f"Selected phases {', '.join(str(p) for p in sorted(selected_phase_set))} complete."
            else:
                final_label = "Build complete." if end_phase >= 14 else f"Phase range {start_phase}–{end_phase} complete."
            pct = 100.0 if end_phase >= 14 else PHASE_PCT[min(end_phase + 1, len(PHASE_PCT) - 1)]
            if end_phase >= 14:
                _save_graph_nexus_progress(
                    conn, 14, 100.0, final_label, "completed", current_phase_label=final_label,
                    stage_update={"stage_index": 16, "status": "completed", "message": final_label, "duration_sec": round(elapsed, 1)},
                )
            else:
                _save_graph_nexus_progress(conn, end_phase, pct, final_label, "completed", current_phase_label=final_label)
        _log("Graph Nexus build complete.", "green")
        _build_final_status = "ok"
        _nexus_rethink_conn = None
        _NEXUS_REQUESTED_PHASE_RANGE[0] = {"start": None, "end": None}
        _NEXUS_REQUESTED_PHASE_SELECTION[0] = None
        return True

    except SystemExit:
        _build_final_status = "exit"
        _NEXUS_REQUESTED_PHASE_RANGE[0] = {"start": None, "end": None}
        _NEXUS_REQUESTED_PHASE_SELECTION[0] = None
        raise
    except Exception as e:
        _build_final_status = "failed"
        _log_stage_error("Build runner", "phase execution", str(e), e)
        _nexus_rethink_conn = None
        _NEXUS_REQUESTED_PHASE_RANGE[0] = {"start": None, "end": None}
        _NEXUS_REQUESTED_PHASE_SELECTION[0] = None
        if conn:
            try:
                prog = _load_graph_nexus_progress(conn)
                last = int(prog.get("last_completed_phase", 0)) if prog else 0
                _save_graph_nexus_progress(conn, last, 0, str(e)[:200], "failed", current_phase_label="Failed")
            except Exception:
                pass
        import traceback
        _log(traceback.format_exc(), "red")
        return False
    finally:
        # Finalize NexusGraphBuilds row + close log file (always runs).
        if conn and _build_row_id:
            try:
                # Re-open a throwaway session for final Neo4j counts.
                _final_session = None
                try:
                    _final_session = driver.session() if driver else None
                except Exception:
                    _final_session = None
                try:
                    _ngb_row_finalize(
                        _r_ngb, conn, _build_row_id,
                        status=_build_final_status,
                        session=_final_session,
                        log_file_path=_build_log_path,
                        log_tail=_build_log_buffer,
                    )
                finally:
                    if _final_session is not None:
                        try:
                            _final_session.close()
                        except Exception:
                            pass
            except Exception as _e_final:
                _log(f"NexusGraphBuilds row_finalize failed (non-fatal): {_e_final}", "yellow")
        try:
            from intellistock_logger import intellistock_logger as _logger_close
            _logger_close.close_nexus_graph_log_file()
            _logger_close.clear_nexus_graph_log_buffer()
        except Exception:
            pass
        if _build_log_file is not None:
            try:
                _ngb_close_log(_build_log_file, _build_final_status, {"build_id": _build_row_id or "?"})
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────────────
# Entry points
# ──────────────────────────────────────────────────────────────────────────────
def _migrate_legacy_nexus_control_phase_value(value, *, is_end_field: bool):
    if value in (None, ""):
        return None
    try:
        phase = int(value)
    except Exception:
        return value
    if phase < 1:
        return value
    if phase == 7 and is_end_field:
        return 8
    if phase >= 8:
        return min(14, phase + 1)
    return phase


def _normalize_nexus_control_phase_values(values):
    if values in (None, ""):
        return None
    if not isinstance(values, (list, tuple, set)):
        return None
    normalized = []
    for value in values:
        try:
            phase = int(value)
        except Exception:
            continue
        if 1 <= phase <= 14:
            normalized.append(phase)
    deduped = sorted(set(normalized))
    return deduped or None


def _load_nexus_control_doc(conn) -> dict:
    if conn is None:
        return {}
    try:
        doc = store.get(NEXUS_CONTROL_TABLE, NEXUS_CONTROL_ID)
        if not isinstance(doc, dict):
            return {}
        schema_version = int(doc.get("phase_selector_schema_version") or 1)
        if schema_version < 3:
            migrated = {
                "phase_selector_schema_version": 3,
                "start_phase": _migrate_legacy_nexus_control_phase_value(doc.get("start_phase"), is_end_field=False),
                "end_phase": _migrate_legacy_nexus_control_phase_value(doc.get("end_phase"), is_end_field=True),
                "selected_phases": _normalize_nexus_control_phase_values(doc.get("selected_phases")),
                "auto_update_start_phase": _migrate_legacy_nexus_control_phase_value(doc.get("auto_update_start_phase"), is_end_field=False),
                "auto_update_end_phase": _migrate_legacy_nexus_control_phase_value(doc.get("auto_update_end_phase"), is_end_field=True),
            }
            try:
                store.update(NEXUS_CONTROL_TABLE, NEXUS_CONTROL_ID, migrated)
            except Exception:
                pass
            doc.update(migrated)
        return doc
    except Exception:
        return {}


def _update_nexus_control_doc(conn, update: dict) -> None:
    if conn is None or not update:
        return
    try:
        update = dict(update)
        update.setdefault("phase_selector_schema_version", 3)
        store.update(NEXUS_CONTROL_TABLE, NEXUS_CONTROL_ID, update)
    except Exception as e:
        _log(f"Could not update Nexus control doc: {e}", "yellow")


def _persist_nexus_temporal_state(conn) -> None:
    if conn is None:
        return
    state = _nexus_temporal_state()
    _update_nexus_control_doc(conn, {
        "historical_mode_enabled": bool(state.get("historical_mode_enabled", False)),
        "historical_start_date": state.get("historical_start_date"),
        "historical_bootstrap_complete": bool(state.get("historical_bootstrap_complete", False)),
        "historical_coverage_end": state.get("historical_coverage_end"),
        "historical_phase_manifests": dict(state.get("historical_phase_manifests") or {}),
    })


def _mark_nexus_bootstrap_complete(success: bool, start_phase: int, end_phase: int) -> None:
    if not success or not _nexus_historical_enabled():
        return
    if start_phase > 1 or end_phase < 14:
        return
    state = _nexus_temporal_state()
    state["historical_bootstrap_complete"] = True
    state["historical_coverage_end"] = _nexus_today_iso()


def _parse_iso_datetime(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _compute_next_auto_update_at(interval_hours: int) -> str:
    hours = max(1, int(interval_hours or 168))
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


def _wait_for_next_nexus_run(conn) -> str:
    while True:
        if conn and _nexus_control_want_stop(conn):
            return "stop"
        ctrl = _load_nexus_control_doc(conn)
        if ctrl.get("start_phase") is not None or ctrl.get("end_phase") is not None:
            return "manual"
        next_run = _parse_iso_datetime(ctrl.get("next_auto_update_at"))
        if next_run is None:
            return "schedule"
        now = datetime.now(timezone.utc)
        if next_run <= now:
            return "auto"
        time.sleep(max(1, min(30, int((next_run - now).total_seconds()))))


def main():
    _progress(0.0, "Graph Nexus service starting.", "green")
    driver, ok = wait_for_neo4j()
    if not ok or not driver:
        _log("Cannot connect to Neo4j after retries. Exiting.", "red")
        _delay_then_exit(1)
    conn = _get_rethink_conn()
    if conn:
        _ensure_progress_table(conn)
        time.sleep(1)
        if _nexus_control_want_stop(conn):
            _log("NexusControl running=False; exiting.", "yellow")
            conn.close()
            driver.close()
            sys.exit(0)
    try:
        while True:
            ctrl_before = _load_nexus_control_doc(conn)
            auto_enabled = bool(ctrl_before.get("auto_update_enabled", False))
            force_bootstrap_rebuild = bool(ctrl_before.get("force_bootstrap_rebuild", False))
            bootstrap_complete_before = _nexus_bootstrap_considered_complete(ctrl_before)
            bootstrap_run = bool(
                ctrl_before.get("historical_mode_enabled")
                and ctrl_before.get("historical_start_date")
                and (force_bootstrap_rebuild or not bootstrap_complete_before)
            )
            run_started_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            run_start_update = {
                "last_run_started_at": run_started_at,
                "last_run_status": "running",
            }
            if bootstrap_run:
                run_start_update.update({
                    "last_historical_bootstrap_started_at": run_started_at,
                    "last_historical_bootstrap_completed_at": None,
                    "last_historical_bootstrap_duration_sec": None,
                    "last_historical_bootstrap_status": "running",
                })
            _update_nexus_control_doc(conn, run_start_update)
            success = _run_build(driver, conn)
            run_completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            ctrl_after = _load_nexus_control_doc(conn)
            auto_enabled = bool(ctrl_after.get("auto_update_enabled", False))
            update = {
                "last_run_completed_at": run_completed_at,
                "last_run_status": "completed" if success else "failed",
                "rebuild_requested_at": None,
                "start_phase": None,
                "end_phase": None,
                "force_bootstrap_rebuild": False,
            }
            if success and auto_enabled:
                update["last_auto_update_at"] = run_completed_at
                update["next_auto_update_at"] = _compute_next_auto_update_at(
                    int(ctrl_after.get("auto_update_interval_hours") or 168)
                )
            elif not auto_enabled:
                update["next_auto_update_at"] = None
            if bootstrap_run:
                bootstrap_complete_now = bool(ctrl_after.get("historical_bootstrap_complete"))
                if success and bootstrap_complete_now:
                    duration_sec = None
                    try:
                        start_dt = datetime.fromisoformat(run_started_at.replace("Z", "+00:00"))
                        end_dt = datetime.fromisoformat(run_completed_at.replace("Z", "+00:00"))
                        duration_sec = round(max(0.0, (end_dt - start_dt).total_seconds()), 1)
                    except Exception:
                        duration_sec = None
                    update["last_historical_bootstrap_completed_at"] = run_completed_at
                    update["last_historical_bootstrap_duration_sec"] = duration_sec
                    update["last_historical_bootstrap_status"] = "completed"
                else:
                    update["last_historical_bootstrap_status"] = (
                        "partial" if success else ("stopped" if conn and _nexus_control_want_stop(conn) else "failed")
                    )
            _update_nexus_control_doc(conn, update)
            if not success:
                if conn and _nexus_control_want_stop(conn):
                    _log("NexusControl running=False; exiting.", "yellow")
                    break
                _delay_then_exit(1)
            if not auto_enabled:
                sys.exit(0)

            next_run = _parse_iso_datetime(update.get("next_auto_update_at"))
            if next_run is not None:
                _log(f"Next Nexus auto-update scheduled for {next_run.strftime('%Y-%m-%d %H:%M UTC')}.", "cyan")
            wait_reason = _wait_for_next_nexus_run(conn)
            if wait_reason == "stop":
                _log("NexusControl running=False; exiting.", "yellow")
                break
            if wait_reason in ("auto", "schedule"):
                ctrl_wait = _load_nexus_control_doc(conn)
                auto_start = ctrl_wait.get("auto_update_start_phase")
                auto_end = ctrl_wait.get("auto_update_end_phase")
                if (
                    ctrl_wait.get("historical_mode_enabled")
                    and ctrl_wait.get("historical_start_date")
                    and _nexus_bootstrap_considered_complete(ctrl_wait)
                ):
                    _log(
                        "Auto-update scheduled in incremental mode; existing historical bootstrap coverage will be reused.",
                        "cyan",
                    )
                # Default to phase 1 so scheduled auto-updates still refresh the
                # Polygon universe (phase 1) and SEC ticker rows (phase 2). The
                # previous default of 3 silently skipped new listings/delistings
                # and any fresh SEC ticker-to-CIK mapping updates. Operators who
                # want the old behavior can set auto_update_start_phase=3 in
                # NexusControl.
                _update_nexus_control_doc(conn, {
                    "start_phase": int(auto_start) if auto_start is not None else 1,
                    "end_phase": int(auto_end) if auto_end is not None else 14,
                    "next_auto_update_at": None,
                })
    finally:
        for obj in (conn, driver):
            if obj:
                try:
                    obj.close()
                except Exception:
                    pass


def _next_scheduled_run_local() -> datetime:
    parts  = GRAPH_NEXUS_UPDATE_TIME.split(":")
    hour   = int(parts[0]) if parts else 2
    minute = int(parts[1]) if len(parts) > 1 else 0
    now    = datetime.now().replace(tzinfo=None)
    next_run = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return next_run


def main_live():
    main()


if __name__ == "__main__":
    live = (
        os.environ.get("GRAPH_NEXUS_LIVE_UPDATE", "").strip().lower() in ("1", "true", "yes")
        or "--live" in sys.argv
    )
    if live:
        main_live()
    else:
        main()
