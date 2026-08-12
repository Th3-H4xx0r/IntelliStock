# Queue-time execution snapshot: smallest default-OFF implementation plan

Date: 2026-08-11
Status: implementation plan only — no production code/config was changed and no backtest was launched

## Decision

Implement this as an **equities-only, request-level opt-in** named
`freeze_execution_snapshot`. The default is a real boolean `false`. When it is absent or false,
`action_create_backtest` must write the exact legacy queue document, return the exact legacy
response, the engine must build the exact legacy broker command, and the broker must take the exact
legacy strategy/model path. No empty snapshot fields, false marker, hash, extra result fields, or new
log line may appear.

When it is true, queue creation resolves the exact effective strategy that the broker would execute,
including model references, `pit_mode=research`, and validated candidate overrides. It stores one
canonical, secret-free snapshot and its SHA-256 in the same `BacktestInstances` document. At broker
startup the same resolver is run against current `Instances` / `Strategies` / `Models`; the broker
verifies both the stored content hash and byte equality with the startup resolution. Any missing,
invalid, tampered, or different value is fatal **before passive-execution setup, experiment
preregistration, market-data fetch, historic lookback, emulator creation, or strategy execution**.
The queue-time snapshot, not the regenerated startup object, is copied to results.

This is the smallest useful executable-control-plane slice of
`frozen-paired-state-design.md`. It is necessary but **not sufficient** for a causal paired P&L
claim: it does not freeze Rethink/Neo4j state, market/PIT data, wall clock, source/image/dependencies,
or model responses. In particular, a frozen run with `pit_mode=research` remains lookahead-biased
and promotion-ineligible. Do not launch the anchor pairs until the state/evidence bundle and
negative-control requirements in that design also exist.

## Current path and the bug boundary

The current queue action (`interactive_utils.action_create_backtest`) stores the normalized run
window/cash/fee fields and validated evidence block, but no executable strategy. At startup
`broker.load_strategies_from_db` rereads the live instance and strategy. The broker then:

1. resolves `*_llm_model_id` fields from live `Models` rows;
2. sorts specs by `execution_position`;
3. injects `pit_mode="research"` into Graph Nexus when requested;
4. applies `nexus_candidate_overrides` to an in-memory copy;
5. overwrites `_backtest_strategy_schema["strategies"]` with that effective list; and
6. preregisters and executes it.

The stored `BacktestResults.strategy_schema` therefore says what startup happened to read, not what
was queued. There is a second leak: `run_run_once_strategies` resolves model references again on
every invocation. A startup-only comparison would not be a freeze if a `Models` row could change on
bar 2. The opt-in path must suppress that per-invocation refresh after successful startup
verification; the default-off/live paths retain it exactly.

## Public and persisted contract

### Request

Add a strict boolean to `CreateBacktestBody`:

```python
freeze_execution_snapshot: StrictBool = False
```

Do not use coercing `bool`: JSON strings such as `"false"`, `"yes"`, and integers must be rejected.
Add the same final keyword argument to `action_create_backtest`, defaulting to `False`, and reject a
non-`bool` direct Python caller.

### Queue row (true only)

```json
{
  "freeze_execution_snapshot": true,
  "execution_snapshot": {
    "protocol_version": "queue-execution-snapshot-v1",
    "resolved_instance_id": "v2-let-run-core",
    "resolved_strategy_id": "193",
    "strategy_schema": {
      "name": "...",
      "experiment_spec": {},
      "strategies": []
    },
    "model_bindings": [
      {
        "strategy_index": 0,
        "config_field": "sentiment_llm_model_id",
        "model_id": "...",
        "identity": {
          "model_id": "...",
          "provider": "openrouter",
          "model": "openai/gpt-...",
          "adapter_fields": {},
          "access_material_present": true
        },
        "model_identity_sha256": "sha256:<64 lowercase hex>"
      }
    ]
  },
  "execution_snapshot_sha256": "sha256:<64 lowercase hex>"
}
```

The `strategy_schema.strategies` list is the full **ordered effective list**, preserving conditions,
config, weights, execution position/scope, decision phase, and any other JSON field on a valid spec.
It is constructed after model resolution, the research-PIT injection, and candidate overrides. The
schema contains `experiment_spec` because changing that between queue and preregistration is drift.
The snapshot intentionally does not duplicate `pit_mode` or candidate overrides in a second metadata
map: their effective values already occur in the strategy config, and duplicating them would make a
paired snapshot differ at two paths rather than the preregistered treatment path only.

