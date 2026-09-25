"""StrategySwing live path: the resumable 09:15 scan (spec §5.1, §8, §9)."""
import importlib.util
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from swing_trader import clock, signals_store  # noqa: E402
from swing_trader.constants import SWING_DEFAULTS  # noqa: E402

PATH = os.path.join(_backend, "strategies", "strategy_swing.py")
MON_0920 = datetime(2026, 6, 1, 13, 20, tzinfo=timezone.utc)
MON_0940 = datetime(2026, 6, 1, 13, 40, tzinfo=timezone.utc)
MON_1000 = datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc)
TUE_0920 = datetime(2026, 6, 2, 13, 20, tzinfo=timezone.utc)


def _load():
    spec = importlib.util.spec_from_file_location("strategies.strategy_swing", PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def ind(close, rsi=45.0, rsi_prev=40.0, **kw):
    out = {"close": close, "volume": 2e6, "rsi": rsi, "rsi_prev": rsi_prev,
           "macd_hist": 0.2, "macd_hist_prev": 0.1, "macd_hist_prev2": 0.05,
           "sma200": close * 0.9, "vol_avg20": 1e6, "adx": 20.0}
    out.update(kw)
    return out


IND = {"SPY": ind(450.0, rsi=60.0, sma200=400.0), "AAA": ind(100.0), "BBB": ind(50.0),
       "CCC": ind(80.0), "DDD": ind(20.0)}
APPROVE = {"conviction_score": 80, "recommendation": "approve", "reasoning": "ok",
           "position_size_adjustment": 1.0, "key_risks": []}
REVIEW = {"conviction_score": 60, "recommendation": "review", "reasoning": "meh",
          "position_size_adjustment": 0.5, "key_risks": ["x"]}
REJECT = {"conviction_score": 30, "recommendation": "reject", "reasoning": "no",
          "position_size_adjustment": 1.0, "key_risks": []}


class LiveAdapter:
    def __init__(self, positions=None, equity=100_000.0, bp=200_000.0,
                 open_orders=(), closed=(), options=()):
        self.pos = dict(positions or {})       # symbol -> (qty, avg_entry, market_value)
        self.equity, self.bp = equity, bp
        self.open_orders, self.closed, self.options = list(open_orders), list(closed), list(options)
        self.closed_calls = []

    def get_positions(self):
        return {s: v[0] for s, v in self.pos.items()}

    def refresh_positions(self):
        return [NS(symbol=s, qty=v[0], avg_entry_price=v[1], market_value=v[2])
                for s, v in self.pos.items()]

    def refresh_account(self):
        return NS(equity=self.equity)

    def refresh_cash(self):
        return NS(cash=self.bp, buying_power=self.bp)

    def get_cash(self):
        return self.bp

    def get_portfolio_value(self, prices=None):
        return self.equity

    def list_option_positions(self):
        return list(self.options)

    def list_open_orders(self, limit=200):
        return list(self.open_orders)

    def list_closed_orders(self, symbols, after):
        self.closed_calls.append((list(symbols), after))
        return list(self.closed)

    def get_trade_history(self):
        return []


class NoNetworkYf:
    def __init__(self, sector=None):
        self.sector = sector

    def Ticker(self, symbol):
        if self.sector is None:
            raise RuntimeError("network disabled in tests")
        return NS(info={"sector": self.sector})


def scripted(results):
    calls = []

    def analyse(signal, **kw):
        calls.append(signal["symbol"])
        r = results[signal["symbol"]]
        if isinstance(r, Exception):
            raise r
        return dict(r, symbol=signal["symbol"])

    analyse.calls = calls
    return analyse


@pytest.fixture
def live(store, monkeypatch):
    m = _load()
    monkeypatch.setattr(m, "store", store)
    monkeypatch.setattr(signals_store, "store", store)
    store.insert("SwingSectorMap", [
        {"id": s, "symbol": s, "sector": sec, "as_of": "2026-09-24", "source": "yfinance"}
        for s, sec in (("AAA", "technology"), ("BBB", "technology"),
                       ("CCC", "energy"), ("DDD", "utilities"))], conflict="replace")
    monkeypatch.setattr(m.market_data, "data_client", lambda k, s: object())
    monkeypatch.setattr(m.market_data, "get_daily_bars", lambda syms, days, client: {})
    monkeypatch.setattr(m.universe, "get_sp500_symbols",
                        lambda: ["AAA", "BBB", "CCC", "DDD", "SPY", "QQQ"])
    monkeypatch.setattr(m.regime, "fetch_vix_close", lambda: 15.0)
    monkeypatch.setattr(m.ai_analyst, "days_until_earnings", lambda s: None)
    monkeypatch.setattr(m.calibration, "record_outcomes", lambda *a, **k: 0)
    monkeypatch.setattr(m.clock, "is_trading_day", lambda d: True)
    monkeypatch.setattr(m, "swing_indicators", lambda frames, c: dict(IND))
    # Symbols SwingSectorMap lacks (XLP) fall back to yfinance live; tests
    # make no network calls.
    monkeypatch.setattr(m.sectors, "yf", NoNetworkYf())
    sent = []
    monkeypatch.setattr(m.notify, "send",
                        lambda cat, iid, title, msg, priority=0: sent.append((cat, title)))
    m.sent = sent
    clock._DEADLINES.clear()
    return m


def cfg(**over):
    c = dict(SWING_DEFAULTS, strategy_swing_enabled=True, instance_id="swing-paper",
             alpaca_key="k", alpaca_secret="s", conviction_llm_provider="claude-cli",
             conviction_llm_model="claude-sonnet-4-6", conviction_llm_api_key="")
    c.update(over)
    return c


def tick(m, at, adapter, cache, config=None, mode="MONITOR"):
    return m.StrategySwing().run_once(["SPY"], {}, at, config or cfg(), {}, data=None,
                                      portfolio_emulator=adapter, strategy_cache=cache,
                                      mode=mode)


def rows():
    return {r["symbol"]: r for r in signals_store.all_signals("swing-paper")}


def test_the_0920_scan_emits_brackets_and_records_every_score(live, monkeypatch):
    ai = scripted({"AAA": APPROVE, "CCC": REVIEW, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    cache = {}
    out = tick(live, MON_0920, LiveAdapter(), cache)
    assert {s: d for s, d in out.items() if not s.startswith("_")} == {"AAA": 1}
    # The engine buys floor(buy_cash / live price) shares; a live price above
    # the prior close may buy one share fewer than ST's 125 (accepted).
    assert out["_nexus_position_sizes"]["AAA"] == {
        "buy_cash": 12_500.0,
        "bracket": {"take_profit_price": 109.0, "stop_loss_price": 94.0},
        "whole_shares": True, "fill_at_next_open": True}
    assert out["_nexus_action_intents"] == {"AAA": "swing_entry"}
    assert ai.calls == ["AAA", "CCC", "DDD"]           # BBB: same sector as AAA (fix 4)
    r = rows()
    assert (r["AAA"]["status"], r["CCC"]["status"], r["DDD"]["status"]) == (
        "auto_approved", "pending", "ai_rejected")
    assert "BBB" not in r
    assert r["CCC"]["proposal"] == {"entry": 80.0, "stop": 75.2, "target": 87.2, "shares": 156}
    cats = [c for c, _ in live.sent]
    assert cats == ["swing_entry", "swing_pending_review", "swing_run_summary",
                    "swing_run_summary"]
    assert live.sent[-1][1] == "Swing Trader ✅ Run Complete"
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01" and live._SCAN_KEY not in cache


def test_a_later_tick_does_not_rescan(live, monkeypatch):
    ai = scripted({"AAA": APPROVE, "CCC": REVIEW, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    cache = {}
    tick(live, MON_0920, LiveAdapter(), cache)
    working = LiveAdapter(open_orders=[NS(symbol="AAA", side="buy")])
    assert tick(live, MON_1000, working, cache) == {}
    assert ai.calls == ["AAA", "CCC", "DDD"]


def test_a_stale_quote_entry_is_reemitted_once_at_the_open(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT}))
    cache = {}
    first = tick(live, MON_0920, LiveAdapter(), cache)
    empty = LiveAdapter()
    again = tick(live, MON_0940, empty, cache)
    assert again["AAA"] == 1
    assert again["_nexus_position_sizes"]["AAA"] == first["_nexus_position_sizes"]["AAA"]
    assert empty.closed_calls == [(["AAA"], "2026-06-01")]
    assert tick(live, MON_1000, LiveAdapter(), cache) == {}


def test_no_rearm_when_the_broker_saw_the_order(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT}))
    cache = {}
    tick(live, MON_0920, LiveAdapter(), cache)
    rejected = LiveAdapter(closed=[NS(symbol="AAA", side="buy", status="rejected")])
    assert tick(live, MON_0940, rejected, cache) == {}
    assert tick(live, MON_0940, LiveAdapter(positions={"AAA": (125, 100.0, 12_500.0)}),
                {**cache}) == {}


def test_an_ai_error_skips_only_that_candidate(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse", scripted({
        "AAA": ValueError("the model returned no valid JSON object"),
        "CCC": APPROVE, "DDD": ValueError("conviction_score 150 is outside 0-100")}))
    cache = {}
    out = tick(live, MON_0920, LiveAdapter(), cache)
    assert out["_nexus_executable_buys"] == ["CCC"]
    assert set(rows()) == {"CCC"}
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01"


