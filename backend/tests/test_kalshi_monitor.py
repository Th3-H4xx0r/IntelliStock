from kalshi.live.live_decision import InPlayCaps
from kalshi.live.match_clock import LIVE
from kalshi.live.monitor import run_live_step


class FakeClient:
    def __init__(self):
        self.orders = []

    def submit_order(self, **kw):
        self.orders.append(kw)
        return {"order_id": "x"}

    def get_orderbook(self, ticker):
        # bid 58 / ask 62 (top NO bid 38) — spread 4, makeable; depth on both sides
        return {"orderbook_fp": {"yes_dollars": [["0.58", "10"]], "no_dollars": [["0.38", "10"]]}}


CAPS = InPlayCaps(bankroll_cents=100_000)


def _live_match():
    return [{
        "fixture_id": "E1", "home": "A", "away": "B", "phase": LIVE, "elapsed_min": None,
        "model_probs": {"M1": 0.85},
        "markets": [{"market_ticker": "M1", "side": "home", "market_type": "winner",
                     "yes_ask_cents": 62, "yes_bid_cents": 58, "mid_cents": 60}],
    }]


def test_material_move_triggers_a_live_maker_open_and_persists_row():
    fc = FakeClient()
    rows = run_live_step(
        client=fc, live_matches=_live_match(),
        price_history={"M1": [50, 50]},   # +10c move when 60 appended -> material
        adds_by_match={}, positions_by_ticker={}, caps=CAPS, dry_run=False,
        instance_id="i", brokerage_id="b", ts="t", llm=None,
    )
    assert len(fc.orders) == 1
    o = fc.orders[0]
    assert o["action"] == "buy" and o["contracts"] > 0
    assert o["post_only"] is True and o["limit_cents"] == 59   # maker bid INSIDE the spread (skip if not makeable)
    assert rows[0]["decision"] == "placed" and rows[0]["in_play"] is True
    assert rows[0]["live_action"] == "open" and rows[0]["event"] == "up"


def test_goal_driven_move_does_not_open():
    # A material move that coincides with a GOAL (score_changed) is an info shock —
    # the monitor must NOT chase/fade it (only confirmed-clock or narrative moves open).
    fc = FakeClient()
    m = _live_match()
    m[0]["score_known"] = True
    m[0]["score_changed"] = True
    rows = run_live_step(
        client=fc, live_matches=m, price_history={"M1": [50, 50]},  # +10c move
        adds_by_match={}, positions_by_ticker={}, caps=CAPS, dry_run=False,
        instance_id="i", brokerage_id="b", ts="t", llm=None,
    )
    assert fc.orders == []
    assert rows[0]["live_action"] == "hold"


def test_dry_run_records_but_does_not_submit():
    fc = FakeClient()
    rows = run_live_step(
        client=fc, live_matches=_live_match(), price_history={"M1": [50, 50]},
        adds_by_match={}, positions_by_ticker={}, caps=CAPS, dry_run=True,
        instance_id="i", brokerage_id="b", ts="t", llm=None,
    )
    assert fc.orders == []   # paper: nothing submitted
    assert rows[0]["decision"] == "placed" and rows[0]["paper"] is True and rows[0]["in_play"] is True


def test_no_open_without_move_when_clock_unknown():
    # Same-day match, unknown minute (elapsed None), price stable -> no move ->
    # even with model value, the monitor must NOT open (pregame-as-live guard).
    fc = FakeClient()
    rows = run_live_step(
        client=fc, live_matches=_live_match(), price_history={"M1": [64, 64]},
        adds_by_match={}, positions_by_ticker={}, caps=CAPS, dry_run=False,
        instance_id="i", brokerage_id="b", ts="t", llm=None,
    )
    assert fc.orders == []
    assert rows[0]["live_action"] == "hold"


def test_exit_blocked_when_no_bid_to_sell_into():
    from dataclasses import dataclass

    @dataclass
    class Pos:
        market_ticker: str
        contracts: int
        avg_price_cents: float
        current_price_cents: float

    # Held position, stop-loss tripped, but the book has NO bid -> must NOT submit
    # a 0c sell that dumps the position.
    match = [{
        "fixture_id": "E1", "home": "A", "away": "B", "phase": LIVE, "elapsed_min": 70,
        "model_probs": {"M1": 0.2},
        "markets": [{"market_ticker": "M1", "side": "home", "market_type": "winner",
                     "yes_ask_cents": 30, "yes_bid_cents": 0, "mid_cents": 30}],
    }]
    fc = FakeClient()
    rows = run_live_step(
        client=fc, live_matches=match, price_history={"M1": [80, 80]},
        adds_by_match={}, positions_by_ticker={"M1": Pos("M1", 10, 80, 20)},
        caps=CAPS, dry_run=False, instance_id="i", brokerage_id="b", ts="t", llm=None,
    )
    assert fc.orders == []                       # no 0c dump
    assert rows[0]["live_action"] == "exit" and rows[0]["decision"] == "blocked"


def test_llm_tilt_called_on_move():
    seen = {}

    def spy_llm(match, mk, move):
        seen["called"] = (match["fixture_id"], mk["market_ticker"], move.direction)
        return 0.0

    run_live_step(
        client=FakeClient(), live_matches=_live_match(), price_history={"M1": [50, 50]},
        adds_by_match={}, positions_by_ticker={}, caps=CAPS, dry_run=True,
        instance_id="i", brokerage_id="b", ts="t", llm=spy_llm,
    )
    assert seen["called"] == ("E1", "M1", "up")
