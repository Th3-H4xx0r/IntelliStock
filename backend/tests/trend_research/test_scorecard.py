"""TR scorecard: regime windows, the honest/optimistic split, the §3 pass bar
and the random baselines (plan Task 10, spec §3, §7.4, C8, C12).

No network: NAV, SPY-TR and QQQ series are synthetic; the backtest builder
reads its ledger and daily-bar cache from a FakeStore and gets the benchmark
bars from a stub loader.
"""
from __future__ import annotations

import ast
import os
import sys
from datetime import date, datetime, time, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db.fake import FakeStore  # noqa: E402
from swing_trader import backtest_bars, clock  # noqa: E402
from trend_research import ledger, regime_windows, scorecard as sc  # noqa: E402

_ROOT = os.path.dirname(_backend)


def sessions(lo: date, hi: date) -> list:
    return sorted(clock.trading_days(lo, hi))


def nav_at_close(days, values):
    """NAV points stamped 15:45 ET (a 900 s engine tick), several per day."""
    out = []
    for d, v in zip(days, values):
        for hh, mm, f in ((9, 30, 0.999), (12, 0, 0.9995), (15, 45, 1.0)):
            ts = datetime.combine(d, time(hh, mm), tzinfo=clock.NY).astimezone(timezone.utc)
            out.append((ts.replace(tzinfo=None), v * f))
    return out


def compound(days, growth):
    """Values starting at 100, multiplied by ``growth(day)`` on every day
    after the first."""
    vals, v = [], 100.0
    for i, d in enumerate(days):
        if i:
            v *= growth(d)
        vals.append(v)
    return vals


def run(nav_days, nav_growth, spy_growth, *, cutoff=None, lookback=150, **kw):
    """Scorecard over NAV on ``nav_days``; SPY starts ``lookback`` calendar
    days earlier (the classifier needs history)."""
    spy_days = sessions(nav_days[0] - timedelta(days=lookback), nav_days[-1])
    spy = list(zip(spy_days, compound(spy_days, spy_growth)))
    nav = nav_at_close(nav_days, compound(nav_days, nav_growth))
    return sc.scorecard(nav, spy, spy, cutoff=cutoff, candidate_weeks=kw.pop("weeks", []),
                        **kw)


# -- regime windows ------------------------------------------------------------------------


def test_regime_windows_are_the_battery_verbatim():
    tree = ast.parse(open(os.path.join(_ROOT, "scripts", "outlier_engine_test.py")).read())
    battery = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", None) == "REGIME_WINDOWS" for t in n.targets))
    assert [tuple(w) for w in battery] == list(regime_windows.REGIME_WINDOWS)
    assert regime_windows.BATTERY_END == date(2026, 8, 27)
    kinds = {w[0] for w in regime_windows.scored_windows()}
    assert kinds == {"bull", "chop", "bear"}


def _spy_path(start: date, legs):
    """SPY sessions from ``start`` with a constant daily drift per leg:
    ``legs = [(n_sessions, daily_ret), ...]``."""
    days = sessions(start, start + timedelta(days=sum(n for n, _ in legs) * 2))
    out, v, i = [], 100.0, 0
    for n, r in legs:
        for _ in range(n):
            v *= 1 + r
            out.append((days[i], v))
            i += 1
    return out


def test_classify_after_labels_rolling_63_session_return():
    # 100 calm sessions, 120 strongly rising, 120 strongly falling.
    spy = _spy_path(date(2026, 9, 1), [(100, 0.0), (120, 0.004), (120, -0.004)])
    start = spy[63][0]
    got = regime_windows.classify_after(spy, start)
    assert [w[0] for w in got] == ["chop", "bull", "bear"]
    assert all(w[1].startswith("after") for w in got)
    assert date.fromisoformat(got[0][2]) == start           # nothing before `start`
    # 63-session return crosses +8% at 1.004^k > 1.08 -> k = 20 rising sessions in.
    bull_first = date.fromisoformat(got[1][2])
    assert bull_first == spy[100 + 19][0]
    for w in got:
        n = len([d for d, _ in spy if w[2] <= d.isoformat() <= w[3]])
        assert n >= regime_windows.MIN_WINDOW_SESSIONS


