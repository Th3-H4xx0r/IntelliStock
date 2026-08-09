# ext-still-blocking — bt 427197: is the extension gate still the binding refusal?

Read-only, 2026-08-09. No code edited, no run started/stopped, nothing pushed.
Builds on `extension-gate-inversion.md` (metric = direction-blind RANGE), `gap-bugsweep.md`
(`entry_extension_metric` reverted to `range` by a2609bd), `gap-capital.md` / `gap-bull.md`
(`core_min_pct` is the capital binder). None of that is redone here.

Logs on disk: `backtests/427197.log` (32,163 lines, run STOPPED at 2026-02-13),
`backtests/915207.log` (41,184), `backtests/383778.log` (19,643), `backtests/542754_sweep.log` (18,265).
Code: `backend/strategies/graph_nexus_analysis.py` (gna).

---

## 0. The parent's question first: `bars=97` is NOT the lookback

**There is no hourly-scaling bug. `bars=97` is a logging artefact.**

`_extension_blocks_entry` returns an EMPTY diagnostics dict on the `range` path:

    gna:9303-9307   metric = str(cfg.get("entry_extension_metric", "range") ...)
                    if metric != "anchor":
                        blocked, reading = _recent_runup_protect(...)
                        return blocked, reading, "range", {}          <-- diag = {}

so the log line falls back to the SOURCE length, not the window length:

    gna:5558        f"[bars={_mext_diag.get('bars_used', len(_mext_bars))}"

`bars=NN` therefore prints `len(_mext_bars)` = every bar `_resolve_asof_bars` handed over.
The window actually used is `[-max(2, lookback):]` (gna:9274) with
`lookback = _scale_bars(20, config)` (gna:5538); `_scale_bars` is a pass-through when the run
cadence equals the 3600s baseline (gna:280-281). **The applied lookback is 20 bars.**

**The bars are DAILY, one per trading session.** Proof — SNDK's own `bars=` counter in 427197
increments by exactly 1 per SESSION, and skips 2026-01-19 (MLK, market closed):

    01-07 bars=97 | 01-08 =98 | 01-22 =107 | 01-23 =108 | 01-26 =109
    02-06 =118 | 02-09 =119 | 02-10 =120 | 02-11 =121 | 02-12 =122 | 02-13 =123

97 -> 107 across 01-07..01-22 is 10 increments over 11 calendar sessions: exactly the
01-19 holiday. Replicated in bt 383778 (AAOI `bars=153` on 03-30 -> `bars=157` on 04-03,
+4 over 4 sessions) and bt 542754 (`bars=133..153`, +1/session).

Why daily and not the hourly broker bars: SNDK is a discovery candidate, not a held/base
symbol, so it is absent from `symbols_for_data` and `price_history` has no SNDK key.
`_resolve_asof_bars` (gna:9410-9423) then falls through to
`strategy_cache["_overlay_bars_raw"]`, which is the DAILY overlay, filtered to `<= date_key`.
The broker's own hourly fetch is visible and irrelevant to this call:
`427197:2417 [BROKER] Backtest symbol expansion: loaded 733 1Hour bars for SNDK`
(733/7 = 105 sessions — if the gate were reading those, `bars` would grow ~7 per session).

**Consequence: the gate measures the trailing-20-SESSION close range — about one calendar
month. That is precisely the horizon over which these names make the move the strategy
exists to buy.** `momentum_rank_on_60d` sorts the watchlist toward large recent range and
the gate refuses on large recent range: same input, opposite sign, same bar.

**So "fix the lookback" is NOT the fix. Do not spend a run on it.**

---

## 1. Yes — it is STILL the binding refusal for SNDK in bt 427197, and it binds on the
##    cheap bars while capital binds on the expensive ones

SNDK is in `new_buys` on **28 of the 28 trading decision bars** from 01-07 to the 02-13 stop
(`Momentum watchlist: ... new_buys=['SNDK', ...]`), and is the **#1-ranked name on 26 of
them**. It is never bought. The refusals partition PERFECTLY, with **zero overlap**:

| bars | terminal refusal | log signature |
|---|---|---|
| **11** (01-07, 01-08, 01-22, 01-23, 01-26, 02-06, 02-09, 02-10, 02-11, 02-12, 02-13) | **extension gate** | `V32 mw_buy extension-block: SNDK range +NN% > 25%` and NOTHING else |
| **17** (01-09..01-21, 01-27..02-05) | capital | `Deferred unfunded buys demoted to hold: ... SNDK` + `V28 ROTATION SKIP: SNDK raw=1.700 < min_score=99.000` + `Backfill queue BLOCKED: SNDK (full_priority_blocked, score=1.700)` |

On the 11 gate bars there is **no** `deferred`, **no** `ROTATION SKIP`, **no** `Backfill
queue BLOCKED` line for SNDK at all — the name leaves the pipeline at
`gna:5562 return True, f"extension_runup_{...}pct"`, upstream of every capital gate.

The split is the regime profile, with the documented one-bar lag
(`entry_extension_block_pct: 0` in `regime_profiles.bull`). 427197's `V31 market regime`
path is chop 01-01..01-07, bull 01-08..01-20, chop 01-21..01-23, bull 01-26..02-04,
chop 02-05..02-13. Lagging by one bar reproduces the 11 block bars **11/11 exactly**.

**This is the finding that matters:** the gate is armed on chop bars, and chop bars are the
CASH-RICH bars; it is disarmed on bull bars, which are the bars where the book is already
full. The two binders tile the window. **`core_min_pct` alone cannot buy SNDK cheap** — the
two cheapest bars in the whole run, 01-07 `$335.90` and 01-08 `$333.19`, are gate bars.
The first bar capital could even see SNDK is 01-09 at `$363.01`.

### The gate fires with cash on the table — two runs, bar 1

**bt 427197, 2026-01-01, $6,000 cash, nothing held:**

    427197:421  V32 mw_buy extension-block: KYTX range +56.5% > 25% — no conviction bypass [bars=90]
    427197:422  V32 mw_buy extension-block: MU   range +30.4% > 25% — no conviction bypass [bars=94]
    427197:423  Momentum watchlist: ... top3=[('KYTX',0.423),('MU',0.307),...] new_buys=['KYTX','MU']
    427197:442  V31.2 total-spend cap [CONCENTRATE]: funded 4 of 7 by conviction (SBLK@$840, CPER@$840, SLV@$840, TDY@$840)
    427197:1663 Buy gate inputs for CPER: cash=$6000.00 ... available=$5880.00 cash_to_use=$840.00 → PASS
    427197:2196 V32 mw_buy extension-block: LITE range +30.8% > 25% [bars=94]
    427197:2197 V32 mw_buy extension-block: RKLB range +73.4% > 25% [bars=94]

Both intended momentum buys were refused on each cycle while `$5,880` sat available; the
$3,360 went to SLV/CPER/SBLK/TDY. Outcome to the 02-13 stop, from the run's own panel
prices: blocked LITE $368.585 -> $542.99 (**+47.3%**), MU $285.47 -> $397.87 (**+39.4%**),
RKLB -4.2%, KYTX -21.3%; bought SBLK +21.7%, TDY +27.9%, SLV +4.5%, CPER +1.7%.

**bt 383778, 2026-03-30, bar 1 — the clean, unconfounded case (different window, different
universe, non-January, OOS):**

    383778:371  V32 mw_buy extension-block: AAOI range +47.2% > 25% — no conviction bypass [bars=153]
    383778:373  Momentum watchlist: ... top3=[('AAOI',1.001),('VG',0.988),...] new_buys=['AAOI','VG']
    383778:381  V32 mw_breakout_add extension-block: AAOI range +47.2% > 25% [bars=153]
    383778:1589 Buy gate inputs for ETH: cash=$6000.00 reserved=$0.00 ... available=$5880.00 → PASS
    383778:1593 AAOI @ 2026-03-30 14:00:00 ($86.07)
    383778:4461 [04-03] V32 mw_buy extension-block: AAOI range +50.1% > 25% [bars=157]   ($107.00)
    383778:7239 [04-08] FILL BUY AAOI qty=5.90590443 price=120.184597
    383778:17378 [04-23] FILL SELL AAOI qty=5.90590443 price=145.216694

