# RethinkDB replacement — evaluation and migration plan

2026-08-22. Trigger: operator asked whether to replace RethinkDB 2.4.4, and with what.

Evidence base: four research reports (repo inventory + live DB read, PostgreSQL/JSONB,
MongoDB/FerretDB/CouchDB, hybrid-and-dismissals), all dated 2026-08-21/22. `file:line`
citations are from the repo inventory and were verified against the live DB at
`100.95.106.23:28015` (read-only). URLs are in §9.

---

## 1. TL;DR

1. **Winner: PostgreSQL 17 + JSONB.** Runner-up MongoDB 8.0 — the only other candidate that
   clears every hard requirement. Everything else is disqualified on change notification,
   document size, or vendor health.
2. **Do today, no migration, ~1 engineer-day:** (a) the RethinkDB container **already** passes
   `--cache-size 2048` (`docker-compose.yml:344`) — that recommendation is already satisfied,
   so the remaining memory lever is the **125-table × 8 MB fixed overhead (~1.0 GB)** against
   a 5 GB container limit, which only table consolidation or TTL touches; (b) add `squash=N`
   and point-feeds/field-projection to the 23 live changefeeds — only 2 of 23 do this today.
3. **The forcing function is not memory. It is the Python driver.** `rethinkdb`
   **2.4.10.post1**, PyPI 2023-12-10, **no 3.13 classifier**, asyncio path broken on 3.11+
   with no official fix, one commit in 2.5 years. Server 2.4.4 is 32 months old and
   **CVE-2026-24810 (buffer overflow, through 2.4.4) has no fixed release.** Budget the
   migration on the CPython-upgrade timeline, not the memory-pain timeline.
4. **Phase 0 is independently worth doing regardless of the decision**: split
   `BacktestResults` into a metadata row + insert-only `backtest_steps`, and fix the three
   incremental writers. It is O(n²) today on every store.
5. **Total migration: ~36–62 engineer-days** across five phases, RethinkDB authoritative
   through Phase 3, rollback = config flip until Phase 4.

---

## 2. Measured facts this rests on

Live server: `rethinkdb 2.4.4~0bookworm`, single node `3ae20aa09ce4_btb`, pid 1,
`cache_size_mb = 2048`, up since 2026-08-07. DB `IntelliStock`, **125 tables, 15.97 GB**.

### 2a. Disk, not row count, names the elephant

| table | rows | disk | retention today |
|---|---|---|---|
| **GraphNexusTradeContexts** | 504,742 | **7,382 MB — 46% of the DB** | none (per-instance clear only) |
| BacktestResults | 1,429 | 1,848 MB | manual delete |
| AlpacaBarsCache | 81,589 | 1,116 MB | none |
| **PriceHistory** | 2,852,289 | **814 MB (4th)** | none |
| GraphNexusLLMPromptCache | 381,530 | 694 MB | none |
| GraphNexusNewsLLMMacro | 104,923 | 638 MB | none |
| LLMUsage | 294,964 | 575 MB | none |
| GraphNexusNewsLLMCompany | 68,462 | 436 MB | none |
| GraphNexusOutcomeSeries | 289,738 | 319 MB | per-instance clear |
| LearningObservations | 10,351 | 29 MB | **the only real retention policy in the repo** |

`GraphNexusTradeContexts` is 9× PriceHistory by disk at 53 KB/row. Every prior handoff framed
PriceHistory as the memory driver; by disk it is fourth. Only `backend/self_learning/retention.py`
implements expiry, and only for `LearningObservations`.

Derived: RethinkDB's documented **8 MB per table per server** floor × 125 tables ≈ **1.0 GB
unevictable**, plus the documented "~1% of dataset" resident metadata (~160 MB at 16 GB), on
top of a 2 GB cache inside a 5 GB container limit (`docker-compose.yml:357-360`). That is the
wedge mechanism, and `--cache-size` does not bound it.

### 2b. BacktestResults is written incrementally by three concurrent writers

Row created as a stub with `conflict='replace'` (`backend/engines/backtest_engine.py:927-966`,
mirrored `broker.py:12120,12133`). Then:

| writer | cadence | payload |
|---|---|---|
| heartbeat thread (`broker.py:12173-12193`) | **every 15 s** | `_last_active`, `time_elapsed_seconds`, **the full last-500 log list re-sent every time** (`:12191`) |
| progress writer (`broker.py:17802-17866`) | **every +2%** | rewrites in full: `backtest_trades` (last 1000), `portfolio_value_history` (downsampled 3000), `logs` (last 500), and **`backtest_decisions` + `backtest_refusals` in their entirety** (`:17863-17865`) |
| terminal write (`broker.py:12933/12936`) | once | full result + `evidence` + `strategy_schema` |

Sampled live row `id=460555` (still running): **3,125,934 bytes**, of which `backtest_decisions`
= 2,779,041 (89%, 4,322 entries). `interactive_utils.py:5296-5300` documents the general case
as **5–13 MB**, 7–15k decisions, 9–37k prices.

`pluck` does not help: RethinkDB materialises the whole document server-side
(`scripts/create_backtest_list_indices.py:6-11` records **12.2 s to render one list page over
1,426 rows, 2026-08-21**). Detail/playback endpoints pull the full doc with no pluck at all
(`interactive_utils.py:1440, 5827, 6039, 6085, 6316, 6841, 6875`).

**This is O(n²) in step count on any store**, and it is the single largest write load in the system.

### 2c. Changefeeds are not a blocker — they are a correctness liability

**23 live runtime changefeeds in 11 files** (§3 corrects the "25" figure).

- **0 of 23 need event-stream semantics.** Every one is a control-doc watch (current state
  suffices) or a work-queue wake-up. `price_engine.py:150` looks like a fan-out but watches
  `LivePricesStocks` — the *watchlist*, not the tick stream. **No changefeed anywhere fans out
  market data.**
- **8 currently LOSE events on reconnect** — `run_reconnecting_changefeed`
  (`backend/rethink_changefeed.py:63-155`) reopens the feed but never re-reads current state.
- **2 have no reconnect at all**: `broker.py:5833` (backtest stop/pause watcher — a drop leaves
  a running backtest unstoppable) and `engines/price_engine.py:71/92/150`.
- The reference implementation exists in-repo: `engines/backtest_engine.py:861` re-sweeps
  pending rows on **every** reconnect (`:917-919`). `self_learning_engine.py:531` uses
  `include_initial=True` + a persisted watermark.
- Latency tolerance is seconds everywhere. Existing recovery is already 2–30 s
  (`rethink_changefeed.py:120-126`) or a fixed 5 s. **A 1–5 s poll is strictly better than
  today's behavior at every site.**
- `broker.py:5730-5732` and `:5786` diff `old_val` vs `new_val` — a NOTIFY replacement must
  carry both or the handler must cache the prior doc.

### 2d. The ReQL surface is shallow and there is no abstraction layer

