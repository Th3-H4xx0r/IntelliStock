# fix-generalize — which levers survive a second window, and which are curve fits

Read-only audit, 2026-08-09. **No code edited, no run started or stopped, nothing pushed,
`docs/OBJECTIVE.txt` untouched.** Every number below is grepped out of a log on disk or a
run sidecar in `backtests/`, or computed here from those files.

Inputs: `_RUNS4.md` (current config), `_SYNTHESIS.md`, `gap-bull/-capital/-target/-winners/
-oos/-bugsweep`, `dd-drop`, `hold-check`, `sweep2`, `sndk-priority-block`, `ext-still-blocking`,
`discovery-and-ranking`; logs `820236 342380 613166 915207 542754 383778 427197 571147`.

---

## 0. THE HEADLINE, BEFORE THE DETAIL

Three things are true and all three are worse than the brief assumes.

1. **bt 915207 / 427197 / 571147 are the same window AND not the same experiment.**
   915207 is a **pre-commit** run (`SKIP BUY CFG — cash_to_use $1.69 < min $50`, legacy
   `recent runup` extension wording). 427197/571147 are post-89e71f3 (`min $366-$406`,
   `range ... [bars=N]`). Opening-book overlap 915207 vs 427197 is **0/4**. Per
   `_SYNTHESIS.md` the noise floor is >= 4.94pp. **There is no baseline in this session.**

2. **The bear result everyone quotes was produced at a setting that is no longer in the
   config.** `542754.log` line: `[sleeve] parked $3166.14 in BEAR leg SQQQ @ 69.50
   (regime=bear, leg=3166/4221 cap, alloc=70%)`. **542754 ran at alloc=0.70, not 0.35.**
   So did 342380 (+18.71%): `leg=4200/4200 cap, alloc=70%`. Only 383778 ran at 0.35
   (`leg=2100/2100 cap, alloc=35%`) — and that is the run where the hedge **lost** $257.
   **`residual_sleeve_bear_alloc_pct=0.35` has never been run in a bear window.**

3. **The whole dataset is one semiconductor bull market.** bt 983687 (2025-11-01..2026-05-26)
   top movers from its own sidecar: `AAOI +400%, LITE +351%, MU +303%, TSEM +239%,
   INTC +210%, VIAV +204%, NVTS +136%, PLAB +127%, RKLB +127%, SKYT +114%`. W1's #1 is SNDK
   (+166%, NAND). W3's #1 and #3 are **AAOI (+48%) and AXTI (+16%)** — optical/compound
   semiconductor. **The "OOS" window is also semiconductor-led.** The only two windows in
   the available data that are not are the **bear (2026-03-02..03-30)**, whose leader is an
   inverse ETF, and **2026-06-01..07-01** (bt 433466: `RXD +86%, ATEN +26%, MU +19%,
   UAL +18%`).

---

## 1. THE EXPERIMENT MATRIX — what has actually been run, on what code

Run start times are UTC off the log's own first line; commit times converted from
`git log --date=iso-local` (-0700). Independently confirmed by log signature in every row.

| run | window | regime | code cut | `core_min` | sleeve alloc | result | max DD |
|---|---|---|---|---|---|---|---|
| 820236 | W1 01-01..03-01 | bull/chop | <= 098cd64 | 0.30 | — | +12.33% | n/a |
| 342380 | W2 03-02..03-30 | **bear** | <= 098cd64 | — | **0.70** | +18.71% | n/a |
| 613166 | W1 | bull/chop | pre-a2054c6 | 0.25 | — | +9.17% | n/a (cache-poisoned) |
| 915207 | W1 | bull/chop | **pre-89e71f3** | 0.25 | — | +9.70% | **3.78%** |
| 542754 | W2 | **bear** | pre-cd630af | (core off) | **0.70** | +11.94% | 5.07% |
| 383778 | W3 03-30..04-27 | OOS bull | pre-cd630af | 0.25 | **0.35** | +4.75% | 6.84% |
| 427197 | W1 | bull/chop | pre-cd630af | **0.10** | — | +8.60% @84% | **11.74%** |
| 571147 | W1 | bull/chop | **cd630af** | **0.10** | — | +4.89% @61% | **10.59%** |