AAOI was the run's **#1-ranked name on bar 1**, blocked at **$86.07** with **$5,880
available**, and bought 6 sessions later at **$120.18 — +39.6% higher**. Booked
**+$147.79**. The same $709.72 of notional at $86.07 is 8.2459 sh -> $1,197.44 at the
04-23 exit = **+$487.72**. **Delta +$339.93 = +5.7pp of NAV, and 119% of that run's entire
+$284.96 profit, from one gate line.**

---

## 2. How many target names it refuses — counts across 427197 / 915207 / 383778

Parsed every `extension-block` / `Entry extension gate` line (99 / 142 / 37 = **278**).

| | 427197 | 915207 | 383778 | total |
|---|---|---|---|---|
| block lines | 99 | 142 | 37 | 278 |
| distinct symbols blocked | 49 | 74 | 21 | 144 |
| of those, ever bought in-run | **0** | 1 (SNDK, at $443.83) | 1 (AAOI, +39.6% higher) | **2 of 144** |
| decision bars (first watchlist cycle each) where the run's own **#1-ranked** name was blocked | **15/32** | **16/42** | **5/21** | **36/95 = 37.9%** |
| `mw_buy`-lane blocks / intended watchlist buys (2/bar) | 32/66 = **48.5%** | 35/86 = **40.7%** | 10/42 = **23.8%** | 77/194 = **39.7%** |

**~40% of every intended momentum-watchlist buy, on three different windows and universes,
is refused by this one gate, and 142 of 144 blocked names are never bought at any price.**
Base rate for comparison: the `quality_filter` lane blocks 67 of 7,044 scored names in
427197 (0.95%) — the refusal is ~40-50x enriched on exactly the names the ranker puts first.

Blocked names are re-queued and re-refused every bar, not dropped once:
`427197:2083 Momentum watchlist: added 24 ticker(s) ... extension_blocked=2`.

---

## 3. The reading carries no information about forward return

For every block event where the symbol also has `[BROKER] SYM @ date ($px)` panel prices,
I took the price on the block bar and the last panel price in the run (n=**133** events
across the three runs; 133/278 = the events with recoverable prices — that is the honest
sample, not all of them).

* Spearman(reading, forward return) = **-0.115, p=0.188** — not significant.
* Pearson = -0.239, p=0.006, but the buckets are **not monotone**, which is what a real
  extension signal would require:

| reading | n | mean fwd | median fwd |
|---|---|---|---|
| 25-40% | 24 | **+26.7%** | +20.2% |
| 40-60% | 22 | -4.8% | -18.3% |
| 60-100% | 65 | +12.8% | +5.2% |
| >100% | 22 | +0.6% | -10.3% |

Mean forward return of the whole blocked basket: **+6.9% (427197) / +13.4% (915207) /
+8.9% (383778)**; medians -0.7% / +5.2% / 0.0%. Stated plainly: the -7.95% blocked basket
recorded in `OBJECTIVE.txt` does **not** replicate on these three windows — but neither is
the basket systematically good. **The reading is noise.**

The in-run anti-monotonicity is visible on SNDK in 427197 alone: reading **+112.0% on
01-26** and **+70.0% on 02-13** while the price went **$486.405 (01-22) -> $590.73 (02-13),
+21.4%**. Price up 21%, "extension" down 42pp — the base rolled out of the 20-session
window. Same defect the prior doc proved on 820236; it is unchanged in the current build.

---

## 4. The smallest generalizable change — and the honest limit on it

