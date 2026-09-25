"""swing-port Task 6: the options branch of UnifiedOrderGate. It is entered
only for asset_class == "us_option"; EB's equity branch is untouched.

EB pins computed from the PRE-change gate on 2026-09-24. Never edit them."""
import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from live_orders import Health, OrderSide, OrderSource, UnifiedOrderGate
from live_order_task8_helpers import NOW
from live_order_task8_helpers import intent as equity_intent
from live_order_task8_helpers import snapshot as equity_helper_snapshot
from swing_live_fixtures import (
    RTH,
    buy_to_close,
    equity_snapshot,
    option_intent,
    option_snapshot,
)


def evaluate(order, snap):
    return UnifiedOrderGate().evaluate(order, snap)


# --- EB invariance ------------------------------------------------------------

_DEPENDENCY_NAMES = ("kill_switch", "quote", "cash", "positions", "calendar",
                     "persistence", "risk_state", "watchdog")
_STAMP_NAMES = ("kill_switch_at", "cash_at", "calendar_at", "persistence_at",
                "risk_state_at", "watchdog_at")


def _eb_intent_shapes():
    """EB-shaped equity intents: every source, both sides, reduce-only exits,
    limit and extended-hours sells, reference-price drift, sub-share sizes."""
    shapes = []
    sides = (
        (OrderSide.BUY, False, OrderSource.STRATEGY),
        (OrderSide.BUY, False, OrderSource.MANUAL),
        (OrderSide.BUY, False, OrderSource.RESIDUAL_SLEEVE),
        (OrderSide.BUY, True, OrderSource.RISK_EXIT),
        (OrderSide.SELL, True, OrderSource.RISK_EXIT),
        (OrderSide.SELL, True, OrderSource.STRATEGY),
        (OrderSide.SELL, False, OrderSource.STRATEGY),
    )
    kinds = (
        {},
        {"order_type": "limit", "limit_price": Decimal("99.5")},
        {"order_type": "limit", "limit_price": Decimal("99.5"),
         "extended_hours": True},
    )
    for side, reduce_only, source in sides:
        for quantity in ("0.5", "1", "5", "12"):
            for kind in kinds:
                for reference in (None, Decimal("100"), Decimal("100.2")):
                    shapes.append(dict(
                        side=side, reduce_only=reduce_only, source=source,
                        quantity=Decimal(quantity), reference_price=reference,
                        **kind))
    return shapes


def _eb_snapshot_changes(order):
    later = NOW + timedelta(seconds=31)
    changes = [
        {},
        {"observed_at": later, "positions_at": later,
         **{name: later for name in _STAMP_NAMES}},
        {"quote_at": NOW + timedelta(seconds=6)},
        {"quote_symbol": "MSFT"},
        {"quote_price": Decimal("0")},
        {"quote_price": Decimal("100.05")},
        {"position_quantity": Decimal("0")},
        {"position_quantity": Decimal("3")},
        {"position_symbol": "MSFT"},
        {"positions_at": NOW - timedelta(seconds=61)},
        {"positions_at": NOW + timedelta(seconds=6)},
        {"available_cash": Decimal("100")},
        {"max_order_notional": Decimal("100")},
        {"max_order_notional": None},
        {"max_position_quantity": Decimal("5")},
        {"max_position_quantity": None},
        {"market_open": False},
        {"armed": False},
        {"account_id": "acct-2"},
        {"instance_id": "instance-2"},
        {"risk_snapshot_id": "risk-2"},
        {"open_order_idempotency_keys": frozenset({order.idempotency_key})},
        {"authorized_sources": frozenset({OrderSource.RISK_EXIT})},
        {"max_reference_price_deviation": Decimal("0.01")},
        {"max_quote_age": timedelta(seconds=60),
         "observed_at": later, "positions_at": later,
         **{name: later for name in _STAMP_NAMES}},
        # The option-only snapshot fields at non-default values change
        # nothing for an equity intent.
        {"regular_session_open": False, "account_equity": Decimal("1"),
         "open_short_put_collateral": None,
         "underlying_put_collateral": Decimal("999999")},
    ]
    for name in _DEPENDENCY_NAMES:
        changes.append({name: Health.UNKNOWN})
        changes.append({name: Health.UNHEALTHY})
    for name in _STAMP_NAMES:
        changes.append({name: None})
        changes.append({name: NOW - timedelta(seconds=61)})
        changes.append({name: NOW + timedelta(seconds=6)})
    return changes


