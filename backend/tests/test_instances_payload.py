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
    fake_r = MagicMock()
    fake_r.db.return_value.table.return_value.pluck.return_value.run.return_value = iter(rows)
    monkeypatch.setattr(iu, "r", fake_r)

    out = iu.action_instances(MagicMock())
    by_id = {i["id"]: i for i in out["instances"]}
    assert by_id["alpaca-main"]["crashed"] is True
    assert by_id["paper"]["crashed"] is False  # absent → defaults False
