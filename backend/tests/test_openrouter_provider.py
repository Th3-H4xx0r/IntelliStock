import pytest

import llm_utils


# ── Task 1: api key + config + meta ─────────────────────────────────────────
def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-xyz")
    assert llm_utils.resolve_api_key_for_provider("openrouter") == "sk-or-xyz"


def test_api_key_explicit_wins(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "envkey")
    assert llm_utils.resolve_api_key_for_provider("openrouter", "explicit") == "explicit"


def test_resolve_config_defaults(monkeypatch):
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    cfg = llm_utils._resolve_provider_config("openrouter", {})
    assert cfg["openrouter_base_url"] == "https://openrouter.ai/api/v1"


def test_resolve_config_headers_and_reasoning():
    cfg = llm_utils._resolve_provider_config("openrouter", {
        "openrouter_referer": "https://intellistock.app",
        "openrouter_title": "IntelliStock",
        "reasoning_effort": "HIGH",
    })
    assert cfg["openrouter_referer"] == "https://intellistock.app"
    assert cfg["openrouter_title"] == "IntelliStock"
    assert cfg["reasoning_effort"] == "high"


def test_resolve_config_base_url_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://proxy.example/api/v1/")
    cfg = llm_utils._resolve_provider_config("openrouter", {})
    assert cfg["openrouter_base_url"] == "https://proxy.example/api/v1"


def test_safe_meta_no_secrets():
    meta = llm_utils._safe_provider_meta("openrouter", {
        "openrouter_base_url": "https://openrouter.ai/api/v1",
        "openrouter_referer": "https://x", "reasoning_effort": "low",
    })
    assert meta["base_url"] == "https://openrouter.ai/api/v1"
    assert "openrouter_referer" not in meta
    assert meta["reasoning_effort"] == "low"


# ── Task 2: structured model build + lock + 404 ─────────────────────────────
@pytest.mark.skipif(not llm_utils._PYDANTIC_AI_AVAILABLE, reason="pydantic_ai not installed")
def test_build_model_openrouter_base_url():
    model = llm_utils._build_pydantic_ai_model(
        "openrouter", "sk-or-x", "anthropic/claude-3.5-sonnet",
        {"openrouter_base_url": "https://openrouter.ai/api/v1"},
    )
    assert model is not None
    blob = " ".join(str(x) for x in (
        getattr(model, "base_url", ""),
        getattr(getattr(model, "client", None), "base_url", ""),
        getattr(getattr(model, "_provider", None), "base_url", ""),
    ))
    assert "openrouter.ai" in blob


@pytest.mark.skipif(not llm_utils._PYDANTIC_AI_AVAILABLE, reason="pydantic_ai not installed")
def test_build_model_openrouter_always_prompted_json_profile():
    # OpenRouter serves many open models (nvidia/nemotron, qwen, llama) whose
    # native structured-output is unreliable, so — like NVIDIA NIM — every
    # openrouter model must get the prompted-JSON profile, NOT only the
    # gpt-oss/gpt-5/kimi "quirky" markers. Regression for a /llm/test failure
    # ("Exceeded maximum output retries (0)") on nvidia/nemotron-3-ultra.
    for model_name in ("nvidia/nemotron-3-ultra-550b-a55b", "anthropic/claude-3.5-sonnet"):
        model = llm_utils._build_pydantic_ai_model(
            "openrouter", "sk-or-x", model_name,
            {"openrouter_base_url": "https://openrouter.ai/api/v1"},
        )
        assert model is not None
        profile = getattr(model, "profile", None)
        assert profile is not None, f"no profile for {model_name}"
        # Prompted-JSON profile disables forced json-schema output.
        assert getattr(profile, "supports_json_schema_output", True) is False, (
            f"{model_name} did not get the prompted-JSON profile"
        )


@pytest.mark.skipif(not llm_utils._PYDANTIC_AI_AVAILABLE, reason="pydantic_ai not installed")
def test_build_model_openrouter_with_headers():
    model = llm_utils._build_pydantic_ai_model(
        "openrouter", "sk-or-x", "anthropic/claude-3.5-sonnet",
        {"openrouter_base_url": "https://openrouter.ai/api/v1",
         "openrouter_referer": "https://intellistock.app",
         "openrouter_title": "IntelliStock"},
    )
    assert model is not None


def test_lock_registered():
    assert "openrouter" in llm_utils._STRUCTURED_LLM_PROVIDER_LOCKS


def test_terminal_not_found_openrouter():
    exc = RuntimeError("HTTP 404: model_not_found")
    assert llm_utils._is_terminal_provider_not_found("openrouter", exc) is True


# ── Task 3: plain path + dispatch ───────────────────────────────────────────
def test_call_openrouter_posts_chat_completions(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        headers = {}
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "hello"}}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "post", _fake_post)
    out = llm_utils._call_openrouter(
        "sk-or-x", "anthropic/claude-3.5-sonnet", "hi",
        referer="https://intellistock.app", title="IntelliStock",
        reasoning_effort="high",
    )
    assert out == "hello"
    assert captured["url"].endswith("/chat/completions")
    assert captured["url"].startswith("https://openrouter.ai/api/v1")
    assert captured["headers"]["Authorization"] == "Bearer sk-or-x"
    assert captured["headers"]["HTTP-Referer"] == "https://intellistock.app"
    assert captured["headers"]["X-Title"] == "IntelliStock"
    assert captured["body"]["reasoning_effort"] == "high"
    assert captured["body"]["reasoning"] == {"effort": "high"}


def test_call_openrouter_no_headers_when_unset(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        headers = {}
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["headers"] = headers
        captured["body"] = json
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "post", _fake_post)
    out = llm_utils._call_openrouter("sk-or-x", "openai/gpt-4o-mini", "hi")
    assert out == "ok"
    assert "HTTP-Referer" not in captured["headers"]
    assert "X-Title" not in captured["headers"]
    assert "reasoning_effort" not in captured["body"]


def test_call_openrouter_empty_key_returns_empty():
    assert llm_utils._call_openrouter("", "openai/gpt-4o-mini", "hi") == ""