Signature proofs for the code cuts: `$50` vs `$341-$406` min-position floor
(915207 vs the rest); `recent runup` vs `range ... [bars=N]` (915207 vs the rest);
`PEAK GIVE-BACK` and momentum-lane `Backfill queue ADD: SNDK` (**571147 only**).

**Read the last three rows.** Only ONE run in this repo contains the two newest levers, it is
on the January window, it is 61% done, and its realised max drawdown is **2.8x** the
0.25-floor run on the same calendar.

---

## 2. LEVER-BY-LEVER VERDICT

| lever (current value) | run coverage | window coverage of the EVIDENCE | verdict |
|---|---|---|---|
| `momentum_swap_vs_portfolio_enabled=False` | all | defect measured 5/5 rotations, `fired=0/4` on 57/57 bars, 3 windows | **KEEP** |
| `watchlist_priority_slots=0` | all | inert on 121/121 audit bars, 4 runs, 3 windows | **KEEP (as 0)** |
| `max_positions=6` | all | `MAX_POSITIONS_GATE: blocked` = **0** in 915207/542754/383778/427197/571147 | **KEEP — slack, not binding** |
| `momentum_scan_cached_bars=True` | all post-8d66404 | 276/150/126 momentum discoveries, 3 windows | **KEEP** |
| `momentum_missing_60d_excluded=True` | all post-a2609bd | 0 fabricated `60d=+0.0%` of 552; 613166 had 39/182 | **KEEP** |
| `total_spend_cap_target_weight_pct=0.14` | all | long-standing; not a session lever | **KEEP** |
| `entry_extension_metric="range"` | 542754/383778/427197/571147 | reading is **noise** on 3 windows | **KEEP FOR NOW, A/B NEXT** |
| `core_min_pct=0.10` | **427197, 571147 — W1 only** | mechanism 2 windows; **dollars 0 windows** | **HALF-SHIPPED — see §3.4** |
| `bfq_include_momentum_lane=True` | **571147 only** | defect 3 windows; **payoff 1 run, capped at $100** | **KEEP, CLAIM NOTHING** |
| `momentum_rank_on_60d=True` | all post-a2609bd | IC evidence is **1 run**, and does not replicate | **UNPROVEN — §4.2** |
| `peak_giveback_*=30/25` | **571147 only** | 1 name, 1 bar, 1 window; **and it does not execute** | **REVERT — §4.1** |
| `residual_sleeve_bear_alloc_pct=0.35` | **383778 only** | the +11.94% bear was **0.70** | **REVERT OR GATE — §4.3** |

---

## 3. (1) LEVERS SAFE TO KEEP

### 3.1 `momentum_swap_vs_portfolio_enabled=False` — keep
This is a *disable* of a defect measured 5 for 5 (`_SYNTHESIS` #5: every rotation sold and
failed to buy; $4,277 out, $87 back). `gap-bugsweep` confirms `rot fired=0/4` on 57 of 57
bars across three windows. Turning a convicted defect off cannot be a curve fit.

### 3.2 `watchlist_priority_slots=0`, `max_positions=6` — keep
Both are *absences*. `watchlist=none` on 121/121 `Priority sizing order` audit bars across
4 runs (`sweep2` §1: `sector_watchlist={}` makes `priority_tickers` unconditionally empty).
`MAX_POSITIONS_GATE: blocked` = **0** in all five current runs (my grep; 45 lines only in
the old 820236 build). Neither can fit anything because neither does anything.

### 3.3 `momentum_scan_cached_bars`, `momentum_missing_60d_excluded` — keep
Cheap, mechanism-level, multi-window signatures (276/150/126 discoveries; zero fabricated
`60d=+0.0%` against 39/182 in the poisoned 613166). These are bug fixes, not tuning.

### 3.4 `core_min_pct=0.10` — keep the direction, but it is HALF-SHIPPED and unvalidated
where it can hurt

