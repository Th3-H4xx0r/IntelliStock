import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning.types import Observation, content_id


def _obs(**over):
    base = dict(
        run_id="559934", origin="backtest", venue="equity",
        strategy_id="graph_nexus_analysis", as_of="2026-04-01T13:30:00",
        symbol="INTC", action="buy", decision=1, normalized_score=1.0,
        executed=False, refusal_reason="unknown",
        votes=(("graph_nexus_analysis", 1, 0.5),), config_hash=None,
    )
    base.update(over)
    return Observation(**base)


def test_content_id_is_stable_and_64_hex():
    a = content_id("observation", {"run_id": "1", "symbol": "X"})
    b = content_id("observation", {"symbol": "X", "run_id": "1"})
    assert a == b, "key order must not change the identity"
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)


def test_same_decision_point_is_the_same_observation():
    assert _obs().id == _obs().id


def test_a_different_bar_is_a_different_observation():
    assert _obs().id != _obs(as_of="2026-04-02T13:30:00").id


def test_identity_ignores_fields_that_are_not_the_decision_point():
    # executed/refusal_reason are RESOLVED later; re-resolving must update the
    # same row, not create a second one. This is what makes backfill idempotent.
    assert _obs(executed=False).id == _obs(executed=True, refusal_reason=None).id


def test_to_doc_round_trips_votes_as_lists():
    doc = _obs().to_doc()
    assert doc["id"] == _obs().id
    assert doc["votes"] == [["graph_nexus_analysis", 1, 0.5]]
