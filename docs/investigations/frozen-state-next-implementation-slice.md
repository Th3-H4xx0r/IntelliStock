# Frozen state: next implementation slice after `06defae`

Date: 2026-08-12
Status: investigation and implementation plan only — no production code/config was changed and no backtest was launched

## Decision

`06defae` is a sound **pure authenticated execution-snapshot foundation**, but it does not yet
make a backtest frozen and is intentionally not wired into queueing or execution. The next work
should not be a queue/API flag and should not be another target-12/target-20 run.

The smallest next slice with real leverage is to make the already-declared model replay object
**readable, fully hydrated, and auditable from a fresh process**. Today `evidence_mode="replay"`
cannot do that: the durable stores cannot load a fixture, the replay session receives an empty
model ledger, and success never calls the session's completeness audit. This slice can remain
completely inert for ordinary backtests and can be tested with fake storage and provider spies.
It removes a false foundation before a larger frozen-state bundle is built on top of it.

Immediately after that repair, add a new pure `frozen-paired-state-v1` contract module. It should
bind the Rethink baseline, process runtime state, exact PIT/model/graph/market fixtures, write and
provider policy, runtime/image/environment attestations, restore receipts, and the target-12
negative-control comparison. The contract module must remain unused until export/restore and
runtime enforcement exist together.

**No target-12 negative control is ready to run now.** The live evidence store has matrices but no
builds, calls, fixtures, or receipts; it has only one PIT manifest, dated after every preregistered
W0-W3 window; the execution snapshot is not collected or executed; and there is no production-store
write or network denial.

## 1. What exists now

### 1.1 Authenticated public execution contract (`06defae`)

`backend/backtest_execution_snapshot.py` is pure and currently unimported by the queue, engine, and
broker paths. It provides:

* bounded, DB-stable canonical JSON;
* a positive, secret-free v1 schema;
* SHA-256 plus HMAC over the snapshot body, queue row ID, creation time, mode, and signer;
* strict partial/downgrade/tamper detection; and
* a safe public status projection.

The v1 schema already has fields for source tree, image digest, dependency/runtime digest, Python
version, one strategy-module digest, seed, timezone, and
`NEXUS_BACKTEST_SNAPSHOT_WRITE=off`. It also admits the anchor execution fields, including
`anchor_reinforce_target_pct`.

This is a contract, not an attestation pipeline. There is no collector, queue envelope, engine
binding, broker verification, or execute-snapshot path. V1 represents only one `Nexus Only` /
`graph_nexus_analysis` spec, one OpenRouter model, a small config allowlist, and no candidate
overrides. It cannot represent the complete effective document-193 strategy. The engine still
launches a mutable image tag on the normal deployment network.

### 1.2 Replay and model-evidence primitives

`backend/backtest_replay.py` already has useful immutable pieces:

* `ExperimentMatrixManifest` content-addresses preregistered arms, windows, cost scenarios,
  recording order, failure/selection rules, and implementation hashes.
* `ReplayFixture` binds an ordered PIT manifest chain, model-ledger hash, per-arm semantic request
  sets, seed manifest, and benchmark manifest.
* `FixtureBuild` publishes model call rows before the final fixture manifest.
* `ReplayReceipt` binds the arm/experiment/fixture, source identity, runtime-digest-shaped value,
  cost model, ordered decision/fill ledger hash, and five audit booleans.
* `ModelEvidenceSession` itself correctly denies undeclared replay IDs, replay misses,
  over-consumption, provider publication during replay, and unused declared occurrences **when
  `finalize()` is called with a populated ledger**.
* LLM plain/structured call seams return recorded values before a provider call; mutable
  LLM-derived caches are centrally denied in every evidence mode.

The ordinary/off path is default-inert. Tests verify it writes no evidence.

### 1.3 Strict PIT and Neo4j query replay

There is a strong storage-neutral PIT seam:

* `point_in_time_registry.py` stores content-addressed, append-only `graph`, `fundamentals`,
  `universe`, and `news` payloads in RethinkDB and publishes the manifest last.
* `point_in_time_graph.py::RecordingGraphDriver` records normalized read queries, parameters, and
  ordered result occurrences. Its normalizer rejects write Cypher.
* `ReplayGraphDriver` has no live delegate, fails on an unrecorded/exhausted query, and rejects
  unused occurrences through `assert_replay_complete()`.
* `GraphNexusAnalysis.run_historical()` resolves all four required datasets before strategy work,
  routes graph operations to the replay driver, routes Alpaca/Google/Benzinga news to the snapshot,
  and audits graph occurrence consumption.
