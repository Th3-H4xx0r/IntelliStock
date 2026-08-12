# Definitive post-edit bug sweep F — anchor observability/turnover terminology

**Date:** 2026-08-10
**Base:** `960a469fee5776544df1a3bfeb7b84fb3c8eeacf`
**Audited working-tree bytes:** `backend/broker.py` SHA-256 `c3f273e0ff059fd9aad81184ee8344f9c17d2c634b58326c3c129979be5c7594`; `backend/tests/test_anchor_execution_contract.py` SHA-256 `6cec44bb27f00b5cdc83314ca7614f7ea0af7aaa494c065847d18ebe701990b0`
**Scope:** the final uncommitted broker/test observability and terminology diff after reading sweeps A-D. I did not edit production code, tests, configuration, Git state, CSVs, or a backtest. This report is the only repository artifact written by sweep F.

## Verdict

**REQUEST CHANGES / BLOCK the observability patch.** The A-D fixes work for marketable next-event orders: dynamic held symbols are now completed into the mark map, invalid marks fall back safely, missing/failed valuations say `unavailable`, the admission cap is frozen on the accepted order, same-symbol batches are explicitly labelled all-source tick snapshots, and the new drift warning cannot prevent stage disposal. Turnover terminology is now accurate, and I found no trading-gate or arithmetic change.

I found one remaining **MEDIUM telemetry-correctness defect**: on the repository's supported opt-in passive execution path, `_apply_backtest_confirmed_fill_state` discards the valid current event mark for the anchor symbol and replaces it with the passive order's fill limit. It can consequently fabricate `ANCHOR CAP DRIFT` while claiming `mark_basis=current`. This does not change cash, quantity, fills, stage arithmetic, turnover, or trimming, and it does not alter the bt735390 diagnosis, but it breaks the purpose of the new cap diagnostic.

## F-1 — MEDIUM — passive anchor fills overwrite the current mark with the resting limit and can emit false cap drift

The production mark collector is correct in this case. `_backtest_fill_snapshot_marks` returns the pending event mid from `prices` after completing held discovery symbols (`broker.py:11114-11147`, production call `12762-12763`). The error is in the consumer:

1. Passive execution is a supported strategy-document option, configured by `passive_execution_enabled` at broker startup (`broker.py:9422-9435`). There is no exclusion between it and anchor execution.
2. `PortfolioEmulator.execute_signal` sets a passive order's `limit_price` to the decision price (`portfolio_emulator.py:1537-1562`).
3. When the market comes through that price, the simulator deliberately fills at the limit, not at the current quote mid (`simulated_execution.py:503-540`). Its passive spread/slippage fields are zero.
4. `PortfolioEmulator.apply_fill` therefore leaves `_last_prices[fill.symbol]` at the fill limit: `_fill_mid` has zero costs to subtract (`portfolio_emulator.py:1127-1141,1198-1205`).
5. Although the new full map contains the real current event mark, the telemetry block takes `mark` from `_last_prices` and unconditionally executes `fill_marks[symbol] = telemetry_mark` (`broker.py:11247-11249,11289-11293`). It also adds the symbol to `current_symbols`, so the resulting false value is labelled `mark_basis=current`.

### Exact real-emulator reproduction

I enabled `PortfolioEmulator.set_passive_execution(True)`, used a real `NextEventExecutionSimulator`, and ran both new broker helpers via the same AST extraction used by the repository test. Initial state was WIN 10 @ $100, OTHER 40 @ $100, and $1,000 cash ($6,000 NAV). A $200 anchor buy rested at the $100 decision price. On the next event WIN's mid was $90 and OTHER's current mark was $90. The passive order filled at its $100 limit.

The new collector correctly returned:

```text
snapshot marks before fill {'WIN': 90.0, 'OTHER': 90.0}
```

The real current tick state after the fill is:

```text
cash       = $800
WIN        = 12 * $90 = $1,080
OTHER      = 40 * $90 = $3,600
NAV        = $5,480
WIN weight = 19.7080%
```

The current helper instead discarded WIN=$90, valued it at the $100 fill limit, and emitted:

```text
ANCHOR FILL: WIN ... tick_snapshot=all_sources quantity=12.00000000
mark=$100.000000 position_value=$1200.00 nav=$5600.00 weight=21.4286%
mark_basis=current valuation=ok admission_cap=20.00%
ANCHOR CAP DRIFT: WIN tick-snapshot weight=21.4286% >
decision-time admission cap=20.00% ...
```

Thus a genuine 19.7080% current weight becomes a false 21.4286% warning. Marketable orders do not expose this because their `_fill_mid` reconstructs the quote mid, which is why all current tests pass. The new test suite has a market-fill mid test and current-map tests, but no passive anchor fill; it never supplies a valid current anchor mark that differs from `_last_prices[anchor]`.

**Minimal required correction:** for the read-only NAV/weight diagnostic, prefer a validated current-map mark for the fill symbol and use `_last_prices`/fill-derived mark only as fallback. Keep the existing stage calculation untouched if behavior neutrality is required. Add a passive anchor regression that asserts exact current NAV/weight and no false drift. The fallback provenance label should also reflect whether the anchor mark really came from the current map.

## A-D remediation recheck

Everything else requested by the prior sweeps now passes:

* **Dynamic/discovery holdings:** the helper enumerates pre-fill held positions and resolves them from the same point-in-time `data`; explicit configured/pending-event `prices` overlay last. Executing the actual extracted `_get_prices_at_time` with a held dynamic name returned its latest observable $50 mark, and when its newer bar was not yet available it correctly retained the earlier $100 observable mark. New buy symbols not present pre-fill are covered by `_bt_pending_events` in `prices`; fully sold pre-fill names are harmless extras.
* **Invalid/missing marks:** only finite positive marks enter either overlay. Invalid current values do not erase valid prior marks. A held position with neither current nor prior mark reports `nav=unavailable weight=unavailable`; valuation exceptions do likewise and stage disposal continues.
* **Partial fills/cap lifecycle:** a real two-event partial/final anchor fill retained `admission_cap_pct=20.0` after the first 0.5-share partial, accumulated $50 then $200 of stage fills, used the same frozen cap on the final event, committed once, and cleared pending/cap state. Final partial-stage, expiry, and next-plan replacement behavior remains as described by sweep D.
* **Multi-ticker fills:** one real `process_price_events` batch containing WIN and ALT anchor fills kept plan/order/cap lineage separate, logged both against the same $10,000 all-source tick NAV, committed both stages, and cleared both pending records.
* **Same-symbol multi-source fill:** the repository test now truthfully labels the already-applied post-batch position `tick_snapshot=all_sources`; the warning explicitly says all same-tick sources are included and it is not attributed solely to the anchor fill.
* **Failure safety:** valuation is broadly caught; the new cap-drift sink is best-effort; and compiling/executing the exact production `_bt_fill` AST showed that a generic logger `BrokenPipeError` still runs reconciliation in `finally` and then propagates the original error. The pre-existing `ANCHOR FILL` sink remains capable of stopping later stage logging only if both logger sinks fail, but this diff did not add that call.
* **Source families:** executing the exact production fill-loop AST for all 12 current source families (`main_signal`, anchor, both scheduled variants, three residual-bull variants, and five residual-bear variants) emitted the exact `source=` value before reconciliation in every case. No in-repo parser consumes the tail of this line.
* **Parsers:** `scripts/simulate_allocation.py`'s fill regex stops after its required price prefix and the turnover regexes match stable prefixes, so the appended source and wording do not break them. An unknown external regex anchored at `model=...$` would need updating, as already noted by sweep A.
* **AST test harness:** `math`, `_get_prices_at_time`, and both new helpers are present in the extraction namespace; `broker.py` is never imported. The production wiring assertions point at the only helper call. The important remaining coverage hole is the passive fill-mark divergence above; the dynamic test also stubs `_get_prices_at_time`, although direct execution of the actual lookup passed.

## Behavior-neutrality and turnover terminology

After stripping docstrings, the AST bodies of `_turnover_ledger_touch`, `_turnover_ledger_record`, `_turnover_ledger_rolling`, `_anchor_reinforcement_execution_policy`, and `_anchor_reinforcement_position_headroom` are structurally identical to `HEAD`. The diff changes no cap percentage, headroom formula, turnover numerator/denominator, record timing, bypass/ceiling comparison, requested cash, shares, notional limit, fill cost, or forced-trim/rebalance behavior.

The only new persistent value is `pending["admission_cap_pct"]`, copied from the same validated local policy used to admit/size the accepted anchor order. Direct search found no consumer of extra pending-record keys other than the new fill diagnostic; the field leaves with pending state on final fill/block/expiry.

The turnover cleanup now accurately names the ledger as **accepted-order request notional** rather than realized trading in the persisted-row description, central record/rolling docstrings, calendar-backstop commentary, active binding/bypass/block logs, gate commentary, and post-submission book comment. The test rejects all misleading forms identified by sweeps B/C. Stable names such as `turnover_ledger` and `TURNOVER BUDGET` remain compatibility labels, while the active operator messages state the numerator explicitly. No arithmetic or booking behavior changed.

The bt735390 conclusions remain unchanged: p7 was admitted at 79.8855% of the accepted-request ledger and filled to 19.483069%; later denominator/mark movement caused the broader 80%/20% observations. Passive execution was not involved in that reconstruction.

## Validation

```text
PYTHONPATH=. python3 -m pytest -q \
  backend/tests/test_anchor_execution_contract.py \
  backend/tests/test_portfolio_emulator_fills.py \
  backend/tests/test_backtest_broker_fill_wiring.py \
  backend/tests/test_backtest_execution_costs.py \
  backend/tests/test_simulated_execution.py \
  backend/tests/test_turnover_conviction_bypass.py \
  backend/tests/test_turnover_core_exemption.py
# 100 passed

PYTHONPATH=. python3 -m pytest -q backend/tests/test_simulate_allocation.py
# 48 passed

PYTHONPATH=. python3 -m pytest -q \
  backend/tests/test_core_sleeve.py \
  backend/tests/test_core_sleeve_live_reachability.py \
  backend/tests/test_core_sleeve_satellite_share.py \
  backend/tests/test_core_sleeve_wiring.py
# 123 passed

python3 -m py_compile backend/broker.py \
  backend/tests/test_anchor_execution_contract.py
# PASS

git diff --check -- backend/broker.py \
  backend/tests/test_anchor_execution_contract.py
# PASS
```

The 271 pytest cases above are non-overlapping. Additional `/tmp`-only adversarial scripts exercised real partial/final fills, a real two-anchor batch, the actual dynamic availability lookup, the exact fill-loop AST/source families and logger failure, and the passive false-positive reproduction.

GitNexus was current at `960a469`. Its query found the anchor tests but no execution flow; context/impact could not index either new helper (`not found`, risk `UNKNOWN`) because of the large broker module. `detect-changes --scope unstaged` reported LOW aggregate risk/zero affected processes, so the conclusions above come from direct AST/call-site tracing and real-emulator execution rather than treating the incomplete graph as proof.
