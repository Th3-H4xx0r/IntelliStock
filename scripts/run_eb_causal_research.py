#!/usr/bin/env python3
"""Offline reproduction using the archived September 6 evidence bundle."""
import gzip
import hashlib
import json
from pathlib import Path
import numpy as np
import eb_causal_research as m

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'output/research/eb-causal-2026-09-06'
OLD=ROOT/'output/research/strategy-eb-2026-09-06'
WINDOWS=[('2022H1','2022-01-01','2022-06-30'),('2026bear','2026-02-01','2026-04-01'),
         ('2025bear','2025-02-15','2025-04-15'),('2023bull','2023-01-01','2023-12-31'),
         ('2024bull','2024-01-01','2024-12-31'),('2022year','2022-01-01','2022-12-31')]
raw_native=json.load(gzip.open(DATA/'alpaca-iex-split-bars.json.gz','rt'))
marks={s:{r['t'][:10]:{'Close':r['c']} for r in a} for s,a in raw_native.items()}
sip=json.load(gzip.open(DATA/'alpaca-sip-split-bars.json.gz','rt'))
bars={s:{r['t'][:10]:{'Open':r['o'],'Close':r['c']} for r in a} for s,a in sip.items()}
actions=json.loads((DATA/'alpaca-corporate-actions.json').read_text())
# QQQ is a signal reference, never held in these accounts. Its source has two
# contradictory records for 2022-09-19; exclude the unused symbol explicitly,
# rather than quietly resolving ambiguous cash entitlements.
actions['cash_dividends']=[e for e in actions['cash_dividends'] if e['symbol']!='QQQ']
events=m.normalize_distributions(actions,basis_asof='2026-08-28')
(DATA/'normalized-distributions.json').write_text(json.dumps(events,indent=2)+'\n')
summary={'method':'fixed-holdings attribution + separately funded shadow-reference screen',
         'data_quality_notes':['Unused QQQ corporate actions excluded: duplicate 2022-09-19 rate with conflicting payment dates.'],
         'accounting':{},'screens':{}}
raws={}
for run in ['785201','443180','630425','877293']:
    raw=json.load(gzip.open(OLD/f'run-{run}-pv.json.gz','rt'));raws[run]=raw
    error=m.reconcile(raw); aligned=m.align(raw,marks)
    changed_same_mark=sum(aligned[i]['session']==aligned[i-1]['session'] and aligned[i]['positions_snapshot']!=aligned[i-1]['positions_snapshot'] for i in range(1,len(aligned)))
    if changed_same_mark:raise ValueError('cannot collapse holdings changes within one mark session')
    aligned=list({r['session']:r for r in aligned}.values())
    corrected=m.account_distributions(aligned,events,price_basis='split')
    with gzip.open(DATA/f'{run}-corrected-attribution.json.gz','wt') as f:json.dump(corrected,f)
    result={'max_nav_reconciliation_error':error,'raw_rows':len(raw),'unique_mark_sessions':len(aligned),
            'start':aligned[0]['session'],'end':aligned[-1]['session'],
            'income_unreinvested':corrected[-1]['distribution_receivable'],
            'corrected_total_pct':(corrected[-1]['corrected_value']/6000-1)*100,
            'windows':{tag:{'price_only_pct':m.interval_return(aligned,a,b)*100,
                            'income_corrected_pct':m.interval_return(corrected,a,b,field='corrected_value')*100}
                       for tag,a,b in WINDOWS}}
    summary['accounting'][run]=result
    if run=='785201':
        reference=[{'session':r['session'],'weights':{s:q*r['prices'][s]/r['value'] for s,q in r['positions_snapshot'].items()}} for r in aligned]
        begin='2021-11-01';finish=aligned[-1]['session']
