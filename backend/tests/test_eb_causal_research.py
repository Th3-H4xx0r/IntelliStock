"""Observable accounting, calendar and risk contracts; no network or broker access."""
import sys
from pathlib import Path
import pytest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
import eb_causal_research as m


def row(day,price=100.,shares=10.,cash=0.):
    return {'timestamp':day+'T13:00:00','prices':{'ABC':price},'positions_snapshot':{'ABC':shares},'cash':cash,'value':cash+shares*price}


def test_control_reconciles_every_row_and_rejects_bad_nav():
    rows=[row('2025-01-02')]
    assert m.reconcile(rows)==0.
    rows[0]['value']+=1
    with pytest.raises(ValueError,match='NAV'):m.reconcile(rows)


def test_exchange_calendar_skips_holiday_and_uses_previous_completed_close():
    assert m.previous_session('2025-02-18')=='2025-02-14'
    assert m.previous_session('2025-02-17')=='2025-02-14'
    assert m.previous_session('2025-02-19')=='2025-02-18'


def test_alignment_requires_actual_prior_session_prices_and_does_not_shop_dates():
    rows=[row('2025-02-18',101.)]
    bars={'ABC':{'2025-02-14':{'Close':101.},'2025-02-13':{'Close':100.}}}
    assert m.align(rows,bars)[0]['session']=='2025-02-14'
    rows[0]['prices']['ABC']=100.
    with pytest.raises(ValueError,match='price'):m.align(rows,bars)


def test_alignment_preserves_multiple_intraday_events_with_one_mark_date():
    rows=[row('2025-02-17'),row('2025-02-18',shares=9,cash=100)]
    bars={'ABC':{'2025-02-14':{'Close':100.}}}
    aligned=m.align(rows,bars)
    assert len(aligned)==2
    assert aligned[0]['session']==aligned[1]['session']


def test_dividend_uses_prior_ex_date_holdings_and_stays_outside_trading_cash():
    rows=[{**row('2025-01-02'),'session':'2025-01-02'},
          {**row('2025-01-03',99.,shares=20.,cash=-990.),'session':'2025-01-03'}]
    events=[{'symbol':'ABC','ex_date':'2025-01-03','amount':1.,'basis':'split'}]
    corrected=m.account_distributions(rows,events,price_basis='split')
    assert corrected[1]['distribution_receivable']==10.
    assert corrected[1]['corrected_value']==1000.
    assert corrected[1]['cash']==-990.
    assert rows[1]['value']==990.


def test_dividend_on_initial_session_does_not_credit_fresh_cash_account():
    rows=[{**row('2025-01-03',99.),'session':'2025-01-03'}]
    events=[{'symbol':'ABC','ex_date':'2025-01-03','amount':1.,'basis':'split'}]
    assert m.account_distributions(rows,events,price_basis='split')[0]['distribution_receivable']==0.


def test_distribution_basis_and_duplicate_events_fail_closed():
    rows=[{**row('2025-01-02'),'session':'2025-01-02'}]
    events=[{'symbol':'ABC','ex_date':'2025-01-03','amount':1.,'basis':'raw'}]
    with pytest.raises(ValueError,match='basis'):m.account_distributions(rows,events,price_basis='split')
    with pytest.raises(ValueError,match='adjusted'):m.account_distributions(rows,[],price_basis='all')
    events[0]['basis']='split'
    with pytest.raises(ValueError,match='duplicate'):m.account_distributions(rows,events*2,price_basis='split')


def test_interval_includes_opening_loss_from_prior_session():
    rows=[{'session':'2024-12-31','value':100.},{'session':'2025-01-02','value':90.},{'session':'2025-01-03','value':95.}]
    assert m.interval_return(rows,'2025-01-01','2025-01-03')==pytest.approx(-.05)


def test_risk_control_never_adds_core_or_leverage_and_caps_metals():
    w={'TQQQ':.4,'GLD':.4,'GDX':.2,'BIL':0.}
    r=m.protect(w,['TQQQ','GLD','GDX','BIL'],np.diag([.8**2,.3**2,.5**2,0.]),risk=True,diversify=False)
    assert sum(r.values())==pytest.approx(1.)
    assert min(r.values())>=0
    assert r['TQQQ']<=w['TQQQ']
    assert r['GLD']+r['GDX']<=.4
    v=np.array([r[s] for s in w])
    assert np.sqrt(v@np.diag([.8**2,.3**2,.5**2,0.])@v)<=.2+1e-10


