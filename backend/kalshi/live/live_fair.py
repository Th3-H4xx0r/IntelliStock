"""Hybrid live fair value (Kalshi-price-only mode).

Blends the live market de-vig probability (the anchor — it has the score baked
in) with a time-decayed pre-match prior, plus a bounded LLM tilt. The model
weight decays to ~0 by full time, so late in a match the market dominates. Pure +
unit-tested."""
from __future__ import annotations


def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def live_fair(market_prob, prematch_prob, elapsed_min, llm_tilt: float = 0.0, *,
              w_market: float = 0.7, regulation_min: float = 115.0,
              tilt_cap: float = 0.05) -> float:
    """Live fair probability in [0,1].

    market_prob: de-vig live market probability (anchor).
    prematch_prob: the pre-match model probability for this side (None -> market).
    elapsed_min: best-effort minute (None -> 0, treated as kickoff).
    The pre-match model's weight is (1 - w_market) at kickoff and decays linearly
    to 0 at regulation_min; the market takes the rest. llm_tilt clamped to ±tilt_cap."""
    m = _clamp01(float(market_prob))
    p = _clamp01(float(prematch_prob)) if prematch_prob is not None else m
    frac = _clamp01((float(elapsed_min) if elapsed_min else 0.0) / max(1.0, regulation_min))
    model_w = (1.0 - w_market) * (1.0 - frac)   # decays to 0 by full time
    mkt_w = 1.0 - model_w
    base = mkt_w * m + model_w * p
    tilt = max(-tilt_cap, min(tilt_cap, float(llm_tilt or 0.0)))
    return _clamp01(base + tilt)
