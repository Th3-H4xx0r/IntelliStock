# INTELLISTOCK_SCHEMA: {"strategy": "MlNews", "weight": 0.55, "execution_position": 10, "decision_phase": "pre", "execution_scope": "run_once", "conditions": {}, "config": {"enable_llm": true, "LLM_IMPULSE_TRIGGER": 0.55, "SENTIMENT_AVG_LONG": 0.35, "SENTIMENT_AVG_SHORT": -0.35, "MIN_LLM_RELEVANCE": 3, "MIN_LLM_STRENGTH": 3, "MIN_ARTICLES_FOR_SIGNAL": 1, "MAX_HOLD_DAYS_DEFAULT": 7, "llm_provider": "gemini", "model_name": "gemini-2.0-flash-exp", "llm_api_key": "<provider_api_key>", "openai_base_url": "<optional>", "azure_openai_api_key": "<optional>", "azure_openai_endpoint": "<optional>", "azure_openai_api_version": "2024-10-21", "alpaca_key": "<optional>", "alpaca_secret": "<optional>", "max_articles_per_symbol": 10, "news_lookback_hours": 24}}
# INTELLISTOCK_DESCRIPTION: ML news pipeline (run_once). Ingests Alpaca news, scores with FinBERT, selectively classifies high-impact items with LLM, aggregates daily features per symbol, and produces buy/hold/sell votes. Alpha source for news_pipeline_v1.
# DIFFICULTY: 4
"""
MlNews — ML-driven news sentiment strategy (run_once).

Pipeline executed once per trading loop (before per-symbol strategies):
1. INGEST:  Fetch Alpaca news for all universe symbols (headline + summary).
            Store raw articles in RethinkDB NewsRaw table.
2. SCORE:   Run FinBERT on headline+summary for every new article.
            Store pos/neg/neu probabilities in NewsScored table.
            Compute sentiment_impulse = (pos - neg) * max(pos, neg, neu).
3. CLASSIFY: For high-impulse items (|impulse| >= LLM_IMPULSE_TRIGGER),
            call LLM to extract structured event classification.
            Store in NewsLLM table.
4. AGGREGATE: Build per-symbol daily features for prior day (D-1).
            Store in TickerDayFeatures table.
5. VOTE:    Apply decision rules to produce buy/hold/sell per symbol.

Returns: dict[symbol -> {"score": 1|0|-1, "reason": str}]

Config:
  enable_llm: whether to run LLM classification (default true)
  LLM_IMPULSE_TRIGGER: min |sentiment_impulse| to trigger LLM (default 0.55)
  SENTIMENT_SUM_LONG: min sentiment_sum for BUY (default 0.8)
  SENTIMENT_SUM_SHORT: max sentiment_sum for SELL (default -0.8)
  MIN_LLM_RELEVANCE: min LLM relevance_score to consider (default 3)
  MIN_LLM_STRENGTH: min LLM impact_strength to consider (default 3)
  MAX_HOLD_DAYS_DEFAULT: passed to risk_manager on buy (default 5)
  llm_provider: "gemini", "deepseek", "openai", or "azure" (default "gemini")
  model_name: LLM model name
  llm_api_key: API key for LLM (or provider env key)
  openai_base_url / azure_openai_endpoint / azure_openai_api_version: optional provider settings
  alpaca_key/alpaca_secret: Alpaca API credentials (or KEY/SECRET env)
  max_articles_per_symbol: max articles to process per symbol (default 10)
  news_lookback_hours: how far back to fetch news (default 24)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

# ── Logging ─────────────────────────────────────────────────────────────────
try:
    import sys
    _broker_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _broker_dir not in sys.path:
        sys.path.insert(0, _broker_dir)
    from intellistock_logger import intellistock_logger
    def _log(msg, color="white"):
        intellistock_logger.log(msg, color, service="MlNews")
except Exception:
    def _log(msg, color="white"):
        print(f"[MlNews] {msg}")

# ── LLM utils ──────────────────────────────────────────────────────────────
try:
    from llm_utils import call_llm_with_prompt_cache
except ImportError:
    try:
        import sys as _sys
        _bd = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _bd not in _sys.path:
            _sys.path.insert(0, _bd)
        from llm_utils import call_llm_with_prompt_cache
    except ImportError:
        call_llm_with_prompt_cache = None

try:
    from llm_telemetry import llm_call_context
except Exception:
    from contextlib import contextmanager
    @contextmanager
    def llm_call_context(**_kwargs):
        yield

# ── RethinkDB ──────────────────────────────────────────────────────────────
try:
    from rethinkdb import RethinkDB
    r = RethinkDB()
    DB_NAME = 'IntelliStock'
    RETHINKDB_HOST = os.environ.get('RETHINKDB_HOST', 'localhost')
    RETHINKDB_PORT = int(os.environ.get('RETHINKDB_PORT', '28015'))
    _db_available = True
except Exception:
    r = None
    _db_available = False
    _log("RethinkDB not available; DB persistence disabled", "yellow")

# Table names
TBL_NEWS_RAW = 'NewsRaw'
TBL_NEWS_SCORED = 'NewsScored'
TBL_NEWS_LLM = 'NewsLLM'
TBL_TICKER_DAY = 'TickerDayFeatures'

# Alpaca News API
ALPACA_NEWS_BASE = "https://data.alpaca.markets/v1beta1"
ALPACA_RETRY_ATTEMPTS = 3
ALPACA_RETRY_WAITS = [10, 30, 60]

# FinBERT cache key in strategy_cache
_FINBERT_CACHE_KEY = "_ml_news_finbert"
_MLNEWS_CACHE_KEY = "ml_news"

# In-memory cache limits
_MAX_NEWS_CACHE = 500

SECONDS_PER_DAY = 86400


# ═══════════════════════════════════════════════════════════════════════════
# DB helpers
# ═══════════════════════════════════════════════════════════════════════════
_ml_news_db_conn = None
_tables_ensured = False


def _get_db_conn(reuse: bool = True):
    global _ml_news_db_conn
    if not _db_available or r is None:
        return None
    if reuse and _ml_news_db_conn is not None:
        try:
            r.db_list().run(_ml_news_db_conn)
            return _ml_news_db_conn
        except Exception:
            _ml_news_db_conn = None
    try:
        _ml_news_db_conn = r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)
        return _ml_news_db_conn
    except Exception as e:
        _log(f"DB connect error: {e}", "yellow")
        return None


def _ensure_tables(conn):
    global _tables_ensured
    if conn is None or _tables_ensured:
        return
    try:
        dbs = list(r.db_list().run(conn))
        if DB_NAME not in dbs:
            r.db_create(DB_NAME).run(conn)
        existing = list(r.db(DB_NAME).table_list().run(conn))
        for tbl in [TBL_NEWS_RAW, TBL_NEWS_SCORED, TBL_NEWS_LLM, TBL_TICKER_DAY]:
            if tbl not in existing:
                r.db(DB_NAME).table_create(tbl).run(conn)
                _log(f"Created table {tbl}", "cyan")
        _tables_ensured = True
    except Exception as e:
        _log(f"Table ensure error: {e}", "yellow")


# ═══════════════════════════════════════════════════════════════════════════
# Time helpers
# ═══════════════════════════════════════════════════════════════════════════
def _to_datetime(dt) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    try:
        return datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _time_increment_to_seconds(ti) -> int | None:
    if ti is None:
        return None
    s = (ti if isinstance(ti, str) else str(ti)).strip().lower()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    num_str = ""
    for c in s:
        if c in "0123456789.":
            num_str += c
        else:
            break
    try:
        num = float(num_str) if num_str else 1.0
    except ValueError:
        return None
    rest = s[len(num_str):].strip() or "s"
    if rest.startswith("d"):
        return int(num * SECONDS_PER_DAY)
    if rest.startswith("h"):
        return int(num * 3600)
    if rest.startswith("m"):
        return int(num * 60)
    return int(num)


def _normalize_llm_provider(provider: str) -> str:
    name = (provider or "gemini").strip().lower()
    return name if name in ("gemini", "deepseek", "openai", "azure", "nvidia", "claude-cli", "anthropic") else "gemini"


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
    if provider in ("claude-cli", "anthropic"):
        return "claude-sonnet-4-6"
    return "gemini-2.0-flash-exp"


def _default_api_key_for_provider(provider: str) -> str:
    provider = _normalize_llm_provider(provider)
    if provider == "deepseek":
        return os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if provider == "openai":
        return os.environ.get("OPENAI_API_KEY", "").strip()
    if provider == "azure":
        return os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
    if provider == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if provider == "claude-cli":
        # Sentinel so ``if not api_key`` short-circuits don't skip the
        # whole pipeline — claude-cli authenticates via the local binary,
        # not an API key. llm_utils' claude-cli branch ignores the value.
        return "claude-cli-no-api-key"
    return os.environ.get("GEMINI_API_KEY", "").strip()


def _resolve_provider_config(config: dict, provider: str) -> dict:
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
    if provider == "claude-cli":
        cli_path = (config.get("cli_path") or "claude").strip() or "claude"
        extra_args = config.get("extra_args") or ""
        out: dict = {"cli_path": cli_path}
        if extra_args:
            out["extra_args"] = extra_args
        return out
    return {}


# ═══════════════════════════════════════════════════════════════════════════
# 1. INGEST — Alpaca News
# ═══════════════════════════════════════════════════════════════════════════
def _fetch_alpaca_news_batch(symbols: list[str], start_dt: datetime,
                             end_dt: datetime, key: str, secret: str,
                             limit_per_symbol: int = 50) -> list[dict]:
    """Fetch news for multiple symbols from Alpaca. Returns list of article dicts."""
    if not key or not secret:
        _log("Alpaca key/secret missing; set KEY/SECRET env or config", "yellow")
        return []

    import requests

    start_iso = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    url = f"{ALPACA_NEWS_BASE}/news"
    headers = {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "accept": "application/json",
    }

    all_articles = []

    # Fetch in batches of symbols (Alpaca supports comma-separated)
    batch_size = 20  # Alpaca limit for symbols param
    for i in range(0, len(symbols), batch_size):
        sym_batch = symbols[i:i + batch_size]
        params = {
            "symbols": ",".join(sym_batch),
            "start": start_iso,
            "end": end_iso,
            "limit": min(limit_per_symbol * len(sym_batch), 50),
        }

        for attempt in range(ALPACA_RETRY_ATTEMPTS + 1):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                articles = data.get("news") or []
                all_articles.extend(articles)
                break
            except requests.HTTPError as e:
                if (e.response is not None and e.response.status_code == 429
                        and attempt < ALPACA_RETRY_ATTEMPTS):
                    wait = ALPACA_RETRY_WAITS[min(attempt, len(ALPACA_RETRY_WAITS) - 1)]
                    _log(f"Alpaca 429, waiting {wait}s (attempt {attempt + 1})", "yellow")
                    time.sleep(wait)
                else:
                    _log(f"Alpaca fetch error: {e}", "yellow")
                    break
            except Exception as e:
                _log(f"Alpaca fetch error: {e}", "yellow")
                break

    return all_articles


def _store_news_raw(conn, articles: list[dict]) -> list[dict]:
    """Store raw articles in NewsRaw, returning only new (not-yet-stored) articles."""
    if not articles:
        return []

    # Deduplicate by article ID
    seen = set()
    unique = []
    for a in articles:
        aid = str(a.get("id") or hashlib.md5(
            (a.get("headline", "") + a.get("url", "")).encode()
        ).hexdigest())
        if aid not in seen:
            seen.add(aid)
            a["_aid"] = aid
            unique.append(a)

    if conn is None:
        return unique

    # Check which IDs already exist
    try:
        existing_ids = set()
        aids = [a["_aid"] for a in unique]
        cursor = r.db(DB_NAME).table(TBL_NEWS_RAW).get_all(*aids).pluck("id").run(conn)
        for doc in cursor:
            existing_ids.add(doc["id"])
    except Exception:
        existing_ids = set()

    new_articles = [a for a in unique if a["_aid"] not in existing_ids]

    if new_articles:
        docs = []
        now_iso = datetime.now(timezone.utc).isoformat()
        for a in new_articles:
            docs.append({
                "id": a["_aid"],
                "created_at": a.get("created_at", ""),
                "source": a.get("source", ""),
                "headline": a.get("headline", ""),
                "summary": a.get("summary", ""),
                "url": a.get("url", ""),
                "symbols": a.get("symbols", []),
                "ingested_at": now_iso,
            })
        try:
            r.db(DB_NAME).table(TBL_NEWS_RAW).insert(docs, conflict="update").run(conn)
        except Exception as e:
            _log(f"NewsRaw insert error: {e}", "yellow")

    return new_articles


# ═══════════════════════════════════════════════════════════════════════════
# 2. SCORE — FinBERT
# ═══════════════════════════════════════════════════════════════════════════
def _load_finbert(strategy_cache: dict):
    """Load FinBERT model + tokenizer, cached in strategy_cache."""
    cache = strategy_cache.setdefault(_FINBERT_CACHE_KEY, {})

    if cache.get("model") is not None and cache.get("tokenizer") is not None:
        return cache["tokenizer"], cache["model"]

    try:
        from transformers import AutoTokenizer, AutoModelForSequenceClassification
        import torch

        model_name = "ProsusAI/finbert"
        _log("Loading FinBERT model (first run)...", "cyan")
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSequenceClassification.from_pretrained(model_name)
        model.eval()

        cache["tokenizer"] = tokenizer
        cache["model"] = model
        _log("FinBERT loaded successfully", "green")
        return tokenizer, model
    except Exception as e:
        _log(f"FinBERT load failed: {e}", "red")
        cache["model"] = None
        cache["tokenizer"] = None
        return None, None


def _score_finbert_batch(articles: list[dict], strategy_cache: dict) -> list[dict]:
    """
    Score articles with FinBERT. Returns list of score dicts:
    {id, finbert_pos, finbert_neg, finbert_neu, sentiment_score, confidence, sentiment_impulse}
    """
    if not articles:
        return []

    tokenizer, model = _load_finbert(strategy_cache)
    if tokenizer is None or model is None:
        _log("FinBERT unavailable, using neutral scores", "yellow")
        return [
            {
                "id": a["_aid"],
                "finbert_pos": 0.33, "finbert_neg": 0.33, "finbert_neu": 0.34,
                "sentiment_score": 0.0, "confidence": 0.34, "sentiment_impulse": 0.0,
            }
            for a in articles
        ]

    import torch

    results = []
    # Process in mini-batches of 16 for memory efficiency
    batch_size = 16
    for i in range(0, len(articles), batch_size):
        batch = articles[i:i + batch_size]
        texts = []
        for a in batch:
            headline = (a.get("headline") or "").strip()
            summary = (a.get("summary") or "").strip()
            content_excerpt = (a.get("content_excerpt") or a.get("body_excerpt") or "").strip()
            parts = [headline]
            if summary:
                parts.append(summary)
            if content_excerpt:
                parts.append(content_excerpt)
            text = ". ".join(part for part in parts if part)
            texts.append(text[:512])  # FinBERT max tokens

        try:
            inputs = tokenizer(texts, padding=True, truncation=True,
                               max_length=512, return_tensors="pt")
            with torch.no_grad():
                outputs = model(**inputs)
                probs = torch.nn.functional.softmax(outputs.logits, dim=-1)

            # FinBERT labels: positive=0, negative=1, neutral=2
            for j, a in enumerate(batch):
                pos = float(probs[j][0])
                neg = float(probs[j][1])
                neu = float(probs[j][2])
                sentiment_score = pos - neg
                confidence = max(pos, neg, neu)
                sentiment_impulse = sentiment_score * confidence

                results.append({
                    "id": a["_aid"],
                    "finbert_pos": round(pos, 4),
                    "finbert_neg": round(neg, 4),
                    "finbert_neu": round(neu, 4),
                    "sentiment_score": round(sentiment_score, 4),
                    "confidence": round(confidence, 4),
                    "sentiment_impulse": round(sentiment_impulse, 4),
                })
        except Exception as e:
            _log(f"FinBERT batch error: {e}", "red")
            for a in batch:
                results.append({
                    "id": a["_aid"],
                    "finbert_pos": 0.33, "finbert_neg": 0.33, "finbert_neu": 0.34,
                    "sentiment_score": 0.0, "confidence": 0.34, "sentiment_impulse": 0.0,
                })

    return results


def _store_news_scored(conn, scores: list[dict]):
    """Store FinBERT scores in NewsScored table."""
    if conn is None or not scores:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    docs = []
    for s in scores:
        docs.append({
            "id": s["id"],
            "finbert_pos": s["finbert_pos"],
            "finbert_neg": s["finbert_neg"],
            "finbert_neu": s["finbert_neu"],
            "sentiment_score": s["sentiment_score"],
            "confidence": s["confidence"],
            "sentiment_impulse": s["sentiment_impulse"],
            "scored_at": now_iso,
        })
    try:
        r.db(DB_NAME).table(TBL_NEWS_SCORED).insert(docs, conflict="replace").run(conn)
    except Exception as e:
        _log(f"NewsScored insert error: {e}", "yellow")


# ═══════════════════════════════════════════════════════════════════════════
# 3. CLASSIFY — Selective LLM
# ═══════════════════════════════════════════════════════════════════════════
_LLM_CLASSIFY_PROMPT = """You are a financial news analyst. For the following news article, provide a structured JSON classification.

