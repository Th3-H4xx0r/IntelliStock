# Queue snapshot vertical-slice readiness review

Date: 2026-08-12
Reviewed commit: `06defae` (`Add authenticated execution snapshot foundation`)
Scope: static architecture review only; no production database read/write, provider call, backtest, container launch, or application-code change

## Verdict

**BLOCK integration and BLOCK exposing an API opt-in.** The pure module in `06defae` is safe to keep
merged because it is unused, but there is not yet a safe executable vertical slice. The first public
`execution_snapshot_mode="execute"` request must not ship until the queue creator, engine and broker
are changed together and the supported configuration/access contract is complete.

The answer to “should integration wait for the complete config schema and access revisions?” is
**yes**. “Complete” need not mean every strategy in IntelliStock: the smallest v1 may continue to
support only one equity `graph_nexus_analysis` spec and OpenRouter. It must, however, positively
represent **every executable input of that supported profile**, reject every unknown field, and then
execute only that representation. The current foundation cannot represent the currently documented
Graph Nexus configuration or its model-role/access behavior.

A safe default-OFF slice is one coordinated release, not an API-first sequence:

1. a server-side collector produces the exact supported public core and access bindings;
2. the queue preallocates a snapshot-required ID, signs the complete document and inserts it once;
3. the engine atomically claims and refreshes/verifies that document before result/config/credential
   work, then launches from the signed core and passes a required digest binding;
4. the broker verifies immediately after argv parsing, before instance classification, access
   hydration or telemetry, hydrates only exact versioned access rows, and executes only the core;
5. all live row/model/access refresh paths are bypassed for a verified snapshot; and
6. results and API endpoints expose only a safe mode/version/digest/verdict projection.

Any subset that allows an `execute` row to be queued is half-integrated and unsafe. Access-revision
writers/migration and additional pure schema/collector code may be deployed earlier while unreachable,
but the public flag must remain absent until the full path passes.

## What `06defae` provides—and what it does not

`backend/backtest_execution_snapshot.py` is a strong pure boundary:

- strict bounded canonical JSON and a 512 KiB maximum;
- an exact positive v1 schema;
- SHA-256 over the snapshot and HMAC over row ID, `created_at`, mode, signer, digest and body;
- constant-time digest/HMAC comparisons;
- partial-envelope and required/digest-binding failure modes;
- a body/signature-free public syntactic projection; and
- adversarial golden/contract tests.

It intentionally has no collector, DB access, key loader, queue/API call site, engine verification,
broker bootstrap/hydration, result projection, or execution switch. The current imports confirm it is
not used by API, queue action, engine or broker.

## Current startup order: the gate is presently far too late

The current broker is a module-level program. Its relevant order is:

1. imports include `PortfolioEmulator`, `llm_utils`, `model_resolver`, persistence and secret helpers
   (`broker.py:25-37`);
2. `.env` is loaded (`:350`);
3. argv is parsed and copied to globals (`:720-734`);
4. `_non_equity_runtime = _is_non_equity_instance_runtime()` is evaluated (`:819`). That calls
   `_instance_kind_and_crypto_config`, whose intended read is current `Instances` and a linked
   `BrokerageAccounts` row (`:745-782`);
5. live or equity-backtest credential discovery/decryption runs from the current instance links
   (`:820-907`);
6. only later are the general RethinkDB `get_conn` helpers defined (`:1884-1938`);
7. `_init_llm_telemetry()` executes at module import (`:1941-2033`) and installs a per-call current
   `Models` pricing lookup, including provider/model scans (`:1950-2009`);
8. the backtest queue row/evidence is not read until `_load_backtest_evidence_options` at `:9399`;
9. current instance/strategy rows are loaded at `:9419`, and current model rows are force-refreshed at
   `:9444-9452`.

There is an additional current-order defect relevant to integration: `get_conn` is not defined until
`:1891`, but the first classification attempt occurs at `:819`. On this source image the resulting
`NameError` is caught by `_instance_kind_and_crypto_config`, which returns the equity-looking fallback.
A snapshot integration must not build on that accidental fallback.

### Required broker bootstrap point

Verification must run immediately after successful argv parsing (`:720-734`) and before the first
classification call at `:819`. Because the normal DB helper does not yet exist, use a small bootstrap
module with a bounded direct RethinkDB connection rather than calling later broker helpers or moving a
late gate upward piecemeal.

