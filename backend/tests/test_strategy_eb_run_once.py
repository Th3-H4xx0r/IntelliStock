"""Wrapper tests for Strategy EB: the broker contract and cache behaviour."""
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

# ONLY backend/ goes on the path. Adding backend/strategies/ too would make
# `strategy_x` resolve to the WRAPPER rather than the pure module — they share
# a name, and the wrapper imports the pure one, so it would self-import.
_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from strategies.strategy_eb import StrategyEb  # noqa: E402
from strategy_eb import DEFAULTS, LAST_REBALANCE_KEY  # noqa: E402

#: THE DECISION WEEKDAY IS THE DATA DATE, NOT THE CALL DATE. `pit_daily_
#: observations` returns only STRICTLY EARLIER sessions, so a call on Thursday
#: sees through Wednesday and `rebalance_weekdays=[2]` fires then. Getting this
#: backwards makes every cadence test pass against a strategy that never trades.
#:   DECIDES: called Thu 2026-06-04, last visible session Wed 2026-06-03 (wd 2).
#:   SKIPS:   called Fri 2026-06-05, last visible session Thu 2026-06-04 (wd 3).
DECIDES = datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc)
SKIPS = datetime(2026, 6, 5, 20, 0, tzinfo=timezone.utc)
DECISION_SESSION = "2026-06-03"

PRICES = {"TQQQ": 80.0, "SPY": 500.0, "BIL": 91.0, "QQQ": 480.0}


def alternating(pct, n=120, end_day=None, start=100.0):
    """Daily bars whose returns alternate exactly +pct, -pct, ..., the LAST one
    stamped `end_day` (default the Wednesday that `DECIDES` sees)."""
    end_day = end_day or datetime(2026, 6, 3, tzinfo=timezone.utc)
    closes = [start]
    for i in range(n - 1):
        closes.append(closes[-1] * ((1 + pct) if i % 2 == 0 else (1 - pct)))
    return [{"t": (end_day - timedelta(days=(n - 1 - i))).isoformat(),
             "c": closes[i]} for i in range(n)]


class FakeEmulator:
    def __init__(self, cash=10000.0, positions=None, prices=None):
        self._cash = cash
        self._positions = dict(positions or {})
        self._prices = dict(prices or PRICES)

    def get_cash(self):
        return self._cash

    def get_positions(self):
        return dict(self._positions)

    def get_portfolio_value(self, prices=None):
        px = prices or self._prices
        return self._cash + sum(q * float(px.get(s, 0.0))
                                for s, q in self._positions.items())


def cfg(**overrides):
    value = dict(DEFAULTS)
    value["strategy_eb_enabled"] = True
    value.update(overrides)
    return value


def data_for(ref_bars, legs=("TQQQ", "SPY", "BIL")):
    out = {"QQQ": {"bars": ref_bars}}
    for symbol in legs:
        out[symbol] = {"bars": alternating(0.002)}
    return out


def test_disabled_by_default_emits_nothing():
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, dict(DEFAULTS), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator())
    assert out == {}


def test_no_emulator_emits_nothing():
    assert StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                 data=data_for(alternating(0.01)),
                                 portfolio_emulator=None) == {}


def test_a_wednesday_opens_the_core_and_the_spy_remainder():
    cache = {}
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert out.get("TQQQ") == 1 and out.get("SPY") == 1
    assert cache["_strategy_eb_last"]["core_weight"] == 0.40
    assert cache["_strategy_eb_last"]["targets"] == {"TQQQ": 0.40, "SPY": 0.60}


def test_every_decision_carries_its_own_size():
    """A bare 1 is sized by the broker's default cash_per_trade (~$1,000):
    index_core_tilt asked for $6,000 of SPY and received $900."""
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    sizes = out["_nexus_position_sizes"]
    for symbol, decision in out.items():
        if symbol.startswith("_"):
            continue
        assert symbol in sizes, symbol
        assert sizes[symbol].get("buy_cash", 0) > 0 or "sell_fraction" in sizes[symbol]


def test_it_does_not_trade_when_the_last_visible_session_is_not_a_decision_day():
    """Called on Friday, the last visible session is Thursday (weekday 3). The
    book is fully invested in SPY, so nothing is idle for the cash sweep either
    and the weekday rule is the only thing under test."""
    assert StrategyEb().run_once(
        ["TQQQ"], PRICES, SKIPS, cfg(), {},
        data=data_for(alternating(0.01, end_day=datetime(
            2026, 6, 4, tzinfo=timezone.utc))),
        portfolio_emulator=FakeEmulator(cash=0.0, positions={"SPY": 20.0}),
        strategy_cache={}) == {}


def test_it_does_not_trade_twice_in_one_session():
    """The engine calls run_once on EVERY tick."""
    strat, cache = StrategyEb(), {}
    emu = FakeEmulator()
    first = strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                           data=data_for(alternating(0.01)),
                           portfolio_emulator=emu, strategy_cache=cache)
    assert first
    assert cache[LAST_REBALANCE_KEY] == DECISION_SESSION
    second = strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                            data=data_for(alternating(0.01)),
                            portfolio_emulator=emu, strategy_cache=cache)
    assert second == {}


