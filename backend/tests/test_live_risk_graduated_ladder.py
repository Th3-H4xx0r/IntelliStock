"""The drawdown ladder is graduated, and it never traps the account in cash.

`evaluate_drawdown` sets `new_exposure_allowed=False` at ANY level from soft
up, and the order gate read that one flag as "block every buy". On doc 200
(soft 25% / hard 35% / kill 45%) a trend-off exit taken at a 25% drawdown
sold TQQQ into cash and then refused the BIL buy the same rule asked for: the
account would sit in cash with no recovery path, and no rung below kill ever
let it out.

The approved ladder (operator, 2026-10-30):
- soft: block new buys of leveraged symbols only. GLD/GDX/XLE and BIL trade.
- hard: block every buy except the strategy's risk-off symbol (BIL).
- kill: as hard; the transition still cancels working buys and pages.
- At every level a buy of the risk-off symbol is allowed.

Everything here is built from the real producers: the risk row comes from
`initialize_risk_state` + `evaluate_drawdown` through broker.py's own
`_refresh_live_account_risk_state`, the dependency view from broker.py's own
`_live_order_dependency_snapshot`, and the verdict from `UnifiedOrderGate`.
"""
import datetime as dtm
import threading
from decimal import Decimal
from types import SimpleNamespace

import pytest

import live_risk_state
from live_orders import OrderIntent, OrderSide, OrderSource, UnifiedOrderGate
from live_risk_state import (
    DEFAULT_LEVERAGED_SYMBOLS,
    RiskLimits,
    RiskStateStore,
    InMemoryRiskBackend,
    buy_allowed_at_level,
    evaluate_drawdown,
    initialize_risk_state,
)
from market_marks import MarketMark, MarkQuality, MarkSource
from strategy_eb import DEFAULTS as EB_DEFAULTS
from swing_broker_harness import extract



class _Frozen(dtm.datetime):
    @classmethod
    def now(cls, tz=None):
        return RTH if tz is not None else RTH.replace(tzinfo=None)


#: broker.py's snapshot checks `isinstance(x, datetime.datetime)` against the
#: clock it is handed, so every stamp is a `_Frozen`.
CLOCK = SimpleNamespace(datetime=_Frozen, timezone=dtm.timezone,
                        timedelta=dtm.timedelta, date=dtm.date)
RTH = _Frozen(2026, 10, 30, 14, 0, 10, tzinfo=dtm.timezone.utc)   # 10:00 ET
BOOT = RTH - dtm.timedelta(days=30)
HWM = Decimal("6081.76")            # alpaca-main's stored high-water mark
#: doc 200's envelope, read from the strategy's own defaults.
DOC_200_LIMITS = RiskLimits(
    max_order_fraction=EB_DEFAULTS["live_max_order_fraction"],
    max_symbol_fraction=EB_DEFAULTS["live_max_symbol_fraction"],
    max_leveraged_fraction=EB_DEFAULTS["live_max_leveraged_fraction"],
    soft=EB_DEFAULTS["live_soft_drawdown"],
    hard=EB_DEFAULTS["live_hard_drawdown"],
    kill=EB_DEFAULTS["live_kill_drawdown"])
#: doc 200's lanes as Postgres stores them (risk_off/cash symbol explicit).
DOC_200 = [{"strategy": "strategy_eb", "weight": 1.0,
            "config": {"strategy_eb_enabled": True, "risk_off_symbol": "BIL",
                       "cash_symbol": "BIL"}},
           {"strategy": "graph_nexus_analysis", "weight": 0.0, "config": {}}]
PRICES = {"TQQQ": 70.0, "GLD": 370.0, "GDX": 75.0, "XLE": 68.0, "BIL": 91.6}


def _equity_at(drawdown):
    return (HWM * (Decimal("1") - Decimal(drawdown))).quantize(Decimal("0.01"))


# --- the policy itself ---------------------------------------------------------

@pytest.mark.parametrize("level, allowed", [
    ("normal", {"TQQQ", "GLD", "GDX", "XLE", "BIL"}),
    ("soft", {"GLD", "GDX", "XLE", "BIL"}),
    ("hard", {"BIL"}),
    ("kill", {"BIL"}),
])
def test_the_ladder_is_graduated(level, allowed):
    for symbol in PRICES:
        assert buy_allowed_at_level(
            level, symbol, risk_off_symbols={"BIL"}) is (symbol in allowed), \
            (level, symbol)


@pytest.mark.parametrize("level", ["normal", "soft", "hard", "kill"])
def test_the_risk_off_symbol_is_buyable_at_every_level(level):
    assert buy_allowed_at_level(level, "bil", risk_off_symbols={"BIL"})


def test_soft_blocks_every_leveraged_symbol_the_caps_count():
    for symbol in DEFAULT_LEVERAGED_SYMBOLS:
        assert not buy_allowed_at_level("soft", symbol, risk_off_symbols={"BIL"})


