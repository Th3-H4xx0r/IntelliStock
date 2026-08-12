# Backtest 735390: anchor execution and turnover reconciliation

**Run:** 735390
**Code:** `960a469fee5776544df1a3bfeb7b84fb3c8eeacf` (the working tree `HEAD` while this audit was performed)
**Result:** deliberately stopped at 33.33%, after the emitted `2026-01-21T00:00:00` snapshot
**Audit scope:** mechanical plan/gate/order/fill/stage/snapshot chain, rolling turnover, core funding, and partial-stage state. This is not a P&L or causal-alpha review.

## Sources and audit basis

* Pre-registration: `docs/investigations/anchor-execution-verification.md`.
* Terminal log: `/tmp/bt735390-stopped.log` (15,224 lines).
* Terminal metadata and summary: `/tmp/bt735390-stopped.json`, including the frozen strategy schema and 211-point equity curve.
* Backend at `960a469`: principally `backend/broker.py`, `backend/portfolio_emulator.py`, `backend/core_sleeve.py`, and `backend/strategies/graph_nexus_analysis.py`.

The terminal summary agrees with the registered run identity: strategy/doc 193, instance `v2-let-run-core`, 3,600-second bars, $6,000, and `2026-01-01..2026-03-01`. The run stopped with 11 fills (9 buys and 2 sells), $6,633.1185 terminal NAV, and the frozen relevant settings included:

* anchor execution enabled, target 20 percentage points, order-position cap 20 percentage points, anchor turnover ceiling 0.80, and floor-bounded core permission enabled;
* `backtest_credit_pending_sell_proceeds=true`;
* general turnover budget 0.50 with conviction bypass enabled, but **`turnover_budget_conviction_bypass_max_pct=0`**, meaning the general conviction bypass had no hard ceiling.

That last distinction matters: the configured 0.80 ceiling was an **anchor-order lane ceiling**, not a global cap on every source in the portfolio.

## Executive findings

1. **The anchor execution chain itself is mechanically genuine.** Plan `UUUU:s2:p7` produced accepted order `sim-000000000011-UUUU`, a source-correlated final fill for 7.05823839 shares / $150.6140, an increase from 53.0750876660 to 60.1333260590 shares, and an explicit `ANCHOR STAGE PARTIAL`. There was no orphan, mismatch, duplicate, or false stage commit.
2. **The p7 anchor order did not cross 80% when admitted.** The runtime ledger was $5,122.96 / $6,607.4458 = **77.5331%** before p7. Adding its $155.43 accepted request projected and booked $5,278.39 / $6,607.4458 = **79.8855%**, matching `projected=79.9%`.
3. **The log's lone “82%” observation was earlier and unrelated to anchors.** It occurred on simulated `2026-01-07 18:00`, before the first anchor plan (January 9). It was caused by the four opening stock requests plus unrelated AMCR and AMD requests. No anchor notional existed then.
4. **It was not later unrelated trading after p7 that crossed the ceiling.** There were no non-anchor fills after p7. At the January 20 fill snapshot, the request ledger read **80.3282%** only because NAV fell between order and fill; the ledger numerator had already been booked at order acceptance. On actual governed fill notional, turnover after the anchor fill was only **78.9648%**.
5. **The generic turnover log is not an actual-fill ledger.** It books requested `cash_to_use` at successful submission and calls it “traded” notional. At the 82% observation it was $5,122.96 / $6,284.5694 = **81.5165%**; actual non-core fills were $5,038.1895 / $6,284.5694 = **80.1676%**. Thus the run still narrowly exceeded 80% on actual governed fills before any anchor, but not by the amount implied by the generic log.
6. **The order did not overfill the 20% position cap.** The immediate post-fill UUUU weight was **19.4831%**. Appreciation—not another order—took it above 20% one hour later and to 21.6116% at the maximum. The backend implements an order-admission headroom cap, not a standing maintenance/trim cap.
7. **No anchor caused a core release.** Every anchor plan was excluded from the core-funding pre-pass. The two actual SPY releases occurred before the first anchor plan. P7 was funded from existing cash, so this run did not exercise the pending-sell-proceeds bridge even though it was configured.
8. **The pre-registration's literal run-level failure remains true, but it should not be described as an anchor-order defect.** The whole partial run had an earlier actual governed-turnover observation just above 80%, and its terminal recipient weight was above 20%. Those are run-level/pre-registration failures. The p7 plan/gate/order/fill implementation did not itself violate either limit at execution.