def test_classify_after_absorbs_short_runs():
    # A spike and reversal inside a calm stretch: a 3-session bull label
    # (+8.2%..+10.4%) and, 63 sessions later, a 1-session bear label (-9.6%).
    # Both are shorter than 21 sessions, so the whole stretch is one chop window.
    spy = _spy_path(date(2026, 9, 1), [(100, 0.0), (5, 0.02), (5, -0.02), (120, 0.0)])
    got = regime_windows.classify_after(spy, spy[63][0])
    assert got == [("chop", "after1", spy[63][0].isoformat(), spy[-1][0].isoformat())]


def test_classify_after_too_short_is_empty():
    spy = _spy_path(date(2026, 9, 1), [(70, 0.001)])
    assert regime_windows.classify_after(spy, spy[63][0]) == []
    assert regime_windows.classify_after([], date(2026, 9, 1)) == []


# -- the split ---------------------------------------------------------------------------


def test_split_at_cutoff():
    days = sessions(date(2023, 1, 3), date(2024, 12, 31))
    cutoff = date(2024, 1, 1)
    card = run(days, lambda d: 1.001, lambda d: 1.0004, cutoff=cutoff)
    opt, hon, overall = card["optimistic"], card["honest"], card["overall"]
    assert opt["end"] < cutoff.isoformat() <= hon["start"]
    assert opt["start"] == overall["start"] and hon["end"] == overall["end"]
    # The honest period is measured from the last close before the cutoff.
    assert (1 + opt["ret"] / 100) * (1 + hon["ret"] / 100) == pytest.approx(
        1 + overall["ret"] / 100, rel=1e-6)
    assert (1 + opt["spy_ret"] / 100) * (1 + hon["spy_ret"] / 100) == pytest.approx(
        1 + overall["spy_ret"] / 100, rel=1e-6)
    # Windows: only those wholly inside each period.
    # (ru3 is declared from 2024-01-01, a holiday: its first session is the
    # honest period's first, 2024-01-02.)
    assert all(w["start"] >= cutoff.isoformat() for w in hon["windows"])
    assert "ru3" in {w["tag"] for w in hon["windows"]}
    assert all(w["end"] <= opt["end"] for w in opt["windows"])
    tags = {w["tag"] for w in overall["windows"]}
    assert "nu1" in tags                            # 2023-10-25 -> 2024-03-28 straddles
    assert "nu1" not in {w["tag"] for w in opt["windows"] + hon["windows"]}
    assert card["meta"]["cutoff"] == "2024-01-01"


def test_no_cutoff_everything_optimistic_honest_untested():
    days = sessions(date(2023, 1, 3), date(2023, 12, 29))
    card = run(days, lambda d: 1.001, lambda d: 1.0004, cutoff=None)
    assert card["honest"]["untested"] is True
    assert card["honest"]["pass_all_regimes"] == "untested"
    assert card["honest"]["pass_plus5"] == "untested"
    assert card["honest"]["pass"] == "untested"
    assert card["honest"]["cagr"] is None and card["honest"]["windows"] == []
    assert card["optimistic"]["ret"] == card["overall"]["ret"]
    assert card["optimistic"]["windows"] == card["overall"]["windows"]


def test_untested_when_no_bear_window():
    days = sessions(date(2023, 1, 3), date(2023, 12, 29))
    card = run(days, lambda d: 1.001, lambda d: 1.0004, cutoff=date(2023, 1, 1))
    hon = card["honest"]
    kinds = {w["kind"] for w in hon["windows"]}
    assert kinds == {"bull", "chop"}                       # ru1, nc1
    assert all(w["beat"] for w in hon["windows"])
    assert hon["regimes"] == {"bull": True, "chop": True, "bear": "untested"}
    assert hon["pass_all_regimes"] == "untested"
    assert hon["pass"] == "untested"
    assert card["optimistic"]["untested"] is True          # cutoff before the run


