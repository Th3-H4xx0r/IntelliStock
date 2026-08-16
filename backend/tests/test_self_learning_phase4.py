"""Phase 4 (Act): permissions, the ladder, budget, and the action adapters.

The asymmetry is the safety property, and most of these tests exist to pin it
down: applying a change can be gated, blocked, or made to wait indefinitely;
REVERTING one never is. A rollback that waits for approval is a rollback that
does not happen at 3am, which is when it is needed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from self_learning import budget as budgeting
from self_learning import ladder as laddering
from self_learning import permissions as perms
from self_learning.actions import (
    ActionError, apply_to_config, is_noop, plan_code, plan_config_levers,
    plan_llm_model, revert_config,
)
from self_learning.execution_proof import ExecutionProof
from self_learning.judge import Judgement
from self_learning.noise import NoiseFloor


# ── Permissions ──────────────────────────────────────────────────────────────

def _matrix():
    return perms.merge_matrix(None)


def test_a_document_not_on_the_allowlist_is_refused_whatever_the_matrix_says():
    decision = perms.decide(matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
                            rung=perms.BACKTEST, document_id="179",
                            allowlist=[])
    assert decision["allowed"] is False
    assert "not on the allowlist" in decision["reason"]


def test_an_autonomous_class_applies_without_asking_below_live():
    decision = perms.decide(matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
                            rung=perms.PAPER, document_id="195",
                            allowlist=["195"])
    assert decision["allowed"] is True and decision["needs_approval"] is False


def test_a_live_rung_needs_approval_by_default():
    decision = perms.decide(matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
                            rung=perms.LIVE_FULL, document_id="179",
                            allowlist=["179"])
    assert decision["allowed"] is False and decision["needs_approval"] is True
    assert "wait indefinitely" in decision["reason"]


def test_code_changes_ask_even_at_the_lowest_rung():
    """The backend auto-deploys from main, so a merged patch is a deployed
    patch."""
    decision = perms.decide(matrix=_matrix(), action_class=perms.CODE,
                            rung=perms.BACKTEST, document_id="195",
                            allowlist=["195"])
    assert decision["needs_approval"] is True


def test_an_approved_proposal_may_apply():
    decision = perms.decide(matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
                            rung=perms.LIVE_FULL, document_id="179",
                            allowlist=["179"], approval={"status": "approved"})
    assert decision["allowed"] is True


def test_a_rejected_proposal_may_not_apply_and_does_not_re_ask():
    decision = perms.decide(matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
                            rung=perms.LIVE_FULL, document_id="179",
                            allowlist=["179"], approval={"status": "rejected"})
    assert decision["allowed"] is False and decision["needs_approval"] is False


def test_an_unknown_mode_falls_back_to_the_default_not_to_autonomous():
    """A typo in a config document must never widen permission."""
    matrix = perms.merge_matrix({perms.CONFIG_LEVERS: {perms.LIVE_FULL: "yolo"}})
    assert matrix[perms.CONFIG_LEVERS][perms.LIVE_FULL] == perms.ASK


def test_an_unknown_action_class_or_rung_is_refused():
    assert perms.decide(matrix=_matrix(), action_class="magic",
                        rung=perms.PAPER, document_id="1",
                        allowlist=["1"])["allowed"] is False
    assert perms.decide(matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
                        rung="MOON", document_id="1",
                        allowlist=["1"])["allowed"] is False


def test_reverting_is_never_gated():
    """THE asymmetry. Permission modes govern applying; rolling back is a
    safety action that must not wait for a human to wake up."""
    assert perms.revert_allowed()["allowed"] is True


# ── Ladder ───────────────────────────────────────────────────────────────────

def _judgement(verdict="confirm"):
    return Judgement(verdict=verdict, reason="r", statistical_accepted=True,
                     llm_verdict=verdict, overridden=False)


def _proof(status="executed"):
    return ExecutionProof(status=status, detail="", control_fingerprint="a",
                          treatment_fingerprint="b", changed_decisions=5,
                          control_n=10, treatment_n=10)


def _floor(measured=True):
    return NoiseFloor(target="t", window_class="c", n=3,
                      floor_pp=4.0 if measured else 0.0, mean_pp=1.0,
                      measured=measured, reason="" if measured else "never measured")


def test_a_clean_pass_promotes_exactly_one_rung():
    move = laddering.promote(current_rung=laddering.BACKTEST,
                             judgement=_judgement(), proof=_proof(),
                             floor=_floor(), sessions_observed=99)
    assert move.direction == "promote"
    assert move.to_rung == laddering.SHADOW


def test_promotion_never_skips_a_rung():
    move = laddering.promote(current_rung=laddering.PAPER,
                             judgement=_judgement(), proof=_proof(),
                             floor=_floor(), sessions_observed=99)
    assert move.to_rung == laddering.LIVE_CAPPED


def test_an_unmeasured_floor_blocks_promotion_at_every_rung():
    """Promoting into paper on an unmeasured floor just moves an unfounded
    belief closer to money."""
    for rung in (laddering.BACKTEST, laddering.PAPER, laddering.LIVE_CAPPED):
        move = laddering.promote(current_rung=rung, judgement=_judgement(),
                                 proof=_proof(), floor=_floor(measured=False),
                                 sessions_observed=99)
        assert move.direction == "hold"
        assert "no measured noise floor" in move.reason


def test_an_ambiguous_proof_blocks_promotion():
    move = laddering.promote(current_rung=laddering.BACKTEST,
                             judgement=_judgement(), proof=_proof("ambiguous"),
                             floor=_floor(), sessions_observed=99)
    assert move.direction == "hold"


def test_a_judge_hold_blocks_promotion():
    move = laddering.promote(current_rung=laddering.BACKTEST,
                             judgement=_judgement("hold"), proof=_proof(),
                             floor=_floor(), sessions_observed=99)
    assert move.direction == "hold"


def test_dwell_time_is_required_before_promotion():
    move = laddering.promote(current_rung=laddering.PAPER,
                             judgement=_judgement(), proof=_proof(),
                             floor=_floor(), sessions_observed=3)
    assert move.direction == "hold"
    assert "3 of 20 session(s)" in move.reason


def test_promotion_into_a_live_rung_is_flagged_as_gated():
    move = laddering.promote(current_rung=laddering.PAPER,
                             judgement=_judgement(), proof=_proof(),
                             floor=_floor(), sessions_observed=99)
    assert move.gated is True


def test_demotion_is_never_gated():
    move = laddering.demote(current_rung=laddering.LIVE_FULL,
                            consecutive_failures=3)
    assert move.direction == "demote" and move.gated is False


def test_demotion_waits_for_consecutive_failures_not_a_single_bad_read():
    move = laddering.demote(current_rung=laddering.LIVE_FULL,
                            consecutive_failures=1)
    assert move.direction == "hold"


def test_the_breaker_unwinds_the_whole_live_tier_at_once():
    """Stepping down one rung would leave real money at LIVE_CAPPED while the
    drawdown continued."""
    move = laddering.breaker(drawdown_pct=9.0, limit_pct=5.0)
    assert move.direction == "demote"
    assert move.to_rung == laddering.PAPER
    assert move.gated is False


def test_the_breaker_holds_inside_the_limit():
    assert laddering.breaker(drawdown_pct=2.0, limit_pct=5.0).direction == "hold"


def test_a_zero_breaker_limit_does_not_fire_constantly():
    assert laddering.breaker(drawdown_pct=2.0, limit_pct=0).direction == "hold"


# ── Budget ───────────────────────────────────────────────────────────────────

def _state(spent_today=0.0, reserved=0.0, daily=10.0, monthly=100.0):
    return budgeting.BudgetState(daily_limit_usd=daily, monthly_limit_usd=monthly,
                                 spent_today_usd=spent_today,
                                 spent_month_usd=spent_today,
                                 reserved_usd=reserved)


def test_a_reservation_within_budget_is_allowed():
    assert budgeting.can_afford(_state(), 5.0)["allowed"] is True


def test_a_reservation_beyond_the_daily_ceiling_is_refused():
    result = budgeting.can_afford(_state(spent_today=8.0), 5.0)
    assert result["allowed"] is False
    assert "remaining today" in result["reason"]


def test_existing_reservations_count_against_the_ceiling():
    """Otherwise two experiments each pass the check and together blow it."""
    assert budgeting.can_afford(_state(reserved=9.0), 5.0)["allowed"] is False


def test_no_configured_ceiling_means_no_spending():
    """Fail closed: an unset limit is not an infinite one."""
    result = budgeting.can_afford(_state(daily=0.0), 1.0)
    assert result["allowed"] is False
    assert "no spend limit is configured" in result["reason"]


def test_a_missing_budget_state_fails_closed():
    assert budgeting.can_afford(None, 1.0)["allowed"] is False


def test_a_non_numeric_or_negative_cost_is_refused():
    assert budgeting.can_afford(_state(), "free")["allowed"] is False
    assert budgeting.can_afford(_state(), -5.0)["allowed"] is False


def test_the_ledger_separates_today_from_the_month():
    ledger = [{"amount_usd": 3.0, "at": "2026-08-15T01:00:00", "status": "spent"},
              {"amount_usd": 4.0, "at": "2026-08-02T01:00:00", "status": "spent"}]
    state = budgeting.state_from_ledger(ledger, now_iso="2026-08-15T12:00:00",
                                        daily_limit_usd=10, monthly_limit_usd=100)
    assert state.spent_today_usd == 3.0
    assert state.spent_month_usd == 7.0


def test_an_undated_debit_counts_against_both_periods():
    """Silently dropping it would let a clock bug become free money."""
    ledger = [{"amount_usd": 6.0, "at": "nonsense", "status": "spent"}]
    state = budgeting.state_from_ledger(ledger, now_iso="2026-08-15T12:00:00",
                                        daily_limit_usd=10, monthly_limit_usd=100)
    assert state.spent_today_usd == 6.0 and state.spent_month_usd == 6.0


def test_a_released_reservation_stops_counting():
    reserved = budgeting.reservation(experiment_id="e1", amount_usd=5.0,
                                     now_iso="2026-08-15T00:00:00")
    released = budgeting.release(reserved, now_iso="2026-08-15T01:00:00",
                                 reason="experiment refused")
    state = budgeting.state_from_ledger([released], now_iso="2026-08-15T12:00:00",
                                        daily_limit_usd=10, monthly_limit_usd=100)
    assert state.reserved_usd == 0.0 and state.spent_today_usd == 0.0


def test_settling_converts_a_reservation_to_actual_spend():
    reserved = budgeting.reservation(experiment_id="e1", amount_usd=5.0,
                                     now_iso="2026-08-15T00:00:00")
    settled = budgeting.settle(reserved, actual_usd=2.75,
                               now_iso="2026-08-15T02:00:00")
    assert settled["status"] == "spent" and settled["amount_usd"] == 2.75


# ── Action adapters ──────────────────────────────────────────────────────────

def test_a_plan_records_before_and_after_for_every_key():
    plan = plan_config_levers(document_id="195", config={"max_positions": 6},
                              changes={"max_positions": 4})
    assert plan.changes == (("max_positions", 6, 4),)


def test_a_no_op_change_is_dropped_rather_than_applied():
    """Applying it would produce an inert treatment that Guard 2 then has to
    catch — better not to spend the runs at all."""
    plan = plan_config_levers(document_id="195", config={"max_positions": 6},
                              changes={"max_positions": 6})
    assert is_noop(plan) is True


def test_an_absent_key_round_trips_to_absent_not_to_none():
    """Setting a key to None is NOT the same as removing it — a missing key
    takes the code default, which is a different value."""
    config = {}
    plan = plan_config_levers(document_id="195", config=config,
                              changes={"new_flag": True})
    applied = apply_to_config(config, plan)
    assert applied["new_flag"] is True
    reverted = revert_config(applied, plan)
    assert "new_flag" not in reverted


def test_apply_never_mutates_the_input():
    config = {"a": 1}
    plan = plan_config_levers(document_id="d", config=config, changes={"a": 2})
    apply_to_config(config, plan)
    assert config == {"a": 1}


def test_a_revert_refuses_when_an_operator_changed_the_key_underneath():
    """A config document is shared mutable state. Blindly restoring would
    silently discard a human's edit."""
    config = {"max_positions": 6}
    plan = plan_config_levers(document_id="195", config=config,
                              changes={"max_positions": 4})
    applied = apply_to_config(config, plan)
    applied["max_positions"] = 8            # the operator intervened
    with pytest.raises(ActionError, match="someone else changed it"):
        revert_config(applied, plan)


