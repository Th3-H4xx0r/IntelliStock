"""swing-port Task 12: OPASN/OPEXP/OPEXC become lifecycle facts, exactly
once, under a cursor kept in the wheel lane's strategy cache."""
import ast
import datetime as datetime_module

from swing_broker_harness import extract, source, tree

# --- EB pin (written first, green on the pre-change code at 40f880b) ---------
#
# Every swing-port block the live loop runs after the stock loop is gated. On
# doc 200 (alpaca-main, an enabled strategy_eb and nothing else, and no option
# orders) every one of those guards is False on every live tick, so EB never
# polls activities, reads the options level or submits an option order.

EB_DOC = [{"strategy": "strategy_eb", "execution_position": 0,
           "config": {"strategy_eb_enabled": True,
                      "honour_single_position_cap": True,
                      "broker_max_single_position_pct": 0.95}}]
WHEEL_DOC = [{"strategy": "strategy_wheel",
              "config": {"strategy_wheel_enabled": True}}]


def _post_stock_loop_guards():
    """The test expression of every top-level `if` between the end of the
    stock loop and the per-tick portfolio snapshot, at the loop's indent."""
    text = source()
    start = text.index("            for symbol in _exec_order:")
    end = text.index("## Save portfolio snapshot every loop", start)
    first = text[:start].count("\n") + 1
    last = text[:end].count("\n") + 1
    guards = []
    for node in ast.walk(tree()):
        if (isinstance(node, ast.If) and first < node.lineno < last
                and node.col_offset == 12):
            guards.append(node.test)
    return guards


def _evaluate(test, **state):
    ns = extract(("_lane_enabled", "_truthy", "_merged_strategy_settings"),
                 assigns=("_LANE_ENABLE_FLAGS",))
    ns.update({"MODE_LIVE": "live", "mode": "live",
               "live_broker_type": "alpaca",
               "_live_stock_order_service": object(),
               "live_adapter": object(), "nexus_option_orders": []})
    ns.update(state)
    return bool(eval(compile(ast.Expression(test), "broker.py", "eval"), ns))


def test_every_post_stock_loop_swing_block_is_closed_on_the_eb_document():
    guards = _post_stock_loop_guards()
    assert guards, "the Task 10 option block moved"
    for eb_state in ({"_cached_strategies": EB_DOC},
                     {"_cached_strategies": None},
                     {"_cached_strategies": EB_DOC, "live_broker_type": "ALPACA"}):
        assert [_evaluate(g, **eb_state) for g in guards] == [False] * len(guards)


def test_the_guard_evaluator_is_not_vacuous():
    """With an enabled wheel lane and a wheel order every guard opens, so the
    EB pin above is a statement about the document, not about the harness."""
    guards = _post_stock_loop_guards()
    opened = [_evaluate(g, _cached_strategies=WHEEL_DOC,
                        nexus_option_orders=[{"contract": "X"}])
              for g in guards]
    assert opened == [True] * len(guards)


# --- the brief's tests --------------------------------------------------------

import sys  # noqa: E402
import types  # noqa: E402
from decimal import Decimal  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from broker_adapters.base import OptionActivityDTO, OptionPositionDTO  # noqa: E402
from live_orders import (  # noqa: E402
    InMemoryLifecycleBackend,
    LifecycleState,
    LiveOrderService,
    OrderLifecycleStore,
    OrderSide,
    OrderSource,
)
from swing_live_fixtures import OCC, RTH  # noqa: E402

CALL = "APH261009C00140000"


def _poller():
    return extract(("_poll_option_activities",),
                   assigns=("_OPTION_ACTIVITY_TYPES",
                            "_option_activity_last_poll"),
                   namespace={"datetime": datetime_module})[
        "_poll_option_activities"]


def _put(symbol=OCC, option_type="put", strike=130.0):
    return OptionPositionDTO(symbol, "APH", option_type, strike, "2026-10-09",
                             -1, 1.2, 3.5, -350.0, -230.0)


