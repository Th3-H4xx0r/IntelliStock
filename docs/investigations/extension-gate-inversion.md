# The entry-extension gate: inversion, decay and direction-blindness

Read-only investigation. **No production code was edited**, no backtest started or
stopped, nothing pushed. Written 2026-08-09.

Evidence base: the finished logs of **bt 201039** (+8.34%, primary), **bt 820236**
(+12.33%) and **bt 613166** (+9.17%) — all `v2-let-run-core`, 2026-01-01..2026-03-01,
$6,000, 3600s — pulled with `scripts/pull_backtest_logs.py` and re-read from
`backtests/201039_full.log`, `backtests/820236_20260808-142050Z.log`,
`backtests/613166_20260808-224744Z.log`.

Code anchors: `backend/strategies/graph_nexus_analysis.py` sha256[:12] `a3170579e0c9`
(32,495 lines), `backend/broker.py` sha256[:12] `a9fff5854de0` (16,406 lines), git
`3f474f5`. Config anchors: the doc-193 backups in `scripts/doc193_backup_*.json`.

Every number below is reconstructed from a quoted log line or a quoted source line.
Where I could not prove something I say so.

---

## 0. The answer to the central question

> *How can a name be blocked at +28.5% early and admitted after +178%?*

**Because the gate is not on.** `entry_extension_block_pct` is overridden to `0` inside
`regime_profiles.bull` and `regime_profiles.recovery`. The gate is armed only in
chop/bear — i.e. only while the market is going sideways and momentum names are still
based — and it is switched **completely off** in bull, which is exactly the state in
which extended names get bought.

Across the three runs the gate fired **393 times. Zero of those fires happened under a
bull profile.**

| run | gate fires | under lagged-regime `chop` | under lagged-regime `bull` |
|---|---|---|---|
| 201039 | 106 | 103 (+3 pre-first-regime) | **0** |
| 820236 | 157 | 151 (+6 pre-first-regime) | **0** |
| 613166 | 130 | 129 (+1 pre-first-regime) | **0** |

(The one-bar lag is `broker.py:5890-5891` — *"Uses the PREVIOUS full cycle's stamped
regime (one-bar lag)"*. The lag is visible in the data: under the **same-bar** regime
label 33 of the 393 fires look like "bull" fires; under the **previous** bar's label,
zero do. That discriminator confirms the merge path.)

And there is a second, independent defect that would keep the gate useless even if it
were always armed: **the metric is a trailing high/low RANGE, not an extension
measure.** It decays to zero as the vertical leg rolls out of the lookback, and it is
blind to direction. Section 3 quantifies both.

---

## 1. What the gate actually is

### 1.1 Config (doc-193, identical in every `scripts/doc193_backup_*.json`)

```
entry_extension_block_pct              = 25
entry_extension_lookback_bars          = 20
entry_extension_glitch_ceiling_pct     = 0      (fail-open ceiling disabled)
entry_extension_require_bars           = False  (no bars -> fail OPEN)
momentum_watchlist_track_extension_blocked         = True
momentum_watchlist_track_extension_blocked_in_bear = True
regime_profiles.bull.entry_extension_block_pct     = 0     <-- gate OFF in bull
regime_profiles.recovery.entry_extension_block_pct = 0     <-- gate OFF in recovery
```

The 25% threshold is confirmed in every block line of all three runs
(`"> 25% — buy blocked"`).

### 1.2 The metric — `graph_nexus_analysis.py:9259-9281`

```python
def _recent_runup_protect(sym, price_history, block_pct, lookback_bars):
    """True when a position's recent close range ran up more than block_pct
    over the last lookback_bars bars ...
    Used to spare such a name from a forced exit at a local dip (run-163943:
    UAL/DELL cut at local bottoms before large rebounds)."""
    ...
    bars = (price_history.get(sym) or [])[-max(2, int(lookback_bars or 0)):]
    ...
    lo = min(closes)
    hi = max(closes)
    runup_pct = ((hi - lo) / lo) * 100.0 if lo > 0 else 0.0
    return (runup_pct > _bp), runup_pct
```

