# gap-target — working backwards from +12% on bt 915207

Window 2026-01-01..2026-03-01, v2-let-run-core, $6,000, cold.
Run made **+$581.83 / +9.70%** (`Final Value: $6,582.03`, `Total Trades: 17`).
Target = **+$720 / +12%**. Gap = **$138**.
Log: `backtests/915207_20260809-153549Z.log` (41,184 lines). All prices below are
lifted from that log only (decision panel `SYM @ date ($px)`, `Monitor decision ... cp=$`,
`Backfill queue ADD ... price=$`, `[execution] FILL ... price=`).

---

## 1. THE CEILING UNDER THE RUN'S OWN CONSTRAINTS: +23.4% / +$1,402

Constraints as the run actually enforced them:
* `max_positions=6`, and `max_positions: index-core leg(s) SPY do not consume a slot` (612 lines) -> 6 satellite slots.
* `core_max_pct=0.40`, `core_target_pct=0.35`; `[core] bought $2400.00 SPY @ 681.82 (band_deploy: 0.0% -> 40.0% of NAV)`.
* `total_spend_cap_target_weight_pct=0.14`; sizer output `V31.2 total-spend cap [CONCENTRATE]: funded 4 of 7 by
  conviction (TCMD@$840, XOM@$840, VOYA@$840, NTR@$840) out of $3,780` -> $840 = 14.0% of $6,000.
* Satellite design share = 60% of NAV (`SATELLITE CAP: X skipped — satellite at its design share`).
  60% / 14% = **4.3 full-size names**, not 6. The 6-slot cap is NOT the binder; the 60% share is.

Best portfolio reachable, using ONLY names the run itself sized that bar and ONLY that bar's price:

| leg | in | px in | out | px out | $ | ret | P&L |
|---|---|---|---|---|---|---|---|
| SPY core 40% | 01-01 | 681.82 | 02-27 | 686.16 | 2,400 | +0.64% | +15.28 |
| TNDM | 01-01 | 22.00 | 02-24 | 28.46 | 840 | +29.36% | +246.65 |
| XOM  | 01-01 | 120.33 | 02-27 | 152.59 | 840 | +26.81% | +225.20 |
| AMAT | 01-08 | 281.51 | 02-27 | 372.64 | 840 | +32.37% | +271.91 |
| NTR (slot, sold) | 01-01 | 61.73 | 01-09 | 59.84 | 840 | -3.06% | -25.72 |
| SNDK (same slot) | 01-09 | 363.01 | 02-27 | 631.54 | 814 | +73.97% | +602.35 |
| WDC stub 4% | 01-15 | 226.08 | 02-23 | 288.78 | 240 | +27.73% | +66.56 |
| | | | | | | **TOTAL** | **+$1,402.24 = +23.37%** |

**The constraint set is NOT the problem.** 6 slots / 40% SPY / 14% cap reaches +23.4%,
roughly 2x the +12% goal. Rejecting the alternative hypothesis explicitly:

To clear +$720 you need the satellite to earn $705 net of the core's +$15.28.
4 slots x $840 => required average per-name return = **+21.0%**.
The run's OWN 2026-01-01 sized slate contained XOM +26.8%, TNDM +29.4%, NTR +21.6%.
Best-4 of the day-1 slate alone, held at the full 14%, = **+11.30%** — one basis point
shy of goal from bar 1 with zero further trading. Add the one swap in section 3 and it is +20.8%.

## 2. WHERE THE $820 WENT: the core is asked, the SATELLITE CAP refuses

The sizer sized **$44,239 of conviction notional across 51 sizings** into a $6,000 account.
Actual BUY fills: **$8,164 across 13 fills (18.5%)**.

`[core] funding request trimmed $A -> $B` — 16 events, `backend/broker.py:14493`:

```
2026-01-02 req $2,517 -> $1,050      2026-01-14 req $3,507 -> $0
2026-01-05 req $1,703 ->   $759      2026-01-15 req $1,783 -> $0
2026-01-06 req $2,573 ->   $761      2026-01-16 req   $891 -> $0
2026-01-07 req $1,712 ->   $687      2026-01-19 req $3,554 -> $0
2026-01-08 req $2,573 ->   $678      2026-01-20 req $3,549 -> $0
2026-01-09 req $3,459 ->    $12      2026-01-28 req $2,712 -> $29
2026-01-12 req $3,463 ->    $21      2026-02-23 req   $210 -> $0
2026-01-13 req $2,605 ->    $19      2026-02-26 req   $917 -> $51
TOTAL asked $37,728  released $4,067  = 10.8%
FROM 2026-01-09 ON: asked $26,650  released $132  = 0.50%
```

The core is willing. The refuser is named in the line itself:
`— satellite headroom will refuse the remainder; releasing core for it would only be bought back`.
`backend/broker.py:14931`: when `_sat_room <= _CORE_MIN_SATELLITE_TRIM_USD` the code does
`continue` — the buy is dropped. 42 `SATELLITE CAP` events, 28 of them `skipped`.

SNDK, the run's own #1 name (`Discovered stock (momentum): SNDK (20d=+15.6%, 60d=+95.9%)`,
final `SNDK: $237.33 -> $631.54 (+166.10%)`), was refused **six times**:

```
2026-01-09  SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($12 room)   px $363.01  fwd +73.97%
2026-01-12  SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($21 room)   px $388.46  fwd +62.58%
2026-01-14  SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($-1 room)   px $393.06  fwd +60.67%
2026-01-19  SATELLITE CAP: SNDK skipped — satellite at its overflow ceiling ($-28 room)  px $413.55  fwd +52.71%
2026-01-28  SATELLITE CAP: SNDK trimmed $904 -> $29 to keep the core at target           px $510.41  fwd +23.73%
2026-02-23  SATELLITE CAP: SNDK skipped — satellite at its design share ($-729 room)     px $665.06  fwd  -5.04%
```

The sizer wanted **$865 / $866 / $877 / $888 / $904** (14-15% of NAV) on those bars. It got **$29**.
Result: `SNDK: P&L = $6.91 (+23.73%)` on a +166% name. That single line is the whole gap.

Refused-name forward returns, 915207: mean **+6.91%**, n=28 skips. Split by the run's own
60d momentum: 60d >= +50% -> mean **+23.52%** (n=10); everything else -> **-2.31%** (n=18).

## 3. THE SINGLE HIGHEST-VALUE CHANGE: score-ranked satellite trim-back

**Mechanism.** `_core_sleeve_satellite_headroom` (`backend/broker.py:3318`) computes
`satellite = max(0.0, nav - cash - core_value - hedge_value)` and returns the room left to
the 60% design share. The satellite can only GROW toward the share. There is no path in
which a HIGH-conviction newcomer causes the WEAKEST satellite name to be sold. The cap is
entry-only — exactly OBJECTIVE.txt blocker #3.

**Change.** At `backend/broker.py:14931`, when `_sat_room <= _CORE_MIN_SATELLITE_TRIM_USD`
AND `_sat_is_conv` (raw >= `satellite_conviction_min_raw`, 1.50), do not `continue`. Instead
find the lowest-`raw_score` held satellite name (age >= N bars, not the core, not the hedge)
and, if `incoming_raw - weakest_raw >= margin`, submit the sell and the buy as ONE paired
order, sized off the sale proceeds. Satellite share is unchanged, so no core release is
requested and the churn objection in the docstring (`extending the release without extending
the buy ... $2,600 of one-way notional for zero net allocation change`) does not apply.

Do NOT do this by re-enabling the V28 rotation lane. That lane is deliberately off
(`V28 ROTATION SKIP: SNDK raw=1.700 < min_score=99.000`, 274 lines; `fired=0/4` on every bar;
zero rotations in 915207) and _SYNTHESIS #5 measured it selling without buying 5 for 5.
The swap must be atomic at the cap site.

**Effect on 915207, using only what the run knew on 2026-01-09:**

```
held-satellite conviction_tier raw_score on 2026-01-09:
  AAL 1.0 | AMAT 0.0 (age 1d) | BA 1.0 | NTR 1.0 | TCMD 0.0 (age 7d, tier LOW) | VOYA 1.0 | XOM 1.0
incoming: SNDK raw=1.700 tier=HIGH
```

