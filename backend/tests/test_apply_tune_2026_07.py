"""Unit tests for the 2026-07 live-tune script's pure logic.

No real database, no secrets. Exercises the pure ``build_tuned_strategies``
function against a doc-179-shaped fixture: every B1 trading key must land with
its exact new value, the B2 macro LLM roles must be copied verbatim from the
doc's default role, untouched keys must stay untouched, and the returned
``diff_rows`` must be complete.
"""

import copy
import os
import sys

import pytest

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from scripts.apply_tune_2026_07 import (  # noqa: E402
    B1_CHANGES,
    B2_ROLE_PREFIXES,
    build_tuned_strategies,
)


# The default-role Nemotron/OpenRouter values doc-179 already carries.
DEFAULT_PROVIDER = "openrouter"
DEFAULT_MODEL = "nvidia/llama-3.1-nemotron-70b-instruct"
DEFAULT_MODEL_ID = "11111111-2222-3333-4444-555555555555"


def _doc179():
    """A doc-179-shaped Strategies row with the PRE-tune live values.

    Inner config carries the OLD B1 values, codex-cli macro roles (B2 source),
    an OpenRouter default role, and a fake alpaca key/secret + benzinga api key
    to prove secrets are never touched by the tune.
    """
    return {
        "id": 179,
        "name": "alpaca-main strategy",
        "strategies": [
            {
                "name": "sub-strategy-0",
                "strategy": "graph_nexus_analysis",
                "config": {
                    # --- default role (B2 SOURCE) ---
                    "llm_provider": DEFAULT_PROVIDER,
                    "llm_model": DEFAULT_MODEL,
                    "llm_model_id": DEFAULT_MODEL_ID,
                    # --- macro roles on codex-cli (B2 TARGETS, pre-tune) ---
                    "macro_article_llm_provider": "codex-cli",
                    "macro_article_llm_model": "gpt-5-codex",
                    "macro_article_llm_model_id": "codex-model-id-000",
                    "lookback_macro_article_llm_provider": "codex-cli",
                    "lookback_macro_article_llm_model": "gpt-5-codex",
                    "lookback_macro_article_llm_model_id": "codex-model-id-000",
                    # --- B1 keys, PRE-tune values ---
                    "portfolio_drawdown_halt_pct": 12,
                    "profitable_min_hold_conviction_override_enabled": True,
                    "new_entry_reserved_budget_pct": 0.3,
                    "cash_reserve_floor_pct": 0.05,
                    "allocation_max_new_stock_buys": 6,
                    "max_propagated_scoring_slots": 20,
                    "max_positions": 8,
                    "rotation_break_glass_delta": 1,
                    "rotation_break_glass_raw_score": 1.5,
                    "rotation_profitable_min_incoming_raw_score": 1.5,
                    "benzinga_company_actions_enabled": True,
                    "benzinga_earnings_calendar_enabled": True,
                    "benzinga_gov_trades_enabled": True,
                    "benzinga_insider_trades_enabled": True,
                    "benzinga_insights_enabled": True,
                    "benzinga_ipo_enabled": True,
                    "benzinga_ma_enabled": True,
                    "benzinga_ratings_enabled": True,
                    "benzinga_splits_enabled": True,
                    # --- UNTOUCHED spot-check keys ---
                    "fast_loser_cut_pct": -10,
                    "min_position_size": 100,
                    "buy_threshold": 0.15,
                    # --- secrets (must never be touched) ---
                    "alpaca_key": "AKFAKELIVEKEY1111111",
                    "alpaca_secret": "SKFAKELIVESECRET11111111111111111111",
                    "benzinga_api_key": "BZFAKEAPIKEY000000",
                },
            },
        ],
        "config": {"legacy_outer": "DO-NOT-TOUCH"},
    }


def _tuned_cfg(doc=None):
    doc = doc or _doc179()
    proposed, _ = build_tuned_strategies(doc)
    return proposed[0]["config"]


# --- B1: every key lands exactly ---------------------------------------------


def test_b1_every_key_lands_exact_new_value():
    cfg = _tuned_cfg()
    for key, new_val in B1_CHANGES.items():
        assert cfg[key] == new_val, f"B1 key {key} = {cfg[key]!r}, expected {new_val!r}"


