# INTELLISTOCK_SCHEMA: {"strategy": "Earnings", "weight": 0.5, "execution_position": 0, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"capital_pct": 0.10, "lookahead_days": 14, "max_earnings_stocks": 50, "sell_days_before": 1, "buy_days_before": 2, "news_days": 7, "sell_days_after_earnings": 1, "max_allocation_pct_per_name": 30, "min_confidence_to_buy": 0.5, "max_post_earnings_volatility_pct": 0, "allow_symbol_date_cache_fallback": false, "llm_provider": "gemini", "model_name": "gemini-2.0-flash-exp", "llm_api_key": "<required>", "openai_base_url": "<optional>", "azure_openai_api_key": "<optional>", "azure_openai_endpoint": "<optional>", "azure_openai_api_version": "2024-10-21", "alpaca_key": "<optional>", "alpaca_secret": "<optional>"}}
# INTELLISTOCK_DESCRIPTION: Earnings run-once strategy. LLM required for sentiment. Fetches earnings dates (yfinance + optional StockTwits API), gathers news and uses LLM to infer good/bad earnings, schedules future trades (buy before good, sell before bad). Uses future cache; can find symbols outside watchlist. Set aside capital via capital_pct (e.g. 0.10 = 10%% of portfolio); reserved for this strategy only.
# DIFFICULTY: 3
"""
Earnings strategy (run_once at start of each pass).

- Queries when earnings occur: yfinance fundamentals (nextEarningsDate) and optional
  StockTwits earnings calendar API (api.stocktwits.com/api/2/discover/earnings_calendar).
- For symbols with earnings in the next lookahead_days, fetches news (Alpaca) and runs
  optional LLM sentiment to infer whether earnings are likely good or bad (e.g. revenue
  miss, guidance cut → bad; beat → good).
- Schedules future trades via future_trades_util: sell before bad earnings (e.g. 1 day
  before), buy before good earnings (e.g. 2 days before). Also schedules for symbols
  outside the instance ticker list so the broker can execute them when the target date
  arrives (if prices are available).
- Returns scores only for symbols in the provided list (voting); extra symbols are
  only in the future queue.
- Config: capital_pct (0-1), lookahead_days, max_earnings_stocks (cap by importance, default 50),
  sell_days_before, buy_days_before, news_days (default 7), sell_days_after_earnings (default 1),
  max_allocation_pct_per_name (default 30), min_confidence_to_buy (default 0.5),
  max_post_earnings_volatility_pct (0 disables), allow_symbol_date_cache_fallback (default false),
  llm_api_key (required; or provider env key), model_name, openai_base_url,
  azure_openai_endpoint, azure_openai_api_version, use_stocktwits_calendar.
  Uses historical beat/miss, post-earnings volatility, and company research (fundamentals) as LLM inputs.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone, date as _date_type
from typing import Any

# Cache keys in strategy_cache; max entries to avoid unbounded growth
_EARNINGS_NEWS_CACHE_KEY = "_earnings_news_cache"
_EARNINGS_LLM_CACHE_KEY = "_earnings_llm_cache"
_EARNINGS_NEWS_CACHE_MAX = 500
_EARNINGS_LLM_CACHE_MAX = 500
_EARNINGS_POSITIONS_KEY = "_earnings_positions"  # {symbol: shares_bought}
_EARNINGS_EPS_CACHE_KEY = "_earnings_eps_cache"
_EARNINGS_BEAT_MISS_CACHE_KEY = "_earnings_beat_miss_cache"
_EARNINGS_VOLATILITY_CACHE_KEY = "_earnings_volatility_cache"
_EARNINGS_RESEARCH_CACHE_KEY = "_earnings_research_cache"
_EARNINGS_DB_KEY_SCHEMA_VERSION = "strict_asof_v1"

try:
    import sys
    _broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _broker_dir not in sys.path:
        sys.path.insert(0, _broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="EarningsStrategy")
except Exception:
    def _log(msg, color="white"):
        print(f"[EarningsStrategy] {msg}")

# ── RethinkDB persistent cache ──────────────────────────────────────────────
try:
    from rethinkdb import RethinkDB
    r = RethinkDB()
    DB_NAME = 'IntelliStock'
    RETHINKDB_HOST = os.environ.get('RETHINKDB_HOST', 'localhost')
    RETHINKDB_PORT = int(os.environ.get('RETHINKDB_PORT', '28015'))
    EARNINGS_CACHE_TABLE = 'EarningsLLMCache'
    _db_available = True
except Exception:
    r = None
    _db_available = False
    _log("RethinkDB not available; persistent LLM caching will be disabled", "yellow")

_earnings_db_conn = None
_cache_table_ensured = False


def _get_db_conn(reuse: bool = True):
    """Get database connection for persistent caching. Reuses single connection per process."""
    global _earnings_db_conn
    if not _db_available or r is None:
        return None
    if reuse and _earnings_db_conn is not None:
        try:
            r.db_list().run(_earnings_db_conn)
            return _earnings_db_conn
        except Exception:
            _earnings_db_conn = None
    try:
        _earnings_db_conn = r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)
        return _earnings_db_conn
    except Exception as e:
        _log(f"Could not connect to DB for caching: {e}", "yellow")
        return None


def _ensure_cache_table(conn):
    """Ensure EarningsLLMCache table exists. Called once per process."""
    global _cache_table_ensured
    if conn is None or _cache_table_ensured:
        return
    try:
        dbs = list(r.db_list().run(conn))
        if DB_NAME not in dbs:
            r.db_create(DB_NAME).run(conn)
        tables = list(r.db(DB_NAME).table_list().run(conn))
        if EARNINGS_CACHE_TABLE not in tables:
            r.db(DB_NAME).table_create(EARNINGS_CACHE_TABLE).run(conn)
        _cache_table_ensured = True
    except Exception as e:
        _log(f"Could not ensure cache table: {e}", "yellow")


def _db_cache_key(
    provider: str,
    model: str,
    symbol: str,
    earn_date: str,
    headlines_hash: str,
    eps_str: str,
    beat_miss_hash: str = "",
    volatility_hash: str = "",
    research_hash: str = "",
    market_ctx_hash: str = "",
) -> str:
    """Create a deterministic cache key for RethinkDB.
    Includes model and all LLM-relevant inputs so stale decisions are not reused."""
    raw = (
        f"{_EARNINGS_DB_KEY_SCHEMA_VERSION}|{provider}|{model}|{symbol}|{earn_date}|{headlines_hash}|{eps_str}|"
        f"{beat_miss_hash}|{volatility_hash}|{research_hash}|{market_ctx_hash}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _db_get_cached_decisions(conn, cache_keys: list[str]) -> dict[str, dict]:
    """Batch-fetch cached LLM decisions from RethinkDB."""
    if conn is None or not cache_keys:
        return {}
    try:
        _ensure_cache_table(conn)
        results = {}
        cursor = r.db(DB_NAME).table(EARNINGS_CACHE_TABLE).get_all(*cache_keys).run(conn)
        for doc in cursor:
            if doc and 'id' in doc:
                results[doc['id']] = {
                    "sentiment": doc.get("sentiment", 0),
                    "allocation_pct": doc.get("allocation_pct", 0.0),
                    "confidence": doc.get("confidence", None),
                    "avoid": bool(doc.get("avoid", False)),
                    "avoid_reason": (doc.get("avoid_reason") or "").strip(),
                }
        return results
    except Exception as e:
        _log(f"DB cache batch lookup error: {e}", "yellow")
        return {}


def _db_get_cached_decisions_by_symbol_earn(
    conn, provider: str, model: str, sym_earn_list: list[tuple[str, str]]
) -> dict[tuple[str, str], dict]:
    """Fallback: fetch cached LLM decisions by (symbol, earn_date) when exact cache key missed.
    Returns {(symbol, earn_date): decision}. Uses latest cached_at when multiple exist."""
    if conn is None or not sym_earn_list:
        return {}
    try:
        _ensure_cache_table(conn)
        # RethinkDB: filter by provider, model, and (symbol, earn_date) in sym_earn_list (use lambda so no r.row in nested query)
        sym_earn_expr = r.expr([list(k) for k in sym_earn_list])
        cursor = r.db(DB_NAME).table(EARNINGS_CACHE_TABLE).filter(
            lambda row: r.and_(
                row["provider"].eq(provider),
                row["model"].eq(model),
                sym_earn_expr.contains([row["symbol"], row["earn_date"]]),
            )
        ).run(conn)
        # Build (sym, earn_date) -> best doc (latest cached_at)
        by_key: dict[tuple[str, str], tuple[str, dict]] = {}  # (sym, earn_date) -> (cached_at, decision)
        for doc in cursor:
            if not doc:
                continue
            sym = (doc.get("symbol") or "").strip().upper()
            earn_date = (doc.get("earn_date") or "")[:10]
            if not sym or not earn_date:
                continue
            key = (sym, earn_date)
            decision = {
                "sentiment": doc.get("sentiment", 0),
                "allocation_pct": doc.get("allocation_pct", 0.0),
                "confidence": doc.get("confidence", None),
                "avoid": bool(doc.get("avoid", False)),
                "avoid_reason": (doc.get("avoid_reason") or "").strip(),
            }
            cached_at = doc.get("cached_at") or ""
            if key not in by_key or cached_at > by_key[key][0]:
                by_key[key] = (cached_at, decision)
        return {k: v[1] for k, v in by_key.items()}
    except Exception as e:
        _log(f"DB cache fallback (symbol+earn_date) lookup error: {e}", "yellow")
        return {}


def _db_save_cached_decisions(conn, entries: list[dict]):
    """Batch-save LLM decisions to RethinkDB. Each entry: {id, symbol, provider, model, ...}."""
    if conn is None or not entries:
        return
    try:
        _ensure_cache_table(conn)
        r.db(DB_NAME).table(EARNINGS_CACHE_TABLE).insert(entries, conflict='replace').run(conn)
        _log(f"DB cache: saved {len(entries)} LLM decisions to RethinkDB", "cyan")
    except Exception as e:
        _log(f"DB cache save error: {e}", "yellow")


# Alpaca News (same base as news strategy)
ALPACA_NEWS_BASE = "https://data.alpaca.markets/v1beta1"
STOCKTWITS_EARNINGS_URL = "https://api.stocktwits.com/api/2/discover/earnings_calendar"

# Symbols that have no earnings (ETFs, indices, etc.). Yahoo Finance returns 404 for their fundamentals/quoteSummary.
# Skipping these avoids repeated HTTP 404 errors when building the earnings universe.
_SKIP_EARNINGS_LOOKUP = frozenset({
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "VEA", "VWO", "EFA", "EEM", "AGG", "BND", "LQD", "HYG",
    "GLD", "SLV", "VNQ", "XLF", "XLK", "XLE", "XLV", "XLI", "XLP", "XLY", "XLB", "XLU", "XLRE", "VTV", "VUG",
})

# Fallback tickers when StockTwits returns nothing and watchlist is empty or only ETFs. Liquid names that report earnings.
_FALLBACK_EARNINGS_TICKERS = (
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "BRK.B", "JPM", "V", "JNJ", "WMT", "PG", "MA", "HD",
    "DIS", "BAC", "ADBE", "XOM", "PFE", "CSCO", "NFLX", "CRM", "KO", "PEP", "COST", "ABT", "AVGO", "TMO", "ACN",
    "NEE", "DHR", "MCD", "ABBV", "NKE", "PM", "BMY", "UNH", "TXN", "RTX", "HON", "INTC", "AMD", "QCOM", "ORCL",
    "LOW", "UPS", "IBM", "GE", "CAT", "AMGN", "SBUX", "DE", "GILD", "INTU", "AXP", "LMT", "BKNG", "ADI", "CVX",
)
_MAX_FALLBACK_QUERIES = 35  # limit yfinance calls when using fallback


def _to_date(dt) -> datetime | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt
    if hasattr(dt, "year"):
        return datetime(dt.year, dt.month, dt.day)
    try:
        return datetime.fromisoformat(str(dt)[:10])
    except Exception:
        return None


def _date_str(dt) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d")
    if hasattr(dt, "strftime"):
        return dt.strftime("%Y-%m-%d")
    return str(dt)[:10]


def _to_utc_naive_datetime(ts) -> datetime | None:
    """Normalize timestamp-like input to naive UTC datetime."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        dt = ts
    else:
        s = str(ts).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            try:
                from dateutil import parser as _du_parser
                dt = _du_parser.parse(str(ts))
            except Exception:
                return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _strict_daily_cutoff_date(current_dt: datetime) -> _date_type:
    """Backtest-safe daily cutoff: use the previous business day."""
    prev = (current_dt.date() - timedelta(days=1))
    return _prev_business_day(prev)


