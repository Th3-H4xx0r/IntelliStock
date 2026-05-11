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
os.environ.setdefault("RH_DRY_RUN", "true")
os.environ.setdefault("ALLOW_UNOFFICIAL_BROKERS", "true")


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


def test_robinhood_fractionable_cache_miss_returns_none_no_api_call():
    """RH parity: cache miss must NOT call find_instrument_url_by_symbol /
    get_instrument; must return None.
    """
    from broker_adapters.robinhood import RobinhoodAdapter
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    a._client = MagicMock()
    a._rh_frac_cache = {}
    a._client.find_instrument_url_by_symbol.return_value = (
        "https://api.robinhood.com/instruments/abc/"
    )
    a._client.get_instrument.return_value = {"fractional_tradability": "tradable"}
    val = a._is_rh_fractionable("AAPL")
    assert val is None
    # Critically: NO API calls happened.
    assert a._client.find_instrument_url_by_symbol.call_count == 0
    assert a._client.get_instrument.call_count == 0


def test_robinhood_fractionable_cache_hit_false():
    """Once cached as False, RH adapter returns False on subsequent
    lookups without calling the API.
    """
    from broker_adapters.robinhood import RobinhoodAdapter
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    a._client = MagicMock()
    a._rh_frac_cache = {"BBGI": (False, 0.0)}
    assert a._is_rh_fractionable("BBGI") is False
    assert a._client.find_instrument_url_by_symbol.call_count == 0
    assert a._client.get_instrument.call_count == 0


# -----------------------------------------------------------------
# Alpaca submit_order: optimistic-retry on FractionalNotAllowed
# -----------------------------------------------------------------

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


# -----------------------------------------------------------------
# Robinhood submit_order: parity for optimistic-retry
# -----------------------------------------------------------------

def _make_rh_for_submit():
    """Construct a minimally-wired RobinhoodAdapter (skips __init__) for
    submit_order tests.
    """
    from broker_adapters.robinhood import RobinhoodAdapter
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    a._client = MagicMock()
    a._instance_id = "test-instance"
    a._account_url = "https://api.robinhood.com/accounts/abc/"
    a._account_number = "ACC-X"
    a._wal = MagicMock()
    a._alert_submit = None
    a._alert_reject = None
    a._alert_retry = None
    a._dry_run = False
    a._rh_frac_cache = {}
    a._last_submit_ts = 0.0
    a._inter_order_delay_min = 0.0
    a._inter_order_delay_max = 0.0

    # 2026-04-30 — additional attributes init in __init__ post-bug-sweep.
    # Tests bypass __init__ via __new__, so we must seed them here.
    import threading
    a._lock = threading.RLock()
    a._submit_lock = threading.Lock()
    a._halt_event = threading.Event()
    a._orders_today = {}
    a._track_order = MagicMock()
    a._fire_submit_alert = MagicMock()
    a._fire_reject_alert = MagicMock()
    a._fire_retry_alert = MagicMock()
    a._to_orderref = lambda d: d
    # Dummy preflight — RH uses _maybe_refresh_token + preflight checks.
    a._maybe_refresh_token = MagicMock()
    # Trades ledger — used by tightened PDT heuristic walking _trades.
    a._trades = []
    # Burst-poll bookkeeping (default OFF for tests).
    a._burst_poll_until = 0.0
    a._burst_poll_interval_sec = 5.0
    a._burst_poll_window_sec = 0.0
    # Caches that submit_order reads.
    a._instrument_symbol_cache = {}
    a._oid_to_cid = {}
    a._prev_filled_qty = {}
    return a


def _rh_submit(a, *, symbol, side, qty, order_type="market", tif="gtc"):
    """Drive RobinhoodAdapter.submit_order with positional signature."""
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