* PIT graph-derived in-memory caches are reset/namespaced when manifest/as-of scope changes.

This query-result fixture is preferable to cloning a current Neo4j database for measured arms, as
long as every query is covered and the container cannot reach live Neo4j. It is not a full Neo4j
snapshot, and only the one recorded query ledger currently stored is available.

### 1.4 Narrow write protections

Some protections exist, but each is narrower than full frozen execution:

* active model-evidence sessions bypass reads and writes for the enumerated LLM-derived caches;
* `ReplayGraphDriver` is read-only;
* `NEXUS_BACKTEST_SNAPSHOT_WRITE=off` suppresses the final backtest-to-`NexusStrategyCache` write;
* the strict PIT news/graph providers do not fall back to current providers on a missing fixture;
  and
* end-of-run receipt source checking compares the executing Python-tree digest with the
  preregistered source-tree digest.

These do not deny ordinary Graph Nexus state writes, DDL, market-data cache writes, network calls,
or production-store access.

## 2. Read-only deployed-store inventory

A read-only table/count inventory was taken during this investigation. It did not launch a run or
call any helper that creates tables.

### Replay/PIT publication state

| table/artifact | observed state |
|---|---:|
| `backtest_replay_matrices` | 10 rows |
| `backtest_replay_fixture_builds` | 0 rows |
| `backtest_replay_calls` | 0 rows |
| `backtest_replay_fixtures` | 0 rows |
| `backtest_replay_receipts` | 0 rows |
| `PointInTimeManifests` | 1 row |
| `PointInTimeDatasetSnapshots` | 4 rows (exactly graph/fundamentals/universe/news) |

The sole PIT manifest is finalized and labelled `strict_verified`, but its `as_of` is
`2026-08-04T17:20:00.000297Z` and its `code_revision` is `unknown`. Its graph payload contains 35
query identities and 40 occurrences. There are no PIT manifests at or before W0-W3 decisions:
W0-W3 all end by 2026-07-01, while registry resolution requires `manifest.as_of <= decision_as_of`.
Therefore every planned strict historical arm currently fails before its first decision.

A snapshot captured on August 4 cannot be relabelled as January-July PIT evidence. The old windows
need a separately validated historical reconstruction from sources with provable availability and
graph effective dates, or the study needs forward windows captured prospectively.

### Representative mutable shared state

| shared table | observed rows |
|---|---:|
| `GraphNexusDiscoveredStocks` | 6,957 |
| `GraphNexusDiscoverySnapshots` | 11 |
| `GraphNexusActiveEvents` | 4,399 |
| `GraphNexusActiveEventHistory` | 9,458 |
| `GraphNexusActiveEventMaintenance` | 988 |
| `GraphNexusOverlayBarsCache` | 4,494 |
| `GraphNexusNewsCache` | 453 |
| `GraphNexusLLMPromptCache` | 282,349 |
| `GraphNexusLearningCache` | 69 |
| `GraphNexusOutcomeSeries` | 267,576 |
| `GraphNexusTradeContexts` | 477,274 |
| `NexusStrategyCache` | 120 |

These counts are not a baseline manifest; they demonstrate that the shared research store is warm,
large, and mutable. A selective reset is not an equally-warm clone.

## 3. Critical gaps and misleading green paths

### 3.1 Durable replay cannot currently be consumed

The largest immediate defect is between the pure contracts and a fresh broker process:

1. Neither `InMemoryReplayStore` nor `RethinkReplayStore` implements `get_fixture()`, although
   `EvidenceRunLifecycle._declared_occurrences()` and `succeed()` call it.
2. `ReplayFixture` has no `from_doc()` validation path.
3. `RethinkReplayStore` has no call-row reader/fixture-ledger hydrator. A fresh store's `_calls` and
   `_fixtures` caches are empty.
4. In replay mode, `EvidenceRunLifecycle.begin()` passes `ledger=None` to `ModelEvidenceSession`.
   The session creates an empty ledger, so every declared model call would be a replay miss even if
   fixture lookup existed.
5. `EvidenceRunLifecycle.succeed()` never calls `ModelEvidenceSession.finalize()`. Unused declared
   occurrences are therefore not audited, while `replay_audit.complete` defaults to true when the
   caller omits a replay audit.
6. `record_extend` does not load the prior fixture ledger and is effectively a fresh record build.
7. The manifest's intended cross-arm union is not durably accumulated across broker processes.
   `arm_recording_order` is identity material but is not enforced by the builder/runtime.