def _eb_gate_grid():
    rows = []
    for shape in _eb_intent_shapes():
        try:
            order = equity_intent(**shape)
        except (TypeError, ValueError) as exc:
            rows.append(["invalid", type(exc).__name__, str(exc)])
            continue
        for changes in _eb_snapshot_changes(order):
            decision = evaluate(order, equity_helper_snapshot(order, **changes))
            rows.append([decision.allowed, str(decision.approved_quantity),
                         list(decision.reason_codes),
                         decision.idempotency_key])
    return rows


EB_GATE_GRID_ROWS = 15120
EB_GATE_GRID_SHA256 = (
    "c759357deb82d3f343a21014ca7629bf02449899780e048b7bb3b0fd8a936e69")


def test_eb_gate_decisions_are_byte_identical():
    rows = _eb_gate_grid()
    digest = hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert (len(rows), digest) == (EB_GATE_GRID_ROWS, EB_GATE_GRID_SHA256)


def test_eb_gate_spot_decisions_are_pinned():
    """Readable anchors inside the grid: a buy, a capped reduce-only exit,
    a stale-quote buy and a stale-quote exit."""
    buy = equity_intent(quantity=Decimal("5"))
    assert evaluate(buy, equity_helper_snapshot(buy)).reason_codes == ()
    exit_ = equity_intent(side=OrderSide.SELL, reduce_only=True,
                          source=OrderSource.RISK_EXIT, quantity=Decimal("12"))
    capped = evaluate(exit_, equity_helper_snapshot(exit_))
    assert (capped.allowed, capped.approved_quantity, capped.reason_codes) == (
        True, Decimal("10"), ("reduce_only.quantity_capped",))
    later = NOW + timedelta(seconds=31)
    stale = {"observed_at": later, "positions_at": later,
             **{name: later for name in _STAMP_NAMES}}
    assert evaluate(buy, equity_helper_snapshot(buy, **stale)).reason_codes == (
        "quote.stale",)
    assert evaluate(exit_, equity_helper_snapshot(exit_, **stale)).reason_codes == (
        "quote.stale", "reduce_only.quantity_capped")


def test_an_equity_intent_never_enters_the_option_branch(monkeypatch):
    def refuse(self, intent, snapshot):
        raise AssertionError("an equity intent entered the options branch")

    monkeypatch.setattr(UnifiedOrderGate, "_evaluate_option", refuse)
    order = equity_intent(side=OrderSide.SELL, reduce_only=False,
                          quantity=Decimal("1"))
    decision = evaluate(order, equity_helper_snapshot(order))
    assert decision.allowed is False
    assert "positions.sell_must_be_reduce_only" in decision.reason_codes


def test_the_eb_grid_never_enters_the_option_branch(monkeypatch):
    def refuse(self, intent, snapshot):
        raise AssertionError("an equity intent entered the options branch")

    monkeypatch.setattr(UnifiedOrderGate, "_evaluate_option", refuse)
    assert len(_eb_gate_grid()) == EB_GATE_GRID_ROWS


# --- the options branch (brief) -----------------------------------------------

def test_a_cash_secured_put_within_cash_and_cap_is_allowed():
    order = option_intent()
    decision = evaluate(order, option_snapshot(order))
    assert decision.allowed is True, decision.reason_codes
    assert decision.approved_quantity == Decimal("1")


