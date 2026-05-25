"""Phase α (variance-containment) helpers extracted for unit testability.

Both broker.py and graph_nexus_analysis.py call into these so tests can
exercise the exact production logic instead of replicating it inline.

Reference: docs/superpowers/plans/2026-05-18-bt136708-fix-implementation.md §11
"""
from __future__ import annotations

import hashlib
import uuid


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
