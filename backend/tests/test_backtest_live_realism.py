"""The emulator must not fill what the live broker would refuse, and must not
hand the strategy money the live account has not released.

Every number this project has published came out of an emulator that modelled
equities as commission-free with no spread and no slippage, credited 100% of
sell proceeds the instant a fill landed, filled orders the broker rejects on
notional, and credited zero dividends while scoring itself against a
total-return SPY series. These tests pin each of those closed.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.portfolio_emulator import (
    DEFAULT_EQUITY_DIVIDEND_YIELDS,
    LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL,
    MIN_EQUITY_ORDER_NOTIONAL,
    PortfolioEmulator,
    SETTLED_SELL_PROCEEDS_FRACTION,
    create_backtest_emulator,
)
from backend.simulated_execution import (
    DEFAULT_EQUITY_EXECUTION_COST_MODEL,
    ExecutionCostModel,
    SimulationQuote,
)


T0 = datetime(2026, 3, 2, 14, 0, tzinfo=timezone.utc)
T1 = datetime(2026, 3, 2, 15, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 3, 2, 16, 0, tzinfo=timezone.utc)


def _quote(symbol, at, mid, model=LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL):
    return SimulationQuote.from_mid(
        symbol=symbol, timestamp=at, mid=mid, spread_bps=model.spread_bps
    )


# --------------------------------------------------------------------------
# 1. A realistic, configurable equity cost model
# --------------------------------------------------------------------------


def test_default_cost_model_matches_the_measured_nbbo_cost():
    """~23 bps one-way, measured -- not the ~12.8 bps the nominal model charges.

    2026-08-02: priced 61 of alpaca-main's 62 real live fills against the SIP
    NBBO at each fill timestamp. Notional-weighted spread 45.6 bps, slippage
    +0.1 bps => 23.2 bps one-way. The band is +/-3 bps around that.

    The earlier 25-35 band came from a liquidity MODEL that assumed 18 bps of
    slippage. The measurement says slippage is ~zero -- $500 orders cannot move
    a book, and market-maker routing gives price improvement about as often as
    it costs. Use the NOTIONAL-WEIGHTED spread, never the median: the median
    trade sees 17.5 bps, but larger trades sit in wider-spread names, so the
    median understates money actually paid by 2.6x.
    """
    model = LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL
    one_way = model.spread_bps / 2.0 + model.slippage_bps + model.fee_bps
    assert 20.0 <= one_way <= 26.0
    assert model.slippage_bps <= 1.0, "measured slippage was ~0; do not reintroduce a modelled 18 bps"
    nominal = (
        DEFAULT_EQUITY_EXECUTION_COST_MODEL.spread_bps / 2.0
        + DEFAULT_EQUITY_EXECUTION_COST_MODEL.slippage_bps
        + DEFAULT_EQUITY_EXECUTION_COST_MODEL.fee_bps
    )
    # Measured 23.2 vs nominal 12.8 = 1.81x. The nominal model is still far too
    # cheap for this book, but the true multiple is 1.8x, not the 2.4x the
    # pre-measurement model implied.
    assert one_way > 1.5 * nominal
    # All three components are separately real and separately configurable.
    assert model.spread_bps > 0 and model.slippage_bps > 0 and model.fee_bps > 0


def test_factory_substitutes_the_nominal_model_but_honours_an_explicit_one():
    """Passing the nominal model means "nobody chose"; a stress arm is a choice."""
    substituted = create_backtest_emulator(
        initial_cash=6_000.0,
        taker_fee=None,
        is_crypto=False,
        execution_delay=timedelta(hours=1),
        cost_model=DEFAULT_EQUITY_EXECUTION_COST_MODEL,
    )
    assert (
        substituted.get_execution_summary()["execution_cost_model_version"]
        == LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL.version
    )
    # The substitution is auditable, not silent.
    assert (
        substituted.get_realism_summary()["equity_cost_model_requested_version"]
        == DEFAULT_EQUITY_EXECUTION_COST_MODEL.version
    )

    stress = ExecutionCostModel(
        version="equity-next-event-v1+stress50",
        spread_bps=19.5,
        slippage_bps=39.1,
        fee_bps=1.2,
        latency=timedelta(0),
    )
    explicit = create_backtest_emulator(
        initial_cash=6_000.0,
        taker_fee=None,
        is_crypto=False,
        execution_delay=timedelta(hours=1),
        cost_model=stress,
    )
    assert (
        explicit.get_execution_summary()["execution_cost_model_version"]
        == stress.version
    )
    assert (
        explicit.get_realism_summary()["equity_cost_model_requested_version"]
        is None
    )


def test_nominal_model_can_still_be_forced_for_reproducing_old_runs():
    legacy = create_backtest_emulator(
        initial_cash=6_000.0,
        taker_fee=None,
        is_crypto=False,
        execution_delay=timedelta(hours=1),
        cost_model=DEFAULT_EQUITY_EXECUTION_COST_MODEL,
        allow_nominal_cost_model=True,
    )
    assert (
        legacy.get_execution_summary()["execution_cost_model_version"]
        == DEFAULT_EQUITY_EXECUTION_COST_MODEL.version
    )


def test_legacy_and_next_event_paths_price_an_identical_fill_identically():
    """A crypto-kind instance holding an equity ticker uses the legacy path.

    Before this, that ticker filled free. The two paths must not disagree about
    what a fill costs, or the same trade is worth different money depending on
    which instance kind happened to run it.
    """
    legacy = PortfolioEmulator(10_000.0)
    assert legacy.buy("AAPL", 5.0, 100.0, timestamp=T0)
    legacy_trade = legacy.get_trade_history()[-1]

    simulated = create_backtest_emulator(
        initial_cash=10_000.0,
        taker_fee=None,
        is_crypto=False,
        execution_delay=timedelta(hours=1),
    )
    simulated.execute_signal(
        "AAPL",
        1,
        100.0,
        timestamp=T0,
        cash_per_trade=legacy_trade["total"],
        order_source="main_signal",
    )
    fills = simulated.process_quote(_quote("AAPL", T1, 100.0))

    assert fills
    assert fills[0].price == pytest.approx(legacy_trade["price"], rel=1e-12)


def test_crypto_fee_handling_does_not_regress():
    """Crypto still pays exactly one taker fee per leg and nothing else."""
    pe = PortfolioEmulator(10_000.0, taker_fee=0.0025)
    assert pe.execute_signal("BTC/USD", 1, 50_000.0, cash_per_trade=1_000.0)
    assert pe.get_positions()["BTC/USD"] == pytest.approx(0.02 * 0.9975)
    assert pe.get_cash() == pytest.approx(9_000.0)
    summary = pe.get_fee_summary()
    assert summary["total_fees"] == pytest.approx(2.5)
    assert summary["taker_rate"] == 0.0025
    # No equity spread/slippage leaked onto a crypto fill.
    realism = pe.get_realism_summary()
    assert realism["legacy_equity_fees"] == 0.0
    assert realism["legacy_equity_spread_cost"] == 0.0
    assert realism["legacy_equity_slippage_cost"] == 0.0


# --------------------------------------------------------------------------
# 2. Dividends / return basis
# --------------------------------------------------------------------------


def test_held_equities_accrue_the_distributions_the_price_series_omits():
    """Bars are adjustment="split", so the curve carries no distributions.

    Verified 2026-08-02 against the live cache: 4,000/4,000 sampled
    AlpacaBarsCache chunks hash under adjustment="split". The SPY benchmark is
    fetched with adjustment="all" (total return), so without this the strategy
    was charged SPY's dividends and credited none of its own.
    """
    pe = PortfolioEmulator(10_000.0)
    pe.buy("SPY", 10.0, 100.0, timestamp=T0)
    pe.save_portfolio_snapshot({"SPY": 100.0}, timestamp=T0)
    cash_before = pe.get_cash()

    one_year_later = T0 + timedelta(days=365)
    pe.save_portfolio_snapshot({"SPY": 100.0}, timestamp=one_year_later)

    credited = pe.get_cash() - cash_before
    assert credited == pytest.approx(
        1_000.0 * DEFAULT_EQUITY_DIVIDEND_YIELDS["SPY"], rel=1e-9
    )
    summary = pe.get_dividend_summary()
    assert summary["return_basis"] == "total_return_accrued"
    assert summary["benchmark_return_basis"] == "total_return"
    assert summary["dividends_by_symbol"]["SPY"] == pytest.approx(credited)


def test_accrual_is_calendar_time_so_weekends_are_not_skipped():
    pe = PortfolioEmulator(10_000.0)
    pe.buy("SPY", 10.0, 100.0, timestamp=T0)
    pe.save_portfolio_snapshot({"SPY": 100.0}, timestamp=T0)
    cash_before = pe.get_cash()
    pe.save_portfolio_snapshot({"SPY": 100.0}, timestamp=T0 + timedelta(days=3))
    assert pe.get_cash() - cash_before == pytest.approx(
        1_000.0 * DEFAULT_EQUITY_DIVIDEND_YIELDS["SPY"] * 3.0 / 365.0,
        rel=1e-9,
    )


def test_unknown_symbols_accrue_nothing_by_default():
    """The discovered microcap universe mostly pays nothing; assume so."""
    pe = PortfolioEmulator(10_000.0)
    pe.buy("NAK", 100.0, 10.0, timestamp=T0)
    pe.save_portfolio_snapshot({"NAK": 10.0}, timestamp=T0)
    cash_before = pe.get_cash()
    pe.save_portfolio_snapshot(
        {"NAK": 10.0}, timestamp=T0 + timedelta(days=365)
    )
    assert pe.get_cash() == pytest.approx(cash_before)
    assert pe.get_dividend_summary()["return_basis"] == "price_return"


def test_dividend_yields_are_configurable_and_crypto_never_accrues():
    pe = PortfolioEmulator(
        10_000.0,
        dividend_yields={"NAK": 0.10},
        taker_fee=0.0,
    )
    pe.buy("NAK", 100.0, 10.0, timestamp=T0)
    pe.buy("BTC/USD", 0.01, 50_000.0, timestamp=T0)
    pe.save_portfolio_snapshot({"NAK": 10.0, "BTC/USD": 50_000.0}, timestamp=T0)
    cash_before = pe.get_cash()
    pe.save_portfolio_snapshot(
        {"NAK": 10.0, "BTC/USD": 50_000.0},
        timestamp=T0 + timedelta(days=365),
    )
    assert pe.get_cash() - cash_before == pytest.approx(1_000.0 * 0.10, rel=1e-9)
    assert "BTC/USD" not in pe.get_dividend_summary()["dividends_by_symbol"]


def test_explicit_distribution_events_credit_the_position():
    pe = PortfolioEmulator(10_000.0, dividend_yields={})
    pe.buy("SPY", 10.0, 100.0, timestamp=T0)
    cash_before = pe.get_cash()
    assert pe.apply_dividend("SPY", 1.75, timestamp=T1) == pytest.approx(17.5)
    assert pe.get_cash() - cash_before == pytest.approx(17.5)
    assert pe.get_trade_history()[-1]["action"] == "dividend"
    # Nothing held, nothing paid.
    assert pe.apply_dividend("AAPL", 1.75, timestamp=T1) == 0.0


# --------------------------------------------------------------------------
# 3. Orders that could never fill live
# --------------------------------------------------------------------------


def test_sub_dollar_equity_orders_are_refused_on_both_paths():
    """219 of 260 trades in the best backtest to date were sub-$1.

    That is $52 of $16,286 of notional and ~84% of the trade count: the
    headline number was produced almost entirely by orders Alpaca rejects.
    """
    legacy = PortfolioEmulator(10_000.0)
    assert legacy.buy("AAPL", 0.005, 100.0, timestamp=T0) is False
    assert legacy.get_positions() == {}
    assert legacy.get_realism_summary()["sub_minimum_rejected_order_count"] == 1

    simulated = create_backtest_emulator(
        initial_cash=10_000.0,
        taker_fee=None,
        is_crypto=False,
        execution_delay=timedelta(hours=1),
    )
    assert (
        simulated.execute_signal(
            "AAPL",
            1,
            100.0,
            timestamp=T0,
            cash_per_trade=0.50,
            order_source="main_signal",
        )
        is False
    )
    assert simulated.pending_execution_symbols() == ()
    assert (
        simulated.get_execution_summary()["sub_minimum_rejected_order_count"]
        == 1
    )


def test_sub_dollar_dust_cannot_be_sold_either():
    """A fractional position worth under $1 is genuinely stuck live."""
    pe = PortfolioEmulator(10_000.0)
    assert pe.buy("AAPL", 5.0, 100.0, timestamp=T0)
    held = pe.get_positions()["AAPL"]
    assert pe.sell("AAPL", held, 100.0, timestamp=T1)
    assert pe.buy("AAPL", 5.0, 100.0, timestamp=T1)
    # Crash to a price where the whole remaining stake is worth pennies.
    assert pe.sell("AAPL", pe.get_positions()["AAPL"], 0.10, timestamp=T2) is False
    assert "AAPL" in pe.get_positions()


def test_crypto_is_exempt_from_the_equity_dollar_floor():
    pe = PortfolioEmulator(10_000.0, taker_fee=0.0025)
    assert pe.buy("BTC/USD", 0.000004, 50_000.0, timestamp=T0)  # $0.20
    assert pe.get_realism_summary()["sub_minimum_rejected_order_count"] == 0


def test_the_floor_is_configurable():
    pe = PortfolioEmulator(10_000.0, min_order_notional=0.0)
    assert pe.buy("AAPL", 0.005, 100.0, timestamp=T0)
    assert pe.get_realism_summary()["min_order_notional"] == 0.0
    assert MIN_EQUITY_ORDER_NOTIONAL == 1.0


# --------------------------------------------------------------------------
# 4. T+1 settlement
# --------------------------------------------------------------------------


def test_unsettled_sell_proceeds_are_not_immediately_spendable():
    """Cash and buying power are different numbers; conflating them lets a
    backtest recycle capital faster than the account can."""
    pe = PortfolioEmulator(10_000.0)
    assert pe.buy("AAPL", 50.0, 100.0, timestamp=T0)
    cash_before_sale = pe.get_cash()
    assert pe.sell("AAPL", 50.0, 100.0, timestamp=T1)

    proceeds = pe.get_cash() - cash_before_sale
    withheld = proceeds * (1.0 - SETTLED_SELL_PROCEEDS_FRACTION)
    assert pe.get_buying_power() == pytest.approx(pe.get_cash() - withheld)
    assert pe.get_available_cash() == pytest.approx(pe.get_cash() - withheld)
    # NAV is unaffected — the money is in the account, just not released.
    assert pe.get_portfolio_value({}) == pytest.approx(pe.get_cash())
    assert pe.get_realism_summary()["unsettled_cash"] == pytest.approx(withheld)


def test_withheld_proceeds_settle_at_t_plus_one():
    pe = PortfolioEmulator(10_000.0)
    assert pe.buy("AAPL", 50.0, 100.0, timestamp=T0)
    assert pe.sell("AAPL", 50.0, 100.0, timestamp=T1)
    assert pe.get_buying_power() < pe.get_cash()

    pe.save_portfolio_snapshot({}, timestamp=T1 + timedelta(days=1, seconds=1))
    assert pe.get_buying_power() == pytest.approx(pe.get_cash())
    assert pe.get_realism_summary()["unsettled_cash"] == 0.0


def test_settlement_is_configurable_and_crypto_settles_instantly():
    instant = PortfolioEmulator(
        10_000.0, settled_sell_proceeds_fraction=1.0
    )
    assert instant.buy("AAPL", 50.0, 100.0, timestamp=T0)
    assert instant.sell("AAPL", 50.0, 100.0, timestamp=T1)
    assert instant.get_buying_power() == pytest.approx(instant.get_cash())

    crypto = PortfolioEmulator(10_000.0, taker_fee=0.0025)
    assert crypto.buy("BTC/USD", 0.1, 50_000.0, timestamp=T0)
    assert crypto.sell("BTC/USD", 0.05, 50_000.0, timestamp=T1)
    assert crypto.get_buying_power() == pytest.approx(crypto.get_cash())


def test_a_buy_cannot_spend_unsettled_proceeds():
    pe = PortfolioEmulator(1_000.0)
    assert pe.buy("AAPL", 9.0, 100.0, timestamp=T0)   # ~$900 out
    assert pe.sell("AAPL", 9.0, 100.0, timestamp=T1)  # ~$897 back, 5% withheld
    cash = pe.get_cash()
    # Asking for every dollar of cash must be refused: part of it is unsettled.
    assert pe.buy("AAPL", cash / 100.0, 100.0, timestamp=T1) is False
    assert pe.buy("AAPL", pe.get_buying_power() / 101.0, 100.0, timestamp=T1)


# --------------------------------------------------------------------------
# 5. Other ways the old emulator flattered a run
# --------------------------------------------------------------------------


def test_a_fresh_buy_is_marked_at_the_mid_not_at_its_own_fill():
    """Marking at the buy fill inflated every new position by the half-spread
    plus slippage and carried it forward through the last-price fallback."""
    emulator = create_backtest_emulator(
        initial_cash=10_000.0,
        taker_fee=None,
        is_crypto=False,
        execution_delay=timedelta(hours=1),
    )
    emulator.execute_signal(
        "AAPL",
        1,
        100.0,
        timestamp=T0,
        cash_per_trade=1_000.0,
        order_source="main_signal",
    )
    fills = emulator.process_quote(_quote("AAPL", T1, 100.0))
    assert fills
    assert fills[0].price > 100.0                       # bought above the mid
    assert emulator._last_prices["AAPL"] == pytest.approx(100.0, rel=1e-9)
    # So NAV right after the buy reflects the cost, not a paper gain.
    assert emulator.get_portfolio_value({}) < 10_000.0


def test_positions_value_uses_the_same_fallback_as_portfolio_value():
    pe = PortfolioEmulator(10_000.0)
    pe.buy("AAPL", 10.0, 100.0, timestamp=T0)
    pe.save_portfolio_snapshot({"AAPL": 100.0}, timestamp=T0)
    # AAPL missing from this bar's prices: both valuations must carry it.
    assert pe.get_positions_value({}) == pytest.approx(1_000.0)
    assert pe.get_portfolio_value({}) == pytest.approx(
        pe.get_cash() + pe.get_positions_value({})
    )


def test_a_position_valued_off_a_stale_print_is_reported_as_such():
    """A halted or delisted name stays marked at its last trade forever.

    The fallback is correct for a one-bar gap and an optimistic fiction for a
    name that has stopped printing; nothing haircuts it, but the exposure is
    no longer invisible.
    """
    pe = PortfolioEmulator(10_000.0)
    pe.buy("AAPL", 10.0, 100.0, timestamp=T0)
    pe.buy("HALT", 10.0, 100.0, timestamp=T0)
    pe.save_portfolio_snapshot({"AAPL": 100.0, "HALT": 100.0}, timestamp=T0)
    # HALT stops printing; AAPL keeps trading.
    for day in range(1, 4):
        pe.save_portfolio_snapshot(
            {"AAPL": 100.0}, timestamp=T0 + timedelta(days=day)
        )

    realism = pe.get_realism_summary()
    assert realism["stale_marked_symbols"]["HALT"] == pytest.approx(
        3 * 86_400.0
    )
    assert "AAPL" not in realism["stale_marked_symbols"]
    assert realism["max_stale_mark_seconds"] == pytest.approx(3 * 86_400.0)
    # And it is still carried at full value, which is the point of reporting it.
    assert pe.get_positions_value({"AAPL": 100.0}) == pytest.approx(2_000.0)


def test_same_session_round_trips_are_counted_against_the_pdt_limit():
    """A $6k account is under the $25k PDT threshold: three day trades per
    rolling five business days in a margin account. Not enforced (the account
    type is unknowable here) but never again invisible."""
    pe = PortfolioEmulator(100_000.0)
    for index in range(4):
        at = T0 + timedelta(minutes=index)
        assert pe.buy("AAPL", 10.0, 100.0, timestamp=at)
        assert pe.sell("AAPL", 10.0, 100.0, timestamp=at + timedelta(seconds=30))
    realism = pe.get_realism_summary()
    assert realism["day_trade_count"] == 4
    assert realism["day_trades_worst_rolling_5_sessions"] == 4
    assert realism["pdt_limit_reference"] == 3


def test_an_overnight_round_trip_is_not_a_day_trade():
    pe = PortfolioEmulator(100_000.0)
    assert pe.buy("AAPL", 10.0, 100.0, timestamp=T0)
    assert pe.sell("AAPL", 10.0, 100.0, timestamp=T0 + timedelta(days=2))
    assert pe.get_realism_summary()["day_trade_count"] == 0


def test_realism_metrics_ride_along_in_the_execution_summary():
    """backtest_summary merges this dict straight onto the result row, so a
    reader sees the cost basis, the rejected orders and the return basis
    without having to know they exist."""
    emulator = create_backtest_emulator(
        initial_cash=6_000.0,
        taker_fee=None,
        is_crypto=False,
        execution_delay=timedelta(hours=1),
    )
    summary = emulator.get_execution_summary()
    for key in (
        "equity_cost_model_version",
        "equity_one_way_cost_bps",
        "min_order_notional",
        "sub_minimum_rejected_order_count",
        "settled_sell_proceeds_fraction",
        "unsettled_cash",
        "day_trades_worst_rolling_5_sessions",
        "return_basis",
        "benchmark_return_basis",
        "dividends_credited",
    ):
        assert key in summary, key
    # The promotion-gating keys are untouched by the realism merge.
    assert summary["execution_provenance_complete"] is True
    assert summary["rejected_order_count"] == 0

    legacy = PortfolioEmulator(6_000.0)
    assert "return_basis" in legacy.get_execution_summary()
    assert (
        legacy.get_execution_summary()["execution_provenance_complete"] is False
    )


def test_constructor_rejects_impossible_realism_settings():
    with pytest.raises(ValueError, match="min_order_notional"):
        PortfolioEmulator(1_000.0, min_order_notional=-1.0)
    with pytest.raises(ValueError, match="settled_sell_proceeds_fraction"):
        PortfolioEmulator(1_000.0, settled_sell_proceeds_fraction=1.5)
    with pytest.raises(ValueError, match="settlement_delay"):
        PortfolioEmulator(1_000.0, settlement_delay=timedelta(seconds=-1))
    with pytest.raises(ValueError, match="default_dividend_yield"):
        PortfolioEmulator(1_000.0, default_dividend_yield=-0.01)
    with pytest.raises(ValueError, match="equity_cost_model"):
        PortfolioEmulator(1_000.0, equity_cost_model="30bps")
