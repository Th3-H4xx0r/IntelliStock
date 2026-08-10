"""fix-runt-leak — the exec floor must measure what the emulator will FUND.

THE DEFECT (bt 676939, +14.65%, 2026-01-01..03-01, `min_position_nav_pct`=0.06
= a ~$360-400 floor on this book). Two NEW names opened as runts anyway:

    2026-01-06  Buy gate inputs for AVY  ... cash_to_use=$860.36 -> PASS
                [execution] FILL BUY AVY  qty=0.26066930 price=181.685112  = $47.36
    2026-02-04  Buy gate inputs for AMZN ... cash_to_use=$613.78 -> PASS
                [execution] FILL BUY AMZN qty=0.42832162 price=238.524980  = $102.17

Neither was an ADD (AVY `initial_buy`, AMZN `backfill_queue_buy`, `open_pos=5`
and `7` — the held-name exemption correctly did not apply), and neither took an
exotic lane: both went through the one gate every buy passes.

WHY THEY PASSED. The floor tested `cash_to_use`, which is what the broker ASKS
for. What opens the position is what the emulator FUNDS:

    amount_to_use = min(cash_per_trade, self.get_buying_power(reserved_cash))
                                            portfolio_emulator.py:1489

`reserved_cash` is the sum of BUY reservations already in flight on this same
tick — the SPY index-core leg is submitted first — and `get_buying_power` also
nets out the unsettled (T+1) slice of recent sells. The gate reads `get_cash()`
and sees neither, so it passed on $860.36 and the emulator opened $47.36.

Reconstructed to the dollar off the log:

    AVY   $1,680.42 cash  - $83.94 unsettled (5% of the 01-05 SPY sale $1,678.76)
                          - $1,548.85 in-flight SPY core buy      = $47.63 fundable
    AMZN    $618.21 cash  - $30.13 unsettled (5% of the 02-03 AXTI sale $602.60)
                          -   $485.40 in-flight SPY core buy      = $102.68 fundable

The emulator already documents this exact class of failure at
portfolio_emulator.py:1480 for the OTHER direction (bt 613166: gate $805.24 ->
fill $87.45). This is the same clamp, seen from the floor's side.

NOT A DEFECT, for the record: bt 676939's AXTI ($686.86) and GLUE ($770.19)
were FULL-SIZE 11-13%-of-NAV positions. $84.30 and $46.70 are their realized
LOSSES, not their sizes. The runt leak is AVY + AMZN only.

These tests drive broker.py's OWN gate against a REAL PortfolioEmulator. They
fail without the fix, and the ABSENCE of `min_position_nav_pct` must leave
every decision byte-identical — asserted first and last.
"""
import ast
import os
import re
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import (  # noqa: E402
    ExecutionCostModel,
    NextEventExecutionSimulator,
)

# ── broker.py's real functions ────────────────────────────────────────────────
# AST-extracted because broker.py argparses and opens sockets at module scope
# (same pattern as test_core_sleeve_wiring.py / test_core_funding_release_reserve.py).
_BROKER_PY = os.path.join(_backend, "broker.py")
_SRC = open(_BROKER_PY, encoding="utf-8").read()
_TREE = ast.parse(_SRC)
_WANTED = {
    "_core_sleeve_cfg_raw",
    "_exec_min_position_floor",
    "_exec_fundable_amount",
    "_exec_min_position_skips",
    "_exec_min_position_gate",
}
_WANTED_CONSTS = {"_EXEC_MIN_POSITION_USD"}
_ns = {}
for _node in _TREE.body:
    if isinstance(_node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in _WANTED_CONSTS
            for t in _node.targets):
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
for _node in _TREE.body:
    if isinstance(_node, ast.FunctionDef) and _node.name in _WANTED:
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "broker.py", "exec"), _ns)
for _name in _WANTED | _WANTED_CONSTS:
    assert _name in _ns, f"failed to extract {_name} from broker.py"
b = types.SimpleNamespace(**{k: v for k, v in _ns.items() if not k.startswith("__")})


