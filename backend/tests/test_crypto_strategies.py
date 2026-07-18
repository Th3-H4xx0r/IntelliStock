"""Unit tests for the crypto run_once strategy classes.

Synthetic bar series only — no network, no ``alpaca``. Each strategy is
exercised through the exact broker ``run_once(...)`` signature and asserted on
its buy/hold/sell/risk-off behaviour and the ``_nexus_*`` metadata contract.
"""

from __future__ import annotations

import os
import sys

import numpy as np

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from strategies.crypto.reference import Reference
from strategies.crypto.fast import Fast
from strategies.crypto.momentum import Momentum
from strategies.crypto.allocator import Allocator


class FakePortfolio:
    """Minimal stand-in for portfolio_emulator (no alpaca)."""

    def __init__(self, positions=None, value=10_000.0, cash=10_000.0):
        self._positions = dict(positions or {})
        self._value = float(value)
        self._cash = float(cash)

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices):
        return self._value

    def get_cash(self):
        return self._cash


def _bars(closes):
    """OHLCV bars from a close series (high/low bracket the close)."""
    return [
        {"o": c, "h": c * 1.001, "l": c * 0.999, "c": c, "v": 10.0, "t": i}
        for i, c in enumerate(closes)
    ]


# ===========================================================================
# Reference
# ===========================================================================

def test_reference_flat_portfolio_buys_toward_equal_weight():
    data = {"BTC/USD": _bars([100, 100]), "ETH/USD": _bars([200, 200])}
    prices = {"BTC/USD": 100.0, "ETH/USD": 200.0}
    out = Reference().run_once(
        ["BTC/USD", "ETH/USD"], prices, None, {}, {},
        data=data, portfolio_emulator=None, mode="MONITOR",
    )
    assert out["BTC/USD"] == 1
    assert out["ETH/USD"] == 1
    # Sizing is left to the broker's default per-symbol sizing: the strategy
    # must NOT emit the contract-violating ``_nexus_position_sizes`` float map.
    assert "_nexus_position_sizes" not in out


def test_reference_trims_overweight_and_buys_underweight():
    data = {"BTC/USD": _bars([100, 100]), "ETH/USD": _bars([200, 200])}
    prices = {"BTC/USD": 100.0, "ETH/USD": 200.0}
    # 100 BTC @ 100 == the entire 10k portfolio -> BTC hugely overweight, ETH 0.
    pf = FakePortfolio(positions={"BTC/USD": 100}, value=10_000.0)
    out = Reference().run_once(
        ["BTC/USD", "ETH/USD"], prices, None, {}, {},
        data=data, portfolio_emulator=pf, mode="MONITOR",
    )
    assert out["BTC/USD"] == -1  # trim the overweight
    assert out["ETH/USD"] == 1   # buy the underweight


def test_reference_discovers_universe_when_seed_empty():
    data = {"BTC/USD": _bars([100, 100]), "ETH/USD": _bars([200, 200])}
    prices = {"BTC/USD": 100.0, "ETH/USD": 200.0}
    out = Reference().run_once(
        [], prices, None, {}, {}, data=data, portfolio_emulator=None, mode="MONITOR",
    )
    assert set(out["_nexus_discovered"]) == {"BTC/USD", "ETH/USD"}


def test_reference_idle_mode_noops():
    data = {"BTC/USD": _bars([100, 100])}
    out = Reference().run_once(
        ["BTC/USD"], {"BTC/USD": 100.0}, None, {}, {},
        data=data, portfolio_emulator=None, mode="IDLE",
    )
    assert out == {}


# ===========================================================================
# Fast (Band 1)
# ===========================================================================

def _breakout_series():
    return list(np.linspace(100.0, 100.0, 25)) + list(np.linspace(101.0, 130.0, 19)) + [140.0]


def _chop_series():
    return [100.0 + (1.0 if i % 2 == 0 else -1.0) for i in range(45)]


def _breakdown_series():
    return list(np.linspace(100.0, 100.0, 25)) + list(np.linspace(99.0, 70.0, 19)) + [60.0]


def test_fast_breakout_buys():
    # Donchian: a decisive uptrend (above the 50-bar SMA) that breaks the prior
    # 20-bar high -> buy.
    data = {"BTC/USD": _bars(list(np.linspace(100.0, 200.0, 60)))}
    out = Fast().run_once(
        ["BTC/USD"], {"BTC/USD": 200.0}, None, {}, {},
        data=data, portfolio_emulator=None, mode="MONITOR",
    )
    assert out["BTC/USD"] == 1


