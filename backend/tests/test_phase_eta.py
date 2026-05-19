"""Phase η (propagation enrichment + V31 sector cap swap) tests.

Reference: docs/superpowers/specs/2026-05-20-eta-propagation-enrichment-design.md

Covers:
  * η.A — momentum_watchlist → sentiment_data seeding
  * η.B' — post-aggregation sector-peer augmentation
  * η.D — V28.9 HIGH-tier-in-grace protection
  * η.E — priority floor differentiator
  * η.G — V31 sector cap conditional swap
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
for _p in (_BACKEND, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.strategies.graph_nexus_analysis as gna


@pytest.fixture(autouse=True)
def _reset_gn_live_flag():
    prev = gna._GN_LIVE_MODE_FLAG
    gna._GN_LIVE_MODE_FLAG = False
    yield
    gna._GN_LIVE_MODE_FLAG = prev


def _capture_logs():
    logs: list[tuple[str, str]] = []

    def _fake_log(msg, color=""):
        logs.append((str(msg), str(color or "")))

    return logs, patch.object(gna, "_log", side_effect=_fake_log)


def test_eta_scaffold_imports_ok():
    """Smoke — module imports and fixtures load."""
    assert gna is not None
    assert hasattr(gna, "_compute_propagated_scores")