8. `abort()` looks for `publish_outcome()`, but neither store provides it, so non-success evidence
   outcomes are not durably recorded by this adapter.

The focused suite passes because it tests the pure session and publication pieces separately, not a
fresh-store replay lifecycle. Direct inspection confirms both stores currently lack `get_fixture`
and `publish_outcome`.

### 3.2 Evidence opt-in can degrade to an ordinary backtest

This is acceptable for legacy research, but fatal for any future `frozen` claim:

* an unreadable queue evidence block is converted to the all-default off contract;
* lifecycle/store/matrix setup failure returns `None` and the broker runs ordinarily;
* final evidence publication failure is swallowed and the normal result is still saved; and
* `default_replay_store()` performs first-use table creation, which is itself a write during what
  should eventually be a read-only measured replay.

A future authenticated frozen-run requirement must be an independent engine/bundle binding. If it
is present, any one of these conditions must terminate before strategy/provider/emulator work and
must never emit a frozen-looking result.

### 3.3 PIT fixture identity is not replay-bound

`ReplayFixture.pit_chain` records manifest metadata, but replay does not consume that chain or force
those exact IDs. The broker resolves “latest finalized manifest at or before this bar” from the
mutable shared registry. Publishing another eligible manifest after fixture sealing can change which
PIT bundle a nominal replay uses.

The frozen bundle must carry the exact decision-to-manifest chain, and the runtime provider must
resolve by bound manifest/source hashes from the mounted bundle—not perform a latest-as-of query
against the shared registry.

### 3.4 Market and process state remain live or incomplete

The existing PIT registry does not bind the primary adjusted price-bar input, overlay bars,
corporate actions, benchmark bytes, or the whole exchange-calendar artifact. The promoted SPY path
hash-checks a fresh fetch against a preregistered manifest, but primary backtest prices still use the
normal Rethink cache/Alpaca fetch path. Overlay-bar caches can be replaced during a run.

`backtest_bar_snapshot.py` is only a single-slot in-process rollback for critical LLM pauses. It is
not a t0 baseline. `strategy_cache_persistence._serialize_cache_for_blob()` is also unsuitable as a
frozen post-lookback state artifact: it intentionally drops PIT handles, Neo4j snapshots, overlay
bars, bulk provider state and other keys; truncates large dictionaries; silently skips unsupported
values; and is designed to fail open.

A frozen baseline needs a strict versioned runtime-state projection that either serializes every
decision-relevant key or fails on an undeclared/skipped key.

### 3.5 No global Rethink write denial

Graph Nexus has many direct `_r.db(...).table(...).insert/update/delete/table_create` call sites.
Strict PIT changes several read providers but still performs legitimate event, discovery, outcome,
ticker-history, analyst, and learning writes. The LLM cache policy does not cover these tables, and
`NEXUS_BACKTEST_SNAPSHOT_WRITE=off` covers only one final snapshot.

The safe boundary is physical:

* two disposable RethinkDB servers, both exposing logical DB name `IntelliStock` and restored from
  the same content bundle;
* production RethinkDB unreachable from arm containers;
* sealed evidence mounted read-only or served by a read-only fixture adapter;
* arm-local state writes allowed only in the disposable server; and
* pre/post write-set hashes as detection in addition to prevention.

A Python deny wrapper is useful telemetry and test support, but should not be the security boundary.
The strategy uses globals and background connections, so a partial wrapper would be easy to bypass.

### 3.6 Neo4j/live network denial is absent

The strict graph replay object is sound, but the current engine still forwards `NEO4J_URI`, user,
and password, joins the normal deployment network, mounts auth state, and forwards credential
decryption/model material. Other market/news helpers can reach the network. There is no egress
allowlist and no provider-access receipt.

Measured replay containers should receive no Neo4j or model/news provider credentials and run on an
internal network that reaches only their arm-local RethinkDB and read-only fixture service. Code-level
provider guards should fail with stable errors, while the network namespace makes a bypass impossible.

### 3.7 Runtime/source/image/environment attestations are not enforced

Today:

* `source_tree_digest()` hashes executing backend `.py` files (excluding tests), and a receipt checks
  it against `ExperimentSpec.source_tree_hash` only at finalization;
* `_dependency_runtime_digest()` hashes Python version plus installed distributions, but
  `ReplayReceipt` checks only its SHA-256 shape—`ExperimentSpec` has no expected dependency digest;
