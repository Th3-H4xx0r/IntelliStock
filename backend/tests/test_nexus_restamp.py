"""Re-stamp module tests. DB access is faked via the module's _fetch/_write helpers.

Imports nexus_restamp only (no broker import).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import nexus_restamp as nr
import nexus_config_identity as nci

NEXUS = nr.NEXUS_STRATEGY_NAME

# Change keys in BOTH identity namespaces so both hashes move:
#   live_config_hash reads stage keys (analyst_llm_model, ...);
#   history_scope_id reads root keys (llm_model, ...).
OLD_CFG = {
    "llm_provider": "bedrock", "llm_model": "kimi-k2.5",
    "analyst_llm_provider": "bedrock", "analyst_llm_model": "kimi-k2.5",
    "lookback_learning_days": 120,
}
NEW_CFG = {
    "llm_provider": "bedrock", "llm_model": "nemotron-3-ultra",
    "analyst_llm_provider": "bedrock", "analyst_llm_model": "nemotron-3-ultra",
    "lookback_learning_days": 120,
}


class _FakeR:
    """Stub `r` whose .now() is deterministic; other calls are unused in tests."""
    def now(self):
        return "NOW"


def _install_fake_db(monkeypatch, *, instances, snapshots, markers):
    """Wire the module's DB helpers to in-memory lists. Returns the live stores."""
    state = {"snapshots": list(snapshots), "markers": list(markers)}

    monkeypatch.setattr(nr, "_fetch_linked_base_instance_ids",
                        lambda conn, r, sid: list(instances))

    def _fetch_live(conn, r, base):
        return [dict(s) for s in state["snapshots"]
                if s["instance_id"] == base and s.get("origin") == "live"
                and s.get("config_hash") and s.get("end_date")]
    monkeypatch.setattr(nr, "_fetch_live_snapshot_rows", _fetch_live)

    def _write(conn, r, row):
        state["snapshots"] = [s for s in state["snapshots"] if s["id"] != row["id"]]
        state["snapshots"].append(dict(row))
    monkeypatch.setattr(nr, "_write_snapshot_row", _write)

    def _fetch_markers(conn, r, base):
        out = []
        for m in state["markers"]:
            iid = str(m.get("instance_id") or "")
            if iid == base or iid.startswith(base + "|"):
                out.append(dict(m))
        return out
    monkeypatch.setattr(nr, "_fetch_cleanup_markers", _fetch_markers)

    def _update_marker(conn, r, marker_id, new_scope):
        for m in state["markers"]:
            if m["id"] == marker_id:
                m["config_hash"] = new_scope
    monkeypatch.setattr(nr, "_update_marker_hash", _update_marker)

    return state


def _snap(base, config_hash, end_date="2026-06-28"):
    return {
        "id": f"{base}|{NEXUS}|{config_hash}|live|{end_date}",
        "instance_id": base, "strategy_name": NEXUS, "origin": "live",
        "config_hash": config_hash, "nexus_module_hash": "modabc",
        "end_date": end_date, "cache_json": "{}", "updated_at_epoch": 1.0,
    }


def _marker(scoped, config_hash):
    return {"id": f"cleanup_done|{scoped}", "instance_id": scoped, "config_hash": config_hash}


def test_restamp_rewrites_snapshot_under_base_id(monkeypatch):
    old_hash = nci.live_config_hash(OLD_CFG)
    new_hash = nci.live_config_hash(NEW_CFG)
    new_scope = nci.history_scope_id(NEW_CFG)
    scoped = f"alpaca-main|{nci.history_scope_id(OLD_CFG)}"
    state = _install_fake_db(
        monkeypatch,
        instances=["alpaca-main"],
        snapshots=[_snap("alpaca-main", old_hash)],
        markers=[_marker(scoped, nci.history_scope_id(OLD_CFG))],
    )
    out = nr.restamp_instance(None, _FakeR(), "alpaca-main", NEW_CFG)

    assert out["snapshots_restamped"] == 1
    assert out["markers_restamped"] == 1
    ids = [s["id"] for s in state["snapshots"]]
    assert f"alpaca-main|{NEXUS}|{new_hash}|live|2026-06-28" in ids
    new_row = next(s for s in state["snapshots"] if s["config_hash"] == new_hash)
    assert new_row["instance_id"] == "alpaca-main"  # base id, not scoped
    assert new_row["nexus_module_hash"] == "modabc"  # other fields preserved
    # marker now carries the new history_scope_id (keyed by scoped id)
    assert state["markers"][0]["config_hash"] == new_scope


