# Bug sweep B — persisted replay closure

**Date:** 2026-08-12
**Base:** `06defae85b0b7c428b331134e3ac6069d8081af8`
**Audited working-tree bytes:**

- `backend/backtest_replay.py` — SHA-256 `2506c1f28c305086096ce4151b67b70a6320b34e6ad7f40aabda4f97dde97d80`
- `backend/backtest_evidence_runtime.py` — SHA-256 `60680a58453e231a4bbed319b751f1e191c887160dc13737eedc754559103717`
- `backend/tests/test_backtest_replay.py` — SHA-256 `f05dbfd3d6e98f950943c0d8c15f045ac3857d74a0ef4f1cc9980ad80d2dc2b5`
- `backend/tests/test_backtest_evidence_api.py` — SHA-256 `d1f8d4b902149df1a5b7c67a8e86d3d0e8191cedde892e86ec4c286b9b369987`

**Coordination:** I messaged sibling sweep A before changing anything and remained report-only. No repository production code, repository test, configuration, database, broker state, or backtest run was changed. This report is my only repository edit; the adversarial test module stayed in `/tmp`. The prompt supplied the GitNexus blast radius: `InMemoryReplayStore` is **MEDIUM** and the other target symbols are **LOW**; I made no symbol edit.

## Verdict

**REQUEST CHANGES / BLOCK persisted replay use.** Fresh-process call hydration and the basic fixture/call hash chain work, but the closure still has multiple false-positive and false-negative paths. Most seriously, a failed record finalization can leave a durable fixture that later replay accepts without a qualifying receipt; a replay can promote against the wrong run window/build; and the real record path cannot publish a fixture containing a model call.

A temporary out-of-tree adversarial suite, `/tmp/test_persisted_replay_adversarial_b.py`, contains 12 cases. All **12 passed**: nine cases assert the reproduced bad behaviors below, and three additional cases confirm selected fail-closed defenses.

## Blocking findings

### B-1 — **HIGH, unsafe false positive** — a receipt-validation failure happens after the fixture is sealed and durably published

**Code:** `EvidenceRunLifecycle.succeed`, `backend/backtest_evidence_runtime.py:243-288`.

The new session finalization is correctly before fixture sealing, but receipt construction is not. The record path currently does this:

1. `_session.finalize()`;
2. `_build.seal()`;
3. `store.publish_fixture(fixture)`;
4. construct and validate `ReplayReceipt`;
5. publish the receipt.

A bad trade-ledger digest, source identity, dependency digest, cost join, or another `ReplayReceipt` constructor failure therefore raises only after a replayable fixture is already in `FIXTURE_TABLE`. The reproduction passed an invalid trade-ledger digest, observed the expected exception, and then proved both `"fixture" in store.write_order` and the fixture present in `store._fixtures`.

Production makes this worse: `broker._finalize_evidence_success` catches the exception and calls `abort`, but neither replay loader requires a successful receipt and `RethinkReplayStore` has no persisted outcome writer/read gate. The orphan fixture remains sufficient for `begin()` replay. A receipt-insert failure can similarly strand a published fixture even if the receipt object itself was valid.

**Required closure:** validate the receipt object before fixture publication, and do not make a fixture replay-visible until its qualifying record receipt is durable. Because fixture-then-receipt is not atomic in the current seam, this needs either a committed publication pointer/status or a replay-load requirement that hydrates and verifies the qualifying receipt. Constructor reordering alone fixes deterministic validation failures but not receipt-write failure.

### B-2 — **HIGH, record-mode blocker** — the real LLM record path updates the ledger but never assigns the occurrence to the arm request set

**Code:** `EvidenceRunLifecycle.begin` and `record_model_row`, `backend/backtest_evidence_runtime.py:156-191,216-219`; `FixtureBuild.record_model_row`, `backend/backtest_replay.py:528-537`; actual integration at `backend/llm_utils.py:6193-6232`.

The lifecycle shares `FixtureBuild.ledger` with `ModelEvidenceSession`, so the production LLM path's `session.reserve(...)` / `session.record(...)` correctly inserts the row into the ledger. It does **not** update `FixtureBuild._arm_requests`. Production has no caller of `EvidenceRunLifecycle.record_model_row`; only the new lifecycle test calls it manually.

The adversarial test used the real session API instead of the test-only shortcut. `session.finalize()` passed, `seal()` produced a non-empty model ledger with an empty `a4` request set, and `publish_fixture()` failed with `fixture model ledger hash differs from published call rows`. The build was already sealed and could not be repaired.

The new happy-path test at `backend/tests/test_backtest_evidence_api.py:356-420` masks this regression by injecting `life.record_model_row(row)` without reserving/completing the row through the active session.

**Required closure:** make one production operation atomically publish the record and attribute its semantic ID to the current arm, or reconcile finalized session occurrences into the still-open build before sealing. Add a lifecycle test that uses `session.reserve` and `session.record`, exactly as `llm_utils` does, and never calls `life.record_model_row` directly.

