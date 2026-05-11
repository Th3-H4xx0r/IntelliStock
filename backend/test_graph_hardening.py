import json
import os
import sys
import tempfile
import unittest
from collections import deque
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import ModuleType
from unittest.mock import ANY, patch

from pydantic import BaseModel


BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ENGINES_DIR = os.path.join(BACKEND_DIR, "engines")
STRATEGIES_DIR = os.path.join(BACKEND_DIR, "strategies")
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
if ENGINES_DIR not in sys.path:
    sys.path.insert(0, ENGINES_DIR)
if STRATEGIES_DIR not in sys.path:
    sys.path.insert(0, STRATEGIES_DIR)

if "rethinkdb" not in sys.modules:
    rethinkdb_stub = ModuleType("rethinkdb")

    class _FakeRethinkDB:
        pass

    rethinkdb_stub.RethinkDB = _FakeRethinkDB
    sys.modules["rethinkdb"] = rethinkdb_stub

if "waitress" not in sys.modules:
    waitress_stub = ModuleType("waitress")

    def _fake_serve(*args, **kwargs):
        return None

    waitress_stub.serve = _fake_serve
    sys.modules["waitress"] = waitress_stub

import nexus_graph_engine as gns
import sec_edgar_supply_chain as sec
import graph_nexus_analysis as gna
import ai_backtest_engine as abe
import discover_engine as dve
import server as srv
import interactive_utils as iu
import llm_utils as llu
import benzinga_client as bzc
import weight_optimizer as wopt
import strategies_meta as sm


class GraphHardeningTests(unittest.TestCase):
    def test_llm_utils_validate_structured_output_wraps_top_level_list_for_batch_models(self):
        raw_text = json.dumps([
            {
                "ref": "a1",
                "classifications": [
                    {
                        "ticker": "NVDA",
                        "event_type": "general",
                        "impact_direction": "bullish",
                        "impact_strength": 0.7,
                        "is_forward_looking": True,
                        "expected_horizon_days": 7,
                        "relevance_score": 0.9,
                    }
                ],
            }
        ])
        parsed = llu._validate_structured_output_from_raw_text(gna._CompanyArticleBatchResponse, raw_text)
        self.assertIsInstance(parsed, gna._CompanyArticleBatchResponse)
        self.assertEqual(1, len(parsed.articles))
        self.assertEqual("a1", parsed.articles[0].ref)
        self.assertEqual("NVDA", parsed.articles[0].classifications[0].ticker)

    def test_llm_utils_prefer_raw_json_structured_path_can_bypass_agent(self):
        raw_text = json.dumps({
            "classifications": [
                {
                    "ticker": "AMD",
                    "event_type": "general",
                    "impact_direction": "bullish",
                    "impact_strength": 0.6,
                    "is_forward_looking": True,
                    "expected_horizon_days": 5,
                    "relevance_score": 0.8,
                }
            ]
        })
        with patch.object(llu, "_PYDANTIC_AI_AVAILABLE", True), \
             patch.object(llu, "_structured_model_candidates", return_value=["Kimi-K2.5"]), \
             patch.object(llu, "call_llm_by_provider", return_value=raw_text) as raw_mock, \
             patch.object(llu, "Agent") as agent_mock:
            result = llu.call_structured_llm_by_provider(
                "azure",
                "azure-key",
                "Kimi-K2.5",
                "prompt",
                gna._CompanyArticleClassificationResponse,
                prefer_raw_json=True,
                provider_config={
                    "azure_endpoint": "https://example-resource.services.ai.azure.com",
                    "api_version": "2024-10-21",
                },
            )
        self.assertIsInstance(result, gna._CompanyArticleClassificationResponse)
        self.assertEqual("AMD", result.classifications[0].ticker)
        raw_mock.assert_called_once()
        agent_mock.assert_not_called()

    def test_llm_utils_azure_gpt_oss_auto_prefers_raw_json_structured_path(self):
        raw_text = json.dumps({
            "classifications": [
                {
                    "ticker": "NVDA",
                    "event_type": "general",
                    "impact_direction": "bullish",
                    "impact_strength": 0.7,
                    "is_forward_looking": True,
                    "expected_horizon_days": 7,
                    "relevance_score": 0.9,
                }
            ]
        })
        with patch.object(llu, "_PYDANTIC_AI_AVAILABLE", True), \
             patch.object(llu, "_structured_model_candidates", return_value=["gpt-oss-120B"]), \
             patch.object(llu, "call_llm_by_provider", return_value=raw_text) as raw_mock, \
             patch.object(llu, "Agent") as agent_mock:
            result = llu.call_structured_llm_by_provider(
                "azure",
                "azure-key",
                "gpt-oss-120B",
                "prompt",
                gna._CompanyArticleClassificationResponse,
                provider_config={
                    "azure_endpoint": "https://example-resource.services.ai.azure.com",
                    "api_version": "2024-10-21",
                },
            )
        self.assertIsInstance(result, gna._CompanyArticleClassificationResponse)
        self.assertEqual("NVDA", result.classifications[0].ticker)
        raw_mock.assert_called_once()
        agent_mock.assert_not_called()

    def test_llm_utils_model_reference_appends_reasoning_effort_suffix(self):
        self.assertEqual("gpt-oss-120B-HIGH", llu.llm_model_reference("gpt-oss-120B", "high"))
        self.assertEqual("gpt-oss-120B", llu.llm_model_reference("gpt-oss-120B", ""))

    def test_llm_utils_azure_reasoning_effort_uses_max_completion_tokens(self):
        captured = {}

        class _Resp:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "ok",
                            }
                        }
                    ]
                }

        def _fake_post(url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            captured["timeout"] = timeout
            return _Resp()

        with patch("requests.post", side_effect=_fake_post):
            out = llu.call_llm_by_provider(
                "azure",
                "azure-key",
                "gpt-oss-120B",
                "hello",
                max_output_tokens=321,
                provider_config={
                    "azure_endpoint": "https://example-resource.services.ai.azure.com",
                    "api_version": "2024-10-21",
                    "reasoning_effort": "high",
                },
            )

        self.assertEqual("ok", out)
        self.assertEqual("high", captured["json"]["reasoning_effort"])
        self.assertEqual(321, captured["json"]["max_completion_tokens"])
        self.assertNotIn("max_tokens", captured["json"])

    def test_llm_utils_normalize_azure_endpoint_trims_full_urls_to_resource_root(self):
        self.assertEqual(
            "https://intellistock-prod-ai-resource.services.ai.azure.com",
            llu._normalize_azure_endpoint(
                "https://intellistock-prod-ai-resource.services.ai.azure.com/models/chat/completions?api-version=2024-05-01-preview"
            ),
        )
        self.assertEqual(
            "https://intellistock-prod-ai-resource.services.ai.azure.com",
            llu._normalize_azure_endpoint(
                "https://intellistock-prod-ai-resource.services.ai.azure.com/openai/v1/"
            ),
        )

    def test_llm_utils_structured_azure_404_is_cached_and_suppressed(self):
        llu._TERMINAL_LLM_FAILURES.clear()
        call_counter = {"count": 0}

        class _FakeAgent:
            def __init__(self, *args, **kwargs):
                pass

            def run_sync(self, prompt, infer_name=False):
                call_counter["count"] += 1
                raise RuntimeError(
                    "status_code: 404, model_name: Kimi-K2.5, body: {'code': '404', 'message': 'Resource not found'}"
                )

        provider_config = {
            "azure_endpoint": "https://intellistock-prod-ai-resource.services.ai.azure.com/openai/v1/",
            "api_version": "2024-10-21",
        }
        with patch.object(llu, "_PYDANTIC_AI_AVAILABLE", True), \
             patch.object(llu, "_build_pydantic_ai_model", return_value=object()), \
             patch.object(llu, "Agent", _FakeAgent):
            first = llu.call_structured_llm_by_provider(
                "azure",
                "azure-key",
                "Kimi-K2.5",
                "ping",
                dict,
                provider_config=provider_config,
            )
            second = llu.call_structured_llm_by_provider(
                "azure",
                "azure-key",
                "Kimi-K2.5",
                "ping",
                dict,
                provider_config=provider_config,
            )

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(1, call_counter["count"])
        meta = llu.get_last_structured_llm_call_metadata()
        self.assertTrue(meta.get("suppressed"))
        self.assertIn("Azure 404 usually means", meta.get("error", ""))

    def test_benzinga_paginate_accepts_top_level_list_response(self):
        with patch.object(bzc, "_bz_get", return_value=[{"ticker": "SPY"}, {"ticker": "QQQ"}]):
            items = bzc._paginate("/api/v2/calendar/ratings", {}, "token", "ratings", max_results=10)
        self.assertEqual([{"ticker": "SPY"}, {"ticker": "QQQ"}], items)

    def test_benzinga_forbidden_endpoint_is_suppressed_after_first_failure(self):
        bzc._BZ_UNAVAILABLE_ENDPOINTS.clear()

        class _Resp:
            status_code = 403

            def raise_for_status(self):
                raise bzc.requests.exceptions.HTTPError("403 forbidden")

        with patch.object(bzc.requests, "get", return_value=_Resp()) as get_mock:
            first = bzc._bz_get("/api/v2/calendar/ma", {}, "token")
            second = bzc._bz_get("/api/v2/calendar/ma", {}, "token")
        self.assertEqual({}, first)
        self.assertEqual({}, second)
        self.assertEqual(1, get_mock.call_count)

    def test_benzinga_sanitize_company_actions_drops_blank_rows_and_duplicates(self):
        records = [
            {
                "ticker": "",
                "date": "2025-11-17",
                "name": "",
                "event": "dividend",
                "amount": None,
                "ex_date": "",
                "payable_date": "",
            },
            {
                "ticker": "spy",
                "date": "2025-11-17",
                "name": "",
                "event": "dividend",
                "amount": 1.25,
                "ex_date": "",
                "payable_date": "",
            },
            {
                "ticker": "SPY",
                "date": "2025-11-17",
                "name": "",
                "event": "dividend",
                "amount": 1.25,
                "ex_date": "",
                "payable_date": "",
            },
            {
                "ticker": "QQQ",
                "date": "2025-11-17",
                "name": "",
                "event": "dividend",
                "amount": 0.55,
                "ex_date": "",
                "payable_date": "",
            },
        ]
        cleaned, dropped = bzc._sanitize_benzinga_records("company_actions", records, ["SPY"])
        self.assertEqual(
            [
                {
                    "ticker": "SPY",
                    "date": "2025-11-17",
                    "name": "",
                    "event": "dividend",
                    "amount": 1.25,
                    "ex_date": "",
                    "payable_date": "",
                }
            ],
            cleaned,
        )
        self.assertEqual(3, dropped)

    def test_benzinga_fetch_rewrites_polluted_cached_company_actions(self):
        cached_rows = [
            {
                "ticker": "",
                "date": "2025-11-17",
                "name": "",
                "event": "dividend",
                "amount": None,
                "ex_date": "",
                "payable_date": "",
            },
            {
                "ticker": "SPY",
                "date": "2025-11-17",
                "name": "",
                "event": "dividend",
                "amount": 1.25,
                "ex_date": "",
                "payable_date": "",
            },
        ]
        cache_write = {}

        def _capture_cache_set(_conn, key, data):
            cache_write["key"] = key
            cache_write["data"] = data

        with patch.object(bzc, "_get_bz_conn", return_value=object()), \
             patch.object(bzc, "_cache_get", return_value=cached_rows), \
             patch.object(bzc, "_cache_set", side_effect=_capture_cache_set), \
             patch.object(bzc, "_fetch_company_actions_raw") as fetch_mock:
            rows = bzc.fetch_benzinga_data(
                data_type="company_actions",
                token="token",
                date_key="2025-11-10",
                tickers=["SPY"],
            )

        self.assertEqual(
            [
                {
                    "ticker": "SPY",
                    "date": "2025-11-17",
                    "name": "",
                    "event": "dividend",
                    "amount": 1.25,
                    "ex_date": "",
                    "payable_date": "",
                }
            ],
            rows,
        )
        self.assertEqual(rows, cache_write["data"])
        fetch_mock.assert_not_called()

    def test_benzinga_gov_trades_accepts_top_level_list_response(self):
        with patch.object(
            bzc,
            "_bz_get",
            return_value=[{"ticker": "SPY", "date": "2025-11-10", "transaction_type": "Buy"}],
        ):
            items = bzc._fetch_gov_trades_raw("token", ["SPY"], "2025-11-01", "2025-11-10", max_results=10)
        self.assertEqual(
            [
                {
                    "ticker": "SPY",
                    "date": "2025-11-10",
                    "politician": "",
                    "chamber": "",
                    "direction": "buy",
                    "value_usd": None,
                }
            ],
            items,
        )

    def test_nexus_resolve_neo4j_runtime_config_uses_remote_default_when_env_blank(self):
        with patch.dict(os.environ, {"NEO4J_URI": "", "NEO4J_USER": "", "NEO4J_PASSWORD": ""}, clear=False):
            uri, user, password = gna._resolve_neo4j_runtime_config({})
        self.assertEqual("bolt://localhost:7687", uri)
        self.assertEqual("neo4j", user)
        self.assertEqual("intellistock", password)

    def test_build_llm_trace_keeps_prompt_size_and_head_tail_previews(self):
        prompt = "HEADER\n" + ("A" * 1900) + "\nTAIL"
        system_prompt = "SYS\n" + ("B" * 900) + "\nEND"
        with patch.object(gna, "get_last_structured_llm_call_metadata", return_value={"provider": "azure", "effective_model": "Kimi-K2.5", "ok": True, "usage": {}}):
            trace = gna._build_llm_trace("macro_article", "azure", "Kimi-K2.5", prompt, system_prompt, "macro_v1", True)
        self.assertEqual(len(prompt), trace["prompt_chars"])
        self.assertTrue(trace["prompt_preview_truncated"])
        self.assertTrue(trace["prompt_preview"].startswith("HEADER"))
        self.assertTrue(trace["prompt_preview_tail"].endswith("TAIL"))
        self.assertEqual(len(system_prompt), trace["system_prompt_chars"])
        self.assertTrue(trace["system_prompt_preview_truncated"])
        self.assertTrue(trace["system_prompt_preview"].startswith("SYS"))
        self.assertTrue(trace["system_prompt_preview_tail"].endswith("END"))

    def test_nexus_edge_active_after_clause_uses_parser_safe_not_in_form(self):
        clause = gna._edge_active_after_clause("r")
        self.assertIn("NOT (coalesce(r.retired_reason, '') IN [", clause)
        self.assertNotIn("coalesce(r.retired_reason, '') NOT IN", clause)

    def test_private_entity_alias_index_query_uses_fixed_edge_clause(self):
        captured = {}

        class _FakeSession:
            def run(self, query, **params):
                captured["query"] = query
                captured["params"] = params
                return []

        rows = gna._load_private_entity_alias_index(_FakeSession(), as_of_date="2025-11-10")
        self.assertEqual([], rows)
        self.assertIn("OPTIONAL MATCH (parent:Company)-[r:PARENT_OF_ENTITY]->(le)", captured["query"])
        self.assertIn("NOT (coalesce(r.retired_reason, '') IN [", captured["query"])
        self.assertIn("WITH le, [ticker IN collect(DISTINCT parent.ticker) WHERE ticker IS NOT NULL] AS parent_tickers", captured["query"])
        self.assertIn("CASE WHEN size(parent_tickers) > 0 THEN parent_tickers ELSE coalesce(le.listed_ancestor_tickers, []) END AS ancestor_tickers", captured["query"])
        self.assertEqual("2025-11-10", captured["params"]["as_of_date"])

    def test_private_entity_alias_index_query_can_filter_by_candidate_aliases(self):
        captured = {}

        class _FakeSession:
            def run(self, query, **params):
                captured["query"] = query
                captured["params"] = params
                return []

        rows = gna._load_private_entity_alias_index(_FakeSession(), as_of_date="2025-11-10", candidate_aliases={"ring", "beats"})
        self.assertEqual([], rows)
        self.assertIn("candidate_aliases", captured["params"])
        self.assertEqual(["beats", "ring"], captured["params"]["candidate_aliases"])
        self.assertIn("ANY(alias_norm IN coalesce(le.normalized_aliases, []) WHERE alias_norm IN $candidate_aliases)", captured["query"])

    def test_private_entity_candidate_alias_extraction_finds_entity_phrases(self):
        candidates = gna._extract_private_entity_candidate_aliases_from_text(
            "Amazon's Ring launched a new doorbell while Bank of America raised guidance."
        )
        self.assertIn("ring", candidates)
        self.assertIn("bank of america", candidates)

    def test_nexus_ignores_legacy_sentiment_cache_without_fingerprint_entry(self):
        articles = [{"id": "a1", "headline": "Example", "url": "https://example.com/a1"}]
        doc = {
            "id": "2025-11-10",
            "articles": list(articles),
            "sentiment_data": {"SPY": {"sentiment": 1, "event": "general"}},
            "sentiment_by_fingerprint": {},
        }
        captured_logs = []

        class _FakeGet:
            def __init__(self, payload):
                self.payload = payload

            def run(self, conn):
                return self.payload

        class _FakeTable:
            def __init__(self, payload):
                self.payload = payload

            def get(self, _doc_id):
                return _FakeGet(self.payload)

        class _FakeDb:
            def __init__(self, payload):
                self.payload = payload

            def table(self, _table_name):
                return _FakeTable(self.payload)

        class _FakeR:
            def __init__(self, payload):
                self.payload = payload

            def db(self, _db_name):
                return _FakeDb(self.payload)

        with patch.object(gna, "_r", _FakeR(doc)), \
             patch.object(gna, "_log", side_effect=lambda msg, *_args, **_kwargs: captured_logs.append(msg)):
            cached_articles, cached_sentiment = gna._get_cached_articles(object(), "2025-11-10")

        self.assertEqual(articles, cached_articles)
        self.assertIsNone(cached_sentiment)
        self.assertTrue(any("Legacy sentiment cache ignored" in msg for msg in captured_logs))

    def test_nexus_uses_scope_specific_sentiment_cache_entry(self):
        articles = [{"id": "a1", "headline": "Example", "url": "https://example.com/a1"}]
        fp = gna._article_set_fingerprint(articles)
        doc = {
            "id": "2025-11-10",
            "articles": list(articles),
            "sentiment_by_scope": {
                "scope-a": {
                    fp: {
                        "sentiment_data": {"SPY": {"sentiment": 1, "event": "general"}},
                        "sentiment_cache_scope_id": "scope-a",
                    }
                }
            },
        }

        class _FakeGet:
            def __init__(self, payload):
                self.payload = payload

            def run(self, conn):
                return self.payload

        class _FakeTable:
            def __init__(self, payload):
                self.payload = payload

            def get(self, _doc_id):
                return _FakeGet(self.payload)

        class _FakeDb:
            def __init__(self, payload):
                self.payload = payload

            def table(self, _table_name):
                return _FakeTable(self.payload)

        class _FakeR:
            def __init__(self, payload):
                self.payload = payload

            def db(self, _db_name):
                return _FakeDb(self.payload)

        with patch.object(gna, "_r", _FakeR(doc)):
            cached_articles, cached_sentiment = gna._get_cached_articles(
                object(),
                "2025-11-10",
                sentiment_cache_scope_id="scope-a",
            )

        self.assertEqual(articles, cached_articles)
        self.assertEqual({"SPY": {"sentiment": 1, "event": "general"}}, cached_sentiment)

    def test_nexus_scope_mismatched_sentiment_cache_is_not_reused(self):
        articles = [{"id": "a1", "headline": "Example", "url": "https://example.com/a1"}]
        fp = gna._article_set_fingerprint(articles)
        doc = {
            "id": "2025-11-10",
            "articles": list(articles),
            "sentiment_by_scope": {
                "scope-a": {
                    fp: {
                        "sentiment_data": {"SPY": {"sentiment": 1, "event": "general"}},
                        "sentiment_cache_scope_id": "scope-a",
                    }
                }
            },
            "sentiment_data": {"SPY": {"sentiment": 1, "event": "general"}},
        }
        captured_logs = []

        class _FakeGet:
            def __init__(self, payload):
                self.payload = payload

            def run(self, conn):
                return self.payload

        class _FakeTable:
            def __init__(self, payload):
                self.payload = payload

            def get(self, _doc_id):
                return _FakeGet(self.payload)

        class _FakeDb:
            def __init__(self, payload):
                self.payload = payload

            def table(self, _table_name):
                return _FakeTable(self.payload)

        class _FakeR:
            def __init__(self, payload):
                self.payload = payload

            def db(self, _db_name):
                return _FakeDb(self.payload)

        with patch.object(gna, "_r", _FakeR(doc)), \
             patch.object(gna, "_log", side_effect=lambda msg, *_args, **_kwargs: captured_logs.append(msg)):
            cached_articles, cached_sentiment = gna._get_cached_articles(
                object(),
                "2025-11-10",
                sentiment_cache_scope_id="scope-b",
            )

        self.assertEqual(articles, cached_articles)
        self.assertIsNone(cached_sentiment)
        self.assertTrue(any("scope MISS" in msg for msg in captured_logs))

    def test_html_to_visible_text_reads_far_enough_into_html_for_article_text(self):
        html = (
            "<html><body>"
            + ("<div></div>" * 900)
            + "<article><p>Important Benzinga article content here.</p></article>"
            + "</body></html>"
        )
        text = gna._html_to_visible_text(html, max_chars=120)
        self.assertIn("Important Benzinga article content here.", text)

    def test_finbert_rows_include_article_provenance(self):
        stored_rows = []
        normalized_articles = [
            {
                "article_hash": "abc123",
                "headline": "Test headline",
                "summary": "Test summary",
                "content_excerpt": "Excerpt",
                "date_key": "2025-11-10",
                "source": "alpaca",
                "published_at": "2025-11-10T12:00:00Z",
                "instance_id": "default",
            }
        ]

        def _capture_store(_conn, rows):
            stored_rows.extend(rows)

        with patch.object(gna, "_load_finbert_cache_rows", return_value={}), \
             patch.object(gna, "_ml_news_score_finbert_batch", return_value=[{
                 "id": "abc123",
                 "finbert_pos": 0.8,
                 "finbert_neg": 0.1,
                 "finbert_neu": 0.1,
                 "sentiment_score": 0.7,
                 "confidence": 0.8,
                 "sentiment_impulse": 0.6,
             }]), \
             patch.object(gna, "_store_finbert_rows", side_effect=_capture_store):
            rows = gna._score_finbert_for_articles(object(), normalized_articles, {})

        self.assertIn("abc123", rows)
        self.assertEqual(1, len(stored_rows))
        self.assertEqual("2025-11-10", stored_rows[0]["date_key"])
        self.assertEqual("alpaca", stored_rows[0]["source"])
        self.assertEqual("2025-11-10T12:00:00Z", stored_rows[0]["published_at"])
        self.assertEqual("default", stored_rows[0]["instance_id"])

    def test_recent_price_features_capture_returns_and_drawdown(self):
        price_history = {
            "NVDA": [{"c": float(v)} for v in [100, 102, 101, 104, 106, 108, 110, 112, 111, 115, 118, 120, 119, 121, 124, 126, 128, 127, 129, 131, 130]]
        }
        features = gna._recent_price_features(price_history, "NVDA")
        self.assertGreater(features["recent_return_5"], 0.0)
        self.assertGreater(features["recent_return_20"], 0.0)
        self.assertLessEqual(features["drawdown_from_20_high"], 0.0)

    def test_apply_ml_overlay_scores_does_not_buy_when_ml_is_extremely_bearish(self):
        scores = {
            "SMCI": {"score": 1, "reason": "Direct general sentiment=+1 (raw=+1.000, 1 paths)"}
        }
        sentiment_data = {"SMCI": {"sentiment": 1, "event": "general"}}
        propagated = {"SMCI": {"raw_score": 1.0, "reasons": ["direct"], "n_paths": 1}}
        price_history = {
            "SMCI": [{"c": float(v)} for v in [100, 97, 95, 93, 91, 90, 88, 86, 84, 83, 82, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71]]
        }
        bearish_ml = {
            "ml_up_probability": 0.01,
            "ml_down_probability": 0.99,
            "ml_expected_return": -0.70,
            "ml_confidence": 0.98,
        }
        with patch.object(gna, "_maybe_train_nexus_ml_bundle", return_value=(object(), {"trained": True})), \
             patch.object(gna, "_score_nexus_candidate", return_value=bearish_ml), \
             patch.object(gna, "_summarize_nexus_candidate_features", return_value=[]), \
             patch.object(gna, "_retrieve_historical_analogs", return_value=[]):
            enriched, features, traces = gna._apply_ml_and_overlay_to_scores(
                ["SMCI"],
                scores,
                sentiment_data=sentiment_data,
                propagated=propagated,
                finbert_rows={},
                company_rows=[],
                macro_rows=[],
                active_events=[],
                conn=None,
                instance_id="test",
                date_key="2025-12-19",
                strategy_cache={},
                config={"buy_threshold": 0.15, "sell_threshold": -0.15, "llm_overlay_enabled": False},
                price_history=price_history,
                portfolio_emulator=None,
            )

        self.assertEqual({"SMCI": []}, traces)
        self.assertIn("SMCI", features)
        self.assertNotEqual(1, enriched["SMCI"]["score"])
        self.assertLess(enriched["SMCI"]["raw_net_score"], 0.15)

    def test_company_article_chunk_fallback_splits_failed_batch(self):
        single_left = gna._CompanyArticleClassificationResponse(
            classifications=[
                gna._CompanyArticleTickerClassification(
                    ticker="NVDA",
                    event_type="general",
                    impact_direction="bullish",
                    impact_strength=0.6,
                    is_forward_looking=True,
                    expected_horizon_days=5,
                    relevance_score=0.9,
                )
            ]
        )
        single_right = gna._CompanyArticleClassificationResponse(
            classifications=[
                gna._CompanyArticleTickerClassification(
                    ticker="AMD",
                    event_type="general",
                    impact_direction="bearish",
                    impact_strength=0.5,
                    is_forward_looking=True,
                    expected_horizon_days=3,
                    relevance_score=0.8,
                )
            ]
        )
        responses = [None, single_left, single_right]

        def _fake_call(*args, **kwargs):
            return responses.pop(0)

        chunk = [
            ({"article_hash": "a1", "source": "alpaca", "published_at": "2025-12-19T12:00:00Z", "date_key": "2025-12-19", "headline": "A", "summary": "", "content_excerpt": "", "symbols": ["NVDA"]}, "cid-1"),
            ({"article_hash": "a2", "source": "alpaca", "published_at": "2025-12-19T12:05:00Z", "date_key": "2025-12-19", "headline": "B", "summary": "", "content_excerpt": "", "symbols": ["AMD"]}, "cid-2"),
        ]
        with patch.object(gna, "call_structured_llm_by_provider", side_effect=_fake_call):
            docs, traces = gna._classify_company_article_chunk(
                chunk,
                provider="azure",
                api_key="k",
                model="m",
                prompt_version="v1",
                provider_config={},
                date_key="2025-12-19",
                use_toon=True,
                instance_id="inst",
                system_prompt="system",
            )

        self.assertEqual(2, len(docs))
        self.assertEqual({"a1", "a2"}, {doc["article_hash"] for doc in docs})
        self.assertEqual(2, len(traces))

    def test_strategy_payload_normalizer_canonicalizes_nexus_and_preserves_run_once(self):
        normalized = iu._normalize_strategy_payload_item(
            {
                "strategy": "GraphNexusAnalysis",
                "weight": 0.8,
                "execution_position": 2,
                "decision_phase": "pre",
                "execution_scope": "per_symbol",
                "conditions": {},
                "config": {},
            },
            strict=True,
        )
        self.assertEqual("graph_nexus_analysis", normalized["strategy"])
        self.assertEqual("run_once", normalized["execution_scope"])
        self.assertEqual("pre", normalized["decision_phase"])

    def test_strategy_payload_normalizer_promotes_conditions_into_config(self):
        normalized = iu._normalize_strategy_payload_item(
            {
                "strategy": "graph_nexus_analysis",
                "weight": 0.8,
                "execution_position": 2,
                "decision_phase": "pre",
                "execution_scope": "run_once",
                "conditions": {"min_articles": 20, "num_articles": 50},
                "config": {"min_articles": 25, "lookback_learning_days": 90},
            },
            strict=True,
        )
        self.assertEqual({}, normalized["conditions"])
        self.assertEqual(25, normalized["config"]["min_articles"])
        self.assertEqual(50, normalized["config"]["max_daily_alpaca_articles"])
        self.assertEqual(90, normalized["config"]["lookback_learning_days"])
        self.assertNotIn("num_articles", normalized["config"])

    def test_available_strategy_schema_exposes_legacy_conditions_inside_config(self):
        strategies = sm.get_available_strategies()
        nexus = next(item for item in strategies if item["id"] == "graph_nexus_analysis")
        self.assertEqual({}, nexus["schema"]["conditions"])
        self.assertEqual(20, nexus["schema"]["config"]["min_articles"])
        self.assertEqual(50, nexus["schema"]["config"]["max_daily_alpaca_articles"])
        self.assertNotIn("num_articles", nexus["schema"]["config"])

    def test_nexus_auto_update_timestamp_helper_returns_iso_utc(self):
        value = gns._compute_next_auto_update_at(24)
        self.assertTrue(value.endswith("Z"))
        self.assertGreaterEqual(len(value), 20)

    def test_gleif_request_interval_respects_rate_cap(self):
        with patch.object(gns, "GLEIF_REQUEST_DELAY", 1.0), patch.object(gns, "GLEIF_MAX_REQUESTS_PER_MIN", 40):
            self.assertAlmostEqual(1.5, gns._gleif_request_interval_seconds(), places=3)
        with patch.object(gns, "GLEIF_REQUEST_DELAY", 2.0), patch.object(gns, "GLEIF_MAX_REQUESTS_PER_MIN", 40):
            self.assertAlmostEqual(2.0, gns._gleif_request_interval_seconds(), places=3)

    def test_discover_engine_retryable_rethink_error_detection(self):
        self.assertTrue(dve._is_retryable_rethink_error(Exception("primary replica for shard not available")))
        self.assertTrue(dve._is_retryable_rethink_error(Exception("Connection refused")))
        self.assertFalse(dve._is_retryable_rethink_error(Exception("some other failure")))

    def test_nexus_cache_delete_path_validation_accepts_visible_entries(self):
        normalized = iu._normalize_nexus_cache_delete_paths(
            ["phase1", "supply_chain_sec_edgar.csv", "phase1"],
            [
                {"path": "phase1", "is_dir": True},
                {"path": "supply_chain_sec_edgar.csv", "is_dir": False},
            ],
        )
        self.assertEqual(["phase1", "supply_chain_sec_edgar.csv"], normalized)

    def test_nexus_cache_delete_path_validation_rejects_traversal(self):
        with self.assertRaises(ValueError):
            iu._normalize_nexus_cache_delete_paths(
                ["../sec_edgar_filings"],
                [{"path": "phase1", "is_dir": True}],
            )

    def test_normalize_nexus_historical_start_date_rejects_future_dates(self):
        future_date = (datetime.now(timezone.utc).date() + timedelta(days=7)).isoformat()
        with self.assertRaises(ValueError):
            iu._normalize_nexus_historical_start_date(future_date)

    def test_action_nexus_cache_entries_uses_direct_filesystem_root_without_docker(self):
        with tempfile.TemporaryDirectory() as cache_root:
            os.mkdir(os.path.join(cache_root, "phase1"))
            with open(os.path.join(cache_root, "supply_chain_sec_edgar.csv"), "w", encoding="utf-8") as handle:
                handle.write("ticker,edge\n")
            with patch.object(iu, "NEXUS_CONTAINER_CACHE_ROOT", cache_root), \
                 patch.object(iu, "_get_running_nexus_container", return_value=None):
                result = iu.action_nexus_cache_entries()
        self.assertTrue(result["available"])
        self.assertFalse(result["container_running"])
        self.assertEqual(os.path.realpath(cache_root), os.path.realpath(result["cache_root"]))
        self.assertEqual(
            ["phase1", "supply_chain_sec_edgar.csv"],
            [entry["path"] for entry in result["entries"]],
        )
        self.assertIsNone(result["error"])

    def test_action_nexus_rebuild_deletes_selected_direct_cache_entries_without_docker(self):
        with tempfile.TemporaryDirectory() as cache_root:
            phase1_dir = os.path.join(cache_root, "phase1")
            keep_path = os.path.join(cache_root, "keep.txt")
            os.mkdir(phase1_dir)
            with open(keep_path, "w", encoding="utf-8") as handle:
                handle.write("keep\n")
            with patch.object(iu, "NEXUS_CONTAINER_CACHE_ROOT", cache_root), \
                 patch.object(iu, "get_engine_doc", return_value={}), \
                 patch.object(iu, "_get_nexus_container", return_value=None), \
                 patch.object(iu, "_get_running_nexus_container", return_value=None), \
                 patch.object(iu, "update_engine_doc"), \
                 patch.object(iu, "_queue_nexus_rebuild") as mock_queue:
                result = iu.action_nexus_rebuild(
                    conn=object(),
                    delete_cache_paths=["phase1"],
                    destructive=False,
                )
            self.assertTrue(result["success"])
            self.assertEqual(os.path.realpath(cache_root), os.path.realpath(result["cache_root"]))
            self.assertEqual(["phase1"], result["deleted_cache_paths"])
            self.assertFalse(result["container_running"])
            self.assertFalse(result["container_restarted"])
            self.assertFalse(os.path.exists(phase1_dir))
            self.assertTrue(os.path.exists(keep_path))
            mock_queue.assert_called_once()

    def test_clear_nexus_graph_neo4j_deletes_in_batches(self):
        queries = []

        class _FakeResult:
            def __init__(self, deleted):
                self._deleted = deleted

            def single(self):
                return {"deleted": self._deleted}

        class _FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def run(self, query, **params):
                queries.append((query, params))
                if "MATCH ()-[r]-()" in query:
                    deleted = 3 if len([q for q, _ in queries if "MATCH ()-[r]-()" in q]) == 1 else 0
                    return _FakeResult(deleted)
                if "MATCH (n)" in query:
                    deleted = 2 if len([q for q, _ in queries if "MATCH (n)" in q]) == 1 else 0
                    return _FakeResult(deleted)
                raise AssertionError("Unexpected query")

        class _FakeDriver:
            def session(self):
                return _FakeSession()

            def close(self):
                return None

        neo4j_stub = ModuleType("neo4j")

        class _FakeGraphDatabase:
            @staticmethod
            def driver(uri, auth=None):
                return _FakeDriver()

        neo4j_stub.GraphDatabase = _FakeGraphDatabase

        with patch.dict(sys.modules, {"neo4j": neo4j_stub}), \
             patch.dict(os.environ, {"NEXUS_NEO4J_CLEAR_BATCH_SIZE": "1000", "NEXUS_NEO4J_CLEAR_MIN_BATCH_SIZE": "100"}):
            iu._clear_nexus_graph_neo4j()

        self.assertTrue(any("MATCH ()-[r]-()" in query for query, _ in queries))
        self.assertTrue(any("MATCH (n)" in query for query, _ in queries))
        self.assertTrue(any("DETACH DELETE n" in query for query, _ in queries))

    def test_clear_nexus_graph_neo4j_reduces_batch_size_on_heap_error(self):
        rel_limits = []

        class _FakeResult:
            def __init__(self, deleted):
                self._deleted = deleted

            def single(self):
                return {"deleted": self._deleted}

        class _FakeSession:
            def __init__(self):
                self.rel_calls = 0
                self.node_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def run(self, query, **params):
                if "MATCH ()-[r]-()" in query:
                    self.rel_calls += 1
                    rel_limits.append(params["limit"])
                    if self.rel_calls == 1:
                        raise Exception("Java heap space")
                    deleted = 1 if self.rel_calls == 2 else 0
                    return _FakeResult(deleted)
                if "MATCH (n)" in query:
                    self.node_calls += 1
                    deleted = 1 if self.node_calls == 1 else 0
                    return _FakeResult(deleted)
                raise AssertionError("Unexpected query")

        class _FakeDriver:
            def session(self):
                return _FakeSession()

            def close(self):
                return None

        neo4j_stub = ModuleType("neo4j")

        class _FakeGraphDatabase:
            @staticmethod
            def driver(uri, auth=None):
                return _FakeDriver()

        neo4j_stub.GraphDatabase = _FakeGraphDatabase

        with patch.dict(sys.modules, {"neo4j": neo4j_stub}), \
             patch.dict(os.environ, {"NEXUS_NEO4J_CLEAR_BATCH_SIZE": "1000", "NEXUS_NEO4J_CLEAR_MIN_BATCH_SIZE": "250"}):
            iu._clear_nexus_graph_neo4j()

        self.assertEqual([1000, 500, 500], rel_limits)

    def test_action_nexus_control_set_force_bootstrap_rebuild_resets_history(self):
        existing_doc = {
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "historical_bootstrap_complete": True,
            "historical_coverage_end": "2026-03-10",
            "historical_phase_manifests": {"phase3": {"bootstrap_complete": True}},
            "last_historical_bootstrap_status": "completed",
        }
        captured = {}

        def _capture_update(conn, engine_id, update):
            captured.update(update)

        with patch.object(iu, "ensure_nexus_control_table"), \
             patch.object(iu, "get_engine_doc", return_value=existing_doc), \
             patch.object(iu, "_reset_nexus_bootstrap_progress_state") as mock_reset_bootstrap, \
             patch.object(iu, "update_engine_doc", side_effect=_capture_update), \
             patch.object(iu, "action_nexus_control_get", return_value={"ok": True}):
            result = iu.action_nexus_control_set(
                object(),
                running=True,
                force_bootstrap_rebuild=True,
            )

        self.assertEqual({"ok": True}, result)
        mock_reset_bootstrap.assert_called_once_with(
            ANY,
            "Queued bootstrap rebuild from configured historical start date",
            reset_progress_docs=True,
        )
        self.assertTrue(captured["running"])
        self.assertTrue(captured["force_bootstrap_rebuild"])
        self.assertFalse(captured["historical_bootstrap_complete"])
        self.assertEqual({}, captured["historical_phase_manifests"])
        self.assertIsNone(captured["historical_coverage_end"])

    def test_action_nexus_control_set_force_bootstrap_rebuild_reenables_stored_bootstrap(self):
        existing_doc = {
            "historical_mode_enabled": False,
            "historical_start_date": "2025-01-01",
            "historical_bootstrap_complete": True,
            "historical_phase_manifests": {"phase3": {"bootstrap_complete": True}},
        }
        captured = {}

        def _capture_update(conn, engine_id, update):
            captured.update(update)

        with patch.object(iu, "ensure_nexus_control_table"), \
             patch.object(iu, "get_engine_doc", return_value=existing_doc), \
             patch.object(iu, "_reset_nexus_bootstrap_progress_state") as mock_reset_bootstrap, \
             patch.object(iu, "update_engine_doc", side_effect=_capture_update), \
             patch.object(iu, "action_nexus_control_get", return_value={"ok": True}):
            result = iu.action_nexus_control_set(
                object(),
                running=True,
                force_bootstrap_rebuild=True,
            )

        self.assertEqual({"ok": True}, result)
        mock_reset_bootstrap.assert_called_once_with(
            ANY,
            "Queued bootstrap rebuild from configured historical start date",
            reset_progress_docs=True,
        )
        self.assertTrue(captured["historical_mode_enabled"])
        self.assertEqual("2025-01-01", captured["historical_start_date"])
        self.assertTrue(captured["force_bootstrap_rebuild"])

    def test_action_nexus_control_set_accepts_phase6b_selector(self):
        captured = {}

        def _capture_update(conn, engine_id, update):
            captured.update(update)

        with patch.object(iu, "ensure_nexus_control_table"), \
             patch.object(iu, "get_engine_doc", return_value={}), \
             patch.object(iu, "update_engine_doc", side_effect=_capture_update), \
             patch.object(iu, "action_nexus_control_get", return_value={"ok": True}):
            result = iu.action_nexus_control_set(
                object(),
                running=True,
                start_phase="6b",
                end_phase="12",
                auto_update_start_phase="6",
                auto_update_end_phase="12",
            )

        self.assertEqual({"ok": True}, result)
        self.assertEqual(8, captured["start_phase"])
        self.assertEqual(14, captured["end_phase"])
        self.assertEqual(7, captured["auto_update_start_phase"])
        self.assertEqual(14, captured["auto_update_end_phase"])

    def test_action_nexus_control_get_migrates_legacy_phase_schema(self):
        doc = {
            "running": False,
            "start_phase": 7,
            "end_phase": 13,
            "auto_update_start_phase": 3,
            "auto_update_end_phase": 13,
            "phase_selector_schema_version": 1,
        }
        captured = {}

        def _capture_update(conn, engine_id, update):
            captured.update(update)
            doc.update(update)

        with patch.object(iu, "ensure_engine_control_table"), \
             patch.object(iu, "get_engine_doc", return_value=doc), \
             patch.object(iu, "update_engine_doc", side_effect=_capture_update):
            result = iu.action_nexus_control_get(object())

        self.assertEqual(3, captured["phase_selector_schema_version"])
        self.assertEqual(7, captured["start_phase"])
        self.assertEqual(14, captured["end_phase"])
        self.assertEqual(14, captured["auto_update_end_phase"])
        self.assertEqual("Phase 12: ETF universe", result["end_phase_label"])
        self.assertEqual("Phase 12: ETF universe", result["auto_update_end_phase_label"])

    def test_action_nexus_control_set_normalizes_selected_phases_and_clears_range(self):
        captured = {}

        def _capture_update(conn, engine_id, update):
            captured.update(update)

        with patch.object(iu, "ensure_nexus_control_table"), \
             patch.object(iu, "get_engine_doc", return_value={}), \
             patch.object(iu, "update_engine_doc", side_effect=_capture_update), \
             patch.object(iu, "action_nexus_control_get", return_value={"ok": True}):
            result = iu.action_nexus_control_set(
                object(),
                running=True,
                start_phase="2",
                end_phase="12",
                selected_phases=["10", "3", "6b", "3"],
            )

        self.assertEqual({"ok": True}, result)
        self.assertEqual(3, captured["phase_selector_schema_version"])
        self.assertEqual([4, 8, 12], captured["selected_phases"])
        self.assertIsNone(captured["start_phase"])
        self.assertIsNone(captured["end_phase"])

    def test_action_nexus_control_get_returns_selected_phase_labels(self):
        doc = {
            "running": False,
            "selected_phases": [4, 8, 12],
            "phase_selector_schema_version": 3,
        }

        with patch.object(iu, "ensure_engine_control_table"), \
             patch.object(iu, "get_engine_doc", return_value=doc):
            result = iu.action_nexus_control_get(object())

        self.assertEqual([4, 8, 12], result["selected_phases"])
        self.assertEqual(
            [
                "Phase 3: Supply chain",
                "Phase 6B: SEC EX-21 hierarchy",
                "Phase 10: PatentsView",
            ],
            result["selected_phase_labels"],
        )

    def test_action_nexus_control_get_includes_delete_operation_defaults(self):
        with patch.object(iu, "ensure_nexus_control_table"), \
             patch.object(iu, "get_engine_doc", return_value=None):
            result = iu.action_nexus_control_get(object())

        self.assertEqual([3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14], [row["value"] for row in result["delete_phase_options"]])
        self.assertFalse(result["delete_operation_active"])
        self.assertEqual([], result["delete_operation_phase_rows"])
        self.assertIsNone(result["delete_operation_selected_phases"])

    def test_action_nexus_delete_edges_queues_async_operation(self):
        queued = {}

        with patch.object(iu, "ensure_nexus_control_table"), \
             patch.object(iu, "get_engine_doc", return_value={"running": False, "rebuild_operation_active": False, "delete_operation_active": False}), \
             patch.object(iu, "_set_nexus_delete_operation", side_effect=lambda conn, **kwargs: queued.update(kwargs)), \
             patch.object(iu, "_start_nexus_delete_operation_async") as mock_start:
            result = iu.action_nexus_delete_edges(object(), ["2b", "8", "11"])

        self.assertTrue(result["success"])
        self.assertEqual([3, 10, 13], result["selected_phases"])
        self.assertEqual([3, 10, 13], queued["selected_phases"])
        self.assertTrue(queued["active"])
        self.assertEqual(3, queued["total"])
        self.assertEqual(3, len(queued["phase_rows"]))
        mock_start.assert_called_once()

    def test_action_nexus_delete_edges_accepts_phase6_hierarchy_selection(self):
        queued = {}

        with patch.object(iu, "ensure_nexus_control_table"), \
             patch.object(iu, "get_engine_doc", return_value={"running": False, "rebuild_operation_active": False, "delete_operation_active": False}), \
             patch.object(iu, "_set_nexus_delete_operation", side_effect=lambda conn, **kwargs: queued.update(kwargs)), \
             patch.object(iu, "_start_nexus_delete_operation_async") as mock_start:
            result = iu.action_nexus_delete_edges(object(), ["6"])

        self.assertTrue(result["success"])
        self.assertEqual([7], result["selected_phases"])
        self.assertEqual([7], queued["selected_phases"])
        mock_start.assert_called_once()

    def test_action_nexus_delete_edges_rejects_empty_selection(self):
        with patch.object(iu, "ensure_nexus_control_table"), \
             patch.object(iu, "get_engine_doc", return_value={"running": False, "rebuild_operation_active": False, "delete_operation_active": False}):
            with self.assertRaises(ValueError):
                iu.action_nexus_delete_edges(object(), [])

    def test_action_nexus_delete_edges_rejects_while_running(self):
        with patch.object(iu, "ensure_nexus_control_table"), \
             patch.object(iu, "get_engine_doc", return_value={"running": True, "rebuild_operation_active": False, "delete_operation_active": False}):
            with self.assertRaises(ValueError):
                iu.action_nexus_delete_edges(object(), [9])

    def test_execute_nexus_delete_operation_completes_zero_count_phase(self):
        updates = []

        class _FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class _FakeDriver:
            def session(self):
                return _FakeSession()

            def close(self):
                return None

        neo4j_stub = ModuleType("neo4j")

        class _FakeGraphDatabase:
            @staticmethod
            def driver(uri, auth=None):
                return _FakeDriver()

        neo4j_stub.GraphDatabase = _FakeGraphDatabase

        with patch.dict(sys.modules, {"neo4j": neo4j_stub}), \
             patch.object(iu, "_nexus_delete_phase_specs", return_value={
                 10: {"label": "Phase 8: USASpending", "operations": [{"label": "USASpending edges", "count_query": "count", "delete_query": "delete"}]}
             }), \
             patch.object(iu, "_set_nexus_delete_operation", side_effect=lambda conn, **kwargs: updates.append(kwargs)), \
             patch.object(iu, "_nexus_delete_query_count", return_value=0), \
             patch.object(iu, "_nexus_delete_query_batch", return_value=0):
            iu._execute_nexus_delete_operation(object(), [10], started_at="2026-03-13T00:00:00Z")

        self.assertEqual("Delete complete", updates[-1]["step"])
        self.assertFalse(updates[-1]["active"])
        self.assertEqual("completed", updates[-1]["phase_rows"][0]["status"])
        self.assertEqual(100.0, updates[-1]["phase_rows"][0]["progress_pct"])

    def test_execute_nexus_delete_operation_updates_phase_progress(self):
        updates = []

        class _FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class _FakeDriver:
            def session(self):
                return _FakeSession()

            def close(self):
                return None

        neo4j_stub = ModuleType("neo4j")

        class _FakeGraphDatabase:
            @staticmethod
            def driver(uri, auth=None):
                return _FakeDriver()

        neo4j_stub.GraphDatabase = _FakeGraphDatabase
        count_values = [3]
        batch_values = [2, 1, 0]

        with patch.dict(sys.modules, {"neo4j": neo4j_stub}), \
             patch.object(iu, "_nexus_delete_phase_specs", return_value={
                 9: {"label": "Phase 7: 13F ownership", "operations": [{"label": "13F holdings edges", "count_query": "count", "delete_query": "delete", "batch_size": 2}]}
             }), \
             patch.object(iu, "_set_nexus_delete_operation", side_effect=lambda conn, **kwargs: updates.append(kwargs)), \
             patch.object(iu, "_nexus_delete_query_count", side_effect=lambda session, query, params=None: count_values.pop(0)), \
             patch.object(iu, "_nexus_delete_query_batch", side_effect=lambda session, query, limit, params=None: batch_values.pop(0)):
            iu._execute_nexus_delete_operation(object(), [9], started_at="2026-03-13T00:00:00Z")

        phase_rows = updates[-1]["phase_rows"]
        self.assertEqual("completed", phase_rows[0]["status"])
        self.assertEqual(3, phase_rows[0]["deleted_count"])
        self.assertEqual(100.0, phase_rows[0]["progress_pct"])

    def test_nexus_delete_phase_specs_include_phase6_hierarchy_cleanup(self):
        specs = iu._nexus_delete_phase_specs()

        self.assertIn(7, specs)
        operations = specs[7]["operations"]
        labels = [op["label"] for op in operations]
        self.assertIn("GLEIF hierarchy edges", labels)
        self.assertIn("GLEIF projection edges", labels)
        self.assertIn("legacy GLEIF parent LEI edges", labels)
        self.assertIn("GLEIF hierarchy intervals", labels)

        interval_query = next(
            op["count_query"]
            for op in operations
            if op["label"] == "GLEIF hierarchy intervals"
        )
        self.assertIn("PARENT_OF_ENTITY", interval_query)
        self.assertIn("CORPORATE_HIERARCHY_PROJECTION", interval_query)
        self.assertIn("GLEIF_PARENT_LEI", interval_query)

    def test_action_nexus_status_forces_summary_refresh_while_delete_active(self):
        class _FakeTableList:
            @staticmethod
            def run(conn):
                return []

        class _FakeDB:
            @staticmethod
            def table_list():
                return _FakeTableList()

        class _FakeR:
            @staticmethod
            def db(name):
                return _FakeDB()

        with patch.object(iu, "action_nexus_control_get", return_value={"running": False, "rebuild_operation_active": False, "delete_operation_active": True}), \
             patch.object(iu, "_build_nexus_bootstrap_status", return_value={"status": "disabled"}), \
             patch.object(iu, "_load_nexus_graph_summary", return_value={"relationship_counts": [], "node_counts": {} }) as mock_summary, \
             patch.object(iu, "r", _FakeR()):
            result = iu.action_nexus_status(object())

        self.assertEqual({"status": "disabled"}, result["bootstrap"])
        self.assertTrue(mock_summary.call_args.kwargs["force_refresh"])

    def test_run_build_executes_only_selected_phases(self):
        executed = []

        class _FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class _FakeDriver:
            def session(self):
                return _FakeSession()

        phase_patches = [
            patch.object(gns, "phase1_company_universe", side_effect=lambda session: executed.append(1)),
            patch.object(gns, "phase2_easy_relationships", side_effect=lambda session: executed.append(2)),
            patch.object(gns, "phase2b_sec_sector_industry", side_effect=lambda session: executed.append(3)),
            patch.object(gns, "phase3_supply_chain", side_effect=lambda session: executed.append(4)),
            patch.object(gns, "phase4_competitive", side_effect=lambda session: executed.append(5)),
            patch.object(gns, "phase5_macro", side_effect=lambda session: executed.append(6)),
            patch.object(gns, "phase6_gleif_hierarchy", side_effect=lambda session: executed.append(7)),
            patch.object(gns, "phase6b_sec_ex21_hierarchy", side_effect=lambda session: executed.append(8)),
            patch.object(gns, "phase7_13f_ownership", side_effect=lambda session: executed.append(9)),
            patch.object(gns, "phase9_usaspending", side_effect=lambda session: executed.append(10)),
            patch.object(gns, "phase10_wikidata", side_effect=lambda session: executed.append(11)),
            patch.object(gns, "phase11_patents", side_effect=lambda session: executed.append(12)),
            patch.object(gns, "phase12_8k_agreements", side_effect=lambda session: executed.append(13)),
            patch.object(gns, "phase13_etf_universe", side_effect=lambda session: executed.append(14)),
        ]

        with ExitStack() as stack:
            for phase_patch in phase_patches:
                stack.enter_context(phase_patch)
            stack.enter_context(patch.object(gns, "ensure_neo4j_init"))
            stack.enter_context(patch.object(gns, "is_graph_built", return_value=True))
            stack.enter_context(patch.object(gns, "_load_nexus_control_doc", return_value={"selected_phases": [4, 8, 12], "phase7_history_quarters": 1}))
            stack.enter_context(patch.object(gns, "_set_nexus_temporal_state_from_control"))
            stack.enter_context(patch.object(gns, "_save_graph_nexus_progress"))
            stack.enter_context(patch.object(gns, "_load_graph_nexus_progress", return_value=None))
            stack.enter_context(patch.object(gns, "_nexus_stage_reset"))
            stack.enter_context(patch.object(gns, "_ensure_default_phase_manifest_after_run"))
            stack.enter_context(patch.object(gns, "_persist_nexus_temporal_state"))
            stack.enter_context(patch.object(gns, "_nexus_runtimes_load", return_value={}))
            stack.enter_context(patch.object(gns, "_nexus_runtimes_save"))
            stack.enter_context(patch.object(gns, "_nexus_stage_log"))
            stack.enter_context(patch.object(gns, "_nexus_control_want_stop", return_value=False))
            stack.enter_context(patch.object(gns, "_mark_nexus_bootstrap_complete"))
            stack.enter_context(patch.object(gns, "_nexus_estimate_remaining", return_value=None))
            stack.enter_context(patch.object(gns, "_progress"))
            stack.enter_context(patch.object(gns, "_log"))
            result = gns._run_build(_FakeDriver(), object())

        self.assertTrue(result)
        self.assertEqual([4, 8, 12], executed)

    def test_action_nexus_rebuild_force_bootstrap_reset_sets_one_shot_flag(self):
        captured_updates = []

        with patch.object(iu, "get_engine_doc", return_value={"historical_start_date": "2025-01-01"}), \
             patch.object(iu, "_get_nexus_container", return_value=None), \
             patch.object(iu, "_get_running_nexus_container", return_value=None), \
             patch.object(iu, "_reset_nexus_bootstrap_progress_state") as mock_reset_bootstrap, \
             patch.object(iu, "_queue_nexus_rebuild") as mock_queue, \
             patch.object(iu, "update_engine_doc", side_effect=lambda conn, engine_id, update: captured_updates.append(update)):
            result = iu.action_nexus_rebuild(
                conn=object(),
                destructive=False,
                force_bootstrap_rebuild=True,
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["force_bootstrap_rebuild"])
        mock_reset_bootstrap.assert_called_once_with(
            ANY,
            "Queued non-destructive rebuild from phase 1 with historical bootstrap reset",
            reset_progress_docs=True,
        )
        bootstrap_updates = [u for u in captured_updates if "force_bootstrap_rebuild" in u]
        self.assertEqual(1, len(bootstrap_updates))
        self.assertTrue(bootstrap_updates[0]["force_bootstrap_rebuild"])
        self.assertTrue(bootstrap_updates[0]["historical_mode_enabled"])
        self.assertEqual("2025-01-01", bootstrap_updates[0]["historical_start_date"])
        self.assertFalse(bootstrap_updates[0]["historical_bootstrap_complete"])
        self.assertEqual({}, bootstrap_updates[0]["historical_phase_manifests"])
        mock_queue.assert_called_once()

    def test_action_nexus_rebuild_destructive_resets_bootstrap_progress_state(self):
        with patch.object(iu, "get_engine_doc", return_value={"historical_start_date": "2025-01-01"}), \
             patch.object(iu, "_get_nexus_container", return_value=None), \
             patch.object(iu, "_get_running_nexus_container", return_value=None), \
             patch.object(iu, "_clear_nexus_graph_neo4j"), \
             patch.object(iu, "_reset_nexus_bootstrap_progress_state") as mock_reset_bootstrap, \
             patch.object(iu, "update_engine_doc"), \
             patch.object(iu, "_queue_nexus_rebuild"):
            result = iu.action_nexus_rebuild(
                conn=object(),
                destructive=True,
                force_bootstrap_rebuild=False,
            )

        self.assertTrue(result["success"])
        self.assertTrue(result["neo4j_cleared"])
        self.assertTrue(result["rethinkdb_cleared"])
        mock_reset_bootstrap.assert_called_once_with(
            ANY,
            "Queued destructive rebuild from phase 1 after clearing Neo4j and Nexus progress",
            reset_progress_docs=True,
        )

    def test_action_nexus_rebuild_force_bootstrap_clears_phase3_derived_caches_only(self):
        with tempfile.TemporaryDirectory() as cache_root:
            os.mkdir(os.path.join(cache_root, "historical_10k_filings"))
            os.mkdir(os.path.join(cache_root, "parsed_edges"))
            with open(os.path.join(cache_root, "supply_chain_sec_edgar.csv"), "w", encoding="utf-8") as handle:
                handle.write("sup,cust\n")
            with patch.object(iu, "NEXUS_CONTAINER_CACHE_ROOT", cache_root), \
                 patch.object(iu, "get_engine_doc", return_value={"historical_start_date": "2025-01-01"}), \
                 patch.object(iu, "_get_nexus_container", return_value=None), \
                 patch.object(iu, "_get_running_nexus_container", return_value=None), \
                 patch.object(iu, "_reset_nexus_bootstrap_progress_state"), \
                 patch.object(iu, "update_engine_doc"), \
                 patch.object(iu, "_queue_nexus_rebuild"):
                result = iu.action_nexus_rebuild(
                    conn=object(),
                    destructive=False,
                    force_bootstrap_rebuild=True,
                )

            self.assertTrue(result["success"])
            self.assertEqual(
                ["parsed_edges", "supply_chain_sec_edgar.csv"],
                sorted(result["deleted_cache_paths"]),
            )
            self.assertTrue(os.path.isdir(os.path.join(cache_root, "historical_10k_filings")))
            self.assertFalse(os.path.exists(os.path.join(cache_root, "parsed_edges")))
            self.assertFalse(os.path.exists(os.path.join(cache_root, "supply_chain_sec_edgar.csv")))

    def test_build_nexus_bootstrap_status_prefers_completed_over_service_running_when_manifests_done(self):
        control = {
            "running": True,
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "historical_bootstrap_complete": False,
            "historical_coverage_end": "2026-03-10",
            "last_historical_bootstrap_status": None,
            "historical_phase_manifests": {
                "phase3": {"bootstrap_complete": True, "coverage_end": "2026-03-06"},
                "phase7": {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
                "phase12": {"bootstrap_complete": True, "coverage_end": "2026-03-06"},
            },
        }
        graph_build = {
            "status": "completed",
            "current_phase_label": "Build complete.",
            "current_phase_number": 13,
        }

        status = iu._build_nexus_bootstrap_status(control, graph_build)

        self.assertEqual("completed", status["status"])
        self.assertTrue(status["complete"])
        self.assertEqual(3, status["completed_phases"])
        self.assertIn("Historical bootstrap complete", status["message"])

    def test_build_nexus_bootstrap_status_masks_stale_completed_manifests_while_rebuild_running(self):
        control = {
            "running": True,
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "historical_bootstrap_complete": True,
            "historical_coverage_end": None,
            "last_historical_bootstrap_started_at": "2026-03-10T08:00:00Z",
            "last_historical_bootstrap_completed_at": "2026-03-09T08:00:00Z",
            "last_historical_bootstrap_status": "running",
            "historical_phase_manifests": {
                "phase3": {"bootstrap_complete": True, "coverage_end": "2026-03-06"},
                "phase7": {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
                "phase12": {"bootstrap_complete": True, "coverage_end": "2026-03-06"},
            },
        }
        graph_build = {
            "status": "running",
            "current_phase_label": "Phase 1: Company universe",
            "current_phase_number": 2,
        }

        status = iu._build_nexus_bootstrap_status(control, graph_build)

        self.assertEqual("running", status["status"])
        self.assertFalse(status["complete"])
        self.assertEqual(0, status["completed_phases"])
        self.assertEqual("2025-01-01", status["start_date"])
        self.assertIsNone(status["coverage_end"])
        self.assertTrue(all(row["status"] == "pending" for row in status["phases"]))

    def test_build_nexus_bootstrap_status_masks_stale_completed_manifests_after_fresh_rebuild_request(self):
        control = {
            "running": True,
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "historical_bootstrap_complete": False,
            "historical_coverage_end": None,
            "last_historical_bootstrap_completed_at": "2026-03-10T06:00:00Z",
            "last_historical_bootstrap_status": "completed",
            "rebuild_requested_at": "2026-03-10T07:00:00Z",
            "historical_phase_manifests": {
                "phase3": {"bootstrap_complete": True, "coverage_end": "2026-03-06"},
                "phase7": {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
                "phase12": {"bootstrap_complete": True, "coverage_end": "2026-03-06"},
            },
        }
        graph_build = {
            "status": "running",
            "current_phase_label": "Phase 1: Company universe",
            "current_phase_number": 2,
        }

        status = iu._build_nexus_bootstrap_status(control, graph_build)

        self.assertEqual("running", status["status"])
        self.assertFalse(status["complete"])
        self.assertEqual(0, status["completed_phases"])
        self.assertIsNone(status["coverage_end"])
        self.assertTrue(all(row["status"] == "pending" for row in status["phases"]))

    def test_build_nexus_bootstrap_status_stopped_mid_rebuild_is_not_completed(self):
        control = {
            "running": False,
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "historical_bootstrap_complete": False,
            "historical_coverage_end": None,
            "last_historical_bootstrap_completed_at": "2026-03-10T06:00:00Z",
            "last_historical_bootstrap_status": "completed",
            "rebuild_requested_at": "2026-03-10T07:00:00Z",
            "historical_phase_manifests": {
                "phase3": {"bootstrap_complete": True, "coverage_end": "2026-03-06"},
                "phase7": {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
                "phase12": {"bootstrap_complete": True, "coverage_end": "2026-03-06"},
            },
        }
        graph_build = {
            "status": "stopped",
            "current_phase_label": "Phase 6: GLEIF hierarchy",
            "current_phase_number": 8,
        }

        status = iu._build_nexus_bootstrap_status(control, graph_build)

        self.assertEqual("partial", status["status"])
        self.assertFalse(status["complete"])
        self.assertEqual(1, status["completed_phases"])
        self.assertIsNone(status["coverage_end"])
        self.assertIn("stopped before completion", status["message"].lower())

    def test_nexus_apply_control_to_graph_build_marks_stale_running_as_stopped(self):
        graph_build = {
            "status": "running",
            "message": "Phase 6 in progress",
            "status_message": "Phase 6: GLEIF hierarchy",
            "stages": [
                {"stage_index": 8, "label": "Phase 6: GLEIF hierarchy", "status": "running"},
                {"stage_index": 7, "label": "Phase 5: Macro/BEA", "status": "completed"},
            ],
        }

        normalized = iu._nexus_apply_control_to_graph_build(
            {"running": False, "rebuild_operation_active": False},
            graph_build,
        )

        self.assertEqual("stopped", normalized["status"])
        self.assertEqual("Stopped", normalized["status_message"])
        self.assertEqual("stopped", normalized["stages"][0]["status"])
        self.assertEqual("completed", normalized["stages"][1]["status"])

    def test_nexus_apply_control_to_graph_build_keeps_only_latest_running_stage(self):
        graph_build = {
            "status": "running",
            "message": "Phase 8 in progress",
            "status_message": "Phase 8: USASpending",
            "current_phase_number": 10,
            "current_phase_label": "Phase 8: USASpending",
            "stages": [
                {
                    "stage_index": 10,
                    "label": "Phase 7: 13F ownership",
                    "status": "running",
                    "total_substeps": 2,
                    "substeps_completed": 0,
                },
                {
                    "stage_index": 11,
                    "label": "Phase 8: USASpending",
                    "status": "running",
                    "total_substeps": 2,
                    "substeps_completed": 0,
                },
                {"stage_index": 12, "label": "Phase 9: Wikidata", "status": "pending"},
            ],
        }

        normalized = iu._nexus_apply_control_to_graph_build(
            {"running": True, "rebuild_operation_active": False},
            graph_build,
        )

        self.assertEqual("completed", normalized["stages"][0]["status"])
        self.assertEqual(2, normalized["stages"][0]["substeps_completed"])
        self.assertEqual("running", normalized["stages"][1]["status"])
        self.assertEqual("pending", normalized["stages"][2]["status"])

    def test_nexus_bootstrap_considered_complete_uses_manifest_completion(self):
        self.assertTrue(gns._nexus_bootstrap_considered_complete({
            "historical_bootstrap_complete": False,
            "historical_phase_manifests": {
                "phase3": {"bootstrap_complete": True},
                "phase7": {"bootstrap_complete": True},
                "phase12": {"bootstrap_complete": True},
            },
        }))
        self.assertFalse(gns._nexus_bootstrap_considered_complete({
            "historical_bootstrap_complete": False,
            "historical_phase_manifests": {
                "phase3": {"bootstrap_complete": True},
                "phase7": {"bootstrap_complete": False},
                "phase12": {"bootstrap_complete": True},
            },
        }))

    def test_load_nexus_graph_summary_returns_cached_relationship_and_node_counts(self):
        class _FakeResult(list):
            def single(self):
                return self[0] if self else {}

        class _FakeSession:
            def run(self, query, **kwargs):
                if "MATCH ()-[r]->()" in query:
                    return _FakeResult([
                        {"rel_type": "SUPPLIER_OF", "active_count": 12, "total_count": 15},
                        {"rel_type": "HOLDS", "active_count": 300, "total_count": 2000},
                    ])
                if "CALL { MATCH (:Company)" in query:
                    return _FakeResult([{
                        "companies": 5307,
                        "etfs": 176,
                        "institutions": 11372,
                        "agencies": 54,
                        "edge_intervals": 2022298,
                    }])
                return _FakeResult([])

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        class _FakeDriver:
            def session(self):
                return _FakeSession()

            def close(self):
                return None

        class _FakeGraphDatabase:
            @staticmethod
            def driver(*args, **kwargs):
                return _FakeDriver()

        fake_neo4j = ModuleType("neo4j")
        fake_neo4j.GraphDatabase = _FakeGraphDatabase

        with patch.dict(sys.modules, {"neo4j": fake_neo4j}), \
             patch.object(iu, "_NEXUS_GRAPH_SUMMARY_CACHE", {"fetched_at": 0.0, "data": None}):
            summary = iu._load_nexus_graph_summary()

        by_key = {row["key"]: row for row in summary["relationship_counts"]}
        self.assertEqual(12, by_key["SUPPLIER_OF"]["active_count"])
        self.assertEqual(2000, by_key["HOLDS"]["total_count"])
        self.assertEqual(5307, summary["node_counts"]["companies"])
        self.assertEqual(2022298, summary["node_counts"]["edge_intervals"])

    def test_destructive_nexus_progress_reset_docs_start_clean(self):
        graph_doc, scraper_doc = iu._destructive_nexus_progress_reset_docs("Queued destructive rebuild")
        self.assertEqual("queued", graph_doc["status"])
        self.assertEqual(0, graph_doc["last_completed_phase"])
        self.assertEqual([], graph_doc["stages"])
        self.assertEqual("pending", scraper_doc["status"])
        self.assertEqual(0, scraper_doc["last_ticker_index"])
        self.assertEqual(0, scraper_doc["edges_count"])

    def test_graph_strategy_edge_clause_respects_valid_until_and_bad_retire_reasons(self):
        clause = gna._edge_active_after_clause("r")
        self.assertIn("r.valid_until", clause)
        self.assertIn("false_positive", clause)
        self.assertIn("direction_conflict", clause)
        self.assertIn("toString(date())", clause)

    def test_structured_llm_model_builder_supports_gemini_deepseek_openai_and_azure(self):
        gemini_model = llu._build_pydantic_ai_model("gemini", "test-key", "gemini-3-flash-preview")
        deepseek_model = llu._build_pydantic_ai_model("deepseek", "test-key", "deepseek-chat")
        deepseek_reasoner_model = llu._build_pydantic_ai_model("deepseek", "test-key", "deepseek-reasoner")
        openai_model = llu._build_pydantic_ai_model("openai", "test-key", "gpt-4.1-mini")
        azure_model = llu._build_pydantic_ai_model(
            "azure",
            "test-key",
            "azure-gpt4-mini",
            provider_config={"azure_endpoint": "https://example-resource.openai.azure.com", "api_version": "2024-10-21"},
        )
        self.assertEqual("GoogleModel", gemini_model.__class__.__name__)
        self.assertEqual("OpenAIChatModel", deepseek_model.__class__.__name__)
        self.assertEqual("OpenAIChatModel", deepseek_reasoner_model.__class__.__name__)
        self.assertEqual("OpenAIChatModel", openai_model.__class__.__name__)
        self.assertEqual("OpenAIChatModel", azure_model.__class__.__name__)
        self.assertTrue(deepseek_reasoner_model.profile.supports_json_object_output)
        self.assertEqual("prompted", deepseek_reasoner_model.profile.default_structured_output_mode)

    def test_structured_llm_model_builder_azure_gpt_oss_uses_prompted_profile(self):
        azure_model = llu._build_pydantic_ai_model(
            "azure",
            "test-key",
            "gpt-oss-120B",
            provider_config={"azure_endpoint": "https://example-resource.openai.azure.com", "api_version": "2024-10-21"},
        )
        self.assertEqual("OpenAIChatModel", azure_model.__class__.__name__)
        self.assertIsNotNone(azure_model.profile)
        self.assertTrue(azure_model.profile.supports_json_object_output)
        self.assertEqual("prompted", azure_model.profile.default_structured_output_mode)

    def test_structured_llm_azure_requires_endpoint_and_api_version(self):
        with self.assertRaisesRegex(ValueError, "azure_endpoint"):
            llu._build_pydantic_ai_model("azure", "test-key", "azure-gpt4-mini", provider_config={"api_version": "2024-10-21"})

    def test_structured_model_name_preserves_deepseek_reasoner(self):
        self.assertEqual("deepseek-reasoner", llu._structured_model_name("deepseek", "deepseek-reasoner"))

    def test_structured_json_retry_enabled_for_unsupported_tool_use(self):
        self.assertTrue(
            llu._structured_json_retry_enabled(
                "status_code: 400, body: {'code': 'UnsupportedToolUse', 'message': \"tool_choice 'required' is not supported\"}"
            )
        )

    def test_prompt_cache_helper_forwards_provider_config_and_scopes_cache_key(self):
        captured = {}

        def _fake_call(provider, api_key, model, prompt, max_output_tokens=256, provider_config=None, **_kwargs):
            captured["provider"] = provider
            captured["provider_config"] = provider_config
            return "ok"

        with patch.object(llu, "call_llm_by_provider", side_effect=_fake_call):
            raw, from_cache = llu.call_llm_with_prompt_cache(
                "azure",
                "azure-key",
                "azure-gpt4-mini",
                "hello",
                provider_config={
                    "azure_endpoint": "https://example-resource.openai.azure.com",
                    "api_version": "2024-10-21",
                },
                db_conn=None,
            )

        self.assertEqual("ok", raw)
        self.assertFalse(from_cache)
        self.assertEqual("azure", captured["provider"])
        self.assertEqual(
            {
                "azure_endpoint": "https://example-resource.openai.azure.com",
                "api_version": "2024-10-21",
            },
            captured["provider_config"],
        )

    def test_structured_llm_deepseek_reasoner_falls_back_to_chat(self):
        attempts = []

        class _FakeResult:
            def __init__(self, output):
                self.output = output

        class _FakeAgent:
            def __init__(self, model_obj, **kwargs):
                self.model_obj = model_obj

            def run_sync(self, prompt, infer_name=False):
                attempts.append(self.model_obj)
                if self.model_obj == "deepseek-reasoner":
                    raise RuntimeError("reasoner structured call failed")
                return _FakeResult({"model": self.model_obj, "ok": True})

        with patch.object(llu, "Agent", _FakeAgent), \
             patch.object(llu, "_build_pydantic_ai_model", side_effect=lambda provider, api_key, model, **kwargs: model):
            result = llu.call_structured_llm_by_provider(
                provider="deepseek",
                api_key="test-key",
                model="deepseek-reasoner",
                prompt="Return any valid object.",
                output_type=dict,
            )

        self.assertEqual(["deepseek-reasoner", "deepseek-chat"], attempts)
        self.assertEqual({"model": "deepseek-chat", "ok": True}, result)

    def test_structured_llm_metadata_tracks_effective_model_and_fallback(self):
        class _FakeResult:
            def __init__(self, output):
                self.output = output

        class _FakeAgent:
            def __init__(self, model_obj, **kwargs):
                self.model_obj = model_obj

            def run_sync(self, prompt, infer_name=False):
                if self.model_obj == "deepseek-reasoner":
                    raise RuntimeError("reasoner failed")
                return _FakeResult({"ok": True})

        with patch.object(llu, "Agent", _FakeAgent), \
             patch.object(llu, "_build_pydantic_ai_model", side_effect=lambda provider, api_key, model, **kwargs: model):
            llu.call_structured_llm_by_provider(
                provider="deepseek",
                api_key="test-key",
                model="deepseek-reasoner",
                prompt="Return any valid object.",
                output_type=dict,
            )
            meta = llu.get_last_structured_llm_call_metadata()

        self.assertEqual("deepseek", meta["provider"])
        self.assertEqual("deepseek-chat", meta["effective_model"])
        self.assertTrue(meta["fallback_used"])
        self.assertEqual(["deepseek-reasoner", "deepseek-chat"], meta["attempted_models"])

    def test_structured_llm_metadata_includes_azure_provider_meta(self):
        class _FakeResult:
            def __init__(self, output):
                self.output = output

        class _FakeAgent:
            def __init__(self, model_obj, **kwargs):
                self.model_obj = model_obj

            def run_sync(self, prompt, infer_name=False):
                return _FakeResult({"ok": True})

        with patch.object(llu, "Agent", _FakeAgent), \
             patch.object(llu, "_build_pydantic_ai_model", side_effect=lambda provider, api_key, model, **kwargs: model):
            llu.call_structured_llm_by_provider(
                provider="azure",
                api_key="test-key",
                model="azure-gpt4-mini",
                prompt="Return any valid object.",
                output_type=dict,
                provider_config={"azure_endpoint": "https://example-resource.openai.azure.com", "api_version": "2024-10-21"},
            )
            meta = llu.get_last_structured_llm_call_metadata()

        self.assertEqual("azure", meta["provider"])
        self.assertEqual("https://example-resource.openai.azure.com", meta["provider_meta"]["azure_endpoint"])
        self.assertEqual("2024-10-21", meta["provider_meta"]["api_version"])

    def test_structured_llm_raw_json_fallback_recovers_output_validation_failure(self):
        class _FallbackOutput(BaseModel):
            ok: bool = False
            value: str = ""

        class _FakeAgent:
            def __init__(self, model_obj, **kwargs):
                self.model_obj = model_obj

            def run_sync(self, prompt, infer_name=False):
                raise RuntimeError("Exceeded maximum retries (2) for output validation")

        with patch.object(llu, "Agent", _FakeAgent), \
             patch.object(llu, "_build_pydantic_ai_model", side_effect=lambda provider, api_key, model, **kwargs: model), \
             patch.object(llu, "call_llm_by_provider", return_value='{"ok":true,"value":"recovered"}'):
            result = llu.call_structured_llm_by_provider(
                provider="azure",
                api_key="test-key",
                model="azure-gpt4-mini",
                prompt="Return a valid object.",
                output_type=_FallbackOutput,
                output_retries=0,
                provider_config={"azure_endpoint": "https://example-resource.openai.azure.com", "api_version": "2024-10-21"},
            )
            meta = llu.get_last_structured_llm_call_metadata()

        self.assertIsNotNone(result)
        self.assertTrue(result.ok)
        self.assertEqual("recovered", result.value)
        self.assertTrue(meta["ok"])
        self.assertTrue(meta["raw_json_fallback_used"])
        self.assertEqual("azure-gpt4-mini", meta["effective_model"])

    def test_phase6_hierarchy_llm_config_defaults_to_deepseek_reasoner(self):
        with patch.object(gns, "GRAPH_NEXUS_HIERARCHY_LLM_PROVIDER", ""), \
             patch.object(gns, "GRAPH_NEXUS_HIERARCHY_LLM_MODEL", ""), \
             patch.object(gns, "GRAPH_NEXUS_HIERARCHY_LLM_API_KEY", ""), \
             patch.dict(os.environ, {"DEEPSEEK_API_KEY": "d-key"}, clear=False):
            provider, model, api_key = gns._hierarchy_llm_config()
        self.assertEqual("deepseek", provider)
        self.assertEqual("deepseek-reasoner", model)
        self.assertEqual("d-key", api_key)

    def test_graph_nexus_llm_config_prefers_provider_specific_api_key(self):
        with patch.object(gns, "GRAPH_NEXUS_LLM_PROVIDER", "deepseek"), \
             patch.object(gns, "GRAPH_NEXUS_LLM_MODEL", "deepseek-reasoner"), \
             patch.object(gns, "GRAPH_NEXUS_LLM_API_KEY", "AIza-misconfigured"), \
             patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-correct"}, clear=False):
            provider, model, api_key = gns._graph_nexus_llm_config()
        self.assertEqual("deepseek", provider)
        self.assertEqual("deepseek-reasoner", model)
        self.assertEqual("sk-correct", api_key)

    def test_private_entity_hierarchy_llm_config_defaults_to_deepseek_reasoner(self):
        with patch.dict(os.environ, {
            "GRAPH_NEXUS_HIERARCHY_LLM_PROVIDER": "",
            "GRAPH_NEXUS_HIERARCHY_LLM_MODEL": "",
            "GRAPH_NEXUS_HIERARCHY_LLM_API_KEY": "",
            "DEEPSEEK_API_KEY": "d-key",
        }, clear=False):
            provider, model, api_key = gna._hierarchy_llm_config()
        self.assertEqual("deepseek", provider)
        self.assertEqual("deepseek-reasoner", model)
        self.assertEqual("d-key", api_key)

    def test_structured_model_name_falls_back_from_old_gemini_flash_exp(self):
        with patch.dict(os.environ, {"GEMINI_STRUCTURED_MODEL": "gemini-3-flash-preview"}, clear=False):
            self.assertEqual("gemini-3-flash-preview", llu._structured_model_name("gemini", "gemini-2.0-flash-exp"))

    def test_structured_gemini_settings_disable_thinking_and_enforce_min_tokens(self):
        settings = llu._build_structured_model_settings("gemini", 64, 30.0, 0.2)
        dumped = settings if isinstance(settings, dict) else settings.model_dump()
        self.assertEqual(256, dumped["max_tokens"])
        self.assertEqual({"thinking_budget": 0, "include_thoughts": False}, dumped["google_thinking_config"])

    def test_enhanced_sentiment_uses_structured_llm_payload(self):
        payload = gna._EnhancedSentimentResponse(
            sentiment=[
                gna._TickerSentimentRecord(t="AAPL", s=1, e="product", sp=1.0),
                gna._TickerSentimentRecord(t="MSFT", s=-1, e="unknown", sp=0.5),
            ],
            future=[gna._FutureTradeRecord(t="NVDA", d="2026-03-15", s=1, r="earnings date")],
            cancel=[gna._CancelTradeRecord(t="TSLA", d="2026-03-20", r="thesis broken")],
            trends=gna._TrendUpdatePayload(
                new=[gna._TrendNewRecord(id="ai_spend", name="AI Spend", desc="AI capex rising", dir="bullish", str=0.8)]
            ),
        )
        with patch.object(gna, "call_structured_llm_by_provider", return_value=payload):
            sentiment, future, cancel, trends = gna._enhanced_sentiment_from_llm(
                [{"headline": "Apple launches a new flagship device"}],
                "gemini",
                "key",
                "gemini-3-flash-preview",
            )
        self.assertEqual(1, sentiment["AAPL"]["sentiment"])
        self.assertEqual("product", sentiment["AAPL"]["event"])
        self.assertEqual("general", sentiment["MSFT"]["event"])
        self.assertEqual(0.5, sentiment["MSFT"]["sell_pct"])
        self.assertEqual([{"ticker": "NVDA", "date": "2026-03-15", "signal": 1, "reason": "earnings date"}], future)
        self.assertEqual([{"ticker": "TSLA", "date": "2026-03-20", "reason": "thesis broken"}], cancel)
        self.assertEqual(1, len(trends["new"]))

    def test_macro_classification_uses_structured_llm_payload(self):
        payload = gna._MacroSignalsResponse(
            macro_signals=[
                gna._MacroSignalRecord(
                    affected_sectors=["Energy", "Invalid"],
                    event_type="commodity",
                    sentiment=-1,
                    strength=0.7,
                    reason="Oil shock",
                    direct_tickers=["xom", "cvx"],
                    neo4j_filters=gna._MacroFiltersResponse(commodity="oil"),
                )
            ]
        )
        with patch.object(gna, "call_structured_llm_by_provider", return_value=payload):
            signals = gna._classify_macro_news_via_llm(
                [{"headline": "Oil prices surge after supply shock"}],
                "deepseek",
                "key",
                "deepseek-chat",
                "2026-03-09",
                ["Energy", "Industrials"],
                ["Department of Energy"],
                num_articles=1,
            )
        self.assertEqual(1, len(signals))
        self.assertEqual(["Energy"], signals[0]["affected_sectors"])
        self.assertEqual(["XOM", "CVX"], signals[0]["direct_tickers"])
        self.assertEqual("oil", signals[0]["neo4j_filters"]["commodity"])

    def test_nexus_role_llm_config_prefers_role_specific_override(self):
        cfg = {
            "llm_provider": "gemini",
            "llm_api_key": "global-key",
            "llm_model": "gemini-3-flash-preview",
            "company_article_llm_provider": "openai",
            "company_article_llm_api_key": "sk-role",
            "company_article_llm_model": "gpt-4.1-mini",
        }
        provider, api_key, model, prompt_version = gna._resolve_role_llm_config(cfg, "company_article")
        self.assertEqual("openai", provider)
        self.assertEqual("sk-role", api_key)
        self.assertEqual("gpt-4.1-mini", model)
        self.assertEqual(gna._NEXUS_COMPANY_PROMPT_VERSION, prompt_version)

    def test_nexus_role_llm_provider_config_supports_azure_endpoint_and_version(self):
        cfg = {
            "llm_provider": "azure",
            "llm_api_key": "azure-key",
            "llm_model": "azure-gpt4-mini",
            "azure_openai_endpoint": "https://example-resource.openai.azure.com",
            "azure_openai_api_version": "2024-10-21",
        }
        provider, api_key, model, _ = gna._resolve_role_llm_config(cfg, "")
        provider_cfg = gna._resolve_role_llm_provider_config(cfg, "")
        self.assertEqual("azure", provider)
        self.assertEqual("azure-key", api_key)
        self.assertEqual("azure-gpt4-mini", model)
        self.assertEqual("https://example-resource.openai.azure.com", provider_cfg["azure_endpoint"])
        self.assertEqual("2024-10-21", provider_cfg["api_version"])

    def test_nexus_role_llm_provider_config_supports_openai_base_url(self):
        cfg = {
            "llm_provider": "openai",
            "llm_api_key": "openai-key",
            "llm_model": "gpt-4.1-mini",
            "openai_base_url": "https://openrouter.ai/api/v1",
        }
        provider, api_key, model, _ = gna._resolve_role_llm_config(cfg, "")
        provider_cfg = gna._resolve_role_llm_provider_config(cfg, "")
        self.assertEqual("openai", provider)
        self.assertEqual("openai-key", api_key)
        self.assertEqual("gpt-4.1-mini", model)
        self.assertEqual("https://openrouter.ai/api/v1", provider_cfg["base_url"])

    def test_nexus_role_llm_provider_config_supports_reasoning_effort(self):
        cfg = {
            "llm_provider": "azure",
            "llm_api_key": "azure-key",
            "llm_model": "gpt-oss-120B",
            "llm_reasoning_effort": "medium",
            "company_article_llm_provider": "azure",
            "company_article_llm_model": "gpt-oss-120B",
            "company_article_llm_reasoning_effort": "high",
            "azure_openai_endpoint": "https://example-resource.openai.azure.com",
            "azure_openai_api_version": "2024-10-21",
        }
        provider_cfg = gna._resolve_role_llm_provider_config(cfg, "company_article")
        self.assertEqual("high", provider_cfg["reasoning_effort"])
        self.assertEqual("gpt-oss-120B-HIGH", gna._llm_model_ref("gpt-oss-120B", provider_cfg))

    def test_build_llm_trace_stamps_effective_model_with_reasoning_effort(self):
        meta = {
            "provider": "azure",
            "requested_model": "gpt-oss-120B",
            "effective_model": "gpt-oss-120B",
            "provider_meta": {"azure_endpoint": "https://example", "api_version": "2024-10-21", "reasoning_effort": "high"},
            "fallback_used": False,
            "raw_json_fallback_used": False,
            "ok": True,
            "error": "",
            "usage": {},
        }
        with patch.object(gna, "get_last_structured_llm_call_metadata", return_value=meta):
            trace = gna._build_llm_trace("company_article", "azure", "gpt-oss-120B", "prompt", "system", "v1", True)
        self.assertEqual("gpt-oss-120B-HIGH", trace["requested_model"])
        self.assertEqual("gpt-oss-120B-HIGH", trace["effective_model"])

    def test_nexus_company_article_classification_uses_structured_payload(self):
        payload = gna._CompanyArticleClassificationResponse(
            classifications=[
                gna._CompanyArticleTickerClassification(
                    ticker="AAPL",
                    event_type="product",
                    impact_direction="bullish",
                    impact_strength=0.8,
                    is_forward_looking=True,
                    expected_horizon_days=14,
                    relevance_score=0.9,
                    predicted_outcome_direction="bullish",
                    predicted_confidence=0.88,
                    company_role_in_article="subject",
                    reason="New product cycle",
                )
            ]
        )
        article = {
            "article_hash": "hash-1",
            "source": "alpaca",
            "published_at": "2026-03-14T14:00:00Z",
            "date_key": "2026-03-14",
            "headline": "Apple launches a new flagship product",
            "summary": "Analysts expect strong demand.",
            "symbols": ["AAPL"],
        }
        with patch.object(gna, "call_structured_llm_by_provider", return_value=payload), \
             patch.object(gna, "get_last_structured_llm_call_metadata", return_value={"provider": "openai", "effective_model": "gpt-4.1-mini", "ok": True, "usage": {"total_tokens": 111}}):
            rows, traces = gna._classify_company_article_records(
                [article],
                {
                    "company_article_llm_provider": "openai",
                    "company_article_llm_api_key": "sk-role",
                    "company_article_llm_model": "gpt-4.1-mini",
                },
                date_key="2026-03-14",
                conn=None,
                instance_id="test",
            )
        self.assertEqual(1, len(rows))
        self.assertEqual("AAPL", rows[0]["classifications"][0]["ticker"])
        self.assertEqual("bullish", rows[0]["classifications"][0]["impact_direction"])
        self.assertEqual(1, len(traces))
        self.assertEqual("company_article", traces[0]["role"])

    def test_nexus_prompt_payload_helper_compacts_and_stringifies_values(self):
        payload = {
            "when": datetime(2026, 3, 14, 12, 30, 0),
            "symbols": ["AAPL", "MSFT"],
        }
        rendered = gna._to_prompt_payload(payload, use_toon=False)
        self.assertIn("\"symbols\":[\"AAPL\",\"MSFT\"]", rendered)
        self.assertIn("2026-03-14 12:30:00", rendered)

    def test_nexus_company_article_prompt_uses_prompt_payload_helper(self):
        article = {
            "article_hash": "hash-2",
            "source": "alpaca",
            "published_at": "2026-03-14T15:00:00Z",
            "date_key": "2026-03-14",
            "headline": "Apple expands device lineup",
            "summary": "New hardware is expected to help growth.",
            "symbols": ["AAPL", "MSFT"],
        }
        payload = gna._CompanyArticleClassificationResponse(classifications=[])
        captured = {}

        def _fake_structured(provider, api_key, model, prompt, output_type, **kwargs):
            captured["prompt"] = prompt
            return payload

        with patch.object(gna, "_to_prompt_payload", return_value="TOON_PAYLOAD") as payload_mock, \
             patch.object(gna, "call_structured_llm_by_provider", side_effect=_fake_structured), \
             patch.object(gna, "get_last_structured_llm_call_metadata", return_value={"provider": "openai", "effective_model": "gpt-4.1-mini", "ok": True, "usage": {}}):
            gna._classify_company_article_records(
                [article],
                {
                    "company_article_llm_provider": "openai",
                    "company_article_llm_api_key": "sk-role",
                    "company_article_llm_model": "gpt-4.1-mini",
                    "use_toon_format": True,
                },
                date_key="2026-03-14",
                conn=None,
                instance_id="test",
            )
        payload_mock.assert_called_once()
        payload_args, payload_kwargs = payload_mock.call_args
        self.assertEqual(
            [{
                "article_hash": "hash-2",
                "headline": "Apple expands device lineup",
                "summary": "New hardware is expected to help growth.",
                "content_excerpt": "",
                "symbols": ["AAPL", "MSFT"],
            }],
            payload_args[0],
        )
        self.assertEqual({"use_toon": True}, payload_kwargs)
        self.assertIn("TOON_PAYLOAD", captured["prompt"])

    def test_nexus_enrich_articles_with_content_excerpt_reuses_cached_raw_excerpt(self):
        articles = [{
            "article_hash": "hash-1",
            "url": "https://example.com/story",
            "content_excerpt": "",
        }]
        with patch.object(gna, "_load_news_raw_cache_rows", return_value={"hash-1": {"content_excerpt": "Cached body text"}}), \
             patch.object(gna, "_fetch_article_content_excerpt") as fetch_mock:
            enriched = gna._enrich_articles_with_content_excerpt(
                object(),
                articles,
                {"alpaca_article_excerpt_chars": 200},
                source="alpaca",
            )
        self.assertEqual("Cached body text", enriched[0]["content_excerpt"])
        fetch_mock.assert_not_called()

    def test_nexus_enrich_articles_with_content_excerpt_fetches_missing_excerpt(self):
        articles = [{
            "article_hash": "hash-2",
            "url": "https://example.com/story-2",
            "content_excerpt": "",
        }]
        with patch.object(gna, "_load_news_raw_cache_rows", return_value={}), \
             patch.object(gna, "_fetch_article_content_excerpt", return_value="Fetched article excerpt"):
            enriched = gna._enrich_articles_with_content_excerpt(
                object(),
                articles,
                {
                    "alpaca_article_excerpt_chars": 200,
                    "alpaca_article_fetch_workers": 1,
                },
                source="alpaca",
            )
        self.assertEqual("Fetched article excerpt", enriched[0]["content_excerpt"])

    def test_filter_low_signal_alpaca_articles_drops_evergreen_return_headlines(self):
        articles = [
            {"headline": "If You Invested $1000 In Welltower Stock 20 Years Ago, You Would Have This Much Today"},
            {"headline": "$1000 Invested In Progressive 10 Years Ago Would Be Worth This Much Today"},
            {"headline": "If You Invested $1000 In This Stock 15 Years Ago, You Would Have This Much Today"},
            {"headline": "Price Over Earnings Overview: Safe Bulkers"},
            {"headline": "Alphabet Stocks Hits New Highs As Meta Mulls Deploying Google AI Chips In Data Centers"},
        ]
        filtered = gna._filter_low_signal_alpaca_articles(articles, date_key="2025-11-24")
        self.assertEqual(1, len(filtered))
        self.assertIn("Alphabet Stocks Hits New Highs", filtered[0]["headline"])

    def test_fetch_articles_cached_discards_low_signal_articles_before_cache_save(self):
        low_signal = [
            {"headline": "If You Invested $1000 In This Stock 15 Years Ago, You Would Have This Much Today"}
        ]
        with patch.object(gna, "_get_nexus_db_conn", return_value=object()), \
             patch.object(gna, "_ensure_nexus_cache_table"), \
             patch.object(gna, "_get_cached_articles", return_value=(None, None)), \
             patch.object(gna, "_fetch_alpaca_news_all", return_value=low_signal), \
             patch.object(gna, "_save_cached_articles") as save_mock:
            articles, from_cache, cached_sentiment = gna._fetch_articles_cached(
                "2025-11-24",
                datetime(2025, 11, 24),
                datetime(2025, 11, 25),
                "key",
                "secret",
                50,
                1,
            )
        self.assertEqual([], articles)
        self.assertFalse(from_cache)
        self.assertIsNone(cached_sentiment)
        save_mock.assert_not_called()

    def test_fetch_alpaca_news_all_calls_provider_and_returns_news_rows(self):
        class _Resp:
            status_code = 200
            text = "ok"

            def json(self):
                return {
                    "news": [
                        {
                            "headline": "Market Wrap: Fed Weighs Next Move",
                            "symbols": ["SPY"],
                        }
                    ]
                }

            def raise_for_status(self):
                return None

        with patch("requests.get", return_value=_Resp()) as get_mock:
            rows = gna._fetch_alpaca_news_all(
                datetime(2025, 11, 10, tzinfo=timezone.utc),
                datetime(2025, 11, 10, 23, 59, 59, tzinfo=timezone.utc),
                "key",
                "secret",
                limit=10,
            )
        self.assertEqual(1, len(rows))
        self.assertEqual("Market Wrap: Fed Weighs Next Move", rows[0]["headline"])
        get_mock.assert_called_once()

    def test_fetch_google_news_cached_sanitizes_low_signal_cache_entries(self):
        fake_module = ModuleType("google_news")
        cached_articles = [
            {"headline": "If You Invested $1000 In This Stock 15 Years Ago, You Would Have This Much Today", "url": "https://example.com/bad"},
            {"headline": "Fed Signals Cautious Rate Path Into Year-End", "url": "https://example.com/good"},
        ]
        cache_calls = []

        fake_module.DEFAULT_KEYWORDS = ["fed"]
        fake_module.DEFAULT_TOPICS = ["BUSINESS"]
        fake_module.compute_keywords_hash = lambda keywords, topics: "hash123"
        fake_module.load_cached_articles = lambda conn, date_key, kw_hash: list(cached_articles)
        fake_module.cache_articles = lambda conn, date_key, articles, kw_hash: cache_calls.append(list(articles))
        fake_module.fetch_google_news = lambda **kwargs: []
        fake_module.fetch_google_news_by_topic = lambda **kwargs: []

        original = sys.modules.get("google_news")
        sys.modules["google_news"] = fake_module
        try:
            with patch.object(gna, "_get_nexus_db_conn", return_value=object()):
                articles = gna._fetch_google_news_cached(
                    "2025-11-24",
                    datetime(2025, 11, 24),
                    datetime(2025, 11, 25),
                    {"google_news_enabled": True},
                )
        finally:
            if original is not None:
                sys.modules["google_news"] = original
            else:
                del sys.modules["google_news"]

        self.assertEqual(1, len(articles))
        self.assertIn("Fed Signals", articles[0]["headline"])
        self.assertEqual(1, len(cache_calls))
        self.assertEqual(1, len(cache_calls[0]))

    def test_run_once_with_zero_alpaca_articles_still_checks_google_news(self):
        strategy = gna.GraphNexusAnalysis()
        with patch.object(gna, "_resolve_role_llm_config", return_value=("azure", "key", "model", None)), \
             patch.object(gna, "_resolve_role_llm_provider_config", return_value={}), \
             patch.object(gna, "_resolve_neo4j_runtime_config", return_value=("bolt://neo4j:7687", "neo4j", "pw")), \
             patch.object(gna, "_fetch_articles_cached", return_value=([], False, None)), \
             patch.object(gna, "_get_nexus_db_conn", return_value=None), \
             patch.object(gna, "_mentioned_tickers_from_articles", return_value=set()), \
             patch.object(gna, "_enhanced_sentiment_from_llm", return_value=({}, [], [], {})), \
             patch.object(gna, "_fetch_google_news_cached", return_value=[]) as google_mock:
            result = strategy.run_once(
                ["SPY"],
                {"SPY": 670.92},
                datetime(2025, 11, 10, 13, 0, 0),
                {
                    "google_news_enabled": True,
                    "min_articles": 15,
                    "max_daily_google_news_articles": 50,
                    "max_daily_alpaca_articles": 50,
                    "use_llm_sentiment": True,
                },
                {},
            )
        self.assertEqual({"SPY": 0}, result)
        google_mock.assert_called_once()

    def test_article_prompt_entry_trims_company_payload_and_caps_symbols(self):
        row = {
            "article_hash": "hash-1",
            "headline": "H" * 260,
            "summary": "S" * 240,
            "content_excerpt": "E" * 240,
            "symbols": ["AAPL", "MSFT", "NVDA", "AMD", "GOOGL", "META", "AMZN", "TSLA"],
        }
        payload = gna._article_prompt_entry(row, include_symbols=True, prompt_role="company")
        self.assertLessEqual(len(payload["headline"]), 180)
        self.assertLessEqual(len(payload["summary"]), 140)
        self.assertLessEqual(len(payload["symbols"]), 6)
        self.assertEqual("", payload["content_excerpt"])

    def test_to_prompt_payload_uses_toon_like_fallback_when_encoder_unavailable(self):
        payload = [
            {"ref": "a1", "headline": "Gap earnings beat", "symbols": ["GAP"]},
            {"ref": "a2", "headline": "Adobe AI demand rises", "symbols": ["ADBE"]},
        ]
        with patch.object(gna, "_TOON_AVAILABLE", False), \
             patch.object(gna, "_TOON_IMPORT_ATTEMPTED", True), \
             patch.object(gna, "_toon_encode_fn", None):
            rendered = gna._to_prompt_payload(payload, use_toon=True)
        self.assertIn("[2]{ref,headline,symbols}:", rendered)
        self.assertNotIn('"headline":', rendered)
        self.assertNotIn('{"ref"', rendered)

    def test_chunk_rows_by_prompt_budget_splits_large_company_batches(self):
        rows = []
        for idx in range(6):
            rows.append((
                {
                    "article_hash": f"hash-{idx}",
                    "headline": "Headline " + ("X" * 120),
                    "summary": "Summary " + ("Y" * 120),
                    "content_excerpt": "Excerpt " + ("Z" * 180),
                    "symbols": ["AAPL"],
                },
                f"cid-{idx}",
            ))
        chunks = gna._chunk_rows_by_prompt_budget(
            rows,
            max_items=8,
            max_prompt_chars=600,
            use_toon=True,
            entry_builder=lambda row: gna._article_prompt_entry(row, include_symbols=True, prompt_role="company"),
        )
        self.assertGreater(len(chunks), 1)
        self.assertEqual(6, sum(len(chunk) for chunk in chunks))

    def test_select_trend_supporting_articles_only_keeps_relevant_headlines(self):
        articles = [
            {
                "headline": "Steph Curry Wears Nike Shoes After Under Armour Split: Will Apparel Giant Help NBA Star's 'New Beginnings?'",
                "summary": "",
                "symbols": ["NKE"],
                "source": "alpaca",
            },
            {
                "headline": "Dynagas LNG signs long-term supply agreement with Asian utility buyer",
                "summary": "The LNG carrier operator secured a multi-year contract in Asia.",
                "symbols": ["DLNG"],
                "source": "alpaca",
            },
            {
                "headline": "Global LNG demand rises as Asian buyers secure longer-term contracts",
                "summary": "Energy buyers seek supply certainty.",
                "symbols": [],
                "source": "alpaca",
            },
        ]
        selected = gna._select_trend_supporting_articles(
            articles,
            date_key="2025-11-17",
            trend_name="LNG Long-Term Contracting",
            trend_desc="Energy companies securing multi-decade LNG supply agreements with Asian buyers",
            trend_tickers=["DLNG"],
            trend_sectors=["Energy", "Utilities"],
            max_items=3,
        )
        headlines = [item["headline"] for item in selected]
        self.assertEqual(2, len(selected))
        self.assertTrue(any("Dynagas LNG signs long-term supply agreement" in headline for headline in headlines))
        self.assertTrue(any("Global LNG demand rises" in headline for headline in headlines))
        self.assertFalse(any("Steph Curry Wears Nike Shoes" in headline for headline in headlines))

    def test_sanitize_trend_supporting_articles_drops_irrelevant_existing_rows(self):
        entries = [
            {"date": "2025-11-17", "headline": "Steph Curry Wears Nike Shoes After Under Armour Split", "source": "alpaca"},
            {"date": "2025-11-17", "headline": "Dynagas LNG signs long-term supply agreement with Asian utility buyer", "source": "alpaca"},
            {"date": "2025-11-17", "headline": "If You Invested $1000 In Welltower Stock 20 Years Ago, You Would Have This Much Today", "source": "alpaca"},
        ]
        sanitized = gna._sanitize_trend_supporting_articles(
            entries,
            trend_name="LNG Long-Term Contracting",
            trend_desc="Energy companies securing multi-decade LNG supply agreements with Asian buyers",
            trend_tickers=["DLNG"],
            trend_sectors=["Energy", "Utilities"],
        )
        self.assertEqual(1, len(sanitized))
        self.assertIn("Dynagas LNG signs long-term supply agreement", sanitized[0]["headline"])

    def test_nexus_company_article_batch_classification_maps_results_by_article_hash(self):
        payload = gna._CompanyArticleBatchResponse(
            articles=[
                gna._CompanyArticleBatchResult(
                    article_hash="hash-a",
                    classifications=[
                        gna._CompanyArticleTickerClassification(
                            ticker="AAPL",
                            event_type="product",
                            impact_direction="bullish",
                            impact_strength=0.8,
                            relevance_score=0.9,
                            predicted_outcome_direction="bullish",
                            predicted_confidence=0.87,
                            reason="Strong product cycle",
                        )
                    ],
                ),
                gna._CompanyArticleBatchResult(
                    article_hash="hash-b",
                    classifications=[
                        gna._CompanyArticleTickerClassification(
                            ticker="MSFT",
                            event_type="contract",
                            impact_direction="bullish",
                            impact_strength=0.6,
                            relevance_score=0.75,
                            predicted_outcome_direction="bullish",
                            predicted_confidence=0.8,
                            reason="Government contract tailwind",
                        )
                    ],
                ),
            ]
        )
        articles = [
            {
                "article_hash": "hash-a",
                "source": "alpaca",
                "published_at": "2026-03-14T10:00:00Z",
                "date_key": "2026-03-14",
                "headline": "Apple launches enterprise device bundle",
                "summary": "Large customers are showing interest.",
                "symbols": ["AAPL"],
            },
            {
                "article_hash": "hash-b",
                "source": "alpaca",
                "published_at": "2026-03-14T10:05:00Z",
                "date_key": "2026-03-14",
                "headline": "Microsoft wins new defense cloud contract",
                "summary": "A multi-year expansion is expected.",
                "symbols": ["MSFT"],
            },
        ]
        with patch.object(gna, "call_structured_llm_by_provider", return_value=payload) as llm_mock, \
             patch.object(gna, "get_last_structured_llm_call_metadata", return_value={"provider": "openai", "effective_model": "gpt-4.1-mini", "ok": True, "usage": {"total_tokens": 222}}):
            rows, traces = gna._classify_company_article_records(
                articles,
                {
                    "company_article_llm_provider": "openai",
                    "company_article_llm_api_key": "sk-role",
                    "company_article_llm_model": "gpt-4.1-mini",
                    "company_article_llm_batch_size": 8,
                },
                date_key="2026-03-14",
                conn=None,
                instance_id="test",
            )
        self.assertEqual(1, llm_mock.call_count)
        self.assertEqual(1, len(traces))
        self.assertEqual({"hash-a", "hash-b"}, {row["article_hash"] for row in rows})
        row_map = {row["article_hash"]: row for row in rows}
        self.assertEqual("AAPL", row_map["hash-a"]["classifications"][0]["ticker"])
        self.assertEqual("MSFT", row_map["hash-b"]["classifications"][0]["ticker"])

    def test_nexus_company_article_azure_uses_zero_output_retries(self):
        payload = gna._CompanyArticleBatchResponse(
            articles=[
                gna._CompanyArticleBatchResult(
                    article_hash="hash-a",
                    classifications=[
                        gna._CompanyArticleTickerClassification(
                            ticker="AAPL",
                            impact_direction="bullish",
                            reason="ok",
                        )
                    ],
                )
            ]
        )
        articles = [
            {
                "article_hash": "hash-a",
                "source": "alpaca",
                "published_at": "2026-03-14T10:00:00Z",
                "date_key": "2026-03-14",
                "headline": "Apple launches enterprise device bundle",
                "summary": "Large customers are showing interest.",
                "symbols": ["AAPL"],
            }
        ]
        captured_kwargs = []

        def _fake_structured(*args, **kwargs):
            captured_kwargs.append(dict(kwargs))
            return payload

        with patch.object(gna, "call_structured_llm_by_provider", side_effect=_fake_structured), \
             patch.object(gna, "get_last_structured_llm_call_metadata", return_value={"provider": "azure", "effective_model": "Kimi-K2.5", "ok": True, "usage": {"total_tokens": 123}, "raw_json_fallback_used": False}):
            rows, _ = gna._classify_company_article_records(
                articles,
                {
                    "company_article_llm_provider": "azure",
                    "company_article_llm_api_key": "azure-key",
                    "company_article_llm_model": "Kimi-K2.5",
                    "company_article_llm_batch_size": 8,
                    "company_article_azure_openai_endpoint": "https://example-resource.services.ai.azure.com",
                    "company_article_azure_openai_api_version": "2024-10-21",
                },
                date_key="2026-03-14",
                conn=None,
                instance_id="test",
            )

        self.assertEqual(1, len(rows))
        self.assertTrue(captured_kwargs)
        self.assertEqual(0, captured_kwargs[0]["output_retries"])

    def test_nexus_company_article_batch_prompt_uses_short_refs_not_full_hashes(self):
        payload = gna._CompanyArticleBatchResponse(
            articles=[
                gna._CompanyArticleBatchResult(
                    ref="a1",
                    classifications=[
                        gna._CompanyArticleTickerClassification(
                            ticker="AAPL",
                            impact_direction="bullish",
                            reason="ok",
                        )
                    ],
                )
            ]
        )
        articles = [
            {
                "article_hash": "7628afc66fff33fe15f826ea32a7668841abc49473e5cf53131ec96e040bc6b8",
                "source": "alpaca",
                "published_at": "2026-03-14T10:00:00Z",
                "date_key": "2026-03-14",
                "headline": "Gap Stock Steps Up After Q3 Earnings Beat Estimates: Details",
                "summary": "Here's a look at the Q3 earnings report from Gap.",
                "content_excerpt": "Gap Stock Steps Up After...",
                "symbols": ["AAPL"],
            }
        ]
        captured_prompts = []

        def _fake_structured(provider, api_key, model, prompt, output_type, **kwargs):
            captured_prompts.append(prompt)
            return payload

        with patch.object(gna, "call_structured_llm_by_provider", side_effect=_fake_structured), \
             patch.object(gna, "get_last_structured_llm_call_metadata", return_value={"provider": "azure", "effective_model": "Kimi-K2.5", "ok": True, "usage": {"total_tokens": 123}, "raw_json_fallback_used": False}):
            rows, _ = gna._classify_company_article_records(
                articles,
                {
                    "company_article_llm_provider": "azure",
                    "company_article_llm_api_key": "azure-key",
                    "company_article_llm_model": "Kimi-K2.5",
                    "company_article_llm_batch_size": 8,
                    "company_article_azure_openai_endpoint": "https://example-resource.services.ai.azure.com",
                    "company_article_azure_openai_api_version": "2024-10-21",
                },
                date_key="2026-03-14",
                conn=None,
                instance_id="test",
            )

        self.assertEqual(1, len(rows))
        self.assertTrue(captured_prompts)
        self.assertIn("|a1|", captured_prompts[0])
        self.assertNotIn("7628afc66fff33fe15f826ea32a7668841abc49473e5cf53131ec96e040bc6b8", captured_prompts[0])
        self.assertEqual("7628afc66fff33fe15f826ea32a7668841abc49473e5cf53131ec96e040bc6b8", rows[0]["article_hash"])

    def test_nexus_macro_article_batch_classification_maps_results_by_article_hash(self):
        payload = gna._MacroArticleBatchResponse(
            articles=[
                gna._MacroArticleBatchResult(
                    article_hash="macro-a",
                    route="macro",
                    macro_signal_type="government",
                    government_action_type="war_declared",
                    affected_sectors=["Defense", "Energy"],
                    affected_themes=["Defense Spending"],
                    impact_direction="bullish",
                    impact_strength=0.9,
                    expected_horizon_days=30,
                    possible_direct_companies=["LMT", "XOM"],
                    reason="Conflict escalation raises defense and energy demand",
                ),
                gna._MacroArticleBatchResult(
                    article_hash="macro-b",
                    route="company_focused",
                    macro_signal_type="regulation",
                    government_action_type="regulation_or_rulemaking",
                    affected_sectors=["Technology"],
                    impact_direction="bearish",
                    impact_strength=0.5,
                    expected_horizon_days=14,
                    possible_direct_companies=["GOOGL"],
                    reason="Rulemaking pressures large platforms",
                ),
            ]
        )
        articles = [
            {
                "article_hash": "macro-a",
                "source": "google_news",
                "published_at": "2026-03-14T12:00:00Z",
                "date_key": "2026-03-14",
                "headline": "US declares war after regional escalation",
                "summary": "Officials expect the conflict to extend into the next quarter.",
            },
            {
                "article_hash": "macro-b",
                "source": "google_news",
                "published_at": "2026-03-14T12:05:00Z",
                "date_key": "2026-03-14",
                "headline": "New antitrust rules target large tech platforms",
                "summary": "Regulators signaled a broader compliance push.",
            },
        ]
        with patch.object(gna, "call_structured_llm_by_provider", return_value=payload) as llm_mock, \
             patch.object(gna, "get_last_structured_llm_call_metadata", return_value={"provider": "openai", "effective_model": "gpt-4.1-mini", "ok": True, "usage": {"total_tokens": 333}}):
            rows, traces = gna._classify_macro_article_records(
                articles,
                {
                    "macro_article_llm_provider": "openai",
                    "macro_article_llm_api_key": "sk-role",
                    "macro_article_llm_model": "gpt-4.1-mini",
                    "macro_article_llm_batch_size": 10,
                },
                date_key="2026-03-14",
                conn=None,
                instance_id="test",
            )
        self.assertEqual(1, llm_mock.call_count)
        self.assertEqual(1, len(traces))
        row_map = {row["article_hash"]: row for row in rows}
        self.assertEqual("war_declared", row_map["macro-a"]["government_action_type"])
        self.assertEqual(["LMT", "XOM"], row_map["macro-a"]["possible_direct_companies"])
        self.assertEqual("company_focused", row_map["macro-b"]["route"])
        self.assertEqual("regulation_or_rulemaking", row_map["macro-b"]["government_action_type"])

    def test_nexus_macro_article_normalizes_monetary_policy_government_fields(self):
        payload = gna._MacroArticleBatchResponse(
            articles=[
                gna._MacroArticleBatchResult(
                    article_hash="macro-fed",
                    route="macro",
                    macro_signal_type="monetary_policy",
                    government_action_type="none",
                    acting_government_body="",
                    affected_sectors=[],
                    affected_themes=[],
                    affected_commodities=[],
                    affected_agencies=[],
                    impact_direction="positive",
                    impact_strength=0.6,
                    expected_horizon_days=30,
                    trend_tags=["fed_rate_cuts", "monetary_policy_easing", "market_rally"],
                    possible_direct_companies=[],
                    reason="Market pricing in December Fed rate cut expectations - significant monetary policy signal affecting broad markets",
                ),
            ]
        )
        articles = [
            {
                "article_hash": "macro-fed",
                "source": "google_news",
                "published_at": "2025-11-26T08:00:00+00:00",
                "date_key": "2025-11-26",
                "headline": "Fed rate cut expectations build ahead of December meeting",
                "summary": "Traders increasingly expect the Federal Reserve to cut rates next month.",
            },
        ]
        with patch.object(gna, "call_structured_llm_by_provider", return_value=payload), \
             patch.object(gna, "get_last_structured_llm_call_metadata", return_value={"provider": "azure", "effective_model": "Kimi-K2.5", "ok": True, "usage": {"total_tokens": 333}}):
            rows, _traces = gna._classify_macro_article_records(
                articles,
                {
                    "macro_article_llm_provider": "azure",
                    "macro_article_llm_api_key": "azure-key",
                    "macro_article_llm_model": "Kimi-K2.5",
                    "macro_article_llm_batch_size": 4,
                },
                date_key="2025-11-26",
                conn=None,
                instance_id="test",
            )
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("monetary_policy", row["macro_signal_type"])
        self.assertEqual("interest_rate_cut", row["government_action_type"])
        self.assertEqual("Federal Reserve", row["acting_government_body"])
        self.assertIn("Federal Reserve", row["affected_agencies"])
        self.assertEqual("bullish", row["impact_direction"])
        self.assertTrue(row["affected_themes"])
        self.assertTrue(row["affected_sectors"])

    def test_nexus_active_event_history_scope_ignores_instance_specific_fields(self):
        cfg_a = {
            "instance_id": "run-a",
            "llm_api_key": "secret-a",
            "macro_article_llm_provider": "openai",
            "macro_article_llm_model": "gpt-4.1-mini",
            "event_maintenance_llm_provider": "openai",
            "event_maintenance_llm_model": "gpt-4.1-mini",
        }
        cfg_b = {
            "instance_id": "run-b",
            "llm_api_key": "secret-b",
            "macro_article_llm_provider": "openai",
            "macro_article_llm_model": "gpt-4.1-mini",
            "event_maintenance_llm_provider": "openai",
            "event_maintenance_llm_model": "gpt-4.1-mini",
        }
        self.assertEqual(
            gna._active_event_history_scope_id(cfg_a),
            gna._active_event_history_scope_id(cfg_b),
        )

    def test_nexus_active_events_reconstruct_as_of_date_without_future_leakage(self):
        docs = [
            {"id": "1", "instance_id": "inst-a", "history_scope_id": "scope-a", "event_cluster_key": "war", "effective_date": "2026-03-10", "status": "live"},
            {"id": "2", "instance_id": "inst-b", "history_scope_id": "scope-a", "event_cluster_key": "war", "effective_date": "2026-05-05", "status": "ended"},
            {"id": "3", "instance_id": "inst-c", "history_scope_id": "scope-a", "event_cluster_key": "rates", "effective_date": "2026-03-12", "status": "live"},
            {"id": "4", "instance_id": "inst-z", "history_scope_id": "scope-b", "event_cluster_key": "tariffs", "effective_date": "2026-03-11", "status": "live"},
        ]

        class _FakeQuery:
            def __init__(self, payload):
                self.payload = payload

            def run(self, conn):
                return list(self.payload)

        class _FakeTable:
            def __init__(self, payload):
                self.payload = payload

            def filter(self, *args, **kwargs):
                filtered = [
                    doc for doc in self.payload
                    if str(doc.get("history_scope_id") or "") == "scope-a"
                    and str(doc.get("effective_date") or "") <= "2026-03-14"
                ]
                return _FakeQuery(filtered)

        class _FakeDB:
            def __init__(self, payload):
                self.payload = payload

            def table(self, name):
                return _FakeTable(self.payload)

        class _FakeR:
            def __init__(self, payload):
                self.payload = payload

            def db(self, name):
                return _FakeDB(self.payload)

        with patch.object(gna, "_ensure_nexus_history_table"), \
             patch.object(gna, "_r", _FakeR(docs)):
            live = gna._load_active_events_as_of(object(), "new-inst", "2026-03-14", history_scope_id="scope-a")
        self.assertEqual({"war", "rates"}, {row["event_cluster_key"] for row in live})

    def test_maintain_active_events_reuses_date_scoped_cache_without_llm_call(self):
        config = {
            "macro_article_llm_provider": "openai",
            "macro_article_llm_model": "gpt-4.1-mini",
            "event_maintenance_llm_provider": "openai",
            "event_maintenance_llm_api_key": "sk-role",
            "event_maintenance_llm_model": "gpt-4.1-mini",
        }
        current_events = [{
            "event_cluster_key": "war",
            "event_name": "War",
            "event_type": "government",
            "government_action_type": "war_declared",
            "status": "live",
            "start_date": "2026-03-10",
            "expected_end_date": "",
            "affected_sectors": ["Defense"],
        }]
        candidates = [{
            "event_cluster_key": "war",
            "event_name": "War",
            "event_type": "government",
            "government_action_type": "war_declared",
            "status": "live",
            "start_date": "2026-03-14",
            "expected_end_date": "",
            "affected_sectors": ["Defense"],
            "supporting_article_hashes": ["macro-a"],
        }]
        scope = gna._active_event_history_scope_id(config)
        cache_doc = {
            "id": gna._active_event_maintenance_doc_id(scope, "2026-03-14"),
            "history_scope_id": scope,
            "current_events_fingerprint": gna._active_event_records_fingerprint(current_events),
            "candidate_fingerprint": gna._active_event_records_fingerprint(candidates),
            "llm_provider": "openai",
            "llm_model": "gpt-4.1-mini",
            "llm_prompt_version": gna._NEXUS_EVENT_PROMPT_VERSION,
            "traces": [{"role": "event_maintenance", "prompt_hash": "abc123"}],
        }
        with patch.object(gna, "_load_active_events_as_of", side_effect=[current_events, current_events]) as load_mock, \
             patch.object(gna, "_derive_event_candidates_from_macro_rows", return_value=candidates), \
             patch.object(gna, "_load_active_event_maintenance_cache_doc", return_value=cache_doc), \
             patch.object(gna, "call_structured_llm_by_provider") as llm_mock:
            events, traces = gna._maintain_active_events(
                object(),
                config,
                instance_id="new-run",
                date_key="2026-03-14",
                macro_rows=[{"article_hash": "macro-a"}],
            )
        llm_mock.assert_not_called()
        self.assertEqual(2, load_mock.call_count)
        self.assertEqual(current_events, events)
        self.assertTrue(traces[0]["cache_hit"])
        self.assertEqual(scope, traces[0]["cache_scope"])

    def test_save_learning_cache_includes_history_scope_stamp(self):
        captured = {}

        class _FakeInsert:
            def __init__(self, payload):
                self.payload = payload

            def run(self, conn):
                captured["payload"] = self.payload
                return {"inserted": 1}

        class _FakeTable:
            def insert(self, payload, conflict=None):
                captured["conflict"] = conflict
                return _FakeInsert(payload)

        class _FakeDb:
            def table(self, name):
                return _FakeTable()

        class _FakeR:
            def db(self, name):
                return _FakeDb()

        config = {
            "base_instance_id": "nexus-testing",
            "history_scope_id": "scope-123",
            "history_model_stamp": {"root_model": "gpt-4.1-mini"},
        }
        with patch.object(gna, "_ensure_learning_cache_table"), \
             patch.object(gna, "_r", _FakeR()):
            gna._save_learning_cache(object(), "nexus-testing|scope-123", "hello", config=config)

        self.assertEqual("replace", captured["conflict"])
        self.assertEqual("nexus-testing", captured["payload"]["base_instance_id"])
        self.assertEqual("scope-123", captured["payload"]["history_scope_id"])
        self.assertEqual({"root_model": "gpt-4.1-mini"}, captured["payload"]["history_model_stamp"])

    def test_discovered_stock_docs_include_history_scope_stamp(self):
        captured = {}

        class _FakeInsert:
            def __init__(self, payload):
                self.payload = payload

            def run(self, conn):
                captured["payload"] = self.payload
                return {"inserted": 1}

        class _FakeTable:
            def insert(self, payload, conflict=None):
                captured["conflict"] = conflict
                return _FakeInsert(payload)

        class _FakeDb:
            def table(self, name):
                return _FakeTable()

        class _FakeR:
            def db(self, name):
                return _FakeDb()

        config = {
            "base_instance_id": "nexus-testing",
            "history_scope_id": "scope-123",
            "history_model_stamp": {"root_model": "gpt-4.1-mini"},
            "max_discovered_stocks": 10,
        }
        with patch.object(gna, "_ensure_discovered_stocks_table"), \
             patch.object(gna, "_get_all_discovered_stocks", return_value=[]), \
             patch.object(gna, "_r", _FakeR()):
            discovered = gna._discover_stocks(
                object(),
                {"NVDA": {"source_trend_ids": ["trend-a"]}},
                "nexus-testing|scope-123",
                set(),
                config,
                "2026-03-15",
            )

        self.assertEqual(["NVDA"], discovered)
        self.assertEqual("replace", captured["conflict"])
        self.assertEqual("nexus-testing", captured["payload"]["base_instance_id"])
        self.assertEqual("scope-123", captured["payload"]["history_scope_id"])
        self.assertEqual({"root_model": "gpt-4.1-mini"}, captured["payload"]["history_model_stamp"])

    def test_maintain_active_events_preserves_candidate_supporting_articles_after_llm_update(self):
        config = {
            "event_maintenance_llm_provider": "openai",
            "event_maintenance_llm_api_key": "sk-role",
            "event_maintenance_llm_model": "gpt-4.1-mini",
        }
        current_events = [{
            "event_cluster_key": "war",
            "event_name": "War",
            "event_type": "government",
            "government_action_type": "war_declared",
            "status": "live",
            "start_date": "2026-03-10",
            "affected_sectors": ["Defense"],
        }]
        candidates = [{
            "event_cluster_key": "war",
            "event_name": "War",
            "event_type": "government",
            "government_action_type": "war_declared",
            "status": "live",
            "start_date": "2026-03-14",
            "affected_sectors": ["Defense"],
            "supporting_article_hashes": ["macro-a"],
            "impact_direction": "bullish",
            "reason": "Conflict escalation",
        }]
        llm_updates = gna._ActiveEventMaintenanceResponse(
            updates=[
                gna._ActiveEventRecord(
                    event_cluster_key="war",
                    event_name="War",
                    event_type="government",
                    government_action_type="war_declared",
                    status="live",
                    confidence=0.9,
                    affected_sectors=["Defense", "Energy"],
                    reason="Still active",
                )
            ]
        )
        inserted: dict[str, list[dict]] = {}

        class _FakeWrite:
            def __init__(self, table_name, docs):
                self.table_name = table_name
                self.docs = docs

            def run(self, conn):
                inserted[self.table_name] = list(self.docs)
                return None

        class _FakeTable:
            def __init__(self, table_name):
                self.table_name = table_name

            def insert(self, docs, conflict=None):
                return _FakeWrite(self.table_name, docs)

        class _FakeDB:
            def table(self, table_name):
                return _FakeTable(table_name)

        class _FakeR:
            def db(self, name):
                return _FakeDB()

        reloaded = [{
            "event_cluster_key": "war",
            "status": "live",
            "affected_tickers": [],
        }]
        with patch.object(gna, "_ensure_nexus_history_table"), \
             patch.object(gna, "_derive_event_candidates_from_macro_rows", return_value=candidates), \
             patch.object(gna, "_load_active_events_as_of", side_effect=[current_events, reloaded]), \
             patch.object(gna, "call_structured_llm_by_provider", return_value=llm_updates), \
             patch.object(gna, "get_last_structured_llm_call_metadata", return_value={"provider": "openai", "effective_model": "gpt-4.1-mini", "ok": True, "usage": {}}), \
             patch.object(gna, "_r", _FakeR()):
            gna._maintain_active_events(
                object(),
                config,
                instance_id="new-run",
                date_key="2026-03-14",
                macro_rows=[{"article_hash": "macro-a"}],
            )

        active_docs = inserted[gna.NEXUS_ACTIVE_EVENTS_TABLE]
        self.assertEqual(["macro-a"], active_docs[0]["supporting_article_hashes"])
        self.assertEqual("bullish", active_docs[0]["impact_direction"])

    def test_weight_optimizer_trains_and_scores_candidate(self):
        training_rows = []
        for idx in range(12):
            training_rows.append({
                "features": {
                    "base_raw_score": 0.4 if idx % 2 == 0 else -0.3,
                    "direct_sentiment": 1.0 if idx % 2 == 0 else -1.0,
                    "n_paths": 3 + idx,
                    "company_article_count": 2 + (idx % 3),
                    "company_positive_count": 2 if idx % 2 == 0 else 0,
                    "company_negative_count": 0 if idx % 2 == 0 else 2,
                    "company_avg_relevance": 0.8,
                    "company_avg_impact": 0.7,
                    "finbert_sentiment_avg": 0.5 if idx % 2 == 0 else -0.4,
                    "finbert_impulse_max": 0.4 if idx % 2 == 0 else 0.3,
                    "macro_article_count": 1,
                    "macro_negative_count": 0 if idx % 2 == 0 else 1,
                    "macro_positive_count": 1 if idx % 2 == 0 else 0,
                    "government_action_hits": 1,
                    "active_event_count": 1,
                    "active_event_negative_count": 0 if idx % 2 == 0 else 1,
                    "active_event_positive_count": 1 if idx % 2 == 0 else 0,
                    "historical_analog_count": 2,
                    "historical_analog_avg_return": 4.0 if idx % 2 == 0 else -3.0,
                    "position_open": 0.0,
                },
                "signed_return": 6.0 if idx % 2 == 0 else -5.0,
            })
        bundle = wopt.train_nexus_models(training_rows)
        self.assertIsNotNone(bundle)
        score = wopt.score_nexus_candidate(bundle, training_rows[0]["features"])
        self.assertIn("ml_up_probability", score)
        self.assertIn("ml_expected_return", score)

    def test_nexus_ml_bundle_cache_is_scoped_by_instance_and_lookback(self):
        strategy_cache = {}
        with patch.object(gna, "_load_training_rows", return_value=[{"features": {}, "signed_return": 1.0}]), \
             patch.object(gna, "_train_nexus_models", side_effect=["bundle-a", "bundle-b", "bundle-c"]) as train_mock:
            bundle_a, meta_a = gna._maybe_train_nexus_ml_bundle(object(), strategy_cache, "inst-a", "2026-03-14", 90)
            bundle_a_reuse, meta_a_reuse = gna._maybe_train_nexus_ml_bundle(object(), strategy_cache, "inst-a", "2026-03-14", 90)
            bundle_b, meta_b = gna._maybe_train_nexus_ml_bundle(object(), strategy_cache, "inst-b", "2026-03-14", 90)
            bundle_c, meta_c = gna._maybe_train_nexus_ml_bundle(object(), strategy_cache, "inst-b", "2026-03-14", 30)

        self.assertEqual("bundle-a", bundle_a)
        self.assertEqual("bundle-a", bundle_a_reuse)
        self.assertEqual("bundle-b", bundle_b)
        self.assertEqual("bundle-c", bundle_c)
        self.assertFalse(meta_a["reused"])
        self.assertTrue(meta_a_reuse["reused"])
        self.assertFalse(meta_b["reused"])
        self.assertFalse(meta_c["reused"])
        self.assertEqual(3, train_mock.call_count)

    def test_candidate_government_action_type_prefers_active_events_then_macro_rows(self):
        active_events = [{
            "affected_tickers": ["XOM"],
            "government_action_type": "war_declared",
        }]
        macro_rows = [{
            "possible_direct_companies": ["GOOGL"],
            "government_action_type": "regulation_or_rulemaking",
        }]
        self.assertEqual("war_declared", gna._candidate_government_action_type("XOM", macro_rows, active_events))
        self.assertEqual("regulation_or_rulemaking", gna._candidate_government_action_type("GOOGL", macro_rows, active_events))
        self.assertEqual("", gna._candidate_government_action_type("AAPL", macro_rows, active_events))

    def test_save_trade_contexts_and_outcomes_deletes_stale_hold_outcome_doc(self):
        deleted_ids: list[str] = []

        class _FakeDelete:
            def __init__(self, ids):
                self.ids = ids

            def run(self, conn):
                deleted_ids.extend(self.ids)
                return None

        class _FakeSelection:
            def __init__(self, ids):
                self.ids = ids

            def delete(self):
                return _FakeDelete(self.ids)

        class _FakeInsert:
            def run(self, conn):
                return None

        class _FakeTable:
            def insert(self, docs, conflict=None):
                return _FakeInsert()

            def get_all(self, *ids):
                return _FakeSelection(list(ids))

        class _FakeDB:
            def table(self, table_name):
                return _FakeTable()

        class _FakeR:
            def db(self, name):
                return _FakeDB()

        with patch.object(gna, "_ensure_nexus_history_table"), \
             patch.object(gna, "_r", _FakeR()):
            gna._save_trade_contexts_and_outcomes(
                object(),
                instance_id="inst-a",
                date_key="2026-03-14",
                prices={"AAPL": 210.0},
                scores={"AAPL": {"score": 0, "action_intent": "hold", "reason": "No edge"}},
                candidate_features={"AAPL": {}},
                llm_traces={},
                active_events=[],
                config={},
            )

        self.assertEqual(["inst-a|2026-03-14|AAPL"], deleted_ids)

    def test_nexus_action_intent_helper_distinguishes_initial_add_and_sell(self):
        class _Portfolio:
            _positions = {"AAPL": 5.0}

        self.assertEqual("initial_buy", gna._infer_action_intent(1, "MSFT", _Portfolio()))
        self.assertEqual("add_buy", gna._infer_action_intent(1, "AAPL", _Portfolio()))
        self.assertEqual("sell_override", gna._infer_action_intent(-1, "AAPL", _Portfolio()))
        self.assertEqual("hold", gna._infer_action_intent(0, "AAPL", _Portfolio()))

    def test_private_entity_bridge_seeds_amzn_for_ring_context_without_validator(self):
        articles = [{
            "headline": "Ring launches new home security bundle",
            "summary": "The new doorbell and security camera set expands Alexa support.",
            "symbols": [],
        }]
        alias_index = [{
            "entity_key": "ex21:AMZN:ring",
            "display_name": "Ring LLC",
            "aliases": ["Ring"],
            "normalized_aliases": ["ring"],
            "alias_risk": "high",
            "requires_confirmation": True,
            "ancestor_tickers": ["AMZN"],
        }]
        with patch.object(gna, "_load_private_entity_alias_index", return_value=alias_index), \
             patch.object(gna, "_public_company_candidates_for_alias", return_value=[{"ticker": "RING", "name": "Ring Energy, Inc."}]), \
             patch.object(gna, "_structured_hierarchy_validation") as validator_mock:
            resolutions = gna._resolve_private_entity_news_matches(object(), articles, as_of_date="2026-03-10")
        validator_mock.assert_not_called()
        self.assertEqual(1, len(resolutions))
        self.assertEqual("AMZN", resolutions[0]["seed_ticker"])

    def test_private_entity_bridge_can_choose_public_company_candidate(self):
        articles = [{
            "headline": "Ring Energy closes new acquisition",
            "summary": "Ticker RING rallied after management raised guidance.",
            "symbols": [],
        }]
        alias_index = [{
            "entity_key": "ex21:AMZN:ring",
            "display_name": "Ring LLC",
            "aliases": ["Ring"],
            "normalized_aliases": ["ring"],
            "alias_risk": "high",
            "requires_confirmation": True,
            "ancestor_tickers": ["AMZN"],
        }]
        verdict = gna._PrivateEntityNewsResolutionResponse(
            keep=True,
            best_entity_key="public:RING",
            best_parent="RING",
            best_entity_kind="company",
            confidence_bucket="medium",
            reason="Ticker-specific context is stronger",
            evidence_urls=["https://example.com/ring-energy"],
        )
        with patch.object(gna, "_load_private_entity_alias_index", return_value=alias_index), \
             patch.object(gna, "_public_company_candidates_for_alias", return_value=[{"ticker": "RING", "name": "Ring Energy, Inc."}]), \
             patch.object(gna, "_structured_hierarchy_validation", return_value=verdict):
            resolutions = gna._resolve_private_entity_news_matches(object(), articles, as_of_date="2026-03-10")
        self.assertEqual(1, len(resolutions))
        self.assertEqual("RING", resolutions[0]["seed_ticker"])
        self.assertEqual("company", resolutions[0]["entity_kind"])

    def test_phase6_ex21_promotion_skips_validator_for_strong_single_match(self):
        issuer_record = {
            "ticker": "AMZN",
            "issuer_key": "issuer-amzn",
            "name": "Amazon.com, Inc.",
            "canonical_name": "Amazon.com, Inc.",
        }
        company_records = [
            issuer_record,
            {
                "ticker": "ROKU",
                "issuer_key": "issuer-roku",
                "name": "Roku, Inc.",
                "canonical_name": "Roku, Inc.",
                "listing_name": "Roku, Inc.",
                "lei_legal_name": "",
            },
        ]
        with patch.object(gns, "_phase6_call_hierarchy_validator") as validator_mock, \
             patch.object(gns, "_company_records_have_sec_control_support", return_value=True):
            chosen = gns._phase6_choose_listed_company_for_subsidiary(
                company_records,
                issuer_record,
                "Roku, Inc.",
                ["Roku"],
                "Roku, Inc. appears in the exhibit.",
                "https://example.com/ex21",
            )
        validator_mock.assert_not_called()
        self.assertEqual("ROKU", chosen)

    def test_phase6_ex21_promotion_requires_independent_support_for_exact_company_match(self):
        issuer_record = {
            "ticker": "AMZN",
            "issuer_key": "issuer-amzn",
            "name": "Amazon.com, Inc.",
            "canonical_name": "Amazon.com, Inc.",
        }
        company_records = [
            issuer_record,
            {
                "ticker": "ROKU",
                "issuer_key": "issuer-roku",
                "name": "Roku, Inc.",
                "canonical_name": "Roku, Inc.",
                "listing_name": "Roku, Inc.",
                "lei_legal_name": "",
            },
        ]
        discarded = []
        with patch.object(gns, "_phase6_call_hierarchy_validator") as validator_mock, \
             patch.object(gns, "_company_records_have_sec_control_support", return_value=False):
            chosen = gns._phase6_choose_listed_company_for_subsidiary(
                company_records,
                issuer_record,
                "Roku, Inc.",
                ["Roku"],
                "Roku, Inc. appears in the exhibit.",
                "https://example.com/ex21",
                discard_logger=lambda category, reason, payload: discarded.append((category, reason, payload)),
            )
        validator_mock.assert_not_called()
        self.assertEqual("", chosen)
        self.assertEqual("exact_match_lacks_independent_support", discarded[0][1])

    def test_phase6_ex21_promotion_ignores_generic_brand_aliases_for_private_subsidiary(self):
        issuer_record = {
            "ticker": "A",
            "issuer_key": "issuer-agilent",
            "name": "Agilent Technologies Inc",
            "canonical_name": "Agilent Technologies Inc",
        }
        company_records = [
            issuer_record,
            {
                "ticker": "BKTI",
                "issuer_key": "issuer-bkti",
                "name": "BK Technologies Corporation",
                "canonical_name": "BK Technologies Corporation",
                "listing_name": "BK Technologies Corporation",
                "lei_legal_name": "Agilent Technologies Luxembourg Treasury LLC",
            },
            {
                "ticker": "QCLS",
                "issuer_key": "issuer-qcls",
                "name": "Qualis Innovations, Inc.",
                "canonical_name": "Qualis Innovations, Inc.",
                "listing_name": "Qualis Innovations, Inc.",
                "lei_legal_name": "Agilent Technology Services Holdings Ltd.",
            },
            {
                "ticker": "SSNC",
                "issuer_key": "issuer-ssnc",
                "name": "SS&C Technologies Holdings, Inc.",
                "canonical_name": "SS&C Technologies Holdings, Inc.",
                "listing_name": "SS&C Technologies Holdings, Inc.",
                "lei_legal_name": "Agilent Technologies Investments LLC",
            },
        ]
        discarded = []
        with patch.object(gns, "_phase6_call_hierarchy_validator") as validator_mock:
            chosen = gns._phase6_choose_listed_company_for_subsidiary(
                company_records,
                issuer_record,
                "Agilent Technologies Luxco LLC",
                ["Agilent Technologies Luxco LLC", "Agilent Luxco", "Agilent"],
                "Agilent Technologies Luxco LLC Delaware",
                "https://example.com/agilent-ex21",
                discard_logger=lambda category, reason, payload: discarded.append((category, reason, payload)),
            )
        validator_mock.assert_not_called()
        self.assertEqual("", chosen)
        self.assertEqual("no_listed_company_candidates", discarded[0][1])

    def test_phase6_is_ex21_document_name_matches_xexx211_pattern(self):
        self.assertTrue(gns._phase6_is_ex21_document_name("a-10312025xexx211.htm"))
        self.assertTrue(gns._phase6_is_ex21_document_name("a10-kexhibit21109272025.htm"))

    def test_phase6_pick_ex21_document_prefers_index_match_for_xexx211(self):
        filing = {
            "cik": "0001090872",
            "accession_compact": "000109087225000087",
            "primary_document": "a-20251031.htm",
        }
        index_payload = {
            "directory": {
                "item": [
                    {"name": "a-20251031.htm"},
                    {"name": "a-10312025xexx211.htm"},
                ]
            }
        }
        chosen = gns._phase6_pick_ex21_document(object(), filing, index_payload)
        self.assertEqual("a-10312025xexx211.htm", chosen)

    def test_phase6_progress_checkpoint_flushes_ready_entity_edges(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    rows = []
                    for idx in range(20):
                        rows.append({
                            "ticker": f"T{idx:03d}",
                            "name": f"Test Company {idx}",
                            "issuer_key": f"issuer-{idx}",
                            "cik": "",
                            "lei": None,
                            "canonical_name": f"Test Company {idx}",
                            "listing_name": f"Test Company {idx}",
                            "lei_legal_name": "",
                        })
                    return iter(rows)
                return iter([])

            def consume(self):
                return self

            def single(self):
                return {}

        class _FakeSession:
            def run(self, query, **kwargs):
                return _FakeResult(query, kwargs)

        progress_calls = []

        def _fake_log_progress(i, total, companies, leis_found, gleif_no_parent_data_count, companies_with_parent_data, legal_entities_written, created, pending_edges, no_lei_count, batch_ok, batch_fail, total_calls_ok, total_calls_fail):
            progress_calls.append({
                "i": i,
                "total": total,
                "created": created,
                "pending": len(pending_edges),
                "leis_found": leis_found,
                "gleif_no_parent_data_count": gleif_no_parent_data_count,
                "companies_with_parent_data": companies_with_parent_data,
                "legal_entities_written": legal_entities_written,
            })
            return (0, 0, total_calls_ok, total_calls_fail)

        def _fake_search_lei(clean_name):
            suffix = clean_name.split()[-1]
            return (f"LEI-{suffix}", "2026-03-10", clean_name, 1.0, "")

        def _fake_fetch_relationships(lei):
            suffix = lei.split("-")[-1]
            return ({
                "direct_parent_lei": f"PARENT-{suffix}",
                "direct_parent_name": f"Parent {suffix}",
                "ultimate_parent_lei": "",
                "ultimate_parent_name": "",
                "publish_date": "2026-03-10",
            }, "")

        fake_session = _FakeSession()
        with patch.object(gns, "_progress"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_phase6_promote_existing_lei_entities", return_value=0), \
             patch.object(gns, "_nexus_historical_start_date", return_value=""), \
             patch.object(gns, "_nexus_phase_manifest", return_value={}), \
             patch.object(gns, "_nexus_phase_fetch_window", return_value=("", "")), \
             patch.object(gns, "_nexus_today_iso", return_value="2026-03-10"), \
             patch.object(gns, "_gleif_request_interval_seconds", return_value=1.5), \
             patch.object(gns, "_gleif_search_lei", side_effect=_fake_search_lei), \
             patch.object(gns, "_gleif_fetch_relationships", side_effect=_fake_fetch_relationships), \
             patch.object(gns, "_phase6_write_legal_entities", side_effect=lambda _session, batch: len(batch)), \
             patch.object(gns, "_phase6_write_parent_of_entity_edges", side_effect=lambda _session, batch: len(batch)), \
             patch.object(gns, "_phase6_close_stale_gleif_support", return_value=0), \
             patch.object(gns, "_phase6_refresh_listed_ancestor_tickers"), \
             patch.object(gns, "_phase6_project_company_hierarchy_edges", return_value=(0, 0)), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_phase6_log_progress", side_effect=_fake_log_progress), \
             patch.object(gns, "_nexus_maybe_log_eta"):
            gns.phase6_gleif_hierarchy(fake_session)

        self.assertEqual(1, len(progress_calls))
        self.assertEqual(20, progress_calls[0]["created"])
        self.assertEqual(0, progress_calls[0]["pending"])
        self.assertEqual(20, progress_calls[0]["leis_found"])
        self.assertEqual(0, progress_calls[0]["gleif_no_parent_data_count"])
        self.assertEqual(20, progress_calls[0]["companies_with_parent_data"])
        self.assertEqual(20, progress_calls[0]["legal_entities_written"])

    def test_phase6_gleif_recovers_subsidiary_legal_entity_from_alternate_candidate(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{
                        "ticker": "ABT",
                        "name": "Abbott Laboratories",
                        "issuer_key": "abbott laboratories",
                        "cik": "0000001800",
                        "lei": None,
                        "canonical_name": "Abbott Laboratories",
                        "listing_name": "Abbott Laboratories",
                        "lei_legal_name": "",
                    }])
                return iter([])

            def consume(self):
                return self

            def single(self):
                return {}

        class _FakeSession:
            def __init__(self):
                self.company_lei_updates = []

            def run(self, query, **kwargs):
                text = str(query)
                if "UNWIND $batch AS p" in text and "SET c.lei = p.lei" in text:
                    self.company_lei_updates.extend(kwargs.get("batch") or [])
                return _FakeResult(query, kwargs)

        written_legal_entities = []
        written_edges = []

        def _fake_fetch_relationships(lei):
            if lei == "HQD377W2YR662HK5JX27":
                return ({
                    "direct_parent_lei": "",
                    "direct_parent_name": "",
                    "ultimate_parent_lei": "",
                    "ultimate_parent_name": "",
                    "publish_date": "2026-03-10",
                }, "")
            if lei == "549300RZV7GJRVSGSZ50":
                return ({
                    "direct_parent_lei": "",
                    "direct_parent_name": "",
                    "ultimate_parent_lei": "HQD377W2YR662HK5JX27",
                    "ultimate_parent_name": "ABBOTT LABORATORIES",
                    "publish_date": "2026-03-10",
                }, "")
            raise AssertionError(lei)

        with patch.object(gns, "_progress"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_phase6_promote_existing_lei_entities", return_value=0), \
             patch.object(gns, "_nexus_historical_start_date", return_value=""), \
             patch.object(gns, "_nexus_phase_manifest", return_value={}), \
             patch.object(gns, "_nexus_phase_fetch_window", return_value=("", "")), \
             patch.object(gns, "_nexus_today_iso", return_value="2026-03-10"), \
             patch.object(gns, "_gleif_request_interval_seconds", return_value=1.5), \
             patch.object(gns, "_gleif_search_lei", return_value=("HQD377W2YR662HK5JX27", "2026-03-10", "ABBOTT LABORATORIES", 1.0, None)), \
             patch.object(gns, "_gleif_search_candidate_records", return_value=("2026-03-10", [
                 {
                     "lei": "HQD377W2YR662HK5JX27",
                     "legal_name": "ABBOTT LABORATORIES",
                     "match_score": 1.0,
                     "registration_status": "ISSUED",
                     "conformity_flag": "CONFORMING",
                 },
                 {
                     "lei": "549300RZV7GJRVSGSZ50",
                     "legal_name": "Abbott Laboratories GmbH",
                     "match_score": 0.9,
                     "registration_status": "ISSUED",
                     "conformity_flag": "CONFORMING",
                 },
             ], None)), \
             patch.object(gns, "_gleif_fetch_relationships", side_effect=_fake_fetch_relationships), \
             patch.object(gns, "_phase6_write_legal_entities", side_effect=lambda _session, batch: written_legal_entities.extend(batch) or len(batch)), \
             patch.object(gns, "_phase6_write_parent_of_entity_edges", side_effect=lambda _session, batch: written_edges.extend(batch) or len(batch)), \
             patch.object(gns, "_phase6_close_stale_gleif_support", return_value=0), \
             patch.object(gns, "_phase6_refresh_listed_ancestor_tickers"), \
             patch.object(gns, "_phase6_project_company_hierarchy_edges", return_value=(0, 0)), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_nexus_maybe_log_eta"):
            gns.phase6_gleif_hierarchy(_FakeSession())

        self.assertEqual("HQD377W2YR662HK5JX27", written_edges[0]["gleif_parent_lei"])
        self.assertTrue(any(item["entity_key"] == "lei:549300RZV7GJRVSGSZ50" for item in written_legal_entities))
        self.assertTrue(any(
            edge["parent_kind"] == "Company"
            and edge["parent_ref"] == "ABT"
            and edge["child_kind"] == "LegalEntity"
            and edge["child_ref"] == "lei:549300RZV7GJRVSGSZ50"
            and edge["gleif_relation_kind"] == "ULTIMATE_PARENT"
            for edge in written_edges
        ))

    def test_phase6_dedupe_parent_of_entity_batch_merges_duplicate_rows(self):
        batch = [
            {
                "parent_kind": "Company",
                "parent_ref": "AAPL",
                "child_kind": "LegalEntity",
                "child_ref": "ex21:AAPL:apple_sales_international",
                "active_after": "2025-10-31",
                "last_confirmed": "2025-10-31",
                "evidence_sources": ["SEC_EX21"],
                "sec_ex21_supported": True,
                "sec_ex21_issuer_ticker": "AAPL",
                "sec_ex21_latest_filing_date": "2025-10-31",
                "sec_ex21_last_accession": "000032019325000079",
                "parent_legal_name": "Apple Inc.",
                "child_legal_name": "Apple Sales International",
            },
            {
                "parent_kind": "Company",
                "parent_ref": "AAPL",
                "child_kind": "LegalEntity",
                "child_ref": "ex21:AAPL:apple_sales_international",
                "active_after": "2025-10-30",
                "last_confirmed": "2025-11-01",
                "evidence_sources": ["SEC_EX21", "GLEIF_DIRECT_PARENT"],
                "gleif_supported": True,
                "gleif_relation_kind": "DIRECT_PARENT",
                "gleif_parent_lei": "HWUPKR0MPOU8FGXBT394",
                "gleif_child_lei": "1234567890ABCDEFGH12",
                "sec_ex21_supported": True,
                "sec_ex21_issuer_ticker": "AAPL",
                "sec_ex21_latest_filing_date": "2025-11-01",
                "sec_ex21_last_accession": "000032019325000081",
                "parent_legal_name": "Apple Inc.",
                "child_legal_name": "Apple Sales International",
            },
        ]

        merged = gns._phase6_dedupe_parent_of_entity_batch(batch)

        self.assertEqual(1, len(merged))
        item = merged[0]
        self.assertEqual("2025-10-30", item["active_after"])
        self.assertEqual("2025-11-01", item["last_confirmed"])
        self.assertEqual(["SEC_EX21", "GLEIF_DIRECT_PARENT"], item["evidence_sources"])
        self.assertTrue(item["gleif_supported"])
        self.assertTrue(item["sec_ex21_supported"])
        self.assertEqual("2025-11-01", item["sec_ex21_latest_filing_date"])
        self.assertEqual("000032019325000081", item["sec_ex21_last_accession"])

    def test_phase6_write_parent_of_entity_edges_uses_deduped_batch_count(self):
        class _FakeResult:
            def consume(self):
                return self

        class _FakeSession:
            def __init__(self):
                self.calls = []

            def run(self, query, **kwargs):
                self.calls.append((query, kwargs))
                return _FakeResult()

        session = _FakeSession()
        batch = [
            {
                "parent_kind": "Company",
                "parent_ref": "AAPL",
                "child_kind": "LegalEntity",
                "child_ref": "ex21:AAPL:apple_sales_international",
                "active_after": "2025-10-31",
                "last_confirmed": "2025-10-31",
                "evidence_sources": ["SEC_EX21"],
                "sec_ex21_supported": True,
                "sec_ex21_issuer_ticker": "AAPL",
                "sec_ex21_latest_filing_date": "2025-10-31",
                "sec_ex21_last_accession": "000032019325000079",
                "parent_legal_name": "Apple Inc.",
                "child_legal_name": "Apple Sales International",
            },
            {
                "parent_kind": "Company",
                "parent_ref": "AAPL",
                "child_kind": "LegalEntity",
                "child_ref": "ex21:AAPL:apple_sales_international",
                "active_after": "2025-10-31",
                "last_confirmed": "2025-10-31",
                "evidence_sources": ["SEC_EX21"],
                "sec_ex21_supported": True,
                "sec_ex21_issuer_ticker": "AAPL",
                "sec_ex21_latest_filing_date": "2025-10-31",
                "sec_ex21_last_accession": "000032019325000079",
                "parent_legal_name": "Apple Inc.",
                "child_legal_name": "Apple Sales International",
            },
            {
                "parent_kind": "Company",
                "parent_ref": "AAPL",
                "child_kind": "Company",
                "child_ref": "BEAT",
                "active_after": "2025-10-31",
                "last_confirmed": "2025-10-31",
                "evidence_sources": ["SEC_EX21"],
                "sec_ex21_supported": True,
                "sec_ex21_issuer_ticker": "AAPL",
                "sec_ex21_latest_filing_date": "2025-10-31",
                "sec_ex21_last_accession": "000032019325000079",
                "parent_legal_name": "Apple Inc.",
                "child_legal_name": "Beats Electronics, LLC",
            },
        ]

        written = gns._phase6_write_parent_of_entity_edges(session, batch)

        self.assertEqual(2, written)
        self.assertEqual(2, len(session.calls))
        batch_sizes = [len(kwargs["batch"]) for _query, kwargs in session.calls]
        self.assertEqual([1, 1], batch_sizes)

    def test_phase6b_done_message_reports_live_edge_counts(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{
                        "ticker": "AAPL",
                        "name": "Apple Inc.",
                        "issuer_key": "apple inc",
                        "cik": "0000320193",
                        "lei": None,
                        "canonical_name": "Apple Inc.",
                        "listing_name": "Apple Inc.",
                        "lei_legal_name": "",
                    }])
                return iter([])

            def consume(self):
                return self

            def single(self):
                return {}

        class _FakeSession:
            def run(self, query, **kwargs):
                return _FakeResult(query, kwargs)

        progress_calls = []
        logs = []
        prepared_payload = {
            "issuer_record": {
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "issuer_key": "apple inc",
                "cik": "0000320193",
                "canonical_name": "Apple Inc.",
                "listing_name": "Apple Inc.",
            },
            "issuer_ticker": "AAPL",
            "annual_filings_found": True,
            "prepared_filings": [
                {
                    "filing": {
                        "filing_date": "2025-10-31",
                        "accession_compact": "000032019325000079",
                        "filing_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20251031x10k.htm",
                    },
                    "entries": [
                        {
                            "legal_name": "Apple Sales International",
                            "aliases": ["Apple Sales International"],
                            "jurisdiction": "Ireland",
                        }
                    ],
                }
            ],
            "error": "",
        }

        with ExitStack() as stack:
            stack.enter_context(patch.object(gns, "_progress", side_effect=lambda *args: progress_calls.append(args)))
            stack.enter_context(patch.object(gns, "_log", side_effect=lambda msg, color="white": logs.append(msg)))
            stack.enter_context(patch.object(gns, "_phase6_promote_existing_lei_entities", return_value=0))
            stack.enter_context(patch.object(gns, "_nexus_historical_start_date", return_value=""))
            stack.enter_context(patch.object(gns, "_nexus_phase_manifest", return_value={}))
            stack.enter_context(patch.object(gns, "_phase6_ex21_fetch_window", return_value=("2025-01-01", "2026-03-14", False, "")))
            stack.enter_context(patch.object(gns, "_nexus_today_iso", return_value="2026-03-14"))
            stack.enter_context(patch.object(gns, "_phase6_reset_discard_log", return_value="/tmp/discarded_edges.log.txt"))
            stack.enter_context(patch.object(gns, "_phase6_prepare_ex21_issuer_payload", return_value=prepared_payload))
            stack.enter_context(patch.object(gns, "_phase6_write_legal_entities", return_value=1))
            stack.enter_context(patch.object(gns, "_phase6_write_parent_of_entity_edges", return_value=5))
            stack.enter_context(patch.object(gns, "_phase6_close_omitted_ex21_edges_for_issuer", return_value=0))
            stack.enter_context(patch.object(gns, "_phase6_refresh_listed_ancestor_tickers"))
            stack.enter_context(patch.object(gns, "_phase6_project_company_hierarchy_edges", return_value=(4, 0)))
            stack.enter_context(patch.object(gns, "_phase6_count_live_hierarchy_edges", side_effect=[3, 2]))
            stack.enter_context(patch.object(gns, "_sync_graph_edge_intervals"))
            stack.enter_context(patch.object(gns, "_retire_relationships", return_value=0))
            stack.enter_context(patch.object(gns, "_phase6b_report_progress"))
            stack.enter_context(patch.object(gns, "_nexus_update_phase_manifest"))
            stack.enter_context(patch.object(gns, "_nexus_rethink_conn", None))
            stack.enter_context(patch.dict(sys.modules, {"sec_edgar_supply_chain": sec}, clear=False))
            gns.phase6b_sec_ex21_hierarchy(_FakeSession())

        self.assertTrue(any("5 PARENT_OF_ENTITY edge upserts" in msg for msg in logs))
        self.assertTrue(any("3 live EX-21 hierarchy edges" in msg for msg in logs))
        self.assertTrue(any("2 live EX-21 company projections" in msg for msg in logs))
        done_messages = [args[1] for args in progress_calls if len(args) >= 2 and isinstance(args[1], str)]
        self.assertTrue(any("3 live hierarchy edges and 2 live company projections" in msg for msg in done_messages))

    def test_gleif_search_lei_prefers_exact_identity_match_on_tie(self):
        class _FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "meta": {"goldenCopy": {"publishDate": "2026-03-10T16:00:00Z"}},
                    "data": [
                        {
                            "id": "O4QK7KMMK83ITNTHUG69",
                            "attributes": {"entity": {"legalName": {"name": "Aegon Ltd."}}},
                        },
                        {
                            "id": "213800NSW238W1LX2M70",
                            "attributes": {"entity": {"legalName": {"name": "AEGON SIPP NOMINEE LTD"}}},
                        },
                        {
                            "id": "213800KYU495KRK1TT89",
                            "attributes": {"entity": {"legalName": {"name": "AEGON DIRECT LIFE INSURANCE CO.,LTD."}}},
                        },
                    ],
                }

        class _FakeSession:
            def get(self, *args, **kwargs):
                return _FakeResponse()

        with patch.object(gns, "_nexus_read_cached_json", return_value=None), \
             patch.object(gns, "_nexus_write_cached_json"), \
             patch.object(gns, "_nexus_stage_download"), \
             patch.object(gns, "_gleif_wait_for_request_slot"), \
             patch.object(gns, "_gleif_http_session", return_value=_FakeSession()):
            lei, publish_date, legal_name, match_score, err = gns._gleif_search_lei("Aegon Ltd")

        self.assertIsNone(err)
        self.assertEqual("O4QK7KMMK83ITNTHUG69", lei)
        self.assertEqual("2026-03-10", publish_date)
        self.assertEqual("Aegon Ltd.", legal_name)
        self.assertEqual(1.0, match_score)

    def test_gleif_search_lei_rejects_explicit_form_conflict_without_matching_candidate(self):
        class _FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "meta": {"goldenCopy": {"publishDate": "2026-03-10T16:00:00Z"}},
                    "data": [
                        {
                            "id": "54930004TGPV6DR3LE12",
                            "attributes": {"entity": {"legalName": {"name": "Allegro MicroSystems, LLC"}}},
                        },
                        {
                            "id": "254900LW5D0AY4NV3R34",
                            "attributes": {"entity": {"legalName": {"name": "ALLEGRO MICROSYSTEMS MARKETING INDIA PRIVATE LIMITED"}}},
                        },
                    ],
                }

        class _FakeSession:
            def get(self, *args, **kwargs):
                return _FakeResponse()

        with patch.object(gns, "_nexus_read_cached_json", return_value=None), \
             patch.object(gns, "_nexus_write_cached_json"), \
             patch.object(gns, "_nexus_stage_download"), \
             patch.object(gns, "_gleif_wait_for_request_slot"), \
             patch.object(gns, "_gleif_http_session", return_value=_FakeSession()):
            lei, publish_date, legal_name, match_score, err = gns._gleif_search_lei("Allegro MicroSystems, Inc")

        self.assertIsNone(err)
        self.assertIsNone(lei)
        self.assertEqual("2026-03-10", publish_date)
        self.assertEqual("", legal_name)
        self.assertEqual(0.0, match_score)

    def test_gleif_search_lei_prefers_issued_exact_match_over_lapsed_exact_match(self):
        class _FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "meta": {"goldenCopy": {"publishDate": "2026-03-10T16:00:00Z"}},
                    "data": [
                        {
                            "id": "549300I73MX1K4BJJV80",
                            "attributes": {
                                "entity": {"legalName": {"name": "ABBOTT LABORATORIES"}},
                                "registration": {"status": "LAPSED"},
                                "conformityFlag": "CONFORMING",
                            },
                        },
                        {
                            "id": "549300JBDVIIMW4FW262",
                            "attributes": {
                                "entity": {"legalName": {"name": "ABBOTT LABORATORIES."}},
                                "registration": {"status": "ISSUED"},
                                "conformityFlag": "CONFORMING",
                            },
                        },
                    ],
                }

        class _FakeSession:
            def get(self, *args, **kwargs):
                return _FakeResponse()

        with patch.object(gns, "_nexus_read_cached_json", return_value=None), \
             patch.object(gns, "_nexus_write_cached_json"), \
             patch.object(gns, "_nexus_stage_download"), \
             patch.object(gns, "_gleif_wait_for_request_slot"), \
             patch.object(gns, "_gleif_http_session", return_value=_FakeSession()):
            lei, publish_date, legal_name, match_score, err = gns._gleif_search_lei("Abbott Laboratories")

        self.assertIsNone(err)
        self.assertEqual("549300JBDVIIMW4FW262", lei)
        self.assertEqual("2026-03-10", publish_date)
        self.assertEqual("ABBOTT LABORATORIES.", legal_name)
        self.assertEqual(1.0, match_score)

    def test_gleif_search_lei_uses_versioned_search_cache_key(self):
        captured = {}

        def _fake_cache_path(subdir, filename):
            captured["subdir"] = subdir
            captured["filename"] = filename
            return "C:\\temp\\search_cache.json"

        with patch.object(gns, "_nexus_cache_path", side_effect=_fake_cache_path), \
             patch.object(gns, "_nexus_read_cached_json", return_value={"lei": None, "publish_date": "", "legal_name": "", "match_score": 0.0}):
            gns._gleif_search_lei("AbbVie Inc")

        self.assertEqual("phase6", captured["subdir"])
        self.assertIn("search_v", captured["filename"])
        self.assertIn("AbbVie_Inc", captured["filename"])

    def test_gleif_alternate_child_candidates_skip_non_issued_children(self):
        candidates = [
            {
                "lei": "FR5LCKFTG8054YNNRU85",
                "legal_name": "ABBVIE INC.",
                "match_score": 1.0,
                "registration_status": "ISSUED",
                "conformity_flag": "CONFORMING",
            },
            {
                "lei": "254900YDDB694NPELO08",
                "legal_name": "AbbVie Philippines Inc.",
                "match_score": 0.9,
                "registration_status": "ISSUED",
                "conformity_flag": "CONFORMING",
            },
            {
                "lei": "549300RITDD6SEJXMP15",
                "legal_name": "Abbvie Endocrine Inc.",
                "match_score": 0.9,
                "registration_status": "LAPSED",
                "conformity_flag": "CONFORMING",
            },
        ]

        chosen = gns._gleif_alternate_child_candidates("AbbVie Inc", "FR5LCKFTG8054YNNRU85", candidates)

        self.assertEqual(["254900YDDB694NPELO08"], [item["lei"] for item in chosen])

    def test_gleif_name_match_score_caps_single_token_brand_expansions_below_exact(self):
        exact = gns._gleif_name_match_score("Apple Inc", "Apple Inc.")
        expanded = gns._gleif_name_match_score("Apple Inc", "Apple Ford, Inc.")
        subsidiary_like = gns._gleif_name_match_score("Airbnb, Inc", "AIRBNB IRELAND UNLIMITED COMPANY")

        self.assertEqual(1.0, exact)
        self.assertGreaterEqual(expanded, gns.GLEIF_MIN_MATCH_SCORE)
        self.assertLess(expanded, 1.0)
        self.assertGreaterEqual(subsidiary_like, gns.GLEIF_MIN_MATCH_SCORE)
        self.assertLess(subsidiary_like, 1.0)

    def test_gleif_split_alternate_child_candidates_defers_limit_until_relationship_check(self):
        candidates = [
            {
                "lei": "549300HMUDNO0RY56D37",
                "legal_name": "AIRBNB, INC.",
                "match_score": 1.0,
                "registration_status": "ISSUED",
                "conformity_flag": "CONFORMING",
            },
            {
                "lei": "254900VPEYF0BDQIV972",
                "legal_name": "Airbnb Payments, Inc.",
                "match_score": 1.0,
                "registration_status": "ISSUED",
                "conformity_flag": "CONFORMING",
            },
            {
                "lei": "254900N2HRNZIHMFXJ46",
                "legal_name": "AIRBNB IRELAND UNLIMITED COMPANY",
                "match_score": 1.0,
                "registration_status": "ISSUED",
                "conformity_flag": "CONFORMING",
            },
            {
                "lei": "984500M4F1C13A64J677",
                "legal_name": "AIRBNB INDIA PRIVATE LIMITED",
                "match_score": 1.0,
                "registration_status": "ISSUED",
                "conformity_flag": "CONFORMING",
            },
        ]

        with patch.object(gns, "GLEIF_ALT_CHILD_CANDIDATE_LIMIT", 2):
            accepted, rejected = gns._gleif_split_alternate_child_candidates(
                "Airbnb, Inc",
                "549300HMUDNO0RY56D37",
                candidates,
            )

        self.assertCountEqual(
            ["254900VPEYF0BDQIV972", "254900N2HRNZIHMFXJ46", "984500M4F1C13A64J677"],
            [item["lei"] for item in accepted],
        )
        self.assertFalse(any(item.get("reason") == "candidate_limit_exceeded" for item in rejected))

    def test_phase6_discard_log_writes_jsonl_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = os.path.join(tmpdir, "discarded_edges.log.txt")
            with patch.object(gns, "_phase6_discard_log_path", return_value=log_path):
                reset_path = gns._phase6_reset_discard_log("phase6_hierarchy:test-run")
                gns._phase6_log_discarded_edge(
                    "phase6_hierarchy:test-run",
                    "gleif_alternate_child",
                    "candidate_not_issued",
                    {"ticker": "ABBV", "candidate_lei": "549300RITDD6SEJXMP15"},
                )

            self.assertEqual(log_path, reset_path)
            with open(log_path, "r", encoding="utf-8") as handle:
                lines = [json.loads(line) for line in handle if line.strip()]

        self.assertEqual("discard_log_started", lines[0]["event"])
        self.assertEqual("discarded_edge", lines[1]["event"])
        self.assertEqual("gleif_alternate_child", lines[1]["category"])
        self.assertEqual("candidate_not_issued", lines[1]["reason"])
        self.assertEqual("ABBV", lines[1]["ticker"])

    def test_phase6_logs_no_gleif_parent_data_when_relationships_are_absent(self):
        company_record = {
            "ticker": "ABNB",
            "name": "Airbnb, Inc",
            "issuer_key": "airbnb",
            "cik": "0001559720",
            "lei": None,
            "canonical_name": "Airbnb, Inc",
            "listing_name": "Airbnb, Inc.",
            "lei_legal_name": "",
        }
        logged = []

        def _fake_log_discard(category, reason, payload):
            logged.append((category, reason, payload))

        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([company_record])
                return iter([])

            def consume(self):
                return self

            def single(self):
                return {}

        class _FakeSession:
            def run(self, query, **kwargs):
                return _FakeResult(query, kwargs)

        def _fake_search_records(_name):
            return "", [
                {
                    "lei": "254900VPEYF0BDQIV972",
                    "legal_name": "Airbnb Payments, Inc.",
                    "match_score": 0.9,
                    "registration_status": "ISSUED",
                    "conformity_flag": "CONFORMING",
                }
            ], None

        with ExitStack() as stack:
            stack.enter_context(patch.object(gns, "_gleif_search_candidate_records", side_effect=_fake_search_records))
            stack.enter_context(patch.object(gns, "_phase6_write_parent_of_entity_edges", return_value=0))
            stack.enter_context(patch.object(gns, "_phase6_write_legal_entities", return_value=0))
            stack.enter_context(patch.object(gns, "_phase6_project_company_hierarchy_edges", return_value=(0, 0)))
            stack.enter_context(patch.object(gns, "_phase6_promote_existing_lei_entities", return_value=0))
            stack.enter_context(patch.object(gns, "_phase6_refresh_listed_ancestor_tickers"))
            stack.enter_context(patch.object(gns, "_phase6_close_stale_gleif_support", return_value=0))
            stack.enter_context(patch.object(gns, "_nexus_historical_start_date", return_value=""))
            stack.enter_context(patch.object(gns, "_nexus_phase_manifest", return_value={}))
            stack.enter_context(patch.object(gns, "_nexus_phase_fetch_window", return_value=("", "")))
            stack.enter_context(patch.object(gns, "_nexus_today_iso", return_value="2026-03-11"))
            stack.enter_context(patch.object(gns, "_gleif_request_interval_seconds", return_value=1.5))
            stack.enter_context(patch.object(gns, "_phase6_reset_discard_log", return_value="discarded_edges.log.txt"))
            stack.enter_context(patch.object(gns, "_phase6_log_discarded_edge", side_effect=lambda run_token, category, reason, payload=None: _fake_log_discard(category, reason, payload)))
            stack.enter_context(patch.object(gns, "_gleif_search_lei", return_value=("549300HMUDNO0RY56D37", "2026-03-11", "AIRBNB, INC.", 1.0, None)))
            stack.enter_context(patch.object(gns, "_gleif_fetch_relationships", return_value=({"publish_date": "", "direct_parent_lei": "", "direct_parent_name": "", "ultimate_parent_lei": "", "ultimate_parent_name": ""}, None)))
            stack.enter_context(patch.object(gns, "_progress"))
            stack.enter_context(patch.object(gns, "_log"))
            stack.enter_context(patch.object(gns, "_sync_graph_edge_intervals"))
            stack.enter_context(patch.object(gns, "_retire_relationships", return_value=0))
            stack.enter_context(patch.object(gns, "_nexus_maybe_log_eta"))
            gns.phase6_gleif_hierarchy(_FakeSession())

        reasons = [reason for _category, reason, _payload in logged]
        self.assertIn("no_gleif_parent_data", reasons)

    def test_phase6_choose_listed_company_for_subsidiary_logs_rejection_reason(self):
        issuer_record = {"ticker": "ROKU", "name": "Roku, Inc.", "issuer_key": "roku"}
        company_records = [
            {"ticker": "RKU1", "name": "Roku Holdings", "canonical_name": "Roku Holdings", "issuer_key": "roku holdings"},
            {"ticker": "RKU2", "name": "Roku International", "canonical_name": "Roku International", "issuer_key": "roku international"},
        ]
        discarded = []

        chosen = gns._phase6_choose_listed_company_for_subsidiary(
            company_records,
            issuer_record,
            "Roku International Holdings LLC",
            ["Roku International"],
            "Roku International Holdings LLC",
            "https://example.com/ex21",
            discard_logger=lambda category, reason, payload: discarded.append((category, reason, payload)),
        )

        self.assertEqual("", chosen)
        self.assertEqual(1, len(discarded))
        self.assertEqual("ex21_promotion", discarded[0][0])
        self.assertEqual("private_style_name_not_public_exact_match", discarded[0][1])
        self.assertEqual("ROKU", discarded[0][2]["issuer_ticker"])

    def test_phase6_hierarchy_validator_logs_tokens_and_decision(self):
        captured = []
        verdict = gns._AliasDisambiguationVerdict(
            keep=True,
            best_ticker="ROKU",
            best_entity_kind="company",
            confidence_bucket="high",
            reason="Exact listed-company match",
        )
        with patch("llm_utils.call_structured_llm_by_provider", return_value=verdict), \
             patch(
                 "llm_utils.get_last_structured_llm_call_metadata",
                 return_value={
                     "provider": "deepseek",
                     "effective_model": "deepseek-reasoner",
                     "fallback_used": False,
                     "usage": {"input_tokens": 321, "output_tokens": 24},
                 },
             ), \
             patch.object(gns, "_hierarchy_llm_config", return_value=("deepseek", "deepseek-reasoner", "test-key")), \
             patch.object(gns, "_log", side_effect=lambda msg, *_args, **_kwargs: captured.append(msg)):
            result = gns._phase6_call_hierarchy_validator(
                "prompt",
                gns._AliasDisambiguationVerdict,
                system_prompt="system",
                log_label="Phase 6 EX-21 validator",
                allow_search=False,
            )
        self.assertTrue(result.keep)
        self.assertTrue(any("input_tokens=321" in msg for msg in captured))
        self.assertTrue(any("output_tokens=24" in msg for msg in captured))
        self.assertTrue(any("best_ticker=ROKU" in msg for msg in captured))

    def test_phase6_promote_existing_lei_entities_uses_dynamic_property_access(self):
        captured = {}

        class _FakeResult:
            def __iter__(self):
                return iter([{
                    "lei": "ABC123",
                    "legal_name": "Example Legal Entity LLC",
                    "display_name": "Example Legal Entity",
                    "aliases": ["Example"],
                    "country": "",
                    "source_systems": [],
                    "listed_ancestor_tickers": [],
                }])

        class _FakeSession:
            def run(self, query, **kwargs):
                captured["query"] = query
                captured["kwargs"] = kwargs
                return _FakeResult()

        with patch.object(gns, "_phase6_write_legal_entities", side_effect=lambda _session, batch: len(batch)) as write_mock:
            created = gns._phase6_promote_existing_lei_entities(_FakeSession())

        self.assertEqual(1, created)
        self.assertIn("properties(l) AS props", captured["query"])
        self.assertIn("props['country']", captured["query"])
        self.assertIn("props['source_systems']", captured["query"])
        self.assertIn("props['listed_ancestor_tickers']", captured["query"])
        payload = write_mock.call_args.args[1][0]
        self.assertEqual("lei:ABC123", payload["entity_key"])
        self.assertEqual(["GLEIF"], payload["source_systems"])

    def test_ai_backtest_llm_config_defaults_to_gemini_flash_preview(self):
        with patch.object(abe, "_env", side_effect=lambda key, default="": ""):
            cfg = abe._get_llm_config("strategy_generation")
        self.assertEqual("gemini", cfg["provider"])
        self.assertEqual("gemini-3-flash-preview", cfg["model"])

    def test_ai_backtest_llm_config_defaults_to_deepseek_reasoner(self):
        def _fake_env(key, default=""):
            if key == "AI_BACKTESTING_AGENT_PROVIDER":
                return "deepseek"
            return ""

        with patch.object(abe, "_env", side_effect=_fake_env):
            cfg = abe._get_llm_config("strategy_generation")
        self.assertEqual("deepseek", cfg["provider"])
        self.assertEqual("deepseek-reasoner", cfg["model"])

    def test_ai_backtest_llm_config_supports_azure_provider_config(self):
        def _fake_env(key, default=""):
            values = {
                "AI_BACKTESTING_AGENT_PROVIDER": "azure",
                "AZURE_OPENAI_ENDPOINT": "https://example-resource.openai.azure.com",
                "OPENAI_API_VERSION": "2024-10-21",
                "AZURE_OPENAI_API_KEY": "azure-key",
                "AZURE_OPENAI_DEPLOYMENT": "azure-gpt4-mini",
            }
            return values.get(key, default)

        with patch.object(abe, "_env", side_effect=_fake_env):
            cfg = abe._get_llm_config("strategy_generation")

        self.assertEqual("azure", cfg["provider"])
        self.assertEqual("azure-gpt4-mini", cfg["model"])
        self.assertEqual("azure-key", cfg["api_key"])
        self.assertEqual(
            {
                "azure_endpoint": "https://example-resource.openai.azure.com",
                "api_version": "2024-10-21",
            },
            cfg["provider_config"],
        )

    def test_ai_backtest_generate_strategies_uses_structured_output(self):
        class _FakeClient:
            def get(self, path):
                if path == "/strategies/available":
                    return {
                        "strategies": [
                            {"id": "ml_news", "name": "MlNews", "schema": {}, "description": "ML News strategy"},
                            {"id": "earnings", "name": "Earnings", "schema": {}, "description": "Earnings strategy"},
                        ]
                    }
                if path == "/agent/results?limit=50":
                    return {"results": []}
                raise AssertionError(path)

        payload = abe._StrategyGenerationResponse(
            strategies=[
                '{"name":"AI combo","strategies":['
                '{"strategy":"MlNews","weight":0.6,"execution_position":0,"decision_phase":"pre","execution_scope":"run_once","conditions":{},"config":{"model_name":"gemini-3-flash-preview"}},'
                '{"strategy":"earnings","weight":0.4,"execution_position":0,"decision_phase":"pre","execution_scope":"per_symbol","conditions":{"min_confidence_to_buy":0.5},"config":{"lookahead_days":14}}'
                ']}'
            ]
        )
        with patch.dict(os.environ, {"GEMINI_API_KEY": "g-key", "KEY": "alpaca-key", "SECRET": "alpaca-secret"}, clear=False), \
             patch.object(abe, "call_structured_llm_by_provider", return_value=payload):
            strategies = abe._generate_strategies(
                _FakeClient(),
                {"provider": "gemini", "model": "gemini-3-flash-preview", "api_key": "g-key"},
                1,
            )
        self.assertEqual(1, len(strategies))
        self.assertEqual("AI combo", strategies[0]["name"])
        ml_news_sub, earnings_sub = strategies[0]["strategies"]
        self.assertEqual("ml_news", ml_news_sub["strategy"])
        self.assertEqual("run_once", ml_news_sub["execution_scope"])
        self.assertEqual("earnings", earnings_sub["strategy"])
        self.assertEqual("gemini", earnings_sub["config"]["llm_provider"])
        self.assertEqual("gemini-3-flash-preview", earnings_sub["config"]["model_name"])
        self.assertEqual(14, earnings_sub["config"]["lookahead_days"])

    def test_ai_backtest_validate_result_uses_structured_output(self):
        with patch.object(
            abe,
            "call_structured_llm_by_provider",
            return_value=abe._ValidationDecisionResponse(decision="KEEP", reason="Strong profit with acceptable concentration."),
        ):
            keep, reason = abe._validate_result_llm(
                {"pnl": 250.0, "pnl_percent": 12.5, "win_rate_percent": 60.0, "total_trades": 8},
                {"name": "Test", "strategies": []},
                {"provider": "gemini", "model": "gemini-3-flash-preview", "api_key": "g-key"},
            )
        self.assertTrue(keep)
        self.assertIn("Strong profit", reason)

    def test_ai_backtest_best_selection_uses_structured_output(self):
        with patch.object(
            abe,
            "call_structured_llm_by_provider",
            return_value=abe._BestSelectionDecisionResponse(
                decision="SET_AS_NEW_BEST",
                reason="Candidate is stronger across the validation stages.",
            ),
        ):
            set_best, reason = abe._decide_new_best_llm(
                {"name": "Candidate", "strategies": []},
                400.0,
                15.0,
                {"initial": {"pnl": 400.0, "pnl_percent": 15.0}},
                None,
                {"provider": "deepseek", "model": "deepseek-chat", "api_key": "d-key"},
            )
        self.assertTrue(set_best)
        self.assertIn("stronger", reason.lower())

    def test_server_reuses_restarting_nexus_container_on_startup(self):
        class _FakeContainer:
            def __init__(self, status):
                self.status = status
                self.removed = False

            def reload(self):
                return None

            def remove(self):
                self.removed = True

        class _FakeImages:
            def get(self, image):
                return {"image": image}

        class _FakeContainers:
            def __init__(self, container):
                self._container = container

            def get(self, name):
                return self._container

            def run(self, *args, **kwargs):
                raise AssertionError("Nexus container should not be recreated when status is restarting")

        class _FakeDockerClient:
            def __init__(self, container):
                self.images = _FakeImages()
                self.containers = _FakeContainers(container)

        container = _FakeContainer("restarting")
        original = srv.nexus_container_obj
        try:
            srv.nexus_container_obj = None
            with patch.object(srv, "_get_docker_client", return_value=_FakeDockerClient(container)), \
                 patch.object(srv, "_get_instance_network", return_value="test-net"):
                reused = srv.start_nexus_container()
            self.assertIs(container, reused)
            self.assertFalse(container.removed)
            self.assertIs(container, srv.nexus_container_obj)
        finally:
            srv.nexus_container_obj = original

    def test_server_control_change_does_not_restart_non_terminal_nexus_container(self):
        class _FakeContainer:
            def __init__(self, status):
                self.status = status

            def reload(self):
                return None

        container = _FakeContainer("restarting")
        original = srv.nexus_container_obj
        try:
            srv.nexus_container_obj = container
            with patch.object(srv, "start_nexus_container") as mock_start, \
                 patch.object(srv, "stop_nexus_container") as mock_stop:
                srv.run_nexus_control_change({"new_val": {"id": srv.ENGINE_ID_NEXUS_GRAPH, "running": True}}, None)
            mock_start.assert_not_called()
            mock_stop.assert_not_called()
            self.assertIs(container, srv.nexus_container_obj)
        finally:
            srv.nexus_container_obj = original

    def test_server_reuses_restarting_discover_container_on_startup(self):
        class _FakeContainer:
            def __init__(self, status):
                self.status = status
                self.removed = False

            def reload(self):
                return None

            def remove(self):
                self.removed = True

        class _FakeImages:
            def get(self, image):
                return {"image": image}

        class _FakeContainers:
            def __init__(self, container):
                self._container = container

            def get(self, name):
                return self._container

            def run(self, *args, **kwargs):
                raise AssertionError("Discover container should not be recreated when status is restarting")

        class _FakeDockerClient:
            def __init__(self, container):
                self.images = _FakeImages()
                self.containers = _FakeContainers(container)

        container = _FakeContainer("restarting")
        original = srv.discover_container_obj
        try:
            srv.discover_container_obj = None
            with patch.object(srv, "_get_docker_client", return_value=_FakeDockerClient(container)), \
                 patch.object(srv, "_get_instance_network", return_value="test-net"):
                reused = srv.start_discover_container()
            self.assertIs(container, reused)
            self.assertFalse(container.removed)
            self.assertIs(container, srv.discover_container_obj)
        finally:
            srv.discover_container_obj = original

    def test_action_discover_control_set_starts_cleanly(self):
        updates = []
        fake_conn = object()

        class _FakeConfigTable:
            def get(self, key):
                self.key = key
                return self

            def update(self, update):
                updates.append(update)
                return self

            def run(self, conn):
                return {"replaced": 1}

        class _FakeDB:
            def table(self, name):
                self.table_name = name
                return _FakeConfigTable()

        class _FakeR:
            def db(self, name):
                return _FakeDB()

        doc = {"id": "discover_engine", "running": True, "terminate": False}
        with patch.object(iu, "ensure_engine_control_table"), \
             patch.object(iu, "update_engine_doc") as mock_update, \
             patch.object(iu, "get_engine_doc", return_value=doc), \
             patch.object(iu, "r", _FakeR()):
            out = iu.action_discover_control_set(fake_conn, running=True)
        mock_update.assert_called_once_with(fake_conn, "discover_engine", {"running": True, "terminate": False})
        self.assertEqual({"terminateDiscoverService": False}, updates[0])
        self.assertTrue(out["running"])
        self.assertFalse(out["terminate"])

    def test_sync_graph_edge_intervals_uses_history_nodes(self):
        class _FakeResult:
            def single(self):
                return {"synced": 3}

        class _FakeSession:
            def __init__(self):
                self.query = ""
                self.params = None

            def run(self, query, **params):
                self.query = query
                self.params = params
                return _FakeResult()

        session = _FakeSession()
        synced = gns._sync_graph_edge_intervals(
            session,
            "SUPPLIER_OF",
            source_scope="SEC_10K_SUPPLIER",
            directed=True,
        )
        self.assertEqual(3, synced)
        self.assertIn("GraphEdgeInterval", session.query)
        self.assertIn("EDGE_INTERVAL_SOURCE", session.query)
        self.assertIn("h.filing_period = r.filing_period", session.query)
        self.assertIn("h.shares = r.shares", session.query)
        self.assertEqual("SUPPLIER_OF", session.params["rel_type"])
        self.assertEqual("SEC_10K_SUPPLIER", session.params["source_scope"])

    def test_patentsview_llm_resolver_uses_structured_output(self):
        calls = []

        def _fake_structured(provider, api_key, model, prompt, output_type, **kwargs):
            calls.append(
                {
                    "provider": provider,
                    "api_key": api_key,
                    "model": model,
                    "prompt": prompt,
                    "output_type": output_type,
                    "kwargs": kwargs,
                }
            )
            return output_type(
                results=[
                    {"assignee_name": "Apple Computer, Inc.", "ticker": "AAPL"},
                    {"assignee_name": "EXTRA ORG", "ticker": "AAPL"},
                ]
            )

        with patch.object(llu, "call_structured_llm_by_provider", side_effect=_fake_structured), \
             patch.object(llu, "call_llm_by_provider", side_effect=AssertionError("raw LLM helper should not be used")), \
             patch.object(gns, "PATENTSVIEW_LLM_MAX_WORKERS", 1), \
             patch.object(gns, "PATENTSVIEW_LLM_RETRIES", 1), \
             patch.object(gns, "PATENTSVIEW_LLM_TIMEOUT_SEC", 30):
            resolved = gns._llm_resolve_patent_assignees(
                ["Apple Computer, Inc.", "National Taiwan University"],
                {
                    "AAPL": "Apple Inc",
                },
                "gemini",
                "gemini-3-flash-preview",
                "test-key",
                batch_size=20,
            )

        self.assertEqual({"Apple Computer, Inc.": "AAPL"}, resolved)
        self.assertEqual(1, len(calls))
        self.assertEqual("gemini", calls[0]["provider"])
        self.assertEqual("gemini-3-flash-preview", calls[0]["model"])
        self.assertEqual("test-key", calls[0]["api_key"])
        self.assertIn("ASSIGNEES_JSON", calls[0]["prompt"])
        self.assertNotIn("National Taiwan University", calls[0]["prompt"])
        self.assertEqual(0.0, calls[0]["kwargs"]["temperature"])

    def test_llm_resolve_patent_assignees_forces_single_worker_for_gemini(self):
        import llm_utils as llu

        log_messages = []

        def _fake_structured(_provider, _api_key, _model, _prompt, output_type, **_kwargs):
            return output_type(results=[{"assignee_name": "Apple Computer, Inc.", "ticker": "AAPL"}])

        with patch.object(llu, "call_structured_llm_by_provider", side_effect=_fake_structured), \
             patch.object(gns, "PATENTSVIEW_LLM_MAX_WORKERS", 6), \
             patch.object(gns, "PATENTSVIEW_LLM_RETRIES", 1), \
             patch.object(gns, "PATENTSVIEW_LLM_TIMEOUT_SEC", 30), \
             patch.object(gns, "_log", side_effect=lambda msg, *_args, **_kwargs: log_messages.append(msg)):
            resolved = gns._llm_resolve_patent_assignees(
                ["Apple Computer, Inc.", "Apple Computer International"],
                {"AAPL": "Apple Inc"},
                "gemini",
                "gemini-3-flash-preview",
                "test-key",
                batch_size=1,
            )

        self.assertEqual({"Apple Computer, Inc.": "AAPL"}, resolved)
        self.assertTrue(any("forcing single-worker mode" in msg for msg in log_messages))

    def test_patentsview_llm_resolver_retries_empty_batch_as_smaller_sub_batches(self):
        import llm_utils as llu

        calls = []

        def _fake_structured(_provider, _api_key, _model, prompt, output_type, **_kwargs):
            calls.append(prompt)
            if "Apple Computer International" in prompt and "Apple Computer, Inc." in prompt:
                return output_type(results=[])
            if "Apple Computer, Inc." in prompt:
                return output_type(results=[{"assignee_name": "Apple Computer, Inc.", "ticker": "AAPL"}])
            return output_type(results=[{"assignee_name": "Apple Computer International", "ticker": "AAPL"}])

        with patch.object(llu, "call_structured_llm_by_provider", side_effect=_fake_structured), \
             patch.object(gns, "PATENTSVIEW_LLM_MAX_WORKERS", 1), \
             patch.object(gns, "PATENTSVIEW_LLM_RETRIES", 1), \
             patch.object(gns, "PATENTSVIEW_LLM_TIMEOUT_SEC", 30):
            resolved = gns._llm_resolve_patent_assignees(
                ["Apple Computer, Inc.", "Apple Computer International"],
                {"AAPL": "Apple Inc"},
                "gemini",
                "gemini-3-flash-preview",
                "test-key",
                batch_size=2,
            )

        self.assertEqual({"Apple Computer, Inc.": "AAPL", "Apple Computer International": "AAPL"}, resolved)
        self.assertEqual(3, len(calls))

    def test_phase7_history_quarters_clamps_to_valid_range(self):
        self.assertEqual(1, gns._clamp_phase7_history_quarters(0))
        self.assertEqual(3, gns._clamp_phase7_history_quarters(3))
        self.assertEqual(
            gns.PHASE7_13F_MAX_HISTORY_QUARTERS,
            gns._clamp_phase7_history_quarters(gns.PHASE7_13F_MAX_HISTORY_QUARTERS + 100),
        )

    def test_phase7_zip_manifest_parser_limits_to_latest_quarters(self):
        html = """
        <a href="/files/structureddata/data/form-13f-data-sets/01dec2024-28feb2025_form13f.zip">Q1</a>
        <a href="/files/structureddata/data/form-13f-data-sets/01mar2025-31may2025_form13f.zip">Q2</a>
        <a href="/files/structureddata/data/form-13f-data-sets/01jun2025-31aug2025_form13f.zip">Q3</a>
        """
        manifests = gns._phase7_13f_extract_zip_manifests(html, 2)
        self.assertEqual(
            [
                "01mar2025-31may2025_form13f",
                "01jun2025-31aug2025_form13f",
            ],
            [item["period_key"] for item in manifests],
        )
        self.assertEqual(
            [
                "2025-05-31",
                "2025-08-31",
            ],
            [item["active_after"] for item in manifests],
        )

    def test_phase7_snapshot_valid_until_dates_follow_next_snapshot_boundary(self):
        snapshots = [
            {"active_after": "2025-02-28", "complete_snapshot": True},
            {"active_after": "2025-05-31", "complete_snapshot": True},
            {"active_after": "2025-08-31", "complete_snapshot": True},
            {"active_after": "2025-09-15", "complete_snapshot": False},
        ]
        self.assertEqual(
            ["2025-05-31", "2025-08-31", "", ""],
            gns._phase7_snapshot_valid_until_dates(snapshots),
        )

    def test_phase7_snapshot_specs_return_empty_when_history_is_already_covered(self):
        with patch.object(gns, "_fetch_13f_quarterly_zip_manifests", return_value=[]):
            headers, specs = gns._fetch_13f_holding_snapshot_specs(
                5,
                historical_start_date="2025-01-01",
                phase_manifest={"coverage_end": "2026-03-10"},
            )
        self.assertIn("User-Agent", headers)
        self.assertEqual([], specs)

    def test_phase7_ignore_existing_coverage_when_graph_has_no_holds(self):
        class _FakeResult:
            def __init__(self, rows=None, single_value=None):
                self._rows = rows or []
                self._single_value = single_value

            def __iter__(self):
                return iter(self._rows)

            def single(self):
                return self._single_value

        class _FakeSession:
            def run(self, query, **params):
                if "RETURN c.cik AS cik, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"cik": "0000789019", "ticker": "MSFT"}])
                if "source_scope: '13F_HR'" in query:
                    return _FakeResult(single_value={"count": 0})
                if "RETURN c.name AS name, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"name": "Microsoft Corporation", "ticker": "MSFT"}])
                return _FakeResult(single_value={"count": 0})

        session = _FakeSession()
        captured = {}
        gns._set_nexus_temporal_state_from_control({
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "force_bootstrap_rebuild": False,
            "historical_phase_manifests": {
                "phase7": {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
            },
        })

        def _fake_specs(history_quarters, historical_start_date="", phase_manifest=None, ignore_existing_coverage=False):
            captured["ignore_existing_coverage"] = ignore_existing_coverage
            return {"User-Agent": "test@example.com"}, []

        with patch.object(gns, "_fetch_sec_company_ticker_rows", return_value=[]), \
             patch.object(gns, "_revalidate_company_ciks_against_sec", return_value={"cleared": 0}), \
             patch.object(gns, "_nexus_historical_start_date", return_value="2025-01-01"), \
             patch.object(gns, "_nexus_phase_manifest", return_value={"bootstrap_complete": True, "coverage_end": "2026-03-10"}), \
             patch.object(gns, "_fetch_13f_holding_snapshot_specs", side_effect=_fake_specs), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_log_stage_unexpected"), \
             patch.object(gns, "_phase7_report_progress"):
            gns.phase7_13f_ownership(session)

        self.assertTrue(captured["ignore_existing_coverage"])

    def test_phase12_ignore_existing_coverage_when_graph_has_no_8k_edges(self):
        class _FakeResult:
            def __init__(self, single_value=None):
                self._single_value = single_value

            def single(self):
                return self._single_value

        class _FakeSession:
            def run(self, query, **params):
                if "SEC_8K_ITEM_1_01" in query:
                    return _FakeResult(single_value={"count": 0})
                return _FakeResult(single_value={"count": 0})

        gns._set_nexus_temporal_state_from_control({
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "force_bootstrap_rebuild": False,
            "historical_phase_manifests": {
                "phase12": {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
            },
        })
        self.assertTrue(
            gns._phase12_should_ignore_existing_coverage(
                _FakeSession(),
                {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
                "2025-01-01",
            )
        )

    def test_phase3_ignore_existing_coverage_when_graph_has_no_supply_chain_edges(self):
        class _FakeResult:
            def __init__(self, single_value=None):
                self._single_value = single_value

            def single(self):
                return self._single_value

        class _FakeSession:
            def run(self, query, **params):
                if params.get("source_scope") == "SEC_10K_SUPPLIER":
                    return _FakeResult(single_value={"count": 0})
                if params.get("source_scope") == "SEC_10K_STRATEGIC_PARTNER":
                    return _FakeResult(single_value={"count": 0})
                return _FakeResult(single_value={"count": 0})

        gns._set_nexus_temporal_state_from_control({
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "force_bootstrap_rebuild": False,
            "historical_phase_manifests": {
                "phase3": {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
            },
        })
        self.assertTrue(
            gns._phase3_should_ignore_existing_coverage(
                _FakeSession(),
                {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
                "2025-01-01",
            )
        )

    def test_phase3_ignore_existing_coverage_keeps_incremental_when_edges_still_exist(self):
        class _FakeResult:
            def __init__(self, single_value=None):
                self._single_value = single_value

            def single(self):
                return self._single_value

        class _FakeSession:
            def run(self, query, **params):
                if params.get("source_scope") == "SEC_10K_SUPPLIER":
                    return _FakeResult(single_value={"count": 12})
                if params.get("source_scope") == "SEC_10K_STRATEGIC_PARTNER":
                    return _FakeResult(single_value={"count": 0})
                return _FakeResult(single_value={"count": 0})

        gns._set_nexus_temporal_state_from_control({
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "force_bootstrap_rebuild": False,
            "historical_phase_manifests": {
                "phase3": {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
            },
        })
        self.assertFalse(
            gns._phase3_should_ignore_existing_coverage(
                _FakeSession(),
                {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
                "2025-01-01",
            )
        )

    def test_run_sec_edgar_supply_chain_scraper_rebuilds_from_history_when_phase3_graph_is_empty(self):
        captured = {}

        class _FakeSession:
            pass

        def _fake_historical(**kwargs):
            captured.update(kwargs)
            summary_cb = kwargs.get("summary_cb")
            if summary_cb:
                summary_cb({
                    "start_date": kwargs["start_date"],
                    "end_date": kwargs["end_date"],
                    "filings_processed": 0,
                    "edge_count": 0,
                    "latest_filing_date": "",
                })
            return []

        with patch.object(gns, "_get_company_tickers_from_graph", return_value=[("AAPL", "0000320193")]), \
             patch.object(gns, "_fetch_sec_company_tickers", return_value={"AAPL": "0000320193"}), \
             patch.object(gns, "_nexus_historical_enabled", return_value=True), \
             patch.object(gns, "_nexus_historical_start_date", return_value="2025-01-01"), \
             patch.object(gns, "_nexus_today_iso", return_value="2026-03-14"), \
             patch.object(gns, "_nexus_phase_manifest", return_value={"bootstrap_complete": True, "coverage_end": "2026-03-10"}), \
             patch.object(gns, "_phase3_should_ignore_existing_coverage", return_value=True), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_phase3_report_progress"), \
             patch.object(gns, "_nexus_maybe_log_eta"), \
             patch.object(gns, "_nexus_rethink_conn", object()), \
             patch("nexus_graph_engine.os.path.isfile", return_value=False), \
             patch("sec_edgar_supply_chain.run_sec_edgar_supply_chain_historical", side_effect=_fake_historical):
            path, merged_incrementally, summary = gns._run_sec_edgar_supply_chain_scraper(_FakeSession())

        self.assertTrue(merged_incrementally)
        self.assertEqual("2025-01-01", captured["start_date"])
        self.assertEqual("2026-03-14", captured["end_date"])
        self.assertTrue(captured["ignore_parsed_edge_cache"])
        self.assertTrue(captured["ignore_existing_output_csv"])
        self.assertEqual("2025-01-01", summary["start_date"])

    def test_nexus_phase_fetch_window_uses_next_day_after_coverage(self):
        with patch.object(gns, "_nexus_today_iso", return_value="2026-03-08"):
            gns._set_nexus_temporal_state_from_control({
                "historical_mode_enabled": True,
                "historical_start_date": "2024-01-01",
                "historical_phase_manifests": {
                    "phase3": {
                        "coverage_end": "2025-12-31",
                    },
                },
            })
            self.assertEqual(("2026-01-01", "2026-03-08"), gns._nexus_phase_fetch_window("phase3"))

    def test_nexus_phase_fetch_window_returns_empty_start_when_current(self):
        with patch.object(gns, "_nexus_today_iso", return_value="2026-03-08"):
            gns._set_nexus_temporal_state_from_control({
                "historical_mode_enabled": True,
                "historical_start_date": "2024-01-01",
                "historical_phase_manifests": {
                    "phase12": {
                        "last_incremental_end": "2026-03-08",
                    },
                },
            })
            self.assertEqual(("", "2026-03-08"), gns._nexus_phase_fetch_window("phase12"))

    def test_set_nexus_temporal_state_tracks_force_bootstrap_rebuild(self):
        gns._set_nexus_temporal_state_from_control({
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "force_bootstrap_rebuild": True,
            "historical_phase_manifests": {},
        })
        self.assertTrue(gns._nexus_force_bootstrap_requested())

    def test_phase3_should_ignore_existing_coverage_when_force_bootstrap_requested(self):
        class _FakeSession:
            def run(self, query, **kwargs):
                raise AssertionError("graph counts should not be queried when force bootstrap is requested")

        gns._set_nexus_temporal_state_from_control({
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "force_bootstrap_rebuild": True,
            "historical_phase_manifests": {
                "phase3": {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
            },
        })
        self.assertTrue(gns._phase3_should_ignore_existing_coverage(
            _FakeSession(),
            {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
            "2025-01-01",
        ))

    def test_phase7_should_ignore_existing_coverage_when_force_bootstrap_requested(self):
        class _FakeSession:
            def run(self, query, **kwargs):
                raise AssertionError("graph counts should not be queried when force bootstrap is requested")

        gns._set_nexus_temporal_state_from_control({
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "force_bootstrap_rebuild": True,
            "historical_phase_manifests": {
                "phase7": {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
            },
        })
        self.assertTrue(gns._phase7_should_ignore_existing_coverage(
            _FakeSession(),
            {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
            "2025-01-01",
        ))

    def test_phase12_should_ignore_existing_coverage_when_force_bootstrap_requested(self):
        class _FakeSession:
            def run(self, query, **kwargs):
                raise AssertionError("graph counts should not be queried when force bootstrap is requested")

        gns._set_nexus_temporal_state_from_control({
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "force_bootstrap_rebuild": True,
            "historical_phase_manifests": {
                "phase12": {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
            },
        })
        self.assertTrue(gns._phase12_should_ignore_existing_coverage(
            _FakeSession(),
            {"bootstrap_complete": True, "coverage_end": "2026-03-10"},
            "2025-01-01",
        ))

    def test_main_preserves_force_bootstrap_flag_until_run_build(self):
        class _FakeConn:
            def close(self):
                return None

        class _FakeDriver:
            def close(self):
                return None

        control_doc = {
            "running": True,
            "auto_update_enabled": False,
            "historical_mode_enabled": True,
            "historical_start_date": "2025-01-01",
            "force_bootstrap_rebuild": True,
            "historical_bootstrap_complete": False,
            "historical_phase_manifests": {},
        }
        observed = {"force_flag_during_run_build": None}

        def _fake_update(_conn, update):
            control_doc.update(update)

        def _fake_run_build(_driver, _conn):
            observed["force_flag_during_run_build"] = bool(control_doc.get("force_bootstrap_rebuild"))
            return True

        with ExitStack() as stack:
            stack.enter_context(patch.object(gns, "wait_for_neo4j", return_value=(_FakeDriver(), True)))
            stack.enter_context(patch.object(gns, "_get_rethink_conn", return_value=_FakeConn()))
            stack.enter_context(patch.object(gns, "_ensure_progress_table"))
            stack.enter_context(patch.object(gns, "_nexus_control_want_stop", return_value=False))
            stack.enter_context(patch.object(gns, "_load_nexus_control_doc", side_effect=lambda _conn: dict(control_doc)))
            stack.enter_context(patch.object(gns, "_update_nexus_control_doc", side_effect=_fake_update))
            stack.enter_context(patch.object(gns, "_run_build", side_effect=_fake_run_build))
            stack.enter_context(patch.object(gns, "_progress"))
            stack.enter_context(patch.object(gns, "_log"))
            stack.enter_context(patch.object(gns.time, "sleep", return_value=None))
            with self.assertRaises(SystemExit):
                gns.main()

        self.assertTrue(observed["force_flag_during_run_build"])
        self.assertFalse(control_doc.get("force_bootstrap_rebuild"))

    def test_sync_graph_edge_intervals_retries_with_smaller_batch_after_memory_error(self):
        class _FakeResult:
            def __init__(self, synced):
                self._synced = synced

            def single(self):
                return {"synced": self._synced}

        class _FakeSession:
            def __init__(self):
                self.calls = []

            def run(self, query, **params):
                self.calls.append({"query": query, "params": dict(params)})
                batch_limit = params["batch_limit"]
                if len(self.calls) == 1:
                    raise Exception(
                        "{neo4j_code: Neo.TransientError.General.MemoryPoolOutOfMemoryError} "
                        "{message: dbms.memory.transaction.total.max threshold reached}"
                    )
                if len(self.calls) == 2:
                    return _FakeResult(batch_limit)
                return _FakeResult(125)

        session = _FakeSession()
        with patch.object(gns, "_log"):
            synced = gns._sync_graph_edge_intervals(
                session,
                "HOLDS",
                source_scope="13F_HR",
                directed=True,
                batch_limit=2000,
            )

        self.assertEqual(1125, synced)
        self.assertEqual([2000, 1000, 1000], [call["params"]["batch_limit"] for call in session.calls])
        self.assertTrue(all("history_sync_token" in call["query"] for call in session.calls[1:]))

    def test_sync_graph_edge_intervals_can_consume_run_token_for_index_friendly_batches(self):
        class _FakeResult:
            def __init__(self, synced):
                self._synced = synced

            def single(self):
                return {"synced": self._synced}

        class _FakeSession:
            def __init__(self):
                self.calls = []

            def run(self, query, **params):
                self.calls.append({"query": query, "params": dict(params)})
                if len(self.calls) == 1:
                    return _FakeResult(750)
                return _FakeResult(125)

        session = _FakeSession()
        with patch.object(gns, "_log"):
            synced = gns._sync_graph_edge_intervals(
                session,
                "HOLDS",
                source_scope="13F_HR",
                directed=True,
                run_token="phase7:test",
                batch_limit=750,
                consume_run_token=True,
            )

        self.assertEqual(875, synced)
        first_query = session.calls[0]["query"]
        self.assertIn("coalesce(r.current_run_token, '') = $run_token", first_query)
        self.assertIn("WHEN $consume_run_token THEN NULL", first_query)
        self.assertIn("WHEN $consume_run_token THEN $run_token", first_query)
        self.assertNotIn("coalesce(r.history_sync_token, '') <> $sync_token", first_query)
        self.assertTrue(all(call["params"]["consume_run_token"] for call in session.calls))

    def test_phase7_13f_batch_write_handles_cypher_map_literals(self):
        class _FakeResult(list):
            def __init__(self, rows=None, single_value=None):
                super().__init__(rows or [])
                self._single_value = single_value

            def single(self):
                return self._single_value

            def consume(self):
                return None

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **params):
                self.queries.append((query, params))
                if "RETURN c.cik AS cik, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"cik": "0000789019", "ticker": "MSFT"}])
                if "RETURN c.name AS name, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"name": "Microsoft Corporation", "ticker": "MSFT"}])
                if "RETURN count(r) AS closed" in query:
                    return _FakeResult(single_value={"closed": 0})
                if "RETURN count(r) AS stale_count" in query:
                    return _FakeResult(single_value={"stale_count": 0})
                if "RETURN count(r) AS total" in query:
                    return _FakeResult(single_value={"total": 1})
                return _FakeResult(single_value={})

        session = _FakeSession()
        snapshots = [{
            "period_key": "01sep2025-30nov2025_form13f",
            "active_after": "2025-12-31",
            "complete_snapshot": True,
            "label": "01sep2025-30nov2025_form13f.zip",
            "edges": [{
                "node_a": "CIK0001067983",
                "properties": {
                    "institution_name": "Berkshire Hathaway Inc",
                    "name_of_issuer": "Microsoft Corporation",
                    "value_usd": 123456.0,
                    "shares": 1000,
                    "active_after": "2025-12-31",
                    "filing_period": "01sep2025-30nov2025_form13f",
                },
            }],
        }]

        with patch.object(gns, "_fetch_sec_company_ticker_rows", return_value=[]), \
             patch.object(gns, "_revalidate_company_ciks_against_sec", return_value={"cleared": 0}), \
             patch.object(
                 gns,
                 "_fetch_13f_holding_snapshot_specs",
                 return_value=(
                     {"User-Agent": "test@example.com"},
                     [
                         {
                             "fetch_mode": "zip",
                             "period_key": "01sep2025-30nov2025_form13f",
                             "active_after": "2025-12-31",
                             "complete_snapshot": True,
                             "label": "01sep2025-30nov2025_form13f.zip",
                         }
                     ],
                 ),
             ), \
             patch.object(gns, "_fetch_13f_holdings_snapshot_from_spec", return_value=snapshots[0]), \
             patch.object(gns, "_sync_graph_edge_intervals", return_value=0), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_nexus_maybe_log_eta"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_log_stage_error"), \
             patch.object(gns, "_log_stage_unexpected"):
            gns.phase7_13f_ownership(session)

        self.assertTrue(
            any("MERGE (inst:Institution {id: p.inst_id})" in query for query, _ in session.queries)
        )
        self.assertTrue(
            any("MATCH (c:Company {ticker: p.ticker})" in query for query, _ in session.queries)
        )
        self.assertTrue(
            any("filing_period: p.period_key" in query for query, _ in session.queries)
        )
        self.assertTrue(
            any("CASE WHEN p.valid_until = '' THEN NULL ELSE p.valid_until END" in query for query, _ in session.queries)
        )
        self.assertTrue(
            any("r.target_ticker = p.ticker" in query for query, _ in session.queries)
        )

    def test_phase7_inflation_check_uses_escaped_scope_literal_and_no_name_error(self):
        class _FakeResult(list):
            def __init__(self, rows=None, single_value=None):
                super().__init__(rows or [])
                self._single_value = single_value

            def single(self):
                return self._single_value

            def consume(self):
                return None

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **params):
                self.queries.append((query, params))
                if "RETURN c.cik AS cik, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"cik": "0000789019", "ticker": "MSFT"}])
                if "RETURN c.name AS name, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"name": "Microsoft Corporation", "ticker": "MSFT"}])
                if "RETURN count(r) AS closed" in query:
                    return _FakeResult(single_value={"closed": 0})
                if "RETURN count(r) AS stale_count" in query:
                    return _FakeResult(single_value={"stale_count": 0})
                if "RETURN count(r) AS total" in query:
                    return _FakeResult(single_value={"total": 1})
                return _FakeResult(single_value={})

        session = _FakeSession()
        log_messages = []
        snapshot = {
            "period_key": "01dec2025-28feb2026_form13f",
            "active_after": "2026-02-28",
            "complete_snapshot": True,
            "label": "01dec2025-28feb2026_form13f.zip",
            "edges": [
                {
                    "node_a": "CIK0001067983",
                    "properties": {
                        "institution_name": "Clearbridge Investments, LLC",
                        "name_of_issuer": "Microsoft Corporation",
                        "value_usd": 5597060.0,
                        "shares": 14910,
                        "active_after": "2026-02-28",
                        "filing_period": "01dec2025-28feb2026_form13f",
                    },
                }
            ],
        }

        with patch.object(gns, "_fetch_sec_company_ticker_rows", return_value=[]), \
             patch.object(gns, "_revalidate_company_ciks_against_sec", return_value={"cleared": 0}), \
             patch.object(
                 gns,
                 "_fetch_13f_holding_snapshot_specs",
                 return_value=(
                     {"User-Agent": "test@example.com"},
                     [
                         {
                             "fetch_mode": "zip",
                             "period_key": "01dec2025-28feb2026_form13f",
                             "active_after": "2026-02-28",
                             "complete_snapshot": True,
                             "label": "01dec2025-28feb2026_form13f.zip",
                         }
                     ],
                 ),
             ), \
             patch.object(gns, "_fetch_13f_holdings_snapshot_from_spec", return_value=snapshot), \
             patch.object(gns, "_sync_graph_edge_intervals", return_value=0), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_nexus_maybe_log_eta"), \
             patch.object(gns, "_log", side_effect=lambda msg, *_args, **_kwargs: log_messages.append(msg)), \
             patch.object(gns, "_log_stage_error"), \
             patch.object(gns, "_log_stage_unexpected"):
            gns.phase7_13f_ownership(session)

        self.assertFalse(any("Inflation check error" in msg for msg in log_messages))
        self.assertTrue(
            any("MATCH ()-[r:HOLDS {source_scope: '13F_HR'}]->()" in query for query, _ in session.queries)
        )

    def test_phase7_interval_sync_consumes_run_token_with_faster_batch_defaults(self):
        class _FakeResult(list):
            def __init__(self, rows=None, single_value=None):
                super().__init__(rows or [])
                self._single_value = single_value

            def single(self):
                return self._single_value

            def consume(self):
                return None

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **params):
                self.queries.append((query, params))
                if "RETURN c.cik AS cik, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"cik": "0000789019", "ticker": "MSFT"}])
                if "RETURN c.name AS name, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"name": "Microsoft Corporation", "ticker": "MSFT"}])
                if "RETURN count(r) AS closed" in query:
                    return _FakeResult(single_value={"closed": 0})
                if "RETURN count(r) AS stale_count" in query:
                    return _FakeResult(single_value={"stale_count": 0})
                if "RETURN count(r) AS total" in query:
                    return _FakeResult(single_value={"total": 1})
                return _FakeResult(single_value={})

        session = _FakeSession()
        snapshot = {
            "period_key": "01dec2025-28feb2026_form13f",
            "active_after": "2026-02-28",
            "complete_snapshot": True,
            "label": "01dec2025-28feb2026_form13f.zip",
            "edges": [
                {
                    "node_a": "CIK0001067983",
                    "properties": {
                        "institution_name": "Clearbridge Investments, LLC",
                        "name_of_issuer": "Microsoft Corporation",
                        "value_usd": 5597060.0,
                        "shares": 14910,
                        "active_after": "2026-02-28",
                        "filing_period": "01dec2025-28feb2026_form13f",
                    },
                }
            ],
        }

        with patch.object(gns, "_fetch_sec_company_ticker_rows", return_value=[]), \
             patch.object(gns, "_revalidate_company_ciks_against_sec", return_value={"cleared": 0}), \
             patch.object(
                 gns,
                 "_fetch_13f_holding_snapshot_specs",
                 return_value=(
                     {"User-Agent": "test@example.com"},
                     [
                         {
                             "fetch_mode": "zip",
                             "period_key": "01dec2025-28feb2026_form13f",
                             "active_after": "2026-02-28",
                             "complete_snapshot": True,
                             "label": "01dec2025-28feb2026_form13f.zip",
                         }
                     ],
                 ),
             ), \
             patch.object(gns, "_fetch_13f_holdings_snapshot_from_spec", return_value=snapshot), \
             patch.object(gns, "_sync_graph_edge_intervals", return_value=0) as sync_mock, \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_nexus_maybe_log_eta"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_log_stage_error"), \
             patch.object(gns, "_log_stage_unexpected"):
            gns.phase7_13f_ownership(session)

        sync_mock.assert_called_once()
        self.assertEqual("HOLDS", sync_mock.call_args.args[1])
        self.assertEqual("13F_HR", sync_mock.call_args.kwargs["source_scope"])
        self.assertTrue(sync_mock.call_args.kwargs["consume_run_token"])
        self.assertEqual(gns.PHASE7_HOLDS_INTERVAL_SYNC_BATCH_SIZE, sync_mock.call_args.kwargs["batch_limit"])
        self.assertEqual(gns.PHASE7_HOLDS_INTERVAL_SYNC_PROGRESS_EVERY, sync_mock.call_args.kwargs["progress_every"])

    def test_phase7_snapshot_weight_pct_real_world_holdings_cases(self):
        period_q4 = "01sep2025-30nov2025_form13f"
        period_q3 = "01jun2025-31aug2025_form13f"
        edges = [
            {"node_a": "cik_0001067983", "properties": {"institution_name": "Berkshire Hathaway Inc", "name_of_issuer": "Microsoft Corporation", "value_usd": 123456.0, "filing_period": period_q4}},
            {"node_a": "cik_0001067983", "properties": {"institution_name": "Berkshire Hathaway Inc", "name_of_issuer": "Apple Inc", "value_usd": 234567.0, "filing_period": period_q4}},
            {"node_a": "cik_0001067983", "properties": {"institution_name": "Berkshire Hathaway Inc", "name_of_issuer": "Amazon.com, Inc.", "value_usd": 345678.0, "filing_period": period_q4}},
            {"node_a": "cik_0000093410", "properties": {"institution_name": "STATE STREET CORP", "name_of_issuer": "Netflix, Inc.", "value_usd": 16574986091.0, "filing_period": period_q4}},
            {"node_a": "cik_0000093410", "properties": {"institution_name": "STATE STREET CORP", "name_of_issuer": "Verizon Communications Inc.", "value_usd": 9080810481.0, "filing_period": period_q4}},
            {"node_a": "cik_0000093410", "properties": {"institution_name": "STATE STREET CORP", "name_of_issuer": "AT&T Inc.", "value_usd": 8249108719.0, "filing_period": period_q4}},
            {"node_a": "cik_0000093410", "properties": {"institution_name": "STATE STREET CORP", "name_of_issuer": "Intel Corporation", "value_usd": 7695007330.0, "filing_period": period_q4}},
            {"node_a": "cik_0000093410", "properties": {"institution_name": "STATE STREET CORP", "name_of_issuer": "Pfizer Inc.", "value_usd": 7481084584.0, "filing_period": period_q4}},
            {"node_a": "cik_0000317788", "properties": {"institution_name": "FMR LLC", "name_of_issuer": "Netflix, Inc.", "value_usd": 12898859336.0, "filing_period": period_q3}},
            {"node_a": "cik_0000317788", "properties": {"institution_name": "FMR LLC", "name_of_issuer": "Boston Scientific Corporation", "value_usd": 6796624090.0, "filing_period": period_q3}},
            {"node_a": "cik_0001534929", "properties": {"institution_name": "GEODE CAPITAL MANAGEMENT, LLC", "name_of_issuer": "Netflix, Inc.", "value_usd": 8155611480.0, "filing_period": period_q3}},
            {"node_a": "cik_0001534929", "properties": {"institution_name": "GEODE CAPITAL MANAGEMENT, LLC", "name_of_issuer": "Wells Fargo & Company", "value_usd": 5831112949.0, "filing_period": period_q3}},
            {"node_a": "cik_0000902732", "properties": {"institution_name": "Capital World Investors", "name_of_issuer": "Netflix, Inc.", "value_usd": 8376656161.0, "filing_period": period_q3}},
            {"node_a": "cik_0000902732", "properties": {"institution_name": "Capital World Investors", "name_of_issuer": "Starbucks Corporation", "value_usd": 7135227538.0, "filing_period": period_q3}},
            {"node_a": "cik_0000102909", "properties": {"institution_name": "VANGUARD GROUP INC", "name_of_issuer": "Bristol-Myers Squibb Company", "value_usd": 9593143343.0, "filing_period": period_q3}},
            {"node_a": "cik_0000102909", "properties": {"institution_name": "VANGUARD GROUP INC", "name_of_issuer": "Starbucks Corporation", "value_usd": 8666417750.0, "filing_period": period_q3}},
        ]
        for edge in edges:
            edge["properties"].setdefault("shares", 1)
        totals = gns._phase7_snapshot_institution_value_totals(edges)
        expected_cases = [
            ("cik_0001067983", period_q4, 123456.0, round(123456.0 / (123456.0 + 234567.0 + 345678.0) * 100.0, 6), "Berkshire Hathaway Inc / Microsoft Corporation"),
            ("cik_0001067983", period_q4, 234567.0, round(234567.0 / (123456.0 + 234567.0 + 345678.0) * 100.0, 6), "Berkshire Hathaway Inc / Apple Inc"),
            ("cik_0001067983", period_q4, 345678.0, round(345678.0 / (123456.0 + 234567.0 + 345678.0) * 100.0, 6), "Berkshire Hathaway Inc / Amazon.com, Inc."),
            ("cik_0000093410", period_q4, 16574986091.0, round(16574986091.0 / (16574986091.0 + 9080810481.0 + 8249108719.0 + 7695007330.0 + 7481084584.0) * 100.0, 6), "STATE STREET CORP / Netflix, Inc."),
            ("cik_0000093410", period_q4, 9080810481.0, round(9080810481.0 / (16574986091.0 + 9080810481.0 + 8249108719.0 + 7695007330.0 + 7481084584.0) * 100.0, 6), "STATE STREET CORP / Verizon Communications Inc."),
            ("cik_0000093410", period_q4, 8249108719.0, round(8249108719.0 / (16574986091.0 + 9080810481.0 + 8249108719.0 + 7695007330.0 + 7481084584.0) * 100.0, 6), "STATE STREET CORP / AT&T Inc."),
            ("cik_0000093410", period_q4, 7695007330.0, round(7695007330.0 / (16574986091.0 + 9080810481.0 + 8249108719.0 + 7695007330.0 + 7481084584.0) * 100.0, 6), "STATE STREET CORP / Intel Corporation"),
            ("cik_0000093410", period_q4, 7481084584.0, round(7481084584.0 / (16574986091.0 + 9080810481.0 + 8249108719.0 + 7695007330.0 + 7481084584.0) * 100.0, 6), "STATE STREET CORP / Pfizer Inc."),
            ("cik_0000317788", period_q3, 12898859336.0, round(12898859336.0 / (12898859336.0 + 6796624090.0) * 100.0, 6), "FMR LLC / Netflix, Inc."),
            ("cik_0000317788", period_q3, 6796624090.0, round(6796624090.0 / (12898859336.0 + 6796624090.0) * 100.0, 6), "FMR LLC / Boston Scientific Corporation"),
            ("cik_0001534929", period_q3, 8155611480.0, round(8155611480.0 / (8155611480.0 + 5831112949.0) * 100.0, 6), "GEODE CAPITAL MANAGEMENT, LLC / Netflix, Inc."),
            ("cik_0001534929", period_q3, 5831112949.0, round(5831112949.0 / (8155611480.0 + 5831112949.0) * 100.0, 6), "GEODE CAPITAL MANAGEMENT, LLC / Wells Fargo & Company"),
            ("cik_0000902732", period_q3, 8376656161.0, round(8376656161.0 / (8376656161.0 + 7135227538.0) * 100.0, 6), "Capital World Investors / Netflix, Inc."),
            ("cik_0000902732", period_q3, 7135227538.0, round(7135227538.0 / (8376656161.0 + 7135227538.0) * 100.0, 6), "Capital World Investors / Starbucks Corporation"),
            ("cik_0000102909", period_q3, 9593143343.0, round(9593143343.0 / (9593143343.0 + 8666417750.0) * 100.0, 6), "VANGUARD GROUP INC / Bristol-Myers Squibb Company"),
            ("cik_0000102909", period_q3, 8666417750.0, round(8666417750.0 / (9593143343.0 + 8666417750.0) * 100.0, 6), "VANGUARD GROUP INC / Starbucks Corporation"),
        ]

        for inst_id, period_key, value_usd, expected_pct, label in expected_cases:
            with self.subTest(label=label):
                self.assertAlmostEqual(
                    expected_pct,
                    gns._phase7_snapshot_weight_pct(inst_id, period_key, value_usd, totals),
                    places=6,
                )

    def test_phase7_13f_batch_write_populates_weight_pct_from_snapshot_totals(self):
        class _FakeResult(list):
            def __init__(self, rows=None, single_value=None):
                super().__init__(rows or [])
                self._single_value = single_value

            def single(self):
                return self._single_value

            def consume(self):
                return None

        class _FakeSession:
            def __init__(self):
                self.queries = []
                self.holds_batch = None

            def run(self, query, **params):
                self.queries.append((query, params))
                if "RETURN c.cik AS cik, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"cik": "0000789019", "ticker": "MSFT"}, {"cik": "0000320193", "ticker": "AAPL"}])
                if "RETURN c.name AS name, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"name": "Microsoft Corporation", "ticker": "MSFT"}, {"name": "Apple Inc", "ticker": "AAPL"}])
                if "MERGE (inst)-[r:HOLDS {source_scope: '13F_HR', filing_period: p.period_key}]->(c)" in query:
                    self.holds_batch = list(params.get("batch") or [])
                    return _FakeResult(single_value={})
                if "RETURN count(r) AS closed" in query:
                    return _FakeResult(single_value={"closed": 0})
                if "RETURN count(r) AS stale_count" in query:
                    return _FakeResult(single_value={"stale_count": 0})
                if "RETURN count(r) AS total" in query:
                    return _FakeResult(single_value={"total": 1})
                return _FakeResult(single_value={})

        session = _FakeSession()
        period_key = "01sep2025-30nov2025_form13f"
        snapshot = {
            "period_key": period_key,
            "active_after": "2025-12-31",
            "complete_snapshot": True,
            "label": "01sep2025-30nov2025_form13f.zip",
            "edges": [
                {
                    "node_a": "CIK0001067983",
                    "properties": {
                        "institution_name": "Berkshire Hathaway Inc",
                        "name_of_issuer": "Microsoft Corporation",
                        "value_usd": 120000.0,
                        "shares": 1000,
                        "active_after": "2025-12-31",
                        "filing_period": period_key,
                    },
                },
                {
                    "node_a": "CIK0001067983",
                    "properties": {
                        "institution_name": "Berkshire Hathaway Inc",
                        "name_of_issuer": "Apple Inc",
                        "value_usd": 80000.0,
                        "shares": 800,
                        "active_after": "2025-12-31",
                        "filing_period": period_key,
                    },
                },
                {
                    "node_a": "CIK0001067983",
                    "properties": {
                        "institution_name": "Berkshire Hathaway Inc",
                        "name_of_issuer": "Private Holdings LLC",
                        "value_usd": 100000.0,
                        "shares": 500,
                        "active_after": "2025-12-31",
                        "filing_period": period_key,
                    },
                },
            ],
        }

        with patch.object(gns, "_fetch_sec_company_ticker_rows", return_value=[]), \
             patch.object(gns, "_revalidate_company_ciks_against_sec", return_value={"cleared": 0}), \
             patch.object(
                 gns,
                 "_fetch_13f_holding_snapshot_specs",
                 return_value=(
                     {"User-Agent": "test@example.com"},
                     [{"fetch_mode": "zip", "period_key": period_key, "active_after": "2025-12-31", "complete_snapshot": True, "label": "01sep2025-30nov2025_form13f.zip"}],
                 ),
             ), \
             patch.object(gns, "_fetch_13f_holdings_snapshot_from_spec", return_value=snapshot), \
             patch.object(gns, "_sync_graph_edge_intervals", return_value=0), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_nexus_maybe_log_eta"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_log_stage_error"), \
             patch.object(gns, "_log_stage_unexpected"):
            gns.phase7_13f_ownership(session)

        self.assertIsNotNone(session.holds_batch)
        self.assertTrue(any("r.weight_pct" in query for query, _ in session.queries))
        by_ticker = {row["ticker"]: row for row in session.holds_batch}
        self.assertAlmostEqual(60.0, by_ticker["MSFT"]["weight_pct"], places=6)
        self.assertAlmostEqual(40.0, by_ticker["AAPL"]["weight_pct"], places=6)
        self.assertEqual({"MSFT", "AAPL"}, set(by_ticker))

    def test_phase7_snapshot_resolved_value_totals_use_write_path_real_world_cases(self):
        period_key = "01dec2024-28feb2025_form13f"
        edges = [
            {
                "node_a": "cik_0001067983",
                "properties": {
                    "institution_name": "Berkshire Hathaway Inc.",
                    "name_of_issuer": "Apple Inc.",
                    "value_usd": 8692034000.0,
                    "shares": 30000000,
                    "filing_period": period_key,
                },
            },
            {
                "node_a": "cik_0001067983",
                "properties": {
                    "institution_name": "Berkshire Hathaway Inc.",
                    "name_of_issuer": "American Express Company",
                    "value_usd": 3456789000.0,
                    "shares": 14400000,
                    "filing_period": period_key,
                },
            },
            {
                "node_a": "cik_0001364742",
                "properties": {
                    "institution_name": "BlackRock Inc.",
                    "name_of_issuer": "Apple Inc.",
                    "value_usd": 278450000000.0,
                    "shares": 1265000000,
                    "filing_period": period_key,
                },
            },
            {
                "node_a": "cik_0001608709",
                "properties": {
                    "institution_name": "Norges Bank",
                    "name_of_issuer": "NVIDIA Corporation",
                    "value_usd": 131550000000.0,
                    "shares": 1854000000,
                    "filing_period": period_key,
                },
            },
            {
                "node_a": "cik_0000886982",
                "properties": {
                    "institution_name": "Goldman Sachs Group Inc.",
                    "name_of_issuer": "Visa Inc.",
                    "value_usd": 98220000000.0,
                    "shares": 322000000,
                    "filing_period": period_key,
                },
            },
            {
                "node_a": "cik_0000317788",
                "properties": {
                    "institution_name": "FMR LLC",
                    "name_of_issuer": "Netflix, Inc.",
                    "value_usd": 12898859336.0,
                    "shares": 18290000,
                    "filing_period": period_key,
                },
            },
            {
                "node_a": "cik_0000093410",
                "properties": {
                    "institution_name": "STATE STREET CORP",
                    "name_of_issuer": "Microsoft Corporation",
                    "value_usd": 16574986091.0,
                    "shares": 152200000,
                    "filing_period": period_key,
                },
            },
            {
                "node_a": "cik_0000093410",
                "properties": {
                    "institution_name": "STATE STREET CORP",
                    "name_of_issuer": "Unresolved Private Holdings LLC",
                    "value_usd": 999999999.0,
                    "shares": 1200,
                    "filing_period": period_key,
                },
            },
            {
                "node_a": "cik_0000019617",
                "properties": {
                    "institution_name": "JPMorgan Chase & Co.",
                    "name_of_issuer": "Booking Holdings Inc.",
                    "value_usd": 4025300000.0,
                    "shares": 763000,
                    "filing_period": period_key,
                },
            },
            {
                "node_a": "cik_0000019617",
                "properties": {
                    "institution_name": "JPMorgan Chase & Co.",
                    "name_of_issuer": "Alphabet Inc.",
                    "value_usd": 5542300000.0,
                    "shares": 37210000,
                    "filing_period": period_key,
                },
            },
            {
                "node_a": "cik_000886982",
                "properties": {
                    "institution_name": "Capital World Investors",
                    "name_of_issuer": "Starbucks Corporation",
                    "value_usd": 7135227538.0,
                    "shares": 73800000,
                    "filing_period": period_key,
                },
            },
            {
                "node_a": "cik_000886982",
                "properties": {
                    "institution_name": "Capital World Investors",
                    "name_of_issuer": "Netflix, Inc.",
                    "value_usd": 8376656161.0,
                    "shares": 11870000,
                    "filing_period": period_key,
                },
            },
        ]

        resolution_map = {
            "Apple Inc.": "AAPL",
            "American Express Company": "AXP",
            "NVIDIA Corporation": "NVDA",
            "Visa Inc.": "V",
            "Netflix, Inc.": "NFLX",
            "Microsoft Corporation": "MSFT",
            "Booking Holdings Inc.": "BKNG",
            "Alphabet Inc.": "GOOGL",
            "Starbucks Corporation": "SBUX",
        }

        totals = gns._phase7_snapshot_resolved_value_totals(
            edges,
            period_key,
            lambda issuer_name: resolution_map.get(str(issuer_name or "")),
        )

        self.assertEqual(
            8692034000.0 + 3456789000.0,
            totals[("cik_0001067983", period_key)],
        )
        self.assertEqual(
            278450000000.0,
            totals[("cik_0001364742", period_key)],
        )
        self.assertEqual(
            131550000000.0,
            totals[("cik_0001608709", period_key)],
        )
        self.assertEqual(
            98220000000.0,
            totals[("cik_0000886982", period_key)],
        )
        self.assertEqual(
            12898859336.0,
            totals[("cik_0000317788", period_key)],
        )
        self.assertEqual(
            16574986091.0,
            totals[("cik_0000093410", period_key)],
        )
        self.assertEqual(
            4025300000.0 + 5542300000.0,
            totals[("cik_0000019617", period_key)],
        )
        self.assertEqual(
            7135227538.0 + 8376656161.0,
            totals[("cik_000886982", period_key)],
        )
        self.assertNotIn(("cik_0000093410", "unresolved"), totals)

    def test_phase7_backfill_snapshot_weight_pct_batches_real_world_portfolio_totals(self):
        class _FakeResult:
            def __init__(self, single_value=None):
                self._single_value = single_value or {}

            def single(self):
                return self._single_value

        class _FakeSession:
            def __init__(self):
                self.calls = []

            def run(self, query, **params):
                self.calls.append((query, params))
                return _FakeResult({"updated": len(params.get("batch") or [])})

        session = _FakeSession()
        period_q4 = "01sep2025-30nov2025_form13f"
        period_q3 = "01jun2025-31aug2025_form13f"
        real_world_totals = [
            ("Berkshire Hathaway Inc.", "cik_0001067983", period_q4, 123456.0 + 234567.0 + 345678.0),
            ("STATE STREET CORP", "cik_0000093410", period_q4, 16574986091.0 + 9080810481.0 + 8249108719.0 + 7695007330.0 + 7481084584.0),
            ("FMR LLC", "cik_0000317788", period_q3, 12898859336.0 + 6796624090.0),
            ("GEODE CAPITAL MANAGEMENT, LLC", "cik_0001534929", period_q3, 8155611480.0 + 5831112949.0),
            ("Capital World Investors", "cik_0000902732", period_q3, 8376656161.0 + 7135227538.0),
            ("VANGUARD GROUP INC", "cik_0000102909", period_q3, 9593143343.0 + 8666417750.0),
            ("BlackRock Inc.", "cik_0001364742", period_q4, 278450000000.0),
            ("Wellington Management Group LLP", "cik_0001037389", period_q4, 182340000000.0),
            ("T. Rowe Price Associates, Inc.", "cik_0001113169", period_q4, 146920000000.0),
            ("Norges Bank", "cik_0001608709", period_q4, 131550000000.0),
            ("JPMorgan Chase & Co.", "cik_0000019617", period_q4, 120880000000.0),
            ("Goldman Sachs Group Inc.", "cik_0000886982", period_q4, 98220000000.0),
        ]
        totals = {
            (inst_id, period_key): total_value
            for _label, inst_id, period_key, total_value in real_world_totals
        }

        updated = gns._phase7_backfill_snapshot_weight_pct(session, "phase7:test", totals)

        self.assertEqual(len(real_world_totals), updated)
        self.assertEqual(1, len(session.calls))
        query, params = session.calls[0]
        self.assertIn("MATCH (:Institution {id: p.inst_id})-[r:HOLDS {source_scope: '13F_HR', filing_period: p.period_key}]->(:Company)", query)
        self.assertIn("SET r.weight_pct = CASE", query)
        self.assertIn("toFloat(r.value_usd) / p.total_value", query)
        self.assertEqual("phase7:test", params["run_token"])
        batch_rows = {(row["inst_id"], row["period_key"]): row["total_value"] for row in params["batch"]}
        self.assertEqual(len(real_world_totals), len(batch_rows))
        for label, inst_id, period_key, total_value in real_world_totals:
            with self.subTest(label=label):
                self.assertAlmostEqual(total_value, batch_rows[(inst_id, period_key)], places=6)

    def test_phase7_snapshot_institution_value_totals_skips_real_world_zero_share_rows(self):
        period_key = "01dec2025-28feb2026_form13f"
        edges = [
            {
                "node_a": "cik_000196001",
                "properties": {
                    "institution_name": "Cercano Management LLC",
                    "name_of_issuer": "Microsoft Corporation",
                    "value_usd": 32164599000.0,
                    "shares": 0,
                },
            },
            {
                "node_a": "cik_000902732",
                "properties": {
                    "institution_name": "Prairie Wealth Advisors, Inc.",
                    "name_of_issuer": "Alphabet Inc.",
                    "value_usd": 5952855000.0,
                    "shares": 0,
                },
            },
            {
                "node_a": "cik_0001843211",
                "properties": {
                    "institution_name": "StoryOne LLC",
                    "name_of_issuer": "Apple Inc.",
                    "value_usd": 5228423000.0,
                    "shares": 0,
                },
            },
            {
                "node_a": "cik_0001330827",
                "properties": {
                    "institution_name": "IFP Advisors, Inc.",
                    "name_of_issuer": "Aaon, Inc.",
                    "value_usd": 91197000.0,
                    "shares": 0,
                },
            },
            {
                "node_a": "cik_0001364742",
                "properties": {
                    "institution_name": "BlackRock Inc.",
                    "name_of_issuer": "Apple Inc.",
                    "value_usd": 278450000000.0,
                    "shares": 1265000000,
                },
            },
            {
                "node_a": "cik_0001364742",
                "properties": {
                    "institution_name": "BlackRock Inc.",
                    "name_of_issuer": "Microsoft Corporation",
                    "value_usd": 301220000000.0,
                    "shares": 820300000,
                },
            },
            {
                "node_a": "cik_0001067983",
                "properties": {
                    "institution_name": "Berkshire Hathaway Inc.",
                    "name_of_issuer": "Apple Inc.",
                    "value_usd": 8692034000.0,
                    "shares": 30000000,
                },
            },
            {
                "node_a": "cik_0001067983",
                "properties": {
                    "institution_name": "Berkshire Hathaway Inc.",
                    "name_of_issuer": "American Express Company",
                    "value_usd": 3456789000.0,
                    "shares": 14400000,
                },
            },
            {
                "node_a": "cik_0001608709",
                "properties": {
                    "institution_name": "Norges Bank",
                    "name_of_issuer": "NVIDIA Corporation",
                    "value_usd": 131550000000.0,
                    "shares": 1854000000,
                },
            },
            {
                "node_a": "cik_0001608709",
                "properties": {
                    "institution_name": "Norges Bank",
                    "name_of_issuer": "Broadcom Inc.",
                    "value_usd": 55210000000.0,
                    "shares": 118000000,
                },
            },
            {
                "node_a": "cik_000886982",
                "properties": {
                    "institution_name": "Goldman Sachs Group Inc.",
                    "name_of_issuer": "Visa Inc.",
                    "value_usd": 98220000000.0,
                    "shares": 322000000,
                },
            },
            {
                "node_a": "cik_000886982",
                "properties": {
                    "institution_name": "Goldman Sachs Group Inc.",
                    "name_of_issuer": "Palantir Technologies Inc.",
                    "value_usd": 8123000000.0,
                    "shares": 144500000,
                },
            },
        ]

        totals = gns._phase7_snapshot_institution_value_totals(edges, period_key)

        self.assertEqual(
            278450000000.0 + 301220000000.0,
            totals[("cik_0001364742", period_key)],
        )
        self.assertEqual(
            8692034000.0 + 3456789000.0,
            totals[("cik_0001067983", period_key)],
        )
        self.assertEqual(
            131550000000.0 + 55210000000.0,
            totals[("cik_0001608709", period_key)],
        )
        self.assertEqual(
            98220000000.0 + 8123000000.0,
            totals[("cik_000886982", period_key)],
        )
        self.assertNotIn(("cik_000196001", period_key), totals)
        self.assertNotIn(("cik_000902732", period_key), totals)
        self.assertNotIn(("cik_0001843211", period_key), totals)
        self.assertNotIn(("cik_0001330827", period_key), totals)

    def test_phase7_delete_invalid_snapshot_rows_batches_cleanup_query(self):
        class _FakeResult:
            def __init__(self, deleted):
                self._deleted = deleted

            def single(self):
                return {"deleted": self._deleted}

        class _FakeSession:
            def __init__(self):
                self.calls = []
                self.deleted_counts = [gns.PHASE7_CLOSE_INTERVAL_BATCH_SIZE, 141]

            def run(self, query, **params):
                self.calls.append((query, params))
                return _FakeResult(self.deleted_counts.pop(0))

        session = _FakeSession()
        deleted = gns._phase7_delete_invalid_snapshot_rows(session, "01dec2025-28feb2026_form13f")

        self.assertEqual(gns.PHASE7_CLOSE_INTERVAL_BATCH_SIZE + 141, deleted)
        self.assertEqual(2, len(session.calls))
        for query, params in session.calls:
            self.assertIn("MATCH (:Institution)-[r:HOLDS {source_scope: '13F_HR', filing_period: $period_key}]->(:Company)", query)
            self.assertIn("coalesce(r.shares, 0) <= 0 OR coalesce(r.value_usd, 0) <= 0", query)
            self.assertEqual("01dec2025-28feb2026_form13f", params["period_key"])
            self.assertEqual(gns.PHASE7_CLOSE_INTERVAL_BATCH_SIZE, params["batch_limit"])

    def test_phase7_13f_ownership_uses_resolved_totals_without_neo4j_weight_recompute(self):
        class _FakeResult(list):
            def __init__(self, rows=None, single_value=None):
                super().__init__(rows or [])
                self._single_value = single_value

            def single(self):
                return self._single_value

            def consume(self):
                return None

        class _FakeSession:
            def __init__(self):
                self.queries = []
                self.holds_batch = None

            def run(self, query, **params):
                self.queries.append((query, params))
                if "RETURN c.cik AS cik, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"cik": "0000789019", "ticker": "MSFT"}, {"cik": "0000320193", "ticker": "AAPL"}])
                if "RETURN c.name AS name, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"name": "Microsoft Corporation", "ticker": "MSFT"}, {"name": "Apple Inc", "ticker": "AAPL"}])
                if "MERGE (inst)-[r:HOLDS {source_scope: '13F_HR', filing_period: p.period_key}]->(c)" in query:
                    self.holds_batch = list(params.get("batch") or [])
                    return _FakeResult(single_value={})
                if "RETURN count(r) AS closed" in query:
                    return _FakeResult(single_value={"closed": 0})
                if "RETURN count(r) AS stale_count" in query:
                    return _FakeResult(single_value={"stale_count": 0})
                if "RETURN count(r) AS total" in query:
                    return _FakeResult(single_value={"total": 1})
                return _FakeResult(single_value={})

        session = _FakeSession()
        period_key = "01sep2025-30nov2025_form13f"
        snapshot = {
            "period_key": period_key,
            "active_after": "2025-12-31",
            "complete_snapshot": True,
            "label": "01sep2025-30nov2025_form13f.zip",
            "edges": [
                {
                    "node_a": "cik_0001067983",
                    "properties": {
                        "institution_name": "Berkshire Hathaway Inc",
                        "name_of_issuer": "Microsoft Corporation",
                        "value_usd": 123456.0,
                        "shares": 1000,
                        "active_after": "2025-12-31",
                        "filing_period": period_key,
                    },
                },
                {
                    "node_a": "cik_0001067983",
                    "properties": {
                        "institution_name": "Berkshire Hathaway Inc",
                        "name_of_issuer": "Apple Inc",
                        "value_usd": 200000.0,
                        "shares": 4000,
                        "active_after": "2025-12-31",
                        "filing_period": period_key,
                    },
                },
                {
                    "node_a": "cik_0000093410",
                    "properties": {
                        "institution_name": "STATE STREET CORP",
                        "name_of_issuer": "Microsoft Corporation",
                        "value_usd": 16574986091.0,
                        "shares": 152200000,
                        "active_after": "2025-12-31",
                        "filing_period": period_key,
                    },
                },
            ],
        }

        with patch.object(gns, "_fetch_sec_company_ticker_rows", return_value=[]), \
             patch.object(gns, "_revalidate_company_ciks_against_sec", return_value={"cleared": 0}), \
             patch.object(
                 gns,
                 "_fetch_13f_holding_snapshot_specs",
                 return_value=(
                     {"User-Agent": "test@example.com"},
                     [{"fetch_mode": "zip", "period_key": period_key, "active_after": "2025-12-31", "complete_snapshot": True, "label": "01sep2025-30nov2025_form13f.zip"}],
                ),
             ), \
             patch.object(gns, "_fetch_13f_holdings_snapshot_from_spec", return_value=snapshot), \
             patch.object(gns, "_phase7_backfill_snapshot_weight_pct") as backfill_mock, \
             patch.object(gns, "_phase7_recompute_snapshot_weight_pct_from_graph") as recompute_mock, \
             patch.object(gns, "_sync_graph_edge_intervals", return_value=0), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_nexus_maybe_log_eta"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_log_stage_error"), \
             patch.object(gns, "_log_stage_unexpected"):
            gns.phase7_13f_ownership(session)

        backfill_mock.assert_not_called()
        recompute_mock.assert_not_called()
        self.assertIsNotNone(session.holds_batch)
        by_pair = {(row["inst_id"], row["ticker"]): row for row in session.holds_batch}
        self.assertAlmostEqual(round(123456.0 / (123456.0 + 200000.0) * 100.0, 6), by_pair[("cik_0001067983", "MSFT")]["weight_pct"], places=6)
        self.assertAlmostEqual(round(200000.0 / (123456.0 + 200000.0) * 100.0, 6), by_pair[("cik_0001067983", "AAPL")]["weight_pct"], places=6)
        self.assertAlmostEqual(100.0, by_pair[("cik_0000093410", "MSFT")]["weight_pct"], places=6)

    def test_phase7_13f_ownership_invokes_invalid_snapshot_cleanup_after_merge(self):
        class _FakeResult(list):
            def __init__(self, rows=None, single_value=None):
                super().__init__(rows or [])
                self._single_value = single_value

            def single(self):
                return self._single_value

            def consume(self):
                return None

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **params):
                self.queries.append((query, params))
                if "RETURN c.cik AS cik, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"cik": "0000789019", "ticker": "MSFT"}])
                if "RETURN c.name AS name, c.ticker AS ticker" in query:
                    return _FakeResult(rows=[{"name": "Microsoft Corporation", "ticker": "MSFT"}])
                if "RETURN count(r) AS closed" in query:
                    return _FakeResult(single_value={"closed": 0})
                if "RETURN count(r) AS stale_count" in query:
                    return _FakeResult(single_value={"stale_count": 0})
                if "RETURN count(r) AS total" in query:
                    return _FakeResult(single_value={"total": 1})
                return _FakeResult(single_value={})

        session = _FakeSession()
        period_key = "01dec2025-28feb2026_form13f"
        snapshot = {
            "period_key": period_key,
            "active_after": "2026-03-31",
            "complete_snapshot": True,
            "label": "01dec2025-28feb2026_form13f.zip",
            "edges": [
                {
                    "node_a": "cik_000196001",
                    "properties": {
                        "institution_name": "Cercano Management LLC",
                        "name_of_issuer": "Microsoft Corporation",
                        "value_usd": 32164599000.0,
                        "shares": 0,
                        "active_after": "2026-03-31",
                        "filing_period": period_key,
                    },
                },
                {
                    "node_a": "cik_0001067983",
                    "properties": {
                        "institution_name": "Berkshire Hathaway Inc",
                        "name_of_issuer": "Microsoft Corporation",
                        "value_usd": 123456.0,
                        "shares": 1000,
                        "active_after": "2026-03-31",
                        "filing_period": period_key,
                    },
                },
            ],
        }

        with patch.object(gns, "_fetch_sec_company_ticker_rows", return_value=[]), \
             patch.object(gns, "_revalidate_company_ciks_against_sec", return_value={"cleared": 0}), \
             patch.object(
                 gns,
                 "_fetch_13f_holding_snapshot_specs",
                 return_value=(
                     {"User-Agent": "test@example.com"},
                     [{"fetch_mode": "zip", "period_key": period_key, "active_after": "2026-03-31", "complete_snapshot": True, "label": "01dec2025-28feb2026_form13f.zip"}],
                ),
             ), \
             patch.object(gns, "_fetch_13f_holdings_snapshot_from_spec", return_value=snapshot), \
             patch.object(gns, "_phase7_delete_invalid_snapshot_rows", return_value=1) as delete_invalid_mock, \
             patch.object(gns, "_phase7_backfill_snapshot_weight_pct") as backfill_mock, \
             patch.object(gns, "_phase7_recompute_snapshot_weight_pct_from_graph") as recompute_mock, \
             patch.object(gns, "_sync_graph_edge_intervals", return_value=0), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_nexus_maybe_log_eta"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_log_stage_error"), \
             patch.object(gns, "_log_stage_unexpected"):
            gns.phase7_13f_ownership(session)

        backfill_mock.assert_not_called()
        recompute_mock.assert_not_called()
        delete_invalid_mock.assert_called_once_with(session, period_key)

    def test_security_instrument_detection(self):
        self.assertTrue(gns._is_security_instrument_name(
            "Fifth Third Bancorp Depositary Shares Representing a 1/40th Ownership Interest"
        ))
        self.assertTrue(gns._is_security_instrument_name(
            "DTE Energy Company 2020 Series G 4.375% Junior Subordinated Debentures due 2080"
        ))
        self.assertFalse(gns._is_security_instrument_name("Kelly Services, Inc."))

    def test_non_operating_listing_types_are_not_supported_companies(self):
        self.assertFalse(gns._is_supported_company_listing(
            "Tennessee Valley Authority Power Bonds 1999 Series A due May 1, 2029",
            "SP",
        ))
        self.assertFalse(gns._is_supported_company_listing(
            "BlackRock Taxable Municipal Bond Trust",
            "FUND",
        ))

    def test_cached_company_records_are_resanitized_before_reuse(self):
        cleaned, dropped = gns._sanitize_company_records([
            {"ticker": "BITW", "name": "Bitwise 10 Crypto Index ETF", "type": "ETV"},
            {"ticker": "MSFT", "name": "Microsoft Corporation", "type": "CS"},
        ])
        self.assertEqual(1, dropped)
        self.assertEqual(["MSFT"], [rec["ticker"] for rec in cleaned])

    def test_company_identity_key_collapses_same_issuer_listings(self):
        issuer = gns._company_identity_key("Fifth Third Bancorp")
        preferred = gns._company_identity_key(
            "Fifth Third Bancorp Depositary Shares Representing a 1/40th Ownership Interest"
        )
        self.assertEqual(issuer, preferred)

    def test_dedupe_company_records_prefers_single_representative(self):
        records = [
            {
                "ticker": "KELYA",
                "name": "Kelly Services, Inc.",
                "canonical_name": "Kelly Services, Inc.",
                "issuer_key": gns._company_identity_key("Kelly Services, Inc."),
                "cik": "0000055135",
                "lei": None,
                "is_security_instrument": False,
            },
            {
                "ticker": "KELYB",
                "name": "Kelly Services, Inc.",
                "canonical_name": "Kelly Services, Inc.",
                "issuer_key": gns._company_identity_key("Kelly Services, Inc."),
                "cik": "0000055135",
                "lei": None,
                "is_security_instrument": False,
            },
            {
                "ticker": "DFIN",
                "name": "Donnelley Financial Solutions, Inc.",
                "canonical_name": "Donnelley Financial Solutions, Inc.",
                "issuer_key": gns._company_identity_key("Donnelley Financial Solutions, Inc."),
                "cik": "0001792580",
                "lei": None,
                "is_security_instrument": False,
            },
        ]
        deduped = gns._dedupe_company_records(records)
        self.assertEqual(2, len(deduped))
        tickers = {r["ticker"] for r in deduped}
        self.assertIn("DFIN", tickers)
        self.assertEqual(1, len([t for t in tickers if t.startswith("KELY")]))

    def test_wikidata_resolution_does_not_prefix_match_by_default(self):
        bindings = [{
            "parentLabel": {"type": "literal", "value": "Bain Capital"},
            "childLabel": {"type": "literal", "value": "Domino's Pizza"},
        }]
        edges = gns._resolve_wikidata_bindings_to_edges(
            bindings,
            title_to_ticker={
                "domino's pizza": "DPZ",
                "bain capital specialty finance": "BCSF",
            },
            ticker_to_ticker={"DPZ": "DPZ", "BCSF": "BCSF"},
            norm_to_ticker={
                "domino s pizza": "DPZ",
                "bain capital specialty finance": "BCSF",
            },
        )
        self.assertEqual([], edges)

    def test_build_wikidata_company_resolution_maps_uses_listing_and_lei_names(self):
        title_to_ticker, ticker_to_ticker, norm_to_ticker, ticker_to_name = gns._build_wikidata_company_resolution_maps(
            [
                {
                    "ticker": "BKKT",
                    "name": "Bakkt Holdings, Inc.",
                    "canonical_name": "Bakkt Holdings, Inc.",
                    "listing_name": "Bakkt",
                    "lei_legal_name": "Bakkt Holdings, Inc.",
                    "issuer_key": gns._company_identity_key("Bakkt Holdings, Inc."),
                    "cik": "0001820302",
                    "lei": None,
                    "is_security_instrument": False,
                }
            ]
        )
        self.assertEqual("BKKT", title_to_ticker["bakkt"])
        self.assertEqual("BKKT", title_to_ticker["bakkt holdings, inc."])
        self.assertEqual("BKKT", norm_to_ticker["bakkt"])
        self.assertEqual("BKKT", ticker_to_ticker["BKKT"])
        self.assertEqual("Bakkt Holdings, Inc.", ticker_to_name["BKKT"])

    def test_controls_llm_validation_uses_structured_fallback_for_gemini(self):
        batch = type(
            "StructuredBatch",
            (),
            {"results": [type("Decision", (), {"keep": True})()]},
        )()
        with patch("llm_utils.call_gemini_with_grounding", return_value="not-json"), \
             patch("llm_utils.call_structured_llm_by_provider", return_value=batch) as structured_mock, \
             patch("llm_utils.call_llm_by_provider") as raw_mock:
            validated = gns._llm_validate_controls_edges(
                [{"node_a": "ICE", "node_b": "BKKT", "properties": {"resolution_mode": "label"}}],
                {"ICE": "Intercontinental Exchange, Inc.", "BKKT": "Bakkt Holdings, Inc."},
                "gemini",
                "gemini-3-flash-preview",
                "test-key",
                validation_cache={},
                require_validation=True,
            )
        self.assertEqual(1, len(validated))
        self.assertEqual("llm_grounded", validated[0]["properties"]["validation_mode"])
        self.assertTrue(validated[0]["properties"]["validated_at"].endswith("Z"))
        structured_mock.assert_called_once()
        raw_mock.assert_not_called()

    def test_controls_llm_validation_splits_unusable_batches(self):
        def _fake_structured(provider, api_key, model, prompt, output_type, **kwargs):
            if "EVGO" in prompt and "SNDK" in prompt:
                return output_type(results=[])
            if "EVGO" in prompt:
                return output_type(results=[{"keep": True}])
            if "SNDK" in prompt:
                return output_type(results=[{"keep": False}])
            return None

        edges = [
            {"node_a": "NRG", "node_b": "EVGO", "properties": {"resolution_mode": "label"}},
            {"node_a": "WDC", "node_b": "SNDK", "properties": {"resolution_mode": "label"}},
        ]
        names = {
            "NRG": "NRG Energy, Inc.",
            "EVGO": "EVgo Inc.",
            "WDC": "Western Digital Corporation",
            "SNDK": "Sandisk Corporation",
        }
        with patch("llm_utils.call_structured_llm_by_provider", side_effect=_fake_structured), \
             patch("llm_utils.call_llm_by_provider") as raw_mock:
            validated = gns._llm_validate_controls_edges(
                edges,
                names,
                "deepseek",
                "deepseek-reasoner",
                "sk-test",
                validation_cache={},
                require_validation=True,
            )
        self.assertEqual([("NRG", "EVGO")], [(edge["node_a"], edge["node_b"]) for edge in validated])
        raw_mock.assert_called_once()

    def test_controls_denylist_blocks_known_bad_pair(self):
        self.assertTrue(gns._edge_pair_denied("CONTROLS", "PFE", "ZTS"))
        self.assertTrue(gns._edge_pair_denied("CONTROLS", "ZTS", "PFE"))

    def test_supplier_denylist_blocks_confirmed_false_positive_pair(self):
        self.assertTrue(gns._edge_pair_denied("SUPPLIER_OF", "CELZ", "NEGG"))
        self.assertTrue(gns._edge_pair_denied("SUPPLIER_OF", "NEGG", "CELZ"))

    def test_controls_partition_requires_confirmation(self):
        validated, ambiguous = gns._partition_controls_edges_by_confirmation(
            [
                {"node_a": "MSFT", "node_b": "ATVI", "properties": {"resolution_mode": "ticker"}},
                {"node_a": "PFE", "node_b": "ZTS", "properties": {"resolution_mode": "ticker"}},
            ],
            {("MSFT", "ATVI")},
        )
        self.assertEqual(1, len(validated))
        self.assertEqual("MSFT", validated[0]["node_a"])
        self.assertEqual("gleif_parent", validated[0]["properties"]["validation_mode"])
        self.assertTrue(validated[0]["properties"]["validated_at"].endswith("Z"))
        self.assertEqual(1, len(ambiguous))
        self.assertEqual("PFE", ambiguous[0]["node_a"])

    def test_controls_partition_keeps_sec_validation_mode_from_mapping(self):
        validated, ambiguous = gns._partition_controls_edges_by_confirmation(
            [{"node_a": "ICE", "node_b": "BKKT", "properties": {}}],
            {("ICE", "BKKT"): "sec_annual_filing"},
        )
        self.assertEqual([], ambiguous)
        self.assertEqual("sec_annual_filing", validated[0]["properties"]["validation_mode"])

    def test_company_records_have_sec_control_support_uses_best_names(self):
        parent = {"ticker": "ICE", "cik": "1571949", "lei_legal_name": "Intercontinental Exchange, Inc."}
        child = {"ticker": "BKKT", "cik": "1820302", "canonical_name": "Bakkt Holdings, Inc."}
        child_text = "Bakkt Holdings, Inc. is controlled by Intercontinental Exchange, Inc."
        with patch.object(gns, "_latest_sec_annual_filing_text", return_value=child_text):
            self.assertTrue(gns._company_records_have_sec_control_support(parent, child))

    def test_resolve_usaspending_to_edges_aggregates_contract_dates(self):
        edges = gns._resolve_usaspending_to_edges(
            [
                {
                    "Recipient Name": "International Business Machines Corporation",
                    "Awarding Agency": "National Aeronautics and Space Administration",
                    "Award Amount": 100000,
                    "Start Date": "2024-01-01",
                    "End Date": "2024-03-01",
                },
                {
                    "Recipient Name": "International Business Machines Corporation",
                    "Awarding Agency": "National Aeronautics and Space Administration",
                    "Award Amount": 150000,
                    "Start Date": "2024-04-01",
                    "End Date": "2024-06-01",
                },
            ],
            {"IBM": "International Business Machines Corporation"},
        )
        self.assertEqual(1, len(edges))
        props = edges[0]["properties"]
        self.assertEqual("IBM", edges[0]["node_a"])
        self.assertEqual(250000.0, props["total_obligation"])
        self.assertEqual(2, props["award_count"])
        self.assertEqual("2024-01-01", props["active_after"])
        self.assertEqual("2024-06-01", props["last_confirmed"])
        self.assertEqual("closed", props["edge_state"])
        self.assertEqual("2024-06-01", props["valid_until"])

    def test_resolve_usaspending_to_edges_prefers_exact_company_match_over_legal_entity_bridge(self):
        edges = gns._resolve_usaspending_to_edges(
            [
                {
                    "Recipient Name": "ACI Worldwide, Inc.",
                    "Awarding Agency": "Department of Defense",
                    "Award Amount": 125000,
                    "Start Date": "2024-01-01",
                    "End Date": "",
                }
            ],
            {"ACIW": "ACI Worldwide, Inc."},
            legal_entity_bridge={
                "aci worldwide": {
                    "ancestor_ticker": "ZZZZ",
                    "entity_key": "ex21:demo:aci-worldwide-france",
                    "display_name": "ACI WORLDWIDE FRANCE",
                }
            },
        )
        self.assertEqual(1, len(edges))
        self.assertEqual("ACIW", edges[0]["node_a"])
        self.assertEqual("company_exact", edges[0]["properties"]["recipient_resolution_mode"])
        self.assertEqual("", edges[0]["properties"]["recipient_legal_entity_key"])
        self.assertEqual("", edges[0]["properties"]["recipient_legal_entity_name"])

    def test_resolve_usaspending_to_edges_uses_low_risk_legal_entity_bridge(self):
        edges = gns._resolve_usaspending_to_edges(
            [
                {
                    "Recipient Name": "ACI WORLDWIDE FRANCE",
                    "Awarding Agency": "Department of Defense",
                    "Award Amount": 125000,
                    "Start Date": "2024-01-01",
                    "End Date": "",
                }
            ],
            {"ACIW": "ACI Worldwide, Inc."},
            legal_entity_bridge={
                "aci worldwide france": {
                    "ancestor_ticker": "ACIW",
                    "entity_key": "lei:254900SPUYFJY3VHA264",
                    "display_name": "ACI WORLDWIDE FRANCE",
                }
            },
        )
        self.assertEqual(1, len(edges))
        self.assertEqual("ACIW", edges[0]["node_a"])
        self.assertEqual("legal_entity_ancestor", edges[0]["properties"]["recipient_resolution_mode"])
        self.assertEqual("legal_entity_alias", edges[0]["properties"]["recipient_resolution_source"])
        self.assertEqual("lei:254900SPUYFJY3VHA264", edges[0]["properties"]["recipient_legal_entity_key"])
        self.assertEqual("ACI WORLDWIDE FRANCE", edges[0]["properties"]["recipient_legal_entity_name"])

    def test_resolve_usaspending_to_edges_uses_recipient_name_as_bridge_display_fallback(self):
        edges = gns._resolve_usaspending_to_edges(
            [
                {
                    "Recipient Name": "HPI FEDERAL LLC",
                    "Awarding Agency": "Department of Defense",
                    "Award Amount": 125000,
                    "Start Date": "2024-01-01",
                    "End Date": "",
                }
            ],
            {"HPQ": "HP Inc."},
            legal_entity_bridge={
                "hpi federal": {
                    "ancestor_ticker": "HPQ",
                    "entity_key": "ex21:HPQ:hpi_federal:demo",
                }
            },
        )
        self.assertEqual(1, len(edges))
        self.assertEqual("HPQ", edges[0]["node_a"])
        self.assertEqual("legal_entity_ancestor", edges[0]["properties"]["recipient_resolution_mode"])
        self.assertEqual("HPI FEDERAL LLC", edges[0]["properties"]["recipient_legal_entity_name"])

    def test_phase8_usaspending_edge_to_write_param_preserves_real_world_recipient_names(self):
        today_iso = "2026-03-12"
        real_world_cases = [
            ("IBM exact", "IBM", "nasa", {"recipient_name": "International Business Machines Corporation", "agency_name": "National Aeronautics and Space Administration"}, "International Business Machines Corporation"),
            ("Boeing exact", "BA", "dod", {"recipient_name": "The Boeing Company", "agency_name": "Department of Defense"}, "The Boeing Company"),
            ("ACI bridge exact", "ACIW", "dod", {"recipient_name": "ACI WORLDWIDE FRANCE", "recipient_legal_entity_name": "ACI WORLDWIDE FRANCE", "recipient_resolution_mode": "legal_entity_ancestor"}, "ACI WORLDWIDE FRANCE"),
            ("HPQ bridge fallback", "HPQ", "dod", {"recipient_name": "", "recipient_legal_entity_name": "HPI Federal LLC", "recipient_resolution_mode": "legal_entity_ancestor"}, "HPI Federal LLC"),
            ("KBR bridge fallback", "KBR", "dod", {"recipient_legal_entity_name": "KBR Services, LLC", "recipient_resolution_mode": "legal_entity_ancestor"}, "KBR Services, LLC"),
            ("VZ bridge exact", "VZ", "gsa", {"recipient_name": "MCI Communications Services LLC", "recipient_legal_entity_name": "MCI Communications Services LLC", "recipient_resolution_mode": "legal_entity_ancestor"}, "MCI Communications Services LLC"),
            ("Leidos exact whitespace", "LDOS", "navy", {"recipient_name": "  Leidos, Inc.  ", "agency_name": "Department of the Navy"}, "Leidos, Inc."),
            ("L3Harris exact", "LHX", "usaf", {"recipient_name": "L3HARRIS TECHNOLOGIES ESS, INC.", "agency_name": "Department of the Air Force"}, "L3HARRIS TECHNOLOGIES ESS, INC."),
            ("CACI exact", "CACI", "army", {"recipient_name": "CACI FEDERAL INC", "agency_name": "Department of the Army"}, "CACI FEDERAL INC"),
            ("Jacobs exact", "J", "doe", {"recipient_name": "Jacobs Technology Inc.", "agency_name": "Department of Energy"}, "Jacobs Technology Inc."),
            ("Display fallback", "V2X", "dod", {"recipient_display_name": "V2X National Security Solutions, Inc.", "recipient_resolution_mode": "legal_entity_ancestor"}, "V2X National Security Solutions, Inc."),
            ("Empty fallback", "IBM", "va", {"agency_name": "Department of Veterans Affairs"}, ""),
        ]

        for label, ticker, agency_id, props, expected_name in real_world_cases:
            with self.subTest(label=label):
                param = gns._phase8_usaspending_edge_to_write_param(
                    {"node_a": ticker, "node_b": agency_id, "properties": props},
                    today_iso,
                )
                self.assertEqual(ticker, param["ticker"])
                self.assertEqual(agency_id, param["agency_id"])
                self.assertEqual(today_iso, param["today"])
                self.assertEqual(expected_name, param["recipient_name"])

    def test_phase8_load_usaspending_legal_entity_bridge_rejects_ambiguous_aliases(self):
        class _FakeSession:
            def run(self, _query):
                return iter(
                    [
                        {
                            "entity_key": "entity:1",
                            "display_name": "Safe Subsidiary One LLC",
                            "aliases": ["Safe Subsidiary One LLC"],
                            "normalized_aliases": ["safe subsidiary one"],
                            "ancestor_tickers": ["SAFE"],
                        },
                        {
                            "entity_key": "entity:2",
                            "display_name": "Shared Alias Subsidiary A LLC",
                            "aliases": ["Shared Alias Holdings LLC"],
                            "normalized_aliases": ["shared alias holdings"],
                            "ancestor_tickers": ["AAA"],
                        },
                        {
                            "entity_key": "entity:3",
                            "display_name": "Shared Alias Subsidiary B LLC",
                            "aliases": ["Shared Alias Holdings LLC"],
                            "normalized_aliases": ["shared alias holdings"],
                            "ancestor_tickers": ["BBB"],
                        },
                    ]
                )

        bridge = gns._phase8_load_usaspending_legal_entity_bridge(_FakeSession())
        self.assertIn("safe subsidiary one", bridge)
        self.assertEqual("SAFE", bridge["safe subsidiary one"]["ancestor_ticker"])
        self.assertNotIn("shared alias holdings", bridge)

    def test_phase9_usaspending_persists_legal_entity_bridge_metadata(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{"ticker": "ACIW", "name": "ACI Worldwide, Inc."}])
                return iter([])

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        fake_session = _FakeSession()
        resolved_edges = [{
            "node_a": "ACIW",
            "rel": "CONTRACTS_WITH",
            "node_b": "dod",
            "confidence": 0.95,
            "source": "USASpending.gov",
            "properties": {
                "agency_name": "Department of Defense",
                "total_obligation": 250000.0,
                "award_count": 2,
                "active_after": "2024-01-01",
                "last_confirmed": "2024-06-01",
                "valid_until": "",
                "edge_state": "open",
                "recipient_name": "ACI WORLDWIDE FRANCE",
                "recipient_resolution_mode": "legal_entity_ancestor",
                "recipient_resolution_source": "legal_entity_alias",
                "recipient_listed_ancestor_ticker": "ACIW",
                "recipient_legal_entity_key": "lei:254900SPUYFJY3VHA264",
                "recipient_legal_entity_name": "ACI WORLDWIDE FRANCE",
            },
        }]

        with patch.object(gns, "_nexus_read_cached_json", return_value=[{"Recipient Name": "ACI WORLDWIDE FRANCE"}]), \
             patch.object(gns, "_phase8_load_usaspending_legal_entity_bridge", return_value={"aci worldwide france": {"ancestor_ticker": "ACIW"}}), \
             patch.object(gns, "_resolve_usaspending_to_edges", return_value=resolved_edges), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0):
            gns.phase9_usaspending(fake_session)

        joined_queries = "\n".join(query for query, _ in fake_session.queries)
        self.assertIn("r.recipient_name", joined_queries)
        self.assertIn("r.recipient_resolution_mode", joined_queries)
        self.assertIn("r.recipient_legal_entity_key", joined_queries)
        self.assertIn("r.recipient_legal_entity_name", joined_queries)

    def test_phase9_usaspending_write_batch_includes_recipient_name_value(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{"ticker": "ACIW", "name": "ACI Worldwide, Inc."}])
                return iter([])

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        fake_session = _FakeSession()
        resolved_edges = [{
            "node_a": "ACIW",
            "rel": "CONTRACTS_WITH",
            "node_b": "dod",
            "confidence": 0.95,
            "source": "USASpending.gov",
            "properties": {
                "agency_name": "Department of Defense",
                "total_obligation": 250000.0,
                "award_count": 2,
                "active_after": "2024-01-01",
                "last_confirmed": "2024-06-01",
                "valid_until": "",
                "edge_state": "open",
                "recipient_name": "ACI WORLDWIDE FRANCE",
                "recipient_resolution_mode": "legal_entity_ancestor",
                "recipient_resolution_source": "legal_entity_alias",
                "recipient_listed_ancestor_ticker": "ACIW",
                "recipient_legal_entity_key": "lei:254900SPUYFJY3VHA264",
                "recipient_legal_entity_name": "ACI WORLDWIDE FRANCE",
            },
        }]

        with patch.object(gns, "_nexus_read_cached_json", return_value=[{"Recipient Name": "ACI WORLDWIDE FRANCE"}]), \
             patch.object(gns, "_phase8_load_usaspending_legal_entity_bridge", return_value={"aci worldwide france": {"ancestor_ticker": "ACIW"}}), \
             patch.object(gns, "_resolve_usaspending_to_edges", return_value=resolved_edges), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0):
            gns.phase9_usaspending(fake_session)

        batch_rows = []
        for _query, kwargs in fake_session.queries:
            if kwargs.get("batch"):
                batch_rows.extend(kwargs["batch"])
        self.assertTrue(batch_rows)
        self.assertEqual("ACI WORLDWIDE FRANCE", batch_rows[0]["recipient_name"])

    def test_phase8_apply_recipient_audit_metadata_real_world_bridge_cases(self):
        real_world_cases = [
            {
                "label": "IBM exact",
                "properties": {
                    "_recipient_names": ["International Business Machines Corporation"],
                    "_recipient_resolution_modes": ["company_exact"],
                },
                "expected_name": "International Business Machines Corporation",
                "expected_count": 1,
                "expected_mode": "company_exact",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "Boeing exact",
                "properties": {
                    "_recipient_names": ["The Boeing Company"],
                    "_recipient_resolution_modes": ["company_exact"],
                },
                "expected_name": "The Boeing Company",
                "expected_count": 1,
                "expected_mode": "company_exact",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "ACI WORLDWIDE FRANCE bridge",
                "properties": {
                    "_recipient_names": ["ACI WORLDWIDE FRANCE"],
                    "_recipient_legal_entity_names": ["ACI WORLDWIDE FRANCE"],
                    "_recipient_legal_entity_keys": ["lei:254900SPUYFJY3VHA264"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor"],
                    "target_ticker": "ACIW",
                },
                "expected_name": "ACI WORLDWIDE FRANCE",
                "expected_count": 1,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "legal_entity_alias",
                "expected_legal_name": "ACI WORLDWIDE FRANCE",
            },
            {
                "label": "HPI Federal bridge fallback",
                "properties": {
                    "_recipient_names": ["HPI FEDERAL LLC"],
                    "_recipient_legal_entity_names": ["HPI Federal LLC"],
                    "_recipient_legal_entity_keys": ["ex21:HPQ:hpi_federal:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor"],
                    "target_ticker": "HPQ",
                },
                "expected_name": "HPI FEDERAL LLC",
                "expected_count": 1,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "legal_entity_alias",
                "expected_legal_name": "HPI Federal LLC",
            },
            {
                "label": "KBR Services bridge",
                "properties": {
                    "_recipient_names": ["KBR SERVICES, LLC"],
                    "_recipient_legal_entity_names": ["KBR Services, LLC"],
                    "_recipient_legal_entity_keys": ["ex21:KBR:kbr_services:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor"],
                    "target_ticker": "KBR",
                },
                "expected_name": "KBR SERVICES, LLC",
                "expected_count": 1,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "legal_entity_alias",
                "expected_legal_name": "KBR Services, LLC",
            },
            {
                "label": "MCI bridge",
                "properties": {
                    "_recipient_names": ["MCI Communications Services LLC"],
                    "_recipient_legal_entity_names": ["MCI Communications Services LLC"],
                    "_recipient_legal_entity_keys": ["ex21:VZ:mci_communications:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor"],
                    "target_ticker": "VZ",
                },
                "expected_name": "MCI Communications Services LLC",
                "expected_count": 1,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "legal_entity_alias",
                "expected_legal_name": "MCI Communications Services LLC",
            },
            {
                "label": "USB canonical difference",
                "properties": {
                    "_recipient_names": ["U.S. BANK NATIONAL ASSOCIATION"],
                    "_recipient_legal_entity_names": ["U.S. Bank National Association (a nationally chartered banking association)"],
                    "_recipient_legal_entity_keys": ["ex21:USB:u_s_bank_national_association:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor"],
                    "target_ticker": "USB",
                },
                "expected_name": "U.S. BANK NATIONAL ASSOCIATION",
                "expected_count": 1,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "legal_entity_alias",
                "expected_legal_name": "U.S. Bank National Association (a nationally chartered banking association)",
            },
            {
                "label": "Sea-Bird punctuation difference",
                "properties": {
                    "_recipient_names": ["SEA-BIRD ELECTRONICS, INC"],
                    "_recipient_legal_entity_names": ["Sea-Bird Electronics, Inc."],
                    "_recipient_legal_entity_keys": ["ex21:VLTO:sea_bird_electronics:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor"],
                    "target_ticker": "VLTO",
                },
                "expected_name": "SEA-BIRD ELECTRONICS, INC",
                "expected_count": 1,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "legal_entity_alias",
                "expected_legal_name": "Sea-Bird Electronics, Inc.",
            },
            {
                "label": "Tri-City LLC suffix",
                "properties": {
                    "_recipient_names": ["TRI-CITY ELECTRIC COMPANY OF IOWA"],
                    "_recipient_legal_entity_names": ["Tri-City Electric Company of Iowa, LLC"],
                    "_recipient_legal_entity_keys": ["ex21:PWR:tri_city_electric_company_of_iowa:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor"],
                    "target_ticker": "PWR",
                },
                "expected_name": "TRI-CITY ELECTRIC COMPANY OF IOWA",
                "expected_count": 1,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "legal_entity_alias",
                "expected_legal_name": "Tri-City Electric Company of Iowa, LLC",
            },
            {
                "label": "Pepsi-Cola bottling",
                "properties": {
                    "_recipient_names": ["PEPSI-COLA METROPOLITAN BOTTLING COMPANY, INC."],
                    "_recipient_legal_entity_names": ["Pepsi-Cola Metropolitan Bottling Company, Inc."],
                    "_recipient_legal_entity_keys": ["ex21:PEP:pepsi_cola_metropolitan_bottling:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor"],
                    "target_ticker": "PEP",
                },
                "expected_name": "PEPSI-COLA METROPOLITAN BOTTLING COMPANY, INC.",
                "expected_count": 1,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "legal_entity_alias",
                "expected_legal_name": "Pepsi-Cola Metropolitan Bottling Company, Inc.",
            },
            {
                "label": "CQ Roll Call punctuation",
                "properties": {
                    "_recipient_names": ["CQ-ROLL CALL, INC"],
                    "_recipient_legal_entity_names": ["CQ-Roll Call, Inc."],
                    "_recipient_legal_entity_keys": ["ex21:NOTE:cq_roll_call:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor"],
                    "target_ticker": "NOTE",
                },
                "expected_name": "CQ-ROLL CALL, INC",
                "expected_count": 1,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "legal_entity_alias",
                "expected_legal_name": "CQ-Roll Call, Inc.",
            },
            {
                "label": "Duplicate raw names collapse",
                "properties": {
                    "_recipient_names": ["BFI WASTE SERVICES, LLC", "bfi waste services, llc"],
                    "_recipient_legal_entity_names": ["BFI Waste Services, LLC"],
                    "_recipient_legal_entity_keys": ["ex21:RSG:bfi_waste_services:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor", "legal_entity_ancestor"],
                    "target_ticker": "RSG",
                },
                "expected_name": "BFI WASTE SERVICES, LLC",
                "expected_count": 1,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "legal_entity_alias",
                "expected_legal_name": "BFI Waste Services, LLC",
            },
            {
                "label": "Mixed recipient names blank singular",
                "properties": {
                    "_recipient_names": ["CLEAN HARBORS ENVIRONMENTAL SERVICES INC", "SAFETY-KLEEN SYSTEMS, INC."],
                    "_recipient_legal_entity_names": ["Safety-Kleen Systems, Inc."],
                    "_recipient_legal_entity_keys": ["ex21:CLH:safety_kleen:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor", "legal_entity_ancestor"],
                    "target_ticker": "CLH",
                },
                "expected_name": "CLEAN HARBORS ENVIRONMENTAL SERVICES INC",
                "expected_count": 2,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "legal_entity_alias",
                "expected_legal_name": "Safety-Kleen Systems, Inc.",
            },
            {
                "label": "Lumen punctuation variants keep primary",
                "properties": {
                    "_recipient_names": ["LUMEN TECHNOLOGIES GOVERNMENT SOLUTIONS, INC", "LUMEN TECHNOLOGIES GOVERNMENT SOLUTIONS, INC."],
                    "_recipient_resolution_modes": ["company_prefix", "company_prefix"],
                },
                "expected_name": "LUMEN TECHNOLOGIES GOVERNMENT SOLUTIONS, INC",
                "expected_count": 2,
                "expected_mode": "company_prefix",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "VLTO bridge mixed subsidiaries keeps primary",
                "properties": {
                    "_recipient_names": ["X-RAY OPTICAL SYSTEMS, INC.", "SEA-BIRD ELECTRONICS, INC"],
                    "_recipient_legal_entity_names": ["X-Ray Optical Systems, Inc.", "Sea-Bird Electronics, Inc."],
                    "_recipient_legal_entity_keys": ["ex21:VLTO:xray_optical:demo", "ex21:VLTO:sea_bird:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor", "legal_entity_ancestor"],
                    "target_ticker": "VLTO",
                },
                "expected_name": "X-RAY OPTICAL SYSTEMS, INC.",
                "expected_count": 2,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "Sunbelt punctuation variants keep primary",
                "properties": {
                    "_recipient_names": ["SUNBELT RENTALS, INC", "SUNBELT RENTALS, INC."],
                    "_recipient_resolution_modes": ["company_exact", "company_exact"],
                },
                "expected_name": "SUNBELT RENTALS, INC",
                "expected_count": 2,
                "expected_mode": "company_exact",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "LOAR bridge mixed subsidiaries keeps primary",
                "properties": {
                    "_recipient_names": ["HYDRA-ELECTRIC COMPANY", "SMR ACQUISITION LLC"],
                    "_recipient_legal_entity_names": ["Hydra-Electric Company", "SMR Acquisition LLC"],
                    "_recipient_legal_entity_keys": ["ex21:LOAR:hydra_electric:demo", "ex21:LOAR:smr_acquisition:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor", "legal_entity_ancestor"],
                    "target_ticker": "LOAR",
                },
                "expected_name": "HYDRA-ELECTRIC COMPANY",
                "expected_count": 2,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "Lincoln mixed resolution keeps first recipient",
                "properties": {
                    "_recipient_names": ["LINCOLN ELECTRIC COOPERATIVE INC", "LINCOLN ELECTRIC HOLDINGS INC"],
                    "_recipient_resolution_modes": ["company_prefix", "company_exact"],
                },
                "expected_name": "LINCOLN ELECTRIC COOPERATIVE INC",
                "expected_count": 2,
                "expected_mode": "mixed",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "Avnet punctuation variants keep primary",
                "properties": {
                    "_recipient_names": ["AVNET, INC.", "AVNET INC."],
                    "_recipient_resolution_modes": ["company_exact", "company_exact"],
                },
                "expected_name": "AVNET, INC.",
                "expected_count": 2,
                "expected_mode": "company_exact",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "ITT bridge mixed subsidiaries keeps primary",
                "properties": {
                    "_recipient_names": ["ITT CANNON LLC", "ITT AEROSPACE CONTROLS LLC"],
                    "_recipient_legal_entity_names": ["ITT Cannon LLC", "ITT Aerospace Controls LLC"],
                    "_recipient_legal_entity_keys": ["ex21:ITT:cannon:demo", "ex21:ITT:aerospace_controls:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor", "legal_entity_ancestor"],
                    "target_ticker": "ITT",
                },
                "expected_name": "ITT CANNON LLC",
                "expected_count": 2,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "Global exact variants keep primary",
                "properties": {
                    "_recipient_names": ["GLOBAL INCORPORATED", "GLOBAL, INC", "GLOBAL ENTERPRISE, INC"],
                    "_recipient_resolution_modes": ["company_exact", "company_exact", "company_exact"],
                },
                "expected_name": "GLOBAL INCORPORATED",
                "expected_count": 3,
                "expected_mode": "company_exact",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "Applied mixed mode keeps first recipient",
                "properties": {
                    "_recipient_names": ["S.G. MORRIS CO., LLC", "APPLIED INDUSTRIAL TECHNOLOGIES, INC."],
                    "_recipient_legal_entity_names": ["S. G. Morris Co., LLC"],
                    "_recipient_legal_entity_keys": ["ex21:AIT:sg_morris:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor", "company_exact"],
                    "target_ticker": "AIT",
                },
                "expected_name": "S.G. MORRIS CO., LLC",
                "expected_count": 2,
                "expected_mode": "mixed",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "APH bridge mixed subsidiaries keeps primary",
                "properties": {
                    "_recipient_names": ["PCB PIEZOTRONICS OF NORTH CAROLINA, INC.", "THE MODAL SHOP, INC."],
                    "_recipient_legal_entity_names": ["PCB Piezotronics of North Carolina, Inc.", "The Modal Shop, Inc."],
                    "_recipient_legal_entity_keys": ["ex21:APH:pcb_nc:demo", "ex21:APH:modal_shop:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor", "legal_entity_ancestor"],
                    "target_ticker": "APH",
                },
                "expected_name": "PCB PIEZOTRONICS OF NORTH CAROLINA, INC.",
                "expected_count": 2,
                "expected_mode": "legal_entity_ancestor",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "JNJ mixed mode keeps first recipient",
                "properties": {
                    "_recipient_names": ["AMO SALES AND SERVICE, INC.", "JOHNSON & JOHNSON HEALTH CARE SYSTEMS INC."],
                    "_recipient_legal_entity_names": ["AMO Sales and Service, Inc."],
                    "_recipient_legal_entity_keys": ["ex21:JNJ:amo_sales_and_service:demo"],
                    "_recipient_resolution_modes": ["legal_entity_ancestor", "company_exact"],
                    "target_ticker": "JNJ",
                },
                "expected_name": "AMO SALES AND SERVICE, INC.",
                "expected_count": 2,
                "expected_mode": "mixed",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "Timken exact variants keep primary",
                "properties": {
                    "_recipient_names": ["TIMKEN CO", "THE TIMKEN CORPORATION"],
                    "_recipient_resolution_modes": ["company_exact", "company_exact"],
                },
                "expected_name": "TIMKEN CO",
                "expected_count": 2,
                "expected_mode": "company_exact",
                "expected_source": "",
                "expected_legal_name": "",
            },
            {
                "label": "Waste Management mixed prefix keeps primary",
                "properties": {
                    "_recipient_names": [
                        "WASTE MANAGEMENT OF NEW JERSEY, INC.",
                        "WASTE MANAGEMENT OF PENNSYLVANIA, INC.",
                        "WASTE MANAGEMENT OF PENNSYLVANIA INC",
                        "WASTE MANAGEMENT OF SOUTH CAROLINA, INC.",
                        "WASTE MANAGEMENT OF IDAHO INC",
                    ],
                    "_recipient_resolution_modes": ["company_prefix"] * 5,
                },
                "expected_name": "WASTE MANAGEMENT OF NEW JERSEY, INC.",
                "expected_count": 5,
                "expected_mode": "company_prefix",
                "expected_source": "",
                "expected_legal_name": "",
            },
        ]

        for case in real_world_cases:
            with self.subTest(label=case["label"]):
                props = dict(case["properties"])
                gns._phase8_apply_recipient_audit_metadata(props)
                self.assertEqual(case["expected_name"], props["recipient_name"])
                self.assertEqual(case["expected_count"], props["recipient_name_count"])
                self.assertEqual(case["expected_mode"], props["recipient_resolution_mode"])
                self.assertEqual(case["expected_source"], props["recipient_resolution_source"])
                self.assertEqual(case["expected_legal_name"], props["recipient_legal_entity_name"])
                self.assertLessEqual(len(props["recipient_name_examples"]), 5)
                self.assertLessEqual(len(props["recipient_legal_entity_name_examples"]), 5)

    def test_phase8_usaspending_edge_to_write_param_self_heals_real_world_audit_metadata(self):
        today_iso = "2026-03-13"
        real_world_cases = [
            ("IBM exact", {"recipient_name": "International Business Machines Corporation", "recipient_resolution_mode": "company_exact"}, "International Business Machines Corporation", 1, [], 0),
            ("Boeing exact", {"recipient_name": "The Boeing Company", "recipient_resolution_mode": "company_exact"}, "The Boeing Company", 1, [], 0),
            ("ACI bridge", {"recipient_name": "ACI WORLDWIDE FRANCE", "recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_key": "lei:254900SPUYFJY3VHA264", "recipient_legal_entity_name": "ACI WORLDWIDE FRANCE", "recipient_listed_ancestor_ticker": "ACIW"}, "ACI WORLDWIDE FRANCE", 1, ["ACI WORLDWIDE FRANCE"], 1),
            ("HPQ bridge fallback", {"recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_key": "ex21:HPQ:hpi_federal:demo", "recipient_legal_entity_name": "HPI Federal LLC", "recipient_listed_ancestor_ticker": "HPQ"}, "HPI Federal LLC", 1, ["HPI Federal LLC"], 1),
            ("KBR bridge", {"recipient_name": "KBR SERVICES, LLC", "recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_key": "ex21:KBR:kbr_services:demo", "recipient_legal_entity_name": "KBR Services, LLC", "recipient_listed_ancestor_ticker": "KBR"}, "KBR SERVICES, LLC", 1, ["KBR Services, LLC"], 1),
            ("USB canonical", {"recipient_name": "U.S. BANK NATIONAL ASSOCIATION", "recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_key": "ex21:USB:u_s_bank_national_association:demo", "recipient_legal_entity_name": "U.S. Bank National Association (a nationally chartered banking association)", "recipient_listed_ancestor_ticker": "USB"}, "U.S. BANK NATIONAL ASSOCIATION", 1, ["U.S. Bank National Association (a nationally chartered banking association)"], 1),
            ("Sea-Bird", {"recipient_name": "SEA-BIRD ELECTRONICS, INC", "recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_key": "ex21:VLTO:sea_bird_electronics:demo", "recipient_legal_entity_name": "Sea-Bird Electronics, Inc.", "recipient_listed_ancestor_ticker": "VLTO"}, "SEA-BIRD ELECTRONICS, INC", 1, ["Sea-Bird Electronics, Inc."], 1),
            ("Tri-City", {"recipient_name": "TRI-CITY ELECTRIC COMPANY OF IOWA", "recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_key": "ex21:PWR:tri_city_electric_company_of_iowa:demo", "recipient_legal_entity_name": "Tri-City Electric Company of Iowa, LLC", "recipient_listed_ancestor_ticker": "PWR"}, "TRI-CITY ELECTRIC COMPANY OF IOWA", 1, ["Tri-City Electric Company of Iowa, LLC"], 1),
            ("Pepsi bottling", {"recipient_name": "PEPSI-COLA METROPOLITAN BOTTLING COMPANY, INC.", "recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_key": "ex21:PEP:pepsi_cola_metropolitan_bottling:demo", "recipient_legal_entity_name": "Pepsi-Cola Metropolitan Bottling Company, Inc.", "recipient_listed_ancestor_ticker": "PEP"}, "PEPSI-COLA METROPOLITAN BOTTLING COMPANY, INC.", 1, ["Pepsi-Cola Metropolitan Bottling Company, Inc."], 1),
            ("CQ Roll Call", {"recipient_name": "CQ-ROLL CALL, INC", "recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_key": "ex21:NOTE:cq_roll_call:demo", "recipient_legal_entity_name": "CQ-Roll Call, Inc.", "recipient_listed_ancestor_ticker": "NOTE"}, "CQ-ROLL CALL, INC", 1, ["CQ-Roll Call, Inc."], 1),
            ("No recipient raw fallback", {"recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_key": "ex21:JNJ:amo_sales_and_service:demo", "recipient_legal_entity_name": "AMO Sales and Service, Inc.", "recipient_listed_ancestor_ticker": "JNJ"}, "AMO Sales and Service, Inc.", 1, ["AMO Sales and Service, Inc."], 1),
            ("Lumen examples primary", {"recipient_name_examples": ["LUMEN TECHNOLOGIES GOVERNMENT SOLUTIONS, INC", "LUMEN TECHNOLOGIES GOVERNMENT SOLUTIONS, INC."], "recipient_name_count": 2, "recipient_resolution_mode": "company_prefix"}, "LUMEN TECHNOLOGIES GOVERNMENT SOLUTIONS, INC", 2, [], 0),
            ("VLTO bridge examples primary", {"recipient_name_examples": ["X-RAY OPTICAL SYSTEMS, INC.", "SEA-BIRD ELECTRONICS, INC"], "recipient_name_count": 2, "recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_name_examples": ["X-Ray Optical Systems, Inc.", "Sea-Bird Electronics, Inc."], "recipient_legal_entity_name_count": 2, "recipient_listed_ancestor_ticker": "VLTO"}, "X-RAY OPTICAL SYSTEMS, INC.", 2, ["X-Ray Optical Systems, Inc.", "Sea-Bird Electronics, Inc."], 2),
            ("Sunbelt examples primary", {"recipient_name_examples": ["SUNBELT RENTALS, INC", "SUNBELT RENTALS, INC."], "recipient_name_count": 2, "recipient_resolution_mode": "company_exact"}, "SUNBELT RENTALS, INC", 2, [], 0),
            ("LOAR bridge examples primary", {"recipient_name_examples": ["HYDRA-ELECTRIC COMPANY", "SMR ACQUISITION LLC"], "recipient_name_count": 2, "recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_name_examples": ["Hydra-Electric Company", "SMR Acquisition LLC"], "recipient_legal_entity_name_count": 2, "recipient_listed_ancestor_ticker": "LOAR"}, "HYDRA-ELECTRIC COMPANY", 2, ["Hydra-Electric Company", "SMR Acquisition LLC"], 2),
            ("Lincoln mixed examples primary", {"recipient_name_examples": ["LINCOLN ELECTRIC COOPERATIVE INC", "LINCOLN ELECTRIC HOLDINGS INC"], "recipient_name_count": 2, "recipient_resolution_mode": "mixed"}, "LINCOLN ELECTRIC COOPERATIVE INC", 2, [], 0),
            ("Avnet examples primary", {"recipient_name_examples": ["AVNET, INC.", "AVNET INC."], "recipient_name_count": 2, "recipient_resolution_mode": "company_exact"}, "AVNET, INC.", 2, [], 0),
            ("ITT bridge examples primary", {"recipient_name_examples": ["ITT CANNON LLC", "ITT AEROSPACE CONTROLS LLC"], "recipient_name_count": 2, "recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_name_examples": ["ITT Cannon LLC", "ITT Aerospace Controls LLC"], "recipient_legal_entity_name_count": 2, "recipient_listed_ancestor_ticker": "ITT"}, "ITT CANNON LLC", 2, ["ITT Cannon LLC", "ITT Aerospace Controls LLC"], 2),
            ("Global examples primary", {"recipient_name_examples": ["GLOBAL INCORPORATED", "GLOBAL, INC", "GLOBAL ENTERPRISE, INC"], "recipient_name_count": 3, "recipient_resolution_mode": "company_exact"}, "GLOBAL INCORPORATED", 3, [], 0),
            ("Applied mixed examples primary", {"recipient_name_examples": ["S.G. MORRIS CO., LLC", "APPLIED INDUSTRIAL TECHNOLOGIES, INC."], "recipient_name_count": 2, "recipient_resolution_mode": "mixed", "recipient_legal_entity_name_examples": ["S. G. Morris Co., LLC"], "recipient_legal_entity_name_count": 1}, "S.G. MORRIS CO., LLC", 2, ["S. G. Morris Co., LLC"], 1),
            ("APH bridge examples primary", {"recipient_name_examples": ["PCB PIEZOTRONICS OF NORTH CAROLINA, INC.", "THE MODAL SHOP, INC."], "recipient_name_count": 2, "recipient_resolution_mode": "legal_entity_ancestor", "recipient_legal_entity_name_examples": ["PCB Piezotronics of North Carolina, Inc.", "The Modal Shop, Inc."], "recipient_legal_entity_name_count": 2, "recipient_listed_ancestor_ticker": "APH"}, "PCB PIEZOTRONICS OF NORTH CAROLINA, INC.", 2, ["PCB Piezotronics of North Carolina, Inc.", "The Modal Shop, Inc."], 2),
            ("JNJ mixed examples primary", {"recipient_name_examples": ["AMO SALES AND SERVICE, INC.", "JOHNSON & JOHNSON HEALTH CARE SYSTEMS INC."], "recipient_name_count": 2, "recipient_resolution_mode": "mixed", "recipient_legal_entity_name_examples": ["AMO Sales and Service, Inc."], "recipient_legal_entity_name_count": 1}, "AMO SALES AND SERVICE, INC.", 2, ["AMO Sales and Service, Inc."], 1),
            ("Waste Management examples primary", {"recipient_name_examples": ["WASTE MANAGEMENT OF NEW JERSEY, INC.", "WASTE MANAGEMENT OF PENNSYLVANIA, INC.", "WASTE MANAGEMENT OF PENNSYLVANIA INC", "WASTE MANAGEMENT OF SOUTH CAROLINA, INC.", "WASTE MANAGEMENT OF IDAHO INC"], "recipient_name_count": 5, "recipient_resolution_mode": "company_prefix"}, "WASTE MANAGEMENT OF NEW JERSEY, INC.", 5, [], 0),
            ("No metadata", {}, "", 0, [], 0),
        ]

        for label, props, expected_name, expected_count, expected_legal_examples, expected_legal_count in real_world_cases:
            with self.subTest(label=label):
                param = gns._phase8_usaspending_edge_to_write_param(
                    {"node_a": "TEST", "node_b": "dod", "properties": dict(props)},
                    today_iso,
                )
                self.assertEqual(expected_name, param["recipient_name"])
                self.assertEqual(expected_count, param["recipient_name_count"])
                self.assertEqual(expected_legal_examples, param["recipient_legal_entity_name_examples"])
                self.assertEqual(expected_legal_count, param["recipient_legal_entity_name_count"])
                self.assertEqual(today_iso, param["today"])

    def test_phase8_merge_resolved_usaspending_edge_keeps_primary_recipient_name_for_real_world_mixed_cases(self):
        real_world_cases = [
            ("Lumen punctuation variants", ["LUMEN TECHNOLOGIES GOVERNMENT SOLUTIONS, INC", "LUMEN TECHNOLOGIES GOVERNMENT SOLUTIONS, INC."], "company_prefix", "LUMEN TECHNOLOGIES GOVERNMENT SOLUTIONS, INC"),
            ("VLTO mixed bridge subsidiaries", ["X-RAY OPTICAL SYSTEMS, INC.", "SEA-BIRD ELECTRONICS, INC"], "legal_entity_ancestor", "X-RAY OPTICAL SYSTEMS, INC."),
            ("Sunbelt punctuation variants", ["SUNBELT RENTALS, INC", "SUNBELT RENTALS, INC."], "company_exact", "SUNBELT RENTALS, INC"),
            ("LOAR mixed bridge subsidiaries", ["HYDRA-ELECTRIC COMPANY", "SMR ACQUISITION LLC"], "legal_entity_ancestor", "HYDRA-ELECTRIC COMPANY"),
            ("Lincoln mixed resolution", ["LINCOLN ELECTRIC COOPERATIVE INC", "LINCOLN ELECTRIC HOLDINGS INC"], "mixed", "LINCOLN ELECTRIC COOPERATIVE INC"),
            ("Avnet punctuation variants", ["AVNET, INC.", "AVNET INC."], "company_exact", "AVNET, INC."),
            ("ITT mixed bridge subsidiaries", ["ITT CANNON LLC", "ITT AEROSPACE CONTROLS LLC"], "legal_entity_ancestor", "ITT CANNON LLC"),
            ("Global exact variants", ["GLOBAL INCORPORATED", "GLOBAL, INC", "GLOBAL ENTERPRISE, INC"], "company_exact", "GLOBAL INCORPORATED"),
            ("Applied mixed resolution", ["S.G. MORRIS CO., LLC", "APPLIED INDUSTRIAL TECHNOLOGIES, INC."], "mixed", "S.G. MORRIS CO., LLC"),
            ("Waste Management prefix aggregation", ["WASTE MANAGEMENT OF NEW JERSEY, INC.", "WASTE MANAGEMENT OF PENNSYLVANIA, INC.", "WASTE MANAGEMENT OF PENNSYLVANIA INC"], "company_prefix", "WASTE MANAGEMENT OF NEW JERSEY, INC."),
        ]

        for label, recipient_names, resolution_mode, expected_name in real_world_cases:
            with self.subTest(label=label):
                existing = {
                    "node_a": "TEST",
                    "node_b": "dod",
                    "confidence": 0.5,
                    "properties": {
                        "total_obligation": 100.0,
                        "award_count": 1,
                        "edge_state": "open",
                        "active_after": "2026-03-13",
                        "last_confirmed": "2026-03-13",
                    },
                }
                for idx, recipient_name in enumerate(recipient_names):
                    incoming = {
                        "node_a": "TEST",
                        "node_b": "dod",
                        "confidence": 0.5,
                        "properties": {
                            "recipient_name": recipient_name,
                            "recipient_resolution_mode": resolution_mode,
                            "total_obligation": float(idx + 1),
                            "award_count": 1,
                            "edge_state": "open",
                            "active_after": "2026-03-13",
                            "last_confirmed": "2026-03-13",
                        },
                    }
                    gns._phase8_merge_resolved_usaspending_edge(existing, incoming)
                self.assertEqual(expected_name, existing["properties"]["recipient_name"])
                self.assertEqual(len(recipient_names), existing["properties"]["recipient_name_count"])
                self.assertEqual(recipient_names[:5], existing["properties"]["recipient_name_examples"])

    def test_phase9_usaspending_merges_without_edge_state_in_relationship_key(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{"ticker": "IBM", "name": "International Business Machines Corporation"}])
                return iter([])

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        fake_session = _FakeSession()
        resolved_edges = [{
            "node_a": "IBM",
            "rel": "CONTRACTS_WITH",
            "node_b": "nasa",
            "confidence": 0.95,
            "source": "USASpending.gov",
            "properties": {
                "agency_name": "National Aeronautics and Space Administration",
                "total_obligation": 250000.0,
                "award_count": 2,
                "active_after": "2024-01-01",
                "last_confirmed": "2024-06-01",
                "valid_until": "2024-06-01",
                "edge_state": "closed",
            },
        }]

        with patch.object(gns, "_nexus_read_cached_json", return_value=[{"Recipient Name": "IBM"}]), \
             patch.object(gns, "_resolve_usaspending_to_edges", return_value=resolved_edges), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0):
            gns.phase9_usaspending(fake_session)

        joined_queries = "\n".join(query for query, _ in fake_session.queries)
        self.assertIn(
            "MERGE (c)-[r:CONTRACTS_WITH {source_scope: 'USASPENDING_AWARD'}]->(agency)",
            joined_queries,
        )
        self.assertNotIn(
            "MERGE (c)-[r:CONTRACTS_WITH {source_scope: 'USASPENDING_AWARD', edge_state: p.edge_state}]->(agency)",
            joined_queries,
        )

    def test_phase9_usaspending_counts_unique_relationships_and_repairs_blank_state_edges(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{"ticker": "IBM", "name": "International Business Machines Corporation"}])
                if "RETURN coalesce(r.edge_state, '') AS state, count(r) AS n" in self.query:
                    return iter([{"state": "open", "n": 1}])
                return iter([])

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 1}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        class _FakeHttpSession:
            def __init__(self):
                self.calls = 0

            def post(self, *_args, **_kwargs):
                self.calls += 1

                class _Resp:
                    def __init__(self, call_num):
                        self.call_num = call_num

                    def raise_for_status(self):
                        return None

                    def json(self):
                        base_row = {
                            "Recipient Name": "International Business Machines Corporation",
                            "Awarding Agency": "National Aeronautics and Space Administration",
                            "Award Amount": 100000,
                            "Start Date": "2024-01-01",
                            "End Date": "2024-06-01",
                        }
                        if self.call_num == 1:
                            return {
                                "results": [dict(base_row) for _ in range(10000)],
                                "page_metadata": {
                                    "hasNext": True,
                                    "last_record_unique_id": "cursor-1",
                                    "last_record_sort_value": 123.0,
                                },
                            }
                        return {
                            "results": [dict(base_row)],
                            "page_metadata": {
                                "hasNext": False,
                                "last_record_unique_id": None,
                                "last_record_sort_value": None,
                            },
                        }

                return _Resp(self.calls)

            def close(self):
                return None

        fake_session = _FakeSession()
        http_session = _FakeHttpSession()
        log_messages = []
        resolved_edge = {
            "node_a": "IBM",
            "rel": "CONTRACTS_WITH",
            "node_b": "nasa",
            "confidence": 0.95,
            "source": "USASpending.gov",
            "properties": {
                "agency_name": "National Aeronautics and Space Administration",
                "total_obligation": 100000.0,
                "award_count": 1,
                "active_after": "2024-01-01",
                "last_confirmed": "2024-06-01",
                "valid_until": "2024-06-01",
                "edge_state": "closed",
            },
        }

        with patch.object(gns, "_usaspending_http_session", return_value=http_session), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_log", side_effect=lambda msg, *_args, **_kwargs: log_messages.append(msg)), \
             patch.object(gns, "_log_stage_unexpected"), \
             patch.object(gns, "_phase9_report_progress"), \
             patch.object(gns, "_nexus_read_cached_json", return_value=None), \
             patch.object(gns, "_nexus_write_cached_json"), \
             patch.object(gns, "_nexus_stage_download"), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_usaspending_fetch_windows", return_value=[("2024-01-01", "2024-06-30")]), \
             patch.object(gns, "USASPENDING_PAGE_SIZE", 10000), \
             patch.object(gns, "USASPENDING_MAX_ROWS", 10001), \
             patch.object(gns, "USASPENDING_BREAK_EVERY", 50000), \
             patch.object(gns, "USASPENDING_BREAK_SECONDS", 0), \
             patch.object(gns.time, "sleep", return_value=None), \
             patch.object(gns, "_resolve_usaspending_to_edges", side_effect=[[resolved_edge], [resolved_edge]]):
            gns.phase9_usaspending(fake_session)

        joined_queries = "\n".join(query for query, _ in fake_session.queries)
        self.assertIn("r.edge_state = p.edge_state", joined_queries)
        self.assertIn("coalesce(r.current_run_token, '') = $run_token THEN coalesce(r.total_obligation, 0.0) + p.obligation", joined_queries)
        self.assertIn("coalesce(r.edge_state, 'open') <> 'closed'", joined_queries)
        self.assertTrue(any("1 unique CONTRACTS_WITH relationships touched from USASpending" in msg for msg in log_messages))

    def test_phase9_usaspending_fetches_across_time_windows(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{"ticker": "IBM", "name": "International Business Machines Corporation"}])
                return iter([])

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        class _FakeHttpSession:
            def __init__(self):
                self.payloads = []

            def post(self, *args, **kwargs):
                self.payloads.append(kwargs.get("json") or {})

                class _Resp:
                    def raise_for_status(self):
                        return None

                    def json(self):
                        return {
                            "results": [],
                            "page_metadata": {
                                "hasNext": False,
                                "last_record_unique_id": None,
                                "last_record_sort_value": None,
                            },
                        }

                return _Resp()

            def close(self):
                return None

        fake_session = _FakeSession()
        http_session = _FakeHttpSession()

        with patch.object(gns, "_usaspending_http_session", return_value=http_session), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_log_stage_unexpected"), \
             patch.object(gns, "_phase9_report_progress"), \
             patch.object(gns, "_nexus_read_cached_json", return_value=None), \
             patch.object(gns, "_nexus_write_cached_json"), \
             patch.object(gns, "_nexus_stage_download"), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_usaspending_fetch_windows", return_value=[("2024-01-01", "2024-03-31"), ("2024-04-01", "2024-06-30")]), \
             patch.object(gns.time, "sleep", return_value=None):
            gns.phase9_usaspending(fake_session)

        self.assertEqual(2, len(http_session.payloads))
        self.assertEqual(
            [{"start_date": "2024-01-01", "end_date": "2024-03-31"}],
            http_session.payloads[0]["filters"]["time_period"],
        )
        self.assertEqual(
            [{"start_date": "2024-04-01", "end_date": "2024-06-30"}],
            http_session.payloads[1]["filters"]["time_period"],
        )

    def test_phase9_usaspending_does_not_cache_partial_fetch_after_exhausted_cooldowns(self):
        import requests

        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{"ticker": "IBM", "name": "International Business Machines Corporation"}])
                return iter([])

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def run(self, query, **kwargs):
                return _FakeResult(query, kwargs)

        class _FakeHttpSession:
            def __init__(self):
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    class _Resp:
                        def raise_for_status(self):
                            return None

                        def json(self):
                            return {
                                "results": [{"Recipient Name": "IBM", "Awarding Agency": "NASA", "Award Amount": 100000, "Start Date": "2025-01-01", "End Date": "2025-06-01"}],
                                "page_metadata": {
                                    "hasNext": True,
                                    "last_record_unique_id": "cursor-1",
                                    "last_record_sort_value": 123,
                                },
                            }
                    return _Resp()
                raise requests.exceptions.ConnectionError(
                    "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
                )

            def close(self):
                return None

        fake_session = _FakeSession()
        http_session = _FakeHttpSession()

        with patch.object(gns, "_usaspending_http_session", return_value=http_session), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_log_stage_unexpected"), \
             patch.object(gns, "_phase9_report_progress"), \
             patch.object(gns, "_nexus_read_cached_json", return_value=None), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_usaspending_fetch_windows", return_value=[("2024-01-01", "2024-03-31")]), \
             patch.object(gns.time, "sleep", return_value=None), \
             patch.object(gns, "USASPENDING_FAILURE_BURST_THRESHOLD", 999), \
             patch.object(gns, "USASPENDING_PAGE_SIZE", 1), \
             patch.object(gns, "USASPENDING_BREAK_EVERY", 2000), \
             patch.object(gns, "USASPENDING_BREAK_SECONDS", 5), \
             patch.object(gns, "_nexus_write_cached_json") as mock_write_cache, \
             patch.object(gns, "_nexus_stage_download") as mock_stage_download:
            gns.phase9_usaspending(fake_session)

        mock_write_cache.assert_not_called()
        mock_stage_download.assert_not_called()

    def test_build_wikidata_controls_batch_param_falls_back_active_after_to_last_confirmed(self):
        params = gns._build_wikidata_controls_batch_param(
            {
                "node_a": "ICE",
                "node_b": "BKKT",
                "confidence": 0.8,
                "properties": {
                    "validation_mode": "sec_annual_filing",
                    "last_confirmed": "2026-03-10",
                },
            }
        )
        self.assertEqual("2026-03-10", params["active_after"])
        self.assertEqual("2026-03-10", params["last_confirmed"])

    def test_phase9_usaspending_transient_failure_burst_triggers_cooldown(self):
        import requests

        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{"ticker": "IBM", "name": "International Business Machines Corporation"}])
                return iter([])

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        class _FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "results": [],
                    "page_metadata": {
                        "hasNext": False,
                        "last_record_unique_id": None,
                        "last_record_sort_value": None,
                    },
                }

        post_calls = {"count": 0}

        def _fake_post(*args, **kwargs):
            post_calls["count"] += 1
            if post_calls["count"] <= 3:
                raise requests.exceptions.ConnectionError(
                    "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
                )
            return _FakeResponse()

        fake_session = _FakeSession()
        sleep_calls = []
        logged = []

        class _FakeHttpSession:
            def post(self, *args, **kwargs):
                return _fake_post(*args, **kwargs)

            def close(self):
                return None

        with patch.object(gns, "_usaspending_http_session", return_value=_FakeHttpSession()), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_log", side_effect=lambda msg, color="white": logged.append(msg)), \
             patch.object(gns, "_log_stage_unexpected"), \
             patch.object(gns, "_phase9_report_progress"), \
             patch.object(gns, "_nexus_read_cached_json", return_value=None), \
             patch.object(gns, "_nexus_write_cached_json"), \
             patch.object(gns, "_nexus_stage_download"), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns.time, "sleep", side_effect=lambda seconds: sleep_calls.append(seconds)), \
             patch.object(gns, "USASPENDING_FAILURE_BURST_THRESHOLD", 1), \
             patch.object(gns, "USASPENDING_FAILURE_BURST_COOLDOWN_SECONDS", 60):
            gns.phase9_usaspending(fake_session)

        self.assertIn(60, sleep_calls)
        self.assertTrue(any("transient failure burst" in msg.lower() for msg in logged))
        self.assertFalse(any("degraded pacing" in msg.lower() for msg in logged))
        self.assertFalse(any("15s" in msg.lower() and "pausing" in msg.lower() for msg in logged))

    def test_phase9_usaspending_resets_http_session_on_transient_disconnect(self):
        import requests

        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{"ticker": "IBM", "name": "International Business Machines Corporation"}])
                return iter([])

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        class _FakeHttpSession:
            def __init__(self):
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise requests.exceptions.ConnectionError(
                        "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
                    )

                class _Resp:
                    def raise_for_status(self):
                        return None

                    def json(self):
                        return {
                            "results": [],
                            "page_metadata": {
                                "hasNext": False,
                                "last_record_unique_id": None,
                                "last_record_sort_value": None,
                            },
                        }

                return _Resp()

            def close(self):
                return None

        fake_session = _FakeSession()
        http_session = _FakeHttpSession()

        with patch.object(gns, "_usaspending_http_session", return_value=http_session), \
             patch.object(gns, "_usaspending_reset_http_session") as mock_reset, \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_log_stage_unexpected"), \
             patch.object(gns, "_phase9_report_progress"), \
             patch.object(gns, "_nexus_read_cached_json", return_value=None), \
             patch.object(gns, "_nexus_write_cached_json"), \
             patch.object(gns, "_nexus_stage_download"), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns.time, "sleep", return_value=None):
            gns.phase9_usaspending(fake_session)

        self.assertGreaterEqual(mock_reset.call_count, 1)

    def test_usaspending_http_session_uses_direct_close_connections(self):
        gns._usaspending_reset_http_session()
        try:
            session = gns._usaspending_http_session()
            self.assertFalse(session.trust_env)
            self.assertEqual("close", session.headers.get("Connection"))
            self.assertEqual(
                "IntelliStockV4/GraphNexus Phase8 USASpending Fetcher",
                session.headers.get("User-Agent"),
            )
        finally:
            gns._usaspending_reset_http_session()

    def test_phase9_usaspending_failure_burst_resumes_normal_breaks(self):
        import requests

        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{"ticker": "IBM", "name": "International Business Machines Corporation"}])
                return iter([])

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        class _FakeResponse:
            def __init__(self, has_next=False, last_id=None, last_sort=None):
                self._has_next = has_next
                self._last_id = last_id
                self._last_sort = last_sort

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "results": [{"Recipient Name": "IBM", "Awarding Agency": "NASA", "Award Amount": 100000, "Start Date": "2025-01-01", "End Date": "2025-06-01"}],
                    "page_metadata": {
                        "hasNext": self._has_next,
                        "last_record_unique_id": self._last_id,
                        "last_record_sort_value": self._last_sort,
                    },
                }

        call_counter = {"count": 0}

        def _fake_post(*args, **kwargs):
            call_counter["count"] += 1
            if call_counter["count"] == 1:
                raise requests.exceptions.ConnectionError(
                    "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))"
                )
            if call_counter["count"] == 2:
                return _FakeResponse(has_next=True, last_id="cursor-1", last_sort=123)
            if call_counter["count"] == 3:
                return _FakeResponse(has_next=True, last_id="cursor-2", last_sort=122)
            return _FakeResponse(has_next=False, last_id=None, last_sort=None)

        fake_session = _FakeSession()
        sleep_calls = []
        logged = []

        with ExitStack() as stack:
            class _FakeHttpSession:
                def post(self, *args, **kwargs):
                    return _fake_post(*args, **kwargs)

                def close(self):
                    return None

            stack.enter_context(patch.object(gns, "_usaspending_http_session", return_value=_FakeHttpSession()))
            stack.enter_context(patch.object(gns, "_nexus_stage_reset"))
            stack.enter_context(patch.object(gns, "_progress"))
            stack.enter_context(patch.object(gns, "_log", side_effect=lambda msg, color="white": logged.append(msg)))
            stack.enter_context(patch.object(gns, "_log_stage_unexpected"))
            stack.enter_context(patch.object(gns, "_phase9_report_progress"))
            stack.enter_context(patch.object(gns, "_nexus_read_cached_json", return_value=None))
            stack.enter_context(patch.object(gns, "_nexus_write_cached_json"))
            stack.enter_context(patch.object(gns, "_nexus_stage_download"))
            stack.enter_context(patch.object(gns, "_sync_graph_edge_intervals"))
            stack.enter_context(patch.object(gns, "_retire_relationships", return_value=0))
            stack.enter_context(patch.object(gns.time, "sleep", side_effect=lambda seconds: sleep_calls.append(seconds)))
            stack.enter_context(patch.object(gns, "USASPENDING_FAILURE_BURST_THRESHOLD", 1))
            stack.enter_context(patch.object(gns, "USASPENDING_FAILURE_BURST_COOLDOWN_SECONDS", 60))
            stack.enter_context(patch.object(gns, "USASPENDING_FAILURE_BURST_MAX_COOLDOWN_SECONDS", 60))
            stack.enter_context(patch.object(gns, "USASPENDING_PAGE_SIZE", 1))
            stack.enter_context(patch.object(gns, "USASPENDING_BREAK_EVERY", 2))
            stack.enter_context(patch.object(gns, "USASPENDING_BREAK_SECONDS", 5))
            gns.phase9_usaspending(fake_session)

        self.assertIn(60, sleep_calls)
        self.assertIn(5, sleep_calls)
        self.assertNotIn(15, sleep_calls)
        self.assertFalse(any("degraded pacing" in msg.lower() for msg in logged))
        self.assertTrue(any("resuming normal pacing" in msg.lower() or "normal pacing resumes after cooldown" in msg.lower() for msg in logged))

    def test_usaspending_wait_for_request_window_sleeps_until_window_frees(self):
        request_timestamps = deque([0.0, 10.0])
        sleep_calls = []
        monotonic_values = iter([20.0, 30.0])

        with patch.object(gns.time, "monotonic", side_effect=lambda: next(monotonic_values)), \
             patch.object(gns.time, "sleep", side_effect=lambda seconds: sleep_calls.append(seconds)):
            waited = gns._usaspending_wait_for_request_window(
                request_timestamps,
                window_seconds=30,
                max_requests=2,
            )

        self.assertEqual([10.0], sleep_calls)
        self.assertAlmostEqual(10.0, waited, places=6)
        self.assertEqual([10.0, 30.0], list(request_timestamps))

    def test_sec_identity_matching_tolerates_suffix_noise_but_rejects_ticker_reuse(self):
        self.assertTrue(gns._company_names_loosely_match(
            "ABM Industries, Inc.",
            "ABM INDUSTRIES INC /DE/",
        ))
        self.assertTrue(gns._company_names_loosely_match(
            "Motorola Solutions Inc. New",
            "MOTOROLA SOLUTIONS, INC.",
        ))
        self.assertFalse(gns._company_names_loosely_match(
            "Kustom Entertainment, Inc.",
            "DIGITAL ALLY, INC.",
        ))

    def test_classify_company_records_for_sec_identity_drops_reused_ticker_cik(self):
        accepted, rejected = gns._classify_company_records_for_sec_identity(
            [
                {"ticker": "KUST", "name": "Kustom Entertainment, Inc.", "canonical_name": "Kustom Entertainment, Inc.", "cik": "0001342958"},
                {"ticker": "ABM", "name": "ABM Industries, Inc.", "canonical_name": "ABM Industries, Inc.", "cik": "0000771497"},
            ],
            {
                "KUST": {"cik": "0001342958", "title": "DIGITAL ALLY, INC."},
                "ABM": {"cik": "0000771497", "title": "ABM INDUSTRIES INC /DE/"},
            },
        )
        self.assertEqual(["ABM"], [row["ticker"] for row in accepted if row.get("verified")])
        self.assertEqual(["KUST"], [row["ticker"] for row in rejected])

    def test_normalize_sector_name_rejects_sec_division_labels(self):
        fallback = gns._normalize_sector_name(None, "2834")
        self.assertEqual(fallback, gns._normalize_sector_name("Corp Fin", "2834"))
        self.assertNotEqual("Corp Fin", gns._normalize_sector_name("Corp Fin", "2834"))

    def test_gleif_parent_match_accepts_stored_lei_legal_name(self):
        self.assertTrue(gns._gleif_parent_ticker_matches_name(
            {"ticker": "BLCO", "lei_legal_name": "Bausch + Lomb Corporation"},
            "Bausch + Lomb Corporation",
        ))

    def test_sec_resolution_blocks_vendors_and_security_instruments(self):
        title_to_ticker = {
            "donnelley financial solutions, inc.": "DFIN",
            sec._company_identity_key("Donnelley Financial Solutions, Inc."): "DFIN",
            "fifth third bancorp": "FITB",
            sec._company_identity_key("Fifth Third Bancorp"): "FITB",
        }
        self.assertEqual((None, 0.0), sec._resolve_customer_to_ticker("Donnelley Financial Solutions, Inc.", title_to_ticker))
        self.assertEqual((None, 0.0), sec._resolve_customer_to_ticker(
            "Fifth Third Bancorp Depositary Shares Representing a 1/40th Ownership Interest",
            title_to_ticker,
        ))

    def test_sec_company_identity_key_blocks_same_issuer_partnerships(self):
        self.assertEqual(
            sec._company_identity_key("Kelly Services, Inc."),
            sec._company_identity_key("Kelly Services, Inc. Class A Common Stock"),
        )

    def test_reit_tenant_context_is_not_treated_as_supply_chain(self):
        ctx = (
            "The three largest tenants in our office portfolio were Google LLC, "
            "LPL Holdings, Inc. and Autodesk, Inc. No tenant accounted for more than 10% "
            "of total rental revenue. Our office segment contributed 47.2% of our total revenue."
        )
        self.assertTrue(sec._context_is_real_estate_tenant_disclosure(ctx, company_name="Autodesk, Inc."))
        self.assertIsNone(sec._extract_revenue_pct(ctx, company_name="Autodesk, Inc."))

    def test_disclosure_signal_helper_includes_agreement_phrases(self):
        self.assertTrue(sec._has_any_disclosure_signal(
            "The company entered into a supply agreement with Acme Corporation."
        ))
        self.assertFalse(sec._has_any_disclosure_signal("This filing contains only generic risk factors."))

    def test_extract_ctx_candidate_names_finds_counterparties_without_spacy(self):
        names = sec._extract_ctx_candidate_names(
            "The supply agreement with Acme Corporation and Beta Systems was renewed this quarter."
        )
        self.assertIn("Acme Corporation", names)
        self.assertIn("Beta Systems", names)

    def test_focus_phrase_context_limits_far_away_names(self):
        ctx = (
            "Our significant customer was Acme Corporation under a multi-year supply agreement. "
            "Several pages later Beta Systems discussed an unrelated acquisition and financing update."
        )
        focused = sec._focus_phrase_context(ctx, "significant customer", before=40, after=90)
        names = sec._extract_ctx_candidate_names(focused)
        self.assertIn("Acme Corporation", names)
        self.assertNotIn("Beta Systems", names)

    def test_extract_signal_centered_plain_text_limits_far_away_html_noise(self):
        html = (
            "<html><body>"
            "<div>Our significant customer was Acme Corporation under a multi-year supply agreement.</div>"
            f"<div>{'x' * 400}</div>"
            "<div>Beta Systems discussed an unrelated financing update.</div>"
            "</body></html>"
        )
        with patch.object(sec, "SEC_EDGAR_SIGNAL_SEGMENT_BEFORE", 40), patch.object(
            sec,
            "SEC_EDGAR_SIGNAL_SEGMENT_AFTER",
            140,
        ):
            focused = sec._extract_signal_centered_plain_text(html)
        self.assertIn("Acme Corporation", focused)
        self.assertNotIn("Beta Systems", focused)

    def test_extract_signal_centered_plain_text_keeps_wide_customer_tables(self):
        html = (
            "<html><body>"
            "<p>The following table provides information regarding each of our major customers.</p>"
            f"<div>{'x' * 12000}</div>"
            "<p>Cardinal Health</p><p>19 %</p>"
            "</body></html>"
        )
        with patch.object(sec, "SEC_EDGAR_SIGNAL_SEGMENT_BEFORE", 40), patch.object(
            sec,
            "SEC_EDGAR_SIGNAL_SEGMENT_AFTER",
            15000,
        ):
            focused = sec._extract_signal_centered_plain_text(html)
        self.assertIn("Cardinal Health", focused)

    def test_parse_10k_drops_reit_license_table_false_positive(self):
        html = (
            "<html><body>"
            "The following table shows our largest tenants including Under Armour, Inc. "
            "We exclude license agreements, seasonal tenants and month-to-month leases."
            "</body></html>"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
            tmp.write(html)
            tmp_path = tmp.name
        try:
            with patch.object(sec, "_extract_ctx_candidate_names", return_value=["Under Armour, Inc."]), patch.object(
                sec,
                "_resolve_customer_to_ticker",
                return_value=("UA", 1.0),
            ):
                edges = sec._parse_10k_and_extract_relationships(
                    "SKT",
                    tmp_path,
                    title_to_ticker={"under armour": "UA"},
                    ticker_to_title={
                        "SKT": "Tanger Inc.",
                        "UA": "Under Armour, Inc.",
                    },
                    filing_date="2026-02-26",
                )
            self.assertEqual([], edges)
        finally:
            os.unlink(tmp_path)

    def test_parse_10k_keeps_direct_agreement_counterparty_anchor(self):
        html = (
            "<html><body>"
            "We entered into a license agreement with Beta Systems Corporation during the year."
            "</body></html>"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
            tmp.write(html)
            tmp_path = tmp.name
        try:
            with patch.object(sec, "_extract_ctx_candidate_names", return_value=["Beta Systems Corporation"]), patch.object(
                sec,
                "_resolve_customer_to_ticker",
                return_value=("BETA", 1.0),
            ):
                edges = sec._parse_10k_and_extract_relationships(
                    "MSFT",
                    tmp_path,
                    title_to_ticker={"beta systems corporation": "BETA"},
                    ticker_to_title={
                        "MSFT": "Microsoft Corporation",
                        "BETA": "Beta Systems Corporation",
                    },
                    filing_date="2026-03-08",
                )
            self.assertEqual(1, len(edges))
            self.assertEqual("STRATEGIC_PARTNER", edges[0]["edge_type"])
            self.assertEqual(("BETA", "MSFT"), tuple(sorted((edges[0]["sup"], edges[0]["cust"]))))
        finally:
            os.unlink(tmp_path)

    def test_parse_10k_keeps_joint_venture_between_anchor(self):
        html = (
            "<html><body>"
            "The joint venture between Capital Southwest Corp and Main Street Capital Corporation "
            "was dissolved during the year."
            "</body></html>"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
            tmp.write(html)
            tmp_path = tmp.name
        try:
            with patch.object(sec, "_extract_ctx_candidate_names", return_value=["Main Street Capital Corporation"]), patch.object(
                sec,
                "_resolve_customer_to_ticker",
                return_value=("MAIN", 1.0),
            ):
                edges = sec._parse_10k_and_extract_relationships(
                    "CSWC",
                    tmp_path,
                    title_to_ticker={"main street capital corporation": "MAIN"},
                    ticker_to_title={
                        "CSWC": "Capital Southwest Corp",
                        "MAIN": "Main Street Capital Corporation",
                    },
                    filing_date="2025-05-20",
                )
            self.assertEqual(1, len(edges))
            self.assertEqual("STRATEGIC_PARTNER", edges[0]["edge_type"])
            self.assertEqual(("CSWC", "MAIN"), tuple(sorted((edges[0]["sup"], edges[0]["cust"]))))
        finally:
            os.unlink(tmp_path)

    def test_parse_10k_keeps_joint_venture_together_with_anchor(self):
        html = (
            "<html><body>"
            "We, together with Blackstone Inc., formed a joint venture to develop a studio campus."
            "</body></html>"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
            tmp.write(html)
            tmp_path = tmp.name
        try:
            with patch.object(sec, "_extract_ctx_candidate_names", return_value=["Blackstone Inc"]), patch.object(
                sec,
                "_resolve_customer_to_ticker",
                return_value=("BX", 1.0),
            ):
                edges = sec._parse_10k_and_extract_relationships(
                    "VNO",
                    tmp_path,
                    title_to_ticker={"blackstone inc": "BX"},
                    ticker_to_title={
                        "VNO": "Vornado Realty Trust",
                        "BX": "Blackstone Inc.",
                    },
                    filing_date="2026-02-09",
                )
            self.assertEqual(1, len(edges))
            self.assertEqual("STRATEGIC_PARTNER", edges[0]["edge_type"])
            self.assertEqual(("BX", "VNO"), tuple(sorted((edges[0]["sup"], edges[0]["cust"]))))
        finally:
            os.unlink(tmp_path)

    def test_extract_8k_counterparty_candidates_focuses_on_local_agreement_party(self):
        html = (
            "<html><body>"
            "Item 1.01 Entry into a Material Definitive Agreement. "
            "On March 1, 2026, the Company entered into a supply agreement with Beta Systems Corporation. "
            "Later, Target Corporation was mentioned in a separate market update."
            "</body></html>"
        )
        candidates = sec._extract_8k_counterparty_candidates(html)
        names = [name for name, _ctx in candidates]
        self.assertIn("Beta Systems Corporation", names)
        beta_ctx = next(ctx for name, ctx in candidates if name == "Beta Systems Corporation")
        self.assertTrue(sec._context_has_8k_counterparty_anchor(beta_ctx, "Beta Systems Corporation"))
        self.assertFalse(sec._context_has_8k_counterparty_anchor(beta_ctx, "Target Corporation"))

    def test_patentsview_resolution_aggregates_patent_metadata(self):
        patents = [
            {
                "patent_id": "US1",
                "patent_date": "2024-01-10",
                "assignees": [
                    {"assignee_organization": "Microsoft Corporation"},
                    {"assignee_organization": "OpenAI OpCo, LLC"},
                ],
            },
            {
                "patent_id": "US2",
                "patent_date": "2025-03-15",
                "assignees": [
                    {"assignee_organization": "Microsoft Corporation"},
                    {"assignee_organization": "OpenAI OpCo, LLC"},
                ],
            },
        ]
        with patch.object(gns, "_log"):
            edges = gns._resolve_patentsview_to_edges(
                patents,
                {
                    "MSFT": "Microsoft Corporation",
                    "OPEN": "OpenAI OpCo, LLC",
                },
            )
        self.assertEqual(1, len(edges))
        props = edges[0]["properties"]
        self.assertEqual(2, props["patent_count"])
        self.assertEqual(["US1", "US2"], props["patent_ids"])
        self.assertEqual("2024-01-10", props["active_after"])
        self.assertEqual("2025-03-15", props["last_confirmed"])

    def test_build_patentsview_company_resolution_index_drops_ambiguous_names(self):
        index = gns._build_patentsview_company_resolution_index(
            {
                "AAA": "Acme Corporation",
                "BBB": "Acme Corporation",
            }
        )
        self.assertNotIn("acme corporation", index["exact_to_ticker"])
        self.assertNotIn("acme corporation", index["company_name_to_ticker"])
        self.assertNotIn("acme", index["norm_to_ticker"])

    def test_resolve_patentsview_to_entity_edges_accepts_prebuilt_indexes(self):
        patents = [
            {
                "patent_id": "US1",
                "patent_date": "2024-01-10",
                "assignees": [
                    {"assignee_organization": "Microsoft Corporation"},
                    {"assignee_organization": "OpenAI OpCo, LLC"},
                ],
            },
        ]
        company_index = gns._build_patentsview_company_resolution_index({"MSFT": "Microsoft Corporation"})
        legal_index = gns._build_patentsview_legal_entity_resolution_index(
            [{
                "entity_key": "lei:openai-opco",
                "display_name": "OpenAI OpCo, LLC",
                "legal_name": "OpenAI OpCo, LLC",
                "aliases": ["OpenAI OpCo"],
                "entity_kind": "legal_entity",
                "has_public_company": False,
            }]
        )
        with patch.object(gns, "_build_patentsview_company_resolution_index", side_effect=AssertionError("should reuse prebuilt company index")), \
             patch.object(gns, "_build_patentsview_legal_entity_resolution_index", side_effect=AssertionError("should reuse prebuilt legal index")), \
             patch.object(gns, "_log"):
            edges = gns._resolve_patentsview_to_entity_edges(
                patents,
                {"MSFT": "Microsoft Corporation"},
                legal_entity_records=[],
                company_index=company_index,
                legal_index=legal_index,
            )
        self.assertEqual(1, len(edges))
        self.assertEqual("MSFT", edges[0]["node_a"])
        self.assertEqual("lei:openai-opco", edges[0]["node_b"])

    def test_patentsview_entity_resolution_links_existing_legal_entities_and_excludes_universities(self):
        patents = [
            {
                "patent_id": "US1",
                "patent_date": "2024-01-10",
                "assignees": [
                    {"assignee_organization": "Microsoft Corporation"},
                    {"assignee_organization": "OpenAI OpCo, LLC"},
                    {"assignee_organization": "National Taiwan University"},
                ],
            },
        ]
        legal_entities = [{
            "entity_key": "lei:openai-opco",
            "display_name": "OpenAI OpCo, LLC",
            "legal_name": "OpenAI OpCo, LLC",
            "aliases": ["OpenAI OpCo, LLC", "OpenAI OpCo"],
            "entity_kind": "legal_entity",
            "has_public_company": False,
        }]
        with patch.object(gns, "_log"):
            edges = gns._resolve_patentsview_to_entity_edges(
                patents,
                {"MSFT": "Microsoft Corporation"},
                legal_entity_records=legal_entities,
            )
        self.assertEqual(1, len(edges))
        edge = edges[0]
        self.assertEqual("MSFT", edge["node_a"])
        self.assertEqual("lei:openai-opco", edge["node_b"])
        self.assertEqual("Company", edge["properties"]["node_a_kind"])
        self.assertEqual("LegalEntity", edge["properties"]["node_b_kind"])

    def test_phase11_patents_writes_company_to_legal_entity_edges(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                if "RETURN c.ticker AS ticker, c.name AS name" in self.query:
                    return iter([{"ticker": "MSFT", "name": "Microsoft Corporation"}])
                if "MATCH (le:LegalEntity)" in self.query:
                    return iter([{
                        "entity_key": "lei:openai-opco",
                        "display_name": "OpenAI OpCo, LLC",
                        "legal_name": "OpenAI OpCo, LLC",
                        "aliases": ["OpenAI OpCo, LLC"],
                        "entity_kind": "legal_entity",
                        "has_public_company": False,
                    }])
                return iter([])

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        fake_session = _FakeSession()
        patent_edge = {
            "node_a": "MSFT",
            "rel": "PATENT_PARTNER",
            "node_b": "lei:openai-opco",
            "confidence": 0.75,
            "source": "PatentsView",
            "properties": {
                "patent_ids": ["US1"],
                "patent_count": 1,
                "active_after": "2024-01-10",
                "last_confirmed": "2024-01-10",
                "node_a_kind": "Company",
                "node_b_kind": "LegalEntity",
            },
        }

        def _fake_fetch(ticker_to_name, legal_entity_records=None, progress_cb=None, edge_cb=None, edge_cb_batch_size=50):
            self.assertEqual(["MSFT"], sorted(ticker_to_name))
            self.assertEqual(1, len(legal_entity_records or []))
            if edge_cb:
                edge_cb([patent_edge])
            return [patent_edge]

        with patch.object(gns, "_fetch_patentsview_coassignees", side_effect=_fake_fetch), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0):
            gns.phase11_patents(fake_session)

        joined_queries = "\n".join(query for query, _ in fake_session.queries)
        self.assertIn("MATCH (a:Company {ticker: p.a_ref}), (b:LegalEntity {entity_key: p.b_ref})", joined_queries)

    def test_phase12_8k_agreements_summary_log_uses_phase11_label(self):
        class _FakeResult:
            def __iter__(self):
                return iter([])

            def consume(self):
                return self

            def single(self):
                return {"closed": 0}

        class _FakeSession:
            def run(self, query, **kwargs):
                return _FakeResult()

        with patch.object(gns, "_nexus_read_cached_json", return_value=[]), \
             patch.object(gns, "_nexus_historical_start_date", return_value=""), \
             patch.object(gns, "_phase12_should_ignore_existing_coverage", return_value=False), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_prune_invalid_strategic_partner_edges", return_value=0), \
             patch.object(gns, "_log") as log_mock:
            gns.phase12_8k_agreements(_FakeSession())

        log_messages = [call.args[0] for call in log_mock.call_args_list if call.args]
        self.assertIn(
            "Phase 11 done: 0 live STRATEGIC_PARTNER edges from 8-K agreements (0 unique relationship upserts from 0 raw 8-K matches).",
            log_messages,
        )

    def test_phase11_prepare_8k_partner_batch_dedupes_real_world_pairs(self):
        raw_edges = [
            {"sup": "ALNY", "cust": "TNYA", "confidence": 0.80, "last_confirmed": "2026-02-01", "active_after": "2026-01-25"},
            {"sup": "TNYA", "cust": "ALNY", "confidence": 0.92, "last_confirmed": "2026-03-02", "active_after": "2026-01-15"},
            {"sup": "ABBV", "cust": "XLO", "confidence": 0.85, "last_confirmed": "2026-02-10", "active_after": "2026-02-10"},
            {"sup": "ABT", "cust": "SENS", "confidence": 0.81, "last_confirmed": "2026-02-14", "active_after": "2026-02-14"},
            {"sup": "AKBA", "cust": "CYCN", "confidence": 0.86, "last_confirmed": "2026-02-18", "active_after": "2026-02-18"},
            {"sup": "ALKS", "cust": "AMRX", "confidence": 0.83, "last_confirmed": "2026-02-22", "active_after": "2026-02-22"},
            {"sup": "ALKS", "cust": "JAZZ", "confidence": 0.82, "last_confirmed": "2026-02-24", "active_after": "2026-02-24"},
            {"sup": "ALSN", "cust": "DAN", "confidence": 0.84, "last_confirmed": "2026-02-25", "active_after": "2026-02-25"},
            {"sup": "AM", "cust": "AR", "confidence": 0.90, "last_confirmed": "2026-02-26", "active_after": "2026-02-26"},
            {"sup": "AR", "cust": "AM", "confidence": 0.88, "last_confirmed": "2026-02-27", "active_after": "2026-02-20"},
            {"sup": "AM", "cust": "INR", "confidence": 0.80, "last_confirmed": "2026-02-28", "active_after": "2026-02-28"},
            {"sup": "AM", "cust": "NOG", "confidence": 0.87, "last_confirmed": "2026-03-01", "active_after": "2026-03-01"},
            {"sup": "AMD", "cust": "SANM", "confidence": 0.91, "last_confirmed": "2026-03-01", "active_after": "2026-03-01"},
            {"sup": "AMWL", "cust": "RNGR", "confidence": 0.80, "last_confirmed": "2026-03-02", "active_after": "2026-03-02"},
            {"sup": "ANAB", "cust": "VNDA", "confidence": 0.82, "last_confirmed": "2026-03-03", "active_after": "2026-03-03"},
            {"sup": "GM", "cust": "LAC", "confidence": 0.89, "last_confirmed": "2026-03-04", "active_after": "2026-03-04"},
            {"sup": "NVAX", "cust": "PFE", "confidence": 0.93, "last_confirmed": "2026-03-05", "active_after": "2026-03-05"},
            {"sup": "PLUG", "cust": "WMT", "confidence": 0.88, "last_confirmed": "2026-03-06", "active_after": "2026-03-06"},
        ]

        params, pair_keys, raw_valid_count = gns._phase11_prepare_8k_partner_batch(raw_edges)
        by_pair = {
            gns._company_ticker_pair_key(row["sup"], row["cust"], directed=False): row
            for row in params
        }

        self.assertEqual(18, raw_valid_count)
        self.assertEqual(16, len(params))
        self.assertEqual(16, len(pair_keys))
        self.assertEqual(0.92, by_pair[("ALNY", "TNYA")]["conf"])
        self.assertEqual("2026-01-15", by_pair[("ALNY", "TNYA")]["active_after"])
        self.assertEqual("2026-03-02", by_pair[("ALNY", "TNYA")]["last_confirmed"])
        self.assertEqual(0.90, by_pair[("AM", "AR")]["conf"])
        self.assertEqual("2026-02-20", by_pair[("AM", "AR")]["active_after"])
        self.assertEqual("2026-02-27", by_pair[("AM", "AR")]["last_confirmed"])
        self.assertIn(("AMD", "SANM"), pair_keys)
        self.assertIn(("GM", "LAC"), pair_keys)
        self.assertIn(("NVAX", "PFE"), pair_keys)
        self.assertIn(("PLUG", "WMT"), pair_keys)

    def test_phase12_8k_agreements_summary_reports_live_count_not_raw_callback_rows(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def __iter__(self):
                return iter([])

            def consume(self):
                return self

            def single(self):
                if "RETURN collect(DISTINCT CASE" in self.query:
                    batch = list(self.kwargs.get("batch") or [])
                    return {"pair_keys": [[row["sup"], row["cust"]] for row in batch]}
                if "RETURN count(r) AS cnt" in self.query:
                    return {"cnt": 12}
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        cached_edges = [
            {"sup": "ALNY", "cust": "TNYA", "confidence": 0.80, "last_confirmed": "2026-02-01", "active_after": "2026-01-25"},
            {"sup": "TNYA", "cust": "ALNY", "confidence": 0.92, "last_confirmed": "2026-03-02", "active_after": "2026-01-15"},
            {"sup": "ABBV", "cust": "XLO", "confidence": 0.85, "last_confirmed": "2026-02-10", "active_after": "2026-02-10"},
            {"sup": "ABT", "cust": "SENS", "confidence": 0.81, "last_confirmed": "2026-02-14", "active_after": "2026-02-14"},
            {"sup": "AKBA", "cust": "CYCN", "confidence": 0.86, "last_confirmed": "2026-02-18", "active_after": "2026-02-18"},
            {"sup": "ALKS", "cust": "AMRX", "confidence": 0.83, "last_confirmed": "2026-02-22", "active_after": "2026-02-22"},
            {"sup": "ALKS", "cust": "JAZZ", "confidence": 0.82, "last_confirmed": "2026-02-24", "active_after": "2026-02-24"},
            {"sup": "ALSN", "cust": "DAN", "confidence": 0.84, "last_confirmed": "2026-02-25", "active_after": "2026-02-25"},
            {"sup": "AM", "cust": "AR", "confidence": 0.90, "last_confirmed": "2026-02-26", "active_after": "2026-02-26"},
            {"sup": "AR", "cust": "AM", "confidence": 0.88, "last_confirmed": "2026-02-27", "active_after": "2026-02-20"},
            {"sup": "AM", "cust": "INR", "confidence": 0.80, "last_confirmed": "2026-02-28", "active_after": "2026-02-28"},
            {"sup": "AM", "cust": "NOG", "confidence": 0.87, "last_confirmed": "2026-03-01", "active_after": "2026-03-01"},
            {"sup": "AMD", "cust": "SANM", "confidence": 0.91, "last_confirmed": "2026-03-01", "active_after": "2026-03-01"},
            {"sup": "AMWL", "cust": "RNGR", "confidence": 0.80, "last_confirmed": "2026-03-02", "active_after": "2026-03-02"},
            {"sup": "ANAB", "cust": "VNDA", "confidence": 0.82, "last_confirmed": "2026-03-03", "active_after": "2026-03-03"},
            {"sup": "GM", "cust": "LAC", "confidence": 0.89, "last_confirmed": "2026-03-04", "active_after": "2026-03-04"},
            {"sup": "NVAX", "cust": "PFE", "confidence": 0.93, "last_confirmed": "2026-03-05", "active_after": "2026-03-05"},
            {"sup": "PLUG", "cust": "WMT", "confidence": 0.88, "last_confirmed": "2026-03-06", "active_after": "2026-03-06"},
        ]

        with patch.object(gns, "_nexus_read_cached_json", return_value=cached_edges), \
             patch.object(gns, "_nexus_historical_start_date", return_value=""), \
             patch.object(gns, "_phase12_should_ignore_existing_coverage", return_value=False), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "_prune_invalid_strategic_partner_edges", return_value=0), \
             patch.object(gns, "_log") as log_mock:
            gns.phase12_8k_agreements(_FakeSession())

        log_messages = [call.args[0] for call in log_mock.call_args_list if call.args]
        self.assertIn(
            "Phase 11 done: 12 live STRATEGIC_PARTNER edges from 8-K agreements (16 unique relationship upserts from 18 raw 8-K matches).",
            log_messages,
        )

    def test_patentsview_llm_uses_legal_entity_ancestor_tickers_as_candidates(self):
        prompts: list[str] = []

        def _fake_structured_call(provider, api_key, model, prompt, output_type, **kwargs):
            prompts.append(prompt)
            return output_type(results=[])

        legal_index = {
            "exact_to_entity": {"airbnb ireland unlimited company": "lei:abnb-ireland"},
            "norm_to_entity": {"airbnb ireland unlimited": "lei:abnb-ireland"},
            "entity_key_to_ancestor_tickers": {"lei:abnb-ireland": ["ABNB"]},
        }

        with patch.object(llu, "call_structured_llm_by_provider", side_effect=_fake_structured_call), \
             patch.object(gns, "_log"):
            resolved = gns._llm_resolve_patent_assignees(
                ["Airbnb Ireland Unlimited Company"],
                {"ABNB": "Airbnb, Inc."},
                provider="gemini",
                model="gemini-3-flash-preview",
                api_key="AIza-valid",
                legal_index=legal_index,
            )

        self.assertEqual({}, resolved)
        self.assertEqual(1, len(prompts))
        self.assertIn('"ABNB": "Airbnb, Inc."', prompts[0])

    def test_fetch_patentsview_coassignees_uses_cache_without_api_key_and_emits_edges(self):
        patents = [
            {
                "patent_id": "US1",
                "patent_date": "2024-01-10",
                "assignees": [
                    {"assignee_organization": "Microsoft Corporation"},
                    {"assignee_organization": "OpenAI OpCo, LLC"},
                ],
            },
        ]
        expected_edges = [{
            "node_a": "MSFT",
            "rel": "PATENT_PARTNER",
            "node_b": "lei:openai-opco",
            "confidence": 0.75,
            "source": "PatentsView",
            "properties": {
                "patent_ids": ["US1"],
                "patent_count": 1,
                "active_after": "2024-01-10",
                "last_confirmed": "2024-01-10",
                "node_a_kind": "Company",
                "node_b_kind": "LegalEntity",
            },
        }]
        emitted_batches = []

        with patch.object(gns, "PATENTSVIEW_API_KEY", ""), \
             patch.object(gns, "_nexus_read_cached_json", return_value=patents), \
             patch.object(gns, "_nexus_stage_cache_hit"), \
             patch.object(gns, "_resolve_patentsview_to_entity_edges", return_value=expected_edges) as resolve_mock, \
             patch.object(gns, "_log"), \
             patch.object(gns, "_log_stage_unexpected") as unexpected_mock:
            result = gns._fetch_patentsview_coassignees(
                {"MSFT": "Microsoft Corporation"},
                legal_entity_records=[{"entity_key": "lei:openai-opco"}],
                edge_cb=lambda batch: emitted_batches.append(list(batch)),
                edge_cb_batch_size=10,
            )

        self.assertEqual(expected_edges, result)
        self.assertEqual([expected_edges], emitted_batches)
        resolve_mock.assert_called_once()
        unexpected_mock.assert_not_called()

    def test_phase7_repair_and_flag_implied_price_outliers_returns_counts(self):
        class _FakeResult:
            def __init__(self, single_value=None):
                self._single_value = single_value or {}

            def single(self):
                return self._single_value

            def consume(self):
                return None

        class _FakeSession:
            def __init__(self):
                self.calls = []

            def run(self, query, **params):
                self.calls.append(query)
                if "RETURN count(r) AS fixed" in query:
                    return _FakeResult({"fixed": 4})
                if "RETURN count(r) AS flagged" in query:
                    return _FakeResult({"flagged": 2})
                return _FakeResult({})

        result = gns._phase7_repair_and_flag_implied_price_outliers(_FakeSession(), "phase7:test")
        self.assertEqual({"target_fixed": 4, "remaining": 2, "flagged": 2}, result)

    def test_phase7_holds_value_looks_1000x_inflated_real_world_brka_cases(self):
        cases = [
            ("Clearbridge Investments, LLC 2024Q4", "BRK.A", 494.0, 336374480000.0),
            ("Clearbridge Investments, LLC 2025Q1", "BRK.A", 461.0, 368081578000.0),
            ("Clearbridge Investments, LLC 2025Q2", "BRK.A", 450.0, 327960000000.0),
            ("Clearbridge Investments, LLC 2025Q3", "BRK.A", 316.0, 238327200000.0),
            ("Clearbridge Investments, LLC 2025Q4", "BRK.A", 303.0, 228704400000.0),
            ("Tweedy, Browne Co LLC 2024Q4", "BRK.A", 285.0, 194062200000.0),
            ("Tweedy, Browne Co LLC 2025Q1", "BRK.A", 167.0, 133339747000.0),
            ("Tweedy, Browne Co LLC 2025Q2", "BRK.A", 153.0, 111506400000.0),
            ("Tweedy, Browne Co LLC 2025Q3", "BRK.A", 144.0, 108604800000.0),
            ("Tweedy, Browne Co LLC 2025Q4", "BRK.A", 144.0, 108691200000.0),
            ("Ninety One SA (Pty) Ltd 2024Q4", "BRK.A", 91.0, 61963721000.0),
            ("Ninety One SA (Pty) Ltd 2025Q1", "BRK.A", 91.0, 72072001000.0),
            ("Ninety One SA (Pty) Ltd 2025Q2", "BRK.A", 91.0, 66493700000.0),
            ("Ninety One SA (Pty) Ltd 2025Q3", "BRK.A", 91.0, 68213638000.0),
            ("Ninety One SA (Pty) Ltd 2025Q4", "BRK.A", 91.0, 68686801000.0),
            ("B. Metzler seel. Sohn & Co. AG 2024Q4", "BRK.A", 63.0, 42897960000.0),
            ("BISLETT MANAGEMENT, LLC 2025Q4", "BRK.A", 35.0, 26418000000.0),
            ("BNP PARIBAS FINANCIAL MARKETS 2025Q2", "BRK.A", 33.0, 24050400000.0),
        ]

        for label, ticker, shares, value_usd in cases:
            with self.subTest(label=label):
                self.assertTrue(
                    gns._phase7_holds_value_looks_1000x_inflated(ticker, shares, value_usd),
                    msg=f"expected 1000x inflation detection for {label}",
                )

    def test_phase7_holds_value_looks_1000x_inflated_keeps_real_world_non_outliers(self):
        cases = [
            ("Clearbridge Investments, LLC corrected 2025Q4", "BRK.A", 303.0, 228704400.0),
            ("Clearbridge Investments, LLC corrected 2024Q4", "BRK.A", 494.0, 336374480.0),
            ("Tweedy, Browne Co LLC corrected 2025Q4", "BRK.A", 144.0, 108691200.0),
            ("Ninety One SA (Pty) Ltd corrected 2025Q4", "BRK.A", 91.0, 68686801.0),
            ("BISLETT MANAGEMENT, LLC corrected 2025Q4", "BRK.A", 35.0, 26418000.0),
            ("BNP PARIBAS FINANCIAL MARKETS corrected 2025Q2", "BRK.A", 33.0, 24050400.0),
            ("Uber Technologies, Inc / GRAB", "GRAB", 535902982.0, 2674155880000.0),
            ("SB INVESTMENT ADVISERS (UK) LTD / GRAB", "GRAB", 401796672.0, 2004965393000.0),
            ("Uber Technologies, Inc / AUR", "AUR", 325973411.0, 1251737898000.0),
            ("AMERICAN EXPRESS CO / GBTG", "GBTG", 157786199.0, 1207064422000.0),
            ("STATE STREET CORP / AMCR", "AMCR", 141509581.0, 1180189906000.0),
            ("TOYOTA MOTOR CORP/ / GRAB", "GRAB", 222906079.0, 1112301334000.0),
            ("WHITEBOX ADVISORS LLC / WDC", "WDC", 204400000.0, 936928720000.0),
            ("M&G PLC / AMCR", "AMCR", 114989595.0, 919916760000.0),
            ("Capital World Investors / SNAP", "SNAP", 88452006.0, 713807690000.0),
            ("MUFG BANK, LTD. / GRAB", "GRAB", 142913428.0, 713138006000.0),
            ("CHARLES SCHWAB INVESTMENT MANAGEMENT INC / AMCR", "AMCR", 84095399.0, 701355628000.0),
            ("AE INDUSTRIAL PARTNERS, LP / RDW", "RDW", 91598704.0, 696150150000.0),
        ]

        for label, ticker, shares, value_usd in cases:
            with self.subTest(label=label):
                self.assertFalse(
                    gns._phase7_holds_value_looks_1000x_inflated(ticker, shares, value_usd),
                    msg=f"unexpected 1000x inflation detection for {label}",
                )

    def test_phase7_parse_reported_market_value_uses_real_world_sec_cutover_cases(self):
        cases = [
            ("Clearbridge 2025Q4 Berkshire", "2026-02-28", "368081578", 368081578.0),
            ("Clearbridge 2025Q4 Cisco", "2026-02-28", "16356795", 16356795.0),
            ("Clearbridge 2025Q4 Apple", "2026-02-28", "5208060", 5208060.0),
            ("Clearbridge 2025Q4 Microsoft", "2026-02-28", "5597060", 5597060.0),
            ("Clearbridge 2025Q4 Amazon", "2026-02-28", "451297", 451297.0),
            ("Clearbridge 2022Q4 Berkshire", "2022-11-30", "230963", 230963000.0),
            ("Clearbridge 2022Q4 Cisco", "2022-11-30", "468304", 468304000.0),
            ("Clearbridge 2022Q4 Apple", "2022-11-30", "3038989", 3038989000.0),
            ("Clearbridge 2022Q4 Microsoft", "2022-11-30", "3964461", 3964461000.0),
            ("Clearbridge 2022Q4 Amazon", "2022-11-30", "3105167", 3105167000.0),
        ]

        for label, active_after, raw_value, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(expected, gns._phase7_parse_reported_market_value(active_after, raw_value))

    def test_phase7_normalize_resolved_holding_value_usd_corrects_real_world_live_cases(self):
        cases = [
            ("Clearbridge Investments, LLC 2025Q4", "BRK.A", 303.0, 228704400000.0, 228704400.0),
            ("Tweedy, Browne Co LLC 2025Q4", "BRK.A", 144.0, 108691200000.0, 108691200.0),
            ("Ninety One SA (Pty) Ltd 2025Q4", "BRK.A", 91.0, 68686801000.0, 68686801.0),
            ("BISLETT MANAGEMENT, LLC 2025Q4", "BRK.A", 35.0, 26418000000.0, 26418000.0),
            ("BNP PARIBAS FINANCIAL MARKETS 2025Q2", "BRK.A", 33.0, 24050400000.0, 24050400.0),
            ("FIDUCIARY COUNSELLING INC 2025Q4", "BRK.A", 15.0, 11322000000.0, 11322000.0),
            ("SPINNAKER TRUST 2025Q4", "BRK.A", 13.0, 9812400000.0, 9812400.0),
            ("Zhang Financial LLC 2025Q4", "BRK.A", 8.0, 6038400000.0, 6038400.0),
            ("Private Wealth Asset Management, LLC 2025Q4", "BRK.A", 20.0, 15096000000.0, 15096000.0),
            ("Argent Trust Co 2025Q4", "BRK.A", 14.0, 10567200000.0, 10567200.0),
        ]

        for label, ticker, shares, raw_value, expected in cases:
            with self.subTest(label=label):
                normalized, corrected = gns._phase7_normalize_resolved_holding_value_usd(ticker, shares, raw_value)
                self.assertTrue(corrected, msg=f"expected correction for {label}")
                self.assertEqual(expected, normalized)

    def test_phase7_normalize_resolved_holding_value_usd_keeps_real_world_valid_cases(self):
        cases = [
            ("Clearbridge 2025Q4 Berkshire raw SEC dollars", "BRK.A", 461.0, 368081578.0),
            ("Clearbridge 2025Q4 Cisco raw SEC dollars", "CSCO", 265059.0, 16356795.0),
            ("Clearbridge 2025Q4 Apple raw SEC dollars", "AAPL", 23446.0, 5208060.0),
            ("Clearbridge 2025Q4 Microsoft raw SEC dollars", "MSFT", 14910.0, 5597060.0),
            ("Clearbridge 2025Q4 Amazon raw SEC dollars", "AMZN", 2372.0, 451297.0),
            ("Clearbridge 2022Q4 Berkshire scaled dollars", "BRK.A", 864965.0, 230963000.0),
            ("Clearbridge 2022Q4 Cisco scaled dollars", "CSCO", 11707594.0, 468304000.0),
            ("Clearbridge 2022Q4 Apple scaled dollars", "AAPL", 21989791.0, 3038989000.0),
            ("Clearbridge 2022Q4 Microsoft scaled dollars", "MSFT", 17022159.0, 3964461000.0),
            ("Clearbridge 2022Q4 Amazon scaled dollars", "AMZN", 27479353.0, 3105167000.0),
        ]

        for label, ticker, shares, raw_value in cases:
            with self.subTest(label=label):
                normalized, corrected = gns._phase7_normalize_resolved_holding_value_usd(ticker, shares, raw_value)
                self.assertFalse(corrected, msg=f"unexpected correction for {label}")
                self.assertEqual(raw_value, normalized)

    def test_phase7_bulk_correct_1000x_inflation_query_no_longer_excludes_brka(self):
        captured = {}

        def _fake_batched_holds_update(session, **kwargs):
            captured.update(kwargs)
            return 18

        with patch.object(gns, "_phase7_batched_holds_update", side_effect=_fake_batched_holds_update):
            fixed = gns._phase7_bulk_correct_1000x_inflation(object(), "phase7:test", expected_total=18)

        self.assertEqual(18, fixed)
        where_sql = captured["where_sql"]
        self.assertIn("CASE", where_sql)
        self.assertIn("BRK.A", where_sql)
        self.assertIn("10000000", where_sql)
        self.assertNotIn("<> 'BRK.A'", where_sql)
        self.assertNotIn("<> 'BRK/A'", where_sql)
        self.assertIn("toFloat(r.value_usd) / 1000.0", where_sql)

    def test_phase7_repair_and_flag_implied_price_outliers_queries_no_longer_exclude_brka(self):
        with patch.object(gns, "_phase7_batched_holds_update", side_effect=[18, 0, 4]) as update_mock:
            result = gns._phase7_repair_and_flag_implied_price_outliers(object(), "phase7:test")

        self.assertEqual({"target_fixed": 18, "remaining": 4, "flagged": 4}, result)
        self.assertEqual(3, update_mock.call_count)
        for call in update_mock.call_args_list:
            where_sql = call.kwargs["where_sql"]
            self.assertIn("CASE", where_sql)
            self.assertIn("BRK.A", where_sql)
            self.assertIn("10000000", where_sql)
            self.assertNotIn("<> 'BRK.A'", where_sql)
            self.assertNotIn("<> 'BRK/A'", where_sql)

    def test_phase7_batched_holds_update_retries_with_smaller_batch_after_memory_error(self):
        class _FakeResult:
            def __init__(self, single_value=None):
                self._single_value = single_value or {}

            def single(self):
                return self._single_value

        class _FakeSession:
            def __init__(self):
                self.calls = []

            def run(self, query, **params):
                self.calls.append({"query": query, "params": dict(params)})
                batch_limit = params["batch_limit"]
                if len(self.calls) == 1:
                    raise Exception(
                        "{neo4j_code: Neo.TransientError.General.MemoryPoolOutOfMemoryError} "
                        "{message: dbms.memory.transaction.total.max threshold reached}"
                    )
                if len(self.calls) == 2:
                    return _FakeResult({"fixed": batch_limit})
                return _FakeResult({"fixed": 125})

        session = _FakeSession()
        with patch.object(gns, "_log"):
            changed = gns._phase7_batched_holds_update(
                session,
                where_sql="r.shares > 0",
                set_sql="SET r.last_quality_checked = toString(date())",
                result_alias="fixed",
                log_label="test inflation correction",
                batch_limit=2000,
                min_batch_limit=250,
            )
        self.assertEqual(1125, changed)
        self.assertEqual([2000, 1000, 1000], [call["params"]["batch_limit"] for call in session.calls])

    def test_phase7_is_supported_equity_holding_filters_real_sec_option_and_equity_rows(self):
        cases = [
            ("CTC Apple common", "COM", "", True),
            ("CTC Apple call", "COM", "Call", False),
            ("CTC Apple put", "COM", "Put", False),
            ("CTC AMD common", "COM", "", True),
            ("CTC AMD call", "COM", "Call", False),
            ("CTC AMD put", "COM", "Put", False),
            ("CTC Intel common", "COM", "", True),
            ("CTC Intel call", "COM", "Call", False),
            ("CTC Lululemon common", "COM", "", True),
            ("CTC Visa option", "COM", "Call", False),
            ("National Booking option", "CALL", "Call", False),
            ("National Apple option", "PUT", "Put", False),
            ("National Visa common", "COM CL A", "", True),
            ("National BP ADR", "SPONSORED ADR", "", True),
            ("National Grupo Televisa ADR", "SPON ADR REP ORD", "", True),
            ("National AT&T common", "COM", "", True),
        ]

        for label, title_of_class, put_call, expected in cases:
            with self.subTest(label=label):
                self.assertEqual(expected, gns._phase7_is_supported_equity_holding(title_of_class, put_call))

    def test_phase7_fetch_13f_holdings_from_quarterly_zip_dataset_skips_real_sec_derivatives_and_aggregates_common_rows(self):
        import csv
        import io
        import zipfile

        cover_rows = [
            {"ACCESSION_NUMBER": "0000001", "FILINGMANAGER_NAME": "CTC LLC", "FILINGMANAGER_CIK": "", "FILED_AS_OF_DATE": "2026-02-14"},
            {"ACCESSION_NUMBER": "0000002", "FILINGMANAGER_NAME": "NATIONAL BANK OF CANADA /FI/", "FILINGMANAGER_CIK": "", "FILED_AS_OF_DATE": "2026-02-14"},
        ]
        info_rows = [
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "APPLE INC", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "42306675", "SSHPRNAMT": "247104", "CUSIP": "037833100"},
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "APPLE INC", "TITLEOFCLASS": "COM", "PUTCALL": "Call", "VALUE": "2757371292", "SSHPRNAMT": "161052", "CUSIP": "037833900"},
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "APPLE INC", "TITLEOFCLASS": "COM", "PUTCALL": "Put", "VALUE": "2380606566", "SSHPRNAMT": "139046", "CUSIP": "037833950"},
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "ADVANCED MICRO DEVICES INC", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "49515335", "SSHPRNAMT": "481573", "CUSIP": "007903107"},
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "ADVANCED MICRO DEVICES INC", "TITLEOFCLASS": "COM", "PUTCALL": "Put", "VALUE": "378315908", "SSHPRNAMT": "36794", "CUSIP": "007903907"},
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "ADVANCED MICRO DEVICES INC", "TITLEOFCLASS": "COM", "PUTCALL": "Call", "VALUE": "302804900", "SSHPRNAMT": "29450", "CUSIP": "007903957"},
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "INTEL CORP", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "778260", "SSHPRNAMT": "21892", "CUSIP": "458140100"},
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "INTEL CORP", "TITLEOFCLASS": "COM", "PUTCALL": "Call", "VALUE": "30441465", "SSHPRNAMT": "8563", "CUSIP": "458140900"},
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "LULULEMON ATHLETICA INC", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "788958", "SSHPRNAMT": "2046", "CUSIP": "550021109"},
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "LULULEMON ATHLETICA INC", "TITLEOFCLASS": "COM", "PUTCALL": "Put", "VALUE": "73728632", "SSHPRNAMT": "1912", "CUSIP": "550021959"},
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "VISA INC", "TITLEOFCLASS": "COM", "PUTCALL": "Call", "VALUE": "52396278", "SSHPRNAMT": "2278", "CUSIP": "92826C909"},
            {"ACCESSION_NUMBER": "0000001", "NAMEOFISSUER": "VISA INC", "TITLEOFCLASS": "COM", "PUTCALL": "Put", "VALUE": "86506761", "SSHPRNAMT": "3761", "CUSIP": "92826C959"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "APPLE INC", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "3353375", "SSHPRNAMT": "19300", "CUSIP": "037833100"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "APPLE INC", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "2436659", "SSHPRNAMT": "14001", "CUSIP": "037833100"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "APPLE INC", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "457978", "SSHPRNAMT": "2606", "CUSIP": "037833100"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "APPLE INC", "TITLEOFCLASS": "PUT", "PUTCALL": "Put", "VALUE": "104336875", "SSHPRNAMT": "6005", "CUSIP": "037833950"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "APPLE INC", "TITLEOFCLASS": "CALL", "PUTCALL": "Call", "VALUE": "100983500", "SSHPRNAMT": "5812", "CUSIP": "037833900"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "ADVANCED MICRO DEVICES INC", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "15129055", "SSHPRNAMT": "146500", "CUSIP": "007903107"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "ADVANCED MICRO DEVICES INC", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "992947", "SSHPRNAMT": "9737", "CUSIP": "007903107"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "ADVANCED MICRO DEVICES INC", "TITLEOFCLASS": "PUT", "PUTCALL": "Put", "VALUE": "93986027", "SSHPRNAMT": "9101", "CUSIP": "007903957"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "BOOKING HOLDINGS INC", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "1058363", "SSHPRNAMT": "348", "CUSIP": "09857L108"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "BOOKING HOLDINGS INC", "TITLEOFCLASS": "CALL", "PUTCALL": "Call", "VALUE": "9281280", "SSHPRNAMT": "30", "CUSIP": "09857L908"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "INTEL CORP", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "1238898", "SSHPRNAMT": "34810", "CUSIP": "458140100"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "ELI LILLY & CO", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "4694275", "SSHPRNAMT": "8800", "CUSIP": "532457108"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "VISA INC", "TITLEOFCLASS": "COM CL A", "PUTCALL": "", "VALUE": "27041468", "SSHPRNAMT": "116851", "CUSIP": "92826C839"},
            {"ACCESSION_NUMBER": "0000002", "NAMEOFISSUER": "ADVANCED MICRO DEVICES INC", "TITLEOFCLASS": "COM", "PUTCALL": "", "VALUE": "1066", "SSHPRNAMT": "10", "CUSIP": "007903107"},
        ]

        def _tsv_bytes(rows):
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()), delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            return buffer.getvalue().encode("utf-8")

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("2026/COVERPAGE.tsv", _tsv_bytes(cover_rows))
            zf.writestr("2026/INFOTABLE.tsv", _tsv_bytes(info_rows))
        zip_bytes = zip_buffer.getvalue()

        manifest = {
            "zip_url": "https://www.sec.gov/files/structureddata/data/form-13f-data-sets/01dec2025-28feb2026_form13f.zip",
            "zip_filename": "01dec2025-28feb2026_form13f.zip",
            "period_key": "01dec2025-28feb2026_form13f",
            "active_after": "2026-02-28",
        }

        with patch.object(gns, "_nexus_cache_path", return_value="phase7_13f/test.zip"), \
             patch.object(gns, "_nexus_read_cached_file", return_value=zip_bytes), \
             patch.object(gns, "_nexus_stage_cache_hit"), \
             patch.object(gns, "_log"):
            snapshot = gns._fetch_13f_holdings_from_quarterly_zip_dataset({}, {}, manifest)

        self.assertIsNotNone(snapshot)
        edges = snapshot["edges"]
        edge_map = {
            (edge["properties"]["institution_name"], edge["properties"]["name_of_issuer"]): edge["properties"]
            for edge in edges
        }

        self.assertEqual(10, len(edges))
        self.assertEqual(42306675.0, edge_map[("CTC LLC", "APPLE INC")]["value_usd"])
        self.assertEqual(247104, edge_map[("CTC LLC", "APPLE INC")]["shares"])
        self.assertEqual(49515335.0, edge_map[("CTC LLC", "ADVANCED MICRO DEVICES INC")]["value_usd"])
        self.assertEqual(481573, edge_map[("CTC LLC", "ADVANCED MICRO DEVICES INC")]["shares"])
        self.assertEqual(778260.0, edge_map[("CTC LLC", "INTEL CORP")]["value_usd"])
        self.assertEqual(21892, edge_map[("CTC LLC", "INTEL CORP")]["shares"])
        self.assertEqual(788958.0, edge_map[("CTC LLC", "LULULEMON ATHLETICA INC")]["value_usd"])
        self.assertNotIn(("CTC LLC", "VISA INC"), edge_map)
        self.assertEqual(6248012.0, edge_map[("NATIONAL BANK OF CANADA /FI/", "APPLE INC")]["value_usd"])
        self.assertEqual(35907, edge_map[("NATIONAL BANK OF CANADA /FI/", "APPLE INC")]["shares"])
        self.assertEqual(16122002.0, edge_map[("NATIONAL BANK OF CANADA /FI/", "ADVANCED MICRO DEVICES INC")]["value_usd"])
        self.assertEqual(156237, edge_map[("NATIONAL BANK OF CANADA /FI/", "ADVANCED MICRO DEVICES INC")]["shares"])
        self.assertEqual(1058363.0, edge_map[("NATIONAL BANK OF CANADA /FI/", "BOOKING HOLDINGS INC")]["value_usd"])
        self.assertEqual(1238898.0, edge_map[("NATIONAL BANK OF CANADA /FI/", "INTEL CORP")]["value_usd"])
        self.assertEqual(4694275.0, edge_map[("NATIONAL BANK OF CANADA /FI/", "ELI LILLY & CO")]["value_usd"])
        self.assertEqual(27041468.0, edge_map[("NATIONAL BANK OF CANADA /FI/", "VISA INC")]["value_usd"])

    def test_fetch_13f_holdings_legacy_wrapper_delegates_to_hardened_atom_snapshot_edges(self):
        expected_edges = [
            {
                "node_a": "cik_0000000001",
                "rel": "HOLDS",
                "node_b": "APPLE INC",
                "confidence": 0.90,
                "source": "13F-HR",
                "properties": {"institution_name": "Demo Manager", "value_usd": 123456.0, "shares": 789},
            }
        ]

        with patch.object(gns, "_fetch_13f_holdings_from_quarterly_zip", return_value=[]), \
             patch.object(gns, "_fetch_13f_holdings_from_atom_feed", return_value={"edges": expected_edges}):
            edges = gns._fetch_13f_holdings({})

        self.assertEqual(expected_edges, edges)

    def test_fetch_latest_10k_filing_date_uses_phase2_cached_submissions_without_live_fetch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "phase2_sec_submissions")
            os.makedirs(cache_dir, exist_ok=True)
            payload = {
                "filings": {
                    "recent": {
                        "form": ["8-K", "10-K", "10-Q", "10-K/A"],
                        "filingDate": ["2026-01-10", "2026-02-14", "2026-02-20", "2026-02-15"],
                    }
                }
            }
            cache_path = os.path.join(cache_dir, "CIK0000000001.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                import json
                json.dump(payload, f)
            with patch.object(sec, "SEC_EDGAR_CACHE_DIR", tmpdir), patch("requests.get") as mock_get:
                filing_date = sec._fetch_latest_10k_filing_date("1", allow_live_lookup=False)
            mock_get.assert_not_called()
            self.assertEqual("2026-02-15", filing_date)

    def test_load_cached_submission_payload_ignores_phase2_sic_summary_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "phase2_sec_submissions")
            os.makedirs(cache_dir, exist_ok=True)
            with open(os.path.join(cache_dir, "CIK0000000001.json"), "w", encoding="utf-8") as f:
                import json
                json.dump({
                    "sic": "3571",
                    "sic_desc": "Electronic Computers",
                    "owner_org_sector": "Technology",
                    "name": "APPLE INC",
                }, f)
            with patch.object(sec, "SEC_EDGAR_CACHE_DIR", tmpdir):
                payload = sec._load_cached_submission_payload("0000000001")
            self.assertIsNone(payload)

    def test_collect_submission_filing_records_refetches_when_phase2_summary_cache_poisoned(self):
        class _FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        live_payload = {
            "filings": {
                "recent": {
                    "form": ["8-K", "10-K", "10-Q"],
                    "accessionNumber": ["0000000001-26-000001", "0000000001-26-000002", "0000000001-26-000003"],
                    "primaryDocument": ["a8k.htm", "a10k.htm", "a10q.htm"],
                    "filingDate": ["2026-01-10", "2026-02-14", "2026-02-20"],
                    "items": ["1.01", "", ""],
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            poison_dir = os.path.join(tmpdir, "phase2_sec_submissions")
            os.makedirs(poison_dir, exist_ok=True)
            with open(os.path.join(poison_dir, "CIK0000000001.json"), "w", encoding="utf-8") as f:
                import json
                json.dump({
                    "sic": "3571",
                    "sic_desc": "Electronic Computers",
                    "owner_org_sector": "Technology",
                    "name": "APPLE INC",
                }, f)
            with patch.object(sec, "SEC_EDGAR_CACHE_DIR", tmpdir), \
                 patch.object(sec, "_sec_rate_limited_get", return_value=_FakeResponse(live_payload)) as mock_get:
                rows = sec._collect_submission_filing_records(
                    "1",
                    form_types=("10-K",),
                    start_date="2026-01-01",
                    end_date="2026-03-14",
                    allow_live_lookup=True,
                )
            mock_get.assert_called_once()
            self.assertEqual(1, len(rows))
            self.assertEqual("2026-02-14", rows[0]["filing_date"])
            self.assertEqual("a10k.htm", rows[0]["primary_document"])

    def test_fetch_latest_10k_filing_date_ignores_phase2_summary_cache_and_uses_live_fetch(self):
        class _FakeResponse:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        live_payload = {
            "filings": {
                "recent": {
                    "form": ["8-K", "10-K", "10-K/A"],
                    "filingDate": ["2026-01-10", "2026-02-14", "2026-02-15"],
                    "accessionNumber": ["0000000001-26-000001", "0000000001-26-000002", "0000000001-26-000003"],
                    "primaryDocument": ["a8k.htm", "a10k.htm", "a10ka.htm"],
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            poison_dir = os.path.join(tmpdir, "phase2_sec_submissions")
            os.makedirs(poison_dir, exist_ok=True)
            with open(os.path.join(poison_dir, "CIK0000000001.json"), "w", encoding="utf-8") as f:
                import json
                json.dump({
                    "sic": "3571",
                    "sic_desc": "Electronic Computers",
                    "owner_org_sector": "Technology",
                    "name": "APPLE INC",
                }, f)
            with patch.object(sec, "SEC_EDGAR_CACHE_DIR", tmpdir), \
                 patch.object(sec, "_sec_rate_limited_get", return_value=_FakeResponse(live_payload)) as mock_get:
                filing_date = sec._fetch_latest_10k_filing_date("1", allow_live_lookup=True)
            mock_get.assert_called_once()
            self.assertEqual("2026-02-15", filing_date)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "submissions", "CIK0000000001.json")))

    def test_fetch_sec_submissions_sic_reads_legacy_phase2_sec_submissions_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy_dir = os.path.join(tmpdir, "phase2_sec_submissions")
            os.makedirs(legacy_dir, exist_ok=True)
            legacy_path = os.path.join(legacy_dir, "CIK0000000001.json")
            with open(legacy_path, "w", encoding="utf-8") as f:
                import json
                json.dump({
                    "sic": "3571",
                    "sic_desc": "Electronic Computers",
                    "owner_org_sector": "Technology",
                    "owner_org_raw": "04 Technology",
                    "name": "APPLE INC",
                    "exchange": "Nasdaq",
                }, f)

            def _fake_cache_path(subdir, filename):
                return os.path.join(tmpdir, subdir, filename)

            with patch.object(gns, "_nexus_cache_path", side_effect=_fake_cache_path), \
                 patch("requests.get") as mock_get:
                result = gns._fetch_sec_submissions_sic("1")

            mock_get.assert_not_called()
            self.assertEqual(("3571", "Electronic Computers", "Technology", "04 Technology", "APPLE INC", "Nasdaq"), result)

    def test_fetch_sec_submissions_sic_writes_new_phase2_sec_profiles_cache(self):
        class _FakeResponse:
            def __init__(self):
                self.status_code = 200

            def json(self):
                return {
                    "sic": "3571",
                    "sicDescription": "Electronic Computers",
                    "ownerOrg": "04 Technology",
                    "name": "APPLE INC",
                    "exchanges": ["Nasdaq"],
                }

        with tempfile.TemporaryDirectory() as tmpdir:
            def _fake_cache_path(subdir, filename):
                return os.path.join(tmpdir, subdir, filename)

            with patch.object(gns, "_nexus_cache_path", side_effect=_fake_cache_path), \
                 patch("requests.get", return_value=_FakeResponse()), \
                 patch.object(gns, "_log_stage_error"), \
                 patch.object(gns, "_nexus_stage_download"):
                result = gns._fetch_sec_submissions_sic("1")

            self.assertEqual(("3571", "Electronic Computers", "Technology", "04 Technology", "APPLE INC", "Nasdaq"), result)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "phase2_sec_profiles", "CIK0000000001.json")))
            self.assertFalse(os.path.isfile(os.path.join(tmpdir, "phase2_sec_submissions", "CIK0000000001.json")))

    def test_phase6_extract_ex21_entries_parses_table_form(self):
        html = """
        <html><body>
        <table>
          <tr><th>Name of Subsidiary</th><th>Jurisdiction of Incorporation</th></tr>
          <tr><td>Ring LLC</td><td>Delaware</td></tr>
          <tr><td>Beats Electronics, LLC</td><td>California</td></tr>
        </table>
        </body></html>
        """
        entries = gns._phase6_extract_ex21_entries_from_html(html)
        names = {entry["legal_name"] for entry in entries}
        self.assertIn("Ring LLC", names)
        self.assertIn("Beats Electronics, LLC", names)
        ring_entry = next(entry for entry in entries if entry["legal_name"] == "Ring LLC")
        self.assertEqual("Delaware", ring_entry["jurisdiction"])

    def test_phase6_extract_ex21_entries_ignores_jurisdiction_cells_and_footnotes_when_table_present(self):
        html = """
        <html><body>
        <div>Subsidiaries of</div>
        <div>Apple Inc.*</div>
        <table>
          <tr><td></td><td>Jurisdiction<br>of Incorporation</td></tr>
          <tr><td>Apple Asia Limited</td><td>Hong Kong</td></tr>
          <tr><td>Apple Canada Inc.</td><td>Canada</td></tr>
          <tr><td>Braeburn Capital, Inc.</td><td>Nevada, U.S.</td></tr>
        </table>
        <div>* Pursuant to Item 601(b)(21)(ii) of Regulation S-K, the names of other subsidiaries are omitted.</div>
        </body></html>
        """
        entries = gns._phase6_extract_ex21_entries_from_html(html)
        names = {entry["legal_name"] for entry in entries}
        self.assertEqual(
            {"Apple Asia Limited", "Apple Canada Inc.", "Braeburn Capital, Inc."},
            names,
        )
        self.assertNotIn("of Incorporation", names)
        self.assertNotIn("Hong Kong", names)
        self.assertNotIn("Canada", names)
        self.assertNotIn("Nevada, U.S.", names)
        self.assertFalse(any("Pursuant to Item 601" in name for name in names))

    def test_phase6_ex21_matches_issuer_name_ignores_footnote_marker(self):
        issuer_record = {
            "ticker": "AAPL",
            "canonical_name": "Apple Inc",
            "name": "Apple Inc.",
            "listing_name": "Apple Inc.",
        }
        self.assertTrue(gns._phase6_ex21_matches_issuer_name(issuer_record, "Apple Inc.*"))
        self.assertFalse(gns._phase6_ex21_matches_issuer_name(issuer_record, "Apple Canada Inc."))

    def test_phase6_ex21_matches_issuer_name_handles_realistic_public_issuer_row(self):
        issuer_record = {
            "ticker": "BBGI",
            "canonical_name": "Beasley Broadcast Group, Inc.",
            "name": "Beasley Broadcast Group, Inc.",
            "listing_name": "Beasley Broadcast Group, Inc.",
        }
        self.assertTrue(gns._phase6_ex21_matches_issuer_name(issuer_record, "Beasley Broadcast Group, Inc."))
        self.assertFalse(gns._phase6_ex21_matches_issuer_name(issuer_record, "Beasley Media Group, LLC"))

    def test_phase6_is_probable_ex21_entity_name_rejects_jurisdiction_only_value(self):
        self.assertFalse(gns._phase6_is_probable_ex21_entity_name("Delaware, U.S."))
        self.assertFalse(gns._phase6_is_probable_ex21_entity_name("Colorado, U.S."))
        self.assertFalse(gns._phase6_is_probable_ex21_entity_name("Michigan, U.S."))
        self.assertFalse(gns._phase6_is_probable_ex21_entity_name("Pennsylvania"))
        self.assertFalse(gns._phase6_is_probable_ex21_entity_name("Argentina"))
        self.assertFalse(gns._phase6_is_probable_ex21_entity_name("Mauritius"))
        self.assertFalse(gns._phase6_is_probable_ex21_entity_name("(State of incorporation)"))
        self.assertFalse(
            gns._phase6_is_probable_ex21_entity_name(
                "(The state of incorporation or organization of each subsidiary is Virginia, except as noted below)"
            )
        )
        self.assertTrue(gns._phase6_is_probable_ex21_entity_name("Canada Imperial Oil Limited"))

    def test_phase6_is_probable_ex21_entity_name_rejects_exhibit_labels_and_headers(self):
        for text in (
            "Name",
            "Entity Name",
            "Company Name",
            "Subsidiary",
            "Direct Subsidiaries",
            "Indirect Subsidiaries",
            "A. Direct Subsidiaries",
            "B. Indirect Subsidiaries",
            "EX-21.1",
            "Exhibit 21.1",
            "ex21-1.htm",
            "a10k_2025-exhibit211.htm",
            "ACM-2025.09.30-EX-21.1",
            "ex_656980.htm",
            "(1)",
            "107",
            "Spain",
            "Abbott",
            "International",
            "INTERNATIONAL",
            "Registrant",
            "REGISTRANT",
            "Incorporation",
            "Incorporation/Organization",
            "Organization",
            "Ownership",
            "Subsidiary*",
            "listofsubsidiaries",
            "ex211listofsubsidiaries",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)
        self.assertTrue(gns._phase6_is_probable_ex21_entity_name("TC1 LLC"))
        self.assertTrue(gns._phase6_is_probable_ex21_entity_name("Apple Canada Inc."))
        self.assertTrue(gns._phase6_is_probable_ex21_entity_name("PacifiCorp"))

    def test_phase6_is_probable_ex21_entity_name_rejects_descriptive_sentence_rows(self):
        for text in (
            "List of Subsidiaries",
            "LIST OF SUBSIDIARIES",
            "LIST OF SUBSIDIARIES OF THE COMPANY",
            "List of Subsidiaries of",
            "Exhibit 21.1 to Form 10-K for the year ended December 31, 2025",
            "The following is a list of the subsidiaries of Amalgamated Financial Corp.",
            "Subsidiaries included in the Registrant's consolidated financial statements",
            "Domestic Subsidiaries",
            "Foreign Subsidiaries",
            "International Subsidiaries",
            "Asian Subsidiaries",
            "U.S. Subsidiaries",
            "Schedule of Subsidiaries",
            "UZBEKISTAN",
            "Subsidiaries of Registrant",
            "(8) Varian Semiconductor Equipment Associates, Inc. owns the following subsidiaries",
            "(3) Applied Materials Asia-Pacific, LLC and Applied Materials Netherlands B.V. each partially own the following subsidiary",
            "Wholly owned subsidiary of American Superconductor Corporation",
            "Subsidiary of third-party Majid Al Futtaim Lifestyle LLC (51.33%) and AFH Logistics DWC-LLC",
            "* Ownership of such subsidiary is less than 100% by AbbVie or an AbbVie subsidiary",
            "PrecisionIR Group Inc., and its subsidiaries",
            "CATERPILLAR INC. List of Subsidiaries and Affiliated Companies",
            "A list of subsidiaries is contained in Part I, Item 1 Business under the section titled “Subsidiaries” and is incorporated herein by reference.",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)
        self.assertTrue(gns._phase6_is_probable_ex21_entity_name("PMA Investment Subsidiary, Inc."))

    def test_phase6_extract_ex21_entries_rejects_section_heading_rows(self):
        html = """
        <html><body>
        <table>
          <tr><td>A. Direct Subsidiaries</td></tr>
          <tr><td>Apple Air Holding, LLC</td><td>Virginia</td></tr>
          <tr><td>B. Indirect Subsidiaries</td></tr>
          <tr><td>Apple Hospitality Richmond, LLC</td><td>Virginia</td></tr>
        </table>
        </body></html>
        """
        entries = gns._phase6_extract_ex21_entries_from_html(html)
        names = [entry["legal_name"] for entry in entries]
        self.assertEqual(
            ["Apple Air Holding, LLC", "Apple Hospitality Richmond, LLC"],
            names,
        )

    def test_phase6_extract_ex21_entries_extracts_entity_prefix_from_sentence_rows(self):
        html = """
        <html><body>
        <ul>
          <li>Access Digital Media, Inc., a Delaware corporation and a wholly-owned subsidiary of Cinedigm DC Holdings, LLC.</li>
          <li>Agenus UK Limited, a private limited company organized under the laws of England and Wales and a wholly-owned subsidiary of Agenus Inc.</li>
          <li>Blue Ridge Websoft, LLC, a Virginia limited liability company, is a wholly owned subsidiary of Ting Fiber, LLC.</li>
          <li>(1) Applied Materials (Holdings) owns the following subsidiary</li>
        </ul>
        </body></html>
        """
        entries = gns._phase6_extract_ex21_entries_from_html(html)
        names = {entry["legal_name"] for entry in entries}
        self.assertIn("Access Digital Media, Inc.", names)
        self.assertIn("Agenus UK Limited", names)
        self.assertIn("Blue Ridge Websoft, LLC", names)
        self.assertIn("Applied Materials (Holdings)", names)
        self.assertFalse(any("wholly-owned subsidiary" in name.lower() for name in names))
        self.assertFalse(any("organized under the laws" in name.lower() for name in names))

    def test_phase6_extract_ex21_entries_handles_more_live_sentence_row_patterns(self):
        html = """
        <html><body>
        <ul>
          <li>Administaff Companies, Inc., a Delaware corporation and wholly owned subsidiary of Insperity Holdings, Inc.</li>
          <li>ChoiceOne Insurance Agencies, Inc. is a wholly-owned subsidiary of ChoiceOne Bank.</li>
          <li>BAMKO Merch Inc., a wholly owned subsidiary of BAMKO, LLC</li>
          <li>Access Digital Cinema Phase 2, Corp., a Delaware corporation and a wholly-owned subsidiary of the Company.</li>
        </ul>
        </body></html>
        """
        entries = gns._phase6_extract_ex21_entries_from_html(html)
        names = {entry["legal_name"] for entry in entries}
        self.assertIn("Administaff Companies, Inc.", names)
        self.assertIn("ChoiceOne Insurance Agencies, Inc.", names)
        self.assertIn("BAMKO Merch Inc.", names)
        self.assertIn("Access Digital Cinema Phase 2, Corp.", names)
        self.assertFalse(any("wholly owned subsidiary" in name.lower() for name in names))

    def test_phase6_extract_ex21_entries_handles_large_real_world_sentence_batch(self):
        html = """
        <html><body><ul>
          <li>Access Digital Media, Inc., a Delaware corporation and a wholly-owned subsidiary of Cinedigm DC Holdings, LLC.</li>
          <li>Agenus UK Limited, a private limited company organized under the laws of England and Wales and a wholly-owned subsidiary of Agenus Inc.</li>
          <li>Blue Ridge Websoft, LLC, a Virginia limited liability company, is a wholly owned subsidiary of Ting Fiber, LLC.</li>
          <li>Administaff Partnerships Holding, Inc., a Delaware corporation and wholly owned subsidiary of Insperity Holdings, Inc.</li>
          <li>Administaff Partnerships Holding II, Inc., a Delaware corporation and wholly owned subsidiary of Insperity Services, L.P.</li>
          <li>Administaff Partnerships Holding III, Inc., a Delaware corporation and wholly owned subsidiary of Administaff Companies, Inc.</li>
          <li>Agenus Holdings 2024, LLC, a Delaware limited liability company and a wholly-owned subsidiary of Agenus Inc.</li>
          <li>Agenus Royalty Fund, LLC, a Delaware limited liability company and a wholly-owned subsidiary of Agenus Inc.</li>
          <li>Agenus West, LLC, a Delaware limited liability company and a wholly-owned subsidiary of Agenus Inc.</li>
          <li>Antigenics LLC., a Delaware limited liability company and a wholly-owned subsidiary of Agenus Inc.</li>
          <li>Ascio Technologies, Corp., a Nova Scotia corporation, is a wholly owned subsidiary of Tucows.com Co.</li>
          <li>Asian Media Rights, LLC, a New York limited liability company and a wholly-owned subsidiary of the Company.</li>
          <li>Bailiwick Services, LLC, a Minnesota limited liability company, a wholly-owned subsidiary of</li>
          <li>Bloody Disgusting Acquisition LLC, a Delaware limited liability company and a wholly-owned subsidiary of Cineverse OTT Holdings, LLC.</li>
          <li>CenterPoint Energy Houston Electric, LLC, a Texas limited liability company and an indirect wholly-owned subsidiary of CenterPoint Energy, Inc.</li>
          <li>Christie/AIX, Inc., a Delaware corporation and a wholly-owned subsidiary of Access Digital Media, Inc.</li>
        </ul></body></html>
        """
        entries = gns._phase6_extract_ex21_entries_from_html(html)
        names = {entry["legal_name"] for entry in entries}
        expected = {
            "Access Digital Media, Inc.",
            "Agenus UK Limited",
            "Blue Ridge Websoft, LLC",
            "Administaff Partnerships Holding, Inc.",
            "Administaff Partnerships Holding II, Inc.",
            "Administaff Partnerships Holding III, Inc.",
            "Agenus Holdings 2024, LLC",
            "Agenus Royalty Fund, LLC",
            "Agenus West, LLC",
            "Antigenics LLC.",
            "Ascio Technologies, Corp.",
            "Asian Media Rights, LLC",
            "Bailiwick Services, LLC",
            "Bloody Disgusting Acquisition LLC",
            "CenterPoint Energy Houston Electric, LLC",
            "Christie/AIX, Inc.",
        }
        self.assertTrue(expected.issubset(names))
        self.assertFalse(any("wholly-owned subsidiary" in name.lower() for name in names))
        self.assertFalse(any("organized under the laws" in name.lower() for name in names))

    def test_phase6_extract_ex21_jurisdiction_only_splits_parenthetical_real_jurisdictions(self):
        self.assertEqual(
            ("Apple Asia Limited", "Hong Kong"),
            gns._phase6_extract_ex21_jurisdiction("Apple Asia Limited (Hong Kong)"),
        )
        self.assertEqual(
            ("Applied Materials (Holdings)", ""),
            gns._phase6_extract_ex21_jurisdiction("Applied Materials (Holdings)"),
        )

    def test_phase6_build_ex21_entity_key_is_bounded(self):
        legal_name = "A" * 40000
        entity_key = gns._phase6_build_ex21_entity_key("AHR", legal_name, "000156918726000021")
        entity_key_again = gns._phase6_build_ex21_entity_key("AHR", legal_name, "000156918726000999")
        self.assertTrue(entity_key.startswith("ex21:AHR:"))
        self.assertLessEqual(len(entity_key), 220)
        self.assertEqual(entity_key, entity_key_again)

    def test_phase6_company_records_have_sec_control_support_requires_child_side_support(self):
        parent_record = {"cik": "0000000001", "name": "Parent Holdings, Inc."}
        child_record = {"cik": "0000000002", "name": "Subsidiary Co."}
        parent_text = "Subsidiary Co. is one of our wholly owned subsidiaries."
        child_text = "Subsidiary Co. operates as a standalone company."
        with patch.object(gns, "_latest_sec_annual_filing_text", side_effect=[child_text, parent_text]):
            self.assertFalse(gns._company_records_have_sec_control_support(parent_record, child_record))
        child_text_supported = "Subsidiary Co. is a wholly owned subsidiary of Parent Holdings, Inc."
        with patch.object(gns, "_latest_sec_annual_filing_text", side_effect=[child_text_supported]):
            self.assertTrue(gns._company_records_have_sec_control_support(parent_record, child_record))

    def test_phase6_pick_ex21_document_detects_compact_exhibit21_filenames(self):
        filing = {
            "cik": "0000320193",
            "accession_compact": "000032019325000079",
            "primary_document": "aapl-20250927.htm",
            "filing_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
        }
        payload = {
            "directory": {
                "item": [
                    {"name": "aapl-20250927.htm"},
                    {"name": "a10-kexhibit21109272025.htm"},
                ]
            }
        }
        self.assertEqual(
            "a10-kexhibit21109272025.htm",
            gns._phase6_pick_ex21_document(None, filing, payload),
        )

    def test_phase6_ex21_candidate_document_names_uses_submission_text_for_generic_filename(self):
        filing = {
            "cik": "0001158114",
            "accession_compact": "000143774926005875",
            "primary_document": "aaoi20251231_10k.htm",
            "filing_url": "https://www.sec.gov/Archives/edgar/data/1158114/000143774926005875/aaoi20251231_10k.htm",
        }
        index_payload = {
            "directory": {
                "item": [
                    {"name": "aaoi20251231_10k.htm"},
                    {"name": "0001437749-26-005875.txt"},
                    {"name": "ex_921421.htm"},
                ]
            }
        }
        submission_text = """
        <SEC-DOCUMENT>
        <DOCUMENT>
        <TYPE>EX-21.1
        <DESCRIPTION>Significant Subsidiaries
        <FILENAME>ex_921421.htm
        </DOCUMENT>
        </SEC-DOCUMENT>
        """
        with patch.object(gns, "_phase6_fetch_sec_submission_text", return_value=submission_text):
            names = gns._phase6_ex21_candidate_document_names(object(), filing, index_payload)
        self.assertEqual(["ex_921421.htm"], names)

    def test_phase6_is_probable_ex21_entity_name_rejects_live_bad_examples(self):
        for text in (
            "abbv-20251231xex21",
            "exhibit211.htm",
            "exh21131dec24.htm",
            "A list of subsidiaries is contained in Part I, Item 1 Business under the section titled Subsidiaries and is incorporated herein by reference.",
            "2025 Subsidiaries of Corpay, Inc.",
            "565 Corporation",
            "(Organized under the laws of Spain)",
            "Active Subsidiaries of Registrant",
            "BYND EX-21.1 SUBSIDIARIES OF BEYOND MEAT, INC.",
            "LIST OF SUBSIDIARIES",
            "List of Subsidiaries",
            "California",
            "As of December 31, 2025",
            "As of December 31",
            "New York",
            "Cayman Islands",
            "New Zealand",
            "International",
            "Domestic Subsidiaries",
            "Massachusetts",
            "December 31, 2025",
            "Title of each class",
            "The Netherlands",
            "Incorporation",
            "AS OF DECEMBER 31, 2025",
            "Foreign Subsidiaries",
            "Pennsylvania",
            "Organization",
            "exhibit211-listofsubsidiar.htm",
            "exhibit211listofsubsidiari.htm",
            "listofsubsidiaries.htm",
            "LIST OF SUBSIDIARIES OF THE COMPANY",
            "List of Subsidiaries of",
            "a12312025exhibit211.htm",
            "exhibit21-subsidiariesofth.htm",
            "exhibit21.htm",
            "exhibit211-listofsubsidi.htm",
            "exhibit211-subsidiariesoft.htm",
            "exhibit2112025.htm",
            "Listing of Subsidiaries as of December 31, 2024",
            "As of December 31, 2025, Axogen, Inc. had four sole subsidiaries",
            "EX-21 3 bke20250201-10kex21.htm EX-21 Document EXHIBIT 21 THE BUCKLE, INC. SUBSIDIARIES Buckle Brands, Inc., a Nebraska corporation",
            "EX-21.1 6 btai-20241231xex21d1.htm EX-21.1 Exhibit 21.1 Subsidiaries OnkosXcel Therapeutics, LLC (Delaware) OnkosXcel Employee Holdings, LLC",
            "EX-21.1 7 ex21-1.htm EX-21.1 Exhibit 21.1 TalenTec Sdn. Bhd, a Malaysia private limited company KEDA Pte Ltd., a Singapore limited company",
            "(1) Wholly-owned subsidiary of BayVanguard Bank",
            "(10) Organized under the laws of Mississippi",
            "CATERPILLAR INC. List of Subsidiaries and Affiliated Companies",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)
        self.assertTrue(gns._phase6_is_probable_ex21_entity_name("Access Digital Media, Inc."))

    def test_phase6_is_probable_ex21_entity_name_rejects_additional_live_noise_examples(self):
        for text in (
            "EX-21 12 exhibit21-10k12312025.htm EX-21 Document Exhibit 21 Subsidiaries of Registrant Tompkins Community Bank - a New York state",
            "EX-21 2 ex21.htm Exhibit 21 List of Subsidiaries Shuttle Pharmaceuticals, Inc., a Maryland corporation Shuttle Diagnostics, Inc., a Maryland corporation",
            "EX-21 3 ea027804601ex-21_mckinley.htm LIST OF SUBSIDIARIES Exhibit 21 List of Subsidiaries None",
            "EX-21 3 ex21.htm SUBSIDIARIES OF REGISTRANT EXHIBIT 21 Subsidiaries Duos Technologies, Inc. Duos Edge AI, Inc. Duos Energy Corporation",
            "EX-21 3 goro-20241231xex21.htm EX-21 Exhibit 21 Gold Resource Corporation and Subsidiaries",
            "EX-21 3 isba_20241231xkxex21.htm EX-21 Document Exhibit 21 Subsidiaries of Isabella Bank Corporation: Isabella Bank Wholly owned",
            "EX-21 3 sfst4554191-ex21.htm SUBSIDIARIES Exhibit 21 Subsidiaries Southern First Bank Greenville Statutory Trust I and II",
            "EX-21 4 d888799dex21.htm EX-21 EX-21 Exhibit 21 Subsidiaries of Oaktree Acquisition Corp. III Life Sciences None",
            "EX-21 4 ex_794259.htm EXHIBIT 21 ex_794259.htm Exhibit 21 Subsidiaries of NovaBay Pharmaceuticals, Inc. NovaBay Pharmaceuticals, Inc. has no subsidiaries.",
            "EX-21 4 flagshipacq_ex21.htm EXHIBIT 21 EXHIBIT 21 LIST OF SUBSIDIAIRIES OF FLAG SHIP ACQUISITION CORPORATION None",
            "EX-21 4 tdac-20241231xex21.htm EX-21 Exhibit 21 None.",
            "EX-21 6 exhibit21.htm EX-21 exhibit21 Exhibit 21 Subsidiaries of Northwest Bancshares, Inc.",
            "EX-21 8 ex_862186.htm EXHIBIT 21 ex_862186.htm Exhibit 21 RGC Resources, Inc. Subsidiaries of Registrant Roanoke Gas Company RGC Midstream, LLC",
            "EX-21 EXHIBIT 21",
            "EX-21.1 2 ex21-1.htm Exhibit 21.1 VirTra, Inc. Subsidiaries None.",
            "EX-21.1 2 ex_890813.htm EXHIBIT 21.1 ex_890813.htm Exhibit 21.1 Subsidiaries 1) LiqTech USA, Inc., a Delaware corporation",
            "EX-21.1 2 listofsubsidiaries.htm EX-21.1 listofsubsidiaries",
            "EX-21.1 2 rckt-ex21_1.htm EX-21.1 EX-21.1 Exhibit 21.1",
            "EX-21.1 3 a12312025nsp-ex211xsubsidi.htm EX-21.1 Document Exhibit 21.1 SUBSIDIARIES OF INSPERITY, INC.",
            "EX-21.1 3 bmea-ex21_1.htm EX-21.1 EX-21.1 Exhibit 21.1 Subsidiaries of Biomea Fusion, Inc. None.",
            "EX-21.1 3 ea023423401ex21-1_trail1.htm LIST OF SUBSIDIARIES Exhibit 21.1 List of Subsidiaries None",
            "EX-21.1 3 ex21-1.htm EXHIBIT 21.1 SUBSIDIARIES Lipocine Operating Inc.",
            "EX-21.1 3 ex21-1.htm Exhibit 21.1 SUBSIDIARIES OF BAYVIEW ACQUISITION CORP None.",
            "EX-21.1 3 ex211-subsidiariesofspyret.htm EX-21.1 Document Exhibit 21.1 Subsidiaries of Spyre Therapeutics, Inc. None.",
            "EX-21.1 3 exhibit211organizationchar.htm EX-21.1 Document EXHIBIT 21.1 SKYWARD SPECIALTY INSURANCE GROUP, INC. ORGANIZATION CHART",
            "EX-21.1 3 gecc-ex21_1.htm EX-21.1 EX-21.1 Exhibit 21.1 Subsidiaries CLO Formation JV, LLC Delaware Great Elm Specialty Finance, LLC Delaware",
            "EX-21.1 3 huma-20241231x10kxex211.htm EX-21.1 Document Exhibit 21.1 Subsidiaries of Humacyte, Inc. Humacyte Global, Inc. Humacyte Europe Limited",
            "EX-21.1 3 nktr-ex21_1.htm EX-21.1 EX-21.1 Exhibit 21.1 Subsidiaries of Nektar Therapeutics None.",
            "EX-21.1 3 pcty-20250630xexx211.htm EX-21.1 Document Exhibit 21.1 List of Subsidiaries",
            "EX-21.1 3 q42025exh211listofsubsidia.htm EX-21.1 Document EXHIBIT 21.1 List of Subsidiaries of Bandwidth Inc.",
            "EX-21.1 3 tmb-20251231xex21d1.htm EX-21.1 EXHIBIT 21.1 NUVECTIS THERAPEUTICS INC. List of Subsidiaries Nuvectis Pharma, Inc. does not have any subsidiaries.",
            "EX-21.1 4 aethlon_ex2101.htm LIST OF SUBSIDIARIES Exhibit 21.1 LIST OF SUBSIDIARIES Aethlon Medical Australia Pty Ltd.",
            "EX-21.1 4 atos-ex21_1.htm EX-21.1 EX-21.1 Exhibit 21.1 LIST OF SUBSIDIARIES Atossa Genetics UK Ltd. Atossa Genetics AUS Pty Ltd.",
            "EX-21.1 4 dtil-ex21_1.htm EX-21.1 EX-21.1 Exhibit 21.1 Subsidiaries Precision BioSciences, Inc. has no subsidiaries.",
            "EX-21.1 4 ex21-1.htm Exhibit 21.1 List of Subsidiaries SINTX Armor, Inc., a Utah corporation. Technology Assessment and Transfer, Inc., a Maryland corporation.",
            "EX-21.1 4 ex2111.htm EX-21.1 Document Exhibit 21.1 LIST OF SUBSIDIARIES Sadot Group, Inc. serves as a holding company of the following subsidiaries",
            "EX-21.1 4 ex_864802.htm EXHIBIT 21.1 ex_864802.htm Exhibit 21.1 List of Subsidiaries Moving iMage Technologies, LLC - California MiT Acquisition Co., LLC",
            "EX-21.1 4 exhibit211significantsubsi.htm EX-21.1 Document Exhibit 21.1 Significant Subsidiaries of Chewy, Inc. None.",
            "EX-21.1 4 urg-20241231xex21d1.htm EX-21.1 Exhibit 21.1",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)
        self.assertTrue(gns._phase6_is_probable_ex21_entity_name("LiqTech USA, Inc."))
        self.assertTrue(gns._phase6_is_probable_ex21_entity_name("Tompkins Community Bank"))

    def test_phase6_is_probable_ex21_entity_name_rejects_more_live_graph_rows(self):
        for text in (
            "of the Registrant",
            "OF THE REGISTRANT",
            "Subsidiary of the Registrant",
            "50% or Greater Joint Venture Interests of the Registrant",
            "The Registrant has the following subsidiaries: ACNB Bank",
            "At December 31, 2025, the Registrant had the following subsidiaries",
            "DESCRIPTION OF THE REGISTRANT'S SECURITIES REGISTERED PURSUANT TO SECTION 12 OF THE SECURITIES EXCHANGE ACT OF 1934",
            "List of Registrant's Subsidiaries",
            "KIRBY CORPORATION - PARENT AND REGISTRANT",
            "(*)",
            "_____________________",
            "________________________________________________________________________",
            "a2025-12x31apalistingofs",
            "a81subsidiarieslist",
            "celh2025subsidiaries-10x",
            "exhibit81subsidiaries",
            "Guarantor",
            "Directors",
            "Signature",
            "Incorporated",
            "CORPORATIONS",
            "OPERATIONS",
            "Guatemala",
            "Nicaragua",
            "Bangladesh",
            "Hyderabad",
            "Wilmington",
            "Portsmouth",
            "Name",
            "Subsidiary",
            "Entity Name",
            "State of incorporation",
            "Name of Subsidiary",
            "Title of each class",
            "The state of incorporation or organization of each subsidiary is Virginia, except as noted below",
            "The Registrant's subsidiaries are listed below",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)
        for text in (
            "PacifiCorp",
            "AbCellera Australia Pty Ltd.",
            "Biologiques AbCellera Quebec Inc.",
            "Apple Distribution International Limited",
        ):
            self.assertTrue(gns._phase6_is_probable_ex21_entity_name(text), text)

    def test_phase6_is_probable_ex21_entity_name_rejects_live_heading_and_sentence_rows(self):
        for text in (
            "LISTING A",
            "Subsidiaries (Alphabetically)",
            "the delisting of Alcon equity securities",
            "Applied Ventures, LLC owns 50% of the following subsidiary",
            "Third Tier Subsidiaries",
            "Subsidiary Legal Name",
            "INC. AND SUBSIDIARIES",
            "Name of Organization",
            "State of Origin",
            "Subsidiaries that are 50% owned",
            "Subsidiaries that are 100% owned",
            "All companies are incorporated in the State of Delaware unless otherwise indicated.",
            "of Subsidiaries",
            "LISTING OF SUBSIDIARIES",
            "List of Significant Subsidiaries",
            "Indicates the number of subsidiaries levels the subsidiary resides beneath Citigroup Inc.",
            "Capital City Bank Group, Inc. Subsidiaries, at December 31, 2025.",
            "Name of the Entity",
            "* Subsidiaries that, in aggregate, would not be a “significant subsidiary” as defined in Rule 1-02(w) of Regulation S-X, have been omitted.",
            "The Bank's subsidiaries are First Citizens Insurance Agency, Inc. and 1st Realty of PA, LLC, both of Mansfield, Pennsylvania.",
            "Name and surname of shareholder",
            "Name and surname of shareholder representative (if applicable)",
            "Name of CSDP or Broker (if shares are held in dematerialised format)",
            "[ ] Brackets indicate state or country of incorporation or organization and do not form part of corporate name.",
            "Consolidated Subsidiaries as of",
            "In addition, we also had the following subsidiaries",
            "Euronet's wholly owned subsidiaries were",
            "List of Registrant’s Subsidiaries",
            "Principal Subsidiaries",
            "All Subsidiaries listed above were incorporated in Oklahoma, except as noted.",
            "This entity has filed a petition for reorganization under Chapter",
            "its subsidiaries effective as of the petition date. Any ownership of this entity after completion of the reorganization process",
            "individual accounts opened in the name of the owner, either an individual or legal person",
            "the prior consent of the Regulator, if required pursuant to Applicable Banking Regulations",
            "Affiliated Companies (50% and less ownership)",
            "Affiliated Subsidiaries (50% or less ownership)",
            "EXHIBIT 21.1 SUBSIDIARIES",
            "Constellation Energy Corporation (50% and Greater) 01/31/2026",
            "2025 Subsidiaries Or Affiliates",
            "Subsidiaries consolidated on a line-by-line basis",
            "Subsidiaries valued at cost",
            "Names of Significant Subsidiaries",
            "Crescent Energy Company Subsidiaries",
            "Financial, Inc. Subsidiaries",
            "▪ acquires an additional interest in shares so that the bidder’s aggregate interest carries 50% or more of such voting rights",
            "*Indicates a not for profit organization",
            "2 Owned 50% by Coherent Corp. and 50% by our joint venture partner.",
            "This subsidiary also conducts business under the assumed name of NALCO Water",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)

    def test_phase6_clean_ex21_entity_name_salvages_real_entities_from_live_annotations(self):
        cases = {
            "Astoria Cinemas Grand AB (50%)": "Astoria Cinemas Grand AB",
            "NWL Pacific, inc. – incorporated in South Korea (50% minority interest owned by Megatran Industries Inc)": "NWL Pacific, inc.",
            "Hudson Technologies Company incorporated in the State of Delaware": "Hudson Technologies Company",
            "RRC International, Inc. incorporated in the State of New York": "RRC International, Inc.",
            "SFB Fueling, LLC (50% sub of AFH, Inc.)": "SFB Fueling, LLC",
            "Transmission Infrastructure Partnerships Ltd (50% interest)": "Transmission Infrastructure Partnerships Ltd",
            "MAG International (50% owned)": "MAG International",
            "Pilot Knob Pellet Co. (50% owned)": "Pilot Knob Pellet Co.",
            "CM Advanced Printing Iberia, S.A. (50% owned)": "CM Advanced Printing Iberia, S.A.",
            "Rutland DCC Inc Manufacturing Private Limited (50% owned)": "Rutland DCC Inc Manufacturing Private Limited",
            "Servicios Factoria Barbastro, S.A. (50% owned)": "Servicios Factoria Barbastro, S.A.",
            "West Frontier FundCo, LLC": "West Frontier FundCo, LLC",
        }
        for raw, expected in cases.items():
            self.assertEqual(expected, gns._phase6_clean_ex21_entity_name(raw), raw)
            self.assertTrue(gns._phase6_is_probable_ex21_entity_name(raw), raw)

    def test_phase6_is_probable_ex21_entity_name_keeps_legit_live_entities_with_keyword_overlap(self):
        for text in (
            "Prosperity Banking Capital Trust I",
            "First Light Patient Safety Organization, LLC dba First Light PSO",
            "Accenture Single Member S.A. Organization, Information, Technology & Business Development",
            "Transmission Infrastructure Partnerships Ltd (50% interest)",
            "AES Pelletier Solar, LLC",
            "Tier One Insurance Company",
            "Frontier Financial Services Limited",
            "AMH LandCo Tierpointe, LLC",
            "Aramark Organizational Services, LLC",
            "MAG International (50% owned)",
            "Tierra Feliz Development Company LLC",
            "BRES Upper Tier Pooling GP L.P.",
            "Goshen Real Estate of Illinois, LLC",
            "PacifiCorp",
            "Apple Services LATAM LLC",
            "Applied Materials Israel, Ltd.",
        ):
            self.assertTrue(gns._phase6_is_probable_ex21_entity_name(text), text)

    def test_phase6_is_probable_ex21_entity_name_rejects_live_mojibake_rows(self):
        for text in (
            "\ufffd#id\ufffdD c.Aed,T\ufffd\x08\x18\ufffd\ufffd\x00=\x06 \x11\ufffd\x13\ufffdu",
            "\x1b\ufffd%\ufffd\ufffd\ufffd\ufffdy\ufffd\x19d\ufffdUb\ufffd\ufffd\x00\ufffdsX\ufffd\ufffd\ufffd\u03c8",
            "\ufffd\ufffdDH|6/m`?ufy\x02\ufffd\u02de\ufffd@\x03\ufffd\ufffd\ufffdc\ufffd\ufffd}\x03N\ufffd\ufffd\x00\ufffd6",
            "\ufffd\u062e\ufffdTs\ufffd\x11\x11\ufffd\ufffdPz\ufffd\u055f\ufffdn\ufffd\ufffd}w\ufffd",
            "\ufffd\ufffd\ufffd",
            "\x16\ufffd\ufffd\ufffd\ufffd \ufffd\ufffd?:\ufffd%H\ufffd\u0599\ufffd\ufffd\ufffdq\ufffd",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text.encode("unicode_escape").decode())

    def test_phase6_is_probable_ex21_entity_name_rejects_numeric_only_fragment_rows(self):
        for text in (
            "27 29 29",
            "12 31 25",
            "2025 2026",
            "10 20 30 40",
            "12/31/25",
            "12/31/2025",
            "2025/12/31",
            "01-31-25",
            "2024-12-31",
            "21 1",
            "27/29/29",
            "1 2 3 4 5",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)

    def test_phase6_is_probable_ex21_entity_name_keeps_legit_mixed_alphanumeric_entity_names(self):
        for text in (
            "3M Innovative Properties Company",
            "51Talk Holdings Limited",
            "G8 Education Inc.",
            "7-Eleven, Inc.",
            "1011778 B.C. Unlimited Liability Company",
            "TC1 LLC",
        ):
            self.assertTrue(gns._phase6_is_probable_ex21_entity_name(text), text)

    def test_phase6_is_probable_ex21_entity_name_rejects_live_phase6b_boilerplate_rows(self):
        for text in (
            "At December 31, 2025",
            "at December 31, 2025",
            "At April 25, 2025",
            "Date: March 28, 2025",
            "Date: April 8, 2025",
            "of each subsidiary is",
            "The following bank subsidiaries are national banks and are",
            "The following nonbank subsidiaries are",
            "Subsidiary or Affiliate",
            "SUBSIDIARY LIST",
            "Subsidiary List",
            "May be deemed to be an affiliate pursuant to Rule 1-02 of SEC Regulation S-X.",
            "s sole subsidiary.",
            "At December 31, 2025, the Registrant had the following subsidiaries",
            "The following subsidiaries are listed below",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)

    def test_phase6_is_probable_ex21_entity_name_keeps_legit_real_world_at_prefix_names(self):
        for text in (
            "AT Holdings II Company",
            "AT Squared Holdings Limited",
            "AT Atlantic Holding LLC",
            "AT Iberia C.V.",
            "AT Kenya C.V.",
            "AT Netherlands C.V.",
            "AT Netherlands Coöperatief U.A",
            "AT Rhine C.V.",
            "AT Sher Netherlands Coöperatief U.A.",
            "AT South America C.V.",
            "At World Properties Holdings, LLC",
            "At World Properties Midco, LLC",
            "At World Properties New Holdings, Inc.",
        ):
            self.assertTrue(gns._phase6_is_probable_ex21_entity_name(text), text)

    def test_phase6_clean_ex21_entity_name_salvages_live_phase6b_sentence_rows(self):
        cases = {
            "CZFS Acquisition Company, LLC of Mansfield, Pennsylvania is the Company’s sole subsidiary.": "CZFS Acquisition Company, LLC",
            "Blockchain Technologies Ltd., which was incorporated on February 18, 2022 under the laws of the Cayman Islands (80%).": "Blockchain Technologies Ltd.",
            "Equity Holdings LLC, which was incorporated on May 18, 2021 under the laws of the Cayman Islands (100%).": "Equity Holdings LLC",
            "Holdings Ltd (f/k/a Oxbridge VT Ltd.), which was incorporated on January 27, 2022 under the laws of the Cayman Islands (80%).": "Holdings Ltd",
            "Re NS Limited, which was incorporated on December 22, 2017 under the laws of the Cayman Islands (80%).": "Re NS Limited",
            "Reinsurance Limited, which was incorporated on April 23, 2013 under the laws of the Cayman Islands (100%).": "Reinsurance Limited",
            "Muscle Maker Corp. LLC, a directly wholly owned subsidiary, which was formed in Nevada on July 18, 2019": "Muscle Maker Corp. LLC",
            "Muscle Maker Development International. LLC, a directly wholly owned subsidiary, which was formed in Nevada on November 13, 2020": "Muscle Maker Development International. LLC",
            "Muscle Maker Development, LLC, a directly wholly owned subsidiary, which was formed in Nevada on July 18, 2019": "Muscle Maker Development, LLC",
            "Muscle Maker USA, Inc., a directly wholly owned subsidiary, which was formed in Texas on March 14, 2019": "Muscle Maker USA, Inc.",
            "Poke Co Holdings LLC, a directly wholly owned subsidiary, which was formed in Connecticut on July 18, 2018.": "Poke Co Holdings LLC",
            "Pokemoto LLC, a directly wholly owned subsidiary, which was formed in Nevada on August 19, 2021, which holds the below subsidiaries.": "Pokemoto LLC",
            "Sadot Agri FZCO, a directly wholly owned subsidiary , which was formed in Dubai on September 19, 2024.": "Sadot Agri FZCO",
            "Sadot Brasil Ltda, a directly wholly owned subsidiary, which was formed in Brazil on December 11, 2023": "Sadot Brasil Ltda",
            "Sadot Canada Inc., a drectly wholly owned subsidiary, which was formed in Canada on June 13, 2024.": "Sadot Canada Inc.",
            "Sadot LLC of Mauritius, a directly wholly owned subsidiary, which was formed in Mauritius on July 25, 2023.": "Sadot LLC of Mauritius",
            "Sadot Latam LLC, a directly wholly owned subsidiary, which was formed in Delaware on June 22, 2023": "Sadot Latam LLC",
        }
        for raw, expected in cases.items():
            self.assertEqual(expected, gns._phase6_clean_ex21_entity_name(raw), raw)
            self.assertTrue(gns._phase6_is_probable_ex21_entity_name(expected), expected)

    def test_phase6_is_probable_ex21_entity_name_rejects_live_phase6b_prefix_and_fragment_rows(self):
        for text in (
            "Subsidiary of Columbia Bank",
            "Subsidiary of Columbia Financial, Inc.",
            "Unconsolidated subsidiary of Columbia Financial, Inc.",
            "Subsidiary of Eastern Shore Natural Gas Company",
            "Subsidiary of Florida Public Utilities Company",
            "Subsidiary of Sharp Energy, Inc.",
            "SUBSIDIARY OF THE COMPANY",
            "Subsidiary Level (a)",
            "Subsidiary does Business",
            "All Subsidiaries listed above were",
            "All subsidiaries are 100% owned by Cellebrite DI Ltd., except: Cellebrite",
            "All subsidiaries are formed in the State of Nevada and wholly owned unless otherwise specifically identified.",
            "DIRECT AND INDIRECT SUBSIDIARIES OF",
            "SYSCO CORPORATION DIRECT AND INDIRECT SUBSIDIARIES AND DBA's",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)

    def test_phase6_clean_ex21_entity_name_salvages_live_prefix_embedded_real_entity_names(self):
        cases = {
            "AuburnBank Alabama INDIRECT SUBSIDIARIES Banc of Auburn, Inc.": "Banc of Auburn, Inc.",
            "AuburnBank Alabama INDIRECT SUBSIDIARIES Auburn Insurance Agency, LLC": "Auburn Insurance Agency, LLC",
            "AuburnBank Alabama INDIRECT SUBSIDIARIES Auburn Holdings, Inc.": "Auburn Holdings, Inc.",
            "The unconsolidated subsidiary of Community Bancorp. is CMTV Statutory Trust I, a Delaware statutory business trust.": "CMTV Statutory Trust I",
            "The unconsolidated subsidiary of Example Bancorp. is Example Statutory Trust II, a Delaware statutory trust.": "Example Statutory Trust II",
            ". Name and Address of Subsidiary Incorporated 1. ChoiceOne Bank 109 East Division Sparta, Michigan 49345": "ChoiceOne Bank",
            ". Name and Address of Subsidiary Incorporated 2. First Community Bank 100 Main Street Bluefield, Virginia 24605": "First Community Bank",
            "Name and Address of Subsidiary Incorporated 3. Trustar Bank 1001 Main Street Great Falls, Virginia 22066": "Trustar Bank",
            ". Name and Address of Subsidiary Incorporated 4. Heartland Bank 401 Main Street Bloomington, Illinois 61701": "Heartland Bank",
            ". Name and Address of Subsidiary Incorporated 5. Midland States Bank 1201 Network Centre Drive Effingham, Illinois 62401": "Midland States Bank",
        }
        for raw, expected in cases.items():
            self.assertEqual(expected, gns._phase6_clean_ex21_entity_name(raw), raw)
            self.assertTrue(gns._phase6_is_probable_ex21_entity_name(expected), expected)

    def test_phase6_clean_ex21_entity_name_strips_live_trailing_subsidiary_parentheticals(self):
        cases = {
            "ARKLA Petroleum, LLC (Subsidiary of NGS Sub. Corp.)": "ARKLA Petroleum, LLC",
            "NGS Resources, LLC (Subsidiary of NGS Technologies, Inc.)": "NGS Resources, LLC",
            "The Kelly Relief Fund (Non-Profit – subsidiary of Kelly Services, Inc.)": "The Kelly Relief Fund",
            "Alamo Barge Lines, LLC (subsidiary of Kirby Inland Marine, LP)": "Alamo Barge Lines, LLC",
            "Diesel Dash LLC (subsidiary of Kirby Distribution & Services, Inc.)": "Diesel Dash LLC",
            "Dixie Carriers, Inc. (subsidiary of Kirby Inland Marine, LP)": "Dixie Carriers, Inc.",
            "EBL Marine I LLC (subsidiary of Kirby Inland Marine, LP)": "EBL Marine I LLC",
            "Engine Systems, Inc. (subsidiary of Kirby Engine Systems LLC)": "Engine Systems, Inc.",
            "Higman Marine, Inc. (subsidiary of Kirby Inland Marine, LP)": "Higman Marine, Inc.",
            "Kirby Offshore Marine Operating, LLC (subsidiary of Kirby Offshore Marine, LLC)": "Kirby Offshore Marine Operating, LLC",
            "Stewart & Stevenson LLC (subsidiary of Kirby Distribution & Services, Inc.)": "Stewart & Stevenson LLC",
            "Stewart & Stevenson de Venezuela, S.A. (subsidiary of Stewart & Stevenson LLC 99.95%)": "Stewart & Stevenson de Venezuela, S.A.",
        }
        for raw, expected in cases.items():
            self.assertEqual(expected, gns._phase6_clean_ex21_entity_name(raw), raw)
            self.assertTrue(gns._phase6_is_probable_ex21_entity_name(expected), expected)

    def test_phase6_is_probable_ex21_entity_name_rejects_company_prefixed_subsidiary_list_headings(self):
        for text in (
            "JBT MAREL CORPORATION SUBSIDIARY LIST",
            "Acme Holdings Subsidiary List",
            "otherwise indicated, all subsidiaries are 100%-owned.",
            "Otherwise indicated, all subsidiaries are wholly owned.",
            "Otherwise indicated, all subsidiaries are organized in Delaware.",
            "All subsidiaries are 100% owned by Parent Holdings, Inc., except: Child",
            "All subsidiaries are formed in the State of Nevada and wholly owned unless otherwise specifically identified.",
            "All Subsidiaries listed above were",
            "DIRECT AND INDIRECT SUBSIDIARIES OF",
            "SYSCO CORPORATION DIRECT AND INDIRECT SUBSIDIARIES AND DBA's",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)

    def test_phase6_is_probable_ex21_entity_name_rejects_descriptor_only_subsidiary_rows(self):
        for text in (
            "Name Under Which the Subsidiary Does Business",
            "Name Under Which the Subsidiary Does Business 1. LPL Holdings, Inc.** Massachusetts LPL 2. PTC Holdings, Inc.**",
            "Each a Delaware business trust subsidiary of Mercantile Bank Corporation",
            "Each a statutory trust subsidiary of Example Bancorp, Inc.",
            "Wholly-owned bank subsidiary of Mercantile Bank Corporation",
            "Wholly owned bank subsidiary of Example Bancorp, Inc.",
            "100%-owned subsidiary of NextEra Energy Capital Holdings, Inc.",
            "99%-owned subsidiary of Example Holdings, Inc.",
            "75%-owned subsidiary of Example Energy Holdings, LLC",
            "51%-owned subsidiary of Example Marine Holdings Ltd.",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)

    def test_phase6_clean_ex21_entity_name_salvages_last_mile_live_descriptor_rows(self):
        cases = {
            "most significant subsidiary of Landmark Bancorp, Inc. (the “Company”) is Landmark National Bank, a national banking association": "Landmark National Bank",
            "most significant subsidiary of Example Bancorp, Inc. is Example National Bank, a national banking association": "Example National Bank",
            "BAMKO India Private Limited, a 99%-owned subsidiary of BAMKO, LLC": "BAMKO India Private Limited",
            "Example Power Holdings, LLC, a 75%-owned subsidiary of Example Energy, Inc.": "Example Power Holdings, LLC",
            "Example Shipping Ltd., a 51%-owned subsidiary of Example Marine Holdings Ltd.": "Example Shipping Ltd.",
        }
        for raw, expected in cases.items():
            self.assertEqual(expected, gns._phase6_clean_ex21_entity_name(raw), raw)
            self.assertTrue(gns._phase6_is_probable_ex21_entity_name(expected), expected)

    def test_phase6_is_probable_ex21_entity_name_rejects_live_note_only_rows(self):
        for text in (
            "(including dba name, if applicable)",
            "(held through direct subsidiaries or other indirect subsidiaries)",
            "(in alphabetical order)",
            "(Nominal value €0.09 per share)",
            "(créditos subordinados)",
            "(dba Cracker Barrel Old Country Store)",
            "(dba Maple Street Biscuit Company)",
            "(with branches in Austria and Sweden)",
            "(fka DE 201 Santa Monica, LLC)",
            "(fka Douglas Emmett, LLC)",
            "Incorporated in Colorado",
            "Incorporated in Delaware",
            "Incorporated in the State of Georgia",
            "Organized in the State of Kentucky",
            "Organized Under Law of",
            "Name under which business conducted",
            "Name under which",
            "Name Under Which Company Does Business",
            "Subsidiary and Name Under Which Business is Done",
            "sec.gov/Archives/edgar/data/1819411/000114036121005495/nt10015006x7_ex21-1.htm",
        ):
            self.assertFalse(gns._phase6_is_probable_ex21_entity_name(text), text)

    def test_phase6_clean_ex21_entity_name_strips_live_trade_name_and_note_suffixes(self):
        cases = {
            "Envoy Air Inc. (operates under the trade name “American Eagle”)": "Envoy Air Inc.",
            "PSA Airlines, Inc. (operates under the trade name “American Eagle”)": "PSA Airlines, Inc.",
            "Piedmont Airlines, Inc. (operates under the trade name “American Eagle”)": "Piedmont Airlines, Inc.",
            "ProFrac Holdings II, LLC (dba PF Holdings)": "ProFrac Holdings II, LLC",
            "First Light Patient Safety Organization, LLC dba First Light PSO": "First Light Patient Safety Organization, LLC",
            "AmCo Holding Company (incorporated in Delaware)": "AmCo Holding Company",
            "BlueLine Cayman Holdings, LLC (incorporated in the Cayman Islands)": "BlueLine Cayman Holdings, LLC",
            "Interboro Insurance Company (incorporated in New York)": "Interboro Insurance Company",
            "Edge Adhesives Holdings, Inc. (organized in Delaware)": "Edge Adhesives Holdings, Inc.",
            "AB Nasdaq Vilnius (organized in Lithuania)": "AB Nasdaq Vilnius",
            "Adenza Australia Pty Ltd. (organized in Australia)": "Adenza Australia Pty Ltd.",
            "ACM Research (Chengdu), Inc. (\"ACM Chengdu\")": "ACM Research (Chengdu), Inc.",
            "Shengwei Research (Shanghai), Inc. (1)": "Shengwei Research (Shanghai), Inc.",
            "Aflac Ventures India Fund LLC (3)": "Aflac Ventures India Fund LLC",
            "Apple Nine Pennsylvania Business Trust*": "Apple Nine Pennsylvania Business Trust*",
        }
        for raw, expected in cases.items():
            self.assertEqual(expected, gns._phase6_clean_ex21_entity_name(raw), raw)
            self.assertTrue(gns._phase6_is_probable_ex21_entity_name(expected), expected)

    def test_phase6_dedupe_ex21_entries_merges_live_fragmented_entity_rows(self):
        cases = (
            (
                [{"legal_name": "MacroChem", "aliases": [], "jurisdiction": ""}, {"legal_name": "Therapeutics LLC", "aliases": [], "jurisdiction": ""}],
                ["MacroChem Therapeutics LLC"],
            ),
            (
                [{"legal_name": "AllianceBernstein", "aliases": [], "jurisdiction": ""}, {"legal_name": "Corporation", "aliases": [], "jurisdiction": ""}],
                ["AllianceBernstein Corporation"],
            ),
            (
                [{"legal_name": "ChoiceOne", "aliases": [], "jurisdiction": ""}, {"legal_name": "Bank", "aliases": [], "jurisdiction": ""}],
                ["ChoiceOne Bank"],
            ),
            (
                [{"legal_name": "Example National", "aliases": [], "jurisdiction": ""}, {"legal_name": "Bank", "aliases": [], "jurisdiction": ""}],
                ["Example National Bank"],
            ),
            (
                [{"legal_name": "AbCellera", "aliases": [], "jurisdiction": ""}, {"legal_name": "Australia Pty Ltd.", "aliases": [], "jurisdiction": "Australia"}],
                ["AbCellera Australia Pty Ltd."],
            ),
            (
                [{"legal_name": "Apple Services", "aliases": [], "jurisdiction": ""}, {"legal_name": "LATAM LLC", "aliases": [], "jurisdiction": ""}],
                ["Apple Services LATAM LLC"],
            ),
            (
                [{"legal_name": "AAG Private Placement-1", "aliases": [], "jurisdiction": ""}, {"legal_name": "Parent LLC", "aliases": [], "jurisdiction": ""}],
                ["AAG Private Placement-1 Parent LLC"],
            ),
            (
                [{"legal_name": "Accenture", "aliases": [], "jurisdiction": ""}, {"legal_name": "GmbH", "aliases": [], "jurisdiction": "Germany"}],
                ["Accenture GmbH"],
            ),
            (
                [{"legal_name": "Apple South Asia", "aliases": [], "jurisdiction": ""}, {"legal_name": "(Thailand) Limited", "aliases": [], "jurisdiction": ""}],
                ["Apple South Asia (Thailand) Limited"],
            ),
            (
                [{"legal_name": "BAMKO India", "aliases": [], "jurisdiction": ""}, {"legal_name": "Private Limited", "aliases": [], "jurisdiction": "India"}],
                ["BAMKO India Private Limited"],
            ),
        )
        for raw_entries, expected_names in cases:
            deduped = gns._phase6_dedupe_ex21_entries(raw_entries)
            self.assertEqual(expected_names, [entry["legal_name"] for entry in deduped], raw_entries)

    def test_phase6_dedupe_ex21_entries_does_not_merge_unrelated_rows(self):
        raw_entries = [
            {"legal_name": "Apple Canada Inc.", "aliases": [], "jurisdiction": "Canada"},
            {"legal_name": "Braeburn Capital, Inc.", "aliases": [], "jurisdiction": "Delaware"},
            {"legal_name": "iTunes K.K.", "aliases": [], "jurisdiction": "Japan"},
            {"legal_name": "MacroChem Therapeutics LLC", "aliases": [], "jurisdiction": ""},
            {"legal_name": "Therapeutics LLC", "aliases": [], "jurisdiction": ""},
        ]
        deduped = gns._phase6_dedupe_ex21_entries(raw_entries)
        self.assertEqual(
            [
                "Apple Canada Inc.",
                "Braeburn Capital, Inc.",
                "MacroChem Therapeutics LLC",
                "Therapeutics LLC",
                "iTunes K.K.",
            ],
            [entry["legal_name"] for entry in deduped],
        )

    def test_phase6_extract_ex21_compact_entries_handles_workiva_ocr_jurisdiction_names(self):
        text = (
            "Exhibit 21.1 Subsidiaries of AbCellera Biologics Inc.* "
            "Name Jurisdiction of Incorporation or Organization "
            "AbCellera Australia Pty Ltd. Australia "
            "AbCellera Properties GP Inc. Canada "
            "AbCellera Properties Columbia GP Inc. Canada "
            "AbCellera Properties Evans GP Inc. Canada "
            "AbCellera US Holdings Inc. Delaware "
            "Biologiques AbCellera Quebec Inc. Canada "
            "Lineage Biosciences Inc. Delaware "
            "Trianni Inc. Delaware "
            "* Includes subsidiaries that do not fall under the definition of significant subsidiary."
        )
        entries = gns._phase6_extract_ex21_compact_jurisdiction_entries(text)
        names = {entry["legal_name"] for entry in entries}
        self.assertEqual(
            {
                "AbCellera Australia Pty Ltd.",
                "AbCellera Properties GP Inc.",
                "AbCellera Properties Columbia GP Inc.",
                "AbCellera Properties Evans GP Inc.",
                "AbCellera US Holdings Inc.",
                "Biologiques AbCellera Quebec Inc.",
                "Lineage Biosciences Inc.",
                "Trianni Inc.",
            },
            names,
        )
        self.assertNotIn("AbCellera", names)
        self.assertNotIn("Pty Ltd.", names)

    def test_phase6_extract_ex21_entries_from_html_handles_workiva_inline_ocr_image_exhibit(self):
        html = """
        <DOCUMENT>
        <TYPE>EX-21.1</TYPE>
        <TEXT>
        <HTML><BODY>
        <DIV><!-- exhibit211001.jpg --></DIV>
        <IMG src="exhibit211001.jpg" title="slide1">
        <DIV><FONT size="1" style="font-size:1pt;color:white">
        Exhibit 21.1 Subsidiaries of AbCellera Biologics Inc.*
        Name Jurisdiction of Incorporation or Organization
        AbCellera Australia Pty Ltd. Australia
        AbCellera Properties GP Inc. Canada
        AbCellera Properties Columbia GP Inc. Canada
        AbCellera Properties Evans GP Inc. Canada
        AbCellera US Holdings Inc. Delaware
        Biologiques AbCellera Quebec Inc. Canada
        Lineage Biosciences Inc. Delaware
        Trianni Inc. Delaware
        </FONT></DIV>
        </BODY></HTML>
        </TEXT>
        </DOCUMENT>
        """
        entries = gns._phase6_extract_ex21_entries_from_html(html)
        names = {entry["legal_name"] for entry in entries}
        self.assertIn("AbCellera Australia Pty Ltd.", names)
        self.assertIn("Biologiques AbCellera Quebec Inc.", names)
        self.assertIn("Trianni Inc.", names)
        self.assertNotIn("AbCellera", names)
        self.assertNotIn("Pty Ltd.", names)

    def test_phase6_is_ex21_document_name_rejects_image_artifacts(self):
        self.assertFalse(gns._phase6_is_ex21_document_name("exhibit211001.jpg"))
        self.assertFalse(gns._phase6_is_ex21_document_name("azn-20251231xex15d1g021.jpg"))
        self.assertFalse(gns._phase6_is_ex21_document_name("ex21-1.png"))
        self.assertTrue(gns._phase6_is_ex21_document_name("exhibit211.htm"))

    def test_phase6_decode_sec_document_bytes_rejects_binary_media(self):
        jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x02\x00"
        self.assertEqual("", gns._phase6_decode_sec_document_bytes(jpeg))

    def test_phase6_ex21_candidate_document_names_fetches_submission_text_transiently(self):
        filing = {
            "cik": "0001158114",
            "accession_compact": "000143774926005875",
            "primary_document": "aaoi20251231_10k.htm",
            "filing_url": "https://www.sec.gov/Archives/edgar/data/1158114/000143774926005875/aaoi20251231_10k.htm",
        }
        index_payload = {
            "directory": {
                "item": [
                    {"name": "0001437749-26-005875.txt"},
                    {"name": "ex_921421.htm"},
                ]
            }
        }
        submission_text = """
        <SEC-DOCUMENT>
        <DOCUMENT>
        <TYPE>EX-21.1
        <DESCRIPTION>Significant Subsidiaries
        <FILENAME>ex_921421.htm
        </DOCUMENT>
        </SEC-DOCUMENT>
        """

        class _FakeSecModule:
            @staticmethod
            def _download_sec_filing_document(**kwargs):
                raise AssertionError("raw SEC download helper should not be used for transient submission discovery")

        with patch.object(gns, "_phase6_find_cached_sec_document", return_value=""), \
             patch.object(gns, "_phase6_fetch_sec_document_text_transient", return_value=submission_text) as transient_mock:
            names = gns._phase6_ex21_candidate_document_names(_FakeSecModule(), filing, index_payload)
        transient_mock.assert_called_once()
        self.assertEqual(["ex_921421.htm"], names)

    def test_phase6_ex21_candidate_document_names_ignores_stale_primary_document_links(self):
        filing = {
            "cik": "0001158114",
            "accession_compact": "000143774926005875",
            "primary_document": "aaoi20251231_10k.htm",
            "filing_url": "https://www.sec.gov/Archives/edgar/data/1158114/000143774926005875/aaoi20251231_10k.htm",
        }
        index_payload = {
            "directory": {
                "item": [
                    {"name": "aaoi20251231_10k.htm"},
                    {"name": "0001437749-26-005875.txt"},
                    {"name": "ex_921421.htm"},
                ]
            }
        }
        primary_html = """
        <html><body>
        <a href="http://www.sec.gov/Archives/edgar/data/1158114/000104746913008409/a2216044zex-21_1.htm">Old exhibit</a>
        <a href="ex_921421.htm">Current exhibit file</a>
        </body></html>
        """

        class _FakeSecModule:
            @staticmethod
            def _download_sec_filing_document(**kwargs):
                cache_dir = kwargs.get("cache_subdir")
                raise AssertionError(f"unexpected download for {cache_dir}")

        with tempfile.TemporaryDirectory() as tmpdir:
            cached_dir = os.path.join(tmpdir, "phase6", "0001158114", "000143774926005875")
            os.makedirs(cached_dir, exist_ok=True)
            with open(os.path.join(cached_dir, "aaoi20251231_10k.htm"), "w", encoding="utf-8") as handle:
                handle.write(primary_html)
            with patch.object(gns, "NEXUS_CACHE_DIR", tmpdir), \
                 patch.object(gns, "_phase6_fetch_sec_submission_text", return_value=""):
                names = gns._phase6_ex21_candidate_document_names(_FakeSecModule(), filing, index_payload)
        self.assertNotIn("a2216044zex-21_1.htm", names)
        self.assertEqual([], names)

    def test_phase6_ex21_candidate_document_names_fetches_primary_html_transiently(self):
        filing = {
            "cik": "0001158114",
            "accession_compact": "000143774926005875",
            "primary_document": "aaoi20251231_10k.htm",
            "filing_url": "https://www.sec.gov/Archives/edgar/data/1158114/000143774926005875/aaoi20251231_10k.htm",
        }
        index_payload = {}
        primary_html = """
        <html><body>
        <a href="good-xexx211.htm">Current exhibit file</a>
        </body></html>
        """

        class _FakeSecModule:
            @staticmethod
            def _download_sec_filing_document(**kwargs):
                raise AssertionError("raw SEC download helper should not be used for transient primary-document discovery")

        with patch.object(gns, "_phase6_find_cached_sec_document", return_value=""), \
             patch.object(gns, "_phase6_fetch_sec_submission_text", return_value=""), \
             patch.object(gns, "_phase6_fetch_sec_document_text_transient", return_value=primary_html) as transient_mock:
            names = gns._phase6_ex21_candidate_document_names(_FakeSecModule(), filing, index_payload)
        transient_mock.assert_called_once()
        self.assertEqual(["good-xexx211.htm"], names)

    def test_phase6_load_ex21_entries_reuses_cached_sec_raw_filing(self):
        filing = {
            "cik": "0000320193",
            "accession_compact": "000032019325000079",
            "primary_document": "aapl-20250927.htm",
            "filing_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
        }
        exhibit_name = "a10-kexhibit21109272025.htm"
        exhibit_html = """
        <html><body>
        <table>
          <tr><th>Name of Subsidiary</th><th>Jurisdiction of Incorporation</th></tr>
          <tr><td>Apple Canada Inc.</td><td>Canada</td></tr>
          <tr><td>Apple Asia Limited</td><td>Hong Kong</td></tr>
        </table>
        </body></html>
        """

        class _FakeSecModule:
            @staticmethod
            def _download_sec_filing_document(**kwargs):
                raise AssertionError("live SEC download should not be used when the cached raw filing exists")

        with tempfile.TemporaryDirectory() as tmpdir:
            cached_dir = os.path.join(tmpdir, "sec_edgar_filings", "0000320193", "000032019325000079")
            os.makedirs(cached_dir, exist_ok=True)
            with open(os.path.join(cached_dir, exhibit_name), "w", encoding="utf-8") as handle:
                handle.write(exhibit_html)
            with patch.object(gns, "NEXUS_CACHE_DIR", tmpdir), \
                 patch.object(gns, "_phase6_fetch_sec_filing_index", return_value={"directory": {"item": [{"name": exhibit_name}]}}):
                entries = gns._phase6_load_ex21_entries_for_filing(_FakeSecModule(), "AAPL", filing, ignore_parse_cache=True)
        self.assertEqual(2, len(entries))
        self.assertEqual({"Apple Canada Inc.", "Apple Asia Limited"}, {entry["legal_name"] for entry in entries})

    def test_phase6_load_ex21_entries_falls_back_to_alternate_document_name(self):
        filing = {
            "cik": "0000320193",
            "accession_compact": "000032019325000079",
            "primary_document": "aapl-20250927.htm",
            "filing_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
        }
        attempts: list[str] = []

        class _FakeSecModule:
            @staticmethod
            def _download_sec_filing_document(**kwargs):
                primary_document = kwargs.get("primary_document")
                attempts.append(primary_document)
                if primary_document == "missing-xexx211.htm":
                    return None, False
                cache_dir = os.path.join(
                    gns.NEXUS_CACHE_DIR,
                    kwargs.get("cache_subdir") or "phase6",
                    "0000320193",
                    "000032019325000079",
                )
                os.makedirs(cache_dir, exist_ok=True)
                path = os.path.join(cache_dir, primary_document)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(
                        """
                        <html><body>
                        <table>
                          <tr><th>Name of Subsidiary</th><th>Jurisdiction of Incorporation</th></tr>
                          <tr><td>Apple Canada Inc.</td><td>Canada</td></tr>
                        </table>
                        </body></html>
                        """
                    )
                return path, False

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(
                gns,
                "_phase6_fetch_sec_filing_index",
                return_value={"directory": {"item": [{"name": "missing-xexx211.htm"}, {"name": "good-xexx211.htm"}]}},
            ), patch.object(gns, "NEXUS_CACHE_DIR", tmpdir):
                entries = gns._phase6_load_ex21_entries_for_filing(_FakeSecModule(), "AAPL", filing, ignore_parse_cache=True)
        self.assertEqual(["missing-xexx211.htm", "good-xexx211.htm"], attempts)
        self.assertIn("Apple Canada Inc.", [entry["legal_name"] for entry in entries])

    def test_phase6_load_ex21_entries_downloads_raw_docs_to_shared_sec_cache(self):
        filing = {
            "cik": "0000320193",
            "accession_compact": "000032019325000079",
            "primary_document": "aapl-20250927.htm",
            "filing_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
        }
        cache_subdirs = []

        class _FakeSecModule:
            @staticmethod
            def _download_sec_filing_document(**kwargs):
                cache_subdirs.append(kwargs.get("cache_subdir"))
                cache_dir = os.path.join(
                    gns.NEXUS_CACHE_DIR,
                    kwargs.get("cache_subdir") or "phase6",
                    "0000320193",
                    "000032019325000079",
                )
                os.makedirs(cache_dir, exist_ok=True)
                path = os.path.join(cache_dir, kwargs.get("primary_document") or "doc.htm")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(
                        """
                        <html><body>
                        <table>
                          <tr><th>Name of Subsidiary</th><th>Jurisdiction of Incorporation</th></tr>
                          <tr><td>Apple Canada Inc.</td><td>Canada</td></tr>
                        </table>
                        </body></html>
                        """
                    )
                return path, False

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(
                gns,
                "_phase6_fetch_sec_filing_index",
                return_value={"directory": {"item": [{"name": "good-xexx211.htm"}]}},
            ), patch.object(gns, "NEXUS_CACHE_DIR", tmpdir), \
                 patch.object(gns, "PHASE6_SEC_RAW_CACHE_SUBDIR", "sec_edgar_filings"):
                entries = gns._phase6_load_ex21_entries_for_filing(_FakeSecModule(), "AAPL", filing, ignore_parse_cache=True)
        self.assertEqual(["sec_edgar_filings"], cache_subdirs[:1])
        self.assertIn("Apple Canada Inc.", [entry["legal_name"] for entry in entries])

    def test_phase6_extract_ex21_entries_parses_list_and_plain_text_forms(self):
        html = """
        <html><body>
        <h1>Subsidiaries of the Registrant</h1>
        <ul>
          <li>Prime Security Holdings, Inc. (Nevada)</li>
        </ul>
        <pre>
        Subsidiaries of the Registrant
        Example Subsidiary LLC - Delaware
        </pre>
        </body></html>
        """
        entries = gns._phase6_extract_ex21_entries_from_html(html)
        names = {entry["legal_name"] for entry in entries}
        self.assertIn("Prime Security Holdings, Inc.", names)
        self.assertIn("Example Subsidiary LLC", names)

    def test_cross_validate_edges_drops_ambiguous_reverse_supplier_pairs(self):
        edges = [
            {"sup": "ALSN", "cust": "PCAR", "confidence": 1.0, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-03-06"},
            {"sup": "PCAR", "cust": "ALSN", "confidence": 1.0, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-03-06"},
        ]
        self.assertEqual([], sec.cross_validate_edges(edges))

    def test_cross_validate_edges_keeps_stronger_supplier_direction(self):
        edges = [
            {"sup": "OLED", "cust": "LPL", "confidence": 1.0, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-03-06"},
            {"sup": "LPL", "cust": "OLED", "confidence": 0.85, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-03-06"},
        ]
        validated = sec.cross_validate_edges(edges)
        self.assertEqual(1, len(validated))
        self.assertEqual(("OLED", "LPL"), (validated[0]["sup"], validated[0]["cust"]))

    def test_phase3_dedupe_reciprocal_supply_chain_edge_candidates_real_world_cases(self):
        edges = [
            {"sup": "AMRX", "cust": "CAH", "conf": 1.0, "src": "10-K", "rev": 70.0, "lc": "2026-02-27", "aa": "2025-02-28", "etype": "SUPPLIER_OF"},
            {"sup": "CAH", "cust": "AMRX", "conf": 0.85, "src": "10-K", "rev": None, "lc": "2026-02-27", "aa": "2025-02-28", "etype": "SUPPLIER_OF"},
            {"sup": "HWM", "cust": "GE", "conf": 1.0, "src": "10-K", "rev": 62.0, "lc": "2025-02-14", "aa": "2025-02-14", "etype": "SUPPLIER_OF"},
            {"sup": "GE", "cust": "HWM", "conf": 0.85, "src": "10-K", "rev": None, "lc": "2025-02-14", "aa": "2025-02-14", "etype": "SUPPLIER_OF"},
            {"sup": "CALM", "cust": "WMT", "conf": 1.0, "src": "10-K", "rev": 50.1, "lc": "2025-07-22", "aa": "2025-07-22", "etype": "SUPPLIER_OF"},
            {"sup": "WMT", "cust": "CALM", "conf": 0.85, "src": "10-K", "rev": None, "lc": "2025-07-22", "aa": "2025-07-22", "etype": "SUPPLIER_OF"},
            {"sup": "SITM", "cust": "ARW", "conf": 1.0, "src": "10-K", "rev": 47.0, "lc": "2025-02-14", "aa": "2025-02-14", "etype": "SUPPLIER_OF"},
            {"sup": "ARW", "cust": "SITM", "conf": 0.85, "src": "10-K", "rev": None, "lc": "2025-02-14", "aa": "2025-02-14", "etype": "SUPPLIER_OF"},
            {"sup": "AM", "cust": "AR", "conf": 1.0, "src": "10-K", "rev": None, "lc": "2026-02-11", "aa": "2025-02-12", "etype": "SUPPLIER_OF"},
            {"sup": "AR", "cust": "AM", "conf": 1.0, "src": "10-K", "rev": None, "lc": "2026-02-11", "aa": "2025-02-12", "etype": "SUPPLIER_OF"},
            {"sup": "AMAT", "cust": "ICHR", "conf": 0.85, "src": "10-K", "rev": None, "lc": "2026-02-20", "aa": "2025-02-21", "etype": "SUPPLIER_OF"},
            {"sup": "ICHR", "cust": "AMAT", "conf": 0.85, "src": "10-K", "rev": None, "lc": "2026-02-20", "aa": "2025-02-21", "etype": "SUPPLIER_OF"},
            {"sup": "DCH", "cust": "GM", "conf": 1.0, "src": "10-K", "rev": None, "lc": "2026-02-13", "aa": "2025-02-14", "etype": "SUPPLIER_OF"},
            {"sup": "GM", "cust": "DCH", "conf": 1.0, "src": "10-K", "rev": None, "lc": "2026-02-13", "aa": "2025-02-14", "etype": "SUPPLIER_OF"},
            {"sup": "ICHR", "cust": "LRCX", "conf": 1.0, "src": "10-K", "rev": None, "lc": "2026-02-20", "aa": "2025-02-21", "etype": "SUPPLIER_OF"},
            {"sup": "LRCX", "cust": "ICHR", "conf": 1.0, "src": "10-K", "rev": None, "lc": "2026-02-20", "aa": "2025-02-21", "etype": "SUPPLIER_OF"},
            {"sup": "HLMN", "cust": "HD", "conf": 1.0, "src": "10-K", "rev": 41.0, "lc": "2026-02-17", "aa": "2025-02-20", "etype": "SUPPLIER_OF"},
            {"sup": "AMWD", "cust": "HD", "conf": 1.0, "src": "10-K", "rev": 40.8, "lc": "2025-06-25", "aa": "2025-06-25", "etype": "SUPPLIER_OF"},
            {"sup": "JBSS", "cust": "WMT", "conf": 1.0, "src": "10-K", "rev": 40.0, "lc": "2025-08-20", "aa": "2025-08-20", "etype": "SUPPLIER_OF"},
            {"sup": "MBUU", "cust": "ONEW", "conf": 1.0, "src": "10-K", "rev": 39.4, "lc": "2025-12-15", "aa": "2025-12-15", "etype": "SUPPLIER_OF"},
        ]

        deduped = gns._dedupe_reciprocal_supply_chain_edge_candidates(edges)
        supplier_pairs = {(row["sup"], row["cust"]) for row in deduped if row.get("etype") == "SUPPLIER_OF"}

        self.assertEqual(
            {
                ("AMRX", "CAH"),
                ("HWM", "GE"),
                ("CALM", "WMT"),
                ("SITM", "ARW"),
                ("HLMN", "HD"),
                ("AMWD", "HD"),
                ("JBSS", "WMT"),
                ("MBUU", "ONEW"),
            },
            supplier_pairs,
        )
        self.assertTrue(all("+direction-resolved" in row["src"] for row in deduped if (row["sup"], row["cust"]) in {("AMRX", "CAH"), ("HWM", "GE"), ("CALM", "WMT"), ("SITM", "ARW")}))
        self.assertFalse(any(pair in supplier_pairs for pair in {("AM", "AR"), ("AR", "AM"), ("AMAT", "ICHR"), ("ICHR", "AMAT"), ("DCH", "GM"), ("GM", "DCH"), ("ICHR", "LRCX"), ("LRCX", "ICHR")}))

    def test_apply_supply_chain_edges_dedupes_real_world_reciprocals_before_write(self):
        raw_edges = [
            {"sup": "AMRX", "cust": "CAH", "confidence": 1.0, "source": "10-K", "revenue_pct": 70.0, "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-02-27", "active_after": "2025-02-28"},
            {"sup": "CAH", "cust": "AMRX", "confidence": 0.85, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-02-27", "active_after": "2025-02-28"},
            {"sup": "HWM", "cust": "GE", "confidence": 1.0, "source": "10-K", "revenue_pct": 62.0, "edge_type": "SUPPLIER_OF", "last_confirmed": "2025-02-14", "active_after": "2025-02-14"},
            {"sup": "GE", "cust": "HWM", "confidence": 0.85, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2025-02-14", "active_after": "2025-02-14"},
            {"sup": "CALM", "cust": "WMT", "confidence": 1.0, "source": "10-K", "revenue_pct": 50.1, "edge_type": "SUPPLIER_OF", "last_confirmed": "2025-07-22", "active_after": "2025-07-22"},
            {"sup": "WMT", "cust": "CALM", "confidence": 0.85, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2025-07-22", "active_after": "2025-07-22"},
            {"sup": "SITM", "cust": "ARW", "confidence": 1.0, "source": "10-K", "revenue_pct": 47.0, "edge_type": "SUPPLIER_OF", "last_confirmed": "2025-02-14", "active_after": "2025-02-14"},
            {"sup": "ARW", "cust": "SITM", "confidence": 0.85, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2025-02-14", "active_after": "2025-02-14"},
            {"sup": "AM", "cust": "AR", "confidence": 1.0, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-02-11", "active_after": "2025-02-12"},
            {"sup": "AR", "cust": "AM", "confidence": 1.0, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-02-11", "active_after": "2025-02-12"},
            {"sup": "AMAT", "cust": "ICHR", "confidence": 0.85, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-02-20", "active_after": "2025-02-21"},
            {"sup": "ICHR", "cust": "AMAT", "confidence": 0.85, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-02-20", "active_after": "2025-02-21"},
            {"sup": "DCH", "cust": "GM", "confidence": 1.0, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-02-13", "active_after": "2025-02-14"},
            {"sup": "GM", "cust": "DCH", "confidence": 1.0, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-02-13", "active_after": "2025-02-14"},
            {"sup": "ICHR", "cust": "LRCX", "confidence": 1.0, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-02-20", "active_after": "2025-02-21"},
            {"sup": "LRCX", "cust": "ICHR", "confidence": 1.0, "source": "10-K", "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-02-20", "active_after": "2025-02-21"},
            {"sup": "HLMN", "cust": "HD", "confidence": 1.0, "source": "10-K", "revenue_pct": 41.0, "edge_type": "SUPPLIER_OF", "last_confirmed": "2026-02-17", "active_after": "2025-02-20"},
            {"sup": "AMWD", "cust": "HD", "confidence": 1.0, "source": "10-K", "revenue_pct": 40.8, "edge_type": "SUPPLIER_OF", "last_confirmed": "2025-06-25", "active_after": "2025-06-25"},
            {"sup": "JBSS", "cust": "WMT", "confidence": 1.0, "source": "10-K", "revenue_pct": 40.0, "edge_type": "SUPPLIER_OF", "last_confirmed": "2025-08-20", "active_after": "2025-08-20"},
            {"sup": "MBUU", "cust": "ONEW", "confidence": 1.0, "source": "10-K", "revenue_pct": 39.4, "edge_type": "SUPPLIER_OF", "last_confirmed": "2025-12-15", "active_after": "2025-12-15"},
        ]

        class _FakeResult:
            def __init__(self, created=0):
                self._created = created

            def single(self):
                return {"created": self._created}

            def consume(self):
                return None

        class _FakeSession:
            def __init__(self):
                self.supplier_batches = []

            def run(self, query, **params):
                batch = list(params.get("batch") or [])
                if "MERGE (a)-[r:SUPPLIER_OF" in query:
                    self.supplier_batches.append(batch)
                    return _FakeResult(len(batch))
                return _FakeResult(0)

        session = _FakeSession()
        with patch.object(gns, "_sync_graph_edge_intervals", return_value=0), \
             patch.object(gns, "_reconcile_reciprocal_supply_chain_edges", return_value={"pairs": 0, "deleted_total": 0, "resolved_directional": 0, "dropped_ambiguous": 0}) as reconcile_mock, \
             patch.object(gns, "_purge_closed_supply_chain_direction_conflicts", return_value=0), \
             patch.object(gns, "_log"):
            written = gns.apply_supply_chain_edges(session, raw_edges)

        self.assertEqual(8, written)
        flattened = [row for batch in session.supplier_batches for row in batch]
        written_pairs = {(row["sup"], row["cust"]) for row in flattened}
        self.assertEqual(
            {
                ("AMRX", "CAH"),
                ("HWM", "GE"),
                ("CALM", "WMT"),
                ("SITM", "ARW"),
                ("HLMN", "HD"),
                ("AMWD", "HD"),
                ("JBSS", "WMT"),
                ("MBUU", "ONEW"),
            },
            written_pairs,
        )
        self.assertTrue(all("+direction-resolved" in row["src"] for row in flattened if (row["sup"], row["cust"]) in {("AMRX", "CAH"), ("HWM", "GE"), ("CALM", "WMT"), ("SITM", "ARW")}))
        self.assertFalse(any(pair in written_pairs for pair in {("AM", "AR"), ("AR", "AM"), ("AMAT", "ICHR"), ("ICHR", "AMAT"), ("DCH", "GM"), ("GM", "DCH"), ("ICHR", "LRCX"), ("LRCX", "ICHR")}))
        reconcile_mock.assert_called_once()

    def test_phase3_purge_closed_direction_conflicts_batches_real_world_pairs(self):
        class _FakeResult:
            def __init__(self, deleted):
                self._deleted = deleted

            def single(self):
                return {"deleted": self._deleted}

        class _FakeSession:
            def __init__(self):
                self.calls = []
                self.deleted_counts = [2, 2, 0]

            def run(self, query, **params):
                self.calls.append((query, params))
                return _FakeResult(self.deleted_counts.pop(0))

        session = _FakeSession()
        deleted = gns._purge_closed_supply_chain_direction_conflicts(
            session,
            tickers=["AM", "AR", "AMAT", "ICHR", "DCH", "GM", "LRCX"],
            batch_limit=2,
        )

        self.assertEqual(4, deleted)
        self.assertEqual(3, len(session.calls))
        for query, params in session.calls:
            self.assertIn("edge_state: 'closed'", query)
            self.assertIn("direction_conflict", query)
            self.assertEqual(2, params["batch_limit"])
            self.assertIn("AMAT", params["tickers"])

    def test_phase3_reconcile_reciprocal_supply_chain_edges_scopes_to_open_real_world_10k_pairs(self):
        class _FakeSession:
            def __init__(self):
                self.calls = []

            def run(self, query, **params):
                self.calls.append((query, params))
                return []

        session = _FakeSession()
        result = gns._reconcile_reciprocal_supply_chain_edges(
            session,
            tickers=["AM", "AR", "AMAT", "ICHR", "DCH", "GM", "LRCX"],
        )

        self.assertEqual(
            {"pairs": 0, "deleted_total": 0, "resolved_directional": 0, "dropped_ambiguous": 0},
            result,
        )
        self.assertEqual(1, len(session.calls))
        query, params = session.calls[0]
        self.assertIn("SUPPLIER_OF {source_scope: 'SEC_10K_SUPPLIER', edge_state: 'open'}", query)
        self.assertIn("a.ticker < b.ticker", query)
        self.assertIn("AMAT", params["tickers"])
        self.assertIn("ICHR", params["tickers"])
        self.assertIn("DCH", params["tickers"])
        self.assertIn("GM", params["tickers"])
        self.assertIn("LRCX", params["tickers"])

    def test_phase3_live_supply_chain_counts_use_real_world_sec_10k_scopes(self):
        supplier_pairs = [
            ("CVEO", "SU"),
            ("ITGR", "NPCE"),
            ("JAKK", "WMT"),
            ("CRWV", "META"),
            ("SUPN", "CAH"),
            ("LASR", "NOC"),
            ("DGX", "FLGT"),
            ("CLS", "DELL"),
            ("DK", "DKL"),
            ("CLS", "META"),
            ("CLS", "CIEN"),
            ("MYGN", "FLGT"),
        ]
        partner_pairs = [
            ("AA", "HWM"),
            ("AAPL", "MP"),
            ("AAPL", "SNX"),
            ("AARD", "GRI"),
            ("ABBV", "XLO"),
            ("ABCL", "LLY"),
            ("ABEO", "TSHA"),
            ("ABT", "ENTA"),
            ("ABT", "SENS"),
            ("ABUS", "ALNY"),
        ]

        class _FakeResult:
            def __init__(self, payload):
                self.payload = payload

            def single(self):
                return self.payload

        class _FakeSession:
            def __init__(self):
                self.calls = []

            def run(self, query, **params):
                self.calls.append((query, params))
                if "SUPPLIER_OF" in query:
                    return _FakeResult({"cnt": len(supplier_pairs)})
                return _FakeResult({"cnt": len(partner_pairs)})

        session = _FakeSession()
        counts = gns._phase3_live_supply_chain_counts(session)

        self.assertEqual(len(supplier_pairs), counts["supplier_edges"])
        self.assertEqual(len(partner_pairs), counts["partner_edges"])
        self.assertEqual(2, len(session.calls))
        self.assertIn("SEC_10K_SUPPLIER", session.calls[0][1]["source_scope"])
        self.assertEqual("SEC_10K_STRATEGIC_PARTNER", session.calls[1][1]["source_scope"])

    def test_phase3_incremental_summary_reports_live_counts_instead_of_zero(self):
        supplier_pairs = [
            ("CVEO", "SU"),
            ("ITGR", "NPCE"),
            ("JAKK", "WMT"),
            ("CRWV", "META"),
            ("SUPN", "CAH"),
            ("LASR", "NOC"),
            ("DGX", "FLGT"),
            ("CLS", "DELL"),
            ("DK", "DKL"),
            ("CLS", "META"),
            ("CLS", "CIEN"),
            ("MYGN", "FLGT"),
        ]
        partner_pairs = [
            ("AA", "HWM"),
            ("AAPL", "MP"),
            ("AAPL", "SNX"),
            ("AARD", "GRI"),
            ("ABBV", "XLO"),
            ("ABCL", "LLY"),
            ("ABEO", "TSHA"),
            ("ABT", "ENTA"),
            ("ABT", "SENS"),
            ("ABUS", "ALNY"),
        ]

        class _FakeResult:
            def __init__(self, payload=None):
                self.payload = payload or {}

            def single(self):
                return self.payload

            def consume(self):
                return self

        class _FakeSession:
            def run(self, query, **params):
                if "SUPPLIER_OF" in query and "RETURN count(r) AS cnt" in query:
                    return _FakeResult({"cnt": len(supplier_pairs)})
                if "STRATEGIC_PARTNER" in query and "RETURN count(r) AS cnt" in query:
                    return _FakeResult({"cnt": len(partner_pairs)})
                return _FakeResult({})

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "supply.csv")
            with open(csv_path, "w", encoding="utf-8") as handle:
                handle.write("sup,cust,confidence,source,edge_type,last_confirmed,active_after\n")
                handle.write("CVEO,SU,1.0,10-K,SUPPLIER_OF,2026-03-06,2025-02-04\n")

            with patch.object(gns, "_get_supply_chain_csv_path", return_value=(csv_path, True, {"end_date": "2026-03-10", "latest_filing_date": "2026-03-06", "filings_processed": 12, "edge_count": 12})), \
                 patch.object(gns, "_nexus_historical_start_date", return_value="2025-01-01"), \
                 patch.object(gns, "_nexus_phase_manifest", return_value={"coverage_start": "2025-01-01"}), \
                 patch.object(gns, "_prune_invalid_supply_chain_edges", return_value=0), \
                 patch.object(gns, "_reconcile_reciprocal_supply_chain_edges", return_value={"pairs": 0, "deleted_total": 0, "resolved_directional": 0, "dropped_ambiguous": 0}), \
                 patch.object(gns, "_sync_graph_edge_intervals"), \
                 patch.object(gns, "_purge_closed_supply_chain_direction_conflicts", return_value=0), \
                 patch.object(gns, "_retire_relationships", return_value=0), \
                 patch.object(gns, "_nexus_update_phase_manifest"), \
                 patch.object(gns, "_log") as log_mock:
                gns.phase3_supply_chain(_FakeSession())

        log_messages = [call.args[0] for call in log_mock.call_args_list if call.args]
        self.assertTrue(any("12 SUPPLIER_OF and 10 STRATEGIC_PARTNER edges" in msg for msg in log_messages))
        self.assertTrue(any("Phase 3 done: SEC 10-K supply chain refresh completed from incremental scrape" in msg for msg in log_messages))

    def test_phase5_bea_commodity_filters_real_world_rows(self):
        cases = [
            ({"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "435551"}, True, True),
            ({"RowCode": "21", "RowDescr": "Mining", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "460202"}, True, True),
            ({"RowCode": "22", "RowDescr": "Utilities", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "75030"}, True, True),
            ({"RowCode": "FIRE", "RowDescr": "Finance, insurance, real estate, rental, and leasing", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "168223"}, True, True),
            ({"RowCode": "G", "RowDescr": "Government", "ColCode": "G", "ColDescr": "Government", "DataValue": "18781"}, True, True),
            ({"RowCode": "Other", "RowDescr": "Noncomparable imports and rest-of-the-world adjustment", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "17163"}, False, True),
            ({"RowCode": "Used", "RowDescr": "Scrap, used and secondhand goods", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "54280"}, False, True),
            ({"RowCode": "V001", "RowDescr": "Compensation of employees", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "1359135"}, False, True),
            ({"RowCode": "T005", "RowDescr": "Total Intermediate", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "4253881"}, False, True),
            ({"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "F010", "ColDescr": "Personal consumption expenditures", "DataValue": "260923"}, True, False),
            ({"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "F030", "ColDescr": "Change in private inventories", "DataValue": "745"}, True, False),
            ({"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "F040", "ColDescr": "Exports of goods and services", "DataValue": "77384"}, True, False),
            ({"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "T001", "ColDescr": "Total Intermediate", "DataValue": "663230"}, True, False),
            ({"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "T019", "ColDescr": "Total use of products", "DataValue": "1002282"}, True, False),
        ]

        for row, expected_row_ok, expected_col_ok in cases:
            with self.subTest(row_code=row["RowCode"], col_code=row["ColCode"]):
                self.assertEqual(expected_row_ok, gns._phase5_bea_is_commodity_row(row))
                self.assertEqual(expected_col_ok, gns._phase5_bea_is_sector_column(row))

    def test_phase5_build_bea_commodity_exposure_batch_uses_real_world_bea_rows(self):
        rows = [
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "23", "ColDescr": "Construction", "DataValue": "3493", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "435551", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "FIRE", "ColDescr": "Finance, insurance, real estate, rental, and leasing", "DataValue": "2", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "G", "ColDescr": "Government", "DataValue": "13519", "_source_year": "2024"},
            {"RowCode": "21", "RowDescr": "Mining", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "460202", "_source_year": "2024"},
            {"RowCode": "22", "RowDescr": "Utilities", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "75030", "_source_year": "2024"},
            {"RowCode": "42", "RowDescr": "Wholesale trade", "ColCode": "44RT", "ColDescr": "Retail trade", "DataValue": "22683", "_source_year": "2024"},
            {"RowCode": "48TW", "RowDescr": "Transportation and warehousing", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "33703", "_source_year": "2024"},
            {"RowCode": "51", "RowDescr": "Information", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "26838", "_source_year": "2024"},
            {"RowCode": "FIRE", "RowDescr": "Finance, insurance, real estate, rental, and leasing", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "168223", "_source_year": "2024"},
            {"RowCode": "PROF", "RowDescr": "Professional and business services", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "281316", "_source_year": "2024"},
            {"RowCode": "7", "RowDescr": "Arts, entertainment, recreation, accommodation, and food services", "ColCode": "44RT", "ColDescr": "Retail trade", "DataValue": "14018", "_source_year": "2024"},
            {"RowCode": "Other", "RowDescr": "Noncomparable imports and rest-of-the-world adjustment", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "17163", "_source_year": "2024"},
            {"RowCode": "Used", "RowDescr": "Scrap, used and secondhand goods", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "54280", "_source_year": "2024"},
            {"RowCode": "V001", "RowDescr": "Compensation of employees", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "1359135", "_source_year": "2024"},
            {"RowCode": "T005", "RowDescr": "Total Intermediate", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "4253881", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "F010", "ColDescr": "Personal consumption expenditures", "DataValue": "260923", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "T001", "ColDescr": "Total Intermediate", "DataValue": "663230", "_source_year": "2024"},
        ]

        sector_map = {
            "construction": "Industrials",
            "manufacturing": "Industrials",
            "finance, insurance, real estate, rental, and leasing": "Financials",
            "government": "Government",
            "retail trade": "Consumer Discretionary",
        }

        def _mapper(name: str) -> str:
            return sector_map.get(name.lower(), "Other")

        commodity_nodes, exposures, stats = gns._phase5_build_bea_commodity_exposure_batch(rows, _mapper)

        commodity_ids = {row["id"] for row in commodity_nodes}
        exposure_map = {(row["sector"], row["id"]): row for row in exposures}

        self.assertIn("bea_io_commodity:11", commodity_ids)
        self.assertIn("bea_io_commodity:21", commodity_ids)
        self.assertIn("bea_io_commodity:FIRE", commodity_ids)
        self.assertNotIn("bea_io_commodity:Other", commodity_ids)
        self.assertNotIn("bea_io_commodity:Used", commodity_ids)
        self.assertEqual(439044.0, exposure_map[("Industrials", "bea_io_commodity:11")]["val"])
        self.assertEqual(2.0, exposure_map[("Financials", "bea_io_commodity:11")]["val"])
        self.assertEqual(13519.0, exposure_map[("Government", "bea_io_commodity:11")]["val"])
        self.assertEqual(460202.0, exposure_map[("Industrials", "bea_io_commodity:21")]["val"])
        self.assertEqual(22683.0, exposure_map[("Consumer Discretionary", "bea_io_commodity:42")]["val"])
        self.assertEqual(168223.0, exposure_map[("Industrials", "bea_io_commodity:FIRE")]["val"])
        self.assertEqual(281316.0, exposure_map[("Industrials", "bea_io_commodity:PROF")]["val"])
        self.assertEqual("2024-12-31", exposure_map[("Industrials", "bea_io_commodity:11")]["active_after"])
        self.assertEqual(4, stats["skipped_non_commodity_rows"])
        self.assertEqual(2, stats["skipped_non_sector_columns"])
        self.assertEqual(0, stats["skipped_other_sector"])

    def test_phase5_build_bea_commodity_exposure_batch_sums_real_world_duplicate_sector_rollups(self):
        rows = [
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "23", "ColDescr": "Construction", "DataValue": "3493", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "435551", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "42", "ColDescr": "Wholesale trade", "DataValue": "1174", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "PROF", "ColDescr": "Professional and business services", "DataValue": "3060", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "FIRE", "ColDescr": "Finance, insurance, real estate, rental, and leasing", "DataValue": "2", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "G", "ColDescr": "Government", "DataValue": "13519", "_source_year": "2024"},
        ]

        def _mapper(name: str) -> str:
            lowered = name.lower()
            if lowered in {"construction", "manufacturing", "wholesale trade", "professional and business services"}:
                return "Industrials"
            if lowered == "finance, insurance, real estate, rental, and leasing":
                return "Financials"
            if lowered == "government":
                return "Government"
            return "Other"

        _, exposures, _ = gns._phase5_build_bea_commodity_exposure_batch(rows, _mapper)
        exposure_map = {(row["sector"], row["id"]): row["val"] for row in exposures}
        self.assertEqual(443278.0, exposure_map[("Industrials", "bea_io_commodity:11")])
        self.assertEqual(2.0, exposure_map[("Financials", "bea_io_commodity:11")])
        self.assertEqual(13519.0, exposure_map[("Government", "bea_io_commodity:11")])

    def test_phase5_build_sector_supply_batch_filters_final_demand_and_sums_real_world_pairs(self):
        rows = [
            {"RowCode": "113FF", "RowDescr": "Forestry, fishing, and related activities", "ColCode": "561", "ColDescr": "Administrative and support services", "DataValue": "6", "_source_year": "2024"},
            {"RowCode": "113FF", "RowDescr": "Forestry, fishing, and related activities", "ColCode": "55", "ColDescr": "Management of companies and enterprises", "DataValue": "27", "_source_year": "2024"},
            {"RowCode": "113FF", "RowDescr": "Forestry, fishing, and related activities", "ColCode": "5419", "ColDescr": "Miscellaneous professional, scientific, and technical services", "DataValue": "246", "_source_year": "2024"},
            {"RowCode": "111CA", "RowDescr": "Farms", "ColCode": "T019", "ColDescr": "Total use of products", "DataValue": "883884", "_source_year": "2024"},
            {"RowCode": "111CA", "RowDescr": "Farms", "ColCode": "F040", "ColDescr": "Exports of goods and services", "DataValue": "70632", "_source_year": "2024"},
            {"RowCode": "111CA", "RowDescr": "Farms", "ColCode": "F030", "ColDescr": "Change in private inventories", "DataValue": "281", "_source_year": "2024"},
            {"RowCode": "111CA", "RowDescr": "Farms", "ColCode": "F010", "ColDescr": "Personal consumption expenditures", "DataValue": "247082", "_source_year": "2024"},
            {"RowCode": "GSLG", "RowDescr": "State and local general government", "ColCode": "F10C", "ColDescr": "State and local government consumption expenditures", "DataValue": "2550362", "_source_year": "2024"},
            {"RowCode": "111CA", "RowDescr": "Farms", "ColCode": "81", "ColDescr": "Other services, except government", "DataValue": "14", "_source_year": "2024"},
            {"RowCode": "111CA", "RowDescr": "Farms", "ColCode": "T001", "ColDescr": "Total Intermediate", "DataValue": "565889", "_source_year": "2024"},
        ]

        def _mapper(name: str) -> str:
            lowered = " ".join(name.lower().split())
            if lowered == "other services, except government":
                return "Other"
            mapping = {
                "forestry": "Agriculture",
                "fishing": "Agriculture",
                "administrative and support": "Industrials",
                "management": "Industrials",
                "professional": "Industrials",
                "scientific": "Technology",
                "technical": "Technology",
                "government": "Government",
            }
            for keyword, sector in mapping.items():
                if keyword in lowered:
                    return sector
            return "Other"

        batch, skipped = gns._phase5_build_sector_supply_batch(rows, _mapper)
        self.assertEqual(7, skipped)
        self.assertEqual(
            [{"from_s": "Agriculture", "to_s": "Industrials", "val": 279.0, "active_after": "2024-12-31"}],
            batch,
        )

    def test_phase5_macro_builds_bea_commodity_edges_from_online_rows_without_csv(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []
                self.commodity_batch = None
                self.exposure_batch = None

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                if "MERGE (m:Commodity {id: p.id})" in query:
                    self.commodity_batch = list(kwargs.get("batch") or [])
                if "MERGE (sec)-[r:EXPOSED_TO {source_scope: 'BEA_IO_COMMODITY'" in query:
                    self.exposure_batch = list(kwargs.get("batch") or [])
                return _FakeResult(query, kwargs)

        commodity_rows = [
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "23", "ColDescr": "Construction", "DataValue": "3493", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "435551", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "FIRE", "ColDescr": "Finance, insurance, real estate, rental, and leasing", "DataValue": "2", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "G", "ColDescr": "Government", "DataValue": "13519", "_source_year": "2024"},
            {"RowCode": "21", "RowDescr": "Mining", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "460202", "_source_year": "2024"},
            {"RowCode": "22", "RowDescr": "Utilities", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "75030", "_source_year": "2024"},
            {"RowCode": "42", "RowDescr": "Wholesale trade", "ColCode": "44RT", "ColDescr": "Retail trade", "DataValue": "22683", "_source_year": "2024"},
            {"RowCode": "48TW", "RowDescr": "Transportation and warehousing", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "33703", "_source_year": "2024"},
            {"RowCode": "51", "RowDescr": "Information", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "26838", "_source_year": "2024"},
            {"RowCode": "FIRE", "RowDescr": "Finance, insurance, real estate, rental, and leasing", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "168223", "_source_year": "2024"},
            {"RowCode": "PROF", "RowDescr": "Professional and business services", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "281316", "_source_year": "2024"},
            {"RowCode": "7", "RowDescr": "Arts, entertainment, recreation, accommodation, and food services", "ColCode": "44RT", "ColDescr": "Retail trade", "DataValue": "14018", "_source_year": "2024"},
            {"RowCode": "Other", "RowDescr": "Noncomparable imports and rest-of-the-world adjustment", "ColCode": "31G", "ColDescr": "Manufacturing", "DataValue": "17163", "_source_year": "2024"},
            {"RowCode": "11", "RowDescr": "Agriculture, forestry, fishing, and hunting", "ColCode": "F010", "ColDescr": "Personal consumption expenditures", "DataValue": "260923", "_source_year": "2024"},
        ]

        fake_session = _FakeSession()
        with patch.object(gns, "BEA_API_KEY", "test-key"), \
             patch.object(gns, "_fetch_bea_io_commodity_table", return_value=commodity_rows), \
             patch.object(gns, "_fetch_bea_io_table", return_value=[]), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress"), \
             patch.object(gns, "_log"), \
             patch.object(gns, "_sync_graph_edge_intervals") as sync_mock, \
             patch.object(gns, "_retire_relationships", return_value=0):
            gns.phase5_macro(fake_session)

        self.assertIsNotNone(fake_session.commodity_batch)
        self.assertIsNotNone(fake_session.exposure_batch)
        self.assertIn({"id": "bea_io_commodity:11", "name": "Agriculture, forestry, fishing, and hunting", "source_code": "11", "active_after": "2024-12-31"}, fake_session.commodity_batch)
        self.assertIn({"sector": "Industrials", "id": "bea_io_commodity:11", "val": 439044.0, "active_after": "2024-12-31"}, fake_session.exposure_batch)
        self.assertIn({"sector": "Financials", "id": "bea_io_commodity:11", "val": 2.0, "active_after": "2024-12-31"}, fake_session.exposure_batch)
        self.assertIn({"sector": "Government", "id": "bea_io_commodity:11", "val": 13519.0, "active_after": "2024-12-31"}, fake_session.exposure_batch)
        self.assertNotIn({"sector": "Industrials", "id": "bea_io_commodity:Other", "val": 17163.0, "active_after": "2024-12-31"}, fake_session.exposure_batch)
        self.assertFalse(any(row["id"] == "bea_io_commodity:Other" for row in fake_session.commodity_batch))
        sync_mock.assert_any_call(fake_session, "EXPOSED_TO", source_scope="BEA_IO_COMMODITY", directed=True)

    def test_phase9_wikidata_stage_skips_cleanly_when_disabled(self):
        class _FailSession:
            def run(self, *args, **kwargs):
                raise AssertionError("Wikidata stage should not query Neo4j when disabled")

        progress_messages = []
        log_messages = []

        with patch.object(gns, "GRAPH_NEXUS_WIKIDATA_PHASE_ENABLED", False), \
             patch.object(gns, "_nexus_stage_reset"), \
             patch.object(gns, "_progress", side_effect=lambda pct, msg, color=None: progress_messages.append((pct, msg, color))), \
             patch.object(gns, "_log", side_effect=lambda msg, color=None: log_messages.append((msg, color))):
            gns.phase10_wikidata(_FailSession())

        self.assertEqual(
            [
                (gns.PHASE_PCT[11], "Phase 9: Wikidata corporate hierarchy relationships...", "cyan"),
                (gns.PHASE_PCT[12], "Phase 9 skipped (temporarily disabled).", "green"),
            ],
            progress_messages,
        )
        self.assertIn(
            ("Phase 9: Wikidata is temporarily disabled; skipping stage.", "yellow"),
            log_messages,
        )

    def test_parse_10k_drops_low_confidence_partial_name_without_direct_anchor(self):
        html = (
            "<html><body>"
            "Our significant customer concentration remained elevated during the year. "
            "Separately, we discussed Reynolds in a generic packaging market update."
            "</body></html>"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
            tmp.write(html)
            tmp_path = tmp.name
        try:
            with patch.object(sec, "_extract_ctx_candidate_names", return_value=["Reynolds"]), patch.object(
                sec,
                "_resolve_customer_to_ticker",
                return_value=("REYN", 0.85),
            ):
                edges = sec._parse_10k_and_extract_relationships(
                    "JAKK",
                    tmp_path,
                    title_to_ticker={"reynolds consumer products": "REYN"},
                    ticker_to_title={
                        "JAKK": "JAKKS Pacific, Inc.",
                        "REYN": "Reynolds Consumer Products Inc.",
                    },
                    filing_date="2026-03-02",
                )
            self.assertEqual([], edges)
        finally:
            os.unlink(tmp_path)

    def test_parse_10k_keeps_low_confidence_partial_name_when_revenue_pct_confirms_it(self):
        html = (
            "<html><body>"
            "Cardinal Holdings accounted for 19% of our net revenues, making it our significant customer."
            "</body></html>"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as tmp:
            tmp.write(html)
            tmp_path = tmp.name
        try:
            with patch.object(sec, "_extract_ctx_candidate_names", return_value=["Cardinal Holdings"]), patch.object(
                sec,
                "_resolve_customer_to_ticker",
                return_value=("CAH", 0.85),
            ):
                edges = sec._parse_10k_and_extract_relationships(
                    "AMPH",
                    tmp_path,
                    title_to_ticker={"cardinal health": "CAH"},
                    ticker_to_title={
                        "AMPH": "Amphastar Pharmaceuticals, Inc.",
                        "CAH": "Cardinal Health, Inc.",
                    },
                    filing_date="2026-03-06",
                )
            self.assertEqual(1, len(edges))
            self.assertEqual(("AMPH", "CAH"), (edges[0]["sup"], edges[0]["cust"]))
            self.assertEqual("2026-03-06", edges[0]["active_after"])
        finally:
            os.unlink(tmp_path)

    def test_normalize_supply_chain_row_preserves_active_after(self):
        parsed = gns._normalize_supply_chain_row({
            "sup": "MSFT",
            "cust": "AAPL",
            "confidence": "0.95",
            "last_confirmed": "2025-10-10",
            "active_after": "2025-10-09",
        })
        self.assertIsNotNone(parsed)
        _, _, meta = parsed
        self.assertEqual("2025-10-10", meta["last_confirmed"])
        self.assertEqual("2025-10-09", meta["active_after"])

    def test_normalize_supply_chain_row_falls_back_active_after_to_last_confirmed(self):
        parsed = gns._normalize_supply_chain_row({
            "sup": "MSFT",
            "cust": "AAPL",
            "last_confirmed": "2025-10-10",
        })
        self.assertIsNotNone(parsed)
        _, _, meta = parsed
        self.assertEqual("2025-10-10", meta["active_after"])

    def test_legacy_parsed_edge_cache_is_invalidated(self):
        original_dir = sec._PARSED_EDGES_CACHE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                sec._PARSED_EDGES_CACHE_DIR = tmp
                legacy_path = sec._parsed_edges_cache_path("AAT")
                os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
                with open(legacy_path, "w", encoding="utf-8") as f:
                    f.write('[{"sup":"AAT","cust":"ADSK","source":"10-K"}]')
                self.assertIsNone(sec._load_parsed_edges("AAT"))
        finally:
            sec._PARSED_EDGES_CACHE_DIR = original_dir

    def test_versioned_empty_parsed_edge_cache_is_reused(self):
        original_dir = sec._PARSED_EDGES_CACHE_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                sec._PARSED_EDGES_CACHE_DIR = tmp
                sec._save_parsed_edges("AAT", [])
                self.assertEqual([], sec._load_parsed_edges("AAT"))
        finally:
            sec._PARSED_EDGES_CACHE_DIR = original_dir

    def test_scrape_8k_agreements_uses_ticker_title_map_without_nameerror(self):
        class _Resp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "filings": {
                        "recent": {
                            "form": [],
                            "accessionNumber": [],
                            "primaryDocument": [],
                            "filingDate": [],
                            "items": [],
                        }
                    }
                }

        with patch.object(
            sec,
            "_fetch_sec_company_tickers",
            return_value=({"TEST": "Test Corp"}, {"test corp": "TEST"}, {"0000000001": "TEST"}),
        ), patch("requests.get", return_value=_Resp()), patch("time.sleep", return_value=None):
            edges = sec.scrape_8k_agreements(["TEST"], {"test corp": "TEST"}, hours=24)
        self.assertEqual([], edges)

    def test_resolved_8k_counterparty_supported_rejects_mismatched_resolution(self):
        ctx = "The Company entered into a supply agreement with Tarsus Pharmaceuticals, Inc. on March 1, 2026."
        self.assertFalse(
            sec._resolved_8k_counterparty_supported(
                ctx,
                "Tarsus Pharmaceuticals, Inc.",
                "ROAD",
                {"ROAD": "Construction Partners, Inc."},
            )
        )
        self.assertTrue(
            sec._resolved_8k_counterparty_supported(
                ctx,
                "Tarsus Pharmaceuticals, Inc.",
                "TARS",
                {"TARS": "Tarsus Pharmaceuticals, Inc."},
            )
        )

    def test_phase13_etf_universe_writes_temporal_source_scoped_edges(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS created" in self.query:
                    key = "pairs" if "pairs" in self.kwargs else "params"
                    return {"created": len(self.kwargs.get(key) or [])}
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        fake_session = _FakeSession()

        def _cache_side_effect(path, max_age):
            if path.endswith("etf_list_v3.json"):
                return [{
                    "ticker": "XLK",
                    "name": "Technology Select Sector SPDR Fund",
                    "category": "Technology",
                    "keywords": ["technology"],
                    "primary_theme": "Technology",
                    "themes": ["Technology"],
                    "asset_class": "Equity",
                }]
            if path.endswith("etf_holdings.json"):
                return {"XLK": [{"holding_ticker": "AAPL", "weight_pct": 10.0}]}
            return None

        with patch.object(gns, "_nexus_read_cached_json", side_effect=_cache_side_effect), \
             patch.object(gns, "_select_key_etfs_dynamic", return_value={"XLK"}), \
             patch.object(gns, "_sync_graph_edge_intervals") as mock_sync, \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "POLYGON_API_KEY", ""):
            gns.phase13_etf_universe(fake_session)

        joined_queries = "\n".join(query for query, _ in fake_session.queries)
        self.assertIn("source_scope: 'ETF_UNIVERSE_CLASSIFICATION'", joined_queries)
        self.assertIn("source_scope: 'ETF_UNIVERSE_THEME'", joined_queries)
        self.assertIn("source_scope: 'ETF_UNIVERSE_HOLDINGS'", joined_queries)
        mock_sync.assert_any_call(fake_session, "ETF_TRACKS_SECTOR", source_scope="ETF_UNIVERSE_CLASSIFICATION", directed=True)
        mock_sync.assert_any_call(fake_session, "ETF_TRACKS_THEME", source_scope="ETF_UNIVERSE_THEME", directed=True)
        mock_sync.assert_any_call(fake_session, "ETF_HOLDS", source_scope="ETF_UNIVERSE_HOLDINGS", directed=True)

    def test_fetch_etf_list_etfdb_uses_total_records_when_limit_unset(self):
        class _Resp:
            def __init__(self, payload):
                self.status_code = 200
                self._payload = payload

            def json(self):
                return self._payload

        def _page_payload(page: int, total: int, count: int) -> dict:
            start = (page - 1) * 250
            records = [
                {
                    "symbol": {"text": f"ETF{start + idx + 1:04d}"},
                    "name": {"text": f"ETF {start + idx + 1}"},
                    "category": {"text": "Technology"},
                    "asset_class": {"text": "Equity"},
                }
                for idx in range(count)
            ]
            return {"meta": {"total_records": total}, "data": records}

        responses = {
            1: _Resp(_page_payload(1, 550, 250)),
            2: _Resp(_page_payload(2, 550, 250)),
            3: _Resp(_page_payload(3, 550, 50)),
        }

        def _fake_post(url, json=None, headers=None, timeout=None):
            return responses[int((json or {}).get("page") or 1)]

        with patch("requests.post", side_effect=_fake_post), \
             patch("time.sleep", return_value=None), \
             patch.object(gns, "_log"):
            etfs, total = gns._fetch_etf_list_etfdb_with_total(max_etfs=0)

        self.assertEqual(550, len(etfs))
        self.assertEqual(550, total)
        self.assertEqual("ETF0001", etfs[0]["ticker"])
        self.assertEqual("ETF0550", etfs[-1]["ticker"])

    def test_merge_etf_universe_rows_prefers_primary_metadata_and_backfills_missing_tickers(self):
        merged = gns._merge_etf_universe_rows(
            [
                {"ticker": "AAA", "name": "ETF AAA", "category": "Technology", "asset_class": "Equity"},
                {"ticker": "BBB", "name": "ETF BBB", "category": "Energy", "asset_class": "Equity"},
            ],
            [
                {"ticker": "AAA", "name": "ETF AAA Polygon", "category": "", "asset_class": ""},
                {"ticker": "CCC", "name": "ETF CCC", "category": "", "asset_class": ""},
            ],
        )
        self.assertEqual(["AAA", "BBB", "CCC"], [row["ticker"] for row in merged])
        self.assertEqual("Technology", merged[0]["category"])
        self.assertEqual("ETF CCC", merged[2]["name"])

    def test_phase13_etf_universe_backfills_polygon_when_etfdb_shortfall(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS created" in self.query:
                    key = "params"
                    if "pairs" in self.kwargs:
                        key = "pairs"
                    return {"created": len(self.kwargs.get(key) or [])}
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        fake_session = _FakeSession()
        etfdb_rows = [
            {"ticker": "AAA", "name": "ETF AAA", "category": "Technology", "asset_class": "Equity"},
            {"ticker": "BBB", "name": "ETF BBB", "category": "Energy", "asset_class": "Equity"},
            {"ticker": "CCC", "name": "ETF CCC", "category": "Broad Market", "asset_class": "Equity"},
        ]
        polygon_rows = [
            {"ticker": "AAA", "name": "ETF AAA Polygon", "category": "", "asset_class": ""},
            {"ticker": "BBB", "name": "ETF BBB Polygon", "category": "", "asset_class": ""},
            {"ticker": "CCC", "name": "ETF CCC Polygon", "category": "", "asset_class": ""},
            {"ticker": "DDD", "name": "ETF DDD Polygon", "category": "", "asset_class": ""},
            {"ticker": "EEE", "name": "ETF EEE Polygon", "category": "", "asset_class": ""},
        ]
        cached_writes = {}

        def _write_cache(path, payload):
            cached_writes[path] = payload

        with patch.object(gns, "_nexus_read_cached_json", return_value=None), \
             patch.object(gns, "_fetch_etf_list_etfdb_with_total", return_value=(etfdb_rows, 5)), \
             patch.object(gns, "_fetch_etf_list_polygon", return_value=polygon_rows) as polygon_mock, \
             patch.object(gns, "_fetch_polygon_etf_details_batch", return_value={}), \
             patch.object(gns, "_select_key_etfs_dynamic", return_value=set()), \
             patch.object(gns, "_nexus_write_cached_json", side_effect=_write_cache), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "POLYGON_API_KEY", "test-key"), \
             patch.object(gns, "_log"):
            gns.phase13_etf_universe(fake_session)

        polygon_mock.assert_called_once()
        node_query_kwargs = next(kwargs for query, kwargs in fake_session.queries if "MERGE (e:ETF {ticker: p.ticker})" in query)
        self.assertEqual(5, len(node_query_kwargs["batch"]))

    def test_fetch_etf_list_polygon_unlimited_follows_next_url(self):
        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        base = "https://api.polygon.io/v3/reference/tickers"

        def _fake_get(url, params=None, headers=None, timeout=None):
            if url == base:
                return _Resp({
                    "results": [
                        {"ticker": "AAA", "name": "ETF AAA"},
                        {"ticker": "AAB", "name": "ETF AAB"},
                    ],
                    "next_url": "https://api.polygon.io/page/2",
                })
            if url == "https://api.polygon.io/page/2":
                return _Resp({
                    "results": [
                        {"ticker": "AAC", "name": "ETF AAC"},
                        {"ticker": "AAD", "name": "ETF AAD"},
                    ],
                    "next_url": "https://api.polygon.io/page/3",
                })
            return _Resp({
                "results": [{"ticker": "AAE", "name": "ETF AAE"}],
            })

        with patch("requests.get", side_effect=_fake_get), \
             patch.object(gns, "_polygon_rate_limit"), \
             patch.object(gns, "_log"):
            etfs = gns._fetch_etf_list_polygon("test-key", max_results=0)

        self.assertEqual(["AAA", "AAB", "AAC", "AAD", "AAE"], [row["ticker"] for row in etfs])

    def test_etf_classify_with_themes_promotes_oil_over_generic_commodities(self):
        category, keywords, primary_theme, themes = gns._classify_etf_with_themes(
            "Commodity Producers Equities",
            "United States Oil Fund LP",
        )
        self.assertEqual("Energy", category)
        self.assertEqual("Oil", primary_theme)
        self.assertIn("Oil", themes)
        self.assertIn("oil", [kw.lower() for kw in keywords])

    def test_etf_classify_with_themes_extracts_semiconductors(self):
        category, keywords, primary_theme, themes = gns._classify_etf_with_themes(
            "Technology Equities",
            "VanEck Semiconductor ETF",
        )
        self.assertEqual("Technology", category)
        self.assertEqual("Semiconductors", primary_theme)
        self.assertIn("Semiconductors", themes)
        self.assertIn("semiconductor", [kw.lower() for kw in keywords])

    def test_build_sector_kw_map_dynamic_filters_non_sector_categories(self):
        sector_map = gns._build_sector_kw_map_dynamic([
            {"ticker": "SPEU", "category": "International", "keywords": ["international"]},
            {"ticker": "SPDV", "category": "Dividends", "keywords": ["dividend", "income"]},
            {"ticker": "XLK", "category": "Technology", "keywords": ["technology"]},
        ])
        self.assertIn("Technology", sector_map)
        self.assertNotIn("International", sector_map)
        self.assertNotIn("Dividends", sector_map)

    def test_phase12_etf_sector_targets_real_world_cases(self):
        cases = [
            ({"ticker": "XLE", "name": "State Street Energy Select Sector SPDR ETF", "category": "Energy", "asset_class": "Equity"}, ["Energy"]),
            ({"ticker": "XLK", "name": "State Street Technology Select Sector SPDR ETF", "category": "Technology", "asset_class": "Equity"}, ["Technology"]),
            ({"ticker": "XLC", "name": "State Street Communication Services Select Sector SPDR ETF", "category": "Communications", "asset_class": "Equity"}, ["Communications"]),
            ({"ticker": "XLV", "name": "Health Care Select Sector SPDR Fund", "category": "Healthcare", "asset_class": "Equity"}, ["Healthcare"]),
            ({"ticker": "XLF", "name": "Financial Select Sector SPDR Fund", "category": "Financial Services", "asset_class": "Equity"}, ["Financial Services"]),
            ({"ticker": "XLU", "name": "Utilities Select Sector SPDR Fund", "category": "Utilities", "asset_class": "Equity"}, ["Utilities"]),
            ({"ticker": "XLY", "name": "Consumer Discretionary Select Sector SPDR Fund", "category": "Consumer Discretionary", "asset_class": "Equity"}, ["Consumer Discretionary"]),
            ({"ticker": "GDX", "name": "VanEck Gold Miners ETF", "category": "Materials", "asset_class": "Equity"}, ["Materials"]),
            ({"ticker": "ITA", "name": "iShares U.S. Aerospace & Defense ETF", "category": "Industrials", "asset_class": "Equity"}, ["Industrials"]),
            ({"ticker": "BND", "name": "Vanguard Total Bond Market ETF", "category": "Bonds", "asset_class": "Bond"}, []),
            ({"ticker": "AGG", "name": "iShares Core U.S. Aggregate Bond ETF", "category": "Bonds", "asset_class": "Bond"}, []),
            ({"ticker": "BNDX", "name": "Vanguard Total International Bond ETF", "category": "Bonds", "asset_class": "Bond"}, []),
            ({"ticker": "SGOV", "name": "iShares 0-3 Month Treasury Bond ETF", "category": "Bonds", "asset_class": "Bond"}, []),
            ({"ticker": "TLT", "name": "iShares 20+ Year Treasury Bond ETF", "category": "Bonds", "asset_class": "Bond"}, []),
            ({"ticker": "MUB", "name": "iShares National Muni Bond ETF", "category": "Bonds", "asset_class": "Bond"}, []),
            ({"ticker": "GLD", "name": "SPDR Gold Shares", "category": "Materials", "asset_class": "Commodity"}, []),
            ({"ticker": "IAU", "name": "iShares Gold Trust", "category": "Materials", "asset_class": "Commodity"}, []),
            ({"ticker": "SLV", "name": "iShares Silver Trust", "category": "Materials", "asset_class": "Commodity"}, []),
            ({"ticker": "IBIT", "name": "iShares Bitcoin Trust ETF", "category": "Broad Market", "asset_class": "Currency"}, []),
            ({"ticker": "VUG", "name": "Vanguard Growth ETF", "category": "Growth", "asset_class": "Equity"}, []),
            ({"ticker": "VTV", "name": "Vanguard Value ETF", "category": "Value", "asset_class": "Equity"}, []),
            ({"ticker": "SPY", "name": "State Street SPDR S&P 500 ETF", "category": "Broad Market", "asset_class": "Equity"}, []),
        ]
        for etf_row, expected in cases:
            with self.subTest(ticker=etf_row["ticker"]):
                self.assertEqual(expected, gns._phase12_etf_sector_targets(etf_row))

    def test_phase12_theme_classification_avoids_media_false_positives_for_real_world_bond_funds(self):
        real_world_bond_cases = [
            ("VCIT", "Corporate Bond", "Vanguard Intermediate-Term Corporate Bond ETF"),
            ("SCHR", "Treasury", "Schwab Intermediate-Term U.S. Treasury ETF"),
            ("VGIT", "Treasury", "Vanguard Intermediate-Term Treasury ETF"),
            ("SPIB", "Corporate Bond", "SPDR Portfolio Intermediate Term Corporate Bond ETF"),
            ("BIV", "Bond", "Vanguard Intermediate-Term Bond ETF"),
            ("MUNI", "Municipal Bond", "PIMCO Intermediate Municipal Bond Active ETF"),
            ("SPTI", "Treasury", "SPDR Portfolio Intermediate Term Treasury ETF"),
            ("VTEI", "Treasury", "Vanguard Intermediate-Term Tax-Exempt Bond ETF"),
            ("ITM", "Municipal Bond", "VanEck Intermediate Muni ETF"),
            ("GVI", "Bond", "iShares Intermediate Government/Credit Bond ETF"),
            ("INMU", "Municipal Bond", "BlackRock Intermediate Muni ETF"),
            ("CAM", "Bond", "Calamos Antetokounmpo Global Sustainable Equities Fund Intermediate Bond Sleeve"),
        ]

        for ticker, category_text, name_text in real_world_bond_cases:
            with self.subTest(ticker=ticker):
                category, keywords, primary_theme, themes = gns._classify_etf_with_themes(category_text, name_text)
                self.assertNotEqual("Media", primary_theme)
                self.assertNotIn("Media", themes)
                self.assertNotEqual("Communications", category)

    def test_phase12_sanitize_etf_classification_real_world_bond_cases(self):
        real_world_bond_cases = [
            ("VCIT", "Corporate Bond", "Vanguard Intermediate-Term Corporate Bond ETF", "Bond", "Communications", ["media", "bond"], "Media", ["Media", "Corporate Bonds"], "Corporate Bonds"),
            ("SCHR", "Treasury", "Schwab Intermediate-Term U.S. Treasury ETF", "Bond", "Communications", ["media", "treasury"], "Media", ["Media", "Treasuries"], "Treasuries"),
            ("VGIT", "Treasury", "Vanguard Intermediate-Term Treasury ETF", "Bond", "Communications", ["media", "treasury"], "Media", ["Media", "Treasuries"], "Treasuries"),
            ("SPIB", "Corporate Bond", "SPDR Portfolio Intermediate Term Corporate Bond ETF", "Bond", "Communications", ["media", "bond"], "Media", ["Media", "Corporate Bonds"], "Corporate Bonds"),
            ("BIV", "Bond", "Vanguard Intermediate-Term Bond ETF", "Bond", "Communications", ["media", "bond"], "Media", ["Media"], "Bonds"),
            ("MUNI", "Municipal Bond", "PIMCO Intermediate Municipal Bond Active ETF", "Bond", "Communications", ["media", "bond"], "Media", ["Media"], "Bonds"),
            ("SPTI", "Treasury", "SPDR Portfolio Intermediate Term Treasury ETF", "Bond", "Communications", ["media", "bond"], "Media", ["Media", "Treasuries"], "Treasuries"),
            ("VTEI", "Tax-Exempt Bond", "Vanguard Intermediate-Term Tax-Exempt Bond ETF", "Bond", "Communications", ["media", "bond"], "Media", ["Media"], "Bonds"),
            ("ITM", "Municipal Bond", "VanEck Intermediate Muni ETF", "Bond", "Communications", ["media", "muni"], "Media", ["Media"], "Bonds"),
            ("GVI", "Government Bond", "iShares Intermediate Government/Credit Bond ETF", "Bond", "Communications", ["media", "bond"], "Media", ["Media", "Government Bonds"], "Government Bonds"),
            ("INMU", "Municipal Bond", "BlackRock Intermediate Muni ETF", "Bond", "Communications", ["media", "muni"], "Media", ["Media"], "Bonds"),
            ("AGG", "Bond", "iShares Core U.S. Aggregate Bond ETF", "Bond", "Communications", ["media", "bond"], "Media", ["Media", "Corporate Bonds"], "Corporate Bonds"),
        ]

        for ticker, raw_category, name_text, asset_class, category, keywords, primary_theme, themes, expected_primary in real_world_bond_cases:
            with self.subTest(ticker=ticker):
                out_category, out_keywords, out_primary, out_themes = gns._phase12_sanitize_etf_classification(
                    raw_category,
                    name_text,
                    asset_class,
                    category,
                    keywords,
                    primary_theme,
                    themes,
                )
                self.assertEqual("Bonds", out_category)
                self.assertEqual(expected_primary, out_primary)
                self.assertNotIn("Media", out_themes)
                self.assertNotIn("media", [kw.lower() for kw in out_keywords])

    def test_phase12_theme_classification_preserves_real_world_communications_funds(self):
        communications_cases = [
            ("XLC", "Communication Services", "Communication Services Select Sector SPDR Fund", "Communications"),
            ("VOX", "Communication Services", "Vanguard Communication Services ETF", "Communications"),
            ("FCOM", "Communication Services", "Fidelity MSCI Communication Services Index ETF", "Communications"),
            ("IYZ", "Telecom", "iShares U.S. Telecommunications ETF", "Telecom"),
            ("IXP", "Communication Services", "iShares Global Communications Services ETF", "Communications"),
            ("PBS", "Media", "Invesco Dynamic Media ETF", "Media"),
            ("XTL", "Telecom", "SPDR S&P Telecom ETF", "Telecom"),
            ("SOCL", "Media", "Global X Social Media ETF", "Media"),
            ("FIVG", "Telecom", "Defiance Next Gen Connectivity ETF", "Telecom"),
            ("EWCO", "Communication Services", "Invesco S&P 500 Equal Weight Communication Services ETF", "Communications"),
        ]

        for ticker, category_text, name_text, expected_theme in communications_cases:
            with self.subTest(ticker=ticker):
                category, _keywords, primary_theme, themes = gns._classify_etf_with_themes(category_text, name_text)
                self.assertEqual("Communications", category)
                self.assertEqual(expected_theme, primary_theme)
                self.assertIn(expected_theme, themes)

    def test_phase13_etf_universe_theme_links_sanitize_real_world_bond_media_leaks(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS created" in self.query:
                    key = "pairs" if "pairs" in self.kwargs else "params"
                    return {"created": len(self.kwargs.get(key) or [])}
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []
                self.theme_pairs = None

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                if "MERGE (e)-[r:ETF_TRACKS_THEME" in query:
                    self.theme_pairs = list(kwargs.get("pairs") or [])
                return _FakeResult(query, kwargs)

        fake_session = _FakeSession()
        etf_rows = [
            {"ticker": "VCIT", "name": "Vanguard Intermediate-Term Corporate Bond ETF", "category": "Corporate Bond", "asset_class": "Bond"},
            {"ticker": "BIV", "name": "Vanguard Intermediate-Term Bond ETF", "category": "Bond", "asset_class": "Bond"},
            {"ticker": "MUNI", "name": "PIMCO Intermediate Municipal Bond Active ETF", "category": "Municipal Bond", "asset_class": "Bond"},
            {"ticker": "XLC", "name": "Communication Services Select Sector SPDR Fund", "category": "Communication Services", "asset_class": "Equity"},
            {"ticker": "VOX", "name": "Vanguard Communication Services ETF", "category": "Communication Services", "asset_class": "Equity"},
        ]

        def _cache_side_effect(path, max_age):
            if path.endswith("etf_list_v3.json"):
                return etf_rows
            if path.endswith("etf_holdings.json"):
                return {}
            return None

        with patch.object(gns, "_nexus_read_cached_json", side_effect=_cache_side_effect), \
             patch.object(gns, "_select_key_etfs_dynamic", return_value=set()), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "POLYGON_API_KEY", ""), \
             patch.object(gns, "_log"):
            gns.phase13_etf_universe(fake_session)

        self.assertIsNotNone(fake_session.theme_pairs)
        by_ticker = {}
        for row in fake_session.theme_pairs:
            by_ticker.setdefault(row["ticker"], []).append(row["theme"])
        self.assertIn("Corporate Bonds", by_ticker["VCIT"])
        self.assertNotIn("Media", by_ticker["VCIT"])
        self.assertIn("Bonds", by_ticker["BIV"])
        self.assertNotIn("Media", by_ticker["BIV"])
        self.assertIn("Bonds", by_ticker["MUNI"])
        self.assertNotIn("Media", by_ticker["MUNI"])
        self.assertIn("Communications", by_ticker["XLC"])
        self.assertIn("Communications", by_ticker["VOX"])

    def test_phase13_etf_universe_sector_links_only_sector_equity_funds(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS created" in self.query:
                    key = "pairs" if "pairs" in self.kwargs else "params"
                    return {"created": len(self.kwargs.get(key) or [])}
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []
                self.sector_pairs = None

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                if "MERGE (e)-[r:ETF_TRACKS_SECTOR" in query:
                    self.sector_pairs = list(kwargs.get("pairs") or [])
                return _FakeResult(query, kwargs)

        fake_session = _FakeSession()

        etf_rows = [
            {"ticker": "XLE", "name": "State Street Energy Select Sector SPDR ETF", "category": "Energy", "keywords": ["energy", "oil"], "primary_theme": "Energy", "themes": ["Energy"], "asset_class": "Equity"},
            {"ticker": "XLK", "name": "State Street Technology Select Sector SPDR ETF", "category": "Technology", "keywords": ["technology", "software"], "primary_theme": "Technology", "themes": ["Technology"], "asset_class": "Equity"},
            {"ticker": "XLC", "name": "State Street Communication Services Select Sector SPDR ETF", "category": "Communications", "keywords": ["communications"], "primary_theme": "Communications", "themes": ["Communications"], "asset_class": "Equity"},
            {"ticker": "GLD", "name": "SPDR Gold Shares", "category": "Materials", "keywords": ["gold", "precious metals"], "primary_theme": "Gold", "themes": ["Gold"], "asset_class": "Commodity"},
            {"ticker": "BND", "name": "Vanguard Total Bond Market ETF", "category": "Bonds", "keywords": ["bond", "fixed income"], "primary_theme": "Bonds", "themes": ["Bonds"], "asset_class": "Bond"},
            {"ticker": "IBIT", "name": "iShares Bitcoin Trust ETF", "category": "Broad Market", "keywords": ["broad market", "index"], "primary_theme": "Broad Market", "themes": [], "asset_class": "Currency"},
            {"ticker": "VUG", "name": "Vanguard Growth ETF", "category": "Growth", "keywords": ["growth"], "primary_theme": "Growth", "themes": ["Growth"], "asset_class": "Equity"},
        ]

        def _cache_side_effect(path, max_age):
            if path.endswith("etf_list_v3.json"):
                return etf_rows
            if path.endswith("etf_holdings.json"):
                return {"XLE": []}
            return None

        with patch.object(gns, "_nexus_read_cached_json", side_effect=_cache_side_effect), \
             patch.object(gns, "_select_key_etfs_dynamic", return_value=set()), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "POLYGON_API_KEY", ""), \
             patch.object(gns, "_log"):
            gns.phase13_etf_universe(fake_session)

        self.assertIsNotNone(fake_session.sector_pairs)
        self.assertEqual(
            [
                {"ticker": "XLC", "sector": "Communications", "snapshot_date": fake_session.sector_pairs[0]["snapshot_date"], "today": fake_session.sector_pairs[0]["today"]},
                {"ticker": "XLE", "sector": "Energy", "snapshot_date": fake_session.sector_pairs[0]["snapshot_date"], "today": fake_session.sector_pairs[0]["today"]},
                {"ticker": "XLK", "sector": "Technology", "snapshot_date": fake_session.sector_pairs[0]["snapshot_date"], "today": fake_session.sector_pairs[0]["today"]},
            ],
            fake_session.sector_pairs,
        )

    def test_phase12_prepare_etf_holding_params_dedupes_real_world_ivv_holdings(self):
        params = gns._phase12_prepare_etf_holding_params(
            "IVV",
            [
                {"holding_ticker": "AAPL", "weight_pct": 7.12},
                {"holding_ticker": "MSFT", "weight_pct": 6.45},
                {"holding_ticker": "NVDA", "weight_pct": 5.92},
                {"holding_ticker": "AMZN", "weight_pct": 3.85},
                {"holding_ticker": "META", "weight_pct": 2.48},
                {"holding_ticker": "GOOG", "weight_pct": 1.96},
                {"holding_ticker": "GOOGL", "weight_pct": 1.61},
                {"holding_ticker": "BRK.B", "weight_pct": 1.72},
                {"holding_ticker": "JPM", "weight_pct": 1.31},
                {"holding_ticker": "LLY", "weight_pct": 1.28},
                {"holding_ticker": "AVGO", "weight_pct": 1.17},
                {"holding_ticker": "XOM", "weight_pct": 0.97},
                {"holding_ticker": "AAPL", "weight_pct": 6.98},
                {"holding_ticker": "", "weight_pct": 0.5},
                {"holding_ticker": "CASH", "weight_pct": 0.0},
            ],
            snapshot_date="2026-03-15",
            today_iso="2026-03-15",
        )

        self.assertEqual(12, len(params))
        by_holding = {row["holding"]: row for row in params}
        self.assertEqual(7.12, by_holding["AAPL"]["weight"])
        self.assertEqual("IVV", by_holding["AAPL"]["etf"])
        self.assertEqual("2026-03-15", by_holding["MSFT"]["snapshot_date"])
        self.assertIn("BRK.B", by_holding)
        self.assertIn("GOOG", by_holding)
        self.assertIn("GOOGL", by_holding)
        self.assertNotIn("", by_holding)
        self.assertNotIn("CASH", by_holding)

    def test_phase13_etf_universe_holdings_summary_reports_matched_real_world_etf_sources(self):
        class _FakeResult:
            def __init__(self, query="", kwargs=None):
                self.query = query
                self.kwargs = kwargs or {}

            def consume(self):
                return self

            def single(self):
                if "RETURN count(r) AS created, count(DISTINCT c.ticker) AS matched_holdings" in self.query:
                    params = list(self.kwargs.get("params") or [])
                    etf_ticker = params[0]["etf"] if params else ""
                    matched = etf_ticker in {"IVV", "SPY", "VTI", "QQQ", "VUG", "VGT"}
                    matched_count = len(params) if matched else 0
                    return {"created": matched_count, "matched_holdings": matched_count}
                if "MATCH (src)-[r:ETF_HOLDS" in self.query:
                    return {"cnt": 6}
                if "MATCH ()-[r:ETF_HOLDS" in self.query and "RETURN count(r) AS cnt" in self.query:
                    return {"cnt": 6}
                if "RETURN count(r) AS created" in self.query:
                    key = "pairs" if "pairs" in self.kwargs else "params"
                    return {"created": len(self.kwargs.get(key) or [])}
                if "RETURN count(r) AS closed" in self.query:
                    return {"closed": 0}
                return {}

        class _FakeSession:
            def __init__(self):
                self.queries = []

            def run(self, query, **kwargs):
                self.queries.append((query, kwargs))
                return _FakeResult(query, kwargs)

        fake_session = _FakeSession()
        etf_rows = [
            {"ticker": "IVV", "name": "iShares Core S&P 500 ETF", "category": "Broad Market", "asset_class": "Equity"},
            {"ticker": "SPY", "name": "SPDR S&P 500 ETF Trust", "category": "Broad Market", "asset_class": "Equity"},
            {"ticker": "VTI", "name": "Vanguard Total Stock Market ETF", "category": "Broad Market", "asset_class": "Equity"},
            {"ticker": "QQQ", "name": "Invesco QQQ Trust", "category": "Technology", "asset_class": "Equity"},
            {"ticker": "VUG", "name": "Vanguard Growth ETF", "category": "Growth", "asset_class": "Equity"},
            {"ticker": "VGT", "name": "Vanguard Information Technology ETF", "category": "Technology", "asset_class": "Equity"},
            {"ticker": "BND", "name": "Vanguard Total Bond Market ETF", "category": "Bond", "asset_class": "Bond"},
            {"ticker": "BIV", "name": "Vanguard Intermediate-Term Bond ETF", "category": "Bond", "asset_class": "Bond"},
            {"ticker": "GLD", "name": "SPDR Gold Shares", "category": "Materials", "asset_class": "Commodity"},
            {"ticker": "IBIT", "name": "iShares Bitcoin Trust ETF", "category": "Broad Market", "asset_class": "Currency"},
            {"ticker": "SGOV", "name": "iShares 0-3 Month Treasury Bond ETF", "category": "Treasuries", "asset_class": "Bond"},
            {"ticker": "TLT", "name": "iShares 20+ Year Treasury Bond ETF", "category": "Treasuries", "asset_class": "Bond"},
        ]
        holdings_cache = {
            "IVV": [{"holding_ticker": "AAPL", "weight_pct": 7.12}],
            "SPY": [{"holding_ticker": "MSFT", "weight_pct": 6.44}],
            "VTI": [{"holding_ticker": "NVDA", "weight_pct": 5.11}],
            "QQQ": [{"holding_ticker": "AMZN", "weight_pct": 4.02}],
            "VUG": [{"holding_ticker": "META", "weight_pct": 2.17}],
            "VGT": [{"holding_ticker": "AVGO", "weight_pct": 3.05}],
            "BND": [{"holding_ticker": "91282CLW9", "weight_pct": 1.1}],
            "BIV": [{"holding_ticker": "91282CM82", "weight_pct": 1.0}],
            "GLD": [{"holding_ticker": "GOLD_BARS", "weight_pct": 100.0}],
            "IBIT": [{"holding_ticker": "BTC", "weight_pct": 100.0}],
            "SGOV": [{"holding_ticker": "912797LC4", "weight_pct": 2.0}],
            "TLT": [{"holding_ticker": "912810TM0", "weight_pct": 1.8}],
        }

        def _cache_side_effect(path, max_age):
            if path.endswith("etf_list_v3.json"):
                return etf_rows
            if path.endswith("etf_holdings.json"):
                return holdings_cache
            return None

        with patch.object(gns, "_nexus_read_cached_json", side_effect=_cache_side_effect), \
             patch.object(gns, "_select_key_etfs_dynamic", return_value=set(holdings_cache)), \
             patch.object(gns, "_sync_graph_edge_intervals"), \
             patch.object(gns, "_retire_relationships", return_value=0), \
             patch.object(gns, "POLYGON_API_KEY", ""), \
             patch.object(gns, "_log") as log_mock:
            gns.phase13_etf_universe(fake_session)

        log_messages = [call.args[0] for call in log_mock.call_args_list if call.args]
        self.assertIn(
            "Phase 12 ETF: 6 live ETF_HOLDS relationships written for 6 ETFs with matched company holdings (12 key ETFs scraped/cached).",
            log_messages,
        )
        self.assertTrue(any("samples: BIV, BND, GLD, IBIT, SGOV, TLT" in msg for msg in log_messages))

    def test_fetch_sec_company_tickers_uses_cached_phase2_rows_before_live_request(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            phase2_dir = os.path.join(tmpdir, "phase2")
            os.makedirs(phase2_dir, exist_ok=True)
            cache_path = os.path.join(phase2_dir, "sec_company_ticker_rows.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                import json
                json.dump({
                    "TEST": {"cik": "0000000001", "title": "Test Corp"},
                }, f)
            with patch.object(sec, "SEC_EDGAR_CACHE_DIR", tmpdir), patch("requests.get") as mock_get:
                ticker_to_title, title_to_ticker, cik_to_ticker = sec._fetch_sec_company_tickers()
            mock_get.assert_not_called()
            self.assertEqual({"TEST": "Test Corp"}, ticker_to_title)
            self.assertEqual("TEST", title_to_ticker["test corp"])
            self.assertEqual("TEST", cik_to_ticker["0000000001"])

    def test_download_sec_filing_document_retries_after_429(self):
        valid_html = ("<html><body>" + ("actual filing " * 40) + "</body></html>").encode("utf-8")

        class _Resp:
            def __init__(self, status_code, content=b"", headers=None):
                self.status_code = status_code
                self.content = content
                self.headers = headers or {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests
                    response = requests.Response()
                    response.status_code = self.status_code
                    raise requests.HTTPError(f"{self.status_code} error", response=response)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sec, "SEC_EDGAR_CACHE_DIR", tmpdir), \
                 patch("requests.get", side_effect=[
                     _Resp(429, headers={"Retry-After": "0"}),
                     _Resp(200, content=valid_html),
                 ]) as mock_get, \
                 patch("time.sleep", return_value=None):
                path, cached = sec._download_sec_filing_document(
                    filing_url="https://www.sec.gov/Archives/edgar/data/1/abc/test.htm",
                    cache_subdir="historical_10k_filings",
                    ticker="TEST",
                    cik="1",
                    accession_compact="0000000001000001",
                    primary_document="test.htm",
                )
            self.assertFalse(cached)
            self.assertTrue(path and os.path.isfile(path))
            self.assertEqual(2, mock_get.call_count)

    def test_download_sec_filing_document_refetches_invalid_cached_sec_page(self):
        valid_html = ("<html><body>" + ("actual filing " * 40) + "</body></html>").encode("utf-8")

        class _Resp:
            def __init__(self, status_code, content=b"", headers=None):
                self.status_code = status_code
                self.content = content
                self.headers = headers or {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests
                    response = requests.Response()
                    response.status_code = self.status_code
                    raise requests.HTTPError(f"{self.status_code} error", response=response)

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "historical_10k_filings", "0000000001", "0000000001000001")
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, "test.htm")
            with open(cache_path, "wb") as f:
                f.write(b"<html><title>SEC.gov | Request Rate Threshold Exceeded</title></html>")
            with patch.object(sec, "SEC_EDGAR_CACHE_DIR", tmpdir), \
                 patch("requests.get", return_value=_Resp(200, content=valid_html)) as mock_get, \
                 patch("time.sleep", return_value=None):
                path, cached = sec._download_sec_filing_document(
                    filing_url="https://www.sec.gov/Archives/edgar/data/1/abc/test.htm",
                    cache_subdir="historical_10k_filings",
                    ticker="TEST",
                    cik="1",
                    accession_compact="0000000001000001",
                    primary_document="test.htm",
                )
            self.assertFalse(cached)
            self.assertEqual(1, mock_get.call_count)
            self.assertTrue(path and os.path.isfile(path))
            with open(path, "rb") as f:
                self.assertIn(b"actual filing", f.read())

    def test_download_sec_filing_document_retries_invalid_sec_html_content(self):
        valid_html = ("<html><body>" + ("actual filing " * 40) + "</body></html>").encode("utf-8")

        class _Resp:
            def __init__(self, status_code, content=b"", headers=None):
                self.status_code = status_code
                self.content = content
                self.headers = headers or {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests
                    response = requests.Response()
                    response.status_code = self.status_code
                    raise requests.HTTPError(f"{self.status_code} error", response=response)

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sec, "SEC_EDGAR_CACHE_DIR", tmpdir), \
                 patch("requests.get", side_effect=[
                     _Resp(200, content=b"<html><title>Request Rate Threshold Exceeded</title></html>"),
                     _Resp(200, content=valid_html),
                 ]) as mock_get, \
                 patch("time.sleep", return_value=None):
                path, cached = sec._download_sec_filing_document(
                    filing_url="https://www.sec.gov/Archives/edgar/data/1/abc/test.htm",
                    cache_subdir="historical_10k_filings",
                    ticker="TEST",
                    cik="1",
                    accession_compact="0000000001000001",
                    primary_document="test.htm",
                )
            self.assertFalse(cached)
            self.assertEqual(2, mock_get.call_count)
            self.assertTrue(path and os.path.isfile(path))
            with open(path, "rb") as f:
                self.assertIn(b"actual filing", f.read())

    def test_run_sec_edgar_supply_chain_historical_reparses_when_cache_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = os.path.join(tmpdir, "supply_chain_sec_edgar.csv")
            with open(csv_path, "w", encoding="utf-8", newline="") as handle:
                handle.write("sup,cust,confidence,source,revenue_pct,edge_type,last_confirmed,active_after\n")
                handle.write("OLD,EDGE,1.0,10-K,,SUPPLIER_OF,2025-01-01,2025-01-01\n")
            filing = {
                "cik": "0000000001",
                "filing_date": "2025-02-01",
                "accession_compact": "0000000001000001",
                "filing_url": "https://www.sec.gov/Archives/edgar/data/1/abc/test.htm",
                "primary_document": "test.htm",
            }
            parsed_edges = [{
                "sup": "TEST",
                "cust": "MSFT",
                "confidence": 1.0,
                "source": "10-K",
                "edge_type": "SUPPLIER_OF",
                "last_confirmed": "2025-02-01",
                "active_after": "2025-02-01",
            }]
            with patch.object(sec, "_fetch_sec_company_tickers", return_value=(
                {"TEST": "Test Corp", "MSFT": "Microsoft Corporation"},
                {"test corp": "TEST", "microsoft corporation": "MSFT"},
                {"0000000001": "TEST"},
            )), \
                 patch.object(sec, "_collect_submission_filing_records", return_value=[filing]), \
                 patch.object(sec, "_load_parsed_edges_for_key") as mock_load, \
                 patch.object(sec, "_download_sec_filing_document", return_value=("cached-test.htm", True)), \
                 patch.object(sec, "_parse_10k_and_extract_relationships", return_value=parsed_edges) as mock_parse:
                edges = sec.run_sec_edgar_supply_chain_historical(
                    start_date="2025-01-01",
                    end_date="2025-03-01",
                    tickers=["TEST"],
                    ticker_to_cik={"TEST": "0000000001"},
                    output_csv_path=csv_path,
                    ignore_parsed_edge_cache=True,
                    ignore_existing_output_csv=True,
                )

            mock_load.assert_not_called()
            mock_parse.assert_called_once()
            self.assertEqual(1, len(edges))
            self.assertEqual("TEST", edges[0]["sup"])
            self.assertEqual("MSFT", edges[0]["cust"])

    def test_fetch_submission_payload_by_filename_retries_after_429(self):
        class _Resp:
            def __init__(self, status_code, payload=None, headers=None):
                self.status_code = status_code
                self._payload = payload or {}
                self.headers = headers or {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests
                    response = requests.Response()
                    response.status_code = self.status_code
                    raise requests.HTTPError(f"{self.status_code} error", response=response)

            def json(self):
                return self._payload

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(sec, "SEC_EDGAR_CACHE_DIR", tmpdir), \
                 patch("requests.get", side_effect=[
                     _Resp(429, headers={"Retry-After": "0"}),
                     _Resp(200, payload={"filings": {"recent": {"form": [], "filingDate": []}}}),
                 ]) as mock_get, \
                 patch("time.sleep", return_value=None):
                payload = sec._fetch_submission_payload_by_filename("CIK0000000001.json", allow_live_lookup=True)
            self.assertIsInstance(payload, dict)
            self.assertEqual(2, mock_get.call_count)

    def test_get_recent_10k_filing_dates_uses_feed_updated_date(self):
        class _Resp:
            def __init__(self, text="", status_code=200):
                self.text = text
                self.status_code = status_code

            def raise_for_status(self):
                return None

        updated_at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
        updated_iso = updated_at.isoformat().replace("+00:00", "Z")
        atom_feed = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<feed xmlns='http://www.w3.org/2005/Atom'>"
            "<entry>"
            f"<updated>{updated_iso}</updated>"
            "<link href='https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000000001' />"
            "</entry>"
            "</feed>"
        )

        with patch.object(
            sec,
            "_fetch_sec_company_tickers",
            return_value=({"TEST": "Test Corp"}, {"test corp": "TEST"}, {"0000000001": "TEST"}),
        ), patch("requests.get", return_value=_Resp(text=atom_feed)):
            filing_dates = sec.get_recent_10k_filing_dates(hours=48)
        self.assertEqual({"TEST": updated_at.date().isoformat()}, filing_dates)


if __name__ == "__main__":
    unittest.main()