def test_no_model_linked_refuses_entries_with_one_alert_per_session(live, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no scoring without a model")

    monkeypatch.setattr(live.ai_analyst, "analyse", boom)
    held = LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0)})
    monkeypatch.setattr(live, "swing_indicators",
                        lambda f, c: dict(IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0)))
    no_model = cfg(conviction_llm_provider="", conviction_llm_model="")
    cache = {}
    out = tick(live, MON_0920, held, cache, config=no_model)
    assert {s: d for s, d in out.items() if not s.startswith("_")} == {"EEE": -1}
    tick(live, MON_1000, held, cache, config=no_model)
    assert [c for c, _ in live.sent].count("strategy_error") == 1
    tick(live, TUE_0920, held, cache, config=no_model)
    assert [c for c, _ in live.sent].count("strategy_error") == 2


def test_the_tick_budget_resumes_the_scan_on_the_next_tick(live, monkeypatch):
    ai = scripted({"AAA": APPROVE, "CCC": REVIEW, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    budget = iter([100.0, 60.0, 10.0])
    monkeypatch.setattr(live.clock, "time_left", lambda deadline: next(budget, 1_000.0))
    cache = {}
    first = tick(live, MON_0920, LiveAdapter(), cache)
    assert first["_nexus_executable_buys"] == ["AAA"]
    assert cache[live._SCAN_KEY]["cursor"] == 1 and live._SCAN_DONE_KEY not in cache
    working = LiveAdapter(open_orders=[NS(symbol="AAA", side="buy")])
    second = tick(live, MON_0940, working, cache, mode="FULL")
    assert second == {}                                # CCC pending, DDD rejected
    assert ai.calls == ["AAA", "CCC", "DDD"]
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01"


def test_rsi_cross_and_close_stop_exits_run_in_the_scan(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": REJECT, "CCC": REJECT, "DDD": REJECT}))
    monkeypatch.setattr(live, "swing_indicators", lambda f, c: dict(
        IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0), FFF=ind(93.0, rsi=40.0, rsi_prev=45.0)))
    adapter = LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0), "FFF": (5, 100.0, 465.0)})
    out = tick(live, MON_0920, adapter, {})
    assert out["EEE"] == -1 and out["FFF"] == -1
    assert out["_nexus_position_sizes"]["EEE"] == {"sell_fraction": 1.0, "fill_at_next_open": True}
    assert out["_nexus_action_intents"] == {"EEE": "swing_rsi_exit", "FFF": "swing_stop_exit"}
    assert [t for c, t in live.sent if c == "swing_exit"] == ["SELL EEE", "SELL FFF"]


