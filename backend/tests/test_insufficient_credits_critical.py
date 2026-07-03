"""Round-2 Task 2: 402 = insufficient_credits is a role-INDEPENDENT critical class.

Backtest 586767's OpenRouter credits ran out mid-run; 195 calls failed HTTP 402
("This request requires more credits ... can only afford 10773") and the strategy
silently traded LLM-blind for a full simulated month. 402 was unclassified.

insufficient_credits must OVERRIDE the Round-1 role-scoped degrade: no role can
run without credits, so it is role-INDEPENDENT fatal — even an article-enrichment
role (macro_article / company_article / lookback_*) must HALT, never degrade.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.modules.setdefault("socketio", MagicMock())
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm_critical_guard import (  # noqa: E402
    classify,
    is_immediately_fatal,
    failure_is_role_independent,
    LLMCriticalFailure,
)
import live_critical_abort  # noqa: E402


@pytest.fixture(autouse=True)
def cage_alerts(monkeypatch):
    """Defect 1 (test hygiene): a real production Discord alert escaped during a
    prior run of this file — a test patched `_halt_live_trading` but left the
    real `_alert_strategy_error` to compose a message from MagicMock halt-summary
    values and push it through the live notification stack (which loads real
    webhook/config from the repo .env).

    This AUTOUSE fixture cages EVERY production side effect reachable from this
    file — notifications (Discord/push/RethinkDB outbox) AND the live kill
    switch (runCommand=False + cancel live orders in prod RethinkDB) — for
    every test, no matter what an individual test leaves unmocked.

    SEAM: both alert paths exercised here reach the sender via a LAZY
    `from live_alerts import alert_strategy_error` resolved at CALL time
    (live_critical_abort._alert_strategy_error AND
    graph_nexus_analysis._alert_article_critical_degrade). The name is looked up
    in the `live_alerts` module namespace at call time, so patching
    `live_alerts.alert_strategy_error` there intercepts BOTH paths — this is the
    where-it's-looked-up seam, not the where-it's-defined one. We ALSO cage the
    deeper boundary (`live_alerts.notify` + `notifications.notify`) so any other
    alert_* helper (e.g. alert_halt fired inside a real halt) and any direct
    notify() caller can never reach Discord/push/RethinkDB regardless of what
    else a test mocks.

    HALT SEAM: handle() reaches the kill switch through the module-level
    `live_critical_abort._halt_live_trading`, which itself does a lazy
    call-time `from live_kill_switch import halt_live_trading`. We stub BOTH
    seams: the module-level name (so a test that forgets its own
    `patch.object(live_critical_abort, "_halt_live_trading")` cannot flip the
    real kill switch) and `live_kill_switch.halt_live_trading` (so even a
    direct lazy import elsewhere is caged). Per-test `patch.object` mocks
    layer on top of the fixture stub and restore it on exit — their own
    assert_called/assert_not_called contracts are unaffected.

    Returns a recorder dict so a test can PROVE the stub — not the real sender
    or kill switch — captured the call.
    """
    import live_alerts
    import live_kill_switch
    import notifications

    calls = {"alert_strategy_error": [], "notify": [], "halt": []}

    def _rec_alert(**kw):
        calls["alert_strategy_error"].append(kw)

    def _rec_notify(*a, **kw):
        calls["notify"].append({"args": a, "kwargs": kw})

    def _rec_halt(**kw):
        calls["halt"].append(kw)
        return {"instances_halted": 0, "orders_canceled": 0}

    monkeypatch.setattr(live_alerts, "alert_strategy_error", _rec_alert)
    monkeypatch.setattr(live_alerts, "notify", _rec_notify)
    monkeypatch.setattr(notifications, "notify", _rec_notify)
    monkeypatch.setattr(live_critical_abort, "_halt_live_trading", _rec_halt)
    monkeypatch.setattr(live_kill_switch, "halt_live_trading", _rec_halt)
    return calls


def _make_failure(*, class_tag, provider, role, body_sample="boom"):
    return LLMCriticalFailure(
        class_tag=class_tag,
        provider=provider,
        model="m",
        attribution={},
        attempts=[{"attempt": 1, "class_tag": class_tag, "body_sample": body_sample}],
        role=role,
    )


def setup_function(_fn):
    live_critical_abort.reset_state()


def teardown_function(_fn):
    live_critical_abort.reset_state()


# --- (a) classify 402 → insufficient_credits, immediately fatal ---

def test_classify_402_is_insufficient_credits_and_fatal():
    tag, critical = classify(
        status=402,
        body="This request requires more credits, or fewer tokens. You can only afford 10773.",
        provider="openrouter",
        model="nemotron",
    )
    assert tag == "insufficient_credits"
    assert critical is True
    assert is_immediately_fatal("insufficient_credits") is True


# --- (d) body-regex variant with NO status code ---

def test_classify_body_regex_without_status():
    for body in (
        "This request requires more credits.",
        "You can only afford 10773 tokens right now.",
    ):
        tag, critical = classify(status=None, body=body, provider="openrouter")
        assert tag == "insufficient_credits", body
        assert critical is True


def test_insufficient_credits_is_role_independent():
    for role in ("macro_article", "company_article",
                 "lookback_macro_article", "lookback_company_article", None, "llm"):
        f = _make_failure(class_tag="insufficient_credits", provider="openrouter", role=role)
        assert failure_is_role_independent(f) is True


def test_other_classes_are_not_role_independent():
    for tag in ("auth_failure", "codex_quota_exhausted", "azure_403_blocked",
                "provider_5xx_persistent", "none"):
        f = _make_failure(class_tag=tag, provider="openrouter", role="macro_article")
        assert failure_is_role_independent(f) is False


# --- (b) handle() with article role + insufficient_credits → HALT (not degrade) ---

def test_handle_halts_for_article_role_when_insufficient_credits(cage_alerts):
    failure = _make_failure(
        class_tag="insufficient_credits", provider="openrouter", role="macro_article",
    )
    with patch.object(live_critical_abort, "_halt_live_trading") as halt:
        live_critical_abort.handle(instance_id="alpaca-main", failure=failure)
        halt.assert_called_once()
        # HALT reason carries the class tag.
        assert halt.call_args.kwargs["reason"] == "LLM critical: insufficient_credits"
    # Exactly one diagnostic alert, captured by the cage (never the wire).
    assert len(cage_alerts["alert_strategy_error"]) == 1
    body = cage_alerts["alert_strategy_error"][0]["message"]
    assert "OpenRouter credits exhausted — top up at openrouter.ai/settings/credits" in body
    assert cage_alerts["notify"] == []


def test_handle_halts_for_company_and_lookback_roles(cage_alerts):
    for role in ("company_article", "lookback_macro_article", "lookback_company_article"):
        live_critical_abort.reset_state()
        cage_alerts["halt"].clear()
        failure = _make_failure(
            class_tag="insufficient_credits", provider="openrouter", role=role,
        )
        live_critical_abort.handle(instance_id="alpaca-main", failure=failure)
        assert cage_alerts["halt"], f"role={role} must halt on insufficient_credits"


# --- (c) auth_failure with article role STILL degrades (Round-1 preserved) ---

def test_auth_failure_article_role_still_degrades(cage_alerts):
    failure = _make_failure(
        class_tag="auth_failure", provider="openrouter", role="macro_article",
    )
    with patch.object(live_critical_abort, "_halt_live_trading") as halt:
        live_critical_abort.handle(instance_id="alpaca-main", failure=failure)
        halt.assert_not_called()
    # Degrade still pages once, through the cage.
    assert len(cage_alerts["alert_strategy_error"]) == 1
    assert cage_alerts["notify"] == []
