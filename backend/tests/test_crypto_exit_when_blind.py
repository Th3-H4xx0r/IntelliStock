"""A held crypto coin must remain evaluable for EXIT even when it has no bars
this tick (degenerate/poisoned window) or is otherwise absent from `universe`.

Entry (+1) ignores `held`; exit (-1) required both `held` AND `universe`
membership (or, for fast, a coin with bars), so a held-but-blind coin was
silently skipped -> never sold. Covers all four crypto strategies."""

import datetime

import pytest

from strategies.crypto import core
from strategies.crypto.momentum import Momentum
from strategies.crypto.allocator import Allocator
from strategies.crypto.fast import Fast
from strategies.crypto.reference import Reference


class _PE:
    def __init__(self, pos):
        self._p = pos

    def get_positions(self):
        return dict(self._p)


@pytest.mark.parametrize("StratCls", [Momentum, Allocator, Fast, Reference])
def test_held_coin_with_no_bars_is_exited(monkeypatch, StratCls):
    # No network in discovery; scoped to this test (auto-restored).
    monkeypatch.setattr(core, "discover_universe", lambda *a, **k: ["BTC/USD"])
    pe = _PE({"BTC/USD": 0.14})            # holding BTC
    strat = StratCls()
    ct = datetime.datetime(2026, 4, 13, 12, 0, 0)
    # EMPTY data window (poisoned/degenerate) -> must still emit -1 to exit.
    res = strat.run_once([], {"BTC/USD": 60000.0}, ct, {"band": "medium"}, {},
                         data={}, portfolio_emulator=pe)
    assert res.get("BTC/USD") == -1


def test_held_positions_helper_is_unfiltered_and_slash_agnostic():
    pe = _PE({"BTC/USD": 0.14, "ETH/USD": 0.0})
    assert core.held_positions(pe) == {"BTC/USD"}
    assert core.held_positions(None) == set()


def test_exit_blind_held_only_targets_unseen_holdings():
    pe = _PE({"BTC/USD": 1.0, "ETH/USD": 2.0, "SOL/USD": 3.0})
    result = {}
    # ETH is in the evaluated universe; SOL has data this tick; only BTC is blind.
    blind = core.exit_blind_held(
        result, pe, {"SOL/USD": [{"c": 1}]}, evaluated=["ETH/USD"]
    )
    assert result == {"BTC/USD": -1}
    assert blind == ["BTC/USD"]
