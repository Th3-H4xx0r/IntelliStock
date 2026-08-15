"""Pure response shaping for the learning endpoints.

Kept out of api/main.py so the aggregation is unit-testable without RethinkDB —
the same reason `nexus_telemetry.py` exists.
"""
from __future__ import annotations

from collections import Counter


def overview(*, findings, funnels, config) -> dict:
    open_findings = [f for f in (findings or [])
                     if str((f or {}).get("status") or "open") == "open"]
    severities = Counter(str(f.get("severity") or "low") for f in open_findings)
    mode = str((config or {}).get("mode") or "observe")
    return {
        "mode": mode,
        # Phase 1 cannot act. Saying so explicitly stops the UI from implying
        # an autonomy that is not wired yet.
        "acts_autonomously": mode not in ("observe", ""),
        "enabled": bool((config or {}).get("enabled", True)),
        "open_findings": len(open_findings),
        "by_severity": dict(severities),
        "runs_observed": len(funnels or []),
        "decisions_observed": sum(int((f or {}).get("decided") or 0)
                                  for f in (funnels or [])),
        "refusals_observed": sum(int((f or {}).get("refused") or 0)
                                 for f in (funnels or [])),
    }
