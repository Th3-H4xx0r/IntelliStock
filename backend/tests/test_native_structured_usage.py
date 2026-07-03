"""Regression tests for the PydanticAI-native structured usage-telemetry gap
(Round-2 Task 1 / spec 1a).

Root cause: ``call_structured_llm_by_provider`` runs the request through a
PydanticAI ``Agent(...).run_sync(...)`` and reads ``result.usage()`` — but on
SUCCESS it only stashed the tokens into ``_LAST_STRUCTURED_LLM_CALL`` and never
called ``_safe_record``. So EVERY successful native structured call (openrouter,
azure, openai, deepseek, nvidia, gemini, bedrock, ollama — every provider that
``_build_pydantic_ai_model`` can build) produced ZERO rows in LLMUsage. This is
the ``$6.71``-invisible-spend hole from backtest 586767 (drained ~$9.25 of
OpenRouter credit; the dashboard recorded only $2.54).

Fix: record exactly one row on native success — one HTTP call → one row. The
raw-JSON fallback (which makes its OWN separate HTTP call through
``call_llm_by_provider``) keeps recording its own row; native success returns
before that path, so there is no double-record.

TEST HYGIENE: an AUTOUSE cage stubs every telemetry-DB / alert / notify seam
reachable from the code under test, so no test can touch a real DB, the network,
or the live notification stack no matter what an individual test leaves
unmocked (two prior real-world incidents).
"""
from __future__ import annotations

import os
import sys

import pytest
from pydantic import BaseModel

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import llm_utils  # noqa: E402


class _OutSchema(BaseModel):
    text: str


# ── Fakes mirroring pydantic-ai 1.0.18's RunUsage / AgentRunResult ────────────
class _FakeUsage:
    """Mirrors the subset of ``pydantic_ai.usage.RunUsage`` (1.0.18) that the
    extractor reads: ``input_tokens`` / ``output_tokens`` and the free-form
    ``details`` map that carries provider-specific reasoning counts."""

    def __init__(self, input_tokens=None, output_tokens=None, details=None):
        self.requests = 1
        self.tool_calls = 0
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.input_audio_tokens = 0
        self.cache_audio_read_tokens = 0
        self.output_audio_tokens = 0
        self.details = details or {}


class _FakeResult:
    def __init__(self, usage, output):
        self._usage = usage
        self.output = output

    def usage(self):
        return self._usage


class _FakeAgent:
    """Stands in for ``pydantic_ai.Agent`` — swallows the ctor kwargs and
    returns a canned result from ``run_sync``."""

    _result = None

    def __init__(self, *a, **k):
        pass

    def run_sync(self, prompt, infer_name=False):
        return type(self)._result


@pytest.fixture(autouse=True)
def cage_alerts(monkeypatch):
    """AUTOUSE cage — no real telemetry DB, no network, no live alerts.

    SEAM: ``call_structured_llm_by_provider`` reaches the telemetry DB through
    the module-level ``llm_utils._telemetry_record`` (bound at import from
    ``llm_telemetry.record_llm_call``); ``_safe_record`` is a thin wrapper over
    it. We stub the module-level name (where it is looked up) so even a test
    that exercises the real ``_safe_record`` cannot write a row. We also stub
    ``llm_telemetry.record_llm_call`` and the notify seams defensively.
    """
    monkeypatch.setattr(llm_utils, "_telemetry_record", lambda **kw: None, raising=False)
    try:
        import llm_telemetry
        monkeypatch.setattr(llm_telemetry, "record_llm_call", lambda **kw: None, raising=False)
    except Exception:
        pass
    for modname, attr in (
        ("notifications", "notify"),
        ("live_alerts", "notify"),
        ("live_alerts", "alert_strategy_error"),
    ):
        try:
            mod = __import__(modname)
            if hasattr(mod, attr):
                monkeypatch.setattr(mod, attr, lambda *a, **k: None, raising=False)
        except Exception:
            pass
    # Terminal-failure cache is process-global; clear it so a prior run can't
    # short-circuit the candidate loop.
    try:
        with llm_utils._TERMINAL_LLM_FAILURES_LOCK:
            llm_utils._TERMINAL_LLM_FAILURES.clear()
    except Exception:
        pass
    yield