def test_pass_requires_every_window():
    days = sessions(date(2022, 1, 3), date(2023, 12, 29))
    rc2 = (date(2022, 7, 1), date(2022, 12, 31))          # the chop window

    def strat(d):
        return 1.0 if rc2[0] <= d <= rc2[1] else 1.002

    card = run(days, strat, lambda d: 1.0005, cutoff=date(2022, 1, 1))
    hon = card["honest"]
    by_tag = {w["tag"]: w for w in hon["windows"]}
    assert {"rb1", "rc2", "ru1", "nc1"} <= set(by_tag)
    assert by_tag["rc2"]["beat"] is False
    assert all(w["beat"] for t, w in by_tag.items() if t != "rc2")
    assert hon["regimes"] == {"bull": True, "chop": False, "bear": True}
    assert hon["pass_all_regimes"] is False
    assert hon["pass"] is False

    everywhere = run(days, lambda d: 1.002, lambda d: 1.0005, cutoff=date(2022, 1, 1))
    assert everywhere["honest"]["pass_all_regimes"] is True


def test_failed_window_beats_untested():
    """One losing window is a fail even when a kind is missing."""
    days = sessions(date(2023, 1, 3), date(2023, 12, 29))
    card = run(days, lambda d: 1.0, lambda d: 1.0005, cutoff=date(2023, 1, 1))
    assert card["honest"]["pass_all_regimes"] is False
    assert card["honest"]["regimes"]["bear"] == "untested"


def test_plus5_is_percentage_points():
    # `lo` (a Monday session) is the close before the run; the run opens on
    # Tuesday at that close, like the engine's first snapshot.
    lo = date(2010, 1, 4)
    days = [lo + timedelta(days=i) for i in range(4 * 365 + 1)]

    def card(strategy_cagr, spy_cagr):
        opening = datetime.combine(days[1], time(14, 30))
        nav = [(opening, 100.0)] + [(datetime.combine(d, time(20, 0)), 100 * (1 + strategy_cagr) **
                                     ((d - lo).days / 365.25)) for d in days[1:]]
        spy = [(d, 100 * (1 + spy_cagr) ** ((d - lo).days / 365.25)) for d in days]
        return sc.scorecard(nav, spy, spy, cutoff=None, candidate_weeks=[])["overall"]

    low = card(0.200, 0.152)
    assert low["cagr"] == pytest.approx(20.0, abs=1e-6)
    assert low["spy_cagr"] == pytest.approx(15.2, abs=1e-6)
    assert low["excess_pp"] == pytest.approx(4.8, abs=1e-6)
    assert low["pass_plus5"] is False
    high = card(0.203, 0.152)
    assert high["excess_pp"] == pytest.approx(5.1, abs=1e-6)
    assert high["pass_plus5"] is True
    assert high["windows"] == []                       # 2010: no battery window
    assert high["pass_all_regimes"] == "untested"
    assert high["pass"] == "untested"


def test_max_drawdown_and_rolling_beat():
    days = sessions(date(2022, 1, 3), date(2023, 12, 29))
    dip = (date(2022, 3, 1), date(2022, 3, 31))
    card = run(days, lambda d: 0.99 if dip[0] <= d <= dip[1] else 1.001,
               lambda d: 1.0003, cutoff=None)["overall"]
    n = len(sessions(*dip))
    assert card["max_dd"] == pytest.approx(100 * (0.99 ** n - 1), abs=1e-3)
    assert card["spy_max_dd"] == pytest.approx(0.0, abs=1e-9)
    assert 0.0 <= card["roll12_beat_pct"] <= 100.0
    assert card["qqq_cagr"] == pytest.approx(card["spy_cagr"])


# -- random baselines ----------------------------------------------------------------------


NAMES = [f"N{i:02d}" for i in range(20)]


def _closes(days):
    """Name k drifts by (k - 5) bps a session: N19 is the best."""
    return {s: [(d, 100 * (1 + (k - 5) / 10000) ** i) for i, d in enumerate(days)]
            for k, s in enumerate(NAMES)}


def _weeks(days, per_week=8, n_holdings=3):
    mondays = sorted({clock.week_monday(d) for d in days})
    out = []
    for j, m in enumerate(mondays):
        cands = [NAMES[(j + i) % len(NAMES)] for i in range(per_week)]
        out.append({"week_start": m.isoformat(), "status": "ok",
                    "candidates": [{"symbol": s, "sources": ["candidates"]} for s in cands],
                    "recommended": [{"symbol": s} for s in cands[:n_holdings]]})
    return out


