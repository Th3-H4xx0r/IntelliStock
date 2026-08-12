# Next conversion experiment priority — stage satellite entries, do not rank replacements

**Date:** 2026-08-10
**Scope:** independent read-only audit of the current objective, handoffs/investigations, current code, and the real backtest artifacts already present under `/tmp`. No config was changed, no backtest was launched or controlled, no external provider was called, and no trading code was edited. This document is the only output.

## Decision

The next causal experiment after the anchor **mechanical** execution wiring is:

> **Stage new satellite-position entries by requiring six market sessions between confirmed new-symbol fills. Keep unspent risk in the SPY core. Do not rank or trim existing holdings to make room.**

Proposed opt-in key:

```yaml
satellite_entry_cadence_min_sessions: 0   # default OFF; 0/absent = current behavior
# treatment only:
satellite_entry_cadence_min_sessions: 6
```

This is one treatment, not a search over cadences. Six is fixed before seeing a result: at a 14%-of-NAV entry clip it permits about four new positions, or 56% accepted-buy notional, during a 21-session turnover window, then permits positions five and six later in a 39-session reference window. That matches the stated mechanism (roughly four positions that matter) without tuning names or sectors.

The experiment outranks entry-rule work, trim/displacement, passive execution, and another SQQQ rule because the binding fact is already measured: the allocator produces full-size conviction requests, but the first six or seven arrivals consume all slots and floor-bounded satellite capacity in three to four sessions. Later candidates cannot obtain an executable clip. A new entry rule has no exposure while that remains true; displacement has no measured ranking edge and adds a sell plus a buy; passive execution attacks tens of basis points while non-fill can lose an entire mover; and the currently armed SQQQ fresh-low rule has already separated the known false-bear and bear windows.

This does **not** claim staged deployment will improve P&L. It is the highest-value remaining identifiable test because it changes only *when capacity becomes available*, the variable implicated by the evidence, without pretending the system can rank future winners.

## Evidence audited

### Objective and latest state

* `docs/OBJECTIVE.txt` says conversion, not discovery, is the gap; requires at least three paired windows, including OOS and non-semiconductor leadership; sets a 4.94pp noise floor; requires default-OFF behavior and real log verification.
* `docs/handoffs/2026-08-10-benchmark-and-capacity.md:120-133` independently places staged deployment first: six slots and all capital are committed in the first 8% of a window, while conviction-ranked displacement is measured useless.
* `docs/handoffs/2026-08-10-sizing-pattern-and-readiness.md:293-308` puts anchor plan-to-fill correctness first, then bull participation/staging, and identifies bear core saw-tooth as the remaining regime-specific churn source.
* The anchor prerequisite is now mechanically real, but only mechanically: `/tmp/bt735390-stopped.log` contains 9 `ANCHOR PLAN`, 1 correlated `ANCHOR ORDER`/`ANCHOR FILL`, a UUUU quantity increase, and an explicit partial stage. The run was stopped at 33.33%; it is not causal P&L evidence. Its book had already reached 81.5% accepted-request turnover before the first anchor, so stacking an anchor-return study on the unstaged opening book would confound reinforcement with exhausted capacity/turnover.

### Real `/tmp` run artifacts

The available logs were read directly, not inferred from config:

| run artifact | window/status | relevant observed signatures |
|---|---|---|
| `/tmp/bt201039.log` | reference, finished, +8.34% | 99 extension-block lines across the two entry paths, 27 satellite skips, 47 satellite trims, 42 overflow lines, 569 turnover-binding lines; SNDK filled only on 02-02 at $660.48 |
| `/tmp/intellistock-anchor-analysis/584712.log` | OOS bull, finished, +12.34% | 43 satellite skips, 8 trims, 16 fresh-low/rally-onset bear-leg skips, zero SQQQ fills, zero passive signatures |
| `/tmp/bt633644-review.log` | reference, finished, +5.61% | four alpha fills on 01-02 and two more on 01-06; 50 satellite skips, 32 trims; five old anchor planner allocations but zero anchor fills |
| `/tmp/bt615886-0342.log` | OOS bull, finished, +9.02% | 10 satellite skips, 17 trims, 46 overflow lines, 279 turnover-binding lines; zero anchor fills and zero passive signatures |
| `/tmp/bt735390-stopped.log` | reference, stopped at 33.33%, +10.55% partial | same four alpha fills on 01-02 and two on 01-06; 9 anchor plans, 8 blocks, one $150.61 UUUU add fill; no passive or SQQQ fill |

