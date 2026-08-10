# fresh-low-verification — the one open question in gap-oos, answered without a run

**Date:** 2026-08-10 · **Method:** `scripts/check_range_position.py` (read-only scan of
`AlpacaBarsCache`, the same table `backend/price_utils.py` writes) · **Runs spent: 0**

---

## 0. THE QUESTION

`gap-oos.md` §6 flagged its own rule as unverified:

> `bars_since_20d_low` is **not logged today**. For 542754 on 03-05 it is **inferred** from
> ret20 = -0.95% / +0.54%, `raw=chop`, and SQQQ filling 1.2% off its range low. **Verify before
> shipping:** stamp the diag and re-run 542754 cold; **if `bars_since_20d_low == 0` on 03-05 the
> rule is dead** and the honest answer is that no gate separates these two windows.

Runs are the scarce resource. But every bar the detector reads is already sitting in
`AlpacaBarsCache` from previous runs, so the question is answerable for free.

---

## 1. THE ANSWER: THE RULE IS ALIVE, WITH THE MAXIMUM POSSIBLE MARGIN

| bar | what happened | SPY `since_20d_low` | QQQ `since_20d_low` |
|---|---|---|---|
| **2026-03-05** | 542754 opens the leg that made **+$889** | **19** | **19** |
| **2026-03-30** | 383778 opens the leg that lost **-$257** | **0** | **0** |

19 is the **maximum value the statistic can take** in a 20-bar window: the low is the OLDEST bar in
it. The good park happened with the proxy at the TOP of its 20-session range (+0.88% off low on SPY,
+2.74% on QQQ — i.e. the market had barely fallen yet, which is precisely why a 3x inverse bought
there had room). The bad park happened on the bar that SET the low.

These are opposite ends of the statistic's range, on **both** candidate proxies, under **both**
close conventions (see §2). This is not a threshold that needs tuning — it is a separation.

`residual_sleeve_bear_block_at_fresh_low_bars` therefore blocks 383778's open and **cannot** touch
542754's, which is the exact property gap-oos asked for and could not confirm.

---

## 2. THE DETECTOR IS POINT-IN-TIME — AND THAT CHANGES THE RECOMMENDED SETTING

The replay table in `_rally_onset`'s docstring (`graph_nexus_analysis.py:7240`) is shifted exactly
one session against a naive "closes through D" reading. Reproducing it with a one-session lag
confirms why: at 15:00 on bar D the last COMPLETE daily close is D-1, so the detector on 03-31 is
still looking at 03-30's close — which IS the low.

**Decision-time view (PIT lag = 1), 383778's four bear bars:**

| decision bar | last close seen | ret20 | `>ma10` | %off 20d low | `since_20d_low` | covered by |
|---|---|---|---|---|---|---|
| 03-30 | 03-27 | -7.37 | False | +0.00 | **0** | fresh-low N>=1 |
| 03-31 | 03-30 | -8.20 | False | +0.00 | **0** | fresh-low N>=1 |
| 04-01 | 03-31 | -3.89 | **False** | +3.57 | **1** | fresh-low **N=2 only** |
| 04-02 | 04-01 | -4.44 | True | +4.72 | 2 | `regime_rally_onset` |

**gap-oos's proposed N=1 leaves 04-01 uncovered.** Its table has `>ma10 = True` on 04-01; this
replay gets `False`. The reason is that the reclaim is knife-edge there:

| bar | SPY close | SPY ma10 | margin | QQQ margin |
|---|---|---|---|---|
| 03-30 | 634.04 | 655.92 | -3.34% | -4.41% |
| 03-31 | 632.02 | 652.27 | -3.11% | -4.44% |
| **04-01** | 649.85 | 650.19 | **-0.05%** | **-0.59%** |
| 04-02 | 655.44 | 649.67 | +0.89% | +0.70% |

A **34-cent** margin on a $650 index. Whether `rally_onset` fires on 04-01 depends on the daily-close
convention (official daily bar vs last hourly bar), which is exactly the kind of coin-flip that
should never be load-bearing.

**Therefore: set `residual_sleeve_bear_block_at_fresh_low_bars = 2`, not 1.**

* N=2 covers 03-30, 03-31 **and** 04-01 outright, leaving only 04-02 to `rally_onset`, where the
  margin is a comfortable +0.89% / +0.70%.
* N=2 costs the good window **nothing**: 542754's first park sits at `since_20d_low = 18` under PIT,
  **sixteen bars clear** of the threshold. There is no value of N between 3 and 17 that would
  behave differently on either window, so the setting is not fitted to a boundary.

---

## 3. WHAT THIS DOES *NOT* SHOW

* **It is gate arithmetic, not P&L.** Blocking the open frees ~$2,100; where that cash actually
  goes (bear caps `max_positions` to 2, per `383778.log:373`) is a run-level question. The $257 is
  a floor on the saving, not the whole counterfactual.
* **n = 2 windows.** These are the only two windows in the dataset with any bear bars at all. The
  mechanism is measured on 542754 and 383778 and nowhere else.
* **The cached-bars replay is not the detector.** It resamples hourly cached bars; the detector has
  its own PIT resampler and proxy-selection order. Values agree to ~0.1pp on every non-knife-edge
  bar, which is why §1's 19-vs-0 conclusion is safe and §2's 04-01 bar is explicitly not.
* Nothing here touches an ADD, the conviction ratchet, the leg stop, the trailing bank, the
  protective exit or the episode latch.

---

## 4. REPRODUCE

```bash
python3 scripts/check_range_position.py
```

Read-only. Prints §1's table, the PIT replay of §2, and the ma10 margins. Requires only `.env`
(RethinkDB) — no backtest credits, no deploy.
