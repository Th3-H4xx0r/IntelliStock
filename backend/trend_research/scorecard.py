"""The TR scorecard: one long run's NAV path against SPY total return, by
period and by market regime (spec §3, §7.4, C8, C12; plan Task 10).

Periods. ``overall`` is the whole run; ``optimistic`` is every session on or
before ``research_model_cutoff`` (the model may remember those outcomes, the
cutoff day included); ``honest`` is every session strictly after it. Without a
cutoff the whole run is optimistic and the honest period is ``untested``. The
honest period is only honest if the research chain started cold after the
cutoff: when the chain's first ``ok`` week starts on or before it, the honest
weeks inherit trends and theses written with hindsight, and ``meta`` says so
(``honest_chain_cold`` False plus a note). A period's returns start from the
last close before its first session (the run's opening NAV for the first
period), so ``(1 + optimistic) * (1 + honest) == 1 + overall``.

Regimes. Windows are the battery in ``regime_windows`` plus the rule-classified
windows after it. A window counts in a period only when it lies wholly inside
it (first session on or after the period's first session, last session on or
before its last). ``pass_all_regimes`` is False when any window trails SPY,
``"untested"`` when a regime kind has no window, and True only when every
window beats SPY and all three kinds are present.

The bar (§3). ``pass_plus5``: CAGR at least 5 percentage points a year above
SPY-TR's CAGR over the same span. ``pass`` combines both; a pass on the
optimistic period alone is not a pass.

Random baselines (C8, ruling R2). Each ledger week (status ``ok``) draws N
names uniformly without replacement, N = the week's holdings (default 10),
equal weight, held from the last close before the week's first session to
the last close before the next ledger week's (the lane holds through a
missing or failed week, so the draw does too). ``random_pctile_candidates``
draws from that week's candidates; ``random_pctile_universe`` from every
candidate named in that week or an earlier one (ruling R24: never a name the
ledger first names later). A name is drawn in a week only with a close in the
7 days before the week AND a close inside the week within 7 days of its end;
a name-week without one is excluded (never scored as a 0% week) and counted
in ``random_excluded_name_weeks``. The closes run through the scorecard's end
date (fetched for every candidate, not only what the ledger builder cached),
so whether a draw has data never depends on later ledger weeks. The percentile is the share of random
portfolios that end below the strategy over the same weeks (ties count half).
Fixed seed: the same inputs give the same numbers.

Units: returns, CAGR, drawdown and excess are percent; ``excess_pp`` is
percentage points a year.
"""
from __future__ import annotations

import bisect
import math
import random
from datetime import date, datetime, timedelta

from swing_trader import clock
from trend_research import regime_windows

__all__ = ["scorecard", "backtest_scorecard", "DEFAULT_HOLDINGS", "PLUS_PP",
           "BENCHMARK_LOOKBACK_DAYS", "NOTES"]

DEFAULT_HOLDINGS = 10
#: The §3 margin: percentage points of CAGR a year above SPY-TR.
PLUS_PP = 5.0
#: Calendar days of SPY history fetched before the run: the regime rule needs
#: 63 sessions before the first date it classifies.
BENCHMARK_LOOKBACK_DAYS = 140
#: A name is drawable in a week only with a close this recent before it.
_FRESH_DAYS = 7
_YEAR_DAYS = 365.25
_EPS = 1e-9
_SEGMENTS = ("overall", "optimistic", "honest")

NOTES = (
    "Engine NAV accrues dividends only for symbols listed in dividend_yields (default "
    "0) while SPY-TR includes all of them: the strategy starts about 1-2%/yr behind.",
    "Before research_model_cutoff the model may remember outcomes; only the honest "
    "period judges the edge.",
    "Random baselines use split-adjusted daily closes (the lane's daily-bar loader, "
    "through the scorecard end), week close to week close, equal weight, no costs; a "
    "name-week without closes is excluded, not scored 0%.",
    "The universe baseline draws each week from every candidate named up to that week.",
    "Survivorship: delisted names may lack bars and are then not drawn.",
)


# -- series ------------------------------------------------------------------------------


def _day(value):
    """The NY session date of a timestamp, or the date itself; None when
    unparseable."""
    if isinstance(value, datetime):
        return clock.as_utc(value).astimezone(clock.NY).date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) == 10:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    ts = clock.as_utc(text)
    return ts.astimezone(clock.NY).date() if ts is not None else None


