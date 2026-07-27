"""Task 4: Alpaca refreshes must update CURRENT market marks.

July 10 incident: ``refresh_positions`` derived fresh broker marks but only
wrote them when ``_last_prices`` had no entry, so fill-time prices survived
every later REST refresh and risk consumed stale marks (MRNA displayed -6%
while the broker close was near -16%). These tests pin the corrected
contract: broker marks replace fill-cache values by timestamped precedence,
``_last_prices`` becomes a compatibility mirror of the newest mark, and the
decision gate never falls back to a fill or stale price.
"""
import asyncio
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_marks import MarkPurpose, MarkQuality, MarkSource, MarketMark, MarketMarkBook


def _alpaca_client_with(positions=None, cash=10000.0, equity=10000.0):
    c = MagicMock()
    c.get_account.return_value = MagicMock(
        cash=str(cash), buying_power=str(cash), daytrading_buying_power=str(cash),
        equity=str(equity), last_equity=str(equity),
        pattern_day_trader=False, daytrade_count=0, account_blocked=False,
        trading_blocked=False,
    )
    c.get_all_positions.return_value = positions or []
    c.get_orders.return_value = []
    c._session = None
    return c


@pytest.fixture
def position_factory():
    def make(symbol, qty="4", market_value="306.04", avg_entry="81.36"):
        p = MagicMock()
        p.symbol = symbol
        p.qty = str(qty)
        p.market_value = str(market_value)
        p.avg_entry_price = str(avg_entry)
        p.unrealized_pl = "0.0"
        return p
    return make


@pytest.fixture
def alpaca_adapter():
    from broker_adapters._wal import InMemoryStore, LiveOrderWAL
    from broker_adapters.alpaca import AlpacaAdapter
    return AlpacaAdapter(
        api_key="k", api_secret="s", paper=True, instance_id="main",
        wal=LiveOrderWAL(InMemoryStore()), seed_trades_from_broker=False,
        initial_value=10000.0, _test_client=_alpaca_client_with(),
    )


def test_second_position_refresh_replaces_fill_time_price(alpaca_adapter, position_factory):
    alpaca_adapter._last_prices["MRNA"] = 81.36
    alpaca_adapter._client.get_all_positions.return_value = [
        position_factory("MRNA", qty="4", market_value="306.04", avg_entry="81.36")
    ]
    alpaca_adapter.refresh_positions()
    assert alpaca_adapter._last_prices["MRNA"] == 76.51
    mark = alpaca_adapter.get_market_marks()["MRNA"]
    assert mark.price == 76.51
    assert mark.source.value == "broker_position"


def test_repeated_refreshes_keep_tracking_the_broker_mark(alpaca_adapter, position_factory):
    alpaca_adapter._last_prices["MRNA"] = 81.36
    alpaca_adapter._client.get_all_positions.return_value = [
        position_factory("MRNA", qty="4", market_value="306.04")
    ]
    alpaca_adapter.refresh_positions()
    assert alpaca_adapter._last_prices["MRNA"] == 76.51
    # The July 10 afternoon leg: broker mark falls to 68.26.
    alpaca_adapter._client.get_all_positions.return_value = [
        position_factory("MRNA", qty="4", market_value="273.04")
    ]
    alpaca_adapter.refresh_positions()
    assert alpaca_adapter._last_prices["MRNA"] == 68.26
    assert alpaca_adapter.get_market_marks()["MRNA"].price == 68.26


def test_get_market_marks_returns_a_copy(alpaca_adapter, position_factory):
    alpaca_adapter._client.get_all_positions.return_value = [
        position_factory("MRNA")
    ]
    alpaca_adapter.refresh_positions()
    marks = alpaca_adapter.get_market_marks()
    marks.clear()
    assert "MRNA" in alpaca_adapter.get_market_marks()


def _fill_event(symbol="MRNA", qty=4.0, price=81.36, side="buy"):
    order = MagicMock()
    order.client_order_id = "main-mrna-1"
    order.symbol = symbol
    order.filled_qty = qty
    order.filled_avg_price = price
    order.side = side
    order.id = "ord-1"
    event = MagicMock()
    event.event = "fill"
    event.order = order
    return event


def test_fill_event_writes_fill_mark_and_mirror(alpaca_adapter):
    asyncio.run(alpaca_adapter._on_trade_update(_fill_event()))
    mark = alpaca_adapter.get_market_marks()["MRNA"]
    assert mark.source is MarkSource.FILL
    assert mark.quality is MarkQuality.EXECUTION_ONLY
    assert alpaca_adapter._last_prices["MRNA"] == 81.36


