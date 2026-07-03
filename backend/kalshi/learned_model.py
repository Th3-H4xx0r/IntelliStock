"""Learned 1X2 model (pure numpy): L2-regularized multinomial logistic regression.

SP2's data-driven counterpart to the physical Elo->Dixon-Coles model. It learns to
map a small feature vector (the physical model's own probs + Elo diff + neutral-site
flag) onto the actual outcome — i.e. it learns to CORRECT the physical model from
settled results. Deliberately simple + strongly regularized: on ~dozens-to-hundreds
of games a heavier model (gradient boosting) would overfit; logistic regression with
L2 degrades gracefully toward the base rate instead.

Deterministic (zero init, fixed iterations) so the same data always yields the same
weights — required for the registry/champion reproducibility. No sklearn/scipy.
"""
from __future__ import annotations

import numpy as np

CLASSES = ("home", "draw", "away")


def _softmax_rows(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def fit(X, y, *, l2: float = 2.0, iters: int = 1000, lr: float = 0.5) -> list:
    """Fit weights for a 3-class multinomial logistic regression.

    X: sequence of feature vectors (equal length). y: class indices in {0,1,2}
    (home,draw,away). Returns the weight matrix (d+1 x 3, last row = bias) as a
    plain nested list (JSON-serialisable for the registry). L2 penalizes weights
    but not the bias. Empty/degenerate input -> a bias-only model at the base rate."""
    X = np.asarray(list(X), dtype=float)
    y = np.asarray(list(y), dtype=int)
    if X.ndim != 2 or len(X) == 0:
        return np.zeros((1, 3)).tolist()  # bias-only -> uniform after softmax
    n, d = X.shape
    Xb = np.hstack([X, np.ones((n, 1))])          # append bias column
    W = np.zeros((d + 1, 3))
    Y = np.zeros((n, 3))
    Y[np.arange(n), np.clip(y, 0, 2)] = 1.0
    reg_mask = np.ones((d + 1, 1))
    reg_mask[-1, 0] = 0.0                          # don't regularize the bias
    for _ in range(iters):
        P = _softmax_rows(Xb @ W)
        grad = Xb.T @ (P - Y) / n + (l2 / n) * (W * reg_mask)
        W -= lr * grad
    return W.tolist()


def predict(weights, x) -> dict:
    """Predict {home,draw,away} for a single feature vector. Degrade-safe: any
    shape mismatch / bad weights -> uniform 1/3 each (never raises)."""
    try:
        W = np.asarray(weights, dtype=float)
        xb = np.append(np.asarray(x, dtype=float), 1.0)
        if xb.shape[0] != W.shape[0]:
            return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
        z = xb @ W
        z = z - z.max()
        e = np.exp(z)
        p = e / e.sum()
        return {"home": float(p[0]), "draw": float(p[1]), "away": float(p[2])}
    except Exception:
        return {"home": 1 / 3, "draw": 1 / 3, "away": 1 / 3}
