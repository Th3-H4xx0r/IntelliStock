"""Fair-value fusion (pure): sharp anchor + statistical model + a BOUNDED LLM
adjustment → one fair value per market. The LLM can never move a probability by
more than ``llm_cap``, so it can't run away with real money. Where there is no
sharp line (props, exact score), the model is used alone.
"""
from __future__ import annotations


def clamp_adjustment(adj: float, cap: float) -> float:
    cap = abs(cap)
    return max(-cap, min(cap, adj))


def fuse(*, sharp: float | None, model: float, llm_adjustment: float, w_sharp: float, llm_cap: float) -> float:
    """Blend sharp + model, then apply the clamped LLM adjustment, clamped to [0,1].
    sharp=None (market the sharp book doesn't price) → model only."""
    if sharp is None:
        base = model
    else:
        w = max(0.0, min(1.0, w_sharp))
        base = w * sharp + (1.0 - w) * model
    f = base + clamp_adjustment(llm_adjustment, llm_cap)
    return max(0.0, min(1.0, f))


def renormalize_group(probs: dict) -> dict:
    """Renormalize a mutually-exclusive group (e.g. 1X2) to sum to 1."""
    total = sum(probs.values())
    if total <= 0:
        n = len(probs)
        return {k: 1.0 / n for k in probs} if n else {}
    return {k: v / total for k, v in probs.items()}
