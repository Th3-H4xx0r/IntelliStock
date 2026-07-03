"""Pure training core: sample gathering, calibrator fit/apply, evaluation."""
import sys, types
sys.modules.setdefault("socketio", types.ModuleType("socketio"))

from kalshi import training


def _model_fn(fx):
    return fx["_probs"]


def test_gather_from_settled_one_sample_per_side():
    fx = [{"result": "home", "_probs": {"home": 0.6, "draw": 0.25, "away": 0.15}},
          {"result": "draw", "_probs": {"home": 0.4, "draw": 0.3, "away": 0.3}}]
    s = training.gather_samples_from_settled(fx, _model_fn)
    assert len(s) == 6  # 2 games x 3 sides
    # home side of game 1 happened -> outcome 1.0
    assert (0.6, 1.0) in s and (0.25, 0.0) in s
    # draw side of game 2 happened
    assert (0.3, 1.0) in s


def test_gather_from_settled_skips_unsettled():
    fx = [{"result": None, "_probs": {"home": 0.5, "draw": 0.3, "away": 0.2}}]
    assert training.gather_samples_from_settled(fx, _model_fn) == []


def test_gather_from_decisions():
    rows = [{"decision": "placed", "outcome": "win", "fused_fair": 0.7},
            {"decision": "placed", "outcome": "loss", "fused_fair": 0.4},
            {"decision": "skipped", "outcome": None, "fused_fair": 0.9},  # not placed
            {"decision": "placed", "outcome": None, "fused_fair": 0.5}]   # open
    s = training.gather_samples_from_decisions(rows)
    assert s == [(0.7, 1.0), (0.4, 0.0)]


def test_fit_identity_when_empty():
    doc = training.fit_calibrator([])
    assert doc["method"] == "identity" and doc["n_samples"] == 0
    assert training.apply(doc, 0.42) == 0.42


def test_fit_shrink_when_thin():
    doc = training.fit_calibrator([(0.9, 1.0), (0.1, 0.0)], min_total=100)
    assert doc["method"] == "shrink"
    # shrink pulls toward 0.5
    assert 0.5 < training.apply(doc, 0.9) < 0.9


def test_fit_isotonic_when_enough_data():
    # 120 samples where the model is systematically overconfident at the top:
    # predicts 0.8 but only wins half the time -> isotonic should pull 0.8 down.
    samples = [(0.8, 1.0 if i % 2 == 0 else 0.0) for i in range(60)]
    samples += [(0.2, 1.0 if i % 5 == 0 else 0.0) for i in range(60)]
    doc = training.fit_calibrator(samples, min_total=100)
    assert doc["method"] == "isotonic" and doc["calibrator"]
    assert training.apply(doc, 0.8) < 0.8   # overconfidence corrected downward


def test_apply_is_monotone_and_bounded():
    doc = training.fit_calibrator([(0.3, 0.0)] * 50 + [(0.7, 1.0)] * 80, min_total=100)
    lo, hi = training.apply(doc, 0.2), training.apply(doc, 0.9)
    assert 0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0
    assert lo <= hi  # monotone


def test_evaluate_reports_raw_and_calibrated():
    samples = [(0.8, 0.0)] * 50 + [(0.8, 1.0)] * 50  # 0.8 pred, actual 50%
    doc = training.fit_calibrator(samples, min_total=50)
    ev = training.evaluate(samples, doc)
    assert ev["n_eval"] == 100
    # calibrating a badly-overconfident predictor lowers log-loss
    assert ev["cal_logloss"] <= ev["raw_logloss"] + 1e-9