def test_it_does_not_re_issue_an_in_flight_exit_in_the_same_session():
    """An exit to zero deliberately bypasses the same-session guard in
    `eb_should_trade` — waiting one more tick to leave a 3x fund is the failure
    the transform exists to prevent. But equity fills are NEXT-BAR, so on every
    one of the ~26 remaining 15m ticks the emulator still reports the position
    and the identical exit would be re-sent. The wrapper dedupes it by session;
    a LATER session re-arms, because there the exit really did not fill."""
    strat, cache = StrategyEb(), {}
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0})
    first = strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                           data=data_for(alternating(0.10)),
                           portfolio_emulator=emu, strategy_cache=cache)
    assert first.get("TQQQ") == -1
    assert cache["_strategy_eb_exit_issued_session"] == DECISION_SESSION

    second = strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                            data=data_for(alternating(0.10)),
                            portfolio_emulator=emu, strategy_cache=cache)
    assert second == {}

    later = strat.run_once(
        ["TQQQ"], PRICES, SKIPS, cfg(), {},
        data=data_for(alternating(0.10, end_day=datetime(
            2026, 6, 4, tzinfo=timezone.utc))),
        portfolio_emulator=emu, strategy_cache=cache)
    assert later.get("TQQQ") == -1
    assert cache["_strategy_eb_exit_issued_session"] == "2026-06-04"


def test_a_sell_publishes_the_fraction_of_the_position_to_close():
    """A bare -1 with no size is the mirror of the bare 1: the broker picks the
    quantity, not the strategy."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0})
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.10)),
                                portfolio_emulator=emu, strategy_cache={})
    assert out["TQQQ"] == -1
    assert out["_nexus_position_sizes"]["TQQQ"]["sell_fraction"] == 1.0


def test_it_refuses_on_short_history_rather_than_levering_up():
    assert StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                 data=data_for(alternating(0.01, n=40)),
                                 portfolio_emulator=FakeEmulator(),
                                 strategy_cache={}) == {}


def test_it_refuses_on_empty_bars():
    """Live passes data=None today; a blind strategy must do NOTHING."""
    for blind in (None, {}, {"QQQ": {"bars": []}}):
        assert StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                     data=blind,
                                     portfolio_emulator=FakeEmulator(),
                                     strategy_cache={}) == {}, blind


def test_it_prices_declared_legs_the_broker_did_not_carry():
    """The declared legs are absent from the operator's watchlist, so
    targets_to_orders would skip them at px <= 0 and emit nothing."""
    out = StrategyEb().run_once(["TQQQ"], {"TQQQ": 80.0}, DECIDES, cfg(), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(cash=10000.0),
                                strategy_cache={})
    assert out.get("SPY") == 1


def test_every_sell_carries_an_action_intent():
    """broker.py's Z2.1 check reads action_intent off the strategy summary and
    whitelists only a fixed enum. Strategy X shipped without this and all 965
    of its sells logged would_block_in_phase2=True."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0})
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.10)),
                                portfolio_emulator=emu, strategy_cache={})
    sells = [s for s, d in out.items() if not s.startswith("_") and d == -1]
    assert sells
    for symbol in sells:
        assert out["_nexus_action_intents"][symbol] == "etf_sell"


