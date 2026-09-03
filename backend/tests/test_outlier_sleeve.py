"""Pure decision rules of the outlier sleeve: screen, cadence, exits, sizing, cap."""
import os
import sys
from datetime import datetime, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from outlier_sleeve import (  # noqa: E402
    DEFAULTS, visible_session, screen, should_screen, exit_decisions,
    new_slot_orders, winner_cap_trims,
)


def cfg(**over):
    c = dict(DEFAULTS)
    c["outlier_sleeve_enabled"] = True
    c.update(over)
    return c


def row(symbol, close=100.0, hi252=100.0, ret126=0.5, adv20=5e7, sma200=80.0,
        n_bars=300, rs_rank=0.95):
    return {"symbol": symbol, "close": close, "hi252": hi252, "ret126": ret126,
            "adv20": adv20, "sma200": sma200, "n_bars": n_bars, "rs_rank": rs_rank}


def test_visible_session_is_strictly_earlier_than_the_call_date():
    dates = ["2026-06-01", "2026-06-02", "2026-06-03"]
    # 2026-06-03 20:00Z is 16:00 NY on the 3rd: the 3rd's close is not visible.
    assert visible_session(datetime(2026, 6, 3, 20, 0, tzinfo=timezone.utc), dates) == "2026-06-02"
    # 2026-06-04 01:00Z is 21:00 NY on the 3rd: still the 3rd, still not visible.
    assert visible_session(datetime(2026, 6, 4, 1, 0, tzinfo=timezone.utc), dates) == "2026-06-02"
    # 2026-06-04 13:30Z is 09:30 NY on the 4th: the 3rd is visible now.
    assert visible_session(datetime(2026, 6, 4, 13, 30, tzinfo=timezone.utc), dates) == "2026-06-03"
    assert visible_session(datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc), dates) is None


def test_screen_requires_breakout_and_top_decile_and_liquidity():
    rows = [row("AAA"),                                   # passes
            row("BBB", close=90.0),                       # 10% under the high
            row("CCC", rs_rank=0.80),                     # not top decile
            row("DDD", adv20=1e6),                        # illiquid
            row("EEE", close=2.0, hi252=2.0),             # penny
            row("FFF", n_bars=50),                        # too young
            row("SPY"),                                   # excluded
            row("HELD")]                                  # already held
    out = screen(rows, cfg(confirm_enabled=False), peers={}, held={"HELD"})
    assert out == ["AAA"]


def test_screen_ranks_by_six_month_return_descending():
    rows = [row("LOW", ret126=0.3), row("HIGH", ret126=1.2), row("MID", ret126=0.6)]
    assert screen(rows, cfg(confirm_enabled=False), peers={}, held=set()) == ["HIGH", "MID", "LOW"]


def test_young_listing_uses_its_all_time_high_and_needs_120_sessions():
    rows = [row("NEW", n_bars=150, hi252=100.0, close=99.0)]
    assert screen(rows, cfg(confirm_enabled=False), peers={}, held=set()) == ["NEW"]
    rows = [row("NEW", n_bars=119, hi252=100.0, close=100.0)]
    assert screen(rows, cfg(confirm_enabled=False), peers={}, held=set()) == []


def test_confirmation_needs_a_quarter_of_at_least_five_peers_hot():
    peers = {"AAA": ["P1", "P2", "P3", "P4", "P5", "P6"], "BBB": ["P1", "P2"]}
    rows = [row("AAA"), row("BBB"),
            row("P1", rs_rank=0.80), row("P2", rs_rank=0.80),   # hot (>= 0.75)
            row("P3", rs_rank=0.10), row("P4", rs_rank=0.10),
            row("P5", rs_rank=0.10), row("P6", rs_rank=0.10)]
    # AAA: 2 of 6 hot = 33% >= 25% with >= 5 peers -> confirmed. BBB: only 2 peers -> rejected.
    for r in rows[2:]:
        r["close"] = 50.0  # peers are not themselves at highs; they must not be candidates
    assert screen(rows, cfg(), peers=peers, held=set()) == ["AAA"]


