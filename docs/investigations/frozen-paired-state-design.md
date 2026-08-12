# Frozen, equally-warm paired-state backtest design

Date: 2026-08-10
Status: design only — no code/config was changed and no backtest was launched

## Decision

**The current backtest path cannot support a causal paired P&L claim. Do not launch the
anchor `12%` versus `20%` pairs yet.** It can start two fresh broker processes, but it cannot
prove that they received the same strategy document, the same persistent Nexus state, or the
same external evidence. A different `history_scope_salt` per arm is specifically forbidden:
it changes the history identity and can trigger discovery import from a mutable base-instance
snapshot. It is not a cold-start control.

The minimum acceptable design is:

1. freeze the resolved executable configuration when the backtest is enqueued;
2. build one arm-neutral, point-in-time baseline at the start of each window;
3. export that baseline as a content-addressed state/evidence bundle;
4. restore the **same bundle** into two physically isolated research databases;
5. run fresh processes with the same logical history scope, source image, dependencies, seed,
   clock, market/graph data, and model replay; and
6. verify a source-tagged BUY fill and recipient quantity increase before interpreting any
   return difference as an effect of funded anchor reinforcement.

“Equally warm” means identical rows and identical cache contents at the treatment boundary,
not “both arms used a cache,” “both were reset,” or “each arm got a new salt.”

## 1. Why the earlier pair design is not evidence

### Separate salts changed the treatment and the inherited state together

`backend/nexus_config_identity.py::history_scope_doc` includes
`history_scope_salt`; therefore a different salt produces a different
`history_scope_id`. `GraphNexusDiscoveredStocks` uses the scoped instance identity, but
`GraphNexusDiscoverySnapshots` is keyed by the **base** instance. On the first bar,
`_bootstrap_discoveries_from_snapshot` can import tickers from a sibling scope; its merge mode
can also add names to a non-empty scope. Each run can then replace the base snapshot with its
own ticker set. The arm order can consequently change the next arm's universe.

That is what invalidates the archived six-arm plan in
`docs/investigations/anchor-multi-window.md`: target and salt changed together, and new salts
were observed importing/saving different discovery state. Five of the six remaining arms were
correctly cancelled.

### The earlier “allocations” had no funded treatment exposure

Across bt **633644** (five plans), **584712** (AXTI), and **615886** (AAOI), all seven planner
allocations were rejected by `SATELLITE CAP`. No recipient quantity increased. Bt 615886's
`+9.02%`, for example, is not an anchor-20 outcome: AAOI held exactly the same quantity from its
original buy through its sell. A planner line is not a position.

Bt **735390** later proved that the research execution envelope can reach one identity-matched
UUUU fill and a quantity increase. It was a single arm, was deliberately stopped at 33.33%, and
failed its literal broad preregistration. It is useful mechanical evidence, not a causal return
comparison.

### Cross-run persistence has already changed nominally comparable runs

This is not a theoretical concern.

* `scripts/reset_backtest_event_state.py` documents same-window runs beginning with different
  active-event chains and different first-tick turnover ledgers (70%, 56%, 72%, and 72% of the
  monthly budget). It clears active events/history and `NexusStrategyCache` while intentionally
  retaining the maintenance cache. That is a targeted operational reset, not a complete frozen
  baseline.
* Same-window runs bt 820236 and 613166 discovered materially different universes.
* A prior bear-window run replaced shared `GraphNexusOverlayBarsCache` rows with narrower
  coverage. A later reference-window run then lost the 60-day momentum lane. Thus even a market
  data “cache” can be a cross-arm writer, not a neutral acceleration layer.

The historical 4.94 percentage-point run-to-run noise floor is therefore a warning, not a cure.
A large difference between contaminated arms is still contaminated.

## 2. What creation/startup actually freezes today

`interactive_utils.action_create_backtest` currently queues dates, stocks, instance, bar
seconds, initial cash, fee-emulation fields, and validated evidence options in
`BacktestInstances`. `CreateBacktestBody` has no immutable strategy/config snapshot field.

At broker startup, `broker.load_strategies_from_db` reads the then-current `Instances` row to
find `strategy_id`, reads the then-current `Strategies` row, and resolves current `Models` rows.
Only then does the broker build `_backtest_strategy_schema`; that later snapshot is written to
`BacktestResults`. It records what startup happened to read, but it does not freeze what was
queued. An edit between enqueue and execution changes the run silently.

A new subprocess and `PYTHONHASHSEED` are insufficient isolation:

* with no explicit `BACKTEST_SEED`, an empty discovery universe can reach the UUID fallback;
* RethinkDB and Neo4j remain shared across processes;
* Alpaca/news/Benzinga/Yahoo/LLM responses remain live unless replayed;
* cache TTLs and snapshot age gates can depend on wall-clock time; and
* completed backtests write `NexusStrategyCache` snapshots by default unless
  `NEXUS_BACKTEST_SNAPSHOT_WRITE=off`.

## 3. State that can be inherited or can leak into the next arm

The snapshot tool must inventory accesses dynamically and fail closed on an undeclared table or
network provider. The table below is the minimum known set, not permission to ignore newly added
state.

| class | known state/input | why it matters | required treatment |
|---|---|---|---|
| executable control plane | `Instances`, `Strategies`, referenced `Models`, experiment spec, prompt text/versions, environment flags, source tree, image and dependencies | currently read after enqueue; any edit changes behavior or identity | resolve at enqueue; store sanitized immutable snapshot and hashes; broker executes it rather than live documents |
| scoped discovery | `GraphNexusDiscoveredStocks`, `GraphNexusMarketTrends`, `GraphNexusTickerHistory` | universe, ranks and history resume differ by scoped instance and prior writes | include in the per-window baseline; exact clone; arm-local writes only |
| base discovery bridge | `GraphNexusDiscoverySnapshots` | one mutable row per base instance can import sibling-scope names and is replaced by a run | clone for audit, but disable bootstrap/merge and snapshot writing during measured arms |
| active-event chain | `GraphNexusActiveEvents`, `GraphNexusActiveEventHistory`, `GraphNexusActiveEventMaintenance` | maintenance output on day N becomes input on day N+1; different initial events invalidate every later cache key | clone the same as-of chain and cache; permit necessary chain writes only inside each arm clone |
| news/evidence caches | `GraphNexusNewsCache`, `GraphNexusNewsRaw`, `GraphNexusNewsFinBERT`, `GraphNexusNewsLLMCompany`, `GraphNexusNewsLLMMacro`, `GraphNexusNewsDayFeatures`, `GraphNexusLLMPromptCache`, `GraphNexusBenzingaCache`, earnings/provider caches | mutable articles, classifications, sentiment and prompt responses can change candidates and scores | preferably replace with sealed PIT/model replay; otherwise prewarm once, clone exactly, make read-only, and fail on a miss |
| analyst/overlay | `GraphNexusAnalystPanel`, `GraphNexusOverlayBarsCache`, `GraphNexusOverlayResultCache` | shared result rows and bar coverage have already contaminated later runs | content-address by complete PIT range/model identity; clone exact and read-only; no replace/upsert during arms |
| learning/outcome state | `GraphNexusLearningCache`, `GraphNexusOutcomes`, `GraphNexusOutcomeSeries`, `GraphNexusTradeContexts`, `GraphNexusTradeOutcomes`, `GraphNexusRotationCooldown` | affects cleanup gates, learned decisions, resume dates and cooldowns | same t0 rows in both clones; after t0, writes are allowed only arm-locally because treatment-caused learning is a legitimate mediator |
| runtime strategy state | process `_strategy_cache`, `NexusStrategyCache`, `NexusRuntimeState`, and relevant `LiveState` fields | includes deployment bars, entry/peak maps, cooldowns, queues and turnover ledger; persistent backtest snapshots can later seed live | declare one canonical runtime state (usually deterministic post-lookback state), hydrate both arms explicitly; forbid “load latest”; disable end-of-run shared snapshot writes |
| market/benchmark/PIT | price bars, corporate actions, fundamentals, calendars, adjusted SPY benchmark, point-in-time manifests | current backtests can fetch a changed response for a historical date | immutable content-addressed PIT bundle; same observations and availability timestamps in both arms; no live fallback |
| graph | Neo4j nodes, edges, aliases, co-holdings and query results | current graph is lookahead-prone and can mutate between arms | offline Neo4j database snapshot or sealed query-result fixture, with graph digest and as-of cutoff |
| model execution | provider/model adapter, prompts, schemas, generation settings, fallbacks and validated outcomes | greedy/seeded remote calls are not a guarantee of identical provider behavior | use the existing model-evidence record/replay seam; measured arms are replay-only and make zero provider calls |
| process/host | explicit RNG seed, `PYTHONHASHSEED`, wall clock, timezone, locale, worker ordering, credentials/data-feed identity | changes tie-breaking, TTLs, availability and fallbacks | pin in manifest; use deterministic occurrence IDs/sorts; credentials are referenced by non-secret identity only |
| outputs | `BacktestInstances`, `BacktestResults`, logs, receipts | operational/result state is not an input to the decision policy | store outside decision-state clones or in arm-local output stores |