**What generalises (2 windows, arithmetic):** the old band `core_target 0.35 - core_min 0.25
= 0.10*NAV` is smaller than one clip `0.14*NAV`, on every bar of both bull windows. Measured
off the logs: band $618/$618/$620/$626/$635 (915207) and $601/$603/$622/$622 (383778) vs
clips $795-$921. 48 `SATELLITE CAP ... skipped` events, none with >= $50 of room.
`542754` (bear) has **0** cap events and **0** funding trims — the change is structurally
inert in bear. That part is solid.

**What does NOT generalise:**

* **The dollars are a counterfactual on one window** and the three docs disagree with each
  other on its size — `gap-capital` says +3.4pp, `gap-bugsweep` +10.2pp, `gap-winners`
  +10.4pp, `gap-target` +11.1pp — all reconstructing the *same* SNDK trade.
* **Two-thirds of the money it frees is bought straight back as SPY.** Release -> re-buy,
  measured by me on all five runs (SPY buys ex-opening-deploy / SPY sells):
  915207 **93%**, 427197 **68%**, **571147 69%**, 542754 40%, 383778 70%.
  `sweep2` §2c named the cause (`core_sleeve.py:517-542` `band_deploy` has no knowledge the
  gap was opened on purpose). **That half was never shipped.** So the lever is 1/3 of itself.
* **It bought churn.** `TURNOVER BUDGET BINDING` median, same window:
  915207 **88%** -> 427197 **104%** -> 571147 **107%**, against a ~50%/mo documented
  break-even. Max 92% -> 111% -> 127%.
* **Its sign flips with SPY's window return, and the flip case is untested.** SPY returned
  **+0.64%** in W1 (the core is free to release) and **+12.79%** in W3 (it is not).
  `core_min_pct=0.10` has been run on W1 twice and on W3 **never**. `gap-bugsweep` states it
  outright: in 383778 the core earned +$129 on ~$1,700 and cutting the floor gives up ~$74.
* **Drawdown.** 3.78% (0.25) -> 11.74% / 10.59% (0.10) on the same calendar. *Honest caveat:*
  `dd-drop` §4 proves this is SLV, whose $840 clip is identical under both floors, and the
  opening books do not overlap — **the DD tripling is not cleanly attributable to
  `core_min_pct`.** It is, however, the observed risk of the current config.

**Verdict:** not a curve fit — the mismatch is arithmetic and window-independent. But it has
**zero windows of realised benefit**, it is missing the half that stops the recapture, and it
has never been run in the one regime where it costs money. **Keep at 0.10 only inside the A/B
in §6; do not lower it further; ship the `band_deploy` suppression in the same arm or the
lever is 1/3 live.**

### 3.5 `bfq_include_momentum_lane=True` — keep, but claim nothing for it yet
**The defect is 3-window**: `full_priority_blocked` 399 (427197) / 691 (915207) / 37 (383778)
/ **0** (bear 542754), and the cause is a fixed reader/writer ordering (`gna:28199` collects
the queue candidates ~650 lines before the momentum lane writes at `gna:28839/28852`).

**The log signature is real, and it is in exactly one run.** 571147:
```
V32 mw_buy extension-block: SNDK range +73.2% > 25% ... [bars=97]
Backfill queue ADD: SNDK (score=1.000, price=$328.19, source=direct)
Backfill queue BUY: SNDK (queued 6 bars, alloc=$100, score=1.700 HIGH-CONV)
FILL BUY SNDK qty=0.24274209 price=414.687474            = $100.67
```
It moved SNDK's entry from **$510.41** (915207, blended) / **never** (427197) to **$414.69**.
That is a genuine improvement and the fix cannot evict anything (`_enqueue_backfill_candidate`
appends without eviction while `len(queue) < max_size`).

**But the drain sizes flat.** BFQ `alloc=$` median is **$100.00** in 915207, 383778, 427197
*and* 571147 — 3 windows. $100.67 is **1.65% of NAV** against a $840 (14%) clip, and it slips
straight past `min_position_nav_pct=0.06` ($366-$406 on those bars), which refused nine other
buys in the same run. SNDK $414.69 -> $631.54 = +52.3%; at $100 that is **+$52**, at the clip
it is **+$439 = +7.3pp**. **The money is in the drain sizing, not in the lane.** Keep the lane
(it is a correctness fix with a 3-window defect); attribute no P&L to it.