Three facts follow directly from those four lines:

1. It is **`(max − min) / min` over a trailing window** — a *range*, i.e. a realised
   volatility measure. It says nothing about where the price sits inside that range.
2. It is **direction-blind**: a name that fell 40% and one that rose 40% return the
   identical number.
3. It has a **finite memory of exactly `lookback_bars`**. Once the vertical leg exits
   the window the reading collapses to the consolidation range, no matter how far the
   price is above its base.

The docstring is explicit that this helper was written as an **exit suppressor**
(`spare such a name from a forced exit at a local dip`). It is reused verbatim as an
**entry blocker**. The two uses want opposite properties.

### 1.3 The five call sites

| site | lane string in the log | bars argument |
|---|---|---|
| `gna:23224-23289` | `Entry extension gate: <sym> …` | `price_history` param of `_apply_quality_filter` |
| `gna:28640` | `V32 mw_buy extension-block: …` | `data` |
| `gna:30290` | `V32 mw_rotation extension-block: …` | `data` |
| `gna:30572` | `V32 mw_swap extension-block: …` | `data` |
| `gna:30786` | `V32 mw_breakout_add extension-block: …` | `data` |

Observed lane mix (201039 / 820236): `quality_filter` 79/95, `mw_buy` 10/33,
`mw_swap` 10/18, `mw_rotation` 7/9, `mw_breakout_add` 0/2.

Held positions are exempt (`gna:23211-23215`, `if _pos_qty > 0: continue`), so the gate
only ever governs **new** entries and rotation *incomings*.

### 1.4 The lookback is ambiguous in *bar shape*

`_scale_bars` (`gna:257-286`) rescales the **count** by the run's cadence. This run is
3600s and the baseline is 3600s, so `20 -> 20`, a no-op.

But `_resolve_asof_bars` (`gna:9298-9329`) then chooses between two sources with
different bar *shapes*:

```python
bars = price_history.get(sym) or []
if len(bars) >= min_bars:          # min_bars = 2
    return bars                    # broker bars — HOURLY in a 3600s run
overlay = strategy_cache["_overlay_bars_raw"]        # DAILY bars
...
if date_key:
    raw = _visible_overlay_bars(raw, strategy_cache, date_key)
return raw if len(raw) >= min_bars else bars
```

So the same config value `20` means **~3 trading sessions** when the broker happens to
hold hourly bars for the symbol, and **~20 trading sessions (about a month)** when it
falls back to the daily overlay. A 6.5x difference in the measured horizon, decided by
data plumbing rather than by design. (Note also that the `date_key` lookahead filter is
applied *only* on the overlay branch.)

Log evidence that both horizons are in play, and that the readings are therefore not
reproducible from a single window:

* `820236:267` reads SNDK **+28.5%** on 2026-01-01 (price $237.33) and `820236:5737`
  reads **+73.2%** on 2026-01-07 (price $335.90). The in-window daily range on 01-07
  (01-01 $237.33 -> 01-07 $335.90) is only **+41.5%** and the last-3-session daily range
  is **+24.2%** — neither can produce 73.2%, so that read reached back before the window
  start.
* `201039:5243` reads PLRZ **+78.8%** on 2026-01-06 while PLRZ's entire in-window range
  to that date ($8.11 -> $13.195) is **+62.7%** — again, a window that reaches before
  01-01.

**I could not fully reconstruct which branch each individual read took**, because the
block line prints only the resulting percentage. That is itself a finding (see R4).

---

## 2. Mechanism A — the gate is disarmed in exactly the regime that buys extended names

### 2.1 The regime path is identical in all three runs

43 decision bars, `V31 market regime:` lines, byte-identical labels across 201039,
820236 and 613166:

```
chop  2026-01-01 .. 2026-01-07   (6 cycles)
bull  2026-01-08 .. 2026-01-20   (9 cycles)   <-- gate OFF
chop  2026-01-21 .. 2026-01-23   (3 cycles)
bull  2026-01-26 .. 2026-02-04   (8 cycles)   <-- gate OFF
chop  2026-02-05 .. 2026-02-27  (17 cycles)
```

17 of 43 bars (39.5%) run with `entry_extension_block_pct = 0`.

### 2.2 Every entry of consequence lands on a gate-off bar

First BUY fill per name, mapped to its decision bar and that bar's **effective**
(one-bar-lagged) profile regime. P&L from the run's `pnl_per_stock` summary.

**bt 201039** (+$500.39 total)

| name | decision bar | profile | gate | entry px | P&L |
|---|---|---|---|---|---|
| BA, NTR, TCMD, VOYA, XOM | 2026-01-02 | chop | **ARMED** | — | +$482.83 |
| AAL | 2026-01-06 | chop | **ARMED** | $16.02 | −$10.94 |
| PLRZ | 2026-01-16 | bull | off | $15.48 | **−$154.46** |
| AVNT | 2026-01-20 | bull | off | $35.64 | +$9.77 |
| HL | 2026-01-27 | bull | off | $28.23 | **−$24.16** |
| WDC | 2026-01-30 | bull | off | $259.37 | +$58.17 |
| EGO | 2026-02-02 | bull | off | $38.87 | +$167.35 |
| SNDK | 2026-02-02 | bull | off | **$660.48** | **−$37.73** |

**Armed-bar entries: 6 names, +$471.88. Gate-off entries: 6 names, +$18.95.**

**bt 820236** (+$739.61): armed 5 names (BA, CORD, CPER, LRCX, WDC, all 01-02)
**+$687.22**; gate-off 2 names (OMER 01-09, SNDK 01-20) **+$39.96**.

**bt 613166** (+$549.91): armed 5 names (HESM/NTR/NVDA 01-02, AGMI 01-08, PLD 01-26)
**+$573.04**; gate-off 4 names (AMZN 01-28, PLRZ 01-28, EGO 02-02, SNDK 02-05)
**−$54.77**.

**Combined: 16 armed-bar entries = +$1,732.14. 12 gate-off entries = +$4.14.**

*Honest caveat.* This split is **confounded with time-in-market**: most armed-bar
entries are the 2026-01-02 opening basket. Removing the opening basket leaves armed
n=3 (AAL −$10.94, AGMI +$341.72, PLD +$105.29 = +$436.07) versus gate-off n=12
(+$4.14). Direction survives; n=3 is not evidence on its own. The *unconfounded*
evidence is in section 2.3.

### 2.3 The one-bar flip — HL, bt 201039

This is the cleanest single artefact in the investigation. The same name, the same
score, two consecutive decision bars, opposite outcomes, and the only thing that
changed is the profile.

```
201039:17465 [01-23, chop]  V32 mw_buy extension-block: HL recent runup +65.7% > 25% — no conviction bypass
201039:17466 [01-23]        Momentum watchlist: … top3=[('PLRZ',1.274),('HL',0.94),…], new_buys=['HL']
201039:17473 [01-23]        V32 mw_swap extension-block: HL recent runup +65.7% > 25% — no conviction bypass

201039:18387 [01-26, prev bar chop -> still armed]
                            V32 mw_buy extension-block: HL recent runup +68.6% > 25% — no conviction bypass
201039:18388 [01-26]        Momentum watchlist: … new_buys=['HL']
201039:18396 [01-26]        V32 mw_swap extension-block: HL recent runup +68.6% > 25% — no conviction bypass

201039:19292 [01-27, prev bar bull -> block_pct = 0]
                            Momentum watchlist: … new_buys=['HL']          <-- no extension line at all
201039:19332 [01-27]        Executable buys: HL
201039:19632 [01-27]        FILL BUY HL qty=4.61693063 price=28.234510 quote=2026-01-27 16:00:00
201039:22537 [01-30]        FILL SELL HL qty=4.61693063 price=23.002205 quote=2026-01-30 18:00:00
```