def test_a_deferred_exit_is_reemitted_until_the_position_is_gone(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": REJECT, "CCC": REJECT, "DDD": REJECT}))
    monkeypatch.setattr(live, "swing_indicators",
                        lambda f, c: dict(IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0)))
    held = LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0)})
    cache = {}
    assert tick(live, MON_0920, held, cache)["EEE"] == -1
    assert cache[live._PENDING_EXIT_KEY]["EEE"]["intent"] == "swing_rsi_exit"
    # The engine deferred it (the legs' cancel did not confirm): nothing at the broker.
    again = tick(live, MON_0940, held, cache)
    assert {s: d for s, d in again.items() if not s.startswith("_")} == {"EEE": -1}
    assert again["_nexus_action_intents"] == {"EEE": "swing_rsi_exit"}
    assert again["_nexus_position_sizes"]["EEE"] == {"sell_fraction": 1.0}
    # A working market sell is the exit in flight: do not stack a second one.
    selling = LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0)},
                          open_orders=[NS(symbol="EEE", side="sell", order_class="simple")])
    assert tick(live, MON_1000, selling, cache) == {}
    # Still held the next morning, before that session's scan: still re-emitted.
    tue_0900 = datetime(2026, 6, 2, 13, 0, tzinfo=timezone.utc)
    assert tick(live, tue_0900, held, cache)["EEE"] == -1
    # Gone: the entry is dropped and nothing more is sent.
    assert tick(live, datetime(2026, 6, 2, 13, 5, tzinfo=timezone.utc),
                LiveAdapter(), cache) == {}
    assert "EEE" not in cache[live._PENDING_EXIT_KEY]


def test_a_bracket_leg_is_not_a_working_exit(live, monkeypatch):
    cache = {live._PENDING_EXIT_KEY: {"EEE": {"reason": "stop_loss",
                                              "intent": "swing_stop_exit",
                                              "since": "2026-06-01"}},
             live._SCAN_DONE_KEY: "2026-06-01"}
    legs = [NS(symbol="EEE", side="sell", order_class="bracket", status="held"),
            NS(symbol="EEE", side="sell", order_class="bracket", status="new")]
    out = tick(live, MON_1000, LiveAdapter(positions={"EEE": (10, 100.0, 930.0)},
                                           open_orders=legs), cache)
    assert out["EEE"] == -1 and out["_nexus_action_intents"] == {"EEE": "swing_stop_exit"}

    class Unreadable(LiveAdapter):
        def list_open_orders(self, limit=200):
            raise RuntimeError("orders endpoint down")

    assert tick(live, MON_1000, Unreadable(positions={"EEE": (10, 100.0, 930.0)}),
                cache) == {}
    assert "EEE" in cache[live._PENDING_EXIT_KEY]


def _alpaca_book(*rows):
    """The working-order book as AlpacaAdapter.list_open_orders_strict reads
    it: real OrderRefs (order_class, status, order_type) from raw rows."""
    from swing_alpaca_fakes import FakeTradingClient, make_adapter

    return make_adapter(FakeTradingClient(orders=rows)).list_open_orders_strict()


def test_g8b_real_bracket_leg_orderrefs_are_not_a_working_exit(live, monkeypatch):
    # G6 review carry: with OrderRef.order_class on the branch (A-live L1/L2),
    # a held position whose only working SELLs are its bracket's legs gets
    # its deferred exit re-sent. The engine cancels those legs before selling.
    from swing_alpaca_fakes import enum, order_row

    def leg(oid, status, kind, **price):
        return order_row(id=oid, client_order_id=f"cid-{oid}", symbol="EEE",
                         side=enum("sell"), qty="10", status=enum(status),
                         order_class=enum("bracket"), type=enum(kind), **price)

    legs = _alpaca_book(leg("tp", "new", "limit", limit_price="109"),
                        leg("sl", "held", "stop", stop_price="94"))
    assert [(o.order_class, o.status, o.side) for o in legs] == [
        ("bracket", "new", "sell"), ("bracket", "held", "sell")]
    cache = {live._PENDING_EXIT_KEY: {"EEE": {"reason": "stop_loss",
                                              "intent": "swing_stop_exit",
                                              "since": "2026-06-01"}},
             live._SCAN_DONE_KEY: "2026-06-01"}
    out = tick(live, MON_1000, LiveAdapter(positions={"EEE": (10, 100.0, 930.0)},
                                           open_orders=legs), cache)
    assert out["EEE"] == -1 and out["_nexus_action_intents"] == {"EEE": "swing_stop_exit"}
    assert out["_nexus_position_sizes"]["EEE"] == {"sell_fraction": 1.0}

    # A simple working sell beside the legs is the exit in flight: nothing stacked.
    selling = _alpaca_book(leg("tp", "new", "limit", limit_price="109"),
                           order_row(id="mkt", client_order_id="cid-mkt", symbol="EEE",
                                     side=enum("sell"), qty="10"))
    assert tick(live, MON_1000, LiveAdapter(positions={"EEE": (10, 100.0, 930.0)},
                                            open_orders=selling), cache) == {}
    assert "EEE" in cache[live._PENDING_EXIT_KEY]


def test_the_bear_counter_advances_once_per_session(live, monkeypatch):
    monkeypatch.setattr(live.regime, "fetch_vix_close", lambda: 30.0)
    monkeypatch.setattr(live, "swing_indicators", lambda f, c: dict(IND, XLP=ind(80.0)))
    monkeypatch.setattr(live.ai_analyst, "analyse", scripted({"XLP": APPROVE}))
    cache = {}
    config = cfg(bear_regime_days=2)
    assert tick(live, MON_0920, LiveAdapter(), cache, config=config) == {}
    tick(live, MON_1000, LiveAdapter(), cache, config=config)
    bear = cache[live._BEAR_KEY]
    assert (bear["session"], bear["blocked_days"]) == ("2026-06-01", 1)
    out = tick(live, TUE_0920, LiveAdapter(), cache, config=config)
    assert cache[live._BEAR_KEY]["blocked_days"] == 2
    assert out["_nexus_action_intents"] == {"XLP": "swing_defensive_entry"}
    assert any(t.startswith("🐻 Bear Mode Day 2") for _, t in live.sent)


