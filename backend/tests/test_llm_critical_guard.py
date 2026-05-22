import importlib
import os
import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    """Reload module to reset module-level state between tests."""
    from backend import llm_critical_guard
    importlib.reload(llm_critical_guard)
    yield


def _classify(**kw):
    from backend.llm_critical_guard import classify
    return classify(
        status=kw.get("status"),
        body=kw.get("body"),
        exc=kw.get("exc"),
        provider=kw.get("provider", "azure"),
    )


def test_azure_403_blocked_classification():
    tag, crit = _classify(
        status=403,
        body="{'error': {'code': 'Forbidden', 'message': 'Your resource has been temporarily blocked because we detected unusual behavior.'}}",
        provider="azure",
    )
    assert tag == "azure_403_blocked"
    assert crit is True


def test_azure_403_other_message_not_critical():
    tag, crit = _classify(
        status=403,
        body="{'error': {'message': 'content policy violation'}}",
        provider="azure",
    )
    assert tag == "none"
    assert crit is False


def test_openai_403_with_blocked_text_not_critical():
    """Provider scope: 'temporarily blocked' is an Azure-only critical class."""
    tag, crit = _classify(
        status=403,
        body="temporarily blocked",
        provider="openai",
    )
    assert tag == "none"
    assert crit is False


@pytest.mark.parametrize("provider", ["azure", "openai", "anthropic", "gemini"])
def test_auth_failure_any_provider(provider):
    tag, crit = _classify(status=401, body="{'error': 'invalid_api_key'}", provider=provider)
    assert tag == "auth_failure"
    assert crit is True


def test_auth_failure_from_body_text():
    tag, crit = _classify(
        status=None,
        body="authentication failed: bad key",
        provider="openai",
    )
    assert tag == "auth_failure"
    assert crit is True


def test_codex_quota_exhausted():
    tag, crit = _classify(
        status=429,
        body='{"error":"usage_limit_reached","resets_at":"2026-05-29T00:00:00Z"}',
        provider="codex_cli",
    )
    assert tag == "codex_quota_exhausted"
    assert crit is True


def test_codex_quota_string_on_other_provider_not_critical():
    """Quota body text on non-codex provider should NOT trigger codex-class abort."""
    tag, crit = _classify(
        status=429,
        body="usage_limit_reached",
        provider="azure",
    )
    assert tag == "none"
    assert crit is False


def test_5xx_single_not_critical():
    from backend.llm_critical_guard import update_consecutive_state
    tag, crit = _classify(status=503, body="server error", provider="azure")
    # Single 5xx returns "none" until update_consecutive_state has been called 3x
    update_consecutive_state(tag="none", status=503, provider="azure", model="m")
    tag2, crit2 = _classify(status=503, body="server error", provider="azure")
    assert crit2 is False


def test_5xx_three_consecutive_critical():
    from backend.llm_critical_guard import update_consecutive_state, classify
    for _ in range(3):
        update_consecutive_state(tag="none", status=503, provider="azure", model="m")
    # 3rd consecutive 5xx now classifies as critical
    tag, crit = classify(status=503, body="server error", provider="azure")
    assert tag == "provider_5xx_persistent"
    assert crit is True


def test_5xx_reset_on_success():
    from backend.llm_critical_guard import update_consecutive_state, classify
    for _ in range(2):
        update_consecutive_state(tag="none", status=503, provider="azure", model="m")
    update_consecutive_state(tag="none", status=200, provider="azure", model="m")  # success resets
    for _ in range(2):
        update_consecutive_state(tag="none", status=503, provider="azure", model="m")
    tag, crit = classify(status=503, body="x", provider="azure")
    # Only 2 consecutive after reset — not critical yet
    assert crit is False


def test_5xx_reset_on_non_5xx_error():
    from backend.llm_critical_guard import update_consecutive_state, classify
    for _ in range(2):
        update_consecutive_state(tag="none", status=503, provider="azure", model="m")
    update_consecutive_state(tag="none", status=429, provider="azure", model="m")  # non-5xx resets
    for _ in range(2):
        update_consecutive_state(tag="none", status=503, provider="azure", model="m")
    tag, crit = classify(status=503, body="x", provider="azure")
    assert crit is False


def test_5xx_separate_counter_per_provider_model():
    from backend.llm_critical_guard import update_consecutive_state, classify
    for _ in range(2):
        update_consecutive_state(tag="none", status=503, provider="azure", model="m1")
    update_consecutive_state(tag="none", status=503, provider="azure", model="m2")
    # m2 has 1 consecutive; m1 has 2; neither is 3
    tag, crit = classify(status=503, body="x", provider="azure")
    assert crit is False


def test_guard_disabled_env(monkeypatch):
    monkeypatch.setenv("LLM_CRITICAL_GUARD_DISABLED", "1")
    import importlib
    from backend import llm_critical_guard
    importlib.reload(llm_critical_guard)
    tag, crit = llm_critical_guard.classify(
        status=403,
        body="temporarily blocked",
        provider="azure",
    )
    assert tag == "none"
    assert crit is False


def test_is_immediately_fatal_helper():
    from backend.llm_critical_guard import is_immediately_fatal
    assert is_immediately_fatal("azure_403_blocked") is True
    assert is_immediately_fatal("auth_failure") is True
    assert is_immediately_fatal("codex_quota_exhausted") is True
    assert is_immediately_fatal("provider_5xx_persistent") is False
    assert is_immediately_fatal("none") is False


def test_llm_critical_failure_exception_shape():
    from backend.llm_critical_guard import LLMCriticalFailure
    exc = LLMCriticalFailure(
        class_tag="azure_403_blocked",
        provider="azure",
        model="gpt-5.4-mini-MEDIUM",
        attribution={"backtest_id": "357345", "instance_id": "main"},
        attempts=[
            {"attempt": 1, "class_tag": "azure_403_blocked", "http_status": 403, "body_sample": "...", "ts": 1.0},
            {"attempt": 4, "class_tag": "azure_403_blocked", "http_status": 403, "body_sample": "...", "ts": 7.5},
        ],
    )
    assert exc.class_tag == "azure_403_blocked"
    assert exc.provider == "azure"
    assert exc.model == "gpt-5.4-mini-MEDIUM"
    assert exc.attribution["backtest_id"] == "357345"
    assert len(exc.attempts) == 2
    # Idempotency: the exception class exposes a class-level _already_raised flag
    assert hasattr(LLMCriticalFailure, "_already_raised")


def test_already_raised_flag_idempotent():
    from backend.llm_critical_guard import (
        LLMCriticalFailure, mark_raised, was_already_raised, reset_state,
    )
    reset_state()
    assert was_already_raised() is False
    mark_raised()
    assert was_already_raised() is True
    # Second mark is a no-op
    mark_raised()
    assert was_already_raised() is True
    reset_state()
    assert was_already_raised() is False
