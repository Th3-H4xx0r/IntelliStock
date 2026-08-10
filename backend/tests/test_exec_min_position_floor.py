"""A position too small to matter must not cost a max_positions slot.

The allocator already enforces a NAV-proportional floor on SIZED buys via
`min_position_nav_pct`. The execution path did not: it used a hardcoded $50,
which is 0.8% of a $6,000 book. So a buy sized at 14% of NAV and then truncated
by available cash still opened a position AND consumed a slot.

bt 371379, on a book that was refusing new names at the cap:
    GH   sized $32.55  -> filled $32.41  (0.5% of NAV)   open_pos=7
    AMZN sized $557.05 -> cash  $108.30  (1.8%)          open_pos=7
    ETN  sized $467.91 -> cash  $162.37  (2.7%)          open_pos=7

That is the objective's blocker #3 — "a great name is refused because a mediocre
one sits on the budget" — arriving through the execution path rather than the
allocator.

2026-08-09 UPDATE — this file no longer MIRRORS the broker, it CALLS it.
A hand-written copy of the rule is how the AVY/AMZN runt leak survived bt
676939: the copy agreed with itself while the real gate measured a different
number. `exec_min_pos` and `skips` below are now thin adapters over broker.py's
own `_exec_min_position_floor` / `_exec_min_position_skips`.

The cases here all predate the emulator-clamp dimension, so they pass
`fundable == cash_to_use` (nothing was in flight). That dimension is covered in
test_exec_runt_leak_fundable.py.
"""
import ast
import os
import sys
import types

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_SRC = open(os.path.join(_backend, "broker.py"), encoding="utf-8").read()
_WANTED = {"_exec_min_position_floor", "_exec_min_position_skips"}
_ns = {}
for _node in ast.parse(_SRC).body:
    if isinstance(_node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_EXEC_MIN_POSITION_USD"
            for t in _node.targets):
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
for _node in ast.parse(_SRC).body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED:
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
for _name in _WANTED | {"_EXEC_MIN_POSITION_USD"}:
    assert _name in _ns, f"failed to extract {_name} from broker.py"
b = types.SimpleNamespace(**{k: v for k, v in _ns.items() if not k.startswith("__")})

HISTORICAL_MIN = b._EXEC_MIN_POSITION_USD


def exec_min_pos(config, nav):
    """The broker-side floor: dollar minimum or NAV share, larger wins."""
    return b._exec_min_position_floor(config, nav)


def skips(cash_to_use, cash_per_trade, config, nav, held=False):
    """The broker's skip test.

    When a NAV floor is configured, size is the whole question and the buy is
    refused however it got small. Without one, the legacy rule stands: only
    refuse a buy that was TRUNCATED from a larger intent. That legacy clause is
    the hole GH went through — the allocator had already sized it down to the
    available $32.55, so cash_to_use == cash_per_trade and it never fired.
    """
    return b._exec_min_position_skips(
        1, cash_to_use, cash_per_trade, cash_to_use,
        exec_min_pos(config, nav), held)


NAV = 6000.0
ON = {"min_position_nav_pct": 0.06}      # $360 on this book
OFF = {}


def test_default_keeps_the_historical_fifty_dollar_floor():
    assert exec_min_pos(OFF, NAV) == pytest.approx(50.0)
    # legacy rule intact: a truncated sub-$50 buy is refused, an untruncated one
    # is not — GH slipped through precisely because it was NOT truncated.
    assert skips(32.41, 900.0, OFF, NAV) is True
    assert skips(32.55, 32.55, OFF, NAV) is False, "the hole, preserved when off"


def test_the_three_runts_that_ate_slots_are_now_skipped():
    for cash_to_use, sized in ((32.55, 32.55), (108.30, 557.05), (162.37, 467.91)):
        assert skips(cash_to_use, sized, ON, NAV) is True


def test_a_full_size_buy_is_untouched():
    assert skips(839.97, 839.97, ON, NAV) is False


def test_a_buy_at_exactly_the_floor_is_kept():
    assert skips(360.0, 840.0, ON, NAV) is False


def test_an_untruncated_runt_is_still_refused_when_a_floor_is_set():
    """GH's exact shape: allocator already sized it down, so nothing was
    'truncated' — and it still must not take a slot."""
    assert skips(100.0, 100.0, ON, NAV) is True
    assert skips(100.0, 100.0, OFF, NAV) is False


def test_the_floor_scales_with_the_book():
    assert exec_min_pos(ON, 6000.0) == pytest.approx(360.0)
    assert exec_min_pos(ON, 60000.0) == pytest.approx(3600.0)
    assert exec_min_pos(ON, 500.0) == pytest.approx(50.0), "never below the dollar floor"


def test_zero_or_missing_nav_falls_back():
    assert exec_min_pos(ON, 0.0) == pytest.approx(50.0)
    assert exec_min_pos(ON, None) == pytest.approx(50.0)


def test_malformed_config_falls_back():
    for bad in ({"min_position_nav_pct": "x"}, None, {"min_position_nav_pct": -1}):
        assert exec_min_pos(bad, NAV) == pytest.approx(50.0)


def test_it_agrees_with_the_allocator_floor():
    """Both ends must use the same number or one will admit what the other
    refuses, which is how the runts got through in the first place."""
    allocator_floor = max(100.0, NAV * ON["min_position_nav_pct"])
    assert exec_min_pos(ON, NAV) == pytest.approx(allocator_floor)


def test_an_ADD_to_a_held_name_is_exempt():
    """bt 571147 refused SNDK's winner-add ($216) and bt 427197 WDC's ($586).
    The floor protects a max_positions SLOT; an add takes no slot, and the
    allocator's own floor already exempts held names."""
    assert skips(216.0, 840.0, ON, NAV, held=False) is True
    assert skips(216.0, 840.0, ON, NAV, held=True) is False
    assert skips(586.0, 840.0, ON, NAV, held=True) is False


def test_a_NEW_name_runt_is_still_refused():
    assert skips(48.23, 840.0, ON, NAV, held=False) is True