def test_random_baseline_deterministic_with_seed():
    days = sessions(date(2025, 1, 6), date(2025, 6, 27))
    closes = _closes(days)
    weeks = _weeks(days)
    nav = nav_at_close(days, [100 * 1.0005 ** i for i in range(len(days))])
    spy = [(d, 100.0) for d in sessions(date(2024, 9, 1), days[-1])]

    def card(seed):
        return sc.scorecard(nav, spy, spy, cutoff=date(2025, 4, 1), candidate_weeks=weeks,
                            closes=closes, n_random=200, seed=seed)

    a, b = card(7), card(7)
    for seg in ("overall", "optimistic", "honest"):
        assert a[seg]["random_pctile_candidates"] == b[seg]["random_pctile_candidates"]
        assert a[seg]["random_pctile_universe"] == b[seg]["random_pctile_universe"]
        assert 0.0 <= a[seg]["random_pctile_candidates"] <= 100.0
        assert 0.0 <= a[seg]["random_pctile_universe"] <= 100.0
    assert a["meta"]["seed"] == 7 and a["meta"]["n_random"] == 200


def test_random_baseline_ranks_the_best_picker_top():
    days = sessions(date(2025, 1, 6), date(2025, 6, 27))
    closes = _closes(days)
    weeks = _weeks(days)
    # The strategy compounds the best candidate every week: better than every draw.
    best = 1 + 14 / 10000 + 0.0005
    nav = nav_at_close(days, [100 * best ** i for i in range(len(days))])
    spy = [(d, 100.0) for d in sessions(date(2024, 9, 1), days[-1])]
    card = sc.scorecard(nav, spy, spy, cutoff=None, candidate_weeks=weeks, closes=closes,
                        n_random=100)
    assert card["overall"]["random_pctile_candidates"] == 100.0
    assert card["overall"]["random_pctile_universe"] == 100.0
    # A flat strategy loses to most draws from a mostly rising universe.
    flat = nav_at_close(days, [100.0] * len(days))
    worse = sc.scorecard(flat, spy, spy, cutoff=None, candidate_weeks=weeks, closes=closes,
                         n_random=100)["overall"]
    assert worse["random_pctile_universe"] < 50.0
    assert worse["random_pctile_candidates"] < 50.0


def test_random_baseline_untested_without_closes_or_weeks():
    days = sessions(date(2025, 1, 6), date(2025, 3, 28))
    nav = nav_at_close(days, [100.0] * len(days))
    spy = [(d, 100.0) for d in days]
    card = sc.scorecard(nav, spy, spy, cutoff=None, candidate_weeks=_weeks(days))
    assert card["overall"]["random_pctile_candidates"] is None
    card = sc.scorecard(nav, spy, spy, cutoff=None, candidate_weeks=[], closes=_closes(days))
    assert card["overall"]["random_pctile_universe"] is None


def test_failed_weeks_are_skipped_and_n_follows_holdings():
    days = sessions(date(2025, 1, 6), date(2025, 3, 28))
    weeks = _weeks(days)
    weeks[2] = dict(weeks[2], status="failed")
    plan = sc._week_plan(weeks)
    assert weeks[2]["week_start"] not in [p["week_start"] for p in plan]
    assert all(p["n"] == 3 for p in plan)
    assert sc._week_plan([{"week_start": "2025-01-06", "status": "ok",
                           "candidates": ["aaa", "BBB"]}])[0]["n"] == 10
    # A row without a status is not an ok row.
    assert sc._week_plan([{"week_start": "2025-01-06", "candidates": ["AAA"]}]) == []


def test_universe_pool_is_cumulative():
    plan = sc._week_plan([
        {"week_start": "2025-01-13", "status": "ok", "candidates": ["CCC", "AAA"]},
        {"week_start": "2025-01-06", "status": "ok", "candidates": ["AAA", "BBB"]},
        {"week_start": "2025-01-20", "status": "ok", "candidates": ["DDD"]}])
    assert [w["seen"] for w in plan] == [["AAA", "BBB"], ["AAA", "BBB", "CCC"],
                                         ["AAA", "BBB", "CCC", "DDD"]]