`model_bindings` is one entry per top-level `*_llm_model_id` reference, ordered by final strategy
index and field name. `identity.adapter_fields` contains only resolver fields that affect dispatch:
provider/model, provider base URL or endpoint/version, reasoning settings, Ollama settings, Bedrock
region/reasoning, OpenRouter attribution/base URL, CLI path/validated args, and model cache family.
It never contains the `Models.api_key` plaintext, ciphertext, a reversible mask, or a hash of the
secret. `access_material_present` may record only presence/absence. A key rotation with the same
non-secret model/adapter identity is therefore allowed and startup uses the fresh in-memory key;
provider/model/endpoint/settings changes, deletion, inability to decrypt, or present-to-absent drift
fail. Identical remote model behavior still requires the model-evidence replay fixture.

Do not use field names such as `config_key` or `credential_source` in this persisted object: the
existing segment-based `persistence_safety` guard correctly treats `key` / `credential` segments as
secret-bearing. The proposed names above pass that guard.

### Canonical bytes

The new module owns one canonicalizer; do not import the private
`experiment_registry._canonical_json` and do not use `repr`, `default=str`, YAML, pickle, or a DB
serialization as the hash input.

* recursively require string mapping keys and reject collisions/non-string keys;
* mappings sort by key; arrays retain order; tuples become arrays;
* booleans remain booleans; finite integral floats normalize consistently with integers; `-0.0`
  normalizes to zero; reject NaN/infinity;
* timezone-aware datetime/date values normalize to UTC ISO-8601; reject naive datetimes;
* reject sets, bytes, callables, arbitrary objects, and non-JSON values;
* serialize UTF-8 with `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`, and
  `allow_nan=False`.

Persist `json.loads(canonical_json)` rather than the pre-normalized Python object. Compute
`"sha256:" + sha256(canonical_json.encode("utf-8")).hexdigest()`. Recomputing after a real RethinkDB
round trip must produce the same bytes and hash.

Before insertion call `assert_secret_free(execution_snapshot)`. At broker startup call it on the
stored snapshot again before hashing. Existing `sanitize_snapshot` redaction markers are acceptable,
but neither a plaintext nor encrypted model key may influence the digest.

### Result row (true only)

Both the initial `BacktestResults` stub and final update conditionally copy:

```json
{
  "freeze_execution_snapshot": true,
  "execution_snapshot": "<deep copy of the verified queue-time object>",
  "execution_snapshot_sha256": "<the queue-time hash>",
  "execution_snapshot_verification": {
    "status": "verified",
    "queue_sha256": "...",
    "startup_sha256": "..."
  }
}
```

Set `strategy_schema` from the queue-time snapshot's `strategy_schema`; never substitute the freshly
read schema merely because it compared equal. This preserves the audit object after the engine
deletes the queue row. Run the existing complete-payload `assert_secret_free` checks as today. False
runs receive none of these keys.

## Exact file and symbol plan

### 1. New `backend/backtest_execution_snapshot.py`

Keep all new resolution/canonicalization/gate logic out of `broker.py` so it is directly unit-testable.
Add:

* `ExecutionSnapshotError(code, *, queue_sha256=None, startup_sha256=None,
  differing_paths=())`; `str(error)` contains codes/paths, never values.
* `canonical_execution_json(value) -> str` and `execution_snapshot_sha256(value) -> str`.
* `ResolvedBacktestExecution`, containing secret-bearing `runtime_strategies` (memory only),
  secret-free `execution_snapshot`, and its digest.
* `resolve_effective_backtest_execution(...) -> ResolvedBacktestExecution`. Inputs are the resolved
  instance/strategy ids, raw strategy name/specs/experiment declaration, validated evidence options,
  and a DB connection. Deep-copy/scrub first; retain the same valid-spec predicate used by
  `load_strategies_from_db`; stable-sort by integer `execution_position`; strictly resolve models;
  inject research PIT only on `graph_nexus_analysis`; apply candidate overrides last; then sanitize,
  canonicalize, hash, and assert secret-free.
* `make_execution_snapshot_queue_fields(resolved) -> dict`, returning exactly the three true-only
  queue fields.
