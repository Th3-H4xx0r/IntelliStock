# Queue-time execution snapshot: adversarial security and correctness review

Date: 2026-08-11
Status: design review only — no application code/config was changed and no provider was called

## Verdict

A default-OFF queue-time snapshot can safely freeze the backtest control plane, but only if
**the broker executes the verified snapshot as the authority**. A startup-only comparison with
current `Instances` / `Strategies` / `Models` rows is not a freeze and is subject to TOCTOU.
Conversely, executing a strategy snapshot alone is not sufficient for a causal paired backtest:
source/image/environment, database state, PIT data, graph state, model outputs, and writes still
need the isolated bundle/replay design in `frozen-paired-state-design.md`.

The minimum safe implementation has four non-negotiable properties:

1. the API builds a strict, secret-free, canonical snapshot; clients cannot submit one;
2. the queue row atomically stores the snapshot, SHA-256, and a keyed integrity attestation;
3. the broker verifies it **before any live instance classification, credential resolution,
   model resolution, data fetch, or result stub**, then executes it without re-reading mutable
   public configuration; and
4. any requested-snapshot read/validation/binding failure aborts. It must never degrade to the
   existing ordinary/default path.

A bare SHA-256 catches corruption and casual hand edits, but not a DB writer who can edit the
payload and recompute the hash. Use a distinct signing key (or asymmetric signature) if
“hand-edited row” includes an adversarial DB writer. Because the feature is default-OFF, deletion
of every snapshot marker is indistinguishable from a legacy row unless a trusted scheduler or
experiment registry independently says that this backtest ID requires a snapshot. This downgrade
limit must be explicit; it cannot be solved by another unsigned boolean in the same row.

## Current-path findings

### 1. Snapshot verification would currently happen too late — blocker

`broker.py` parses `backtest_row_id` around lines 710–731, but immediately afterward it reads the
current `Instances` row to classify kind/crypto configuration (`_instance_kind_and_crypto_config`,
~742), and at module initialization it resolves the current trading and data brokerage links and
decrypts their material (~819–907). The existing evidence row is not loaded until
`_load_backtest_evidence_options` is invoked around line 9399. Strategies are then loaded from
current rows at line 9419.

For snapshot execution the verified queue row must be bootstrapped immediately after argument
parsing, before `_is_non_equity_instance_runtime()` is first called. Otherwise an instance link or
`kind` edit between enqueue and startup chooses the wrong credential path before the snapshot is
even examined.

The engine has the same issue. `engines/backtest_engine.py::run_one_backtest` classifies the
current instance and, for a current non-equity classification, can copy queue-row/environment
credentials into Docker argv/environment before the broker validates anything. Safe v1 should be
equity-only, or the engine must validate the signed snapshot and use its frozen kind/bindings
before constructing argv/env. Never let a current `Instances.kind` value decide whether secret
fields cross the container boundary for a snapshotted run.

### 2. Current strategy/model behavior remains mutable during the run — blocker

`broker.py::load_strategies_from_db` (~5658) reads the current instance, its current `strategy_id`,
and the current `Strategies` row. Missing tables/rows and broad exceptions collapse to empty specs.
At startup the broker calls `resolve_model_refs_in_config(..., force_refresh=True)` (~9439–9452).
More importantly, `run_run_once_strategies` re-resolves every `*_llm_model_id` on every invocation
(~6268–6315). An edit after a startup compare can therefore change provider, model, base URL,
reasoning settings, CLI arguments, or credential material mid-run.

`model_resolver.py` is fail-open for configuration identity: `_get_model_from_cache_or_db` turns a
DB exception into `None`, and `resolve_model_refs_in_config` silently skips a deleted/missing model,
allowing environment/default fallback. This is acceptable legacy behavior only when snapshot mode
is off. In snapshot mode a missing or malformed referenced model is fatal at enqueue, and runtime
must hydrate only access material into the frozen public model configuration.

There is a second mutable `Models` read in `broker.py::_init_llm_telemetry` (~1948–1988): pricing
overrides are fetched per call, with a provider/model fallback that can select among duplicate rows.
Pricing can affect cost receipts and potentially budget logic. Snapshot mode must use the frozen
four pricing fields and disable this current-row fallback.

### 3. Brokerage identity is also TOCTOU — blocker

The equity credential helpers correctly use exact links and `decrypt_required`, but they discover
the link from the *current* `Instances` row. `_resolve_data_brokerage_creds_now` (~1686) repeats that
lookup when a discovered symbol needs bars. Thus an enqueue-time instance snapshot does not freeze
which account/feed is used unless all of these paths accept the frozen effective brokerage row ID.

