"""Point-in-time gross profitability (Novy-Marx GP/A) — factor_profitability.py.

Every test here is HERMETIC: the fetcher is injected, so nothing touches
Yahoo. A factor test that hits the network is a test that goes red when a
vendor rate-limits, which is how a suite stops being trusted.

The contracts under test, in the order they matter:
  1. the reporting-lag rule (a fiscal period is INVISIBLE until it was public)
  2. staleness rejection
  3. fund / financial exclusion
  4. deterministic tie handling in the cross-sectional rank
  5. cache correctness — especially that changing as_of RE-DERIVES
  6. graceful degradation when the data source misbehaves
"""

import os
import sys
import threading
from datetime import date, datetime, timedelta, timezone

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import pytest

import factor_profitability as fp


UTC = timezone.utc


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ──────────────────────────────────────────────────────────────────────────────

def _period(period_end, gp=200.0, assets=1000.0, revenue=500.0, **kw):
    return fp.FiscalPeriod(
        period_end=date.fromisoformat(period_end) if isinstance(period_end, str) else period_end,
        gross_profit=gp,
        total_revenue=revenue,
        total_assets=assets,
        **kw,
    )


def _record(symbol="ACME", periods=(), quote_type="EQUITY", sector="Technology"):
    return fp.FundamentalsRecord(
        symbol=symbol, quote_type=quote_type, sector=sector,
        periods=tuple(periods), source="test", fetched_at=0.0,
    )


class _CountingFetcher:
    """Injected fetcher that records how many times the network *would* be hit."""

    def __init__(self, records=None, raises=None, returns_none=False):
        self.records = {k.upper(): v for k, v in (records or {}).items()}
        self.raises = raises
        self.returns_none = returns_none
        self.calls = []

    def __call__(self, symbol):
        self.calls.append(symbol)
        if self.raises is not None:
            raise self.raises
        if self.returns_none:
            return None
        return self.records.get(symbol.upper())

    @property
    def n(self):
        return len(self.calls)


def _fresh_cache(**kw):
    """A cache nobody else shares, so test order can never matter."""
    return fp.FundamentalsCache(**kw)


class _Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# ──────────────────────────────────────────────────────────────────────────────
# 1. The reporting-lag rule
# ──────────────────────────────────────────────────────────────────────────────

def test_period_is_invisible_before_its_availability_date():
    """The core anti-lookahead contract. AAPL's FY ends 2025-09-30; the figures
    are not knowable on 2025-10-01 because the 10-K has not been filed."""
    rec = _record(periods=[_period("2025-09-30", gp=200.0, assets=1000.0)])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache()

    # One day after the fiscal period end: nothing is public yet.
    assert fp.gross_profitability("ACME", "2025-10-01", fetcher=fetcher, cache=cache) is None
    # One day before availability (2025-09-30 + 120d = 2026-01-28).
    assert fp.gross_profitability("ACME", "2026-01-27", fetcher=fetcher, cache=cache) is None


def test_period_is_visible_on_and_after_its_availability_date():
    rec = _record(periods=[_period("2025-09-30", gp=200.0, assets=1000.0)])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache()

    availability = date(2025, 9, 30) + timedelta(days=fp.DEFAULT_REPORTING_LAG_DAYS)
    assert availability == date(2026, 1, 28)
    assert fp.gross_profitability("ACME", availability, fetcher=fetcher, cache=cache) == pytest.approx(0.2)
    assert fp.gross_profitability("ACME", "2026-02-15", fetcher=fetcher, cache=cache) == pytest.approx(0.2)


def test_lag_is_configurable_and_a_shorter_lag_reveals_the_period_sooner():
    rec = _record(periods=[_period("2025-09-30", gp=200.0, assets=1000.0)])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache()

    as_of = "2025-12-15"  # 76 days after period end
    assert fp.gross_profitability("ACME", as_of, fetcher=fetcher, cache=cache) is None
    assert fp.gross_profitability(
        "ACME", as_of, lag_days=60, fetcher=fetcher, cache=cache,
    ) == pytest.approx(0.2)
    # ...and a longer one hides it for longer.
    assert fp.gross_profitability(
        "ACME", "2026-02-15", lag_days=180, fetcher=fetcher, cache=cache,
    ) is None


