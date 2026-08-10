# 2026-08-10 — the benchmark, the sizing pattern, and why this is not going near real money

**HEAD:** `54bacbf` == `origin/main`, pushed, deploy hash-verified (`scripts/check_deployed_code.py`)
**Tests:** 4,809 passed / 13 skipped / **19 pre-existing failures** (`test_A11` + the
`adversarial_findings` family — these fail ON PURPOSE and were failing identically at session start;
verified by stashing every change and re-running. NOT a regression.)
**Doc:** 193 (`v2-let-run-core`) · **Runs this session:** 5 · **In flight:** none
**Goal:** NOT achieved, on all three clauses. See §1 and §7.

---

## 0. READ THIS FIRST IF YOU READ NOTHING ELSE

1. **Every push auto-deploys and kills a running backtest.** Operator-confirmed twice. Batch
   commits; push only between runs. Eighteen commits were held through five runs this session.
2. **Every backtest runs `pit_mode='research'`** (`interactive_utils.py:5607-5608`), which means
   **lookahead**. Every number in this document and every number in every prior handoff is from a
   run with lookahead. They are research signals, not P&L estimates.
3. **The noise floor is about NAMES, not returns.** Every A/B this session overlapped its control by
   **2 of 8–11 held names**. Nothing below ~5pp on a single window is evidence, in either direction.
4. **Declare the log signature and the pass/fail BEFORE the run.** Five for five this session.

---

## 1. THE SCORECARD — NOW WITH A BENCHMARK COLUMN

Twenty-plus investigation docs reported returns. **Not one stated what SPY did in the same window.**
`scripts/scorecard.py` joins every finished run to the SPY return of its own window, read from bars
already cached — zero runs.

```
  22/24 beat SPY     10/24 at or above 1x pace     mean alpha +9.17pp
```

| window | SPY | runs | best | alpha | 1x bar | verdict |
|---|---|---|---|---|---|---|
| reference bull/chop `01-01..03-01` | **+0.24%** | 16 | +17.36% | +17.1pp | +11.6% | 4/16 at 1x |
| bear `03-02..03-30` | **-7.86%** | 4 | **+21.27%** | **+29.1pp** | +5.5% | **4/4 at 1x** |
| OOS bull `03-30..04-27` | **+13.10%** | 3 | +13.35% | +0.25pp | +5.5% | **0/3 beat SPY** |
| non-semi `06-01..07-01` | **-1.71%** | 1 | +3.09% | +4.80pp | +5.9% | below 1x |

**Every window the strategy wins big is one where SPY is flat or falling.** The one window where SPY
runs hard has been run three times — hedge misfiring (+4.75%), hedge losing $514 (+13.35%), hedge
suppressed entirely (+12.34%) — and has **never beaten the index**. It is **not** a hedge problem.
It is a **participation** problem.

The two objective clauses therefore fail in *different* windows:
* flat/down tapes give **alpha but not pace**;
* the one hard-bull tape gives **pace but not alpha**.

Only a lever that raises returns fixes both. A lever that avoids losses fixes neither — which is
exactly what bt 584712 measured.

**The overfit test PASSED on the alpha clause.** The non-semi window (`RXD/ATEN/UAL`, the only
non-semiconductor-led window in the data) returned +3.09% against SPY's -1.71%. The config is **not**
fitted to semiconductors. It is just not fast enough in a flat tape.

---

## 2. THE MASTER PATTERN — FIVE INSTANCES OF ONE BUG

**Every lane whose job is to put size on a winner is calibrated below the size of one position.**
One position = `total_spend_cap_target_weight_pct 0.14 × NAV` = **$840** on a $6,000 book.

| # | lane | its budget/target | vs one clip | status |
|---|---|---|---|---|
| 1 | conviction overflow band | `(0.35-0.25)×NAV = $600` | $600 < $840 | **FIXED 2026-08-09** → window +9.70% → +17.36% |
| 2 | BFQ priority pool | `$155–$385` | 3–6× too small | lever reverted; the pool is the wall |
| 3 | satellite-cap remainder | `$235` | below the `$370` min-position floor | SNDK refused outright |
| 4 | anchor reinforcement target | `0.12×NAV = $720` | **below the $840 ENTRY** | fixed → **proven to fire**; reverted pending multi-window |
| 5 | anchor budget cap | `stock_budget × 0.40 = $170–$250` | vs a $234 stage-1 add | **NEXT** — now greppable |

