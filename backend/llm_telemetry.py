"""Central LLM telemetry sink: captures per-call token usage from every
provider, computes USD cost from a pricing registry, persists to RethinkDB
asynchronously, and exposes a small public API for the FastAPI layer to
query recent calls and aggregates.

Public functions:
    load_pricing_yaml(path)       - read YAML pricing into dict
    configure(...)                - wire DB connection + flush settings
    record_llm_call(...)          - the instrumentation entry point
    llm_call_context(...)         - context manager for attribution
    get_recent_calls(n)           - in-memory ring buffer reader (UI fast path)
    flush()                       - forced sync flush (tests, shutdown)
    get_buffer_depth()            - health-endpoint accessor
    get_pricing(model)            - exposed for the Models UI
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional


def load_pricing_yaml(path: str) -> Dict[str, Dict[str, Any]]:
    """Read the pricing YAML file. Returns an empty dict if the file is
    missing or unparseable - telemetry continues with cost_source="unknown"
    rather than blocking calls.
    """
    if not os.path.isfile(path):
        return {}
    try:
        import yaml
    except ImportError:
        print(
            "[llm_telemetry] PyYAML not installed; pricing disabled.",
            flush=True,
        )
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(
            f"[llm_telemetry] failed to parse pricing YAML ({path}): {e}",
            flush=True,
        )
        return {}


_LLM_USAGE_TABLE = "LLMUsage"
_LLM_USAGE_DAILY_TABLE = "LLMUsageDaily"

_LLM_USAGE_INDEXES = ("ts", "provider", "model", "backtest_id", "instance_id")
_LLM_USAGE_DAILY_INDEXES = ("date",)


def ensure_llm_usage_tables(*, conn, r, db_name: str) -> None:
    """Idempotently create the LLMUsage + LLMUsageDaily tables and their
    secondary indexes. Safe to call on every process start.
    """
    if db_name not in list(r.db_list().run(conn)):
        r.db_create(db_name).run(conn)
    existing = list(r.db(db_name).table_list().run(conn))
    if _LLM_USAGE_TABLE not in existing:
        r.db(db_name).table_create(_LLM_USAGE_TABLE).run(conn)
    if _LLM_USAGE_DAILY_TABLE not in existing:
        r.db(db_name).table_create(_LLM_USAGE_DAILY_TABLE).run(conn)

    def _ensure_indexes(table: str, indexes: tuple[str, ...]) -> None:
        existing_idx = list(r.db(db_name).table(table).index_list().run(conn))
        for idx in indexes:
            if idx not in existing_idx:
                try:
                    r.db(db_name).table(table).index_create(idx).run(conn)
                except Exception as e:
                    print(
                        f"[llm_telemetry] index_create {table}.{idx} failed: {e}",
                        flush=True,
                    )

    _ensure_indexes(_LLM_USAGE_TABLE, _LLM_USAGE_INDEXES)
    _ensure_indexes(_LLM_USAGE_DAILY_TABLE, _LLM_USAGE_DAILY_INDEXES)


_COST_FIELDS = (
    "input_cost_usd",
    "output_cost_usd",
    "cache_creation_cost_usd",
    "cache_read_cost_usd",
    "total_cost_usd",
)

_USAGE_TO_PRICE_KEY = {
    "input_tokens": ("input_per_1m", "input_cost_per_1m", "input_cost_usd"),
    "output_tokens": ("output_per_1m", "output_cost_per_1m", "output_cost_usd"),
    "cache_creation_input_tokens": (
        "cache_creation_per_1m",
        "cache_creation_cost_per_1m",
        "cache_creation_cost_usd",
    ),
    "cache_read_input_tokens": (
        "cache_read_per_1m",
        "cache_read_cost_per_1m",
        "cache_read_cost_usd",
    ),
}


def compute_cost(
    *,
    model: str,
    usage: Dict[str, int],
    pricing_yaml: Dict[str, Dict[str, Any]],
    models_override: Optional[Dict[str, Any]],
    cost_usd_override: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute per-call USD cost from token counts.

    Priority order:
      1. cost_usd_override (envelope) -> cost_source="envelope"
      2. models_override (Models-table fields) -> cost_source="models_override"
      3. pricing_yaml entry for `model` -> cost_source="yaml"
      4. Fallback: zero cost, cost_source="unknown"
    """
    result = {field: 0.0 for field in _COST_FIELDS}

    if cost_usd_override is not None:
        result["total_cost_usd"] = float(cost_usd_override)
        result["cost_source"] = "envelope"
        return result

    # Pick the price table to use.
    price_keys: Dict[str, Optional[float]] = {}
    source = "unknown"
    if models_override and any(
        models_override.get(k) is not None
        for _, k, _ in _USAGE_TO_PRICE_KEY.values()
    ):
        # Models table provides at least one field - use it (others fall through
        # to YAML, then 0).
        source = "models_override"
        for yaml_k, override_k, _ in _USAGE_TO_PRICE_KEY.values():
            price_keys[yaml_k] = models_override.get(override_k)
        # Fill the gaps from YAML if available.
        yaml_entry = pricing_yaml.get(model) or {}
        for yaml_k in price_keys:
            if price_keys[yaml_k] is None:
                price_keys[yaml_k] = yaml_entry.get(yaml_k)
    else:
        yaml_entry = pricing_yaml.get(model)
        if yaml_entry is not None:
            source = "yaml"
            for yaml_k in _USAGE_TO_PRICE_KEY.values():
                pass  # noqa - iterate below
            for yaml_k, _, _ in _USAGE_TO_PRICE_KEY.values():
                price_keys[yaml_k] = yaml_entry.get(yaml_k)

    total = 0.0
    for usage_k, (yaml_k, _, cost_k) in _USAGE_TO_PRICE_KEY.items():
        tokens = int(usage.get(usage_k) or 0)
        price_per_1m = price_keys.get(yaml_k)
        if price_per_1m is None:
            continue
        component = (tokens / 1_000_000.0) * float(price_per_1m)
        result[cost_k] = component
        total += component
    result["total_cost_usd"] = total
    result["cost_source"] = source
    return result


