# Postgres cutover runbook

Move the datastore from RethinkDB to PostgreSQL 17. The system's behaviour does
not change; only where the documents live does.

Ordered. Each step has a stop condition. Do not proceed past one.

Nothing here runs from the branch's CI, and nothing here is reversible by
accident. The re-certification in step 7 is the gate: it is the user's, and it is
what decides whether `alpaca-main` ever restarts on Postgres.

Read the whole runbook before starting step 1. Budget a weekend.

---

## 1. Pre-flight

Backend redeployed from `feat/postgres-port` but **still pointed at RethinkDB**
(`PG_DSN` unset, `POSTGRES_*` present). The code is Postgres-capable and
RethinkDB-resident; that is the safe resting state.

Bring the database up on its own and inspect it:

    docker compose up -d postgres
    docker compose exec postgres psql -U intellistock -d IntelliStock \
      -c "SHOW default_toast_compression;"
    docker compose exec postgres psql -U intellistock -d IntelliStock \
      -c "SHOW shared_buffers;"
    docker compose exec postgres df -h /dev/shm      # expect 1.0G, not 64M
    docker stats --no-stream intellistock-postgres   # confirm the 4G limit
    df -h                                            # ~16 GB free, plus headroom

Record what RethinkDB holds, so step 4 has something to compare against:

    python3 scripts/migrate_rethinkdb_to_postgres.py --dry-run > preflight-counts.txt

**Stop if** the container does not come up, or if `default_toast_compression` is
not `lz4`. lz4 is a build-time option, and a `postgres:17` built without it
rejects the value outright — the failure looks like a container that will not
start, with `invalid value for parameter "default_toast_compression"` in its
logs, not like a missing feature. Drop `-c default_toast_compression=lz4` from
the `postgres` service's `command` in `docker-compose.yml`, recreate the
container, and repeat this step. pglz is the fallback and costs disk, not
correctness.

**Stop if** `/dev/shm` is 64 MB or `shared_buffers` is not `1GB`. The Docker
default `/dev/shm` is 64 MB and is *not* `shared_buffers`; parallel scans fail
against it with an error that names neither.

**Stop if** free disk is under about 24 GB. The import writes ~16 GB and the
RethinkDB volume stays.

## 2. Freeze

Stop every instance and every engine. Confirm zero running backtests and zero
running brokers:

    docker compose stop backend api price-service backtest-engine discord-bot
    docker ps --filter name=intellistock --format '{{.Names}}\t{{.Status}}'

Do this on a **weekend, outside market hours**. A partial export of a table that
is still being written is a silent corruption, not an error: the export pages by
primary key, so a row inserted behind the cursor is simply never seen and the
row counts still match.

**Stop if** any instance, engine, or ephemeral broker container is still
running. "It is only the price service" is how a table gets written mid-export.

## 3. Export/import

    python3 scripts/migrate_rethinkdb_to_postgres.py --batch 2000

Expect ~16 GB and roughly an hour, most of it `PriceHistory`.

The run is resumable. If it dies — OOM, a dropped connection, an impatient
Ctrl-C — rerun the same command; it continues from `_migration_state.last_id`
per table rather than starting over. Rerunning after a *successful* run is a
no-op for the same reason.

To move one table at a time, or to redo one:

    python3 scripts/migrate_rethinkdb_to_postgres.py --tables PriceHistory --batch 2000
    python3 scripts/migrate_rethinkdb_to_postgres.py --tables PriceHistory --since-id '<id>'

**Stop if** the exit code is non-zero. Read the error; do not rerun blind. A
resumable script that is rerun through a real fault just reproduces the fault
further along.

## 4. Verify

Row counts alone do not prove a document survived. The verifier compares
canonical sha256 hashes, and it refuses to summarise a mismatch.

Hash **every** row of the tables the frozen-state contract reads — those are the
tables whose contents decide whether two backtests are comparable at all:

    python3 scripts/migrate_rethinkdb_to_postgres.py --verify \
      --verify-sample 1.0 --tables "$(python3 -c '
    import sys; sys.path.insert(0, "backend")
    from frozen_paired_state import _ALLOWED_STATE_TABLES
    print(",".join(sorted(_ALLOWED_STATE_TABLES)))')"

Then sample the rest:

    python3 scripts/migrate_rethinkdb_to_postgres.py --verify --verify-sample 0.05

Budget an hour for the sampled pass over 16 GB.

**Stop on any mismatch.** Mismatches are written whole — both documents, in
full — to `.migration-mismatches/<table>/<id>.json`. Read them. Do not summarise
them, and do not decide from a diff summary that a difference is cosmetic: the
whole port turns on documents surviving byte-for-byte, and the one field you
skim past is the one that changes a trade.

## 5. Flip

    # set PG_DSN in .env  (or leave it unset and rely on the POSTGRES_* parts)
    docker compose up -d --force-recreate \
      backend api price-service backtest-engine discord-bot
    docker compose ps

The RethinkDB container stays up, unreferenced. Every `RETHINKDB_*` variable
stays in `.env` and in `docker-compose.yml`. That is not tidiness — step 8 is
"unset `PG_DSN`", and that only works while they are still there.

