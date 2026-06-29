"""Action-layer tests for the preview endpoint + preserve-history re-stamp wiring."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import interactive_utils as iu
import nexus_restamp as nr

NEXUS = nr.NEXUS_STRATEGY_NAME


def test_preview_delegates_to_restamp(monkeypatch):
    captured = {}

    def fake_preview(conn, r, sid, strategies):
        captured["args"] = (conn, sid, strategies)
        return {"needs_prompt": True, "instances": []}

    monkeypatch.setattr(nr, "preview_change", fake_preview)
    out = iu.action_preview_strategy_config_change("CONN", "179",
                                                   [{"strategy": NEXUS, "config": {}}])
    assert out["needs_prompt"] is True
    assert captured["args"][1] == 179  # sid coerced to int
    assert captured["args"][2] == [{"strategy": NEXUS, "config": {}}]


def test_preview_rejects_bad_strategy_id():
    with pytest.raises(ValueError):
        iu.action_preview_strategy_config_change("CONN", "not-an-int", [])


def test_preview_rejects_non_list_strategies():
    with pytest.raises(ValueError):
        iu.action_preview_strategy_config_change("CONN", "179", {"strategy": NEXUS})


def test_apply_preserve_history_restamps_each_instance(monkeypatch):
    calls = []
    monkeypatch.setattr(nr, "resolve_for_identity", lambda conn, cfg: {"resolved": True})
    monkeypatch.setattr(nr, "linked_base_instance_ids",
                        lambda conn, r, sid: ["alpaca-main", "alpaca-second"])
    monkeypatch.setattr(nr, "restamp_instance",
                        lambda conn, r, base, resolved: calls.append(base) or {"base_instance_id": base})

    out = iu._apply_preserve_history("CONN", 179, [{"strategy": NEXUS, "config": {"x": 1}}])
    assert calls == ["alpaca-main", "alpaca-second"]
    assert [r["base_instance_id"] for r in out["restamp"]] == ["alpaca-main", "alpaca-second"]


def test_apply_preserve_history_never_raises(monkeypatch):
    def boom(conn, cfg):
        raise RuntimeError("models table down")

    monkeypatch.setattr(nr, "resolve_for_identity", boom)
    out = iu._apply_preserve_history("CONN", 179, [{"strategy": NEXUS, "config": {}}])
    assert "restamp_error" in out
    assert "models table down" in out["restamp_error"]