For every engine-launched backtest the bootstrap must:

1. parse `--require-execution-snapshot` and
   `--expected-execution-snapshot-sha256`;
2. load the exact `BacktestInstances` row once by ID;
3. infer required mode from trusted launch policy as well as the CLI binding;
4. load a dedicated HMAC key and verify the complete envelope, ID, `created_at`, schema, SHA, HMAC and
   expected engine digest;
5. compare all ordinary argv values with signed `core.run` values (instance, symbol mode/list, dates,
   granularity, cash, fee/evidence/seed); and
6. compare source, module, dependency and executing image identities.

Only then may the broker establish signed equity classification, hydrate access, initialize telemetry,
create/write a result stub, configure passive execution, construct evidence/preregistration, fetch
bars, create the emulator, import/execute strategies or start a watcher. Gate failure must write at
most a value-free terminal result and exit nonzero; it must not call the ordinary path.

## Queue ID allocation and atomicity

The HMAC binds `backtest_id`, so the existing allocator cannot be used as-is:

- `action_create_backtest` builds `doc` without an ID, then calls
  `insert_backtest_with_unique_id` (`interactive_utils.py:5648-5683`);
- that helper chooses an ID inside the insertion loop (`:840-850`), after the caller would need it for
  HMAC creation;
- it assumes duplicate-key writes raise. RethinkDB writes normally return a write-result document;
  unlike `action_create_strategy` (`:4955-4962`), this helper does not check `inserted`, `errors` or
  `first_error`, so a collision can be reported as success without proving insertion; and
- RethinkDB has no transaction covering the preceding `Instances`/`Strategies`/`Models`/access reads.

### Required queue algorithm

1. Strictly validate snapshot-only launch inputs and resolve the public core twice. Require equal
   canonical bytes, with bounded retry, to detect concurrent relinking during cross-table reads.
2. Generate `created_at` once in the exact canonical UTC form.
3. Preallocate an ID **before** HMAC generation.
4. Build the five envelope fields for that ID and merge them into the complete pending row.
5. Perform one `insert(..., conflict="error")`; require `inserted == 1` and `errors == 0`.
6. On a duplicate only, choose a new ID, recompute the ID-bound HMAC and retry. Do not log the write
   query/result body. On any other error, insert nothing and return a stable value-free failure.
7. Never insert a draft and patch the snapshot later.

The complete row insertion is atomic. The multi-table collection is not; double collection plus
self-contained execution and exact access revisions is the necessary boundary.

### Downgrade-resistant required policy

HMAC does not detect deletion of all five optional fields before an engine observes a row. A second
unsigned boolean in the same row does not fix that. The smallest code-local policy is to reserve a
non-overlapping integer ID namespace for snapshot requests (legacy allocation is currently six-digit)
and make both engine and broker treat that namespace as unconditionally required. The HMAC already
binds the ID. An independently authenticated scheduler/experiment manifest is also acceptable.

Without one of those policies, a DB writer can turn an opted-in row into an ordinary row by deleting
the entire envelope. This is a release blocker, not documentation-only risk.

## HMAC key-management gap

The foundation accepts caller-supplied raw `bytes` of length 32–4096
(`backtest_execution_snapshot.py:550-553`). There is no production key name, parser, generator,
rotation policy or container propagation.

Minimum operational contract:

- add a dedicated stable key, for example `BACKTEST_EXECUTION_SNAPSHOT_HMAC_KEY`, encoded as exactly
  64 lowercase hex and decoded centrally to 32 bytes;
- do not use `INTELLISTOCK_CRED_KEY`, a model/broker secret, JWT secret, or the socket-control key;
- generate it in `install.sh`/`install.ps1`, document it in `.env.example`/`SECURITY.md`, and require it
  only when creating or executing snapshot rows;
- API and engine already load `.env`; the engine must forward the new key to the broker only for a
  required snapshot row, in environment—not argv;
- a false/absent mode must not read or require the key; an execute request with no key fails before
  insertion; an engine/broker with a required row and no/wrong key fails before other work;
- never log, persist, return or fingerprint the key; and
- either prohibit rotation until the snapshot queue is drained, or extend the envelope with a bounded
  non-secret key ID and retain old verification keys. The fixed current signer has no key ID.

