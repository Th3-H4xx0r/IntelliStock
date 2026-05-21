"""Tests for backend.strategy_cache_persistence (Phase 1 snapshot extensions)."""

from __future__ import annotations

import pytest

from backend import strategy_cache_persistence as scp


def test_config_hash_stable_across_dict_order():
    """Same fields, different dict insertion order, must produce same hash."""
    cfg_a = {
        "strategy_name": "graph_nexus_analysis",
        "prompt_versions": {"sentiment": "v1", "macro": "v2"},
        "llm_stages": {"discovery": {"provider": "azure", "model": "gpt-5.4-mini", "effort": "medium"}},
        "history_scope_id_inputs": {"neo4j_uri": "bolt://x", "salt": ""},
        "lookback_learning_days": 120,
    }
    cfg_b = {
        "lookback_learning_days": 120,
        "history_scope_id_inputs": {"salt": "", "neo4j_uri": "bolt://x"},
        "llm_stages": {"discovery": {"effort": "medium", "model": "gpt-5.4-mini", "provider": "azure"}},
        "prompt_versions": {"macro": "v2", "sentiment": "v1"},
        "strategy_name": "graph_nexus_analysis",
    }
    assert scp._compute_config_hash(cfg_a) == scp._compute_config_hash(cfg_b)


def test_config_hash_changes_on_prompt_version_bump():
    """Bumping any prompt_version must change the hash."""
    cfg = {
        "strategy_name": "graph_nexus_analysis",
        "prompt_versions": {"sentiment": "v1"},
        "llm_stages": {},
        "history_scope_id_inputs": {},
        "lookback_learning_days": 120,
    }
    h1 = scp._compute_config_hash(cfg)
    cfg["prompt_versions"]["sentiment"] = "v2"
    h2 = scp._compute_config_hash(cfg)
    assert h1 != h2


def test_config_hash_ignores_neutral_fields():
    """Fields not in the canonical list must not affect the hash."""
    cfg_a = {
        "strategy_name": "graph_nexus_analysis",
        "prompt_versions": {"sentiment": "v1"},
        "llm_stages": {},
        "history_scope_id_inputs": {},
        "lookback_learning_days": 120,
        "display_name": "Nexus v1",
        "ui_theme": "dark",
        "operator_notes": "test config",
    }
    cfg_b = dict(cfg_a)
    cfg_b["display_name"] = "Nexus v999"
    cfg_b["ui_theme"] = "light"
    cfg_b["operator_notes"] = "different notes"
    assert scp._compute_config_hash(cfg_a) == scp._compute_config_hash(cfg_b)


def test_config_hash_invalid_input_returns_marker():
    assert scp._compute_config_hash(None) == "invalid"
    assert scp._compute_config_hash("not a dict") == "invalid"
    assert scp._compute_config_hash(42) == "invalid"


def test_module_hash_stable_for_same_file(tmp_path):
    """Same file contents must produce same hash across calls."""
    f = tmp_path / "fake_strategy.py"
    f.write_text("def run_once():\n    pass\n", encoding="utf-8")
    h1 = scp._compute_module_hash(str(f))
    h2 = scp._compute_module_hash(str(f))
    assert h1 == h2
    assert len(h1) == 16


def test_module_hash_changes_on_file_edit(tmp_path):
    """Edited file must produce different hash."""
    f = tmp_path / "fake_strategy.py"
    f.write_text("def run_once():\n    pass\n", encoding="utf-8")
    h1 = scp._compute_module_hash(str(f))
    f.write_text("def run_once():\n    return 1\n", encoding="utf-8")
    h2 = scp._compute_module_hash(str(f))
    assert h1 != h2


def test_module_hash_missing_file_returns_marker():
    assert scp._compute_module_hash("/nonexistent/path.py") == "missing"


def test_serialize_cache_roundtrip_preserves_basic_types():
    cache = {
        "_momentum_watchlist": ["AAPL", "MSFT"],
        "_deployment_bar_index": 3,
        "_peak_hwm": {"AAPL": 195.5, "MSFT": 410.2},
        "_some_set": {"a", "b", "c"},
    }
    blob = scp._serialize_cache_for_blob(cache)
    restored = scp._deserialize_cache_from_blob(blob)
    assert restored["_momentum_watchlist"] == ["AAPL", "MSFT"]
    assert restored["_deployment_bar_index"] == 3
    assert restored["_peak_hwm"] == {"AAPL": 195.5, "MSFT": 410.2}
    assert restored["_some_set"] == {"a", "b", "c"}


def test_serialize_cache_records_skipped_fields():
    """Blacklisted keys must NOT appear in the blob, and the blob must
    record which keys were dropped via __skipped_fields__."""
    cache = {
        "_momentum_watchlist": ["AAPL"],
        "_neo4j_snapshot": {"big": "lru cache"},
        "_llm_trace_buffer": ["..."],
        "_eta_sector_map": {"AAPL": "XLK"},
    }
    blob = scp._serialize_cache_for_blob(cache)
    restored = scp._deserialize_cache_from_blob(blob)
    assert "_neo4j_snapshot" not in restored
    assert "_llm_trace_buffer" not in restored
    assert "_eta_sector_map" not in restored
    assert "_momentum_watchlist" in restored
    skipped = restored.get("__skipped_fields__", [])
    assert "_neo4j_snapshot" in skipped
    assert "_llm_trace_buffer" in skipped
    assert "_eta_sector_map" in skipped


def test_deserialize_corrupt_blob_raises_value_error():
    with pytest.raises(ValueError):
        scp._deserialize_cache_from_blob("not valid json{{{")


def test_deserialize_empty_blob_returns_empty_dict():
    assert scp._deserialize_cache_from_blob("") == {}
    assert scp._deserialize_cache_from_blob("{}") == {}
