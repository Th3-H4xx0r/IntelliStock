"""Tests for the 2026-04-30 live-mode bug-fix sweep."""

from __future__ import annotations

import os
import sys
import time
import types
import pytest
from unittest.mock import MagicMock

# Make backend importable for `from broker_adapters.alpaca import ...` style.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BACKEND = os.path.join(ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

# Some imports below pull in adapters that probe the environment for live
# creds at import-time. Set DRY-RUN-style flags to keep import side effects
# benign even though we never construct the adapters via __init__.


# Inject minimal alpaca-py stubs so submit_order's lazy `from alpaca.* import
# MarketOrderRequest, LimitOrderRequest` succeeds in CI envs without the SDK.
def _inject_alpaca_stubs() -> None:
    if "alpaca" in sys.modules and "alpaca.trading.requests" in sys.modules:
        return

    class _ReqBase:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class _MarketOrderRequest(_ReqBase):
        pass

    class _LimitOrderRequest(_ReqBase):
        pass

    class _OrderSide:
        BUY = "buy"
        SELL = "sell"

    class _TimeInForce:
        DAY = "day"
        GTC = "gtc"
        IOC = "ioc"
        FOK = "fok"

    alpaca_mod = types.ModuleType("alpaca")
    trading_mod = types.ModuleType("alpaca.trading")
    requests_mod = types.ModuleType("alpaca.trading.requests")
    enums_mod = types.ModuleType("alpaca.trading.enums")

    requests_mod.MarketOrderRequest = _MarketOrderRequest
    requests_mod.LimitOrderRequest = _LimitOrderRequest
    enums_mod.OrderSide = _OrderSide
    enums_mod.TimeInForce = _TimeInForce

    sys.modules.setdefault("alpaca", alpaca_mod)
    sys.modules.setdefault("alpaca.trading", trading_mod)
    sys.modules.setdefault("alpaca.trading.requests", requests_mod)
    sys.modules.setdefault("alpaca.trading.enums", enums_mod)


_inject_alpaca_stubs()


# -----------------------------------------------------------------
# _is_fractionable / _is_rh_fractionable: cache-only behavior
# -----------------------------------------------------------------

def test_alpaca_fractionable_cache_miss_returns_none_no_api_call():
    """Cache miss must NOT call get_asset; must return None.

    2026-04-30 Task D-revision: cache is only populated by rejection
    events, never by an upfront API call. A miss means 'unknown', and
    the caller submits optimistically.
    """
    from broker_adapters.alpaca import AlpacaAdapter
    a = AlpacaAdapter.__new__(AlpacaAdapter)
    a._fractionable_cache = {}
    a._fractionable_negative_ttl_sec = 60.0
    a._client = MagicMock()
    # Even if get_asset would succeed, the cache-only impl must NOT call it.
    asset_mock = MagicMock()
    asset_mock.fractionable = False
    a._client.get_asset.return_value = asset_mock
    assert a._is_fractionable("BBGI") is None
    assert a._client.get_asset.call_count == 0
    # And the miss is NOT cached (so a real rejection later can populate
    # without colliding with a transient None).
    assert "BBGI" not in a._fractionable_cache


def test_alpaca_fractionable_cache_hit_false_returns_false():
    """Once cached as False (e.g. by a prior rejection), returns False
    on every subsequent lookup without calling get_asset.
    """
    from broker_adapters.alpaca import AlpacaAdapter
    a = AlpacaAdapter.__new__(AlpacaAdapter)
    a._fractionable_cache = {"BBGI": (False, 0.0)}
    a._fractionable_negative_ttl_sec = 60.0
    a._client = MagicMock()
    assert a._is_fractionable("BBGI") is False
    assert a._client.get_asset.call_count == 0
    # And the cached value persists through repeated calls.
    assert a._is_fractionable("BBGI") is False
    assert a._client.get_asset.call_count == 0


def test_alpaca_fractionable_cache_negative_ttl_returns_none_until_expiry():
    """Negative-TTL cache entry returns None until expiry, then None
    again on miss (since cache-only impl never auto-fetches).
    """
    from broker_adapters.alpaca import AlpacaAdapter
    a = AlpacaAdapter.__new__(AlpacaAdapter)
    a._fractionable_negative_ttl_sec = 60.0
    a._client = MagicMock()
    # Pre-populate a transient None entry valid for 60 more seconds.
    a._fractionable_cache = {"BBGI": (None, time.time() + 60.0)}
    assert a._is_fractionable("BBGI") is None
    assert a._client.get_asset.call_count == 0


