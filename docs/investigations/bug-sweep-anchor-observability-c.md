# Final bug sweep C — anchor observability

**Date:** 2026-08-10
**Base:** `960a469`
**Audited working-tree files:** `backend/broker.py` SHA-256 `9ad211efd822342bd2809b680fc23209e99929207ce0a72ba7cde8b41422fd4f`; `backend/tests/test_anchor_execution_contract.py` SHA-256 `2c2c93bbee1d3f092891fa872d9657c3f232418cf8f5a70ae266bf5bf6810895`
**Scope:** final uncommitted diff plus the A/B reports. I did not edit code, configuration, Git state, CSVs, or backtest state. This requested report is the only file written by sweep C.

## Verdict

**BLOCK / request changes.** The frozen admission-cap provenance, per-fill source line, multi-ticker reconciliation, and `try/finally` failure ordering work. I found no normal-path change to gates, quantities, ledger arithmetic, or fills. However, the production argument now passed as `current_prices` is not always the full current held-book tick map, so the stale non-fill-mark defect remains reachable. The turnover wording cleanup also still misses active ledger text explicitly identified by sweep B.

## Blockers

### C-1 — HIGH for the observability objective — the only production call passes `prices`, but `prices` is not always the full current held-book map

There is exactly one production call to `_apply_backtest_confirmed_fill_state`, and it now passes `prices` (`broker.py:12683`). That part is complete. The object passed is incomplete for a supported production state:

* At the beginning of the backtest tick, `prices` is built with `_get_prices_at_time(data, symbols, current_time)` (`12632`). `symbols` is the original CLI/base universe.
* The pre-decision fill block adds only pending execution symbols (`12644-12655`). Thus the fill symbol has a current mark.
* Graph-discovered/executable tickers are loaded into `data`/`symbols_for_data` later and can remain held without belonging to the original `symbols` list (`13769-13905`). Non-pending held dynamic names therefore remain absent at fill reconciliation time.
* `_ensure_prices_include_positions`, the function that actually completes the held-book map, does not run until `16826-16829`, after fill reconciliation and immediately before the later snapshot.

The helper consequently falls back to `_last_prices` for a non-pending dynamic holding and recreates the stale-mark false-negative/false-positive from sweeps A/B. The new multi-asset tests call the helper directly with a hand-built complete dict, so they do not exercise this call-site gap.

**Real-emulator, production-shaped reproduction:** I seeded WIN=$1,000, OTHER=$4,000, and cash=$1,000 at prior $100 marks. `data` had current WIN=$100 and OTHER=$50, but the production price initializer was called with base `symbols=["WIN"]`, representing a held dynamic OTHER. After the accepted $200 WIN fill:

```text
production-shaped prices argument: {'WIN': 100.0}
ANCHOR FILL: WIN ... position_value=$1200.00 nav=$6000.00 weight=20.0000% admission_cap=20.00%
ANCHOR CAP DRIFT emitted: false
actual full current map: {'WIN': 100.0, 'OTHER': 50.0}
actual NAV: $4000.00; actual WIN weight: 30.0000%
```

Passing the actual full map to the same real helper produced `nav=$4000.00 weight=30.0000%` and the drift warning. No trading state or arithmetic was affected; the new diagnostic remains wrong in this reachable universe-expansion case.

### C-2 — MEDIUM wording/test blocker — active turnover-ledger language is still incomplete

Sweep B explicitly identified `broker.py:3099`, and it remains unchanged:

```text
Rows are ``[session_date, one_way_notional]``.
```

That active persisted-row description still does not identify the value as accepted-order request notional. The new test rejects several old phrases but does not reject or pin this one, so it passes while the known B item remains.

A second active comment at `16417-16424` still says that after submission the backtest emulator has already mutated positions and contrasts the ledger with one that counted “backtest fills.” That is stale for the promotable next-event equity path: submission returns an unfilled receipt and does not mutate the position. The calculation remains correctly performed before submission and the ledger still records request notional, but the active explanation is not accurate for the current path.

This is documentation/test scope only; I observed no ledger timing, numerator, budget, bypass, or ceiling arithmetic change.

## Adversarial checks that passed

### Multi-fill / multi-ticker lineage