**Standing rule: check any sizing lane against `0.14 × NAV` before believing it.** Four of these were
invisible because the lane logged its *budget* and never logged that it funded nothing.

### #4 in detail — the winner-add lane could never add

`_plan_anchor_reinforcement` computes `additional_needed = max(0, target_total − current_value)`.

```
    anchor_reinforce_target_pct  12   ->  target 0.12 x NAV = $720
    entry clip                        ->  entry  0.14 x NAV = $840
```

A position entered at the clip is **already worth more than its reinforcement target before it gains
a cent**, and it moves further out of reach as it wins. Stage 1/2/3 all compute **$0**.

bt 571147 (the best run on record, +17.36%) logged `candidates=4..6` on **25 separate bars** while
holding AMAT +45%, SBLK +37%, SLV +32%, XOM +27% and **SNDK +166%**, and funded **zero** adds.

Fix = `anchor_reinforce_target_pct` 12 → 20. The switch-on boundary is **~17.8%** of NAV (the target
must clear the stage-1 value `0.14 × 1.15 = 16.1%` **plus** `min_position_size` $100). Tests assert
`16→no, 17→no, 18→yes`. **The real function corrected the paper arithmetic here — 17 looks fundable
and is not.** 20 is the first round number with margin.

---

## 3. WHAT SHIPPED AND IS VERIFIED IN A RUN

### 3.1 The fresh-low sleeve gate — **LIVE on doc-193, keep it**

```
residual_sleeve_bear_block_at_fresh_low_bars = 2
regime_rally_onset_enabled                   = true
```

The sleeve was the one position in the book exempt from every extension check — it sized on regime
label + `ret5` alone. Measured separation, both proxies, **for zero runs**
(`scripts/check_range_position.py` reads `AlpacaBarsCache` directly):

```
2026-03-05   the leg that made +$889    bars_since_20d_low = 19   (the MAXIMUM possible)
2026-03-30   the leg that lost -$257    bars_since_20d_low =  0   (it IS the low)
```

Both runs confirmed it against **pre-declared** pass/fail:

| run | window | fresh-low blocks | outcome |
|---|---|---|---|
| **584712** | OOS bull | 12 (+4 rally-onset) | SQQQ notional **$0**; maxDD 11.4% → **5.8%**; core gross 2.54× → **0.21×** NAV |
| **789099** | bear | **0** | leg opened 03-05 @ **$70.80** — the same bar and price as the +$889 leg; SQQQ **+$918.78** |

**The gate is selective.** That is the entire design and it is now measured, not argued.

The offline replay also **corrected `gap-oos.md`**: the detector is point-in-time, so N=1 leaks the
third bad bar, whose `rally_onset` fallback clears by **34 cents on a $650 index**. N=2 covers it and
still clears the good park by sixteen bars. **Use N=2.**

**What it does NOT cover:** bt 584886 lost $45.54 on a 06-30 SQQQ round trip where the proxy was
**13 bars past** its low and +3.0%/+6.3% above it. This gate stops *shorting the bottom*, not
*shorting a rally*. Deliberately unfixed: -0.76pp is an order of magnitude below the noise floor.

### 3.2 `b40d2d8` — a pure-hold bar aborted the ENTIRE strategy

```
Run-once strategy 'graph_nexus_analysis' error:
  cannot access local variable '_max_positions' where it is not associated with a value
```

`_max_positions` was assigned only inside `if portfolio_total > 0 and _primary_buy_budget > 0:` →
`if stock_buys or _bfq_pending:`, while the `_nexus_max_positions` publish is unconditional. On a
bar with no buys the read raised and **the whole invocation was abandoned — no scores, no sells, no
sleeve intent.** 8 occurrences in the bear window, where `max_positions_bear=2` makes most bars pure
holds. **Every bear number recorded before this fix was measured with some of its bars silently
skipped.** Test drives the real AST; verified to fail with the fix stashed.

### 3.3 `57f83d7` — the anchor lane's missing log signature

```
ANCHOR ADD: UUUU stage=1 +$241 (held 7d, pnl +24.4%, drop_from_peak 0.0%, entry $840, raw 1.200)
ANCHOR ADD: none funded from 6 candidate(s) on a $178 budget — check
            anchor_reinforce_target_pct against the entry clip
```

