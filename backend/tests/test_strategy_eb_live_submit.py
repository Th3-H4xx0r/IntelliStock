"""E1 + E2: a live submit that did not reach the broker must be loud.

Three lanes on broker.py's live equity submit path lost an order in silence:

E1  `OrderSubmission.uncertain` — allowed, NOT accepted, reference None. The
    transport raised, the lookup could not say whether the order exists, and
    `accepted` is False. The only branch that logged was `not allowed`, so an
    UNCERTAIN exit produced no line at all.
E2  The 90s watchdog timeout and the blanket `except Exception` were yellow,
    with no alert and no retry. On a SELL that is a leveraged position the
    strategy believes it has exited: the wrapper had already stamped
    `_strategy_eb_exit_issued_session`, so it would not re-arm until the next
    session.
"""
import ast
import os
import sys

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from types import SimpleNamespace  # noqa: E402

_BROKER = os.path.join(_BACKEND, "broker.py")

EXIT_KEY = "_strategy_eb_exit_issued_session"


def _extract(*names):
    """AST-extract broker functions. broker.py argparses at module scope and
    SystemExits under pytest, so it cannot be imported."""
    tree = ast.parse(open(_BROKER).read())
    keep = set(names) | {"_truthy", "_merged_strategy_settings",
                         "_strategy_eb_merged_config",
                         "_core_sell_may_be_working",
                         "_eb_buy_may_be_working",
                         "_eb_order_may_be_working",
                         "_eb_sell_leg_may_be_working"}
    consts = {"_EB_EXIT_ISSUED_KEY", "_EB_LANE_NAMES",
              "_EB_SWEEP_ISSUED_KEY", "_EB_REBALANCE_KEY",
              "_EB_SELL_REARMS_KEY", "_EB_SELL_REARM_CAP"}
    wanted = [n for n in tree.body
              if (isinstance(n, ast.FunctionDef) and n.name in keep)
              or (isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id in consts
                          for t in n.targets))]
    found = {n.name for n in wanted if isinstance(n, ast.FunctionDef)}
    assert set(names) <= found, f"missing from broker.py: {set(names) - found}"
    ns = {"_log": lambda *a, **k: None}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), _BROKER, "exec"), ns)
    return ns


def _sink():
    lines, alerts = [], []

    def log(message, color="white"):
        lines.append((str(message), color))

    def alert(**kwargs):
        alerts.append(kwargs)

    return lines, alerts, log, alert


def report(**kw):
    ns = _extract("_report_live_submit_failure")
    return ns["_report_live_submit_failure"](**kw)


def eb_cache(session="2026-09-10"):
    return {"strategy_eb": {EXIT_KEY: session, "_strategy_eb_last": {}}}


# --- E1: the uncertain submission -------------------------------------------

def test_an_uncertain_submission_is_red_and_paged():
    lines, alerts, log, alert = _sink()
    report(symbol="TQQQ", decision=-1, detail="broker.order.outcome_unknown",
           instance_id="alpaca-main", outcome="uncertain", log=log, alert=alert)
    red = [m for m, c in lines if c == "red"]
    assert red, f"the silent branch: {lines}"
    assert "TQQQ" in red[0]
    assert len(alerts) == 1
    assert alerts[0]["instance_id"] == "alpaca-main"
    assert "TQQQ" in alerts[0]["message"]


def test_an_uncertain_buy_is_loud_too():
    """Uncertainty is about whether an order EXISTS. On a buy that is an
    unbudgeted position, not a missing one."""
    lines, alerts, log, alert = _sink()
    report(symbol="TQQQ", decision=1, detail="transport outcome unknown",
           instance_id="alpaca-main", outcome="uncertain", log=log, alert=alert)
    assert [m for m, c in lines if c == "red"]
    assert len(alerts) == 1


# --- E2/C2: WHICH failure may re-arm the exit -------------------------------
#
# The re-arm was inverted. `_strategy_eb_exit_issued_session` means "an exit is
# in flight"; clearing it lets the wrapper re-send on the next tick. A SELL
# intent's identity carries `decision_minute` and `quantity` (live_orders/
# types.py:268-272), so a re-send on a later tick mints a FRESH idempotency key
# — the service will not dedupe it against the order that may already be
# working. Which failure clears the key is therefore a duplicate-order
# decision, not a logging one.
#
#   gate-blocked  -> a DEFINITE non-submission. Nothing reached the broker, so
#                    the exit is not in flight and the key MUST be cleared.
#   uncertain     -> the transport raised and the lookup could not say whether
#   timed out        the order exists; it may be working right now. Clearing
#   raised           blindly is how one exit becomes two. Re-arm ONLY when the
#                    working-order book shows no SELL for the core.

