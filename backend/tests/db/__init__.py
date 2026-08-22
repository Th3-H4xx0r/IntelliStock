"""Test package for ``backend/db``.

This directory has to be a package: later tests import their fixtures
relatively (``from .conftest import PG_TEST_DSN``), which only works inside a
package. But pytest's prepend import mode puts ``backend/tests`` on
``sys.path``, and the rest of the suite depends on that (many test modules
import sibling helpers by bare name). So a package here named ``db`` shadows
``backend/db`` — the package under test — and ``from db import json`` would
resolve to this directory instead.

Splice the real package in rather than fight it:

* ``pkgutil.extend_path`` appends every other ``db`` directory reachable from
  ``sys.path`` — i.e. ``backend/db`` — to our ``__path__``, so ``db.json``,
  ``db.store`` and friends resolve to the real modules while ``.conftest``
  still resolves here.
* Executing the real ``__init__.py`` in this namespace re-exports whatever it
  exports (``StoreError`` today, more later), so ``from db import <name>``
  behaves the same under pytest as it does in production.
"""
from __future__ import annotations

import os as _os
import pkgutil as _pkgutil

__path__ = _pkgutil.extend_path(__path__, __name__)

_REAL_INIT = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))),
    "db",
    "__init__.py",
)
with open(_REAL_INIT, "r", encoding="utf-8") as _fh:
    exec(compile(_fh.read(), _REAL_INIT, "exec"))