HL: **−$24.16, −18.5%**, on a stock that moved **+29.7%** over the window. The gate
declared HL over-extended on two consecutive bars and then, one bar later, was not
consulted at all.

### 2.4 PLRZ, bt 201039 — blocked five times low, bought high

```
201039:649   [01-01, $8.11 ]  V32 mw_buy extension-block: PLRZ recent runup +106.2% > 25%
201039:2664  [01-01, $8.11 ]  Entry extension gate: PLRZ recent runup +106.2% > 25% — buy blocked
201039:3523  [01-02, $8.11 ]  V32 mw_buy extension-block: PLRZ recent runup +106.2% > 25%
201039:4334  [01-05, $11.795] V32 mw_buy extension-block: PLRZ recent runup  +97.2% > 25%
201039:5243  [01-06, $13.195] V32 mw_buy extension-block: PLRZ recent runup  +78.8% > 25%
201039:7217  [01-08, $12.47 ] Entry extension gate: PLRZ recent runup  +83.6% > 25% — buy blocked
201039:13157 [01-16, BULL  ]  FILL BUY  PLRZ price=15.475358 quote=2026-01-16 16:00:00
201039:22507 [01-30        ]  FILL SELL PLRZ price=12.750734 quote=2026-01-30 17:00:00
```

Blocked at **$8.11**. Bought at **$15.48 — +90.8% higher**, and +24.1% above the last
block price. Result **−$154.46, −17.6%**, the largest single loss of the run, on a
stock that moved **+61.8%**. `new_buys=['PLRZ']` appears on 01-02, 01-05 and 01-06:
the ranker wanted it every one of those bars.

bt 613166 repeats it: PLRZ blocked in chop, bought `613166:18876` on 01-28 (bull) at
$14.68, sold 01-30 at $11.52, **−$18.47**.

### 2.5 SNDK — the same shape at a bigger scale

```
820236:267   [01-01, $237.33]  Entry extension gate: SNDK recent runup +28.5% > 25% — buy blocked
820236:3065  [01-02, $262.08]  Entry extension gate: SNDK recent runup +28.5% > 25% — buy blocked
820236:5737  [01-07, $335.90]  V32 mw_buy extension-block: SNDK recent runup +73.2% > 25%
820236:6625  [01-08, $333.19]  V32 mw_buy extension-block: SNDK recent runup +75.3% > 25%
820236:14062 [01-20, BULL   ]  FILL BUY SNDK price=443.834068 quote=2026-01-20 16:00:00   -> +$100.95
613166:24341 [02-05, prev bar bull] FILL BUY SNDK price=592.012625 quote=2026-02-05 16:00 -> +$3.04
201039:23469 [02-02, BULL   ]  FILL BUY SNDK price=660.479056 quote=2026-02-02 16:00:00   -> −$37.73
```

SNDK is **never once** evaluated by the extension gate in 201039 or 613166 (zero
extension lines for the symbol in either log). It reaches 201039's scored universe only
on 2026-01-30 (`201039:21995 Propagation scoring expansion: … SNDK …`) and is bought
three bars later on a bull bar at $660.48 — **+178% above the $237.33 price at which
820236's gate blocked it.**

The three runs bought the *same stock* at **$443.83 / $592.01 / $660.48** and earned
**+$100.95 / +$3.04 / −$37.73** on a **+166.1%** move. Entry price is the whole result;
the gate that exists to control entry price was consulted on none of the three entries.

### 2.6 Blocked-then-bought, all three runs

Only **5 of the 201 distinct symbol-blocks** ever became a position (201039: 68 distinct
symbols blocked, 2 bought; 820236: 71 blocked, 2 bought; 613166: 62 blocked, 1 bought).
All 5 were bought **after** the block, on a gate-off bar. **4 of 5 lost money.**

