"""Unit tests for the 2026-07 ROUND-2 script's pure logic.

No real database, no secrets. Exercises the pure ``build_round2_strategies``
function against a doc-179-shaped fixture: every R2 lever must land with its
exact new value, deletions must hit ONLY keys matching the dead-secret regex,
secret values must never appear in diff rows (fingerprints only), and
untouched keys — including ``single_position_max_pct``, which Task 12 now
reads — must stay untouched.
"""

import copy
import os
import sys

import pytest

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from scripts.apply_round2_2026_07 import (  # noqa: E402
    DEAD_SECRET_KEY_RE,
    R2_CHANGES,
    build_round2_strategies,
)


FAKE_AZURE_KEY = "azsecretvalue00000000000000000001"
FAKE_AZURE_ENDPOINT = "https://dead-legacy.openai.azure.com/"


def _doc179():
    """A doc-179-shaped Strategies row with the PRE-round2 live values.

    Inner config carries the OLD lever values, a family of dead
    ``*_llm_azure_openai_*`` keys holding live-looking credentials, LIVE
    ``{role}_azure_openai_*`` keys that must survive (code reads those), and
    untouched spot-check keys.
    """
    dead = {}
    for role in ("analyst_panel", "company_article", "event_maintenance",
                 "macro_article", "overlay", "sentiment",
                 "lookback_macro_article", "lookback_company_article"):
        dead[f"{role}_llm_azure_openai_api_key"] = FAKE_AZURE_KEY
        dead[f"{role}_llm_azure_openai_endpoint"] = FAKE_AZURE_ENDPOINT
        dead[f"{role}_llm_azure_openai_api_version"] = "2024-10-21"
        dead[f"{role}_llm_azure_openai_model_id"] = "dead-model-row-id-0001"
    return {
        "id": 179,
        "name": "alpaca-main strategy",
        "strategies": [
            {
                "name": "sub-strategy-0",
                "strategy": "graph_nexus_analysis",
                "config": {
                    # --- R2 lever keys, PRE-round2 values ---
                    "max_positions": 10,
                    "allocation_max_new_stock_buys": 10,
                    "profitable_min_hold_release_peak_drop_pct": 8,
                    "backfill_rotation_winner_lock_bypass_max_held_pnl_pct": 10,
                    "backfill_rotation_min_hold_days": 10,
                    "backfill_budget_reserve_pct": 0.2,
                    "macro_risk_scale_min": 0.8,
                    "etf_portfolio_pct": 0.20,
                    # rotation_positive_graph_gate_enabled deliberately ABSENT
                    # (Task 12 shipped it default-OFF; round-2 opts doc-179 in).
                    # --- dead secret family (must ALL be deleted) ---
                    **dead,
                    # --- LIVE azure role keys (code reads these — must survive) ---
                    "macro_article_azure_openai_api_key": "azLIVEROLEKEY0000000",
                    "macro_article_azure_openai_endpoint": "https://live.openai.azure.com/",
                    "azure_openai_api_key": "azROOTKEY00000000000",
                    # --- live role model refs (do NOT match the dead regex) ---
                    "macro_article_llm_model_id": "live-model-row-id-42",
                    "macro_article_llm_provider": "openrouter",
                    # --- UNTOUCHED spot-check keys ---
                    "fast_loser_cut_pct": -10,
                    "single_position_max_pct": 0.18,  # Task 12 reads it now!
                    "profitable_min_hold_release_enabled": True,
                    "backfill_queue_grace_bars": 3,
                    "backfill_queue_priority_grace_bars": 8,
                    "buy_threshold": 0.15,
                    # --- secrets (must never be touched or printed) ---
                    "alpaca_key": "AKFAKELIVEKEY1111111",
                    "alpaca_secret": "SKFAKELIVESECRET11111111111111111111",
                },
            },
        ],
        "config": {"legacy_outer": "DO-NOT-TOUCH"},
    }


def _build(doc=None):
    doc = doc or _doc179()
    return build_round2_strategies(doc)


def _cfg(doc=None):
    proposed, _rows, _deleted = _build(doc)
    return proposed[0]["config"]


# --- R2 levers: every key lands exactly ---------------------------------------


def test_every_r2_key_lands_exact_new_value():
    cfg = _cfg()
    for key, new_val in R2_CHANGES.items():
        assert cfg[key] == new_val, f"R2 key {key} = {cfg[key]!r}, expected {new_val!r}"


def test_r2_key_specific_values():
    cfg = _cfg()
    assert cfg["max_positions"] == 8
    assert cfg["allocation_max_new_stock_buys"] == 6
    assert cfg["profitable_min_hold_release_peak_drop_pct"] == 12
    assert cfg["backfill_rotation_winner_lock_bypass_max_held_pnl_pct"] == 3
    assert cfg["backfill_rotation_min_hold_days"] == 15
    assert cfg["backfill_budget_reserve_pct"] == 0.1
    assert cfg["macro_risk_scale_min"] == 0.9
    assert cfg["etf_portfolio_pct"] == 0.05
    assert cfg["rotation_positive_graph_gate_enabled"] is True


