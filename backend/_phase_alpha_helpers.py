"""Phase α (variance-containment) helpers extracted for unit testability.

Both broker.py and graph_nexus_analysis.py call into these so tests can
exercise the exact production logic instead of replicating it inline.

Reference: docs/superpowers/plans/2026-05-18-bt136708-fix-implementation.md §11
"""
from __future__ import annotations

import hashlib
import uuid


_MODEL_EVIDENCE_CACHE_KINDS = frozenset(
    {
        "ordinary_prompt",
        "sentiment",
        "overlay_result",
        "active_event_maintenance",
        "analyst_panel",
        "learning",
        "macro_classification",
    }
)


def _model_evidence_session():
    try:
        from backend.model_evidence import get_model_evidence_session
    except ImportError:
        from model_evidence import get_model_evidence_session
    return get_model_evidence_session()


def evidence_cache_access_allowed(
    cache_kind: str,
    *,
    access: str,
) -> bool:
    """Central read/write policy for mutable caches derived from an LLM."""
    session = _model_evidence_session()
    if session is None or session.mode == "off":
        return True
    try:
        from backend.model_evidence import ModelEvidenceError
    except ImportError:
        from model_evidence import ModelEvidenceError
    if cache_kind not in _MODEL_EVIDENCE_CACHE_KINDS:
        raise ModelEvidenceError(
            f"unknown model-evidence cache kind: {cache_kind}"
        )
    if access not in {"read", "write"}:
        raise ModelEvidenceError(
            f"unknown model-evidence cache access: {access}"
        )
    # Caller config is never proof that an artifact belongs to the sealed
    # fixture. Until the active session exposes verified artifact identities,
    # every evidence mode fails closed for mutable LLM-derived cache access.
    return False


def evidence_cache_read_allowed(cache_kind: str, **_ignored) -> bool:
    """Compatibility wrapper for centralized mutable-cache read policy."""
    return evidence_cache_access_allowed(cache_kind, access="read")


def evidence_cache_write_allowed(cache_kind: str) -> bool:
    """Return whether a mutable LLM-derived cache may be mutated."""
    return evidence_cache_access_allowed(cache_kind, access="write")


def validate_model_evidence_preflight(config: dict) -> None:
    """Reject mutable-cache/non-clean evidence runs before strategy work."""
    session = _model_evidence_session()
    if session is None or session.mode == "off":
        return
    try:
        from backend.model_evidence import ModelEvidenceError
    except ImportError:
        from model_evidence import ModelEvidenceError
    if config.get("nexus_sentiment_cache_force_in_backtest", True) is not False:
        raise ModelEvidenceError("model evidence requires sentiment cache force disabled")
    if config.get("use_sentiment_cache", True) is not False:
        raise ModelEvidenceError("model evidence requires sentiment cache off")
    if bool(config.get("nexus_fast_mode", False)):
        raise ModelEvidenceError("model evidence requires fast cache mode off")
    if bool(config.get("overlay_result_cache_enabled", False)):
        raise ModelEvidenceError("model evidence requires overlay result cache off")
    audit = session.clean_start_audit
    if audit is None:
        raise ModelEvidenceError(
            "model evidence requires a session-bound deterministic clean-start audit"
        )
    missing_scopes = _MODEL_EVIDENCE_CACHE_KINDS - set(
        audit.cleared_scope_identities
    )
    if missing_scopes:
        raise ModelEvidenceError(
            "model evidence clean-start audit is missing required cleared scopes: "
            f"{sorted(missing_scopes)}"
        )