| run | name | blocks (reading) | bought | P&L |
|---|---|---|---|---|
| 201039 | PLRZ | 5x, 106.2 -> 83.6 | 01-16 $15.48 | **−$154.46** |
| 201039 | HL | 2x, 65.7 / 68.6 | 01-27 $28.23 | **−$24.16** |
| 820236 | SNDK | 4x, 28.5 -> 75.3 | 01-20 $443.83 | +$100.95 |
| 820236 | OMER | 9 fires / 6 bars, 96.0 flat | 01-09 $13.03 | **−$60.99** |
| 613166 | PLRZ | 1x | 01-28 $14.68 | **−$18.47** |
| | | | **net** | **−$157.13** |

The gate is not preventing bad entries. It is **delaying** them until the price is worse.

---

## 3. Mechanism B — the metric decays and is direction-blind

### 3.1 It decays: SNDK reads "not extended" at maximum extension

SNDK's decision-bar closes are fully recoverable from bt 820236 (42 daily bars,
`BROKER SNDK @ <date> ($px)` lines; the one overlapping bar in 201039, 2026-02-02
$617.375, matches exactly, so the series is the same price truth for both runs).

Rolling `(max−min)/min` over the last 20 daily closes — the gate's own formula:

| date | close | gate metric (20 daily bars) | 3-session range | vs 2026-01-01 |
|---|---|---|---|---|
| 2026-01-06 | $328.19 | **38.3%** BLOCK | 25.2% | +38.3% |
| 2026-01-20 | $446.96 | 88.3% | 10.2% | +88.3% |
| 2026-01-30 | $620.08 | **129.2% (peak)** | 22.1% | +161.3% |
| 2026-02-02 | $617.38 | 88.9% | 16.2% | +160.1% |
| 2026-02-16 | $626.79 | 46.6% | 10.4% | +164.1% |
| 2026-02-20 | $638.40 | 34.5% | 7.2% | +169.0% |
| 2026-02-23 | $683.19 (window high) | 37.6% | 12.6% | +187.9% |
| 2026-02-25 | $645.43 | 28.1% | 5.9% | +172.0% |
| **2026-02-26** | **$636.61** | **22.4% — PASSES** | 4.4% | **+168.2%** |
| **2026-02-27** | **$641.26** | **22.4% — PASSES** | 1.4% | **+170.2%** |

The gate metric **peaks at +129.2% on 01-30 and falls to +22.4% by 02-26** — below the
25% block threshold — while the stock is **+168% above its window base and 6.8% below
its all-time high**. On 2026-02-26 and 2026-02-27, the two most extended sessions of the
entire window in any economic sense, the gate would have waved SNDK through.

That is not a tuning accident, it is arithmetic: a trailing range has memory of exactly
`lookback_bars`. **`entry_extension_lookback_bars = 20` is literally the number of
sessions after which a parabola is forgotten.** A name that spikes and then
consolidates is guaranteed to read clean, and consolidation-after-spike is the normal
shape of the names this strategy exists to buy.

If the read instead lands on the hourly branch (~3 sessions, §1.4), it is worse still:
**SNDK exceeded 25% on 37 of 42 bars on the 20-daily window but on only 1 of 42 bars on
the 3-session window.** On the short branch the gate is effectively never armed for a
trending name, at any price.

### 3.2 It is direction-blind: OMER, bt 820236

OMER's decision-bar closes and the gate's reading, same bars:

| bar | OMER close | gate reading |
|---|---|---|
| 2026-01-01 | $17.18 | +96.0% |
| 2026-01-02 | $16.93 | +96.0% |
| 2026-01-05 | $15.93 | +96.0% |
| 2026-01-06 | $14.67 | +96.0% |
| 2026-01-08 | $15.53 | +96.0% |

(9 fire lines across those 6 decision bars, every one reading exactly `+96.0%`.)

