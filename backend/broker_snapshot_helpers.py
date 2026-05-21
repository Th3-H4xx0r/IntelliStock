"""Backtest-snapshot persist helpers (Phase 1 live-mode safe startup).

Extracted from ``broker.py`` so tests can exercise the helpers without
dragging in broker.py's module-level argparse + DB bootstrap (broker.py
parses ``sys.argv`` and connects to RethinkDB on import).

Pattern follows the existing extraction shims (``broker_session.py``,
``strategy_tick_state.py``, ``nexus_lookback_db.py``): broker.py imports
the public symbol lazily at each call site and the test file targets
this module directly.

Public API:
  - ``_invoke_persist_backtest_snapshot`` — compute hashes + delegate to
    ``strategy_cache_persistence.persist_backtest_snapshot``; swallows
    exceptions so a snapshot failure never breaks the backtest itself.
  - ``_collect_prompt_versions`` / ``_collect_llm_stages`` /
    ``_collect_history_scope_inputs`` — flat-config dict pluckers used
    to build the canonical ``config_dict`` fed to the hash function.
  - ``_resolve_nexus_module_path`` — best-effort path lookup for
    ``graph_nexus_analysis.py`` used to compute ``module_hash``.

Honors ``NEXUS_BACKTEST_SNAPSHOT_WRITE=off`` to short-circuit writes.
"""

from __future__ import annotations

import os as _os


def _collect_prompt_versions(cfg: dict) -> dict:
    """Pluck prompt version constants from a strategy config dict."""
    cfg = cfg or {}
    return {
        "sentiment": cfg.get("nexus_sentiment_prompt_version", ""),
        "macro": cfg.get("nexus_macro_prompt_version", ""),
        "company": cfg.get("nexus_company_prompt_version", ""),
        "discovery": cfg.get("nexus_discovery_prompt_version", ""),
        "analyst": cfg.get("nexus_analyst_prompt_version", ""),
    }


def _collect_llm_stages(cfg: dict) -> dict:
    """Pluck provider/model/effort per LLM stage from a strategy config dict."""
    cfg = cfg or {}
    out = {}
    for stage in ("discovery", "sentiment", "macro", "company", "analyst"):
        out[stage] = {
            "provider": cfg.get(f"{stage}_llm_provider", ""),
            "model": cfg.get(f"{stage}_llm_model", ""),
            "effort": cfg.get(f"{stage}_llm_effort", ""),
        }
    return out


def _collect_history_scope_inputs(cfg: dict) -> dict:
    """Pluck history_scope_id ingredients from a strategy config dict."""
    cfg = cfg or {}
    return {
        "neo4j_uri": cfg.get("neo4j_uri", ""),
        "neo4j_user": cfg.get("neo4j_user", ""),
        "sentiment_cache_scope_salt": cfg.get("sentiment_cache_scope_salt", ""),
        "use_toon_format": bool(cfg.get("use_toon_format", False)),
        "num_articles_for_llm": int(cfg.get("num_articles_for_llm", 0) or 0),
    }


def _resolve_nexus_module_path() -> str:
    """Best-effort lookup of the graph_nexus_analysis.py file path.

    Returns empty string if not resolvable, in which case the caller's
    ``module_hash`` falls back to ``"missing"``.
    """
    try:
        import importlib.util
        spec = importlib.util.find_spec("graph_nexus_analysis")
        if spec and spec.origin:
            return str(spec.origin)
        spec = importlib.util.find_spec("strategies.graph_nexus_analysis")
        if spec and spec.origin:
            return str(spec.origin)
    except Exception:
        pass
    return ""