def test_it_never_sells_a_symbol_outside_its_own_universe():
    """`owned` scoping: walking the whole book liquidates a co-deployed
    strategy's positions, and _nexus_sell_enforcement is a HARD override."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0, "AAPL": 50.0})
    out = StrategyEb().run_once(["TQQQ"], dict(PRICES, AAPL=200.0), DECIDES,
                                cfg(), {}, data=data_for(alternating(0.10)),
                                portfolio_emulator=emu, strategy_cache={})
    assert "AAPL" not in out


def test_it_publishes_every_nexus_channel():
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    for key in ("_nexus_position_sizes", "_nexus_discovered",
                "_nexus_executable_buys", "_nexus_sell_enforcement",
                "_nexus_action_intents"):
        assert key in out, key
    assert set(out["_nexus_discovered"]) == {"QQQ", "TQQQ", "SPY", "BIL"}


def test_the_bil_dial_routes_the_remainder_to_bills():
    cache = {}
    StrategyEb().run_once(["TQQQ"], PRICES, DECIDES,
                          cfg(remainder_bil_fraction=1.0), {},
                          data=data_for(alternating(0.01)),
                          portfolio_emulator=FakeEmulator(),
                          strategy_cache=cache)
    assert cache["_strategy_eb_last"]["targets"] == {"TQQQ": 0.40, "BIL": 0.60}


# ── the cash sweep ──────────────────────────────────────────────────────────
#
# `targets_to_orders` sizes buys off SETTLED cash and equity fills are
# next-bar, so the tick that sells the core cannot also fund the remainder leg
# — the SPY buy is clipped to whatever cash had already settled. On every later
# session the band sees no breach (and after a full exit `eb_should_trade`
# returns (False, 0.0)), so the freed cash is NEVER deployed and the book
# drifts to cash. That is not the strategy the spec describes.

SWEEP_SESSION = "2026-06-04"


def sweep_data(pct=0.01):
    """Bars whose last visible session at `SKIPS` is a Thursday, so no core
    decision is due — which is exactly when the sweep is allowed to run."""
    return data_for(alternating(
        pct, end_day=datetime(2026, 6, 4, tzinfo=timezone.utc)))


def test_the_session_after_a_full_exit_puts_the_freed_cash_back_to_work():
    cache = {}
    out = StrategyEb().run_once(["TQQQ"], PRICES, SKIPS, cfg(), {},
                                data=sweep_data(),
                                portfolio_emulator=FakeEmulator(cash=10000.0),
                                strategy_cache=cache)
    assert out.get("SPY") == 1
    assert "TQQQ" not in out
    assert out["_nexus_position_sizes"]["SPY"]["buy_cash"] > 9000
    for key in ("_nexus_position_sizes", "_nexus_discovered",
                "_nexus_executable_buys", "_nexus_sell_enforcement",
                "_nexus_action_intents"):
        assert key in out, key
    assert cache["_strategy_eb_sweep_session"] == SWEEP_SESSION


def test_the_sweep_follows_the_bil_dial_like_every_other_remainder():
    out = StrategyEb().run_once(["TQQQ"], PRICES, SKIPS,
                                cfg(remainder_bil_fraction=1.0), {},
                                data=sweep_data(),
                                portfolio_emulator=FakeEmulator(cash=10000.0),
                                strategy_cache={})
    assert out.get("BIL") == 1
    assert "SPY" not in out


def test_a_book_that_is_barely_in_cash_is_left_alone():
    """`cash_sweep_min_pct`: below it the sweep is pure churn."""
    emu = FakeEmulator(cash=100.0, positions={"SPY": 19.8})
    assert StrategyEb().run_once(["TQQQ"], PRICES, SKIPS, cfg(), {},
                                 data=sweep_data(),
                                 portfolio_emulator=emu,
                                 strategy_cache={}) == {}


def test_a_core_rebalance_session_never_sweeps_on_top_of_its_own_plan():
    """The core plan already carries the remainder legs, and its buys have not
    settled — cash still reads FULL on every remaining tick of that session, so
    an unguarded sweep would spend the same dollars a second time."""
    strat, cache = StrategyEb(), {}
    emu = FakeEmulator()
    out = strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                         data=data_for(alternating(0.01)),
                         portfolio_emulator=emu, strategy_cache=cache)
    assert out.get("TQQQ") == 1
    assert "_strategy_eb_sweep_session" not in cache

    later_tick = strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=emu, strategy_cache=cache)
    assert later_tick == {}
    assert "_strategy_eb_sweep_session" not in cache


def test_an_in_flight_exit_tick_does_not_sweep_either():
    strat, cache = StrategyEb(), {}
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0})
    strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                   data=data_for(alternating(0.10)),
                   portfolio_emulator=emu, strategy_cache=cache)
    assert strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                          data=data_for(alternating(0.10)),
                          portfolio_emulator=emu, strategy_cache=cache) == {}
    assert "_strategy_eb_sweep_session" not in cache


def test_the_sweep_never_buys_or_sells_the_core():
    """The core is outside both the sweep's targets and its `owned` scope, so
    it can be neither trimmed to fund the remainder nor topped up outside the
    weekly cadence."""
    emu = FakeEmulator(cash=5000.0, positions={"TQQQ": 100.0})
    out = StrategyEb().run_once(["TQQQ"], PRICES, SKIPS, cfg(), {},
                                data=sweep_data(),
                                portfolio_emulator=emu, strategy_cache={})
    assert "TQQQ" not in out
    assert out.get("SPY") == 1


def test_the_sweep_fires_once_per_session_not_once_per_tick():
    """Equity fills are next-bar here too: the cash is still settled on the
    next tick, so an unguarded sweep re-sends the identical buy ~26 times."""
    strat, cache = StrategyEb(), {}
    emu = FakeEmulator(cash=10000.0)
    assert strat.run_once(["TQQQ"], PRICES, SKIPS, cfg(), {},
                          data=sweep_data(), portfolio_emulator=emu,
                          strategy_cache=cache).get("SPY") == 1
    assert strat.run_once(["TQQQ"], PRICES, SKIPS, cfg(), {},
                          data=sweep_data(), portfolio_emulator=emu,
                          strategy_cache=cache) == {}


# ── log volume ──────────────────────────────────────────────────────────────

def test_a_refusal_is_logged_once_per_session_not_once_per_tick(monkeypatch):
    """A daily strategy that refuses all day writes one line, not ~26. The sink
    is BacktestResults.logs, which an operator reads by eye."""
    import strategies.strategy_eb as mod

    lines = []
    monkeypatch.setattr(mod, "_log",
                        lambda msg, color="white": lines.append(msg))
    strat, cache = mod.StrategyEb(), {}
    for _ in range(4):
        strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                       data=data_for(alternating(0.01, n=40)),
                       portfolio_emulator=FakeEmulator(), strategy_cache=cache)
    assert len(lines) == 1


def test_a_blind_refusal_is_logged_once_too(monkeypatch):
    """`data=None` is EVERY live tick today, so this is the loudest one."""
    import strategies.strategy_eb as mod

    lines = []
    monkeypatch.setattr(mod, "_log",
                        lambda msg, color="white": lines.append(msg))
    strat, cache = mod.StrategyEb(), {}
    for _ in range(4):
        strat.run_once(["TQQQ"], PRICES, DECIDES, cfg(), {}, data=None,
                       portfolio_emulator=FakeEmulator(), strategy_cache=cache)
    assert len(lines) == 1


def test_the_schema_header_contains_exactly_every_default():
    path = os.path.join(_backend, "strategies", "strategy_eb.py")
    header = re.search(r"# INTELLISTOCK_SCHEMA: (.*)", open(path).read())
    schema = json.loads(header.group(1))
    assert set(schema["config"]) == set(DEFAULTS)
    assert schema["strategy"] == "strategy_eb"
    assert schema["execution_scope"] == "run_once"
    assert schema["decision_phase"] == "pre"
    assert schema["execution_position"] == 10


def test_the_class_name_matches_what_the_broker_derives_from_the_id():
    """broker.py resolves a run-once strategy by CamelCasing its id, so the
    class name is part of the contract. Strategy XS shipped once as
    `StrategyXS` and BT634331 ran 1,259 sessions completely inert — the only
    sign was one log line, and every unit test still passed because they import
    by name."""
    import strategies.strategy_eb as mod
    from strategies_meta import _module_to_class_name

    derived = _module_to_class_name("strategy_eb")
    assert derived == "StrategyEb"
    assert hasattr(mod, derived), f"broker looks for {derived}"
    assert hasattr(getattr(mod, derived), "run_once")


def test_the_sweep_funds_the_pending_core_book_from_settled_cash():
    """BT 222375: the Wednesday plan sells SPY to make room but the TQQQ buy
    sizes off settled cash and is skipped; the next-session sweep must deploy
    the settled proceeds toward the PENDING full book — core included — and
    never sell anything."""
    from strategies.strategy_eb import _PENDING_TARGETS_KEY
    emu = FakeEmulator(cash=6000.0, positions={})
    cache = {_PENDING_TARGETS_KEY: {"TQQQ": 0.45, "SPY": 0.55}}
    out = StrategyEb().run_once(["TQQQ"], PRICES, SKIPS, cfg(), {},
                                data=sweep_data(),
                                portfolio_emulator=emu, strategy_cache=cache)
    assert out.get("TQQQ") == 1 and out.get("SPY") == 1
    assert all(v == 1 for k, v in out.items() if not k.startswith("_"))
    assert "buy_cash" in out["_nexus_position_sizes"]["TQQQ"]


def test_every_payload_zeroes_the_brokers_cash_reserve_floor():
    """BT 400783: the default 10%-of-initial-NAV cash floor blocked 775 of 805
    buys on a 2-leg book. The wrapper publishes a zero floor; the vol target is
    this book's risk control."""
    emu = FakeEmulator(cash=6000.0, positions={})
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=sweep_data(),
                                portfolio_emulator=emu, strategy_cache={})
    assert out["_nexus_position_sizes"]["_cash_reserve_floor_pct"] == 0.0


