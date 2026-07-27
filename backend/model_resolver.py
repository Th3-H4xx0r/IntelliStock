"""
Runtime resolver for model_id references in strategy configs.

When a strategy config contains keys like ``llm_model_id`` or
``sentiment_llm_model_id``, this module looks up the corresponding
document in the RethinkDB ``Models`` table and injects the credentials
as inline config fields.  Downstream strategy code continues to read
``config.get("llm_provider")`` etc. unchanged — the resolution is
transparent.
"""

import os
import threading
import time

from rethinkdb import RethinkDB

r = RethinkDB()
DB_NAME = "IntelliStock"
RETHINKDB_HOST = os.environ.get("RETHINKDB_HOST", "localhost")
RETHINKDB_PORT = int(os.environ.get("RETHINKDB_PORT", "28015"))

# Thread-safe in-memory cache with TTL
_model_cache: dict = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # seconds


def _get_conn():
    return r.connect(host=RETHINKDB_HOST, port=RETHINKDB_PORT)


def _get_model_from_cache_or_db(conn, model_id: str):
    """Fetch a model doc, using the in-memory cache when fresh."""
    now = time.time()
    with _cache_lock:
        cached = _model_cache.get(model_id)
        if cached and (now - cached["ts"]) < _CACHE_TTL:
            return cached["doc"]
    try:
        doc = r.db(DB_NAME).table("Models").get(model_id).run(conn)
    except Exception:
        return None
    if doc:
        with _cache_lock:
            _model_cache[model_id] = {"doc": doc, "ts": now}
    return doc


def invalidate_model_cache(model_id: str | None = None):
    """Drop cached model(s) so the next resolution fetches fresh data."""
    with _cache_lock:
        if model_id:
            _model_cache.pop(model_id, None)
        else:
            _model_cache.clear()


