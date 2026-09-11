"""The live counterpart of PortfolioEmulator.pending_execution_symbols().

Strategy EB's pending-buy guard is opt-in because live there was nothing to
read: the object the broker hands a strategy as `portfolio_emulator` is the
broker ADAPTER, and the adapter has no `pending_execution_symbols`. With the
flag on, every session blocked every buy.

These tests pin the two properties the guard depends on. The shape must match
the emulator's — a tuple of non-empty, already-upper-cased symbols, which is
also the stricter contract Strategy X validates. And an unreadable order book
must RAISE: `list_open_orders` answers a dead endpoint and a genuinely flat
book with the same `[]`, and reading a dead endpoint as "nothing pending" is
how the guard would wave through the duplicate buy it exists to stop.
"""
import os
import sys
from types import SimpleNamespace

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from broker_adapters._wal import InMemoryStore, LiveOrderWAL  # noqa: E402
from broker_adapters.alpaca import AlpacaAdapter  # noqa: E402
from broker_adapters.base import OrderRef  # noqa: E402
from live_pending_orders import (  # noqa: E402
    LivePendingOrdersUnavailable,
    LivePendingOrderView,
    live_pending_symbols,
)


def ref(symbol, status):
    return OrderRef(broker_order_id="b1", client_order_id="c1", symbol=symbol,
                    side="buy", qty=1.0, status=status)


class FakeAdapter:
    """Duck-types the one adapter method the reader is allowed to use."""

    def __init__(self, result, portfolio_value=1234.5):
        self.result = result
        self.calls = []
        self._portfolio_value = portfolio_value

    def list_open_orders_strict(self, limit=200):
        self.calls.append(limit)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def get_portfolio_value(self, prices=None):
        return self._portfolio_value


def test_only_unresolved_orders_are_returned_and_they_are_upper_cased():
    adapter = FakeAdapter([
        ref(" tqqq ", "new"),
        ref("spy", "partially_filled"),
        ref("GLD", "accepted"),
        ref("XLE", "filled"),
        ref("GDX", "canceled"),
        ref("BIL", "rejected"),
        ref("VIXY", "expired"),
        ref("VIXM", "done_for_day"),
    ])
    assert set(live_pending_symbols(adapter)) == {"TQQQ", "SPY", "GLD"}


def test_a_symbol_with_two_working_orders_is_reported_once():
    adapter = FakeAdapter([ref("TQQQ", "new"), ref("tqqq", "accepted")])
    assert live_pending_symbols(adapter) == ("TQQQ",)


def test_a_flat_book_is_an_empty_tuple_not_a_failure():
    """The common case. Failing closed here would block every buy forever."""
    assert live_pending_symbols(FakeAdapter([])) == ()


def test_an_unknown_status_counts_as_unresolved():
    """Alpaca adds states; an unrecognised one is not evidence of a fill."""
    assert live_pending_symbols(FakeAdapter([ref("SPY", "held")])) == ("SPY",)


def test_the_shape_satisfies_the_emulator_contract():
    out = live_pending_symbols(FakeAdapter([ref("tqqq", "new")]))
    assert isinstance(out, tuple)
    assert all(type(s) is str and s and s == s.strip().upper() for s in out)


def test_an_unreadable_order_book_raises():
    adapter = FakeAdapter(RuntimeError("503 service unavailable"))
    with pytest.raises(LivePendingOrdersUnavailable):
        live_pending_symbols(adapter)


def test_an_adapter_without_the_strict_reader_raises():
    with pytest.raises(LivePendingOrdersUnavailable):
        live_pending_symbols(SimpleNamespace(list_open_orders=lambda: []))


def test_a_missing_adapter_raises():
    with pytest.raises(LivePendingOrdersUnavailable):
        live_pending_symbols(None)


@pytest.mark.parametrize("result", [None, "SPY", {"SPY": 1}, 7])
def test_a_non_list_result_raises(result):
    with pytest.raises(LivePendingOrdersUnavailable):
        live_pending_symbols(FakeAdapter(result))


