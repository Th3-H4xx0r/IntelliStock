"""Regression tests for the OpenRouter usage-telemetry gap.

Root cause: ``_call_openrouter`` was the only OpenAI-compatible provider path
that never called ``_safe_record`` — so every OpenRouter call (plain AND the
raw-JSON structured fallback that Nemotron uses on every request) produced
ZERO rows in the LLMUsage table, making live + backtest cost invisible.

These tests are fully caged: no real network (requests.post is mocked) and no
real DB (telemetry is configured with a null conn factory + flusher disabled).
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest
from pydantic import BaseModel

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


class _OutSchema(BaseModel):
    text: str


@pytest.fixture
def telemetry_clean():
    import llm_telemetry
    llm_telemetry._reset_for_tests()
    llm_telemetry.configure(
        db_conn_factory=lambda: None, enabled=True,
        auto_start_flusher=False, pricing_yaml_path=None,
    )
    yield llm_telemetry
    llm_telemetry._reset_for_tests()


class _FakeResp:
    def __init__(self, *, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload


def _ok_payload(*, content='{"text": "hi"}', cost=None, reasoning=0):
    usage = {"prompt_tokens": 120, "completion_tokens": 42,
             "total_tokens": 162}
    if reasoning:
        usage["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    if cost is not None:
        usage["cost"] = cost
    return {
        "choices": [{"message": {"content": content}}],
        "usage": usage,
    }


# ── Unit: usage extractor ────────────────────────────────────────────────────
def test_extract_openrouter_usage_tokens_and_cost():
    import llm_utils
    u, cost = llm_utils._extract_openrouter_usage(
        {"prompt_tokens": 120, "completion_tokens": 42, "cost": 0.0123,
         "completion_tokens_details": {"reasoning_tokens": 7}}
    )
    assert u["input_tokens"] == 120
    assert u["output_tokens"] == 42
    assert u["reasoning_tokens"] == 7
    assert cost == pytest.approx(0.0123)


def test_extract_openrouter_usage_no_cost_returns_none():
    import llm_utils
    u, cost = llm_utils._extract_openrouter_usage(
        {"prompt_tokens": 10, "completion_tokens": 5}
    )
    assert u == {"input_tokens": 10, "output_tokens": 5}
    assert cost is None


def test_extract_openrouter_usage_garbage():
    import llm_utils
    assert llm_utils._extract_openrouter_usage(None) == ({}, None)
    assert llm_utils._extract_openrouter_usage("nope") == ({}, None)


# ── Plain path records a row (with OpenRouter-reported cost) ──────────────────
def test_plain_openrouter_records_row_with_cost(telemetry_clean):
    import llm_utils
    resp = _FakeResp(payload=_ok_payload(cost=0.0123, reasoning=7))
    with patch("requests.post", return_value=resp):
        out = llm_utils.call_llm_by_provider(
            "openrouter", "sk-or-key", "nvidia/nemotron-3-ultra-550b-a55b",
            "decide", max_output_tokens=64,
            provider_config={"openrouter_base_url": "https://openrouter.ai/api/v1"},
        )
    assert out == '{"text": "hi"}'
    rows = telemetry_clean.get_recent_calls(10)
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "openrouter"
    assert row["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert row["input_tokens"] == 120
    assert row["output_tokens"] == 42
    assert row["reasoning_tokens"] == 7
    assert row["ok"] is True
    assert row["total_cost_usd"] == pytest.approx(0.0123)
    assert row["cost_source"] == "envelope"


def test_plain_openrouter_records_row_without_cost(telemetry_clean):
    """No OpenRouter cost in the response → row still recorded with tokens,
    cost falls back (0 here) but the row is NEVER dropped."""
    import llm_utils
    resp = _FakeResp(payload=_ok_payload(cost=None))
    with patch("requests.post", return_value=resp):
        out = llm_utils.call_llm_by_provider(
            "openrouter", "sk-or-key", "nvidia/nemotron-3-ultra-550b-a55b", "decide",
        )
    assert out == '{"text": "hi"}'
    rows = telemetry_clean.get_recent_calls(10)
    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 120
    assert rows[0]["output_tokens"] == 42
    assert rows[0]["cost_source"] != "envelope"


def test_plain_openrouter_records_failure_row(telemetry_clean):
    """A 4xx (auth/model error) still records an ok=False row so live health
    stays visible instead of silently vanishing."""
    import llm_utils
    resp = _FakeResp(status_code=401, payload={"error": {"message": "no key"}},
                     text='{"error": {"message": "no key"}}')
    with patch("requests.post", return_value=resp):
        out = llm_utils.call_llm_by_provider(
            "openrouter", "sk-or-key", "nvidia/nemotron-3-ultra-550b-a55b", "decide",
            retries=0,
        )
    assert out == ""
    rows = telemetry_clean.get_recent_calls(10)
    assert len(rows) == 1
    assert rows[0]["provider"] == "openrouter"
    assert rows[0]["ok"] is False


def test_plain_openrouter_records_empty_completion_terminal(telemetry_clean):
    """HTTP 200 with an empty completion, retries exhausted → the call still
    records one ok=False row carrying the parsed usage (OpenRouter may have
    billed the attempt) instead of vanishing from telemetry."""
    import llm_utils
    resp = _FakeResp(payload=_ok_payload(content="", cost=0.004))
    with patch("requests.post", return_value=resp):
        out = llm_utils.call_llm_by_provider(
            "openrouter", "sk-or-key", "nvidia/nemotron-3-ultra-550b-a55b", "decide",
            retries=0,
        )
    assert out == ""
    rows = telemetry_clean.get_recent_calls(10)
    assert len(rows) == 1
    row = rows[0]
    assert row["provider"] == "openrouter"
    assert row["ok"] is False
    assert row["error"] == "empty response"
    assert row["input_tokens"] == 120
    assert row["output_tokens"] == 42
    assert row["total_cost_usd"] == pytest.approx(0.004)
    assert row["retry_count"] == 0


def test_plain_openrouter_records_no_choices_terminal(telemetry_clean):
    """HTTP 200 with no choices at all, retries exhausted → ok=False row."""
    import llm_utils
    resp = _FakeResp(payload={"choices": [], "usage": {"prompt_tokens": 33,
                                                       "completion_tokens": 0}})
    with patch("requests.post", return_value=resp):
        out = llm_utils.call_llm_by_provider(
            "openrouter", "sk-or-key", "nvidia/nemotron-3-ultra-550b-a55b", "decide",
            retries=0,
        )
    assert out == ""
    rows = telemetry_clean.get_recent_calls(10)
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert rows[0]["error"] == "no choices"
    assert rows[0]["input_tokens"] == 33


# ── Structured raw-JSON fallback path (what Nemotron actually uses) ──────────
def test_structured_raw_json_fallback_records_row(telemetry_clean):
    import llm_utils
    resp = _FakeResp(payload=_ok_payload(content='{"text": "buy"}', cost=0.02))
    with patch("requests.post", return_value=resp):
        result = llm_utils.call_structured_llm_by_provider(
            "openrouter", "sk-or-key", "nvidia/nemotron-3-ultra-550b-a55b",
            prompt="decide", output_type=_OutSchema,
            prefer_raw_json=True,
            provider_config={"openrouter_base_url": "https://openrouter.ai/api/v1"},
        )
    assert result is not None
    assert result.text == "buy"
    rows = telemetry_clean.get_recent_calls(10)
    assert len(rows) == 1
    assert rows[0]["provider"] == "openrouter"
    assert rows[0]["ok"] is True
    assert rows[0]["total_cost_usd"] == pytest.approx(0.02)


def test_call_openrouter_requests_cost_include(telemetry_clean):
    """The request body must ask OpenRouter to include cost so the cheapest,
    most-accurate cost source is available."""
    import llm_utils
    seen = {}

    def _capture(url, headers=None, json=None, timeout=None):
        seen["body"] = json
        return _FakeResp(payload=_ok_payload(cost=0.01))

    with patch("requests.post", side_effect=_capture):
        llm_utils.call_llm_by_provider(
            "openrouter", "sk-or-key", "nvidia/nemotron-3-ultra-550b-a55b", "decide",
        )
    assert seen["body"].get("usage") == {"include": True}
