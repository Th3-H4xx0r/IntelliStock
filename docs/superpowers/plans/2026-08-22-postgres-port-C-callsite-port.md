# Postgres Port — Call-Site Port (Plan C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port every remaining ReQL call site in `backend/` (~1,000 sites across 68 files) to `backend.db.store`, every change-feed site (23) to `backend.db.watch`, and delete every `from rethinkdb import` / `RethinkDB()` / `r.db(DB_NAME)` / `get_conn()` from the runtime tree — with byte-identical behaviour.

**Architecture:** Purely mechanical translation. `backend/db/` (Plan A) already provides `store`, `watch`, `pool`, `schema`, `merge`, `errors`, a `FakeStore`, and the shared `store` pytest fixture. This plan consumes that API and rewrites call sites file-group by file-group. Groups are disjoint file sets so each can run in its own git worktree in parallel; only three ordering edges exist (below). No behaviour changes, no refactors beyond the mechanical rewrite, no new features.

**Tech Stack:** Python 3.11 (prod image `python:3.11-slim`), `psycopg[binary,pool]>=3.2.10,<4`, PostgreSQL 17 + JSONB, pytest, Hypothesis, GitNexus MCP for impact analysis.

**Spec:** `docs/superpowers/specs/2026-08-22-postgres-port-design.md`
**Evidence:** `docs/investigations/DB-REPLACEMENT-2026-08-22.md` (Phase 4 = the 23 change-feed sites), repo inventory `01-repo-inventory.md` (call-site census).

## Global Constraints

- **User invariant (binding on every decision):** "Keep all functionality the same completely as using rethinkdb, just a different db." A P&L difference in any backtest is a bug, not a result.
- **Shape fidelity.** Every value reaching Python stays a plain `dict`/`list`/scalar with the same keys present and the same keys **absent**. `pluck` omits missing keys; it never emits `null`.
- **Order fidelity.** Every ordered read reproduces the same total order including ties. String ordering is bytewise: `COLLATE "C"` on every text column and every `ORDER BY`. Numeric order fields cast explicitly: `(doc->>'f')::numeric`.
- **Merge fidelity.** `store.update()` deep-merges objects and replaces arrays; `Literal(v)` sets shallow. Never `||` — it is shallow and silently drops sibling keys.
- **Notification fidelity or better.** All 23 watchers keep working, re-read on start and on every reconnect.
- **Python 3.11 compat.** Prod image is `python:3.11-slim` (`backend/Dockerfile:2`). No `match` guards, `except*`, PEP 695 generics, or 3.12+ stdlib.
- **`COLLATE "C"` everywhere.** No exceptions, no "it looks the same on ASCII".
- **No GIN indexes anywhere.** `WHERE doc->>'k' = v` cannot use GIN and `fastupdate=on` stalls inserts.
- **No `rethinkdb` in the runtime.** After this plan, `grep -rn "rethinkdb" backend --include='*.py'` returns nothing outside `backend/tests/` fixtures and the migration script.
- **Never SQL `BETWEEN`.** ReQL `between` is `[lo, hi)`; SQL `BETWEEN` is `[lo, hi]`. Use `store.between()`.
- **Frontend and mobile are untouched.** Any change visible to Flutter is a bug.
- **Every commit message ends with:**
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
  ```
- **Before editing any symbol** run `gitnexus_impact({target: "<symbol>", direction: "upstream"})` and report the blast radius. HIGH/CRITICAL is escalated to the user, never overridden. **Before every commit** run `gitnexus_detect_changes()`.

---

## Interfaces — Common (every task consumes these)

Copied verbatim from spec §2.7 / §5.2. **Every task's Interfaces block references this section by name and reproduces the subset it uses.** Do not invent methods that are not here; a site needing more gets hand-written SQL in its owning module.

```python
# backend/db/store.py — module-level singleton `store`; Doc = dict[str, Any]
def get(table: str, row_id: Any) -> Doc | None: ...
def get_all(table: str, *keys: Any, index: str | None = None) -> list[Doc]: ...   # NO dedupe
def insert(table, doc_or_docs: Doc | Sequence[Doc], *,
           conflict: Literal["error","replace","update"] = "error",
           durability: str = "hard") -> InsertResult: ...
def update(table, selector: Any | Selection, patch: Doc) -> WriteResult: ...
def replace(table, row_id: Any, doc: Doc) -> WriteResult: ...
def replace_if(table, row_id: Any, *, when: Predicate | None,
               doc: Doc, insert_if_absent: bool = False) -> Doc | None: ...
def delete(table, selector: Any | Selection) -> WriteResult: ...
def between(table, lo, hi, *, index: str | None = None,
            left_bound: Literal["closed","open"] = "closed",
            right_bound: Literal["open","closed"] = "open") -> Selection: ...
def filter(table, predicate: Doc | Predicate) -> Selection: ...
def pluck(rows_or_selection, *fields: str) -> list[Doc]: ...        # missing keys OMITTED
def order_by(selection, *, index: str | None = None,
             fields: Sequence[Order] = (), desc: bool = False) -> Selection: ...
def limit(selection, n: int) -> Selection: ...
def slice(selection, start: int, end: int) -> Selection: ...
def count(table_or_selection) -> int: ...
def run(selection) -> list[Doc]: ...                # raises StoreError above PG_MAX_ROWS (100_000)
def iter(selection, *, batch: int = 1000) -> Iterator[Doc]: ...     # unbounded streaming path
def table_list() -> list[str]: ...
def table_create(name: str, *, primary_key: str = "id") -> bool: ...
def index_list(table: str) -> list[str]: ...
```

```python
# backend/db/merge.py
class Literal:                        # ReQL r.literal(): replace subtree, do not merge
    __slots__ = ("value",)
    def __init__(self, value: Any) -> None: ...
def deep_merge(base: Any, patch: Any) -> Any: ...
def encode_patch(patch: Any) -> Any: ...
```

```python
# backend/db/store.py — Predicate DSL
class P:
    @staticmethod
    def field(key: str) -> FieldRef: ...
# FieldRef: .eq(v) .ne(v) .lt(v) .le(v) .gt(v) .ge(v)
#           .default(v)  .coerce_to_string()  .downcase()
#           .match(regex)  .split_nth(sep, n)  .is_in(seq)
# Combinators: p & q, p | q, ~p
# Predicate.to_sql() -> (fragment, params)     # never string-interpolated
```

```python
# backend/db/store.py — result types (both support result["key"] and .get("key", 0))
@dataclass(frozen=True)
class InsertResult:
    inserted: int = 0; replaced: int = 0; unchanged: int = 0; skipped: int = 0
    errors: int = 0; first_error: str | None = None
    generated_keys: list[Any] = field(default_factory=list)

@dataclass(frozen=True)
class WriteResult:
    replaced: int = 0; unchanged: int = 0; deleted: int = 0; skipped: int = 0
    errors: int = 0; first_error: str | None = None
```

```python
# backend/db/watch.py
@dataclass
class Watcher:
    def start(self) -> None: ...
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

def watch_filter(table: str, predicate: Doc | Predicate,
                 on_change: Callable[[Change], None], *, label: str, **kwargs) -> Watcher: ...

def feed(table: str, *, include_initial: bool = True, row_id: Any = None,
         predicate: Doc | Predicate | None = None) -> Iterator[Change]: ...
# Change is a plain dict: {"old_val": <doc|None>, "new_val": <doc|None>}
```

```python
# backend/db/pool.py
@contextmanager
def connection(*, autocommit: bool = False) -> Iterator[Connection]: ...
def listen_connection() -> Connection: ...     # dedicated, unpooled, autocommit
def reset_after_fork() -> None: ...
def close_pool() -> None: ...
def health() -> dict: ...                      # {"ok": bool, "size": int, "dsn_host": str}
```

```python
# backend/db/schema.py
def ensure_schema(*, tables: Iterable[str] | None = None) -> list[str]: ...
def ensure_table(table: str) -> None: ...
def spec(table: str) -> TableSpec: ...
```

```python
# backend/db/errors.py
class StoreError(Exception): ...
class ConflictError(StoreError): ...
class UnavailableError(StoreError): ...
class CasFailed(StoreError): ...
```

```python
# backend/tests/conftest.py (Plan A)
@pytest.fixture
def store(request): ...
    """Real Postgres when PG_TEST_DSN is set, else FakeStore. Fresh schema per test."""
@pytest.fixture
def fake_watch(request): ...
    """FakeStore-backed watch harness. `fake_watch.notify(table, row_id)` delivers one
    simulated change to every Watcher registered against that table."""
```

---

## Appendix R — The mechanical translation recipe

Every construct the census found in `backend/`, with its before/after. **Each task below names the recipe rows it must apply (`R1`…`R26`); apply exactly those and nothing else.** Where a task has a site-specific twist, that task carries its own before/after in full.

Throughout: `r` / `_r` is the old ReQL module handle, `conn` the old connection, `DB_NAME` the old database name. All three disappear.

### R1 — `.get(pk)` → `store.get`
```python
# before
doc = r.db(DB_NAME).table('Instances').get(instance_id).run(conn)
# after
from db import store
doc = store.get('Instances', instance_id)
```
Missing row returns `None`, never `{}`. `id_type="int"` tables coerce, so `store.get(t, 460555) is store.get(t, "460555")` in value.

### R2 — `.get_all(a, b, index=f)` → `store.get_all`
```python
# before
rows = list(r.db(DB_NAME).table('T').get_all(a, b, index='instance_id').run(conn))
# after
rows = store.get_all('T', a, b, index='instance_id')
```
**No dedupe.** `get_all("a","a","b")` returns 3 rows if all exist. Variadic over the PK: `store.get_all('T', *ids)`. An empty key set must still yield a valid empty result — never the `"__no_match_sentinel__"` trick.

### R3 — `.insert(doc)` → `store.insert(..., conflict='error')`
```python
# before
res = r.db(DB_NAME).table('T').insert(doc).run(conn)
if res.get('errors'): ...
# after
res = store.insert('T', doc)
if res['errors']: ...
```
`conflict='error'` is **not** a silent no-op: rows that did not insert are counted into `errors` with `first_error` = `"Duplicate primary key `id`"`. Multi-doc insert is partial-success (savepoint per row); keep `WRITE_CHUNK = 500`.

### R4 — `.insert(doc, conflict='replace')`
```python
# before
r.db(DB_NAME).table('T').insert(doc, conflict='replace').run(conn)
# after
store.insert('T', doc, conflict='replace')
```
Replaces the whole document, including dropping keys.

### R5 — `.insert(doc, conflict='update')`
```python
# after
store.insert('T', doc, conflict='update')
```
Deep-merges (`jsonb_deep_merge`), **not** `||`.

### R6 — `.update(patch)` → `store.update` (deep merge)
```python
# before
r.db(DB_NAME).table('T').get(rid).update({'a': {'b': 1}}).run(conn)
# after
store.update('T', rid, {'a': {'b': 1}})
```
Objects merge recursively; arrays and scalars replace wholesale; a patch value of `None` sets JSON null (it does **not** delete the key); missing intermediate objects are created.

### R7 — `r.literal(v)` → `Literal(v)`
```python
# before
r.db(DB_NAME).table('T').get(rid).update({'secrets': r.literal({})}).run(conn)
# after
from db import store, Literal
store.update('T', rid, {'secrets': Literal({})})
```
`Literal({})` blanks a subtree. There are exactly 3 sites.

### R8 — `.replace(doc)` → `store.replace`
```python
# after
store.replace('T', rid, doc)
```

### R9 — `.replace(lambda row: r.branch(cond, new, row))` → `store.replace_if` (CAS)
```python
# before
res = (r.db(DB_NAME).table('T').get(key)
       .replace(lambda row: r.branch(row['version'] == expected, new_doc, row))
       .run(conn))
if res['replaced'] != 1:
    raise StateConflict(...)
# after
from db import store, P
saved = store.replace_if('T', key, when=P.field('version').eq(expected), doc=new_doc)
if saved is None:
    raise StateConflict(...)          # predicate did not hold
```
`replace_if` returns the doc on success and `None` when `when` did not hold. It distinguishes "predicate false" from "row missing" — never conflate them. `insert_if_absent=True` covers first-write.

### R10 — `.delete()` → `store.delete`
```python
# before
r.db(DB_NAME).table('T').filter({'instance_id': iid}).delete().run(conn)
# after
store.delete('T', store.filter('T', {'instance_id': iid}))
```
On a `Selection` this is **one** statement — never fetch-then-delete.

### R11 — `.between(lo, hi, index=f)` → `store.between`
```python
# before
cur = r.db(DB_NAME).table('T').between(lo, hi, index='ts').run(conn)
# after
sel = store.between('T', lo, hi, index='ts')     # [lo, hi) by default
rows = store.run(sel)
```
Never SQL `BETWEEN`. `right_bound='closed'` emits `<=`; `left_bound='open'` emits `>`.

### R12 — `r.minval` / `r.maxval` → omit the bound
```python
# before
.between([iid, r.minval], [iid, r.maxval], index='instance_ts')
# after
store.between('BacktestResults', [iid, None], [iid, None], index='instance_ts')
# which compiles to: WHERE instance = %s   (no timestamp bound at all)
```

### R13 — prefix scan `between(p, p + "￿", right_bound="closed")`
```python
# before
high = str(val) + "￿"
sel = r.db(DB_NAME).table(table).between(val, high, index=field, right_bound='closed')
# after
sel = store.between(table, val, None, index=field, prefix=True)
# store emits `WHERE f LIKE %s` with escape_like(prefix) || '%' when a _pfx index
# exists, and `f >= %s AND f < %s` otherwise. Under COLLATE "C" both agree.
```
If `store.between(..., prefix=True)` is not in Plan A's shipped signature, use the explicit form instead — it is equivalent and always available:
```python
sel = store.filter(table, P.field(field).match_prefix(val))
```
`_`, `%`, and `\` in the prefix are escaped by the store. **Regression to re-verify per table:** `clear_instance_state.py:100-104` records that exact-only matching once found **zero** scoped rows and turned a full clear into a silent no-op. Assert non-zero counts on scoped ids.

### R14 — `.filter({dict})` → `store.filter`
```python
# after
sel = store.filter('T', {'status': 'pending'})
rows = store.run(sel)
```
A dict value of `None` means the key is JSON null, not absent.

### R15 — `.filter(lambda row: ...)` → Predicate DSL
```python
# before
.filter(lambda doc: (doc['instance_id'] == iid)
                    & (doc['date_key'].ge(cutoff))
                    & (doc['date_key'].lt(date_key)))
# after
store.filter(OUTCOMES_TABLE,
             P.field('instance_id').eq(iid)
             & P.field('date_key').ge(cutoff)
             & P.field('date_key').lt(date_key))
```
`P.field('k')` without `.default()` compiles to `doc->>'k'`; comparison with SQL NULL is false. A ReQL lambda that used `.default(v)` maps to `P.field('k').default(v)`.

### R16 — `r.row['origin'].default('') != 'backtest'`
```python
# after
P.field('origin').default('').ne('backtest')     # coalesce(doc->>'origin','') <> 'backtest'
```

### R17 — `.default(x).match('^prefix')`
```python
# before
r.row[f].default('').match('^' + re.escape(prefix))
# after
P.field(f).default('').match_prefix(prefix)      # coalesce(doc->>'f','') LIKE escaped||'%'
```
The old code escaped `|` as `[|]` for the regex; the `LIKE` form escapes `%`, `_`, `\` instead — `|` needs no escaping in `LIKE`.

### R18 — `.default(...).coerce_to('string')`
```python
# after
P.field('instance_id').coerce_to_string().default(P.field('instance').coerce_to_string()).default('')
# compiles to: coalesce(doc->>'instance_id', doc->>'instance', '')
```
`->>` already stringifies; JSON number `5` becomes `'5'`.

### R19 — `.split('|').nth(0)`
```python
# after
P.field('instance_id').split_nth('|', 0)         # split_part(doc->>'instance_id', '|', 1)
```
`split_part` is 1-based; `nth(0)` → index 1. On a value with no separator both return the whole string.

### R20 — `.pluck(*fields)` → `store.pluck`
```python
# after
rows = store.pluck(store.run(sel), 'id', 'status')
```
Missing keys are **omitted**, never emitted as `null`. Nested specs recurse: `store.pluck(rows, {'new_val': ['id', 'status']})`.

### R21 — `.order_by(index=f)` / `r.desc` / `r.asc`
```python
# before
.order_by(_r.desc('latest_observation_date'), _r.desc('id')).limit(80)
# after
store.limit(
    store.order_by(sel, fields=('latest_observation_date', 'id'), desc=True),
    80)
