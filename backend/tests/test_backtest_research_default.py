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


# ------------------------------------------------------- client body shapes
def test_mobile_and_web_bodies_carry_no_pit_mode():
    """Neither client sends pit_mode, which is the point.

    mobile/lib/.../backtest_detail_controller.dart builds
    {instance_id, stocks, start_date, end_date, granularity, initial_cash,
     emulate_fee_venue} and POSTs /backtests. The web UI sends the same shape.
    Both therefore inherit the equities default from action_create_backtest
    rather than each needing their own copy of the decision.
    """
    for body in (
        # mobile rerun
        {"instance_id": "alpaca-main", "stocks": ["AAPL"], "start_date": "2026-03-02",
         "end_date": "2026-03-30", "granularity": "3600", "initial_cash": 6000,
         "emulate_fee_venue": "default"},
        # web "new backtest"
        {"instance_id": "alpaca-main", "stocks": [], "start_date": "2026-03-02",
         "end_date": "2026-03-30", "granularity": "3600", "initial_cash": 6000.0},
    ):
        assert "pit_mode" not in body
        # Nothing in these bodies is an evidence option, so the contract sees {}
        opts = {k: v for k, v in body.items()
                if k in {"evidence_mode", "pit_mode", "equity_total_cost_bps",
                         "nexus_candidate_overrides"}}
        assert validate_evidence_options(opts)["pit_mode"] == "strict", (
            "the library default stays strict; the equities creation path is "
            "what opts these clients into research")


def test_every_equity_creator_shares_the_one_choke_point():
    """UI, CLI, chatbot, rerun script and Discord bot all call this function,
    so the default cannot drift between clients."""
    import subprocess
    root = os.path.dirname(_backend)
    out = subprocess.run(
        ["grep", "-rln", "action_create_backtest", f"{_backend}", f"{root}/scripts"],
        capture_output=True, text=True).stdout
    for caller in ("cli.py", "chatbot/tools.py", "rerun_backtest.py"):
        assert caller in out, f"{caller} should route through action_create_backtest"