- **~1,119 conn-scoped `.run()` call sites** (1,004 backend + 115 scripts; 1,114 excluding tests).
- **~96–97% simple**: `get(pk)`, `insert(dict)`, `get(pk).update(dict)`, `delete()`,
  `get_all(v, index=)`, `between(lo,hi,index=)`, `filter({dict})`.
- **~35–45 complex sites, individually enumerable** — `interactive_utils.py:5232-5236`
  (the only `merge`+server-side-lambda), `:5287-5292`, `:7170/7231/7278` (server-side
  `split("|").nth(0)`), `clear_instance_state.py:311-372` (`_build_filter` prefix trees),
  `graph_nexus_analysis.py:11846-11858, 12363, 12405, 12415, 26313-26431`.
- **Zero `eq_join`, zero ReQL `group`/`reduce`, zero `r.js`, zero `r.do`, zero `return_changes`,
  zero `r.uuid`.** No server-side join or aggregation exists anywhere; all joins are Python-side
  dict lookups (`interactive_utils.py:6805-6820`).
- **16 independent `get_conn()` defs, no pool, no retry wrapper, no `db.py`** —
  `instance.py:194`, `server.py:105`, `priceBroker.py:35`, `auth_utils.py:51`,
  `interactive_utils.py:30`, `kalshi/db.py:55`, `broker.py:1891/1931`,
  `self_learning/store.py:126`, `engines/{backtest,discover,price}_engine.py:322/27/33`, plus
  test stubs. Plus `llm_utils.py:5773` opening a fresh connection **per prompt-cache operation**.
- **The one existing seam**: `backend/backtest_replay.py:692 InMemoryReplayStore` /
  `:853 RethinkReplayStore` — a genuine swap-in interface (`insert_record`, `update_row`,
  `compare_and_swap_state`) already consumed by `experiment_registry.py:587`,
  `live_risk_state.py:589`, `nexus_runtime_state.py:98`. Widen this, do not invent a new one.

### 2e. Hard-to-port semantics, counted

| feature | sites | why it hurts |
|---|---|---|
| partial-merge `update()` | **130 backend / 19 scripts** | recursive nested merge; no target store reproduces it by default |
| `conflict=` upsert | 142 / 4 | `replace` vs `update` map to different SQL |
| `r.now()` | 34 / 0 | server-side clock returning native time objects |
| `r.literal` | 3 | `purge_backtest_secrets.py:101-103` — the codebase has already been burned by merge semantics once |
| `between(..., "￿", right_bound="closed")` | `clear_instance_state.py:327-333` | prefix-scan emulation |
| `durability=` | 2 real | `broker.py:8039` hard, `:8423` soft |

### 2f. Determinism is the binding constraint

`scripts/run_paired_experiment.py:20-22` defines the protocol (clear → attest COLD → apply arm
→ run, twice, both cold-states identical). `backend/frozen_paired_state.py:39-52`
`_ALLOWED_STATE_TABLES` (**26 tables**) is the authoritative "must reproduce byte-identically"
manifest; `paired_state_attest.py:47-77` is the fingerprint subset with `_VOLATILE_FIELDS`
excluded and `_MAX_INLINE_STRING = 512`.

The one live ordering hazard is already found and fixed —
`graph_nexus_analysis.py:11843-11858` orders by `(latest_observation_date DESC, id DESC)` and
`.limit(80)` makes that order decide **membership**, which lands in the LLM overlay prompt.
`id` is a string PK. **Any target store with a non-bytewise default collation silently changes
the 80-row window, therefore the prompt bytes, therefore the results.** Postgres default
locale collation is not bytewise; `COLLATE "C"` is mandatory, not optional.

---

## 3. Handoff contradictions and corrections

| prior claim | verdict | correction |
|---|---|---|
| "25 changefeeds in 12 files" | ⚠️ grep-true, **wrong operationally** | `rethink_changefeed.py:84` is a docstring; `engine_control.py:159 engine_changes()` is a dead helper with zero callers. **23 live feeds in 11 files.** |
| "100k array-limit gotcha" | ❌ **not found in the repo** | No `array_limit=`, no `"Array over size limit"`, no relevant `100000`. **Nothing in the codebase defends against it** — yet `backtest_decisions` hits 4,322 entries on a *running* run and `interactive_utils.py:5296` documents 9–37k `backtest_prices`. **Latent and undefended.** The real size constraint the code fights is a **16 MB manifest cap** (`paired_state_attest.py:64-68`) and `WRITE_CHUNK = 500` (`self_learning/store.py:63-64`). |
| "597-key strategy config" | ⚠️ stale | Live doc 179 (`alpaca-main`, real money) = **758 deep keys / 28,462 bytes / 4 top-level keys**. Largest Strategies doc is id 180 at 963 keys / 31.8 KB. Right order of magnitude, wrong number. |
| "PriceHistory is the elephant / drives restarts" | ⚠️ **framing stale** | Sourced in 5 places (`self_learning/store.py:10-12`, `retention.py:3-4`, `outcomes.py:45`, `tests/test_pit_storage_efficiency.py:5`, the 2026-08-15 design doc) citing "2.3M rows → 17 restarts in 12 days". Rows confirmed and grown to 2.85M — but **814 MB vs GraphNexusTradeContexts' 7.4 GB.** Do not aim the fix at PriceHistory. |
| "1,080 ReQL call sites" | ✅ accurate within 4% | ~1,119 conn-scoped. |
| "connections not thread-safe" | ✅ confirmed, load-bearing | verbatim in 4 docstrings (`instance.py:195`, `server.py:106`, `priceBroker.py:36`, `kalshi/backtest_worker.py:205`) + `server.py:1849`. This is *why* there are 16 `get_conn` defs. |
| "set `--cache-size` explicitly — highest-value change available" (report 04) | ❌ **already done** | `docker-compose.yml:344`: `rethinkdb --bind all -d /data --cache-size 2048`, with `mem_limit 5G` at `:357-360`. My correction to the input report. |
| LLM-COST-2026-08-22 implication for `GraphNexusLLMPromptCache` | ✅ growth **slows**, shape unchanged | f68af81 fixed the prompt-byte busters; warm A/A went 1,605 → 275 paid calls (83% cached). Fewer distinct prompts ⇒ fewer new 48-hex rows. The row schema `{id, prompt_hash, prompt_len, model, effort, response, cached_at, hit_count}` is unaffected. `cache_read_input_tokens = 0` on all 294,964 `LLMUsage` rows ⇒ **this table does 100% of the caching work and will keep doing so.** It stays a hot exact-match KV by 48-hex hash — the most trivially portable table in the DB. |

---

## 4. Per-candidate evaluation

### Hard requirements (pass/fail)

H1 documents ≥13 MB · H2 partial/nested update without full rewrite · H3 push or ≤5 s
change notification for 23 watchers · H4 native or cheap TTL/retention · H5 maintained Python
client on 3.13+ · H6 5-year support horizon · H7 multi-process concurrent writes without
serialising the live order path.