def _utc_day(bar):
    """A daily bar's date, read the way the lane's loader reads it."""
    ts = clock.as_utc((bar or {}).get("t")) if isinstance(bar, dict) else None
    return ts.date() if ts is not None else None


def _positive(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) and f > 0 else None


class _Series:
    """One value per day, oldest first (the last point of a day wins).

    ``opening=True`` (the NAV path) keeps the run's earliest point -- the
    opening NAV, before the first session's move -- as an extra point dated
    the previous NYSE session, so the strategy, SPY and every name are all
    measured from the close before the first session. ``first_session`` is
    the first real day either way."""

    def __init__(self, points, *, opening: bool = False):
        by_day = {}
        keyed = []
        for i, point in enumerate(points or ()):
            try:
                ts, value = point
            except (TypeError, ValueError):
                continue
            d, v = _day(ts), _positive(value)
            if d is not None and v is not None:
                at = clock.as_utc(ts) if isinstance(ts, (datetime, str)) else None
                keyed.append((d, at.timestamp() if at is not None else -math.inf, i, v))
        keyed.sort(key=lambda k: k[:3])
        for d, _at, _i, v in keyed:
            by_day[d] = v
        self.first_session = min(by_day) if by_day else None
        if opening and keyed:
            prior = (_sessions_near(self.first_session - timedelta(days=1), forward=False)
                     or self.first_session - timedelta(days=1))
            by_day[prior] = keyed[0][3]
        self.days = sorted(by_day)
        self.vals = [by_day[d] for d in self.days]

    def __bool__(self):
        return bool(self.days)

    def before(self, d):
        i = bisect.bisect_left(self.days, d) - 1
        return i if i >= 0 else None

    def on_or_before(self, d):
        i = bisect.bisect_right(self.days, d) - 1
        return i if i >= 0 else None

    def on_or_after(self, d):
        i = bisect.bisect_left(self.days, d)
        return i if i < len(self.days) else None

    def base(self, d):
        """The last point before ``d``, else the first on or after it."""
        i = self.before(d)
        return i if i is not None else self.on_or_after(d)


def _ret(series, i, j):
    if i is None or j is None or j < i:
        return None
    return series.vals[j] / series.vals[i] - 1


def _cagr(ret, span_days):
    if ret is None or span_days <= 0 or ret <= -1:
        return None
    try:
        return (1 + ret) ** (_YEAR_DAYS / span_days) - 1
    except OverflowError:                   # a huge move over a few days
        return None


def _max_dd(vals):
    if not vals:
        return None
    peak, worst = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def _pct(x, nd=4):
    return None if x is None else round(100 * x, nd)


# -- windows -----------------------------------------------------------------------------


_SESSION_CACHE: dict = {}


def _sessions_near(d: date, *, forward: bool):
    key = (d, forward)
    if key not in _SESSION_CACHE:
        lo, hi = (d, d + timedelta(days=10)) if forward else (d - timedelta(days=10), d)
        days = sorted(clock.trading_days(lo, hi))
        _SESSION_CACHE[key] = (days[0] if forward else days[-1]) if days else None
    return _SESSION_CACHE[key]


def _all_windows(spy: _Series) -> list:
    after = regime_windows.classify_after(
        list(zip(spy.days, spy.vals)), regime_windows.BATTERY_END + timedelta(days=1))
    out = []
    for source, windows in (("battery", regime_windows.scored_windows()), ("rule", after)):
        for kind, tag, start, end in windows:
            ws = _sessions_near(date.fromisoformat(start), forward=True)
            we = _sessions_near(date.fromisoformat(end), forward=False)
            if ws is not None and we is not None and ws <= we:
                out.append({"tag": tag, "kind": kind, "start": start, "end": end,
                            "first": ws, "last": we, "source": source})
    return out


def _score_windows(windows, nav, spy, lo, hi):
    out = []
    for w in windows:
        if w["first"] < lo or w["last"] > hi:
            continue
        ret = _ret(nav, nav.base(w["first"]), nav.on_or_before(w["last"]))
        spy_ret = _ret(spy, spy.base(w["first"]), spy.on_or_before(w["last"]))
        beat = None if ret is None or spy_ret is None else ret > spy_ret
        out.append({"tag": w["tag"], "kind": w["kind"], "start": w["start"], "end": w["end"],
                    "source": w["source"], "ret": _pct(ret), "spy_ret": _pct(spy_ret),
                    "beat": beat})
    return out


