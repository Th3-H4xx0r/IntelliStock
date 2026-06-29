"""Probability calibration (PURE — no DB/network, unit-tested).

Fixes the overconfidence the autopsy found (model too high on longshots/draws).
Per the design: use a GLOBAL shrink toward the base rate until enough settled data
exists, then a per-bucket ISOTONIC remap (monotone, fit via pool-adjacent-violators)
once a bucket has >= min_per_bucket observations. `calibrate()` picks automatically.

This is the data-blocked component made ready: feed it reconciled (predicted, won)
samples from kalshi_decisions once settlements accrue, and apply to fused fair value.
Until then it degrades to a safe global shrink (never trusts thin data).
"""
from __future__ import annotations


def shrink(p: float, *, strength: float = 0.5, base: float = 0.5) -> float:
    """Pull a probability toward `base` by `strength` (0=no change, 1=fully to base).
    A conservative global calibrator that curbs overconfidence when we lack the data
    to fit a real curve."""
    p = max(0.0, min(1.0, float(p)))
    s = max(0.0, min(1.0, float(strength)))
    return (1.0 - s) * p + s * base


def _pav(points):
    """Weighted pool-adjacent-violators isotonic regression. points: [(x, y, w)]
    sorted by x. Returns [(x, fitted_y)] with fitted_y monotone non-decreasing."""
    blocks = [[y, w, [x]] for x, y, w in points]  # [mean, weight, xs]
    i = 1
    while i < len(blocks):
        if blocks[i - 1][0] <= blocks[i][0]:
            i += 1
            continue
        # violation: merge i-1 and i (weighted mean)
        m0, w0, x0 = blocks[i - 1]
        m1, w1, x1 = blocks[i]
        tw = w0 + w1
        blocks[i - 1] = [(m0 * w0 + m1 * w1) / tw, tw, x0 + x1]
        del blocks[i]
        if i > 1:
            i -= 1
    out = []
    for mean, _w, xs in blocks:
        for x in xs:
            out.append((x, mean))
    return out


def fit_isotonic(samples):
    """samples: [(predicted_prob, outcome 0/1)]. Aggregates by unique prediction
    (so ties pool to their empirical rate), then PAV. Returns a calibrator: sorted
    [(pred, calibrated)] breakpoints for interpolation. None if too few distinct preds."""
    agg: dict = {}
    for p, o in samples:
        if p is None or o is None:
            continue
        s = agg.setdefault(float(p), [0.0, 0])
        s[0] += float(o)
        s[1] += 1
    if len(agg) < 2:
        return None
    pts = sorted((x, sm / n, float(n)) for x, (sm, n) in agg.items())
    return _pav(pts)


def apply_isotonic(calibrator, p: float) -> float:
    """Piecewise-linear interpolation over the isotonic breakpoints."""
    if not calibrator:
        return max(0.0, min(1.0, p))
    p = float(p)
    if p <= calibrator[0][0]:
        return calibrator[0][1]
    if p >= calibrator[-1][0]:
        return calibrator[-1][1]
    for (x0, y0), (x1, y1) in zip(calibrator, calibrator[1:]):
        if x0 <= p <= x1:
            if x1 == x0:
                return y1
            t = (p - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return max(0.0, min(1.0, p))


def calibrate(p: float, samples, *, min_total: int = 100, shrink_strength: float = 0.4) -> float:
    """Auto-select: enough settled data -> isotonic remap; else global shrink. Always
    returns a probability in [0,1]. Safe to call with no/thin data (falls back)."""
    n = sum(1 for s in (samples or []) if s and s[0] is not None and s[1] is not None)
    if n >= min_total:
        cal = fit_isotonic(samples)
        if cal:
            return max(0.0, min(1.0, apply_isotonic(cal, p)))
    return shrink(p, strength=shrink_strength)