### 3.6 `entry_extension_metric="range"` — keep for now, but it is the biggest open A/B
This is the legacy default, so it is not a fit. It is also not evidenced in either direction:

* `ext-still-blocking` §3, 3 windows, n=133: Spearman(reading, fwd) = **-0.115, p=0.188**,
  buckets non-monotone. ~**40%** of every intended momentum-watchlist buy is refused, and
  **142 of 144** blocked names are never bought at any price.
* My own horizon-controlled replication (block bar -> +10 sessions, log panel prices):
  blocked-basket mean fwd = **+4.7% / -0.6% / -1.9% / +12.6% / +3.6%** for
  915207/427197/571147/542754/383778; median negative in 3 of 5. **No consistent sign.**

Do **not** flip it unilaterally: `ext-still-blocking` §4 measured that on the `anchor` scale
**0 of 46** block events exceed 25, so `metric=anchor` at the current threshold is
*behaviourally identical to disarming the gate*, which `OBJECTIVE.txt` lists DO-NOT-RETRY.
It needs the paired A/B in §6 with a re-expressed threshold, not a config flip.

---

## 4. (2) SINGLE-WINDOW FITS — REVERT

### 4.1 `peak_giveback_min_peak_pnl_pct=30` / `peak_giveback_exit_drawdown_pct=25` — REVERT

**It is inert.** 55 `PEAK GIVE-BACK EXIT` lines in 571147, **all 55 on SLV**, and there is no
`FILL SELL SLV` in the run. The seven sells are six SPY core legs and one CART. Downstream the
name emits `SLV @ ...: hold action_intent=hold` (19x) and
`Monitor decision: SLV ... -> HOLD (monitor: hold)` (192x). `fresh_score=-1` at
`gna:20160-20164` never becomes an order. **This is the fifth inert lever of the session.**

**Its evidence is one name, one bar, one window — and the multi-window measurement of the
same rule says reject.** `dd-drop` §5(b) replayed this family over 4 windows:

| rule | 427197 | 915207 | 542754 bear | 383778 OOS |
|---|---|---|---|---|
| exit T=12% G=20% | -1.62pp | -2.94pp | never fires | never fires |
| **exit T=15% G=30%** | **+1.40pp** | **-0.04pp** | **never fires** | **never fires** |

and concluded verbatim: *"The only positive setting fires once, in one run, and fires zero
times in both control windows. That is a single observation, not a mechanism. Reject."*
`hold-check` §5 then re-proposed it at (30, 25) and shipped it. **The two docs contradict; the
one with four windows says no.** `hold-check` also states its own ceiling honestly: the
recoverable dollars are **$90-$165**, not the $469 give-back, because SLV gapped 21.2% -> 28.2%
from peak inside one bar.

Fires across every other run in the repo: **0**. It is fitted to SLV on 2026-01-30.
**Revert both keys to 0.** If it is kept, it must first be made to actually sell (an inert
exit is strictly worse than none — it burns a lever slot and creates false confidence).

### 4.2 `momentum_rank_on_60d=True` — the ranking key rests on ONE RUN, and it does not replicate

`discovery-and-ranking.md` justifies this with pooled Spearman **IC(60d)=+0.201, p=0.00012**.
Two problems.

**(a) The pool is one window.** Its own header: *"Runs read: bt 820236 and bt 613166, both
2026-01-01 -> 2026-03-01."* Same window. And **613166 is one of the cache-poisoned runs** —
I counted **39 of its 182** momentum discoveries carrying a fabricated `60d=+0.0%` (0 of 214,
150, 126, 194, 169 in 915207/542754/383778/427197/571147). So ~21% of half the pool is noise
by construction.

**(b) I tried to replicate it and could not.** For each `Discovered stock (momentum): SYM
(20d=..., 60d=...)` line I took the first `[BROKER] SYM @ date ($px)` panel price on/after the
discovery bar and the last panel price H sessions later, bar-date aligned:

| run | window | IC60 @H=3 | @H=5 | @H=10 | @H=15 |
|---|---|---|---|---|---|
| **820236** | W1 | +0.082 (p .27) | +0.156 (p .04) | +0.124 (p .11) | +0.111 (p .16) |
| 915207 | W1 | -0.088 | **-0.174 (p .01)** | **-0.199 (p .01)** | **-0.212 (p .00)** |
| 427197 | W1 | **-0.188 (p .01)** | **-0.247 (p .00)** | **-0.236 (p .00)** | **-0.222 (p .01)** |
| 571147 | W1 | **-0.227 (p .00)** | **-0.264 (p .00)** | -0.161 (p .07) | **-0.212 (p .03)** |
| 613166 | W1 | — | +0.021 (p .78) | +0.080 (p .30) | — |
| 542754 | W2 bear | +0.036 | +0.171 (p .06) | -0.095 | **-0.230 (p .04)** |
| 383778 | W3 OOS | +0.024 | -0.061 | -0.002 | +0.045 |

**IC(60d) is positive in exactly one run — 820236 — and negative, significantly, in three
other runs on the same calendar window.** In the bear window the sign inverts with horizon
(and IC(20d) there is **+0.214** on the window-end estimator, i.e. the "anti-signal" is the
signal in a bear). Out of sample it is indistinguishable from zero at every horizon.

**Honest limit on my own number:** my estimator is not the doc's. I use logged panel prices,
which exist only on bars where a name was scored — a selected sample — and a window-end
horizon for the first table in §0. That is exactly why I ran H=3/5/10/15: the sign is stable
across all four. The defensible claim is **not** "60d is an anti-signal"; it is
**"+0.201 is estimator-sensitive, single-run, and partly computed on fabricated data — it
does not meet the >=2-window bar."**

Do **not** revert to `max(20d, 60d)` on this — that key measured worse (IC -0.003) and my
`ICmax` column is negative in 4 of 6 runs too. The right action is to **stop treating the
ranking key as settled** and put it in the A/B (§6) as its own arm.

### 4.3 `residual_sleeve_bear_alloc_pct=0.35` — REVERT OR GATE. This is the largest
single-window fit in the config, and it silently invalidates the bear result.

From the logs, not the config:
```
542754  [sleeve] parked $3166.14 in BEAR leg SQQQ @ 69.50 (regime=bear, leg=3166/4221 cap, alloc=70%)
342380  [sleeve] parked $4200.00 in BEAR leg SQQQ @ 71.53 (regime=bear, leg=4200/4200 cap, alloc=70%)
383778  [sleeve] parked $2100.00 in BEAR leg SQQQ @ 87.51 (regime=bear, leg=2100/2100 cap, alloc=35%)
```
**Both bear runs ran at 0.70. The only 0.35 run is the OOS bull, where the leg lost money.**

542754's ledger, reconstructed from its own FILL lines: bought 109.3985 sh avg $72.64, sold
57.8713 sh avg $72.73, 51.5272 sh open at $89.80. Sidecar: `SQQQ pnl = +$889.44`,
`total pnl = +$716.44` — so **everything except SQQQ was -$173.00**. Halving the leg cap
($4,221 -> ~$2,110) at the same entry schedule gives roughly **+$445 - $173 = +$272 = +4.5%**,
**below the +6%/mo target.** (Approximate — the sleeve refills to a cash target, so it is not
exactly linear — but the direction is not in doubt.) Symmetrically, 383778's -$257 at 0.35
would be ~-$514 at 0.70, i.e. **that run goes to roughly zero.**

So the dial is pure variance, not alpha:

| alloc | W2 bear | W3 OOS bull |
|---|---|---|
| 0.70 | +11.94% / +18.71% | ~0% or negative |
| 0.35 | ~+4.5% (never run) | +4.75% |

