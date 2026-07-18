"""Task 7: typed alpha records with natural identities and indexed writes."""
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from benchmark_alpha.records import (
    AllocationPlan,
    BrokerOrderRecord,
    CashActivity,
    FillRecord,
    Forecast,
    GateRecord,
    HorizonOutcome,
    IncidentRecord,
    OrderIntent,
    PortfolioSnapshot,
    TargetPosition,
)
from benchmark_alpha.rethink_store import AlphaIntegrityError, AlphaRethinkStore
from benchmark_alpha.types import (
    EvidenceClass,
    GateEffect,
    OrderEffect,
    OwnerSource,
    RunOrigin,
)

TS = datetime(2026, 7, 11, 14, 30, tzinfo=timezone.utc)


def _forecast(**kw):
    base = dict(
        producer="graph_nexus", model_version="gn-2026.07",
        evidence_class=EvidenceClass.DIRECT, symbol="aapl", as_of=TS,
        horizon_trading_days=3, expected_excess_return=0.012,
        probability_outperform=0.61, confidence=0.55,
        feature_version="f1", data_snapshot_id="snap-1",
        eligibility=True, gate_reasons=(), revision=0,
        instance_id="alpaca-main", origin=RunOrigin.LIVE, run_id="run-1",
    )
    base.update(kw)
    return Forecast(**base)


def test_forecast_round_trips_and_normalizes_symbol():
    f = _forecast()
    doc = f.to_doc()
    assert doc["symbol"] == "AAPL"
    assert doc["evidence_class"] == "DIRECT"
    assert doc["origin"] == "LIVE"
    assert doc["schema_version"] == 1
    assert doc["record_type"] == "forecast"
    assert doc["id"] == f.record_id
    assert doc["as_of"] == TS.isoformat()


def test_forecast_rejects_unknown_evidence_class_and_naive_timestamp():
    with pytest.raises(ValueError):
        _forecast(evidence_class="unknown")
    with pytest.raises(ValueError):
        _forecast(as_of=datetime(2026, 7, 11))
    with pytest.raises(ValueError):
        _forecast(expected_excess_return=float("nan"))


def test_direct_and_propagation_forecasts_never_collide():
    direct = _forecast(evidence_class=EvidenceClass.DIRECT)
    prop = _forecast(evidence_class=EvidenceClass.PROPAGATION)
    assert direct.record_id != prop.record_id
    # Two producers on the same symbol/horizon also stay distinct.
    other = _forecast(producer="challenger")
    assert other.record_id != direct.record_id
    # Same natural identity => same ID (deterministic).
    assert _forecast().record_id == direct.record_id
    # A revision is a new record.
    assert _forecast(revision=1).record_id != direct.record_id


def test_gate_record_is_the_decision_and_requires_lineage():
    g = GateRecord(run_id="run-1", instance_id="alpaca-main",
                   origin=RunOrigin.LIVE, symbol="AAPL", as_of=TS,
                   effect=GateEffect.REJECTED, reason_codes=("no_graph_signal",),
                   forecast_id=_forecast().record_id, revision=0)
    doc = g.to_doc()
    assert doc["effect"] == "REJECTED"
    assert doc["forecast_id"]
    with pytest.raises(ValueError):
        GateRecord(run_id="run-1", instance_id="alpaca-main",
                   origin=RunOrigin.LIVE, symbol="AAPL", as_of=TS,
                   effect="rejected", reason_codes=(), forecast_id="f", revision=0)
    # Same-cycle decision revisions are distinct records.
    g2 = GateRecord(run_id="run-1", instance_id="alpaca-main",
                    origin=RunOrigin.LIVE, symbol="AAPL", as_of=TS,
                    effect=GateEffect.REJECTED, reason_codes=("x",),
                    forecast_id="f1", revision=1)
    assert g2.record_id != g.record_id


