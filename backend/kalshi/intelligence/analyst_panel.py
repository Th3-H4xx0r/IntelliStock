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


def make_llm_call(model_doc: dict | None, log=None, instance_id: str | None = None):  # pragma: no cover - integration
    """Build an llm_call(prompt)->dict bound to a chosen model (a Models-table row
    {provider, model, name, api_key}). Returns None if it can't be built, so the
    analyst degrades to a no-op (the engine still trades on the statistical model).
    `log(msg, color)` (optional) surfaces what's happening — which provider/model
    is bound, each call, and the actual provider error when a call fails (otherwise
    failures are invisible because the analyst swallows them). `instance_id` tags the
    token usage so it shows on the Token Usage page, like regular instances."""
    try:
        from llm_telemetry import llm_call_context as _llm_ctx
    except Exception:
        from contextlib import contextmanager

        @contextmanager
        def _llm_ctx(**_k):
            yield

    def _log(msg, color="white"):
        if log:
            try:
                log(msg, color)
            except Exception:
                pass

    if not model_doc:
        _log("Analyst LLM: no model document — analyst disabled.", "yellow")
        return None
    try:
        from pydantic import BaseModel, Field
        from llm_utils import call_structured_llm_by_provider, resolve_api_key_for_provider

        # The Models table may store the provider under a few different keys, and
        # display names like "AWS Bedrock" must normalize to the dispatch key
        # ("bedrock"). Likewise the model id may live under model/model_id/name.
        provider_raw = (model_doc.get("provider") or model_doc.get("provider_key")
                        or model_doc.get("type") or "").strip()
        provider = _normalize_provider(provider_raw)
        model = (model_doc.get("model") or model_doc.get("model_id")
                 or model_doc.get("model_name") or model_doc.get("name") or "").strip()
        if not provider or not model:
            _log(f"Analyst LLM: missing provider/model (provider={provider_raw!r}, "
                 f"model={model!r}) — analyst disabled.", "yellow")
            return None
        api_key = (model_doc.get("api_key") or "").strip() or resolve_api_key_for_provider(provider)
        provider_config = _provider_config_from_doc(provider, model_doc)
        _log(f"Analyst LLM bound: provider={provider!r} model={model!r} "
             f"api_key={'set' if api_key else 'none(env)'}"
             + (f" config={provider_config}" if provider_config else "") + ".", "white")
        if provider == "bedrock" and not provider_config.get("bedrock_region"):
            _log("Analyst LLM: bedrock has no region (set bedrock_region on the model "
                 "or BEDROCK_REGION/AWS_REGION env) — the call will fail.", "yellow")

        class _AnalystOut(BaseModel):
            adjustments: dict = Field(default_factory=dict)
            shortlist: list = Field(default_factory=list)
            rationales: dict = Field(default_factory=dict)

        def _call(prompt: str) -> dict:
            try:
                # Tag the call so its token usage is attributed to this instance on
                # the Token Usage page. The structured path does NOT auto-record
                # telemetry, so we read the usage it stashes and record it ourselves
                # while still inside the context (which carries the instance_id).
                with _llm_ctx(instance_id=instance_id, strategy="KalshiAnalyst", call_site="analyst"):
                    res = call_structured_llm_by_provider(
                        provider, api_key, model, prompt, _AnalystOut,
                        max_output_tokens=512, temperature=0.2,
                        provider_config=provider_config or None,
                    )
                    try:
                        import llm_utils as _lu
                        from llm_telemetry import record_llm_call as _rec
                        _usage = (getattr(_lu._LAST_STRUCTURED_LLM_CALL, "data", {}) or {}).get("usage") or {}
                        if _usage:
                            _rec(provider=provider, model=model, usage=_usage, ok=True)
                    except Exception:
                        pass
            except Exception as e:
                _log(f"Analyst LLM call FAILED: {type(e).__name__}: {e}", "red")
                return {}
            if isinstance(res, tuple):
                res = res[0]
            if res is None:
                _log("Analyst LLM returned None (provider call failed or unsupported "
                     f"provider {provider!r}) — match priced model-only.", "red")
                return {}
            out = res.model_dump() if hasattr(res, "model_dump") else (res if isinstance(res, dict) else {})
            _log(f"Analyst LLM ok: adjustments={out.get('adjustments')} "
                 f"shortlist={out.get('shortlist')}.", "cyan")
            return out

        return _call
    except Exception as e:
        _log(f"Analyst LLM init failed: {type(e).__name__}: {e}", "red")
        return None


_PROVIDER_ALIASES = {
    "aws bedrock": "bedrock", "aws-bedrock": "bedrock", "amazon bedrock": "bedrock",
    "bedrock converse": "bedrock", "azure openai": "azure", "azure-openai": "azure",
    "open ai": "openai", "claude cli": "claude-cli", "codex cli": "codex-cli",
    "google": "gemini", "google gemini": "gemini",
}


def _provider_config_from_doc(provider: str, model_doc: dict) -> dict:
    """Provider-specific settings call_structured_llm_by_provider needs but that
    aren't (provider, api_key, model). For bedrock the REGION is required — without
    it the call silently returns None. Mirrors graph_nexus_analysis's resolver but
    reads the fields straight off the Models-table row."""
    import os
    d = model_doc or {}
    cfg: dict = {}
    if provider == "bedrock":
        region = (str(d.get("bedrock_region") or "").strip()
                  or os.environ.get("BEDROCK_REGION", "").strip()
                  or os.environ.get("AWS_REGION", "").strip())
        if region:
            cfg["bedrock_region"] = region
        reasoning = str(d.get("bedrock_reasoning") or "").strip()
        if reasoning:
            cfg["bedrock_reasoning"] = reasoning
    elif provider == "azure":
        ep = str(d.get("azure_openai_endpoint") or "").strip()
        ver = str(d.get("azure_openai_api_version") or "").strip()
        if ep:
            cfg["azure_endpoint"] = ep
        if ver:
            cfg["api_version"] = ver
    elif provider == "ollama":
        bu = str(d.get("ollama_base_url") or "").strip()
        if bu:
            cfg["ollama_base_url"] = bu
    elif provider in ("openai", "nvidia"):
        bu = str(d.get("openai_base_url") or d.get("nvidia_base_url") or "").strip()
        if bu:
            cfg[f"{provider}_base_url"] = bu
    return cfg


def _normalize_provider(raw: str) -> str:
    """Map a stored/display provider name to the llm_utils dispatch key."""
    k = " ".join((raw or "").strip().lower().split())
    if k in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[k]
    # "AWS Bedrock / Kimi-K2.5" style composites -> take the leading token group.
    for needle, key in (("bedrock", "bedrock"), ("azure", "azure"), ("gemini", "gemini"),
                        ("deepseek", "deepseek"), ("openai", "openai"), ("ollama", "ollama"),
                        ("nvidia", "nvidia"), ("claude-cli", "claude-cli"), ("codex-cli", "codex-cli")):
        if needle in k:
            return key
    return k


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
