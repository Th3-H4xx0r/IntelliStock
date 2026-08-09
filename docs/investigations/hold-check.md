# hold-check — bt 427197, the 46.7% -> 66.7% drawdown is a NON-exit

Read-only. No code edited, no run started/stopped, nothing pushed.
Source: `backtests/427197.log` (32,163 lines, pulled 2026-08-09 while the run was
still `running` at progress **77.64%**), plus `/backtests/427197/summary`.
Builds on `_SYNTHESIS.md`, `exits-and-capture.md`, `gap-capital.md`, `gap-bull.md`.

---

## 0. HEADLINE — the parent's hypothesis is FALSIFIED, and the real leak is the opposite

**No winner was sold, trimmed, stopped or circuit-breakered anywhere in the stretch.**
Zero. The **-10% circuit breaker never fired on anything** in the whole run: there is
exactly **one** `gate=circuit_breaker … result=fired` line in 32,163 lines and its floor
is **-20%**, not -10%.

The -5.76pp give-back was caused by a position the exit stack **refused** to reduce:

> **SLV rode +60.3% to +4.5% — a $468.79 peak give-back (7.81% of the $6,000 book) —
> while `Trailing stop SUPPRESSED (trailing_stop_disabled)` printed 125 times on it.**

`exits-and-capture.md`'s claim "exits are NOT the leak" still holds for *realised* exits.
Its corollary — "the trailing stop is suppressed by design and re-arming it would have
exited all five big winners" — **no longer holds under this config**, because there is now
a name whose peak give-back (40.4%) is nearly **double** the worst give-back any historical
winner ever survived (22.4%, SNDK). A rule can separate them. Section 5.

---

## 1. WHERE THE STRETCH IS, IN DATES

Progress maps to trading bars. 38 distinct bar-dates processed at 77.64% =>
`bar_index / 48.9`. Derived from every `Monitor cycle complete | date=` line:

| progress | bar date |
|---|---|
| 47.0% | **2026-01-27**  <- the +15.88 peak checkpoint (46.7%) |
| 53.1% | 2026-01-30 |
| 61.3% | 2026-02-04 |
| 65.4% / 67.4% | **2026-02-06 / 02-07**  <- the +10.12 checkpoint (66.7%) |
| 69.5% | 2026-02-09 (the 70.1% / +11.14 checkpoint) |

**The drawdown stretch = 2026-01-27 -> 2026-02-06, 9 bars.**

---

## 2. EVERY EXIT IN THE STRETCH, IN DOLLARS — the complete list is 3 items

`FILL SELL` is the authoritative ledger. Six sells exist in the entire run
(`summary.total_sells = 6`): five SPY core legs and one alpha name.

| # | date | sym | mechanism, quoted | notional | realised |
|---|---|---|---|---|---|
| 1 | **2026-01-30 18:00** | **APP** | `[sell-gate] APP \| gate=circuit_breaker \| tier=HIGH \| regime=bull \| unrealized=-20.0% \| floor=-20.0% (base=-20%) \| result=fired` | $563.80 out | **-$136.58** |
| 2 | 2026-02-04 16:00 | SPY | `[core] released 0.6582 SPY @ 689.43 (core rebalance: funding (18.9% -> 19.0% of NAV) … source='residual_bull_refill')` | $452.18 out | sleeve, ~$0 |
| 3 | 2026-02-05 16:00 | SPY | `[core] released 0.0366 SPY @ 686.10 (core rebalance: funding (12.4% -> 19.7% of NAV) … source='residual_bull_refill')` | $24.75 out | sleeve, ~$0 |

`summary.round_trips = 1`, `losing_round_trips = 1`, `winning_round_trips = 0`,
`total_round_trip_pnl = **-136.577**` — that is APP, to the cent, and it is the run's
**only** completed round trip. My reconstruction:
`1.12927950 x (499.254095 - 620.161940) - 0.016914 - 0.021010 = -$136.58`. ✓

**APP was not a winner.** Its whole life, from `Monitor decision` lines:
peak `cp=$675.06` on 2026-01-13 = **+8.85%** ($762 mark on a $700 cost), then monotone to
`day 22 pnl=-20.0% cp=$496.04 entry=$620.16 -> SELL`. Cutting it was correct: it was
-19.5% and falling, and the -20% floor is exactly where a HIGH-tier name should stop.