def test_disabled_transform_is_identity_and_diversifier_does_not_change_core():
    w={'TQQQ':.4,'GLD':.3,'GDX':.15,'XLE':.15}
    assert m.protect(w,list(w),np.eye(4),risk=False,diversify=False)==w
    r=m.protect(w,list(w),np.eye(4),risk=False,diversify=True)
    assert r['TQQQ']==.4
    assert r['KMLM']==pytest.approx(.12)
    assert sum(r.values())==pytest.approx(1.)


def test_debit_spread_risk_includes_fees_and_existing_open_positions():
    q={'feed':'opra','age_seconds':2,'bid':1.9,'ask':2.0,'bid_size':5,'ask_size':5}
    args=dict(width=5.,debit=2.,quantity=2,multiplier=100,fees=4.,existing_risk=0.,budget=600.,quotes=[q,q])
    assert m.spread_risk(**args)==404.
    with pytest.raises(ValueError,match='budget'):m.spread_risk(**{**args,'quantity':3})
    with pytest.raises(ValueError,match='budget'):m.spread_risk(**{**args,'existing_risk':250.})


@pytest.mark.parametrize('bad',[{'feed':'indicative'},{'age_seconds':31},{'bid':3.},{'ask_size':0},{'ask':float('nan')}])
def test_options_reject_bad_quotes(bad):
    q={'feed':'opra','age_seconds':2,'bid':1.9,'ask':2.,'bid_size':5,'ask_size':5,**bad}
    with pytest.raises(ValueError):m.spread_risk(width=5,debit=2,quantity=1,multiplier=100,fees=2,existing_risk=0,budget=600,quotes=[q,q])


def test_raw_dividends_are_rebased_for_later_splits_and_components_summed():
    raw={'cash_dividends':[{'id':'a','symbol':'XLE','ex_date':'2024-01-02','rate':2.,'payable_date':'2024-01-09'},
                           {'id':'b','symbol':'XLE','ex_date':'2024-01-02','rate':1.,'payable_date':'2024-01-09'}],
         'forward_splits':[{'symbol':'XLE','ex_date':'2025-12-05','old_rate':1,'new_rate':2}]}
    out=m.normalize_distributions(raw,basis_asof='2026-08-28')
    assert out==[{'symbol':'XLE','ex_date':'2024-01-02','payable_date':'2024-01-09','amount':1.5,'basis':'split'}]
    assert m.normalize_distributions(raw,basis_asof='2024-12-31')[0]['amount']==3.
    raw['cash_dividends'].append(raw['cash_dividends'][0])
    with pytest.raises(ValueError,match='duplicate'):m.normalize_distributions(raw,basis_asof='2026-08-28')


def market_fixture():
    dates=[x.strftime('%Y-%m-%d') for x in m._calendar().sessions_in_range('2024-01-02','2024-05-31')]
    symbols=['TQQQ','GLD','GDX','XLE','BIL','KMLM','SPY']
    bars={s:{d:{'Open':100+i*.1,'Close':100+i*.1} for i,d in enumerate(dates)} for s in symbols}
    reference=[{'session':d,'weights':{'TQQQ':.4,'GLD':.3,'GDX':.15,'XLE':.15}} for d in dates]
    return dates,bars,reference


def test_screen_has_no_future_price_dependency_and_accounts_for_costs():
    dates,bars,reference=market_fixture()
    start=dates[45];end=dates[-1];cut=dates[65]
    result=m.run_screen(reference,bars,[],start,end,risk=False,diversify=False)
    assert result[0]['value']==6000
    assert result[1]['fees_paid']>0
    assert all(r['cash']>=-1e-8 for r in result)
    changed={s:{d:{k:(v*1.5 if d>cut else v) for k,v in b.items()} for d,b in v.items()} for s,v in bars.items()}
    again=m.run_screen(reference,changed,[],start,end,risk=False,diversify=False)
    assert [r for r in result if r['session']<=cut]==[r for r in again if r['session']<=cut]


def test_screen_dividend_entitlement_precedes_ex_date_trades_and_payment():
    dates,bars,reference=market_fixture()
    for r in reference:r['weights']={'BIL':1.}
    start=dates[45];ex=dates[48];pay=dates[51]
    event={'symbol':'BIL','ex_date':ex,'payable_date':pay,'amount':1.,'basis':'split'}
    result=m.run_screen(reference,bars,[event],start,dates[53],risk=False,diversify=False)
    by={r['session']:r for r in result}
    assert by[ex]['receivable']>0
    assert by[pay]['receivable']==0
    assert by[pay]['dividends_paid']>0


