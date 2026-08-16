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
    decision = perms.decide(
        matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
        rung=perms.LIVE_FULL, document_id="179", allowlist=["179"],
        approval={"status": "approved", "rung": perms.LIVE_FULL,
                  "action_class": perms.CONFIG_LEVERS, "document_id": "179"})
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
                             floor=_floor(), sessions_observed=99,
                             windows_passed=4)
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
                                 sessions_observed=99, windows_passed=4)
        assert move.direction == "hold"
        assert "no measured noise floor" in move.reason


def test_an_ambiguous_proof_blocks_promotion():
    move = laddering.promote(current_rung=laddering.BACKTEST,
                             judgement=_judgement(), proof=_proof("ambiguous"),
                             floor=_floor(), sessions_observed=99,
                             windows_passed=4)
    assert move.direction == "hold"


def test_a_judge_hold_blocks_promotion():
    move = laddering.promote(current_rung=laddering.BACKTEST,
                             judgement=_judgement("hold"), proof=_proof(),
                             floor=_floor(), sessions_observed=99,
                             windows_passed=4)
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


# ── Sweep regressions: 11 mutations survived, and one reached real money ─────

def test_an_approval_granted_at_paper_is_not_consent_at_live():
    """THE critical one. An approval that AUTO-PROCEEDED at PAPER on a 4-hour
    timeout — one the operator never answered — was accepted as consent for
    LIVE_CAPPED on document 179, the live Alpaca account. Silence below live
    minted a token honoured above it."""
    decision = perms.decide(
        matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
        rung=perms.LIVE_CAPPED, document_id="179", allowlist=["179"],
        approval={"status": "auto_approved", "rung": perms.PAPER,
                  "action_class": perms.CONFIG_LEVERS, "document_id": "179"})
    assert decision["allowed"] is False
    assert decision["needs_approval"] is True


def test_one_yes_at_paper_does_not_carry_through_both_live_rungs():
    approval = {"status": "approved", "rung": perms.PAPER,
                "action_class": perms.CONFIG_LEVERS, "document_id": "179"}
    for rung in (perms.LIVE_CAPPED, perms.LIVE_FULL):
        decision = perms.decide(matrix=_matrix(),
                                action_class=perms.CONFIG_LEVERS, rung=rung,
                                document_id="179", allowlist=["179"],
                                approval=approval)
        assert decision["allowed"] is False


def test_an_auto_proceeded_approval_is_never_consent_at_a_live_rung():
    """Belt and braces with approvals.auto_proceed, which refuses to mint one."""
    decision = perms.decide(
        matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
        rung=perms.LIVE_FULL, document_id="179", allowlist=["179"],
        approval={"status": "auto_approved", "rung": perms.LIVE_FULL,
                  "action_class": perms.CONFIG_LEVERS, "document_id": "179"})
    assert decision["allowed"] is False
    assert "silence is not a yes" in decision["reason"]


def test_an_approval_for_another_document_is_not_consent():
    decision = perms.decide(
        matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
        rung=perms.LIVE_FULL, document_id="179", allowlist=["179"],
        approval={"status": "approved", "rung": perms.LIVE_FULL,
                  "action_class": perms.CONFIG_LEVERS, "document_id": "195"})
    assert decision["allowed"] is False


def test_a_blocked_cell_is_refused():
    """The operator's 'never touch this' mode was 100% untested."""
    matrix = perms.merge_matrix({perms.CODE: {perms.LIVE_FULL: perms.BLOCKED}})
    decision = perms.decide(matrix=matrix, action_class=perms.CODE,
                            rung=perms.LIVE_FULL, document_id="179",
                            allowlist=["179"],
                            approval={"status": "approved",
                                      "rung": perms.LIVE_FULL,
                                      "action_class": perms.CODE,
                                      "document_id": "179"})
    assert decision["allowed"] is False


def test_a_document_not_in_a_non_empty_allowlist_is_refused():
    """The only negative test used an EMPTY allowlist, so a substring match
    would have survived: doc '17' passing on ['179']."""
    decision = perms.decide(matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
                            rung=perms.PAPER, document_id="193",
                            allowlist=["179", "195"])
    assert decision["allowed"] is False
    decision = perms.decide(matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
                            rung=perms.PAPER, document_id="17",
                            allowlist=["179"])
    assert decision["allowed"] is False


