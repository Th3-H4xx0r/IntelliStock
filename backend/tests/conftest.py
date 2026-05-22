"""pytest conftest for backend/tests.

Production runs broker.py with ``backend/`` on sys.path, so internal modules
use bare imports (e.g., ``from strategy_cache_persistence import ...``).
When pytest runs from the repo root, ``backend/`` is NOT on sys.path by
default, which breaks any lazy bare-import a backend module performs at call
time. We mirror the production layout here.

Idempotent: each individual test file may also add backend/ to sys.path; the
``if`` guard makes this a no-op in that case.
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
