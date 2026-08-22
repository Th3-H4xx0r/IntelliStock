# Postgres port — design spec

2026-08-22. Branch `feat/postgres-port`.

Inputs: the user-approved design (§1–§8, 2026-08-22), `docs/investigations/DB-REPLACEMENT-2026-08-22.md`
(evidence), and the repo inventory (call-site census, live DB read). This spec implements the
approved design; it does not revisit its decisions. Where the approved design left an interface
unspecified, §12 lists the choice made and marks it for veto.

---

## 1. Goal, invariant, non-goals

**Goal.** Replace RethinkDB with PostgreSQL 17 + JSONB as the sole datastore. Every behaviour
the application has today it must still have — same JSON shapes, same key sets, same orderings,
same failure modes — with the storage engine swapped underneath.

**Invariant (the user's, binding on every decision below).**

> Keep all functionality the same completely as using rethinkdb but just different db.

Operationally that means four testable things:

1. **Shape fidelity.** Every value that reaches Python is the same plain `dict`/`list`/scalar it
   is today, with the same keys present and the same keys absent.
2. **Order fidelity.** Every ordered read returns the same total order, including ties. String
   ordering is bytewise (`COLLATE "C"`), because `graph_nexus_analysis.py:11856`'s
   `(latest_observation_date DESC, id DESC)` tiebreak decides membership of an 80-row window that
   lands in an LLM prompt.
3. **Merge fidelity.** `update()` deep-merges objects and replaces arrays, and `r.literal()` sets
   shallow — 149 call sites depend on it.
4. **Notification fidelity or better.** All 23 change watchers keep working. Eight of them lose
   events on reconnect today and three have no reconnect at all; the replacement re-reads on start
   and on every reconnect, which is a strict upgrade, not a behaviour change the app can observe.

**Non-goals.**

- No relational redesign. Tables stay `(id, doc jsonb)` and keep their RethinkDB names 1:1.
- No ReQL shim. Call sites are ported mechanically to a typed store API.
- No new features, no strategy changes, no tuning. A P&L difference in any backtest is a bug.
- No production backtests and no production cutover in this branch. Local testing only; the cold
  and warm A/A re-certification is the user's, run from the runbook this spec produces.
- Frontend and mobile are untouched. Any change visible to Flutter is a bug.
- The RethinkDB driver leaves the runtime. It survives only as an optional import inside the
  one-shot migration script.

**Done means:** the 475 existing backend tests pass, the new `backend/tests/db/` suite passes
against a real local Postgres, `grep -rn "rethinkdb" backend --include='*.py'` returns only the
migration script and archived scripts, and a PR is open (not merged).

---

## 2. Architecture

New package `backend/db/`. Seven modules, no other module in the repo opens a connection.

```
backend/db/
  __init__.py     re-exports the store singleton + Literal + errors
  pool.py         one psycopg_pool.ConnectionPool per process, created after fork
  json.py         canonical dumps/loads, NaN rejection, sha256 helper
  merge.py        ReQL-compatible deep merge (Python + PL/pgSQL twin)
  schema.py       table registry + idempotent DDL
  store.py        the typed API every call site uses
  watch.py        LISTEN/NOTIFY + poll-backstop change watchers
  errors.py       StoreError, ConflictError, UnavailableError, CasFailed
```

Dependency order is strict and acyclic: `errors` → `json` → `merge` → `pool` → `schema` →
`store` → `watch`. `merge` must not import `pool` (it is pure and property-tested standalone).

### 2.1 Who routes where

- All 16 `get_conn()` definitions (`instance.py:194`, `server.py:105`, `priceBroker.py:35`,
  `auth_utils.py:51`, `interactive_utils.py:30`, `kalshi/db.py:55`, `broker.py:1891` and `:1931`,
  `self_learning/store.py:126`, `engines/backtest_engine.py:322`,
  `engines/discover_engine.py:27`, `engines/price_engine.py:33`, plus test stubs) are deleted.
  Modules import `from db import store`.
- `llm_utils._prompt_cache_new_conn()` (`:5773`) — the per-operation connection — is deleted; the
  prompt cache uses the pool. Its self-disable-after-5-failures behaviour
  (`_PROMPT_CACHE_MAX_FAILS`) is preserved verbatim, counting `StoreError` instead of driver
  exceptions.
- `backend/kalshi/db.py` keeps `KALSHI_TABLES` (the 27-entry `(table, primary_key)` registry) as
  the source of truth for those tables' PK names, and feeds it into `schema.py` at import.
  `get_conn()`, `is_conn_error()`, and `reconnect()` are deleted; the pool owns reconnection.
- `backtest_replay.RethinkReplayStore` → `PostgresReplayStore`, same class interface. It takes a
  *backend* object (not a connection), so the change is confined to
  `benchmark_alpha/rethink_store.py::_RethinkBackend` → `PostgresBackend` and the factory
  `backtest_evidence_runtime.default_replay_store()`. `InMemoryReplayStore` is unchanged; the
  ~10 test doubles implementing `insert_record`/`get_record`/`update_row`/
  `compare_and_swap_state` keep working untouched.
- `rethink_changefeed.run_reconnecting_changefeed` is reimplemented on `watch.py` (§6.4).
- `engine_control.py:159 engine_changes()` is deleted (dead helper, zero callers).

### 2.2 Process model

Postgres connections are not process-safe. Every process that forks (server → broker subprocess,
backtest engine → container, FastAPI workers) must not inherit a pool. `pool.py` therefore:

- creates the pool lazily on first use, never at import;
- registers `os.register_at_fork(after_in_child=reset_after_fork)` which drops the child's
  inherited pool object without closing the parent's sockets.

Cursors are not thread-safe; connections are. The pool hands out one connection per operation
via a context manager, so no cursor crosses a thread boundary. Watchers get their own dedicated
autocommit connection *outside* the pool (a `LISTEN` session must never sit in a pooled
connection or inside a long transaction — a full 8 GB notify queue fails commits).

### 2.3 pool.py

```python
DEFAULT_MIN_SIZE = 1
DEFAULT_MAX_SIZE = 8          # env PG_POOL_MAX

def dsn_from_env() -> str: ...
    # PG_DSN wins; else assembled from POSTGRES_HOST / POSTGRES_PORT /
    # POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB (defaults
    # localhost/5432/intellistock/-/IntelliStock).

def get_pool(dsn: str | None = None) -> ConnectionPool: ...
    # Idempotent per process. Sets options: -c timezone=UTC
    # -c default_transaction_isolation=read committed.

@contextmanager
def connection(*, autocommit: bool = False) -> Iterator[Connection]: ...

@contextmanager
def cursor(*, autocommit: bool = False) -> Iterator[Cursor]: ...
    # row_factory=dict_row always.

def listen_connection() -> Connection: ...
    # A dedicated, unpooled, autocommit connection for watch.py. Caller closes.

def reset_after_fork() -> None: ...
def close_pool() -> None: ...
def health() -> dict: ...      # {"ok": bool, "size": int, "dsn_host": str}
```

Retry policy: `connection()` retries a connection-level failure twice with 0.5 s / 1.5 s backoff
before raising `UnavailableError`. Query-level errors are never retried (a retried non-idempotent
write is worse than an error). This matches today's `get_conn_retry(max_attempts, delay)` shape
in `broker.py:1897/1934`, whose call sites keep their own outer loops unchanged.

### 2.4 json.py

```python
dumps: Callable[[Any], str]           # partial(json.dumps, allow_nan=False, separators=(",",":"))
loads: Callable[[str], Any]

def install() -> None: ...
    # psycopg.types.json.set_json_dumps(dumps); set_json_loads(loads).
    # Called once from pool.get_pool(). NaN/Infinity raise ValueError at the
    # client, mirroring RethinkDB's client-side rejection, instead of surfacing
    # as a server-side "invalid input syntax for type json".

def canonical(value: Any) -> str: ...          # json.dumps(value, sort_keys=True, allow_nan=False)
def canonical_sha256(value: Any) -> str: ...   # hexdigest of canonical(value).encode()
```

`canonical_sha256` is the single hashing entry point. Before cutover, `paired_state_attest.py`
and `nexus_config_identity.py:127` are audited to route every fingerprint through it, so a
fingerprint is invariant to jsonb's key reordering and number renormalisation
(`1.230e-5` → `0.00001230`).

### 2.5 merge.py

```python
class Literal:
    """ReQL r.literal(): replace the subtree, do not merge into it."""
    __slots__ = ("value",)
    def __init__(self, value: Any) -> None: ...

def deep_merge(base: Any, patch: Any) -> Any: ...
    # Objects merge recursively. Arrays and scalars replace wholesale.
    # patch value None sets JSON null (it does NOT delete the key).
    # Literal(v) sets v shallow; Literal({}) with an empty dict is how the
    # 3 existing r.literal sites blank a subtree.
    # Missing intermediate objects are CREATED (ReQL does; jsonb_set does not).

def encode_patch(patch: Any) -> Any: ...
    # Rewrites Literal(v) into the wire sentinel {"__db_literal__": v} so the
    # patch can travel as one jsonb parameter. Raises if a real document key
    # named "__db_literal__" is encountered.
```

SQL twin, created by `schema.ensure_schema()`:

```sql
CREATE OR REPLACE FUNCTION jsonb_deep_merge(a jsonb, b jsonb)
RETURNS jsonb LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
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
$$;
```

`a` NULL (row absent) is handled by the caller, not the function. The recursive `'{}' ||`
branch exists so a `__db_literal__` nested inside a new subtree is still unwrapped.

**Property test (mandatory, blocks merge.py sign-off):** for 10,000 Hypothesis-generated
`(base, patch)` JSON pairs including `Literal` markers, `jsonb_deep_merge` in Postgres and
`deep_merge` in Python return byte-identical `canonical()` output.

### 2.6 schema.py

```python
@dataclass(frozen=True)
class PartitionSpec:
    by: str                    # column name, e.g. "ts"
    interval: str              # pg_partman: "1 month"
    premake: int = 3

@dataclass(frozen=True)
class RetentionSpec:
    field: str                 # doc key or column holding the timestamp
    days_env: str              # env var naming the retention window
    default_days: int | None = None   # None => retention OFF by default

@dataclass(frozen=True)
class TableSpec:
    name: str                             # exact RethinkDB table name
    id_type: Literal["text", "int"] = "text"
    pk_field: str = "id"                  # doc key RethinkDB used as the PK
    pk: tuple[str, ...] = ("id",)         # real columns forming the SQL PK
    indexed_fields: tuple[str, ...] = ()  # doc keys -> STORED generated col + btree
    compound_indexes: Mapping[str, str] = field(default_factory=dict)
                                          # index name -> SQL expression over doc
    time_fields: tuple[str, ...] = ()     # doc keys decoded back to datetime on read
    partitioned: PartitionSpec | None = None
    retention: RetentionSpec | None = None
    notify: bool = True                   # install the pg_notify trigger

TABLES: dict[str, TableSpec]              # keyed by name

def spec(table: str) -> TableSpec: ...    # unknown table => default TableSpec(name=table)
def ensure_schema(*, tables: Iterable[str] | None = None) -> list[str]: ...
    # Idempotent. Creates the db objects, jsonb_deep_merge, notify_row(), then
    # every table/column/index/trigger in TABLES. Returns what it created.
    # Replaces the 21 scattered index_create blocks. Called once at process boot.
def ensure_table(table: str) -> None: ...
    # On-demand path for the handful of sites that create a table lazily
    # (llm_utils prompt cache, backtest_evidence_runtime, kalshi ensure_tables).
```

`ensure_schema()` runs under an advisory lock (`pg_advisory_lock(hashtext('intellistock.ddl'))`)
so concurrent process boots do not race on `CREATE INDEX`.

`TABLES` holds an entry only for a table that needs something beyond the default shape — a
non-`id` primary key, an index, a partition, a retention policy, an int id, time fields, or
suppressed notifications. Roughly 40 of the 125 tables qualify; the other ~85 are created from
the default template by name, with no entry to maintain. Registry entries carry only what the
code proves it needs. Examples:

```python
TableSpec("BacktestResults", id_type="int",
          indexed_fields=("status", "instance_id", "timestamp"),
          compound_indexes={
            "instance_or_instance_id":
              "(coalesce(doc->>'instance_id', doc->>'instance', '')) COLLATE \"C\"",
            "list_ts": "(coalesce(doc->>'timestamp','')) COLLATE \"C\"",
            "instance_ts":
              "((coalesce(doc->>'instance_id', doc->>'instance','')) COLLATE \"C\", "
              " (coalesce(doc->>'timestamp','')) COLLATE \"C\")",
          })

TableSpec("GraphNexusTradeContexts",
          indexed_fields=("instance_id", "base_instance_id", "date"),
          compound_indexes={
            "instance_base": "(split_part(doc->>'instance_id', '|', 1)) COLLATE \"C\"",
          },
          retention=RetentionSpec(field="date", days_env="RETAIN_TRADE_CONTEXTS_DAYS"))

TableSpec("PriceHistory", pk=("ticker", "ts", "id"), notify=False,
          partitioned=PartitionSpec(by="ts", interval="1 month"),
          retention=RetentionSpec(field="ts", days_env="RETAIN_PRICE_HISTORY_DAYS"))

TableSpec("GraphNexusLLMPromptCache", notify=False,
          indexed_fields=("cached_at",),
          retention=RetentionSpec(field="cached_at", days_env="RETAIN_PROMPT_CACHE_DAYS"))
```

`status_norm` is **not** a `BacktestResults` index — it moves to `BacktestProgress` (§5.3).

### 2.7 store.py

Module-level singleton `store`, methods on it. `Doc = dict[str, Any]`.

```python
def get(table: str, row_id: Any) -> Doc | None: ...

def get_all(table: str, *keys: Any, index: str | None = None) -> list[Doc]: ...
    # NO dedupe: get_all("a","a","b") returns 3 rows if all exist.

def insert(table, doc_or_docs: Doc | Sequence[Doc], *,
           conflict: Literal["error","replace","update"] = "error",
           durability: str = "hard") -> InsertResult: ...

def update(table, selector: Any | Selection, patch: Doc) -> WriteResult: ...
    # patch is deep-merged (merge.deep_merge semantics). Literal supported.

def replace(table, row_id: Any, doc: Doc) -> WriteResult: ...

def replace_if(table, row_id: Any, *, when: Predicate | None,
               doc: Doc, insert_if_absent: bool = False) -> Doc | None: ...
    # Atomic compare-and-swap. Returns doc on success, None when `when`
    # did not hold. Backs the 5 r.branch(...) replace sites.

def delete(table, selector: Any | Selection) -> WriteResult: ...

def between(table, lo, hi, *, index: str | None = None,
            left_bound: Literal["closed","open"] = "closed",
            right_bound: Literal["open","closed"] = "open") -> Selection: ...

def filter(table, predicate: Doc | Predicate) -> Selection: ...

def pluck(rows_or_selection, *fields: str) -> list[Doc]: ...
    # Missing keys are OMITTED, never emitted as null.

def order_by(selection, *, index: str | None = None,
             fields: Sequence[Order] = (), desc: bool = False) -> Selection: ...

def limit(selection, n: int) -> Selection: ...
def slice(selection, start: int, end: int) -> Selection: ...
def count(table_or_selection) -> int: ...
def run(selection) -> list[Doc]: ...       # materialise
def iter(selection, *, batch: int = 1000) -> Iterator[Doc]: ...   # server-side cursor

def table_list() -> list[str]: ...
def table_create(name: str, *, primary_key: str = "id") -> bool: ...   # False if it existed
def index_list(table: str) -> list[str]: ...
```

`Selection` is a lazy, immutable query builder (table + WHERE terms + ORDER + LIMIT/OFFSET). It
is never executed until `run`, `iter`, `count`, `delete`, or `update` touches it — matching ReQL,
where `.filter(...).delete()` is one server-side statement, not a fetch-then-delete.

Result types:

```python
@dataclass(frozen=True)
class InsertResult:      # ReQL-shaped; supports result["inserted"] too
    inserted: int = 0
    replaced: int = 0
    unchanged: int = 0
    skipped: int = 0
    errors: int = 0
    first_error: str | None = None
    generated_keys: list[Any] = field(default_factory=list)

@dataclass(frozen=True)
class WriteResult:
    replaced: int = 0
    unchanged: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: int = 0
    first_error: str | None = None
```

Both implement `__getitem__` so existing `result['errors']` / `result.get('replaced', 0)` call
sites port with a name change only.

**Predicate DSL** — deliberately small. It covers the ~40 complex sites and nothing more; a site
that needs anything else gets its own hand-written SQL in the owning module.

```python
class P:                       # builder; all methods return Predicate
    @staticmethod
    def field(key: str) -> FieldRef: ...

# FieldRef methods (each maps to one SQL fragment over doc):
#   .eq(v) .ne(v) .lt(v) .le(v) .gt(v) .ge(v)
#   .default(v)             coalesce(doc->>'k', v)
#   .coerce_to_string()     ->> instead of ->
#   .downcase()             lower(...)
#   .match(regex)           ~ '<regex>'  (anchored prefixes special-cased, §4)
#   .split_nth(sep, n)      split_part(..., sep, n+1)
#   .is_in(seq)             = ANY(%s)
# Combinators: p & q, p | q, ~p
```

`Predicate.to_sql()` returns `(fragment, params)`; predicates are never string-interpolated with
user data.

---

## 3. Schema

### 3.1 Default table

```sql
CREATE TABLE IF NOT EXISTS "<Table>" (
  id          text PRIMARY KEY COLLATE "C",
  doc         jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
```

Table names are preserved exactly, quoted everywhere (`"BacktestResults"`, `"kalshi_decisions"`).
This is what lets `clear_instance_state.py`'s 18-target inventory and
`frozen_paired_state._ALLOWED_STATE_TABLES` (26 tables) port by name with no mapping layer.

`id` is `text` for every table, including the int-keyed ones. `TableSpec.id_type` drives coercion
(§4, "int id coercion").

Tables whose RethinkDB primary key is not `id` (the entries in `KALSHI_TABLES` keyed on
`fixture_id`, `market_ticker`, `client_order_id`, `window`, `instance_id`) keep `id` as the
physical PK column; the store copies `doc[TableSpec.pk_field]` into it on insert, exactly as
RethinkDB derives the PK from the named field. The named field also stays in `doc`, so documents
are unchanged. `get(table, key)` looks up `id`. Writing a document that lacks its `pk_field`
raises `StoreError`, matching RethinkDB's rejection.

### 3.2 Generated columns and indexes

Every field that is a secondary index, filter key, or prefix-scan key today becomes a **STORED**
generated column plus a B-tree. `STORED` is written explicitly: PG 18 makes `VIRTUAL` the
default and virtual columns cannot be indexed.

```sql
ALTER TABLE "<Table>"
  ADD COLUMN IF NOT EXISTS "<f>" text COLLATE "C"
  GENERATED ALWAYS AS (doc ->> '<f>') STORED;
CREATE INDEX IF NOT EXISTS "<Table>_<f>_idx" ON "<Table>" ("<f>");
```

For a column that also serves prefix scans, a second index with `text_pattern_ops`:

```sql
CREATE INDEX IF NOT EXISTS "<Table>_<f>_pfx" ON "<Table>" ("<f>" text_pattern_ops);
```

`COLLATE "C"` is on the column *and* implied by `text_pattern_ops`, so `LIKE 'x|%'` and
`ORDER BY <f>` are both bytewise and index-backed.

Compound and functional indexes are expression indexes reproducing the ReQL lambdas verbatim:

| ReQL index | defined at | Postgres |
|---|---|---|
| `instance_or_instance_id` | `interactive_utils.py:377` | `((coalesce(doc->>'instance_id', doc->>'instance','')) COLLATE "C")` |
| `list_ts` | `create_backtest_list_indices.py:53` | `((coalesce(doc->>'timestamp','')) COLLATE "C")` |
| `instance_ts` | `:61` | `(coalesce(doc->>'instance_id',doc->>'instance','') COLLATE "C", coalesce(doc->>'timestamp','') COLLATE "C")` |
| `status_norm` | `:55` | on `"BacktestProgress"`: `((CASE WHEN lower(status) LIKE 'paused%' THEN 'paused' ELSE lower(status) END) COLLATE "C")` |

No GIN anywhere. `WHERE doc->>'k' = v` cannot use GIN, and `fastupdate=on` produces
multi-hundred-ms insert stalls.

### 3.3 Notify trigger

```sql
CREATE OR REPLACE FUNCTION notify_row() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('tbl:' || TG_TABLE_NAME,
                    COALESCE(NEW.id, OLD.id));
  RETURN NULL;
END $$;

CREATE TRIGGER "<Table>_notify"
AFTER INSERT OR UPDATE OR DELETE ON "<Table>"
FOR EACH ROW EXECUTE FUNCTION notify_row();
```

Guardrails encoded here: the payload is the id only (never data — the 8000-byte cap), the channel
is `tbl:<name>` (longest table name is `GraphNexusActiveEventMaintenance` = 32 bytes, well inside
the silent 63-byte truncation), and nothing is delivered until commit.

The trigger is installed where `TableSpec.notify` is true. It is set **false** on the eight
high-volume append-only tables nothing watches — `PriceHistory`, `AlpacaBarsCache`,
`GraphNexusLLMPromptCache`, `LLMUsage`, `GraphNexusNewsLLMMacro`, `GraphNexusNewsLLMCompany`,
`GraphNexusNewsDayFeatures`, `GraphNexusOutcomeSeries` — because a `pg_notify` per row across
2.85M `PriceHistory` inserts is pure cost. See §12.

### 3.4 PriceHistory

A partitioned table's primary key must contain the partition key, and a generated column cannot
be part of a primary key. `PriceHistory` therefore gets real columns:

```sql
CREATE TABLE "PriceHistory" (
  ticker      text COLLATE "C" NOT NULL,
  ts          timestamptz NOT NULL,
  id          text COLLATE "C" NOT NULL,
  doc         jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (ticker, ts, id)
) PARTITION BY RANGE (ts);

CREATE INDEX "PriceHistory_id_idx" ON "PriceHistory" (id);
```

`id` is in the PK because today's ids are uuid4 and `(ticker, ts)` is not unique. Monthly
partitions are managed by pg_partman's background worker (`retention_keep_table = false`,
premake 3). **No DEFAULT partition** — a default partition forbids `DETACH CONCURRENTLY`, which
also cannot run inside a transaction block. Rows land in the right partition because the store
writes `ts` from `doc['timestamp']` on insert; a row whose timestamp will not parse is rejected
with a `StoreError` rather than silently dropped.