def test_buys_size_off_buying_power_when_the_emulator_offers_it():
    """With backtest_credit_pending_sell_proceeds the emulator's buying power
    includes the same-tick funding sell; the wrapper must ask for it rather
    than settled cash (the funding lag cost 1.11pp CAGR on BT 400783)."""
    emu = FakeEmulator(cash=100.0, positions={"SPY": 12.0})
    emu.get_buying_power = lambda prices=None: 5900.0
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=emu, strategy_cache={})
    sizes = out["_nexus_position_sizes"]
    spent = sum(v.get("buy_cash", 0.0) for v in sizes.values()
                if isinstance(v, dict))
    assert spent > 1000.0


# ── the trend-conditioned remainder ─────────────────────────────────────────
#
# `trend_filter_bars = 100` and `risk_off_symbol = "GLD"` is config A' of the
# replay. The machine is evaluated on DECISION SESSIONS ONLY — a daily
# evaluation is a twitchier state path than the one that was measured.

TREND = {"trend_filter_bars": 100, "risk_off_symbol": "GLD"}
GLD_PRICES = dict(PRICES, GLD=200.0)
_TREND_LEGS = ("TQQQ", "SPY", "BIL", "GLD")
THURSDAY = datetime(2026, 6, 4, tzinfo=timezone.utc)