WORKING = "working_sell_present"


def orders(*specs):
    return SimpleNamespace(list_open_orders_strict=lambda limit=200: [
        SimpleNamespace(broker_order_id=f"b{i}", client_order_id=f"c{i}",
                        symbol=sym, side=side, qty=1.0, status="new")
        for i, (sym, side) in enumerate(specs)])


def unreachable():
    def boom(limit=200):
        raise RuntimeError("orders endpoint unreachable")
    return SimpleNamespace(list_open_orders_strict=boom)


def test_a_gate_block_is_a_definite_non_submission_and_re_arms():
    """Nothing reached the broker. Leaving the key stamped suppressed the exit
    for the rest of the session over an order that was never sent."""
    lines, alerts, log, alert = _sink()
    cache = eb_cache()
    report(symbol="TQQQ", decision=-1, detail="gate:market_closed",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="blocked", adapter=orders(("TQQQ", "sell")),
           log=log, alert=alert)
    assert EXIT_KEY not in cache["strategy_eb"], (
        "a definite non-submission left the exit marked in flight")
    assert [m for m, c in lines if c == "red"], lines
    assert len(alerts) == 1


def test_a_gate_block_does_not_consult_the_order_book():
    """It cannot matter what is working: THIS order was not sent."""
    lines, _a, log, alert = _sink()
    cache = eb_cache()
    report(symbol="TQQQ", decision=-1, detail="gate:market_closed",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="blocked", adapter=unreachable(), log=log, alert=alert)
    assert EXIT_KEY not in cache["strategy_eb"]


def test_an_uncertain_sell_with_a_working_sell_does_NOT_re_arm():
    """THE duplicate-order case. The order is at the broker; re-sending mints a
    new idempotency key and sells the position twice."""
    lines, alerts, log, alert = _sink()
    cache = eb_cache()
    report(symbol="TQQQ", decision=-1, detail="broker.order.outcome_unknown",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="uncertain", adapter=orders(("TQQQ", "sell")),
           log=log, alert=alert)
    assert cache["strategy_eb"][EXIT_KEY] == "2026-09-10", (
        "cleared the key while a SELL for the core was working at the broker")
    assert [m for m, c in lines if c == "red"], lines
    assert any(WORKING in m or "working" in m.lower() for m, _c in lines)


def test_an_uncertain_sell_with_an_empty_book_re_arms():
    lines, alerts, log, alert = _sink()
    cache = eb_cache()
    report(symbol="TQQQ", decision=-1, detail="broker.order.outcome_unknown",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="uncertain", adapter=orders(), log=log, alert=alert)
    assert EXIT_KEY not in cache["strategy_eb"]
    assert [m for m, c in lines if c == "red"]


def test_a_working_BUY_for_the_core_does_not_count_as_the_exit():
    lines, _a, log, alert = _sink()
    cache = eb_cache()
    report(symbol="TQQQ", decision=-1, detail="unknown", instance_id="i",
           strategy_cache=cache, eb_core="TQQQ", outcome="uncertain",
           adapter=orders(("TQQQ", "buy"), ("GLD", "sell")),
           log=log, alert=alert)
    assert EXIT_KEY not in cache["strategy_eb"]


def test_a_timeout_is_treated_as_unknown_not_as_a_failure_to_send():
    """The 90s watchdog abandons the future to the background — the submit may
    complete a second later."""
    lines, _a, log, alert = _sink()
    cache = eb_cache()
    report(symbol="TQQQ", decision=-1, detail="hard-timeout (>90s)",
           instance_id="i", strategy_cache=cache, eb_core="TQQQ",
           outcome="uncertain", adapter=orders(("tqqq", "sell")),
           log=log, alert=alert)
    assert cache["strategy_eb"][EXIT_KEY] == "2026-09-10", "case-folding"