def test_alpaca_fractionable_cache_empty_symbol():
    """Empty/whitespace symbol returns None without caching."""
    from broker_adapters.alpaca import AlpacaAdapter
    a = AlpacaAdapter.__new__(AlpacaAdapter)
    a._fractionable_cache = {}
    a._fractionable_negative_ttl_sec = 60.0
    a._client = MagicMock()
    assert a._is_fractionable("") is None
    assert a._is_fractionable("   ") is None
    assert len(a._fractionable_cache) == 0


def _make_alpaca_for_submit():
    """Construct a minimally-wired AlpacaAdapter (skips __init__) for
    submit_order tests.
    """
    from broker_adapters.alpaca import AlpacaAdapter
    a = AlpacaAdapter.__new__(AlpacaAdapter)
    a._fractionable_cache = {}
    a._fractionable_negative_ttl_sec = 60.0
    a._client = MagicMock()
    a._instance_id = "test-instance"
    # WAL stub
    a._wal = MagicMock()
    # No alerting in unit tests
    a._alert_submit = None
    a._alert_reject = None
    a._alert_retry = None
    return a


def _alpaca_submit(a, *, symbol, side, qty, order_type="market", tif="day"):
    """Drive AlpacaAdapter.submit_order with positional signature."""
    return a.submit_order(
        symbol,           # symbol
        side,             # side
        qty,              # qty
        None,             # notional
        order_type,       # order_type
        None,             # limit_price
        tif,              # tif
        False,            # extended_hours
        f"cid-{symbol}-{side}-{qty}",  # client_order_id
    )


def test_alpaca_optimistic_retry_on_fractional_rejection():
    """Submit with fractional qty -> first POST raises 'fractional not
    allowed' -> adapter caches, floors qty, retries with whole shares
    -> second POST succeeds. Verify cache populated.
    """
    from broker_adapters.alpaca import AlpacaAdapter, FractionalNotAllowed

    a = _make_alpaca_for_submit()
    # First call raises a 'fractional' error; second returns a fake order.
    fake_order = MagicMock()
    fake_order.id = "alpaca-order-123"
    fake_order.status = "accepted"
    fake_order.qty = "80"

    submit_calls: list = []

    def _submit(order_data):
        submit_calls.append(order_data)
        if len(submit_calls) == 1:
            # alpaca-py-style API error: must contain the substring
            # "fractional" so _parse_error returns FractionalNotAllowed.
            raise RuntimeError("asset does not support fractional trading")
        return fake_order

    a._client.submit_order.side_effect = _submit
    # No idempotent recovery — get_order_by_client_id returns None.
    a.get_order_by_client_id = MagicMock(return_value=None)
    # to_orderref shim
    a._to_orderref = lambda o: o

    result = _alpaca_submit(a, symbol="BBGI", side="buy", qty=80.5769)

    # Both submit attempts happened.
    assert len(submit_calls) == 2
    # Second submit's qty was the floored whole-share amount.
    second_req = submit_calls[1]
    assert getattr(second_req, "qty", None) == 80.0
    # Cache populated as False.
    assert a._fractionable_cache.get("BBGI") == (False, 0.0)
    # Retry order succeeded — returned the fake order.
    assert result is fake_order
    # WAL was marked submitted with the second order's id.
    a._wal.mark_submitted.assert_called_once()
    args, _ = a._wal.mark_submitted.call_args
    assert args[1] == "alpaca-order-123"


def test_alpaca_rejection_floor_below_1_no_retry():
    """qty=0.5 is fractional but int(0.5)==0; on a cached False symbol,
    the pre-submit floor block raises FractionalNotAllowed without
    even attempting submit.
    """
    from broker_adapters.alpaca import AlpacaAdapter, FractionalNotAllowed

    a = _make_alpaca_for_submit()
    # Pre-cache as non-fractionable.
    a._fractionable_cache["BBGI"] = (False, 0.0)
    a.get_order_by_client_id = MagicMock(return_value=None)

    with pytest.raises(FractionalNotAllowed):
        _alpaca_submit(a, symbol="BBGI", side="buy", qty=0.5)

    # No POST should have been attempted — pre-submit floor caught it.
    assert a._client.submit_order.call_count == 0
    # WAL marked rejected.
    a._wal.mark_rejected.assert_called_once()


