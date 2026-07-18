"""Alpaca StockDataStream lifecycle feeding a MarketMarkBook (Task 4).

Maps quote and trade callbacks into typed ``MarketMark`` updates, reconnects
with bounded backoff on a daemon thread (never blocking the broker strategy
thread), and enforces the websocket subscription budget explicitly instead of
silently dropping symbols. This component holds data-plane credentials only
and has no order-submission capability of any kind.
"""
import threading
from datetime import datetime, timezone

from market_marks import (
    MarkQuality,
    MarkSource,
    MarketMark,
    classify_session,
)

# Alpaca Basic-plan stock websocket subscription budget (adversarial review
# H02). Symbols beyond the budget are recorded as overflow, never silently
# unmarked.
MAX_STREAM_SYMBOLS = 30


class AlpacaMarkStream:
    """Owns one StockDataStream and writes every event into the mark book."""

    def __init__(self, book, *, api_key, api_secret, feed="iex",
                 stream_factory=None, max_symbols=MAX_STREAM_SYMBOLS,
                 reconnect_min_seconds=1.0, reconnect_max_seconds=60.0):
        self._book = book
        self._api_key = api_key
        self._api_secret = api_secret
        self._feed = str(feed or "iex").lower()
        self._quality = (MarkQuality.CONSOLIDATED if self._feed == "sip"
                         else MarkQuality.SINGLE_EXCHANGE)
        self._stream_factory = stream_factory or self._default_stream_factory
        self._max_symbols = int(max_symbols)
        self._reconnect_min = float(reconnect_min_seconds)
        self._reconnect_max = float(reconnect_max_seconds)

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._stream = None
        self._subscribed = set()
        self._overflow = set()
        self._healthy = False
        self._last_event_utc = None
        self._last_disconnect_reason = None

    # -- health ---------------------------------------------------------------

    @property
    def healthy(self):
        with self._lock:
            return self._healthy

    def record_disconnect(self, reason):
        with self._lock:
            self._healthy = False
            self._last_disconnect_reason = str(reason)

    # -- callbacks (sync core; async wrappers delegate here) ------------------

    def handle_quote(self, quote, received_at=None):
        symbol = str(getattr(quote, "symbol", "") or "")
        bid = _as_float(getattr(quote, "bid_price", None))
        ask = _as_float(getattr(quote, "ask_price", None))
        # Zero/negative sides are routine on IEX (empty book). Normalize to
        # None so a one-sided quote is usable and MarketMark validation can
        # never raise out of the stream callback (audit 2026-07-18: a $0 bid
        # crashed the dispatch coroutine).
        bid = bid if bid and bid > 0 else None
        ask = ask if ask and ask > 0 else None
        if not symbol or (bid is None and ask is None):
            return False
        if bid is not None and ask is not None:
            price = (bid + ask) / 2.0
        else:
            price = bid if bid is not None else ask
        if not price or price <= 0:
            return False
        observed = _aware(getattr(quote, "timestamp", None))
        received = received_at or datetime.now(timezone.utc)
        mark = MarketMark(
            symbol=symbol, price=price, bid=bid, ask=ask,
            bid_size=_as_float(getattr(quote, "bid_size", None)),
            ask_size=_as_float(getattr(quote, "ask_size", None)),
            observed_at=observed, received_at=received,
            source=MarkSource.STREAM_QUOTE, feed=self._feed,
            quality=self._quality, session=classify_session(observed),
            conditions=tuple(getattr(quote, "conditions", None) or ()),
        )
        accepted = self._book.update(mark)
        with self._lock:
            self._healthy = True
            self._last_event_utc = received
        return accepted

    def handle_trade(self, trade, received_at=None):
        symbol = str(getattr(trade, "symbol", "") or "")
        price = _as_float(getattr(trade, "price", None))
        if not symbol or not price or price <= 0:
            return False
        observed = _aware(getattr(trade, "timestamp", None))
        received = received_at or datetime.now(timezone.utc)
        mark = MarketMark(
            symbol=symbol, price=price, bid=None, ask=None,
            bid_size=None, ask_size=None,
            observed_at=observed, received_at=received,
            source=MarkSource.STREAM_TRADE, feed=self._feed,
            quality=self._quality, session=classify_session(observed),
            conditions=tuple(getattr(trade, "conditions", None) or ()),
        )
        accepted = self._book.update(mark)
        with self._lock:
            self._healthy = True
            self._last_event_utc = received
        return accepted

    async def _async_on_quote(self, quote):
        self.handle_quote(quote)

    async def _async_on_trade(self, trade):
        self.handle_trade(trade)

    # -- subscriptions --------------------------------------------------------

    def set_symbols(self, symbols):
        """Idempotently reconcile subscriptions to ``symbols`` (upper-cased).

        Deterministically keeps the first ``max_symbols`` in sorted order and
        records the remainder as overflow so no symbol is silently unmarked.
        """
        wanted = {str(s).upper() for s in (symbols or ()) if str(s).strip()}
        ordered = sorted(wanted)
        keep = set(ordered[:self._max_symbols])
        overflow = wanted - keep
        with self._lock:
            to_add = sorted(keep - self._subscribed)
            to_remove = sorted(self._subscribed - keep)
            stream = self._stream
            self._subscribed = keep
            self._overflow = overflow
        if stream is not None:
            try:
                if to_add:
                    stream.subscribe_quotes(self._async_on_quote, *to_add)
                    stream.subscribe_trades(self._async_on_trade, *to_add)
                if to_remove:
                    stream.unsubscribe_quotes(*to_remove)
                    stream.unsubscribe_trades(*to_remove)
            except Exception as exc:
                self.record_disconnect(f"subscription change failed: {exc}")

    def subscribed_symbols(self):
        with self._lock:
            return set(self._subscribed)

    def overflow_symbols(self):
        with self._lock:
            return set(self._overflow)

    # -- lifecycle ------------------------------------------------------------

    def _default_stream_factory(self):
        from alpaca.data.live import StockDataStream
        return StockDataStream(self._api_key, self._api_secret, feed=self._feed)

    def start(self):
        """Start the reconnect loop on a daemon thread; returns immediately."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop, name="alpaca-mark-stream", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop_event.set()
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _run_loop(self):
        backoff = self._reconnect_min
        while not self._stop_event.is_set():
            try:
                stream = self._stream_factory()
                with self._lock:
                    self._stream = stream
                    resubscribe = sorted(self._subscribed)
                # stop() may have raced the factory (audit 2026-07-18: the
                # zombie stream held Alpaca's one-websocket slot forever).
                if self._stop_event.is_set():
                    with self._lock:
                        self._stream = None
                    try:
                        stream.stop()
                    except Exception:
                        pass
                    return
                if resubscribe:
                    stream.subscribe_quotes(self._async_on_quote, *resubscribe)
                    stream.subscribe_trades(self._async_on_trade, *resubscribe)
                # Re-diff after wiring: a concurrent set_symbols removal must
                # not leave a ghost wire subscription eating the 30-symbol
                # budget (audit 2026-07-18).
                with self._lock:
                    ghosts = sorted(set(resubscribe) - self._subscribed)
                if ghosts:
                    try:
                        stream.unsubscribe_quotes(*ghosts)
                        stream.unsubscribe_trades(*ghosts)
                    except Exception:
                        pass
                backoff = self._reconnect_min
                stream.run()  # blocking until disconnect/stop
                self.record_disconnect("stream run() returned")
            except Exception as exc:
                self.record_disconnect(exc)
            if self._stop_event.wait(backoff):
                return
            backoff = min(backoff * 2.0, self._reconnect_max)


def _as_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _aware(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    return datetime.now(timezone.utc)
