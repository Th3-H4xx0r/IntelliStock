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

#: What this book actually costs to trade -- MEASURED, not modelled.
#:
#: 2026-08-02: priced 61 of alpaca-main's 62 real live fills against the SIP
#: NBBO at each fill timestamp. Results:
#:   quoted spread   median 17.5 bps, mean 41.8, NOTIONAL-WEIGHTED 45.6, p90 109.0
#:   slippage vs mid median +0.0 bps, mean -0.6, notional-weighted +0.1
#:
#: Two findings worth keeping. First, slippage is ~ZERO: ~$500 orders cannot
#: move a book, and Alpaca's market-maker routing delivers price improvement
#: about as often as it costs (AMZN filled 30.8 bps INSIDE the mid). The
#: earlier 18 bps slippage assumption was wrong for this account and is now 0.1.
#: Second, use the NOTIONAL-WEIGHTED spread, not the median: the median trade
#: sees 17.5 bps but the larger trades are in wider-spread names, so the
#: median understates the money actually paid by 2.6x.
#:
#: One-way = 45.6/2 + 0.1 + 0.3 = 23.2 bps. Alpaca charges no commission; the
#: 0.3 bps is the SEC 31 and FINRA TAF sell-side pass-throughs.
#:
#: It lives HERE, beside the nominal model, because resolve_execution_cost_model
#: hashes whichever model it returns into the experiment receipt while the
#: emulator does the filling. With the two definitions in different modules the
#: receipt claimed `equity-next-event-v1` on runs whose fills had already been
#: substituted to this one -- a provenance record asserting a cost basis that
#: was never used.
LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL = ExecutionCostModel(
    version="equity-measured-v3-nbbo23",
    spread_bps=45.6,
    slippage_bps=0.1,
    fee_bps=0.3,
    latency=timedelta(0),
)


#: THE 8 bps ETF SPREAD IS AN ASSUMPTION, NOT A MEASUREMENT.
#: The 45.6 bps above was priced against SIP NBBO on 61 real fills. This was
#: not. It is conservative for SPY/QQQ (~1 bp quoted) and roughly right for
#: TQQQ; the first live EB fills must be priced the same way the original 61
#: were, and this preset updated if it is off by more than 2x.
#: One-way = 8.0/2 + 0.1 + 0.3 = 4.4 bps.
ETF_LIQUID_EQUITY_COST_MODEL = ExecutionCostModel(
    version="equity-etf-liquid-v1",
    spread_bps=8.0,
    slippage_bps=0.1,
    fee_bps=0.3,
    latency=timedelta(0),
)

#: Every leg Strategy EB and its siblings can trade, plus the two index proxies
#: a comparison run needs. Deliberately a CLOSED list: an open rule ("any ETF")
#: would quietly re-price a thin sector fund at mega-cap-index costs.
ETF_LIQUID_SYMBOLS = frozenset(
    {"SPY", "QQQ", "TQQQ", "QLD", "SQQQ", "BIL", "GLD", "IWM"}
)