def _to_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _to_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(value)


def _normalize_llm_provider(provider: str) -> str:
    name = (provider or "gemini").strip().lower()
    return name if name in ("gemini", "deepseek", "openai", "azure") else "gemini"


def _default_model_for_provider(provider: str) -> str:
    provider = _normalize_llm_provider(provider)
    if provider == "deepseek":
        return "deepseek-chat"
    if provider == "openai":
        return "gpt-4.1-mini"
    if provider == "azure":
        return (
            os.environ.get("AZURE_OPENAI_DEPLOYMENT")
            or os.environ.get("AZURE_OPENAI_MODEL")
            or "gpt-4.1-mini"
        ).strip()
    return "gemini-2.0-flash-exp"


def _default_api_key_for_provider(provider: str) -> str:
    provider = _normalize_llm_provider(provider)
    if provider == "deepseek":
        return os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if provider == "openai":
        return os.environ.get("OPENAI_API_KEY", "").strip()
    if provider == "azure":
        return os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    return os.environ.get("GEMINI_API_KEY", "").strip()


def _resolve_provider_config(config: dict, provider: str) -> dict[str, str]:
    provider = _normalize_llm_provider(provider)
    if provider == "azure":
        endpoint = (
            (config.get("azure_openai_endpoint") or "").strip()
            or os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        )
        api_version = (
            (config.get("azure_openai_api_version") or "").strip()
            or os.environ.get("OPENAI_API_VERSION", "").strip()
            or "2024-10-21"
        )
        return {"azure_endpoint": endpoint, "api_version": api_version}
    if provider == "openai":
        base_url = (
            (config.get("openai_base_url") or "").strip()
            or os.environ.get("OPENAI_BASE_URL", "").strip()
        )
        return {"base_url": base_url} if base_url else {}
    if provider == "nvidia":
        base_url = (
            (config.get("nvidia_base_url") or "").strip()
            or os.environ.get("NVIDIA_BASE_URL", "").strip()
            or "https://integrate.api.nvidia.com/v1"
        )
        return {"base_url": base_url}
    return {}


def _prev_business_day(d: _date_type) -> _date_type:
    """Roll a date back to the nearest preceding business day (Mon-Fri).
    If already Mon-Fri, returns as-is."""
    wd = d.weekday()  # 0=Mon, 6=Sun
    if wd == 5:  # Saturday → Friday
        return d - timedelta(days=1)
    if wd == 6:  # Sunday → Friday
        return d - timedelta(days=2)
    return d


def _next_business_day(d: _date_type) -> _date_type:
    """Roll a date forward to the nearest following business day (Mon-Fri).
    If already Mon-Fri, returns as-is."""
    wd = d.weekday()
    if wd == 5:  # Saturday → Monday
        return d + timedelta(days=2)
    if wd == 6:  # Sunday → Monday
        return d + timedelta(days=1)
    return d


STOCKTWITS_RETRY_ATTEMPTS = 3
STOCKTWITS_RETRY_WAIT_SEC = 10


