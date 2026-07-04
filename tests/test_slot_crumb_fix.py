"""Run-185254 leak #3: the direct-reserved buy was capped at min_pos*2 = $200
regardless of $49.8k available cash, consumed the last max_positions slot, and
locked the book for the final week."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.strategies.graph_nexus_analysis import (
    _direct_reserve_alloc,
    _slot_min_notional,
)


def test_direct_reserve_scales_with_budget():
    # old behavior: min(33255*0.15, 200) = $200. new: 15% of budget.
    assert _direct_reserve_alloc({}, min_pos=100.0, stock_budget=33255.0) == 4988.25


def test_direct_reserve_respects_min_pos_floor():
    assert _direct_reserve_alloc({}, min_pos=100.0, stock_budget=300.0) == 100.0


def test_direct_reserve_pct_lever():
    assert _direct_reserve_alloc(
        {"direct_reserve_alloc_pct": 0.10}, min_pos=100.0, stock_budget=10000.0
    ) == 1000.0


def test_slot_min_notional_default_off():
    assert _slot_min_notional({}, portfolio_value=100000.0) == 0.0


def test_slot_min_notional_pct():
    assert _slot_min_notional(
        {"slot_min_notional_pct": 1.5}, portfolio_value=100000.0
    ) == 1500.0
