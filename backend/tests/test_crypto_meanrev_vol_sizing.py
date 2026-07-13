"""Volatility-scaled position sizing for crypto MeanRev.

Task 1 covers the shared sizing helper (equal-weight default preserved for
Connors; vol path over-weights higher-ATR coins, bounded + fallback). Task 2
covers run_once computing ATR% and honoring the ``sizing`` config.
"""
from strategies.crypto.meanrev import _apply_equal_weight_sizing


class _PE:
    def __init__(self, pv):
        self._pv = pv

    def get_portfolio_value(self, prices):
        return self._pv


def _sizes(result):
    return result.get("_nexus_position_sizes", {})


# ---- Task 1: helper ------------------------------------------------------
def test_equal_default_matches_pv_over_topk():
    # No atr_pct_by / sizing='equal' -> each buy gets pv/top_k (the Connors path).
    result = {"BTC/USD": 1, "ETH/USD": 1}
    _apply_equal_weight_sizing(result, {"BTC/USD": 100, "ETH/USD": 50}, _PE(10000.0), 2)
    s = _sizes(result)
    assert s["BTC/USD"]["buy_cash"] == 5000.0
    assert s["ETH/USD"]["buy_cash"] == 5000.0
    assert s["BTC/USD"]["asset_class"] == "crypto"
    assert s["_cash_reserve_floor_pct"] == 0.0 and s["_buy_price_floor"] == 0.0


def test_vol_overweights_higher_atr_coin():
    # ETH has 2x BTC's ATR% -> ETH gets more buy_cash than BTC.
    result = {"BTC/USD": 1, "ETH/USD": 1}
    atr = {"BTC/USD": 0.01, "ETH/USD": 0.02}
    _apply_equal_weight_sizing(result, {"BTC/USD": 100, "ETH/USD": 50}, _PE(10000.0), 2,
                               atr_pct_by=atr, sizing="vol")
    s = _sizes(result)
    assert s["ETH/USD"]["buy_cash"] > s["BTC/USD"]["buy_cash"]


def test_vol_weight_is_clamped():
    # Extreme ATR ratios are bounded to [0.6, 1.6] * pv/top_k.
    result = {"BTC/USD": 1, "ETH/USD": 1}
    atr = {"BTC/USD": 0.001, "ETH/USD": 0.5}  # 500x ratio
    _apply_equal_weight_sizing(result, {"BTC/USD": 100, "ETH/USD": 50}, _PE(10000.0), 2,
                               atr_pct_by=atr, sizing="vol")
    s = _sizes(result)
    per = 10000.0 / 2
    assert 0.6 * per - 0.01 <= s["BTC/USD"]["buy_cash"] <= 1.6 * per + 0.01
    assert 0.6 * per - 0.01 <= s["ETH/USD"]["buy_cash"] <= 1.6 * per + 0.01


def test_vol_missing_atr_falls_back_to_equal():
    result = {"BTC/USD": 1, "ETH/USD": 1}
    atr = {"BTC/USD": 0.01}  # ETH missing -> equal fallback
    _apply_equal_weight_sizing(result, {"BTC/USD": 100, "ETH/USD": 50}, _PE(10000.0), 2,
                               atr_pct_by=atr, sizing="vol")
    s = _sizes(result)
    assert s["ETH/USD"]["buy_cash"] == 5000.0


def test_sells_still_emit_full_fraction():
    result = {"BTC/USD": -1, "ETH/USD": 1}
    atr = {"BTC/USD": 0.01, "ETH/USD": 0.02}
    _apply_equal_weight_sizing(result, {"BTC/USD": 100, "ETH/USD": 50}, _PE(10000.0), 2,
                               atr_pct_by=atr, sizing="vol")
    s = _sizes(result)
    assert s["BTC/USD"]["sell_fraction"] == 1.0
