# Final independent bug sweep D — anchor observability/terminology

**Date:** 2026-08-10
**Base:** `960a469`
**Audited bytes:** `backend/broker.py` SHA-256 `9ad211efd822342bd2809b680fc23209e99929207ce0a72ba7cde8b41422fd4f`; `backend/tests/test_anchor_execution_contract.py` SHA-256 `2c2c93bbee1d3f092891fa872d9657c3f232418cf8f5a70ae266bf5bf6810895`
**Scope:** read-only review of those final uncommitted code/test changes. I read sweeps A, B, and C plus the bt735390 cap and turnover audits. I did not edit production code, tests, configuration, CSVs, Git state, or any backtest; this report is the only requested artifact written by sweep D.

## Verdict

**BLOCK / request changes.** Sweep C's incomplete-production-mark-map and remaining turnover-wording blockers still stand. Independently of those, sweep D found two additional telemetry-correctness defects and one low-probability failure-path state defect:

1. invalid numeric marks (`NaN`, infinity, zero, or negative) are accepted into the new valuation overlay and can fabricate or suppress cap drift;
2. a same-symbol, same-tick fill batch makes the source-specific `ANCHOR CAP DRIFT` line describe the post-*batch* position, so another fill source can create the warning attributed to the anchor fill; and
3. failure of the newly added drift-warning log can interrupt final stage disposition after the portfolio fill.

There is still **no normal-path cap, turnover, quantity, order, fill, trimming, or forced-rebalance behavior change** in the diff.

## New D findings

### D-1 — HIGH for the stated telemetry objective — invalid numeric current marks poison NAV/weight instead of falling back or reporting unavailable

**Code:** `backend/broker.py:11225-11241`, especially the overlay conversion at `11228-11233` and the zero-value exception fallback at `11238-11241`.

The overlay catches values that cannot be converted to `float`, but conversion is not validation. `float("nan")`, `float("inf")`, `0.0`, and negative values all pass and overwrite a valid prior mark. `PortfolioEmulator.get_portfolio_value` does not reject them; it propagates them into NAV. The guard `fill_nav > 0` then turns non-finite/nonpositive NAV into `weight=0`, suppressing the drift warning. Zero can instead remove a holding from NAV and create a false warning.

This is reachable through the production price path, not merely a hand-built mapping: `_get_prices_at_time` at `11054-11058` converts bar closes with `float` but does not require a finite positive value. The later held-position completion path also does not reliably replace `NaN`/infinity, and, as sweep C established, it runs after fill reconciliation anyway.

Using the real AST-extracted helper and a real multi-asset `PortfolioEmulator`, I seeded WIN=$1,000, OTHER=$4,000, cash=$1,000, and valid prior marks of $100. A final $200 WIN anchor fill at a $100 mid should leave fallback NAV=$6,000 and WIN weight=20%. Only the supplied current OTHER mark changed:

| current `OTHER` input | emitted NAV / weight | warning | valid-prior fallback truth |
|---|---|---|---|
| `NaN` | `nav=$nan weight=0.0000%` | none | `$6000 / 20%` |
| `+inf` | `nav=$inf weight=0.0000%` | none | `$6000 / 20%` |
| `0.0` | `nav=$2000.00 weight=60.0000%` | **false drift** | `$6000 / 20%` |
| `-50.0` | `nav=$0.00 weight=0.0000%` | none | `$6000 / 20%` |
| `"bad"` | `nav=$6000.00 weight=20.0000%` | none | correct fallback |

A separate failure injection made the real emulator's read-only valuation raise `RuntimeError`. Reconciliation safely continued, but the helper logged `nav=$0.00 weight=0.0000%` and no diagnostic rather than stating that valuation was unavailable. Exception safety is desirable; silently substituting a real-looking zero/zero observation is not.

**Required:** accept an overlaid mark only if it is finite and positive, otherwise retain the prior valid mark. Validate the final NAV/weight as finite too; if valuation truly fails, emit explicit `unavailable`/valuation-error telemetry rather than a false 0/0 measurement. Add exact tests for missing, nonnumeric, `NaN`, infinity, zero, negative, and valuation-exception cases.