def test_an_unreadable_order_book_does_not_re_arm():
    """`list_open_orders_strict` raises rather than reporting an unreachable
    endpoint as a clear book, and absence cannot be proven from a raise. Fail
    closed on the DUPLICATE, and say so: the operator has to know the exit is
    suppressed."""
    lines, alerts, log, alert = _sink()
    cache = eb_cache()
    report(symbol="TQQQ", decision=-1, detail="unknown", instance_id="i",
           strategy_cache=cache, eb_core="TQQQ", outcome="uncertain",
           adapter=unreachable(), log=log, alert=alert)
    assert cache["strategy_eb"][EXIT_KEY] == "2026-09-10"
    reds = [m for m, c in lines if c == "red"]
    assert reds and any("unreachable" in m or "could not" in m.lower()
                        for m in reds), reds


def test_no_adapter_means_absence_cannot_be_proven_either():
    lines, _a, log, alert = _sink()
    cache = eb_cache()
    report(symbol="TQQQ", decision=-1, detail="unknown", instance_id="i",
           strategy_cache=cache, eb_core="TQQQ", outcome="uncertain",
           adapter=None, log=log, alert=alert)
    assert cache["strategy_eb"][EXIT_KEY] == "2026-09-10"


def test_a_failed_buy_stays_yellow_and_pages_nobody():
    """A buy that did not reach the broker costs an opportunity, not a
    position. Paging on it is how a pager stops being read."""
    lines, alerts, log, alert = _sink()
    cache = eb_cache()
    report(symbol="TQQQ", decision=1, detail="TimeoutError: >90s",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="failed", adapter=orders(), log=log, alert=alert)
    assert lines and all(c == "yellow" for _, c in lines), lines
    assert alerts == []
    assert cache["strategy_eb"][EXIT_KEY] == "2026-09-10"


def test_a_failed_sell_of_another_symbol_leaves_the_exit_alone():
    """The key is per-LANE, and clearing it for an unrelated sell would
    re-send an exit that really is in flight."""
    lines, alerts, log, alert = _sink()
    cache = eb_cache()
    report(symbol="GLD", decision=-1, detail="TimeoutError: >90s",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="blocked", adapter=orders(), log=log, alert=alert)
    assert cache["strategy_eb"][EXIT_KEY] == "2026-09-10"


def test_the_legacy_lane_spelling_is_cleared_too():
    lines, alerts, log, alert = _sink()
    cache = {"StrategyEb": {EXIT_KEY: "2026-09-10"}}
    report(symbol="TQQQ", decision=-1, detail="boom", instance_id="i",
           strategy_cache=cache, eb_core="tqqq", outcome="blocked",
           log=log, alert=alert)
    assert EXIT_KEY not in cache["StrategyEb"]


def test_a_broken_alert_never_reaches_the_trade_path():
    def explode(**_kw):
        raise RuntimeError("discord outbox unreachable")

    lines, _alerts, log, _alert = _sink()
    report(symbol="TQQQ", decision=-1, detail="boom", instance_id="i",
           strategy_cache=eb_cache(), eb_core="TQQQ", outcome="blocked",
           log=log, alert=explode)
    assert [m for m, c in lines if c == "red"]


def test_a_missing_cache_is_not_an_error():
    lines, _a, log, alert = _sink()
    report(symbol="TQQQ", decision=-1, detail="boom", instance_id="i",
           strategy_cache=None, eb_core="TQQQ", outcome="blocked",
           log=log, alert=alert)
    assert [m for m, c in lines if c == "red"]


# --- the EB core symbol the re-arm is keyed on ------------------------------

def core(specs):
    ns = _extract("_strategy_eb_core_symbol")
    return ns["_strategy_eb_core_symbol"](specs)


def test_the_core_symbol_comes_from_the_enabled_eb_lane():
    assert core([{"strategy": "strategy_eb",
                  "config": {"strategy_eb_enabled": True}}]) == "TQQQ"
    assert core([{"strategy": "strategy_eb",
                  "config": {"strategy_eb_enabled": True,
                             "core_symbol": "QLD"}}]) == "QLD"


def test_a_core_set_in_conditions_is_seen():
    """The dispatcher merges conditions UNDER config; reading config alone is
    how the risk envelope went blind to a value set in conditions."""
    assert core([{"strategy": "strategy_eb",
                  "conditions": {"strategy_eb_enabled": True,
                                 "core_symbol": "QLD"},
                  "config": {}}]) == "QLD"


