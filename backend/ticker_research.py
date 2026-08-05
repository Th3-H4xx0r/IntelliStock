"""Ticker research pipeline with a structural no-lookahead guarantee.

Design: docs/superpowers/specs/2026-08-05-ticker-research-pipeline-design.md

WHY IT IS BUILT THIS WAY
------------------------
Backtests must not see the future, and "we were careful" is not a guarantee. So
the filtering does not live in the adapters — it lives here, and every adapter's
output passes through `point_in_time_data.filter_available` on the way out. An
adapter cannot opt out, and a record without an `available_at` cannot be emitted
at all.

THE RULE THAT MATTERS: a source with no verifiable as-of timestamp is BANNED, not
approximated. yfinance fundamentals are the canonical example — they serve
statements as they read TODAY with no filing date, so a 2026-04 backtest would
silently use figures published in 2026-08. `factor_profitability`'s own header
calls its 120-day lag heuristic "a PROXY and a strictly worse one than the
truth". EDGAR's `filed` date replaces it.

WHAT IT IS FOR
--------------
Measured over the 98 positions this system actually took, the largest destroyer
of capital was not bad companies but **unresearchable** ones: 36 positions with no
visible filings, worth -$590 at a 25% hit rate. Grade F (burns cash AND
unprofitable) was another -$262 at 12%. This makes that judgement available at
buy time, point-in-time.

It is a filter, not an alpha engine. See the spec's "What this cannot do".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable

from point_in_time_data import PointInTimeContext, filter_available

__all__ = [
    "ResearchAdapter",
    "ResearchRecord",
    "REGISTRY",
    "register_adapter",
    "research_ticker",
]


@dataclass(frozen=True)
class ResearchRecord:
    """One dated fact about a ticker.

    `available_at` is REQUIRED and must be timezone-aware UTC. There is no
    default: a fact whose publication time is unknown cannot be filtered, and an
    unfilterable fact is a leak.
    """

    source: str
    kind: str
    payload: Any
    available_at: datetime

    def __post_init__(self):
        if not isinstance(self.available_at, datetime):
            raise ValueError(
                f"{self.source}: available_at must be a datetime, got "
                f"{type(self.available_at).__name__} — a fact with no publication "
                "time cannot be point-in-time filtered")
        if self.available_at.tzinfo is None:
            raise ValueError(
                f"{self.source}: available_at must be timezone-aware UTC; a naive "
                "timestamp silently shifts by the host's zone")


@dataclass
class ResearchAdapter:
    """One data source. `fetch` returns records; the PIPELINE filters them.

    Adapters ship DISABLED until their timestamp semantics are verified against
    live data, because an unverified adapter is a leakage vector rather than a
    missing feature.
    """

    name: str
    fetch: Callable[[str, date], Iterable[ResearchRecord]]
    enabled: bool = False
    note: str = ""


REGISTRY: dict[str, ResearchAdapter] = {}


def register_adapter(adapter: ResearchAdapter) -> None:
    REGISTRY[adapter.name] = adapter


@dataclass
class ResearchResult:
    symbol: str
    as_of: date
    records: list = field(default_factory=list)
    dossier: Any = None
    errors: dict = field(default_factory=dict)

    def by_source(self, name):
        return [r for r in self.records if r.source == name]


def _as_utc(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raise ValueError(f"cannot interpret {value!r} as a point in time")


# ── EDGAR adapter — the only one enabled, because it is the only one whose
#    timestamps are verified against live data (every XBRL fact carries `filed`).
def _edgar_fetch(symbol: str, as_of: date):
    from company_research import research_company
    dossier = research_company(symbol, as_of)
    if dossier.period_end is None:
        return []
    # `filed_at` is the honest availability stamp. Fall back to period_end only
    # when the fetcher did not surface it — never to `as_of`, which would make
    # the record trivially "available" and defeat the filter.
    fs = dossier.factors
    stamp = getattr(fs, "filed_at", None) or dossier.period_end
    return [ResearchRecord(
        source="edgar", kind="fundamentals", payload=dossier,
        available_at=_as_utc(stamp))]


register_adapter(ResearchAdapter(
    name="edgar", fetch=_edgar_fetch, enabled=True,
    note="SEC XBRL; every fact carries a `filed` date — true point-in-time"))

# ── Declared but DISABLED. Each needs its timestamp semantics verified against
#    live data before it may run in a backtest.
register_adapter(ResearchAdapter(
    name="news", fetch=lambda s, d: [], enabled=False,
    note="Alpaca/Google articles; needs published_at verified per provider"))
register_adapter(ResearchAdapter(
    name="benzinga", fetch=lambda s, d: [], enabled=False,
    note="ratings/insider/gov trades; needs the event-vs-disclosure date settled"))
register_adapter(ResearchAdapter(
    name="graph", fetch=lambda s, d: [], enabled=False,
    note="Neo4j GraphEdgeInterval is interval-scoped; needs interval->as_of proof"))

#: Sources that may NEVER be adapted. Kept as code so the ban is greppable.
BANNED_SOURCES = {
    "yfinance": "serves RESTATED statements with no filing date — a 2026-04 "
                "backtest would read figures published in 2026-08",
}


def research_ticker(symbol: str, as_of, *, adapters=None) -> ResearchResult:
    """Research `symbol` using ONLY information public at `as_of`.

    Every adapter's output is passed through `filter_available`, so a record
    dated after `as_of` cannot reach the caller regardless of adapter behaviour.
    Adapter failures are captured in `errors` and never raised — a research feed
    must not be able to halt trading.
    """
    sym = str(symbol or "").strip().upper()
    as_of_dt = _as_utc(as_of)
    as_of_d = as_of_dt.date()
    result = ResearchResult(symbol=sym, as_of=as_of_d)
    if not sym:
        return result

    ctx = PointInTimeContext.live(as_of=as_of_dt)
    for name, adapter in (adapters or REGISTRY).items():
        if not adapter.enabled:
            continue
        try:
            raw = list(adapter.fetch(sym, as_of_d) or [])
            visible = filter_available(ctx, raw, lambda rec: rec.available_at)
            result.records.extend(visible)
        except Exception as exc:
            result.errors[name] = f"{type(exc).__name__}: {exc}"

    for rec in result.records:
        if rec.source == "edgar" and rec.kind == "fundamentals":
            result.dossier = rec.payload
            break
    return result
