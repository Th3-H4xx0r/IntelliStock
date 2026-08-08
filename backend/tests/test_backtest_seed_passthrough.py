"""BACKTEST_SEED must reach the spawned broker, or nothing reproduces.

`derive_backtest_seed` falls back to sha256("<backtest_row_id>|<universe>") when
BACKTEST_SEED is unset, so every run draws a different seed purely because it
has a different row id. Measured: bt 820236 and bt 718249 — same window, same
instance, same cash — finished with ZERO position names in common.

It is also a direct credit cost. Divergent discovery means a divergent candidate
set, and the active-event maintenance cache is keyed on (scope, date) but
VALIDATED on a fingerprint of its inputs, so it misses on essentially every
call: 42 invocations, 42 LLM batches, 0 cache hits in bt 718249 — $1.47 of a
$3.43 run.

`.env.example` has documented BACKTEST_SEED=42 since forever; docker-compose
never passed it through.
"""
import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from _phase_alpha_helpers import (  # noqa: E402
    backtest_determinism_env_vars,
    derive_backtest_seed,
)

REPO = os.path.join(os.path.dirname(__file__), "..", "..")


def _compose_env(service: str) -> list:
    with open(os.path.join(REPO, "docker-compose.yml")) as fh:
        doc = yaml.safe_load(fh)
    return list((doc["services"][service].get("environment") or []))


def test_compose_passes_the_seed_to_the_engine():
    entries = _compose_env("backtest-engine")
    seed = [e for e in entries if str(e).startswith("BACKTEST_SEED")]
    assert seed, "docker-compose does not pass BACKTEST_SEED — runs cannot reproduce"
    assert "${BACKTEST_SEED:-42}" in seed[0], "must default, not just pass through"


def test_compose_still_passes_pythonhashseed():
    entries = _compose_env("backtest-engine")
    assert any(str(e).startswith("PYTHONHASHSEED") for e in entries)


def test_env_seed_wins_over_the_row_id():
    got, why = derive_backtest_seed("820236", ["AAPL"], env_seed="42")
    assert got == 42 and "BACKTEST_SEED" in why


def test_the_bug_two_row_ids_give_two_different_seeds():
    """Identical universe, different run -> different draw."""
    a, _ = derive_backtest_seed("820236", ["AAPL", "MSFT"])
    b, _ = derive_backtest_seed("718249", ["AAPL", "MSFT"])
    assert a != b, "this is why no two runs reproduce"


def test_pinning_the_seed_makes_those_two_runs_identical():
    a, _ = derive_backtest_seed("820236", ["AAPL", "MSFT"], env_seed="42")
    b, _ = derive_backtest_seed("718249", ["AAPL", "MSFT"], env_seed="42")
    assert a == b == 42


def test_forwarder_passes_the_seed_when_set():
    out = backtest_determinism_env_vars({"BACKTEST_SEED": "42"})
    assert out.get("BACKTEST_SEED") == "42"


def test_forwarder_omits_the_seed_when_unset():
    """Documents the gap: unset means the row-id fallback, which is the bug."""
    assert "BACKTEST_SEED" not in backtest_determinism_env_vars({})


def test_greedy_decoding_is_on_by_default():
    assert backtest_determinism_env_vars({}).get("NEXUS_LLM_DETERMINISTIC") == "1"


def test_determinism_kill_switch_disables_greedy_decoding():
    out = backtest_determinism_env_vars({"NEXUS_BACKTEST_DETERMINISM": "0"})
    assert out.get("NEXUS_LLM_DETERMINISTIC") == "0"
