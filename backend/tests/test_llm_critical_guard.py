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
        model=kw.get("model", ""),
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
    # 3rd consecutive 5xx now classifies as critical — must pass the same
    # (provider, model) to classify() since the counter is per-key now.
    tag, crit = classify(status=503, body="server error", provider="azure", model="m")
    assert tag == "provider_5xx_persistent"
    assert crit is True


def test_5xx_reset_on_success():
    from backend.llm_critical_guard import update_consecutive_state, classify
    for _ in range(2):
        update_consecutive_state(tag="none", status=503, provider="azure", model="m")
    update_consecutive_state(tag="none", status=200, provider="azure", model="m")  # success resets this key
    for _ in range(2):
        update_consecutive_state(tag="none", status=503, provider="azure", model="m")
    tag, crit = classify(status=503, body="x", provider="azure", model="m")
    # Only 2 consecutive after reset — not critical yet
    assert crit is False


def test_5xx_reset_on_non_5xx_error():
    from backend.llm_critical_guard import update_consecutive_state, classify
    for _ in range(2):
        update_consecutive_state(tag="none", status=503, provider="azure", model="m")
    update_consecutive_state(tag="none", status=429, provider="azure", model="m")  # non-5xx resets this key
    for _ in range(2):
        update_consecutive_state(tag="none", status=503, provider="azure", model="m")
    tag, crit = classify(status=503, body="x", provider="azure", model="m")
    assert crit is False


def test_5xx_separate_counter_per_provider_model():
    """Hitting 3× 5xx on azure-m1 must NOT make a single openai-m2 5xx critical.

    Previously (buggy) the classifier used `any(v >= 3 for v in values())`
    so m1's history would trip m2. Now classify() looks up the exact
    (provider, model) tuple.
    """
    from backend.llm_critical_guard import update_consecutive_state, classify
    # m1 trips at 3
    for _ in range(3):
        update_consecutive_state(tag="none", status=503, provider="azure", model="m1")
    # m2 hits its first 5xx
    update_consecutive_state(tag="none", status=503, provider="azure", model="m2")
    tag, crit = classify(status=503, body="x", provider="azure", model="m2")
    # m2 has 1 consecutive; m1 has 3. Lookup is per-key now → m2 is NOT critical.
    assert tag == "none"
    assert crit is False
    # Sanity: m1 IS still critical when queried with its own key.
    tag1, crit1 = classify(status=503, body="x", provider="azure", model="m1")
    assert tag1 == "provider_5xx_persistent"
    assert crit1 is True


def test_5xx_one_provider_does_not_trip_another():
    """Azure 2× 5xx history, then OpenAI hits its first 5xx — OpenAI must NOT
    be classified critical. Regression test for Bug 2 (per-(provider, model)
    scoping of the 5xx counter check in classify())."""
    from backend.llm_critical_guard import update_consecutive_state, classify
    for _ in range(2):
        update_consecutive_state(tag="none", status=503, provider="azure", model="gpt-5.4-mini")
    update_consecutive_state(tag="none", status=503, provider="openai", model="gpt-4")
    tag, crit = classify(status=503, body="server error", provider="openai", model="gpt-4")
    assert tag == "none"
    assert crit is False


def test_5xx_one_provider_success_does_not_reset_another():
    """A successful Gemini call must NOT zero Azure's near-trip 5xx history.

    Regression test for Bug 3 (per-(provider, model) reset semantics in
    update_consecutive_state). Previously a single non-5xx response would
    `_consecutive_5xx.clear()` and forgive every provider's history.
    """
    from backend.llm_critical_guard import update_consecutive_state, classify
    # Azure builds up 2× 5xx history
    for _ in range(2):
        update_consecutive_state(tag="none", status=503, provider="azure", model="gpt-5.4-mini")
    # Gemini has a successful response — must NOT touch the azure counter
    update_consecutive_state(tag="none", status=200, provider="gemini", model="gemini-1.5-pro")
    # Azure's 3rd 5xx should still trip critical
    update_consecutive_state(tag="none", status=503, provider="azure", model="gpt-5.4-mini")
    tag, crit = classify(status=503, body="x", provider="azure", model="gpt-5.4-mini")
    assert tag == "provider_5xx_persistent"
    assert crit is True


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
