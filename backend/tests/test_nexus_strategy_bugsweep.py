"""Tests for the 2026-05-07 strategy bug-sweep fixes:

1. Mcap fail-closed default (`quality_filter_missing_metadata_policy="block"`)
2. No-buy-on-negative-aggregated-raw-score in `_apply_quality_filter`
3. Propagation TTL pruner (`_prune_stale_propagation_discoveries`)
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest

_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from strategies.graph_nexus_analysis import (  # noqa: E402
    _apply_quality_filter,
    _get_effective_nexus_config,
    _prune_stale_propagation_discoveries,
)


# ─── Fix 1 + 2: quality filter ────────────────────────────────────────────


def _scoring_fixture(score=1, raw_net_score=0.4, market_cap=5_000_000_000, avg_volume=500_000, current_price=12.0):
    """Return a minimal scores dict for one ticker."""
    return {
        "BBGI": {
            "score": score,
            "raw_net_score": raw_net_score,
            "n_paths": 3,
            "quality_metadata": {
                "market_cap": market_cap,
                "avg_volume": avg_volume,
                "current_price": current_price,
            },
            "is_propagation_candidate": False,
            "reason": "",
        }
    }


def test_effective_config_reports_block_default_for_missing_meta_policy():
    """Preflight summary must match what the filter actually enforces."""
    cfg = _get_effective_nexus_config({})
    assert cfg["quality_filter_missing_metadata_policy"] == "block"


def test_quality_filter_blocks_buy_on_negative_aggregated_raw_score():
    """BBGI failure mode: score=1 with raw_net_score=-0.519 should be downgraded."""
    scores = _scoring_fixture(score=1, raw_net_score=-0.519)
    out = _apply_quality_filter(scores, ["BBGI"], None, None, None, {})
    assert out["BBGI"]["score"] == 0
    assert out["BBGI"]["action_intent"] == "hold"
    assert "aggregated raw_score=-0.519" in out["BBGI"]["reason"]


def test_quality_filter_allows_positive_raw_score_with_good_meta():
    """Sanity check: positive raw + good meta still passes."""
    scores = _scoring_fixture(score=1, raw_net_score=0.45)
    out = _apply_quality_filter(scores, ["BBGI"], None, None, None, {})
    assert out["BBGI"]["score"] == 1


def test_quality_filter_negative_raw_check_can_be_disabled():
    """Operators can opt out via config."""
    scores = _scoring_fixture(score=1, raw_net_score=-0.519)
    out = _apply_quality_filter(
        scores, ["BBGI"], None, None, None,
        {"quality_filter_block_negative_raw_score": False},
    )
    # With the block disabled, mcap+volume are good → buy survives.
    assert out["BBGI"]["score"] == 1


def test_quality_filter_blocks_missing_market_cap_by_default():
    """Default 'block' policy rejects buys on tickers with no market_cap."""
    scores = _scoring_fixture(score=1, raw_net_score=0.4, market_cap=None)
    # The fixture's quality_metadata still has the key but the filter
    # delegates lookup to _extract_quality_metadata. Pass None directly.
    scores["BBGI"]["quality_metadata"] = {
        "market_cap": None, "avg_volume": 500_000, "current_price": 12.0,
    }
    out = _apply_quality_filter(scores, ["BBGI"], None, None, None, {})
    assert out["BBGI"]["score"] == 0
    assert "missing market_cap metadata" in out["BBGI"]["reason"]


def test_quality_filter_warn_policy_lets_missing_meta_through():
    """Operators can revert to old behavior with warn policy."""
    scores = _scoring_fixture(score=1, raw_net_score=0.4)
    scores["BBGI"]["quality_metadata"] = {
        "market_cap": None, "avg_volume": 500_000, "current_price": 12.0,
    }
    out = _apply_quality_filter(
        scores, ["BBGI"], None, None, None,
        {"quality_filter_missing_metadata_policy": "warn"},
    )
    assert out["BBGI"]["score"] == 1


def test_quality_filter_only_processes_buys_not_sells():
    """Sells (score=-1) must not be touched by the negative-raw block."""
    scores = _scoring_fixture(score=-1, raw_net_score=-0.519)
    out = _apply_quality_filter(scores, ["BBGI"], None, None, None, {})
    assert out["BBGI"]["score"] == -1


# ─── Fix 3: propagation TTL pruner ────────────────────────────────────────


class _FakeStore:
    """Minimal db.store shim. Returns a pre-baked cursor for the scan and
    captures the bulk update's primary-key selector + payload. Real Postgres
    is not required for unit tests of this helper.
    """
    from db import store as _real

    Selection = _real.Selection
    coerce_id = staticmethod(_real.coerce_id)
    filter = staticmethod(_real.filter)

    def __init__(self, cursor_rows):
        self._cursor_rows = cursor_rows
        self.update_calls = []
        self.last_get_all_ids = None

    def run(self, _selection):
        return list(self._cursor_rows)

    def update(self, _table, selector, payload):
        # `selector` is the _by_ids Selection: one term, params = (keys,).
        self.last_get_all_ids = list(selector.terms[0][1][0])
        self.update_calls.append(payload)
        return self._real.WriteResult(replaced=len(self.last_get_all_ids))


def _make_doc(id_, source="propagation", discovered_date="2025-11-10", status="active"):
    return {
        "id": id_, "ticker": id_.upper(), "source": source,
        "discovered_date": discovered_date, "status": status,
    }


def test_prune_returns_zero_on_empty_cursor(monkeypatch):
    """No stale rows → returns 0, no update call."""
    fake = _FakeStore(cursor_rows=[])
    import strategies.graph_nexus_analysis as gna
    monkeypatch.setattr(gna, "store", fake)
    monkeypatch.setattr(gna, "_ensure_discovered_stocks_table", lambda c: None)
    out = _prune_stale_propagation_discoveries(
        conn="any", instance_id="main", today_iso="2026-05-07", max_age_days=30,
    )
    assert out == 0
    assert fake.update_calls == []


def test_prune_marks_stale_propagation_rows_as_expired(monkeypatch):
    """Stale propagation rows get bulk-updated to status=expired."""
    stale_rows = [
        _make_doc("p1", source="propagation", discovered_date="2025-11-10"),
        _make_doc("p2", source="propagation", discovered_date="2025-11-11"),
    ]
    fake = _FakeStore(cursor_rows=stale_rows)
    import strategies.graph_nexus_analysis as gna
    monkeypatch.setattr(gna, "store", fake)
    monkeypatch.setattr(gna, "_ensure_discovered_stocks_table", lambda c: None)
    out = _prune_stale_propagation_discoveries(
        conn="any", instance_id="main", today_iso="2026-05-07", max_age_days=30,
    )
    assert out == 2
    assert fake.last_get_all_ids == ["p1", "p2"]
    assert len(fake.update_calls) == 1
    payload = fake.update_calls[0]
    assert payload["status"] == "expired"
    assert payload["expired_date"] == "2026-05-07"
    assert "propagation_ttl_30d" in payload["expired_reason"]


def test_prune_handles_malformed_today_iso(monkeypatch):
    """Bad date string → return 0 cleanly."""
    fake = _FakeStore(cursor_rows=[])
    import strategies.graph_nexus_analysis as gna
    monkeypatch.setattr(gna, "store", fake)
    out = _prune_stale_propagation_discoveries(
        conn="any", instance_id="main", today_iso="not-a-date", max_age_days=30,
    )
    assert out == 0


def test_prune_returns_zero_when_conn_or_r_missing():
    """Defensive: missing conn returns 0."""
    out = _prune_stale_propagation_discoveries(
        conn=None, instance_id="main", today_iso="2026-05-07",
    )
    assert out == 0


def test_prune_clamps_negative_max_age_days(monkeypatch):
    """max_age_days <= 0 should not error; falls back to 1-day floor."""
    fake = _FakeStore(cursor_rows=[])
    import strategies.graph_nexus_analysis as gna
    monkeypatch.setattr(gna, "store", fake)
    monkeypatch.setattr(gna, "_ensure_discovered_stocks_table", lambda c: None)
    out = _prune_stale_propagation_discoveries(
        conn="any", instance_id="main", today_iso="2026-05-07", max_age_days=-5,
    )
    assert out == 0  # No rows in fixture; verifies no crash on negative input
