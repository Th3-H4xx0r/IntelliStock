# dd-drop — the 5.7pp give-back in bt 427197 (2026-01-28 -> 2026-02-09)

Read-only. No code edited, no run started/stopped, nothing pushed. Logs:
`backtests/427197.log` (32,163 lines, run still live at 71.7%/2026-02-13 when pulled),
`backtests/915207.log` (41,184), `backtests/542754_20260809-154317Z.log`,
`backtests/383778_20260809-154318Z.log`. Builds on `_RUNS3.md`, `_SYNTHESIS.md`,
`gap-winners.md`, `extension-gate-inversion.md` — none of those findings are re-derived here.

## 0. ONE LINE

**SLV. -$379.00 of a -$346.50 drop (109%). One name, giving back open profit, not a
loss.** The new `core_min_pct 0.10` did **not** cause it — the SLV clip was $840 in both
configs, byte-identical. Only APP (-$49.19, 14%) is attributable to the wider band.

## 1. Equity path reconstructed from the fills + hourly monitor marks

Replaying all 19 `FILL` lines against the per-bar `Monitor decision` marks reproduces the
reported trajectory to 0.01pp, so the attribution below is exact, not inferred:

| date | reconstructed NAV | pnl | reported (`_RUNS3.md`) |
|---|---|---|---|
| 2026-01-28 | $6,952.19 | **+15.87%** | 46.7% -> +15.88% |
| 2026-01-29 | $7,038.70 | +17.31% | (true peak) |
| 2026-01-30 | $6,492.89 | +8.21% | |
| 2026-02-05 | $6,090.42 | +1.51% | (true trough) |
| 2026-02-09 | $6,605.69 | **+10.09%** | 66.7% -> +10.12% |
| 2026-02-13 | $6,602.78 | +10.05% | 71.7% (last bar in log) |

Peak-to-report drop = **-$346.50 = -5.78pp**, matching the 5.7pp in the brief.

## 2. Dollars per symbol, 2026-01-28 close -> 2026-02-09 close

P&L = (end value - start value) + net cash flow for that symbol. Sums exactly to -$346.50.

| sym | qty | px 01-28 | val | px 02-09 | val | net flow | **P&L $** | share of drop |
|---|---|---|---|---|---|---|---|---|
| **SLV** | 12.4711 | 105.90 | 1,320.69 | 75.51 | 941.69 | 0 | **-379.00** | **109%** |
| APP | 1.1293 | 542.80 | 612.97 | sold 496.04 | 0 | +563.78 | **-49.19** | 14% |
| AMAT | 3.0318 | 336.84 | 1,021.24 | 330.50 | 1,002.02 | 0 | -19.22 | 6% |
| CPER | 23.9444 | 36.77 | 880.44 | 36.76 | 880.20 | 0 | -0.24 | 0% |
| SPY | 1.0640->1.7625 | 695.39 | 739.87 | 694.00 | 1,223.15 | -481.78 | **+1.49** | — |
| LLY | 0->0.1272 | — | 0 | 1,044.33 | 132.79 | -130.34 | +2.46 | — |
| BA | 1.0772 | 241.65 | 260.30 | 244.73 | 263.62 | 0 | +3.32 | — |
| BALL | 0.8584 | 56.25 | 48.29 | 66.67 | 57.23 | 0 | +8.94 | — |
| SBLK | 43.0656 | 22.65 | 975.43 | 23.28 | 1,002.57 | 0 | +27.13 | — |
| TDY | 1.6335 | 620.83 | 1,014.13 | 656.22 | 1,071.94 | 0 | +57.81 | — |
| | | | | | | | **-346.50** | |

**It was not broad** — 6 of 10 names were up over the stretch, +$101.15 combined.
**It was not the SPY core** — SPY contributed **+$1.49**; the index was flat
(695.39 -> 694.00, -0.20%).
**It was not a forced exit** — only one sell fired in the whole stretch (APP,
`427197.log:22846`, 2026-01-30 @ $499.25 vs $620.16 entry = **-$136.53 realized**, of
which -$49.19 fell inside the window). No stop, no circuit breaker, no rotation.

