# anchor reinforcement — pre-registered multi-window evaluation

> **CORRECTED / STOPPED 2026-08-10 after bt 615886.** The pre-registration below treated planner
> allocation as execution and treated separate salts as cold isolation. Both assumptions are false.
> Across bt 633644 (five plans), bt 584712 (AXTI), and bt 615886 (AAOI), all seven planner
> allocations were rejected by `SATELLITE CAP`; recipient quantities never increased. Separate salts
> also change/inherit discovery state and therefore cannot identify an aggregate return effect. The
> remaining five paired arms were cancelled rather than spending non-causal compute. Archived design
> details are retained below, but its return/drawdown verdict rules are invalid for this mechanism.

Date: 2026-08-10. This plan was written before launching the first run below and corrected after its
execution assumptions were falsified.

## Question

Can `anchor_reinforce_target_pct=20` produce an **executed BUY fill and recipient quantity increase**
after the planner selects a winner, without violating bounded concentration/core/turnover policy?
Correction: bt 633644 proved only that the planner can allocate. Its five orders all stopped at
`SATELLITE CAP`, so it supplied no reinforcement exposure and no add-lot P&L evidence.

## Design

Run cold, paired arms on all three untuned windows. Each arm gets a unique `history_scope_salt`, and
`scripts/reset_backtest_event_state.py --apply` runs immediately before every backtest. Fixed across
arms: doc 193, instance `v2-let-run-core`, 3600-second bars, $6,000 cash, deployed code at `2cd998c`,
`residual_sleeve_bear_block_at_fresh_low_bars=2`, and `regime_rally_onset_enabled=true`.

| regime/window | control | treatment | benchmark / 1x pace |
|---|---|---|---|
| OOS bull `2026-03-30..2026-04-27` | target 12, unique cold salt | target 20, unique cold salt | SPY +13.10% / +5.5% |
| bear `2026-03-02..2026-03-30` | target 12, unique cold salt | target 20, unique cold salt | SPY -7.86% / +5.5% |
| non-semi `2026-06-01..2026-07-01` | target 12, unique cold salt | target 20, unique cold salt | SPY -1.71% / +5.9% |

The treatment was launched first. The claim that separate salts create cold isolation is false:
logs show new salts importing prior-scope discovery snapshots, then saving different ticker sets.
Changing target and salt together changes names before the seven-day anchor gate can fire, so any
arm-level return delta is confounded even if its magnitude exceeds the 4.94pp noise floor.

## Signatures and measurements declared before the runs

* Grep planner intent **and the whole execution chain**: `ANCHOR ADD:` / `winner_add_buy`, then
  `SATELLITE CAP`, turnover, broker single-position cap, cash/order decisions, and `[execution] FILL
  BUY`. Success requires a later BUY fill plus a recipient quantity increase; a planner line is not
  funded exposure. Count `none funded`, planned, blocked, ordered, and filled states separately.
* Record symbol, stage, planned dollars, actual fill dollars, pre/post quantity, P&L and drop from peak.
  Bt 633644's NVO reversal is selection-risk evidence only: NVO qualified near +15%, but its add was
  rejected, so the -$213.32 belongs to the original lot.
* Record total return, SPY alpha, max drawdown, turnover/fills, held-name overlap, and P&L of every
  add recipient. Do not infer the lever worked from config alone.
* The known single-window/name-selection noise floor is 4.94 percentage points.

## Pre-declared verdict

Per window:

* **Strong pass:** target 20 beats its cold control by at least 4.94pp, still clears the window's 1x
  pace, and does not worsen max drawdown by 4.94pp or more.
* **Strong fail:** target 20 trails its cold control by at least 4.94pp, or funded adds cause a
  drawdown increase of at least 4.94pp.
* **Mechanically alive but P&L inconclusive:** funded adds occur and both return and drawdown deltas
  remain inside ±4.94pp.
* Bull objective evidence additionally requires beating SPY. A margin smaller than 4.94pp is not
  robust single-window evidence, so `+13.10%..+18.04%` is only a provisional bull pass.

These aggregate-return rules are archived and **must not be applied** until the execution path yields
source-tagged fills on a frozen identical discovery/history snapshot. The current defensible verdict
is: target 20 activates the planner, but observed execution is zero; target 12 can also plan for a
partial/runt entry (AXTI in bt 584712), so it is not universally planner-inert either. No return delta
can be promoted from these salted, lookahead-research arms.

## Run ledger

| arm | salt | backtest | result | verdict |
|---|---|---:|---:|---|
| OOS treatment 20 | `anchor20-oos-20260810-a` | 615886 | +9.02% | **execution fail:** 1 AAOI plan, satellite reject, 0 fill/quantity increase |
| OOS control 12 | `anchor12-oos-20260810-a` | cancelled | — | invalid pair: treatment had no exposure; salt confounds names |
| bear treatment 20 | `anchor20-bear-20260810-a` | cancelled | — | old plan stopped after execution falsification |
| bear control 12 | `anchor12-bear-20260810-a` | cancelled | — | old plan stopped after execution falsification |
| non-semi treatment 20 | `anchor20-nonsemi-20260810-a` | cancelled | — | old plan stopped after execution falsification |
| non-semi control 12 | `anchor12-nonsemi-20260810-a` | cancelled | — | old plan stopped after execution falsification |

Bt 615886 final audit: +9.02%, max DD 5.4%, 27 fills. `ANCHOR ADD` printed 19 negative lines and
one `AAOI stage=1 +$265` plan. The broker immediately logged `SATELLITE CAP: AAOI skipped` with
-$595 design room. AAOI held exactly 7.031933505093401 shares in all 180 snapshots between its
original BUY and equal-quantity SELL. Its +$238.63 is original-lot P&L. The run trailed SPY's
+13.10% by 4.08pp, but that delta is not attributable to the target.
