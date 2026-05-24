"""Tests for the Bedrock call paths + config helpers in backend/llm_utils.py."""
from unittest.mock import MagicMock

import llm_utils


# ────────────────────────── _normalize_bedrock_reasoning ────────────────────


def test_reasoning_off_returns_none():
    assert llm_utils._normalize_bedrock_reasoning("off", "us.anthropic.claude-3-7-sonnet-20250219-v1:0") is None
    assert llm_utils._normalize_bedrock_reasoning("", "anthropic.claude-3-5-sonnet-20241022-v2:0") is None


def test_reasoning_claude_maps_to_budget():
    out = llm_utils._normalize_bedrock_reasoning("medium", "us.anthropic.claude-3-7-sonnet-20250219-v1:0")
    assert out == {"reasoning_config": {"type": "enabled", "budget_tokens": 4096}}
    hi = llm_utils._normalize_bedrock_reasoning("high", "anthropic.claude-opus-4-20250514-v1:0")
    assert hi["reasoning_config"]["budget_tokens"] == 16384
    lo = llm_utils._normalize_bedrock_reasoning("low", "anthropic.claude-3-7-sonnet-20250219-v1:0")
    assert lo["reasoning_config"]["budget_tokens"] == 1024


def test_reasoning_omitted_for_unsupported_families():
    # Llama / Nova / Mistral take neither reasoning_config nor reasoning_effort — omit.
    assert llm_utils._normalize_bedrock_reasoning("high", "meta.llama3-1-70b-instruct-v1:0") is None
    assert llm_utils._normalize_bedrock_reasoning("high", "amazon.nova-pro-v1:0") is None


def test_reasoning_gpt_oss_uses_reasoning_effort():
    # OpenAI gpt-oss on Bedrock takes the OpenAI Chat-Completion `reasoning_effort`
    # field (verified honored live: high produces ~7x the reasoning of low).
    assert llm_utils._normalize_bedrock_reasoning("medium", "openai.gpt-oss-120b-1:0") == {"reasoning_effort": "medium"}
    assert llm_utils._normalize_bedrock_reasoning("high", "openai.gpt-oss-20b-1:0") == {"reasoning_effort": "high"}
    assert llm_utils._normalize_bedrock_reasoning("low", "openai.gpt-oss-120b-1:0") == {"reasoning_effort": "low"}
    assert llm_utils._normalize_bedrock_reasoning("off", "openai.gpt-oss-120b-1:0") is None


# ────────────────────────── config helpers ──────────────────────────────────


def test_resolve_api_key_bedrock_env(monkeypatch):
    monkeypatch.setenv("BEDROCK_API_KEY", "envkey")
    assert llm_utils.resolve_api_key_for_provider("bedrock", None) == "envkey"
    assert llm_utils.resolve_api_key_for_provider("bedrock", "explicit") == "explicit"


def test_cache_effort_key_bedrock_isolates_reasoning():
    k_off = llm_utils._cache_effort_key("bedrock", {"bedrock_reasoning": "off"})
    k_hi = llm_utils._cache_effort_key("bedrock", {"bedrock_reasoning": "high"})
    assert k_off != k_hi
    assert k_hi.startswith("reason:")
    assert k_off == ""  # off behaves like "no effort set"


def test_resolve_provider_config_bedrock_keeps_region_reasoning():
    cfg = llm_utils._resolve_provider_config("bedrock", {"bedrock_region": "us-west-2", "bedrock_reasoning": "Low"})
    assert cfg["bedrock_region"] == "us-west-2"
    assert cfg["bedrock_reasoning"] == "low"
    assert "reasoning_effort" not in cfg


def test_safe_provider_meta_bedrock():
    meta = llm_utils._safe_provider_meta("bedrock", {"bedrock_region": "eu-central-1", "bedrock_reasoning": "high"})
    assert meta["bedrock_region"] == "eu-central-1"
    assert meta["bedrock_reasoning"] == "high"


# ────────────────────────── Converse fakes ──────────────────────────────────


class _FakeConverseClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        if self._error:
            raise self._error
        return self._response


def _ok_converse(text="hello", in_tok=10, out_tok=5):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}},
            "usage": {"inputTokens": in_tok, "outputTokens": out_tok},
            "stopReason": "end_turn"}