def _regimes(windows):
    regimes = {}
    for kind in regime_windows.SCORED_KINDS:
        beats = [w["beat"] for w in windows if w["kind"] == kind]
        if any(b is False for b in beats):
            regimes[kind] = False
        elif not beats or any(b is None for b in beats):
            regimes[kind] = "untested"          # no window, or one without SPY data
        else:
            regimes[kind] = True
    if any(w["beat"] is False for w in windows):
        verdict = False
    elif any(v == "untested" for v in regimes.values()):
        verdict = "untested"
    else:
        verdict = True
    return regimes, verdict


# -- random baselines --------------------------------------------------------------------


def _symbol(item):
    raw = item.get("symbol") if isinstance(item, dict) else item
    sym = str(raw or "").strip().upper()
    return sym or None


def _week_plan(candidate_weeks) -> list:
    """The ``ok`` ledger weeks, oldest first, one per Monday:
    ``[{"week_start", "monday", "symbols", "seen", "n"}]``. ``seen`` is every
    candidate named in this or an earlier ok week (the universe pool, R24).
    A row without ``status: "ok"`` is skipped."""
    by_monday = {}
    for row in candidate_weeks or ():
        if not isinstance(row, dict) or row.get("status") != "ok":
            continue
        try:
            monday = date.fromisoformat(str(row.get("week_start"))[:10])
        except ValueError:
            continue
        if monday in by_monday:
            continue
        symbols = []
        for item in row.get("candidates") or ():
            sym = _symbol(item)
            if sym and sym not in symbols:
                symbols.append(sym)
        n = row.get("n_holdings")
        if not (isinstance(n, int) and not isinstance(n, bool) and n > 0):
            held = {_symbol(r) for r in row.get("recommended") or ()} - {None}
            n = len(held) or DEFAULT_HOLDINGS
        by_monday[monday] = {"week_start": monday.isoformat(), "monday": monday,
                             "symbols": symbols, "n": n}
    plan, seen = [by_monday[m] for m in sorted(by_monday)], set()
    for week in plan:
        seen.update(week["symbols"])
        week["seen"] = sorted(seen)
    return plan


def _periods(plan, in_segment, lo, hi):
    """``[(week, start, stop)]``: the week is held from the close before
    ``start`` to the close before ``stop``, clipped to the period."""
    out = []
    for i, week in enumerate(plan):
        if not in_segment(week["monday"]):
            continue
        start = max(week["monday"], lo)
        stop = plan[i + 1]["monday"] if i + 1 < len(plan) else hi + timedelta(days=1)
        stop = min(stop, hi + timedelta(days=1))
        if start < stop:
            out.append((week, start, stop))
    return out


def _name_ret(series, start, stop):
    """The name's return held from the close before ``start`` to the close
    before ``stop``; None (excluded, not 0%) unless both closes are fresh --
    one within ``_FRESH_DAYS`` before ``start``, one inside the week within
    ``_FRESH_DAYS`` of ``stop``."""
    i = series.before(start)
    if i is None or (start - series.days[i]).days > _FRESH_DAYS:
        return None
    j = series.before(stop)
    if (j is None or j <= i or series.days[j] < start
            or (stop - series.days[j]).days > _FRESH_DAYS):
        return None
    return _ret(series, i, j)


def _random_pctiles(periods, nav, closes, universe, n_random, seed, segment):
    out = {"candidates": None, "universe": None, "excluded": None}
    if not periods or not closes or n_random <= 0:
        return out
    strat, steps = 1.0, []
    for week, start, stop in periods:
        r = _ret(nav, nav.base(start), nav.before(stop))
        if r is None:
            continue
        strat *= 1 + r
        rets = {}
        for sym in universe:
            series = closes.get(sym)
            nr = _name_ret(series, start, stop) if series else None
            if nr is not None:
                rets[sym] = nr
        steps.append((week, rets))
    if not steps:
        return out
    out["excluded"] = sum(1 for week, rets in steps for s in week["seen"] if s not in rets)
    for pool_name in ("candidates", "universe"):
        pools = []
        for week, rets in steps:
            names = week["symbols"] if pool_name == "candidates" else week["seen"]
            pool = [rets[s] for s in names if s in rets]
            pools.append((pool, min(week["n"], len(pool))))
        if not any(k for _, k in pools):
            continue
        rng = random.Random("tr-scorecard|%s|%s|%s" % (seed, pool_name, segment))
        below = ties = 0
        for _ in range(n_random):
            total = 1.0
            for pool, k in pools:
                if k:
                    total *= 1 + sum(rng.sample(pool, k)) / k
            if abs(total - strat) <= _EPS * max(1.0, abs(strat)):
                ties += 1
            elif total < strat:
                below += 1
        out[pool_name] = round(100.0 * (below + 0.5 * ties) / n_random, 2)
    return out