Resolve the effective data row at enqueue (explicit data row, otherwise explicit trading-row
fallback), record that row ID, paper/live mode, feed, and a non-secret access revision. At startup,
fetch exactly that row, verify identity/type/mode/revision, decrypt strictly once, and do not scan,
relink, or fall back. A missing row, plaintext legacy material, wrong type, changed revision, or
failed decrypt aborts without trying environment/global credentials.

For the initial implementation, reject `kind in {crypto, kalshi}` when snapshot mode is requested.
Those legacy paths still persist/copy direct credentials and use broad fallback behavior. Extending
snapshot mode to them requires a separate credential-boundary design.

### 4. `sanitize_snapshot` / `assert_secret_free` are defense-in-depth, not a schema — blocker

The persistence guard is valuable, including camel-case key splitting, deep traversal, value
patterns, and path-only errors. It is not proof that an arbitrary document is secret-free. Concrete
current bypasses, reproduced locally without any network call, all survive both
`sanitize_snapshot` and `assert_secret_free`:

```python
{"strategy_config_hash": "CANARY_RAW_SECRET_MATERIAL"}
{"secret_ref": "env:CANARY_RAW_SECRET_MATERIAL"}
{"apikeyvalue": "CANARY_RAW_SECRET_MATERIAL"}
{"note": "fernet:gAAAAABTHIS_IS_CIPHERTEXT"}
{"endpoint": "https://example.invalid/?access_code=CANARY_RAW_SECRET_MATERIAL"}
```

The first two exploit broad allowlists; the others fall outside the key/value regexes. Also,
`sanitize_snapshot` preserves tuples and deep-copies arbitrary objects, and it does not reject
NaN/infinity. Those values are unsuitable for a cross-process canonical JSON contract.

Do not snapshot whole rows and hope the sanitizer finds every bad field. Build the snapshot from
explicit public-field allowlists. Then require all of the following:

```python
sanitize_snapshot(snapshot) == snapshot     # any redaction is a build failure
assert_secret_free(snapshot)                 # defense in depth
strict_json_validate(snapshot)               # strings keys; dict/list/scalars only; finite numbers
```

An executable snapshot must contain **no redaction markers**. Redaction markers are appropriate in
a display/audit copy, but executing a config in which a string became a marker dict changes
semantics. Validate `strategy_config_hash` as an actual digest (for example 64 lowercase hex), and
restrict any allowed environment reference to a known name allowlist; do not trust the present
generic `secret_ref` regex as the snapshot schema.

### 5. Encryption does not provide queue integrity — high

`secret_store.py` provides Fernet confidentiality/authentication for each stored secret and
`decrypt_required` correctly rejects plaintext. It does not authenticate a queue row. Do not:

* put plaintext, Fernet ciphertext, a ciphertext hash, last-four/masked material, or a hash of
  plaintext in the snapshot;
* use `INTELLISTOCK_CRED_KEY` as the queue HMAC key (key reuse couples compromise domains); or
* decrypt model/broker material while building the queue snapshot.

At enqueue it is enough to verify that required stored fields are non-empty Fernet values and to
record an opaque row ID plus a non-secret revision. Add an `access_revision` integer that increments
only when access material changes. As a conservative v1 fallback, current `updated_at` may be used,
but it will abort on unrelated edits and must be treated as an opaque equality token, not parsed as
a security timestamp.

### 6. Queue inputs and engine launch must be bound too — high

A snapshot hash covering only `Strategies.strategies` still permits a hand edit to symbols, window,
granularity, cash, fee model, evidence overrides, seed, instance ID, or snapshot mode. Put every
immutable launch input in the canonical core and compare argv/env to it in the broker. Keep mutable
operational fields (`status`, `paused`, `run`) outside the core.

The engine consumes a possibly stale row retained from its initial sweep/changefeed. It should
refresh and verify the exact row immediately before launch. The broker must independently compare
its argv to the signed queue core, so a delete/edit between engine refresh and process startup is
still fatal.

Current `backtest_engine.py` also resolves a mutable Docker tag rather than an immutable image ID.
The smallest control-plane feature can record/compare the executing source-tree hash, but it does
not freeze the image. A formal pair additionally needs an immutable image digest and dependency
runtime digest.

### 7. Missing-row fallback is unsafe for requested mode — high

