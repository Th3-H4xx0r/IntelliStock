"""
PortfolioEmulator: used only for backtesting. Tracks cash, positions, trades,
and portfolio value snapshots over time.

Its job is to be WRONG IN THE SAME DIRECTION AS THE LIVE ACCOUNT, or ideally
slightly against itself. Until 2026-08-02 it was wrong in the strategy's
favour on five separate axes at once — equity fills were free (no spread, no
slippage, no fee), sub-$1 orders that Alpaca rejects filled anyway, sell
proceeds were spendable the instant they landed, fresh buys were marked at
their own above-mid fill price, and held positions earned no distributions
while the SPY series they were scored against was total-return. Each of those
is now modelled, each is configurable, and each is reported in
``get_realism_summary()`` so a result can be read against its own assumptions.
Read the module constants below before changing any of them: the numbers are
sourced from this book's measured liquidity, not from convenience.
"""

import copy
from datetime import datetime, timedelta, timezone
import math

try:
    from simulated_execution import (
        DEFAULT_EQUITY_EXECUTION_COST_MODEL,
        InsufficientBuyingPower,
        LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL,
        ExecutionCostModel,
        NextEventExecutionSimulator,
        SimulationFill,
        SimulationOrder,
        SimulationPriceEvent,
        SimulationQuote,
        SimulationSubmission,
        TieredExecutionCostModel,
    )
except ImportError:  # Package import path used by repository-root pytest.
    from backend.simulated_execution import (
        DEFAULT_EQUITY_EXECUTION_COST_MODEL,
        InsufficientBuyingPower,
        LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL,
        ExecutionCostModel,
        NextEventExecutionSimulator,
        SimulationFill,
        SimulationOrder,
        SimulationPriceEvent,
        SimulationQuote,
        SimulationSubmission,
        TieredExecutionCostModel,
    )

# Crypto is NEVER commission-free: backtest fills must model the taker fee.
# Source the fee from the crypto core so sizing and fills stay in lock-step;
# guard the import so any failure falls back to the known taker fee.
# Only ever applied to crypto symbols (detected via "/" in the ticker —
# equities never contain a slash).
try:  # pragma: no cover - trivial import guard
    from strategies.crypto.core import CRYPTO_FEES as _CRYPTO_FEES
    _CRYPTO_TAKER_FEE = float(_CRYPTO_FEES["taker"])
except Exception:  # pragma: no cover - fall back so equity paths never break
    _CRYPTO_TAKER_FEE = 0.0025


# --------------------------------------------------------------------------
# Equity execution costs. "Commission-free" is a marketing term, not a cost
# model.
#
# This emulator used to charge equities NOTHING: no spread, no slippage, no
# fee. Every backtest number this project has ever produced was therefore a
# gross return quoted as if orders executed at the mid of a bar close with
# infinite depth. A run instrumented with the nominal upstream model
# (equity-next-event-v1: 5 bps spread / 10 bps slippage / 0.3 bps fee =
# 12.8 bps one-way) still cost a median $26.09 per 20-session run on $6,000
# — 0.435% per 20 sessions, ~5.4%/yr, implying ~43x annual turnover. That
# nominal model is itself optimistic: 5 bps of quoted spread describes SPY,
# not this book.
#
# The book this strategy actually trades has a 60-day median dollar volume of
# $2.4M-$7.9M per name. At that liquidity the realistic all-in one-way cost is
# 25-35 bps, so the components below are set to land at the middle of that
# band:
#
#   spread_bps    24.0  quoted spread; a marketable order pays HALF (12.0 bps)
#   slippage_bps  18.0  impact + adverse selection on a market order that
#                       crosses and walks the book
#   fee_bps        0.3  SEC Section 31 (~0.28 bps of principal, sell side) +
#                       FINRA TAF ($0.000166/share, sell side). Alpaca charges
#                       no commission; these pass-throughs are still real.
#   one-way      = 24/2 + 18 + 0.3 = 30.3 bps      round trip ~60.6 bps
#
# At the ~43x/yr turnover measured above that is ~12.5%/yr of drag versus the
# ~5.4%/yr the nominal model showed and the 0%/yr the emulator charged before.
# That is larger than any alpha this project has claimed, which is the point:
# a cost model that flatters the strategy is worse than no backtest at all.
#
# `latency` stays zero deliberately. The T+1 fill discipline is enforced by
# the emulator's `execution_delay` plus the simulator's strict
# "quote must be after the decision" rule; putting latency here as well would
# double-count the same delay.
#
# Every component is configurable — pass an explicit ExecutionCostModel to run
# a sensitivity sweep (see backtest_evidence_options.resolve_execution_cost_model,
# which scales all three components to a target one-way cost).
# Defined in simulated_execution beside the nominal model so the experiment
# receipt and the fills can never name different models. Re-exported here
# because this is where callers expect it.

#: Alpaca rejects any equity order whose notional is under $1 (the fractional
#: minimum). An audit of the best-performing backtest to date found 219 of its
#: 260 trades were sub-$1 — $52 of $16,286 total notional — meaning ~84% of the
#: trades that produced the headline number could never have been placed. The
#: emulator has to refuse them or the equity curve is built on orders the
#: broker would have bounced. Applies to BOTH sides: a fractional position
#: worth less than a dollar cannot be sold either, so dust is genuinely stuck.
MIN_EQUITY_ORDER_NOTIONAL = 1.0

#: Fraction of equity sell proceeds usable in the same cycle. Alpaca's instant
#: settlement advances most of an unsettled sale but not all of it, and this
#: project's own LIVE sizing already haircuts anticipated proceeds by the same
#: 0.95 (`nexus_broker_utils.buy_ceiling`). Crediting 100% instantly — which is
#: what this emulator did — lets a backtest recycle capital slightly faster
#: than the live account can, which compounds over a high-turnover run.
SETTLED_SELL_PROCEEDS_FRACTION = 0.95

#: When the withheld remainder becomes spendable. US equities settle T+1.
DEFAULT_SETTLEMENT_DELAY = timedelta(days=1)

#: Annual distribution yields used to accrue dividends onto held equities.
#:
#: This exists because the two sides of the benchmark comparison do NOT share a
#: return convention. Verified 2026-08-02 against the live cache: all 4,000
#: sampled AlpacaBarsCache chunks hash under adjustment="split"
#: (price_utils.alpaca_bars_cache_key folds `adjustment` into the row id), so
#: the traded series — and therefore the whole equity curve — is PRICE RETURN.
#: The SPY benchmark is fetched separately at broker.py:1302 with
#: adjustment="all" and experiment_registry pins `total_return: True`, so the
#: benchmark is TOTAL RETURN. Every `active_return` / `information_ratio` this
#: project has computed therefore charged the strategy SPY's ~1.25%/yr of
#: distributions that its own holdings were never credited.
#:
#: Only symbols listed here accrue. Anything absent uses
#: `default_dividend_yield` (0.0), which is both conservative and roughly true
#: for the microcap-ish discovered universe. Values are trailing-12-month
#: distribution yields rounded to the nearest 5 bps; they are ESTIMATES and are
#: meant to be overridden per run via `dividend_yields=`.
#:
#: Deliberately NOT seeded: leveraged/inverse ETFs (TQQQ, SQQQ, …). Their
#: distributions are mostly collateral interest and therefore track the fed
#: funds rate rather than the index — a fixed number here would be wrong in
#: both directions across a multi-year window. Set them explicitly per run.
DEFAULT_EQUITY_DIVIDEND_YIELDS = {
    # S&P 500 trackers
    "SPY": 0.0125,
    "VOO": 0.0125,
    "IVV": 0.0125,
    "SPLG": 0.0125,
    # Total market
    "VTI": 0.0125,
    # Nasdaq-100 — the sleeve this strategy actually holds long
    "QQQ": 0.0050,
    "QQQM": 0.0055,
    # Other broad indices that show up as sleeve/benchmark proxies
    "DIA": 0.0155,
    "IWM": 0.0110,
    "VGT": 0.0060,
}


