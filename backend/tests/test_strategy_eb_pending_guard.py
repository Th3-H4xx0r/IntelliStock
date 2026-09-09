"""Pending-order lifecycle regression checks with synthetic quotes, not backtests."""
from datetime import datetime, timedelta, timezone

import pytest

from portfolio_emulator import create_backtest_emulator
from simulated_execution import SimulationQuote
from strategies.strategy_eb import StrategyEb
from test_strategy_eb_run_once import (
    FakeEmulator, PRICES, DECIDES, alternating, cfg, data_for,
)

T0 = datetime(2026, 6, 4, 12, tzinfo=timezone.utc)
MARKS = {'PSQ': 100.0, 'BIL': 100.0}


def native_pending(amount=7500):
    emu = create_backtest_emulator(initial_cash=10000, taker_fee=None,
                                  is_crypto=False, execution_delay=timedelta(days=1))
    receipt = emu.execute_signal('PSQ', 1, 100, timestamp=T0,
                                 cash_per_trade=amount, order_source='main_signal')
    assert receipt.accepted and not receipt.filled
    return emu


def sweep(emu, session='2026-06-05', enabled=True):
    config = cfg(pending_buy_guard_enabled=enabled, trend_filter_bars=25,
                 trend_off_book={'PSQ': .75, 'BIL': .25}, risk_off_symbol='BIL')
    cache = {'_strategy_eb_pending_targets': {'PSQ': .75, 'BIL': .25}}
    return StrategyEb()._sweep(config, cache, session, ['PSQ', 'BIL'], MARKS,
                               emu.get_portfolio_value(MARKS), emu.get_positions(),
                               emu, 0, 'OFF')


def test_sweep_does_not_duplicate_a_target_buy_across_sessions():
    emu = native_pending()
    assert sweep(emu, enabled=False).get('PSQ') == 1  # Reproduces the old defect.
    guarded = sweep(emu)
    assert 'PSQ' not in guarded
    assert guarded.get('BIL') == 1  # An independent leg is still fundable.
    assert 'PSQ' not in guarded['_nexus_position_sizes']
    assert 'PSQ' not in guarded['_nexus_executable_buys']


def test_partial_fill_keeps_target_buy_blocked():
    emu = native_pending()
    fills = emu.process_quote(SimulationQuote.from_mid(
        symbol='PSQ', timestamp=T0 + timedelta(days=2), mid=100, spread_bps=0,
        available_quantity=20))
    assert fills and emu.get_positions()['PSQ'] == 20
    assert 'PSQ' in emu.pending_execution_symbols()
    assert 'PSQ' not in sweep(emu, session='2026-06-08')


def test_resolved_order_allows_only_remaining_target_to_be_funded():
    emu = native_pending(amount=2000)
    assert 'PSQ' not in sweep(emu)
    emu.process_quote(SimulationQuote.from_mid(
        symbol='PSQ', timestamp=T0 + timedelta(days=2), mid=100, spread_bps=0))
    assert not emu.pending_execution_symbols()
    out = sweep(emu, session='2026-06-08')
    assert out.get('PSQ') == 1
    gap = .75 * emu.get_portfolio_value(MARKS) - emu.get_positions()['PSQ'] * 100
    assert 0 < out['_nexus_position_sizes']['PSQ']['buy_cash'] <= gap + .01


def test_main_rebalance_plan_also_blocks_pending_buys():
    emu = FakeEmulator()
    emu.pending_execution_symbols = lambda: (' tqqq ',)
    out = StrategyEb().run_once([], PRICES, DECIDES,
                                cfg(pending_buy_guard_enabled=True), {},
                                data=data_for(alternating(.01)), portfolio_emulator=emu)
    assert 'TQQQ' not in out and out.get('SPY') == 1
    assert 'TQQQ' not in out['_nexus_executable_buys']


@pytest.mark.parametrize('pending', [None, 'SPY', [None], {'SPY': 1}])
def test_unusable_pending_state_blocks_buys_but_keeps_sells(pending):
    emu = FakeEmulator(cash=10000, positions={'TQQQ': 200})
    emu.pending_execution_symbols = lambda: pending
    out = StrategyEb().run_once([], PRICES, DECIDES,
                                cfg(pending_buy_guard_enabled=True), {},
                                data=data_for(alternating(.01)), portfolio_emulator=emu)
    assert out.get('TQQQ') == -1
    assert not out['_nexus_executable_buys']
    assert out['_nexus_sell_enforcement'] == ['TQQQ']


def test_pending_reader_error_cannot_silently_enable_buys():
    emu = FakeEmulator()
    def unavailable():
        raise RuntimeError('pending orders unavailable')
    emu.pending_execution_symbols = unavailable
    out = StrategyEb().run_once([], PRICES, DECIDES,
                                cfg(pending_buy_guard_enabled=True), {},
                                data=data_for(alternating(.01)), portfolio_emulator=emu)
    assert out == {}


def test_missing_pending_reader_blocks_buys_when_enabled():
    out = StrategyEb().run_once([], PRICES, DECIDES,
                                cfg(pending_buy_guard_enabled=True), {},
                                data=data_for(alternating(.01)), portfolio_emulator=FakeEmulator())
    assert out == {}


def test_unrelated_pending_symbol_does_not_block_the_book():
    emu = FakeEmulator()
    emu.pending_execution_symbols = lambda: ('OTHER',)
    outputs = [StrategyEb().run_once([], PRICES, DECIDES,
                                     cfg(pending_buy_guard_enabled=enabled), {},
                                     data=data_for(alternating(.01)), portfolio_emulator=emu)
               for enabled in (False, True)]
    assert outputs[0] == outputs[1] and outputs[1].get('TQQQ') == 1


def test_disabled_flag_preserves_payload_and_cache_without_pending_api():
    outputs = []
    for config in (cfg(), cfg(pending_buy_guard_enabled=False)):
        cache = {}
        out = StrategyEb().run_once([], PRICES, DECIDES, config, {},
                                    data=data_for(alternating(.01)),
                                    portfolio_emulator=FakeEmulator(), strategy_cache=cache)
        outputs.append((out, cache))
    assert outputs[0] == outputs[1]
