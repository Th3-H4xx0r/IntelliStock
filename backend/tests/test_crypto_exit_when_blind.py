"""A held crypto coin must remain evaluable for EXIT even when it has no bars
this tick (degenerate/poisoned window) or is otherwise absent from `universe`.

Entry (+1) ignores `held`; exit (-1) required both `held` AND `universe`
membership, so a held-but-blind coin was silently skipped -> never sold."""

import datetime

import pytest

from strategies.crypto.momentum import Momentum
from strategies.crypto import core


class _PE:
    def __init__(self, pos):
        self._p = pos

    def get_positions(self):
        return dict(self._p)


def test_held_coin_with_no_bars_is_exited(monkeypatch):
    # Avoid any network in discovery; scoped to this test only (auto-restored).
    monkeypatch.setattr(core, "discover_universe", lambda *a, **k: ["BTC/USD"])
    pe = _PE({"BTC/USD": 0.14})            # holding BTC
    m = Momentum()
    ct = datetime.datetime(2026, 4, 13, 12, 0, 0)
    # EMPTY data window for BTC (poisoned/degenerate) -> must still emit -1.
    res = m.run_once([], {"BTC/USD": 60000.0}, ct, {"band": "medium"}, {},
                     data={}, portfolio_emulator=pe)
    assert res.get("BTC/USD") == -1


def test_held_positions_helper_is_unfiltered_and_slash_agnostic():
    pe = _PE({"BTC/USD": 0.14, "ETH/USD": 0.0})
    assert core.held_positions(pe) == {"BTC/USD"}
    assert core.held_positions(None) == set()
