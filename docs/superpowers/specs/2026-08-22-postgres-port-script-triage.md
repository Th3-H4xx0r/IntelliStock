# Postgres port — scripts triage

43 git-tracked `.py` files under `scripts/` and `backend/scripts/` speak ReQL
(they match `rethinkdb` or `r.db(`). Porting them is 43 diffs; deciding which ones deserve a
diff is one document. This is that document, and
`backend/tests/test_script_triage.py` fails the build if it ever stops matching
the tree.

**Rule** (spec §9): a script is **PORTED** if a runbook, a cron, `docs/`, or CI
references it, *or* if its name says it is a recurring operation
(`create_*_indices`, `purge_*`, `check_*`, `diag_*`, `run_paired_experiment`,
`clear_backtest_state`, `pg_retention`). Everything else is **ARCHIVED**
verbatim.

Two buckets were not in the rule and reality required them:

- **RETAINED** — left exactly where it is, still speaking ReQL, deliberately.
  Either the file's *job* is to read RethinkDB (the migration script, the
  fixture puller), or moving it would break a live test for no gain.
- **DELETED** — subsumed by `db.schema.ensure_schema()`.

Counts: **22 PORTED, 14 ARCHIVED, 5 RETAINED, 2 DELETED** = 43. The census counts
what **git tracks**, not what happens to sit in the working directory. The PORTED table
carries one extra row (`run_paired_experiment.py`) that has no ReQL at all but is
the cutover's re-certification gate, so task 6 owns it.

"ReQL sites" is `grep -c 'r\.db('`. A zero means the file names `rethinkdb` —
usually a lazy `from rethinkdb import RethinkDB` inside a `_connect()` — without
a literal `r.db(` call.

## PORTED

Task 6 ports exactly this list and nothing else.

| script | ReQL sites | why |
|---|---|---|
| `scripts/validate_live_launch_readiness.py` | 4 | referenced by `docs/runbooks/live-launch-checklist.md` |
| `scripts/clear_main_instance_lookback_state.py` | 3 | referenced by `docs/runbooks/live-launch-checklist.md` |
| `scripts/run_paired_experiment.py` | 0 (HTTP only) | the A/A re-certification harness — the cutover gate, runbook step 7 |
| `scripts/clear_backtest_state.py` | 8 | named recurring operation; the widest ReQL surface in `scripts/` |
| `scripts/inspect_broker_state.py` | 0 | recurring diagnostic, 16 doc mentions |
| `scripts/check_range_position.py` | 1 | `check_*` recurring |
| `scripts/diag_alpaca_open.py` | 3 | `diag_*` recurring |
| `scripts/purge_bad_discovered_tickers.py` | 3 | `purge_*` recurring |
| `scripts/purge_overlay_bars_cache.py` | 2 | `purge_*` recurring |
| `scripts/reset_backtest_event_state.py` | 4 | recurring reset, 14 doc mentions |
| `scripts/snapshot_instance_state.py` | 3 | the paired-experiment flow reads its snapshots |
| `scripts/attest_arm_start.py` | 2 | the paired-experiment flow |
| `scripts/backfill_learning_observations.py` | 2 | recurring backfill, 8 doc mentions |
| `scripts/benchmark_window.py` | 1 | recurring benchmark, 7 doc mentions |
| `scripts/encrypt_brokerage_credentials.py` | 2 | credential rotation, recurring |
| `scripts/migrate_external_position.py` | 0 | recurring position fixup, 15 doc mentions |
| `scripts/migrate_llm_cache_to_canonical.py` | 4 | recurring cache maintenance, 8 doc mentions |
| `backend/scripts/audit_point_in_time_coverage.py` | 3 | referenced by `docs/runbooks/point-in-time-capture.md` |
| `backend/scripts/purge_backtest_secrets.py` | 1 | `purge_*` recurring; the only `r.literal({})` site in the tree |
| `backend/scripts/verify_inactive_deployment.py` | 2 | deploy check, 6 doc mentions |
| `backend/scripts/migrate_alpha_tables.py` | 6 | schema operation, 5 doc mentions |
| `backend/scripts/rerun_backtest.py` | 1 | recurring operation |
| `backend/scripts/run_alpha_research.py` | 0 | the production research CLI — `backend/tests/test_task6_review_remediation.py::test_production_research_cli_registers_immutable_specs_before_execution` runs its `main()` |

## ARCHIVED

Moved verbatim (`git mv`, no edits) to `scripts/archive_rethinkdb/`. The paths
below are where each file *was*; it is now that basename under the archive
directory. See `scripts/archive_rethinkdb/README.md`.

