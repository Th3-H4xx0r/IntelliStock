"""Frozen harness for the 2026-11 forward test (self-contained; no imports from the hunt scratchpad).

Ported from the strategy hunt (hunt/lib.py sha1 f291498b03, hunt/f14/bt.py sha1 8d02807bb7):
  * decision_days, BTResult, judge (+ its helpers)  -- copied verbatim (OLD/INTL monthly branches dropped:
    every series here is daily, set "VAL"-style: C1 margin 1.0 pp, 252 periods a year).
  * backtest                                       -- f14/bt.py (lib.backtest with frame columns as the
    instruments), accounting UNCHANGED. Two additive edits, listed in FROZEN.md:
      (A1) `is_dec` may be passed in, computed on the NYSE calendar extended past the last data row
           (lib flags the last data row as a period end ALWAYS; harmless in a backtest that stops
           there, wrong when the last row is "today" and we print tomorrow's trade);
      (A2) the drifted end-of-run book is returned in meta["final_weights"] (reporting only).
  * NYSE holiday calendar (new): used ONLY to extend the session calendar past the last data row.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

COST_BPS = 23.0          # stress cost (PROTOCOL C5 / R10)
LOW_COST_BPS = 5.0       # primary cost (R10)
FIN_SPREAD = 0.005       # margin financed at T-bill + 0.5%/yr
WIN_EPS = 1e-6           # R8: a window is won only by a measurable margin


# ----------------------------------------------------------------------------- NYSE calendar
def _easter(y: int) -> _dt.date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a = y % 19; b = y // 100; c = y % 100
    d = b // 4; e = b % 4; f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = ((h + l_ - 7 * m + 114) % 31) + 1
    return _dt.date(y, month, day)


def _nth_weekday(y, month, weekday, n):
    d = _dt.date(y, month, 1)
    d += _dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + _dt.timedelta(weeks=n - 1)


def _last_weekday(y, month, weekday):
    d = _dt.date(y + (month == 12), month % 12 + 1, 1) - _dt.timedelta(days=1)
    return d - _dt.timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: _dt.date, new_year=False):
    if d.weekday() == 5:                       # Saturday -> Friday (NYSE skips this for New Year's Day)
        return None if new_year else d - _dt.timedelta(days=1)
    if d.weekday() == 6:                       # Sunday -> Monday
        return d + _dt.timedelta(days=1)
    return d


def nyse_holidays(y: int) -> set:
    """Regular full-day NYSE holidays of year y (rules in force since 2022; unscheduled closures such as
    national days of mourning cannot be known in advance and are not included)."""
    out = [
        _observed(_dt.date(y, 1, 1), new_year=True),
        _nth_weekday(y, 1, 0, 3),               # Martin Luther King Jr. Day
        _nth_weekday(y, 2, 0, 3),               # Washington's Birthday
        _easter(y) - _dt.timedelta(days=2),     # Good Friday
        _last_weekday(y, 5, 0),                 # Memorial Day
        _observed(_dt.date(y, 6, 19)) if y >= 2022 else None,   # Juneteenth
        _observed(_dt.date(y, 7, 4)),
        _nth_weekday(y, 9, 0, 1),               # Labor Day
        _nth_weekday(y, 11, 3, 4),              # Thanksgiving
        _observed(_dt.date(y, 12, 25)),
    ]
    return {pd.Timestamp(d) for d in out if d is not None}


def nyse_sessions(start, end) -> pd.DatetimeIndex:
    days = pd.bdate_range(pd.Timestamp(start), pd.Timestamp(end))
    hol = set()
    for y in range(days[0].year, days[-1].year + 1) if len(days) else []:
        hol |= nyse_holidays(y)
    return pd.DatetimeIndex([d for d in days if d not in hol])


def extend_calendar(idx: pd.DatetimeIndex, months: int = 2) -> pd.DatetimeIndex:
    """The data's session index followed by the expected NYSE sessions through the end of the month
    `months` months after the last data row. Rows <= idx[-1] are exactly idx."""
    last = idx[-1]
    end = (last + pd.offsets.MonthEnd(0)) + pd.offsets.MonthEnd(months)
    fut = nyse_sessions(last + pd.Timedelta(days=1), end)
    return idx.append(fut)


def flags_on_extended(idx: pd.DatetimeIndex, rebalance: str) -> np.ndarray:
    """decision_days computed on the extended calendar, truncated to the data rows (adaptation A1)."""
    return decision_days(extend_calendar(idx), rebalance)[:len(idx)]


# ----------------------------------------------------------------------------- schedule (verbatim lib)
def decision_days(dates: pd.DatetimeIndex, rebalance: str) -> np.ndarray:
    """Boolean mask: True on the last session of each rebalance period.

    "D" every session; "W-WED" (any W-XXX) last session of the week ending that weekday
    (i.e. Wednesday, or Tuesday if Wednesday is a holiday); "M" month; "Q" quarter; "A" year.
    """
    if rebalance == "D":
        return np.ones(len(dates), dtype=bool)
    freq = {"M": "M", "Q": "Q", "A": "Y", "Y": "Y"}.get(rebalance, rebalance)
    if not (freq in ("M", "Q", "Y") or freq.startswith("W")):
        raise ValueError(f"unsupported rebalance '{rebalance}'")
    per = dates.to_period(freq)
    nxt = np.empty(len(per), dtype=object)
    nxt[:-1] = per[1:]
    nxt[-1] = None
    return np.array([n is None or n != p for p, n in zip(per, nxt)])


def set_freq(set_name: str) -> str:
    return "D"


# ----------------------------------------------------------------------------- backtest (f14/bt.py)
@dataclass
class BTResult:
    nav: pd.Series                 # NAV, 1.0 at the base close (session before `start`)
    decisions: pd.DataFrame        # one row per decision: date, exec_date, target weights
    trades: pd.DataFrame           # one row per executed rebalance
    turnover: float                # one-way, per year: (buys+sells)/2/NAV
    traded_gross: float            # (buys+sells)/NAV per year (what the cost is charged on)
    cost_total: float              # sum of costs as a fraction of NAV at each trade
    max_gross: float               # max over targets of sum of weights (excl. tbill)
    max_exposure: float            # max over targets of sum(w * leverage factor)
    avg_gross: float
    meta: dict = field(default_factory=dict)


def backtest(weights_fn, universe, start, end, *, prices, tradable_from=None, rebalance="W-WED", cost_bps=COST_BPS,
             lag=1, set_name="DEV", country=None, fin_spread=FIN_SPREAD, min_trade=0.0,
             allow_short=False, enforce_tradable=True, is_dec=None) -> BTResult:
    """Simulate a weights rule (hunt f14/bt.py; see that docstring in lib.backtest).

    weights_fn(hist) -> dict {name: weight} | None. `hist` = levels of `universe` + "tbill" up to and
    including the decision close; hist.attrs["tradable"] = columns holdable at the next fill.
    NAV = 1.0 at the close of the session before `start`; the first decision is made one fill-lag before
    that base close. Weights > 1 in total are financed at the T-bill + fin_spread.
    is_dec (A1): optional precomputed period-end mask over prices.loc[:end].index.
    """
    tradable_from = tradable_from or {}
    universe = [u for u in dict.fromkeys(universe) if u != "tbill"]
    names = {u: u for u in universe + ["tbill"]}            # frame columns ARE the instruments
    P = prices.loc[:pd.Timestamp(end), universe + ["tbill"]]
    dates = P.index
    b = int(dates.searchsorted(pd.Timestamp(start))) - 1      # base session index
    if b < 1:
        raise ValueError(f"no session before {start} to use as the base")
    d0 = b - lag
    if d0 < 0:
        raise ValueError("not enough history for the first decision")
    if P["tbill"].iloc[d0:].isna().any():
        raise ValueError("T-bill series has gaps in the window")

    vals = P.values.astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        R = np.vstack([np.full(vals.shape[1], np.nan), vals[1:] / vals[:-1] - 1.0])
    k_tb = len(universe)
    rtb = R[:, k_tb]
    gap_days = np.r_[1, np.diff(dates.values).astype("timedelta64[D]").astype(float)]
    fin_d = fin_spread * gap_days / 365.0
    col = {u: k for k, u in enumerate(universe)}
    lev = np.ones(len(universe))                            # no LETF is held
    trad_from = np.array([np.datetime64(tradable_from.get(u, "1800-01-01")) for u in universe])
    if is_dec is None:
        is_dec = decision_days(dates, rebalance)
    else:
        is_dec = np.asarray(is_dec, dtype=bool)
        assert len(is_dec) == len(dates), "is_dec must cover prices.loc[:end]"
    n_last = len(dates) - 1
    c = cost_bps / 1e4

    pending: dict[int, np.ndarray] = {}
    dec_rows, trade_rows = [], []
    gross_list, expo_list = [], []

    def decide(i):
        hist = P.iloc[: i + 1]
        j = min(i + lag, n_last)
        ok = (~np.isnan(vals[j, :k_tb])) & (trad_from <= dates.values[j])
        hist.attrs["tradable"] = {u for u in universe if ok[col[u]]} | {"tbill"}
        hist.attrs["set"] = set_name
        hist.attrs["freq"] = set_freq(set_name)
        nav_now = h.sum() + cash
        hist.attrs["weights"] = {**{u: h[col[u]] / nav_now for u in universe if h[col[u]] != 0},
                                 "tbill": cash / nav_now}
        w = weights_fn(hist)
        if w is None:
            return
        tgt = np.zeros(k_tb)
        for k_, v in w.items():
            v = float(v)
            if not math.isfinite(v):
                raise ValueError(f"{dates[i].date()}: non-finite weight for {k_}")
            if k_ == "tbill":
                continue
            if k_ not in col:
                raise KeyError(f"{dates[i].date()}: weight for '{k_}' not in universe {universe}")
            if v < 0 and not allow_short:
                raise ValueError(f"{dates[i].date()}: negative weight for {k_} (shorting is off)")
            if abs(v) > 0 and not ok[col[k_]]:
                raise ValueError(f"{dates[i].date()}: '{k_}' ({names[k_]}) is not tradable on "
                                 f"{dates[j].date()} — check hist.attrs['tradable']")
            tgt[col[k_]] = v
        dec_rows.append({"date": dates[i], "exec_date": dates[i + lag] if i + lag <= n_last else pd.NaT,
                         **{u: tgt[col[u]] for u in universe}})
        gross_list.append(float(np.abs(tgt).sum()))
        expo_list.append(float((np.abs(tgt) * lev).sum()))
        if i + lag <= n_last:
            pending[i + lag] = tgt

    h = np.zeros(k_tb)
    cash = 1.0
    navs = np.empty(n_last - b + 1)
    traded = 0.0
    cost_total = 0.0
    for i in range(d0, b):            # first decision(s) before the base close
        if i == d0 or is_dec[i]:
            decide(i)
    for i in range(b, n_last + 1):
        if i > b:
            held = h != 0
            if held.any():
                r = R[i, :k_tb]
                if np.isnan(r[held]).any():
                    bad = [u for u in universe if held[col[u]] and np.isnan(r[col[u]])]
                    raise ValueError(f"{dates[i].date()}: no return for held {bad}")
                h = np.where(held, h * (1.0 + np.nan_to_num(r)), 0.0)
            cash *= 1.0 + rtb[i] + (fin_d[i] if cash < 0 else 0.0)
        if lag == 0 and (i == d0 or is_dec[i]):
            decide(i)
        if i in pending:
            tgt = pending.pop(i)
            nav = h.sum() + cash
            # costs come out of NAV first, so post-trade weights equal the target exactly
            nav_after = nav
            for _ in range(4):
                new_h = tgt * nav_after
                d = new_h - h
                if min_trade > 0:
                    small = np.abs(d) < min_trade * nav
                    small &= ~((tgt == 0) & (h != 0))          # exits always go through
                    new_h[small] = h[small]
                    d[small] = 0.0
                g = np.abs(d).sum()
                cost = c * g
                nav_after = nav - cost
            cash = nav - new_h.sum() - cost
            h = new_h
            traded += g / nav
            cost_total += cost / nav
            if g > 1e-9 * nav:
                trade_rows.append({"date": dates[i], "traded": g / nav, "cost": cost / nav, "nav": nav})
        navs[i - b] = h.sum() + cash
        if navs[i - b] <= 0:
            raise ValueError(f"{dates[i].date()}: NAV hit zero (ruin)")
        if lag > 0 and is_dec[i]:
            decide(i)

    nav = pd.Series(navs, index=dates[b:])
    years = (nav.index[-1] - nav.index[0]).days / 365.25
    nav_end = h.sum() + cash
    final_w = {u: float(h[col[u]] / nav_end) for u in universe if h[col[u]] != 0}      # (A2)
    final_w["tbill"] = float(cash / nav_end)
    return BTResult(nav=nav, decisions=pd.DataFrame(dec_rows), trades=pd.DataFrame(trade_rows),
                    turnover=traded / 2 / years, traded_gross=traded / years, cost_total=cost_total,
                    max_gross=max(gross_list) if gross_list else 0.0,
                    max_exposure=max(expo_list) if expo_list else 0.0,
                    avg_gross=float(np.mean(gross_list)) if gross_list else 0.0,
                    meta=dict(set=set_name, country=country, rebalance=rebalance, lag=lag,
                              cost_bps=cost_bps, names=names, start=str(nav.index[1].date()),
                              end=str(nav.index[-1].date()), final_weights=final_w))


# ----------------------------------------------------------------------------- judge (verbatim lib)
WINDOWS = {"3m": 3, "6m": 6, "12m": 12, "3y": 36, "5y": 60}


def _cagr(nav):
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    return (nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1


def _mdd(nav):
    return float((nav / nav.cummax() - 1).min())


def _window_returns(nav: pd.Series, months: int, monthly: bool = False):
    """Daily-stepped trailing returns over `months` calendar months (start = last session on or
    before t - months). Only windows fully inside the series."""
    if monthly:
        v = nav.values
        end_i = np.arange(months, len(v))
        return end_i, v[end_i] / v[end_i - months] - 1.0
    idx = nav.index
    lagged = idx - pd.DateOffset(months=months)
    pos = idx.searchsorted(lagged, side="right") - 1
    ok = (lagged >= idx[0]) & (pos >= 0)
    v = nav.values
    end_i = np.where(ok)[0]
    return end_i, v[end_i] / v[pos[ok]] - 1.0


def _longest_below_high(rel: pd.Series) -> float:
    """Longest spell (years) the relative line stayed below its prior high (incl. an open spell)."""
    hi = rel.cummax()
    below = rel.values < hi.values * (1 - 1e-12)
    best, start = 0.0, None
    d = rel.index
    for i, bflag in enumerate(below):
        if bflag and start is None:
            start = d[i - 1] if i > 0 else d[i]
        if not bflag and start is not None:
            best = max(best, (d[i] - start).days / 365.25)
            start = None
    if start is not None:
        best = max(best, (d[-1] - start).days / 365.25)
    return best


def judge(nav: pd.Series, bench_nav: pd.Series, set_name: str = "VAL", *, c1_margin=None) -> dict:
    """Every PROTOCOL metric for one strategy NAV vs its benchmark NAV over the same span (daily rows).
    C1: CAGR >= bench + 1.0pp. C2: >= 80% of rolling 3y AND >= 90% of rolling 5y windows won.
    C3: maxDD no deeper than the benchmark's."""
    monthly = False
    ppy = 252
    ix = nav.index.intersection(bench_nav.index)
    nav, bench = nav.loc[ix], bench_nav.loc[ix]
    nav, bench = nav / nav.iloc[0], bench / bench.iloc[0]
    cs, cb = _cagr(nav), _cagr(bench)
    out = {"set": set_name, "start": str(ix[1].date()), "end": str(ix[-1].date()),
           "years": round((ix[-1] - ix[0]).days / 365.25, 2),
           "cagr": cs, "bench_cagr": cb, "excess_pp": (cs - cb) * 100,
           "mdd": _mdd(nav), "bench_mdd": _mdd(bench)}
    vs = nav.pct_change().dropna()
    vb = bench.pct_change().dropna()
    out["vol"] = float(vs.std() * math.sqrt(ppy))
    out["bench_vol"] = float(vb.std() * math.sqrt(ppy))
    out["freq"] = "D"
    for k, m in WINDOWS.items():
        e1, r1 = _window_returns(nav, m, monthly)
        e2, r2 = _window_returns(bench, m, monthly)
        assert np.array_equal(e1, e2)
        out[f"win_{k}"] = float(np.mean((r1 - r2) > WIN_EPS)) if len(r1) else None
        out[f"n_{k}"] = int(len(r1))
        if k == "12m":
            rel = (1 + r1) / (1 + r2) - 1
            out["worst_12m_rel"] = float(rel.min()) if len(rel) else None
            out["worst_12m_rel_end"] = str(nav.index[e1[np.argmin(rel)]].date()) if len(rel) else None
    out["longest_below_rel_high_yrs"] = round(_longest_below_high(nav / bench), 2)
    margin = 1.0 if c1_margin is None else c1_margin
    out["C1"] = bool(out["excess_pp"] >= margin - 1e-9)
    out["C2"] = bool(out["win_3y"] is not None and out["win_3y"] >= 0.80 and
                     out["win_5y"] is not None and out["win_5y"] >= 0.90)
    out["C3"] = bool(out["mdd"] >= out["bench_mdd"] - 1e-12)
    return out


def rebase(level: pd.Series, start) -> pd.Series:
    """Level from the base close (session before `start`), divided by its base value (lib.buy_hold)."""
    s = level.dropna()
    b = int(s.index.searchsorted(pd.Timestamp(start))) - 1
    if b < 0:
        raise ValueError(f"no session before {start}")
    s = s.iloc[b:]
    return s / s.iloc[0]