def test_a_disabled_or_absent_eb_has_no_core():
    assert core([{"strategy": "strategy_eb", "config": {}}]) == ""
    assert core([{"strategy": "graph_nexus_analysis", "config": {}}]) == ""
    for junk in (None, [], [None], ["strategy_eb"]):
        assert core(junk) == "", junk


# --- the three call sites ---------------------------------------------------

def test_the_re_arm_policy_reaches_all_three_call_sites():
    """A source assertion: the submit block is inline in the module-level main
    loop. Each of the three lanes must declare which kind of failure it is, or
    it inherits the wrong duplicate-order decision."""
    source = open(_BROKER).read()
    block = source.split("_submission.accepted", 1)[1][:4000]
    assert 'outcome="blocked"' in block, "the gate-blocked lane does not re-arm"
    assert 'outcome="uncertain"' in block
    timeout = source.split("execute_signal hard-timeout", 1)[1][:1500]
    assert 'outcome="uncertain"' in timeout and "adapter=" in timeout, (
        "the 90s watchdog abandons the future to the background, so its "
        "outcome is UNKNOWN and its re-arm must consult the order book")
    raised = source.split("execute_signal raised", 1)[1][:1500]
    # Every exception is an unknown failure EXCEPT an RTH deferral, which
    # raises before the order exists and is routed as "deferred".
    assert '"failed"' in raised and '"deferred"' in raised and "adapter=" in raised


def test_every_silent_lane_on_the_live_submit_path_reports():
    """A source assertion: the submit block is inline in the module-level main
    loop and cannot be AST-extracted. One definition plus the uncertain branch,
    the timeout handler and the blanket except."""
    source = open(_BROKER).read()
    # one definition + the gate-blocked, uncertain, timeout and except lanes
    assert source.count("_report_live_submit_failure(") >= 5, (
        "one of the blocked / uncertain / timeout / exception lanes is still "
        "silent")
    block = source.split("execute_signal hard-timeout", 1)[1][:1500]
    assert "_report_live_submit_failure(" in block
    uncertain = source.split("_submission.accepted", 1)[1][:4000]
    assert "uncertain" in uncertain


# --- E3: a refused BUY must not burn the session's only attempt -------------

SWEEP_KEY = "_strategy_eb_sweep_session"
REBAL_KEY = "_eb_last_rebalance_session"


def eb_buy_cache(session="2026-09-15"):
    return {"strategy_eb": {SWEEP_KEY: session, REBAL_KEY: session,
                            EXIT_KEY: session}}


def test_a_definite_buy_block_re_arms_the_sweep_and_rebalance_latches():
    """2026-09-16, alpaca-main (REAL MONEY): EB swept $6,041 of idle cash at
    01:00 PT — 04:00 ET, where a regular-hours order has no fresh quote — and
    the gate refused all three legs on quote.stale. The latches are stamped on
    EMISSION, so the session's only attempt was spent on orders that never
    reached the broker, and the account sat in 100% cash. The next day repeated
    it. Nothing was sent, so re-arming cannot duplicate anything."""
    lines, alerts, log, alert = _sink()
    cache = eb_buy_cache()
    report(symbol="GLD", decision=1, detail="gate:quote.stale",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="blocked", adapter=orders(), log=log, alert=alert)
    assert SWEEP_KEY not in cache["strategy_eb"]
    assert REBAL_KEY not in cache["strategy_eb"]
    assert alerts == [], "a refused buy is an opportunity cost, not a page"
    assert any("RE-ARMED" in m for m, _c in lines), lines


def test_a_refused_buy_leaves_the_exit_latch_alone():
    """The exit latch means a position the strategy believes it has left; a
    refused BUY says nothing about it."""
    _l, _a, log, alert = _sink()
    cache = eb_buy_cache()
    report(symbol="GLD", decision=1, detail="gate:quote.stale",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="blocked", adapter=orders(), log=log, alert=alert)
    assert cache["strategy_eb"][EXIT_KEY] == "2026-09-15"


def test_a_working_buy_blocks_the_re_arm():
    """THE duplicate-buy case: one plan has several legs, so a sibling may have
    been accepted while this leg was refused. Re-planning then buys twice."""
    lines, _a, log, alert = _sink()
    cache = eb_buy_cache()
    report(symbol="GLD", decision=1, detail="gate:quote.stale",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="blocked", adapter=orders(("XLE", "buy")), log=log, alert=alert)
    assert cache["strategy_eb"][SWEEP_KEY] == "2026-09-15"
    assert any("NOT re-armed" in m for m, _c in lines), lines