| script | ReQL sites | why |
|---|---|---|
| `scripts/apply_doc179_bull_participation_levers.py` | 3 | dated one-shot config patch |
| `scripts/apply_doc179_cash_reserve_floor_raise.py` | 0 | dated one-shot config patch |
| `scripts/apply_doc179_config_patch.py` | 3 | dated one-shot config patch |
| `scripts/apply_doc179_rotation_override_fix.py` | 3 | dated one-shot config patch |
| `scripts/apply_doc179_round3_ab_levers.py` | 3 | dated one-shot config patch |
| `scripts/apply_doc179_winner_depth_fix.py` | 3 | dated one-shot config patch |
| `scripts/apply_doc193_backtest_sell_proceeds_credit.py` | 0 | dated one-shot config patch |
| `scripts/apply_doc193_concentrate_position_sizing.py` | 0 | dated one-shot config patch |
| `scripts/apply_doc193_core_funding_mpg_aware.py` | 0 | dated one-shot config patch |
| `scripts/apply_doc193_core_satellite_reweight.py` | 0 | dated one-shot config patch |
| `scripts/apply_doc193_live_parity.py` | 0 | dated one-shot config patch |
| `scripts/apply_doc193_swap_sleeve_exclusion.py` | 0 | dated one-shot config patch |
| `scripts/apply_main_clean_room_config.py` | 0 | one-shot clean-room config apply |
| `scripts/clear_main_recent_sell_block.py` | 0 | one-shot fixup for a single stuck instance |

## RETAINED

Not ported, not moved. Each row says why the file keeps speaking ReQL from where
it sits.

| script | ReQL sites | why |
|---|---|---|
| `scripts/migrate_rethinkdb_to_postgres.py` | n/a | *is* the migration. The only file in the tree allowed to import `rethinkdb`, lazily, inside the function that needs it (spec §7). Written on a sibling branch; the row is here so the triage test does not flag it on merge. |
| `scripts/dev_fetch_backtest_fixture.py` | 1 | the fixture puller. Its job is to read one `BacktestResults` document per lifecycle stage out of live RethinkDB, read-only, and write the gzipped fixtures the migration tests replay. Porting it to `db.store` would make it read the database it exists to populate. |
| `backend/scripts/apply_round2_2026_07.py` | 2 | dated one-shot, but `backend/tests/test_apply_round2_2026_07.py` imports it as `scripts.apply_round2_2026_07` and it bootstraps `sys.path` from `dirname(dirname(__file__))`. Moving it one directory deeper breaks both. Its ReQL is a lazy `_connect()` no test reaches. |
| `backend/scripts/apply_tune_2026_07.py` | 2 | same shape; `backend/tests/test_apply_tune_2026_07.py` |
| `backend/scripts/fix_doc179_hygiene.py` | 3 | same shape; `backend/tests/test_fix_doc179_hygiene.py` — which exercises `apply_updates` against a fake in-process rdb object, so the ReQL *is* the thing under test |
| `backend/scripts/migrate_encrypted_credentials.py` | 2 | same shape; `backend/tests/test_credential_audit.py` asserts `main(["--apply"])` exits 2 before any database access — a live security guard |

The four `backend/scripts/` rows are dead weight, not live code: nothing calls
them, and their `_connect()` will not run again. Deleting them, with their tests,
is a defensible follow-up. It is a deletion decision, not a port decision, so it
is not made here.

## DELETED

`scripts/create_backtest_list_indices.py` and `scripts/create_clear_state_indices.py`
are deleted rather than ported or archived. `db.schema.ensure_schema()` creates
every index they created, idempotently, at process boot (spec §9), so keeping a
hand-rolled index script is an invitation to create a second, divergent set.

One dangling reference is left deliberately: the comment at
`backend/interactive_utils.py:5203` still names `create_backtest_list_indices.py`
as the origin of the `list_ts` / `status_norm` / `instance_ts` indexes. That
block is unported ReQL that plan C rewrites wholesale, so editing the comment now
would only collide with that rewrite. Whoever ports it points the comment at
`backend/db/schema.py`.

## Out of scope: gitignored scratch

`.gitignore` has excluded `scripts/_*.py` since long before this port —
"Diagnostic / one-off scripts (underscore-prefixed by convention)". Files matching
it are not in the repository, so they are not triaged, not ported and not
archived. An ignored file cannot be `git mv`'d into `scripts/archive_rethinkdb/`
without committing exactly what the ignore rule exists to keep out.

This matters because the census used to walk the filesystem. On a checkout that
happened to hold `scripts/_kalshi_analyze.py`, `scripts/_kalshi_recon.py` and
`scripts/_kalshi_recreate_instance.py` — untracked scratch from 2026-06-28 — the
build failed demanding they be classified, while the same commit passed on a
checkout without them. The census now asks `git ls-files`, so the answer is a
property of the commit rather than of the developer's directory, and
`test_gitignored_scratch_scripts_are_out_of_scope_by_construction` pins it: it
drops a scratch file with a `rethinkdb` import into `scripts/` and asserts the
triage stays green.

An earlier draft listed those three `_kalshi_*` files as ARCHIVED. That was wrong
twice over — they were never repository files, and the move it prescribed was
impossible.

## How to regenerate

```bash
for f in $(grep -rl "rethinkdb\|r\.db(" scripts backend/scripts --include='*.py' | sort); do
  printf '%s|%s\n' "$(grep -c 'r\.db(' "$f")" "$f"
done
```

That is the filesystem view. The test uses `git ls-files` instead, so ignored
scratch never enters the set.

`backend/tests/test_script_triage.py` fails if that set stops being covered by
PORTED ∪ RETAINED, if any bucket claims a script twice, or if an ARCHIVED row is
not actually under `scripts/archive_rethinkdb/`.