def test_fast_chop_holds():
    data = {"BTC/USD": _bars(_chop_series())}
    out = Fast().run_once(
        ["BTC/USD"], {"BTC/USD": 100.0}, None, {}, {},
        data=data, portfolio_emulator=None, mode="MONITOR",
    )
    assert out["BTC/USD"] == 0  # no breakout + gap below the fee edge


def test_fast_breakdown_exits():
    # Donchian trailing exit: a HELD position that breaks below the prior
    # exit-window low -> sell.
    data = {"BTC/USD": _bars(list(np.linspace(200.0, 100.0, 60)))}
    pf = FakePortfolio(positions={"BTC/USD": 0.1})
    out = Fast().run_once(
        ["BTC/USD"], {"BTC/USD": 100.0}, None, {}, {},
        data=data, portfolio_emulator=pf, mode="MONITOR",
    )
    assert out["BTC/USD"] == -1


def test_fast_trades_any_seeded_pair():
    # The Donchian fast band is universe-based (no longer BTC/ETH-only): a seeded
    # pair with a breakout in an up-regime is bought.
    data = {"SOL/USD": _bars(list(np.linspace(100.0, 200.0, 60)))}
    out = Fast().run_once(
        ["SOL/USD"], {"SOL/USD": 200.0}, None, {}, {},
        data=data, portfolio_emulator=None, mode="MONITOR",
    )
    assert out["SOL/USD"] == 1


# ===========================================================================
# Momentum (Band 2)
# ===========================================================================

def test_momentum_uptrend_ranks_top_k():
    data = {
        "BTC/USD": _bars(list(np.linspace(100.0, 200.0, 40))),  # +100%
        "ETH/USD": _bars(list(np.linspace(100.0, 150.0, 40))),  # +50%
        "SOL/USD": _bars(list(np.linspace(100.0, 120.0, 40))),  # +20%
    }
    prices = {"BTC/USD": 200.0, "ETH/USD": 150.0, "SOL/USD": 120.0}
    out = Momentum().run_once(
        ["BTC/USD", "ETH/USD", "SOL/USD"], prices, None, {}, {"top_k": 2},
        data=data, portfolio_emulator=None, mode="MONITOR",
    )
    assert out["BTC/USD"] == 1   # strongest momentum
    assert out["ETH/USD"] == 1   # second strongest
    assert out["SOL/USD"] == 0   # outside top-K, not held -> hold
    # Sizing is delegated to the broker; no _nexus_position_sizes contract map.
    assert "_nexus_position_sizes" not in out


def test_momentum_aggregate_downtrend_risk_off():
    data = {
        "BTC/USD": _bars(list(np.linspace(200.0, 100.0, 40))),
        "ETH/USD": _bars(list(np.linspace(150.0, 100.0, 40))),
        "SOL/USD": _bars(list(np.linspace(120.0, 100.0, 40))),
    }
    prices = {"BTC/USD": 100.0, "ETH/USD": 100.0, "SOL/USD": 100.0}
    out = Momentum().run_once(
        ["BTC/USD", "ETH/USD", "SOL/USD"], prices, None, {}, {},
        data=data, portfolio_emulator=None, mode="MONITOR",
    )
    # flat portfolio -> risk-off means no buys (all zeros)
    assert out["BTC/USD"] == 0
    assert out["ETH/USD"] == 0
    assert out["SOL/USD"] == 0
    assert "_nexus_position_sizes" not in out


def test_momentum_risk_off_exits_held_positions():
    data = {
        "BTC/USD": _bars(list(np.linspace(200.0, 100.0, 40))),
        "ETH/USD": _bars(list(np.linspace(150.0, 100.0, 40))),
    }
    prices = {"BTC/USD": 100.0, "ETH/USD": 100.0}
    pf = FakePortfolio(positions={"BTC/USD": 5}, value=10_000.0)
    out = Momentum().run_once(
        ["BTC/USD", "ETH/USD"], prices, None, {}, {},
        data=data, portfolio_emulator=pf, mode="MONITOR",
    )
    assert out["BTC/USD"] == -1  # held -> exit to USD
    assert out["ETH/USD"] == 0   # not held -> hold