`store.get("PriceHistory", id)` is supported through `PriceHistory_id_idx` but is used only by
the migration verifier; the application reads by `(ticker, ts)` range.

### 3.5 The BacktestResults split

Today three concurrent writers rewrite one multi-MB document: a heartbeat every 15 s that
re-sends the last 500 log lines (`broker.py:12173-12193`), a progress writer every +2% that
rewrites `backtest_decisions` and `backtest_refusals` **in their entirety**
(`broker.py:17802-17866`), and a terminal write (`broker.py:12933/12936`). The live sample is
3.1 MB while still running, 89% of it `backtest_decisions`. On Postgres that is ~8 GB of WAL and
~4M dead TOAST chunks per backtest. Splitting is not optional.

**Three tables.**

```sql
CREATE TABLE "BacktestResults" (
  id          text PRIMARY KEY COLLATE "C",
  doc         jsonb NOT NULL,          -- every non-step key, verbatim
  updated_at  timestamptz NOT NULL DEFAULT now()
);
-- STORED generated columns off doc, each with a btree:
--   backtest_id, instance_id, instance, start_date, end_date,
--   started_at, created_at, timestamp, completed_at, tickers_total
-- plus expression indexes instance_or_instance_id / list_ts / instance_ts (§3.2)

CREATE TABLE "BacktestSteps" (
  backtest_id text NOT NULL COLLATE "C",
  kind        text NOT NULL COLLATE "C",   -- decision|refusal|trade|pv|log|price
  seq         bigint NOT NULL,
  final       boolean NOT NULL DEFAULT false,
  doc         jsonb NOT NULL,
  PRIMARY KEY (backtest_id, kind, final, seq)
);
CREATE INDEX "BacktestSteps_bid_idx" ON "BacktestSteps" (backtest_id);

CREATE TABLE "BacktestProgress" (
  id                   text PRIMARY KEY COLLATE "C",   -- = BacktestResults.id
  status               text COLLATE "C",
  progress             double precision,
  time_elapsed_seconds integer,
  last_active          timestamptz,
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX "BacktestProgress_status_norm_idx" ON "BacktestProgress"
  (((CASE WHEN lower(status) LIKE 'paused%' THEN 'paused' ELSE lower(status) END)
    COLLATE "C"));
```

