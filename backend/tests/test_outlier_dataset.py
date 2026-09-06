"""Version isolation and historical nominal-price eligibility."""
from outlier_features import FEATURES_TABLE, cross_section, visible_dates, feature_id
from outlier_sleeve import screen
import strategies.outlier_sleeve as mod
from portfolio_emulator import PortfolioEmulator
from test_outlier_sleeve_run_once import cfg, seed, DECIDES, VISIBLE


def test_versioned_rows_are_isolated_from_legacy_rows(store):
    store.insert(FEATURES_TABLE, [
        {'id': '2026-06-03|AAA', 'date': VISIBLE, 'symbol': 'AAA'},
        {'id': 'v2|2026-06-03|BBB', 'date': VISIBLE, 'symbol': 'BBB'}])
    assert [r['symbol'] for r in cross_section(store, VISIBLE, dataset='v2')] == ['BBB']
    assert [r['symbol'] for r in cross_section(store, VISIBLE)] == ['AAA']
    assert feature_id(VISIBLE, 'bbb', dataset='v2') == 'v2|2026-06-03|BBB'


def test_missing_dataset_never_falls_back_to_legacy_dates(store):
    seed(store)
    assert visible_dates(store, '2026-06-04', dataset='missing') == []
    assert cross_section(store, VISIBLE, dataset='missing') == []


def test_price_floor_uses_historical_nominal_price_not_future_split_units():
    common = {'ret126': .8, 'adv20': 5e7, 'n_bars': 300, 'rs_rank': .95}
    rows = [{**common, 'symbol': 'AAA', 'close': 1, 'nominal_close': 10, 'hi252': 1},
            {**common, 'symbol': 'BBB', 'close': 10, 'nominal_close': 1, 'hi252': 10}]
    assert screen(rows, cfg(), {}, set()) == ['AAA']


def test_incomplete_dataset_refuses_to_trade(store, monkeypatch):
    monkeypatch.setattr(mod, 'store', store)
    seed(store)
    out = mod.OutlierSleeve().run_once([], {}, DECIDES, cfg(feature_dataset='v2'), {},
                                      portfolio_emulator=PortfolioEmulator(6000), strategy_cache={})
    assert out.get('_nexus_executable_buys', []) == []


def test_complete_dataset_uses_its_own_manifest_and_rows(store, monkeypatch):
    monkeypatch.setattr(mod, 'store', store)
    seed(store, rows=[{'symbol': 'AAA', 'close': 100, 'hi252': 100, 'ret126': .8,
                      'adv20': 5e7, 'sma200': 70, 'n_bars': 300}])
    store.insert(FEATURES_TABLE, {'id': 'v2|2026-06-03|BBB', 'date': VISIBLE,
         'symbol': 'BBB', 'close': 50, 'nominal_close': 50, 'hi252': 50,
         'ret126': .9, 'adv20': 5e7, 'sma200': 40, 'n_bars': 300, 'rs_rank': .95})
    store.insert('PointInTimeDatasetSnapshots', {'id': 'outlier:v2', 'complete': True,
         'kind': 'outlier_features', 'dates': [VISIBLE], 'build_id': 'test-build'})
    out = mod.OutlierSleeve().run_once([], {}, DECIDES, cfg(feature_dataset='v2'), {},
                                      portfolio_emulator=PortfolioEmulator(6000), strategy_cache={})
    assert out['_nexus_executable_buys'] == ['BBB']


def test_versioned_dataset_cannot_use_undated_graph_confirmation(store, monkeypatch):
    monkeypatch.setattr(mod, 'store', store)
    seed(store)
    store.insert('PointInTimeDatasetSnapshots', {'id': 'outlier:v2', 'complete': True,
        'kind': 'outlier_features', 'dates': [VISIBLE]})
    store.insert(FEATURES_TABLE, {'id': f'v2|{VISIBLE}|AAA', 'date': VISIBLE,
        'symbol': 'AAA', 'close': 100, 'hi252': 100, 'ret126': .9, 'adv20': 5e7,
        'sma200': 70, 'n_bars': 300, 'rs_rank': 1})
    store.insert('OutlierGraphPeers', {'id': 'AAA', 'peers': ['AAA']})
    out = mod.OutlierSleeve().run_once([], {}, DECIDES,
        cfg(feature_dataset='v2', confirm_enabled=True, confirm_min_peers=1), {},
        portfolio_emulator=PortfolioEmulator(6000), strategy_cache={})
    assert out.get('_nexus_executable_buys', []) == []


def test_complete_dataset_cannot_trade_a_month_beyond_its_history(store, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(mod, 'store', store)
    store.insert('PointInTimeDatasetSnapshots', {'id': 'outlier:v2', 'complete': True,
        'kind': 'outlier_features', 'dates': [VISIBLE]})
    store.insert(FEATURES_TABLE, {'id': f'v2|{VISIBLE}|AAA', 'date': VISIBLE,
        'symbol': 'AAA', 'close': 100, 'hi252': 100, 'ret126': .9, 'adv20': 5e7,
        'sma200': 70, 'n_bars': 300, 'rs_rank': 1})
    out = mod.OutlierSleeve().run_once([], {}, datetime(2026, 7, 4, 14, tzinfo=timezone.utc),
        cfg(feature_dataset='v2'), {}, portfolio_emulator=PortfolioEmulator(6000), strategy_cache={})
    assert out.get('_nexus_executable_buys', []) == []
