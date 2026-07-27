"""Deterministic next-event execution for promotable equity backtests.

The simulator accepts immutable orders and two-sided quotes.  An order can
only consume a quote strictly after its decision event and at or after its
configured execution/latency boundary.  It emits immutable, cumulative fill
events; it never mutates portfolio accounting itself.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import math
import sys


# IntelliStock executes backend files both as top-level modules (broker.py adds
# ``backend`` to sys.path) and as ``backend.*`` packages under pytest.  Keep one
# module identity so immutable event classes survive either import route.
if __name__ == "simulated_execution":
    sys.modules.setdefault("backend.simulated_execution", sys.modules[__name__])
elif __name__ == "backend.simulated_execution":
    sys.modules.setdefault("simulated_execution", sys.modules[__name__])


def _finite_number(value, *, field: str, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def _event_seconds(value, *, field: str) -> float:
    if not isinstance(value, datetime):
        raise ValueError(f"{field} must be a datetime")
    # The legacy equity backtest clock is naive.  Interpret it consistently as
    # UTC while accepting the aware UTC values used by the new event-time API.
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    else:
        normalized = normalized.astimezone(timezone.utc)
    return normalized.timestamp()


@dataclass(frozen=True)
class ExecutionCostModel:
    version: str
    spread_bps: float
    slippage_bps: float
    fee_bps: float
    latency: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a non-empty string")
        object.__setattr__(self, "version", self.version.strip())
        for field in ("spread_bps", "slippage_bps", "fee_bps"):
            number = _finite_number(getattr(self, field), field=field)
            if number < 0 or number >= 10_000:
                raise ValueError(f"{field} must be between 0 and 10000")
            object.__setattr__(self, field, number)
        if not isinstance(self.latency, timedelta):
            raise ValueError("latency must be a timedelta")
        latency_seconds = self.latency.total_seconds()
        if not math.isfinite(latency_seconds) or latency_seconds < 0:
            raise ValueError("latency must be finite and nonnegative")

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "spread_bps": self.spread_bps,
            "slippage_bps": self.slippage_bps,
            "fee_bps": self.fee_bps,
            "latency_seconds": self.latency.total_seconds(),
        }


DEFAULT_EQUITY_EXECUTION_COST_MODEL = ExecutionCostModel(
    version="equity-next-event-v1",
    spread_bps=5.0,
    slippage_bps=10.0,
    fee_bps=0.3,
    latency=timedelta(0),
)


@dataclass(frozen=True)
class SimulationOrder:
    order_id: str
    symbol: str
    side: str
    quantity: float
    decision_at: datetime
    execute_not_before: datetime
    source: str = "equity_backtest"

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError("order_id must be a non-empty string")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        side = str(self.side).strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        quantity = _finite_number(self.quantity, field="quantity", positive=True)
        decision = _event_seconds(self.decision_at, field="decision_at")
        execute = _event_seconds(
            self.execute_not_before, field="execute_not_before"
        )
        if execute < decision:
            raise ValueError("execute_not_before cannot precede decision_at")
        object.__setattr__(self, "order_id", self.order_id.strip())
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "source", str(self.source or "equity_backtest"))


@dataclass(frozen=True)
class SimulationQuote:
    symbol: str
    timestamp: datetime
    bid: float
    ask: float
    available_quantity: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        _event_seconds(self.timestamp, field="timestamp")
        bid = _finite_number(self.bid, field="bid", positive=True)
        ask = _finite_number(self.ask, field="ask", positive=True)
        if bid >= ask:
            raise ValueError("bid must be less than ask")
        available = self.available_quantity
        if available is not None:
            available = _finite_number(
                available, field="available_quantity", positive=True
            )
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "bid", bid)
        object.__setattr__(self, "ask", ask)
        object.__setattr__(self, "available_quantity", available)

    @classmethod
    def from_mid(
        cls,
        *,
        symbol: str,
        timestamp: datetime,
        mid: float,
        spread_bps: float,
        available_quantity: float | None = None,
    ) -> "SimulationQuote":
        mid_value = _finite_number(mid, field="mid", positive=True)
        spread = _finite_number(spread_bps, field="spread_bps")
        if spread <= 0 or spread >= 20_000:
            raise ValueError("spread_bps must be between 0 and 20000")
        half = spread / 20_000.0
        return cls(
            symbol=symbol,
            timestamp=timestamp,
            bid=mid_value * (1.0 - half),
            ask=mid_value * (1.0 + half),
            available_quantity=available_quantity,
        )


@dataclass(frozen=True)
class SimulationFill:
    order_id: str
    symbol: str
    side: str
    incremental_quantity: float
    cumulative_quantity: float
    price: float
    fees: float
    spread_cost: float
    slippage_cost: float
    quote_timestamp: datetime
    executed_at: datetime
    cost_model_version: str
    source: str = "equity_backtest"

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError("order_id must be a non-empty string")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        side = str(self.side).strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        incremental = _finite_number(
            self.incremental_quantity,
            field="incremental_quantity",
            positive=True,
        )
        cumulative = _finite_number(
            self.cumulative_quantity,
            field="cumulative_quantity",
            positive=True,
        )
        if incremental > cumulative:
            raise ValueError(
                "incremental_quantity cannot exceed cumulative_quantity"
            )
        price = _finite_number(self.price, field="price", positive=True)
        costs = {}
        for field in ("fees", "spread_cost", "slippage_cost"):
            value = _finite_number(getattr(self, field), field=field)
            if value < 0:
                raise ValueError(f"{field} must be nonnegative")
            costs[field] = value
        quote_seconds = _event_seconds(
            self.quote_timestamp, field="quote_timestamp"
        )
        executed_seconds = _event_seconds(self.executed_at, field="executed_at")
        if executed_seconds < quote_seconds:
            raise ValueError("executed_at cannot precede quote_timestamp")
        if (
            not isinstance(self.cost_model_version, str)
            or not self.cost_model_version.strip()
        ):
            raise ValueError("cost_model_version must be a non-empty string")
        object.__setattr__(self, "order_id", self.order_id.strip())
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "incremental_quantity", incremental)
        object.__setattr__(self, "cumulative_quantity", cumulative)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "fees", costs["fees"])
        object.__setattr__(self, "spread_cost", costs["spread_cost"])
        object.__setattr__(self, "slippage_cost", costs["slippage_cost"])
        object.__setattr__(
            self, "cost_model_version", self.cost_model_version.strip()
        )
        object.__setattr__(self, "source", str(self.source or "equity_backtest"))

    def as_dict(self) -> dict:
        result = asdict(self)
        result["quote_timestamp"] = self.quote_timestamp.isoformat()
        result["executed_at"] = self.executed_at.isoformat()
        return result


@dataclass
class _PendingOrder:
    order: SimulationOrder
    cumulative_quantity: float = 0.0
    last_fill_quote_seconds: float | None = None


class NextEventExecutionSimulator:
    def __init__(self, cost_model: ExecutionCostModel):
        if not isinstance(cost_model, ExecutionCostModel):
            raise ValueError("cost_model must be an ExecutionCostModel")
        self.cost_model = cost_model
        self._pending: dict[str, _PendingOrder] = {}
        self._known_order_ids: set[str] = set()
        self._fills: list[SimulationFill] = []
        self._rejected_order_count = 0

    @property
    def pending_orders(self) -> tuple[SimulationOrder, ...]:
        return tuple(state.order for state in self._pending.values())

    @property
    def pending_symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(order.symbol for order in self.pending_orders))

    @property
    def pending_order_count(self) -> int:
        return len(self._pending)

    @property
    def rejected_order_count(self) -> int:
        return self._rejected_order_count

    @property
    def fills(self) -> tuple[SimulationFill, ...]:
        return tuple(self._fills)

    def submit(self, order: SimulationOrder) -> None:
        if not isinstance(order, SimulationOrder):
            self._rejected_order_count += 1
            raise ValueError("order must be a SimulationOrder")
        if order.order_id in self._known_order_ids:
            self._rejected_order_count += 1
            raise ValueError(f"duplicate order_id: {order.order_id}")
        self._known_order_ids.add(order.order_id)
        self._pending[order.order_id] = _PendingOrder(order=order)

    def affordable_buy_quantity(self, cash: float, reference_price: float) -> float:
        cash_value = _finite_number(cash, field="cash", positive=True)
        mid = _finite_number(
            reference_price, field="reference_price", positive=True
        )
        modeled_ask = mid * (1.0 + self.cost_model.spread_bps / 20_000.0)
        fill_price = modeled_ask * (
            1.0 + self.cost_model.slippage_bps / 10_000.0
        )
        all_in_per_share = fill_price * (
            1.0 + self.cost_model.fee_bps / 10_000.0
        )
        return cash_value / all_in_per_share

    def on_quote(self, quote: SimulationQuote) -> tuple[SimulationFill, ...]:
        if not isinstance(quote, SimulationQuote):
            raise ValueError("quote must be a SimulationQuote")
        quote_seconds = _event_seconds(quote.timestamp, field="timestamp")
        liquidity = (
            math.inf
            if quote.available_quantity is None
            else quote.available_quantity
        )
        emitted: list[SimulationFill] = []
        completed: list[str] = []

        for order_id, state in tuple(self._pending.items()):
            order = state.order
            if order.symbol != quote.symbol or liquidity <= 0:
                continue
            decision_seconds = _event_seconds(
                order.decision_at, field="decision_at"
            )
            execute_seconds = _event_seconds(
                order.execute_not_before, field="execute_not_before"
            )
            eligible_seconds = max(
                execute_seconds,
                decision_seconds + self.cost_model.latency.total_seconds(),
            )
            if (
                quote_seconds <= decision_seconds
                or quote_seconds < eligible_seconds
                or (
                    state.last_fill_quote_seconds is not None
                    and quote_seconds <= state.last_fill_quote_seconds
                )
            ):
                continue

            remaining = order.quantity - state.cumulative_quantity
            incremental = min(remaining, liquidity)
            if incremental <= 0:
                continue

            mid = (quote.bid + quote.ask) / 2.0
            modeled_half_spread = (
                mid * self.cost_model.spread_bps / 20_000.0
            )
            if order.side == "buy":
                touch_price = max(quote.ask, mid + modeled_half_spread)
                fill_price = touch_price * (
                    1.0 + self.cost_model.slippage_bps / 10_000.0
                )
            else:
                touch_price = min(quote.bid, mid - modeled_half_spread)
                fill_price = touch_price * (
                    1.0 - self.cost_model.slippage_bps / 10_000.0
                )
            fill_price = _finite_number(
                fill_price, field="fill price", positive=True
            )
            cumulative = state.cumulative_quantity + incremental
            notional = incremental * fill_price
            fill = SimulationFill(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                incremental_quantity=incremental,
                cumulative_quantity=cumulative,
                price=fill_price,
                fees=notional * self.cost_model.fee_bps / 10_000.0,
                spread_cost=abs(touch_price - mid) * incremental,
                slippage_cost=abs(fill_price - touch_price) * incremental,
                quote_timestamp=quote.timestamp,
                executed_at=quote.timestamp,
                cost_model_version=self.cost_model.version,
                source=order.source,
            )
            state.cumulative_quantity = cumulative
            state.last_fill_quote_seconds = quote_seconds
            self._fills.append(fill)
            emitted.append(fill)
            liquidity -= incremental
            if math.isclose(cumulative, order.quantity, rel_tol=0, abs_tol=1e-12):
                completed.append(order_id)

        for order_id in completed:
            self._pending.pop(order_id, None)
        return tuple(emitted)

    def execution_summary(self) -> dict:
        return {
            "execution_provenance_complete": True,
            "execution_cost_model_version": self.cost_model.version,
            "execution_cost_model": self.cost_model.as_dict(),
            "total_fees": sum(fill.fees for fill in self._fills),
            "spread_cost": sum(fill.spread_cost for fill in self._fills),
            "slippage_cost": sum(fill.slippage_cost for fill in self._fills),
            "unfilled_order_count": self.pending_order_count,
            "rejected_order_count": self.rejected_order_count,
            "fill_provenance": [
                fill.as_dict() for fill in self._fills
            ],
        }