The second line would have caught §2 #4 **25 times in a single run**. Shipped before the config
change, per the five-inert-levers rule.

---

## 4. WHAT WAS KILLED — DO NOT REVIVE

* **"Remove the SQQQ leg and the OOS window is +21.9%."** WRONG, measured. bt 584712 removed it
  entirely and returned **+12.34%**, *below* the control's +13.35%. The freed $2,100 bought different
  names and the book diverged to **2 of 11** overlapping. **Every "remove the loser, add back its
  loss" estimate in this repo is subject to this rebuttal.**
* **Conviction-ranked displacement.** bt 584886 refused ten top-band names; refused basket **+2.41%**
  vs bought basket **+3.46%**, and inside the band the score does not rank: **r = -0.235** (n=15),
  with NVTS (1.829) at -33% scoring above ALAB (1.700) at +34%. Third independent confirmation of
  *nothing separates winners from losers at entry.*
* **`bfq_conviction_target_weight_pct`.** Fired exactly as declared; its own log line killed it —
  every ask capped at the pool (`BKR $155`, `TTWO $184`, `SNDK $233` against a $840-863 target).
  Reverted, key removed from doc-193.

---

## 5. THE ANCHOR RESULT — "NOT PROVEN", NOT "HARMFUL"

bt **633644**, reference window, `anchor_reinforce_target_pct=20`. Control bt 571147 (+17.36%).

**The lane fired for the first time in this repo's recorded history:**

```
ANCHOR ADD: UUUU stage=1 +$241  (+24.4%)
ANCHOR ADD: NVO  stage=1 +$175  (+15.2%)
ANCHOR ADD: UUUU stage=2 +$211  (+38.7%)
ANCHOR ADD: UUUU stage=3 +$319  (+51.7%)
ANCHOR ADD: SNDK stage=2 +$207  (+32.0%)
```

UUUU scaled through **all three stages** and finished **top contributor at +$293.42**. SNDK returned
**+$203.38** against **+$52.64** in the control, where it was a $101 runt.

**And the return was +5.61%** against the control's +17.36% — below the pre-declared +12.42% revert
line. **Reverted to 12.**

Recorded as **not proven**, not harmful:
* book overlap with the control is **2 of 8**;
* this window's sixteen recorded runs span **+1.72% to +17.36%**;
* the three add-receiving names netted **+$283** between them on ~$1,153 of adds (2 of 3 worked);
* **NVO -$213.32** took a stage-1 add at +15.2% and reversed — the real risk of this lane, and not
  dismissible at n=1.

**Reverting to 12 is NOT a safe default.** It restores a lane that provably cannot add at all. The
arithmetic defect in §2 #4 stands regardless of this run's P&L.

---

## 6. THE OTHER STRUCTURAL FINDING — CAPACITY IS SPENT BY DAY THREE

Following SNDK (**+166.10%** that window) through bt 424219:

```
V31.2 CONCENTRATE: funded 3 of 4 by conviction (SNDK@$863, ...)      <- sized CORRECTLY
SATELLITE CAP: SNDK trimmed $863 -> $235 to keep the core at target  <- cut to a runt
SKIP BUY SNDK — cash_to_use $233.13 < min $370                       <- refused outright
```

`open_pos=6` against `max_positions=6`, core already drawn to ~14% of NAV, because:

```
01-02  ETH $840, PANW $831, SNDR $840, SON $838  + SPY core $2,398 (40% of NAV)
01-06  AMCR $862, AMD $831
```

**Six slots and 100% of capital, committed in three sessions of a thirty-nine-session window.** Same
shape in bt 584886 (7 names in 4 of 22 sessions) and bt 571147 (whole book by day 5, then SNDK gets
$101 while $582 goes into SPY *on the same bar*).

The runt chain is **conviction sizes right → satellite cap trims → min-position floor refuses**.
Three gates, name gets nothing. Not a queue problem, not a ranking problem — a **capacity** problem.

**First measurement on the participation gap** (sampled at the 22 buy-gate lines in bt 584712, the
window that never beats SPY): cash at the gate **median $1,130 (18.8% of NAV), p75 $3,382 (56.4%)**,
open positions median 5 of 6, core logged `deploy_below_min: core 1.2% vs target 10.0%`. In a month
SPY compounded +13.10%. **Flagged as a lead, not a conclusion** — 22 gate lines is a biased sample.

