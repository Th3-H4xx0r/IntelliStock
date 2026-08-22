# Postgres Port — Plan A: `backend/db` Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `backend/db` package — pool, canonical JSON, ReQL-compatible deep merge, the 125-table schema registry, the typed store API, and the LISTEN/NOTIFY change watchers — plus the test harness (`FakeStore`, `scripts/dev_pg.sh`, the shared `conftest.py` fixture) that every later call-site port depends on.

**Architecture:** Seven modules under `backend/db/` with a strict acyclic dependency order (`errors` → `json` → `merge` → `pool` → `schema` → `store` → `watch`). Tables stay `(id text PRIMARY KEY, doc jsonb, updated_at timestamptz)` and keep their RethinkDB names 1:1. Secondary indexes become STORED generated columns plus B-trees under `COLLATE "C"`. Change notification is a per-row `pg_notify` trigger carrying only the id, consumed by watchers that re-read on start and on every reconnect with a 2 s poll backstop.

**Tech Stack:** Python 3.11 (prod) / 3.14 (this laptop), psycopg 3 + psycopg_pool, PostgreSQL 17, pytest, Hypothesis.

**Spec:** `docs/superpowers/specs/2026-08-22-postgres-port-design.md`

**Supporting evidence:** `docs/investigations/DB-REPLACEMENT-2026-08-22.md`, and the repo inventory (call-site census + live DB read) at `/private/tmp/claude-501/-Users-pranavkrishna-PranavFiles-coding-projects-IntelliStock/9425583a-f23f-4ff5-829f-1fa3c762d70a/scratchpad/dbresearch/01-repo-inventory.md`.

## Global Constraints

- **The user's invariant, binding on every decision:** *"Keep all functionality the same completely as using rethinkdb but just different db."* Same JSON shapes, same key sets, same orderings, same failure modes.
- **Python 3.11 compatible.** Prod image is `python:3.11-slim` (`backend/Dockerfile:2`). No `match` guards beyond 3.11, no PEP 695 `type` statements, no `typing.override`. `from __future__ import annotations` at the top of every new module so `X | None` annotations are safe everywhere.
- **`COLLATE "C"` on every text column and every `ORDER BY` over text.** `graph_nexus_analysis.py:11856`'s `(latest_observation_date DESC, id DESC)` tiebreak decides membership of an 80-row window that lands in an LLM prompt.
- **No GIN indexes anywhere.** `WHERE doc->>'k' = v` cannot use GIN and `fastupdate=on` produces multi-hundred-ms insert stalls.
- **`STORED` is written explicitly** on every generated column. PG 18 makes `VIRTUAL` the default and virtual columns cannot be indexed.
- **No `rethinkdb` import in runtime code.** Nothing in `backend/db/` may import it, directly or lazily.
- **Never SQL `BETWEEN`.** ReQL ranges are `[lo, hi)`; SQL `BETWEEN` is `[lo, hi]`.
- **Never `||` for a document merge.** `||` is shallow and silently drops sibling keys; `jsonb_deep_merge` is the only merge.
- **Table names are quoted everywhere** (`"BacktestResults"`, `"kalshi_decisions"`) and preserved byte-for-byte from RethinkDB.
- **Tests run as** `python3 -m pytest backend/tests` from the repo root. `python3` (not `python`) is required on this machine, and some suites need the stub `socketio` module already on the path via `backend/tests/conftest.py`'s `sys.path` insert.
- **Dependency pin:** `psycopg[binary,pool]>=3.2.10,<4`. The floor is the `notifies()` memory-leak fix. Watchers use `notifies()` only, never `add_notify_handler()`.
- **Commit trailer on every commit in this plan:**
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
  ```
- **Before editing any existing symbol**, run `gitnexus_impact({target: "<symbol>", direction: "upstream"})` and report the blast radius; escalate HIGH/CRITICAL rather than overriding. Run `gitnexus_detect_changes()` before each commit.

---

## File Structure

**Created by this plan:**

| file | responsibility |
|---|---|
| `backend/db/__init__.py` | re-exports `store`, `Literal`, `P`, and the error classes |
| `backend/db/errors.py` | `StoreError`, `ConflictError`, `UnavailableError`, `CasFailed` |
| `backend/db/json.py` | `dumps`/`loads` with `allow_nan=False`, `install()`, `canonical()`, `canonical_sha256()` |
| `backend/db/merge.py` | `Literal`, `deep_merge()`, `encode_patch()` — pure, imports nothing from `db` except `errors` |
| `backend/db/pool.py` | one `ConnectionPool` per process, fork-safe, `connection()`/`cursor()`/`listen_connection()` |
| `backend/db/schema.py` | `TableSpec` registry for all 125 tables, `ensure_schema()`, `ensure_table()`, DDL for `jsonb_deep_merge` and `notify_row()` |
| `backend/db/store.py` | the typed API every call site uses; `Selection`, `Predicate`, `P`, `InsertResult`, `WriteResult` |
| `backend/db/watch.py` | `Watcher`, `feed()`, `watch_row()`, `watch_table()`, `watch_filter()` |
| `backend/db/fake.py` | `FakeStore` — the same API over Python dicts, for laptops with no database |
| `scripts/dev_pg.sh` | throwaway local cluster: `up` / `dsn` / `psql` / `down` / `nuke` |
| `backend/tests/db/__init__.py` … `test_*.py` | the new suite (§6.2 of the spec) |

**Modified by this plan:**

| file | change |
|---|---|
| `backend/rethink_changefeed.py` | `run_reconnecting_changefeed` reimplemented on `watch.feed`; `is_transient_db_error` added, old name aliased |
| `backend/tests/conftest.py` | adds the shared `store` fixture (real PG when `PG_TEST_DSN` is set, else `FakeStore`) |
| `backend/requirements.txt` | adds `psycopg[binary,pool]>=3.2.10,<4` |
| `.gitignore` | adds `.devpg/` |

**Out of scope for Plan A:** every call-site port (spec §10.2 groups A–O), the migration script, docker-compose, and the `BacktestResults` split writers/readers (Plan B).

---

## Interface Summary (what Plan B and the port groups consume)

```python
from db import store, Literal, P
from db.errors import StoreError, ConflictError, UnavailableError, CasFailed

store.get(table: str, row_id: Any) -> dict | None
store.get_all(table: str, *keys: Any, index: str | None = None) -> list[dict]
store.insert(table: str, doc_or_docs, *, conflict: str = "error", durability: str = "hard") -> InsertResult
store.update(table: str, selector, patch: dict) -> WriteResult
store.replace(table: str, row_id: Any, doc: dict) -> WriteResult
store.replace_if(table: str, row_id: Any, *, when, doc: dict, insert_if_absent: bool = False) -> dict | None
store.delete(table: str, selector) -> WriteResult
store.between(table, lo, hi, *, index=None, left_bound="closed", right_bound="open") -> Selection
store.filter(table: str, predicate) -> Selection
store.pluck(rows_or_selection, *fields: str) -> list[dict]
store.order_by(selection, *, index=None, fields=(), desc=False) -> Selection
store.limit(selection, n: int) -> Selection
store.slice(selection, start: int, end: int) -> Selection
store.count(table_or_selection) -> int
store.run(selection) -> list[dict]
store.iter(selection, *, batch: int = 1000) -> Iterator[dict]
store.sql(query: str, params: Sequence[Any] | Mapping[str, Any] = ()) -> list[dict]
store.table_list() -> list[str]
store.table_create(name: str, *, primary_key: str = "id") -> bool
store.index_list(table: str) -> list[str]
store.asc(field: str, *, numeric: bool = False) -> Order
store.desc(field: str, *, numeric: bool = False) -> Order
```

---

## Task 1: Errors and canonical JSON

**Files:**
- Create: `backend/db/__init__.py`
- Create: `backend/db/errors.py`
- Create: `backend/db/json.py`
- Create: `backend/tests/db/__init__.py`
- Create: `backend/tests/db/test_json.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `db.errors.StoreError(Exception)`, `ConflictError(StoreError)`, `UnavailableError(StoreError)`, `CasFailed(StoreError)`; `db.json.dumps(obj) -> str`, `db.json.loads(s) -> Any`, `db.json.install() -> None`, `db.json.canonical(value) -> str`, `db.json.canonical_sha256(value) -> str`.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_json.py`:

```python
import math
import pytest

from db import json as dbjson


def test_dumps_is_compact_and_stable():
    assert dbjson.dumps({"b": 1, "a": 2}) == '{"b":1,"a":2}'


def test_dumps_rejects_nan():
    with pytest.raises(ValueError):
        dbjson.dumps({"x": float("nan")})


def test_dumps_rejects_infinity():
    with pytest.raises(ValueError):
        dbjson.dumps({"x": math.inf})


def test_loads_roundtrips():
    assert dbjson.loads('{"a":[1,2,{"b":null}]}') == {"a": [1, 2, {"b": None}]}


def test_canonical_sorts_keys_at_every_depth():
    assert dbjson.canonical({"b": {"z": 1, "a": 2}, "a": 3}) == \
        '{"a": 3, "b": {"a": 2, "z": 1}}'


def test_canonical_is_key_order_invariant():
    left = {"a": 1, "b": {"c": 2, "d": 3}}
    right = {"b": {"d": 3, "c": 2}, "a": 1}
    assert dbjson.canonical_sha256(left) == dbjson.canonical_sha256(right)


def test_canonical_sha256_is_a_hex_digest():
    digest = dbjson.canonical_sha256({"a": 1})
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_canonical_rejects_nan():
    with pytest.raises(ValueError):
        dbjson.canonical({"x": float("nan")})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_json.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Write minimal implementation**

`backend/db/errors.py`:

```python
"""Exception hierarchy for the Postgres store.

Every failure the store surfaces is a ``StoreError`` or a subclass, so call
sites that used to catch RethinkDB driver exceptions have one class to catch.
"""
from __future__ import annotations


class StoreError(Exception):
    """Any store-level failure: bad input, a rejected write, a query error."""


class ConflictError(StoreError):
    """A primary-key conflict that ``conflict='error'`` did not absorb."""


class UnavailableError(StoreError):
    """The database could not be reached after the connection retry budget."""


class CasFailed(StoreError):
    """A compare-and-swap predicate did not hold. Raised only when the caller
    asks for it; ``store.replace_if`` returns ``None`` instead by default."""
```

`backend/db/json.py`:

```python
"""Canonical JSON encoding for the store.

Two jobs:

1. ``dumps``/``loads`` are what psycopg uses for every jsonb parameter and
   result. ``allow_nan=False`` makes NaN/Infinity raise ``ValueError`` at the
   client, mirroring RethinkDB's client-side rejection, instead of surfacing
   as a server-side "invalid input syntax for type json" three layers away.
2. ``canonical``/``canonical_sha256`` are the single hashing entry point.
   jsonb reorders keys and renormalises numbers (``1.230e-5`` becomes
   ``0.00001230``), so a fingerprint taken over raw bytes is not stable across
   a round trip. Canonicalising first makes it stable.
"""
from __future__ import annotations

import hashlib
import json as _json
from typing import Any


def dumps(value: Any) -> str:
    """Compact JSON. Raises ValueError on NaN/Infinity."""
    return _json.dumps(value, allow_nan=False, separators=(",", ":"))


def loads(value: str | bytes) -> Any:
    return _json.loads(value)


def canonical(value: Any) -> str:
    """Key-sorted JSON, used for hashing and for byte-comparison in tests."""
    return _json.dumps(value, sort_keys=True, allow_nan=False)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


_INSTALLED = False


def install() -> None:
    """Point psycopg's jsonb adapters at our dumps/loads. Idempotent.

    Called once from ``pool.get_pool()`` so nothing has to remember to call it.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    from psycopg.types.json import set_json_dumps, set_json_loads
    set_json_dumps(dumps)
    set_json_loads(loads)
    _INSTALLED = True
```

`backend/db/__init__.py`:

```python
"""Postgres store package. Nothing outside this package opens a connection."""
from __future__ import annotations

from .errors import CasFailed, ConflictError, StoreError, UnavailableError

__all__ = ["CasFailed", "ConflictError", "StoreError", "UnavailableError"]
```

`backend/tests/db/__init__.py`: empty file.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/db/test_json.py -v`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add backend/db/__init__.py backend/db/errors.py backend/db/json.py \
        backend/tests/db/__init__.py backend/tests/db/test_json.py
git commit -m "$(cat <<'EOF'
feat(db): canonical JSON + store error hierarchy

allow_nan=False so NaN is rejected at the client the way RethinkDB rejects
it, not as a server-side json syntax error. canonical_sha256 is the single
hashing entry point so fingerprints survive jsonb key reordering.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 2: ReQL-compatible deep merge (Python side)

**Files:**
- Create: `backend/db/merge.py`
- Create: `backend/tests/db/test_merge_python.py`

**Interfaces:**
- Consumes: `db.errors.StoreError`.
- Produces: `db.merge.Literal(value)` (attribute `.value`, `__eq__`, `__repr__`); `db.merge.deep_merge(base, patch) -> Any`; `db.merge.encode_patch(patch) -> Any`; `db.merge.LITERAL_KEY = "__db_literal__"`.

`merge.py` must not import `pool`, `schema`, or `store` — it is pure and property-tested standalone against the SQL twin in Task 6.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_merge_python.py`:

```python
import pytest

from db.errors import StoreError
from db.merge import LITERAL_KEY, Literal, deep_merge, encode_patch


def test_objects_merge_recursively():
    base = {"a": {"b": 1, "c": 2}, "d": 3}
    patch = {"a": {"c": 9, "e": 5}}
    assert deep_merge(base, patch) == {"a": {"b": 1, "c": 9, "e": 5}, "d": 3}


def test_arrays_replace_wholesale():
    assert deep_merge({"a": [1, 2, 3]}, {"a": [9]}) == {"a": [9]}


def test_scalar_replaces_object():
    assert deep_merge({"a": {"b": 1}}, {"a": 5}) == {"a": 5}


def test_none_sets_json_null_it_does_not_delete():
    assert deep_merge({"a": 1}, {"a": None}) == {"a": None}


def test_missing_intermediates_are_created():
    # ReQL creates them; jsonb_set does not. This is the trap.
    assert deep_merge({}, {"a": {"b": {"c": 1}}}) == {"a": {"b": {"c": 1}}}


def test_literal_sets_shallow_without_merging():
    base = {"cfg": {"keep": 1, "drop": 2}}
    assert deep_merge(base, {"cfg": Literal({"only": 3})}) == {"cfg": {"only": 3}}


def test_literal_empty_dict_blanks_a_subtree():
    # purge_backtest_secrets.py:101-103 needs exactly this.
    assert deep_merge({"s": {"k": "secret"}}, {"s": Literal({})}) == {"s": {}}


def test_literal_at_the_root_replaces_the_document():
    assert deep_merge({"a": 1}, Literal({"b": 2})) == {"b": 2}


def test_base_non_object_is_replaced_by_patch_object():
    assert deep_merge(5, {"a": 1}) == {"a": 1}


def test_base_none_yields_the_patch():
    assert deep_merge(None, {"a": 1}) == {"a": 1}


def test_deep_merge_does_not_mutate_its_inputs():
    base = {"a": {"b": 1}}
    patch = {"a": {"c": 2}}
    deep_merge(base, patch)
    assert base == {"a": {"b": 1}} and patch == {"a": {"c": 2}}


def test_encode_patch_rewrites_literal_to_the_wire_sentinel():
    assert encode_patch({"a": Literal({"b": 1})}) == {"a": {LITERAL_KEY: {"b": 1}}}


def test_encode_patch_recurses_into_nested_objects_and_arrays():
    got = encode_patch({"a": {"b": [Literal(1), 2]}})
    assert got == {"a": {"b": [{LITERAL_KEY: 1}, 2]}}


def test_encode_patch_rejects_a_real_key_named_like_the_sentinel():
    with pytest.raises(StoreError):
        encode_patch({LITERAL_KEY: 1})


def test_deep_merge_unwraps_the_wire_sentinel_too():
    # The Python side must agree with the SQL side on encoded patches.
    assert deep_merge({"a": {"b": 1}}, {"a": {LITERAL_KEY: {"c": 2}}}) == {"a": {"c": 2}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_merge_python.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.merge'`

- [ ] **Step 3: Write minimal implementation**

`backend/db/merge.py`:

```python
"""ReQL-compatible deep merge.

``.update({...})`` in RethinkDB merges nested objects recursively and replaces
arrays and scalars wholesale. 149 call sites depend on that, and the codebase
has already been burned once by the semantics (purge_backtest_secrets.py:101).
``r.literal(v)`` opts out: it sets the subtree shallow instead of merging into
it.

This module is pure. Its SQL twin ``jsonb_deep_merge`` lives in schema.py and
is proved equivalent by the Hypothesis property test in
backend/tests/db/test_merge_property.py.
"""
from __future__ import annotations

from typing import Any

from .errors import StoreError

LITERAL_KEY = "__db_literal__"


class Literal:
    """ReQL ``r.literal()``: replace the subtree, do not merge into it."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Literal) and other.value == self.value

    def __hash__(self) -> int:  # pragma: no cover - Literal is rarely hashed
        return hash(("Literal", repr(self.value)))

    def __repr__(self) -> str:
        return "Literal(%r)" % (self.value,)


def _is_sentinel(value: Any) -> bool:
    return isinstance(value, dict) and len(value) == 1 and LITERAL_KEY in value


def deep_merge(base: Any, patch: Any) -> Any:
    """Merge ``patch`` into ``base`` with ReQL ``update`` semantics.

    Objects merge recursively. Arrays and scalars replace. ``None`` sets JSON
    null (it does NOT delete the key). Missing intermediate objects are
    created. ``Literal(v)`` and the wire sentinel ``{"__db_literal__": v}``
    both set ``v`` shallow.
    """
    if isinstance(patch, Literal):
        return patch.value
    if _is_sentinel(patch):
        return patch[LITERAL_KEY]
    if not isinstance(base, dict) or not isinstance(patch, dict):
        # A patch that is not an object replaces whatever was there, and an
        # object patch onto a non-object base replaces it too.
        return _strip(patch)
    out = dict(base)
    for key, value in patch.items():
        if key in out:
            out[key] = deep_merge(out[key], value)
        else:
            # Not present in base: still unwrap literals and create the subtree.
            out[key] = deep_merge({}, value) if _mergeable(value) else _strip(value)
    return out


def _mergeable(value: Any) -> bool:
    return isinstance(value, dict) and not _is_sentinel(value)


def _strip(value: Any) -> Any:
    """Unwrap Literal/sentinel markers anywhere inside a replacement value."""
    if isinstance(value, Literal):
        return _strip(value.value)
    if _is_sentinel(value):
        return _strip(value[LITERAL_KEY])
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def encode_patch(patch: Any) -> Any:
    """Rewrite ``Literal(v)`` into ``{"__db_literal__": v}`` so a patch can
    travel to Postgres as one jsonb parameter."""
    if isinstance(patch, Literal):
        return {LITERAL_KEY: encode_patch(patch.value)}
    if isinstance(patch, dict):
        if LITERAL_KEY in patch:
            raise StoreError(
                "document key %r collides with the r.literal wire sentinel" % LITERAL_KEY
            )
        return {k: encode_patch(v) for k, v in patch.items()}
    if isinstance(patch, list):
        return [encode_patch(v) for v in patch]
    return patch
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/db/test_merge_python.py -v`
Expected: PASS, 15 tests

- [ ] **Step 5: Commit**

```bash
git add backend/db/merge.py backend/tests/db/test_merge_python.py
git commit -m "$(cat <<'EOF'
feat(db): ReQL-compatible deep_merge with r.literal support

Objects merge recursively, arrays and scalars replace, None sets JSON null,
missing intermediates are created (ReQL does; jsonb_set does not). Literal(v)
sets shallow and Literal({}) blanks a subtree, which is what
purge_backtest_secrets.py:101-103 needs.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 3: Local Postgres, dependency pin, `.gitignore`

**Files:**
- Create: `scripts/dev_pg.sh`
- Modify: `backend/requirements.txt`
- Modify: `.gitignore`
- Create: `backend/tests/db/test_dependency_pin.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a running Postgres 17 and an exported `PG_TEST_DSN`. Every later task's real-database tests skip without it.

**Environment facts for the implementer:** Homebrew `postgresql@17` binaries are at `/opt/homebrew/opt/postgresql@17/bin` and the service is **not** running. There is **no Docker on this Mac**. `psycopg 3.3.4` and `psycopg_pool 3.3.1` are already importable under Python 3.14.5. `backend/Dockerfile` needs no edit — it already runs `pip install --no-cache-dir -r requirements.txt`, so adding the pin to `backend/requirements.txt` is the whole change. `rethinkdb` **stays** in `requirements.txt` for now; it is removed only when the last call site is ported, which is out of this plan's scope.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_dependency_pin.py`:

```python
import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_REQ = os.path.join(_REPO, "backend", "requirements.txt")
_DOCKERFILE = os.path.join(_REPO, "backend", "Dockerfile")


def test_requirements_pin_psycopg_with_binary_and_pool_extras():
    text = open(_REQ, encoding="utf-8").read()
    assert re.search(r"^psycopg\[binary,pool\]>=3\.2\.10,<4\s*$", text, re.M), \
        "psycopg[binary,pool]>=3.2.10,<4 must be pinned: <3.2.10 leaks memory in notifies()"


def test_dockerfile_installs_from_requirements_so_no_separate_pin_is_needed():
    text = open(_DOCKERFILE, encoding="utf-8").read()
    assert "pip install --no-cache-dir -r requirements.txt" in text


def test_dev_pg_script_is_executable():
    path = os.path.join(_REPO, "scripts", "dev_pg.sh")
    assert os.path.exists(path) and os.access(path, os.X_OK)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_dependency_pin.py -v`
Expected: FAIL on the pin assertion and on the missing `scripts/dev_pg.sh`.

- [ ] **Step 3: Write minimal implementation**

Append to `backend/requirements.txt` (leave `rethinkdb` in place — it is removed at cutover, not here):

```
# Postgres store (backend/db). The >=3.2.10 floor is the notifies() memory-leak
# fix; watchers use notifies() exclusively. [pool] pulls psycopg_pool, which
# pool.py needs; [binary] avoids needing libpq headers in the slim image.
psycopg[binary,pool]>=3.2.10,<4
```

Append to `.gitignore`:

```
# Throwaway local Postgres cluster (scripts/dev_pg.sh)
.devpg/
```

`scripts/dev_pg.sh` (`chmod +x` it):

```bash
#!/usr/bin/env bash
# Throwaway PostgreSQL 17 cluster for local tests. No root, no Docker.
#
#   ./scripts/dev_pg.sh up     initdb into .devpg/, start on a free port,
#                              create the DB, print PG_TEST_DSN
#   ./scripts/dev_pg.sh dsn    echo PG_TEST_DSN
#   ./scripts/dev_pg.sh psql   open a shell on it
#   ./scripts/dev_pg.sh down   stop the cluster
#   ./scripts/dev_pg.sh nuke   stop and delete .devpg/
#
# Usage:  export PG_TEST_DSN="$(./scripts/dev_pg.sh dsn)"
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGROOT="$REPO_ROOT/.devpg"
PGDATA="$PGROOT/data"
PGLOG="$PGROOT/postgres.log"
PORTFILE="$PGROOT/port"
DBNAME="intellistock_test"

find_bindir() {
  # Homebrew postgresql@17 first; it is binaries-only and we run our own
  # cluster, never Homebrew's service.
  for cand in \
      /opt/homebrew/opt/postgresql@17/bin \
      /usr/local/opt/postgresql@17/bin \
      /usr/lib/postgresql/17/bin; do
    if [ -x "$cand/initdb" ]; then echo "$cand"; return 0; fi
  done
  if command -v initdb >/dev/null 2>&1; then dirname "$(command -v initdb)"; return 0; fi
  # Fallback: pgserver vendors a Postgres binary inside a wheel.
  local pgs
  pgs="$(python3 -c 'import pgserver,os;print(os.path.join(os.path.dirname(pgserver.__file__),"pginstall","bin"))' 2>/dev/null || true)"
  if [ -n "$pgs" ] && [ -x "$pgs/initdb" ]; then echo "$pgs"; return 0; fi
  echo "ERROR: no PostgreSQL 17 binaries found." >&2
  echo "  brew install postgresql@17     (preferred)" >&2
  echo "  pip install pgserver           (fallback)" >&2
  return 1
}

free_port() {
  python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

BINDIR="$(find_bindir)"
export PATH="$BINDIR:$PATH"

cmd_up() {
  mkdir -p "$PGROOT"
  if [ ! -f "$PGDATA/PG_VERSION" ]; then
    echo "initdb -> $PGDATA (using $BINDIR)"
    initdb -D "$PGDATA" -U postgres --encoding=UTF8 \
           --locale=C --lc-collate=C --lc-ctype=C >/dev/null
  fi
  if pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
    echo "already running on port $(cat "$PORTFILE")"
  else
    local port; port="$(free_port)"; echo "$port" > "$PORTFILE"
    pg_ctl -D "$PGDATA" -l "$PGLOG" \
      -o "-p $port -k $PGROOT -c listen_addresses=127.0.0.1 -c timezone=UTC" \
      -w start
    createdb -h 127.0.0.1 -p "$port" -U postgres "$DBNAME" 2>/dev/null || true
  fi
  local port; port="$(cat "$PORTFILE")"
  # Risk #4 in the spec: lz4 availability is unverified. Report, never assume.
  local comp
  comp="$(psql -h 127.0.0.1 -p "$port" -U postgres -d "$DBNAME" -tAc \
          'SHOW default_toast_compression' 2>/dev/null || echo unknown)"
  echo "default_toast_compression = $comp  (lz4 wanted; pglz costs disk, not correctness)"
  echo "PG_TEST_DSN=$(cmd_dsn)"
}

cmd_dsn() {
  [ -f "$PORTFILE" ] || { echo "not started; run: $0 up" >&2; return 1; }
  echo "postgresql://postgres@127.0.0.1:$(cat "$PORTFILE")/$DBNAME"
}

cmd_psql() { psql "$(cmd_dsn)"; }
cmd_down() { pg_ctl -D "$PGDATA" -m fast -w stop || true; }
cmd_nuke() { cmd_down; rm -rf "$PGROOT"; echo "removed $PGROOT"; }

case "${1:-}" in
  up) cmd_up ;;
  dsn) cmd_dsn ;;
  psql) cmd_psql ;;
  down) cmd_down ;;
  nuke) cmd_nuke ;;
  *) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 2 ;;
esac
```

- [ ] **Step 4: Run the script and the tests**

```bash
chmod +x scripts/dev_pg.sh
./scripts/dev_pg.sh up
export PG_TEST_DSN="$(./scripts/dev_pg.sh dsn)"
psql "$PG_TEST_DSN" -c 'select version()'
python3 -m pytest backend/tests/db/test_dependency_pin.py -v
```
Expected: `up` prints a `default_toast_compression` line and a DSN; `select version()` reports PostgreSQL 17.x; 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/dev_pg.sh backend/requirements.txt .gitignore \
        backend/tests/db/test_dependency_pin.py