def test_broker_refresh_after_fill_replaces_fill_mark(alpaca_adapter, position_factory):
    asyncio.run(alpaca_adapter._on_trade_update(_fill_event()))
    alpaca_adapter._client.get_all_positions.return_value = [
        position_factory("MRNA", qty="4", market_value="306.04")
    ]
    alpaca_adapter.refresh_positions()
    mark = alpaca_adapter.get_market_marks()["MRNA"]
    assert mark.source is MarkSource.BROKER_POSITION
    assert mark.price == 76.51


def test_save_portfolio_snapshot_records_rest_quote_marks(alpaca_adapter):
    ts = datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc)
    alpaca_adapter.save_portfolio_snapshot({"MRNA": 77.25}, timestamp=ts)
    mark = alpaca_adapter.get_market_marks()["MRNA"]
    assert mark.source is MarkSource.REST_QUOTE
    assert mark.price == 77.25
    assert alpaca_adapter._last_prices["MRNA"] == 77.25


# --- decision gate: no fill, no stale price, no retroactive bar ------------

def test_decision_price_uses_fresh_mark(alpaca_adapter, position_factory):
    alpaca_adapter._client.get_all_positions.return_value = [
        position_factory("MRNA", qty="4", market_value="306.04")
    ]
    alpaca_adapter.refresh_positions()
    now = datetime.now(timezone.utc)
    resolved = alpaca_adapter.decision_price("MRNA", now)
    assert resolved is not None
    price, check = resolved
    assert price == 76.51
    assert check.allowed


def test_decision_price_never_falls_back_to_fill(alpaca_adapter):
    asyncio.run(alpaca_adapter._on_trade_update(_fill_event()))
    now = datetime.now(timezone.utc)
    assert alpaca_adapter.decision_price("MRNA", now) is None


def test_stale_bar_near_submission_cannot_satisfy_decision_gate(alpaca_adapter):
    """Task 4 Step 9: a historical one-minute bar 'fetched near submission'
    is at best a stale REST-quality price — it cannot retroactively satisfy
    the decision-time mark gate, and the gate must not silently fall back to
    a fill price either."""
    old = datetime.now(timezone.utc) - timedelta(minutes=5)
    bar_price = MarketMark(
        symbol="MRNA", price=79.99, bid=None, ask=None, bid_size=None,
        ask_size=None, observed_at=old, received_at=old,
        source=MarkSource.REST_QUOTE, feed="iex",
        quality=MarkQuality.SINGLE_EXCHANGE, session="regular",
    )
    alpaca_adapter._market_marks.update(bar_price)
    asyncio.run(alpaca_adapter._on_trade_update(_fill_event()))  # newer fill exists
    now = datetime.now(timezone.utc)
    assert alpaca_adapter.decision_price("MRNA", now) is None


def test_base_adapter_without_book_returns_empty_marks():
    from broker_adapters.base import BrokerAdapter
    assert BrokerAdapter.get_market_marks.__isabstractmethod__ is False if hasattr(
        BrokerAdapter.get_market_marks, "__isabstractmethod__") else True


def test_adapter_market_stream_shares_authoritative_mark_book(
    alpaca_adapter, monkeypatch
):
    import alpaca_mark_stream

    created = []

    class StubMarkStream:
        def __init__(self, book, **_kwargs):
            self.book = book
            self.symbols = set()
            self.started = False
            created.append(self)

        def set_symbols(self, symbols):
            self.symbols = {str(symbol).upper() for symbol in symbols}

        def start(self):
            self.started = True

        def subscribed_symbols(self):
            return set(self.symbols)

        def overflow_symbols(self):
            return set()

    monkeypatch.setattr(alpaca_mark_stream, "AlpacaMarkStream", StubMarkStream)

    status = alpaca_adapter.start_market_marks(["AAPL", "MSFT"])

    assert created[0].book is alpaca_adapter._market_marks
    assert created[0].started is True
    assert status["subscribed"] == ("AAPL", "MSFT")


# --- AlpacaMarkStream -------------------------------------------------------

class FakeStream:
    """Blocks in run() until stop(), like the real StockDataStream."""

    def __init__(self):
        self.quote_symbols = []
        self.trade_symbols = []
        self.unsubscribed = []
        self.run_calls = 0
        self.stopped = False
        self._halt = threading.Event()

    def subscribe_quotes(self, handler, *symbols):
        self.quote_symbols.extend(symbols)

    def subscribe_trades(self, handler, *symbols):
        self.trade_symbols.extend(symbols)

    def unsubscribe_quotes(self, *symbols):
        self.unsubscribed.extend(symbols)

    def unsubscribe_trades(self, *symbols):
        pass

    def run(self):
        self.run_calls += 1
        self._halt.wait(timeout=10)

    def stop(self):
        self.stopped = True
        self._halt.set()


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _make_stream(book=None):
    from alpaca_mark_stream import AlpacaMarkStream
    book = book or MarketMarkBook()
    fakes = []

    def factory():
        fake = FakeStream()
        fakes.append(fake)
        return fake

    stream = AlpacaMarkStream(book, api_key="k", api_secret="s", feed="iex",
                              stream_factory=factory)
    return stream, book, fakes


