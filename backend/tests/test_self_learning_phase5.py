"""Phase 5 (Live + code): attribution, the breaker, and the loop's decisions.

The loop is a PURE function returning intents, which is the only reason a fully
autonomous system that can touch real money is reviewable at all — every branch
that could move money is testable without a database, a model, or a broker.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from self_learning import loop as looping
from self_learning import permissions as perms
from self_learning.attribution import since_applied, worst_drawdown
from self_learning.execution_proof import ExecutionProof
from self_learning.judge import Judgement
from self_learning.noise import NoiseFloor


def _series(points):
    return [{"timestamp": t, "value": v} for t, v in points]


# ── Attribution ──────────────────────────────────────────────────────────────

def test_a_drop_matched_by_the_market_is_not_attributable():
    """Tripping the breaker on a market-wide drop would revert a change for
    being right."""
    result = since_applied(
        applied_at="2026-08-01T00:00:00",
        equity_series=_series([("2026-08-01T00:00:00", 10000.0),
                               ("2026-08-10T00:00:00", 9000.0)]),
        benchmark_series=_series([("2026-08-01T00:00:00", 500.0),
                                  ("2026-08-10T00:00:00", 450.0)]))
    assert result.measurable is True
    assert round(result.raw_pct, 2) == -10.0
    assert round(result.attributable_pct, 2) == 0.0


def test_a_drop_against_a_flat_market_is_attributable():
    result = since_applied(
        applied_at="2026-08-01T00:00:00",
        equity_series=_series([("2026-08-01T00:00:00", 10000.0),
                               ("2026-08-10T00:00:00", 9100.0)]),
        benchmark_series=_series([("2026-08-01T00:00:00", 500.0),
                                  ("2026-08-10T00:00:00", 500.0)]))
    assert round(result.attributable_pct, 2) == -9.0


def test_with_no_benchmark_the_raw_move_is_used_and_says_so():
    """Refusing to measure would mean never firing, and a breaker that cannot
    fire is decoration."""
    result = since_applied(
        applied_at="2026-08-01T00:00:00",
        equity_series=_series([("2026-08-01T00:00:00", 10000.0),
                               ("2026-08-10T00:00:00", 9100.0)]))
    assert round(result.attributable_pct, 2) == -9.0
    assert "errs toward firing" in result.reason


def test_equity_before_the_action_is_not_counted():
    """A loss that predates the change is not the change's fault."""
    result = since_applied(
        applied_at="2026-08-05T00:00:00",
        equity_series=_series([("2026-08-01T00:00:00", 20000.0),
                               ("2026-08-05T00:00:00", 10000.0),
                               ("2026-08-10T00:00:00", 9500.0)]))
    assert result.equity_start == 10000.0
    assert round(result.raw_pct, 2) == -5.0


def test_an_unmeasurable_attribution_is_flagged_not_guessed():
    assert since_applied(applied_at="nonsense", equity_series=[]).measurable is False
    assert since_applied(applied_at="2026-08-01T00:00:00",
                         equity_series=[]).measurable is False


def test_worst_drawdown_returns_a_positive_percent():
    """Positive means 'this much drawdown', so it compares directly against the
    breaker limit."""
    losing = since_applied(
        applied_at="2026-08-01T00:00:00",
        equity_series=_series([("2026-08-01T00:00:00", 100.0),
                               ("2026-08-10T00:00:00", 91.0)]))
    assert round(worst_drawdown([losing]), 2) == 9.0


def test_a_profitable_set_has_no_drawdown_rather_than_a_negative_one():
    winning = since_applied(
        applied_at="2026-08-01T00:00:00",
        equity_series=_series([("2026-08-01T00:00:00", 100.0),
                               ("2026-08-10T00:00:00", 120.0)]))
    assert worst_drawdown([winning]) == 0.0


def test_unmeasurable_attributions_are_skipped_not_counted_as_zero():
    bad = since_applied(applied_at="nonsense", equity_series=[])
    assert worst_drawdown([bad]) == 0.0


# ── The loop's decisions ─────────────────────────────────────────────────────

def _floor(measured=True):
    return NoiseFloor(target="equity/nexus", window_class="c", n=3,
                      floor_pp=4.0, mean_pp=1.0, measured=measured)


def _change(rung=perms.PAPER, failures=0, sessions=99, verdict="confirm"):
    return {
        "target": "equity/nexus", "hypothesis_id": "h1", "document_id": "195",
        "action_class": perms.CONFIG_LEVERS, "rung": rung,
        "consecutive_failures": failures, "sessions_observed": sessions,
        "judgement": Judgement(verdict=verdict, reason="r",
                               statistical_accepted=True, llm_verdict=verdict,
                               overridden=False),
        "proof": ExecutionProof(status="executed", detail="",
                                control_fingerprint="a", treatment_fingerprint="b",
                                changed_decisions=5, control_n=10, treatment_n=10),
    }


