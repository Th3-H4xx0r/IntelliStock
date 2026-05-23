"""Tests that the existing critical-guard classifier handles Ollama correctly
without any provider-specific code.

The existing rule ``status == 401 → auth_failure (critical)`` is
provider-agnostic, so Ollama Cloud's 401 already classifies correctly.
The per-(provider, model) 5xx counter likewise participates
automatically once Ollama appears in dispatch.

This file ONLY contains tests — no production code change is required.
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_guard():
    from llm_critical_guard import reset_state
    reset_state()
    yield
    reset_state()


def test_classify_ollama_401_is_auth_failure_critical():
    """An Ollama Cloud 401 must classify as auth_failure (critical)."""
    from llm_critical_guard import classify
    tag, is_critical = classify(
        status=401, body="unauthorized", exc=None,
        provider="ollama", model="llama3.2",
    )
    assert tag == "auth_failure"
    assert is_critical is True


def test_classify_ollama_local_404_is_not_critical():
    """Model-not-installed (404) is user config, not a provider outage."""
    from llm_critical_guard import classify
    tag, is_critical = classify(
        status=404, body="model not found", exc=None,
        provider="ollama", model="ghost",
    )
    assert tag == "none"
    assert is_critical is False


def test_classify_ollama_persistent_5xx_after_three():
    """Three consecutive 5xx for the same (provider, model) → critical.

    The existing per-(provider, model) counter participates automatically
    — no Ollama-specific code required.
    """
    from llm_critical_guard import classify, update_consecutive_state

    # First 2 5xx — counter rises but not yet critical.
    for status in (500, 502):
        update_consecutive_state(
            tag="provider_5xx_persistent", status=status,
            provider="ollama", model="llama3.2",
        )
        tag, is_critical = classify(
            status=status, body="oops", exc=None,
            provider="ollama", model="llama3.2",
        )
        assert is_critical is False
    # 3rd 5xx — should now be critical.
    update_consecutive_state(
        tag="provider_5xx_persistent", status=503,
        provider="ollama", model="llama3.2",
    )
    tag, is_critical = classify(
        status=503, body="oops", exc=None,
        provider="ollama", model="llama3.2",
    )
    assert tag == "provider_5xx_persistent"
    assert is_critical is True


def test_classify_ollama_200_resets_5xx_counter():
    from llm_critical_guard import classify, update_consecutive_state
    # Build up the counter to 2.
    for status in (500, 502):
        update_consecutive_state(
            tag="provider_5xx_persistent", status=status,
            provider="ollama", model="llama3.2",
        )
    # Success resets the (ollama, llama3.2) entry.
    update_consecutive_state(
        tag="none", status=200,
        provider="ollama", model="llama3.2",
    )
    # A single 500 should now NOT be critical (counter was reset).
    update_consecutive_state(
        tag="provider_5xx_persistent", status=500,
        provider="ollama", model="llama3.2",
    )
    tag, is_critical = classify(
        status=500, body="oops", exc=None,
        provider="ollama", model="llama3.2",
    )
    assert is_critical is False


def test_classify_ollama_5xx_outage_does_not_bleed_into_other_provider():
    """Per-(provider, model) scoping: an Ollama outage must not make
    Gemini's first 5xx classify as critical."""
    from llm_critical_guard import classify, update_consecutive_state
    # 3 5xx on ollama brings ollama to critical.
    for status in (500, 502, 503):
        update_consecutive_state(
            tag="provider_5xx_persistent", status=status,
            provider="ollama", model="llama3.2",
        )
    # Gemini's first 5xx must still be non-critical.
    update_consecutive_state(
        tag="provider_5xx_persistent", status=500,
        provider="gemini", model="gemini-2.5-flash",
    )
    tag, is_critical = classify(
        status=500, body="oops", exc=None,
        provider="gemini", model="gemini-2.5-flash",
    )
    assert is_critical is False
