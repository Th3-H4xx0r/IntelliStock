"""Persist per-strategy in-memory cache across broker restarts.

2026-04-23: Live-trading bug — `buys=0 sells=12 holds=87` — root-caused to
`strategy_cache["_deployment_bar_index"]` (and sibling keys like
`_portfolio_drawdown_state`, `_v28_hold_trim_cooldown`, `_sold_cooldown`)
living in a module-level dict in `broker.py`. Every container restart
wiped the cache, which reset the deployment ramp to bar=1 (50% cap),
which left `ramp_room=0` once the portfolio MTM grew past 50% of initial
equity. Backtest was unaffected because backtest never restarts mid-run.

This module persists the dict to a RethinkDB table so live mode survives
restarts. Strategy code is NOT modified — the broker just loads the cache
before calling `run_run_once_strategies` and saves it after.

Design:
- Single table `NexusStrategyCache` keyed `id = "<instance_id>|<strategy_name>"`.
- Live mode only (caller must gate).
- Blacklist transient keys (`_llm_*`, `_trace_*`, `_prompt_*`, `_last_prompt*`)
  so LLM trace/prompt buffers don't bloat the row. Persist everything else.
- Cap each dict-of-cooldowns to the most recent 500 entries before saving.
- Rows older than 14 days are considered stale and ignored on load.
- Fail-open on ANY error (corrupt JSON, schema drift, Rethink outage) — the
  broker continues with the empty cache it would have had today.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

TABLE_NAME = "NexusStrategyCache"
DB_NAME = "IntelliStock"
_COMPOUND_INDEX_NAME = "instance_id_config_hash"

_BLACKLIST_PREFIXES = (
    "_llm_",
    "_trace_",
    "_prompt_",
    "_last_prompt",
    "_raw_llm_",
    "_debug_",
    # bug-sweep 2026-04-23: large transient bulk payloads that the strategy
    # regenerates each tick. Persisting them bloats the cache blob without
    # improving correctness across restarts.
    "_bz_bulk_",
    "_overlay_bars_raw",
    "_overlay_bars_range",
    # Tier-3 Phase 3 (2026-05-17): observation-only telemetry buffer. Grows
    # up to 2000 list-of-dicts entries during run_once evaluations and is
    # consumed offline for post-hoc analysis. Persisting bloats the cache
    # row by ~100KB+ per account without improving correctness.
    "_nexus_conviction_telemetry",
    "_nexus_conviction_telemetry_capped_logged",
    # BT136708 P1.7 (2026-05-18): A4 in-memory post_sell_watch mirror is a
    # per-backtest-run construct; persisting it across restarts would carry
    # stale exit prices into a fresh backtest. The mcap pre-seed marker is
    # similarly per-run.
    # Phase γ.1 (2026-05-18, BT232179 follow-up): the bool flag
    # `_yf_market_cap_cache_preseeded` is replaced by a set[str]
    # `_yf_market_cap_cache_preseeded_tickers`. Both prefixes are explicit
    # here for clarity even though the legacy bool form would also match
    # via prefix; keep both for one release while live state migrates.
    "_post_sell_watch_inmem",
    "_yf_market_cap_cache_preseeded",
    "_yf_market_cap_cache_preseeded_tickers",
    # Phase δ (2026-05-18, BT232179 follow-up): one-shot audit-log flag.
    # Persisting would suppress the audit log on restart — operators want
    # to re-see the resolved sentiment_cache_scope_id every fresh run.
    "_sentiment_cache_scope_audit_emitted",
    # Phase ε.C.4 (2026-05-19, BT294837 follow-up): per-run held-ETF tracker
    # used by the ETF allocation cap enforcement. The set is reconciled
    # against live portfolio_emulator._positions each bar so it stays
    # consistent across restarts even without persistence, but adding
    # to the blacklist is safer than relying on the reconciliation alone.
    "_nexus_held_etfs",
    # Phase α.2 (BT109429 follow-up, 2026-05-18): Neo4j query snapshot
    # for variance containment. Per-backtest-run; persisting would mix
    # snapshots across runs with different universes and dates. The
    # stats counters are also per-run telemetry.
    "_neo4j_snapshot",
    "_neo4j_snapshot_stats",
    # Phase η (2026-05-20): per-run sector map cache for η.B'/η.G.
    # Sectors don't change mid-backtest, but a fresh run should re-fetch
    # via Neo4j IN_SECTOR. Persisting would carry stale sector
    # assignments across backtest restarts.
    "_eta_sector_map",
    # Phase 1 bug-sweep (2026-05-21): per-run cache keys discovered by the
    # blacklist audit. Each is regenerated every tick / bar / decision cycle
    # from current portfolio state, so persisting stale copies across a
    # restart would feed the new run incorrect universe / sizing data.
    "_inst_co_holdings_",       # per-instrument company-holdings cache, derived from current positions
    "_active_watchlist_",       # filtered active watchlist; should reflect this run's universe
    "_mw_rotation_sells_",      # rotation-sell candidates computed this cycle
    "_mw_sized_",               # sized order book this cycle (depends on current cash)
    "_mw_free_cash_",           # free-cash snapshot used by this cycle's sizing
    "_overlay_no_data_",        # negative-result cache for overlay lookups, per-symbol per-run
    "_momentum_ranked_cache",   # ranked-momentum table, recomputed each run from fresh data
    # Point-in-time boundary handles (2026-08-02). The context is a frozen
    # dataclass and the resolver is a bare function — both coerce to None and
    # would be silently dropped anyway, but a persisted decision boundary is
    # exactly the thing that must never survive into a run with a different
    # as_of. The cutoff memo is keyed by (date, as_of) tuples, so it is
    # per-bar by construction and stringifying those keys would only store junk.
    "_point_in_time_context",
    "_point_in_time_session_close_resolver",
    "_point_in_time_fundamentals",
    "_overlay_daily_cutoff_memo",
)

_MAX_DICT_ENTRIES = 500
_ROW_TTL_SEC = 14 * 24 * 3600


#: Keys whose loss CHANGES WHAT THE BOT DOES WITH MONEY on the next boot, rather
#: than merely costing it a recomputation. The oversize-eviction loop below sorts
#: by size and pops until the blob fits; these are tiny, so sort order has been
#: evicting them last BY ACCIDENT. That is not a guarantee, and the failure is
#: silent — which is the part that matters, because each of these fails in the
#: expensive direction:
#:
#:   _peak_*      per-position peak for the trailing stop. Lost, the peak
#:                re-bases to the current price on restart
#:                (`_resolve_position_peak_state`) and every point of accumulated
#:                downside protection on a winner disappears.
#:   *cooldown*   re-entry suppression. Lost, the bot can immediately re-buy a
#:                name it just sold — and turnover is this system's known leak
#:                (~290%/mo live against ~50%/mo break-even).
#:   *latch*      regime hysteresis. Lost, the regime can flap on the first bar
#:                after a restart and re-run a whole allocation.
#:   _dd_kill_*   drawdown-circuit episode state. Lost, a kill that already fired
#:                can re-arm and liquidate the book a second time.
_RISK_CRITICAL_MARKERS = ("_peak_", "cooldown", "latch", "_dd_kill_")


def _is_risk_critical(key: str) -> bool:
    """True when losing `key` across a restart changes a money decision."""
    if not isinstance(key, str):
        return False
    return any(m in key for m in _RISK_CRITICAL_MARKERS)


def _is_blacklisted(key: str) -> bool:
    if not isinstance(key, str):
        return False
    for prefix in _BLACKLIST_PREFIXES:
        if key.startswith(prefix):
            return True
    return False


def _truncate_dict(d: dict, cap: int = _MAX_DICT_ENTRIES) -> dict:
    if not isinstance(d, dict) or len(d) <= cap:
        return d
    items = list(d.items())[-cap:]
    return dict(items)


def _coerce_for_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    # bug-sweep: Decimal has no .isoformat and falls through isinstance checks;
    # previously returned None (silently dropped). Financial math occasionally
    # surfaces Decimals via price-string parsing.
    try:
        from decimal import Decimal
        if isinstance(value, Decimal):
            return float(value)
    except Exception:
        pass
    if isinstance(value, set):
        return {"__set__": sorted(_coerce_for_json(v) for v in value if _coerce_for_json(v) is not None)}
    if isinstance(value, (list, tuple)):
        return [_coerce_for_json(v) for v in value]
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if not isinstance(k, str):
                try:
                    k = str(k)
                except Exception:
                    continue
            coerced = _coerce_for_json(v)
            if coerced is None and v is not None:
                continue
            out[k] = coerced
        return _truncate_dict(out)
    try:
        if hasattr(value, "isoformat"):
            return {"__iso__": value.isoformat()}
    except Exception:
        pass
    return None


def _decode_json(value: Any) -> Any:
    if isinstance(value, dict):
        if "__set__" in value and len(value) == 1:
            return set(value["__set__"] or [])
        if "__iso__" in value and len(value) == 1:
            try:
                import datetime
                return datetime.datetime.fromisoformat(str(value["__iso__"]))
            except Exception:
                return value["__iso__"]
        return {k: _decode_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode_json(v) for v in value]
    return value


def _canonical_json(value: Any) -> str:
    """Deterministic JSON encoding: sorted keys, no whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _compute_config_hash(config: dict) -> str:
    """Compute a stable 16-char SHA256 hex of the behaviorally-significant
    portion of the strategy config.

    Fields included (all others are considered behaviorally neutral):
    - strategy_name
    - prompt_versions (all roles)
    - llm_stages (provider/model/effort per stage)
    - history_scope_id_inputs (neo4j_uri, neo4j_user, sentiment_cache_scope_salt,
      use_toon_format, num_articles_for_llm)
    - lookback_learning_days
    """
    if not isinstance(config, dict):
        return "invalid"
    canonical = {
        "strategy_name": config.get("strategy_name", ""),
        "prompt_versions": config.get("prompt_versions", {}),
        "llm_stages": config.get("llm_stages", {}),
        "history_scope_id_inputs": config.get("history_scope_id_inputs", {}),
        "lookback_learning_days": config.get("lookback_learning_days", 0),
    }
    blob = _canonical_json(canonical).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _compute_module_hash(file_path: str) -> str:
    """16-char SHA256 hex of file bytes. Returns 'missing' if file not readable.

    Used to detect strategy code changes between the time a snapshot was
    written and the time it's being loaded.

    Phase 1 bug-sweep (2026-05-21): normalize CRLF -> LF so the hash is
    OS-agnostic. A Windows-checkout backtest and a Linux-deploy live host
    must agree on the hash of the same logical file. Without this, the
    live boot's load_with_fallback returns ``module_drift`` and falls back
    to the full lookback even when the strategy code is byte-identical
    apart from line endings.
    """
    try:
        with open(file_path, "rb") as fh:
            data = fh.read()
    except Exception:
        return "missing"
    data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()[:16]


