"""Typed, immutable values crossing the live stock-order boundary."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional


class Health(str, Enum):
    """Tri-state dependency health used to make unknown explicit."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderSource(str, Enum):
    STRATEGY = "strategy"
    MANUAL = "manual"
    RISK_EXIT = "risk_exit"
    RESIDUAL_SLEEVE = "residual_sleeve"
    # swing-port: a reduce-only child leg of a bracket parent, registered from
    # the parent's nested legs so a leg fill resolves to a known record.
    BRACKET_LEG = "bracket_leg"
    # swing-port: a broker-originated fill with no order behind it (an option
    # assignment adds or removes the underlying's shares).
    OPTION_ACTIVITY = "option_activity"


#: The interfaces contract's name for the bracket-leg lifecycle source.
BRACKET_LEG = OrderSource.BRACKET_LEG.value

#: swing-port value sets.
ASSET_CLASSES = ("us_equity", "us_option")
POSITION_INTENTS = ("buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close")
_POSITION_INTENT_SIDE = {
    "buy_to_open": "buy",
    "buy_to_close": "buy",
    "sell_to_open": "sell",
    "sell_to_close": "sell",
}
#: Sources whose rows are keyed by an id the BROKER minted, not by our hash.
_BROKER_KEYED_SOURCES = frozenset({"bracket_leg", "option_activity"})
#: (field, default). A field joins the identity dict only when it differs from
#: this default -- which is what keeps every stored EB row on its existing key.
_SWING_IDENTITY_DEFAULTS = (
    ("asset_class", "us_equity"),
    ("order_class", None),
    ("position_intent", None),
    ("contract_multiplier", 1),
    ("underlying", None),
    ("option_type", None),
    ("strike", None),
    ("expiry", None),
    ("parent_client_order_id", None),
    ("broker_client_order_id", None),
)
#: The stored row carries the identity fields plus the two leg prices. The leg
#: prices are row-only: they are sizing, like limit_price, and must never
#: re-key a re-emitted entry.
SWING_ROW_DEFAULTS = _SWING_IDENTITY_DEFAULTS + (
    ("take_profit_price", None),
    ("stop_loss_price", None),
)


class LifecycleState(str, Enum):
    """Durable states for one immutable client-order identity."""

    INTENT = "intent"
    SUBMITTING = "submitting"
    UNKNOWN = "unknown"
    ACKNOWLEDGED = "acknowledged"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


TERMINAL_LIFECYCLE_STATES = frozenset(
    {
        LifecycleState.FILLED,
        LifecycleState.CANCELED,
        LifecycleState.REJECTED,
        LifecycleState.EXPIRED,
    }
)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _decimal(value, field_name: str, *, positive: bool = False) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must be decimal-compatible") from exc
    if not result.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{field_name} must be > 0")
    return result


def _optional_decimal(value, field_name: str) -> Optional[Decimal]:
    if value is None:
        return None
    return _decimal(value, field_name)


def _normalized_decimal(value: Decimal) -> str:
    # ``format(..., 'f')`` avoids exponent spellings changing the hash.
    rendered = format(value.normalize(), "f")
    return "0" if rendered in ("-0", "") else rendered


def _session_date(value: datetime) -> str:
    """Collapse a decision timestamp onto its US-equities trading date.

    The exchange day, not the UTC day: 00:00 UTC lands at 19:00 ET in winter,
    which would split a single after-hours session across two identities.
    """

    try:
        from zoneinfo import ZoneInfo

        return value.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d")


def instance_key_prefix(instance_id) -> str:
    """The prefix every hash-keyed client order id of this instance carries:
    its first 8 alphanumerics (or "x") and a dash. The same string as
    broker_adapters._classifier.derive_cid_prefix and the reconciler's
    cid_prefix in broker.py (pinned in tests/test_swing_live_service.py)."""
    return f"{re.sub(r'[^A-Za-z0-9]', '', str(instance_id or ''))[:8] or 'x'}-"


def _optional_text(value, *, case: str = "") -> Optional[str]:
    """A stripped string, or None for None or blank; ``case`` folds it."""
    if value is None:
        return None
    text = str(value).strip()
    if case == "upper":
        text = text.upper()
    elif case == "lower":
        text = text.lower()
    return text or None