**Stop if** `docker compose ps` shows `postgres` as anything other than
`healthy`, or if any recreated service is restarting.

## 6. Smoke

Each of these must pass before step 7. None of them is a formality.

- The backtest list page renders, with the same rows in the same order as
  before the freeze.
- A backtest starts, progresses (watch `BacktestProgress` move), and stops on
  request.
- An instance start/stop round-trips, and the spawned container reaches the
  database — a broker that inherited no `POSTGRES_HOST` dials its own loopback
  and finds nothing.
- `clear-state` on a **scratch** instance reports a **non-zero** `would_delete`
  for the scoped tables. This is the `clear_instance_state.py` regression of
  2026-05-25: exact-only prefix matching found zero scoped rows — ids are
  namespaced `"<instance>|<config-hash>"`, not bare — and turned a full clear
  into a silent no-op that reported success.
- `python3 scripts/pg_retention.py --dry-run` runs and reports. Retention is OFF
  by default (`RetentionSpec.default_days` is `None` unless the table sets one),
  so a dry run that proposes deleting nothing is the correct answer here, not a
  failure.

**Stop if** `would_delete` is zero for a table that has scoped rows.

## 7. Re-certify (the user's gate)

Two paired A/A runs — one **cold**, one **warm** — on a scratch instance, never
on `alpaca-main`:

    python3 scripts/run_paired_experiment.py --instance <scratch> --doc <doc> \
      --start <YYYY-MM-DD> --end <YYYY-MM-DD> --cash 6000 --granularity 900

`--granularity` is **seconds**; 900 is the 15-minute cadence every prior
certification used. The default is 3600. Add `--warmup-start <DATE>` for the
warm arm and `--snapshot <PATH>` to freeze state; leave `--control` and
`--treatment` empty, because an A/A is the point.

Both runs must come back **byte-identical with 100% traded-name overlap** — the
same bar bt 479057 / 193668 cleared. Anything less is not "close enough": the
A/A noise floor after cold-start clearing is 0.5pp, so a difference here is a
difference the port introduced.

Then check the collation window explicitly. `graph_nexus_analysis.py` orders
`GraphNexusTradeOutcomes` by `(latest_observation_date DESC, id DESC)` and takes
the first 80. By mid-window most rows share one `latest_observation_date`, so
`id` alone decides *membership* — and those 80 rows land in an LLM prompt.

    docker compose exec -T postgres psql -U intellistock -d IntelliStock <<'SQL'
    SELECT id FROM "GraphNexusTradeOutcomes"
     WHERE doc->>'instance_id' = '<scoped-instance-id>'
       AND doc->>'entry_date' < '<as-of-date>'
     ORDER BY doc->>'latest_observation_date' COLLATE "C" DESC,
              id COLLATE "C" DESC
     LIMIT 80;
    SQL

The scoped instance id is `"<instance>|<config-hash>"`, not the bare instance
name. Compare against the same 80 ids read from RethinkDB before the freeze. A
non-bytewise collation reorders `"AACI"` against `"aaci"` and changes which five
analogs reach the prompt, silently, with no error anywhere. This is the single
most likely silent failure in the migration.

**Stop if** either A/A is not byte-identical, or the 80-id window differs in
membership or order.

## 8. Rollback

    # unset PG_DSN in .env
    docker compose up -d --force-recreate \
      backend api price-service backtest-engine discord-bot

RethinkDB is untouched and still authoritative for everything written before the
freeze.

Everything written after the flip is **lost** — Postgres keeps it, but nothing
reads Postgres once `PG_DSN` is unset, and there is no reverse migration. That
is precisely why step 7 runs before any real-money instance restarts: the window
in which rollback is cheap is the window in which nothing important has been
written yet.

**Stop if** a live instance has traded since the flip. Rolling back then does
not restore a consistent world: the broker's positions are real and the ledger
that recorded them is the one you are about to stop reading. Reconcile the
account against RethinkDB by hand first, or do not roll back.

## 9. Decommission (a later, separate decision)

Stop the RethinkDB container, keep `rethinkdb_data` for 30 days, then drop it.
Only after that should the `rethinkdb` service and the `RETHINKDB_*` variables
leave `docker-compose.yml`.

**Stop and answer first:** is anything backing RethinkDB up off-box today? If
not, that volume is the only rollback path in existence and the 30 days are
load-bearing rather than ceremonial.

---

## The order instances come back

`alpaca-main` (Strategies doc 179, real money) is **restarted last** — after
every other instance has run a full clean weekly cycle on Postgres, and after
step 7 passed. Editing doc 179 is a real-money change; restarting its instance
onto a fresh datastore is a larger one.

## Local rehearsal

Steps 3, 4 and 6 can be rehearsed against a throwaway cluster, no Docker and no
root:

    ./scripts/dev_pg.sh up          # prints PG_TEST_DSN and the lz4 verdict
    export PG_TEST_DSN="$(./scripts/dev_pg.sh dsn)"
    ./scripts/dev_pg.sh psql
    ./scripts/dev_pg.sh down        # or: nuke, to delete .devpg/ entirely

A rehearsal proves the commands and the flags. It does not prove the data, which
is what step 4 is for.