`broker.py::_load_backtest_evidence_options` explicitly treats an unreadable/missing row as all-
default evidence `off`. Do not copy that behavior. For a row that requests snapshot execution,
missing/unreadable row, missing payload, invalid hash/signature, or unsupported version must exit
nonzero before strategy/model/data execution. The engine should mark a safe terminal error, but
must not manufacture a P&L row from current configuration.

Likewise, `load_strategies_from_db` returning empty on broad exceptions is not a valid snapshot
loader. Distinguish `legacy_off` from `requested_invalid`; never infer off from a failed read.

### 8. API exposure must remain projection-only — high

`CreateBacktestBody` currently accepts launch/evidence fields and `api_create_backtest` passes them
to the server-side action. Add only an enum/strict flag such as
`execution_snapshot_mode: Literal["off", "execute"] = "off"`. Never accept
`execution_snapshot`, its hash, model bindings, or access bindings from the client. Configure the
request model to forbid extra fields rather than silently ignore a client attempt to inject one.

`action_list_backtests` already uses a `pluck` whitelist, and status/summary endpoints return
selected fields. Preserve that shape. Public/authenticated responses may expose mode, version,
SHA-256, and a validation verdict, but never the snapshot body, HMAC/signature, access row IDs,
Fernet material, or raw model documents. `BacktestResults` should copy the queue-time SHA-256 and
safe effective `strategy_schema`; it should not copy the full access-binding block.

## Recommended canonical contract

### Row shape

Only opted-in rows get these fields, preserving byte-for-byte/default behavior for ordinary rows:

```json
{
  "execution_snapshot_mode": "execute",
  "execution_snapshot": {"schema_version": 1, "core": {}},
  "execution_snapshot_sha256": "64-lowercase-hex",
  "execution_snapshot_hmac_sha256": "64-lowercase-hex",
  "execution_snapshot_signer": "queue-snapshot-hmac-v1"
}
```

`execution_snapshot_sha256` hashes only canonical `execution_snapshot`; that makes the effective
snapshot comparable across paired row IDs. The HMAC covers a separate canonical envelope containing
`backtest_id`, normalized `created_at`, mode, snapshot hash, and the snapshot body. Preallocate the
backtest ID, build/hash/sign, and insert the complete queue row in one DB insert; never insert a
draft and patch the snapshot later.

A separate HMAC key is the smallest authentication improvement. Ed25519 is stronger operationally:
the queue creator holds the private key and broker containers receive only the public key. A plain
SHA-256 is acceptable only if the stated threat is accidental/casual editing, not an attacker with
DB write access.

### Canonical `execution_snapshot`

The following is the recommended v1 shape. It intentionally contains only explicit public
projections and opaque access identities. Nulls are explicit where default-vs-empty changes runtime
semantics.

```json
{
  "schema_version": 1,
  "core": {
    "run": {
      "instance_id": "v2-let-run-core",
      "symbols": ["SPY"],
      "start_date": "2026-03-30",
      "end_date": "2026-04-27",
      "granularity_sec": 3600,
      "initial_cash": 6000.0,
      "fee": {
        "emulated": false,
        "requested_venue": "default",
        "resolved_venue": "alpaca",
        "taker_rate": 0.0
      },
      "seed": {
        "algorithm": "intellistock-backtest-v1",
        "value": 424242,
        "python_hash_seed": "0"
      },
      "evidence": {
        "evidence_mode": "off",
        "pit_mode": "research",
        "equity_total_cost_bps": null,
        "nexus_candidate_overrides": {}
      }
    },
    "instance": {
      "record_id": "v2-let-run-core",
      "kind": "equity",
      "strategy_record_id": 193
    },
    "strategy": {
      "record_id": 193,
      "name": "Nexus Only",
      "experiment_spec": null,
      "specs": [
        {
          "ordinal": 0,
          "strategy": "graph_nexus_analysis",
          "weight": 1.0,
          "execution_position": 0,
          "decision_phase": "pre",
          "execution_scope": "run_once",
          "conditions": {},
          "config": {
            "llm_model_id": "model-7",
            "llm_provider": "openrouter",
            "llm_model": "vendor/model",
            "model_name": "vendor/model",
            "anchor_reinforce_execution_enabled": true,
            "anchor_reinforce_target_pct": 12
          }
        }
      ]
    },
    "models": [
      {
        "spec_ordinal": 0,
        "role_prefix": "",
        "record_id": "model-7",
        "provider": "openrouter",
        "model": "vendor/model",
        "adapter": {
          "openai_base_url": null,
          "nvidia_base_url": null,
          "azure_openai_endpoint": null,
          "azure_openai_api_version": null,
          "reasoning_effort": null,
          "cli_path": null,
          "extra_args": null,
          "ollama_base_url": null,
          "ollama_keep_alive": null,
          "ollama_think": null,
          "bedrock_region": null,
          "bedrock_reasoning": null,
          "openrouter_base_url": "https://openrouter.ai/api/v1",
          "openrouter_referer": null,
          "openrouter_title": null,
          "model_cache_family": null,
          "input_cost_per_1m": null,
          "output_cost_per_1m": null,
          "cache_creation_cost_per_1m": null,
          "cache_read_cost_per_1m": null
        },
        "runtime_access": {
          "kind": "models_row",
          "record_id": "model-7",
          "access_revision": 4,
          "required": true
        }
      }
    ],
    "broker_access": {
      "trading": {
        "kind": "brokerage_row",
        "record_id": "brokerage-trading-id",
        "access_revision": 8,
        "brokerage_type": "alpaca",
        "paper": true,
        "data_feed": "iex"
      },
      "market_data": {
        "kind": "brokerage_row",
        "record_id": "brokerage-data-id",
        "access_revision": 3,
        "brokerage_type": "alpaca",
        "paper": false,
        "data_feed": "sip"
      }
    },
    "runtime": {
      "source_tree_sha256": "64-lowercase-hex",
      "python_version": "3.x.y",
      "strategy_modules": [
        {"strategy": "graph_nexus_analysis", "module_sha256": "64-lowercase-hex"}
      ],
      "environment": {
        "timezone": "UTC",
        "nexus_backtest_snapshot_write": "off"
      }
    }
  }
}
```

