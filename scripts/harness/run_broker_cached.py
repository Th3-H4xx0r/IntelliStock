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
    return cache


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