class _Account:
    def __init__(self, activities, *, positions=None, contracts=None,
                 fail=False):
        self.activities = list(activities)
        self.calls = []
        self._option_positions = dict(positions or {})
        self.contracts = dict(contracts or {})
        self.fail = fail
        self.refreshes = 0

    def get_option_activities(self, types=("OPASN", "OPEXP", "OPEXC"),
                              after=None):
        self.calls.append((tuple(types), after))
        if self.fail:
            raise RuntimeError("activities endpoint unreachable")
        return list(self.activities)

    def option_contract_meta(self, symbol):
        return self.contracts.get(symbol)

    def refresh_positions(self):
        self.refreshes += 1
        return []


def _service(**kwargs):
    fills = []
    service = LiveOrderService(
        account_id="acct-1", instance_id="instance-1", snapshot_provider=None,
        transport=None,
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        confirmed_fill_handler=kwargs.pop("handler", fills.append), **kwargs)
    return service, fills


def _assigned(activity_id="act-1", symbol=OCC, qty=-1.0, day="2026-10-09"):
    return OptionActivityDTO(activity_id, "OPASN", symbol, qty, day, None)


def _collect():
    sent = []
    return sent, lambda instance_id, **fields: sent.append(
        {"instance_id": instance_id, **fields})


def _ignore(*_args, **_kwargs):
    return None


def test_a_put_assignment_adds_shares_owned_by_the_wheel_once():
    poll = _poller()
    service, fills = _service()
    account = _Account([_assigned()], positions={OCC: _put()})
    cache, (sent, notify) = {}, _collect()
    done = poll(account, service, cache, now_utc=RTH, notify=notify,
                min_interval_s=0)
    assert [a.id for a in done] == ["act-1"]
    (fill,) = fills
    assert (fill.event.symbol, fill.event.side, fill.incremental_quantity,
            fill.incremental_price, fill.cash_delta) == (
        "APH", OrderSide.BUY, Decimal("100"), Decimal("130"), Decimal("-13000"))
    record = next(r for r in service.lifecycle_store.list_for_instance(
        "instance-1"))
    assert record.intent.source is OrderSource.OPTION_ACTIVITY
    assert cache["_engine_wheel_assignments"] == [{
        "activity_id": "act-1", "contract": OCC, "underlying": "APH",
        "shares": 100, "side": "buy", "strike": 130.0, "date": "2026-10-09"}]
    assert cache["_engine_option_activity_cursor"] == {
        "after": "2026-10-08", "seen": ["act-1"]}
    assert sent == [{"instance_id": "instance-1", "symbol": "APH", "qty": 100,
                     "price": 130.0, "date": "2026-10-09"}]
    assert account.refreshes == 1


def test_a_replay_with_a_lost_cursor_records_and_announces_nothing_twice():
    poll = _poller()
    service, fills = _service()
    account = _Account([_assigned()], positions={OCC: _put()})
    sent, notify = _collect()
    poll(account, service, {}, now_utc=RTH, notify=notify, min_interval_s=0)
    poll(account, service, {}, now_utc=RTH, notify=notify, min_interval_s=0)
    assert len(fills) == 1 and len(sent) == 1


def test_a_seen_activity_is_skipped_by_id():
    poll = _poller()
    service, fills = _service()
    cache = {"_engine_option_activity_cursor": {"after": "2026-10-08",
                                                "seen": ["act-1"]}}
    account = _Account([_assigned()], positions={OCC: _put()})
    assert poll(account, service, cache, now_utc=RTH, min_interval_s=0) == []
    assert account.calls[0][1] == "2026-10-08" and fills == []


#: An adjusted contract (a corporate action renamed its root): its OCC symbol
#: names no tradable underlying, so it is never booked from the symbol.
ADJUSTED = "APH1261009P00130000"


def test_an_unresolvable_contract_is_retried_not_marked_seen():
    poll = _poller()
    service, fills = _service()
    lines = []
    account = _Account([_assigned(symbol=ADJUSTED)])
    cache = {}
    assert poll(account, service, cache, now_utc=RTH, min_interval_s=0,
                log=lambda m, c="white": lines.append((m, c))) == []
    assert fills == [] and "_engine_option_activity_cursor" not in cache
    assert any(c == "red" and "NOT recorded" in m for m, c in lines)
    account.contracts[ADJUSTED] = _put(ADJUSTED)
    poll(account, service, cache, now_utc=RTH, min_interval_s=0,
         notify=_ignore)
    assert len(fills) == 1