Across all five available real logs, `passive` has **zero** signatures. The available artifacts therefore validate marketable execution only.

## What is already measured, what failed, and what remains unproven

### 1. Entry timing

**Measured**

* Entry lateness is strongly associated with outcome: pooled fraction-of-move elapsed at fill versus capture is `r=-0.895` (`timing-per-stock.md`), and capture versus actual entry is effectively 99.99%. In bt 201039, SNDK discovery latency accounts for 97.1 of 107.3 points of lateness; it was invisible until late, then filled at $660.48.
* The current range-width extension metric is direction-blind and decays. Current code still defaults to `entry_extension_metric="range"` (`graph_nexus_analysis.py:9304-9337`), while bull/recovery profiles set the threshold to zero. Real `/tmp` logs show it blocking early signals and later admitting worse prices.
* An offline 20-day-high freshness tie-break was earlier than trailing-return rank in 4/5 windows and on big movers in 5/5, but it fires on 62% of the universe and has no real-run causal exposure. Current run schemas in `/tmp` have `momentum_breakout_freshness_pct=0`.

**Failed / do not retry**

* Globally loosening the extension gate lost 7.95% on the blocked portfolio.
* MA-distance as a late-entry blocker failed; consolidation/VCP delayed every large entry; pullback/higher-low entry was worse in 4/5 windows; a tight first-breakout-only rule was mixed/confounded.
* Nothing observable at entry cleanly separates winners from losers. XOM/NTR/VOYA had the same bar, score, lane and size but returned +26.9%/+21.2%/-8.9%.

**Unproven**

* No entry-timing rule has a paired real-run fill/P&L result on the required window set.
* More importantly, an earlier trigger cannot matter if all executable slots/capital are already consumed. Testing it first risks another correctly firing but exposure-zero lever.

**Priority consequence:** do not invent another discovery/entry filter now. Make later entry capacity exist first.

### 2. Satellite allocation and trim-back

**Measured**

* Reference bt 424219: six names and all capital were committed in three sessions. SNDK was correctly sized at $863, satellite-trimmed to $235, then refused below the $370 minimum-position floor.
* Non-semiconductor bt 584886: ten raw>=1.50 names requested $8,358, were trimmed to $960 total, and none filled; three more were skipped. Seven earlier names consumed the book in four sessions. The highest score, MRVL raw 1.888, got $105 while earlier AAPL got $2,771.
* The same shape is visible in `/tmp/bt633644-review.log` and `/tmp/bt735390-stopped.log`: four alpha fills on 01-02 plus two on 01-06, before a late leader can arrive.
* Current `satellite_design_share`/`satellite_max_share` and broker headroom logic cap *entry* requests (`core_sleeve.py:218-315`, `broker.py:15591-15724`). There is no standing portfolio trim. `test_A11_an_appreciation_overrun_never_recovers` explicitly records that an appreciated overrun is stable and untrimmed.
* The existing `deployment_ramp_*` is not session staging: `_get_deployment_ramp_bar_index` advances on each unique bar, and `_compute_available_buy_budget` applies only three caps before returning to 100% (`graph_nexus_analysis.py:11380-11495`). At 3600-second granularity it is exhausted within the opening session, so it cannot preserve capacity across days.

**Failed / do not retry**