* `verify_execution_snapshot_contract(queue_fields, startup_snapshot,
  *, engine_expected_sha256=None) -> VerifiedExecutionSnapshot`. Require exact boolean `True`, the
  supported protocol, a valid digest, a secret-free stored object, stored-object rehash equality,
  optional engine-binding equality, and canonical-byte equality with startup. Produce a sorted,
  capped list of JSON paths on drift, with no old/new values.

The helper must invalidate the model cache once per whole resolution and use one connection so a
repeated model id has one row image throughout that resolution.

### 2. `backend/model_resolver.py`

Refactor without changing the public behavior of `resolve_model_refs_in_config`:

* add `ModelResolutionError`;
* add `resolve_model_refs_with_identities(conn, config, *, force_refresh=False,
  require_all=False) -> (resolved_config, safe_identities)`;
* make the existing function a compatibility wrapper returning only `resolved_config` with
  `require_all=False`.

The queue/startup snapshot resolver calls the new API with `require_all=True`. A non-empty referenced
id that is missing, malformed, undecryptable, or cannot be read is fatal. The identity builder uses an
explicit non-secret allowlist matching the resolver's `field_map`; it does not sanitize an entire
`Models` row (which would accidentally bind names/timestamps/pricing fields that runtime dispatch
never reads). `force_refresh=True` clears the cache once before walking all refs, not once per spec.
Existing live/default-off callers and TTL behavior stay unchanged.

### 3. `backend/backtest_evidence_options.py`

The effective snapshot must call the existing `validate_evidence_options` and
`apply_candidate_overrides`; do not implement a second override path.

For the preregistered anchor pair, add only `anchor_reinforce_target_pct` to the allowlist and validate
it as a real finite number whose value is exactly `12` or `20` (bools rejected). Return a canonical
numeric value. This is the smallest way to queue both arms against one unchanged Strategy row:
control passes 12, treatment passes 20, and startup re-applies the row-specific stored override before
comparison. Do not allow the API to override enablement, salt, caps, core floor, turnover, rally,
rotation, or regime fields; those remain common live-document inputs captured by the snapshot.

If this one-key extension is not approved in the same change, leave the current A1-A4 allowlist
unchanged but mark the anchor study blocked: editing the shared Strategy row between enqueue/startup
would correctly trip the gate and cannot be the arm mechanism.

### 4. `backend/interactive_utils.py::action_create_backtest`

Add `freeze_execution_snapshot=False` as the last keyword. Preserve the first part of the function,
especially evidence validation and the equity default to `pit_mode="research"`.

After the base `doc` and evidence block are final but before `insert_backtest_with_unique_id`:

1. validate the flag's exact type;
2. if false, execute no import/query/resolution/log branch and leave `doc` untouched;
3. if true, require an existing equity instance; reject `kind in {"crypto", "kalshi"}`; require a
   linked Strategy row and valid specs;
4. lazily import the new module, resolve with the already validated `_evidence`, and `doc.update()`
   the three true-only fields;
5. perform the existing single insert.

This is as atomic as the queue schema supports: snapshot, hash, and pending job are one RethinkDB
document insertion. RethinkDB does not provide a cross-table transaction over the preceding reads;
the concrete resolved output is nevertheless fully captured and startup must independently match it.
On any resolution/serialization/secret failure, insert nothing.

Conditionally add `execution_snapshot_sha256` to the returned API object only for true. Leave the
legacy five-key response unchanged for false.

### 5. `backend/api/main.py::CreateBacktestBody` and `api_create_backtest`

Add the strict default-false body field and pass it as the new action keyword. Do not place the flag
inside `evidence_options`, and do not persist false. Current mobile/web bodies that omit it remain
ordinary research-default backtests.

### 6. `backend/broker.py`

`broker.py` exceeds the GitNexus indexer cap, so manual caller analysis is required. Its relevant
internal callers of `load_strategies_from_db` are the early backtest load, the live snapshot-hash
preload, and the later live startup load; the latter two must remain unchanged.

Add broker CLI/global support for `--require-execution-snapshot` and
`--expected-execution-snapshot-sha256`, default absent/`None`. Add globals
`_execution_snapshot_verified=False`, `_verified_execution_snapshot=None`, and
`_verified_execution_snapshot_sha256=None`.

At the early backtest setup:

* load/revalidate the stored evidence options first, because PIT and candidate overrides are inputs;
* determine frozen intent from either the engine binding or the presence of any of the three queue
  snapshot fields; an inconsistent partial envelope is frozen-and-invalid, never legacy;