def test_only_the_latest_public_period_is_used_not_the_latest_period():
    """The 2025 year exists in the record but is not yet public on 2025-11-01;
    the factor must fall back to the 2024 year, not peek."""
    rec = _record(periods=[
        _period("2024-09-30", gp=180.0, assets=1000.0),
        _period("2025-09-30", gp=250.0, assets=1000.0),
    ])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache()

    assert fp.gross_profitability("ACME", "2025-11-01", fetcher=fetcher, cache=cache) == pytest.approx(0.18)
    assert fp.gross_profitability("ACME", "2026-03-01", fetcher=fetcher, cache=cache) == pytest.approx(0.25)


def test_an_explicit_filing_date_overrides_the_lag_heuristic():
    """The documented upgrade path: a real EDGAR acceptance date wins."""
    rec = _record(periods=[
        _period("2025-09-30", gp=200.0, assets=1000.0, filed_at=date(2025, 10, 31)),
    ])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache()

    assert fp.gross_profitability("ACME", "2025-10-30", fetcher=fetcher, cache=cache) is None
    assert fp.gross_profitability("ACME", "2025-10-31", fetcher=fetcher, cache=cache) == pytest.approx(0.2)


def test_no_qualifying_period_reports_the_reason():
    rec = _record(periods=[_period("2025-09-30")])
    detail = fp.gross_profitability_detail(
        "ACME", "2025-10-01", fetcher=_CountingFetcher({"ACME": rec}), cache=_fresh_cache(),
    )
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_NO_QUALIFYING_PERIOD


# ──────────────────────────────────────────────────────────────────────────────
# 2. Staleness
# ──────────────────────────────────────────────────────────────────────────────

def test_a_period_more_than_two_years_stale_is_refused():
    rec = _record(periods=[_period("2023-12-31", gp=200.0, assets=1000.0)])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache()

    # 2023-12-31 + 730d = 2025-12-30. Inside the window it computes.
    assert fp.gross_profitability("ACME", "2025-12-30", fetcher=fetcher, cache=cache) == pytest.approx(0.2)
    # One day past it, the company has effectively stopped reporting.
    detail = fp.gross_profitability_detail("ACME", "2025-12-31", fetcher=fetcher, cache=cache)
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_STALE_PERIOD


def test_staleness_window_is_configurable():
    rec = _record(periods=[_period("2023-12-31", gp=200.0, assets=1000.0)])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache()
    assert fp.gross_profitability(
        "ACME", "2025-06-01", max_period_age_days=365, fetcher=fetcher, cache=cache,
    ) is None
    assert fp.gross_profitability(
        "ACME", "2025-06-01", max_period_age_days=900, fetcher=fetcher, cache=cache,
    ) == pytest.approx(0.2)


def test_stale_newest_period_does_not_silently_fall_back_to_an_older_one():
    """Reaching back would emit an even staler number under a fresh as_of."""
    rec = _record(periods=[
        _period("2021-12-31", gp=100.0, assets=1000.0),
        _period("2022-12-31", gp=200.0, assets=1000.0),
    ])
    detail = fp.gross_profitability_detail(
        "ACME", "2026-01-01", fetcher=_CountingFetcher({"ACME": rec}), cache=_fresh_cache(),
    )
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_STALE_PERIOD


# ──────────────────────────────────────────────────────────────────────────────
# 3. Exclusions
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("symbol", ["SPY", "QQQ", "SQQQ", "TQQQ", "VGT", "GLD", "BITO"])
def test_known_funds_are_excluded_without_a_network_call(symbol):
    fetcher = _CountingFetcher({})
    detail = fp.gross_profitability_detail(symbol, "2026-01-01", fetcher=fetcher, cache=_fresh_cache())
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_NOT_AN_OPERATING_COMPANY
    assert fetcher.n == 0, "an ETF must be screened before the fetch, not after"


@pytest.mark.parametrize("quote_type", ["ETF", "MUTUALFUND", "INDEX", "CURRENCY", "CRYPTOCURRENCY"])
def test_quote_type_exclusion_catches_funds_not_on_the_static_list(quote_type):
    """A brand-new ETF ticker will not be in the static set; the vendor's
    quoteType is the general defence."""
    rec = _record(symbol="NEWETF", quote_type=quote_type, sector="",
                  periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])
    detail = fp.gross_profitability_detail(
        "NEWETF", "2026-01-01", fetcher=_CountingFetcher({"NEWETF": rec}), cache=_fresh_cache(),
    )
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_NOT_AN_OPERATING_COMPANY