# -- periods -----------------------------------------------------------------------------


def _untested():
    return {"untested": True, "start": None, "end": None, "sessions": 0,
            "ret": None, "spy_ret": None, "qqq_ret": None,
            "cagr": None, "spy_cagr": None, "qqq_cagr": None, "excess_pp": None,
            "max_dd": None, "spy_max_dd": None, "roll12_beat_pct": None,
            "windows": [], "regimes": {k: "untested" for k in regime_windows.SCORED_KINDS},
            "random_pctile_candidates": None, "random_pctile_universe": None,
            "random_excluded_name_weeks": None,
            "pass_all_regimes": "untested", "pass_plus5": "untested", "pass": "untested"}


def _roll12(nav, spy, base, end):
    base_day = nav.days[base]
    beats = total = 0
    for i in range(base, end + 1):
        back = nav.days[i] - timedelta(days=365)
        if back < base_day:
            continue
        j = nav.on_or_before(back)
        s0, s1 = spy.on_or_before(back), spy.on_or_before(nav.days[i])
        r, sr = _ret(nav, j, i), _ret(spy, s0, s1)
        if r is None or sr is None:
            continue
        total += 1
        beats += r > sr
    return None if not total else round(100.0 * beats / total, 2)


def _segment(name, in_segment, nav, spy, qqq, windows, plan, closes, universe, n_random, seed):
    days = [d for d in nav.days if d >= nav.first_session and in_segment(d)] if nav else []
    if not days:
        return _untested()
    lo, hi = days[0], days[-1]
    base, end = nav.base(lo), nav.on_or_before(hi)
    span = (hi - nav.days[base]).days
    ret = _ret(nav, base, end)

    def bench(series):
        if not series:
            return None, None, None
        i, j = series.base(lo), series.on_or_before(hi)
        r = _ret(series, i, j)
        return r, _cagr(r, span), (_max_dd(series.vals[i:j + 1]) if r is not None else None)

    spy_ret, spy_cagr, spy_dd = bench(spy)
    qqq_ret, qqq_cagr, _ = bench(qqq)
    cagr = _cagr(ret, span)
    excess = None if cagr is None or spy_cagr is None else 100 * (cagr - spy_cagr)
    scored = _score_windows(windows, nav, spy, lo, hi)
    regimes, all_regimes = _regimes(scored)
    plus5 = "untested" if excess is None else excess >= PLUS_PP - _EPS
    if all_regimes is False or plus5 is False:
        verdict = False
    elif all_regimes == "untested" or plus5 == "untested":
        verdict = "untested"
    else:
        verdict = True
    pctiles = _random_pctiles(_periods(plan, in_segment, lo, hi), nav, closes, universe,
                              n_random, seed, name)
    return {"untested": False, "start": lo.isoformat(), "end": hi.isoformat(),
            "sessions": len(days),
            "ret": _pct(ret), "spy_ret": _pct(spy_ret), "qqq_ret": _pct(qqq_ret),
            "cagr": _pct(cagr), "spy_cagr": _pct(spy_cagr), "qqq_cagr": _pct(qqq_cagr),
            "excess_pp": None if excess is None else round(excess, 4),
            "max_dd": _pct(_max_dd(nav.vals[base:end + 1])), "spy_max_dd": _pct(spy_dd),
            "roll12_beat_pct": _roll12(nav, spy, base, end) if spy else None,
            "windows": scored, "regimes": regimes,
            "random_pctile_candidates": pctiles["candidates"],
            "random_pctile_universe": pctiles["universe"],
            "random_excluded_name_weeks": pctiles["excluded"],
            "pass_all_regimes": all_regimes, "pass_plus5": plus5, "pass": verdict}


