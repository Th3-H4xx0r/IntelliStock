"""Calling the four roles, and recording exactly what each call cost.

The only module here that talks to a model. Everything it returns is a
`RoleResult` carrying the model id, the prompt hash and the spend, so:

* a bad model is attributable after the fact rather than suspected;
* the MODELS THEMSELVES become A/B-able, which is one of the declared action
  classes; and
* the budget accountant has real numbers instead of estimates.

A role that is not configured does NOT silently fall back to some ambient
default. It returns `configured=False` and the caller skips that role. A
recorded model id that is not the model that answered would make every other
guarantee here a lie.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from self_learning.prompts import prompt_hash
from self_learning.roles import model_id_key, role_config

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class RoleResult:
    role: str
    ok: bool
    text: str
    payload: dict | None
    model: str
    provider: str
    prompt_hash: str
    cost_usd: float
    error: str = ""

    def to_doc(self) -> dict:
        return {
            "role": self.role, "ok": self.ok, "model": self.model,
            "provider": self.provider, "prompt_hash": self.prompt_hash,
            "cost_usd": round(self.cost_usd, 6), "error": self.error,
        }


def extract_json(text):
    """Pull a JSON object out of a model response, or None.

    Models wrap JSON in prose and fences however they like. Returning None on
    failure — rather than a partially-parsed guess — matters because the caller
    treats an unparseable proposal as "no proposal", never as an empty one.
    """
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```[a-zA-Z]*\n?", "", candidate)
        candidate = re.sub(r"\n?```$", "", candidate).strip()
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        pass
    match = _JSON_BLOCK.search(candidate)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError):
        return None


def call_role(role: str, resolved_config: dict, system: str, user: str, *,
              caller=None, expect_json: bool = True) -> RoleResult:
    """Invoke one role. `caller` is injectable so this is testable offline."""
    cfg = role_config(role, resolved_config)
    digest = prompt_hash(f"{system}\n----\n{user}")

    if not cfg.configured:
        return RoleResult(
            role=role, ok=False, text="", payload=None, model=cfg.model,
            provider=cfg.provider, prompt_hash=digest, cost_usd=0.0,
            error=(f"role '{role}' has no model configured — set "
                   f"{model_id_key(role)} in LearningConfig. Falling back to a "
                   f"default would make the recorded model id wrong."))

    if caller is None:                                    # pragma: no cover
        from llm_utils import call_llm_by_provider

        def caller(**kwargs):
            # `provider_config` carries the endpoint/base-url/region keys
            # `model_resolver` injects. Dropping them sent an Ollama or
            # OpenRouter role to whatever the environment defaulted to — for
            # Ollama that is localhost, a documented past outage here.
            #
            # NOTE: `call_llm_by_provider` has no temperature parameter, so a
            # role's temperature is NOT applied on this path. `RoleConfig`
            # still carries it for the structured path and for the record; it
            # is deliberately not silently dropped without a note, because
            # "the judge runs at 0.0" would otherwise be an unenforced claim.
            return call_llm_by_provider(
                provider=kwargs["provider"], api_key=kwargs["api_key"],
                model=kwargs["model"],
                prompt=f"{kwargs['system']}\n\n{kwargs['user']}",
                max_output_tokens=kwargs["max_output_tokens"],
                provider_config=kwargs.get("provider_config") or None)

    try:
        text = caller(provider=cfg.provider, api_key=cfg.api_key,
                      model=cfg.model, system=system, user=user,
                      max_output_tokens=cfg.max_output_tokens,
                      temperature=cfg.temperature,
                      provider_config=cfg.provider_config)
    except Exception as exc:
        return RoleResult(role=role, ok=False, text="", payload=None,
                          model=cfg.model, provider=cfg.provider,
                          prompt_hash=digest, cost_usd=0.0,
                          error=f"{type(exc).__name__}: {exc}")

    text = text if isinstance(text, str) else str(text or "")
    payload = extract_json(text) if expect_json else None
    if expect_json and payload is None:
        return RoleResult(role=role, ok=False, text=text, payload=None,
                          model=cfg.model, provider=cfg.provider,
                          prompt_hash=digest, cost_usd=0.0,
                          error="response contained no parseable JSON object")
    return RoleResult(role=role, ok=True, text=text, payload=payload,
                      model=cfg.model, provider=cfg.provider,
                      prompt_hash=digest, cost_usd=0.0)