class TieredExecutionCostModel:
    """One cost model per symbol tier, one VERSION for the whole run.

    `assert_execution_provenance_promotable` requires every fill's
    `cost_model_version` to equal the summary's. So the composite version — not
    the matched tier's version — is what gets stamped, and `as_dict()` carries
    the default model's scalars so the promotion checker's finite-number rules
    still have something to read.

    Not an `ExecutionCostModel` subclass on purpose: the dataclass is frozen and
    compared by value in `create_backtest_emulator`'s nominal-substitution
    check, and a subclass would compare equal to a plain model with the same
    fields.
    """

    __slots__ = ("_default", "_tiers", "_version", "_index")

    def __init__(self, default, tiers, version):
        if not isinstance(default, ExecutionCostModel):
            raise ValueError("default must be an ExecutionCostModel")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("version must be a non-empty string")
        index = {}
        for symbols, model in (tiers or {}).items():
            if not isinstance(model, ExecutionCostModel):
                raise ValueError("every tier must be an ExecutionCostModel")
            for symbol in symbols:
                key = str(symbol).strip().upper()
                if not key:
                    continue
                if key in index:
                    raise ValueError(f"symbol {key} appears in two tiers")
                index[key] = model
        self._default = default
        self._tiers = {frozenset(s): m for s, m in (tiers or {}).items()}
        self._version = version.strip()
        self._index = index

    @property
    def default(self):
        return self._default

    @property
    def version(self):
        return self._version

    # Delegated so callers that read a bare scalar off `cost_model` — the quote
    # constructors in portfolio_emulator and the promotion checker — keep
    # working without knowing about tiers.
    @property
    def spread_bps(self):
        return self._default.spread_bps

    @property
    def slippage_bps(self):
        return self._default.slippage_bps

    @property
    def fee_bps(self):
        return self._default.fee_bps

    @property
    def latency(self):
        return self._default.latency

    def model_for(self, symbol) -> ExecutionCostModel:
        return self._index.get(str(symbol or "").strip().upper(), self._default)

    def one_way_cost_bps_by_tier(self) -> dict:
        """One-way cost in bps for the default and for every tier.

        The headline `equity_one_way_cost_bps` reports the DEFAULT tier. That
        is the honest number for a mixed book and the WRONG one for a book that
        sits entirely inside a tier: an all-ETF run would read 23.2 bps while
        paying 4.4. This breakdown is what keeps that legible.
        """
        costs = {}
        for symbols, model in self._tiers.items():
            costs[_tier_label(model)] = _one_way_cost_bps(model)
        return {"default": _one_way_cost_bps(self._default),
                **{name: costs[name] for name in sorted(costs)}}

    def as_dict(self) -> dict:
        payload = dict(self._default.as_dict())
        payload["version"] = self._version
        payload["tiers"] = [
            {"symbols": sorted(symbols), "model": model.as_dict()}
            for symbols, model in sorted(
                self._tiers.items(), key=lambda kv: sorted(kv[0]))
        ]
        return payload


#: preset id -> (symbols, model). One preset today.
COST_TIER_PRESETS = {
    "etf-liquid": (ETF_LIQUID_SYMBOLS, ETF_LIQUID_EQUITY_COST_MODEL),
}


def _one_way_cost_bps(model) -> float:
    """Half the round-trip spread, plus slippage, plus fees."""
    return model.spread_bps / 2.0 + model.slippage_bps + model.fee_bps


def _tier_label(model) -> str:
    """The preset id a tier model came from, else its own version.

    Labelled by PRESET rather than by model version because the preset is what
    a run selects and what a reader recognises; an ad-hoc tier built without a
    preset still gets a stable, unambiguous name.
    """
    for preset, (_symbols, preset_model) in COST_TIER_PRESETS.items():
        if preset_model is model or preset_model.version == model.version:
            return preset
    return model.version


def tiered_cost_model(preset_id, default) -> TieredExecutionCostModel:
    """Build the tiered model for a named preset over `default`."""
    key = str(preset_id or "").strip()
    if key not in COST_TIER_PRESETS:
        raise ValueError(
            f"unknown execution cost tier preset {preset_id!r}; "
            f"known: {sorted(COST_TIER_PRESETS)}")
    symbols, model = COST_TIER_PRESETS[key]
    return TieredExecutionCostModel(
        default=default, tiers={symbols: model},
        version=f"equity-tiered-v1[{key}]")


@dataclass(frozen=True)
class SimulationOrder:
    order_id: str
    symbol: str
    side: str
    quantity: float
    decision_at: datetime
    execute_not_before: datetime
    source: str = "equity_backtest"
    notional_limit: float | None = None
    #: 2026-08-03 — PASSIVE execution. When set, this order RESTS at
    #: `limit_price` instead of taking the touch, and fills only if the market
    #: comes to it. None preserves today's marketable behaviour byte-for-byte.
    #:
    #: This is the largest unexploited cost lever on the book. Every fill
    #: currently crosses the spread BY CONSTRUCTION — a market buy lifts the
    #: ask, a market sell hits the bid — so the measured 22.8 bps of half-spread
    #: is paid on every side of every trade. Measured slippage vs mid is ~0.1
    #: bps, i.e. execution quality is already fine; the cost is the crossing
    #: itself, and only a resting order avoids it.
    limit_price: float | None = None
    #: Quotes this order may rest for before it is abandoned. A resting order
    #: that never fills is not free — it is a position you wanted and did not
    #: get — so an unfilled limit must EXPIRE and be visible, never linger
    #: silently. 0 means "never expire".
    expire_after_quotes: int = 0

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
        notional_limit = self.notional_limit
        if notional_limit is not None:
            notional_limit = _finite_number(
                notional_limit,
                field="notional_limit",
                positive=True,
            )
            if side != "buy":
                raise ValueError("notional_limit is only valid for buy orders")
        object.__setattr__(self, "notional_limit", notional_limit)
        limit_price = self.limit_price
        if limit_price is not None:
            limit_price = _finite_number(
                limit_price, field="limit_price", positive=True
            )
        object.__setattr__(self, "limit_price", limit_price)
        try:
            expire = int(self.expire_after_quotes or 0)
        except (TypeError, ValueError):
            raise ValueError("expire_after_quotes must be an integer")
        if expire < 0:
            raise ValueError("expire_after_quotes cannot be negative")
        object.__setattr__(self, "expire_after_quotes", expire)