## What the code actually enforces

### Planner and pending identity

In execution-aware mode, `_plan_anchor_reinforcement`:

* chooses the highest currently eligible stage;
* computes target and actual marked position value;
* creates a unique `ticker:stage:sequence` plan ID;
* stores `_anchor_reinforce_pending[ticker]` with stage, plan ID, planned amount, required notional, target, and no order ID;
* does **not** commit `_anchor_reinforce_stage` at planning time.

Broker rejection calls `_anchor_reinforcement_block`, which removes that pending record. An accepted simulation submission adds the order ID to the same pending record.

### Anchor turnover gate

The broker calculates:

`projected = rolling_request_ledger / current_NAV + planned_cash_per_trade / current_NAV`

and accepts only when `projected <= anchor_reinforce_execution_turnover_ceiling_pct`. The gate is evaluated at order submission using then-current NAV. The accepted amount is booked into the turnover ledger immediately after submission. There is no second turnover gate at next-event fill time.

The generic rolling ledger is a submission ledger:

* `_turnover_ledger_record` adds the absolute precomputed request notional after a successful submission;
* `_turnover_ledger_rolling` sums those daily buckets;
* `turnover_budget_state` divides that sum by current NAV;
* the core symbol is intentionally excluded by `_turnover_is_governed` whenever the core is configured.

Consequently the numerator can remain fixed while the percentage moves solely because NAV moves, and the numerator can differ from actual fill notional.

### Fill correlation and stage commit

The emulator carries `order.source` through the `SimulationFill` and persisted trade row. For p7 the submitted source was:

`anchor_reinforcement:stage=2:plan=UUUU:s2:p7`

`_apply_backtest_confirmed_fill_state` requires all of the following before emitting `ANCHOR FILL`:

* source prefix `anchor_reinforcement:`;
* buy side and matching symbol;
* source stage equals pending stage;
* source plan equals pending plan;
* actual order ID equals pending order ID;
* unseen order/cumulative-quantity fill key.

Only a final correlated fill can commit a stage, and only when marked position value is within tolerance of the stored target. Otherwise it logs `STAGE PARTIAL`, leaves the stage uncommitted, retains the cumulative filled-stage ledger, and removes the now-final order from pending state.

## Complete plan ledger

There were 9 actual plans, 5 `PLAN NONE` notices, 8 broker blocks, 1 order, 1 correlated fill, 0 commits, and 1 partial-stage result. Invalid fill counts were all zero.

| Simulated decision | Plan | Planner dollars | Broker result |
|---|---|---|---|
| Jan 9 | `UUUU:s1:p1` | target/current/need/planned `$1285/$1045/$241/$240.76` | Core two-leg projection 87.2%; one-leg used/projected 79.7%/82.9%; turnover block; pending cleared. |
| Jan 12 | `NVO:s1:p2` | `$1269/$968/$301/$175.32` | Core 86.3%; one-leg 80.8%/83.5%; turnover block; pending cleared. |
| Jan 13 | `NVO:s1:p3` | `$1286/$969/$316/$177.65` | Core 85.2%; one-leg 79.7%/82.5%; turnover block; pending cleared. |
| Jan 14 | `UUUU:s1:p4` | `$1289/$1045/$244/$178.06` | Core 85.0%; one-leg 79.5%/82.3%; turnover block; pending cleared. |
| Jan 15 | `UUUU:s1:p5` | `$1307/$1111/$195/$180.56` | Core 83.9%; one-leg 78.4%/81.2%; turnover block; pending cleared. |
| Jan 16 | `UUUU:s2:p6` | `$1318/$1165/$153/$152.71` | Core 82.4%; one-leg 77.8%/80.1%; turnover block; pending cleared. |
| Jan 19 | `UUUU:s2:p7` | `$1321/$1166/$155/$155.43` | Core two-leg version excluded at 82.2%; cash-funded one-leg projection 79.9%; order accepted and finally filled; stage partial. |
| Jan 19 | `NVO:s1:p8` | `$1321/$1006/$315/$108.14` | Core 80.8%; after p7's booked request, used/projected 79.9%/81.5%; turnover block; pending cleared. |
| Jan 20 | `NVO:s1:p9` | `$1327/$974/$353/$258.02` | Core 87.3%; floor-bounded satellite room trimmed to $33.82, below lane minimum; satellite-cap block; pending cleared. |

