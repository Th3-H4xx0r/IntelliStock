"""A stalled LLM call must be re-issued, not handed to a different model.

A stall and a slow call are indistinguishable from the caller, but they need
opposite treatment: a stall never returns however long you wait, and re-issuing
it almost always succeeds. The previous behaviour raised on first expiry and let
the candidate-fallback loop pick a DIFFERENT model — which silently changed
which model produced the run's decisions, on top of not fixing anything.
"""
import ast
import os
import sys
import time
import types

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LLM = os.path.join(BACKEND, "llm_utils.py")

WANTED = {"_run_sync_bounded", "_structured_stall_attempts"}


def _load(env=None):
    with open(LLM, "r") as fh:
        tree = ast.parse(fh.read())
    keep = [n for n in tree.body if getattr(n, "name", None) in WANTED]
    mod = types.ModuleType("_llm")
    mod.os, mod.time, mod.sys = os, time, sys

    class _Exec:
        def submit(self, fn, *a, **k):
            import concurrent.futures as _cf
            f = _cf.Future()
            try:
                f.set_result(fn(*a, **k))
            except BaseException as exc:      # noqa: BLE001 - mirrors executor
                f.set_exception(exc)
            return f

    # Real executor so timeouts behave; swapped per-test where needed.
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=4)
    mod._structured_executor = lambda: pool
    exec(compile(ast.Module(body=keep, type_ignores=[]), LLM, "exec"), mod.__dict__)
    return mod


MOD = _load()


@pytest.fixture(autouse=True)
def _fresh_executor():
    """Every test gets its own pool.

    Abandoning a stalled call is the whole point of the code under test, so
    each test leaks worker threads that stay asleep for the duration. Sharing
    one small pool across tests meant later tests queued behind those leaked
    threads and never got to run their retries — the third test saw calls == 1
    while stderr showed all three attempts being issued. That was a defect in
    this harness, not in the retry logic; production uses 32 workers against
    calls that do eventually return.
    """
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=8)
    prev = MOD._structured_executor
    MOD._structured_executor = lambda: pool
    try:
        yield
    finally:
        MOD._structured_executor = prev
        pool.shutdown(wait=False)


class _Agent:
    """Stalls for the first `stalls` calls, then returns."""

    def __init__(self, stalls, stall_for=30.0):
        self.stalls = stalls
        self.stall_for = stall_for
        self.calls = 0

    def run_sync(self, prompt, infer_name=False):
        self.calls += 1
        if self.calls <= self.stalls:
            time.sleep(self.stall_for)
        return f"ok after {self.calls}"


def test_a_single_stall_is_retried_and_succeeds():
    """THE case the user described: it stalls once, a retry works.

    stall_for is far longer than any attempt budget so the first attempt cannot
    finish by luck — the earlier version of this test raced the 5s floor and
    passed or failed depending on scheduling.
    """
    a = _Agent(stalls=1, stall_for=90.0)
    got = MOD._run_sync_bounded(a, "p", 3.0, "openrouter", "nemotron")
    assert got == "ok after 2"
    assert a.calls == 2, "the same model should have been re-issued"


def test_two_stalls_still_recovers_on_the_third():
    a = _Agent(stalls=2, stall_for=90.0)
    got = MOD._run_sync_bounded(a, "p", 3.0, "openrouter", "nemotron")
    assert got == "ok after 3"
    assert a.calls == 3


def test_a_persistent_stall_eventually_raises():
    a = _Agent(stalls=99, stall_for=90.0)
    with pytest.raises(TimeoutError) as e:
        MOD._run_sync_bounded(a, "p", 3.0, "openrouter", "nemotron")
    assert "stalled" in str(e.value)
    assert a.calls == MOD._structured_stall_attempts()


def test_a_fast_call_is_not_retried():
    a = _Agent(stalls=0)
    assert MOD._run_sync_bounded(a, "p", 30.0, "p", "m") == "ok after 1"
    assert a.calls == 1, "a successful call must not be re-issued"


def test_the_last_attempt_gets_the_callers_full_budget(monkeypatch):
    """A single-attempt config must not be given LESS time than it asked for.

    With attempts=1 an earlier version handed the only attempt 0.6 of the
    budget — a tighter deadline than existed before this function.
    """
    monkeypatch.setenv("LLM_STALL_ATTEMPTS", "1")
    a = _Agent(stalls=1, stall_for=8.0)
    t0 = time.monotonic()
    got = MOD._run_sync_bounded(a, "p", 30.0, "p", "m")
    assert got == "ok after 1", "the full 30s budget should have covered an 8s call"
    assert a.calls == 1
    assert time.monotonic() - t0 < 25


def test_a_non_timeout_error_is_not_swallowed():
    class _Boom:
        calls = 0

        def run_sync(self, prompt, infer_name=False):
            _Boom.calls += 1
            raise ValueError("content filter")

    with pytest.raises(ValueError):
        MOD._run_sync_bounded(_Boom(), "p", 30.0, "p", "m")
    assert _Boom.calls == 1, "a hard error must not be retried as if it stalled"


def test_attempt_count_is_env_tunable(monkeypatch):
    monkeypatch.setenv("LLM_STALL_ATTEMPTS", "5")
    assert MOD._structured_stall_attempts() == 5
    monkeypatch.setenv("LLM_STALL_ATTEMPTS", "0")
    assert MOD._structured_stall_attempts() == 1, "must always try at least once"
    monkeypatch.setenv("LLM_STALL_ATTEMPTS", "junk")
    assert MOD._structured_stall_attempts() == 3