Notes:

* `specs` is the final effective ordered list: merge legacy `conditions` exactly as runtime does,
  apply validated evidence overrides and `pit_mode` before hashing, resolve public model fields,
  and preserve a stable original ordinal for equal execution positions. The broker must not apply
  those transformations a second time.
* Include every public field currently propagated by `model_resolver.field_map`, CLI-specific
  fields, and the four pricing overrides. Missing/empty/null semantics must match the legacy
  resolver exactly.
* `runtime_access`, `broker_access`, `record_id`, and `access_revision` avoid secret-bearing key
  names and survive the current persistence guard. Test that the sanitizer is an identity over the
  final payload.
* Opaque `Models` / `BrokerageAccounts` row IDs are identities, not secrets. Exclude account names,
  account numbers, masks/last-four values, stored ciphertext, ciphertext hashes, and material-
  derived fingerprints.
* Environment/CLI/IAM/OAuth access needs an explicit identity variant (for example an allowlisted
  environment variable name or profile name), never the value. Formal replay arms should make no
  provider call, so these bindings are audit metadata only in that protocol.
* If there is no separate data link, store the trading row ID again as the *effective* market-data
  binding. Do not encode “fallback to whatever is current.”
* Exact image/dependency digests should be added before claiming full causal execution freezing.
  The v1 source hash only prevents silent source-tree drift when the broker verifies it.

### Canonical bytes

Use one implementation in a small pure module, shared by API, engine, and broker:

```python
json.dumps(
    strict_json_value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
    allow_nan=False,
).encode("utf-8")
```

Before dumping: require string dictionary keys; dict/list/string/null/exact bool/int/finite float
only; reject bytes, tuples, sets, datetimes, Decimals, custom objects, NaN, and infinity. Normalize
fields at their schema boundary (dates, IDs, symbols, enums, numeric ranges), but do not apply a
blanket Unicode/value coercion that could change strategy semantics. Put a golden canonical byte
string and hash in tests. If cross-language producers are later allowed, adopt RFC 8785/JCS rather
than assuming Python float formatting is a universal contract.

## Safe collection and execution sequence

### Enqueue (mode defaults to `off`)

1. Strictly validate launch inputs. Require a valid date order, positive finite cash, supported
   positive granularity, normalized expanded symbols, and an explicit seed for snapshot mode.
2. Resolve the exact instance row and require equity v1, exact strategy row, nonempty list specs,
   known strategy modules, every top-level `*_llm_model_id`, exact model rows, and exact effective
   brokerage/data rows. No table scans or provider/environment fallback.
3. Build only public projections. At enqueue, check required material with `is_encrypted`; do not
   decrypt it.
