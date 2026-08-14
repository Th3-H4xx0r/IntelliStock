# Turnover brake investigation — bt 523085

**Scope:** the turnover brake only. Evidence base: `/tmp/bt523085.log` (40,344 lines,
run 2026-08-13 21:36 → 22:53, backtest window 2026-01-01 → 2026-02-28, `$6,000` start,
final `$6,366.10` / `+6.10%`, 32 trades — log lines 40293–40313) and the source in
`backend/`. **No backtest was run**; nothing was pushed. Log citations are 1-based line
numbers in that file. Where I could not support a claim from the log or source, I say so
in §7.

---

## 1. Headline numbers

| Question | Answer | Evidence |
|---|---|---|
| Ticks where the brake binds | **611 of 634 evaluated ticks = 96.4%** (611 of 677 `[Pending]` tick starts = 90.2%; 43 ticks exit early at `Outside session … skipping strategies and execution`) | 611 × `TURNOVER BUDGET BINDING`; 634 × `max_positions gate armed` (the line emitted immediately before the budget check, broker.py:15455) |
| Sessions where it binds | **49 of 51**; only 2026-02-27 and 2026-02-28 never bind | grouped by tick date |
| Reported usage range | **54% → 150%** of NAV over the rolling 21 sessions; budget is `turnover_budget_monthly_pct = 0.5` | log line 1915 (`56%`), line 25989 (`150%`), min 54% at line 4274; `scripts/doc193_backup_patch_20260808T110842Z.json:825`; `core_sleeve.py:708-722` |
| Buys it actually blocked | **7, ever** — all in the first 4 sessions, none after 2026-01-06 | lines 2501, 2505, 2509, 2513, 3384, 4282, 5214 |
| Buys it admitted through the conviction bypass | **71** | 71 × `TURNOVER BUDGET BYPASS` |
| Bypass-ceiling refusals | **0**, even at 150% usage | no `TURNOVER BYPASS CEILING` line exists; `_turnover_cfg_bypass_ceiling` returns `0.0` = "none" when the key is absent (broker.py:3375-3381) |
| Gross traded notional (fills) | **$24,477.63** on a $6,000 book ≈ **3.95× average NAV in 1.9 months ≈ ~208%/month one-way** | 32 `[execution] FILL` lines |
| Share of that gross the brake can even see | **55.9%.** SPY (the index core) is **$10,791.76 = 44.1%** and is exempt from the ledger | `_turnover_is_governed` (broker.py:3155-3210) |

So: the brake is *on* essentially all the time, blocks almost nothing, and is blind to
44% of the churn it exists to measure.

---

## 2. What it blocks, and what happened to those names

All 7 blocks are `initial_buy` / `backfill_queue_buy` on a **cold book in week 1** — not
rotations, not churn:

| Date | Symbol | Intent (line) | Price at block | Last price observed in log | Move |
|---|---|---|---|---|---|
| 01-01 | RCL | `initial_buy` (2500) | $278.79 | none after block | **unknown** |
| 01-01 | REXR | `initial_buy` (2504) | $38.70 | none after block | **unknown** |
| 01-01 | RUN | `initial_buy` (2508) | $18.41 | 2026-02-12 $18.865 | +2.5% |
| 01-01 | VICR | `initial_buy` (2512) | $109.625 | 2026-02-25 $198.715 | **+81.3%** |
| 01-02 | TPG | `initial_buy` (3382) | $65.06 | 2026-01-30 $58.34 | −10.3% |
| 01-05 | V | `backfill_queue_buy` (4280) | $351.89 | 2026-02-17 $318.92 | −9.4% |
| 01-06 | AIFD | `initial_buy` (5212) | $38.195 | 2026-01-07 $38.755 | +1.5% |

(Prices are from the broker's own `SYM @ ts ($px): … action_intent=` lines, so they are
observations inside this run's window only. RCL and REXR never appear again after the
block, so **no post-block move can be claimed for them**.)

**Did it ever block a name that later moved ≥30%? Yes — exactly one, VICR (+81.3%) —
but the honest reading is weaker than it looks.** The VICR block at line 2513 was a
**duplicate** order: an earlier tick on the same session had already funded
`VICR@$840` (line 436) and that order filled 2026-01-02 at $116.19 (line 2908). A later
tick on the *same* session re-proposed the identical name (`funded 4 of 9 by conviction
(VICR@$840, RCL@$840, REXR@$840, RUN@$840)`, line 2228) because next-event execution
means the position does not exist yet on the submitting bar. So the brake's single
"expensive" block prevented the book from buying VICR twice on day 1 (2 × $840 = 28% of
NAV into one name). **The brake was right there.** The genuinely lost names are RCL and
REXR (unmeasurable from this log) and V/TPG/RUN/AIFD (flat to −10%).

