"""LLM analyst panel — the LLM-in-the-loop. Reuses the repo's structured-LLM
helper (like nexus_analyst_panel). The statistical model + sharp line produce
the BASE probability; this reads the qualitative match features (injuries,
lineups, form, rest, h2h) and returns:
  - a BOUNDED probability adjustment per market (clamped to ±adj_cap),
  - a shortlist of the most exploitable markets for this match,
  - a one-line rationale per recommended bet (-> the decision log).
It never bypasses the edge gate or risk caps. `llm_call` is injected so this is
unit-testable with a fake LLM (no network / no cost in tests).
"""
from __future__ import annotations

from kalshi.intelligence.fusion import clamp_adjustment

DEFAULT_ADJ_CAP = 0.05


def build_prompt(features, markets: list[str]) -> str:
    lines = [f"Soccer match: {features.home} vs {features.away} (fixture {features.fixture_id})."]
    if getattr(features, "home_form", None):
        f = features.home_form
        lines.append(f"Home: Elo {f.elo:.0f}, xG/g {f.xg_for:.2f} (against {f.xg_against:.2f}), form {f.form_pts:.1f} pts.")
    if getattr(features, "away_form", None):
        f = features.away_form
        lines.append(f"Away: Elo {f.elo:.0f}, xG/g {f.xg_for:.2f} (against {f.xg_against:.2f}), form {f.form_pts:.1f} pts.")
    lines.append(
        f"Lineup confirmed: {features.lineup_confirmed}. "
        f"Rest: home {features.days_rest_home}d, away {features.days_rest_away}d."
    )
    if getattr(features, "h2h", None):
        lines.append(f"Recent head-to-head: {features.h2h[:5]}.")
    lines.append(f"Candidate market types: {', '.join(markets)}.")
    lines.append(
        "Considering injuries, confirmed lineups, motivation, rest/travel and the h2h, "
        "return JSON {adjustments:{market_type: delta in [-0.05,0.05]}, shortlist:[market_type,...], "
        "rationales:{market_type: one short sentence}}. Be conservative; only adjust where you have a "
        "qualitative reason the statistical model would miss."
    )
    return "\n".join(lines)


def _default_llm_call(prompt: str) -> dict:  # pragma: no cover - integration
    from llm_utils import call_structured_llm
    schema = {
        "type": "object",
        "properties": {
            "adjustments": {"type": "object"},
            "shortlist": {"type": "array", "items": {"type": "string"}},
            "rationales": {"type": "object"},
        },
    }
    return call_structured_llm(prompt=prompt, schema=schema) or {}


def analyze(features, markets: list[str], *, llm_call=None, adj_cap: float = DEFAULT_ADJ_CAP) -> dict:
    """Returns {adjustments, shortlist, rationales}. Adjustments are hard-clamped
    to ±adj_cap so the LLM can never move a probability beyond that. Any LLM
    failure degrades to a no-op (empty adjustments)."""
    call = llm_call or _default_llm_call
    prompt = build_prompt(features, markets)
    try:
        raw = call(prompt) or {}
    except Exception:
        return {"adjustments": {}, "shortlist": [], "rationales": {}}
    adjustments = {}
    for k, v in (raw.get("adjustments") or {}).items():
        try:
            adjustments[k] = clamp_adjustment(float(v), adj_cap)
        except (TypeError, ValueError):
            continue
    return {
        "adjustments": adjustments,
        "shortlist": list(raw.get("shortlist") or []),
        "rationales": {k: str(v) for k, v in (raw.get("rationales") or {}).items()},
    }
