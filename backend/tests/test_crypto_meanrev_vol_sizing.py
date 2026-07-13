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


# ---- Task 2: run_once computes ATR% + honors the sizing config -----------
from strategies.crypto.meanrev import Meanrev  # noqa: E402


class _PE2:
    def __init__(self, cash=10000.0):
        self._cash = cash
        self.positions = {}

    def get_portfolio_value(self, prices):
        return self._cash

    def get_positions(self):
        return {}


def _synth_bars(n, base, vol, dip_last=True):
    """Deterministic OHLC: gentle uptrend (close > SMA200 -> regime OK) with a
    drop over the last few bars (RSI < 35 -> oversold). ``vol`` sets the high/low
    spread so ATR% differs between coins."""
    bars = []
    px = base
    for i in range(n):
        px = px * (1.0 + 0.0008)
        o = px
        c = px
        if dip_last and i >= n - 6:
            c = px * (1 - 0.04)
        hi = max(o, c) * (1 + vol)
        lo = min(o, c) * (1 - vol)
        bars.append({"t": f"2026-01-{(i % 28) + 1:02d}T{i % 24:02d}:00:00Z",
                     "o": o, "h": hi, "l": lo, "c": c, "v": 1.0})
    return bars


def _run(sizing):
    n = 260
    data = {"BTC/USD": _synth_bars(n, 100.0, 0.005),   # low vol
            "ETH/USD": _synth_bars(n, 100.0, 0.03)}    # high vol
    prices = {s: data[s][-1]["c"] for s in data}
    strat = Meanrev()
    return strat.run_once(
        symbols=["BTC/USD", "ETH/USD"], prices=prices, current_time=None,
        config={"band": "low", "top_k": 2, "regime_ma": 200, "rsi_buy": 35,
                "rsi_exit": 55, "sizing": sizing},
        conditions={}, data=data, portfolio_emulator=_PE2(), mode="LIVE")


def test_run_once_vol_sizing_prefers_higher_vol_coin():
    res = _run("vol")
    sizes = res.get("_nexus_position_sizes", {})
    buys = {s: v for s, v in res.items()
            if isinstance(s, str) and not s.startswith("_") and v == 1}
    assert set(buys) == {"BTC/USD", "ETH/USD"}, res
    assert sizes["ETH/USD"]["buy_cash"] > sizes["BTC/USD"]["buy_cash"]


def test_run_once_equal_sizing_is_flat():
    res = _run("equal")
    sizes = res.get("_nexus_position_sizes", {})
    assert sizes["BTC/USD"]["buy_cash"] == sizes["ETH/USD"]["buy_cash"]


def test_run_once_returns_only_int_signals():
    # contract: sym -> 1|0|-1, never a float size on the top-level keys.
    res = _run("vol")
    for k, v in res.items():
        if isinstance(k, str) and not k.startswith("_"):
            assert v in (1, 0, -1)
