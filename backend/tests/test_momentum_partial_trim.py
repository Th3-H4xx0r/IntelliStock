"""Peak-defense (2026-07-22): _momentum_partial_trim_missing decides which held
momentum-watchlist names carrying a RETAINED profit-take partial trim must be
force-included into expanded_symbols so the trim actually executes (instead of
being silently dropped by the _sell_first filter, which is what let CAR ride
+142% -> -18% untrimmed in bt701112).

broker.py is a module-scope script (runs argparse at import), so we extract the
pure function's real source via `ast` and exec it in isolation — the test stays
bound to the actual code, not a copy.
"""
import ast
import os
import pathlib

_BROKER = pathlib.Path(__file__).resolve().parents[1] / "broker.py"


def _load_fns(*names):
    """Exec the named top-level functions from broker.py in ONE shared namespace
    (so functions that call each other resolve). broker.py runs argparse at
    import, so we extract real source via ast instead of importing the module."""
    src = _BROKER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    wanted = set(names)
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(ast.get_source_segment(src, node), ns)
    for n in names:
        if n not in ns:
            raise AssertionError(f"{n} not found in broker.py")
    return tuple(ns[n] for n in names)


partial_trim_syms, missing = _load_fns("_partial_trim_syms", "_momentum_partial_trim_missing")


def test_partial_trim_not_in_expanded_is_included():
    # CAR: held, has a 40% profit-take trim, and is NOT in the discovery set.
    nps = {"CAR": {"sell_fraction": 0.4}}
    assert missing(nps, set(), {"CAR": 2.68}) == {"CAR"}


def test_full_sell_is_excluded():
    # sell_fraction == 1.0 is a full liquidation (an enforcement/forced exit),
    # handled by the V7.5 path — not a partial trim.
    assert missing({"CAR": {"sell_fraction": 1.0}}, set(), {"CAR": 2.68}) == set()


def test_buy_entry_is_excluded():
    # A buy hint must never be pulled into the sell-execution set.
    nps = {"AAOI": {"buy_cash": 900.0, "sell_fraction": 0.5}}
    assert missing(nps, set(), {"AAOI": 6.0}) == set()


def test_control_keys_are_ignored():
    # nexus_position_sizes also carries non-dict control keys (floats/bools) like
    # _cash_reserve_floor_pct and the enable flag itself — never symbols.
    nps = {
        "_cash_reserve_floor_pct": 0.10,
        "_momentum_partial_trim_execution_enabled": True,
        "CAR": {"sell_fraction": 0.4},
    }
    assert missing(nps, set(), {"CAR": 2.68}) == {"CAR"}


def test_already_in_expanded_is_skipped():
    # If the name is already in expanded_symbols it reaches _sell_first on its
    # own — nothing to inject.
    assert missing({"FLY": {"sell_fraction": 0.4}}, {"FLY"}, {"FLY": 24.0}) == set()


def test_not_held_is_skipped():
    # A trim hint for a position we don't actually hold (0 shares) is a no-op.
    assert missing({"CAR": {"sell_fraction": 0.4}}, set(), {"CAR": 0.0}) == set()
    assert missing({"CAR": {"sell_fraction": 0.4}}, set(), {}) == set()


def test_zero_and_negative_fraction_excluded():
    assert missing({"X": {"sell_fraction": 0.0}}, set(), {"X": 5.0}) == set()
    assert missing({"X": {"sell_fraction": -0.2}}, set(), {"X": 5.0}) == set()


def test_multiple_names_mixed():
    nps = {
        "CAR": {"sell_fraction": 0.4},          # include
        "TOYO": {"sell_fraction": 0.6},         # include
        "FLY": {"sell_fraction": 0.4},          # already expanded -> skip
        "GOOGL": {"buy_cash": 500.0},           # buy -> skip
        "MARA": {"sell_fraction": 1.0},         # full sell -> skip
        "_buy_price_floor": 5.0,                # control key -> skip
    }
    held = {"CAR": 2.68, "TOYO": 42.0, "FLY": 24.0, "MARA": 7.9}
    assert missing(nps, {"FLY"}, held) == {"CAR", "TOYO"}


# --- _partial_trim_syms (the set kept alive through the allowed_syms score filter) ---

def test_partial_trim_syms_selects_partials_only():
    nps = {
        "CAR": {"sell_fraction": 0.4},          # partial -> in
        "TOYO": {"sell_fraction": 0.6},         # partial -> in
        "MARA": {"sell_fraction": 1.0},         # full -> out
        "GOOGL": {"buy_cash": 500.0},           # buy -> out
        "X": {"sell_fraction": 0.0},            # zero -> out
        "_cash_reserve_floor_pct": 0.10,        # control key -> out
        "_momentum_partial_trim_execution_enabled": True,  # flag -> out
    }
    assert partial_trim_syms(nps) == {"CAR", "TOYO"}


def test_partial_trim_syms_empty():
    assert partial_trim_syms({}) == set()
    assert partial_trim_syms(None) == set()


def test_missing_is_subset_of_partial_trim_syms():
    # The execution-order injection can only ever target partial-trim names.
    nps = {"CAR": {"sell_fraction": 0.4}, "MARA": {"sell_fraction": 1.0}}
    held = {"CAR": 2.68, "MARA": 7.9}
    assert missing(nps, set(), held) <= partial_trim_syms(nps)
