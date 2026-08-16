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


def test_the_same_claim_on_the_same_target_is_the_same_hypothesis():
    a = build(_payload(), finding_id="f1", target="t")
    b = build(_payload(), finding_id="f2", target="t")
    assert a.id == b.id


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


def test_the_judge_cannot_promote_what_the_statistics_rejected():
    """THE load-bearing rule of the whole phase."""
    judgement = judging.decide(statistical_verdict=_stat(False, "inside the floor"),
                               proof=_proof(), llm_verdict="confirm",
                               llm_reason="looks good to me")
    assert judgement.verdict == judging.HOLD
    assert judgement.overridden is True
    assert "never raise it" in judgement.reason
    assert judging.promotes(judgement) is False


def test_the_judge_may_veto_something_the_statistics_accepted():
    judgement = judging.decide(statistical_verdict=_stat(True),
                               proof=_proof(), llm_verdict="veto",
                               llm_reason="the win is one position")
    assert judgement.verdict == judging.VETO
    assert judging.promotes(judgement) is False


def test_a_clean_pass_promotes():
    judgement = judging.decide(statistical_verdict=_stat(True), proof=_proof(),
                               llm_verdict="confirm")
    assert judging.promotes(judgement) is True


def test_an_inert_treatment_is_held_not_rejected():
    """Calling it rejected would retire a hypothesis that was never tested —
    the exact error that turned 13 inert levers into 13 'no effect' results."""
    judgement = judging.decide(statistical_verdict=_stat(True),
                               proof=_proof("inert"), llm_verdict="confirm")
    assert judgement.verdict == judging.HOLD
    assert "not testable as specified" in judgement.reason


def test_an_unprovable_treatment_is_also_held():
    judgement = judging.decide(statistical_verdict=_stat(True),
                               proof=_proof("unprovable"), llm_verdict="confirm")
    assert judgement.verdict == judging.HOLD


def test_an_unparseable_judgement_defaults_to_hold_not_approval():
    judgement = judging.decide(statistical_verdict=_stat(True), proof=_proof(),
                               llm_verdict="LGTM ship it")
    assert judgement.verdict == judging.HOLD
    assert "unparseable" in judgement.reason


def test_a_missing_judgement_defaults_to_hold():
    judgement = judging.decide(statistical_verdict=_stat(True), proof=_proof(),
                               llm_verdict=None)
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
