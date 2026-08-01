"""
PortfolioEmulator: used only for backtesting. Tracks cash, positions, trades,
and portfolio value snapshots over time.
"""

import copy
from datetime import timedelta
import math

try:
    from simulated_execution import (
        DEFAULT_EQUITY_EXECUTION_COST_MODEL,
        ExecutionCostModel,
        NextEventExecutionSimulator,
        SimulationFill,
        SimulationOrder,
        SimulationPriceEvent,
        SimulationQuote,
        SimulationSubmission,
    )
except ImportError:  # Package import path used by repository-root pytest.
    from backend.simulated_execution import (
        DEFAULT_EQUITY_EXECUTION_COST_MODEL,
        ExecutionCostModel,
        NextEventExecutionSimulator,
        SimulationFill,
        SimulationOrder,
        SimulationPriceEvent,
        SimulationQuote,
        SimulationSubmission,
    )

# Crypto is NEVER commission-free: backtest fills must model the taker fee.
# Source the fee from the crypto core so sizing and fills stay in lock-step;
# guard the import so any failure falls back to the known taker fee and never
# breaks the (commission-free) equity paths. Only ever applied to crypto
# symbols (detected via "/" in the ticker — equities never contain a slash).
try:  # pragma: no cover - trivial import guard
    from strategies.crypto.core import CRYPTO_FEES as _CRYPTO_FEES
    _CRYPTO_TAKER_FEE = float(_CRYPTO_FEES["taker"])