# ===========================================================================
# Allocator (Band 3)
# ===========================================================================

def test_allocator_above_ma_gets_inverse_vol_weights():
    data = {
        "BTC/USD": _bars(list(np.linspace(100.0, 140.0, 30))),  # above its MA
        "ETH/USD": _bars(list(np.linspace(100.0, 130.0, 30))),  # above its MA
    }
    prices = {"BTC/USD": 140.0, "ETH/USD": 130.0}
    out = Allocator().run_once(
        ["BTC/USD", "ETH/USD"], prices, None, {}, {},
        data=data, portfolio_emulator=None, mode="MONITOR",
    )
    assert out["BTC/USD"] == 1
    assert out["ETH/USD"] == 1
    # Sizing is delegated to the broker; no _nexus_position_sizes contract map.
    assert "_nexus_position_sizes" not in out


def test_allocator_btc_below_ma_full_risk_off():
    data = {
        "BTC/USD": _bars(list(np.linspace(140.0, 100.0, 30))),  # below its MA
        "ETH/USD": _bars(list(np.linspace(100.0, 130.0, 30))),  # would be above, but regime off
    }
    prices = {"BTC/USD": 100.0, "ETH/USD": 130.0}
    out = Allocator().run_once(
        ["BTC/USD", "ETH/USD"], prices, None, {}, {},
        data=data, portfolio_emulator=None, mode="MONITOR",
    )
    # BTC regime off -> full risk-off; flat portfolio -> all zeros, no sizing.
    assert out["BTC/USD"] == 0
    assert out["ETH/USD"] == 0
    assert "_nexus_position_sizes" not in out


# ===========================================================================
# Contract guard: no strategy may emit _nexus_position_sizes
# ===========================================================================

def test_no_strategy_emits_nexus_position_sizes():
    """The broker reads _nexus_position_sizes as {sym: dict}; a {sym: float} map
    raises TypeError and aborts the whole execution loop. Assert every crypto
    strategy — even on its BUY path — omits the key entirely."""
    prices = {"BTC/USD": 200.0, "ETH/USD": 150.0}

    ref = Reference().run_once(
        ["BTC/USD", "ETH/USD"], {"BTC/USD": 100.0, "ETH/USD": 200.0}, None, {}, {},
        data={"BTC/USD": _bars([100, 100]), "ETH/USD": _bars([200, 200])},
        portfolio_emulator=None, mode="MONITOR",
    )
    mom = Momentum().run_once(
        ["BTC/USD", "ETH/USD"], prices, None, {}, {},
        data={
            "BTC/USD": _bars(list(np.linspace(100.0, 200.0, 40))),
            "ETH/USD": _bars(list(np.linspace(100.0, 150.0, 40))),
        },
        portfolio_emulator=None, mode="MONITOR",
    )
    alloc = Allocator().run_once(
        ["BTC/USD", "ETH/USD"], prices, None, {}, {},
        data={
            "BTC/USD": _bars(list(np.linspace(100.0, 140.0, 30))),
            "ETH/USD": _bars(list(np.linspace(100.0, 130.0, 30))),
        },
        portfolio_emulator=None, mode="MONITOR",
    )
    fast = Fast().run_once(
        ["BTC/USD"], {"BTC/USD": 200.0}, None, {}, {},
        data={"BTC/USD": _bars(list(np.linspace(100.0, 200.0, 60)))},
        portfolio_emulator=None, mode="MONITOR",
    )
    for name, out in [("reference", ref), ("momentum", mom), ("allocator", alloc), ("fast", fast)]:
        assert "_nexus_position_sizes" not in out, f"{name} still emits _nexus_position_sizes"
        # Sanity: each hit its buy path, so the assertion above is meaningful.
        assert any(v == 1 for k, v in out.items() if not str(k).startswith("_")), \
            f"{name} produced no buy signal"