### 4.1 PostgreSQL 17 + JSONB

H1 ✅ 1 GB field limit (TOAST). H2 ⚠️ needs a hand-written `jsonb_deep_merge` (§5). H3 ✅
LISTEN/NOTIFY + re-read, poll backstop. H4 ✅ partition drop. H5 ✅ psycopg 3.3.4 (2026-05-01),
Python ≥3.10. H6 ✅ PG17 EOL 2029-11-08, JSONB in core since 9.4 (2014). H7 ✅ MVCC.

Costs, all quantified:
- `SELECT doc->>'status'` **fully detoasts** a 13 MB jsonb. No jsonb TOAST slicing exists in any
  released PG including 18. Measured: 40 B jsonb <10 ms vs 40 KB jsonb **500 ms** on the same
  10k rows; generated column + B-tree → 5 ms (~100× swing).
- Incremental update of a growing 13 MB doc ≈ **8 GB of WAL and ~4M dead TOAST chunk rows per
  backtest**, O(n²). Against a `backtest_steps` child table: ~10 MB, O(n). **Phase 0 is not
  optional here.**
- GIN is the wrong index: `WHERE doc->>'k' = v` **cannot use GIN** (47.9 ms seq scan vs 0.1 ms
  B-tree), and `fastupdate=on` produces unpredictable multi-hundred-ms insert stalls. Use
  **STORED generated columns + B-tree**; on PG18 `VIRTUAL` is the new default and cannot be
  indexed, so write `STORED` explicitly (a reason to pick 17).
- Memory: `shared_buffers` is a fixed start-time allocation. **Table size cannot exhaust
  memory** — the structural fix for the wedge. OOM risk moves to `work_mem` multiplication and
  per-backend detoast (20 concurrent 13 MB reads ≈ 260 MB private), both bounded by config.

### 4.2 MongoDB 8.0 Community — runner-up, clears every hard req

H1 ⚠️ **16 MiB hard cap against 13 MB docs** — ~3 MB headroom, and the doc grows during the run.
`BSONObjectTooLarge` (10334) rejects atomically. H2 ⚠️ `$set` with a nested object **replaces
the entire embedded document, removing all other fields** — the exact inverse of ReQL, silent,
on 758-key real-money strategy docs. H3 ✅ **best fidelity of any candidate**: change streams
with server-side `$match` on `documentKey._id` and resume tokens. H4 ✅ native TTL indexes,
compose with partial indexes. H5 ⚠️ PyMongo 4.17.0 healthy; **Motor deprecated 2025-05-14, EOL
2026-05-14 — already dead.** H6 ✅ 8.0 EOL Oct 2029; SSPL §13 imposes nothing on a private
datastore. H7 ✅.

Extra ops cost: change streams require a replica set (single-member is fine) and, once auth is
on, a **keyfile**; `oplogMinRetentionHours` defaults to **0**, and a fallen-off token raises
**`ChangeStreamHistoryLost` (286)**, which pymongo **raises rather than retries** — all 23
consumers need an outer loop anyway. Projection saves network + decode but **not** the disk
read (non-covered queries FETCH), so 13 MB reads still evict WiredTiger cache.

**Honest read: Mongo is the smallest conceptual jump and it clears everything.** It loses on
the 16 MiB ceiling being 1.23× the current worst doc, on `$set` being a silent-corruption trap
across ~149 update sites, and on giving no analytics upside.

### 4.3 CouchDB 3.5 — disqualified on the write model

`max_document_size` defaults to **8,000,000 bytes** → 13 MB docs **rejected with HTTP 413** out
of the box (raisable to ~16 MB). Worse: **no partial update exists at all** — every write is a
full-body `PUT` with a matching `_rev`, so a 13 MB doc updated N times writes ~13N MB
(**100 appends ≈ 1.3 GB** for one logical document), and a large share of ~1,119 call sites
become read-modify-write with 409 retry. No native TTL. `_changes` is actually a *good*
semantic match (durable sequences surviving restart and compaction, `since=now` + longpoll) but
its filters are **unindexed per-row scans** — watched docs would need their own database. Python
client is the weakest of any option: `couchdb-python` dead since 2018, `cloudant` EOL 2021, no
Apache-maintained driver; you own ~300 lines of httpx and its pooling. Project health is
excellent (ASF, 3.5.2 2026-05-19, active 4.0 roadmap) — the server is not the risk, the client
and the write model are.

### 4.4 FerretDB — disqualified twice over

**No change streams anywhere in the self-hostable stack.** Issue #175 open since 2021-12-14,
blocked on documentdb#50 (milestone: Backlog); **2.x removed the oplog emulation 1.x had**. All
23 consumers become polling or hand-written triggers on internal `documentdb_data.documents_<id>`
tables that are not stable API. And the vendor is in apparent wind-down: last release v2.7.0
(2025-11-10), one commit since, **`ferretdb.com` serving an expiration notice since 2026-05-19
with no maintainer reply in three months**, $2.5M seed against **MongoDB, Inc. v. FerretDB Inc.,
1:25-cv-00641 (D. Del.)**. Security patches are already not arriving. Also: no authorization at
all (every authenticated user is effectively superuser), and projection is the worst of the
three — `bson_dollar_project` calls `PG_DETOAST_DATUM`, reassembling all 13 MB per row.

### 4.5 Hybrid / no-migration

TTL cron deletes + changefeed hygiene + a Valkey tier for cheap caches + a DuckDB/parquet
analytics layer. **~7–15 engineer-days** (~10–20 with a BacktestResults split). Detailed in §7.
Fixes symptoms, buys 12–24 months, and **does not move the Python-driver ceiling at all.**

Notable finding that cuts against an obvious idea: **do not move `GraphNexusLLMPromptCache` into
Redis/Valkey.** 381k × 3–10 KB = 1.1–3.8 GB payload on an already memory-starved box; RedisJSON
costs 1.2–2.8× raw size; TTLs cost extra memory; Redis never returns freed memory to the OS; and
**every eviction policy is designed to throw away data** whose entries cost real dollars in LLM
calls. RethinkDB's single-digit-ms PK lookup is already fast enough against a multi-second LLM
call.

### 4.6 Dismissed — one line each

