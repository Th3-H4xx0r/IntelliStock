"""strategy_f14: the F14 paper mirror as a run-once lane that trades out of band."""
import datetime as dt
import importlib.util
import os
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import forward_mirror as fm  # noqa: E402
from test_forward_mirror import HOLD, FakeClient, _at  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "strategy_f14_lane_under_test", os.path.join(_BACKEND, "strategies", "strategy_f14.py"))
lane = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lane)

ON = {"strategy_f14_enabled": True}


@pytest.fixture
def rig(store, monkeypatch):
    """The lane over a fake account: `fm.build` hands back an executor Mirror."""
    clock = _at(20, 40)
    clock[0] += dt.timedelta(seconds=3)                     # the 12:40 PT tick, a few seconds late
    client = FakeClient(lambda: clock[0])
    built = []

    def build(**kw):
        built.append(kw)
        return fm.Mirror(store=store, client=client, prices_fn=lambda syms: {s: 100.0 for s in syms},
                         frozen_dir="/nope", data_dir="/nope", now_fn=lambda: clock[0],
                         sleep_fn=lambda s: None, **kw)

    monkeypatch.setattr(fm, "build", build)
    monkeypatch.setattr(lane, "_mirrors", {})
    monkeypatch.setattr(lane, "_last_refusal", [None])
    lines = []
    monkeypatch.setattr(lane, "_log", lambda msg, color="white": lines.append(msg))
    yield type("Rig", (), dict(store=store, clock=clock, client=client, built=built, lines=lines))
    fm.set_log_sink(None)


def _run(mode="MONITOR", config=ON):
    return lane.StrategyF14().run_once(["SPY"], {"SPY": 1.0}, None, config, {}, mode=mode)


def _signal(store):
    store.insert(fm.TABLE, {"id": "f14-paper|2026-11-09|signal", "frame_last_close": "2026-11-06", "next": HOLD})


def test_the_class_name_matches_the_brokers_camel_case():
    assert "".join(p.capitalize() for p in "strategy_f14".split("_")) == "StrategyF14"
    assert hasattr(lane, "StrategyF14")


def test_the_schema_header_ships_disabled_and_pinned_to_the_allowed_account():
    import json
    with open(lane.__file__, encoding="utf-8") as f:
        header = f.readline()
    schema = json.loads(header.split("INTELLISTOCK_SCHEMA:", 1)[1])
    assert schema["strategy"] == "strategy_f14" and schema["execution_scope"] == "run_once"
    assert schema["config"] == lane.DEFAULTS
    assert schema["config"]["strategy_f14_enabled"] is False
    assert schema["config"]["account_number"] == "PA3TRLBV6ZNZ"


def test_it_always_returns_nothing_to_the_broker_and_trades_through_the_mirror(rig):
    _signal(rig.store)
    assert _run() == {}
    assert len(rig.client.submitted) == 4                   # the initial sync, sent by the mirror
    assert rig.built == [dict(brokerage_id=fm.DEFAULT_BROKERAGE_ID, account_number="PA3TRLBV6ZNZ",
                              role="executor", own_instance_id="f14-paper", dry_run=False, act_minutes=25.0)]
    assert any("StrategyF14 acted" in line for line in rig.lines)
    assert any(line.startswith("[f14] ") for line in rig.lines)     # the mirror's own lines reach the instance log
    assert _run() == {} and len(rig.client.submitted) == 4  # next tick, same session
    assert len(rig.built) == 1                              # one mirror per process


@pytest.mark.parametrize("mode", [None, "", "backtest", "live"])
def test_a_backtest_tick_never_builds_a_mirror(rig, mode):
    _signal(rig.store)
    assert _run(mode=mode) == {}
    assert rig.built == [] and rig.client.submitted == []


def test_disabled_is_inert(rig):
    _signal(rig.store)
    assert _run(config={}) == {} and _run(config={"strategy_f14_enabled": "false"}) == {}
    assert rig.built == []


def test_a_refusal_is_logged_recorded_once_and_does_not_raise(rig):
    _signal(rig.store)
    rig.client.positions = [type("P", (), dict(symbol="BTCUSD", qty="1", market_value="100", current_price="100"))]
    assert _run() == {} and _run() == {}                    # boots fine, refuses at the book check, twice
    assert rig.client.submitted == []
    assert any("REFUSED" in line and "non-mirror positions" in line for line in rig.lines)
    refused = [r for r in rig.store.run(rig.store.filter(fm.TABLE, {"kind": "refused"}))]
    assert len(refused) == 1 and refused[0]["role"] == "executor"


def test_a_refused_boot_is_retried_on_the_next_tick(rig):
    _signal(rig.store)
    rig.client.account.account_number = "PA0THER00000"
    assert _run() == {}
    assert rig.client.submitted == [] and lane._mirrors == {}
    rig.client.account.account_number = "PA3TRLBV6ZNZ"
    assert _run() == {}
    assert len(rig.client.submitted) == 4


def test_an_unexpected_error_never_reaches_the_broker(rig):
    _signal(rig.store)
    rig.client.get_clock = lambda: (_ for _ in ()).throw(TimeoutError("alpaca down"))
    assert _run() == {}
    assert any("step failed" in line for line in rig.lines)


def test_the_off_limits_account_is_refused_even_if_configured(rig):
    _signal(rig.store)
    assert _run(config={**ON, "account_number": "PA37JPXGU196"}) == {}
    assert rig.client.submitted == []
    assert any("off limits" in line for line in rig.lines)