# => ORDER BY latest_observation_date COLLATE "C" DESC, id COLLATE "C" DESC LIMIT 80
```
**`COLLATE "C"` is mandatory.** Numeric order fields cast: `fields=(('score', 'numeric'),)` → `(doc->>'score')::numeric`.

### R22 — `.limit(n)` / `.slice(a, b)` / `.count()`
```python
store.limit(sel, n)          # LIMIT n
store.slice(sel, a, b)       # LIMIT b-a OFFSET a
store.count(sel)             # SELECT count(*) -> int
store.count('T')             # whole table
```

### R23 — `r.expr(list).contains(doc[f])`
```python
# before
r.expr(allowed).contains(doc['status'])
# after
P.field('status').is_in(allowed)      # doc->>'status' = ANY(%s); empty list -> false
```

### R24 — `r.now()` / `r.now().to_epoch_time()` / `r.epoch_time(x)`
```python
# before
.update({'updated_at': r.now()})
# after
store.update('T', rid, {'updated_at': store.now()})        # to_jsonb(now()) server-side
# before: r.now().to_epoch_time().run(conn)   after: store.epoch_now()   -> float
# before: r.epoch_time(x)                     after: store.from_epoch(x)
```
`now()` is transaction-start time; every one of the 34 sites writes in its own short transaction, so the distinction is invisible. **The store never batches two `now()` writes into one transaction.**

### R25 — `table_create` / `index_create` / `index_wait` / `table_list` → schema.py
```python
# before
if 'T' not in r.db(DB_NAME).table_list().run(conn):
    r.db(DB_NAME).table_create('T').run(conn)
if 'f' not in r.db(DB_NAME).table('T').index_list().run(conn):
    r.db(DB_NAME).table('T').index_create('f').run(conn)
    r.db(DB_NAME).table('T').index_wait('f').run(conn)
# after
from db import schema
schema.ensure_table('T')          # idempotent; index set comes from schema.TABLES
```
**Delete the whole block.** `schema.py` owns DDL; `index_wait` is a no-op (`CREATE INDEX` is synchronous). If a field the old code created an index for is not in `schema.TABLES[T].indexed_fields`, **add it there in the same commit** — do not leave the index behind.

### R26 — connection lifecycle
```python
# before
conn = get_conn()
try:
    ...
finally:
    conn.close(noreply_wait=False)
# after
# nothing — store methods take their own pooled connection per operation
```
Delete every `get_conn()` definition, every `conn = get_conn()`, every `conn.close(...)`, every `noreply_wait=`. `durability='hard'|'soft'` stays in the signature and is accepted-and-ignored. Where a function's `conn` parameter is load-bearing for a test double's arity, keep the parameter, accept `None`, and ignore it — but never open a connection from it.

### R27 — change feeds → `watch.py`
```python
# before
conn = get_conn()
for change in r.db(DB_NAME).table('Instances').get(instance_id).changes().run(conn):
    old, new = change.get('old_val'), change.get('new_val')
    handle(old, new)
# after
from db import watch
w = watch.watch_row('Instances', instance_id, _on_change,
                    label='instance-config', include_initial=True,
                    should_continue=lambda: not stop_event.is_set())
w.start()
...
w.stop()

def _on_change(change):
    old, new = change.get('old_val'), change.get('new_val')
    handle(old, new)
```
`Change` is a plain dict `{"old_val": ..., "new_val": ...}` so handler bodies do not change shape. `old_val` comes from the watcher's per-id cache; on the first sighting after a restart it is `None`, same as a fresh ReQL feed. A missing row after a notification emits `{"old_val": cached, "new_val": None}`.

For `run_reconnecting_changefeed` call sites, only the `open_feed` lambda changes:
```python
# before
run_reconnecting_changefeed(
    lambda c: r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).changes().run(c),
    handle_change, label='engine-control', get_conn=get_conn, ...)
# after
run_reconnecting_changefeed(
    lambda c: watch.feed(ENGINE_CONTROL_TABLE, include_initial=True),
    handle_change, label='engine-control', get_conn=pool.listen_connection, ...)