assert raws['785201']==raws['443180']
summary['aa_exact']=True
(DATA/'reference-weights.json').write_text(json.dumps(reference,indent=2)+'\n')
# Write completed accounting before any screen, including if screen data fails.
(DATA/'results.json').write_text(json.dumps(summary,indent=2)+'\n')
for label,risk,diversify in [('control',False,False),('R',True,False),('D',False,True),('RD',True,True)]:
    for scale in (1.,2.):
        key=f'{label}-cost{scale:g}'
        try:
            rows=m.run_screen(reference,bars,events,begin,finish,risk=risk,diversify=diversify,cost_scale=scale)
        except ValueError as exc:
            summary['screens'][key]={'status':'blocked','reason':str(exc)}
            print(key,'BLOCKED',str(exc),flush=True)
            continue
        with gzip.open(DATA/f'screen-{key}.json.gz','wt') as f:json.dump(rows,f)
        nav=np.array([r['value'] for r in rows]);dd=float(np.min(nav/np.maximum.accumulate(nav)-1))
        result={'status':'screen_only','total_pct':float((nav[-1]/6000-1)*100),'maxdd_pct':dd*100,
                'execution_costs':rows[-1]['fees_paid'],'fills':rows[-1]['fills'],
                'traded_notional':rows[-1]['traded_notional'],'windows':{tag:m.interval_return(rows,a,b)*100 for tag,a,b in WINDOWS}}
        summary['screens'][key]=result
        print(key,round(result['total_pct'],2),round(result['maxdd_pct'],2),{k:round(v,2) for k,v in result['windows'].items()},flush=True)
(DATA/'results.json').write_text(json.dumps(summary,indent=2)+'\n')
# Benchmark and rolling-regime comparison on the same genuine sessions.
first_rows=json.load(gzip.open(DATA/'screen-control-cost1.json.gz','rt'))
benchmark=m.benchmark_total_return(bars['SPY'],events,[r['session'] for r in first_rows])
summary['benchmark']={'total_pct':(benchmark[-1]['value']/6000-1)*100,
                      'windows':{tag:m.interval_return(benchmark,a,b)*100 for tag,a,b in WINDOWS}}
for key,result in summary['screens'].items():
    if result['status']!='screen_only':continue
    rows=json.load(gzip.open(DATA/f'screen-{key}.json.gz','rt'))
    result['rolling']=m.rolling_metrics(rows,benchmark)
    result['calendar']=m.calendar_metrics(rows,benchmark)
# Reuse the frozen 25-window definitions without importing the API runner.
import ast
parsed=ast.parse((ROOT/'scripts/outlier_engine_test.py').read_text())
battery=next(ast.literal_eval(node.value) for node in parsed.body if isinstance(node,ast.Assign)
             and any(isinstance(t,ast.Name) and t.id=='REGIME_WINDOWS' for t in node.targets))
for key,result in summary['screens'].items():
    if result['status']!='screen_only':continue
    rows=json.load(gzip.open(DATA/f'screen-{key}.json.gz','rt'))
    details=[]
    for regime,tag,a,b in battery:
        if a<=rows[0]['session'] or b>rows[-1]['session']:
            details.append({'regime':regime,'tag':tag,'status':'outside_complete_path','requested_start':a,'requested_end':b})
            continue
        ret=m.interval_return(rows,a,b)*100;bench=m.interval_return(benchmark,a,b)*100
        details.append({'regime':regime,'tag':tag,'status':'complete','return_pct':ret,'spy_pct':bench,'excess_pp':ret-bench})
    result['original_25_continuous']=details
    result['original_25_complete_n']=sum(r['status']=='complete' for r in details)
    result['original_25_spy_wins']=sum(r.get('excess_pp',-1)>0 for r in details)
with gzip.open(DATA/'spy-total-return.json.gz','wt') as f:json.dump(benchmark,f)
(DATA/'results.json').write_text(json.dumps(summary,indent=2)+'\n')
manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DATA.glob('*')) if p.is_file() and p.name!='manifest.json'}
(DATA/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Saved',DATA/'results.json')
