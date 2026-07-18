"""Unit tests for the Adaptive regime switcher (bull -> hold EW basket;
bear -> flip-liquidate then delegate to gated MeanRev).

Fixtures are self-validating: each test asserts the regime predicate (via
_is_bull) before asserting run_once behavior. Small switch/confirm MAs are
passed via config so ~900-bar fixtures exercise the real code paths.
"""
import numpy as np

from strategies.crypto.adaptive import Adaptive, _is_bull


def _bars(closes):
    return [{"t": i, "o": c, "h": c * 1.001, "l": c * 0.999, "c": float(c), "v": 1.0}
            for i, c in enumerate(closes)]


def _rising(n=900, lo=100.0, hi=260.0):
    return np.linspace(lo, hi, n)


def _collapsing(n=900):
    return np.linspace(100.0, 10.0, n)


def _healthy_dip_long(n=900, dip_bars=16):
    """Above own SMA200 with oversold RSI (the validated meanrev dip shape)."""
    flat = np.full(n - 230, 100.0)
    up = np.linspace(100.0, 200.0, 214)
    dip = np.linspace(200.0, 176.0, dip_bars)
    return np.concatenate([flat, up, dip])


class _StubEmu:
    def __init__(self, positions, value=10_000.0):
        self._pos = positions
        self._val = value

    def get_positions(self):
        return dict(self._pos)

    def get_portfolio_value(self, prices):
        return self._val


# confirm_ma must be >= 600: the bear gate fails OPEN below its 600-bar live-
# safety floor (max(600, ma//2) shared bars), so smaller test gates never block.
CFG = {"switch_ma": 800, "confirm_ma": 600}


def _run(data, positions=None, config=None, cache=None):
    emu = _StubEmu(positions or {})
    prices = {s: data[s][-1]["c"] for s in data}
    return Adaptive().run_once(symbols=list(data.keys()), prices=prices,
                               current_time=None, config={**CFG, **(config or {})},
                               conditions={}, data=data, portfolio_emulator=emu,
                               strategy_cache=cache, mode="LIVE")


def _bull_universe(n=900):
    return {s: _bars(_rising(n, 100.0, hi)) for s, hi in
            [("BTC/USD", 300.0), ("ETH/USD", 260.0), ("LTC/USD", 240.0), ("BCH/USD", 280.0)]}


def _bear_universe(n=900):
    return {
        "BTC/USD": _bars(_healthy_dip_long(n)),   # buyable dip if gate were open
        "ETH/USD": _bars(_collapsing(n)),
        "LTC/USD": _bars(_collapsing(n)),
        "BCH/USD": _bars(_collapsing(n)),
    }


def test_bull_holds_the_basket():
    data = _bull_universe()
    assert _is_bull(data, list(data.keys()), 800, 600) is True  # fixture valid
    out = _run(data)
    buys = [s for s, v in out.items()
            if isinstance(s, str) and not s.startswith("_") and v == 1]
    assert sorted(buys) == sorted(data.keys())               # buy the whole basket
    sizes = out["_nexus_position_sizes"]
    for s in buys:
        assert abs(sizes[s]["buy_cash"] - 10_000.0 / 4) < 1.0  # pv/N each


def test_bull_does_not_churn_held_coins():
    # Held coins AT target weight (~25% of the 10k stub pv) must hold (0) —
    # only non-held coins get bought. (Out-of-band held coins are covered by
    # the drift-rebalancing test below.)
    data = _bull_universe()
    prices = {s: data[s][-1]["c"] for s in data}
    pos = {"BTC/USD": 2500.0 / prices["BTC/USD"],
           "ETH/USD": 2500.0 / prices["ETH/USD"]}
    out = _run(data, positions=pos)
    assert out.get("BTC/USD") == 0 and out.get("ETH/USD") == 0   # hold, no rebuy
    assert out.get("LTC/USD") == 1 and out.get("BCH/USD") == 1   # top up the rest


def test_bear_flip_liquidates_basket():
    data = _bear_universe()
    assert _is_bull(data, list(data.keys()), 800, 600) is False  # fixture valid
    cache = {"_adaptive_mode": "bull"}
    out = _run(data, positions={s: 1.0 for s in data}, cache=cache)
    for s in data:
        assert out.get(s) == -1                                  # sell everything
    assert out["_nexus_position_sizes"]["BTC/USD"]["sell_fraction"] == 1.0
    assert cache["_adaptive_mode"] == "bear"                     # mode recorded