A table that is “only a cache” still belongs in the manifest if its hit/miss changes a strategy
input. A table that is not read by a backtest should be denied rather than silently cloned “just
in case.”

## 4. Required minimum tooling

None of the following complete contract exists today. It must exist and pass a no-treatment
replicate test before the anchor comparison is launched.

### 4.1 Immutable queue-time executable snapshot

Add a sanitized `execution_snapshot` to `BacktestInstances`, built in the same transaction as
queue creation. At minimum it contains:

* resolved instance identity and strategy id;
* the full ordered strategy specs (`conditions`, `config`, weights, phases and experiment spec);
* resolved model provider/model/adapter identities, never plaintext credentials;
* source-tree digest, immutable container image digest, dependency/runtime digest;
* exact environment allowlist, bar/calendar/data-feed identity, cost model, cash, window and
  explicit seed; and
* `execution_snapshot_sha256` over canonical JSON.

The broker must fail closed if the snapshot is absent, its hash differs, or it would need to read
live `Instances`/`Strategies`/`Models` to decide what to execute. `BacktestResults` must copy the
same queue-time hash; it must not substitute a newly read schema.

For a pair, canonicalize both snapshots and remove the preregistered treatment path. The remaining
JSON must be byte-identical. The full diff must be exactly:

```text
strategies[graph_nexus_analysis].config.anchor_reinforce_target_pct: 12 -> 20
```

For the target-dose estimand, `anchor_reinforce_execution_enabled=true` and every execution/risk
limit must be the same in both arms. Do not simultaneously test enablement, target, salt, cap, or
turnover rules. A separate enable/disable experiment would hold target at 20 in both arms and
change only the enable flag.

### 4.2 Per-window consistent state bundle

Build a tool with `export`, `restore`, and `verify` operations (name is unimportant). Its manifest
must contain:

```yaml
protocol_version: frozen-paired-state-v1
pair_id: ...
window: {start: ..., end: ..., baseline_cutoff: ...}
logical_identity:
  base_instance_id: v2-let-run-core
  history_scope_id: ...          # identical in both arms
  history_scope_doc_sha256: ...
execution:
  common_snapshot_sha256: ...
  control_snapshot_sha256: ...
  treatment_snapshot_sha256: ...
  allowed_diff: {anchor_reinforce_target_pct: [12, 20]}
  source_tree_digest: ...
  image_digest: ...
  dependency_runtime_digest: ...
  seed: ...
state:
  bundle_sha256: ...
  tables:
    GraphNexusDiscoveredStocks: {selector: ..., rows: ..., sha256: ...}
    # every declared decision-state table
external:
  pit_fixture_id: ...
  pit_manifest_sha256: ...
  neo4j_snapshot_id: ...
  neo4j_sha256: ...
  model_fixture_id: ...
  model_ledger_sha256: ...
clock: {wall_time: ..., timezone: UTC, market_calendar: ...}
```

Hash canonical, primary-key-sorted rows, including decision-relevant timestamps and availability
fields. Record full export hashes as well as per-table row count/hash. Do not take a multi-table
snapshot from an actively written production database: RethinkDB does not make an ad hoc sequence
of table exports an atomic cross-table checkpoint. Build/quiesce a dedicated research database,
then snapshot it.

The preferred physical isolation is two disposable RethinkDB servers, each exposing a database
named `IntelliStock`, restored from the same bundle. That lets current hard-coded table names and
logical `instance_id|history_scope_id` values remain identical while the physical write targets are
different. Give each arm its own Neo4j clone too, or use a read-only sealed query fixture. Merely
adding `control`/`treatment` to `history_scope_salt` is not physical isolation.

A sequential fallback is acceptable only if the complete bundle is transactionally restored before
each arm and a digest proves that no other service wrote during the pair. Never restore a research
snapshot over the live database.

### 4.3 Arm-neutral baseline builder

For each window, build the canonical baseline once from empty declared state using only evidence
available by `baseline_cutoff` (the last decision boundary before treatment begins). Use the common
configuration: the treatment is not active while prehistory/lookback is constructed. Seal the
resulting persistent rows **and the post-lookback process runtime cache**. This identifies the
estimand as “activate target 20 rather than 12 at the start of this window.”

If the desired estimand is instead “the policy had always been 20 throughout prehistory,” the two
arms cannot share a post-policy baseline; they need separately replayed prehistories, which is a
different and much less tightly paired experiment. Do not mix the two estimands.