**Conclusion for the objective: the brake is not where the money is going. It cannot be
"loosened" to fix anything — after 2026-01-06 it refused zero trades in 604 further
binding ticks.** The three defects below are what actually generate the ~200%/month.

---

## 3. Defect 1 — a rotation's SELL executes unconditionally; its paired BUY is gated separately and often never happens

`_exec_order = _sell_first + _buy_rest` (broker.py:14603) puts every nexus sell ahead of
every buy, and broker.py:17068 states the intent: *"Sells sort first, so a rotation's
funding exit is booked before its buy."* The buy leg then has to survive the satellite
cap, the cash floor, the funding trim, `max_positions` and the turnover brake. When it
does not, the book has paid the exit, lost the exposure, and bought nothing.

**2026-01-16, the clearest instance:**

```
12561  Momentum rotation: sell VICR (score=0.805) → buy SNDK (score=1.113, $1081)
12592  V31.2 rotation cap: SNDK alloc $1081 → $952 (15% of portfolio $6350)
12717  SATELLITE CAP: SNDK skipped — satellite at its design share ($-1,393 room);
       core would be squeezed below target
12962  [execution] FILL SELL VICR qty=7.22960347 price=149.087809  (= $1,077.94)
```

SNDK was refused at $405.47 (line 12716). It was finally bought **2026-02-04 at
$617.42** (line 24977) — +52.3% higher — after being admitted through the brake by the
conviction bypass **nine separate times** (01-12, 01-13, 01-14, 01-15, 01-19, 01-21,
01-29, 02-03, 02-04) and killed by cash on eight of them
(`SKIP BUY SNDK — cash_to_use $212.59 < min $368`, etc.). VICR, meanwhile, went from the
$149.09 exit to $198.715 by 02-25 (**+33.3% forgone**, line 38227) and the run's own
summary prints `VICR: $109.62 -> $201.80 (+84.08%)` (line 40340).

Same pattern twice more:
* 2026-01-26, line 18166: `V31 conditional swap: sell existing BALL (eff=-0.850, $895) to keep new buy EFX (eff=1.800)`. BALL sold at $56.949; EFX bought and stopped out 8 days later at −16.2% (`[sell-gate] EFX | gate=circuit_breaker … result=fired`, line 24096). Summary: `BALL: P&L = $23.69 (+2.72%)` while `BALL: $52.98 -> $67.14 (+26.72%)`; `EFX: P&L = $-173.98 (-18.19%)`.
* 2026-02-02, line 22792: `V31 conditional swap: sell existing CCK … to keep new buy BBSI`. CCK was sold ($837.19). **BBSI never appears in any fill in the entire run.**

Three of the five satellite sells in this run were swap/rotation exits whose replacement
either never happened (CCK→BBSI), happened 13 sessions and +52% later (VICR→SNDK), or
was itself stopped out in 8 days (BALL→EFX). That is pure two-way turnover for negative
exposure.

**Fix.** Make the sell leg conditional on the buy leg's admissibility instead of merely
ordering it first. Concretely, in `backend/broker.py` before line 14603: build the
rotation pair map that already exists in the nexus hints, run the *buy* candidate through
a dry-run admission (`_core_sleeve_satellite_headroom`, `cash_to_use >= min_position_size`,
`max_positions`, turnover) and **drop the paired symbol from `_sell_first` when the buy
would be refused**, logging `ROTATION ABORTED: <sell> kept — paired buy <buy> not
admissible (<reason>)`. This is the same class of pre-check the code already does for the
core (`[core] anchor funding excluded … not releasing core for a buy the execution gate
will refuse`, broker.py:15350-15358) — it simply was never applied to the satellite
rotation.

---

## 4. Defect 2 — the meter excludes the largest churn source, and that source oscillates

### 4a. The core is invisible to the ledger *and* exempt from the brake

`_turnover_is_governed` (broker.py:3155-3210) returns `False` for the core symbol
whenever `core_sleeve_enabled` appears in the base config **or any regime profile**, so
core notional is never booked. `_core_sleeve_decide` additionally forces
`_blocked = False` unless `core_respects_turnover_budget` (broker.py:4180-4188, default
`False`). The core is therefore both unmeasured and ungoverned.

**Arithmetic proof from day 1.** On 2026-01-01 the book submitted four satellite buys of
$840 (`funded 4 of 8 by conviction (VICR@$840, SBLK@$840, ROKU@$840, RTX@$840)`, line 436;
`cash_per_trade=$840.00`, line 1678) *and* `[core] bought $2400.00 SPY` (line 1918). The
brake on that tick reported **56%** (line 1915). 4 × $840 / $6,000 = **56.0% exactly**;
including the SPY leg it would have been **96.0%**. The $2,400 was not booked.

