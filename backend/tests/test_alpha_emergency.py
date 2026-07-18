"""Task 6: narrowly-scoped, broker-reconciled reduce-only emergency executor."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.emergency import ReduceOnlyEmergencyExecutor


class FakeBroker:
    def __init__(self, positions):
        self._positions = dict(positions)
        self.submitted = []

    def read_positions(self):
        return dict(self._positions)

    def submit_reduce(self, symbol, qty, client_order_id):
        self.submitted.append((symbol, qty, client_order_id))
        return f"broker-{len(self.submitted)}"


def _executor(positions):
    broker = FakeBroker(positions)
    return ReduceOnlyEmergencyExecutor(
        read_positions=broker.read_positions,
        submit_reduce=broker.submit_reduce,
        instance_id="alpaca-main",
    ), broker


def test_reduce_caps_sell_quantity_to_held():
    executor, broker = _executor({"MRNA": 4.0})
    actions = executor.reduce_to_targets("ep1", {"MRNA": 0.0})
    assert len(actions) == 1
    assert broker.submitted[0][0] == "MRNA"
    assert broker.submitted[0][1] == 4.0
    # Even an absurd negative target can never sell more than held.
    executor2, broker2 = _executor({"MRNA": 4.0})
    executor2.reduce_to_targets("ep1", {"MRNA": -50.0})
    assert broker2.submitted[0][1] == 4.0


def test_rejects_buys_and_shorts():
    executor, broker = _executor({"MRNA": 4.0})
    actions = executor.reduce_to_targets("ep1", {"MRNA": 10.0})  # increase
    assert actions == []
    assert broker.submitted == []


def test_symbol_not_returned_by_broker_reconciliation_is_skipped():
    executor, broker = _executor({"MRNA": 4.0})
    actions = executor.reduce_to_targets("ep1", {"TSLA": 0.0})
    assert actions == []
    assert broker.submitted == []


def test_client_order_id_is_deterministic_per_episode():
    executor, broker = _executor({"MRNA": 4.0, "OKTA": 2.0})
    executor.reduce_to_targets("ep42", {"MRNA": 0.0})
    cid_first = broker.submitted[0][2]
    executor2, broker2 = _executor({"MRNA": 4.0})
    executor2.reduce_to_targets("ep42", {"MRNA": 0.0})
    assert broker2.submitted[0][2] == cid_first
    assert "ep42" in cid_first


def test_executor_has_no_general_order_interface():
    executor, _ = _executor({})
    for forbidden in ("submit_order", "buy", "allocate", "execute_signal",
                      "candidates", "strategy"):
        assert not hasattr(executor, forbidden)


def test_partial_reduction_sells_only_the_excess():
    executor, broker = _executor({"CNC": 12.877131951})
    executor.reduce_to_targets("ep1", {"CNC": 7.0})
    assert broker.submitted[0][1] == pytest.approx(5.877131951)
