"""Regression tests for backtest run-to-run determinism.

Four replicate runs of the SAME window/config (2026-03-30..2026-04-27, 900s,
$6000, strategy 179) returned +15.80%, +7.05%, -0.67% and -0.92% — a 16.73pp
spread on a system being used to detect a ~3pp edge. Two independent causes
were found, and each has a test here:

1. Unpinned LLM sampling. Every provider call site hard-coded temperature=0.2
   with no seed, so the per-candidate trade overlay drew a fresh sample each
   run. Comparing bt 738395 against bt 259145, 308 of 2544 shared decisions
   came back with a different overlay verdict and 4 flipped action outright.

2. Completion-order result collection. `collect_bounded` gathered futures out
   of a `set`, so the company/macro article LLM fan-out (4 and 6 workers) fed
   downstream ranking in whatever order the provider happened to answer. A set
   of `Future` objects iterates in id()-hash order, which PYTHONHASHSEED cannot
   stabilise.

The `collect_bounded` tests are the "shuffled input ordering" regression the
brief asked for: they drive the real collector with completion orders that are
the exact reverse of submission order and assert the output is unchanged.
"""
from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import llm_utils as llu  # noqa: E402
from _phase_alpha_helpers import backtest_determinism_env_vars  # noqa: E402
from llm_deadline import collect_bounded, shutdown_bounded  # noqa: E402


# ── 1. Order determinism in the bounded LLM collector ────────────────────

def _collect_with_completion_order(values, release_order):
    """Run `collect_bounded` forcing completions in `release_order`.

    Each worker blocks on its own event; the events are set in `release_order`,
    so the futures genuinely complete in that order inside a real executor.
    """
    n = len(values)
    gates = [threading.Event() for _ in range(n)]
    done_gates = [threading.Event() for _ in range(n)]

    def work(i):
        gates[i].wait(timeout=10)
        done_gates[i].set()
        return values[i]

    ex = ThreadPoolExecutor(n)
    try:
        futures = [ex.submit(work, i) for i in range(n)]
        for i in release_order:
            gates[i].set()
            # Serialise completion so the ordering under test is the real one.
            done_gates[i].wait(timeout=10)
        out = collect_bounded(futures, per_call_timeout=10)
        return list(out)
    finally:
        for g in gates:
            g.set()
        shutdown_bounded(ex)


def test_results_follow_submission_order_not_completion_order():
    values = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN"]
    forward = _collect_with_completion_order(values, range(len(values)))
    reverse = _collect_with_completion_order(values, reversed(range(len(values))))
    assert forward == values, "submission order must be preserved"
    assert reverse == values, (
        "reversing completion order changed the result list — downstream "
        "ranking and truncation would see a different candidate order"
    )
    assert forward == reverse


def test_shuffled_completion_orders_all_agree():
    """The brief's regression shape: same input, many orderings, one output."""
    values = list(range(8))
    orders = [
        list(range(8)),
        list(reversed(range(8))),
        [3, 0, 7, 1, 6, 2, 5, 4],
        [7, 6, 0, 1, 5, 2, 4, 3],
    ]
    outputs = {tuple(_collect_with_completion_order(values, o)) for o in orders}
    assert outputs == {tuple(values)}, f"orderings disagreed: {outputs}"


def test_error_futures_do_not_shift_surviving_order():
    """A failed call must drop out without reordering its neighbours."""
    def work(i):
        if i in (1, 3):
            raise RuntimeError("provider blew up")
        return i

    ex = ThreadPoolExecutor(5)
    try:
        futures = [ex.submit(work, i) for i in range(5)]
        seen = []
        out = collect_bounded(
            futures, per_call_timeout=10, on_error=lambda exc, f: seen.append(exc)
        )
        assert list(out) == [0, 2, 4]
        assert len(seen) == 2
    finally:
        shutdown_bounded(ex)


# ── 2. Deterministic LLM sampling ────────────────────────────────────────

@pytest.fixture
def deterministic_env(monkeypatch):
    monkeypatch.setenv("NEXUS_LLM_DETERMINISTIC", "1")
    monkeypatch.delenv("NEXUS_LLM_SEED", raising=False)
    return monkeypatch


def test_sampling_is_stochastic_by_default(monkeypatch):
    """Live must keep provider defaults — the flag is backtest-only."""
    monkeypatch.delenv("NEXUS_LLM_DETERMINISTIC", raising=False)
    assert llu.deterministic_sampling_active() is False
    assert llu.effective_temperature(0.2) == pytest.approx(0.2)
    body = {"temperature": 0.2}
    llu.apply_deterministic_sampling(body, model="gpt-4o")
    assert body == {"temperature": 0.2}, "live body must be untouched"


