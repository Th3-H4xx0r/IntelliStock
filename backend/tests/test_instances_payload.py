"""The instances list payload surfaces the `crashed` flag for the UI badge."""
from __future__ import annotations

from unittest.mock import MagicMock


def test_action_instances_includes_crashed(monkeypatch):
    import interactive_utils as iu
    monkeypatch.setattr(iu, "ensure_instances_table", lambda conn: None)
    rows = [
        {"id": "alpaca-main", "name": "Alpaca", "runCommand": True, "crashed": True},
        {"id": "paper", "name": "Paper", "runCommand": False},  # no crashed field
    ]
    # The store reads the table and plucks in Python, so the seam is the
    # scan itself rather than a ReQL chain.
    monkeypatch.setattr(iu.store, "run", lambda _t: list(rows))

    out = iu.action_instances(None)
    by_id = {i["id"]: i for i in out["instances"]}
    assert by_id["alpaca-main"]["crashed"] is True
    assert by_id["paper"]["crashed"] is False  # absent → defaults False