Over the run: SPY = **$10,791.76 of $24,477.63** gross (44.1%), in 16 fills
(5 `residual_bull_deploy` buys, 11 `residual_bull_refill` sells).

### 4b. The core leg round-trips itself

| Deploy | Reversed by | Reversed share |
|---|---|---|
| 01-20 BUY $1,152.59 | 01-21 SELL $1,114.24 | 96.7% |
| 01-22 BUY $1,127.65 | 01-23 SELL $1,127.93 | 100.0% |
| 02-03 BUY $962.14 | 02-04 SELL $954.06 | 99.2% |

$6,438.61 of gross — **107% of the initial NAV** — in three one-session round trips that
left net exposure essentially unchanged. Plus six *dust* refills of $5.18, $7.04, $8.63,
$8.77, $9.41, $10.59 that clear the $5.00 floor (`_RESIDUAL_SLEEVE_MIN_RELEASE_USD = 5.0`,
broker.py:2951) on a $6,000 book.

The driver is visible on **41 of 41 trading sessions**: a
`[core] funding request trimmed $X -> $Y` line every single session, with X between
$1,682 and $3,699 (28–62% of NAV *per day*) and Y between $0 and $2,594. The satellite
asks the core for cash it then cannot deploy; the core releases; the band sees itself
under target and re-deploys. The 01-16 rotation feeds it directly: VICR's $1,077.94 exit
was followed on 01-17 by `[core] bought $1165.72 SPY (band_deploy: 11.6% -> 30.2%)`
(line 13194) — the cash freed by selling the winner went straight into the index, then
back out on 01-21 ($1,114.24), then back in on 01-22 ($1,127.65), then out again on
01-23 ($1,127.93).

At the run's own cost model (`equity-measured-v3-nbbo23`, 22.8 bps half-spread + 0.1 bps
slippage + 0.3 bps fees = **23.2 bps one-way**, `backend/simulated_execution.py:92-121`)
the SPY leg alone spent ≈ **$25.04** and its realised result was
`SPY: P&L = $-3.63 (-0.06%)` (line 40326). Total modelled friction on the whole run's
gross ≈ **$56.79**, i.e. **15.5% of the +$366.10 P&L**.

**Fix.**
1. Book core notional into the ledger — delete the core exemption in
   `_turnover_is_governed` (or, if the exemption must stay to avoid the starvation
   described in its own docstring, keep a **second** `core_turnover_ledger` with its own
   ceiling and log it, so the number the operator sees is total book turnover, not
   satellite-only).
2. Add a **minimum ticket and hysteresis** to the sleeve legs: refuse any
   deploy/release below `max($100, 1% of NAV)` (raise `_RESIDUAL_SLEEVE_MIN_RELEASE_USD`
   from `5.0` and add the symmetric constant for deploy) and refuse a `band_deploy` on a
   tick where a funding release has already fired, or within N sessions of a release of
   comparable size. The three tables above show that would have removed ~$6.4k of gross
   with no change to the book's net position.
3. Net the two legs: `_residual_sleeve_release` and `_residual_sleeve_deploy` run in the
   same tick body (release at broker.py:15429, deploy at broker.py:17195/17256). Compute the *net* core delta once
   per tick and submit one order, instead of a release followed by a deploy.

---

## 5. Defect 3 — the brake charges exits but only blocks entries, and is spent by the book's own initial deployment

* `_turnover_ledger_record(current_time, _to_notional, f"main_signal {'buy' if decision == 1 else 'sell'} {symbol}")` — broker.py:17045-17048 — books **both sides**.
* The refusal `if _turnover_blocked and not _tb_bypass:` — broker.py:16126 — sits inside `if decision == 1:` (broker.py:15835), so it can only ever stop a **buy**. The binding line's own text says so: *"risk exits and reduce-only sells are unaffected"* (line 1915).

Consequence: a forced-exit day burns the entry budget. On 2026-02-02→02-04 the reported
usage climbed 62% → 75% → 101% → 145% → 150% while the fills on those sessions were the
CCK swap exit ($837.19), two circuit-breaker exits (EFX $782.63, ROKU $709.68 — lines
24096, 24162) and the three replacement buys. The exits the book was *forced* to take
locked the door behind them. (Exact attribution is approximate: the ledger books
*request* notional and NAV moves intraday; the direction and rough magnitude are solid,
the per-dollar split is not.)

And the budget is consumed before any churn exists: the opening basket is 56% of NAV in
one session (§4a), so a 50% budget is already exhausted on tick 1 of a cold book, which
is why all 7 lifetime blocks are `initial_buy`/`backfill_queue_buy` in week 1. With
`cash_per_trade = $840` = 14% of NAV and `max_positions = 6`, one full turn of the book
is 84% one-way — **a 50%/month budget is arithmetically incompatible with the sizing
policy it is supposed to govern**, independent of any churn.