* no replay receipt binds an image ID, strategy-module hash, env allowlist, clock/locale/network
  policy, or the actual seed/Python hash seed observed by the process;
* execution-snapshot v1 declares these identities but nothing collects or verifies them; and
* the engine looks up `DOCKER_INSTANCE_IMAGE` by mutable tag and runs that tag, rather than resolving
  and launching the exact immutable image ID.

Also normalize digest formats before integration: current replay source helpers use a `sha256:`
prefix, while the execution snapshot's `source_tree_sha256` and `dependency_runtime_sha256` fields
currently require bare 64-character hex strings.

Frozen startup must verify the runtime attestation before live config, credential, provider, market,
or strategy work, and verify it again on the terminal receipt. A mismatch must be a failed arm, not a
finished ordinary result with missing evidence.

### 3.8 No target-12 negative-control gate

The current matrix can name two arms and `trade_ledger_hash()` can hash decisions/fills, but there is
no pair manifest or comparator that proves:

* both target-12 arms restored the same state/runtime bundle;
* execution snapshot bodies are identical (their queue HMACs legitimately differ because row ID and
  creation time are signed);
* PIT/model/graph/price occurrences and provider/write audits match;
* NAV, positions, fills, decisions, treatment-intent records, terminal runtime state and arm-local
  write sets match after removing only run/arm identity fields; or
* a passing negative control is bound as a prerequisite of the later target-12/target-20 pair.

Backtest IDs normally enter seed derivation. The negative control therefore needs the same explicit
snapshot-bound seed and `PYTHONHASHSEED`, not two default-derived seeds.

## 4. Smallest next implementation slice: close persisted replay

This should be one reviewable PR and should not wire execution snapshots or add a public “frozen”
flag.

### 4.1 `backend/backtest_replay.py`

Add strict read-side contracts:

* `ModelEvidenceRecord` document reconstruction helper (or an equivalent private parser) that
  recomputes semantic ID/content hash and rejects extra/missing/divergent immutable data.
* `ReplayFixture.from_doc(...)` that recomputes fixture ID, request-set hashes, PIT ordering,
  benchmark manifest and cost identity. Do not trust stored derived fields.
* `InMemoryReplayStore.get_fixture(fixture_id)` and `get_call(semantic_id)`.
* `RethinkReplayStore.get_fixture(fixture_id)` and `get_call(semantic_id)` that read persisted rows
  on a fresh adapter.
* `load_replay_fixture(fixture_id, arm_name)` returning the validated fixture plus a
  `ModelEvidenceLedger` hydrated from exactly the fixture's declared semantic IDs. Recompute the
  ledger hash and reject missing, extra-by-reference, tampered, or divergent rows before activation.
* Either implement durable `publish_outcome()` in an explicit table or remove the appearance that
  abort persistence exists. Do not silently keep the optional no-op.
* Reject `record_extend` until its cross-process merge/union semantics are implemented; do not let
  it masquerade as an extension.

Do not add DDL to the read path. Provision tables separately and explicitly.

### 4.2 `backend/backtest_evidence_runtime.py`

Only the already-opted-in evidence path changes:

* replay `begin()` must load and retain the exact fixture and hydrated ledger before activating the
  session;
* `succeed()` must call `self._session.finalize()` before constructing/publishing a receipt;
* derive `replay_audit.complete` and the ledger hash from that finalized audit—never default true;
* a replay miss, unused occurrence, tampered fixture/call, or publication failure must make the
  evidence attempt ineligible and must not publish a successful replay receipt; and
* off mode must not instantiate/read a store or change queue/result shapes.

Do **not** yet make the ordinary broker fail closed. The later authenticated frozen-run binding will
own that semantic change atomically with state/provider isolation.

### 4.3 Tests

Add end-to-end tests using a new adapter instance, not the same in-memory publisher:

1. publish matrix/build/calls/fixture, construct a fresh `RethinkReplayStore`, load, replay every
   declared occurrence, finalize, and publish a receipt;
2. provider spy is never called on replay;
3. missing/tampered call row, fixture ID/hash drift, wrong arm request set and ledger mismatch fail
   before session activation;
4. unused declared and extra requested occurrences fail at finalization;
5. `record_extend` fails explicitly until implemented;
6. abort outcome is durable and idempotent if that feature is retained; and
7. off mode touches no store and produces no new fields/logs.

Acceptance: the test must fail against current code specifically because `get_fixture`/ledger
hydration/session finalization are absent, then pass without importing the new path from an off run.

