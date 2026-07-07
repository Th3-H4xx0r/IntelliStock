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


def test_catastrophe_exit_only_when_losing_late():
    # Mark collapsed to <=40% of entry -> catastrophe exit ONLY when the side we backed is
    # genuinely behind late (score-confirmed). Fair is irrelevant here (last-resort floor).
    pos = Pos(contracts=10, avg_price_cents=80, current_price_cents=30)
    a = decide(position=pos, live_fair=0.9, yes_ask_cents=85, yes_bid_cents=30,
               caps=CAPS, phase=LIVE, elapsed_min=80, our_goal_margin=-1)
    assert a.kind == "exit" and a.contracts == 10 and "catastrophe" in a.reason


def test_thesis_break_exit_only_when_losing_late():
    # Live fair well below the bid AND behind late (score-confirmed) -> thesis-break exit.
    pos = Pos(contracts=10, avg_price_cents=50, current_price_cents=60)
    a = decide(position=pos, live_fair=0.30, yes_ask_cents=62, yes_bid_cents=60,
               caps=CAPS, phase=LIVE, elapsed_min=80, our_goal_margin=-1)
    assert a.kind == "exit" and "thesis break" in a.reason


def test_spain_bug_pregame_winner_not_stopped_out_on_price_gap():
    # THE REGRESSION: pregame 'Spain to win' bought 12 @ 51c; price gapped to 19c mid-match
    # (<=40% of entry -> old catastrophe stop fired and dumped it at a loss; Spain then won).
    # Now: the score is level/unknown (NOT genuinely losing late) -> HOLD, ride to settlement.
    pos = Pos(contracts=12, avg_price_cents=51, current_price_cents=19)
    a = decide(position=pos, live_fair=0.45, yes_ask_cents=21, yes_bid_cents=19,
               caps=CAPS, phase=LIVE, elapsed_min=55, origin="pregame", our_goal_margin=0)
    assert a.kind == "hold"


def test_price_collapse_without_score_confirmation_holds():
    # Even for an in-play scalp: a price collapse with NO adverse score (unknown margin)
    # holds through the noise — no price-only stop-out.
    pos = Pos(contracts=12, avg_price_cents=51, current_price_cents=19)
    a = decide(position=pos, live_fair=0.45, yes_ask_cents=21, yes_bid_cents=19,
               caps=CAPS, phase=LIVE, elapsed_min=80, our_goal_margin=None)
    assert a.kind == "hold"


def test_pregame_holds_winner_no_take_profit():
    # A pregame conviction bet in profit is NOT scalped out (held to settlement); only
    # in-play scalps take profit on an overshoot.
    pos = Pos(contracts=10, avg_price_cents=40, current_price_cents=68)
    a = decide(position=pos, live_fair=0.55, yes_ask_cents=70, yes_bid_cents=67,
               caps=CAPS, phase=LIVE, elapsed_min=60, origin="pregame", our_goal_margin=1)
    assert a.kind == "hold"


def test_pregame_exits_when_genuinely_losing_late():
    # Even a pregame bet bails when the backed side is really behind late (score-confirmed)
    # and the price has collapsed — salvage value rather than ride a lost bet to zero.
    pos = Pos(contracts=12, avg_price_cents=51, current_price_cents=19)
    a = decide(position=pos, live_fair=0.15, yes_ask_cents=21, yes_bid_cents=19,
               caps=CAPS, phase=LIVE, elapsed_min=85, origin="pregame", our_goal_margin=-2)
    assert a.kind == "exit"   # thesis-break or catastrophe — salvage a genuinely lost bet


def test_take_profit_scales_out_on_overshoot_with_thesis_intact():
    # In profit, thesis INTACT (fair 0.55 >= entry 0.40), market overshot fair by >8c
    # -> scale out HALF (not a full thesis-break exit).
    pos = Pos(contracts=10, avg_price_cents=40, current_price_cents=68)
    a = decide(position=pos, live_fair=0.55, yes_ask_cents=70, yes_bid_cents=67,
               caps=CAPS, phase=LIVE, elapsed_min=60)
    assert a.kind == "reduce" and a.contracts == 5 and "take-profit" in a.reason


def test_order_size_range_sizes_in_play():
    # order-size range $5-$10; strong live edge 0.30 (fair .80 vs ask .50) -> top ($10).
    caps = InPlayCaps(bankroll_cents=5000, order_size_min_cents=500, order_size_max_cents=1000,
                      max_contracts_per_market=10000, inplay_exposure_frac=1.0)
    a = decide(position=None, live_fair=0.80, yes_ask_cents=50, yes_bid_cents=49,
               caps=caps, phase=LIVE, elapsed_min=30, allow_open=True)
    assert a.kind == "open" and a.contracts == 20   # $10 / 50c


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


def test_unknown_phase_no_open_and_no_price_only_exit():
    # Flat + value but not live and not move-confirmed -> hold (no open).
    a = decide(position=None, live_fair=0.70, yes_ask_cents=50, yes_bid_cents=49,
               caps=CAPS, phase=UNKNOWN, elapsed_min=None, allow_open=False)
    assert a.kind == "hold"
    # A held position whose PRICE collapsed but with no adverse score confirmation HOLDS
    # (the fix: no price-only stop-out, in any phase).
    pos = Pos(contracts=10, avg_price_cents=80, current_price_cents=30)
    b = decide(position=pos, live_fair=0.9, yes_ask_cents=85, yes_bid_cents=30,
               caps=CAPS, phase=UNKNOWN, elapsed_min=None, allow_open=False)
    assert b.kind == "hold"


def test_hold_when_no_value():
    a = decide(position=None, live_fair=0.50, yes_ask_cents=55, yes_bid_cents=54,
               caps=CAPS, phase=LIVE, elapsed_min=30, allow_open=True)
    assert a.kind == "hold"