def _serialize_cache_for_blob(cache: dict) -> str:
    """JSON-encode `cache` for storage in NexusStrategyCache.cache_json.

    Drops blacklisted keys (see _BLACKLIST_PREFIXES) and records dropped
    key names under `__skipped_fields__` so future readers can tell what
    was intentionally omitted.
    """
    if not isinstance(cache, dict):
        return "{}"
    filtered: dict = {}
    skipped: list = []
    for k, v in cache.items():
        if not isinstance(k, str):
            continue
        if _is_blacklisted(k):
            skipped.append(k)
            continue
        coerced = _coerce_for_json(v)
        if coerced is None and v is not None:
            skipped.append(k)
            continue
        filtered[k] = coerced
    if skipped:
        filtered["__skipped_fields__"] = sorted(set(skipped))
    return json.dumps(filtered, default=str)


def _deserialize_cache_from_blob(blob: str) -> dict:
    """Decode a cache_json blob written by _serialize_cache_for_blob.

    Raises ValueError on malformed JSON. Returns {} for empty input.
    """
    if not blob:
        return {}
    try:
        raw = json.loads(blob)
    except (ValueError, TypeError) as e:
        raise ValueError(f"corrupt cache blob: {e}") from e
    if not isinstance(raw, dict):
        return {}
    return _decode_json(raw)