def test_held_lookup_tolerates_slashless_position_keys():
    """Finding #4: Alpaca may key crypto positions WITHOUT the slash (BTCUSD).
    Held detection must still fire so a risk-off exits the position."""
    data = {
        "BTC/USD": _bars(list(np.linspace(200.0, 100.0, 40))),  # downtrend -> risk-off
        "ETH/USD": _bars(list(np.linspace(150.0, 100.0, 40))),
    }
    prices = {"BTC/USD": 100.0, "ETH/USD": 100.0}
    pf = FakePortfolio(positions={"BTCUSD": 5}, value=10_000.0)  # NO slash
    out = Momentum().run_once(
        ["BTC/USD", "ETH/USD"], prices, None, {}, {},
        data=data, portfolio_emulator=pf, mode="MONITOR",
    )
    assert out["BTC/USD"] == -1  # detected as held despite slash-less key -> exit
    assert out["ETH/USD"] == 0   # not held -> hold


# ===========================================================================
# Auto-discovery wiring (empty seed) with robust fallback
# ===========================================================================

def _fake_alpaca_http_get(assets, bars_payload):
    """A stand-in for ``requests.get`` serving the Alpaca assets + bars REST."""
    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def _get(url, params=None, headers=None):
        if "/v2/assets" in url:
            return _Resp(assets)
        return _Resp({"bars": bars_payload})

    return _get


def test_momentum_discovers_universe_when_seed_empty(monkeypatch):
    """Empty seed + creds in config -> discovery runs through the injected
    providers and the ranked pairs surface in _nexus_discovered (broker then
    expands the universe and fetches their bars on the next tick)."""
    assets = [
        {"symbol": "BTC/USD", "tradable": True, "status": "active"},
        {"symbol": "ETH/USD", "tradable": True, "status": "active"},
    ]
    bars_payload = {
        "BTC/USD": _bars(list(np.linspace(100.0, 200.0, 8))),
        "ETH/USD": _bars(list(np.linspace(100.0, 150.0, 8))),
    }
    monkeypatch.setattr(
        "strategies.crypto.core.requests.get",
        _fake_alpaca_http_get(assets, bars_payload),
    )
    out = Momentum().run_once(
        [], {}, None, {"alpaca_key": "K", "alpaca_secret": "S"}, {},
        data={}, portfolio_emulator=None, mode="MONITOR",
    )
    assert set(out.get("_nexus_discovered") or []) == {"BTC/USD", "ETH/USD"}
    assert "_nexus_position_sizes" not in out


def test_allocator_discovers_universe_when_seed_empty(monkeypatch):
    assets = [
        {"symbol": "BTC/USD", "tradable": True, "status": "active"},
        {"symbol": "ETH/USD", "tradable": True, "status": "active"},
    ]
    bars_payload = {
        "BTC/USD": _bars(list(np.linspace(100.0, 140.0, 8))),
        "ETH/USD": _bars(list(np.linspace(100.0, 130.0, 8))),
    }
    monkeypatch.setattr(
        "strategies.crypto.core.requests.get",
        _fake_alpaca_http_get(assets, bars_payload),
    )
    out = Allocator().run_once(
        [], {}, None, {"alpaca_key": "K", "alpaca_secret": "S"}, {},
        data={}, portfolio_emulator=None, mode="MONITOR",
    )
    assert set(out.get("_nexus_discovered") or []) == {"BTC/USD", "ETH/USD"}
    assert "_nexus_position_sizes" not in out


def test_momentum_falls_back_to_default_majors_without_creds():
    """No creds -> discovery short-circuits to []; strategy falls back to majors."""
    from strategies.crypto.momentum import DEFAULT_MAJORS
    out = Momentum().run_once(
        [], {}, None, {}, {},  # no alpaca_key/secret
        data={}, portfolio_emulator=None, mode="MONITOR",
    )
    assert out.get("_nexus_discovered") == DEFAULT_MAJORS


def test_allocator_falls_back_to_default_majors_without_creds():
    from strategies.crypto.allocator import DEFAULT_MAJORS
    out = Allocator().run_once(
        [], {}, None, {}, {},
        data={}, portfolio_emulator=None, mode="MONITOR",
    )
    assert out.get("_nexus_discovered") == DEFAULT_MAJORS


def test_discovery_network_error_never_crashes_tick(monkeypatch):
    """Discovery must never raise: a provider blowing up falls back to majors."""
    from strategies.crypto.momentum import DEFAULT_MAJORS

    def boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("strategies.crypto.core.requests.get", boom)
    out = Momentum().run_once(
        [], {}, None, {"alpaca_key": "K", "alpaca_secret": "S"}, {},
        data={}, portfolio_emulator=None, mode="MONITOR",
    )
    assert out.get("_nexus_discovered") == DEFAULT_MAJORS