This ledger reconciles every plan to exactly one terminal disposition. It also shows that p7's accepted request immediately affected p8 in the same execution order, so there was no same-cycle omission of the accepted anchor reservation.

## P7 plan-to-snapshot chain

### 1. Plan and gates

At simulated `2026-01-19 15:00`:

* NAV: $6,607.4458229254.
* Pre-order UUUU: 53.07508766598 shares at $21.97 = $1,166.0596760217 (17.6476% of NAV).
* Exact 20% cap: $1,321.4891645851.
* Headroom/need: about $155.42949, rounded to the $155.43 plan/request.
* Existing cash: $225.04; no same-tick core release was admitted.
* The anchor turnover gate logged `projected=79.9% <= 80.0%`.
* The position-cap diagnostic independently trimmed to the same $155.43 headroom.
* Buying-power/fundable diagnostic was $155.43.

The broker then accepted `sim-000000000011-UUUU` with the same symbol, stage 2, and plan ID `UUUU:s2:p7`.

### 2. Source fill

At the next available UUUU price event, quote timestamp `2026-01-20 14:00:00+00:00`:

* fill quantity: 7.05823839;
* fill price: $21.338755;
* gross fill notional: **$150.6140197**;
* fee: $0.004518;
* source/stage/plan/order checks all passed before `ANCHOR FILL` was emitted.

The generic `[execution] FILL BUY UUUU` line does not print `source=...`; nevertheless, the adjacent `ANCHOR FILL` cannot be reached in this code unless the fill object's source and all identities match. The trade record also stores `source`. Thus source correlation is evidenced, while the literal pre-registered desire for a visibly source-tagged generic fill line is not met by the log format.

### 3. Quantity and stage state

The emitted curve changes as follows:

| Snapshot | UUUU quantity | UUUU mark/value | NAV | Weight |
|---|---:|---:|---:|---:|
| Jan 20 13:00, before fill | 53.0750876660 | $21.97 / $1,166.0597 | $6,607.4695 | 17.6476% |
| Jan 20 14:00, after fill | 60.1333260590 | $21.29 / $1,280.2385 | $6,571.0309 | **19.4831%** |
| Jan 20 15:00 | 60.1333260590 | $23.21 / $1,395.6945 | $6,635.3438 | 21.0342% |
| Jan 20 20:00, maximum | 60.1333260590 | $23.93 / $1,438.9905 | $6,658.4196 | **21.6116%** |
| Jan 21 00:00, terminal | 60.1333260590 | $23.50 / $1,413.1332 | $6,633.1185 | **21.3042%** |

The stored target was $1,321.49. At the fill mark, marked value was $1,280.24, leaving $41.25, well above the roughly $1.32 completion tolerance. The fill was the final fill for the order; `STAGE PARTIAL` therefore means **partial progress toward the stage target**, not an exchange-level partial fill still awaiting more shares. The code removed the final order from pending state, retained $150.6140 in the stage fill ledger, and did not advance stage 2.

