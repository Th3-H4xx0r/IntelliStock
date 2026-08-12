# Definitive post-fix sweep E — anchor observability

**Date:** 2026-08-11
**Base:** `960a469`
**Audited final working-tree bytes:** `backend/broker.py` SHA-256 `c3f273e0ff059fd9aad81184ee8344f9c17d2c634b58326c3c129979be5c7594`; `backend/tests/test_anchor_execution_contract.py` SHA-256 `6cec44bb27f00b5cdc83314ca7614f7ea0af7aaa494c065847d18ebe701990b0`
**Scope:** final uncommitted versions of those two files, prior sweeps C/D, actual pending-fill production ordering, and adversarial real-`PortfolioEmulator` executions. I did not edit production code, tests, configuration, CSVs, or Git state; this requested report is the only file written by sweep E.

## Verdict

**PASS — no blockers.** Every C/D blocker is fixed in the final bytes. The additions remain observability/metadata/wiring only: I found no changed trade, gate, position-headroom, turnover-ledger, quantity, fill, or stage-completion arithmetic.

## C/D blocker closure

### C-1 — fixed: non-base held names are completed from point-in-time data at the pending-fill call site

The production order is now explicit at `backend/broker.py:12737-12793`:

1. the tick starts with the base-universe `prices` map;
2. exact pending execution events are resolved and overlaid into it;
3. `_backtest_fill_snapshot_marks(portfolio_emulator, prices, data, current_time)` captures the diagnostic mark map **before** `process_price_events` mutates the book;
4. the emulator applies the entire event batch; and
5. every returned fill is reconciled with that captured map.

The new helper (`11114-11147`) asks the real portfolio for every currently held symbol and calls `_get_prices_at_time(data, held, current_time)` without the cursor shortcut. It therefore uses the same bar-availability boundary as the production backtest while no longer depending on the original CLI/base universe. It then overlays pending/base marks and retains only finite positive values. A newly bought symbol not in the pre-fill holdings is still covered because every fill symbol is a pending execution symbol and its exact event mark was overlaid before capture.

I replayed the exact production sequence with a real next-event emulator, base universe `['WIN']`, held non-base `DYNAMIC`, and point-in-time bars `DYNAMIC=$50` plus a not-yet-visible future `$999` bar. The captured map was `{'WIN': 100.0, 'DYNAMIC': 50.0}`, not the future mark. After the accepted $200 WIN fill, the real reconciliation emitted `nav=$4000.00 weight=30.0000% mark_basis=current`, warned for real cap drift, committed stage 1, and cleared pending state. This is the former C false-negative reproduction, now corrected.

The test suite pins both halves: the helper must query all held names (`test_dynamic_held_symbol_is_added_to_production_fill_mark_map`), and the source-wiring test requires `_bt_fill_prices` to be captured with that helper and passed to the sole fill-reconciliation call.

### C-2 — fixed: persisted schema and submission terminology match the actual ledger

The active persisted row contract now says:

```text
[session_date, accepted_order_request_notional]
```

`_turnover_ledger_record` consistently describes a submission budget: requested notional is recorded after an accepted/submit-successful order, before a later partial fill, expiry, or venue rejection. The main submission comment at `16526-16531` now accurately distinguishes the legacy immediate emulator from next-event equity receipts and says the shared path books only an accepted request. The rolling docstring and operator budget/bypass/block logs use accepted-order request notional rather than realized/traded-fill wording. The new terminology test pins the corrected phrases and rejects the specific stale C/D text.

This was wording only. After removing docstrings, the AST bodies of `_turnover_ledger_touch`, `_turnover_ledger_record`, and `_turnover_ledger_rolling` are identical to `HEAD`.

### D-1 — fixed: invalid marks safely fall back or make valuation explicitly unavailable

Both mark ingestion layers validate `math.isfinite(mark) and mark > 0.0`:

* `_backtest_fill_snapshot_marks` filters current point-in-time/base/pending marks; and
* `_apply_backtest_confirmed_fill_state` filters both `_last_prices` and the captured current map before valuation.

A bad current mark cannot overwrite a valid prior mark. Every nonzero held position must have a valid final mark; missing marks, invalid fill marks, valuation exceptions, non-finite/nonpositive NAV, or invalid weights enter the caught unavailable path. The log then prints `nav=unavailable weight=unavailable valuation=unavailable:<ExceptionType>` — never a fabricated `$0.00 / 0.0000%` observation — and suppresses CAP DRIFT because there is no valid weight.

