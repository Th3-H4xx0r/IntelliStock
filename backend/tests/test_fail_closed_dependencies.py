import inspect
from dataclasses import replace

from benchmark_alpha.watchdog import AlphaWatchdog

from live_orders import Health, OrderSide, UnifiedOrderGate
from live_order_task8_helpers import intent, snapshot


def test_kill_switch_read_failure_blocks_buy():
    decision = UnifiedOrderGate().evaluate(
        intent(side=OrderSide.BUY),
        snapshot(kill_switch=Health.UNKNOWN),
    )
    assert decision.allowed is False
    assert "dependency.kill_switch.unknown" in decision.reason_codes


def test_stale_watchdog_blocks_buy():
    healthy = snapshot()
    decision = UnifiedOrderGate().evaluate(
        intent(side=OrderSide.BUY),
        replace(healthy, watchdog_at=None),
    )
    assert decision.allowed is False
    assert "dependency.watchdog.stale" in decision.reason_codes


def test_watchdog_runtime_has_no_direct_order_submission_path():
    import benchmark_alpha.watchdog_main as runtime

    source = inspect.getsource(runtime)
    assert "submit_order" not in source
    assert "MarketOrderRequest" not in source
    assert "ReduceOnlyEmergencyExecutor" not in source


def test_watchdog_persists_health_evidence_on_every_poll():
    class Probe:
        def broker_equity(self):
            return 100

        def broker_positions(self):
            return {}

        def cancel_entry_orders(self):
            raise AssertionError("healthy poll must not cancel")

        def halt_instance(self):
            raise AssertionError("healthy poll must not halt")

    class Store:
        def get_state(self, _key):
            return type(
                "Record",
                (),
                {"payload": {"equity": 100, "marks": {}}},
            )()

    writes = []
    result = AlphaWatchdog(
        probe=Probe(),
        rethink_store=Store(),
        thresholds={},
        instance_id="alpaca-main",
        reduce_executor=None,
        health_writer=writes.append,
    ).poll_once(__import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ))
    assert result.status == "OK"
    assert len(writes) == 1
    assert writes[0].status == "healthy"
    assert writes[0].evidence_hash