def test_a_string_allowlist_is_refused_rather_than_iterated_as_characters():
    """Storing "179" would arm docs 1, 7 and 9 while refusing 179."""
    decision = perms.decide(matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
                            rung=perms.PAPER, document_id="1", allowlist="179")
    assert decision["allowed"] is False
    assert "must be a list" in decision["reason"]


def test_ask_without_an_approval_is_not_allowed():
    """The CODE test asserted only needs_approval, never allowed is False —
    and the backend auto-deploys from main, so an allowed CODE change is a
    deployed patch."""
    decision = perms.decide(matrix=_matrix(), action_class=perms.CODE,
                            rung=perms.BACKTEST, document_id="195",
                            allowlist=["195"])
    assert decision["allowed"] is False


def test_an_approval_dataclass_is_accepted_not_crashed_on():
    from self_learning.approvals import Approval
    approval = Approval(hypothesis_id="h", experiment_id="e", target="t",
                        rung=perms.LIVE_FULL, action_class=perms.CONFIG_LEVERS,
                        summary="s", document_id="179", status="approved")
    decision = perms.decide(matrix=_matrix(), action_class=perms.CONFIG_LEVERS,
                            rung=perms.LIVE_FULL, document_id="179",
                            allowlist=["179"], approval=approval)
    assert decision["allowed"] is True


def test_one_confirmed_backtest_does_not_promote():
    """The replication requirement was decorative — promote() read only
    'sessions' while BACKTEST expresses dwell as windows. One judge-confirmed
    run promoting is the max-of-N artifact this project already has."""
    move = laddering.promote(current_rung=laddering.BACKTEST,
                             judgement=_judgement(), proof=_proof(),
                             floor=_floor(), windows_passed=1)
    assert move.direction == "hold"
    assert "max-of-N" in move.reason


def test_a_partial_requirements_override_does_not_zero_the_other_rungs():
    """Editing the PAPER dwell removed the dwell on the promotion into full
    real money."""
    move = laddering.promote(current_rung=laddering.LIVE_CAPPED,
                             judgement=_judgement(), proof=_proof(),
                             floor=_floor(), sessions_observed=0,
                             requirements={laddering.PAPER: {"sessions": 50}})
    assert move.direction == "hold"
    assert "session(s) observed" in move.reason


def test_the_shadow_and_live_capped_dwells_are_enforced():
    """Only PAPER's dwell was tested."""
    for rung in (laddering.SHADOW, laddering.LIVE_CAPPED):
        move = laddering.promote(current_rung=rung, judgement=_judgement(),
                                 proof=_proof(), floor=_floor(),
                                 sessions_observed=0)
        assert move.direction == "hold"


def test_demotion_drops_exactly_one_rung():
    move = laddering.demote(current_rung=laddering.LIVE_FULL,
                            consecutive_failures=3)
    assert move.to_rung == laddering.LIVE_CAPPED


def test_the_breaker_fires_on_a_negative_drawdown_convention_too():
    """Callers differ on whether a drawdown is -9 or +9, and under the negative
    convention the breaker could never fire."""
    assert laddering.breaker(drawdown_pct=-9.0, limit_pct=5.0).direction == "demote"


def test_the_breaker_records_the_rung_it_actually_fired_from():
    move = laddering.breaker(drawdown_pct=9.0, limit_pct=5.0,
                             current_rung=laddering.LIVE_CAPPED)
    assert move.from_rung == laddering.LIVE_CAPPED


def test_reservations_from_a_ledger_count_against_the_ceiling():
    """The pre-commitment mechanism the module exists for was untested — every
    test hand-built the reserved figure."""
    reserved = budgeting.reservation(experiment_id="e1", amount_usd=9.0,
                                     now_iso="2026-08-15T00:00:00")
    state = budgeting.state_from_ledger([reserved], now_iso="2026-08-15T01:00:00",
                                        daily_limit_usd=10, monthly_limit_usd=100)
    assert state.reserved_usd == 9.0
    assert budgeting.can_afford(state, 5.0)["allowed"] is False


def test_an_abandoned_reservation_expires_instead_of_bricking_the_loop():
    """Nothing releases a reservation if the engine dies between reserve and
    settle, and this host restarts often."""
    stale = budgeting.reservation(experiment_id="e1", amount_usd=9.0,
                                  now_iso="2026-05-01T00:00:00")
    state = budgeting.state_from_ledger([stale], now_iso="2026-08-15T00:00:00",
                                        daily_limit_usd=10, monthly_limit_usd=100)
    assert state.reserved_usd == 0.0