---

## 7. PRODUCTION / REAL MONEY — THE GATE'S OWN VERDICT

The operator asked to "make it production ready with real money." **I did not, and this is why.**
`python3 scripts/assess_live_readiness.py` — the repo's own promotion gate, which writes nothing:

```
LIVE READINESS - alpaca-main (real money)
state          : RESEARCH          checks passed : 0/6

BLOCKING:
  [FAIL] secrets             secret_migration, rollback
  [FAIL] research_integrity  history_months, point_in_time_provenance, unseen_months,
                             regime_count, purged_folds, sealed_holdout, trial_count,
                             predeclared_repeats, median_repeat, median_active, bootstrap,
                             information_ratio, deflated_sharpe, max_drawdown, beta,
                             profit_factor, unseen_quarters, parameter_stability,
                             leave_one_winner_out, concentration_analysis
  [FAIL] execution_safety    lifecycle_chaos, dependency_chaos
  [FAIL] risk_state          restart_state
  [FAIL] operations          watchdog
  [FAIL] paper_observation   paper_build, paper_days
```

**Four blockers that no amount of code can clear today:**

1. **Every backtest is `pit_mode='research'` = lookahead** (`interactive_utils.py:5607`). Zero PIT
   manifests exist. **The entire evidence base — +21.27%, +17.36%, all of it — is not
   promotion-eligible.** This is disqualifying on its own.
2. **0 of 60 paper-trading days.** Calendar-bound. Cannot be shortened.
3. **`sealed_holdout_preregistered = False`.** A sealed holdout is only valid if registered *before*
   it is looked at. Every window in the data has now been looked at. This one is arguably
   unrecoverable without new price history.
4. **Six gate criteria are currently UNCOMPUTABLE** — max_drawdown, beta, information_ratio,
   deflated_sharpe, profit_factor, unseen-quarter fraction — because **a backtest emits no equity
   time series**. `portfolio_value_high/_low` are run extremes, not a peak-to-trough pair.

And on the objective's own terms the strategy does not yet earn promotion: **0/3 on beating SPY in a
bull market**, 10/24 at 1x pace, with results that do not reproduce (0/18 name overlap on repeats).

`Instances.<id>.live_readiness_report` still has **no writer anywhere in `backend/`**.
`doc-179 / alpaca-main` is real money, stopped, and per the objective **nothing ships there without
explicit sign-off**. I did not touch it.

**Shortest honest path to real money:** (a) build PIT capture so backtests stop being lookahead;
(b) emit an equity curve on the result row so six gate criteria become computable; (c) pre-register a
sealed holdout on genuinely unseen history; (d) run 60 paper days on the exact promoted build;
(e) rehearse secret migration, rollback, lifecycle/dependency chaos, restart-state recovery; (f) write
the `live_readiness_report` writer. **(d) alone is a hard 60-day floor.**

---

## 8. NEXT STEPS, IN PRIORITY ORDER

1. **Evaluate `anchor_reinforce_target_pct=20` on the bear, OOS and non-semi windows** — three runs,
   *not* another reference-window run. It is the only lever that has ever put real size on a winner.
   Grep `ANCHOR ADD:`; watch specifically for the NVO failure mode (add at +15%, then reversal).
2. **Fix §2 #5** — `_winner_add_budget_cap = _stock_budget_available * 0.40` yields $170–$250 against
   a $234 stage-1 add. It fired `none funded` **36 times** in bt 633644. Same class of bug, already
   diagnosed, now greppable.
3. **Bull participation** — the only clause failing outright (0/3). Start from §6's cash-drag lead,
   but measure the per-bar invested fraction properly, from position values, not gate lines.
4. **Emit an equity time series on the result row.** Unblocks six promotion criteria *and* makes
   drawdown comparisons real. Cheap, and it is on the critical path for §7 regardless of strategy work.
5. **Stage the deployment** (§6). The only remaining structural lever that does not require ranking
   names against each other — which is measured useless (`r = -0.235`).
6. **Bear-regime core saw-tooth** — 40 SPY fills, **3.72× NAV** gross in bt 789099 against 0.21× in
   the hedge-free OOS run. Largest remaining churn source, concentrated in one regime.

---

## 9. RUN INVENTORY — THIS SESSION