HMAC protects the queue envelope from a writer who lacks this key. It does not by itself authenticate
mutable access rows or prevent full-envelope deletion; those need the separate controls above/below.

## `access_revision` is currently fictional

The v1 schema requires a nonnegative integer `access_revision` for every Models binding
(`backtest_execution_snapshot.py:469-472`) and both brokerage bindings (`:474-481`). No application
row currently creates, increments or verifies such a field; outside the foundation/tests/docs there
are no references.

Concrete missing writers:

- Alpaca creation omits it (`interactive_utils.py:7603-7618`);
- Alpaca update changes key/secret at `:7695-7737` and writes at `:7778` without incrementing it;
- Models creation omits it (`:8253-8293`);
- Models edit changes `api_key` at `:8352-8382` without incrementing it; and
- credential migration rewrites encrypted Brokerage/Models material directly
  (`api/main.py:3055-3063`) without incrementing it.

Required prerequisites:

1. add `access_revision` to new Models and BrokerageAccounts rows;
2. backfill existing **strictly encrypted** usable rows once; do not bless plaintext/empty rows;
3. atomically increment exactly once whenever any access material changes (including migration), and
   not for name-only edits; define whether paper/feed/mode changes increment or are separately exact-
   compared;
4. cover every writer, including admin migration and future import scripts;
5. at enqueue, require encrypted material presence without decrypting and record the exact row ID and
   revision;
6. at broker bootstrap, fetch only that ID, require the exact revision/type/mode/feed, then call
   strict decryption once; no instance-link lookup, table scan, environment fallback or alternate row;
7. if trading and market-data bindings name the same row, fetch/hydrate one consistent row image; and
8. after hydration, retain that access in memory for the run and do not refresh on rotation.

For an adversarial DB-writer threat, a revision stored beside the ciphertext is only a cooperative
version: a writer can replace/copy ciphertext and leave or roll back the integer. Either explicitly
scope access-row tampering out of the security claim, enforce DB writes through trusted writers, or
add an access-row attestation/versioned external secret store. Queue HMAC must not be advertised as
solving that problem.

## Public configuration schema is not execution-complete

The current positive schema is deliberately much narrower than the running strategy:

- `_SPEC_CONFIG_FIELDS` permits 11 names (`backtest_execution_snapshot.py:68-80`), while the checked-in
  `graph_nexus_analysis.py` `INTELLISTOCK_SCHEMA` currently declares 198 config keys, including nested
  controls. That header itself contains secret placeholders and therefore cannot simply be copied.
- v1 requires exactly one `graph_nexus_analysis` spec, `conditions == {}`, `name == "Nexus Only"`,
  `experiment_spec is None` and only one model role (`backtest_execution_snapshot.py:406-457`).
- the current runtime preserves and executes arbitrary valid spec/config fields
  (`broker.py:5686-5702`) and merges config/conditions (`:6330-6334`). Dropping unsupported fields from
  the signed projection while executing them would be a false signature.
- Graph Nexus resolves default, sentiment, company-article, macro-article, event-maintenance, overlay,
  lookback and dynamic analyst-panel roles, with config and environment fallback chains
  (`graph_nexus_analysis.py:944-1324`). The foundation permits only `role_prefix == ""` and cannot map
  multiple role paths or duplicate model IDs.
- nonempty candidate overrides are currently rejected as metadata-only
  (`backtest_execution_snapshot.py:434-437`), so the intended treatment-arm mechanism is not yet
  representable.
- the adapter lists pricing fields, but OpenRouter v1 requires every field except
  `openrouter_base_url` to be null (`:458-468`). Broker telemetry currently rereads pricing from
  mutable Models rows (`broker.py:1950-1988`) and otherwise uses `llm_pricing.yaml`; neither behavior
  is frozen by this schema.
- the runtime core requires image/dependency/source/module identities, but there is no collector.
  Existing `source_tree_digest` and broker dependency helpers return `sha256:`-prefixed values while
  the schema expects bare hex for two fields. The engine launches a mutable image tag
  (`backtest_engine.py:656-658`), not a verified immutable ID.
- current seed behavior is supplied by `BACKTEST_SEED`/derived runtime logic, but the API request has
  no explicit snapshot seed even though the core requires one.
