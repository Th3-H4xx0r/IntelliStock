# backend/tests/test_llm_critical_guard_retry.py
import importlib
import time
import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    from backend import llm_critical_guard, llm_utils
    importlib.reload(llm_critical_guard)
    # Don't reload llm_utils (it's huge & has heavy import side effects);
    # just clear the per-thread stash.
    if hasattr(llm_utils, "_LAST_HTTP_PER_THREAD"):
        llm_utils._LAST_HTTP_PER_THREAD.clear()
    yield


def _patch_call(monkeypatch, sequence):
    """Patch _call_with_capture to return canned (text, status, body, exc)
    tuples from `sequence`, popping one per call."""
    from backend import llm_utils
    calls = []
    seq_iter = iter(sequence)
    def fake(provider, api_key, model, prompt, **kw):
        calls.append((provider, model, prompt))
        return next(seq_iter)
    monkeypatch.setattr(llm_utils, "_call_with_capture", fake)
    return calls


def test_critical_then_success_no_raise(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)  # no real waits
    seq = [
        ("", 403, "temporarily blocked", None),  # attempt 1: critical
        ("", 403, "temporarily blocked", None),  # attempt 2: critical
        ("OK_RESULT", 200, None, None),          # attempt 3: success
    ]
    calls = _patch_call(monkeypatch, seq)
    from backend.llm_utils import _call_llm_with_critical_guard
    result = _call_llm_with_critical_guard(
        "azure", "k", "gpt-5.4-mini", "prompt-x",
        attribution_keys={"backtest_id": "bt9"},
    )
    assert result == "OK_RESULT"
    assert len(calls) == 3


def test_critical_four_attempts_raises(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    seq = [("", 403, "temporarily blocked", None)] * 4
    _patch_call(monkeypatch, seq)
    from backend.llm_utils import _call_llm_with_critical_guard
    from backend.llm_critical_guard import LLMCriticalFailure
    with pytest.raises(LLMCriticalFailure) as excinfo:
        _call_llm_with_critical_guard(
            "azure", "k", "gpt-5.4-mini", "p",
            attribution_keys={"backtest_id": "bt9", "instance_id": "main"},
        )
    exc = excinfo.value
    assert exc.class_tag == "azure_403_blocked"
    assert exc.provider == "azure"
    assert exc.model == "gpt-5.4-mini"
    assert len(exc.attempts) == 4
    assert exc.attribution["backtest_id"] == "bt9"


def test_backoff_timing(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    seq = [("", 403, "temporarily blocked", None)] * 4
    _patch_call(monkeypatch, seq)
    from backend.llm_utils import _call_llm_with_critical_guard
    from backend.llm_critical_guard import LLMCriticalFailure
    with pytest.raises(LLMCriticalFailure):
        _call_llm_with_critical_guard(
            "azure", "k", "m", "p", attribution_keys={},
        )
    # Backoff: 1s, 2s, 4s (3 sleeps before the final raise)
    assert sleeps == [1, 2, 4]


def test_no_retry_on_normal_response(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
    seq = [("RESULT", 200, None, None)]
    _patch_call(monkeypatch, seq)
    from backend.llm_utils import _call_llm_with_critical_guard
    result = _call_llm_with_critical_guard(
        "azure", "k", "m", "p", attribution_keys={},
    )
    assert result == "RESULT"
    assert sleeps == []


def test_idempotent_after_first_raise(monkeypatch):
    """Second concurrent call sees _already_raised=True and short-circuits."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    seq = [("", 403, "temporarily blocked", None)] * 4
    _patch_call(monkeypatch, seq)
    from backend.llm_utils import _call_llm_with_critical_guard
    from backend.llm_critical_guard import LLMCriticalFailure, was_already_raised

    with pytest.raises(LLMCriticalFailure):
        _call_llm_with_critical_guard("azure", "k", "m", "p", attribution_keys={})
    assert was_already_raised() is True

    # Second call doesn't trigger 4 more retries; the wrapper bails fast.
    # But the call DOES still happen (we don't block normal traffic); it just
    # doesn't re-raise. Caller receives empty string.
    seq2 = [("", 403, "temporarily blocked", None)]
    _patch_call(monkeypatch, seq2)
    result = _call_llm_with_critical_guard("azure", "k", "m", "p", attribution_keys={})
    # Result is whatever the underlying provider returned (empty here)
    assert result == ""
