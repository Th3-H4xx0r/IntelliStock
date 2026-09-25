"""A backtest that never asks for a bracket or a next-open fill is unchanged.

Spec 2026-09-24-swing-trader-port-design.md, sections 6 and 11: every new
simulator branch triggers only for an order that carries `bracket`,
`whole_shares` or `fill_at_next_open`. This file pins that promise against the
code as it stood BEFORE any of that work: a deterministic non-bracket run --
two symbols, a tiered cost model, a cash-clamped buy, a sub-$1 reject, partial
and full sells, orders still pending at the end -- serialised byte for byte.

The golden was written by the pre-change code (plan Task 1). Never regenerate
it to make a later task pass: a diff here IS the regression.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from portfolio_emulator import PortfolioEmulator  # noqa: E402
from simulated_execution import (  # noqa: E402
    LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL,
    NextEventExecutionSimulator,
    SimulationPriceEvent,
    tiered_cost_model,
)

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "swing_port_non_bracket_golden.json"
DAY = timedelta(days=1)
#: 05:00 PT on a weekday, as the broker's naive-UTC clock carries it.
T0 = datetime(2026, 3, 2, 13, 0)
CLOSES = {
    "SPY": [500.0, 505.0, 498.0, 510.0, 507.0, 512.0, 509.0, 515.0],
    "ABC": [20.0, 19.5, 21.0, 22.5, 18.0, 18.5, 19.25, 20.0],
}


def _close_event(symbol, day):
    """Day `day`'s close, visible from 21:00 UTC that day."""
    close_at = datetime(2026, 3, 2, 21, 0, tzinfo=timezone.utc) + day * DAY
    return SimulationPriceEvent(
        symbol=symbol,
        price=CLOSES[symbol][day],
        available_at=close_at,
        bar_timestamp=close_at - timedelta(hours=16),
    )


def _run(submit):
    """Drive one emulator through eight daily ticks. `submit(emu, day, now)`
    places that tick's orders, so a later test can replay the same run through
    a different submission path."""
    simulator = NextEventExecutionSimulator(
        tiered_cost_model("etf-liquid", LIQUIDITY_ADJUSTED_EQUITY_COST_MODEL))
    emulator = PortfolioEmulator(
        10_000.0, execution_simulator=simulator, execution_delay=DAY)
    steps = []
    for day in range(len(CLOSES["SPY"])):
        now = T0 + day * DAY
        visible = {}
        if day:
            events = {s: _close_event(s, day - 1) for s in CLOSES}
            emulator.process_price_events(events)
            visible = {s: CLOSES[s][day - 1] for s in CLOSES}
        emulator.save_portfolio_snapshot(visible, timestamp=now)
        submit(emulator, day, now)
        steps.append({
            "day": day,
            "cash": emulator.get_cash(),
            "buying_power": emulator.get_buying_power(),
            "positions": emulator.get_positions(),
            "pending": list(emulator.pending_execution_symbols()),
            "pending_sell_proceeds": emulator.pending_sell_proceeds(),
        })
    return {
        "steps": steps,
        "trades": emulator.get_trade_history(),
        "summary": emulator.get_execution_summary(),
        "portfolio": emulator.get_portfolio_history(),
    }


def _submit_plain(emulator, day, now):
    if day == 0:
        emulator.execute_signal("SPY", 1, 500.0, timestamp=now,
                                cash_per_trade=4000.0, order_source="main_signal")
        # More than the account can fund: clamped to buying power.
        emulator.execute_signal("ABC", 1, 20.0, timestamp=now,
                                cash_per_trade=7000.0, order_source="main_signal")
    elif day == 2:
        # Under Alpaca's $1 minimum: refused and counted, never submitted.
        emulator.execute_signal("SPY", 1, 505.0, timestamp=now,
                                cash_per_trade=0.5, order_source="main_signal")
    elif day == 3:
        emulator.execute_signal("ABC", -1, 22.5, timestamp=now,
                                sell_fraction=0.5, order_source="main_signal")
    elif day == 4:
        emulator.execute_signal("SPY", -1, 507.0, timestamp=now,
                                sell_fraction=1.0, order_source="main_signal")
    elif day == 6:
        emulator.execute_signal("ABC", 1, 19.25, timestamp=now,
                                cash_per_trade=300.0, order_source="main_signal")
        emulator.execute_signal("ABC", -1, 19.25, timestamp=now,
                                sell_fraction=1.0, order_source="main_signal")


def _canonical(result) -> str:
    return json.dumps(result, indent=1, default=str) + "\n"


@pytest.fixture(autouse=True)
def _passive_execution_off(monkeypatch):
    """The golden was written with passive execution off. Pin that here, so a
    host's PASSIVE_EXECUTION_ENABLED or an override broker.py set earlier in
    the process cannot fail this guard falsely."""
    monkeypatch.delenv("PASSIVE_EXECUTION_ENABLED", raising=False)
    monkeypatch.setattr(PortfolioEmulator, "_PASSIVE_OVERRIDE", None)


def test_non_bracket_run_is_byte_identical_to_the_pre_change_golden():
    assert _canonical(_run(_submit_plain)) == GOLDEN.read_text()


def test_the_golden_itself_was_not_regenerated():
    digest = hashlib.sha256(GOLDEN.read_bytes()).hexdigest()
    assert digest == GOLDEN_SHA256, (
        "the golden changed. It may only be written once, by the pre-change "
        "code; restore it from git instead of regenerating it.")


def _submit_with_explicit_defaults(emulator, day, now):
    """The same orders, each passing the three swing-port keywords at their
    defaults -- what an EB tick looks like after the change."""
    plain = emulator.execute_signal

    def execute_signal(*args, **kwargs):
        kwargs.update(bracket=None, whole_shares=False,
                      fill_at_next_open=False)
        return plain(*args, **kwargs)

    emulator.execute_signal = execute_signal
    try:
        _submit_plain(emulator, day, now)
    finally:
        del emulator.execute_signal


def test_default_swing_keywords_change_nothing():
    assert _canonical(_run(_submit_with_explicit_defaults)) == GOLDEN.read_text()


def test_a_run_without_bar_work_never_needs_the_bar_hook():
    seen = []

    def submit(emulator, day, now):
        _submit_plain(emulator, day, now)
        seen.append((emulator.has_bracket_legs(),
                     emulator.has_next_open_orders(),
                     emulator.bar_event_requirements()))

    _run(submit)
    assert set(map(repr, seen)) == {repr((False, False, {}))}


GOLDEN_SHA256 = "9731cf219e9e0f6405a9e5ffdea3dc5244218015a60c9f448872d099f4b6a182"


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-golden"]:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(_canonical(_run(_submit_plain)))
        print(hashlib.sha256(GOLDEN.read_bytes()).hexdigest())
