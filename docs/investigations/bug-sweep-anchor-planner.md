# Anchor execution contract: planner/accounting bug sweep

**Read-only code snapshot:** 2026-08-10 11:15:54 UTC, `HEAD=2cd998c`, after the
operator's turnover/core-funding/final-nonbuy follow-up edits. The reviewed bytes were:

| file | SHA-256 |
|---|---|
| `backend/strategies/graph_nexus_analysis.py` | `9114433bc0555396f3af0a2ff5b554fc5b9dbf0510fcbf83d222f99c23b68993` |
| `backend/broker.py` | `2850bd131526bb4a2cfcd63ced57dc55157220d62416ea5f3a7645e4eddc9ac4` |
| `backend/tests/test_anchor_execution_contract.py` | `4f7b0e1f7b722c90a014421693a1e9ef585421cbf0f6e646b2781ab05ddd6c46` |

No code/config/API/git-index/commit/push/backtest state was changed. This report is the only
repository write. Reproduction output is under `/tmp/bug-sweep-anchor-adversarial-repros*.txt`.

## Verdict

**Do not enable `anchor_reinforce_execution_enabled` yet.** The patch fixes the original
plan-time stage commit and uses actual marked position value in the planner, but the full
plan/order/fill lifecycle is not correct. I reproduced false stage completion, cross-stage fill
misattribution, sub-policy order funding, retry crowd-out, passive-expiry deadlock, restart
poisoning, stale stage state after re-entry, and multi-symbol core-release over-accounting. The
explicit default-OFF branch also changes the legacy output payload when the flag is absent/false,
so the promised byte-identical control is not established.

## Must-fix blockers

### M1. A deliberately partial plan can complete the whole stage

**Code:** `backend/broker.py:11134-11158`; missing adversarial coverage at
`backend/tests/test_anchor_execution_contract.py:304-319`.

The fill handler commits whenever a final order leaves less than `min_position_size` of target gap:

```python
if remaining + 1e-9 < min_fill:
    stage_map[symbol] = ... stage
```

That is not the declared contract. The audit explicitly required that a `$150` plan against a
`$234` need remain partial, but only the pre-fill pending state is tested. I drove a real
`PortfolioEmulator` with current value `$966`, target `$1,200`, planned/final fill `$150`. The fill
left `$84` remaining and emitted:

```text
ANCHOR STAGE COMMIT: WIN stage=1 filled=$150.00 target=$1200.00 remaining=$84.00
```

A final fill means the **order** is done, not the **stage**. Completion must be based on the defined
stage-fill policy (and exact correlated cumulative fills), not “the residual is too small to place
another order.” If dust abandonment is desired, it needs an explicit, separately named policy and
log; it cannot silently turn a partial into a completed stage.

### M2. Fill-time “position value” is not actual marked position value

**Code:** `backend/broker.py:11122-11127`; related correct emulator marking is
`backend/portfolio_emulator.py:1198-1205`.

The handler computes `current_qty * fill.price`. A BUY fill price is the ask plus modeled slippage;
the emulator intentionally marks the position at the quote mid. The handler therefore values all
pre-existing shares at the inflated execution price and can falsely cross the stage target.

Reproduction with a deliberately wide spread: a `$100` final buy filled at `$110`; handler value
was exactly `$1,200` and committed stage 1, while the actual mid-marked value was `$1,090.91`, still
`$109.09` short (at/above the `$100` policy floor). Use the emulator's actual mark/current price,
not the fill price, for target satisfaction.

### M3. Source/stage/order identity is not correlated

**Code:** pending record creation `graph_nexus_analysis.py:10913-10919`; order source/acceptance
`broker.py:16360-16390`; fill handling `broker.py:11110-11121`.

The pending record has ticker/stage/dollars but no plan ID or order ID. On accepted submission, the
returned order ID is logged but never stored. On fill, the code checks only
`source.startswith("anchor_reinforcement:")`; it never parses `stage=N` or verifies that source
stage/order ID equals the current pending record.

I submitted a real fill whose source was `anchor_reinforcement:stage=1` while the pending ticker
record said stage 2. The handler trusted the pending record and committed **stage 2**. A stale,
duplicated, restored, or otherwise mis-correlated fill can therefore advance the wrong stage.

Pending state must carry a unique plan ID plus the accepted order ID; fill handling must parse and
validate source stage and order identity before adding notional or committing. Unknown/mismatched
fills must fail closed without mutating the stage map.

### M4. Broker rejects recreate the original repeated budget crowd-out problem

**Code:** pending suppression/creation `graph_nexus_analysis.py:10834-10837,10910-10919`; planner
spend `:30310-30313`; broker pop `backend/broker.py:3561-3565`.

Pending suppresses duplicates only while the record exists. Every immediate broker block pops it.
On the next eligible bar the planner creates the same plan and deducts it from the new-name slate
again. Reproduction:

```text
plan 1: allocation=$150, remaining anchor budget=$0
broker block: pending cleared
plan 2: allocation=$150, remaining anchor budget=$0
```

A structural satellite/turnover/position-cap reject can repeat on every bar, repeatedly starving
unrelated entries without buying the anchor. This is exactly the unsafe retry-only behavior the
pre-edit audit warned against. A rejection needs bounded retry state (gate-aware cooldown/backoff or
a no-crowd-out retry reservation), while a live accepted order remains uniquely pending until it
fills/cancels/expires.

### M5. The anchor min-fill check can approve a `$50` order under a `$100` policy

**Code:** generic fundable helper `backend/broker.py:3609-3653`; conditional use
`:3690-3722`; anchor comparison `:15961-15978`.

The anchor branch compares `_emp_fundable` with policy `min_fill`, but
`_exec_min_position_gate` calls the actual `get_buying_power` clamp only when
`min_position_nav_pct > 0`. With that independent config absent/zero, `_emp_fundable` is simply
`cash_to_use` even though `PortfolioEmulator.execute_signal` still subtracts in-flight BUY
reservations and unsettled proceeds.

Reproduction: requested/cash-to-use `$200`, earlier in-flight reservation left `$50` actual buying
power, policy minimum `$100`. The gate returned `skip=False, fundable=$200`; the real simulator
accepted a `$50` order. The anchor policy must always measure `_exec_fundable_amount` for this
lane, irrespective of the unrelated new-position NAV-floor flag.

### M6. Expire/cancel/restart lifecycle can permanently wedge a ticker

**Code:** pending suppression `graph_nexus_analysis.py:10834-10837`; pending has no order identity
`:10913-10919`; accepted order does not update it `backend/broker.py:16375-16390`; only block/fill
remove it `:3561-3565,11134-11158`.

There is no expiry/cancel/reconciliation callback. With passive execution and
`expire_after_quotes=1`, I submitted an anchor order, fed a non-marketable quote, and got zero
fills. The simulator correctly had no pending order afterward, but strategy cache still contained
`_anchor_reinforce_pending["WIN"]`; the planner then suppresses WIN forever.

The cache serializer persists all non-blacklisted keys
(`backend/strategy_cache_persistence.py:237-261`), so `_anchor_reinforce_pending` round-trips even
though it has no order ID. After serialize/restore, a fresh planner returned no plan and the full
budget unused: it cannot distinguish a live order from an orphaned record. A last-bar unfilled
order or restart can therefore poison later state/snapshots.

Required lifecycle: store accepted order identity/status, receive or poll terminal reject/cancel/
expiry, clear or retry by policy, reconcile persisted pending records against the actual order
book on boot, and never hydrate a backtest-only orphan into a new execution process.

### M7. Completed stage state belongs to a ticker forever, not to a position episode

**Code:** stage lookup `graph_nexus_analysis.py:10793,10863-10866`; only writes occur at legacy plan
`:10920-10923` and confirmed fill `backend/broker.py:11141-11144`. There is no full-exit cleanup or
position-generation key anywhere in the repository.

A ticker that completed stage 1, was fully sold, and was later re-entered has a new position but the
old `stage_state["WIN"] = 1`. Reproduction with a fresh 8-day/+15% candidate and that cache returned
no funded stage-1 plan. Persisted live cache makes the lifetime longer still. Key stage/pending
state by a position episode (entry/order lineage) or clear it only on a confirmed zero-quantity
full exit; clearing on sell intent would be too early.

### M8. Absent/false is not byte-identical beyond the isolated planner

**Code:** `backend/strategies/graph_nexus_analysis.py:30315-30331`; insufficient test
`backend/tests/test_anchor_execution_contract.py:76-85`.

Even with the execution flag absent/false, every legacy winner/anchor hint now unconditionally
contains four new keys:

```python
"anchor_reinforcement": False,
"anchor_stage": <legacy stage>,
"anchor_target_total": None,
"anchor_execution_max_position_pct": None,
```

Those keys did not exist in `HEAD`. The test checks only `_plan_anchor_reinforcement`'s funded item,
not the emitted `nexus_position_sizes`/run-once metadata. Thus allocations may be unchanged but the
serialized/aggregated payload is not byte-identical. Add these keys only in the true branch and
compare the complete absent/false run-once output (including cache/log/payload bytes) to legacy.
Also avoid parsing new execution-only config values before the flag branch
(`graph_nexus_analysis.py:10802-10815`) if strict dormant-path identity is required.

### M9. Core-funding turnover precheck is not cumulative across multiple anchors

**Code:** `backend/broker.py:14824-14869`; execution does correctly re-read the ledger per order at
`:15540-15578`.