def scorecard(nav, spy_tr, qqq, *, cutoff, candidate_weeks, n_random: int = 1000,
              seed: int = 7, closes: dict | None = None,
              chain_first_week=None) -> dict:
    """``{"overall", "optimistic", "honest", "meta"}`` (see the module docstring).

    ``nav``: ``[(timestamp, value)]``, any number of points a day (the last
    wins). ``spy_tr`` / ``qqq``: ``[(date, close)]`` total-return closes.
    ``cutoff``: the model's research cutoff (date), or None. ``candidate_weeks``:
    the run's ledger rows. ``closes``: ``{symbol: [(date, close)]}`` daily closes
    for the random baselines; without them both percentiles are None.
    ``chain_first_week``: the Monday of the chain's first ``ok`` row (any
    window), to say whether the honest chain started cold after the cutoff."""
    nav_s, spy_s, qqq_s = _Series(nav, opening=True), _Series(spy_tr), _Series(qqq)
    cutoff = _as_date(cutoff) if cutoff not in (None, "") else None
    windows = _all_windows(spy_s) if spy_s else []
    plan = _week_plan(candidate_weeks)
    universe = sorted({s for w in plan for s in w["symbols"]})
    close_s = {str(k).upper(): _Series(v) for k, v in (closes or {}).items()}
    preds = {"overall": lambda d: True,
             "optimistic": (lambda d: True) if cutoff is None else (lambda d: d <= cutoff),
             "honest": (lambda d: False) if cutoff is None else (lambda d: d > cutoff)}
    out = {name: _segment(name, preds[name], nav_s, spy_s, qqq_s, windows, plan, close_s,
                          universe, n_random, seed) for name in _SEGMENTS}
    out["meta"] = {"cutoff": cutoff.isoformat() if cutoff else None,
                   "battery_end": regime_windows.BATTERY_END.isoformat(),
                   "rule_windows": sum(1 for w in windows if w["source"] == "rule"),
                   "rule": "after %s: SPY %d-session return > %+.0f%% bull, < %+.0f%% bear, "
                           "else chop; runs under %d sessions absorbed"
                           % (regime_windows.BATTERY_END.isoformat(),
                              regime_windows.LOOKBACK_SESSIONS,
                              100 * regime_windows.BULL_ABOVE, 100 * regime_windows.BEAR_BELOW,
                              regime_windows.MIN_WINDOW_SESSIONS),
                   "plus_pp": PLUS_PP, "n_random": n_random, "seed": seed,
                   "random_excluded_name_weeks": out["overall"]["random_excluded_name_weeks"],
                   "honest_chain_cold": None,
                   "notes": list(NOTES)}
    first = _as_date(chain_first_week) if chain_first_week not in (None, "") else None
    if cutoff is not None and first is not None:
        out["meta"]["chain_first_week"] = first.isoformat()
        out["meta"]["honest_chain_cold"] = first > cutoff
        if first <= cutoff:
            out["meta"]["notes"].append(
                "WARNING: the research chain's first ok week (%s) starts on or before the "
                "model cutoff (%s): the honest weeks build on trends and theses written "
                "with hindsight, so the honest period is not a cold start (spec C10/C11)."
                % (first.isoformat(), cutoff.isoformat()))
    return out


# -- the backtest result -------------------------------------------------------------------


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _bars_to_closes(bars) -> list:
    out = []
    for bar in bars or ():
        d = _utc_day(bar)
        c = _positive((bar or {}).get("c", (bar or {}).get("close")))
        if d is not None and c is not None:
            out.append((d, c))
    return out


def _ledger_rows(cfg, start, end, st) -> list:
    from trend_research import ledger
    chain = str(cfg.get("tr_chain_id") or "").strip()
    model = str(cfg.get("tr_model") or "").strip()
    pv = str(cfg.get("tr_prompt_version") or "tr-v1").strip()
    if not chain or not model or "|" in chain:
        return []
    lo = "%s|%s" % (chain, clock.week_monday(start).isoformat())
    hi = "%s|%s" % (chain, (end + timedelta(days=1)).isoformat())
    return [row for row in st.iter(st.between(ledger.TABLE, lo, hi))
            if row.get("chain_id") == chain and row.get("model") == model
            and row.get("prompt_version") == pv]


def _chain_first_ok_week(cfg, st):
    """The Monday of the configured chain's first ``ok`` row, any window."""
    from trend_research import ledger
    chain = str(cfg.get("tr_chain_id") or "").strip()
    model = str(cfg.get("tr_model") or "").strip()
    pv = str(cfg.get("tr_prompt_version") or "tr-v1").strip()
    if not chain or not model or "|" in chain:
        return None
    first = None
    for row in st.iter(st.between(ledger.TABLE, chain + "|", chain + "|~")):
        if (row.get("chain_id") == chain and row.get("model") == model
                and row.get("prompt_version") == pv and row.get("status") == "ok"):
            d = _as_date(row.get("week_start"))
            if d is not None and (first is None or d < first):
                first = d
    return first