def test_order_intent_carries_full_lineage_and_mark_provenance():
    intent = OrderIntent(
        run_id="run-1", allocation_id="alloc-1", decision_id="dec-1",
        forecast_id="fc-1", instance_id="alpaca-main", origin=RunOrigin.LIVE,
        symbol="AAPL", side="buy", qty=2.0, notional=None,
        reserved_cash=350.0, mark_id="mk-1", mark_source="stream_quote",
        mark_age_seconds=2.5, owner_source=OwnerSource.STRATEGY,
        config_hash="cfg-abc", reason_codes=("fresh_direct",),
        as_of=TS, revision=0,
    )
    doc = intent.to_doc()
    for key in ("run_id", "allocation_id", "decision_id", "forecast_id",
                "mark_source", "mark_age_seconds", "reserved_cash",
                "config_hash", "client_order_id"):
        assert doc[key] is not None
    assert doc["owner_source"] == "STRATEGY"
    # Revision produces a successor identity AND a successor client ID.
    successor = OrderIntent(
        run_id="run-1", allocation_id="alloc-1", decision_id="dec-1",
        forecast_id="fc-1", instance_id="alpaca-main", origin=RunOrigin.LIVE,
        symbol="AAPL", side="buy", qty=2.0, notional=None,
        reserved_cash=350.0, mark_id="mk-1", mark_source="stream_quote",
        mark_age_seconds=2.5, owner_source=OwnerSource.STRATEGY,
        config_hash="cfg-abc", reason_codes=("fresh_direct",),
        as_of=TS, revision=1,
    )
    assert successor.record_id != intent.record_id
    assert successor.client_order_id != intent.client_order_id
    with pytest.raises(ValueError):
        OrderIntent(
            run_id="run-1", allocation_id="alloc-1", decision_id="dec-1",
            forecast_id="fc-1", instance_id="alpaca-main", origin=RunOrigin.LIVE,
            symbol="AAPL", side="hold", qty=2.0, notional=None,
            reserved_cash=0.0, mark_id="m", mark_source="s",
            mark_age_seconds=1.0, owner_source=OwnerSource.STRATEGY,
            config_hash="c", reason_codes=(), as_of=TS, revision=0)


def test_fill_record_prefers_activity_id_and_rejects_regression():
    fill = FillRecord(
        broker_order_id="bo-1", activity_id="act-9", instance_id="alpaca-main",
        origin=RunOrigin.LIVE, symbol="AAPL", side="buy",
        cumulative_qty=3.0, delta_qty=1.0, price=190.0, event_time=TS,
        sequence=2, owner_source=OwnerSource.STRATEGY,
    )
    assert "act-9" in fill.record_id or fill.to_doc()["activity_id"] == "act-9"
    with pytest.raises(ValueError):
        FillRecord(broker_order_id="bo-1", activity_id=None,
                   instance_id="alpaca-main", origin=RunOrigin.LIVE,
                   symbol="AAPL", side="buy", cumulative_qty=1.0,
                   delta_qty=2.0,  # delta exceeds cumulative: regression
                   price=190.0, event_time=TS, sequence=3,
                   owner_source=OwnerSource.STRATEGY)
    with pytest.raises(ValueError):
        FillRecord(broker_order_id="bo-1", activity_id=None,
                   instance_id="alpaca-main", origin=RunOrigin.LIVE,
                   symbol="AAPL", side="buy", cumulative_qty=1.0,
                   delta_qty=0.0, price=190.0, event_time=TS, sequence=3,
                   owner_source=OwnerSource.STRATEGY)
    # Without an activity id, identity includes order/cumulative/time/sequence.
    a = FillRecord(broker_order_id="bo-1", activity_id=None,
                   instance_id="alpaca-main", origin=RunOrigin.LIVE,
                   symbol="AAPL", side="buy", cumulative_qty=2.0,
                   delta_qty=2.0, price=190.0, event_time=TS, sequence=1,
                   owner_source=OwnerSource.STRATEGY)
    b = FillRecord(broker_order_id="bo-1", activity_id=None,
                   instance_id="alpaca-main", origin=RunOrigin.LIVE,
                   symbol="AAPL", side="buy", cumulative_qty=3.0,
                   delta_qty=1.0, price=190.0, event_time=TS, sequence=2,
                   owner_source=OwnerSource.STRATEGY)
    assert a.record_id != b.record_id


