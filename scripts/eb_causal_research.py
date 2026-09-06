"""Offline causal accounting and bounded research transforms for Strategy EB."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from functools import lru_cache
import copy
import math
import numpy as np


def reconcile(rows, tolerance=1e-7):
    """Verify the immutable price-only control without changing any holding."""
    worst = 0.0
    for row in rows:
        calculated = float(row['cash']) + sum(float(q) * float(row['prices'][s])
                    for s, q in row['positions_snapshot'].items())
        error = abs(calculated - float(row['value']))
        if not math.isfinite(error) or error > tolerance:
            raise ValueError(f"NAV reconciliation failed at {row['timestamp']}: {error}")
        worst = max(worst, error)
    return worst


@lru_cache(maxsize=1)
def _calendar():
    import exchange_calendars as xc
    return xc.get_calendar('XNYS', start='1990-01-01', end='2030-12-31')


@lru_cache(maxsize=10000)
def previous_session(day):
    """For a pre-open engine valuation, latest completed exchange session."""
    previous = date.fromisoformat(day) - timedelta(days=1)
    return _calendar().date_to_session(previous.isoformat(), direction='previous').strftime('%Y-%m-%d')


def align(rows, bars, tolerance=0.011):
    """Attach expected mark session and check against that date, never shop dates.

    Does not retime orders: timestamp remains unchanged. Multiple observations
    can share a price session; callers must audit trade timing independently.
    Bars must be split-only on the same share basis as stored prices.
    """
    result = []
    previous_time = ''
    for row in rows:
        if row['timestamp'] < previous_time:
            raise ValueError('rows must be chronological')
        previous_time = row['timestamp']
        session = previous_session(row['timestamp'][:10])
        for symbol, price in row['prices'].items():
            mark = bars.get(symbol, {}).get(session, {}).get('Close')
            if mark is None or not math.isfinite(mark) or abs(mark-price)>tolerance:
                raise ValueError(f'price mismatch: {symbol} {row["timestamp"]} expected session {session}')
        result.append({**copy.deepcopy(row), 'session':session})
    return result


def account_distributions(rows, events, *, price_basis):
    """Fixed-share-schedule total-return attribution, not reinvestment or cash settlement.

    Inputs must already use genuine session-close holdings. Engine observations
    retimed from pre-open valuations require separate execution-timing validation.
    Income is recognized on ex-date and tracked outside the trading cash balance.
    """
    if price_basis!='split':
        raise ValueError('dividend-adjusted/raw prices require a different accounting basis')
    seen=set()
    for event in events:
        if event.get('basis')!=price_basis:
            raise ValueError('distribution/share basis mismatch')
        key=(event['symbol'],event['ex_date'],event.get('type','cash'))
        if key in seen:raise ValueError('duplicate distribution event')
        seen.add(key)
        if not math.isfinite(event['amount']) or event['amount']<0:
            raise ValueError('invalid distribution amount')
    sessions=[r['session'] for r in rows]
    if sessions!=sorted(sessions):raise ValueError('sessions must be chronological')
    credits=[]
    for event in events:
        i=bisect_left(sessions,event['ex_date'])-1
        if i<0:continue
        shares=rows[i]['positions_snapshot'].get(event['symbol'],0.)
        if shares<0:raise ValueError('long-only attribution required')
        credits.append((event['ex_date'],shares*event['amount']))
    credits.sort()
    j=0;income=0.;result=[]
    for row in rows:
        while j<len(credits) and credits[j][0]<=row['session']:
            income+=credits[j][1];j+=1
        result.append({**copy.deepcopy(row),'distribution_receivable':income,
                       'corrected_value':row['value']+income})
    return result


def interval_return(rows, start, end, *, field='value'):
    """Include the opening move by using the last valuation before start."""
    by_session={r['session']:r[field] for r in rows}
    sessions=sorted(by_session)
    left=bisect_left(sessions,start)-1
    right=bisect_right(sessions,end)-1
    if left<0 or right<=left:raise ValueError('interval lacks boundary valuations')
    return by_session[sessions[right]]/by_session[sessions[left]]-1.


def protect(weights, symbols, covariance, *, risk, diversify):
    """Frozen R/D screen: risk reduction only; no core leverage increase.

    Covariance is annualized and must include every nonzero final holding.
    Callers provide only returns available at the decision time.
    """
    out=dict(weights)
    if any(not math.isfinite(v) or v<0 for v in out.values()) or sum(out.values())>1+1e-9:
        raise ValueError('invalid long-only weights')
    if not risk and not diversify:return out
    if diversify:
        amount=.2*sum(v for s,v in out.items() if s!='TQQQ')
        out={s:(v if s=='TQQQ' else v*.8) for s,v in out.items()}
        out['KMLM']=out.get('KMLM',0.)+amount
    if not risk:return out
    total=sum(out.values())
    metals=out.get('GLD',0.)+out.get('GDX',0.)
    if metals>.4:
        for s in ('GLD','GDX'):out[s]=out.get(s,0.)*.4/metals
        out['BIL']=out.get('BIL',0.)+metals-.4
    if 'BIL' not in symbols or any(s not in symbols for s,w in out.items() if w):
        raise ValueError('covariance missing a held symbol')
    cov=np.asarray(covariance,dtype=float)
    if cov.shape!=(len(symbols),len(symbols)) or not np.isfinite(cov).all() or not np.allclose(cov,cov.T):
        raise ValueError('invalid covariance')
    if np.linalg.eigvalsh(cov).min() < -1e-9:raise ValueError('covariance not positive semidefinite')
    w=np.array([out.get(s,0.) for s in symbols]);cash=np.zeros(len(symbols));cash[symbols.index('BIL')]=total
    if cash@cov@cash>.2**2:raise ValueError('cash leg exceeds risk ceiling')
    if w@cov@w>.2**2:
        lo,hi=0.,1.
        for _ in range(60):
            alpha=(lo+hi)/2;candidate=alpha*w+(1-alpha)*cash
            if candidate@cov@candidate<=.2**2:lo=alpha
            else:hi=alpha
        w=lo*w+(1-lo)*cash
    return {s:float(v) for s,v in zip(symbols,w)}


def spread_risk(*, width, debit, quantity, multiplier, fees, existing_risk, budget, quotes):
    """Payoff/quote feasibility only, not order submission or expected return."""
    values=(width,debit,quantity,multiplier,fees,existing_risk,budget)
    if not all(math.isfinite(v) for v in values):raise ValueError('nonfinite spread input')
    if not 0<debit<width or quantity<=0 or quantity!=int(quantity) or multiplier!=100:
        raise ValueError('invalid standard debit vertical')
    if min(fees,existing_risk)<0 or budget<=0 or len(quotes)!=2:
        raise ValueError('invalid risk/quote inputs')
    for q in quotes:
        if q.get('feed')!='opra':raise ValueError('actual OPRA quotes required')
        nums=[q[k] for k in ('bid','ask','age_seconds','bid_size','ask_size')]
        if not all(math.isfinite(v) for v in nums):raise ValueError('invalid quote')
        if not (0<=q['bid']<=q['ask'] and q['ask']>0 and 0<=q['age_seconds']<=30
                and min(q['bid_size'],q['ask_size'])>=quantity):
            raise ValueError('stale/crossed/insufficient quote')
    loss=debit*quantity*multiplier+fees
    if loss+existing_risk>budget:raise ValueError('aggregate options budget exceeded')
    return loss


def normalize_distributions(actions, *, basis_asof):
    """Sum distinct distribution components; restate raw amounts for later splits."""
    seen=set();grouped={}
    splits=actions.get('forward_splits',[])+actions.get('reverse_splits',[])
    for event in actions.get('cash_dividends',[]):
        if event['id'] in seen:raise ValueError('duplicate corporate-action id')
        seen.add(event['id'])
        if event['ex_date']>basis_asof:continue
        factor=1.
        for split in splits:
            if split['symbol']==event['symbol'] and event['ex_date']<split['ex_date']<=basis_asof:
                factor*=float(split['new_rate'])/float(split['old_rate'])
        rate=float(event['rate'])/factor
        if not math.isfinite(rate) or rate<0:raise ValueError('invalid distribution')
        key=(event['symbol'],event['ex_date'])
        if key not in grouped:
            grouped[key]={'symbol':key[0],'ex_date':key[1],'payable_date':event.get('payable_date'),
                          'amount':0.,'basis':'split'}
        elif grouped[key]['payable_date']!=event.get('payable_date'):
            raise ValueError('distribution components have different payment dates')
        grouped[key]['amount']+=rate
    return sorted(grouped.values(),key=lambda e:(e['ex_date'],e['symbol']))


def run_screen(reference, bars, events, start, end, *, risk, diversify, cost_scale=1.):
    """Funded shadow-reference policy screen, not broker-engine reproduction.

    At each next session open, follow the prior close's observable reference
    weights, optionally transformed. Covariance uses 40 completed total-return
    observations. Orders sized at prior close fill at next open, sells before
    buys, with integer-independent fractional ETF quantities and $25 minimum.
    Cash is never borrowed; unknown payment dates remain nonspendable receivables.
    """
    if not math.isfinite(cost_scale) or cost_scale<0:raise ValueError('invalid costs')
    symbols=['TQQQ','GLD','GDX','XLE','BIL','KMLM','SPY']
    sessions=[x.strftime('%Y-%m-%d') for x in _calendar().sessions_in_range(start,end)]
    if not sessions:raise ValueError('empty session range')
    initial=previous_session(sessions[0])
    ref={r['session']:dict(r['weights']) for r in reference}
    ref_dates=sorted(ref)
    if not ref_dates or ref_dates[0]>initial:raise ValueError('reference unavailable before first trade')
    relevant=['TQQQ','GLD','GDX','XLE','BIL']+(['KMLM'] if diversify else [])
    history=[x.strftime('%Y-%m-%d') for x in _calendar().sessions_in_range(
        (date.fromisoformat(start)-timedelta(days=100)).isoformat(),end)]
    indices={d:i for i,d in enumerate(history)}
    first=indices[sessions[0]]
    if first<41:raise ValueError('insufficient covariance history')
    all_events={(e['symbol'],e['ex_date']):e for e in events}
    if len(all_events)!=len(events):raise ValueError('duplicate ex-date event')
    for e in events:
        if e.get('basis')!='split':raise ValueError('event basis must be split')
    for d in history[first-41:]:
        for s in relevant:
            if d not in bars.get(s,{}):raise ValueError(f'missing bar {s} {d}')
            if any(not math.isfinite(bars[s][d][k]) or bars[s][d][k]<=0 for k in ('Open','Close')):
                raise ValueError('invalid price')
    cash=6000.;positions={s:0. for s in symbols};receivables=[]
    paid=fees=traded=0.;count=0
    result=[{'session':initial,'value':6000.,'cash':6000.,'receivable':0.,'dividends_paid':0.,
             'fees_paid':0.,'traded_notional':0.,'fills':0,'positions':dict(positions)}]
    for day in sessions:
        i=indices[day];prior=history[i-1]
        # Entitlement belongs to the holder before ex-date trading.
        for s in relevant:
            e=all_events.get((s,day))
            if e and positions[s]>0:
                receivables.append((e.get('payable_date'),positions[s]*e['amount']))
        pending=[]
        for payable,amount in receivables:
            if payable and payable<=day:cash+=amount;paid+=amount
            else:pending.append((payable,amount))
        receivables=pending
        ref_index=bisect_right(ref_dates,prior)-1
        if ref_index<0:raise ValueError('reference lookahead')
        weights=ref[ref_dates[ref_index]]
        cov=np.zeros((len(symbols),len(symbols)))
        if risk:
            returns=np.zeros((40,len(symbols)))
            for j in range(i-40,i):
                for k,s in enumerate(symbols):
                    if s not in relevant:continue
                    now,prev=history[j],history[j-1]
                    income=all_events.get((s,now),{}).get('amount',0.)
                    returns[j-(i-40),k]=(bars[s][now]['Close']+income)/bars[s][prev]['Close']-1
            cov=np.cov(returns,rowvar=False)*252
        targets=protect(weights,symbols,cov,risk=risk,diversify=diversify)
        if any(s not in relevant for s,w in targets.items() if w):raise ValueError('unsupported target')
        prior_nav=cash+sum(positions[s]*bars[s][prior]['Close'] for s in relevant)
        desired={s:targets.get(s,0.)*prior_nav/bars[s][prior]['Close'] for s in relevant}
        costs={s:cost_scale*(.00044 if s in ('TQQQ','GLD','BIL','SPY') else .00232) for s in relevant}
        # Fixed quantities selected using prior information; current open is execution only.
        for s in relevant:
            qty=max(0.,positions[s]-desired[s]);price=bars[s][day]['Open'];notional=qty*price
            if notional<25:continue
            fee=notional*costs[s];cash+=notional-fee;positions[s]-=qty
            fees+=fee;traded+=notional;count+=1
        buys={s:max(0.,desired[s]-positions[s]) for s in relevant}
        for s in relevant:
            if buys[s]*bars[s][day]['Open']<25:buys[s]=0.
        need=sum(q*bars[s][day]['Open']*(1+costs[s]) for s,q in buys.items())
        scale=min(1.,max(0.,cash)/need) if need else 0.
        for s,qty in buys.items():
            qty*=scale;notional=qty*bars[s][day]['Open']
            if notional<25:continue
            fee=notional*costs[s];cash-=notional+fee;positions[s]+=qty
            fees+=fee;traded+=notional;count+=1
        if cash < -1e-7:raise ValueError('negative funded cash')
        income=sum(amount for _,amount in receivables)
        nav=cash+income+sum(positions[s]*bars[s][day]['Close'] for s in relevant)
        result.append({'session':day,'value':nav,'cash':cash,'receivable':income,'dividends_paid':paid,
                       'fees_paid':fees,'traded_notional':traded,'fills':count,'positions':dict(positions)})
    return result


def debit_vertical_risk(legs, *, asof, quantity, fees, existing_risk, budget):
    """Validate a standard two-leg debit vertical, pricing at executable sides.

    Underlying/expiry/type/strike/multiplier must come from verified contract
    metadata. Expiration handling and early assignment remain separate controls.
    """
    if len(legs)!=2:raise ValueError('two legs required')
    long,short=legs
    if long.get('side')!='buy' or short.get('side')!='sell':raise ValueError('invalid leg sides')
    for key in ('underlying','expiry','type','multiplier'):
        if not long.get(key) or long[key]!=short.get(key):raise ValueError('contract identity mismatch')
    if date.fromisoformat(long['expiry'])<=date.fromisoformat(asof):raise ValueError('expiry must be after today')
    if long['type'] not in ('call','put'):raise ValueError('invalid option type')
    strikes=[long['strike'],short['strike']]
    if not all(math.isfinite(x) and x>0 for x in strikes):raise ValueError('invalid strike')
    width=(short['strike']-long['strike']) if long['type']=='call' else (long['strike']-short['strike'])
    return spread_risk(width=width,debit=long['ask']-short['bid'],quantity=quantity,
                       multiplier=long['multiplier'],fees=fees,existing_risk=existing_risk,
                       budget=budget,quotes=legs)


def benchmark_total_return(prices, events, sessions):
    """SPY total-return index, reinvested at ex-date close; no constant yield."""
    dividends={e['ex_date']:e['amount'] for e in events if e['symbol']=='SPY'}
    nav=6000.;rows=[]
    for i,day in enumerate(sessions):
        if i:
            nav*=(prices[day]['Close']+dividends.get(day,0.))/prices[sessions[i-1]]['Close']
        rows.append({'session':day,'value':nav})
    return rows


def rolling_metrics(rows, benchmark, *, horizons=(63,126,252)):
    """Report absolute bear profit separately from relative SPY outperformance."""
    if [r['session'] for r in rows]!=[r['session'] for r in benchmark]:raise ValueError('unaligned benchmark')
    nav=np.array([r['value'] for r in rows]);spy=np.array([r['value'] for r in benchmark]);out={}
    for horizon in horizons:
        if horizon<1:raise ValueError('positive horizon required')
        r=nav[horizon:]/nav[:-horizon]-1.;b=spy[horizon:]/spy[:-horizon]-1.
        bear=b<-.05;bull=b>.05;side=~(bear|bull)
        out[str(horizon)]={'n':len(r),'beat_pct':float(np.mean(r>b)*100) if len(r) else None,
          'bear_n':int(bear.sum()),'bear_positive_pct':float(np.mean(r[bear]>0)*100) if bear.any() else None,
          'bull_n':int(bull.sum()),'bull_beat_pct':float(np.mean(r[bull]>b[bull])*100) if bull.any() else None,
          'sideways_n':int(side.sum()),'sideways_positive_pct':float(np.mean(r[side]>0)*100) if side.any() else None,
          'worst_pct':float(np.min(r)*100) if len(r) else None}
    return out


def calendar_metrics(rows, benchmark, *, months=(3,6,12)):
    """Calendar horizons anchored at the latest close at/before month offset."""
    import pandas as pd
    sessions=[r['session'] for r in rows]
    if sessions!=[r['session'] for r in benchmark]:raise ValueError('unaligned benchmark')
    nav=np.array([r['value'] for r in rows]);spy=np.array([r['value'] for r in benchmark]);out={}
    for n in months:
        if n<1:raise ValueError('positive month horizon required')
        returns=[];bench=[]
        for i,day in enumerate(sessions):
            anchor=(pd.Timestamp(day)-pd.DateOffset(months=n)).strftime('%Y-%m-%d')
            j=bisect_right(sessions,anchor)-1
            if j>=0 and j<i:
                returns.append(nav[i]/nav[j]-1);bench.append(spy[i]/spy[j]-1)
        r=np.array(returns);b=np.array(bench);bear=b<-.05;bull=b>.05
        out[str(n)]={'n':len(r),'beat_pct':float(np.mean(r>b)*100) if len(r) else None,
          'bear_n':int(bear.sum()),'bear_positive_pct':float(np.mean(r[bear]>0)*100) if bear.any() else None,
          'bull_n':int(bull.sum()),'bull_beat_pct':float(np.mean(r[bull]>b[bull])*100) if bull.any() else None,
          'worst_pct':float(np.min(r)*100) if len(r) else None}
    return out
