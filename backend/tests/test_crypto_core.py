"""Unit tests for backend/strategies/crypto/core.py.

Pure math / URL / order-shape tests plus one monkeypatched fetch (no network)
and a real-scheduler validation that a crypto config schedules a weekend wake
(proving 24/7). No ``alpaca`` import anywhere.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from strategies.crypto import core


# --- fee model -------------------------------------------------------------

def test_crypto_fees_tier1():
    assert core.CRYPTO_FEES == {"maker": 0.0015, "taker": 0.0025}


def test_is_crypto_instance():
    assert core.is_crypto_instance({"kind": "crypto"}) is True
    assert core.is_crypto_instance({"kind": "kalshi"}) is False
    assert core.is_crypto_instance({}) is False


def test_round_trip_fee_taker_and_maker():
    assert abs(core.round_trip_fee(False, False) - 0.005) < 1e-9
    assert abs(core.round_trip_fee(True, True) - 0.003) < 1e-9
    # mixed leg = one maker + one taker
    assert abs(core.round_trip_fee(True, False) - (0.0015 + 0.0025)) < 1e-9


def test_min_edge_to_trade():
    # taker round-trip alone is 0.005; edge must clear at least that.
    assert core.min_edge_to_trade(False) >= 0.005
    # maker edge is strictly cheaper than taker edge.
    assert core.min_edge_to_trade(True) < core.min_edge_to_trade(False)


# --- scheduler config (24/7) ----------------------------------------------

def test_scheduler_config_is_24_7_and_band_paced():
    hi = core.crypto_scheduler_config("high")
    assert hi["weekdays_only"] is False
    assert hi["open_pt_min"] == 0
    assert hi["close_pt_min"] == 1440
    assert hi["full_anchor_pt_min"] == 0
    assert hi["monitor_interval_min"] == 5
    assert core.crypto_scheduler_config("medium")["monitor_interval_min"] == 15
    assert core.crypto_scheduler_config("low")["monitor_interval_min"] == 60
    # unknown band falls back to medium
    assert core.crypto_scheduler_config("bogus")["monitor_interval_min"] == 15


def test_scheduler_accepts_crypto_config_weekend():
    """The real scheduler must schedule a wake on a Saturday (crypto is 24/7)."""
    import scheduler

    cfg = core.crypto_scheduler_config("medium")
    sat = datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc)  # a Saturday
    nxt, mode = scheduler.get_next_wake(sat, marker=None, config=cfg)
    assert nxt > sat  # a weekend wake was scheduled, not skipped to Monday


# --- bars URL / params -----------------------------------------------------

def test_crypto_bars_url_is_v1beta3_not_stocks():
    url = core.crypto_bars_url("1Min")
    assert url == "https://data.alpaca.markets/v1beta3/crypto/us/bars"
    assert "/stocks/" not in url
    assert "v1beta3" in url


def test_crypto_bars_params_comma_symbols_no_feed():
    params = core.crypto_bars_params(["BTC/USD", "ETH/USD"], "1Min", None, None, 100)
    assert params["symbols"] == "BTC/USD,ETH/USD"
    assert params["timeframe"] == "1Min"
    assert params["limit"] == 100
    assert "feed" not in params
    assert "start" not in params and "end" not in params


def test_crypto_bars_params_includes_start_end_when_given():
    params = core.crypto_bars_params(["BTC/USD"], "1Day", "2026-01-01", "2026-02-01", 500)
    assert params["start"] == "2026-01-01"
    assert params["end"] == "2026-02-01"
    assert params["symbols"] == "BTC/USD"


def test_fetch_crypto_bars_monkeypatched_no_network():
    """fetch_crypto_bars returns the inner ``bars`` mapping via injected http_get."""
    captured = {}

    class _Resp:
        def json(self):
            return {"bars": {"BTC/USD": [{"c": 100.0, "v": 5.0}]}}

    def fake_get(url, params=None, headers=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _Resp()

    bars = core.fetch_crypto_bars(
        ["BTC/USD"], "1Min", "KEY", "SECRET", http_get=fake_get,
    )
    assert bars == {"BTC/USD": [{"c": 100.0, "v": 5.0}]}
    assert captured["url"].endswith("/v1beta3/crypto/us/bars")
    assert captured["params"]["symbols"] == "BTC/USD"
    assert captured["headers"]["APCA-API-KEY-ID"] == "KEY"
    assert captured["headers"]["APCA-API-SECRET-KEY"] == "SECRET"
    assert "feed" not in captured["params"]


# --- order builder ---------------------------------------------------------

def test_build_crypto_order_maker_buy_rests_below():
    o = core.build_crypto_order("BTC/USD", "buy", 0.5, prefer_maker=True, last_price=100.0)
    assert o["order_type"] == "limit"
    assert o["limit_price"] is not None and o["limit_price"] < 100.0
    assert o["tif"] == "gtc"
    assert o["extended_hours"] is False


def test_build_crypto_order_maker_sell_rests_above():
    o = core.build_crypto_order("ETH/USD", "sell", 1.0, prefer_maker=True, last_price=200.0)
    assert o["order_type"] == "limit"
    assert o["limit_price"] > 200.0
    assert o["tif"] == "gtc"
    assert o["extended_hours"] is False


def test_build_crypto_order_taker_is_marketable():
    o = core.build_crypto_order("BTC/USD", "buy", 0.5, prefer_maker=False, last_price=100.0)
    assert o["order_type"] == "market"
    assert o["limit_price"] is None
    assert o["tif"] == "gtc"
    assert o["extended_hours"] is False


def test_build_crypto_order_maker_without_price_falls_back_to_market():
    o = core.build_crypto_order("BTC/USD", "buy", 0.5, prefer_maker=True, last_price=None)
    assert o["order_type"] == "market"
    assert o["limit_price"] is None
    assert o["tif"] == "gtc"


# --- sizing ----------------------------------------------------------------

def test_vol_target_size_caps_notional():
    # Tiny recent vol wants huge exposure -> must cap at max_frac of equity.
    qty = core.vol_target_size(10_000.0, 100.0, recent_vol=0.0001, target_vol=0.02, max_frac=0.25)
    assert abs(qty - (0.25 * 10_000.0 / 100.0)) < 1e-9  # == 25.0


def test_vol_target_size_scales_with_vol():
    # recent_vol == target_vol -> full (uncapped) fraction of 1.0*equity is
    # still capped by max_frac; use a vol that lands below the cap.
    qty = core.vol_target_size(10_000.0, 100.0, recent_vol=0.20, target_vol=0.02, max_frac=0.25)
    # frac = 0.02/0.20 = 0.1 -> notional 1000 -> qty 10
    assert abs(qty - 10.0) < 1e-9


def test_vol_target_size_guards_zero():
    assert core.vol_target_size(10_000.0, 0.0, 0.02) == 0.0
    assert core.vol_target_size(0.0, 100.0, 0.02) == 0.0
    # zero recent vol -> falls back to max_frac, not a div-by-zero.
    qty = core.vol_target_size(10_000.0, 100.0, recent_vol=0.0, max_frac=0.25)
    assert abs(qty - 25.0) < 1e-9


def test_risk_off_targets_all_zero():
    t = core.risk_off_targets(["BTC/USD", "ETH/USD"])
    assert t == {"BTC/USD": 0.0, "ETH/USD": 0.0}


# --- shared strategy helpers (consolidated single source of truth) ---------

def test_series_bar_field_to_array():
    import numpy as np

    bars = [{"c": 1.0}, {"c": 2.0}, {"c": None}, {}]
    arr = core.series(bars, "c")
    assert isinstance(arr, np.ndarray)
    assert list(arr) == [1.0, 2.0, 0.0, 0.0]  # missing/None -> 0.0
    assert list(core.series([], "c")) == []


def test_position_qty_matches_slash_and_slashless_keys():
    # Alpaca may key crypto positions with OR without the slash.
    assert core.position_qty({"BTC/USD": 3}, "BTC/USD") == 3.0
    assert core.position_qty({"BTCUSD": 4}, "BTC/USD") == 4.0   # slash-less key
    assert core.position_qty({}, "BTC/USD") == 0.0
    assert core.position_qty({"BTC/USD": None}, "BTC/USD") == 0.0
    assert core.position_qty(None, "BTC/USD") == 0.0


class _PF:
    def __init__(self, positions):
        self._p = positions

    def get_positions(self):
        return dict(self._p)


def test_held_symbols_detects_both_key_forms_and_guards():
    held = core.held_symbols(_PF({"BTC/USD": 2, "ETHUSD": 1, "SOL/USD": 0}), ["BTC/USD", "ETH/USD", "SOL/USD"])
    assert held == {"BTC/USD", "ETH/USD"}  # SOL flat (0) excluded; ETH slash-less matched
    assert core.held_symbols(None, ["BTC/USD"]) == set()


# --- auto-discovery wiring (robust fallback) -------------------------------

def test_discover_universe_without_creds_returns_empty():
    # No creds -> short-circuit to [] so the caller falls back to its majors.
    assert core.discover_universe("medium", 5, {}, "1Hour") == []
    assert core.discover_universe("medium", 5, {"alpaca_key": "K"}, "1Hour") == []


def test_discover_universe_swallows_provider_errors():
    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    out = core.discover_universe(
        "medium", 5, {"alpaca_key": "K", "alpaca_secret": "S"}, "1Hour", http_get=boom,
    )
    assert out == []  # never raises


def test_discover_universe_ranks_via_injected_http_get():
    """End-to-end wiring: injected http_get serves assets + bars, discovery ranks."""
    assets = [
        {"symbol": "BTC/USD", "tradable": True, "status": "active"},
        {"symbol": "ETH/USD", "tradable": True, "status": "active"},
    ]

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, params=None, headers=None):
        if "/v2/assets" in url:
            return _Resp(assets)
        return _Resp({"bars": {
            "BTC/USD": [{"c": 100.0 + i, "v": 10.0} for i in range(6)],
            "ETH/USD": [{"c": 50.0 + i, "v": 20.0} for i in range(6)],
        }})

    out = core.discover_universe(
        "medium", 5, {"alpaca_key": "K", "alpaca_secret": "S"}, "1Hour", http_get=fake_get,
    )
    assert set(out) == {"BTC/USD", "ETH/USD"}
