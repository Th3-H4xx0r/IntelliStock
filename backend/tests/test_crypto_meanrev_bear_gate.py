"""Unit tests for MeanRev's optional crash-bear gate (``bear_gate_ma``).

While the equal-weight basket of the visible universe is below its
``bear_gate_ma``-bar MA, NEW entries are blocked; exits and holds are
unaffected; with too little history the gate FAILS OPEN. Default (0) must be a
strict no-op. Fixtures are self-validating: each test first asserts the gate
predicate itself (via _bear_gate_blocked) so a drifted fixture fails loudly
instead of vacuously passing.
"""
import numpy as np

from strategies.crypto.meanrev import Meanrev, _bear_gate_blocked


def _bars(closes):
    return [{"t": i, "o": c, "h": c * 1.001, "l": c * 0.999, "c": float(c), "v": 1.0}
            for i, c in enumerate(closes)]


def _healthy_dip_long(n=900, dip_bars=16):
    """Flat, then the validated uptrend-then-dip tail: close above its SMA200
    (in-regime) with oversold RSI. Long enough to feed the basket gate."""
    flat = np.full(n - 230, 100.0)
    up = np.linspace(100.0, 200.0, 214)
    dip = np.linspace(200.0, 176.0, dip_bars)
    return np.concatenate([flat, up, dip])


def _recovered_long(n=900, up_bars=16):
    """Held-dip that has rallied: RSI past rsi_exit -> should sell even gated."""
    flat = np.full(n - 230, 100.0)
    base = np.linspace(100.0, 180.0, 230 - up_bars)
    rally = np.linspace(180.0, 205.0, up_bars)
    return np.concatenate([flat, base, rally])


def _collapsing(n=900):
    return np.linspace(100.0, 10.0, n)


class _StubEmu:
    def __init__(self, positions, value=10_000.0):
        self._pos = positions
        self._val = value

    def get_positions(self):
        return dict(self._pos)

    def get_portfolio_value(self, prices):
        return self._val


def _run(strat, data, positions=None, config=None):
    emu = _StubEmu(positions or {})
    prices = {s: data[s][-1]["c"] for s in data}
    return strat.run_once(symbols=list(data.keys()), prices=prices, current_time=None,
                          config=config or {}, conditions={}, data=data,
                          portfolio_emulator=emu, mode="LIVE")


def _bear_universe(n=900, healthy=_healthy_dip_long):
    """One buyable healthy dip + three collapsed coins dragging the basket
    below its MA — the 2022-bear-rally shape the gate exists to suppress."""
    return {
        "BTC/USD": _bars(healthy(n)),
        "ETH/USD": _bars(_collapsing(n)),
        "LTC/USD": _bars(_collapsing(n)),
        "BCH/USD": _bars(_collapsing(n)),
    }


def test_gate_predicate_fires_on_bear_basket():
    data = _bear_universe()
    assert _bear_gate_blocked(data, list(data.keys()), 600) is True


def test_gate_blocks_new_entries_when_basket_below_ma():
    data = _bear_universe()
    assert _bear_gate_blocked(data, list(data.keys()), 600) is True  # fixture valid
    out = _run(Meanrev(), data, config={"bear_gate_ma": 600})
    assert out.get("BTC/USD") == 0                       # dip NOT bought while gated
    assert "BTC/USD" not in out.get("_nexus_position_sizes", {})


def test_default_off_is_noop():
    data = _bear_universe()
    out = _run(Meanrev(), data)                          # no bear_gate_ma -> off
    assert out.get("BTC/USD") == 1                       # pre-gate behavior preserved
    out2 = _run(Meanrev(), data, config={"bear_gate_ma": 0})
    assert out2.get("BTC/USD") == 1


def test_exits_still_fire_while_gated():
    data = _bear_universe(healthy=_recovered_long)
    assert _bear_gate_blocked(data, list(data.keys()), 600) is True  # fixture valid
    out = _run(Meanrev(), data, positions={"BTC/USD": 1.0},
               config={"bear_gate_ma": 600})
    assert out.get("BTC/USD") == -1                      # bank the bounce regardless


def test_fails_open_on_short_history():
    # 500 bars < max(600, gate_ma//2) -> gate off -> dip still bought.
    data = {
        "BTC/USD": _bars(_healthy_dip_long(500)),
        "ETH/USD": _bars(_collapsing(500)),
        "LTC/USD": _bars(_collapsing(500)),
        "BCH/USD": _bars(_collapsing(500)),
    }
    assert _bear_gate_blocked(data, list(data.keys()), 600) is False
    out = _run(Meanrev(), data, config={"bear_gate_ma": 600})
    assert out.get("BTC/USD") == 1


def test_gate_never_raises_on_garbage():
    assert _bear_gate_blocked({}, ["BTC/USD"], 1200) is False
    assert _bear_gate_blocked({"BTC/USD": []}, ["BTC/USD"], 1200) is False
    zeros = _bars(np.zeros(700))
    assert _bear_gate_blocked({"BTC/USD": zeros}, ["BTC/USD"], 600) is False


def test_bull_basket_does_not_gate():
    # All coins rising -> basket above its MA -> gate must NOT block the dip.
    data = {
        "BTC/USD": _bars(_healthy_dip_long()),
        "ETH/USD": _bars(np.linspace(100.0, 300.0, 900)),
        "LTC/USD": _bars(np.linspace(100.0, 260.0, 900)),
        "BCH/USD": _bars(np.linspace(100.0, 280.0, 900)),
    }
    assert _bear_gate_blocked(data, list(data.keys()), 600) is False
    out = _run(Meanrev(), data, config={"bear_gate_ma": 600})
    assert out.get("BTC/USD") == 1
