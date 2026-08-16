"""The four AI roles, and the rules that keep them from grading themselves.

Each role resolves its OWN model through the existing `Models` table, using the
`<prefix>llm_model_id` convention `model_resolver.resolve_model_refs_in_config`
already implements. So a cheap model can narrate while a strong one forms
hypotheses, and the operator changes either from the Models UI without a code
change — and because the model id is recorded on every output, the models
themselves become A/B-able, which is one of the declared action classes.

Three structural rules live here rather than in prose:

* **The judge is not the generator.** Different role, different call, and the
  judge is handed the pre-registered prediction and the measured result — never
  the generator's reasoning. An LLM shown its own argument will agree with it.
* **The statistical verdict is a FLOOR.** The judge may veto a promotion or
  demote, but it cannot promote something the statistics rejected. That keeps
  "LLM as judge" from becoming a way to launder a failed experiment.
* **Rejected hypotheses stay in the generator's context.** Without that it
  re-proposes the entry-gate idea this project already disproved twice.
"""
from __future__ import annotations

from dataclasses import dataclass

ANALYST = "analyst"
GENERATOR = "generator"
CODER = "coder"
JUDGE = "judge"

ROLES = (ANALYST, GENERATOR, CODER, JUDGE)

# The config key each role reads for its model, and its default budget. The
# analyst runs per event and must be cheap; the generator and judge run per
# hypothesis and are worth a strong model.
ROLE_SPECS = {
    ANALYST: {
        "config_prefix": "learning_analyst_",
        "max_output_tokens": 1500,
        "temperature": 0.2,
        "purpose": "explain what happened, in plain language, from the numbers",
    },
    GENERATOR: {
        "config_prefix": "learning_generator_",
        "max_output_tokens": 2000,
        "temperature": 0.6,   # some spread: this is the role that must invent
        "purpose": "propose WHAT to change and WHY, with a predicted direction",
    },
    CODER: {
        "config_prefix": "learning_coder_",
        "max_output_tokens": 8000,
        "temperature": 0.1,
        "purpose": "write the patch for a confirmed hypothesis",
    },
    JUDGE: {
        "config_prefix": "learning_judge_",
        "max_output_tokens": 1200,
        "temperature": 0.0,   # a judge should not be creative
        "purpose": "confirm, hold, demote or veto — never promote past the stats",
    },
}


@dataclass(frozen=True)
class RoleConfig:
    role: str
    provider: str
    model: str
    api_key: str
    max_output_tokens: int
    temperature: float
    configured: bool

    def to_doc(self) -> dict:
        # Never serialize the key.
        return {
            "role": self.role, "provider": self.provider, "model": self.model,
            "max_output_tokens": self.max_output_tokens,
            "temperature": self.temperature, "configured": self.configured,
        }


def model_id_key(role: str) -> str:
    """The LearningConfig key holding this role's Models-table id."""
    spec = ROLE_SPECS.get(role)
    if not spec:
        raise ValueError(f"unknown role: {role}")
    return f"{spec['config_prefix']}llm_model_id"


def role_config(role: str, resolved_config: dict) -> RoleConfig:
    """Read one role's model out of an ALREADY-resolved config.

    Resolution (id -> provider/model/key) is `model_resolver`'s job; this only
    reads the result, which keeps this module pure and testable without a
    database.
    """
    spec = ROLE_SPECS.get(role)
    if not spec:
        raise ValueError(f"unknown role: {role}")
    prefix = spec["config_prefix"]
    config = resolved_config or {}
    provider = str(config.get(f"{prefix}llm_provider") or "").strip().lower()
    model = str(config.get(f"{prefix}llm_model") or "").strip()
    api_key = str(config.get(f"{prefix}llm_api_key") or "")
    return RoleConfig(
        role=role, provider=provider, model=model, api_key=api_key,
        max_output_tokens=int(config.get(f"{prefix}max_output_tokens")
                              or spec["max_output_tokens"]),
        temperature=float(config.get(f"{prefix}temperature")
                          if config.get(f"{prefix}temperature") is not None
                          else spec["temperature"]),
        # A role with no provider or model is NOT configured. Silently falling
        # back to some ambient default would make the recorded model id a lie,
        # and the whole point is that every output is attributable.
        configured=bool(provider and model),
    )


def all_role_configs(resolved_config: dict) -> dict:
    return {role: role_config(role, resolved_config) for role in ROLES}


def unconfigured_roles(resolved_config: dict) -> list:
    return [role for role, cfg in all_role_configs(resolved_config).items()
            if not cfg.configured]
