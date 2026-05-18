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

import json
import time
from typing import Any, Optional

TABLE_NAME = "NexusStrategyCache"
DB_NAME = "IntelliStock"

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
    "_post_sell_watch_inmem",
    "_yf_market_cap_cache_preseeded",
    # Phase α.2 (BT109429 follow-up, 2026-05-18): Neo4j query snapshot
    # for variance containment. Per-backtest-run; persisting would mix
    # snapshots across runs with different universes and dates. The
    # stats counters are also per-run telemetry.
    "_neo4j_snapshot",
    "_neo4j_snapshot_stats",
)

_MAX_DICT_ENTRIES = 500
_ROW_TTL_SEC = 14 * 24 * 3600


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


def _ensure_table(conn, r) -> bool:
    try:
        tables = list(r.db(DB_NAME).table_list().run(conn))
    except Exception:
        return False
    if TABLE_NAME in tables:
        return True
    try:
        r.db(DB_NAME).table_create(TABLE_NAME).run(conn)
        return True
    except Exception:
        return False


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
) -> bool:
    """Upsert `cache` dict keyed by `(instance_id, strategy_name)`.

    Returns True on success, False otherwise. Never raises.
    Skips any key matching blacklist prefixes. Caps nested dicts at
    ``_MAX_DICT_ENTRIES``.
    """
    if conn is None or r is None or not instance_id or not strategy_name:
        return False
    if not isinstance(cache, dict):
        return False
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
            # Over-cap: drop the largest top-level entries until we fit.
            sized = sorted(
                filtered.items(),
                key=lambda kv: len(json.dumps(kv[1], default=str)),
                reverse=True,
            )
            for k, _ in sized:
                filtered.pop(k, None)
                blob = json.dumps(filtered, default=str)
                if len(blob) <= max_blob_bytes:
                    break
        if not _ensure_table(conn, r):
            return False
        r.db(DB_NAME).table(TABLE_NAME).insert(
            {
                "id": row_id,
                "instance_id": instance_id,
                "strategy_name": strategy_name,
                "cache_json": blob,
                "size_bytes": len(blob),
                "updated_at": r.now(),
                "updated_at_epoch": time.time(),
            },
            conflict="replace",
        ).run(conn)
        return True
    except Exception:
        return False


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