def test_universe_never_draws_a_name_before_it_is_named():
    """CCC soars in weeks 1-2 but is first named in week 3: a flat strategy
    over flat AAA/BBB ties every draw (pctile 50) only if CCC is never drawn
    early."""
    days = sessions(date(2024, 12, 23), date(2025, 1, 31))
    mondays = [date(2025, 1, 6), date(2025, 1, 13), date(2025, 1, 20), date(2025, 1, 27)]
    soar_end = date(2025, 1, 17)
    closes = {"AAA": [(d, 100.0) for d in days], "BBB": [(d, 100.0) for d in days],
              "CCC": [(d, 100.0 * (1.05 ** len([x for x in days if date(2025, 1, 6) <= x <= min(d, soar_end)])))
                      for d in days]}
    weeks = [{"week_start": m.isoformat(), "status": "ok", "n_holdings": 1,
              "candidates": ["AAA", "BBB"] + (["CCC"] if i >= 2 else [])}
             for i, m in enumerate(mondays)]
    run_days = [d for d in days if d >= mondays[0]]
    nav = nav_at_close(run_days, [100.0] * len(run_days))
    nav = [(t, 100.0) for t, _ in nav]
    spy = [(d, 100.0) for d in days]
    card = sc.scorecard(nav, spy, spy, cutoff=None, candidate_weeks=weeks, closes=closes,
                        n_random=200)["overall"]
    assert card["random_pctile_universe"] == 50.0
    assert card["random_pctile_candidates"] == 50.0


def test_day_one_move_counts_for_strategy_and_spy_alike():
    """Review reproduction: NAV and SPY both +5% on the run's first day, flat
    after. Both are measured from the close before the first session."""
    days = sessions(date(2023, 1, 3), date(2023, 12, 29))
    opening = datetime.combine(days[0], time(14, 30))          # 09:30 ET
    nav = [(opening, 100.0)] + [(datetime.combine(d, time(20, 45)), 105.0) for d in days]
    spy = [(date(2022, 12, 30), 100.0)] + [(d, 105.0) for d in days]
    # The one candidate moves exactly like the NAV: every draw ties.
    closes = {"AAA": list(spy)}
    weeks = [{"week_start": m.isoformat(), "status": "ok", "n_holdings": 1,
              "candidates": ["AAA"]} for m in sorted({clock.week_monday(d) for d in days})]
    card = sc.scorecard(nav, spy, spy, cutoff=None, candidate_weeks=weeks, closes=closes,
                        n_random=50)["overall"]
    assert card["ret"] == pytest.approx(5.0) and card["spy_ret"] == pytest.approx(5.0)
    assert card["excess_pp"] == pytest.approx(0.0, abs=1e-6)
    ru1 = next(w for w in card["windows"] if w["tag"] == "ru1")      # starts on day 1
    assert ru1["ret"] == pytest.approx(5.0) and ru1["spy_ret"] == pytest.approx(5.0)
    assert card["random_pctile_candidates"] == 50.0
    assert card["start"] == "2023-01-03"                    # the opening point is not a session


def test_window_without_spy_data_is_untested_not_dropped():
    windows = [{"tag": "ru1", "kind": "bull", "beat": True},
               {"tag": "nu1", "kind": "bull", "beat": None},
               {"tag": "nc1", "kind": "chop", "beat": True},
               {"tag": "rb1", "kind": "bear", "beat": True}]
    regimes, verdict = sc._regimes(windows)
    assert regimes == {"bull": "untested", "chop": True, "bear": True}
    assert verdict == "untested"
    regimes, verdict = sc._regimes(windows + [{"tag": "x", "kind": "bull", "beat": False}])
    assert regimes["bull"] is False and verdict is False


def test_cagr_overflow_is_none():
    assert sc._cagr(7.0, 1) is None
    assert sc._cagr(-1.0, 100) is None
    assert sc._cagr(0.1, 0) is None


def test_empty_nav_is_all_untested():
    card = sc.scorecard([], [], [], cutoff=None, candidate_weeks=[])
    for seg in ("overall", "optimistic", "honest"):
        assert card[seg]["untested"] is True
        assert card[seg]["pass"] == "untested"


