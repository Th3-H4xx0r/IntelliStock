"""D1: the drawdown ladder had no rung above "stop allowing new exposure".

`evaluate_drawdown` computes normal / soft / hard / kill and sets
`new_exposure_allowed`, and that was the whole of it: hard and kill did exactly
what soft did. Nothing logged the transition, nothing paged, and open BUY
orders placed a minute before a 45% drawdown was measured stayed working at the
broker — so the level that is supposed to stop the account could still ADD to
it on a fill.

Deliberately NOT here: auto-liquidation, and flipping runCommand. Both are
operator decisions; this rung makes the ladder observable and stops new
exposure from arriving through an order that was already in flight.
"""
import ast
import datetime
import os
import sys
from decimal import Decimal
from types import SimpleNamespace

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from live_risk_state import (  # noqa: E402
    RISK_LEVELS,
    apply_risk_level_transition,
    cancel_open_buy_orders,
    initialize_risk_state,
    risk_level_rank,
)

T0 = datetime.datetime(2026, 6, 3, 14, 0, tzinfo=datetime.timezone.utc)


def state(level="normal", equity="6000", high="10000", drawdown="0.40"):
    base = initialize_risk_state("alpaca-main", "acct", Decimal(high), T0)
    return type(base)(
        instance_id=base.instance_id, account_id=base.account_id,
        version=base.version, high_water_equity=Decimal(high),
        last_equity=Decimal(equity), observed_at=T0,
        drawdown=Decimal(drawdown), level=level,
        new_exposure_allowed=(level == "normal"))


def order(symbol, side, oid):
    return SimpleNamespace(broker_order_id=oid, client_order_id=f"c-{oid}",
                           symbol=symbol, side=side, qty=1.0, status="new")


class Adapter:
    """Only the two methods this rung is allowed to use. Anything else — a
    liquidation, a market sell — raises, which is the test for "does not
    auto-liquidate"."""

    def __init__(self, *orders, raises=False):
        self.orders = list(orders)
        self.cancelled = []
        self.raises = raises

    def list_open_orders_strict(self, limit=200):
        if self.raises:
            raise RuntimeError("orders endpoint unreachable")
        return list(self.orders)

    def cancel_order(self, broker_order_id):
        self.cancelled.append(broker_order_id)
        return True

    def __getattr__(self, name):
        raise AssertionError(f"the kill rung must not call {name}()")


def sink():
    lines, alerts = [], []

    def log(message, color="white"):
        lines.append((str(message), color))

    def alert(**kwargs):
        alerts.append(kwargs)

    return lines, alerts, log, alert


def reds(lines):
    return [m for m, c in lines if c == "red"]


def test_the_ladder_is_ordered():
    assert RISK_LEVELS == ("normal", "soft", "hard", "kill")
    assert risk_level_rank("normal") < risk_level_rank("soft") \
        < risk_level_rank("hard") < risk_level_rank("kill")
    assert risk_level_rank("nonsense") == 0


def test_no_transition_is_silent():
    lines, alerts, log, alert = sink()
    adapter = Adapter(order("TQQQ", "buy", "1"))
    assert apply_risk_level_transition(
        "soft", state("soft"), adapter=adapter, log=log, alert=alert) is False
    assert lines == [] and alerts == [] and adapter.cancelled == []


def test_normal_to_soft_is_red_and_paged():
    lines, alerts, log, alert = sink()
    assert apply_risk_level_transition(
        "normal", state("soft"), instance_id="alpaca-main",
        log=log, alert=alert) is True
    assert reds(lines), lines
    assert "normal" in reds(lines)[0] and "SOFT" in reds(lines)[0]
    assert len(alerts) == 1
    assert alerts[0]["instance_id"] == "alpaca-main"


def test_soft_to_hard_is_red_and_paged_and_cancels_nothing():
    lines, alerts, log, alert = sink()
    adapter = Adapter(order("TQQQ", "buy", "1"))
    assert apply_risk_level_transition(
        "soft", state("hard"), adapter=adapter, log=log, alert=alert) is True
    assert reds(lines) and len(alerts) == 1
    assert adapter.cancelled == [], "only the kill rung cancels"


def test_kill_cancels_the_working_buys_and_leaves_the_sells():
    """An open SELL at the kill level is a reduce-only exit — the one order you
    want to survive. An open BUY is new exposure arriving after the account has
    been told to stop taking any."""
    lines, alerts, log, alert = sink()
    adapter = Adapter(order("TQQQ", "buy", "1"), order("GLD", "sell", "2"),
                      order("SPY", "BUY", "3"))
    assert apply_risk_level_transition(
        "hard", state("kill", drawdown="0.46"), adapter=adapter,
        log=log, alert=alert) is True
    assert adapter.cancelled == ["1", "3"]
    assert reds(lines) and len(alerts) == 1


def test_the_kill_rung_never_liquidates_or_stops_the_container():
    """`Adapter.__getattr__` raises on anything but list/cancel. Auto-exiting
    a book at the bottom of a drawdown, or flipping runCommand, are operator
    decisions and stay operator decisions."""
    _l, _a, log, alert = sink()
    apply_risk_level_transition("hard", state("kill"), adapter=Adapter(),
                                log=log, alert=alert)


def test_an_unreachable_order_book_at_kill_is_reported_not_swallowed():
    lines, alerts, log, alert = sink()
    adapter = Adapter(raises=True)
    assert apply_risk_level_transition(
        "hard", state("kill"), adapter=adapter, log=log, alert=alert) is True
    assert len(reds(lines)) >= 2, lines
    assert any("unreachable" in m or "cancel" in m.lower() for m in reds(lines))


def test_kill_with_no_adapter_still_reports():
    lines, alerts, log, alert = sink()
    assert apply_risk_level_transition(
        "hard", state("kill"), adapter=None, log=log, alert=alert) is True
    assert reds(lines) and len(alerts) == 1


def test_a_recovery_is_logged_but_pages_nobody():
    lines, alerts, log, alert = sink()
    adapter = Adapter(order("TQQQ", "buy", "1"))
    assert apply_risk_level_transition(
        "kill", state("normal", drawdown="0.01"), adapter=adapter,
        log=log, alert=alert) is True
    assert lines and not reds(lines), lines
    assert alerts == []
    assert adapter.cancelled == []


def test_a_broken_alert_never_reaches_the_risk_path():
    def explode(**_kw):
        raise RuntimeError("discord outbox unreachable")

    lines, _a, log, _alert = sink()
    assert apply_risk_level_transition(
        "normal", state("kill"), log=log, alert=explode) is True
    assert reds(lines)


def test_cancel_open_buy_orders_counts_what_it_cancelled():
    adapter = Adapter(order("TQQQ", "buy", "1"), order("GLD", "sell", "2"))
    assert cancel_open_buy_orders(adapter) == 1
    assert adapter.cancelled == ["1"]


def test_the_broker_runs_the_ladder_on_every_refresh():
    """Computing the level and never acting on it is what made hard and kill
    do exactly what soft did."""
    tree = ast.parse(open(os.path.join(_backend, "broker.py")).read())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "_refresh_live_account_risk_state")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "apply_risk_level_transition" in called