def resolve_use_sentiment_cache(config: dict) -> tuple[bool, bool]:
    """Phase α.1: return (use_sentiment_cache, force_was_applied).

    Forces cache=True in any non-live context unless operator opts out via
    ``nexus_sentiment_cache_force_in_backtest=False``. Live mode (live=True
    AND lookback=False) honors operator config.

    ``force_was_applied`` is True iff the gate flipped operator-False to True.
    Callers can log on that branch without firing on the default-True path.
    """
    use_sentiment_cache = bool(config.get("use_sentiment_cache", True))
    is_live = bool(config.get("_nexus_is_live_mode", False))
    is_lookback = bool(config.get("historical_lookback_mode", False))
    force = bool(config.get("nexus_sentiment_cache_force_in_backtest", True))
    force_applied = False
    if force and (is_lookback or not is_live):
        if not use_sentiment_cache:
            force_applied = True
        use_sentiment_cache = True
    return use_sentiment_cache, force_applied


def resolve_backtest_determinism(is_backtest: bool, environ: dict) -> bool:
    """Phase α (2026-07-23): is deterministic-backtest mode active?

    Deterministic mode makes paired API backtests of the same window+config
    reproducible by (1) routing EVERY structured LLM call (learning summary,
    per-candidate overlay) through the content-hash prompt cache and (2)
    deriving the RNG seed from the universe instead of the per-run backtest id.
    ON by default in backtest mode; an operator can disable it by setting
    ``NEXUS_BACKTEST_DETERMINISM=0`` on the backtest-engine/container env. It is
    NEVER active in live mode (``is_backtest`` gates it), so live trading is
    byte-identical regardless of the env var.
    """
    if not is_backtest:
        return False
    val = str((environ or {}).get("NEXUS_BACKTEST_DETERMINISM", "1")).strip().lower()
    return val not in ("0", "false", "no", "off")


def derive_backtest_seed(
    backtest_row_id,
    symbols,
    *,
    env_seed: str | None = None,
) -> tuple[int, str]:
    """Phase α.3: derive the backtest-mode RNG seed.

    Precedence:
      1. ``env_seed`` (BACKTEST_SEED env var) — operator manual override.
      2. ``sha256(f"{backtest_row_id}|{sorted_universe}")`` masked to 31 bits.
      3. If ``backtest_row_id`` is None AND universe is empty, fall back to
         a UUID-derived seed (so paired no-id ad-hoc runs aren't all
         identical via the constant ``sha256("|")``).
      4. Hard fallback: 12345.

    Returns ``(seed_int, source_description)`` for logging. The numpy global
    RNG must receive ``seed_int & 0xFFFFFFFF`` (uint32-bounded); the caller
    handles that mask. Python ``random`` accepts the full 31-bit int.
    """
    if env_seed is not None and str(env_seed).strip():
        try:
            return int(env_seed), f"BACKTEST_SEED env ({env_seed!r})"
        except (TypeError, ValueError):
            pass  # fall through to derive
    try:
        seed_universe = ",".join(
            sorted(
                str(s).strip().upper()
                for s in (symbols or [])
                if str(s or "").strip()
            )
        )
        has_id = bool(str(backtest_row_id or "").strip())
        has_universe = bool(seed_universe)
        if not has_id and not has_universe:
            # Bug-sweep 2026-05-18 (α.3): without a backtest_row_id OR
            # universe, sha256("|") is constant — all no-id ad-hoc runs
            # would share a seed. Mix a per-process UUID so each run gets
            # a unique seed (not deterministic across processes, but no
            # less so than the BT###### IDs that gate the deterministic
            # path).
            seed_input = f"NO-ID-NO-UNIVERSE|{uuid.uuid4().hex}"
            digest = hashlib.sha256(seed_input.encode("utf-8")).hexdigest()[:16]
            return (
                int(digest, 16) & 0x7FFFFFFF,
                f"uuid fallback (no backtest_id, no universe)",
            )
        seed_input = f"{backtest_row_id or ''}|{seed_universe}"
        digest = hashlib.sha256(seed_input.encode("utf-8")).hexdigest()[:16]
        return (
            int(digest, 16) & 0x7FFFFFFF,
            f"sha256 of backtest_id={backtest_row_id} + universe[{len(symbols or [])}]",
        )
    except Exception as exc:  # pragma: no cover — defensive
        return 12345, f"hard fallback (derive failed: {exc!r})"