## 5. Staged order after replay closure

### Stage A — pure frozen-state and pair contracts (default unused)

Add `backend/frozen_paired_state.py` and pure tests. It should own canonical bytes and these immutable
types:

* `FrozenStateBundleManifest` — window/cutoff, logical base/history identity, exact queue-snapshot
  body hashes, per-table selector/count/hash, strict runtime-cache hash, exact PIT manifest chain,
  model fixture/ledger hash, graph query-ledger hashes, primary/overlay/benchmark market fixture
  hashes, seed/clock/calendar, and expected runtime attestation;
* `FrozenWritePolicy` — arm-local mutable tables/selectors, sealed read-only fixtures, output tables,
  forbidden stores/providers, and network-policy digest;
* `RestoreReceipt` — destination identity, every restored table hash, fixture mount hashes, and an
  aggregate bundle hash; and
* `PairedArmContract` / `NegativeControlVerdict` — actual snapshot-body diff and artifact equality
  rules.

The module must have no DB, Docker, provider, or broker imports. Merely importing it must do nothing.
Use strict positive schemas and value-free errors. Do not accept arbitrary table names/fields as a
way to make the allowlist meaningless.

For execution-snapshot bodies, the target experiment's only allowed diff should be the actual v1/v2
path:

```text
$.core.strategy.specs[0].config.anchor_reinforce_target_pct: 12 -> 20
```

The negative control allows no body diff. Compare body digests/common projections; queue HMACs are
expected to differ because they authenticate different queue identities.

### Stage B — strict offline Rethink/runtime export, restore, verify

Add storage adapters/CLI that are read-only or dry-run by default:

* export only from a quiesced dedicated research database, never the active production DB;
* canonicalize primary-key-sorted rows and emit per-table selector/count/hash plus an aggregate hash;
* fail on an undeclared table read, duplicate key, unsupported value, non-quiescent generation, or
  runtime-cache skipped field;
* export the post-lookback process state with a strict allowlist/version, not the fail-open
  `NexusStrategyCache` serializer;
* restore only with an explicit `--apply`, expected-empty disposable destination, and expected
  destination identity; and
* verify by reading the destination back and matching the source manifest byte-for-byte.

Use fake Rethink cursors in unit tests. Integration tests should use disposable local servers only.
Never restore over the live database.

### Stage C — complete immutable provider bundle

Extend the current PIT/provider contract rather than inventing parallel ad hoc caches:

* bind exact manifest IDs/source hashes per decision; remove latest-as-of registry lookup in frozen
  mode;
* include adjusted primary bars, overlay bars/results, corporate actions, benchmark observations,
  calendars and availability timestamps;
* hydrate the repaired model ledger and require zero provider calls;
* use `ReplayGraphDriver` per exact decision ledger and require complete occurrence consumption;
* emit one provider audit for model, graph, news, fundamentals, universe, market and calendar reads;
  missing/unused/undeclared occurrences fail; and
* build historical data only from records with provable availability/effective times. The current
  August snapshot cannot backfill W0-W3 by assertion.

A dynamic provider/socket deny test should monkeypatch every known provider and fail if any is
reached. This is still defense-in-depth; Stage D supplies OS/network enforcement.

### Stage D — physical isolation and write denial

Create two disposable Rethink servers from the same verified bundle. Keep logical DB/table/history
identities identical; only the physical host differs. Use an internal Docker network with no default
egress. Arm containers receive:

* only the arm-local Rethink endpoint;
* a read-only fixture mount/service;
* no production Rethink/Neo4j route;
* no model/news/market provider secrets; and
* no mutable auth mounts.

Prefer query-ledger Neo4j replay, so no Neo4j endpoint is needed. Add a code-level Rethink access
audit, but prove prevention through network/credentials and prove detection through before/after
shared-store hashes and arm-local write-set hashes.

### Stage E — full execution snapshot v2 and atomic integration

Keep v1's narrow foundation stable or introduce a v2 schema that can positively represent the full
effective document-193 strategy, every model role, broker/data access identity, launch arguments,
complete non-secret environment, and the frozen bundle/write/network policy digest.

Then integrate queue builder, engine and broker as one opt-in change:

* server-side collector builds/signs from one common snapshot;
* pair builder applies the target patch in memory; it does not edit `Strategies` and does not add
  anchor target to the general A1-A4 override API;
* engine independently requires both execution snapshot and frozen bundle digest;
* engine resolves the image tag to `image.attrs["Id"]` and launches that immutable ID;
* broker verifies snapshot, bundle restore receipt and actual runtime/source/module/dependency/image/
  env/seed/network identities before config, secrets, providers, data, emulator or strategy work;