| bt | window | config delta | result | vs SPY | verdict |
|---|---|---|---|---|---|
| 584886 | non-semi `06-01..07-01` | baseline | +3.09% | -1.71% | overfit test **PASSED** on alpha, below 1x |
| 584712 | OOS `03-30..04-27` | fresh-low N=2 + rally-onset | +12.34% | +13.10% | SQQQ $0, maxDD 11.4→5.8%; return inside noise |
| 789099 | bear `03-02..03-30` | same | **+21.27%** | -7.86% | safety check **PASSED**: 0 blocks, SQQQ +$918.78 |
| 424219 | reference `01-01..03-01` | + `bfq_conviction_target_weight_pct=0.14` | +4.10% | +0.24% | **REVERTED** — pool-bound |
| 633644 | reference `01-01..03-01` | + `anchor_reinforce_target_pct=20` | +5.61% | +0.24% | lane **FIRED** (5 adds); **REVERTED**, not proven |

---

## 10. CONFIG LIVE ON doc-193 RIGHT NOW

```
residual_sleeve_bear_block_at_fresh_low_bars  2      <- NEW this session, KEEP
regime_rally_onset_enabled                    true   <- NEW this session, KEEP
anchor_reinforce_target_pct                   12     <- reverted; 20 is the tested value
bfq_conviction_target_weight_pct              absent <- reverted, do not re-add
core_min_pct 0.10   core_max_pct 0.40   total_spend_cap_target_weight_pct 0.14
min_position_nav_pct 0.06   min_position_size 100   max_positions 6
momentum_swap_vs_portfolio_enabled false   (rotation OFF — keep)
history_scope_salt 'let-run-core-193'      (STILL never varied between arms)
```

---

## 11. TOOLING BUILT THIS SESSION (all committed)

| script | what it answers |
|---|---|
| `scorecard.py` | every finished run vs the SPY return of its own window, marked against both objective clauses |
| `benchmark_window.py` | what SPY/QQQ did in any window, from cached bars |
| `summarize_backtest.py` | the handoff table, with the **sleeve broken out** from the stock book |
| `set_doc_config.py` | change doc config with a printed diff **and a read-back** |
| `check_range_position.py` | offline 20-day range-position replay (PIT-aware) |

**THE TECHNIQUE WORTH KEEPING: `AlpacaBarsCache` in RethinkDB is readable offline.** Three of this
session's five findings cost **zero backtests** — the fresh-low kill test, the whole benchmark
column, and the refused-vs-bought basket comparison. Runs are the scarce resource; check whether the
bars already answer the question.

---

## 12. MANDATORY RUN PROCEDURE

```bash
python3 scripts/check_deployed_code.py                                   # hash FIRST
python3 scripts/set_doc_config.py 193 --set KEY=VALUE --apply            # diff + read-back
python3 scripts/reset_backtest_event_state.py --instance v2-let-run-core --apply
python3 scripts/run_validation_backtest.py <start> <end> --cash 6000 \
        --granularity 3600 --instance v2-let-run-core
# poll: python3 scripts/pull_backtest_logs.py <id> --summary   (free)
# read: python3 scripts/summarize_backtest.py <id>
# DO NOT git push until status != 'running'
```

The reset is not optional: `GraphNexusActiveEvents` **and** the `turnover_ledger` (inside
`NexusStrategyCache`) are inherited between runs; tick-1 turnover was 56–72% of a 50% budget before a
single decision. Without it you are measuring the previous run.

---

## 13. WHERE THE EVIDENCE LIVES

New this session, all under `docs/investigations/`:
`fresh-low-verification.md` (the offline kill test + what the gate does not cover),
`584712-fresh-low-result.md` (gate works, +21.9% counterfactual was wrong),
`789099-bear-safety-check.md` (gate inert in the real bear),
`sndk-100-dollars.md` (SNDK at $100 while $582 went to SPY),
`satellite-capacity-584886.md` (refusals are capacity; displacement measured useless),
`424219-bfq-target-weight-reverted.md` (the pool is the wall),
`anchor-target.md` (**the fourth instance + the run that proved the lane can fire**),
`scorecard.md` (where the alpha comes from).

Prior session: `fix-audit-levers.md` has a 63-row WORKS/INERT/BACKWARDS verdict table for every
doc-193 key. **Read it before enabling anything.**