Headline: {headline}
Summary: {summary}
Symbol(s): {symbols}

Reply with ONLY a JSON object (no markdown, no extra text):
{{
  "event_type": "<one of: earnings, guidance, m_and_a, lawsuit, downgrade, upgrade, macro, product, other>",
  "is_forward_looking": <true or false>,
  "impact_direction": "<one of: bullish, bearish, mixed, unclear>",
  "impact_strength": <integer 1 to 5>,
  "expected_horizon_days": <integer 1 to 5>,
  "numerical_surprise": "<one of: beat, miss, inline, none>",
  "relevance_score": <integer 1 to 5>
}}"""


def _classify_llm_batch(high_impulse_articles: list[dict], provider: str,
                         api_key: str, model: str, conn, provider_config: dict[str, Any] | None = None) -> list[dict]:
    """Run LLM classification on high-impulse articles. Returns list of classification dicts."""
    if not high_impulse_articles or call_llm_with_prompt_cache is None:
        return []

    results = []
    for article in high_impulse_articles:
        headline = article.get("headline", "")
        summary = article.get("summary", "")
        symbols = ", ".join(article.get("symbols", []))

        prompt = _LLM_CLASSIFY_PROMPT.format(
            headline=headline, summary=summary, symbols=symbols
        )

        try:
            with llm_call_context(strategy="MLNews", call_site="main"):
                raw, from_cache = call_llm_with_prompt_cache(
                    provider, api_key, model, prompt,
                    max_output_tokens=0, provider_config=provider_config, db_conn=conn,
                    db_name=DB_NAME, prompt_cache_table="MlNewsLLMPromptCache",
                )
            if from_cache:
                _log(f"LLM classify cache hit: {headline[:50]}", "cyan")

            parsed = _parse_llm_classification(raw)
            parsed["id"] = article["_aid"]
            parsed["llm_model"] = model
            parsed["llm_at"] = datetime.now(timezone.utc).isoformat()
            results.append(parsed)
        except Exception as e:
            _log(f"LLM classify error for {headline[:40]}: {e}", "yellow")

    return results


def _parse_llm_classification(raw: str) -> dict:
    """Parse LLM JSON response into classification dict with defaults."""
    defaults = {
        "event_type": "other",
        "is_forward_looking": False,
        "impact_direction": "unclear",
        "impact_strength": 1,
        "expected_horizon_days": 1,
        "numerical_surprise": "none",
        "relevance_score": 1,
    }
    if not raw:
        return defaults

    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return defaults

        # Validate and clamp values
        valid_events = {"earnings", "guidance", "m_and_a", "lawsuit", "downgrade",
                        "upgrade", "macro", "product", "other"}
        valid_directions = {"bullish", "bearish", "mixed", "unclear"}
        valid_surprises = {"beat", "miss", "inline", "none"}

        result = {}
        result["event_type"] = parsed.get("event_type", "other")
        if result["event_type"] not in valid_events:
            result["event_type"] = "other"

        result["is_forward_looking"] = bool(parsed.get("is_forward_looking", False))

        result["impact_direction"] = parsed.get("impact_direction", "unclear")
        if result["impact_direction"] not in valid_directions:
            result["impact_direction"] = "unclear"

        result["impact_strength"] = max(1, min(5, int(parsed.get("impact_strength", 1))))
        result["expected_horizon_days"] = max(1, min(5, int(parsed.get("expected_horizon_days", 1))))

        result["numerical_surprise"] = parsed.get("numerical_surprise", "none")
        if result["numerical_surprise"] not in valid_surprises:
            result["numerical_surprise"] = "none"

        result["relevance_score"] = max(1, min(5, int(parsed.get("relevance_score", 1))))
        return result
    except (json.JSONDecodeError, ValueError, TypeError):
        return defaults


def _store_news_llm(conn, classifications: list[dict]):
    """Store LLM classifications in NewsLLM table."""
    if conn is None or not classifications:
        return
    docs = []
    for c in classifications:
        docs.append({
            "id": c["id"],
            "event_type": c.get("event_type", "other"),
            "is_forward_looking": c.get("is_forward_looking", False),
            "impact_direction": c.get("impact_direction", "unclear"),
            "impact_strength": c.get("impact_strength", 1),
            "expected_horizon_days": c.get("expected_horizon_days", 1),
            "numerical_surprise": c.get("numerical_surprise", "none"),
            "relevance_score": c.get("relevance_score", 1),
            "llm_model": c.get("llm_model", ""),
            "llm_at": c.get("llm_at", ""),
        })
    try:
        r.db(DB_NAME).table(TBL_NEWS_LLM).insert(docs, conflict="replace").run(conn)
    except Exception as e:
        _log(f"NewsLLM insert error: {e}", "yellow")


# ═══════════════════════════════════════════════════════════════════════════
# 4. AGGREGATE — Daily per-symbol features
# ═══════════════════════════════════════════════════════════════════════════
def _aggregate_daily_features(
    articles: list[dict],       # raw articles with _aid
    scores: list[dict],         # FinBERT scores
    classifications: list[dict], # LLM classifications
    symbols: list[str],
    agg_date: str,              # YYYY-MM-DD
) -> dict[str, dict]:
    """
    Build per-symbol daily features from articles, scores, and LLM data.
    Returns {symbol: feature_dict}.
    """
    # Index scores and classifications by article ID
    score_by_id = {s["id"]: s for s in scores}
    class_by_id = {c["id"]: c for c in classifications}

    # Group articles by symbol
    articles_by_sym: dict[str, list[dict]] = {sym: [] for sym in symbols}
    for a in articles:
        for sym in (a.get("symbols") or []):
            if sym in articles_by_sym:
                articles_by_sym[sym].append(a)

    features = {}
    for sym in symbols:
        sym_articles = articles_by_sym.get(sym, [])
        if not sym_articles:
            continue

        sentiment_scores = []
        confidence_weights = []
        impulses = []
        bullish_strengths = []
        bearish_strengths = []
        relevances = []
        event_types = []
        forward_looking_any = False

        for a in sym_articles:
            aid = a.get("_aid", "")
            sc = score_by_id.get(aid, {})
            cl = class_by_id.get(aid, {})

            sent_score = sc.get("sentiment_score", 0.0)
            impulse = sc.get("sentiment_impulse", 0.0)
            confidence = sc.get("confidence", 0.34)
            sentiment_scores.append(sent_score)
            confidence_weights.append(confidence)
            impulses.append(impulse)

            if cl:
                direction = cl.get("impact_direction", "unclear")
                strength = cl.get("impact_strength", 1)
                if direction == "bullish":
                    bullish_strengths.append(strength)
                elif direction == "bearish":
                    bearish_strengths.append(strength)
                relevances.append(cl.get("relevance_score", 1))
                event_types.append(cl.get("event_type", "other"))
                if cl.get("is_forward_looking"):
                    forward_looking_any = True

        sentiment_sum = sum(sentiment_scores)
        # Confidence-weighted average sentiment (-1 to 1 range)
        total_weight = sum(confidence_weights) or 1.0
        sentiment_avg = sum(s * w for s, w in zip(sentiment_scores, confidence_weights)) / total_weight
        sentiment_abs_max = max((abs(s) for s in impulses), default=0.0)
        bullish_sum = sum(bullish_strengths)
        bearish_sum = sum(bearish_strengths)
        max_relevance = max(relevances, default=0)

        # Mode of event types
        event_mode = "other"
        if event_types:
            counter = Counter(event_types)
            event_mode = counter.most_common(1)[0][0]

        feat = {
            "id": f"{sym}|{agg_date}",
            "symbol": sym,
            "date": agg_date,
            "news_count": len(sym_articles),
            "sentiment_sum": round(sentiment_sum, 4),
            "sentiment_avg": round(sentiment_avg, 4),
            "sentiment_abs_max": round(sentiment_abs_max, 4),
            "bullish_strength_sum": bullish_sum,
            "bearish_strength_sum": bearish_sum,
            "max_relevance": max_relevance,
            "forward_looking_any": forward_looking_any,
            "event_type_mode": event_mode,
        }
        features[sym] = feat

    return features


def _store_ticker_day_features(conn, features: dict[str, dict]):
    """Store aggregated features in TickerDayFeatures table."""
    if conn is None or not features:
        return
    docs = list(features.values())
    try:
        r.db(DB_NAME).table(TBL_TICKER_DAY).insert(docs, conflict="replace").run(conn)
    except Exception as e:
        _log(f"TickerDayFeatures insert error: {e}", "yellow")


# ═══════════════════════════════════════════════════════════════════════════
# 5. VOTE — Decision rules
# ═══════════════════════════════════════════════════════════════════════════
def _compute_votes(
    features: dict[str, dict],
    symbols: list[str],
    cfg: dict,
    data: dict | None = None,
) -> dict[str, dict]:
    """
    Apply decision rules to daily features.
    Returns {symbol: {"score": 1|0|-1, "reason": str}}.
    """
    sent_long = float(cfg.get("SENTIMENT_AVG_LONG", cfg.get("SENTIMENT_SUM_LONG", 0.35)))
    sent_short = float(cfg.get("SENTIMENT_AVG_SHORT", cfg.get("SENTIMENT_SUM_SHORT", -0.35)))
    min_relevance = int(cfg.get("MIN_LLM_RELEVANCE", 3))
    min_strength = int(cfg.get("MIN_LLM_STRENGTH", 3))
    min_articles = int(cfg.get("MIN_ARTICLES_FOR_SIGNAL", 1))

    votes = {}
    for sym in symbols:
        feat = features.get(sym)
        if feat is None:
            votes[sym] = {"score": 0, "reason": "No news"}
            continue

        sent_avg = feat.get("sentiment_avg", feat.get("sentiment_sum", 0))
        bull_sum = feat.get("bullish_strength_sum", 0)
        bear_sum = feat.get("bearish_strength_sum", 0)
        max_rel = feat.get("max_relevance", 0)
        news_count = feat.get("news_count", 0)

        # Require minimum articles for any signal
        if news_count < min_articles:
            votes[sym] = {"score": 0, "reason": f"HOLD: only {news_count} article(s), need {min_articles}"}
            continue

        # ── Trend filter + RSI gate (buys only) ─────────────────────────
        trend_ok_for_buy = True
        trend_reason = ""
        if data and sym in data:
            bars = data[sym]
            closes = [float(b["c"]) for b in bars if b.get("c") is not None]
            if len(closes) >= 50:
                sma20 = sum(closes[-20:]) / 20
                sma50 = sum(closes[-50:]) / 50
                price_now = closes[-1]
                if price_now < sma20 and price_now < sma50:
                    trend_ok_for_buy = False
                    trend_reason = f"downtrend (price {price_now:.2f} < SMA20 {sma20:.2f} & SMA50 {sma50:.2f})"
            # RSI overbought gate
            if trend_ok_for_buy and len(closes) >= 16:
                rsi_val = _compute_rsi_simple(closes, 14)
                if rsi_val is not None and rsi_val > 75:
                    trend_ok_for_buy = False
                    trend_reason = f"RSI overbought ({rsi_val:.1f} > 75)"

        # BUY signal
        if sent_avg >= sent_long:
            if not trend_ok_for_buy:
                votes[sym] = {"score": 0, "reason": f"HOLD (blocked): sentiment_avg={sent_avg:.2f} bullish but {trend_reason}"}
                continue
            # Check LLM relevance + bullish strength dominance
            if max_rel >= min_relevance and bull_sum > bear_sum + min_strength:
                reason = (f"BUY: sentiment_avg={sent_avg:.2f}, "
                          f"bull={bull_sum} > bear={bear_sum}+{min_strength}, "
                          f"relevance={max_rel}, {news_count} articles")
                votes[sym] = {"score": 1, "reason": reason}
                continue
            elif max_rel < min_relevance and bull_sum == 0 and bear_sum == 0:
                # No LLM data — rely on FinBERT sentiment alone
                reason = (f"BUY (FinBERT only): sentiment_avg={sent_avg:.2f}, "
                          f"{news_count} articles")
                votes[sym] = {"score": 1, "reason": reason}
                continue

        # SELL signal (no trend gate — always allow exits)
        if sent_avg <= sent_short:
            if max_rel >= min_relevance and bear_sum > bull_sum + min_strength:
                reason = (f"SELL: sentiment_avg={sent_avg:.2f}, "
                          f"bear={bear_sum} > bull={bull_sum}+{min_strength}, "
                          f"relevance={max_rel}, {news_count} articles")
                votes[sym] = {"score": -1, "reason": reason}
                continue
            elif max_rel < min_relevance and bull_sum == 0 and bear_sum == 0:
                reason = (f"SELL (FinBERT only): sentiment_avg={sent_avg:.2f}, "
                          f"{news_count} articles")
                votes[sym] = {"score": -1, "reason": reason}
                continue

        # HOLD
        reason = (f"HOLD: sentiment_avg={sent_avg:.2f}, "
                  f"bull={bull_sum}, bear={bear_sum}, "
                  f"relevance={max_rel}, {news_count} articles")
        votes[sym] = {"score": 0, "reason": reason}

    return votes


def _compute_rsi_simple(closes: list[float], period: int = 14) -> float | None:
    """Compute RSI from a list of close prices without external dependencies."""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(len(closes) - period, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


# ═══════════════════════════════════════════════════════════════════════════
# Main Strategy Class
# ═══════════════════════════════════════════════════════════════════════════
class MlNews:
    """ML news pipeline. run_once: ingest -> FinBERT -> LLM classify -> aggregate -> vote."""

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
        # The live broker's scheduler forwards mode=(FULL|MONITOR|IDLE) to every
        # run_once strategy. MlNews has no lightweight monitor task, so only the
        # FULL/legacy paths should run the news pipeline. This also keeps IDLE
        # ticks from doing API/DB/model work.
        scheduler_mode = str(mode or "").strip().upper()
        if scheduler_mode in {"IDLE", "MONITOR"}:
            return {}

        config = config or {}
        conditions = conditions or {}
        strategy_cache = strategy_cache if strategy_cache is not None else {}
        ml_cache = strategy_cache.setdefault(_MLNEWS_CACHE_KEY, {})

        # ── Config ──────────────────────────────────────────────────────
        enable_llm = config.get("enable_llm", True)
        if isinstance(enable_llm, str):
            enable_llm = enable_llm.lower() in ("1", "true", "yes")
        llm_impulse_trigger = float(config.get("LLM_IMPULSE_TRIGGER", 0.55))
        llm_provider = _normalize_llm_provider(config.get("llm_provider") or "gemini")
        llm_api_key = (
            (config.get("azure_openai_api_key") or "").strip()
            or (config.get("llm_api_key") or "").strip()
            or _default_api_key_for_provider(llm_provider)
        )
        model_name = (config.get("model_name") or "").strip() or _default_model_for_provider(llm_provider)
        llm_provider_config = _resolve_provider_config(config, llm_provider)
        # `<optional>` / `<required>` / `<provider_api_key>` etc. are literal
        # schema-template placeholders. Operators who never replace them in
        # the UI ship the placeholder string through to runtime, which then
        # gets sent to Alpaca as the API key and 401s. Treat any value
        # wrapped in angle brackets as empty so the env fallback wins.
        def _strip_placeholder(v):
            s = (v or "").strip()
            if s.startswith("<") and s.endswith(">"):
                return ""
            return s
        alpaca_key = _strip_placeholder(config.get("alpaca_key")) or os.environ.get("KEY", "")
        alpaca_secret = _strip_placeholder(config.get("alpaca_secret")) or os.environ.get("SECRET", "")
        max_articles = int(config.get("max_articles_per_symbol", 10))
        lookback_hours = int(config.get("news_lookback_hours", 24))

        now_dt = _to_datetime(current_time)
        today_str = now_dt.strftime("%Y-%m-%d")

        # Skip if we already ran for this date
        last_run = ml_cache.get("last_run_date")
        if last_run == today_str:
            # Return cached votes
            cached_votes = ml_cache.get("last_votes", {})
            if cached_votes:
                _log(f"Already ran for {today_str}, returning cached votes "
                     f"({len(cached_votes)} symbols)", "cyan")
                return cached_votes

        _log(f"Starting ML news pipeline for {today_str} "
             f"({len(symbols)} symbols)", "green")

        # ── DB connection ───────────────────────────────────────────────
        conn = _get_db_conn()
        if conn:
            _ensure_tables(conn)

        # ── 1. INGEST ──────────────────────────────────────────────────
        # Determine time range: look back from current time
        inc_sec = _time_increment_to_seconds(time_increment)
        if inc_sec is not None and inc_sec < SECONDS_PER_DAY:
            start_dt = now_dt - timedelta(hours=lookback_hours)
        else:
            # Daily granularity: get prior day's news (D-1)
            start_dt = now_dt - timedelta(hours=lookback_hours)
        end_dt = now_dt

        # Check in-memory cache for this date range
        news_cache = strategy_cache.setdefault("_ml_news_articles", {})
        cache_key = (today_str, start_dt.isoformat(), end_dt.isoformat())

        if cache_key in news_cache:
            all_articles = news_cache[cache_key]
            _log(f"Using cached articles ({len(all_articles)} articles)", "cyan")
        else:
            all_articles = _fetch_alpaca_news_batch(
                symbols, start_dt, end_dt, alpaca_key, alpaca_secret,
                limit_per_symbol=max_articles,
            )
            # Evict old entries
            if len(news_cache) >= _MAX_NEWS_CACHE:
                keys_to_drop = list(news_cache.keys())[:len(news_cache) // 4]
                for k in keys_to_drop:
                    del news_cache[k]
            news_cache[cache_key] = all_articles

        if not all_articles:
            _log("No articles found, returning hold for all symbols", "yellow")
            votes = {sym: {"score": 0, "reason": "No news found"} for sym in symbols}
            ml_cache["last_run_date"] = today_str
            ml_cache["last_votes"] = votes
            return votes

        # Store raw and get new-only articles
        new_articles = _store_news_raw(conn, all_articles)
        _log(f"Ingested {len(all_articles)} articles ({len(new_articles)} new)", "cyan")

        # Use all articles for scoring (both new and cached)
        # Ensure _aid is set on all articles
        for a in all_articles:
            if "_aid" not in a:
                a["_aid"] = str(a.get("id") or hashlib.md5(
                    (a.get("headline", "") + a.get("url", "")).encode()
                ).hexdigest())

        # ── 2. SCORE with FinBERT ──────────────────────────────────────
        # Check which articles are already scored in cache
        scored_cache = strategy_cache.setdefault("_ml_news_scores", {})
        articles_to_score = [a for a in all_articles if a["_aid"] not in scored_cache]

        if articles_to_score:
            new_scores = _score_finbert_batch(articles_to_score, strategy_cache)
            _store_news_scored(conn, new_scores)
            for s in new_scores:
                scored_cache[s["id"]] = s
            _log(f"FinBERT scored {len(new_scores)} articles", "cyan")

        all_scores = [scored_cache[a["_aid"]] for a in all_articles if a["_aid"] in scored_cache]

        # ── 3. CLASSIFY high-impulse with LLM ─────────────────────────
        all_classifications = []
        if enable_llm and llm_api_key:
            llm_cache = strategy_cache.setdefault("_ml_news_llm", {})
            high_impulse = [
                a for a in all_articles
                if abs((scored_cache.get(a["_aid"]) or {}).get("sentiment_impulse", 0))
                >= llm_impulse_trigger
                and a["_aid"] not in llm_cache
            ]

            if high_impulse:
                _log(f"LLM classifying {len(high_impulse)} high-impulse articles "
                     f"(trigger={llm_impulse_trigger})", "cyan")
                new_classifications = _classify_llm_batch(
                    high_impulse, llm_provider, llm_api_key, model_name, conn, provider_config=llm_provider_config
                )
                _store_news_llm(conn, new_classifications)
                for c in new_classifications:
                    llm_cache[c["id"]] = c
                _log(f"LLM classified {len(new_classifications)} articles", "cyan")

            # Gather all classifications for this batch
            all_classifications = [
                llm_cache[a["_aid"]] for a in all_articles
                if a["_aid"] in llm_cache
            ]

        # ── 4. AGGREGATE daily features ────────────────────────────────
        agg_date = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        features = _aggregate_daily_features(
            all_articles, all_scores, all_classifications, symbols, agg_date
        )
        _store_ticker_day_features(conn, features)

        symbols_with_news = [sym for sym in symbols if sym in features]
        _log(f"Aggregated features for {len(symbols_with_news)} symbols "
             f"(date={agg_date})", "cyan")

        # Store features in cache for other strategies
        daily_cache = ml_cache.setdefault("daily_features_cache", {})
        daily_cache[agg_date] = features

        # ── 5. VOTE ────────────────────────────────────────────────────
        votes = _compute_votes(features, symbols, config, data=data)

        # Add score=0 for symbols with no news
        for sym in symbols:
            if sym not in votes:
                votes[sym] = {"score": 0, "reason": "No news"}

        # Log vote summary
        buy_count = sum(1 for v in votes.values() if v["score"] == 1)
        sell_count = sum(1 for v in votes.values() if v["score"] == -1)
        hold_count = sum(1 for v in votes.values() if v["score"] == 0)
        _log(f"Votes: {buy_count} BUY, {sell_count} SELL, {hold_count} HOLD", "green")
        for sym, v in votes.items():
            if v["score"] != 0:
                _log(f"  {sym}: {v['reason']}", "green" if v["score"] == 1 else "yellow")

        # Cache results
        ml_cache["last_run_date"] = today_str
        ml_cache["last_votes"] = votes
        ml_cache["last_ingested_at"] = end_dt.isoformat()

        return votes