# -- the backtest builder --------------------------------------------------------------------


CHAIN, MODEL, PV = "honest", "claude-test", "tr-v1"


def _ledger_row(st, monday, cands, recommended):
    as_of = ledger.week_as_of(monday).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"id": ledger.week_id(CHAIN, monday, MODEL, PV), "chain_id": CHAIN,
           "week_start": monday.isoformat(), "as_of": as_of, "available_at": as_of,
           "model": MODEL, "provider": "claude-cli", "prompt_version": PV, "status": "ok",
           "candidates": [{"symbol": s, "sources": ["candidates"]} for s in cands],
           "recommended": [{"symbol": s} for s in recommended], "book": {"picks": []}}
    ledger.put_week(row, store=st)


def test_backtest_scorecard_reads_ledger_and_bar_cache():
    st = FakeStore()
    days = sessions(date(2025, 1, 6), date(2025, 3, 28))
    mondays = sorted({clock.week_monday(d) for d in days})
    for m in mondays:
        _ledger_row(st, m, NAMES[:6], NAMES[:2])
    closes = _closes(days)
    st.insert(backtest_bars.CACHE_TABLE, [
        {"id": f"{s}|2025", "adjustment": backtest_bars.ADJUSTMENT, "feed": "iex",
         "bars": [{"t": f"{d.isoformat()}T05:00:00Z", "c": c} for d, c in closes[s]]}
        for s in NAMES[:6]], conflict="replace")
    seen = {}

    def bench(symbols, start, end):
        seen["args"] = (tuple(symbols), start, end)
        sp = sessions(start, end)
        return {s: [{"t": f"{d.isoformat()}T05:00:00Z", "c": 100 + i * 0.01}
                    for i, d in enumerate(sp)] for s in symbols}

    snaps = [{"timestamp": ts, "value": v}
             for ts, v in nav_at_close(days, [100 * 1.0005 ** i for i in range(len(days))])]
    cfg = {"tr_chain_id": CHAIN, "tr_model": MODEL, "tr_prompt_version": PV,
           "research_model_cutoff": "2025-02-15"}
    card = sc.backtest_scorecard(cfg, snaps, start=days[0], end=days[-1], benchmark_bars=bench,
                                 store=st, n_random=50)
    assert seen["args"][0] == ("SPY", "QQQ")
    assert seen["args"][1] <= days[0] - timedelta(days=90)
    assert card["meta"]["ledger_weeks"] == len(mondays)
    assert card["meta"]["cutoff"] == "2025-02-15"
    assert card["overall"]["random_pctile_candidates"] is not None
    assert card["honest"]["start"] >= "2025-02-15"
    assert any("dividend_yields" in n for n in card["meta"]["notes"])


def test_backtest_scorecard_bad_cutoff_is_untested():
    st = FakeStore()
    days = sessions(date(2025, 1, 6), date(2025, 2, 28))
    snaps = [{"timestamp": ts, "value": v} for ts, v in nav_at_close(days, [100.0] * len(days))]
    card = sc.backtest_scorecard({"tr_chain_id": CHAIN, "tr_model": MODEL,
                                  "research_model_cutoff": "soon"}, snaps,
                                 start=days[0], end=days[-1],
                                 benchmark_bars=lambda s, a, b: {}, store=st, n_random=10)
    assert card["honest"]["untested"] is True
    assert card["meta"]["cutoff"] is None
    assert card["meta"]["spy_sessions"] == 0
    assert any("SPY benchmark bars unavailable" in n for n in card["meta"]["notes"])
    assert card["overall"]["pass_plus5"] == "untested"


# -- broker wiring ---------------------------------------------------------------------------


_BROKER = os.path.join(_backend, "broker.py")


def _broker_fn(name, ns_extra=None):
    tree = ast.parse(open(_BROKER).read())
    keep = {name, "_truthy", "_merged_strategy_settings"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in keep]
    assert {n.name for n in nodes} == keep
    logs = []
    ns = {"_log": lambda msg, color="white": logs.append((str(msg), color))}
    ns.update(ns_extra or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), _BROKER, "exec"), ns)
    return ns[name], logs