## 3. What SLV actually did

Bought 2026-01-02, `427197.log:2826`:
`FILL BUY SLV qty=12.47106599 ... price=67.353890` = **$840.00 = 14.0% of NAV**.

Price path (last hourly mark per session): 67.35 (entry) -> 93.57 (01-23) -> **105.90
(01-28, +57.2%, 19.0% of NAV)** -> 105.01 (01-29) -> **77.12 (01-30)** -> 64.37 (02-05)
-> 75.51 (02-09) -> 70.40 (02-13).

The whole event is **one session**: 2026-01-30, hourly marks 105.01 -> 75.22 intraday,
**-26.6% close-to-close**. That single bar is -$348 of the -$379.

SLV never came close to a loss. Entry $67.35, worst mark $64.37 (-4.4%), Feb-9 mark
$75.51 = **still +12.1% vs entry**. The catastrophic stop is a -10%-from-entry floor and
was never in range. What was lost was **79% of the open gain** ($481 open profit at the
peak, $102 left on 02-09).

The trailing stop is off by design and logged its suppression **130 times**, first at
`427197.log:22140`:
`Trailing stop SUPPRESSED (trailing_stop_disabled): SLV drop=18.6% >= 12% — held;
catastrophic stop is the floor`
then 28.7% / 33.7% / 40.5% on subsequent bars.

## 4. Did `core_min_pct 0.10` cause it? **No.**

Three independent proofs from the two logs:

1. **The clip is identical.** Bar 1, 427197 `:442` —
   `V31.2 total-spend cap [CONCENTRATE]: funded 4 of 7 by conviction (SBLK@$840,
   CPER@$840, SLV@$840, TDY@$840) out of $3,780; dropped 3 to the queue`
   Bar 1, 915207 `:368` —
   `V31.2 total-spend cap [CONCENTRATE]: funded 4 of 7 by conviction (TCMD@$840,
   XOM@$840, VOYA@$840, NTR@$840) out of $3,780; dropped 3 to the queue`
   Same $840 clip, same $3,780 satellite budget. The position that lost the money was
   sized exactly as the 0.25-floor config would have sized it.
2. **The core buy is identical to 8 decimals.** 427197 `:3519` and 915207 `:3578` both
   read `FILL BUY SPY qty=3.51184282 ... price=682.970444` — $2,398.5 = 40.0% of NAV.
   `core_min_pct` is a *floor on releases*, and it did not touch bar-1 construction.
3. **The core the wider band freed was worth $1.49.** By 2026-01-28 the core had been
   released down to **10.6% of NAV** in 427197 vs **26.0%** in 915207. Over the stretch
   SPY was flat, so the 15.4pp of NAV that 915207 parked in SPY would have earned -0.2%.
   Holding the old floor would have avoided roughly $45 of the $346.50 — 0.8pp of 5.78pp.

The only genuinely config-attributable dollars are **APP, -$49.19 (14%)**. APP exists
only because the wider band funded it: `2026-01-08 SATELLITE OVERFLOW: APP raw=+1.800 >=
1.50 — funding $1,634 of room out of the core (floor-bounded)`. 915207 on the same bar
bought AMAT only.

**Same calendar, both runs, $6,000 each:**

| date | 427197 (0.10) | 915207 (0.25) | edge |
|---|---|---|---|
| 2026-01-28 | +15.87% | +6.47% | **+9.40pp** |
| 2026-02-05 (trough) | +1.51% | +4.17% | -2.66pp |
| 2026-02-09 | +10.09% | +7.94% | **+2.15pp** |
| 2026-02-13 (last common) | +10.05% | +7.51% | **+2.54pp** |

The new config is ahead at the peak *and* after the drawdown. **Reverting `core_min_pct`
would surrender 9.4pp of peak to avoid 0.8pp of drawdown.** It coincided; it did not cause.

## 5. Three candidate drawdown fixes — I measured all three across 4 windows. None generalize.