* Conviction-ranked displacement is unsupported: in bt 584886 the refused basket mean return was +2.41% versus +3.46% for bought names, and raw score versus return was `r=-0.235` (n=15). Rotation was already measured at -3.04%, and a replacement is two-way turnover.
* Raising `max_positions` caused latched breach/forced-liquidation churn and diluted the prize.
* `bfq_conviction_target_weight_pct=0.14` changed a formula but remained pool-bound and was reverted.
* `satellite_conviction_reserve_pct=0.15` is not clean staging. It did remove one opening position ($3,780 to $2,880), but its only run was stopped/confounded, max drawdown reached 11.4%, and the reserve did not guarantee that a later candidate received an executable clip.

**Unproven**

* No completed paired run has tested temporal staging while preserving the core and leaving held positions alone.
* No evidence supports selecting a specific existing holding for trim-back. Automatic trim-back also risks selling the very appreciated winner the objective says to hold.

**Priority consequence:** test time-based capacity staging, not merit-ranked displacement or standing winner trims.

### 3. Passive execution

**Measured**

* The default-off code exists and unit tests pin a pessimistic midpoint limit: a buy fills only if the ask reaches the decision-price limit, fills at the limit, and otherwise expires (`simulated_execution.py:503-540`; `test_passive_limit_execution.py`). A passive fill books zero spread/slippage.
* The modelled saving is 22.8 bps per filled side. This is a real cost lever, but timing forensics price the entry-lateness prize at roughly $885-$1,939 versus about $115 total signal-to-fill cost in bt 201039.
* The model overcharges SPY relative to its own approximate 5bp quoted spread while probably undercharging thin satellite names. The aggregate 22.8bp number is not a per-symbol live guarantee.

**Failed / limitations already visible**

* There is no failed P&L run because there is **no passive run at all** in the available evidence.
* Observability is not ready for the required verdict: `expired_order_count` exists as an in-memory property, but `NextEventExecutionSimulator.execution_summary()` reports pending/rejected counts and omits the expired count (`simulated_execution.py:628-640`). That contradicts the handoff requirement to report expiries beside every saving claim.

**Unproven**

* Fill rate, side-specific expiry rate, missed-winner P&L, stale sell risk, and live/backtest parity are all unmeasured. A single missed 14%-NAV mover dominates a 22.8bp saving.

**Priority consequence:** high-value cost experiment later, after expiry is persisted/logged. It is not the next conversion experiment.

### 4. SQQQ bear leg

**Measured**

* `docs/OBJECTIVE.txt` saying the leg has never profited in a bear is now stale. The same known bear window has three profitable runs: SQQQ +$889 (bt 542754), +$965 (321638), and +$919 (789099); bt 789099 returned +21.27% versus SPY -7.86%.
* The current N=2 fresh-low/rally-onset gate is mechanically selective: bt 584712 logged 12 fresh-low plus four rally-onset skips and traded zero SQQQ; bt 789099 logged zero such blocks and reopened the known profitable leg at the same bar/price.
* Removing the false-bear SQQQ loss reduced deterministic hedge exposure and max drawdown (11.4% to 5.8%), but the treatment book changed and return moved -1.01pp, inside the noise floor. It was not a causal return win.
* Code still correctly keeps the bear profile absent: arming the core in bear routes de-risk to cash and suppresses the inverse leg (`core_sleeve.py:163-215`, `broker.py:5003-5029`). The leg has a default-off fresh-low open gate, stop, hold-through-chop, ratcheted allocation, pending-order accounting, and exits (`broker.py:2841-2929`, `4565-4745`, `5075-5264`).

**Failed / contrary evidence**

* The historical stress audit across 45 episodes found only 18 winners (40%) and -$87,736 on $100k. The observed bear success is one 21-day episode at the 88th percentile of duration, repeatedly replayed, not independent generalization.
* The believed 35% allocation was never tested in a bear; real bear logs ran at 70%.

**Unproven**

* Independent OOS bear profitability remains unproven. The bear core/SQQQ system also generated 3.72x NAV of SPY gross in bt 789099, the largest remaining regime-specific churn source.

**Priority consequence:** preserve the current bear leg in this experiment and add a bear no-op safety pair. Do not mix another bear rule into a bull/chop capacity test.

## Pre-registered experiment