Using one real `PortfolioEmulator.process_price_events` batch with simultaneous accepted WIN and ALT anchor orders produced two fills. Reconciliation committed both stages, cleared both pending records, and retained the distinct frozen caps:

```text
WIN ... nav=$10000.00 weight=15.0000% admission_cap=20.00%
ALT ... nav=$10000.00 weight=15.0000% admission_cap=18.00%
stage map: {'WIN': 1, 'ALT': 1}; pending: {}
```

The full-map overlay normalizes ticker keys and does not leak one ticker's cap or pending record into another. Partial-fill state continues to keep the pending record until a final fill, as in the focused contract tests.

### Frozen admission-cap provenance and safe reads

On the accepted-anchor path, the broker writes `admission_cap_pct` beside `order_id` from the same local `_anchor_policy["max_position_fraction"]` used for admission (`16585-16603`). It no longer re-reads mutable strategy configuration at fill. The focused mutation test admits at 20%, changes `_cached_strategies` to 25%, and still logs/compares against 20%.

I also injected `None` and `"bad"` into the persisted field. Both safely logged `admission_cap=0.00%`, committed the real fill/stage, and cleared pending state; diagnostics did not interrupt reconciliation. Under the normal planner invariant, every accepted, source-tagged anchor receipt with an order ID has the pending dict that receives the frozen field.

### Generic-log failure ordering and old failure behavior

I AST-executed the exact current top-level `_bt_fill` loop with the generic `[execution] FILL` logger raising `BrokenPipeError("stdout closed")`. The `finally` still ran the real anchor reconciliation: `ANCHOR FILL` and `ANCHOR STAGE COMMIT` ran, stage became `{'WIN': 1}`, and pending became `{}`. The original `BrokenPipeError` was then propagated.

Executing the exact base loop under the same injected generic failure also reconciled the current fill and propagated the same exception. Thus the intended execution-before-reconciliation print order is gained without changing the old terminal failure behavior for that fill. As before, propagation stops later loop iterations; the patch does not silently swallow log failures.

### Trading/gate behavior

The executable diff outside tests consists of wording, read-only valuation/logging, the frozen metadata field, and generic-log/reconciliation ordering. It does not change:

* anchor policy values, position-headroom or turnover-ceiling arithmetic;
* global/satellite/core-floor/buying-power gates;
* requested cash, shares, notional limits, fill prices, or forced-trim behavior;
* turnover ledger record timing/numerator; or
* normal stage completion arithmetic.

`get_portfolio_value` is read-only, and valuation exceptions are caught. The C-1 defect is telemetry-only.

## Validation

```text
PYTHONPATH=. python3 -m pytest -q \
  backend/tests/test_anchor_execution_contract.py \
  backend/tests/test_portfolio_emulator_fills.py \
  backend/tests/test_backtest_broker_fill_wiring.py \
  backend/tests/test_simulated_execution.py \
  backend/tests/test_turnover_conviction_bypass.py \
  backend/tests/test_turnover_core_exemption.py
84 passed

PYTHONPATH=. python3 -m pytest -q \
  backend/tests/test_core_sleeve.py \
  backend/tests/test_core_sleeve_live_reachability.py \
  backend/tests/test_core_sleeve_satellite_share.py \
  backend/tests/test_core_sleeve_wiring.py
123 passed

AST syntax parse of both audited files: PASS
git diff --check -- backend/broker.py backend/tests/test_anchor_execution_contract.py: PASS
```

GitNexus status was current at `960a469`. `detect-changes --scope unstaged` reported LOW aggregate risk/zero flows, but the index still does not map the large broker helpers: upstream impact/context for `_apply_backtest_confirmed_fill_state`, `_turnover_ledger_record`, and `_anchor_reinforcement_position_headroom` returned `not found` / `UNKNOWN`. The call-site and state-radius conclusions above therefore come from direct AST/source tracing and real-emulator reproduction, not the incomplete graph result.

## Recommendation

**REQUEST CHANGES.** Complete the current held-position mark map before the pending-fill reconciliation (or capture an equivalent immutable complete tick map), and pin that production wiring with a dynamic/non-base held-symbol test. Finish the remaining active accepted-request ledger wording, including the persisted row description and stale next-event submission comment, without changing any gate or ledger arithmetic.
