"""Smoke tests for the new operator scripts.

Both scripts open a RethinkDB connection at runtime; the tests only verify
that the modules import cleanly, expose a callable ``main()``, and have a
working ``--help`` argparse path.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script(rel_path: str):
    path = _REPO_ROOT / rel_path
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_inspect_broker_state_imports():
    module = _load_script("scripts/inspect_broker_state.py")
    assert hasattr(module, "main")
    assert callable(module.main)


def test_migrate_external_position_imports():
    module = _load_script("scripts/migrate_external_position.py")
    assert hasattr(module, "main")
    assert callable(module.main)


def test_migrate_external_position_derives_correct_cid_prefix():
    """Adoption-CID prefix must match broker_adapters._client_order_id.::_safe(8)."""
    module = _load_script("scripts/migrate_external_position.py")
    # Re-implemented locally to avoid backend/ import in this script
    assert module._derive_cid_prefix("main") == "main-"
    assert module._derive_cid_prefix("nexus-live") == "nexusliv-"
    assert module._derive_cid_prefix("nexus-testing") == "nexustes-"
    assert module._derive_cid_prefix("") == "x-"


def test_inspect_broker_state_help_runs():
    """--help should exit 0 without touching DB or broker."""
    proc = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "inspect_broker_state.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"--help should exit 0, got {proc.returncode} stderr={proc.stderr}"
    assert "--instance" in proc.stdout


def test_migrate_external_position_help_runs():
    proc = subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / "migrate_external_position.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "--instance" in proc.stdout and "--ticker" in proc.stdout
