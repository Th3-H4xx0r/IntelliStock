# Anchor reinforcement: log/code audit

**Read-only snapshot:** 2026-08-10 09:23:05 UTC. No code, strategy config, event state,
or git state was changed. Temporary pulls went to `/tmp/intellistock-anchor-analysis`; this file is
the only repository output.

## Bottom line

The available runs contain **six planner allocations and zero executed anchor adds**: five
`ANCHOR ADD:` allocations in bt 633644 and the older `Winner add-on:` allocation in target-12 bt
584712 were all rejected immediately by the broker's `SATELLITE CAP`. The published interpretation
that bt 633644 “scaled” UUUU/SNDK, that the recipients made `+$283 on ~$1,153 of adds`, or that NVO's
add caused its reversal is therefore false. Those P&Ls belong to the original positions. The logs do
show a useful selection-risk signal (NVO qualified at +15.2% and later fully reversed), but no add
exposure existed.

The multi-window verdict is pre-declared, but **not well posed as an anchor-lever causal test**:
`ANCHOR ADD:` is a planner signature rather than a fill; the target is downstream-blocked by a
standing satellite cap and normally by the broker's 15% single-name cap; each arm also changes
`history_scope_salt`, which demonstrably changes/inherits discovery state and names before an anchor
can possibly qualify. A return or drawdown delta can therefore come from names/state and from
planner budget crowd-out, not from filled reinforcement lots.

## Reproduction commands

```bash
cd /Users/pranavkrishna/PranavFiles/coding-projects/IntelliStock
mkdir -p /tmp/intellistock-anchor-analysis
python3 scripts/pull_backtest_logs.py 584712 --out /tmp/intellistock-anchor-analysis/584712.log
python3 scripts/pull_backtest_logs.py 633644 --out /tmp/intellistock-anchor-analysis/633644.log
python3 scripts/pull_backtest_logs.py 615886 --out /tmp/intellistock-anchor-analysis/615886.log
python3 scripts/summarize_backtest.py 584712 633644 615886
# Two bounded refreshes; no polling loop:
python3 scripts/pull_backtest_logs.py 615886 --out /tmp/intellistock-anchor-analysis/615886-refresh1.log
python3 scripts/pull_backtest_logs.py 615886 --out /tmp/intellistock-anchor-analysis/615886-refresh2.log
python3 scripts/summarize_backtest.py 615886

grep -nE 'ANCHOR ADD: (UUUU|NVO|SNDK)|Winner add-on: (UUUU|NVO|SNDK)|@ 2026-.*action_intent=winner_add_buy|SATELLITE CAP: (UUUU|NVO|SNDK)' \
  /tmp/intellistock-anchor-analysis/633644.log
grep -nE '\[execution\] FILL (BUY|SELL) (UUUU|NVO|SNDK)' \
  /tmp/intellistock-anchor-analysis/633644.log
grep -nE 'Winner add-on: AXTI|AXTI @ .*action_intent=winner_add_buy|SATELLITE CAP: AXTI|\[execution\] FILL (BUY|SELL) AXTI' \
  /tmp/intellistock-anchor-analysis/584712.log
grep -nE 'V31 anchor reinforcement budget|ANCHOR ADD:|\[execution\] FILL (BUY|SELL)|History scope:|Backtest cleanup:|Discovery (bootstrap IMPORT|snapshot SAVED)' \
  /tmp/intellistock-anchor-analysis/615886-refresh2.log
```

The frozen `strategy_schema` differs between 584712 and 615886 only in target (`12` vs `20`) and
salt (`let-run-core-193` vs `anchor20-oos-20260810-a`); 633644 and 615886 differ only in salt.
However, 584712 predates commits `b40d2d8` (pure-hold abort fix) and `57f83d7` (the `ANCHOR ADD`
logging); it is a useful historical target-12 observation, not a same-build control for 615886.

## Run-level evidence