import threading
import time
import uuid
import sys
from collections import deque
from contextlib import contextmanager
from typing import Iterator, List

# Module state
_state_lock = threading.Lock()
_buffer: deque = deque()                  # in-flight rows waiting to be flushed
_ring_buffer: deque = deque(maxlen=200)   # recent calls for UI fast path
_state = {
    "enabled": False,
    "db_conn_factory": None,
    "r_module": None,
    "db_name": "IntelliStock",
    "flush_interval_s": 5.0,
    "max_buffer": 50,
    "max_buffer_hard_cap": 5000,
    "pricing_yaml": {},
    "pricing_yaml_path": "backend/llm_pricing.yaml",
    "models_override_lookup": None,  # Optional[Callable[[str], Optional[dict]]]
    "write_errors_24h": 0,
    "last_flush_ts": 0,
    "flusher_thread": None,
    "stop_flusher": False,
}

# Thread-local attribution stack.
_ctx_local = threading.local()


def _get_ctx_stack() -> List[Dict[str, Any]]:
    stack = getattr(_ctx_local, "stack", None)
    if stack is None:
        stack = []
        _ctx_local.stack = stack
    return stack


def _merge_active_ctx() -> Dict[str, Any]:
    """Merge all active context frames, inner wins for set keys."""
    merged: Dict[str, Any] = {}
    for frame in _get_ctx_stack():
        for k, v in frame.items():
            if v is not None:
                merged[k] = v
    return merged