def test_a_short_call_assignment_sells_the_shares():
    poll = _poller()
    service, fills = _service()
    account = _Account([_assigned(symbol=CALL)],
                       positions={CALL: _put(CALL, "call", 140.0)})
    poll(account, service, {}, now_utc=RTH, notify=_ignore, min_interval_s=0)
    (fill,) = fills
    assert (fill.event.side, fill.incremental_quantity, fill.cash_delta) == (
        OrderSide.SELL, Decimal("100"), Decimal("14000"))


def test_an_expiry_is_logged_and_refreshed_but_records_no_fill():
    poll = _poller()
    service, fills = _service()
    expiry = OptionActivityDTO("exp-1", "OPEXP", OCC, -1.0, "2026-10-09", 0.0)
    account = _Account([expiry], positions={OCC: _put()})
    cache, (sent, notify) = {}, _collect()
    done = poll(account, service, cache, now_utc=RTH, notify=notify,
                min_interval_s=0)
    assert [a.id for a in done] == ["exp-1"] and fills == [] and sent == []
    assert cache["_engine_option_activity_cursor"]["seen"] == ["exp-1"]
    assert account.refreshes == 1


def test_polls_are_throttled_and_a_read_failure_changes_nothing():
    poll = _poller()
    service, _fills = _service()
    ticks = [1000.0]
    account = _Account([], fail=True)
    cache = {}
    assert poll(account, service, cache, now_utc=RTH,
                monotonic=lambda: ticks[0]) == []
    assert poll(account, service, cache, now_utc=RTH,
                monotonic=lambda: ticks[0]) == []
    assert len(account.calls) == 1 and cache == {}
    ticks[0] += 301.0
    poll(account, service, cache, now_utc=RTH, monotonic=lambda: ticks[0])
    assert len(account.calls) == 2
    assert account.calls[0][1] == "2026-09-28"


def test_the_loop_polls_only_for_an_enabled_wheel_lane_before_orders():
    text = source()
    stock_loop = text.index("for symbol in _exec_order:")
    poll = text.index("_poll_option_activities(\n", stock_loop)
    execute = text.index("_execute_option_intents(\n", stock_loop)
    assert poll < execute
    guard = text[text.rindex("if (", stock_loop, poll):poll]
    assert '_lane_enabled(_cached_strategies, "strategy_wheel")' in guard


# --- controller ruling 1 (L5): the activity id, the cursor, ownership, and a
# --- non-list activities answer ------------------------------------------------

def test_the_assignment_is_recorded_under_the_brokers_activity_id():
    poll = _poller()
    service, _fills = _service()
    account = _Account([_assigned("act-77")], positions={OCC: _put()})
    poll(account, service, {}, now_utc=RTH, notify=_ignore, min_interval_s=0)
    (record,) = service.lifecycle_store.list_for_instance("instance-1")
    assert record.broker_order_id == "act-77"
    assert record.state is LifecycleState.FILLED
    assert record.client_order_id.startswith("opasn-")
    assert (record.intent.source, record.intent.symbol, record.intent.side,
            record.intent.quantity, record.intent.reduce_only) == (
        OrderSource.OPTION_ACTIVITY, "APH", OrderSide.BUY, Decimal("100"), False)


def test_the_cursor_survives_the_strategy_cache_save_and_restore(monkeypatch):
    """The cursor rides the wheel lane's strategy cache, which the live loop
    persists per lane (strategy_cache_persistence). After a restart the
    restored cursor skips the handled activity and reads from its date."""
    import strategy_cache_persistence as scp

    rows = []
    monkeypatch.setattr(scp, "_ensure_table", lambda *a, **k: True)
    monkeypatch.setattr(scp.store, "insert",
                        lambda table, row, **kw: rows.append(row))
    poll = _poller()
    service, fills = _service()
    account = _Account([_assigned()], positions={OCC: _put()})
    lane_cache = {}
    poll(account, service, lane_cache, now_utc=RTH, notify=_ignore,
         min_interval_s=0)
    assert scp.save_strategy_cache_to_db(None, None, "instance-1",
                                         "strategy_wheel", lane_cache)
    restored = {}
    scp.merge_loaded_cache_into(
        restored, scp._deserialize_cache_from_blob(rows[-1]["cache_json"]))
    assert restored["_engine_option_activity_cursor"] == {
        "after": "2026-10-08", "seen": ["act-1"]}
    assert restored["_engine_wheel_assignments"] == lane_cache[
        "_engine_wheel_assignments"]
    rebooted = _poller()
    sent, notify = _collect()
    assert rebooted(account, service, restored, now_utc=RTH, notify=notify,
                    min_interval_s=0) == []
    assert account.calls[-1][1] == "2026-10-08"
    assert len(fills) == 1 and sent == []