def test_cash_exactly_equal_to_collateral_is_allowed_one_cent_short_is_not():
    """Review Focus 5. 130 x 100 x 1 = 13,000 against 20,000 - 4,000 - 3,000."""
    order = option_intent()
    exact = option_snapshot(
        order, available_cash=Decimal("20000"),
        open_short_put_collateral=Decimal("4000"),
        pending_sell_to_open_collateral=Decimal("3000"))
    assert evaluate(order, exact).allowed is True
    short = option_snapshot(
        order, available_cash=Decimal("19999.99"),
        open_short_put_collateral=Decimal("4000"),
        pending_sell_to_open_collateral=Decimal("3000"))
    decision = evaluate(order, short)
    assert decision.allowed is False
    assert "option.collateral_insufficient" in decision.reason_codes


def test_the_underlying_cap_counts_existing_puts():
    """Spec section 9 fix 3: 25% of equity per underlying, puts included."""
    order = option_intent()
    assert evaluate(order, option_snapshot(
        order, account_equity=Decimal("52000"))).allowed is True
    over = evaluate(order, option_snapshot(
        order, account_equity=Decimal("52000"),
        underlying_put_collateral=Decimal("0.01")))
    assert "option.underlying_cap" in over.reason_codes
    small = evaluate(order, option_snapshot(
        order, account_equity=Decimal("50000")))
    assert "option.underlying_cap" in small.reason_codes


@pytest.mark.parametrize("changes,code", [
    ({"open_short_put_collateral": None}, "option.collateral_unknown"),
    ({"account_equity": None}, "option.underlying_cap_unknown"),
    ({"underlying_put_collateral": None}, "option.underlying_cap_unknown"),
])
def test_unknown_collateral_inputs_refuse_a_new_put(changes, code):
    order = option_intent()
    decision = evaluate(order, option_snapshot(order, **changes))
    assert decision.allowed is False and code in decision.reason_codes


def test_options_trade_regular_hours_only_even_inside_the_extended_window():
    order = option_intent()
    extended = evaluate(order, option_snapshot(
        order, market_open=True, regular_session_open=False))
    assert "market.regular_hours_required" in extended.reason_codes
    unknown = evaluate(order, option_snapshot(order, regular_session_open=None))
    assert "market.regular_hours_required" in unknown.reason_codes


def test_buy_to_close_needs_a_short_at_least_as_large():
    order = buy_to_close()
    assert evaluate(order, option_snapshot(
        order, position_quantity=Decimal("-1"))).allowed is True
    flat = evaluate(order, option_snapshot(order, position_quantity=Decimal("0")))
    assert "option.short_position_insufficient" in flat.reason_codes
    two = buy_to_close(quantity=Decimal("2"))
    assert "option.short_position_insufficient" in evaluate(
        two, option_snapshot(two, position_quantity=Decimal("-1"))).reason_codes


def test_a_kill_level_blocks_a_new_put_but_never_a_buy_to_close():
    closing = buy_to_close()
    snap = option_snapshot(closing, position_quantity=Decimal("-1"),
                           risk_state=Health.UNHEALTHY, cash=Health.UNKNOWN)
    assert evaluate(closing, snap).allowed is True
    opening = option_intent()
    decision = evaluate(opening, option_snapshot(
        opening, risk_state=Health.UNHEALTHY))
    assert "dependency.risk_state.unhealthy" in decision.reason_codes


def test_notional_is_quantity_times_price_times_one_hundred():
    order = buy_to_close()
    poor = option_snapshot(order, position_quantity=Decimal("-1"),
                           quote_price=Decimal("2.50"),
                           available_cash=Decimal("249.99"))
    assert "cash.insufficient" in evaluate(order, poor).reason_codes
    enough = option_snapshot(order, position_quantity=Decimal("-1"),
                             quote_price=Decimal("2.50"),
                             available_cash=Decimal("250"))
    assert evaluate(order, enough).allowed is True
    opening = option_intent()
    capped = option_snapshot(opening, max_order_notional=Decimal("119.99"))
    assert "exposure.max_order_notional" in evaluate(opening, capped).reason_codes
    closing_cap = option_snapshot(order, position_quantity=Decimal("-1"),
                                  quote_price=Decimal("2.50"),
                                  max_order_notional=Decimal("1"))
    assert evaluate(order, closing_cap).allowed is True


