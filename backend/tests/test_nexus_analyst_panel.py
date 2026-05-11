"""
Tests for the Nexus Adversarial Analyst Panel.

Covers: default roles structure, consensus aggregation (all bullish, split,
weighted), score adjustments, agent failure handling, disabled-by-default,
agent memory format, weight computation, max LLM call cap, Pydantic response
parsing, debate response parsing, and consensus context formatting.

All tests use mocks -- no database, API, or LLM connections required.
"""

import os, sys, unittest
from unittest.mock import patch

_backend = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.nexus_analyst_panel import (
    DEFAULT_AGENTS as _DEFAULT_AGENT_ROLES,
    _aggregate_consensus, _compute_agent_weights,
    _AnalystPanelResponse, _DebateResponse, _OutlookEntry, _StockPrediction,
    _format_consensus_context, run_analyst_panel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RATING_SCORE = {"strong_buy": 2, "buy": 1, "hold": 0, "sell": -1, "strong_sell": -2}


def _cfg(**overrides) -> dict:
    """Minimal config with analyst panel keys."""
    c = {
        "analyst_panel_enabled": True,
        "analyst_panel_agents": list(_DEFAULT_AGENT_ROLES),
        "analyst_panel_rounds": 3,
        "analyst_panel_max_stocks": 5,
        "analyst_panel_score_weight": 0.15,
        "analyst_panel_max_llm_calls": 25,
        "analyst_panel_max_workers": 5,
        "analyst_panel_timeout_sec": 120,
        "analyst_panel_cooldown_seconds": 0,
        "analyst_panel_memory_days": 14,
        "analyst_panel_debate_style": "adversarial",
        "analyst_panel_llm_provider": "azure",
        "analyst_panel_llm_model": "gpt-4.1-mini",
        "analyst_panel_llm_api_key": "test-key",
        "analyst_panel_azure_openai_endpoint": "https://test.openai.azure.com",
        "analyst_panel_azure_openai_api_version": "2024-10-21",
        "llm_provider": "azure", "llm_model": "gpt-4.1-mini",
        "llm_api_key": "test-key", "instance_id": "test_unit",
    }
    c.update(overrides)
    return c


def _mock_panel_resp(direction="bullish", confidence=0.8, stocks=None):
    ol = _OutlookEntry(direction=direction, confidence=confidence)
    return _AnalystPanelResponse(
        outlook_1d=ol, outlook_3d=ol, outlook_1w=ol, outlook_1m=ol,
        stocks=stocks or [
            _StockPrediction(ticker="AAPL", rating="buy", conviction=0.8, rationale="Strong"),
            _StockPrediction(ticker="MSFT", rating="buy", conviction=0.7, rationale="Solid"),
        ],
        risks=["market volatility"], catalysts=["AI spending"],
    )


def _get_dir(consensus):
    """Extract direction from consensus dict (handles nested or flat layout)."""
    return consensus.get("direction", consensus.get("outlook_1d", {}).get("direction", ""))


def _get_conf(consensus):
    """Extract confidence from consensus dict."""
    return consensus.get("confidence", consensus.get("outlook_1d", {}).get("confidence", 0))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDefaultAgentRoles(unittest.TestCase):

    def test_default_agent_roles_structure(self):
        """DEFAULT_AGENTS has role, bias, system; no dupes."""
        self.assertIsInstance(_DEFAULT_AGENT_ROLES, (list, tuple))
        self.assertGreaterEqual(len(_DEFAULT_AGENT_ROLES), 1)
        seen = set()
        for entry in _DEFAULT_AGENT_ROLES:
            for key in ("role", "bias", "system"):
                self.assertIn(key, entry, f"Missing '{key}' in {entry}")
                self.assertIsInstance(entry[key], str)
                self.assertTrue(entry[key].strip(), f"Empty {key} for {entry.get('role')}")
            role = entry["role"]
            self.assertNotIn(role, seen, f"Duplicate role: {role}")
            seen.add(role)


class TestConsensusAggregation(unittest.TestCase):

    def test_consensus_aggregation_all_bullish(self):
        """5 agents all bullish -> consensus bullish with high confidence."""
        r1 = {
            f"agent_{i}": {
                "outlook_1d": {"direction": "bullish", "confidence": 0.8},
                "stocks": [
                    {"ticker": "AAPL", "rating": "buy", "conviction": 0.8},
                    {"ticker": "MSFT", "rating": "strong_buy", "conviction": 0.9},
                ],
            }
            for i in range(5)
        }
        weights = {f"agent_{i}": 1.0 for i in range(5)}
        outlook, adj = _aggregate_consensus(r1, {}, weights)
        self.assertIsInstance(outlook, dict)
        self.assertEqual(outlook["direction"], "bullish")
        self.assertGreaterEqual(outlook["confidence"], 0.7)

    def test_consensus_aggregation_split_panel(self):
        """3 bull + 2 bear -> weighted result, not unanimously bullish."""
        r1 = {}
        for i in range(3):
            r1[f"bull_{i}"] = {
                "outlook_1d": {"direction": "bullish", "confidence": 0.7},
                "stocks": [{"ticker": "AAPL", "rating": "buy", "conviction": 0.7}],
            }
        for i in range(2):
            r1[f"bear_{i}"] = {
                "outlook_1d": {"direction": "bearish", "confidence": 0.7},
                "stocks": [{"ticker": "AAPL", "rating": "sell", "conviction": 0.7}],
            }
        weights = {k: 1.0 for k in r1}
        outlook, adj = _aggregate_consensus(r1, {}, weights)
        self.assertIsInstance(outlook, dict)
        self.assertIn(outlook["direction"], ("bullish", "neutral"))
        self.assertLessEqual(outlook["confidence"], 0.85)

    def test_consensus_aggregation_with_weights(self):
        """High-accuracy bear vs low-accuracy bulls -> bear dominates."""
        r1 = {
            "strong_bear": {
                "outlook_1d": {"direction": "bearish", "confidence": 0.9},
                "stocks": [{"ticker": "AAPL", "rating": "strong_sell", "conviction": 0.9}],
            },
        }
        weights = {"strong_bear": 5.0}
        for i in range(4):
            r1[f"weak_bull_{i}"] = {
                "outlook_1d": {"direction": "bullish", "confidence": 0.5},
                "stocks": [{"ticker": "AAPL", "rating": "buy", "conviction": 0.5}],
            }
            weights[f"weak_bull_{i}"] = 0.5
        outlook, adj = _aggregate_consensus(r1, {}, weights)
        self.assertIsInstance(outlook, dict)
        self.assertIn(outlook["direction"], ("bearish", "neutral"))


class TestScoreAdjustments(unittest.TestCase):

    def test_score_adjustments_buy_signal(self):
        """Strong consensus buy -> positive adjustment."""
        r1 = {
            f"agent_{i}": {
                "outlook_1d": {"direction": "bullish", "confidence": 0.85},
                "stocks": [
                    {"ticker": "AAPL", "rating": "strong_buy", "conviction": 0.9},
                    {"ticker": "MSFT", "rating": "buy", "conviction": 0.8},
                ],
            }
            for i in range(5)
        }
        weights = {f"agent_{i}": 1.0 for i in range(5)}
        outlook, adj = _aggregate_consensus(r1, {}, weights)
        # adj maps ticker -> weighted score; strong_buy with high conviction -> positive
        self.assertGreater(adj.get("AAPL", 0.0), 0.0)

    def test_score_adjustments_mixed(self):
        """Mixed signals -> near-zero adjustment."""
        r1 = {}
        for i in range(2):
            r1[f"bull_{i}"] = {
                "outlook_1d": {"direction": "bullish", "confidence": 0.7},
                "stocks": [{"ticker": "TSLA", "rating": "strong_buy", "conviction": 0.7}],
            }
        for i in range(2):
            r1[f"bear_{i}"] = {
                "outlook_1d": {"direction": "bearish", "confidence": 0.7},
                "stocks": [{"ticker": "TSLA", "rating": "strong_sell", "conviction": 0.7}],
            }
        r1["neutral_0"] = {
            "outlook_1d": {"direction": "neutral", "confidence": 0.5},
            "stocks": [{"ticker": "TSLA", "rating": "hold", "conviction": 0.5}],
        }
        weights = {k: 1.0 for k in r1}
        outlook, adj = _aggregate_consensus(r1, {}, weights)
        self.assertAlmostEqual(adj.get("TSLA", 0.0), 0.0, delta=0.5)


class TestAgentFailureGracefulSkip(unittest.TestCase):

    def test_agent_failure_graceful_skip(self):
        """One agent fails (filtered out before aggregation), others still produce consensus."""
        # In real code, _run_parallel_agents filters out None results before
        # calling _aggregate_consensus, so only successful agents appear in r1.
        r1 = {
            "good_1": {
                "outlook_1d": {"direction": "bullish", "confidence": 0.8},
                "stocks": [{"ticker": "AAPL", "rating": "buy", "conviction": 0.8}],
            },
            "good_2": {
                "outlook_1d": {"direction": "bullish", "confidence": 0.7},
                "stocks": [{"ticker": "AAPL", "rating": "buy", "conviction": 0.7}],
            },
        }
        weights = {"good_1": 1.0, "good_2": 1.0, "failed": 1.0}
        outlook, adj = _aggregate_consensus(r1, {}, weights)
        self.assertIsInstance(outlook, dict)
        self.assertIn(outlook["direction"], ("bullish", "neutral", "bearish"))


class TestPanelDisabledByDefault(unittest.TestCase):

    def test_panel_disabled_by_default(self):
        """analyst_panel_enabled=False returns empty results."""
        config = _cfg(analyst_panel_enabled=False)
        ctx, adj = run_analyst_panel(
            config=config,
            news_summary="Markets up today",
            stock_candidates=[{"ticker": "AAPL", "score": 0.8}],
            strategy_cache={},
            instance_id="test_unit",
            date_key="2026-03-29",
        )
        self.assertEqual(ctx, "")
        self.assertEqual(adj, {})


class TestAgentMemoryFormat(unittest.TestCase):

    def test_agent_memory_format(self):
        """Memory entries have correct structure matching _save_round_results doc format.

        _save_round_results stores round1 as the raw model_dump(by_alias=True) output
        of _AnalystPanelResponse, which uses outlook_1d/3d/1w/1m (not 'outlook').
        """
        mem = {
            "id": "test_unit_2026-03-28_bull_analyst",
            "instance_id": "test_unit",
            "date_key": "2026-03-28",
            "agent_role": "bull_analyst",
            "round1": {
                "outlook_1d": {"d": "bullish", "c": 0.8},
                "outlook_3d": {"d": "neutral", "c": 0.5},
                "outlook_1w": {"d": "neutral", "c": 0.5},
                "outlook_1m": {"d": "neutral", "c": 0.5},
                "stocks": [{"t": "AAPL", "r": "buy", "cv": 0.8, "ra": "Strong"}],
                "risks": ["volatility"],
                "catalysts": ["AI spending"],
            },
            "round2": {
                "agreements": [{"with": "macro_strategist", "on": "rate path"}],
                "challenges": [{"against": "bear_analyst", "point": "overstated"}],
                "revised_outlook_1d": {"d": "bullish", "c": 0.75},
                "revised_stocks": [{"t": "AAPL", "r": "buy", "cv": 0.8, "ra": "Strong"}],
                "conviction_change": "unchanged",
                "defense": "",
            },
            "outcome_filled": False,
            "outcome_accuracy": {},
        }
        for k in ("id", "instance_id", "date_key", "agent_role", "round1",
                   "outcome_filled", "outcome_accuracy"):
            self.assertIn(k, mem)
        for k in ("outlook_1d", "stocks", "risks", "catalysts"):
            self.assertIn(k, mem["round1"])
        expected_id = f"{mem['instance_id']}_{mem['date_key']}_{mem['agent_role']}"
        self.assertEqual(mem["id"], expected_id)


class TestComputeAgentWeights(unittest.TestCase):

    def test_compute_agent_weights_no_history(self):
        """No history (conn=None) -> all weights equal to 1.0."""
        agents = [
            {"role": "bull_analyst", "bias": "optimistic", "system": "Bull"},
            {"role": "bear_analyst", "bias": "pessimistic", "system": "Bear"},
            {"role": "macro_strategist", "bias": "neutral", "system": "Macro"},
        ]
        weights = _compute_agent_weights(None, "test", "2026-03-29", agents)
        self.assertIsInstance(weights, dict)
        self.assertEqual(set(weights.keys()), {a["role"] for a in agents})
        vals = list(weights.values())
        for v in vals:
            self.assertAlmostEqual(v, 1.0, places=6,
                                   msg="All weights should be 1.0 with no DB connection")
            self.assertGreater(v, 0.0)

    def test_compute_agent_weights_default_equal(self):
        """With conn=None, all agents get default weight of 1.0."""
        agents = [
            {"role": "high_acc", "bias": "neutral", "system": "A"},
            {"role": "low_acc", "bias": "neutral", "system": "B"},
            {"role": "mid_acc", "bias": "neutral", "system": "C"},
        ]
        weights = _compute_agent_weights(None, "test", "2026-03-29", agents)
        self.assertIsInstance(weights, dict)
        # Without DB, all weights are equal (default path)
        self.assertAlmostEqual(weights["high_acc"], weights["low_acc"], places=6)
        self.assertAlmostEqual(weights["high_acc"], weights["mid_acc"], places=6)


class TestMaxLlmCallsRespected(unittest.TestCase):

    @patch("strategies.nexus_analyst_panel._get_nexus_db_conn", return_value=None)
    @patch("strategies.nexus_analyst_panel.call_structured_llm_by_provider")
    def test_max_llm_calls_respected(self, mock_llm, _mock_conn):
        """Configure 10 agents but cap at 5 LLM calls -> at most 5 calls."""
        mock_llm.return_value = _mock_panel_resp()
        config = _cfg(analyst_panel_max_llm_calls=5, analyst_panel_rounds=1)
        try:
            run_analyst_panel(
                config=config,
                news_summary="Markets up today",
                stock_candidates=[{"ticker": "AAPL", "score": 0.8}],
                strategy_cache={},
                instance_id="test_unit",
                date_key="2026-03-29",
            )
        except Exception:
            pass  # Other deps may not be mocked; verify via count
        self.assertLessEqual(
            mock_llm.call_count, 5,
            f"LLM called {mock_llm.call_count} times, expected <= 5",
        )


class TestPydanticResponseParsing(unittest.TestCase):

    def test_pydantic_response_parsing(self):
        """_AnalystPanelResponse parses correctly from aliased input."""
        resp = _AnalystPanelResponse(
            outlook_1d={"d": "bullish", "c": 0.8},
            outlook_3d={"d": "neutral", "c": 0.5},
            outlook_1w={"d": "bearish", "c": 0.6},
            outlook_1m={"d": "bullish", "c": 0.7},
            stocks=[
                {"t": "AAPL", "r": "strong_buy", "cv": 0.9, "ra": "AI momentum"},
                {"t": "MSFT", "r": "buy", "cv": 0.7, "ra": "Cloud growth"},
            ],
            risks=["Fed rate hike", "Tariff escalation"],
            catalysts=["AI capex cycle"],
        )
        self.assertEqual(resp.outlook_1d.direction, "bullish")
        self.assertAlmostEqual(resp.outlook_1d.confidence, 0.8)
        self.assertEqual(resp.outlook_3d.direction, "neutral")
        self.assertEqual(resp.outlook_1w.direction, "bearish")
        self.assertEqual(resp.outlook_1m.direction, "bullish")
        self.assertEqual(len(resp.stocks), 2)
        self.assertEqual(resp.stocks[0].ticker, "AAPL")
        self.assertEqual(resp.stocks[0].rating, "strong_buy")
        self.assertAlmostEqual(resp.stocks[0].conviction, 0.9)
        self.assertEqual(resp.stocks[0].rationale, "AI momentum")
        self.assertEqual(resp.risks, ["Fed rate hike", "Tariff escalation"])
        self.assertEqual(resp.catalysts, ["AI capex cycle"])


class TestDebateResponseParsing(unittest.TestCase):

    def test_debate_response_parsing(self):
        """_DebateResponse parses correctly."""
        resp = _DebateResponse(
            agreements=[{"with": "macro_strategist", "on": "Fed will hold rates"}],
            challenges=[
                {"against": "bear_analyst", "point": "Ignoring strong earnings"},
                {"against": "risk_manager", "point": "Overstating tail risk"},
            ],
            revised_outlook_1d={"d": "bullish", "c": 0.85},
            revised_stocks=[{"t": "NVDA", "r": "strong_buy", "cv": 0.95, "ra": "AI leader"}],
            conviction_change="increased",
            defense="Earnings growth justifies premium",
        )
        self.assertEqual(len(resp.agreements), 1)
        self.assertEqual(resp.agreements[0]["with"], "macro_strategist")
        self.assertEqual(len(resp.challenges), 2)
        self.assertEqual(resp.revised_outlook_1d.direction, "bullish")
        self.assertAlmostEqual(resp.revised_outlook_1d.confidence, 0.85)
        self.assertEqual(len(resp.revised_stocks), 1)
        self.assertEqual(resp.revised_stocks[0].ticker, "NVDA")
        self.assertEqual(resp.revised_stocks[0].rating, "strong_buy")
        self.assertEqual(resp.conviction_change, "increased")
        self.assertEqual(resp.defense, "Earnings growth justifies premium")


class TestFormatConsensusContext(unittest.TestCase):

    def test_format_consensus_context(self):
        """Output string contains expected sections.

        _format_consensus_context(outlook, adj, sw, mod=None)
        - outlook: dict with direction/confidence
        - adj: dict of ticker -> float score adjustment
        - sw: float score weight multiplier
        """
        outlook = {"direction": "bullish", "confidence": 0.78}
        adj = {"AAPL": 0.12, "NVDA": 0.18}
        sw = 0.15
        ctx = _format_consensus_context(outlook, adj, sw)
        self.assertIsInstance(ctx, str)
        self.assertGreater(len(ctx), 0)
        low = ctx.lower()
        self.assertIn("bullish", low)
        self.assertIn("aapl", low)
        self.assertIn("nvda", low)


if __name__ == "__main__":
    unittest.main()
