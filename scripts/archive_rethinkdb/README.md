# Archived RethinkDB scripts

These ran once against RethinkDB. They are kept for provenance: what was changed,
when, and with what reasoning. The docstring at the top of each one is usually
the best surviving record of a config decision.

They **do not run against Postgres**. Each imports `rethinkdb`, opens its own
connection, and speaks ReQL, none of which exists in the runtime any more.

They also no longer run against RethinkDB. Every one of them bootstraps its
repo root as `dirname(dirname(__file__))`, which resolved to the repository when
the file sat in `scripts/` and resolves to `scripts/` now that it sits one level
deeper. Backups and sibling JSON files they reference are still in `scripts/`.
Reviving one means fixing that path too — which is the point: nothing in this
directory should start working by accident.

Porting one is not mechanical — read it first, understand what it did, decide
whether it should ever run again, then port it deliberately against
`backend/db/store`. Do not batch-convert this directory.

The split that put each file here is
`docs/superpowers/specs/2026-08-22-postgres-port-script-triage.md`, and
`backend/tests/test_script_triage.py` keeps it honest.

## Not archived, deleted

`create_backtest_list_indices.py` and `create_clear_state_indices.py` were
deleted rather than moved here. `db.schema.ensure_schema()` creates every index
they created, idempotently, at process boot; keeping a hand-rolled index script
alongside it would eventually produce two divergent sets of indexes.

## Not archived, retained in place

Four dated one-shots stayed in `backend/scripts/` even though they belong here:
`apply_round2_2026_07.py`, `apply_tune_2026_07.py`, `fix_doc179_hygiene.py`, and
`migrate_encrypted_credentials.py`. Live tests import them as `scripts.<name>`
and exercise their pure config-building logic, so moving them would delete
working test coverage to tidy a directory. The triage document explains the
call.

## Not archived, out of scope

`.gitignore` excludes `scripts/_*.py` — one-off diagnostics, underscore-prefixed
by convention. Those files are not in the repository, so nothing here applies to
them: an ignored file cannot be moved into this directory without committing the
very thing the ignore rule keeps out. The triage census asks `git ls-files`, not
the filesystem, for exactly that reason.
