# Strict Point-in-Time Provenance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make historical Graph Nexus runs resolve immutable, hash-verified datasets automatically, reject unproven legacy evidence, and capture reproducible datasets from future live/paper decisions.

**Architecture:** Add an append-only RethinkDB registry for content-addressed dataset snapshots and finalized manifests. Historical broker calls resolve the latest eligible manifest at the decision timestamp and consume graph/news/fundamental/universe data through strict adapters; live/paper Graph Nexus runs record the inputs they actually used and atomically finalize a forward manifest. Persist manifest provenance on trade contexts and require verified provenance at the alpha-promotion boundary.

**Tech Stack:** Python 3.14, pytest, dataclasses, SHA-256 canonical JSON, RethinkDB, Neo4j Python driver adapters, exchange-calendars.

## Global Constraints

- `alpaca-main` remains stopped; implementation and deployment must not call any instance start endpoint.
- Existing historical contexts without a finalized manifest remain `legacy_unverified` and cannot become promotion evidence.
- No current-state fallback is allowed in strict historical mode.
- Legacy compatibility, if used for research, must be explicit and must report `promotion_eligible=False`.
- Snapshot rows are content-addressed and immutable; manifests are published only after all required datasets exist and validate.
- Secrets and credentials must never enter snapshot payloads, manifests, logs, fixtures, or errors.
- The minimum strict Graph Nexus dataset set is `graph`, `fundamentals`, `universe`, and `news`.
- Existing user changes in the primary checkout remain untouched.
- Every production-symbol edit requires upstream GitNexus impact analysis.
- Every implementation task follows red-green-refactor TDD.
- Before each commit, run `gitnexus_detect_changes({scope:"staged"})`.

---

### Task 1: Immutable Snapshot Registry and Graph Record/Replay

**Files:**
- Create: `backend/point_in_time_registry.py`
- Create: `backend/point_in_time_graph.py`
- Create: `backend/tests/test_point_in_time_registry.py`
- Create: `backend/tests/test_point_in_time_graph.py`

**Interfaces:**
- Produces `canonical_payload(value: Any) -> Any` and `content_hash(value: Any) -> str`.
- Produces `StoredManifest(manifest_id, as_of, created_at, source_hashes, code_revision, status, provenance)`.
- Produces `InMemoryPointInTimeRegistry` and `RethinkPointInTimeRegistry`.
- Produces `finalize_bundle(*, as_of, datasets, code_revision) -> StoredManifest`.
- Produces `resolve_bundle(as_of) -> ResolvedPointInTimeBundle`.
- Produces `RecordingGraphDriver` and `ReplayGraphDriver`.

- [ ] **Step 1: Write failing immutable-registry tests**

```python
def test_manifest_is_content_addressed_and_idempotent():
    registry = InMemoryPointInTimeRegistry()
    first = registry.finalize_bundle(
        as_of=ts("2026-07-28T20:00:00Z"),
        datasets=complete_datasets(),
        code_revision="abc123",
    )
    second = registry.finalize_bundle(
        as_of=ts("2026-07-28T20:00:00Z"),
        datasets=complete_datasets(),
        code_revision="abc123",
    )
    assert first == second
    assert first.status == "finalized"


def test_finalize_rejects_missing_required_dataset():
    datasets = complete_datasets()
    datasets.pop("news")
    with pytest.raises(PointInTimeDataError, match="missing.*news"):
        InMemoryPointInTimeRegistry().finalize_bundle(
            as_of=ts("2026-07-28T20:00:00Z"),
            datasets=datasets,
            code_revision="abc123",
        )
```

- [ ] **Step 2: Run registry tests and verify RED**

Run:

```bash
python3 -m pytest -q \
  backend/tests/test_point_in_time_registry.py \
  backend/tests/test_point_in_time_graph.py
```

Expected: import failures because the modules do not exist.

- [ ] **Step 3: Implement canonical hashing and atomic finalization**

`canonical_payload` must normalize aware datetimes to UTC `Z`, sort mapping keys,
sort sets by canonical JSON, preserve list order, and reject opaque objects.
`finalize_bundle` writes content-addressed snapshot rows first and writes the
finalized manifest last. An existing hash with different canonical content
raises `PointInTimeDataError`.

- [ ] **Step 4: Implement strict graph record/replay**

`RecordingGraphDriver.session().run(query, parameters, **kwargs)` consumes the
read result into JSON-safe records, records an ordered occurrence under a hash
of normalized query plus canonical parameters, and returns an in-memory result
supporting iteration, `single()`, `data()`, and `consume()`.
`ReplayGraphDriver` returns those occurrences in order and raises
`PointInTimeDataError("graph snapshot has no recorded query")` on any miss.
Write-like Cypher (`CREATE`, `MERGE`, `DELETE`, `SET`, `DROP`) is rejected.

- [ ] **Step 5: Verify GREEN**

Run both Task 1 test files and confirm all tests pass.

- [ ] **Step 6: Detect changes and commit**

Stage only Task 1 files, run GitNexus staged change detection, then commit:

