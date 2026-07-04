"""Run-185254 leak #5: anchor reinforce tops winners up to only 1.3x ENTRY
notional (PANW +21% got one $835 add) — adds shrink as winners run. And the
positive-graph gate kept DNOW (-$788) frozen because graph raws are
sticky-positive; a loss override lets rotation evict bleeding holds."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.strategies.graph_nexus_analysis import (
    _anchor_reinforce_target,
    _graph_gate_holds,
)


def test_anchor_target_default_is_entry_multiple():
    # default lever 0.0 -> legacy 1.3x entry notional (stage-1 mult)
    assert _anchor_reinforce_target(
        {}, entry_notional=6809.52, portfolio_value=100000.0
    ) == 6809.52 * 1.3


def test_anchor_target_pct_of_portfolio():
    assert _anchor_reinforce_target(
        {"anchor_reinforce_target_pct": 12.0},
        entry_notional=6809.52, portfolio_value=100000.0,
    ) == 12000.0


def test_anchor_target_pct_ignored_without_portfolio_value():
    # No portfolio value available (e.g. caller couldn't resolve it) ->
    # legacy entry-multiple behavior, never a zero/negative target.
    assert _anchor_reinforce_target(
        {"anchor_reinforce_target_pct": 12.0},
        entry_notional=1000.0, portfolio_value=0.0,
    ) == 1000.0 * 1.3


def test_graph_gate_holds_profitable_positive():
    assert _graph_gate_holds(
        {"rotation_positive_graph_gate_enabled": True},
        held_raw=0.99, held_pnl_pct=17.7,
    ) is True


def test_graph_gate_releases_deep_loser():
    cfg = {"rotation_positive_graph_gate_enabled": True,
           "rotation_graph_gate_max_loss_pct": 5.0}
    assert _graph_gate_holds(cfg, held_raw=0.70, held_pnl_pct=-6.2) is False


def test_graph_gate_loss_override_off_by_default():
    assert _graph_gate_holds(
        {"rotation_positive_graph_gate_enabled": True},
        held_raw=0.70, held_pnl_pct=-6.2,
    ) is True


def test_graph_gate_disabled_never_holds():
    assert _graph_gate_holds({}, held_raw=0.99, held_pnl_pct=17.7) is False


def test_graph_gate_negative_raw_never_holds():
    assert _graph_gate_holds(
        {"rotation_positive_graph_gate_enabled": True},
        held_raw=-0.1, held_pnl_pct=-6.2,
    ) is False
