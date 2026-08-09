# gap-oos — why bt 383778 shorted the bottom, and the one rule that stops it

Scope: bt 383778 (OOS bull 2026-03-30..04-27, +4.73%, SQQQ -$257) vs bt 542754
(bear 2026-03-02..03-30, +11.94%, SQQQ +$889). Both $6,000, v2-let-run-core, cold.
Everything below is quoted from `backtests/383778.log` / `backtests/542754_sweep.log`.

## 0. Size of the prize

383778: `Final Value: $6,284.09` -> profit $284.09.
The SQQQ round trip cost **$256.98** = **90.5% of the entire window's profit**.
Kill it and 383778 goes +4.73% -> ~+9.0%. Nothing else in that run is close
(next largest single item: AAOI +$148).

542754: `Final Value: $6,716.44` -> profit $716.44. SQQQ P&L (reconstructed from
every FILL line) = **+$889.44** on 109.3985 sh bought avg $72.64, 57.8713 sh sold
avg $72.73, 51.5272 sh still open at $89.80. **SQQQ is 124% of that window's
profit.** Any fix that dents it is a net loss.

## 1. What actually happened in 383778 — order #2 of the entire run

    383778.log:121   V31 market regime: bear (raw=bear, proxy=QQQ, closes=153, ret20=-7.38)
    383778.log:1825  [sleeve] parked $2100.00 in BEAR leg SQQQ @ 87.51 (regime=bear,
                     leg=2100/2100 cap, alloc=35%, order_id='sim-000000000002-SQQQ')
    383778.log:1827  FILL BUY SQQQ qty=23.94171050 price=86.923602 quote=2026-03-30 15:00
    383778.log:3347  [sleeve] released 23.9417 SQQQ @ 76.97 (leg stop-loss: 76.97 <= 86.92 -10%)
    383778.log:3349  FILL SELL SQQQ qty=23.94171050 price=76.195114 quote=2026-04-01 16:00

23.9417 x (86.9236 - 76.1951) + $0.117 fees = **-$256.98**. Sim order id
`sim-000000000002` — the hedge was the SECOND order the run ever placed, on the
first cycle of the first bar, with the whole $6,000 still in cash.

Bear lasted exactly 4 sessions:

    03-30 bear ret20=-7.38 | 03-31 bear -8.18 | 04-01 bear -4.08 | 04-02 bear -4.30
    04-03 chop -3.94 | 04-06 chop -3.94 | 04-07 bull -1.91 | ... | 04-27 bull +10.67

## 2. The three gates the operator asked about — all three FAIL

**(a) Freshness gate `residual_sleeve_bear_require_fresh_pct` — cannot help, it is
already at its useful limit.** It is ON in both runs (same doc-193 config) and it
*worked* in 542754, deferring the first park by 3 sessions:

    542754.log:427   [sleeve] bear leg SKIPPED — stale bear (ret5=-0.2% > -0.5%)   [03-02]
    542754.log:2379  [sleeve] bear leg SKIPPED — stale bear (ret5=+1.1% > -0.5%)   [03-03]
    542754.log:5549  [sleeve] bear leg SKIPPED — stale bear (ret5=-0.1% > -0.5%)   [03-06]

In 383778 the string "stale bear" appears **zero times** — the gate passed on all
four bear bars. The detector's own replay of this exact window
(`graph_nexus_analysis.py:7240-7241`) gives the reason:

    sim     ret20   ret5   >ma10  off_low  %off 20d low  bars since low
    03-30   -7.57  -2.23   False   0.000     +0.00        0   <- IS the low
    03-31   -7.93  -3.57   False   0.000     +0.00        0   <- IS the low

ret5 = **-2.23%** on the bad bar vs **-0.2% / +1.1%** on the bars the gate
correctly suppressed in the real bear. The bad bar is *more* fresh, by 4x.
Tightening the threshold makes it worse; there is no value of this knob that is
ON in 542754 and OFF in 383778. (Confirms the note at `broker.py:4784-4791`.)