@pytest.mark.parametrize("sector", ["Financial Services", "financial services", "Financials"])
def test_financials_are_excluded(sector):
    """For a bank, total assets are the loan book: GP/A measures leverage."""
    rec = _record(symbol="JPM", sector=sector,
                  periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])
    detail = fp.gross_profitability_detail(
        "JPM", "2025-12-01", fetcher=_CountingFetcher({"JPM": rec}), cache=_fresh_cache(),
    )
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_EXCLUDED_SECTOR


def test_excluded_sectors_are_configurable():
    rec = _record(symbol="REIT", sector="Real Estate",
                  periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])
    fetcher = _CountingFetcher({"REIT": rec})
    # Default: real estate is NOT excluded (Novy-Marx drops financials only).
    assert fp.gross_profitability("REIT", "2025-12-01", fetcher=fetcher, cache=_fresh_cache()) == pytest.approx(0.2)
    # Caller opts in.
    assert fp.gross_profitability(
        "REIT", "2025-12-01", excluded_sectors={"real estate"},
        fetcher=fetcher, cache=_fresh_cache(),
    ) is None


@pytest.mark.parametrize("symbol", ["BTC/USD", "ETH/BTC", "^VIX", "EURUSD=X", "", "   "])
def test_non_equity_symbols_are_refused_before_any_fetch(symbol):
    """The crypto book runs through the same broker path; asking Yahoo for the
    gross profit of BTC/USD is nonsense a batch caller would otherwise do on
    every bar."""
    fetcher = _CountingFetcher({})
    detail = fp.gross_profitability_detail(symbol, "2026-01-01", fetcher=fetcher, cache=_fresh_cache())
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_BAD_SYMBOL
    assert fetcher.n == 0


def test_share_class_tickers_are_still_accepted():
    rec = _record(symbol="BRK-B", sector="Technology",
                  periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])
    assert fp.gross_profitability(
        "BRK-B", "2025-12-01", fetcher=_CountingFetcher({"BRK-B": rec}), cache=_fresh_cache(),
    ) == pytest.approx(0.2)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Data-quality — fail closed
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("assets,reason", [
    (0.0, fp.REASON_BAD_TOTAL_ASSETS),
    (-1000.0, fp.REASON_BAD_TOTAL_ASSETS),
    (None, fp.REASON_BAD_TOTAL_ASSETS),
    (float("nan"), fp.REASON_BAD_TOTAL_ASSETS),
])
def test_bad_total_assets_abstains(assets, reason):
    rec = _record(periods=[_period("2024-12-31", gp=200.0, assets=assets)])
    detail = fp.gross_profitability_detail(
        "ACME", "2025-12-01", fetcher=_CountingFetcher({"ACME": rec}), cache=_fresh_cache(),
    )
    assert detail["gp_a"] is None
    assert detail["reason"] == reason


def test_missing_gross_profit_abstains():
    rec = _record(periods=[_period("2024-12-31", gp=None, assets=1000.0, revenue=None)])
    detail = fp.gross_profitability_detail(
        "ACME", "2025-12-01", fetcher=_CountingFetcher({"ACME": rec}), cache=_fresh_cache(),
    )
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_MISSING_GROSS_PROFIT


def test_negative_gross_profit_is_a_real_number_and_is_kept():
    """Pre-revenue biotech is a legitimate bottom-decile name, not bad data."""
    rec = _record(periods=[_period("2024-12-31", gp=-50.0, assets=1000.0, revenue=10.0)])
    assert fp.gross_profitability(
        "ACME", "2025-12-01", fetcher=_CountingFetcher({"ACME": rec}), cache=_fresh_cache(),
    ) == pytest.approx(-0.05)


def test_gross_profit_exceeding_revenue_is_rejected_as_mis_paired_rows():
    rec = _record(periods=[_period("2024-12-31", gp=900.0, assets=1000.0, revenue=500.0)])
    detail = fp.gross_profitability_detail(
        "ACME", "2025-12-01", fetcher=_CountingFetcher({"ACME": rec}), cache=_fresh_cache(),
    )
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_GP_EXCEEDS_REVENUE


def test_implausible_ratio_is_rejected_as_a_units_bug():
    """Gross profit in dollars against total assets in thousands."""
    rec = _record(periods=[_period("2024-12-31", gp=50_000.0, assets=1000.0, revenue=None)])
    detail = fp.gross_profitability_detail(
        "ACME", "2025-12-01", fetcher=_CountingFetcher({"ACME": rec}), cache=_fresh_cache(),
    )
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_IMPLAUSIBLE_RATIO