def test_bear_flip_fallback_without_cache():
    # No strategy_cache: holding more coins than top_k in a bear = basket
    # remnants -> liquidate (the stateless fallback).
    data = _bear_universe()
    out = _run(data, positions={s: 1.0 for s in data})
    assert all(out.get(s) == -1 for s in data)


def test_steady_bear_delegates_to_gated_meanrev():
    # Bear + no holdings: MeanRev runs with bear_gate_ma=confirm_ma. The basket
    # is deep under its MAs, so the gate must block the BTC dip-buy.
    data = _bear_universe()
    cache = {"_adaptive_mode": "bear"}
    out = _run(data, positions={}, cache=cache)
    assert out.get("BTC/USD") == 0                # gated: dip NOT bought
    assert "BTC/USD" not in out.get("_nexus_position_sizes", {})


def test_steady_bear_exits_on_rsi_recovery():
    # A held coin whose RSI recovered must still be sold in bear mode
    # (MeanRev exit semantics pass through the delegation).
    n = 900
    flat = np.full(n - 230, 100.0)
    base = np.linspace(100.0, 180.0, 214)
    rally = np.linspace(180.0, 205.0, 16)
    data = _bear_universe()
    data["BTC/USD"] = _bars(np.concatenate([flat, base, rally]))
    cache = {"_adaptive_mode": "bear"}
    out = _run(data, positions={"BTC/USD": 1.0}, cache=cache)
    assert out.get("BTC/USD") == -1


def test_fail_open_to_bull_on_short_history():
    data = {s: _bars(_rising(200)) for s in ("BTC/USD", "ETH/USD")}
    assert _is_bull(data, list(data.keys()), 800, 600) is True   # < min_hist
    out = _run(data)
    assert all(v == 1 for s, v in out.items()
               if isinstance(s, str) and not s.startswith("_"))


def test_idle_mode_noop():
    assert Adaptive().run_once([], {}, None, {}, {}, data={}, mode="IDLE") == {}


def test_bull_rebalance_trims_overweight_and_tops_up_underweight():
    # BTC massively overweight, ETH underweight, LTC/BCH in band. pv=10k,
    # 4 coins -> target 25% each; band 0.5 -> act outside [12.5%, 37.5%].
    data = _bull_universe()
    prices = {s: data[s][-1]["c"] for s in data}
    # qty chosen so BTC ~50% of pv, ETH ~5%, LTC ~25%, BCH ~20%
    pos = {
        "BTC/USD": 5000.0 / prices["BTC/USD"],
        "ETH/USD": 500.0 / prices["ETH/USD"],
        "LTC/USD": 2500.0 / prices["LTC/USD"],
        "BCH/USD": 2000.0 / prices["BCH/USD"],
    }
    out = _run(data, positions=pos)
    sizes = out["_nexus_position_sizes"]
    assert out["BTC/USD"] == -1                              # trim the 50%er
    assert 0.0 < sizes["BTC/USD"]["sell_fraction"] < 1.0     # fractional, not exit
    assert abs(sizes["BTC/USD"]["sell_fraction"] - 0.5) < 0.05   # (50-25)/50
    assert out["ETH/USD"] == 1                               # top up the 5%er
    assert abs(sizes["ETH/USD"]["buy_cash"] - 2000.0) < 60.0     # (25-5)% of 10k
    assert out["LTC/USD"] == 0 and out["BCH/USD"] == 0       # in band: hold


def test_bull_rebalance_disabled_with_zero_drift():
    data = _bull_universe()
    prices = {s: data[s][-1]["c"] for s in data}
    pos = {"BTC/USD": 5000.0 / prices["BTC/USD"],
           "ETH/USD": 500.0 / prices["ETH/USD"],
           "LTC/USD": 2500.0 / prices["LTC/USD"],
           "BCH/USD": 2000.0 / prices["BCH/USD"]}
    out = _run(data, positions=pos, config={"rebalance_drift": 0})
    assert all(out.get(s) == 0 for s in data)                # pure hold