def _ensure_table(conn, r) -> bool:
    """Ensure NexusStrategyCache exists with the compound secondary index
    required for load_with_fallback's get_all query.

    Returns True if the table+index are usable, False on any error.
    """
    try:
        tables = list(r.db(DB_NAME).table_list().run(conn))
    except Exception:
        return False
    if TABLE_NAME not in tables:
        try:
            r.db(DB_NAME).table_create(TABLE_NAME).run(conn)
        except Exception:
            return False
    try:
        existing_indexes = list(r.db(DB_NAME).table(TABLE_NAME).index_list().run(conn))
    except Exception:
        return False
    if _COMPOUND_INDEX_NAME not in existing_indexes:
        try:
            r.db(DB_NAME).table(TABLE_NAME).index_create(
                _COMPOUND_INDEX_NAME,
                lambda doc: [doc["instance_id"], doc["config_hash"]],
            ).run(conn)
            r.db(DB_NAME).table(TABLE_NAME).index_wait(_COMPOUND_INDEX_NAME).run(conn)
        except Exception:
            # If create fails (e.g., existing rows lack the fields), the load
            # query will fall back to None; persistent failures will be
            # visible via the load_with_fallback "db_error" path.
            return False
    return True


def load_strategy_cache_from_db(
    conn,
    r,
    instance_id: str,
    strategy_name: str,
) -> Optional[dict]:
    """Return persisted cache dict for `(instance_id, strategy_name)` or None.

    Never raises — fail-open on any error. Caller treats None as "start with
    empty cache".
    """
    if conn is None or r is None or not instance_id or not strategy_name:
        return None
    row_id = f"{instance_id}|{strategy_name}"
    try:
        if not _ensure_table(conn, r):
            return None
        row = r.db(DB_NAME).table(TABLE_NAME).get(row_id).run(conn)
        if not row:
            return None
        updated_at = row.get("updated_at_epoch") or 0.0
        if updated_at and (time.time() - float(updated_at)) > _ROW_TTL_SEC:
            return None
        blob = row.get("cache_json") or ""
        if not blob:
            return None
        raw = json.loads(blob)
        if not isinstance(raw, dict):
            return None
        return _decode_json(raw)
    except Exception:
        return None