def test_gross_profit_is_reconstructed_from_revenue_minus_cogs():
    """Novy-Marx's actual definition is REVT - COGS; using it when the vendor
    omits the pre-computed row is the definition, not a guess."""
    rec = _record(periods=[
        _period("2024-12-31", gp=None, assets=1000.0, revenue=500.0, cost_of_revenue=300.0),
    ])
    fetcher = _CountingFetcher({"ACME": rec})
    assert fp.gross_profitability(
        "ACME", "2025-12-01", fetcher=fetcher, cache=_fresh_cache(),
    ) == pytest.approx(0.2)
    # ...and the reconstruction can be switched off.
    assert fp.gross_profitability(
        "ACME", "2025-12-01", allow_cogs_fallback=False, fetcher=fetcher, cache=_fresh_cache(),
    ) is None


def test_apple_fiscal_years_reproduce_the_published_ratios():
    """Anchor on real AAPL figures ($M): FY2024 gross profit 180,683 on total
    assets 364,980 = 0.4950, the value observed from yfinance."""
    rec = _record(symbol="AAPL", sector="Technology", periods=[
        _period("2023-09-30", gp=169_148.0, assets=352_583.0, revenue=383_285.0),
        _period("2024-09-30", gp=180_683.0, assets=364_980.0, revenue=391_035.0),
    ])
    fetcher = _CountingFetcher({"AAPL": rec})
    cache = _fresh_cache()
    assert fp.gross_profitability("AAPL", "2024-06-30", fetcher=fetcher, cache=cache) == pytest.approx(0.4797, abs=1e-4)
    assert fp.gross_profitability("AAPL", "2025-06-30", fetcher=fetcher, cache=cache) == pytest.approx(0.4950, abs=1e-4)


# ──────────────────────────────────────────────────────────────────────────────
# 5. Cross-sectional ranks — determinism above all
# ──────────────────────────────────────────────────────────────────────────────

def test_percentile_ranks_span_zero_to_one_worst_to_best():
    ranks = fp.cross_sectional_ranks({"A": 0.1, "B": 0.2, "C": 0.3, "D": 0.4, "E": 0.5})
    assert ranks == {"A": 0.0, "B": 0.25, "C": 0.5, "D": 0.75, "E": 1.0}


def test_tied_values_receive_identical_percentiles():
    """The concrete failure this guards: a tied sort ahead of a position-weighted
    decay once flipped a BUY into a SELL between runs of identical inputs. Equal
    inputs must produce equal outputs so no downstream weight can differ."""
    ranks = fp.cross_sectional_ranks({"A": 0.10, "B": 0.10, "C": 0.30, "D": 0.40, "E": 0.50})
    assert ranks["A"] == ranks["B"]
    # The tie group spans 0-based ranks 0 and 1 -> mean percentile 0.5/4.
    assert ranks["A"] == pytest.approx(0.125)
    assert ranks["C"] == pytest.approx(0.5)


def test_ranks_are_independent_of_input_ordering():
    scores = {"AAA": 0.4, "BBB": 0.4, "CCC": 0.1, "DDD": 0.9, "EEE": 0.4}
    forward = fp.cross_sectional_ranks(scores)
    reversed_order = fp.cross_sectional_ranks(dict(reversed(list(scores.items()))))
    shuffled = fp.cross_sectional_ranks({k: scores[k] for k in ["DDD", "BBB", "EEE", "CCC", "AAA"]})
    assert forward == reversed_order == shuffled


def test_a_whole_group_of_ties_collapses_to_one_percentile():
    ranks = fp.cross_sectional_ranks({s: 0.25 for s in "ABCDEFG"})
    assert len(set(ranks.values())) == 1
    assert ranks["A"] == pytest.approx(0.5)


def test_float_dust_below_the_tie_tolerance_still_ties():
    """1e-12 apart is the same number wearing different float dust; letting the
    dust pick the ordering is how a sort stops being deterministic."""
    ranks = fp.cross_sectional_ranks({
        "A": 0.3, "B": 0.3 + 1e-12, "C": 0.5, "D": 0.7, "E": 0.9,
    })
    assert ranks["A"] == ranks["B"]


def test_genuinely_different_values_are_not_merged_by_the_tie_tolerance():
    ranks = fp.cross_sectional_ranks({
        "A": 0.30000001, "B": 0.30000002, "C": 0.5, "D": 0.7, "E": 0.9,
    })
    assert ranks["A"] < ranks["B"]