git commit -m "$(cat <<'EOF'
build(db): local throwaway PG17 cluster + psycopg pin

dev_pg.sh runs its own cluster in .devpg/ (never Homebrew's service, no
Docker on this machine) with --locale=C so the default collation matches the
COLLATE "C" the store depends on. psycopg[binary,pool]>=3.2.10 is the
notifies() memory-leak floor. rethinkdb stays pinned until the last call site
is ported.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 4: The connection pool

**Files:**
- Create: `backend/db/pool.py`
- Create: `backend/tests/db/conftest.py`
- Create: `backend/tests/db/test_pool_fork.py`

**Interfaces:**
- Consumes: `db.json.install()`, `db.errors.UnavailableError`.
- Produces:
  ```python
  db.pool.DEFAULT_MIN_SIZE = 1
  db.pool.DEFAULT_MAX_SIZE = 8                    # env PG_POOL_MAX
  db.pool.dsn_from_env() -> str
  db.pool.get_pool(dsn: str | None = None) -> ConnectionPool
  db.pool.connection(*, autocommit: bool = False)  # contextmanager -> Connection
  db.pool.cursor(*, autocommit: bool = False)      # contextmanager -> Cursor, row_factory=dict_row
  db.pool.listen_connection() -> Connection        # unpooled, autocommit; caller closes
  db.pool.reset_after_fork() -> None
  db.pool.close_pool() -> None
  db.pool.health() -> dict                         # {"ok": bool, "size": int, "dsn_host": str}
  ```

**Why fork safety matters here:** the server forks a broker subprocess, the backtest engine spawns containers, and FastAPI runs workers. Postgres connections are not process-safe — a child that inherits a parent's socket and writes to it corrupts both sides.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/conftest.py` (fixtures shared by every real-PG test in this suite):

```python
"""Real-Postgres fixtures for backend/tests/db.

Every test here needs a database. Without PG_TEST_DSN they skip: only a real
Postgres can prove collation, jsonb_deep_merge, and LISTEN/NOTIFY.
Each test gets its own Postgres *schema* so tests never share state.
"""
import os
import uuid

import pytest

PG_TEST_DSN = os.environ.get("PG_TEST_DSN")
requires_pg = pytest.mark.skipif(
    not PG_TEST_DSN, reason="PG_TEST_DSN not set (run: ./scripts/dev_pg.sh up)")


@pytest.fixture
def pg_schema():
    """Create a throwaway schema, point the pool's search_path at it, drop it."""
    if not PG_TEST_DSN:
        pytest.skip("PG_TEST_DSN not set")
    from db import pool as dbpool
    name = "t_" + uuid.uuid4().hex[:16]
    dbpool.close_pool()
    os.environ["PG_DSN"] = PG_TEST_DSN
    os.environ["PG_SEARCH_PATH"] = name
    with dbpool.connection(autocommit=True) as conn:
        conn.execute('CREATE SCHEMA IF NOT EXISTS "%s"' % name)
    dbpool.close_pool()          # reopen so every pooled conn gets the new path
    try:
        yield name
    finally:
        os.environ.pop("PG_SEARCH_PATH", None)
        dbpool.close_pool()
        with dbpool.connection(autocommit=True) as conn:
            conn.execute('DROP SCHEMA IF EXISTS "%s" CASCADE' % name)
        dbpool.close_pool()
```

`backend/tests/db/test_pool_fork.py`:

```python
import os

import pytest

from db import pool as dbpool
from db.errors import UnavailableError

from .conftest import requires_pg


def test_dsn_from_env_prefers_pg_dsn(monkeypatch):
    monkeypatch.setenv("PG_DSN", "postgresql://u@h:5555/dbx")
    assert dbpool.dsn_from_env() == "postgresql://u@h:5555/dbx"


def test_dsn_from_env_assembles_from_parts_with_defaults(monkeypatch):
    monkeypatch.delenv("PG_DSN", raising=False)
    for k in ("POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_USER",
              "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.delenv(k, raising=False)
    dsn = dbpool.dsn_from_env()
    assert "host=localhost" in dsn and "port=5432" in dsn
    assert "user=intellistock" in dsn and "dbname=IntelliStock" in dsn


def test_dsn_from_env_includes_password_when_set(monkeypatch):
    monkeypatch.delenv("PG_DSN", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "s3cret")
    assert "password=s3cret" in dbpool.dsn_from_env()


def test_unreachable_host_raises_unavailable_not_a_driver_error(monkeypatch):
    monkeypatch.setenv("PG_DSN", "postgresql://postgres@127.0.0.1:1/nope")
    monkeypatch.setenv("PG_CONNECT_RETRIES", "0")
    dbpool.close_pool()
    with pytest.raises(UnavailableError):
        with dbpool.connection():
            pass
    dbpool.close_pool()


@requires_pg
def test_get_pool_is_idempotent_per_process(pg_schema):
    assert dbpool.get_pool() is dbpool.get_pool()


@requires_pg
def test_cursor_returns_dict_rows(pg_schema):
    with dbpool.cursor() as cur:
        cur.execute("SELECT 1 AS a, 'x' AS b")
        assert cur.fetchone() == {"a": 1, "b": "x"}


@requires_pg
def test_connections_run_in_utc(pg_schema):
    with dbpool.cursor() as cur:
        cur.execute("SHOW timezone")
        assert cur.fetchone()["TimeZone"] == "UTC"


@requires_pg
def test_health_reports_ok_and_the_host(pg_schema):
    h = dbpool.health()
    assert h["ok"] is True and isinstance(h["size"], int) and h["dsn_host"]


@requires_pg
def test_listen_connection_is_autocommit_and_unpooled(pg_schema):
    conn = dbpool.listen_connection()
    try:
        assert conn.autocommit is True
        conn.execute("LISTEN some_channel")
    finally:
        conn.close()


@requires_pg
def test_forked_child_does_not_inherit_a_usable_pool(pg_schema):
    """The child must build its own pool, not write to the parent's socket."""
    parent_pool = dbpool.get_pool()
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:                                   # child
        code = 1
        try:
            child_pool = dbpool.get_pool()
            with dbpool.cursor() as cur:
                cur.execute("SELECT 42 AS n")
                ok = cur.fetchone()["n"] == 42
            code = 0 if (ok and child_pool is not parent_pool) else 2
        except Exception:
            code = 3
        finally:
            os.write(write_fd, b"x")
            os._exit(code)
    os.close(write_fd)
    os.read(read_fd, 1)
    _, status = os.waitpid(pid, 0)
    os.close(read_fd)
    assert os.WEXITSTATUS(status) == 0
    # The parent's pool must still work after the child exits.
    with dbpool.cursor() as cur:
        cur.execute("SELECT 7 AS n")
        assert cur.fetchone()["n"] == 7
```

- [ ] **Step 2: Run test to verify it fails**

```bash
export PG_TEST_DSN="$(./scripts/dev_pg.sh dsn)"
python3 -m pytest backend/tests/db/test_pool_fork.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'db.pool'`

- [ ] **Step 3: Write minimal implementation**

`backend/db/pool.py`:

```python
"""One psycopg_pool.ConnectionPool per process, created lazily after fork.

Postgres connections are not process-safe. Every process that forks (server ->
broker subprocess, backtest engine -> container, FastAPI workers) must not
inherit a live pool, so the pool is created on first use, never at import, and
``os.register_at_fork`` drops the child's inherited object without closing the
parent's sockets.

Cursors are not thread-safe; connections are. The pool hands out one
connection per operation via a context manager, so no cursor crosses a thread
boundary. Watchers get their own dedicated autocommit connection OUTSIDE the
pool -- a LISTEN session must never sit in a pooled connection or inside a
long transaction.
"""
from __future__ import annotations

import contextlib
import os
import threading
import time
from typing import Any, Iterator, Optional

from . import json as dbjson
from .errors import UnavailableError

DEFAULT_MIN_SIZE = 1
DEFAULT_MAX_SIZE = 8            # env PG_POOL_MAX

_pool: Optional[Any] = None      # psycopg_pool.ConnectionPool
_pool_pid: Optional[int] = None
_lock = threading.RLock()


def dsn_from_env() -> str:
    """PG_DSN wins; otherwise assemble from the POSTGRES_* parts."""
    dsn = os.environ.get("PG_DSN")
    if dsn:
        return dsn
    parts = [
        "host=%s" % os.environ.get("POSTGRES_HOST", "localhost"),
        "port=%s" % os.environ.get("POSTGRES_PORT", "5432"),
        "user=%s" % os.environ.get("POSTGRES_USER", "intellistock"),
        "dbname=%s" % os.environ.get("POSTGRES_DB", "IntelliStock"),
    ]
    password = os.environ.get("POSTGRES_PASSWORD")
    if password:
        parts.append("password=%s" % password)
    return " ".join(parts)


def _options() -> str:
    opts = ["-c timezone=UTC", "-c default_transaction_isolation=read committed"]
    search_path = os.environ.get("PG_SEARCH_PATH")
    if search_path:
        # Test isolation: each test runs in its own schema.
        opts.append("-c search_path=%s,public" % search_path)
    return " ".join(opts)


def get_pool(dsn: str | None = None):
    """Idempotent per process. Rebuilds if the pid changed (post-fork)."""
    global _pool, _pool_pid
    with _lock:
        if _pool is not None and _pool_pid == os.getpid():
            return _pool
        if _pool is not None and _pool_pid != os.getpid():
            _pool = None          # inherited across a fork: abandon, never close
        dbjson.install()
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
        _pool = ConnectionPool(
            conninfo=dsn or dsn_from_env(),
            min_size=int(os.environ.get("PG_POOL_MIN", DEFAULT_MIN_SIZE)),
            max_size=int(os.environ.get("PG_POOL_MAX", DEFAULT_MAX_SIZE)),
            kwargs={"options": _options(), "row_factory": dict_row},
            open=True,
            timeout=float(os.environ.get("PG_POOL_TIMEOUT", "30")),
        )
        _pool_pid = os.getpid()
        return _pool


_RETRY_DELAYS = (0.5, 1.5)


@contextlib.contextmanager
def connection(*, autocommit: bool = False) -> Iterator[Any]:
    """Check a connection out of the pool.

    A connection-level failure is retried twice (0.5s, 1.5s) before raising
    UnavailableError -- the same shape as today's
    ``broker.py:1897 get_conn_retry(max_attempts, delay)``, whose call sites
    keep their own outer loops. Query-level errors are NEVER retried: a
    retried non-idempotent write is worse than an error.
    """
    import psycopg
    from psycopg_pool import PoolTimeout

    budget = int(os.environ.get("PG_CONNECT_RETRIES", str(len(_RETRY_DELAYS))))
    attempt = 0
    while True:
        try:
            pool = get_pool()
            with pool.connection() as conn:
                if autocommit and not conn.autocommit:
                    conn.autocommit = True
                yield conn
            return
        except (psycopg.OperationalError, PoolTimeout, OSError) as exc:
            if attempt >= budget:
                raise UnavailableError("postgres unavailable: %s" % exc) from exc
            time.sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)])
            attempt += 1
            close_pool()


@contextlib.contextmanager
def cursor(*, autocommit: bool = False) -> Iterator[Any]:
    """A dict-row cursor on a pooled connection."""
    from psycopg.rows import dict_row
    with connection(autocommit=autocommit) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            yield cur


def listen_connection():
    """A dedicated, unpooled, autocommit connection for watch.py.

    Never pooled: a LISTEN session must outlive any single operation, and a
    full 8 GB notify queue fails commits on whatever transaction is open.
    The caller closes it.
    """
    import psycopg
    from psycopg.rows import dict_row
    dbjson.install()
    try:
        conn = psycopg.connect(dsn_from_env(), options=_options(),
                               row_factory=dict_row, autocommit=True)
    except Exception as exc:
        raise UnavailableError("listen connection failed: %s" % exc) from exc
    return conn


def reset_after_fork() -> None:
    """Drop the child's inherited pool object WITHOUT closing the parent's
    sockets. Registered with os.register_at_fork(after_in_child=...)."""
    global _pool, _pool_pid
    _pool = None
    _pool_pid = None


def close_pool() -> None:
    global _pool, _pool_pid
    with _lock:
        pool, _pool, _pool_pid = _pool, None, None
    if pool is not None:
        try:
            pool.close()
        except Exception:
            pass


def health() -> dict:
    try:
        pool = get_pool()
        with cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            cur.fetchone()
        stats = pool.get_stats()
        size = int(stats.get("pool_size", 0))
        ok = True
    except Exception:
        size, ok = 0, False
    dsn = dsn_from_env()
    host = "unknown"
    for token in dsn.replace("postgresql://", " ").replace("@", " ").split():
        if token.startswith("host="):
            host = token[5:]
    return {"ok": ok, "size": size, "dsn_host": host}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=reset_after_fork)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/db/test_pool_fork.py -v`
Expected: PASS, 10 tests. Then confirm the skip path works:
`env -u PG_TEST_DSN python3 -m pytest backend/tests/db/test_pool_fork.py -v` → 4 pass, 6 skip.

- [ ] **Step 5: Commit**

```bash
git add backend/db/pool.py backend/tests/db/conftest.py backend/tests/db/test_pool_fork.py
git commit -m "$(cat <<'EOF'
feat(db): fork-safe per-process connection pool

Created lazily on first use, never at import, with register_at_fork dropping
the child's inherited pool object without closing the parent's sockets.
Connection-level failures retry twice (0.5s/1.5s) then raise UnavailableError;
query errors are never retried. listen_connection() is deliberately unpooled
and autocommit -- a LISTEN session must not sit in a pooled connection.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 5: Table registry and `ensure_schema()` — default tables

**Files:**
- Create: `backend/db/schema.py`
- Create: `backend/tests/db/test_schema_ensure.py`

**Interfaces:**
- Consumes: `db.pool.connection()`, `db.errors.StoreError`.
- Produces:
  ```python
  db.schema.PartitionSpec(by: str, interval: str, premake: int = 3)
  db.schema.RetentionSpec(field: str, days_env: str, default_days: int | None = None)
  db.schema.TableSpec(
      name: str, id_type: str = "text", pk_field: str = "id",
      pk: tuple[str, ...] = ("id",),
      indexed_fields: tuple[str, ...] = (),
      prefix_fields: tuple[str, ...] = (),
      generated: Mapping[str, tuple[str, str]] = {},   # col -> (sql_type, expression)
      compound_indexes: Mapping[str, str] = {},        # index name -> SQL expression
      time_fields: tuple[str, ...] = (),
      partitioned: PartitionSpec | None = None,
      retention: RetentionSpec | None = None,
      notify: bool = True, tune_autovacuum: bool = False,
      ddl: str | None = None)
  db.schema.ALL_TABLES: tuple[str, ...]        # all 125 names, live-verified
  db.schema.TABLES: dict[str, TableSpec]       # the ~40 that need more than the default
  db.schema.spec(table: str) -> TableSpec      # unknown table -> TableSpec(name=table)
  db.schema.ensure_schema(*, tables: Iterable[str] | None = None) -> list[str]
  db.schema.ensure_table(table: str) -> None
  db.schema.quoted(name: str) -> str
  ```

**Registry source of truth.** `ALL_TABLES` is the live `table_list()` read (125 names). The secondary indexes come from the live `index_list()` read; the non-`id` primary keys from the live `table_config` read, which agrees with `backend/kalshi/db.py:21-52 KALSHI_TABLES`. Both are reproduced verbatim in the code below — do not re-derive them.

**Three interface decisions this task locks in** (see the Ambiguities section at the end of this plan): `TableSpec.generated` for expression-defined generated columns (`tickers_total`, `created_at`, `PriceHistory.ticker`/`ts`), `TableSpec.prefix_fields` for the `text_pattern_ops` companion index, and `TableSpec.ddl` for the three tables whose shape is not the default template.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_schema_ensure.py`:

```python
import threading

import pytest

from db import schema
from db import pool as dbpool

from .conftest import requires_pg


def test_all_tables_has_the_125_live_tables():
    assert len(schema.ALL_TABLES) == 125
    assert len(set(schema.ALL_TABLES)) == 125
    for name in ("BacktestResults", "PriceHistory", "GraphNexusTradeContexts",
                 "kalshi_decisions", "sports_fixtures", "Users"):
        assert name in schema.ALL_TABLES


def test_spec_of_an_unregistered_table_is_the_default_template():
    s = schema.spec("DiscordOutbox")
    assert s.name == "DiscordOutbox" and s.id_type == "text"
    assert s.pk_field == "id" and s.indexed_fields == () and s.notify is True


def test_non_id_primary_keys_match_the_live_table_config():
    live = {
        "KalshiHistFixtures": "fixture_key",
        "kalshi_capital_plan": "instance_id",
        "kalshi_market_listings": "fixture_id",
        "kalshi_markets": "market_ticker",
        "kalshi_orders": "client_order_id",
        "kalshi_scan_budget": "window",
        "lineups": "fixture_id",
        "match_features": "fixture_id",
        "sports_fixtures": "fixture_id",
    }
    for table, pk_field in live.items():
        assert schema.spec(table).pk_field == pk_field
    others = {n: s.pk_field for n, s in schema.TABLES.items()
              if s.pk_field != "id" and n not in live}
    assert others == {}, "unexpected non-id primary key: %r" % others


def test_the_eight_high_volume_tables_have_notifications_off():
    for name in ("PriceHistory", "AlpacaBarsCache", "GraphNexusLLMPromptCache",
                 "LLMUsage", "GraphNexusNewsLLMMacro", "GraphNexusNewsLLMCompany",
                 "GraphNexusNewsDayFeatures", "GraphNexusOutcomeSeries"):
        assert schema.spec(name).notify is False


def test_int_id_tables_are_declared():
    for name in ("BacktestResults", "BacktestInstances", "Instances", "Strategies"):
        assert schema.spec(name).id_type == "int"


def test_backtest_results_compound_indexes_reproduce_the_reql_lambdas():
    ci = schema.spec("BacktestResults").compound_indexes
    assert set(ci) == {"instance_or_instance_id", "list_ts", "instance_ts"}
    for expr in ci.values():
        assert 'COLLATE "C"' in expr


@requires_pg
def test_ensure_schema_creates_the_default_table_shape(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
            (pg_schema, "DiscordOutbox"))
        cols = [(r["column_name"], r["data_type"]) for r in cur.fetchall()]
    assert cols == [("id", "text"), ("doc", "jsonb"),
                    ("updated_at", "timestamp with time zone")]


@requires_pg
def test_ensure_schema_creates_generated_columns_and_btrees(pg_schema):
    schema.ensure_schema(tables=["GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        cur.execute(
            "SELECT column_name, is_generated FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s", (pg_schema, "GraphNexusTradeContexts"))
        gen = {r["column_name"]: r["is_generated"] for r in cur.fetchall()}
        cur.execute("SELECT indexname FROM pg_indexes "
                    "WHERE schemaname=%s AND tablename=%s", (pg_schema, "GraphNexusTradeContexts"))
        idx = {r["indexname"] for r in cur.fetchall()}
    assert gen.get("instance_id") == "ALWAYS"
    assert gen.get("base_instance_id") == "ALWAYS"
    assert "GraphNexusTradeContexts_instance_id_idx" in idx
    assert "GraphNexusTradeContexts_instance_id_pfx" in idx
    assert "GraphNexusTradeContexts_instance_base_idx" in idx


@requires_pg
def test_generated_columns_are_stored_not_virtual(pg_schema):
    # PG18 defaults to VIRTUAL and virtual columns cannot be indexed.
    schema.ensure_schema(tables=["GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        cur.execute("SELECT attgenerated FROM pg_attribute "
                    "WHERE attrelid = %s::regclass AND attname = 'instance_id'",
                    ('"%s"."GraphNexusTradeContexts"' % pg_schema,))
        assert cur.fetchone()["attgenerated"] == "s"


@requires_pg
def test_generated_text_columns_use_collate_c(pg_schema):
    schema.ensure_schema(tables=["GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        cur.execute(
            "SELECT c.collname FROM pg_attribute a "
            "JOIN pg_collation c ON c.oid = a.attcollation "
            "WHERE a.attrelid = %s::regclass AND a.attname = 'instance_id'",
            ('"%s"."GraphNexusTradeContexts"' % pg_schema,))
        assert cur.fetchone()["collname"] == "C"


@requires_pg
def test_no_gin_index_is_ever_created(pg_schema):
    schema.ensure_schema()
    with dbpool.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_am am ON am.oid = c.relam "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND am.amname = 'gin'", (pg_schema,))
        assert cur.fetchone()["n"] == 0


@requires_pg
def test_notify_trigger_installed_only_where_notify_is_true(pg_schema):
    schema.ensure_schema(tables=["Instances", "LLMUsage"])
    with dbpool.cursor() as cur:
        cur.execute("SELECT tgname, tgrelid::regclass::text AS tbl FROM pg_trigger "
                    "WHERE NOT tgisinternal")
        names = {r["tgname"] for r in cur.fetchall()}
    assert "Instances_notify" in names
    assert "LLMUsage_notify" not in names


@requires_pg
def test_ensure_schema_is_idempotent(pg_schema):
    first = schema.ensure_schema()
    assert first, "first run must report created objects"
    second = schema.ensure_schema()
    assert second == [], "second run must create nothing: %r" % second


@requires_pg
def test_ensure_schema_is_concurrent_safe(pg_schema):
    errors = []

    def run():
        try:
            schema.ensure_schema()
        except Exception as exc:      # pragma: no cover - the failure we guard
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)
    assert errors == []


@requires_pg
def test_ensure_table_creates_one_table_on_demand(pg_schema):
    schema.ensure_table("GraphNexusLLMPromptCache")
    with dbpool.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) AS t",
                    ('"%s"."GraphNexusLLMPromptCache"' % pg_schema,))
        assert cur.fetchone()["t"] is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_schema_ensure.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.schema'`

- [ ] **Step 3: Write minimal implementation**

`backend/db/schema.py` — part 1, the types and the registry:

```python
"""Table registry and idempotent DDL.

Tables keep their RethinkDB names 1:1 and the default shape
``(id text PRIMARY KEY COLLATE "C", doc jsonb NOT NULL, updated_at timestamptz)``.
Every field that is a secondary index, a filter key, or a prefix-scan key
today becomes a STORED generated column plus a B-tree under COLLATE "C".

ALL_TABLES is the live ``table_list()`` (125 names, read 2026-08-22).
TABLES holds an entry only where a table needs something beyond the default:
a non-id primary key, an index, a partition, a retention policy, an int id,
time fields, or suppressed notifications. The other ~85 tables are created
from the default template by name, with no entry to maintain.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

from . import pool as dbpool
from .errors import StoreError


@dataclass(frozen=True)
class PartitionSpec:
    by: str                       # column name, e.g. "ts"
    interval: str = "1 month"     # pg_partman
    premake: int = 3


@dataclass(frozen=True)
class RetentionSpec:
    field: str                    # doc key or column holding the timestamp
    days_env: str                 # env var naming the retention window
    default_days: Optional[int] = None   # None => retention OFF by default

    def days(self) -> Optional[int]:
        raw = os.environ.get(self.days_env)
        if raw is None or raw == "":
            return self.default_days
        try:
            return max(1, int(raw))
        except ValueError:
            raise StoreError("%s must be an integer, got %r" % (self.days_env, raw))


@dataclass(frozen=True)
class TableSpec:
    name: str
    id_type: str = "text"                       # "text" | "int"
    pk_field: str = "id"                        # doc key RethinkDB used as the PK
    pk: tuple = ("id",)                         # real columns forming the SQL PK
    indexed_fields: tuple = ()                  # doc keys -> STORED text col + btree
    prefix_fields: tuple = ()                   # subset that also gets text_pattern_ops
    generated: Mapping[str, tuple] = field(default_factory=dict)
                                                # col -> (sql_type, expression)
    compound_indexes: Mapping[str, str] = field(default_factory=dict)
                                                # index name -> SQL expression over doc
    time_fields: tuple = ()                     # doc keys decoded back to datetime on read
    partitioned: Optional[PartitionSpec] = None
    retention: Optional[RetentionSpec] = None
    notify: bool = True
    tune_autovacuum: bool = False
    ddl: Optional[str] = None                   # full CREATE TABLE, overrides the template


# The live table_list() of the IntelliStock database, read 2026-08-22.
ALL_TABLES: tuple = (
    "AIBacktestingResults", "AgentBest", "AgentCycleLog", "AgentTop5",
    "AlpacaBarsCache", "AlphaState", "BacktestInstances", "BacktestResults",
    "BotTradeDecisions", "BrokerageAccounts", "ChatbotConversations", "Config",
    "DiscordMessageIds", "DiscordOutbox", "DiscoverPriceCache", "DiscoverStocks",
    "EarningsLLMCache", "EarningsLLMPromptCache", "EngineControl", "GoogleNewsCache",
    "GraphNexusActiveEventHistory", "GraphNexusActiveEventMaintenance",
    "GraphNexusActiveEvents", "GraphNexusAnalystPanel", "GraphNexusBenzingaCache",
    "GraphNexusDiscoveredStocks", "GraphNexusDiscoverySnapshots",
    "GraphNexusLLMPromptCache", "GraphNexusLearningCache", "GraphNexusMarketTrends",
    "GraphNexusNewsCache", "GraphNexusNewsDayFeatures", "GraphNexusNewsFinBERT",
    "GraphNexusNewsLLMCompany", "GraphNexusNewsLLMGoogle", "GraphNexusNewsLLMMacro",
    "GraphNexusNewsRaw", "GraphNexusOutcomeSeries", "GraphNexusOutcomes",
    "GraphNexusOverlayBarsCache", "GraphNexusOverlayResultCache", "GraphNexusProgress",
    "GraphNexusRotationCooldown", "GraphNexusTickerHistory", "GraphNexusTradeContexts",
    "GraphNexusTradeOutcomes", "Instances", "KalshiBacktestResults", "KalshiBacktests",
    "KalshiBtFixtureList", "KalshiHistCandles", "KalshiHistFixtures", "KalshiHistOdds",
    "KalshiModelRegistry", "LLMUsage", "LLMUsageDaily", "LearningActiveChanges",
    "LearningActivity", "LearningApprovals", "LearningBudgetLedger", "LearningConfig",
    "LearningEngineStatus", "LearningExperiments", "LearningFindings", "LearningFunnels",
    "LearningHypotheses", "LearningIntents", "LearningLease", "LearningNoiseFloors",
    "LearningObservationRollups", "LearningObservations", "LearningOutcomes",
    "LearningReports", "LiveBootAudit", "LiveCommands", "LiveDecisionAudit",
    "LiveOrderLifecycle", "LiveOrderWAL", "LivePrices", "LivePricesStocks", "LiveState",
    "MlNewsLLMPromptCache", "Models", "NewsLLM", "NewsLLMCache", "NewsLLMPromptCache",
    "NewsRaw", "NewsScored", "NexusGraphBuilds", "NexusRuntimeState",
    "NexusStrategyCache", "NotificationPreferences", "PointInTimeDatasetSnapshots",
    "PointInTimeManifests", "PriceHistory", "PushDevices", "Stocks", "Strategies",
    "TickerDayFeatures", "Users", "backtest_replay_calls",
    "backtest_replay_fixture_builds", "backtest_replay_fixtures",
    "backtest_replay_matrices", "backtest_replay_receipts", "h2h_history",
    "kalshi_capital_plan", "kalshi_clv_log", "kalshi_decisions", "kalshi_edge_history",
    "kalshi_edges", "kalshi_fills", "kalshi_live", "kalshi_market_listings",
    "kalshi_markets", "kalshi_odds_snapshots", "kalshi_orders",
    "kalshi_portfolio_snapshots", "kalshi_positions", "kalshi_scan_budget", "lineups",
    "match_features", "player_stats", "sports_fixtures", "team_stats",
)

_C = ' COLLATE "C"'


def _instance_coalesce() -> str:
    return "(coalesce(doc->>'instance_id', doc->>'instance', ''))" + _C


_SPECS = [
    # ---- BacktestResults and its split companions (Plan B) -------------------
    TableSpec(
        "BacktestResults", id_type="int",
        indexed_fields=("status", "instance_id", "instance", "backtest_id",
                        "start_date", "end_date", "started_at", "created_at",
                        "timestamp", "completed_at"),
        generated={"tickers_total": ("integer",
                                     "coalesce(jsonb_array_length(doc->'tickers'), 0)")},
        compound_indexes={
            "instance_or_instance_id": _instance_coalesce(),
            "list_ts": "(coalesce(doc->>'timestamp',''))" + _C,
            "instance_ts": _instance_coalesce() + ", (coalesce(doc->>'timestamp',''))" + _C,
        },
        tune_autovacuum=True),
    TableSpec(
        "BacktestSteps", notify=False,
        ddl='''CREATE TABLE IF NOT EXISTS "BacktestSteps" (
  backtest_id text COLLATE "C" NOT NULL,
  kind        text COLLATE "C" NOT NULL,
  seq         bigint NOT NULL,
  final       boolean NOT NULL DEFAULT false,
  doc         jsonb NOT NULL,
  PRIMARY KEY (backtest_id, kind, final, seq)
)''',
        tune_autovacuum=True),
    # NOTE: Plan B Task 2 replaces this DDL with a payload-jsonb version --
    # a double precision column would turn a stopped run's integer progress 0
    # into 0.0 and break byte-identical assembly. The status_norm index below
    # is unchanged by that amendment.
    TableSpec(
        "BacktestProgress",
        ddl='''CREATE TABLE IF NOT EXISTS "BacktestProgress" (
  id                   text PRIMARY KEY COLLATE "C",
  status               text COLLATE "C",
  progress             double precision,
  time_elapsed_seconds integer,
  last_active          timestamptz,
  updated_at           timestamptz NOT NULL DEFAULT now()
)''',
        compound_indexes={
            "status_norm":
                "((CASE WHEN lower(status) LIKE 'paused%' THEN 'paused' "
                "      ELSE lower(status) END) COLLATE \"C\")",
        }),
    TableSpec("BacktestInstances", id_type="int", indexed_fields=("instance",)),
    TableSpec("Instances", id_type="int"),
    TableSpec("Strategies", id_type="int"),

    # ---- PriceHistory: real columns, partitioned ----------------------------
    TableSpec(
        "PriceHistory", pk=("ticker", "ts", "id"), notify=False,
        partitioned=PartitionSpec(by="ts", interval="1 month"),
        retention=RetentionSpec(field="ts", days_env="RETAIN_PRICE_HISTORY_DAYS"),
        tune_autovacuum=True,
        ddl='''CREATE TABLE IF NOT EXISTS "PriceHistory" (
  ticker      text COLLATE "C" NOT NULL,
  ts          timestamptz NOT NULL,
  id          text COLLATE "C" NOT NULL,
  doc         jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, ts, id)
) PARTITION BY RANGE (ts)'''),

    # ---- The Nexus state tables clear_instance_state.py scopes by prefix ----
    TableSpec("GraphNexusTradeContexts",
              indexed_fields=("instance_id", "base_instance_id", "date"),
              prefix_fields=("instance_id",),
              compound_indexes={"instance_base":
                                "(split_part(doc->>'instance_id', '|', 1))" + _C},
              retention=RetentionSpec(field="date",
                                      days_env="RETAIN_TRADE_CONTEXTS_DAYS"),
              tune_autovacuum=True),
    TableSpec("GraphNexusOutcomes",
              indexed_fields=("instance_id", "base_instance_id"),
              prefix_fields=("instance_id",)),
    TableSpec("GraphNexusTradeOutcomes",
              indexed_fields=("instance_id", "base_instance_id"),
              prefix_fields=("instance_id",)),
    TableSpec("GraphNexusOutcomeSeries", notify=False,
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusActiveEvents",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusActiveEventHistory",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusActiveEventMaintenance",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusAnalystPanel",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusDiscoveredStocks",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    TableSpec("GraphNexusMarketTrends",
              indexed_fields=("instance_id",), prefix_fields=("instance_id",)),
    # id-prefix-only clear targets: the id column itself needs text_pattern_ops.
    TableSpec("GraphNexusRotationCooldown", prefix_fields=("id",)),
    TableSpec("GraphNexusLearningCache", prefix_fields=("id",)),
    TableSpec("GraphNexusDiscoverySnapshots", prefix_fields=("id",)),
    TableSpec("NexusRuntimeState", prefix_fields=("id",)),
    TableSpec("NexusStrategyCache",
              indexed_fields=("instance_id", "instance_id_config_hash", "origin"),
              prefix_fields=("id",)),
    TableSpec("LiveBootAudit", indexed_fields=("instance_id",), prefix_fields=("id",)),
    TableSpec("LiveCommands", indexed_fields=("instance_id",)),

    # ---- Cache tables: notify off, created_at for retention -----------------
    TableSpec("GraphNexusLLMPromptCache", notify=False,
              generated={"created_at": ("timestamptz",
                                        "((doc->>'cached_at')::timestamptz)")},
              retention=RetentionSpec(field="cached_at",
                                      days_env="RETAIN_PROMPT_CACHE_DAYS"),
              tune_autovacuum=True),
    TableSpec("AlpacaBarsCache", notify=False,
              generated={"created_at": ("timestamptz",
                                        "((doc->>'cached_at')::timestamptz)")},
              retention=RetentionSpec(field="cached_at",
                                      days_env="RETAIN_BARS_CACHE_DAYS"),
              tune_autovacuum=True),
    TableSpec("LLMUsage", notify=False,
              indexed_fields=("backtest_id", "instance_id", "model", "provider", "ts"),
              retention=RetentionSpec(field="ts", days_env="RETAIN_LLM_USAGE_DAYS")),
    TableSpec("LLMUsageDaily", indexed_fields=("date",)),
    TableSpec("GraphNexusNewsLLMMacro", notify=False),
    TableSpec("GraphNexusNewsLLMCompany", notify=False),
    TableSpec("GraphNexusNewsDayFeatures", notify=False),

    # ---- Assorted secondary indexes read live from index_list() -------------
    TableSpec("BotTradeDecisions", indexed_fields=("brokerage_id",)),
    TableSpec("ChatbotConversations", indexed_fields=("user_id",)),
    TableSpec("Users", indexed_fields=("username",)),
    TableSpec("NewsLLMCache", indexed_fields=("article_hash",)),
    TableSpec("LearningExperiments", indexed_fields=("registered_at", "status")),
    TableSpec("LearningFindings", indexed_fields=("detected_at",)),
    TableSpec("LearningFunnels", indexed_fields=("observed_at",)),
    TableSpec("LearningObservations", indexed_fields=("as_of", "run_id"),
              retention=RetentionSpec(field="as_of",
                                      days_env="RETAIN_LEARNING_OBSERVATIONS_DAYS",
                                      default_days=90)),
    TableSpec("LearningOutcomes",
              indexed_fields=("as_of", "observation_id", "run_id")),
    TableSpec("KalshiBacktests", indexed_fields=("status",)),

    # ---- Non-`id` primary keys (live table_config; == kalshi/db.py registry) --
    TableSpec("sports_fixtures", pk_field="fixture_id"),
    TableSpec("lineups", pk_field="fixture_id"),
    TableSpec("match_features", pk_field="fixture_id"),
    TableSpec("kalshi_market_listings", pk_field="fixture_id"),
    TableSpec("kalshi_markets", pk_field="market_ticker"),
    TableSpec("kalshi_orders", pk_field="client_order_id"),
    TableSpec("kalshi_scan_budget", pk_field="window"),
    TableSpec("kalshi_capital_plan", pk_field="instance_id"),
    TableSpec("KalshiHistFixtures", pk_field="fixture_key"),
]

TABLES: dict = {s.name: s for s in _SPECS}
```

`backend/db/schema.py` — part 2, the DDL emitter:

```python
_DEFAULT_TEMPLATE = '''CREATE TABLE IF NOT EXISTS {q} (
  id          text PRIMARY KEY COLLATE "C",
  doc         jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
)'''

MERGE_FN = '''
CREATE OR REPLACE FUNCTION jsonb_deep_merge(a jsonb, b jsonb)
RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $fn$
  SELECT CASE
    WHEN b ? '__db_literal__' THEN b -> '__db_literal__'
    WHEN jsonb_typeof(a) <> 'object' OR jsonb_typeof(b) <> 'object' THEN b
    ELSE (
      SELECT jsonb_object_agg(k, v) FROM (
        SELECT k, CASE
                    WHEN a ? k AND b ? k THEN jsonb_deep_merge(a -> k, b -> k)
                    WHEN b ? k            THEN
                      CASE WHEN jsonb_typeof(b -> k) = 'object'
                           THEN jsonb_deep_merge('{}'::jsonb, b -> k)
                           ELSE b -> k END
                    ELSE a -> k
                  END AS v
        FROM (SELECT jsonb_object_keys(a) AS k
              UNION SELECT jsonb_object_keys(b)) AS keys
      ) AS merged
    )
  END
$fn$;
'''

NOTIFY_FN = '''
CREATE OR REPLACE FUNCTION notify_row() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
  PERFORM pg_notify('tbl:' || TG_TABLE_NAME, COALESCE(NEW.id, OLD.id));
  RETURN NULL;
END $fn$;
'''

_AUTOVACUUM = ('ALTER TABLE {q} SET (autovacuum_vacuum_scale_factor = 0.02, '
               'autovacuum_analyze_scale_factor = 0.01, '
               'toast.autovacuum_vacuum_scale_factor = 0.02)')

_ensure_lock = threading.Lock()


def quoted(name: str) -> str:
    if '"' in name:
        raise StoreError("illegal table name %r" % name)
    return '"%s"' % name


def spec(table: str) -> TableSpec:
    """Unknown table -> the default template. New tables need no registry edit."""
    return TABLES.get(table) or TableSpec(name=table)


def _statements(s: TableSpec) -> list:
    """Every statement needed to bring one table to its declared shape."""
    q = quoted(s.name)
    out = [s.ddl if s.ddl else _DEFAULT_TEMPLATE.format(q=q)]
    for f in s.indexed_fields:
        out.append('ALTER TABLE %s ADD COLUMN IF NOT EXISTS "%s" text COLLATE "C" '
                   "GENERATED ALWAYS AS (doc ->> '%s') STORED" % (q, f, f))
        out.append('CREATE INDEX IF NOT EXISTS "%s_%s_idx" ON %s ("%s")'
                   % (s.name, f, q, f))
    for col, (sql_type, expr) in sorted(s.generated.items()):
        out.append('ALTER TABLE %s ADD COLUMN IF NOT EXISTS "%s" %s '
                   "GENERATED ALWAYS AS (%s) STORED" % (q, col, sql_type, expr))
        out.append('CREATE INDEX IF NOT EXISTS "%s_%s_idx" ON %s ("%s")'
                   % (s.name, col, q, col))
    for f in s.prefix_fields:
        # text_pattern_ops backs LIKE 'prefix%'. COLLATE "C" makes the LIKE form
        # and the >=/< range form agree by construction (spec section 4).
        out.append('CREATE INDEX IF NOT EXISTS "%s_%s_pfx" ON %s ("%s" text_pattern_ops)'
                   % (s.name, f, q, f))
    for index_name, expr in sorted(s.compound_indexes.items()):
        out.append('CREATE INDEX IF NOT EXISTS "%s_%s_idx" ON %s (%s)'
                   % (s.name, index_name, q, expr))
    if s.notify:
        out.append('DROP TRIGGER IF EXISTS "%s_notify" ON %s' % (s.name, q))
        out.append('CREATE TRIGGER "%s_notify" AFTER INSERT OR UPDATE OR DELETE ON %s '
                   "FOR EACH ROW EXECUTE FUNCTION notify_row()" % (s.name, q))
    if s.tune_autovacuum:
        out.append(_AUTOVACUUM.format(q=q))
    return out


def _already_applied(cur, s: TableSpec) -> bool:
    """True when every object this spec declares already exists."""
    cur.execute("SELECT to_regclass(%s) AS t", (quoted(s.name),))
    if cur.fetchone()["t"] is None:
        return False
    cur.execute("SELECT attname FROM pg_attribute "
                "WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped",
                (quoted(s.name),))
    cols = {r["attname"] for r in cur.fetchall()}
    wanted_cols = set(s.indexed_fields) | set(s.generated)
    if not wanted_cols <= cols:
        return False
    cur.execute("SELECT c.relname FROM pg_index i "
                "JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE i.indrelid = %s::regclass", (quoted(s.name),))
    idx = {r["relname"] for r in cur.fetchall()}
    wanted_idx = {"%s_%s_idx" % (s.name, f) for f in s.indexed_fields}
    wanted_idx |= {"%s_%s_idx" % (s.name, c) for c in s.generated}
    wanted_idx |= {"%s_%s_pfx" % (s.name, f) for f in s.prefix_fields}
    wanted_idx |= {"%s_%s_idx" % (s.name, n) for n in s.compound_indexes}
    if not wanted_idx <= idx:
        return False
    if s.notify:
        cur.execute("SELECT 1 FROM pg_trigger WHERE tgrelid = %s::regclass "
                    "AND tgname = %s AND NOT tgisinternal",
                    (quoted(s.name), "%s_notify" % s.name))
        if cur.fetchone() is None:
            return False
    return True


def ensure_schema(*, tables: Optional[Iterable[str]] = None) -> list:
    """Idempotent DDL for the whole database (or the named subset).

    Creates jsonb_deep_merge and notify_row first, then every table, column,
    index and trigger. Returns the statements it actually ran, so a second
    call on an unchanged database returns []. Runs under an advisory lock so
    concurrent process boots do not race on CREATE INDEX.
    """
    names = list(tables) if tables is not None else list(ALL_TABLES)
    applied = []
    with _ensure_lock:
        with dbpool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(hashtext('intellistock.ddl'))")
                try:
                    cur.execute("SELECT proname FROM pg_proc WHERE proname = 'notify_row'")
                    if cur.fetchone() is None:
                        cur.execute(MERGE_FN)
                        cur.execute(NOTIFY_FN)
                        applied += ["jsonb_deep_merge", "notify_row"]
                    for name in names:
                        s = spec(name)
                        if _already_applied(cur, s):
                            continue
                        for stmt in _statements(s):
                            cur.execute(stmt)
                            applied.append(stmt)
                finally:
                    cur.execute("SELECT pg_advisory_unlock(hashtext('intellistock.ddl'))")
            conn.commit()
    return applied


def ensure_table(table: str) -> None:
    """On-demand path for the sites that create a table lazily (llm_utils
    prompt cache, backtest_evidence_runtime, kalshi ensure_tables)."""
    ensure_schema(tables=[table])
```

Note the `MERGE_FN`/`NOTIFY_FN` guard: both are `CREATE OR REPLACE`, so re-running them is harmless, but `ensure_schema` must report `[]` on a second run — hence the `pg_proc` probe before emitting them.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/db/test_schema_ensure.py -v`
Expected: PASS, 15 tests (6 pure + 9 real-PG). `test_ensure_schema_is_idempotent` is the one that catches a spec whose `_already_applied` probe disagrees with `_statements`.

- [ ] **Step 5: Commit**

```bash
git add backend/db/schema.py backend/tests/db/test_schema_ensure.py
git commit -m "$(cat <<'EOF'
feat(db): 125-table registry + idempotent ensure_schema

ALL_TABLES is the live table_list(); TABLES carries only the ~40 tables that
need more than the default (id, doc, updated_at) shape. Secondary indexes
become STORED generated columns with COLLATE "C" plus B-trees; prefix-scan
fields get a text_pattern_ops companion so the LIKE form and the >=/< range
form agree by construction. No GIN anywhere. The notify trigger is installed
only where TableSpec.notify is true. ensure_schema runs under an advisory
lock so concurrent boots do not race on CREATE INDEX.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 6: `PriceHistory` partitions and the retention sweeper

**Files:**
- Modify: `backend/db/schema.py` (add `ensure_partitions`, wire it into `ensure_schema`)
- Create: `scripts/pg_retention.py`
- Create: `backend/tests/db/test_partitions_retention.py`

**Interfaces:**
- Consumes: `db.schema.spec()`, `db.schema.PartitionSpec`, `db.schema.RetentionSpec`, `db.pool.cursor()`.
- Produces:
  ```python
  db.schema.ensure_partitions(table: str, *, lo: datetime, hi: datetime) -> list[str]
  db.schema.partition_name(table: str, month_start: date) -> str   # "PriceHistory_p2026_08"
  # scripts/pg_retention.py
  sweep(table: str, *, dry_run: bool = False, batch: int = 10000) -> dict
  main(argv: Sequence[str] | None = None) -> int
  ```

**Why no DEFAULT partition:** a default partition forbids `DETACH CONCURRENTLY`, which also cannot run inside a transaction block. Rows land in the right partition because the store writes `ts` from `doc['timestamp']` on insert; a row whose timestamp will not parse is rejected with `StoreError` rather than silently dropped. In production pg_partman's background worker keeps partitions ahead (premake 3); `ensure_partitions` is what makes the table usable locally, in tests, and during the migration copy, where partman's hourly tick is too slow.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_partitions_retention.py`:

```python
import datetime as dt

import pytest

from db import pool as dbpool
from db import schema

from .conftest import requires_pg


def test_partition_name_is_month_stamped():
    assert schema.partition_name("PriceHistory", dt.date(2026, 8, 1)) == \
        "PriceHistory_p2026_08"


@requires_pg
def test_ensure_partitions_creates_one_partition_per_month(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    made = schema.ensure_partitions(
        "PriceHistory",
        lo=dt.datetime(2026, 1, 15, tzinfo=dt.timezone.utc),
        hi=dt.datetime(2026, 3, 2, tzinfo=dt.timezone.utc))
    assert len(made) == 3            # Jan, Feb, Mar
    with dbpool.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relname LIKE 'PriceHistory_p%%'",
                    (pg_schema,))
        assert cur.fetchone()["n"] == 3


@requires_pg
def test_ensure_partitions_is_idempotent(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    args = dict(lo=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc),
                hi=dt.datetime(2026, 1, 31, tzinfo=dt.timezone.utc))
    assert len(schema.ensure_partitions("PriceHistory", **args)) == 1
    assert schema.ensure_partitions("PriceHistory", **args) == []


@requires_pg
def test_there_is_no_default_partition(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    with dbpool.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = %s AND c.relname = 'PriceHistory_default'",
                    (pg_schema,))
        assert cur.fetchone()["n"] == 0


@requires_pg
def test_rows_route_to_the_right_partition(pg_schema):
    schema.ensure_schema(tables=["PriceHistory"])
    schema.ensure_partitions("PriceHistory",
                             lo=dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc),
                             hi=dt.datetime(2026, 2, 28, tzinfo=dt.timezone.utc))
    with dbpool.cursor() as cur:
        cur.execute(
            'INSERT INTO "PriceHistory" (ticker, ts, id, doc) VALUES '
            "(%s, %s, %s, %s)",
            ("AAPL", dt.datetime(2026, 2, 10, tzinfo=dt.timezone.utc), "u1",
             '{"ticker":"AAPL","price":1.5}'))
        cur.execute('SELECT tableoid::regclass::text AS part FROM "PriceHistory"')
        assert cur.fetchone()["part"].endswith("PriceHistory_p2026_02")


def test_retention_is_off_until_the_env_var_is_set(monkeypatch):
    monkeypatch.delenv("RETAIN_PROMPT_CACHE_DAYS", raising=False)
    assert schema.spec("GraphNexusLLMPromptCache").retention.days() is None
    monkeypatch.setenv("RETAIN_PROMPT_CACHE_DAYS", "30")
    assert schema.spec("GraphNexusLLMPromptCache").retention.days() == 30


def test_learning_observations_retention_defaults_to_90_days(monkeypatch):
    monkeypatch.delenv("RETAIN_LEARNING_OBSERVATIONS_DAYS", raising=False)
    assert schema.spec("LearningObservations").retention.days() == 90


def test_retention_days_floor_is_one(monkeypatch):
    # retention.py:34 MIN_RETAIN_DAYS: a stored 0 would delete the table.
    monkeypatch.setenv("RETAIN_PROMPT_CACHE_DAYS", "0")
    assert schema.spec("GraphNexusLLMPromptCache").retention.days() == 1


@requires_pg
def test_sweep_is_a_noop_when_retention_is_unset(pg_schema, monkeypatch):
    import importlib.util, os
    monkeypatch.delenv("RETAIN_PROMPT_CACHE_DAYS", raising=False)
    schema.ensure_schema(tables=["GraphNexusLLMPromptCache"])
    mod = _load_retention_module()
    assert mod.sweep("GraphNexusLLMPromptCache") == {
        "table": "GraphNexusLLMPromptCache", "deleted": 0, "skipped": "retention off"}


@requires_pg
def test_sweep_deletes_only_rows_past_the_cutoff(pg_schema, monkeypatch):
    monkeypatch.setenv("RETAIN_PROMPT_CACHE_DAYS", "10")
    schema.ensure_schema(tables=["GraphNexusLLMPromptCache"])
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=40)).isoformat()
    new = dt.datetime.now(dt.timezone.utc).isoformat()
    with dbpool.cursor() as cur:
        for rid, when in (("a", old), ("b", new)):
            cur.execute('INSERT INTO "GraphNexusLLMPromptCache" (id, doc) VALUES (%s,%s)',
                        (rid, '{"cached_at":"%s"}' % when))
    mod = _load_retention_module()
    assert mod.sweep("GraphNexusLLMPromptCache")["deleted"] == 1
    with dbpool.cursor() as cur:
        cur.execute('SELECT id FROM "GraphNexusLLMPromptCache"')
        assert [r["id"] for r in cur.fetchall()] == ["b"]


@requires_pg
def test_sweep_never_deletes_a_row_whose_timestamp_will_not_parse(pg_schema, monkeypatch):
    # retention.py's rule: an unparseable timestamp is NEVER deleted.
    monkeypatch.setenv("RETAIN_PROMPT_CACHE_DAYS", "1")
    schema.ensure_schema(tables=["GraphNexusLLMPromptCache"])
    with dbpool.cursor() as cur:
        cur.execute('INSERT INTO "GraphNexusLLMPromptCache" (id, doc) VALUES (%s,%s)',
                    ("junk", '{"cached_at":"not-a-date"}'))
    mod = _load_retention_module()
    assert mod.sweep("GraphNexusLLMPromptCache")["deleted"] == 0


def _load_retention_module():
    import importlib.util, os
    repo = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    path = os.path.join(repo, "scripts", "pg_retention.py")
    spec_ = importlib.util.spec_from_file_location("pg_retention", path)
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)
    return mod
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_partitions_retention.py -v`
Expected: FAIL — `AttributeError: module 'db.schema' has no attribute 'partition_name'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/db/schema.py`:

```python
def partition_name(table: str, month_start) -> str:
    return "%s_p%04d_%02d" % (table, month_start.year, month_start.month)


def _month_starts(lo, hi):
    import datetime as _dt
    cur = _dt.date(lo.year, lo.month, 1)
    end = _dt.date(hi.year, hi.month, 1)
    out = []
    while cur <= end:
        out.append(cur)
        cur = _dt.date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
    return out


def ensure_partitions(table: str, *, lo, hi) -> list:
    """Create the monthly range partitions covering [lo, hi].

    In production pg_partman's background worker keeps partitions ahead
    (premake 3). This is the path tests and the migration copy use, where the
    hourly partman tick is too slow. There is deliberately NO default
    partition: a default partition forbids DETACH CONCURRENTLY.
    """
    import datetime as _dt
    s = spec(table)
    if s.partitioned is None:
        raise StoreError("%s is not a partitioned table" % table)
    made = []
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            for start in _month_starts(lo, hi):
                nxt = _dt.date(start.year + (start.month == 12),
                               start.month % 12 + 1, 1)
                part = partition_name(table, start)
                cur.execute("SELECT to_regclass(%s) AS t", (quoted(part),))
                if cur.fetchone()["t"] is not None:
                    continue
                cur.execute(
                    "CREATE TABLE %s PARTITION OF %s FOR VALUES FROM (%%s) TO (%%s)"
                    % (quoted(part), quoted(table)),
                    (start.isoformat(), nxt.isoformat()))
                made.append(part)
        conn.commit()
    return made
