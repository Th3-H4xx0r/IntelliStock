# Postgres Port — Migration & Ops (Plan D) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship everything needed to *move the data and run the system* on PostgreSQL — a resumable, verifying migration script; a retention job; a triaged and ported `scripts/` tree; a compose `postgres` service; a cutover runbook; docs; and an open (not merged) PR.

**Architecture:** One streaming export/import script is the only file in the tree allowed to import `rethinkdb`, and it imports it lazily. It pages by primary key, converts RethinkDB TIME pseudotypes to ISO-8601, and `COPY`s batches into `(id, doc)` tables created by `db.schema.ensure_schema()`. `BacktestResults` is split into metadata + `BacktestSteps` + `BacktestProgress` during the copy; `PriceHistory` gets `ticker`/`ts` promoted into real partitioned columns. Verification compares row counts and canonical sha256 hashes and refuses to summarise a mismatch. The compose `postgres` service is added alongside the untouched `rethinkdb` service, so the flip is an env change and the rollback is unsetting it.

**Tech Stack:** Python 3.11 (prod image `python:3.11-slim`), `psycopg[binary,pool]>=3.2.10,<4`, PostgreSQL 17 + `pg_partman`, Docker Compose, pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-postgres-port-design.md` (§7 migration, §8 compose, §9 scripts triage)
**Companion plans:** Plan A (`backend/db/` package), Plan B (BacktestResults split), Plan C (call-site port).

## Global Constraints

- **User invariant (binding on every decision):** "Keep all functionality the same completely as using rethinkdb, just a different db."
- **Order fidelity.** `COLLATE "C"` on every text column and every `ORDER BY`. Bytewise, no exceptions.
- **Merge fidelity.** `jsonb_deep_merge`, never `||`.
- **Python 3.11 compat.** Prod image is `python:3.11-slim` (`backend/Dockerfile:2`). No 3.12+ syntax or stdlib.
- **No GIN indexes anywhere.**
- **No `rethinkdb` in the runtime.** It survives only as an optional extra for `scripts/migrate_rethinkdb_to_postgres.py`, imported lazily inside the function that needs it.
- **Never SQL `BETWEEN`.** ReQL is `[lo, hi)`.
- **Nothing in this branch runs against production.** No production backtests, no production cutover. The cold and warm A/A re-certification is the user's, run from the runbook this plan produces.
- **The `rethinkdb` compose service stays.** Deleting it is a later, separate decision.
- **Every commit message ends with:**
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
  ```
- **Before editing any symbol** run `gitnexus_impact({target, direction: "upstream"})` and report the blast radius; HIGH/CRITICAL is escalated, never overridden. **Before every commit** run `gitnexus_detect_changes()`.

---

## Interfaces — Common (every task consumes these)

From Plan A's `backend/db/` package. Copied verbatim from spec §2; do not invent methods outside this list.

```python
# backend/db/store.py — module-level singleton `store`; Doc = dict[str, Any]
def get(table: str, row_id: Any) -> Doc | None: ...
def get_all(table: str, *keys: Any, index: str | None = None) -> list[Doc]: ...
def insert(table, doc_or_docs: Doc | Sequence[Doc], *,
           conflict: Literal["error","replace","update"] = "error",
           durability: str = "hard") -> InsertResult: ...
def update(table, selector: Any | Selection, patch: Doc) -> WriteResult: ...
def delete(table, selector: Any | Selection) -> WriteResult: ...
def between(table, lo, hi, *, index: str | None = None,
            left_bound: Literal["closed","open"] = "closed",
            right_bound: Literal["open","closed"] = "open") -> Selection: ...
def filter(table, predicate: Doc | Predicate) -> Selection: ...
def order_by(selection, *, index: str | None = None,
             fields: Sequence[Order] = (), desc: bool = False) -> Selection: ...
def limit(selection, n: int) -> Selection: ...
def count(table_or_selection) -> int: ...
def run(selection) -> list[Doc]: ...        # raises StoreError above PG_MAX_ROWS (100_000)
def iter(selection, *, batch: int = 1000) -> Iterator[Doc]: ...   # unbounded path
def table_list() -> list[str]: ...
def table_create(name: str, *, primary_key: str = "id") -> bool: ...
def index_list(table: str) -> list[str]: ...
```

```python
# backend/db/schema.py
@dataclass(frozen=True)
class PartitionSpec:
    by: str; interval: str; premake: int = 3

@dataclass(frozen=True)
class RetentionSpec:
    field: str; days_env: str; default_days: int | None = None   # None => retention OFF

@dataclass(frozen=True)
class TableSpec:
    name: str
    id_type: Literal["text", "int"] = "text"
    pk_field: str = "id"
    pk: tuple[str, ...] = ("id",)
    indexed_fields: tuple[str, ...] = ()
    compound_indexes: Mapping[str, str] = field(default_factory=dict)
    time_fields: tuple[str, ...] = ()
    partitioned: PartitionSpec | None = None
    retention: RetentionSpec | None = None
    notify: bool = True

TABLES: dict[str, TableSpec]
def spec(table: str) -> TableSpec: ...
def ensure_schema(*, tables: Iterable[str] | None = None) -> list[str]: ...
def ensure_table(table: str) -> None: ...
```

```python
# backend/db/json.py
dumps: Callable[[Any], str]        # partial(json.dumps, allow_nan=False, separators=(",",":"))
loads: Callable[[str], Any]
def install() -> None: ...
def canonical(value: Any) -> str: ...            # sort_keys=True, allow_nan=False
def canonical_sha256(value: Any) -> str: ...     # THE single hashing entry point
```

```python
# backend/db/pool.py
@contextmanager
def connection(*, autocommit: bool = False) -> Iterator[Connection]: ...
@contextmanager
def cursor(*, autocommit: bool = False) -> Iterator[Cursor]: ...   # row_factory=dict_row
def listen_connection() -> Connection: ...
def dsn_from_env() -> str: ...
def reset_after_fork() -> None: ...
def close_pool() -> None: ...
def health() -> dict: ...          # {"ok": bool, "size": int, "dsn_host": str}
```

```python
# backend/db/errors.py
class StoreError(Exception): ...
class ConflictError(StoreError): ...
class UnavailableError(StoreError): ...
class CasFailed(StoreError): ...
```

```python
# backend/broker_backtest_assembly.py  (Plan B)
def assemble(backtest_id: str) -> dict | None: ...
def assemble_field(backtest_id: str, key: str) -> Any: ...
_STEP_KEYS: tuple[tuple[str, str, tuple | None], ...]
_ALWAYS_PRESENT: frozenset[str]
```

```python
# backend/tests/conftest.py  (Plan A)
@pytest.fixture
def store(request): ...          # real Postgres when PG_TEST_DSN is set, else FakeStore
```

---

## File Structure

| file | responsibility | task |
|---|---|---|
| `docs/superpowers/specs/2026-08-22-postgres-port-script-triage.md` | the ported/archived split of 78 scripts, as reviewable data | 1 |
| `scripts/archive_rethinkdb/README.md` | why the archived scripts do not run against Postgres | 1 |
| `scripts/migrate_rethinkdb_to_postgres.py` | streaming export/import, resumable per table | 2, 3 |
| `backend/tests/test_migration_script.py` | fixture-dump round trip, split correctness, resumability, verify | 2, 3 |
| `scripts/pg_retention.py` | batched ranged deletes driven by `schema.TABLES[...].retention` | 4 |
| `backend/tests/test_pg_retention.py` | never issues an unindexed DELETE; off by default | 4 |
| `docker/postgres/Dockerfile` | `postgres:17` + `postgresql-17-partman` | 5 |
| `docker-compose.yml` | the `postgres` service + `PG_*` env on every backend service | 5 |
| `backend/tests/test_docker_compose_security.py` | bind-address assertion for Postgres (existing file, extended) | 5 |
| ported `scripts/*.py` | the recurring-operation scripts, on `db.store` | 6 |
| `docs/runbooks/postgres-cutover.md` | export → import → verify → flip → re-certify → rollback | 7 |
| `CLAUDE.md`, `README.md`, `.env.example`, `.gitignore` | document the new store | 7 |
| `backend/requirements.txt`, `backend/requirements-migration.txt` | psycopg in, rethinkdb out (optional extra) | 8 |

---

### Task 1: Script triage — produce the split as reviewable data

**Files:**
- Create: `docs/superpowers/specs/2026-08-22-postgres-port-script-triage.md`
- Create: `scripts/archive_rethinkdb/README.md`
- Move: the archived scripts into `scripts/archive_rethinkdb/` (verbatim, `git mv`, no edits)
- Delete: `scripts/create_backtest_list_indices.py`, `scripts/create_clear_state_indices.py`
- Test: `backend/tests/test_script_triage.py` (create)

**Interfaces:**
- Consumes: nothing from Plan A — this task is filesystem and documentation only.
- Produces: `docs/superpowers/specs/2026-08-22-postgres-port-script-triage.md` with two tables, `PORTED` and `ARCHIVED`, each row `(path, ReQL sites, why)`. Task 6 ports exactly the `PORTED` list and nothing else.

- [ ] **Step 1: Write the failing test that pins the triage contract**

