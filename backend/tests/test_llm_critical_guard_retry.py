# backend/tests/test_llm_critical_guard_retry.py
import importlib
import time
import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    import sys
    from backend import llm_critical_guard, llm_utils
    from backend.model_evidence import clear_model_evidence_session
    clear_model_evidence_session()
    importlib.reload(llm_critical_guard)
    # Don't reload llm_utils (it's huge & has heavy import side effects);
    # just clear the per-thread stash.
    if hasattr(llm_utils, "_LAST_HTTP_PER_THREAD"):
        llm_utils._LAST_HTTP_PER_THREAD.clear()
    # _call_llm_with_critical_guard imports llm_critical_guard via the bare
    # module name. If an earlier test file (test_nexus_fixes.py etc.) caused
    # the module to be loaded under the bare alias, it's a DISTINCT module
    # object with its own LLMCriticalFailure class identity — pytest.raises
    # on backend.llm_critical_guard.LLMCriticalFailure won't match the
    # exception the wrapper raises. Force the bare alias to point at the
    # just-reloaded backend module so both aliases share the same class.
    sys.modules["llm_critical_guard"] = llm_critical_guard
    # Defensive reset in case any other reference is still live.
    for _alias in ("llm_critical_guard", "backend.llm_critical_guard"):
        _mod = sys.modules.get(_alias)
        if _mod is not None and hasattr(_mod, "reset_state"):
            try:
                _mod.reset_state()
            except Exception:
                pass
    yield
    clear_model_evidence_session()


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


# ----------------------------------------------------------------------------
# Structured-JSON path (_call_structured_llm_with_critical_guard) tests.
# Mirrors the plain-text path above but drives the wrapper that
# graph_nexus_analysis.py / nexus_analyst_panel.py actually use.
# ----------------------------------------------------------------------------


def test_structured_critical_then_success_no_raise(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    from backend import llm_utils

    seq = iter([
        ("raw_json_preferred=HTTP 403: temporarily blocked", False),
        ("raw_json_preferred=HTTP 403: temporarily blocked", False),
        ("", True),  # ok=True
    ])

    def fake_call(provider, api_key, model, prompt, output_type, **kw):
        err, ok = next(seq)
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {"ok": ok, "error": err, "provider": provider}
        return {"result": "OK"} if ok else None

    monkeypatch.setattr(llm_utils, "call_structured_llm_by_provider", fake_call)
    result = llm_utils._call_structured_llm_with_critical_guard(
        "azure", "k", "gpt-5.4-mini", "p", dict,
        attribution_keys={"backtest_id": "bt9"},
    )
    assert result == {"result": "OK"}


def test_structured_critical_four_attempts_raises(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    from backend import llm_utils
    from backend.llm_critical_guard import LLMCriticalFailure

    def fake_call(*a, **kw):
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": False,
            "error": "raw_json_preferred=HTTP 403: Your resource has been temporarily blocked",
            "provider": "azure",
        }
        return None

    monkeypatch.setattr(llm_utils, "call_structured_llm_by_provider", fake_call)
    with pytest.raises(LLMCriticalFailure) as excinfo:
        llm_utils._call_structured_llm_with_critical_guard(
            "azure", "k", "gpt-5.4-mini", "p", dict,
            attribution_keys={"backtest_id": "357345"},
        )
    assert excinfo.value.class_tag == "azure_403_blocked"
    assert len(excinfo.value.attempts) == 4


def test_structured_non_critical_no_retry(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    from backend import llm_utils

    calls = {"n": 0}

    def fake_call(*a, **kw):
        calls["n"] += 1
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": False,
            "error": "raw_json_preferred=Skeleton output: model=foo",  # no HTTP status, non-critical
            "provider": "azure",
        }
        return None

    monkeypatch.setattr(llm_utils, "call_structured_llm_by_provider", fake_call)
    result = llm_utils._call_structured_llm_with_critical_guard(
        "azure", "k", "m", "p", dict, attribution_keys={},
    )
    assert result is None
    assert calls["n"] == 1  # no retries


# ----------------------------------------------------------------------------
# Mid-retry short-circuit: if a SIBLING worker raises LLMCriticalFailure while
# THIS worker is sleeping between attempts, this worker should bail out
# instead of running its full 4-attempt loop and raising a second time. The
# second raise would otherwise be silently swallowed by ThreadPoolExecutor
# (stored on a Future that nobody awaits), masking the failure.
# ----------------------------------------------------------------------------


def test_second_worker_short_circuits_mid_retry(monkeypatch):
    """If another worker raises mid-retry, this worker should short-circuit
    instead of completing its own 4-retry loop and raising again."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    from backend import llm_utils
    from backend.llm_critical_guard import mark_raised

    seq = iter([
        ("", 403, "temporarily blocked", None),  # attempt 1: critical
        ("", 403, "temporarily blocked", None),  # attempt 2: critical (then we externally trip the flag)
    ])
    call_count = {"n": 0}

    def fake_call(*a, **kw):
        call_count["n"] += 1
        # After the second call, simulate another worker tripping the flag
        if call_count["n"] == 2:
            mark_raised()
        return next(seq)

    monkeypatch.setattr(llm_utils, "_call_with_capture", fake_call)

    # Wrapper should NOT raise (the short-circuit kicks in before attempt 3)
    result = llm_utils._call_llm_with_critical_guard(
        "azure", "k", "m", "p", attribution_keys={},
    )
    # No exception; result is empty string passthrough
    assert result == ""
    assert call_count["n"] == 2  # only 2 calls, not 4


def test_structured_second_worker_short_circuits_mid_retry(monkeypatch):
    """Structured-path analog: sibling worker tripping the guard mid-retry
    must cause this worker to bail with the last (non-ok) result instead of
    completing 4 attempts and re-raising."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    from backend import llm_utils
    from backend.llm_critical_guard import mark_raised

    call_count = {"n": 0}

    def fake_call(provider, api_key, model, prompt, output_type, **kw):
        call_count["n"] += 1
        llm_utils._LAST_STRUCTURED_LLM_CALL.data = {
            "ok": False,
            "error": "raw_json_preferred=HTTP 403: temporarily blocked",
            "provider": provider,
        }
        # After the second call, simulate another worker tripping the flag
        if call_count["n"] == 2:
            mark_raised()
        return None

    monkeypatch.setattr(llm_utils, "call_structured_llm_by_provider", fake_call)

    # Wrapper should NOT raise (the short-circuit kicks in before attempt 3)
    result = llm_utils._call_structured_llm_with_critical_guard(
        "azure", "k", "m", "p", dict, attribution_keys={},
    )
    # No exception; result is None passthrough (last non-ok structured return)
    assert result is None
    assert call_count["n"] == 2  # only 2 calls, not 4


def test_off_mode_keeps_missing_context_retry_and_backoff_behavior(monkeypatch):
    sleeps = []
    monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
    calls = _patch_call(
        monkeypatch,
        [
            ("", 403, "temporarily blocked", None),
            ("OK", 200, None, None),
        ],
    )

    from backend.llm_utils import _call_llm_with_critical_guard

    assert (
        _call_llm_with_critical_guard(
            "azure", "key", "model", "prompt", attribution_keys={}
        )
        == "OK"
    )
    assert len(calls) == 2
    assert sleeps == [1]