```bash
git commit -m "feat(pit): add immutable snapshot registry"
```

---

### Task 2: Automatic Historical Manifest Resolution

**Files:**
- Modify: `backend/point_in_time_data.py`
- Modify: `backend/broker.py`
- Modify: `backend/tests/test_point_in_time_data.py`
- Modify: `backend/tests/test_broker_graph_nexus_pit.py`

**Interfaces:**
- `ImmutableSnapshotStore.coerce` accepts immutable mapping stores and registry-backed stores.
- `PointInTimeContext` exposes `provenance`.
- Produces `resolve_default_bundle(as_of) -> ResolvedPointInTimeBundle`.
- `_run_graph_nexus_with_point_in_time` resolves a finalized manifest when strategy config does not embed one.

- [ ] **Step 1: Run GitNexus impact analysis**

Analyze `ImmutableSnapshotStore.coerce`, `PointInTimeContext`, and
`_run_graph_nexus_with_point_in_time` upstream. Report direct callers, affected
processes, modules, and risk before editing.

- [ ] **Step 2: Write failing resolver tests**

```python
def test_broker_resolves_registered_bundle_without_serialized_config(monkeypatch):
    monkeypatch.setattr(
        point_in_time_registry,
        "resolve_default_bundle",
        lambda as_of: resolved_bundle(as_of),
    )
    result = run_graph_backtest(config={})
    assert result.context.manifest.manifest_id == "pit-manifest"
    assert result.context.provenance == "strict_verified"


def test_resolver_rejects_manifest_newer_than_decision():
    registry = registry_with_manifest("2026-07-29T20:00:00Z")
    with pytest.raises(PointInTimeDataError, match="no finalized"):
        registry.resolve_bundle(ts("2026-07-28T20:00:00Z"))
```

- [ ] **Step 3: Run focused tests and verify RED**

Run the two modified test files. Expected: missing resolver/provenance behavior.

- [ ] **Step 4: Implement automatic resolution**

Preserve explicitly supplied test bundles. Otherwise resolve the latest
finalized registry manifest with `manifest.as_of <= decision.as_of`, construct a
strict context, use `resolve_nyse_session_close`, and never mutate the stored
strategy configuration.

- [ ] **Step 5: Verify GREEN and commit**

Run the existing PIT suite plus the new resolver tests, run staged GitNexus
change detection, and commit:

```bash
git commit -m "fix(pit): resolve historical manifests at runtime"
```

---

### Task 3: Bind Graph Nexus Inputs and Capture Future Manifests

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py`
- Modify: `backend/ticker_universe.py`
- Modify: `backend/tests/test_nexus_graph_snapshot_pit.py`
- Modify: `backend/tests/test_nexus_news_pit.py`
- Modify: `backend/tests/test_ticker_universe_pit.py`
- Create: `backend/tests/test_nexus_pit_capture.py`

**Interfaces:**
- `_fetch_articles_cached(..., context, snapshot_store)` reads only `news.alpaca` in strict mode.
- `_fetch_google_news_cached(..., context, snapshot_store)` reads only `news.google` in strict mode.
- Historical Benzinga data reads only `news.benzinga`.
- Live/paper full cycles optionally wrap Neo4j in `RecordingGraphDriver`.
- Produces `_capture_point_in_time_bundle(...) -> StoredManifest | None`.

- [ ] **Step 1: Run GitNexus impact analysis**

Analyze `GraphNexusAnalysis.run_historical`, `GraphNexusAnalysis.run_once`,
`_fetch_articles_cached`, `_fetch_google_news_cached`,
`get_breadth_universe`, and `_save_trade_contexts_and_outcomes`.
Warn before proceeding if any result is HIGH or CRITICAL.

- [ ] **Step 2: Write failing source-isolation tests**

```python
def test_strict_news_uses_snapshot_without_calling_current_fetch(monkeypatch):
    monkeypatch.setattr(graph, "_fetch_alpaca_news_all", forbidden)
    articles, _, _ = graph._fetch_articles_cached(
        DATE, START, END, "", "",
        context=strict_context(),
        snapshot_store=complete_store(),
    )
    assert [article["id"] for article in articles] == ["known-at-cutoff"]


def test_graph_replay_miss_fails_before_strategy_can_query_live():
    with pytest.raises(PointInTimeDataError, match="no recorded query"):
        run_historical_with_graph_ledger({})