```

`scripts/pg_retention.py`:

```python
#!/usr/bin/env python3
"""Retention sweeper driven by the schema registry.

Every RetentionSpec whose env var is set gets a batched, indexed, ranged
delete. Retention is OFF for every cache table until the operator sets the
env var -- the registry's default_days is None everywhere except
LearningObservations, which already has a 90-day policy in
backend/self_learning/retention.py.

Two rules carried over from that module, both load-bearing:
  * the retention floor is 1 day (a stored 0 would delete the table);
  * a row whose timestamp will not parse is NEVER deleted.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "backend"))

from db import pool as dbpool          # noqa: E402
from db import schema                  # noqa: E402


def _cutoff_iso(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()


def sweep(table: str, *, dry_run: bool = False, batch: int = 10000) -> dict:
    spec_ = schema.spec(table)
    if spec_.retention is None:
        return {"table": table, "deleted": 0, "skipped": "no retention spec"}
    days = spec_.retention.days()
    if days is None:
        return {"table": table, "deleted": 0, "skipped": "retention off"}
    cutoff = _cutoff_iso(days)
    field = spec_.retention.field
    q = schema.quoted(table)
    # The predicate is deliberately explicit about parseability: a row whose
    # timestamp is not a valid timestamptz never matches, so it is never
    # deleted. pg_input_is_valid is PG16+; PG17 is the target.
    where = ("doc ->> %s IS NOT NULL "
             "AND pg_input_is_valid(doc ->> %s, 'timestamptz') "
             "AND (doc ->> %s)::timestamptz < %s::timestamptz")
    params = (field, field, field, cutoff)
    if dry_run:
        with dbpool.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM %s WHERE %s" % (q, where), params)
            return {"table": table, "would_delete": cur.fetchone()["n"],
                    "cutoff": cutoff}
    deleted = 0
    while True:
        with dbpool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM {q} WHERE ctid IN "
                    "(SELECT ctid FROM {q} WHERE {w} LIMIT {n})".format(
                        q=q, w=where, n=int(batch)), params)
                n = cur.rowcount
            conn.commit()
        deleted += n
        if n < batch:
            break
    return {"table": table, "deleted": deleted, "cutoff": cutoff}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tables", default="",
                    help="comma-separated subset (default: every table with a spec)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch", type=int, default=10000)
    args = ap.parse_args(argv)
    names = ([t.strip() for t in args.tables.split(",") if t.strip()]
             or [n for n, s in schema.TABLES.items() if s.retention is not None])
    rc = 0
    for name in sorted(names):
        try:
            print(sweep(name, dry_run=args.dry_run, batch=args.batch))
        except Exception as exc:               # keep sweeping the other tables
            print({"table": name, "error": "%s: %s" % (type(exc).__name__, exc)})
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/db/test_partitions_retention.py -v`
Expected: PASS, 11 tests

- [ ] **Step 5: Commit**

```bash
git add backend/db/schema.py scripts/pg_retention.py \
        backend/tests/db/test_partitions_retention.py
git commit -m "$(cat <<'EOF'
feat(db): PriceHistory monthly partitions + registry-driven retention

No DEFAULT partition (it would forbid DETACH CONCURRENTLY). Retention is OFF
for every cache table until the operator sets the env var; the 1-day floor and
the never-delete-an-unparseable-timestamp rule are carried over verbatim from
backend/self_learning/retention.py.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 7: The merge property test — Python ≡ SQL (GATE)

**Files:**
- Create: `backend/tests/db/test_merge_property.py`
- Modify: `backend/requirements.txt` (add `hypothesis` under a test-only comment)

**Interfaces:**
- Consumes: `db.merge.deep_merge`, `db.merge.encode_patch`, `db.merge.Literal`, `db.json.canonical`, `db.schema.ensure_schema` (which creates `jsonb_deep_merge`), `db.pool.cursor`.
- Produces: nothing new. **This task is a gate: `merge.py` is not signed off until it passes against a real Postgres.**

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_merge_property.py`:

```python
"""jsonb_deep_merge in Postgres must equal deep_merge in Python, byte for byte.

10,000 Hypothesis-generated (base, patch) pairs including Literal markers.
This is mandatory and blocks merge.py sign-off (spec section 2.5).
"""
import time

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from db import json as dbjson
from db import pool as dbpool
from db import schema
from db.merge import Literal, deep_merge, encode_patch

from .conftest import requires_pg

_scalars = st.one_of(
    st.none(), st.booleans(),
    st.integers(min_value=-10**9, max_value=10**9),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
    st.text(max_size=12),
)
_json = st.recursive(
    _scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(min_size=1, max_size=6), children, max_size=4),
    ),
    max_leaves=12,
)
_objects = st.dictionaries(st.text(min_size=1, max_size=6), _json, max_size=5)
_patches = st.recursive(
    _json,
    lambda children: st.one_of(
        children.map(Literal),
        st.dictionaries(st.text(min_size=1, max_size=6), children, max_size=4),
    ),
    max_leaves=10,
).filter(lambda v: isinstance(v, dict))


def _sql_merge(base, patch):
    with dbpool.cursor() as cur:
        cur.execute("SELECT jsonb_deep_merge(%s::jsonb, %s::jsonb) AS m",
                    (dbjson.dumps(base), dbjson.dumps(encode_patch(patch))))
        return cur.fetchone()["m"]


@requires_pg
@settings(max_examples=10000, deadline=None,
          suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(base=_objects, patch=_patches)
def test_sql_and_python_deep_merge_agree(pg_schema, base, patch):
    schema.ensure_schema(tables=[])          # installs jsonb_deep_merge only
    assert dbjson.canonical(_sql_merge(base, patch)) == \
        dbjson.canonical(deep_merge(base, patch))


@requires_pg
def test_literal_blanks_a_subtree_in_sql_too(pg_schema):
    schema.ensure_schema(tables=[])
    got = _sql_merge({"s": {"k": "secret"}}, {"s": Literal({})})
    assert got == {"s": {}}


@requires_pg
def test_deep_merge_on_a_realistic_config_stays_under_10ms(pg_schema):
    """Risk #5: doc 179 is 758 deep keys / 28 KB, doc 180 is 963 keys.

    If a single deep merge exceeds 10 ms we materialise the merge in Python
    and write the whole doc for the handful of large-config tables.
    Correctness is unaffected either way -- this test exists to make the
    decision on evidence rather than on a hunch.
    """
    schema.ensure_schema(tables=[])
    deep = {"lvl%d" % i: {"a": i, "b": [1, 2, 3], "c": {"d": "x" * 24}}
            for i in range(200)}
    start = time.perf_counter()
    for _ in range(10):
        _sql_merge(deep, {"lvl7": {"a": 99}})
    per_call_ms = (time.perf_counter() - start) / 10 * 1000
    assert per_call_ms < 10.0, "deep merge took %.2f ms/call" % per_call_ms
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_merge_property.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hypothesis'`

- [ ] **Step 3: Write minimal implementation**

Add to `backend/requirements.txt`:

```
# Test-only: the jsonb_deep_merge property test (backend/tests/db) generates
# 10k (base, patch) pairs and asserts the SQL and Python merges agree.
hypothesis>=6.100
```

Then `python3 -m pip install 'hypothesis>=6.100'`.

No production code changes. If the property test fails, the bug is in `merge.deep_merge` or in `schema.MERGE_FN`; fix whichever the counterexample indicts and re-run — do not weaken the strategies.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/db/test_merge_property.py -v`
Expected: PASS, 3 tests. The 10k-example run takes several minutes; that is the price of the gate.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/db/test_merge_property.py backend/requirements.txt
git commit -m "$(cat <<'EOF'
test(db): 10k-example property test proving SQL == Python deep merge

Gate for merge.py sign-off. Also benchmarks a 200-key config merge against
the 10 ms budget from risk #5, so the "materialise in Python instead"
decision is made on evidence.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 8: `store.py` — Selection plumbing and key reads

**Files:**
- Create: `backend/db/store.py`
- Create: `backend/tests/db/test_store_reads.py`

**Interfaces:**
- Consumes: `db.pool.cursor/connection`, `db.schema.spec/quoted/ensure_table`, `db.json`, `db.errors`.
- Produces (this task's half of the store API):
  ```python
  Doc = dict
  PG_MAX_ROWS = int(os.environ.get("PG_MAX_ROWS", "100000"))

  @dataclass(frozen=True)
  class Order:  field: str; desc: bool = False; numeric: bool = False
  def asc(field: str, *, numeric: bool = False) -> Order
  def desc(field: str, *, numeric: bool = False) -> Order

  @dataclass(frozen=True)
  class Selection:
      table: str
      terms: tuple = ()          # tuple[(sql_fragment, params_tuple), ...]
      orders: tuple = ()         # tuple[Order, ...]
      limit_n: int | None = None
      offset_n: int = 0
      # methods: where(sql, params) -> Selection ; ordered(orders) -> Selection
      #          with_limit(n, offset) -> Selection ; to_sql(columns="doc") -> (sql, params)

  def get(table, row_id) -> Doc | None
  def get_all(table, *keys, index=None) -> list[Doc]
  def run(selection) -> list[Doc]
  def iter(selection, *, batch=1000) -> Iterator[Doc]
  def count(table_or_selection) -> int
  def limit(selection, n) -> Selection
  def slice(selection, start, end) -> Selection
  def pluck(rows_or_selection, *fields) -> list[Doc]
  def sql(query: str, params: Sequence = ()) -> list[Doc]
  def table_list() -> list[str]
  def table_create(name, *, primary_key="id") -> bool
  def index_list(table) -> list[str]
  def coerce_id(table, value) -> str        # exported: Plan B and the ports use it
  ```

**Semantics rows from spec §4 this task must satisfy:** `.get(pk)` missing → `None` never `{}`; `.get_all` **no dedupe**, empty key set → valid empty selection (never the `"__no_match_sentinel__"` trick); int primary keys coerce both ways; `.pluck` **omits** missing keys, never emits nulls, and recurses into a nested spec; the ReQL 100k array limit is preserved as `store.run()` raising above `PG_MAX_ROWS` with `store.iter()` as the explicit unbounded path; `TableSpec.time_fields` decoded back to timezone-aware `datetime` on read.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_store_reads.py`:

```python
import pytest

from db import store
from db import schema
from db import pool as dbpool
from db.errors import StoreError

from .conftest import requires_pg


@pytest.fixture
def seeded(pg_schema):
    schema.ensure_schema(tables=["Instances", "DiscordOutbox", "BacktestResults"])
    with dbpool.cursor() as cur:
        for rid, doc in (("1", '{"id":1,"name":"main","stocks":["A","B"]}'),
                         ("2", '{"id":2,"name":"test"}')):
            cur.execute('INSERT INTO "Instances" (id, doc) VALUES (%s,%s)', (rid, doc))
        for rid, doc in (("a", '{"id":"a","kind":"x"}'),
                         ("b", '{"id":"b","kind":"y"}'),
                         ("c", '{"id":"c","kind":"x"}')):
            cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)', (rid, doc))
    return pg_schema


def test_asc_and_desc_build_order_objects():
    assert store.asc("timestamp") == store.Order("timestamp", False, False)
    assert store.desc("pnl", numeric=True) == store.Order("pnl", True, True)


def test_coerce_id_round_trips_int_tables():
    assert store.coerce_id("BacktestResults", 460555) == "460555"
    assert store.coerce_id("BacktestResults", "460555") == "460555"


def test_coerce_id_rejects_a_non_integer_for_an_int_table():
    with pytest.raises(StoreError):
        store.coerce_id("BacktestResults", "not-a-number")


def test_coerce_id_leaves_text_tables_alone():
    assert store.coerce_id("DiscordOutbox", "alpaca-main|abc") == "alpaca-main|abc"


@requires_pg
def test_get_returns_the_document(seeded):
    assert store.get("Instances", 1) == {"id": 1, "name": "main", "stocks": ["A", "B"]}


@requires_pg
def test_get_accepts_int_and_string_ids_interchangeably(seeded):
    assert store.get("Instances", 1) == store.get("Instances", "1")


@requires_pg
def test_get_missing_row_is_none_never_empty_dict(seeded):
    assert store.get("Instances", 999) is None


@requires_pg
def test_get_all_returns_rows_in_key_order(seeded):
    got = store.get_all("DiscordOutbox", "c", "a")
    assert [r["id"] for r in got] == ["c", "a"]


@requires_pg
def test_get_all_does_not_dedupe(seeded):
    # ReQL returns 3 rows for get_all("a","a","b"); = ANY() would collapse them.
    got = store.get_all("DiscordOutbox", "a", "a", "b")
    assert [r["id"] for r in got] == ["a", "a", "b"]


@requires_pg
def test_get_all_with_no_keys_is_a_valid_empty_result(seeded):
    # The "__no_match_sentinel__" trick in clear_instance_state.py is deleted.
    assert store.get_all("DiscordOutbox") == []


@requires_pg
def test_get_all_on_a_secondary_index_field(seeded):
    schema.ensure_schema(tables=["GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        cur.execute('INSERT INTO "GraphNexusTradeContexts" (id, doc) VALUES (%s,%s)',
                    ("k1", '{"id":"k1","instance_id":"main|h"}'))
    got = store.get_all("GraphNexusTradeContexts", "main|h", index="instance_id")
    assert [r["id"] for r in got] == ["k1"]


@requires_pg
def test_count_of_a_table(seeded):
    assert store.count("DiscordOutbox") == 3


@requires_pg
def test_run_raises_above_pg_max_rows(seeded, monkeypatch):
    """ReQL fails loudly above 100k array elements; nothing here defends
    against it today, so run() preserves the loud failure."""
    monkeypatch.setattr(store, "PG_MAX_ROWS", 2)
    with pytest.raises(StoreError):
        store.run(store.Selection("DiscordOutbox"))


@requires_pg
def test_iter_is_the_explicit_unbounded_path(seeded, monkeypatch):
    monkeypatch.setattr(store, "PG_MAX_ROWS", 2)
    assert len(list(store.iter(store.Selection("DiscordOutbox")))) == 3


@requires_pg
def test_limit_and_slice(seeded):
    sel = store.Selection("DiscordOutbox").ordered((store.asc("id"),))
    assert [r["id"] for r in store.run(store.limit(sel, 2))] == ["a", "b"]
    assert [r["id"] for r in store.run(store.slice(sel, 1, 3))] == ["b", "c"]


def test_pluck_omits_missing_keys_it_never_emits_null():
    rows = [{"id": 1, "status": "ok"}, {"id": 2}]
    assert store.pluck(rows, "id", "status") == [{"id": 1, "status": "ok"}, {"id": 2}]


def test_pluck_recurses_into_a_nested_spec():
    rows = [{"new_val": {"id": 5, "status": "running", "logs": [1]}, "old_val": None}]
    assert store.pluck(rows, {"new_val": ["id", "status"]}) == \
        [{"new_val": {"id": 5, "status": "running"}}]


@requires_pg
def test_table_list_and_table_create(pg_schema):
    assert store.table_create("DiscordOutbox") is True
    assert store.table_create("DiscordOutbox") is False     # already existed
    assert "DiscordOutbox" in store.table_list()


@requires_pg
def test_index_list_reports_the_reql_index_names(pg_schema):
    schema.ensure_schema(tables=["BacktestResults"])
    names = set(store.index_list("BacktestResults"))
    assert {"instance_or_instance_id", "list_ts", "instance_ts", "status"} <= names


@requires_pg
def test_time_fields_decode_back_to_aware_datetimes(pg_schema):
    import datetime as dt
    schema.TABLES["DiscordOutbox"] = schema.TableSpec(
        "DiscordOutbox", time_fields=("sent_at",))
    try:
        schema.ensure_schema(tables=["DiscordOutbox"])
        with dbpool.cursor() as cur:
            cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                        ("t", '{"id":"t","sent_at":"2026-08-22T03:37:00.123456+00:00"}'))
        got = store.get("DiscordOutbox", "t")
        assert got["sent_at"] == dt.datetime(2026, 8, 22, 3, 37, 0, 123456,
                                             tzinfo=dt.timezone.utc)
    finally:
        del schema.TABLES["DiscordOutbox"]


@requires_pg
def test_sql_is_the_escape_hatch_for_hand_written_statements(seeded):
    rows = store.sql('SELECT id FROM "DiscordOutbox" WHERE doc->>%s = %s ORDER BY id',
                     ("kind", "x"))
    assert [r["id"] for r in rows] == ["a", "c"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_store_reads.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.store'`

- [ ] **Step 3: Write minimal implementation**

`backend/db/store.py` — part 1:

```python
"""The typed store API. Every call site in the repo goes through this module.

Selection is a lazy, immutable query builder (table + WHERE terms + ORDER +
LIMIT/OFFSET). It is never executed until run/iter/count/delete/update touches
it -- matching ReQL, where ``.filter(...).delete()`` is one server-side
statement, not a fetch-then-delete.
"""
from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, replace as _replace
from typing import Any, Iterator, Optional, Sequence

from . import json as dbjson
from . import pool as dbpool
from . import schema as dbschema
from .errors import StoreError
from .merge import Literal, deep_merge, encode_patch   # re-exported for callers

Doc = dict

# ReQL fails loudly above a 100k-element array. Nothing in the repo defends
# against it, but nothing proves nothing RELIES on the loud failure either --
# so run() keeps it and iter() is the explicit unbounded path.
PG_MAX_ROWS = int(os.environ.get("PG_MAX_ROWS", "100000"))

_C = ' COLLATE "C"'


@dataclass(frozen=True)
class Order:
    field: str
    desc: bool = False
    numeric: bool = False

    def to_sql(self) -> str:
        if self.numeric:
            expr = "(doc->>'%s')::numeric" % self.field
        else:
            expr = "(doc->>'%s')%s" % (self.field, _C)
        return "%s %s" % (expr, "DESC" if self.desc else "ASC")


def asc(field: str, *, numeric: bool = False) -> Order:
    return Order(field, False, numeric)


def desc(field: str, *, numeric: bool = False) -> Order:
    return Order(field, True, numeric)


@dataclass(frozen=True)
class Selection:
    table: str
    terms: tuple = ()          # ((sql_fragment, params_tuple), ...)
    orders: tuple = ()
    limit_n: Optional[int] = None
    offset_n: int = 0

    def where(self, fragment: str, params: Sequence = ()) -> "Selection":
        return _replace(self, terms=self.terms + ((fragment, tuple(params)),))

    def ordered(self, orders: Sequence) -> "Selection":
        return _replace(self, orders=tuple(orders))

    def with_limit(self, n: Optional[int], offset: int = 0) -> "Selection":
        return _replace(self, limit_n=n, offset_n=offset)

    def where_sql(self):
        if not self.terms:
            return "", ()
        params: list = []
        frags = []
        for frag, prms in self.terms:
            frags.append("(%s)" % frag)
            params.extend(prms)
        return " WHERE " + " AND ".join(frags), tuple(params)

    def to_sql(self, columns: str = "doc"):
        where, params = self.where_sql()
        sql_ = "SELECT %s FROM %s%s" % (columns, dbschema.quoted(self.table), where)
        if self.orders:
            sql_ += " ORDER BY " + ", ".join(o.to_sql() for o in self.orders)
        if self.limit_n is not None:
            sql_ += " LIMIT %d" % int(self.limit_n)
        if self.offset_n:
            sql_ += " OFFSET %d" % int(self.offset_n)
        return sql_, params


def _as_selection(target) -> Selection:
    return target if isinstance(target, Selection) else Selection(str(target))


def coerce_id(table: str, value: Any) -> str:
    """RethinkDB primary keys are type-strict. ``id`` is text in every Postgres
    table, so an int-keyed table coerces both ways: get(t, 460555) and
    get(t, "460555") must return the same row. A non-integer input to an
    id_type="int" table raises rather than creating a shadow row.
    """
    spec_ = dbschema.spec(table)
    if spec_.id_type == "int":
        try:
            return str(int(value))
        except (TypeError, ValueError):
            raise StoreError("%s.id must be an integer, got %r" % (table, value))
    return value if isinstance(value, str) else str(value)


def _decode_times(table: str, doc):
    """Decode TableSpec.time_fields back to timezone-aware datetimes, so call
    sites that relied on the RethinkDB driver's datetime objects are
    unchanged. Anything not listed stays the ISO string it already was."""
    spec_ = dbschema.spec(table)
    if not spec_.time_fields or not isinstance(doc, dict):
        return doc
    out = dict(doc)
    for key in spec_.time_fields:
        value = out.get(key)
        if isinstance(value, str):
            try:
                out[key] = _dt.datetime.fromisoformat(value)
            except ValueError:
                pass
    return out


def get(table: str, row_id: Any) -> Optional[Doc]:
    with dbpool.cursor() as cur:
        cur.execute('SELECT doc FROM %s WHERE id = %%s' % dbschema.quoted(table),
                    (coerce_id(table, row_id),))
        row = cur.fetchone()
    return _decode_times(table, row["doc"]) if row else None


def get_all(table: str, *keys: Any, index: Optional[str] = None) -> list:
    """No dedupe: get_all("a","a","b") returns 3 rows if all exist, matching
    ReQL. ``= ANY()`` would collapse them, so this joins against an ordinal
    unnest instead."""
    if not keys:
        return []
    column = index or "id"
    values = [coerce_id(table, k) for k in keys] if column == "id" else \
             [k if isinstance(k, str) else str(k) for k in keys]
    with dbpool.cursor() as cur:
        cur.execute(
            "SELECT t.doc FROM unnest(%%s::text[]) WITH ORDINALITY AS k(v, ord) "
            'JOIN %s t ON t."%s" = k.v ORDER BY k.ord'
            % (dbschema.quoted(table), column),
            (values,))
        rows = cur.fetchall()
    return [_decode_times(table, r["doc"]) for r in rows]


def run(selection) -> list:
    sel = _as_selection(selection)
    sql_, params = sel.to_sql()
    with dbpool.cursor() as cur:
        cur.execute(sql_, params)
        rows = cur.fetchall()
    if sel.limit_n is None and len(rows) > PG_MAX_ROWS:
        raise StoreError(
            "%s returned %d rows, above PG_MAX_ROWS=%d; use store.iter()"
            % (sel.table, len(rows), PG_MAX_ROWS))
    return [_decode_times(sel.table, r["doc"]) for r in rows]


def iter(selection, *, batch: int = 1000) -> Iterator:
    """Server-side cursor. The explicit unbounded path: the migration script,
    pg_retention, clear_instance_state's PK materialisation, and assemble()."""
    sel = _as_selection(selection)
    sql_, params = sel.to_sql()
    with dbpool.connection() as conn:
        with conn.cursor(name="store_iter_%d" % id(sel)) as cur:
            cur.itersize = int(batch)
            cur.execute(sql_, params)
            for row in cur:
                yield _decode_times(sel.table, row["doc"])


def count(table_or_selection) -> int:
    sel = _as_selection(table_or_selection)
    where, params = sel.where_sql()
    with dbpool.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM %s%s"
                    % (dbschema.quoted(sel.table), where), params)
        return int(cur.fetchone()["n"])


def limit(selection, n: int) -> Selection:
    return _as_selection(selection).with_limit(int(n))


def slice(selection, start: int, end: int) -> Selection:
    return _as_selection(selection).with_limit(int(end) - int(start), int(start))


def pluck(rows_or_selection, *fields):
    """ReQL omits missing fields. jsonb_build_object('a', doc->'a') would
    yield {"a": null} and flip every ``if 'a' in result`` in the codebase, so
    this is done in Python over the materialised rows."""
    rows = rows_or_selection
    if isinstance(rows, Selection):
        rows = run(rows)
    out = []
    for row in rows:
        out.append(_pluck_one(row, fields))
    return out


def _pluck_one(row, fields):
    picked = {}
    for f in fields:
        if isinstance(f, dict):
            for key, sub in f.items():
                if key in row and isinstance(row[key], dict):
                    picked[key] = _pluck_one(row[key], tuple(sub))
                elif key in row:
                    picked[key] = row[key]
        elif f in row:
            picked[f] = row[f]
    return picked


def sql(query: str, params=()) -> list:
    """Escape hatch for the handful of sites that need hand-written SQL (the
    BacktestResults list endpoint, the migration verifier). Predicates are
    never string-interpolated with user data; pass everything through params.

    ``params`` may be a sequence for ``%s`` placeholders or a mapping for
    ``%(name)s`` placeholders -- the BacktestResults list query uses named
    ones. A mapping is passed through unchanged; anything else is coerced to a
    tuple. Commits, so a write issued through here is durable.
    """
    from collections.abc import Mapping
    bound = params if isinstance(params, Mapping) else tuple(params)
    with dbpool.connection() as conn:
        from psycopg.rows import dict_row
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, bound)
            rows = list(cur.fetchall()) if cur.description is not None else []
        conn.commit()
    return rows


def table_list() -> list:
    rows = sql("SELECT tablename FROM pg_tables WHERE schemaname = "
               "ANY(current_schemas(false)) ORDER BY tablename")
    return [r["tablename"] for r in rows]


def table_create(name: str, *, primary_key: str = "id") -> bool:
    """False when the table already existed, matching the ensure-blocks that
    swallow RethinkDB's ReqlOpFailedError today."""
    if name in table_list():
        return False
    if primary_key != "id" and dbschema.spec(name).pk_field != primary_key:
        raise StoreError(
            "%s: primary_key=%r contradicts the registry (%r); update db/schema.py"
            % (name, primary_key, dbschema.spec(name).pk_field))
    dbschema.ensure_table(name)
    return True


