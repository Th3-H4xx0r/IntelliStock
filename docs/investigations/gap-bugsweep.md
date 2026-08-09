# gap-bugsweep — verification of the last-24h levers against bt 915207 / 542754 / 383778

Method: pulled all three RUN logs (`scripts/pull_backtest_logs.py <id>`) to
`backtests/{915207,542754,383778}_sweep.log` (41,184 / 18,265 / 19,643 lines) and
searched for each lever's own log signature. Config read from
`scripts/doc193_backup_patch_20260808T110842Z.json` (latest).

---

## VERDICT TABLE

| lever | fires? | count 915207 / 542754 / 383778 | matches its claim? |
|---|---|---|---|
| momentum_scan_cached_bars | YES | 276 / 150 / 126 momentum discoveries | YES |
| max_positions_honour_regime_cap | YES, but bought nothing | 43 / 21 / 21 | MECHANICALLY YES, **ECONOMICALLY INERT** |
| min_position_nav_pct + exec floor | **NOT IN 915207** | min$50 / — / min$341-389 | **915207 PREDATES THE COMMIT** |
| backtest_credit_pending_sell_proceeds | **UNVERIFIABLE** | 0 / 0 / 0 log lines | **NO SIGNATURE AT ALL** |
| momentum_rank_on_60d | YES | sorted 60d-desc in all 3 | YES |
| momentum_missing_60d_excluded | YES | 0 fabricated `60d=+0.0%` of 552 | YES |
| entry_extension_metric | **CONFIGURED INERT** | 142 / 139 / 37 blocks, on `range` | **REVERTED TO THE BROKEN METRIC** |
| momentum_swap_vs_portfolio_enabled=False | YES | rotation `fired=0/4` in 57/57 | YES |
| overlay_bars_min_history_bars | ABSENT | 0 / 0 / 0 | not needed here (see below) |
| turnover_budget_conviction_bypass_* | YES | 9 / 0 / 33 bypasses | YES, but brake binds 41%/88%/**100%** of bars |

---

## FLAGGED — the three that are not doing what the row says

### 1. `min_position_nav_pct` exec floor is NOT in bt 915207. 915207 is a PRE-COMMIT run.
915207 prints the legacy dollar floor, 383778 prints the NAV floor:

    915207: SKIP BUY CFG  — cash_to_use $1.69  < min $50  (allocated $759.42)
    915207: SKIP BUY LLY  — cash_to_use $38.15 < min $50  (allocated $687.41)
    383778: SKIP BUY LWLG — cash_to_use $45.45 < min $386 (allocated $882.44)
    383778: SKIP BUY AMRX — cash_to_use $50.79 < min $389 (allocated $823.68)

915207 started `[2026-08-09 06:08:31]`; commit 89e71f3 landed `2026-08-09 02:50:02 -0700`
= 09:50 UTC, **3h41m after the run began**. Proof in the closing book — three sub-$50
runts a 6%-of-NAV floor ($360) would have refused:

    915207 Positions:  SNDK 0.0570 sh = $36.27 | NVDA 0.1514 sh = $26.82 | RVLV 1.9355 sh = $48.70

Same for the extension gate: 915207 only ever prints the legacy wording
(`SNDK recent runup +73.2% > 25%`), while 383778 prints the new wired form
(`BLBX range #% > #% ... [bars=#]`, gna:5556). **915207's +9.70% was not produced by
the shipped code and must not be quoted as the shipped baseline.**

### 2. `entry_extension_metric` — shipped, then configured back to the metric its own commit convicted.
a2054c6 wired `_extension_blocks_entry` (gna:5540, gna:23421); a2609bd then set the
document back to `"range"`, which *is* the legacy `_recent_runup_protect` behaviour the
same commit proved decays / is direction-blind / anti-monotonic. It is still blocking:
142 / 139 / 37 lines. In 915207 it refused SNDK **five times**:

    V32 mw_buy extension-block: SNDK recent runup +73.2%  > 25% — no conviction bypass
    V32 mw_buy extension-block: SNDK recent runup +75.3%  > 25% — no conviction bypass
    V32 mw_buy extension-block: SNDK recent runup +111.2% > 25% — no conviction bypass

SNDK moved **+166.10%** ($237.33 -> $631.54). The lever is code-live and behaviour-null.

### 3. `max_positions_honour_regime_cap` — fires, blocks nothing, bought nothing.
43 / 21 / 21 `honouring the regime cap` lines, but `MAX_POSITIONS_GATE: blocked` = **0 in
all three runs**. In the bear and OOS runs the observed direction is *tightening*
(`6 -> 2`, 21x in 542754 and 4x in 383778) — which the call site honours **without** the
flag (broker.py:14205, `if nexus_max_positions < _mpg_cap or _mpg_widen`). Only the 43
widenings in 915207 needed the flag and none of them converted a refusal into a buy.
max_positions is no longer the binding constraint; the flag is now dead weight.

Also: `backtest_credit_pending_sell_proceeds` emits **zero** log lines in any run — it is
set on the emulator every tick (broker.py:14178-14182) but is unobservable. It cannot be
confirmed to fire. Add a one-line log before trusting it.
`overlay_bars_min_history_bars` is absent, but is not needed here: 0 of 552 momentum
discoveries carry a fabricated `60d=+0.0%` (values run to `DZZ 60d=+141.6%`), so the bar
cache in these three runs is clean. `momentum_missing_60d_excluded` covers it.

---

## THE ONE CHANGE THAT MOVES THE RETURN: `core_min_pct` 0.25 -> 0.05

None of the shipped levers is the leak. **The core floor is.** It refuses the funding the
core sleeve exists to provide, and it does so on both bull windows.

**Mechanism.** A conviction buy (`raw >= satellite_conviction_overflow_min_raw_score=1.5`)
may overflow the satellite design share, bounded by the core's floor (broker.py:14916-14939).
The band is `core_target_pct 0.35 -> core_min_pct 0.25` = **10% of NAV = ~$600 on a $6,000
book, for the whole run**. The first two conviction buys consume it, then room collapses:

    915207  SATELLITE OVERFLOW: BA   ... funding $1,050 of room out of the core (floor-bounded)
    915207  SATELLITE OVERFLOW: CFG  ... funding   $759 of room out of the core (floor-bounded)
    915207  SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($12 room)
    915207  SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($21 room)
    915207  SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($-1 room)
    915207  SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($-28 room)
    915207  SATELLITE CAP: SNDK trimmed $904 -> $29 to keep the core at target
    915207  Buy gate inputs for SNDK: ... cash_per_trade=$29.11 ... cash_to_use=$29.11 -> PASS

The core's own NAV share bottoms at exactly **25.0%** (`[core] hold — core 25.0% vs target
35.4%`) — pinned on `core_min_pct=0.25`. Negative room is the floor already breached.

**Counts and dollars, both bull windows:**

| | 915207 (bull/chop) | 383778 (OOS bull) | 542754 (bear) |
|---|---|---|---|
| SATELLITE CAP events | 42 | 41 | 0 (core OFF in bear, by design) |
| skipped at the overflow ceiling | 17 | 13 | 0 |
| $ trimmed off sized buys | **$6,196** | **$7,717** | 0 |
| core funding requests trimmed | 16, **$33,661** refused | 16, **$41,828** refused | 0 |
| `deferred_unfunded_buy` | 49 | 35 | 0 |

**Size decides the P&L, on both windows.** The names that cleared near full size paid;
the trimmed ones did not.

    915207  XOM  $838 filled -> +$225.25 (+26.90%)
    915207  AMAT ~$652 filled -> +$211.11 (+32.37%)
    915207  NTR  $840 filled -> +$178.30 (+21.23%)      3 names = $614.66 of a +$581.83 run
    915207  SNDK $29 filled  -> +$6.91  on a +166.10% move
    915207  SPY  $2,314 held (38.6% of NAV) -> +$8.96 (+0.24%)

    383778  AAOI trimmed only $814 -> $718 (12%) -> +$147.79 (+20.82%), best name in the run
    383778  XOM  trimmed $795->$195, $814->$136, $907->$176 (3x)
    383778  HOOD stock moved +27.00%; captured -5.68%

**Expected effect.** SNDK's first refusal is 2026-01-09 at $363.01 with the sizer asking
$185 (already pre-trimmed from the 14% design weight, ~$840). At $840 -> 2.313 shares ->
$1,461 at the $631.54 close = **+$621**, against the +$6.91 actually booked. Delta
**+$614 on $6,000 = +10.2pp**; 915207 goes +9.70% -> **~+19.9%**, i.e. through the +6%/mo
target. Funding cost is ~$840 of SPY that returned +0.24% over the window: **-$2**.

**Generalizable?** Yes, on mechanism and on two windows. `core_min_pct` is a hard constant
that binds in 915207 (42 events, core pinned at 25.0%) and in 383778 (41 events, core
27.1%), on different universes and different regimes-within-bull. It is **neutral in the
bear** — 542754 has zero SATELLITE CAP lines because doc-193 deliberately has no bear core
profile, so this change cannot touch the SQQQ leg.

**The honest caveat.** In 383778 the SPY core earned **+$129.08 (+3.22%)** on ~$1,700 while
SPY ran +12.79%; cutting the floor 0.25 -> 0.05 gives up roughly $74 of that. It is a clear
net win only because the freed $1,200 goes to names like AAOI (+20.82% on the one buy that
got through). In 915207 the core earned $8.96 and there is no cost at all. Also, in 383778
`TURNOVER BUDGET BINDING` fires on **25 of 25 bars** (50%-264% of NAV vs a 50% budget), so
some freed capital will hit the brake — conviction buys at raw>=1.50 pass it
(`TURNOVER BUDGET BYPASS`, 33 events there), but plain buys will not.

**Do NOT pair this with a max_positions change** — the cap blocked nothing in any of the
three runs.

## Secondary, cheap, same direction
Turn `entry_extension_block_pct` to 0. On the current `range` metric it is pure delay: it
refused SNDK 5x during a +166% move in 915207 and fired 142 / 139 / 37 times across the
three runs, with zero evidence in this sweep that it blocked a loser.