def _as_utc(value):
    """Normalize a trade/snapshot timestamp for arithmetic.

    The legacy equity backtest clock is naive and the event-time API is aware;
    mixing them raises TypeError on subtraction, which would surface as a
    mid-run crash rather than a bad number. Returns None for anything that is
    not a datetime so callers can simply skip time-dependent accounting.
    """
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class PortfolioEmulator:
    """
    Emulates a portfolio for backtesting. Records all buys/sells with timestamps
    and portfolio value snapshots so you can retrieve trade history and portfolio
    value history at the end of a backtest.
    """

    def __init__(
        self,
        initial_cash=100000.0,
        taker_fee=None,
        *,
        execution_simulator=None,
        execution_delay=None,
        equity_cost_model=None,
        min_order_notional=MIN_EQUITY_ORDER_NOTIONAL,
        settled_sell_proceeds_fraction=SETTLED_SELL_PROCEEDS_FRACTION,
        settlement_delay=DEFAULT_SETTLEMENT_DELAY,
        dividend_yields=None,
        default_dividend_yield=0.0,
    ):
        try:
            initial_cash = float(initial_cash)
        except (TypeError, ValueError) as exc:
            raise ValueError("initial_cash must be finite and nonnegative") from exc
        if not math.isfinite(initial_cash) or initial_cash < 0:
            raise ValueError("initial_cash must be finite and nonnegative")
        self._cash = initial_cash
        self._initial_value = initial_cash  # Track original portfolio value
        # Crypto taker fee applied to fills (equities are commission-free). Defaults
        # to the module constant (Alpaca 0.25%); a Binance.US backtest passes
        # 0.0002 (0.02%) so low-fee/high-frequency strategies value correctly.
        try:
            resolved_taker_fee = (
                float(taker_fee)
                if taker_fee is not None
                else float(_CRYPTO_TAKER_FEE)
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("taker_fee must be finite and between 0 and 1") from exc
        if (
            not math.isfinite(resolved_taker_fee)
            or resolved_taker_fee < 0
            or resolved_taker_fee >= 1
        ):
            raise ValueError("taker_fee must be finite and between 0 and 1")
        self._taker_fee = resolved_taker_fee
        self._positions = {}  # ticker -> shares (float)
        self._trades = []    # list of { timestamp, action, ticker, shares, price, total, cash_after }
        self._portfolio_snapshots = []  # list of { timestamp, value, cash, positions_snapshot, prices }
        self._last_prices = {}  # V7.3: track last known prices for portfolio valuation fallback
        # Crypto fee accounting: total taker fees actually charged, and total
        # crypto notional traded (gross, both legs) — the base for per-platform
        # fee estimates. Stays 0 on a pure-equity run; equity execution cost is
        # accounted separately below because its shape is different (three
        # named components, not one embedded fee).
        self._crypto_fees_paid = 0.0
        self._crypto_volume = 0.0
        # Equity execution cost model. Applied to EVERY equity fill, including
        # the legacy immediate buy()/sell() path — which is not dead code: a
        # crypto-kind instance carrying an equity ticker in its watchlist
        # builds an emulator with no execution simulator, so before this its
        # equity fills were free and instant. When a simulator IS attached we
        # reuse ITS model so the two paths can never disagree about what a
        # fill cost.
        if equity_cost_model is None:
            equity_cost_model = (
                execution_simulator.cost_model
                if isinstance(execution_simulator, NextEventExecutionSimulator)
                else LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL
            )
        if not isinstance(equity_cost_model,
                          (ExecutionCostModel, TieredExecutionCostModel)):
            raise ValueError("equity_cost_model must be an ExecutionCostModel")
        self._equity_cost_model = equity_cost_model
        self._equity_tiered = isinstance(equity_cost_model,
                                         TieredExecutionCostModel)
        self._equity_fees_paid = 0.0
        self._equity_spread_cost = 0.0
        self._equity_slippage_cost = 0.0
        self._equity_volume = 0.0
        # Orders the live broker would have rejected outright.
        try:
            min_order_notional = float(min_order_notional)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "min_order_notional must be finite and nonnegative"
            ) from exc
        if not math.isfinite(min_order_notional) or min_order_notional < 0:
            raise ValueError("min_order_notional must be finite and nonnegative")
        self._min_order_notional = min_order_notional
        self._sub_minimum_rejects = 0
        self._sub_minimum_reject_notional = 0.0
        # T+1 settlement: [(settles_at_utc, still_withheld)] for equity sells.
        try:
            settled_fraction = float(settled_sell_proceeds_fraction)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "settled_sell_proceeds_fraction must be between 0 and 1"
            ) from exc
        if not math.isfinite(settled_fraction) or not 0.0 <= settled_fraction <= 1.0:
            raise ValueError(
                "settled_sell_proceeds_fraction must be between 0 and 1"
            )
        if (
            not isinstance(settlement_delay, timedelta)
            or not math.isfinite(settlement_delay.total_seconds())
            or settlement_delay.total_seconds() < 0
        ):
            raise ValueError("settlement_delay must be a nonnegative timedelta")
        self._settled_sell_proceeds_fraction = settled_fraction
        # Count same-tick SUBMITTED-but-unfilled sells toward buying power.
        # Default False keeps every existing backtest byte-identical; the broker
        # sets it from `backtest_credit_pending_sell_proceeds`. See
        # `pending_sell_proceeds` for the measurement that motivated it.
        self.credit_pending_sell_proceeds = False
        self._settlement_delay = settlement_delay
        self._unsettled_tranches = []
        self._clock = None
        # Dividend accrual (see DEFAULT_EQUITY_DIVIDEND_YIELDS for why).
        try:
            default_yield = float(default_dividend_yield)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "default_dividend_yield must be finite and nonnegative"
            ) from exc
        if not math.isfinite(default_yield) or default_yield < 0:
            raise ValueError(
                "default_dividend_yield must be finite and nonnegative"
            )
        self._default_dividend_yield = default_yield
        self._dividend_yields = {
            str(sym).strip().upper(): float(rate)
            for sym, rate in (
                DEFAULT_EQUITY_DIVIDEND_YIELDS
                if dividend_yields is None
                else dividend_yields
            ).items()
        }
        if any(
            not math.isfinite(rate) or rate < 0
            for rate in self._dividend_yields.values()
        ):
            raise ValueError("dividend yields must be finite and nonnegative")
        self._dividends_credited = 0.0
        self._dividends_by_symbol = {}
        self._dividend_accrual_clock = None
        # Pattern-day-trader diagnostics. A $6k account is far under the
        # $25,000 PDT threshold, so in a MARGIN account only three day trades
        # are permitted per rolling five business days. Nothing here enforces
        # that — the account type is not knowable from inside a backtest — but
        # a run that needs dozens of same-session round trips to make its
        # number is not a run that could have happened, and the summary now
        # says so out loud instead of hiding it.
        self._intraday_opens = {}
        self._day_trades_by_date = {}
        # Staleness of the last-known-price fallback. Carrying a held name at
        # its last print is right for a symbol missing from one bar and wrong
        # for one that has stopped trading: a halted or delisted position stays
        # marked at full value forever and props up NAV, sizing and the
        # drawdown halt. Nothing here haircuts it — inventing a liquidation
        # price would be its own fiction — but a run that leaned on a mark for
        # days can no longer do so invisibly.
        self._price_seen_at = {}
        self._max_stale_mark_seconds = 0.0
        self._stale_marked_symbols = {}
        # Set by create_backtest_emulator when it overrides an optimistic
        # nominal model, so the substitution is auditable downstream.
        self._execution_cost_model_requested_version = None
        if (
            execution_simulator is not None
            and not isinstance(execution_simulator, NextEventExecutionSimulator)
        ):
            raise ValueError(
                "execution_simulator must be a NextEventExecutionSimulator"
            )
        if execution_delay is None:
            execution_delay = timedelta(0)
        if (
            not isinstance(execution_delay, timedelta)
            or not math.isfinite(execution_delay.total_seconds())
            or execution_delay.total_seconds() < 0
        ):
            raise ValueError("execution_delay must be a nonnegative timedelta")
        self._execution_simulator = execution_simulator
        self._execution_delay = execution_delay
        self._simulation_order_sequence = 0
        self._recorded_orders = []
        self._applied_cumulative_fills = {}
        self._confirmed_simulation_fills = []
        self._execution_cash_reservations = {}
        self._execution_position_reservations = {}
        self._execution_event_provenance_complete = True

    @property
    def has_next_event_execution(self):
        return self._execution_simulator is not None

    # -- realism helpers ---------------------------------------------------

    @staticmethod
    def _is_crypto(ticker):
        """Crypto symbols carry a slash ("BTC/USD"); equities never do.

        The same convention already gates the taker fee, split reconciliation
        and the exit-when-blind path, so keep one definition of it.
        """
        return "/" in str(ticker)

    def _advance_clock(self, timestamp):
        """Move the settlement clock forward and release matured proceeds.

        Only ever called with a timestamp the caller actually supplied. A run
        that trades without timestamps (older harnesses, scripts) simply never
        withholds anything — modelling settlement with no clock would strand
        cash for the life of the run, which is a worse lie than not modelling
        it at all.
        """
        now = _as_utc(timestamp)
        if now is None:
            return None
        if self._clock is None or now > self._clock:
            self._clock = now
        if self._unsettled_tranches:
            self._unsettled_tranches = [
                (settles_at, amount)
                for settles_at, amount in self._unsettled_tranches
                if settles_at > self._clock and amount > 1e-12
            ]
        return self._clock

    def _withheld_cash(self):
        """Sell proceeds booked into cash but not yet spendable."""
        if not self._unsettled_tranches:
            return 0.0
        return sum(amount for _settles_at, amount in self._unsettled_tranches)

    def pending_sell_proceeds(self, prices=None):
        """Expected proceeds of sells already SUBMITTED but not yet filled.

        Execution is next-event: an order submitted while a bar is processed
        fills at the next quote. The index core submits its funding SPY sell on
        the SAME tick as the buy it is funding, so the buy is sized against a
        cash balance that does not yet contain the money raised for it.

        Measured over bt 820236 / 613166 / 725146: 41 cash-bound buy events,
        $14,801.27 of approved size refused, and $14,084.02 of that (95.2%)
        was a same-tick core release that had been submitted and not filled.
        On the canonical bar (725146, 2026-02-05) the core sized the release to
        the cent — `need = 827.86 - 124.64 = 703.22`, released $703.18 — and the
        buy still saw `available=$4.64` and skipped. Only the clock was wrong.

        Valued at the LAST TRADED price, never at a hoped-for price, and only
        for orders the simulator is actually holding.
        """
        sim = self._execution_simulator
        if sim is None:
            return 0.0
        marks = dict(getattr(self, "_last_prices", None) or {})
        if prices:
            for sym, px in prices.items():
                try:
                    if float(px or 0.0) > 0:
                        marks[str(sym).strip().upper()] = float(px)
                except (TypeError, ValueError):
                    continue
        total = 0.0
        try:
            for order in sim.pending_orders:
                if str(getattr(order, "side", "")).strip().lower() != "sell":
                    continue
                sym = str(getattr(order, "symbol", "")).strip().upper()
                px = float(marks.get(sym, 0.0) or 0.0)
                qty = float(getattr(order, "quantity", 0.0) or 0.0)
                if px > 0 and qty > 0:
                    total += qty * px
        except Exception:
            return 0.0
        return max(0.0, total)

    def spendable_cash(self):
        """Money that has ARRIVED and settled — the SETTLE-time question.

        Deliberately not `get_buying_power`, which answers the COMMIT-time
        question ("how much may I put in the market?") and, with
        `credit_pending_sell_proceeds`, includes a sale that is only SUBMITTED.
        A promise cannot pay for a confirmed fill: paying out of that number is
        how `_cash` reached -$200.28 in the bt 101666 reconstruction, silently.
        """
        return self._cash - self._withheld_cash()

    def get_buying_power(self, reserved=0.0, *, prices=None):
        """Cash that could actually fund a new order right now.

        Distinct from `get_cash()` on purpose: unsettled sell proceeds are part
        of the account's cash (and therefore of NAV) but cannot be spent yet.
        Conflating the two is exactly how a backtest recycles capital faster
        than the live account does.

        `credit_pending_sell_proceeds` (default False) additionally counts sells
        that are SUBMITTED but not yet filled — see `pending_sell_proceeds`. That
        is not the same loosening: an unsettled tranche is cash the broker has
        and will not lend against, whereas a pending same-tick sell is an order
        already in the market whose proceeds this very bar's buy was sized
        against. The same 0.95 haircut applies, so the credit can never exceed
        what a partial fill would deliver.
        """
        try:
            reserved = float(reserved or 0.0)
        except (TypeError, ValueError):
            reserved = 0.0
        base = self.spendable_cash() - reserved
        if getattr(self, "credit_pending_sell_proceeds", False):
            base += self._settled_sell_proceeds_fraction * self.pending_sell_proceeds(prices)
        return max(0.0, base)

    def _withhold_sell_proceeds(self, ticker, proceeds, timestamp):
        """Withhold the unsettled slice of an equity sale until T+1."""
        if self._is_crypto(ticker):
            return  # crypto settles on the spot at every venue we trade
        now = self._advance_clock(timestamp)
        if now is None or self._settlement_delay.total_seconds() <= 0:
            return
        withheld = float(proceeds) * (
            1.0 - self._settled_sell_proceeds_fraction
        )
        if withheld <= 1e-12:
            return
        self._unsettled_tranches.append(
            (now + self._settlement_delay, withheld)
        )

    def _equity_model_for(self, symbol):
        """The cost model for one symbol. Identity when untiered."""
        return (self._equity_cost_model.model_for(symbol)
                if self._equity_tiered else self._equity_cost_model)

    def _equity_fill(self, side, shares, mid, symbol=None):
        """Price one equity fill through the cost model.

        Mirrors NextEventExecutionSimulator.on_quote exactly — touch price,
        then slippage, then a fee on the resulting notional — so the legacy
        immediate path and the next-event path cannot disagree about what an
        identical fill cost. Returns (fill_price, fees, spread_cost,
        slippage_cost).
        """
        model = self._equity_model_for(symbol)
        half_spread = mid * model.spread_bps / 20_000.0
        if side == "buy":
            touch = mid + half_spread
            fill_price = touch * (1.0 + model.slippage_bps / 10_000.0)
        else:
            touch = mid - half_spread
            fill_price = touch * (1.0 - model.slippage_bps / 10_000.0)
        if not math.isfinite(fill_price) or fill_price <= 0:
            raise ValueError("equity cost model produced a nonpositive fill price")
        notional = shares * fill_price
        fees = notional * model.fee_bps / 10_000.0
        return (
            fill_price,
            fees,
            abs(touch - mid) * shares,
            abs(fill_price - touch) * shares,
        )

    def _book_equity_costs(self, fees, spread_cost, slippage_cost, notional):
        self._equity_fees_paid += fees
        self._equity_spread_cost += spread_cost
        self._equity_slippage_cost += slippage_cost
        self._equity_volume += notional

    #: Set once per run by broker.py from the strategy config, so passive
    #: execution can be A/B'd per-strategy-doc rather than only per-host. None
    #: means "not configured" and the env var decides.
    _PASSIVE_OVERRIDE = None

    @classmethod
    def set_passive_execution(cls, enabled, expire_quotes=8):
        """Configure passive execution from a strategy config."""
        try:
            on = enabled if isinstance(enabled, bool) else str(
                enabled or "").strip().lower() in {"1", "true", "yes", "on"}
            exp = int(expire_quotes or 8)
        except (TypeError, ValueError):
            on, exp = False, 8
        cls._PASSIVE_OVERRIDE = (bool(on), max(0, exp))

    def _passive_limit_for(self, side, price):
        """`(limit_price, expire_after_quotes)` for this order, or `(None, 0)`.

        DEFAULT OFF. Without `PASSIVE_EXECUTION_ENABLED` every order keeps
        today's marketable behaviour byte-for-byte, so an untouched run is
        unchanged.

        The limit is placed at the DECISION price — the mid the strategy saw —
        rather than inside the spread. Posting inside would be more aggressive
        and fill more often, but it also starts giving the spread back, and the
        entire point is to stop paying it. Posting at the mid is the honest
        midpoint between "always fills, always pays" and "never pays, never
        fills".
        """
        try:
            import os
            override = type(self)._PASSIVE_OVERRIDE
            if override is not None:
                on, expire = override
            else:
                on = str(os.environ.get("PASSIVE_EXECUTION_ENABLED", "") or
                         "").strip().lower() in {"1", "true", "yes", "on"}
                try:
                    expire = int(os.environ.get("PASSIVE_EXPIRE_QUOTES", "8") or 8)
                except (TypeError, ValueError):
                    expire = 8
            if not on:
                return None, 0
            limit = float(price or 0.0)
            if limit <= 0:
                return None, 0
            return limit, max(0, expire)
        except Exception:
            # Fail OPEN to the marketable path: a passive order that silently
            # fails to rest is a trade that never happens, which is worse than
            # paying the spread.
            return None, 0

    def _rejects_below_minimum(self, ticker, notional):
        """True when the live broker would bounce this order on notional.

        Counted rather than raised: the strategy asked for something the market
        cannot do, which is a fact about the strategy, and a backtest that
        crashed there would just be re-run with the check off.
        """
        if self._is_crypto(ticker):
            return False  # crypto minimums are per-asset and far smaller
        if self._min_order_notional <= 0:
            return False
        if notional >= self._min_order_notional - 1e-12:
            return False
        self._sub_minimum_rejects += 1
        self._sub_minimum_reject_notional += max(0.0, float(notional))
        return True

    def _track_mark_staleness(self, prices, timestamp):
        """Measure how long each held name has been valued off a stale print.

        The last-known-price fallback in get_portfolio_value exists to stop a
        one-bar gap from making a position vanish, and it is right for that.
        It is also, unmodified, an assumption that a name which has stopped
        printing is still worth exactly what it was worth when it stopped —
        which is the optimistic reading of a halt, a delisting or a data
        outage. Record the exposure so a run cannot lean on it silently.
        """
        now = _as_utc(timestamp)
        if now is None:
            return
        for symbol in (prices or {}):
            self._price_seen_at[symbol] = now
        for symbol in self._positions:
            seen_at = self._price_seen_at.get(symbol)
            if seen_at is None:
                continue
            stale = (now - seen_at).total_seconds()
            if stale <= 0:
                continue
            self._stale_marked_symbols[symbol] = max(
                stale, self._stale_marked_symbols.get(symbol, 0.0)
            )
            self._max_stale_mark_seconds = max(
                self._max_stale_mark_seconds, stale
            )

    def _record_day_trade_leg(self, ticker, action, shares, timestamp):
        """Track same-session round trips for the PDT diagnostic."""
        now = _as_utc(timestamp)
        if now is None or self._is_crypto(ticker):
            return
        session = now.date()
        key = (str(ticker), session)
        if action == "buy":
            self._intraday_opens[key] = (
                self._intraday_opens.get(key, 0.0) + float(shares)
            )
            return
        opened = self._intraday_opens.get(key, 0.0)
        if opened <= 1e-12:
            return
        self._intraday_opens[key] = max(0.0, opened - float(shares))
        self._day_trades_by_date[session] = (
            self._day_trades_by_date.get(session, 0) + 1
        )

    def buy(self, ticker, shares, price, timestamp=None):
        """
        Execute a buy: spend cash and add to positions.
        ticker: str
        shares: float (can be fractional)
        price: float (price per share)
        timestamp: optional datetime for the trade record (caller can pass current_time)
        Returns: True if trade was executed, False if insufficient cash, if the
        notional is below what the broker would accept, or if the equity cost
        model pushes the all-in cost past available buying power.
        """
        if shares <= 0 or price <= 0:
            return False
        self._advance_clock(timestamp)
        if self._rejects_below_minimum(ticker, shares * price):
            return False
        trade = {
            "timestamp": timestamp,
            "action": "buy",
            "ticker": ticker,
        }
        if self._is_crypto(ticker):
            # Crypto is NOT commission-free: the taker fee is charged on
            # notional, so the same cash buys fewer coins. Byte-identical to
            # the pre-cost-model math — crypto venues quote a taker fee that
            # already subsumes what the equity model splits out.
            total = shares * price
            if total > self._cash:
                return False
            filled_shares = shares * (1.0 - self._taker_fee)
            self._crypto_fees_paid += total * self._taker_fee
            self._crypto_volume += total
            self._cash -= total
            fill_price = price
        else:
            # Equities pay spread + slippage + pass-through fees. The share
            # count asked for is the share count received; what moves is the
            # cash it takes to get them, so a caller that sized against the
            # mid now discovers it cannot afford the whole clip — which is
            # precisely what happens live.
            fill_price, fees, spread_cost, slippage_cost = self._equity_fill(
                "buy", shares, price, symbol=ticker
            )
            notional = shares * fill_price
            total = notional + fees
            if total > self.get_buying_power():
                return False
            filled_shares = shares
            self._book_equity_costs(fees, spread_cost, slippage_cost, notional)
            self._cash -= total
            trade.update({
                "fees": fees,
                "spread_cost": spread_cost,
                "slippage_cost": slippage_cost,
                "cost_model_version": self._equity_cost_model.version,
                "decision_price": price,
            })
        self._positions[ticker] = self._positions.get(ticker, 0.0) + filled_shares
        self._record_day_trade_leg(ticker, "buy", filled_shares, timestamp)
        trade.update({
            "shares": filled_shares,
            "price": fill_price,
            "total": total,
            "cash_after": self._cash,
        })
        self._trades.append(trade)
        print(f"TRADE: Buy {filled_shares} shares of {ticker} at {fill_price} for a total of {total} cash_after: {self._cash}")
        return True

    def sell(self, ticker, shares, price, timestamp=None):
        """
        Execute a sell: reduce positions and add cash.
        Returns: True if trade was executed, False if insufficient shares.
        """
        if shares <= 0 or price <= 0:
            return False
        current = self._positions.get(ticker, 0.0)
        if current < shares:
            shares = current  # sell all we have
        if shares <= 0:
            return False
        self._advance_clock(timestamp)
        # The floor bites on exits too, and it is not symmetric with the buy
        # case: a fractional position that has decayed below $1 cannot be
        # closed at all. Dust that the emulator used to sweep away for free
        # now stays on the books exactly as it would live.
        if self._rejects_below_minimum(ticker, shares * price):
            return False
        trade = {
            "timestamp": timestamp,
            "action": "sell",
            "ticker": ticker,
        }
        if self._is_crypto(ticker):
            total = shares * price
            self._crypto_fees_paid += total * self._taker_fee
            self._crypto_volume += total
            total = total * (1.0 - self._taker_fee)
            fill_price = price
        else:
            fill_price, fees, spread_cost, slippage_cost = self._equity_fill(
                "sell", shares, price, symbol=ticker
            )
            notional = shares * fill_price
            total = notional - fees
            self._book_equity_costs(fees, spread_cost, slippage_cost, notional)
            trade.update({
                "fees": fees,
                "spread_cost": spread_cost,
                "slippage_cost": slippage_cost,
                "cost_model_version": self._equity_cost_model.version,
                "decision_price": price,
            })
        self._cash += total
        self._withhold_sell_proceeds(ticker, total, timestamp)
        self._positions[ticker] = current - shares
        if self._positions[ticker] <= 0:
            del self._positions[ticker]
        self._record_day_trade_leg(ticker, "sell", shares, timestamp)
        trade.update({
            "shares": shares,
            "price": fill_price,
            "total": total,
            "cash_after": self._cash,
        })
        self._trades.append(trade)
        print(f"TRADE: Sell {shares} shares of {ticker} at {fill_price} for a total of {total} cash_after: {self._cash}")
        return True

    def get_portfolio_value(self, prices):
        """
        Current portfolio value: cash + sum(positions[t] * prices[t]).

        A held ticker absent from `prices` is carried at its LAST KNOWN price.
        It used to contribute nothing, i.e. be valued at zero, which made a
        position silently vanish from the valuation for as long as its symbol
        was missing from the bar and then reappear — producing cliff-drops and
        plateaus in the portfolio curve that look like market moves but are
        not. `_last_prices` was already being maintained for exactly this
        fallback ("V3: Track last known prices for portfolio_total fallback")
        and simply was not consulted here.

        This is not cosmetic: this value is NAV. It feeds position sizing,
        the single-position cap, ramp room, portfolio_value_high/low,
        max_drawdown_magnitude and the drawdown halt — so a spurious zero
        could halt trading or mis-size a buy.

        Only a ticker never seen at any price is skipped; there is nothing to
        carry forward for it.
        """
        value = self._cash
        last = getattr(self, "_last_prices", None) or {}
        for ticker, shares in self._positions.items():
            p = (prices or {}).get(ticker)
            if p is None:
                p = last.get(ticker)
            if p is not None:
                value += shares * float(p)
        return value

    def reconcile_splits(self, prices):
        """Restate share counts for HELD positions whose price gapped by an
        unadjusted split ratio. Returns a list of (ticker, ratio, old, new).

        Backtests read raw bars, and the feed does NOT split-adjust. VGT split
        8:1 mid-window: its close went $808.88 -> $101.57 in a single bar
        (ratio 7.96) while the share count stayed put, so NAV fell 13.6%
        instantly and the position booked a phantom -$765 against a true
        +$168. That turned a +13.54% run into -0.92% -- and it did it to two
        independent runs before it was caught. Any backtest spanning a split in
        a held name is silently corrupted, and the corruption looks exactly
        like a market crash on the equity curve.

        This is deliberately conservative: it fires only when the ratio is
        within 1.5% of a real split factor, so an ordinary gap-down (or even a
        genuine -50% halt-and-reopen) is left alone. Detection is symmetric --
        the same test with the ratio inverted catches reverse splits.

        Live mode is unaffected: this class is backtest-only, and a live broker
        reports post-split share counts through the positions API.
        """
        # 2026-08-02 DEFAULT OFF. Bars are now fetched with adjustment="split"
        # (broker.py), so a genuine split no longer produces a step in backtest
        # bars at all. The only thing left that can still match a split ratio is
        # a REAL CRASH -- and reconciling that fabricates shares out of thin air
        # and erases the loss from the equity curve. Enable only for a cache
        # known to predate the adjustment change, and prefer purging it instead.
        if not bool(getattr(self, "_split_reconcile_enabled", False)):
            return []
        # Crypto does not split. A ~50% single-bar drawdown in an altcoin is
        # entirely possible and would otherwise be misread as a 2-for-1,
        # doubling the coin count out of thin air.
        from split_detect import detect_split_ratio

        last = getattr(self, "_last_prices", None) or {}
        adjusted = []
        for ticker, shares in list(self._positions.items()):
            # Crypto never splits, and a ~50% single-bar drawdown in an altcoin
            # is entirely ordinary. The previous `_is_crypto` attribute check was
            # DEAD CODE: create_backtest_emulator drops its is_crypto argument and
            # the attribute is never set, so getattr always returned False and
            # every crypto backtest was exposed. Detect from the symbol
            # convention, which buy()/sell() already rely on.
            if "/" in str(ticker):
                continue
            try:
                new_px = float((prices or {}).get(ticker) or 0.0)
                old_px = float(last.get(ticker) or 0.0)
                shares = float(shares or 0.0)
            except (TypeError, ValueError):
                continue
            if new_px <= 0 or old_px <= 0 or shares <= 0:
                continue
            ratio = detect_split_ratio(old_px, new_px)
            if ratio is None:
                continue
            # `ratio` is always the share multiplier: 8.0 for an 8-for-1,
            # 0.1 for a 1-for-10 reverse. Position value is preserved.
            new_shares = shares * ratio
            self._positions[ticker] = new_shares
            # Restating the share count alone is NOT enough, and shipping only
            # that is what left the original bug half-fixed. Decisions read the
            # ENTRY price out of trade history:
            #     unrealized_pct = (current - entry) / entry * 100
            # so an unrestated $808 entry against a $101 post-split price still
            # reads -87.4%, and the circuit breaker still liquidates a position
            # that did nothing. Restate the executed prices and share counts on
            # this symbol's open lineage so cost basis stays economically true.
            for tr in self._trades:
                if str(tr.get("ticker") or "") != ticker:
                    continue
                try:
                    if tr.get("price") is not None:
                        tr["price"] = float(tr["price"]) / ratio
                    if tr.get("shares") is not None:
                        tr["shares"] = float(tr["shares"]) * ratio
                    # The decision price rides along or it becomes a stale
                    # pre-split number sitting next to a restated fill.
                    if tr.get("decision_price") is not None:
                        tr["decision_price"] = (
                            float(tr["decision_price"]) / ratio
                        )
                except (TypeError, ValueError):
                    pass
            if isinstance(last, dict) and ticker in last:
                try:
                    last[ticker] = float(last[ticker]) / ratio
                except (TypeError, ValueError):
                    pass
            adjusted.append((ticker, ratio, shares, new_shares))
        return adjusted

    def save_portfolio_snapshot(self, prices, timestamp=None):
        """
        Record current portfolio value at this moment with the given prices.
        prices: dict ticker -> price (live/current prices for valuation).
        timestamp: optional datetime (e.g. current_time in backtest loop).
        """
        # Before valuing anything: a held name whose price gapped by a split
        # factor must have its share count restated, or NAV -- which drives
        # sizing, the drawdown halt and max_drawdown_magnitude -- takes a
        # double-digit phantom hit.
        for _t, _r, _old, _new in self.reconcile_splits(prices):
            print(f"[emulator] SPLIT RECONCILED {_t}: ratio {_r:.2f}x, "
                  f"{_old:.4f} -> {_new:.4f} shares (price feed was unadjusted)")
        self._advance_clock(timestamp)
        # Distributions accrue on the position, not on the trade, so this is
        # the only hook that sees every held name on every bar.
        self.accrue_dividends(prices, timestamp)
        self._track_mark_staleness(prices, timestamp)
        value = self.get_portfolio_value(prices)
        # V3: Track last known prices for portfolio_total fallback
        if prices:
            if not hasattr(self, '_last_prices'):
                self._last_prices = {}
            self._last_prices.update(prices)
        self._portfolio_snapshots.append({
            "timestamp": timestamp,
            "value": value,
            "cash": self._cash,
            "positions_snapshot": copy.copy(self._positions),
            "prices": copy.copy(prices) if prices else {},
        })

    def apply_dividend(self, ticker, amount_per_share, timestamp=None):
        """Credit a known cash distribution on a held position.

        The exact, event-driven path: hand it a real ex-date and a real
        per-share amount from a corporate-actions feed and it books the cash.
        Nothing in this repo fetches distributions yet, which is why the
        yield-accrual fallback below exists — but when that feed is wired,
        this is the seam, and a run that uses it should set the accrual yields
        to zero so the two do not double-count.
        """
        shares = float(self._positions.get(ticker, 0.0) or 0.0)
        if shares <= 0:
            return 0.0
        try:
            amount_per_share = float(amount_per_share)
        except (TypeError, ValueError) as exc:
            raise ValueError("amount_per_share must be finite") from exc
        if not math.isfinite(amount_per_share) or amount_per_share <= 0:
            return 0.0
        cash = shares * amount_per_share
        self._cash += cash
        self._dividends_credited += cash
        key = str(ticker)
        self._dividends_by_symbol[key] = (
            self._dividends_by_symbol.get(key, 0.0) + cash
        )
        self._trades.append({
            "timestamp": timestamp,
            "action": "dividend",
            "ticker": ticker,
            "shares": shares,
            "price": amount_per_share,
            "total": cash,
            "cash_after": self._cash,
        })
        return cash

    def accrue_dividends(self, prices, timestamp):
        """Accrue distributions onto held equities, pro-rata by calendar time.

        THIS IS THE FIX FOR A REAL, MEASURED APPLES-TO-ORANGES COMPARISON, not
        a refinement. The traded bar series is fetched with adjustment="split"
        (verified 2026-08-02: 4,000/4,000 sampled AlpacaBarsCache chunks hash
        under "split", and the cache key folds `adjustment` into the row id),
        so the portfolio's entire equity curve is PRICE RETURN. The SPY
        benchmark it is scored against is fetched with adjustment="all" and
        the experiment registry pins total_return=True, so the benchmark is
        TOTAL RETURN. Result: every active_return / information_ratio /
        deflated-Sharpe number this project has produced silently charged the
        strategy ~1.25%/yr of SPY distributions while crediting its own
        holdings zero.

        Accrual is on ACT/365 calendar time, so the gaps between snapshots
        (nights, weekends, holidays) accrue too and the pieces sum to the full
        window — dividends do not care that the exchange was shut.

        This is deliberately an approximation of a lumpy quarterly cash flow.
        A yield only accrues for symbols explicitly listed in
        `dividend_yields`; everything else gets `default_dividend_yield`
        (0.0), so an unknown microcap is assumed to pay nothing. The error
        that leaves is one-sided in the safe direction: it understates the
        strategy, never flatters it.
        """
        now = _as_utc(timestamp)
        if now is None:
            return 0.0
        previous = self._dividend_accrual_clock
        self._dividend_accrual_clock = now
        if previous is None:
            return 0.0
        elapsed_days = (now - previous).total_seconds() / 86_400.0
        if elapsed_days <= 0:
            return 0.0
        last = getattr(self, "_last_prices", None) or {}
        total = 0.0
        for ticker, shares in self._positions.items():
            if self._is_crypto(ticker) or shares <= 0:
                continue
            rate = self._dividend_yields.get(
                str(ticker).strip().upper(), self._default_dividend_yield
            )
            if rate <= 0:
                continue
            mark = (prices or {}).get(ticker)
            if mark is None:
                mark = last.get(ticker)
            try:
                mark = float(mark)
            except (TypeError, ValueError):
                continue
            if mark <= 0:
                continue
            cash = shares * mark * rate * elapsed_days / 365.0
            if cash <= 0:
                continue
            self._cash += cash
            self._dividends_credited += cash
            key = str(ticker)
            self._dividends_by_symbol[key] = (
                self._dividends_by_symbol.get(key, 0.0) + cash
            )
            total += cash
        return total

    def get_dividend_summary(self):
        """What the portfolio was credited, and on what basis.

        `return_basis` is the load-bearing field: it tells a reader whether the
        equity curve is comparable to a total-return benchmark or not.
        """
        credited = bool(self._dividends_credited > 0)
        return {
            "dividends_credited": round(self._dividends_credited, 6),
            "dividends_by_symbol": {
                sym: round(amount, 6)
                for sym, amount in self._dividends_by_symbol.items()
            },
            "dividend_yields": dict(self._dividend_yields),
            "default_dividend_yield": self._default_dividend_yield,
            # Bars are split-adjusted only, so price appreciation carries no
            # distributions; whatever is credited above is what makes the
            # curve total-return.
            "return_basis": (
                "total_return_accrued" if credited else "price_return"
            ),
            "benchmark_return_basis": "total_return",
        }

    def get_trade_history(self):
        """Return list of all trades, each with timestamp, action, ticker, shares, price, total, cash_after."""
        return list(self._trades)

    def get_fee_summary(self):
        """Crypto fee accounting for the backtest. Equities are commission-free so
        these are 0 for pure-equity runs. ``total_fees`` is the taker fee actually
        charged; ``total_volume`` is gross crypto notional traded across both legs
        (the base for per-platform fee estimates); ``taker_rate`` is the applied
        rate."""
        return {
            "total_fees": round(self._crypto_fees_paid, 6),
            "total_volume": round(self._crypto_volume, 2),
            "taker_rate": self._taker_fee,
        }

    def get_portfolio_history(self):
        """Return list of all snapshots: timestamp and value (and optionally cash/positions)."""
        return list(self._portfolio_snapshots)

    def get_cash(self):
        """Current cash balance."""
        return self._cash

    def get_available_cash(self, reserved: float = 0.0):
        """Cash available for non-reserving strategies (e.g. total_cash - reserved_capital).

        Now also nets out unsettled sell proceeds: this is the surface
        strategies size against, and sizing against money the broker has not
        released is how a backtest turns capital over faster than the account
        can.
        """
        return self.get_buying_power(reserved)

    def get_positions(self):
        """Current positions: dict ticker -> shares (copy)."""
        return copy.copy(self._positions)

    def get_positions_value(self, prices):
        """Value of positions only (excluding cash) using given prices.

        Uses the same last-known-price fallback as get_portfolio_value. It did
        not, which meant the two disagreed by the value of any held name
        missing from this bar's price dict — the exact cliff-drop that
        fallback was added to get_portfolio_value to kill.
        """
        v = 0.0
        last = getattr(self, "_last_prices", None) or {}
        for ticker, shares in self._positions.items():
            p = (prices or {}).get(ticker)
            if p is None:
                p = last.get(ticker)
            if p is not None:
                v += shares * float(p)
        return v

    def record_order(self, order):
        """Record a simulated order without mutating portfolio accounting."""
        if not isinstance(order, SimulationOrder):
            raise ValueError("order must be a SimulationOrder")
        if self._execution_simulator is not None:
            self._execution_simulator.submit(order)
        self._recorded_orders.append(order)

    @staticmethod
    def _fill_mid(fill, quantity):
        """Recover the quote mid a fill was priced off.

        Falls back to the fill price if the costs are not decomposable (a
        hand-built fill in a test, or a zero quantity), which is the old
        behavior and never worse than it.
        """
        if quantity <= 1e-12:
            return fill.price
        offset = (fill.spread_cost + fill.slippage_cost) / quantity
        mid = fill.price - offset if fill.side == "buy" else fill.price + offset
        if not math.isfinite(mid) or mid <= 0:
            return fill.price
        return mid

    def apply_fill(self, fill):
        """Apply only the new cumulative quantity from a confirmed fill event."""
        if not isinstance(fill, SimulationFill):
            raise ValueError("fill must be a SimulationFill")
        previous = float(
            self._applied_cumulative_fills.get(fill.order_id, 0.0) or 0.0
        )
        if fill.cumulative_quantity < previous - 1e-12:
            raise ValueError(
                "cumulative_quantity cannot move backwards for an order"
            )
        delta = fill.cumulative_quantity - previous
        if abs(delta) <= 1e-12:
            return
        if not abs(delta - fill.incremental_quantity) <= 1e-9:
            raise ValueError(
                "incremental_quantity must equal the new cumulative delta"
            )

        self._advance_clock(fill.executed_at)
        gross = delta * fill.price
        if fill.side == "buy":
            cash_delta = gross + fill.fees
            # Buying power, not raw cash: proceeds from a sale that has not
            # settled are in the balance but are not yet spendable.
            # SETTLE-time, not COMMIT-time. `get_buying_power` may carry the
            # pending-sell credit, and bt 101666 died on a $0.4646 shortfall
            # that was exactly the funding sale's own 23.2 bps of execution
            # cost. The simulator now clamps buys to this same number before
            # proposing them, so this is a backstop against a future desync
            # rather than the gate — and it raises a soft type the simulator
            # can catch instead of killing the run.
            if cash_delta > self.spendable_cash() + 1e-9:
                raise InsufficientBuyingPower(
                    "confirmed buy fill exceeds spendable cash"
                )
            new_cash = self._cash - cash_delta
            new_quantity = self._positions.get(fill.symbol, 0.0) + delta
            trade_total = cash_delta
        else:
            current = float(self._positions.get(fill.symbol, 0.0) or 0.0)
            if delta > current + 1e-9:
                raise ValueError(
                    "confirmed sell fill exceeds the current position"
                )
            cash_delta = gross - fill.fees
            if cash_delta < -1e-9:
                raise ValueError("confirmed sell fees exceed gross proceeds")
            new_cash = self._cash + cash_delta
            new_quantity = current - delta
            trade_total = cash_delta

        self._cash = new_cash
        if fill.side == "sell":
            self._withhold_sell_proceeds(
                fill.symbol, cash_delta, fill.executed_at
            )
        if new_quantity > 1e-12:
            self._positions[fill.symbol] = new_quantity
        else:
            self._positions.pop(fill.symbol, None)
        self._record_day_trade_leg(
            fill.symbol, fill.side, delta, fill.executed_at
        )
        # Mark at the MID the fill was priced off, not at the fill itself. A
        # buy fills above the mid by half the spread plus slippage, so storing
        # fill.price here marked every freshly-bought position ~30 bps above
        # what it was worth and carried that inflation forward through
        # get_portfolio_value's last-known-price fallback. The mid is exactly
        # recoverable because spread_cost and slippage_cost are both booked as
        # (distance x quantity) against it.
        self._last_prices[fill.symbol] = self._fill_mid(fill, delta)
        self._applied_cumulative_fills[fill.order_id] = (
            fill.cumulative_quantity
        )
        self._confirmed_simulation_fills.append(fill)
        self._trades.append({
            "timestamp": fill.executed_at,
            "action": fill.side,
            "ticker": fill.symbol,
            "shares": delta,
            "price": fill.price,
            "total": trade_total,
            "fees": fill.fees,
            "spread_cost": fill.spread_cost,
            "slippage_cost": fill.slippage_cost,
            "order_id": fill.order_id,
            "cumulative_quantity": fill.cumulative_quantity,
            "quote_timestamp": fill.quote_timestamp,
            "cost_model_version": fill.cost_model_version,
            "source": fill.source,
            "cash_after": self._cash,
        })

    def process_quote(self, quote):
        """Apply all normalized fills emitted for one quote event."""
        if self._execution_simulator is None:
            return ()
        if not isinstance(quote, SimulationQuote):
            raise ValueError("quote must be a SimulationQuote")
        fills = self._execution_simulator.on_quote(
            quote,
            accept_fill=self.apply_fill,
            cash_budget=self.spendable_cash,
        )
        for fill in fills:
            if fill.side == "buy":
                spent = fill.incremental_quantity * fill.price + fill.fees
                remaining = max(
                    0.0,
                    float(
                        self._execution_cash_reservations.get(
                            fill.order_id, 0.0
                        )
                        or 0.0
                    )
                    - spent,
                )
                self._execution_cash_reservations[fill.order_id] = remaining
            else:
                remaining = max(
                    0.0,
                    float(
                        self._execution_position_reservations.get(
                            fill.order_id, 0.0
                        )
                        or 0.0
                    )
                    - fill.incremental_quantity,
                )
                self._execution_position_reservations[
                    fill.order_id
                ] = remaining
        pending_ids = {
            order.order_id for order in self._execution_simulator.pending_orders
        }
        for reservations in (
            self._execution_cash_reservations,
            self._execution_position_reservations,
        ):
            for order_id in tuple(reservations):
                if order_id not in pending_ids:
                    reservations.pop(order_id, None)
        return fills

    def pending_execution_symbols(self):
        if self._execution_simulator is None:
            return ()
        return self._execution_simulator.pending_symbols

    def process_price_event(self, prices, *, timestamp):
        """Legacy timestamp relabeling facade; never promotable."""
        if self._execution_simulator is None:
            return ()
        self._execution_event_provenance_complete = False
        emitted = []
        for symbol in self.pending_execution_symbols():
            mid = (prices or {}).get(symbol)
            try:
                mid = float(mid)
            except (TypeError, ValueError):
                continue
            if mid <= 0:
                continue
            quote = SimulationQuote.from_mid(
                symbol=symbol,
                timestamp=timestamp,
                mid=mid,
                spread_bps=self._execution_simulator._model_for(
                    symbol).spread_bps,
            )
            emitted.extend(self.process_quote(quote))
        return tuple(emitted)

    def process_price_events(self, events):
        """Apply typed events carrying the source bar's true availability."""
        if self._execution_simulator is None:
            return ()
        emitted = []
        for symbol in self.pending_execution_symbols():
            event = (events or {}).get(symbol)
            if event is None:
                continue
            if not isinstance(event, SimulationPriceEvent):
                raise ValueError(
                    "price events must be SimulationPriceEvent instances"
                )
            if event.symbol != str(symbol).strip().upper():
                raise ValueError("price event symbol does not match its key")
            quote = SimulationQuote.from_mid(
                symbol=event.symbol,
                timestamp=event.available_at,
                mid=event.price,
                spread_bps=self._execution_simulator._model_for(
                    event.symbol).spread_bps,
            )
            emitted.extend(self.process_quote(quote))
        return tuple(emitted)

    def get_realism_summary(self):
        """Everything the emulator models that a naive backtest does not.

        Kept separate from the promotion-gating provenance keys so adding a
        realism metric can never silently change what is promotable — but it
        rides along in the backtest summary, because these numbers are the
        difference between a result and a fantasy. Read
        ``sub_minimum_rejected_order_count`` first: if it is a large fraction
        of the trade count, the run's headline number came from orders the
        broker would have refused.
        """
        day_trade_dates = sorted(self._day_trades_by_date)
        worst_rolling_5 = 0
        for index, _date in enumerate(day_trade_dates):
            window = day_trade_dates[max(0, index - 4):index + 1]
            worst_rolling_5 = max(
                worst_rolling_5,
                sum(self._day_trades_by_date[d] for d in window),
            )
        summary = {
            "equity_cost_model_version": self._equity_cost_model.version,
            "equity_cost_model": self._equity_cost_model.as_dict(),
            "equity_cost_model_requested_version": getattr(
                self, "_execution_cost_model_requested_version", None
            ),
            "equity_one_way_cost_bps": (
                self._equity_cost_model.spread_bps / 2.0
                + self._equity_cost_model.slippage_bps
                + self._equity_cost_model.fee_bps
            ),
            "legacy_equity_fees": round(self._equity_fees_paid, 6),
            "legacy_equity_spread_cost": round(self._equity_spread_cost, 6),
            "legacy_equity_slippage_cost": round(self._equity_slippage_cost, 6),
            "legacy_equity_volume": round(self._equity_volume, 2),
            "min_order_notional": self._min_order_notional,
            "sub_minimum_rejected_order_count": self._sub_minimum_rejects,
            "sub_minimum_rejected_notional": round(
                self._sub_minimum_reject_notional, 6
            ),
            "settled_sell_proceeds_fraction": (
                self._settled_sell_proceeds_fraction
            ),
            "settlement_delay_seconds": (
                self._settlement_delay.total_seconds()
            ),
            "unsettled_cash": round(self._withheld_cash(), 6),
            # Diagnostic only. Under $25,000 a MARGIN account is capped at
            # three day trades per rolling five business days; nothing here
            # enforces that because the account type is not knowable from a
            # backtest, but a run whose worst window is well above three did
            # not describe something the account could have done.
            "day_trade_count": sum(self._day_trades_by_date.values()),
            "day_trades_worst_rolling_5_sessions": worst_rolling_5,
            "pdt_limit_reference": 3,
            # A held name valued off a print this old was, for that long,
            # assumed to still be worth what it last traded at.
            "max_stale_mark_seconds": round(self._max_stale_mark_seconds, 3),
            "stale_marked_symbols": {
                symbol: round(seconds, 3)
                for symbol, seconds in self._stale_marked_symbols.items()
            },
        }
        summary.update(self.get_dividend_summary())
        return summary

    def get_execution_summary(self):
        if self._execution_simulator is None:
            summary = {
                "execution_provenance_complete": False,
                "execution_cost_model_version": None,
                "execution_cost_model": None,
                "total_fees": None,
                "spread_cost": None,
                "slippage_cost": None,
                "unfilled_order_count": None,
                "rejected_order_count": None,
                "fill_provenance": [],
            }
            summary.update(self.get_realism_summary())
            return summary
        summary = self._execution_simulator.execution_summary()
        summary["execution_provenance_complete"] = bool(
            summary.get("execution_provenance_complete")
            and self._execution_event_provenance_complete
        )
        if not self._execution_event_provenance_complete:
            summary["execution_provenance_error"] = (
                "price event availability was relabeled"
            )
        summary.update(self.get_realism_summary())
        return summary

    def execute_signal(
        self,
        ticker,
        signal,
        price,
        timestamp=None,
        cash_per_trade=1000.0,
        sell_fraction=1.0,
        order_source=None,
    ):
        """
        Convenience: execute a strategy signal (1=buy, -1=sell, 0=hold) with a simple rule.
        Buy: invest up to cash_per_trade; if cash is less than cash_per_trade, use all available cash so the trade still goes through.
        Sell: sell sell_fraction (0-1) of shares of ticker; default 1.0 = sell all.
        Returns True if a trade was executed.
        """
        if price is None:
            return False
        try:
            price = float(price)
        except (TypeError, ValueError) as exc:
            raise ValueError("price must be finite and positive") from exc
        if not math.isfinite(price):
            raise ValueError("price must be finite and positive")
        if price <= 0:
            return False
        if self._execution_simulator is not None and signal in (1, -1):
            if not isinstance(order_source, str) or not order_source.strip():
                raise ValueError(
                    "order_source is required for next-event execution"
                )
            if timestamp is None:
                raise ValueError(
                    "next-event execution requires a decision timestamp"
                )
            next_sequence = self._simulation_order_sequence + 1
            order_id = (
                f"sim-{next_sequence:012d}-"
                f"{str(ticker).strip().upper()}"
            )
            if signal == 1:
                try:
                    cash_per_trade = float(cash_per_trade)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "cash_per_trade must be finite and positive"
                    ) from exc
                if not math.isfinite(cash_per_trade):
                    raise ValueError(
                        "cash_per_trade must be finite and positive"
                    )
                reserved_cash = sum(
                    float(value or 0.0)
                    for value in self._execution_cash_reservations.values()
                )
                # Buying power, not raw cash: in-flight buy reservations AND
                # the unsettled slice of recent sales are both off the table.
                #
                # This clamp is why crediting the release at the BROKER gate
                # alone was inert — the gate said PASS and the emulator cut the
                # order anyway (bt 613166 02-05: gate $805.24 -> fill $87.45).
                # `get_buying_power` now carries the pending-sell credit, so
                # both ends agree.
                # No `prices` here — execute_signal only carries the BUY
                # symbol's price, and the pending sell is a different symbol
                # (the SPY core leg). `pending_sell_proceeds` falls back to
                # `_last_prices`, which is marked for every held symbol.
                amount_to_use = min(
                    cash_per_trade,
                    self.get_buying_power(reserved_cash),
                )
                if amount_to_use <= 0:
                    return False
                shares = self._execution_simulator.affordable_buy_quantity(
                    amount_to_use, price, symbol=ticker
                )
                side = "buy"
            else:
                try:
                    sell_fraction = float(sell_fraction)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "sell_fraction must be finite"
                    ) from exc
                if not math.isfinite(sell_fraction):
                    raise ValueError("sell_fraction must be finite")
                total_shares = float(self._positions.get(ticker, 0.0) or 0.0)
                reserved_shares = sum(
                    float(value or 0.0)
                    for order_key, value
                    in self._execution_position_reservations.items()
                    if order_key.endswith(
                        f"-{str(ticker).strip().upper()}"
                    )
                )
                total_shares = max(0.0, total_shares - reserved_shares)
                if total_shares <= 0:
                    return False
                frac = max(0.0, min(1.0, sell_fraction))
                shares = (
                    total_shares * frac if frac < 1.0 else total_shares
                )
                if shares <= 0:
                    return False
                side = "sell"
            # Never submit what the broker would reject. Alpaca bounces any
            # equity order under $1 of notional, and an audit of the
            # best-performing run found 219 of its 260 trades were sub-$1 —
            # $52 of $16,286 — so ~84% of the trades behind that number could
            # not have been placed. Gate at submission, on the decision price,
            # because that is the number the broker sees. Not an exception:
            # the strategy asking for an impossible clip is a property of the
            # strategy, and it is counted into the realism summary instead.
            if self._rejects_below_minimum(ticker, shares * price):
                return False
            # 2026-08-03 PASSIVE EXECUTION (opt-in, default OFF).
            #
            # A market order crosses the spread by construction: a buy lifts the
            # ask, a sell hits the bid. At the measured notional-weighted 45.6
            # bps that is 22.8 bps on every side of every trade, and measured
            # slippage vs mid is ~0.1 bps — so the cost is the CROSSING, not
            # execution quality, and only a resting order avoids it.
            #
            # Posting at the mid asks a counterparty to come to us. The saving
            # is the half spread; the price is non-fill risk, which the
            # simulator surfaces via `expired_order_count` rather than hiding.
            # Only viable alongside a holding floor: waiting hours for a fill is
            # free on a 30-day hold and fatal at a 15-minute cadence.
            _limit_px, _expire = self._passive_limit_for(side, price)
            order = SimulationOrder(
                order_id=order_id,
                symbol=ticker,
                side=side,
                quantity=shares,
                decision_at=timestamp,
                execute_not_before=timestamp + self._execution_delay,
                source=order_source.strip(),
                notional_limit=amount_to_use if side == "buy" else None,
                limit_price=_limit_px,
                expire_after_quotes=_expire,
            )
            self.record_order(order)
            self._simulation_order_sequence = next_sequence
            if side == "buy":
                self._execution_cash_reservations[order_id] = amount_to_use
            else:
                self._execution_position_reservations[order_id] = shares
            return SimulationSubmission(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                source=order.source,
            )
        if signal == 1:
            amount_to_use = min(cash_per_trade, self.get_buying_power())
            if amount_to_use <= 0:
                return False
            if self._is_crypto(ticker):
                shares = amount_to_use / price
            else:
                # Size against the ALL-IN cost per share, not the mid, or the
                # order asks for more shares than the cash can pay for and
                # buy() bounces the whole trade at the last moment.
                model = self._equity_model_for(ticker)
                all_in = (
                    price
                    * (1.0 + model.spread_bps / 20_000.0)
                    * (1.0 + model.slippage_bps / 10_000.0)
                    * (1.0 + model.fee_bps / 10_000.0)
                )
                shares = amount_to_use / all_in
            return self.buy(ticker, shares, price, timestamp=timestamp)
        if signal == -1:
            total_shares = self._positions.get(ticker, 0.0)
            if total_shares <= 0:
                return False
            frac = max(0.0, min(1.0, float(sell_fraction)))
            shares = total_shares * frac if frac < 1.0 else total_shares
            if shares <= 0:
                return False
            return self.sell(ticker, shares, price, timestamp=timestamp)
        return False

    def get_initial_value(self):
        """Return the original/initial portfolio value."""
        return self._initial_value

    def print_portfolio(self, prices, logger=None):
        """
        Print a pretty formatted summary of the portfolio.
        prices: dict ticker -> price (current prices for valuation)
        logger: optional logger function(msg, color) (if None, uses print)
        """
        final_value = self.get_portfolio_value(prices)
        cash = self.get_cash()
        positions = self.get_positions()
        positions_value = self.get_positions_value(prices)
        pnl = final_value - self._initial_value
        pnl_percent = (pnl / self._initial_value * 100) if self._initial_value > 0 else 0.0
        trades = self.get_trade_history()
        
        # Determine color/sign for P&L
        pnl_sign = "+" if pnl >= 0 else ""
        pnl_color = "green" if pnl >= 0 else "red"
        
        # Helper to log with or without logger
        def log(msg, color="white"):
            if logger:
                logger(msg, color)
            else:
                print(msg)
        
        # Print header
        log("=" * 70, "cyan")
        log("PORTFOLIO SUMMARY", "cyan")
        log("=" * 70, "cyan")
        
        # Initial value
        log(f"Initial Value:     ${self._initial_value:,.2f}", "white")
        
        # Final value
        log(f"Final Value:       ${final_value:,.2f}", "white")
        
        # P&L
        pnl_str = f"{pnl_sign}${abs(pnl):,.2f} ({pnl_sign}{pnl_percent:.2f}%)"
        log(f"Profit & Loss:     {pnl_str}", pnl_color)
        
        log("-" * 70, "white")
        
        # Cash
        log(f"Cash:              ${cash:,.2f}", "white")
        
        # Positions value
        log(f"Positions Value:   ${positions_value:,.2f}", "white")
        
        # Positions breakdown
        if positions:
            log("-" * 70, "white")
            log("Positions:", "white")
            for ticker, shares in sorted(positions.items()):
                price = (prices or {}).get(ticker, 0.0)
                position_value = shares * price
                log(f"  {ticker:8s}  {shares:10.4f} shares  @ ${price:8.2f}  = ${position_value:10,.2f}", "white")
        else:
            log("Positions:         None", "white")
        
        # Trade summary
        log("-" * 70, "white")
        log(f"Total Trades:       {len(trades)}", "white")
        if trades:
            buy_count = sum(1 for t in trades if t.get("action") == "buy")
            sell_count = sum(1 for t in trades if t.get("action") == "sell")
            log(f"  Buys:            {buy_count}", "white")
            log(f"  Sells:           {sell_count}", "white")
        
        log("=" * 70, "cyan")


