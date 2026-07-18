"""Deterministic momentum/event challenger (Task 11).

A research control and shadow candidate: its success alone can never pass
the Stage B gate or authorize LIVE_40. Raw score is the registered
cross-sectional formula ``0.50*z(excess_5d) + 0.30*z(excess_20d)
+ 0.20*z(event_score) - 0.20*z(realized_vol_20d)`` with deterministic
symbol tie-breaking; features consume bars at or before ``as_of`` only.
"""
import math
from dataclasses import dataclass

from benchmark_alpha.records import Forecast
from benchmark_alpha.types import EvidenceClass

HORIZONS = (1, 3, 5)


@dataclass(frozen=True)
class ChallengerFeatures:
    excess_5d: float
    excess_20d: float
    realized_vol_20d: float
    trend_quality: float
    event_score: float


def _closes_at_or_before(bars, as_of):
    cutoff = as_of.date().isoformat()
    closes = [float(b["close_adjusted"]) for b in sorted(
        (b for b in bars or () if str(b.get("date")) <= cutoff),
        key=lambda b: str(b.get("date")))
        if b.get("close_adjusted")]
    return closes


def _window_return(closes, days):
    if len(closes) < days + 1:
        return 0.0
    return closes[-1] / closes[-(days + 1)] - 1.0


def build_features(stock_bars, spy_bars, event_score, as_of):
    stock = _closes_at_or_before(stock_bars, as_of)
    spy = _closes_at_or_before(spy_bars, as_of)
    excess_5d = _window_return(stock, 5) - _window_return(spy, 5)
    excess_20d = _window_return(stock, 20) - _window_return(spy, 20)
    rets = [stock[i] / stock[i - 1] - 1.0 for i in range(1, len(stock))][-20:]
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
    else:
        vol = 0.0
    positive_days = sum(1 for r in rets if r > 0)
    trend_quality = positive_days / len(rets) if rets else 0.0
    return ChallengerFeatures(
        excess_5d=excess_5d, excess_20d=excess_20d, realized_vol_20d=vol,
        trend_quality=trend_quality, event_score=float(event_score or 0.0))


def _z_scores(values):
    n = len(values)
    if n < 2:
        return [0.0] * n
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    if var <= 1e-18:
        return [0.0] * n  # zero-variance cross-section
    std = math.sqrt(var)
    return [(v - mean) / std for v in values]


def challenger_forecasts(universe, bars, spy_bars, event_scores, calibrators,
                         run_context):
    symbols = sorted({str(s).upper() for s in universe or ()})
    features = {
        sym: build_features(
            (bars or {}).get(sym) or (), spy_bars,
            (event_scores or {}).get(sym, 0.0), run_context["as_of"])
        for sym in symbols}
    z5 = dict(zip(symbols, _z_scores([features[s].excess_5d for s in symbols])))
    z20 = dict(zip(symbols, _z_scores([features[s].excess_20d for s in symbols])))
    zev = dict(zip(symbols, _z_scores([features[s].event_score for s in symbols])))
    zvol = dict(zip(symbols, _z_scores([features[s].realized_vol_20d for s in symbols])))

    out = []
    for sym in symbols:
        raw = (0.50 * z5[sym] + 0.30 * z20[sym] + 0.20 * zev[sym]
               - 0.20 * zvol[sym])
        for horizon in HORIZONS:
            model = (calibrators or {}).get((EvidenceClass.DETERMINISTIC.value,
                                             horizon))
            reasons = ["deterministic_research_control"]
            if model is None:
                reasons.append("uncalibrated")
                expected, probability, confidence = 0.0, 0.5, 0.0
            else:
                expected, probability, uncertainty = model.predict(raw)
                confidence = max(0.0, min(1.0, 1.0 - uncertainty * 10.0))
            out.append(Forecast(
                producer="deterministic_challenger",
                model_version=str(run_context["model_version"]),
                evidence_class=EvidenceClass.DETERMINISTIC,
                symbol=sym,
                as_of=run_context["as_of"],
                horizon_trading_days=horizon,
                expected_excess_return=expected,
                probability_outperform=probability,
                confidence=confidence,
                feature_version=str(run_context["feature_version"]),
                data_snapshot_id=str(run_context["data_snapshot_id"]),
                eligibility=False,  # research/shadow control only
                gate_reasons=tuple(reasons),
                revision=0,
                instance_id=str(run_context["instance_id"]),
                origin=run_context["origin"],
                run_id=str(run_context["run_id"]),
                raw_score=raw,
            ))
    return out