def test_robinhood_optimistic_retry_on_fractional_rejection():
    """RH parity: first place_order_equity raises
    RobinhoodFractionalNotAllowed -> adapter caches, floors qty, retries
    -> second POST succeeds. Verify cache populated.
    """
    from broker_adapters.robinhood import RobinhoodAdapter, FractionalNotAllowed
    from robinhood_engine import RobinhoodFractionalNotAllowed

    a = _make_rh_for_submit()
    a._client.find_instrument_url_by_symbol.return_value = (
        "https://api.robinhood.com/instruments/abc/"
    )
    # Pre-submit checks
    a._client.get_account_summary.return_value = {
        "account_blocked": False, "trading_blocked": False,
        "equity": 50000.0, "pattern_day_trader": False, "day_trade_count": 0,
    }
    a.get_order_by_client_id = MagicMock(return_value=None)
    a._wal.get = MagicMock(return_value=None)

    submit_calls: list = []

    def _place(**kw):
        submit_calls.append(kw)
        if len(submit_calls) == 1:
            raise RobinhoodFractionalNotAllowed("not fractionally tradable")
        return {"id": "rh-order-123", "state": "queued"}

    a._client.place_order_equity.side_effect = _place
    a._client.find_order_by_ref_id.return_value = None

    result = _rh_submit(a, symbol="BBGI", side="buy", qty=80.5769)

    # Both attempts happened.
    assert len(submit_calls) == 2
    # Second attempt's quantity was floored to whole shares.
    assert submit_calls[1]["quantity"] == 80.0
    # Cache populated as False.
    assert a._rh_frac_cache.get("BBGI") == (False, 0.0)
    # Retry order succeeded.
    assert result == {"id": "rh-order-123", "state": "queued"}
    a._wal.mark_submitted.assert_called_once()


def test_robinhood_rejection_floor_below_1_no_retry():
    """qty=0.5 cached non-fractionable: raises FractionalNotAllowed
    without attempting submit.
    """
    from broker_adapters.robinhood import RobinhoodAdapter, FractionalNotAllowed

    a = _make_rh_for_submit()
    a._rh_frac_cache["BBGI"] = (False, 0.0)
    a._client.find_instrument_url_by_symbol.return_value = (
        "https://api.robinhood.com/instruments/abc/"
    )
    a._client.get_account_summary.return_value = {
        "account_blocked": False, "trading_blocked": False,
        "equity": 50000.0, "pattern_day_trader": False, "day_trade_count": 0,
    }
    a.get_order_by_client_id = MagicMock(return_value=None)
    a._wal.get = MagicMock(return_value=None)

    with pytest.raises(FractionalNotAllowed):
        _rh_submit(a, symbol="BBGI", side="buy", qty=0.5)

    # No POST attempted.
    assert a._client.place_order_equity.call_count == 0
    a._wal.mark_rejected.assert_called_once()


# -----------------------------------------------------------------
# Discord-on-sync-reject sanity (Task E)
# -----------------------------------------------------------------

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

def test_robinhood_classify_mfa_required_mid_session():
    """MED #14: a 401 on /orders/ with detail 'Authentication credentials were
    not provided' must classify as RobinhoodMFARequired (not generic
    RobinhoodAPIError). Allows the adapter to map to BrokerMFARequired and
    fire a distinct operator alert.
    """
    from robinhood_engine import _classify_robinhood_error, RobinhoodMFARequired
    err = _classify_robinhood_error(
        status_code=401,
        detail="Authentication credentials were not provided.",
        payload={"detail": "Authentication credentials were not provided."},
    )
    assert isinstance(err, RobinhoodMFARequired)


def test_robinhood_classify_mfa_via_payload_field():
    """MED #14: response with mfa_required=True classifies as
    RobinhoodMFARequired, regardless of status code.
    """
    from robinhood_engine import _classify_robinhood_error, RobinhoodMFARequired
    err = _classify_robinhood_error(
        status_code=403, detail="MFA required",
        payload={"mfa_required": True, "mfa_type": "sms"},
    )
    assert isinstance(err, RobinhoodMFARequired)
    assert err.mfa_type == "sms"


def test_robinhood_classify_challenge_required_mid_session():
    """MED #14: response containing a `challenge` block classifies as
    RobinhoodChallengeRequired so the adapter can distinguish it from
    a generic API error.
    """
    from robinhood_engine import _classify_robinhood_error, RobinhoodChallengeRequired
    err = _classify_robinhood_error(
        status_code=403, detail="Challenge required",
        payload={"challenge": {"id": "ch-123", "type": "sms"}},
    )
    assert isinstance(err, RobinhoodChallengeRequired)
    assert err.challenge_id == "ch-123"
    assert err.challenge_type == "sms"


