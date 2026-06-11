"""PushDevices store — pure doc-builder (DB I/O is a thin wrapper)."""
from __future__ import annotations


def test_push_device_doc_shape():
    from interactive_utils import _push_device_doc
    doc = _push_device_doc(
        "user-1", "TOKENABC", platform="ios", env="sandbox",
        app_version="1.2.3", now_iso="2026-06-10T00:00:00Z",
    )
    # token is the primary key so register is idempotent
    assert doc["id"] == "TOKENABC"
    assert doc["device_token"] == "TOKENABC"
    assert doc["user_id"] == "user-1"
    assert doc["platform"] == "ios"
    assert doc["env"] == "sandbox"
    assert doc["app_version"] == "1.2.3"
    assert doc["created_at"] == "2026-06-10T00:00:00Z"
    assert doc["last_seen"] == "2026-06-10T00:00:00Z"


def test_push_device_doc_defaults():
    from interactive_utils import _push_device_doc
    doc = _push_device_doc("u", "t")
    assert doc["platform"] == "ios"
    assert doc["env"] == "prod"
    assert doc["app_version"] is None
    assert doc["created_at"]  # auto-stamped