ON = {"min_position_nav_pct": 0.06}
OFF = {}                       # the key absent — every run before 89e71f3


def _specs(cfg):
    """What `_cached_strategies` looks like to `_core_sleeve_cfg_raw`."""
    return [{"strategy": "graph_nexus_analysis", "config": dict(cfg or {})}]


def _legacy_skips(decision, cash_to_use, cash_per_trade, floor, held):
    """The rule as it stood BEFORE this fix: measured against the REQUEST.

    Kept verbatim so every test below can state the DELTA rather than assert a
    bare truth. A test that passes under both rules is not testing this fix.
    """
    if decision != 1 or held:
        return False
    hard = floor > 50.0
    return cash_to_use < floor and (hard or cash_to_use < cash_per_trade)


# ── a real emulator in bt 676939's exact state ────────────────────────────────
# Zero-cost model: the arithmetic under test is cash accounting, and a cost
# model would blur the reservation against the fill by a few bps and hide it.
_FREE = ExecutionCostModel(
    version="test-zero-cost",
    spread_bps=0.0,
    slippage_bps=0.0,
    fee_bps=0.0,
    latency=timedelta(0),
)
# The rest of the alpha book (GLD/CPER/SBLK/XOM/BA/...) as one synthetic line,
# so `get_portfolio_value` returns the run's real NAV and the floor is the
# run's real floor. Its composition is irrelevant to this gate; its VALUE is not.
_BOOK = "BOOK"
_BOOK_PX = 100.0
_PRICES = {_BOOK: _BOOK_PX, "SPY": 690.678055, "AVY": 182.17, "AMZN": 235.25}


def _emulator_at(*, nav, cash, sold_notional, sell_at, sell_px):
    """Cash `cash`, NAV `nav`, and 5% of `sold_notional` still unsettled.

    Built through buy()/sell(), not by poking privates, so the T+1 tranche is
    the one the engine really makes.
    """
    book_notional = nav - cash
    assert book_notional > 0 and sold_notional <= cash
    emu = PortfolioEmulator(
        cash + book_notional,
        execution_simulator=NextEventExecutionSimulator(_FREE),
        execution_delay=timedelta(hours=1),
        equity_cost_model=_FREE,
    )
    assert emu.buy(_BOOK, book_notional / _BOOK_PX, _BOOK_PX,
                   timestamp=sell_at - timedelta(days=2))
    qty = sold_notional / sell_px
    assert emu.buy("SPY", qty, sell_px, timestamp=sell_at - timedelta(hours=1))
    assert emu.sell("SPY", qty, sell_px, timestamp=sell_at)
    assert emu.get_cash() == pytest.approx(cash, abs=0.01)
    assert emu.get_portfolio_value(_PRICES) == pytest.approx(nav, abs=0.01)
    return emu


# bt 676939 NAV around these ticks, read off the neighbouring SKIP lines
# (`min $365` on 01-05, `min $366` on 01-07, `min $396` on 01-28).
AVY_NAV = 6090.0          # -> floor $365.40
AMZN_NAV = 6600.0         # -> floor $396.00
AVY_T = datetime(2026, 1, 6, 15, tzinfo=timezone.utc)
AMZN_T = datetime(2026, 2, 4, 15, tzinfo=timezone.utc)


def _avy_tick():
    """bt 676939, 2026-01-06 15:00 — the tick AVY opened at $47.36."""
    emu = _emulator_at(
        nav=AVY_NAV,
        cash=1680.42,
        sold_notional=1678.76,                                  # FILL SELL SPY 01-05
        sell_at=datetime(2026, 1, 5, 16, tzinfo=timezone.utc),
        sell_px=686.743763,
    )
    # The index-core leg is emitted FIRST on this tick and books its reservation.
    emu.execute_signal("SPY", 1, 690.678055, timestamp=AVY_T,
                       cash_per_trade=1548.85, order_source="core_sleeve")
    return emu