**Mechanisms that fired ZERO times in the stretch (and in the whole run):**
`Trailing stop` fired, `Hold-limit EXIT`, `Profit take TRIGGER`, `fast_loser_cut`,
`Momentum rotation: sell`, `Momentum portfolio swap: sell`, `ML overlay PRESERVE
forced-exit`, `[sleeve] released`, any `TRIM` of a profitable name. Confirmed by grep:
0 matches each. The only `trim` strings in the log are `[core] funding request trimmed`
and `SATELLITE CAP: X trimmed` — both **buy-side** refusals, not exits.

**Winner exits BLOCKED in the stretch** (the stack was working, hard):
```
2026-02-04  [sell-gate] AMAT | gate=winner_protect | pnl=+11.6% | drop_from_peak=8.0% < 8.0% | result=blocked (hold)
2026-02-04  [sell-gate] BALL | gate=winner_protect | pnl=+13.8% | drop_from_peak=0.0% < 8.0% | result=blocked (hold)
2026-02-05  [sell-gate] BA   | gate=winner_protect | pnl=+7.1%  | drop_from_peak=6.0% < 8.0% | result=blocked (hold)
```
19 `winner_protect … blocked (hold)` lines run-wide. **Not one winner was let go.**

### Where the APP proceeds went — the one second-order cost
$563.80 of cash released by the circuit breaker was redeployed into
`FILL BUY SPY $510.60` (02-02) + `FILL BUY LLY $130.33` (02-05, a 2.2%-of-NAV runt after
`SATELLITE CAP: LLY trimmed $882 -> $610`). Net core purchases in the stretch **+$481.73**.
A conviction slot was liquidated and the money went back into the index. That is the
`gap-capital.md` satellite-headroom binder re-expressing itself, not an exit fault.

---

## 3. WHAT ACTUALLY COST THE 5.76pp — attribution, 01-27 -> 02-06

Marks are the last `Monitor decision … cp=$` on each bar; share counts are the
`FILL BUY` quantities.

| sym | shares | 01-27 | 02-06 | $ change |
|---|---:|---:|---:|---:|
| **SLV** | 12.47107 | $101.55 | $70.51 | **-$387.10** |
| APP (sold 01-30) | 1.12928 | $544.16 | $499.25 | -$50.71 |
| AMAT | 3.03184 | $332.70 | $322.51 | -$30.89 |
| CPER | 23.94445 | $36.57 | $36.22 | -$8.38 |
| BA | 1.07717 | $244.55 | $243.06 | -$1.60 |
| BALL | 0.85841 | $56.99 | $66.48 | +$8.15 |
| SBLK | 43.06555 | $22.02 | $22.72 | +$30.15 |
| TDY | 1.63351 | $615.96 | $647.46 | +$51.46 |
| | | | **total** | **-$388.95 = -6.48pp** |

Reported move: +15.88 -> +10.12 = **-5.76pp = -$345.60**.
**SLV alone is -$387.10 = 112% of the entire reported drawdown.**
Everything else nets +$1.85. This is a one-name mark-to-market event, not an exit event.

---

## 4. THE SLV TAPE — a +60% winner round-tripped to flat with 125 suppressions

Cost `12.47106599 sh @ $67.353890 = $840.00` (14.0% of NAV, bar 1, 2026-01-02 14:00).

| bar | px | mark | vs cost |
|---|---:|---:|---:|
| entry 01-02 | $67.35 | $839.97 | — |
| 01-27 (46.7% checkpoint) | $101.55 | $1,266.44 | **+$426.46** |
| **peak 01-29** | **$107.99** | **$1,346.75** | **+$506.78 (+60.3%)** |
| 01-30 | $77.12 | $961.77 | +$121.79 |
| **trough 02-05** | **$64.37** | **$802.76** | **-$37.21 (below cost)** |
| 02-13 (latest bar) | $70.40 | $877.96 | **+$37.99 (+4.5%)** |

**Peak-to-latest give-back = $468.79 = 7.81% of the $6,000 account.** It is the single
largest item in the run, larger than the entire realised loss ledger (-$136.58) by 3.4x.