4. Resolve public model settings into deep-copied specs, validate provider/model combinations,
   URLs (no userinfo/query/fragment carrying material), finite nonnegative pricing, CLI path/args,
   types, lengths, and role-prefix syntax. Missing model references are fatal rather than skipped.
5. Collect the projection twice and require identical canonical bytes (or bounded retry) to detect
   concurrent instance/strategy/model relinking during the multi-row read. RethinkDB does not give
   this sequence a cross-table transactional snapshot.
6. Require `sanitize_snapshot(snapshot) == snapshot`, then `assert_secret_free`, strict-serialize,
   size-limit (for example 512 KiB), hash, and attest.
7. Preallocate/retry the integer backtest ID and atomically insert the complete row. No draft state.
8. Return only ID, mode, version, and SHA-256.

### Engine and broker

1. Engine refreshes the row immediately before launch, verifies it, pins/attests the image, and
   passes no snapshot body or access material through argv. Pass only the row ID and ordinary
   normalized non-secret args.
2. Broker loads the row once immediately after argv parsing. Requested mode makes every load or
   validation error fatal. Verify signature with constant-time comparison, SHA-256, exact schema,
   secret-free invariant, source digest, and argv/core equality.
3. Populate instance kind/config from the snapshot before any current `Instances` read.
4. Hydrate access once: exact row ID + expected access revision + exact declared type/mode/feed,
   then `decrypt_required`. Inject only the plaintext access material into an in-memory deep copy.
   Do not persist/log/`repr` that copy.
5. Execute frozen specs. Snapshot mode must disable `load_strategies_from_db`, general
   `resolve_model_refs_in_config`, per-cycle model refresh, current-row telemetry pricing lookup,
   current-link data credential refresh, and any model/provider/environment fallback.
6. Current-row comparison may run as **path-only drift telemetry** after authority has been chosen;
   it cannot supply values to execution. Current config deletion is not an execution failure once
   the public snapshot is self-contained. Required access-row deletion/revision mismatch is fatal.
7. Copy the same queue SHA-256/version/mode to running stub, terminal result, evidence receipt, and
   logs. Results should never substitute a newly generated strategy snapshot.

## Failure-mode contract

| condition | required behavior when mode=`execute` |
|---|---|
| queue row missing/unreadable | abort before config/access/provider/data work; safe terminal error |
| mode says execute but body/hash/attestation absent | abort |
| snapshot fields present while mode is off/absent | abort as suspicious for new code; never half-use them |
| unsupported version, unknown/extra structural field, over-size | abort |
| canonicalization error, NaN/non-JSON type, sanitizer changes payload, redaction marker present | abort with code/path only |
| SHA mismatch | abort |
| HMAC/signature missing or invalid | abort; a recomputed bare SHA is not enough |
| argv instance/window/symbol/cash/granularity/fee/evidence/seed differs | abort |
| executing source/module/image attestation differs | abort (or permanently mark non-evidence for explicitly scoped legacy v1; never silently claim frozen) |
| instance/strategy config rows edited/deleted after enqueue | execute the verified public snapshot; optional drift telemetry only |
| required model/broker access row missing, wrong ID/type/mode/feed, plaintext, revision changed, decrypt failure | abort; no environment/global/other-row fallback |
| model public fields edited after enqueue | ignore for execution; never call the general resolver |
| access rotates after startup | continue with the once-hydrated binding if still usable; do not switch identity mid-run; provider failure remains terminal under formal replay rules |
| engine held stale row or duplicate launcher races | broker verification/argv comparison aborts mismatch; add atomic pending→starting claim for duplicate safety |
| legacy row truly has no snapshot fields and trusted policy says optional | preserve current behavior exactly |

A malicious DB writer who deletes all optional markers can force the last row unless a trusted
outside policy says the ID requires a snapshot. For formal paired/evidence runs, make snapshot
required in an independently authenticated experiment manifest/scheduler job. Do not claim the
optional row format alone prevents this downgrade.

## Targeted tests

### Pure snapshot/security tests (`test_execution_snapshot_security.py`)

* default `off` performs no new snapshot/model/broker projection and produces the old queue shape;
* client-provided body/hash/bindings are rejected as extra fields;
* a golden payload has exact canonical bytes and SHA; insertion-order changes hash identically;
* list order is preserved, equal execution positions use stable ordinals, and treatment-only diff
  reports exactly the registered config path;
* reject non-string keys, tuple/set/bytes/datetime/custom objects, NaN/infinity, oversized payload,
  invalid dates/ranges/enums/types, and invalid digest fields;
