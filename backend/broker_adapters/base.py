"""BrokerAdapter ABC + DTOs.

The ABC intentionally mirrors the PortfolioEmulator surface including the
private attributes _positions, _trades, _initial_value, _cash, _last_prices.
A 34-site audit of strategies found these attrs accessed directly; we preserve
pass-through so live mode works without touching strategy code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class OrderRef:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    qty: float
    status: str
    filled_qty: float = 0.0
    filled_avg_price: Optional[float] = None
    submitted_at_utc: Optional[datetime] = None
    # swing-port (interfaces doc section 3). Defaults keep every old caller.
    order_class: Optional[str] = None
    legs: tuple = ()
    position_intent: Optional[str] = None
    asset_class: Optional[str] = None
    # swing-port (plan A-live contract additions): tell a take-profit leg
    # (limit) from a stop-loss leg (stop), and carry their prices.
    order_type: Optional[str] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None


@dataclass
class PositionDTO:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    created_at_utc: Optional[datetime] = None
    # swing-port: filled only for option rows (refresh_positions), so every
    # equity row is exactly what it was.
    asset_class: Optional[str] = None
    side: Optional[str] = None
    unrealized_pl: Optional[float] = None
    unrealized_plpc: Optional[float] = None
    current_price: Optional[float] = None
    multiplier: int = 1
    underlying: Optional[str] = None
    # Ruling F4 (A-live pre-flight): "put" | "call", from the contract fields.
    option_type: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None


@dataclass
class CashDTO:
    cash: float
    buying_power: float
    daytrading_buying_power: float
    unsettled: float = 0.0


@dataclass
class AccountDTO:
    equity: float
    pattern_day_trader: bool
    daytrade_count: int
    account_blocked: bool
    trading_blocked: bool
    # Added 2026-04-21 so LiveState mirrors the Alpaca UI (Buying Power +
    # authoritative Daily Change). Optional/default to 0 for older callers.
    buying_power: float = 0.0
    last_equity: float = 0.0
    cash: float = 0.0
    # swing-port: options approval and buying power (interfaces doc section 3).
    options_trading_level: Optional[int] = None
    options_approved_level: Optional[int] = None
    options_buying_power: Optional[float] = None
    non_marginable_buying_power: Optional[float] = None


@dataclass(frozen=True)
class OptionContractDTO:
    symbol: str
    underlying: str
    option_type: str
    strike: float
    expiration: str
    open_interest: Optional[int]
    close_price: Optional[float]


@dataclass(frozen=True)
class OptionSnapshotDTO:
    symbol: str
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    quote_ts: Optional[str]


@dataclass(frozen=True)
class OptionPositionDTO:
    symbol: str
    underlying: str
    option_type: str
    strike: float
    expiry: str
    qty: int                      # signed; negative = short
    avg_entry_price: float        # per-share premium
    current_price: Optional[float]
    market_value: Optional[float]
    unrealized_pl: Optional[float]
    multiplier: int = 100


@dataclass(frozen=True)
class OptionActivityDTO:
    id: str
    activity_type: str            # "OPASN" | "OPEXP" | "OPEXC"
    symbol: str
    qty: float
    date: str
    price: Optional[float]


@dataclass
class HealthStatus:
    auth_fresh: bool
    trade_updates_connected: bool
    last_heartbeat_utc: Optional[datetime]
    errors: list[str] = field(default_factory=list)


class BrokerAdapter(ABC):
    """Broker abstraction. Exposes PortfolioEmulator-compatible private attrs
    for strategy pass-through. Implementations MUST populate these attributes
    at construction and keep them in sync via the trade_updates stream or
    periodic REST refresh.
    """

    _positions: dict[str, float]
    _trades: list[dict]
    _initial_value: float
    _cash: float
    _last_prices: dict[str, float]
    # Populated only by adapters running in clean_room_mode. Quarantine
    # for broker-side positions that lacked WAL provenance at boot — the
    # strategy NEVER reads this. For operator visibility (audit + alerts).
    _external_positions: dict[str, dict]

    # --- Current market marks (Task 4, benchmark-alpha Stage A) ---
    def get_market_marks(self) -> dict:
        """Return a copy of the adapter's current typed market marks.

        Adapters that maintain a ``MarketMarkBook`` (``_market_marks``) return
        its snapshot; others return ``{}``. ``_last_prices`` remains a
        temporary compatibility mirror of the newest mark price — it carries
        no timestamp and must never be treated as fresh live data.
        """
        book = getattr(self, "_market_marks", None)
        if book is None:
            return {}
        return dict(book.snapshot())

    def decision_price(self, symbol: str, now: datetime):
        """Resolve a price eligible to authorize an exposure increase.

        Returns ``(price, PurposeCheck)`` from a mark passing the DECISION
        purpose policy, or ``None``. Never falls back to a fill price, a
        stale mark, or an untimestamped scalar cache — callers must fail
        closed when this returns ``None``.
        """
        from market_marks import MarkPurpose, evaluate_mark
        mark = self.get_market_marks().get(str(symbol).upper())
        if mark is None:
            return None
        check = evaluate_mark(mark, MarkPurpose.DECISION, now)
        if not check.allowed:
            return None
        return mark.price, check

    # --- External-position quarantine (clean-room mode) ---
    def get_external_positions(self) -> dict[str, dict]:
        """Return the quarantined external positions dict (empty if not
        in clean_room_mode). Concrete adapters populate ``_external_positions``
        during __init__ when ``clean_room_mode=True``; otherwise this stays
        ``{}`` and the strategy sees no externals."""
        return getattr(self, "_external_positions", {}) or {}

    # --- Order submission ---
    @abstractmethod
    def submit_order(
        self,
        symbol: str,
        side: str,
        qty: Optional[float],
        notional: Optional[float],
        order_type: str,
        limit_price: Optional[float],
        tif: str,
        extended_hours: bool,
        client_order_id: str,
    ) -> OrderRef: ...

    @abstractmethod
    def cancel_order(self, broker_order_id: str) -> bool: ...

    @abstractmethod
    def get_order(self, broker_order_id: str) -> OrderRef: ...

    @abstractmethod
    def get_order_by_client_id(self, client_order_id: str) -> Optional[OrderRef]: ...

    @abstractmethod
    def list_open_orders(self, limit: int = 200) -> list[OrderRef]: ...

    # --- REST refresh ---
    @abstractmethod
    def refresh_positions(self) -> list[PositionDTO]: ...

    @abstractmethod
    def refresh_cash(self) -> CashDTO: ...

    @abstractmethod
    def refresh_account(self) -> AccountDTO: ...

    @abstractmethod
    def is_market_open(self, now_utc: datetime) -> bool: ...

    @abstractmethod
    def health_check(self) -> HealthStatus: ...

    # --- PortfolioEmulator compatibility shims ---
    @abstractmethod
    def buy(self, ticker: str, shares: float, price: float, timestamp: Optional[datetime] = None) -> bool: ...

    @abstractmethod
    def sell(self, ticker: str, shares: float, price: float, timestamp: Optional[datetime] = None) -> bool: ...

    @abstractmethod
    def execute_signal(
        self,
        ticker: str,
        signal: int,
        price: float,
        timestamp: Optional[datetime] = None,
        cash_per_trade: float = 1000.0,
        sell_fraction: float = 1.0,
    ) -> bool: ...

    @abstractmethod
    def get_positions(self) -> dict[str, float]: ...

    @abstractmethod
    def get_positions_value(self, prices: dict[str, float]) -> float: ...

    @abstractmethod
    def get_portfolio_value(self, prices: dict[str, float]) -> float: ...

    @abstractmethod
    def get_trade_history(self) -> list[dict]: ...

    @abstractmethod
    def get_portfolio_history(self) -> list[dict]: ...

    @abstractmethod
    def get_cash(self) -> float: ...

    @abstractmethod
    def get_available_cash(self, reserved: float = 0.0) -> float: ...

    @abstractmethod
    def get_initial_value(self) -> float: ...

    @abstractmethod
    def save_portfolio_snapshot(self, prices: dict[str, float], timestamp: Optional[datetime] = None) -> None: ...

    @abstractmethod
    def print_portfolio(self, prices: dict[str, float], logger: Any = None) -> None: ...

    # --- swing-port: options, brackets, reference data ------------------------
    # Non-abstract on purpose: an adapter that does not trade options
    # (Binance.US, every test double) inherits these and refuses loudly only
    # if something actually asks it to.
    def get_option_chain(self, underlying, *, option_type=None,
                         expiration_gte=None, expiration_lte=None,
                         strike_gte=None, strike_lte=None) -> dict:
        raise NotImplementedError(f"{type(self).__name__} does not support get_option_chain")

    def get_option_contracts(self, underlying, *, option_type=None,
                             expiration_gte=None, expiration_lte=None,
                             strike_gte=None, strike_lte=None) -> list:
        raise NotImplementedError(f"{type(self).__name__} does not support get_option_contracts")

    def get_option_snapshots(self, contracts) -> dict:
        raise NotImplementedError(f"{type(self).__name__} does not support get_option_snapshots")

    def list_option_positions(self) -> list:
        raise NotImplementedError(f"{type(self).__name__} does not support list_option_positions")

    def get_account_options(self) -> dict:
        raise NotImplementedError(f"{type(self).__name__} does not support get_account_options")

    def get_option_activities(self, types=("OPASN", "OPEXP", "OPEXC"),
                              after=None) -> list:
        raise NotImplementedError(f"{type(self).__name__} does not support get_option_activities")

    def get_order_with_legs(self, order_id) -> "OrderRef":
        raise NotImplementedError(f"{type(self).__name__} does not support get_order_with_legs")

    def cancel_orders_confirmed(self, order_ids, timeout_s: float = 10.0, *,
                                booked_fills=None) -> bool:
        raise NotImplementedError(f"{type(self).__name__} does not support cancel_orders_confirmed")

    def list_closed_orders(self, symbols, after) -> list:
        raise NotImplementedError(f"{type(self).__name__} does not support list_closed_orders")

    def get_daily_bars(self, symbols, days) -> dict:
        raise NotImplementedError(f"{type(self).__name__} does not support get_daily_bars")

    def get_latest_trades(self, symbols) -> dict:
        raise NotImplementedError(f"{type(self).__name__} does not support get_latest_trades")

    def option_positions_health(self) -> dict:
        """``{"complete": bool, "stale_since": <stamp or None>}`` for the
        option book (plan B G7 review I-1). Refuses here, like every option
        method above: an adapter that cannot trade options must never be read
        as holding a complete, empty option book. A caller treats the refusal
        as "unknown"."""
        raise NotImplementedError(f"{type(self).__name__} does not support option_positions_health")


#: Order classes whose exit legs are conditional children of another order.
_MULTI_LEG_CLASSES = frozenset({"bracket", "oco", "oto"})
_CLOSING_POSITION_INTENTS = frozenset({"buy_to_close", "sell_to_close"})


def _enum_text(value) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def is_bracket_child_order(order) -> bool:
    """True for a conditional exit leg of a multi-leg equity order.

    Alpaca reports the take-profit and stop-loss legs of a bracket as separate
    orders sharing ``order_class == "bracket"``; the stop waits in ``held``.
    IntelliStock submits only BUY-entry brackets (the gate refuses equity sells
    that are not reduce-only), so inside a multi-leg class every SELL, and
    every ``held`` order, is a child exit. A ``held`` order OUTSIDE a multi-leg
    class, which Alpaca does not produce, is NOT ignored: guards stay closed
    on what they cannot explain.
    """
    if _enum_text(getattr(order, "order_class", None)) not in _MULTI_LEG_CLASSES:
        return False
    return (
        _enum_text(getattr(order, "status", None)) == "held"
        or _enum_text(getattr(order, "side", None)) == "sell"
    )


def is_risk_reducing_order(order) -> bool:
    """True for an order that only ever REDUCES exposure: a bracket child leg,
    or an OPTION buy/sell-to-close. Kill-level and halt cancellation leave these.

    Ruling F1 (A-live pre-flight, 2026-09-24): ``position_intent`` counts only
    on an order whose ``asset_class`` is ``us_option``. Alpaca may tag a stock
    order with a position_intent (``sell_to_close`` on an EB trim), and a halt
    or kill rung on alpaca-main must keep cancelling EB's working stock sells
    exactly as it did before this helper existed. An order with no asset_class
    is therefore never risk-reducing by its position_intent.
    """
    if is_bracket_child_order(order):
        return True
    return (
        _enum_text(getattr(order, "asset_class", None)) == "us_option"
        and _enum_text(getattr(order, "position_intent", None))
        in _CLOSING_POSITION_INTENTS
    )


def is_opening_option_sell(order) -> bool:
    """True for an OPTION sell-to-open: a SELL that opens a strike x 100
    obligation (swing-port fix wave, FW-lo-I4). The kill rung cancels it with
    the working buys.

    Ruling F1 applies as in ``is_risk_reducing_order``: ``position_intent``
    counts only on an order whose ``asset_class`` is ``us_option``, so an EB
    stock sell is never one, whatever Alpaca tags it with.
    """
    return (
        _enum_text(getattr(order, "asset_class", None)) == "us_option"
        and _enum_text(getattr(order, "position_intent", None)) == "sell_to_open"
    )