### B-3 — **HIGH, provenance false positive** — replay does not join the fixture to the executing window, ordinal, or supplied build ID

**Code:** `EvidenceRunLifecycle.begin`, `backend/backtest_evidence_runtime.py:135-191`; `ReplayReceipt.__post_init__`, `backend/backtest_replay.py:576-628`.

A fixture's own window and ordinal are content-addressed, but they are never compared with `EvidenceRunLifecycle._window` and `_fixture_ordinal`. A replay-supplied `fixture_build_id` is used as the session audit build ID but is never compared with `fixture.build_id`.

The reproduction loaded a valid fixture for ordinal 0 and the declared 2026 window, then executed a lifecycle with a 1999 window, ordinal 1, and `fixture_build_id="wrong-build"`. After consuming the declared row, the receipt was **promotion eligible**. Its fixture still named the original window while the session audit named the attacker-supplied build.

**Required closure:** at replay `begin`, require exact matrix ID, arm coverage, window, fixture ordinal, cost-scenario ID/hash, and (when supplied) build ID joins before activating the global session.

### B-4 — **HIGH, persisted integrity** — `ReplayFixture.build_id` is neither in fixture identity nor re-derived on fresh read

**Code:** fixture identity at `backend/backtest_replay.py:394-402`; `ReplayFixture.from_doc` at `:422-458`.

`build_id` is serialized and required by shape, but is omitted from the fixture identity payload. `from_doc` reconstructs the attacker-provided value and never checks it against the deterministic fixture-build address (`matrix_id`, `window`, `fixture_ordinal`, `cost_scenario_id`) or the durable build declaration.

Changing only the persisted `build_id` to `"attacker-chosen-build"`—without changing or recomputing any hash—was accepted by a brand-new `RethinkReplayStore`, and `load_replay_fixture` returned the tampered value.

**Required closure:** either include `build_id` in fixture identity or treat it as a derived assertion and verify the canonical build address. Fresh replay should also hydrate/verify the corresponding immutable build declaration, not merely trust that publication once did so in another process.

### B-5 — **HIGH, audit boundary bypass** — `ReplayReceipt` and the store accept a caller-forged replay audit

**Code:** `ReplayReceipt.__post_init__`, `backend/backtest_replay.py:606-628`; `publish_receipt`, `:794-806,919-927`.

The runtime now derives its `complete` bit, which is an improvement, but the durable receipt boundary still accepts any mapping whose `complete` value is literally `True`. It does not require `ledger_content_hash`, compare that hash with the fixture, validate audit shape/mode/occurrences, or prove that a session finalized.

A directly constructed receipt with:

```python
replay_audit={"complete": True, "ledger_content_hash": "attacker-lie"}
```

was promotion eligible and was accepted by `publish_receipt`. The existing replay-store tests routinely construct `{ "complete": True }`, so they normalize this bypass.

**Required closure:** make the receipt/store boundary validate an exact derived audit contract at minimum, including the appropriate verified fixture/arm hash and occurrence set. Prefer an internal audit value produced from a finalized session rather than accepting a caller-authored truthy mapping.

### B-6 — **MEDIUM, fail-closed false negative** — exact-arm replay hashes are compared with the union fixture hash

**Code:** `load_replay_fixture`, `backend/backtest_replay.py:687-715`; `EvidenceRunLifecycle.succeed`, `backend/backtest_evidence_runtime.py:259-263`.

The loader correctly reconstructs a ledger containing exactly the selected arm's rows and separately verifies the union across every arm against `fixture.model_ledger_hash`. The lifecycle then compares the selected-arm session ledger hash to that union hash.

With one distinct baseline row and one distinct candidate row, a fresh-process candidate replay consumed every candidate occurrence and finalized successfully, yet:

```text
session_audit.ledger_content_hash != fixture.model_ledger_hash
receipt.replay_audit.complete == False
receipt.promotion_eligible == False
```

The new happy-path lifecycle test misses this because the non-selected arm has no row, making selected-arm and union ledgers accidentally identical.

**Required closure:** keep arm-local replay isolation, but carry the separately verified union hash/proof from `load_replay_fixture`; do not compare an arm-subset hash to a union hash. Add a lifecycle test with distinct rows in both arms.

### B-7 — **MEDIUM, strict cost provenance** — a wrong cost-scenario ID can promote when two scenarios bind identical specs

**Code:** lifecycle experiment selection at `backend/backtest_evidence_runtime.py:243-245`; receipt selection at `backend/backtest_replay.py:585-599`.

The lifecycle selects the experiment from its requested scenario, while the receipt independently selects from the fixture scenario. It compares fingerprints and cost hashes, not scenario IDs. A matrix may legally contain two scenario names bound to the same specs. The reproduction used fixture scenario `base` and lifecycle option `alias`; the receipt promoted, while `summary_projection()` reported `alias` and the fixture reported `base`.

