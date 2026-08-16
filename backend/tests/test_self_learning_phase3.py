"""Phase 3 (Hypothesize): roles, the hypothesis ledger, and the judge's floor.

The load-bearing test in this file is
`test_the_judge_cannot_promote_what_the_statistics_rejected`. The operator asked
for an LLM promotion judge; that is only safe if the statistical verdict is a
floor the judge may lower and never raise. Without it, "LLM as judge" is a
mechanism for laundering a failed experiment into a shipped one — this project's
max-of-N artifact, automated.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from self_learning import judge as judging
from self_learning.execution_proof import ExecutionProof
from self_learning.hypotheses import (
    CLOSED, HypothesisError, STATUS_REJECTED, already_proposed, build,
    prior_rejections,
)
from self_learning.noise import Verdict
from self_learning.roles import (
    ANALYST, GENERATOR, JUDGE, ROLES, all_role_configs, model_id_key,
    role_config, unconfigured_roles,
)


# ── Roles ────────────────────────────────────────────────────────────────────

def test_each_role_reads_its_own_model():
    """A cheap model narrates, a strong one hypothesises. If they shared a key
    that choice would not exist."""
    keys = {model_id_key(role) for role in ROLES}
    assert len(keys) == len(ROLES)
    assert model_id_key(ANALYST) == "learning_analyst_llm_model_id"


def test_a_role_reads_the_resolved_provider_and_model():
    resolved = {
        "learning_generator_llm_provider": "anthropic",
        "learning_generator_llm_model": "claude-opus-5",
        "learning_generator_llm_api_key": "secret",
    }
    cfg = role_config(GENERATOR, resolved)
    assert cfg.provider == "anthropic" and cfg.model == "claude-opus-5"
    assert cfg.configured is True


def test_a_role_with_no_model_is_not_configured():
    """Silently falling back to an ambient default would make the recorded
    model id a lie, and attribution is the point."""
    cfg = role_config(GENERATOR, {})
    assert cfg.configured is False
    assert unconfigured_roles({}) == list(ROLES)


def test_a_role_config_never_serializes_its_api_key():
    resolved = {"learning_judge_llm_provider": "anthropic",
                "learning_judge_llm_model": "m", "learning_judge_llm_api_key": "SECRET"}
    doc = role_config(JUDGE, resolved).to_doc()
    assert "SECRET" not in repr(doc)
    assert "api_key" not in doc


def test_the_judge_defaults_to_zero_temperature():
    """A judge should not be creative."""
    cfg = role_config(JUDGE, {"learning_judge_llm_provider": "p",
                              "learning_judge_llm_model": "m"})
    assert cfg.temperature == 0.0


def test_an_unknown_role_is_refused():
    with pytest.raises(ValueError):
        role_config("oracle", {})


def test_all_role_configs_covers_every_role():
    assert set(all_role_configs({})) == set(ROLES)


# ── Hypotheses ───────────────────────────────────────────────────────────────

def _payload(**over):
    base = {
        "claim": "the min-position floor refuses every grant",
        "mechanism": "grant median $160 vs floor median $370",
        "lever_keys": ["sizing_respects_satellite_share_enabled"],
        "predicted_direction": "increase",
        "predicted_min_pp": 2.0,
        "predicted_max_pp": 6.0,
    }
    base.update(over)
    return base


def test_a_valid_proposal_becomes_a_hypothesis():
    hypothesis = build(_payload(), finding_id="f1", target="equity/nexus")
    assert hypothesis.predicted_direction == "increase"
    assert hypothesis.lever_keys == ("sizing_respects_satellite_share_enabled",)


def test_a_proposal_without_a_direction_is_refused():
    """A prediction that cannot be wrong is not evidence."""
    with pytest.raises(HypothesisError, match="cannot be wrong"):
        build(_payload(predicted_direction=""), finding_id="f", target="t")


def test_a_proposal_without_a_magnitude_is_refused():
    with pytest.raises(HypothesisError, match="falsifiable"):
        build(_payload(predicted_min_pp=None), finding_id="f", target="t")


def test_a_proposal_without_a_mechanism_is_refused():
    """'It will help' teaches nothing when it turns out null."""
    with pytest.raises(HypothesisError, match="MECHANISM"):
        build(_payload(mechanism="  "), finding_id="f", target="t")


def test_a_proposal_with_no_lever_is_refused():
    with pytest.raises(HypothesisError):
        build(_payload(lever_keys=[]), finding_id="f", target="t")


def test_an_inverted_range_is_normalised_rather_than_rejected():
    hypothesis = build(_payload(predicted_min_pp=6.0, predicted_max_pp=2.0),
                       finding_id="f", target="t")
    assert hypothesis.predicted_min_pp == 2.0
    assert hypothesis.predicted_max_pp == 6.0


def test_the_prediction_is_part_of_the_identity():
    """Change what you predicted and it is a different hypothesis — you cannot
    edit a forecast to match the outcome."""
    up = build(_payload(predicted_direction="increase"), finding_id="f", target="t")
    down = build(_payload(predicted_direction="decrease"), finding_id="f", target="t")
    assert up.id != down.id


def test_the_same_change_is_the_same_hypothesis_however_it_is_worded():
    """Dedup was defeated by a full stop. The generator is SHOWN the rejected
    claim verbatim and runs at temperature 0.6 — it is being asked to reword —
    so identity is what CHANGES, not the prose."""
    a = build(_payload(), finding_id="f1", target="t")
    b = build(_payload(claim="The min-position floor refuses every grant."),
              finding_id="f2", target="t")
    c = build(_payload(claim="minimum-position floor blocks all grants"),
              finding_id="f3", target="t")
    assert a.id == b.id == c.id


def test_a_different_lever_is_a_different_hypothesis():
    a = build(_payload(), finding_id="f", target="t")
    b = build(_payload(lever_keys=["max_positions"]), finding_id="f", target="t")
    assert a.id != b.id


def test_lever_order_does_not_change_the_identity():
    a = build(_payload(lever_keys=["a", "b"]), finding_id="f", target="t")
    b = build(_payload(lever_keys=["b", "a"]), finding_id="f", target="t")
    assert a.id == b.id


def test_the_same_change_on_a_different_target_is_a_different_hypothesis():
    a = build(_payload(), finding_id="f", target="equity/nexus")
    b = build(_payload(), finding_id="f", target="crypto/meanrev")
    assert a.id != b.id


def test_a_prediction_inside_the_noise_floor_is_refused():
    """Unfalsifiable by construction: the experiment cannot resolve it either
    way, so running it only spends money."""
    from self_learning.noise import NoiseFloor
    floor = NoiseFloor(target="t", window_class="c", n=3, floor_pp=4.1,
                       mean_pp=0.0, measured=True)
    with pytest.raises(HypothesisError, match="inside the measured"):
        build(_payload(predicted_min_pp=0.1, predicted_max_pp=0.3),
              finding_id="f", target="t", noise_floor=floor)


def test_a_lever_that_does_not_exist_is_refused():
    """Otherwise the subsystem can autonomously write a key no strategy reads —
    a self-authored inert lever, on a live document."""
    with pytest.raises(HypothesisError, match="not present in the strategy"):
        build(_payload(lever_keys=["definitely_not_a_real_lever"]),
              finding_id="f", target="t",
              known_levers=["max_positions", "buy_threshold"])


def test_a_non_finite_prediction_is_refused():
    """NaN survives every comparison as False and is not valid JSON."""
    with pytest.raises(HypothesisError, match="finite"):
        build(_payload(predicted_min_pp=float("nan")), finding_id="f", target="t")
    with pytest.raises(HypothesisError, match="finite"):
        build(_payload(predicted_max_pp=float("inf")), finding_id="f", target="t")


def test_a_rejected_idea_is_not_re_proposed():
    """Without this the generator re-proposes the entry-gate idea this project
    already disproved twice."""
    hypothesis = build(_payload(), finding_id="f", target="t")
    ledger = [{"id": hypothesis.id, "status": STATUS_REJECTED,
               "status_reason": "effect inside the noise floor"}]
    reason = already_proposed(hypothesis, ledger)
    assert "already rejected" in reason
    assert "noise floor" in reason


def test_dedup_is_against_the_whole_ledger_not_just_survivors():
    """Checking only confirmed ones would let every rejected idea return each
    round, and the loop would never converge."""
    hypothesis = build(_payload(), finding_id="f", target="t")
    for status in CLOSED:
        assert already_proposed(hypothesis, [{"id": hypothesis.id, "status": status}])


def test_an_unseen_hypothesis_is_not_flagged():
    hypothesis = build(_payload(), finding_id="f", target="t")
    assert already_proposed(hypothesis, [{"id": "other", "status": "rejected"}]) == ""


def test_prior_rejections_are_scoped_to_the_target():
    ledger = [
        {"id": "a", "status": "rejected", "target": "equity/nexus", "created_at": "2"},
        {"id": "b", "status": "rejected", "target": "crypto/meanrev", "created_at": "1"},
        {"id": "c", "status": "proposed", "target": "equity/nexus", "created_at": "3"},
    ]
    rows = prior_rejections(ledger, target="equity/nexus")
    assert [r["id"] for r in rows] == ["a"]


# ── The judge's floor ────────────────────────────────────────────────────────

def _stat(accepted, reason="r"):
    return Verdict(accepted=accepted, reason=reason, effect_pp=5.0,
                   floor_pp=1.0, bar_pp=1.5, sign_consistent=4, n=4)


def _proof(status="executed"):
    return ExecutionProof(status=status, detail="", control_fingerprint="a",
                          treatment_fingerprint="b", changed_decisions=3,
                          control_n=10, treatment_n=10)


def _hyp(direction="increase"):
    return build(_payload(predicted_direction=direction), finding_id="f",
                 target="equity/nexus")


def test_the_judge_cannot_promote_what_the_statistics_rejected():
    """THE load-bearing rule of the whole phase."""
    judgement = judging.decide(statistical_verdict=_stat(False, "inside the floor"),
                               proof=_proof(), hypothesis=_hyp(),
                               llm_verdict="confirm",
                               llm_reason="looks good to me")
    assert judgement.verdict == judging.HOLD
    assert judgement.overridden is True
    assert "never raise it" in judgement.reason
    assert judging.promotes(judgement) is False


def test_the_judge_may_veto_something_the_statistics_accepted():
    judgement = judging.decide(statistical_verdict=_stat(True),
                               proof=_proof(), hypothesis=_hyp(), llm_verdict="veto",
                               llm_reason="the win is one position")
    assert judgement.verdict == judging.VETO
    assert judging.promotes(judgement) is False


def test_a_clean_pass_promotes():
    judgement = judging.decide(statistical_verdict=_stat(True), proof=_proof(),
                               hypothesis=_hyp(), llm_verdict="confirm")
    assert judging.promotes(judgement) is True


def test_an_inert_treatment_is_held_not_rejected():
    """Calling it rejected would retire a hypothesis that was never tested —
    the exact error that turned 13 inert levers into 13 'no effect' results."""
    judgement = judging.decide(statistical_verdict=_stat(True),
                               proof=_proof("inert"), hypothesis=_hyp(), llm_verdict="confirm")
    assert judgement.verdict == judging.HOLD
    assert "not testable as specified" in judgement.reason


def test_an_unprovable_treatment_is_also_held():
    judgement = judging.decide(statistical_verdict=_stat(True),
                               proof=_proof("unprovable"), hypothesis=_hyp(), llm_verdict="confirm")
    assert judgement.verdict == judging.HOLD


def test_an_unparseable_judgement_defaults_to_hold_not_approval():
    judgement = judging.decide(statistical_verdict=_stat(True), proof=_proof(),
                               hypothesis=_hyp(), llm_verdict="LGTM ship it")
    assert judgement.verdict == judging.HOLD
    assert "unparseable" in judgement.reason


def test_a_missing_judgement_defaults_to_hold():
    judgement = judging.decide(statistical_verdict=_stat(True), proof=_proof(),
                               hypothesis=_hyp(), llm_verdict=None)
    assert judgement.verdict == judging.HOLD


def test_the_judge_never_sees_the_generators_reasoning():
    """Shown its own argument, a model agrees with it."""
    hypothesis = build(_payload(), finding_id="f", target="equity/nexus")
    context = judging.judge_prompt_context(
        hypothesis=hypothesis, statistical_verdict=_stat(True),
        proof=_proof(), summary={"decided": 10})
    assert "mechanism" not in context
    assert hypothesis.mechanism not in repr(context)
    assert context["predicted_direction"] == "increase"
    assert context["measured_effect_pp"] == 5.0


# ── LLM plumbing ─────────────────────────────────────────────────────────────

def test_json_is_extracted_from_a_fenced_response():
    from self_learning.llm import extract_json
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_json_is_extracted_from_surrounding_prose():
    from self_learning.llm import extract_json
    assert extract_json('Sure! {"a": 1} hope that helps') == {"a": 1}


def test_an_unparseable_response_is_none_not_an_empty_dict():
    """The caller treats None as 'no proposal'. An empty dict would be read as
    a proposal with every field missing, which then fails validation with a
    confusing message instead of being skipped."""
    from self_learning.llm import extract_json
    assert extract_json("no json here") is None
    assert extract_json("[1, 2, 3]") is None
    assert extract_json("") is None


def test_an_unconfigured_role_does_not_silently_use_a_default():
    """A recorded model id that is not the model that answered would make every
    attribution guarantee here a lie."""
    from self_learning.llm import call_role
    result = call_role("generator", {}, "sys", "user",
                       caller=lambda **kw: '{"a": 1}')
    assert result.ok is False
    assert "no model configured" in result.error


def test_a_configured_role_records_the_model_it_used():
    from self_learning.llm import call_role
    resolved = {"learning_generator_llm_provider": "anthropic",
                "learning_generator_llm_model": "claude-opus-5",
                "learning_generator_llm_api_key": "k"}
    result = call_role("generator", resolved, "sys", "user",
                       caller=lambda **kw: '{"claim": "x"}')
    assert result.ok is True
    assert result.model == "claude-opus-5"
    assert result.payload == {"claim": "x"}
    assert len(result.prompt_hash) == 32


def test_a_provider_exception_is_captured_not_raised():
    from self_learning.llm import call_role

    def boom(**_kw):
        raise RuntimeError("provider down")

    resolved = {"learning_analyst_llm_provider": "p",
                "learning_analyst_llm_model": "m"}
    result = call_role("analyst", resolved, "s", "u", caller=boom)
    assert result.ok is False and "provider down" in result.error


def test_the_generator_prompt_carries_the_floor_the_rejections_and_the_levers():
    from self_learning.prompts import generator_prompt
    _system, user = generator_prompt(
        target="equity/nexus",
        levers=[{"key": "max_positions", "value_type": "number", "default": 6}],
        noise_floor={"measured": True, "floor_pp": 4.1},
        summary={"decided": 10}, refusal_cost={}, findings=[],
        rejected=[{"claim": "entry gates", "status": "rejected",
                   "status_reason": "made both windows worse"}])
    assert "4.1pp" in user
    assert "max_positions" in user
    assert "entry gates" in user
    assert "do NOT propose these again" in user


def test_the_generator_is_told_when_no_floor_exists():
    from self_learning.prompts import generator_prompt
    _system, user = generator_prompt(
        target="t", levers=[], noise_floor={"measured": False},
        summary={}, refusal_cost={}, findings=[], rejected=[])
    assert "NO noise floor has been measured" in user


# ── Approval queue ───────────────────────────────────────────────────────────

def _approval(rung="PAPER", **over):
    from self_learning.approvals import Approval
    base = dict(hypothesis_id="h1", experiment_id="e1", target="equity/nexus",
                rung=rung, action_class="config_levers",
                summary="clamp per-name weight",
                requested_at="2026-08-15T00:00:00")
    base.update(over)
    return Approval(**base)


def test_a_sub_live_approval_auto_proceeds_after_the_timeout():
    from self_learning.approvals import is_expired
    assert is_expired(_approval("PAPER"), now_iso="2026-08-15T05:00:00",
                      timeout_hours=4) is True


def test_a_live_approval_never_auto_proceeds():
    """The operator's rule: silence is never consent for real money."""
    from self_learning.approvals import is_expired
    for rung in ("LIVE_CAPPED", "LIVE_FULL"):
        assert is_expired(_approval(rung), now_iso="2030-01-01T00:00:00",
                          timeout_hours=4) is False