def _invoke_persist_backtest_snapshot(
    conn,
    r,
    *,
    base_instance_id: str,
    strategy_name: str,
    strategy_cache: dict,
    config_dict: dict,
    start_date: str,
    end_date: str,
) -> bool:
    """Compute hashes and call ``strategy_cache_persistence.persist_backtest_snapshot``.

    Wrapped so a snapshot failure never breaks the backtest itself.
    Returns ``True`` on success, ``False`` otherwise.

    Short-circuits with ``False`` (without calling persist) when:
      - ``NEXUS_BACKTEST_SNAPSHOT_WRITE=off`` env is set, or
      - ``base_instance_id`` or ``strategy_name`` is empty.
    """
    if (_os.environ.get("NEXUS_BACKTEST_SNAPSHOT_WRITE", "on") or "on").lower() == "off":
        return False
    if not base_instance_id or not strategy_name:
        return False
    try:
        # Lazy import so tests can monkeypatch
        # ``strategy_cache_persistence.persist_backtest_snapshot`` on the
        # module object and have it picked up here.
        try:
            from backend import strategy_cache_persistence as _scp
        except ImportError:
            import strategy_cache_persistence as _scp  # type: ignore[no-redef]
        config_hash = _scp._compute_config_hash(config_dict)
        module_path = _resolve_nexus_module_path()
        module_hash = _scp._compute_module_hash(module_path) if module_path else "missing"
        ok = _scp.persist_backtest_snapshot(
            conn=conn,
            r=r,
            instance_id=base_instance_id,
            strategy_name=strategy_name,
            cache=strategy_cache,
            config_hash=config_hash,
            module_hash=module_hash,
            start_date=start_date,
            end_date=end_date,
        )
        if ok:
            print(f"[snapshot] persisted: id={base_instance_id}|{strategy_name}|{config_hash}|backtest|{end_date}")
        else:
            print(f"[snapshot] persist FAILED for id={base_instance_id}|{strategy_name}|{config_hash}|backtest|{end_date}")
        return bool(ok)
    except Exception as e:
        print(f"[snapshot] persist raised (suppressed): {e}")
        return False


def _invoke_load_snapshot_with_gap(
    conn,
    r,
    *,
    instance_id: str,
    strategy_name: str,
    current_config_hash: str,
    current_module_hash: str,
    staleness_days: int = 7,
) -> tuple:
    """Call ``load_with_fallback`` and compute a gap-day list for shortened lookback.

    Returns ``(cache_dict_or_None, reason, gap_dates_list_or_None)``:
      - cache=None, gap_dates=None  -> snapshot not usable; caller runs FULL lookback.
      - cache=dict, gap_dates=[]    -> snapshot covers today; caller skips lookback.
      - cache=dict, gap_dates=[...] -> snapshot partial; caller runs lookback for those days.

    Looks up ``load_with_fallback`` on the live module object (not as a
    re-imported local symbol) so the standard test pattern of
    ``monkeypatch.setattr(strategy_cache_persistence, "load_with_fallback", ...)``
    is honored.
    """
    try:
        try:
            from backend import strategy_cache_persistence as _scp
        except ImportError:
            import strategy_cache_persistence as _scp  # type: ignore[no-redef]
    except Exception:
        # If the helper module isn't importable, treat as no match — caller falls back.
        return None, "no_match", None
    try:
        cache, reason, meta = _scp.load_with_fallback(
            conn=conn,
            r=r,
            instance_id=instance_id,
            strategy_name=strategy_name,
            current_config_hash=current_config_hash,
            current_module_hash=current_module_hash,
            staleness_days=staleness_days,
        )
    except Exception:
        return None, "no_match", None
    if cache is None:
        return None, reason, None
    try:
        import datetime as _dt
        end_date_str = (meta or {}).get("end_date", "") if meta else ""
        end_dt = _dt.date.fromisoformat(end_date_str) if end_date_str else _dt.date.today()
        today_dt = _dt.date.today()
        gap_dates = []
        cursor = end_dt + _dt.timedelta(days=1)
        while cursor <= today_dt:
            if cursor.weekday() < 5:  # weekdays only; lookback skips holidays via data availability
                gap_dates.append(cursor.isoformat())
            cursor += _dt.timedelta(days=1)
        return cache, reason, gap_dates
    except Exception:
        # Bad date parse -> conservative: return cache but no gap (caller will skip lookback).
        return cache, reason, []


def _invoke_save_strategy_cache(
    conn,
    r,
    *,
    instance_id: str,
    strategy_name: str,
    cache: dict,
    config_dict: dict,
) -> bool:
    """Wrapper around save_strategy_cache_to_db that computes config_hash and
    module_hash from the current strategy state and passes end_date=today.

    Used by the live runtime periodic save path. Errors are swallowed.
    Returns True on success, False otherwise.
    """
    try:
        from backend import strategy_cache_persistence as _scp
        config_hash = _scp._compute_config_hash(config_dict)
        module_path = _resolve_nexus_module_path()
        module_hash = _scp._compute_module_hash(module_path) if module_path else "missing"
        import datetime as _dt
        return bool(_scp.save_strategy_cache_to_db(
            conn, r, instance_id, strategy_name, cache,
            config_hash=config_hash,
            module_hash=module_hash,
            end_date=_dt.date.today().isoformat(),
        ))
    except Exception as e:
        try:
            print(f"[snapshot] live save raised (suppressed): {e}")
        except Exception:
            pass
        return False
