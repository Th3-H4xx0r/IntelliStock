"""Point-in-time gross profitability (Novy-Marx GP/A) as a tradeable factor.

WHY THIS EXISTS
---------------
Every signal this engine currently trades on is either (a) unproven — the
news/graph sentiment path has never demonstrated out-of-sample alpha — or
(b) unmeasurable, because the Neo4j-backed backtests read the graph at
*today's* state and ~36% of delisted names are simply absent, so a historical
run cannot be trusted even when it prints a number.

Gross profitability is the opposite on both counts:

  * Novy-Marx, "The Other Side of Value: The Gross Profitability Premium"
    (JFE 2013) shows GP/A has roughly the same predictive power as
    book-to-market, and that the premium persists for more than three years
    after formation. It is not a two-week news pop.
  * It turns over about 25% PER YEAR. This engine's live book turns over
    ~3,500%/yr at a measured 23.2 bps one-way. In Novy-Marx & Velikov's
    cost taxonomy the low-turnover bucket is the only one whose gross alpha
    survives realistic trading costs — a 25%/yr factor pays ~6 bps/yr of
    cost, a 3,500%/yr factor pays ~800 bps/yr.
  * It is computable POINT-IN-TIME from dated fundamentals, so a backtest of
    it can be honest in a way the graph backtests structurally cannot.

THE POINT-IN-TIME RULE (the whole reason this module is careful)
---------------------------------------------------------------
A fiscal period END is not the date the numbers became public. Apple's fiscal
year ends 2025-09-30; nobody outside Apple could compute FY2025 GP/A on
2025-09-30 because the 10-K had not been filed. Sorting a portfolio on
2025-10-01 using the FY2025 figure is lookahead, and it is exactly the class
of bug that makes the existing Neo4j backtests worthless.

So every fiscal period here carries an AVAILABILITY date = period end +
a conservative reporting lag (default 120 days, configurable), and a period
is invisible to `as_of` until that date. See `DEFAULT_REPORTING_LAG_DAYS`
for the justification and for why a real SEC filing date would be better.

MEASURED COVERAGE (2026-08-03, 79-name sample of alpaca-main's own book:
every symbol with a real BotTradeDecisions row, plus a seeded random draw from
the 1,308 tickers GraphNexusDiscoveredStocks has surfaced for that instance)
-----------------------------------------------------------------------------
    57/79 = 72.2%  computed a GP/A
    10/79          excluded_fund       (CHPY COPJ DZZ FTXL IYH OIH PPA SKYU
                                        UBOT WGMI — ALL caught by quoteType,
                                        none were on the static ticker list)
     4/79          excluded_sector     (BX FHB HIG MARA)
     4/79          missing_gross_profit (clinical-stage biotech, no COGS line)
     3/79          no_data
     1/79          fetch_failed        (Yahoo 404 on a delisted ticker)
    GP/A spread across the 57: -0.230 / 0.197 / 0.586 (min / median / max),
    AAPL = 0.5434 on the 2025-09-30 fiscal year, matching the value verified
    directly against yfinance.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not decide anything. It returns a number or it returns None. Wiring
it into sizing/tilt is the caller's job, deliberately, so that a factor bug
can never place an order by itself.

And four honest limits, because a factor that oversells itself is worse than
no factor:

  * RESTATEMENTS ARE INVISIBLE. yfinance serves the statements as they read
    TODAY, so a period fetched now reflects any later restatement of it. The
    availability rule prevents lookahead in TIME but not in REVISION. Only a
    vintage-aware source (Compustat point-in-time, or reconstructing from the
    original EDGAR filing rather than the latest) fixes this.
  * SURVIVORSHIP. Yahoo drops delisted tickers, so a historical universe built
    from what it answers today is missing the failures — the same hole that
    makes the Neo4j backtests untrustworthy (~36% of delisted names absent).
    This module abstains on those names rather than inventing them, which
    keeps the LIVE path honest but does not repair a historical study.
  * DEPTH. ~4 annual periods are available per name, so this cannot support a
    long-horizon historical sort without a different data source.
  * THE SECTOR EXCLUSION IS ONLY AS GOOD AS THE VENDOR'S LABEL. In the sample
    above Yahoo files MARA (a bitcoin miner) under Financial Services, so it
    is excluded; a mislabelled bank would be included. A SIC code from EDGAR
    would be the durable fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import math
import re
import threading
import time
from typing import Any, Callable, Iterable, Mapping

UTC = timezone.utc


# ──────────────────────────────────────────────────────────────────────────────
# Tunables
# ──────────────────────────────────────────────────────────────────────────────

# Conservative reporting lag, in CALENDAR days, between a fiscal period end and
# the date its figures may be treated as public.
#
# Justification (SEC deadlines for a 10-K, Exchange Act Rule 13a-1):
#     large accelerated filer  60 days after fiscal year end
#     accelerated filer        75 days
#     non-accelerated filer    90 days
# plus Rule 12b-25 (Form NT 10-K), which buys a late filer another 15 days.
# 90 + 15 = 105 is the regulatory worst case for a compliant filer, so 120
# leaves ~2 weeks of margin for genuinely delinquent filers and for the lag
# between EDGAR acceptance and a data vendor surfacing the figures. Quarterly
# deadlines (40/45 days, +5 under 12b-25) are covered many times over.
#
# For reference, Fama-French and Novy-Marx himself use an even blunter rule —
# accounting data for fiscal years ending in calendar year t-1 are matched to
# returns from JUNE of year t, i.e. a lag of 6 to 17 months. 120 days is
# tighter than the academic convention and looser than the regulatory maximum.
#
# THIS IS A PROXY AND A STRICTLY WORSE ONE THAN THE TRUTH. The correct input is
# the actual EDGAR filing timestamp (`acceptanceDateTime` from the SEC
# submissions API, https://data.sec.gov/submissions/CIK##########.json, or the
# `filed` field on the companyfacts endpoint). Using it would (a) remove the
# 30-60 days of signal this lag throws away for the large accelerated filers
# that make up most of a liquid book, and (b) remove the residual lookahead
# risk for the small minority of filers who file later than 120 days. Swapping
# the availability source is a one-function change: see `FiscalPeriod.available_at`.
DEFAULT_REPORTING_LAG_DAYS = 120

# A fiscal period older than this (measured period end -> as_of) is refused
# outright. A company that has not reported in two years is either delisted,
# in a filing delinquency, or the data feed has silently stopped updating it —
# in all three cases a GP/A computed off the last known statement is a stale
# number wearing a fresh timestamp, which is worse than no number.
DEFAULT_MAX_PERIOD_AGE_DAYS = 730

# GP/A above this is not a company, it is a units bug. Real GP/A spans roughly
# -1 to 2 (Novy-Marx's decile breakpoints top out well under 1.0 for the broad
# cross-section). A ratio of 40 means gross profit arrived in dollars and total
# assets in thousands, or the statement rows were mis-paired.
MAX_PLAUSIBLE_GP_A = 10.0

# Gross profit cannot exceed revenue. Tolerance absorbs float noise and the
# occasional vendor rounding of the two lines to different precisions.
_GP_OVER_REVENUE_TOLERANCE = 1.0005

# Percentile a caller should use when `cross_sectional_ranks` abstains (None).
# Exported so nobody reaches for 0.0 — see the comment in that function for the
# reason that particular default is dangerous.
NEUTRAL_PERCENTILE = 0.5

# Below this many ranked names a percentile is theatre: with 2 names the loser
# gets 0.0 and the winner 1.0, i.e. maximum-strength tilts derived from a
# single pairwise comparison. Novy-Marx sorts the whole cross-section into
# deciles; a handful of names cannot support that.
DEFAULT_MIN_RANKED_NAMES = 5

# Tie quantization. Two GP/A values that differ at the 1e-10 level are the same
# number with different float dust, and letting the dust decide the ordering is
# how a sort becomes non-deterministic. Quantization is monotone, so it can only
# MERGE adjacent values into a tie — it can never reorder two names.
_TIE_DECIMALS = 9

# Percentiles are rounded so that two runs of the same cross-section compare
# equal with `==`, which the callers (and the A/B harness) do.
_PERCENTILE_DECIMALS = 12


# ──────────────────────────────────────────────────────────────────────────────
# Exclusions — GP/A is meaningless for these, so emit nothing rather than junk
# ──────────────────────────────────────────────────────────────────────────────

# Yahoo `quoteType` values that are not an operating company. A fund has
# "total assets" (AUM) and no gross profit; if a vendor ever fills those rows
# with fund accounting, GP/A becomes a number that means nothing and ranks
# somewhere in the cross-section anyway.
EXCLUDED_QUOTE_TYPES = frozenset({
    "ETF", "MUTUALFUND", "INDEX", "CURRENCY", "CRYPTOCURRENCY",
    "FUTURE", "OPTION", "MONEYMARKET",
})

# Novy-Marx excludes financials, and the reason is mechanical rather than
# stylistic: for a bank the balance sheet IS the business. Total assets are
# the loan book, which is levered 10-15x against equity, so GP/A measures
# leverage, not profitability, and every bank sorts into the bottom decile
# for a reason that has nothing to do with the factor. Insurers have the same
# problem via float. Comparison is done lower-cased; Yahoo says
# "Financial Services", other vendors say "Financials".
DEFAULT_EXCLUDED_SECTORS = frozenset({
    "financial services", "financials", "financial",
})

# Static fallback for the fund check. The `quoteType` lookup is a network call
# that returns "" often enough to matter (delisted tickers, Yahoo 404s on
# funds — see the HTTPError branch in fundamentals_util.get_fundamentals), and
# an empty quoteType would let a leveraged ETF through the type gate. These are
# the funds this book actually touches, lifted from the sets that
# graph_nexus_analysis already maintains. Belt and braces: the statement fetch
# for an ETF also returns nothing, so a miss here still abstains.
_KNOWN_FUND_TICKERS = frozenset({
    # broad / sector
    "SPY", "QQQ", "DIA", "IWM", "VTI", "VOO", "SMH", "SOXX", "XBI", "VGT",
    "XLF", "XLE", "XLV", "XLI", "XLK", "XLP", "XLY", "XLU", "XLRE", "XLC", "XLB",
    # commodity
    "PSLV", "GLDM", "SLV", "IAU", "GLD", "GDX", "GDXJ", "PPLT", "PALL", "SIVR",
    "SGOL", "AAAU", "BAR", "OUNZ", "USO", "BNO", "UNG", "DBA", "DBC", "GSG", "PDBC",
    # leveraged / inverse — SQQQ in particular is a standing position on this book
    "SOXL", "SOXS", "OILU", "OILD", "BOIL", "KOLD", "NRGU", "NRGD", "GUSH", "DRIP",
    "LABU", "LABD", "TQQQ", "SQQQ", "UVXY", "SVXY", "UVIX", "TNA", "TZA", "FAS", "FAZ",
    "YINN", "YANG", "UCO", "SCO", "UGL", "GLL", "AGQ", "ZSL", "JNUG", "JDST",
    "NUGT", "DUST", "ERX", "ERY", "TECL", "TECS", "SPXL", "SPXS", "UPRO", "SPXU",
    "UDOW", "SDOW", "TMF", "TMV", "BITX", "CONL", "DPST", "WANT", "CWEB",
    "COPX", "COPZ", "CPER", "KCOP", "SLVX", "SLVO",
    # crypto trusts / bond funds that carry a "sector" but no gross profit
    "BITO", "GBTC", "BLOK", "BND", "AGG", "TLT", "HYG", "LQD",
})

# A plain US equity ticker. This gate exists because the SAME broker code path
# now trades crypto pairs in Alpaca's slash form ("BTC/USD") — see
# ticker_universe.is_valid_crypto_pair. Asking Yahoo for the gross profit of
# BTC/USD is meaningless, and a batch caller that forwards its whole universe
# would do exactly that on every bar.
_EQUITY_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}([.\-][A-Z0-9]{1,2})?$")


# Reason codes. Returned verbatim in the detail dict so the caller can log WHY
# a name abstained instead of silently seeing None for every symbol.
REASON_OK = "ok"
REASON_BAD_SYMBOL = "bad_symbol"
REASON_NOT_AN_OPERATING_COMPANY = "excluded_fund"
REASON_EXCLUDED_SECTOR = "excluded_sector"
REASON_FETCH_FAILED = "fetch_failed"
REASON_NO_DATA = "no_data"
REASON_NO_QUALIFYING_PERIOD = "no_qualifying_period"
REASON_STALE_PERIOD = "stale_period"
REASON_BAD_TOTAL_ASSETS = "bad_total_assets"
REASON_MISSING_GROSS_PROFIT = "missing_gross_profit"
REASON_GP_EXCEEDS_REVENUE = "gross_profit_exceeds_revenue"
REASON_IMPLAUSIBLE_RATIO = "implausible_ratio"


# ──────────────────────────────────────────────────────────────────────────────
# Record types — RAW, dated, as-of-independent
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class FiscalPeriod:
    """One fiscal period's raw statement lines, as reported.

    Deliberately holds RAW figures and NOT the derived ratio: the reporting-lag
    rule is a policy that changes (a real EDGAR filing date will replace it),
    and if the cache held ratios every policy change would require a full
    refetch of the whole universe.
    """

    period_end: date
    gross_profit: float | None = None
    total_revenue: float | None = None
    total_assets: float | None = None
    # Kept so GP can be reconstructed as REVT - COGS (Novy-Marx's actual
    # definition) when a vendor omits the pre-computed "Gross Profit" row.
    cost_of_revenue: float | None = None
    frequency: str = "annual"          # "annual" | "quarterly"
    # Optional: the true filing timestamp when a source can supply it. When
    # present it WINS over the lag heuristic — that is the whole upgrade path.
    filed_at: date | None = None

    def available_at(self, lag_days: int = DEFAULT_REPORTING_LAG_DAYS) -> date:
        """The first date on which these figures may be used.

        Prefers a real filing date; falls back to period end + lag. Replacing
        the heuristic globally is a matter of populating `filed_at`.
        """
        if self.filed_at is not None:
            return self.filed_at
        return self.period_end + timedelta(days=max(0, int(lag_days)))


@dataclass(frozen=True, slots=True)
class FundamentalsRecord:
    """Everything about a symbol that does NOT depend on `as_of`.

    This is the unit the cache stores. Note what is absent: no ratio, no
    percentile, no as_of, no lag. Everything time-dependent is derived at read
    time from `periods`, which is what makes a single cached row correct for
    every possible `as_of`.
    """

    symbol: str
    quote_type: str = ""
    sector: str = ""
    periods: tuple[FiscalPeriod, ...] = ()
    source: str = ""
    fetched_at: float = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────────────────────

# Bumped whenever FundamentalsRecord / FiscalPeriod change shape. A stored
# entry whose version differs is treated as a MISS and refetched, mirroring the
# `adjustment` check in graph_nexus_analysis._overlay_bars_cache_get.
CACHE_SCHEMA_VERSION = 1

# Fundamentals move once a quarter. A 12h TTL means a fresh 10-K is picked up
# within half a day while a live loop over a 200-name universe makes ~400
# network calls a DAY instead of ~400 per BAR (at 15m bars that is the
# difference between 400 and 10,400).
DEFAULT_CACHE_TTL_SECONDS = 12 * 3600

# Failure throttle, same idea as ticker_universe._BREADTH_LAST_FAIL_AT: when
# Yahoo 429s or times out, do not re-hit it once per symbol per bar. A 30s
# timeout per call across a 200-name universe stalls the bar for over an hour.
DEFAULT_FAILURE_RETRY_SECONDS = 900


@dataclass
class _CacheEntry:
    record: FundamentalsRecord
    stored_at: float
    schema_version: int = CACHE_SCHEMA_VERSION


class FundamentalsCache:
    """Per-symbol cache of RAW dated statements.

    KEYING — read this before changing it.

    The key is (schema_version, SYMBOL) and NOTHING ELSE, which is only safe
    because the cached value is as-of-independent raw data. This repo already
    shipped the other version of this bug: `GraphNexusOverlayBarsCache` was
    keyed by symbol alone but stored a value whose meaning depended on a
    setting outside the key (`ALPACA_BARS_ADJUSTMENT`), so a row written when
    bars were raw got served to a reader expecting split-adjusted bars, and
    VGT's 8-for-1 split was read as an -87% crash.

    The invariant that keeps this cache honest:

        anything stored here must be valid for EVERY as_of.

    `put()` enforces the typed half of that (only FundamentalsRecord, never a
    derived float), and the derivation in `gross_profitability_detail` never
    writes back. If you are ever tempted to memoize a GP/A here, the key must
    grow to include as_of AND lag_days AND max_period_age_days — at which point
    you have a different cache, so give it a different name.
    """

    __slots__ = ("_entries", "_failures", "_lock", "_ttl", "_failure_retry",
                 "_clock", "_hits", "_misses", "_fetches")

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        failure_retry_seconds: float = DEFAULT_FAILURE_RETRY_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._entries: dict[str, _CacheEntry] = {}
        self._failures: dict[str, float] = {}
        # RLock, not Lock: the live loop calls this from the broker thread while
        # the backfill/discovery path can call it re-entrantly through a batch.
        self._lock = threading.RLock()
        self._ttl = float(ttl_seconds)
        self._failure_retry = float(failure_retry_seconds)
        self._clock = clock
        self._hits = 0
        self._misses = 0
        self._fetches = 0

    # -- raw accessors -------------------------------------------------------

    def peek(self, symbol: str) -> FundamentalsRecord | None:
        """Return a live (non-expired, right-schema) record or None. No fetch."""
        key = _normalize_symbol(symbol)
        if not key:
            return None
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            # Schema drift is a MISS, never a coerced read: a record written by
            # an older shape may be missing the very field the caller is about
            # to trust.
            if entry.schema_version != CACHE_SCHEMA_VERSION:
                self._entries.pop(key, None)
                return None
            if self._ttl >= 0 and (now - entry.stored_at) > self._ttl:
                self._entries.pop(key, None)
                return None
            return entry.record

    def put(self, record: FundamentalsRecord) -> None:
        """Store a record. Rejects anything that is not as-of-independent raw data."""
        if not isinstance(record, FundamentalsRecord):
            # Loud, not silent. A float here would be a derived value cached
            # under an as_of-free key — the exact GraphNexusOverlayBarsCache
            # failure, and it would serve one date's answer for another.
            raise TypeError(
                "FundamentalsCache stores raw dated statements only; "
                f"refusing to cache {type(record).__name__}"
            )
        key = _normalize_symbol(record.symbol)
        if not key:
            return
        with self._lock:
            self._entries[key] = _CacheEntry(
                record=record,
                stored_at=self._clock(),
                schema_version=CACHE_SCHEMA_VERSION,
            )
            self._failures.pop(key, None)

    def note_failure(self, symbol: str) -> None:
        """Record a failed/empty fetch so it is not retried every bar.

        The failure is NOT stored as a record. An empty result must never be
        persisted as if it were data — the overlay-bars post-mortem
        (2026-07-19) found empty rows persisting forever and permanently
        blinding the regime detector.
        """
        key = _normalize_symbol(symbol)
        if key:
            with self._lock:
                self._failures[key] = self._clock()

    def in_failure_backoff(self, symbol: str) -> bool:
        key = _normalize_symbol(symbol)
        if not key:
            return False
        with self._lock:
            last = self._failures.get(key)
        if last is None:
            return False
        return (self._clock() - last) < self._failure_retry

    # -- fetch-through -------------------------------------------------------

    def get_or_fetch(
        self,
        symbol: str,
        fetcher: Callable[[str], FundamentalsRecord | None],
    ) -> FundamentalsRecord | None:
        """Cached read, falling through to `fetcher` on a miss.

        Never raises for a data problem: a fetcher that blows up is a failure,
        and a failure is an abstention, not an exception in the trading loop.

        The lock is deliberately NOT held across the fetch. Two threads that
        miss on the same symbol at the same instant will both call the fetcher
        and the second `put` wins — one wasted network call. Holding the lock
        instead would serialize a 200-name universe behind one 30s Yahoo
        timeout, which is the failure that actually stalls a bar.
        """
        key = _normalize_symbol(symbol)
        if not key:
            return None
        cached = self.peek(key)
        if cached is not None:
            with self._lock:
                self._hits += 1
            return cached
        with self._lock:
            self._misses += 1
        if self.in_failure_backoff(key):
            return None
        try:
            with self._lock:
                self._fetches += 1
            record = fetcher(key)
        except Exception:
            # yfinance raises everything from HTTPError to KeyError to
            # JSONDecodeError depending on which of Yahoo's endpoints is
            # unhappy. Catch broadly, the same way fundamentals_util does.
            self.note_failure(key)
            return None
        if record is None or not isinstance(record, FundamentalsRecord):
            self.note_failure(key)
            return None
        self.put(record)
        return record

    # -- housekeeping --------------------------------------------------------

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._failures.clear()
            self._hits = self._misses = self._fetches = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "failures": len(self._failures),
                "hits": self._hits,
                "misses": self._misses,
                "fetches": self._fetches,
            }


# Module-level default, same pattern as ticker_universe's `_BREADTH_CACHE`:
# process-wide so a live loop shares it across strategies.
_DEFAULT_CACHE = FundamentalsCache()


def default_cache() -> FundamentalsCache:
    return _DEFAULT_CACHE


def clear_fundamentals_cache() -> None:
    """Drop the process-wide cache (tests, and the operator 'refetch' path)."""
    _DEFAULT_CACHE.clear()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def _num(value: Any) -> float | None:
    """float(value) or None. NaN is None — yfinance frames are full of NaN."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def coerce_as_of(as_of: Any) -> date:
    """Normalize `as_of` to a UTC calendar date.

    Accepts a date, a datetime (naive is read as UTC — the whole engine stamps
    UTC), an ISO string, or any object exposing `.as_of` (which is how a
    `point_in_time_data.PointInTimeContext` can be handed straight in without
    this module importing it).

    Raises ValueError on junk ON PURPOSE. Every other failure in this module is
    an abstention, but an unparseable as_of is a caller bug, not a data
    problem, and silently returning None for the entire universe would hide it
    behind a factor that merely looks unhelpful.
    """
    value = as_of
    if hasattr(value, "as_of") and not isinstance(value, (date, datetime, str)):
        value = value.as_of
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                pass
            else:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC).date()
    raise ValueError(f"as_of is not a date/datetime/ISO string: {as_of!r}")