def test_a_second_put_on_a_contract_already_short_is_refused():
    """Spec section 9 fix 1: open puts are checked."""
    order = option_intent()
    decision = evaluate(order, option_snapshot(
        order, position_quantity=Decimal("-1")))
    assert "option.already_short_contract" in decision.reason_codes


def test_a_sell_to_open_call_is_refused():
    order = option_intent(symbol="APH261009C00130000", option_type="call")
    assert "option.sell_to_open_requires_put" in evaluate(
        order, option_snapshot(order)).reason_codes


def test_an_equity_snapshot_cannot_approve_an_option():
    order = option_intent()
    decision = evaluate(order, option_snapshot(order, asset_class="us_equity"))
    assert "option.snapshot_asset_class_mismatch" in decision.reason_codes


def test_identity_quote_and_position_checks_still_apply():
    order = option_intent()
    decision = evaluate(order, option_snapshot(
        order, quote_symbol="APH", positions_at=RTH - timedelta(minutes=5),
        armed=False))
    assert {"identity.not_armed", "quote.symbol_mismatch",
            "positions.stale"} <= set(decision.reason_codes)


# --- ruling F5: the copied checks answer exactly as the originals -------------

def _random_dependencies(rng, at):
    """A random health and freshness state for all eight dependencies."""
    changes = {}
    for name in _DEPENDENCY_NAMES:
        changes[name] = rng.choice(
            (Health.HEALTHY, Health.HEALTHY, Health.UNKNOWN, Health.UNHEALTHY))
    for name in _STAMP_NAMES:
        changes[name] = rng.choice((
            at, at, None, at - timedelta(seconds=61),
            at - timedelta(seconds=60), at + timedelta(seconds=6),
            at + timedelta(seconds=5)))
    return changes


@pytest.mark.parametrize("reduce_only", [False, True])
def test_the_copied_dependency_checks_match_the_equity_gate(reduce_only):
    """Ruling F5. _dependency_blockers is a verbatim copy of evaluate's step 2.
    Over 3,000 random health/freshness states it must return exactly the
    dependency codes the untouched equity branch returns."""
    gate = UnifiedOrderGate()
    rng = random.Random(20260924 + int(reduce_only))
    order = equity_intent(
        side=OrderSide.SELL if reduce_only else OrderSide.BUY,
        reduce_only=reduce_only,
        source=OrderSource.RISK_EXIT if reduce_only else OrderSource.STRATEGY)
    required = (("quote", "positions", "persistence") if reduce_only
                else UnifiedOrderGate._DEPENDENCIES)
    seen = set()
    for _ in range(3000):
        snap = equity_helper_snapshot(order, **_random_dependencies(rng, NOW))
        original = [code for code in gate.evaluate(order, snap).reason_codes
                    if code.startswith("dependency.")]
        assert gate._dependency_blockers(required, snap) == original
        seen.update(original)
    # The sample reached every kind of answer the check can give.
    assert {code.rsplit(".", 1)[1] for code in seen} == {
        "unknown", "unhealthy", "stale", "clock_skew"}


_SHARED_FAMILIES = ("identity.", "dependency.", "quote.symbol_mismatch",
                    "quote.timestamp_mismatch", "quote.clock_skew",
                    "quote.stale", "quote.invalid_price",
                    "positions.symbol_mismatch", "positions.clock_skew",
                    "positions.stale", "risk.", "idempotency.",
                    "authorization.")


def _shared(codes):
    return [code for code in codes if code.startswith(_SHARED_FAMILIES)]


