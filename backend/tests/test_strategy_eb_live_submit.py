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
                         "_core_sell_may_be_working"}
    consts = {"_EB_EXIT_ISSUED_KEY", "_EB_LANE_NAMES"}
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
    raised = source.split("execute_signal raised", 1)[1][:1200]
    assert 'outcome="failed"' in raised and "adapter=" in raised


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
