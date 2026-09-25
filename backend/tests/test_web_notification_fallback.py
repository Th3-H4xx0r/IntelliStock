"""FW item 5: the web settings page's fallback taxonomy (used when an older
API sends no `types`) lists the swing and wheel types exactly as
notification_types.py does: same keys, order, group, label and desc. The web
module is read with node; the test skips where node or the frontend is absent
(the backend image has neither)."""
import json
import os
import shutil
import subprocess
import sys

import pytest

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import notification_types  # noqa: E402

_FRONTEND = os.path.join(os.path.dirname(_BACKEND), "frontend")
_MODULE = os.path.join(_FRONTEND, "src", "utils", "notificationFallback.js")


def _web_fallback():
    if not os.path.isdir(_FRONTEND):
        pytest.skip("frontend/ is not checked out here")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = ("import { FALLBACK_TYPES } from %s; "
              "console.log(JSON.stringify(FALLBACK_TYPES))" % json.dumps("file://" + _MODULE))
    out = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True,
                         text=True, timeout=60, check=True)
    return json.loads(out.stdout)


def test_the_web_fallback_lists_every_swing_and_wheel_type_as_the_backend_does():
    backend = [t for t in notification_types.public_types() if t["group"] == "Swing & Wheel"]
    assert len(backend) == 9
    web = [t for t in _web_fallback() if t["group"] == "Swing & Wheel"]
    assert web == backend


def test_the_web_fallback_keys_are_all_real_types():
    keys = [t["key"] for t in _web_fallback()]
    assert len(keys) == len(set(keys))
    assert set(keys) <= set(notification_types.NOTIFICATION_TYPE_KEYS)