**Change: one document key. `entry_extension_metric: "range" -> "anchor"`.**
No code edit. The `anchor` path already exists and is tested
(`_extension_above_anchor`, gna:9323-9376); a2054c6 shipped it, a2609bd set the document
back to `"range"` (`gap-bugsweep.md` flag #2). Flipping it also switches the log line onto
the populated-diag branch (gna:5559-5560), so every future block prints
`bars_used / anchor / price` — which is the R4 "log the inputs" fix for free.

**Mechanism (generalizes; it is one code path, exercised on all three runs).** `anchor`
measures distance above the PRIOR 20-session high instead of `(max-min)/min`. It is signed,
monotone in price, and does not decay: a name grinding to new highs reads ~0% by
construction, a name that GAPPED away from its base reads large. That removes all three
measured defects (decay, direction-blindness, anti-monotonicity) at once.

**On the same three runs it would have admitted the names that mattered.** SNDK, 01-07:
close $335.90 vs the prior session's $328.19 -> anchor ~+2.3%, PASSES (range said +73.2%).
AAOI, 03-30: reads at/below its prior high, PASSES (range said +47.2%).

**The honest limit, stated plainly.** I computed the anchor reading for the 46 block events
with >=5 prior panel bars: **max = +16.4%, median = -1.4%, and 0 of 46 exceed the current
25% threshold.** At `entry_extension_block_pct = 25` the metric flip is therefore
*behaviourally equivalent to disarming the gate on this evidence*, which is what
`OBJECTIVE.txt` lists as DO-NOT-RETRY. To keep a real gate the threshold must be
re-expressed on the anchor scale (order +10%), and **I have no in-sample support for any
particular value** — the 10-25% anchor band has n=1 in my sample. So:

* the **metric** change is generalizable (mechanism + 3 windows + same code path): SHIP IT.
* the **threshold** is NOT evidenced by anything I measured: it must be A/B'd, default-OFF,
  paired, own `history_scope_salt`, >=3 windows incl. 383778 (OOS) and 542754 (bear, where
  the gate fires 139x and the SQQQ leg is 124% of the profit — do not disturb it blind).

**Do NOT ship it alone.** Section 1 proves the binders tile the window by regime: fixing
the gate opens the 11 chop bars, fixing `core_min_pct` opens the 17 bull bars, and neither
alone converts a single SNDK entry that the other does not then refuse. The gate is the one
that binds at the CHEAP prices ($335.90/$333.19 vs $363.01+), and at bar 1 where 100% of the
cash is. **Pair `entry_extension_metric: anchor` with the `core_min_pct` reduction already
recommended in `gap-bugsweep.md` / `gap-capital.md`, in the same arm.**

**What NOT to do:** do not change `entry_extension_lookback_bars` (§0 — there is no
lookback bug), do not move `entry_extension_block_pct` on the `range` metric (§3 — the
reading is noise, so any threshold is a coin flip and OBJECTIVE already paid for that
lesson), and do not re-arm the current `range` metric in bull (it blocks WDC/AAOI-class
winners, measured in `extension-gate-inversion.md` §5).

---

## Appendix — reproduction

```bash
python3 scripts/pull_backtest_logs.py 427197 --filter 'extension-block:|extension gate:' --stdout
python3 scripts/pull_backtest_logs.py 427197 --filter 'SNDK' --stdout          # 178 lines
python3 scripts/pull_backtest_logs.py 383778 --filter 'AAOI' --stdout
python3 scripts/pull_backtest_logs.py 427197 --filter 'V31 market regime' --stdout
python3 scripts/pull_backtest_logs.py 427197 --filter 'BROKER\] (LITE|MU|RKLB|KYTX) @ 2026' --stdout
```
Bar-date attribution uses the preceding `Run once | ... | date=` line. Regime attribution
uses the PREVIOUS bar's `V31 market regime:` label (one-bar lag, broker.py:5890-5891).
