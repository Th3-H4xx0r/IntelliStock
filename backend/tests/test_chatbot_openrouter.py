"""Chatbot (dock) LLM dispatch must route the `openrouter` provider through the
OpenAI-compatible path, not fall through to Gemini. Regression for: an
OpenRouter model in the dock called the Gemini API ("API key not valid")."""
from chatbot import llm as chatbot_llm


def test_openai_compat_url_openrouter_default():
    url, headers = chatbot_llm._openai_compat_url("openrouter", None, None, None, "nvidia/nemotron-3-ultra-550b-a55b")
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Content-Type"] == "application/json"


def test_openai_compat_url_openrouter_custom_base():
    url, _ = chatbot_llm._openai_compat_url("openrouter", "https://proxy.example/api/v1", None, None, "x/y")
    assert url == "https://proxy.example/api/v1/chat/completions"


def test_call_chat_with_tools_routes_openrouter_to_openai_compat(monkeypatch):
    captured = {}

    def _fake_compat(**kwargs):
        captured.update(kwargs)
        return {"content": "ok", "tool_calls": [], "finish_reason": "stop", "raw": {}}

    def _fake_gemini(**kwargs):
        captured["WENT_TO_GEMINI"] = True
        return {"content": "", "tool_calls": [], "finish_reason": "", "raw": {}}

    monkeypatch.setattr(chatbot_llm, "_call_openai_compat", _fake_compat)
    monkeypatch.setattr(chatbot_llm, "_call_gemini", _fake_gemini)

    out = chatbot_llm.call_chat_with_tools(
        provider="openrouter",
        api_key="sk-or-x",
        model="nvidia/nemotron-3-ultra-550b-a55b",
        messages=[{"role": "user", "content": "hi"}],
        base_url="https://openrouter.ai/api/v1",
    )
    assert out["content"] == "ok"
    assert "WENT_TO_GEMINI" not in captured
    assert captured["provider"] == "openrouter"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
