# 584712 — the fresh-low gate WORKS, and the +21.9% counterfactual was WRONG

Run: bt **584712**, OOS bull `2026-03-30..2026-04-27`, v2-let-run-core, $6,000, 3600s, cold state,
with `residual_sleeve_bear_block_at_fresh_low_bars=2` + `regime_rally_onset_enabled=true`.
Control: bt **337615**, same window/config, gate OFF.

---

## 1. THE MECHANISM: VERIFIED, PRE-DECLARED, GREPPED

The signature written down in `2026-08-10-next-run-plan.md` **before** the run, found verbatim in it:

```
[sleeve] bear leg SKIPPED — proxy at a fresh 20d low (since_20d_low=0 < 2, off_low=0.0%)
[sleeve] bear leg SKIPPED — proxy at a fresh 20d low (since_20d_low=1 < 2, off_low=3.37%)
[sleeve] bear leg SKIPPED — rally onset (short MA reclaimed off a fresh 20d low)
```

12 fresh-low skips + 4 rally-onset skips. And the new diagnostic is on every regime line:

```
V31 market regime: bear (raw=bear, proxy=QQQ, closes=155, ret20=-4.08, ret5=-1.17,
                         since_20d_low=1, off_20d_low=3.37)
```

**The offline replay predicted this bar-for-bar.** `fresh-low-verification.md` §2 said the
decision-time values would be `since_low = 0, 0, 1, 2` and that **N=1 would leak the third bar**;
the run's own numbers are `since_20d_low=0, 0, 1` then `rally_onset` on the fourth. The bar that
N=1 would have leaked is the one the log shows being caught by `since_20d_low=1 < 2`.

**Result: `SQQQ` does not appear in `tickers` or `pnl_per_stock` at all.** Zero notional, against
$2,100 parked and **-$513.95** in the control.

---

## 2. THE P&L: THE COUNTERFACTUAL WAS WRONG

| | 337615 (gate OFF) | 584712 (gate ON) | delta |
|---|---|---|---|
| return | +13.35% | **+12.34%** | **-1.01pp** |
| vs SPY (+13.10%) | +0.25pp | -0.76pp | — |
| **max drawdown** | 11.4% | **5.8%** | **-5.6pp** |
| SQQQ P&L | **-$513.95** | **none traded** | +$514 |
| core lane gross | 2.54x NAV | **0.21x NAV** | -2.33x |
| round trips | 3 (33% win) | 5 (40% win) | — |

`gap-oos.md` and this session's own plan both predicted "kill the SQQQ leg and the run is ~+21.9%".
**It is not.** Removing a deterministic -$514 loss moved the return **down 1.01pp**.

Why: the freed $2,100 bought different names, and the book then diverged completely.

```
337615 book:  AEHR +482, AXTI +476, AAOI +248, NET -134, ETH +94, BKR +74, GS +28, CTVA +6
584712 book:  AXTI +208, AAOI +172, RRX +132, BTC +128, MSFT +109, NVDA +108, NVTS +71,
              RIVN -58, LWLG -96, SOC -138
```

**AEHR (+$482 in the control) is not in the treatment book at all.** Two names overlap out of
eleven. This is the repo's own measured noise floor (>=4.94pp, 0/18 name overlap on repeat runs)
doing exactly what it is documented to do, and -1.01pp is a fifth of it.

**Honest verdict: the return difference is not attributable to the gate, in either direction.**

---

## 3. WHAT *IS* ATTRIBUTABLE

Two things are deterministic rather than name-selection-dependent, and both moved a lot:

1. **Max drawdown 11.4% -> 5.8%.** The control's trough was driven by a 3x inverse bought at 96.8%
   of its range and stopped out -10% two days later. That is a mechanical loss, not a draw.
2. **Core lane gross 2.54x NAV -> 0.21x NAV.** With no hedge to fund, the core is not sold down
   and bought back; the SPY saw-tooth nearly vanishes (9 fills -> 5, $15,259 -> $1,287 of gross).

On the objective's own terms — "beat SPY in every regime" — **the OOS window is a tie in both
arms** (+13.35% and +12.34% against +13.10%). The gate does not fix that. What it removes is a
deterministic loss and half the drawdown.

---

## 4. WHAT THIS CHANGES ABOUT THE PLAN

* **Keep the gate on.** It does what it was built to do, it is cheap, it halves the drawdown, and
  it removes a loss that was 90.5% of one window's profit and 64.2% of another's.
* **Stop quoting "+21.9%".** Delete that counterfactual from the working set. Every "remove the
  loser and add back its loss" estimate in this repo is subject to the same rebuttal: the capital
  gets redeployed and the book re-randomizes.
* **The OOS window is still not a pass.** Neither arm beats SPY. The next lever has to change
  what the book *holds* or *how big* it holds it, not what it avoids — which is why
  `bfq_conviction_target_weight_pct` (see `sndk-100-dollars.md`) is next: it changes the SIZE of a
  name the run already picks, so it is not a coin flip on selection.
* **Still owed: the bear window with the gate ON.** SQQQ was +$965 = 124% of that window's profit
  and its open was at `since_20d_low = 18`, sixteen bars clear of the threshold. The offline
  prediction is that the gate is inert there. That prediction must be checked against the run —
  this session has now twice shown offline predictions to be right about mechanism and wrong
  about P&L.