def test_restamp_is_idempotent(monkeypatch):
    new_hash = nci.live_config_hash(NEW_CFG)
    new_scope = nci.history_scope_id(NEW_CFG)
    scoped = f"alpaca-main|{nci.history_scope_id(NEW_CFG)}"
    _install_fake_db(
        monkeypatch,
        instances=["alpaca-main"],
        snapshots=[_snap("alpaca-main", new_hash)],
        markers=[_marker(scoped, new_scope)],
    )
    out = nr.restamp_instance(None, _FakeR(), "alpaca-main", NEW_CFG)
    assert out["snapshots_restamped"] == 0
    assert out["markers_restamped"] == 0


def test_restamp_handles_multiple_scoped_markers(monkeypatch):
    old_hash = nci.live_config_hash(OLD_CFG)
    new_scope = nci.history_scope_id(NEW_CFG)
    state = _install_fake_db(
        monkeypatch,
        instances=["alpaca-main"],
        snapshots=[_snap("alpaca-main", old_hash)],
        markers=[
            _marker("alpaca-main|aaaa", "h1"),
            _marker("alpaca-main|bbbb", "h2"),
            _marker("other-instance|cccc", "h3"),  # must NOT be touched
        ],
    )
    out = nr.restamp_instance(None, _FakeR(), "alpaca-main", NEW_CFG)
    assert out["markers_restamped"] == 2
    by_id = {m["id"]: m for m in state["markers"]}
    assert by_id["cleanup_done|alpaca-main|aaaa"]["config_hash"] == new_scope
    assert by_id["cleanup_done|alpaca-main|bbbb"]["config_hash"] == new_scope
    assert by_id["cleanup_done|other-instance|cccc"]["config_hash"] == "h3"


def test_preview_needs_prompt_when_changed_and_snapshot_exists(monkeypatch):
    old_hash = nci.live_config_hash(OLD_CFG)
    scoped = f"alpaca-main|{nci.history_scope_id(OLD_CFG)}"
    _install_fake_db(
        monkeypatch,
        instances=["alpaca-main"],
        snapshots=[_snap("alpaca-main", old_hash)],
        markers=[_marker(scoped, nci.history_scope_id(OLD_CFG))],
    )
    monkeypatch.setattr(nr, "resolve_for_identity", lambda conn, cfg: NEW_CFG)
    out = nr.preview_change(None, _FakeR(), 179,
                            [{"strategy": NEXUS, "config": NEW_CFG}])
    assert out["needs_prompt"] is True
    assert out["instances"][0]["would_rebuild"] is True
    assert out["instances"][0]["snapshot_exists"] is True


def test_preview_no_prompt_when_unchanged(monkeypatch):
    new_hash = nci.live_config_hash(NEW_CFG)
    scoped = f"alpaca-main|{nci.history_scope_id(NEW_CFG)}"
    _install_fake_db(
        monkeypatch,
        instances=["alpaca-main"],
        snapshots=[_snap("alpaca-main", new_hash)],
        markers=[_marker(scoped, nci.history_scope_id(NEW_CFG))],
    )
    monkeypatch.setattr(nr, "resolve_for_identity", lambda conn, cfg: NEW_CFG)
    out = nr.preview_change(None, _FakeR(), 179,
                            [{"strategy": NEXUS, "config": NEW_CFG}])
    assert out["needs_prompt"] is False
    assert out["instances"][0]["would_rebuild"] is False


def test_preview_no_prompt_when_no_snapshot(monkeypatch):
    _install_fake_db(
        monkeypatch,
        instances=["fresh-instance"],
        snapshots=[],  # never ran -> nothing to preserve
        markers=[],
    )
    monkeypatch.setattr(nr, "resolve_for_identity", lambda conn, cfg: NEW_CFG)
    out = nr.preview_change(None, _FakeR(), 179,
                            [{"strategy": NEXUS, "config": NEW_CFG}])
    assert out["needs_prompt"] is False
    assert out["instances"][0]["snapshot_exists"] is False