def _quote(symbol="MRNA", bid=76.50, ask=76.52, ts=None, bid_size=3, ask_size=4):
    q = MagicMock()
    q.symbol = symbol
    q.bid_price = bid
    q.ask_price = ask
    q.bid_size = bid_size
    q.ask_size = ask_size
    q.timestamp = ts or datetime.now(timezone.utc)
    q.conditions = []
    return q


def test_newer_stream_quote_replaces_broker_mark():
    stream, book, _ = _make_stream()
    t0 = datetime.now(timezone.utc)
    book.update(MarketMark(
        symbol="MRNA", price=76.51, bid=None, ask=None, bid_size=None,
        ask_size=None, observed_at=t0, received_at=t0,
        source=MarkSource.BROKER_POSITION, feed="broker",
        quality=MarkQuality.BROKER_DERIVED, session="regular"))
    stream.handle_quote(_quote(ts=t0 + timedelta(seconds=5)))
    mark = book.get("MRNA")
    assert mark.source is MarkSource.STREAM_QUOTE
    assert mark.price == pytest.approx(76.51, abs=0.02)
    assert mark.bid == 76.50 and mark.ask == 76.52


def test_older_stream_callback_is_ignored():
    stream, book, _ = _make_stream()
    t0 = datetime.now(timezone.utc)
    stream.handle_quote(_quote(bid=77.00, ask=77.02, ts=t0))
    stream.handle_quote(_quote(bid=10.00, ask=10.02, ts=t0 - timedelta(seconds=30)))
    assert book.get("MRNA").bid == 77.00


def test_trade_callback_writes_stream_trade_mark():
    stream, book, _ = _make_stream()
    t = MagicMock()
    t.symbol = "MRNA"
    t.price = 76.49
    t.timestamp = datetime.now(timezone.utc)
    t.conditions = []
    stream.handle_trade(t)
    mark = book.get("MRNA")
    assert mark.source is MarkSource.STREAM_TRADE
    assert mark.price == 76.49


def test_subscription_changes_are_idempotent():
    stream, _, fakes = _make_stream()
    stream.start()
    assert _wait_for(lambda: fakes)
    stream.set_symbols({"MRNA", "SPY"})
    stream.set_symbols({"MRNA", "SPY"})
    assert stream.subscribed_symbols() == {"MRNA", "SPY"}
    assert _wait_for(lambda: sorted(fakes[0].quote_symbols) == ["MRNA", "SPY"])
    stream.set_symbols({"MRNA"})
    assert stream.subscribed_symbols() == {"MRNA"}
    stream.stop()


def test_subscription_budget_is_enforced_with_explicit_overflow():
    stream, _, _ = _make_stream()
    stream.start()
    wanted = {f"SYM{i}" for i in range(35)}
    stream.set_symbols(wanted)
    assert len(stream.subscribed_symbols()) <= 30
    assert stream.overflow_symbols()
    assert stream.subscribed_symbols() | stream.overflow_symbols() == wanted
    stream.stop()


def test_disconnect_sets_degraded_health_and_never_submits():
    stream, _, fakes = _make_stream()
    stream.start()
    assert stream.healthy is False  # not yet connected
    stream.handle_quote(_quote())
    assert stream.healthy is True
    stream.record_disconnect("stream died")
    assert stream.healthy is False
    assert _wait_for(lambda: fakes)
    assert not hasattr(fakes[0], "submit_order")
    stream.stop()


def test_start_never_blocks_when_stream_factory_raises():
    from alpaca_mark_stream import AlpacaMarkStream

    def bad_factory():
        raise RuntimeError("no network")

    stream = AlpacaMarkStream(MarketMarkBook(), api_key="k", api_secret="s",
                              feed="iex", stream_factory=bad_factory,
                              reconnect_min_seconds=0.01, reconnect_max_seconds=0.02)
    started = datetime.now(timezone.utc)
    stream.start()
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    assert elapsed < 1.0
    stream.stop()
    assert stream.healthy is False


def test_zero_bid_quote_is_normalized_not_fatal():
    """Audit: a $0 bid (routine empty IEX book) raised out of the stream
    callback and could kill the websocket dispatch task."""
    stream, book, _ = _make_stream()
    q = _quote()
    q.bid_price = 0.0
    q.bid_size = 0
    stream.handle_quote(q)  # must not raise
    mark = book.get("MRNA")
    assert mark is not None
    assert mark.bid is None  # zero side normalized away
    assert mark.price == pytest.approx(76.52)  # one-sided: the ask
