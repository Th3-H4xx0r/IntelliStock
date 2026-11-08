"""Mandatory reproduction check (FROZEN.md step 4). Runs the SAME code path score_forward.py uses, on the
frozen data snapshot (./snapshot, fetched 2026-11-07, cut at 2026-11-06), and compares with the hunt's logged
numbers. Tolerance: 0.3 pp/yr of excess and 1.0 pt of max drawdown.

    python3 repro_check.py            (output committed as repro_check.log)

Checks
  1. F14_F_live (DBMF)   2019-05-10..2026-11-06  vs REPORT_F14 "Live-implementable version" (f14/dbmf_check.log)
  2. F4r2_stress_xs      VAL 2013-01-01..2026-11-06  vs f4/tables.md (23 bps primary there; 5 bps column)
  3. F13_D engine        VAL 2013-01-01..2026-11-06 with the archived synthetic sleeves (repro_data/) vs
                         f13/tables.md -- proves the frozen F13 engine copy; F13_D_live itself has no logged row
  4. F13_D_live          2019-05-10..2026-11-06 (new; no reference) and every book on the common 2019-05 window
  5. Truncation consistency: running on data cut at date t reproduces, through t, the NAV and every decision of
     the run on all data (what the daily forward re-run relies on; also exercises adaptation A1)
  6. NYSE holiday calendar vs SPY's actual sessions 2010-2026
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import f13_engine  # noqa: E402
import strategies as S  # noqa: E402
from fetch_data import build_frame  # noqa: E402
from harness import decision_days, extend_calendar, judge, nyse_sessions, rebase  # noqa: E402

END = "2026-11-06"
TOL_PP, TOL_DD = 0.3, 1.0
ok_all = True


def fmt(j, extra=""):
    w3y = "-" if j["win_3y"] is None else f"{j['win_3y']*100:.0f}"
    w5y = "-" if j["win_5y"] is None else f"{j['win_5y']*100:.0f}"
    return (f"{j['start']}..{j['end']} | {j['cagr']*100:.2f} / {j['bench_cagr']*100:.2f} | {j['excess_pp']:+.2f} | "
            f"{j['mdd']*100:.1f} / {j['bench_mdd']*100:.1f} | {j['win_3m']*100:.0f}/{j['win_6m']*100:.0f}/"
            f"{j['win_12m']*100:.0f} | {w3y}/{w5y} | {j['worst_12m_rel']*100:.1f} ({j['worst_12m_rel_end']}) | "
            f"{j['longest_below_rel_high_yrs']:.1f}{extra}")


def check(label, got_ex, ref_ex, got_dd, ref_dd):
    global ok_all
    dx, dd = got_ex - ref_ex, (got_dd - ref_dd)
    ok = abs(dx) <= TOL_PP and abs(dd) <= TOL_DD
    ok_all &= ok
    print(f"| {label} | {got_ex:+.2f} vs {ref_ex:+.2f} (diff {dx:+.2f}) | {got_dd:.1f} vs {ref_dd:.1f} (diff {dd:+.1f}) | "
          f"{'PASS' if ok else 'FAIL'} |")


P = build_frame(os.path.join(HERE, "snapshot"), end=END)
print(f"data: snapshot {P.index[0].date()}..{P.index[-1].date()} ({len(P)} sessions)\n")
HDR = ("| book | span | CAGR / SPY % | excess pp | maxDD / SPY % | win 3m/6m/12m % | win 3y/5y % | "
       "worst 12m rel % (end) | yrs below rel high | turnover %/yr |")
SEP = "|---|---|---|---|---|---|---|---|---|---|"

# ---------------------------------------------------------------- 1. F14_F_live
S0 = "2019-05-10"
b19 = rebase(P["market"], S0)
f14 = {c: S.run_f14_live(P, S0, END, c) for c in (5.0, 23.0)}
j5, j23 = judge(f14[5.0]["nav"], b19), judge(f14[23.0]["nav"], b19)
print("## 1. F14_F_live (DBMF) vs REPORT_F14 / f14/dbmf_check.log\n")
print(HDR); print(SEP)
print(f"| F14_F_live @5 | {fmt(j5)} | {f14[5.0]['turnover']*100:.0f} |")
print(f"| F14_F_live @23 | {fmt(j23)} | {f14[23.0]['turnover']*100:.0f} |")
print("| REFERENCE @5 (23) | 2019-05-10..2026-11-06 | 21.63 / 15.06 | +6.57 (+4.95) | -30.0 / -33.7 | 63/69/72 | 84/- "
      "| -12.0 (2023-11-03) | 3.6 | 384 |")
print(f"\nmean DBMF weight at decisions {f14[5.0]['fund_w_mean']:.2f} (ref 0.42); max gross {f14[5.0]['max_gross']:.2f} (cap 2.00)\n")
print("| check | excess pp/yr got vs ref | maxDD got vs ref | result |\n|---|---|---|---|")
check("F14_F_live 5 bps", j5["excess_pp"], 6.57, j5["mdd"] * 100, -30.0)
check("F14_F_live 23 bps", j23["excess_pp"], 4.95, j23["mdd"] * 100, -30.0)   # ref DD is the 5-bps row's

# ---------------------------------------------------------------- 2. F4r2_stress_xs VAL
V0 = "2013-01-01"
b13 = rebase(P["market"], V0)
f4 = {c: S.run_f4(P, V0, END, c) for c in (5.0, 23.0)}
k5, k23 = judge(f4[5.0]["nav"], b13), judge(f4[23.0]["nav"], b13)
print("\n## 2. F4r2_stress_xs VAL vs f4/tables.md (hunt round 2b; its table is at 23 bps)\n")
print(HDR); print(SEP)
print(f"| F4r2_stress_xs @23 | {fmt(k23)} | {f4[23.0]['turnover']*100:.0f} |")
print(f"| F4r2_stress_xs @5 | {fmt(k5)} | {f4[5.0]['turnover']*100:.0f} |")
print("| REFERENCE @23 | 2013-01-02..2026-11-06 | 17.72 / 14.50 | +3.23 (@5 +3.65) | -29.9 / -33.7 | 67/69/69 | 96/100 "
      "| -10.3 (2016-04-26) | 2.6 | 106 |\n")
print("| check | excess pp/yr got vs ref | maxDD got vs ref | result |\n|---|---|---|---|")
check("F4r2_stress_xs 23 bps", k23["excess_pp"], 3.23, k23["mdd"] * 100, -29.9)
check("F4r2_stress_xs 5 bps", k5["excess_pp"], 3.65, k5["mdd"] * 100, -29.9)

# ---------------------------------------------------------------- 3. F13_D engine (synthetic sleeves)
sl = pd.read_csv(os.path.join(HERE, "repro_data", "f13_sleeves_2009.csv.gz"), index_col=0, parse_dates=True)
F = P[["market", "tbill"]].copy()
for c in sl.columns:                                    # f13/frames._on: ffill onto the calendar, NaN before
    x = sl[c].dropna()
    y = x.reindex(x.index.union(F.index)).ffill().reindex(F.index)
    y[y.index < x.index[0]] = np.nan
    F[c] = y
ext = extend_calendar(F.index)
is_m, is_w = decision_days(ext, "M")[:len(F)], decision_days(ext, "W-WED")[:len(F)]
syn = {}
ov = None
for c in (5.0, 23.0):
    r = f13_engine.run(F, list(sl.columns), V0, END, dict(S.F13D_PARAMS), cost_bps=c, core="f4", ov=ov,
                       is_m=is_m, is_w=is_w)                           # FEE stays 0.0085 (synthetic overlay)
    ov = r["ov"]
    syn[c] = r
m5, m23 = judge(syn[5.0]["nav"], b13), judge(syn[23.0]["nav"], b13)
print("\n## 3. F13_D_f4core_exeq_s04 VAL with the archived synthetic sleeves vs f13/tables.md\n")
print(HDR.replace("turnover %/yr", "turnover ov / core %/yr")); print(SEP)
print(f"| F13_D synthetic @5 | {fmt(m5)} | {syn[5.0]['turnover_ov']*100:.0f} / {syn[5.0]['turnover_core']*100:.0f} |")
print(f"| F13_D synthetic @23 | {fmt(m23)} | {syn[23.0]['turnover_ov']*100:.0f} / {syn[23.0]['turnover_core']*100:.0f} |")
print("| REFERENCE @5 (23) | 2013-01-02..2026-11-06 | 18.54 / 14.50 | +4.04 (+1.23) | -25.3 / -33.7 (@23 -25.5) | "
      "59/67/75 | 85/98 | -11.9 (2019-01-31) | 2.9 | 598 / 87 |\n")
print("| check | excess pp/yr got vs ref | maxDD got vs ref | result |\n|---|---|---|---|")
check("F13_D engine 5 bps", m5["excess_pp"], 4.04, m5["mdd"] * 100, -25.3)
check("F13_D engine 23 bps", m23["excess_pp"], 1.23, m23["mdd"] * 100, -25.5)

# ---------------------------------------------------------------- 4. all books, common window
f13l = {c: S.run_f13d_live(P, S0, END, c) for c in (5.0, 23.0)}
print("\n## 4. Every frozen book on the common DBMF window 2019-05-10..2026-11-06 (F13_D_live: new, no reference)\n")
print(HDR); print(SEP)
for name, runs in (("F14_F_live", f14), ("F13_D_live", f13l), ("F4r2_stress_xs (slice of the 2013 run)", f4)):
    for c in (5.0, 23.0):
        nav = runs[c]["nav"]
        nav = rebase(nav, S0)
        print(f"| {name} @{c:.0f} | {fmt(judge(nav, b19))} | {runs[c]['turnover']*100:.0f} |")
print(f"\nF13_D_live DBMF weight E = {f13l[5.0]['E']:.4f} (F13 engine: 1.25 x 0.04 / 0.12)")

# ---------------------------------------------------------------- 5. truncation consistency
print("\n## 5. Truncation consistency (run on data cut at t == run on all data, through t)\n")
cuts = ["2024-03-26", "2025-06-27", "2025-10-29", "2026-08-05"]       # a Tue, Fri, Wed (TOM day -3), Wed
trunc_ok = True
for cut in cuts:
    Pc = P.loc[:cut]
    for name, fn in S.RUNNERS.items():
        a = fn(P, S.INCEPTION[name], END, 5.0)
        b = fn(Pc, S.INCEPTION[name], cut, 5.0)
        na, nb = a["nav"].loc[:cut], b["nav"]
        same_nav = len(na) == len(nb) and np.allclose(na.values, nb.values, rtol=0, atol=1e-12)
        if name == "F13_D_live":
            da = [(k, v) for k, v in a["res"]["ov"].items() if k <= len(Pc) - 1]
            db = list(b["res"]["ov"].items())
            same_dec = da == db
        else:
            A = a["res"].decisions
            B = b["res"].decisions
            A = A[A["date"] <= pd.Timestamp(cut)].drop(columns="exec_date").reset_index(drop=True)
            B = B.drop(columns="exec_date").reset_index(drop=True)
            same_dec = A.shape == B.shape and A["date"].equals(B["date"]) and np.allclose(
                A.drop(columns="date").values, B.drop(columns="date").values, rtol=0, atol=1e-12)
        trunc_ok &= same_nav and same_dec
        print(f"- cut {cut} {name}: NAV {'identical' if same_nav else 'DIFFERS'}, decisions "
              f"{'identical' if same_dec else 'DIFFER'}; next-session action on the cut data: {b['next']['action']}")
ok_all &= trunc_ok

# ---------------------------------------------------------------- 6. NYSE calendar
spy = P.index[P.index >= "2010-01-01"]
gen = nyse_sessions("2010-01-01", END)
extra, missing = gen.difference(spy), spy.difference(gen)
print("\n## 6. NYSE holiday calendar vs SPY sessions 2010-01-01..2026-11-06\n")
print(f"generated {len(gen)}, actual {len(spy)}; generated-but-closed: {[str(d.date()) for d in extra]}; "
      f"open-but-not-generated: {[str(d.date()) for d in missing]}")
print("(expected generated-but-closed: unscheduled closures only -- 2012-10-29/30 Sandy, 2018-12-05 Bush, "
      "2025-01-09 Carter)")
cal_ok = len(missing) == 0 and set(str(d.date()) for d in extra) <= {"2012-10-29", "2012-10-30", "2018-12-05", "2025-01-09"}
ok_all &= cal_ok

print(f"\nOVERALL: {'PASS' if ok_all else 'FAIL'}")
sys.exit(0 if ok_all else 1)