def test_the_live_loop_keeps_the_cursor_in_the_persisted_wheel_cache():
    text = source()
    call = text.index("_poll_option_activities(\n", text.index(
        "for symbol in _exec_order:"))
    window = " ".join(text[call:call + 600].split())
    assert '_strategy_cache.setdefault("strategy_wheel", {})' in window
    assert "_reconcile_alpaca_ownership( live_adapter, _live_stock_order_service)" in window


def _equity_row(symbol, qty, price):
    return SimpleNamespace(
        symbol=symbol, qty=str(qty), market_value=str(qty * price),
        avg_entry_price=str(price), current_price=str(price),
        asset_class=SimpleNamespace(value="us_equity"),
        side=SimpleNamespace(value="long"))


def _real_book():
    """A real AlpacaAdapter holding one short APH put, a real service whose
    fills move the adapter's mirror, and broker.py's own reconcile."""
    import threading

    from swing_alpaca_fakes import (
        FakeOptionsTradingClient,
        contract_row,
        make_adapter,
        option_position,
    )

    client = FakeOptionsTradingClient(
        positions=[option_position(OCC)],
        contracts_by_symbol={OCC: contract_row(OCC, expiration="2026-10-09")},
        activities={"OPASN": [{"id": "act-1", "activity_type": "OPASN",
                               "symbol": OCC, "qty": "-1",
                               "date": "2026-10-09"}]})
    adapter = make_adapter(client, instance_id="swing-paper", clean_room=True)
    service = LiveOrderService(
        account_id="acct-1", instance_id="swing-paper",
        snapshot_provider=None, transport=None,
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        event_handler=adapter.apply_lifecycle_event)
    reconcile = extract(
        ("_reconcile_alpaca_ownership", "_lane_enabled", "_truthy",
         "_merged_strategy_settings"),
        assigns=("_LANE_ENABLE_FLAGS",),
        namespace={"datetime": datetime_module, "instance_id": "swing-paper",
                   "_live_order_dependency_lock": threading.Lock(),
                   "_live_order_dependency_state": {},
                   "_cached_strategies": WHEEL_DOC,
                   "_log": _ignore})["_reconcile_alpaca_ownership"]
    return client, adapter, service, reconcile


def test_an_assignment_ends_owned_by_the_lane_with_the_put_gone():
    """Ruling 1: after the poll and the reconcile the loop runs next, the put
    is gone from the option book and the 100 shares are this instance's
    (lineage), not quarantined as external."""
    client, adapter, service, reconcile = _real_book()
    assert OCC in adapter._option_positions
    # Alpaca settles the assignment: the put is gone, 100 APH shares arrive.
    client.positions = [_equity_row("APH", 100, 130.0)]
    cache, (sent, notify) = {}, _collect()
    done = _poller()(adapter, service, cache, now_utc=RTH, notify=notify,
                     min_interval_s=0)
    assert [a.id for a in done] == ["act-1"]
    assert OCC not in adapter._option_positions
    result = reconcile(adapter, service)
    assert result.healthy and result.owned == {"APH": Decimal("100")}
    assert result.external == {}
    assert adapter._positions == {"APH": 100.0}
    assert "APH" not in adapter._external_positions
    assert cache["_engine_wheel_assignments"][0]["underlying"] == "APH"
    assert [s["symbol"] for s in sent] == ["APH"]


def test_without_the_poller_the_same_shares_would_be_quarantined():
    """The control for the test above: the reconcile alone finds 100 APH
    shares with no lineage and quarantines them."""
    client, adapter, service, reconcile = _real_book()
    client.positions = [_equity_row("APH", 100, 130.0)]
    adapter.refresh_positions()
    result = reconcile(adapter, service)
    assert result.external == {"APH": Decimal("100")} and result.owned == {}
    assert "APH" in adapter._external_positions