`BacktestResults.doc` holds **every key that is not one of the six step arrays**, verbatim and
unsplit — `strategy_schema`, `evidence`, benchmark fields, per-symbol P&L dicts, scalars. Keeping
`doc` verbatim rather than promoting summary fields into real columns is what makes byte-identical
assembly provable: nothing has to be re-derived. The list endpoint still avoids detoasting `doc`
because it reads only generated columns, which are physically separate.

`BacktestSteps` is insert-only during a run. `final=true` rows are written once by the terminal
writer.

**Writer changes.**

| writer | today | after |
|---|---|---|
| stub (`backtest_engine.py:927-966`, `broker.py:12120/12133`) | `insert(stub, conflict='replace')` with 4 empty arrays | `insert` the metadata doc **without** the six arrays; `upsert BacktestProgress(status, progress=0)` |
| heartbeat (`broker.py:12173-12193`) | update `_last_active`, `time_elapsed_seconds`, 500 logs | `UPDATE "BacktestProgress" SET last_active, time_elapsed_seconds`; new log lines appended to `BacktestSteps(kind='log')` since the last watermark. Scalars only in the hot row. |
| progress (`broker.py:17802-17866`) | rewrites 5 arrays in full | `UPDATE "BacktestProgress" SET status,progress,time_elapsed_seconds,last_active`; `UPDATE "BacktestResults" SET doc = jsonb_deep_merge(doc, patch)` for the scalar/tickers part; **append only the entries past each kind's watermark** into `BacktestSteps` |
| terminal (`broker.py:12933/12936`) | full-blob write | `assert_secret_free()` unchanged; metadata → `doc`; each final array → `BacktestSteps(final=true)` in one `COPY`; `BacktestProgress` set to terminal status |

The broker keeps per-kind in-process watermarks (`_steps_written[kind]`), reset to 0 on the
stub write. A reconnect re-reads `SELECT kind, max(seq) FROM "BacktestSteps" WHERE backtest_id=%s
GROUP BY kind` so a resumed writer never duplicates or skips.

**Assembly — reconstructing the legacy JSON, byte for byte.**

```python
_STEP_KEYS = (           # order fixed; drives the SELECT, not the output order
    ("decision", "backtest_decisions", None),
    ("refusal",  "backtest_refusals",  None),
    ("trade",    "backtest_trades",    ("tail", 1000)),
    ("pv",       "portfolio_value_history", ("downsample", 3000)),
    ("log",      "logs",               ("tail", 500)),
    ("price",    "backtest_prices",    None),
)

def assemble(backtest_id: str) -> dict | None:
    row = store.get("BacktestResults", backtest_id)
    if row is None:
        return None
    doc = dict(row)                                  # verbatim metadata
    steps = _fetch_steps(backtest_id)                # {kind: (final_rows, live_rows)}
    for kind, key, cap in _STEP_KEYS:
        final_rows, live_rows = steps.get(kind, ((), ()))
        if final_rows:                               # terminal write happened
            values = [s for s in final_rows]         # ORDER BY seq, no cap
        else:
            values = _apply_cap([s for s in live_rows], cap)
        if values or key in _ALWAYS_PRESENT:
            doc[key] = values
    prog = store.get("BacktestProgress", backtest_id)
    if prog is not None:                             # hot row wins over doc
        doc["status"] = prog["status"]
        doc["progress"] = prog["progress"]
        if prog["time_elapsed_seconds"] is not None:
            doc["time_elapsed_seconds"] = prog["time_elapsed_seconds"]
        if prog["last_active"] is not None:
            doc["_last_active"] = _iso(prog["last_active"])
    return dict(sorted(doc.items()))
```