**The size is not the bug; the entry is.** `gap-oos` §3 measured it: 542754 bought the hedge
at $70.80 = **1.2% off the low of its whole range**; 383778 bought at $86.92 = **96.8% of the
high**, on a bar its own regime replay records as `bars since 20-session low = 0`. Every
magnitude/duration knob is *inverted* between the two cases (ret20 -7.38 vs -3.93,
ret5 -2.23 vs -0.2); only range position separates them.

**And that fix is not in the tree.** `grep -rn` over `backend/`:
`residual_sleeve_bear_block_at_fresh_low_bars` = **0 hits**, `bars_since_20d_low` = **0 hits**,
and `regime_rally_onset_enabled` exists (`gna:7277`, `broker.py:4026`) but is **absent from
the config, i.e. default False**. `gap-oos`'s recommendation was never shipped.

**Action:** either restore `0.70` and accept the OOS cost until the entry gate exists, or —
better — keep `0.35` but **stop citing 542754's +11.94% as evidence for the current config**,
and ship the fresh-low first-park gate + `regime_rally_onset_enabled=True` before re-running
either bear window. Right now the config has neither the size that made the money nor the gate
that would make the size safe.

---

## 5. (3) WINDOWS STILL UNTESTED

Data is available 2025-11-01 .. 2026-07-01 (bt 983687 and 931112 span it).

**Untested against the CURRENT config (`core_min 0.10` + `bfq lane` + `peak_giveback`):**

| gap | why it matters | status |
|---|---|---|
| **W2 bear 2026-03-02..03-30** | `core_min 0.10` is claimed inert here; `bfq lane` had 0 blocks here; `peak_giveback` never fires here | **never run on this code** |
| **W3 OOS bull 2026-03-30..04-27** | the ONLY window where SPY runs hard (+12.79%), i.e. the only one where releasing the core **costs** money | **never run at `core_min 0.10`** |
| **2026-04-27..2026-06-27** | genuinely forward of every lever ever fitted | **never run at all** |
| **2026-06-01..07-01** | the only non-semi-led window in the data (`RXD +86, ATEN +26, UAL +18`) | last run 433466, old config |
| **2025-11-01..2026-01-01** | the run-up *into* W1; the levers were fitted on the payoff, never on the setup | **never run** |
| **2026-02-01..2026-04-01** | straddles the bull->bear turn; the only window that tests the sleeve *transition* rather than a clean regime | **never run** |

**Structural gaps, not just calendar gaps:**

1. **No non-semiconductor bull window has ever been tested.** W1 = SNDK/AMAT. W3 = AAOI/AXTI.
   `983687` shows the entire 2025-11..2026-05 tape is AAOI/LITE/MU/TSEM/INTC/VIAV/NVTS/PLAB.
   Every "let the moonshot in at size" lever is validated on windows that contain a moonshot.
2. **No window longer than 8 weeks / shorter than target.** W2 and W3 are 4 weeks (~20-25
   bars) against a **+12% per 2 months** target. They are half-horizon by construction.
3. **No window without a single dominant leader.** All three have one name or one hedge
   carrying >90% of the P&L (SNDK/SQQQ/AAOI+SQQQ). A genuinely choppy, breadth-less window
   has never been sampled.
4. **No paired A/B anywhere.** Every comparison in every doc is *between* runs that straddle
   commits, universes, and event states. Zero same-code, same-seed, single-key-difference pairs.

---

## 6. (4) THE SMALLEST VALIDATION PLAN THAT SATISFIES ">=3 WINDOWS, 1 OOS, 1 NOT SEMI-LED"

**Window set (3 windows, minimum, all >= 4 weeks):**

| slot | window | satisfies |
|---|---|---|
| **A** | 2026-01-01 .. 2026-03-01 | in-sample; every lever was fitted here; 8 weeks = full target horizon |
| **B** | 2026-03-30 .. 2026-04-27 | **OOS**; and the only window where SPY (+12.79%) makes core release expensive |
| **C** | **2026-06-01 .. 2026-07-01** | **not led by semiconductors** (`RXD/ATEN/UAL` per bt 433466); also forward of every fit |

Add **D = 2026-03-02..03-30 (bear)** for any arm that touches the sleeve, because A and C have
**zero bear bars** and cannot exercise it at all.