def test_b1_key_specific_values():
    cfg = _tuned_cfg()
    assert cfg["portfolio_drawdown_halt_pct"] == 8
    assert cfg["profitable_min_hold_conviction_override_enabled"] is False
    assert cfg["new_entry_reserved_budget_pct"] == 0.1
    assert cfg["cash_reserve_floor_pct"] == 0.02
    assert cfg["allocation_max_new_stock_buys"] == 10
    assert cfg["max_propagated_scoring_slots"] == 40
    assert cfg["max_positions"] == 10
    assert cfg["rotation_break_glass_delta"] == 2.5
    assert cfg["rotation_break_glass_raw_score"] == 3.5
    assert cfg["rotation_profitable_min_incoming_raw_score"] == 2.0
    for k in (
        "benzinga_company_actions_enabled",
        "benzinga_earnings_calendar_enabled",
        "benzinga_gov_trades_enabled",
        "benzinga_insider_trades_enabled",
        "benzinga_insights_enabled",
        "benzinga_ipo_enabled",
        "benzinga_ma_enabled",
        "benzinga_ratings_enabled",
        "benzinga_splits_enabled",
    ):
        assert cfg[k] is False, f"{k} should be disabled"


# --- B2: macro roles copied from the default role ----------------------------


def test_b2_macro_roles_copied_from_default_role():
    cfg = _tuned_cfg()
    for prefix in B2_ROLE_PREFIXES:
        assert cfg[prefix + "llm_provider"] == DEFAULT_PROVIDER
        assert cfg[prefix + "llm_model"] == DEFAULT_MODEL
        assert cfg[prefix + "llm_model_id"] == DEFAULT_MODEL_ID


def test_b2_default_role_itself_unchanged():
    cfg = _tuned_cfg()
    assert cfg["llm_provider"] == DEFAULT_PROVIDER
    assert cfg["llm_model"] == DEFAULT_MODEL
    assert cfg["llm_model_id"] == DEFAULT_MODEL_ID


def test_b2_raises_when_default_role_incomplete():
    doc = _doc179()
    del doc["strategies"][0]["config"]["llm_model_id"]
    with pytest.raises(ValueError):
        build_tuned_strategies(doc)


# --- untouched spot-checks ---------------------------------------------------


def test_untouched_keys_stay_untouched():
    cfg = _tuned_cfg()
    assert cfg["fast_loser_cut_pct"] == -10
    assert cfg["min_position_size"] == 100
    assert cfg["buy_threshold"] == 0.15


def test_secrets_untouched():
    cfg = _tuned_cfg()
    assert cfg["alpaca_key"] == "AKFAKELIVEKEY1111111"
    assert cfg["alpaca_secret"] == "SKFAKELIVESECRET11111111111111111111"
    assert cfg["benzinga_api_key"] == "BZFAKEAPIKEY000000"


def test_outer_legacy_config_untouched():
    doc = _doc179()
    proposed, _ = build_tuned_strategies(doc)
    # build_tuned_strategies returns only the strategies list; outer doc["config"]
    # is legacy and is never part of what we return/write.
    assert doc["config"] == {"legacy_outer": "DO-NOT-TOUCH"}  # input not mutated


# --- purity ------------------------------------------------------------------


def test_does_not_mutate_input():
    doc = _doc179()
    before = copy.deepcopy(doc)
    build_tuned_strategies(doc)
    assert doc == before


# --- diff_rows completeness --------------------------------------------------


def test_diff_rows_complete_and_correct():
    doc = _doc179()
    proposed, diff_rows = build_tuned_strategies(doc)

    # One row per B1 key + 3 rows per B2 role prefix.
    expected_count = len(B1_CHANGES) + 3 * len(B2_ROLE_PREFIXES)
    assert len(diff_rows) == expected_count

    by_key = {row["key"]: row for row in diff_rows}

    # Every B1 key present with correct old -> new.
    src = doc["strategies"][0]["config"]
    for key, new_val in B1_CHANGES.items():
        assert key in by_key
        assert by_key[key]["new"] == new_val
        assert by_key[key]["old"] == src[key]

    # B2 rows: old = codex-cli source, new = default-role value.
    for prefix in B2_ROLE_PREFIXES:
        assert by_key[prefix + "llm_provider"]["old"] == "codex-cli"
        assert by_key[prefix + "llm_provider"]["new"] == DEFAULT_PROVIDER
        assert by_key[prefix + "llm_model"]["new"] == DEFAULT_MODEL
        assert by_key[prefix + "llm_model_id"]["new"] == DEFAULT_MODEL_ID


def test_diff_rows_old_marks_absent_key():
    doc = _doc179()
    del doc["strategies"][0]["config"]["max_positions"]
    proposed, diff_rows = build_tuned_strategies(doc)
    row = next(r for r in diff_rows if r["key"] == "max_positions")
    assert row["old"] == "(absent)"
    assert row["new"] == 10
