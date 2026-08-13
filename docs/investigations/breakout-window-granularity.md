# The breakout detector's windows assume daily bars; the backtest feeds hourly

Date: 2026-08-13. Code: `_compute_breakout_score_boost`. Runs: bt 873929 / 180796, 3600s
granularity. No credits spent.

## The mismatch

The boost has four components, and the comments name their intended windows:

| component | comment | code | boost |
|---|---|---|---:|
| 52-week breakout | "within 1% of 252-bar high" | `closes[-252:]` | +0.5 |
| 5-week breakout | "5-week (25-bar) breakout" | `closes[-25:]` | +0.3 |
| volume surge | "avg of last 20 days" | `volumes[-21:-1]` | +0.4 |
| gap-up | "today's open > yesterday's close x 1.05" | `opens[-1]` vs `closes[-2]` | +0.3 |

Those windows are correct for **daily** bars. The reference backtest runs at **3600s**, and the log
confirms the feed: `Backtest symbol expansion: loaded 642 1Hour bars for NVTS`. At hourly
granularity, with ~7 trading hours a day:

* `closes[-25:]` is about **3.5 trading days**, not 5 weeks.
* `closes[-252:]` is about **36 trading days**, not 52 weeks.
* the "20-day" volume average is **20 hours**.
* "today's open vs yesterday's close" is one **hourly** bar against the previous hourly bar, so the
  5% gap test is applied to an intraday step rather than an overnight gap.

## Why it matters

`buy_threshold` is 0.3 in doc 193, and an unpropagated name has `_existing_raw = 0.0`, so the
promotion needs `boost >= 0.3` — a single 5-week-high hit clears it exactly. The mechanism is
therefore *supposed* to fire readily for a name making a sustained move. In bt 180796 only 11 of
18,177 lines mention breakout at all, and 84 of 103 names that moved >=30% ended the window at a
flat vote.

The granularity mismatch does not by itself prove the boost is failing — a 25-hour high is a real
signal, just a much noisier and more frequent one than a 25-day high. It does mean the tuned
constants (`breakout_52w_boost`, `breakout_gap_up_pct=5.0`, `breakout_volume_surge_mult=2.0`) were
chosen for a different bar size than the one the reference window actually runs, so their
calibration cannot be assumed.

## What is still unknown

Whether the boost computes and lands under 0.3, or never computes because the symbol is absent
from `price_history` at scoring time. The named skip reasons shipped alongside this note
distinguish those two cases (`skip:bars=0<25` versus a computed sub-threshold boost), and one
diagnostic run with `breakout_diagnostics_enabled=true` settles it.

Explicitly **not** proposed here: changing `breakout_min_history_bars`. That was this
investigation's prime suspect and it was falsified for free — all 103 W0 movers carry >=111 bars
(median 702, none under 25).

## Caveats

`pit_mode=research` (lookahead). The granularity claim rests on the `1Hour` bar-load lines and the
3600s run parameter, both from the run logs; the intended-window claim rests on the source
comments.
