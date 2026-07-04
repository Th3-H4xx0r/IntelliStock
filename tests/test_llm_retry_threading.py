"""The raw-structured fallback path must forward the caller's retry budget
to call_llm_by_provider instead of hardcoding retries=0 (run-185254 finding:
retry_count=0 on all 1062 calls; OpenRouter backoff never exercised)."""
from unittest.mock import patch

import backend.llm_utils as llm_utils


def test_raw_structured_forwards_retries():
    captured = {}

    def fake_call(provider, api_key, model, prompt, **kwargs):
        captured.update(kwargs)
        return '{"ok": true}'

    with patch.object(llm_utils, "call_llm_by_provider", side_effect=fake_call):
        llm_utils._try_raw_structured_json_once(
            "openrouter", "k", "m", "p", dict,
            max_output_tokens=1024, timeout_sec=30, retries=2,
        )
    assert captured.get("retries") == 2


def test_raw_structured_default_stays_zero():
    captured = {}

    def fake_call(provider, api_key, model, prompt, **kwargs):
        captured.update(kwargs)
        return '{"ok": true}'

    with patch.object(llm_utils, "call_llm_by_provider", side_effect=fake_call):
        llm_utils._try_raw_structured_json_once(
            "openrouter", "k", "m", "p", dict,
            max_output_tokens=1024, timeout_sec=30,
        )
    assert captured.get("retries") == 0


def test_openrouter_models_get_request_limiter():
    lim = llm_utils._get_model_request_rate_limiter(
        "nvidia/nemotron-3-ultra-550b-a55b", "openrouter"
    )
    assert lim is not None


def test_unknown_provider_still_none():
    assert llm_utils._get_model_request_rate_limiter("whatever", "bedrock") is None