The close fell from **$17.18 to $15.53 (−9.6%)** across the block bars, touching
**$13.97 on 2026-01-07 (−18.7% from the first block)**, and the "recent runup" reading
did not move one decimal place: the window's old high pinned `hi`, and `(hi−lo)/lo`
cannot tell a downtrend from an uptrend. OMER was then bought on 2026-01-09 at $13.03
(`820236:7873`) — after the decline the gate had been calling a "runup" — for
**−$60.99** on a stock that finished **−29.8%**.

The gate blocked a falling knife for being over-extended, then admitted it once it had
fallen further.

### 3.3 It moves the wrong way while a name trends: PLRZ, bt 201039

| bar | PLRZ close | gate reading |
|---|---|---|
| 2026-01-01 | $8.11 | +106.2% |
| 2026-01-02 | $8.11 | +106.2% |
| 2026-01-05 | $11.795 | +97.2% |
| 2026-01-06 | $13.195 | **+78.8%** |

Price **+62.7%**, reading **−27.4 percentage points**. As the old low rolls out of the
window the denominator rises and the measured "runup" *shrinks* — so the longer and
harder a name trends, the *less* extended the gate believes it to be. The reading is
**not monotone in extension**; it is roughly monotone in *how recently the base was in
the window*.

---

## 4. Why this produces the central fact

The parent's table — big movers (mean +58.1%) captured +2.1%, modest movers (mean
+7.0%) captured +7.9% — falls straight out of the two mechanisms:

* **Modest movers** (XOM +26.8% -> +26.9%, NTR +21.6% -> +21.2%) were all bought in the
  2026-01-02 opening basket, on a gate-armed chop bar, at the window's starting prices.
  Nothing about them was extended, the gate never touched them, and we hold the whole
  move.
* **Big movers** make their vertical legs during bull stretches. Bull is precisely when
  `entry_extension_block_pct = 0`, so the only entry-price discipline in the system is
  absent for the entire move. Our entries land wherever the ranker happens to surface
  the name: SNDK $660.48 after $237 -> $660; PLRZ $15.48 after $8.11; HL $28.23 one bar
  after being called +68.6% extended.
* When the gate *is* armed (chop), the names it blocks are blocked at their *lowest*
  prices, because chop is early and because the range metric reads highest right after
  the first leg (§3.3). It then forgets them (68/71/62 distinct names blocked, 2/2/1
  ever bought), and the two it does not forget it re-admits later and higher.

Net: the gate systematically converts "buy the leader early" into "buy the leader late,
or not at all". That is an inversion of its stated purpose
(`gna:23217-23223`: *"Blocks NEW entries whose recent close range ran up more than the
threshold"*).

---

## 5. What this does NOT explain, and the counterfactual risk

**Do not read this as "arm the gate in bull".** With the *current* metric that is
measurably destructive:

* **WDC**, the best name in bt 820236 (+$450.49 = 61% of that run's P&L, +61.9% move),
  had a 20-daily-bar range of **51.4% on 2026-01-30**, the bar bt 201039 bought it
  ($259.37, +$58.17). Arming the same range metric on bull bars **blocks WDC**.
* The same metric would have blocked SNDK's profitable 820236 entry (range 88.3% on
  01-20, +$100.95).
* `docs/OBJECTIVE.txt` already records the paired measurement: *"Loosening the
  entry-extension gate: blocked basket returned −7.95%."* The blocked basket is not
  systematically good **or** systematically bad, because the metric does not measure
  extension. Both loosening and tightening it are coin flips.

Also not explained here, and out of scope: **why SNDK reached 201039's scored universe
only on 2026-01-30** (via `Propagation scoring expansion`) when 820236 had it on bar 1.
That is a discovery-path difference, not a gate difference, and it is the second half
of the SNDK story. It is consistent with the `_SYNTHESIS.md` findings #3/#4 (poisoned
overlay cache, `max(20d,60d)` sort key) and should be chased there.

---

## 6. Ranked recommendations