def test_screen_fires_on_wednesday_close_every_other_week():
    c = cfg()
    cache = {}
    assert should_screen("2026-06-03", cache, c) is True        # a Wednesday, nothing prior
    cache["_outlier_last_screen_ordinal"] = 20_000
    assert should_screen("2026-06-04", cache, c) is False       # Thursday
    # Wednesday 2026-06-10 is 5 sessions after 2026-06-03: too soon at n=2
    from strategy_eb import session_ordinal
    cache["_outlier_last_screen_ordinal"] = session_ordinal("2026-06-03")
    assert should_screen("2026-06-10", cache, c) is False
    assert should_screen("2026-06-17", cache, c) is True
    assert should_screen("2026-06-10", cache, cfg(screen_every_n_weeks=1)) is True


def test_exit_on_the_fifth_consecutive_close_below_sma200():
    from strategy_eb import session_ordinal
    slots = {"AAA": {"entry_px": 100.0, "entry_ordinal": session_ordinal("2026-01-05"),
                     "entry_cost": 150.0, "proven": True, "below": 0, "last_eval": ""}}
    below = {"AAA": row("AAA", close=70.0, sma200=80.0)}
    above = {"AAA": row("AAA", close=90.0, sma200=80.0)}
    days = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
    for d in days[:4]:
        assert exit_decisions(slots, below, d, cfg()) == {}
    assert slots["AAA"]["below"] == 4
    # the same session evaluated twice does not double count
    assert exit_decisions(slots, below, days[3], cfg()) == {}
    assert slots["AAA"]["below"] == 4
    assert exit_decisions(slots, above, days[4], cfg()) == {}
    assert slots["AAA"]["below"] == 0
    for d in ["2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11"]:
        exit_decisions(slots, below, d, cfg())
    assert exit_decisions(slots, below, "2026-06-12", cfg()) == {"AAA": "sma"}


def test_time_stop_cuts_a_name_that_never_proved_itself():
    from strategy_eb import session_ordinal
    entry = session_ordinal("2026-01-05")
    slots = {"AAA": {"entry_px": 100.0, "entry_ordinal": entry, "entry_cost": 150.0,
                     "proven": False, "below": 0, "last_eval": ""},
             "BBB": {"entry_px": 100.0, "entry_ordinal": entry, "entry_cost": 150.0,
                     "proven": False, "below": 0, "last_eval": ""}}
    rows = {"AAA": row("AAA", close=105.0, sma200=80.0),      # +5%: never proven
            "BBB": row("BBB", close=120.0, sma200=80.0)}      # +20%: proven, immune
    late = "2026-04-06"   # >= 60 sessions after 2026-01-05
    assert session_ordinal(late) - entry >= 60
    assert exit_decisions(slots, rows, late, cfg()) == {"AAA": "time"}
    assert slots["BBB"]["proven"] is True


def test_new_slots_are_sized_at_one_and_a_half_percent_within_the_sleeve_budget():
    orders = new_slot_orders(["AAA", "BBB", "CCC"], slots={}, nav=10_000.0,
                             cash=10_000.0, cfg=cfg(max_slots=2))
    assert orders == {"AAA": 150.0, "BBB": 150.0}
    # budget = 15% of NAV minus cost basis already committed
    slots = {"OLD": {"entry_px": 1.0, "entry_ordinal": 1, "entry_cost": 1_400.0,
                     "proven": False, "below": 0, "last_eval": ""}}
    orders = new_slot_orders(["AAA", "BBB"], slots=slots, nav=10_000.0,
                             cash=10_000.0, cfg=cfg(max_slots=10))
    assert orders == {"AAA": 100.0}          # only $100 of budget left; BBB gets nothing
    assert new_slot_orders(["AAA"], slots={}, nav=10_000.0, cash=20.0, cfg=cfg()) == {}


def test_winner_cap_trims_only_the_excess_above_thirty_percent():
    slots = {"AAA": {"entry_px": 10.0, "entry_ordinal": 1, "entry_cost": 150.0,
                     "proven": True, "below": 0, "last_eval": ""}}
    trims = winner_cap_trims(slots, positions={"AAA": 40.0}, prices={"AAA": 100.0},
                             nav=10_000.0, cfg=cfg())
    # market value 4,000 = 40% of NAV; cap 30% = 3,000; sell 1,000 / 4,000
    assert trims == {"AAA": 0.25}
    assert winner_cap_trims(slots, positions={"AAA": 20.0}, prices={"AAA": 100.0},
                            nav=10_000.0, cfg=cfg()) == {}
