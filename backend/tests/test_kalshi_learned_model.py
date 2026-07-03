"""Learned 1X2 model: fit/predict, it learns signal, and degrades safely."""
import sys, types
sys.modules.setdefault("socketio", types.ModuleType("socketio"))

from kalshi import learned_model as lm


def test_predict_is_a_distribution():
    w = lm.fit([[0.6, 0.2, 0.2], [0.2, 0.2, 0.6]], [0, 2], iters=50)
    p = lm.predict(w, [0.6, 0.2, 0.2])
    assert set(p) == {"home", "draw", "away"}
    assert abs(sum(p.values()) - 1.0) < 1e-9
    assert all(0.0 <= v <= 1.0 for v in p.values())


def test_learns_a_separable_signal():
    # Feature 0 high -> home wins; feature 2 high -> away wins. Learnable.
    X = [[0.9, 0.05, 0.05]] * 30 + [[0.05, 0.05, 0.9]] * 30
    y = [0] * 30 + [2] * 30
    w = lm.fit(X, y, l2=0.5, iters=800)
    assert lm.predict(w, [0.9, 0.05, 0.05])["home"] > 0.5
    assert lm.predict(w, [0.05, 0.05, 0.9])["away"] > 0.5


def test_strong_l2_is_less_confident_than_weak():
    X = [[0.9, 0.05, 0.05]] * 30 + [[0.05, 0.05, 0.9]] * 30
    y = [0] * 30 + [2] * 30
    weak = lm.predict(lm.fit(X, y, l2=0.1, iters=800), [0.9, 0.05, 0.05])["home"]
    strong = lm.predict(lm.fit(X, y, l2=20.0, iters=800), [0.9, 0.05, 0.05])["home"]
    assert strong < weak   # heavy L2 damps the feature-driven confidence


def test_deterministic():
    X, y = [[0.6, 0.2, 0.2], [0.3, 0.3, 0.4]], [0, 2]
    assert lm.fit(X, y, iters=100) == lm.fit(X, y, iters=100)


def test_predict_degrades_on_bad_input():
    assert lm.predict([[0, 0, 0]], [1, 2, 3, 4, 5]) == {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
    assert lm.predict("garbage", [0.5]) == {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}


def test_fit_empty_is_bias_only():
    w = lm.fit([], [])
    p = lm.predict(w, [])   # bias-only -> uniform
    assert abs(p["home"] - 1 / 3) < 1e-9


def test_predict_uniform_on_nonfinite_weights():
    nan_w = [[float("nan"), 0.0, 0.0], [0.0, float("inf"), 0.0]]
    assert lm.predict(nan_w, [0.5]) == {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}


def test_fit_never_returns_nonfinite_even_if_diverged():
    import numpy as np
    X = [[0.9, 0.05, 0.05]] * 30 + [[0.05, 0.05, 0.9]] * 30
    y = [0] * 30 + [2] * 30
    w = lm.fit(X, y, l2=100000.0, lr=0.5, iters=200)   # deliberately unstable -> must not persist NaN
    assert np.isfinite(np.asarray(w)).all()
    p = lm.predict(w, [0.9, 0.05, 0.05])
    assert abs(sum(p.values()) - 1.0) < 1e-9 and all(0 <= v <= 1 for v in p.values())
