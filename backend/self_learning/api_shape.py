"""Pure response shaping for the learning endpoints.

Kept out of api/main.py so the aggregation is unit-testable without RethinkDB —
the same reason `nexus_telemetry.py` exists.

The counters arrive pre-aggregated by the database. An earlier version summed a
500-row slice and presented it as a total, so `runs_observed` pinned at 500 and
"decisions observed" silently became "decisions observed in the newest 500 runs".
"""
from __future__ import annotations


def overview(*, counts, config, engine_running=False) -> dict:
    counts = counts or {}
    mode = str((config or {}).get("mode") or "observe")
    return {
        "mode": mode,
        # Phase 1 cannot act. Saying so explicitly stops the UI from implying
        # an autonomy that is not wired yet.
        "acts_autonomously": mode not in ("observe", ""),
        "enabled": bool((config or {}).get("enabled", True)),
        "engine_running": bool(engine_running),
        "retain_days": (config or {}).get("retain_days"),
        "open_findings": int(counts.get("open_findings") or 0),
        "by_severity": dict(counts.get("by_severity") or {}),
        "runs_observed": int(counts.get("runs_observed") or 0),
        "decisions_observed": int(counts.get("decisions_observed") or 0),
        "refusals_observed": int(counts.get("refusals_observed") or 0),
    }