# ────────────────────────── _call_bedrock (plain) ───────────────────────────


def test_call_bedrock_happy_path(monkeypatch):
    fake = _FakeConverseClient(response=_ok_converse("hi there"))
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    out = llm_utils._call_bedrock("key", "anthropic.claude-3-5-sonnet-20241022-v2:0", "ping", region="us-east-1")
    assert out == "hi there"
    assert fake.calls[0]["modelId"] == "anthropic.claude-3-5-sonnet-20241022-v2:0"
    assert fake.calls[0]["messages"][0]["content"][0]["text"] == "ping"


def test_call_bedrock_includes_reasoning_for_claude(monkeypatch):
    fake = _FakeConverseClient(response=_ok_converse())
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    llm_utils._call_bedrock("key", "us.anthropic.claude-3-7-sonnet-20250219-v1:0", "ping",
                            region="us-east-1", reasoning="high")
    assert fake.calls[0]["additionalModelRequestFields"]["reasoning_config"]["budget_tokens"] == 16384


def test_call_bedrock_no_reasoning_field_for_llama(monkeypatch):
    fake = _FakeConverseClient(response=_ok_converse())
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    llm_utils._call_bedrock("key", "meta.llama3-1-70b-instruct-v1:0", "ping", region="us-east-1", reasoning="high")
    assert "additionalModelRequestFields" not in fake.calls[0]


def test_call_bedrock_returns_empty_on_client_error(monkeypatch):
    from botocore.exceptions import ClientError
    err = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"},
                       "ResponseMetadata": {"HTTPStatusCode": 403}}, "Converse")
    fake = _FakeConverseClient(error=err)
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    llm_utils._pop_last_http()  # clear any stale stash
    out = llm_utils._call_bedrock("key", "anthropic.claude-3-5-sonnet-20241022-v2:0", "ping", region="us-east-1")
    assert out == ""
    captured = llm_utils._pop_last_http() or {}
    assert captured.get("status") == 403


def test_call_bedrock_empty_without_region_or_key():
    assert llm_utils._call_bedrock("", "model", "p", region="us-east-1") == ""
    assert llm_utils._call_bedrock("key", "model", "p", region="") == ""


# ────────────────────────── call_bedrock_with_tools ─────────────────────────


def test_call_bedrock_with_tools_parses_tooluse(monkeypatch):
    resp = {"output": {"message": {"role": "assistant", "content": [
                {"text": "calling"},
                {"toolUse": {"toolUseId": "t1", "name": "lookup", "input": {"q": "AAPL"}}}]}},
            "usage": {"inputTokens": 3, "outputTokens": 4}, "stopReason": "tool_use"}
    fake = _FakeConverseClient(response=resp)
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    tools = [{"type": "function", "function": {"name": "lookup", "description": "d",
              "parameters": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}}}]
    out = llm_utils.call_bedrock_with_tools("key", "anthropic.claude-3-5-sonnet-20241022-v2:0",
                                            "find AAPL", tools, region="us-east-1")
    assert out["tool_calls"] == [{"name": "lookup", "arguments": {"q": "AAPL"}}]
    assert "calling" in out["text"]
    assert "toolConfig" in fake.calls[0]
    assert fake.calls[0]["toolConfig"]["tools"][0]["toolSpec"]["name"] == "lookup"


def test_call_bedrock_with_tools_empty_on_error(monkeypatch):
    from botocore.exceptions import ClientError
    err = ClientError({"Error": {"Code": "ValidationException", "Message": "bad"},
                       "ResponseMetadata": {"HTTPStatusCode": 400}}, "Converse")
    fake = _FakeConverseClient(error=err)
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    out = llm_utils.call_bedrock_with_tools("key", "m", "p", [], region="us-east-1")
    assert out == {"text": "", "tool_calls": []}


# ────────────────────────── _build_pydantic_ai_model (structured) ───────────


