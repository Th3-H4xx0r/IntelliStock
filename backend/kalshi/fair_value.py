"""Fair-value estimation. The sharp market is the model: de-vig the sharpest
book (Pinnacle) into fair P(home/draw/away). A statistical fallback (Elo /
Dixon-Coles) is used only where bookmaker coverage is too thin — ranked below
the sharp line where both exist.
"""
from __future__ import annotations

from kalshi.models import OddsQuote
from kalshi.devig import power_devig, shin_devig, proportional_devig

_METHODS = {
    "power": power_devig,
    "shin": shin_devig,
    "proportional": proportional_devig,
}


def fair_from_odds(q: OddsQuote, method: str = "power") -> dict:
    """De-vig a 3-way quote into fair probabilities keyed by outcome."""
    if method not in _METHODS:
        raise ValueError(f"unknown de-vig method: {method!r}")
    raw = [1.0 / q.home, 1.0 / q.draw, 1.0 / q.away]
    p = _METHODS[method](raw)
    return {"home": p[0], "draw": p[1], "away": p[2]}


def sharp_probs_from_quote(quote, method: str = "power") -> dict:
    """De-vig a 3-way OddsQuote into the ``sharp_probs`` shape the orchestrator +
    build_market_probs expect: ``{"winner": {home, draw, away}}``. None quote -> ``{}``
    (the engine then prices model-only)."""
    if quote is None:
        return {}
    return {"winner": fair_from_odds(quote, method)}


def blend_fair(sharp: dict, fallback: dict | None, sharp_weight: float = 1.0) -> dict:
    """Blend sharp-line fair value with a statistical fallback. With no
    fallback (thin-market coverage absent), returns the sharp value unchanged.
    sharp_weight in [0,1]; the sharp line dominates by default."""
    if not fallback:
        return dict(sharp)
    w = max(0.0, min(1.0, sharp_weight))
    out = {k: w * sharp[k] + (1.0 - w) * fallback.get(k, sharp[k]) for k in sharp}
    total = sum(out.values()) or 1.0
    return {k: v / total for k, v in out.items()}