def test_auto_proceeding_a_live_approval_is_refused_outright():
    from self_learning.approvals import auto_proceed
    with pytest.raises(ValueError, match="silence is not consent"):
        auto_proceed(_approval("LIVE_FULL"), now_iso="2030-01-01T00:00:00")


def test_a_zero_timeout_means_hold_not_proceed_immediately():
    from self_learning.approvals import is_expired
    assert is_expired(_approval("PAPER"), now_iso="2030-01-01T00:00:00",
                      timeout_hours=0) is False


def test_an_unparseable_request_time_is_not_consent():
    from self_learning.approvals import is_expired
    assert is_expired(_approval("PAPER", requested_at="nonsense"),
                      now_iso="2030-01-01T00:00:00", timeout_hours=1) is False


def test_a_decided_approval_cannot_be_re_decided():
    """Re-deciding would silently reverse an operator's answer."""
    from self_learning.approvals import resolve
    approved = resolve(_approval(), decision="approved",
                       now_iso="2026-08-15T01:00:00")
    with pytest.raises(ValueError, match="already approved"):
        resolve(approved, decision="rejected", now_iso="2026-08-15T02:00:00")


def test_an_unknown_decision_is_refused():
    from self_learning.approvals import resolve
    with pytest.raises(ValueError):
        resolve(_approval(), decision="maybe", now_iso="t")