| candidate | reason |
|---|---|
| **SurrealDB 3.2** | Python SDK's 3.x support still beta (3.0.0b8) six months after the 3.x server GA; LIVE SELECT disclaims ordering and **silently skips notifications without rolling back the write**. |
| **ArangoDB 3.12** | No changefeed at all (only polling `/_api/wal/tail`); core DB unreleased since March 2024 while the vendor became an AI company; CE binaries revocable at will with audit rights. |
| **ClickHouse** | No changefeed of any kind and beta/async mutations unsuited to per-row config writes — **but the best analytics sink in this report** (Apache 2.0, declarative MergeTree TTL) if DuckDB outgrows the job. |
| **SQLite** | The single global write lock puts 5–13 MB backtest writes in front of live broker order writes; cross-process notification degrades to database-wide `PRAGMA data_version` polling. |
| **TimescaleDB** | Not needed yet — 2.85M rows is small; declarative `PARTITION BY RANGE` + a cron drop gives the same O(1) retention in ~30 lines you own. Revisit above ~10⁸ rows. |
| **Gel (ex-EdgeDB)** | No changefeed, live-query, or subscription feature exists (`gel watch` is a schema-file dev-loop CLI, not a data subscription); full EdgeQL rewrite for no gain over the Postgres underneath. |
| **CockroachDB** | Mandatory telemetry throttles a self-hosted free-tier cluster to **5 concurrent transactions after 7 offline days**; 3-node-minimum distributed SQL on one Docker node is all cost. |

### 4.7 Scoring

Weights: risk to live path ×3, solves the 4 pains ×3, effort ×2, changefeed fidelity ×2,
analytics upside ×1. Scores 1–5, higher is better (effort: higher = less effort).

| candidate | live-path risk (×3) | solves 4 pains (×3) | effort (×2) | changefeed (×2) | analytics (×1) | **weighted** |
|---|---|---|---|---|---|---|
| **PostgreSQL 17 + JSONB** | 4 | **5** | 2 | 4 | **5** | **[12+15+4+8+5] = 44** |
| MongoDB 8.0 | 4 | 4 | **3** | **5** | 1 | [12+12+6+10+1] = **41** |
| Hybrid / do-nothing | **5** | 2 | **5** | 5 | 3 | [15+6+10+10+3] = **44*** |
| CouchDB 3.5 | 2 | 3 | 1 | 3 | 1 | [6+9+2+6+1] = 24 |
| FerretDB 2.7 | 1 | 2 | 2 | **1** | 1 | [3+6+4+2+1] = 16 |

\* The hybrid ties on score and **loses on the axis the score cannot express**: it scores 2 on
"solves the pains" because it fixes none of them permanently, and its 5s are for *not changing
anything*. It does not move the Python-3.13 ceiling or the unfixed CVE. **It is a bridge, not a
destination** — and §7 recommends building part of it anyway, because the DuckDB layer survives
the migration unchanged.

Pain-by-pain:

| pain | Postgres | Mongo | Hybrid |
|---|---|---|---|
| memory wedging | **fixed structurally** (bounded buffer pool) | mitigated (explicit `wiredTigerCacheSizeGB`; 13 MB reads still thrash) | **not fixed** (architectural floor) |
| no TTL | **fixed** (partition detach+drop) | **fixed** (TTL indexes) | worked around (cron deletes) |
| huge-doc full loads | **fixed by Phase 0**, not by the store | same, and 16 MiB cap looms | only via the optional split |
| abandonment | **fixed** | **fixed** | **not fixed** — CVE-2026-24810 unpatchable |

---

## 5. Winner: PostgreSQL 17 + JSONB

MongoDB 8.0 is the honest runner-up and the only other candidate that clears all seven hard
requirements. Postgres still wins on four things Mongo cannot offer:

1. **Bounded buffer pool.** `shared_buffers` is fixed at server start; a 50 GB table cycles 8 kB
   pages through clock-sweep. **Table size cannot exhaust memory** — the direct structural
   answer to RethinkDB-style wedging, where the ~1% resident metadata floor and 8 MB/table
   overhead sit *outside* `--cache-size`. Mongo's WiredTiger cache is cgroup-aware on 7.x/8.x
   but its own docs tell you not to rely on it in a container, and every 13 MB read evicts hot
   pages.
2. **SQL removes a class of agent work.** The repo has **zero server-side joins and zero
   server-side aggregation** — every join is a Python dict lookup (`interactive_utils.py:6805-6820`),
   and P&L/attribution reconstruction is currently bespoke code plus an Opus agent reading full
   backtest paths. In SQL that becomes a query. Mongo's aggregation pipeline is a worse fit for
   the same job and buys no ad-hoc analyst surface.
3. **No 16 MB ceiling.** Current worst doc is 13 MB against Mongo's 16 MiB hard cap — 1.23×
   headroom on a document that grows during a run. Postgres' limit is 1 GB. Phase 0 removes the
   problem on both stores, but only one of them has a cliff.
4. **Partition-drop TTL, 5-year support, one process to operate.** `DETACH CONCURRENTLY` +
   `DROP` "entirely avoids the VACUUM overhead caused by a bulk DELETE" (PG docs, verbatim).
   PG 17 EOL 2029-11-08; JSONB in core since 2014, no deprecation.

Mongo's specific costs, stated plainly: the 16 MiB cap; the `$set` non-merge trap (silent,
across ~149 nested-update sites, on the 758-key real-money doc 179); Motor already EOL so any
async path must be rewritten onto `AsyncMongoClient`; and oplog/286 hygiene — `oplogMinRetentionHours`
defaults to 0 and pymongo raises `ChangeStreamHistoryLost` rather than resuming.

### The changefeed replacement is NOT logical decoding

**Use trigger → `pg_notify(channel, id)` → unconditional re-read, with a 1–2 s poll-and-diff
backstop.** For the 23 config/control sites, **poll-only is also sufficient** — they are ~25
small rows; `SELECT` them all every 1–2 s and diff in memory is correct by construction,
self-healing, and one page of I/O.

Why not logical decoding: **psycopg 3 has no replication API** (issue #71 open since 2021-08-29;
maintainer, 2025-11-24: "No further replication support is planned for release 3.3"; a community
POC PR was closed unmerged the same day). You would run psycopg2 alongside psycopg3 solely for
this. And on a single node the slot is the worst failure mode available: slots "know nothing
about the state of their consumer(s)… will prevent removal of required resources even when there
is no connection using them… **In extreme cases this could cause the database to shut down**",
with `max_slot_wal_keep_size` defaulting to **-1 = unlimited WAL retention**.

**A missed notification on a system that re-reads anyway is a strictly better failure than a
database that shuts down.** This is exactly the path CoCalc took off RethinkDB changefeeds.

NOTIFY guardrails: payload <8000 bytes (send an id, never data); nothing delivers until commit;
identical payloads within one transaction fold to one delivery; the queue is 8 GB and a **full
queue makes committing transactions fail**, so never let a listening session sit inside a long
transaction; channel names are silently truncated at **63 bytes** — a real hazard for
scope-suffixed ids like `alpaca-main|3df5616cacc43b413a6eaf21|2026-03-02|AACI`. Use psycopg
**≥3.2.10**, `notifies()` generator only, never mixed with `add_notify_handler()`, autocommit,
finite `timeout=` treated as a resync tick, and re-issue `LISTEN` after every reconnect. Keep
watchers off pgbouncer transaction pooling — LISTEN is listed "Never" supported there.