def test_a_non_list_activities_answer_is_retried_without_moving_the_cursor():
    """The adapter raises on a 200 whose body is not a list (L2 ruling 4); the
    poller reads that as a failure, keeps the cursor and retries on the next
    poll, which the throttle allows after five minutes."""
    client, adapter, service, _reconcile = _real_book()
    answers = [{"message": "rate limited"}]
    real_get = client.get

    def get(path, data=None):
        if answers:
            client.get_calls.append((path, dict(data or {})))
            return answers.pop(0)
        return real_get(path, data)

    client.get = get
    cursor = {"after": "2026-10-01", "seen": ["old-1"]}
    cache = {"_engine_option_activity_cursor": dict(cursor)}
    lines = []
    ticks = [5000.0]
    poll = _poller()
    assert poll(adapter, service, cache, now_utc=RTH, monotonic=lambda: ticks[0],
                log=lambda m, c="white": lines.append((m, c))) == []
    assert cache == {"_engine_option_activity_cursor": cursor}
    assert not service.lifecycle_store.list_for_instance("swing-paper")
    assert any(c == "yellow" and "unreadable" in m for m, c in lines)
    ticks[0] += 301.0
    done = poll(adapter, service, cache, now_utc=RTH, notify=_ignore,
                monotonic=lambda: ticks[0])
    assert [a.id for a in done] == ["act-1"]
    assert all(params.get("after") == "2026-10-01"
               for _path, params in client.get_calls)
    assert cache["_engine_option_activity_cursor"] == {
        "after": "2026-10-08", "seen": ["old-1", "act-1"]}


# --- beyond the brief: exactly-once when a fill handler fails, a lost cache,
# --- the cursor never skipping a retried activity, an unknown contract type ---

def test_a_handler_failure_after_the_record_still_lists_and_announces_once():
    """The live fill handler raises when the account risk state is missing.
    The lifecycle row is already FILLED by then, so the brief's `applied`
    test would read "not recorded", retry, find the row terminal and never
    list or announce the assignment. The durable row decides instead."""
    def missing_risk_state(_fill):
        raise RuntimeError("confirmed fill cannot update missing risk state")

    poll = _poller()
    service, _fills = _service(handler=missing_risk_state)
    account = _Account([_assigned()], positions={OCC: _put()})
    cache, (sent, notify) = {}, _collect()
    lines = []
    done = poll(account, service, cache, now_utc=RTH, notify=notify,
                min_interval_s=0, log=lambda m, c="white": lines.append((m, c)))
    assert [a.id for a in done] == ["act-1"]
    assert [row["activity_id"] for row in cache["_engine_wheel_assignments"]] == [
        "act-1"]
    assert len(sent) == 1
    assert any(c == "red" and "missing risk state" in m for m, c in lines)
    assert not any("NOT recorded" in m for m, _c in lines)
    poll(account, service, {}, now_utc=RTH, notify=notify, min_interval_s=0)
    assert len(sent) == 1


def test_a_lost_cache_relists_the_assignment_without_a_second_notice():
    poll = _poller()
    service, fills = _service()
    account = _Account([_assigned()], positions={OCC: _put()})
    sent, notify = _collect()
    poll(account, service, {}, now_utc=RTH, notify=notify, min_interval_s=0)
    fresh = {}
    poll(account, service, fresh, now_utc=RTH, notify=notify, min_interval_s=0)
    assert [row["activity_id"] for row in fresh["_engine_wheel_assignments"]] == [
        "act-1"]
    assert len(fills) == 1 and len(sent) == 1
    poll(account, service, fresh, now_utc=RTH, notify=notify, min_interval_s=0)
    assert len(fresh["_engine_wheel_assignments"]) == 1