* reject a frozen non-equity runtime again (defense in depth);
* call `load_strategies_from_db()` for raw docs, then the shared effective resolver; empty/missing
  values that the legacy loader returns become fatal in this branch;
* verify the queue object/hash/engine binding/startup bytes;
* set `_cached_strategies` to the verified startup runtime realization (same non-secret bytes, fresh
  in-memory access material), but set `_backtest_strategy_schema` and result snapshot from deep copies
  of the queue-time public object;
* only then configure passive execution, build/activate evidence lifecycle, derive the seed,
  preregister, fetch data, construct the emulator, or split/run specs.

Put the current model-resolution/sort/research/candidate code under the `else` legacy branch without
textual/ordering changes so false remains behavior-identical. Do not change `load_strategies_from_db`
return shape or its crypto synthetic path.

In `run_run_once_strategies`, skip the per-invocation model re-resolution block only when the module
run mode is backtest **and** `_execution_snapshot_verified is True`. This keeps startup-resolved
access material in memory for the frozen run and prevents a later Models edit from changing bar 2.
Live and unfrozen backtests retain the exact hot-refresh behavior.

Before exiting on a gate failure, best-effort write a secret-free `BacktestResults` error row with
`status="error"`, `progress=100`, the error code, safe hashes, and differing paths. Never store values
or a regenerated schema on failure. Failure to write the error row must not turn the gate into a
warning; log and exit non-zero.

Pass `_verified_execution_snapshot_sha256` into `_preregister_backtest_experiment` only when true and
conditionally include it in the preregistered `effective_config`. The actual effective strategies are
already bound there; the explicit hash links registrations/results/pair manifests. When the optional
argument is `None`, do not add a key, preserving the old fingerprint.

### 7. `backend/engines/backtest_engine.py::run_one_backtest`

For a row with `freeze_execution_snapshot is True` **or any snapshot-envelope field present**, append
`--require-execution-snapshot` and the row's raw expected hash to the broker command. For a legacy row
append nothing, so its Docker command is byte-for-byte unchanged. This binds what the engine observed
to what the broker later reads and catches mutation/deletion between dispatch and broker startup.

The unprivileged integrity claim is limited: an administrator who can rewrite the flag, snapshot,
hash, all live config rows, and the row before the engine ever observes it can forge an unsigned
Rethink row. Preventing that requires an HMAC/signature key outside Rethink or an append-only registry,
which is outside this smallest slice. Tests must still cover partial-envelope downgrade, content/hash
tampering, engine-to-broker mutation, and live-doc drift.

### 8. Rerun and other callers

Caller policy must be explicit rather than accidental:

| caller | plan |
|---|---|
| REST `/backtests` | only initial public opt-in; strict bool, default false |
| `backend/cli.py::cmd_create_backtest` | no change; remains false/default-off |
| `backend/chatbot/tools.py::_exe_create_backtest` and tool schema | no change; remains false; do not let an LLM silently label a run frozen |
| `backend/engines/discord_bot.py::cmd_create_backtest` | no change; remains false |
| `backend/engines/ai_backtest_engine.py` HTTP POSTs | no change; remain false |
| current web/mobile ordinary create bodies | no change; omitted field is false |
| `backend/scripts/rerun_backtest.py` | add a strict `--freeze-execution-snapshot` opt-in (default false); never copy the old snapshot/hash. If opted in, pass the source row's validated `evidence` options so PIT/candidate treatment is rebuilt, then resolve a new queue-time snapshot against current DB state. |
| web/mobile “Rerun” buttons | remain false in this smallest slice and relabel/document frozen reruns as a fresh, unfrozen run; alternatively, a follow-up backend rerun endpoint must copy safe original evidence inputs and request a fresh freeze. Do not merely pass `true` while dropping candidate/PIT options and call that “same settings.” |

This policy makes every existing caller byte-compatible. A true frozen run is deliberately created by
an authenticated research caller, not by changing the meaning of existing UI/chatbot/agent actions.

## Stable log and failure signatures

Emit exactly one broker success line and one fatal line family; tests and operational preflight grep
these literal tokens:

```text
EXECUTION_SNAPSHOT_VERIFY_OK queue_sha256=<hash> startup_sha256=<hash> protocol=queue-execution-snapshot-v1
EXECUTION_SNAPSHOT_VERIFY_FAIL reason=<code> queue_sha256=<hash-or-none> startup_sha256=<hash-or-none> differing_paths=<comma-list-or-none>
```