def test_build_pydantic_ai_model_bedrock(monkeypatch):
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: MagicMock())
    m = llm_utils._build_pydantic_ai_model(
        "bedrock", "key", "us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        {"bedrock_region": "us-east-1", "bedrock_reasoning": "medium"})
    assert m is not None
    assert m.__class__.__name__ == "BedrockConverseModel"


def test_build_pydantic_ai_model_bedrock_requires_key():
    assert llm_utils._build_pydantic_ai_model("bedrock", "", "m", {"bedrock_region": "us-east-1"}) is None


def test_build_pydantic_ai_model_bedrock_requires_region(monkeypatch):
    monkeypatch.delenv("BEDROCK_REGION", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    assert llm_utils._build_pydantic_ai_model("bedrock", "key", "m", {}) is None


# ────────────────────────── dispatch wiring ─────────────────────────────────


def test_dispatch_routes_bedrock_to_call_bedrock(monkeypatch):
    called = {}

    def _fake(api_key, model, prompt, **kw):
        called["args"] = (api_key, model, prompt, kw)
        return "routed"

    monkeypatch.setattr(llm_utils, "_call_bedrock", _fake)
    monkeypatch.setattr(llm_utils, "_check_prompt_cache", lambda *a, **k: None)
    monkeypatch.setattr(llm_utils, "_store_prompt_cache", lambda *a, **k: None)
    monkeypatch.setattr(llm_utils, "_get_model_rate_limiter", lambda *a, **k: None)
    out = llm_utils.call_llm_by_provider(
        "bedrock", "key", "anthropic.claude-x", "hi",
        provider_config={"bedrock_region": "us-east-1", "bedrock_reasoning": "low"})
    assert out == "routed"
    assert called["args"][3]["region"] == "us-east-1"
    assert called["args"][3]["reasoning"] == "low"


# ────────────────────────── reasoning maxTokens reconciliation ──────────────


def test_call_bedrock_reasoning_bumps_max_tokens(monkeypatch):
    # Converse requires maxTokens > reasoning budget_tokens; the default 256
    # would 400 without reconciliation.
    fake = _FakeConverseClient(response=_ok_converse())
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    llm_utils._call_bedrock("key", "us.anthropic.claude-3-7-sonnet-20250219-v1:0", "ping",
                            max_output_tokens=256, region="us-east-1", reasoning="high")
    ic = fake.calls[0]["inferenceConfig"]
    budget = fake.calls[0]["additionalModelRequestFields"]["reasoning_config"]["budget_tokens"]
    assert ic["maxTokens"] > budget
    assert ic["maxTokens"] == budget + 1024


def test_call_bedrock_with_tools_reasoning_bumps_max_tokens(monkeypatch):
    fake = _FakeConverseClient(response=_ok_converse())
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    llm_utils.call_bedrock_with_tools("key", "anthropic.claude-3-7-sonnet-20250219-v1:0", "p", [],
                                      region="us-east-1", max_output_tokens=1024, reasoning="low")
    ic = fake.calls[0]["inferenceConfig"]
    assert ic["maxTokens"] == 1024 + 1024  # budget 1024 + margin


def test_call_bedrock_gpt_oss_reasoning_effort_floor_and_uncapped(monkeypatch):
    # gpt-oss reasoning_effort is sent verbatim; a small maxTokens cap is raised
    # to a floor so reasoning doesn't starve the answer.
    fake = _FakeConverseClient(response=_ok_converse())
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake)
    llm_utils._call_bedrock("k", "openai.gpt-oss-120b-1:0", "p",
                            max_output_tokens=128, region="us-east-1", reasoning="medium")
    c0 = fake.calls[0]
    assert c0["additionalModelRequestFields"] == {"reasoning_effort": "medium"}
    assert c0["inferenceConfig"]["maxTokens"] == 4096

    # Uncapped (max_output_tokens=0) stays uncapped — the caller intends no limit.
    fake2 = _FakeConverseClient(response=_ok_converse())
    monkeypatch.setattr(llm_utils.bedrock_client, "build_runtime_client", lambda api_key, region, **kw: fake2)
    llm_utils._call_bedrock("k", "openai.gpt-oss-120b-1:0", "p",
                            max_output_tokens=0, region="us-east-1", reasoning="high")
    c1 = fake2.calls[0]
    assert c1["additionalModelRequestFields"] == {"reasoning_effort": "high"}
    assert "maxTokens" not in c1.get("inferenceConfig", {})
