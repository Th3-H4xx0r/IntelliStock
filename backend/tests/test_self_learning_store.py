import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.store import (
    DEFAULT_CONFIG, LEARNING_TABLES, merge_config, observation_payloads,
    persistable,
)
from self_learning.types import Observation


def _obs(decision=1, symbol="X"):
    return Observation(run_id="1", origin="backtest", venue="equity",
                       strategy_id="rsi", as_of="2026-04-01T00:00:00",
                       symbol=symbol, action="buy", decision=decision,
                       normalized_score=1.0, executed=False,
                       refusal_reason="unfilled" if decision else None,
                       votes=(), config_hash=None)


def test_every_declared_table_is_prefixed_so_it_is_greppable():
    assert all(t.startswith("Learning") for t in LEARNING_TABLES)
    assert "LearningObservations" in LEARNING_TABLES
    assert "LearningFindings" in LEARNING_TABLES


def test_payloads_carry_the_content_id_as_the_primary_key():
    payloads = observation_payloads([_obs()])
    assert payloads[0]["id"] == _obs().id


def test_payloads_are_idempotent_for_the_same_decision_point():
    assert observation_payloads([_obs()]) == observation_payloads([_obs()])


def test_holds_do_not_become_rows():
    """A 15-minute-bar run emits 7-15k decisions, overwhelmingly HOLDs. A row
    per hold is what would make this a second PriceHistory; holds survive in the
    per-run aggregate, and the variance guard sees them in memory regardless."""
    mixed = [_obs(decision=1, symbol="A"), _obs(decision=0, symbol="B"),
             _obs(decision=-1, symbol="C")]
    kept = persistable(mixed)
    assert {o.symbol for o in kept} == {"A", "C"}
    assert len(observation_payloads(mixed)) == 2


def test_merge_config_fills_missing_keys_from_defaults():
    merged = merge_config({"retain_days": 30})
    assert merged["retain_days"] == 30
    assert merged["enabled"] == DEFAULT_CONFIG["enabled"]


def test_merge_config_of_none_is_the_defaults():
    assert merge_config(None) == DEFAULT_CONFIG


def test_defaults_ship_observe_only_and_with_an_empty_allowlist():
    # Phase 1 observes. An empty allowlist is what makes that structural
    # rather than a promise.
    assert DEFAULT_CONFIG["mode"] == "observe"
    assert DEFAULT_CONFIG["document_allowlist"] == []


def test_defaults_carry_a_processed_watermark():
    """Without a persisted watermark every container restart re-pulls every
    completed run — multi-GB of transfer against a 5-13MB-per-row table."""
    assert DEFAULT_CONFIG["processed_run_ids"] == []