* broker executes only the verified snapshot; and
* any partial/missing/mismatch condition exits non-zero with a safe failed-arm record.

The absent/default path must preserve the existing queue document, command, environment, network,
logs and results exactly.

### Stage F — arm-neutral baseline builder

For each eligible window, run the neutral prehistory once against sealed providers in a dedicated
builder store, stop at the registered treatment boundary, export the complete Rethink state and
strict process runtime state, then restore/verify it into both disposable arms. Disable discovery
bootstrap/merge and base discovery snapshot writes in measured arms. Do not let one window's terminal
state seed another.

If historical W0-W3 inputs cannot be reconstructed with provable PIT availability, capture forward
windows instead. Do not weaken `pit_mode` to `research` to preserve the old dates.

### Stage G — target-12 negative control, then and only then treatment pairs

The first operational run authorization after all prior acceptance gates is two target-12 arms from
the same bundle. It is not a smoke test and must not be “close enough.” See the exact gate below.
A failing control blocks every target-20 arm and opens a determinism defect; it is not silently rerun
under the same pair ID.

## 6. Target-12 negative-control acceptance contract

Pre-register one `negative-control-v1` pair with:

* identical execution snapshot **body** hash and target value `12`;
* distinct authenticated queue envelopes/run IDs, both bound to the same pair/bundle;
* same source, immutable image ID, dependency/runtime, module, public env, timezone/locale, clock,
  calendar, seed, `PYTHONHASHSEED`, provider fixture and network-policy hashes;
* same logical `base_instance_id`, `history_scope_id`, and history-scope document hash;
* two independently verified restores of the same baseline bundle; and
* randomized order only after the pair manifest is sealed.

Canonicalize outputs with an explicit versioned projection that removes only operational identity
(`backtest_id`, arm label, host/container ID and receipt creation timestamp). It must not remove
order, decision timestamps, reason text, quantities, prices, table keys, provider occurrences, or
state values.

All of these hashes must be equal:

1. ordered decision ledger;
2. ordered submitted-order and source-tagged fill ledger;
3. NAV and full position/quantity series;
4. anchor plan/block/order/fill/stage treatment-exposure ledger;
5. exact first/last process runtime-state projection;
6. arm-local Rethink write set by table and primary key;
7. PIT/model/graph/market provider occurrence audit; and
8. terminal summary/accounting/benchmark artifacts.

Additionally:

* no provider fallback or undeclared read occurred;
* shared production/evidence stores have identical before/after hashes;
* writes occurred only in declared arm-local/output namespaces;
* both arms reached the same terminal boundary and all audits completed; and
* there is no first ledger divergence because the treatment input is identical.

A single mismatch yields `NEGATIVE_CONTROL_FAILED`, identifies the first differing artifact/path,
and blocks the study. A pass receipt's hash must be included in every later target-12/target-20 pair
manifest.

## 7. Test and impact evidence from this investigation

Focused existing tests were run without launching a backtest:

```text
168 passed
```

The set covered replay contracts, evidence lifecycle/API, execution-snapshot contract, point-in-time
graph/registry, and evidence cache policy. This is component coverage, not an end-to-end replay or
frozen-state pass.

GitNexus upstream impact (plan only; no symbols edited) reported LOW indexed risk for
`ReplayFixture`, `RethinkReplayStore`, `EvidenceRunLifecycle`, and `default_replay_store`.
`ReplayFixture`/`RethinkReplayStore` have direct imports from evidence runtime and API; the lifecycle
has an API import. GitNexus does not index the oversized `broker.py` reliably, so its evidence
lifecycle calls and the engine/container boundary require manual impact review. Treat the eventual
broker/engine integration as high operational sensitivity despite the graph's LOW/zero-call result.

## Bottom line

The current repository has valuable canonicalization, immutable publication, model-call, strict PIT
and graph-replay primitives, but no usable sealed replay from a fresh process and no frozen state or
write/network boundary. Repair persisted replay first because it is the smallest default-inert slice
and because the current green component tests mask that the formal replay path cannot load its own
fixture. Then add the pure frozen-state/pair contract, offline bundle tooling, provider closure,
physical isolation, full runtime attestation and atomic execute-snapshot integration in that order.

Do not launch the target-12 negative control until all of those gates pass. Do not launch target 20
until the target-12 control produces identical canonical artifact hashes.