def test_a_forced_revert_wins_for_the_breaker():
    """Getting out matters more than politeness when the breaker fires."""
    config = {"max_positions": 6}
    plan = plan_config_levers(document_id="195", config=config,
                              changes={"max_positions": 4})
    applied = apply_to_config(config, plan)
    applied["max_positions"] = 8
    assert revert_config(applied, plan, force=True)["max_positions"] == 6


def test_an_llm_role_swap_must_target_a_model_id_key():
    with pytest.raises(ActionError, match="model_resolver"):
        plan_llm_model(document_id="d", config={}, role_key="llm_provider",
                       model_id="m")


def test_an_llm_role_swap_is_its_own_action_class():
    plan = plan_llm_model(document_id="d", config={},
                          role_key="learning_generator_llm_model_id",
                          model_id="abc")
    assert plan.action_class == perms.LLM_MODELS


def test_a_code_action_rolls_back_with_a_revert_commit():
    """The backend auto-deploys from main, so 'undo' means landing a revert,
    not editing a document."""
    plan = plan_code(branch="feat/x", diff_summary="one flag",
                     test_command="pytest backend/tests -q")
    assert plan.rollback_token["kind"] == "revert_commit"
    assert plan.action_class == perms.CODE


def test_planning_nothing_is_an_error_not_an_empty_plan():
    with pytest.raises(ActionError):
        plan_config_levers(document_id="d", config={}, changes={})


def test_every_plan_carries_a_proof_probe():
    """Guard 2 needs positive evidence; 13 levers here shipped without any."""
    plan = plan_config_levers(document_id="d", config={"a": 1}, changes={"a": 2})
    assert plan.proof_probe["kind"] == "config_keys"
    assert plan.proof_probe["keys"] == ["a"]