# --- deletions: regex-matching dead keys ONLY ----------------------------------


def test_all_dead_secret_keys_deleted():
    doc = _doc179()
    src_cfg = doc["strategies"][0]["config"]
    expected_dead = sorted(k for k in src_cfg if DEAD_SECRET_KEY_RE.match(k))
    assert len(expected_dead) == 32  # 8 roles × 4 suffixes in the fixture
    proposed, _rows, deleted = _build(doc)
    assert deleted == expected_dead
    cfg = proposed[0]["config"]
    for k in expected_dead:
        assert k not in cfg


def test_live_azure_role_keys_survive():
    # Code reads {role}_azure_openai_* (model_resolver / GNA) — never delete.
    cfg = _cfg()
    assert cfg["macro_article_azure_openai_api_key"] == "azLIVEROLEKEY0000000"
    assert cfg["macro_article_azure_openai_endpoint"] == "https://live.openai.azure.com/"
    assert cfg["azure_openai_api_key"] == "azROOTKEY00000000000"


def test_live_role_model_refs_survive():
    # {role}_llm_model_id / {role}_llm_provider do NOT match the dead regex.
    cfg = _cfg()
    assert cfg["macro_article_llm_model_id"] == "live-model-row-id-42"
    assert cfg["macro_article_llm_provider"] == "openrouter"


def test_regex_never_matches_live_key_shapes():
    for live in (
        "macro_article_llm_model_id",
        "macro_article_azure_openai_api_key",
        "azure_openai_api_key",
        "azure_openai_endpoint",
        "llm_azure_openai_api_key",  # no role prefix + underscore -> no match
    ):
        assert not DEAD_SECRET_KEY_RE.match(live), f"{live} must NOT match"
    for dead in (
        "sentiment_llm_azure_openai_api_key",
        "overlay_llm_azure_openai_endpoint",
        "lookback_macro_article_llm_azure_openai_api_version",
        "analyst_panel_llm_azure_openai_model_id",
    ):
        assert DEAD_SECRET_KEY_RE.match(dead), f"{dead} MUST match"


# --- secrets never in diff output ----------------------------------------------


def test_secret_values_never_in_diff_rows():
    _proposed, rows, _deleted = _build()
    rendered = repr(rows)
    assert FAKE_AZURE_KEY not in rendered
    assert FAKE_AZURE_ENDPOINT not in rendered
    assert "AKFAKELIVEKEY1111111" not in rendered
    assert "SKFAKELIVESECRET11111111111111111111" not in rendered


def test_deleted_rows_carry_fingerprints_only():
    _proposed, rows, _deleted = _build()
    del_rows = [r for r in rows if r["section"] == "DEL"]
    assert len(del_rows) == 32
    for row in del_rows:
        assert row["new"] == "(deleted)"
        # fingerprint = first-2-chars + length, e.g. "az…(len 33)"
        assert "…(len " in row["old"]
        assert FAKE_AZURE_KEY not in row["old"]


# --- untouched spot-checks -----------------------------------------------------


def test_untouched_keys_stay_untouched():
    cfg = _cfg()
    assert cfg["fast_loser_cut_pct"] == -10
    assert cfg["single_position_max_pct"] == 0.18  # Task 12 reads it — keep it
    assert cfg["profitable_min_hold_release_enabled"] is True
    assert cfg["backfill_queue_grace_bars"] == 3          # grace keys untouched
    assert cfg["backfill_queue_priority_grace_bars"] == 8
    assert cfg["buy_threshold"] == 0.15


def test_secrets_untouched_in_proposed_config():
    cfg = _cfg()
    assert cfg["alpaca_key"] == "AKFAKELIVEKEY1111111"
    assert cfg["alpaca_secret"] == "SKFAKELIVESECRET11111111111111111111"


def test_outer_legacy_config_untouched():
    doc = _doc179()
    build_round2_strategies(doc)
    assert doc["config"] == {"legacy_outer": "DO-NOT-TOUCH"}


# --- purity / shape ------------------------------------------------------------


def test_does_not_mutate_input():
    doc = _doc179()
    before = copy.deepcopy(doc)
    build_round2_strategies(doc)
    assert doc == before


def test_raises_on_wrong_shape():
    with pytest.raises(ValueError):
        build_round2_strategies({"strategies": []})
    with pytest.raises(ValueError):
        build_round2_strategies({"strategies": [{"config": "not-a-dict"}]})


def test_diff_rows_complete():
    doc = _doc179()
    _proposed, rows, deleted = _build(doc)
    assert len(rows) == len(R2_CHANGES) + len(deleted)
    by_key = {r["key"]: r for r in rows}
    src = doc["strategies"][0]["config"]
    for key, new_val in R2_CHANGES.items():
        assert by_key[key]["new"] == new_val
        assert by_key[key]["old"] == src.get(key, "(absent)")
    row = by_key["rotation_positive_graph_gate_enabled"]
    assert row["old"] == "(absent)"  # gate shipped default-OFF, key absent
    assert row["new"] is True