The baseline build must use strict PIT manifests and model-evidence record mode. Measured arms use
that sealed fixture in replay mode, deny network fallbacks, and fail on a missing occurrence or
cache miss. Current equity backtests default to `pit_mode=research` because historical strict
snapshots generally do not exist; such runs remain lookahead-biased and cannot satisfy this design.

### 4.4 Write isolation and read-only shared evidence

During measured arms:

* set `nexus_discovery_bootstrap_enabled=false` and merge mode false;
* set `nexus_discovery_snapshot_enabled=false`;
* set `NEXUS_BACKTEST_SNAPSHOT_WRITE=off`;
* make PIT, graph, prompt/model, overlay-bar, overlay-result, article, classification and analyst
  fixture tables read-only, or serve them from immutable fixture files;
* permit discovery/event/outcome/learning writes only to the arm's disposable database; and
* deny access to production RethinkDB/Neo4j and all external data/model network endpoints.

Before and after a pair, hash the real shared stores. Any mutation makes the pair invalid even if
arm-local receipts look correct.

## 5. Paired protocol

### 5.1 Fixed question and arms

Primary estimand: the effect, from the first measured decision onward, of changing
`anchor_reinforce_target_pct` from **12** (control) to **20** (treatment), under an identical enabled
research execution envelope.

Hold fixed: instance `v2-let-run-core` / strategy doc 193 snapshot, 3,600-second bars, $6,000 cash,
explicit seed, source/image/dependencies, evidence and cost model. Also hold fixed in both arms the
execution enable, 20% admission cap, 80% accepted-request turnover ceiling, core-floor funding,
pending-sell credit, fresh-low N=2, rally-onset setting, rotation setting and regime profiles. The
queue-time snapshot, not the mutable live doc, is authoritative.

### 5.2 Pre-register at least these four window blocks

| block | window | purpose |
|---|---|---|
| W0 | `2026-01-01..2026-03-01` | historical reference and known anchor-opportunity window |
| W1 | `2026-03-30..2026-04-27` | historically labelled OOS bull window |
| W2 | `2026-03-02..2026-03-30` | bear/safety window |
| W3 | `2026-06-01..2026-07-01` | non-semiconductor-led generalization (RXD/ATEN/UAL leadership in prior audit) |

The “OOS” label is historical; these dates have been inspected previously and are not newly unseen
data. They are still useful blocked causal contrasts because the target-dose pairs have not been
validly run. An optional fifth long generalization block is `2025-11-10..2026-02-24`, but it cannot
replace W2 or W3.

Freeze a separate baseline/evidence bundle for each window. Do not let W0's terminal state seed W1.

### 5.3 Execution order

1. Publish the protocol, window list, outcome rules, treatment diff and all bundle/snapshot hashes.
2. Run a **negative-control replicate** first: two target-12 arms from the same clone with the same
   seed. Trade ledger, decisions, fills, snapshots and final NAV must hash identically. Any mismatch
   blocks the study and becomes a determinism bug.
3. For every window, restore the same bundle to control and treatment databases and verify every
   preflight hash.
4. Choose arm order from a preregistered hash/coin, or run simultaneously on isolated hosts. Arm
   labels remain hidden from any manual intervention. Do not push/deploy while a pair is running.
5. Run both arms to the same terminal boundary. A stopped, failed, paused, partial, provider-fallback
   or audit-incomplete arm invalidates its pair; do not replace it opportunistically.
6. Export arm-local write-set, execution, treatment-exposure and result receipts; then verify shared
   stores remained unchanged.

## 6. Treatment-exposure contract

There are three different facts and all must be recorded:

1. **assignment:** the control/treatment execution-snapshot hashes and the one-key diff;
2. **treatment read/intent:** the target used at each eligible anchor calculation, winner, stage,
   plan id, target/current/needed/planned dollars, and reason for `PLAN NONE`; and
3. **funded exposure:** accepted order and correlated fill that changes the recipient position.

For every plan, reconcile this chain by symbol, stage and immutable identity:

```text
ANCHOR PLAN
  -> ANCHOR BLOCK(gate=...)  OR  ANCHOR ORDER(plan_id, order_id, requested dollars)
  -> execution FILL BUY(source=anchor_reinforcement, plan_id, order_id)
  -> ANCHOR FILL(actual dollars/qty)
  -> STAGE COMMIT or explicit PARTIAL
  -> equity snapshot quantity_before + fill_qty = quantity_after
```

Also record the first binder and dollars lost to `SATELLITE CAP`, turnover ceiling, core floor,
single-position cap, max-positions, cash/min-order, submission rejection or no-fill. A generic BUY or
an unchanged mark-to-market position is not exposure.

