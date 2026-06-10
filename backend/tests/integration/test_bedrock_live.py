"""Opt-in live integration test for the Amazon Bedrock provider.

Requires a valid Bedrock API key + region with access to the test model.
Skipped unless ``RUN_BEDROCK_LIVE=1`` in the environment.

Run with:

    export BEDROCK_API_KEY=...           # Bedrock API key (bearer token)
    export BEDROCK_REGION=us-east-1
    export BEDROCK_TEST_MODEL=us.anthropic.claude-3-5-sonnet-20241022-v2:0
    RUN_BEDROCK_LIVE=1 python3 -m pytest \\
        backend/tests/integration/test_bedrock_live.py -v
"""
import os
import pytest

from pydantic import BaseModel


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BEDROCK_LIVE") != "1",
    reason="Set RUN_BEDROCK_LIVE=1 to enable; requires a real Bedrock API key + region",
)

_API_KEY = os.environ.get("BEDROCK_API_KEY", "")
_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
_MODEL = os.environ.get("BEDROCK_TEST_MODEL", "us.anthropic.claude-3-5-sonnet-20241022-v2:0")


def _cfg():
    return {"bedrock_region": _REGION}


def test_plain_text_chat():
    """call_llm_by_provider → _call_bedrock → Bedrock Converse roundtrip."""
    from llm_utils import call_llm_by_provider
    out = call_llm_by_provider(
        provider="bedrock", api_key=_API_KEY, model=_MODEL,
        prompt="Reply with exactly the word PONG.",
        max_output_tokens=16, provider_config=_cfg(),
    )
    assert isinstance(out, str) and out.strip()


def test_structured_output():
    """call_structured_llm_by_provider → PydanticAI BedrockConverseModel roundtrip."""
    from llm_utils import call_structured_llm_by_provider

    class Answer(BaseModel):
        answer: str

    out = call_structured_llm_by_provider(
        provider="bedrock", api_key=_API_KEY, model=_MODEL,
        prompt='Reply as JSON: {"answer": "PONG"}',
        output_type=Answer, provider_config=_cfg(),
    )
    assert isinstance(out, Answer)
    assert out.answer


def test_tool_calling():
    """Claude on Bedrock supports Converse tool use."""
    from llm_utils import call_bedrock_with_tools
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
    result = call_bedrock_with_tools(
        api_key=_API_KEY, model=_MODEL,
        prompt="What's the weather in San Francisco? Call the get_weather tool.",
        tools=tools, region=_REGION,
    )
    assert "text" in result
    assert "tool_calls" in result


def test_discovery_best_effort():
    """list_models may 401 for a narrowly-scoped key — that's acceptable;
    we only assert it returns a list or raises a typed auth error."""
    import bedrock_client
    try:
        models = bedrock_client.list_models(_API_KEY, _REGION)
        assert isinstance(models, list)
    except bedrock_client.BedrockAuthError:
        pytest.skip("API key lacks bedrock:ListFoundationModels — discovery unavailable (expected for scoped keys)")
