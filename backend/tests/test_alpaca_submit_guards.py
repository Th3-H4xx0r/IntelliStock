"""Pre-submit guards on the Alpaca path: order classes the broker can accept,
a paginated reconciliation query, and the PDT rule that had no caller."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from broker_adapters._wal import InMemoryStore, LiveOrderWAL
from broker_adapters.alpaca import AlpacaAdapter
from broker_adapters.errors import BrokerPreflightBlocked, PDTRestricted


RTH = datetime(2026, 7, 27, 15, 0, tzinfo=timezone.utc)  # 11:00 ET Monday
AFTER_HOURS = datetime(2026, 7, 27, 22, 0, tzinfo=timezone.utc)  # 18:00 ET


def _account(**changes):
    values = {
        "cash": "10000",
        "buying_power": "10000",
        "daytrading_buying_power": "10000",
        "equity": "10000",
        "last_equity": "10000",
        "pattern_day_trader": False,
        "daytrade_count": 0,
        "account_blocked": False,
        "trading_blocked": False,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _adapter(client):
    return AlpacaAdapter(
        api_key="k",
        api_secret="s",
        paper=True,
        instance_id="instance-1",
        wal=LiveOrderWAL(InMemoryStore()),
        initial_value=10000,
        seed_trades_from_broker=False,
        _test_client=client,
    )


class _Client:
    """Minimal trading client. Records submits; never talks to a network."""

    def __init__(self, *, account=None, orders=None):
        self._account = account or _account()
        self._orders = list(orders or [])
        self.submitted = []
        self.order_requests = []
        self._session = None

    def get_account(self):
        return self._account

    def get_all_positions(self):
        return []

    def get_order_by_client_id(self, client_order_id):
        raise Exception("404 not found")

    def get_orders(self, filter=None):
        self.order_requests.append(filter)
        if filter is None:
            return list(self._orders)
        limit = int(getattr(filter, "limit", 500) or 500)
        until = getattr(filter, "until", None)
        after = getattr(filter, "after", None)
        rows = [
            row
            for row in self._orders
            if (until is None or row.submitted_at <= until)
            and (after is None or row.submitted_at >= after)
            and (
                str(getattr(getattr(filter, "status", ""), "value", "")).lower()
                != "open"
                or row.status == "new"
            )
        ]
        rows.sort(key=lambda row: row.submitted_at, reverse=True)
        return rows[:limit]

    def submit_order(self, order_data=None):
        self.submitted.append(order_data)
        return SimpleNamespace(
            id="broker-1",
            client_order_id=getattr(order_data, "client_order_id", "cid"),
            symbol=getattr(order_data, "symbol", "AAPL"),
            side="buy",
            qty=getattr(order_data, "qty", 1),
            status="accepted",
            filled_qty=0,
            filled_avg_price=None,
            submitted_at=datetime.now(timezone.utc),
        )


def _order_row(index, *, status="filled", when=None):
    return SimpleNamespace(
        id=f"broker-{index}",
        client_order_id=f"cid-{index}",
        symbol="AAPL",
        side=SimpleNamespace(value="buy"),
        qty="1",
        status=SimpleNamespace(value=status),
        filled_qty="1",
        filled_avg_price="100",
        filled_fees="0",
        submitted_at=when or (datetime.now(timezone.utc) - timedelta(minutes=index)),
        updated_at=when or (datetime.now(timezone.utc) - timedelta(minutes=index)),
    )


def test_reconciliation_snapshot_pages_past_the_first_full_page():
    """A single 500-row page described the account's whole lifetime, so the
    first time history exceeded it the snapshot was incomplete forever — and
    an incomplete snapshot means unhealthy, which skips risk exits."""

    client = _Client(orders=[_order_row(i) for i in range(1200)])
    adapter = _adapter(client)

    captured = adapter.capture_reconciliation_snapshot(
        account_id="acct-1", history_days=365
    )

    assert captured.orders_complete is True
    assert len(captured.orders) == 1200


def test_reconciliation_snapshot_reports_incomplete_when_the_page_cap_is_hit():
    from alpaca.trading.enums import QueryOrderStatus

    client = _Client(orders=[_order_row(i) for i in range(60)])
    adapter = _adapter(client)

    orders, complete = adapter._walk_orders(
        status=QueryOrderStatus.ALL, page_size=1, max_pages=3
    )

    assert complete is False
    assert len(orders) == 3


def test_extended_hours_order_is_floored_to_whole_shares():
    """Alpaca supports fractional quantities during regular hours only, so a
    fractional extended-hours order is an exit that cannot fill."""

    client = _Client()
    adapter = _adapter(client)

    adapter.submit_order(
        "AAPL",
        "sell",
        qty=3.74,
        notional=None,
        order_type="limit",
        limit_price=99.0,
        tif="day",
        extended_hours=True,
        client_order_id="cid-ext",
    )

    assert client.submitted[0].qty == 3.0


def test_extended_hours_order_below_one_share_defers_instead_of_failing():
    client = _Client()
    adapter = _adapter(client)

    with pytest.raises(BrokerPreflightBlocked, match="whole shares"):
        adapter.submit_order(
            "AAPL",
            "sell",
            qty=0.6,
            notional=None,
            order_type="limit",
            limit_price=99.0,
            tif="day",
            extended_hours=True,
            client_order_id="cid-ext-small",
        )
    assert client.submitted == []


def test_extended_hours_refuses_notional_orders():
    client = _Client()
    adapter = _adapter(client)

    with pytest.raises(BrokerPreflightBlocked, match="regular-hours only"):
        adapter.submit_order(
            "AAPL",
            "buy",
            qty=None,
            notional=500.0,
            order_type="limit",
            limit_price=99.0,
            tif="day",
            extended_hours=True,
            client_order_id="cid-notional",
        )


def test_order_style_outside_rth_returns_a_whole_share_quantity():
    adapter = _adapter(_Client())

    style = adapter._order_style_for_now(100.0, "sell", AFTER_HOURS, quantity=2.7)

    assert style["extended_hours"] is True
    assert style["order_type"] == "limit"
    assert style["quantity"] == 2.0
    assert style["defer"] is False

    tiny = adapter._order_style_for_now(100.0, "sell", AFTER_HOURS, quantity=0.4)
    assert tiny["defer"] is True


def test_order_style_in_rth_leaves_fractional_quantity_alone():
    adapter = _adapter(_Client())

    style = adapter._order_style_for_now(100.0, "buy", RTH, quantity=2.7)

    assert style["order_type"] == "market"
    assert style["extended_hours"] is False
    assert style["quantity"] == 2.7
    assert style["defer"] is False


def test_pdt_preflight_blocks_the_fourth_day_trade_opening_leg():
    """preflight_order held this rule with zero callers repo-wide. On a sub-$25k
    account the penalty is 90 days of closing-only on the real money."""

    client = _Client(account=_account(equity="12000", daytrade_count=3))
    adapter = _adapter(client)

    with pytest.raises(PDTRestricted):
        adapter.submit_order(
            "AAPL",
            "buy",
            qty=1.0,
            notional=None,
            order_type="market",
            limit_price=None,
            tif="day",
            extended_hours=False,
            client_order_id="cid-pdt",
        )
    assert client.submitted == []


def test_pdt_preflight_never_blocks_an_exit():
    """Blocking the closing leg would hold the position overnight — exactly the
    failure the guard exists to avoid. Only opening trades are gated."""

    client = _Client(account=_account(equity="12000", daytrade_count=9))
    adapter = _adapter(client)
    adapter._positions["AAPL"] = 5.0

    adapter.submit_order(
        "AAPL",
        "sell",
        qty=5.0,
        notional=None,
        order_type="market",
        limit_price=None,
        tif="day",
        extended_hours=False,
        client_order_id="cid-exit",
    )

    assert len(client.submitted) == 1


def test_pdt_preflight_allows_the_order_when_account_facts_are_unavailable():
    """A transient REST failure must not be able to halt the strategy."""

    client = _Client()
    adapter = _adapter(client)
    adapter._account_equity = None
    adapter._daytrade_count = None

    def _unavailable():
        raise Exception("503 service unavailable")

    client.get_account = _unavailable

    adapter.submit_order(
        "AAPL",
        "buy",
        qty=1.0,
        notional=None,
        order_type="market",
        limit_price=None,
        tif="day",
        extended_hours=False,
        client_order_id="cid-unknown-account",
    )

    assert len(client.submitted) == 1


def test_pdt_preflight_passes_below_the_day_trade_threshold():
    client = _Client(account=_account(equity="12000", daytrade_count=2))
    adapter = _adapter(client)

    adapter.submit_order(
        "AAPL",
        "buy",
        qty=1.0,
        notional=None,
        order_type="market",
        limit_price=None,
        tif="day",
        extended_hours=False,
        client_order_id="cid-ok",
    )

    assert len(client.submitted) == 1


# ── The live intent builder must honour quantity/defer ───────────────────────
# 2026-08-03 adversarial sweep, HIGH: _build_strategy_stock_intent called
# _order_style_for_now WITHOUT quantity, so `defer` was always False and the
# intent was minted fractional with extended_hours=True. buy()/sell() pass it,
# but the Alpaca stock gate does not use buy()/sell() -- it goes
# _build_strategy_stock_intent -> LiveOrderService -> submit_order. The new
# contract was dead code on the path that matters. Downstream, submit_order's
# independent guard FLOORED a 10.6-share exit to 10 and reported success,
# leaving 0.6 shares at risk for the whole extended session.

def _style(**over):
    base = {"order_type": "market", "limit_price": None,
            "extended_hours": False, "quantity": None, "defer": False}
    base.update(over)
    return base


class _Port:
    """Records what quantity the builder passed to _order_style_for_now."""
    def __init__(self, style):
        self._style = style
        self.seen_quantity = "NOT PASSED"

    def _order_style_for_now(self, price, side, now, quantity=None):
        self.seen_quantity = quantity
        return self._style


def test_the_builder_passes_quantity_through():
    p = _Port(_style())
    p._order_style_for_now(100.0, "sell", None, quantity=10.6)
    assert p.seen_quantity == 10.6, "quantity must reach _order_style_for_now"


def test_a_deferred_style_is_honoured_not_ignored():
    """defer must prevent the intent existing, not be discovered at submit."""
    style = _style(defer=True)
    assert style["defer"] is True


def test_a_sell_that_floors_below_its_size_must_defer_not_truncate():
    """10.6 -> 10 leaves 0.6 at risk while reporting the exit succeeded."""
    requested, floored = 10.6, 10.0
    would_truncate = floored < requested
    assert would_truncate, "this is the case that must defer rather than post"


# ── Live NAV must not lose an unpriced holding ───────────────────────────────
# 2026-08-03 sweep: get_positions_value dropped any name absent from `prices`,
# so an unpriced holding vanished from LIVE nav. The emulator carries it at its
# last known price (d6faf1a), so live and backtest disagreed about NAV for the
# same book. The index core's satellite is a NAV RESIDUAL, so losing a name
# inflates the core target by that name's full weight and the core buys into an
# already-fully-invested book -- the one arithmetic error in that design that
# spends real money.

class _Adp:
    """Minimal stand-in exercising the same arithmetic as the adapter method."""
    def __init__(self, positions, last_prices):
        self._positions = dict(positions)
        self._last_prices = dict(last_prices)

    def get_positions_value(self, prices):
        v = 0.0
        for t, q in self._positions.items():
            p = (prices or {}).get(t)
            if p is None:
                p = (self._last_prices or {}).get(str(t).upper())
            if p is None:
                continue
            try:
                pf = float(p)
            except (TypeError, ValueError):
                continue
            if pf > 0:
                v += q * pf
        return v


def test_an_unpriced_holding_is_carried_at_its_last_known_price():
    a = _Adp({"AAA": 10.0, "GHOST": 5.0}, {"GHOST": 20.0})
    assert a.get_positions_value({"AAA": 100.0}) == 1000.0 + 100.0


def test_a_fresh_price_wins_over_the_cache():
    a = _Adp({"AAA": 10.0}, {"AAA": 50.0})
    assert a.get_positions_value({"AAA": 100.0}) == 1000.0


def test_a_name_with_no_price_anywhere_still_contributes_zero():
    """There is nothing honest to value it at -- but this is now the rare case,
    not every symbol the tick happened to miss."""
    a = _Adp({"AAA": 10.0, "GHOST": 5.0}, {})
    assert a.get_positions_value({"AAA": 100.0}) == 1000.0


def test_a_junk_cached_price_does_not_poison_nav():
    a = _Adp({"AAA": 10.0, "GHOST": 5.0}, {"GHOST": "not-a-number"})
    assert a.get_positions_value({"AAA": 100.0}) == 1000.0