@dataclass(frozen=True)
class SimulationSubmission:
    """A durable simulation intent receipt; it is not a confirmed fill."""

    order_id: str
    symbol: str
    side: str
    source: str
    accepted: bool = True
    filled: bool = False

    def __bool__(self) -> bool:
        return self.accepted


@dataclass(frozen=True)
class SimulationPriceEvent:
    """A price paired with the source bar's real availability time."""

    symbol: str
    price: float
    available_at: datetime
    bar_timestamp: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        price = _finite_number(self.price, field="price", positive=True)
        _event_seconds(self.available_at, field="available_at")
        _event_seconds(self.bar_timestamp, field="bar_timestamp")
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "price", price)


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
        if bid > ask:
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
        if spread < 0 or spread >= 20_000:
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
    order_quantity: float | None = None
    is_final: bool = False

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
        order_quantity = self.order_quantity
        if order_quantity is not None:
            order_quantity = _finite_number(
                order_quantity,
                field="order_quantity",
                positive=True,
            )
            if cumulative > order_quantity + 1e-9:
                raise ValueError(
                    "cumulative_quantity cannot exceed order_quantity"
                )
        object.__setattr__(self, "order_quantity", order_quantity)
        object.__setattr__(self, "is_final", bool(self.is_final))

    def as_dict(self) -> dict:
        result = asdict(self)
        result["quote_timestamp"] = self.quote_timestamp.isoformat()
        result["executed_at"] = self.executed_at.isoformat()
        return result


@dataclass
class _PendingOrder:
    order: SimulationOrder
    cumulative_quantity: float = 0.0
    cumulative_cost: float = 0.0
    last_fill_quote_seconds: float | None = None
    #: Quotes this order has rested through without filling (passive only).
    quotes_seen: int = 0


class InsufficientBuyingPower(ValueError):
    """Accounting refused a proposed fill: the cash is not there.

    Subclasses ValueError so every existing handler around the fill path
    behaves as it did, but distinct so `on_quote` can honour the soft-reject
    its own docstring already promises instead of letting a shortfall kill a
    three-hour run. bt 101666 died on $0.4646 — exactly the funding sale's own
    23.2 bps of execution cost.
    """


