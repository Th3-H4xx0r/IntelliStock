"""Opt-in live integration test for the Ollama provider.

Requires a local Ollama server (``ollama serve``) with ``llama3.2``
pulled. Skipped unless ``RUN_OLLAMA_LIVE=1`` in the environment.

Run with:

    ollama pull llama3.2
    RUN_OLLAMA_LIVE=1 python3 -m pytest \\
        backend/tests/integration/test_ollama_live.py -v
"""
import os
import pytest

from pydantic import BaseModel


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_OLLAMA_LIVE") != "1",
    reason="Set RUN_OLLAMA_LIVE=1 to enable; requires local Ollama + llama3.2",
)


def test_plain_text_chat():
    """Full call_llm_by_provider → _call_ollama → Ollama server roundtrip."""
    from llm_utils import call_llm_by_provider
    out = call_llm_by_provider(
        provider="ollama",
        api_key="",
        model="llama3.2",
        prompt="Reply with exactly the word PONG.",
        max_output_tokens=16,
        provider_config={"ollama_base_url": "http://localhost:11434"},
    )
    assert isinstance(out, str) and out.strip()


def test_structured_output():
    """Full call_structured_llm_by_provider → PydanticAI → /v1 roundtrip."""
    from llm_utils import call_structured_llm_by_provider

    class Answer(BaseModel):
        answer: str

    out = call_structured_llm_by_provider(
        provider="ollama",
        api_key="",
        model="llama3.2",
        prompt='Reply as JSON: {"answer": "PONG"}',
        output_type=Answer,
        provider_config={"ollama_base_url": "http://localhost:11434"},
    )
    assert isinstance(out, Answer)
    assert out.answer


def test_tool_calling():
    """qwen2.5 supports tools; skip if not installed."""
    import urllib.request
    import json
    try:
        with urllib.request.urlopen(
            "http://localhost:11434/api/tags", timeout=4
        ) as resp:
            tags = json.loads(resp.read().decode())
    except Exception as e:
        pytest.skip(f"Cannot reach Ollama: {e}")

    names = {m.get("name", "") for m in (tags.get("models") or [])}
    if not any(n.startswith("qwen2.5") for n in names):
        pytest.skip("qwen2.5 not installed; `ollama pull qwen2.5` to enable")

    qwen_name = next(n for n in names if n.startswith("qwen2.5"))

    from llm_utils import call_ollama_with_tools
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }]
    result = call_ollama_with_tools(
        api_key="",
        model=qwen_name,
        prompt="What's the weather in San Francisco? Call the get_weather tool.",
        tools=tools,
        base_url="http://localhost:11434",
    )
    assert "text" in result
    assert "tool_calls" in result