Polling trap worth encoding once: `now()` returns **transaction start** time, so a watermark
poller can skip a long transaction's writes entirely. Overlap the window
(`> last_seen - interval '30 seconds'`) and dedupe by PK — or just diff all 25 rows.

---

## 6. Phased migration sketch

**Live-money safeguards, binding on every phase:**
- Never cut over `alpaca-main` (doc 179) first. It is last, after every other instance has run
  a full weekly cycle on Postgres.
- **A Postgres write can never fail a trade during Phases 1–3.** RethinkDB stays authoritative;
  Postgres is fed by a relay, not by dual writes.
- No cutover during market hours. Flips happen on a weekend, after a full clean weekly cycle.
- Rollback door stays open through Phase 3: reverting is a config flip.

### Phase 0 — BacktestResults split + writer fixes · **4–7 days** · do this regardless

Store-agnostic. Do it **inside RethinkDB first** as a separate table.

1. Split: metadata row (`id`, status, window, headline P&L, timestamps as real fields) +
   **`backtest_steps` child table, insert-only, PK `(backtest_id, step_no)`**. O(n), not O(n²).
2. Fix the three incremental writers:
   - heartbeat (`broker.py:12173-12193`) — stop re-sending 500 logs every 15 s; write logs as
     child rows, heartbeat carries scalars only.
   - progress writer (`broker.py:17802-17866`) — stop rewriting `backtest_decisions` /
     `backtest_refusals` in their entirety every 2%; append new entries as child rows.
   - terminal write (`broker.py:12933/12936`) — keep as the single full-blob write.
3. Update readers: the `_slim()` fast path (`interactive_utils.py:5222-5273`) becomes a
   metadata-table scan; the seven bare `.get(bid)` detail sites
   (`:1440, 5827, 6039, 6085, 6316, 6841, 6875`) fetch child rows on demand.

Independently buys: the 12.2 s list render collapses, the latent 100k-array-limit exposure
(§3) goes away, and both Postgres (8 GB WAL/run) and Mongo (16 MiB cap) stop being cliffs.

*Rollback:* new table is additive; keep writing the legacy arrays behind a flag for one release,
flip readers back if anything regresses.

### Phase 1 — Postgres up + helper module + isolated low-risk tables · **8–13 days**

Infra: custom image `FROM postgres:17` + `postgresql-17-partman` (prefer **pg_partman's own
BGW** over pg_cron on a single node — no `cron.database_name`, no one-database-per-cluster
limit; note `retention_keep_table` defaults to TRUE, set it false). `shm_size: 1gb` (the Docker
default `/dev/shm` is **64 MB** and is *not* `shared_buffers` — parallel scans fail with a
confusing error). `mem_limit`, `PG_OOM_ADJUST_FILE=/proc/self/oom_score_adj` +
`PG_OOM_ADJUST_VALUE=0`. `default_toast_compression=lz4` if the PGDG build has it (§8).
Per-table `autovacuum_vacuum_scale_factor` — the 0.2 default means **570,050 dead tuples on
PriceHistory** before autovacuum reacts — and set `toast.autovacuum_*` separately on blob tables.

**Thin typed helper module — ~10 functions, NOT a ReQL shim.** No production-grade
ReQL-on-Postgres shim exists (the complete inventory is three abandoned 2016–2017 repos with
0–20 stars); the only calibration point, `reqlite`, needed 2000+ differential tests for a subset
of ReQL 2.2, and your oracle would be a server nobody releases. Both parties who actually did
this migration ported call sites instead. Widen `backtest_replay.RethinkReplayStore` (§2d) into
the seam.

Functions: `get / get_all / insert(conflict) / update(deep-merge) / delete / between / filter /
pluck / order_by / count`. It must encode:
- **`jsonb_deep_merge(a,b)` in PL/pgSQL.** `doc || excluded.doc` "does not operate recursively"
  — stored `{"data":{"age":30,"city":"SF"}}` + `{"data":{"age":19}}` → **`city` silently gone**.
  ReQL deep-merges objects only (arrays replace wholesale); every `r.literal()` site (3) maps to
  a **shallow** set. `jsonb_set` returns the target **unchanged** if an earlier path step is
  missing — a silent no-op where ReQL creates the nesting.
- **`between` bounds: emit explicit `>= / <`, never SQL `BETWEEN`.** ReQL is `[lo,hi)`, SQL
  `BETWEEN` is `[lo,hi]` — an off-by-one bar in every window.
- **`get_all` does not dedupe; `= ANY()` does.** `[a,a,b]` → 3 rows vs 2.
- **`COLLATE "C"`** everywhere strings are ordered — ReQL orders bytewise, and
  `graph_nexus_analysis.py:11856`'s `id DESC` tiebreak is decision-affecting (§2f).
- **`set_json_dumps(partial(json.dumps, allow_nan=False))`** — RethinkDB rejects NaN today with
  a clear client-side error; `json.dumps` defaults to `allow_nan=True`, so post-migration you'd
  get a server-side `invalid input syntax for type json` from a different layer. In a system
  computing indicators over talib warmup windows this is not hypothetical.
- **`Jsonb()` wrapping** — psycopg 3 does not auto-adapt `dict`.
- **Key-order and number canonicalization**: jsonb "does not preserve the order of object keys"
  and rewrites `1.230e-5` → `0.00001230`; RethinkDB's `keys()` is lexicographic. **Any
  fingerprint or hash must move to `json.dumps(doc, sort_keys=True)` on BOTH sides before
  cutover**, or the first post-migration A/A looks like a discovery. Audit
  `paired_state_attest.py` and `nexus_config_identity.py:127` first.
- **`conflict=` mapping**: `replace` → `DO UPDATE SET doc = EXCLUDED.doc`; `update` →
  `SET doc = jsonb_deep_merge(t.doc, EXCLUDED.doc)` (**not `||`**); `error` → not `DO NOTHING`
  (ReQL records the conflict without aborting the batch). ReQL multi-doc insert is
  partial-success; a Postgres multi-row INSERT is all-or-nothing — any code reading
  `result['errors']` changes behavior.
- Create pools **after fork** in broker, backtest engine, and FastAPI workers (psycopg
  connections are thread-safe, cursors are not, connections are **not** process-safe).

Table order (all isolated, none on the live order path):
1. `GraphNexusLLMPromptCache` — hot exact-match KV by 48-hex hash, `(text PK, jsonb)`, zero
   durability requirement, growth now slowing (§3). Add the first real TTL.
2. `LLMUsage` — 295k rows, batched DELETE + tuned autovacuum; partitioning is over-engineering
   below ~5M rows.