- Neo4j endpoint/user/secret identity, effective telemetry pricing, deterministic-mode environment,
  Graph Nexus environment fallbacks and other provider access are not covered by the current runtime
  environment block.

Before integration, create a code-owned allowlisted schema/collector for the exact supported profile.
It must reject an unknown raw spec/config field rather than sanitize/drop it, normalize defaults only
where runtime uses the same normalized value, bind every model role/path, resolve concrete effective
pricing, and prove the broker materializer produces exactly the public runtime config plus separately
hydrated secrets. A specially created minimal Graph Nexus profile is acceptable; the existing large
profile is not supported until all its executable fields are represented.

## Engine claim, refresh and digest binding

The engine currently queues full row images from changefeed/sweeps (`backtest_engine.py:860-896`) and
can hold them through high-difficulty deferral and CPU waits. It then:

- reads current instance/strategy rows for difficulty before launch (`:261-314`, `:1063-1129`);
- atomically changes `pending -> running` but does not retrieve the claimed new row (`:1107-1127`);
- creates two result stubs from the stale row (`:1126-1127`);
- waits for CPU (`:1193`) and submits the original stale row (`:1203`); and
- `run_one_backtest` again classifies the current instance and may decrypt/copy credentials before
  constructing argv (`:566-645`). It never refreshes or verifies the queue envelope.

Required execute-mode path:

1. determine required policy from the trusted ID namespace/manifest or any envelope field;
2. atomically claim pending status with `return_changes` (or claim then exact get), take the fresh
   `new_val`, verify its HMAC/schema and compare its mirrored launch columns to the signed core;
3. do not query current instance/strategy for scheduling; calculate supported difficulty from the
   verified spec or use a conservative fixed value;
4. only after verification create a safe result stub;
5. after any deferral/CPU wait, immediately re-read the row, require `running`, reverify it and require
   the same digest observed at claim;
6. construct argv only from signed core values; snapshot v1 is equity, so pass no brokerage secrets;
7. inspect/launch an immutable image and compare its ID with the signed image digest;
8. append `--require-execution-snapshot` and
   `--expected-execution-snapshot-sha256 <verified digest>`; pass no body/HMAC/access IDs via argv;
9. launch with a snapshot-specific environment/mount allowlist, not generic LLM/API fallbacks; and
10. on any failure write a value-free terminal result and never launch the broker.

The broker then independently reloads and verifies the row and expected digest. This catches mutation
or deletion between engine refresh and process startup. Operational `status/run/paused` stay outside
the signed core; every immutable launch input stays inside it.

## Execute-snapshot authority and model-refresh bypass

A successful HMAC check is not enough if current rows can still supply behavior. For verified mode:

- do not call `load_strategies_from_db`; materialize ordered specs from the verified core;
- do not run the startup `resolve_model_refs_in_config(... force_refresh=True)` block
  (`broker.py:9439-9452`);
- in `run_run_once_strategies`, skip the per-invocation resolver at `:6268-6315` only when the module
  run mode is backtest **and** the snapshot context is verified. The function parameter named `mode`
  is scheduler mode and must not be used for this gate;
- use only exact once-hydrated model access and disable all role/provider/environment fallback;
- make `_init_llm_telemetry` use the snapshot's concrete pricing map and never query `Models`;
- make `_resolve_data_brokerage_creds_now` / discovered-symbol loading use the once-hydrated data
  binding rather than rereading current `Instances`/BrokerageAccounts (`broker.py:1686-1861`);
- set evidence, seed, kind, fee, symbols and strategy schema from the verified core; and
- current `Instances`/`Strategies`/public `Models` reads, if retained at all, are path-only drift
  telemetry after authority is established and cannot alter execution or turn deletion into fallback.

The experiment preregistration must include snapshot mode/version/SHA in its effective config and use
the same public specs. The running stub, terminal result, evidence receipt and logs must carry the same
digest. None may regenerate an “equivalent” snapshot from current rows.

## Result and API projection

Never copy the full core or HMAC into `BacktestResults`. It contains internal model/broker access row
IDs even though it is secret-free. Store one safe projection, for example:

```json
{
  "execution_snapshot": {
    "mode": "execute",
    "schema_version": "queue-execution-snapshot-v1",
    "sha256": "sha256:...",
    "verification": "verified"
  }
}
```

