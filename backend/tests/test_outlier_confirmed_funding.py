"""Execution-state regressions, using native portfolio accounting, not a backtest."""
from datetime import datetime, timezone

import strategies.outlier_sleeve as mod
from portfolio_emulator import PortfolioEmulator
from strategy_eb import session_ordinal
from test_outlier_sleeve_run_once import cfg, seed, DECIDES, SKIPS, VISIBLE


def test_reserved_cash_cannot_fund_an_outlier_entry(store, monkeypatch):
    monkeypatch.setattr(mod, 'store', store)
    seed(store)
    emu = PortfolioEmulator(initial_cash=6000)
    emu._execution_cash_reservations['sim-000000000001-GLD'] = 6000
    out = mod.OutlierSleeve().run_once([], {}, DECIDES, cfg(), {},
                                      portfolio_emulator=emu, strategy_cache={})
    assert out.get('_nexus_executable_buys', []) == []


def test_unfilled_signal_does_not_own_a_confirmed_slot(store, monkeypatch):
    monkeypatch.setattr(mod, 'store', store)
    seed(store)
    cache = {}
    mod.OutlierSleeve().run_once([], {}, DECIDES, cfg(), {},
                                portfolio_emulator=PortfolioEmulator(6000), strategy_cache=cache)
    assert cache.get(mod.SLOTS_KEY, {}) == {}
    assert set(cache.get('_outlier_pending_entries', {})) == {'AAA', 'BBB'}


def test_unsubmitted_entry_releases_capacity_on_next_session(store, monkeypatch):
    monkeypatch.setattr(mod, 'store', store)
    seed(store)
    cache = {}
    emu = PortfolioEmulator(6000)
    mod.OutlierSleeve().run_once([], {}, DECIDES, cfg(), {}, portfolio_emulator=emu, strategy_cache=cache)
    seed(store, date='2026-06-04')
    out = mod.OutlierSleeve().run_once([], {}, SKIPS, cfg(), {}, portfolio_emulator=emu, strategy_cache=cache)
    assert not cache.get(mod.SLOTS_KEY)
    assert not cache.get('_outlier_pending_entries')
    assert out.get('_nexus_sell_enforcement', []) == []


def test_confirmed_slot_uses_actual_fill_cost_and_date(store, monkeypatch):
    monkeypatch.setattr(mod, 'store', store)
    seed(store)
    cache = {}
    emu = PortfolioEmulator(6000)
    mod.OutlierSleeve().run_once([], {}, DECIDES, cfg(), {}, portfolio_emulator=emu, strategy_cache=cache)
    assert emu.buy('AAA', 1, 80, timestamp=DECIDES)
    seed(store, date='2026-06-04')
    mod.OutlierSleeve().run_once([], {'AAA': 100}, SKIPS, cfg(), {}, portfolio_emulator=emu, strategy_cache=cache)
    slot = cache[mod.SLOTS_KEY]['AAA']
    actual_fill = emu.get_trade_history()[0]
    assert slot['entry_cost'] == actual_fill['total']
    assert slot['entry_px'] == actual_fill['price']
    assert slot['entry_ordinal'] == session_ordinal('2026-06-04')
    assert 'BBB' not in cache[mod.SLOTS_KEY]


def test_exit_keeps_ownership_until_sale_fills(store, monkeypatch):
    monkeypatch.setattr(mod, 'store', store)
    seed(store, rows=[{'symbol': 'AAA', 'close': 60, 'hi252': 100, 'ret126': -.2,
                      'adv20': 5e7, 'sma200': 70, 'n_bars': 300}])
    emu = PortfolioEmulator(6000)
    assert emu.buy('AAA', 1, 100, timestamp=datetime(2026, 1, 5, 21, tzinfo=timezone.utc))
    cache = {mod.SLOTS_KEY: {'AAA': {'entry_px': 100, 'entry_cost': 100,
              'entry_ordinal': session_ordinal('2026-01-05'), 'proven': True,
              'below': 4, 'last_eval': ''}}}
    out = mod.OutlierSleeve().run_once([], {'AAA': 60}, DECIDES, cfg(), {}, portfolio_emulator=emu, strategy_cache=cache)
    assert out['AAA'] == -1
    assert 'AAA' in cache[mod.SLOTS_KEY]
    assert emu.sell('AAA', 1, 60, timestamp=DECIDES)
    seed(store, date='2026-06-04')
    mod.OutlierSleeve().run_once([], {}, SKIPS, cfg(), {}, portfolio_emulator=emu, strategy_cache=cache)
    assert 'AAA' not in cache[mod.SLOTS_KEY]


def test_live_adapter_cash_interface_does_not_raise_or_spend_reserved_cash(store, monkeypatch):
    from broker_adapters.alpaca import AlpacaAdapter
    monkeypatch.setattr(mod, 'store', store)
    seed(store)
    adapter = AlpacaAdapter.__new__(AlpacaAdapter)
    adapter._cash = 6000
    adapter._buying_power = 6000
    adapter._positions = {}
    adapter._trades = []
    adapter._execution_cash_reservations = {'sim-pending-GLD': 6000}
    out = mod.OutlierSleeve().run_once([], {}, DECIDES, cfg(), {},
                         portfolio_emulator=adapter, strategy_cache={}, mode='live')
    assert out.get('_nexus_executable_buys', []) == []


def test_partial_fill_retains_pending_reservation_without_duplicate_entry(store, monkeypatch):
    monkeypatch.setattr(mod, 'store', store)
    seed(store)
    emu = PortfolioEmulator(6000)
    cache = {mod.PENDING_KEY: {'AAA': {'entry_cost': 200, 'signal_session': '2026-06-02'}}}
    assert emu.buy('AAA', 1, 80, timestamp=DECIDES)
    emu._execution_cash_reservations['sim-000000000001-AAA'] = 100
    out = mod.OutlierSleeve().run_once([], {'AAA': 100}, DECIDES, cfg(max_slots=1), {},
                                      portfolio_emulator=emu, strategy_cache=cache)
    assert out.get('_nexus_executable_buys', []) == []
    assert cache[mod.PENDING_KEY]['AAA']['entry_cost'] == 100
    assert cache[mod.SLOTS_KEY]['AAA']['entry_cost'] == emu.get_trade_history()[0]['total']


def test_pending_sale_is_not_submitted_again(store, monkeypatch):
    monkeypatch.setattr(mod, 'store', store)
    seed(store)
    emu = PortfolioEmulator(6000)
    assert emu.buy('AAA', 1, 100, timestamp=DECIDES)
    emu._execution_position_reservations['sim-000000000002-AAA'] = 1
    cache = {mod.SLOTS_KEY: {'AAA': {'entry_px': 100, 'entry_cost': 100,
             'entry_ordinal': session_ordinal('2026-01-05'), 'proven': True,
             'below': 0, 'last_eval': '', 'exit_reason': 'time'}}}
    out = mod.OutlierSleeve().run_once([], {'AAA': 100}, DECIDES, cfg(), {},
                                      portfolio_emulator=emu, strategy_cache=cache)
    assert out.get('AAA', 0) == 0
    assert 'AAA' in cache[mod.SLOTS_KEY]