def test_the_cursor_never_moves_past_an_assignment_it_must_retry():
    poll = _poller()
    service, fills = _service()
    stuck = _assigned("act-old", symbol="APH1261009C00140000", day="2026-10-02")
    expiry = OptionActivityDTO("exp-1", "OPEXP", OCC, -1.0, "2026-10-09", 0.0)
    account = _Account([stuck, expiry], positions={OCC: _put()})
    cache = {}
    done = poll(account, service, cache, now_utc=RTH, notify=_ignore,
                min_interval_s=0)
    assert [a.id for a in done] == ["exp-1"]
    assert cache["_engine_option_activity_cursor"] == {
        "after": "2026-10-01", "seen": ["exp-1"]}
    account.contracts["APH1261009C00140000"] = _put("APH1261009C00140000",
                                                    "call", 140.0)
    poll(account, service, cache, now_utc=RTH, notify=_ignore, min_interval_s=0)
    assert account.calls[-1][1] == "2026-10-01" and len(fills) == 1


@pytest.mark.parametrize("option_type", ["", None, "straddle"])
def test_an_unknown_option_type_is_never_guessed_to_be_a_call(option_type):
    poll = _poller()
    service, fills = _service()
    lines = []
    account = _Account([_assigned()],
                       contracts={OCC: SimpleNamespace(
                           underlying="APH", option_type=option_type,
                           strike=130.0)})
    assert poll(account, service, {}, now_utc=RTH, min_interval_s=0,
                log=lambda m, c="white": lines.append((m, c))) == []
    assert fills == []
    assert any(c == "red" and "NOT recorded" in m for m, c in lines)


# --- the one wheel_assignment notice (A-live pre-flight F12) -------------------

def test_the_notice_goes_through_plan_bs_sender_when_it_is_deployed(monkeypatch):
    calls = []
    package = types.ModuleType("swing_trader")
    package.__path__ = []
    module = types.ModuleType("swing_trader.notify")
    module.notify_wheel_assignment = (
        lambda instance_id, **fields: calls.append((instance_id, fields)))
    package.notify = module
    monkeypatch.setitem(sys.modules, "swing_trader", package)
    monkeypatch.setitem(sys.modules, "swing_trader.notify", module)
    import notifications

    monkeypatch.setattr(notifications, "notify", lambda **kw: pytest.fail(
        "the bare notification was sent beside plan B's"))
    poll = _poller()
    service, _fills = _service()
    poll(_Account([_assigned()], positions={OCC: _put()}), service, {},
         now_utc=RTH, min_interval_s=0)
    assert calls == [("instance-1", {"symbol": "APH", "qty": 100,
                                     "price": 130.0, "date": "2026-10-09"})]


def test_without_plan_b_the_notice_is_a_bare_wheel_assignment(monkeypatch):
    monkeypatch.setitem(sys.modules, "swing_trader", None)
    monkeypatch.delitem(sys.modules, "swing_trader.notify", raising=False)
    import notifications

    sent = []
    monkeypatch.setattr(notifications, "notify", lambda **kw: sent.append(kw))
    poll = _poller()
    service, _fills = _service()
    poll(_Account([_assigned()], positions={OCC: _put()}), service, {},
         now_utc=RTH, min_interval_s=0)
    (notice,) = sent
    assert notice["category"] == "wheel_assignment"
    assert notice["instance_id"] == "instance-1"
    assert "APH" in notice["title"]
    assert "100 shares @ $130.00 on 2026-10-09" in notice["body"]


# --- L5 review ruling: the poller fails safe ----------------------------------

class _FlakyStore:
    """The service's lifecycle store with reads that fail on demand."""

    def __init__(self, inner):
        self.inner = inner
        self.fail_next = 0

    def get(self, key):
        if self.fail_next:
            self.fail_next -= 1
            raise ConnectionError("lifecycle store unreachable")
        return self.inner.get(key)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_a_lost_cursor_and_one_failed_store_read_announce_exactly_once():
    """A failed read is UNKNOWN, never "not filled": read as not filled, the
    replay would take itself for the poll that filled the row and announce
    the assignment a second time."""
    poll = _poller()
    service, fills = _service()
    flaky = service.lifecycle_store = _FlakyStore(service.lifecycle_store)
    account = _Account([_assigned()], positions={OCC: _put()})
    sent, notify = _collect()
    poll(account, service, {}, now_utc=RTH, notify=notify, min_interval_s=0)
    assert len(sent) == 1
    fresh, lines = {}, []
    flaky.fail_next = 1
    assert poll(account, service, fresh, now_utc=RTH, notify=notify,
                min_interval_s=0,
                log=lambda m, c="white": lines.append((m, c))) == []
    assert "_engine_option_activity_cursor" not in fresh
    assert any(c == "red" and "NOT recorded" in m and "unreadable" in m
               for m, c in lines)
    done = poll(account, service, fresh, now_utc=RTH, notify=notify,
                min_interval_s=0)
    assert [a.id for a in done] == ["act-1"]
    assert len(sent) == 1 and len(fills) == 1
    assert [row["activity_id"] for row in fresh["_engine_wheel_assignments"]] == [
        "act-1"]