```
`pass_conn=True` still hands the connection to the handler; handlers that used it to issue queries now ignore it and call `store` directly.

---

## File Structure — the groups

Each group is **one git worktree, one agent, one commit**. No two groups touch the same file.

| group | files | ReQL sites (`r.db(` count) | depends on |
|---|---|---|---|
| G1 | `server.py`, `engine_control.py`, `priceBroker.py`, `instance.py` | 19 + 7 + 6 + 11 = **43** (incl. 11 feeds) | Plan A |
| G2 | `broker.py` — everything except the BacktestResults writers | **54 total, ~34 in scope** (incl. 3 feeds) | Plan A, **Plan B** |
| G3 | `interactive_utils.py` (3 sub-groups by line range) | **247 total, ~232 in scope** | Plan A, **Plan B** |
| G4 | `strategies/graph_nexus_analysis.py` | **144** | Plan A |
| G5 | `engines/nexus_graph_engine.py` | **17** | Plan A |
| G6 | `engines/{price,discover,daily_digest,self_learning,backtest}_engine.py`, `engines/discord_bot.py` | 6+5+2+3+21+1 = **38** (incl. 6 feeds) | Plan A, **Plan B** (backtest_engine) |
| G7 | `self_learning/store.py`, `self_learning/retention.py` | **62** | Plan A |
| G8 | `kalshi/db.py`, `kalshi/backtest_worker.py`, `kalshi/engine.py`, `kalshi/runner.py`, `kalshi/backtest_data.py`, `kalshi/__init__.py` | 37+3+11+2+2 = **55** (incl. 1 feed) | Plan A |
| G9 | `api/main.py`, `auth_utils.py`, `chatbot/conversations.py`, `chatbot/orchestration.py` | 4+12+14+1 = **31** | Plan A |
| G10 | State & identity core: `nexus_runtime_state.py`, `live_state.py`, `live_risk_state.py`, `experiment_registry.py`, `benchmark_alpha/rethink_store.py`, `benchmark_alpha/watchdog_main.py`, `backtest_replay.py`, `backtest_evidence_runtime.py`, `llm_utils.py`, `clear_instance_state.py`, `paired_state_attest.py`, `frozen_paired_state.py`, `nexus_graph_builds.py`, `backtest_critical_abort.py` | 17+14+2+0+8+1+0+2+0+10+0+0+9+2 = **65** | Plan A, **Plan B** (`backtest_critical_abort.py`) |
| G11 | Long tail: `discover.py`, `cli.py`, `intellistock.py`, `live_boot_audit.py`, `live_boot_setup.py`, `live_broker_fetch.py`, `live_kill_switch.py`, `live_mode_overrides.py`, `llm_telemetry.py`, `model_resolver.py`, `nexus_lookback_db.py`, `nexus_restamp.py`, `point_in_time_registry.py`, `price_utils.py`, `strategy_cache_persistence.py`, `sec_edgar_supply_chain.py`, `benzinga_client.py`, `strategies/earnings.py`, `strategies/google_news.py`, `strategies/ml_news.py`, `strategies/nexus_analyst_panel.py`, `test_graph_hardening.py`, `rethink_changefeed.py` | 9+4+6+1+3+0+3+0+8+1+0+5+6+0+9+4+4+5+4+7+6+0+1 = **86** | Plan A |
| G12 | Final sweep: residual-import gate + the 46 test files that stub `r` | — | **all of G1–G11** |

**Ordering (only these edges exist):**

1. **Plan B lands first** for `broker.py`, `interactive_utils.py`, `engines/backtest_engine.py`, and `backtest_critical_abort.py`. G2, G3, G6, and G10 rebase onto Plan B's commit before starting. A file cannot have two agents.
2. **G12 runs last**, after every other group has merged.
3. Everything else (G1, G4, G5, G7, G8, G9, G11) is fully independent and can run concurrently from the start.

**Not ported / deleted outright:**
- `engine_control.py:159 engine_changes()` — dead helper, zero callers. **Deleted** (G1).
- `rethink_changefeed.py:84` — docstring example only; rewritten to the `watch.feed` form (G11).
- `backend/scripts/*` and `scripts/*` — owned by **Plan D**'s triage, not this plan.

---

### Task 1: G1 — control plane (`server.py`, `engine_control.py`, `priceBroker.py`, `instance.py`)

**Files:**
- Modify: `backend/server.py` (19 sites, incl. 7 `.changes()` at `:1211, :1478, :1519, :1691, :1799, :1894, :1906`; `get_conn()` at `:105`)
- Modify: `backend/engine_control.py` (7 sites; delete `engine_changes()` at `:159`)
- Modify: `backend/priceBroker.py` (6 sites, 2 feeds at `:76, :102`; `get_conn()` at `:35`)
- Modify: `backend/instance.py` (11 sites, 2 feeds at `:373, :666`; `get_conn()` at `:194`)
- Test: `backend/tests/test_control_plane_watch.py` (create)
- Test (existing, must pass): `backend/tests/test_instance_crash_handling.py`, `backend/tests/test_instances_payload.py`, `backend/tests/test_instance_halt_hygiene.py`, `backend/tests/test_changefeed_selfheal.py`

**Constructs present:** R1, R3, R4, R6, R8, R14, R24, R25, R26, R27.

**Interfaces:**
- Consumes (from `Interfaces — Common`): `store.get(table, row_id) -> Doc | None`; `store.insert(table, doc_or_docs, *, conflict='error', durability='hard') -> InsertResult`; `store.update(table, selector, patch) -> WriteResult`; `store.replace(table, row_id, doc) -> WriteResult`; `store.filter(table, predicate) -> Selection`; `store.run(selection) -> list[Doc]`; `store.now()`; `schema.ensure_table(table) -> None`; `pool.listen_connection() -> Connection`; `watch.watch_row(table, row_id, on_change, *, label, include_initial=True, squash=False, poll_interval=2.0, log=None, should_continue=None) -> Watcher`; `watch.watch_table(table, on_change, *, label, fields=None, include_initial=True, squash=False, poll_interval=2.0, log=None, should_continue=None) -> Watcher`; `watch.feed(table, *, include_initial=True, row_id=None, predicate=None) -> Iterator[Change]`; the `store` and `fake_watch` pytest fixtures.
- Produces: nothing new. `server.get_conn`, `priceBroker.get_conn`, `instance.get_conn`, and `engine_control.engine_changes` cease to exist — no other group may import them.

- [ ] **Step 1: Run impact analysis on the symbols this task deletes**

Run, and report each blast radius to the user before editing:
```
gitnexus_impact({target: "get_conn", direction: "upstream"})
gitnexus_impact({target: "engine_changes", direction: "upstream"})
```
Expected: `engine_changes` has zero callers (spec §2.1). If it has any, STOP and escalate — the spec's premise is wrong. `get_conn` will be HIGH: report it, then proceed, because deleting it is the whole point of the task.

- [ ] **Step 2: Write the failing test for the `EngineControl` whole-table watch**

Create `backend/tests/test_control_plane_watch.py`:
```python
import threading
import pytest


def test_engine_control_watch_delivers_initial_then_change(store, fake_watch, monkeypatch):
    import server

    store.insert("EngineControl", {"id": "price_engine", "command": "idle"})
    seen = []
    done = threading.Event()

    def _capture(change):
        seen.append(change)
        if len(seen) >= 2:
            done.set()

    w = server._watch_engine_control(_capture)
    try:
        assert done.wait(0.5) is False        # only the initial change so far
        assert seen == [{"old_val": None,
                         "new_val": {"id": "price_engine", "command": "idle"}}]
        store.update("EngineControl", "price_engine", {"command": "stop"})
        fake_watch.notify("EngineControl", "price_engine")
        assert done.wait(2.0) is True
        assert seen[1]["old_val"]["command"] == "idle"
        assert seen[1]["new_val"]["command"] == "stop"
    finally:
        w.stop()


def test_config_pings_row_watch_reports_deletion(store, fake_watch):
    import priceBroker

    store.insert("Config", {"id": "Pings", "ping": 1})
    seen = []
    w = priceBroker._watch_pings(seen.append)
    try:
        store.delete("Config", "Pings")
        fake_watch.notify("Config", "Pings")
        for _ in range(20):
            if len(seen) >= 2:
                break
            fake_watch.pump(0.1)
        assert seen[-1]["new_val"] is None
        assert seen[-1]["old_val"]["ping"] == 1
    finally:
        w.stop()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_control_plane_watch.py -v`
Expected: FAIL with `AttributeError: module 'server' has no attribute '_watch_engine_control'`.

- [ ] **Step 4: Port `engine_control.py`**

Delete `engine_changes()` (`:159`) entirely. Apply R1/R3/R6/R25/R26 to the remaining 6 sites. Example:
```python
# before
def get_engine_command(engine_id: str, conn=None):
    own = conn is None
    if own:
        conn = get_conn()
    try:
        row = r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).get(engine_id).run(conn)
    finally:
        if own:
            conn.close(noreply_wait=False)
    return (row or {}).get('command')

# after
from db import store

def get_engine_command(engine_id: str, conn=None):
    # `conn` is kept for call-site arity and ignored; the store pools its own.
    row = store.get(ENGINE_CONTROL_TABLE, engine_id)
    return (row or {}).get('command')
```

- [ ] **Step 5: Port `server.py`'s 5 `EngineControl` feeds and 2 row/table feeds**

Delete `get_conn()` (`:105`). Introduce one helper so the five identical `EngineControl` feeds share a definition, then use it at `:1211, :1478, :1519, :1691, :1799`:
```python
from db import pool, store, watch

def _watch_engine_control(on_change, *, label='engine-control', should_continue=None):
    """Whole-table EngineControl watch. Replaces the 5 identical changefeeds."""
    return watch.watch_table(
        ENGINE_CONTROL_TABLE, on_change, label=label,
        include_initial=True, poll_interval=2.0,
        should_continue=should_continue,
    )
```
Each old call site becomes:
```python
# before
run_reconnecting_changefeed(
    lambda c: r.db(DB_NAME).table(ENGINE_CONTROL_TABLE).changes().run(c),
    handle_change, label='engine-control-restart', get_conn=get_conn)
# after
_watch_engine_control(handle_change, label='engine-control-restart').start()
```
`:1894` (`Config.get('Pings')`) → `watch.watch_row('Config', 'Pings', handle_change, label='pings')`.
`:1906` (`Instances` table) → `watch.watch_table('Instances', handle_change, label='instances')`.

Keep every handler body byte-identical — they already read `change['old_val']` / `change['new_val']`.

- [ ] **Step 6: Port `priceBroker.py` and `instance.py`**

`priceBroker.py`: delete `get_conn()` (`:35`); wrap the two feeds:
```python
from db import store, watch

def _watch_pings(on_change):
    return watch.watch_row('Config', 'Pings', on_change, label='pricebroker-pings')

def _watch_config(on_change):
    return watch.watch_row('Config', 'Config', on_change, label='pricebroker-config')
```
Both `.start()` where the old `run_reconnecting_changefeed` calls were, and `.stop()` on the existing shutdown path.

`instance.py`: delete `get_conn()` (`:194`); the two `Instances.get(instance_id).changes()` loops at `:373` and `:666` become:
```python
# before
conn = get_conn()
for change in r.db(DB_NAME).table('Instances').get(instance_id).changes().run(conn):
    _apply_instance_change(change)
# after
w = watch.watch_row('Instances', instance_id, _apply_instance_change,
                    label=f'instance-{instance_id}',
                    should_continue=lambda: not _shutdown.is_set())
w.start()
_shutdown.wait()
w.stop()
```
Apply R1/R3/R6/R25/R26 to the remaining 9 non-feed sites in `instance.py`.

- [ ] **Step 7: Run the new test and verify it passes**

Run: `python3 -m pytest backend/tests/test_control_plane_watch.py -v`
Expected: PASS (2 passed).

- [ ] **Step 8: Run the existing tests for these modules**

Run:
```bash
python3 -m pytest backend/tests/test_instance_crash_handling.py \
  backend/tests/test_instances_payload.py \
  backend/tests/test_instance_halt_hygiene.py \
  backend/tests/test_changefeed_selfheal.py -v
```
Expected: PASS, same test count as on `main`. If a test fails only because it stubbed `r`, port that stub to the `store` fixture now — do not skip it.

- [ ] **Step 9: Verify no residual ReQL in G1's files**

Run: `grep -n "rethinkdb\|r\.db(\|RethinkDB()\|noreply_wait\|index_wait" backend/server.py backend/engine_control.py backend/priceBroker.py backend/instance.py`
Expected: no output.

- [ ] **Step 10: Detect changes and commit**

Run `gitnexus_detect_changes()` and confirm the affected symbol set is limited to these four files.
```bash
git add backend/server.py backend/engine_control.py backend/priceBroker.py \
        backend/instance.py backend/tests/test_control_plane_watch.py
git commit -m "$(cat <<'EOF'
port(G1): control plane off ReQL — server/engine_control/priceBroker/instance

43 call sites and 11 changefeeds to db.store / db.watch. Deletes 3 get_conn()
definitions and the dead engine_changes() helper. The 5 identical EngineControl
feeds collapse into server._watch_engine_control.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 2: G2 — `broker.py` non-BacktestResults sites

**Files:**
- Modify: `backend/broker.py` — all ReQL except the BacktestResults writers Plan B owns (`:12110-12200`, `:12933`, `:12936`, `:17795-17870`). ~34 sites in scope, including 3 feeds at `:5728`, `:5784`, `:5833`; `get_conn()`/`get_conn_retry()` at `:1891/:1897` and `:1931/:1934`; the `r.branch` CAS at `:5891`; `durability=` at `:8039`, `:8423`.
- Test: `backend/tests/test_broker_watch_and_state.py` (create)
- Test (existing, must pass): `backend/tests/test_cache_eviction_protects_risk_state.py`, `backend/tests/test_persistence_safety.py`, `backend/tests/test_momentum_discontinuity_guard.py`

**Depends on:** Plan B's `broker.py` commit. **Rebase onto it before starting.**

**Constructs present:** R1, R3, R6, R8, R9, R10, R14, R21, R22, R24, R25, R26, R27, plus `durability=` (accepted-ignored).

**Interfaces:**
- Consumes: `store.get(table, row_id) -> Doc | None`; `store.insert(table, doc_or_docs, *, conflict='error', durability='hard') -> InsertResult`; `store.update(table, selector, patch) -> WriteResult`; `store.replace(table, row_id, doc) -> WriteResult`; `store.replace_if(table, row_id, *, when, doc, insert_if_absent=False) -> Doc | None`; `store.delete(table, selector) -> WriteResult`; `store.filter(table, predicate) -> Selection`; `store.order_by(selection, *, index=None, fields=(), desc=False) -> Selection`; `store.limit(selection, n) -> Selection`; `store.count(table_or_selection) -> int`; `store.run(selection) -> list[Doc]`; `store.now()`; `P.field(key) -> FieldRef` with `.eq/.ne/.lt/.le/.gt/.ge/.default/.is_in`; `schema.ensure_table(table)`; `watch.watch_row(...) -> Watcher`; `errors.UnavailableError`.
- Produces: nothing new. `broker.get_conn` / `broker.get_conn_retry` cease to exist.

- [ ] **Step 1: Impact analysis**

Run and report:
```
gitnexus_impact({target: "get_conn_retry", direction: "upstream"})
gitnexus_impact({target: "watch_instance_config", direction: "upstream"})
```
`broker.py:5728` is flagged in the spec as the **highest-risk site in the port** — the BUG #6 class, where a running broker went blind to `crypto_config` edits. Report its blast radius verbatim before touching it.

- [ ] **Step 2: Write the failing test for the three broker watchers**

Create `backend/tests/test_broker_watch_and_state.py`:
```python
import pytest


def test_instance_watch_diffs_strategy_and_crypto_config(store, fake_watch):
    """The BUG #6 regression: a running broker must see crypto_config edits."""
    import broker

    store.insert("Instances", {"id": "test", "strategy_id": 179,
                               "crypto_config": {"strategy": "adaptive", "alloc": 0.0}})
    applied = []
    w = broker._watch_instance_config("test", applied.append)
    try:
        assert applied[0]["new_val"]["crypto_config"]["alloc"] == 0.0
        store.update("Instances", "test", {"crypto_config": {"alloc": 0.05}})
        fake_watch.notify("Instances", "test")
        fake_watch.pump(0.5)
        last = applied[-1]
        assert last["old_val"]["crypto_config"]["alloc"] == 0.0
        assert last["new_val"]["crypto_config"]["alloc"] == 0.05
        # deep merge, not replace: the sibling key survived
        assert last["new_val"]["crypto_config"]["strategy"] == "adaptive"
    finally:
        w.stop()


def test_queue_row_deletion_reports_new_val_none(store, fake_watch):
    import broker

    store.insert("BacktestInstances", {"id": "q1", "status": "running"})
    seen = []
    w = broker._watch_queue_row("q1", seen.append)
    try:
        store.delete("BacktestInstances", "q1")
        fake_watch.notify("BacktestInstances", "q1")
        fake_watch.pump(0.5)
        assert seen[-1]["new_val"] is None
    finally:
        w.stop()


def test_risk_state_cas_returns_none_when_predicate_fails(store):
    import broker

    store.insert("LiveRiskState", {"id": "alpaca-main", "version": 3, "halted": False})
    assert broker._cas_risk_state("alpaca-main", expected_version=3,
                                  doc={"id": "alpaca-main", "version": 4,
                                       "halted": True}) is not None
    assert broker._cas_risk_state("alpaca-main", expected_version=3,
                                  doc={"id": "alpaca-main", "version": 5,
                                       "halted": False}) is None
    assert store.get("LiveRiskState", "alpaca-main")["version"] == 4
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_broker_watch_and_state.py -v`
Expected: FAIL with `AttributeError: module 'broker' has no attribute '_watch_instance_config'`.

- [ ] **Step 4: Port the three watchers**

```python
from db import store, watch

def _watch_instance_config(instance_id, on_change):
    return watch.watch_row('Instances', instance_id, on_change,
                           label=f'broker-instance-{instance_id}',
                           include_initial=True)

def _watch_strategy(strategy_id, on_change):
    return watch.watch_row('Strategies', strategy_id, on_change,
                           label=f'broker-strategy-{strategy_id}',
                           include_initial=True)

def _watch_queue_row(row_id, on_change):
    # :5833 gains a reconnect it does not have today.
    return watch.watch_row('BacktestInstances', row_id, on_change,
                           label=f'broker-queue-{row_id}',
                           include_initial=True)
```
Replace the three `for change in r.db(DB_NAME)...changes().run(conn):` loops with `.start()` / wait / `.stop()`. **Handler bodies stay byte-identical** — they already diff `old_val`/`new_val`.

- [ ] **Step 5: Port the `r.branch` CAS at `:5891` (R9)**

```python
# before
res = (r.db(DB_NAME).table('LiveRiskState').get(key)
       .replace(lambda row: r.branch(row['version'] == expected_version, new_doc, row))
       .run(conn))
if res['replaced'] != 1:
    return None
return new_doc

# after
from db import store, P

def _cas_risk_state(key, *, expected_version, doc):
    return store.replace_if('LiveRiskState', key,
                            when=P.field('version').eq(expected_version),
                            doc=doc)
```

- [ ] **Step 6: Port the remaining ~28 sites and delete the connection plumbing**

Delete `get_conn()` at `:1891` and `:1931`. `get_conn_retry(max_attempts, delay)` at `:1897`/`:1934` is deleted too — `pool.connection()` already retries a connection-level failure twice (0.5 s / 1.5 s) and raises `UnavailableError`. **The call sites' own outer retry loops stay unchanged**; only the inner `get_conn_retry` disappears:
```python
# before
conn = get_conn_retry(max_attempts=5, delay=2)
try:
    r.db(DB_NAME).table('GraphNexusGateEvents').insert(event, durability='soft').run(conn)
finally:
    conn.close(noreply_wait=False)

# after
store.insert('GraphNexusGateEvents', event, durability='soft')
```
`durability='soft'` (`:8423`, telemetry) and `durability='hard'` (`:8039`) stay in the call — the store accepts and ignores them. Apply R1/R3/R6/R8/R10/R14/R21/R22/R24/R25/R26 to the rest: gate-event writes, risk-telemetry writes, `Instances`/`Strategies`/`LiveState` reads, the `table_create`/`index_create` guards (→ `schema.ensure_table`).

**Do not touch** `:12110-12200`, `:12933`, `:12936`, `:17795-17870` — Plan B owns them. Verify with `git diff --stat` that those ranges are unchanged.

- [ ] **Step 7: Run the new and existing tests**

Run:
```bash
python3 -m pytest backend/tests/test_broker_watch_and_state.py \
  backend/tests/test_cache_eviction_protects_risk_state.py \
  backend/tests/test_persistence_safety.py \
  backend/tests/test_momentum_discontinuity_guard.py -v
```
Expected: PASS.

- [ ] **Step 8: Verify no residual ReQL outside Plan B's ranges**

Run: `grep -n "rethinkdb\|r\.db(\|RethinkDB()\|noreply_wait" backend/broker.py`
Expected: no output (Plan B's ranges are already ported).

- [ ] **Step 9: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add backend/broker.py backend/tests/test_broker_watch_and_state.py
git commit -m "$(cat <<'EOF'
port(G2): broker.py non-backtest sites off ReQL

~34 call sites, 3 changefeeds (Instances/Strategies/BacktestInstances), the
LiveRiskState compare-and-swap, and the gate-event/risk-telemetry writes.
Deletes both get_conn()/get_conn_retry() pairs; durability= is accepted-ignored.
:5728 keeps its old_val/new_val diff — the BUG #6 regression is under test.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 3: G3a — `interactive_utils.py` lines 1–1560

**Files:**
- Modify: `backend/interactive_utils.py:1-1560` (~82 ReQL sites; `get_conn(host=None, port=None)` at `:30`). **Excludes `:1440`** — a bare `BacktestResults.get(bid)` that Plan B replaced with `assemble(bid)`.
- Test: `backend/tests/test_interactive_utils_store_a.py` (create)
- Test (existing, must pass): `backend/tests/test_interactive_utils_brokerages.py`

**Depends on:** Plan B's `interactive_utils.py` commit. Rebase first.

**Constructs present:** R1, R2, R3, R4, R6, R7, R8, R10, R14, R20, R22, R24, R25, R26.

**Interfaces:**
- Consumes: `store.get(table, row_id) -> Doc | None`; `store.get_all(table, *keys, index=None) -> list[Doc]`; `store.insert(table, doc_or_docs, *, conflict='error', durability='hard') -> InsertResult`; `store.update(table, selector, patch) -> WriteResult`; `store.replace(table, row_id, doc) -> WriteResult`; `store.delete(table, selector) -> WriteResult`; `store.filter(table, predicate) -> Selection`; `store.pluck(rows_or_selection, *fields) -> list[Doc]`; `store.count(table_or_selection) -> int`; `store.run(selection) -> list[Doc]`; `store.table_list() -> list[str]`; `store.now()`; `Literal(value)`; `schema.ensure_table(table)`.
- Produces: `interactive_utils.get_conn` is **deleted**. It is the most-imported `get_conn` in the repo; every importer is inside G1–G11 and ports in its own task.

- [ ] **Step 1: Impact analysis on the deleted symbol**

Run: `gitnexus_impact({target: "get_conn", direction: "upstream"})` scoped to `interactive_utils`. Expected: CRITICAL (widest fan-out in the repo). Report it, list every importer, and confirm each importer's file appears in a G1–G11 group before proceeding. If any importer is outside the group map, add it to G11 and say so.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_interactive_utils_store_a.py`:
```python
def test_get_conn_is_gone():
    import interactive_utils as iu
    assert not hasattr(iu, "get_conn")
    assert not hasattr(iu, "r")


def test_update_deep_merges_nested_config(store):
    import interactive_utils as iu

    store.insert("Strategies", {"id": 179, "config": {"a": 1, "b": {"c": 2, "d": 3}}})
    iu.update_strategy_config(179, {"config": {"b": {"c": 9}}})
    got = store.get("Strategies", 179)
    assert got["config"] == {"a": 1, "b": {"c": 9, "d": 3}}


def test_literal_blanks_a_subtree(store):
    from db import Literal
    import interactive_utils as iu

    store.insert("Strategies", {"id": 180, "secrets": {"k": "v"}, "keep": 1})
    store.update("Strategies", 180, {"secrets": Literal({})})
    got = store.get("Strategies", 180)
    assert got["secrets"] == {}
    assert got["keep"] == 1


def test_pluck_omits_absent_keys(store):
    import interactive_utils as iu

    store.insert("Instances", [{"id": "a", "name": "A", "kind": "equity"},
                               {"id": "b", "name": "B"}])
    rows = store.pluck(store.run(store.filter("Instances", {})), "id", "kind")
    by_id = {row["id"]: row for row in rows}
    assert "kind" not in by_id["b"]      # omitted, NOT null
    assert by_id["a"]["kind"] == "equity"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_interactive_utils_store_a.py -v`
Expected: FAIL — `assert not hasattr(iu, "get_conn")` fails, and `update_strategy_config` still opens a ReQL connection.

- [ ] **Step 4: Delete the module-level ReQL plumbing**

At the top of `interactive_utils.py`:
```python
# before
from rethinkdb import RethinkDB
r = RethinkDB()
DB_NAME = os.environ.get('RETHINKDB_DB', 'IntelliStock')

def get_conn(host=None, port=None):
    return r.connect(host=host or RETHINKDB_HOST, port=port or RETHINKDB_PORT)

# after
from db import store, schema, Literal, P
```
Every `conn = get_conn()` / `conn.close(...)` pair in this range is deleted with it (R26).

- [ ] **Step 5: Port the ~82 sites in lines 1–1560**

Apply the recipe rows mechanically. Representative rewrites:
```python
# R1
inst = store.get('Instances', instance_id)

# R2  (was: .get_all(*ids).run(conn))
rows = store.get_all('Instances', *ids)

# R4  (was: .insert(doc, conflict='replace').run(conn))
store.insert('Instances', doc, conflict='replace')

# R10 (was: .filter({'instance_id': iid}).delete().run(conn))
store.delete('LiveState', store.filter('LiveState', {'instance_id': iid}))

# R25 (was: the table_create / index_create / index_wait guard block)
schema.ensure_table('Instances')

# R24 (was: .update({'updated_at': r.now()}))
store.update('Instances', iid, {'updated_at': store.now()})
```
`store.table_list()` replaces `r.db(DB_NAME).table_list().run(conn)` at every membership check that is *not* part of a create-guard; create-guards collapse to `schema.ensure_table` (R25).

**Do not touch line `:1440`** — Plan B replaced it with `assemble(bid)`.

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest backend/tests/test_interactive_utils_store_a.py backend/tests/test_interactive_utils_brokerages.py -v`
Expected: PASS.

- [ ] **Step 7: Verify the line range is clean**

Run: `sed -n '1,1560p' backend/interactive_utils.py | grep -n "rethinkdb\|r\.db(\|RethinkDB()\|noreply_wait"`
Expected: no output.

- [ ] **Step 8: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add backend/interactive_utils.py backend/tests/test_interactive_utils_store_a.py
git commit -m "$(cat <<'EOF'
port(G3a): interactive_utils.py lines 1-1560 off ReQL

~82 call sites. Deletes the module-level RethinkDB handle and get_conn(), the
repo's most-imported connection factory. Deep-merge, r.literal, and pluck-omits-
absent-keys are each under test.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 4: G3b — `interactive_utils.py` lines 1561–6100

**Files:**
- Modify: `backend/interactive_utils.py:1561-6100` (~81 ReQL sites). **Excludes `:5215-5300`** (the list fast path and the deleted slow path) and `:5827`, `:6039`, `:6085` (detail/playback `assemble(bid)` sites) — all owned by Plan B.
- Test: `backend/tests/test_interactive_utils_store_b.py` (create)

**Depends on:** Plan B's `interactive_utils.py` commit **and Task 3 (G3a)** — same file, sequential.

**Constructs present:** R1, R2, R3, R6, R8, R10, R11, R12, R14, R15, R18, R21, R22, R24, R25.

**Interfaces:**
- Consumes: `store.get`, `store.get_all`, `store.insert`, `store.update`, `store.replace`, `store.delete`, `store.filter`, `store.between(table, lo, hi, *, index=None, left_bound='closed', right_bound='open') -> Selection`, `store.order_by(selection, *, index=None, fields=(), desc=False) -> Selection`, `store.limit(selection, n)`, `store.slice(selection, start, end)`, `store.count`, `store.run`, `store.iter(selection, *, batch=1000)`, `P.field(key)` with `.eq/.ne/.lt/.le/.gt/.ge/.default/.coerce_to_string/.match_prefix/.split_nth/.is_in`, `store.now()`, `store.epoch_now()`.
- Produces: nothing new.

- [ ] **Step 1: Impact analysis**

Run `gitnexus_impact({target: "<symbol>", direction: "upstream"})` for each top-level function you are about to edit in this range (list them first with `grep -n "^def " backend/interactive_utils.py | awk -F: '$1>1560 && $1<=6100'`). Report any HIGH/CRITICAL to the user.

- [ ] **Step 2: Write the failing test for the `r.minval`/`r.maxval` and `.between` semantics**

Create `backend/tests/test_interactive_utils_store_b.py`:
```python
def test_between_is_half_open_not_sql_between(store):
    for i, ts in enumerate(["2026-01-01", "2026-01-02", "2026-01-03"]):
        store.insert("BacktestResults", {"id": str(i), "instance_id": "x",
                                         "timestamp": ts})
    sel = store.between("BacktestResults", "2026-01-01", "2026-01-03",
                        index="list_ts")
    got = sorted(row["timestamp"] for row in store.run(sel))
    assert got == ["2026-01-01", "2026-01-02"]      # NOT the 03 row


def test_minval_maxval_drop_the_bound_entirely(store):
    store.insert("BacktestResults", [
        {"id": "1", "instance_id": "x", "timestamp": "2026-01-01"},
        {"id": "2", "instance_id": "y", "timestamp": "2026-01-02"},
    ])
    sel = store.between("BacktestResults", ["x", None], ["x", None],
                        index="instance_ts")
    assert [row["id"] for row in store.run(sel)] == ["1"]


def test_coerce_to_string_bridges_number_and_string_instance_ids(store):
    store.insert("BacktestResults", [
        {"id": "1", "instance_id": 5},        # NUMBER, as 592 live rows have
        {"id": "2", "instance": "5"},         # legacy key, STRING
    ])
    pred = (P.field("instance_id").coerce_to_string()
            .default(P.field("instance").coerce_to_string()).default(""))
    rows = store.run(store.filter("BacktestResults", pred.eq("5")))
    assert sorted(row["id"] for row in rows) == ["1", "2"]
```
Add `from db import store, P` at the top of the file.

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_interactive_utils_store_b.py -v`
Expected: FAIL — the `list_ts` / `instance_ts` expression indexes do not yet exist for the fixture, or `store.between` is unreachable from this module.

- [ ] **Step 4: Port the range's ordered reads and range scans**

```python
# R11/R12 — the r.minval/r.maxval site at :5246 (outside Plan B's :5215-5300?
# it is INSIDE; if your diff touches :5246, stop — that line is Plan B's.)
# Every OTHER between in this range:
sel = store.between('PriceHistory', lo, hi, index='ts')

# R21 — ordered read
sel = store.order_by(store.filter('BacktestResults', {'instance_id': iid}),
                     fields=('timestamp',), desc=True)
rows = store.run(store.limit(sel, 50))

# R18 — the instance_or_instance_id projection
pred = (P.field('instance_id').coerce_to_string()
        .default(P.field('instance').coerce_to_string()).default(''))
sel = store.filter('BacktestResults', pred.eq(str(instance_id)))

# R24 — epoch clock
now_epoch = store.epoch_now()          # was r.now().to_epoch_time().run(conn)
```

- [ ] **Step 5: Port the remaining sites in the range**

Apply R1/R2/R3/R6/R8/R10/R14/R15/R22/R25 to the rest. Any read that can legitimately exceed 100,000 rows uses `store.iter(sel)` rather than `store.run(sel)` — `store.run` raises `StoreError` above `PG_MAX_ROWS` to preserve ReQL's loud 100k-array failure.

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest backend/tests/test_interactive_utils_store_b.py -v`
Expected: PASS.

- [ ] **Step 7: Verify the line range is clean**

Run: `sed -n '1561,6100p' backend/interactive_utils.py | grep -n "rethinkdb\|r\.db(\|r\.minval\|r\.maxval\|noreply_wait"`
Expected: no output.

- [ ] **Step 8: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add backend/interactive_utils.py backend/tests/test_interactive_utils_store_b.py
git commit -m "$(cat <<'EOF'
port(G3b): interactive_utils.py lines 1561-6100 off ReQL

~81 call sites. between() stays half-open, r.minval/r.maxval become dropped
bounds, and the coerce_to_string bridge over the 592-NUMBER/833-STRING
instance_id split is under test.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 5: G3c — `interactive_utils.py` lines 6101–end

**Files:**
- Modify: `backend/interactive_utils.py:6101-8851` (~84 ReQL sites), including the three `.split('|').nth(0)` sites at `:7170`, `:7231`, `:7278`. **Excludes `:6316`, `:6841`, `:6875`** (Plan B's `assemble` / `assemble_field` / `BacktestProgress` sites) and `:6811-6814` (Plan B's cross-table best-per-strategy SELECT).
- Test: `backend/tests/test_interactive_utils_store_c.py` (create)

**Depends on:** Plan B's `interactive_utils.py` commit **and Task 4 (G3b)** — same file, sequential.

**Constructs present:** R1, R2, R3, R6, R8, R10, R14, R15, R19, R20, R21, R22, R24, R25.

**Interfaces:**
- Consumes: `store.get`, `store.get_all`, `store.insert`, `store.update`, `store.replace`, `store.delete`, `store.filter`, `store.pluck`, `store.order_by`, `store.limit`, `store.count`, `store.run`, `store.iter`, `P.field(key).split_nth(sep, n)`, `P.field(key).default(v)`, `store.now()`, `schema.ensure_table`.
- Produces: nothing new. After this task `interactive_utils.py` has zero ReQL.

- [ ] **Step 1: Impact analysis**

List the functions in the range (`grep -n "^def " backend/interactive_utils.py | awk -F: '$1>6100'`) and run `gitnexus_impact` upstream on each. Report HIGH/CRITICAL.

- [ ] **Step 2: Write the failing test for `split_nth`**

Create `backend/tests/test_interactive_utils_store_c.py`:
```python
from db import store, P


def test_split_nth_matches_reql_split_pipe_nth_zero(store):
    store.insert("GraphNexusTradeContexts", [
        {"id": "1", "instance_id": "alpaca-main|abc123"},
        {"id": "2", "instance_id": "alpaca-main"},        # no separator
        {"id": "3", "instance_id": "test|xyz"},
    ])
    pred = P.field("instance_id").split_nth("|", 0).eq("alpaca-main")
    rows = store.run(store.filter("GraphNexusTradeContexts", pred))
    assert sorted(row["id"] for row in rows) == ["1", "2"]


def test_count_on_a_selection_is_one_statement(store):
    store.insert("LLMUsage", [{"id": str(i), "instance_id": "x"} for i in range(5)])
    assert store.count(store.filter("LLMUsage", {"instance_id": "x"})) == 5
    assert store.count("LLMUsage") == 5
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_interactive_utils_store_c.py -v`
Expected: FAIL — `split_nth` predicate not yet exercised through this module's schema registration.

- [ ] **Step 4: Port the three `split('|').nth(0)` sites**

```python
# before (:7170, :7231, :7278)
.filter(lambda doc: doc['instance_id'].split('|').nth(0) == base_instance_id)
# after
store.filter('GraphNexusTradeContexts',
             P.field('instance_id').split_nth('|', 0).eq(base_instance_id))
```
`split_part` is 1-based, so `nth(0)` → `split_part(..., '|', 1)`. On a value with no separator both return the whole string — the test above pins that.

- [ ] **Step 5: Port the remaining ~81 sites in the range**

Apply R1/R2/R3/R6/R8/R10/R14/R15/R20/R21/R22/R24/R25. Reads that can exceed 100k rows use `store.iter`.

- [ ] **Step 6: Run the tests, then the whole existing suite for the file**

Run:
```bash
python3 -m pytest backend/tests/test_interactive_utils_store_a.py \
  backend/tests/test_interactive_utils_store_b.py \
  backend/tests/test_interactive_utils_store_c.py \
  backend/tests/test_interactive_utils_brokerages.py -v
```
Expected: PASS.

- [ ] **Step 7: Verify the entire file is clean**

Run: `grep -n "rethinkdb\|r\.db(\|RethinkDB()\|noreply_wait\|index_wait" backend/interactive_utils.py`
Expected: no output. This is the gate for the whole 247-site file.

- [ ] **Step 8: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add backend/interactive_utils.py backend/tests/test_interactive_utils_store_c.py
git commit -m "$(cat <<'EOF'
port(G3c): interactive_utils.py lines 6101-end off ReQL

~84 call sites including the three split('|').nth(0) scope-prefix filters.
interactive_utils.py is now ReQL-free (247 sites total across G3a/b/c).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 6: G4 — `strategies/graph_nexus_analysis.py`

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py` (144 ReQL sites; module handle is `_r`, not `r`). Load-bearing sites: the `(latest_observation_date DESC, id DESC)` tiebreak at `:11843-11858`, the ordered outcome reads at `:3131-3137` and `:3251-3253`, the `r.expr(...).contains(...)` at `:4824`, the unordered diagnostic `.limit()` at `:4364`.
- Test: `backend/tests/test_graph_nexus_ordering.py` (create)
- Test (existing, must pass): `backend/tests/test_nexus_graph_snapshot_pit.py`, `backend/tests/test_nexus_strategy_bugsweep.py`

**Constructs present:** R1, R2, R3, R6, R8, R10, R14, R15, R20, R21, R22, R23, R24, R25.

**Interfaces:**
- Consumes: `store.get`, `store.get_all(table, *keys, index=None)`, `store.insert`, `store.update`, `store.replace`, `store.delete`, `store.filter`, `store.pluck`, `store.order_by(selection, *, index=None, fields=(), desc=False)`, `store.limit`, `store.count`, `store.run`, `store.iter`, `P.field(key)` with `.eq/.lt/.ge/.default/.is_in`, `store.now()`, `schema.ensure_table`.
- Produces: nothing new.

- [ ] **Step 1: Impact analysis on the ordering site**

Run:
```
gitnexus_impact({target: "_load_trade_outcome_analogs", direction: "upstream"})
```
(use the actual enclosing function name at `:11843`). Report the blast radius. This window feeds an LLM prompt; the spec calls a wrong collation here "the single most likely silent failure in the migration".

- [ ] **Step 2: Write the failing collation test**

Create `backend/tests/test_graph_nexus_ordering.py`:
```python
from db import store, P

TABLE = "GraphNexusTradeOutcomes"


def test_tiebreak_order_is_bytewise_desc(store):
    """(latest_observation_date DESC, id DESC) under COLLATE "C".

    Every row shares one latest_observation_date, so `id DESC` alone decides
    which 3 of 5 survive .limit(3). Bytewise: '|' (0x7C) sorts AFTER 'Z' (0x5A)
    and after every digit, but BEFORE lowercase letters (0x61+).
    """
    ids = ["alpaca-main|a", "alpaca-mainZ", "alpaca-main9",
           "alpaca-main|Z", "alpaca-mainb"]
    for rid in ids:
        store.insert(TABLE, {"id": rid, "instance_id": "x",
                             "latest_observation_date": "2026-08-01",
                             "entry_date": "2026-07-01"})
    sel = store.order_by(
        store.filter(TABLE, P.field("entry_date").lt("2026-08-22")),
        fields=("latest_observation_date", "id"), desc=True)
    got = [row["id"] for row in store.run(store.limit(sel, 3))]
    assert got == ["alpaca-mainb", "alpaca-main|a", "alpaca-main|Z"]


def test_is_in_on_empty_list_is_false_not_error(store):
    store.insert(TABLE, {"id": "1", "instance_id": "x", "event": "merger"})
    assert store.run(store.filter(TABLE, P.field("event").is_in([]))) == []
    assert len(store.run(store.filter(TABLE, P.field("event").is_in(["merger"])))) == 1
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `PG_TEST_DSN="$(scripts/dev_pg.sh dsn)" python3 -m pytest backend/tests/test_graph_nexus_ordering.py -v`
Expected: FAIL — ordering not yet routed through `store.order_by`.
**This test must be run against real Postgres.** `FakeStore` cannot prove a collation.

- [ ] **Step 4: Port the tiebreak site at `:11843-11858`**

```python
# before
cursor = (
    _r.db(DB_NAME)
    .table(NEXUS_TRADE_OUTCOMES_TABLE)
    .get_all(instance_id, index="instance_id")
    .filter(lambda doc: doc["entry_date"].lt(as_of_date))
    .order_by(_r.desc("latest_observation_date"), _r.desc("id"))
    .limit(80)
    .run(conn)
)

# after
sel = store.filter(
    NEXUS_TRADE_OUTCOMES_TABLE,
    P.field("instance_id").eq(instance_id) & P.field("entry_date").lt(as_of_date),
)
sel = store.order_by(sel, fields=("latest_observation_date", "id"), desc=True)
cursor = store.run(store.limit(sel, 80))
```
**Keep the existing comment block above the `order_by` verbatim** — it documents why `id` is the tiebreak, and it is still true. Add one line to it: `# Under Postgres this is ORDER BY ... COLLATE "C" DESC — bytewise, matching RethinkDB.`

Note on `.filter(lambda doc: doc["entry_date"].lt(as_of_date))`: this is an **undefaulted** field access, so in ReQL a row missing `entry_date` raises and the enclosing `try/except` returns early. `P.field("entry_date").lt(...)` instead yields SQL NULL → false, so such a row is silently excluded rather than aborting the query. Before committing, prove the case cannot arise:
```bash
# against the live RethinkDB, read-only
python3 - <<'PY'
from rethinkdb import RethinkDB
r = RethinkDB(); conn = r.connect(host=..., port=...)
print(r.db('IntelliStock').table('GraphNexusTradeOutcomes')
      .filter(lambda d: ~d.has_fields('entry_date')).count().run(conn))
PY
```
Expected: `0`. If it is non-zero, escalate to the user — the two stores differ on real data and the difference is not this plan's to decide.

- [ ] **Step 5: Port the ordered outcome reads at `:3131-3137` and `:3251-3253`**

```python
# before (:3131)
cursor = _r.db(DB_NAME).table(OUTCOMES_TABLE).filter(
    lambda doc: (doc["instance_id"] == instance_id)
                & (doc["date_key"].ge(cutoff))
                & (doc["date_key"].lt(date_key))
).order_by(_r.desc("date_key")).limit(60).run(conn)

# after
sel = store.filter(OUTCOMES_TABLE,
                   P.field("instance_id").eq(instance_id)
                   & P.field("date_key").ge(cutoff)
                   & P.field("date_key").lt(date_key))
cursor = store.run(store.limit(store.order_by(sel, fields=("date_key",), desc=True), 60))

# before (:3135, the no-date_key branch)
cursor = _r.db(DB_NAME).table(OUTCOMES_TABLE).filter(
    lambda doc: doc["instance_id"] == instance_id
).order_by(_r.desc("date_key")).limit(60).run(conn)
# after
sel = store.filter(OUTCOMES_TABLE, P.field("instance_id").eq(instance_id))
cursor = store.run(store.limit(store.order_by(sel, fields=("date_key",), desc=True), 60))

# before (:3251, ASCENDING)
).order_by("date_key").run(conn)
# after
cursor = store.run(store.order_by(sel, fields=("date_key",), desc=False))
```
`:3253` is **ascending** — do not copy the `desc=True` from its neighbours.

- [ ] **Step 6: Port `:4824` (R23) and leave `:4364` unordered**

```python
# before (:4824)
.filter(lambda doc: _r.expr(allowed_kinds).contains(doc['kind']))
# after
store.filter(TABLE, P.field('kind').is_in(allowed_kinds))
```
`:4364` is an unordered `.limit()` on a diagnostic read; an unordered `LIMIT` has no defined order in either store. **Port it as-is with no `order_by` added** — adding one would be a behaviour change.

- [ ] **Step 7: Port the remaining ~138 sites**

Apply R1/R2/R3/R6/R8/R10/R14/R15/R20/R22/R24/R25. Delete the module-level `_r = RethinkDB()` handle and every `conn`/`close` pair. The two `index_create`/`index_wait` blocks collapse to `schema.ensure_table`; confirm the fields they created are in `schema.TABLES` for those tables and add them if not.

- [ ] **Step 8: Run the tests**

Run:
```bash
PG_TEST_DSN="$(scripts/dev_pg.sh dsn)" python3 -m pytest \
  backend/tests/test_graph_nexus_ordering.py \
  backend/tests/test_nexus_graph_snapshot_pit.py \
  backend/tests/test_nexus_strategy_bugsweep.py -v
```
Expected: PASS.

- [ ] **Step 9: Verify the file is clean and re-index**

Run: `grep -n "rethinkdb\|_r\.db(\|RethinkDB()\|noreply_wait" backend/strategies/graph_nexus_analysis.py`
Expected: no output.

This file is ~1.7 MB / 4,205 symbols and GitNexus skips files >512 KB without the env var. Re-index before `detect_changes`:
```bash
GITNEXUS_MAX_FILE_SIZE=2048 npx gitnexus analyze
```

- [ ] **Step 10: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add backend/strategies/graph_nexus_analysis.py backend/tests/test_graph_nexus_ordering.py
git commit -m "$(cat <<'EOF'
port(G4): graph_nexus_analysis.py off ReQL

144 call sites. The (latest_observation_date DESC, id DESC) tiebreak at :11856
is COLLATE "C" and pinned by a real-Postgres test on scope-suffixed ids; the
:3253 read stays ascending; :4364 stays deliberately unordered.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 7: G5 — `engines/nexus_graph_engine.py`

**Files:**
- Modify: `backend/engines/nexus_graph_engine.py` (17 ReQL sites; heavy `.replace` and `.update` use — 31 `.replace(` and 23 `.update(` textual occurrences, of which the ReQL ones are on `r.db(...)` chains)
- Test: `backend/tests/test_nexus_graph_engine_store.py` (create)

**Constructs present:** R1, R3, R6, R8, R22, R24, R25, R26.

**Interfaces:**
- Consumes: `store.get`, `store.insert`, `store.update`, `store.replace(table, row_id, doc) -> WriteResult`, `store.count`, `store.run`, `store.now()`, `schema.ensure_table`.
- Produces: nothing new.

- [ ] **Step 1: Impact analysis**

Run `gitnexus_impact` upstream on every function in the file that contains an `r.db(` chain (find them with `grep -n "r\.db(" backend/engines/nexus_graph_engine.py` then map to the enclosing `def`). Report HIGH/CRITICAL.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_nexus_graph_engine_store.py`:
```python
from db import store


def test_replace_drops_keys_update_does_not(store):
    store.insert("GraphNexusState", {"id": "s1", "a": 1, "b": 2})
    store.update("GraphNexusState", "s1", {"a": 9})
    assert store.get("GraphNexusState", "s1") == {"id": "s1", "a": 9, "b": 2}
    store.replace("GraphNexusState", "s1", {"id": "s1", "a": 9})
    assert store.get("GraphNexusState", "s1") == {"id": "s1", "a": 9}


def test_update_replaces_arrays_wholesale(store):
    store.insert("GraphNexusState", {"id": "s2", "edges": [1, 2, 3]})
    store.update("GraphNexusState", "s2", {"edges": [7]})
    assert store.get("GraphNexusState", "s2")["edges"] == [7]


def test_update_none_sets_json_null_it_does_not_delete(store):
    store.insert("GraphNexusState", {"id": "s3", "k": "v"})
    store.update("GraphNexusState", "s3", {"k": None})
    got = store.get("GraphNexusState", "s3")
    assert "k" in got and got["k"] is None
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_nexus_graph_engine_store.py -v`
Expected: FAIL (table not registered / module still on ReQL).

- [ ] **Step 4: Port the 17 sites**

```python
# R8 — before
r.db(DB_NAME).table('GraphNexusState').get(sid).replace(new_doc).run(conn)
# after
store.replace('GraphNexusState', sid, new_doc)

# R6 — before
r.db(DB_NAME).table('GraphNexusState').get(sid).update({'built_at': r.now()}).run(conn)
# after
store.update('GraphNexusState', sid, {'built_at': store.now()})

# R25 — the two table_create guards
schema.ensure_table('GraphNexusState')
```
Delete the module `r` handle and every `conn`/`close` pair (R26).

- [ ] **Step 5: Run the test**

Run: `python3 -m pytest backend/tests/test_nexus_graph_engine_store.py -v`
Expected: PASS.

- [ ] **Step 6: Verify clean, detect changes, commit**

```bash
grep -n "rethinkdb\|r\.db(\|noreply_wait" backend/engines/nexus_graph_engine.py   # expect no output
gitnexus_detect_changes()
git add backend/engines/nexus_graph_engine.py backend/tests/test_nexus_graph_engine_store.py
git commit -m "$(cat <<'EOF'
port(G5): engines/nexus_graph_engine.py off ReQL

17 call sites. replace-drops-keys vs update-deep-merges, array-replaces-wholesale,
and None-sets-null are each pinned by a test.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 8: G6 — remaining `engines/*`

**Files:**
- Modify: `backend/engines/price_engine.py` (6 sites; `get_conn()` at `:33`; 3 feeds at `:71`, `:92`, `:150`)
- Modify: `backend/engines/discover_engine.py` (5 sites; `get_conn()` at `:27`; 2 feeds at `:68`, `:106`)
- Modify: `backend/engines/daily_digest_engine.py` (2 sites; 1 feed at `:479`; **delete the `:497-500` re-raise-non-replica-errors block**)
- Modify: `backend/engines/self_learning_engine.py` (3 sites; the plucked feed at `:531-534`, minus the BacktestResults write at `:531` that Plan B owns)
- Modify: `backend/engines/backtest_engine.py` — everything except Plan B's `:927-966` stub writer (21 sites total, ~14 in scope; `get_conn()` at `:322`; the `BacktestInstances` feed at `:861` and its `_sweep_pending`)
- Modify: `backend/engines/discord_bot.py` (1 site)
- Test: `backend/tests/test_engine_watchers.py` (create)
- Test (existing, must pass): `backend/tests/test_backtest_engine_queue_health.py`, `backend/tests/test_regime_data_layer.py`

**Depends on:** Plan B's `engines/backtest_engine.py` commit. Rebase first.

**Constructs present:** R1, R3, R6, R10, R14, R15, R20, R21, R22, R24, R25, R26, R27.

**Interfaces:**
- Consumes: `store.get`, `store.insert`, `store.update`, `store.delete`, `store.filter`, `store.pluck`, `store.order_by`, `store.limit`, `store.count`, `store.run`, `store.now()`, `P.field(key)`, `schema.ensure_table`, `watch.watch_row(...)`, `watch.watch_table(table, on_change, *, label, fields=None, include_initial=True, squash=False, poll_interval=2.0, log=None, should_continue=None) -> Watcher`, `pool.listen_connection()`.
- Produces: nothing new. `price_engine.get_conn`, `discover_engine.get_conn`, `backtest_engine.get_conn` cease to exist.

- [ ] **Step 1: Impact analysis**

Run:
```
gitnexus_impact({target: "_sweep_pending", direction: "upstream"})
gitnexus_impact({target: "get_conn", direction: "upstream"})
```
Report both.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_engine_watchers.py`:
```python
from db import store


def test_self_learning_watch_projects_to_status_only(store, fake_watch):
    """:531's server-side .pluck({"new_val":["id","status"]}) becomes a
    BacktestProgress watch with fields=("status",)."""
    import engines.self_learning_engine as sle

    store.insert("BacktestProgress", {"id": "bt1", "status": "running",
                                      "progress": 0.5})
    seen = []
    w = sle._watch_backtest_progress(seen.append)
    try:
        assert set(seen[0]["new_val"]) == {"id", "status"}   # progress projected away
        store.update("BacktestProgress", "bt1", {"status": "finished"})
        fake_watch.notify("BacktestProgress", "bt1")
        fake_watch.pump(0.5)
        assert seen[-1]["old_val"]["status"] == "running"
        assert seen[-1]["new_val"]["status"] == "finished"
    finally:
        w.stop()


def test_backtest_queue_watch_reread_dedupes_against_active_ids(store, fake_watch):
    """_sweep_pending becomes the watcher's re-read hook; it must not re-queue
    an id already in _queued_or_active_ids."""
    import engines.backtest_engine as be

    store.insert("BacktestInstances", {"id": "q1", "status": "pending"})
    queued = []
    be._queued_or_active_ids.clear()
    w = be._watch_queue(queued.append)
    try:
        fake_watch.pump(0.5)
        assert queued == ["q1"]
        fake_watch.reconnect("BacktestInstances")   # forces the re-read path
        fake_watch.pump(0.5)
        assert queued == ["q1"]                     # NOT duplicated
    finally:
        w.stop()


def test_price_engine_live_prices_watch_is_off_the_main_thread(store, fake_watch):
    import engines.price_engine as pe

    w = pe._watch_live_prices(lambda change: None)
    try:
        assert w.is_alive()
    finally:
        w.stop()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_engine_watchers.py -v`
Expected: FAIL with `AttributeError: ... has no attribute '_watch_backtest_progress'`.

- [ ] **Step 4: Port `price_engine.py` and `discover_engine.py`**

Delete both `get_conn()` definitions. Then:
```python
from db import store, watch, schema

# price_engine :71 and :92 — these GAIN a reconnect they do not have today
def _watch_pings(on_change):
    return watch.watch_row('Config', 'Pings', on_change, label='price-pings')

def _watch_control(on_change):
    return watch.watch_row(ENGINE_CONTROL_TABLE, ENGINE_ID_PRICE, on_change,
                           label='price-control')

# price_engine :150 — whole-table LivePricesStocks. GAINS a reconnect AND
# moves off the main thread (the old `for item in ...changes().run(conn)`
# blocked the main loop).
def _watch_live_prices(on_change):
    return watch.watch_table('LivePricesStocks', on_change,
                             label='live-prices', include_initial=False)
```
`discover_engine.py:68` → `watch.watch_row('Config', 'Pings', ...)`; `:106` → `watch.watch_row(ENGINE_CONTROL_TABLE, ENGINE_ID_DISCOVER, ...)`.

- [ ] **Step 5: Port `daily_digest_engine.py:479` and delete the `:497-500` block**

```python
# after
w = watch.watch_row(ENGINE_CONTROL_TABLE, DIGEST_CONTROL_ID, _handle,
                    label='daily-digest-control')
w.start()
```
Delete the `:497-500` block that re-raises non-replica errors. Its whole purpose was to let a dying feed surface a real fault; `watch.py` never dies (handler exceptions are logged and swallowed, connection loss reconnects with capped backoff). Add a one-line comment recording the deletion:
```python
# The old :497-500 re-raise existed because a ReQL feed could die silently.
# watch.py reconnects with capped backoff and re-reads, so there is nothing
# left to re-raise.
```

- [ ] **Step 6: Port `self_learning_engine.py:531-534`**

```python
# before
cursor = (r.db(DB_NAME).table('BacktestResults')
          .pluck({'new_val': ['id', 'status']})
          .changes(squash=True, include_initial=True)
          .run(conn))
# after
def _watch_backtest_progress(on_change):
    """BacktestProgress IS the projection the server-side pluck was for."""
    return watch.watch_table('BacktestProgress', on_change,
                             label='self-learning-progress',
                             fields=('status',), squash=True,
                             include_initial=True)
```
The persisted `processed_run_ids` watermark logic is unchanged.

- [ ] **Step 7: Port `backtest_engine.py:861` + `_sweep_pending`, and `discord_bot.py`**

```python
# before
conn = get_conn()
for change in r.db(DB_NAME).table(TABLE_NAME).changes().run(conn):
    _handle_queue_change(change)
# after
def _watch_queue(on_queue):
    def _handle(change):
        new = change.get('new_val')
        if not new or new.get('status') != 'pending':
            return
        rid = new['id']
        if rid in _queued_or_active_ids:      # dedupe preserved verbatim
            return
        _queued_or_active_ids.add(rid)
        on_queue(rid)
    return watch.watch_table(TABLE_NAME, _handle, label='backtest-queue',
                             include_initial=True)
```
`_sweep_pending`'s body becomes the watcher's re-read hook: the `include_initial=True` start read and every reconnect re-read run the same `_handle` over the current pending set, so the periodic sweep loop can be deleted while its dedupe against `_queued_or_active_ids` stays. Delete `get_conn()` at `:322` and port the remaining ~13 non-Plan-B sites with R1/R3/R6/R10/R14/R20/R21/R22/R25.

`discord_bot.py`'s single site is a plain `.get` (R1).

- [ ] **Step 8: Run the tests**

Run:
```bash
python3 -m pytest backend/tests/test_engine_watchers.py \
  backend/tests/test_backtest_engine_queue_health.py \
  backend/tests/test_regime_data_layer.py -v
```
Expected: PASS.

- [ ] **Step 9: Verify clean, detect changes, commit**

```bash
grep -rn "rethinkdb\|r\.db(\|noreply_wait" backend/engines/   # expect no output
gitnexus_detect_changes()
git add backend/engines/ backend/tests/test_engine_watchers.py
git commit -m "$(cat <<'EOF'
port(G6): remaining engines/* off ReQL

~38 call sites and 6 changefeeds. price_engine's three feeds gain reconnects
and LivePricesStocks moves off the main thread; daily_digest's :497-500
re-raise block is deleted (the watcher never dies); self_learning's server-side
pluck becomes a BacktestProgress projection; backtest_engine's _sweep_pending
becomes the watcher re-read hook with its dedupe intact.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 9: G7 — `self_learning/store.py` + `self_learning/retention.py`

**Files:**
- Modify: `backend/self_learning/store.py` (62 ReQL sites; `get_conn()` at `:126`; 2 `r.branch` uses; 2 `r.minval`; 4 `index_create`/`index_wait` blocks; `conflict='update'` inserts)
- Modify: `backend/self_learning/retention.py` (no `r.db(` chains today — confirm with `grep -n "r\.db(\|rethinkdb" backend/self_learning/retention.py`; if it has none, the only change is any import of `self_learning.store.get_conn`)
- Test: `backend/tests/test_self_learning_store_pg.py` (create)

**Constructs present:** R1, R2, R3, R5, R6, R9, R10, R11, R12, R14, R15, R20, R21, R22, R25, R26.

**Interfaces:**
- Consumes: `store.get`, `store.get_all`, `store.insert(table, doc_or_docs, *, conflict='error', durability='hard') -> InsertResult` (with `conflict='update'`), `store.update`, `store.delete`, `store.filter`, `store.between(table, lo, hi, *, index=None, left_bound='closed', right_bound='open')`, `store.order_by`, `store.limit`, `store.pluck`, `store.count`, `store.run`, `store.iter`, `store.replace_if(table, row_id, *, when, doc, insert_if_absent=False) -> Doc | None`, `P.field(key)`, `schema.ensure_table`.
- Produces: nothing new. `self_learning.store.get_conn` ceases to exist.

- [ ] **Step 1: Impact analysis**

Run `gitnexus_impact({target: "get_conn", direction: "upstream"})` scoped to `self_learning`, plus upstream on the two functions containing `r.branch`. Report.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_self_learning_store_pg.py`:
```python
from db import store, P


def test_conflict_update_deep_merges_not_shallow(store):
    """`||` would drop the sibling key. jsonb_deep_merge must not."""
    store.insert("SelfLearningObservations",
                 {"id": "o1", "features": {"a": 1, "b": 2}, "label": "x"})
    store.insert("SelfLearningObservations",
                 {"id": "o1", "features": {"b": 9}}, conflict="update")
    got = store.get("SelfLearningObservations", "o1")
    assert got["features"] == {"a": 1, "b": 9}
    assert got["label"] == "x"


def test_multi_insert_is_partial_success(store):
    store.insert("SelfLearningObservations", {"id": "dup"})
    docs = [{"id": str(i)} for i in range(5)] + [{"id": "dup"}]
    res = store.insert("SelfLearningObservations", docs)
    assert res["inserted"] == 5
    assert res["errors"] == 1
    assert "Duplicate primary key" in res["first_error"]


def test_minval_upper_bound_drops_the_bound(store):
    for i in range(3):
        store.insert("SelfLearningObservations",
                     {"id": str(i), "run_id": "r1", "seq": i})
    sel = store.between("SelfLearningObservations", ["r1", None], ["r1", None],
                        index="run_seq")
    assert len(store.run(sel)) == 3
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_self_learning_store_pg.py -v`
Expected: FAIL.

- [ ] **Step 4: Delete `get_conn()` and the 4 index blocks**

```python
# before (x4)
if 'run_id' not in r.db(DB_NAME).table(T).index_list().run(conn):
    r.db(DB_NAME).table(T).index_create('run_id').run(conn)
    r.db(DB_NAME).table(T).index_wait('run_id').run(conn)
# after
schema.ensure_table(T)
```
Confirm every field these blocks created appears in `schema.TABLES[T].indexed_fields` (or `compound_indexes` for the compound ones) and add it if not — the index must not be silently lost.

- [ ] **Step 5: Port the two `r.branch` CAS sites (R9) and the `r.minval` bounds (R12)**

```python
# R9
saved = store.replace_if(T, key, when=P.field('version').eq(expected), doc=new_doc)
if saved is None:
    raise ObservationConflict(key)

# R12
sel = store.between(T, [run_id, None], [run_id, None], index='run_seq')
```

- [ ] **Step 6: Port the remaining ~55 sites**

Apply R1/R2/R3/R5/R6/R10/R11/R14/R15/R20/R21/R22/R26. Preserve `WRITE_CHUNK = 500` for multi-doc inserts. Any read over the observation table that can exceed 100k rows uses `store.iter`.

- [ ] **Step 7: Run the tests**

Run: `python3 -m pytest backend/tests/test_self_learning_store_pg.py backend/tests/ -k self_learning -v`
Expected: PASS.

- [ ] **Step 8: Verify clean, detect changes, commit**

```bash
grep -rn "rethinkdb\|r\.db(\|noreply_wait" backend/self_learning/   # expect no output
gitnexus_detect_changes()
git add backend/self_learning/ backend/tests/test_self_learning_store_pg.py
git commit -m "$(cat <<'EOF'
port(G7): self_learning/{store,retention}.py off ReQL

62 call sites. conflict='update' is jsonb_deep_merge (never ||), multi-doc insert
keeps partial-success shapes at WRITE_CHUNK=500, the two r.branch sites become
store.replace_if, and the 4 index_create blocks move to schema.TABLES.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 10: G8 — `kalshi/*`

**Files:**
- Modify: `backend/kalshi/db.py` (37 sites; `get_conn()` at `:55`; **keep `KALSHI_TABLES`**, the 27-entry `(table, primary_key)` registry; **delete `is_conn_error()` and `reconnect()`**; `r.expr(...).contains` at `:385`)
- Modify: `backend/kalshi/backtest_worker.py` (3 sites; the filtered feed at `:256`)
- Modify: `backend/kalshi/engine.py` (11 sites)
- Modify: `backend/kalshi/runner.py` (2 sites)
- Modify: `backend/kalshi/backtest_data.py` (2 sites)
- Modify: `backend/kalshi/__init__.py` (a `rethinkdb` import only)
- Test: `backend/tests/test_kalshi_store_pg.py` (create)
- Test (existing, must pass): `backend/tests/test_kalshi_backtest_worker.py`, `backend/tests/test_kalshi_model_registry.py`

**Constructs present:** R1, R3, R6, R10, R14, R20, R21, R22, R23, R25, R26, R27, plus non-`id` primary keys.

**Interfaces:**
- Consumes: `store.get`, `store.insert`, `store.update`, `store.delete`, `store.filter`, `store.pluck`, `store.order_by`, `store.limit`, `store.run`, `P.field(key).is_in(seq)`, `schema.ensure_table`, `schema.spec(table) -> TableSpec`, `watch.watch_filter(table, predicate, on_change, *, label, **kwargs) -> Watcher`, `errors.StoreError`.
- Produces: `KALSHI_TABLES: tuple[tuple[str, str], ...]` stays exported from `kalshi/db.py` and is fed into `schema.TABLES` at import — Plan A's `schema.py` already consumes it. `kalshi.db.get_conn`, `.is_conn_error`, `.reconnect` cease to exist.

- [ ] **Step 1: Impact analysis**

Run:
```
gitnexus_impact({target: "is_conn_error", direction: "upstream"})
gitnexus_impact({target: "reconnect", direction: "upstream"})
gitnexus_impact({target: "KALSHI_TABLES", direction: "upstream"})
```
`KALSHI_TABLES` must survive — if impact shows a consumer expecting the old `(table, pk)` shape, keep that shape exactly.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_kalshi_store_pg.py`:
```python
import pytest
from db import store, P
from db.errors import StoreError


def test_non_id_primary_key_round_trips(store):
    """kalshi_fixtures is keyed on fixture_id, not id."""
    from kalshi import db as kdb
    assert ("kalshi_fixtures", "fixture_id") in kdb.KALSHI_TABLES

    store.insert("kalshi_fixtures", {"fixture_id": "F1", "home": "ARS"})
    got = store.get("kalshi_fixtures", "F1")
    assert got == {"fixture_id": "F1", "home": "ARS"}   # pk field still in doc


def test_missing_pk_field_raises(store):
    with pytest.raises(StoreError):
        store.insert("kalshi_fixtures", {"home": "ARS"})


def test_pending_filter_watch_skips_initial(store, fake_watch):
    import kalshi.backtest_worker as kbw

    store.insert("KalshiBacktests", {"id": "kb0", "status": "pending"})
    seen = []
    w = kbw._watch_pending(seen.append)
    try:
        fake_watch.pump(0.3)
        assert seen == []                       # include_initial=False
        store.insert("KalshiBacktests", {"id": "kb1", "status": "pending"})
        fake_watch.notify("KalshiBacktests", "kb1")
        fake_watch.pump(0.5)
        assert seen[-1]["new_val"]["id"] == "kb1"
    finally:
        w.stop()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_kalshi_store_pg.py -v`
Expected: FAIL.

- [ ] **Step 4: Rework `kalshi/db.py`'s plumbing**

Delete `get_conn()` (`:55`), `is_conn_error()`, and `reconnect()` — the pool owns reconnection. Keep `KALSHI_TABLES` verbatim and register it:
```python
from db import store, schema, P

KALSHI_TABLES = (           # unchanged, 27 entries
    ("kalshi_fixtures", "fixture_id"),
    ("kalshi_markets", "market_ticker"),
    ...
)

def ensure_tables():
    for table, _pk in KALSHI_TABLES:
        schema.ensure_table(table)
```
`schema.py` reads `KALSHI_TABLES` at import for the `pk_field` of each table, so `store.get(table, key)` looks up the physical `id` column while the named field stays in `doc`. Writing a document that lacks its `pk_field` raises `StoreError` — matching RethinkDB.

- [ ] **Step 5: Port `:385` (R23) and the filtered feed at `backtest_worker.py:256` (R27)**

```python
# :385 before
.filter(lambda doc: r.expr(open_statuses).contains(doc['status']))
# after
store.filter('kalshi_markets', P.field('status').is_in(open_statuses))

# backtest_worker :256 before
cursor = (r.db(DB_NAME).table('KalshiBacktests')
          .filter({'status': 'pending'}).changes(include_initial=False).run(c))
# after
def _watch_pending(on_change):
    return watch.watch_filter('KalshiBacktests', {'status': 'pending'},
                              on_change, label='kalshi-pending',
                              include_initial=False)
```
**Keep the existing `pending_or_running_backtests` re-drain** exactly as it is — it is the belt to the watcher's braces.

- [ ] **Step 6: Port the remaining ~50 sites across `db.py`, `engine.py`, `runner.py`, `backtest_data.py`, `__init__.py`**

Apply R1/R3/R6/R10/R14/R20/R21/R22/R25/R26. `kalshi/__init__.py`'s only change is dropping the `rethinkdb` import.

- [ ] **Step 7: Run the tests**

Run:
```bash
python3 -m pytest backend/tests/test_kalshi_store_pg.py \
  backend/tests/test_kalshi_backtest_worker.py \
  backend/tests/test_kalshi_model_registry.py \
  backend/tests/ -k kalshi -v
```
Expected: PASS.

- [ ] **Step 8: Verify clean, detect changes, commit**

```bash
grep -rn "rethinkdb\|r\.db(\|noreply_wait" backend/kalshi/   # expect no output
gitnexus_detect_changes()
git add backend/kalshi/ backend/tests/test_kalshi_store_pg.py
git commit -m "$(cat <<'EOF'
port(G8): kalshi/* off ReQL

55 call sites and the pending-backtest changefeed. KALSHI_TABLES survives as the
source of truth for the 27 non-`id` primary keys and now feeds schema.py;
get_conn/is_conn_error/reconnect are deleted — the pool owns reconnection.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 11: G9 — `api/main.py`, `auth_utils.py`, `chatbot/*`

**Files:**
- Modify: `backend/api/main.py` (4 `r.db(` chains; heavy `.filter`/`.delete`/`.between` use)
- Modify: `backend/auth_utils.py` (12 sites; `get_conn()` at `:51`; 1 `index_create`/`index_wait` block)
- Modify: `backend/chatbot/conversations.py` (14 sites; `r.row` filters, 1 `index_create` block, ordered read)
- Modify: `backend/chatbot/orchestration.py` (1 site)
- Test: `backend/tests/test_api_auth_chat_store.py` (create)
- Test (existing, must pass): `backend/tests/test_alpha_api.py`, `backend/tests/test_api_llm_usage.py`, `backend/tests/test_api_push_and_test.py`, `backend/tests/test_credential_audit.py`

**Constructs present:** R1, R2, R3, R6, R8, R10, R11, R14, R15, R20, R21, R22, R25, R26.

**Interfaces:**
- Consumes: `store.get`, `store.get_all`, `store.insert`, `store.update`, `store.replace`, `store.delete(table, selector) -> WriteResult`, `store.filter`, `store.between`, `store.order_by`, `store.limit`, `store.pluck`, `store.count`, `store.run`, `P.field(key)`, `schema.ensure_table`, `pool.health() -> dict`.
- Produces: `auth_utils.get_conn` ceases to exist.

- [ ] **Step 1: Impact analysis**

Run `gitnexus_impact({target: "get_conn", direction: "upstream"})` scoped to `auth_utils`, and upstream on the FastAPI handlers in `api/main.py` that contain `r.db(` chains. Report. Auth is a security boundary — if impact shows a handler that skips auth on a store error, say so explicitly.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_api_auth_chat_store.py`:
```python
from db import store, P


def test_delete_on_a_selection_is_one_statement(store):
    store.insert("Sessions", [{"id": str(i), "user": "u1"} for i in range(3)]
                             + [{"id": "keep", "user": "u2"}])
    res = store.delete("Sessions", store.filter("Sessions", {"user": "u1"}))
    assert res["deleted"] == 3
    assert store.get("Sessions", "keep") is not None


def test_conversation_order_is_bytewise_desc(store):
    for cid, ts in [("a", "2026-08-01"), ("b", "2026-08-02"), ("c", "2026-08-03")]:
        store.insert("Conversations", {"id": cid, "user": "u1", "updated_at": ts})
    sel = store.order_by(store.filter("Conversations", {"user": "u1"}),
                         fields=("updated_at",), desc=True)
    assert [row["id"] for row in store.run(store.limit(sel, 2))] == ["c", "b"]


def test_auth_lookup_by_index_returns_no_duplicates_for_distinct_keys(store):
    store.insert("Users", [{"id": "1", "email": "a@x"}, {"id": "2", "email": "b@x"}])
    rows = store.get_all("Users", "a@x", "b@x", index="email")
    assert sorted(row["id"] for row in rows) == ["1", "2"]
    # ...but get_all does NOT dedupe repeated keys:
    assert len(store.get_all("Users", "a@x", "a@x", index="email")) == 2
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_api_auth_chat_store.py -v`
Expected: FAIL.

- [ ] **Step 4: Port `auth_utils.py`**

Delete `get_conn()` (`:51`); collapse the `index_create`/`index_wait` block to `schema.ensure_table('Users')` (R25), confirming `email` is in `schema.TABLES['Users'].indexed_fields`. Port the 12 sites with R1/R2/R3/R6/R10/R26.

- [ ] **Step 5: Port `api/main.py`**

The `.between` sites use `store.between` (R11) — half-open, never SQL `BETWEEN`. The `.delete()` on a filter becomes `store.delete(table, store.filter(...))` (R10) — one statement, never fetch-then-delete. Health endpoints that reported RethinkDB connectivity now report `pool.health()`:
```python
# before
{'rethinkdb': {'ok': _rethink_ok(), 'host': RETHINKDB_HOST}}
# after
from db import pool
{'postgres': pool.health()}          # {"ok": bool, "size": int, "dsn_host": str}
```
**This is Flutter-visible.** Check `mobile/` and the web frontend for a consumer of the `rethinkdb` key before renaming:
```bash
grep -rn "rethinkdb" mobile/lib frontend/src 2>/dev/null
```
If any consumer exists, **keep the old key name** with the new values and note it in the commit message — the invariant forbids a frontend-visible change.

- [ ] **Step 6: Port `chatbot/conversations.py` and `chatbot/orchestration.py`**

`r.row` filters → Predicate DSL (R15/R16). The ordered read → `store.order_by(..., desc=True)` with `COLLATE "C"` (R21). The `index_create` block → `schema.ensure_table('Conversations')` (R25). `orchestration.py`'s single site is a plain `.get` (R1).

- [ ] **Step 7: Run the tests**

Run:
```bash
python3 -m pytest backend/tests/test_api_auth_chat_store.py \
  backend/tests/test_alpha_api.py backend/tests/test_api_llm_usage.py \
  backend/tests/test_api_push_and_test.py backend/tests/test_credential_audit.py -v
```
Expected: PASS.

- [ ] **Step 8: Verify clean, detect changes, commit**

```bash
grep -rn "rethinkdb\|r\.db(\|noreply_wait" backend/api/ backend/auth_utils.py backend/chatbot/
gitnexus_detect_changes()
git add backend/api/ backend/auth_utils.py backend/chatbot/ \
        backend/tests/test_api_auth_chat_store.py
git commit -m "$(cat <<'EOF'
port(G9): api/main.py, auth_utils.py, chatbot/* off ReQL

31 call sites. delete-on-a-selection stays one statement, get_all keeps its
no-dedupe semantics, and the health endpoint reports pool.health() under a key
name checked against the Flutter and web consumers.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 12: G10 — state, identity, and replay core

**Files:**
- Modify: `backend/nexus_runtime_state.py` (17 sites; `r.branch` CAS at `:231`; 1 index block)
- Modify: `backend/live_state.py` (14 sites; `r.branch` CAS at `:359`; 1 index block; 4 `r.now()`)
- Modify: `backend/live_risk_state.py` (2 sites; `r.branch` CAS at `:578`)
- Modify: `backend/benchmark_alpha/rethink_store.py` → **rename to `backend/benchmark_alpha/pg_store.py`**; `_RethinkBackend` → `PostgresBackend`; `AlphaRethinkStore` → `AlphaPostgresStore` (8 sites; `r.branch` CAS at `:185`)
- Modify: `backend/benchmark_alpha/watchdog_main.py` (1 site)
- Modify: `backend/backtest_replay.py` — `RethinkReplayStore` → `PostgresReplayStore`, same class interface (takes a *backend* object, not a connection)
- Modify: `backend/backtest_evidence_runtime.py:52-94` — `default_replay_store()` factory
- Modify: `backend/experiment_registry.py` (no `r.db(` today; only its import of a deleted `get_conn`)
- Modify: `backend/llm_utils.py` — delete `_prompt_cache_new_conn()` (`:5772`) and its 3 call sites (`:5803`, `:5880`, `:5945`)
- Modify: `backend/clear_instance_state.py` (10 sites; `_build_filter` at `:384`, `_single_criterion_selection` at `:359`, `_indexed_selection` at `:246`, `_ensure_index` at `:207`, the `"￿"` sentinel at `:377`, the `"__no_match_sentinel__"` trick at `:313`, `origin_not_backtest` at `:366`/`:389`)
- Modify: `backend/paired_state_attest.py`, `backend/frozen_paired_state.py`, `backend/nexus_config_identity.py:127` — route every fingerprint through `db.json.canonical_sha256`
- Modify: `backend/nexus_graph_builds.py` (9 sites)
- Modify: `backend/backtest_critical_abort.py` — the 2 sites **not** owned by Plan B
- Test: `backend/tests/test_clear_instance_state_prefix.py` (create), `backend/tests/test_cas_and_fingerprints.py` (create)
- Test (existing, must pass): `backend/tests/test_alpha_rethink_store.py`, `backend/tests/test_alpha_records.py`, `backend/tests/test_alpha_watchdog.py`, `backend/tests/test_frozen_paired_state_contract.py`, `backend/tests/test_clear_main_instance_lookback_state.py`, `backend/tests/test_backtest_critical_abort.py`

**Depends on:** Plan B's `backtest_critical_abort.py` commit. Rebase first.

**Constructs present:** R1, R2, R3, R6, R8, R9, R10, R11, R13, R14, R15, R16, R17, R20, R22, R24, R25, R26.

**Interfaces:**
- Consumes: `store.get`, `store.get_all(table, *keys, index=None)`, `store.insert`, `store.update`, `store.replace`, `store.replace_if(table, row_id, *, when, doc, insert_if_absent=False) -> Doc | None`, `store.delete`, `store.filter(table, predicate)`, `store.between(table, lo, hi, *, index=None, left_bound='closed', right_bound='open')`, `store.pluck`, `store.count`, `store.run`, `store.iter`, `store.now()`, `P.field(key)` with `.eq/.ne/.default/.match_prefix/.coerce_to_string`, `schema.ensure_table`, `schema.spec`, `json.canonical(value) -> str`, `json.canonical_sha256(value) -> str`, `errors.StoreError`, `errors.UnavailableError`.
- Produces: `benchmark_alpha.pg_store.PostgresBackend`, `benchmark_alpha.pg_store.AlphaPostgresStore`, `backtest_replay.PostgresReplayStore`. `InMemoryReplayStore` is **unchanged**, and the ~10 test doubles implementing `insert_record`/`get_record`/`update_row`/`compare_and_swap_state` keep working untouched — do not change that interface.

- [ ] **Step 1: Impact analysis on the renames**

Renames go through GitNexus, never find-and-replace:
```
gitnexus_impact({target: "RethinkReplayStore", direction: "upstream"})
gitnexus_impact({target: "_RethinkBackend", direction: "upstream"})
gitnexus_impact({target: "AlphaRethinkStore", direction: "upstream"})
gitnexus_impact({target: "_prompt_cache_new_conn", direction: "upstream"})
gitnexus_impact({target: "_single_criterion_selection", direction: "upstream"})
```
Report each. Then perform the three class renames with `gitnexus_rename` (it understands the call graph); do **not** `sed`.

- [ ] **Step 2: Write the failing prefix-scan test**

Create `backend/tests/test_clear_instance_state_prefix.py`:
```python
from db import store, P
import clear_instance_state as cis


def test_scoped_prefix_matches_suffixed_ids(store):
    """The :100-104 regression: exact-only matching once found ZERO scoped rows
    and turned a full clear into a silent no-op."""
    store.insert("GraphNexusTradeContexts", [
        {"id": "alpaca-main|h1|2026-08-01|AAPL", "instance_id": "alpaca-main|h1"},
        {"id": "alpaca-main|h2|2026-08-01|MSFT", "instance_id": "alpaca-main|h2"},
        {"id": "test|h3|2026-08-01|NVDA", "instance_id": "test|h3"},
        {"id": "alpaca-main", "instance_id": "alpaca-main"},
    ])
    sel = store.filter("GraphNexusTradeContexts",
                       P.field("instance_id").default("").match_prefix("alpaca-main"))
    got = sorted(row["id"] for row in store.run(sel))
    assert len(got) == 3                     # NOT zero, NOT one
    assert "test|h3|2026-08-01|NVDA" not in got


def test_like_form_and_range_form_agree_on_every_target(store):
    for target in cis.build_targets("alpaca-main", scope="lookback_only"):
        table, field = target[0], target[1]
        like_rows = {row["id"] for row in store.run(
            store.filter(table, P.field(field).default("").match_prefix("alpaca-main")))}
        range_rows = {row["id"] for row in store.run(
            store.between(table, "alpaca-main", "alpaca-main￿",
                          index=field, right_bound="closed"))}
        assert like_rows == range_rows, table


def test_empty_id_set_yields_a_valid_empty_selection(store):
    sel = store.filter("GraphNexusTradeContexts", P.field("id").is_in([]))
    assert store.count(sel) == 0
    assert store.delete("GraphNexusTradeContexts", sel)["deleted"] == 0


def test_prefix_special_chars_are_escaped(store):
    store.insert("GraphNexusTradeContexts", [
        {"id": "1", "instance_id": "a_b|x"},
        {"id": "2", "instance_id": "axb|x"},
        {"id": "3", "instance_id": "100%|x"},
    ])
    sel = store.filter("GraphNexusTradeContexts",
                       P.field("instance_id").default("").match_prefix("a_b"))
    assert [row["id"] for row in store.run(sel)] == ["1"]     # `_` is literal
    sel = store.filter("GraphNexusTradeContexts",
                       P.field("instance_id").default("").match_prefix("100%"))
    assert [row["id"] for row in store.run(sel)] == ["3"]     # `%` is literal
```

- [ ] **Step 3: Write the failing CAS + fingerprint test**

Create `backend/tests/test_cas_and_fingerprints.py`:
```python
from db import store, P
from db.json import canonical, canonical_sha256


def test_cas_distinguishes_predicate_false_from_row_missing(store):
    store.insert("NexusRuntimeState", {"id": "s", "version": 1})
    assert store.replace_if("NexusRuntimeState", "s",
                            when=P.field("version").eq(2),
                            doc={"id": "s", "version": 2}) is None      # predicate false
    assert store.replace_if("NexusRuntimeState", "missing",
                            when=P.field("version").eq(1),
                            doc={"id": "missing"}) is None              # row absent
    assert store.replace_if("NexusRuntimeState", "missing",
                            when=None, doc={"id": "missing"},
                            insert_if_absent=True) == {"id": "missing"}


def test_fingerprint_is_invariant_to_key_order_and_number_form():
    a = {"b": 1.230e-5, "a": [1, 2]}
    b = {"a": [1, 2], "b": 0.00001230}
    assert canonical(a) == canonical(b)
    assert canonical_sha256(a) == canonical_sha256(b)


def test_paired_state_attest_routes_through_canonical_sha256(monkeypatch):
    import paired_state_attest as psa
    import db.json as dbjson

    calls = []
    monkeypatch.setattr(dbjson, "canonical_sha256",
                        lambda v: calls.append(v) or "deadbeef")
    psa.fingerprint_state({"Instances": [{"id": "x"}]})
    assert calls, "fingerprint must go through db.json.canonical_sha256"
```

- [ ] **Step 4: Run both tests to verify they fail**

Run: `python3 -m pytest backend/tests/test_clear_instance_state_prefix.py backend/tests/test_cas_and_fingerprints.py -v`
Expected: FAIL.

- [ ] **Step 5: Port the four `r.branch` CAS sites (R9)**

`nexus_runtime_state.py:231`, `live_state.py:359`, `live_risk_state.py:578`, `benchmark_alpha/pg_store.py:185` all become `store.replace_if`:
```python
# before
res = (r.db(DB_NAME).table(STATE_TABLE).get(key)
       .replace(lambda row: r.branch(row['version'] == expected_version, doc, row),
                durability=durability)
       .run(conn))
if res['replaced'] != 1:
    raise AlphaStateConflictError(key)
# after
saved = store.replace_if(STATE_TABLE, key,
                         when=P.field('version').eq(expected_version),
                         doc=doc, insert_if_absent=(expected_version is None))
if saved is None:
    raise AlphaStateConflictError(key)
```
`durability=` disappears from the ReQL call but **stays in the Python signature** of `compare_and_swap_state`/`insert_record`/`insert_event` — the ~10 test doubles depend on that arity.

- [ ] **Step 6: Rewrite `clear_instance_state.py`'s selection builders**

```python
# _single_criterion_selection — before
def _single_criterion_selection(conn, r, table, criterion):
    field, val, kind = criterion
    if field == "origin_not_backtest":
        return r.db(DB_NAME).table(table).filter(
            r.row["origin"].default("") != "backtest")
    # ￿ sorts after every UTF-8 character RethinkDB will see
    high = str(val) + "￿"
    return r.db(DB_NAME).table(table).between(val, high, index=field,
                                              right_bound="closed")

# after
def _single_criterion_selection(table, criterion):
    field, val, kind = criterion
    if field == "origin_not_backtest":
        return store.filter(table, P.field("origin").default("").ne("backtest"))
    # Prefix scan. The "￿" sentinel is gone: match_prefix compiles to
    # `coalesce(doc->>'f','') LIKE escaped||'%'` against the _pfx index under
    # COLLATE "C", which is the same row set by construction.
    return store.filter(table, P.field(field).default("").match_prefix(str(val)))


# _build_filter — before
def _build_filter(r, criteria, combine="or"):
    exprs = []
    for field, val, kind in criteria:
        if field == "origin_not_backtest":
            exprs.append(r.row["origin"].default("") != "backtest")
        else:
            exprs.append(r.row[field].default("").match("^" + _re_escape(val)))
    ...
# after
def _build_filter(criteria, combine="or"):
    preds = []
    for field, val, kind in criteria:
        if field == "origin_not_backtest":
            preds.append(P.field("origin").default("").ne("backtest"))
        else:
            preds.append(P.field(field).default("").match_prefix(str(val)))
    if not preds:
        return None
    out = preds[0]
    for p in preds[1:]:
        out = (out | p) if combine == "or" else (out & p)
    return out
```
The `"__no_match_sentinel__"` trick at `:313` is deleted; an empty id set becomes `P.field("id").is_in([])`, which compiles to `false` and answers `.count()` (0) and `.delete()` (no-op) correctly. `_ensure_index` (`:207`) is deleted — `schema.ensure_schema()` guarantees the indexes. The `origin_not_backtest` criterion **keeps its name** in `build_targets`; the 18-target inventory and the `(table, field, kind)` tuple shape do not change.

- [ ] **Step 7: Port `llm_utils.py`'s prompt cache to the pool**

Delete `_prompt_cache_new_conn()` (`:5772`). At `:5803`, `:5880`, `:5945`:
```python
# before
_conn = _prompt_cache_new_conn()
if _conn is None:
    return None
try:
    row = r.db(DB_NAME).table(_PROMPT_CACHE_TABLE).get(key).run(_conn)
except Exception:
    _prompt_cache_fail()
    return None
finally:
    try:
        _conn.close(noreply_wait=False)
    except Exception:
        pass

# after
from db import store
from db.errors import StoreError
try:
    row = store.get(_PROMPT_CACHE_TABLE, key)
except StoreError:
    _prompt_cache_fail()
    return None
```
The self-disable-after-5-failures behaviour (`_PROMPT_CACHE_MAX_FAILS`, `_prompt_cache_stats`, `_prompt_cache_lock`) is preserved **verbatim**, counting `StoreError` instead of driver exceptions. `:5803`'s lazy table creation becomes `schema.ensure_table(_PROMPT_CACHE_TABLE)`.

- [ ] **Step 8: Route every fingerprint through `canonical_sha256`**

In `paired_state_attest.py`, `frozen_paired_state.py`, and `nexus_config_identity.py:127`, replace every ad-hoc `hashlib.sha256(json.dumps(...).encode()).hexdigest()` with:
```python
from db.json import canonical_sha256
fp = canonical_sha256(value)
```
This makes a fingerprint invariant to jsonb's key reordering and number renormalisation (`1.230e-5` → `0.00001230`). `_ALLOWED_STATE_TABLES` (26 tables) and `_VOLATILE_FIELDS` are unchanged — they port by name because table names are preserved exactly.

- [ ] **Step 9: Port the remaining files in the group**

`backtest_replay.PostgresReplayStore` keeps the same class interface (it takes a *backend* object, not a connection), so only the name and the backend it is constructed with change. `backtest_evidence_runtime.default_replay_store()`:
```python
# before
from backtest_replay import RethinkReplayStore
from benchmark_alpha.rethink_store import _RethinkBackend
return RethinkReplayStore(_RethinkBackend(r, _conn_factory, DB_NAME))
# after
from backtest_replay import PostgresReplayStore
from benchmark_alpha.pg_store import PostgresBackend
return PostgresReplayStore(PostgresBackend())
```
`InMemoryReplayStore` is **untouched**. Port `nexus_graph_builds.py` (9 sites) and `backtest_critical_abort.py`'s 2 non-Plan-B sites with R1/R3/R6/R10/R14/R25/R26. `experiment_registry.py`'s only change is dropping its import of a deleted `get_conn`.

- [ ] **Step 10: Run the tests**

Run:
```bash
PG_TEST_DSN="$(scripts/dev_pg.sh dsn)" python3 -m pytest \
  backend/tests/test_clear_instance_state_prefix.py \
  backend/tests/test_cas_and_fingerprints.py \
  backend/tests/test_alpha_rethink_store.py backend/tests/test_alpha_records.py \
  backend/tests/test_alpha_watchdog.py \
  backend/tests/test_frozen_paired_state_contract.py \
  backend/tests/test_clear_main_instance_lookback_state.py \
  backend/tests/test_backtest_critical_abort.py -v
```
Expected: PASS. Rename `backend/tests/test_alpha_rethink_store.py` → `backend/tests/test_alpha_pg_store.py` in the same commit (`git mv`).

- [ ] **Step 11: Verify clean, detect changes, commit**

```bash
grep -rn "rethinkdb\|r\.db(\|RethinkReplayStore\|_RethinkBackend\|AlphaRethinkStore\|noreply_wait" \
  backend/nexus_runtime_state.py backend/live_state.py backend/live_risk_state.py \
  backend/benchmark_alpha/ backend/backtest_replay.py backend/backtest_evidence_runtime.py \
  backend/llm_utils.py backend/clear_instance_state.py backend/paired_state_attest.py \
  backend/frozen_paired_state.py backend/nexus_config_identity.py \
  backend/nexus_graph_builds.py backend/backtest_critical_abort.py \
  backend/experiment_registry.py
gitnexus_detect_changes()
git add -A backend/ && git commit -m "$(cat <<'EOF'
port(G10): state, identity, and replay core off ReQL

65 call sites. The 4 r.branch compare-and-swaps become store.replace_if (which
distinguishes predicate-false from row-missing); clear_instance_state's "￿"
sentinel and "__no_match_sentinel__" trick are replaced by the Predicate DSL with
the :100-104 silent-no-op regression under test; the llm_utils prompt cache moves
to the pool keeping _PROMPT_CACHE_MAX_FAILS verbatim; every fingerprint now goes
through db.json.canonical_sha256. RethinkReplayStore -> PostgresReplayStore,
_RethinkBackend -> PostgresBackend, AlphaRethinkStore -> AlphaPostgresStore, all
via gitnexus_rename. InMemoryReplayStore and the ~10 test doubles are untouched.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 13: G11 — the long tail

**Files (23, ~86 sites):**
- Modify: `backend/discover.py` (9), `backend/cli.py` (4), `backend/intellistock.py` (6), `backend/live_boot_audit.py` (1), `backend/live_boot_setup.py` (3), `backend/live_broker_fetch.py` (import only), `backend/live_kill_switch.py` (3), `backend/live_mode_overrides.py` (import only), `backend/llm_telemetry.py` (8), `backend/model_resolver.py` (1), `backend/nexus_lookback_db.py` (import only), `backend/nexus_restamp.py` (5), `backend/point_in_time_registry.py` (6), `backend/price_utils.py` (import only), `backend/strategy_cache_persistence.py` (9), `backend/sec_edgar_supply_chain.py` (4), `backend/benzinga_client.py` (4), `backend/strategies/earnings.py` (5, incl. the `r.expr(...).contains` at `:178`), `backend/strategies/google_news.py` (4), `backend/strategies/ml_news.py` (7), `backend/strategies/nexus_analyst_panel.py` (6), `backend/test_graph_hardening.py` (import only), `backend/rethink_changefeed.py` (docstring + `is_transient_rethinkdb_error`)
- Test: `backend/tests/test_long_tail_store.py` (create)
- Test (existing, must pass): `backend/tests/test_llm_telemetry.py`, `backend/tests/test_llm_telemetry_throttle.py`, `backend/tests/test_strategy_cache_persistence.py`, `backend/tests/test_live_boot_audit.py`, `backend/tests/test_live_boot_setup.py`, `backend/tests/test_live_mode_overrides.py`, `backend/tests/test_nexus_restamp.py`, `backend/tests/test_changefeed_selfheal.py`

**Constructs present:** R1, R2, R3, R4, R6, R8, R10, R11, R14, R15, R21, R22, R23, R24, R25, R26.

**Interfaces:**
- Consumes: `store.get`, `store.get_all`, `store.insert`, `store.update`, `store.replace`, `store.delete`, `store.filter`, `store.between`, `store.order_by`, `store.limit`, `store.count`, `store.run`, `store.iter`, `store.now()`, `P.field(key).is_in(seq)`, `schema.ensure_table`, `pool.listen_connection()`, `watch.feed(table, *, include_initial=True, row_id=None, predicate=None) -> Iterator[Change]`.
- Produces: `rethink_changefeed.is_transient_db_error(exc) -> bool`, with `is_transient_rethinkdb_error` kept as an alias for one release. `run_reconnecting_changefeed`'s signature is **unchanged**:
  ```python
  def run_reconnecting_changefeed(open_feed, handle_change, label, *, get_conn,
                                  log=None, pass_conn=True, initial_delay=2.0,
                                  max_delay=30.0, sleep=time.sleep,
                                  should_continue=None): ...
  ```

- [ ] **Step 1: Impact analysis**

Run:
```
gitnexus_impact({target: "is_transient_rethinkdb_error", direction: "upstream"})
gitnexus_impact({target: "run_reconnecting_changefeed", direction: "upstream"})
```
Report. `run_reconnecting_changefeed`'s ~6 call sites must all still typecheck at the same arity after this task.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_long_tail_store.py`:
```python
import psycopg
import psycopg_pool
import pytest
from db import store, P


def test_is_transient_db_error_reclassified():
    from rethink_changefeed import is_transient_db_error, is_transient_rethinkdb_error

    assert is_transient_rethinkdb_error is is_transient_db_error   # alias kept
    assert is_transient_db_error(psycopg.OperationalError("server closed"))
    assert is_transient_db_error(psycopg_pool.PoolTimeout("timed out"))
    assert is_transient_db_error(ConnectionResetError("reset"))
    assert is_transient_db_error(OSError("broken pipe"))
    assert not is_transient_db_error(ValueError("bad json"))


def test_earnings_is_in_filter(store):
    store.insert("EarningsLLMCache", [
        {"id": "1", "ticker": "AAPL"}, {"id": "2", "ticker": "MSFT"},
        {"id": "3", "ticker": "NVDA"},
    ])
    rows = store.run(store.filter("EarningsLLMCache",
                                  P.field("ticker").is_in(["AAPL", "NVDA"])))
    assert sorted(row["id"] for row in rows) == ["1", "3"]


def test_llm_telemetry_write_survives_a_store_error(store, monkeypatch):
    """Telemetry must never take down a caller."""
    import llm_telemetry
    from db.errors import UnavailableError

    def _boom(*a, **k):
        raise UnavailableError("down")
    monkeypatch.setattr(store, "insert", _boom)
    llm_telemetry.record_usage({"model": "x", "tokens": 1})   # must not raise
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_long_tail_store.py -v`
Expected: FAIL with `ImportError: cannot import name 'is_transient_db_error'`.

- [ ] **Step 4: Rework `rethink_changefeed.py`**

```python
# after
import psycopg
import psycopg_pool

_TRANSIENT_HINTS = (
    "connection", "timeout", "broken pipe", "server closed",
    "terminating connection", "could not connect",
)

def is_transient_db_error(exc) -> bool:
    if isinstance(exc, (psycopg.OperationalError, psycopg_pool.PoolTimeout,
                        OSError, ConnectionError)):
        return True
    text = str(exc).lower()
    return any(hint in text for hint in _TRANSIENT_HINTS)

# Kept for one release so no call site changes arity in this commit.
is_transient_rethinkdb_error = is_transient_db_error
```
Drop the four RethinkDB-specific substring hints. Update the `:84` docstring:
```python
"""``open_feed`` returns an iterator of Change dicts, e.g.
``lambda c: watch.feed(TABLE, include_initial=True)``."""
```
The function signature and body are otherwise unchanged.

- [ ] **Step 5: Port the 22 remaining files**

Apply R1/R2/R3/R4/R6/R8/R10/R11/R14/R15/R21/R22/R23/R24/R25/R26 mechanically. Six files (`live_broker_fetch.py`, `live_mode_overrides.py`, `nexus_lookback_db.py`, `price_utils.py`, `test_graph_hardening.py`, and one more the grep finds) carry **only** a `rethinkdb` import with no `r.db(` chain — delete the import and nothing else. `strategies/earnings.py:178`'s `r.expr(...).contains` is R23.

- [ ] **Step 6: Run the tests**

Run:
```bash
python3 -m pytest backend/tests/test_long_tail_store.py \
  backend/tests/test_llm_telemetry.py backend/tests/test_llm_telemetry_throttle.py \
  backend/tests/test_strategy_cache_persistence.py \
  backend/tests/test_live_boot_audit.py backend/tests/test_live_boot_setup.py \
  backend/tests/test_live_mode_overrides.py backend/tests/test_nexus_restamp.py \
  backend/tests/test_changefeed_selfheal.py -v
```
Expected: PASS.

- [ ] **Step 7: Verify clean, detect changes, commit**

```bash
gitnexus_detect_changes()
git add -A backend/ && git commit -m "$(cat <<'EOF'
port(G11): long-tail modules off ReQL

~86 call sites across 23 files. is_transient_rethinkdb_error becomes
is_transient_db_error (psycopg.OperationalError / PoolTimeout / OSError plus the
non-RethinkDB substring hints), with the old name kept as an alias for one
release so run_reconnecting_changefeed's ~6 call sites keep their arity.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

### Task 14: G12 — residual-import gate and the test-stub migration

**Files:**
- Create: `backend/tests/test_no_rethinkdb_in_runtime.py`
- Modify: the **46** test files that stub a module-level `r` (list produced in Step 3) and the **2** that import `rethinkdb` directly (`backend/tests/test_changefeed_selfheal.py`, `backend/tests/test_live_mode_overrides.py`)
- Modify: `backend/requirements.txt` — remove `rethinkdb`
- Test: the full backend suite

**Depends on:** every other group (G1–G11) merged.

**Interfaces:**
- Consumes: the `store` and `fake_watch` pytest fixtures from `backend/tests/conftest.py` (Plan A).
- Produces: nothing new.

- [ ] **Step 1: Write the failing residual-import gate**

Create `backend/tests/test_no_rethinkdb_in_runtime.py`:
```python
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
ALLOWED = {
    "scripts/migrate_rethinkdb_to_postgres.py",   # the one-shot migration script
}
PATTERN = re.compile(r"rethinkdb|RethinkDB\(\)|r\.db\(")


def _offenders():
    out = subprocess.run(
        ["grep", "-rln", "-E", PATTERN.pattern, "backend", "--include=*.py"],
        cwd=REPO, capture_output=True, text=True).stdout.split()
    keep = []
    for path in out:
        if path in ALLOWED:
            continue
        if path.startswith("backend/tests/"):
            continue          # fixtures may name the old store in comments
        if path.startswith("scripts/archive_rethinkdb/"):
            continue
        keep.append(path)
    return keep


def test_no_rethinkdb_left_in_backend_runtime():
    assert _offenders() == []


def test_requirements_has_no_rethinkdb():
    text = (REPO / "backend" / "requirements.txt").read_text()
    assert "rethinkdb" not in text.lower()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest backend/tests/test_no_rethinkdb_in_runtime.py -v`
Expected: FAIL, listing whatever `backend/` files still mention RethinkDB. If it already passes, G1–G11 are complete — record the empty list in the commit message and move on.

- [ ] **Step 3: Enumerate and migrate the test stubs**

Produce the list:
```bash
grep -rln 'setattr([a-zA-Z_]*, *"r"\|fake_r\|_FakeR\|class FakeR' \
  backend/tests --include='*.py' | sort > /tmp/stub_files.txt
wc -l /tmp/stub_files.txt      # expect 46
grep -rln "import rethinkdb\|from rethinkdb\|RethinkDB()" \
  backend/tests --include='*.py' | sort     # expect 2
```
For each file, replace the ad-hoc stub with the shared fixture:
```python
# before
class FakeR:
    def db(self, name): return self
    def table(self, name): return self
    def get(self, k): return self
    def run(self, conn): return self._rows
...
def test_x(monkeypatch):
    monkeypatch.setattr(iu, "r", FakeR([{"id": "1"}]))
    monkeypatch.setattr(iu, "get_conn", lambda: object())
    assert iu.load_instance("1") == {"id": "1"}

# after
def test_x(store):
    store.insert("Instances", {"id": "1"})
    assert iu.load_instance("1") == {"id": "1"}
```
The fixture is real Postgres when `PG_TEST_DSN` is set and `FakeStore` otherwise, so these tests keep running on a laptop with no database. Delete every `FakeR`/`fake_r` class and every `monkeypatch.setattr(<mod>, "get_conn", ...)`.

`test_changefeed_selfheal.py` additionally swaps its ReQL feed double for `watch.feed`/`fake_watch`; `test_live_mode_overrides.py` drops its `rethinkdb` import.

- [ ] **Step 4: Remove `rethinkdb` from runtime requirements**

In `backend/requirements.txt`, delete the `rethinkdb` line. Plan D adds it back as an **optional extra** for the migration script only — do not add it here.

- [ ] **Step 5: Run the gate and the full suite**

Run:
```bash
python3 -m pytest backend/tests/test_no_rethinkdb_in_runtime.py -v
python3 -m pytest backend/tests -q
```
Expected: the gate PASSES, and the full suite reports **the same passed count as `main`** (475 at the time of writing). A lower count means a test was dropped, not fixed — go find it. Record both numbers in the commit message.

Then repeat against real Postgres:
```bash
scripts/dev_pg.sh up
PG_TEST_DSN="$(scripts/dev_pg.sh dsn)" python3 -m pytest backend/tests -q
```
Expected: PASS, with the real-PG-only tests (`test_collation.py`, `test_merge_property.py`, `test_watch.py`, `test_prefix_scan.py`, `test_backtest_split.py`) now running instead of skipping.

- [ ] **Step 6: Detect changes and commit**

```bash
gitnexus_detect_changes()
git add backend/tests/ backend/requirements.txt
git commit -m "$(cat <<'EOF'
port(G12): residual-import gate + migrate the 48 test stubs to the store fixture

test_no_rethinkdb_in_runtime.py fails the build if any backend/ file outside
the migration script mentions rethinkdb. 46 test files drop their hand-rolled
FakeR doubles for the shared `store` fixture and 2 drop a direct rethinkdb
import. rethinkdb leaves backend/requirements.txt.

Full suite: <N> passed with FakeStore, <N> passed against PG_TEST_DSN.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NYF7Zh2WDs4ziJGznYN8Fs
EOF
)"
```

---

## Self-review notes (resolved during writing)

- **`get_conn()` count.** The spec says 16 including test stubs; the runtime tree has **14** definitions (12 `get_conn`, 2 `get_conn_retry`), enumerated across Tasks 1, 2, 3, 8, 9, 10, 11. The remainder are test-side and are deleted in Task 14.
- **Test-stub count.** The brief said 16 test files stub RethinkDB. The actual counts are **2** files importing `rethinkdb` directly and **46** stubbing a module-level `r`. Task 14 covers all 48.
- **`graph_nexus_analysis.py:11846`'s undefaulted `doc["entry_date"]`.** Spec §4 claims every ported lambda uses `.default()`; this one does not. Task 6 Step 4 requires proving on live data that no row lacks `entry_date` before accepting the NULL-is-false translation, and escalating if any does.
- **`backend/scripts/` and `scripts/`** are Plan D's, not this plan's. Task 14's gate skips `scripts/archive_rethinkdb/` and the migration script for that reason.