Rule picks TCMD (lowest raw, oldest eligible).

* sell TCMD 28.938 sh @ $30.23 = $874.81 (cost $814.89) -> realized +$59.92; actual TCMD leg was +$32.55 => **+$27.37**
* buy SNDK $875 @ $363.01 -> $631.54 => **+$647.12**; actual $29 stub was +$6.91 => **+$640.21**
* **Final $7,249.61 = +20.83%**, vs +9.70%. **+$667.58 from ONE swap.** Clears +12% by $530.
* (selling VOYA instead — raw 1.0 but the run's only loser, -$75.09 — gives **+22.37%**.)

Holding is already proven: the run held its $29 SNDK stub 30 days through
`Trailing stop SUPPRESSED (trailing_stop_disabled): SNDK drop=15.7% >= 15% — held`.
From a $363.01 entry the position is never underwater, so the -10% breaker never arms.
Exits are not the leak (_SYNTHESIS "DO NOT TOUCH"); nothing in the exit stack changes.

## 4. GENERALIZABILITY — stated plainly

**The mechanism reproduces on 2 of 2 bull/chop windows** (same code path, same log strings):

| run | window | core-funding requests | asked | released | % | sized notional | filled | % |
|---|---|---|---|---|---|---|---|---|
| 915207 | bull/chop 01-01..03-01 | 16 | $37,728 | $4,067 | **10.8%** | $44,239 | $8,164 | 18.5% |
| 383778 | OOS bull 03-30..04-27 | 16 | $50,523 | $8,695 | **17.2%** | $55,526 | $13,989 | 25.2% |
| 542754 | bear 03-02..03-30 | 0 | $0 | $0 | n/a | — | — | — |

383778 shows the identical refusal pattern — six consecutive bars at ~$0 released
(`04-13 $2,533 -> $0`, `04-14 $3,486 -> $0`, `04-15 $3,485 -> $0`, `04-21 $3,686 -> $16`),
20 `SATELLITE CAP ... skipped`, 21 `... trimmed`, and its best name clipped:
`2026-04-08 SATELLITE CAP: AAOI trimmed $814 -> $718`, where `AAOI: $98.39 -> $145.78 (+48.17%)`
but `AAOI: P&L = $147.79 (+20.82%)`. `HOOD: $66.02 -> $83.84 (+27.00%)` returned `-$6.09`.

**But say it straight: the DOLLARS are demonstrated on one window.**
On 383778 the SATELLITE-CAP-refused basket averaged only **+1.23% forward (n=20)** — there
was no SNDK-class name being refused there (only 2 refused names had 60d >= +50%, mean +2.89%).
So the change is worth **+$668 on 915207** and is approximately **neutral on 383778**.
It is score-gated, so it does not fire when nothing good is being refused; that is why it
should not be able to hurt, but that is an argument, not a measurement. It is untestable on
542754: the core is OFF in a bear by design, zero funding requests, zero cap events.

**Required validation before promotion:** paired A/B on 915207 + 383778 + one non-semi
window, separate `history_scope_salt` per arm, equally cold. Confirm on the log that the
paired sell and buy BOTH fill on the same bar — the failure mode of every previous attempt
at this (_SYNTHESIS #5) is the sell landing and the buy not.

## 5. WHAT NOT TO DO

* Do not raise `max_positions`. 6 slots were never the binder: the satellite sat at its
  60% design share, which supports 4.3 names at the 14% cap. OBJECTIVE.txt already forbids it.
* Do not blanket-release the core. On 383778 the refused basket is +1.23%; releasing for
  everything buys $50k of mediocrity and is the churn the docstring at broker.py:3318 warns about.
* Do not touch exits. Capture vs actual entry is 99.99%; the SNDK stub survived a -15.7% drawdown.
* Do not trust `broker.py:14227`'s claim that "SATELLITE CAP ... is fixed". Measured on
  915207 it refused SNDK five times at +23% to +74% forward. It is not fixed.