def test_deterministic_mode_forces_greedy_decoding(deterministic_env):
    assert llu.deterministic_sampling_active() is True
    assert llu.effective_temperature(0.2) == 0.0
    assert llu.effective_temperature(0.7) == 0.0
    assert llu.effective_top_p(0.95) == 1.0


def test_deterministic_mode_pins_body_sampling_params(deterministic_env):
    body = {"model": "m", "messages": [], "temperature": 0.2, "top_p": 0.95}
    llu.apply_deterministic_sampling(body, model="nvidia/nemotron-3-ultra-550b-a55b")
    assert body["temperature"] == 0.0
    assert body["top_p"] == 1.0
    assert body["seed"] == llu.deterministic_seed()


def test_reasoning_models_still_omit_temperature(deterministic_env):
    """gpt-5/o-series reject an explicit temperature; don't send one."""
    body = {"temperature": 0.2}
    llu.apply_deterministic_sampling(body, model="gpt-5")
    assert "temperature" not in body
    assert body["top_p"] == 1.0 and body["seed"] == llu.deterministic_seed()


def test_seed_is_stable_and_overridable(deterministic_env, monkeypatch):
    assert llu.deterministic_seed() == llu.deterministic_seed()
    monkeypatch.setenv("NEXUS_LLM_SEED", "4242")
    assert llu.deterministic_seed() == 4242
    monkeypatch.setenv("NEXUS_LLM_SEED", "not-an-int")
    assert llu.deterministic_seed() == llu._DEFAULT_DETERMINISTIC_SEED


def test_structured_model_settings_pin_sampling(deterministic_env):
    settings = llu._build_structured_model_settings(
        "openrouter", 256, 60.0, 0.2, model="some/model"
    )
    assert settings.get("temperature") == 0.0
    assert settings.get("top_p") == 1.0
    assert settings.get("seed") == llu.deterministic_seed()


# ── 3. The env var actually reaches a spawned backtest container ─────────

def test_backtest_container_gets_deterministic_llm_env():
    out = backtest_determinism_env_vars({})
    assert out["NEXUS_LLM_DETERMINISTIC"] == "1"
    assert out["PYTHONHASHSEED"] == "0"


def test_determinism_killswitch_disables_llm_pinning_too():
    out = backtest_determinism_env_vars({"NEXUS_BACKTEST_DETERMINISM": "0"})
    assert out["NEXUS_LLM_DETERMINISTIC"] == "0"
    assert out["NEXUS_BACKTEST_DETERMINISM"] == "0"


def test_llm_seed_is_forwarded_when_operator_pins_it():
    out = backtest_determinism_env_vars({"NEXUS_LLM_SEED": "99"})
    assert out["NEXUS_LLM_SEED"] == "99"
    assert "NEXUS_LLM_SEED" not in backtest_determinism_env_vars({})


# ── 4. A cache hit must equal a cache miss ───────────────────────────────

def test_cached_skeleton_is_rejected_like_a_fresh_one():
    """A cache must be a pure speedup.

    The fresh path raises on skeleton output (all-defaults, no model opinion)
    so the caller retries. The cache-hit path used to accept the same object as
    `ok=True`, so whether a run got a real overlay verdict or a neutral default
    depended on cache warmth — a cache changing results, not just latency.
    """
    from pydantic import BaseModel, Field

    class Overlay(BaseModel):
        delta_score: float = Field(0.0)
        confidence_delta: float = Field(0.0)
        reason_codes: list[str] = Field(default_factory=list)

    skeleton = Overlay()
    assert llu._is_skeleton_structured_output(skeleton), (
        "an all-defaults overlay is the skeleton shape this guards"
    )
    real = Overlay(delta_score=0.2, reason_codes=["strong_earnings"])
    assert not llu._is_skeleton_structured_output(real)


def test_cache_hit_path_applies_the_skeleton_guard():
    """Assert the guard is wired into every cache-hit branch, not just defined."""
    import inspect

    src = inspect.getsource(llu)
    hit_branches = src.count("_cached_obj = _validate_structured_output_from_raw_text")
    guarded = src.count(
        "if _cached_obj is not None and _is_skeleton_structured_output(_cached_obj)"
    )
    assert hit_branches > 0
    assert guarded == hit_branches, (
        f"{hit_branches} cache-hit branches but only {guarded} skeleton guards — "
        "an unguarded hit can return what a miss would have rejected"
    )
