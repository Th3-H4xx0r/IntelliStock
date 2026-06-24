"""Dixon-Coles bivariate-Poisson scoreline model.

Given expected goals for each side, produce the full P(home=i, away=j) matrix.
Every other goal-based market (1X2, totals, BTTS, exact/correct score, double
chance, halftime) is derived from this one matrix in derive_markets.py — one
model, many markets. Pure stdlib (no scipy needed for the PMF).
"""
from __future__ import annotations

import math

DEFAULT_RHO = -0.05  # low-score dependence correction (Dixon-Coles 1997)


def _poisson(k: int, lam: float) -> float:
    if lam < 0:
        lam = 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def rho_correction(i: int, j: int, home_xg: float, away_xg: float, rho: float) -> float:
    """The Dixon-Coles tau adjustment for the four low-score cells; 1.0 elsewhere."""
    if i == 0 and j == 0:
        return 1.0 - home_xg * away_xg * rho
    if i == 0 and j == 1:
        return 1.0 + home_xg * rho
    if i == 1 and j == 0:
        return 1.0 + away_xg * rho
    if i == 1 and j == 1:
        return 1.0 - rho
    return 1.0


def scoreline_matrix(home_xg: float, away_xg: float, max_goals: int = 10, rho: float = DEFAULT_RHO) -> list[list[float]]:
    """Normalized P(home=i, away=j) for i,j in [0, max_goals]."""
    m = [[0.0] * (max_goals + 1) for _ in range(max_goals + 1)]
    total = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            v = _poisson(i, home_xg) * _poisson(j, away_xg) * rho_correction(i, j, home_xg, away_xg, rho)
            v = max(v, 0.0)
            m[i][j] = v
            total += v
    if total > 0:
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                m[i][j] /= total
    return m
