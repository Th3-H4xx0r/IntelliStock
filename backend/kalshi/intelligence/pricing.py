"""Fused per-market pricing (pure). Turns expected goals -> a Dixon-Coles
scoreline matrix -> model probabilities per market type, then fuses each with the
sharp line (where it exists) and the bounded LLM adjustment. Output feeds
strategy.candidates. This is the glue that the run_instance loop orchestrates."""
from __future__ import annotations

from kalshi.quant.dixon_coles import scoreline_matrix
from kalshi.quant.derive_markets import one_x_two, over_under, btts, double_chance
from kalshi.quant.player_poisson import p_player_scores
from kalshi.intelligence.fusion import fuse, clamp_adjustment, renormalize_group


def player_market_probs(players, *, default_minutes: float = 75.0, lineup_confirmed: bool = True) -> dict:
    """P(player scores) per player (the model is primary — sharp books don't
    price these). side key = player name; gated on lineup confirmation."""
    out = {}
    for p in players or []:
        name = getattr(p, "name", "")
        if name:
            out[name] = p_player_scores(p.xg_per90, default_minutes, lineup_confirmed)
    return out

# Mutually-exclusive groups whose sides must sum to 1. The LLM scalar adjustment
# nudges the named CANONICAL side (positive = toward it); the group is then
# renormalized so complements/other outcomes adjust correctly. Markets NOT here
# (double_chance: overlapping; exact_score/player_score: independent per ticker)
# get the adjustment per side without renormalization.
_ME_GROUPS = {"winner": "home", "over_under": "over", "btts": "yes"}


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


def build_market_probs(expected_goals, sharp_probs, analyst_adjustments, *, player_probs=None, w_sharp: float = 0.7, llm_cap: float = 0.05) -> dict:
    """Fused fair value per {market_type: {side: prob}}. Combines the scoreline
    model, the sharp line, the bounded per-market LLM adjustment, and (when
    provided) the player-to-score model under the 'player_score' market type."""
    model = model_market_probs(expected_goals)
    sharp_probs = sharp_probs or {}
    analyst_adjustments = analyst_adjustments or {}

    out: dict = {}
    for mt in set(model) | set(sharp_probs):
        m_sides = model.get(mt, {})
        s_sides = sharp_probs.get(mt, {})
        adj = clamp_adjustment(float(analyst_adjustments.get(mt, 0.0) or 0.0), llm_cap)

        # Base fused per side (sharp + model, NO LLM yet).
        base = {}
        for side in set(m_sides) | set(s_sides):
            sharp_v = s_sides.get(side)
            model_v = m_sides.get(side, sharp_v if sharp_v is not None else 0.0)
            base[side] = fuse(sharp=sharp_v, model=model_v, llm_adjustment=0.0, w_sharp=w_sharp, llm_cap=llm_cap)

        if mt in _ME_GROUPS:
            # Nudge the canonical side, then renormalize so the group sums to 1
            # (complements/other outcomes move correctly; no double-counting).
            primary = _ME_GROUPS[mt]
            if primary in base:
                base[primary] = max(0.0, min(1.0, base[primary] + adj))
            out[mt] = renormalize_group(base)
        else:
            # Independent / overlapping markets: apply the adjustment per side,
            # no renormalization.
            out[mt] = {side: max(0.0, min(1.0, v + adj)) for side, v in base.items()}

    # Player-to-score props: model-primary, independent per player.
    if player_probs:
        padj = clamp_adjustment(float(analyst_adjustments.get("player_score", 0.0) or 0.0), llm_cap)
        out["player_score"] = {name: max(0.0, min(1.0, float(v) + padj)) for name, v in player_probs.items()}
    return out