| bt | window / frozen target, salt | status at pull | `summarize_backtest.py` | planner allocations | executed anchor adds |
|---:|---|---|---|---:|---:|
| 584712 | OOS bull, 12, `let-run-core-193` | finished | +12.34%, maxDD 5.8%, 21 trades | 1 (`AXTI $130`; old build has no `ANCHOR ADD:` line) | **0** |
| 633644 | reference, 20, `let-run-core-193` | finished | +5.61%, maxDD 12.9%, 24 trades | 5, $1,153 total | **0** |
| 615886 | OOS, 20, `anchor20-oos-20260810-a` | running, 8.62%, 387 s | partial +0.25%, maxDD 0.4%, 2 buys | 0; two explicit `none funded` | **0** |

The partial 615886 values are not a verdict. At the last bounded pull it had replayed only through
`2026-04-01T12:00:00` and held ARKF/USO.

## Exact signatures, broker outcomes, and reversals

### bt 633644: all five “funded” lines were pre-execution only

| planner line | immediate execution-path evidence | actual position / subsequent path |
|---|---|---|
| `7746 ANCHOR ADD: UUUU stage=1 +$241 (held 7d, pnl +24.4%, drop_from_peak 0.0%, entry $840, raw 1.200)` | line 7872 emits `buy action_intent=winner_add_buy`; line 7873: `SATELLITE CAP: UUUU skipped ... ($-1,400 room)` | UUUU quantity stayed **53.07508767** for the entire run. Signal price $19.68 to end mark $21.355 was +8.5% (hypothetical add only). |
| `8675 ANCHOR ADD: NVO stage=1 +$175 ... pnl +15.2% ...` | lines 8818-8819: buy intent, then `SATELLITE CAP: NVO skipped ... ($-1,364 room)` | Quantity stayed **16.13507190** until the only sell. NVO then went from signal $59.98 to sell fill $38.840851 (**-35.2%**) and original-position P&L `-$213.32 (-25.40%)`: a real full reversal in an *eligible name*, not an add loss. |
| `12351 ANCHOR ADD: UUUU stage=2 +$211 ... pnl +38.7% ...` | lines 12505-12506: buy intent, then satellite skip (`$-1,457 room`) | No quantity change. Signal $21.95 to end $21.355 was -2.7% hypothetically. |
| `17115 ANCHOR ADD: UUUU stage=3 +$319 ... pnl +51.7% ...` | lines 17298-17299: buy intent, then satellite skip (`$-1,574 room`) | No quantity change. Signal $24.01 to end $21.355 was -11.1%; original UUUU still finished `+$293.42 (+34.93%)`. |
| `36009 ANCHOR ADD: SNDK stage=2 +$207 ... pnl +32.0% ...` | lines 36106-36107: buy intent, then satellite skip (`$-1,574 room`) | SNDK quantity stayed **1.78663492**. Signal $683.19 to end $631.54 was -7.6% hypothetically; original-position P&L was `+$203.38 (+21.99%)`. |

The only recipient buy fills are the original NVO/UUUU fills at lines 2921/2923 and the original
SNDK fill at line 21185. There are no later recipient buy fills. The result arithmetic independently
confirms this: `backtest_summary.compute_per_stock_pnl` defines percent as P&L / total buys
(`backend/backtest_summary.py:573-601`), implying total buys of exactly **$840 UUUU, $840 NVO,
$924.95 SNDK**, not $840+$771, $840+$175, or $925+$207. Thus the oft-quoted recipient net `+$283`
is original-lot P&L and cannot be attributed to $1,153 that never traded. A simple, explicitly
non-executed signal-price-to-end mark of all five requested clips would be about **-$102** before
slippage/fees; it is a risk diagnostic, not realized P&L.

### bt 584712: target 12 was not planner-inert either