def declining(pct=0.01, drift=-0.01, n=120, end_day=None, start=300.0):
    """Bars that alternate +/-`pct` around a `drift` downtrend, so the last
    close sits far below its own 100-session average and the machine reads OFF.
    A PURE decline would have zero realised volatility and `eb_core_weight`
    would refuse; the alternation keeps rv at the same 0.163 the ON fixture
    measures, so both fixtures target the SAME 0.40 core and the only thing
    under test is the occupant."""
    end_day = end_day or datetime(2026, 6, 3, tzinfo=timezone.utc)
    closes = [start]
    for i in range(n - 1):
        closes.append(closes[-1] * (1 + drift)
                      * ((1 + pct) if i % 2 == 0 else (1 - pct)))
    return [{"t": (end_day - timedelta(days=(n - 1 - i))).isoformat(),
             "c": closes[i]} for i in range(n)]


def trend_data(ref_bars):
    return data_for(ref_bars, legs=_TREND_LEGS)


def test_the_default_config_writes_no_trend_state_at_all():
    """Byte-identity. With `trend_filter_bars = 0` nothing about the machine
    may reach the cache, the payload or the declared universe."""
    cache = {}
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert "_strategy_eb_trend_state" not in cache
    assert "_strategy_eb_last_state" not in cache
    assert set(out["_nexus_discovered"]) == {"QQQ", "TQQQ", "SPY", "BIL"}


def test_a_risk_off_decision_puts_the_whole_remainder_in_gold():
    cache = {}
    out = StrategyEb().run_once(["TQQQ"], GLD_PRICES, DECIDES, cfg(**TREND),
                                {}, data=trend_data(declining()),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert cache["_strategy_eb_trend_state"] == "OFF"
    assert cache["_strategy_eb_last"]["targets"] == {"TQQQ": 0.40, "GLD": 0.60}
    assert out.get("GLD") == 1
    assert "SPY" not in out
    assert "GLD" in out["_nexus_discovered"]


def test_a_risk_on_decision_keeps_the_remainder_in_spy():
    """The SAME config on a tape above its average: the feature being enabled
    must not itself change the book."""
    cache = {}
    out = StrategyEb().run_once(["TQQQ"], GLD_PRICES, DECIDES, cfg(**TREND),
                                {}, data=trend_data(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert cache["_strategy_eb_trend_state"] == "ON"
    assert cache["_strategy_eb_last"]["targets"] == {"TQQQ": 0.40, "SPY": 0.60}
    assert out.get("SPY") == 1
    assert "GLD" not in out


def test_the_state_is_re_evaluated_only_on_a_decision_session():
    """Called on Friday, the last visible session is Thursday. The replay
    updates the machine on Wednesdays ONLY; evaluating it daily is a different
    state path with different flip counts and different turnover."""
    cache = {"_strategy_eb_trend_state": "ON"}
    StrategyEb().run_once(
        ["TQQQ"], GLD_PRICES, SKIPS, cfg(**TREND), {},
        data=trend_data(declining(end_day=THURSDAY)),
        portfolio_emulator=FakeEmulator(cash=0.0, positions={"SPY": 20.0}),
        strategy_cache=cache)
    assert cache["_strategy_eb_trend_state"] == "ON"


def test_a_state_flip_rotates_the_occupant_inside_the_band():
    """THE clause the feature depends on. The book is already AT its target
    core weight, so |w - w_held| is 0 and the band suppresses everything — but
    the remainder must still leave SPY. Without this the whole risk-off leg is
    spent in the asset the flip was supposed to exit."""
    # 50 TQQQ at 80 = $4,000 and 12 SPY at 500 = $6,000: NAV $10,000, held
    # core exactly 0.40, which is what this tape targets.
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 50.0, "SPY": 12.0})
    cache = {"_strategy_eb_last_state": "ON"}
    out = StrategyEb().run_once(["TQQQ"], GLD_PRICES, DECIDES, cfg(**TREND),
                                {}, data=trend_data(declining()),
                                portfolio_emulator=emu, strategy_cache=cache)
    assert cache["_strategy_eb_last"]["held_weight"] == 0.4
    assert out.get("SPY") == -1
    assert cache["_strategy_eb_last"]["targets"] == {"TQQQ": 0.40, "GLD": 0.60}
    assert cache["_strategy_eb_last_state"] == "OFF"


def test_the_same_book_with_the_state_already_executed_does_nothing():
    """The control for the test above: same tape, same book, same band — only
    the recorded executed state differs, and the whole trade disappears."""
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 50.0, "SPY": 12.0})
    assert StrategyEb().run_once(
        ["TQQQ"], GLD_PRICES, DECIDES, cfg(**TREND), {},
        data=trend_data(declining()), portfolio_emulator=emu,
        strategy_cache={"_strategy_eb_last_state": "OFF"}) == {}


def test_the_sweep_carries_the_occupant_of_the_current_state():
    """The pending book was written while the state was ON, so it names SPY.
    Deploying it verbatim would buy back the very leg the flip just sold."""
    from strategies.strategy_eb import _PENDING_TARGETS_KEY
    emu = FakeEmulator(cash=6000.0, positions={})
    cache = {_PENDING_TARGETS_KEY: {"TQQQ": 0.40, "SPY": 0.60},
             "_strategy_eb_trend_state": "OFF"}
    out = StrategyEb().run_once(
        ["TQQQ"], GLD_PRICES, SKIPS, cfg(**TREND), {},
        data=trend_data(declining(end_day=THURSDAY)),
        portfolio_emulator=emu, strategy_cache=cache)
    assert out.get("GLD") == 1
    assert out.get("TQQQ") == 1
    assert "SPY" not in out