def _emit_log(msg: str) -> None:
    """Best-effort log line for resolver activity. Routed through the
    broker's logger when available so the message shows up alongside
    "[BROKER] Resolved model_id references…"; otherwise dropped silently
    (e.g. during unit tests where no broker is wired up)."""
    try:
        import sys
        # Print to stderr so the broker's tail picks it up. The broker
        # itself uses a similar emit style for its "[BROKER]" lines.
        print(f"[ModelResolver] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def resolve_model_refs_in_config(conn, config: dict, *, force_refresh: bool = False) -> dict:
    """
    Find all ``*_llm_model_id`` keys in *config*, look up each from the
    Models table, and inject the model's credentials as inline fields.

    Returns a **new** dict — the original is not mutated.
    If a model_id cannot be resolved (deleted / missing), the key is
    silently skipped and the strategy falls through to environment
    variables / provider defaults.

    When ``force_refresh`` is True, the per-module 5-minute doc cache
    is dropped before resolution so the DB row is always re-read.
    Use this at backtest-process spawn / live-broker startup so a key
    edit via the Models UI propagates without waiting for the TTL.
    """
    if not config:
        return config

    # Fast path: skip if no model_id keys exist
    model_keys = [k for k in config if k.endswith("llm_model_id")]
    if not model_keys:
        _emit_log(
            "No *_llm_model_id keys found in this strategy config — "
            "nothing to resolve. If you set a model via the LLM picker "
            "but the strategy is still using the old provider, the "
            "picker change was not saved (use the top-level Save button "
            "on the strategy/instance after picking)."
        )
        return config

    if force_refresh:
        invalidate_model_cache()
    _emit_log(
        f"Resolving {len(model_keys)} *_llm_model_id key(s): "
        f"{', '.join(sorted(model_keys))}"
    )

    resolved = dict(config)
    for key in model_keys:
        model_id = (resolved[key] or "").strip()
        if not model_id:
            _emit_log(f"  {key}: empty value — skipped")
            continue
        prefix = key[: -len("llm_model_id")]  # e.g. "sentiment_" or ""
        model_doc = _get_model_from_cache_or_db(conn, model_id)
        if model_doc is None:
            _emit_log(
                f"  {key}={model_id}: NOT FOUND in Models table — "
                f"strategy will fall through to env / inline values "
                f"(was the Models row deleted?)"
            )
            continue
        # Models.api_key is encrypted at rest.  Keep decrypt()'s legacy
        # plaintext compatibility while old rows await the migration, but
        # never inject Fernet ciphertext into a strategy configuration.
        model_doc = dict(model_doc)
        if model_doc.get("api_key"):
            from secret_store import decrypt
            model_doc["api_key"] = decrypt(model_doc["api_key"])
        _emit_log(
            f"  {key}={model_id}: resolved → provider="
            f"{(model_doc.get('provider') or '').strip().lower()!r} "
            f"model={(model_doc.get('model') or '').strip()!r}"
        )

        provider = (model_doc.get("provider") or "").strip().lower()

        # Build field mapping: model_doc field → strategy config key.
        # ``provider`` and ``api_key`` are ALWAYS overwritten (even with
        # empty) so a previously-saved stale role entry can't linger.
        # Without that, switching from Azure → Ollama at this role left
        # the old Azure api_key in place and the call routed through
        # the wrong provider via fallback. Other fields preserve the
        # "only-set-if-non-empty" semantics so non-applicable provider
        # fields (e.g. azure_endpoint on an Ollama row) don't clobber
        # legitimate inline values elsewhere.
        always_overwrite = {"provider", "api_key"}
        field_map = {
            "provider": f"{prefix}llm_provider",
            "model": f"{prefix}llm_model" if prefix else "model_name",
            "api_key": f"{prefix}llm_api_key",
            "openai_base_url": f"{prefix}openai_base_url",
            "nvidia_base_url": f"{prefix}nvidia_base_url",
            "azure_openai_endpoint": f"{prefix}azure_openai_endpoint",
            "azure_openai_api_version": f"{prefix}azure_openai_api_version",
            "reasoning_effort": f"{prefix}llm_reasoning_effort",
            # Ollama-specific propagation. Without these the dispatcher
            # didn't see the operator's ollama_base_url / keep_alive /
            # think settings even when the Models row had them — the
            # strategy fell back to OLLAMA_BASE_URL env (= localhost)
            # which is wrong for any remote Ollama host.
            "ollama_base_url": f"{prefix}ollama_base_url",
            "ollama_keep_alive": f"{prefix}ollama_keep_alive",
            "ollama_think": f"{prefix}ollama_think",
            # Bedrock-specific propagation: region is required and reasoning
            # is the bedrock equivalent of ollama_think. Not in
            # always_overwrite — only set when the row has them, so a
            # non-bedrock row never injects empty bedrock keys.
            "bedrock_region": f"{prefix}bedrock_region",
            "bedrock_reasoning": f"{prefix}bedrock_reasoning",
            # OpenRouter-specific propagation: base_url + optional attribution
            # headers (HTTP-Referer / X-Title). Not in always_overwrite — only
            # set when the row has them, so a non-openrouter row never injects
            # empty openrouter keys.
            "openrouter_base_url": f"{prefix}openrouter_base_url",
            "openrouter_referer": f"{prefix}openrouter_referer",
            "openrouter_title": f"{prefix}openrouter_title",
            # Cache-grouping override (canonical_model_cache_key).
            "model_cache_family": f"{prefix}model_cache_family",
        }

        for doc_field, config_key in field_map.items():
            val = (model_doc.get(doc_field) or "").strip()
            if val:
                resolved[config_key] = val
            elif doc_field in always_overwrite:
                # Empty value from the Models row is meaningful for
                # provider/api_key — set it explicitly to "" so the
                # strategy doesn't keep using a stale value.
                resolved[config_key] = ""

        # Azure uses a separate api key field too
        if provider == "azure":
            api_key = (model_doc.get("api_key") or "").strip()
            if api_key:
                resolved[f"{prefix}azure_openai_api_key"] = api_key

        # claude-cli uses the locally-installed `claude` binary instead of
        # an API key. Inject cli_path and extra_args under the same
        # ``{prefix}…`` naming convention so strategies can read them.
        if provider == "claude-cli":
            cli_path = (model_doc.get("cli_path") or "claude").strip() or "claude"
            resolved[f"{prefix}cli_path"] = cli_path
            extra_args = model_doc.get("extra_args")
            if isinstance(extra_args, str):
                resolved[f"{prefix}extra_args"] = extra_args.strip()
            elif isinstance(extra_args, (list, tuple)):
                resolved[f"{prefix}extra_args"] = list(extra_args)
        # codex-cli uses the locally-installed `codex` binary; same shape
        # as claude-cli, default cli_path is "codex".
        if provider == "codex-cli":
            cli_path = (model_doc.get("cli_path") or "codex").strip() or "codex"
            resolved[f"{prefix}cli_path"] = cli_path
            extra_args = model_doc.get("extra_args")
            if isinstance(extra_args, str):
                resolved[f"{prefix}extra_args"] = extra_args.strip()
            elif isinstance(extra_args, (list, tuple)):
                resolved[f"{prefix}extra_args"] = list(extra_args)

        # Also set the alternative model key for the default group
        if not prefix and model_doc.get("model"):
            resolved["llm_model"] = model_doc["model"].strip()

    return resolved