**(b) Regime warm-up — nothing to fix, and delaying does not help.** The detector
had `closes=153` on bar 1 (needs 21). It was not cold. And blocking bar 1 only
moves the entry to 03-31, where SQQQ's first print is **$89.80** (383778 monitor
lines, 03-31 first cp) — a *higher* entry. The -10% stop trips the same day
(03-31 last cp $80.19). Delay-by-one-bar saves ~$47 of $257.

**(c) `residual_sleeve_bear_min_dwell_days` — the in-code rationale is factually
wrong on the RUN.** `broker.py:2884-2886` claims "2026-03-02..03-30 runs 21
consecutive bear days". Measured from 542754's 24 regime lines, the dwell counter
(`graph_nexus_analysis.py:26713-26719`, resets on ANY chop bar) actually goes:

    03-02:1  03-03:2->0(chop)  03-04:1  03-05:2  03-06:3  03-09..03-12:0
    03-13:1  03-16:2  03-17:3  03-18:0  03-19:1  03-20:2  03-23:3 ... 03-30:8

**542754's first park happened at dwell = 1** (`542754.log:4146`, 03-05; last
detect was 03-04). 383778's bad park was also at dwell = 1. Dwell does not
separate them at the decision point. Sweeping the threshold:

    min_dwell   383778 first park          542754 first park        net
    2           03-31 @ ~$89.80 (worse)    03-05/06 @ ~$70.8-73.4   ~ -$40
    3           04-01 @ ~$80.19            03-06 @ ~$73.4           ~ +$60
    5           never (max dwell = 4)      03-25 @ ~$77.08          +$257 / -$190 = +$67

min_dwell=5 is the only setting that fully suppresses 383778, and it costs 542754
~$190 of its $889 (entry 70.80 -> 77.08 turns a +26.8% leg into +16.5%). Near wash.

**(d) Scale-in — did not exist to be blamed.** 542754 also went to the full cap on
its first park (`leg=3166/4221` then `4221/4221`, same bar). Staging the entry
costs 542754 too: all four of its later adds filled at $76.19/$71.40/$75.58/$75.42
— all ABOVE the $70.80 first fill.

## 3. What DOES separate them: where in the range the hedge was bought

SQQQ price, from the monitor lines in both logs (187 prints in 542754, 14 in 383778):

    542754 SQQQ cp range 69.05 .. 89.80  | first park FILL $70.80 (03-05)
    383778 SQQQ cp range 76.97 .. 89.80  | only  park FILL $86.92 (03-30)

$89.80 is the maximum SQQQ print in **either** log (542754 03-30 close, 383778
03-31 open). So:

* **542754 bought the hedge at $70.80 — 1.2% off the LOW of its entire range,
  78.8% of the eventual high.** QQQ ret20 that day was **-0.95%**, and **+0.54%
  the next day** (`542754.log:4255`, `:5026`), with `raw=chop` on both — the label
  was "bear" only through hysteresis. The tape was flat over 20 sessions. It then
  rode 70.80 -> 89.80 = **+26.8%**.
