"""Score-to-excess-return calibration (Task 10).

Probability calibration and conditional-return estimation are SEPARATE
artifacts (one isotonic curve cannot produce both — H08). Fitting uses
date-grouped cross-fitting for the uncertainty estimate, conservative
shrinkage toward zero/0.5, and requires at least 100 resolved observations
containing both positive and negative classes. Artifacts serialize to JSON
breakpoints — never pickle.
"""
import json
from dataclasses import dataclass

MIN_OBSERVATIONS = 100
RETURN_SHRINKAGE = 0.5      # toward zero
PROBABILITY_SHRINKAGE = 0.3  # toward 0.5
CROSS_FIT_FOLDS = 5


class CalibrationError(Exception):
    pass


def _pav(points):
    """Pool-adjacent-violators isotonic regression on (x, y) pairs.
    Returns breakpoints [(x, fitted_y), ...] with fitted_y non-decreasing."""
    points = sorted(points)
    blocks = [[x, y, 1.0] for x, y in points]  # x, mean, weight
    merged = []
    for block in blocks:
        merged.append(block)
        while len(merged) > 1 and merged[-2][1] > merged[-1][1]:
            x2, y2, w2 = merged.pop()
            x1, y1, w1 = merged.pop()
            merged.append([x2, (y1 * w1 + y2 * w2) / (w1 + w2), w1 + w2])
    return [(b[0], b[1]) for b in merged]


def _step_interpolate(breakpoints, x):
    if not breakpoints:
        return 0.0
    result = breakpoints[0][1]
    for bx, by in breakpoints:
        if x >= bx:
            result = by
        else:
            break
    return result


@dataclass(frozen=True)
class CalibratedModel:
    evidence_class: str
    horizon_trading_days: int
    probability_breakpoints: tuple
    return_breakpoints: tuple
    uncertainty: float
    sample_count: int
    training_start: str
    training_end: str

    def predict(self, raw_score):
        raw_prob = _step_interpolate(self.probability_breakpoints, float(raw_score))
        raw_ret = _step_interpolate(self.return_breakpoints, float(raw_score))
        prob = 0.5 + (raw_prob - 0.5) * (1.0 - PROBABILITY_SHRINKAGE)
        expected = raw_ret * (1.0 - RETURN_SHRINKAGE)
        return expected, min(1.0, max(0.0, prob)), self.uncertainty

    def to_json(self):
        return json.dumps({
            "evidence_class": self.evidence_class,
            "horizon_trading_days": self.horizon_trading_days,
            "probability_breakpoints": list(map(list, self.probability_breakpoints)),
            "return_breakpoints": list(map(list, self.return_breakpoints)),
            "uncertainty": self.uncertainty,
            "sample_count": self.sample_count,
            "training_start": self.training_start,
            "training_end": self.training_end,
        }, sort_keys=True)

    @classmethod
    def from_json(cls, payload):
        doc = json.loads(payload)
        return cls(
            evidence_class=doc["evidence_class"],
            horizon_trading_days=int(doc["horizon_trading_days"]),
            probability_breakpoints=tuple(map(tuple, doc["probability_breakpoints"])),
            return_breakpoints=tuple(map(tuple, doc["return_breakpoints"])),
            uncertainty=float(doc["uncertainty"]),
            sample_count=int(doc["sample_count"]),
            training_start=doc["training_start"],
            training_end=doc["training_end"],
        )


class ForecastCalibrator:
    def fit(self, rows, evidence_class, horizon_trading_days):
        rows = [r for r in (rows or [])
                if r.get("raw_score") is not None
                and r.get("excess_return") is not None]
        if len(rows) < MIN_OBSERVATIONS:
            raise CalibrationError(
                f"need >= {MIN_OBSERVATIONS} resolved observations, "
                f"got {len(rows)}")
        positives = [r for r in rows if float(r["excess_return"]) > 0]
        negatives = [r for r in rows if float(r["excess_return"]) < 0]
        if not positives or not negatives:
            raise CalibrationError(
                "training sample must contain both positive and negative "
                "excess-return classes")

        prob_points = [(float(r["raw_score"]),
                        1.0 if float(r["excess_return"]) > 0 else 0.0)
                       for r in rows]
        ret_points = [(float(r["raw_score"]), float(r["excess_return"]))
                      for r in rows]

        # Date-grouped cross-fit residuals feed the uncertainty estimate:
        # observations from one date never calibrate their own fold.
        dates = sorted({str(r.get("as_of_date") or "") for r in rows})
        folds = [set(dates[i::CROSS_FIT_FOLDS]) for i in range(CROSS_FIT_FOLDS)]
        residuals = []
        for fold_dates in folds:
            train = [(float(r["raw_score"]), float(r["excess_return"]))
                     for r in rows if str(r.get("as_of_date")) not in fold_dates]
            test = [r for r in rows if str(r.get("as_of_date")) in fold_dates]
            if not train or not test:
                continue
            curve = _pav(train)
            for r in test:
                fitted = _step_interpolate(curve, float(r["raw_score"]))
                residuals.append(float(r["excess_return"]) - fitted)
        if residuals:
            mean = sum(residuals) / len(residuals)
            var = sum((x - mean) ** 2 for x in residuals) / max(1, len(residuals) - 1)
            uncertainty = max(1e-6, var ** 0.5)
        else:
            uncertainty = 1.0

        return CalibratedModel(
            evidence_class=str(evidence_class),
            horizon_trading_days=int(horizon_trading_days),
            probability_breakpoints=tuple(_pav(prob_points)),
            return_breakpoints=tuple(_pav(ret_points)),
            uncertainty=uncertainty,
            sample_count=len(rows),
            training_start=dates[0],
            training_end=dates[-1],
        )