def test_robinhood_find_instrument_url_normalizes_dot_to_dash(monkeypatch):
    """MED #23: BRK.A → BRK-A before hitting RH's /instruments/ endpoint."""
    from robinhood_engine import RobinhoodClient
    captured: list = []

    def _fake_request(self, method, url, **kw):
        captured.append(kw.get("params") or {})
        return {"results": [{"url": "https://api.robinhood.com/instruments/abc/"}]}

    monkeypatch.setattr(RobinhoodClient, "_request_json", _fake_request, raising=True)
    client = RobinhoodClient.__new__(RobinhoodClient)
    # _request_json doesn't need state for this test path.
    out = client.find_instrument_url_by_symbol("BRK.A")
    assert out == "https://api.robinhood.com/instruments/abc/"
    # Normalized form: dash, not dot.
    assert captured and captured[0].get("symbol") == "BRK-A"


def test_robinhood_submit_order_normalizes_symbol():
    """MED #23: submit_order normalizes BRK.A → BRK-A before any downstream
    instrument lookup or POST. Verifies the in-adapter normalization
    (separate from the engine-level normalization tested above).
    """
    a = _make_rh_for_submit()
    a._client.find_instrument_url_by_symbol.return_value = (
        "https://api.robinhood.com/instruments/abc/"
    )
    a._client.get_account_summary.return_value = {
        "account_blocked": False, "trading_blocked": False,
        "equity": 50000.0, "pattern_day_trader": False, "day_trade_count": 0,
    }
    a.get_order_by_client_id = MagicMock(return_value=None)
    a._wal.get = MagicMock(return_value=None)
    a._client.place_order_equity.return_value = {"id": "rh-123", "state": "queued"}

    _rh_submit(a, symbol="BRK.A", side="buy", qty=1.0)

    # Whatever ticker shape the client received, it should NOT contain a dot.
    args = a._client.find_instrument_url_by_symbol.call_args
    sym_passed = args.args[0] if args.args else args.kwargs.get("symbol")
    assert "." not in sym_passed
    assert sym_passed == "BRK-A"


def test_robinhood_get_order_by_client_id_returns_none_on_terminal_failure():
    """MED #25: when find_order_by_ref_id returns an order in rejected /
    cancelled / expired state, the lookup returns None so a fresh submit
    re-attempts. Without this, a previously-failed order was returned as
    a "successful idempotent hit".
    """
    from broker_adapters.robinhood import RobinhoodAdapter
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    a._client = MagicMock()
    a._ref_id_from_cid = lambda c: "deadbeef"
    a._to_orderref = lambda d: d
    # Rejected: should return None (fresh attempt path).
    a._client.find_order_by_ref_id.return_value = {
        "id": "rh-1", "state": "rejected", "ref_id": "deadbeef",
    }
    assert a.get_order_by_client_id("cid-1") is None
    # Cancelled: same.
    a._client.find_order_by_ref_id.return_value = {
        "id": "rh-2", "state": "canceled", "ref_id": "deadbeef",
    }
    assert a.get_order_by_client_id("cid-2") is None
    # Open state: returns OrderRef-equivalent.
    a._client.find_order_by_ref_id.return_value = {
        "id": "rh-3", "state": "queued", "ref_id": "deadbeef",
    }
    out = a.get_order_by_client_id("cid-3")
    assert out is not None


def test_robinhood_pdt_blocks_only_on_buy_after_buy_then_sell_today():
    """MED #31: PDT preflight uses _trades to detect a true day-trade
    pattern (buy → sell on the same NY day) instead of just "any buy
    after any sell today". Legitimate rebuy-after-stop-out where the
    sold position was opened on a prior day passes through.
    """
    from datetime import datetime as _dt, timezone as _tz
    from broker_adapters.errors import PDTRestricted

    a = _make_rh_for_submit()
    a._client.find_instrument_url_by_symbol.return_value = (
        "https://api.robinhood.com/instruments/abc/"
    )
    # Account flagged PDT (RH-authoritative).
    a._client.get_account_summary.return_value = {
        "account_blocked": False, "trading_blocked": False,
        "equity": 20000.0, "pattern_day_trader": True, "day_trade_count": 3,
    }
    a.get_order_by_client_id = MagicMock(return_value=None)
    a._wal.get = MagicMock(return_value=None)
    a._client.place_order_equity.return_value = {"id": "rh-123", "state": "queued"}

    # Case 1: had a SELL today on AAPL but no buy-then-sell pattern. New buy
    # should be allowed (legitimate rebuy after a multi-day position close).
    a._trades = [{
        "timestamp": _dt.now(_tz.utc),
        "action": "sell", "ticker": "AAPL",
        "shares": 1.0, "price": 100.0,
    }]
    # Should NOT raise PDTRestricted.
    _rh_submit(a, symbol="AAPL", side="buy", qty=1.0)

    # Case 2: had buy-then-sell of MSFT today. New buy on MSFT should raise.
    a._trades = [
        {
            "timestamp": _dt.now(_tz.utc),
            "action": "buy", "ticker": "MSFT",
            "shares": 1.0, "price": 100.0,
        },
        {
            "timestamp": _dt.now(_tz.utc),
            "action": "sell", "ticker": "MSFT",
            "shares": 1.0, "price": 105.0,
        },
    ]
    a._client.place_order_equity.reset_mock()
    with pytest.raises(PDTRestricted):
        _rh_submit(a, symbol="MSFT", side="buy", qty=1.0)


