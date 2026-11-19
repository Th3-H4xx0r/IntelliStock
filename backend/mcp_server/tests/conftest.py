"""Shared fixtures, and the import isolation every test in this suite runs under.

The image's build context is backend/mcp_server alone, so the suite must not be
able to import anything else from backend/. At import time this module puts a
fresh temporary directory holding one symlink, mcp_server -> backend/mcp_server,
at the front of sys.path, then this tests/ directory (helper modules import as
top-level `fake_api` and `webauthn_fake`). backend/ itself is never on sys.path,
so an accidental `import db` fails here exactly as it would in the container.

pytest.ini selects --import-mode=importlib, which keeps pytest's own hands off
sys.path. Under the default prepend mode, pytest imports the package root's
__init__.py when it sets up the package node and puts backend/ at sys.path[0]
to do so.
"""
import atexit
import base64
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_ISOLATED = tempfile.mkdtemp(prefix="mcp-path-")
_LINK = os.path.join(_ISOLATED, "mcp_server")
os.symlink(_TESTS_DIR.parent, _LINK, target_is_directory=True)
sys.path.insert(0, str(_TESTS_DIR))
sys.path.insert(0, _ISOLATED)
# importlib mode registers this file as "tests.conftest"; alias it so a test's
# `from conftest import helper` gets this module rather than a second copy.
sys.modules.setdefault("conftest", sys.modules[__name__])

import mcp_server  # noqa: E402,F401  (resolve the package through the isolated path, once)


def _remove_isolated_path():
    # unlink + rmdir rather than rmtree: never walk through the symlink into the source tree.
    try:
        os.unlink(_LINK)
        os.rmdir(_ISOLATED)
    except OSError:
        pass


atexit.register(_remove_isolated_path)


class FakeClock:
    """Float seconds when called; moves only when advance() is called.

    Starts at the real time.time(), because the SDK's bearer backend compares a
    token's expires_at against the real clock.
    """

    def __init__(self, start=None):
        self._now = time.time() if start is None else float(start)

    def __call__(self):
        return self._now

    def advance(self, seconds):
        self._now += float(seconds)
        return self._now


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def env(tmp_path):
    """A complete, valid MCP environment; tests mutate their own copy."""
    return {
        "MCP_PUBLIC_BASE": "https://s7.tail.ts.net:8443",
        "MCP_RP_ID": "s7.tail.ts.net",
        "MCP_API_BASE": "http://api:8012",
        "MCP_SERVICE_TOKEN": "test-service-token-" + "0123456789abcdef" * 2,
        "MCP_DATA_DIR": str(tmp_path),
        "MCP_JARVIS_CLIENT_ID": "jarvis",
        "MCP_JARVIS_REDIRECT_URI": "http://127.0.0.1:33418/callback",
        "MCP_TOTP_ENC_KEY": base64.b64encode(bytes(range(32))).decode("ascii"),
        "MCP_LOG_LEVEL": "info",
    }


@pytest.fixture
def settings(env):
    from mcp_server.config import load_settings

    return load_settings(env)