Ordered by (evidence x dollars) / risk. Each states its mechanism and which runs it is
evidenced on. **All of these are structural, not window-specific**, except where noted.

---

### R1. Replace the metric: measure *position in the move*, not *range of the move*

**Change.** Retire `(max−min)/min` over N bars as the entry-extension signal. Use a
directional, base-anchored measure that is monotone in extension. Two candidates already
exist in the codebase and need no new data:

* `_recent_return_pct` (`gna:9284-9295`) — signed close-over-close return over N bars.
* the near-ATH distance already computed by the T1-c gate
  (`portfolio_swap_ath_gate_max_pct`, `gna:5573-5580`).

A defensible composite: block when the entry price is **both** far above a *long*
anchor (e.g. 60-session-ago close, or a 50-session MA) **and** in the top X% of its
52-week range. Keep 25%-class thresholds for tuning later; ship the *metric* change
first, default-OFF behind a new key, and A/B it.

**Expected effect.** Removes three failure modes at once: the decay (§3.1), the
direction-blindness (§3.2), and the anti-monotonicity (§3.3). It would have read SNDK as
extended on 2026-02-26 (+168% vs base) instead of "22.4%, passes", and would *not* have
read OMER as extended while it fell 18.7%.

**Evidence.** §3.1 (SNDK 42-bar reconstruction, bt 820236 prices), §3.2 (OMER, bt
820236, 6 bars at a pinned 96.0% through a −18.7% decline), §3.3 (PLRZ, bt 201039,
+62.7% price / −27.4pp reading). Code: `gna:9278-9281`.

**Risk.** Medium. Any change to the metric changes which names are admitted. Must be
run paired with its own `history_scope_salt` on >= 3 windows including a
non-semiconductor-led one, per `docs/OBJECTIVE.txt`.

---

### R2. Make the gate's arming state independent of the index regime