def create_backtest_emulator(
    *,
    initial_cash,
    taker_fee,
    is_crypto,
    execution_delay,
    cost_model=None,
    allow_nominal_cost_model=False,
):
    """Build the broker's emulator without changing crypto compatibility.

    ``cost_model=None`` now resolves to LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL
    (~30 bps one-way), not the upstream nominal model (~12.8 bps). See that
    constant for why 12.8 bps does not describe a book whose names trade
    $2.4M-$7.9M a day.

    A caller that passes the nominal model *by value* gets the liquidity model
    substituted, loudly. That is not second-guessing an explicit choice: the
    only way to arrive at exactly DEFAULT_EQUITY_EXECUTION_COST_MODEL is for
    ``resolve_execution_cost_model`` to have been handed ``None``, i.e. for no
    cost scenario to have been chosen at all. Every deliberate stress arm
    carries a scaled, ``+stress``-versioned model and passes through
    untouched, so sensitivity sweeps still mean what they say. Pass
    ``allow_nominal_cost_model=True`` to reproduce a pre-2026-08 run.
    """
    if is_crypto:
        return PortfolioEmulator(
            initial_cash=initial_cash,
            taker_fee=taker_fee,
        )
    requested_version = None
    if cost_model is None:
        cost_model = LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL
    if not isinstance(cost_model,
                      (ExecutionCostModel, TieredExecutionCostModel)):
        raise ValueError("cost_model must be an ExecutionCostModel")
    if cost_model == DEFAULT_EQUITY_EXECUTION_COST_MODEL and not allow_nominal_cost_model:
        requested_version = cost_model.version
        cost_model = LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL
        print(
            "[emulator] equity cost model substituted: "
            f"{requested_version} (nominal, ~12.8 bps one-way) -> "
            f"{cost_model.version} (~30 bps one-way, sized to this book's "
            "$2.4M-$7.9M median dollar volume). Pass an explicit "
            "ExecutionCostModel or allow_nominal_cost_model=True to override."
        )
    emulator = PortfolioEmulator(
        initial_cash=initial_cash,
        taker_fee=taker_fee,
        execution_simulator=NextEventExecutionSimulator(cost_model),
        execution_delay=execution_delay,
    )
    # Surfaced so a receipt cannot quietly claim a cost basis the fills did
    # not use: broker.py hashes the model it RESOLVED into the preregistration
    # (broker.py:1481) while the fills used the model above.
    emulator._execution_cost_model_requested_version = requested_version
    return emulator