def test_spend_is_billed_when_it_settled_not_when_it_was_reserved():
    """A backtest starting at 23:59 and finishing at 00:30 was billed to a day
    already closed — and across a month boundary it escaped BOTH ceilings."""
    reserved = budgeting.reservation(experiment_id="e1", amount_usd=9.0,
                                     now_iso="2026-07-31T23:50:00")
    settled = budgeting.settle(reserved, actual_usd=9.0,
                               now_iso="2026-08-01T00:10:00")
    state = budgeting.state_from_ledger([settled], now_iso="2026-08-01T12:00:00",
                                        daily_limit_usd=10, monthly_limit_usd=100)
    assert state.spent_today_usd == 9.0
    assert state.spent_month_usd == 9.0


def test_a_negative_ledger_row_cannot_raise_the_ceiling():
    """A refund row or a sign bug turned a hard stop into a bigger budget."""
    state = budgeting.state_from_ledger(
        [{"amount_usd": -1000.0, "status": "spent", "at": "2026-08-15T00:00:00"}],
        now_iso="2026-08-15T01:00:00", daily_limit_usd=10, monthly_limit_usd=100)
    assert state.daily_remaining == 10.0


def test_the_monthly_ceiling_actually_binds():
    """Every test set monthly=100 with spent_month == spent_today, so the
    monthly ceiling was never the binding constraint."""
    state = budgeting.BudgetState(daily_limit_usd=100.0, monthly_limit_usd=10.0,
                                  spent_today_usd=0.0, spent_month_usd=9.0,
                                  reserved_usd=0.0)
    assert budgeting.can_afford(state, 5.0)["allowed"] is False


def test_a_debit_from_the_same_month_last_year_does_not_count():
    state = budgeting.state_from_ledger(
        [{"amount_usd": 9.0, "status": "spent", "at": "2025-08-15T00:00:00"}],
        now_iso="2026-08-15T01:00:00", daily_limit_usd=10, monthly_limit_usd=100)
    assert state.spent_month_usd == 0.0


def test_a_missing_cost_estimate_is_refused():
    """It always launched and settled for its real cost afterwards — the
    'tally that notices afterwards' this module replaces."""
    assert budgeting.can_afford(_state(), None)["allowed"] is False


def test_reverting_a_code_plan_with_a_config_revert_raises():
    """plan_code's token has no `before` map, so the breaker would have fired,
    'reverted', logged success, and left the change live."""
    plan = plan_code(branch="feat/x", diff_summary="d", test_command="t")
    with pytest.raises(ActionError, match="cannot restore a config document"):
        revert_config({"a": 1}, plan, force=True)


def test_a_token_missing_a_key_raises_rather_than_half_reverting():
    plan = plan_config_levers(document_id="d", config={"a": 1}, changes={"a": 2})
    broken = plan.__class__(action_class=plan.action_class,
                            document_id=plan.document_id, changes=plan.changes,
                            rollback_token={"before": {}}, proof_probe={},
                            notes="")
    with pytest.raises(ActionError, match="no prior value"):
        revert_config({"a": 2}, broken, force=True)


def test_a_list_value_round_tripped_from_the_database_still_reverts():
    """A tuple applied here comes back a list, and ("SPY","QQQ") !=
    ["SPY","QQQ"] refused the revert forever."""
    plan = plan_config_levers(document_id="d", config={"universe": ["A"]},
                              changes={"universe": ("SPY", "QQQ")})
    stored = {"universe": ["SPY", "QQQ"]}          # as RethinkDB returns it
    assert revert_config(stored, plan)["universe"] == ["A"]


def test_a_plan_round_trips_through_a_document():
    """Without a deserializer, a plan persisted before a container restart had
    nothing to revert with at 3am."""
    from self_learning.actions import from_doc
    plan = plan_config_levers(document_id="195", config={"a": 1}, changes={"a": 2})
    restored = from_doc(plan.to_doc())
    assert restored.action_class == plan.action_class
    assert restored.rollback_token == plan.rollback_token


def test_an_action_class_swap_keeps_the_rollback_token():
    """A hand-listed constructor silently dropped fields on the LLM_MODELS and
    UNIVERSE paths only — producing an unrevertable plan."""
    plan = plan_llm_model(document_id="d", config={},
                          role_key="learning_generator_llm_model_id",
                          model_id="abc")
    assert plan.rollback_token.get("before") is not None
    assert plan.proof_probe
