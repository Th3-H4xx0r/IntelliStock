"""Task 4: role-scoped LLM criticality — article-enrichment roles degrade, decision roles halt.

Regression guard for the 2026-06-22/23 incident: the `macro_article` role
(codex-cli quota exhaustion) flipped the whole-instance kill switch
(runCommand=False) and cost alpaca-main two trading days. Macro/company article
classification is an enrichment signal — its critical LLM failure must degrade
the run (empty signal), never halt live trading.

SAFETY-CRITICAL DIRECTION: the inverse bug (a *decision*-role failure that no
longer halts) is the failure mode to avoid. Anything not in the four explicit
article roles — including None/unknown — MUST remain halt-worthy (fail-safe).
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.modules.setdefault("socketio", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm_critical_guard import role_is_halt_worthy, LLMCriticalFailure  # noqa: E402
import live_critical_abort  # noqa: E402


def _make_failure(*, class_tag, provider, role):
    """Build an LLMCriticalFailure using the REAL keyword-only ctor
    (class_tag / provider / model / attribution / attempts) found in Step 1,
    plus the new optional role= attribute."""
    return LLMCriticalFailure(
        class_tag=class_tag,
        provider=provider,
        model="m",
        attribution={},
        attempts=[{"attempt": 1, "class_tag": class_tag, "body_sample": "boom"}],
        role=role,
    )


def setup_function(_fn):
    live_critical_abort.reset_state()


def teardown_function(_fn):
    live_critical_abort.reset_state()


def test_article_roles_do_not_halt():
    for role in ("macro_article", "company_article",
                 "lookback_macro_article", "lookback_company_article"):
        assert role_is_halt_worthy(role) is False


def test_decision_and_unknown_roles_halt():
    for role in ("llm", "sentiment", "overlay", "event_maintenance", None, "??", ""):
        assert role_is_halt_worthy(role) is True


def test_llm_critical_failure_defaults_role_none():
    exc = LLMCriticalFailure(
        class_tag="auth_failure", provider="openrouter", model="m",
        attribution={}, attempts=[],
    )
    assert exc.role is None
    # role=None is fail-safe → still halt-worthy
    assert role_is_halt_worthy(exc.role) is True


def test_handle_degrades_for_article_role():
    failure = _make_failure(
        class_tag="codex_quota_exhausted", provider="codex-cli", role="macro_article",
    )
    with patch.object(live_critical_abort, "_halt_live_trading") as halt, \
            patch.object(live_critical_abort, "_alert_strategy_error") as alert:
        live_critical_abort.handle(instance_id="alpaca-main", failure=failure)
        halt.assert_not_called()
        # degrade still pages the operator once (idempotence machinery reused)
        alert.assert_called_once()


def test_handle_still_halts_for_decision_role():
    failure = _make_failure(
        class_tag="auth_failure", provider="openrouter", role="llm",
    )
    with patch.object(live_critical_abort, "_halt_live_trading") as halt:
        live_critical_abort.handle(instance_id="alpaca-main", failure=failure)
        halt.assert_called_once()


def test_handle_still_halts_for_none_role():
    """A failure with no role attribution (unknown provenance) must halt."""
    failure = LLMCriticalFailure(
        class_tag="auth_failure", provider="azure", model="m",
        attribution={}, attempts=[],
    )
    with patch.object(live_critical_abort, "_halt_live_trading") as halt:
        live_critical_abort.handle(instance_id="alpaca-main", failure=failure)
        halt.assert_called_once()


def test_strategy_degrade_rearms_the_critical_guard():
    """Strategy-side survive path: after an article-role failure is degraded,
    the process-wide critical guard MUST be re-armed. mark_raised() latched
    _already_raised=True before the raise; leaving it latched would silently
    disable escalation for later decision-role calls (inverse bug)."""
    import llm_critical_guard as g
    try:
        import strategies.graph_nexus_analysis as gna
    except Exception as _imp:  # pragma: no cover - heavy import guard
        import pytest
        pytest.skip(f"graph_nexus_analysis import unavailable: {_imp}")

    g.reset_state()
    g.mark_raised()
    assert g.was_already_raised() is True
    gna._ARTICLE_CRITICAL_DEGRADE_ALERTED = False
    exc = _make_failure(
        class_tag="codex_quota_exhausted", provider="codex-cli", role="macro_article",
    )
    with patch("live_alerts.alert_strategy_error") as _alert:
        gna._alert_article_critical_degrade(exc, instance_id="alpaca-main", stage="Macro article")
    assert g.was_already_raised() is False, "guard must be re-armed after enrichment degrade"
    g.reset_state()


def test_handle_degrade_is_idempotent():
    """Second call is a no-op (shared _already_alerted machinery)."""
    failure = _make_failure(
        class_tag="codex_quota_exhausted", provider="codex-cli", role="company_article",
    )
    with patch.object(live_critical_abort, "_halt_live_trading") as halt, \
            patch.object(live_critical_abort, "_alert_strategy_error") as alert:
        live_critical_abort.handle(instance_id="alpaca-main", failure=failure)
        live_critical_abort.handle(instance_id="alpaca-main", failure=failure)
        halt.assert_not_called()
        alert.assert_called_once()