@contextmanager
def llm_call_context(
    *,
    backtest_id: Optional[str] = None,
    instance_id: Optional[str] = None,
    strategy: Optional[str] = None,
    call_site: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Iterator[None]:
    frame = {
        "backtest_id": backtest_id,
        "instance_id": instance_id,
        "strategy": strategy,
        "call_site": call_site,
        "conversation_id": conversation_id,
    }
    stack = _get_ctx_stack()
    stack.append(frame)
    try:
        yield
    finally:
        stack.pop()


# Public API

def configure(
    *,
    db_conn_factory: Optional[Any] = None,
    enabled: bool = True,
    flush_interval_s: float = 5.0,
    max_buffer: int = 50,
    max_buffer_hard_cap: int = 5000,
    pricing_yaml_path: Optional[str] = None,
    models_override_lookup: Optional[Any] = None,
    auto_start_flusher: bool = True,
    r_module: Optional[Any] = None,
    db_name: str = "IntelliStock",
) -> None:
    """Wire up the sink. Call once at FastAPI startup."""
    with _state_lock:
        _state["enabled"] = bool(enabled)
        _state["db_conn_factory"] = db_conn_factory
        _state["flush_interval_s"] = float(flush_interval_s)
        _state["max_buffer"] = int(max_buffer)
        _state["max_buffer_hard_cap"] = int(max_buffer_hard_cap)
        _state["pricing_yaml_path"] = pricing_yaml_path
        _state["models_override_lookup"] = models_override_lookup
        _state["r_module"] = r_module
        _state["db_name"] = db_name
        _state["pricing_yaml"] = (
            load_pricing_yaml(pricing_yaml_path) if pricing_yaml_path else {}
        )

    if auto_start_flusher and enabled:
        _start_flusher()


def _reset_for_tests() -> None:
    """Tear down all state. ONLY for use from tests."""
    with _state_lock:
        _buffer.clear()
        _ring_buffer.clear()
        if _state.get("flusher_thread"):
            _state["stop_flusher"] = True
        _state.update({
            "enabled": False, "db_conn_factory": None, "r_module": None,
            "pricing_yaml": {}, "models_override_lookup": None,
            "write_errors_24h": 0, "last_flush_ts": 0,
            "flusher_thread": None, "stop_flusher": False,
        })
    _ctx_local.stack = []


def record_llm_call(
    *,
    provider: str,
    model: str,
    usage: Dict[str, int],
    ok: bool = True,
    duration_ms: int = 0,
    retry_count: int = 0,
    error: Optional[str] = None,
    cost_usd_override: Optional[float] = None,
    model_id: Optional[str] = None,
) -> None:
    """Record one LLM call. O(1); never blocks or raises out to the caller."""
    try:
        if not _state.get("enabled"):
            return

        # Cost computation
        override_lookup = _state.get("models_override_lookup")
        models_override = None
        if override_lookup is not None and model_id:
            try:
                models_override = override_lookup(model_id)
            except Exception:
                models_override = None
        cost = compute_cost(
            model=model,
            usage=usage or {},
            pricing_yaml=_state.get("pricing_yaml") or {},
            models_override=models_override,
            cost_usd_override=cost_usd_override,
        )

        ctx = _merge_active_ctx()
        row: Dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "ts": int(time.time() * 1000),
            "provider": provider,
            "model": model,
            "model_id": model_id,
            "input_tokens": int(usage.get("input_tokens", 0) or 0),
            "output_tokens": int(usage.get("output_tokens", 0) or 0),
            "cache_creation_input_tokens": int(
                usage.get("cache_creation_input_tokens", 0) or 0
            ),
            "cache_read_input_tokens": int(
                usage.get("cache_read_input_tokens", 0) or 0
            ),
            "reasoning_tokens": int(usage.get("reasoning_tokens", 0) or 0),
            "ok": bool(ok),
            "duration_ms": int(duration_ms or 0),
            "retry_count": int(retry_count or 0),
            "error": (str(error)[:200] if error else None),
            "backtest_id": ctx.get("backtest_id"),
            "instance_id": ctx.get("instance_id"),
            "strategy": ctx.get("strategy"),
            "call_site": ctx.get("call_site"),
            "conversation_id": ctx.get("conversation_id"),
        }
        row.update(cost)  # input_cost_usd, output_cost_usd, ..., cost_source

        with _state_lock:
            hard_cap = _state.get("max_buffer_hard_cap", 5000)
            if len(_buffer) >= hard_cap:
                # Drop oldest from the in-flight buffer (memory safety).
                # Newest entries are still recorded; the dropped ones may
                # never reach the DB but will appear in the ring buffer.
                try:
                    _buffer.popleft()
                except IndexError:
                    pass
            _buffer.append(row)
            # Ring buffer holds the most recent rows for the UI fast path.
            _ring_buffer.appendleft(row)
    except Exception as e:
        print(f"[llm_telemetry] record_llm_call swallowed: {e}", file=sys.stderr,
              flush=True)


