"""Every equities backtest defaults to the declared research opt-out.

Strict point-in-time replay needs frozen snapshots, and none exist for
historical windows -- capture only happens on live ticks. Left strict, every
equities backtest dies at lookback bar 1 with "no finalized point-in-time
manifest exists at or before ...", which is why the UI could not start one at
all.

The default is applied in action_create_backtest rather than the API layer, so
EVERY creation path inherits it: the UI, the CLI, chatbot tools, the rerun
script and the Discord bot all funnel through that one function. Crypto and
Kalshi never get it -- point-in-time replay is an equities concept.

A second defect is covered here too: the queue row only persisted its evidence
block when evidence_mode/cost/overrides were set, so a run carrying ONLY
pit_mode lost it silently on the way to the broker.
"""
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from backtest_evidence_options import validate_evidence_options  # noqa: E402

_SRC = open(os.path.join(_backend, "interactive_utils.py")).read()


def test_a_pit_mode_only_run_is_persisted():
    """The row must keep the block when pit_mode is the only thing set."""
    assert '_evidence["pit_mode"] != "strict"' in _SRC, (
        "pit_mode must keep the evidence block on the queue row")


def test_default_is_applied_for_equities_only():
    assert 'if not non_equity_compatibility and _evidence_in.get("pit_mode") is None:' in _SRC
    assert '_evidence["pit_mode"] = "research"' in _SRC


def test_default_sits_at_the_shared_choke_point():
    """One default in action_create_backtest covers UI, CLI, chatbot, rerun
    and Discord — rather than one per caller, which would drift."""
    idx = _SRC.index("def action_create_backtest(")
    nxt = _SRC.index("\ndef ", idx + 10)
    body = _SRC[idx:nxt]
    assert '_evidence["pit_mode"] = "research"' in body


def test_explicit_strict_is_still_honoured():
    """An operator who has snapshots must be able to demand strict replay."""
    assert validate_evidence_options({"pit_mode": "strict"})["pit_mode"] == "strict"


def test_contract_default_stays_strict():
    """The options module keeps its honest default; only the equities creation
    path opts out, so the fail-closed semantics remain the library behaviour."""
    assert validate_evidence_options({})["pit_mode"] == "strict"


def test_research_is_a_valid_declared_value():
    assert validate_evidence_options({"pit_mode": "research"})["pit_mode"] == "research"


def test_unknown_pit_mode_is_still_rejected():
    with pytest.raises(Exception):
        validate_evidence_options({"pit_mode": "yolo"})
