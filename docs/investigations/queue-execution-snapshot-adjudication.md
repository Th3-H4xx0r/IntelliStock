# Queue execution snapshot: architecture/security adjudication

Date: 2026-08-12
Status: implementation decision; no backtest authorization

## Decision

The security review overrides the startup compare-current proposal where they conflict.
A startup comparison against mutable `Instances`, `Strategies`, and `Models` is drift
detection, not a freeze: it has a post-check TOCTOU window and the broker re-resolves
model rows during execution. The authoritative opt-in path must therefore **execute the
verified queue snapshot**. A current-row comparison may exist only as non-authoritative,
path-only telemetry.

The feature remains default-OFF and equities-only. An opted-in queue row must contain a
strict, canonical, secret-free public execution snapshot, its SHA-256, and a keyed
attestation made with a key distinct from brokerage/model credential encryption. The
attestation binds row identity, creation time, mode, and snapshot digest. Missing,
partial, unreadable, malformed, tampered, or mismatched requested snapshots fail closed
before configuration, credential, provider, data, emulator, preregistration, or strategy
work. Legacy rows with no marker must remain unchanged.

At runtime the broker may hydrate only secret access material using the exact frozen
model/brokerage record identities and non-secret revisions. It must not re-resolve public
model fields, scan for alternative accounts, use current instance links, or fall back to
environment/default providers. Public list/status/result projections expose only mode,
version, digest, and verdict—not the snapshot body, attestation, access IDs, or material.

## Why the compare-current plan is rejected

1. A DB edit after comparison but before or during execution changes behavior.
2. `run_run_once_strategies` currently re-resolves model references during the run.
3. startup instance classification and credential resolution currently happen before the
   later evidence/config load boundary.
4. a bare SHA can be recomputed by a writer; it is corruption detection, not origin
   authentication.
5. deleting all optional markers is a downgrade unless an independently authenticated
   scheduler/manifest requires snapshot mode for that run ID.

Accordingly, the implementation-plan report remains useful for caller inventory, blast
radius, false-path compatibility, and tests, but its compare-current execution authority,
secret-rotation allowance, redaction-marker allowance, and unsigned-envelope assumptions
are not accepted.

## Staged implementation boundary

The first safe code slice is a pure, unused contract module with strict canonical JSON,
secret-projection checks, SHA/HMAC creation and verification, partial/downgrade detection,
and golden tests. It introduces no queue/runtime behavior. Integration is allowed only
when engine and broker can enforce the full fail-closed ordering and execute-snapshot
semantics together; a half-integrated API flag must not ship.

The eventual integration must bind every immutable launch input (instance, window,
symbols, cash, granularity, fee/evidence/seed), effective ordered strategies and public
model identities, exact access identities/revisions, source/module/image/dependency
identity, and broker argv. The queue builder is server-side only and must use explicit
public projections; `sanitize_snapshot`/`assert_secret_free` are defense-in-depth, not a
schema.

## Causal-claim boundary

Even a correctly executed queue snapshot freezes only the public control plane. It does
not freeze RethinkDB/Neo4j contents, PIT/market inputs, clock, model outputs, source image,
or writes. Therefore it does **not** authorize the anchor 12%/20% P&L pair. That pair
remains blocked until the content-hashed isolated state/replay bundle and target-12
negative control in `frozen-paired-state-design.md` pass.

No new backtest should be launched from this decision.

## Foundation status (2026-08-12)

The first, behavior-inert code slice now exists in
`backend/backtest_execution_snapshot.py`, with adversarial regressions in
`backend/tests/test_backtest_execution_snapshot_contract.py`. It is not imported by the API,
queue action, engine, or broker and therefore cannot alter or label an ordinary backtest.

The pure contract uses DB-stable canonical JSON, a dedicated SHA/HMAC envelope, bounded and
value-free failures, strict marker/downgrade handling, immutable authenticated bytes, and a
positive v1 schema. That schema is deliberately much narrower than document 193: one equity
Graph Nexus spec, one mandatory OpenRouter model/access binding, exact Alpaca/no-fee settings,
explicit-versus-discovery symbol semantics, complete evidence identity/cost consistency,
access revisions, seed, and source/image/dependency hashes. Unsupported candidate overrides,
providers, strategy shapes, missing access identities, crypto semantics, contradictory model/PIT
bindings, and excessive window/granularity/symbol workloads fail closed.

This narrowness is a safety property for the foundation, not a claim that queue integration is
ready. It cannot yet represent document 193's complete effective multi-role configuration, and no
queue/API/engine/broker call site is wired. Integration remains blocked until the public schema and
collector cover every executable field, the engine supplies an independent required/digest binding,
and the broker verifies before live instance/credential/model/telemetry setup and then executes only
the verified snapshot. The separate frozen state/data/model replay bundle is still required before
any causal paired P&L run.