def test_live_approvals_are_pinned_to_the_top_of_the_queue():
    from self_learning.approvals import queue_view
    rows = [_approval("PAPER", requested_at="2026-08-01T00:00:00").to_doc(),
            _approval("LIVE_FULL", requested_at="2026-08-15T00:00:00").to_doc()]
    view = queue_view(rows)
    assert view["pending"][0]["rung"] == "LIVE_FULL"
    assert view["live_pending_count"] == 1
    assert view["blocking"] is True


def test_a_decided_approval_leaves_the_queue():
    from self_learning.approvals import queue_view
    rows = [_approval().to_doc(), dict(_approval().to_doc(), status="approved")]
    assert queue_view(rows)["pending_count"] == 1


def test_an_ambiguous_proof_cannot_be_promoted():
    """AMBIGUOUS was added to the proof module AFTER the judge was written, and
    the judge listed statuses by hand — so a treatment whose stream moved no
    more than control-vs-control churn fell straight through to CONFIRM."""
    judgement = judging.decide(statistical_verdict=_stat(True),
                               proof=_proof("ambiguous"), hypothesis=_hyp(), llm_verdict="confirm")
    assert judgement.verdict == judging.HOLD
    assert judging.promotes(judgement) is False


def test_a_missing_proof_cannot_be_promoted():
    judgement = judging.decide(statistical_verdict=_stat(True), proof=None,
                               hypothesis=_hyp(), llm_verdict="confirm")
    assert judging.promotes(judgement) is False


