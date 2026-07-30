"""A research run must be able to resume its own lookback.

The resume filter accepted only pit_provenance == "strict_verified". Research
runs write "legacy_unverified" by design, so every day they had just finished
was rejected and each attempt restarted all 85 days -- roughly 4 hours of LLM
work repeated on every rerun, which is why the log always read
"85/85 days still need processing" even though the scope was unchanged and
cleanup was explicitly skipped with "lookback data preserved".

Strict runs are unaffected: resuming a strict run from unverified rows would
launder current-state data into a strict result.
"""
import os
import sys
from datetime import datetime, timezone

_backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from nexus_lookback_db import (  # noqa: E402
    _resumable_trade_context_row,
    _strict_pit_trade_context_row,
)

_AS_OF = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc).isoformat()


def _row(prov, manifest="m-1", as_of=_AS_OF):
    return {"date_key": "2026-03-02", "pit_provenance": prov,
            "pit_manifest_id": manifest, "pit_as_of": as_of}


def test_strict_rows_resume_in_either_mode():
    row = _row("strict_verified")
    assert _resumable_trade_context_row(row, allow_research=False) is True
    assert _resumable_trade_context_row(row, allow_research=True) is True


def test_research_rows_resume_only_a_research_run():
    row = _row("legacy_unverified")
    assert _resumable_trade_context_row(row, allow_research=True) is True
    assert _resumable_trade_context_row(row, allow_research=False) is False, (
        "a strict run must never resume from unverified rows")


def test_incomplete_research_rows_never_count():
    """Completeness still applies — a half-written row is not a finished day."""
    for bad in (_row("legacy_unverified", manifest=""),
                _row("legacy_unverified", as_of=""),
                _row("legacy_unverified", as_of="not-a-timestamp"),
                _row("legacy_unverified", as_of="2026-03-02T14:30:00")):  # naive
        assert _resumable_trade_context_row(bad, allow_research=True) is False, bad


def test_unknown_provenance_never_resumes():
    for prov in ("", None, "live_current", "something_else"):
        assert _resumable_trade_context_row(
            _row(prov), allow_research=True) is False, prov


def test_non_dict_rows_are_safe():
    for bad in (None, "x", 42, []):
        assert _resumable_trade_context_row(bad, allow_research=True) is False


def test_strict_predicate_is_unchanged():
    assert _strict_pit_trade_context_row(_row("strict_verified")) is True
    assert _strict_pit_trade_context_row(_row("legacy_unverified")) is False


def test_broker_passes_the_run_mode_through():
    src = open(os.path.join(_backend, "broker.py")).read()
    assert "allow_research=_research" in src
    assert '_opts.get("pit_mode")' in src