def _amzn_tick():
    """bt 676939, 2026-02-04 15:00 — the tick AMZN opened at $102.17."""
    emu = _emulator_at(
        nav=AMZN_NAV,
        cash=618.21,
        sold_notional=602.60,                                   # FILL SELL AXTI 02-03
        sell_at=datetime(2026, 2, 3, 20, tzinfo=timezone.utc),
        sell_px=19.380517,
    )
    emu.execute_signal("SPY", 1, 690.166887, timestamp=AMZN_T,
                       cash_per_trade=485.40, order_source="core_sleeve")
    return emu


def _gate(emu, cfg, *, symbol, cash_to_use, cash_per_trade, decision=1):
    """broker.py's real gate, called exactly as the buy block calls it.
    Returns (skip, floor, fundable, held)."""
    return b._exec_min_position_gate(
        decision, symbol, cash_to_use, cash_per_trade,
        _specs(cfg), emu, _PRICES)


# ── 0. the contract that must hold when the key is absent ────────────────────

def test_absent_key_is_byte_identical_on_the_real_gate():
    """No `min_position_nav_pct` -> the historical $50 rule, and the emulator's
    clamp is never consulted. Driven through the REAL gate on the REAL emulator
    state that would otherwise trip it."""
    emu = _avy_tick()
    skip, floor, fundable, held = _gate(
        emu, OFF, symbol="AVY", cash_to_use=860.36, cash_per_trade=860.36)
    assert floor == pytest.approx(50.0)
    assert fundable == pytest.approx(860.36), \
        "the clamp must not be read when the key is absent"
    assert held is False
    assert skip is False
    assert skip is _legacy_skips(1, 860.36, 860.36, 50.0, False)


def test_absent_key_reproduces_the_whole_legacy_truth_table():
    emu = PortfolioEmulator(
        6000.0, execution_simulator=NextEventExecutionSimulator(_FREE),
        execution_delay=timedelta(hours=1), equity_cost_model=_FREE)
    cases = [
        (1, 32.41, 900.00),   # truncated sub-$50 -> refused
        (1, 32.55, 32.55),    # untruncated sub-$50 -> the historical hole, kept
        (1, 49.99, 49.99),
        (1, 50.00, 900.00),
        (1, 860.36, 860.36),
        (-1, 10.00, 900.00),  # sells never touch this gate
        (0, 10.00, 900.00),
    ]
    for decision, ctu, cpt in cases:
        skip, floor, _f, _h = _gate(
            emu, OFF, symbol="ZZZ", cash_to_use=ctu,
            cash_per_trade=cpt, decision=decision)
        assert floor == pytest.approx(50.0)
        assert skip is _legacy_skips(decision, ctu, cpt, 50.0, False), (decision, ctu, cpt)


# ── 1. AVY — the $47.36 runt on a gate that read $860.36 ──────────────────────

def test_AVY_the_emulator_would_only_have_funded_47_dollars():
    """Before asserting the fix, prove the premise: the gate's number is not
    the position's size. This is the measurement the old rule was missing."""
    emu = _avy_tick()
    assert emu.get_cash() == pytest.approx(1680.42, abs=0.01)
    assert b._exec_fundable_amount(emu, 860.36) == pytest.approx(47.63, abs=0.05)


def test_AVY_a_new_name_below_the_floor_no_longer_opens():
    emu = _avy_tick()
    skip, floor, fundable, held = _gate(
        emu, ON, symbol="AVY", cash_to_use=860.36, cash_per_trade=860.36)
    assert floor == pytest.approx(365.40)
    assert held is False
    assert fundable == pytest.approx(47.63, abs=0.05)
    assert _legacy_skips(1, 860.36, 860.36, floor, held) is False, \
        "the old rule admitted it — that is the leak"
    assert skip is True


def test_AVY_the_runt_really_does_fill_at_47_dollars():
    """End-to-end through the real emulator: submit the buy the old rule let
    through and read back what was actually committed. 0.8% of the book,
    holding one of `max_positions` slots."""
    emu = _avy_tick()
    emu.execute_signal("AVY", 1, 182.17, timestamp=AVY_T,
                       cash_per_trade=860.36, order_source="main_signal")
    committed = [v for k, v in emu._execution_cash_reservations.items()
                 if k.endswith("-AVY")]
    assert committed, "the emulator accepted the order"
    assert committed[0] == pytest.approx(47.63, abs=0.05)
    assert committed[0] / AVY_NAV < 0.01          # the log's 0.8% of NAV