Rules this encodes, each of which is a fidelity requirement, not a preference:

- **Presence, not emptiness, is the contract.** `_ALWAYS_PRESENT = {"portfolio_value_history",
  "backtest_trades", "backtest_prices", "logs"}` — the four arrays the stub creates empty at
  `backtest_engine.py:952-955`, so they exist from the first read exactly as today.
  `backtest_decisions` / `backtest_refusals` appear only once written, matching today.
- **Caps are applied at read time while running, and not at all once final.** The live path
  reproduces the legacy truncations exactly: trades tail-1000, logs tail-500, portfolio history
  through `broker_snapshot_helpers.downsample_history(..., 3000)`. The finished path returns the
  terminal writer's arrays whole, because that is what the legacy terminal write stored.
- **`seq` is the only ordering.** `ORDER BY seq` under the composite PK; ties are impossible
  because `seq` is per `(backtest_id, kind, final)`.
- **Key order is lexicographic.** `dict(sorted(...))` matches RethinkDB's lexicographic `keys()`,
  so a naive `json.dumps(doc)` produces the same bytes as before. Every fingerprint independently
  goes through `json.canonical`, so the ordering is belt-and-braces, not the guarantee.
- **The progress overlay is last.** A stale `status` inside `doc` can never win over the hot row.

**Readers.**

- List fast path (`interactive_utils.py:5215-5273`): the `_slim()` `pluck+merge` becomes a single
  SQL statement reading generated columns joined to `BacktestProgress`, with the ticker preview
  (`_LIST_TICKER_PREVIEW`) and `tickers_total` computed as
  `jsonb_path_query_array(doc,'$.tickers[0 to N]')` and `jsonb_array_length` — never in Python
  over a detoasted `doc`. Active rows come from the `status_norm` index on `BacktestProgress`.
  The active-then-page merge, dedupe, and page-1-only pinning logic stay byte-identical.
- The slow path (`:5279-5300`) is deleted. It exists only as a fallback for missing indices;
  `ensure_schema()` guarantees them.
- The seven bare `.get(bid)` detail/playback sites (`:1440, 5827, 6039, 6085, 6316, 6841, 6875`)
  call `assemble(bid)`. `:6841` (portfolio history only) and `:6875` (status) get narrow helpers
  `assemble_field(bid, "portfolio_value_history")` and `store.get("BacktestProgress", bid)` so
  they stop paying for the whole document.
- `self_learning_engine.py:531`'s server-side `.pluck({"new_val":["id","status"]})` becomes a
  watch on `BacktestProgress` (`id`, `status`), which is the hot row — the projection's whole
  purpose.
- `interactive_utils.py:6811-6814` (cross-table best-per-strategy over the entire table) becomes
  a generated-column-only `SELECT`.

### 3.6 Cache tables