def test_an_unreadable_order_book_blocks_the_buy_re_arm():
    """Absence of a working BUY cannot be proven from a dead endpoint, so the
    latch stays stamped — stuck cash beats a double buy."""
    _l, _a, log, alert = _sink()
    cache = eb_buy_cache()
    report(symbol="GLD", decision=1, detail="gate:quote.stale",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="blocked", adapter=unreachable(), log=log, alert=alert)
    assert cache["strategy_eb"][SWEEP_KEY] == "2026-09-15"


def test_an_uncertain_buy_does_not_re_arm_the_latches():
    """Uncertain means the order may be working this second."""
    _l, _a, log, alert = _sink()
    cache = eb_buy_cache()
    report(symbol="GLD", decision=1, detail="ReadTimeout",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="uncertain", adapter=orders(), log=log, alert=alert)
    assert cache["strategy_eb"][SWEEP_KEY] == "2026-09-15"


def test_a_refused_sell_does_not_touch_the_buy_latches():
    _l, _a, log, alert = _sink()
    cache = eb_buy_cache()
    report(symbol="TQQQ", decision=-1, detail="gate:market_closed",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="blocked", adapter=orders(), log=log, alert=alert)
    assert cache["strategy_eb"][SWEEP_KEY] == "2026-09-15"
    assert EXIT_KEY not in cache["strategy_eb"]


def test_the_gate_blocked_call_site_hands_over_the_order_book():
    """The BUY re-arm must be able to rule out a working sibling leg, and it
    fails closed without an adapter. Until 2026-09-16 the gate-blocked lane
    was the one call site that passed no `adapter=`, because the SELL re-arm
    deliberately does not need it — which made the BUY re-arm inert on the
    exact path it exists for."""
    source = open(_BROKER).read()
    block = source.split('outcome="blocked"', 1)[0]
    call = block.rsplit("_report_live_submit_failure(", 1)[1]
    assert "adapter=" in call, (
        "the gate-blocked call site passes no adapter, so "
        "_eb_buy_may_be_working cannot rule out a working BUY and the sweep "
        "latch is never re-armed")


# --- E4: an RTH deferral must be retried, as the guard promises -------------

def test_a_deferred_rotation_sell_re_arms_the_rebalance_latch():
    """2026-09-17, alpaca-main (REAL MONEY): the weekly rebalance fired at
    04:40 ET and rotated toward the risk-off book, but all three sells were
    fractional, and fractional shares only trade in regular hours. The guard
    deferred them — "it will be retried when regular hours resume" — yet EB had
    already stamped its once-per-session rebalance latch, so no later tick
    re-planned, not even at the open. The book sat in the wrong regime for a
    week. A deferral means the order was never created, so re-arming cannot
    duplicate it."""
    lines, alerts, log, alert = _sink()
    cache = eb_buy_cache()
    report(symbol="GDX", decision=-1,
           detail="execute_signal raised ValueError: order deferred: GDX sell of "
                  "4.000214 floors to 4.0 outside RTH, which would leave 0.000214 at risk",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="deferred", adapter=orders(), log=log, alert=alert)
    assert REBAL_KEY not in cache["strategy_eb"], "the rebalance stays suppressed"
    assert SWEEP_KEY not in cache["strategy_eb"]


def test_a_deferral_is_expected_outside_rth_and_pages_nobody():
    """The guard doing its job is not a failure. Paging every pre-market hour
    on it is how a pager stops being read."""
    lines, alerts, log, alert = _sink()
    report(symbol="GDX", decision=-1, detail="order deferred: ... outside RTH",
           instance_id="alpaca-main", strategy_cache=eb_buy_cache(),
           eb_core="TQQQ", outcome="deferred", adapter=orders(),
           log=log, alert=alert)
    assert alerts == []
    assert any("DEFERRED" in m for m, _c in lines), lines
    assert not any(c == "red" for _m, c in lines), lines


def test_a_deferred_leg_does_not_re_arm_while_an_order_is_working():
    """One plan has several legs; if any order is working, re-planning from
    unsettled positions could trade it twice."""
    _l, _a, log, alert = _sink()
    cache = eb_buy_cache()
    report(symbol="GDX", decision=-1, detail="order deferred: ... outside RTH",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="deferred", adapter=orders(("GLD", "sell")),
           log=log, alert=alert)
    assert cache["strategy_eb"][REBAL_KEY] == "2026-09-15"


