"""Graph Nexus evidence -> typed forecasts (Task 10).

Reason TEXT is explanatory only and can never create eligibility. Only a
fresh, current-cycle DIRECT forecast may be tradable-eligible; propagation
is research-only, and every legacy override lane (backfill, scheduled votes,
breakout, reserved slots, high-conviction bypass, queued-score grace) is
non-executable until separately promoted. Missing calibration produces an
audited ineligible forecast — a raw score never maps directly to a tradable
expected return.
"""
from benchmark_alpha.records import Forecast
from benchmark_alpha.types import EvidenceClass

HORIZONS = (1, 3, 5)

_EXECUTABLE_LANES = frozenset({"fresh_direct"})


def _gate_reasons(payload):
    reasons = []
    intent = str(payload.get("action_intent") or "").lower()
    if intent in ("", "unknown"):
        reasons.append("unknown_reason_code")
    lane = str(payload.get("source_lane") or "fresh_direct")
    if lane not in _EXECUTABLE_LANES:
        reasons.append(f"non_executable_lane_{lane}")
    if not payload.get("has_graph_signal", False):
        reasons.append("no_graph_signal")
    if not payload.get("quality_ok", True):
        reasons.append("failed_quality_filter")
    if str(payload.get("trend_evidence") or "").lower() == "negative":
        reasons.append("negative_trend_evidence")
    if not payload.get("price_available", True):
        reasons.append("missing_price")
    if payload.get("ml_output_present") and not payload.get("ml_enabled", True):
        reasons.append("disabled_model_leg_output")
    return reasons


def graph_forecasts(scores, metadata, calibrators, run_context):
    """Emit 1/3/5-session typed forecasts for every scored symbol.

    ``calibrators`` maps ``(evidence_class_value, horizon)`` to a
    ``CalibratedModel``. Ineligible evidence still emits audited forecasts
    (research + outcome evaluation cover rejected candidates too)."""
    out = []
    for symbol, payload in sorted((scores or {}).items()):
        if not isinstance(payload, dict) or str(symbol).startswith("_"):
            continue
        evidence_raw = str(payload.get("evidence") or "direct").lower()
        evidence = (EvidenceClass.DIRECT if evidence_raw == "direct"
                    else EvidenceClass.PROPAGATION)
        reasons = _gate_reasons(payload)
        if evidence is not EvidenceClass.DIRECT:
            reasons.append("propagation_research_only")
        raw_score = float(payload.get("score") or 0.0)

        for horizon in HORIZONS:
            model = (calibrators or {}).get((evidence.value, horizon))
            horizon_reasons = list(reasons)
            if model is None:
                horizon_reasons.append("uncalibrated")
                expected, probability, confidence = 0.0, 0.5, 0.0
            else:
                expected, probability, uncertainty = model.predict(raw_score)
                confidence = max(0.0, min(1.0, 1.0 - uncertainty * 10.0))
            eligible = not horizon_reasons
            out.append(Forecast(
                producer="graph_nexus",
                model_version=str(run_context["model_version"]),
                evidence_class=evidence,
                symbol=str(symbol),
                as_of=run_context["as_of"],
                horizon_trading_days=horizon,
                expected_excess_return=expected if eligible else (
                    expected if model is not None else 0.0),
                probability_outperform=probability,
                confidence=confidence,
                feature_version=str(run_context["feature_version"]),
                data_snapshot_id=str(run_context["data_snapshot_id"]),
                eligibility=eligible,
                gate_reasons=tuple(horizon_reasons),
                revision=0,
                instance_id=str(run_context["instance_id"]),
                origin=run_context["origin"],
                run_id=str(run_context["run_id"]),
            ))
    return out
