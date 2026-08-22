<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **IntelliStock** (58888 symbols, 107351 relationships, 155 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `GITNEXUS_MAX_FILE_SIZE=2048 npx gitnexus analyze` in terminal first.
> ⚠️ The env var is REQUIRED: without it the indexer silently skips files >512KB — including `backend/strategies/graph_nexus_analysis.py` (~1.7MB, 4,205 symbols), leaving the graph blind to the largest file in the repo.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/IntelliStock/context` | Codebase overview, check index freshness |
| `gitnexus://repo/IntelliStock/clusters` | All functional areas |
| `gitnexus://repo/IntelliStock/processes` | All execution flows |
| `gitnexus://repo/IntelliStock/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## Datastore

PostgreSQL 17 + JSONB, through `backend/db/`. **No module outside `backend/db/`
opens a connection**, and nothing outside `scripts/migrate_rethinkdb_to_postgres.py`
imports `rethinkdb` (`scripts/dev_fetch_backtest_fixture.py` is the one
read-only exception, and it imports lazily).

- Reads and writes: `from db import store` — `store.get / get_all / insert /
  update / delete / between / filter / order_by / limit / count / run / iter`.
- `store.update()` **deep-merges** (objects merge, arrays replace), matching
  ReQL. Never write `||`.
- `store.between(lo, hi)` is `[lo, hi)`, matching ReQL. Never write SQL
  `BETWEEN`, which is inclusive at both ends.
- `store.run()` raises above `PG_MAX_ROWS` (100k). Use `store.iter()` for the
  unbounded path.
- Change notification: `from db import watch` — `watch_row` / `watch_table` /
  `watch_filter`. Watchers re-read on start and on every reconnect.
- Every ordered read is `COLLATE "C"` (bytewise), because RethinkDB ordered
  bytewise and one 80-row window decides what reaches an LLM prompt. No GIN
  indexes anywhere.
- DDL lives in `backend/db/schema.py` — table registry, partitioning, retention.
  Do not write `CREATE INDEX` at a call site.
- Local test database: `scripts/dev_pg.sh up`, then export the `PG_TEST_DSN` it
  prints. Without it the suite runs against the in-process `FakeStore`.
- Which `scripts/` still speak ReQL, and which will never be ported:
  `docs/superpowers/specs/2026-08-22-postgres-port-script-triage.md`.
- Cutover: `docs/runbooks/postgres-cutover.md`.

RethinkDB is retained — service, volume, and every `RETHINKDB_*` variable —
until the store is decommissioned. The flip is setting `PG_DSN`; the rollback is
unsetting it, and that only works while the old configuration is still there.
