#!/usr/bin/env python3
"""Evaluate Strategy EB's PRE-REGISTERED acceptance gate on a finished backtest.

    python3 scripts/strategy_eb_gate.py 812345
    python3 scripts/strategy_eb_gate.py 812345 --source pg
    python3 scripts/strategy_eb_gate.py --self-test

The gate was frozen in
docs/superpowers/specs/2026-08-27-strategy-eb-design.md section 11 BEFORE any
engine run. It is not re-tuned to pass. If it fails, the strategy ships disabled
with the numbers recorded in DEFAULTS comments, per the XS precedent.

Exit code 0 = all six pass. 1 = any failure. 2 = the run could not be read.

Data sources
------------
`--source api` (default), the serving truth post-Postgres-cutover:

    GET /backtests/{id}/graph-data  -> portfolio_value_history + backtest_trades
    GET /backtests/{id}/logs        -> the two silent-failure greps

`--source pg` reads the BacktestSteps rows directly, read-only, for the case
the API caps bite: a RUNNING run's trades are tailed at 1,000 and its pv is
downsampled to 3,000, and a log array that did not survive to a file on the
shared volume is the last 500 lines only. A FINISHED run's step rows are
final=true and uncapped, so for the run this gate is meant for the two sources
agree.

The engine persists no `final_value` and no `max_drawdown`, so both are
reconstructed from the `pv` snapshots; `pnl_percent` is persisted but says
nothing about the path, and the path is what G2, G3 and G4 ask about. The SPY
benchmark comes from the `prices` dict inside those same snapshots rather than
from a separate download that could disagree about adjustment.

Those prices are the engine's TRADED prices, which are split-adjusted but carry
no dividend. SPY has paid ~1.25%/yr over this window, and holding the strategy
to a price-only SPY would hand it a free 1.25pp of CAGR and roughly six free
rolling windows a year. So the benchmark is accrued to total return in
`_accrue` before anything is measured against it -- the same 1.0125^years the
rest of the program's SPY-TR figures use.

Beyond the six frozen conditions the report also prints rolling 3-, 6- and
12-month win rates. Only the 12-month one is G4; the other two are diagnostic,
because a strategy that wins on the year but loses two quarters in three is a
different animal from one that wins steadily, and the gate alone cannot tell
them apart.
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: Frozen by spec section 11. Editing any of these is re-tuning the gate.
CAGR_MARGIN_PP = 4.0             # G1: EB CAGR >= SPY CAGR + this
DRAWDOWN_MULTIPLE = 1.2          # G2: |EB maxDD| <= this * |SPY maxDD|
YEAR_2022_TOLERANCE_PP = 12.0    # G3: EB 2022 >= SPY 2022 - this
MIN_ROLLING_WIN_RATE_PCT = 60.0  # G4
MAX_TURNOVER_PCT_PER_YEAR = 400.0  # G5
BENCHMARK = "SPY"
#: G4's window, in months. The other spans in ROLLING_SPANS are reported, never
#: gated: widening the gate to "3 of 3 spans" would be re-tuning it.
GATE_ROLLING_MONTHS = 12

#: Reported win-rate spans, in months. 12 is G4.
ROLLING_SPANS = (3, 6, 12)

#: SPY's trailing distribution yield over the gate window, accrued
#: continuously onto the engine's price-only SPY series so the benchmark is
#: total return. Not a free parameter: it is the same constant behind every
#: SPY-TR figure in the EB research, and lowering it flatters the strategy.
BENCHMARK_DIVIDEND_YIELD = 0.0125

#: The exact strings the two silent failures print. broker.py:17765 emits the
#: ghost-sell observation, broker.py:17431 the cap trim.
GHOST_SELL_MARKER = "would_block_in_phase2=True"
CAP_TRIM_MARKER = "Broker single-position cap:"

#: backtest_result_store caps a RUNNING run's trades at 1,000. A daily
#: 2021-11..2026-08 run is ~1,200 pv rows and well under 1,000 trades, so this
#: should not bind -- but at exactly the cap, turnover is a lower bound.
TRADE_TAIL_CAP = 1000

Check = namedtuple("Check", "name description passed")


class GateError(ValueError):
    """The run cannot be measured. Distinct from the gate failing."""


class GateResult:
    def __init__(self, checks, metrics, warnings):
        self.checks = list(checks)
        self.metrics = dict(metrics)
        self.warnings = list(warnings)

    @property
    def passed(self):
        return all(check.passed for check in self.checks)

    @property
    def exit_code(self):
        return 0 if self.passed else 1


# ------------------------------------------------------------------ maths
def _ts(value):
    """A timestamp as a naive UTC datetime. The pv array mixes `Z`, explicit
    offsets and naive strings across engine versions, and subtracting an aware
    from a naive one raises."""
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise GateError("unparseable timestamp %r" % (value,)) from exc
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(timezone.utc).replace(tzinfo=None)
    return stamp


def _cagr(first, last, years):
    if first <= 0 or last <= 0 or years <= 0:
        return float("nan")
    return ((last / first) ** (1.0 / years) - 1.0) * 100.0


def _maxdd(series):
    """The worst peak-to-trough, as a NEGATIVE percentage."""
    peak, worst = -math.inf, 0.0
    for value in series:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, value / peak - 1.0)
    return worst * 100.0


def _monthly_last(points):
    """The LAST observation in each calendar month, in calendar order.

    Month-end sampling, not row sampling: a run whose granularity puts twenty
    snapshots in one month must still contribute one rolling window, or G4's
    denominator is a function of the bar size rather than of the strategy.
    """
    out = {}
    for stamp, *values in points:
        out[(stamp.year, stamp.month)] = values
    return [out[key] for key in sorted(out)]


def _calendar_year_return(points, year):
    """The calendar-year return, in percent, or None if `year` is not covered.

    The base is the LAST observation strictly before January 1st, which is what
    "the 2022 return" means everywhere else in this program -- SPY's published
    -18.2% is measured from the 2021 close, not from its first January print.
    A run that starts inside the year has no such base and falls back to its
    first observation inside it, which understates a January selloff; that
    fallback is applied identically to EB and to SPY, so the two stay
    comparable even when the number itself is a little short.
    """
    before = [value for stamp, value in points if stamp.year < year]
    inside = [value for stamp, value in points if stamp.year == year]
    if not inside:
        return None
    base = before[-1] if before else (inside[0] if len(inside) > 1 else None)
    if base is None or base <= 0:
        return None
    return (inside[-1] / base - 1.0) * 100.0


def _accrue(benchmark, yield_per_year=BENCHMARK_DIVIDEND_YIELD):
    """The price-only benchmark as a TOTAL-RETURN series.

    The engine stores traded prices: split-adjusted, dividend-free. Comparing a
    strategy's NAV -- which banks every distribution it receives -- against a
    price-only SPY is a systematic gift of the yield, once to G1's margin and
    again to every rolling window. Accrual is continuous in elapsed time rather
    than per row, so a gap in the snapshots cannot skip a quarter of it.
    """
    if not benchmark:
        return []
    start = benchmark[0][0]
    return [(stamp,
             price * (1.0 + yield_per_year) ** ((stamp - start).days / 365.25))
            for stamp, price in benchmark]


def _rolling_win_rate(months, span):
    """(wins, windows, percent) over `span`-month windows of month-end pairs.

    `months` is the output of `_monthly_last`: [[eb, spy], ...] in calendar
    order. A window is a WIN when EB's total return over it strictly exceeds
    the benchmark's; ties go to the benchmark, which is the conservative
    direction for a gate.
    """
    wins = total = 0
    for i in range(len(months) - span):
        (eb_open, spy_open), (eb_close, spy_close) = months[i], months[i + span]
        if eb_open <= 0 or spy_open <= 0:
            continue
        total += 1
        wins += (eb_close / eb_open) > (spy_close / spy_open)
    return wins, total, (100.0 * wins / total if total else 0.0)


# ------------------------------------------------------------------ the gate
def evaluate(pv, trades, log_lines, *, logs_complete=True):
    """Pure: the six conditions from spec section 11 over one run's rows.

    `pv` are portfolio_value_history snapshots, `trades` backtest_trades rows,
    `log_lines` the run's log. Raises GateError when the run cannot be
    measured at all; returns a GateResult otherwise, failing loudly rather
    than skipping a condition it could not compute.
    """
    rows = sorted(((_ts(row["timestamp"]), row) for row in (pv or [])),
                  key=lambda pair: pair[0])
    if len(rows) < 2:
        raise GateError("no portfolio value history: %d snapshot(s)" % len(rows))

    stamps = [stamp for stamp, _ in rows]
    equity = [float(row.get("value") or 0.0) for _, row in rows]
    years = (stamps[-1] - stamps[0]).days / 365.25

    benchmark = [(stamp, float((row.get("prices") or {}).get(BENCHMARK) or 0.0))
                 for stamp, row in rows]
    benchmark = [(stamp, price) for stamp, price in benchmark if price > 0]
    if len(benchmark) < 2:
        raise GateError(
            "the run carries no %s price series; the benchmark must come from "
            "the run's own prices, not a separate download" % BENCHMARK)
    # Total return, not price return. See _accrue.
    benchmark = _accrue(benchmark)
    spy_years = (benchmark[-1][0] - benchmark[0][0]).days / 365.25

    warnings = []
    eb_cagr = _cagr(equity[0], equity[-1], years)
    spy_cagr = _cagr(benchmark[0][1], benchmark[-1][1], spy_years)
    eb_dd = _maxdd(equity)
    spy_dd = _maxdd([price for _, price in benchmark])

    eb_2022 = _calendar_year_return(list(zip(stamps, equity)), 2022)
    spy_2022 = _calendar_year_return(benchmark, 2022)

    # G4: rolling 12-month windows, on the month-end observations of BOTH
    # series taken from the same rows, so the two can never drift apart by an
    # index. 3 and 6 months are computed the same way and reported alongside,
    # but only the 12-month rate is a condition.
    priced = {stamp: price for stamp, price in benchmark}
    paired = [(stamp, value, priced[stamp])
              for stamp, value in zip(stamps, equity) if stamp in priced]
    months = _monthly_last(paired)
    rolling = {span: _rolling_win_rate(months, span) for span in ROLLING_SPANS}
    if GATE_ROLLING_MONTHS not in rolling:
        rolling[GATE_ROLLING_MONTHS] = _rolling_win_rate(
            months, GATE_ROLLING_MONTHS)
    _, total, win_rate = rolling[GATE_ROLLING_MONTHS]
    if total == 0:
        warnings.append("no complete %d-month window in this run; G4 has no "
                        "observations and fails by default"
                        % GATE_ROLLING_MONTHS)

    # G5: turnover against mean equity, both legs summed -- the definition the
    # research harness used (scripts/strategy_xs_matrix.py:152), which is what
    # the 400%/yr bound was calibrated against.
    trades = list(trades or [])
    if len(trades) >= TRADE_TAIL_CAP:
        warnings.append(
            "%d trade rows == the %d tail cap: turnover is a LOWER BOUND. "
            "Re-read this run with --source pg before trusting G5."
            % (len(trades), TRADE_TAIL_CAP))
    traded = sum(abs(float(trade.get("total") or 0.0)) for trade in trades)
    mean_equity = sum(equity) / len(equity)
    turnover = (traded / mean_equity / max(years, 1e-9) * 100.0
                if mean_equity > 0 else float("inf"))

    # G6: the two silent failures that burned XS.
    text = "\n".join(str(line) for line in (log_lines or []))
    ghost = text.count(GHOST_SELL_MARKER)
    capped = text.count(CAP_TRIM_MARKER)
    if not logs_complete:
        warnings.append("the log came back truncated, so G6's zero is a lower "
                        "bound over the visible lines only. Re-read with "
                        "--source pg to count the whole run.")

    checks = [
        Check("G1", "CAGR %.2f%% >= SPY %.2f%% + %.0fpp"
              % (eb_cagr, spy_cagr, CAGR_MARGIN_PP),
              eb_cagr >= spy_cagr + CAGR_MARGIN_PP),
        Check("G2", "maxDD %.2f%% within %.1fx SPY %.2f%%"
              % (eb_dd, DRAWDOWN_MULTIPLE, spy_dd),
              abs(eb_dd) <= DRAWDOWN_MULTIPLE * abs(spy_dd)),
        Check("G3", "2022 %.2f%% >= SPY 2022 %.2f%% - %.0fpp"
              % (eb_2022, spy_2022, YEAR_2022_TOLERANCE_PP)
              if eb_2022 is not None and spy_2022 is not None
              else "calendar 2022 is not in this window; the gate cannot be "
                   "decided on it",
              eb_2022 is not None and spy_2022 is not None
              and eb_2022 >= spy_2022 - YEAR_2022_TOLERANCE_PP),
        Check("G4", "rolling %dm win rate %.1f%% >= %.0f%% (n=%d)"
              % (GATE_ROLLING_MONTHS, win_rate, MIN_ROLLING_WIN_RATE_PCT,
                 total),
              total > 0 and win_rate >= MIN_ROLLING_WIN_RATE_PCT),
        Check("G5", "turnover %.0f%%/yr <= %.0f%% (%d trades)"
              % (turnover, MAX_TURNOVER_PCT_PER_YEAR, len(trades)),
              turnover <= MAX_TURNOVER_PCT_PER_YEAR),
        Check("G6", "%d ghost sells, %d cap trims (both must be 0)"
              % (ghost, capped), ghost == 0 and capped == 0),
    ]
    metrics = {
        "start": stamps[0], "end": stamps[-1], "years": years,
        "pv_rows": len(rows),
        "eb_cagr_pct": eb_cagr, "spy_cagr_pct": spy_cagr,
        "eb_max_drawdown_pct": eb_dd, "spy_max_drawdown_pct": spy_dd,
        "eb_2022_pct": eb_2022, "spy_2022_pct": spy_2022,
        "turnover_pct_per_year": turnover, "traded_notional": traded,
        "mean_equity": mean_equity, "trades": len(trades),
        "ghost_sell_lines": ghost, "cap_trim_lines": capped,
        "benchmark_dividend_yield": BENCHMARK_DIVIDEND_YIELD,
    }
    for span, (span_wins, span_total, span_rate) in sorted(rolling.items()):
        metrics["rolling_%dm_win_rate_pct" % span] = span_rate
        metrics["rolling_%dm_windows" % span] = span_total
        metrics["rolling_%dm_wins" % span] = span_wins
    return GateResult(checks, metrics, warnings)


def render(result, backtest_id):
    """The report, as lines. Carries no credential and no connection detail."""
    metrics = result.metrics
    lines = ["FROZEN GATE — backtest %s, %s -> %s, %.2fy, %d snapshots"
             % (backtest_id, metrics["start"].date(), metrics["end"].date(),
                metrics["years"], metrics["pv_rows"])]
    for check in result.checks:
        lines.append("  %s  %s  %s"
                     % ("PASS" if check.passed else "FAIL",
                        check.name, check.description))
    lines.append("")
    lines.append("  benchmark: %s accrued to total return at %.2f%%/yr"
                 % (BENCHMARK, 100.0 * metrics["benchmark_dividend_yield"]))
    lines.append("  rolling win rate vs %s (only %dm is G4):"
                 % (BENCHMARK, GATE_ROLLING_MONTHS))
    for span in ROLLING_SPANS:
        key = "rolling_%dm_win_rate_pct" % span
        if key not in metrics:
            continue
        lines.append("    %2dm  %5.1f%%  (%d of %d windows)"
                     % (span, metrics[key], metrics["rolling_%dm_wins" % span],
                        metrics["rolling_%dm_windows" % span]))
    for warning in result.warnings:
        lines.append("  WARNING: %s" % warning)
    lines.append("")
    lines.append("ALL SIX PASS — ship enabled."
                 if result.passed else
                 "GATE FAILED — ship DISABLED with these numbers recorded in "
                 "strategy_eb.DEFAULTS comments. Do NOT re-tune to pass.")
    return lines


# ------------------------------------------------------------------ sources
def unpack_api(graph_data, logs_payload):
    """(pv, trades, log_lines, logs_complete) from the two API responses.

    `logs` reads a full file off the shared volume when the container wrote
    one (source="file") and otherwise falls back to the 500-line array in the
    document (source="db"), which is exactly the case G6 must not silently
    call clean.
    """
    graph_data = graph_data or {}
    logs_payload = logs_payload or {}
    lines = logs_payload.get("logs") or []
    return (graph_data.get("portfolio_value_history") or [],
            graph_data.get("backtest_trades") or [],
            list(lines),
            str(logs_payload.get("source") or "") == "file")


def unpack_steps(rows):
    """(pv, trades, log_lines) from raw BacktestSteps rows.

    `rows` are (kind, seq, final, doc). Mirrors backtest_result_store.assemble:
    once a kind has final=true rows they supersede the live ones entirely, and
    final rows carry no cap.
    """
    buckets = {kind: {True: [], False: []} for kind in ("pv", "trade", "log")}
    finalized = set()
    for kind, seq, final, doc in rows:
        if kind not in buckets:
            continue
        if final:
            # The seq=0 marker carries a JSON null and is the ONLY evidence
            # that a kind was finalized with zero entries -- a stopped run's
            # empty arrays. It has to be seen here, before the null is dropped,
            # or such a kind falls back to the live rows it superseded.
            finalized.add(kind)
        # Nulls never become entries: a marker row reaching _ts() would be
        # `None["timestamp"]`.
        if doc is not None:
            buckets[kind][bool(final)].append((int(seq), doc))

    def series(kind):
        entries = (buckets[kind][True] if kind in finalized
                   else buckets[kind][False])
        return [doc for _seq, doc in sorted(entries, key=lambda pair: pair[0])]

    return series("pv"), series("trade"), series("log")


def fetch_api(backtest_id):
    from _api import call  # deferred: importing it reads .env and can log in

    status, data = call("GET", "/backtests/%s/graph-data" % backtest_id)
    if status != 200:
        raise GateError("graph-data -> %s: %s" % (status, data))
    status, logs = call("GET", "/backtests/%s/logs" % backtest_id)
    if status != 200:
        raise GateError("logs -> %s: %s" % (status, logs))
    return unpack_api(data, logs)


#: A gate that could write to the serving store would be a gate that could
#: edit the run it is judging. `default_transaction_read_only` is set in the
#: connection options, so it is on before the first statement rather than
#: after a round trip, and it cannot be cleared by anything this script does.
PG_READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"


def pg_connect():
    """A read-only connection to the serving store.

    Credentials come from the environment the same way `backend/db/pool` and
    `scripts/_pg.py` read them, never from an argument, so nothing here can
    print one. `scripts/_pg.py` is imported only for its .env loading; its own
    `conn()` is read-WRITE and is deliberately not used.
    """
    import os

    import psycopg2

    import _pg  # noqa: F401  -- imported for the .env load side effect

    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "server7"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "intellistock"),
        dbname=os.environ.get("POSTGRES_DB", "IntelliStock"),
        password=os.environ.get("POSTGRES_PASSWORD"),
        options=PG_READ_ONLY_OPTIONS,
        connect_timeout=15,
    )


def fetch_pg(backtest_id, *, connect=None):
    """The three step series straight out of BacktestSteps, uncapped.

    `pv` docs are {timestamp, value, cash, prices, positions_snapshot}; the
    benchmark lives in `prices`, which is why this read cannot be narrowed to
    `value` alone.
    """
    connect = connect or pg_connect
    connection = cursor = None
    try:
        connection = connect()
        cursor = connection.cursor()
        cursor.execute(
            'SELECT kind, seq, final, doc FROM "BacktestSteps" '
            "WHERE backtest_id = %s AND kind IN ('pv', 'trade', 'log') "
            "ORDER BY kind, final, seq",
            (str(backtest_id),))
        rows = cursor.fetchall()
    finally:
        if cursor is not None:
            cursor.close()
        if connection is not None:
            connection.close()
    if not rows:
        raise GateError("no BacktestSteps rows for backtest %s" % backtest_id)
    pv, trades, lines = unpack_steps(rows)
    return pv, trades, lines, True


# ------------------------------------------------------------------ self-test
def _synthetic(eb_annual, spy_annual, months=58, trade_count=20):
    """One synthetic run: month-end pv snapshots in the shape the engine
    writes, an EB curve at `eb_annual` and a price-only SPY at `spy_annual`."""
    stamps = []
    for i in range(months):
        year, month = divmod(10 + i, 12)      # start 2021-11
        stamps.append(datetime(2021 + year, month + 1, 28))
    pv = []
    for i, stamp in enumerate(stamps):
        pv.append({
            "timestamp": stamp.isoformat() + "Z",
            "value": 6000.0 * (1.0 + eb_annual) ** (i / 12.0),
            "cash": 0.0,
            "positions_snapshot": {},
            "prices": {BENCHMARK: 400.0 * (1.0 + spy_annual) ** (i / 12.0)},
        })
    trades = [{"timestamp": stamps[i % months].isoformat(), "action": "buy",
               "ticker": "TQQQ", "shares": 1.0, "price": 500.0, "total": 500.0}
              for i in range(trade_count)]
    return pv, trades


def self_test():
    """Exercise the pure evaluator on curves whose verdict is known by
    construction, with no API, no database and no clock. Prints both reports so
    the arithmetic is inspectable, and returns non-zero if either verdict is
    not the one the construction guarantees."""
    failures = []

    pv, trades = _synthetic(0.20, 0.10)
    winner = evaluate(pv, trades, ["tick 1 ok", "tick 2 ok"])
    print("\n".join(render(winner, "SYNTHETIC-WINNER")))
    if not winner.passed:
        failures.append("a 20%/yr curve against a 10%/yr SPY did not pass: "
                        + ", ".join(c.name for c in winner.checks
                                    if not c.passed))
    # The accrual has to be visible, or the benchmark is still price-only.
    if winner.metrics["spy_cagr_pct"] <= 10.0:
        failures.append("SPY CAGR %.3f%% is not accrued above its %.1f%% price "
                        "return" % (winner.metrics["spy_cagr_pct"], 10.0))
    for span in ROLLING_SPANS:
        if winner.metrics["rolling_%dm_windows" % span] <= 0:
            failures.append("no %dm rolling windows were measured" % span)

    print("")
    laggard = evaluate(*_synthetic(0.10, 0.10), ["tick ok"])
    print("\n".join(render(laggard, "SYNTHETIC-LAGGARD")))
    if laggard.passed or laggard.exit_code != 1:
        failures.append("a curve matching SPY passed a gate that asks for "
                        "SPY + %.0fpp" % CAGR_MARGIN_PP)

    print("")
    if failures:
        print("SELF-TEST FAILED:")
        for line in failures:
            print("  - %s" % line)
        return 1
    print("SELF-TEST OK — the evaluator agrees with both constructions.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evaluate the frozen Strategy EB gate on one backtest.")
    parser.add_argument("backtest_id", nargs="?",
                        help="the finished run to judge (omit with --self-test)")
    parser.add_argument("--source", choices=("api", "pg"), default="api",
                        help="api (default, the serving truth) or pg "
                             "(read-only, uncapped step rows)")
    parser.add_argument("--self-test", action="store_true",
                        help="run the evaluator on synthetic curves and exit; "
                             "touches no network and no database")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.backtest_id:
        parser.error("a backtest id is required (or pass --self-test)")

    fetch = fetch_api if args.source == "api" else fetch_pg
    try:
        pv, trades, lines, logs_complete = fetch(args.backtest_id)
        result = evaluate(pv, trades, lines, logs_complete=logs_complete)
    except GateError as exc:
        print("CANNOT EVALUATE: %s" % exc)
        return 2
    print("\n".join(render(result, args.backtest_id)))
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