def test_a_deferred_core_exit_re_arms_the_exit():
    """A deferred exit leaves the levered position held at risk; the guard
    promised the retry, so the exit latch must not suppress it."""
    _l, _a, log, alert = _sink()
    cache = eb_buy_cache()
    report(symbol="TQQQ", decision=-1, detail="order deferred: ... outside RTH",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome="deferred", adapter=orders(), log=log, alert=alert)
    assert EXIT_KEY not in cache["strategy_eb"]


def test_the_exception_lane_routes_rth_deferrals_as_deferred():
    """A source assertion: the exception lane is inline in the main loop. A
    deferral raises before the order is created, so it is a definite
    non-submission, not an unknown failure."""
    source = open(_BROKER).read()
    raised = source.split('f"execute_signal raised "', 1)[1][:1500]
    assert "order deferred" in raised and '"deferred"' in raised, (
        "RTH deferrals are still reported as unknown failures, so they page "
        "and never re-arm EB's latches")


# --- E5: a gate-refused rotation SELL must be retried, not dropped ----------
#
# 2026-09-22 sweep, ahead of the first live EB SELL ever (the 09-24 rebalance):
# a GLD/GDX/XLE trim refused by the gate at the open reported
# outcome="blocked", and for a non-core sell `_report_live_submit_failure`
# returned without clearing anything. `_eb_last_rebalance_session` stayed
# stamped, every later tick returned {} (and the next session's sweep only
# buys), so the leg sat untrimmed until the next cadence day, a week away.
# A real trigger was reproduced against the real gate: the 3-second position
# refresh rewrote the held mark between the intent and the gate read, and the
# gate refused on quote.timestamp_mismatch.

EB_UNIVERSE = ("QQQ", "TQQQ", "SPY", "BIL", "GLD", "GDX", "XLE")
TALLY_KEY = "_strategy_eb_sell_rearms"


def trim_refused(cache, adapter, symbol="GDX", outcome="blocked",
                 universe=EB_UNIVERSE, detail="gate: quote.timestamp_mismatch"):
    lines, alerts, log, alert = _sink()
    report(symbol=symbol, decision=-1, detail=detail,
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           outcome=outcome, adapter=adapter, log=log, alert=alert,
           eb_universe=universe)
    return lines, alerts


def test_a_gate_refused_rotation_sell_re_arms_the_rebalance_latch():
    cache = eb_buy_cache()
    lines, alerts = trim_refused(cache, orders())
    assert REBAL_KEY not in cache["strategy_eb"], "the leg waits a week"
    assert SWEEP_KEY not in cache["strategy_eb"]
    assert cache["strategy_eb"][EXIT_KEY] == "2026-09-15", (
        "a book-leg refusal says nothing about the core exit")
    assert any("RE-ARMED" in m for m, _c in lines), lines
    assert alerts, "a refused SELL still pages"


def test_a_refused_rotation_sell_does_not_re_arm_while_that_symbol_has_a_working_sell():
    """A SELL intent's key carries the decision minute, so a re-plan would NOT
    be deduped against an order for that symbol already working."""
    cache = eb_buy_cache()
    lines, _a = trim_refused(cache, orders(("GDX", "sell")))
    assert cache["strategy_eb"][REBAL_KEY] == "2026-09-15"
    assert any("NOT re-armed" in m for m, _c in lines), lines


def test_a_sibling_legs_working_sell_does_not_block_the_re_arm():
    """The sibling legs of the same plan were submitted a second earlier, so
    they are exactly what is working when this leg is refused. They are
    protected on the re-plan by the band (once filled they are at target) and
    by the broker's already-ordered-today guard; demanding an empty book here
    would make the re-arm inert on the path it exists for."""
    cache = eb_buy_cache()
    trim_refused(cache, orders(("GLD", "sell"), ("XLE", "sell")),
                 detail="gate: positions.stale")
    assert REBAL_KEY not in cache["strategy_eb"]


