"""Attributing live P&L to the subsystem's own changes.

The automatic breaker fires on "drawdown attributable to learning actions". That
phrase has to mean something specific or the breaker either never fires or fires
on the market.

What is attributable: the equity change since a live-tier action was applied,
MINUS the benchmark's change over the same span. A 9% drop while the index drops
10% is not the subsystem's doing, and tripping on it would revert a change for
being right. A 9% drop while the index is flat is.

Two conservative choices, both in the direction of firing:

* When there is no benchmark, the raw drawdown is used. Refusing to measure
  would mean never firing, and a breaker that cannot fire is decoration.
* When several live actions are in flight, the drawdown is attributed to ALL of
  them. The subsystem cannot separate their contributions, and the safe response
  to "one of these is hurting" is to unwind them, not to keep them all.
"""
from __future__ import annotations

from dataclasses import dataclass

from self_learning.timeline import to_naive_utc


@dataclass(frozen=True)
class Attribution:
    applied_at: str
    equity_start: float
    equity_now: float
    raw_pct: float
    benchmark_pct: float | None
    attributable_pct: float
    measurable: bool
    reason: str = ""

    def to_doc(self) -> dict:
        return {
            "applied_at": self.applied_at,
            "equity_start": self.equity_start, "equity_now": self.equity_now,
            "raw_pct": round(self.raw_pct, 6),
            "benchmark_pct": (None if self.benchmark_pct is None
                              else round(self.benchmark_pct, 6)),
            "attributable_pct": round(self.attributable_pct, 6),
            "measurable": self.measurable, "reason": self.reason,
        }


def _at_or_after(series, stamp):
    """(value, timestamp) of the first point at or after `stamp`, or None."""
    best = None
    for point in (series or []):
        point_stamp = to_naive_utc((point or {}).get("timestamp"))
        if point_stamp is None or point_stamp < stamp:
            continue
        if best is None or point_stamp < best[1]:
            try:
                best = (float(point.get("value")), point_stamp)
            except (TypeError, ValueError):
                continue
    return best


def _last(series):
    best = None
    for point in (series or []):
        point_stamp = to_naive_utc((point or {}).get("timestamp"))
        if point_stamp is None:
            continue
        if best is None or point_stamp > best[1]:
            try:
                best = (float(point.get("value")), point_stamp)
            except (TypeError, ValueError):
                continue
    return best


def since_applied(*, applied_at, equity_series, benchmark_series=None) -> Attribution:
    """Drawdown since a live action landed, net of the benchmark."""
    stamp = to_naive_utc(applied_at)
    if stamp is None:
        return Attribution(str(applied_at), 0.0, 0.0, 0.0, None, 0.0, False,
                           "the action has no parseable applied_at")

    start = _at_or_after(equity_series, stamp)
    end = _last(equity_series)
    if start is None or end is None or start[0] <= 0:
        return Attribution(str(applied_at), 0.0, 0.0, 0.0, None, 0.0, False,
                           "no equity observation covering this action")
    if end[1] <= start[1]:
        return Attribution(str(applied_at), start[0], start[0], 0.0, None, 0.0,
                           False, "no elapsed time since the action landed")

    raw_pct = ((end[0] - start[0]) / start[0]) * 100.0

    benchmark_pct = None
    if benchmark_series:
        b_start = _at_or_after(benchmark_series, stamp)
        b_end = _last(benchmark_series)
        if b_start and b_end and b_start[0] > 0 and b_end[1] > b_start[1]:
            benchmark_pct = ((b_end[0] - b_start[0]) / b_start[0]) * 100.0

    attributable = raw_pct if benchmark_pct is None else raw_pct - benchmark_pct
    return Attribution(
        applied_at=str(applied_at), equity_start=start[0], equity_now=end[0],
        raw_pct=raw_pct, benchmark_pct=benchmark_pct,
        attributable_pct=attributable, measurable=True,
        reason=("no benchmark series — using the raw move, which errs toward "
                "firing" if benchmark_pct is None else ""))


def worst_drawdown(attributions) -> float:
    """The deepest attributable LOSS across live actions, as a positive percent.

    Positive means "this much drawdown", so it compares directly against the
    breaker's limit. A profitable set returns 0.0 rather than a negative
    number, which would read as a drawdown of the wrong sign.
    """
    worst = 0.0
    for item in (attributions or []):
        if not getattr(item, "measurable", False):
            continue
        loss = -float(getattr(item, "attributable_pct", 0.0) or 0.0)
        worst = max(worst, loss)
    return round(worst, 6)