def backtest_determinism_env_vars(environ: dict) -> dict:
    """Phase α.3: env vars to forward into a spawned backtest broker container
    for run-to-run determinism.

    ``PYTHONHASHSEED`` must be set before the interpreter starts — the broker
    relies on it for deterministic ``set`` iteration during stock discovery
    (broker.py warns when it is unset). Default it to ``"0"`` so determinism is
    on even when the deployment env omits it; an explicit operator value wins.
    ``BACKTEST_SEED`` is forwarded only when explicitly set (otherwise the
    broker derives one from the backtest id + universe — see derive_backtest_seed).
    """
    out: dict = {}
    ph = (environ.get("PYTHONHASHSEED") or "").strip() or "0"
    out["PYTHONHASHSEED"] = ph
    bt = (environ.get("BACKTEST_SEED") or "").strip()
    if bt:
        out["BACKTEST_SEED"] = bt
    # 2026-07-23: forward the deterministic-backtest kill-switch into the
    # spawned container only when the operator set it explicitly (default is
    # ON, so an unset var already yields deterministic mode in the broker —
    # see resolve_backtest_determinism). Forwarding lets an operator disable
    # determinism from the engine env without a code change.
    det = (environ.get("NEXUS_BACKTEST_DETERMINISM") or "").strip()
    if det:
        out["NEXUS_BACKTEST_DETERMINISM"] = det
    # Greedy LLM decoding inside the spawned backtest container. The prompt
    # cache alone cannot deliver reproducibility: it only helps when the prompt
    # is byte-identical, and the overlay prompt embeds graph analogs and
    # LLM-maintained active events that drift between runs, so in practice the
    # cache missed on essentially every call (each of four replicate runs of
    # the same window wrote ~1.2-1.6k NEW cache rows). With sampling unpinned
    # each miss then drew a fresh sample at temperature 0.2. This var makes
    # llm_utils decode greedily; it is set ONLY here, so live keeps provider
    # defaults. Follows the determinism kill-switch when the operator sets it.
    out["NEXUS_LLM_DETERMINISTIC"] = "0" if det.lower() in (
        "0", "false", "no", "off",
    ) else "1"
    seed = (environ.get("NEXUS_LLM_SEED") or "").strip()
    if seed:
        out["NEXUS_LLM_SEED"] = seed
    return out


def neo4j_snapshot_key(
    query_name: str,
    seeds,
    date_key: str,
    config: dict,
) -> tuple[str, str] | None:
    """Phase α.2: compute the snapshot cache key and granularity.

    Returns ``(cache_key, granularity)`` or ``None`` when caching is
    disabled (``nexus_neo4j_snapshot_granularity="off"``). Extracted so
    both ``_neo4j_cached_query`` and the institutional bg-thread path
    can share keying logic.
    """
    granularity = str(
        config.get("nexus_neo4j_snapshot_granularity", "weekly") or "weekly"
    ).strip().lower()
    if granularity == "off":
        return None
    if granularity == "once":
        week_key = "ONCE"
    else:  # weekly
        try:
            from datetime import date as _date_local
            d = _date_local.fromisoformat((date_key or "")[:10]) if date_key else None
            if d is not None:
                ic = d.isocalendar()
                yr = ic.year if hasattr(ic, "year") else ic[0]
                wk = ic.week if hasattr(ic, "week") else ic[1]
                week_key = f"{yr}-W{wk:02d}"
            else:
                week_key = "UNK"
        except Exception:
            week_key = "UNK"
    if seeds is None:
        seeds_hash = "NONE"
    else:
        try:
            seeds_norm = "|".join(
                sorted(
                    str(s).strip().upper()
                    for s in seeds
                    if str(s or "").strip()
                )
            )
            seeds_hash = hashlib.sha256(seeds_norm.encode("utf-8")).hexdigest()[:16]
        except Exception:
            # Bug-sweep 2026-05-18 (α.2 #3): broken iterable → cache-bypass
            # rather than colliding under a fixed empty hash.
            return None
    return f"{query_name}|{week_key}|{seeds_hash}", granularity