No model ids, config values, endpoints, exception values, or credentials occur on the FAIL line.
Reason codes are stable: `queue_row_unreadable`, `contract_missing`, `partial_contract`,
`protocol_unsupported`, `digest_invalid`, `stored_hash_mismatch`, `engine_binding_mismatch`,
`non_equity_forbidden`, `startup_resolution_failed`, `startup_hash_mismatch`, and
`startup_content_mismatch`. Default-off runs emit neither token.

Preflight must grep `EXECUTION_SNAPSHOT_VERIFY_OK` in a real opt-in smoke run before claiming the
feature works; a queued hash alone is not evidence that the broker gate ran.

## Tests

### New `backend/tests/test_backtest_execution_snapshot.py`

1. Canonical mappings with different insertion order produce identical bytes/hash; arrays with
   different order do not.
2. Integral float/int, negative zero, Unicode, tuple, aware datetime/date, and a JSON round trip have
   the declared stable representation.
3. NaN/infinity, naive datetime, set, bytes, callable, arbitrary object, and non-string/colliding keys
   fail closed.
4. A nested model-key canary and its encrypted ciphertext are absent from canonical bytes; the whole
   snapshot passes `assert_secret_free`.
5. Each resolver-supported provider produces the expected non-secret identity/adapter fields and a
   stable `model_identity_sha256`.
6. Rotating only the model access material (present to present) leaves public identity/hash equal;
   changing provider/model/base URL/reasoning/CLI args or removing configured material changes it.
7. Missing/deleted/malformed/undecryptable referenced models fail in strict snapshot mode; the legacy
   wrapper retains its current permissive behavior.
8. Effective specs retain stable execution ordering and all fields, while input docs are not mutated.
9. `pit_mode=research` appears on only the effective Graph Nexus config before hashing; strict does
   not inject research.
10. Candidate overrides are applied to the effective copy before hashing.
11. Stored-content mutation with old hash, hash-only mutation, unsupported protocol, partial envelope,
    and engine-binding mismatch each yield the exact safe reason code.
12. Queue/startup snapshots built from identical docs pass; changing instance strategy link,
    Strategy name/spec/experiment declaration, or a non-secret Models identity fails with only JSON
    paths in the error.
13. Rethink-compatible serialize/deserialize/re-hash is identical.

### Extend `backend/tests/test_backtest_candidate_overrides.py`

* exact numeric 12 and 20 are accepted for `anchor_reinforce_target_pct`;
* bool, string, NaN/infinity, values other than 12/20 are rejected;
* only Graph Nexus receives the value and the source specs remain unchanged;
* control-12 and treatment-20 canonical snapshots have exactly one differing executable path:
  `strategy_schema.strategies[<graph-index>].config.anchor_reinforce_target_pct`; model bindings,
  research PIT value, execution enable/caps/salt/turnover/core-floor/regime fields are identical.

### New `backend/tests/test_backtest_execution_snapshot_queue.py`

Use the existing fake-ReQL/captured-insert style and freeze time/random id.

* omitted and explicit false produce a queue dict and action response exactly equal to a checked-in
  legacy golden object; no snapshot import/resolver is called and no new log signature appears;
* API omission forwards false without persisting it; true forwards true; non-boolean JSON gets 422;
* true writes all three fields in the same single insert, returns the hash, and rehashes correctly;
* equity default `pit_mode=research` is already reflected in the effective snapshot; explicit strict
  is not rewritten;
* a candidate override is in the snapshot and the live Strategy input is unchanged;
* inline/Models canaries are absent from the complete queued row;
* missing instance/strategy/model and serialization/secret errors insert no row;
* crypto and Kalshi true requests fail with the stable equities-only error; false retains the current
  credential/fee compatibility path.

### New `backend/tests/test_broker_execution_snapshot_gate.py`

Test shared orchestration with fakes rather than importing all of `broker.py`, plus a small AST/source
ordering test because `broker.py` executes at import time.

* true identical startup verifies and selects queue schema/hash for results;
* every failure reason exits non-zero and a fake passive setup/preregister/fetch/emulator/strategy
  counter remains zero;
* verification call appears before `PortfolioEmulator.set_passive_execution`,
  `_preregister_backtest_experiment`, `fetch_alpaca_historical_bars`,
  `create_backtest_emulator`, and the first strategy-run call;