### D-2 — MEDIUM observability semantics — same-symbol batches can attribute another source's exposure to the anchor cap warning

**Code:** fills are all applied inside `PortfolioEmulator.process_price_events` (`portfolio_emulator.py:1306-1328`) before the broker iterates them at `broker.py:12659-12683`. The helper reads the emulator's already-final quantity/cash at `11206-11213`.

For multiple fills on different tickers, using the post-batch portfolio is a valid emitted-tick snapshot. For multiple orders on the **same ticker**, however, the new source-specific line combines the anchor fill's notional with the quantity after every same-tick source has filled.

Real-emulator reproduction:

* initial WIN: 10 shares at $100, NAV $6,000;
* accepted anchor order: $200, frozen cap 20%;
* accepted `main_signal` order on the same WIN event: $600;
* both next-event fills at $100 in one real `process_price_events` call.

The anchor order alone reaches exactly 12 shares / 20%. The helper instead emits after both fills:

```text
ANCHOR FILL: WIN ... fill=$200.00 ... quantity=18.00000000 ...
position_value=$1800.00 nav=$6000.00 weight=30.0000% admission_cap=20.00%
ANCHOR CAP DRIFT: WIN fill-snapshot weight=30.0000% > ... 20.00%
```

Both numerical values are correct for the final emitted **tick**, but not for the source-specific anchor fill state; the extra 10 percentage points came from `main_signal`. The simulator permits multiple pending orders per symbol, and scheduled plus main/anchor submissions share that simulator, so this is a supported batch shape rather than an invalid `SimulationFill`.

This does not create a trade or trim, and the pre-existing stage calculation already read the post-batch quantity. The new defect is causal telemetry: the line intended to distinguish anchor overfill/gap drift from other causes can blame the anchor signature for another source's same-tick exposure.

**Required:** define and test the semantic explicitly. If the intended observation is the emitted post-batch tick, label it `tick-snapshot` and make clear that same-tick other-source fills are included. If it is meant to be source-specific post-fill state, capture/reconstruct state at each accepted fill rather than reading the portfolio only after the full batch. Add a same-symbol anchor + non-anchor regression, not only multi-ticker tests.

### D-3 — LOW — the new drift-warning `_log` adds a failure point before final stage commit/partial cleanup

**Code:** `backend/broker.py:11251-11260` occurs before final stage disposition at `11261-11281`.

The generic execution log is correctly protected by `finally`, and the valuation calculation is broadly caught. The new `ANCHOR CAP DRIFT` log itself is not fail-safe. I compiled the repository's actual `_log` and helper, let the primary logger successfully write `ANCHOR FILL`, then made the primary logger fail specifically on `ANCHOR CAP DRIFT` while fallback stdout raised `BrokenPipeError`. The confirmed fill had already changed cash/quantity and the anchor fill ledger, but the result was:

```text
calls: ['ANCHOR FILL', 'ANCHOR CAP DRIFT']
raised: BrokenPipeError('stdout closed')
stage: {}
pending: ['WIN']
filled ledger: {'WIN': {'stage': 1, 'filled_notional': 200.0}}
```

On the next reconciliation the simulator no longer had the final order, so pending state was cleared as `no_active_order` without committing the otherwise complete stage. The old `ANCHOR FILL` log already had a logger-failure hazard, but this patch adds a second diagnostic-only opportunity after that old call succeeds. It is rare because both the primary and fallback sinks must fail, hence LOW, but it violates the stated observability-only guarantee on that failure path.

**Required:** make the new warning best-effort so its failure cannot prevent the already-confirmed final-fill disposition. Add a failure-injection test where `ANCHOR FILL` succeeds and only `ANCHOR CAP DRIFT` fails.

## Focused checks that passed

### Frozen-cap lifecycle

The intended provenance lifecycle is sound on normal inputs:

* the planner creates a pending plan without claiming an accepted-order cap;
* successful broker submission binds `order_id` and the local admission policy's percentage into that same record (`16585-16603`);
* fill reconciliation reads the pending value rather than mutable `_cached_strategies`;
* a real partial fill retained the active pending order and `admission_cap_pct=20.0`, then the later final fill used it and cleared pending state;
* a real passive expiry removed the order from the simulator, after which `_reconcile_anchor_pending_orders` cleared the pending record/cap without committing a stage; and
* final stage-partial disposition clears pending but retains the existing stage-fill ledger for a later plan, whose next accepted order receives its own cap.

The production bind is only pinned by a source assertion while test helper `_bind_order` manually writes the field. A stronger regression would execute an extracted bind seam rather than mirror it, but direct source tracing found no lifecycle bug.

### Generic fill provenance and log ordering

I AST-compiled the exact `_bt_fill` loop and executed every production next-event source family: `main_signal`; anchor; `scheduled_start:*`; `scheduled_same_bar:*`; residual bull deploy/refill/protective exit; and residual bear deploy/refill/full/stop/protective exit. All 12 emitted their exact source in the generic `[execution] FILL ... source=...` line and all 12 reconciled.

When the generic logger raised, its `finally` reconciled the current fill before propagating the original exception. Thus normal grep order is execution then source-specific reconciliation, and the sweep-A failure-order regression is fixed for the current fill. The repository test is static source slicing rather than a runtime parameterized source/failure test, but the shared choke point itself is complete.

### No normal trading or ledger behavior change

After removing docstrings, the AST bodies of `_turnover_ledger_record`, `_turnover_ledger_rolling`, `_anchor_reinforcement_execution_policy`, and `_anchor_reinforcement_position_headroom` are byte-equivalent in structure to HEAD. The remaining executable additions are read-only diagnostic valuation, pending metadata, logs, and failure ordering. I found no change to:

* anchor or global cap values/headroom arithmetic;
* turnover numerator, booking time, bypass, or ceiling arithmetic;
* cash, quantity, notional-limit, cost-model, or order-submission decisions;
* stage arithmetic on the normal path; or
* continuous trimming/rebalancing behavior.

The bt735390 conclusions therefore remain unchanged: UUUU filled at 19.483069%; later appreciation/relative-NAV movement caused the >20% state; p7 was admitted at 79.8855% of the accepted-request ledger; and later denominator movement, not a new request/fill, moved that ratio above 80% at the fill snapshot.

Sweep C's two separate blockers also remain: the production `prices` argument can omit a current dynamically held name before `_ensure_prices_include_positions`, and active turnover text still leaves `one_way_notional` ambiguous plus contains a stale immediate-mutation explanation. I do not re-count those as D findings.

## Validation

```text
PYTHONPATH=. pytest -q \
  backend/tests/test_anchor_execution_contract.py \
  backend/tests/test_core_sleeve.py \
  backend/tests/test_core_sleeve_live_reachability.py \
  backend/tests/test_core_sleeve_satellite_share.py \
  backend/tests/test_core_sleeve_wiring.py \
  backend/tests/test_portfolio_emulator_fills.py \
  backend/tests/test_simulated_execution.py \
  backend/tests/test_turnover_conviction_bypass.py \
  backend/tests/test_turnover_core_exemption.py \
  backend/tests/test_backtest_broker_fill_wiring.py \
  backend/tests/test_backtest_execution_costs.py
# 212 passed

python3 -m py_compile backend/broker.py \
  backend/tests/test_anchor_execution_contract.py
# PASS

git diff --check -- backend/broker.py \
  backend/tests/test_anchor_execution_contract.py
# PASS
```

GitNexus status was current at `960a469`. `detect-changes --scope unstaged` reported LOW aggregate risk and zero processes, but impact queries for the changed broker helpers returned `not found` / `UNKNOWN` because the index does not map this very large module's top-level helpers. The scope conclusions above therefore use direct source/AST tracing and real-emulator executions.

## Recommendation

**REQUEST CHANGES.** Fix sweep C's incomplete full-map wiring and remaining active turnover terminology, validate invalid numeric marks rather than allowing them to poison NAV, resolve/label same-symbol post-batch semantics, and make the new drift warning failure-safe. Preserve the current frozen-cap lifecycle, generic source choke point, normal log order, and behavior-inert cap/turnover/trim contract.