def _swing_fields(
    intent, *, source, side, quantity, order_type, tif, extended_hours
) -> dict:
    """Normalize and validate the swing-port fields of one ``OrderIntent``.

    Defaults pass through untouched, so an EB intent comes back exactly as the
    defaults and nothing about its identity or its stored row changes.
    """
    asset_class = _optional_text(intent.asset_class, case="lower") or "us_equity"
    if asset_class not in ASSET_CLASSES:
        raise ValueError(f"unsupported asset_class: {intent.asset_class!r}")
    order_class = _optional_text(intent.order_class, case="lower")
    if order_class not in (None, "bracket"):
        raise ValueError(f"unsupported order_class: {intent.order_class!r}")
    take_profit = _optional_decimal(intent.take_profit_price, "take_profit_price")
    stop_loss = _optional_decimal(intent.stop_loss_price, "stop_loss_price")
    for name, value in (
        ("take_profit_price", take_profit),
        ("stop_loss_price", stop_loss),
    ):
        if value is not None and value <= 0:
            raise ValueError(f"{name} must be > 0")
    if (
        (take_profit is not None or stop_loss is not None)
        and order_class != "bracket"
        and source is not OrderSource.BRACKET_LEG
    ):
        # L1 review: a leg price with no bracket flag would send the entry it
        # meant to protect as a plain order with no exit legs at all. Only a
        # bracket parent, or the record of one of its legs, carries them.
        raise ValueError(
            "take_profit_price/stop_loss_price need order_class='bracket'"
        )
    position_intent = _optional_text(intent.position_intent, case="lower")
    if position_intent is not None and position_intent not in POSITION_INTENTS:
        raise ValueError(f"unsupported position_intent: {intent.position_intent!r}")
    raw_multiplier = intent.contract_multiplier
    if isinstance(raw_multiplier, bool):
        raise TypeError("contract_multiplier must be an integer")
    try:
        multiplier = int(raw_multiplier)
    except (TypeError, ValueError) as exc:
        raise TypeError("contract_multiplier must be an integer") from exc
    if multiplier != raw_multiplier:
        raise ValueError("contract_multiplier must be an integer")
    underlying = _optional_text(intent.underlying, case="upper")
    option_type = _optional_text(intent.option_type, case="lower")
    strike = _optional_decimal(intent.strike, "strike")
    expiry = _optional_text(intent.expiry)
    parent = _optional_text(intent.parent_client_order_id)
    broker_key = _optional_text(intent.broker_client_order_id)
    whole = quantity == quantity.to_integral_value()

    if order_class == "bracket":
        if side is not OrderSide.BUY:
            raise ValueError("a bracket parent must be a BUY")
        if order_type != "market" or tif != "gtc" or extended_hours:
            raise ValueError("a bracket parent is a regular-hours market GTC order")
        if not whole:
            raise ValueError("a bracket parent needs whole shares")
        if take_profit is None or stop_loss is None:
            raise ValueError("a bracket needs take_profit_price and stop_loss_price")
        if stop_loss >= take_profit:
            raise ValueError("stop_loss_price must be below take_profit_price")
    if asset_class == "us_option":
        if position_intent is None:
            raise ValueError("an option order needs position_intent")
        if _POSITION_INTENT_SIDE[position_intent] != side.value:
            raise ValueError("position_intent contradicts side")
        if multiplier != 100:
            raise ValueError("an option contract_multiplier must be 100")
        if not whole:
            raise ValueError("options trade whole contracts")
        if extended_hours:
            raise ValueError("options do not trade extended hours")
        if order_class is not None:
            raise ValueError("an option order cannot be a bracket")
        if underlying is None:
            raise ValueError("an option order needs underlying")
        if option_type not in ("put", "call"):
            raise ValueError("option_type must be put or call")
        if strike is None or strike <= 0:
            raise ValueError("an option order needs strike > 0")
        try:
            # Stored normalised (L1 review): "20261002" and "2026-10-02" are
            # one contract, and must never be keyed as two.
            expiry = date.fromisoformat(expiry or "").isoformat()
        except ValueError as exc:
            raise ValueError("expiry must be YYYY-MM-DD") from exc
    else:
        if position_intent is not None:
            raise ValueError("position_intent is for options only")
        if multiplier != 1:
            raise ValueError("an equity contract_multiplier must be 1")
        if any(value is not None for value in (underlying, option_type, strike, expiry)):
            raise ValueError("underlying/option_type/strike/expiry are for options only")
    if broker_key is not None and source.value not in _BROKER_KEYED_SOURCES:
        raise ValueError(
            "broker_client_order_id is reserved for bracket legs and option activity"
        )
    if source is OrderSource.BRACKET_LEG:
        # L1 review: the broker mints a leg's client order id, so the leg's
        # key carries no instance prefix. What makes the row ours is its
        # parent, which must be an order this instance minted.
        if broker_key is None:
            raise ValueError("a bracket leg needs the broker's client order id")
        if side is not OrderSide.SELL or not bool(intent.reduce_only):
            raise ValueError("a bracket leg must be a reduce-only SELL")
        own = instance_key_prefix(intent.instance_id)
        if parent is None or not parent.startswith(own):
            raise ValueError(
                "a bracket leg needs a parent_client_order_id minted by this "
                f"instance ({own}...)"
            )
    return {
        "asset_class": asset_class,
        "order_class": order_class,
        "take_profit_price": take_profit,
        "stop_loss_price": stop_loss,
        "position_intent": position_intent,
        "contract_multiplier": multiplier,
        "underlying": underlying,
        "option_type": option_type,
        "strike": strike,
        "expiry": expiry,
        "parent_client_order_id": parent,
        "broker_client_order_id": broker_key,
    }


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """One immutable request to change live stock exposure."""

    account_id: str
    instance_id: str
    source: OrderSource
    reason: str
    symbol: str
    side: OrderSide
    quantity: Decimal
    reduce_only: bool
    decision_at: datetime
    quote_at: datetime
    risk_snapshot_id: str
    retry_ordinal: int = 0
    order_type: str = "market"
    limit_price: Optional[Decimal] = None
    tif: str = "day"
    extended_hours: bool = False
    reference_price: Optional[Decimal] = None
    # swing-port (interfaces doc sections 2 and 9). Each field joins the
    # identity only when it differs from its default, so stored EB rows keep
    # their keys; take_profit_price and stop_loss_price never join it.
    asset_class: str = "us_equity"
    order_class: Optional[str] = None
    take_profit_price: Optional[Decimal] = None
    stop_loss_price: Optional[Decimal] = None
    position_intent: Optional[str] = None
    contract_multiplier: int = 1
    underlying: Optional[str] = None
    option_type: Optional[str] = None
    strike: Optional[Decimal] = None
    expiry: Optional[str] = None
    parent_client_order_id: Optional[str] = None
    broker_client_order_id: Optional[str] = None
    identity_payload: str = field(init=False)
    idempotency_key: str = field(init=False)

    def __post_init__(self) -> None:
        account_id = str(self.account_id or "").strip()
        instance_id = str(self.instance_id or "").strip()
        reason = str(self.reason or "").strip()
        symbol = str(self.symbol or "").strip().upper()
        risk_snapshot_id = str(self.risk_snapshot_id or "").strip()
        if not account_id:
            raise ValueError("account_id is required")
        if not instance_id:
            raise ValueError("instance_id is required")
        if not reason:
            raise ValueError("reason is required")
        if not symbol:
            raise ValueError("symbol is required")
        if not risk_snapshot_id:
            raise ValueError("risk_snapshot_id is required")

        try:
            source = self.source if isinstance(self.source, OrderSource) else OrderSource(self.source)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported order source: {self.source!r}") from exc
        try:
            side = self.side if isinstance(self.side, OrderSide) else OrderSide(str(self.side).lower())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported order side: {self.side!r}") from exc
        quantity = _decimal(self.quantity, "quantity", positive=True)
        decision_at = _aware_utc(self.decision_at, "decision_at")
        quote_at = _aware_utc(self.quote_at, "quote_at")
        try:
            retry_ordinal = int(self.retry_ordinal)
        except (TypeError, ValueError) as exc:
            raise TypeError("retry_ordinal must be an integer") from exc
        if retry_ordinal < 0 or retry_ordinal != self.retry_ordinal:
            raise ValueError("retry_ordinal must be a non-negative integer")

        order_type = str(self.order_type or "").strip().lower()
        if order_type not in ("market", "limit"):
            raise ValueError("order_type must be market or limit")
        limit_price = _optional_decimal(self.limit_price, "limit_price")
        if order_type == "limit":
            if limit_price is None or limit_price <= 0:
                raise ValueError("limit orders require limit_price > 0")
        else:
            limit_price = None
        tif = str(self.tif or "").strip().lower()
        if tif not in ("day", "gtc", "ioc", "fok"):
            raise ValueError(f"unsupported tif: {tif!r}")
        extended_hours = bool(self.extended_hours)
        if extended_hours and (order_type != "limit" or tif != "day"):
            raise ValueError("extended_hours requires a limit DAY order")
        reference_price = _optional_decimal(self.reference_price, "reference_price")
        if reference_price is not None and reference_price <= 0:
            raise ValueError("reference_price must be > 0")
        swing = _swing_fields(
            self,
            source=source,
            side=side,
            quantity=quantity,
            order_type=order_type,
            tif=tif,
            extended_hours=extended_hours,
        )

        for name, value in (
            ("account_id", account_id),
            ("instance_id", instance_id),
            ("source", source),
            ("reason", reason),
            ("symbol", symbol),
            ("side", side),
            ("quantity", quantity),
            ("reduce_only", bool(self.reduce_only)),
            ("decision_at", decision_at),
            ("quote_at", quote_at),
            ("risk_snapshot_id", risk_snapshot_id),
            ("retry_ordinal", retry_ordinal),
            ("order_type", order_type),
            ("limit_price", limit_price),
            ("tif", tif),
            ("extended_hours", extended_hours),
            ("reference_price", reference_price),
            *swing.items(),
        ):
            object.__setattr__(self, name, value)

        # 2026-08-02 duplicate-buy regression. This hash used to cover
        # decision_at/quote_at at MICROSECOND precision plus quantity, prices
        # and risk_snapshot_id — every one of which changes when the same
        # logical decision is re-emitted (a restart replays the decision on a
        # different bar; risk_snapshot_id carries the risk-state version and
        # advances every tick). The client order id therefore changed too,
        # Alpaca's duplicate-cid check could not see the morning's order, and
        # a SECOND real buy landed. The legacy date-keyed cid in
        # broker_adapters/_client_order_id.py had exactly this property and it
        # was lost when order identity moved into OrderIntent.
        #
        # The two sides are deliberately asymmetric, because the two failures
        # are not symmetric:
        #
        # BUY  — a duplicate spends real cash that the strategy never intended
        #        to deploy, so identity collapses to (account, instance,
        #        source, symbol, side, trading day, retry_ordinal). Re-emitting
        #        a buy for the same name on the same session date is the SAME
        #        order however much the sizing drifted in between, and the
        #        second submit is short-circuited against broker truth instead
        #        of posted.
        # SELL — a suppressed exit traps capital, which is strictly worse than
        #        an extra exit (the gate caps every reduce-only sell at the
        #        held quantity and refuses non-reduce sells outright, so a
        #        duplicate can never go short). Sells therefore keep quantity
        #        and bucket the decision to the MINUTE: two identical exits
        #        inside one tick collapse, but the next minute always mints a
        #        fresh identity, so no exit can ever be permanently deduped
        #        against a dead order.
        identity = {
            "account_id": account_id,
            "instance_id": instance_id,
            "source": source.value,
            "symbol": symbol,
            "side": side.value,
            # KNOWN LIMITATION, 2026-08-03 adversarial sweep (HIGH, unfixed).
            #
            # Keying buys on the session date means: buy AAPL at 10:00, exit at
            # 11:00, and the 14:00 re-entry hashes to this SAME key. The morning
            # record is terminal FILLED with cumulative_quantity > 0, so
            # _live_identity returns idempotency.terminal_requires_retry and
            # never escalates (escalation is zero-fill-only). The re-entry is
            # denied for the rest of the session and never reaches the broker.
            # Scaling into a position hits the same wall.
            #
            # The obvious fix -- key on the decision BAR -- was tried and
            # REVERTED, because it breaks the protection this key exists for:
            # test_reemitted_buy_keeps_one_identity_across_drifted_sizing
            # documents that a restart replays one logical buy on a DIFFERENT
            # bar (+2h) with drifted sizing. So "different bar" describes both
            # the replay that must dedupe and the re-entry that must not; the
            # bar alone cannot separate them.
            #
            # What separates them is whether the POSITION WAS CLOSED in between.
            # The correct fix is to let escalation advance retry_ordinal when
            # the prior identity is terminal-FILLED *and* the exposure is gone,
            # which needs position truth inside _live_identity. Left unfixed
            # rather than trading a duplicate-buy risk (loses money) for a
            # blocked re-entry (loses opportunity).
            "session_date": _session_date(decision_at),
            "retry_ordinal": retry_ordinal,
        }
        # swing-port: an option SELL (sell_to_open) is keyed on the SESSION like
        # a buy, never on the minute: spec section 9 fix 1, a rerun must not
        # mint a second put of the same contract. Equity sells are unchanged.
        if side is not OrderSide.BUY and swing["asset_class"] == "us_equity":
            identity["decision_minute"] = decision_at.replace(
                second=0, microsecond=0
            ).isoformat(timespec="minutes")
            identity["quantity"] = _normalized_decimal(quantity)
            identity["reduce_only"] = bool(self.reduce_only)
        for name, default in _SWING_IDENTITY_DEFAULTS:
            value = swing[name]
            if value != default:
                identity[name] = (
                    _normalized_decimal(value)
                    if isinstance(value, Decimal)
                    else value
                )
        payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if swing["broker_client_order_id"] is not None:
            # A bracket leg's client order id is minted by Alpaca, and an
            # assignment has no order at all: the row must carry the id the
            # broker reports, or no event could ever resolve to it.
            key = swing["broker_client_order_id"]
        else:
            # Preserve the existing clean-room classifier contract: every
            # strategy-owned WAL row begins with this instance's 8-char prefix.
            instance_prefix = instance_key_prefix(instance_id)
            retry_suffix = f"-{retry_ordinal}"
            digest_room = 48 - len(instance_prefix) - len(retry_suffix)
            key = f"{instance_prefix}{digest[:digest_room]}{retry_suffix}"
        object.__setattr__(self, "identity_payload", payload)
        object.__setattr__(self, "idempotency_key", key)

    def same_identity(self, other: "OrderIntent") -> bool:
        """True when two intents name the same logical order.

        Only the fields that feed ``idempotency_key`` count. Sizing, prices and
        risk_snapshot_id drift between re-emissions of one decision; treating
        that drift as a different order is what let a duplicate buy through.
        """

        return (
            isinstance(other, OrderIntent)
            and other.identity_payload == self.identity_payload
        )