except Exception:  # pragma: no cover - fall back so equity paths never break
    _CRYPTO_TAKER_FEE = 0.0025


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
        # Crypto fee accounting (equities are commission-free, so these stay 0):
        # total taker fees actually charged, and total crypto notional traded
        # (gross, both legs) — the base for per-platform fee estimates.
        self._crypto_fees_paid = 0.0
        self._crypto_volume = 0.0
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

    def buy(self, ticker, shares, price, timestamp=None):
        """
        Execute a buy: spend cash and add to positions.
        ticker: str
        shares: float (can be fractional)
        price: float (price per share)
        timestamp: optional datetime for the trade record (caller can pass current_time)
        Returns: True if trade was executed, False if insufficient cash.
        """
        if shares <= 0 or price <= 0:
            return False
        total = shares * price
        if total > self._cash:
            return False
        self._cash -= total
        # Crypto is NOT commission-free: the taker fee is charged on notional, so
        # the same cash (the full `total`, still decremented above) buys fewer
        # coins. Equity symbols (no "/") keep the EXACT commission-free math.
        filled_shares = shares
        if "/" in ticker:
            filled_shares = shares * (1.0 - self._taker_fee)
            # The taker fee is embedded as fewer coins; record it (and the gross
            # notional) for the backtest fee summary.
            self._crypto_fees_paid += total * self._taker_fee
            self._crypto_volume += total
        self._positions[ticker] = self._positions.get(ticker, 0.0) + filled_shares
        self._trades.append({
            "timestamp": timestamp,
            "action": "buy",
            "ticker": ticker,
            "shares": filled_shares,
            "price": price,
            "total": total,
            "cash_after": self._cash,
        })
        print(f"TRADE: Buy {filled_shares} shares of {ticker} at {price} for a total of {total} cash_after: {self._cash}")
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
        total = shares * price
        # Crypto is NOT commission-free: the taker fee is charged on notional, so
        # proceeds credited to cash are net of the fee. Equity symbols (no "/")
        # keep the EXACT commission-free math.
        if "/" in ticker:
            self._crypto_fees_paid += total * self._taker_fee
            self._crypto_volume += total
            total = total * (1.0 - self._taker_fee)
        self._cash += total
        self._positions[ticker] = current - shares
        if self._positions[ticker] <= 0:
            del self._positions[ticker]
        self._trades.append({
            "timestamp": timestamp,
            "action": "sell",
            "ticker": ticker,
            "shares": shares,
            "price": price,
            "total": total,
            "cash_after": self._cash,
        })
        print(f"TRADE: Sell {shares} shares of {ticker} at {price} for a total of {total} cash_after: {self._cash}")
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

    # Ratios a real corporate action actually uses. A genuine one-bar move of
    # this size that ALSO lands within 1.5% of one of these is not a market
    # move — intraday moves that large halt the tape, and they do not arrive at
    # exactly 8.000x.
    _SPLIT_RATIOS = (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 15.0, 20.0)

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
        if not bool(getattr(self, "_split_reconcile_enabled", True)):
            return []
        last = getattr(self, "_last_prices", None) or {}
        adjusted = []
        for ticker, shares in list(self._positions.items()):
            new_px = (prices or {}).get(ticker)
            old_px = last.get(ticker)
            try:
                new_px = float(new_px or 0.0)
                old_px = float(old_px or 0.0)
            except (TypeError, ValueError):
                continue
            if new_px <= 0 or old_px <= 0 or shares <= 0:
                continue
            for ratio in self._SPLIT_RATIOS:
                # forward split: price falls by `ratio`, shares multiply by it
                for factor, observed in ((ratio, old_px / new_px),
                                         (1.0 / ratio, new_px / old_px)):
                    if abs(observed / ratio - 1.0) <= 0.015:
                        new_shares = shares * (old_px / new_px)
                        self._positions[ticker] = new_shares
                        adjusted.append((ticker, old_px / new_px, shares, new_shares))
                        break
                else:
                    continue
                break
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
        """Cash available for non-reserving strategies (e.g. total_cash - reserved_capital)."""
        return max(0.0, self._cash - float(reserved))

    def get_positions(self):
        """Current positions: dict ticker -> shares (copy)."""
        return copy.copy(self._positions)

    def get_positions_value(self, prices):
        """Value of positions only (excluding cash) using given prices."""
        v = 0.0
        for ticker, shares in self._positions.items():
            p = (prices or {}).get(ticker)
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

        gross = delta * fill.price
        if fill.side == "buy":
            cash_delta = gross + fill.fees
            if cash_delta > self._cash + 1e-9:
                raise ValueError("confirmed buy fill exceeds available cash")
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
        if new_quantity > 1e-12:
            self._positions[fill.symbol] = new_quantity
        else:
            self._positions.pop(fill.symbol, None)
        self._last_prices[fill.symbol] = fill.price
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
                spread_bps=self._execution_simulator.cost_model.spread_bps,
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
                spread_bps=self._execution_simulator.cost_model.spread_bps,
            )
            emitted.extend(self.process_quote(quote))
        return tuple(emitted)

    def get_execution_summary(self):
        if self._execution_simulator is None:
            return {
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
        summary = self._execution_simulator.execution_summary()
        summary["execution_provenance_complete"] = bool(
            summary.get("execution_provenance_complete")
            and self._execution_event_provenance_complete
        )
        if not self._execution_event_provenance_complete:
            summary["execution_provenance_error"] = (
                "price event availability was relabeled"
            )
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
                amount_to_use = min(
                    cash_per_trade,
                    max(0.0, self._cash - reserved_cash),
                )
                if amount_to_use <= 0:
                    return False
                shares = self._execution_simulator.affordable_buy_quantity(
                    amount_to_use, price
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
            order = SimulationOrder(
                order_id=order_id,
                symbol=ticker,
                side=side,
                quantity=shares,
                decision_at=timestamp,
                execute_not_before=timestamp + self._execution_delay,
                source=order_source.strip(),
                notional_limit=amount_to_use if side == "buy" else None,
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
            amount_to_use = min(cash_per_trade, self._cash)
            if amount_to_use <= 0:
                return False
            shares = amount_to_use / price
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
):
    """Build the broker's emulator without changing crypto compatibility."""
    if is_crypto:
        return PortfolioEmulator(
            initial_cash=initial_cash,
            taker_fee=taker_fee,
        )
    if cost_model is None:
        cost_model = DEFAULT_EQUITY_EXECUTION_COST_MODEL
    if not isinstance(cost_model, ExecutionCostModel):
        raise ValueError("cost_model must be an ExecutionCostModel")
    return PortfolioEmulator(
        initial_cash=initial_cash,
        taker_fee=taker_fee,
        execution_simulator=NextEventExecutionSimulator(cost_model),
        execution_delay=execution_delay,
    )