def test_the_remainder_sweep_of_a_risk_off_book_buys_the_risk_off_leg():
    """No pending book at all — the fallback plan must be scoped to the
    occupant too, or the sweep's `owned` set cannot even name it."""
    emu = FakeEmulator(cash=10000.0, positions={})
    out = StrategyEb().run_once(
        ["TQQQ"], GLD_PRICES, SKIPS, cfg(**TREND), {},
        data=trend_data(declining(end_day=THURSDAY)),
        portfolio_emulator=emu,
        strategy_cache={"_strategy_eb_trend_state": "OFF"})
    assert out.get("GLD") == 1
    assert "SPY" not in out


def test_the_off_damp_reaches_the_emitted_book():
    """`core_off_damp` is applied inside the weight, before quantisation, so
    the whole payload — core AND remainder — moves with it."""
    cache = {}
    StrategyEb().run_once(["TQQQ"], GLD_PRICES, DECIDES,
                          cfg(**TREND, core_off_damp=0.5), {},
                          data=trend_data(declining()),
                          portfolio_emulator=FakeEmulator(),
                          strategy_cache=cache)
    assert cache["_strategy_eb_last"]["targets"] == {"TQQQ": 0.20, "GLD": 0.80}


def test_a_zero_damp_exits_the_core_entirely_while_risk_off():
    emu = FakeEmulator(cash=0.0, positions={"TQQQ": 100.0})
    out = StrategyEb().run_once(["TQQQ"], GLD_PRICES, DECIDES,
                                cfg(**TREND, core_off_damp=0.0), {},
                                data=trend_data(declining()),
                                portfolio_emulator=emu, strategy_cache={})
    assert out.get("TQQQ") == -1


# ── the remainder BOOKS through the broker contract ─────────────────────────
#
# A book is {SYMBOL: share of the remainder}. The default is {} on both states,
# which is the single-occupant book this strategy shipped with.

BOOK_PRICES = dict(GLD_PRICES, SMH=250.0, GDX=40.0, XLE=90.0)
STATIC_BOOK = {"trend_on_book": {"SMH": 0.5, "GLD": 0.5}}
BOOK_LEGS = ("TQQQ", "SPY", "BIL", "GLD", "SMH", "GDX", "XLE")


def book_data(ref_bars):
    return data_for(ref_bars, legs=BOOK_LEGS)


def test_the_default_run_is_byte_identical_with_the_book_keys_removed():
    """The differential: a default run with the two new keys deleted from the
    config entirely must produce the SAME payload, cache and universe as one
    carrying them. If it does not, the feature is not default-off."""
    stripped = cfg()
    stripped.pop("trend_on_book")
    stripped.pop("trend_off_book")
    runs = []
    for config in (cfg(), stripped):
        cache = {}
        out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, config, {},
                                    data=data_for(alternating(0.01)),
                                    portfolio_emulator=FakeEmulator(),
                                    strategy_cache=cache)
        runs.append((out, cache))
    assert runs[0][0] == runs[1][0]
    assert runs[0][1] == runs[1][1]
    assert runs[0][0]["_nexus_discovered"] == ["QQQ", "TQQQ", "SPY", "BIL"]