At 15:00 the unchanged shares appreciated above the old target. The code deliberately cannot commit from appreciation: commits occur only inside the correlated-fill handler. This produces a safe uncommitted stage rather than a false commit. It may be replanned later if marked shortfall again reaches the minimum order size; no accepted no-fill order remained pending at stop.

## Turnover reconstruction

### Two distinct quantities

For this run, “turnover” must be stated with its numerator:

1. **Runtime request ledger:** accepted/requested `cash_to_use`, booked at submission. This is what the gates and `TURNOVER BUDGET BINDING` lines used.
2. **Realized governed fills:** gross fill notional of non-core symbols. This is the closest literal measure of notional actually traded under the same intended core exemption.

SPY is deliberately excluded from both lane calculations. Including all SPY core fills would give $9,352.6501 gross by the 82% observation (148.8193% of that NAV) and $9,503.2641 after p7, but that is not the configured rolling ledger. The terminal summary's $4,314.4827 SPY gross is core churn telemetry, not anchor-ceiling turnover.

### Numerators before p7

Accepted stock requests booked by the runtime ledger were:

* January 1 opening basket: NVO, ODFL, UUUU, V = 4 × $840.00 = **$3,360.00**;
* January 6 unrelated conviction buys: AMCR and AMD = 2 × $881.48 = **$1,762.96**;
* total before any anchor order = **$5,122.96**.

Actual gross fills for those same governed orders were:

* opening basket = **$3,346.6593551**;
* AMCR + AMD = **$1,691.5301481**;
* total = **$5,038.1895032**.

The $84.7705 difference is why the generic log is not an exact actual-fill measure. It does not change the historical conclusion at January 7: actual governed fills were still just over 80% then.

### Exact checkpoints

| Checkpoint | NAV | Runtime request ledger | Runtime % | Realized governed fills | Realized % |
|---|---:|---:|---:|---:|---:|
| Jan 7 18:00, line reported “82%” | $6,284.5694 | $5,122.96 | **81.5165%** | $5,038.1895 | **80.1676%** |
| Jan 19 15:00, immediately before p7 | $6,607.4458 | $5,122.96 | **77.5331%** | $5,038.1895 | **76.2502%** |
| Jan 19, immediately after accepted p7 order | $6,607.4458 | $5,278.39 | **79.8855%** | $5,038.1895 (not filled yet) | **76.2502%** |
| Jan 20 13:00, immediately before fill | $6,607.4695 | $5,278.39 | **79.8852%** | $5,038.1895 | **76.2499%** |
| Jan 20 14:00, immediately after fill | $6,571.0309 | $5,278.39 | **80.3282%** | $5,188.8035 | **78.9648%** |
| Jan 21 00:00, terminal | $6,633.1185 | $5,278.39 | **79.5763%** | $5,188.8035 | **78.2257%** |

The post-fill request-ledger percentage rose above 80 because NAV fell from the decision/prefill level to the fill snapshot. The fill itself did not add to that ledger—it had been booked at order acceptance—and actual fill notional was $4.816 less than the request. Later NAV recovery brought both measures back below 80 at the terminal snapshot.

### Answer to the specific causation question

* **Did the anchor order itself violate the admission ceiling? No.** Its exact request-ledger projection at order time was 79.8855%.
* **Did later unrelated activity cross it? No.** There were no later unrelated fills. The later request-ledger ratio of 80.3282% came from denominator movement at the fill snapshot.
* **What produced the logged 82%? Earlier unrelated activity.** The observation was January 7, before any anchor plan, after the opening basket and AMCR/AMD. Actual governed fill turnover was 80.1676%; runtime request-ledger turnover was 81.5165%.
* **Does the partial run nevertheless violate the pre-registration's broad sentence “turnover exceeds 80%”? Yes, if read as a run-wide ever-observed condition.** It did so before anchor execution. That sentence is broader than the implemented anchor-lane admission rule and therefore cannot diagnose an anchor-order failure.

## Core release and funding

All nine plans produced a core pre-pass exclusion. The pre-pass conservatively projects two legs—hypothetical core sale plus anchor buy—even though SPY is excluded from the generic runtime ledger.

