"""Config validation at the write boundary.

These settings gate real money, and a settings form makes bad input far more
likely than a hand-edited document did. So the rejection happens here rather
than being left to the UI — and a bad value rejects the WHOLE patch, because a
form that silently applies three of five fields is worse than one that refuses.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from self_learning.store import ConfigError, _validated


def test_a_string_allowlist_is_refused():
    """Stored as "179" it iterates as the characters 1, 7 and 9 — arming three
    documents that do not exist while refusing the one that does."""
    with pytest.raises(ConfigError, match="each character"):
        _validated("document_allowlist", "179")


def test_an_allowlist_is_normalised_to_trimmed_strings():
    assert _validated("document_allowlist", [179, " 195 ", ""]) == ["179", "195"]


def test_a_negative_budget_is_refused():
    """A negative ceiling raises the limit rather than lowering it."""
    with pytest.raises(ConfigError, match="cannot be negative"):
        _validated("daily_budget_usd", -10)


def test_a_zero_budget_is_allowed_because_it_means_no_spending():
    assert _validated("daily_budget_usd", 0) == 0.0


def test_a_non_numeric_budget_is_refused():
    with pytest.raises(ConfigError, match="must be a number"):
        _validated("monthly_budget_usd", "lots")


def test_an_unknown_mode_is_refused():
    with pytest.raises(ConfigError):
        _validated("mode", "yolo")
    assert _validated("mode", " Act ") == "act"


def test_a_permission_matrix_with_an_unknown_mode_is_refused():
    """A typo must never widen permission."""
    with pytest.raises(ConfigError, match="unknown permission mode"):
        _validated("permission_matrix",
                   {"config_levers": {"LIVE_FULL": "autonomus"}})


def test_a_permission_matrix_with_an_unknown_rung_or_class_is_refused():
    with pytest.raises(ConfigError, match="unknown rung"):
        _validated("permission_matrix", {"config_levers": {"MOON": "ask"}})
    with pytest.raises(ConfigError, match="unknown action class"):
        _validated("permission_matrix", {"magic": {"PAPER": "ask"}})


def test_a_valid_permission_matrix_passes():
    matrix = {"config_levers": {"LIVE_FULL": "blocked"}}
    assert _validated("permission_matrix", matrix) == matrix


def test_a_variance_threshold_must_be_a_probability():
    with pytest.raises(ConfigError, match="between 0 and 1"):
        _validated("variance_threshold", 95)
    assert _validated("variance_threshold", 0.95) == 0.95


def test_counts_must_be_at_least_one():
    for key in ("retain_days", "variance_min_n", "demote_after"):
        with pytest.raises(ConfigError, match="at least 1"):
            _validated(key, 0)


def test_a_breaker_limit_of_zero_is_allowed_because_it_means_never_fire():
    assert _validated("breaker_limit_pct", 0) == 0.0