3. `AlpacaBarsCache` — 64-hex hash PK, 1.1 GB, pure cache.
4. `PriceHistory` — monthly RANGE partitions, **PK `(symbol, ts)`** (a partitioned PK must
   include the partition key), retention = `DETACH CONCURRENTLY` + `DROP`, and **no DEFAULT
   partition** (a default partition forbids `DETACH CONCURRENTLY`, which also cannot run inside
   a transaction block). Rebuild at 2.85M rows is minutes — do it now while it's cheap.

*Rollback:* these four tables are read through the helper behind a per-table flag; flip back to
RethinkDB. Nothing else has moved.

### Phase 2 — changefeed relay + backfill + shadow comparator · **7–12 days**

- **Relay, not dual-write.** Tail RethinkDB `.changes()` into Postgres: **one code path instead
  of 1,119 call sites**, naturally retryable, and structurally incapable of failing a live
  order. Dual-writing has an interleaving where one store commits and the other doesn't, and the
  stores then diverge permanently and silently — which poisons the shadow-read signal with
  migration artifacts and trains you to ignore the alarm.
- Backfill separately: `rethinkdb export` → **JSONL** (never CSV — "values in CSV imports will
  always be imported as strings", a data-corruption vector here) → `COPY`. Use
  `time_format='raw'` on the export run, or pass `default=` to ISO-format; the driver defaults
  to `native` and yields `datetime` objects `json.dumps` cannot serialize. Record the relay
  start timestamp **before** the export begins so the two overlap rather than leave a hole.
  Page by PK with `.between(last_id, None, left_bound='open')`, never `skip()`.
- **Shadow-read comparator** normalizing key order, number format, and timestamps to UTC before
  comparing — naive `==` fires constantly on non-divergence. Sample 1–5% of reads, **store full
  mismatched pairs, not counters**. Return the control result always; the candidate can never
  affect production.
- **Gate: hold 0.00% mismatch across a full weekly cycle** including a weekend and a
  market-close/settlement boundary before flipping any read.

*Rollback:* the relay is read-only into Postgres; kill it and nothing in production notices.

### Phase 3 — Nexus tables + byte-identical re-certification · **8–14 days**

- `GraphNexusTradeContexts` (7.4 GB, 504,742 rows, 53 KB/row): `(text PK, jsonb)` with
  **STORED generated columns** for `instance_id`, `base_instance_id`, and `date` + btree.
  The scope-suffixed prefix patterns in `clear_instance_state.py:311-372` map to
  `split_part(instance_id,'|',1)` for base-id lookups (index that expression directly rather
  than `LIKE 'alpaca-main|%'`) and `text_pattern_ops` where a genuine prefix scan is needed.
  Note the 2026-05-25 lesson at `clear_instance_state.py:100-104`: exact-only matching found
  **zero** scoped rows and made a full clear a silent no-op. Re-verify per table.
- Then the rest of the 26-table `_ALLOWED_STATE_TABLES` manifest
  (`frozen_paired_state.py:39-52`) — this list *is* the migration checklist.
- Also fix the `instance_id` type split on `BacktestResults`: **NUMBER on 592 rows, STRING on
  833**. The ReQL index coerces; a typed store needs an explicit migration.
- **Re-certification gate: ≥1 paired cold A/A + 1 warm A/A** under the existing protocol
  (`run_paired_experiment.py:20-22`, warm protocol accepted 2026-08-21). Both must come back
  byte-identical with 100% traded-name overlap, the same bar as bt 479057/193668. The
  `(latest_observation_date DESC, id DESC)` tiebreak must be verified under `COLLATE "C"`
  specifically — this is the single most likely silent failure in the whole migration.

*Rollback:* per-table read flags; RethinkDB still written by the relay's source of truth.

### Phase 4 — config/control tables LAST · **6–10 days**

`Instances`, `Strategies`, `EngineControl`, `Config`, `BacktestInstances`, `LiveState`. These
are changefeed-critical, deep-merge-critical, and the live-money path. Deliverable: the
per-file replacement table below, then flip reads per table.

| # | file:line | watches | class | replacement | note |
|---|---|---|---|---|---|
| 1 | `instance.py:373` | `Instances.get(id)` | a | NOTIFY `instances` + re-read | **loses events today** |
| 2 | `instance.py:666` | `Instances.get(id)` | a | NOTIFY + re-read | **loses events today** |
| 3 | `server.py:1211` | `EngineControl` table | a | poll-and-diff 1–2 s | loses; boot-only self-heal |
| 4 | `server.py:1478` | `EngineControl` table | a | poll-and-diff | loses |
| 5 | `server.py:1519` | `EngineControl` table | a | poll-and-diff | loses |
| 6 | `server.py:1691` | `EngineControl` table | a | poll-and-diff | loses |
| 7 | `server.py:1799` | `EngineControl` table | a | poll-and-diff | loses; partly covered by the 10 s `check_resume_timer` |
| 8 | `server.py:1894` | `Config.get('Pings')` | b | poll-and-diff | irrelevant (next ping re-fires) |
| 9 | `server.py:1906` | `Instances` table | a | poll-and-diff | **loses** — an instance started during a blip never gets a container |
| 10 | `priceBroker.py:76` | `Config.get('Pings')` | b | poll | irrelevant |
| 11 | `priceBroker.py:102` | `Config.get('Config')` | a | NOTIFY + re-read | **loses** a shutdown command |
| 12 | `broker.py:5728` | `Instances.get(id)` | a | NOTIFY + re-read, **carry old/new or cache prior doc** (`:5730-5732`) | **highest-risk gap** — the BUG #6 class this code exists to fix; only needs to land before the next 60–900 s tick |
| 13 | `broker.py:5784` | `Strategies.get(id)` | a | NOTIFY + re-read (diffs at `:5786`) | loses |
| 14 | `broker.py:5833` | `BacktestInstances.get(row)` | a | queue poll 1–5 s | **no reconnect at all** — a drop today leaves a backtest permanently unstoppable |
| 15 | `kalshi/backtest_worker.py:256` | filtered `KalshiBacktests` | b | queue poll | already covered (re-drains) |
| 16 | `engines/discover_engine.py:68` | `Config.get('Pings')` | b | poll | irrelevant |
| 17 | `engines/discover_engine.py:106` | `EngineControl.get(...)` | a | poll-and-diff | loses |
| 18 | `engines/daily_digest_engine.py:479` | `EngineControl.get(DIGEST)` | b | poll-and-diff | partly covered; **re-raises non-replica errors** (`:497-500`) — the 2026-07-06 regression pattern, still present |
| 19 | `engines/price_engine.py:71` | `Config.get('Pings')` | b | poll | **no reconnect** |
| 20 | `engines/price_engine.py:92` | `EngineControl.get('price_engine')` | a | poll-and-diff | **no reconnect** |
| 21 | `engines/price_engine.py:150` | `LivePricesStocks` table | a | poll-and-diff | **no reconnect**, runs on the main thread; watchlist not tick stream |
| 22 | `engines/self_learning_engine.py:531` | `BacktestResults` table | b | queue poll on `(id,status)` | already covered by `include_initial` + watermark; the server-side `.pluck` exists because `new_val` would otherwise be the whole 5–13 MB doc |
| 23 | `engines/backtest_engine.py:861` | `BacktestInstances` table | b | queue poll | **the reference implementation** — re-sweeps on every reconnect |

**14 of 23 are class (a) control-doc watches; 8 lose events today and 3 files have no reconnect
at all. Every replacement above is a correctness upgrade, not a regression.** Delete the dead
`engine_control.py:159 engine_changes()` while here.

*Rollback:* config flip per table; RethinkDB is still written until Phase 5.

### Phase 5 — decommission, retention, analytics · **3–6 days**

Stop writing RethinkDB, remove the relay, enable pg_partman retention on PriceHistory /
LLMUsage / prompt cache / `GraphNexusTradeContexts` (the 7.4 GB elephant finally gets a policy),
tear down the container. Optional DuckDB/parquet layer — but note **SQL itself now covers the
P&L/attribution reconstruction** that currently requires bespoke code plus an agent reading full
backtest paths.

### Totals

| phase | days |
|---|---|
| 0 — BacktestResults split (do regardless) | 4–7 |
| 1 — Postgres up + helper + 4 isolated tables | 8–13 |
| 2 — relay + backfill + shadow comparator | 7–12 |
| 3 — Nexus tables + byte-identical re-cert | 8–14 |
| 4 — config/control + 23 changefeed replacements | 6–10 |
| 5 — decommission + retention + analytics | 3–6 |
| **total** | **36–62 engineer-days** |

Calendar, part-time alongside trading operations and with a mandatory full-weekly-cycle soak
at the end of Phases 2, 3, and 4: **~4–6 months**. Full-time with no other work: ~2–3 months.
Calibration: CoCalc rewrote ~5,600 lines in ~1 month for the same migration.

---

## 7. Do-nothing + hybrid baseline

| # | piece | days | note |
|---|---|---|---|
| 0 | explicit `--cache-size` | **0 — already done** | `docker-compose.yml:344` |
| 1 | TTL cron deletes | 0.5–1 | ranged `.between().delete()` on an indexed timestamp, batched, off-hours. **Never an unindexed `filter().delete()` on 501k rows** — that is a full scan that pages the whole table through cache and *causes* the wedge you are fixing. |
| 2 | changefeed hygiene (`squash`, point feeds, projection) | 0.5–1 | only `self_learning_engine.py:531` does this today |
| 3 | Valkey tier — article caches, rate limits, dedupe; **not** LLMPromptCache | 2–4 | §4.5 |
| 4 | DuckDB export + analytics layer | 4–8 | ~150–250 LOC exporter + parquet partition design |
| 5 | *(optional)* BacktestResults split | 3–5 | = Phase 0 |
| | **total** | **7–15 (10–20 with #5)** | |

**Fixes:** dataset growth (TTL shrinks the resident-metadata floor proportionally — genuine
relief), giant-doc *analytics* scans (DuckDB), some changefeed load. Buys 12–24 months.

**Does not fix:** the ~1% unevictable metadata floor and the ~1.0 GB of 8 MB-per-table
overhead; a single heavy query blowing past `--cache-size` ([#6275]: a container with
`--cache-size 15000` reached **64 GB** before OOM); giant-doc loads outside the analytics path;
abandonment — **CVE-2026-24810 has no fixed release**; and **the Python-driver ceiling, which
no hybrid piece touches.**

**Build the DuckDB layer early regardless.** It survives the Postgres migration unchanged, and
it is the only piece here that is not throwaway. Two named traps: `maximum_object_size` defaults
to **16,777,216** against 5–13 MB docs (<25% headroom — raise it explicitly in the loader, this
is the most likely production break), and `ignore_errors` works only with `newline_delimited`,
so standardize on ndjson. Keep parquet as the source of truth and the `.duckdb` file as a
disposable rebuildable index — DuckDB **2.0.0 is slated for Fall 2026**, inside the planning
window.

---

## 8. Open questions before Phase 1

1. **Is `lz4` compiled into the PGDG `postgres:17` image?** `SHOW default_toast_compression`
   and an explicit `ALTER TABLE … SET COMPRESSION lz4` before relying on it. **UNVERIFIED.**
2. **Python version plan.** What CPython is production on today, and does anything call
   `set_loop_type('asyncio')` on the RethinkDB driver? If yes, you are already pinned below
   3.11 and the migration clock is shorter than it looks.
3. **Does any code depend on ReQL's 100k array-limit as a guardrail?** §3 says nothing defends
   against it, but confirm nothing *relies* on the failure either — Postgres has no such limit
   and queries that fail loudly today would quietly return huge result sets.
4. **`pluck` missing-field semantics. UNVERIFIED.** ReQL empirically *omits* absent fields;
   `jsonb_build_object('a', doc->'a')` yields `{"a": null}` — a present key with a null value.
   Any `if 'a' in result` flips. Test against live data before porting `_slim()`.
5. **Proxmox guest `vm.overcommit_memory`.** Kernel-wide, cannot be set inside the container.
   Set `vm.overcommit_memory=2` on the guest and disable transparent huge pages there (PG docs:
   THP "use is currently discouraged").
6. **`--cache-size`: RESOLVED, already explicit.** `docker-compose.yml:344` passes
   `--cache-size 2048`, with `mem_limit 5G` / `cpus 3` at `:355-364`. Report 04's "highest-value
   single change available" is already in place. The open sub-question is whether **2048 against
   a 16 GB dataset with ~1.0 GB of per-table floor** is the right split of a 5 GB budget, or
   whether raising the container limit is the cheaper interim move.
7. **PG 17 vs 18.** Recommend **17** (mature, EOL 2029-11): PG 18 makes generated columns
   `VIRTUAL` by default and virtual columns **cannot be indexed**, so §5's whole indexing
   strategy changes meaning on 18. Revisit at the next major.
8. **Does anything replicate the RethinkDB data off-box today** (backup, secondary)? Affects
   whether the relay is the only reader and whether Phase 5 loses a backup path.
9. **The 592-NUMBER / 833-STRING `instance_id` split on `BacktestResults`** — decide the target
   type and write the coercion before Phase 3, not during.

---

## 9. Sources

**PostgreSQL**
- storage-toast — https://www.postgresql.org/docs/current/storage-toast.html
- limits — https://www.postgresql.org/docs/current/limits.html
- datatype-json (§8.14.2 row-lock warning, §8.14.4 GIN opclasses, key-order) — https://www.postgresql.org/docs/current/datatype-json.html
- functions-json (`||` not recursive) — https://www.postgresql.org/docs/current/functions-json.html
- gin.html (fastupdate, pending list) — https://www.postgresql.org/docs/current/gin.html
- ddl-partitioning — https://www.postgresql.org/docs/current/ddl-partitioning.html
- ddl-generated-columns (PG18 VIRTUAL default) — https://www.postgresql.org/docs/current/ddl-generated-columns.html
- sql-notify — https://www.postgresql.org/docs/18/sql-notify.html
- logicaldecoding-explanation (slot shutdown risk) — https://www.postgresql.org/docs/18/logicaldecoding-explanation.html
- runtime-config-replication (`max_slot_wal_keep_size` = -1) — https://www.postgresql.org/docs/18/runtime-config-replication.html
- runtime-config-resource (`shared_buffers`, `work_mem`) — https://www.postgresql.org/docs/current/runtime-config-resource.html
- routine-vacuuming — https://www.postgresql.org/docs/current/routine-vacuuming.html
- kernel-resources (OOM killer) — https://www.postgresql.org/docs/current/kernel-resources.html
- functions-datetime (`now()` = txn start) — https://www.postgresql.org/docs/18/functions-datetime.html
- versioning policy — https://www.postgresql.org/support/versioning/
- docker-library postgres README (`/dev/shm` 64 MB) — https://github.com/docker-library/docs/blob/master/postgres/README.md
- Yen, generated columns vs GIN benchmark — https://richyen.com/postgres/2026/05/11/generated_columns_jsonb.html
- Ramsey, jsonb + TOAST (40 KB → 500 ms) — https://www.snowflake.com/en/blog/engineering/postgres-jsonb-columns-and-toast/
- psycopg replication issue #71 — https://github.com/psycopg/psycopg/issues/71
- psycopg notifies memory leak #1091 — https://github.com/psycopg/psycopg/issues/1091
- pg_cron — https://github.com/citusdata/pg_cron
- pg_partman BGW — https://github.com/pgpartman/pg_partman
- pgbouncer features (LISTEN "Never") — https://www.pgbouncer.org/features.html
- CoCalc RethinkDB→Postgres — https://blog.cocalc.com/2017/02/09/rethinkdb-vs-postgres.html
- Lumi migration — https://medium.com/fuzzy-sharp/migrating-to-postgres-2dc1519a6dc7
- Stripe, online migrations at scale — https://stripe.com/blog/online-migrations
- GitHub scientist — https://github.com/github/scientist

**RethinkDB**
- limitations (1% metadata, 8 MB/table, 16 MB doc ceiling, NaN rejected) — https://rethinkdb.com/limitations/
- memory-usage — https://rethinkdb.com/docs/memory-usage/
- changefeeds (squash, no delivery guarantee) — https://rethinkdb.com/docs/changefeeds/python/
- update (recursive merge) — https://rethinkdb.com/api/python/update/
- literal — https://rethinkdb.com/api/python/literal/
- between (`[lo,hi)`) — https://rethinkdb.com/api/python/between/
- run() (`time_format='raw'`, `array_limit`) — https://rethinkdb.com/api/python/run
- releases (2.4.4, 2023-12-11) — https://github.com/rethinkdb/rethinkdb/releases
- PyPI `rethinkdb` 2.4.10.post1 — https://pypi.org/pypi/rethinkdb/json
- driver asyncio 3.11+ break — https://github.com/rethinkdb/rethinkdb-python/issues/294
- cgroup limit not honored — https://github.com/rethinkdb/rethinkdb-dockerfiles/issues/29
- 64 GB OOM with `--cache-size 15000` — https://github.com/rethinkdb/rethinkdb/issues/6275
- pluck inefficiency — https://github.com/rethinkdb/rethinkdb/issues/947
- 7 MB records → cache 8%→74% — https://github.com/rethinkdb/rethinkdb/issues/5695
- CVEs (incl. CVE-2026-24810, unfixed) — https://app.opencve.io/cve/?vendor=rethinkdb

**MongoDB**
- limits (16 MiB) — https://www.mongodb.com/docs/manual/reference/limits/
- `$set` replaces embedded documents — https://www.mongodb.com/docs/manual/reference/operator/update/set/
- replica-set oplog / retention — https://www.mongodb.com/docs/manual/core/replica-set-oplog/
- change-streams driver spec (resume-once, 286 non-resumable) — https://github.com/mongodb/specifications/blob/master/source/change-streams/change-streams.md
- TTL indexes — https://www.mongodb.com/docs/manual/core/index-ttl/
- WiredTiger cache sizing — https://www.mongodb.com/docs/manual/core/wiredtiger/
- lifecycle schedules (8.0 EOL Oct 2029) — https://www.mongodb.com/legal/support-policy/lifecycles

**CouchDB / FerretDB**
- CouchDB config (`max_document_size` 8,000,000) — https://docs.couchdb.org/en/stable/config/couchdb.html
- CouchDB `_changes` — https://docs.couchdb.org/en/stable/api/database/changes.html
- FerretDB change-streams issue #175 — https://github.com/FerretDB/FerretDB/issues/175
- FerretDB expired-domain issue #5650 — https://github.com/FerretDB/FerretDB/issues/5650
- MongoDB, Inc. v. FerretDB Inc. docket — https://www.courtlistener.com/docket/70354365/mongodb-inc-v-ferretdb-inc/

**Dismissed / hybrid**
- SurrealDB LIVE SELECT — https://surrealdb.com/docs/surrealql/statements/live
- ArangoDB community license PDF (100 GB cap, audit rights) — https://arango.ai/wp-content/uploads/2025/11/ADB-Community-License_31OCT2023.pdf
- ClickHouse UPDATE (beta caveats) — https://clickhouse.com/docs/sql-reference/statements/update
- ClickHouse TTL — https://clickhouse.com/docs/guides/developer/ttl
- SQLite WAL — https://sqlite.org/wal.html
- SQLite `pragma data_version` — https://sqlite.org/pragma.html#pragma_data_version
- TimescaleDB data retention — https://www.tigerdata.com/docs/use-timescale/latest/data-retention
- CockroachDB licensing FAQ (telemetry throttle) — https://docs.cockroachlabs.com/docs/stable/licensing-faqs
- Redis JSON RAM overhead — https://redis.io/docs/latest/develop/data-types/json/ram/
- Redis eviction (TTL costs memory) — https://redis.io/docs/latest/develop/reference/eviction/
- DuckDB loading JSON (`maximum_object_size`) — https://duckdb.org/docs/current/data/json/loading_json.html
- DuckDB concurrency — https://duckdb.org/docs/current/connect/concurrency.html
- DuckDB release calendar (2.0 Fall 2026) — https://duckdb.org/release_calendar

**In-repo**
- `docs/investigations/LLM-COST-2026-08-22.md`
- `docs/investigations/prereg-warm-protocol-acceptance-2026-08-21.md`
- `backend/frozen_paired_state.py:39-52` — the 26-table byte-identical manifest
- `scripts/run_paired_experiment.py:20-22` — the paired protocol
- `scripts/create_backtest_list_indices.py:6-11` — the 12.2 s list-render measurement
