"""Wrapper tests for Strategy HT: the broker contract."""
import os
import sys
from datetime import datetime, timedelta, timezone

# ONLY backend/ goes on the path (see test_strategy_xs_run_once.py for why).
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.strategy_ht import StrategyHt  # noqa: E402
from strategy_ht import DEFAULTS  # noqa: E402

NOW = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)
PRICES = {"SSO": 100.0, "SPY": 500.0, "BIL": 91.0}


def bars(closes, end_day=None):
    end_day = end_day or datetime(2026, 6, 1, tzinfo=timezone.utc)
    n = len(closes)
    return [{"t": (end_day - timedelta(days=(n - 1 - i))).isoformat(), "c": c}
            for i, c in enumerate(closes)]


def uptrend():      # flat then a decisive break above the +3% band
    return bars([400.0] * 230 + [420.0] * 30)


def downtrend():    # was in, then a decisive break below the -3% band
    return bars([400.0] * 200 + [420.0] * 30 + [380.0] * 30)


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None, prices=None, pending_credit=0.0):
        self._cash = cash
        self._positions = dict(positions or {})
        self._prices = dict(prices or PRICES)
        self._pending_credit = pending_credit

    def get_cash(self):
        return self._cash

    def get_buying_power(self, reserved=0.0, *, prices=None):
        return self._cash + self._pending_credit

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        # like the real emulator, NAV counts unsettled/pending proceeds as cash
        px = prices or self._prices
        return self._cash + self._pending_credit + sum(q * float(px.get(s, 0.0)) for s, q in self._positions.items())


def cfg(**overrides):
    value = dict(DEFAULTS)
    value["strategy_ht_enabled"] = True
    value.update(overrides)
    return value


def data_for(filter_bars):
    out = {"SPY": {"bars": filter_bars}}
    for s in ("SSO", "BIL"):
        out[s] = {"bars": bars([100.0] * 260)}
    return out


def test_the_class_name_matches_the_brokers_camel_case_resolution():
    assert "".join(w.capitalize() for w in "strategy_ht".split("_")) == StrategyHt.__name__


def test_disabled_by_default_emits_nothing():
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, dict(DEFAULTS), {},
                                data=data_for(uptrend()), portfolio_emulator=FakeEmulator())
    assert out == {}


def test_too_little_history_refuses_to_trade():
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {},
                                data=data_for(bars([400.0] * 120)), portfolio_emulator=FakeEmulator())
    assert out == {}


def test_an_uptrend_buys_the_levered_leg_sized_off_nav():
    cache = {}
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache=cache)
    assert out.get("SSO") == 1
    assert "SPY" not in out and "BIL" not in out
    assert out["_nexus_position_sizes"]["SSO"]["buy_cash"] > 9000.0
    assert cache["_strategy_ht_last"]["in_market"] is True
    assert out["_nexus_discovered"] == ["BIL", "SPY", "SSO"]


def test_a_violent_uptrend_dials_exposure_down_into_the_one_x_leg():
    # in-state (far above the MA) but the last 21 days swing +-4%: vol target -> 0.5x
    closes = [300.0] * 200 + [420.0] * 30
    for i in range(30):
        closes.append(closes[-1] * (1.04 if i % 2 else 0.96))
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(bars(closes)),
                                portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache={})
    assert out.get("SSO") is None
    assert out.get("SPY") == 1 and out.get("BIL") == 1
    assert 4000.0 < out["_nexus_position_sizes"]["SPY"]["buy_cash"] < 5100.0


def test_a_crash_day_triggers_the_bounce_even_when_parked():
    closes = [400.0] * 200 + [420.0] * 30 + [380.0] * 29 + [380.0 * 0.94]   # parked, then a -6% close
    cache = {}
    # A daily bar's close is knowable only once its day is over, so the -6% bar
    # must be dated BEFORE the decision day to be visible (the engine sees the
    # crash close at the next session's decision, which is the bounce day).
    crash_bars = bars(closes, end_day=datetime(2026, 5, 31, tzinfo=timezone.utc))
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(bounce_drop=-0.05), {}, data=data_for(crash_bars),
                                portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache=cache)
    assert out.get("SSO") == 1 and "SPY" not in out
    assert cache["_strategy_ht_last"]["bounce"] is True and cache["_strategy_ht_last"]["in_market"] is False
    # off by default: the same crash day just parks
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(crash_bars),
                                portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache={})
    assert out.get("SSO") is None and out.get("SPY") == 1


def test_a_downtrend_parks_in_the_out_book():
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(downtrend()),
                                portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache={})
    assert out.get("SPY") == 1 and out.get("BIL") == 1
    assert out.get("SSO") is None
    sizes = out["_nexus_position_sizes"]
    assert 4000.0 < sizes["SPY"]["buy_cash"] < 5100.0
    assert 4000.0 < sizes["BIL"]["buy_cash"] < 5100.0