def index_list(table: str) -> list:
    """The ReQL index names for a table: the generated-column indexes and the
    compound expression indexes, with the "<Table>_"/"_idx" affixes stripped."""
    spec_ = dbschema.spec(table)
    known = set(spec_.indexed_fields) | set(spec_.compound_indexes) | set(spec_.generated)
    rows = sql("SELECT indexname FROM pg_indexes WHERE tablename = %s", (table,))
    out = []
    for r in rows:
        name = r["indexname"]
        if name.startswith(table + "_") and name.endswith("_idx"):
            short = name[len(table) + 1:-4]
            if short in known:
                out.append(short)
    return sorted(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/db/test_store_reads.py -v`
Expected: PASS, 22 tests

- [ ] **Step 5: Commit**

```bash
git add backend/db/store.py backend/tests/db/test_store_reads.py
git commit -m "$(cat <<'EOF'
feat(db): store Selection builder + key reads

get() returns None (never {}) for a missing row; get_all joins an ordinal
unnest so duplicate keys yield duplicate rows the way ReQL does; int-keyed
tables coerce both ways; pluck omits absent keys instead of emitting nulls;
run() preserves ReQL's loud failure above PG_MAX_ROWS with iter() as the
explicit unbounded path.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 9: Predicate DSL, `filter`, `between`, `order_by` — plus collation and prefix scans

**Files:**
- Modify: `backend/db/store.py` (append the predicate section)
- Create: `backend/tests/db/test_store_query.py`
- Create: `backend/tests/db/test_collation.py`
- Create: `backend/tests/db/test_prefix_scan.py`

**Interfaces:**
- Consumes: `Selection`, `Order`, `asc`, `desc`, `run`, `coerce_id` from Task 8.
- Produces:
  ```python
  class Predicate:  to_sql() -> (fragment: str, params: tuple)
                    __and__, __or__, __invert__
  class FieldRef:   eq(v) ne(v) lt(v) le(v) gt(v) ge(v)
                    default(v) coerce_to_string() downcase()
                    match(regex) starts_with(prefix) split_nth(sep, n) is_in(seq)
  class P:          @staticmethod field(key: str) -> FieldRef
  def filter(table, predicate: Doc | Predicate) -> Selection
  def between(table, lo, hi, *, index=None,
              left_bound="closed", right_bound="open") -> Selection
  def order_by(selection, *, index=None, fields=(), desc=False) -> Selection
  def escape_like(value: str) -> str
  MINVAL = _Sentinel("minval");  MAXVAL = _Sentinel("maxval")
  ```

**Semantics rows from spec §4 this task must satisfy:** never SQL `BETWEEN` (ReQL is `[lo, hi)`); `r.minval`/`r.maxval` omit the bound entirely; the prefix scan has a `LIKE` form and a range form that must return identical rows (this is the `clear_instance_state.py:100-104` silent-no-op regression); `.filter({dict})` with a `None` value means JSON null, not absent; `P.field("k")` without `.default()` compiles to `doc->>'k'`, which is SQL NULL, and every comparison with NULL is false; `.split("|").nth(0)` → `split_part(..., '|', 1)` (1-based); `r.expr(list).contains(doc[f])` → `= ANY(%s)` with an empty list yielding `false`, not an error; `ORDER BY` over text always carries `COLLATE "C"`, numeric order fields cast to `numeric` explicitly.

- [ ] **Step 1: Write the failing tests**

`backend/tests/db/test_store_query.py`:

```python
import pytest

from db import pool as dbpool
from db import schema, store
from db.store import P

from .conftest import requires_pg


@pytest.fixture
def rows(pg_schema):
    schema.ensure_schema(tables=["NexusStrategyCache", "GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        for rid, doc in (
            ("main|gna|h1|backtest|2026-05-23", '{"id":"main|gna|h1|backtest|2026-05-23","instance_id":"main","origin":"backtest","score":10}'),
            ("main|gna|h2|live|2026-05-24", '{"id":"main|gna|h2|live|2026-05-24","instance_id":"main","origin":"live","score":2}'),
            ("alpaca-main|gna|h3|live|2026-05-25", '{"id":"alpaca-main|gna|h3|live|2026-05-25","instance_id":"alpaca-main","score":7}'),
        ):
            cur.execute('INSERT INTO "NexusStrategyCache" (id, doc) VALUES (%s,%s)',
                        (rid, doc))
    return pg_schema


def test_predicate_never_interpolates_values():
    frag, params = P.field("k").eq("O'Brien").to_sql()
    assert "O'Brien" not in frag and params == ("O'Brien",)


def test_split_nth_is_one_based_in_sql():
    frag, _ = P.field("instance_id").split_nth("|", 0).eq("main").to_sql()
    assert "split_part(doc->>'instance_id', '|', 1)" in frag


def test_default_compiles_to_coalesce():
    frag, params = P.field("origin").default("").ne("backtest").to_sql()
    assert "coalesce(doc->>'origin', %s)" in frag
    assert params == ("", "backtest")


@requires_pg
def test_filter_dict_matches_on_equality(rows):
    got = store.run(store.filter("NexusStrategyCache", {"instance_id": "main"}))
    assert {r["id"] for r in got} == {
        "main|gna|h1|backtest|2026-05-23", "main|gna|h2|live|2026-05-24"}


@requires_pg
def test_filter_dict_none_means_json_null_not_absent(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.cursor() as cur:
        cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                    ("null_row", '{"id":"null_row","err":null}'))
        cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                    ("absent_row", '{"id":"absent_row"}'))
    got = store.run(store.filter("DiscordOutbox", {"err": None}))
    assert [r["id"] for r in got] == ["null_row"]


@requires_pg
def test_origin_not_backtest_criterion(rows):
    # clear_instance_state.py:377's "special" criterion, name preserved.
    pred = P.field("instance_id").eq("main") & P.field("origin").default("").ne("backtest")
    got = store.run(store.filter("NexusStrategyCache", pred))
    assert [r["id"] for r in got] == ["main|gna|h2|live|2026-05-24"]


@requires_pg
def test_undefaulted_field_comparison_is_false_on_a_missing_key(rows):
    # ReQL raises on row["k"] for a missing key; every ported site writes
    # .default(). Undefaulted access is SQL NULL, so the comparison is false.
    got = store.run(store.filter("NexusStrategyCache", P.field("origin").ne("backtest")))
    assert [r["id"] for r in got] == ["main|gna|h2|live|2026-05-24"]


@requires_pg
def test_is_in_matches_any_of_a_list(rows):
    got = store.run(store.filter("NexusStrategyCache",
                                 P.field("instance_id").is_in(["main", "nope"])))
    assert len(got) == 2


@requires_pg
def test_is_in_with_an_empty_list_is_false_not_an_error(rows):
    assert store.run(store.filter("NexusStrategyCache",
                                  P.field("instance_id").is_in([]))) == []


@requires_pg
def test_combinators(rows):
    pred = (P.field("instance_id").eq("main") | P.field("instance_id").eq("alpaca-main"))
    assert len(store.run(store.filter("NexusStrategyCache", pred))) == 3
    assert len(store.run(store.filter("NexusStrategyCache",
                                      ~P.field("instance_id").eq("main")))) == 1


@requires_pg
def test_split_nth_selects_the_base_instance(rows):
    pred = P.field("id").split_nth("|", 0).eq("alpaca-main")
    got = store.run(store.filter("NexusStrategyCache", pred))
    assert [r["id"] for r in got] == ["alpaca-main|gna|h3|live|2026-05-25"]


@requires_pg
def test_coerce_to_string_stringifies_a_json_number(pg_schema):
    schema.ensure_schema(tables=["BacktestResults"])
    with dbpool.cursor() as cur:
        cur.execute('INSERT INTO "BacktestResults" (id, doc) VALUES (%s,%s)',
                    ("1", '{"id":1,"instance_id":5}'))
    pred = P.field("instance_id").default("").coerce_to_string().eq("5")
    assert len(store.run(store.filter("BacktestResults", pred))) == 1


@requires_pg
def test_between_is_half_open_by_default(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.cursor() as cur:
        for rid in ("a", "b", "c"):
            cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                        (rid, '{"id":"%s"}' % rid))
    sel = store.between("DiscordOutbox", "a", "c")
    assert sorted(r["id"] for r in store.run(sel)) == ["a", "b"]


@requires_pg
def test_between_right_bound_closed_includes_the_upper_key(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.cursor() as cur:
        for rid in ("a", "b", "c"):
            cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                        (rid, '{"id":"%s"}' % rid))
    sel = store.between("DiscordOutbox", "a", "c", right_bound="closed")
    assert sorted(r["id"] for r in store.run(sel)) == ["a", "b", "c"]


@requires_pg
def test_between_left_bound_open_excludes_the_lower_key(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    with dbpool.cursor() as cur:
        for rid in ("a", "b"):
            cur.execute('INSERT INTO "DiscordOutbox" (id, doc) VALUES (%s,%s)',
                        (rid, '{"id":"%s"}' % rid))
    sel = store.between("DiscordOutbox", "a", "z", left_bound="open")
    assert [r["id"] for r in store.run(sel)] == ["b"]


@requires_pg
def test_minval_and_maxval_omit_the_bound(rows):
    sel = store.between("NexusStrategyCache", "main", store.MAXVAL, index="instance_id")
    assert len(store.run(sel)) == 2
    frag = sel.where_sql()[0]
    assert frag.count(">=") == 1 and "<" not in frag.replace("<=", "")


@requires_pg
def test_order_by_numeric_field_casts_explicitly(rows):
    sel = store.order_by(store.Selection("NexusStrategyCache"),
                         fields=(store.desc("score", numeric=True),))
    assert [r["doc"]["score"] if "doc" in r else r["score"]
            for r in store.run(sel)] == [10, 7, 2]
```

`backend/tests/db/test_collation.py`:

```python
"""ORDER BY must be bytewise.

graph_nexus_analysis.py:11843-11858 orders by
(latest_observation_date DESC, id DESC) and takes .limit(80); by mid-window
_update_indefinite_outcomes has rewritten latest_observation_date to the same
value on most rows, so `id` alone decides MEMBERSHIP of the window that feeds
an LLM prompt. A non-bytewise collation silently changes results.
"""
import pytest

from db import pool as dbpool
from db import schema, store

from .conftest import requires_pg

# Real scope-suffixed id shapes: instance | config-hash | date | ticker.
_IDS = [
    "alpaca-main|3df5616cacc43b413a6eaf21|2026-03-02|AACI",
    "alpaca-main|3df5616cacc43b413a6eaf21|2026-03-02|AA",
    "alpaca-main|3df5616cacc43b413a6eaf21|2026-03-02|aaci",
    "alpaca-main|3df5616cacc43b413a6eaf21|2026-03-02|ZZ",
    "alpaca-main|3df5616cacc43b413a6eaf21|2026-03-10|AACI",
    "alpaca_main|3df5616cacc43b413a6eaf21|2026-03-02|AACI",
    "alpaca-main2|3df5616cacc43b413a6eaf21|2026-03-02|AACI",
    "Alpaca-Main|3df5616cacc43b413a6eaf21|2026-03-02|AACI",
]


@pytest.fixture
def contexts(pg_schema):
    schema.ensure_schema(tables=["GraphNexusTradeContexts"])
    with dbpool.cursor() as cur:
        for rid in _IDS:
            cur.execute(
                'INSERT INTO "GraphNexusTradeContexts" (id, doc) VALUES (%s,%s)',
                (rid, '{"id":"%s","instance_id":"alpaca-main|h",'
                      '"latest_observation_date":"2026-08-01"}' % rid))
    return pg_schema


@requires_pg
def test_order_by_id_desc_is_bytewise(contexts):
    got = store.sql('SELECT id FROM "GraphNexusTradeContexts" '
                    'ORDER BY id COLLATE "C" DESC')
    assert [r["id"] for r in got] == sorted(_IDS, reverse=True)


@requires_pg
def test_pipe_sorts_before_lowercase_and_after_uppercase(contexts):
    """'|' is 0x7C: after 'Z' (0x5A) and after digits, before 'a' (0x61)? No --
    0x7C > 0x61, so '|' sorts AFTER lowercase letters. A locale-aware
    collation would ignore punctuation entirely and reorder these."""
    got = [r["id"] for r in store.sql(
        'SELECT id FROM "GraphNexusTradeContexts" ORDER BY id COLLATE "C" ASC')]
    assert got.index("Alpaca-Main|3df5616cacc43b413a6eaf21|2026-03-02|AACI") == 0
    assert got.index("alpaca_main|3df5616cacc43b413a6eaf21|2026-03-02|AACI") == len(got) - 1


@requires_pg
def test_the_graph_nexus_window_tiebreak_reproduces_python_byte_order(contexts):
    """The exact query shape from graph_nexus_analysis.py:11843-11858."""
    sel = store.order_by(
        store.filter("GraphNexusTradeContexts",
                     store.P.field("instance_id").eq("alpaca-main|h")),
        fields=(store.desc("latest_observation_date"), store.desc("id")))
    got = [r["id"] for r in store.run(store.limit(sel, 80))]
    assert got == sorted(_IDS, key=lambda s: s.encode("utf-8"), reverse=True)


@requires_pg
def test_generated_columns_order_bytewise_without_an_explicit_collate(contexts):
    # COLLATE "C" is on the column itself, so an index scan agrees with the
    # explicit ORDER BY ... COLLATE "C".
    a = [r["id"] for r in store.sql(
        'SELECT id FROM "GraphNexusTradeContexts" ORDER BY id')]
    b = [r["id"] for r in store.sql(
        'SELECT id FROM "GraphNexusTradeContexts" ORDER BY id COLLATE "C"')]
    assert a == b
```

`backend/tests/db/test_prefix_scan.py`:

```python
"""The LIKE form and the >=/< range form must return identical rows.

clear_instance_state.py:100-104 records the 2026-05-25 regression: exact-only
matching found ZERO scoped rows, so a full clear was a silent no-op. Rows are
written under a config-hash-scoped id ("main|<hash>"), not the bare instance
id, and NexusRuntimeState uses a COLON suffix while everything else uses a
pipe.
"""
import pytest

from db import pool as dbpool
from db import schema, store

from .conftest import requires_pg

# The 18 full_instance targets' prefix shapes, from clear_instance_state.py.
_PREFIX_CASES = [
    ("GraphNexusTradeContexts", "instance_id", "main|"),
    ("GraphNexusOutcomes", "instance_id", "main|"),
    ("GraphNexusDiscoveredStocks", "instance_id", "main|"),
    ("GraphNexusMarketTrends", "instance_id", "main|"),
    ("GraphNexusActiveEvents", "instance_id", "main|"),
    ("GraphNexusActiveEventHistory", "instance_id", "main|"),
    ("GraphNexusActiveEventMaintenance", "instance_id", "main|"),
    ("GraphNexusOutcomeSeries", "instance_id", "main|"),
    ("GraphNexusAnalystPanel", "instance_id", "main|"),
    ("GraphNexusTradeOutcomes", "instance_id", "main|"),
    ("GraphNexusRotationCooldown", "id", "main|"),
    ("GraphNexusLearningCache", "id", "cleanup_done|main|"),
    ("GraphNexusDiscoverySnapshots", "id", "main|"),
    ("NexusRuntimeState", "id", "main:"),          # COLON, not pipe
    ("LiveBootAudit", "id", "main|"),
]


def test_escape_like_escapes_the_three_like_metacharacters():
    assert store.escape_like(r"a%b_c\d") == r"a\%b\_c\\d"


def test_escape_like_leaves_pipe_alone():
    # Today's code escapes | as [|] for the REGEX form; LIKE needs no escape.
    assert store.escape_like("main|") == "main|"


@requires_pg
@pytest.mark.parametrize("table,field,prefix", _PREFIX_CASES)
def test_like_form_and_range_form_agree(pg_schema, table, field, prefix):
    schema.ensure_schema(tables=[table])
    scoped = prefix + "3df5616cacc43b413a6eaf21|2026-03-02|AACI"
    others = ["maintenance|x", "other|y", prefix.rstrip("|:")]  # near-misses
    with dbpool.cursor() as cur:
        for i, value in enumerate([scoped] + others):
            rid = "r%d" % i if field != "id" else value
            cur.execute('INSERT INTO %s (id, doc) VALUES (%%s, %%s)'
                        % schema.quoted(table),
                        (rid, '{"id":"%s","%s":"%s"}' % (rid, field, value)))
    like_rows = store.run(store.filter(table, store.P.field(field).starts_with(prefix)))
    range_rows = store.run(store.between(table, prefix, prefix + "￿",
                                         index=field, right_bound="closed"))
    assert {r["id"] for r in like_rows} == {r["id"] for r in range_rows}
    assert len(like_rows) == 1, "the scoped row must match: %r" % prefix


@requires_pg
def test_a_prefix_containing_like_metacharacters_is_escaped(pg_schema):
    schema.ensure_schema(tables=["NexusRuntimeState"])
    with dbpool.cursor() as cur:
        for rid in ("100%|a", "100X|a"):
            cur.execute('INSERT INTO "NexusRuntimeState" (id, doc) VALUES (%s,%s)',
                        (rid, '{"id":"%s"}' % rid))
    got = store.run(store.filter("NexusRuntimeState",
                                 store.P.field("id").starts_with("100%")))
    assert [r["id"] for r in got] == ["100%|a"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest backend/tests/db/test_store_query.py backend/tests/db/test_collation.py \
                  backend/tests/db/test_prefix_scan.py -v
```
Expected: FAIL — `AttributeError: module 'db.store' has no attribute 'P'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/db/store.py`:

```python
class _Sentinel:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return "store.%s" % self.name


MINVAL = _Sentinel("MINVAL")     # r.minval: omit the lower bound
MAXVAL = _Sentinel("MAXVAL")     # r.maxval: omit the upper bound


def escape_like(value: str) -> str:
    """Escape the three LIKE metacharacters. '|' needs no escaping here --
    only the regex form did."""
    return (value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))


@dataclass(frozen=True)
class Predicate:
    fragment: str
    params: tuple = ()

    def to_sql(self):
        return self.fragment, self.params

    def __and__(self, other: "Predicate") -> "Predicate":
        return Predicate("(%s) AND (%s)" % (self.fragment, other.fragment),
                         self.params + other.params)

    def __or__(self, other: "Predicate") -> "Predicate":
        return Predicate("(%s) OR (%s)" % (self.fragment, other.fragment),
                         self.params + other.params)

    def __invert__(self) -> "Predicate":
        # NOT NULL-safe: a NULL comparison is false, and its negation must be
        # false too, matching "row lacks the key" in ReQL rather than flipping.
        return Predicate("COALESCE(NOT (%s), false)" % self.fragment, self.params)


class FieldRef:
    """One doc key, plus the transforms the ported call sites actually use."""

    __slots__ = ("expr", "params")

    def __init__(self, expr: str, params: tuple = ()) -> None:
        self.expr = expr
        self.params = params

    # -- transforms -------------------------------------------------------
    def default(self, value: Any) -> "FieldRef":
        return FieldRef("coalesce(%s, %%s)" % self.expr, self.params + (value,))

    def coerce_to_string(self) -> "FieldRef":
        # ->> already stringifies; a JSON number 5 becomes '5'.
        return FieldRef(self.expr, self.params)

    def downcase(self) -> "FieldRef":
        return FieldRef("lower(%s)" % self.expr, self.params)

    def split_nth(self, sep: str, n: int) -> "FieldRef":
        # split_part is 1-based; ReQL .nth(0) is index 1.
        return FieldRef("split_part(%s, %%s, %%s)" % self.expr,
                        self.params + (sep, int(n) + 1))

    # -- comparisons ------------------------------------------------------
    def _cmp(self, op: str, value: Any) -> Predicate:
        return Predicate("%s %s %%s" % (self.expr, op), self.params + (value,))

    def eq(self, value: Any) -> Predicate:
        return self._cmp("=", value)

    def ne(self, value: Any) -> Predicate:
        return self._cmp("<>", value)

    def lt(self, value: Any) -> Predicate:
        return self._cmp("<", value)

    def le(self, value: Any) -> Predicate:
        return self._cmp("<=", value)

    def gt(self, value: Any) -> Predicate:
        return self._cmp(">", value)

    def ge(self, value: Any) -> Predicate:
        return self._cmp(">=", value)

    def is_in(self, seq: Sequence) -> Predicate:
        values = [v if isinstance(v, str) else str(v) for v in seq]
        if not values:
            return Predicate("false", ())
        return Predicate("%s = ANY(%%s)" % self.expr, self.params + (values,))

    def match(self, regex: str) -> Predicate:
        return Predicate("%s ~ %%s" % self.expr, self.params + (regex,))

    def starts_with(self, prefix: str) -> Predicate:
        """The LIKE form of a prefix scan. Under COLLATE "C" it returns exactly
        the same rows as between(prefix, prefix + '\\uffff', right_bound=
        'closed'), and it is backed by the "_pfx" text_pattern_ops index."""
        return Predicate("%s LIKE %%s" % self.expr,
                         self.params + (escape_like(prefix) + "%",))

    def is_null(self) -> Predicate:
        return Predicate("%s IS NULL" % self.expr, self.params)


class P:
    @staticmethod
    def field(key: str) -> FieldRef:
        if "'" in key:
            raise StoreError("illegal doc key %r" % key)
        return FieldRef("doc->>'%s'" % key)


def _predicate_from(predicate) -> Predicate:
    if isinstance(predicate, Predicate):
        return predicate
    if isinstance(predicate, dict):
        out = None
        for key, value in predicate.items():
            if value is None:
                # A dict value of None means the key is JSON null, not absent.
                term = Predicate("doc -> '%s' = 'null'::jsonb" % key, ())
            elif isinstance(value, bool):
                term = Predicate("doc -> '%s' = %%s::jsonb" % key,
                                 ("true" if value else "false",))
            elif isinstance(value, (int, float)):
                term = Predicate("(doc -> '%s')::numeric = %%s" % key, (value,))
            else:
                term = P.field(key).eq(value)
            out = term if out is None else (out & term)
        return out if out is not None else Predicate("true", ())
    raise StoreError("filter needs a dict or a Predicate, got %r" % type(predicate))


def filter(table: str, predicate) -> Selection:      # noqa: A001 - ReQL's name
    frag, params = _predicate_from(predicate).to_sql()
    return Selection(table).where(frag, params)


def _bound_column(table: str, index: Optional[str]) -> str:
    if index in (None, "id"):
        return "id"
    return '"%s"' % index


def between(table: str, lo, hi, *, index: Optional[str] = None,
            left_bound: str = "closed", right_bound: str = "open") -> Selection:
    """NEVER SQL BETWEEN: ReQL is [lo, hi); SQL BETWEEN is [lo, hi].

    r.minval / r.maxval omit the bound entirely, which is how
    interactive_utils.py:5246's [instance, minval] -> [instance, maxval] scan
    on a compound index becomes a plain equality on the instance.
    """
    col = _bound_column(table, index)
    sel = Selection(table)
    if lo is not None and not isinstance(lo, _Sentinel):
        op = ">" if left_bound == "open" else ">="
        sel = sel.where("%s %s %%s" % (col, op), (coerce_id(table, lo)
                                                  if col == "id" else lo,))
    if hi is not None and not isinstance(hi, _Sentinel):
        op = "<=" if right_bound == "closed" else "<"
        sel = sel.where("%s %s %%s" % (col, op), (coerce_id(table, hi)
                                                  if col == "id" else hi,))
    return sel


_INDEX_ORDER = {
    # ReQL secondary indexes that order_by(index=...) targets, mapped to the
    # generated/expression column they were built on.
    "list_ts": "(coalesce(doc->>'timestamp',''))",
    "instance_ts": "(coalesce(doc->>'instance_id', doc->>'instance',''))",
}


@dataclass(frozen=True)
class _RawOrder:
    """An ORDER BY term over an index expression rather than a doc key.

    Duck-typed against Order: Selection.to_sql only calls .to_sql() on each
    element, so the two types are interchangeable there.
    """
    sql_text: str

    def to_sql(self) -> str:
        return self.sql_text


def order_by(selection, *, index: Optional[str] = None,
             fields: Sequence = (), desc: bool = False) -> Selection:   # noqa: A002
    """ORDER BY over text always carries COLLATE "C"."""
    sel = _as_selection(selection)
    if index is None:
        return sel.ordered(tuple(fields))
    expr = _INDEX_ORDER.get(index)
    if expr is None:
        expr = "id" if index == "id" else '"%s"' % index
    direction = "DESC" if desc else "ASC"
    return sel.ordered(sel.orders + (_RawOrder("%s%s %s" % (expr, _C, direction)),))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest backend/tests/db/test_store_query.py backend/tests/db/test_collation.py \
                  backend/tests/db/test_prefix_scan.py -v
```
Expected: PASS — 17 + 4 + 17 tests (the prefix-scan file is parametrised over 15 targets).

- [ ] **Step 5: Commit**

```bash
git add backend/db/store.py backend/tests/db/test_store_query.py \
        backend/tests/db/test_collation.py backend/tests/db/test_prefix_scan.py
git commit -m "$(cat <<'EOF'
feat(db): predicate DSL, filter, half-open between, bytewise order_by

between never emits SQL BETWEEN (ReQL is [lo,hi)); minval/maxval omit the
bound. The LIKE prefix form and the >=/< range form are proved equal on all
15 scoped clear-state targets -- the clear_instance_state.py:100-104 silent
no-op regression. ORDER BY always carries COLLATE "C", proved against the
graph_nexus_analysis.py:11856 (latest_observation_date DESC, id DESC) window.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 10: `store.py` — writes

**Files:**
- Modify: `backend/db/store.py` (append the write section)
- Create: `backend/tests/db/test_store_writes.py`
- Create: `backend/tests/db/test_store_conflict.py`

**Interfaces:**
- Consumes: `Selection`, `coerce_id`, `Predicate`, `db.merge.encode_patch`, `db.schema.spec/quoted`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class InsertResult:   # ReQL-shaped; supports result["inserted"] too
      inserted: int = 0; replaced: int = 0; unchanged: int = 0
      skipped: int = 0;  errors: int = 0; first_error: str | None = None
      generated_keys: list = field(default_factory=list)

  @dataclass(frozen=True)
  class WriteResult:
      replaced: int = 0; unchanged: int = 0; deleted: int = 0
      skipped: int = 0;  errors: int = 0; first_error: str | None = None

  WRITE_CHUNK = 500
  def insert(table, doc_or_docs, *, conflict="error", durability="hard") -> InsertResult
  def update(table, selector, patch: dict) -> WriteResult
  def replace(table, row_id, doc) -> WriteResult
  def replace_if(table, row_id, *, when, doc, insert_if_absent=False) -> Doc | None
  def delete(table, selector) -> WriteResult
  ```

Both result types implement `__getitem__` and `.get(key, default)` so existing `result['errors']` / `result.get('replaced', 0)` call sites port with a name change only.

**Semantics rows from spec §4 this task must satisfy:** `conflict='error'` counts conflicts into `errors` with ReQL's wording ``Duplicate primary key `id` `` and does **not** abort the batch; `conflict='replace'` replaces the whole document including dropping keys; `conflict='update'` uses `jsonb_deep_merge`, never `||`; a multi-doc insert is partial-success via a savepoint per row, chunked at 500; `.update()` deep-merges; `r.literal` blanks a subtree; `.delete()` on a `Selection` is one statement, never fetch-then-delete; `durability=` is accepted and ignored; a write to a `pk_field != "id"` table without that field raises `StoreError`; `replace_if` distinguishes "predicate did not hold" from "row missing".

- [ ] **Step 1: Write the failing tests**

`backend/tests/db/test_store_writes.py`:

```python
import pytest

from db import pool as dbpool
from db import schema, store
from db.errors import StoreError
from db.merge import Literal
from db.store import P

from .conftest import requires_pg


@pytest.fixture
def tables(pg_schema):
    schema.ensure_schema(tables=["Instances", "DiscordOutbox", "kalshi_markets",
                                 "NexusRuntimeState"])
    return pg_schema


@requires_pg
def test_insert_then_get(tables):
    res = store.insert("Instances", {"id": 1, "name": "main"})
    assert res.inserted == 1 and res.errors == 0
    assert store.get("Instances", 1) == {"id": 1, "name": "main"}


@requires_pg
def test_insert_result_supports_dict_access(tables):
    res = store.insert("Instances", {"id": 1})
    assert res["inserted"] == 1 and res.get("errors", 0) == 0


@requires_pg
def test_durability_is_accepted_and_ignored(tables):
    assert store.insert("Instances", {"id": 3}, durability="soft").inserted == 1


@requires_pg
def test_update_deep_merges_objects_and_replaces_arrays(tables):
    store.insert("Instances", {"id": 1, "cfg": {"a": 1, "b": 2}, "syms": ["A", "B"]})
    store.update("Instances", 1, {"cfg": {"b": 9, "c": 3}, "syms": ["Z"]})
    assert store.get("Instances", 1) == {
        "id": 1, "cfg": {"a": 1, "b": 9, "c": 3}, "syms": ["Z"]}


@requires_pg
def test_update_creates_missing_intermediates(tables):
    store.insert("Instances", {"id": 1})
    store.update("Instances", 1, {"a": {"b": {"c": 1}}})
    assert store.get("Instances", 1)["a"] == {"b": {"c": 1}}


@requires_pg
def test_update_none_sets_json_null_it_does_not_delete(tables):
    store.insert("Instances", {"id": 1, "k": 5})
    store.update("Instances", 1, {"k": None})
    doc = store.get("Instances", 1)
    assert "k" in doc and doc["k"] is None


@requires_pg
def test_update_with_literal_blanks_a_subtree(tables):
    # purge_backtest_secrets.py:101-103
    store.insert("Instances", {"id": 1, "secrets": {"k": "v", "j": "w"}})
    store.update("Instances", 1, {"secrets": Literal({})})
    assert store.get("Instances", 1)["secrets"] == {}


@requires_pg
def test_update_over_a_selection_is_one_statement(tables):
    for i in (1, 2, 3):
        store.insert("DiscordOutbox", {"id": str(i), "kind": "x" if i < 3 else "y"})
    res = store.update("DiscordOutbox", store.filter("DiscordOutbox", {"kind": "x"}),
                       {"sent": True})
    assert res.replaced == 2
    assert store.get("DiscordOutbox", "3").get("sent") is None


@requires_pg
def test_update_of_a_missing_row_reports_skipped_not_replaced(tables):
    res = store.update("Instances", 999, {"a": 1})
    assert res.replaced == 0 and res.skipped == 1


@requires_pg
def test_replace_swaps_the_whole_document(tables):
    store.insert("Instances", {"id": 1, "a": 1, "b": 2})
    store.replace("Instances", 1, {"id": 1, "c": 3})
    assert store.get("Instances", 1) == {"id": 1, "c": 3}


@requires_pg
def test_replace_if_writes_when_the_predicate_holds(tables):
    store.insert("Instances", {"id": 1, "status": "paused_llm_critical"})
    got = store.replace_if(
        "Instances", 1,
        when=P.field("status").default("").eq("paused_llm_critical"),
        doc={"id": 1, "status": "running"})
    assert got == {"id": 1, "status": "running"}
    assert store.get("Instances", 1)["status"] == "running"


@requires_pg
def test_replace_if_returns_none_when_the_predicate_does_not_hold(tables):
    store.insert("Instances", {"id": 1, "status": "running"})
    got = store.replace_if("Instances", 1,
                           when=P.field("status").default("").eq("paused_llm_critical"),
                           doc={"id": 1, "status": "x"})
    assert got is None
    assert store.get("Instances", 1)["status"] == "running"


@requires_pg
def test_replace_if_distinguishes_missing_row_from_failed_predicate(tables):
    with pytest.raises(StoreError):
        store.replace_if("Instances", 404, when=P.field("status").eq("x"),
                         doc={"id": 404})
    got = store.replace_if("Instances", 404, when=None, doc={"id": 404},
                           insert_if_absent=True)
    assert got == {"id": 404}


@requires_pg
def test_delete_by_id(tables):
    store.insert("Instances", {"id": 1})
    assert store.delete("Instances", 1).deleted == 1
    assert store.get("Instances", 1) is None


@requires_pg
def test_delete_over_a_selection_is_one_statement(tables):
    for i in (1, 2, 3):
        store.insert("DiscordOutbox", {"id": str(i), "kind": "x" if i < 3 else "y"})
    assert store.delete("DiscordOutbox",
                        store.filter("DiscordOutbox", {"kind": "x"})).deleted == 2
    assert store.count("DiscordOutbox") == 1


@requires_pg
def test_non_id_primary_key_is_copied_from_its_doc_field(tables):
    store.insert("kalshi_markets", {"market_ticker": "KXM-26", "yes_bid": 40})
    got = store.get("kalshi_markets", "KXM-26")
    assert got == {"market_ticker": "KXM-26", "yes_bid": 40}


@requires_pg
def test_writing_a_document_without_its_pk_field_raises(tables):
    with pytest.raises(StoreError):
        store.insert("kalshi_markets", {"yes_bid": 40})


@requires_pg
def test_nan_is_rejected_at_the_client(tables):
    with pytest.raises(ValueError):
        store.insert("Instances", {"id": 1, "rsi": float("nan")})


@requires_pg
def test_int_table_rejects_a_non_integer_id(tables):
    with pytest.raises(StoreError):
        store.insert("Instances", {"id": "abc"})


@requires_pg
def test_updated_at_advances_on_every_write(tables):
    store.insert("Instances", {"id": 1})
    first = store.sql('SELECT updated_at FROM "Instances" WHERE id=%s', ("1",))[0]
    store.update("Instances", 1, {"a": 1})
    second = store.sql('SELECT updated_at FROM "Instances" WHERE id=%s', ("1",))[0]
    assert second["updated_at"] >= first["updated_at"]
```

`backend/tests/db/test_store_conflict.py`:

```python
import pytest

from db import schema, store

from .conftest import requires_pg


@pytest.fixture
def outbox(pg_schema):
    schema.ensure_schema(tables=["DiscordOutbox"])
    return pg_schema


@requires_pg
def test_conflict_error_records_the_conflict_without_aborting_the_batch(outbox):
    store.insert("DiscordOutbox", {"id": "a", "n": 1})
    res = store.insert("DiscordOutbox",
                       [{"id": "a", "n": 2}, {"id": "b", "n": 3}])
    assert res.inserted == 1
    assert res.errors == 1
    assert "Duplicate primary key `id`" in res.first_error
    assert store.get("DiscordOutbox", "a")["n"] == 1     # unchanged
    assert store.get("DiscordOutbox", "b")["n"] == 3     # the batch continued


@requires_pg
def test_conflict_replace_drops_keys_the_new_document_lacks(outbox):
    store.insert("DiscordOutbox", {"id": "a", "keep": 1, "drop": 2})
    res = store.insert("DiscordOutbox", {"id": "a", "keep": 9}, conflict="replace")
    assert res.replaced == 1
    assert store.get("DiscordOutbox", "a") == {"id": "a", "keep": 9}


@requires_pg
def test_conflict_update_deep_merges_it_does_not_use_shallow_concat(outbox):
    store.insert("DiscordOutbox", {"id": "a", "cfg": {"x": 1, "y": 2}})
    store.insert("DiscordOutbox", {"id": "a", "cfg": {"y": 9}}, conflict="update")
    # `||` would have dropped "x". jsonb_deep_merge must keep it.
    assert store.get("DiscordOutbox", "a")["cfg"] == {"x": 1, "y": 9}


@requires_pg
def test_a_500_doc_chunk_with_two_duplicates_is_partial_success(outbox):
    store.insert("DiscordOutbox", [{"id": "d%03d" % i} for i in (7, 400)])
    docs = [{"id": "d%03d" % i, "n": i} for i in range(500)]
    res = store.insert("DiscordOutbox", docs)
    assert res.inserted == 498
    assert res.errors == 2
    assert "Duplicate primary key `id`" in res.first_error
    assert store.count("DiscordOutbox") == 500


@requires_pg
def test_inserts_are_chunked_at_500(outbox):
    assert store.WRITE_CHUNK == 500
    res = store.insert("DiscordOutbox", [{"id": "x%04d" % i} for i in range(1200)])
    assert res.inserted == 1200 and res.errors == 0


@requires_pg
def test_unchanged_is_reported_when_a_replace_writes_identical_bytes(outbox):
    store.insert("DiscordOutbox", {"id": "a", "n": 1})
    res = store.insert("DiscordOutbox", {"id": "a", "n": 1}, conflict="replace")
    assert res.unchanged == 1 and res.replaced == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest backend/tests/db/test_store_writes.py backend/tests/db/test_store_conflict.py -v
```
Expected: FAIL — `AttributeError: module 'db.store' has no attribute 'insert'`

- [ ] **Step 3: Write minimal implementation**

Append to `backend/db/store.py`:

```python
WRITE_CHUNK = 500     # self_learning/store.py:64 -- a 15k-document insert is
                      # one oversized request that can fail as a unit.

_DUP_ERROR = "Duplicate primary key `id`"


class _ResultMixin:
    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def get(self, key, default=None):
        return getattr(self, key, default)


@dataclass(frozen=True)
class InsertResult(_ResultMixin):
    inserted: int = 0
    replaced: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    first_error: Optional[str] = None
    generated_keys: list = None

    def __post_init__(self):
        if self.generated_keys is None:
            object.__setattr__(self, "generated_keys", [])


@dataclass(frozen=True)
class WriteResult(_ResultMixin):
    replaced: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: int = 0
    first_error: Optional[str] = None


def _row_id_for(table: str, doc: Doc) -> str:
    spec_ = dbschema.spec(table)
    if spec_.pk_field not in doc or doc[spec_.pk_field] is None:
        raise StoreError("%s: document is missing its primary key field %r"
                         % (table, spec_.pk_field))
    return coerce_id(table, doc[spec_.pk_field])


def insert(table: str, doc_or_docs, *, conflict: str = "error",
           durability: str = "hard") -> InsertResult:
    """``durability`` is accepted and ignored: Postgres is durable by default,
    and the parameter stays in the signature so backtest_replay's interface and
    its ~10 test doubles are unchanged.

    ReQL multi-insert is partial-success; a plain multi-row INSERT is
    all-or-nothing, so each row gets its own savepoint.
    """
    if conflict not in ("error", "replace", "update"):
        raise StoreError("conflict must be error|replace|update, got %r" % conflict)
    docs = [doc_or_docs] if isinstance(doc_or_docs, dict) else list(doc_or_docs)
    if not docs:
        return InsertResult()
    q = dbschema.quoted(table)
    if conflict == "replace":
        tail = (" ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc, "
                "updated_at = now() WHERE %s.doc IS DISTINCT FROM EXCLUDED.doc" % q)
    elif conflict == "update":
        # NOT `||` -- `||` is shallow and silently drops sibling keys.
        tail = (" ON CONFLICT (id) DO UPDATE SET "
                "doc = jsonb_deep_merge(%s.doc, EXCLUDED.doc), updated_at = now()" % q)
    else:
        tail = " ON CONFLICT (id) DO NOTHING"
    statement = ("INSERT INTO %s (id, doc) VALUES (%%s, %%s::jsonb)%s "
                 "RETURNING (xmax = 0) AS was_insert" % (q, tail))

    inserted = replaced = unchanged = errors = 0
    first_error = None
    for start in range(0, len(docs), WRITE_CHUNK):
        chunk = docs[start:start + WRITE_CHUNK]
        with dbpool.connection() as conn:
            with conn.cursor() as cur:
                for doc in chunk:
                    row_id = _row_id_for(table, doc)
                    payload = dbjson.dumps(doc)      # raises on NaN, at the client
                    cur.execute("SAVEPOINT s")
                    try:
                        cur.execute(statement, (row_id, payload))
                        row = cur.fetchone()
                        cur.execute("RELEASE SAVEPOINT s")
                    except Exception as exc:
                        cur.execute("ROLLBACK TO SAVEPOINT s")
                        errors += 1
                        first_error = first_error or str(exc)
                        continue
                    if row is None:                  # DO NOTHING absorbed it
                        errors += 1
                        first_error = first_error or _DUP_ERROR
                    elif row["was_insert"]:
                        inserted += 1
                    elif conflict == "replace" or conflict == "update":
                        replaced += 1
                    else:
                        unchanged += 1
                # A replace that wrote identical bytes is filtered out by the
                # IS DISTINCT FROM guard: no row returns, and that is
                # "unchanged", not an error.
            conn.commit()
    if conflict == "replace" and errors:
        unchanged += errors
        errors = 0
        first_error = None
    return InsertResult(inserted=inserted, replaced=replaced, unchanged=unchanged,
                        errors=errors, first_error=first_error)


def _selector_to_selection(table: str, selector) -> Selection:
    if isinstance(selector, Selection):
        return selector
    return Selection(table).where("id = %s", (coerce_id(table, selector),))


def update(table: str, selector, patch: Doc) -> WriteResult:
    """UPDATE ... SET doc = jsonb_deep_merge(doc, patch). One statement, even
    over a Selection -- ReQL's .filter(...).update() is server-side too."""
    sel = _selector_to_selection(table, selector)
    where, params = sel.where_sql()
    payload = dbjson.dumps(encode_patch(patch))
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE %s SET doc = jsonb_deep_merge(doc, %%s::jsonb), "
                "updated_at = now()%s" % (dbschema.quoted(table), where),
                (payload,) + params)
            n = cur.rowcount
        conn.commit()
    if n == 0 and not isinstance(selector, Selection):
        return WriteResult(skipped=1)
    return WriteResult(replaced=n)


def replace(table: str, row_id, doc: Doc) -> WriteResult:
    payload = dbjson.dumps(doc)
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE %s SET doc = %%s::jsonb, updated_at = now() "
                        "WHERE id = %%s" % dbschema.quoted(table),
                        (payload, coerce_id(table, row_id)))
            n = cur.rowcount
        conn.commit()
    return WriteResult(replaced=n) if n else WriteResult(skipped=1)


def replace_if(table: str, row_id, *, when, doc: Doc,
               insert_if_absent: bool = False) -> Optional[Doc]:
    """Atomic compare-and-swap, backing the 5 ``.replace(lambda row:
    r.branch(...))`` sites. Returns the document on success and None when the
    predicate did not hold. A missing row is NOT conflated with a failed
    predicate: it raises unless insert_if_absent is set.
    """
    rid = coerce_id(table, row_id)
    q = dbschema.quoted(table)
    payload = dbjson.dumps(doc)
    frag, params = (when.to_sql() if when is not None else ("true", ()))
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM %s WHERE id = %%s FOR UPDATE" % q, (rid,))
            exists = cur.fetchone() is not None
            if not exists:
                if not insert_if_absent:
                    conn.rollback()
                    raise StoreError("%s: row %r does not exist" % (table, row_id))
                cur.execute("INSERT INTO %s (id, doc) VALUES (%%s, %%s::jsonb) "
                            "ON CONFLICT (id) DO NOTHING" % q, (rid, payload))
                conn.commit()
                return doc
            cur.execute("UPDATE %s SET doc = %%s::jsonb, updated_at = now() "
                        "WHERE id = %%s AND (%s)" % (q, frag),
                        (payload, rid) + params)
            n = cur.rowcount
        conn.commit()
    return doc if n else None


def delete(table: str, selector) -> WriteResult:
    """On a Selection this is ONE statement -- never fetch-then-delete."""
    sel = _selector_to_selection(table, selector)
    where, params = sel.where_sql()
    with dbpool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM %s%s" % (dbschema.quoted(table), where), params)
            n = cur.rowcount
        conn.commit()
    return WriteResult(deleted=n)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest backend/tests/db/test_store_writes.py backend/tests/db/test_store_conflict.py -v
```
Expected: PASS — 20 + 6 tests

- [ ] **Step 5: Commit**

```bash
git add backend/db/store.py backend/tests/db/test_store_writes.py \
        backend/tests/db/test_store_conflict.py
git commit -m "$(cat <<'EOF'
feat(db): store writes with ReQL-shaped partial-success results

conflict='error' records "Duplicate primary key `id`" into errors without
aborting the batch, exactly as ReQL does; a savepoint per row inside a
500-document chunk is what preserves {inserted: 498, errors: 2}.
conflict='update' uses jsonb_deep_merge, never `||`. delete() and update()
over a Selection each compile to one server-side statement. durability= is
accepted and ignored so backtest_replay's ~10 test doubles are unchanged.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 11: `watch.py` — LISTEN/NOTIFY watchers with a poll backstop

**Files:**
- Create: `backend/db/watch.py`
- Create: `backend/tests/db/test_watch.py`

**Interfaces:**
- Consumes: `db.pool.listen_connection()`, `db.store.get/run/filter/Selection`, `db.schema.spec`.
- Produces:
  ```python
  Change = dict            # {"old_val": doc|None, "new_val": doc|None}

  class Watcher:
      def start(self) -> None
      def stop(self, timeout: float = 5.0) -> None
      def is_alive(self) -> bool

  def watch_row(table, row_id, on_change, *, label, include_initial=True,
                squash=False, squash_window=1.0, poll_interval=2.0,
                log=None, should_continue=None) -> Watcher
  def watch_table(table, on_change, *, label, fields=None, include_initial=True,
                  squash=False, squash_window=1.0, poll_interval=2.0,
                  log=None, should_continue=None) -> Watcher
  def watch_filter(table, predicate, on_change, *, label, **kwargs) -> Watcher
  def feed(table, *, row_id=None, predicate=None, fields=None,
           include_initial=True, squash=False, poll_interval=2.0,
           should_continue=None) -> Iterator[Change]
  ```

**Behaviour contract (spec §5.1), every clause of which gets a test:** re-read on start; **re-read on every reconnect** (the fix for the 8 event-losing sites and the 3 with no reconnect at all); `old_val` from the watcher's per-id cache, `None` on first sighting after a restart; a missing row after a notification emits `{"old_val": cached, "new_val": None}` then drops the cache entry (`broker.py:5833` depends on `new_val is None` meaning "queue row deleted"); `squash=True` coalesces same-id notifications inside `squash_window` and delivers the oldest `old_val` with the newest `new_val`; a poll every `poll_interval` seconds (env `DB_WATCH_POLL_SECONDS`) re-reads and diffs regardless of notifications; handler exceptions are logged and swallowed — the watcher never dies; backoff 2 s → 30 s ×1.5, reset only after a change is **delivered**.

**LISTEN hygiene:** dedicated unpooled autocommit connection; `notifies()` generator only, never `add_notify_handler()`; `LISTEN` re-issued after every reconnect; a finite `timeout=` is a resync tick, not an error; never routed through pgbouncer transaction pooling.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_watch.py`:

```python
import threading
import time

import pytest

from db import pool as dbpool
from db import schema, store, watch

from .conftest import requires_pg


def _collect():
    changes, lock = [], threading.Lock()

    def on_change(c):
        with lock:
            changes.append(c)

    return changes, on_change


def _wait_for(predicate, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def watched(pg_schema):
    schema.ensure_schema(tables=["Instances", "EngineControl", "BacktestInstances"])
    return pg_schema


@requires_pg
def test_watch_row_emits_current_state_on_start(watched):
    store.insert("Instances", {"id": 1, "strategy_id": 179})
    changes, on_change = _collect()
    w = watch.watch_row("Instances", 1, on_change, label="t")
    w.start()
    try:
        assert _wait_for(lambda: len(changes) >= 1)
        assert changes[0] == {"old_val": None,
                              "new_val": {"id": 1, "strategy_id": 179}}
    finally:
        w.stop()


@requires_pg
def test_watch_row_include_initial_false_seeds_the_cache_silently(watched):
    store.insert("Instances", {"id": 1, "n": 1})
    changes, on_change = _collect()
    w = watch.watch_row("Instances", 1, on_change, label="t", include_initial=False)
    w.start()
    try:
        time.sleep(1.0)
        assert changes == []
        store.update("Instances", 1, {"n": 2})
        assert _wait_for(lambda: len(changes) >= 1)
        assert changes[0]["old_val"]["n"] == 1 and changes[0]["new_val"]["n"] == 2
    finally:
        w.stop()


@requires_pg
def test_old_val_comes_from_the_cache(watched):
    """broker.py:5730-5732 diffs strategy_id and crypto_config across
    old_val/new_val -- the BUG #6 class."""
    store.insert("Instances", {"id": 1, "strategy_id": 1,
                               "crypto_config": {"strategy": "meanrev"}})
    changes, on_change = _collect()
    w = watch.watch_row("Instances", 1, on_change, label="t", include_initial=False)
    w.start()
    try:
        store.update("Instances", 1, {"crypto_config": {"strategy": "adaptive"}})
        assert _wait_for(lambda: len(changes) >= 1)
        c = changes[-1]
        assert c["old_val"]["crypto_config"]["strategy"] == "meanrev"
        assert c["new_val"]["crypto_config"]["strategy"] == "adaptive"
    finally:
        w.stop()


@requires_pg
def test_deletion_emits_new_val_none(watched):
    store.insert("BacktestInstances", {"id": 5, "run": True})
    changes, on_change = _collect()
    w = watch.watch_row("BacktestInstances", 5, on_change, label="t",
                        include_initial=False)
    w.start()
    try:
        store.delete("BacktestInstances", 5)
        assert _wait_for(lambda: any(c["new_val"] is None for c in changes))
        c = [c for c in changes if c["new_val"] is None][0]
        assert c["old_val"]["run"] is True
    finally:
        w.stop()


@requires_pg
def test_reconnect_re_reads_and_emits_what_moved_while_blind(watched):
    """The strict upgrade over today's behaviour: 8 sites lose events on
    reconnect and 3 have no reconnect at all."""
    store.insert("Instances", {"id": 1, "n": 1})
    changes, on_change = _collect()
    w = watch.watch_row("Instances", 1, on_change, label="t", include_initial=False)
    w.start()
    try:
        assert _wait_for(lambda: w.is_alive())
        # Kill the watcher's LISTEN backend from another session, then change
        # the row while it is blind.
        store.sql("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                  "WHERE query LIKE 'LISTEN %%' AND pid <> pg_backend_pid()")
        store.update("Instances", 1, {"n": 99})
        assert _wait_for(lambda: any((c["new_val"] or {}).get("n") == 99
                                     for c in changes), timeout=40)
    finally:
        w.stop()


@requires_pg
def test_poll_backstop_catches_a_change_with_notify_suppressed(watched):
    schema.ensure_schema(tables=["Instances"])
    store.sql('DROP TRIGGER IF EXISTS "Instances_notify" ON "Instances"')
    store.insert("Instances", {"id": 1, "n": 1})
    changes, on_change = _collect()
    w = watch.watch_row("Instances", 1, on_change, label="t",
                        include_initial=False, poll_interval=0.5)
    w.start()
    try:
        store.update("Instances", 1, {"n": 2})
        assert _wait_for(lambda: any((c["new_val"] or {}).get("n") == 2
                                     for c in changes), timeout=10)
    finally:
        w.stop()


@requires_pg
def test_squash_coalesces_same_id_changes(watched):
    store.insert("Instances", {"id": 1, "n": 0})
    changes, on_change = _collect()
    w = watch.watch_row("Instances", 1, on_change, label="t",
                        include_initial=False, squash=True, squash_window=1.0)
    w.start()
    try:
        for n in range(1, 6):
            store.update("Instances", 1, {"n": n})
        assert _wait_for(lambda: any((c["new_val"] or {}).get("n") == 5
                                     for c in changes), timeout=10)
        assert len(changes) < 5, "squash must coalesce: %r" % changes
        assert changes[-1]["old_val"]["n"] == 0     # oldest old_val kept
    finally:
        w.stop()


@requires_pg
def test_watch_table_emits_every_row_on_start(watched):
    for i in (1, 2):
        store.insert("EngineControl", {"id": "e%d" % i, "running": False})
    changes, on_change = _collect()
    w = watch.watch_table("EngineControl", on_change, label="t")
    w.start()
    try:
        assert _wait_for(lambda: len(changes) >= 2)
        assert {c["new_val"]["id"] for c in changes[:2]} == {"e1", "e2"}
    finally:
        w.stop()


@requires_pg
def test_watch_table_fields_projects_old_and_new(watched):
    """self_learning_engine.py:534's server-side pluck replacement."""
    schema.ensure_schema(tables=["BacktestProgress"])
    store.sql('INSERT INTO "BacktestProgress" (id, status, progress) '
              "VALUES ('1','running',0.5)")
    changes, on_change = _collect()
    w = watch.watch_table("BacktestProgress", on_change, label="t",
                          fields=("id", "status"))
    w.start()
    try:
        assert _wait_for(lambda: len(changes) >= 1)
        assert set(changes[0]["new_val"]) == {"id", "status"}
    finally:
        w.stop()


@requires_pg
def test_watch_filter_only_delivers_matching_rows(watched):
    schema.ensure_schema(tables=["KalshiBacktests"])
    changes, on_change = _collect()
    w = watch.watch_filter("KalshiBacktests", {"status": "pending"}, on_change,
                           label="t", include_initial=False)
    w.start()
    try:
        store.insert("KalshiBacktests", {"id": "k1", "status": "running"})
        store.insert("KalshiBacktests", {"id": "k2", "status": "pending"})
        assert _wait_for(lambda: len(changes) >= 1)
        time.sleep(1.0)
        assert [c["new_val"]["id"] for c in changes] == ["k2"]
    finally:
        w.stop()


@requires_pg
def test_a_raising_handler_never_kills_the_watcher(watched):
    store.insert("Instances", {"id": 1, "n": 0})
    seen = []

    def boom(change):
        seen.append(change)
        raise RuntimeError("handler blew up")

    w = watch.watch_row("Instances", 1, boom, label="t", include_initial=False)
    w.start()
    try:
        store.update("Instances", 1, {"n": 1})
        assert _wait_for(lambda: len(seen) >= 1)
        store.update("Instances", 1, {"n": 2})
        assert _wait_for(lambda: len(seen) >= 2)
        assert w.is_alive()
    finally:
        w.stop()


@requires_pg
def test_feed_yields_the_same_change_dicts(watched):
    store.insert("Instances", {"id": 1, "n": 1})
    gen = watch.feed("Instances", row_id=1, include_initial=True)
    try:
        first = next(gen)
        assert first == {"old_val": None, "new_val": {"id": 1, "n": 1}}
    finally:
        gen.close()


@requires_pg
def test_stop_joins_the_thread(watched):
    w = watch.watch_table("EngineControl", lambda c: None, label="t")
    w.start()
    assert w.is_alive()
    w.stop()
    assert not w.is_alive()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_watch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.watch'`

- [ ] **Step 3: Write minimal implementation**

`backend/db/watch.py`:

```python
"""Change watchers over LISTEN/NOTIFY with a poll backstop.

Delivers ReQL-shaped changes -- ``{"old_val": doc|None, "new_val": doc|None}``
-- so handlers written against ReQL changefeeds port with no shape edit.

Three properties the RethinkDB feeds do not have:

  * the watcher re-reads current state on start AND on every reconnect, so a
    stop issued while disconnected is no longer silently dropped (8 of the 23
    sites lose events today; 3 have no reconnect at all);
  * a poll every poll_interval seconds re-reads and diffs regardless of
    notifications, so a missed NOTIFY costs at most one interval;
  * a handler exception is logged and swallowed -- the watcher never dies.

The NOTIFY payload is the row id only (never data: the payload cap is 8000
bytes), so old_val comes from the watcher's per-id cache.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Iterator, Optional, Sequence

from . import pool as dbpool
from . import store as dbstore

Change = dict

DEFAULT_POLL = float(os.environ.get("DB_WATCH_POLL_SECONDS", "2.0"))
_INITIAL_BACKOFF = 2.0
_MAX_BACKOFF = 30.0


def _project(doc, fields):
    if doc is None or not fields:
        return doc
    return {k: doc[k] for k in fields if k in doc}


class Watcher:
    """One daemon thread: LISTEN + poll + diff for one watched set."""

    def __init__(self, table: str, on_change: Callable[[Change], None], *,
                 label: str, row_id: Any = None, predicate: Any = None,
                 fields: Optional[Sequence] = None, include_initial: bool = True,
                 squash: bool = False, squash_window: float = 1.0,
                 poll_interval: float = DEFAULT_POLL, log=None,
                 should_continue: Optional[Callable[[], bool]] = None) -> None:
        self.table = table
        self.on_change = on_change
        self.label = label
        self.row_id = row_id
        self.predicate = predicate
        self.fields = tuple(fields) if fields else None
        self.include_initial = include_initial
        self.squash = squash
        self.squash_window = squash_window
        self.poll_interval = poll_interval
        self.log = log
        self._cont = should_continue or (lambda: True)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cache: dict = {}
        self._seeded = False
        self._pending: dict = {}          # id -> (oldest_old_val, deadline)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="watch:%s" % self.label)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t is not None:
            t.join(timeout=timeout)

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- logging ----------------------------------------------------------
    def _log(self, msg: str, color: str = "yellow") -> None:
        if self.log is None:
            return
        try:
            self.log("%s: %s" % (self.label, msg), color, service="Postgres")
        except Exception:
            pass

    # -- reads ------------------------------------------------------------
    def _snapshot(self) -> dict:
        """Current state of the watched set, keyed by row id."""
        if self.row_id is not None:
            doc = dbstore.get(self.table, self.row_id)
            key = dbstore.coerce_id(self.table, self.row_id)
            return {key: doc} if doc is not None else {}
        if self.predicate is not None:
            rows = dbstore.run(dbstore.filter(self.table, self.predicate))
        else:
            rows = dbstore.run(dbstore.Selection(self.table))
        out = {}
        pk_field = _pk_field(self.table)
        for row in rows:
            out[str(row.get(pk_field))] = row
        return out

    # -- delivery ---------------------------------------------------------
    def _deliver(self, old_val, new_val) -> None:
        change = {"old_val": _project(old_val, self.fields),
                  "new_val": _project(new_val, self.fields)}
        try:
            self.on_change(change)
        except Exception as exc:            # never let a handler kill the feed
            self._log("handler error: %s: %s" % (type(exc).__name__, exc), "red")

    def _emit(self, key: str, old_val, new_val) -> bool:
        """Returns True when something was actually delivered."""
        if old_val == new_val:
            return False
        if not self.squash:
            self._deliver(old_val, new_val)
            return True
        oldest, _ = self._pending.get(key, (old_val, 0.0))
        self._pending[key] = (oldest, time.time() + self.squash_window)
        return False

    def _flush_squashed(self) -> bool:
        if not self._pending:
            return False
        now = time.time()
        delivered = False
        for key in list(self._pending):
            oldest, deadline = self._pending[key]
            if now < deadline:
                continue
            del self._pending[key]
            self._deliver(oldest, self._cache.get(key))
            delivered = True
        return delivered

    def _diff(self, snapshot: dict) -> bool:
        delivered = False
        for key, new_val in snapshot.items():
            old_val = self._cache.get(key)
            if self._emit(key, old_val, new_val):
                delivered = True
            self._cache[key] = new_val
        for key in list(self._cache):
            if key not in snapshot:
                # broker.py:5833 needs new_val is None to mean "row deleted".
                if self._emit(key, self._cache[key], None):
                    delivered = True
                del self._cache[key]
        return delivered

    def _resync(self) -> bool:
        snapshot = self._snapshot()
        if not self._seeded and not self.include_initial:
            # kalshi/backtest_worker.py:256 seeds silently.
            self._cache = dict(snapshot)
            self._seeded = True
            return False
        self._seeded = True
        return self._diff(snapshot)

    # -- main loop --------------------------------------------------------
    def _run(self) -> None:
        backoff = _INITIAL_BACKOFF
        while self._cont() and not self._stop.is_set():
            conn = None
            try:
                conn = dbpool.listen_connection()
                conn.execute('LISTEN "tbl:%s"' % self.table)
                if self._resync():
                    backoff = _INITIAL_BACKOFF
                last_poll = time.time()
                for _notify in conn.notifies(timeout=self.poll_interval,
                                             stop_after=None):
                    if self._stop.is_set() or not self._cont():
                        break
                    if self._resync():
                        backoff = _INITIAL_BACKOFF
                    if self._flush_squashed():
                        backoff = _INITIAL_BACKOFF
                    last_poll = time.time()
                    if self._stop.is_set():
                        break
                # notifies() returning is the resync tick (finite timeout) or a
                # dropped connection; either way the loop re-reads above.
                if not self._stop.is_set():
                    if time.time() - last_poll >= self.poll_interval:
                        self._resync()
                        self._flush_squashed()
                    continue
            except Exception as exc:
                self._log("connection lost (%s); reconnecting in %.0fs"
                          % (exc, backoff))
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 1.5, _MAX_BACKOFF)


def _pk_field(table: str) -> str:
    from . import schema as dbschema
    return dbschema.spec(table).pk_field


def watch_row(table: str, row_id, on_change, *, label: str,
              include_initial: bool = True, squash: bool = False,
              squash_window: float = 1.0, poll_interval: float = DEFAULT_POLL,
              log=None, should_continue=None) -> Watcher:
    return Watcher(table, on_change, label=label, row_id=row_id,
                   include_initial=include_initial, squash=squash,
                   squash_window=squash_window, poll_interval=poll_interval,
                   log=log, should_continue=should_continue)


def watch_table(table: str, on_change, *, label: str,
                fields: Optional[Sequence] = None, include_initial: bool = True,
                squash: bool = False, squash_window: float = 1.0,
                poll_interval: float = DEFAULT_POLL, log=None,
                should_continue=None) -> Watcher:
    return Watcher(table, on_change, label=label, fields=fields,
                   include_initial=include_initial, squash=squash,
                   squash_window=squash_window, poll_interval=poll_interval,
                   log=log, should_continue=should_continue)


def watch_filter(table: str, predicate, on_change, *, label: str,
                 **kwargs) -> Watcher:
    return Watcher(table, on_change, label=label, predicate=predicate, **kwargs)


def feed(table: str, *, row_id=None, predicate=None, fields=None,
         include_initial: bool = True, squash: bool = False,
         poll_interval: float = DEFAULT_POLL,
         should_continue=None) -> Iterator:
    """A blocking generator over the same Change dicts a Watcher delivers.

    This is what ``run_reconnecting_changefeed``'s ``open_feed(conn)`` returns
    after the port: callers write ``lambda c: watch.feed(T,
    include_initial=True)`` instead of
    ``lambda c: r.db(DB).table(T).changes().run(c)``.
    """
    import queue as _queue
    q: "_queue.Queue" = _queue.Queue()
    w = Watcher(table, q.put, label="feed:%s" % table, row_id=row_id,
                predicate=predicate, fields=fields,
                include_initial=include_initial, squash=squash,
                poll_interval=poll_interval, should_continue=should_continue)
    w.start()
    try:
        while True:
            try:
                yield q.get(timeout=poll_interval)
            except _queue.Empty:
                if not w.is_alive():
                    return
    finally:
        w.stop()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest backend/tests/db/test_watch.py -v`
Expected: PASS, 13 tests. `test_reconnect_re_reads_and_emits_what_moved_while_blind` is slow (it waits out one backoff); that is the point.

- [ ] **Step 5: Commit**

```bash
git add backend/db/watch.py backend/tests/db/test_watch.py
git commit -m "$(cat <<'EOF'
feat(db): LISTEN/NOTIFY watchers that re-read on start and on reconnect

The NOTIFY payload is the id only, so old_val comes from a per-id cache --
broker.py:5730-5732 diffs strategy_id and crypto_config across it, and
broker.py:5833 needs new_val is None to mean "queue row deleted". Re-reading
on every reconnect plus a 2s poll backstop is a strict upgrade over the 8
sites that lose events today and the 3 with no reconnect at all. Handler
exceptions are logged and swallowed; the watcher never dies.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 12: Reimplement `run_reconnecting_changefeed` on `watch.feed`

**Files:**
- Modify: `backend/rethink_changefeed.py` (whole file rewritten; `_TRANSIENT_HINTS` at `:20-37`, `is_transient_rethinkdb_error` at `:39-60`, `run_reconnecting_changefeed` at `:63-155`)
- Create: `backend/tests/db/test_changefeed_runner.py`
- Check (do not edit yet): `backend/tests/test_changefeed_selfheal.py` must still pass unchanged

**Interfaces:**
- Consumes: `db.pool.listen_connection`, `db.watch.feed`.
- Produces (signature **unchanged**, so its ~6 call sites port with an import change only):
  ```python
  def run_reconnecting_changefeed(open_feed, handle_change, label, *, get_conn,
                                  log=None, pass_conn=True, initial_delay=2.0,
                                  max_delay=30.0, sleep=time.sleep,
                                  should_continue=None) -> None
  def is_transient_db_error(exc: BaseException) -> bool
  is_transient_rethinkdb_error = is_transient_db_error      # alias, one release
  ```

**What changes is the contract of the two callables, not the arity:**
- `open_feed(conn)` must return an **iterator of `Change` dicts**. Callers stop writing `lambda c: r.db(DB).table(T).changes().run(c)` and write `lambda c: watch.feed(T, include_initial=True)`.
- `get_conn` is called for the "connection", which for Postgres is `pool.listen_connection()`. `pass_conn=True` still hands it to the handler; handlers that used it to issue queries now ignore it and call `store` directly. `pass_conn` is kept so no call site's arity changes in the same commit as its body.

**Reclassification:** transient means `psycopg.OperationalError`, `psycopg_pool.PoolTimeout`, `OSError`/`ConnectionError`, plus the same substring hints **minus the four RethinkDB-specific ones**. Run `gitnexus_impact({target: "run_reconnecting_changefeed", direction: "upstream"})` first and report the blast radius — this symbol has ~6 runtime callers and a dedicated test file.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_changefeed_runner.py`:

```python
import itertools

import pytest

import rethink_changefeed as rcf


class _Boom(Exception):
    pass


def test_is_transient_db_error_covers_psycopg_operational_errors():
    import psycopg
    assert rcf.is_transient_db_error(psycopg.OperationalError("server closed"))


def test_is_transient_db_error_covers_pool_timeout():
    from psycopg_pool import PoolTimeout
    assert rcf.is_transient_db_error(PoolTimeout("no connection"))


def test_is_transient_db_error_covers_os_errors():
    assert rcf.is_transient_db_error(ConnectionResetError(104, "reset"))


def test_is_transient_db_error_matches_substring_hints():
    assert rcf.is_transient_db_error(_Boom("connection is broken"))
    assert rcf.is_transient_db_error(_Boom("could not connect"))


def test_is_transient_db_error_rejects_a_programming_error():
    assert rcf.is_transient_db_error(_Boom("column doc does not exist")) is False


def test_the_old_name_is_still_importable():
    assert rcf.is_transient_rethinkdb_error is rcf.is_transient_db_error


def test_no_rethinkdb_import_remains():
    import inspect
    assert "rethinkdb" not in inspect.getsource(rcf)


def test_handler_receives_the_connection_when_pass_conn_is_true():
    seen = []
    conns = []

    def get_conn():
        c = object()
        conns.append(c)
        return c

    counter = itertools.count()
    rcf.run_reconnecting_changefeed(
        open_feed=lambda c: iter([{"old_val": None, "new_val": {"id": 1}}]),
        handle_change=lambda change, conn: seen.append((change, conn)),
        label="t", get_conn=get_conn, sleep=lambda d: None,
        should_continue=lambda: next(counter) < 1)
    assert seen[0][0] == {"old_val": None, "new_val": {"id": 1}}
    assert seen[0][1] is conns[0]


def test_pass_conn_false_calls_the_handler_with_one_argument():
    seen = []
    counter = itertools.count()
    rcf.run_reconnecting_changefeed(
        open_feed=lambda c: iter([{"old_val": None, "new_val": {"id": 2}}]),
        handle_change=lambda change: seen.append(change),
        label="t", get_conn=lambda: None, pass_conn=False,
        sleep=lambda d: None, should_continue=lambda: next(counter) < 1)
    assert seen == [{"old_val": None, "new_val": {"id": 2}}]


def test_backoff_grows_and_resets_only_after_a_delivered_change():
    delays = []
    state = {"round": 0}

    def open_feed(conn):
        state["round"] += 1
        if state["round"] <= 2:
            raise _Boom("connection is broken")
        return iter([{"old_val": None, "new_val": {"id": 3}}])

    counter = itertools.count()
    rcf.run_reconnecting_changefeed(
        open_feed=open_feed, handle_change=lambda c, x: None, label="t",
        get_conn=lambda: None, sleep=delays.append,
        should_continue=lambda: next(counter) < 3)
    assert delays[0] == 2.0 and delays[1] == 3.0     # 2.0 then 2.0*1.5
    assert delays[2] == 2.0                          # reset after delivery


def test_a_closable_connection_is_closed_every_round():
    closed = []

    class Conn:
        def close(self):
            closed.append(True)

    counter = itertools.count()
    rcf.run_reconnecting_changefeed(
        open_feed=lambda c: iter([]), handle_change=lambda c, x: None,
        label="t", get_conn=Conn, sleep=lambda d: None,
        should_continue=lambda: next(counter) < 2)
    assert len(closed) == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest backend/tests/db/test_changefeed_runner.py -v
```
Expected: FAIL — `AttributeError: module 'rethink_changefeed' has no attribute 'is_transient_db_error'`

- [ ] **Step 3: Write minimal implementation**

Replace `backend/rethink_changefeed.py` entirely:

```python
"""Reconnecting change-watcher runner.

Named for its history; it no longer speaks RethinkDB. ``open_feed(conn)`` now
returns an iterator of Change dicts -- in practice ``watch.feed(TABLE, ...)``
-- and ``get_conn`` returns ``db.pool.listen_connection()``. The signature is
unchanged so every call site ports with an import change only.
"""
from __future__ import annotations

import time as _time

# Substring hints, minus the four RethinkDB-specific ones that used to be here
# ("primary replica", "not available", "reql", "rethinkdb").
_TRANSIENT_HINTS = (
    "connection is closed",
    "connection is broken",
    "server closed the connection",
    "broken pipe",
    "bad file descriptor",
    "could not connect",
    "terminating connection",
    "lost contact",
    "timed out",
    "timeout",
    "the connection is lost",
    "ssl connection has been closed",
)


def is_transient_db_error(exc: BaseException) -> bool:
    """True when ``exc`` looks like a transient connection or availability
    loss that should be retried by reconnecting."""
    try:
        import psycopg
        if isinstance(exc, psycopg.OperationalError):
            return True
    except Exception:
        pass
    try:
        from psycopg_pool import PoolTimeout
        if isinstance(exc, PoolTimeout):
            return True
    except Exception:
        pass
    try:
        from db.errors import UnavailableError
        if isinstance(exc, UnavailableError):
            return True
    except Exception:
        pass
    if isinstance(exc, (OSError, ConnectionError)):
        return True
    msg = str(exc).lower()
    return any(hint in msg for hint in _TRANSIENT_HINTS)


# Kept for one release so no call site changes name and body in one commit.
is_transient_rethinkdb_error = is_transient_db_error


def run_reconnecting_changefeed(
    open_feed,
    handle_change,
    label,
    *,
    get_conn,
    log=None,
    pass_conn=True,
    initial_delay=2.0,
    max_delay=30.0,
    sleep=_time.sleep,
    should_continue=None,
):
    """Run a change watcher forever, reconnecting on any transient loss with
    capped exponential backoff.

    Parameters
    ----------
    open_feed : callable(conn) -> iterator of Change dicts
        e.g. ``lambda c: watch.feed("EngineControl", include_initial=True)``.
        Each item is ``{"old_val": doc|None, "new_val": doc|None}``.
    handle_change : callable(change, conn) or callable(change)
        Called with the connection when ``pass_conn`` is True (the default).
        Handlers swallow their own errors; anything that escapes is treated
        like a feed error and triggers a reconnect. The feed never dies.
    label : str
        Human name for log lines (e.g. "NexusControl").
    get_conn : callable() -> connection
        ``db.pool.listen_connection`` in production. Handlers that used the
        connection to issue queries now call ``db.store`` directly and ignore
        it; the parameter stays so no call site's arity changes.
    initial_delay, max_delay : float
        Backoff bounds. Backoff resets once the feed DELIVERS a change (proof
        it is genuinely live), not merely on a successful connect.
    sleep, should_continue : callables
        Test seams.
    """
    def _log(msg, color):
        if log is None:
            return
        try:
            log(msg, color, service="Postgres")
        except Exception:
            pass

    _cont = should_continue or (lambda: True)
    delay = initial_delay
    while _cont():
        c = None
        try:
            c = get_conn()
            healthy = False
            for change in open_feed(c):
                if not healthy:
                    healthy = True
                    delay = initial_delay
                if pass_conn:
                    handle_change(change, c)
                else:
                    handle_change(change)
            _log("%s feed ended unexpectedly; reconnecting..." % label, "yellow")
        except Exception as e:  # noqa: BLE001 -- deliberate: keep the feed alive
            if is_transient_db_error(e):
                _log("%s feed connection lost (%s); reconnecting in %.0fs..."
                     % (label, e, delay), "yellow")
            else:
                _log("%s feed error (%s); reconnecting in %.0fs..."
                     % (label, e, delay), "red")
        finally:
            if c is not None:
                try:
                    c.close()
                except Exception:
                    pass
        sleep(delay)
        delay = min(delay * 1.5, max_delay)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest backend/tests/db/test_changefeed_runner.py -v
python3 -m pytest backend/tests/test_changefeed_selfheal.py -v
```
Expected: 11 PASS in the new file. `test_changefeed_selfheal.py` must pass **unchanged** — it drives the runner through the `sleep`/`should_continue` seams, not through a driver. If it fails, the runner's loop shape changed and that is a bug in this task, not in the test.

- [ ] **Step 5: Commit**

```bash
git add backend/rethink_changefeed.py backend/tests/db/test_changefeed_runner.py
git commit -m "$(cat <<'EOF'
refactor(db): run_reconnecting_changefeed on watch.feed, no rethinkdb import

Signature unchanged so its ~6 call sites port with an import change only;
open_feed now returns an iterator of Change dicts and get_conn returns
pool.listen_connection(). is_transient_db_error reclassifies against
psycopg.OperationalError / PoolTimeout / OSError plus the substring hints
minus the four RethinkDB-specific ones; the old name stays as an alias for
one release. test_changefeed_selfheal.py passes unchanged.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 13: `FakeStore` and the shared `conftest.py` fixture (GATE)

**Files:**
- Create: `backend/db/fake.py`
- Modify: `backend/tests/conftest.py`
- Create: `backend/tests/db/test_fake_parity.py`

**Interfaces:**
- Consumes: `db.merge.deep_merge` (shared code, **not** a re-implementation), `db.store.InsertResult/WriteResult/Selection/Predicate/pluck/coerce_id`.
- Produces:
  ```python
  class FakeStore:      # the same public API as db.store, over Python dicts
      def __init__(self) -> None
      def clear(self) -> None
      # get, get_all, insert, update, replace, replace_if, delete, between,
      # filter, pluck, order_by, limit, slice, count, run, iter, table_list,
      # table_create, index_list, sql (raises NotImplementedError), asc, desc
  # backend/tests/conftest.py
  @pytest.fixture
  def store(request): ...    # real Postgres when PG_TEST_DSN is set, else FakeStore
  ```

**Why it exists:** so the ~475 existing tests keep running on a laptop with no database, and so the ~30 test files that stub RethinkDB with `monkeypatch.setattr(iu, "r", fake_r)` change **once**, mechanically, rather than twice. `FakeStore` is not a validation target — it never proves collation, `jsonb_deep_merge`, or `LISTEN/NOTIFY`.

**This task is a gate:** the 475 existing tests must still pass with the fixture in place and RethinkDB still in the tree.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_fake_parity.py`:

```python
"""FakeStore must answer the same way the real store does.

Every test here runs twice when PG_TEST_DSN is set: once against FakeStore and
once against the real store. Divergence here is a bug in FakeStore, never a
reason to weaken the real store.
"""
import pytest

from db import schema
from db.errors import StoreError
from db.fake import FakeStore
from db.merge import Literal
from db.store import P

from .conftest import PG_TEST_DSN


@pytest.fixture(params=["fake", "real"])
def s(request, pg_schema_or_skip):
    if request.param == "fake":
        return FakeStore()
    if not PG_TEST_DSN:
        pytest.skip("PG_TEST_DSN not set")
    from db import store as real
    schema.ensure_schema(tables=["Instances", "DiscordOutbox"])
    return real


@pytest.fixture
def pg_schema_or_skip(request):
    """Only the 'real' parametrisation needs a database schema."""
    if request.node.callspec.params.get("s") == "real":
        yield request.getfixturevalue("pg_schema")
    else:
        yield None


def test_get_missing_is_none(s):
    assert s.get("Instances", 12345) is None


def test_insert_and_get(s):
    s.insert("Instances", {"id": 1, "name": "main"})
    assert s.get("Instances", 1) == {"id": 1, "name": "main"}


def test_int_ids_coerce_both_ways(s):
    s.insert("Instances", {"id": 1})
    assert s.get("Instances", "1") == s.get("Instances", 1)


def test_update_deep_merges(s):
    s.insert("Instances", {"id": 1, "cfg": {"a": 1, "b": 2}})
    s.update("Instances", 1, {"cfg": {"b": 9}})
    assert s.get("Instances", 1)["cfg"] == {"a": 1, "b": 9}


def test_update_literal_blanks(s):
    s.insert("Instances", {"id": 1, "cfg": {"a": 1}})
    s.update("Instances", 1, {"cfg": Literal({})})
    assert s.get("Instances", 1)["cfg"] == {}


def test_get_all_does_not_dedupe(s):
    for rid in ("a", "b"):
        s.insert("DiscordOutbox", {"id": rid})
    assert [r["id"] for r in s.get_all("DiscordOutbox", "a", "a", "b")] == \
        ["a", "a", "b"]


def test_conflict_error_counts_without_aborting(s):
    s.insert("DiscordOutbox", {"id": "a", "n": 1})
    res = s.insert("DiscordOutbox", [{"id": "a", "n": 2}, {"id": "b"}])
    assert (res.inserted, res.errors) == (1, 1)
    assert s.get("DiscordOutbox", "a")["n"] == 1


def test_conflict_update_deep_merges(s):
    s.insert("DiscordOutbox", {"id": "a", "cfg": {"x": 1, "y": 2}})
    s.insert("DiscordOutbox", {"id": "a", "cfg": {"y": 9}}, conflict="update")
    assert s.get("DiscordOutbox", "a")["cfg"] == {"x": 1, "y": 9}


def test_ordering_is_bytewise(s):
    for rid in ("alpaca-main|z", "Alpaca-Main|a", "alpaca_main|a"):
        s.insert("DiscordOutbox", {"id": rid})
    got = [r["id"] for r in s.run(s.order_by(s.filter("DiscordOutbox", {}),
                                             fields=(s.asc("id"),)))]
    assert got == sorted(got, key=lambda v: v.encode("utf-8"))


def test_between_is_half_open(s):
    for rid in ("a", "b", "c"):
        s.insert("DiscordOutbox", {"id": rid})
    assert sorted(r["id"] for r in s.run(s.between("DiscordOutbox", "a", "c"))) == \
        ["a", "b"]


def test_pluck_omits_absent_keys(s):
    assert s.pluck([{"id": 1}], "id", "status") == [{"id": 1}]


def test_delete_over_a_selection(s):
    for rid, kind in (("a", "x"), ("b", "x"), ("c", "y")):
        s.insert("DiscordOutbox", {"id": rid, "kind": kind})
    assert s.delete("DiscordOutbox", s.filter("DiscordOutbox", {"kind": "x"})).deleted == 2
    assert s.count("DiscordOutbox") == 1


def test_predicate_default_and_ne(s):
    s.insert("DiscordOutbox", {"id": "a", "origin": "backtest"})
    s.insert("DiscordOutbox", {"id": "b", "origin": "live"})
    s.insert("DiscordOutbox", {"id": "c"})
    pred = P.field("origin").default("").ne("backtest")
    assert {r["id"] for r in s.run(s.filter("DiscordOutbox", pred))} == {"b", "c"}


def test_starts_with_prefix(s):
    for rid in ("main|h|x", "mainly", "other"):
        s.insert("DiscordOutbox", {"id": rid})
    got = s.run(s.filter("DiscordOutbox", P.field("id").starts_with("main|")))
    assert [r["id"] for r in got] == ["main|h|x"]


def test_missing_pk_field_raises(s):
    with pytest.raises(StoreError):
        s.insert("kalshi_markets", {"yes_bid": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_fake_parity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'db.fake'`

- [ ] **Step 3: Write minimal implementation**

`backend/db/fake.py`:

```python
"""An in-process store over Python dicts, API-identical to db.store.

It exists so the ~475 existing tests keep running on a laptop with no
database. It is NOT a validation target: only real Postgres can prove
collation, jsonb_deep_merge, and LISTEN/NOTIFY, and every test that proves
those skips without PG_TEST_DSN.

The merge is db.merge.deep_merge -- shared code, not a re-implementation --
so FakeStore cannot drift from the SQL twin on the one semantic that has 149
call sites.
"""
from __future__ import annotations

import re
from typing import Any, Iterator, Optional, Sequence

from . import json as dbjson
from . import schema as dbschema
from . import store as _s
from .errors import StoreError
from .merge import deep_merge


class FakeStore:
    def __init__(self) -> None:
        self._tables: dict = {}

    # -- helpers ----------------------------------------------------------
    def clear(self) -> None:
        self._tables.clear()

    def _rows(self, table: str) -> dict:
        return self._tables.setdefault(table, {})

    def _match(self, table: str, row: dict, sel) -> bool:
        for frag, params in getattr(sel, "terms", ()):
            if not _eval_fragment(frag, params, row, table):
                return False
        return True

    def _select(self, sel) -> list:
        sel = sel if isinstance(sel, _s.Selection) else _s.Selection(str(sel))
        rows = [r for r in self._rows(sel.table).values()
                if self._match(sel.table, r, sel)]
        for order in reversed(sel.orders):
            rows.sort(key=lambda r, o=order: _sort_key(r, o), reverse=order.desc)
        if sel.offset_n:
            rows = rows[sel.offset_n:]
        if sel.limit_n is not None:
            rows = rows[:sel.limit_n]
        return [dict(r) for r in rows]

    # -- reads ------------------------------------------------------------
    def get(self, table: str, row_id: Any):
        row = self._rows(table).get(_s.coerce_id(table, row_id))
        return dict(row) if row is not None else None

    def get_all(self, table: str, *keys: Any, index: Optional[str] = None) -> list:
        out = []
        for key in keys:                          # no dedupe, matching ReQL
            if index in (None, "id"):
                row = self._rows(table).get(_s.coerce_id(table, key))
                if row is not None:
                    out.append(dict(row))
            else:
                for row in self._rows(table).values():
                    if str(row.get(index)) == str(key):
                        out.append(dict(row))
        return out

    def run(self, selection) -> list:
        rows = self._select(selection)
        sel = selection if isinstance(selection, _s.Selection) else None
        if (sel is None or sel.limit_n is None) and len(rows) > _s.PG_MAX_ROWS:
            raise StoreError("above PG_MAX_ROWS; use store.iter()")
        return rows

    def iter(self, selection, *, batch: int = 1000) -> Iterator:
        return builtins_iter(self._select(selection))

    def count(self, table_or_selection) -> int:
        return len(self._select(table_or_selection))

    def pluck(self, rows_or_selection, *fields):
        rows = (self._select(rows_or_selection)
                if isinstance(rows_or_selection, _s.Selection) else rows_or_selection)
        return _s.pluck(rows, *fields)

    def sql(self, query: str, params: Sequence = ()) -> list:
        raise NotImplementedError(
            "FakeStore has no SQL engine; this test needs PG_TEST_DSN")

    def table_list(self) -> list:
        return sorted(self._tables)

    def table_create(self, name: str, *, primary_key: str = "id") -> bool:
        if name in self._tables:
            return False
        self._tables[name] = {}
        return True

    def index_list(self, table: str) -> list:
        spec_ = dbschema.spec(table)
        return sorted(set(spec_.indexed_fields) | set(spec_.compound_indexes))

    # -- query builders (delegate to the real ones: same Selection type) ---
    filter = staticmethod(_s.filter)
    between = staticmethod(_s.between)
    order_by = staticmethod(_s.order_by)
    limit = staticmethod(_s.limit)
    slice = staticmethod(_s.slice)
    asc = staticmethod(_s.asc)
    desc = staticmethod(_s.desc)
    Selection = _s.Selection
    P = _s.P

    # -- writes -----------------------------------------------------------
    def insert(self, table: str, doc_or_docs, *, conflict: str = "error",
               durability: str = "hard") -> _s.InsertResult:
        docs = [doc_or_docs] if isinstance(doc_or_docs, dict) else list(doc_or_docs)
        rows = self._rows(table)
        inserted = replaced = unchanged = errors = 0
        first_error = None
        for doc in docs:
            dbjson.dumps(doc)                     # NaN rejection parity
            rid = _s._row_id_for(table, doc)
            if rid not in rows:
                rows[rid] = dict(doc)
                inserted += 1
            elif conflict == "replace":
                if rows[rid] == doc:
                    unchanged += 1
                else:
                    rows[rid] = dict(doc)
                    replaced += 1
            elif conflict == "update":
                rows[rid] = deep_merge(rows[rid], doc)
                replaced += 1
            else:
                errors += 1
                first_error = first_error or _s._DUP_ERROR
        return _s.InsertResult(inserted=inserted, replaced=replaced,
                               unchanged=unchanged, errors=errors,
                               first_error=first_error)

    def update(self, table: str, selector, patch) -> _s.WriteResult:
        rows = self._rows(table)
        if isinstance(selector, _s.Selection):
            targets = [r for r in list(rows.values())
                       if self._match(table, r, selector)]
        else:
            row = rows.get(_s.coerce_id(table, selector))
            targets = [row] if row is not None else []
            if not targets:
                return _s.WriteResult(skipped=1)
        for row in targets:
            rid = _s._row_id_for(table, row)
            rows[rid] = deep_merge(row, patch)
        return _s.WriteResult(replaced=len(targets))

    def replace(self, table: str, row_id, doc) -> _s.WriteResult:
        rid = _s.coerce_id(table, row_id)
        if rid not in self._rows(table):
            return _s.WriteResult(skipped=1)
        self._rows(table)[rid] = dict(doc)
        return _s.WriteResult(replaced=1)

    def replace_if(self, table: str, row_id, *, when, doc,
                   insert_if_absent: bool = False):
        rid = _s.coerce_id(table, row_id)
        rows = self._rows(table)
        if rid not in rows:
            if not insert_if_absent:
                raise StoreError("%s: row %r does not exist" % (table, row_id))
            rows[rid] = dict(doc)
            return doc
        if when is not None:
            frag, params = when.to_sql()
            if not _eval_fragment(frag, params, rows[rid], table):
                return None
        rows[rid] = dict(doc)
        return doc

    def delete(self, table: str, selector) -> _s.WriteResult:
        rows = self._rows(table)
        if isinstance(selector, _s.Selection):
            doomed = [k for k, r in rows.items() if self._match(table, r, selector)]
        else:
            rid = _s.coerce_id(table, selector)
            doomed = [rid] if rid in rows else []
        for key in doomed:
            del rows[key]
        return _s.WriteResult(deleted=len(doomed))


def builtins_iter(seq):
    for item in seq:
        yield item


def _sort_key(row, order):
    value = row.get(order.field)
    if order.numeric:
        try:
            return float(value)
        except (TypeError, ValueError):
            return float("-inf")
    return ("" if value is None else str(value)).encode("utf-8")


# -- a tiny interpreter for the SQL fragments the Predicate DSL emits ------
#
# The DSL emits a bounded grammar (see db/store.py FieldRef): this evaluates
# the same shapes over a Python dict so FakeStore agrees with Postgres on
# every predicate the repo actually writes. Anything outside the grammar
# raises, rather than silently returning the wrong rows.
_FRAG_RE = re.compile(
    r"^(?P<expr>.+?)\s*(?P<op>=\s*ANY|IS NULL|LIKE|~|<=|>=|<>|=|<|>)\s*(?P<rhs>%s)?$")


def _eval_fragment(fragment: str, params, row: dict, table: str) -> bool:
    frag = fragment.strip()
    if frag == "true":
        return True
    if frag == "false":
        return False
    if frag.startswith("COALESCE(NOT ("):
        inner = frag[len("COALESCE(NOT ("):frag.rindex("), false)")]
        try:
            return not _eval_fragment(inner, params, row, table)
        except _Undefined:
            return False
    for joiner, combine in ((") AND (", all), (") OR (", any)):
        if joiner in frag and frag.startswith("(") and frag.endswith(")"):
            left, right = _split_top(frag, joiner)
            if left is not None:
                n_left = left.count("%s")
                return combine([
                    _eval_fragment(left, params[:n_left], row, table),
                    _eval_fragment(right, params[n_left:], row, table)])
    m = _FRAG_RE.match(frag)
    if not m:
        raise StoreError("FakeStore cannot evaluate %r" % fragment)
    value, consumed = _eval_expr(m.group("expr"), params, row)
    op = m.group("op").replace(" ", "")
    if op == "ISNULL":
        return value is None
    rhs = params[consumed]
    if value is None:
        return False
    if op == "=ANY":
        return str(value) in [str(v) for v in rhs]
    if op == "LIKE":
        return _like(str(value), str(rhs))
    if op == "~":
        return re.search(str(rhs), str(value)) is not None
    left = str(value).encode("utf-8")
    right = ("" if rhs is None else str(rhs)).encode("utf-8")
    return {"=": left == right, "<>": left != right, "<": left < right,
            "<=": left <= right, ">": left > right, ">=": left >= right}[op]


class _Undefined(Exception):
    pass


def _eval_expr(expr: str, params, row: dict):
    """Returns (value, n_params_consumed) for one FieldRef expression."""
    expr = expr.strip()
    m = re.match(r"^doc->>'([^']+)'$", expr)
    if m:
        value = row.get(m.group(1))
        return (None if value is None else
                (value if isinstance(value, str) else _scalar_str(value))), 0
    m = re.match(r"^coalesce\((.+), %s\)$", expr)
    if m:
        inner, used = _eval_expr(m.group(1), params, row)
        return (inner if inner is not None else params[used]), used + 1
    m = re.match(r"^lower\((.+)\)$", expr)
    if m:
        inner, used = _eval_expr(m.group(1), params, row)
        return (None if inner is None else inner.lower()), used
    m = re.match(r"^split_part\((.+), %s, %s\)$", expr)
    if m:
        inner, used = _eval_expr(m.group(1), params, row)
        sep, idx = params[used], params[used + 1]
        parts = ("" if inner is None else inner).split(sep)
        return (parts[idx - 1] if 0 < idx <= len(parts) else ""), used + 2
    m = re.match(r"^id$", expr)
    if m:
        return row.get("id"), 0
    m = re.match(r'^"([^"]+)"$', expr)            # a generated column
    if m:
        value = row.get(m.group(1))
        return (None if value is None else _scalar_str(value)), 0
    raise StoreError("FakeStore cannot evaluate expression %r" % expr)


def _scalar_str(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _like(value: str, pattern: str) -> bool:
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
        i += 1
    return re.match("^" + "".join(out) + "$", value, re.S) is not None


def _split_top(frag: str, joiner: str):
    depth = 0
    for i in range(len(frag)):
        if frag[i] == "(":
            depth += 1
        elif frag[i] == ")":
            depth -= 1
            if depth == 1 and frag[i:i + len(joiner)] == joiner:
                return frag[1:i], frag[i + len(joiner) - 1:-1]
    return None, None
```

Then append the shared fixture to `backend/tests/conftest.py` (keep the existing `sys.path` block above it untouched):

```python
import pytest


@pytest.fixture
def store(request):
    """The store under test: real Postgres when PG_TEST_DSN is set, else a
    FakeStore over Python dicts.

    Replaces the ad-hoc ``monkeypatch.setattr(iu, "r", fake_r)`` pattern in
    the ~30 test files that stub RethinkDB today. Each test gets fresh state:
    real PG gets a per-test schema dropped on teardown, fake gets a fresh
    instance.
    """
    dsn = os.environ.get("PG_TEST_DSN")
    if not dsn:
        from db.fake import FakeStore
        yield FakeStore()
        return
    import uuid
    from db import pool as dbpool
    from db import schema as dbschema
    from db import store as real_store
    name = "t_" + uuid.uuid4().hex[:16]
    dbpool.close_pool()
    os.environ["PG_DSN"] = dsn
    os.environ["PG_SEARCH_PATH"] = name
    with dbpool.connection(autocommit=True) as conn:
        conn.execute('CREATE SCHEMA IF NOT EXISTS "%s"' % name)
    dbpool.close_pool()
    dbschema.ensure_schema()
    try:
        yield real_store
    finally:
        os.environ.pop("PG_SEARCH_PATH", None)
        dbpool.close_pool()
        with dbpool.connection(autocommit=True) as conn:
            conn.execute('DROP SCHEMA IF EXISTS "%s" CASCADE' % name)
        dbpool.close_pool()
```

- [ ] **Step 4: Run tests to verify they pass — and run the whole suite (GATE)**

```bash
python3 -m pytest backend/tests/db/test_fake_parity.py -v
python3 -m pytest backend/tests/db -v
env -u PG_TEST_DSN python3 -m pytest backend/tests -q     # the 475-test gate
```
Expected: parity file PASS (15 tests × 2 parametrisations); the whole `backend/tests` suite passes with the same pass/fail counts as before this branch. Record the before/after counts in the commit body. Nothing in `backend/tests` is ported to `store` yet — this task only proves the fixture is inert until a test asks for it.

- [ ] **Step 5: Commit**

```bash
git add backend/db/fake.py backend/tests/conftest.py backend/tests/db/test_fake_parity.py
git commit -m "$(cat <<'EOF'
feat(db): FakeStore + shared store fixture; 475 existing tests still green

FakeStore shares db.merge.deep_merge rather than re-implementing it, so it
cannot drift from the SQL twin on the one semantic with 149 call sites. It
answers the same way on no-dedupe get_all, pluck omission, bytewise ordering,
half-open between, and the conflict= result shapes -- proved by a parametrised
parity suite that runs every assertion against both stores. The conftest
fixture is inert until a test asks for it, so this commit changes no existing
test's behaviour.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Task 14: The §4 semantics acceptance matrix and the package exports

**Files:**
- Modify: `backend/db/__init__.py` (re-export `store`, `Literal`, `P`, `watch`)
- Create: `backend/tests/db/test_store_semantics.py`

**Interfaces:**
- Consumes: everything built in Tasks 1–13.
- Produces: `from db import store, watch, Literal, P` works; `db.SEMANTICS_ROWS` is not a thing — the coverage map lives in the test file.

**Why this task exists:** spec §4 says *"This table is the acceptance criteria for `backend/tests/db/test_store_semantics.py`; each row gets at least one test."* Tasks 8–10 covered most rows inside the module they belong to. This task adds the rows that had no natural home, and adds one explicit coverage map so a reviewer can check the table off row by row instead of trusting a claim.

- [ ] **Step 1: Write the failing test**

`backend/tests/db/test_store_semantics.py`:

```python
"""Acceptance matrix for spec section 4: ReQL -> store.

COVERAGE maps every row of the spec's mapping table to the test that proves
it. A reviewer checks the table off against this dict. Rows proved elsewhere
name the file; rows proved here name a test in this file.
"""
import datetime as dt

import pytest

from db import Literal, P, store
from db import schema
from db.errors import StoreError

from .conftest import requires_pg

COVERAGE = {
    ".get(pk)": "test_store_reads.py::test_get_missing_row_is_none_never_empty_dict",
    ".get_all(index)": "test_store_reads.py::test_get_all_on_a_secondary_index_field",
    ".get_all no dedupe": "test_store_reads.py::test_get_all_does_not_dedupe",
    ".get_all variadic empty": "test_store_reads.py::test_get_all_with_no_keys_is_a_valid_empty_result",
    ".insert(doc)": "test_store_writes.py::test_insert_then_get",
    "conflict='error'": "test_store_conflict.py::test_conflict_error_records_the_conflict_without_aborting_the_batch",
    "conflict='replace'": "test_store_conflict.py::test_conflict_replace_drops_keys_the_new_document_lacks",
    "conflict='update'": "test_store_conflict.py::test_conflict_update_deep_merges_it_does_not_use_shallow_concat",
    "multi-doc insert / WRITE_CHUNK": "test_store_conflict.py::test_a_500_doc_chunk_with_two_duplicates_is_partial_success",
    ".update(patch)": "test_store_writes.py::test_update_deep_merges_objects_and_replaces_arrays",
    "r.literal(v)": "test_store_writes.py::test_update_with_literal_blanks_a_subtree",
    ".replace(doc)": "test_store_writes.py::test_replace_swaps_the_whole_document",
    ".replace(r.branch(...)) CAS": "test_store_writes.py::test_replace_if_distinguishes_missing_row_from_failed_predicate",
    ".delete()": "test_store_writes.py::test_delete_over_a_selection_is_one_statement",
    ".between(lo,hi)": "test_store_query.py::test_between_is_half_open_by_default",
    "r.minval / r.maxval": "test_store_query.py::test_minval_and_maxval_omit_the_bound",
    "prefix between + LIKE": "test_prefix_scan.py::test_like_form_and_range_form_agree",
    ".filter({dict})": "test_store_query.py::test_filter_dict_none_means_json_null_not_absent",
    ".filter(lambda)": "test_store_query.py::test_undefaulted_field_comparison_is_false_on_a_missing_key",
    "origin_not_backtest": "test_store_query.py::test_origin_not_backtest_criterion",
    ".default().match('^prefix')": "test_prefix_scan.py::test_a_prefix_containing_like_metacharacters_is_escaped",
    ".default().coerce_to('string')": "test_store_query.py::test_coerce_to_string_stringifies_a_json_number",
    ".split('|').nth(0)": "test_store_query.py::test_split_nth_selects_the_base_instance",
    ".pluck(*fields)": "test_store_reads.py::test_pluck_omits_missing_keys_it_never_emits_null",
    ".merge(lambda)": "test_merge_is_not_implemented_in_the_store",
    ".order_by(index)/r.desc": "test_collation.py::test_the_graph_nexus_window_tiebreak_reproduces_python_byte_order",
    ".limit/.slice": "test_store_reads.py::test_limit_and_slice",
    ".count()": "test_store_reads.py::test_count_of_a_table",
    "r.expr(list).contains": "test_store_query.py::test_is_in_matches_any_of_a_list",
    "r.branch(c,a,b)": "test_status_norm_index_expression_is_a_case_expression",
    "r.now()": "test_now_is_server_side_and_transaction_start",
    "r.now().to_epoch_time()": "test_now_epoch_returns_a_float",
    "r.epoch_time(x)": "test_epoch_time_builds_a_timestamp",
    "TIME pseudotype": "test_store_reads.py::test_time_fields_decode_back_to_aware_datetimes",
    "NaN / Infinity": "test_store_writes.py::test_nan_is_rejected_at_the_client",
    "int primary keys": "test_store_reads.py::test_coerce_id_round_trips_int_tables",
    "instance_id NUMBER/STRING": "test_instance_id_type_split_is_left_alone_in_doc",
    "durability=": "test_store_writes.py::test_durability_is_accepted_and_ignored",
    "noreply_wait / conn.close": "test_noreply_and_close_are_not_part_of_the_api",
    "unimplemented ReQL ops": "test_unimplemented_reql_ops_are_absent",
    "table_create / index_wait": "test_store_reads.py::test_table_list_and_table_create",
    "ReQL 100k array limit": "test_store_reads.py::test_run_raises_above_pg_max_rows",
}


def test_every_spec_row_names_a_test():
    assert len(COVERAGE) == 42
    for row, target in COVERAGE.items():
        assert target and "::" in target or not target.endswith(".py"), row


def test_merge_is_not_implemented_in_the_store():
    """The only .merge() site is interactive_utils.py:5232's _slim ticker
    projection, which the BacktestResults list SQL replaces (Plan B)."""
    assert not hasattr(store, "merge")


def test_noreply_and_close_are_not_part_of_the_api():
    """noreply_wait=False and conn.close(...) become no-ops / pool release:
    there is nothing to call."""
    for name in ("noreply_wait", "close", "connect"):
        assert not hasattr(store, name)


def test_unimplemented_reql_ops_are_absent():
    """r.args, r.uuid, r.js, r.do, return_changes, eq_join, group, reduce all
    have ZERO sites. If a port needs one, that is a signal the site is being
    rewritten rather than ported."""
    for name in ("args", "uuid", "js", "do", "eq_join", "group", "reduce",
                 "for_each", "union", "has_fields"):
        assert not hasattr(store, name), name


@requires_pg
def test_now_is_server_side_and_transaction_start(pg_schema):
    """r.now() -> now() in SQL. now() is transaction START time, not statement
    time; every one of the 34 sites writes in its own short transaction, so
    the distinction is invisible and the store never batches two into one."""
    rows = store.sql("SELECT now() AS a, to_jsonb(now()) AS b")
    assert isinstance(rows[0]["a"], dt.datetime)
    assert rows[0]["a"].tzinfo is not None


@requires_pg
def test_now_epoch_returns_a_float(pg_schema):
    value = store.sql("SELECT extract(epoch from now()) AS t")[0]["t"]
    assert float(value) > 1_700_000_000


@requires_pg
def test_epoch_time_builds_a_timestamp(pg_schema):
    value = store.sql("SELECT to_timestamp(%s) AS t", (1_755_000_000,))[0]["t"]
    assert value.year == 2025 or value.year == 2026


@requires_pg
def test_status_norm_index_expression_is_a_case_expression(pg_schema):
    """r.branch(c, a, b) -> CASE WHEN. status_norm lives on BacktestProgress
    (spec section 5.3 decision 6), not on BacktestResults."""
    schema.ensure_schema(tables=["BacktestProgress", "BacktestResults"])
    rows = store.sql("SELECT indexdef FROM pg_indexes WHERE tablename = %s",
                     ("BacktestProgress",))
    defs = " ".join(r["indexdef"] for r in rows)
    assert "CASE" in defs.upper() and "paused" in defs
    br = store.sql("SELECT indexname FROM pg_indexes WHERE tablename = %s",
                   ("BacktestResults",))
    assert not any("status_norm" in r["indexname"] for r in br)


@requires_pg
def test_instance_id_type_split_is_left_alone_in_doc(pg_schema):
    """592 rows carry instance_id as a NUMBER and 833 as a STRING. The doc is
    left as-is; the generated column coalesces for indexing, exactly as the
    ReQL index does. Rewriting the values would change 592 documents' bytes
    and break every fingerprint taken before cutover."""
    schema.ensure_schema(tables=["BacktestResults"])
    store.insert("BacktestResults", [{"id": 1, "instance_id": 5},
                                     {"id": 2, "instance_id": "5"}])
    assert store.get("BacktestResults", 1)["instance_id"] == 5
    assert store.get("BacktestResults", 2)["instance_id"] == "5"
    both = store.sql(
        'SELECT id FROM "BacktestResults" '
        "WHERE coalesce(doc->>'instance_id', doc->>'instance','') = %s ORDER BY id",
        ("5",))
    assert [r["id"] for r in both] == ["1", "2"]


def test_package_exports_the_public_surface():
    from db import Literal as L, P as Pp, store as s, watch as w
    assert L is Literal and Pp is P and s is store and w is not None


@requires_pg
def test_a_missing_pk_field_write_is_rejected_like_rethinkdb(pg_schema):
    schema.ensure_schema(tables=["kalshi_markets"])
    with pytest.raises(StoreError):
        store.insert("kalshi_markets", {"yes_bid": 1})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest backend/tests/db/test_store_semantics.py -v`
Expected: FAIL — `ImportError: cannot import name 'Literal' from 'db'`

- [ ] **Step 3: Write minimal implementation**

Replace `backend/db/__init__.py`:

```python
"""Postgres store package. Nothing outside this package opens a connection.

Import surface for the whole repo::

    from db import store, watch, Literal, P
    from db.errors import StoreError, ConflictError, UnavailableError, CasFailed
"""
from __future__ import annotations

from . import json, merge, pool, schema, store, watch
from .errors import CasFailed, ConflictError, StoreError, UnavailableError
from .merge import Literal
from .store import P

__all__ = [
    "store", "watch", "schema", "pool", "merge", "json",
    "Literal", "P",
    "StoreError", "ConflictError", "UnavailableError", "CasFailed",
]
```

> **Import-order note:** `watch` imports `store`, `store` imports `schema`, `schema` imports `pool`, `pool` imports `json`. The `from . import ...` line lists them in dependency order, so the package import cannot deadlock on a partially initialised module. `merge` must still not import `pool` — keep it pure.

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest backend/tests/db/test_store_semantics.py -v
python3 -m pytest backend/tests/db -q
```
Expected: 11 PASS in this file; the whole `backend/tests/db` suite green.

- [ ] **Step 5: Commit**

```bash
git add backend/db/__init__.py backend/tests/db/test_store_semantics.py
git commit -m "$(cat <<'EOF'
test(db): spec section 4 acceptance matrix + package exports

COVERAGE maps all 42 rows of the ReQL->store mapping table to the test that
proves each one, so a reviewer checks the table off rather than trusting a
claim. Adds the rows that had no natural home: r.now/epoch, the status_norm
CASE index (on BacktestProgress, not BacktestResults), the NUMBER/STRING
instance_id split left verbatim in doc, and the absence of every ReQL op with
zero sites.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Final verification

Run all of it, both tiers, and record the numbers:

```bash
export PG_TEST_DSN="$(./scripts/dev_pg.sh dsn)"
python3 -m pytest backend/tests/db -v                 # the new suite, real PG
env -u PG_TEST_DSN python3 -m pytest backend/tests/db -q   # skip tier
env -u PG_TEST_DSN python3 -m pytest backend/tests -q      # the 475-test gate
grep -rn "rethinkdb" backend/db/                      # must return nothing
```

Plan A is done when: the `backend/tests/db` suite is green against a real Postgres, it skips cleanly without `PG_TEST_DSN`, `backend/tests` has the same pass count as before the branch, and `backend/db/` contains no reference to `rethinkdb`.

**Not in Plan A, deliberately:** removing `rethinkdb` from `requirements.txt`, the docker-compose `postgres` service, the migration script, and the call-site ports. Those land with the groups in spec §10.2.

---

## Ambiguities resolved while writing this plan

| # | gap in the spec | resolution | reversible? |
|---|---|---|---|
| 1 | The task brief says "registry of ALL 125 tables"; spec §2.6 says `TABLES` holds an entry only for the ~40 tables needing more than the default. | Both: `ALL_TABLES` is the live 125-name tuple (what `ensure_schema()` iterates), `TABLES` is the ~40-entry `TableSpec` registry. A new table needs no registry edit. | yes |
| 2 | `TableSpec` in the spec has no field for expression-defined generated columns, but `tickers_total` (`jsonb_array_length`), the cache tables' `created_at` (`::timestamptz`), and `PriceHistory.ticker/ts` all need one. | Added `generated: Mapping[str, (sql_type, expression)]`. | yes |
| 3 | Spec §3.2 describes a `_pfx` `text_pattern_ops` index "for a column that also serves prefix scans" but no field says which. | Added `prefix_fields: tuple[str, ...]`, populated from `clear_instance_state.py`'s 18 targets. | yes |
| 4 | `PriceHistory`, `BacktestSteps`, and `BacktestProgress` are not the default `(id, doc, updated_at)` shape. | Added `TableSpec.ddl: str | None` carrying the full `CREATE TABLE`, overriding the template. | yes |
| 5 | Spec §3.3 lists eight `notify=False` tables; `BacktestSteps` (new, insert-only, ~5,000 decision rows per run) is not among them because it did not exist when the list was written. | `BacktestSteps` gets `notify=False`. Nothing watches it; the hot row `BacktestProgress` is what watchers read. | yes — flip the flag |
| 6 | The predicate DSL has `.match(regex)` but the prefix-scan rule wants a `LIKE` form the `_pfx` index can serve. | Added `FieldRef.starts_with(prefix)` emitting `LIKE escape_like(prefix) || '%'`, plus `store.escape_like`. `.match` is kept for genuine regexes. | yes |
| 7 | Spec §2.7 says a site needing more than the DSL "gets its own hand-written SQL in the owning module", but every module is forbidden from opening a connection. | Added `store.sql(query, params)` as the single escape hatch. `FakeStore.sql` raises `NotImplementedError`, so any test using it must set `PG_TEST_DSN`. | yes |
| 8 | `order_by(selection, *, index=...)` needs a way to order by an expression index (`list_ts`, `instance_ts`) rather than a doc key. | Added the private `_INDEX_ORDER` map and a `_RawOrder` element that `Selection.to_sql` duck-types alongside `Order`. | yes |
| 9 | The spec's `dsn_from_env()` defaults name user `intellistock` and db `IntelliStock` but do not say what wins. | `PG_DSN` wins outright; the `POSTGRES_*` parts are assembled only when it is unset. `PG_SEARCH_PATH` is a test-only addition that scopes a run to one schema. | yes |
| 10 | Spec §8 removes `rethinkdb` from `requirements.txt`. | Deferred: Plan A **adds** `psycopg[binary,pool]` and leaves `rethinkdb` pinned, because ~1,119 call sites still import it. Removal is the last commit of the port, not the first. | n/a |
| 11 | `~Predicate` (NOT) over a row missing the key: SQL `NOT NULL` is NULL, which is falsy, but `NOT (a = b)` on a NULL `a` is also NULL. | `__invert__` wraps in `COALESCE(NOT (...), false)`, so a missing key is false under both the predicate and its negation — matching "ReQL raises, and every ported site uses `.default()`". | yes |
| 12 | `hypothesis` is not in `requirements.txt`. | Added under a test-only comment, since the property test is a mandatory gate and CI would otherwise not run it. | yes |
| 13 | `store.sql`'s signature says `Sequence`, but the `BacktestResults` list query (Plan B Task 8) needs `%(name)s` named parameters. | `sql()` accepts a `Mapping` and passes it through unchanged; anything else is coerced to a tuple. It also commits, so a write issued through the escape hatch is durable. | yes |
| 14 | Spec §3.5's `BacktestProgress` DDL types `progress` as `double precision` and `time_elapsed_seconds` as `integer`. Live documents carry both as **either** an int or a float depending on lifecycle stage, so those types would change the bytes. | **Plan B Task 2 replaces this table's DDL** with a `payload jsonb` version whose typed columns are generated over it. Plan A ships the spec's version and the `status_norm` index; the amendment is Plan B's, because Plan B is where the byte-identity gate proves it is needed. | yes |