def test_a_restarted_scan_reuses_the_recorded_decision(live, monkeypatch):
    ai = scripted({"CCC": REJECT, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    signals_store.insert_signal(signals_store.new_signal(
        instance_id="swing-paper", lane="swing", symbol="AAA", session="2026-06-01",
        score=80, recommendation="approve", reasoning="ok", key_risks=[],
        size_adjustment=1.0, proposal={}, status="auto_approved"))
    out = tick(live, MON_0920, LiveAdapter(), {})
    assert out["AAA"] == 1 and "AAA" not in ai.calls


def test_a_holiday_or_an_early_tick_does_nothing(live, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not scan")

    monkeypatch.setattr(live.ai_analyst, "analyse", boom)
    early = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)       # 09:00 ET
    assert tick(live, early, LiveAdapter(), {}) == {}
    monkeypatch.setattr(live.clock, "is_trading_day", lambda d: False)
    assert tick(live, MON_0920, LiveAdapter(), {}) == {}


def test_with_the_ai_gate_off_every_signal_enters_unscored(live, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("gate off: no scoring")

    monkeypatch.setattr(live.ai_analyst, "analyse", boom)
    out = tick(live, MON_0920, LiveAdapter(), {},
               config=cfg(ai_gate_enabled=False, conviction_llm_provider=""))
    assert out["_nexus_executable_buys"] == ["AAA", "CCC", "DDD"]
    assert all(r["score"] is None and r["status"] == "auto_approved" for r in rows().values())


def test_the_scan_never_places_more_than_the_open_slots(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": APPROVE, "CCC": APPROVE, "DDD": APPROVE}))
    out = tick(live, MON_0920, LiveAdapter(), {}, config=cfg(max_positions=1))
    assert out["_nexus_executable_buys"] == ["AAA"]


def test_missing_credentials_refuse_the_scan(live, monkeypatch):
    def refuse(k, s):
        raise RuntimeError("Alpaca data credentials missing")

    monkeypatch.setattr(live.market_data, "data_client", refuse)
    cache = {}
    assert tick(live, MON_0920, LiveAdapter(), cache) == {}
    assert live._SCAN_DONE_KEY not in cache


# -- controller rulings (G6) ---------------------------------------------------

class StrictBook(LiveAdapter):
    """Alpaca's shape: list_open_orders answers an outage with [], the strict
    reader raises (or shows what the lenient one hides)."""

    def __init__(self, strict=None, **kw):
        super().__init__(**kw)
        self.strict = strict

    def list_open_orders(self, limit=200):
        return []

    def list_open_orders_strict(self, limit=200):
        if isinstance(self.strict, Exception):
            raise self.strict
        return list(self.strict or [])


def test_f3_an_unreadable_strict_book_places_nothing_this_tick(live, monkeypatch):
    lines = []
    monkeypatch.setattr(live, "_log", lambda msg, color="white": lines.append(msg))
    ai = scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    monkeypatch.setattr(live, "swing_indicators",
                        lambda f, c: dict(IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0)))
    cache = {live._PENDING_EXIT_KEY: {"FFF": {"reason": "stop_loss",
                                              "intent": "swing_stop_exit",
                                              "since": "2026-05-29"}}}
    down = StrictBook(strict=RuntimeError("orders endpoint down"),
                      positions={"EEE": (10, 100.0, 1_040.0), "FFF": (5, 100.0, 465.0)})
    assert tick(live, MON_0920, down, cache) == {}
    assert ai.calls == [] and live._SCAN_DONE_KEY not in cache
    assert "FFF" in cache[live._PENDING_EXIT_KEY]
    assert any("unreadable" in line for line in lines)
    # The book reads again: the scan runs and the pending exit is re-sent.
    up = StrictBook(strict=[], positions={"EEE": (10, 100.0, 1_040.0),
                                          "FFF": (5, 100.0, 465.0)})
    out = tick(live, MON_0940, up, cache)
    assert {s: d for s, d in out.items() if not s.startswith("_")} == {
        "AAA": 1, "EEE": -1, "FFF": -1}
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01"


def test_f3_the_strict_book_is_the_one_read(live, monkeypatch):
    cache = {live._PENDING_EXIT_KEY: {"EEE": {"reason": "stop_loss",
                                              "intent": "swing_stop_exit",
                                              "since": "2026-06-01"}},
             live._SCAN_DONE_KEY: "2026-06-01"}
    selling = StrictBook(strict=[NS(symbol="EEE", side="sell", order_class="simple")],
                         positions={"EEE": (10, 100.0, 930.0)})
    assert tick(live, MON_1000, selling, cache) == {}


def test_a_working_buy_counts_as_held_in_the_scan(live, monkeypatch):
    # An approved bracket for AAA is queued for the open: AAA is not scored
    # again, and it holds the technology slot (BBB is not scored either).
    ai = scripted({"CCC": REJECT, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    queued = StrictBook(strict=[NS(symbol="AAA", side="buy", order_class="bracket")])
    assert tick(live, MON_0920, queued, {}) == {}
    assert ai.calls == ["CCC", "DDD"]


def test_the_scan_does_not_stack_an_exit_on_a_working_sell(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": REJECT, "CCC": REJECT, "DDD": REJECT}))
    monkeypatch.setattr(live, "swing_indicators", lambda f, c: dict(
        IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0), FFF=ind(93.0, rsi=40.0, rsi_prev=45.0)))
    book = StrictBook(strict=[NS(symbol="EEE", side="sell", order_class="simple"),
                              NS(symbol="FFF", side="sell", order_class="bracket")],
                      positions={"EEE": (10, 100.0, 1_040.0), "FFF": (5, 100.0, 465.0)})
    out = tick(live, MON_0920, book, {})
    assert {s: d for s, d in out.items() if not s.startswith("_")} == {"FFF": -1}
    assert [t for c, t in live.sent if c == "swing_exit"] == ["SELL FFF"]


def test_live_buy_cash_carries_the_size_adjustment_and_no_share_count(live, monkeypatch):
    half = dict(APPROVE, position_size_adjustment=0.5)
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": half, "CCC": REJECT, "DDD": REJECT}))
    out = tick(live, MON_0920, LiveAdapter(), {})
    hint = out["_nexus_position_sizes"]["AAA"]
    assert hint["buy_cash"] == 6_250.0                 # 100,000 x 0.125 x 0.5
    assert not {"qty", "shares", "notional"} & set(hint)
    assert all(type(hint[k]) is bool for k in ("whole_shares", "fill_at_next_open"))
    assert rows()["AAA"]["proposal"]["shares"] == 125  # ST's base count, for the record


def test_an_unparseable_scan_time_refuses_the_scan(live, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not scan")

    lines = []
    monkeypatch.setattr(live, "_log", lambda msg, color="white": lines.append(msg))
    monkeypatch.setattr(live.ai_analyst, "analyse", boom)
    cache = {}
    after_midnight = datetime(2026, 6, 1, 4, 5, tzinfo=timezone.utc)   # 00:05 ET
    for at in (after_midnight, MON_0920, MON_1000):
        assert tick(live, at, LiveAdapter(), cache, config=cfg(scan_time_et="9h15")) == {}
    assert live._SCAN_DONE_KEY not in cache
    assert sum("scan_time_et" in line for line in lines) == 1
    # A pending exit is still re-sent: only the scan is refused.
    cache[live._PENDING_EXIT_KEY] = {"EEE": {"reason": "stop_loss",
                                             "intent": "swing_stop_exit",
                                             "since": "2026-06-01"}}
    held = LiveAdapter(positions={"EEE": (10, 100.0, 930.0)})
    assert tick(live, MON_1000, held, cache, config=cfg(scan_time_et="25:00"))["EEE"] == -1


def test_an_empty_sector_reads_unknown(live, monkeypatch, store):
    # AAA and BBB are unmapped and yfinance gives them no sector: "" must not
    # read as a shared sector (sector_conflict exempts only "unknown").
    for s in ("AAA", "BBB"):
        store.delete("SwingSectorMap", s)
    monkeypatch.setattr(live.sectors, "yf", NoNetworkYf(sector=""))
    out = tick(live, MON_0920, LiveAdapter(), {},
               config=cfg(ai_gate_enabled=False, conviction_llm_provider=""))
    assert out["_nexus_executable_buys"] == ["AAA", "BBB", "CCC", "DDD"]


def test_g1_minor_5_a_bad_row_skips_only_that_symbol_in_the_scan(live, monkeypatch):
    lines = []
    monkeypatch.setattr(live, "_log", lambda msg, color="white": lines.append(msg))
    ai = scripted({"BBB": REJECT, "CCC": REJECT, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    monkeypatch.setattr(live, "swing_indicators", lambda f, c: dict(
        IND, AAA=ind(100.0, rsi="n/a"), EEE=dict(ind(100.0), close="n/a"),
        FFF=ind(93.0, rsi=40.0, rsi_prev=45.0)))
    adapter = LiveAdapter(positions={"EEE": (10, 100.0, 1_000.0), "FFF": (5, 100.0, 465.0)})
    cache = {}
    out = tick(live, MON_0920, adapter, cache)
    assert {s: d for s, d in out.items() if not s.startswith("_")} == {"FFF": -1}
    assert ai.calls == ["BBB", "CCC", "DDD"]          # AAA skipped; BBB no longer blocked
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01"
    assert any("AAA" in line for line in lines) and any("EEE" in line for line in lines)


# -- review fix round 1 (G6) -----------------------------------------------------

class Blind(LiveAdapter):
    def refresh_positions(self):
        raise RuntimeError("positions endpoint down")

    def get_positions(self):
        raise RuntimeError("positions endpoint down")


class Preserved(LiveAdapter):
    """AlpacaAdapter.refresh_positions on a REST failure under 10 minutes old:
    the cached quantities come back with avg_entry_price=0.0."""

    def refresh_positions(self):
        return [NS(symbol=s, qty=v[0], avg_entry_price=0.0, market_value=v[2])
                for s, v in self.pos.items()]


def test_fix_a_unreadable_positions_are_not_ready(live, monkeypatch):
    # Review probe 1: eight names fill every slot. With the positions read
    # down, the scan must not see an empty book and enter AAA, CCC and DDD.
    lines = []
    monkeypatch.setattr(live, "_log", lambda msg, color="white": lines.append(msg))
    ai = scripted({"AAA": APPROVE, "CCC": APPROVE, "DDD": APPROVE})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    held = {f"H{i}": (10, 100.0, 1_000.0) for i in range(8)}
    assert tick(live, MON_0920, LiveAdapter(positions=held), {}) == {}     # no open slot
    cache = {}
    assert tick(live, MON_0920, Blind(positions=held), cache) == {}
    assert tick(live, MON_0940, Blind(positions=held), cache) == {}
    assert ai.calls == []
    assert live._SCAN_DONE_KEY not in cache and live._SCAN_KEY not in cache
    assert sum("positions" in line and "not ready" in line for line in lines) == 1
    assert tick(live, MON_1000, LiveAdapter(positions=held), cache) == {}
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01"


def test_fix_a_a_preserved_snapshot_is_not_ready_and_the_exit_survives(live, monkeypatch):
    # Review probe 2: the cached snapshot carries no entry price, so the RSI
    # cross on EEE was skipped silently and the scan latched, losing the
    # one-bar signal. Not ready: no latch, and the next tick sells EEE.
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": REJECT, "CCC": REJECT, "DDD": REJECT}))
    monkeypatch.setattr(live, "swing_indicators",
                        lambda f, c: dict(IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0)))
    cache = {}
    assert tick(live, MON_0920, Preserved(positions={"EEE": (10, 100.0, 1_040.0)}),
                cache) == {}
    assert live._SCAN_DONE_KEY not in cache and not cache.get(live._PENDING_EXIT_KEY)
    out = tick(live, MON_1000, LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0)}), cache)
    assert out["EEE"] == -1 and out["_nexus_action_intents"] == {"EEE": "swing_rsi_exit"}
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01"


def test_fix_a_a_missing_entry_price_the_trade_history_fills_is_ready(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": REJECT, "CCC": REJECT, "DDD": REJECT}))
    monkeypatch.setattr(live, "swing_indicators",
                        lambda f, c: dict(IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0)))

    class Filled(Preserved):
        def get_trade_history(self):
            return [{"action": "buy", "ticker": "EEE", "price": 100.0}]

    out = tick(live, MON_0920, Filled(positions={"EEE": (10, 100.0, 1_040.0)}), {})
    assert out["EEE"] == -1


def test_fix_a_the_adapters_stale_positions_flag_is_not_ready(live, monkeypatch):
    ai = scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    # A fresh process whose first refresh failed: an empty book, flag set.
    first_failed = LiveAdapter()
    first_failed._positions_stale_since = 1_000.0
    cache = {}
    assert tick(live, MON_0920, first_failed, cache) == {}

    # The call that clobbers the cache after 10 minutes clears the flag it
    # found set, and returns an empty book.
    class Clobbering(LiveAdapter):
        _positions_stale_since = 1_000.0

        def refresh_positions(self):
            self._positions_stale_since = None
            return []

    assert tick(live, MON_0940, Clobbering(), cache) == {}
    assert ai.calls == [] and live._SCAN_DONE_KEY not in cache
    out = tick(live, MON_1000, LiveAdapter(), cache)
    assert out["AAA"] == 1 and cache[live._SCAN_DONE_KEY] == "2026-06-01"


class HealthAccessor(LiveAdapter):
    """An adapter with A-live's public option_positions_health() (9b1c370).
    Its private flag says the opposite of the accessor, so a scan that still
    reads the flag is caught. An exception answer is raised."""

    def __init__(self, answer, *, private_stale=None, **kw):
        super().__init__(**kw)
        self.answer, self._positions_stale_since = answer, private_stale

    def option_positions_health(self):
        if isinstance(self.answer, Exception):
            raise self.answer
        return dict(self.answer)


def test_g8b_the_scan_reads_staleness_through_the_public_accessor(live, monkeypatch):
    ai = scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    cache = {}
    stale = HealthAccessor({"complete": True, "stale_since": 1_000.0})
    assert tick(live, MON_0920, stale, cache) == {}
    # The base adapter refuses the accessor: health unknown, so not ready.
    unknown = HealthAccessor(NotImplementedError("does not support option_positions_health"))
    assert tick(live, MON_0940, unknown, cache) == {}
    assert ai.calls == [] and live._SCAN_DONE_KEY not in cache
    fresh = HealthAccessor({"complete": True, "stale_since": None}, private_stale=1_000.0)
    out = tick(live, MON_1000, fresh, cache)
    assert out["AAA"] == 1 and cache[live._SCAN_DONE_KEY] == "2026-06-01"


@pytest.mark.parametrize("answer", ["not a dict", None, ["complete", True],
                                    {"complete": True}])
def test_g8b_an_unreadable_health_answer_holds_the_scan(live, monkeypatch, answer):
    """G8b minor 4: a non-dict accessor answer (and minor 1: a dict with no
    stale_since) is unknown health, so the scan decides nothing and retries."""
    ai = scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    cache = {}
    assert tick(live, MON_0920, HealthAccessor(answer), cache) == {}
    assert ai.calls == [] and live._SCAN_DONE_KEY not in cache
    out = tick(live, MON_0940, HealthAccessor({"complete": True, "stale_since": None}), cache)
    assert out["AAA"] == 1 and cache[live._SCAN_DONE_KEY] == "2026-06-01"


def test_fix_c_a_missing_vix_retries_until_the_scan_reads_it(live, monkeypatch):
    ai = scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    vix = iter([None, 15.0])
    monkeypatch.setattr(live.regime, "fetch_vix_close", lambda: next(vix))
    prior = {"session": "2026-05-29", "blocked_days": 3}
    cache = {live._BEAR_KEY: dict(prior)}
    assert tick(live, MON_0920, LiveAdapter(), cache) == {}
    assert live._SCAN_DONE_KEY not in cache and cache[live._BEAR_KEY] == prior
    assert ai.calls == []
    out = tick(live, MON_0940, LiveAdapter(), cache)
    assert out["AAA"] == 1 and cache[live._SCAN_DONE_KEY] == "2026-06-01"
    bear = cache[live._BEAR_KEY]
    assert (bear["session"], bear["blocked_days"]) == ("2026-06-01", 0)   # ruling 4


def test_fix_c_a_vix_still_missing_at_1000_proceeds_blocked(live, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("regime blocked: nothing to score")

    monkeypatch.setattr(live.ai_analyst, "analyse", boom)
    monkeypatch.setattr(live.regime, "fetch_vix_close", lambda: None)
    prior = {"session": "2026-05-29", "blocked_days": 3}
    cache = {live._BEAR_KEY: dict(prior)}
    for at in (MON_0920, MON_0940):
        assert tick(live, at, LiveAdapter(), cache) == {}
        assert live._SCAN_DONE_KEY not in cache and cache[live._BEAR_KEY] == prior
    assert tick(live, MON_1000, LiveAdapter(), cache) == {}
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01"
    assert cache[live._BEAR_KEY]["blocked_days"] == 4                     # ST: VIX None blocks


# -- review fix round 2 (G6) -----------------------------------------------------

class ClearedBook(LiveAdapter):
    """Another caller's refresh cleared the adapter's cache after 600 s (the
    flag reads None, the cache {}); the scan's own refresh fails again, sets
    the flag and returns the empty cache."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self._positions_stale_since = None

    def refresh_positions(self):
        self._positions_stale_since = 1_000.0
        return []

    def get_positions(self):
        return {}


def _pending(*symbols):
    return {s: {"reason": "rsi_overbought", "intent": "swing_rsi_exit", "since": "2026-05-29"}
            for s in symbols}


def test_fix2_1_a_degraded_empty_book_keeps_every_pending_exit(live, monkeypatch):
    # Review round 2, finding 1: the degraded empty book read as "position gone
    # — exit complete", and the healthy book at 10:00 still held EEE unsold.
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": REJECT, "CCC": REJECT, "DDD": REJECT}))
    cache = {live._PENDING_EXIT_KEY: _pending("EEE")}
    assert tick(live, MON_0920, ClearedBook(), cache) == {}
    assert tick(live, MON_0940, Blind(positions={"EEE": (10, 100.0, 1_040.0)}), cache) == {}
    assert set(cache[live._PENDING_EXIT_KEY]) == {"EEE"}
    assert live._SCAN_DONE_KEY not in cache
    out = tick(live, MON_1000, LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0)}), cache)
    assert out["EEE"] == -1 and out["_nexus_action_intents"]["EEE"] == "swing_rsi_exit"


def test_fix2_1_a_degraded_read_still_resends_a_visibly_held_exit(live, monkeypatch):
    # The cached snapshot still shows EEE (entry 0.0, not ready): its exit is
    # re-sent; FFF is not visible, but only a ready read may forget it.
    cache = {live._PENDING_EXIT_KEY: _pending("EEE", "FFF"), live._SCAN_DONE_KEY: "2026-06-01"}
    out = tick(live, MON_1000, Preserved(positions={"EEE": (10, 100.0, 1_040.0)}), cache)
    assert {s: d for s, d in out.items() if not s.startswith("_")} == {"EEE": -1}
    assert set(cache[live._PENDING_EXIT_KEY]) == {"EEE", "FFF"}
    # A ready read that shows FFF gone forgets it.
    tick(live, MON_1000, LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0)}), cache)
    assert set(cache[live._PENDING_EXIT_KEY]) == {"EEE"}


def test_fix2_2_a_refresh_that_fails_during_the_scan_is_not_ready(live, monkeypatch):
    # The flag is None before the scan's refresh; the refresh fails, sets it
    # and returns [] — an empty book that must not read as eight free slots.
    ai = scripted({"AAA": APPROVE, "CCC": APPROVE, "DDD": APPROVE})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    cache = {}
    assert tick(live, MON_0920, ClearedBook(), cache) == {}
    assert ai.calls == [] and live._SCAN_DONE_KEY not in cache


def test_fix2_3_one_entryless_name_is_skipped_and_the_scan_proceeds(live, monkeypatch):
    lines = []
    monkeypatch.setattr(live, "_log", lambda msg, color="white": lines.append(msg))
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": REJECT, "CCC": REJECT, "DDD": REJECT}))
    monkeypatch.setattr(live, "swing_indicators", lambda f, c: dict(
        IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0), ZZZ=ind(104.0, rsi=72.0, rsi_prev=68.0)))

    class OneEntryless(LiveAdapter):
        def refresh_positions(self):
            return [NS(symbol="EEE", qty=10, avg_entry_price=100.0, market_value=1_040.0),
                    NS(symbol="ZZZ", qty=5, avg_entry_price=0.0, market_value=520.0)]

    cache = {}
    out = tick(live, MON_0920, OneEntryless(positions={"EEE": (10, 100.0, 1_040.0),
                                                       "ZZZ": (5, 0.0, 520.0)}), cache)
    assert {s: d for s, d in out.items() if not s.startswith("_")} == {"EEE": -1}
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01"
    assert any("ZZZ" in line and "entry price" in line for line in lines)


def test_fix2_3_every_held_name_without_an_entry_price_is_not_ready(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT}))
    cache = {}
    snapshot = Preserved(positions={"EEE": (10, 100.0, 1_040.0), "FFF": (5, 100.0, 465.0)})
    assert tick(live, MON_0920, snapshot, cache) == {}
    assert live._SCAN_DONE_KEY not in cache


def test_g8a_the_scan_passes_its_working_buys_to_the_outcome_pass(live, monkeypatch):
    seen = []
    monkeypatch.setattr(live.calibration, "record_outcomes",
                        lambda *a, **k: seen.append(k) or 0)
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": REJECT, "CCC": REJECT, "DDD": REJECT}))
    book = [NS(symbol="EEE", side="buy"),                       # a GTC entry still working
            NS(symbol="FFF", side="sell"),
            NS(symbol="APH261002P00130000", side="buy")]         # a wheel buy-to-close
    tick(live, MON_0920, LiveAdapter(open_orders=book), {})
    assert seen and seen[0]["working"] == {"EEE"}


# -- FW-str minor (d): no entries from a scan that first ran after the open ----------

MON_1400 = datetime(2026, 6, 1, 18, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("first_tick", [MON_0940, MON_1400])
def test_a_first_scan_tick_after_the_open_plans_exits_but_no_entries(
        live, monkeypatch, first_tick):
    """strategies-review M-6: an instance started (or restarted) after 09:30
    scanned then and entered at intraday prices, with legs anchored on the
    prior close. ST's cron only ever ran at 09:15."""
    ai = scripted({"AAA": APPROVE, "CCC": APPROVE, "DDD": APPROVE})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    monkeypatch.setattr(live, "swing_indicators",
                        lambda f, c: dict(IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0)))
    lines = []
    monkeypatch.setattr(live, "_log", lambda msg, color="white": lines.append(msg))
    held = LiveAdapter(positions={"EEE": (10, 100.0, 1_040.0)})
    cache = {}
    out = tick(live, first_tick, held, cache)
    assert {s: d for s, d in out.items() if not s.startswith("_")} == {"EEE": -1}
    assert out["_nexus_action_intents"] == {"EEE": "swing_rsi_exit"}
    assert out["_nexus_executable_buys"] == []
    assert cache[live._SCAN_DONE_KEY] == "2026-06-01"
    assert ai.calls == [] and rows() == {}
    late = [m for m in lines if "NO ENTRIES" in m]
    assert len(late) == 1 and "09:30" in late[0]
    assert [c for c, _ in live.sent].count("swing_run_summary") == 1
    # The next session's pre-market scan enters as usual.
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT}))
    monkeypatch.setattr(live, "swing_indicators", lambda f, c: dict(IND))
    assert tick(live, TUE_0920, LiveAdapter(), cache)["AAA"] == 1


def test_a_pre_market_first_tick_that_was_not_ready_still_enters_later(live, monkeypatch):
    """The rule reads the day's FIRST scan tick: a 09:20 scan held back
    (positions, VIX) and prepared at 09:40 keeps its entries (fix a, fix c)."""
    ai = scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    unready = LiveAdapter()
    unready._positions_stale_since = 1_000.0
    cache = {}
    assert tick(live, MON_0920, unready, cache) == {}
    assert tick(live, MON_0940, LiveAdapter(), cache)["AAA"] == 1


def test_a_restart_after_the_open_keeps_the_first_tick_it_saw(live, monkeypatch):
    """The first-tick stamp lives in the strategy cache, which survives a
    restart; a cache that never saw the pre-market tick is late."""
    ai = scripted({"AAA": APPROVE, "CCC": REJECT, "DDD": REJECT})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    monkeypatch.setattr(live.regime, "fetch_vix_close", lambda: None)
    cache = {}
    assert tick(live, MON_0920, LiveAdapter(), cache) == {}      # VIX retry
    monkeypatch.setattr(live.regime, "fetch_vix_close", lambda: 15.0)
    restarted = dict(cache)
    assert tick(live, MON_0940, LiveAdapter(), restarted)["AAA"] == 1


# -- FW-lo-I5 (sizing): cash that secures short puts is not the swing lane's ---------

class PutBook(LiveAdapter):
    """An account with a cash balance apart from its buying power, open short
    puts and working sell-to-open orders, as the real adapter reports them."""

    def __init__(self, *, cash, bp, puts=(), sto=(), health=None, **kw):
        super().__init__(bp=bp, options=[
            NS(symbol=f"XYZ261016P{int(strike * 1000):08d}", qty=-qty, option_type="put",
               strike=strike, underlying="XYZ", expiry="2026-10-16")
            for strike, qty in puts], open_orders=[
            NS(symbol=f"QRS261016P{int(strike * 1000):08d}", side="sell", qty=qty,
               filled_qty=0, position_intent="sell_to_open", asset_class="us_option",
               order_class="simple")
            for strike, qty in sto], **kw)
        self.cash, self.health = cash, health

    def refresh_cash(self):
        return NS(cash=self.cash, buying_power=self.bp)

    def get_cash(self):
        return self.cash

    def option_positions_health(self):
        return dict(self.health or {"complete": True, "stale_since": None})


def _entries(out):
    return {s: out["_nexus_position_sizes"][s]["buy_cash"]
            for s in out.get("_nexus_executable_buys", [])}


def test_open_and_pending_put_collateral_comes_off_the_swing_budget(live, monkeypatch):
    """live-orders-review I-5: the lane sized against buying power with no
    deduction for short puts, so swing entries could spend the cash securing
    them. $100k cash less a $40k open put and a $40k working sell-to-open
    leaves $20k: AAA takes its $12,500, CCC the $7,500 left, DDD nothing."""
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": APPROVE, "CCC": APPROVE, "DDD": APPROVE}))
    book = PutBook(cash=100_000.0, bp=100_000.0, puts=[(200.0, 2)], sto=[(100.0, 4)])
    out = tick(live, MON_0920, book, {})
    assert _entries(out) == {"AAA": 12_500.0, "CCC": 7_500.0}


def test_with_puts_open_margin_buying_power_does_not_lift_the_budget(live, monkeypatch):
    """Cash-secured means CASH: with puts open the budget is the smaller of
    buying power and cash, less the collateral ($50k - $30k = $20k), never
    the $200k margin figure less it."""
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": APPROVE, "CCC": APPROVE, "DDD": APPROVE}))
    book = PutBook(cash=50_000.0, bp=200_000.0, puts=[(150.0, 2)])
    out = tick(live, MON_0920, book, {})
    assert _entries(out) == {"AAA": 12_500.0, "CCC": 7_500.0}


def test_without_puts_the_budget_is_st_s_buying_power(live, monkeypatch):
    monkeypatch.setattr(live.ai_analyst, "analyse",
                        scripted({"AAA": APPROVE, "CCC": APPROVE, "DDD": APPROVE}))
    book = PutBook(cash=5_000.0, bp=200_000.0)
    out = tick(live, MON_0920, book, {})
    assert _entries(out) == {"AAA": 12_500.0, "CCC": 12_500.0, "DDD": 12_500.0}


def test_an_unreadable_option_book_refuses_entries_but_runs_exits(live, monkeypatch):
    ai = scripted({"AAA": APPROVE, "CCC": APPROVE, "DDD": APPROVE})
    monkeypatch.setattr(live.ai_analyst, "analyse", ai)
    monkeypatch.setattr(live, "swing_indicators",
                        lambda f, c: dict(IND, EEE=ind(104.0, rsi=72.0, rsi_prev=68.0)))
    lines = []
    monkeypatch.setattr(live, "_log", lambda msg, color="white": lines.append(msg))
    book = PutBook(cash=100_000.0, bp=100_000.0, positions={"EEE": (10, 100.0, 1_040.0)},
                   health={"complete": False, "stale_since": None})
    cache = {}
    out = tick(live, MON_0920, book, cache)
    assert {s: d for s, d in out.items() if not s.startswith("_")} == {"EEE": -1}
    assert ai.calls == [] and cache[live._SCAN_DONE_KEY] == "2026-06-01"
    assert any("REFUSING ENTRIES" in m and "short puts" in m for m in lines)
