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


@dataclass
class PositionDTO:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    created_at_utc: Optional[datetime] = None


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