C is the only slot that meets "not led by semiconductors" from real data. If the operator will
not accept a 4-week window there, the honest statement is: **no non-semi-led 8-week window
exists in this dataset, and the requirement cannot be met without new price history.**

**Arm design — 4 arms x 3 windows = 12 runs. Nothing else changes.**

| arm | change vs baseline | what it decides |
|---|---|---|
| **0 BASELINE** | current config, but `peak_giveback_*=0` and `residual_sleeve_bear_alloc_pct` frozen at whatever ships | the missing baseline; makes every other arm readable |
| **1 CAPITAL** | `core_min_pct 0.10` **+ `band_deploy` suppression while a conviction release is outstanding** | is the core floor worth the churn once the recapture is closed? Kills the 68-93% leak. |
| **2 SIZING** | BFQ drain sizes at the conviction clip instead of flat `$100` | the $52 -> $439 question; the lane fix is worthless without it |
| **3 GATE** | `entry_extension_metric="anchor"` with the threshold re-expressed (~+10%, per `ext-still-blocking` §4) | the ~40% refusal rate on the run's own #1 names |

Do **not** run `peak_giveback` or a sleeve-size change as arms. The first is inert and must be
made to execute before it can be tested at all; the second is a variance dial whose evidence
requires the unshipped fresh-low entry gate.

**Non-negotiable run hygiene** (every one of these has already burned a comparison):

1. **Same commit for every arm and every window.** Tag it. `gap-bugsweep` §1 caught 915207
   being pre-89e71f3 *after* it had been quoted as a baseline four times.
2. **Own `history_scope_salt` per arm**, per `OBJECTIVE.txt:88-96`. `_SYNTHESIS` #3: a bear
   run rewrote 183 cache rows on a shared scope and fabricated the 60d for three later runs.
3. **Cold event state + `BACKTEST_SEED` on every run** (`e045979`, `203ddc9`).
4. **Run to 100%.** 427197 and 571147 are both quoted at partial progress; 725146 was called
   "negative" at 79.65% and is actually +0.11%.
5. **Accept nothing under the noise floor.** `_SYNTHESIS`: **>= 4.94pp**, from a 0/18 held-name
   overlap between two runs of the same config. An arm that wins by 3pp on one window has
   proved nothing. Require: **same sign on >= 3 windows, and >= 4.94pp on at least one**, or
   a mechanism-level count (log signature) that is monotone across all three.
6. **Every arm must state its log signature in advance**, and the pull must find it. Five
   levers shipped inert this session (`rank_band_momentum_exempt_min_score`,
   `watchlist_priority_slots`, `max_positions_honour_regime_cap`,
   `backtest_credit_pending_sell_proceeds`, `peak_giveback_*`) precisely because nobody
   checked for the string before believing the lever.

**Cost:** 12 runs at 3600s. If that is too many, drop arm 3 (the gate needs a threshold study
first) and run 9. The one thing that cannot be dropped is **arm 0 on all three windows** —
without a baseline on this commit, none of the other numbers mean anything.

---

## 7. WHAT I AM NOT CLAIMING

* I did not run a backtest, so **every forward number here is a reconstruction from logs**,
  including the 0.35-vs-0.70 sleeve arithmetic in §4.3.
* My IC replication (§4.2) uses a different estimator from `discovery-and-ranking.md`. It shows
  the published number is **not robust**; it does not prove the published number is wrong.
* The drawdown tripling in §3.4 is **confounded** by a 0/4 opening-book change and a commit
  boundary. I report it as the observed risk of the current config, not as causation.
* `bfq_include_momentum_lane` and `core_min_pct=0.10` are, in my reading, **correct fixes with
  no realised evidence** — that is a different failure from a curve fit, and they should be
  treated differently: validate, do not revert.
* The two levers I would revert today are **`peak_giveback_*` (inert, one name, one window,
  and contradicted by a four-window measurement)** and the **unexamined citation of 542754 as
  support for `residual_sleeve_bear_alloc_pct=0.35`** — the run that produced +11.94% was
  configured at 0.70.
