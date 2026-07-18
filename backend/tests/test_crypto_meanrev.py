"""Unit tests for the regime-filtered RSI mean-reversion crypto strategy.

Covers: dip-buy only in-regime (no falling knives), top_k slot cap + most-oversold
ranking, equal-weight buy_cash sizing, exit on RSI recovery, and — the key
validated behavior — that a held dip is NOT sold merely on a regime break.
"""
import numpy as np

from strategies.crypto.meanrev import Meanrev, MeanRev


def _bars(closes):
    """OHLCV bar dicts from a close series (h/l bracket close; volume constant)."""
    return [{"t": i, "o": c, "h": c * 1.001, "l": c * 0.999, "c": float(c), "v": 1.0}
            for i, c in enumerate(closes)]


def _uptrend_then_dip(n=230, dip_bars=16):
    """Rising ramp (close well above its SMA200) ending in a sharp multi-bar drop
    -> low RSI while still in-regime. A 'healthy dip'."""
    up = np.linspace(100.0, 200.0, n - dip_bars)
    dip = np.linspace(200.0, 176.0, dip_bars)
    return np.concatenate([up, dip])


def _downtrend_dip(n=230):
    """Falling ramp -> close below SMA200 with low RSI. A 'falling knife'."""
    return np.linspace(200.0, 96.0, n)


def _recovered(n=230, up_bars=16):
    """A prior dip that has since rallied hard -> RSI high (>55), in-regime."""
    base = np.linspace(100.0, 180.0, n - up_bars)
    rally = np.linspace(180.0, 205.0, up_bars)
    return np.concatenate([base, rally])


class _StubEmu:
    def __init__(self, positions, value=10_000.0):
        self._pos = positions
        self._val = value

    def get_positions(self):
        return dict(self._pos)

    def get_portfolio_value(self, prices):
        return self._val


def _run(strat, data, positions=None, prices=None, config=None):
    emu = _StubEmu(positions or {})
    prices = prices or {s: data[s][-1]["c"] for s in data}
    return strat.run_once(symbols=list(data.keys()), prices=prices, current_time=None,
                          config=config or {}, conditions={}, data=data, portfolio_emulator=emu,
                          mode="LIVE")


def test_class_alias():
    assert MeanRev is Meanrev


def test_buys_oversold_in_regime_not_falling_knives():
    data = {
        "BTC/USD": _bars(_uptrend_then_dip()),   # healthy dip -> buy
        "ETH/USD": _bars(_downtrend_dip()),      # falling knife -> skip
    }
    out = _run(Meanrev(), data)
    assert out.get("BTC/USD") == 1
    assert out.get("ETH/USD") == 0            # below regime MA -> never bought


def test_top_k_caps_positions_and_sizes_equal_weight():
    # three healthy dips, top_k=2 -> only the 2 most-oversold are bought.
    # sizing="equal" pins each slot to pv/top_k (the vol default reweights by ATR%,
    # covered in test_crypto_meanrev_vol_sizing.py).
    data = {s: _bars(_uptrend_then_dip(dip_bars=d))
            for s, d in [("BTC/USD", 20), ("ETH/USD", 16), ("SOL/USD", 12)]}
    out = _run(Meanrev(), data, config={"sizing": "equal"})
    buys = [s for s, v in out.items() if v == 1]
    assert len(buys) == 2
    sizes = out["_nexus_position_sizes"]
    for s in buys:
        assert abs(sizes[s]["buy_cash"] - 10_000.0 / 2) < 1.0   # ~half the book each
    assert sizes["_cash_reserve_floor_pct"] == 0.0


def test_exits_when_rsi_recovers():
    # RSI recovered past rsi_exit -> emit the -1 sell signal. (A bare -1 with no
    # concurrent buy carries no size hint; the broker fully sells -1 by default,
    # same as the momentum strategy.)
    data = {"BTC/USD": _bars(_recovered())}
    out = _run(Meanrev(), data, positions={"BTC/USD": 1.0})
    assert out.get("BTC/USD") == -1


def test_does_not_sell_held_dip_on_regime_break():
    # Held coin now BELOW its regime MA but RSI still oversold (< rsi_exit):
    # must HOLD (0), not sell — the validated fix (regime break must not exit).
    data = {"BTC/USD": _bars(_downtrend_dip())}
    out = _run(Meanrev(), data, positions={"BTC/USD": 1.0})
    assert out.get("BTC/USD") == 0                       # hold through the dip
    assert "BTC/USD" not in out.get("_nexus_position_sizes", {})


def test_idle_mode_noop():
    assert Meanrev().run_once([], {}, None, {}, {}, data={}, mode="IDLE") == {}