def test_a_working_buy_blocks_the_sell_re_arm():
    """Buys size off cash, and an unfilled buy has not spent it yet: the
    re-plan would size the same buy again."""
    cache = eb_buy_cache()
    lines, _a = trim_refused(cache, orders(("TQQQ", "buy")))
    assert cache["strategy_eb"][REBAL_KEY] == "2026-09-15"
    assert any("NOT re-armed" in m for m, _c in lines), lines


def test_an_unreadable_order_book_blocks_the_sell_re_arm():
    cache = eb_buy_cache()
    trim_refused(cache, unreachable(), detail="gate: positions.stale")
    assert cache["strategy_eb"][REBAL_KEY] == "2026-09-15"


def test_no_adapter_blocks_the_sell_re_arm():
    cache = eb_buy_cache()
    trim_refused(cache, None, detail="gate: positions.stale")
    assert cache["strategy_eb"][REBAL_KEY] == "2026-09-15"


def test_an_uncertain_rotation_sell_does_not_re_arm_the_plan():
    """Uncertain means the order may exist at the broker this second."""
    cache = eb_buy_cache()
    trim_refused(cache, orders(), outcome="uncertain", detail="ReadTimeout")
    assert cache["strategy_eb"][REBAL_KEY] == "2026-09-15"


def test_a_refused_sell_of_a_symbol_eb_does_not_trade_leaves_the_plan_alone():
    """A broker risk exit on some other holding is not EB's leg to re-plan."""
    cache = eb_buy_cache()
    trim_refused(cache, orders(), symbol="AAPL")
    assert cache["strategy_eb"][REBAL_KEY] == "2026-09-15"


def test_without_the_eb_universe_nothing_is_re_armed():
    """Fail closed: a call site that cannot say what EB trades gets the old
    behaviour, never a re-plan for an unknown symbol."""
    cache = eb_buy_cache()
    trim_refused(cache, orders(), universe=None)
    assert cache["strategy_eb"][REBAL_KEY] == "2026-09-15"


def test_the_re_arm_is_capped_per_leg_per_session():
    """A PERMANENT refusal (not armed, source denied, a degraded DB) would
    otherwise re-plan and page on every tick of the session."""
    cache = eb_buy_cache()
    for attempt in range(1, 7):
        cache["strategy_eb"][REBAL_KEY] = "2026-09-23"   # the re-plan stamps
        trim_refused(cache, orders())
        assert REBAL_KEY not in cache["strategy_eb"], attempt
    cache["strategy_eb"][REBAL_KEY] = "2026-09-23"
    lines, _a = trim_refused(cache, orders())
    assert cache["strategy_eb"][REBAL_KEY] == "2026-09-23", "7th re-arm"
    assert any("refused 6 times" in m for m, _c in lines), lines
    # other legs keep their own budget, and next week's session starts fresh
    trim_refused(cache, orders(), symbol="XLE")
    assert REBAL_KEY not in cache["strategy_eb"]
    cache["strategy_eb"][REBAL_KEY] = "2026-09-30"
    trim_refused(cache, orders())
    assert REBAL_KEY not in cache["strategy_eb"]
    assert cache["strategy_eb"][TALLY_KEY]["session"] == "2026-09-30"


def test_both_call_sites_that_can_report_blocked_pass_the_eb_universe():
    source = open(_BROKER).read()
    gate = source.split('outcome="blocked",', 1)[1][:400]
    assert "eb_universe=_strategy_eb_universe_symbols(" in gate
    raised = source.split('f"execute_signal raised "', 1)[1][:2500]
    assert "eb_universe=_strategy_eb_universe_symbols(" in raised


def test_an_intent_that_could_not_be_built_is_a_definite_non_submission():
    """Reproduced in the 2026-09-24 replay: a failed orders read during the
    pre-submit reconcile published an empty book, every opening trim raised
    `computed order quantity <= 0` inside `_build_strategy_stock_intent` —
    BEFORE the order existed — and the exception lane reported it as an
    unknown "failed", which re-arms nothing. The rotation waited a week."""
    source = open(_BROKER).read()
    raised = source.split('f"execute_signal raised "', 1)[1][:2500]
    assert '"computed order quantity <= 0"' in raised and '"blocked"' in raised, (
        "an intent that failed to build is still reported as an unknown "
        "failure, so a refused rotation leg is never re-planned")
    builder = source.split("def _build_strategy_stock_intent(", 1)[1][:3000]
    assert 'raise ValueError("computed order quantity <= 0")' in builder