The core pre-pass evaluates every anchor against the same current rolling ledger and never adds
previously admitted planned anchor dollars to the local projection. At used turnover `0.74`, two
`$200` anchors on `$6,000` NAV each independently pass (`0.7733 <= 0.80`), but together project
`0.8067`. The pre-pass can release core for both; execution records the first, then blocks the
second, leaving excess core release/redeploy churn. Accumulate admitted pre-pass notional in exact
execution order, and test at least two symbols around the boundary.

### M10. Partial retry accounting is not cumulative per stage

**Code:** initialization `graph_nexus_analysis.py:10913-10919`; update/pop
`backend/broker.py:11118-11120,11134-11158`.

An incomplete final fill is logged as partial and the entire pending record is popped. The next
retry creates `filled_notional: 0.0`; there is no completed/partial stage fill ledger. If a later
order commits, `ANCHOR STAGE COMMIT filled=$...` reports only the last order, not total dollars
filled into that stage. This breaks the required exactly-once plan/order/fill reconciliation even
when sizing from actual position value is correct. Preserve cumulative stage dollars separately
from the live-order pending record and deduplicate by order/cumulative fill identity.

## Follow-up findings (not independent reasons to keep the flag off after M1-M10)

1. **The new config keys are absent from the line-1 `INTELLISTOCK_SCHEMA`.** None of
   `anchor_reinforce_execution_enabled`, `anchor_reinforce_execution_max_position_pct`,
   `anchor_reinforce_execution_turnover_ceiling_pct`, or
   `anchor_reinforce_execution_core_floor_enabled` appears in the strategy schema. Before an
   experiment, expose/validate all four with unambiguous units (`*_pct` currently means a fraction
   for turnover but percent points for position cap), while preserving default OFF.
2. **No structured planner rejection breakdown.** `ANCHOR PLAN NONE` at
   `graph_nexus_analysis.py:30295-30301` still reports only document count and budget, not
   age/P&L/drawdown/stage/pending/target-gap/budget reasons requested by the audit.
3. **Expiry is not visible in the public execution summary.** The passive simulator increments an
   internal expired counter but its `execution_summary()` currently exposes pending and rejected
   counts, not expired count (`backend/simulated_execution.py:628-640`). This made the terminal
   no-fill look like neither pending nor rejected. It should be auditable separately from anchor
   pending reconciliation.
4. **Unused duplicate helper.** `_anchor_reinforcement_position_headroom` is asserted directly in
   the new tests but production duplicates the arithmetic inline. That test can pass while the real
   buy loop drifts. Drive the actual broker choke point or extract one helper used by production.

## Test assessment

Commands run in the project's native `python3` environment:

```text
python3 -m pytest \
  backend/tests/test_anchor_execution_contract.py \
  backend/tests/test_anchor_reinforce_target.py -q
# 20 passed

python3 -m pytest backend/tests/test_dead_config_registry.py -q
# 47 passed
```

The green focused suite does not establish the contract:

* `test_absent_or_false...` stops at planner output and misses unconditional run-once metadata.
* turnover/final-decision/core-funding “wiring” tests (`:208-238`) inspect source substrings rather
  than executing the broker loop; they cannot expose multi-symbol state or accounting order.
* real fill tests call the handler directly with zero spread and only an exact full target or a
  very large residual. They miss residual-below-floor partials, nonzero execution costs/marks,
  stage/source/order mismatch, duplicate cumulative events, retries, and multiple stages/symbols.
* no test drives an immediate reject followed by the next planner bar, passive expiry/cancel,
  restart/serialization reconciliation, full exit/re-entry, or buying power below policy minimum.

Add adversarial tests for every M-item before enabling. At least one test must traverse the actual
`run_once -> broker gates -> PortfolioEmulator next-event -> fill callback -> next run_once` path;
AST-extracted helpers and greppable source strings are useful supplements, not execution-contract
proof.

## Manual blast radius

GitNexus cannot provide symbol coverage for these edits because
`graph_nexus_analysis.py` (~1.7 MiB) and `broker.py` (~0.9 MiB) exceed the indexer's 512 KiB file
limit. I did not claim LOW risk from missing graph results.

Manual radius is at least **MEDIUM**:

* `_plan_anchor_reinforcement` has one production caller in `GraphNexusAnalysis.run_once` plus the
  planner tests; its spend changes both anchor sizing and the new-name slate.
* run-once anchor hints feed the shared broker aggregation/execution path.
* `_apply_backtest_confirmed_fill_state` observes every backtest fill; its new branch is
  source-prefixed but owns stage accounting.
* the broker buy choke point serves every lane; the policy branch is double-gated/default OFF, but
  core funding, turnover, cash/buying-power, settlement, passive order lifecycle, and global cache
  persistence all intersect it.
* related lifecycle surfaces are `PortfolioEmulator.execute_signal/process_quote`,
  `NextEventExecutionSimulator`, and `strategy_cache_persistence`.

No HIGH/CRITICAL graph result was available; that is an index limitation, not evidence of safety.
