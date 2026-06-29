"""Single source of truth for the two ``graph_nexus_analysis`` boot identities.

Changing an instance's LLM model alters two independent identities, each with a
distinct consequence on the next live boot:

* ``live_config_hash`` (16-char) -- the snapshot-reuse identity. The live-boot
  snapshot lookup (``strategy_cache_persistence.load_with_fallback``) keys on it;
  a mismatch returns ``no_match`` and forces a historic lookback.
* ``history_scope_id`` (24-char) -- the cleanup identity. The config-change
  cleanup marker (``GraphNexusLearningCache`` row ``cleanup_done|<instance_id>``)
  compares it; a mismatch fires the destructive per-instance cleanup.

Both the broker boot path and the "preserve history" re-stamp feature import
these functions so a re-stamped row can never carry an identity that boot would
not match. This module deliberately has **no** dependency on ``broker`` -- it
only touches leaf helpers (``llm_utils``, ``broker_snapshot_helpers``,
``strategy_cache_persistence``).
"""

from __future__ import annotations

import hashlib
import json

from llm_utils import llm_model_reference, normalize_reasoning_effort
from broker_snapshot_helpers import (
    _collect_prompt_versions,
    _collect_llm_stages,
    _collect_history_scope_inputs,
)
import strategy_cache_persistence as _scp

# Default learning-window length used by boot when the config omits it.
DEFAULT_LOOKBACK_LEARNING_DAYS = 120
# The only strategy that participates in the snapshot/lookback machinery.
NEXUS_STRATEGY_NAME = "graph_nexus_analysis"


def live_config_hash(
    resolved_cfg: dict,
    *,
    strategy_name: str = NEXUS_STRATEGY_NAME,
    lookback_days_default: int = DEFAULT_LOOKBACK_LEARNING_DAYS,
) -> str:
    """Compute the 16-char snapshot-reuse hash for a *resolved* strategy config.

    Mirrors the broker boot computation exactly (broker.py snapshot-load block):
    the config must already have ``*_llm_model_id`` refs resolved to inline
    ``*_llm_provider`` / ``*_llm_model`` values, and live overrides applied, so
    the hash matches a live boot.
    """
    cfg = resolved_cfg if isinstance(resolved_cfg, dict) else {}
    return _scp._compute_config_hash({
        "strategy_name": strategy_name,
        "prompt_versions": _collect_prompt_versions(cfg),
        "llm_stages": _collect_llm_stages(cfg),
        "history_scope_id_inputs": _collect_history_scope_inputs(cfg),
        "lookback_learning_days": int(
            cfg.get("lookback_learning_days", lookback_days_default) or lookback_days_default
        ),
    })


def history_scope_doc(settings: dict) -> dict:
    """Build the canonical doc whose hash is the ``history_scope_id``.

    Moved verbatim from ``broker._nexus_history_scope_doc``. Includes per-role
    provider/model/prompt so a model change changes the scope id; deliberately
    excludes trading-rule keys (those flow through the cleanup marker's
    ``trading_config_hash`` instead).
    """
    settings = settings if isinstance(settings, dict) else {}

    def _role_provider(prefix):
        return str(
            settings.get(f"{prefix}llm_provider")
            or settings.get("llm_provider")
            or ""
        ).strip().lower()

    def _role_reasoning(prefix):
        return normalize_reasoning_effort(
            settings.get(f"{prefix}llm_reasoning_effort")
            or settings.get("llm_reasoning_effort")
            or ""
        )

    def _role_model(prefix):
        model = str(
            settings.get(f"{prefix}llm_model")
            or settings.get("llm_model")
            or ""
        ).strip()
        return llm_model_reference(model, _role_reasoning(prefix))

    def _role_prompt(prefix):
        if prefix:
            return str(
                settings.get(f"{prefix}llm_prompt_version")
                or settings.get("llm_prompt_version")
                or ""
            ).strip()
        return str(settings.get("llm_prompt_version") or "").strip()

    return {
        "scope_version": "nexus_history_model_scope_v3",
        "root_provider": _role_provider(""),
        "root_model": _role_model(""),
        "root_prompt_version": _role_prompt(""),
        "company_provider": _role_provider("company_article_"),
        "company_model": _role_model("company_article_"),
        "company_prompt_version": _role_prompt("company_article_"),
        "macro_provider": _role_provider("macro_article_"),
        "macro_model": _role_model("macro_article_"),
        "macro_prompt_version": _role_prompt("macro_article_"),
        "event_provider": _role_provider("event_maintenance_"),
        "event_model": _role_model("event_maintenance_"),
        "event_prompt_version": _role_prompt("event_maintenance_"),
        "overlay_provider": _role_provider("overlay_"),
        "overlay_model": _role_model("overlay_"),
        "overlay_prompt_version": _role_prompt("overlay_"),
        "azure_endpoint": str(settings.get("azure_openai_endpoint") or "").strip(),
        "azure_api_version": str(settings.get("azure_openai_api_version") or "").strip(),
        "openai_base_url": str(settings.get("openai_base_url") or "").strip(),
        "use_toon_format": bool(settings.get("use_toon_format", True)),
        "graph_feature_version": str(settings.get("graph_feature_version") or "nexus_hybrid_v1"),
        "history_scope_salt": str(settings.get("history_scope_salt") or "").strip(),
        "max_daily_alpaca_articles": int(settings.get("max_daily_alpaca_articles", 50)),
        "max_daily_google_news_articles": int(settings.get("max_daily_google_news_articles", 50)),
        "num_articles_for_llm": int(settings.get("num_articles_for_llm", 30)),
        "min_articles": int(settings.get("min_articles", 20)),
        "google_news_enabled": bool(settings.get("google_news_enabled", True)),
    }


def history_scope_id(settings: dict) -> str:
    """Compute the 24-char history-scope id (cleanup identity).

    Moved verbatim from ``broker._nexus_history_scope_id``. An explicit
    ``history_scope_id`` in the config wins (used by tests / pinned scopes).
    """
    settings = settings if isinstance(settings, dict) else {}
    explicit = str(settings.get("history_scope_id") or "").strip()
    if explicit:
        return explicit
    scope_doc = history_scope_doc(settings)
    return hashlib.sha256(
        json.dumps(scope_doc, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]
