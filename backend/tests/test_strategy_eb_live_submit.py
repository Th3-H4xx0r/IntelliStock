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

_BROKER = os.path.join(_BACKEND, "broker.py")

EXIT_KEY = "_strategy_eb_exit_issued_session"


def _extract(*names):
    """AST-extract broker functions. broker.py argparses at module scope and
    SystemExits under pytest, so it cannot be imported."""
    tree = ast.parse(open(_BROKER).read())
    keep = set(names) | {"_truthy", "_merged_strategy_settings"}
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
           instance_id="alpaca-main", uncertain=True, log=log, alert=alert)
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
           instance_id="alpaca-main", uncertain=True, log=log, alert=alert)
    assert [m for m, c in lines if c == "red"]
    assert len(alerts) == 1


# --- E2: the timeout and the blanket except ---------------------------------

def test_a_failed_sell_is_red_paged_and_re_arms_the_exit():
    lines, alerts, log, alert = _sink()
    cache = eb_cache()
    report(symbol="TQQQ", decision=-1, detail="TimeoutError: >90s",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           log=log, alert=alert)
    assert [m for m, c in lines if c == "red"], lines
    assert len(alerts) == 1
    assert EXIT_KEY not in cache["strategy_eb"], (
        "the exit stayed marked issued, so it would not re-arm until the "
        "NEXT session — on a 3x fund")


def test_a_failed_buy_stays_yellow_and_pages_nobody():
    """A buy that did not reach the broker costs an opportunity, not a
    position. Paging on it is how a pager stops being read."""
    lines, alerts, log, alert = _sink()
    cache = eb_cache()
    report(symbol="TQQQ", decision=1, detail="TimeoutError: >90s",
           instance_id="alpaca-main", strategy_cache=cache, eb_core="TQQQ",
           log=log, alert=alert)
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
           log=log, alert=alert)
    assert cache["strategy_eb"][EXIT_KEY] == "2026-09-10"


def test_the_legacy_lane_spelling_is_cleared_too():
    lines, alerts, log, alert = _sink()
    cache = {"StrategyEb": {EXIT_KEY: "2026-09-10"}}
    report(symbol="TQQQ", decision=-1, detail="boom", instance_id="i",
           strategy_cache=cache, eb_core="tqqq", log=log, alert=alert)
    assert EXIT_KEY not in cache["StrategyEb"]


def test_a_broken_alert_never_reaches_the_trade_path():
    def explode(**_kw):
        raise RuntimeError("discord outbox unreachable")

    lines, _alerts, log, _alert = _sink()
    report(symbol="TQQQ", decision=-1, detail="boom", instance_id="i",
           strategy_cache=eb_cache(), eb_core="TQQQ", log=log, alert=explode)
    assert [m for m, c in lines if c == "red"]


def test_a_missing_cache_is_not_an_error():
    lines, _a, log, alert = _sink()
    report(symbol="TQQQ", decision=-1, detail="boom", instance_id="i",
           strategy_cache=None, eb_core="TQQQ", log=log, alert=alert)
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

def test_every_silent_lane_on_the_live_submit_path_reports():
    """A source assertion: the submit block is inline in the module-level main
    loop and cannot be AST-extracted. One definition plus the uncertain branch,
    the timeout handler and the blanket except."""
    source = open(_BROKER).read()
    assert source.count("_report_live_submit_failure(") >= 4, (
        "one of the uncertain / timeout / exception lanes is still silent")
    block = source.split("execute_signal hard-timeout", 1)[1][:1200]
    assert "_report_live_submit_failure(" in block
    uncertain = source.split("_submission.accepted", 1)[1][:1500]
    assert "uncertain" in uncertain