def test_unavailable_names_map_to_none_never_to_zero():
    """0.0 is the STRONGEST negative tilt; defaulting a missing name to it would
    turn 'no fundamentals' into 'short it'."""
    ranks = fp.cross_sectional_ranks({
        "A": 0.1, "B": None, "C": 0.3, "D": float("nan"), "E": 0.5, "F": 0.7, "G": 0.9,
    })
    assert ranks["B"] is None and ranks["D"] is None
    assert ranks["A"] == 0.0 and ranks["G"] == 1.0
    # Excluded names are still PRESENT in the result, so a caller iterating the
    # mapping sees an explicit "no opinion" rather than a silently absent key.
    assert set(ranks) == {"A", "B", "C", "D", "E", "F", "G"}


def test_a_cross_section_too_thin_to_rank_abstains_entirely():
    """With 2 names the loser gets 0.0 and the winner 1.0 — maximum-strength
    tilts from a single pairwise comparison."""
    assert fp.cross_sectional_ranks({"A": 0.1, "B": 0.9}) == {"A": None, "B": None}
    ranks = fp.cross_sectional_ranks({"A": 0.1, "B": 0.9}, min_names=2)
    assert ranks == {"A": 0.0, "B": 1.0}


def test_rank_symbols_are_normalized_and_deduplicated_deterministically():
    ranks = fp.cross_sectional_ranks({" aapl ": 0.5, "MSFT": 0.4, "NVDA": 0.3, "AMD": 0.2, "INTC": 0.1})
    assert "AAPL" in ranks and " aapl " not in ranks
    assert ranks["AAPL"] == 1.0


def test_rank_gross_profitability_end_to_end():
    records = {}
    for i, sym in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
        records[sym] = _record(symbol=sym, periods=[
            _period("2024-12-31", gp=100.0 * (i + 1), assets=1000.0, revenue=5000.0),
        ])
    records["SPY"] = _record(symbol="SPY", quote_type="ETF")
    fetcher = _CountingFetcher(records)
    ranks = fp.rank_gross_profitability(
        ["AAA", "BBB", "CCC", "DDD", "EEE", "SPY"], "2025-12-01",
        fetcher=fetcher, cache=_fresh_cache(),
    )
    assert ranks["SPY"] is None
    assert ranks["AAA"] == 0.0 and ranks["EEE"] == 1.0
    assert "SPY" not in fetcher.calls


# ──────────────────────────────────────────────────────────────────────────────
# 6. Cache
# ──────────────────────────────────────────────────────────────────────────────

def test_repeated_calls_hit_the_network_once():
    rec = _record(periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache()
    for _ in range(25):
        fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=cache)
    assert fetcher.n == 1


def test_changing_as_of_re_derives_instead_of_serving_a_stale_answer():
    """THE cache test. GraphNexusOverlayBarsCache was keyed by symbol alone
    while its value depended on a setting outside the key, and served raw bars
    as split-adjusted. Here the cached value is as-of-INDEPENDENT raw
    statements, so one fetch must still produce different answers per as_of."""
    rec = _record(periods=[
        _period("2023-12-31", gp=100.0, assets=1000.0, revenue=5000.0),
        _period("2024-12-31", gp=300.0, assets=1000.0, revenue=5000.0),
    ])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache()

    early = fp.gross_profitability("ACME", "2024-06-01", fetcher=fetcher, cache=cache)
    late = fp.gross_profitability("ACME", "2025-06-01", fetcher=fetcher, cache=cache)
    back_to_early = fp.gross_profitability("ACME", "2024-06-01", fetcher=fetcher, cache=cache)

    assert fetcher.n == 1, "the raw statements are as-of independent: one fetch"
    assert early == pytest.approx(0.10)
    assert late == pytest.approx(0.30)
    assert back_to_early == pytest.approx(0.10), "a later as_of must not poison an earlier one"


def test_changing_the_lag_re_derives_without_a_refetch():
    """The whole reason the cache holds RAW statements: the lag rule is policy,
    and policy will change when real EDGAR filing dates land."""
    rec = _record(periods=[_period("2025-09-30", gp=200.0, assets=1000.0)])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache()

    assert fp.gross_profitability("ACME", "2025-12-15", lag_days=120, fetcher=fetcher, cache=cache) is None
    assert fp.gross_profitability("ACME", "2025-12-15", lag_days=60, fetcher=fetcher, cache=cache) == pytest.approx(0.2)
    assert fetcher.n == 1


def test_cache_refuses_to_store_a_derived_value():
    """Typed guard against re-introducing the as-of-free derived cache bug."""
    cache = _fresh_cache()
    with pytest.raises(TypeError):
        cache.put(0.42)
    with pytest.raises(TypeError):
        cache.put({"symbol": "ACME", "gp_a": 0.42})