* canaries placed in every secret field, nested innocuous fields, `strategy_config_hash`,
  `secret_ref`, fused names such as `apikeyvalue`, Fernet text, URLs/query strings, model names,
  headers, CLI args, and errors never appear in snapshot JSON, DB document, exception, or log;
* require `sanitize_snapshot(payload) == payload`; reject any redaction marker;
* snapshot identities contain only opaque row IDs/revisions/public adapter fields — no plaintext,
  ciphertext, ciphertext hash, mask, account number, prefix/suffix, or material-derived digest;
* missing/malformed instance/strategy/model/broker row, plaintext stored material, unsupported
  non-equity kind, provider/model contradiction, duplicate/conflicting role, invalid endpoint, or
  pricing value fails before insert;
* mutate each source document during the double-collect and assert bounded retry/abort rather than
  a mixed snapshot; ensure returned snapshot does not alias/mutate source dicts;
* mutate payload alone, hash alone, immutable outer input, mode, and recomputed SHA; assert HMAC
  verification rejects every unauthorized edit and errors contain no values;
* delete every optional marker and document/test the external-required-policy behavior.

### Broker/engine tests (`test_broker_execution_snapshot_startup.py`)

* call-order spy proves verification occurs before `_instance_kind_and_crypto_config`, brokerage
  decrypt, strategy/model load, telemetry initialization, data fetch, provider dispatch, and result
  stub;
* requested missing/unreadable/malformed row exits nonzero — it never takes the evidence loader's
  current `off` fallback and never loads current strategies;
* argv/core mismatch for every immutable launch field aborts;
* edit/relink/delete `Instances` and `Strategies` after enqueue: frozen specs and effective account
  IDs remain unchanged; current-row canaries are never observed;
* edit provider/model/base URL/reasoning/pricing after enqueue and during bar N: public config stays
  frozen, `run_run_once_strategies` performs no general Models lookup, and telemetry uses snapshot
  pricing;
* delete or rotate required access row: exact failure, no environment/default/other-row fallback;
* discovered-symbol history uses the frozen effective data binding, not
  `_resolve_data_brokerage_creds_now` current linkage;
* stale engine changefeed row versus refreshed DB row is rejected; atomic launch claim prevents two
  containers from executing the same signed row;
* Docker command/environment contain no model/Alpaca material or snapshot body; only the existing
  process-wide decryption/signature verification capabilities where required;
* running and finished `BacktestResults` carry the identical queue SHA/version; result write passes
  `assert_secret_free`; raw binding body/signature is absent;
* failure messages are stable codes/path-only and contain none of the source/access canaries.

### API/projection regression tests

* POST omitted/off is backward compatible; execute is strict and server-built;
* list/status/summary responses expose only safe mode/version/hash/verdict fields and never body,
  HMAC, access row IDs, raw/masked documents, or ciphertext fragments;
* existing `test_persistence_safety.py`, `test_alpaca_secret_boundaries.py`,
  `test_model_secret_resolution.py`, and backtest-evidence tests remain green;
* a pair-comparison helper removes only attestation/provenance metadata, resolves evidence manifest
  identities to their effective content, and proves all effective bytes except the preregistered
  treatment path are equal.

## Compare-current versus execute-snapshot

| approach | value | insufficiency |
|---|---|---|
| compare current rows at startup, then execute current | detects enqueue-to-start drift and can abort | not a freeze; race immediately after compare; per-cycle model refresh defeats it; deleted rows cannot execute; legitimate edits kill queued work |
| execute verified queue snapshot | actually freezes captured public control-plane semantics and tolerates later config edits/deletion | must close every live config/access fallback; does not itself freeze code/image/env/state/data/graph/model outputs |
| execute snapshot + compare current | good audit telemetry if comparison never supplies values | still no substitute for signature, source attestation, access binding, state isolation, or replay |

Therefore: **execute-snapshot is necessary for this feature; compare-current is optional telemetry,
not the authority. Neither is sufficient for the complete frozen paired-state claim.** A valid
causal pair also needs the identical isolated state/PIT/graph/model-replay bundle, fixed clock/seed,
write denial, immutable source/image/dependencies, and negative-control determinism described in
`docs/investigations/frozen-paired-state-design.md`.

For pair comparison, compare the effective `core`, not row ID/created-at/HMAC. Resolve any different
matrix/arm identifiers to their effective manifests before comparison. After removing the one
preregistered treatment path, every remaining effective byte — including access row IDs/revisions,
seed, data feed, public model adapter/pricing, source, and environment projection — must match.