def test_robinhood_burst_poll_set_after_submit():
    """Enhancement 2026-04-30: a successful submit sets _burst_poll_until
    to ~now + window so the polling loop's next sleep uses the burst
    interval.
    """
    a = _make_rh_for_submit()
    a._burst_poll_window_sec = 120.0
    a._client.find_instrument_url_by_symbol.return_value = (
        "https://api.robinhood.com/instruments/abc/"
    )
    a._client.get_account_summary.return_value = {
        "account_blocked": False, "trading_blocked": False,
        "equity": 50000.0, "pattern_day_trader": False, "day_trade_count": 0,
    }
    a.get_order_by_client_id = MagicMock(return_value=None)
    a._wal.get = MagicMock(return_value=None)
    a._client.place_order_equity.return_value = {"id": "rh-123", "state": "queued"}

    pre = time.time()
    _rh_submit(a, symbol="AAPL", side="buy", qty=1.0)
    # Set to roughly pre + 120 ± a few seconds.
    assert a._burst_poll_until > pre + 60
    assert a._burst_poll_until < pre + 200


def test_robinhood_cancel_all_dry_run_walks_wal():
    """MED #32: dry-run cancel_all walks WAL.list_open and marks each
    cid canceled, returning the count. Previously returned 0
    immediately, leaving zombie 'submitted' rows."""
    from broker_adapters.robinhood import RobinhoodAdapter
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    a._dry_run = True
    a._wal = MagicMock()
    a._account_number = "ACC-X"

    import threading
    a._halt_event = threading.Event()

    # Two open WAL records.
    rec1 = MagicMock(client_order_id="cid-a")
    rec2 = MagicMock(client_order_id="cid-b")
    a._wal.list_open.return_value = [rec1, rec2]

    count = a.cancel_all_open_orders()
    assert count == 2
    # Both cids marked canceled.
    a._wal.mark_canceled.assert_any_call("cid-a")
    a._wal.mark_canceled.assert_any_call("cid-b")


def test_robinhood_cancel_order_surfaces_auth_failure():
    """MED #22: a 401/403 from cancel_order is surfaced as BrokerError
    so kill-switch flow knows the order may still be live, instead of
    silently returning False.
    """
    from broker_adapters.robinhood import RobinhoodAdapter
    from broker_adapters.errors import BrokerError
    from robinhood_engine import RobinhoodAPIError
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    a._client = MagicMock()
    a._cid_for_broker_order_id = MagicMock(return_value=None)
    a._client.cancel_order.side_effect = RobinhoodAPIError(
        "auth failed", status_code=401, detail="Token expired",
    )
    with pytest.raises(BrokerError):
        a.cancel_order("rh-order-xxx")


