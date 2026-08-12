# Independent final-final sweep H — anchor observability after passive current-mark preference

**Date:** 2026-08-12 UTC
**Base:** `960a469fee5776544df1a3bfeb7b84fb3c8eeacf`
**Audited working-tree bytes:** `backend/broker.py` SHA-256 `c6d745eb0e0b60fbc11d4f626d2b63331ce642fa500a2cfee31a2e3f98c9384d`; `backend/tests/test_anchor_execution_contract.py` SHA-256 `99c36ed713375c59f4ff3de20265ca6951d36a77bfcaa9f40279e3617acc03dd`
**Scope:** only the current uncommitted observability/terminology diff after the passive current-mark preference. I did not edit production code, tests, configuration, CSVs, Git state, or backtest state. This requested report is the only artifact written by sweep H.

## Verdict

**PASS — no blockers.** The passive correction is sound: a validated current event mark now overrides the passive fill-limit value in `_last_prices` for diagnostic NAV/weight. All adversarial marketable, passive, partial, same-symbol multi-source, multi-symbol, dynamic-held, future-bar, and invalid-mark cases produced exact expected NAV/weight/provenance, with no false `ANCHOR CAP DRIFT`. Frozen cap state and final stage disposal remained correct. I found no change from `HEAD` to executable gate, ledger, order, fill, or stage arithmetic.

## Adversarial real-emulator results

I exercised the exact AST-extracted broker helpers against real `PortfolioEmulator` and `NextEventExecutionSimulator` instances.

| Case | Exact result | State / warning result |
|---|---|---|
| Marketable gap, multi-asset current map | WIN fill at $150; cash $800; WIN $1,700; OTHER $8,000; NAV **$10,500**; WIN **16.1905%**; `mark_basis=current` | Stage committed; no false CAP warning. A stale OTHER=$100 mark would have falsely produced 26.1538%, but it was not used. |
| Passive fill through resting limit | Fill price remained $100 while current WIN/OTHER marks were $90; cash $800; WIN $1,080; OTHER $3,600; NAV **$5,480**; WIN **19.7080%**; `mark=$90`, `mark_basis=current` | Stage committed; no false CAP warning. `_last_prices[WIN]=$100` did not overwrite the valid current $90 mark. |
| Two-event partial then final marketable fill | First 0.5-share fill: NAV **$10,000**, WIN **10.5000%**. Final 1.5-share fill: NAV **$10,000**, WIN **12.0000%**. | `admission_cap_pct=20` and $50 cumulative fill remained pending after the partial. Final fill committed stage 1 and cleared both pending/cap and filled-stage state. No CAP warning. |
| Same-symbol anchor + `main_signal` batch | Final all-source WIN quantity 18; NAV **$10,000**; WIN **18.0000%**; `tick_snapshot=all_sources`, `mark_basis=current`. | Correct all-source semantics and no false CAP warning. The focused suite also pins the genuine 30% warning and its explicit “not attributed solely to the anchor fill” wording. |
| Two anchor symbols in one batch | WIN gap-down: position $1,200 / NAV $10,000 = **12.0000%**, frozen cap 20%. ALT gap-up: position $1,700 / NAV $10,000 = **17.0000%**, frozen cap 18%. | Distinct plan/order/cap lineage; both stages committed and both pending/cap records cleared; no CAP warning. The WIN accepted $500 request realized only $400 because the decision-time quantity bound remained intact, consistent with accepted-request rather than fill terminology. |

The cap comparator also remained quiet at the exact 20.0000% boundary and fired in the focused real-drift cases only when the exact unrounded weight exceeded the frozen cap.

## Dynamic holdings, future bars, and mark validation

A production-shaped point-in-time lookup held `DYNAMIC` outside the base universe. At 15:00 UTC, its 14:00 hourly bar ($200) was observable and its 15:00 future bar ($999) was not. The captured map was exactly:

```text
{'DYNAMIC': 200.0, 'WIN': 150.0}
```

Composing that actual captured map with a real WIN fill produced NAV **$10,500**, WIN weight **16.1905%**, `mark_basis=current`, and no false CAP warning. Thus held-symbol completion and the future-bar boundary work together at the real call boundary.

I independently ran the current/prior invalid-mark matrix for missing, nonnumeric, `NaN`, positive infinity, zero, and negative values:

* invalid/missing current OTHER + valid prior: **$6,000 / 20.0000%**, `mark_basis=current+prior_fallback:1`, `valuation=ok`, no CAP;
* invalid/missing prior OTHER + valid current $200: **$10,000 / 12.0000%**, `mark_basis=current`, `valuation=ok`, no CAP; and
* invalid/missing current and prior OTHER: `nav=unavailable weight=unavailable valuation=unavailable:ValueError`, no CAP, while the confirmed final fill still committed its stage and cleared pending/cap state.

The focused regressions additionally cover a valuation exception and CAP-warning sink failure; neither can prevent final disposition.

## Source logging and terminology

I compiled and ran the exact production `_bt_fill` loop for all 12 current source families: `main_signal`, anchor, both scheduled variants, three residual-bull variants, and five residual-bear variants. Every generic execution line contained the exact `source=` value and appeared before reconciliation. When the generic sink raised, `finally` still reconciled the current fill with the captured mark map and then re-raised, preserving the previous failure-state behavior.

The active ledger schema, docstrings, comments, and operator messages consistently call the numerator **accepted-order request notional**. The stale `one_way_notional`, “notional traded,” immediate-emulator-mutation, and “backtest fills” descriptions targeted by earlier sweeps are absent. Compatibility identifiers such as `turnover_ledger` and `TURNOVER BUDGET` remain without misstating what is counted.

## Executable comparison with HEAD

After removing docstrings, AST bodies are identical to `HEAD` for:

* `_anchor_reinforcement_execution_policy`;
* `_anchor_reinforcement_position_headroom`;
* `_anchor_reinforcement_turnover_allows`;
* `_turnover_ledger_touch`;
* `_turnover_ledger_record`; and
* `_turnover_ledger_rolling`.

AST multisets for every call to `_anchor_reinforcement_position_headroom`, `_anchor_reinforcement_turnover_allows`, `_submit_portfolio_signal`, `_turnover_ledger_record`, and `execute_signal` are also identical to `HEAD`. All assignments to `_to_notional`, `cash_to_use`, `_mpg_submit_ok`, and `_anchor_turnover_used` are identical. Within `_apply_backtest_confirmed_fill_state`, the full prefix through `remaining`, the final stage-disposition `if`, and all outer source-specific reconciliation after the anchor branch are identical to `HEAD`.

The only production occurrences of `admission_cap_pct` are the accepted-order metadata bind and the diagnostic read. It does not feed admission, headroom, requested cash, order quantity/notional limit, fill price, turnover booking, stage completion, or trimming.

## Validation

```text
PYTHONPATH=. python3 -m pytest -q \
  backend/tests/test_anchor_execution_contract.py \
  backend/tests/test_portfolio_emulator_fills.py \
  backend/tests/test_backtest_broker_fill_wiring.py \
  backend/tests/test_simulated_execution.py \
  backend/tests/test_turnover_conviction_bypass.py \
  backend/tests/test_turnover_core_exemption.py
# 96 passed

PYTHONPATH=. python3 -m pytest -q \
  backend/tests/test_core_sleeve.py \
  backend/tests/test_core_sleeve_live_reachability.py \
  backend/tests/test_core_sleeve_satellite_share.py \
  backend/tests/test_core_sleeve_wiring.py
# 123 passed

PYTHONPATH=.:backend python3 -m pytest -q \
  backend/tests/test_backtest_execution_costs.py \
  backend/tests/test_simulate_allocation.py
# 53 passed
```

**Total focused tests: 272 passed.** The separate adversarial real-emulator/AST harness passed all market, passive, partial, multi-source, multi-symbol, invalid-mark, dynamic/future-bar, source-order, arithmetic-equivalence, and terminology assertions.

AST parsing of both audited files and `git diff --check -- backend/broker.py backend/tests/test_anchor_execution_contract.py` passed.

GitNexus reports the base index current and the aggregate unstaged change mapping as LOW risk (7 files, 12 mapped symbols, zero affected processes). The new collector and large broker helper are not symbol-addressable in the base index, so the direct AST comparison and real-emulator executions above are the authoritative evidence for this diff.

## Recommendation

**APPROVE / PASS.** The latest passive current-mark preference closes the remaining false-positive path without changing executable trading or accounting behavior.
