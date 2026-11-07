"""FORWARD-TEST COPY of hunt/f13/engine.py (sha1 41f9296f51), rule and accounting code UNCHANGED.
Edits (FROZEN.md A3/A1/A2):
  * `import lib` -> the frozen harness; `from frames import ...` -> MONTHLY_SLEEVES = set() (no monthly-step
    sleeve exists here);
  * run(): optional `is_m` / `is_w` masks computed on the NYSE-extended calendar (A1), and the end-of-run
    book plus any decision made at the last data row are returned (A2, reporting only);
  * FEE is a module constant the F13_D_live wrapper sets to 0.0 (the real DBMF NAV is already net of its
    0.85% fee); the synthetic-overlay reproduction keeps 0.0085.

--- original docstring ---
F13 book: a 1x index core (or F4's trend-leverage core) + a multi-asset time-series-momentum
(managed-futures) overlay held as futures-like notional positions on excess-return sleeves.

Accounting (per row i of the set frame P; same conventions as lib.backtest):
  core   M_i = M_{i-1} (1 + R_mkt,i)
  sleeve notional N_k,i = N_k,i-1 (1 + e_k,i), P&L N_k,i-1 e_k,i paid into cash (futures: excess
         return, collateral is the cash itself)
  cash   C_i = C_{i-1} (1 + r_cash,i [+ 0.5%/yr x gap/365 if C < 0]) + sum_k P&L - drag
  drag   implementation cost of holding the overlay as a managed-futures ETF on margin:
         E x (fee 0.85% + margin spread 0.5%) per year, E = 1.25 sigma_o / 12% (ETF weight that carries
         the overlay's REALISED vol (1.25 x nominal on DEV) at DBMF's realised vol of 12%),
         accrued per calendar day on NAV.
  NAV_i = M_i + C_i.
Decisions at the close of row d; trades fill at the close of row d+1 (lag 1) at cost_bps per unit
of |traded notional| (core and every sleeve), with lib's 4-step cost iteration.

Overlay rule (decided on the last session of each month, = month-end; literature form of
Moskowitz-Ooi-Pedersen 2012 / Hurst-Ooi-Pedersen 2017):
  signal  s_k = mean(sign(r_k over 1m), sign(3m), sign(L m))   [HOP blend]   or sign(L m) [MOP]
  vol     sigma_k = EWMA (centre of mass 60 rows) of squared row returns, annualised by the rows in
          the trailing 365 days (monthly-step sleeves: 3 years of row returns)
  risk    v_k = s_k (each sleeve one unit of risk x its signal); correlation C from the trailing W
          month-end returns of the sleeves (pairs with < 24 common months -> 0), shrunk 50% to I
  scale   k = sigma_o / sqrt(v' C* v); position w_k = k s_k / sigma_k (fraction of NAV notional);
          per-sleeve risk capped at sigma_o; gross notional capped at 8x NAV.
  A sleeve is live once it has L months + 3 months of history.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

import harness as lib

MONTHLY_SLEEVES = set()

FEE, SPREAD, SIGMA_ETF = 0.0085, lib.FIN_SPREAD, 0.12
VOL_RATIO = 1.25        # realised / nominal overlay vol measured on DEV 1993-2012 (dev_grid.log: 9.9/8, 12.4/10)
GROSS_CAP, SHRINK, MIN_PAIR = 8.0, 0.5, 24


def _ewm_var(r: np.ndarray, com: float) -> np.ndarray:
    """causal EWMA of r^2 (adjust=True weights); NaN rows (no data) keep the state."""
    a = 1.0 / (1.0 + com)
    out = np.full(len(r), np.nan)
    num = den = 0.0
    for i, x in enumerate(r):
        if np.isfinite(x):
            num = (1 - a) * num + x * x
            den = (1 - a) * den + 1.0
        if den > 0:
            out[i] = num / den
    return out


def _f4_core(P, is_wk, monthly_rows, p):
    """F4r2_stress_xs equity exposure 1 + k u - f (1-u), stress (sigma10 > vs) -> 1 - f; u = mean of the
    last t samples of [market/tbill > its n-row SMA]; decided on weekly rows (monthly rows on
    INTL_OLD_M with the OLD adaptation: SMA 10 months, 3-month vol of monthly returns)."""
    k, f_, vs, n, t = p.get("k", 0.5), p.get("f", 0.3), p.get("vs", 0.30), int(p.get("sma", 200)), int(p.get("t", 8))
    x = (P["market"] / P["tbill"]).values
    mk = P["market"].values
    dates = P.index
    out = {}
    hist = []
    if monthly_rows is not None:
        rows = np.where(dates.isin(monthly_rows))[0]
        n_m = max(1, int(round(n / 21)))
        for j, i in enumerate(rows):
            xs = x[rows[:j + 1]]
            if len(xs) < n_m:
                out[i] = 1.0
                continue
            s = float(xs[-1] > xs[-n_m:].mean())
            hist.append(s); hist = hist[-t:]
            u = sum(hist) / len(hist)
            mr = mk[rows[max(0, j - 3):j + 1]]
            v = np.std(np.diff(np.log(mr)), ddof=1) * math.sqrt(12) if len(mr) >= 4 else 0.0
            out[i] = (1.0 - f_) if v > vs else 1.0 + k * u - f_ * (1 - u)
        return out
    lr = np.r_[np.nan, np.diff(np.log(mk))]
    for i in np.where(is_wk)[0]:
        if i + 1 < n or not np.isfinite(x[i - n + 1:i + 1]).all():
            out[i] = 1.0
            continue
        s = float(x[i] > x[i - n + 1:i + 1].mean())
        hist.append(s); hist = hist[-t:]
        u = sum(hist) / len(hist)
        rr = lr[i - 9:i + 1]
        rr = rr[np.isfinite(rr)]
        days = (dates[i] - dates[i - 10]).days
        v = np.std(rr, ddof=1) * math.sqrt(len(rr) / max(days / 365.25, 1e-9)) if len(rr) >= 5 else 0.0
        out[i] = (1.0 - f_) if v > vs else 1.0 + k * u - f_ * (1 - u)
    return out


def overlay_targets(P, sleeves, p, dec_rows):
    """{row: {sleeve: weight}} for each overlay decision row (uses rows <= that row only)."""
    sig_o = p["sigma_o"]
    L = p.get("lb", 12)
    W = int(round(p.get("cw", 36)))
    blend = p.get("blend", 1)
    dates = P.index
    E = P[sleeves].values.astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        R = np.vstack([np.full(len(sleeves), np.nan), E[1:] / E[:-1] - 1.0])
    first = np.array([np.argmax(np.isfinite(E[:, j])) if np.isfinite(E[:, j]).any() else len(E) for j in range(len(sleeves))])
    var = np.column_stack([_ewm_var(R[:, j], 60.0) for j in range(len(sleeves))])
    # rows per year (trailing 365 days), per row
    t_ns = dates.values.astype("datetime64[D]").astype(np.int64)
    lo = np.searchsorted(t_ns, t_ns - 365, side="left")
    ppy = (np.arange(len(dates)) - lo + 1).astype(float)
    ppy[t_ns - t_ns[0] < 365] = np.nan
    sq = np.where(np.isfinite(R), R * R, 0.0)
    csq = np.cumsum(sq, axis=0)
    lo3 = np.searchsorted(t_ns, t_ns - 3 * 365, side="left")
    mon = [j for j, s in enumerate(sleeves) if s in MONTHLY_SLEEVES]
    out = {}
    lvl_hist = []                                      # month-end levels at decision rows
    lbs = ([1, 3, L] if blend else [L])
    for d in dec_rows:
        lvl_hist.append(E[d].copy())
        t = dates[d]
        live, s, sg = [], [], []
        for j, name in enumerate(sleeves):
            if not np.isfinite(E[d, j]) or first[j] >= d:
                continue
            if dates[first[j]] > t - pd.DateOffset(months=int(math.ceil(L)) + 3):
                continue
            sig = []
            for m in lbs:
                m_ = pd.DateOffset(months=int(m), days=int(round((m - int(m)) * 30.44)))
                k0 = int(dates.searchsorted(t - m_, side="right")) - 1
                if k0 < first[j]:
                    sig = None
                    break
                sig.append(np.sign(E[d, j] / E[k0, j] - 1.0))
            if sig is None:
                continue
            if j in mon:
                yrs = (t - dates[lo3[d]]).days / 365.25
                v_ = (csq[d, j] - csq[lo3[d], j]) / max(yrs, 1e-9)
            else:
                v_ = var[d, j] * ppy[d]
            if not np.isfinite(v_) or v_ <= 0:
                continue
            live.append(j); s.append(float(np.mean(sig))); sg.append(math.sqrt(v_))
        w = {}
        if live and any(abs(x) > 0 for x in s):
            Lh = np.array(lvl_hist[-(W + 1):])[:, live]
            with np.errstate(invalid="ignore", divide="ignore"):
                Mr = Lh[1:] / Lh[:-1] - 1.0
            n = len(live)
            C = np.eye(n)
            if len(Mr) >= MIN_PAIR:
                df = pd.DataFrame(Mr)
                Cc = df.corr(min_periods=MIN_PAIR).values
                C = np.where(np.isfinite(Cc), Cc, 0.0)
                np.fill_diagonal(C, 1.0)
            C = (1 - SHRINK) * C + SHRINK * np.eye(n)
            v = np.array(s)
            sp = math.sqrt(max(float(v @ C @ v), 1e-12))
            kk = sig_o / sp
            risk = np.minimum(np.abs(v) * kk, sig_o) * np.sign(v)
            wt = risk / np.array(sg)
            g = np.abs(wt).sum()
            if g > GROSS_CAP:
                wt *= GROSS_CAP / g
            w = {sleeves[j]: float(x) for j, x in zip(live, wt) if x != 0}
        out[d] = w
    return out


def run(P, sleeves, start, end, p, *, cost_bps=lib.LOW_COST_BPS, core="1x", monthly_rows=None, lag=1,
        drag=True, ov=None, is_m=None, is_w=None):
    """Simulate the book from `start` to `end`. Returns dict(nav, overlay_ret, decisions, turnover, ...).
    `ov`: precomputed overlay_targets (identical for every cost level)."""
    P = P.loc[:pd.Timestamp(end)]
    dates = P.index
    b = int(dates.searchsorted(pd.Timestamp(start))) - 1
    if b < 1:
        raise ValueError("no base session")
    S = P["market"].values.astype(float)
    tb = P["tbill"].values.astype(float)
    Ev = P[sleeves].values.astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        R = np.r_[np.nan, S[1:] / S[:-1] - 1.0]
        rtb = np.r_[np.nan, tb[1:] / tb[:-1] - 1.0]
        Re = np.vstack([np.full(len(sleeves), np.nan), Ev[1:] / Ev[:-1] - 1.0])
    Re = np.where(np.isfinite(Re), Re, 0.0)
    gap = np.r_[1, np.diff(dates.values).astype("timedelta64[D]").astype(float)]
    fin_d = lib.FIN_SPREAD * gap / 365.0
    E_etf = VOL_RATIO * p["sigma_o"] / SIGMA_ETF if p["sigma_o"] > 0 else 0.0
    drag_d = (FEE + SPREAD) * E_etf * gap / 365.25 if drag else np.zeros(len(gap))
    is_m = lib.decision_days(dates, "M") if is_m is None else np.asarray(is_m, dtype=bool)[:len(dates)]   # A1
    is_w = lib.decision_days(dates, "W-WED") if is_w is None else np.asarray(is_w, dtype=bool)[:len(dates)]
    dec_rows = np.where(is_m)[0]
    dec_rows = dec_rows[dec_rows < len(dates)]
    pre = dec_rows[dec_rows <= b - lag]
    d_first = pre[-1] if len(pre) else b - lag      # the last decision whose fill is at or before b
    if ov is None:
        ov = overlay_targets(P, sleeves, p, dec_rows[dec_rows >= d_first]) if p["sigma_o"] > 0 else {}
    core_w = _f4_core(P, is_w, monthly_rows, p) if core == "f4" else {}
    cst = cost_bps / 1e4
    nl = len(dates) - 1
    pend_ov, pend_core = {}, {}
    for d, w in ov.items():
        if d >= d_first and d + lag <= nl:
            pend_ov[max(d + lag, b)] = w
    cd = sorted(core_w)
    for d in cd:
        if d + lag <= nl:
            if d + lag <= b:
                pend_core[b] = core_w[d]            # latest core decision filled by the first session
            else:
                pend_core[d + lag] = core_w[d]
    M, C = 0.0, 1.0
    N = np.zeros(len(sleeves))
    cur_core = 0.0 if core == "none" else 1.0
    navs = np.empty(nl - b + 1)
    ovr = np.zeros(nl - b + 1)
    traded_core = traded_ov = 0.0
    decisions = []
    idx = {s: j for j, s in enumerate(sleeves)}
    first = True
    for i in range(b, nl + 1):
        k_ = i - b
        nav0 = M + C
        if i > b:
            M *= 1.0 + R[i]
            pnl = float(N @ Re[i])
            N = N * (1.0 + Re[i])
            C = C * (1.0 + rtb[i] + (fin_d[i] if C < 0 else 0.0)) + pnl - nav0 * drag_d[i]
            ovr[k_] = (pnl - nav0 * drag_d[i]) / nav0
        trade = first or (i in pend_ov) or (i in pend_core)
        if trade:
            if i in pend_core:
                cur_core = pend_core[i]
            tgtN = N.copy()
            if i in pend_ov:
                tgtN = np.zeros(len(sleeves))
                for s_, w_ in pend_ov[i].items():
                    tgtN[idx[s_]] = w_
                decisions.append({"date": dates[i], "core": cur_core, **pend_ov[i]})
                frac = True
            else:
                frac = False
            nav = M + C
            nav_after = nav
            for _ in range(4):
                newM = cur_core * nav_after
                newN = tgtN * nav_after if frac else N
                gc = abs(newM - M)
                go = float(np.abs(newN - N).sum())
                cost = cst * (gc + go)
                nav_after = nav - cost
            traded_core += gc / nav
            traded_ov += go / nav
            C = nav - cost - newM
            M = newM
            N = newN
            first = False
        navs[k_] = M + C
        if navs[k_] <= 0:
            raise ValueError(f"{dates[i].date()}: NAV hit zero (ruin)")
    nav = pd.Series(navs, index=dates[b:])
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    nav_end = M + C                                                            # A2: reporting only
    final = {"core": M / nav_end, **{s_: float(N[j] / nav_end) for j, s_ in enumerate(sleeves) if N[j] != 0}}
    final["cash"] = C / nav_end
    last_dec = {"core": core_w.get(nl) if is_w[nl] else None, "overlay": ov.get(nl) if is_m[nl] else None}
    return {"final_weights": final, "last_row_decision": last_dec, "nav": nav, "overlay_ret": pd.Series(ovr, index=dates[b:]), "decisions": decisions, "ov": ov,
            "turnover_core": traded_core / 2 / yrs, "turnover_ov": traded_ov / 2 / yrs, "years": yrs,
            "E_etf": E_etf, "max_core": max([1.0] + list(core_w.values())) if core_w else 1.0}