Adversarial real-emulator matrices passed for missing, nonnumeric, `NaN`, infinity, zero, and negative current marks: all six retained the prior $100 mark and emitted `nav=$6000.00 weight=20.0000% mark_basis=current+prior_fallback:1 valuation=ok`. Repeating all six with no valid current **or** prior mark emitted unavailable (not 0/0), still committed the stage, and cleared pending. A real `get_portfolio_value` `RuntimeError` likewise emitted `valuation=unavailable:RuntimeError` and did not disrupt disposition.

Focused regressions cover the invalid-current matrix, missing-current-and-prior case, and valuation exception.

### D-2 — fixed by explicit all-source tick-snapshot semantics

`PortfolioEmulator.process_price_events` applies every fill through `process_quote/apply_fill` before returning the batch (`portfolio_emulator.py:1306-1328`). The reconciliation comment now states that the diagnostic is not a causal reconstruction of one fill. `ANCHOR FILL` labels it `tick_snapshot=all_sources`, and `ANCHOR CAP DRIFT` says that all same-tick sources are included, the warning is diagnostic only, and it is **not attributed solely to the anchor fill**.

A real same-symbol batch (initial WIN 10 shares, accepted anchor $200 plus `main_signal` $600, both filled at $100) emitted the truthful final tick state `quantity=18`, `weight=30%`. The drift line contained both the all-source warning and non-attribution language. No forced trim or extra order occurred. The dedicated same-symbol regression pins these semantics.

### D-3 — fixed: CAP DRIFT logging cannot block final disposition

The new drift `_log` is wrapped in `try/except Exception` immediately before final-fill disposition (`11350-11367`). With a logger that succeeds for `ANCHOR FILL`, raises `BrokenPipeError` only for `ANCHOR CAP DRIFT`, and records subsequent calls, the real helper still emitted `ANCHOR STAGE COMMIT`, set `{'WIN': 1}`, and cleared pending. The focused failure-injection test covers this exact path.

## Production ordering and behavior-inertness

The generic execution line remains before source-specific reconciliation and includes the exact fill source. Its `finally` guarantees current-fill reconciliation if the generic logger fails. The point-in-time mark map is captured before the all-fill mutation and reused read-only for the returned batch. `_reconcile_anchor_pending_orders` runs after all returned fills are reconciled.

The observability valuation calls the emulator's read-only `get_portfolio_value`; every valuation failure is contained. `admission_cap_pct` is frozen beside the accepted `order_id` from the same already-computed local policy and is later read only for telemetry. It does not feed admission, sizing, fill, stage, or trim decisions.

AST comparison of current `broker.py` with `HEAD`, excluding docstrings, found all of these gate/ledger bodies unchanged:

* `_anchor_reinforcement_execution_policy`
* `_anchor_reinforcement_position_headroom`
* `_anchor_reinforcement_turnover_allows`
* `_turnover_ledger_touch`
* `_turnover_ledger_record`
* `_turnover_ledger_rolling`

The only changed existing top-level function body is `_apply_backtest_confirmed_fill_state`, where the diff inserts read-only valuation/telemetry after the unchanged `remaining` calculation; `_backtest_fill_snapshot_marks` is new. The top-level loop diff adds the mark capture/pass-through, source log/failure ordering, and frozen metadata field. Direct diff review found no changed gate predicate, cash/shares/notional calculation, turnover record condition/value, cost model, fill application, or stage completion tolerance/arithmetic.

## Validation

```text
PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_anchor_execution_contract.py
36 passed

PYTHONPATH=.:backend python3 -m pytest -q \
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
223 passed

python3 -m py_compile backend/broker.py \
  backend/tests/test_anchor_execution_contract.py
PASS

git diff --check -- backend/broker.py \
  backend/tests/test_anchor_execution_contract.py
PASS
```

An additional no-file adversarial harness AST-executed the exact current broker helpers with real `PortfolioEmulator`, `NextEventExecutionSimulator`, typed point-in-time price events, and the production capture/process/reconcile ordering. It passed the dynamic-held/future-bar boundary, invalid-current matrix, invalid-prior matrix, valuation exception, same-symbol batch, and drift-log failure cases described above.

GitNexus is current at `960a469`. `detect-changes --scope unstaged` reported LOW aggregate risk and zero mapped flows. As in C/D, the index does not map the large broker module's new helpers (`context`/`impact` return not found/UNKNOWN); direct AST/source tracing and the real-emulator executions above are the authoritative evidence for those paths. GitNexus does confirm that `PortfolioEmulator.process_price_events` delegates to `process_quote` over pending symbols, consistent with the directly inspected production source.

## Recommendation

**APPROVE / PASS.** The C/D observability and terminology blockers are closed with no detected trade, gate, ledger, fill, or stage-math regression.