class NextEventExecutionSimulator:
    def __init__(self, cost_model: ExecutionCostModel | TieredExecutionCostModel):
        if not isinstance(cost_model,
                          (ExecutionCostModel, TieredExecutionCostModel)):
            raise ValueError(
                "cost_model must be an ExecutionCostModel or a "
                "TieredExecutionCostModel")
        self.cost_model = cost_model
        self._tiered = isinstance(cost_model, TieredExecutionCostModel)
        self._pending: dict[str, _PendingOrder] = {}
        self._known_order_ids: set[str] = set()
        self._fills: list[SimulationFill] = []
        self._rejected_order_count = 0
        self._expired_order_count = 0
        self._refused_fill_count = 0

    def _model_for(self, symbol) -> ExecutionCostModel:
        """The cost model for one symbol. Identity when untiered, so an
        untiered run's object graph is unchanged."""
        return (self.cost_model.model_for(symbol) if self._tiered
                else self.cost_model)

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
    def expired_order_count(self) -> int:
        """Passive orders abandoned unfilled.

        This is the cost that REPLACES the spread when you stop crossing it. A
        passive model that does not surface non-fills is indistinguishable from
        free money, so this must be read alongside any saving claimed from
        limit execution: a strategy that "saves" 22.8 bps while missing a third
        of its entries has not saved anything.
        """
        return self._expired_order_count

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

    def affordable_buy_quantity(
        self, cash: float, reference_price: float, symbol=None
    ) -> float:
        cash_value = _finite_number(cash, field="cash", positive=True)
        mid = _finite_number(
            reference_price, field="reference_price", positive=True
        )
        model = self._model_for(symbol)
        modeled_ask = mid * (1.0 + model.spread_bps / 20_000.0)
        fill_price = modeled_ask * (
            1.0 + model.slippage_bps / 10_000.0
        )
        all_in_per_share = fill_price * (
            1.0 + model.fee_bps / 10_000.0
        )
        return cash_value / all_in_per_share

    @property
    def refused_fill_count(self) -> int:
        """Fills accounting could not pay for. Should be 0; if it is not, the
        emulator and the simulator disagree about the cash and the run's sizing
        is suspect."""
        return self._refused_fill_count

    def on_quote(
        self,
        quote: SimulationQuote,
        *,
        accept_fill=None,
        cash_budget=None,
    ) -> tuple[SimulationFill, ...]:
        """Propose, account, then commit each fill.

        When ``accept_fill`` is supplied it runs before simulator state changes.
        If accounting rejects the candidate, the order remains pending and no
        fill provenance is recorded.
        """
        if not isinstance(quote, SimulationQuote):
            raise ValueError("quote must be a SimulationQuote")
        if accept_fill is not None and not callable(accept_fill):
            raise ValueError("accept_fill must be callable")
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
            model = self._model_for(order.symbol)
            decision_seconds = _event_seconds(
                order.decision_at, field="decision_at"
            )
            execute_seconds = _event_seconds(
                order.execute_not_before, field="execute_not_before"
            )
            eligible_seconds = max(
                execute_seconds,
                decision_seconds + model.latency.total_seconds(),
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

            mid = (quote.bid + quote.ask) / 2.0
            modeled_half_spread = (
                mid * model.spread_bps / 20_000.0
            )
            if order.limit_price is not None:
                # ── PASSIVE: the order RESTS; the market must come to it.
                #
                # Deliberately pessimistic, because an optimistic passive model
                # is indistinguishable from free money and this codebase has
                # already shipped one fantasy execution model (sub-$1 fills,
                # zero spread) that made a strategy look profitable when it was
                # not. Two rules:
                #
                #   1. A buy fills ONLY if the ASK falls to the limit — i.e. a
                #      seller crossed to us. Requiring the ask (not the mid, and
                #      not the bid) means we never assume a fill just because
                #      the quote drifted our way.
                #   2. The fill price is the LIMIT, never something better. Real
                #      price improvement exists but is not ours to assume.
                #
                # Consequence: no spread is paid, and no slippage either — we
                # set the price. What replaces that cost is NON-FILL risk,
                # which is why `expire_after_quotes` exists and why unfilled
                # orders must stay visible rather than silently resting.
                if order.side == "buy":
                    if quote.ask > order.limit_price:
                        state.quotes_seen += 1
                        if (order.expire_after_quotes
                                and state.quotes_seen >= order.expire_after_quotes):
                            completed.append(order_id)
                            self._expired_order_count += 1
                        continue
                    fill_price = order.limit_price
                else:
                    if quote.bid < order.limit_price:
                        state.quotes_seen += 1
                        if (order.expire_after_quotes
                                and state.quotes_seen >= order.expire_after_quotes):
                            completed.append(order_id)
                            self._expired_order_count += 1
                        continue
                    fill_price = order.limit_price
            elif order.side == "buy":
                touch_price = max(quote.ask, mid + modeled_half_spread)
                fill_price = touch_price * (
                    1.0 + model.slippage_bps / 10_000.0
                )
            else:
                touch_price = min(quote.bid, mid - modeled_half_spread)
                fill_price = touch_price * (
                    1.0 - model.slippage_bps / 10_000.0
                )
            fill_price = _finite_number(
                fill_price, field="fill price", positive=True
            )
            remaining = order.quantity - state.cumulative_quantity
            incremental = min(remaining, liquidity)
            all_in_per_share = fill_price * (
                1.0 + model.fee_bps / 10_000.0
            )
            if order.notional_limit is not None:
                remaining_cash = max(
                    0.0,
                    order.notional_limit - state.cumulative_cost,
                )
                incremental = min(
                    incremental,
                    remaining_cash / all_in_per_share,
                )
            # A broker fills to BUYING POWER; it does not overdraw the account.
            #
            # Sized here rather than rejected downstream on purpose: this method
            # commits `state.cumulative_quantity` from the fill it proposes, so a
            # clamp applied inside `accept_fill` would desynchronise the
            # emulator's applied quantity from the simulator's committed one and
            # make `fill_provenance` a lie.
            #
            # bt 101666 died on a $0.4646 shortfall that was exactly the funding
            # sale's own 23.2 bps of execution cost: the pending-sell credit
            # values a submitted sale at its MARK, while the sale delivers mark
            # minus half-spread, slippage and fee. The crash was the benign
            # outcome — under a different fill ordering the same gap drove cash
            # to -$200.28 with no exception at all.
            #
            # Inert unless `cash_budget` is supplied, and inert with it unless
            # the book is genuinely short.
            budget_bound = False
            if order.side == "buy" and cash_budget is not None:
                try:
                    _budget = max(0.0, float(cash_budget() or 0.0))
                except (TypeError, ValueError):
                    _budget = None
                if _budget is not None:
                    affordable = _budget / all_in_per_share
                    if affordable < incremental:
                        incremental, budget_bound = affordable, True
            if incremental <= 1e-12:
                # Own notional exhausted => the order is done. Cash-starved =>
                # NOT done: the funding sell may fill on a later quote.
                if not budget_bound:
                    completed.append(order_id)
                continue

            cumulative = state.cumulative_quantity + incremental
            notional = incremental * fill_price
            fees = notional * model.fee_bps / 10_000.0
            fill_cost = notional + fees if order.side == "buy" else 0.0
            final_by_quantity = math.isclose(
                cumulative,
                order.quantity,
                rel_tol=0,
                abs_tol=1e-12,
            )
            final_by_notional = (
                order.notional_limit is not None
                and order.notional_limit
                - (state.cumulative_cost + fill_cost)
                <= max(1e-9, order.notional_limit * 1e-12)
            )
            is_final = final_by_quantity or final_by_notional
            fill = SimulationFill(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                incremental_quantity=incremental,
                cumulative_quantity=cumulative,
                price=fill_price,
                fees=fees,
                # A passive fill crosses nothing: we posted the price and a
                # counterparty came to us. Reporting a spread here would
                # double-count — the economics are already in `price`, which is
                # the limit rather than the touch. Non-fill risk is the cost
                # that replaces it, and it shows up as expired orders.
                spread_cost=(0.0 if order.limit_price is not None
                             else abs(touch_price - mid) * incremental),
                slippage_cost=(0.0 if order.limit_price is not None
                               else abs(fill_price - touch_price) * incremental),
                quote_timestamp=quote.timestamp,
                executed_at=quote.timestamp,
                # The COMPOSITE version, never the matched tier's:
                # assert_execution_provenance_promotable requires every fill to
                # carry the summary's version, and a per-tier stamp would make
                # a mixed run permanently promotion-ineligible.
                cost_model_version=self.cost_model.version,
                source=order.source,
                order_quantity=order.quantity,
                is_final=is_final,
            )
            if accept_fill is not None:
                try:
                    accept_fill(fill)
                except InsufficientBuyingPower:
                    # The contract this method's docstring already states:
                    # "If accounting rejects the candidate, the order remains
                    # pending and no fill provenance is recorded." Until now the
                    # only rejection channel was an exception nothing caught, so
                    # a sub-dollar shortfall killed the entire run.
                    self._refused_fill_count += 1
                    continue
            state.cumulative_quantity = cumulative
            state.cumulative_cost += fill_cost
            state.last_fill_quote_seconds = quote_seconds
            self._fills.append(fill)
            emitted.append(fill)
            liquidity -= incremental
            if is_final:
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
            "refused_fill_count": self.refused_fill_count,
            "fill_provenance": [
                fill.as_dict() for fill in self._fills
            ],
        }
