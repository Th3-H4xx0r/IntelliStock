"""Loss-policy decisions and actual-fill lifecycle; no historical simulation."""
import json
from datetime import datetime, timezone

import pytest
from outlier_sleeve import exit_decisions, winner_cap_trims
from strategy_eb import session_ordinal
import strategies.outlier_sleeve as wrapper
from portfolio_emulator import PortfolioEmulator
from test_outlier_sleeve import cfg, row
from test_outlier_sleeve_run_once import seed, DECIDES


def slot():
    return {'entry_px':100.0,'entry_cost':180.0,
            'entry_ordinal':session_ordinal('2026-01-05'),
            'proven':False,'below':0,'last_eval':''}


def policy(**overrides):
    return cfg(loss_controls_enabled=True,initial_stop_pct=.12,
               winner_trail_pct=.20,winner_trail_activation_gain=.25,**overrides)


@pytest.mark.parametrize('close',[88.0,75.0])
def test_entry_loss_exits_without_waiting_for_sma_or_time_stop(close):
    slots={'AAA':slot()}
    assert exit_decisions(slots,{'AAA':row('AAA',close=close,sma200=60)},'2026-01-06',policy())=={'AAA':'loss'}
    assert exit_decisions(slots,{'AAA':row('AAA',close=close,sma200=60)},'2026-01-06',policy())=={}


def test_winner_trail_activates_and_tracks_only_post_entry_closes():
    slots={'AAA':slot()}
    assert exit_decisions(slots,{'AAA':row('AAA',close=125,hi252=1000)},'2026-01-06',policy())=={}
    assert slots['AAA']['risk_peak_close']==125
    assert slots['AAA']['risk_trail_active'] is True
    assert exit_decisions(slots,{'AAA':row('AAA',close=150)},'2026-01-07',policy())=={}
    assert exit_decisions(slots,{'AAA':row('AAA',close=120,sma200=80)},'2026-01-08',policy())=={'AAA':'trail'}


def test_twenty_percent_giveback_before_activation_does_not_trigger_trail():
    slots={'AAA':slot()}
    exit_decisions(slots,{'AAA':row('AAA',close=124)},'2026-01-06',policy())
    assert slots['AAA']['risk_trail_active'] is False
    assert exit_decisions(slots,{'AAA':row('AAA',close=99.2,sma200=80)},'2026-01-07',policy())=={}


def test_prefill_observation_cannot_set_a_peak_or_trigger_a_loss():
    slots={'AAA':slot()}
    assert exit_decisions(slots,{'AAA':row('AAA',close=300)},'2026-01-02',policy())=={}
    assert 'risk_peak_close' not in slots['AAA']
    assert exit_decisions(slots,{'AAA':row('AAA',close=100)},'2026-01-05',policy())=={}
    assert slots['AAA']['risk_peak_close']==100


def test_peak_and_activation_survive_serialization_and_fill_reconciliation():
    emulator=PortfolioEmulator(6000)
    assert emulator.buy('AAA',1,100,timestamp=datetime(2026,1,5,21,tzinfo=timezone.utc))
    slots={'AAA':slot()}
    exit_decisions(slots,{'AAA':row('AAA',close=150)},'2026-01-06',policy())
    slots=json.loads(json.dumps(slots))
    wrapper._reconcile_entries(slots,{},emulator.get_positions(),emulator,'2026-01-07')
    assert slots['AAA']['risk_peak_close']==150 and slots['AAA']['risk_trail_active'] is True
    assert exit_decisions(slots,{'AAA':row('AAA',close=119.9)},'2026-01-07',policy())=={'AAA':'trail'}


def test_gap_loss_emits_a_full_enforced_exit_and_retains_slot_until_fill(store,monkeypatch):
    monkeypatch.setattr(wrapper,'store',store)
    seed(store,rows=[row('AAA',close=70,sma200=60)])
    emulator=PortfolioEmulator(6000)
    assert emulator.buy('AAA',1,100,timestamp=datetime(2026,1,5,21,tzinfo=timezone.utc))
    cache={wrapper.SLOTS_KEY:{'AAA':{**slot(),'proven':True}}}
    out=wrapper.OutlierSleeve().run_once([],{'AAA':70},DECIDES,policy(confirm_enabled=False),{},portfolio_emulator=emulator,strategy_cache=cache)
    assert out['AAA']==-1 and out['_nexus_sell_enforcement']==['AAA']
    assert 'sell_fraction' not in out['_nexus_position_sizes'].get('AAA',{})
    assert cache[wrapper.SLOTS_KEY]['AAA']['exit_reason']=='loss'


def test_unfilled_entry_never_creates_peak_state(store,monkeypatch):
    monkeypatch.setattr(wrapper,'store',store);seed(store)
    cache={}
    wrapper.OutlierSleeve().run_once([],{},DECIDES,policy(confirm_enabled=False),{},portfolio_emulator=PortfolioEmulator(6000),strategy_cache=cache)
    assert cache.get(wrapper.SLOTS_KEY,{})=={}


def test_fifteen_percent_cap_trims_only_excess():
    assert winner_cap_trims({'AAA':slot()},{'AAA':16},{'AAA':100},10000,
                            cfg(winner_cap_fraction=.15))=={'AAA':.0625}


def test_disabled_policy_preserves_legacy_exit_decision():
    slots={'AAA':slot()}
    assert exit_decisions(slots,{'AAA':row('AAA',close=70,sma200=60)},'2026-01-06',cfg())=={}
    assert 'risk_peak_close' not in slots['AAA']


@pytest.mark.parametrize(('key','value'),[('initial_stop_pct',0),('initial_stop_pct',float('nan')),
                                         ('winner_trail_pct',1),('winner_trail_activation_gain',-.1)])
def test_invalid_enabled_loss_policy_is_rejected(key,value):
    config=policy();config[key]=value
    with pytest.raises(ValueError):
        exit_decisions({'AAA':slot()},{'AAA':row('AAA')},'2026-01-06',config)


def test_invalid_price_cannot_mutate_loss_tracking_state():
    slots={'AAA':slot()}
    assert exit_decisions(slots,{'AAA':row('AAA',close=float('nan'))},'2026-01-06',policy())=={}
    assert slots['AAA']['last_eval']=='' and 'risk_peak_close' not in slots['AAA']


@pytest.mark.parametrize('key', ['risk_entry_px', 'risk_peak_close'])
@pytest.mark.parametrize('value', [0, float('nan'), 'bad', float('inf'), None])
def test_corrupt_persisted_risk_prices_force_exit(key, value):
    slots = json.loads(json.dumps({'AAA': {**slot(), key: value}}))
    assert exit_decisions(slots, {'AAA': row('AAA', close=100)},
                          '2026-01-06', policy()) == {'AAA': 'risk_state'}


@pytest.mark.parametrize('value', ['bad', None, float('inf')])
def test_corrupt_entry_ordinal_forces_risk_exit(value):
    slots = {'AAA': {**slot(), 'entry_ordinal': value}}
    assert exit_decisions(slots, {'AAA': row('AAA', close=100)},
                          '2026-01-06', policy()) == {'AAA': 'risk_state'}


def test_corrupt_trail_activation_forces_risk_exit():
    slots = {'AAA': {**slot(), 'risk_trail_active': 'false'}}
    assert exit_decisions(slots, {'AAA': row('AAA', close=100)},
                          '2026-01-06', policy()) == {'AAA': 'risk_state'}