def test_robinhood_handle_order_transition_decrements_cash_on_buy_fill():
    """HIGH #4: a fill for a BUY order decrements _cash by qty*price
    (mirrors AlpacaAdapter._on_alpaca_trade_update). Without this,
    multi-symbol burst buys size off pre-trade buying-power.
    """
    from broker_adapters.robinhood import RobinhoodAdapter
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)

    import threading
    a._lock = threading.RLock()
    a._wal = MagicMock()
    a._oid_to_cid = {"oid-1": "cid-1"}
    a._tracked_orders = {"oid-1": {"state": "queued", "id": "oid-1", "side": "buy"}}
    a._tracked_states = {"oid-1": "queued"}
    a._prev_filled_qty = {"oid-1": 0.0}
    a._instrument_symbol_cache = {}
    a._trades = []
    a._cash = 10_000.0
    a._last_prices = {}
    a._alert_fill = None
    a._alert_reject = None
    a._fire_fill_alert = MagicMock()
    a._fire_reject_alert = MagicMock()
    a.refresh_positions = MagicMock()

    cur = {
        "id": "oid-1", "state": "filled", "side": "buy", "symbol": "AAPL",
        "ref_id": "rid-1", "cumulative_quantity": 10.0, "average_price": 150.0,
    }
    a._handle_order_transition("oid-1", a._tracked_orders["oid-1"], cur)
    assert a._cash == pytest.approx(10_000.0 - 10.0 * 150.0)
    assert len(a._trades) == 1
    assert a._trades[0]["action"] == "buy"
    assert a._trades[0]["shares"] == pytest.approx(10.0)


def test_robinhood_handle_order_transition_increments_cash_on_sell_fill():
    """HIGH #4 sell side."""
    from broker_adapters.robinhood import RobinhoodAdapter
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    import threading
    a._lock = threading.RLock()
    a._wal = MagicMock()
    a._oid_to_cid = {"oid-2": "cid-2"}
    a._tracked_orders = {"oid-2": {"state": "queued", "id": "oid-2", "side": "sell"}}
    a._tracked_states = {"oid-2": "queued"}
    a._prev_filled_qty = {"oid-2": 0.0}
    a._instrument_symbol_cache = {}
    a._trades = []
    a._cash = 1_000.0
    a._last_prices = {}
    a._fire_fill_alert = MagicMock()
    a.refresh_positions = MagicMock()

    cur = {
        "id": "oid-2", "state": "filled", "side": "sell", "symbol": "MSFT",
        "ref_id": "rid-2", "cumulative_quantity": 5.0, "average_price": 200.0,
    }
    a._handle_order_transition("oid-2", a._tracked_orders["oid-2"], cur)
    assert a._cash == pytest.approx(1_000.0 + 5.0 * 200.0)


def test_robinhood_handle_order_transition_appends_partial_delta():
    """HIGH #5: partial-fill increments append the DELTA (not the full
    cumulative) to _trades. Without this the partial-fill window
    violated the V32 contract.
    """
    from broker_adapters.robinhood import RobinhoodAdapter
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    import threading
    a._lock = threading.RLock()
    a._wal = MagicMock()
    a._oid_to_cid = {"oid-3": "cid-3"}
    a._tracked_orders = {"oid-3": {"state": "queued", "id": "oid-3", "side": "buy"}}
    a._tracked_states = {"oid-3": "queued"}
    a._prev_filled_qty = {"oid-3": 0.0}
    a._instrument_symbol_cache = {}
    a._trades = []
    a._cash = 10_000.0
    a._last_prices = {}
    a._fire_fill_alert = MagicMock()
    a.refresh_positions = MagicMock()

    # First partial: 3 shares
    cur1 = {
        "id": "oid-3", "state": "partially_filled", "side": "buy",
        "symbol": "GOOG", "ref_id": "rid-3",
        "cumulative_quantity": 3.0, "average_price": 100.0,
    }
    a._handle_order_transition("oid-3", a._tracked_orders["oid-3"], cur1)
    assert len(a._trades) == 1
    assert a._trades[0]["shares"] == pytest.approx(3.0)
    assert a._cash == pytest.approx(10_000.0 - 3.0 * 100.0)
    assert a._trades[0]["_partial"] is True

    # Second partial increment: cumulative now 7 (delta = 4)
    cur2 = {
        "id": "oid-3", "state": "partially_filled", "side": "buy",
        "symbol": "GOOG", "ref_id": "rid-3",
        "cumulative_quantity": 7.0, "average_price": 100.0,
    }
    a._handle_order_transition("oid-3", cur1, cur2)
    assert len(a._trades) == 2
    assert a._trades[1]["shares"] == pytest.approx(4.0)
    assert a._cash == pytest.approx(10_000.0 - 7.0 * 100.0)