`GraphNexusLLMPromptCache`, `LLMUsage`, `AlpacaBarsCache`, `GraphNexusNews*`, `EarningsLLMCache`,
`NewsLLMCache` keep the default `(id, doc, updated_at)` shape, gain a
`created_at timestamptz GENERATED ALWAYS AS ((doc->>'cached_at')::timestamptz) STORED` (or the
table's own timestamp key) with a B-tree, and a `RetentionSpec` whose `default_days` is `None` —
**retention is OFF until the operator sets the env var.** `scripts/pg_retention.py` reads the
registry and issues batched ranged deletes; it never runs an unindexed `DELETE`.

Per-table autovacuum tuning ships in the DDL for the four largest tables — the 0.2 default scale
factor means 570,050 dead tuples on `PriceHistory` before autovacuum reacts:

```sql
ALTER TABLE "<T>" SET (autovacuum_vacuum_scale_factor = 0.02,
                       autovacuum_analyze_scale_factor = 0.01,
                       toast.autovacuum_vacuum_scale_factor = 0.02);
```

---

## 4. Semantics mapping: ReQL → store

Every construct the inventory found, with its implementation and its edge-case rule. This table
is the acceptance criteria for `backend/tests/db/test_store_semantics.py`; each row gets at least
one test.

| ReQL | sites | Postgres implementation | edge-case rule |
|---|---|---|---|
| `.get(pk)` | 359 + 42 | `SELECT doc FROM "T" WHERE id = %s` | missing row → `None`, never `{}` |
| `.get_all(a, b, index=f)` | 17 + 2 | `SELECT t.doc FROM unnest(%s::text[]) WITH ORDINALITY k(v,ord) JOIN "T" t ON t."f" = k.v ORDER BY k.ord` | **no dedupe** — duplicate keys yield duplicate rows, matching ReQL; `= ANY()` would collapse them |
| `.get_all(*ids)` variadic | `clear_instance_state.py:300` | same, on `id` | an empty id set must still produce a valid empty selection (today's `"__no_match_sentinel__"` trick is deleted; `WHERE false` replaces it) |
| `.insert(doc)` | 141 + 4 | `INSERT ... ON CONFLICT` per `conflict=` | see below |
| `conflict='error'` | default | `ON CONFLICT DO NOTHING`, then compare affected rows | **not** a silent no-op: rows that did not insert are counted into `errors` with `first_error` set to ReQL's wording, `"Duplicate primary key `id`"`. ReQL records the conflict without aborting the batch. |
| `conflict='replace'` | `backtest_engine.py:966`, `broker.py:12120/12133` | `ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc, updated_at = now()` | replaces the whole document, including dropping keys |
| `conflict='update'` | `self_learning/store.py` | `DO UPDATE SET doc = jsonb_deep_merge("T".doc, EXCLUDED.doc)` | **not `||`** — `||` is shallow and silently drops sibling keys |
| multi-doc insert | `WRITE_CHUNK = 500` | one statement per chunk, each row in a **savepoint** | ReQL multi-insert is partial-success; a plain multi-row `INSERT` is all-or-nothing. Savepoint-per-row preserves `{inserted: 498, errors: 2, first_error: ...}`. Chunking stays at 500. |
| `.update(patch)` | 130 + 19 | `UPDATE "T" SET doc = jsonb_deep_merge(doc, %s::jsonb) WHERE ...` | objects merge recursively, arrays and scalars replace, `None` sets JSON null, missing intermediates are created |
| `r.literal(v)` | 3 | patch carries `{"__db_literal__": v}`; `jsonb_deep_merge` unwraps and sets shallow | `r.literal({})` blanks a subtree — the exact case `purge_backtest_secrets.py:101-103` needs |
| `.replace(doc)` | 1 | `UPDATE ... SET doc = %s` | replaces wholesale |
| `.replace(lambda row: r.branch(cond, new, row))` | 5 (`benchmark_alpha/rethink_store.py:185`, `nexus_runtime_state.py:231`, `live_risk_state.py:578`, `live_state.py:359`, `broker.py:5891`) | `store.replace_if(...)`: `UPDATE "T" SET doc=%s WHERE id=%s AND <predicate>` and report `replaced = rowcount` | this is a compare-and-swap; `replaced == 0` must mean "predicate did not hold", never "row missing" conflated with it — `replace_if` distinguishes them |
| `.delete()` | 25 + 6 | `DELETE ... WHERE <selection>` | on a `Selection`, one statement — never fetch-then-delete |
| `.between(lo, hi, index=f)` | 1 + prefix scans | `WHERE f >= %s AND f < %s` | **never SQL `BETWEEN`.** ReQL is `[lo, hi)`; SQL `BETWEEN` is `[lo, hi]`. `right_bound='closed'` emits `<=`; `left_bound='open'` emits `>`. |
| `r.minval` / `r.maxval` | `interactive_utils.py:5246` | omit the bound entirely | `[v, minval]`→`[v, maxval]` on a compound index becomes `WHERE instance = %s` with no timestamp bound |
| `.between(p, p+"￿", right_bound="closed")` | `clear_instance_state.py:327-333` | `WHERE f >= %s AND f < %s` with `hi = prefix + '￿'`, **and** a `LIKE` form for the `text_pattern_ops` index: `WHERE f LIKE %s` with `escape_like(prefix) || '%'` | both forms must return the same rows; the store picks `LIKE` when a `_pfx` index exists, the range form otherwise. Under `COLLATE "C"` the two agree by construction. `_` and `%` in a prefix are escaped. Re-verify per table: `clear_instance_state.py:100-104` records that exact-only matching once found **zero** scoped rows and made a full clear a silent no-op. |
| `.filter({dict})` | 48 + 30 | `WHERE doc->>'k' = %s AND ...` | a dict value of `None` means the key is JSON null, not absent |
| `.filter(lambda row: ...)` | ~40 | Predicate DSL (§2.7) | `row["k"]` on a missing key raises in ReQL; `.default(v)` is what the code always writes. The DSL has no undefaulted field access — `P.field("k")` without `.default()` compiles to `doc->>'k'`, which is SQL NULL, and any comparison with NULL is false. That matches the observed behaviour of every ported site (all of them use `.default`). |
| `r.row["origin"].default("") != "backtest"` | `clear_instance_state.py:377` | `coalesce(doc->>'origin','') <> 'backtest'` | the `origin_not_backtest` "special" criterion keeps its name in `build_targets` |
| `.default(x).match("^prefix")` | `clear_instance_state.py:398` | `coalesce(doc->>'f','') LIKE %s` with the escaped prefix | today's code escapes `|` as `[|]` for the regex; the `LIKE` form escapes `%`, `_`, `\` instead. `|` needs no escaping in `LIKE`. |
| `.default(...).coerce_to("string")` | `interactive_utils.py:5287-5292` | `coalesce(doc->>'instance_id', doc->>'instance','')` | `->>` already stringifies; a JSON number `5` becomes `'5'`, matching `coerce_to("string")`. This is also how the 592-NUMBER/833-STRING `instance_id` split stops mattering (§4, int coercion). |
| `.split("|").nth(0)` | `interactive_utils.py:7170/7231/7278` | `split_part(doc->>'instance_id', '|', 1)` | `split_part` is 1-based; `nth(0)` → index 1. On a value with no separator both return the whole string. |
| `.pluck(*fields)` | 4 + 1 | `SELECT` the named `doc` keys, build the dict **skipping absent keys** | ReQL omits missing fields; `jsonb_build_object('a', doc->'a')` would yield `{"a": null}` and flip every `if 'a' in result`. Implemented in Python over `doc - (all_keys - wanted)` so absence is preserved. `pluck` on a nested spec `{"new_val": ["id","status"]}` recurses. |
| `.merge(lambda row: {...})` | 1 (`interactive_utils.py:5232`) | not implemented in the store | the only site is the `_slim` ticker projection, which §3.5 replaces with explicit SQL |
| `.order_by(index=f)` / `r.desc` | 2 + chained | `ORDER BY f COLLATE "C" DESC` | **`COLLATE "C"` is mandatory.** `graph_nexus_analysis.py:11856`'s `(latest_observation_date DESC, id DESC)` decides membership of a `.limit(80)` window feeding an LLM prompt. A non-bytewise collation silently changes results. Numeric order fields cast explicitly: `(doc->>'f')::numeric`. |
| `.limit(n)` / `.slice(a,b)` | 9 + 1 | `LIMIT n` / `LIMIT b-a OFFSET a` | an unordered `LIMIT` has no defined order in either store; the one such site (`graph_nexus_analysis.py:4364`) is diagnostic-only and stays as-is |
| `.count()` | 3 + 6 | `SELECT count(*)` | returns `int` |
| `r.expr(list).contains(doc[f])` | 3 (`kalshi/db.py:385`, `earnings.py:178`, `graph_nexus_analysis.py:4824`) | `doc->>'f' = ANY(%s)` via `P.field(f).is_in(seq)` | empty list → `false`, not an error |
| `r.branch(c, a, b)` | 8 + 1 | `CASE WHEN ... THEN ... ELSE ... END` in the index expression or predicate | the `status_norm` index and the 5 CAS sites are the only uses |
| `r.now()` | 34 | `now()` in SQL for column defaults; `to_jsonb(now())` for doc fields | server-side clock preserved. `now()` is **transaction start** time, not statement time — every one of the 34 sites writes in its own short transaction, so the distinction is invisible; the store never batches two `r.now()` writes into one transaction. |
| `r.now().to_epoch_time()` | `interactive_utils.py:560` | `extract(epoch from now())` | returns a float, as today |
| `r.epoch_time(x)` | 2 | `to_timestamp(%s)` | |
| RethinkDB TIME pseudotype | wire type | stored in `doc` as an **ISO-8601 string with offset** (`2026-08-22T03:37:00.123456+00:00`) | `TableSpec.time_fields` names the keys the store decodes back to timezone-aware `datetime` on read, so callers that relied on driver `datetime` objects are unchanged. Anything not in `time_fields` stays a string — which is what most of the code already sees, since most timestamps were written as ISO strings by Python. |
| NaN / Infinity | — | `json.dumps(allow_nan=False)` raises `ValueError` at the client | RethinkDB rejects NaN client-side today with a clear error. Without this the same bug surfaces as a server-side `invalid input syntax for type json` from a different layer, in a system computing indicators over talib warmup windows. |
| int primary keys | `Instances`, `BacktestResults`, `BacktestInstances`, … | stored as `text`; `TableSpec.id_type="int"` makes the store coerce on the way in (`str(int(v))`) and on the way out (`int(v)`) | `get(t, 460555)` and `get(t, "460555")` must return the same row. Non-integer input to an `id_type="int"` table raises `StoreError` rather than silently creating a shadow row. |
| `BacktestResults.instance_id` NUMBER on 592 rows / STRING on 833 | — | **left as-is inside `doc`** | the generated column `coalesce(doc->>'instance_id', doc->>'instance','')` coerces for indexing, exactly as the ReQL index does. Rewriting the doc values would change 592 documents' bytes and break every fingerprint taken before cutover. |
| `durability='hard'` / `'soft'` | 2 (`broker.py:8039`, `:8423`) | accepted and ignored | Postgres is durable by default; the parameter stays in the signature so the `backtest_replay` interface and its ~10 test doubles are unchanged |
| `noreply_wait=False`, `conn.close(...)` | 21 | no-op / pool release | |
| `r.args`, `r.uuid`, `r.js`, `r.do`, `return_changes`, `eq_join`, ReQL `group`/`reduce` | 0 each | not implemented | if a port needs one, that is a signal the site is being rewritten rather than ported |
| `.table_create` / `.index_create` / `.index_wait` | 21 + 2 | `schema.ensure_table` / `ensure_schema` | idempotent; `index_wait` is a no-op (`CREATE INDEX` is synchronous) |
| ReQL 100k array limit | 0 defences | **no equivalent** | queries that fail loudly today would quietly return huge result sets. `store.run()` raises `StoreError` above `PG_MAX_ROWS` (default 100,000) to preserve the loud failure. `store.iter()` is the explicit unbounded path, used by the migration script, `pg_retention.py`, `clear_instance_state`'s PK materialisation, and `assemble()`'s step fetch — every site that legitimately streams more than 100k rows. |

---

## 5. Change notification

### 5.1 Behaviour contract

`watch.py` helpers deliver ReQL-shaped changes: `{"old_val": <doc|None>, "new_val": <doc|None>}`.

- **Re-read on start.** Before delivering anything, the watcher reads current state and emits it
  as `{"old_val": None, "new_val": doc}` when `include_initial=True` (the default for row and
  filter watches, matching `self_learning_engine.py:534`'s `include_initial=True`), or seeds its
  cache silently when `include_initial=False` (matching `kalshi/backtest_worker.py:256`).
- **Re-read on every reconnect.** Not just reopen. This is the fix for the 8 event-losing sites
  and the 3 with no reconnect at all: after any `LISTEN` connection loss the watcher re-reads the
  watched set, diffs against its cache, and emits changes for anything that moved while it was
  blind. `backtest_engine._sweep_pending` semantics generalised.
- **`old_val` from cache.** The trigger payload is an id only. The watcher keeps the last-seen doc
  per watched id and computes `old_val` from it. On the very first sighting of a row after a
  restart, `old_val` is `None` — same as a fresh feed today. This is what
  `broker.py:5730-5732` and `:5786` need to diff `strategy_id` and `crypto_config`.
- **Deletion.** A missing row after a notification emits `{"old_val": cached, "new_val": None}`,
  then drops the cache entry. `broker.py:5833` depends on `new_val is None` meaning "queue row
  deleted".
- **Squash.** `squash=True` coalesces notifications for the same id inside a `squash_window`
  (default 1.0 s) and delivers one change with the oldest `old_val` and the newest `new_val`.
- **Poll backstop.** Every `poll_interval` seconds (default 2.0, env `DB_WATCH_POLL_SECONDS`) the
  watcher re-reads its watched set and diffs, regardless of notifications. A missed `NOTIFY`
  therefore costs at most one poll interval. Today's recovery latency is 2–30 s
  (`rethink_changefeed.py:120-126`) or a fixed 5 s, so 2 s is strictly faster everywhere.
- **Never dies.** Handler exceptions are logged and swallowed. Connection loss reconnects with
  capped exponential backoff (2 s → 30 s, ×1.5), backoff reset only after a change is *delivered*
  — the existing policy, kept verbatim.
- **`LISTEN` hygiene.** Dedicated unpooled autocommit connection; `notifies()` generator only,
  never mixed with `add_notify_handler()`; `LISTEN` re-issued after every reconnect; a finite
  `timeout=` treated as a resync tick; never routed through pgbouncer transaction pooling.

Whole-table watches over small control tables (`EngineControl` 8 rows, `Instances` 10,
`Config`, `LiveState` 5, `BacktestInstances` 1) do not need notifications to be correct: the
poll-and-diff alone is correct by construction. Notifications only reduce latency.

### 5.2 API

```python
@dataclass
class Watcher:
    def start(self) -> None: ...        # spawns the daemon thread
    def stop(self, timeout: float = 5.0) -> None: ...
    def is_alive(self) -> bool: ...

def watch_row(table: str, row_id: Any, on_change: Callable[[Change], None], *,
              label: str, include_initial: bool = True, squash: bool = False,
              poll_interval: float = 2.0, log: Log | None = None,
              should_continue: Callable[[], bool] | None = None) -> Watcher: ...

def watch_table(table: str, on_change: Callable[[Change], None], *,
                label: str, fields: Sequence[str] | None = None,
                include_initial: bool = True, squash: bool = False,
                poll_interval: float = 2.0, log: Log | None = None,
                should_continue: Callable[[], bool] | None = None) -> Watcher: ...
    # `fields` projects old_val/new_val down to those doc keys — the
    # replacement for the server-side .pluck at self_learning_engine.py:534.

def watch_filter(table: str, predicate: Doc | Predicate,
                 on_change: Callable[[Change], None], *, label: str,
                 **kwargs) -> Watcher: ...
```

`Change` is a plain `dict` with `old_val`/`new_val`, so handlers written against ReQL changes
port with no shape edit.

### 5.3 The 23 sites

| # | file:line | watches | helper |
|---|---|---|---|
| 1 | `instance.py:373` | `Instances.get(id)` | `watch_row("Instances", id, ...)` |
| 2 | `instance.py:666` | `Instances.get(id)` | `watch_row` |
| 3 | `server.py:1211` | `EngineControl` table | `watch_table("EngineControl", ...)` |
| 4 | `server.py:1478` | `EngineControl` table | `watch_table` |
| 5 | `server.py:1519` | `EngineControl` table | `watch_table` |
| 6 | `server.py:1691` | `EngineControl` table | `watch_table` |
| 7 | `server.py:1799` | `EngineControl` table | `watch_table` |
| 8 | `server.py:1894` | `Config.get('Pings')` | `watch_row("Config","Pings",...)` |
| 9 | `server.py:1906` | `Instances` table | `watch_table("Instances", ...)` |
| 10 | `priceBroker.py:76` | `Config.get('Pings')` | `watch_row` |
| 11 | `priceBroker.py:102` | `Config.get('Config')` | `watch_row` |
| 12 | `broker.py:5728` | `Instances.get(id)` | `watch_row`, handler diffs `old_val`/`new_val` for `strategy_id`, `crypto_config`, `crypto_config.strategy` — **highest-risk site**, the BUG #6 class |
| 13 | `broker.py:5784` | `Strategies.get(id)` | `watch_row` |
| 14 | `broker.py:5833` | `BacktestInstances.get(row_id)` | `watch_row`; **gains a reconnect it does not have today** |
| 15 | `kalshi/backtest_worker.py:256` | `KalshiBacktests` status=pending | `watch_filter(..., include_initial=False)`; the existing `pending_or_running_backtests` re-drain stays |
| 16 | `engines/discover_engine.py:68` | `Config.get('Pings')` | `watch_row` |
| 17 | `engines/discover_engine.py:106` | `EngineControl.get('discover_engine')` | `watch_row` |
| 18 | `engines/daily_digest_engine.py:479` | `EngineControl.get(DIGEST)` | `watch_row`; the `:497-500` re-raise-non-replica-errors block is deleted (the watcher never dies) |
| 19 | `engines/price_engine.py:71` | `Config.get('Pings')` | `watch_row`; **gains a reconnect** |
| 20 | `engines/price_engine.py:92` | `EngineControl.get('price_engine')` | `watch_row`; **gains a reconnect** |
| 21 | `engines/price_engine.py:150` | `LivePricesStocks` table | `watch_table`; **gains a reconnect**, and moves off the main thread |
| 22 | `engines/self_learning_engine.py:531` | `BacktestResults` table, plucked `(id,status)` | `watch_table("BacktestProgress", fields=("status",), squash=True, include_initial=True)` — the hot row *is* the projection; the persisted `processed_run_ids` watermark stays |
| 23 | `engines/backtest_engine.py:861` | `BacktestInstances` table | `watch_table`; `_sweep_pending` becomes the watcher's re-read hook, keeping the dedupe against `_queued_or_active_ids` |

Not ported: `rethink_changefeed.py:84` (docstring) and `engine_control.py:159 engine_changes()`
(deleted).

### 5.4 `run_reconnecting_changefeed`

The public signature is unchanged so its ~6 call sites and
`backend/tests/test_changefeed_selfheal.py` port with an import change only:

```python
def run_reconnecting_changefeed(open_feed, handle_change, label, *, get_conn,
                                log=None, pass_conn=True, initial_delay=2.0,
                                max_delay=30.0, sleep=time.sleep,
                                should_continue=None): ...
```

What changes is the contract of the two callables:

- `open_feed(conn)` must return an **iterator of change dicts**. Callers stop writing
  `lambda c: r.db(DB).table(T).changes().run(c)` and write
  `lambda c: watch.feed(T, include_initial=True)` — `watch.feed(...)` returns a blocking
  generator over the same `Change` dicts a `Watcher` delivers.
- `get_conn` is called for the "connection", which for the Postgres implementation is
  `pool.listen_connection()`; `pass_conn=True` still hands it to the handler, and handlers that
  used it to issue queries now ignore it and call `store` directly. `pass_conn` is kept so no
  call site's arity changes in the same commit as its body.

`is_transient_rethinkdb_error` is renamed `is_transient_db_error` (the old name kept as an alias
for one release) and reclassified against `psycopg.OperationalError`,
`psycopg_pool.PoolTimeout`, `OSError`/`ConnectionError`, plus the same substring hints minus the
four RethinkDB-specific ones.

---

## 6. Testing

### 6.1 Strategy

Two tiers, chosen by whether `PG_TEST_DSN` is set.

- **`PG_TEST_DSN` set** → tests run against a real local Postgres 17. Every semantics test in
  §4, the merge property test, the watch tests, and the split round-trip **require** this tier
  and `skip` without it. Only real Postgres can prove collation, `jsonb_deep_merge`, and
  `LISTEN/NOTIFY`.
- **`PG_TEST_DSN` unset** → an in-process `FakeStore` implementing the identical `store` API over
  Python dicts, with the same merge (`merge.deep_merge`, shared code, not a re-implementation),
  the same no-dedupe `get_all`, the same `pluck` omission rule, and bytewise ordering. It exists
  so the ~475 existing tests keep running on a laptop with no database, not to validate the
  store.

A shared `conftest.py` fixture replaces the ad-hoc `monkeypatch.setattr(iu, "r", fake_r)` pattern
in the ~30 test files that stub RethinkDB today:

```python
@pytest.fixture
def store(request):
    """Real Postgres when PG_TEST_DSN is set, else FakeStore. Each test gets a
    fresh schema (real: a per-test schema dropped on teardown; fake: a fresh
    instance)."""
```

Each real-PG test runs in its own Postgres *schema* (`SET search_path`), created and dropped per
test, so tests are parallel-safe and never share state.

### 6.2 New suite: `backend/tests/db/`

| file | proves |
|---|---|
| `test_merge_property.py` | 10k Hypothesis pairs: `jsonb_deep_merge` ≡ `deep_merge`, including `Literal` |
| `test_store_semantics.py` | one test per row of §4 |
| `test_store_conflict.py` | partial-success shapes for all three `conflict=` modes across a 500-doc chunk with 2 duplicates |
| `test_collation.py` | `ORDER BY id COLLATE "C" DESC` reproduces RethinkDB's order on a fixture of real scope-suffixed ids (`alpaca-main\|<hash>\|<date>\|<ticker>`), including `\|` vs alphanumerics |
| `test_prefix_scan.py` | the `LIKE` form and the `>=/<` form return identical row sets for every prefix in `clear_instance_state`'s 18 targets, on a fixture that includes scoped ids — the `:100-104` silent-no-op regression test |
| `test_watch.py` | start re-read, reconnect re-read after a forced connection kill, `old_val` caching, deletion, squash, `include_initial=False`, poll backstop with NOTIFY suppressed |
| `test_schema_ensure.py` | `ensure_schema()` is idempotent (run twice, second run creates nothing) and concurrent-safe (two threads) |
| `test_backtest_split.py` | **the round-trip gate**: a document → split into metadata + steps → `assemble()` → `canonical()` byte-identical to the input, at every lifecycle stage (stub, running, paused, stopped, errored, finished). The checked-in fixture is **synthetic**, built to the documented shape (4.3k decisions, 542 pv points, 500 logs, a 28 KB `strategy_schema`) and gzipped — a real 3.1 MB document is not committed. `BACKTEST_SPLIT_FIXTURE=<path>` runs the same assertions against an operator-supplied real export, `assert_secret_free`-checked first. |
| `test_migration_script.py` | the migration script against a fixture RethinkDB dump: row counts, canonical hashes, split correctness, resumability (kill mid-table, rerun) |
| `test_pool_fork.py` | a forked child does not inherit a usable pool and creates its own |

### 6.3 Local infrastructure

`scripts/dev_pg.sh` — a throwaway cluster, no root, no Docker:

```
dev_pg.sh up      # initdb into .devpg/, start on a free port, create the DB,
                  # print the DSN; enables lz4 check + prints the verdict
dev_pg.sh dsn     # echo PG_TEST_DSN
dev_pg.sh psql    # shell in
dev_pg.sh down    # stop
dev_pg.sh nuke    # stop + rm -rf .devpg
```

Primary path: `brew install postgresql@17` (binaries only; `dev_pg.sh` runs its own cluster in
`.devpg/`, never Homebrew's service). Fallback when Homebrew is unavailable:
`pip install pgserver`, which vendors a Postgres binary. `dev_pg.sh` picks whichever it finds and
says which. `.devpg/` goes in `.gitignore`.

CI, if added later, uses the same script — there is one way to get a test database.

---

## 7. Data migration

### 7.1 `scripts/migrate_rethinkdb_to_postgres.py`

The only file in the runtime tree allowed to import `rethinkdb`, and it imports it lazily inside
the function that needs it.

```
--tables T1,T2      restrict (default: all 125 from table_list())
--since-id ID       resume a table mid-stream
--batch N           COPY batch size (default 2000; 200 for BacktestResults)
--verify            verify only, no writes
--verify-sample P   fraction of rows to hash-compare (default 0.05)
--dry-run
```

Behaviour:

1. `ensure_schema()` first, so every target table, column, index, and trigger exists.
2. Per table, page by primary key with `.between(last_id, None, left_bound='open').limit(batch)`
   — never `skip()`, which is O(n²) on RethinkDB.
3. Run the driver with `time_format='raw'` and convert RethinkDB TIME pseudotypes to ISO-8601
   with offset before serialising. The driver's `native` default yields `datetime` objects
   `json.dumps` cannot serialise.
4. `COPY "T" (id, doc) FROM STDIN` per batch, through `psycopg.Copy`, with `json.dumps` from
   `db.json` so NaN is rejected here rather than at the far end.
5. **`BacktestResults` is split during the copy**: the six arrays are extracted, written to
   `BacktestSteps` with `final=true` and `seq` = position in the source array, and the remaining
   metadata goes into `doc`. A `BacktestProgress` row is written from `status`, `progress`,
   `time_elapsed_seconds`, `_last_active`.
6. **`PriceHistory`** gets `ticker` and `ts` promoted to columns; partitions for the observed
   `ts` range are pre-created before the copy.
7. Idempotent and resumable per table: a `_migration_state` table records
   `(table, last_id, rows_copied, finished_at)`; a rerun continues from `last_id`. Rows are
   copied with `ON CONFLICT (id) DO UPDATE`, so a re-run of a partial batch is a no-op.

### 7.2 Verification

`--verify` reports, per table, and exits non-zero on any mismatch:

- **Row count**, RethinkDB vs Postgres.
- **Canonical hash** on a sampled fraction: `canonical_sha256(rethink_doc)` vs
  `canonical_sha256(assembled_pg_doc)` — `assemble()` for `BacktestResults`, plain `doc`
  otherwise. Timestamps are normalised to UTC ISO on both sides first; nothing else is
  normalised, because anything else that differs is a bug.
- **Index parity**: every ReQL secondary index has a Postgres counterpart.
- **Ordering parity**: for each table in `_ALLOWED_STATE_TABLES`, the first 200 ids in
  `ORDER BY id` must match between the two stores. This is the collation check on real data.
- **The 26-table fingerprint**: `paired_state_attest`'s fingerprint over
  `_ALLOWED_STATE_TABLES` computed against each store must be equal, with `_VOLATILE_FIELDS`
  excluded as it does today.

Mismatches are written whole (both documents) to `.migration-mismatches/<table>/<id>.json`,
never summarised into a counter.

### 7.3 `docs/runbooks/postgres-cutover.md`

Ordered, with a stop condition at each step:

1. **Pre-flight.** Backend redeployed from this branch but still pointed at RethinkDB.
   `SHOW default_toast_compression`. Confirm `shm_size`, `mem_limit`, `shared_buffers`.
   Record the RethinkDB row counts.
2. **Freeze.** Stop every instance and engine. No market hours. Weekend.
3. **Export/import.** `migrate_rethinkdb_to_postgres.py` full run. Expect ~16 GB.
4. **Verify.** `--verify --verify-sample 1.0` on the 26 `_ALLOWED_STATE_TABLES`,
   `--verify-sample 0.05` elsewhere. **Stop on any mismatch.**
5. **Flip.** `docker compose up -d postgres`; set `PG_DSN`; restart backend. RethinkDB container
   stays up but unreferenced.
6. **Smoke.** List page renders; a backtest starts, progresses, and stops on request; an instance
   start/stop round-trips; `clear-state` on a scratch instance reports non-zero
   `would_delete` for the scoped tables (the `:100-104` regression).
7. **Re-certify (the user's gate).** One paired **cold** A/A and one **warm** A/A under
   `scripts/run_paired_experiment.py`. Both must be byte-identical with 100% traded-name overlap,
   the same bar as bt 479057/193668. Explicitly verify the
   `(latest_observation_date DESC, id DESC)` window under `COLLATE "C"` — the single most likely
   silent failure in the migration.
8. **Rollback.** Unset `PG_DSN`, restart. RethinkDB is untouched and still authoritative for
   everything written before the freeze; anything written after the flip is lost, which is why
   step 7 runs before any real-money instance is restarted.
9. **Decommission** (a later, separate decision): stop the RethinkDB container, keep the volume
   for 30 days, then drop it.

`alpaca-main` (Strategies doc 179, real money) is restarted **last**, after every other instance
has run a full clean weekly cycle on Postgres.

---

## 8. Docker compose

Added service:

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

`shared_buffers=1GB` is 25% of the 4G limit. `shm_size: 1gb` is required — the Docker default
`/dev/shm` is 64 MB and is *not* `shared_buffers`; parallel scans fail with a confusing error.

Also:

- Every service that declares `RETHINKDB_HOST`/`RETHINKDB_PORT` gains
  `POSTGRES_HOST=postgres`, `POSTGRES_PORT=5432`, `POSTGRES_DB`, `POSTGRES_USER`,
  `POSTGRES_PASSWORD`. `INSTANCE_RETHINKDB_HOST` gains an `INSTANCE_POSTGRES_HOST` twin, and
  `server.py`'s spawned-container env builder passes it through.
- `depends_on` gains `postgres` with `condition: service_healthy` where the service already used
  a condition.
- The `rethinkdb` service is left in place, unreferenced by any code, until the operator
  decommissions it. Deleting it is not part of this branch.
- Unlike RethinkDB, Postgres is **not** exposed on `0.0.0.0` by default. `POSTGRES_BIND_ADDR`
  defaults to `127.0.0.1`; an operator who needs Tailscale access sets it explicitly.
  `backend/tests/test_docker_compose_security.py` gains an assertion for this.
- `backend/requirements.txt`: `rethinkdb` is removed; `psycopg[binary,pool]>=3.2.10,<4` added.
  ≥3.2.10 is the floor for the `notifies()` memory-leak fix.

---

## 9. scripts/ triage

191 files in `scripts/`, 15 in `backend/scripts/`, ~115 ReQL call sites between them.

**Ported** — a script is ported if it is referenced by a runbook, a cron, `docs/`, or CI, or if
its name says it is a recurring operation (`create_*_indices`, `purge_*`, `check_*`, `diag_*`,
`run_paired_experiment`, `clear_backtest_state`, `pg_retention`). Porting means replacing ReQL
with `db.store` and nothing else.

**Archived** — everything else moves verbatim to `scripts/archive_rethinkdb/` with no edits.
These are one-shot historical migrations and investigations
(`apply_round2_2026_07.py`, dated backfills, one-off fixups). `scripts/archive_rethinkdb/README.md`
states: these ran once against RethinkDB, they are kept for provenance, they do not run against
Postgres, and porting one means reading it first.

The triage list is produced mechanically and committed as
`docs/superpowers/specs/2026-08-22-postgres-port-script-triage.md` before any script is touched,
so the split is reviewable as data rather than as 191 diffs.

`scripts/create_backtest_list_indices.py` and `scripts/create_clear_state_indices.py` are
**deleted**, not ported: `schema.ensure_schema()` subsumes both.

---

## 10. Execution plan

### 10.1 Build order

Modules first, sequential, each with its tests green before the next starts. This is the only
sequential part.

1. `errors.py`, `json.py` — no dependencies.
2. `merge.py` + `test_merge_property.py`. **Gate: the property test passes against real PG.**
3. `pool.py` + `test_pool_fork.py`.
4. `schema.py` + `test_schema_ensure.py`. The 125-entry registry is built from
   `01-repo-inventory.md` §3's index list plus a `grep` of every `index_create` site.
5. `store.py` + `test_store_semantics.py` + `test_store_conflict.py` + `test_collation.py` +
   `test_prefix_scan.py`. **Gate: every row of §4 has a passing test.**
6. `watch.py` + `test_watch.py`.
7. `FakeStore` + the shared `conftest.py` fixture. **Gate: the 475 existing tests still pass
   with the fixture in place and RethinkDB still in the tree.**

### 10.2 Parallelisable file groups

After step 7 the call-site port fans out. Groups are chosen so no two agents touch the same file,
and each group is one worktree, one Opus agent, TDD, one commit.

| group | files | ~sites |
|---|---|---|
| A | `interactive_utils.py` | 280 |
| B | `strategies/graph_nexus_analysis.py` | 181 |
| C | `engines/nexus_graph_engine.py` | 126 |
| D | `broker.py`, everything except the BacktestResults writers — **runs after E**, same file | ~50 |
| E | **BacktestResults split**: `broker.py:12110-12200`, `:17795-17870`, `:12933/12936`, `engines/backtest_engine.py:927-966`, and `assemble()` — one agent, because these four sites are one contract | ~20 |
| F | `self_learning/store.py`, `self_learning/retention.py` | 64 |
| G | `api/main.py`, `auth_utils.py`, `chatbot/conversations.py` | ~74 |
| H | `kalshi/*` | 39 |
| I | `server.py`, `instance.py`, `priceBroker.py`, `engine_control.py` | ~40 |
| J | `engines/{backtest,discover,price,daily_digest,self_learning}_engine.py` — `backtest_engine.py` **after E**, same file | ~40 |
| K | `live_state.py`, `live_risk_state.py`, `nexus_runtime_state.py`, `live_boot_audit.py`, `llm_telemetry.py`, `strategy_cache_persistence.py`, `nexus_lookback_db.py` | ~60 |
| L | `backtest_replay.py`, `benchmark_alpha/rethink_store.py`, `backtest_evidence_runtime.py`, `experiment_registry.py` | ~30 |
| M | `clear_instance_state.py`, `frozen_paired_state.py`, `paired_state_attest.py`, `nexus_config_identity.py` | ~25 |
| N | `llm_utils.py` prompt cache, `backtest_critical_abort.py` | ~15 |
| O | `scripts/` triage + ported scripts | ~115 |

Two ordering constraints break the fan-out, and only two:

- **E owns `broker.py` and `engines/backtest_engine.py` first.** D (the rest of `broker.py`) and
  J (the rest of `backtest_engine.py`) run on top of E's commit, because a file cannot have two
  agents. E itself starts only after §3.5's assembly tests exist.
- **M must land before any re-certification**, because it routes every fingerprint through
  `json.canonical_sha256`. It can run in parallel with everything else.

Every other group is independent and touches a disjoint file set.

Before editing any symbol, each agent runs `gitnexus_impact({target, direction:"upstream"})` and
reports the blast radius; HIGH/CRITICAL is escalated, not overridden. Each agent runs
`gitnexus_detect_changes()` before its commit.

### 10.3 Bug sweep

Four adversarial Opus reviewers in parallel after the port lands, each with one lens and no
authority to fix:

1. **Semantics traps.** Walk §4 row by row against the actual diff. Hunt: `||` where
   `jsonb_deep_merge` belongs; SQL `BETWEEN`; `= ANY` where multiplicity matters; `pluck`
   emitting nulls; a missing `COLLATE "C"`; an `int` id compared to a `text` id.
2. **Changefeed fidelity.** All 23 sites: does each one re-read on reconnect, does each handler
   still see the `old_val` it diffs, does any watcher sit inside a transaction, does any handler
   exception kill a thread.
3. **BacktestResults round-trip.** Assembly against real documents at every lifecycle stage:
   stub, mid-run, paused, stopped, errored, finished. Presence of empty arrays. Cap application.
   Key order. The Flutter-facing payload.
4. **Determinism and collation.** Every ordered read in the diff. The `(latest_observation_date
   DESC, id DESC)` window specifically. Every fingerprint path. Every place a number could be
   renormalised or a key reordered before hashing.

Findings go to a fix pass; the reviewers do not fix their own findings.

Then: `superpowers:verification-before-completion`, PR opened, **not merged**.

---

## 11. Risks and open questions

| # | risk | mitigation / what to check |
|---|---|---|
| 1 | **Collation silently changes results.** The `(latest_observation_date DESC, id DESC)` window feeds an LLM prompt. | `COLLATE "C"` on every text column and every `ORDER BY`. `test_collation.py` on real scope-suffixed ids. The cold + warm A/A is the real gate, and it is the user's. |
| 2 | **Python version split.** Prod image is `python:3.11-slim` (`backend/Dockerfile:2`); this machine is CPython 3.14.5. | psycopg 3.3 supports 3.10–3.13; **3.14 wheel availability is unverified** and is the first thing to check. If `psycopg[binary]` has no 3.14 wheel, either develop against 3.13 via pyenv or build from source locally — prod is unaffected either way. `pgserver`'s 3.14 wheel is likewise unverified; `brew postgresql@17` is the path that does not care about the Python version. Nothing in the repo calls `set_loop_type` or uses the RethinkDB asyncio driver (verified by grep), so **no async pool is needed** and the pool design is thread-only. |
| 3 | **psycopg version floor.** `notifies()` leaked memory before 3.2.10. | Pin `psycopg[binary,pool]>=3.2.10,<4`. Watchers use `notifies()` only, never `add_notify_handler()`. |
| 4 | **lz4 in the image.** UNVERIFIED whether the PGDG `postgres:17` build has it. | `SHOW default_toast_compression` in `dev_pg.sh up` and in the cutover pre-flight. If absent, drop the `-c default_toast_compression=lz4` flag; pglz is the fallback and costs disk, not correctness. |
| 5 | **`jsonb_deep_merge` recursion cost** on the 758-key doc 179 and the 963-key doc 180. | Benchmark in `test_merge_property.py`; if a single deep merge exceeds 10 ms, materialise the merge in Python and write the whole doc for the handful of large-config tables. Correctness is unaffected either way. |
| 6 | **`pluck` missing-key semantics.** UNVERIFIED against live data. | `test_store_semantics.py` asserts omission. If any live consumer turns out to depend on a present-but-null key, that consumer is the bug and is fixed explicitly. |
| 7 | **Losing the ReQL 100k array limit.** Nothing defends against it today, but nothing proves nothing *relies* on the loud failure either. | `store.run()` raises above `PG_MAX_ROWS=100_000`. `store.iter()` is the explicit unbounded path. |
| 8 | **NOTIFY on every table** costs a `pg_notify` per row on 2.85M-row tables. | `TableSpec.notify=False` on the eight high-volume unwatched tables (§3.3, §12). |
| 9 | **`--verify` on 16 GB is slow.** | Sample 5% by default, 100% for the 26 `_ALLOWED_STATE_TABLES`. Budget an hour. |
| 10 | **No off-box replica.** Whether anything backs RethinkDB up today is unknown and affects whether the RethinkDB volume is the only rollback path. | Ask before step 9 of the runbook. Until answered, keep the volume. |
| 11 | **475 existing tests stub `r` directly** in ~30 files. | The shared `store` fixture lands *before* the port (step 7), so those files change once, mechanically, not twice. |
| 12 | **Scope creep.** Postgres invites a relational redesign, SQL analytics, and better indexes. | Non-goal §1. Any structural improvement beyond the BacktestResults split is a separate branch. |

---

## 12. Interface decisions not in the approved design

Each of these fills a gap the approved design did not specify. Each is reversible; flagged for veto.

1. **`store.replace_if(table, id, when=, doc=, insert_if_absent=)`** — a compare-and-swap
   primitive. The approved §3 API list has no CAS, but five sites use
   `.replace(lambda row: r.branch(...))` for exactly that, including `nexus_runtime_state` and
   `live_risk_state`. Without it those sites lose atomicity.
2. **`P.field(...).is_in(seq)`** added to the predicate DSL. Three sites use
   `r.expr(list).contains(doc[f])`; the approved DSL list (eq/lt/gt/match/default/coerce_to_string/
   split_nth) does not cover it.
3. **`BacktestResults.doc` stays verbatim; the summary fields are STORED generated columns rather
   than promoted real columns.** The approved §2 says "metadata + small summary as real columns +
   doc for the rest". Generated columns give the same list-endpoint performance (they are
   physically separate from `doc`, so reading them does not detoast) and make byte-identical
   assembly provable, because nothing has to be re-derived.
4. **A sixth step kind, `price`**, for `backtest_prices`. The approved set is
   `{decision, refusal, trade, pv, log}`. `interactive_utils.py:5296` documents 9–37k
   `backtest_prices` entries per row; leaving them in `doc` would keep a large array in the hot
   metadata row.
5. **`BacktestSteps.final`** boolean, and the read rule "final rows win, uncapped; otherwise the
   live rows with the legacy cap". Without it, assembly cannot tell a finished run's authoritative
   arrays from the incremental ones and cannot reproduce the legacy truncations.
6. **`status_norm` moves from `BacktestResults` to `BacktestProgress`**, and the list endpoint
   joins the two. Status and progress live in the hot row, so the index must too.
7. **`TableSpec.notify=False` on eight high-volume unwatched tables.** The approved §4 says the
   trigger goes on every table. Nothing watches these, and the trigger costs a `pg_notify` per
   inserted row on `PriceHistory`.
8. **`PriceHistory` primary key is `(ticker, ts, id)`, not `(ticker, ts)`.** Today's ids are
   uuid4 and `(ticker, ts)` is not unique, so the approved PK would reject rows.
9. **`run_reconnecting_changefeed` keeps its signature but narrows `open_feed`'s contract** to
   "returns an iterator of `Change` dicts", supplied by `watch.feed(...)`. A ReQL cursor cannot
   survive the port; this is the smallest change that keeps every call site's arity.
10. **`durability=` is accepted and ignored** rather than mapped to `synchronous_commit`. Postgres
    is durable by default and `broker.py:8423`'s `soft` write is telemetry; mapping it would be a
    behaviour change, not a preservation.
11. **`assemble()` emits `dict(sorted(...))`** — lexicographic key order, matching RethinkDB's
    `keys()`. Fingerprints go through `json.canonical` independently, so this is redundancy, not
    the guarantee.
12. **Postgres binds to `127.0.0.1` by default** in compose, where RethinkDB binds `0.0.0.0`.
    Preserving the old default would export an authenticated database to the network by accident.
