"""The overlay prompt must hash the same across replicate runs — or not at all.

The backtest engine caches LLM responses under a content hash of the prompt
(``llm_utils._prompt_cache_key``). Measured on four replicate runs of one
window+config (bt 258930 / 738395 / 145317 / 259145, 2026-03-30→04-27 @900s),
the cache never reused a single row: 1,167-1,577 newly-written rows per run and
ZERO key overlap between any pair. Every call was a miss, every miss drew a
fresh sample, and the resulting replicate P&L spread swamped the 2-5pp of alpha
the strategy is being measured for.

These tests pin the two halves of the fix:

  * canonicalization — shuffled key order, float jitter, run-scoped ids and a
    reshuffled analog list must all collapse to one cache key;
  * no over-collapse — anything that would change the trade must still produce
    a different key, because a cache that merges distinct decisions is worse
    than one that never hits.
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from unittest.mock import patch  # noqa: E402

import llm_utils  # noqa: E402
from strategies import graph_nexus_analysis as gna  # noqa: E402


# ── The canonicalizer itself ──────────────────────────────────────────────

def test_dict_key_order_does_not_change_the_payload():
    a = {"alpha": 1, "beta": 2, "gamma": 3}
    b = {"gamma": 3, "alpha": 1, "beta": 2}
    assert gna._to_prompt_payload(a) == gna._to_prompt_payload(b)


def test_key_order_inside_a_list_of_dicts_does_not_change_the_payload():
    # TOON derives its tabular header from the union of keys in first-seen
    # order, so this is the case where key order actually reached the wire.
    a = [{"x": 1, "y": 2}, {"y": 4, "x": 3}]
    b = [{"y": 2, "x": 1}, {"x": 3, "y": 4}]
    assert gna._to_prompt_payload(a) == gna._to_prompt_payload(b)


def test_float_repr_jitter_collapses():
    # 0.1 + 0.2 == 0.30000000000000004 — a different prompt, an identical trade.
    assert gna._to_prompt_payload({"v": 0.1 + 0.2}) == gna._to_prompt_payload({"v": 0.3})
    assert "0.30000000000000004" not in gna._to_prompt_payload({"v": 0.1 + 0.2})


def test_negative_zero_folds_into_zero():
    assert gna._to_prompt_payload({"v": -0.0}) == gna._to_prompt_payload({"v": 0.0})


def test_quantization_is_finer_than_any_decision_threshold():
    # Producers already round to 4 places; 6 must not erase a distinction they keep.
    assert gna._to_prompt_payload({"v": 0.1234}) != gna._to_prompt_payload({"v": 0.1235})


def test_booleans_are_not_coerced_to_numbers():
    # bool is an int subclass — a careless numeric branch renders True as 1.0.
    assert gna._to_prompt_payload({"v": True}) != gna._to_prompt_payload({"v": 1})


def test_list_order_is_preserved():
    # Price series and ranked-reason lists carry meaning in their order; the
    # canonicalizer must not sort them.
    assert gna._to_prompt_payload([3, 1, 2]) != gna._to_prompt_payload([1, 2, 3])


def test_canonicalizer_survives_deeply_nested_payloads():
    deep = value = {}
    for _ in range(40):
        value["child"] = {}
        value = value["child"]
    value["v"] = 0.1 + 0.2
    gna._to_prompt_payload(deep)  # must not recurse past the depth cap


# ── Run-scoped stripping ──────────────────────────────────────────────────

def test_strip_run_scoped_removes_ids_recursively():
    payload = {
        "keep": 1,
        "instance_id": "alpaca-main",
        "nested": {"pit_manifest_id": "m-1", "keep": 2},
        "rows": [{"id": "r-1", "keep": 3}],
    }
    assert gna._strip_run_scoped(payload) == {
        "keep": 1, "nested": {"keep": 2}, "rows": [{"keep": 3}],
    }


def test_training_row_band_collapses_drift_but_keeps_magnitude():
    # Two replicate runs disagree on the exact count by a few hundred rows;
    # they must not disagree on the prompt.
    assert gna._training_row_band(2431) == gna._training_row_band(2687)
    # An order-of-magnitude difference is a real difference and must survive.
    assert gna._training_row_band(200) != gna._training_row_band(2000)
    assert gna._training_row_band(0) == 0
    assert gna._training_row_band(None) == 0
    assert gna._training_row_band("nonsense") == 0
    assert [gna._training_row_band(n) for n in (1, 7, 47, 2431, 5200)] == [1, 5, 20, 2000, 5000]


def test_canonical_historical_analogs_is_a_total_order():
    rows = [
        {"symbol": "MSFT", "entry_date": "2026-03-02", "latest_return": 1.0},
        {"symbol": "AAPL", "entry_date": "2026-03-02", "latest_return": 2.0},
        {"symbol": "NVDA", "entry_date": "2026-03-10", "latest_return": 3.0},
    ]
    assert gna._canonical_historical_analogs(rows) == gna._canonical_historical_analogs(rows[::-1])
    # Most recent first is preserved; the symbol is only the tiebreak.
    ordered = gna._canonical_historical_analogs(rows)
    assert [r["symbol"] for r in ordered] == ["NVDA", "AAPL", "MSFT"]


# ── End-to-end: the overlay prompt's cache key ────────────────────────────

def _overlay_cache_key(**overrides) -> str:
    """Build the real overlay prompt and return the key the cache would use."""
    payload = {
        "symbol": "AAPL",
        "base_score_doc": {
            "base_score": {"score": 1, "reason": "Graph(3 paths, raw=+0.310): supplier lift"},
            "ml": {
                "ml_up_probability": 0.61, "ml_down_probability": 0.39,
                "ml_expected_return": 1.2, "ml_confidence": 0.22,
                "ml_data_confidence": 0.31, "ml_training_rows": 2431,
                "top_features": [{"feature": "base_raw_score", "value": 0.31, "magnitude": 0.31}],
            },
            "base_signal": 0.42,
            "base_components": {"base_raw": 0.31, "direct": 1.0},
            "graph_relationships": ["TSMC supplies AAPL", "QCOM competes AAPL"],
            "graph_raw_score": 0.31,
            "graph_n_paths": 3,
        },
        "feature_row": {"base_raw_score": 0.31, "position_open": 0.0, "n_paths": 3.0},
        "active_events": [
            {"event_cluster_key": "k1", "event_name": "Tariff round", "impact_direction": "bearish"},
            {"event_cluster_key": "k2", "event_name": "AI capex", "impact_direction": "bullish"},
        ],
        "historical_analogs": [
            {"symbol": "MSFT", "entry_date": "2026-03-02", "latest_return": 1.5,
             "government_action_type": "none", "dominant_event_type": "earnings"},
            {"symbol": "NVDA", "entry_date": "2026-03-11", "latest_return": -0.8,
             "government_action_type": "none", "dominant_event_type": "earnings"},
        ],
        "price_history": {"current": 190.12, "ret_5d": 1.2, "recent_closes": [188.0, 189.1, 190.12]},
        "benzinga_context": "",
        "date_key": "2026-04-01",
    }
    payload.update(overrides)

    captured: dict = {}

    def _capture(provider, api_key, model, prompt, output_type, **kw):
        captured["prompt"] = prompt
        return None

    with patch.object(gna, "_resolve_role_llm_config",
                      return_value=("openrouter", "key", "nvidia/nemotron-3-ultra-550b-a55b", "v1")), \
         patch.object(gna, "_resolve_role_llm_provider_config", return_value={"reasoning_effort": "medium"}), \
         patch.object(gna, "_scl_guarded", side_effect=_capture):
        gna._apply_trade_overlay(
            payload.pop("symbol"),
            payload.pop("base_score_doc"),
            config={"llm_overlay_enabled": True, "use_toon_format": True},
            **payload,
        )
    return llm_utils._prompt_cache_key(
        captured["prompt"], "nvidia/nemotron-3-ultra-550b-a55b@medium", "",
    )


def test_replicate_runs_of_the_same_decision_share_one_cache_key():
    """The headline case: everything that differed between the four replicate
    runs, applied at once, must still hash to a single key."""
    baseline = _overlay_cache_key()

    replicate = _overlay_cache_key(
        # Events and analogs arrive from unordered RethinkDB scans.
        active_events=[
            {"impact_direction": "bullish", "event_name": "AI capex", "event_cluster_key": "k2"},
            {"impact_direction": "bearish", "event_name": "Tariff round", "event_cluster_key": "k1"},
        ],
        historical_analogs=[
            {"dominant_event_type": "earnings", "latest_return": -0.8, "symbol": "NVDA",
             "entry_date": "2026-03-11", "government_action_type": "none"},
            {"dominant_event_type": "earnings", "latest_return": 1.5, "symbol": "MSFT",
             "entry_date": "2026-03-02", "government_action_type": "none"},
        ],
        # Last-bit float drift and run-scoped identity riding along in the
        # feature row, which was never stripped before.
        feature_row={
            "n_paths": 3.0,
            "base_raw_score": 0.31 + 1e-13,
            "position_open": 0.0,
            "instance_id": "alpaca-main|9f2c",
            "pit_manifest_id": "manifest-77",
            "id": "alpaca-main|2026-04-01|AAPL",
        },
    )
    assert replicate == baseline


def test_training_row_growth_within_a_band_shares_one_cache_key():
    """`ml_training_rows` grows every bar and never matches between runs; on
    its own it pinned the hit rate at zero."""
    base = _overlay_cache_key()
    drifted = dict(
        base_score_doc={
            "base_score": {"score": 1, "reason": "Graph(3 paths, raw=+0.310): supplier lift"},
            "ml": {
                "ml_up_probability": 0.61, "ml_down_probability": 0.39,
                "ml_expected_return": 1.2, "ml_confidence": 0.22,
                "ml_data_confidence": 0.31, "ml_training_rows": 2687,
                "top_features": [{"feature": "base_raw_score", "value": 0.31, "magnitude": 0.31}],
            },
            "base_signal": 0.42,
            "base_components": {"base_raw": 0.31, "direct": 1.0},
            "graph_relationships": ["TSMC supplies AAPL", "QCOM competes AAPL"],
            "graph_raw_score": 0.31,
            "graph_n_paths": 3,
        }
    )
    assert _overlay_cache_key(**drifted) == base


def test_a_different_graph_score_gets_a_different_cache_key():
    base = _overlay_cache_key()
    changed = _overlay_cache_key(
        base_score_doc={
            "base_score": {"score": 1, "reason": "Graph(3 paths, raw=+0.720): supplier lift"},
            "ml": {
                "ml_up_probability": 0.61, "ml_down_probability": 0.39,
                "ml_expected_return": 1.2, "ml_confidence": 0.22,
                "ml_data_confidence": 0.31, "ml_training_rows": 2431,
                "top_features": [{"feature": "base_raw_score", "value": 0.31, "magnitude": 0.31}],
            },
            "base_signal": 0.42,
            "base_components": {"base_raw": 0.72, "direct": 1.0},
            "graph_relationships": ["TSMC supplies AAPL", "QCOM competes AAPL"],
            "graph_raw_score": 0.72,
            "graph_n_paths": 3,
        }
    )
    assert changed != base


def test_holding_the_position_gets_a_different_cache_key():
    # `position_open` is run-scoped portfolio state AND decision-bearing —
    # exactly the thing that must never be canonicalized away.
    assert _overlay_cache_key(
        feature_row={"base_raw_score": 0.31, "position_open": 1.0, "n_paths": 3.0},
    ) != _overlay_cache_key()


def test_a_different_symbol_or_date_gets_a_different_cache_key():
    base = _overlay_cache_key()
    assert _overlay_cache_key(symbol="MSFT") != base
    # The date reaches the prompt through price history and the event block;
    # it must never collapse two trading days onto one cached verdict.
    assert _overlay_cache_key(
        price_history={"current": 201.44, "ret_5d": -0.4, "recent_closes": [199.0, 200.1, 201.44]},
    ) != base


def test_a_dropped_active_event_gets_a_different_cache_key():
    base = _overlay_cache_key()
    assert _overlay_cache_key(active_events=[
        {"event_cluster_key": "k1", "event_name": "Tariff round", "impact_direction": "bearish"},
    ]) != base


def test_different_analogs_get_a_different_cache_key():
    base = _overlay_cache_key()
    assert _overlay_cache_key(historical_analogs=[
        {"symbol": "MSFT", "entry_date": "2026-03-02", "latest_return": 9.9,
         "government_action_type": "none", "dominant_event_type": "earnings"},
        {"symbol": "NVDA", "entry_date": "2026-03-11", "latest_return": -0.8,
         "government_action_type": "none", "dominant_event_type": "earnings"},
    ]) != base


# ── ETF leg ───────────────────────────────────────────────────────────────

def _etf_overlay_cache_key(**overrides) -> str:
    payload = {
        "symbol": "SMH",
        "base_score_doc": {
            "base_score": {"score": 1, "reason": "Trend"},
            "ml": {"ml_confidence": 0.2, "ml_data_confidence": 0.9, "ml_training_rows": 2431},
            "base_signal": 0.3,
            "base_components": {"base_raw": 0.2},
        },
        "feature_row": {"base_raw_score": 0.2, "position_open": 0.0},
        "active_events": [{"event_cluster_key": "k1", "event_name": "AI capex",
                           "impact_direction": "bullish"}],
        "active_trends": [
            {"name": "ai-infrastructure", "strength": 0.7, "age_days": 12, "status": "active",
             "last_confirmed_date": "2026-03-30", "description": "capex cycle"},
            {"name": "semis-cycle", "strength": 0.5, "age_days": 30, "status": "active",
             "last_confirmed_date": "2026-03-28", "description": "memory pricing"},
        ],
        "price_history": {"current": 240.5},
        "date_key": "2026-04-01",
    }
    payload.update(overrides)
    captured: dict = {}

    def _capture(provider, api_key, model, prompt, output_type, **kw):
        captured["prompt"] = prompt
        return None

    with patch.object(gna, "_resolve_role_llm_config",
                      return_value=("openrouter", "key", "nvidia/nemotron-3-ultra-550b-a55b", "v1")), \
         patch.object(gna, "_resolve_role_llm_provider_config", return_value={"reasoning_effort": "medium"}), \
         patch.object(gna, "_scl_guarded", side_effect=_capture), \
         patch.object(gna, "_get_etfs_for_trend", return_value=["SMH"]):
        gna._apply_etf_trade_overlay(
            payload.pop("symbol"),
            payload.pop("base_score_doc"),
            config={"llm_overlay_enabled": True, "use_toon_format": True},
            **payload,
        )
    return llm_utils._prompt_cache_key(
        captured["prompt"], "nvidia/nemotron-3-ultra-550b-a55b@medium", "",
    )


def test_etf_overlay_trend_order_does_not_change_the_cache_key():
    base = _etf_overlay_cache_key()
    assert _etf_overlay_cache_key(active_trends=[
        {"description": "memory pricing", "name": "semis-cycle", "strength": 0.5, "age_days": 30,
         "status": "active", "last_confirmed_date": "2026-03-28"},
        {"description": "capex cycle", "name": "ai-infrastructure", "strength": 0.7, "age_days": 12,
         "status": "active", "last_confirmed_date": "2026-03-30"},
    ]) == base


def test_etf_overlay_still_separates_a_weakening_trend():
    base = _etf_overlay_cache_key()
    assert _etf_overlay_cache_key(active_trends=[
        {"name": "ai-infrastructure", "strength": 0.1, "age_days": 12, "status": "weakening",
         "last_confirmed_date": "2026-03-30", "description": "capex cycle"},
        {"name": "semis-cycle", "strength": 0.5, "age_days": 30, "status": "active",
         "last_confirmed_date": "2026-03-28", "description": "memory pricing"},
    ]) != base