A failure projection may add a bounded stable reason code, but must not include paths containing raw
field names/values, snapshot body, signature, access IDs, ciphertext or key metadata. Build
`strategy_schema` separately from the verified public specs, stripping runtime access bindings and
secret-hydrated fields; never sanitize a secret-bearing runtime copy and treat the result as authority.

Current API actions are explicit projections but need the new safe fields added deliberately:

- list uses `pluck` whitelists (`interactive_utils.py:5181-5194`, `:5228-5245`);
- status constructs selected keys (`:5955-5982`);
- summary constructs selected keys and returns `strategy_schema` (`:6122-6191`); and
- graph/playback endpoints also construct selected responses.

Create response should return only ID, mode, version and SHA for execute, while the off response stays
exactly the existing five keys. `CreateBacktestBody` should add only
`execution_snapshot_mode: Literal["off", "execute"] = "off"` (plus a strict snapshot seed if chosen)
and set `extra="forbid"`; clients must not submit body/hash/HMAC/access bindings. Off must execute no
snapshot import, DB read, key read, field persistence, command change, result field or new log.

## Smallest safe release sequence

### Prerequisite releases (behavior-inert with respect to snapshot execution)

1. Add/migrate atomic access revisions and tests for every credential writer.
2. Add dedicated HMAC key generation/configuration and missing-key tests without consuming it on off.
3. Finish the exact supported public config/model-role/runtime/access schema and pure collector/
   materializer tests. Resolve immutable image/dependency/source identity.
4. Choose and implement trusted required policy (reserved ID namespace is the smallest option).

### One coordinated vertical integration release

5. Add strict API mode and server-only collection/signing plus collision-safe one-insert queue logic.
6. Add engine claim/refresh/HMAC verification, immutable image check, safe stub and expected-digest
   launch binding.
7. Add early broker bootstrap, exact access hydration, snapshot authority, telemetry/data/model refresh
   bypass, argv/runtime comparison and safe failure persistence.
8. Add result/evidence/preregistration digest propagation and API whitelist projections.

Do not deploy steps 5–8 partially with the API mode reachable.

## Acceptance gates before GO

- Default-off golden tests prove identical request acceptance for valid existing bodies, queue document,
  create response, engine command/environment and broker live-refresh behavior.
- Execute-mode end-to-end test uses fake DB/provider/data boundaries and proves no current
  Instance/Strategy/public Model read, decrypt, telemetry setup, result write or provider/data work
  occurs before HMAC verification.
- Queue collision tests use Rethink-style write result documents, prove HMAC is recomputed for the new
  ID and prove there is never a partial row.
- Missing/partial/deleted envelope, recomputed SHA, wrong/missing key, wrong ID/time, unsupported schema,
  oversize body, stale engine row and engine-to-broker mutation all fail closed.
- Required-policy test deletes every marker from a snapshot-required ID and still blocks legacy
  fallback.
- Access tests cover create, edit, name-only edit, key/secret rotation, migration, missing/wrong/rolled
  revision, wrong ID/type/mode/feed, plaintext, decrypt failure and same-row dual binding.
- Config coverage test feeds the exact supported raw Strategy fixture and fails on every unknown or
  dropped field; materialized secret-free runtime config matches the signed public config.
- Model-role tests cover duplicate model IDs, every role prefix/path, concrete pricing and no
  provider/environment fallback.
- Broker tests prove both startup resolution and every-bar model resolution are bypassed only for a
  verified backtest; ordinary backtests and live mode remain unchanged.
- Engine test refreshes after CPU wait and launches only immutable image/digest-matching args.
- Result/API tests prove body, HMAC, access identities, ciphertext and secret material never appear in
  queue responses, results, list, status, summary, playback, logs or errors.
- A controlled non-production smoke must produce the stable verification success signature before
  operational GO; no anchor/causal P&L run is authorized by this control-plane slice.

## Final decision

- **GO:** retain `06defae` as an unused pure foundation and proceed with inert prerequisite work.
- **BLOCK:** queue/API/engine/broker integration or any reachable `execute` request today.
- **Exact unblock:** complete the supported config/model-role/runtime schema and collector; introduce
  real versioned access rows and hydration; establish dedicated HMAC key operations plus a trusted
  required policy; then land and validate the complete queue → engine → early broker → result
  projection slice as one coordinated release.