def test_screen_rejects_missing_trading_day_and_reference_lookahead():
    dates,bars,reference=market_fixture()
    del bars['GLD'][dates[48]]
    with pytest.raises(ValueError,match='missing'):m.run_screen(reference,bars,[],dates[45],dates[53],risk=False,diversify=False)
    dates,bars,reference=market_fixture()
    with pytest.raises(ValueError,match='reference'):m.run_screen(reference[46:],bars,[],dates[45],dates[53],risk=False,diversify=False)


def vertical_fixture():
    common={'underlying':'SPY','expiry':'2026-10-16','type':'call','multiplier':100,
            'feed':'opra','age_seconds':2,'bid_size':5,'ask_size':5}
    return [{**common,'side':'buy','strike':650.,'bid':3.8,'ask':4.0},
            {**common,'side':'sell','strike':655.,'bid':2.,'ask':2.2}]


def test_vertical_uses_long_ask_short_bid_and_validates_contract_identity():
    legs=vertical_fixture()
    assert m.debit_vertical_risk(legs,asof='2026-09-06',quantity=2,fees=4,existing_risk=0,budget=600)==404
    for change in ({'expiry':'2026-11-20'},{'underlying':'QQQ'},{'type':'put'},{'multiplier':10},{'side':'buy'},{'strike':645.}):
        with pytest.raises(ValueError):m.debit_vertical_risk([legs[0],{**legs[1],**change}],asof='2026-09-06',quantity=1,fees=2,existing_risk=0,budget=600)
    with pytest.raises(ValueError,match='expiry'):m.debit_vertical_risk(legs,asof='2026-10-16',quantity=1,fees=2,existing_risk=0,budget=600)


def test_bear_put_vertical_requires_higher_long_strike():
    legs=vertical_fixture()
    legs[0].update(type='put',strike=655.)
    legs[1].update(type='put',strike=650.)
    assert m.debit_vertical_risk(legs,asof='2026-09-06',quantity=1,fees=2,existing_risk=0,budget=600)==202


def test_total_return_benchmark_reinvests_actual_distribution_once():
    prices={'2025-01-02':{'Close':100.},'2025-01-03':{'Close':99.},'2025-01-06':{'Close':100.}}
    events=[{'symbol':'SPY','ex_date':'2025-01-03','amount':1.,'basis':'split'}]
    rows=m.benchmark_total_return(prices,events,list(prices))
    assert rows[1]['value']==6000.
    assert rows[-1]['value']==pytest.approx(6000*100/99)


def test_rolling_report_keeps_bear_profit_and_spy_outperformance_separate():
    rows=[{'session':f'2025-01-0{i+1}','value':v} for i,v in enumerate([100,99,98,97])]
    spy=[{'session':r['session'],'value':v} for r,v in zip(rows,[100,95,90,85])]
    result=m.rolling_metrics(rows,spy,horizons=(2,))['2']
    assert result['bear_n']==2
    assert result['bear_positive_pct']==0.
    assert result['beat_pct']==100.


def test_risk_covariance_uses_only_completed_past_prices():
    dates,bars,reference=market_fixture();cut=dates[65]
    a=m.run_screen(reference,bars,[],dates[45],dates[-1],risk=True,diversify=True)
    for symbol in ['TQQQ','GLD','GDX','KMLM']:
        for i,day in enumerate(dates):
            if day>cut:
                bars[symbol][day]['Open']*=1.5 if i%2 else .8
                bars[symbol][day]['Close']*=1.5 if i%2 else .8
    b=m.run_screen(reference,bars,[],dates[45],dates[-1],risk=True,diversify=True)
    assert [r for r in a if r['session']<=cut]==[r for r in b if r['session']<=cut]


def test_calendar_month_window_uses_available_close_at_month_offset():
    rows=[{'session':d,'value':v} for d,v in [('2024-02-29',100.),('2024-03-28',99.),('2024-03-29',98.),('2024-04-01',97.)]]
    spy=[{**r,'value':100.} for r in rows]
    out=m.calendar_metrics(rows,spy,months=(1,))['1']
    assert out['n']==2
    assert out['worst_pct']==pytest.approx(-3.)
    assert out['beat_pct']==0.