The stack saw it and stood down, every bar:
```
2026-01-30  Trailing stop SUPPRESSED (trailing_stop_disabled): SLV drop=28.2% >= 12% — held; catastrophic stop is the floor
2026-02-02  Trailing stop SUPPRESSED (trailing_stop_disabled): SLV drop=36.5% >= 12% — held; catastrophic stop is the floor
2026-02-05  Trailing stop SUPPRESSED (trailing_stop_disabled): SLV drop=40.5% >= 12% — held; catastrophic stop is the floor
```
125 SLV suppressions run-wide (70 inside the stretch), plus 5 on AMAT. `drop=` reached
**40.5%**. The threshold in force was `trailing_stop_commodity_etf_pct = 12` and it
widened to 24 on 02-06 — *after* the damage.

### Why nothing else could catch it — this is the mechanism, and it is structural
Every other exit is **entry-anchored**; only the trailing stop is **peak-anchored**.
SLV's worst unrealised-vs-entry was `64.37/67.35 - 1 = **-4.4%**`, so:
* `circuit_breaker` floors (-15 LOW / -20 MID / -25 HIGH, `graph_nexus_analysis.py:8709-8719`) — unreachable.
* `catastrophic_stop_pct = -40` (doc-193) — unreachable.
* `fast_loser_cut` — unreachable.
* `winner_protect` — a *blocker*, never an exit.
* `trailing_stop` — the only peak-referenced gate, and `trailing_stop_disabled = True`
  (`scripts/doc193_backup_patch_20260808T110842Z.json` -> `/strategies[0]/config/trailing_stop_disabled = True`),
  suppressed at `graph_nexus_analysis.py:20399-20411`.

**Worse: the two config keys an operator would read as "protect a big winner" make the
exit LESS likely.** `peak_protection_enabled=True`, `peak_protection_min_peak_pnl_pct=30`,
`peak_protection_max_drawdown_from_peak_pct=25` are all set in doc-193 — and they are
**not an exit**. `graph_nexus_analysis.py:20099-20144` uses them only to *bypass* the
fast-loser cut:
```
20142:   f"current_pnl={_unrealized_pct:+.1f}% — deferring to trailing stop",
```
It defers to a stop that is disabled. SLV satisfied both peak-protection conditions
(peak +60.3% >= 30, drawdown 40.4%... note >25 so it fell out of the bypass band anyway)
and there was no exit on the other side of the deferral either way.
The repo already knows this: `backend/tests/test_adv_exit_discipline_findings.py:82`
— *"F1 — trailing_stop_disabled removes the ONLY peak-referenced protection."*

---

## 5. THE FIX, AND WHY IT IS GENERALIZABLE

**Rule: a peak-referenced profit-lock — exit (or halve) a position once
`peak_pnl >= +30%` AND `drawdown_from_peak >= 25%`.** Mechanically this is
`peak_protection_*` inverted: same two numbers already in doc-193, wired as an *exit*
instead of a *bypass*.

This is **not** re-arming the 12-15% trailing stop. `exits-and-capture.md` is right that
a 15% trail kills the franchise. The 25% band is chosen because it sits above every
give-back a real winner has ever survived in this codebase and below the one that killed
this run.

I replayed the rule over the `Monitor decision … cp=$` series of **six runs, five
windows, three regimes**. `maxdd` = worst drawdown-from-peak the name ever printed.

| run | window | name | peak | ended | maxdd from peak | rule fires? |
|---|---|---|---:|---:|---:|---|
| 820236 | bull 01-01..03-01 | WDC | +69.3% | +53.6% | 17.1% | **no** |
| 820236 | " | SNDK | +57.0% | +42.3% | **22.4%** | **no** |
| 820236 | " | LRCX | +37.5% | +28.4% | 16.2% | **no** |
| 915207 | bull 01-01..03-01 | AMAT | +40.5% | +32.4% | 15.4% | **no** |
| 915207 | " | SNDK | +36.5% | +23.7% | 22.4% | **no** |
| 915207 | " | XOM | +30.1% | +26.9% | 6.9% | **no** |
| 613166 | bull 01-01..03-01 | AGMI | +33.5% | +31.5% | 20.3% | **no** |
| 383778 | OOS bull 03-30..04-27 | AAOI | +36.1% | +24.4% | 12.4% | **no** |
| 542754 | bear 03-02..03-30 | (none >= +30% peak) | — | — | — | **no** |
| **427197** | bull 01-01..03-01 | **SLV** | **+60.3%** | **+4.5%** | **40.4%** | **YES, 2026-01-30** |