def test_a_failed_read_after_the_record_announces_once_on_the_retry():
    """The poll that recorded the assignment could not confirm the row: it
    holds the activity and owes the notice, which the retry that finds the
    row FILLED sends, once."""
    poll = _poller()
    service, fills = _service()
    flaky = service.lifecycle_store = _FlakyStore(service.lifecycle_store)
    real_record = service.record_external_fill

    def record_then_lose_the_store(*args, **kwargs):
        out = real_record(*args, **kwargs)
        flaky.fail_next = 1
        return out

    service.record_external_fill = record_then_lose_the_store
    account = _Account([_assigned()], positions={OCC: _put()})
    cache, (sent, notify) = {}, _collect()
    assert poll(account, service, cache, now_utc=RTH, notify=notify,
                min_interval_s=0) == []
    assert sent == [] and len(fills) == 1
    assert "_engine_option_activity_cursor" not in cache
    service.record_external_fill = real_record
    done = poll(account, service, cache, now_utc=RTH, notify=notify,
                min_interval_s=0)
    assert [a.id for a in done] == ["act-1"]
    assert sent == [{"instance_id": "instance-1", "symbol": "APH", "qty": 100,
                     "price": 130.0, "date": "2026-10-09"}]
    poll(account, service, cache, now_utc=RTH, notify=notify, min_interval_s=0)
    poll(account, service, {}, now_utc=RTH, notify=notify, min_interval_s=0)
    assert len(sent) == 1 and len(fills) == 1


@pytest.mark.parametrize("symbol,side,cash", [
    (OCC, OrderSide.BUY, Decimal("-13000")),
    (CALL, OrderSide.SELL, Decimal("14000")),
])
def test_an_assignment_whose_contract_is_gone_is_booked_from_its_symbol(
        symbol, side, cash):
    """After a restart that follows expiry the contract is neither held nor
    listed by Alpaca: its accounting fields come from the OCC symbol, so the
    assignment is booked instead of held forever."""
    poll = _poller()
    service, fills = _service()
    account = _Account([_assigned(symbol=symbol)])
    cache, (sent, notify) = {}, _collect()
    lines = []
    done = poll(account, service, cache, now_utc=RTH, notify=notify,
                min_interval_s=0, log=lambda m, c="white": lines.append((m, c)))
    assert [a.id for a in done] == ["act-1"]
    (fill,) = fills
    assert (fill.event.symbol, fill.event.side, fill.incremental_quantity,
            fill.cash_delta) == ("APH", side, Decimal("100"), cash)
    (listed,) = cache["_engine_wheel_assignments"]
    assert (listed["underlying"], listed["contract"], listed["side"]) == (
        "APH", symbol, side.value)
    assert [n["symbol"] for n in sent] == ["APH"]
    assert any(c == "yellow" and "OCC symbol" in m for m, c in lines)


@pytest.mark.parametrize("qty", [0.0, None, "", "nan", 0.4, "lots"])
def test_an_assignment_without_a_quantity_is_held_never_booked_as_one(qty):
    poll = _poller()
    service, fills = _service()
    account = _Account([_assigned(qty=qty)], positions={OCC: _put()})
    cache, (sent, notify) = {}, _collect()
    lines = []
    assert poll(account, service, cache, now_utc=RTH, notify=notify,
                min_interval_s=0,
                log=lambda m, c="white": lines.append((m, c))) == []
    assert fills == [] and sent == []
    assert "_engine_option_activity_cursor" not in cache
    assert not service.lifecycle_store.list_for_instance("instance-1")
    assert any(c == "red" and "NOT recorded" in m and "quantity" in m
               for m, c in lines)