def test_an_effect_in_the_wrong_direction_is_refused_at_the_judge_too():
    """Defence in depth: if a caller forgets `expected_direction` on
    `acceptance`, the verdict arrives here two-sided and already 'accepted'."""
    hypothesis = build(_payload(predicted_direction="increase"),
                       finding_id="f", target="t")
    losing = Verdict(accepted=True, reason="r", effect_pp=-9.0, floor_pp=1.0,
                     bar_pp=1.5, sign_consistent=4, n=4)
    judgement = judging.decide(statistical_verdict=losing, proof=_proof(),
                               llm_verdict="confirm", hypothesis=hypothesis)
    assert judging.promotes(judgement) is False
    assert "predicted increase" in judgement.reason


# ── Sweep regressions: 15 mutations survived the first suite ─────────────────

def test_an_approval_identity_includes_the_rung():
    """Without it a sub-live `auto_approved` row would satisfy a live gate."""
    from self_learning.approvals import Approval
    base = dict(hypothesis_id="h", experiment_id="e", target="t",
                action_class="config_levers", summary="s", document_id="195")
    assert Approval(rung="PAPER", **base).id != Approval(rung="LIVE_FULL", **base).id


def test_an_approval_identity_includes_the_action_class_and_document():
    """Two proposals from one hypothesis collapsed into one row under
    conflict="update", so a single click answered a question never shown."""
    from self_learning.approvals import Approval
    base = dict(hypothesis_id="h", experiment_id="e", target="t",
                rung="LIVE_FULL", summary="s")
    a = Approval(action_class="config_levers", document_id="179", **base)
    b = Approval(action_class="universe", document_id="179", **base)
    c = Approval(action_class="config_levers", document_id="404", **base)
    assert len({a.id, b.id, c.id}) == 3


