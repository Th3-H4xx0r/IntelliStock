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

import math

from kalshi.intelligence.fusion import clamp_adjustment

DEFAULT_ADJ_CAP = 0.05


def build_prompt(features, markets: list[str], news: str = "") -> str:
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
    if news:
        lines.append("")
        lines.append("RECENT NEWS (injuries, suspensions, confirmed lineups, who's playing, team news):")
        lines.append(news)
        lines.append("")
    lines.append(f"Candidate market types: {', '.join(markets)}.")
    lines.append(
        "Read the news for injuries, suspensions, confirmed lineups, key players in/out, motivation, "
        "rest/travel, and the h2h. Then return JSON {adjustments:{market_type: delta in [-0.05,0.05]}, "
        "shortlist:[market_type,...], rationales:{market_type: one short sentence citing the news/reason}}. "
        "Be conservative; only adjust where you have a qualitative reason (e.g. a key striker injured) the "
        "statistical model would miss. Positive winner delta favors HOME; positive over_under favors OVER; "
        "positive btts favors YES."
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


def make_llm_call(model_doc: dict | None):  # pragma: no cover - integration
    """Build an llm_call(prompt)->dict bound to a chosen model (a Models-table row
    {provider, model, name, api_key}). Returns None if it can't be built, so the
    analyst degrades to a no-op (the engine still trades on the statistical model)."""
    if not model_doc:
        return None
    try:
        from pydantic import BaseModel, Field
        from llm_utils import call_structured_llm_by_provider, resolve_api_key_for_provider

        provider = (model_doc.get("provider") or "").strip()
        model = (model_doc.get("model") or model_doc.get("name") or "").strip()
        if not provider or not model:
            return None
        api_key = (model_doc.get("api_key") or "").strip() or resolve_api_key_for_provider(provider)

        class _AnalystOut(BaseModel):
            adjustments: dict = Field(default_factory=dict)
            shortlist: list = Field(default_factory=list)
            rationales: dict = Field(default_factory=dict)

        def _call(prompt: str) -> dict:
            res = call_structured_llm_by_provider(
                provider, api_key, model, prompt, _AnalystOut,
                max_output_tokens=512, temperature=0.2,
            )
            if isinstance(res, tuple):
                res = res[0]
            if hasattr(res, "model_dump"):
                return res.model_dump()
            return res if isinstance(res, dict) else {}

        return _call
    except Exception:
        return None


def analyze(features, markets: list[str], *, news: str = "", llm_call=None, adj_cap: float = DEFAULT_ADJ_CAP) -> dict:
    """Returns {adjustments, shortlist, rationales}. The LLM reads `news`
    (injuries/lineups/team news) + the feature bundle. Adjustments are
    hard-clamped to ±adj_cap so the LLM can never move a probability beyond that.
    Any LLM failure degrades to a no-op (empty adjustments)."""
    call = llm_call or _default_llm_call
    prompt = build_prompt(features, markets, news)
    try:
        raw = call(prompt) or {}
    except Exception:
        return {"adjustments": {}, "shortlist": [], "rationales": {}}
    # A loosely-validated LLM may return these fields as non-dict/non-list truthy
    # values (a JSON string, a list, NaN). Coerce defensively so a malformed
    # response degrades to a no-op instead of raising (the documented contract).
    raw_adj = raw.get("adjustments")
    raw_adj = raw_adj if isinstance(raw_adj, dict) else {}
    raw_short = raw.get("shortlist")
    raw_short = raw_short if isinstance(raw_short, list) else []
    raw_rats = raw.get("rationales")
    raw_rats = raw_rats if isinstance(raw_rats, dict) else {}

    adjustments = {}
    for k, v in raw_adj.items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(fv):   # reject NaN/inf so it can't become a max-cap nudge
            continue
        adjustments[k] = clamp_adjustment(fv, adj_cap)
    return {
        "adjustments": adjustments,
        "shortlist": list(raw_short),
        "rationales": {k: str(v) for k, v in raw_rats.items()},
    }