@pytest.mark.parametrize("bad", [
    SimpleNamespace(symbol=None, status="new"),
    SimpleNamespace(symbol="", status="new"),
    SimpleNamespace(symbol="   ", status="new"),
    SimpleNamespace(symbol="SPY", status=None),
    SimpleNamespace(symbol="SPY", status=""),
    SimpleNamespace(symbol=7, status="new"),
    object(),
    None,
])
def test_a_malformed_order_raises(bad):
    with pytest.raises(LivePendingOrdersUnavailable):
        live_pending_symbols(FakeAdapter([ref("TQQQ", "new"), bad]))


# --- the view the broker hands Strategy EB ---------------------------------


def test_the_view_reads_pending_orders_through_the_adapter():
    adapter = FakeAdapter([ref("tqqq", "new")])
    assert LivePendingOrderView(adapter).pending_execution_symbols() == ("TQQQ",)


def test_the_view_raises_when_the_order_book_cannot_be_read():
    view = LivePendingOrderView(FakeAdapter(RuntimeError("boom")))
    with pytest.raises(LivePendingOrdersUnavailable):
        view.pending_execution_symbols()


def test_the_view_forwards_every_other_read_to_the_adapter():
    adapter = FakeAdapter([], portfolio_value=9999.0)
    view = LivePendingOrderView(adapter)
    assert view.get_portfolio_value({"SPY": 1.0}) == 9999.0
    assert view.list_open_orders_strict == adapter.list_open_orders_strict


def test_the_view_does_not_invent_attributes_the_adapter_lacks():
    view = LivePendingOrderView(FakeAdapter([]))
    with pytest.raises(AttributeError):
        view.get_buying_power


# --- the Alpaca adapter's fail-closed reader -------------------------------


class _Client:
    """The slice of alpaca-py's TradingClient the adapter touches at boot."""

    def __init__(self, orders=None, orders_exc=None):
        self._orders = orders or []
        self._orders_exc = orders_exc

    def get_account(self):
        return SimpleNamespace(
            cash="10000", buying_power="10000", daytrading_buying_power="10000",
            equity="10000", last_equity="10000", pattern_day_trader=False,
            daytrade_count=0, account_blocked=False, trading_blocked=False)

    def get_all_positions(self):
        return []

    def get_orders(self, filter=None):
        if self._orders_exc is not None:
            raise self._orders_exc
        return self._orders


def _adapter(client):
    return AlpacaAdapter(api_key="k", api_secret="s", paper=True,
                         instance_id="instance-1",
                         wal=LiveOrderWAL(InMemoryStore()), initial_value=10000,
                         seed_trades_from_broker=False, _test_client=client)


def _alpaca_order(symbol, status):
    return SimpleNamespace(id="o1", client_order_id="c1", symbol=symbol,
                           side="buy", qty="1", status=status, filled_qty="0",
                           filled_avg_price=None, submitted_at=None,
                           notional=None)


def test_the_strict_reader_raises_where_the_lenient_one_answers_empty():
    adapter = _adapter(_Client(orders_exc=RuntimeError("503 unavailable")))
    # The documented lenient behaviour is deliberately preserved.
    assert adapter.list_open_orders() == []
    with pytest.raises(Exception):
        adapter.list_open_orders_strict()
    with pytest.raises(LivePendingOrdersUnavailable):
        live_pending_symbols(adapter)


def test_the_strict_reader_returns_the_open_orders_alpaca_reports():
    adapter = _adapter(_Client(orders=[_alpaca_order("tqqq", "new"),
                                       _alpaca_order("SPY", "filled")]))
    assert [o.symbol for o in adapter.list_open_orders_strict()] == ["tqqq",
                                                                    "SPY"]
    assert live_pending_symbols(adapter) == ("TQQQ",)
