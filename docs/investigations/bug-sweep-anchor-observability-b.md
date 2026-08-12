# Post-edit bug sweep B — anchor observability

**Date:** 2026-08-10
**Audit base:** `960a469` (the deployed code for stopped bt `735390`)
**Audited working-tree files:** `backend/broker.py` SHA-256 `be6a7c70dbf2869727460774a8e58591f267a0dca0f36a592abd06f9fd8de250`; `backend/tests/test_anchor_execution_contract.py` SHA-256 `7369d243ae961d27218f8b371fa847bc34a48b7cc57c455b8a870b0afdabcf20`
**Verdict:** **BLOCK / request changes.** The gate/accounting behavior remains unchanged and the cap-contract clarification is directionally right, but the new `ANCHOR CAP DRIFT` calculation can emit a false warning (or miss a real one) in a multi-position book. The turnover terminology cleanup is also not complete, and its new test cannot detect that incompleteness.

No code, configuration, Git state, deployed document, or backtest state was changed by this audit. This report is the only repository file written.

## Scope and bt 735390 facts used

The stopped run establishes the distinction this patch is meant to preserve:

* The runtime turnover numerator was **accepted/submitted request dollars**, booked on submission, not realized fills. Before p7 it was `$5,122.96`; after accepting the `$155.43` p7 request it was `$5,278.39`. Its ratio was 79.8855% at admission, then 80.3282% at the next fill snapshot solely because NAV fell. Realized governed fills were 78.9648% at that snapshot.
* The p7 anchor order was admitted from 17.6477% UUUU weight and filled to 19.4831%. UUUU crossed 20% one snapshot later without a quantity change and reached 21.6116%. Thus the executable cap is a **decision-time buy-admission/current-mark cap**, not a fill-time invariant and not a continuous trim/rebalance rule.
* Base `960a469` printed the correlated `ANCHOR FILL` before the generic `[execution] FILL BUY`, and the generic line omitted `source=`. The working diff reverses the print order and adds visible source provenance.

## PASS findings

### 1. No trading gate or order-sizing arithmetic changed

The diff changes comments/log wording, adds post-fill diagnostics, reorders two log/reconciliation calls, and adds tests. It does **not** change:

* `_anchor_reinforcement_execution_policy` cap/turnover values;
* `_anchor_reinforcement_position_headroom` arithmetic;
* `_anchor_reinforcement_turnover_allows` or the accepted-request ledger;
* satellite, core-floor, buying-power, or broker single-position gates;
* order quantity/notional-limit construction; or
* stage completion arithmetic.

The anchor lane remains default-OFF/backtest-only and the existing p7 admission result would be unchanged. This is consistent with the objective constraint that new behavior remain scoped/default-OFF and with the cap audit's recommendation not to smuggle in a continuous trim policy.

### 2. Decision-time versus fill-time/continuous semantics are now substantially clearer

The edited policy/headroom comments correctly say:

* the percentage is a decision-time buy-admission cap;
* a next-event gap can take the eventual fill snapshot through it; and
* later mark-to-market appreciation is not continuously trimmed.

That exactly describes the executable contract at `960a469`. It does not rewrite bt 735390's preregistered whole-run failure: its terminal weight condition still failed, while the p7 order-time/fill-time diagnosis remains that the order itself did not overfill.

### 3. The price-gap quantity assertion is mathematically correct

In `test_next_event_gap_reports_cap_drift_without_forced_trim`, the accepted `$200` buy is initially sized from a `$100` decision mark. At the `$150` next quote, the simulator's `$200` notional limit reduces the executable increment to `200 / 150 = 1.333333...`, so the asserted total `10 + 200/150` shares is correct. The assertion demonstrates that no new weight clamp or forced trim was introduced at fill.

The test is nevertheless incomplete as a telemetry test; see blocker 1.

### 4. Generic fill provenance and print order are fixed as intended

The working loop now emits:

1. `[execution] FILL ... source=<actual SimulationFill.source>`
2. source-specific reconciliation (`ANCHOR FILL`, stage result, mismatch/orphan diagnostics)

This provides the literal visible source tag absent from bt 735390 and puts execution before reconciliation in grep order. `source` is already carried by `SimulationFill` and the trade record, so this is an observability change rather than invented provenance.

### 5. Main operator-facing turnover messages no longer claim realized fills

The changed `TURNOVER BUDGET BINDING`, `TURNOVER BYPASS CEILING`, and `TURNOVER BUDGET BLOCK` messages now explicitly say `accepted-order request notional`. That is materially more accurate than bt 735390's `NAV traded` language and preserves the existing conservative submission budget.

## BLOCKERS

### Blocker 1 — `ANCHOR CAP DRIFT` does not use the real current fill-snapshot marks

**Severity: HIGH for the stated observability objective; no trading-gate change.**

`_apply_backtest_confirmed_fill_state` constructs `fill_marks` only from `portfolio_emulator._last_prices`, then replaces the fill symbol's mark. In the real loop this helper runs near broker lines 12656-12675. The loop already has the current tick's `prices`, including `_bt_pending_events`, but does not pass them to the helper. `_last_prices` is refreshed for the whole book only later by `save_portfolio_snapshot` (PortfolioEmulator lines 903-926; broker snapshot path near 16775/16828). Therefore non-fill holdings are valued at the previous snapshot's marks, or omitted if no prior snapshot populated them.

That means the line labelled `fill-snapshot weight` is not reliably the emitted fill-snapshot weight. It can:

* emit a false `ANCHOR CAP DRIFT` if other holdings rose and current NAV is higher than stale-mark NAV;
* miss a real crossing if other holdings fell and current NAV is lower than stale-mark NAV; or
* grossly overstate weight by omitting held names absent from `_last_prices`.