def _shared_changes(rng, option_order, equity_order):
    """One random set of snapshot inputs applied identically to both
    branches: identity, dependencies, quote and position freshness, risk
    snapshot, open orders and source authorization."""
    later = RTH + timedelta(seconds=rng.choice((0, 0, 30, 60, 61)))
    changes = {"observed_at": later}
    changes.update(_random_dependencies(rng, later))
    if rng.random() < 0.2:
        changes["armed"] = False
    if rng.random() < 0.1:
        changes["account_id"] = "acct-2"
    if rng.random() < 0.1:
        changes["instance_id"] = "instance-2"
    if rng.random() < 0.15:
        changes["quote_symbol"] = "OTHER"
    if rng.random() < 0.15:
        changes["quote_at"] = RTH + timedelta(seconds=rng.choice((-1, 6, 5)))
    if rng.random() < 0.1:
        changes["quote_price"] = Decimal("0")
    if rng.random() < 0.15:
        changes["position_symbol"] = "OTHER"
    changes["positions_at"] = later + timedelta(
        seconds=rng.choice((0, 0, -61, -60, 6, 5)))
    if rng.random() < 0.1:
        changes["risk_snapshot_id"] = "risk-other"
    open_key = rng.random() < 0.15
    denied = rng.random() < 0.15
    option = dict(changes)
    equity = dict(changes)
    if open_key:
        option["open_order_idempotency_keys"] = frozenset(
            {option_order.idempotency_key})
        equity["open_order_idempotency_keys"] = frozenset(
            {equity_order.idempotency_key})
    if denied:
        option["authorized_sources"] = frozenset({OrderSource.MANUAL})
        equity["authorized_sources"] = frozenset({OrderSource.MANUAL})
    for values, order in ((option, option_order), (equity, equity_order)):
        values.setdefault("quote_symbol", order.symbol)
        values.setdefault("position_symbol", order.symbol)
    return option, equity


@pytest.mark.parametrize("closing", [False, True])
def test_the_copied_quote_position_identity_checks_match_the_equity_gate(
        closing):
    """Ruling F5. The options branch's copies of the identity, dependency,
    quote, position, risk, idempotency and authorization checks return the
    same codes, in the same order, as the equity branch on the same inputs."""
    rng = random.Random(924 + int(closing))
    if closing:
        option_order = buy_to_close()
        equity_order = equity_intent(
            side=OrderSide.SELL, reduce_only=True,
            source=OrderSource.RISK_EXIT, decision_at=RTH, quote_at=RTH)
    else:
        option_order = option_intent()
        equity_order = equity_intent(decision_at=RTH, quote_at=RTH)
    age = timedelta(seconds=60)
    compared = set()
    for _ in range(3000):
        option_changes, equity_changes = _shared_changes(
            rng, option_order, equity_order)
        option_codes = _shared(evaluate(option_order, option_snapshot(
            option_order, max_quote_age=age,
            position_quantity=Decimal("-1") if closing else Decimal("0"),
            **option_changes)).reason_codes)
        equity_codes = _shared(evaluate(equity_order, equity_snapshot(
            equity_order, max_quote_age=age, position_quantity=Decimal("5"),
            **equity_changes)).reason_codes)
        assert option_codes == equity_codes
        compared.update(code.split(".")[0] for code in option_codes)
    assert compared == {"identity", "dependency", "quote", "positions", "risk",
                        "idempotency", "authorization"}


# --- ruling 6: the pre-flight's option order rules ----------------------------

def _at(order_changes, when, **snapshot_changes):
    order = option_intent(decision_at=when, quote_at=when, **order_changes)
    stamps = {name: when for name in _STAMP_NAMES}
    return order, option_snapshot(order, observed_at=when, positions_at=when,
                                  **stamps, **snapshot_changes)


@pytest.mark.parametrize("utc,open_", [
    (datetime(2026, 10, 5, 13, 29, tzinfo=timezone.utc), False),
    (datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc), True),
    (datetime(2026, 10, 5, 19, 59, tzinfo=timezone.utc), True),
    (datetime(2026, 10, 5, 20, 0, tzinfo=timezone.utc), False),
    # 2026-11-27, the day after Thanksgiving: NYSE closes at 13:00 ET.
    (datetime(2026, 11, 27, 17, 59, tzinfo=timezone.utc), True),
    (datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc), False),
    (datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc), False),
    # Thanksgiving itself and a Saturday.
    (datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc), False),
    (datetime(2026, 10, 10, 16, 0, tzinfo=timezone.utc), False),
])
def test_regular_hours_follow_the_nyse_calendar_including_half_days(
        utc, open_):
    """The option snapshot (Task 10) sets regular_session_open from
    live_calendar.is_nyse_open, which reads the XNYS calendar: 09:30-16:00
    ET, early closes and holidays included. The gate refuses anything else."""
    pytest.importorskip("exchange_calendars")
    from live_calendar import is_nyse_open

    assert is_nyse_open(utc) is open_
    order, snap = _at({}, utc, regular_session_open=is_nyse_open(utc),
                      market_open=True)
    decision = evaluate(order, snap)
    assert decision.allowed is open_, decision.reason_codes
    assert ("market.regular_hours_required" in decision.reason_codes) is (
        not open_)