A genuinely different matrix or cost fingerprint does eventually fail in `ReplayReceipt`, but only after the run. Strict preregistration requires identity joins, not merely economic equivalence.

**Required closure:** compare the requested scenario ID with the fixture scenario ID during replay preflight. Also fail wrong matrix/cost joins before executing, rather than only at receipt construction.

## Additional hardening findings

### B-8 — **LOW** — off-mode abort is not completely inert

`EvidenceRunLifecycle.abort` calls `_clear_session()` unconditionally (`backend/backtest_evidence_runtime.py:296-319`). An off lifecycle can therefore clear an unrelated active process-global evidence session. The reproduction activated a sentinel record session, called `off.abort("stopped")`, and observed `get_model_evidence_session() is None`.

The existing off test begins with no active session and checks only store writes. If off truly means inert, it must not mutate global evidence state it did not install.

### B-9 — **LOW / schema hardening** — fresh matrix hydration is permissive while fixture/call hydration is exact

`ExperimentMatrixManifest.from_doc` (`backend/backtest_replay.py:306-329`) ignores `record_kind`, ignores arbitrary extra fields, accepts a missing `id` when `matrix_id` is present, and treats derived `arm_ids` as optional. A fresh Rethink reader accepted a matrix document with `id`, `record_kind`, `arm_ids`, and `cost_scenario_hashes` removed and an attacker-controlled extra field added.

The canonical matrix ID still prevents an ignored field from changing effective experiment contents, so this is not equivalent to B-4. It nevertheless breaks the stated exact persisted-document contract and makes schema drift/tampering invisible.

### Session-ledger truthiness note

`ModelEvidenceSession.__init__` uses `self.ledger = ledger or ModelEvidenceLedger()` (`backend/model_evidence.py:585`). The concrete `ModelEvidenceLedger` currently has no false-y `__len__`/`__bool__`, so an empty concrete ledger is preserved today and empty-fixture replay worked. The expression is fragile for a false-y subclass/future `__len__`; the identity-safe form is `ledger if ledger is not None else ModelEvidenceLedger()`.

## Defenses that held

1. **Fresh Rethink call hydration:** semantic ID, canonical envelope/context, response metadata, and stored content hash are reconstructed rather than trusted. Ordinary call-outcome tamper was rejected.
2. **Recomputed call tamper:** replacing a call with a fully canonical changed outcome and recomputed call content hash was still rejected by the fixture's union ledger hash.
3. **Missing union row:** a row used only by the other arm is still loaded for union verification; deleting it fails replay even when replaying the selected arm.
4. **Fixture/call outer shape:** extra fixture fields and missing call fields failed closed. (Matrix shape is the B-9 exception.)
5. **Finalize before seal for incomplete sessions:** a pending provider reservation made session finalization fail while `FixtureBuild._sealed` remained `None` and no fixture was published.
6. **Exact occurrence accounting:** the shipped incomplete-replay test correctly rejects an unused declared occurrence before receipt publication.
7. **Wrong arm ID:** the lifecycle resolves arm name only from the hydrated matrix arm IDs; an unknown arm ID fails `begin`.
8. **Ordinary tamper/recompute roots:** content changes to hashed fixture fields require a different fixture ID; the exploitable exception found is the unhashed/unverified `build_id`.

## Test evidence

Commands (unit tests only; no backtest/configuration run):

```bash
PYTHONPATH=.:backend python3 -m pytest -q   backend/tests/test_backtest_replay.py   backend/tests/test_backtest_evidence_api.py   backend/tests/test_model_evidence.py   backend/tests/test_llm_model_evidence.py
# 93 passed, 6 warnings

python3 -m pytest -q /tmp/test_persisted_replay_adversarial_b.py
# 12 passed
```

The first attempt to collect model-evidence tests from inside `backend/` failed because those two tests import the `backend` package; rerunning from repository root with the project's expected `PYTHONPATH=.:backend` passed all 93.

## Minimum acceptance matrix

Before unblocking persisted replay, tests should prove:

- a real `session.reserve` / `session.record` record run produces the current arm request set and a durable receipt;
- distinct per-arm rows replay exactly one arm while the verified union proof remains complete;
- wrong matrix, arm, window, ordinal, build, cost-scenario ID, or cost hash fails at `begin`;
- tampered `build_id`, missing build row, and divergent build declaration fail in a fresh store;
- invalid receipt input and receipt-write failure never create a replay-visible fixture;
- no fixture is replayable without its qualifying durable record receipt;
- replay audit lies/extra/missing fields cannot construct or publish an eligible receipt;
- off begin/capture/abort/succeed cannot alter an already-active unrelated session;
- fixture, call, matrix, build, and receipt persisted shapes are exact and re-addressed on hydration.
