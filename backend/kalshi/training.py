"""Self-supervised calibration training (PURE — no DB/network, unit-tested).

Turns settled soccer outcomes into a fitted probability calibrator plus held-out
metrics for the model registry. The core learning loop: the model predicts, games
settle, and its probabilities are remapped toward the empirical hit-rate so a
stated "60%" actually wins ~60% of the time.

Reuses `kalshi.calibration` (isotonic PAV + global shrink). Degrades safely: no /
thin data -> shrink or identity, never a hard failure.
"""
from __future__ import annotations

import math

from kalshi import calibration

_SIDES = ("home", "draw", "away")


def gather_samples_from_settled(fixtures, model_fn) -> list:
    """One `(pred, outcome01)` sample per SIDE of each settled fixture — the richest
    training signal (every game, every side), no trading required.

    `fixtures`: dicts carrying `result` in {home,draw,away}. `model_fn(fx)` ->
    `{home,draw,away}` probabilities. Sides the model doesn't price are skipped."""
    out = []
    for fx in fixtures or []:
        res = (fx or {}).get("result")
        if res not in _SIDES:
            continue
        probs = model_fn(fx) or {}
        for side in _SIDES:
            p = probs.get(side)
            if p is None:
                continue
            out.append((float(p), 1.0 if side == res else 0.0))
    return out


def gather_samples_from_decisions(rows) -> list:
    """`(fused_fair, win01)` from PLACED + settled decision rows — the bot's own
    realized bets. Narrower than the settled-fixture signal (only sides it bet),
    but grades exactly what it traded."""
    out = []
    for r in rows or []:
        if (r or {}).get("decision") != "placed":
            continue
        oc = r.get("outcome")
        fair = r.get("fused_fair")
        if oc not in ("win", "loss") or fair is None:
            continue
        out.append((float(fair), 1.0 if oc == "win" else 0.0))
    return out


def fit_calibrator(samples, *, min_total: int = 100, shrink_strength: float = 0.4) -> dict:
    """Fit a registry-shaped calibrator doc from `(pred, 0/1)` samples: an ISOTONIC
    remap once there's enough data, else a conservative global SHRINK, else identity
    when there's nothing. Never raises."""
    clean = [(p, o) for p, o in (samples or []) if p is not None and o is not None]
    n = len(clean)
    if n == 0:
        return {"method": "identity", "calibrator": None, "shrink_strength": 0.0, "n_samples": 0}
    if n >= min_total:
        cal = calibration.fit_isotonic(clean)
        if cal:
            return {"method": "isotonic",
                    "calibrator": [[float(x), float(y)] for x, y in cal],
                    "shrink_strength": 0.0, "n_samples": n}
    # thin data -> shrink toward the base rate (never trust a curve fit on noise)
    return {"method": "shrink", "calibrator": None,
            "shrink_strength": float(shrink_strength), "n_samples": n}


def apply(doc, p):
    """Apply a calibrator doc to a probability. Identity for a missing/identity doc.
    Result is clamped to [0,1]. Monotone."""
    if p is None:
        return p
    p = max(0.0, min(1.0, float(p)))
    if not doc:
        return p
    method = doc.get("method")
    if method == "isotonic" and doc.get("calibrator"):
        cal = [(float(x), float(y)) for x, y in doc["calibrator"]]
        return max(0.0, min(1.0, calibration.apply_isotonic(cal, p)))
    if method == "shrink":
        return calibration.shrink(p, strength=float(doc.get("shrink_strength", 0.4)))
    return p


def _logloss_brier(samples, doc):
    if not samples:
        return 0.0, 0.0
    ll = 0.0
    br = 0.0
    for p, o in samples:
        q = apply(doc, p) if doc else max(0.0, min(1.0, float(p)))
        q = max(1e-6, min(1.0 - 1e-6, q))
        ll += -(o * math.log(q) + (1.0 - o) * math.log(1.0 - q))
        br += (q - o) ** 2
    n = len(samples)
    return ll / n, br / n


def evaluate(samples, doc) -> dict:
    """Held-out raw-vs-calibrated log-loss + Brier on `(pred, 0/1)` samples."""
    raw_ll, raw_br = _logloss_brier(samples, None)
    cal_ll, cal_br = _logloss_brier(samples, doc)
    return {"raw_logloss": raw_ll, "cal_logloss": cal_ll,
            "raw_brier": raw_br, "cal_brier": cal_br, "n_eval": len(samples)}


def reliability_buckets(samples, doc=None, *, n_buckets: int = 5) -> list:
    """Predicted-vs-actual reliability points (for a UI reliability curve). Bins
    calibrated predictions into `n_buckets` and reports avg predicted vs hit rate."""
    buckets = [[0, 0.0, 0.0] for _ in range(n_buckets)]  # [n, sum_pred, sum_outcome]
    for p, o in samples or []:
        q = apply(doc, p)
        idx = min(n_buckets - 1, max(0, int(q * n_buckets)))
        buckets[idx][0] += 1
        buckets[idx][1] += q
        buckets[idx][2] += o
    out = []
    for n, sp, so in buckets:
        if n:
            out.append({"n": n, "predicted": round(sp / n, 4), "actual": round(so / n, 4)})
    return out