@dataclass(frozen=True, slots=True)
class DependencySnapshot:
    """One coherent, read-only dependency view evaluated by the gate."""

    account_id: str
    instance_id: str
    observed_at: datetime
    armed: bool
    kill_switch: Health
    quote: Health
    cash: Health
    positions: Health
    calendar: Health
    persistence: Health
    risk_state: Health
    watchdog: Health
    quote_symbol: str
    quote_price: Decimal
    quote_at: datetime
    position_symbol: str
    position_quantity: Decimal
    positions_at: datetime
    available_cash: Decimal
    market_open: bool
    risk_snapshot_id: str
    kill_switch_at: Optional[datetime] = None
    cash_at: Optional[datetime] = None
    calendar_at: Optional[datetime] = None
    persistence_at: Optional[datetime] = None
    risk_state_at: Optional[datetime] = None
    watchdog_at: Optional[datetime] = None
    max_order_notional: Optional[Decimal] = None
    max_position_quantity: Optional[Decimal] = None
    open_order_idempotency_keys: frozenset[str] = field(default_factory=frozenset)
    authorized_sources: frozenset[OrderSource] = field(
        default_factory=lambda: frozenset(OrderSource)
    )
    max_quote_age: timedelta = timedelta(seconds=30)
    max_positions_age: timedelta = timedelta(seconds=60)
    max_cash_age: timedelta = timedelta(seconds=60)
    max_calendar_age: timedelta = timedelta(seconds=60)
    max_control_age: timedelta = timedelta(seconds=60)
    max_clock_skew: timedelta = timedelta(seconds=5)
    max_reference_price_deviation: Decimal = Decimal("0.001")
    # swing-port (options branch of the gate). Every default is the equity
    # snapshot EB builds today; only an option snapshot sets these.
    asset_class: str = "us_equity"
    regular_session_open: Optional[bool] = None
    account_equity: Optional[Decimal] = None
    open_short_put_collateral: Optional[Decimal] = None
    # None = unknown, like open_short_put_collateral: a snapshot that omits
    # it must fail closed, never read as "no pending puts" (L3 review M3).
    pending_sell_to_open_collateral: Optional[Decimal] = None
    underlying_put_collateral: Optional[Decimal] = None
    max_underlying_collateral_fraction: Decimal = Decimal("0.25")

    def __post_init__(self) -> None:
        health_fields = (
            "kill_switch",
            "quote",
            "cash",
            "positions",
            "calendar",
            "persistence",
            "risk_state",
            "watchdog",
        )
        for name in health_fields:
            value = getattr(self, name)
            try:
                health = value if isinstance(value, Health) else Health(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be a Health value") from exc
            object.__setattr__(self, name, health)

        observed_at = _aware_utc(self.observed_at, "observed_at")
        quote_at = _aware_utc(self.quote_at, "quote_at")
        positions_at = _aware_utc(self.positions_at, "positions_at")
        dependency_times = {}
        for name in (
            "kill_switch_at",
            "cash_at",
            "calendar_at",
            "persistence_at",
            "risk_state_at",
            "watchdog_at",
        ):
            value = getattr(self, name)
            dependency_times[name] = (
                _aware_utc(value, name) if value is not None else None
            )
        quote_price = _decimal(self.quote_price, "quote_price")
        position_quantity = _decimal(self.position_quantity, "position_quantity")
        available_cash = _decimal(self.available_cash, "available_cash")
        max_order_notional = _optional_decimal(
            self.max_order_notional, "max_order_notional"
        )
        max_position_quantity = _optional_decimal(
            self.max_position_quantity, "max_position_quantity"
        )
        max_reference_price_deviation = _decimal(
            self.max_reference_price_deviation,
            "max_reference_price_deviation",
        )
        asset_class = _optional_text(self.asset_class, case="lower") or "us_equity"
        if asset_class not in ASSET_CLASSES:
            raise ValueError(f"unsupported asset_class: {self.asset_class!r}")
        # swing-port: a short option is a negative position. Nothing else may
        # be one; the equity book is long-only.
        if position_quantity < 0 and asset_class != "us_option":
            raise ValueError("position_quantity must be >= 0")
        account_equity = _optional_decimal(self.account_equity, "account_equity")
        open_short_put_collateral = _optional_decimal(
            self.open_short_put_collateral, "open_short_put_collateral"
        )
        pending_sell_to_open_collateral = _optional_decimal(
            self.pending_sell_to_open_collateral,
            "pending_sell_to_open_collateral",
        )
        underlying_put_collateral = _optional_decimal(
            self.underlying_put_collateral, "underlying_put_collateral"
        )
        max_underlying_collateral_fraction = _decimal(
            self.max_underlying_collateral_fraction,
            "max_underlying_collateral_fraction",
        )
        for name, value in (
            ("open_short_put_collateral", open_short_put_collateral),
            ("pending_sell_to_open_collateral", pending_sell_to_open_collateral),
            ("underlying_put_collateral", underlying_put_collateral),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be >= 0")
        if not Decimal("0") < max_underlying_collateral_fraction <= Decimal("1"):
            raise ValueError("max_underlying_collateral_fraction must be in (0, 1]")
        regular_session_open = (
            None
            if self.regular_session_open is None
            else bool(self.regular_session_open)
        )
        if available_cash < 0:
            raise ValueError("available_cash must be >= 0")
        if max_order_notional is not None and max_order_notional <= 0:
            raise ValueError("max_order_notional must be > 0")
        if max_position_quantity is not None and max_position_quantity < 0:
            raise ValueError("max_position_quantity must be >= 0")
        if max_reference_price_deviation < 0:
            raise ValueError("max_reference_price_deviation must be >= 0")
        if not isinstance(self.max_quote_age, timedelta) or self.max_quote_age.total_seconds() < 0:
            raise ValueError("max_quote_age must be a non-negative timedelta")
        if not isinstance(self.max_positions_age, timedelta) or self.max_positions_age.total_seconds() < 0:
            raise ValueError("max_positions_age must be a non-negative timedelta")
        for name in (
            "max_cash_age",
            "max_calendar_age",
            "max_control_age",
            "max_clock_skew",
        ):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or value.total_seconds() < 0:
                raise ValueError(f"{name} must be a non-negative timedelta")

        try:
            sources = frozenset(
                value if isinstance(value, OrderSource) else OrderSource(value)
                for value in self.authorized_sources
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("authorized_sources contains an unsupported source") from exc

        for name, value in (
            ("account_id", str(self.account_id or "").strip()),
            ("instance_id", str(self.instance_id or "").strip()),
            ("observed_at", observed_at),
            ("armed", bool(self.armed)),
            ("quote_symbol", str(self.quote_symbol or "").strip().upper()),
            ("quote_price", quote_price),
            ("quote_at", quote_at),
            ("position_symbol", str(self.position_symbol or "").strip().upper()),
            ("position_quantity", position_quantity),
            ("positions_at", positions_at),
            *dependency_times.items(),
            ("available_cash", available_cash),
            ("market_open", bool(self.market_open)),
            ("risk_snapshot_id", str(self.risk_snapshot_id or "").strip()),
            ("max_order_notional", max_order_notional),
            ("max_position_quantity", max_position_quantity),
            (
                "max_reference_price_deviation",
                max_reference_price_deviation,
            ),
            (
                "open_order_idempotency_keys",
                frozenset(str(value) for value in self.open_order_idempotency_keys),
            ),
            ("authorized_sources", sources),
            ("asset_class", asset_class),
            ("regular_session_open", regular_session_open),
            ("account_equity", account_equity),
            ("open_short_put_collateral", open_short_put_collateral),
            ("pending_sell_to_open_collateral", pending_sell_to_open_collateral),
            ("underlying_put_collateral", underlying_put_collateral),
            (
                "max_underlying_collateral_fraction",
                max_underlying_collateral_fraction,
            ),
        ):
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Pure gate output. A denied decision never approves transport quantity."""

    allowed: bool
    approved_quantity: Decimal
    reason_codes: tuple[str, ...]
    idempotency_key: str

    def __post_init__(self) -> None:
        approved_quantity = _decimal(self.approved_quantity, "approved_quantity")
        if approved_quantity < 0:
            raise ValueError("approved_quantity must be >= 0")
        object.__setattr__(self, "allowed", bool(self.allowed))
        object.__setattr__(self, "approved_quantity", approved_quantity)
        object.__setattr__(
            self, "reason_codes", tuple(str(code) for code in self.reason_codes)
        )
        object.__setattr__(self, "idempotency_key", str(self.idempotency_key))

    @property
    def reasons(self) -> tuple[str, ...]:
        """Compatibility/readability alias for callers displaying denials."""

        return self.reason_codes


@dataclass(frozen=True, slots=True)
class BrokerOrderEvent:
    """Immutable normalized broker event.

    Broker feeds supply cumulative quantities and average prices. The order
    service fills the incremental fields before persistence/accounting.
    """

    event_id: str
    account_id: str
    instance_id: str
    client_order_id: str
    broker_order_id: Optional[str]
    symbol: str
    side: OrderSide
    state: LifecycleState
    cumulative_quantity: Decimal
    cumulative_average_price: Optional[Decimal]
    cumulative_fees: Decimal
    occurred_at: datetime
    sequence: int = -1
    incremental_quantity: Decimal = Decimal("0")
    incremental_price: Optional[Decimal] = None
    incremental_fees: Decimal = Decimal("0")
    reason: str = ""

    def __post_init__(self) -> None:
        strings = {
            "event_id": self.event_id,
            "account_id": self.account_id,
            "instance_id": self.instance_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
        }
        for name, raw in strings.items():
            value = str(raw or "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            if name == "symbol":
                value = value.upper()
            object.__setattr__(self, name, value)
        broker_order_id = str(self.broker_order_id or "").strip() or None
        object.__setattr__(self, "broker_order_id", broker_order_id)
        try:
            side = self.side if isinstance(self.side, OrderSide) else OrderSide(
                str(self.side).lower()
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported order side: {self.side!r}") from exc
        try:
            state = (
                self.state
                if isinstance(self.state, LifecycleState)
                else LifecycleState(str(self.state).lower())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported lifecycle state: {self.state!r}") from exc
        cumulative = _decimal(self.cumulative_quantity, "cumulative_quantity")
        incremental = _decimal(
            self.incremental_quantity, "incremental_quantity"
        )
        cumulative_fees = _decimal(self.cumulative_fees, "cumulative_fees")
        incremental_fees = _decimal(self.incremental_fees, "incremental_fees")
        average = _optional_decimal(
            self.cumulative_average_price, "cumulative_average_price"
        )
        incremental_price = _optional_decimal(
            self.incremental_price, "incremental_price"
        )
        if min(cumulative, incremental, cumulative_fees, incremental_fees) < 0:
            raise ValueError("event quantities and fees must be non-negative")
        if average is not None and average <= 0:
            raise ValueError("cumulative_average_price must be > 0")
        if incremental_price is not None and incremental_price <= 0:
            raise ValueError("incremental_price must be > 0")
        if state in (LifecycleState.PARTIAL, LifecycleState.FILLED):
            if cumulative <= 0 or average is None:
                raise ValueError("fill events require cumulative quantity and price")
        try:
            sequence = int(self.sequence)
        except (TypeError, ValueError) as exc:
            raise TypeError("sequence must be an integer") from exc
        if sequence < -1 or sequence != self.sequence:
            raise ValueError("sequence must be -1 or a non-negative integer")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "cumulative_quantity", cumulative)
        object.__setattr__(self, "cumulative_average_price", average)
        object.__setattr__(self, "cumulative_fees", cumulative_fees)
        object.__setattr__(self, "incremental_quantity", incremental)
        object.__setattr__(self, "incremental_price", incremental_price)
        object.__setattr__(self, "incremental_fees", incremental_fees)
        object.__setattr__(self, "occurred_at", _aware_utc(self.occurred_at, "occurred_at"))
        object.__setattr__(self, "sequence", sequence)
        object.__setattr__(self, "reason", str(self.reason or "").strip())

    def with_identity(self, **changes) -> "BrokerOrderEvent":
        """Return an immutable variant; useful to test identity drift."""

        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class ConfirmedFill:
    """Exactly-once incremental accounting instruction."""

    event: BrokerOrderEvent
    incremental_quantity: Decimal
    incremental_price: Decimal
    incremental_fees: Decimal
    position_delta: Decimal
    cash_delta: Decimal
    # swing-port: a contract fill moves `quantity x price x multiplier` cash.
    asset_class: str = "us_equity"
    contract_multiplier: int = 1
    # swing-port fix wave (FW1 follow-up): the contract of a fill on an
    # option order WE placed, from its intent, so the adapter's option map
    # can type a brand-new position row at once. None on every equity fill
    # (EB's included) and on a fill of unknown origin.
    underlying: Optional[str] = None
    option_type: Optional[str] = None
    strike: Optional[Decimal] = None
    expiry: Optional[str] = None

    def __post_init__(self) -> None:
        quantity = _decimal(
            self.incremental_quantity, "incremental_quantity", positive=True
        )
        price = _decimal(self.incremental_price, "incremental_price", positive=True)
        fees = _decimal(self.incremental_fees, "incremental_fees")
        position_delta = _decimal(self.position_delta, "position_delta")
        cash_delta = _decimal(self.cash_delta, "cash_delta")
        if fees < 0:
            raise ValueError("incremental_fees must be non-negative")
        object.__setattr__(self, "incremental_quantity", quantity)
        object.__setattr__(self, "incremental_price", price)
        object.__setattr__(self, "incremental_fees", fees)
        object.__setattr__(self, "position_delta", position_delta)
        object.__setattr__(self, "cash_delta", cash_delta)
        asset_class = _optional_text(self.asset_class, case="lower") or "us_equity"
        if asset_class not in ASSET_CLASSES:
            raise ValueError(f"unsupported asset_class: {self.asset_class!r}")
        if isinstance(self.contract_multiplier, bool):
            raise TypeError("contract_multiplier must be an integer")
        multiplier = int(self.contract_multiplier)
        if multiplier != self.contract_multiplier or multiplier < 1:
            raise ValueError("contract_multiplier must be an integer >= 1")
        object.__setattr__(self, "asset_class", asset_class)
        object.__setattr__(self, "contract_multiplier", multiplier)
        object.__setattr__(
            self, "underlying", _optional_text(self.underlying, case="upper"))
        object.__setattr__(
            self, "option_type", _optional_text(self.option_type, case="lower"))
        object.__setattr__(self, "strike", _optional_decimal(self.strike, "strike"))
        object.__setattr__(self, "expiry", _optional_text(self.expiry))
