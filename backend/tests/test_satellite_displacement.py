"""Displacement must trim the weakest holding, never the largest (bt 873929).

On the 2026-01-19 tick the book held AGQ $2,535 (42% of NAV, went on to +169.7%,
captured 94.9%), CPER $921.78 at raw +1.000 (went on to +13.8%), APP and BKNG at
+1.800. SNDK scored +1.700 and needed $955.76; only $27.20 was fundable.

CPER is the correct trim: weakest conviction, and its value alone nearly covers the
buy. AGQ is the trap: trimming by position size sells the best trade of the window.

AST extraction because broker.py is not import-safe (argv parsing at module scope).
"""
import ast
import os
import sys

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

_src = open(os.path.join(_backend, "broker.py"), encoding="utf-8").read()
_tree = ast.parse(_src)
_ns = {"_core_sleeve_cfg_raw": lambda specs: (
    (specs or [{}])[0].get("config") if isinstance(specs, list) and specs
    and isinstance(specs[0], dict) else None)}
for _node in _tree.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in (
            "_displacement_candidate", "_displacement_enabled"):
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
_pick = _ns["_displacement_candidate"]
_enabled = _ns["_displacement_enabled"]

# symbol -> (conviction, market value) exactly as reconstructed from the run
BOOK = {
    "AGQ": (None, 2535.70),   # no raw score anywhere in the run
    "CPER": (1.000, 921.78),
    "APP": (1.800, 847.55),
    "BKNG": (1.800, 824.68),
}
SNDK = 1.700
NEED = 420.0   # _exec_min_pos on that tick


def test_picks_the_weakest_conviction_not_the_largest_position():
    sym, val = _pick(BOOK, SNDK, NEED)
    assert sym == "CPER", "must trim the weakest name, not the biggest"
    assert val == pytest.approx(921.78)


def test_never_sells_the_winner_even_though_it_is_the_biggest():
    for _ in range(10):
        assert _pick(BOOK, SNDK, NEED)[0] != "AGQ"


def test_an_unscored_holding_is_never_displaced():
    """AGQ carried no score; unknown must not read as weakest."""
    assert _pick({"AGQ": (None, 2535.70)}, SNDK, NEED) is None


def test_refuses_when_the_gap_is_not_material():
    near = {"APP": (1.800, 900.0), "BKNG": (1.800, 900.0)}
    assert _pick(near, SNDK, NEED) is None


def test_refuses_a_holding_too_small_to_cover_the_shortfall():
    small = {"CPER": (1.000, 100.0)}
    assert _pick(small, SNDK, NEED) is None, "selling it still leaves the buy refused"


def test_never_displaces_a_stronger_name():
    stronger = {"BIG": (2.500, 5000.0)}
    assert _pick(stronger, SNDK, NEED) is None


def test_gap_threshold_is_enforced_exactly():
    assert _pick({"X": (1.200, 999.0)}, SNDK, NEED)[0] == "X"   # gap 0.500 clears
    assert _pick({"X": (1.201, 999.0)}, SNDK, NEED) is None      # gap 0.499 does not


def test_malformed_rows_never_raise():
    bad = {"A": None, "B": ("x", "y"), "C": (1.0,), "OK": (0.5, 999.0)}
    assert _pick(bad, SNDK, NEED)[0] == "OK"


def test_empty_book_is_none():
    assert _pick({}, SNDK, NEED) is None
    assert _pick(None, SNDK, NEED) is None


def test_default_off_and_reader_never_raises():
    assert _enabled(None) is False
    assert _enabled([]) is False
    assert _enabled(object()) is False
    assert _enabled([{"config": {"satellite_displacement_enabled": True}}]) is True