SPEC = {"strategy": "strategy_trend_research",
        "config": {"strategy_trend_research_enabled": True, "tr_chain_id": CHAIN,
                   "tr_model": MODEL, "research_model_cutoff": "2025-02-15"}}


def test_broker_scorecard_only_for_an_enabled_lane(monkeypatch):
    fn, _ = _broker_fn("_trend_research_scorecard")
    called = []
    monkeypatch.setattr(sc, "backtest_scorecard",
                        lambda cfg, snaps, **kw: called.append((cfg, kw)) or {"overall": {}})
    kw = dict(start=date(2025, 1, 6), end=date(2025, 2, 28), fetch_bars=None, key="k",
              secret="s", feed="iex")
    off = {**SPEC, "config": dict(SPEC["config"], strategy_trend_research_enabled=False)}
    assert fn([off], [], **kw) is None
    assert fn([{"strategy": "strategy_eb", "config": {}}], [], **kw) is None
    for junk in (None, [], [None]):
        assert fn(junk, [], **kw) is None
    assert called == []
    assert fn([SPEC], [{"timestamp": "x", "value": 1}], **kw) == {"overall": {}}
    cfg, got_kw = called[0]
    assert cfg["research_model_cutoff"] == "2025-02-15"
    assert cfg["tr_prompt_version"] == "tr-v1"            # lane DEFAULTS merged


def test_broker_scorecard_benchmark_fetch_is_total_return(monkeypatch):
    fn, _ = _broker_fn("_trend_research_scorecard")
    fetched = {}

    def fetch_bars(symbols, start, end, **kw):
        fetched.update(kw, symbols=symbols, start=start, end=end)
        return {}

    def fake(cfg, snaps, *, benchmark_bars, **kw):
        benchmark_bars(["SPY", "QQQ"], date(2025, 1, 1), date(2025, 2, 1))
        return {}

    monkeypatch.setattr(sc, "backtest_scorecard", fake)
    fn([SPEC], [], start=date(2025, 1, 6), end=date(2025, 2, 28), fetch_bars=fetch_bars,
       key="k", secret="s", feed="iex")
    assert fetched["adjustment"] == "all" and fetched["timeframe"] == "1Day"
    assert fetched["key"] == "k" and fetched["secret"] == "s" and fetched["feed"] == "iex"
    # broker.fetch_alpaca_historical_bars reads a bare date as "no range".
    assert fetched["start"] == datetime(2025, 1, 1, 0, 0, 0)
    assert fetched["end"] == datetime(2025, 2, 1, 23, 59, 59)


def test_broker_scorecard_failure_logs_and_returns_error(monkeypatch):
    fn, logs = _broker_fn("_trend_research_scorecard")

    def boom(*a, **k):
        raise RuntimeError("no bars")

    monkeypatch.setattr(sc, "backtest_scorecard", boom)
    got = fn([SPEC], [], start=date(2025, 1, 6), end=date(2025, 2, 28), fetch_bars=None,
             key="k", secret="s", feed="iex")
    assert got == {"error": "RuntimeError: no bars"}
    assert any("scorecard failed" in m and c == "red" for m, c in logs)


def test_result_site_wired():
    tree = ast.parse(open(_BROKER).read())
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)
            and n.name == "_trend_research_scorecard"]
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "_trend_research_scorecard"]
    stores = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                      and t.value.id == "backtest_result"
                      and isinstance(t.slice, ast.Constant) and t.slice.value == "tr_scorecard"
                      for t in n.targets)]
    assert len(defs) == 1 and len(calls) == 1 and len(stores) == 1
    kw = {k.arg for k in calls[0].keywords}
    assert {"start", "end", "fetch_bars", "key", "secret", "feed"} <= kw


def test_fingerprint_lists_include_scorecard_files():
    main_src = open(os.path.join(_backend, "api", "main.py")).read()
    check_src = open(os.path.join(_ROOT, "scripts", "check_deployed_code.py")).read()
    for f in ("trend_research/scorecard.py", "trend_research/regime_windows.py"):
        assert f'"{f}"' in main_src
        assert f'"backend/{f}"' in check_src