* **383778 bought the hedge at $86.92 — 96.8% of the high**, on a bar the
  detector's own replay records as `bars since 20-session low = 0, %off low
  +0.00`. It bought the inverse ETF at the exact extension top and the market
  turned within one session.

This is the entry-extension problem the long book already gates for — and the
sleeve is the one position in the book that is **exempt from every extension
check**. It sizes on regime label + ret5 only.

## 4. THE RULE (ON in 542754, OFF in 383778)

> **Do not OPEN a bear-leg episode on a bar where the regime proxy is at a fresh
> 20-session closing low (`bars_since_20d_low == 0`). ADDs to an already-open leg
> are untouched. Pair it with the already-written, currently-OFF
> `regime_rally_onset_enabled=True`, which covers the two bars after the low.**

Bar-by-bar on 383778's only four bear bars:

    03-30  since_low=0                       -> first-park gate BLOCKS   (saves the $257)
    03-31  since_low=0                       -> first-park gate BLOCKS
    04-01  since_low=1, bounce +2.91, >ma10  -> rally_onset BLOCKS (broker.py:4800-4804)
    04-02  since_low=2, bounce +3.68, >ma10  -> rally_onset BLOCKS
    04-03  regime = chop                     -> no bear path at all

Result: SQQQ notional in 383778 = **$0**. `+$256.98`, +4.73% -> ~+9.0%.

On 542754:

    03-05 first park: ret20 -0.95% / +0.54% next day, raw=chop, SQQQ $70.80 =
          1.2% off the leg's low print -> NOT at a fresh low -> gate PASSES.
    03-14/03-17/03-20/03-23 parks are ADDs to an open leg -> gate does not apply.
    rally_onset: the author's own 2022-2026 calibration (graph_nexus_analysis.py:7271)
          — "ZERO fire inside 2026-03-02..03-30 — that window never reclaims its
          10-day MA on any of its 20 sim days."

Result: SQQQ P&L in 542754 = **unchanged, +$889.44**.

Why this predicate and not the others: it keys on *position in the range*, not on
*magnitude of decline* or *duration of label*. Both of those are LARGER in the bad
case (ret20 -7.38 vs -3.93; ret5 -2.23 vs -0.2) — every magnitude/duration knob is
therefore inverted against you. Range position is the only measured quantity that
points the right way in both windows.

## 5. Implementation (surgical, default-OFF)

1. `graph_nexus_analysis.py:7406-7412` — next to the unconditional `diag["ret5"]`
   stamp, add an unconditional `diag["bars_since_20d_low"]` (the `_since_low`
   expression already exists at `:7302`, but it is unreachable behind the
   `regime_rally_onset_enabled` early-return at `:7277`).
2. `broker.py:4805-4816` — in the first-park gate block (where `bear_min_dwell_days`
   already lives, which is documented as "Gates the first park only"), add
   `residual_sleeve_bear_block_at_fresh_low_bars` (default 0 = OFF): skip when the
   leg is flat AND `bars_since_20d_low <= N`. Log it like the neighbours.
3. Set `regime_rally_onset_enabled=True` on doc-193. Already written and tested.

Every EXIT path (leg stop, trailing bank, protective exit, episode latch) is
untouched, as is the whole long book.

## 6. Honest limits

* **N=2 windows have any bear bars at all.** 915207 (2026-01-01..03-01) has 26 chop
  + 17 bull regime bars and **zero** bear bars — no SQQQ, so it cannot corroborate.
  The mechanism is measured on 542754 and 383778 only.
* Independent replication of the FAILURE (not of the fix) exists: `broker.py:2879-2884`
  records "17 of 19 backtests on the 2026-03-30..04-27 bull window parked 35% of NAV
  in a 3x inverse on DAY 1 ... for a deterministic -3.94pp". Same window, so it
  proves the loss is deterministic, not that the fix generalises.
* `bars_since_20d_low` is **not logged today**. For 383778 it comes from the
  detector's own replay table of this exact window (`graph_nexus_analysis.py:7240-7241`,
  SPY proxy; 383778 ran QQQ proxy on 03-30, and SQQQ printing its cross-log maximum
  $89.80 on 03-31 confirms the index low is 03-30/03-31). For 542754 on 03-05 it is
  **inferred** from ret20 = -0.95% / +0.54%, `raw=chop`, and SQQQ filling 1.2% off
  its range low. **Verify before shipping:** stamp the diag (step 1 above, inert on
  its own) and re-run 542754 cold; if `bars_since_20d_low == 0` on 03-05 the rule is
  dead and the honest answer is that no gate separates these two windows.
* The counterfactual for 383778 is the direct $257 plus compounding; it assumes the
  freed $2,100 stays in cash (bear regime caps `max_positions 6 -> 2`,
  `383778.log:373`), which is what the sleeve does when the bear leg is skipped.