* true suppresses per-invocation model DB resolution after startup; false backtest and live still call
  it;
* success/failure log lines equal the literal signatures above and failure text contains no canary;
* stub and final result copy the queue object/hash; false result payload is exactly the legacy golden;
* true failure writes safe error metadata when DB is available and remains fatal when the write fails;
* tampering the row after engine dispatch is caught by the expected-hash binding.

### Extend engine/API/caller tests

* `backend/tests/test_backtest_engine_queue_health.py`: legacy command list unchanged; true/partial
  envelope adds require/expected arguments; crypto true never reaches launch.
* `backend/tests/test_backtest_evidence_api.py`: true field forwarding coexists with evidence options;
  off/false remains inert.
* `backend/tests/test_backtest_research_default.py`: existing mobile/web source bodies still omit the
  flag and default to the unchanged action behavior.
* `backend/tests/test_alpaca_secret_boundaries.py`: complete true queue/result snapshot has no broker
  or model credential material.
* `backend/tests/test_nexus_evidence_matrix_script.py`: when a paired research payload explicitly
  requests freezing it carries the boolean and both arms' non-treatment executable bytes compare
  equal. Do not silently turn all existing matrix runs on.
* `backend/tests/test_preregistration...` (or the existing task-6 remediation test): verified hash is
  present before registration only for true; false experiment fingerprint is unchanged.
* tests for CLI/chatbot/Discord/AI-engine calls assert they omit the new keyword and remain false.

Suggested focused command after implementation (using the backend project's normal interpreter):

```bash
python3 -m pytest -q \
  backend/tests/test_backtest_execution_snapshot.py \
  backend/tests/test_backtest_execution_snapshot_queue.py \
  backend/tests/test_broker_execution_snapshot_gate.py \
  backend/tests/test_backtest_candidate_overrides.py \
  backend/tests/test_backtest_evidence_api.py \
  backend/tests/test_backtest_research_default.py \
  backend/tests/test_alpaca_secret_boundaries.py \
  backend/tests/test_backtest_engine_queue_health.py
```

Then run the full backend suite. Before any commit, run `npx gitnexus detect-changes`; before any push,
confirm no backtest is running because every push to `main` auto-deploys and interrupts it.

## Acceptance gates

The implementation is complete only when all are true:

* false/omitted queue, response, engine command, broker behavior, preregistration fingerprint, result
  payload, and logs match legacy goldens;
* true queue insertion is one atomic document containing a canonical secret-free snapshot/hash;
* a real DB serialization round trip rehashes identically;
* queue/startup equality is checked before every execution-side effect and can be proven by the OK
  log signature;
* stored tampering, live instance/strategy/model drift, partial/downgraded envelopes, and queue-read
  failure are fatal, never warnings/fallbacks;
* the verified backtest never rereads Models during strategy execution;
* crypto/Kalshi cannot opt in and their false path is unchanged;
* results copy the queue-time object/hash, and preregistration is linked to that hash;
* a 12/20 pair against one unchanged Strategy row differs at exactly the registered target path (if
  the narrow anchor override is approved);
* no claim is made that research PIT, mutable state, market data, graph, source, or model responses are
  frozen by this slice.

## Blast radius recorded before implementation

GitNexus index status was current at commit `dee56d6` (indexed 2026-08-11). Upstream impact results:

* `action_create_backtest`: **LOW**, 3 direct callers, 6 total; direct callers are chatbot, CLI, and
  `scripts/rerun_backtest.py` (HTTP/Discord/AI callers require manual search).
* `CreateBacktestBody`: **LOW**, 1 direct import, 3 total.
* `api_create_backtest`: **LOW**, no indexed upstream callers.
* `resolve_model_refs_in_config`: **LOW**, 1 indexed direct / 8 total; manual search additionally found
  four broker call sites because the broker file is not indexed.
* `validate_evidence_options`: **LOW**, 1 direct / 6 total.
* `apply_candidate_overrides`: **LOW**, no indexed upstream callers; manual broker call exists.
* `run_one_backtest`: **LOW**, no indexed upstream callers.

GitNexus cannot index `broker.py` at its current size, so `load_strategies_from_db`, `parse_args`,
`run_run_once_strategies`, and `_preregister_backtest_experiment` returned unknown/not found. Manual
analysis above is mandatory for those symbols. No HIGH or CRITICAL indexed risk was reported.