Line 16306 says `Winner add-on: AXTI alloc=$130 (P&L=+54.8%, held=14d, drop=3.0%)`; lines
16436-16437 emit the winner-add buy intent and then `SATELLITE CAP: AXTI skipped ... ($-1,478
room)`. AXTI quantity stayed **11.20708417**. The requested clip's signal $80.01 to end $70.235
was -12.2% hypothetically, while the original lot finished `+$207.84 (+35.88%)`.

This directly falsifies the pre-registration's expected “target 12 should produce no funded adds”
at the **planner** level. AXTI's actual opening notional was only about $579.29 after execution
funding, so stage 2 arithmetic is approximately
`0.12*$6,953*(1.6/1.3) - $579.29*1.548 = $130.16`. The claim that 12 is incapable “for any name”
assumes every entry filled at the 14%-NAV clip; partial/runt fills violate that assumption. It was
still execution-inert here because the broker rejected it.

## “None funded” counts and budget interpretation

* **633644:** 43 budget cycles: 2 with zero candidates, 41 candidate-bearing. Exactly **36**
  `ANCHOR ADD: none funded` and 5 planner allocations. Of the 36 negative lines, **13 had a $0
  budget**; the other **23 had positive budgets totaling $6,809**, median $246, range $170-$600.
  Candidate-bearing budget median was $207 (range $0-$600).
* **584712:** 21 budget cycles: 1 zero-candidate and 20 candidate-bearing. The older build lacks the
  negative signature. One `Winner add-on` leaves **19 planner-negative cycles inferred**, including
  one $0 budget; candidate-bearing median budget was $192 (range $0-$1,728).
* **615886 available prefix:** two candidate-bearing cycles, both explicit negative lines, on
  **$1,497** and **$1,495** budgets. No planned or executed add.

These counts do **not** prove the hard-coded 40% budget is why a line is negative. `_winner_add_docs`
contains held/raw-score candidates, not stage-qualified winners
(`graph_nexus_analysis.py:30070-30118`). The planner can reject for hold days, P&L, peak drop,
entry notional, prior stage, or a target gap below the $100 floor. Current 615886 demonstrates this:
a one-day-old holding produces “none funded ... on a $1,497 budget” because it cannot meet the 7-day
stage threshold. Also, the planner deliberately permits a partial add:
`allocation = min(remaining_budget, additional_needed)` and funds it when allocation is at least the
minimum (`:10857-10869`); the real test `test_the_budget_still_binds` asserts that a **$150 budget
funds** a nominal $234 stage-1 need. Therefore the prior claim “$178 < $234, so budget is the next
zero-funding constraint” is not supported. The negative signature does not log a rejection reason.

## Capacity, cash, and mechanism defects exposed by the cross-check

1. **Planner allocation is not execution.** After planning, the strategy logs `ANCHOR ADD`, promotes
   a score and writes `winner_add_buy` (`:30193-30249`); the broker later runs the standing satellite,
   turnover, cash, min-size and single-position gates. All six historical plans died at the first of
   those downstream gates.
2. **The target conflicts with downstream caps.** Stage targets for target 20 are 20.0%, 24.6%, and
   30.8% of NAV. Held adds bypass the strategy final-pass cap (`:32291-32299`), but the broker applies
   `BROKER_MAX_SINGLE_POSITION_PCT`, default **15%**, in backtests too (`backend/broker.py:15414-15463`).
   The remote env override is not frozen in the result, another audit gap. At the five 633644 signals,
   the original positions were already approximately 15.25%-18.86% of NAV, so the default broker cap
   would have left zero headroom even if the earlier satellite gate had not skipped them. All had raw
   1.20-1.25, below the 1.50 satellite-overflow threshold, while the satellite book was already above
   its design share.
3. **Failed attempts consume strategy budget but not cash.** Planner “spent” is subtracted before the
   new-stock slate (`:30217-30220`, used at `:30375-30386`). The broker does not refund this when it
   later skips the add. Thus target 20 can alter new-name selection/capacity without buying the anchor;
   any arm-level P&L delta is not necessarily add P&L. Recipient quantities and cash were unchanged by
   the skipped clips.