def test_a_leveraged_risk_off_symbol_is_never_exempt():
    """A config that names SQQQ as the risk-off leg must not turn the hard and
    kill rungs into permission to buy a 3x ETF."""
    for level in ("soft", "hard", "kill"):
        assert not buy_allowed_at_level(level, "SQQQ", risk_off_symbols={"SQQQ"})


def test_with_no_risk_off_symbol_hard_blocks_everything():
    for symbol in PRICES:
        assert not buy_allowed_at_level("hard", symbol)
        assert not buy_allowed_at_level("kill", symbol, risk_off_symbols=())


def test_an_unknown_level_fails_closed_except_for_the_risk_off_symbol():
    assert not buy_allowed_at_level("bogus", "GLD", risk_off_symbols={"BIL"})
    assert buy_allowed_at_level("bogus", "BIL", risk_off_symbols={"BIL"})


@pytest.mark.parametrize("drawdown, level", [
    ("0.12", "normal"), ("0.26", "soft"), ("0.36", "hard"), ("0.46", "kill")])
def test_the_producer_lands_on_the_rung_the_policy_reads(drawdown, level):
    row = initialize_risk_state("alpaca-main", "acct-1", HWM, BOOT,
                                limits=DOC_200_LIMITS)
    got = evaluate_drawdown(row, _equity_at(drawdown), RTH, limits=DOC_200_LIMITS)
    assert got.level == level
    # The flag keeps its meaning: UNRESTRICTED new exposure.
    assert got.new_exposure_allowed is (level == "normal")


# --- broker.py end to end: refresh -> dependency view -> gate -----------------

def _mark(symbol):
    price = PRICES[symbol]
    return MarketMark(symbol=symbol, price=price, bid=price - 0.01,
                      ask=price + 0.01, bid_size=100, ask_size=100,
                      observed_at=RTH - dtm.timedelta(seconds=1), received_at=RTH,
                      source=MarkSource.REST_QUOTE, feed="sip",
                      quality=MarkQuality.CONSOLIDATED, session="regular")


class _Adapter:
    def __init__(self, equity):
        self._account_equity = float(equity)
        self._cash = float(equity)
        self._positions = {"TQQQ": 10.0}
        self._instance_id = "alpaca-main"
        self._account_id = "acct-1"
        self._positions_stale_since = None
        self._market_marks = SimpleNamespace(get=_mark)
        self.cancelled = []

    def list_open_orders_strict(self, limit=200):
        return []

    def cancel_order(self, broker_order_id):
        self.cancelled.append(broker_order_id)
        return True


def _world(drawdown, monkeypatch, strategies=DOC_200):
    # The transition pages through live_alerts; a test must never reach it.
    pages = []
    monkeypatch.setattr(live_risk_state, "_default_risk_alert",
                        lambda **kw: pages.append(kw))
    store = RiskStateStore(InMemoryRiskBackend())
    row = store.save(initialize_risk_state("alpaca-main", "acct-1", HWM, BOOT,
                                           limits=DOC_200_LIMITS))
    # Every dependency but risk_state healthy; the refresh writes risk_state.
    state = {n: "healthy" for n in ("kill_switch", "cash", "positions",
                                    "persistence", "watchdog", "calendar")}
    state.update({f"{n}_at": RTH for n in ("kill_switch", "cash", "positions",
                                           "persistence", "watchdog",
                                           "calendar")})
    state.update({"market_open": True})
    helpers = extract(("_lane_enabled", "_truthy", "_merged_strategy_settings"),
                      assigns=("_LANE_ENABLE_FLAGS",))
    lines = []
    namespace = {
        "datetime": CLOCK, "_live_order_dependency_lock": threading.Lock(),
        "_live_order_dependency_state": state,
        "_live_stock_order_service": SimpleNamespace(),      # armed
        "_live_risk_store": store, "_live_risk_state": row,
        "_live_risk_caps_rescaled_logged": False,
        "_live_risk_limits_for_this_document": lambda: DOC_200_LIMITS,
        "_log": lambda message, color="white": lines.append((message, color)),
        "instance_id": "alpaca-main", "MODE_LIVE": "live", "mode": "live",
        "live_broker_type": "alpaca", "live_brokerage_id": "acct-1",
        "_cached_strategies": strategies,
        "_LANE_ENABLE_FLAGS": helpers["_LANE_ENABLE_FLAGS"],
        "_lane_enabled": helpers["_lane_enabled"],
        "_truthy": helpers["_truthy"],
        "_merged_strategy_settings": helpers["_merged_strategy_settings"],
    }
    ns = extract(("_refresh_live_account_risk_state",
                  "_live_risk_off_symbols",
                  "_live_option_dependency_snapshot",
                  "_pending_sell_to_open_collateral",
                  "_open_order_idempotency_keys",
                  "_live_order_dependency_snapshot"),
                 assigns=("_live_option_quotes",), namespace=namespace,
                 check=("_refresh_live_account_risk_state",
                        "_live_risk_off_symbols",
                        "_live_order_dependency_snapshot"))
    adapter = _Adapter(_equity_at(drawdown))
    refreshed = ns["_refresh_live_account_risk_state"](adapter, RTH)
    return ns, adapter, refreshed, pages


