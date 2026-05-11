from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


DEFAULT_FEATURE_KEYS = [
    "base_raw_score",
    "direct_sentiment",
    "n_paths",
    "company_article_count",
    "company_positive_count",
    "company_negative_count",
    "company_avg_relevance",
    "company_avg_impact",
    "finbert_sentiment_avg",
    "finbert_impulse_max",
    "macro_article_count",
    "macro_negative_count",
    "macro_positive_count",
    "government_action_hits",
    "active_event_count",
    "active_event_negative_count",
    "active_event_positive_count",
    "historical_analog_count",
    "historical_analog_avg_return",
    "position_open",
    "recent_return_5",
    "recent_return_20",
    "recent_return_60",
    "recent_volatility_20",
    "drawdown_from_20_high",
]


@dataclass
class NexusModelBundle:
    feature_keys: list[str]
    classifier: Any | None
    regressor: Any | None
    row_count: int
    trained_at: str
    label_positive_rate: float
    fallback_profile: dict[str, float] | None = None
    ml_data_confidence: float = 0.0


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _feature_vector(feature_keys: list[str], features: dict[str, Any]) -> list[float]:
    row = []
    for key in feature_keys:
        row.append(_coerce_float((features or {}).get(key), 0.0))
    return row


def _adaptive_hyperparams(n_rows: int) -> dict:
    """Scale model complexity to training data size to prevent overfitting."""
    if n_rows < 30:
        return {"max_depth": 2, "max_iter": 30, "min_samples_leaf": max(4, n_rows // 3), "learning_rate": 0.03}
    elif n_rows < 80:
        return {"max_depth": 3, "max_iter": 80, "min_samples_leaf": 4, "learning_rate": 0.05}
    else:
        return {"max_depth": 4, "max_iter": 150, "min_samples_leaf": 4, "learning_rate": 0.06}


def train_nexus_models(
    training_rows: list[dict[str, Any]],
    *,
    feature_keys: list[str] | None = None,
) -> NexusModelBundle | None:
    if not training_rows:
        return None
    feature_keys = list(feature_keys or DEFAULT_FEATURE_KEYS)
    usable = []
    for row in training_rows:
        features = row.get("features") or {}
        signed_return = _coerce_float(row.get("signed_return"), 0.0)
        usable.append({
            "x": _feature_vector(feature_keys, features),
            "signed_return": signed_return,
            "label": 1 if signed_return > 0.0 else 0,
        })
    if len(usable) < 8:
        return None

    classifier = None
    regressor = None
    fallback_profile: dict[str, float] | None = None
    positive_rate = sum(item["label"] for item in usable) / max(len(usable), 1)
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

        hp = _adaptive_hyperparams(len(usable))
        xs = [item["x"] for item in usable]
        ys_class = [item["label"] for item in usable]
        ys_reg = [item["signed_return"] for item in usable]

        if len(set(ys_class)) >= 2:
            classifier = HistGradientBoostingClassifier(
                max_depth=hp["max_depth"],
                learning_rate=hp["learning_rate"],
                max_iter=hp["max_iter"],
                min_samples_leaf=hp["min_samples_leaf"],
                random_state=7,
            )
            classifier.fit(xs, ys_class)

        if len(set(round(y, 4) for y in ys_reg)) >= 2:
            regressor = HistGradientBoostingRegressor(
                max_depth=hp["max_depth"],
                learning_rate=hp["learning_rate"],
                max_iter=hp["max_iter"],
                min_samples_leaf=hp["min_samples_leaf"],
                random_state=7,
            )
            regressor.fit(xs, ys_reg)
    except Exception:
        weighted = {key: 0.0 for key in feature_keys}
        for item in usable:
            for idx, key in enumerate(feature_keys):
                weighted[key] += item["x"][idx] * item["signed_return"]
        scale = max(1, len(usable))
        fallback_profile = {key: (weighted[key] / scale) for key in feature_keys}

    data_confidence = min(1.0, max(0.0, (len(usable) - 8) / 200.0))

    return NexusModelBundle(
        feature_keys=feature_keys,
        classifier=classifier,
        regressor=regressor,
        row_count=len(usable),
        trained_at=datetime.now(timezone.utc).isoformat(),
        label_positive_rate=round(positive_rate, 4),
        fallback_profile=fallback_profile,
        ml_data_confidence=round(data_confidence, 4),
    )


def score_nexus_candidate(
    bundle: NexusModelBundle | None,
    features: dict[str, Any],
) -> dict[str, float]:
    if bundle is None:
        return {
            "ml_up_probability": 0.5,
            "ml_down_probability": 0.5,
            "ml_expected_return": 0.0,
            "ml_confidence": 0.0,
            "ml_data_confidence": 0.0,
            "ml_training_rows": 0,
        }
    x = [_feature_vector(bundle.feature_keys, features)]
    up = 0.5
    down = 0.5
    expected_return = 0.0
    if bundle.classifier is not None:
        try:
            probs = bundle.classifier.predict_proba(x)
            if probs is not None and len(probs) > 0:
                row = list(probs[0])
                if len(row) == 2:
                    down = _coerce_float(row[0], 0.5)
                    up = _coerce_float(row[1], 0.5)
        except Exception:
            pass
    if bundle.regressor is not None:
        try:
            preds = bundle.regressor.predict(x)
            if preds is not None and len(preds) > 0:
                expected_return = _coerce_float(preds[0], 0.0)
        except Exception:
            pass
    if bundle.fallback_profile:
        heuristic = 0.0
        for key, weight in bundle.fallback_profile.items():
            heuristic += _coerce_float((features or {}).get(key), 0.0) * _coerce_float(weight, 0.0)
        expected_return = expected_return or max(-15.0, min(15.0, heuristic / max(len(bundle.feature_keys), 1)))
        centered = max(-0.49, min(0.49, expected_return / 30.0))
        up = max(up, 0.5 + centered)
        down = min(down, 0.5 - centered)
    confidence = max(abs(up - down), min(1.0, abs(expected_return) / 10.0))
    return {
        "ml_up_probability": round(up, 4),
        "ml_down_probability": round(down, 4),
        "ml_expected_return": round(expected_return, 4),
        "ml_confidence": round(confidence, 4),
        "ml_data_confidence": round(bundle.ml_data_confidence, 4) if bundle else 0.0,
        "ml_training_rows": bundle.row_count if bundle else 0,
    }


def summarize_candidate_features(
    bundle: NexusModelBundle | None,
    features: dict[str, Any],
    *,
    top_n: int = 5,
) -> list[dict[str, float]]:
    keys = list((bundle.feature_keys if bundle is not None else DEFAULT_FEATURE_KEYS))
    ranked = []
    for key in keys:
        val = _coerce_float((features or {}).get(key), 0.0)
        if abs(val) <= 1e-9:
            continue
        ranked.append({"feature": key, "value": round(val, 4), "magnitude": round(abs(val), 4)})
    ranked.sort(key=lambda item: item["magnitude"], reverse=True)
    return ranked[: max(1, int(top_n or 5))]