def backtest_scorecard(cfg, snapshots, *, start, end, benchmark_bars, store=None,
                       daily_bars=None, n_random: int = 1000, seed: int = 7) -> dict:
    """The scorecard of a finished TR backtest.

    ``snapshots``: the engine's NAV path (``{"timestamp", "value"}``).
    ``benchmark_bars(symbols, start, end) -> {symbol: [daily bar]}`` must return
    total-return (``adjustment=all``) bars; SPY and QQQ are asked for from
    ``BENCHMARK_LOOKBACK_DAYS`` before ``start``. Ledger rows come from the
    store. The random baselines' daily closes come from ``daily_bars(symbols,
    start, end)`` -- the lane's daily-bar loader (ruling R2:
    ``backtest_bars.fetch_daily_bars`` through the SwingDailyBars cache,
    fetching what the cache lacks) -- for every candidate through ``end``.
    Without a loader, or when it fails, the cache alone is read and a note
    says the baselines may be short."""
    from trend_research import market_tape
    if store is None:
        from db import store as st
    else:
        st = store
    cfg = cfg or {}
    nav = [(s.get("timestamp"), s.get("value")) for s in snapshots or () if isinstance(s, dict)]
    nav_days = _Series(nav).days
    start = _as_date(start) or (nav_days[0] if nav_days else None)
    end = _as_date(end) or (nav_days[-1] if nav_days else None)
    if start is None or end is None:
        return scorecard([], [], [], cutoff=None, candidate_weeks=[], n_random=n_random,
                         seed=seed)
    rows = _ledger_rows(cfg, start, end, st)
    plan = _week_plan(rows)
    symbols = sorted({s for w in plan for s in w["symbols"]})
    lo = start - timedelta(days=2 * _FRESH_DAYS)
    bars, closes_note = {}, None
    if symbols and daily_bars is not None:
        try:
            bars = daily_bars(symbols, lo, end) or {}
        except Exception as exc:              # never costs the scorecard
            closes_note = ("daily-bar loader failed (%s); the random baselines read the "
                           "cache only and may exclude names" % type(exc).__name__)
            bars = None
    elif symbols:
        closes_note = ("no daily-bar loader: the random baselines read the SwingDailyBars "
                       "cache only and may exclude names")
        bars = None
    if bars is None:
        bars = market_tape.cached_daily_bars(symbols, lo, end, store=st)
    closes = {s: _bars_to_closes(b) for s, b in bars.items()}
    try:
        chain_first = _chain_first_ok_week(cfg, st)
    except Exception:
        chain_first = None
    bench = benchmark_bars(["SPY", "QQQ"], start - timedelta(days=BENCHMARK_LOOKBACK_DAYS),
                           end) or {}
    cutoff = _as_date(cfg.get("research_model_cutoff")) if cfg.get(
        "research_model_cutoff") else None
    spy = _bars_to_closes(bench.get("SPY"))
    card = scorecard(nav, spy, _bars_to_closes(bench.get("QQQ")),
                     cutoff=cutoff, candidate_weeks=rows, n_random=n_random, seed=seed,
                     closes=closes, chain_first_week=chain_first)
    if closes_note:
        card["meta"]["notes"].append(closes_note)
    card["meta"].update({
        "chain_id": str(cfg.get("tr_chain_id") or "") or None,
        "model": str(cfg.get("tr_model") or "") or None,
        "prompt_version": str(cfg.get("tr_prompt_version") or "tr-v1"),
        "ledger_weeks": len(plan),
        "ledger_failed_weeks": sum(1 for r in rows if r.get("status") != "ok"),
        "random_names": len(symbols),
        "random_names_with_bars": sum(1 for s in symbols if closes.get(s)),
        "benchmark": "SPY / QQQ daily closes, adjustment=all (total return)",
    })
    card["meta"]["spy_sessions"] = len(spy)
    if not spy:
        card["meta"]["notes"].append("SPY benchmark bars unavailable: every comparison "
                                     "with SPY is untested")
    if cfg.get("research_model_cutoff") and cutoff is None:
        card["meta"]["notes"].append("research_model_cutoff %r is not a date: the honest "
                                     "period is untested" % (cfg.get("research_model_cutoff"),))
    return card