def save_strategy_cache_to_db(
    conn,
    r,
    instance_id: str,
    strategy_name: str,
    cache: dict,
    *,
    max_blob_bytes: int = 1_000_000,
    config_hash: Optional[str] = None,
    module_hash: Optional[str] = None,
    end_date: Optional[str] = None,
) -> bool:
    """Upsert `cache` dict for `(instance_id, strategy_name)`.

    Phase 1: if config_hash + module_hash + end_date are all provided, writes
    with the new 5-segment PK and origin="live" so the snapshot loader can
    pick it up on next boot. Otherwise writes with the legacy 2-segment PK
    (back-compat for callers that haven't been updated yet).
    """
    if conn is None or r is None or not instance_id or not strategy_name:
        return False
    if not isinstance(cache, dict):
        return False

    use_new_schema = bool(config_hash and module_hash and end_date)
    if use_new_schema:
        row_id = f"{instance_id}|{strategy_name}|{config_hash}|live|{end_date}"
    else:
        row_id = f"{instance_id}|{strategy_name}"

    try:
        filtered = {
            k: _coerce_for_json(v)
            for k, v in cache.items()
            if not _is_blacklisted(k)
        }
        filtered = {k: v for k, v in filtered.items() if v is not None}
        blob = json.dumps(filtered, default=str)
        if len(blob) > max_blob_bytes:
            # Evict expendable keys first and risk-critical ones only as a last
            # resort. Previously this was one size-sorted pass over everything:
            # the risk keys survived only because they happen to be small, and
            # nothing said so or noticed when they did not.
            sized = sorted(
                filtered.items(),
                key=lambda kv: len(json.dumps(kv[1], default=str)),
                reverse=True,
            )
            expendable = [k for k, _ in sized if not _is_risk_critical(k)]
            critical = [k for k, _ in sized if _is_risk_critical(k)]
            evicted, evicted_critical = [], []
            for k in expendable + critical:
                if len(blob) <= max_blob_bytes:
                    break
                filtered.pop(k, None)
                evicted.append(k)
                if _is_risk_critical(k):
                    evicted_critical.append(k)
                blob = json.dumps(filtered, default=str)
            # Silent truncation reads as "everything was saved". Say what went.
            if evicted:
                try:
                    from intellistock_logger import intellistock_logger
                    intellistock_logger.log(
                        f"strategy cache over {max_blob_bytes} bytes for "
                        f"{instance_id}/{strategy_name}: evicted {len(evicted)} key(s)"
                        + (f" — INCLUDING {len(evicted_critical)} RISK-CRITICAL "
                           f"{evicted_critical[:8]}; trailing-stop peaks, cooldowns "
                           f"or regime latches will be REBUILT FROM SCRATCH on the "
                           f"next boot" if evicted_critical else ""),
                        "red" if evicted_critical else "yellow",
                        service="STRATEGY_CACHE_PERSISTENCE",
                    )
                except Exception:
                    pass
        if not _ensure_table(conn, r):
            return False
        row = {
            "id": row_id,
            "instance_id": instance_id,
            "strategy_name": strategy_name,
            "cache_json": blob,
            "size_bytes": len(blob),
            "updated_at": r.now(),
            "updated_at_epoch": time.time(),
        }
        if use_new_schema:
            row.update(
                {
                    "origin": "live",
                    "config_hash": config_hash,
                    "nexus_module_hash": module_hash,
                    "end_date": end_date,
                    "record_version": 1,
                }
            )
        r.db(DB_NAME).table(TABLE_NAME).insert(row, conflict="replace").run(conn)
        return True
    except Exception:
        return False


