"""Unit tests for the Connors fast RSI mean-reversion crypto strategy.

Covers: dip-buy only in-regime (no falling knives), the fast mean-touch exit
(close back above the short exit MA), holding a still-below-MA dip, and top_k.
"""
import numpy as np

from strategies.crypto.connors import Connors


def _bars(closes):
    return [{"t": i, "o": c, "h": c * 1.001, "l": c * 0.999, "c": float(c), "v": 1.0}
            for i, c in enumerate(closes)]


def _uptrend_then_dip(n=230, dip_bars=10):
    up = np.linspace(100.0, 200.0, n - dip_bars)
    dip = np.linspace(200.0, 174.0, dip_bars)   # sharp drop -> short RSI ~ 0, below SMA5
    return np.concatenate([up, dip])


def _downtrend_dip(n=230):
    return np.linspace(200.0, 96.0, n)          # below SMA200 -> falling knife


def _recovered(n=230, up_bars=8):
    base = np.linspace(100.0, 180.0, n - up_bars)
    rally = np.linspace(180.0, 206.0, up_bars)  # close now well above SMA5
    return np.concatenate([base, rally])


class _StubEmu:
    def __init__(self, positions):
        self._pos = positions

    def get_positions(self):
        return dict(self._pos)

    def get_portfolio_value(self, prices):
        return 10_000.0


def _run(data, positions=None):
    emu = _StubEmu(positions or {})
    prices = {s: data[s][-1]["c"] for s in data}
    return Connors().run_once(symbols=list(data.keys()), prices=prices, current_time=None,
                              config={}, conditions={}, data=data, portfolio_emulator=emu, mode="LIVE")


def test_buys_oversold_dip_in_regime_not_knife():
    data = {"BTC/USD": _bars(_uptrend_then_dip()), "ETH/USD": _bars(_downtrend_dip())}
    out = _run(data)
    assert out.get("BTC/USD") == 1
    assert out.get("ETH/USD") == 0


def test_fast_exit_on_mean_touch():
    # Held coin whose price mean-reverted back above the short exit MA -> sell.
    data = {"BTC/USD": _bars(_recovered())}
    out = _run(data, positions={"BTC/USD": 1.0})
    assert out.get("BTC/USD") == -1


def test_holds_dip_still_below_exit_ma():
    # Held coin still below its short exit MA -> hold (0), not sold.
    data = {"BTC/USD": _bars(_uptrend_then_dip())}
    out = _run(data, positions={"BTC/USD": 1.0})
    assert out.get("BTC/USD") == 0


def test_top_k_and_equal_weight_sizing():
    data = {s: _bars(_uptrend_then_dip(dip_bars=d))
            for s, d in [("BTC/USD", 12), ("ETH/USD", 10), ("SOL/USD", 8)]}
    out = _run(data)
    buys = [s for s, v in out.items() if v == 1]
    assert len(buys) == 2
    for s in buys:
        assert abs(out["_nexus_position_sizes"][s]["buy_cash"] - 5_000.0) < 1.0


def test_idle_noop():
    assert Connors().run_once([], {}, None, {}, {}, data={}, mode="IDLE") == {}