def test_unresolved_owner_source_is_rejected():
    with pytest.raises(ValueError):
        BrokerOrderRecord(
            intent_id="in-1", intent_revision=0, broker_order_id="bo-1",
            instance_id="alpaca-main", origin=RunOrigin.LIVE, symbol="AAPL",
            side="buy", effect=OrderEffect.SUBMITTED, requested_qty=2.0,
            as_of=TS, owner_source="unknown")


def test_outcome_identity_includes_revision_for_corrections():
    base = dict(forecast_id="fc-1", horizon_trading_days=3, data_revision=0,
                instance_id="alpaca-main", origin=RunOrigin.LIVE,
                entry_session="2026-07-11", exit_session="2026-07-16",
                entry_price_raw=100.0, exit_price_raw=104.0,
                entry_price_adjusted=100.0, exit_price_adjusted=104.0,
                spy_entry_adjusted=500.0, spy_exit_adjusted=505.0,
                benchmark_relative_return=0.03, corporate_action_state="none",
                missing_reason=None, hypothesis_generating=False,
                evaluated_at=TS)
    o0 = HorizonOutcome(**base)
    corrected = HorizonOutcome(**{**base, "data_revision": 1,
                                  "exit_price_adjusted": 103.5})
    assert corrected.record_id != o0.record_id


def test_allocation_snapshot_cash_activity_and_incident_round_trip():
    plan = AllocationPlan(
        run_id="run-1", instance_id="alpaca-main", origin=RunOrigin.LIVE,
        as_of=TS, revision=0,
        targets=(TargetPosition(symbol="aapl", weight=0.08,
                                forecast_id="fc-1", reason_codes=("top",)),
                 TargetPosition(symbol="SPY", weight=0.90,
                                forecast_id=None, reason_codes=("residual",))),
        reason_codes=("normal",))
    doc = plan.to_doc()
    assert doc["targets"][0]["symbol"] == "AAPL"
    assert abs(sum(t["weight"] for t in doc["targets"]) - 0.98) < 1e-9
    snap = PortfolioSnapshot(instance_id="alpaca-main", origin=RunOrigin.LIVE,
                             as_of=TS, equity=5979.38, cash=1624.15,
                             positions={"CNC": 12.877}, run_id="run-1")
    assert snap.to_doc()["equity"] == 5979.38
    cash = CashActivity(activity_id="act-1", activity_type="deposit",
                        amount=4000.0, effective_at=TS,
                        instance_id="alpaca-main", origin=RunOrigin.LIVE)
    assert cash.to_doc()["activity_type"] == "deposit"
    inc = IncidentRecord(instance_id="alpaca-main", opened_at=TS,
                         kind="reconciliation_mismatch", severity="critical",
                         description="qty drift", origin=RunOrigin.LIVE)
    assert inc.to_doc()["severity"] == "critical"


class FakeRecordBackend:
    def __init__(self):
        self.tables = {}
        self.durabilities = []

    def insert_record(self, table, doc, *, durability):
        self.durabilities.append(durability)
        rows = self.tables.setdefault(table, {})
        prior = rows.get(doc["id"])
        if prior is not None:
            return prior
        rows[doc["id"]] = doc
        return None


def test_write_record_is_idempotent_only_for_byte_identical_content():
    backend = FakeRecordBackend()
    store = AlphaRethinkStore.for_backend(backend)
    f = _forecast()
    assert store.write_record(f) is True
    assert store.write_record(_forecast()) is False  # identical content
    diverged = _forecast(expected_excess_return=0.999)
    # Same natural identity fields except a non-identity payload change is
    # impossible here (payload differs => integrity error on same ID only if
    # IDs match). Force the collision by reusing the identical identity:
    object.__setattr__(diverged, "expected_excess_return", 0.5)
    with pytest.raises(AlphaIntegrityError):
        store.write_record(_ForcedId(f.record_id, diverged))
    assert set(backend.durabilities) == {"hard"}
    assert f.TABLE in backend.tables


class _ForcedId:
    """Wrapper forcing a record onto an existing ID with different content."""

    def __init__(self, record_id, inner):
        self._id = record_id
        self._inner = inner
        self.TABLE = inner.TABLE

    @property
    def record_id(self):
        return self._id

    def to_doc(self):
        doc = dict(self._inner.to_doc())
        doc["id"] = self._id
        return doc