Replayed each run's actual fills through a rule engine (cash earns 0, 3bp on forced sells),
windows = 427197 (bull/chop), 915207 (same window, old floor), 542754 (bear), 383778 (OOS).

**(a) Mark-to-market weight cap on satellites (trim back to target):**

| rule | 427197 | 915207 | 542754 bear | 383778 OOS |
|---|---|---|---|---|
| cap 16% -> 14% | **-1.46pp** | +0.28pp | +1.11pp | **-0.93pp** |
| cap 18% -> 15% | **-1.19pp** | 0.00pp | +1.17pp | **-0.50pp** |
| cap 20% -> 16% | 0.00pp | 0.00pp | +1.10pp | -0.69pp |

Negative in the run it was designed for and negative out of sample. In 383778, AAOI hits
20.3% of NAV — the cap clips exactly the name the OBJECTIVE wants at size. **Reject.**

**(b) Re-arm the trailing stop only on big open winners** (exit if peak gain >= G and
give-back >= T):

| rule | 427197 | 915207 | 542754 | 383778 |
|---|---|---|---|---|
| exit, T=12% G=20% | -1.62pp | -2.94pp | never fires | never fires |
| exit, T=15% G=30% | **+1.40pp** | -0.04pp | never fires | never fires |
| half, T=15% G=30% | +1.22pp | -0.05pp | never fires | never fires |

The only positive setting fires **once, in one run**, and fires **zero times** in both
control windows. That is a single observation, not a mechanism. It also contradicts
`_SYNTHESIS.md#DO-NOT-TOUCH` (re-arming trailing stops exited all five 820236 winners).
**Reject.**

**(c) Revert `core_min_pct` to 0.25:** does not address the cause (section 4) and costs
9.4pp of peak. **Reject.**

Stated plainly, as required: **there is no generalizable fix for this drawdown.** A
-26.6% one-session move in a position already up +57% is not reachable by any exit or
sizing rule that survives a second window. The right response is to stop optimising
against it.

## 6. Where the same effort is worth 2.4x the drawdown: the extension gate

While the book was giving back $379 on SLV, the log blocked the largest mover in the
universe **11 times**.

`427197.log:5827` (2026-01-07):
`V32 mw_buy extension-block: SNDK range +73.2% > 25% — no conviction bypass [bars=97]`
... through 2026-02-12: `SNDK range +79.4% > 25%`, price `$651.97`.

Discovery had it on bar 1: `Discovered stock (momentum): SNDK (20d=+15.6%, 60d=+95.9%)`
at `$237.33`. Code: `backend/strategies/graph_nexus_analysis.py:5531-5557` —
`entry_extension_block_pct` guards the momentum lane with **no conviction bypass**.

Counterfactual at the *first* block, not the discovery price: $840 at $328.19 (2026-01-07)
= 2.5595 sh -> $1,668.72 at $651.97 (2026-02-12) = **+$828.72 = +13.81pp on $6,000**.
The capital was idle: cash on 2026-01-07 was **$958.23** and **zero buys filled that bar**.

**+13.8pp from one admitted name vs -5.8pp from one un-stoppable gap.** And the mechanism
is already corroborated across windows in `extension-gate-inversion.md` (393 gate fires
over 201039/820236/613166, zero under a bull profile) and here in a 4th run; 915207 shows
the cost of the same gate from the other side — it admitted SNDK only on 2026-01-28 at
$510.41 for **$29.11** (`915207.log:20549`), 0.5% of NAV, on a name that then went to $652.

## 7. Verdict

* The 5.7pp is **SLV, -$379.00, one session, pure open-profit give-back**.
* `core_min_pct 0.10` **coincided**. Config-attributable = APP, -$49.19 = 0.82pp of 5.78pp.
* Do **not** revert it: 427197 leads 915207 by +9.40pp at the peak and +2.54pp at the last
  common bar of the same calendar window.
* Do **not** add a weight cap or re-arm trailing stops: measured, and both fail out of sample.
* Spend the next change on `entry_extension_block_pct` / the momentum-lane bypass. SNDK
  alone is worth +13.8pp in this window, 2.4x the drawdown being investigated.
