# Bug sweep B — pure queue execution snapshot contract

Date: 2026-08-12
Scope: `backend/backtest_execution_snapshot.py` and `backend/tests/test_backtest_execution_snapshot_contract.py` only
Reviewed production SHA-256: `11f990d9fd45a2bd8b212d1d186211f9180e6f9d6db4d6dc6b28936f8fd7c967`
Reviewed test SHA-256: `1bc9892cc566994ef9d9eed166db56994a4ecf5abb47f2599cd94ff6d39b05c0`
Integration state: unused/default-inert
Verdict: **PASS — no blocker remains in the pure foundation**

No production code was edited by this audit.

## Verification

Native focused suite:

```text
cd backend && python3 -m pytest -q tests/test_backtest_execution_snapshot_contract.py
55 passed in 0.15s
```

Focused suite plus the imported persistence boundary:

```text
cd backend && python3 -m pytest -q \
  tests/test_backtest_execution_snapshot_contract.py \
  tests/test_persistence_safety.py
67 passed in 0.18s
```

Both reviewed files compile. A tree search found no production import/caller of the new module.
Direct adversarial probes were rerun after the last discovery-workload fix; the previously accepted
one-year/one-minute discovery and explicit-seed cases now both fail with
`schema_workload_limit`, while the target fixture still builds, signs, and verifies.

## Final findings by requested risk area

### Python types and canonical JSON — PASS

- Only exact built-in JSON types are accepted; bool is distinct, subclasses/custom objects are
  rejected, keys must be exact strings, and unsafe/non-finite numerics are rejected.
- Integral floats, integers, and signed zero share one RethinkDB-stable authenticated image; mapping
  order is canonical and array order remains significant. Golden JSON, digest, and HMAC vectors
  cover the representation.
- Unpaired surrogates fail with stable errors. Obvious overlength strings reject before a complete
  scan/UTF-8 allocation; dictionary keys are validated and capped before safe-path hashing.
- Depth, node count, collection cardinality, per-key/per-string bytes, total input text, final
  snapshot bytes, safe integers, and exact protocol type/version are bounded.

### Resource exhaustion — PASS

- The document traversal/materialization bounds now reject hostile oversized structures before
  unbounded recursive copying or canonicalization.
- The signed run also has execution-work bounds: at most ten years and five million theoretical
  user-plus-warmup symbol/bar slots.
- The final calculation includes the broker's max(700 cycles, 90 days) warmup and conservatively
  includes Graph Nexus's source-pinned default of up to 90 discovered stocks **in addition to any
  explicit seed symbols**. This closes the last B blocker: `symbol_mode="discovery"` no longer gets
  a multiplier of one, and explicit seeds no longer hide dynamic fanout.

### Tamper, recomputed hash, and downgrade — PASS

- The plain SHA covers canonical snapshot bytes; the HMAC covers normalized row identity, exact UTC
  creation text, mode, signer, digest, and complete body.
- Body mutation with the old hash, hash-only mutation, recomputing the public SHA after mutation,
  wrong row/time/key, partial envelopes, unsupported signer/protocol, and an engine digest mismatch
  all fail closed. HMAC comparisons use constant-time comparison.
- `expected_sha256` independently implies `required`, so a trusted engine binding turns all-marker
  deletion into `contract_missing`. Without external required policy, optional all-marker deletion
  remains inherently indistinguishable from a legacy row, as documented.

### HMAC key handling — PASS for this pure boundary

- Keys must be exact immutable bytes, 32 through 4096 bytes. Strings, bytearrays, missing/short, and
  oversized values fail with a value-free code; key contents are never rendered or persisted.
- Signer/domain is authenticated and verification does not accept a recomputed unkeyed digest.
- Deployment must still supply a dedicated random key distinct from credential encryption and define
  rotation/retention for the maximum queue lifetime. That operational property cannot be proven by
  this pure helper and remains an integration requirement, not a blocker in these files.

### Secret material and error hygiene — PASS

- V1 is a narrow positive schema: exactly one `Nexus Only` Graph Nexus spec, one mandatory and
  relationally consistent OpenRouter model/access binding, exact Alpaca brokerage bindings, fixed
  public adapter endpoint, and bounded content-address evidence identities.
- Secret-bearing names, exact and marker-superset redaction shapes, known token/value patterns,
  embedded Fernet text, secret URL userinfo/query/fragment, unexpected config/adapter fields, and
  sanitizer/assertion changes are rejected.
- Dynamic hostile key names never appear in errors: paths use bounded hashes. Outer queue keys,
  mode, signer, digest, and signature are exact-typed before equality/lookup, so hostile subclasses
  and `__eq__`/`__ne__` objects cannot run or leak canaries through verify/status.

### Strict execution/provenance relationships — PASS

- Explicit versus discovery symbols are unambiguous; slash/crypto symbols and non-Alpaca fee
  semantics are forbidden in equity v1.
- PIT evidence equals executable config. The mandatory model id/provider/model/model-name and
  runtime-access record agree. Candidate overrides are rejected until they can be represented in
  the effective config.
- Full evidence identifiers are captured with bounded grammars. `base`, `25bps`, and `50bps`
  provenance is relationally bound to executable `None`, `25`, and `50` costs.
- Seed, access revisions, source tree, strategy module, image digest, dependency runtime, Python
  version, and fixed environment are all included in the signed strict shape.

### Projection and verified-value leakage — PASS

- `execution_snapshot_public_status` copies no body, HMAC, access identity, or attacker-controlled
  schema value. It returns only constant protocol metadata, a syntactically valid digest, and an
  explicit `unverified_claim` label; malformed/hostile outer controls yield `None`.
- `VerifiedExecutionSnapshot` stores immutable authenticated canonical text. Every `.snapshot`
  access is a fresh materialization, so mutating one returned dictionary cannot change later
  execution material or retain a stale authorized digest over changed data.

### Backward-inert behavior — PASS

- Importing the module has no DB/provider/credential/strategy operation and the production tree does
  not import it.
- With no recognized envelope fields and no external required policy, verification returns `None`
  even when snapshot identity, creation time, and HMAC key are unavailable. This preserves an
  unconfigured/default-OFF legacy deployment.
- Any snapshot-field presence invokes the strict fail-closed path; partial/off/unsupported shapes do
  not degrade into legacy behavior.

## Re-audit history

Earlier revisions were correctly blocked for an open arbitrary core, secret-canary bypasses, public
projection body leakage, raw-key error/log leakage, mutable verified dictionaries, ambiguous HMAC
identity types, non-inert legacy key validation, post-limit allocation, missing model/PIT/evidence
relationships, non-equity symbol/fee acceptance, contradictory cost labels, and workload bounds that
omitted warmup and discovery fanout. Every reproduced case was rerun against the hashes above and is
now closed by code plus regression coverage.

## Boundary of this PASS

This PASS covers only these pure, currently unintegrated foundational files. It does **not** approve
a partial API/queue/engine/broker integration and does not authorize a causal P&L run. Integration
must verify before mutable configuration, credential, provider, data, emulator, preregistration, or
result work; execute the verified snapshot as authority; hydrate only exact access identities and
revisions; carry an independent required/engine digest against marker deletion; and preserve the
ordinary path. Full causal claims still require the separate frozen state/PIT/data/graph/model-output
isolation and replay design.
