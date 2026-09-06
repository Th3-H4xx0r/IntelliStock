"""Bounded winner-add decisions and actual-fill accounting, without a backtest."""
from datetime import datetime, timezone

from outlier_sleeve import winner_add_orders
from strategy_eb import session_ordinal
import strategies.outlier_sleeve as mod
from portfolio_emulator import PortfolioEmulator
from test_outlier_sleeve_run_once import cfg, seed, DECIDES

SESSION = "2026-06-03"


def inputs():
    slot = {"entry_px": 50, "entry_cost": 500, "entry_ordinal": session_ordinal("2026-01-05"),
            "buy_count": 1, "last_buy_ordinal": session_ordinal("2026-01-05")}
    row = {"close": 100, "hi252": 100, "ret126": 1, "rs_rank": .95,
           "adv20": 5e7, "nominal_close": 100, "n_bars": 300}
    return {"AAA": slot}, {"AAA": row}, {"AAA": 10}


def plan(slots=None, rows=None, positions=None, cash=1000, blocked=(), **over):
    a, b, c = inputs()
    return winner_add_orders(slots if slots is not None else a,
                             rows if rows is not None else b,
                             positions if positions is not None else c,
                             10000, cash, SESSION, cfg(winner_add_enabled=True, **over), blocked)


def test_profitable_aged_near_high_winner_gets_bounded_add():
    assert plan() == {"AAA": 500}
    assert plan(cash=100) == {"AAA": 100}
    assert plan(cash=20) == {}


def test_loser_or_recent_buy_or_exhausted_add_count_cannot_add():
    slots, rows, _ = inputs()
    slots["AAA"]["entry_px"] = 110
    assert plan(slots=slots) == {}
    slots, rows, _ = inputs()
    slots["AAA"]["last_buy_ordinal"] = session_ordinal(SESSION)
    assert plan(slots=slots) == {}
    slots, _, _ = inputs()
    slots["AAA"]["buy_count"] = 3
    assert plan(slots=slots) == {}


def test_drawn_down_or_weak_relative_strength_cannot_add():
    _, rows, _ = inputs()
    rows["AAA"]["hi252"] = 150
    assert plan(rows=rows) == {}
    rows["AAA"].update(hi252=100, rs_rank=.5)
    assert plan(rows=rows) == {}


def test_add_respects_position_cap_and_entire_sleeve_budget():
    assert plan(positions={"AAA": 19}) == {"AAA": 100}
    slots, _, _ = inputs()
    slots["OTHER"] = {"entry_cost": 900}
    assert plan(slots=slots) == {"AAA": 100}
    assert plan(blocked={"AAA"}) == {}


def test_fill_basis_counts_distinct_buy_orders_and_last_add_date():
    trades = [dict(ticker="AAA", action="buy", shares=1, price=50, total=50,
                   timestamp="2026-01-05T21:00:00Z", order_id="entry"),
              dict(ticker="AAA", action="buy", shares=2, price=80, total=160,
                   timestamp="2026-02-02T21:00:00Z", order_id="add"),
              dict(ticker="AAA", action="buy", shares=1, price=80, total=80,
                   timestamp="2026-02-02T21:00:00Z", order_id="add")]
    actual = mod._open_basis(trades, "AAA")
    assert actual["buy_count"] == 2
    assert actual["last_buy_ordinal"] == session_ordinal("2026-02-02")


def test_wrapper_adds_share_actual_cash_with_new_entries(store, monkeypatch):
    monkeypatch.setattr(mod, "store", store)
    seed(store)
    emu = PortfolioEmulator(6000)
    emu.buy("AAA", 5, 50, timestamp=datetime(2026, 1, 5, 21, tzinfo=timezone.utc))
    cache = {mod.SLOTS_KEY: {"AAA": {"proven": True, "below": 0, "last_eval": ""}}}
    out = mod.OutlierSleeve().run_once([], {"AAA": 100}, DECIDES,
             cfg(winner_add_enabled=True), {}, portfolio_emulator=emu, strategy_cache=cache)
    assert out["AAA"] == 1
    assert cache[mod.SLOTS_KEY]["AAA"]["buy_count"] == 1
    assert "AAA" in cache[mod.PENDING_KEY]
    buys = [v["buy_cash"] for v in out["_nexus_position_sizes"].values() if isinstance(v, dict)]
    assert sum(buys) <= emu.get_buying_power(prices={"AAA": 100})
    again = mod.OutlierSleeve().run_once([], {"AAA": 100}, DECIDES,
             cfg(winner_add_enabled=True), {}, portfolio_emulator=emu, strategy_cache=cache)
    assert again.get("_nexus_executable_buys", []) == []


def test_current_mark_blocks_add_even_when_previous_close_has_cap_room():
    slots, rows, _ = inputs()
    actual = winner_add_orders(slots, rows, {"AAA": 19}, 10000, 1000, SESSION,
               cfg(winner_add_enabled=True), prices={"AAA": 120})
    assert actual == {}
