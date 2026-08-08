"""A rotation's paired buy must be able to spend its own sell (bt 559864).

Execution is NEXT-EVENT: a sell submitted while the 15:00 bar is processed
fills at the 16:00 quote, so in backtest its proceeds land after the same bar's
buy has been sized. broker.py assumed the emulator credited synchronously.

    Momentum portfolio swap: sell EEM (pnl=+2.6%) -> buy SNDK (score=1.013, $743)
    Buy gate inputs for SNDK: cash=$125.31 cash_per_trade=$742.82
                              available=$125.31 -> PASS
    FILL BUY SNDK qty=0.31934420 price=392.371487

The book sold $1,845 that bar (SPY $1,107.91 + EEM $737.54) and bought the
winner with $125 -- 2.1% of NAV against a 12.4% intent. Per the objective that
is the difference between the year and noise.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nexus_broker_utils import buy_ceiling  # noqa: E402


NAV = 6000.0


def test_the_bug_the_winner_enters_as_noise():
    """Without the credit the buy is clamped to pre-sell cash."""
    cash = 125.31
    intended = 742.82
    assert min(intended, cash) == pytest.approx(125.31)
    assert min(intended, cash) / NAV < 0.025


def test_crediting_the_paired_sell_restores_the_intended_size():
    """EEM $737.54 booked -> the ceiling covers the $742.82 the swap sized."""
    ceiling = buy_ceiling(125.31, [737.54], enabled=True)
    assert ceiling >= 742.82
    assert min(742.82, ceiling) / NAV == pytest.approx(0.1238, abs=0.001)


def test_haircut_means_the_ceiling_never_exceeds_cash_plus_proceeds():
    """The 5% haircut is the safety margin against a partial fill."""
    ceiling = buy_ceiling(125.31, [737.54], enabled=True)
    assert ceiling <= 125.31 + 737.54


def test_kill_switch_restores_the_old_behaviour_exactly():
    assert buy_ceiling(125.31, [737.54], enabled=False) == pytest.approx(125.31)


def test_no_sells_this_cycle_changes_nothing():
    assert buy_ceiling(125.31, [], enabled=True) == pytest.approx(125.31)


def test_multiple_sells_in_one_cycle_all_count():
    """01-13 freed SPY $1,107.91 and EEM $737.54 in the same cycle."""
    one = buy_ceiling(125.31, [737.54], enabled=True)
    both = buy_ceiling(125.31, [737.54, 1107.91], enabled=True)
    assert both > one
    assert both <= 125.31 + 737.54 + 1107.91


def test_a_full_size_entry_is_what_the_objective_asks_for():
    """12.4% of NAV, not 2.1%."""
    ceiling = buy_ceiling(125.31, [737.54], enabled=True)
    got = min(742.82, ceiling)
    assert 0.10 <= got / NAV <= 0.15