def test_a_put_is_sold_in_whole_contracts_only():
    with pytest.raises(ValueError, match="whole contracts"):
        option_intent(quantity=Decimal("1.5"))
    order = option_intent(quantity=Decimal("2"))
    decision = evaluate(order, option_snapshot(
        order, available_cash=Decimal("26000"),
        account_equity=Decimal("104000")))
    assert decision.allowed is True and decision.approved_quantity == 2


def test_collateral_is_cash_not_margin_or_equity():
    """Spec section 9 fix 8: a put is secured by cash. Equity (or margin
    buying power, which the snapshot does not even carry) never covers it."""
    order = option_intent()
    decision = evaluate(order, option_snapshot(
        order, available_cash=Decimal("12999.99"),
        account_equity=Decimal("1000000")))
    assert decision.reason_codes == ("option.collateral_insufficient",)


def test_the_cap_and_collateral_scale_with_quantity():
    """Two contracts: 130 x 100 x 2 = 26,000 of collateral and of cap."""
    order = option_intent(quantity=Decimal("2"))
    snap = option_snapshot(order, available_cash=Decimal("25999.99"),
                           account_equity=Decimal("104000"))
    assert evaluate(order, snap).reason_codes == (
        "option.collateral_insufficient",)
    snap = option_snapshot(order, available_cash=Decimal("26000"),
                           account_equity=Decimal("103999.96"))
    assert evaluate(order, snap).reason_codes == ("option.underlying_cap",)


def test_buy_to_close_may_exceed_neither_the_short_nor_close_a_long():
    closing = buy_to_close()
    long_held = evaluate(closing, option_snapshot(
        closing, position_quantity=Decimal("1")))
    assert "option.short_position_insufficient" in long_held.reason_codes


def test_sell_to_close_needs_a_long_at_least_as_large():
    order = option_intent(position_intent="sell_to_close", reduce_only=True,
                          source=OrderSource.RISK_EXIT)
    ok = evaluate(order, option_snapshot(order, position_quantity=Decimal("1")))
    assert ok.allowed is True, ok.reason_codes
    short = evaluate(order, option_snapshot(
        order, position_quantity=Decimal("-1")))
    assert "option.long_position_insufficient" in short.reason_codes


@pytest.mark.parametrize("age,stale", [(60, False), (61, True)])
def test_option_quote_freshness_is_judged_by_the_quote_timestamp(age, stale):
    """F13: the DECISION mark is the quote's own timestamp; older than 60 s
    is refused (Task 10 refreshes it over REST before submitting)."""
    quoted = RTH - timedelta(seconds=age)
    order = option_intent(quote_at=quoted)
    decision = evaluate(order, option_snapshot(order))
    assert ("quote.stale" in decision.reason_codes) is stale
    assert decision.allowed is (not stale)


def test_a_closing_intent_is_not_bound_by_the_opening_order_cap():
    order = buy_to_close(quantity=Decimal("3"))
    decision = evaluate(order, option_snapshot(
        order, position_quantity=Decimal("-3"), quote_price=Decimal("40"),
        max_order_notional=Decimal("100")))
    assert decision.allowed is True, decision.reason_codes
    assert "cash.insufficient" in evaluate(order, option_snapshot(
        order, position_quantity=Decimal("-3"), quote_price=Decimal("67"),
    )).reason_codes
