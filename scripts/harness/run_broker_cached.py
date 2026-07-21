#!/usr/bin/env python3
"""Launch the REAL broker.py backtest with a persistent local LLM cache
installed — WITHOUT changing any production code.

How it stays faithful + zero-touch:
  1. We import `llm_utils` and monkeypatch its single dispatch chokepoints
     (`call_structured_llm_by_provider`, `call_llm_by_provider`) with a
     read-through disk cache (see llm_disk_cache.py). Because we do this
     BEFORE broker.py (and therefore graph_nexus_analysis) is imported, the
     strategy's `from llm_utils import ...` binds to the patched versions.
  2. We then run `backend/broker.py` verbatim as __main__ via runpy, so the
     entire real backtest loop / PortfolioEmulator / fill-gate logic executes
     exactly as it does under the official engine.

The cache sits AFTER the strategy's own RethinkDB cache checks (those happen
upstream, before the dispatch is called) and BEFORE the live Claude call:
    DB cache (existing) -> local disk cache (this) -> live claude-cli.

Usage (same positional args as broker.py, forwarded verbatim):
  NEXUS_LOCAL_LLM_CACHE_DIR=/path/to/.backtest_llm_cache \
  python scripts/harness/run_broker_cached.py <instance> backtest <start> <end> \
        <granularity> <key> <secret> [symbols...] --initial-cash N --backtest-id ID
"""
from __future__ import annotations

import os
import runpy
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.environ.get("INTELLISTOCK_BACKEND_DIR") or os.path.abspath(os.path.join(_HERE, "..", "..", "backend"))


def _install_cache() -> "object|None":
    """Patch the LLM dispatch chokepoints. Returns the cache module (for
    end-of-run stats) or None if the cache dir isn't configured / patch fails."""
    sys.path.insert(0, _HERE)          # llm_disk_cache
    sys.path.insert(0, _BACKEND)       # llm_utils
    try:
        import llm_disk_cache as cache
    except Exception as e:  # pragma: no cover - defensive
        print(f"[run_broker_cached] disk-cache import failed ({e}); running WITHOUT local cache", file=sys.stderr)
        return None
    if cache.cache_dir() is None:
        print("[run_broker_cached] NEXUS_LOCAL_LLM_CACHE_DIR unset; running WITHOUT local cache", file=sys.stderr)
        return None
    try:
        import llm_utils
    except Exception as e:  # pragma: no cover
        print(f"[run_broker_cached] llm_utils import failed ({e}); running WITHOUT local cache", file=sys.stderr)
        return None
    patched = []
    if hasattr(llm_utils, "call_structured_llm_by_provider"):
        llm_utils.call_structured_llm_by_provider = cache.wrap_structured(llm_utils.call_structured_llm_by_provider)
        patched.append("structured")
    if hasattr(llm_utils, "call_llm_by_provider"):
        llm_utils.call_llm_by_provider = cache.wrap_plain(llm_utils.call_llm_by_provider)
        patched.append("plain")
    print(f"[run_broker_cached] local LLM cache ON at {cache.cache_dir()} (patched: {', '.join(patched) or 'none'})",
          file=sys.stderr)
    _install_forced_provider()
    return cache


def _install_forced_provider() -> None:
    """Force ALL graph_nexus LLM roles to a provider/model regardless of the
    strategy config. The resolver's precedence is config-before-env, so
    doc-179's llm_provider=openrouter/nemotron would otherwise win over the
    GRAPH_NEXUS_LLM_PROVIDER env var. We patch the single resolver chokepoint
    `_resolve_role_llm_config` (returns (provider, api_key, model, prompt_v));
    the strategy calls it as a module global, so patching the attribute here —
    before broker.py imports the strategy — redirects every role. prompt_v is
    preserved so the sentiment cache scope stays well-formed."""
    prov = (os.environ.get("HARNESS_FORCE_LLM_PROVIDER") or "").strip()
    if not prov:
        return
    model = (os.environ.get("HARNESS_FORCE_LLM_MODEL") or "haiku").strip()
    try:
        import strategies.graph_nexus_analysis as gna
    except Exception as e:  # pragma: no cover
        print(f"[run_broker_cached] could not import strategy to force provider ({e})", file=sys.stderr)
        return
    _orig = gna._resolve_role_llm_config
    _key = "claude-cli-no-api-key" if prov == "claude-cli" else (os.environ.get("HARNESS_FORCE_LLM_KEY") or "")

    def _forced(config, role, *a, **kw):
        try:
            _p, _k, _m, _pv = _orig(config, role, *a, **kw)
        except Exception:
            _pv = "default_v1"
        return prov, _key, model, _pv

    gna._resolve_role_llm_config = _forced
    print(f"[run_broker_cached] FORCED LLM provider={prov} model={model} for ALL roles (overrides config)",
          file=sys.stderr)


def main() -> int:
    cache = _install_cache()
    broker = os.path.join(_BACKEND, "broker.py")
    if not os.path.isfile(broker):
        print(f"[run_broker_cached] broker.py not found at {broker}", file=sys.stderr)
        return 2
    # broker.py parses sys.argv[1:] and uses cwd=backend for relative access.
    sys.argv = [broker] + sys.argv[1:]
    os.chdir(_BACKEND)
    try:
        runpy.run_path(broker, run_name="__main__")
        rc = 0
    except SystemExit as e:  # broker.py may sys.exit
        rc = int(e.code) if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        if cache is not None:
            s = cache.stats()
            hits = s.get("struct_hit", 0) + s.get("plain_hit", 0)
            miss = s.get("struct_miss", 0) + s.get("plain_miss", 0)
            total = hits + miss
            rate = (100.0 * hits / total) if total else 0.0
            print(f"[run_broker_cached] LLM cache: {hits} hit / {miss} miss ({rate:.0f}% hit), "
                  f"{s.get('store',0)} stored, {s.get('error',0)} errors", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
