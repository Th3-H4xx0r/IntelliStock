"""
Benzinga REST API client with RethinkDB caching.

Data sources:
  1. Analyst Ratings          GET /api/v2/calendar/ratings
  2. Analyst Insights         GET /api/v2/analyst-insights
  3. Insider Transactions     GET /api/v1/sec/insider_transactions/transactions
  4. Government Trades        GET /api/v1/government_trades
  5. Mergers & Acquisitions   GET /api/v2/calendar/ma
  6. IPOs                     GET /api/v2/calendar/ipos
  7. Stock Splits             GET /api/v2/calendar/splits
  8. Earnings Calendar        GET /api/v2/calendar/earnings
  9. Company Actions          GET /api/v2/calendar/dividends
 10. Prediction Markets       GET /api/v2/analyst-insights (bullish/bearish cases)

Authentication: token query parameter
Base URL: https://api.benzinga.com
Cache: RethinkDB table "GraphNexusBenzingaCache", key="{type}_{date_key}[_{ticker_hash}]"
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timedelta

import requests

try:
    from rethinkdb import RethinkDB as _RDB
    _r = _RDB()
    _BZ_DB_NAME = "IntelliStock"
    _BZ_CACHE_TABLE = "GraphNexusBenzingaCache"
    _BZ_DB_AVAILABLE = True
except Exception:
    _r = None
    _BZ_DB_AVAILABLE = False

try:
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="BenzingaClient")
except Exception:
    def _log(msg, color="white"):
        print(f"[BenzingaClient] {msg}")

BZ_BASE = "https://api.benzinga.com"
_BZ_UNAVAILABLE_ENDPOINTS: dict[str, int] = {}
_BZ_TICKER_SCOPED_TYPES = {
    "ratings",
    "insights",
    "insider_trades",
    "gov_trades",
    "splits",
    "earnings",
    "company_actions",
    "prediction_markets",
}

_BZ_DAILY_CALLS: dict[str, int] = {}
_BZ_DAILY_BUDGET = 900
_BZ_PERSISTED_STATE_LOADED = False

# Backtest mode: when True, skip the daily budget check.
# During backtests the entire run happens on the same real day, so the budget
# counter exhausts after the first bulk fetch (~200 calls).  RethinkDB cache
# prevents duplicate HTTP calls, so bypassing the counter is safe.
_BZ_BACKTEST_MODE = False
_BZ_401_ALERTED = False  # one-shot Discord alert per process on Benzinga 401


def set_benzinga_backtest_mode(enabled: bool):
    """Enable/disable backtest mode (skips daily budget check)."""
    global _BZ_BACKTEST_MODE
    _BZ_BACKTEST_MODE = enabled

# ---------------------------------------------------------------------------
# RethinkDB cache helpers
# ---------------------------------------------------------------------------

_bz_conn = None
_bz_table_ensured = False


def _get_bz_conn(reuse: bool = True):
    global _bz_conn, _bz_table_ensured
    if not _BZ_DB_AVAILABLE or _r is None:
        return None
    rethink_host = os.environ.get("RETHINKDB_HOST", "localhost")
    rethink_port = int(os.environ.get("RETHINKDB_PORT", "28015"))
    if reuse and _bz_conn is not None:
        try:
            _r.db_list().run(_bz_conn)
            return _bz_conn
        except Exception:
            _bz_conn = None
    try:
        _bz_conn = _r.connect(host=rethink_host, port=rethink_port)
        _ensure_bz_table(_bz_conn)
        return _bz_conn
    except Exception as e:
        _log(f"RethinkDB unavailable for cache: {e}", "yellow")
        return None


def _ensure_bz_table(conn):
    global _bz_table_ensured
    if conn is None or _bz_table_ensured:
        return
    try:
        dbs = list(_r.db_list().run(conn))
        if _BZ_DB_NAME not in dbs:
            _r.db_create(_BZ_DB_NAME).run(conn)
        tables = list(_r.db(_BZ_DB_NAME).table_list().run(conn))
        if _BZ_CACHE_TABLE not in tables:
            _r.db(_BZ_DB_NAME).table_create(_BZ_CACHE_TABLE).run(conn)
        _bz_table_ensured = True
    except Exception as e:
        _log(f"Failed to ensure Benzinga cache table: {e}", "yellow")


def _cache_key(data_type: str, date_key: str, tickers: list[str] | None = None) -> str:
    """Build a unique cache key. Include ticker hash when tickers provided."""
    if tickers:
        h = hashlib.md5(",".join(sorted(tickers)).encode()).hexdigest()[:8]
        return f"{data_type}_{date_key}_{h}"
    return f"{data_type}_{date_key}"


def _cache_get(conn, key: str, max_age_hours: int = 12) -> list | None:
    """Return cached data if fresh, else None."""
    if conn is None:
        return None
    try:
        doc = _r.db(_BZ_DB_NAME).table(_BZ_CACHE_TABLE).get(key).run(conn)
        if not doc:
            return None
        cached_at = doc.get("cached_at")
        if cached_at:
            try:
                age = (datetime.utcnow() - datetime.fromisoformat(cached_at)).total_seconds() / 3600
                if age > max_age_hours:
                    return None
            except Exception:
                pass
        return doc.get("data")
    except Exception:
        return None


def _cache_set(conn, key: str, data: list) -> None:
    """Store data in cache with current timestamp."""
    if conn is None:
        return
    try:
        _r.db(_BZ_DB_NAME).table(_BZ_CACHE_TABLE).insert(
            {"id": key, "data": data, "cached_at": datetime.utcnow().isoformat()},
            conflict="replace",
        ).run(conn)
    except Exception as e:
        _log(f"Cache write failed for {key}: {e}", "yellow")


# ---------------------------------------------------------------------------
# Daily budget tracking + persisted unavailable endpoints
# ---------------------------------------------------------------------------


def _load_persisted_state():
    """Load unavailable endpoints and daily call count from RethinkDB on first use."""
    global _BZ_PERSISTED_STATE_LOADED
    if _BZ_PERSISTED_STATE_LOADED:
        return
    _BZ_PERSISTED_STATE_LOADED = True
    conn = _get_bz_conn()
    if not conn:
        return
    # Unavailable endpoints — persisted 7 days
    cached = _cache_get(conn, "bz_unavailable_endpoints", max_age_hours=24 * 7)
    if isinstance(cached, list):
        for entry in cached:
            if isinstance(entry, dict) and "key" in entry and "status" in entry:
                _BZ_UNAVAILABLE_ENDPOINTS[entry["key"]] = entry["status"]
        if _BZ_UNAVAILABLE_ENDPOINTS:
            _log(
                f"Loaded {len(_BZ_UNAVAILABLE_ENDPOINTS)} known-unavailable Benzinga endpoint(s) from cache",
                "cyan",
            )
    # Daily call counter
    today = datetime.utcnow().strftime("%Y-%m-%d")
    counter = _cache_get(conn, f"bz_daily_calls_{today}", max_age_hours=24)
    if isinstance(counter, list) and counter and isinstance(counter[0], dict):
        _BZ_DAILY_CALLS[today] = int(counter[0].get("count", 0))
        if _BZ_DAILY_CALLS[today]:
            _log(
                f"Benzinga daily budget: {_BZ_DAILY_BUDGET - _BZ_DAILY_CALLS[today]} calls remaining today",
                "cyan",
            )


def _persist_unavailable():
    """Save unavailable endpoints to RethinkDB cache."""
    conn = _get_bz_conn()
    if conn and _BZ_UNAVAILABLE_ENDPOINTS:
        data = [{"key": k, "status": v} for k, v in _BZ_UNAVAILABLE_ENDPOINTS.items()]
        _cache_set(conn, "bz_unavailable_endpoints", data)


def _track_api_call():
    """Increment daily call counter and persist to RethinkDB."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    _BZ_DAILY_CALLS[today] = _BZ_DAILY_CALLS.get(today, 0) + 1
    count = _BZ_DAILY_CALLS[today]
    conn = _get_bz_conn()
    if conn:
        _cache_set(conn, f"bz_daily_calls_{today}", [{"count": count}])
    remaining = _BZ_DAILY_BUDGET - count
    if 0 <= remaining <= 5:
        _log(
            f"Benzinga API budget: {remaining} calls remaining today ({count}/{_BZ_DAILY_BUDGET})",
            "yellow",
        )


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _bz_get(
    path: str,
    params: dict,
    token: str,
    timeout: int = 30,
    retries: int = 2,
) -> dict | list:
    """Make GET request to Benzinga API with retry logic and budget tracking."""
    _load_persisted_state()
    # Check daily budget
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if not _BZ_BACKTEST_MODE and _BZ_DAILY_CALLS.get(today, 0) >= _BZ_DAILY_BUDGET:
        _log(f"Benzinga daily API budget exhausted ({_BZ_DAILY_BUDGET}/day); skipping {path}", "yellow")
        return {}
    url = f"{BZ_BASE}{path}"
    params = dict(params)
    params["token"] = token
    headers = {"Accept": "application/json"}
    token_fingerprint = hashlib.sha1(str(token or "").encode("utf-8")).hexdigest()[:10]
    unavailable_key = f"{path}|{token_fingerprint}"
    if unavailable_key in _BZ_UNAVAILABLE_ENDPOINTS:
        return {}
    last_exc = None
    for attempt in range(retries + 1):
        # Re-check budget before each attempt (retries count against quota too)
        if not _BZ_BACKTEST_MODE and _BZ_DAILY_CALLS.get(datetime.utcnow().strftime("%Y-%m-%d"), 0) >= _BZ_DAILY_BUDGET:
            _log(f"Benzinga daily API budget exhausted during request; aborting", "yellow")
            return {}
        try:
            _track_api_call()
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 401:
                _log("Benzinga API: 401 Unauthorized — check BENZINGA_API_KEY", "red")
                # Live-readiness MED (2026-04-29): page operator on key revocation.
                # Old code returned {} silently which turned the strategy into a
                # price-only bot with no alert. Discord alert fires once per
                # process to avoid spam.
                global _BZ_401_ALERTED
                if not globals().get("_BZ_401_ALERTED"):
                    try:
                        from live_alerts import alert_strategy_error  # type: ignore
                        alert_strategy_error(
                            instance_id=os.environ.get("INSTANCE_ID", "<unknown>"),
                            tag="benzinga_401",
                            message="Benzinga API 401 — key revoked or rotated. Strategy will run without Benzinga signals until fixed.",
                        )
                    except Exception as _alert_e:
                        # Surface alert failures so future signature drift doesn't silently swallow.
                        _log(f"Benzinga 401 alert dispatch failed: {type(_alert_e).__name__}: {_alert_e}", "yellow")
                    _BZ_401_ALERTED = True
                return {}
            if resp.status_code in {403, 404}:
                _BZ_UNAVAILABLE_ENDPOINTS[unavailable_key] = resp.status_code
                _persist_unavailable()
                _log(
                    f"Benzinga API {resp.status_code} for {path}; suppressing further requests",
                    "yellow",
                )
                return {}
            if resp.status_code == 429:
                wait = 2 ** attempt
                _log(f"Benzinga API rate limited; waiting {wait}s", "yellow")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            last_exc = "timeout"
            if attempt < retries:
                time.sleep(1)
        except requests.exceptions.RequestException as e:
            last_exc = str(e)
            if attempt < retries:
                time.sleep(1)
        except Exception as e:
            last_exc = str(e)
            break
    _log(f"Benzinga API request failed ({path}): {last_exc}", "yellow")
    return {}