def _fetch_stocktwits_earnings(date_from: str, date_to: str) -> list[dict]:
    """Fetch earnings calendar from StockTwits API. Returns list of {symbol, date, importance, ...} or [] on error.
    Retries up to STOCKTWITS_RETRY_ATTEMPTS times with STOCKTWITS_RETRY_WAIT_SEC between attempts on timeout/connection errors."""
    import requests
    last_error = None
    for attempt in range(1, STOCKTWITS_RETRY_ATTEMPTS + 1):
        try:
            _log(f"Fetching StockTwits earnings calendar: {date_from} to {date_to} (attempt {attempt}/{STOCKTWITS_RETRY_ATTEMPTS})", "white")
            r = requests.get(
                STOCKTWITS_EARNINGS_URL,
                params={"date_from": date_from, "date_to": date_to},
                timeout=15,
                headers={"User-Agent": "IntelliStock/1.0"},
            )
            if r.status_code != 200:
                _log(f"StockTwits earnings calendar returned HTTP {r.status_code}", "yellow")
                return []
            data = r.json()
            if not isinstance(data, dict):
                _log("StockTwits response is not a dict; skipping calendar.", "yellow")
                return []
            # StockTwits actual format: { "date_from", "date_to", "earnings": { "YYYY-MM-DD": { "stocks": [ {"symbol", "date", "time", "title", "importance"}, ... ], ... }, ... } }
            earnings = data.get("earnings")
            if isinstance(earnings, dict):
                out = []
                for date_str, day_data in earnings.items():
                    if not re.match(r"\d{4}-\d{2}-\d{2}", str(date_str)[:10]):
                        continue
                    stocks = (day_data or {}).get("stocks") if isinstance(day_data, dict) else []
                    if not isinstance(stocks, list):
                        continue
                    for s in stocks:
                        if not isinstance(s, dict):
                            continue
                        sym = (s.get("symbol") or s.get("ticker") or "").strip().upper()
                        d = s.get("date") or date_str
                        if sym:
                            ds = d[:10] if isinstance(d, str) else (getattr(d, "strftime", lambda x: "")( "%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10])
                            imp = int(s.get("importance", 0)) if isinstance(s.get("importance"), (int, float)) else 0
                            out.append({"symbol": sym, "date": ds, "importance": max(0, min(5, imp))})
                if out:
                    with_imp = sum(1 for e in out if e.get("importance", 0) > 0)
                    _log(f"StockTwits calendar: {len(out)} entries ({with_imp} with importance > 0)", "cyan")
                    return out
            # Fallback: two arrays symbols[] and dates[] (no importance)
            syms = data.get("symbols") or data.get("tickers")
            dates = data.get("dates") or data.get("earnings_dates")
            if isinstance(syms, list) and isinstance(dates, list) and len(syms) == len(dates):
                out = []
                for i, s in enumerate(syms):
                    d = dates[i] if i < len(dates) else None
                    if s and d:
                        ds = d[:10] if isinstance(d, str) else (getattr(d, "strftime", lambda x: "")( "%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10])
                        out.append({"symbol": str(s).strip().upper(), "date": ds, "importance": 0})
                if out:
                    return out
            # Fallback: list under "calendar", "results", "data"
            for key in ("calendar", "results", "data"):
                if key in data and isinstance(data[key], list):
                    _log(f"StockTwits using fallback key '{key}' ({len(data[key])} items); no importance data.", "yellow")
                    return data[key]
            _log("StockTwits response has no recognized earnings structure.", "yellow")
            return []
        except Exception as e:
            last_error = e
            _log(f"StockTwits earnings fetch failed (attempt {attempt}/{STOCKTWITS_RETRY_ATTEMPTS}): {e}", "yellow")
            if attempt < STOCKTWITS_RETRY_ATTEMPTS:
                _log(f"Waiting {STOCKTWITS_RETRY_WAIT_SEC}s before retry.", "yellow")
                time.sleep(STOCKTWITS_RETRY_WAIT_SEC)
    _log(f"StockTwits earnings fetch failed after {STOCKTWITS_RETRY_ATTEMPTS} attempts: {last_error}", "yellow")
    return []


def _earnings_date_from_yfinance(symbol: str) -> datetime | None:
    """Get next earnings date for symbol from yfinance. Returns naive datetime or None."""
    try:
        import io, contextlib
        from fundamentals_util import get_fundamentals
        with contextlib.redirect_stderr(io.StringIO()):
            fund = get_fundamentals(symbol)
        d = fund.get("nextEarningsDate")
        return _to_date(d)
    except Exception:
        return None


_EARNINGS_DATES_ALL_CACHE_KEY = "_earnings_dates_all"  # {symbol: [date, ...]} — full history, filtered in memory


def _get_all_earnings_dates_yfinance(symbol: str, strategy_cache: dict | None) -> list[_date_type]:
    """Get ALL earnings dates for a symbol (cached per backtest run).
    Returns full sorted list; caller filters to window. Caching avoids repeated yfinance calls
    across bars when the window slides by 1 day."""
    if strategy_cache is not None:
        all_cache: dict = strategy_cache.setdefault(_EARNINGS_DATES_ALL_CACHE_KEY, {})
        if symbol in all_cache:
            return all_cache[symbol]  # may be empty list (symbol has no earnings)
    else:
        all_cache = None

    import time as _time
    import io, contextlib
    dates: list[_date_type] = []
    max_retries = 3
    for attempt in range(max_retries):
        try:
            import yfinance as yf
            t = yf.Ticker(symbol)
            df = None
            # Suppress yfinance stderr noise (401, "No earnings dates found", etc.)
            with contextlib.redirect_stderr(io.StringIO()):
                if hasattr(t, "get_earnings_dates"):
                    try:
                        df = t.get_earnings_dates(limit=20)
                    except Exception:
                        pass
                if df is None or (hasattr(df, "empty") and df.empty):
                    if hasattr(t, "earnings_dates"):
                        try:
                            df = t.earnings_dates
                        except Exception:
                            pass
            if df is not None and not (hasattr(df, "empty") and df.empty):
                for idx in df.index:
                    try:
                        if hasattr(idx, "date"):
                            d = idx.date()
                        elif hasattr(idx, "year"):
                            d = _date_type(idx.year, idx.month, idx.day)
                        else:
                            d = datetime.fromisoformat(str(idx)[:10]).date()
                        dates.append(d)
                    except Exception:
                        continue
            break  # success (even if empty)
        except Exception as e:
            err_str = str(e).lower()
            if ("401" in err_str or "unauthorized" in err_str or "crumb" in err_str) and attempt < max_retries - 1:
                _time.sleep(1.5 * (attempt + 1))  # backoff: 1.5s, 3s
                continue
            break

    dates.sort()
    if all_cache is not None:
        all_cache[symbol] = dates
    return dates


def _earnings_dates_from_yfinance(symbol: str, window_start: _date_type, window_end: _date_type,
                                   strategy_cache: dict | None = None) -> list[_date_type]:
    """Get earnings dates for a symbol within the given window.
    Uses cached full history to avoid repeated API calls across bars."""
    all_dates = _get_all_earnings_dates_yfinance(symbol, strategy_cache)
    return [d for d in all_dates if window_start <= d <= window_end]


def _get_expected_eps_str(symbol: str) -> str:
    """Return a short string with expected EPS for upcoming earnings (e.g. 'expected EPS 0.45') or '' if unavailable.
    Supports both newer get_earnings_estimate() and older .earnings_estimate DataFrame fallback.
    Suppresses yfinance stderr spam (401 errors, etc.)."""
    try:
        import yfinance as yf
        import io, contextlib
        t = yf.Ticker(symbol)

        est = None
        # Suppress yfinance stderr noise (401, crumb errors)
        with contextlib.redirect_stderr(io.StringIO()):
            # Prefer newer API if available
            if hasattr(t, "get_earnings_estimate"):
                try:
                    est = t.get_earnings_estimate()
                except Exception:
                    pass

            # Fallback: older yfinance uses .earnings_estimate property (DataFrame)
            if est is None or (hasattr(est, "empty") and est.empty):
                if hasattr(t, "earnings_estimate"):
                    try:
                        est = t.earnings_estimate
                    except Exception:
                        pass

        if est is None or (hasattr(est, "empty") and est.empty):
            return ""

        # yfinance: index 0q, +1q, 0y, +1y; columns include avg, low, high
        val = None
        if hasattr(est, "loc") and hasattr(est, "index") and hasattr(est, "columns"):
            for idx in ("0q", "+1q"):
                if idx in est.index and "avg" in est.columns:
                    try:
                        val = est.loc[idx, "avg"]
                        break
                    except (KeyError, TypeError):
                        continue
        if val is None and hasattr(est, "iloc") and len(est) > 0 and "avg" in getattr(est, "columns", []):
            try:
                val = est.iloc[0]["avg"]
            except (KeyError, TypeError):
                pass
        if val is not None and str(val).strip():
            try:
                return "expected EPS %s" % (float(val),)
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    return ""


def _get_beat_miss_str(symbol: str, strategy_cache: dict | None, as_of_date: _date_type | None = None) -> str:
    """Return a short string e.g. 'Last 4 quarters: 3 beats, 1 miss; last surprise % +2.1%' or '' if unavailable.
    Uses get_earnings_history() when available; cached per run."""
    cache_key = (symbol, as_of_date.strftime("%Y-%m-%d")) if as_of_date is not None else symbol
    if strategy_cache is not None:
        cache: dict = strategy_cache.setdefault(_EARNINGS_BEAT_MISS_CACHE_KEY, {})
        if cache_key in cache:
            return cache[cache_key]
    out = ""
    try:
        import io
        import contextlib
        import yfinance as yf
        with contextlib.redirect_stderr(io.StringIO()):
            t = yf.Ticker(symbol)
            if not hasattr(t, "get_earnings_history"):
                if strategy_cache is not None:
                    strategy_cache.setdefault(_EARNINGS_BEAT_MISS_CACHE_KEY, {})[cache_key] = out
                return out
            df = t.get_earnings_history()
        if df is None or (hasattr(df, "empty") and df.empty) or len(df) == 0:
            if strategy_cache is not None:
                strategy_cache.setdefault(_EARNINGS_BEAT_MISS_CACHE_KEY, {})[cache_key] = out
            return out
        # Backtest-safe: ignore earnings records after as_of_date when date info exists.
        if as_of_date is not None:
            try:
                date_series = None
                if "quarter" in getattr(df, "columns", []):
                    date_series = df["quarter"]
                elif len(df.index) > 0 and hasattr(df.index[0], "year"):
                    date_series = df.index
                if date_series is not None:
                    parsed = []
                    for d in date_series:
                        try:
                            if hasattr(d, "date"):
                                parsed.append(d.date())
                            elif hasattr(d, "year"):
                                parsed.append(_date_type(d.year, d.month, d.day))
                            else:
                                parsed.append(datetime.fromisoformat(str(d)[:10]).date())
                        except Exception:
                            parsed.append(None)
                    keep_idx = [i for i, d in enumerate(parsed) if d is not None and d <= as_of_date]
                    if keep_idx:
                        df = df.iloc[keep_idx]
                    else:
                        df = df.iloc[0:0]
            except Exception:
                pass
        if df is None or (hasattr(df, "empty") and df.empty) or len(df) == 0:
            if strategy_cache is not None:
                strategy_cache.setdefault(_EARNINGS_BEAT_MISS_CACHE_KEY, {})[cache_key] = out
            return out
        # Take last 4 quarters
        n = min(4, len(df))
        df = df.iloc[-n:]
        beats = 0
        misses = 0
        last_surprise_pct = None
        for _idx, row in df.iterrows():
            est = row.get("epsEstimate") if hasattr(row, "get") else None
            actual = row.get("epsActual") if hasattr(row, "get") else None
            surprise = row.get("surprisePercent") if hasattr(row, "get") else None
            if actual is not None and est is not None:
                try:
                    a, e = float(actual), float(est)
                    if a >= e:
                        beats += 1
                    else:
                        misses += 1
                except (TypeError, ValueError):
                    pass
            if surprise is not None and last_surprise_pct is None:
                try:
                    last_surprise_pct = float(surprise)
                except (TypeError, ValueError):
                    pass
        if last_surprise_pct is None and "surprisePercent" in getattr(df, "columns", []):
            try:
                last_surprise_pct = float(df["surprisePercent"].iloc[-1])
            except Exception:
                pass
        if beats + misses > 0:
            out = "Last %d quarters: %d beats, %d miss" % (n, beats, misses)
            if last_surprise_pct is not None:
                out += "; last surprise %% %+.1f%%" % (last_surprise_pct * 100)
        if strategy_cache is not None:
            strategy_cache.setdefault(_EARNINGS_BEAT_MISS_CACHE_KEY, {})[cache_key] = out
    except Exception:
        if strategy_cache is not None:
            strategy_cache.setdefault(_EARNINGS_BEAT_MISS_CACHE_KEY, {})[cache_key] = ""
    return out


def _get_post_earnings_volatility_str(
    symbol: str, strategy_cache: dict | None, as_of_date: _date_type | None = None
) -> str:
    """Return e.g. 'Earnings volatility: 5.2%%' (avg |1-day post-earnings move| over last few quarters) or ''."""
    cache_key = (symbol, as_of_date.strftime("%Y-%m-%d")) if as_of_date is not None else symbol
    if strategy_cache is not None:
        cache: dict = strategy_cache.setdefault(_EARNINGS_VOLATILITY_CACHE_KEY, {})
        if cache_key in cache:
            return cache[cache_key]
    out = ""
    try:
        import io
        import contextlib
        import yfinance as yf
        today = as_of_date or datetime.utcnow().date()
        all_dates = _get_all_earnings_dates_yfinance(symbol, strategy_cache)
        past_dates = [d for d in all_dates if d < today][-6:]
        if not past_dates:
            if strategy_cache is not None:
                strategy_cache.setdefault(_EARNINGS_VOLATILITY_CACHE_KEY, {})[cache_key] = out
            return out
        with contextlib.redirect_stderr(io.StringIO()):
            t = yf.Ticker(symbol)
            hist_start = (today - timedelta(days=730)).strftime("%Y-%m-%d")
            hist_end = (today + timedelta(days=1)).strftime("%Y-%m-%d")
            hist = t.history(start=hist_start, end=hist_end)
        if hist is None or (hasattr(hist, "empty") and hist.empty) or len(hist) < 2:
            if strategy_cache is not None:
                strategy_cache.setdefault(_EARNINGS_VOLATILITY_CACHE_KEY, {})[cache_key] = out
            return out
        hist_idx = hist.index
        if hasattr(hist_idx[0], "date"):
            dates = [d.date() for d in hist_idx]
        else:
            dates = [_date_type(d.year, d.month, d.day) for d in hist_idx]
        closes = list(hist["Close"]) if "Close" in getattr(hist, "columns", []) else []
        if not dates or not closes:
            if strategy_cache is not None:
                strategy_cache.setdefault(_EARNINGS_VOLATILITY_CACHE_KEY, {})[cache_key] = out
            return out
        moves = []
        for ed in past_dates:
            try:
                day_before = _prev_business_day(ed)
                day_after = _next_business_day(ed)
                i_before = None
                i_after = None
                for i, d in enumerate(dates):
                    if d == day_before:
                        i_before = i
                    if d == day_after:
                        i_after = i
                if i_before is not None and i_after is not None and i_before < len(closes) and i_after < len(closes):
                    c_before = float(closes[i_before])
                    c_after = float(closes[i_after])
                    if c_before and c_before > 0:
                        moves.append(abs((c_after - c_before) / c_before))
            except Exception:
                continue
        if moves:
            avg_pct = sum(moves) / len(moves) * 100
            out = "Earnings volatility: %.1f%%" % avg_pct
        if strategy_cache is not None:
            strategy_cache.setdefault(_EARNINGS_VOLATILITY_CACHE_KEY, {})[cache_key] = out
    except Exception:
        if strategy_cache is not None:
            strategy_cache.setdefault(_EARNINGS_VOLATILITY_CACHE_KEY, {})[cache_key] = ""
    return out


def _get_company_research_str(symbol: str, current_price: float | None = None) -> str:
    """Return one line: Sector; Analyst: X; target $X vs price $Y; growth/leverage. Uses fundamentals_util."""
    try:
        from fundamentals_util import get_fundamentals
        fund = get_fundamentals(symbol)
        parts = []
        sector = (fund.get("sector") or "").strip()
        if sector:
            parts.append("Sector: %s" % str(sector)[:30])
        rec = (fund.get("recommendationKey") or "").strip()
        if rec:
            parts.append("Analyst: %s" % rec)
        target = fund.get("targetMeanPrice")
        try:
            t = float(target) if target is not None else None
        except (TypeError, ValueError):
            t = None
        if t is not None and t > 0:
            if current_price is not None and current_price > 0:
                parts.append("target $%.2f vs price $%.2f" % (t, current_price))
            else:
                parts.append("target $%.2f" % t)
        for key, label in [("revenueGrowth", "revenue_growth"), ("earningsGrowth", "earnings_growth"), ("debtToEquity", "debtToEquity"), ("returnOnEquity", "ROE")]:
            v = fund.get(key)
            if v is not None:
                try:
                    f = float(v)
                    if key in ("revenueGrowth", "earningsGrowth") and abs(f) <= 2:
                        parts.append("%s: %.1f%%" % (label, f * 100))
                    else:
                        parts.append("%s: %.2f" % (label, f))
                except (TypeError, ValueError):
                    pass
        out = "; ".join(parts) if parts else ""
    except Exception:
        out = ""
    return out


def _slice_history_to_as_of(hist, as_of_date: _date_type):
    """Return rows whose index date is <= as_of_date (works for tz-aware and naive indices)."""
    if hist is None or (hasattr(hist, "empty") and hist.empty):
        return hist
    try:
        idx = getattr(hist, "index", None)
        if idx is None:
            return hist
        mask = []
        for it in idx:
            try:
                d = it.date() if hasattr(it, "date") else _date_type(it.year, it.month, it.day)
                mask.append(d <= as_of_date)
            except Exception:
                mask.append(False)
        if not mask:
            return hist
        return hist.loc[mask]
    except Exception:
        return hist


def _get_market_context_str(as_of_date: _date_type, strategy_cache: dict | None) -> str:
    """Return compact market regime context as of as_of_date (backtest-safe)."""
    cache_key = "_earnings_market_context_cache"
    day_key = as_of_date.strftime("%Y-%m-%d")
    if strategy_cache is not None:
        c = strategy_cache.setdefault(cache_key, {})
        if day_key in c:
            return c[day_key]
    out = ""
    try:
        import io
        import contextlib
        import yfinance as yf

        start = as_of_date - timedelta(days=130)
        end = as_of_date + timedelta(days=1)
        with contextlib.redirect_stderr(io.StringIO()):
            spy_hist = yf.Ticker("SPY").history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
            vix_hist = yf.Ticker("^VIX").history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
        spy_hist = _slice_history_to_as_of(spy_hist, as_of_date)
        vix_hist = _slice_history_to_as_of(vix_hist, as_of_date)
        if spy_hist is not None and not (hasattr(spy_hist, "empty") and spy_hist.empty):
            closes = list(spy_hist["Close"]) if "Close" in getattr(spy_hist, "columns", []) else []
            if len(closes) >= 50:
                c_last = float(closes[-1])
                sma20 = sum(float(x) for x in closes[-20:]) / 20.0
                sma50 = sum(float(x) for x in closes[-50:]) / 50.0
                spy_trend = "bullish" if c_last >= sma20 >= sma50 else ("bearish" if c_last <= sma20 <= sma50 else "mixed")
                out = f"Market: SPY trend {spy_trend}; close {c_last:.2f}; SMA20 {sma20:.2f}; SMA50 {sma50:.2f}"
        if vix_hist is not None and not (hasattr(vix_hist, "empty") and vix_hist.empty):
            vix_vals = list(vix_hist["Close"]) if "Close" in getattr(vix_hist, "columns", []) else []
            if vix_vals:
                vix_last = float(vix_vals[-1])
                vix_regime = "calm" if vix_last < 18 else ("normal" if vix_last < 25 else "stressed")
                if out:
                    out += f"; VIX {vix_last:.2f} ({vix_regime})"
                else:
                    out = f"Market: VIX {vix_last:.2f} ({vix_regime})"
    except Exception:
        out = ""
    if strategy_cache is not None:
        strategy_cache.setdefault(cache_key, {})[day_key] = out
    return out


def _parse_volatility_pct(volatility_str: str) -> float | None:
    """Parse numeric percent from strings like 'Earnings volatility: 5.2%'."""
    if not volatility_str:
        return None
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%", volatility_str)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def _fetch_alpaca_news(symbol: str, start_dt: datetime, end_dt: datetime, key: str, secret: str) -> list:
    if not key or not secret:
        return []
    try:
        import requests
        url = f"{ALPACA_NEWS_BASE}/news"
        headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret, "accept": "application/json"}
        params = {"symbols": symbol, "start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 20}
        r = requests.get(url, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        raw_news = (r.json() or {}).get("news") or []
        if not isinstance(raw_news, list):
            return []

        # Defense-in-depth: enforce timestamp bounds locally in case provider filtering is loose.
        filtered = []
        for a in raw_news:
            if not isinstance(a, dict):
                continue
            article_ts = (
                _to_utc_naive_datetime(a.get("created_at"))
                or _to_utc_naive_datetime(a.get("updated_at"))
                or _to_utc_naive_datetime(a.get("timestamp"))
            )
            if article_ts is None:
                # Strict mode: discard undated articles because we cannot prove point-in-time safety.
                continue
            if start_dt <= article_ts <= end_dt:
                filtered.append(a)
        return filtered
    except Exception:
        return []


def _fetch_news_batch_parallel(
    symbols_with_dates: list[tuple[str, str]],
    news_start: datetime,
    news_end: datetime,
    alpaca_key: str,
    alpaca_secret: str,
    strategy_cache: dict,
    max_workers: int = 8,
) -> dict[str, str]:
    """Fetch Alpaca news for multiple symbols in parallel. Returns {symbol: headlines_text}.
    Uses in-memory cache for hits, parallel HTTP for misses."""
    cache = strategy_cache.get(_EARNINGS_NEWS_CACHE_KEY)
    if cache is None:
        strategy_cache[_EARNINGS_NEWS_CACHE_KEY] = OrderedDict()
        cache = strategy_cache[_EARNINGS_NEWS_CACHE_KEY]

    start_str = news_start.strftime("%Y-%m-%dT%H:%M:%S")
    end_str = news_end.strftime("%Y-%m-%dT%H:%M:%S")

    results: dict[str, str] = {}
    to_fetch: list[str] = []

    for sym, _ in symbols_with_dates:
        cache_key = (sym.upper(), start_str, end_str)
        if cache_key in cache:
            results[sym] = cache[cache_key]
        else:
            to_fetch.append(sym)

    if not to_fetch:
        return results

    def _worker(sym: str) -> tuple[str, str]:
        articles = _fetch_alpaca_news(sym, news_start, news_end, alpaca_key, alpaca_secret)
        text = " ".join((a.get("headline") or a.get("title") or str(a))[:150] for a in (articles or [])[:8])
        return (sym, text.strip() or "No recent headlines.")

    workers = min(max_workers, len(to_fetch))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_worker, sym): sym for sym in to_fetch}
        for fut in as_completed(futures):
            try:
                sym, text = fut.result()
                results[sym] = text
                ck = (sym.upper(), start_str, end_str)
                cache[ck] = text
            except Exception as e:
                sym = futures[fut]
                results[sym] = "No recent headlines."
                _log(f"  Parallel news fetch failed for {sym}: {e}", "yellow")

    # Trim cache
    while len(cache) > _EARNINGS_NEWS_CACHE_MAX:
        cache.pop(next(iter(cache)))

    return results


def _fetch_eps_batch_parallel(
    symbols: list[str],
    strategy_cache: dict,
    max_workers: int = 8,
) -> dict[str, str]:
    """Fetch expected EPS for multiple symbols in parallel with in-memory caching."""
    eps_cache: dict = strategy_cache.setdefault(_EARNINGS_EPS_CACHE_KEY, {})
    results: dict[str, str] = {}
    to_fetch: list[str] = []

    for sym in symbols:
        if sym in eps_cache:
            results[sym] = eps_cache[sym]
        else:
            to_fetch.append(sym)

    if not to_fetch:
        return results

    def _worker(sym: str) -> tuple[str, str]:
        return (sym, _get_expected_eps_str(sym))

    workers = min(max_workers, len(to_fetch))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_worker, sym): sym for sym in to_fetch}
        for fut in as_completed(futures):
            try:
                sym, eps = fut.result()
                results[sym] = eps
                eps_cache[sym] = eps
            except Exception:
                sym = futures[fut]
                results[sym] = ""
                eps_cache[sym] = ""

    return results


def _fetch_beat_miss_batch_parallel(
    symbols: list[str], strategy_cache: dict, as_of_date: _date_type | None = None, max_workers: int = 4
) -> dict[str, str]:
    """Fetch beat/miss summary for multiple symbols in parallel with caching."""
    results: dict[str, str] = {}
    day_key = as_of_date.strftime("%Y-%m-%d") if as_of_date is not None else ""
    cache: dict = strategy_cache.setdefault(_EARNINGS_BEAT_MISS_CACHE_KEY, {})
    to_fetch = [s for s in symbols if ((s, day_key) if as_of_date is not None else s) not in cache]
    for sym in symbols:
        ck = (sym, day_key) if as_of_date is not None else sym
        if ck in cache:
            results[sym] = cache[ck]
    if not to_fetch:
        return results
    with ThreadPoolExecutor(max_workers=min(max_workers, len(to_fetch))) as ex:
        futures = {ex.submit(_get_beat_miss_str, sym, strategy_cache, as_of_date): sym for sym in to_fetch}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result()
            except Exception:
                results[sym] = ""
    return results


def _fetch_volatility_batch_parallel(
    symbols: list[str], strategy_cache: dict, as_of_date: _date_type | None = None, max_workers: int = 4
) -> dict[str, str]:
    """Fetch post-earnings volatility string for multiple symbols in parallel with caching."""
    results: dict[str, str] = {}
    day_key = as_of_date.strftime("%Y-%m-%d") if as_of_date is not None else ""
    cache: dict = strategy_cache.setdefault(_EARNINGS_VOLATILITY_CACHE_KEY, {})
    to_fetch = [s for s in symbols if ((s, day_key) if as_of_date is not None else s) not in cache]
    for sym in symbols:
        ck = (sym, day_key) if as_of_date is not None else sym
        if ck in cache:
            results[sym] = cache[ck]
    if not to_fetch:
        return results
    with ThreadPoolExecutor(max_workers=min(max_workers, len(to_fetch))) as ex:
        futures = {ex.submit(_get_post_earnings_volatility_str, sym, strategy_cache, as_of_date): sym for sym in to_fetch}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result()
            except Exception:
                results[sym] = ""
    return results


def _fetch_research_batch_parallel(symbols: list[str], prices: dict, strategy_cache: dict, max_workers: int = 4) -> dict[str, str]:
    """Fetch company research line for multiple symbols in parallel. Cached per (symbol, price bucket)."""
    cache: dict = strategy_cache.setdefault(_EARNINGS_RESEARCH_CACHE_KEY, {})
    results: dict[str, str] = {}
    to_fetch = []
    for sym in symbols:
        price = prices.get(sym) if prices else None
        key = (sym, round(float(price), 2) if price is not None else None)
        if key in cache:
            results[sym] = cache[key]
        else:
            to_fetch.append((sym, float(price) if price is not None else None))
    if not to_fetch:
        return results
    def _worker(item):
        sym, pr = item
        return (sym, _get_company_research_str(sym, pr))
    with ThreadPoolExecutor(max_workers=min(max_workers, len(to_fetch))) as ex:
        futures = {ex.submit(_worker, item): item[0] for item in to_fetch}
        for fut in as_completed(futures):
            try:
                sym, s = fut.result()
                results[sym] = s
                price = prices.get(sym) if prices else None
                cache[(sym, round(float(price), 2) if price is not None else None)] = s
            except Exception:
                sym = futures[fut]
                results[sym] = ""
    return results


# Max symbols per LLM call to avoid truncated JSON (models often cut off after ~5–10 entries in one go).
_EARNINGS_LLM_BATCH_SIZE = 10


def _parse_sentiment_raw_response(raw: str) -> dict[str, dict]:
    """
    Parse raw LLM response into symbol -> {sentiment, allocation_pct, confidence, avoid}.
    Same logic as used after call_llm_by_provider; used for DB-cached raw output.
    """
    import json as _json
    import re as _re
    out: dict[str, dict] = {}
    if not raw or not raw.strip():
        return out
    raw = raw.strip()
    json_str = None
    code_block = _re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", raw, _re.IGNORECASE)
    if code_block:
        json_str = code_block.group(1).strip()
    if not json_str:
        first_brace = _re.search(r"\{[\s\S]*\}", raw)
        if first_brace:
            json_str = first_brace.group(0)
    if not json_str:
        idx = raw.rfind("{")
        if idx >= 0:
            rest = raw[idx:]
            last_brace = rest.rfind("}")
            json_str = rest[: last_brace + 1] if last_brace >= 0 else rest
    if not json_str:
        return out
    try:
        data = _json.loads(json_str)
    except _json.JSONDecodeError:
        data = None
        repaired = None
        all_complete = list(_re.finditer(
            r"\"[A-Z0-9.]+\"\s*:\s*\{[^{}]*\"sentiment\"\s*:\s*\"(?:POSITIVE|NEGATIVE|UNCERTAIN)\"[^{}]*\"allocation_pct\"\s*:\s*\d+(?:\.\d+)?[^{}]*\}",
            json_str
        ))
        if all_complete:
            last_complete = all_complete[-1]
            repaired = json_str[: last_complete.end()] + "}"
        if not repaired:
            idx = json_str.rfind("}, ")
            if idx >= 0:
                repaired = json_str[: idx + 1] + "}"
        if repaired:
            try:
                data = _json.loads(repaired)
            except _json.JSONDecodeError:
                pass
    if not data:
        return out
    for sym, val in (data or {}).items():
        if not isinstance(val, dict):
            continue
        s = (sym or "").strip().upper()
        sent = (val.get("sentiment") or "").strip().upper()
        alloc = val.get("allocation_pct")
        try:
            alloc_f = max(0.0, min(100.0, float(alloc))) if alloc is not None else 0.0
        except (TypeError, ValueError):
            alloc_f = 0.0
        conf = val.get("confidence")
        try:
            conf_f = max(0.0, min(1.0, float(conf))) if conf is not None else None
        except (TypeError, ValueError):
            conf_f = None
        avoid = val.get("avoid")
        avoid_reason = (val.get("avoid_reason") or "").strip() if isinstance(val.get("avoid_reason"), str) else ""
        if "POSITIVE" in sent:
            out[s] = {"sentiment": 1, "allocation_pct": alloc_f, "confidence": conf_f, "avoid": bool(avoid), "avoid_reason": avoid_reason}
        elif "NEGATIVE" in sent:
            out[s] = {"sentiment": -1, "allocation_pct": 0.0, "confidence": conf_f, "avoid": bool(avoid), "avoid_reason": avoid_reason}
        else:
            out[s] = {"sentiment": 0, "allocation_pct": 0.0, "confidence": conf_f, "avoid": bool(avoid), "avoid_reason": avoid_reason}
    return out


def _call_llm_batch_sentiment_and_allocation(
    provider: str,
    api_key: str,
    model: str,
    symbols_with_news: list[tuple],
    market_context: str = "",
    provider_config: dict[str, Any] | None = None,
) -> dict[str, dict]:
    """
    One or more LLM calls (batched by _EARNINGS_LLM_BATCH_SIZE) for all symbols.
    symbols_with_news: (sym, earn_date, headlines_text, expected_eps_str, beat_miss_str, volatility_str, research_str).
    Returns dict[symbol -> {"sentiment": 1|-1|0, "allocation_pct": 0-100, "confidence": 0-1, "avoid": bool, "avoid_reason": str}].
    """
    if not api_key or not symbols_with_news:
        return {}
    import re as _re
    from llm_utils import call_llm_with_prompt_cache

    p = (provider or "gemini").strip().lower()
    batch_size = max(1, _EARNINGS_LLM_BATCH_SIZE)
    chunks = [
        symbols_with_news[i : i + batch_size]
        for i in range(0, min(len(symbols_with_news), 50), batch_size)
    ]
    out: dict[str, dict] = {}
    raw_response: str = ""
    db_conn = _get_db_conn() if _db_available and r else None
    _log(f"Calling LLM for sentiment + allocation: {len(symbols_with_news)} symbols in {len(chunks)} batch(es) (provider={provider}, model={model})", "cyan")

    for chunk_idx, chunk in enumerate(chunks):
        lines = []
        for item in chunk:
            sym = item[0]
            earn_date = item[1]
            headlines_text = item[2]
            eps_str = (item[3] if len(item) > 3 else "") or ""
            beat_miss_str = (item[4] if len(item) > 4 else "") or ""
            volatility_str = (item[5] if len(item) > 5 else "") or ""
            research_str = (item[6] if len(item) > 6 else "") or ""
            line = f"- {sym} (earnings ~{earn_date})"
            if eps_str:
                line += f", {eps_str}"
            if beat_miss_str:
                line += f". {beat_miss_str}"
            if volatility_str:
                line += f". {volatility_str}"
            if research_str:
                line += f". {research_str}"
            line += f": {headlines_text[:300]}"
            lines.append(line)
        block = "\n".join(lines)
        prompt = f"""You are an equity analyst. DATA_POLICY=STRICT_AS_OF_V1 (point-in-time only, no future data). Below are stocks with upcoming earnings, optional expected EPS, historical beat/miss, earnings volatility, company research, market regime context, and headline snippets (news capped at strategy timestamp). For each stock, decide:
1) SENTIMENT: POSITIVE (likely beat/strong), NEGATIVE (likely miss/weak), or UNCERTAIN. Use expected EPS, history, and headlines.
2) ALLOCATION_PCT: For POSITIVE only, give a percentage (0-100) of a reserved portfolio to allocate to buying before earnings. All POSITIVE allocations in this list should sum to 100. Use 0 for NEGATIVE/UNCERTAIN.
3) CONFIDENCE: Return confidence from 0.0 to 1.0 for your sentiment call.
4) AVOID (optional): If you recommend skipping this name even if news is mixed (e.g. high volatility, serial misser, weak fundamentals), set "avoid": true and "avoid_reason": "short reason".
When avoid is true: do not buy; treat like NEGATIVE (sell if held).

Market context:
{market_context or "N/A"}

Stocks and context:
{block}

Reply with ONLY a JSON object, one key per ticker (e.g. "AAPL", "MSFT"). Each value is an object with "sentiment" (string: POSITIVE/NEGATIVE/UNCERTAIN), "allocation_pct" (number 0-100), "confidence" (number 0.0-1.0), and optionally "avoid" (boolean), "avoid_reason" (string). No other text. If you reason first, end your reply with the JSON object only.
Example: {{"AAPL": {{"sentiment": "POSITIVE", "allocation_pct": 60, "confidence": 0.74}}, "MSFT": {{"sentiment": "NEGATIVE", "allocation_pct": 0, "confidence": 0.68, "avoid": true, "avoid_reason": "high earnings volatility"}}}}"""

        try:
            raw, from_cache = call_llm_with_prompt_cache(
                provider,
                api_key,
                model,
                prompt,
                max_output_tokens=0,
                provider_config=provider_config,
                db_conn=db_conn,
                db_name=DB_NAME,
                prompt_cache_table="EarningsLLMPromptCache",
            )
            raw_response = raw or ""
            if from_cache and raw:
                parsed = _parse_sentiment_raw_response(raw)
                if parsed:
                    out.update(parsed)
                    _log("Batch %d: %d symbols from prompt-hash cache" % (chunk_idx + 1, len(parsed)), "cyan")
                continue
            if not raw or not raw.strip():
                _log("LLM batch %d returned empty; skipping." % (chunk_idx + 1), "yellow")
                continue
            parsed = _parse_sentiment_raw_response(raw)
            if parsed:
                out.update(parsed)
            else:
                _log("Batch %d: no JSON found; skipping." % (chunk_idx + 1), "yellow")
        except Exception as e:
            _log("LLM batch %d error: %s" % (chunk_idx + 1, e), "yellow")
            if raw_response:
                _log("Response preview: %s" % (raw_response[:300].replace("\n", " "),), "yellow")
            continue

    # Normalize positive allocations to sum to 100 across all batches
    positive_alloc = [(s, out[s]["allocation_pct"]) for s, v in out.items() if v.get("sentiment") == 1]
    total_alloc = sum(a for _, a in positive_alloc)
    if total_alloc > 0 and positive_alloc:
        scale = 100.0 / total_alloc
        for s, a in positive_alloc:
            out[s]["allocation_pct"] = round(a * scale, 2)
    n_pos = sum(1 for v in out.values() if v.get("sentiment") == 1)
    n_neg = sum(1 for v in out.values() if v.get("sentiment") == -1)
    n_unc = len(out) - n_pos - n_neg
    _log(f"LLM result: {len(out)} symbols — {n_pos} positive, {n_neg} negative, {n_unc} uncertain", "green")
    return out


def _build_earnings_universe(
    symbols_list: list[str],
    current_time,
    lookahead_days: int,
    use_stocktwits: bool,
    strategy_cache: dict | None = None,
    max_earnings_stocks: int = 50,
    allow_next_earnings_fallback: bool = True,
) -> list[tuple[str, str]]:
    """Returns list of (symbol, earnings_date_yyyy_mm_dd), ranked by importance (StockTwits), capped at max_earnings_stocks.
    Builds from calendar API first, then watchlist, then yfinance fallback if empty. Sorts by importance desc and takes top max.
    Caches the universe per date window to avoid redundant API calls across bars."""
    if isinstance(current_time, datetime):
        today = current_time.date()
    else:
        try:
            today = datetime.fromisoformat(str(current_time)[:10]).date()
        except Exception:
            today = datetime.utcnow().date()
    start = datetime(today.year, today.month, today.day)
    end = start + timedelta(days=lookahead_days)
    date_from = start.strftime("%Y-%m-%d")
    date_to = end.strftime("%Y-%m-%d")

    # Cache key for this exact date window — reuse if same window was already built
    _UNIVERSE_CACHE_KEY = "_earnings_universe_cache"
    if strategy_cache is not None:
        cached_universe = strategy_cache.get(_UNIVERSE_CACHE_KEY)
        if isinstance(cached_universe, dict) and cached_universe.get("date_from") == date_from and cached_universe.get("date_to") == date_to:
            _log(f"Earnings universe cache hit: {len(cached_universe['data'])} symbols for {date_from}–{date_to}", "cyan")
            return list(cached_universe["data"])

    _log(f"Building earnings universe: window {date_from}–{date_to}, max={max_earnings_stocks}, use_stocktwits={use_stocktwits}", "white")

    # Collect as list of dicts with symbol, date, importance (0 for non-StockTwits)
    result_entries: list[dict] = []
    seen: set[str] = set()

    # 1) Calendar API first: all stocks with earnings in range (StockTwits returns importance 0-5)
    if use_stocktwits:
        st_entries = _fetch_stocktwits_earnings(date_from, date_to)
        for ent in st_entries:
            if isinstance(ent, dict):
                sym = (ent.get("symbol") or ent.get("ticker") or "").strip().upper()
                d = ent.get("date") or ent.get("earnings_date") or ent.get("report_date")
                if not sym or not d:
                    continue
                if sym in _SKIP_EARNINGS_LOOKUP:
                    continue
                try:
                    ds = d[:10] if isinstance(d, str) else getattr(d, "strftime", lambda x: "")( "%Y-%m-%d")
                    if start.strftime("%Y-%m-%d") <= ds <= date_to and sym not in seen:
                        seen.add(sym)
                        imp = int(ent.get("importance", 0)) if isinstance(ent.get("importance"), (int, float)) else 0
                        result_entries.append({"symbol": sym, "date": ds, "importance": max(0, min(5, imp))})
                except Exception:
                    pass
            elif isinstance(ent, (list, tuple)) and len(ent) >= 2:
                s = str(ent[0]).upper()
                if s not in _SKIP_EARNINGS_LOOKUP and s not in seen:
                    seen.add(s)
                    result_entries.append({"symbol": s, "date": str(ent[1])[:10], "importance": 0})
        if result_entries:
            _log(f"From calendar: {len(result_entries)} symbols in window", "cyan")

    # 2) Supplement with watchlist via yfinance earnings history (supports backtests)
    watchlist_added = 0
    w_start = start.date()
    w_end = end.date()
    for sym in symbols_list or []:
        s = (sym or "").strip().upper()
        if not s or s in _SKIP_EARNINGS_LOOKUP or s in seen:
            continue
        try:
            # Try historical earnings_dates first (works for backtests), then optional nextEarningsDate fallback.
            dates_in_window = _earnings_dates_from_yfinance(s, w_start, w_end, strategy_cache)
            if dates_in_window:
                for ed_date in dates_in_window:
                    seen.add(s)
                    result_entries.append({"symbol": s, "date": ed_date.strftime("%Y-%m-%d"), "importance": 0})
                    watchlist_added += 1
                    break  # one entry per symbol
            elif allow_next_earnings_fallback:
                ed = _earnings_date_from_yfinance(s)
                if ed is None:
                    continue
                ed_date = ed.date() if hasattr(ed, "date") else ed
                if w_start <= ed_date <= w_end:
                    seen.add(s)
                    result_entries.append({"symbol": s, "date": ed_date.strftime("%Y-%m-%d"), "importance": 0})
                    watchlist_added += 1
        except Exception:
            pass
    if watchlist_added:
        _log(f"Watchlist: added {watchlist_added} symbols with earnings in window", "cyan")

    # 3) Fallback: when calendar API and watchlist yield nothing, use yfinance history for a curated list
    if not result_entries:
        cache_key = "_earnings_fallback_" + date_from
        cached: list[tuple[str, str]] = []
        if strategy_cache is not None and isinstance(strategy_cache.get(cache_key), list):
            cached = strategy_cache[cache_key]
        if cached:
            result_entries = [{"symbol": s, "date": d, "importance": 0} for s, d in cached if start.strftime("%Y-%m-%d") <= d <= date_to]
        else:
            # Use parallel yfinance lookups for faster fallback (low concurrency to avoid 401 rate limits)
            fallback_syms = [s for s in _FALLBACK_EARNINGS_TICKERS if s not in seen][:_MAX_FALLBACK_QUERIES]
            def _yf_worker(s):
                dates_in_window = _earnings_dates_from_yfinance(s, w_start, w_end, strategy_cache)
                if dates_in_window:
                    return (s, dates_in_window[0].strftime("%Y-%m-%d"))
                return None
            with ThreadPoolExecutor(max_workers=3) as ex:
                for result in ex.map(_yf_worker, fallback_syms):
                    if result is not None:
                        s, d = result
                        if s not in seen:
                            seen.add(s)
                            result_entries.append({"symbol": s, "date": d, "importance": 0})
            if result_entries and strategy_cache is not None:
                strategy_cache[cache_key] = [(e["symbol"], e["date"]) for e in result_entries]
            if result_entries:
                _log("Earnings universe built from yfinance fallback (%d symbols)" % len(result_entries), "cyan")

    # Rank by importance (desc), then take top max_earnings_stocks
    total_before_cap = len(result_entries)
    result_entries.sort(key=lambda e: (-int(e.get("importance", 0)), e.get("date", ""), e.get("symbol", "")))
    max_n = max(1, int(max_earnings_stocks))
    capped = result_entries[:max_n]
    if total_before_cap > max_n:
        _log(f"Ranked by importance: keeping top {len(capped)} of {total_before_cap} (cap={max_n})", "cyan")

    final = [(e["symbol"], e["date"]) for e in capped]

    # Cache this universe so subsequent bars with the same window skip all API calls
    if strategy_cache is not None:
        strategy_cache[_UNIVERSE_CACHE_KEY] = {"date_from": date_from, "date_to": date_to, "data": final}

    return final


class Earnings:
    """Run-once strategy: earnings calendar + sentiment → future trades and scores."""

    def run(
        self,
        symbol,
        price,
        current_time,
        config: dict,
        conditions: dict,
        data=None,
        portfolio_emulator=None,
        strategy_cache=None,
        time_increment=None,
    ):
        """Per-symbol entry; delegate to run_once for single symbol."""
        prices = {symbol: price} if price is not None else {}
        raw = self.run_once(
            [symbol], prices, current_time, config, conditions,
            data=data, portfolio_emulator=portfolio_emulator,
            strategy_cache=strategy_cache, time_increment=time_increment,
        )
        if not raw or symbol not in raw:
            return (0, None, None, "No clear signal")
        val = raw[symbol]
        if isinstance(val, dict):
            return val.get("score", val.get("signal", 0))
        return int(val) if val is not None else 0

    def run_once(
        self,
        symbols: list,
        prices: dict,
        current_time,
        config: dict,
        conditions: dict,
        data=None,
        portfolio_emulator=None,
        strategy_cache=None,
        time_increment=None,
        *,
        mode=None,
    ) -> dict[str, Any]:
        """
        Run once per pass. Build earnings universe (yfinance + optional StockTwits),
        fetch news and LLM sentiment, schedule future trades, return scores for symbols in list.
        """
        # The live scheduler passes mode to all run_once strategies. Earnings has
        # no monitor/idle work, so skip expensive calendar/news/LLM work unless
        # this is a FULL cycle or a legacy/backtest call with mode=None.
        scheduler_mode = str(mode or "").strip().upper()
        if scheduler_mode in {"IDLE", "MONITOR"}:
            return {}

        from future_trades_util import schedule_trade, expire_old_trades

        symbols_list = list(symbols or [])
        config = config or {}
        conditions = conditions or {}
        lookahead_days = int(config.get("lookahead_days") or 14)
        max_earnings_stocks = int(config.get("max_earnings_stocks") or 50)
        sell_days_before = int(config.get("sell_days_before") or 1)
        buy_days_before = int(config.get("buy_days_before") or 2)
        news_days = int(config.get("news_days") or 7)
        sell_days_after_earnings = int(config.get("sell_days_after_earnings") or 1)
        max_allocation_pct_per_name = max(0.0, min(100.0, _to_float(config.get("max_allocation_pct_per_name"), 30.0)))
        min_confidence_to_buy = max(0.0, min(1.0, _to_float(config.get("min_confidence_to_buy"), 0.5)))
        max_post_earnings_volatility_pct = max(0.0, _to_float(config.get("max_post_earnings_volatility_pct"), 0.0))
        allow_symbol_date_cache_fallback = _to_bool(config.get("allow_symbol_date_cache_fallback", False), False)
        use_stocktwits = True  # Always use StockTwits calendar — yfinance alone is too unreliable for backtests
        llm_provider = _normalize_llm_provider(config.get("llm_provider") or "gemini")
        model_name = (config.get("model_name") or "").strip() or _default_model_for_provider(llm_provider)
        llm_key = (
            (config.get("azure_openai_api_key") or "").strip()
            or (config.get("llm_api_key") or "").strip()
            or _default_api_key_for_provider(llm_provider)
        )
        llm_provider_config = _resolve_provider_config(config, llm_provider)
        alpaca_key = (config.get("alpaca_key") or "").strip() or os.environ.get("KEY", "")
        alpaca_secret = (config.get("alpaca_secret") or "").strip() or os.environ.get("SECRET", "")

        # LLM is required for sentiment; no fallback to fundamentals-only
        if not llm_key:
            _log("Earnings strategy requires LLM (llm_api_key in config or provider-specific env key). Skipping sentiment and scheduled trades.", "red")
            return {sym: 0 for sym in symbols_list}

        if strategy_cache is not None:
            expire_old_trades(strategy_cache, current_time)

        # Resolve point-in-time boundaries.
        current_dt = _to_utc_naive_datetime(current_time) or datetime.utcnow()
        today = current_dt.date()
        today_str = today.strftime("%Y-%m-%d")
        is_backtest = data is not None
        # Daily Yahoo-style features are only safe up to the previous business day in backtests.
        feature_as_of_date = _strict_daily_cutoff_date(current_dt) if is_backtest else today
        # Disable non-point-in-time data fallbacks in backtests.
        allow_watchlist_next_earnings_fallback = not is_backtest
        if is_backtest:
            allow_symbol_date_cache_fallback = False
            _log(
                "Backtest strict mode: disabling live-only EPS/fundamentals snapshots and symbol+date cache fallback.",
                "cyan",
            )
        _log(
            f"Earnings run_once started - as_of={current_dt.isoformat()}Z, today={today_str}, "
            f"feature_as_of={feature_as_of_date.strftime('%Y-%m-%d')}, lookahead={lookahead_days}d, "
            f"max_stocks={max_earnings_stocks}, capital_pct={config.get('capital_pct', 0.1)}",
            "white",
        )
        # Build list of (symbol, earnings_date), ranked by importance and capped at max_earnings_stocks
        # Uses universe cache so subsequent bars with same window skip StockTwits/yfinance API calls
        earnings_universe = _build_earnings_universe(
            symbols_list, current_time, lookahead_days, use_stocktwits,
            strategy_cache=strategy_cache, max_earnings_stocks=max_earnings_stocks,
            allow_next_earnings_fallback=allow_watchlist_next_earnings_fallback,
        )
        _log(f"Earnings universe: {len(earnings_universe)} symbols (top {max_earnings_stocks} by importance) with earnings in next {lookahead_days} days", "cyan")

        if not earnings_universe:
            _log("No earnings symbols in window; skipping news and scheduling.", "yellow")
            return {sym: 0 for sym in symbols_list}

        # Dedup: track (symbol, earnings_date) already scheduled to avoid duplicates across bars
        if strategy_cache is None:
            strategy_cache = {}
        _SCHEDULED_KEY = "_earnings_already_scheduled"
        already_scheduled: set = strategy_cache.setdefault(_SCHEDULED_KEY, set())

        # Filter universe to only symbols not yet scheduled — skip news/LLM for already-handled symbols
        new_universe = [(sym, ed) for sym, ed in earnings_universe if (sym, ed) not in already_scheduled]
        if len(new_universe) < len(earnings_universe):
            _log(f"Skipping {len(earnings_universe) - len(new_universe)} already-scheduled symbols (dedup)", "cyan")
        if not new_universe:
            _log("All symbols already scheduled; no new work.", "cyan")
            return {sym: 0 for sym in symbols_list}

        # ── Gather news + EPS in parallel ──────────────────────────────────
        if not alpaca_key or not alpaca_secret:
            _log("Alpaca API keys not set; news will be empty (use config.alpaca_key/alpaca_secret or KEY/SECRET env).", "yellow")
        _log(
            f"Fetching Alpaca news for {len(new_universe)} symbols (last {news_days}d up to as_of {current_dt.isoformat()}Z, strict no-lookahead)...",
            "cyan",
        )

        # Strict no-lookahead: cap at exact strategy timestamp.
        news_end_capped = current_dt
        news_start = news_end_capped - timedelta(days=news_days)

        # Parallel news fetch (up to 8 concurrent HTTP requests)
        headlines_map = _fetch_news_batch_parallel(
            new_universe, news_start, news_end_capped,
            alpaca_key, alpaca_secret, strategy_cache, max_workers=8,
        )

        # Parallel EPS fetch (low concurrency — yfinance rate-limits aggressively)
        sym_list_for_eps = [sym for sym, _ in new_universe]
        if is_backtest:
            # EPS estimates and fundamentals snapshots are not point-in-time and can leak future revisions.
            eps_map = {sym: "" for sym in sym_list_for_eps}
            research_map = {sym: "" for sym in sym_list_for_eps}
        else:
            eps_map = _fetch_eps_batch_parallel(sym_list_for_eps, strategy_cache, max_workers=2)
            research_map = _fetch_research_batch_parallel(sym_list_for_eps, prices or {}, strategy_cache, max_workers=4)

        beat_miss_map = _fetch_beat_miss_batch_parallel(
            sym_list_for_eps, strategy_cache, as_of_date=feature_as_of_date, max_workers=4
        )
        volatility_map = _fetch_volatility_batch_parallel(
            sym_list_for_eps, strategy_cache, as_of_date=feature_as_of_date, max_workers=4
        )
        market_context = _get_market_context_str(feature_as_of_date, strategy_cache)

        # Assemble symbols_with_news: (sym, earn_date, headlines, eps_str, beat_miss_str, volatility_str, research_str)
        symbols_with_news: list[tuple] = []
        for sym, earn_date_str in new_universe:
            headlines_text = headlines_map.get(sym, "No recent headlines.")
            eps_str = eps_map.get(sym, "")
            beat_miss_str = beat_miss_map.get(sym, "")
            volatility_str = volatility_map.get(sym, "")
            research_str = research_map.get(sym, "")
            symbols_with_news.append((sym, earn_date_str, headlines_text, eps_str, beat_miss_str, volatility_str, research_str))

        with_headlines = sum(1 for x in symbols_with_news if (x[2] or "").strip() and x[2] != "No recent headlines.")
        _log(f"News gathered: {len(symbols_with_news)} symbols ({with_headlines} with headlines)", "cyan")

        # ── LLM sentiment: RethinkDB persistent cache → in-memory cache → LLM call ──
        # 1) Build cache keys for all symbols
        item_cache_keys: list[tuple[tuple[str, str, str, str], str]] = []  # (item, db_cache_key)
        for item in symbols_with_news:
            sym, earn_date_str = item[0], item[1]
            headlines_text = item[2]
            eps_str = (item[3] if len(item) > 3 else "") or ""
            beat_miss_str = (item[4] if len(item) > 4 else "") or ""
            volatility_str = (item[5] if len(item) > 5 else "") or ""
            research_str = (item[6] if len(item) > 6 else "") or ""
            headlines_hash = hashlib.md5((headlines_text or "").encode("utf-8")).hexdigest()[:16]
            beat_miss_hash = hashlib.md5((beat_miss_str or "").encode("utf-8")).hexdigest()[:16]
            volatility_hash = hashlib.md5((volatility_str or "").encode("utf-8")).hexdigest()[:16]
            research_hash = hashlib.md5((research_str or "").encode("utf-8")).hexdigest()[:16]
            market_ctx_hash = hashlib.md5((market_context or "").encode("utf-8")).hexdigest()[:16]
            db_key = _db_cache_key(
                llm_provider, model_name, sym.upper(), earn_date_str, headlines_hash, eps_str,
                beat_miss_hash=beat_miss_hash, volatility_hash=volatility_hash,
                research_hash=research_hash, market_ctx_hash=market_ctx_hash,
            )
            item_cache_keys.append((item, db_key))

        # 2) Check in-memory cache first
        llm_cache = strategy_cache.get(_EARNINGS_LLM_CACHE_KEY)
        if llm_cache is None:
            strategy_cache[_EARNINGS_LLM_CACHE_KEY] = OrderedDict()
            llm_cache = strategy_cache[_EARNINGS_LLM_CACHE_KEY]

        llm_result: dict[str, dict] = {}
        mem_misses: list[tuple[tuple[str, str, str, str], str]] = []  # items not in memory cache

        for item, db_key in item_cache_keys:
            sym = item[0]
            if db_key in llm_cache:
                llm_result[sym] = dict(llm_cache[db_key])
            else:
                mem_misses.append((item, db_key))

        # 3) Check RethinkDB persistent cache for memory-cache misses
        db_conn = _get_db_conn()
        db_misses: list[tuple[tuple[str, str, str, str], str]] = []  # items not in DB either

        if mem_misses and db_conn is not None:
            miss_keys = [db_key for _, db_key in mem_misses]
            db_hits = _db_get_cached_decisions(db_conn, miss_keys)
            db_hit_count = 0
            for item, db_key in mem_misses:
                sym = item[0]
                if db_key in db_hits:
                    llm_result[sym] = db_hits[db_key]
                    llm_cache[db_key] = db_hits[db_key]  # promote to memory cache
                    db_hit_count += 1
                else:
                    db_misses.append((item, db_key))
            if db_hit_count:
                _log(f"LLM cache: {len(llm_result) - len(db_misses)} from cache ({db_hit_count} from RethinkDB), {len(db_misses)} to fetch from LLM", "cyan")
        else:
            db_misses = mem_misses

        # 3b) Fallback: for exact-key misses, reuse any cached decision for same (symbol, earn_date)
        if db_misses and db_conn is not None and allow_symbol_date_cache_fallback:
            sym_earn_list = [(item[0], item[1]) for item, _ in db_misses]
            fallback_map = _db_get_cached_decisions_by_symbol_earn(db_conn, llm_provider, model_name, sym_earn_list)
            if fallback_map:
                new_db_misses = []
                fallback_count = 0
                for (item, db_key) in db_misses:
                    key = (item[0], item[1])
                    if key in fallback_map:
                        sym = item[0]
                        llm_result[sym] = dict(fallback_map[key])
                        llm_cache[db_key] = dict(fallback_map[key])
                        fallback_count += 1
                    else:
                        new_db_misses.append((item, db_key))
                db_misses = new_db_misses
                if fallback_count:
                    _log(f"LLM cache: {len(llm_result)} from cache ({fallback_count} from symbol+date fallback), {len(db_misses)} to fetch from LLM", "cyan")

        # 4) Call LLM only for true misses
        if db_misses:
            if llm_result and not (mem_misses and db_conn is not None):
                _log("LLM cache: %d symbols from cache, %d to fetch from LLM" % (len(llm_result), len(db_misses)), "cyan")
            miss_items = [item for item, _ in db_misses]
            fresh = _call_llm_batch_sentiment_and_allocation(
                llm_provider, llm_key, model_name, miss_items, market_context=market_context
                , provider_config=llm_provider_config
                )

            # Save fresh results to both in-memory and RethinkDB
            db_entries_to_save: list[dict] = []
            for item, db_key in db_misses:
                sym = item[0]
                earn_date_str, headlines_text = item[1], item[2]
                eps_str = (item[3] if len(item) > 3 else "") or ""
                if sym in fresh:
                    decision = {
                        "sentiment": fresh[sym].get("sentiment", 0),
                        "allocation_pct": fresh[sym].get("allocation_pct", 0.0),
                        "confidence": fresh[sym].get("confidence", None),
                        "avoid": bool(fresh[sym].get("avoid", False)),
                        "avoid_reason": (fresh[sym].get("avoid_reason") or "").strip(),
                    }
                    llm_result[sym] = fresh[sym]
                    llm_cache[db_key] = dict(decision)
                    # Prepare RethinkDB entry
                    db_entries_to_save.append({
                        "id": db_key,
                        "symbol": sym.upper(),
                        "provider": llm_provider,
                        "model": model_name,
                        "earn_date": earn_date_str,
                        "headlines_hash": hashlib.md5((headlines_text or "").encode("utf-8")).hexdigest()[:16],
                        "sentiment": decision["sentiment"],
                        "allocation_pct": decision["allocation_pct"],
                        "confidence": decision["confidence"],
                        "avoid": decision["avoid"],
                        "avoid_reason": decision["avoid_reason"],
                        "cached_at": datetime.utcnow().isoformat() + "Z",
                    })
            # Batch-save to RethinkDB
            if db_entries_to_save and db_conn is not None:
                _db_save_cached_decisions(db_conn, db_entries_to_save)

            # Trim in-memory cache
            while len(llm_cache) > _EARNINGS_LLM_CACHE_MAX:
                llm_cache.pop(next(iter(llm_cache)))
        else:
            n_pos = sum(1 for v in llm_result.values() if v.get("sentiment") == 1)
            n_neg = sum(1 for v in llm_result.values() if v.get("sentiment") == -1)
            n_unc = len(llm_result) - n_pos - n_neg
            _log("LLM result (all cached): %d symbols — %d positive, %d negative, %d uncertain (no LLM calls)" % (len(llm_result), n_pos, n_neg, n_unc), "green")

        # Cap each positive allocation at max_allocation_pct_per_name then renormalize to sum to 100
        positive_alloc = [(s, llm_result[s].get("allocation_pct", 0.0)) for s, v in llm_result.items() if v.get("sentiment") == 1]
        if positive_alloc:
            capped = [(s, min(a, max_allocation_pct_per_name)) for s, a in positive_alloc]
            total_capped = sum(a for _, a in capped)
            if total_capped > 0:
                scale = 100.0 / total_capped
                for s, a in capped:
                    llm_result[s]["allocation_pct"] = round(a * scale, 2)

        # Initialize earnings positions tracker (for sell fraction tracking)
        earnings_positions: dict = strategy_cache.setdefault(_EARNINGS_POSITIONS_KEY, {})

        # Schedule future trades using LLM sentiment and allocation_pct for buys
        _log("Scheduling future trades (sell before negative, buy before positive)...", "cyan")
        scheduled_sells = 0
        scheduled_buys = 0
        for sym, earn_date_str in new_universe:
            try:
                sched_key = (sym, earn_date_str)
                if sched_key in already_scheduled:
                    continue
                earn_dt = datetime.strptime(earn_date_str, "%Y-%m-%d")
                try:
                    sell_target_dt = (earn_dt - timedelta(days=sell_days_before)).date()
                    buy_target_dt = (earn_dt - timedelta(days=buy_days_before)).date()
                except Exception:
                    sell_target_dt = earn_dt.date()
                    buy_target_dt = earn_dt.date()

                # Roll target dates to business days (Mon-Fri) so trades don't land on weekends
                sell_target_dt = _prev_business_day(sell_target_dt)
                buy_target_dt = _prev_business_day(buy_target_dt)

                # Clamp to today if target is in the past
                if sell_target_dt < today:
                    sell_target_dt = today
                if buy_target_dt < today:
                    buy_target_dt = today

                sell_target = sell_target_dt.strftime("%Y-%m-%d")
                buy_target = buy_target_dt.strftime("%Y-%m-%d")

                info = llm_result.get(sym, {"sentiment": 0, "allocation_pct": 0.0, "confidence": None, "avoid": False, "avoid_reason": ""})
                sentiment = info.get("sentiment", 0)
                allocation_pct = info.get("allocation_pct", 0.0)
                confidence = info.get("confidence")
                try:
                    confidence_f = float(confidence) if confidence is not None else None
                except (TypeError, ValueError):
                    confidence_f = None
                if confidence_f is None:
                    confidence_f = max(0.0, min(1.0, float(allocation_pct) / 100.0)) if sentiment >= 1 else 0.0
                avoid = info.get("avoid", False)

                if sentiment <= -1 or avoid:
                    schedule_trade(
                        strategy_cache, symbol=sym, target_date=sell_target, signal=-1,
                        reason=f"Earnings {earn_date_str} inferred negative (LLM); sell before" + (f" — {info.get('avoid_reason', '')}" if avoid and info.get("avoid_reason") else ""),
                        source_date=today_str, confidence=0.65,
                    )
                    scheduled_sells += 1
                    already_scheduled.add(sched_key)
                    _log(f"  Scheduled sell {sym} by {sell_target} (earnings {earn_date_str}, negative" + (" / avoid" if avoid else "") + ")", "yellow")
                elif sentiment >= 1 and not avoid:
                    vol_str = volatility_map.get(sym, "") if isinstance(volatility_map, dict) else ""
                    vol_pct = _parse_volatility_pct(vol_str)
                    if max_post_earnings_volatility_pct > 0 and vol_pct is not None and vol_pct > max_post_earnings_volatility_pct:
                        _log(
                            f"  Skipping buy {sym} (volatility {vol_pct:.1f}% > max_post_earnings_volatility_pct {max_post_earnings_volatility_pct:.1f}%)",
                            "yellow",
                        )
                        already_scheduled.add(sched_key)
                        continue
                    if confidence_f < min_confidence_to_buy:
                        _log(f"  Skipping buy {sym} (confidence {confidence_f:.2f} < min_confidence_to_buy {min_confidence_to_buy:.2f})", "yellow")
                        already_scheduled.add(sched_key)
                        continue
                    schedule_trade(
                        strategy_cache, symbol=sym, target_date=buy_target, signal=1,
                        reason=f"Earnings {earn_date_str} inferred positive; buy before" + (f" (alloc {allocation_pct:.0f}%)" if allocation_pct > 0 else ""),
                        source_date=today_str, confidence=0.6,
                        allocation_pct=allocation_pct if allocation_pct > 0 else None,
                    )
                    scheduled_buys += 1
                    already_scheduled.add(sched_key)
                    _log(f"  Scheduled buy {sym} by {buy_target} (earnings {earn_date_str}, positive" + (f", {allocation_pct:.0f}% of reserved" if allocation_pct > 0 else "") + ")", "green")
                    # Sell the position sell_days_after_earnings after earnings so we don't hold indefinitely
                    sell_after_dt = _next_business_day((earn_dt + timedelta(days=sell_days_after_earnings)).date())
                    sell_after_earnings = sell_after_dt.strftime("%Y-%m-%d")
                    if sell_after_earnings < today_str:
                        sell_after_earnings = today_str
                    schedule_trade(
                        strategy_cache, symbol=sym, target_date=sell_after_earnings, signal=-1,
                        reason=f"Close day after earnings {earn_date_str}",
                        source_date=today_str, confidence=0.7,
                    )
                    _log(f"  Scheduled sell {sym} by {sell_after_earnings} (day after earnings)", "cyan")
            except Exception as e:
                _log(f"  Error scheduling for {sym}: {e}", "yellow")

        _log(f"Earnings run_once done: {scheduled_sells} sells, {scheduled_buys} buys scheduled; returning scores for {len(symbols_list)} watchlist symbols.", "green")
        # Return scores for symbols in the list. Execution on target date is via broker
        # popping and executing pending trades; we do not vote here to avoid double-execution.
        out: dict[str, Any] = {sym: 0 for sym in symbols_list}
        return out
