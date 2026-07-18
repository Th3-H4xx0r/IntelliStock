"""Unit tests for backend/strategies/crypto/discovery.py.

Pure ranking on canned bars — no network, no ``alpaca``. Covers pair
validation, tradable-universe filtering, band-specific volatility weighting, and
point-in-time (``as_of`` changes the ranking, proving no lookahead).
"""

from __future__ import annotations

import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from strategies.crypto import discovery


def _bars(closes, volume_dollar=1000.0):
    """Build OHLCV bars; volume chosen so close*volume == volume_dollar each bar."""
    out = []
    for i, c in enumerate(closes):
        out.append({
            "o": c, "h": c * 1.001, "l": c * 0.999, "c": c,
            "v": (volume_dollar / c) if c else 0.0, "t": i,
        })
    return out


# --- validators ------------------------------------------------------------

def test_is_valid_crypto_pair():
    assert discovery.is_valid_crypto_pair("BTC/USD") is True
    assert discovery.is_valid_crypto_pair("SHIB/USDT") is True
    assert discovery.is_valid_crypto_pair("AAPL") is False
    assert discovery.is_valid_crypto_pair("BTC-USD") is False
    assert discovery.is_valid_crypto_pair("") is False


def test_is_usd_pair():
    assert discovery.is_usd_pair("BTC/USD") is True
    assert discovery.is_usd_pair("ETH/USDT") is False  # quoted in USDT, not USD
    assert discovery.is_usd_pair("AAPL") is False


# --- tradable universe -----------------------------------------------------

def test_list_tradable_pairs_filters():
    assets = [
        {"symbol": "BTC/USD", "tradable": True, "status": "active"},
        {"symbol": "ETH/USD", "tradable": True, "status": "active"},
        {"symbol": "DOGE/USD", "tradable": False, "status": "active"},   # not tradable
        {"symbol": "XRP/USD", "tradable": True, "status": "inactive"},   # inactive
        {"symbol": "ETH/USDT", "tradable": True, "status": "active"},    # non-USD quote
        {"symbol": "AAPL", "tradable": True, "status": "active"},        # equity, not a pair
    ]
    pairs = discovery.list_tradable_pairs(lambda: assets)
    assert pairs == ["BTC/USD", "ETH/USD"]


# --- ranking ---------------------------------------------------------------

def test_rank_universe_band_volatility_weighting():
    """Same liquidity + net momentum, different vol: fast favours volatile,
    allocator favours steady."""
    vola = _bars([100, 90, 120, 95, 110])       # net +10%, high vol
    steady = _bars([100, 102, 104, 108, 110])   # net +10%, low vol
    bars = {"VOLA/USD": vola, "STEADY/USD": steady}

    high = discovery.rank_universe(bars, "high")
    assert high[0][0] == "VOLA/USD"   # fast band rewards volatility

    low = discovery.rank_universe(bars, "low")
    assert low[0][0] == "STEADY/USD"  # allocator band penalises volatility


def test_rank_universe_liquidity_and_momentum():
    """Higher dollar-volume + higher momentum should rank first."""
    leader = _bars([100, 105, 112, 120, 130], volume_dollar=5000.0)  # strong up, liquid
    laggard = _bars([100, 100, 100, 100, 100], volume_dollar=500.0)  # flat, thin
    ranked = discovery.rank_universe({"LEAD/USD": leader, "LAG/USD": laggard}, "medium")
    assert ranked[0][0] == "LEAD/USD"
    assert ranked[-1][0] == "LAG/USD"


def test_rank_universe_deterministic():
    bars = {
        "AAA/USD": _bars([100, 101, 103, 106, 110]),
        "BBB/USD": _bars([100, 100, 101, 101, 102]),
    }
    assert discovery.rank_universe(bars, "medium") == discovery.rank_universe(bars, "medium")


# --- discover (point-in-time) ---------------------------------------------

# EARLY spikes then decays; UP grinds up steadily. Their momentum leader flips
# between an early and a late as_of — the point-in-time proof.
_SERIES = {
    "UP/USD":    [100, 101, 102, 110, 120, 130],   # +2% by t2, +30% by t5
    "EARLY/USD": [100, 140, 138, 130, 120, 110],   # +38% by t2, +10% by t5
    "FLAT/USD":  [100, 100, 100, 100, 100, 100],   # 0 always
}


def _assets_provider():
    return [{"symbol": s, "tradable": True, "status": "active"} for s in _SERIES]


def _bars_provider(symbols, as_of):
    """Point-in-time: only bars with t <= as_of (as_of None => all bars)."""
    cutoff = len(next(iter(_SERIES.values()))) - 1 if as_of is None else as_of
    out = {}
    for sym in symbols:
        closes = _SERIES[sym][: cutoff + 1]
        out[sym] = _bars(closes)
    return out


def test_discover_returns_top_k_and_is_deterministic():
    # "high" band favours movers, so the two moving names take both slots and
    # the flat name is excluded.
    top = discovery.discover("high", 2, _assets_provider, _bars_provider, as_of=5)
    assert len(top) == 2
    assert discovery.discover("high", 2, _assets_provider, _bars_provider, as_of=5) == top
    assert "FLAT/USD" not in top  # flat name never beats two movers in a fast band


def test_discover_point_in_time_changes_ranking():
    early = discovery.discover("medium", 1, _assets_provider, _bars_provider, as_of=2)
    late = discovery.discover("medium", 1, _assets_provider, _bars_provider, as_of=5)
    assert early == ["EARLY/USD"]  # EARLY leads on t0..t2 momentum
    assert late == ["UP/USD"]      # UP overtakes by t5
    assert early != late           # ranking is genuinely point-in-time
