"""Forward outcomes — what each decision was WORTH, including the refusals.

This is the half of Phase 1 that was deliberately deferred, and it is what turns
a record of refusals into an answer. "The min-position floor blocked 134 grants"
is a fact. "Those 134 names went on to beat the benchmark by 8.4pp while the
book held cash" is a finding.

Resolution is IN-DOCUMENT: `BacktestResults.backtest_prices` already carries
`{timestamp, symbol, close}` for the run, so an outcome needs no network call.

────────────────────────────────────────────────────────────────────────────
WHY HORIZONS ARE MEASURED IN TIME, NOT IN BARS
────────────────────────────────────────────────────────────────────────────
The first version counted N bars forward in each symbol's own series. That is
wrong here, because `backtest_prices` does NOT have a uniform cadence per
symbol. `backtest_summary.build_backtest_price_series` emits raw intraday bars
for watchlist symbols as-is, then fills everything else from daily snapshots
deduped on `(date, symbol)`. So on a 15-minute run:

    watchlist symbol      ~26 rows/day   -> "20 bars" ≈ 5 hours
    discovered symbol       1 row/day    -> "20 bars" ≈ 20 trading days

`excess = symbol_return - benchmark_return` then subtracted a 5-hour SPY move
from a 20-day stock move. The bias has a direction and it is the worst one: the
sparse series are exactly the discovered names a gate refused, so the subsystem
would have manufactured a large positive "refusal cost" out of pure unit
mismatch — a units artifact dressed as a finding, in a project whose whole
problem is artifacts dressed as findings.

Now both legs resolve at the same wall-clock instant, and an outcome whose two
legs still span materially different elapsed time is refused rather than
reported.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from self_learning.timeline import to_naive_utc
from self_learning.types import content_id

# Only this horizon is PERSISTED. Three horizons times thousands of decisions
# times ~1000 historical runs is millions of rows, and PriceHistory at 2.3M rows
# already drove 17 restarts in 12 days here. The others are computed in memory
# for the summary and thrown away.
SCORING_HORIZON_BARS = 20
DEFAULT_HORIZONS = (1, 5, SCORING_HORIZON_BARS)

# If the symbol leg and the benchmark leg differ in elapsed time by more than
# this ratio, they are not comparable and no excess is reported.
MAX_SPAN_RATIO = 2.0

DEFAULT_CADENCE_SECONDS = 86400


@dataclass(frozen=True)
class Outcome:
    observation_id: str
    run_id: str
    symbol: str
    as_of: str
    horizon_bars: int
    entry_price: float
    exit_price: float
    return_pct: float
    benchmark_return_pct: float | None
    excess_pct: float | None
    resolved: bool
    reason: str = ""
    span_seconds: float = 0.0

    @property
    def id(self) -> str:
        return content_id("outcome", {
            "observation_id": self.observation_id,
            "horizon_bars": self.horizon_bars,
        })

    def to_doc(self) -> dict:
        return {
            "id": self.id, "observation_id": self.observation_id,
            "run_id": self.run_id, "symbol": self.symbol, "as_of": self.as_of,
            "horizon_bars": self.horizon_bars,
            "entry_price": self.entry_price, "exit_price": self.exit_price,
            "return_pct": round(self.return_pct, 6),
            "benchmark_return_pct": (None if self.benchmark_return_pct is None
                                     else round(self.benchmark_return_pct, 6)),
            "excess_pct": (None if self.excess_pct is None
                           else round(self.excess_pct, 6)),
            "resolved": self.resolved, "reason": self.reason,
            "span_seconds": round(self.span_seconds, 3),
        }


def price_series(doc, symbols=None) -> dict:
    """{SYMBOL: ([naive-utc timestamps ascending], [closes])}.

    `symbols` restricts the build. A wide-universe run carries 9-37k price rows
    and only the decided names plus the benchmark are ever read.
    """
    wanted = None if symbols is None else {str(s).strip().upper() for s in symbols}
    rows = defaultdict(list)
    for row in ((doc or {}).get("backtest_prices") or []):
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if wanted is not None and symbol not in wanted:
            continue
        stamp = to_naive_utc(row.get("timestamp"))
        if stamp is None:
            continue
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        rows[symbol].append((stamp, close))
    series = {}
    for symbol, points in rows.items():
        points.sort(key=lambda p: p[0])
        series[symbol] = ([p[0] for p in points], [p[1] for p in points])
    return series


def cadence_seconds(series) -> float:
    """The run's bar spacing, taken from its DENSEST series.

    The densest series is the one that actually reflects the run's cadence;
    sparse snapshot-filled symbols would report a daily cadence for a
    15-minute run.
    """
    best = None
    for stamps, _closes in (series or {}).values():
        if len(stamps) < 2:
            continue
        if best is None or len(stamps) > len(best):
            best = stamps
    if not best:
        return DEFAULT_CADENCE_SECONDS
    gaps = sorted((best[i + 1] - best[i]).total_seconds()
                  for i in range(len(best) - 1))
    gaps = [g for g in gaps if g > 0]
    if not gaps:
        return DEFAULT_CADENCE_SECONDS
    return gaps[len(gaps) // 2]


def _leg(series, stamp, target):
    """(entry, exit, pct, span_seconds) resolved by TIME, or None.

    Entry is the last bar at or before the decision; exit is the first bar at
    or after the target instant. Resolving both legs against the same instant
    is what makes a symbol and the benchmark comparable even when their bar
    densities differ by 26x.
    """
    stamps, closes = series
    start = bisect_right(stamps, stamp) - 1
    if start < 0:
        return None
    end = bisect_left(stamps, target)
    if end >= len(closes):
        return None                     # the horizon runs past the series
    if end <= start:
        return None                     # no forward bar; nothing to measure
    entry, exit_price = closes[start], closes[end]
    if entry <= 0:
        return None
    span = (stamps[end] - stamps[start]).total_seconds()
    return entry, exit_price, ((exit_price - entry) / entry) * 100.0, span


def resolve(observations, doc, *, horizons=DEFAULT_HORIZONS,
            benchmark_symbol="SPY", series=None) -> list:
    """One `Outcome` per (observation, horizon).

    Refused observations resolve exactly like executed ones — that is the whole
    point. An unresolvable one is returned with a reason so the caller can
    report the denominator rather than let it vanish.
    """
    observations = list(observations or [])
    benchmark_key = str(benchmark_symbol or "").strip().upper()
    if series is None:
        wanted = {getattr(o, "symbol", "") for o in observations}
        wanted.add(benchmark_key)
        series = price_series(doc, symbols=wanted)
    benchmark = series.get(benchmark_key)
    cadence = cadence_seconds(series)
    run_id = str((doc or {}).get("id") or "")

    out = []
    for observation in observations:
        symbol = getattr(observation, "symbol", "")
        stamp = to_naive_utc(getattr(observation, "as_of", ""))
        symbol_series = series.get(symbol)
        for horizon in horizons:
            base = dict(observation_id=observation.id, run_id=run_id,
                        symbol=symbol, as_of=getattr(observation, "as_of", ""),
                        horizon_bars=int(horizon), entry_price=0.0,
                        exit_price=0.0, return_pct=0.0,
                        benchmark_return_pct=None, excess_pct=None,
                        resolved=False)
            if stamp is None:
                out.append(Outcome(**base, reason="undated observation"))
                continue
            if not symbol_series:
                out.append(Outcome(
                    **base, reason="no price series for this symbol in the run"))
                continue
            target = stamp + timedelta(seconds=cadence * int(horizon))
            leg = _leg(symbol_series, stamp, target)
            if leg is None:
                out.append(Outcome(
                    **base,
                    reason=f"the {horizon}-bar horizon runs past the end of "
                           f"this symbol's series"))
                continue
            entry, exit_price, pct, span = leg

            bench_pct, excess, reason = None, None, ""
            if benchmark is None:
                # Never silently return "no excess". A missing benchmark and a
                # zero refusal cost look identical downstream otherwise.
                reason = (f"benchmark {benchmark_key} is absent from "
                          f"backtest_prices — excess cannot be computed")
            else:
                bench_leg = _leg(benchmark, stamp, target)
                if bench_leg is None:
                    reason = "the benchmark has no bar covering this horizon"
                else:
                    bench_pct = bench_leg[2]
                    bench_span = bench_leg[3]
                    lo, hi = sorted((span or 1.0, bench_span or 1.0))
                    if hi / max(lo, 1e-9) > MAX_SPAN_RATIO:
                        # The units mismatch that would have manufactured a
                        # fake refusal cost. Refuse rather than report it.
                        bench_pct = None
                        reason = (f"symbol and benchmark legs span "
                                  f"{span:.0f}s vs {bench_span:.0f}s — not "
                                  f"comparable, excess withheld")
                    else:
                        excess = pct - bench_pct

            out.append(Outcome(
                observation_id=observation.id, run_id=run_id, symbol=symbol,
                as_of=getattr(observation, "as_of", ""),
                horizon_bars=int(horizon), entry_price=entry,
                exit_price=exit_price, return_pct=pct,
                benchmark_return_pct=bench_pct, excess_pct=excess,
                resolved=True, reason=reason, span_seconds=span))
    return out


def unresolved_reasons(outcomes) -> dict:
    """The denominator, kept honest.

    Unresolved outcomes are not persisted (volume), so their COUNTS travel on
    the run summary instead. Unresolved is not random — it is concentrated in
    decisions near the window end and in symbols missing from the price series,
    which is the discovered-and-refused population.
    """
    counts = {}
    for outcome in (outcomes or []):
        if outcome.resolved:
            continue
        key = (outcome.reason or "unknown").split(" — ")[0][:80]
        counts[key] = counts.get(key, 0) + 1
    return counts


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def refusal_cost(outcomes, observations, *, horizon_bars=SCORING_HORIZON_BARS) -> dict:
    """What the BUY refusals were worth, split by the gate that caused them.

    Buys only, deliberately. The sign convention only works one way: a refused
    BUY whose name then rose cost you that move, but a refused SELL whose name
    rose EARNED it — you kept the position. Pooling both, as the first version
    did, produced a number with half its inputs inverted and a mix ratio that
    varied run to run.
    """
    by_id = {o.id: o for o in (observations or [])}
    refused, executed = [], []
    by_reason = defaultdict(list)

    for outcome in (outcomes or []):
        if not outcome.resolved or outcome.horizon_bars != horizon_bars:
            continue
        if outcome.excess_pct is None:
            continue
        observation = by_id.get(outcome.observation_id)
        if observation is None or getattr(observation, "decision", 0) != 1:
            continue                     # buys only — see the docstring
        if observation.refusal_reason is not None:
            refused.append(outcome.excess_pct)
            by_reason[observation.refusal_reason].append(outcome.excess_pct)
        elif observation.executed:
            executed.append(outcome.excess_pct)

    return {
        "horizon_bars": int(horizon_bars),
        "refused_n": len(refused),
        "executed_n": len(executed),
        "refused_median_excess_pct": _median(refused),
        "executed_median_excess_pct": _median(executed),
        "refusals_resolvable": bool(refused),
        # Which gate cost what. This is the question the subsystem exists for:
        # "the min-position floor blocked 134 grants" -> what did they do next.
        "by_gate": {reason: {"n": len(values),
                             "median_excess_pct": _median(values)}
                    for reason, values in sorted(by_reason.items())},
    }