def test_an_unknown_rung_is_refused_at_construction():
    """`rung='live_full'` read as sub-live and auto-proceeded on real money."""
    from self_learning.approvals import Approval
    for bad in ("live_full", "LIVE", "LIVE_CAPPED ", ""):
        with pytest.raises(ValueError, match="unknown rung"):
            Approval(hypothesis_id="h", experiment_id="e", target="t",
                     rung=bad, action_class="config_levers", summary="s")


def test_a_messy_stored_status_still_reaches_the_queue_and_can_be_decided():
    """'Pending' was a LIVE row that held forever AND never appeared in the
    queue — the operator could neither see nor clear it."""
    from self_learning.approvals import from_doc, normalise_status, queue_view
    for messy in ("Pending", "", None, "  pending "):
        assert normalise_status(messy) == "pending"
    row = _approval("LIVE_FULL").to_doc()
    row["status"] = "Pending"
    assert queue_view([row])["pending_count"] == 1
    assert from_doc(row).status == "pending"


def test_an_approval_rebuilt_from_a_future_schema_still_works():
    """A blacklist of two keys meant one added field broke the operator's only
    approve path — while live approvals accumulated."""
    from self_learning.approvals import from_doc
    row = _approval().to_doc()
    row["schema_version"] = 1
    row["some_future_field"] = "x"
    assert from_doc(row).rung == "PAPER"