def _extract_result_items(data: dict | list, result_key: str) -> list:
    """Handle Benzinga endpoints that may return a list or wrap records under varying keys."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []

    candidate_keys = (
        result_key,
        result_key.replace("-", "_"),
        result_key.replace("_", "-"),
        "data",
        "results",
        "items",
    )
    for key in candidate_keys:
        items = data.get(key)
        if isinstance(items, list):
            return items
        if isinstance(items, dict):
            nested = items.get("data") or items.get("results") or items.get("items")
            if isinstance(nested, list):
                return nested
    return []


def _has_meaningful_value(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _sanitize_benzinga_records(
    data_type: str,
    records: list[dict] | None,
    tickers: list[str] | None = None,
) -> tuple[list[dict], int]:
    """Drop invalid/noisy Benzinga rows and dedupe exact duplicates."""
    if not isinstance(records, list) or not records:
        return [], 0

    requested_tickers = {
        str(t or "").strip().upper()
        for t in (tickers or [])
        if str(t or "").strip()
    }
    cleaned: list[dict] = []
    dropped = 0
    seen: set[str] = set()

    for item in records:
        if not isinstance(item, dict):
            dropped += 1
            continue

        rec = dict(item)
        ticker = str(rec.get("ticker") or "").strip().upper()
        if ticker:
            rec["ticker"] = ticker
        acquirer_ticker = str(rec.get("acquirer_ticker") or "").strip().upper()
        if acquirer_ticker:
            rec["acquirer_ticker"] = acquirer_ticker

        if data_type in _BZ_TICKER_SCOPED_TYPES:
            if not ticker:
                dropped += 1
                continue
            if requested_tickers and ticker not in requested_tickers:
                dropped += 1
                continue
        elif data_type == "ma":
            if requested_tickers and not ({ticker, acquirer_ticker} & requested_tickers):
                dropped += 1
                continue

        if data_type == "company_actions":
            if not any(
                _has_meaningful_value(rec.get(key))
                for key in ("amount", "ex_date", "payable_date", "name")
            ):
                dropped += 1
                continue

        dedupe_key = json.dumps(rec, sort_keys=True, default=str)
        if dedupe_key in seen:
            dropped += 1
            continue
        seen.add(dedupe_key)
        cleaned.append(rec)

    return cleaned, dropped


def _paginate(
    path: str,
    base_params: dict,
    token: str,
    result_key: str,
    max_results: int = 500,
) -> list:
    """Paginate through a Benzinga calendar endpoint."""
    all_items = []
    page = 0
    page_size = min(100, max_results)
    while True:
        params = dict(base_params)
        params["page"] = page
        params["pagesize"] = page_size
        data = _bz_get(path, params, token)
        items = _extract_result_items(data, result_key)
        if not items:
            break
        all_items.extend(items)
        if len(items) < page_size or len(all_items) >= max_results:
            break
        page += 1
    return all_items[:max_results]


# ---------------------------------------------------------------------------
# Individual data fetchers (no cache — cache is handled by the cached wrappers)
# ---------------------------------------------------------------------------

def _fetch_ratings_raw(
    token: str,
    tickers: list[str],
    date_from: str,
    date_to: str,
    max_results: int = 200,
) -> list[dict]:
    """Analyst ratings: upgrades/downgrades/initiations."""
    params = {
        "parameters[date_from]": date_from,
        "parameters[date_to]": date_to,
    }
    if tickers:
        params["parameters[tickers]"] = ",".join(tickers[:50])
    items = _paginate("/api/v2/calendar/ratings", params, token, "ratings", max_results)
    out = []
    for it in items:
        out.append({
            "ticker": (it.get("ticker") or "").upper(),
            "date": it.get("date", ""),
            "analyst": it.get("analyst_name") or it.get("analyst", ""),
            "action": it.get("action_company", ""),
            "rating": it.get("rating_current", ""),
            "pt": it.get("pt_current"),
            "pt_prior": it.get("pt_prior"),
        })
    return out


def _fetch_insights_raw(
    token: str,
    tickers: list[str],
    date_from: str,
    date_to: str,
    max_results: int = 100,
) -> list[dict]:
    """Analyst insights — detailed research commentary."""
    params = {
        "parameters[date_from]": date_from,
        "parameters[date_to]": date_to,
    }
    if tickers:
        params["parameters[tickers]"] = ",".join(tickers[:50])
    items = _paginate("/api/v2/analyst-insights", params, token, "analyst-insights", max_results)
    out = []
    for it in items:
        insight_text = (it.get("insight") or "")[:200]
        out.append({
            "ticker": (it.get("ticker") or "").upper(),
            "date": it.get("date", ""),
            "firm": it.get("firm", ""),
            "rating": it.get("rating", ""),
            "action": it.get("rating_action", ""),
            "insight": insight_text,
            "pt": it.get("price_target"),
        })
    return out


def _fetch_insider_transactions_raw(
    token: str,
    tickers: list[str],
    date_from: str,
    date_to: str,
    max_results: int = 200,
) -> list[dict]:
    """Insider transaction filings (SEC Form 4)."""
    out = []
    for ticker in tickers[:20]:  # limit to avoid too many requests
        params = {
            "date_from": date_from,
            "date_to": date_to,
            "search_keys_type": "symbol",
            "search_keys": ticker,
            "display": "flat",
        }
        data = _bz_get("/api/v1/sec/insider_transactions/transactions", params, token)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("insider-transactions") or data.get("insider_transactions") or []
        else:
            items = []
        if isinstance(items, dict):
            items = items.get("transactions") or []
        for it in items[:max_results]:
            code = it.get("transaction_code", "")
            # P=purchase, S=sale
            direction = "buy" if code == "P" else ("sell" if code == "S" else code)
            out.append({
                "ticker": (it.get("company_symbol") or ticker).upper(),
                "date": it.get("filing_date") or it.get("date_transaction", ""),
                "insider": it.get("owner_name") or it.get("insider_name", ""),
                "title": it.get("owner_title") or it.get("insider_title", ""),
                "direction": direction,
                "shares": it.get("shares", 0),
                "price": it.get("price_per_share"),
            })
        if len(out) >= max_results:
            break
    return out[:max_results]


def _fetch_gov_trades_raw(
    token: str,
    tickers: list[str],
    date_from: str,
    date_to: str,
    max_results: int = 200,
) -> list[dict]:
    """US Congressional stock trades (House & Senate disclosures)."""
    out = []
    for ticker in tickers[:20]:
        params = {
            "date_from": date_from,
            "date_to": date_to,
            "search_keys_type": "ticker",
            "search_keys": ticker,
        }
        data = _bz_get("/api/v1/government_trades", params, token)
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("government_trades") or data.get("trades") or []
            if isinstance(items, dict):
                items = items.get("transactions") or items.get("data") or []
        else:
            items = []
        for it in items:
            out.append({
                "ticker": (it.get("ticker") or ticker).upper(),
                "date": it.get("transaction_date") or it.get("date", ""),
                "politician": it.get("politician_name") or it.get("representative", ""),
                "chamber": it.get("chamber", ""),
                "direction": (it.get("transaction_type") or "").lower(),
                "value_usd": it.get("value_usd") or it.get("amount"),
            })
        if len(out) >= max_results:
            break
    return out[:max_results]


def _fetch_ma_raw(
    token: str,
    tickers: list[str],
    date_from: str,
    date_to: str,
    max_results: int = 100,
) -> list[dict]:
    """Mergers & Acquisitions calendar."""
    params = {
        "parameters[date_from]": date_from,
        "parameters[date_to]": date_to,
    }
    if tickers:
        params["parameters[tickers]"] = ",".join(tickers[:50])
    items = _paginate("/api/v2/calendar/ma", params, token, "ma", max_results)
    out = []
    for it in items:
        out.append({
            "ticker": (it.get("ticker") or "").upper(),
            "date": it.get("date", ""),
            "name": it.get("name", ""),
            "deal_type": it.get("deal_type", ""),
            "deal_status": it.get("deal_status", ""),
            "deal_size": it.get("deal_size"),
            "acquirer": it.get("acquirer_name", ""),
            "acquirer_ticker": (it.get("acquirer_ticker") or "").upper(),
            "expected_close": it.get("expected_close_date", ""),
        })
    return out


def _fetch_ipos_raw(
    token: str,
    date_from: str,
    date_to: str,
    max_results: int = 100,
) -> list[dict]:
    """IPO calendar (no ticker filter — all upcoming IPOs in date range)."""
    params = {
        "parameters[date_from]": date_from,
        "parameters[date_to]": date_to,
    }
    items = _paginate("/api/v2/calendar/ipos", params, token, "ipos", max_results)
    out = []
    for it in items:
        out.append({
            "ticker": (it.get("ticker") or "").upper(),
            "date": it.get("date") or it.get("pricing_date", ""),
            "name": it.get("name", ""),
            "exchange": it.get("exchange", ""),
            "price_min": it.get("price_min"),
            "price_max": it.get("price_max"),
            "offering_shares": it.get("offering_shares"),
            "offering_value": it.get("offering_value"),
            "status": it.get("deal_status", ""),
        })
    return out


def _fetch_splits_raw(
    token: str,
    tickers: list[str],
    date_from: str,
    date_to: str,
    max_results: int = 100,
) -> list[dict]:
    """Stock splits calendar."""
    params = {
        "parameters[date_from]": date_from,
        "parameters[date_to]": date_to,
    }
    if tickers:
        params["parameters[tickers]"] = ",".join(tickers[:50])
    items = _paginate("/api/v2/calendar/splits", params, token, "splits", max_results)
    out = []
    for it in items:
        out.append({
            "ticker": (it.get("ticker") or "").upper(),
            "date": it.get("date") or it.get("execution_date", ""),
            "ratio": f"{it.get('split_from', '?')}-for-{it.get('split_to', '?')}",
            "type": it.get("split_type") or it.get("adjustment_type", ""),
        })
    return out


def _fetch_earnings_calendar_raw(
    token: str,
    tickers: list[str],
    date_from: str,
    date_to: str,
    max_results: int = 200,
) -> list[dict]:
    """Earnings calendar (upcoming and recent earnings reports)."""
    params = {
        "parameters[date_from]": date_from,
        "parameters[date_to]": date_to,
    }
    if tickers:
        params["parameters[tickers]"] = ",".join(tickers[:50])
    items = _paginate("/api/v2/calendar/earnings", params, token, "earnings", max_results)
    out = []
    for it in items:
        out.append({
            "ticker": (it.get("ticker") or "").upper(),
            "date": it.get("date", ""),
            "time": it.get("time", ""),  # BMO/AMC
            "name": it.get("name", ""),
            "eps_est": it.get("eps_estimate") or it.get("eps_estimated"),
            "eps_actual": it.get("eps_actual"),
            "eps_surprise_pct": it.get("eps_surprise_percent"),
            "rev_est": it.get("revenue_estimate") or it.get("revenue_estimated"),
            "rev_actual": it.get("revenue_actual"),
            "period": it.get("fiscal_period", ""),
            "importance": it.get("importance"),
        })
    return out


def _fetch_company_actions_raw(
    token: str,
    tickers: list[str],
    date_from: str,
    date_to: str,
    max_results: int = 100,
) -> list[dict]:
    """Company actions (dividends, special events)."""
    params = {
        "parameters[date_from]": date_from,
        "parameters[date_to]": date_to,
    }
    if tickers:
        params["parameters[tickers]"] = ",".join(tickers[:50])
    # Try dividends only — economics fallback produces untickered data that fails validation
    items = _paginate("/api/v2/calendar/dividends", params, token, "dividends", max_results)
    out = []
    for it in items:
        out.append({
            "ticker": (it.get("ticker") or "").upper(),
            "date": it.get("date", ""),
            "name": it.get("name") or it.get("security_name") or "",
            "event": it.get("event_type") or it.get("action_type") or it.get("dividend_type") or "dividend",
            "amount": it.get("dividend_amount") or it.get("dividend") or it.get("amount"),
            "ex_date": it.get("ex_dividend_date") or it.get("ex_date", ""),
            "payable_date": it.get("payable_date") or it.get("pay_date", ""),
        })
    return out


def _fetch_prediction_markets_raw(
    token: str,
    tickers: list[str],
    date_from: str = "",
    date_to: str = "",
    max_results: int = 100,
) -> list[dict]:
    """
    Bulls vs Bears / analyst prediction markets.
    Returns bullish/bearish investment thesis for tickers.
    """
    params = {}
    if tickers:
        params["parameters[tickers]"] = ",".join(tickers[:50])
    if date_from:
        params["parameters[date_from]"] = date_from
    if date_to:
        params["parameters[date_to]"] = date_to
    # Try the analyst-insights endpoint filtered for bullish/bearish cases
    items = _paginate("/api/v2/analyst-insights", params, token, "analyst-insights", max_results)
    if not items:
        # Fallback: try bulls-bears endpoint
        data = _bz_get("/api/v1/bulls-bears", params, token)
        items = _extract_result_items(data, "bulls_bears")
    out = []
    for it in items:
        outlook = (it.get("rating") or it.get("outlook") or "").lower()
        if "bull" in outlook:
            stance = "bullish"
        elif "bear" in outlook:
            stance = "bearish"
        else:
            stance = outlook or "neutral"
        out.append({
            "ticker": (it.get("ticker") or "").upper(),
            "date": it.get("date", ""),
            "firm": it.get("firm", ""),
            "stance": stance,
            "thesis": (it.get("insight") or it.get("thesis") or "")[:200],
        })
    return out[:max_results]


# ---------------------------------------------------------------------------
# Data-leakage prevention
# ---------------------------------------------------------------------------

# Data types that only return historical data (no look-ahead risk):
#   ratings, insights, insider_trades, gov_trades, prediction_markets
# Data types that can return future-dated records (publicly announced events):
#   earnings, ipos, splits, ma, company_actions
# NOTE: For the forward-looking types, we strip any "actual result" fields
# for records whose date is after date_key, since those outcomes haven't
# happened yet in backtesting context.

_FORWARD_LOOKING_TYPES = {"earnings", "ipos", "splits", "ma", "company_actions"}


def _strip_future_actuals(data_type: str, records: list[dict], date_key: str) -> list[dict]:
    """
    Prevent look-ahead bias by clearing actual-result fields for records
    whose date is in the future relative to date_key.
    Only earnings currently have actual result fields that could leak.
    IPO confirmed prices are also stripped for future IPOs.
    """
    if data_type not in _FORWARD_LOOKING_TYPES or not records:
        return records
    out = []
    for rec in records:
        rec_date = rec.get("date", "")
        if rec_date and rec_date > date_key:
            rec = dict(rec)
            if data_type == "earnings":
                # Strip actuals — they're not known yet in backtesting
                rec["eps_actual"] = None
                rec["rev_actual"] = None
                rec["eps_surprise_pct"] = None
            elif data_type == "ipos":
                # Final IPO price is set day before pricing — not known yet
                rec.pop("price_confirmed", None)
                rec.pop("price_public_offering", None)
            elif data_type == "ma":
                # Deal status updates (completed/terminated) are forward-looking
                rec["deal_status"] = "announced"
                rec.pop("expected_close", None)
            elif data_type == "splits":
                # Execution-related fields not yet known
                rec.pop("execution_date", None)
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Bulk fetch API (for backtest pre-fetching)
# ---------------------------------------------------------------------------

_CHUNK_DAYS = 30  # split long ranges into ~30-day windows


def _bulk_cache_key(data_type: str, date_from: str, date_to: str, tickers: list[str] | None) -> str:
    """Build a cache key for a bulk (range-based) fetch."""
    rng = f"{date_from}_{date_to}"
    if tickers:
        h = hashlib.md5(",".join(sorted(tickers)).encode()).hexdigest()[:8]
        return f"bulk_{data_type}_{rng}_{h}"
    return f"bulk_{data_type}_{rng}"


def _fetch_raw_for_type(
    data_type: str,
    token: str,
    tickers: list[str] | None,
    date_from: str,
    date_to: str,
    max_results: int = 500,
) -> list[dict]:
    """Route to the correct raw fetcher for a single date range."""
    t = tickers or []
    if data_type == "ratings":
        return _fetch_ratings_raw(token, t, date_from, date_to, max_results)
    if data_type == "insights":
        return _fetch_insights_raw(token, t, date_from, date_to, max_results)
    if data_type == "insider_trades":
        return _fetch_insider_transactions_raw(token, t, date_from, date_to, max_results)
    if data_type == "gov_trades":
        return _fetch_gov_trades_raw(token, t, date_from, date_to, max_results)
    if data_type == "ma":
        return _fetch_ma_raw(token, t, date_from, date_to, max_results)
    if data_type == "ipos":
        return _fetch_ipos_raw(token, date_from, date_to, max_results)
    if data_type == "splits":
        return _fetch_splits_raw(token, t, date_from, date_to, max_results)
    if data_type == "earnings":
        return _fetch_earnings_calendar_raw(token, t, date_from, date_to, max_results)
    if data_type == "company_actions":
        return _fetch_company_actions_raw(token, t, date_from, date_to, max_results)
    if data_type == "prediction_markets":
        return _fetch_prediction_markets_raw(token, t, date_from, date_to, max_results)
    return []


def _fetch_chunk_with_split(
    data_type: str,
    token: str,
    tickers: list[str] | None,
    from_dt: datetime,
    to_dt: datetime,
    max_results: int,
    conn,
    effective_hours: int,
    depth: int = 0,
) -> list[dict]:
    """
    Fetch a single date-range chunk. If the result hits exactly max_results
    (likely truncated), auto-split the chunk in half and re-fetch each half.
    Max split depth of 3 (~4-day minimum chunks).
    """
    c_from = from_dt.strftime("%Y-%m-%d")
    c_to = to_dt.strftime("%Y-%m-%d")
    chunk_key = _bulk_cache_key(data_type, c_from, c_to, tickers)

    # Check chunk cache first
    chunk_cached = _cache_get(conn, chunk_key, max_age_hours=effective_hours)
    if chunk_cached is not None:
        return chunk_cached

    chunk_data = _fetch_raw_for_type(data_type, token, tickers, c_from, c_to, max_results)
    if not chunk_data:
        _cache_set(conn, chunk_key, [])
        return []

    # Truncation detection: if we got exactly max_results, data was likely cut off
    range_days = (to_dt - from_dt).days
    if len(chunk_data) >= max_results and depth < 3 and range_days >= 2:
        mid = from_dt + timedelta(days=range_days // 2)
        _log(
            f"Benzinga {data_type}: chunk {c_from}..{c_to} hit {max_results} cap — "
            f"splitting into sub-chunks (depth {depth + 1})",
            "yellow",
        )
        left = _fetch_chunk_with_split(
            data_type, token, tickers, from_dt, mid, max_results, conn, effective_hours, depth + 1
        )
        right = _fetch_chunk_with_split(
            data_type, token, tickers, mid + timedelta(days=1), to_dt, max_results, conn, effective_hours, depth + 1
        )
        combined = left + right
        _cache_set(conn, chunk_key, combined)
        return combined

    if len(chunk_data) >= max_results:
        _log(
            f"Benzinga {data_type}: chunk {c_from}..{c_to} hit {max_results} cap at max split depth — "
            f"some data may be missing",
            "yellow",
        )

    _cache_set(conn, chunk_key, chunk_data)
    return chunk_data


def fetch_benzinga_bulk(
    data_type: str,
    token: str,
    date_from: str,
    date_to: str,
    tickers: list[str] | None = None,
    max_results: int = 2000,
    cache_hours: int = 8760,
) -> list[dict]:
    """
    Fetch Benzinga data for a full date range in bulk.
    Designed for backtest pre-fetching.

    For long ranges (>35 days), automatically splits into ~30-day chunks so
    dense sources (e.g. broad ratings) don't hit the per-query result cap.
    Each chunk is cached individually in RethinkDB; the combined result is
    also cached under the full-range key.

    If any chunk returns exactly max_results records (likely truncated), the
    chunk is auto-split in half and each half is re-fetched to capture all data.

    Does NOT apply _strip_future_actuals — caller must strip per-day when slicing.
    """
    if not token:
        return []

    conn = _get_bz_conn()
    full_key = _bulk_cache_key(data_type, date_from, date_to, tickers)

    # Historical ranges get permanent cache
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    effective_hours = cache_hours if date_to >= today_str else 8760

    # Check combined cache first
    cached = _cache_get(conn, full_key, max_age_hours=effective_hours)
    if cached is not None:
        sanitized, dropped = _sanitize_benzinga_records(data_type, cached, tickers)
        if dropped:
            _cache_set(conn, full_key, sanitized)
        _log(f"Benzinga bulk {data_type}: {len(sanitized)} records from cache ({date_from} to {date_to})", "cyan")
        return sanitized

    # Determine if chunking is needed
    try:
        from_dt = datetime.strptime(date_from, "%Y-%m-%d")
        to_dt = datetime.strptime(date_to, "%Y-%m-%d")
    except ValueError:
        _log(f"Benzinga bulk: invalid date range {date_from}..{date_to}", "yellow")
        return []

    range_days = (to_dt - from_dt).days

    try:
        if range_days <= 35:
            # Short range — single fetch with truncation detection
            data = _fetch_chunk_with_split(
                data_type, token, tickers, from_dt, to_dt, max_results, conn, effective_hours
            )
        else:
            # Long range — fetch in ~30-day chunks so we don't hit the result cap
            data = []
            chunk_start = from_dt
            n_chunks = 0
            while chunk_start <= to_dt:
                chunk_end = min(chunk_start + timedelta(days=_CHUNK_DAYS), to_dt)

                chunk_data = _fetch_chunk_with_split(
                    data_type, token, tickers, chunk_start, chunk_end,
                    max_results, conn, effective_hours,
                )
                data.extend(chunk_data)

                n_chunks += 1
                chunk_start = chunk_end + timedelta(days=1)

            _log(f"Benzinga bulk {data_type}: fetched in {n_chunks} chunk(s) over {range_days} days", "cyan")

        # Sanitize combined result — do NOT strip future actuals (caller handles per-day)
        data, dropped = _sanitize_benzinga_records(data_type, data, tickers)
        if dropped:
            _log(f"Benzinga bulk {data_type}: dropped {dropped} invalid/duplicate record(s)", "cyan")
        if data:
            _log(f"Benzinga bulk {data_type}: {len(data)} total records ({date_from} to {date_to})", "green")
        _cache_set(conn, full_key, data)
        return data
    except Exception as e:
        _log(f"Error bulk-fetching {data_type}: {e}", "yellow")
        return []


# ---------------------------------------------------------------------------
# Cached public API (used by graph_nexus_analysis.py)
# ---------------------------------------------------------------------------

def fetch_benzinga_data(
    data_type: str,
    token: str,
    date_key: str,
    tickers: list[str] | None = None,
    lookback_days: int = 7,
    lookahead_days: int = 7,
    cache_hours: int = 12,
) -> list[dict]:
    """
    Fetch Benzinga data for `data_type` with RethinkDB caching.

    data_type: one of "ratings", "insights", "insider_trades", "gov_trades",
               "ma", "ipos", "splits", "earnings", "company_actions",
               "prediction_markets"

    lookback_days: how many days back to fetch (all types)
    lookahead_days: how many days forward to fetch (calendar types only).
        Set to 0 for strict backtesting. Default 7 covers announced upcoming events.
        Actual result fields for future dates are always stripped (no leakage).

    Returns list of normalized dicts. Returns [] on error or if token missing.

    Data leakage notes:
    - Non-calendar types (ratings, insights, insider_trades, gov_trades,
      prediction_markets) are always backward-looking only. No leakage.
    - Calendar types (earnings, ipos, splits, ma, company_actions) may include
      future-dated records (publicly announced events). Actual result fields
      (e.g. eps_actual for upcoming earnings) are stripped automatically.
    """
    if not token:
        return []

    try:
        base = datetime.strptime(date_key, "%Y-%m-%d")
    except ValueError:
        base = datetime.utcnow()

    date_from = (base - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    # Non-calendar types: look back only; calendar types: configurable look-ahead
    if data_type in _FORWARD_LOOKING_TYPES and lookahead_days > 0:
        date_to = (base + timedelta(days=lookahead_days)).strftime("%Y-%m-%d")
    else:
        date_to = date_key  # inclusive of today only

    # Normalize tickers before building cache key so key is consistent
    tickers = list(tickers or [])

    conn = _get_bz_conn()
    key = _cache_key(data_type, date_key, tickers if tickers else None)

    # Historical dates get permanent cache (data will never change).
    # Only "today" uses the shorter TTL since new data can still arrive.
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    effective_cache_hours = cache_hours if date_key >= today_str else 8760  # 1 year for historical

    # Check cache
    cached = _cache_get(conn, key, max_age_hours=effective_cache_hours)
    if cached is not None:
        sanitized_cached, dropped = _sanitize_benzinga_records(data_type, cached, tickers)
        if dropped:
            _log(
                f"Benzinga {data_type}: dropped {dropped} invalid/duplicate cached record(s) before reuse",
                "cyan",
            )
            _cache_set(conn, key, sanitized_cached)
        return sanitized_cached

    # Fetch from API
    try:
        if data_type == "ratings":
            data = _fetch_ratings_raw(token, tickers, date_from, date_to)
        elif data_type == "insights":
            data = _fetch_insights_raw(token, tickers, date_from, date_to)
        elif data_type == "insider_trades":
            data = _fetch_insider_transactions_raw(token, tickers, date_from, date_to)
        elif data_type == "gov_trades":
            data = _fetch_gov_trades_raw(token, tickers, date_from, date_to)
        elif data_type == "ma":
            data = _fetch_ma_raw(token, tickers, date_from, date_to)
        elif data_type == "ipos":
            data = _fetch_ipos_raw(token, date_from, date_to)
        elif data_type == "splits":
            data = _fetch_splits_raw(token, tickers, date_from, date_to)
        elif data_type == "earnings":
            data = _fetch_earnings_calendar_raw(token, tickers, date_from, date_to)
        elif data_type == "company_actions":
            data = _fetch_company_actions_raw(token, tickers, date_from, date_to)
        elif data_type == "prediction_markets":
            data = _fetch_prediction_markets_raw(token, tickers, date_from, date_to)
        else:
            _log(f"Unknown data_type: {data_type}", "yellow")
            return []

        # Strip future actual results to prevent data leakage in backtesting
        data = _strip_future_actuals(data_type, data, date_key)
        data, dropped = _sanitize_benzinga_records(data_type, data, tickers)
        if dropped:
            _log(
                f"Benzinga {data_type}: dropped {dropped} invalid/duplicate record(s) before cache",
                "cyan",
            )

        if data:
            _log(f"Benzinga {data_type}: {len(data)} records fetched", "green")
        _cache_set(conn, key, data)
        return data
    except Exception as e:
        _log(f"Error fetching {data_type}: {e}", "yellow")
        return []