def _coerce_period_end(value: Any) -> date | None:
    """pandas Timestamp / datetime / date / ISO string -> date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    to_pydatetime = getattr(value, "to_pydatetime", None)
    if callable(to_pydatetime):
        try:
            return to_pydatetime().date()
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def exclusion_reason(
    symbol: str,
    record: FundamentalsRecord | None = None,
    *,
    excluded_sectors: Iterable[str] | None = None,
) -> str | None:
    """Return a reason code if GP/A is meaningless for this name, else None.

    Split out from the main path so a caller (or a universe builder) can screen
    a list before spending a network call per name.
    """
    sym = _normalize_symbol(symbol)
    if not sym or not _EQUITY_TICKER_RE.match(sym):
        # Catches "", "BTC/USD", "^VIX", "EURUSD=X" and the empty-string
        # symbols that fall out of a malformed positions payload.
        return REASON_BAD_SYMBOL
    if sym in _KNOWN_FUND_TICKERS:
        return REASON_NOT_AN_OPERATING_COMPANY
    if record is None:
        return None
    quote_type = _normalize_symbol(record.quote_type)
    if quote_type and quote_type in EXCLUDED_QUOTE_TYPES:
        return REASON_NOT_AN_OPERATING_COMPANY
    sectors = DEFAULT_EXCLUDED_SECTORS if excluded_sectors is None else {
        str(s or "").strip().lower() for s in excluded_sectors
    }
    sector = str(record.sector or "").strip().lower()
    if sector and sector in sectors:
        return REASON_EXCLUDED_SECTOR
    return None


# ──────────────────────────────────────────────────────────────────────────────
# The factor
# ──────────────────────────────────────────────────────────────────────────────

def select_period(
    record: FundamentalsRecord,
    as_of: date,
    *,
    lag_days: int = DEFAULT_REPORTING_LAG_DAYS,
    max_period_age_days: int = DEFAULT_MAX_PERIOD_AGE_DAYS,
) -> tuple[FiscalPeriod | None, str]:
    """Pick the most recent fiscal period that was PUBLIC as of `as_of`.

    Returns (period, reason). The reason distinguishes "this company had not
    reported yet" from "this company stopped reporting", which are very
    different problems and get logged differently.
    """
    visible: list[FiscalPeriod] = []
    for period in record.periods or ():
        if not isinstance(period, FiscalPeriod) or period.period_end is None:
            continue
        if period.available_at(lag_days) <= as_of:
            visible.append(period)
    if not visible:
        return None, REASON_NO_QUALIFYING_PERIOD
    # Deterministic even when a feed hands back duplicate period ends: sort by
    # (period_end, frequency) and take the last. Annual sorts after "quarterly"
    # is false alphabetically, so make the preference explicit instead of
    # relying on the string — a quarterly GP paired with a fiscal-year-end
    # balance sheet would be an apples/oranges ratio.
    visible.sort(key=lambda p: (p.period_end, 0 if p.frequency == "quarterly" else 1))
    chosen = visible[-1]
    age_days = (as_of - chosen.period_end).days
    if age_days > max(0, int(max_period_age_days)):
        # The company has not reported in two years. Whatever the last
        # statement says, it is not a description of the business today.
        return chosen, REASON_STALE_PERIOD
    return chosen, REASON_OK


def gp_a_from_period(
    period: FiscalPeriod,
    *,
    allow_cogs_fallback: bool = True,
) -> tuple[float | None, str]:
    """Novy-Marx GP/A for one period, or (None, reason).

    Every branch here fails CLOSED. A factor that silently returns a wrong
    number gets sized like a right one.
    """
    total_assets = _num(period.total_assets)
    if total_assets is None or total_assets <= 0.0:
        # Zero/negative total assets is not a distressed company, it is a
        # parsing failure — assets are an identity (liabilities + equity) and
        # cannot be negative. Dividing by it produces a signed infinity or a
        # huge negative that sorts straight into the short bucket.
        return None, REASON_BAD_TOTAL_ASSETS

    gross_profit = _num(period.gross_profit)
    total_revenue = _num(period.total_revenue)
    if gross_profit is None and allow_cogs_fallback:
        # Novy-Marx defines gross profit as Compustat REVT - COGS. Vendors
        # sometimes omit the pre-computed "Gross Profit" row while supplying
        # both inputs; reconstructing it is the definition, not a guess.
        cost_of_revenue = _num(period.cost_of_revenue)
        if total_revenue is not None and cost_of_revenue is not None:
            gross_profit = total_revenue - cost_of_revenue
    if gross_profit is None:
        # NOTE: a MISSING gross profit abstains; a NEGATIVE one does not.
        # Negative gross profit is real (pre-revenue biotech, a miner below
        # cash cost) and Novy-Marx's sort keeps those names in the bottom
        # decile rather than dropping them.
        return None, REASON_MISSING_GROSS_PROFIT

    if total_revenue is not None and total_revenue > 0.0:
        if gross_profit > total_revenue * _GP_OVER_REVENUE_TOLERANCE:
            # Gross profit is revenue minus cost of goods; exceeding revenue
            # means the two rows came from different periods or different
            # units. Cheap integrity check that catches mis-pairing.
            return None, REASON_GP_EXCEEDS_REVENUE

    gp_a = gross_profit / total_assets
    if abs(gp_a) > MAX_PLAUSIBLE_GP_A:
        return None, REASON_IMPLAUSIBLE_RATIO
    return gp_a, REASON_OK


def gross_profitability_detail(
    symbol: str,
    as_of: Any,
    *,
    lag_days: int = DEFAULT_REPORTING_LAG_DAYS,
    max_period_age_days: int = DEFAULT_MAX_PERIOD_AGE_DAYS,
    excluded_sectors: Iterable[str] | None = None,
    allow_cogs_fallback: bool = True,
    fetcher: Callable[[str], FundamentalsRecord | None] | None = None,
    cache: FundamentalsCache | None = None,
) -> dict[str, Any]:
    """`gross_profitability` with the audit trail attached.

    Returns a dict the caller can drop straight into bot_decision_log:
    {symbol, gp_a, reason, period_end, available_at, gross_profit,
     total_assets, frequency, source}. `gp_a` is None whenever `reason` is
    anything but "ok".
    """
    as_of_date = coerce_as_of(as_of)          # raises on caller bugs, by design
    sym = _normalize_symbol(symbol)
    out: dict[str, Any] = {
        "symbol": sym,
        "gp_a": None,
        "reason": REASON_NO_DATA,
        "as_of": as_of_date.isoformat(),
        "period_end": None,
        "available_at": None,
        "gross_profit": None,
        "total_assets": None,
        "frequency": None,
        "source": None,
    }

    pre = exclusion_reason(sym, None, excluded_sectors=excluded_sectors)
    if pre is not None:
        out["reason"] = pre
        return out

    store = cache if cache is not None else _DEFAULT_CACHE
    fetch = fetcher if fetcher is not None else yfinance_fetcher
    record = store.get_or_fetch(sym, fetch)
    if record is None:
        out["reason"] = REASON_FETCH_FAILED
        return out
    out["source"] = record.source or None

    post = exclusion_reason(sym, record, excluded_sectors=excluded_sectors)
    if post is not None:
        out["reason"] = post
        return out

    if not record.periods:
        out["reason"] = REASON_NO_DATA
        return out

    period, reason = select_period(
        record, as_of_date,
        lag_days=lag_days,
        max_period_age_days=max_period_age_days,
    )
    if period is not None:
        out["period_end"] = period.period_end.isoformat()
        out["available_at"] = period.available_at(lag_days).isoformat()
        out["frequency"] = period.frequency
    if reason != REASON_OK:
        out["reason"] = reason
        return out

    # No fallback to an earlier period when the newest one fails a quality
    # check. Silently reaching back a year would emit a stale number under a
    # fresh as_of, which is the failure mode this whole module exists to avoid.
    gp_a, reason = gp_a_from_period(period, allow_cogs_fallback=allow_cogs_fallback)
    out["gross_profit"] = _num(period.gross_profit)
    out["total_assets"] = _num(period.total_assets)
    out["reason"] = reason
    if reason == REASON_OK:
        out["gp_a"] = gp_a
    return out


def gross_profitability(
    symbol: str,
    as_of: Any,
    *,
    lag_days: int = DEFAULT_REPORTING_LAG_DAYS,
    max_period_age_days: int = DEFAULT_MAX_PERIOD_AGE_DAYS,
    excluded_sectors: Iterable[str] | None = None,
    allow_cogs_fallback: bool = True,
    fetcher: Callable[[str], FundamentalsRecord | None] | None = None,
    cache: FundamentalsCache | None = None,
) -> float | None:
    """Novy-Marx gross profitability (gross profit / total assets) for `symbol`,
    using ONLY fiscal periods that were public as of `as_of`.

    Returns None — never a guess — when the name is a fund or a financial, when
    no fiscal period had been published yet, when the newest published period
    is more than `max_period_age_days` old, or when the underlying figures fail
    a data-quality check.
    """
    return gross_profitability_detail(
        symbol, as_of,
        lag_days=lag_days,
        max_period_age_days=max_period_age_days,
        excluded_sectors=excluded_sectors,
        allow_cogs_fallback=allow_cogs_fallback,
        fetcher=fetcher,
        cache=cache,
    )["gp_a"]


def gross_profitability_batch(
    symbols: Iterable[str],
    as_of: Any,
    **kwargs: Any,
) -> dict[str, float | None]:
    """GP/A for a universe. Every input symbol appears in the output; excluded
    and unavailable names map to None so a caller cannot mistake "no opinion"
    for "computed a zero".

    Iterates in sorted order so a batch of network fetches happens in the same
    sequence on every run — a rate-limited partial failure then hits the same
    names, which makes an A/B reproducible instead of luck-of-the-dict.
    """
    as_of_date = coerce_as_of(as_of)
    out: dict[str, float | None] = {}
    for symbol in sorted({_normalize_symbol(s) for s in (symbols or ()) if _normalize_symbol(s)}):
        out[symbol] = gross_profitability(symbol, as_of_date, **kwargs)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Cross-sectional ranking
# ──────────────────────────────────────────────────────────────────────────────

def cross_sectional_ranks(
    scores: Mapping[str, float | None],
    *,
    min_names: int = DEFAULT_MIN_RANKED_NAMES,
) -> dict[str, float | None]:
    """Percentile-rank a {symbol: gp_a} cross-section into [0.0, 1.0].

    0.0 = lowest gross profitability in the cross-section, 1.0 = highest.
    Symbols whose score is None/NaN (excluded, unavailable) map to None, NOT to
    0.0. A caller MUST NOT do `ranks.get(sym, 0.0)`: 0.0 is the strongest
    possible negative tilt, so that default would turn "we have no fundamentals
    for this name" into "short it". Use `NEUTRAL_PERCENTILE` if a number is
    structurally required.

    TIES ARE HANDLED SO THAT EQUAL INPUTS PRODUCE EQUAL OUTPUTS, ALWAYS.
    This is not pedantry. This codebase has already been bitten by a tied sort
    feeding a position-weighted decay, where the two orderings of a tie
    produced different weights and flipped a BUY into a SELL between two runs
    of identical inputs. Two defences here:

      1. the sort key is (quantized_value, symbol), which is a TOTAL order, so
         the result does not depend on input order or on sort stability;
      2. every member of a tie group receives the SAME percentile (the mean of
         the ranks the group spans), so even if the ordering within a group
         changed, no downstream weight could change.
    """
    valid: dict[str, float] = {}
    out: dict[str, float | None] = {}

    # Sorted iteration so a duplicate normalized key ("aapl" and "AAPL" in the
    # same mapping) resolves the same way on every run.
    for raw_symbol, raw_value in sorted(
        ((str(k), v) for k, v in (scores or {}).items()), key=lambda kv: kv[0]
    ):
        symbol = _normalize_symbol(raw_symbol)
        if not symbol:
            continue
        out[symbol] = None
        value = _num(raw_value)
        if value is not None:
            valid[symbol] = value

    n = len(valid)
    if n < max(1, int(min_names)):
        # Too thin to rank. Abstaining beats handing a 2-name book a 0.0/1.0
        # pair that looks like a decile sort.
        return out

    ordered = sorted(
        ((round(v, _TIE_DECIMALS), s) for s, v in valid.items()),
        key=lambda pair: (pair[0], pair[1]),
    )

    denominator = float(n - 1) if n > 1 else 1.0
    index = 0
    while index < n:
        stop = index
        while stop + 1 < n and ordered[stop + 1][0] == ordered[index][0]:
            stop += 1
        # Mean percentile of ranks index..stop (0-based ranks -> [0,1]).
        if n > 1:
            percentile = ((index + stop) / 2.0) / denominator
        else:
            percentile = NEUTRAL_PERCENTILE
        percentile = round(percentile, _PERCENTILE_DECIMALS)
        for position in range(index, stop + 1):
            out[ordered[position][1]] = percentile
        index = stop + 1
    return out


def rank_gross_profitability(
    symbols: Iterable[str],
    as_of: Any,
    *,
    min_names: int = DEFAULT_MIN_RANKED_NAMES,
    **kwargs: Any,
) -> dict[str, float | None]:
    """Convenience: compute GP/A for a universe and percentile-rank it."""
    return cross_sectional_ranks(
        gross_profitability_batch(symbols, as_of, **kwargs),
        min_names=min_names,
    )


# ──────────────────────────────────────────────────────────────────────────────
# yfinance fetcher (the only place in this module that touches the network)
# ──────────────────────────────────────────────────────────────────────────────

_STATEMENT_ROW_ALIASES: dict[str, tuple[str, ...]] = {
    # Yahoo renames rows between endpoint versions; match case/space-insensitively
    # against a list rather than a single literal so a rename degrades to
    # "missing" only when EVERY alias is gone.
    "gross_profit": ("gross profit", "grossprofit"),
    "total_revenue": ("total revenue", "totalrevenue", "operating revenue", "operatingrevenue"),
    "cost_of_revenue": ("cost of revenue", "costofrevenue", "reconciled cost of revenue",
                        "cost of goods sold", "costofgoodssold"),
    "total_assets": ("total assets", "totalassets"),
}


def _frame_rows(frame: Any) -> dict[str, Any]:
    """{lower-cased row label: original label} for a pandas-like frame."""
    rows: dict[str, Any] = {}
    try:
        for label in list(frame.index):
            key = str(label).strip().lower()
            # First alias wins — duplicate labels in a Yahoo frame are the same
            # row repeated, and picking deterministically beats picking last.
            rows.setdefault(key, label)
    except Exception:
        return {}
    return rows


def _frame_value(frame: Any, rows: dict[str, Any], field_name: str, column: Any) -> float | None:
    for alias in _STATEMENT_ROW_ALIASES.get(field_name, ()):
        label = rows.get(alias)
        if label is None:
            continue
        try:
            return _num(frame.at[label, column])
        except Exception:
            continue
    return None


def periods_from_frames(
    financials: Any,
    balance_sheet: Any,
    *,
    frequency: str = "annual",
) -> tuple[FiscalPeriod, ...]:
    """Zip a yfinance income-statement frame and balance-sheet frame into periods.

    Both frames are line-items x fiscal-period-end. A period is emitted ONLY
    when the SAME column date exists in both frames. Pairing a 2024 gross
    profit with a 2023 balance sheet would produce a plausible-looking ratio
    that is simply wrong, and nothing downstream could detect it.
    """
    if financials is None or balance_sheet is None:
        return ()
    income_rows = _frame_rows(financials)
    balance_rows = _frame_rows(balance_sheet)
    if not income_rows or not balance_rows:
        return ()

    balance_columns: dict[date, Any] = {}
    try:
        for column in list(balance_sheet.columns):
            column_date = _coerce_period_end(column)
            if column_date is not None:
                balance_columns.setdefault(column_date, column)
    except Exception:
        return ()

    periods: list[FiscalPeriod] = []
    seen: set[date] = set()
    try:
        income_columns = list(financials.columns)
    except Exception:
        return ()
    for column in income_columns:
        period_end = _coerce_period_end(column)
        if period_end is None or period_end in seen:
            continue
        balance_column = balance_columns.get(period_end)
        if balance_column is None:
            continue
        seen.add(period_end)
        total_assets = _frame_value(balance_sheet, balance_rows, "total_assets", balance_column)
        gross_profit = _frame_value(financials, income_rows, "gross_profit", column)
        total_revenue = _frame_value(financials, income_rows, "total_revenue", column)
        cost_of_revenue = _frame_value(financials, income_rows, "cost_of_revenue", column)
        # The REVT - COGS reconstruction is left to gp_a_from_period so the
        # cached record stays a faithful copy of what the vendor reported —
        # derivation policy belongs at read time, not in the cache.
        periods.append(FiscalPeriod(
            period_end=period_end,
            gross_profit=gross_profit,
            total_revenue=total_revenue,
            total_assets=total_assets,
            cost_of_revenue=cost_of_revenue,
            frequency=frequency,
        ))
    periods.sort(key=lambda p: p.period_end)
    return tuple(periods)


def yfinance_fetcher(symbol: str) -> FundamentalsRecord | None:
    """Default fetcher: annual statements + quote type/sector from yfinance.

    Imported lazily so this module can be imported (and tested) in an
    environment without yfinance, and so a Yahoo outage cannot break import of
    whatever ends up wiring the factor in.

    Ordering matters: the cheap profile lookup runs first, and a fund
    short-circuits before the two statement calls. Over a universe with a
    dozen ETFs in it that is a couple of dozen network calls saved per TTL.
    """
    sym = _normalize_symbol(symbol)
    if not sym:
        return None
    try:
        import yfinance as yf
    except Exception:
        return None

    fetched_at = time.time()
    try:
        ticker = yf.Ticker(sym)
    except Exception:
        return None

    quote_type = ""
    sector = ""
    try:
        info = ticker.info or {}
        quote_type = str(info.get("quoteType") or "").strip().upper()
        sector = str(info.get("sector") or "").strip()
    except Exception:
        # Yahoo 404s quoteSummary for many funds and for delisted tickers —
        # fundamentals_util already documents this. An empty profile is not a
        # fetch failure; the statement pull below still decides.
        pass

    if quote_type and quote_type in EXCLUDED_QUOTE_TYPES:
        return FundamentalsRecord(
            symbol=sym, quote_type=quote_type, sector=sector,
            periods=(), source="yfinance", fetched_at=fetched_at,
        )

    try:
        financials = ticker.financials
        balance_sheet = ticker.balance_sheet
    except Exception:
        return None

    periods = periods_from_frames(financials, balance_sheet, frequency="annual")
    if not periods and not quote_type:
        # Nothing at all came back and we cannot even say what this is. Return
        # None so the caller records a FAILURE (retried after the backoff)
        # rather than caching an empty record for the full TTL — the
        # empty-row-persists-forever bug from the overlay bars cache.
        return None
    return FundamentalsRecord(
        symbol=sym, quote_type=quote_type, sector=sector,
        periods=periods, source="yfinance", fetched_at=fetched_at,
    )


def static_fetcher(
    records: Mapping[str, FundamentalsRecord],
) -> Callable[[str], FundamentalsRecord | None]:
    """A fetcher over an in-memory mapping.

    Exists for tests and for an offline research run over a frozen fundamentals
    snapshot — the point-in-time story only holds if a backtest can be replayed
    from fixed data instead of whatever Yahoo says today.
    """
    frozen = {_normalize_symbol(k): v for k, v in (records or {}).items()}

    def _fetch(symbol: str) -> FundamentalsRecord | None:
        return frozen.get(_normalize_symbol(symbol))

    return _fetch