Create `backend/tests/test_script_triage.py`:
```python
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
TRIAGE = REPO / "docs/superpowers/specs/2026-08-22-postgres-port-script-triage.md"
ROW = re.compile(r"^\| `((?:backend/)?scripts/[^`]+\.py)` \|")


def _rows(section: str) -> set[str]:
    text = TRIAGE.read_text()
    body = text.split(f"## {section}", 1)[1].split("\n## ", 1)[0]
    return {m.group(1) for line in body.splitlines() if (m := ROW.match(line))}


def test_every_reql_script_is_classified_exactly_once():
    reql = set()
    for base in ("scripts", "backend/scripts"):
        for path in (REPO / base).rglob("*.py"):
            if "archive_rethinkdb" in path.parts:
                continue
            if re.search(r"rethinkdb|r\.db\(", path.read_text()):
                reql.add(str(path.relative_to(REPO)))
    ported, archived = _rows("PORTED"), _rows("ARCHIVED")
    assert ported & archived == set(), "a script is in both tables"
    # Archived scripts have already moved, so they are not in `reql`.
    assert reql - ported == set(), f"unclassified ReQL scripts: {sorted(reql - ported)}"


def test_the_two_index_scripts_are_deleted_not_archived():
    assert not (REPO / "scripts/create_backtest_list_indices.py").exists()
    assert not (REPO / "scripts/create_clear_state_indices.py").exists()
    assert not (REPO / "scripts/archive_rethinkdb/create_backtest_list_indices.py").exists()


def test_archive_readme_states_the_contract():
    readme = (REPO / "scripts/archive_rethinkdb/README.md").read_text()
    assert "do not run against Postgres" in readme
    assert "read it first" in readme
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_script_triage.py -v`
Expected: FAIL — the triage document does not exist.

- [ ] **Step 3: Generate the raw inventory**

```bash
cd /Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock
for f in $(grep -rl "rethinkdb\|r\.db(" scripts backend/scripts --include='*.py' | sort); do
  echo "$(grep -c "r\.db(" "$f") $f"
done
```
At the time of writing this yields **35** files under `scripts/` and **9** under `backend/scripts/` — 44 total.

- [ ] **Step 4: Write the triage document**

Create `docs/superpowers/specs/2026-08-22-postgres-port-script-triage.md`. The rule (spec §9): a script is **PORTED** if a runbook, a cron, `docs/`, or CI references it, **or** if its name says it is a recurring operation (`create_*_indices`, `purge_*`, `check_*`, `diag_*`, `run_paired_experiment`, `clear_backtest_state`, `pg_retention`). Everything else is **ARCHIVED** verbatim.

Applying it to the inventory:

```markdown
# Postgres port — scripts triage

Rule (spec §9): referenced by a runbook/cron/docs/CI, or named as a recurring
operation => PORTED. Everything else => ARCHIVED verbatim, no edits.

## PORTED

| script | ReQL sites | why |
|---|---|---|
| `scripts/validate_live_launch_readiness.py` | 1 | referenced by `docs/runbooks/live-launch-checklist.md` |
| `scripts/clear_main_instance_lookback_state.py` | 1 | referenced by docs (21 mentions) |
| `scripts/audit_point_in_time_coverage.py` | 3 | referenced by `docs/runbooks/point-in-time-capture.md` |
| `scripts/run_paired_experiment.py` | 0 (HTTP) | the A/A re-certification harness — the cutover gate |
| `scripts/clear_backtest_state.py` | 2 | recurring operation |
| `scripts/inspect_broker_state.py` | 3 | 11 doc mentions; recurring diagnostic |
| `scripts/check_range_position.py` | 2 | `check_*` recurring |
| `scripts/diag_alpaca_open.py` | 2 | `diag_*` recurring |
| `scripts/purge_bad_discovered_tickers.py` | 2 | `purge_*` recurring |
| `scripts/purge_overlay_bars_cache.py` | 2 | `purge_*` recurring |
| `scripts/reset_backtest_event_state.py` | 3 | 9 doc mentions |
| `scripts/snapshot_instance_state.py` | 3 | used by the paired-experiment flow |
| `scripts/attest_arm_start.py` | 2 | used by the paired-experiment flow |
| `scripts/backfill_learning_observations.py` | 3 | 7 doc mentions |
| `scripts/benchmark_window.py` | 2 | 2 doc mentions; recurring |
| `scripts/encrypt_brokerage_credentials.py` | 2 | credential rotation, recurring |
| `scripts/migrate_external_position.py` | 2 | 10 doc mentions |
| `scripts/migrate_llm_cache_to_canonical.py` | 2 | 7 doc mentions |
| `backend/scripts/purge_backtest_secrets.py` | 1 | `purge_*` recurring; the only `r.literal({})` site |
| `backend/scripts/audit_point_in_time_coverage.py` | 3 | runbook-referenced |
| `backend/scripts/verify_inactive_deployment.py` | 2 | 3 doc mentions; deploy check |
| `backend/scripts/migrate_alpha_tables.py` | 6 | 3 doc mentions; schema operation |
| `backend/scripts/rerun_backtest.py` | 1 | recurring operation |

## ARCHIVED

| script | ReQL sites | why |
|---|---|---|
| `scripts/apply_doc179_bull_participation_levers.py` | 2 | dated one-shot config patch |
| `scripts/apply_doc179_cash_reserve_floor_raise.py` | 2 | dated one-shot config patch |
| `scripts/apply_doc179_config_patch.py` | 2 | dated one-shot config patch |
| `scripts/apply_doc179_rotation_override_fix.py` | 2 | dated one-shot config patch |
| `scripts/apply_doc179_round3_ab_levers.py` | 2 | dated one-shot config patch |
| `scripts/apply_doc179_winner_depth_fix.py` | 2 | dated one-shot config patch |
| `scripts/apply_doc193_backtest_sell_proceeds_credit.py` | 2 | dated one-shot config patch |
| `scripts/apply_doc193_concentrate_position_sizing.py` | 2 | dated one-shot config patch |
| `scripts/apply_doc193_core_funding_mpg_aware.py` | 2 | dated one-shot config patch |
| `scripts/apply_doc193_core_satellite_reweight.py` | 2 | dated one-shot config patch |
| `scripts/apply_doc193_live_parity.py` | 2 | dated one-shot config patch |
| `scripts/apply_doc193_swap_sleeve_exclusion.py` | 2 | dated one-shot config patch |
| `scripts/apply_main_clean_room_config.py` | 2 | one-shot |
| `scripts/clear_main_recent_sell_block.py` | 2 | one-shot fixup |
| `scripts/_kalshi_analyze.py` | 2 | underscore-prefixed investigation |
| `scripts/_kalshi_recon.py` | 2 | underscore-prefixed investigation |
| `scripts/_kalshi_recreate_instance.py` | 2 | underscore-prefixed investigation |
| `backend/scripts/apply_round2_2026_07.py` | 2 | dated one-shot |
| `backend/scripts/apply_tune_2026_07.py` | 2 | dated one-shot |
| `backend/scripts/fix_doc179_hygiene.py` | 3 | one-shot fixup |
| `backend/scripts/migrate_encrypted_credentials.py` | 2 | completed one-shot migration |
| `backend/scripts/run_alpha_research.py` | 1 | one-shot research run |

## DELETED

`scripts/create_backtest_list_indices.py` and `scripts/create_clear_state_indices.py`
are deleted, not ported: `schema.ensure_schema()` subsumes both (spec §9).
```

**Regenerate the two tables from the actual `grep` before committing** — the counts above are from 2026-08-22 and the file set may have moved. `test_script_triage.py` fails if any ReQL script is unclassified.

- [ ] **Step 5: Move the archived scripts and write the README**

```bash
mkdir -p scripts/archive_rethinkdb
git mv scripts/apply_doc179_bull_participation_levers.py scripts/archive_rethinkdb/
# ...one `git mv` per ARCHIVED row, backend/scripts included...
git rm scripts/create_backtest_list_indices.py scripts/create_clear_state_indices.py
```
Create `scripts/archive_rethinkdb/README.md`:
```markdown
# Archived RethinkDB scripts

These ran once against RethinkDB. They are kept for provenance.

They **do not run against Postgres**: they import `rethinkdb`, open their own
connection, and speak ReQL, none of which exists in the runtime any more.

Porting one is not mechanical — read it first, understand what it did and
whether it should ever run again, then port it deliberately against
`backend/db/store`. Do not batch-convert this directory.

Deleted rather than archived: `create_backtest_list_indices.py` and
`create_clear_state_indices.py`. `db.schema.ensure_schema()` creates every index
they created, idempotently, at process boot.
```

- [ ] **Step 6: Run the test**

Run: `python3 -m pytest backend/tests/test_script_triage.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add -A scripts backend/tests/test_script_triage.py docs/superpowers/specs/
git commit -m "$(cat <<'EOF'
ops: triage the 44 ReQL scripts into PORTED / ARCHIVED / DELETED

The split lands as a reviewable document before any script is touched, so it is
reviewable as data rather than as 44 diffs. 22 one-shot historical scripts move
verbatim to scripts/archive_rethinkdb/; the two index-creation scripts are
deleted because schema.ensure_schema() subsumes them. A test fails the build if
any ReQL script is left unclassified.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 2: The migration script — export, convert, COPY, resume

**Files:**
- Create: `scripts/migrate_rethinkdb_to_postgres.py`
- Create: `backend/tests/test_migration_script.py`
- Create: `backend/tests/fixtures/rethink_dump_small.json.gz` (synthetic fixture)

**Interfaces:**
- Consumes: `schema.ensure_schema(*, tables=None) -> list[str]`, `schema.spec(table) -> TableSpec`, `schema.TABLES`, `pool.cursor(*, autocommit=False)`, `pool.connection(*, autocommit=False)`, `json.dumps`, `json.canonical_sha256(value) -> str`, `errors.StoreError`, `store.iter(selection, *, batch=1000)`, `broker_backtest_assembly._STEP_KEYS`.
- Produces:
  ```python
  def rethink_conn(): ...                    # lazy `import rethinkdb` lives HERE and nowhere else
  def iso(value: Any) -> Any: ...            # RethinkDB TIME pseudotype (raw) -> ISO-8601 with offset
  def export_table(table: str, *, since_id: Any | None, batch: int) -> Iterator[list[dict]]: ...
  def split_backtest_row(doc: dict) -> tuple[dict, list[tuple], dict]: ...
      # -> (metadata_doc, [(kind, seq, step_doc)], progress_row)
  def copy_batch(table: str, rows: list[dict]) -> int: ...
  def migrate_table(table: str, *, batch: int, dry_run: bool) -> dict: ...
  def main(argv: list[str] | None = None) -> int: ...
  MIGRATION_STATE_TABLE = "_migration_state"     # (table, last_id, rows_copied, finished_at)
  ```

- [ ] **Step 1: Write the failing round-trip test**

Create `backend/tests/test_migration_script.py`:
```python
import gzip
import json
import pathlib
import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "rethink_dump_small.json.gz"


@pytest.fixture
def dump():
    with gzip.open(FIXTURE, "rt") as fh:
        return json.load(fh)


def test_iso_converts_raw_time_pseudotype():
    from scripts.migrate_rethinkdb_to_postgres import iso

    raw = {"$reql_type$": "TIME", "epoch_time": 1755835020.123456, "timezone": "+00:00"}
    assert iso(raw) == "2025-08-22T03:57:00.123456+00:00"
    assert iso({"a": [raw]}) == {"a": ["2025-08-22T03:57:00.123456+00:00"]}
    assert iso("plain") == "plain"


def test_backtest_row_splits_into_metadata_steps_progress(dump):
    from scripts.migrate_rethinkdb_to_postgres import split_backtest_row
    from broker_backtest_assembly import _STEP_KEYS

    row = dump["BacktestResults"][0]
    meta, steps, progress = split_backtest_row(row)

    step_keys = {key for _kind, key, _cap in _STEP_KEYS}
    assert step_keys.isdisjoint(meta)                 # no step array left in doc
    assert meta["strategy_schema"] == row["strategy_schema"]   # metadata verbatim
    assert progress["status"] == row["status"]
    assert progress["progress"] == row["progress"]
    # seq is the source array position, final=true for a migrated row
    decisions = [s for s in steps if s[0] == "decision"]
    assert [s[1] for s in decisions] == list(range(len(row["backtest_decisions"])))
    assert decisions[0][2] == row["backtest_decisions"][0]


def test_migrated_backtest_assembles_back_byte_identical(store, dump):
    from scripts.migrate_rethinkdb_to_postgres import split_backtest_row
    from broker_backtest_assembly import assemble
    from db.json import canonical

    row = dump["BacktestResults"][0]
    meta, steps, progress = split_backtest_row(row)
    store.insert("BacktestResults", meta)
    store.insert("BacktestProgress", progress)
    store.insert("BacktestSteps",
                 [{"backtest_id": meta["id"], "kind": k, "seq": s,
                   "final": True, "doc": d} for k, s, d in steps])
    assert canonical(assemble(meta["id"])) == canonical(dict(sorted(row.items())))


def test_resume_from_last_id_does_not_duplicate(store, dump, monkeypatch):
    """Kill mid-table, rerun, end with the same row count."""
    from scripts import migrate_rethinkdb_to_postgres as mig

    rows = dump["Instances"]
    monkeypatch.setattr(mig, "export_table",
                        lambda t, *, since_id, batch: iter([rows[:2]]))
    mig.migrate_table("Instances", batch=2, dry_run=False)
    assert store.count("Instances") == 2
    monkeypatch.setattr(mig, "export_table",
                        lambda t, *, since_id, batch: iter([rows]))
    mig.migrate_table("Instances", batch=10, dry_run=False)
    assert store.count("Instances") == len(rows)     # not 2 + len(rows)


def test_nan_is_rejected_at_the_client_not_the_server(store):
    from scripts.migrate_rethinkdb_to_postgres import copy_batch

    with pytest.raises(ValueError):
        copy_batch("Instances", [{"id": "x", "v": float("nan")}])
```

- [ ] **Step 2: Build the synthetic fixture**

Create `backend/tests/fixtures/rethink_dump_small.json.gz` from a script you run once and do **not** commit:
```python
import gzip, json, uuid
dump = {
    "Instances": [{"id": f"i{n}", "name": f"inst-{n}", "kind": "equity"} for n in range(5)],
    "BacktestResults": [{
        "id": "460555",
        "instance_id": 5,                      # NUMBER, as 592 live rows have
        "status": "finished", "progress": 100.0,
        "time_elapsed_seconds": 812, "_last_active": "2026-08-01T00:00:00+00:00",
        "timestamp": "2026-08-01T00:00:00+00:00",
        "strategy_schema": {"k": "v" * 2800},         # ~28 KB
        "backtest_decisions": [{"d": n} for n in range(4300)],
        "backtest_refusals":  [{"r": n} for n in range(120)],
        "backtest_trades":    [{"t": n} for n in range(340)],
        "portfolio_value_history": [{"pv": n} for n in range(542)],
        "logs":               [f"line {n}" for n in range(500)],
        "backtest_prices":    [{"p": n} for n in range(9000)],
    }],
    "PriceHistory": [{"id": str(uuid.uuid4()), "ticker": "AAPL",
                      "timestamp": {"$reql_type$": "TIME",
                                    "epoch_time": 1755835020.0, "timezone": "+00:00"},
                      "close": 231.5} for _ in range(50)],
}
with gzip.open("backend/tests/fixtures/rethink_dump_small.json.gz", "wt") as fh:
    json.dump(dump, fh)
```
**A real 3.1 MB production document is never committed.** `MIGRATION_FIXTURE=<path>` lets an operator point the same assertions at a real export, `assert_secret_free`-checked first.

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_migration_script.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.migrate_rethinkdb_to_postgres'`.

- [ ] **Step 4: Write the export half**

Create `scripts/migrate_rethinkdb_to_postgres.py`:
```python
"""One-shot RethinkDB -> PostgreSQL migration.

The ONLY file in the tree allowed to import `rethinkdb`, and it imports it
lazily inside rethink_conn() so nothing else pays for the dependency.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
from typing import Any, Iterator

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from db import schema, store                       # noqa: E402
from db.json import canonical_sha256, dumps        # noqa: E402
from db import pool                                # noqa: E402

MIGRATION_STATE_TABLE = "_migration_state"
DEFAULT_BATCH = 2000
BACKTEST_BATCH = 200


def rethink_conn():
    """Lazy import — this is the one place `rethinkdb` may be named."""
    from rethinkdb import RethinkDB
    r = RethinkDB()
    return r, r.connect(
        host=os.environ.get("RETHINKDB_HOST", "localhost"),
        port=int(os.environ.get("RETHINKDB_PORT", "28015")),
        db=os.environ.get("RETHINKDB_DB", "IntelliStock"),
        time_format="raw",          # REQUIRED: `native` yields datetimes json.dumps cannot serialise
    )


def iso(value: Any) -> Any:
    """RethinkDB TIME pseudotype (raw form) -> ISO-8601 with offset. Recursive."""
    if isinstance(value, dict):
        if value.get("$reql_type$") == "TIME":
            tz = value.get("timezone") or "+00:00"
            sign = 1 if tz[0] != "-" else -1
            hours, minutes = int(tz[1:3]), int(tz[4:6])
            offset = _dt.timezone(sign * _dt.timedelta(hours=hours, minutes=minutes))
            return _dt.datetime.fromtimestamp(
                value["epoch_time"], tz=_dt.timezone.utc).astimezone(offset).isoformat()
        return {k: iso(v) for k, v in value.items()}
    if isinstance(value, list):
        return [iso(v) for v in value]
    return value


def export_table(table: str, *, since_id: Any | None, batch: int) -> Iterator[list[dict]]:
    """Page by primary key. NEVER skip() — it is O(n^2) on RethinkDB."""
    r, conn = rethink_conn()
    db = os.environ.get("RETHINKDB_DB", "IntelliStock")
    pk = schema.spec(table).pk_field
    last = since_id
    try:
        while True:
            q = r.db(db).table(table)
            if last is None:
                q = q.order_by(index=pk).limit(batch)
            else:
                q = q.between(last, None, index=pk,
                              left_bound="open").order_by(index=pk).limit(batch)
            rows = [iso(row) for row in q.run(conn)]
            if not rows:
                return
            yield rows
            last = rows[-1][pk]
            if len(rows) < batch:
                return
    finally:
        conn.close()
```

- [ ] **Step 5: Write `split_backtest_row` and the `PriceHistory` promotion**

```python
from broker_backtest_assembly import _STEP_KEYS      # (kind, doc_key, cap)


def split_backtest_row(doc: dict) -> tuple[dict, list[tuple], dict]:
    """(metadata_doc, [(kind, seq, step_doc)], progress_row).

    Every key that is NOT one of the six step arrays stays in `doc`, verbatim
    and unsplit. That is what makes byte-identical assembly provable: nothing
    has to be re-derived.
    """
    meta = dict(doc)
    steps: list[tuple] = []
    for kind, key, _cap in _STEP_KEYS:
        values = meta.pop(key, None)
        if values is None:
            continue                          # absent stays absent
        for seq, entry in enumerate(values):
            steps.append((kind, seq, entry))
    progress = {
        "id": doc["id"],
        "status": doc.get("status"),
        "progress": doc.get("progress"),
        "time_elapsed_seconds": doc.get("time_elapsed_seconds"),
        "last_active": doc.get("_last_active"),
    }
    return meta, steps, progress


def promote_price_history(doc: dict) -> dict:
    """PriceHistory gets ticker/ts as real columns for the partitioned PK."""
    ts = doc.get("timestamp")
    if not ts:
        raise ValueError(f"PriceHistory row {doc.get('id')!r} has no timestamp")
    return {"ticker": doc["ticker"], "ts": ts, "id": doc["id"], "doc": doc}
```
Rows whose timestamp will not parse are **rejected with an error**, never silently dropped.

- [ ] **Step 6: Write `copy_batch`, `migrate_table`, and the resume state**

```python
import psycopg


def copy_batch(table: str, rows: list[dict]) -> int:
    """COPY "T" (id, doc) FROM STDIN, with db.json.dumps so NaN is rejected here
    rather than at the far end as an opaque server-side json syntax error."""
    spec = schema.spec(table)
    payload = [(str(row[spec.pk_field]), dumps(row)) for row in rows]   # raises on NaN
    with pool.connection() as conn:
        with conn.cursor().copy(
                f'COPY "{table}" (id, doc) FROM STDIN') as copy:
            for rid, blob in payload:
                copy.write_row((rid, blob))
    return len(payload)


def migrate_table(table: str, *, batch: int, dry_run: bool) -> dict:
    schema.ensure_schema(tables=[table])
    state = store.get(MIGRATION_STATE_TABLE, table) or {}
    since = state.get("last_id")
    copied = int(state.get("rows_copied", 0))
    for rows in export_table(table, since_id=since, batch=batch):
        if dry_run:
            copied += len(rows)
            continue
        copied += _write_rows(table, rows)
        store.insert(MIGRATION_STATE_TABLE,
                     {"id": table, "last_id": rows[-1][schema.spec(table).pk_field],
                      "rows_copied": copied},
                     conflict="replace")
    if not dry_run:
        store.update(MIGRATION_STATE_TABLE, table,
                     {"finished_at": store.now()})
    return {"table": table, "rows": copied}
```
`_write_rows` dispatches: `BacktestResults` → `split_backtest_row` then three COPYs (`final=True`, `seq` = source array position); `PriceHistory` → `promote_price_history` and a 4-column COPY with partitions pre-created for the observed `ts` range; everything else → `copy_batch`. Every write is `ON CONFLICT (id) DO UPDATE`, so re-running a partial batch is a no-op — that is what makes the script resumable.

- [ ] **Step 7: Write the CLI**

```python
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="migrate_rethinkdb_to_postgres")
    p.add_argument("--tables", help="comma-separated; default: all from table_list()")
    p.add_argument("--since-id", help="resume a table mid-stream")
    p.add_argument("--batch", type=int, default=DEFAULT_BATCH,
                   help=f"COPY batch size (default {DEFAULT_BATCH}; "
                        f"{BACKTEST_BATCH} for BacktestResults)")
    p.add_argument("--verify", action="store_true", help="verify only, no writes")
    p.add_argument("--verify-sample", type=float, default=0.05)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    ...
    return 0
```

- [ ] **Step 8: Run the tests**

Run: `PG_TEST_DSN="$(scripts/dev_pg.sh dsn)" python3 -m pytest backend/tests/test_migration_script.py -v`
Expected: PASS (5 passed). The byte-identical assembly test is the gate — if `canonical(assemble(...)) != canonical(source)`, do not proceed.

- [ ] **Step 9: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add scripts/migrate_rethinkdb_to_postgres.py backend/tests/test_migration_script.py \
        backend/tests/fixtures/rethink_dump_small.json.gz
git commit -m "$(cat <<'EOF'
migrate: streaming RethinkDB -> Postgres export/import, resumable per table

Pages by primary key (never skip(), which is O(n^2)), runs the driver with
time_format='raw' and converts TIME pseudotypes to ISO-8601 with offset, and
COPYs batches through db.json.dumps so NaN is rejected at the client.
BacktestResults is split during the copy and assembles back byte-identical
under canonical(); PriceHistory gets ticker/ts promoted with partitions
pre-created. A _migration_state row per table makes a rerun continue rather
than duplicate. rethinkdb is imported lazily, in one function.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 3: `--verify` — counts, canonical hashes, index and ordering parity

**Files:**
- Modify: `scripts/migrate_rethinkdb_to_postgres.py` (add the verify half)
- Modify: `backend/tests/test_migration_script.py` (add verify tests)

**Interfaces:**
- Consumes: `json.canonical_sha256(value) -> str`, `broker_backtest_assembly.assemble(backtest_id) -> dict | None`, `store.count`, `store.iter`, `store.index_list(table) -> list[str]`, `frozen_paired_state._ALLOWED_STATE_TABLES`, `paired_state_attest` fingerprint entry point (routed through `canonical_sha256` by Plan C Task 12), `schema.spec`.
- Produces:
  ```python
  def verify_table(table: str, *, sample: float) -> dict: ...
      # {"table","rethink_rows","pg_rows","sampled","mismatches",[...]}
  def verify_ordering(table: str, *, first_n: int = 200) -> list[str]: ...
  def verify_indexes(table: str) -> list[str]: ...
  def verify_fingerprint() -> tuple[str, str]: ...    # (rethink_fp, pg_fp)
  MISMATCH_DIR = ".migration-mismatches"
  ```

- [ ] **Step 1: Write the failing verify tests**

Append to `backend/tests/test_migration_script.py`:
```python
def test_verify_writes_whole_documents_on_mismatch(store, dump, tmp_path, monkeypatch):
    from scripts import migrate_rethinkdb_to_postgres as mig

    monkeypatch.setattr(mig, "MISMATCH_DIR", str(tmp_path))
    source = dict(dump["Instances"][0])
    store.insert("Instances", {**source, "name": "TAMPERED"})
    monkeypatch.setattr(mig, "export_table",
                        lambda t, *, since_id, batch: iter([[source]]))
    report = mig.verify_table("Instances", sample=1.0)
    assert report["mismatches"] == 1
    written = list(tmp_path.rglob("*.json"))
    assert len(written) == 1
    body = json.loads(written[0].read_text())
    assert body["rethink"]["name"] == source["name"]     # WHOLE doc, both sides
    assert body["postgres"]["name"] == "TAMPERED"


def test_verify_hash_is_invariant_to_key_order(store, dump, monkeypatch):
    from scripts import migrate_rethinkdb_to_postgres as mig

    source = dump["Instances"][1]
    store.insert("Instances", dict(reversed(list(source.items()))))
    monkeypatch.setattr(mig, "export_table",
                        lambda t, *, since_id, batch: iter([[source]]))
    assert mig.verify_table("Instances", sample=1.0)["mismatches"] == 0


def test_verify_ordering_is_bytewise_on_scope_suffixed_ids(store):
    from scripts import migrate_rethinkdb_to_postgres as mig

    ids = ["alpaca-main|a", "alpaca-mainZ", "alpaca-main9", "alpaca-mainb"]
    for rid in ids:
        store.insert("GraphNexusTradeContexts", {"id": rid})
    assert mig.verify_ordering("GraphNexusTradeContexts", first_n=4) == sorted(ids)


def test_verify_exits_nonzero_on_any_mismatch(store, dump, monkeypatch):
    from scripts import migrate_rethinkdb_to_postgres as mig

    source = dump["Instances"][2]
    store.insert("Instances", {**source, "name": "WRONG"})
    monkeypatch.setattr(mig, "export_table",
                        lambda t, *, since_id, batch: iter([[source]]))
    assert mig.main(["--verify", "--tables", "Instances", "--verify-sample", "1.0"]) != 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_migration_script.py -k verify -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'verify_table'`.

- [ ] **Step 3: Implement `verify_table`**

```python
import json as _stdjson
import pathlib
import random

MISMATCH_DIR = ".migration-mismatches"


def verify_table(table: str, *, sample: float) -> dict:
    """Row count + canonical sha256 on a sampled fraction. Mismatches are written
    WHOLE, both documents, never summarised into a counter."""
    from broker_backtest_assembly import assemble

    rethink_rows = 0
    mismatches = 0
    sampled = 0
    for batch in export_table(table, since_id=None, batch=DEFAULT_BATCH):
        rethink_rows += len(batch)
        for source in batch:
            if random.random() > sample:
                continue
            sampled += 1
            rid = str(source[schema.spec(table).pk_field])
            target = (assemble(rid) if table == "BacktestResults"
                      else store.get(table, rid))
            if target is None or canonical_sha256(source) != canonical_sha256(target):
                mismatches += 1
                _dump_mismatch(table, rid, source, target)
    return {"table": table, "rethink_rows": rethink_rows,
            "pg_rows": store.count(table), "sampled": sampled,
            "mismatches": mismatches}


def _dump_mismatch(table, rid, source, target) -> None:
    out = pathlib.Path(MISMATCH_DIR) / table
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{rid}.json").write_text(
        _stdjson.dumps({"rethink": source, "postgres": target},
                       indent=2, sort_keys=True))
```
Timestamps are normalised to UTC ISO on both sides first (that is what `iso()` already did on the source). **Nothing else is normalised**, because anything else that differs is a bug.

- [ ] **Step 4: Implement `verify_ordering`, `verify_indexes`, `verify_fingerprint`**

```python
def verify_ordering(table: str, *, first_n: int = 200) -> list[str]:
    """The collation check on real data: the first N ids in ORDER BY id must
    match between the two stores. Run for every _ALLOWED_STATE_TABLES entry."""
    sel = store.limit(store.order_by(store.filter(table, {}),
                                     fields=("id",), desc=False), first_n)
    return [row["id"] for row in store.run(sel)]


def verify_indexes(table: str) -> list[str]:
    """Every ReQL secondary index must have a Postgres counterpart."""
    r, conn = rethink_conn()
    db = os.environ.get("RETHINKDB_DB", "IntelliStock")
    try:
        reql = set(r.db(db).table(table).index_list().run(conn))
    finally:
        conn.close()
    pg = set(store.index_list(table))
    return sorted(reql - pg)


def verify_fingerprint() -> tuple[str, str]:
    """paired_state_attest's fingerprint over the 26 _ALLOWED_STATE_TABLES,
    computed against each store, with _VOLATILE_FIELDS excluded as it does today."""
    from frozen_paired_state import _ALLOWED_STATE_TABLES
    import paired_state_attest as psa
    return psa.fingerprint_rethink(_ALLOWED_STATE_TABLES), \
           psa.fingerprint_store(_ALLOWED_STATE_TABLES)
```
If `paired_state_attest` has no `fingerprint_rethink`, add one **in this script** rather than in the module — the module must stay RethinkDB-free.

- [ ] **Step 5: Wire the CLI's `--verify` path and exit code**

```python
if args.verify:
    failed = False
    for table in tables:
        report = verify_table(table, sample=args.verify_sample)
        print(report)
        if report["mismatches"] or report["rethink_rows"] != report["pg_rows"]:
            failed = True
        missing = verify_indexes(table)
        if missing:
            print(f"  MISSING INDEXES: {missing}")
            failed = True
    rk_fp, pg_fp = verify_fingerprint()
    if rk_fp != pg_fp:
        print(f"  FINGERPRINT MISMATCH: {rk_fp} != {pg_fp}")
        failed = True
    return 1 if failed else 0
```

- [ ] **Step 6: Run the tests**

Run: `PG_TEST_DSN="$(scripts/dev_pg.sh dsn)" python3 -m pytest backend/tests/test_migration_script.py -v`
Expected: PASS (9 passed).

- [ ] **Step 7: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add scripts/migrate_rethinkdb_to_postgres.py backend/tests/test_migration_script.py
git commit -m "$(cat <<'EOF'
migrate: --verify with counts, canonical sha256, index and ordering parity

Per table: row count both sides, canonical_sha256 on a sampled fraction
(assemble() for BacktestResults, plain doc otherwise), every ReQL secondary
index has a Postgres counterpart, and the first 200 ids under ORDER BY id match
— the collation check on real data. The 26-table paired_state_attest fingerprint
must be equal. Mismatches are written whole, both documents, to
.migration-mismatches/<table>/<id>.json; the run exits non-zero on any.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 4: `scripts/pg_retention.py`

**Files:**
- Create: `scripts/pg_retention.py`
- Create: `backend/tests/test_pg_retention.py`

**Interfaces:**
- Consumes: `schema.TABLES: dict[str, TableSpec]`, `schema.spec(table)`, `RetentionSpec(field, days_env, default_days=None)`, `store.between`, `store.iter`, `store.delete`, `store.count`, `pool.cursor`.
- Produces:
  ```python
  def retention_days(spec: TableSpec) -> int | None: ...
      # env spec.retention.days_env wins; else spec.retention.default_days; None => OFF
  def prune_table(table: str, *, dry_run: bool = False, batch: int = 5000) -> dict: ...
  def main(argv: list[str] | None = None) -> int: ...
  ```

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_pg_retention.py`:
```python
import pytest


def test_retention_is_off_until_the_operator_sets_the_env_var(monkeypatch):
    from scripts.pg_retention import retention_days
    from db import schema

    spec = schema.spec("GraphNexusLLMPromptCache")
    monkeypatch.delenv(spec.retention.days_env, raising=False)
    assert retention_days(spec) is None


def test_env_var_enables_it(monkeypatch):
    from scripts.pg_retention import retention_days
    from db import schema

    spec = schema.spec("GraphNexusLLMPromptCache")
    monkeypatch.setenv(spec.retention.days_env, "30")
    assert retention_days(spec) == 30


def test_prune_is_a_ranged_delete_on_the_indexed_column(store, monkeypatch):
    from scripts.pg_retention import prune_table
    from db import schema

    spec = schema.spec("GraphNexusLLMPromptCache")
    monkeypatch.setenv(spec.retention.days_env, "1")
    store.insert("GraphNexusLLMPromptCache", [
        {"id": "old", "cached_at": "2020-01-01T00:00:00+00:00"},
        {"id": "new", "cached_at": "2999-01-01T00:00:00+00:00"},
    ])
    statements = []
    monkeypatch.setattr("scripts.pg_retention._execute",
                        lambda sql, params: statements.append(sql) or 1)
    prune_table("GraphNexusLLMPromptCache")
    assert all("DELETE FROM" in s for s in statements)
    assert all('"cached_at"' in s for s in statements), "must filter the indexed column"
    assert not any("doc->>" in s for s in statements), "unindexed DELETE"


def test_prune_of_an_unconfigured_table_is_a_noop(store):
    from scripts.pg_retention import prune_table
    assert prune_table("Instances") == {"table": "Instances", "deleted": 0,
                                        "skipped": "retention not configured"}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_pg_retention.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement it**

```python
"""Batched ranged deletes driven by schema.TABLES[...].retention.

Never issues an unindexed DELETE: every predicate is on the STORED generated
column that carries a B-tree, not on `doc->>`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
from db import pool, schema                      # noqa: E402

BATCH = 5000


def retention_days(spec) -> int | None:
    if spec.retention is None:
        return None
    raw = os.environ.get(spec.retention.days_env)
    if raw is not None and raw.strip():
        return int(raw)
    return spec.retention.default_days           # None => OFF by default


def _execute(sql: str, params: tuple) -> int:
    with pool.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def prune_table(table: str, *, dry_run: bool = False, batch: int = BATCH) -> dict:
    spec = schema.spec(table)
    days = retention_days(spec)
    if days is None:
        return {"table": table, "deleted": 0, "skipped": "retention not configured"}
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    col = spec.retention.field
    deleted = 0
    while True:
        if dry_run:
            break
        sql = (f'DELETE FROM "{table}" WHERE ctid IN ('
               f'  SELECT ctid FROM "{table}" WHERE "{col}" < %s LIMIT {batch})')
        n = _execute(sql, (cutoff,))
        deleted += n
        if n < batch:
            break
    return {"table": table, "deleted": deleted, "cutoff": cutoff.isoformat()}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pg_retention")
    p.add_argument("--tables", help="comma-separated; default: every table with a RetentionSpec")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--batch", type=int, default=BATCH)
    args = p.parse_args(argv)
    names = (args.tables.split(",") if args.tables
             else [n for n, s in schema.TABLES.items() if s.retention is not None])
    for name in names:
        print(prune_table(name, dry_run=args.dry_run, batch=args.batch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```
The `ctid IN (SELECT ... LIMIT n)` form is what keeps each statement bounded: an unbounded `DELETE` on `PriceHistory` (2.85M rows) would hold one long transaction and bloat the table. For partitioned tables the operator drops whole partitions instead — that path is `pg_partman`'s, not this script's.

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest backend/tests/test_pg_retention.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add scripts/pg_retention.py backend/tests/test_pg_retention.py
git commit -m "$(cat <<'EOF'
ops: scripts/pg_retention.py — batched ranged deletes from the table registry

Reads schema.TABLES[...].retention, and every window is OFF until the operator
sets the named env var. Each DELETE is bounded by ctid IN (SELECT ... LIMIT n)
and filters the STORED generated column that carries a B-tree, never `doc->>` —
a test fails the build if an unindexed DELETE ever appears.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 5: Docker compose — the `postgres` service and `PG_*` env wiring

**Files:**
- Create: `docker/postgres/Dockerfile`
- Modify: `docker-compose.yml` (add the `postgres` service; add `PG_*` env to `backend`, `api`, `price-service`, `backtest-engine`, `discord-bot`; add the `postgres_data` volume; **leave the `rethinkdb` service in place**)
- Modify: `backend/server.py:492`, `:701`, `:720`, `:735` — the spawned-container env builder gains `INSTANCE_POSTGRES_HOST`
- Modify: `backend/tests/test_docker_compose_security.py` (extend)
- Modify: `.env.example`

**Interfaces:**
- Consumes: `pool.dsn_from_env() -> str` — `PG_DSN` wins; else assembled from `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` (defaults `localhost` / `5432` / `intellistock` / – / `IntelliStock`).
- Produces: env contract `POSTGRES_HOST=postgres`, `POSTGRES_PORT=5432`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_BIND_ADDR` (default `127.0.0.1`), `INSTANCE_POSTGRES_HOST`.

- [ ] **Step 1: Impact analysis on the env builder**

Run:
```
gitnexus_impact({target: "start_instance_container", direction: "upstream"})
```
(use the actual function name enclosing `server.py:492` and `:701`). Report. These functions build the env for every spawned broker container — a missed variable means every instance boots blind to the database.

- [ ] **Step 2: Write the failing compose test**

Append to `backend/tests/test_docker_compose_security.py`:
```python
def test_postgres_binds_to_localhost_by_default():
    """Unlike RethinkDB, Postgres is not exposed on 0.0.0.0. Preserving the old
    default would export an authenticated database to the network by accident."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert '"${POSTGRES_BIND_ADDR:-127.0.0.1}:5432:5432"' in compose
    assert '\n      - "5432:5432"' not in compose


def test_postgres_password_is_mandatory_during_interpolation():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert ("POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}"
            in compose)
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-" not in compose


def test_every_rethinkdb_service_also_gets_postgres_env():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    services = ["backend", "api", "price-service", "backtest-engine", "discord-bot"]
    for name in services:
        block = compose.split(f"\n  {name}:", 1)[1].split("\n  ", 1)[0]
        assert "RETHINKDB_HOST=rethinkdb" in block, name    # left in place
        assert "POSTGRES_HOST=postgres" in block, name
        assert "POSTGRES_PORT=5432" in block, name


def test_rethinkdb_service_is_left_in_place():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    assert "\n  rethinkdb:" in compose
    assert "rethinkdb_data:" in compose


def test_postgres_has_shm_size_and_a_memory_limit():
    compose = (REPO_ROOT / "docker-compose.yml").read_text()
    block = compose.split("\n  postgres:", 1)[1].split("\n  ", 1)[0]
    assert "shm_size: 1gb" in block          # the Docker default 64MB breaks parallel scans
    assert "memory: 4G" in block
    assert "shared_buffers=1GB" in block     # 25% of the 4G limit
    assert "PG_OOM_ADJUST_FILE: /proc/self/oom_score_adj" in block
    assert 'PG_OOM_ADJUST_VALUE: "0"' in block
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_docker_compose_security.py -v`
Expected: FAIL on the five new tests; the existing RethinkDB tests still pass.

- [ ] **Step 4: Write `docker/postgres/Dockerfile`**

```dockerfile
FROM postgres:17

# pg_partman's background worker manages PriceHistory's monthly partitions.
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-partman \
 && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 5: Add the `postgres` service to `docker-compose.yml`**

```yaml
  postgres:
    build:
      context: ./docker/postgres        # FROM postgres:17 + postgresql-17-partman
    container_name: intellistock-postgres
    command: >
      postgres -c shared_buffers=1GB -c work_mem=16MB
               -c maintenance_work_mem=256MB
               -c shared_preload_libraries=pg_partman_bgw
               -c pg_partman_bgw.dbname=IntelliStock
               -c pg_partman_bgw.interval=3600
               -c default_toast_compression=lz4
               -c max_connections=120
               -c timezone=UTC
    environment:
      POSTGRES_DB: IntelliStock
      POSTGRES_USER: ${POSTGRES_USER:-intellistock}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      PG_OOM_ADJUST_FILE: /proc/self/oom_score_adj
      PG_OOM_ADJUST_VALUE: "0"
    shm_size: 1gb
    ports: ["${POSTGRES_BIND_ADDR:-127.0.0.1}:5432:5432"]
    volumes: [postgres_data:/var/lib/postgresql/data]
    restart: unless-stopped
    deploy:
      resources:
        limits: {cpus: "3", memory: 4G}
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-intellistock} -d IntelliStock"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 20s
```
Add `postgres_data:` under `volumes:`. **`shm_size: 1gb` is required** — the Docker default `/dev/shm` is 64 MB and is *not* `shared_buffers`; parallel scans fail with a confusing error. `shared_buffers=1GB` is 25% of the 4G limit.

`default_toast_compression=lz4` is **unverified** for the PGDG `postgres:17` build. `scripts/dev_pg.sh up` prints the `SHOW default_toast_compression` verdict; if it is absent, drop the flag — pglz is the fallback and costs disk, not correctness.

- [ ] **Step 6: Wire `PG_*` into every service that declares `RETHINKDB_*`**

For `backend` (`:104-116`), `api` (`:176-177`), `price-service` (`:199-200`), `backtest-engine` (`:230-236`), and `discord-bot` (`:302-303`), add alongside the existing RethinkDB variables:
```yaml
      - POSTGRES_HOST=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_DB=${POSTGRES_DB:-IntelliStock}
      - POSTGRES_USER=${POSTGRES_USER:-intellistock}
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
```
`backend` and `backtest-engine` also gain `INSTANCE_POSTGRES_HOST=postgres` next to their `INSTANCE_RETHINKDB_HOST`. Add `postgres` to each service's `depends_on`, with `condition: service_healthy` where that service already uses a condition and as a bare entry where it does not.

**Leave every `RETHINKDB_*` variable in place.** The rethinkdb service stays up but unreferenced until the operator decommissions it, and the rollback path is unsetting `PG_DSN` — which only works if the old variables are still there.

- [ ] **Step 7: Pass `INSTANCE_POSTGRES_HOST` through the spawned-container env builder**

In `backend/server.py`, at `:492`/`:701` (both call sites) and in the passthrough list at `:720`:
```python
# before
rethink_host = os.environ.get('INSTANCE_RETHINKDB_HOST', RETHINKDB_HOST)
...
'RETHINKDB_HOST': rethink_host,
# after — additive; the RethinkDB lines stay
pg_host = os.environ.get('INSTANCE_POSTGRES_HOST',
                         os.environ.get('POSTGRES_HOST', 'localhost'))
...
'RETHINKDB_HOST': rethink_host,
'POSTGRES_HOST': pg_host,
'POSTGRES_PORT': os.environ.get('POSTGRES_PORT', '5432'),
'POSTGRES_DB': os.environ.get('POSTGRES_DB', 'IntelliStock'),
'POSTGRES_USER': os.environ.get('POSTGRES_USER', 'intellistock'),
'POSTGRES_PASSWORD': os.environ.get('POSTGRES_PASSWORD', ''),
```
And extend the `:720` passthrough tuple:
```python
'RETHINKDB_HOST', 'RETHINKDB_PORT',
'POSTGRES_HOST', 'POSTGRES_PORT', 'POSTGRES_DB',
'POSTGRES_USER', 'POSTGRES_PASSWORD', 'PG_DSN', 'PG_POOL_MAX',
```

- [ ] **Step 8: Update `.env.example` and `.gitignore`**

`.env.example` gains:
```
# PostgreSQL (the datastore as of the 2026-08 port). PG_DSN wins if set.
POSTGRES_PASSWORD=<FILL THIS IN>
# POSTGRES_USER=intellistock
# POSTGRES_DB=IntelliStock
# POSTGRES_BIND_ADDR=127.0.0.1   # set to 0.0.0.0 only for private-network access
# PG_DSN=
# PG_POOL_MAX=8
```
`.gitignore` gains `.devpg/` and `.migration-mismatches/`.

- [ ] **Step 9: Run the test and validate the compose file**

Run:
```bash
python3 -m pytest backend/tests/test_docker_compose_security.py -v
POSTGRES_PASSWORD=x SOCKET_CONTROL_MASTER_KEY=$(printf 'a%.0s' {1..64}) \
  docker compose config >/dev/null && echo "compose OK"
```
Expected: PASS, and `compose OK`.

- [ ] **Step 10: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add docker/ docker-compose.yml backend/server.py .env.example .gitignore \
        backend/tests/test_docker_compose_security.py
git commit -m "$(cat <<'EOF'
ops: compose postgres service + PG_* env on every backend service

postgres:17 + postgresql-17-partman, shm_size 1gb (the Docker default 64MB is
not shared_buffers and breaks parallel scans), shared_buffers at 25% of the 4G
limit, PG_OOM_ADJUST so the postmaster is not the OOM killer's first choice, and
a pg_isready healthcheck. Unlike RethinkDB it binds 127.0.0.1 by default —
preserving the old default would export an authenticated database to the network
by accident. The rethinkdb service and every RETHINKDB_* variable stay in place;
the flip is setting PG_DSN and the rollback is unsetting it.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 6: Port the PORTED scripts

**Files:**
- Modify: every script in Task 1's `PORTED` table (23 files at the time of writing, ~45 ReQL sites)
- Test: `backend/tests/test_ported_scripts.py` (create)
- Test (existing, must pass): `backend/tests/test_fix_doc179_hygiene.py`, `backend/tests/test_clear_main_instance_lookback_state.py`

**Interfaces:**
- Consumes: the full `store` API in `Interfaces — Common`, plus `Literal(value)` from `db.merge` (`backend/scripts/purge_backtest_secrets.py:101-103` is the `r.literal({})` site), `P.field(key)`, `schema.ensure_table`, `pool.health()`.
- Produces: nothing new. Porting means replacing ReQL with `db.store` **and nothing else** — no refactors, no renamed flags, no changed output format.

- [ ] **Step 1: Impact analysis**

For each ported script, run `gitnexus_impact({target: "main", direction: "upstream"})` scoped to that file, plus upstream on any helper it exports. Report. Most are standalone entry points with zero upstream — say so explicitly rather than skipping the check.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_ported_scripts.py`:
```python
import importlib
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
TRIAGE = REPO / "docs/superpowers/specs/2026-08-22-postgres-port-script-triage.md"
ROW = re.compile(r"^\| `((?:backend/)?scripts/[^`]+\.py)` \|")


def _ported():
    body = TRIAGE.read_text().split("## PORTED", 1)[1].split("\n## ", 1)[0]
    return [m.group(1) for line in body.splitlines() if (m := ROW.match(line))]


@pytest.mark.parametrize("path", _ported())
def test_ported_script_has_no_reql(path):
    text = (REPO / path).read_text()
    assert "rethinkdb" not in text
    assert "r.db(" not in text
    assert "noreply_wait" not in text


def test_purge_secrets_uses_literal_to_blank_the_subtree(store):
    from db import Literal
    import backend.scripts.purge_backtest_secrets as purge

    store.insert("BacktestResults",
                 {"id": "1", "alpaca_secret": "s3cr3t", "credentials": {"k": "v"},
                  "status": "finished"})
    purge.purge_row("1")
    got = store.get("BacktestResults", "1")
    assert got["credentials"] == {}          # blanked, not merged into
    assert got["status"] == "finished"       # siblings survive


def test_clear_backtest_state_deletes_on_a_selection(store):
    import scripts.clear_backtest_state as cbs

    store.insert("BacktestResults", [{"id": str(i), "instance_id": "x"}
                                     for i in range(4)]
                                    + [{"id": "keep", "instance_id": "y"}])
    assert cbs.clear("x") == 4
    assert store.get("BacktestResults", "keep") is not None
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_ported_scripts.py -v`
Expected: FAIL — every parametrised case fails on `"rethinkdb" not in text`.

- [ ] **Step 4: Port the scripts**

Apply the same recipe Plan C uses. The two that need care:

**`backend/scripts/purge_backtest_secrets.py:101-103`** — the only `r.literal({})` site in the tree:
```python
# before
r.db(DB_NAME).table('BacktestResults').get(bid).update({
    'credentials': r.literal({}),
    'alpaca_secret': r.literal(''),
}).run(conn)
# after
from db import store, Literal
store.update('BacktestResults', bid, {
    'credentials': Literal({}),
    'alpaca_secret': Literal(''),
})
```
`Literal` sets the subtree shallow rather than merging into it — a plain `{}` patch would be a no-op under deep merge, which is exactly the bug this script exists to prevent.

**`scripts/clear_backtest_state.py`** and **`scripts/clear_main_instance_lookback_state.py`** — delete on a `Selection` is one statement:
```python
res = store.delete('BacktestResults', store.filter('BacktestResults', {'instance_id': iid}))
return res['deleted']
```

The remaining 20 are `.get` / `.insert` / `.update` / `.filter` / `.order_by` rewrites plus deleting the `get_conn` / `conn.close(noreply_wait=False)` plumbing. `scripts/run_paired_experiment.py` speaks HTTP, not ReQL — its only change is any import of a deleted helper.

- [ ] **Step 5: Run the tests**

Run:
```bash
python3 -m pytest backend/tests/test_ported_scripts.py \
  backend/tests/test_fix_doc179_hygiene.py \
  backend/tests/test_clear_main_instance_lookback_state.py -v
```
Expected: PASS.

- [ ] **Step 6: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add scripts backend/scripts backend/tests/test_ported_scripts.py
git commit -m "$(cat <<'EOF'
ops: port the 23 recurring-operation scripts to db.store

Porting means replacing ReQL and nothing else — no refactors, no renamed flags,
no changed output. purge_backtest_secrets keeps its r.literal({}) semantics via
db.merge.Literal (a plain {} patch would be a no-op under deep merge, which is
the exact bug that script exists to prevent), and the clear-state scripts keep
delete-on-a-selection as one statement. A parametrised test reads the triage
document and fails if any PORTED script still names rethinkdb.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 7: The cutover runbook and the docs

**Files:**
- Create: `docs/runbooks/postgres-cutover.md`
- Modify: `CLAUDE.md` (a "Datastore" section)
- Modify: `README.md` (the setup section)

**Interfaces:**
- Consumes: `scripts/migrate_rethinkdb_to_postgres.py` (`--tables`, `--since-id`, `--batch`, `--verify`, `--verify-sample`, `--dry-run`), `scripts/pg_retention.py`, `scripts/dev_pg.sh` (`up`/`dsn`/`psql`/`down`/`nuke`, from Plan A), `scripts/run_paired_experiment.py` (`--instance`, `--doc`, `--start`, `--end`, `--cash`, `--granularity`, `--control KEY=VALUE`, `--treatment KEY=VALUE`, `--snapshot PATH`, `--warmup-start DATE`).
- Produces: `docs/runbooks/postgres-cutover.md` — nine ordered steps, each with a stop condition.

- [ ] **Step 1: Write the failing runbook-contract test**

Create `backend/tests/test_cutover_runbook.py`:
```python
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
RUNBOOK = REPO / "docs/runbooks/postgres-cutover.md"


def test_runbook_has_every_ordered_step():
    text = RUNBOOK.read_text()
    for heading in ["## 1. Pre-flight", "## 2. Freeze", "## 3. Export/import",
                    "## 4. Verify", "## 5. Flip", "## 6. Smoke",
                    "## 7. Re-certify", "## 8. Rollback", "## 9. Decommission"]:
        assert heading in text, heading


def test_runbook_names_the_collation_gate_and_the_real_money_instance():
    text = RUNBOOK.read_text()
    assert "latest_observation_date DESC, id DESC" in text
    assert 'COLLATE "C"' in text
    assert "alpaca-main" in text
    assert "restarted last" in text


def test_runbook_stops_on_any_verify_mismatch():
    text = RUNBOOK.read_text()
    assert "Stop on any mismatch" in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_cutover_runbook.py -v`
Expected: FAIL — the runbook does not exist.

- [ ] **Step 3: Write `docs/runbooks/postgres-cutover.md`**

```markdown
# Postgres cutover runbook

Ordered. Each step has a stop condition. Do not proceed past a stop condition.

Nothing here runs from the branch's CI. The re-certification in step 7 is the
user's gate.

## 1. Pre-flight

Backend redeployed from `feat/postgres-port` but **still pointed at RethinkDB**
(`PG_DSN` unset). Then:

    docker compose exec postgres psql -U intellistock -d IntelliStock \
      -c "SHOW default_toast_compression;"
    docker compose exec postgres psql -U intellistock -d IntelliStock \
      -c "SHOW shared_buffers;"
    docker stats --no-stream intellistock-postgres     # confirm the 4G limit
    df -h                                              # expect ~16 GB free, plus headroom

Record the RethinkDB row counts:

    python3 scripts/migrate_rethinkdb_to_postgres.py --dry-run > preflight-counts.txt

**Stop if** `default_toast_compression` is not `lz4` — drop the
`-c default_toast_compression=lz4` flag from the compose command and restart
(pglz is the fallback and costs disk, not correctness), then repeat this step.
**Stop if** `shm_size` is not 1 GB or `shared_buffers` is not 1 GB.

## 2. Freeze

Stop every instance and every engine. Confirm zero running backtests. Do this on
a **weekend, outside market hours** — a partial export of a table being written
is a silent corruption, not an error.

**Stop if** any instance is still running.

## 3. Export/import

    python3 scripts/migrate_rethinkdb_to_postgres.py --batch 2000

Expect ~16 GB and roughly an hour. The run is resumable: if it dies, rerun the
same command and it continues from `_migration_state.last_id` per table.

**Stop if** the exit code is non-zero.

## 4. Verify

    python3 scripts/migrate_rethinkdb_to_postgres.py --verify \
      --verify-sample 1.0 --tables "$(python3 -c '
import sys; sys.path.insert(0, "backend")
from frozen_paired_state import _ALLOWED_STATE_TABLES
print(",".join(sorted(_ALLOWED_STATE_TABLES)))')"
    python3 scripts/migrate_rethinkdb_to_postgres.py --verify --verify-sample 0.05

**Stop on any mismatch.** Mismatches are written whole, both documents, to
`.migration-mismatches/<table>/<id>.json`. Read them; do not summarise them.

Budget an hour for the 16 GB sampled pass.

## 5. Flip

    docker compose up -d postgres
    # set PG_DSN in .env
    docker compose up -d --force-recreate backend api price-service backtest-engine discord-bot

The RethinkDB container stays up, unreferenced.

**Stop if** `docker compose ps` shows postgres anything other than `healthy`.

## 6. Smoke

Each of these must pass before step 7:

- The backtest list page renders, with the same rows in the same order.
- A backtest starts, progresses (watch `BacktestProgress` move), and stops on
  request.
- An instance start/stop round-trips.
- `clear-state` on a **scratch** instance reports a **non-zero** `would_delete`
  for the scoped tables. This is the `clear_instance_state.py:100-104`
  regression: exact-only prefix matching once found zero scoped rows and turned
  a full clear into a silent no-op.

**Stop if** `would_delete` is zero for a table that has scoped rows.

## 7. Re-certify (the user's gate)

One paired **cold** A/A and one paired **warm** A/A:

    python3 scripts/run_paired_experiment.py --instance <scratch> --doc <doc> \
      --start <YYYY-MM-DD> --end <YYYY-MM-DD> --cash 6000 --granularity 900

Both must be **byte-identical with 100% traded-name overlap** — the same bar as
bt 479057 / 193668.

Then verify the collation window explicitly:

    docker compose exec postgres psql -U intellistock -d IntelliStock -c '
      SELECT id FROM "GraphNexusTradeOutcomes"
      ORDER BY latest_observation_date COLLATE "C" DESC, id COLLATE "C" DESC
      LIMIT 80;'

Compare against the same 80 ids read from RethinkDB before the freeze. The
`(latest_observation_date DESC, id DESC)` tiebreak decides membership of a
window that lands in an LLM prompt, and a non-bytewise collation changes it
silently. This is the single most likely silent failure in the migration.

**Stop if** either A/A is not byte-identical, or the 80-id window differs.

## 8. Rollback

Unset `PG_DSN`, restart. RethinkDB is untouched and still authoritative for
everything written before the freeze. Anything written after the flip is lost —
which is why step 7 runs before any real-money instance is restarted.

## 9. Decommission (a later, separate decision)

Stop the RethinkDB container, keep the volume for 30 days, then drop it.

**Before this step, answer:** is anything backing RethinkDB up off-box today? If
not, the RethinkDB volume is the only rollback path and the 30 days are load-
bearing.

---

`alpaca-main` (Strategies doc 179, real money) is **restarted last**, after every
other instance has run a full clean weekly cycle on Postgres.
```

- [ ] **Step 4: Update `CLAUDE.md` and `README.md`**

Add to `CLAUDE.md`, after the GitNexus section:
```markdown
## Datastore

PostgreSQL 17 + JSONB, through `backend/db/`. **No module outside `backend/db/`
opens a connection**, and nothing outside `scripts/migrate_rethinkdb_to_postgres.py`
imports `rethinkdb`.

- Reads and writes: `from db import store` — `store.get/get_all/insert/update/
  replace/replace_if/delete/between/filter/order_by/limit/count/run/iter`.
- `store.update()` **deep-merges** (objects merge, arrays replace). `Literal(v)`
  sets shallow. Never write `||`.
- Change notification: `from db import watch` — `watch_row` / `watch_table` /
  `watch_filter`. Watchers re-read on start and on every reconnect.
- Every ordered read is `COLLATE "C"` (bytewise). No GIN indexes anywhere.
- DDL lives in `backend/db/schema.py`. Do not write `CREATE INDEX` at a call site.
- Local test database: `scripts/dev_pg.sh up`, then export the `PG_TEST_DSN` it
  prints. Without it the test suite runs against an in-process `FakeStore`.
- Cutover: `docs/runbooks/postgres-cutover.md`.
```
Update `README.md`'s setup section to mention `POSTGRES_PASSWORD` in `.env` and the `postgres` compose service, keeping the existing RethinkDB paragraph with a note that it is retained for rollback until decommissioned.

- [ ] **Step 5: Run the test**

Run: `python3 -m pytest backend/tests/test_cutover_runbook.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add docs/runbooks/postgres-cutover.md CLAUDE.md README.md \
        backend/tests/test_cutover_runbook.py
git commit -m "$(cat <<'EOF'
docs: postgres cutover runbook + CLAUDE.md/README datastore section

Nine ordered steps, each with a stop condition: pre-flight (lz4/shm/mem verdict
plus recorded row counts), freeze, export/import, verify (100% sample on the 26
_ALLOWED_STATE_TABLES, 5% elsewhere, stop on any mismatch), flip, smoke
(including the clear-state silent-no-op regression), re-certify (cold + warm A/A
byte-identical, plus an explicit check of the 80-row COLLATE "C" window that
feeds an LLM prompt), rollback, decommission. alpaca-main restarts last.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 8: Final cleanup, full verification, and the PR

**Files:**
- Modify: `backend/requirements.txt` (`psycopg[binary,pool]>=3.2.10,<4`; no `rethinkdb`)
- Create: `backend/requirements-migration.txt` (`rethinkdb` as the optional extra)
- Modify: `backend/Dockerfile` (confirm it installs only `requirements.txt`)

**Interfaces:**
- Consumes: everything Plans A, B, C, and D produced.
- Produces: an **open, unmerged** PR from `feat/postgres-port`.

- [ ] **Step 1: Write the failing dependency test**

Create `backend/tests/test_requirements_split.py`:
```python
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_runtime_requirements_have_psycopg_and_no_rethinkdb():
    text = (REPO / "backend" / "requirements.txt").read_text()
    assert "psycopg[binary,pool]>=3.2.10,<4" in text
    assert "rethinkdb" not in text.lower()


def test_migration_extra_carries_rethinkdb():
    text = (REPO / "backend" / "requirements-migration.txt").read_text()
    assert "rethinkdb" in text.lower()


def test_dockerfile_installs_only_the_runtime_requirements():
    text = (REPO / "backend" / "Dockerfile").read_text()
    assert "requirements.txt" in text
    assert "requirements-migration.txt" not in text
    assert "python:3.11-slim" in text        # the compat floor this port targets
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_requirements_split.py -v`
Expected: FAIL.

- [ ] **Step 3: Split the requirements**

`backend/requirements.txt`: delete the `rethinkdb` line (Plan C Task 14 already did this — confirm) and add:
```
psycopg[binary,pool]>=3.2.10,<4
```
The `>=3.2.10` floor is the `notifies()` memory-leak fix; watchers use `notifies()` only, never `add_notify_handler()`.

Create `backend/requirements-migration.txt`:
```
# Only for scripts/migrate_rethinkdb_to_postgres.py, the one-shot export.
# NOT installed in the runtime image. Install it in a throwaway venv on the
# machine that runs the migration:
#     python3 -m venv .migvenv && .migvenv/bin/pip install \
#       -r backend/requirements.txt -r backend/requirements-migration.txt
-r requirements.txt
rethinkdb
```
Confirm `backend/Dockerfile` installs only `requirements.txt`.

- [ ] **Step 4: Run the full suite, both tiers**

Run:
```bash
python3 -m pytest backend/tests -q                       # FakeStore tier
scripts/dev_pg.sh up
PG_TEST_DSN="$(scripts/dev_pg.sh dsn)" python3 -m pytest backend/tests -q   # real PG tier
```
Expected: both PASS, and the FakeStore tier reports **at least** the `main` branch's passed count (475 at the time of writing). Record both numbers. A lower count means a test was dropped, not fixed.

- [ ] **Step 5: Run the residual-import gate one more time**

Run:
```bash
grep -rn "rethinkdb" backend --include='*.py' | grep -v "^backend/tests/"
grep -rn "rethinkdb" scripts --include='*.py' | grep -v "^scripts/archive_rethinkdb/" \
  | grep -v "^scripts/migrate_rethinkdb_to_postgres.py"
```
Expected: both empty. If not, the offending file was missed by Plan C — fix it here rather than opening the PR around it.

- [ ] **Step 6: `gitnexus_detect_changes` and commit**

```bash
GITNEXUS_MAX_FILE_SIZE=2048 npx gitnexus analyze     # graph_nexus_analysis.py is 1.7MB
gitnexus_detect_changes()
git add backend/requirements.txt backend/requirements-migration.txt \
        backend/tests/test_requirements_split.py
git commit -m "$(cat <<'EOF'
deps: psycopg in, rethinkdb out of the runtime image

psycopg[binary,pool]>=3.2.10,<4 — the floor is the notifies() memory-leak fix,
and watchers use notifies() only, never add_notify_handler(). rethinkdb moves to
backend/requirements-migration.txt, installed only in a throwaway venv on the
machine that runs the one-shot export.

Full suite: <N> passed with FakeStore, <N> passed against PG_TEST_DSN.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

- [ ] **Step 7: Open the PR (do NOT merge)**

```bash
git push -u origin feat/postgres-port
gh pr create --base main --head feat/postgres-port \
  --title "Replace RethinkDB with PostgreSQL 17 + JSONB" \
  --body "$(cat <<'EOF'
## What this is

RethinkDB out, PostgreSQL 17 + JSONB in, as the sole datastore. Tables stay
`(id, doc jsonb)` and keep their RethinkDB names 1:1. No relational redesign, no
ReQL shim, no new features, no strategy changes.

The user's invariant, binding on every decision here:

> Keep all functionality the same completely as using rethinkdb, just a
> different db.

## What landed

- **`backend/db/`** (Plan A) — `pool` / `json` / `merge` / `schema` / `store` /
  `watch` / `errors`, plus a `FakeStore` and a shared pytest fixture. No other
  module in the repo opens a connection.
- **The BacktestResults split** (Plan B) — one multi-MB document rewritten by
  three concurrent writers becomes `BacktestResults` (metadata) +
  `BacktestSteps` (insert-only) + `BacktestProgress` (the hot scalar row).
  `assemble()` reconstructs the legacy JSON byte for byte at every lifecycle
  stage, and that round trip is a gate, not a hope.
- **~1,000 call sites** (Plan C) ported mechanically across 68 files, in 12
  disjoint file groups. All 23 changefeeds move to `watch.py`, which re-reads on
  start and on every reconnect — a strict upgrade over the 8 sites that lose
  events today and the 3 with no reconnect at all.
- **Migration and ops** (Plan D) — a resumable streaming export with `--verify`
  (counts, canonical sha256, index and ordering parity, the 26-table
  fingerprint), `pg_retention.py`, a triaged `scripts/` tree, a compose
  `postgres` service, and a cutover runbook.

## Fidelity, in four testable claims

1. **Shape** — the same plain dicts, the same keys present and the same keys
   absent. `pluck` omits; it never emits null.
2. **Order** — `COLLATE "C"` everywhere. The
   `(latest_observation_date DESC, id DESC)` tiebreak decides membership of an
   80-row window that lands in an LLM prompt, and it is pinned by a real-Postgres
   test on scope-suffixed ids.
3. **Merge** — `jsonb_deep_merge`, never `||`, with a 10,000-case Hypothesis
   property test proving the Python and PL/pgSQL twins agree byte for byte.
4. **Notification** — all 23 watchers keep working, with a 2 s poll backstop
   under the notifications.

## Cutover prerequisites — none of this has run against production

- **Nothing in this branch touched production.** No production backtests, no
  cutover. Local testing only.
- The operator must set `POSTGRES_PASSWORD` in `.env`. Postgres binds
  `127.0.0.1` by default, unlike RethinkDB's `0.0.0.0`.
- `docs/runbooks/postgres-cutover.md` is the ordered procedure, with a stop
  condition at every step. It requires a **weekend freeze outside market hours**.
- **The gate is the user's:** one cold and one warm paired A/A under
  `scripts/run_paired_experiment.py`, both byte-identical with 100% traded-name
  overlap, plus an explicit check of the 80-id collation window.
- `alpaca-main` (Strategies doc 179, real money) restarts **last**, after every
  other instance has run a full clean weekly cycle on Postgres.
- The `rethinkdb` service and volume stay up and unreferenced. Rollback is
  unsetting `PG_DSN`. Decommissioning is a later, separate decision — and it
  needs an answer to "is anything backing RethinkDB up off-box today?"

## Known open items

- `default_toast_compression=lz4` availability in the PGDG `postgres:17` image is
  unverified; `dev_pg.sh up` and the runbook's pre-flight both print the verdict,
  and pglz is the fallback (costs disk, not correctness).
- Losing ReQL's 100k-array limit: `store.run()` raises above `PG_MAX_ROWS`
  (100,000) to preserve the loud failure; `store.iter()` is the explicit
  unbounded path.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```
**Do not merge.** Report the PR URL to the user.

---

## Self-review notes (resolved during writing)

- **Script counts.** The brief said "port the scripts referenced by docs/runbooks/crons/docker-compose". `docker-compose.yml` references **no** scripts and there is no crontab in the repo, so the triage rule falls back to spec §9's "referenced by docs, or named as a recurring operation". Task 1's test enforces that every ReQL script is classified, so a miscount in the tables is caught rather than shipped.
- **`scripts/` inventory.** 64 files in `scripts/`, 14 in `backend/scripts/`, of which **44** contain ReQL (35 + 9). Task 1's tables cover all 44.
- **`backend/scripts/`** is triaged here rather than in Plan C, because Plan C's residual-import gate exempts the archive directory and the migration script and would otherwise fail on files nobody owns.
- **`create_backtest_list_indices.py` / `create_clear_state_indices.py`** are deleted, not archived — spec §9 is explicit, and `test_script_triage.py` asserts they are absent from both the tree and the archive.
- **`docs/runbooks/`** contains two files today (`live-launch-checklist.md`, `point-in-time-capture.md`); `postgres-cutover.md` is the third. Neither existing runbook needs an edit — they reference scripts that are in the PORTED list.