def _buy(ns, symbol, quantity="1"):
    return OrderIntent(
        account_id="acct-1", instance_id="alpaca-main",
        source=OrderSource.STRATEGY, reason="strategy_eb_rebalance",
        symbol=symbol, side=OrderSide.BUY, quantity=Decimal(quantity),
        reduce_only=False, decision_at=RTH,
        quote_at=RTH - dtm.timedelta(seconds=1),
        risk_snapshot_id=ns["_live_order_dependency_state"]["risk_snapshot_id"],
        order_type="market", tif="day",
        reference_price=Decimal(str(PRICES[symbol])))


def _decide(ns, adapter, intent):
    snap = ns["_live_order_dependency_snapshot"](adapter, intent)
    return UnifiedOrderGate().evaluate(intent, snap)


@pytest.mark.parametrize("drawdown, level, allowed", [
    ("0.12", "normal", {"TQQQ", "GLD", "GDX", "XLE", "BIL"}),
    ("0.26", "soft", {"GLD", "GDX", "XLE", "BIL"}),
    ("0.36", "hard", {"BIL"}),
    ("0.46", "kill", {"BIL"}),
])
def test_the_gate_admits_exactly_the_rung_s_buys(drawdown, level, allowed,
                                                 monkeypatch):
    ns, adapter, refreshed, _pages = _world(drawdown, monkeypatch)
    assert refreshed.level == level
    for symbol in PRICES:
        decision = _decide(ns, adapter, _buy(ns, symbol))
        if symbol in allowed:
            assert decision.allowed, (level, symbol, decision.reason_codes)
        else:
            assert not decision.allowed, (level, symbol)
            assert decision.reason_codes == ("dependency.risk_state.unhealthy",), \
                (level, symbol, decision.reason_codes)


def test_a_trend_off_exit_at_soft_can_still_buy_its_bil(monkeypatch):
    """The trap itself: TQQQ sold at a 26% drawdown, then the BIL buy the
    trend-off book asks for. It used to be refused, forever."""
    ns, adapter, _r, _p = _world("0.26", monkeypatch)
    adapter._positions = {}
    assert _decide(ns, adapter, _buy(ns, "BIL", "14")).allowed


def test_reduce_only_exits_pass_at_every_level(monkeypatch):
    for drawdown in ("0.26", "0.36", "0.46"):
        ns, adapter, _r, _p = _world(drawdown, monkeypatch)
        sell = OrderIntent(
            account_id="acct-1", instance_id="alpaca-main",
            source=OrderSource.STRATEGY, reason="strategy_eb_trend_off",
            symbol="TQQQ", side=OrderSide.SELL, quantity=Decimal("10"),
            reduce_only=True, decision_at=RTH,
            quote_at=RTH - dtm.timedelta(seconds=1),
            risk_snapshot_id=ns["_live_order_dependency_state"]["risk_snapshot_id"],
            order_type="market", tif="day", reference_price=Decimal("70"))
        assert _decide(ns, adapter, sell).allowed, drawdown


def test_a_missing_risk_row_still_blocks_every_buy(monkeypatch):
    """`unknown` (no row, bootstrap refused) is not a ladder rung: BIL waits
    with everything else, exactly as before."""
    ns, adapter, _r, _p = _world("0.12", monkeypatch)
    ns["_live_order_dependency_state"]["risk_state"] = "unknown"
    ns["_live_risk_state"] = None
    decision = _decide(ns, adapter, _buy(ns, "BIL"))
    assert "dependency.risk_state.unknown" in decision.reason_codes


def test_a_document_without_eb_has_no_risk_off_exemption(monkeypatch):
    ns, adapter, _r, _p = _world("0.36", monkeypatch,
                                 strategies=[{"strategy": "strategy_swing",
                                              "config": {"strategy_swing_enabled": True}}])
    assert not _decide(ns, adapter, _buy(ns, "BIL")).allowed


def test_a_disabled_eb_lane_contributes_no_risk_off_symbol(monkeypatch):
    ns, _a, _r, _p = _world("0.12", monkeypatch)
    resolve = ns["_live_risk_off_symbols"]
    assert resolve(DOC_200) == frozenset({"BIL"})
    assert resolve([{"strategy": "strategy_eb",
                     "config": {"strategy_eb_enabled": False,
                                "risk_off_symbol": "BIL"}}]) == frozenset()
    assert resolve(None) == frozenset()
    # Keys the document omits fall back to the strategy's own defaults.
    assert resolve([{"strategy": "strategy_eb",
                     "config": {"strategy_eb_enabled": True}}]) == frozenset({"BIL"})


def test_the_kill_transition_still_pages_and_never_liquidates(monkeypatch):
    ns, adapter, refreshed, pages = _world("0.46", monkeypatch)
    assert refreshed.level == "kill"
    assert len(pages) == 1
    assert adapter.cancelled == []          # nothing working to cancel