### Causal mechanism

When `satellite_entry_cadence_min_sessions=6`:

1. The first **new satellite symbol** may be admitted immediately.
2. After an accepted new-position order, a pending reservation prevents other same-bar orders from slipping through before next-event execution. The six-session clock starts only on a confirmed fill; rejection/cancel/expiry clears the reservation.
3. Until six distinct market sessions have elapsed, every other *new-symbol* satellite buy is blocked before allocation spend, core funding release, turnover booking, or broker order submission. It remains eligible for the existing queue/re-evaluation.
4. Held-name adds (including anchor plans), all sells/risk exits, SPY core orders, and the SQQQ sleeve are outside this gate. For this experiment anchor execution remains OFF in both arms so its unproven P&L/turnover does not confound staging.
5. The capital not released to a satellite remains in the residual SPY core, not idle cash. No existing holding is sold merely to make room.
6. Scope is bull/chop/recovery only. Bear/crash must be byte-inert.

The authoritative broker choke point and strategy/core funding pre-pass must agree. A broker-only block that still spends allocator budget or releases SPY is not treatment exposure; it is another false-plan/churn defect.

### Arms held fixed

| arm | only config difference |
|---|---|
| control | key absent or `satellite_entry_cadence_min_sessions=0` |
| treatment | `satellite_entry_cadence_min_sessions=6` |

Hold anchor execution OFF, passive execution OFF, rotation OFF, fresh-low N=2/rally-onset ON, current SQQQ allocation/config, entry/rank rules, core floor/target, max positions, 3600-second bars, $6,000, cost model, and deployed build identical.

Each arm needs its own clean event/cache namespace **and the same immutable discovery/bootstrap snapshot**. Separate salts alone are insufficient: the anchor audit observed different names and snapshot inheritance before treatment could act. Require identical bootstrap/discovery hashes and identical candidate tape until the first cadence block; otherwise report only mechanical exposure and do not attribute a return delta.

### Paired windows

Run control/treatment pairs on the same instance/build/granularity/cash:

| window | role | known benchmark/evidence |
|---|---|---|
| `2026-01-01..2026-03-01` | reference; direct three-session capacity evidence | SPY about +0.24%; current book fills four names on 01-02 and two on 01-06 |
| `2026-03-30..2026-04-27` | OOS bull and the outright benchmark-failure window | SPY +13.10%; strategy 0/3 beats SPY |
| `2026-06-01..2026-07-01` | OOS **non-semiconductor-led** test | leaders already measured as RXD/ATEN/UAL; SPY -1.71%; bt 584886 +3.09%; seven-name capacity spent in four sessions |
| `2026-03-02..2026-03-30` | bear safety/no-op pair, not efficacy sample | SPY -7.86%; treatment should emit zero cadence blocks and leave SQQQ behavior unchanged |

Do not optimize the six-session value after the first pair. A different cadence is a new experiment.

### Required treatment-exposure signatures

Add unambiguous, greppable states; a config read is not evidence:

```text
SATELLITE CADENCE ADMIT: X new_position session=... last_fill=... gap=... required=6 requested=$...
SATELLITE CADENCE PENDING: X order_id=... prevents same-bar second admission
SATELLITE CADENCE BLOCK: Y new_position gap=2/6 requested=$... raw=... queued=true
SATELLITE CADENCE FILL: X order_id=... session_index=... notional=$... nav_pct=...
SATELLITE CADENCE CLEAR: X order_id=... reason=reject|cancel|expire
SATELLITE CADENCE SUMMARY: new_fills=N min_gap=... blocks=N later_filled=N sat_weight_d3/d5/d10/d20=... peak_21d_request_turnover=...
```

Also reconcile the existing `V31.2 [CONCENTRATE]`, `[core] funding/released`, `SATELLITE CAP/OVERFLOW`, turnover block/bypass, buy-gate, order, and `[execution] FILL BUY` lines.

**Mechanical exposure required:**