**Change.** Remove `entry_extension_block_pct: 0` from `regime_profiles.bull` and
`regime_profiles.recovery`. Extension is a property of the *name*, not of QQQ/SPY. If a
bull genuinely deserves more tolerance, express it as a *wider threshold* (e.g. 40%
under R1's metric), never as `0`.

**Do this only together with R1.** On its own, with the current metric, it blocks WDC
(§5) and is a likely regression.

**Expected effect.** Restores entry-price discipline to the 17 of 43 bars (39.5%) on
which it is currently absent — the bars on which 12 of 28 entries across the three runs
were made for a combined **+$4.14**.

**Evidence.** §0 table (0 of 393 fires under a bull profile), §2.1-2.3 (identical regime
path across three runs; the HL one-bar flip at `201039:18387` -> `201039:19292`).
Config: `scripts/doc193_backup_*.json` `regime_profiles.bull.entry_extension_block_pct`.
Code: `broker.py:5651-5683`, `broker.py:5899-5926`.

**Risk.** High if shipped alone. Low-to-medium after R1. Default-OFF, per-document.

---

### R3. Turn a block into a *deferral* with an explicit re-entry trigger

**Change.** Today a block does `sc["score"] = 0; sc["action_intent"] = "hold"`
(`gna:23240-23241`) and the name is dropped;
`momentum_watchlist_track_extension_blocked` only keeps it *priced*, not *pending*.
Replace with a pending record carrying the block price, plus a trigger: buy on the first
close back within X% of a **rising** N-session MA, valid for K sessions.

**Expected effect.** The gate currently forfeits the names it blocks: 68/71/62 distinct
symbols blocked per run, of which **2/2/1** were ever bought — and every one of those
was bought later at a worse price (PLRZ $8.11 -> $15.48; SNDK $237.33 -> $443.83; HL
blocked twice, bought the next bar). A deferral converts "blocked at the low, bought at
the high" into "blocked at the high, bought at the pullback", which is the behaviour the
gate's own comment claims (`gna:23248-23251`: *"buyable once it is no longer
extended"*).

**Evidence.** §2.4, §2.6. Code: `gna:23239-23289`.

**Risk.** Medium — it adds buying, which interacts with the cash race and the
`max_positions` cap already identified in `_SYNTHESIS.md` #1/#2. Sequence it after
those.

---

### R4. Log the gate's inputs (zero-risk, do first)

**Change.** Every block/pass-thru line should carry the inputs, not just the output:
`bars_used`, `source=broker|overlay`, `first_bar`, `last_bar`, `hi`, `lo`, `price`,
`threshold`.

**Expected effect.** Right now the reading **cannot be reproduced from the log**. I could
not determine which branch of `_resolve_asof_bars` (§1.4) each read took, and therefore
could not prove whether the effective lookback was 3 sessions or 20. Every future
investigation of this gate pays that cost again. One log-string change removes it.

**Evidence.** §1.4: SNDK's +73.2% on 01-07 is inconsistent with its in-window daily
range (+41.5%) and with a 3-session range (+24.2%); PLRZ's +78.8% on 01-06 exceeds its
entire in-window range (+62.7%).

**Risk.** None. Log-only.

---

### R5. Normalise the lookback to sessions, not to "bars"

**Change.** `_scale_bars` (`gna:257-286`) corrects the bar *count* for the run cadence
but not for the bar *shape* of whichever source `_resolve_asof_bars` lands on. Express
`entry_extension_lookback_bars` as **sessions** and resample/derive the window from
daily closes regardless of branch (or refuse to evaluate when only intraday bars are
available, i.e. set `entry_extension_require_bars` semantics on the *daily* source).

**Expected effect.** Removes a 6.5x silent variation in the gate's horizon that depends
on whether the broker happened to have fetched hourly bars for the symbol. On the short
branch the gate is close to inert for a trending name: SNDK exceeded 25% on **37 of 42**
bars on a 20-session window but only **1 of 42** on a 3-session window.

**Evidence.** §1.4 (code), §3.1 (the 37/42 vs 1/42 count, bt 820236 prices).

**Risk.** Low-medium. Changes the gate's sensitivity; ship with R1 and measure together.

---

### R6. Do NOT retune `entry_extension_block_pct` on its own

**Non-change, stated explicitly.** The median blocked reading is 46.0 / 55.9 / 51.4
(201039 / 820236 / 613166) with p25 = 33.4 / 39.0 / 34.4 — the block population is not
clustered at the margin, so moving 25% to 20% or 35% reshuffles which names are lost
without touching either defect. `docs/OBJECTIVE.txt` already records the paired
measurement that loosening it produced a −7.95% blocked basket. Threshold tuning is
fitting to a window; R1 changes the mechanism.

---

## Appendix — reproduction

```bash
# all extension-gate decisions, any run
python3 scripts/pull_backtest_logs.py 201039 --filter 'extension gate:|extension-block:' --stdout
python3 scripts/pull_backtest_logs.py 820236 --filter 'SNDK recent runup|FILL BUY SNDK' --stdout
python3 scripts/pull_backtest_logs.py 613166 --filter 'extension gate:|extension-block:' --stdout

# the regime path that arms/disarms the gate
python3 scripts/pull_backtest_logs.py 201039 --filter 'V31 market regime' --stdout

# the price series used for every reconstruction above
python3 scripts/pull_backtest_logs.py 820236 --filter 'BROKER\] SNDK @ 2026' --stdout
python3 scripts/pull_backtest_logs.py 820236 --filter 'BROKER\] OMER @ 2026' --stdout
python3 scripts/pull_backtest_logs.py 201039 --filter 'BROKER\] PLRZ @ 2026' --stdout
```

Attribution of the gate fire to a profile state uses the **previous** `V31 market
regime:` line, not the one on the same bar (`broker.py:5890-5891`, one-bar lag). Using
the same-bar label, 33 of 393 fires appear to occur under `bull`; using the previous
bar's label, zero do.