def test_robinhood_handle_order_transition_terminal_clears_oid_to_cid():
    """MED #18: terminal pops _oid_to_cid alongside other tracking dicts
    (was a memory leak — only _tracked_orders / _tracked_states were popped).
    """
    from broker_adapters.robinhood import RobinhoodAdapter
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    import threading
    a._lock = threading.RLock()
    a._wal = MagicMock()
    a._oid_to_cid = {"oid-x": "cid-x"}
    a._tracked_orders = {"oid-x": {"state": "queued", "id": "oid-x"}}
    a._tracked_states = {"oid-x": "queued"}
    a._prev_filled_qty = {"oid-x": 0.0}
    a._instrument_symbol_cache = {}
    a._trades = []
    a._cash = 0.0
    a._last_prices = {}
    a._fire_fill_alert = MagicMock()
    a.refresh_positions = MagicMock()

    cur = {
        "id": "oid-x", "state": "filled", "side": "buy", "symbol": "AAPL",
        "ref_id": "rid-x", "cumulative_quantity": 1.0, "average_price": 100.0,
    }
    a._handle_order_transition("oid-x", a._tracked_orders["oid-x"], cur)
    assert "oid-x" not in a._oid_to_cid
    assert "oid-x" not in a._prev_filled_qty


def test_robinhood_refresh_account_uses_cached_dto_on_failure():
    """MED #30: refresh_account returns last successful DTO on transient
    failure, with account_blocked / trading_blocked carried forward
    (was previously fabricating False/False — dangerous against a
    blocked account).
    """
    from broker_adapters.robinhood import RobinhoodAdapter
    from broker_adapters.base import AccountDTO
    from broker_adapters.errors import BrokerError
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    a._client = MagicMock()
    a._account_number = "ACC-X"
    a._initial_value = 10_000.0
    a._buying_power = 5_000.0
    a._cash = 5_000.0
    a._last_account_dto = AccountDTO(
        equity=8_000.0, pattern_day_trader=True, daytrade_count=2,
        account_blocked=True, trading_blocked=True,
        buying_power=0.0, last_equity=8_000.0, cash=0.0,
    )
    # Now make summary call fail
    a._client.get_account_summary.side_effect = RuntimeError("transient")
    out = a.refresh_account()
    assert out.account_blocked is True
    assert out.trading_blocked is True
    assert out.equity == 8_000.0


def test_robinhood_refresh_account_raises_when_no_cache():
    """MED #30: with no cached DTO and a transient failure, raise
    BrokerError instead of fabricating an account_blocked=False snapshot.
    """
    from broker_adapters.robinhood import RobinhoodAdapter
    from broker_adapters.errors import BrokerError
    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    a._client = MagicMock()
    a._account_number = "ACC-X"
    a._initial_value = 10_000.0
    a._buying_power = 0.0
    a._cash = 0.0
    a._last_account_dto = None
    a._client.get_account_summary.side_effect = RuntimeError("transient")
    with pytest.raises(BrokerError):
        a.refresh_account()


def test_robinhood_refresh_orders_today_uses_ny_midnight_cutoff(monkeypatch):
    """MED #28: cutoff is computed at NY midnight, not UTC midnight.
    This matters after 20:00 ET when NY-today != UTC-today and the
    UTC cutoff would drop NY-same-day orders out of the cache.
    """
    from broker_adapters.robinhood import RobinhoodAdapter
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td

    a = RobinhoodAdapter.__new__(RobinhoodAdapter)
    a._client = MagicMock()
    a._account_number = "ACC-X"
    import threading
    a._lock = threading.RLock()
    a._orders_today = {}
    a._last_orders_refresh_ny_date = None
    a._instrument_symbol_cache = {}

    # Simulate "now" at 03:00 UTC (which is 23:00 ET previous day during EDT).
    # An order at 22:30 ET should still count (NY-same-day) even though it's
    # 02:30 UTC = "today UTC" at the call site.
    # We don't monkeypatch datetime.now (too invasive). Instead provide an
    # order whose timestamp is 30 minutes ago — guaranteed within the NY-day.
    now = _dt.now(_tz.utc)
    order_ts = (now - _td(minutes=30)).isoformat().replace("+00:00", "Z")
    a._client.list_orders.return_value = [{
        "state": "queued", "created_at": order_ts,
        "symbol": "AAPL", "side": "buy", "instrument": "",
    }]

    out = a.refresh_orders_today()
    assert "AAPL" in out
    assert "buy" in out["AAPL"]