4. **A failed plan permanently advances the stage.** `_plan_anchor_reinforcement` writes
   `_anchor_reinforce_stage[ticker] = current_stage` immediately on planner funding (`:10863-10869`),
   before sell-intent filtering and before broker execution. A broker-skipped stage is never retried.
5. **Later-stage target math ignores earlier actual adds.** `current_value` is calculated from the
   first entry notional and first-entry return (`:10852-10857`; snapshot source `:9026-9095`), not
   actual current shares/market value. If adds ever fill, later stages can double-count earlier added
   capital; downstream caps, rather than the planner, become the effective sizing rule.

## Held-name/state divergence precedes the treatment mechanism

At the same early simulated timestamps—before the 7-day minimum could let target 20 fire—the books
were already disjoint:

| simulated time | historical 584712 target-12 | current 615886 target-20 |
|---|---|---|
| 2026-03-30 15:00 | ETH; cash $5,100.00 | ARKF; cash $5,100.12 |
| 2026-03-31 15:00 | ETH, SOC; cash $4,268.05 | ARKF, USO; cash $4,202.28 |
| 2026-04-01 12:00 | ETH, SOC | ARKF, USO |

The discovery logs explain why this is not a clean target-only pair. Runs 584712 and 633644 use
scope `4ffd8b...` and say `Backtest cleanup: skipped ... lookback data preserved`; they were not
cold despite the prior report calling 633644 cold. Current 615886 uses new scope `bb8417...` and
says it cleared 126 instance-scoped rows, **but immediately before that it says**:

```
Discovery bootstrap IMPORT 126/126 tickers from snapshot scope 4ffd8b13f738...
Backtest cleanup: cleared 126 rows ... Shared caches preserved.
```

It then saved 80 discovery tickers, versus 126 in 584712, and selected different names. Source
confirms `history_scope_salt` is an input to the history-scope hash
(`backend/nexus_config_identity.py:105-149`). Separate salts therefore do not hold discovery fixed;
the observed bootstrap also disproves the design statement that they prevent inheritance. Because
snapshots/shared caches survive, treatment-first ordering can contaminate the later arm rather than
isolate it.

Finally, every examined run repeatedly logs `PIT RESEARCH MODE: no frozen snapshots ... legacy
current-state path ... lookahead bias and is NOT promotion-eligible`. That is incompatible with an
across-window rule that says to “promote” target 20 from these returns.

## Assessment of `anchor-multi-window.md`

The return/drawdown thresholds are explicit and were written before 615886, which is good. But the
verdict is not presently identifiable as an anchor-reinforcement verdict:

* “mechanically alive” must require a **recipient quantity increase / execution fill**, not a planner
  `ANCHOR ADD:` line;
* “funded adds cause drawdown” cannot be inferred from aggregate symbol P&L or from arms that hold
  different names; `summarize_backtest.py` correctly summarizes the whole symbol/run, not lots or
  action intents;
* target and salt are changed together; discovery state is neither frozen nor actually cold;
* a 4.94pp name-selection noise threshold does not deconfound a single pair—it only refuses small
  deltas, while large salt/name shocks can still be mislabelled as anchor effects;
* the historical 584712 row is different-build/preserved-state evidence, not the planned cold
  control; and the current run is both incomplete and lookahead-contaminated.

A well-posed future test would clone one frozen PIT discovery/history snapshot into both arms, vary
only the target on the same deployed build/environment, verify actual filled add dollars and
post-fill quantities, record broker skip reasons and per-lot/action-intent P&L, and separate direct
add exposure from planner budget crowd-out. Until then the defensible verdict is:
**planner lane triggered; execution lane remains unproven/inert in the observed runs; P&L effect is
unidentified.**
