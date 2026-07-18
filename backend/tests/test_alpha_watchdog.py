"""Task 6: independent broker/strategy mark and equity watchdog."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.rethink_store import AlphaRethinkStore, AlphaUnavailableError
from benchmark_alpha.watchdog import AlphaWatchdog, build_watchdog_command

NOW = datetime(2026, 7, 11, 15, 0, tzinfo=timezone.utc)


class FakeProbe:
    """Read/cancel-only broker probe — no general submit method by design."""

    def __init__(self, equity=6000.0, positions=None):
        self.equity = equity
        self.positions = positions or {"MRNA": 4.0}
        self.canceled_entries = 0
        self.halted = 0

    def broker_equity(self):
        return self.equity

    def broker_positions(self):
        return dict(self.positions)

    def cancel_entry_orders(self):
        self.canceled_entries += 1

    def halt_instance(self):
        self.halted += 1


class FakeStateBackend:
    def __init__(self, payload):
        self.payload = payload

    def get_state_row(self, key):
        return {"id": key, "version": 1, "payload": self.payload}

    def health_probe(self):
        return None


class DownStateBackend:
    def get_state_row(self, key):
        raise ConnectionError("rethinkdb down")

    def health_probe(self):
        return "down"


def _watchdog(probe, payload=None, backend=None, reduce_calls=None):
    store = AlphaRethinkStore.for_backend(
        backend or FakeStateBackend(payload or {
            "equity": 6000.0, "marks": {"MRNA": 76.51}}))
    reduce_calls = reduce_calls if reduce_calls is not None else []
    return AlphaWatchdog(
        probe=probe, rethink_store=store,
        thresholds={"equity_pct": 0.02, "consecutive_critical": 3},
        instance_id="alpaca-main",
        reduce_executor=lambda episode_id, targets: reduce_calls.append(
            (episode_id, targets)),
    ), reduce_calls


def test_healthy_poll_reports_ok_and_no_actions():
    probe = FakeProbe(equity=6000.0)
    watchdog, reduce_calls = _watchdog(probe)
    result = watchdog.poll_once(NOW)
    assert result.status == "OK"
    assert probe.canceled_entries == 0
    assert reduce_calls == []


def test_single_mismatch_is_alert_only():
    probe = FakeProbe(equity=5000.0)  # 16.7% off persisted 6000
    watchdog, reduce_calls = _watchdog(probe)
    result = watchdog.poll_once(NOW)
    assert result.status == "ALERT"
    assert result.mismatches
    assert probe.canceled_entries == 0
    assert probe.halted == 0
    assert reduce_calls == []


def test_three_consecutive_mismatches_cancel_halt_and_reduce():
    probe = FakeProbe(equity=5000.0)
    watchdog, reduce_calls = _watchdog(probe)
    assert watchdog.poll_once(NOW).status == "ALERT"
    assert watchdog.poll_once(NOW).status == "ALERT"
    result = watchdog.poll_once(NOW)
    assert result.status == "CRITICAL_ACTION"
    assert probe.canceled_entries == 1
    assert probe.halted == 1
    assert len(reduce_calls) == 1
    episode_id, targets = reduce_calls[0]
    assert targets == {"MRNA": 0.0}  # reduce only what the BROKER reports held


def test_healthy_poll_resets_the_consecutive_counter():
    probe = FakeProbe(equity=5000.0)
    watchdog, reduce_calls = _watchdog(probe)
    watchdog.poll_once(NOW)
    watchdog.poll_once(NOW)
    probe.equity = 6000.0  # recovers
    assert watchdog.poll_once(NOW).status == "OK"
    probe.equity = 5000.0
    watchdog.poll_once(NOW)
    watchdog.poll_once(NOW)
    assert probe.canceled_entries == 0  # counter restarted; not critical yet


def test_watchdog_has_no_general_order_submission():
    watchdog, _ = _watchdog(FakeProbe())
    for forbidden in ("submit_order", "buy", "sell", "execute_signal"):
        assert not hasattr(watchdog, forbidden)
        assert not hasattr(FakeProbe(), forbidden if forbidden != "sell" else "submit_order")


def test_store_outage_keeps_broker_backed_monitoring_and_flags_degraded_audit():
    probe = FakeProbe(equity=6000.0)
    watchdog, _ = _watchdog(probe, backend=DownStateBackend())
    result = watchdog.poll_once(NOW)
    assert result.status in ("OK", "ALERT")
    assert result.degraded_audit is True  # broker history temporarily authoritative


def test_build_watchdog_command_is_pure_and_secret_free():
    cmd = build_watchdog_command("alpaca-main")
    assert any("watchdog" in part for part in cmd)
    assert "alpaca-main" in cmd
    joined = " ".join(cmd)
    assert "key" not in joined.lower() and "secret" not in joined.lower()