def test_a_flip_to_parked_sells_the_whole_levered_leg_with_the_etf_intent():
    emu = FakeEmulator(cash=0.0, positions={"SSO": 100.0})   # $10k of SSO, no cash
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(downtrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out.get("SSO") == -1
    assert out["_nexus_position_sizes"]["SSO"]["sell_fraction"] == 1.0
    assert out["_nexus_action_intents"] == {"SSO": "etf_sell"}
    assert out["_nexus_sell_enforcement"] == ["SSO"]


def test_holding_the_target_already_emits_no_churn():
    emu = FakeEmulator(cash=50.0, positions={"SSO": 99.5})
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out == {}


def test_idle_cash_above_the_drift_band_is_deployed():
    # 91% SSO + 9% cash: the 5% drift band would otherwise leave the cash idle forever
    emu = FakeEmulator(cash=900.0, positions={"SSO": 91.0})
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out.get("SSO") == 1
    assert 800.0 < out["_nexus_position_sizes"]["SSO"]["buy_cash"] <= 900.0


def test_buys_are_sized_off_buying_power_when_the_emulator_credits_pending_sells():
    # no settled cash, but $9,500 of pending-sell credit: the SSO buy is sized against it
    emu = FakeEmulator(cash=0.0, positions={}, pending_credit=9500.0)
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out.get("SSO") == 1
    assert 9000.0 < out["_nexus_position_sizes"]["SSO"]["buy_cash"] <= 9500.0
    assert DEFAULTS["backtest_credit_pending_sell_proceeds"] is True


def test_the_previous_grid_level_is_carried_through_the_cache_while_in_market():
    # in-state, vol just past the 1.5/2.0 midpoint: nearest is 2.0, but a cached 1.5 is held
    closes = [300.0] * 200 + [420.0] * 30
    for i in range(30):
        closes.append(closes[-1] * (1.0068 if i % 2 else 1 / 1.0068))   # ~10.8% vol -> raw ~1.85
    fresh = {}
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(bars(closes)),
                                portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache=fresh)
    assert fresh["_strategy_ht_last"]["exposure"] == 2.0
    carried = {"_strategy_ht_last": {"in_market": True, "exposure": 1.5}}
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(bars(closes)),
                                portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache=carried)
    assert carried["_strategy_ht_last"]["exposure"] == 1.5
    assert out.get("SSO") == 1 and out.get("SPY") == 1          # 1.5x = 50% SSO + 50% SPY
    parked_before = {"_strategy_ht_last": {"in_market": False, "exposure": 1.5}}
    StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(bars(closes)),
                          portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache=parked_before)
    assert parked_before["_strategy_ht_last"]["exposure"] == 2.0   # a fresh entry rounds to nearest


def test_idle_cash_below_the_band_is_left_alone():
    emu = FakeEmulator(cash=300.0, positions={"SSO": 97.0})     # 3% cash
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out == {}


def test_missing_broker_prices_fall_back_to_the_visible_close():
    out = StrategyHt().run_once(["SSO"], {"SPY": 500.0}, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=FakeEmulator(cash=10000.0, prices={"SPY": 500.0}),
                                strategy_cache={})
    assert out.get("SSO") == 1


def test_the_broker_cash_floor_is_zeroed_so_the_whole_book_deploys():
    """Without this the buy gate holds back 10% of starting value on every
    buy (observed live: $1,000 of $9,936 idle on the first OUT tick)."""
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=FakeEmulator(cash=10000.0), strategy_cache={})
    assert out["_nexus_position_sizes"]["_cash_reserve_floor_pct"] == 0.0


# ── live: a flip must not send a stub buy beside its funding sell ──
# strategy-ht-paper, 2026-11-12: OUT (BIL) -> IN. The BIL sell and the buy went
# out one second apart; the buy was sized from $84 of cash, and the broker's
# one-buy-per-symbol-per-day guard then refused the real buy all day.

def _live(**overrides):
    return cfg(_nexus_is_live_mode=True, **overrides)


def test_live_flip_sends_the_sell_and_holds_the_starved_buy():
    emu = FakeEmulator(cash=84.0, positions={"BIL": 109.0})      # ~$9.9k BIL
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, _live(), {}, data=data_for(uptrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out.get("BIL") == -1
    assert "SSO" not in out
    assert "SSO" not in out["_nexus_position_sizes"]
    assert out["_nexus_executable_buys"] == []


def test_live_buy_goes_out_in_full_once_the_sale_cash_has_arrived():
    emu = FakeEmulator(cash=10000.0, positions={})
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, _live(), {}, data=data_for(uptrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out.get("SSO") == 1
    assert out["_nexus_position_sizes"]["SSO"]["buy_cash"] > 9000.0


def test_live_a_leftover_stub_is_topped_up_when_no_sell_is_pending():
    emu = FakeEmulator(cash=9850.0, positions={"SSO": 0.85})     # the 11-12 book
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, _live(), {}, data=data_for(uptrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out.get("SSO") == 1
    assert out["_nexus_position_sizes"]["SSO"]["buy_cash"] > 9000.0


def test_live_short_cash_with_no_sell_still_buys():
    # nothing to wait for: holding here would deadlock the book
    emu = FakeEmulator(cash=3000.0, positions={})
    emu.get_portfolio_value = lambda prices=None: 10000.0
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, _live(), {}, data=data_for(uptrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out.get("SSO") == 1


def test_live_hold_can_be_switched_off():
    emu = FakeEmulator(cash=84.0, positions={"BIL": 109.0})
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, _live(live_min_buy_fill_fraction=0.0), {},
                                data=data_for(uptrend()), portfolio_emulator=emu, strategy_cache={})
    assert out.get("BIL") == -1 and out.get("SSO") == 1


def test_backtest_flip_is_unchanged_by_the_live_hold():
    emu = FakeEmulator(cash=84.0, positions={"BIL": 109.0})
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out.get("BIL") == -1 and out.get("SSO") == 1


def test_a_scheduler_mode_alone_marks_the_tick_live():
    emu = FakeEmulator(cash=84.0, positions={"BIL": 109.0})
    out = StrategyHt().run_once(["SSO"], PRICES, NOW, cfg(), {}, data=data_for(uptrend()),
                                portfolio_emulator=emu, strategy_cache={}, mode="full")
    assert out.get("BIL") == -1 and "SSO" not in out