def test_auto_proceed_records_that_nobody_answered():
    """Writing APPROVED would erase the distinction between 'the operator said
    yes' and 'nobody answered'."""
    from self_learning.approvals import EXPIRED_AUTO, auto_proceed
    assert auto_proceed(_approval("PAPER"),
                        now_iso="2026-08-15T09:00:00").status == EXPIRED_AUTO


def test_a_decided_approval_is_not_expirable():
    from self_learning.approvals import is_expired, resolve
    decided = resolve(_approval("PAPER"), decision="approved",
                      now_iso="2026-08-15T01:00:00")
    assert is_expired(decided, now_iso="2030-01-01T00:00:00",
                      timeout_hours=1) is False


def test_an_auto_approved_row_leaves_the_queue():
    from self_learning.approvals import queue_view
    row = dict(_approval().to_doc(), status="auto_approved")
    assert queue_view([row])["pending_count"] == 0


def test_call_role_reports_unparseable_json_as_not_ok():
    """Callers must not receive ok=True with payload=None and then call
    .get() on it."""
    from self_learning.llm import call_role
    resolved = {"learning_judge_llm_provider": "p", "learning_judge_llm_model": "m"}
    result = call_role("judge", resolved, "s", "u", caller=lambda **kw: "no json")
    assert result.ok is False and result.payload is None


def test_a_blank_temperature_does_not_take_down_every_role_call():
    """A cleared UI text box saves ''. `role_config` runs OUTSIDE call_role's
    try, so one blank field took down the Roles tab and every role call."""
    from self_learning.llm import call_role
    resolved = {"learning_judge_llm_provider": "p", "learning_judge_llm_model": "m",
                "learning_judge_temperature": "", "learning_judge_max_output_tokens": ""}
    cfg = role_config(JUDGE, resolved)
    assert cfg.temperature == 0.0 and cfg.max_output_tokens == 1200
    assert call_role("judge", resolved, "s", "u",
                     caller=lambda **kw: '{"verdict": "hold"}').ok is True


def test_the_unconfigured_error_names_the_real_config_key():
    """It said `generator_llm_model_id`; the real key is
    `learning_generator_llm_model_id`, so an operator following the message set
    a key nothing reads."""
    from self_learning.llm import call_role
    result = call_role("generator", {}, "s", "u", caller=lambda **kw: "{}")
    assert "learning_generator_llm_model_id" in result.error


def test_provider_endpoint_config_reaches_the_role():
    """Dropping it sent an Ollama role to localhost — a documented outage."""
    resolved = {"learning_judge_llm_provider": "ollama",
                "learning_judge_llm_model": "m",
                "learning_judge_ollama_base_url": "http://gpu-box:11434"}
    cfg = role_config(JUDGE, resolved)
    assert cfg.provider_config.get("ollama_base_url") == "http://gpu-box:11434"


def test_the_judge_system_prompt_states_the_floor_rule():
    """No test asserted anything about a SYSTEM prompt, so the sentence telling
    the model it cannot promote past the statistics could be deleted freely."""
    from self_learning.prompts import judge_prompt
    system, _user = judge_prompt({"claim": "x"})
    assert "cannot promote" in system
    for verdict in ("confirm", "hold", "demote", "veto"):
        assert verdict in system


def test_the_generator_system_prompt_demands_a_mechanism_and_a_range():
    from self_learning.prompts import generator_prompt
    system, _user = generator_prompt(target="t", levers=[], noise_floor={},
                                     summary={}, refusal_cost={}, findings=[],
                                     rejected=[])
    assert "MECHANISM" in system
    assert "noise floor" in system


def test_prior_rejections_shows_the_newest_first():
    """With a limit, ordering decides WHICH failures the generator is shown."""
    ledger = [{"id": str(i), "status": "rejected", "target": "t",
               "created_at": f"2026-08-{i:02d}"} for i in range(1, 6)]
    rows = prior_rejections(ledger, target="t", limit=2)
    assert [r["id"] for r in rows] == ["5", "4"]