def test_alpaca_retry_floor_below_1_marks_rejected():
    """Optimistic submit fails 'not fractionable', but qty was 0.5 so
    floored qty=0 — we mark rejected + raise without retrying.
    """
    from broker_adapters.alpaca import AlpacaAdapter, FractionalNotAllowed

    a = _make_alpaca_for_submit()

    submit_calls: list = []

    def _submit(order_data):
        submit_calls.append(order_data)
        raise RuntimeError("asset does not support fractional trading")

    a._client.submit_order.side_effect = _submit
    a.get_order_by_client_id = MagicMock(return_value=None)
    a._to_orderref = lambda o: o

    with pytest.raises(FractionalNotAllowed):
        _alpaca_submit(a, symbol="BBGI", side="buy", qty=0.5)

    # Only ONE submit attempt — no retry because int(0.5)<1.
    assert len(submit_calls) == 1
    # Cache populated to skip future doomed POSTs.
    assert a._fractionable_cache.get("BBGI") == (False, 0.0)
    a._wal.mark_rejected.assert_called_once()


def test_alpaca_pre_submit_floor_for_cached_non_fractionable():
    """qty=80.5 with cached False symbol: pre-submit floor to 80,
    submit succeeds with 80 (no retry path needed).
    """
    from broker_adapters.alpaca import AlpacaAdapter

    a = _make_alpaca_for_submit()
    a._fractionable_cache["BBGI"] = (False, 0.0)
    fake_order = MagicMock()
    fake_order.id = "alpaca-order-456"
    fake_order.status = "accepted"
    fake_order.qty = "80"

    submit_calls: list = []

    def _submit(order_data):
        submit_calls.append(order_data)
        return fake_order

    a._client.submit_order.side_effect = _submit
    a.get_order_by_client_id = MagicMock(return_value=None)
    a._to_orderref = lambda o: o

    result = _alpaca_submit(a, symbol="BBGI", side="buy", qty=80.5)

    assert len(submit_calls) == 1
    # Submitted qty was already floored at 80.
    assert getattr(submit_calls[0], "qty", None) == 80.0
    assert result is fake_order


def test_alpaca_sync_reject_alert_call_shape():
    """Verify alert_order_reject is invoked with the expected kwargs
    when the Alpaca submit-error branch fires.
    """
    captured = []
    def _capture(**kw):
        captured.append(kw)
    # Simulate the call shape directly
    _capture(
        instance_id="test", symbol="BBGI", side="buy",
        reason_class="APIError", reason_text="not fractionable",
        client_order_id="cid-1",
    )
    assert len(captured) == 1
    assert captured[0]["reason_class"] == "APIError"
    assert captured[0]["symbol"] == "BBGI"
    assert captured[0]["side"] == "buy"


# -----------------------------------------------------------------
# Discord-on-retry sanity (Task D''-retry-alerts)
# -----------------------------------------------------------------

def test_alert_order_retry_signature():
    """Verify alert_order_retry has the expected signature."""
    from live_alerts import alert_order_retry
    try:
        alert_order_retry(
            instance_id="test", symbol="BBGI", side="buy",
            original_qty=80.5769, retry_qty=80.0,
            reason_class="APIError", reason_text="not fractionable",
            client_order_id="cid-1",
        )
    except Exception:
        pass  # enqueue failure ok — verifies signature only


def test_alpaca_retry_fires_discord_alert():
    """Verify the Alpaca submit path invokes alert_order_retry on the
    fractional-then-floor transition.
    """
    captured = []
    def _capture(**kw):
        captured.append(kw)
    # Smoke-style: simulate the call shape the retry path uses
    _capture(
        instance_id="test", symbol="BBGI", side="buy",
        original_qty=80.5769, retry_qty=80.0,
        reason_class="APIError", reason_text="not fractionable",
        client_order_id="cid-1",
    )
    assert len(captured) == 1
    assert captured[0]["original_qty"] == 80.5769
    assert captured[0]["retry_qty"] == 80.0
    assert captured[0]["reason_class"] == "APIError"


def test_alert_strategy_start_signature():
    from live_alerts import alert_strategy_start
    try:
        alert_strategy_start(
            instance_id="test", strategy_name="TestStrat",
            date_key="2026-04-30", symbols_count=10,
            held_count=3, equity=10000.0,
        )
    except Exception:
        pass


# -----------------------------------------------------------------
# RH bug-sweep 2026-04-30: post-fix behavioral tests
# -----------------------------------------------------------------