def test_cache_entries_expire_and_refetch():
    clock = _Clock()
    rec = _record(periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache(ttl_seconds=100.0, clock=clock)

    fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=cache)
    clock.advance(99)
    fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=cache)
    assert fetcher.n == 1
    clock.advance(2)
    fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=cache)
    assert fetcher.n == 2


def test_a_schema_version_bump_invalidates_stored_entries():
    """Mirrors the `adjustment` mismatch check in the overlay-bars cache: a row
    written under an older shape is a MISS, never a coerced read."""
    cache = _fresh_cache()
    cache.put(_record(periods=[_period("2024-12-31")]))
    assert cache.peek("ACME") is not None
    original = fp.CACHE_SCHEMA_VERSION
    try:
        fp.CACHE_SCHEMA_VERSION = original + 1
        assert cache.peek("ACME") is None
    finally:
        fp.CACHE_SCHEMA_VERSION = original


def test_cache_is_symbol_normalized():
    cache = _fresh_cache()
    fetcher = _CountingFetcher({"ACME": _record(periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])})
    fp.gross_profitability(" acme ", "2025-12-01", fetcher=fetcher, cache=cache)
    fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=cache)
    assert fetcher.n == 1


def test_cache_stats_report_hits_misses_and_fetches():
    cache = _fresh_cache()
    fetcher = _CountingFetcher({"ACME": _record(periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])})
    fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=cache)
    fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=cache)
    stats = cache.stats()
    assert stats["fetches"] == 1 and stats["hits"] == 1 and stats["misses"] == 1
    cache.clear()
    assert cache.stats()["entries"] == 0


def test_module_level_cache_can_be_cleared():
    """The live loop shares one process-wide cache; the operator needs a way to
    force a refetch after a restatement without bouncing the container."""
    fetcher = _CountingFetcher({"ACME": _record(periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])})
    fp.clear_fundamentals_cache()
    try:
        fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher)
        fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher)
        assert fetcher.n == 1
        fp.clear_fundamentals_cache()
        fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher)
        assert fetcher.n == 2
    finally:
        fp.clear_fundamentals_cache()


def test_concurrent_readers_share_one_cached_record():
    rec = _record(periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])
    fetcher = _CountingFetcher({"ACME": rec})
    cache = _fresh_cache()
    cache.put(rec)  # pre-warm so the assertion is about reads, not fetch races
    results = []

    def _worker():
        results.append(fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=cache))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [pytest.approx(0.2)] * 8
    assert fetcher.n == 0


# ──────────────────────────────────────────────────────────────────────────────
# 7. Graceful degradation
# ──────────────────────────────────────────────────────────────────────────────

def test_a_raising_fetcher_abstains_instead_of_propagating():
    """yfinance raises HTTPError / KeyError / JSONDecodeError depending on which
    Yahoo endpoint is unhappy. None of that may reach the trading loop."""
    fetcher = _CountingFetcher(raises=RuntimeError("Yahoo 429"))
    detail = fp.gross_profitability_detail("ACME", "2025-12-01", fetcher=fetcher, cache=_fresh_cache())
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_FETCH_FAILED


def test_a_fetcher_returning_none_abstains():
    fetcher = _CountingFetcher(returns_none=True)
    assert fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=_fresh_cache()) is None


def test_an_empty_record_is_no_data_not_a_zero():
    rec = _record(periods=[])
    detail = fp.gross_profitability_detail(
        "ACME", "2025-12-01", fetcher=_CountingFetcher({"ACME": rec}), cache=_fresh_cache(),
    )
    assert detail["gp_a"] is None
    assert detail["reason"] == fp.REASON_NO_DATA


def test_a_failed_fetch_is_not_cached_as_data_and_is_backed_off():
    """An empty result must never be persisted as if it were data — the
    2026-07-19 overlay-bars post-mortem found empty rows persisting forever and
    permanently blinding the regime detector. But it must not be retried once
    per symbol per bar either."""
    clock = _Clock()
    fetcher = _CountingFetcher(returns_none=True)
    cache = _fresh_cache(failure_retry_seconds=300.0, clock=clock)

    fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=cache)
    for _ in range(10):
        fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=cache)
    assert fetcher.n == 1, "a down vendor must not be re-hit every bar"
    assert cache.peek("ACME") is None, "an empty result must not be cached as data"

    clock.advance(301)
    fp.gross_profitability("ACME", "2025-12-01", fetcher=fetcher, cache=cache)
    assert fetcher.n == 2, "the backoff must expire"