# ── 2. AMZN — the $102.17 runt on a gate that read $613.78 ────────────────────

def test_AMZN_a_new_name_below_the_floor_no_longer_opens():
    emu = _amzn_tick()
    skip, floor, fundable, held = _gate(
        emu, ON, symbol="AMZN", cash_to_use=613.78, cash_per_trade=613.78)
    assert floor == pytest.approx(396.0)
    assert fundable == pytest.approx(102.68, abs=0.05)
    assert _legacy_skips(1, 613.78, 613.78, floor, held) is False
    assert skip is True


# ── 3. the ADD must survive the fix ──────────────────────────────────────────

def test_an_ADD_to_a_held_name_still_opens_even_when_clamped():
    """The point of the exemption: the floor protects a `max_positions` SLOT
    and an add takes no slot. bt 571147 refused SNDK's winner-add ($216) and
    bt 427197 WDC's ($586) before it existed — measuring the FUNDABLE amount
    instead of the request must not resurrect that."""
    emu = _avy_tick()
    # the same clamped tick, but the name is already in the book
    skip, floor, fundable, held = _gate(
        emu, ON, symbol=_BOOK, cash_to_use=860.36, cash_per_trade=860.36)
    assert held is True
    assert fundable < floor, "clamped well under the floor, and still funded"
    assert skip is False


def test_a_new_name_at_the_same_size_is_refused_on_the_same_tick():
    """The two halves of the contract, side by side on one emulator state."""
    emu = _avy_tick()
    add, *_ = _gate(emu, ON, symbol=_BOOK,
                    cash_to_use=216.0, cash_per_trade=840.0)
    new, *_ = _gate(emu, ON, symbol="SNDK",
                    cash_to_use=216.0, cash_per_trade=840.0)
    assert (add, new) == (False, True)


# ── 4. the fix must not refuse anything it should fund ───────────────────────

def test_an_unclamped_full_size_buy_is_untouched():
    """No in-flight reservation, nothing unsettled -> fundable == request, and
    the decision is exactly what it was before this fix."""
    emu = PortfolioEmulator(
        6000.0, execution_simulator=NextEventExecutionSimulator(_FREE),
        execution_delay=timedelta(hours=1), equity_cost_model=_FREE)
    for ctu, cpt in ((839.97, 839.97), (360.0, 840.0), (5000.0, 5000.0)):
        skip, floor, fundable, _h = _gate(
            emu, ON, symbol="XOM", cash_to_use=ctu, cash_per_trade=cpt)
        assert floor == pytest.approx(360.0)
        assert fundable == pytest.approx(ctu)
        assert skip is False
        assert skip is _legacy_skips(1, ctu, cpt, floor, False)


def test_a_clamp_that_still_clears_the_floor_is_funded():
    """The gate refuses runts, not clamps. A buy the emulator will cut from
    $860 to $500 is still a real position and must go through."""
    emu = _emulator_at(
        nav=6090.0, cash=1680.42, sold_notional=100.0,
        sell_at=datetime(2026, 1, 5, 16, tzinfo=timezone.utc),
        sell_px=686.743763)
    emu.execute_signal("SPY", 1, 690.678055, timestamp=AVY_T,
                       cash_per_trade=1175.0, order_source="core_sleeve")
    skip, floor, fundable, _h = _gate(
        emu, ON, symbol="AVY", cash_to_use=860.36, cash_per_trade=860.36)
    assert floor < fundable < 860.36
    assert skip is False


# ── 5. the fundable read must never itself break a trade ─────────────────────