Finally the bypass makes it porous exactly where it matters: 71 admits, ceiling never
fires (`turnover_budget_conviction_bypass_max_pct` absent ⇒ `0.0` ⇒ "none",
broker.py:3375-3381), and admits were granted at 145%, 146%, 147%, 150% of NAV. Yet only
**5 of the 71** admits reached a fill (BALL and CCK 01-06; SNDK, AMZN and ETN 02-04) —
the other 66 died downstream at the cash gate. **The bypass added on 2026-08-08 to stop
the book missing SNDK did not stop the book missing SNDK** (§3): the binding constraint
was never the brake.

**Fix.**
1. Meter **replacement** turnover, not gross notional: charge `min(buy_notional, sell_notional)` matched within the window, or exempt buys taken while the book is below its target invested fraction (a cold-start allowance). Both keep the control's intent — throttling round-trips — without spending the budget on getting invested.
2. Keep risk exits out of the meter, or give them a separate ledger: booking a circuit-breaker exit against the entry budget is the one direction the control should never take.
3. Set `turnover_budget_conviction_bypass_max_pct` (the ceiling code already exists and is dead at its default) — otherwise the bypass is an unbounded off-switch.
4. Reconcile the budget with the sizing policy: either `turnover_budget_monthly_pct` rises to something a 14%-slot / 6-name book can physically respect, or `momentum_position_size_floor_pct` / `max_positions` change. Shipping both as-is guarantees a permanently-pinned control.

---

## 6. Smaller, still concrete

* **Inconsistent booking site.** The four sleeve call sites guard with `_turnover_is_governed` (broker.py:4601, 4664, 5005, 5070); the main-signal site (broker.py:17045) does **not**. If the alpha lane ever trades the core symbol — and it does score it: `conviction_tier: sym=SPY`, `Monitor decision: SPY day 56 …` — that notional is booked while the identical sleeve trade is not. *No instance of a main-signal SPY trade occurs in this log*, so this is a code-level inconsistency, not an observed one. Fix: apply the same guard at 17045.
* **Duplicate in-flight proposals.** On 2026-01-01 the slate proposed `VICR@$840` in two different ticks of the same session (lines 436 and 2228) because next-event execution leaves the position invisible on the submitting bar. Cash-in-flight *is* tracked (`orders already in flight this tick reserve the rest`), but slate membership is not. Fix: exclude symbols with an accepted, unfilled order from the next tick's slate.
* **The binding log line is 611 lines of noise.** It fires once per tick regardless of whether any buy candidate exists; in the last three weeks of the run it fired ~15×/session while every buy died at `cash_to_use $165 < min $375`. Fix: only log when the brake actually refuses something, or log once per session with a count.

---

## 7. Claims I could **not** support

* **No post-block price data exists in this log for RCL or REXR**, so I cannot say whether blocking them cost or saved money.
* **I cannot attribute the ledger's dollar level to individual orders.** The ledger books *accepted request* notional (broker.py:3213-3240) and is never printed as a dollar figure; every reconstruction above goes through `used% × NAV` with NAV moving intraday. The day-1 identity (56% = 4 × $840 / $6,000) is exact; the later attributions are directional.
* **I did not verify the ledger's persistence behaviour empirically.** `_turnover_ledger_touch` trims only rows *older* than `_key - 31 days` (broker.py:3122-3132), so future-dated rows from a previous replay of the same window would survive a re-run — but in *this* log the day-1 reading is fully explained by this run's own opening basket, so **there is no evidence of cross-run ledger carry-over here.** I flag the trim asymmetry as a code observation only.
* **The run's config was not dumped in the log.** `turnover_budget_monthly_pct = 0.5` comes from `scripts/doc193_backup_patch_20260808T110842Z.json:825` (a 2026-08-08 backup) plus the fact that the lowest observed binding usage is 54%. The bypass being enabled and un-ceilinged is inferred from behaviour (71 `BYPASS` lines, 0 `CEILING` lines at 150%) and the code defaults, not from a config dump.
* **Fee/cost figures are model outputs, not measured P&L attribution.** 23.2 bps is what `equity-measured-v3-nbbo23` charges; the fills already embed it, so $56.79 is what the run *paid*, not an extra deduction.

---

## 8. One-line answer

The brake binds on 96.4% of evaluated ticks, has refused 7 buys in its life (all
cold-book entries in week 1, none since 2026-01-06), and is blind to the 44% of gross
notional that the index core generates — so it is neither the constraint on the strategy
nor the measurement of its churn. The churn comes from (1) rotation sells that execute
while their paired buys are refused, (2) a core deploy/refill oscillation that round-trips
~$6.4k (107% of NAV) for zero net exposure, and (3) a budget that is spent by the book's
own initial deployment and then charged again by every forced exit.
