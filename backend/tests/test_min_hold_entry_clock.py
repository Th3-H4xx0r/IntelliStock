"""The min-hold clock must start on the decision to open, not on the fill.

Regression cover for a gate that was inert in production for its entire life.
Execution is next-event in BOTH modes: `execute_signal` records the order and
returns an accepted-but-unfilled submission, and `_positions` is not touched
until a later bar. The old stamper read the post-fill position, so a new-name
buy saw qty == 0 and *popped* the symbol instead of stamping it; `min_hold` then
took its fail-open `no_entry_timestamp` branch and blocked nothing.

bt 216767 is the empirical proof this guards: a 106-day window under a 120-day
floor, which should have permitted zero satellite sells, sold BOIL after 5 days,
OLMA after 7 and CAE after 9.

Extracted by AST rather than imported — broker.py pulls the live trading runtime
in at import time.
"""
import ast
import os
import types

BROKER = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "broker.py")

WANTED = {"_POSITION_ENTRY_TS", "_min_hold_note_position", "_iso_utc"}


def _load():
    with open(BROKER, "r") as fh:
        src = fh.read()
    tree = ast.parse(src)
    keep = []
    for node in tree.body:
        if getattr(node, "name", None) in WANTED:
            keep.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(getattr(t, "id", None) in WANTED for t in targets):
                keep.append(node)
    mod = types.ModuleType("_mh")
    exec(compile(ast.Module(body=keep, type_ignores=[]), BROKER, "exec"), mod.__dict__)
    return mod


MOD = _load()


class _Emu:
    """A next-event emulator: submitting does NOT move _positions."""

    def __init__(self, positions=None):
        self._positions = dict(positions or {})


def _fresh():
    MOD._POSITION_ENTRY_TS.clear()
    return MOD._POSITION_ENTRY_TS


def test_buy_stamps_even_though_nothing_has_filled():
    """THE bug. New-name buy, position still empty, clock must start anyway."""
    ts = _fresh()
    MOD._min_hold_note_position("SNDK", _Emu({}), "2026-01-06T15:00:00Z", is_buy=True)
    assert "SNDK" in ts, (
        "buy did not start the holding clock — min_hold will fail open and the "
        "position is sellable on the next bar")


def test_old_behaviour_would_have_failed_this():
    """Pin the exact defect: position-derived stamping drops a pending buy."""
    ts = _fresh()
    MOD._min_hold_note_position("SNDK", _Emu({}), "2026-01-06T15:00:00Z")  # is_buy=None
    assert "SNDK" not in ts, (
        "the position-derived path should still be position-derived; if this "
        "now stamps, the compatibility branch changed meaning")


def test_adding_to_a_winner_does_not_reset_the_clock():
    ts = _fresh()
    MOD._min_hold_note_position("MU", _Emu({}), "2026-01-01T15:00:00Z", is_buy=True)
    first = ts["MU"]
    MOD._min_hold_note_position("MU", _Emu({"MU": 10}), "2026-03-01T15:00:00Z", is_buy=True)
    assert ts["MU"] == first, "a top-up handed the position a fresh min_hold window"


def test_sell_in_flight_does_not_stamp():
    """The second-order bug: a sell used to stamp at the moment of first sale."""
    ts = _fresh()
    # position still present because the sell has not settled
    MOD._min_hold_note_position("CAE", _Emu({"CAE": 40}), "2026-01-21T15:00:00Z", is_buy=False)
    assert "CAE" not in ts, "an unfilled sell created an entry timestamp"


def test_sell_keeps_the_clock_running_until_the_exit_settles():
    ts = _fresh()
    MOD._min_hold_note_position("CAE", _Emu({}), "2026-01-01T15:00:00Z", is_buy=True)
    opened = ts["CAE"]
    MOD._min_hold_note_position("CAE", _Emu({"CAE": 40}), "2026-01-21T15:00:00Z", is_buy=False)
    assert ts.get("CAE") == opened, "in-flight sell disturbed the entry stamp"


def test_settled_full_exit_clears_so_reentry_starts_fresh():
    ts = _fresh()
    MOD._min_hold_note_position("NVO", _Emu({}), "2026-01-01T15:00:00Z", is_buy=True)
    MOD._min_hold_note_position("NVO", _Emu({}), "2026-01-28T15:00:00Z", is_buy=False)
    assert "NVO" not in ts, "a settled exit left a stale clock behind"
    MOD._min_hold_note_position("NVO", _Emu({}), "2026-03-01T15:00:00Z", is_buy=True)
    assert ts["NVO"] == MOD._iso_utc("2026-03-01T15:00:00Z"), (
        "re-entry inherited the age of the closed position")


def test_symbol_is_normalised():
    ts = _fresh()
    MOD._min_hold_note_position("  sndk ", _Emu({}), "2026-01-06T15:00:00Z", is_buy=True)
    assert "SNDK" in ts


def test_blank_symbol_is_ignored():
    ts = _fresh()
    MOD._min_hold_note_position("", _Emu({}), "2026-01-06T15:00:00Z", is_buy=True)
    MOD._min_hold_note_position(None, _Emu({}), "2026-01-06T15:00:00Z", is_buy=True)
    assert not ts


def test_broken_emulator_does_not_raise():
    _fresh()
    MOD._min_hold_note_position("X", object(), "2026-01-06T15:00:00Z", is_buy=True)
    MOD._min_hold_note_position("X", None, "2026-01-06T15:00:00Z", is_buy=False)


def test_call_site_passes_the_side():
    """A correct helper is useless if the one caller still omits is_buy."""
    with open(BROKER, "r") as fh:
        src = fh.read()
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_min_hold_note_position"]
    assert calls, "no call site found — did the helper get renamed?"
    for c in calls:
        assert any(k.arg == "is_buy" for k in c.keywords), (
            f"call at line {c.lineno} does not pass is_buy; under next-event "
            "execution the position cannot tell a buy from a sell")