def persist_backtest_snapshot(
    conn,
    r,
    *,
    instance_id: str,
    strategy_name: str,
    cache: dict,
    config_hash: str,
    module_hash: str,
    start_date: str,
    end_date: str,
    max_blob_bytes: int = 5_000_000,
) -> bool:
    """Write a backtest end-of-run snapshot of `_strategy_cache` to NexusStrategyCache.

    Row PK = "{instance_id}|{strategy_name}|{config_hash}|backtest|{end_date}"
    so backtest snapshots coexist with live runtime rows. Returns True on
    success, False otherwise. Never raises.
    """
    # CRITICAL-GUARD: skip snapshot persist when a backtest aborted due to
    # LLM critical failure. Otherwise we'd seed live-mode warm-start with a
    # corrupted cache (ruined LLM classifications cached as misses).
    try:
        from backtest_critical_abort import _skip_snapshot_persist
        if _skip_snapshot_persist:
            try:
                from intellistock_logger import intellistock_logger
                intellistock_logger.log(
                    "persist_backtest_snapshot: SKIPPED — critical LLM abort flagged this run as corrupt.",
                    "yellow", service="STRATEGY_CACHE_PERSISTENCE",
                )
            except Exception:
                pass
            return False
    except ImportError as _scg_import_err:
        # Log loudly when the critical-abort guard is missing — mid-deploy
        # state where backtest_critical_abort.py hasn't landed yet would
        # otherwise silently disable the guard exactly when we need it.
        try:
            from intellistock_logger import intellistock_logger
            intellistock_logger.log(
                f"persist_backtest_snapshot: critical-abort guard DISARMED "
                f"(import failed: {_scg_import_err}). Persist will proceed.",
                "yellow", service="STRATEGY_CACHE_PERSISTENCE",
            )
        except Exception:
            pass
    except Exception:
        pass

    if conn is None or r is None or not instance_id or not strategy_name:
        return False
    if not config_hash or not module_hash:
        return False
    if not end_date:
        return False
    row_id = f"{instance_id}|{strategy_name}|{config_hash}|backtest|{end_date}"
    try:
        if not _ensure_table(conn, r):
            return False
        blob = _serialize_cache_for_blob(cache or {})
        if len(blob) > max_blob_bytes:
            payload = json.loads(blob)
            sized = sorted(
                payload.items(),
                key=lambda kv: len(json.dumps(kv[1], default=str)),
                reverse=True,
            )
            for k, _ in sized:
                if k == "__skipped_fields__":
                    continue
                payload.pop(k, None)
                payload.setdefault("__skipped_fields__", []).append(k)
                blob = json.dumps(payload, default=str)
                if len(blob) <= max_blob_bytes:
                    break
        r.db(DB_NAME).table(TABLE_NAME).insert(
            {
                "id": row_id,
                "instance_id": instance_id,
                "strategy_name": strategy_name,
                "origin": "backtest",
                "config_hash": config_hash,
                "nexus_module_hash": module_hash,
                "start_date": start_date,
                "end_date": end_date,
                "cache_json": blob,
                "size_bytes": len(blob),
                "updated_at": r.now(),
                "updated_at_epoch": time.time(),
                "record_version": 1,
            },
            conflict="replace",
        ).run(conn)
        return True
    except Exception:
        return False


