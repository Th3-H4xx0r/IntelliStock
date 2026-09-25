"""swing-port Task 13: the wheel lane trades only on an account approved for
options level 1 (cash-secured puts); below that it is refused, in red, with
one alert."""
import ast
from types import SimpleNamespace

from swing_broker_harness import extract, source, tree

# --- EB pin (written first, green on the pre-change code at 75994c9) ---------
#
# The options-level read, like the option orders it guards, lives inside the
# one main-loop block whose test starts with `nexus_option_orders`. EB emits no
# option orders, so on doc 200 nothing inside that block runs (the block's
# guard is pinned False for doc 200 in test_swing_option_activities.py).

_WHEEL_ONLY_CALLS = ("_execute_option_intents", "_wheel_options_refusal")


def _enclosing_ifs(target):
    """Each module-level call of ``target`` with the `if` tests around it."""
    found = []

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef):
                continue
            here = stack + [child.test] if isinstance(child, ast.If) else stack
            if (isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                    and child.func.id == target):
                found.append(stack)
            walk(child, here)

    walk(tree(), [])
    return found


def test_the_wheel_calls_run_only_inside_the_option_order_block():
    sites = {name: _enclosing_ifs(name) for name in _WHEEL_ONLY_CALLS}
    assert sites["_execute_option_intents"], "the Task 10 call site moved"
    for name, stacks in sites.items():
        for stack in stacks:
            firsts = [test.values[0] for test in stack
                      if isinstance(test, ast.BoolOp)
                      and isinstance(test.op, ast.And)]
            assert any(isinstance(first, ast.Name)
                       and first.id == "nexus_option_orders"
                       for first in firsts), name


# --- the brief's tests --------------------------------------------------------

WHEEL = [{"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}}]


def _check():
    return extract(("_wheel_options_refusal", "_lane_enabled", "_truthy",
                    "_merged_strategy_settings"),
                   assigns=("_wheel_options_check", "_LANE_ENABLE_FLAGS"))[
        "_wheel_options_refusal"]


class _Account:
    def __init__(self, level=1, fail=False):
        self.level = level
        self.fail = fail
        self.reads = 0
        self._instance_id = "swing-paper"

    def get_account_options(self):
        self.reads += 1
        if self.fail:
            raise RuntimeError("account endpoint unreachable")
        return {"options_trading_level": self.level}


def _sink():
    lines, alerts = [], []
    return (lines, alerts, lambda m, c="white": lines.append((m, c)),
            lambda **kw: alerts.append(kw))


def test_no_wheel_lane_means_no_check():
    check = _check()
    account = _Account(level=0)
    assert check(account, [{"strategy": "strategy_eb",
                            "config": {"strategy_eb_enabled": True}}]) == ""
    assert account.reads == 0


def test_level_one_permits_the_lane_and_is_read_once():
    check = _check()
    account = _Account(level=1)
    assert check(account, WHEEL) == "" and check(account, WHEEL) == ""
    assert account.reads == 1


def test_level_zero_refuses_the_lane_in_red_with_one_alert():
    check = _check()
    account = _Account(level=0)
    lines, alerts, log, alert = _sink()
    refusal = check(account, WHEEL, log=log, alert=alert)
    assert "options_trading_level=0" in refusal
    assert check(account, WHEEL, log=log, alert=alert) == refusal
    assert account.reads == 1 and len(alerts) == 1
    assert alerts[0]["tag"] == "wheel-options-level"
    assert any(c == "red" and "REFUSED" in m for m, c in lines)


def test_an_unknown_level_refuses():
    check = _check()
    assert check(_Account(level=None), WHEEL, alert=lambda **kw: None) != ""


def test_an_unreadable_account_refuses_this_tick_and_asks_again():
    check = _check()
    account = _Account(fail=True)
    assert check(account, WHEEL) != ""
    account.fail = False
    assert check(account, WHEEL) == ""
    assert account.reads == 2


def test_the_option_call_site_passes_the_refusal():
    text = source()
    call = " ".join(text.split("_execute_option_intents(\n", 1)[1][:900].split())
    assert ("refused_reason=_wheel_options_refusal( live_adapter, "
            "_cached_strategies, log=_log)") in call


# --- controller ruling 2 (L5): first wheel tick, level < 1 refuses the lane's
# --- orders, a red line and ONE alert --------------------------------------------

def test_the_default_alert_is_one_strategy_error_for_the_process(monkeypatch):
    import live_alerts

    sent = []
    monkeypatch.setattr(live_alerts, "alert_strategy_error",
                        lambda **kw: sent.append(kw))
    check = _check()
    account = _Account(level=0)
    refusals = {check(account, WHEEL) for _tick in range(5)}
    assert refusals == {"options_trading_level=0 is below 1"}
    assert sent == [{"instance_id": "swing-paper", "tag": "wheel-options-level",
                     "message": "Wheel lane refused: options_trading_level=0 "
                                "is below 1"}]
    assert account.reads == 1


def test_a_failing_alert_still_refuses():
    def broken(**_kw):
        raise RuntimeError("discord outbox down")

    check = _check()
    assert check(_Account(level=0), WHEEL, alert=broken) != ""


def test_an_unreadable_level_is_not_remembered_as_a_verdict():
    check = _check()
    lines, alerts, log, alert = _sink()
    account = _Account(fail=True)
    assert "unreadable" in check(account, WHEEL, log=log, alert=alert)
    assert alerts == [] and any(c == "yellow" for _m, c in lines)
    account.fail, account.level = False, 0
    assert "options_trading_level=0" in check(account, WHEEL, log=log,
                                              alert=alert)
    assert len(alerts) == 1


def test_a_level_zero_refusal_places_no_option_order():
    """The verdict, handed to the executor exactly as the call site does,
    refuses every wheel order before any quote or submit (L4 Task 10)."""
    import datetime as datetime_module

    from swing_live_fixtures import OCC, RTH

    check = _check()
    refusal = check(_Account(level=0), WHEEL, alert=lambda **kw: None)
    ns = extract(("_execute_option_intents", "_build_option_intent",
                  "_refresh_option_quote"),
                 assigns=("_live_option_quotes", "_auto_close_alerts"),
                 namespace={"datetime": datetime_module,
                            "_wheel_alert": lambda *a, **k: True})

    class _NoService:
        account_id, instance_id = "acct-1", "swing-paper"

        def submit(self, intent):
            raise AssertionError("a refused lane reached the order service")

    class _NoQuotes:
        def get_option_snapshots(self, contracts):
            raise AssertionError("a refused lane read an options quote")

    lines = []
    order = {"contract": OCC, "position_intent": "sell_to_open",
             "underlying": "APH", "option_type": "put", "strike": 130.0,
             "expiry": "2026-10-09", "qty": 1, "order_type": "limit",
             "limit_price": 1.2, "tif": "day"}
    (result,) = ns["_execute_option_intents"](
        [order], order_service=_NoService(), adapter=_NoQuotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", refused_reason=refusal,
        log=lambda m, c="white": lines.append((m, c)))
    assert result["status"] == "refused"
    assert any(c == "red" and "options_trading_level=0" in m for m, c in lines)