def test_a_successful_fetch_clears_an_earlier_failure_backoff():
    clock = _Clock()
    rec = _record(periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])
    cache = _fresh_cache(failure_retry_seconds=300.0, clock=clock)
    cache.note_failure("ACME")
    assert cache.in_failure_backoff("ACME")
    cache.put(rec)
    assert not cache.in_failure_backoff("ACME")


def test_batch_returns_every_input_symbol():
    records = {"AAA": _record(symbol="AAA", periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])}
    out = fp.gross_profitability_batch(
        ["AAA", "SPY", "BTC/USD", "zzz"], "2025-12-01",
        fetcher=_CountingFetcher(records), cache=_fresh_cache(),
    )
    assert set(out) == {"AAA", "SPY", "BTC/USD", "ZZZ"}
    assert out["AAA"] == pytest.approx(0.2)
    assert out["SPY"] is None and out["BTC/USD"] is None and out["ZZZ"] is None


# ──────────────────────────────────────────────────────────────────────────────
# 8. as_of coercion
# ──────────────────────────────────────────────────────────────────────────────

def test_as_of_accepts_dates_datetimes_and_iso_strings():
    assert fp.coerce_as_of(date(2025, 6, 1)) == date(2025, 6, 1)
    assert fp.coerce_as_of(datetime(2025, 6, 1, 20, 0, tzinfo=UTC)) == date(2025, 6, 1)
    assert fp.coerce_as_of("2025-06-01") == date(2025, 6, 1)
    assert fp.coerce_as_of("2025-06-01T20:00:00Z") == date(2025, 6, 1)


def test_a_naive_datetime_is_read_as_utc():
    assert fp.coerce_as_of(datetime(2025, 6, 1, 23, 30)) == date(2025, 6, 1)


def test_as_of_accepts_a_point_in_time_context_like_object():
    class _Ctx:
        as_of = datetime(2025, 6, 1, 20, 0, tzinfo=UTC)

    assert fp.coerce_as_of(_Ctx()) == date(2025, 6, 1)


@pytest.mark.parametrize("bad", [None, "", "not-a-date", 12345, object()])
def test_an_unparseable_as_of_raises_rather_than_abstaining(bad):
    """Every DATA failure abstains; a caller bug must be loud, not hidden behind
    a factor that merely looks unhelpful across the whole universe."""
    with pytest.raises(ValueError):
        fp.coerce_as_of(bad)
    with pytest.raises(ValueError):
        fp.gross_profitability("ACME", bad, fetcher=_CountingFetcher({}), cache=_fresh_cache())


# ──────────────────────────────────────────────────────────────────────────────
# 9. yfinance frame parsing (no network — pandas frames built inline)
# ──────────────────────────────────────────────────────────────────────────────

def _frames(income_rows, balance_rows, income_cols, balance_cols=None):
    pd = pytest.importorskip("pandas")
    balance_cols = balance_cols if balance_cols is not None else income_cols
    financials = pd.DataFrame.from_dict(income_rows, orient="index", columns=income_cols)
    balance_sheet = pd.DataFrame.from_dict(balance_rows, orient="index", columns=balance_cols)
    return financials, balance_sheet


def test_periods_from_frames_pairs_matching_fiscal_period_ends():
    cols = [date(2024, 9, 30), date(2023, 9, 30)]
    financials, balance_sheet = _frames(
        {"Gross Profit": [180_683.0, 169_148.0], "Total Revenue": [391_035.0, 383_285.0]},
        {"Total Assets": [364_980.0, 352_583.0]},
        cols,
    )
    periods = fp.periods_from_frames(financials, balance_sheet)
    assert [p.period_end for p in periods] == [date(2023, 9, 30), date(2024, 9, 30)]
    gp_a, reason = fp.gp_a_from_period(periods[-1])
    assert reason == fp.REASON_OK
    assert gp_a == pytest.approx(0.4950, abs=1e-4)


def test_periods_from_frames_never_pairs_mismatched_period_ends():
    """A 2024 gross profit against a 2023 balance sheet yields a plausible
    number that is simply wrong, and nothing downstream could detect it."""
    financials, balance_sheet = _frames(
        {"Gross Profit": [180_683.0], "Total Revenue": [391_035.0]},
        {"Total Assets": [352_583.0]},
        [date(2024, 9, 30)],
        balance_cols=[date(2023, 9, 30)],
    )
    assert fp.periods_from_frames(financials, balance_sheet) == ()