```

- [ ] **Step 3: Run Task 3 tests and verify RED**

Run the four Task 3 test files. Expected: snapshot-store arguments/capture
behavior are absent.

- [ ] **Step 4: Bind strict sources**

Preload `graph`, `fundamentals`, `universe`, and `news` before the strategy
pipeline. In strict mode, Alpaca/Google/Benzinga source functions read only the
snapshot payload; current APIs and mutable cache tables are not consulted.
Universe membership and breadth continue through their existing snapshot-aware
paths. The graph payload is decoded as `ReplayGraphDriver`.

- [ ] **Step 5: Capture future live/paper inputs**

When `PIT_CAPTURE_ENABLED=1`, wrap the current Neo4j driver with
`RecordingGraphDriver`. At successful full-cycle completion, atomically finalize
one bundle containing the graph query ledger, market-cap fundamentals, universe
rows, and the Alpaca/Google/Benzinga articles actually visible to that decision.
Capture failure logs dataset names and exception types only and cannot affect
trading decisions.

- [ ] **Step 6: Verify GREEN and commit**

Run Task 3 tests plus the complete existing PIT suite, run staged GitNexus
change detection, and commit:

```bash
git commit -m "feat(pit): capture and replay Graph Nexus inputs"
```

---

### Task 4: Legacy Classification and Promotion Enforcement

**Files:**
- Modify: `backend/strategies/graph_nexus_analysis.py`
- Modify: `backend/nexus_lookback_db.py`
- Modify: `backend/benchmark_alpha/promotion.py`
- Modify: `backend/tests/test_nexus_graph_snapshot_pit.py`
- Modify: `backend/tests/test_broker_nexus_resume_index.py`
- Modify: `backend/tests/test_alpha_promotion.py`

**Interfaces:**
- Strict trade-context rows include `pit_manifest_id`, `pit_as_of`, and `pit_provenance="strict_verified"`.
- Rows missing those fields classify as `legacy_unverified`.
- `load_nexus_processed_trade_context_dates(..., require_strict=True)` ignores legacy rows.
- `PromotionEvidence` includes `point_in_time_provenance_verified: bool`.

- [ ] **Step 1: Run GitNexus impact analysis**

Analyze `_save_trade_contexts_and_outcomes`,
`load_nexus_processed_trade_context_dates`, `historic_lookback_resume_dates`,
`PromotionEvidence`, and `evaluate_promotion`.

- [ ] **Step 2: Write failing provenance tests**

```python
def test_legacy_context_does_not_suppress_strict_lookback():
    rows = [{"date_key": "2026-01-05"}]
    assert processed_dates(rows, require_strict=True) == set()


def test_unverified_pit_evidence_blocks_promotion():
    evidence = replace(
        passing_evidence(),
        point_in_time_provenance_verified=False,
    )
    assert "point_in_time_provenance" in evaluate_promotion(evidence).reasons
```

- [ ] **Step 3: Run tests and verify RED**

Run the three Task 4 test files. Expected: missing fields/gate.

- [ ] **Step 4: Persist and enforce provenance**

Bind each historical trade-context write to its context manifest. Resume only
from a contiguous prefix of strict verified rows. Add the provenance gate to
`_RESEARCH_REASONS`; a legacy compatibility result can never reach paper or
live eligibility.

- [ ] **Step 5: Verify GREEN and commit**

Run Task 4 tests, staged GitNexus change detection, and commit:

```bash
git commit -m "fix(readiness): reject legacy PIT evidence"
```

---

### Task 5: Operations, Verification, and Inactive Deployment

**Files:**
- Create: `backend/scripts/import_point_in_time_bundle.py`
- Create: `backend/scripts/audit_point_in_time_coverage.py`
- Create: `backend/tests/test_point_in_time_scripts.py`
- Modify: `.env.example`
- Modify: `docs/runbooks/live-launch-checklist.md`

**Interfaces:**
- Import defaults to dry-run; mutation requires `--apply`.
- Coverage audit reports finalized dates, missing datasets, earliest/latest
  cutoff, verified month count, and legacy row count without exposing payloads.

- [ ] **Step 1: Write failing script tests**

Test dry-run non-mutation, explicit apply, invalid hash rejection, coverage
month calculation, and secret-key rejection.

- [ ] **Step 2: Implement scripts and configuration**

Document `PIT_CAPTURE_ENABLED=0` as the safe default. The import script accepts
one JSON bundle with all four required datasets and finalizes it atomically.
The audit exits nonzero when a requested date range has a missing session.

- [ ] **Step 3: Run comprehensive verification**

Run:

```bash
python3 -m pytest -q \
  backend/tests/test_point_in_time_registry.py \
  backend/tests/test_point_in_time_graph.py \
  backend/tests/test_point_in_time_data.py \
  backend/tests/test_broker_graph_nexus_pit.py \
  backend/tests/test_nexus_graph_snapshot_pit.py \
  backend/tests/test_nexus_news_pit.py \
  backend/tests/test_ticker_universe_pit.py \
  backend/tests/test_nexus_pit_capture.py \
  backend/tests/test_broker_nexus_resume_index.py \
  backend/tests/test_alpha_promotion.py \
  backend/tests/test_point_in_time_scripts.py
```

Also run `py_compile`, `git diff --check`, the secret-boundary tests, and a
read-only API check proving `alpaca-main.runCommand == false`.

- [ ] **Step 4: Detect changes, commit, and merge**

Run GitNexus change detection for the entire branch, commit the operations
files, merge into `main`, and push only after verifying no backtest is running.

- [ ] **Step 5: Verify inactive deployment**

Wait for the replacement API container, require `/health` and RethinkDB `ok`,
and verify `alpaca-main.runCommand == false`. Do not call the start endpoint.