* OFF arms emit zero cadence lines and preserve current behavior.
* Treatment has no pair of confirmed new-symbol satellite fills fewer than six market sessions apart, including the opening batch.
* Reference and non-semi treatment arms show blocks before the dates on which their controls exhaust the book, and retain at least one >=10%-NAV executable clip of floor-bounded headroom beyond session 10.
* At least one >=10%-NAV new satellite fill occurs after session 10 in each of those two treatment windows; otherwise staging only underinvested and did not create later conversion.
* A cadence-blocked order causes zero core release, zero turnover-ledger booking, zero stage/allocator spend, and no submitted order.
* Bear safety arm emits zero cadence blocks/admissions and preserves the SQQQ open/skip path.

### Outcomes and decision rule

Read both accepted-request turnover and realized fill notional; do not call the former “traded.” For every pair report:

* return, SPY return/alpha, max drawdown;
* new-symbol fill dates, initial and median NAV weight, session-10/session-20 satellite weight;
* raw>=1.50 full-size requests, fills, capacity/cadence blocks, and block-to-later-fill conversion;
* cash, SPY core weight, and total invested weight by session;
* satellite, core, SQQQ, and whole-book accepted-request and realized turnover separately;
* per-lot P&L for entries admitted after session 10.

**Promotion bar:** all mechanical clauses pass; treatment improves control in at least 2/3 efficacy windows; mean return delta is at least +4.94pp; neither OOS nor non-semi treatment regresses by >=4.94pp; mean SPY alpha is not worse; max drawdown and whole-book turnover do not worsen materially. Anything smaller is below the measured harness noise and remains research-only.

### Stop / reject rules

Stop the active arm or reject the lever immediately if any occurs:

1. a second new-symbol order/fill slips through inside six sessions, or a rejected/expired order wedges the cadence;
2. a cadence block consumes allocator budget, records turnover, releases core, or suppresses a sell, held-name add, SPY, or SQQQ action;
3. cash exceeds 10% of NAV for two consecutive completed sessions after the initial session solely because staged capital failed to remain in the core;
4. matched-through-time whole-book accepted-request or realized turnover exceeds control by >10%; this would falsify the expected turnover direction and likely indicate new core saw-tooth;
5. max drawdown exceeds 15% or exceeds the paired control by >5pp during a run;
6. no exposure separation is visible by the control's capacity-exhaustion date (reference session 3; non-semi session 4);
7. candidate/bootstrap hashes or the pre-treatment candidate tape differ between arms—finish only as a non-causal diagnostic, not a return comparison;
8. after completion, treatment trails control by >=4.94pp in two efficacy windows, or either required OOS/non-semi window loses >=4.94pp while conversion exposure did not improve.

## Anticipated turnover impact

The hypothesis is **lower peak rolling turnover, not necessarily lower lifetime notional**:

* Current reference evidence books roughly four 14% opening requests (~56% NAV), then two more a few sessions later, reaching about 80% before any anchor. The proposed cadence admits roughly four clips per 21 sessions (~56%) instead of six in three sessions.
* With the same eventual six entries, full-window satellite buy notional can remain near 84% of starting NAV. The experiment spreads it; it does not claim those dollars vanish.
* It creates no displacement sells, so it avoids the extra sell+buy round trip of rotation/trim-back. It should also eliminate unfillable $82-$235 runt attempts while the cadence is closed.
* Because unspent capacity stays in SPY, later admissions require staged core releases. Core gross could merely shift in time or even rise if the pre-pass/deploy paths disagree. That is why core turnover is reported separately and a >10% whole-book increase is a hard reject.

## Why this is the one next experiment

Anchor wiring answered “can a planned add become a correlated fill?” once; it has not answered whether adding at exhausted turnover improves returns. The next question should not be another score, another ticker lane, or another way to sell an incumbent. Existing evidence already identifies the causal bottleneck upstream of all of them: **the book awards the full two-month capacity to the first few sessions.** A default-off, fill-acknowledged session cadence is the smallest experiment that makes later conversion possible while respecting the objective to hold winners, the negative displacement evidence, and the turnover constraint.