def _drive_native(
    monkeypatch,
    *,
    usage,
    output=None,
    provider="openrouter",
    model="nvidia/nemotron-3-ultra-550b-a55b",
    record_side_effect=None,
    fail_raw=True,
):
    """Drive a successful PydanticAI-native structured call and capture every
    ``_safe_record`` invocation. Returns ``(returned_output, recorded_calls)``.

    ``fail_raw`` sentinels ``call_llm_by_provider`` (the raw/plain HTTP path)
    so a test can prove the native success path never fell through to a second
    HTTP call (and thus never records a second row)."""
    if output is None:
        output = _OutSchema(text="ok")

    recorded: list[dict] = []

    def _recorder(**kw):
        recorded.append(kw)
        if record_side_effect is not None:
            record_side_effect()

    monkeypatch.setattr(llm_utils, "_safe_record", _recorder)
    monkeypatch.setattr(llm_utils, "_PYDANTIC_AI_AVAILABLE", True)
    monkeypatch.setattr(llm_utils, "_build_pydantic_ai_model", lambda *a, **k: object())
    _FakeAgent._result = _FakeResult(usage, output)
    monkeypatch.setattr(llm_utils, "Agent", _FakeAgent)

    if fail_raw:
        def _boom_raw(*a, **k):
            raise AssertionError(
                "native success must NOT fall through to the raw/plain HTTP path"
            )
        monkeypatch.setattr(llm_utils, "call_llm_by_provider", _boom_raw)

    out = llm_utils.call_structured_llm_by_provider(
        provider, "sk-test-key", model, "decide now", _OutSchema,
        max_output_tokens=64,
    )
    return out, recorded


# ── Unit: usage normaliser ────────────────────────────────────────────────────
def test_structured_usage_for_record_maps_reasoning():
    out = llm_utils._structured_usage_for_record(
        {"input_tokens": 120, "output_tokens": 42, "detail_reasoning_tokens": 7,
         "requests": 1, "tool_calls": 0}
    )
    assert out == {"input_tokens": 120, "output_tokens": 42, "reasoning_tokens": 7}


def test_structured_usage_for_record_no_reasoning():
    out = llm_utils._structured_usage_for_record(
        {"input_tokens": 10, "output_tokens": 5}
    )
    assert out == {"input_tokens": 10, "output_tokens": 5}


def test_structured_usage_for_record_garbage():
    assert llm_utils._structured_usage_for_record(None) == {}
    assert llm_utils._structured_usage_for_record({}) == {}


# ── Native success records exactly one row ────────────────────────────────────
def test_native_structured_success_records_one_row(monkeypatch):
    usage = _FakeUsage(input_tokens=120, output_tokens=42,
                       details={"reasoning_tokens": 7})
    out, recorded = _drive_native(monkeypatch, usage=usage)

    assert isinstance(out, _OutSchema) and out.text == "ok"
    assert len(recorded) == 1
    row = recorded[0]
    assert row["provider"] == "openrouter"
    assert row["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert row["usage"] == {"input_tokens": 120, "output_tokens": 42,
                            "reasoning_tokens": 7}
    assert row["ok"] is True
    # PydanticAI carries no USD envelope — cost falls back to the registry/YAML.
    assert row["cost_usd_override"] is None


def test_native_success_records_for_azure_provider(monkeypatch):
    """Coverage is provider-agnostic: azure routes through the same native
    path, so it must record too."""
    usage = _FakeUsage(input_tokens=8, output_tokens=3)
    out, recorded = _drive_native(
        monkeypatch, usage=usage, provider="azure", model="gpt-4o-mini",
    )
    assert isinstance(out, _OutSchema)
    assert len(recorded) == 1
    assert recorded[0]["provider"] == "azure"
    assert recorded[0]["usage"] == {"input_tokens": 8, "output_tokens": 3}
    assert recorded[0]["ok"] is True


# ── Recording failure must never break the LLM call ───────────────────────────
def test_recording_failure_does_not_break_call(monkeypatch):
    usage = _FakeUsage(input_tokens=1, output_tokens=1)

    def _explode():
        raise RuntimeError("telemetry backend down")

    out, recorded = _drive_native(
        monkeypatch, usage=usage, record_side_effect=_explode,
    )
    # The structured output is still returned even though recording raised.
    assert isinstance(out, _OutSchema) and out.text == "ok"
    assert len(recorded) == 1  # attempted once, exception swallowed


# ── No double-record: native success does not also hit the raw/plain path ─────
def test_native_success_does_not_double_record(monkeypatch):
    usage = _FakeUsage(input_tokens=50, output_tokens=25)
    # fail_raw=True installs a sentinel on call_llm_by_provider that raises if
    # the raw/plain HTTP path is ever reached — proving the one-row-per-call
    # invariant (a native success is one HTTP call → exactly one row).
    out, recorded = _drive_native(monkeypatch, usage=usage, fail_raw=True)
    assert isinstance(out, _OutSchema)
    assert len(recorded) == 1