**Total fires across the five prior runs: 0 of 8 winners. Total fires in 427197: 1.**
The worst give-back any surviving winner ever printed is 22.38% (SNDK, in two separate
runs); the rule arms at 25%. It has 2.6pp of clearance and it is not knife-edge — at a
23% threshold the SLV exit bar is identical, because SLV gapped 21.2% -> 28.2% from peak
inside one bar on 01-30.

### Dollars, on 427197
First bar the rule arms: **2026-01-30**, at the `drop=28.2%` print, i.e.
`107.99 x (1 - 0.282) = $77.59`.
* exit 12.47106599 sh @ $77.59 = **$967.63**, realising **+$127.66**
* actually held to the 02-13 bar at $70.40 = $877.96, **+$37.99**
* **delta = +$89.67 (+1.49pp)** as of the latest bar, and **+$164.87 (+2.75pp)** measured
  at the 02-05 trough.

Stated plainly: **the recoverable dollars here are $90-$165, not the full $468.79.** No
peak-anchored rule that leaves WDC/SNDK/LRCX/AGMI alone can catch more than that, because
SLV was already 28% off its high the first time it crossed a safe threshold — it gapped.
The rule's value is that it converts an unbounded give-back into a bounded one; on this
window that bound is worth ~1.5pp, and the run's checkpoint deficit was 5.76pp.

### Caveats I will not paper over
1. **427197 is still running** (77.64%, latest bar 2026-02-13). SLV at $70.40 could
   recover by 03-01, shrinking the delta, or fall further, growing it. The +$468.79
   peak give-back is already realised in the equity curve
   (`portfolio_value_high = $7,048.72` vs current `$6,588.67` = -$460.05) and will not
   un-happen; only the exit *delta* is open.
2. **One firing is one firing.** The mechanism (peak-anchored vs entry-anchored) is
   proven on 6 runs; the *profit* of the rule is measured on 1. The honest claim is
   "provably inert on 8 historical winners across 5 windows and 3 regimes, and positive
   on the one case that broke this run" — not "worth X pp per window".
3. SLV is a **commodity ETF at a 14%-of-NAV clip**. The deeper question — why $840 of a
   $6,000 alpha book is in silver at all — is an entry/sizing question and belongs with
   `gap-capital.md` / `discovery-and-ranking.md`, not here.

---

## 6. DIRECT ANSWERS TO THE PARENT'S QUESTIONS

* *Did any winner get sold, trimmed, stopped or circuit-breakered between 46.7% and 66.7%?*
  **No. Zero.** One alpha sell in the stretch (APP), a -19.5% loser at a -20% floor,
  -$136.58. Two SPY core funding releases, $476.93 combined, ~$0 P&L. 19
  `winner_protect … blocked (hold)` lines prove the stack actively refused to sell winners.
* *Do `exits-and-capture.md`'s conclusions still hold under the new config?*
  **Half.** "Exits are not the leak" — **holds**: realised exits cost $136.58 total and the
  one that fired was correct. "Trailing-stop suppression is right / re-arming it would
  exit all the big winners" — **holds for a 12-15% trail, fails as a general defence**:
  suppression let a +60.3% winner round-trip to +4.5% for a $468.79 give-back, because
  there is no other peak-referenced gate in the tree.
* *Is the -10% circuit breaker now firing on names it previously never reached?*
  **No — falsified.** One circuit-breaker fire in the run, `floor=-20.0% (base=-20%)`,
  `tier=HIGH`. Tier mix run-wide: 1,300 LOW / 1,049 HIGH / 692 MID, and the LOW/MID/HIGH
  floors are -15/-20/-25 (`graph_nexus_analysis.py:8709-8719`). There is **no -10% floor
  in this config at all**; the only `floor=10` strings in the log are
  `Buy budget floor: … (floor=10% of $NAV)`, an unrelated buy-side line.
