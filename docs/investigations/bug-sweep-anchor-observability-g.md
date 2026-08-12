# Final-final bug sweep G — anchor observability after passive-mark correction

**Date:** 2026-08-12 UTC
**Base:** `960a469fee5776544df1a3bfeb7b84fb3c8eeacf`
**Audited working-tree bytes:** `backend/broker.py` SHA-256 `c6d745eb0e0b60fbc11d4f626d2b63331ce642fa500a2cfee31a2e3f98c9384d`; `backend/tests/test_anchor_execution_contract.py` SHA-256 `99c36ed713375c59f4ff3de20265ca6951d36a77bfcaa9f40279e3617acc03dd`
**Scope:** the current uncommitted broker/test diff, reports E/F, and every C/D blocker. I did not edit production code, tests, configuration, CSVs, Git state, or backtest state; this requested report is the only artifact written by sweep G.

## Verdict

**PASS — no blockers.** F-1 is fixed. The real passive-anchor case now uses the current event mark for diagnostic NAV/weight, emits the exact `$5,480 / 19.7080%` observation, and emits no false cap-drift warning. All C/D remediations remain intact. I found no change to a trading gate, sizing formula, fill, turnover arithmetic, stage-completion calculation, or trim/rebalance behavior.

## Required passive-fill reproduction — PASS

I independently AST-executed the repository's exact current `_backtest_fill_snapshot_marks` and `_apply_backtest_confirmed_fill_state` (the broker module is intentionally not import-safe) against a real `PortfolioEmulator`, `NextEventExecutionSimulator`, `SimulationPriceEvent`, and real `process_price_events` path.

Setup:

* prior book: WIN 10 @ $100, OTHER 40 @ $100, cash $1,000;
* accepted passive anchor request: $200 at the resting/decision limit of $100;
* current event marks: WIN=$90 and OTHER=$90; and
* frozen admission cap: 20%.

Observed:

```text
snapshot_marks= {'WIN': 90.0, 'OTHER': 90.0}
fill_price= 100.0 cash= 800.0 positions= {'WIN': 12.0, 'OTHER': 40.0}
independent_current_nav=$5480.00 independent_WIN_weight=19.7080%
ANCHOR FILL: WIN ... fill=$200.00 ... quantity=12.00000000
mark=$90.000000 position_value=$1080.00 nav=$5480.00 weight=19.7080%
mark_basis=current valuation=ok admission_cap=20.00%
ANCHOR STAGE COMMIT: WIN ...
```

There was **no** `ANCHOR CAP DRIFT: WIN` line. The collector retained the real current quote, while the simulator correctly executed the passive order at its $100 limit. In the reconciler, validated `_last_prices` are loaded first and validated `current_prices` second, so the current event map wins for telemetry. The passive fix does not alter the legacy `mark/current_value/remaining` stage calculation that precedes the observability block; it is diagnostic-only, as required for behavior neutrality.

The added regression `test_passive_anchor_uses_current_quote_mark_not_resting_fill_limit` pins the exact mark, position value, NAV, weight, `mark_basis=current`, and absence of drift.

## C/D and E/F recheck

### C-1 / complete current held-book map — closed

`_backtest_fill_snapshot_marks` enumerates the real pre-fill holdings, resolves them through `_get_prices_at_time(data, held, current_time)` without the main-loop cursor shortcut, then overlays explicit base/pending-event `prices`. Valid finite positive point-in-time marks therefore include non-base discovery holdings; a newly bought pending symbol is supplied by its pending event. Production captures this map before `process_price_events` mutates the whole batch and passes the same immutable diagnostic map to every returned fill.

The dynamic-held regression and source-wiring assertions passed. The old production state in which a held non-base name fell back to a stale prior mark is no longer reachable through this call site.

### C-2 / turnover wording — closed

The active persisted schema is now `[session_date, accepted_order_request_notional]`. Record/rolling docstrings, age-out comments, binding/bypass/block messages, gate comments, and post-submission booking comments consistently describe an accepted-order request/submission budget rather than realized fill turnover. The stale immediate-mutation/backtest-fill explanation is gone. Stable compatibility names such as `turnover_ledger` and `TURNOVER BUDGET` remain, without misstating the numerator.

This is wording-only. After stripping docstrings, AST bodies for all of the following are identical to `HEAD`:

* `_turnover_ledger_touch`
* `_turnover_ledger_record`
* `_turnover_ledger_rolling`
* `_anchor_reinforcement_execution_policy`
* `_anchor_reinforcement_position_headroom`
* `_anchor_reinforcement_turnover_allows`

### D-1 / mark validation and unavailable paths — closed

Both the collector and reconciler accept only finite positive marks. A bad current mark cannot overwrite a usable prior mark. The reconciler requires every nonzero holding to have a valid final mark and validates NAV, position value, and weight. Missing current/prior marks, nonnumeric/`NaN`/infinite/zero/negative inputs, valuation exceptions, and invalid results use explicit telemetry:

```text
nav=unavailable weight=unavailable valuation=unavailable:<ExceptionType>
```

They do not fabricate `$0.00 / 0.0000%`, do not emit cap drift, and do not interrupt final stage disposition. When a prior mark is legitimately used, provenance is `mark_basis=current+prior_fallback:<count>`; the corrected passive case is truthfully `mark_basis=current`.

### D-2 / batch semantics — closed

`process_price_events` applies the complete fill batch before returning. The source-specific observation is therefore explicitly labelled `tick_snapshot=all_sources`; a same-symbol anchor plus non-anchor batch reports the final all-source quantity/weight. Its warning says all same-tick sources are included and is not attributed solely to the anchor fill. Multi-ticker fills retain separate pending order/plan/frozen-cap lineage while sharing the same valid tick NAV snapshot.

### D-3 and A-3 / logger failures — closed for the changed paths

The new `ANCHOR CAP DRIFT` sink is best-effort. The runtime failure-injection regression where `ANCHOR FILL` succeeds and only the drift sink raises still commits the stage and clears pending state.

I also AST-executed the exact current production `for _bt_fill in _bt_fills` loop. Normal order was generic execution log then reconciliation. When the generic execution logger raised `BrokenPipeError`, the `finally` still reconciled the current fill with `_bt_fill_prices`, after which the same exception propagated. This preserves the old failure-path state behavior while giving logs causal execution-before-reconciliation order.

The older primary `ANCHOR FILL` log itself remains an inherited failure point if both of its underlying sinks fail; this existed before the observability patch and the passive correction adds no new sink or state dependency there. It is not a regression or blocker for this behavior-neutral patch.

### Frozen cap and source/mark provenance — closed

The accepted anchor path freezes `admission_cap_pct` beside the accepted `order_id` from the same already-computed local `_anchor_policy["max_position_fraction"]`. Reconciliation reads only that pending value; mutable strategy configuration is not re-read. The field has no other production consumer and leaves with pending state on completion/block/expiry.

The generic execution line includes the exact `source=` value before source-specific reconciliation. Anchor fill lines distinguish execution `fill=$...` from current diagnostic `mark=$...`; this is the distinction that now makes passive fills correct.

## Behavior neutrality

Direct source/diff tracing and the AST comparisons found no changed:

* anchor/global/satellite/core-floor/buying-power gate predicate;
* cap/headroom/turnover ceiling arithmetic;
* cash, quantity, request-notional, cost-model, or fill-price calculation;
* turnover booking condition, timing, numerator, rolling sum, bypass, or ceiling comparison;
* stage completion tolerance or `remaining` calculation; or
* forced trim/rebalance behavior.

The only executable additions are read-only mark capture/valuation, logs/failure ordering, and accepted-order metadata used only by telemetry. `admission_cap_pct` occurs in production only at bind and diagnostic read sites. The bt735390 diagnosis therefore remains unchanged.

## Validation

```text
PYTHONPATH=.:backend python3 -m pytest -q \
  backend/tests/test_anchor_execution_contract.py \
  backend/tests/test_portfolio_emulator_fills.py \
  backend/tests/test_backtest_broker_fill_wiring.py \
  backend/tests/test_backtest_execution_costs.py \
  backend/tests/test_simulated_execution.py \
  backend/tests/test_turnover_conviction_bypass.py \
  backend/tests/test_turnover_core_exemption.py
# 101 passed

PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_simulate_allocation.py
# 48 passed

PYTHONPATH=.:backend python3 -m pytest -q \
  backend/tests/test_core_sleeve.py \
  backend/tests/test_core_sleeve_live_reachability.py \
  backend/tests/test_core_sleeve_satellite_share.py \
  backend/tests/test_core_sleeve_wiring.py
# 123 passed
```

**Total focused tests: 272 passed.**

```text
python3 -m py_compile backend/broker.py backend/tests/test_anchor_execution_contract.py
# PASS

git diff --check -- backend/broker.py backend/tests/test_anchor_execution_contract.py
# PASS
```

The separate no-file passive harness passed all exact numeric/log assertions above. The exact-loop generic logger harness also passed normal ordering and reconcile-then-reraise behavior.

GitNexus is current at the base commit. `detect-changes --scope unstaged` reports 7 files / 12 mapped symbols, zero affected processes, and aggregate **LOW** risk. As in E/F, the graph cannot map the large broker helper (`_apply_backtest_confirmed_fill_state` impact is `UNKNOWN`; the new collector is absent from the base index), so direct AST/source tracing and the real-emulator reproductions are the authoritative evidence. GitNexus does confirm that real `PortfolioEmulator.process_price_events` calls `process_quote` for pending symbols.

## Final recommendation

**APPROVE / PASS.** F-1 is corrected with truthful current-mark provenance and exact passive-fill NAV/weight, every C/D blocker remains closed, and no trading or accounting regression was detected.