def get_buffer_depth() -> int:
    with _state_lock:
        return len(_buffer)


def get_recent_calls(n: int = 50) -> List[Dict[str, Any]]:
    with _state_lock:
        return list(_ring_buffer)[:max(0, int(n))]


def flush() -> None:
    """Synchronously drain the buffer to the DB. Used by tests and graceful
    shutdown. The background flusher uses the same internal _do_flush()."""
    _do_flush()


def _do_flush() -> None:
    with _state_lock:
        if not _state.get("enabled"):
            return
        rows = list(_buffer)
        _buffer.clear()
        conn_factory = _state.get("db_conn_factory")
        r_module = _state.get("r_module")
        db_name = _state.get("db_name", "IntelliStock")

    if not rows:
        return
    if conn_factory is None or r_module is None:
        # Not configured for DB writes - drop silently (test scenario).
        return
    try:
        conn = conn_factory()
        r_module.db(db_name).table(_LLM_USAGE_TABLE).insert(rows).run(conn)
        with _state_lock:
            _state["last_flush_ts"] = int(time.time() * 1000)
    except Exception as e:
        print(f"[llm_telemetry] flush failed ({len(rows)} rows): {e}",
              file=sys.stderr, flush=True)
        with _state_lock:
            _state["write_errors_24h"] = int(_state.get("write_errors_24h", 0)) + 1


def _flusher_loop() -> None:
    while True:
        with _state_lock:
            if _state.get("stop_flusher"):
                return
            interval = float(_state.get("flush_interval_s", 5.0))
        time.sleep(interval)
        _do_flush()


def _start_flusher() -> None:
    with _state_lock:
        if _state.get("flusher_thread") is not None:
            return
        t = threading.Thread(target=_flusher_loop, name="llm-telemetry-flusher",
                             daemon=True)
        _state["flusher_thread"] = t
    t.start()


def rollup_daily(
    *,
    conn,
    r,
    db_name: str,
    date_iso: str,
    _seed_rows_for_test: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """Aggregate LLMUsage rows for `date_iso` (YYYY-MM-DD) into LLMUsageDaily
    upserts (one per (date, provider, model)). Idempotent.
    Returns number of buckets written.
    """
    if _seed_rows_for_test is not None:
        rows = list(_seed_rows_for_test)
    else:
        # Pull rows for the target date.
        import datetime as _dt
        start = int(_dt.datetime.fromisoformat(date_iso + "T00:00:00").timestamp() * 1000)
        end = start + 24 * 3600 * 1000
        rows = list(
            r.db(db_name).table(_LLM_USAGE_TABLE)
            .filter(lambda x: (x["ts"] >= start) & (x["ts"] < end)).run(conn)
        )

    buckets: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        provider = row.get("provider") or "unknown"
        model = row.get("model") or "unknown"
        bucket_id = f"{date_iso}_{provider}_{model}"
        b = buckets.setdefault(bucket_id, {
            "id": bucket_id, "date": date_iso, "provider": provider, "model": model,
            "call_count": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "reasoning_tokens": 0, "total_cost_usd": 0.0,
        })
        b["call_count"] += 1
        for fld in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                    "cache_read_input_tokens", "reasoning_tokens"):
            b[fld] += int(row.get(fld, 0) or 0)
        b["total_cost_usd"] += float(row.get("total_cost_usd", 0.0) or 0.0)

    now_ms = int(time.time() * 1000)
    out = []
    for bucket_id, b in buckets.items():
        b["last_updated_ts"] = now_ms
        out.append(b)
    if out:
        r.db(db_name).table(_LLM_USAGE_DAILY_TABLE).insert(out, conflict="replace").run(conn)
    return len(out)


def probe_local_cli_usage_file() -> Optional[Dict[str, Any]]:
    """Best-effort: read claude CLI's local usage file if it exists.
    Returns None if nothing found.
    """
    candidates = [
        os.path.expanduser("~/.claude/usage.json"),
        os.path.expanduser("~/.claude/.usage"),
        os.path.expanduser("~/.config/claude/usage.json"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                import json as _json
                return _json.load(f)
        except Exception:
            continue
    return None
