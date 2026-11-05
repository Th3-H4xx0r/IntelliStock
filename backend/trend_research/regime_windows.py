"""Market-regime windows for the TR scorecard (spec C12, plan Task 10).

``REGIME_WINDOWS`` is a verbatim copy of the battery in
``scripts/outlier_engine_test.py``: ``(kind, tag, start, end)`` with ISO
dates, both ends inclusive. Only the ``bull`` / ``chop`` / ``bear`` kinds are
scored; ``handoff`` / ``year`` / ``multi`` are kept so the copy stays exact.

Dates after the battery's last window (``BATTERY_END``) are classified by a
rule declared here before any TR result existed:

* each session's label is SPY's 63-session return ending that session:
  above +8% is ``bull``, below -8% is ``bear``, anything else ``chop``
  (a session with fewer than 63 prior sessions gets no label);
* consecutive sessions with the same label form a run;
* a run shorter than 21 sessions is absorbed into the run before it (the
  first run into the one after), and same-kind neighbours then merge; this
  repeats until every run has at least 21 sessions;
* a lone run still under 21 sessions is not a window.
"""
from __future__ import annotations

from datetime import date

__all__ = ["REGIME_WINDOWS", "SCORED_KINDS", "BATTERY_END", "LOOKBACK_SESSIONS",
           "BULL_ABOVE", "BEAR_BELOW", "MIN_WINDOW_SESSIONS", "scored_windows",
           "classify_after"]

REGIME_WINDOWS = (
    ("bear", "rb1", "2022-01-01", "2022-06-30"), ("bear", "rb2", "2026-02-01", "2026-04-01"),
    ("bear", "rb3", "2025-02-15", "2025-04-15"),
    ("bull", "ru1", "2023-01-01", "2023-07-31"), ("bull", "ru2", "2026-04-01", "2026-06-01"),
    ("bull", "ru3", "2024-01-01", "2024-06-30"), ("bull", "p21bull", "2021-01-01", "2021-10-31"),
    ("bull", "nu1", "2023-10-25", "2024-03-28"), ("bull", "nu2", "2024-08-06", "2024-12-31"),
    ("chop", "rc1", "2025-11-10", "2026-02-24"), ("chop", "rc2", "2022-07-01", "2022-12-31"),
    ("chop", "rc3", "2024-07-01", "2024-10-31"), ("chop", "nc1", "2023-02-01", "2023-06-15"),
    ("chop", "nc2", "2024-03-15", "2024-08-30"),
    ("handoff", "h1", "2021-11-01", "2021-12-31"), ("handoff", "h2", "2023-08-01", "2023-10-31"),
    ("handoff", "h3", "2025-05-01", "2025-10-31"), ("handoff", "h4", "2024-11-01", "2025-02-14"),
    ("handoff", "h5", "2026-06-01", "2026-08-27"),
    ("year", "y22", "2022-01-01", "2022-12-31"), ("year", "y23", "2023-01-01", "2023-12-31"),
    ("year", "y24", "2024-01-01", "2024-12-31"), ("year", "y25", "2025-01-01", "2025-12-31"),
    ("multi", "ny2", "2023-01-01", "2024-12-31"), ("multi", "cyc", "2021-11-01", "2026-08-27"),
)

SCORED_KINDS = ("bull", "chop", "bear")
#: The last day any battery window covers; the rule applies after it.
BATTERY_END = max(date.fromisoformat(w[3]) for w in REGIME_WINDOWS)
LOOKBACK_SESSIONS = 63
BULL_ABOVE = 0.08
BEAR_BELOW = -0.08
MIN_WINDOW_SESSIONS = 21


def scored_windows() -> list:
    """The battery's bull / chop / bear windows, in battery order."""
    return [w for w in REGIME_WINDOWS if w[0] in SCORED_KINDS]


def _day(value) -> date:
    if isinstance(value, date):
        return value if type(value) is date else value.date()
    return date.fromisoformat(str(value)[:10])


def classify_after(spy_closes, start: date) -> list:
    """Windows ``(kind, "afterN", start_iso, end_iso)`` for sessions on or
    after ``start`` by the module rule. ``spy_closes`` is ``[(date, close)]``
    (any order; the last close of a repeated day wins) and should reach at
    least 63 sessions before ``start``."""
    by_day = {}
    for d, c in spy_closes or ():
        try:
            close = float(c)
        except (TypeError, ValueError):
            continue
        if close > 0:
            by_day[_day(d)] = close
    days = sorted(by_day)
    runs: list = []                           # [[kind, [days]]]
    for i, d in enumerate(days):
        if d < start or i < LOOKBACK_SESSIONS:
            continue
        r = by_day[d] / by_day[days[i - LOOKBACK_SESSIONS]] - 1
        kind = "bull" if r > BULL_ABOVE else "bear" if r < BEAR_BELOW else "chop"
        if runs and runs[-1][0] == kind:
            runs[-1][1].append(d)
        else:
            runs.append([kind, [d]])
    while len(runs) > 1:
        short = next((i for i, run in enumerate(runs) if len(run[1]) < MIN_WINDOW_SESSIONS),
                     None)
        if short is None:
            break
        if short == 0:
            runs[1][1][:0] = runs[0][1]
        else:
            runs[short - 1][1].extend(runs[short][1])
        del runs[short]
        merged: list = []
        for run in runs:
            if merged and merged[-1][0] == run[0]:
                merged[-1][1].extend(run[1])
            else:
                merged.append(run)
        runs = merged
    return [(kind, "after%d" % (n + 1), ds[0].isoformat(), ds[-1].isoformat())
            for n, (kind, ds) in enumerate(r for r in runs
                                           if len(r[1]) >= MIN_WINDOW_SESSIONS)]