def test_a_static_blend_needs_no_state_machine_at_all():
    """`trend_filter_bars = 0` with a non-empty ON book: the state is pinned
    ON, so the book is simply the remainder, on every session, forever."""
    cache = {}
    out = StrategyEb().run_once(["TQQQ"], BOOK_PRICES, DECIDES,
                                cfg(**STATIC_BOOK), {},
                                data=book_data(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert cache["_strategy_eb_last"]["targets"] == {"TQQQ": 0.40,
                                                     "SMH": 0.30, "GLD": 0.30}
    assert out.get("SMH") == 1 and out.get("GLD") == 1 and out.get("TQQQ") == 1
    assert "SPY" not in out
    assert out["_nexus_discovered"] == ["QQQ", "TQQQ", "SPY", "BIL",
                                        "SMH", "GLD"]
    # Nothing about the state machine may appear: there is no machine here.
    assert "_strategy_eb_trend_state" not in cache


def test_a_static_blend_ignores_a_stale_persisted_off_state():
    """With the filter off the state is PINNED ON. A leftover OFF in the cache
    from an earlier config must not route the book through legs this run never
    even declared."""
    cache = {"_strategy_eb_trend_state": "OFF"}
    StrategyEb().run_once(["TQQQ"], BOOK_PRICES, DECIDES,
                          cfg(**STATIC_BOOK, trend_off_book={"GDX": 1.0},
                              risk_off_symbol="GLD"), {},
                          data=book_data(alternating(0.01)),
                          portfolio_emulator=FakeEmulator(),
                          strategy_cache=cache)
    assert cache["_strategy_eb_last"]["targets"] == {"TQQQ": 0.40,
                                                     "SMH": 0.30, "GLD": 0.30}


PURE = {"target_vol": 0.0, "trend_filter_bars": 100, "risk_off_symbol": "GLD",
        "trend_on_book": {"SMH": 0.3, "GLD": 0.7},
        "trend_off_book": {"GDX": 0.5, "XLE": 0.5}}


def test_a_pure_book_holds_no_core_and_buys_the_whole_book():
    cache = {}
    out = StrategyEb().run_once(["TQQQ"], BOOK_PRICES, DECIDES, cfg(**PURE),
                                {}, data=book_data(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache=cache)
    assert cache["_strategy_eb_last"]["targets"] == {"SMH": 0.30, "GLD": 0.70}
    assert out.get("SMH") == 1 and out.get("GLD") == 1
    assert "TQQQ" not in out
    # Largest intended weight first: GLD asks for its whole 70% of a $10,000
    # NAV and SMH gets what is left after the 0.5% cost haircut.
    sizes = out["_nexus_position_sizes"]
    assert sizes["GLD"]["buy_cash"] == 7000.0
    assert sizes["SMH"]["buy_cash"] == 2985.0


def test_a_pure_book_rotates_the_whole_book_when_the_state_flips():
    """The core band cannot see a book rotation — there is no core. The
    executed-state clause is what forces it, and the plan must SELL the ON book
    as well as buy the OFF one."""
    emu = FakeEmulator(cash=0.0, positions={"SMH": 12.0, "GLD": 35.0})
    cache = {"_strategy_eb_last_state": "ON"}
    out = StrategyEb().run_once(["TQQQ"], BOOK_PRICES, DECIDES, cfg(**PURE),
                                {}, data=book_data(declining()),
                                portfolio_emulator=emu, strategy_cache=cache)
    assert cache["_strategy_eb_trend_state"] == "OFF"
    assert cache["_strategy_eb_last"]["targets"] == {"GDX": 0.50, "XLE": 0.50}
    assert out.get("SMH") == -1 and out.get("GLD") == -1
    assert cache["_strategy_eb_last_state"] == "OFF"


def test_a_pure_book_that_has_not_drifted_sends_nothing():
    """The weekday alone decides for a pure book, so the per-leg band in
    `targets_to_orders` is the only churn control left. It must hold."""
    emu = FakeEmulator(cash=0.0, positions={"SMH": 12.0, "GLD": 35.0})
    out = StrategyEb().run_once(["TQQQ"], BOOK_PRICES, DECIDES, cfg(**PURE),
                                {}, data=book_data(alternating(0.01)),
                                portfolio_emulator=emu,
                                strategy_cache={"_strategy_eb_last_state":
                                                "ON"})
    assert out == {}


def test_a_pure_book_run_never_declares_the_levered_core_as_a_buy():
    """`target_vol = 0` is a CONFIGURED zero, not a refusal: the run still
    declares its universe and still trades."""
    out = StrategyEb().run_once(["TQQQ"], BOOK_PRICES, DECIDES, cfg(**PURE),
                                {}, data=book_data(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(),
                                strategy_cache={})
    assert set(out["_nexus_executable_buys"]) == {"SMH", "GLD"}
    assert out["_nexus_sell_enforcement"] == []
    assert set(out["_nexus_discovered"]) == {"QQQ", "TQQQ", "SPY", "BIL",
                                             "GLD", "SMH", "GDX", "XLE"}


def test_the_sweep_deploys_idle_cash_into_the_current_state_book():
    emu = FakeEmulator(cash=6000.0, positions={}, prices=BOOK_PRICES)
    out = StrategyEb().run_once(
        ["TQQQ"], BOOK_PRICES, SKIPS, cfg(**PURE), {},
        data=book_data(declining(end_day=THURSDAY)),
        portfolio_emulator=emu,
        strategy_cache={"_strategy_eb_trend_state": "OFF"})
    assert out.get("GDX") == 1 and out.get("XLE") == 1
    assert "SMH" not in out and "GLD" not in out


def test_a_dict_valued_config_key_survives_the_api_round_trip():
    """The header is what `/strategies/available` serves and what the UI seeds
    an instance from, and `PUT /strategies/{id}` writes back what it was given.
    A dict-valued key has to survive BOTH: the JSON header parse in
    `strategies_meta`, and the payload normalisation in `interactive_utils`
    that every write goes through."""
    from strategies_meta import _parse_header_meta, get_available_strategies

    path = os.path.join(_backend, "strategies", "strategy_eb.py")
    schema, _ = _parse_header_meta(open(path).read())
    # The shipped schema is the bil25 config adopted 2026-08-31: champion
    # books with 25% of the risk-off remainder falling through to BIL.
    assert schema["config"]["trend_on_book"] == {
        "GLD": 0.5, "GDX": 0.25, "XLE": 0.25}
    assert schema["config"]["trend_off_book"] == {
        "GLD": 0.375, "GDX": 0.1875, "XLE": 0.1875}

    entry = next(s for s in get_available_strategies()
                 if s.get("id") == "strategy_eb")
    assert entry["schema"]["config"]["trend_on_book"] == {
        "GLD": 0.5, "GDX": 0.25, "XLE": 0.25}

    from interactive_utils import _normalize_strategy_payload_item

    books = {"trend_on_book": {"SMH": 0.3, "GLD": 0.7},
             "trend_off_book": {"GDX": 0.5, "XLE": 0.5}}
    payload = {"strategy": "strategy_eb", "weight": 1.0,
               "config": dict(DEFAULTS, **books)}
    # Through JSON, as the HTTP body actually arrives.
    normalized = _normalize_strategy_payload_item(
        json.loads(json.dumps(payload)), strict=True)
    assert normalized["config"]["trend_on_book"] == books["trend_on_book"]
    assert normalized["config"]["trend_off_book"] == books["trend_off_book"]
    # And back out again, as the row is stored and re-read.
    assert json.loads(json.dumps(normalized))["config"]["trend_on_book"] == {
        "SMH": 0.3, "GLD": 0.7}


def test_a_reserve_for_a_sibling_lane_shrinks_the_book_and_holds_its_cash():
    """reserve_for_other_lanes_pct=0.15 with nothing held by the sibling: the
    book is 85% of NAV and 15% of cash stays idle for the sibling."""
    out = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES,
                                cfg(reserve_for_other_lanes_pct=0.15), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=FakeEmulator(cash=10000.0),
                                strategy_cache={})
    sizes = out["_nexus_position_sizes"]
    haircut = 1.0 - DEFAULTS["cost_haircut_pct"]        # targets_to_orders shaves the leveraged core
    assert abs(sizes["TQQQ"]["buy_cash"] - 0.40 * 8500.0 * haircut) < 1.0
    assert abs(sizes["SPY"]["buy_cash"] - 0.60 * 8500.0) < 1.0     # the remainder leg is exact


def test_a_siblings_grown_position_replaces_the_reserve():
    """The sibling already holds 20% of NAV: the book is NAV minus that (80%),
    and no extra cash is held back because the reserve is already deployed."""
    emu = FakeEmulator(cash=8000.0, positions={"FOO": 20.0},
                       prices={**PRICES, "FOO": 100.0})
    out = StrategyEb().run_once(["TQQQ"], {**PRICES, "FOO": 100.0}, DECIDES,
                                cfg(reserve_for_other_lanes_pct=0.15), {},
                                data=data_for(alternating(0.01)),
                                portfolio_emulator=emu, strategy_cache={})
    sizes = out["_nexus_position_sizes"]
    haircut = 1.0 - DEFAULTS["cost_haircut_pct"]
    assert abs(sizes["TQQQ"]["buy_cash"] - 0.40 * 8000.0 * haircut) < 1.0
    assert abs(sizes["SPY"]["buy_cash"] - 0.60 * 8000.0) < 1.0
    assert "FOO" not in out            # never touches the sibling's name


def test_reserve_zero_is_the_pre_existing_arithmetic():
    a = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES, cfg(), {},
                              data=data_for(alternating(0.01)),
                              portfolio_emulator=FakeEmulator(), strategy_cache={})
    b = StrategyEb().run_once(["TQQQ"], PRICES, DECIDES,
                              cfg(reserve_for_other_lanes_pct=0.0), {},
                              data=data_for(alternating(0.01)),
                              portfolio_emulator=FakeEmulator(), strategy_cache={})
    assert a == b


def test_vts_evaluates_the_state_every_session_from_the_vix_etf_pair():
    """With the overlay on, a Thursday (not a decision day) still re-evaluates
    the state: a falling reference with a NORMAL vol curve stays ON."""
    ref = alternating(0.01)
    for i, b in enumerate(ref):                       # make the reference fall hard at the end
        if i >= len(ref) - 30:
            b["c"] = ref[len(ref) - 31]["c"] * (0.995 ** (i - (len(ref) - 31)))
    data = data_for(ref, legs=("TQQQ", "SPY", "BIL"))
    data["VIXY"] = {"bars": alternating(0.001, start=20.0)}
    data["VIXM"] = {"bars": alternating(0.001, start=25.0)}
    cache = {"_strategy_eb_trend_state": "OFF"}
    StrategyEb().run_once(["TQQQ"], PRICES, SKIPS, cfg(trend_filter_bars=25, vts_enabled=True), {},
                          data=data, portfolio_emulator=FakeEmulator(), strategy_cache=cache)
    assert cache["_strategy_eb_trend_state"] == "ON"      # price OFF, curve normal -> ON
    cache2 = {"_strategy_eb_trend_state": "OFF"}
    StrategyEb().run_once(["TQQQ"], PRICES, SKIPS, cfg(trend_filter_bars=25), {},
                          data=data, portfolio_emulator=FakeEmulator(), strategy_cache=cache2)
    assert cache2["_strategy_eb_trend_state"] == "OFF"    # VTS off: Thursday does not re-evaluate