def test_a_broker_without_the_clamp_takes_the_identity_path():
    """`get_buying_power` exists only on PortfolioEmulator. Every LIVE adapter
    must fall through unchanged, or this backtest fix would silently resize
    real orders."""
    class _LiveAdapter:
        _positions = {}

        def get_cash(self):
            return 1680.42

        def get_portfolio_value(self, _prices):
            return 6090.0

    assert b._exec_fundable_amount(_LiveAdapter(), 860.36) == pytest.approx(860.36)
    assert b._exec_fundable_amount(None, 860.36) == pytest.approx(860.36)
    skip, floor, fundable, _h = _gate(
        _LiveAdapter(), ON, symbol="AVY",
        cash_to_use=860.36, cash_per_trade=860.36)
    assert fundable == pytest.approx(860.36)
    assert floor == pytest.approx(365.40)
    assert skip is False


def test_a_raising_emulator_falls_back_to_the_request():
    class _Angry:
        _execution_cash_reservations = {"x": 1.0}
        _positions = {}

        def get_buying_power(self, reserved=0.0, **_kw):
            raise RuntimeError("boom")

        def get_portfolio_value(self, _prices):
            raise RuntimeError("boom")

    assert b._exec_fundable_amount(_Angry(), 860.36) == pytest.approx(860.36)
    # and the composed gate degrades to the historical $50, never to a raise
    skip, floor, fundable, _h = _gate(
        _Angry(), ON, symbol="AVY", cash_to_use=32.41, cash_per_trade=900.0)
    assert floor == pytest.approx(50.0)
    assert skip is True


def test_malformed_inputs_never_raise():
    assert b._exec_fundable_amount(None, None) == pytest.approx(0.0)
    assert b._exec_fundable_amount(None, "x") == pytest.approx(0.0)
    for bad in ({"min_position_nav_pct": "x"}, None, {"min_position_nav_pct": -1}):
        assert b._exec_min_position_floor(bad, 6000.0) == pytest.approx(50.0)
    assert b._exec_min_position_floor(ON, 0.0) == pytest.approx(50.0)
    assert b._exec_min_position_floor(ON, None) == pytest.approx(50.0)
    assert b._exec_min_position_floor(ON, 60000.0) == pytest.approx(3600.0)
    assert b._exec_min_position_floor(ON, 6000.0) == pytest.approx(360.0)


def test_it_still_agrees_with_the_allocator_floor():
    """Both ends must use the same number or one admits what the other
    refuses, which is how the runts got through in the first place."""
    for nav in (6000.0, 6090.0, 6600.0):
        assert b._exec_min_position_floor(ON, nav) == pytest.approx(
            max(100.0, nav * ON["min_position_nav_pct"]))


# ── 6. the gate must actually be wired into the buy path ─────────────────────

def test_the_buy_path_calls_the_gate_and_not_a_copy():
    """A gate the tests reach only through a MIRROR is how this leak survived
    two fixes: the mirror agreed with itself. Pin the real call site."""
    block = _SRC[_SRC.index("# V3: Execution-time min position size check for buys."):]
    block = block[:block.index("Gate skips reported back")]
    assert "_exec_min_position_gate(" in block
    call = re.search(r"_exec_min_position_gate\(\s*([^)]*)\)", block, re.S).group(1)
    for arg in ("decision", "symbol", "cash_to_use", "cash_per_trade",
                "_cached_strategies", "portfolio_emulator", "prices"):
        assert arg in call, (arg, call)
    # the old expression, which measured the REQUEST, must be gone
    assert "cash_to_use < _exec_min_pos" not in block


def test_the_skip_log_line_keeps_its_grepable_shape():
    """Five sessions of investigation docs grep `SKIP BUY X — ... < min $N
    (allocated $M)`. Widening the message must not break the ledger."""
    block = _SRC[_SRC.index("# V3: Execution-time min position size check for buys."):]
    block = block[:block.index("Gate skips reported back")]
    assert 'f"SKIP BUY {symbol} — {_emp_what} < min ${_exec_min_pos:.0f} ' \
           '(allocated ${cash_per_trade:.2f})"' in block
    assert 'f"cash_to_use ${cash_to_use:.2f}"' in block