def _plan(**over):
    base = dict(
        config={"enabled": True, "mode": "act", "document_allowlist": ["195"]},
        matrix=perms.merge_matrix(None),
        floors={"equity/nexus": _floor()},
        active_changes=[], hypotheses=[],
        budget_state={"remaining_usd": 10.0}, lease_decision=None,
        drawdown_pct=0.0, breaker_limit_pct=5.0,
        targets_seen=["equity/nexus"])
    base.update(over)
    return looping.plan_turn(**base)


def test_the_breaker_pre_empts_the_entire_turn():
    """Nothing else in the turn matters while real money is bleeding."""
    intents = _plan(drawdown_pct=9.0,
                    active_changes=[_change(rung=perms.LIVE_FULL)])
    assert len(intents) == 1
    assert intents[0].kind == looping.BREAK
    assert intents[0].payload["documents"] == ["195"]


def test_the_breaker_fires_even_when_the_subsystem_is_disabled():
    """Switching the subsystem off does not un-apply what it already did."""
    intents = _plan(config={"enabled": False, "mode": "act",
                            "document_allowlist": ["195"]},
                    drawdown_pct=9.0,
                    active_changes=[_change(rung=perms.LIVE_FULL)])
    assert intents[0].kind == looping.BREAK


def test_a_disabled_subsystem_otherwise_does_nothing():
    intents = _plan(config={"enabled": False, "mode": "act",
                            "document_allowlist": ["195"]})
    assert [i.kind for i in intents] == [looping.IDLE]


def test_observe_mode_still_emits_demotions():
    """Demotions are safety actions — they are not part of 'acting'."""
    intents = _plan(config={"enabled": True, "mode": "observe",
                            "document_allowlist": ["195"]},
                    active_changes=[_change(failures=3)])
    kinds = [i.kind for i in intents]
    assert looping.DEMOTE in kinds
    assert looping.PROMOTE not in kinds


def test_observe_mode_proposes_nothing():
    intents = _plan(config={"enabled": True, "mode": "observe",
                            "document_allowlist": ["195"]})
    assert [i.kind for i in intents] == [looping.IDLE]


def test_a_failing_change_is_demoted_before_anything_is_promoted():
    intents = _plan(active_changes=[_change(failures=3)])
    kinds = [i.kind for i in intents]
    assert kinds[0] == looping.DEMOTE


def test_a_change_coming_down_is_not_also_promoted():
    intents = _plan(active_changes=[_change(failures=3)])
    assert looping.PROMOTE not in [i.kind for i in intents]


def test_a_promotion_into_a_live_rung_asks_instead_of_applying():
    intents = _plan(config={"enabled": True, "mode": "act",
                            "document_allowlist": ["179"]},
                    active_changes=[dict(_change(rung=perms.PAPER),
                                         document_id="179")])
    kinds = [i.kind for i in intents]
    assert looping.REQUEST_APPROVAL in kinds
    assert looping.PROMOTE not in kinds


def test_a_document_off_the_allowlist_is_neither_promoted_nor_asked_about():
    """Blocked is blocked — asking would imply it could be approved."""
    intents = _plan(config={"enabled": True, "mode": "act",
                            "document_allowlist": []},
                    active_changes=[_change()])
    kinds = [i.kind for i in intents]
    assert looping.PROMOTE not in kinds and looping.REQUEST_APPROVAL not in kinds


def test_a_target_without_a_floor_can_only_measure_its_floor():
    intents = _plan(floors={})
    kinds = [i.kind for i in intents]
    assert looping.MEASURE_FLOOR in kinds
    assert looping.PROPOSE not in kinds


def test_an_exhausted_budget_stops_the_turn_before_any_spending():
    intents = _plan(budget_state={"remaining_usd": 0.0})
    assert intents[-1].kind == looping.IDLE
    assert "spend ceiling" in intents[-1].reason
    assert looping.RUN_EXPERIMENT not in [i.kind for i in intents]


def test_an_unavailable_lease_stops_the_turn():
    class _Denied:
        granted = False
        reason = "a human run is in flight"
    intents = _plan(lease_decision=_Denied())
    assert intents[-1].kind == looping.IDLE
    assert "human run" in intents[-1].reason


def test_a_pending_hypothesis_is_run_before_a_new_one_is_proposed():
    """A loop that proposes faster than it tests just grows a backlog."""
    intents = _plan(hypotheses=[{"id": "h9", "status": "proposed",
                                 "target": "equity/nexus"}])
    kinds = [i.kind for i in intents]
    assert looping.RUN_EXPERIMENT in kinds
    assert looping.PROPOSE not in kinds


def test_an_idle_loop_with_everything_measured_asks_for_a_new_hypothesis():
    intents = _plan()
    assert looping.PROPOSE in [i.kind for i in intents]


def test_every_intent_carries_its_reason():
    """The operator should read the loop's thinking in the ledger rather than
    infer it from behaviour."""
    for intent in _plan(active_changes=[_change()]):
        assert intent.reason


def test_the_summary_flags_a_breaking_turn():
    intents = _plan(drawdown_pct=9.0,
                    active_changes=[_change(rung=perms.LIVE_FULL)])
    assert looping.summarise(intents)["breaking"] is True
