from dataclasses import dataclass

from kalshi.live.live_decision import InPlayCaps, decide
from kalshi.live.match_clock import LIVE, UNKNOWN


@dataclass
class Pos:
    contracts: int
    avg_price_cents: float
    current_price_cents: float


CAPS = InPlayCaps(bankroll_cents=100_000)   # $1000 so sizing is non-zero


def test_open_on_value_when_flat_and_allowed():
    a = decide(position=None, live_fair=0.70, yes_ask_cents=50, yes_bid_cents=49,
               caps=CAPS, phase=LIVE, elapsed_min=30, allow_open=True)
    assert a.kind == "open" and a.contracts == 50   # clamped to max_contracts_per_market


def test_add_when_holding_and_value():
    pos = Pos(contracts=10, avg_price_cents=40, current_price_cents=55)
    a = decide(position=pos, live_fair=0.70, yes_ask_cents=50, yes_bid_cents=49,
               caps=CAPS, phase=LIVE, elapsed_min=30, adds_so_far=0, allow_open=True)
    assert a.kind == "add" and a.contracts == 40   # 50 cap - 10 held


def test_stop_loss_exit():
    pos = Pos(contracts=10, avg_price_cents=80, current_price_cents=30)
    a = decide(position=pos, live_fair=0.9, yes_ask_cents=85, yes_bid_cents=30,
               caps=CAPS, phase=LIVE, elapsed_min=70)
    assert a.kind == "exit" and a.contracts == 10 and "stop-loss" in a.reason


def test_thesis_break_exit():
    pos = Pos(contracts=10, avg_price_cents=50, current_price_cents=60)
    a = decide(position=pos, live_fair=0.30, yes_ask_cents=62, yes_bid_cents=60,
               caps=CAPS, phase=LIVE, elapsed_min=70)
    assert a.kind == "exit" and "thesis break" in a.reason


def test_take_profit_scales_out_on_overshoot_with_thesis_intact():
    # In profit, thesis INTACT (fair 0.55 >= entry 0.40), market overshot fair by >8c
    # -> scale out HALF (not a full thesis-break exit).
    pos = Pos(contracts=10, avg_price_cents=40, current_price_cents=68)
    a = decide(position=pos, live_fair=0.55, yes_ask_cents=70, yes_bid_cents=67,
               caps=CAPS, phase=LIVE, elapsed_min=60)
    assert a.kind == "reduce" and a.contracts == 5 and "take-profit" in a.reason


def test_take_profit_banks_single_contract():
    # held 1, entry 40c, live fair 0.50 (thesis intact: fair >= entry), mark 60c
    # overshoots fair by >= tp_overshoot (8c) -> should REDUCE (full exit of the 1).
    pos = Pos(contracts=1, avg_price_cents=40, current_price_cents=60)
    a = decide(position=pos, live_fair=0.50, yes_ask_cents=55, yes_bid_cents=60,
               caps=CAPS, phase=LIVE, elapsed_min=70, allow_open=True)
    assert a.kind == "reduce" and a.contracts == 1


def test_no_open_or_add_past_cutoff_minute():
    a = decide(position=None, live_fair=0.70, yes_ask_cents=50, yes_bid_cents=49,
               caps=CAPS, phase=LIVE, elapsed_min=85, allow_open=True)
    assert a.kind == "hold" and "no-add" in a.reason


def test_max_adds_blocks_further_adds():
    pos = Pos(contracts=10, avg_price_cents=40, current_price_cents=55)
    a = decide(position=pos, live_fair=0.70, yes_ask_cents=50, yes_bid_cents=49,
               caps=CAPS, phase=LIVE, elapsed_min=30, adds_so_far=3, allow_open=True)
    assert a.kind == "hold" and "max adds" in a.reason


def test_unknown_phase_no_open_without_move_but_exit_allowed():
    # Flat + value but not live and not move-confirmed -> hold (no open).
    a = decide(position=None, live_fair=0.70, yes_ask_cents=50, yes_bid_cents=49,
               caps=CAPS, phase=UNKNOWN, elapsed_min=None, allow_open=False)
    assert a.kind == "hold"
    # But a held position can still be defensively exited in any phase.
    pos = Pos(contracts=10, avg_price_cents=80, current_price_cents=30)
    b = decide(position=pos, live_fair=0.9, yes_ask_cents=85, yes_bid_cents=30,
               caps=CAPS, phase=UNKNOWN, elapsed_min=None, allow_open=False)
    assert b.kind == "exit"


def test_hold_when_no_value():
    a = decide(position=None, live_fair=0.50, yes_ask_cents=55, yes_bid_cents=54,
               caps=CAPS, phase=LIVE, elapsed_min=30, allow_open=True)
    assert a.kind == "hold"
