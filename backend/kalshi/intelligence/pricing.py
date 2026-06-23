"""Fused per-market pricing (pure). Turns expected goals -> a Dixon-Coles
scoreline matrix -> model probabilities per market type, then fuses each with the
sharp line (where it exists) and the bounded LLM adjustment. Output feeds
strategy.candidates. This is the glue that the run_instance loop orchestrates."""
from __future__ import annotations

from kalshi.quant.dixon_coles import scoreline_matrix
from kalshi.quant.derive_markets import one_x_two, over_under, btts, double_chance
from kalshi.intelligence.fusion import fuse


def model_market_probs(expected_goals, over_under_line: float = 2.5) -> dict:
    """Model probabilities per market type from expected goals (home_xg, away_xg).
    None -> {} (no model signal; caller falls back to the sharp line)."""
    if expected_goals is None:
        return {}
    m = scoreline_matrix(expected_goals[0], expected_goals[1])
    bt = btts(m)
    return {
        "winner": one_x_two(m),                 # {home, draw, away}
        "over_under": over_under(m, over_under_line),  # {over, under}
        "btts": {"yes": bt["yes"], "no": bt["no"]},
        "double_chance": double_chance(m),
    }


def build_market_probs(expected_goals, sharp_probs, analyst_adjustments, *, w_sharp: float = 0.7, llm_cap: float = 0.05) -> dict:
    """Fused fair value per {market_type: {side: prob}}. Combines the scoreline
    model, the sharp line, and the bounded per-market LLM adjustment."""
    model = model_market_probs(expected_goals)
    sharp_probs = sharp_probs or {}
    analyst_adjustments = analyst_adjustments or {}

    out: dict = {}
    for mt in set(model) | set(sharp_probs):
        m_sides = model.get(mt, {})
        s_sides = sharp_probs.get(mt, {})
        adj = float(analyst_adjustments.get(mt, 0.0) or 0.0)
        fused = {}
        for side in set(m_sides) | set(s_sides):
            sharp_v = s_sides.get(side)
            model_v = m_sides.get(side, sharp_v if sharp_v is not None else 0.0)
            fused[side] = fuse(sharp=sharp_v, model=model_v, llm_adjustment=adj, w_sharp=w_sharp, llm_cap=llm_cap)
        out[mt] = fused
    return out