def load_with_fallback(
    conn,
    r,
    *,
    instance_id: str,
    strategy_name: str,
    current_config_hash: str,
    current_module_hash: str,
    staleness_days: int = 7,
) -> tuple:
    """Try to hydrate `_strategy_cache` from a recent NexusStrategyCache row.

    Returns (cache_dict_or_None, reason, meta_or_None) where:
      reason in {"ok", "disabled", "no_match", "stale", "module_drift",
                "deserialize_error", "db_error"}
      meta is a dict {"id", "origin", "end_date", "config_hash", "size_bytes"}
        on the "ok" path, None otherwise.
    """
    import os as _os
    if (_os.environ.get("NEXUS_LIVE_SNAPSHOT_LOAD", "on") or "on").lower() == "off":
        return None, "disabled", None
    if conn is None or r is None or not instance_id or not strategy_name:
        return None, "no_match", None
    try:
        if not _ensure_table(conn, r):
            return None, "db_error", None
        # Phase 1 bug-sweep (2026-05-21): tiebreaker was ``created_at``, which
        # neither writer (persist_backtest_snapshot, save_strategy_cache_to_db)
        # sets -- so the secondary sort was undefined. Use ``updated_at_epoch``,
        # which both writers DO set on every upsert.
        row = (
            r.db(DB_NAME).table(TABLE_NAME)
             .get_all([instance_id, current_config_hash], index="instance_id_config_hash")
             .filter(r.row["strategy_name"].eq(strategy_name))
             .order_by(r.desc("end_date"), r.desc("updated_at_epoch"))
             .limit(1)
             .nth(0)
             .default(None)
             .run(conn)
        )
    except Exception:
        return None, "db_error", None
    if not row:
        return None, "no_match", None
    module_hash = row.get("nexus_module_hash", "")
    if module_hash != current_module_hash:
        return None, "module_drift", None
    end_date_str = row.get("end_date") or ""
    try:
        import datetime as _dt
        end_dt = _dt.date.fromisoformat(end_date_str)
        if (_dt.date.today() - end_dt).days > staleness_days:
            return None, "stale", None
    except Exception:
        return None, "stale", None
    try:
        cache = _deserialize_cache_from_blob(row.get("cache_json", "") or "")
    except ValueError:
        return None, "deserialize_error", None
    meta = {
        "id": row.get("id", ""),
        "origin": row.get("origin", ""),
        "end_date": end_date_str,
        "config_hash": row.get("config_hash", ""),
        "size_bytes": int(row.get("size_bytes", 0) or 0),
    }
    return cache, "ok", meta


def merge_loaded_cache_into(target: dict, loaded: Optional[dict]) -> None:
    """Merge loaded dict into ``target`` in-place without blowing away
    existing entries. Used to restore persisted keys into a freshly-created
    `_strategy_cache[name]` dict without clobbering any live writes."""
    if not isinstance(target, dict) or not isinstance(loaded, dict):
        return
    for k, v in loaded.items():
        if not isinstance(k, str) or _is_blacklisted(k):
            continue
        if k in target:
            # Prefer the already-present value (live write wins over stale cache).
            continue
        target[k] = v