For p7 the exact logic was approximately:

* base request ledger: 77.5331%;
* hypothetical $155.43 core sale: +2.3523 percentage points;
* $155.43 anchor buy: +2.3523 points;
* two-leg projection: **82.2378%**, logged as 82.2%, so no core sale;
* cash-funded one-leg projection: **79.8855%**, so the order remained admissible.

The log phrase “not releasing core for a buy the execution gate will refuse” is overbroad in this case. The **core-funded two-leg variant** would be refused; the cash-funded one-leg order was correctly accepted.

Only two `[core] released` submissions existed:

* 2.43426160 SPY submitted January 5 and filled January 5 16:00;
* 0.16929019 SPY submitted January 7 and filled January 7 16:00.

Both predated the first anchor plan on January 9. No core release followed a known inadmissible anchor. P7 used existing cash ($225.04 at decision) with $155.43 fundable. Therefore this run verifies prevention of an unnecessary core release, but **does not verify** that pending same-tick sell proceeds can fund an anchor; no such bridge was used.

The SPY core never breached its configured 10% floor in the emitted curve: minimum 11.1162%, 11.4993% immediately after the anchor fill, and 11.1623% terminal.

## Evidenced defects versus wording mismatches

### Evidenced implementation/observability defects

1. **The turnover telemetry calls submissions “traded” notional.** The ledger books pre-fill request notional and is never reconciled to actual fills, partial fills, or expirations. This run quantifies the resulting overstatement: 81.5165% logged-ledger versus 80.1676% realized governed fills at the 82% checkpoint, and 79.5763% versus 78.2257% terminal. This is a measurement/labeling defect, not evidence that p7 was improperly admitted.
2. **The generic `FILL BUY` log omits source provenance.** Source exists in the fill and trade record and is strongly validated before `ANCHOR FILL`, but the literal generic execution signature is not visibly source-tagged. Grep-only verification therefore needs the adjacent source-gated `ANCHOR FILL` or a log-format change.

No orphan/mismatch/duplicate, false commit, wrong quantity mutation, order-cap overfill, improper core release, or dangling accepted order is evidenced.

### Pre-registration/config wording mismatches, not anchor implementation defects

1. **“Hard rolling-turnover ceiling” and “turnover exceeds 80%” conflate lane-local admission with a global invariant.** The 0.80 setting only gates anchor projections. The frozen general conviction bypass explicitly had no ceiling, and unrelated buys took the book above 80 before any anchor.
2. **“Final recipient weight stays at or below 20%” is stronger than the implemented cap.** The backend caps order headroom at the decision mark. It has no standing trim or appreciation guard. P7 filled to 19.4831%; price appreciation caused the later 21% readings.
3. **“Partial anchor fill” is ambiguous.** P7's order reached a final fill; only the stage target remained partial. No order was left pending.
4. **The listed signature order is not the emitted log order.** The source-gated `ANCHOR FILL` is logged while processing the confirmed fill, before the generic `[execution] FILL BUY` line is printed. Semantically the fill was already applied; only the logging order is reversed.
5. **Core-floor/pending-proceeds validity was configured but not exercised.** The successful anchor was cash-funded after the core-funded variant was excluded. This run cannot validate the pending-sell credit bridge.

## Final assessment

**Anchor mechanical verdict:** the p7 lane passed the narrow execution test. The plan, gate, order ID, hidden source provenance, fill, stage partial state, and quantity increase reconcile exactly. It was admitted below the configured order-time turnover ceiling and filled below the configured order-time position cap. Core funding was safely withheld.

**Registered whole-run verdict:** the stopped partial run still fails the document's broader literal conditions because actual non-core turnover had already touched 80.1676% on January 7 and terminal UUUU weight was 21.3042%. Those failures justify the predeclared stop as an operational decision, but they are not evidence that the p7 anchor order itself broke the two gates. The turnover breach was earlier unrelated activity; the concentration breach was later appreciation; and no later unrelated trade followed the anchor.
