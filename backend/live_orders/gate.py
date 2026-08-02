"""Pure, fail-closed admission logic for every live Alpaca stock order."""

from __future__ import annotations

from decimal import Decimal

from .types import (
    DependencySnapshot,
    GateDecision,
    Health,
    OrderIntent,
    OrderSide,
)


class UnifiedOrderGate:
    """Evaluate an immutable intent against one immutable dependency view.

    The method deliberately has no injected collaborators and performs no
    I/O. It returns every applicable reason in stable evaluation order.
    """

    _DEPENDENCIES = (
        "kill_switch",
        "quote",
        "cash",
        "positions",
        "calendar",
        "persistence",
        "risk_state",
        "watchdog",
    )

    def evaluate(
        self, intent: OrderIntent, snapshot: DependencySnapshot
    ) -> GateDecision:
        blockers: list[str] = []
        notices: list[str] = []
        approved = intent.quantity

        # 1. Identity and arming.
        if intent.account_id != snapshot.account_id:
            blockers.append("identity.account_mismatch")
        if intent.instance_id != snapshot.instance_id:
            blockers.append("identity.instance_mismatch")
        if not snapshot.armed:
            blockers.append("identity.not_armed")

        # 2. Dependency health. New exposure requires complete knowledge.
        # Emergency reductions only depend on authoritative quote/position
        # truth; stale cash/risk inputs must not prevent reducing exposure.
        required = (
            self._DEPENDENCIES
            if not intent.reduce_only
            else ("quote", "positions", "persistence")
        )
        for name in required:
            health = getattr(snapshot, name)
            if health is Health.UNKNOWN:
                blockers.append(f"dependency.{name}.unknown")
            elif health is Health.UNHEALTHY:
                blockers.append(f"dependency.{name}.unhealthy")

        # A green status without recent evidence is not healthy. Control
        # timestamps are evaluated against the same immutable observation.
        freshness = {
            "kill_switch": (
                snapshot.kill_switch_at,
                snapshot.max_control_age,
            ),
            "cash": (snapshot.cash_at, snapshot.max_cash_age),
            "calendar": (snapshot.calendar_at, snapshot.max_calendar_age),
            "persistence": (
                snapshot.persistence_at,
                snapshot.max_control_age,
            ),
            "risk_state": (
                snapshot.risk_state_at,
                snapshot.max_control_age,
            ),
            "watchdog": (
                snapshot.watchdog_at,
                snapshot.max_control_age,
            ),
        }
        for name in required:
            if name in ("quote", "positions"):
                continue
            timestamp, max_age = freshness[name]
            if timestamp is None:
                blockers.append(f"dependency.{name}.stale")
            elif timestamp - snapshot.observed_at > snapshot.max_clock_skew:
                blockers.append(f"dependency.{name}.clock_skew")
            elif snapshot.observed_at - timestamp > max_age:
                blockers.append(f"dependency.{name}.stale")

        # 3. Reduce-only containment.
        if intent.reduce_only:
            if intent.side is not OrderSide.SELL:
                blockers.append("reduce_only.sell_required")
            if snapshot.position_quantity <= 0:
                blockers.append("reduce_only.no_position")
            else:
                capped = min(intent.quantity, snapshot.position_quantity)
                if capped < intent.quantity:
                    notices.append("reduce_only.quantity_capped")
                approved = capped
        elif intent.side is OrderSide.SELL:
            # IntelliStock's stock book is long-only. A non-reduce sell could
            # create short exposure and must use an explicit future authority.
            blockers.append("positions.sell_must_be_reduce_only")

        # 4. Market and quote identity/freshness.
        if snapshot.quote_symbol != intent.symbol:
            blockers.append("quote.symbol_mismatch")
        if snapshot.quote_at != intent.quote_at:
            blockers.append("quote.timestamp_mismatch")
        if snapshot.quote_at - snapshot.observed_at > snapshot.max_clock_skew:
            blockers.append("quote.clock_skew")
        elif snapshot.observed_at - snapshot.quote_at > snapshot.max_quote_age:
            blockers.append("quote.stale")
        if snapshot.quote_price <= 0:
            blockers.append("quote.invalid_price")
        if (
            intent.reference_price is not None
            and snapshot.quote_price > 0
            and (
                abs(intent.reference_price - snapshot.quote_price)
                / snapshot.quote_price
            )
            > snapshot.max_reference_price_deviation
        ):
            blockers.append("quote.reference_price_mismatch")
        if not snapshot.market_open:
            # No reduce-only exemption here, deliberately. `market_open` is the
            # FULL 04:00-20:00 ET window (regular plus extended), so a closed
            # market means there is no venue at all, not merely no regular
            # session. Alpaca rejects orders that are not extended-hours
            # eligible when they are submitted between 16:00 and 19:00 ET and
            # queues anything later for the next open, so an "emergency exit"
            # let through here does not exit anything: it becomes a resting
            # order that fills at the next open at a price nobody evaluated,
            # while the strategy independently re-emits the same exit on that
            # open. Two exits, one of them unbounded.
            #
            # What must never happen is the exit getting STUCK, and that is
            # handled where it belongs: order identity re-mints itself every
            # minute for sells, terminal-without-fill records escalate to a
            # fresh retry, and abandoned rows retire instead of pinning the
            # whole cycle unhealthy. The exit is deferred to the next session,
            # not lost.
            blockers.append("market.closed")

        # Position identity/freshness is needed both to cap reductions and to
        # calculate the projected exposure of an add.
        if snapshot.position_symbol != intent.symbol:
            blockers.append("positions.symbol_mismatch")
        if snapshot.positions_at - snapshot.observed_at > snapshot.max_clock_skew:
            blockers.append("positions.clock_skew")
        elif (
            snapshot.observed_at - snapshot.positions_at
            > snapshot.max_positions_age
        ):
            blockers.append("positions.stale")

        # Risk snapshot identity prevents mixing a decision with another tick.
        if snapshot.risk_snapshot_id != intent.risk_snapshot_id:
            blockers.append("risk.snapshot_mismatch")

        # 5. Cash and positions.
        notional = approved * snapshot.quote_price
        if intent.side is OrderSide.BUY and notional > snapshot.available_cash:
            blockers.append("cash.insufficient")

        # 6. Exposure limits.
        if (
            intent.side is OrderSide.BUY
            and snapshot.max_order_notional is not None
            and notional > snapshot.max_order_notional
        ):
            blockers.append("exposure.max_order_notional")
        if intent.side is OrderSide.BUY and snapshot.max_position_quantity is not None:
            current = (
                snapshot.position_quantity
                if snapshot.position_symbol == intent.symbol
                else Decimal("0")
            )
            if current + approved > snapshot.max_position_quantity:
                blockers.append("exposure.max_position_quantity")

        # 7. Open orders and idempotency.
        if intent.idempotency_key in snapshot.open_order_idempotency_keys:
            blockers.append("idempotency.open_order_exists")

        # 8. Source authorization.
        if intent.source not in snapshot.authorized_sources:
            blockers.append("authorization.source_denied")

        allowed = not blockers
        return GateDecision(
            allowed=allowed,
            approved_quantity=approved if allowed else Decimal("0"),
            reason_codes=tuple(blockers + notices),
            idempotency_key=intent.idempotency_key,
        )
