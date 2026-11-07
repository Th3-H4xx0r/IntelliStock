"""The three frozen strategies, in live-implementable form (FROZEN.md has the protocol and adaptations).

Every strategy is run as ONE continuous simulation from its fixed INCEPTION date to the latest close.
A forward window is that simulation's NAV rebased at the forward base close, so the book entering the
forward test is exactly the book the rule was holding (history before the start is warm-up, never scored).

  F14_F_live      hunt F14_F_tsfix_sign12_Ttc23 with the TSMOM stream = the real DBMF ETF (REPORT_F14
                  "Live-implementable version"): streams L B T D M V on SPY / IEF / MTUM / IWD, gated by
                  their own trailing-12m shadow excess (sign12), T = cost-aware turn-of-month (23 bps),
                  S = 0.417 x DBMF always on; trades only on TOM day -3 / day +2; gross <= 2.0.
  F13_D_live      hunt F13_D_f4core_exeq_s04 with the synthetic ex-equity TSMOM overlay replaced by
                  0.4167 x DBMF on margin (= F13's own ETF-equivalent E = 1.25 x 0.04 / 0.12), decided at
                  month-end; core = F4r2_stress_xs equity exposure 0.7-1.5x SPY decided weekly.
  F4r2_stress_xs  hunt F4 round-2b rule as is (SPY 0.7-1.5x + 0.4 IEF bond trend), weekly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import f13_engine
import f14_engine
import f4_rule
from harness import backtest, extend_calendar, flags_on_extended, decision_days

F14_PARAMS = dict(meta=1, lb=12, vs=0.30, xs=0.417, sa=1, tc=23)          # f14/specs.py F14_F
F4P = {"k": 0.5, "f": 0.3, "vs": 0.30, "sma": 200, "t": 8}                  # f13/specs.py F4P
F13D_PARAMS = {"sigma_o": 0.04, "lb": 12, "cw": 36, "blend": 0, **F4P}     # f13/specs.py F13_D
INCEPTION = {"F14_F_live": "2019-05-10", "F13_D_live": "2019-05-10", "F4r2_stress_xs": "2013-01-01"}
TICKER = {"market": "SPY", "bond10": "IEF", "MTUM": "MTUM", "IWD": "IWD", "fund": "DBMF", "DBMF_XS": "DBMF",
          "core": "SPY", "tbill": "cash (negative = margin loan)", "cash": "cash (negative = margin loan)"}


def _target_info(res, last):
    """Next-session action for a lib-style backtest: a decision made at the last data close is filled at
    the next close (TRADE); otherwise the drifted book is held (HOLD)."""
    dec = res.decisions
    cur = res.meta["final_weights"]
    if len(dec) and pd.Timestamp(dec["date"].iloc[-1]) == last:
        row = dec.iloc[-1]
        tgt = {k: float(row[k]) for k in dec.columns if k not in ("date", "exec_date") and abs(float(row[k])) > 1e-12}
        tgt["tbill"] = 1.0 - sum(tgt.values())
        return {"action": "TRADE", "target": tgt, "current": cur}
    return {"action": "HOLD", "target": None, "current": cur}


def run_f14_live(P, start, end, cost_bps):
    F = P.loc[:pd.Timestamp(end), ["market", "bond10", "MTUM", "IWD", "fund", "tbill"]]
    uni = ["market", "bond10", "MTUM", "IWD", "fund"]
    f14_engine.set_calendar(extend_calendar(F.index))                       # A1
    try:
        eng = f14_engine.Engine(dict(F14_PARAMS))
        res = backtest(eng, uni, start, end, prices=F, rebalance="D", cost_bps=cost_bps, set_name="HIST")
    finally:
        f14_engine.set_calendar(None)
    info = _target_info(res, F.index[-1])
    fw = res.decisions["fund"].astype(float) if len(res.decisions) else pd.Series(dtype=float)
    return {"nav": res.nav, "res": res, "engine": eng, "next": info, "turnover": res.turnover,
            "fund_w_mean": float(fw.mean()) if len(fw) else 0.0, "max_gross": res.max_gross}


def run_f4(P, start, end, cost_bps):
    F = P.loc[:pd.Timestamp(end), ["market", "bond10", "tbill"]]
    is_dec = flags_on_extended(F.index, f4_rule.REBALANCE)                  # A1
    res = backtest(f4_rule.make(), f4_rule.UNIVERSE, start, end, prices=F, rebalance=f4_rule.REBALANCE,
                   cost_bps=cost_bps, set_name="VAL", is_dec=is_dec)
    info = _target_info(res, F.index[-1])
    return {"nav": res.nav, "res": res, "next": info, "turnover": res.turnover, "max_gross": res.max_gross}


def dbmf_excess_level(P):
    """Futures-like excess-return level of DBMF over cash: prod(1 + r_DBMF - r_cash), NaN before DBMF."""
    r = P["fund"].pct_change() - P["tbill"].pct_change()
    lvl = (1.0 + r.fillna(0.0)).cumprod()
    lvl[P["fund"].isna()] = np.nan
    return lvl


def run_f13d_live(P, start, end, cost_bps):
    F = P.loc[:pd.Timestamp(end), ["market", "tbill"]].copy()
    F["DBMF_XS"] = dbmf_excess_level(P.loc[:pd.Timestamp(end)])
    ext = extend_calendar(F.index)
    is_m = decision_days(ext, "M")[:len(F)]                                  # A1
    is_w = decision_days(ext, "W-WED")[:len(F)]
    E = f13_engine.VOL_RATIO * F13D_PARAMS["sigma_o"] / f13_engine.SIGMA_ETF     # 0.41667 x DBMF
    x = F["DBMF_XS"].values
    ov = {int(d): ({"DBMF_XS": E} if np.isfinite(x[d]) else {}) for d in np.where(is_m)[0]}
    old_fee = f13_engine.FEE
    f13_engine.FEE = 0.0                                                     # A4: DBMF NAV is net of its fee
    try:
        r = f13_engine.run(F, ["DBMF_XS"], start, end, dict(F13D_PARAMS), cost_bps=cost_bps, core="f4",
                           ov=ov, is_m=is_m, is_w=is_w)
    finally:
        f13_engine.FEE = old_fee
    nl = len(F) - 1
    cur = r["final_weights"]
    ld = r["last_row_decision"]
    if ld["core"] is not None or ld["overlay"] is not None:
        tgt = {"core": ld["core"] if ld["core"] is not None else cur["core"]}
        if ld["overlay"] is not None:
            tgt.update(ld["overlay"])
        else:
            tgt.update({k: v for k, v in cur.items() if k not in ("core", "cash")})
        info = {"action": "TRADE", "target": tgt, "current": cur,
                "which": [k for k in ("core", "overlay") if ld[k] is not None]}
    else:
        info = {"action": "HOLD", "target": None, "current": cur}
    return {"nav": r["nav"], "res": r, "next": info, "turnover": r["turnover_core"] + r["turnover_ov"],
            "E": E}


RUNNERS = {"F14_F_live": run_f14_live, "F13_D_live": run_f13d_live, "F4r2_stress_xs": run_f4}
PRIMARY = "F14_F_live"


def run_all(P, end=None, costs=(5.0, 23.0), only=None):
    end = str(P.index[-1].date()) if end is None else end
    out = {}
    for name, fn in RUNNERS.items():
        if only and name not in only:
            continue
        out[name] = {c: fn(P, INCEPTION[name], end, c) for c in costs}
    return out
