"""Per-category notification preferences store — pure logic (no DB needed).

Defaults preserve today's behavior: every category routes to Discord only
(discord=True, push=False) until the operator opts a category into push.
"""
from __future__ import annotations

import pytest


def test_default_categories_cover_all_types_discord_on_push_opt_in():
    from interactive_utils import (
        NOTIFICATION_CATEGORIES,
        _default_notification_categories,
    )
    from notification_types import _PUSH_ON_BY_DEFAULT
    cats = _default_notification_categories()
    assert set(cats.keys()) == set(NOTIFICATION_CATEGORIES)
    # the 9 live-alert categories plus the broader taxonomy
    assert len(NOTIFICATION_CATEGORIES) >= 9
    assert "order_fill" in cats and "broker_boot" in cats
    # Discord on for all; push opt-in for all EXCEPT the curated push-on set.
    for key, v in cats.items():
        assert v["discord"] is True
        assert v["push"] is (key in _PUSH_ON_BY_DEFAULT)
    assert cats["instance_crash"] == {"discord": True, "push": True}


def test_merge_fills_missing_and_ignores_unknown_stored_keys():
    from interactive_utils import _merge_notification_categories
    stored = {
        "order_fill": {"discord": False, "push": True},
        "bogus_category": {"discord": True, "push": True},  # ignored
    }
    merged = _merge_notification_categories(stored)
    assert merged["order_fill"] == {"discord": False, "push": True}
    # untouched categories keep defaults
    assert merged["halt"] == {"discord": True, "push": False}
    assert "bogus_category" not in merged
    from interactive_utils import NOTIFICATION_CATEGORIES
    assert len(merged) == len(NOTIFICATION_CATEGORIES)


def test_merge_handles_none():
    from interactive_utils import _merge_notification_categories, _default_notification_categories
    assert _merge_notification_categories(None) == _default_notification_categories()


def test_validate_rejects_unknown_category():
    from interactive_utils import _validate_notification_categories
    with pytest.raises(ValueError):
        _validate_notification_categories({"not_a_category": {"discord": True}})


def test_validate_coerces_booleans_and_fills_defaults():
    from interactive_utils import _validate_notification_categories
    cleaned = _validate_notification_categories(
        {"order_fill": {"discord": 0, "push": 1}}
    )
    assert cleaned["order_fill"] == {"discord": False, "push": True}
    # categories not provided fall back to defaults
    assert cleaned["crash_loop"] == {"discord": True, "push": False}
    from interactive_utils import NOTIFICATION_CATEGORIES
    assert len(cleaned) == len(NOTIFICATION_CATEGORIES)