def test_periods_from_frames_tolerates_row_label_renames():
    financials, balance_sheet = _frames(
        {"Operating Revenue": [500.0], "Cost Of Revenue": [300.0]},
        {"TotalAssets": [1000.0]},
        [date(2024, 12, 31)],
    )
    periods = fp.periods_from_frames(financials, balance_sheet)
    assert len(periods) == 1
    assert periods[0].total_assets == pytest.approx(1000.0)
    gp_a, reason = fp.gp_a_from_period(periods[0])
    assert reason == fp.REASON_OK and gp_a == pytest.approx(0.2)


def test_periods_from_frames_survives_empty_or_missing_frames():
    pd = pytest.importorskip("pandas")
    assert fp.periods_from_frames(None, None) == ()
    assert fp.periods_from_frames(pd.DataFrame(), pd.DataFrame()) == ()


def test_periods_from_frames_keeps_nan_cells_as_missing():
    financials, balance_sheet = _frames(
        {"Gross Profit": [float("nan")], "Total Revenue": [500.0]},
        {"Total Assets": [1000.0]},
        [date(2024, 12, 31)],
    )
    periods = fp.periods_from_frames(financials, balance_sheet)
    assert periods[0].gross_profit is None
    gp_a, reason = fp.gp_a_from_period(periods[0])
    assert gp_a is None and reason == fp.REASON_MISSING_GROSS_PROFIT


def test_yfinance_fetcher_short_circuits_a_fund_before_pulling_statements(monkeypatch):
    """Hermetic exercise of the real fetcher against a stub module."""
    calls = {"statements": 0}

    class _Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def info(self):
            return {"quoteType": "ETF", "sector": ""}

        @property
        def financials(self):
            calls["statements"] += 1
            raise AssertionError("must not pull statements for a fund")

        balance_sheet = financials

    monkeypatch.setitem(sys.modules, "yfinance", type(sys)("yfinance"))
    sys.modules["yfinance"].Ticker = _Ticker

    record = fp.yfinance_fetcher("NEWETF")
    assert record is not None
    assert record.quote_type == "ETF" and record.periods == ()
    assert calls["statements"] == 0


def test_yfinance_fetcher_returns_none_when_nothing_comes_back(monkeypatch):
    """No profile AND no statements: a FAILURE (retried after backoff), not an
    empty record cached for the full TTL."""
    class _Ticker:
        def __init__(self, symbol):
            pass

        @property
        def info(self):
            return {}

        @property
        def financials(self):
            return None

        @property
        def balance_sheet(self):
            return None

    monkeypatch.setitem(sys.modules, "yfinance", type(sys)("yfinance"))
    sys.modules["yfinance"].Ticker = _Ticker
    assert fp.yfinance_fetcher("ACME") is None


def test_yfinance_fetcher_builds_a_record_from_frames(monkeypatch):
    pd = pytest.importorskip("pandas")
    financials = pd.DataFrame.from_dict(
        {"Gross Profit": [180_683.0], "Total Revenue": [391_035.0]},
        orient="index", columns=[date(2024, 9, 30)],
    )
    balance_sheet = pd.DataFrame.from_dict(
        {"Total Assets": [364_980.0]}, orient="index", columns=[date(2024, 9, 30)],
    )

    class _Ticker:
        def __init__(self, symbol):
            pass

        info = {"quoteType": "EQUITY", "sector": "Technology"}

        @property
        def financials(self):
            return financials

        @property
        def balance_sheet(self):
            return balance_sheet

    monkeypatch.setitem(sys.modules, "yfinance", type(sys)("yfinance"))
    sys.modules["yfinance"].Ticker = _Ticker

    record = fp.yfinance_fetcher("aapl")
    assert record.symbol == "AAPL" and record.sector == "Technology"
    assert len(record.periods) == 1
    assert fp.gross_profitability(
        "AAPL", "2025-06-30", fetcher=fp.yfinance_fetcher, cache=_fresh_cache(),
    ) == pytest.approx(0.4950, abs=1e-4)


def test_static_fetcher_supports_an_offline_frozen_snapshot():
    """The point-in-time story only holds if a research run can be replayed from
    fixed data instead of whatever Yahoo says today."""
    rec = _record(periods=[_period("2024-12-31", gp=200.0, assets=1000.0)])
    fetch = fp.static_fetcher({"acme": rec})
    assert fetch("ACME") is rec
    assert fetch("NOPE") is None
