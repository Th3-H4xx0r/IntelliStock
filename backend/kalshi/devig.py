"""De-vig methods for n-way markets.

Input: raw implied probabilities (q_i = 1 / decimal_odds), which sum to the
overround O > 1 because the book bakes in vig. Output: fair probabilities
summing to 1.0.

For 3-way soccer (1X2) the favorite-longshot bias makes simple proportional
normalization wrong; Power (and Shin) remove proportionally more vig from
longshots. Power is the default for soccer.
"""
from __future__ import annotations

import math


def proportional_devig(raw: list[float]) -> list[float]:
    """Baseline only — divide out the overround. Wrong for 3-way (no
    favorite-longshot correction); kept for sanity comparison."""
    o = sum(raw)
    return [q / o for q in raw]


def power_devig(raw: list[float], tol: float = 1e-12, max_iter: int = 200) -> list[float]:
    """Find k such that sum(q_i ** k) == 1, then return q_i ** k.

    Each q_i < 1 (decimal odds > 1), so s(k) = sum(q_i ** k) is strictly
    decreasing in k. s(1) = O > 1, so the root k* > 1. Bisection on k.
    """
    def s(k: float) -> float:
        return sum(q ** k for q in raw)

    lo, hi = 1e-4, 64.0
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        val = s(mid)
        if abs(val - 1.0) < tol:
            break
        if val > 1.0:
            lo = mid   # need a larger exponent to shrink the sum
        else:
            hi = mid
    k = (lo + hi) / 2.0
    return [q ** k for q in raw]


def shin_devig(raw: list[float], tol: float = 1e-12, max_iter: int = 200) -> list[float]:
    """Shin (1992): corrects for insider-driven longshot bias.

    Solve for the insider proportion z in [0, ~0.2] such that the implied
    probabilities sum to 1, then return them. At z=0 the sum is sqrt(O) > 1,
    and the sum decreases as z grows, so bisect on z.
    """
    o = sum(raw)

    def probs(z: float) -> list[float]:
        return [
            (math.sqrt(z * z + 4.0 * (1.0 - z) * (q * q) / o) - z) / (2.0 * (1.0 - z))
            for q in raw
        ]

    z_lo, z_hi = 0.0, 0.5
    p = probs((z_lo + z_hi) / 2.0)
    for _ in range(max_iter):
        z = (z_lo + z_hi) / 2.0
        p = probs(z)
        tot = sum(p)
        if abs(tot - 1.0) < tol:
            return p
        if tot > 1.0:
            z_lo = z   # more insider weight pulls the sum down
        else:
            z_hi = z
    return p