Pair-level first-stage fields:

* treatment minus control cumulative source-tagged anchor fill dollars and fill quantity;
* recipient weight difference by bar and exposure-weighted bar count;
* treatment-only plans, orders and fills, plus plans both arms emitted at different sizes;
* any displacement of non-anchor orders, core releases or turnover (planning spillover); and
* the exact first decision at which the trade/decision ledgers diverge. No divergence is allowed
  before the treatment key is read.

**Mechanical exposure** requires a strictly positive incremental correlated fill and recipient
quantity difference, followed by at least one valuation bar. For the P&L mechanism to be
interpretable rather than a dust-order artifact, preregister an economically meaningful first stage:
incremental filled notional at least `max($50, 1% of initial NAV)` (therefore $60 here), or an
incremental recipient weight of at least 1 percentage point held for one complete bar.

If every treatment plan is blocked, if both arms receive the same filled quantity, or if the only
change is a planner allocation with no recipient increase, label the pair **NO DIFFERENTIAL FUNDED
EXPOSURE**. Its return is mechanistically inconclusive regardless of P&L. Planning spillovers may be
reported as a separate adverse mechanism, but must not be described as “reinforcing winners.”

## 7. Validity and analysis gates

A pair is valid only when all are true:

* common queue snapshot differs only at the registered treatment path;
* t0 state bundle, logical history scope, PIT/model/graph fixture, seed, clock, source and dependency
  hashes match;
* no undeclared DB table, provider or live document was read;
* neither arm wrote outside its disposable store;
* negative-control determinism has passed;
* both arms finished the full window and accounting/execution/PIT/replay audits passed; and
* treatment/order/fill/quantity identities reconcile without orphan, mismatch or duplicate.

Report invalid pairs; never silently rerun them with a new snapshot. A repair creates a new protocol
version and new pair id.

For every valid, exposed pair report treatment minus control:

* total return and SPY active return;
* max-drawdown magnitude;
* source-tagged anchor P&L, incremental anchor notional and exposure-time;
* gross turnover, execution cost, core releases and displaced orders; and
* held-name overlap before first exposure (must be 100%) and after exposure (a mediated outcome).

Keep the historical **4.94pp** threshold as the predeclared materiality/noise floor for promotion:
inside ±4.94pp is economically inconclusive; a return improvement cannot compensate for a drawdown
worsening of 4.94pp or more. The bull block must also beat its frozen SPY benchmark; the bear block
is a safety veto, not a place to tune a special profile. Do not pool or apply these return rules to a
window with no differential funded exposure.

With only four chosen historical blocks, publish the four paired deltas and their median/sign rather
than manufacturing a high-powered significance claim. Promotion requires valid exposure in at
least three blocks including the bull, bear and non-semiconductor blocks, no safety veto, and a
separately declared aggregation rule written before runs.

## 8. Preflight/postflight checklist

### Preflight (fail closed)

- [ ] queue-time executable snapshots exist; exact allowed one-key diff
- [ ] explicit identical `BACKTEST_SEED` and `PYTHONHASHSEED`
- [ ] identical `history_scope_id`; no arm-specific history salt
- [ ] per-window baseline bundle export/restore hashes match in both databases
- [ ] model ledger, PIT bundle, graph snapshot, bars and benchmark hashes match
- [ ] cache fixture is complete/read-only; no live-provider fallback
- [ ] discovery bootstrap/merge/snapshot write and shared strategy-snapshot write are off
- [ ] production DB/graph/network are unreachable from arm containers
- [ ] target-12 negative-control replicate produced identical artifact hashes
- [ ] no deployment/code/config edit is scheduled during the pair

### Postflight (before looking at returns)

- [ ] both terminal statuses are complete and audit-eligible
- [ ] shared-store before/after hashes are unchanged
- [ ] arm-local write sets contain only declared tables/namespaces
- [ ] first ledger divergence is at/after a logged treatment read
- [ ] every anchor plan reconciles through block or order/fill/quantity
- [ ] pair has differential funded exposure, or is labelled mechanistically inconclusive
- [ ] market/PIT/model occurrence manifests are complete and identical
- [ ] report was generated from immutable receipts, not mutable live config/current caches

## Bottom line

The next useful work is not another salted backtest. It is queue-time config freezing plus a
content-addressed research-state/PIT/model bundle that can be restored into two isolated stores.
Only after an identical-target negative control hashes identically should the four target-12 versus
target-20 blocks run. Only after a source-tagged incremental fill increases treatment recipient
quantity should their return delta be interpreted as evidence about funded anchor reinforcement.