I reproduced a false positive with the real emulator and the AST-extracted real helper, without editing the repository:

* decision state: `$6,000` NAV; `WIN` 10 shares at `$100`; `OTHER` 40 shares at `$100`; `$1,000` cash;
* accepted anchor request: `$200`, exactly filling the 20% decision-mark headroom;
* next fill event: `WIN=$150`; same-tick actual mark `OTHER=$200`;
* actual current NAV using the tick marks: `$10,500`; actual WIN weight: **16.1905%**, below 20%;
* helper used stale `OTHER=$100`: logged NAV `$6,500`, weight **26.1538%**, and emitted `ANCHOR CAP DRIFT`.

The exact emitted warning was:

```text
ANCHOR CAP DRIFT: WIN fill-snapshot weight=26.1538% > decision-time admission cap=20.00% — diagnostic only; no forced trim
```

With no prior snapshot at all, `OTHER` was omitted and the helper logged `$2,500` NAV / 68% weight for the same actual 16.1905% state.

The added price-gap test contains only `WIN`, invokes the helper directly without the real loop's current `prices`, and merely checks that some `weight=` text and a drift line exist. It therefore masks this defect. It also does not assert the expected NAV/weight numerically.

**Required before PASS:** compute the diagnostic from the same full current price map used for the tick/snapshot (or an explicitly captured immutable fill-snapshot mark map), and add a multi-asset test covering both false-positive and false-negative directions. Preserve the diagnostic-only/no-trim behavior.

### Blocker 2 — the turnover terminology cleanup is incomplete, and the new test is vacuous against leftovers

**Severity: MEDIUM; wording/test blocker, not a gate blocker.**

The central docstring and three main operator messages were improved, but the same ledger is still described as realized trading elsewhere in current `broker.py`:

* line 3121-3122: `notional the book traded last quarter`;
* line 3242: `_turnover_ledger_rolling` still says `One-way notional traded over the trailing 21 sessions`;
* line 16377-16380: a gate comment says suppressed requests must not be booked as `notional traded`;
* line 16606: `Book this trade's one-way notional`;
* line 3099 still gives the persisted row as generic `one_way_notional`, without identifying it as accepted-request notional.

There is also an old quoted log in the turnover rationale at line 15658 (`traded in 21 sessions`). Historical quotations can remain if clearly historical, but the active ledger docstrings/comments must not contradict the new contract.

The new test:

```python
assert "Book accepted-order request notional" in SOURCE
assert "of NAV in accepted-order request notional" in SOURCE
```

only proves that two desired substrings occur somewhere. It passes even if every misleading `traded` statement remains, and it does not execute/inspect the relevant operator logs. It therefore cannot enforce the test name's claim, `names_accepted_requests_not_realized_fills`.

**Required before PASS:** finish the active ledger terminology consistently (`accepted/submitted request notional`, not realized turnover) and make the test pin the specific active docstrings/log templates while rejecting the known misleading forms in that scoped block. Do not change ledger timing, numerator, gates, or ceiling arithmetic.

## Secondary robustness concern

The newly added block is labelled `Observability only` but runs before final-fill stage commit/partial cleanup. Its `try` catches only `TypeError`, `ValueError`, and `AttributeError`. An unexpected exception from valuation/config access would now abort `_apply_backtest_confirmed_fill_state` after seen/fill ledgers were mutated but before pending cleanup/stage disposition. Normal `PortfolioEmulator` inputs make this unlikely, and I did not count it as a separate proven production blocker, but observability should be fail-safe if the intended contract is truly behavior-neutral. A focused failure-injection test would make that guarantee explicit.

## Tests and static checks

* `PYTHONPATH=. pytest -q backend/tests/test_anchor_execution_contract.py` — **22 passed**.
* Focused execution/core/turnover suite excluding the intentionally red adversarial specification — **199 passed**:
  `test_anchor_execution_contract`, `test_core_sleeve`, `test_core_sleeve_live_reachability`, `test_core_sleeve_satellite_share`, `test_core_sleeve_wiring`, `test_portfolio_emulator_fills`, `test_simulated_execution`, `test_turnover_conviction_bypass`, and `test_turnover_core_exemption`.
* Including `test_core_sleeve_adversarial.py` — **203 passed, 7 failed**. That file explicitly says every test in it is written to fail against the current implementation; the failures are A1-A4 and A9-A11 and are not caused by this diff.
* `python3 -m py_compile backend/broker.py backend/tests/test_anchor_execution_contract.py` — PASS.
* `git diff --check -- backend/broker.py backend/tests/test_anchor_execution_contract.py` — PASS.

## GitNexus / blast-radius note

`npx gitnexus status` reported an up-to-date index at `960a469`. `detect-changes` reported LOW risk/zero flows but failed to map the large broker file's changed helpers; upstream impact queries for `_turnover_ledger_record`, `_anchor_reinforcement_execution_policy`, `_anchor_reinforcement_position_headroom`, and `_apply_backtest_confirmed_fill_state` all returned `not found` / risk `UNKNOWN`. This is the known >512 KiB broker indexing limitation, so the LOW result is not graph proof. Manual radius for the behavioral addition is the next-event backtest fill loop and anchor stage reconciliation; live anchor submission remains rejected by the research-only policy.

## Final recommendation

**REQUEST CHANGES.** Keep the decision-time cap wording, visible fill source, causal log order, and accepted-request operator messages. Fix the diagnostic to use the real full current mark set, add multi-asset positive/negative drift coverage with exact NAV/weight assertions, and complete/pin the active turnover terminology. No gate, cap, ledger, or trim behavior should change.
