# The breakout rescue never runs: price_history is empty at scoring time

Date: 2026-08-13. Run: bt **278531** (W0, doc 195, `breakout_diagnostics_enabled=true`).
18,846 lines. One diagnostic run settled a question three prior investigations could not.

## Result

    BREAKOUT SKIP lines            2,922
    reasons                        skip:bars=0<25   -> 2,922 (100%)
    distinct symbols               396
    breakout promotions that fired 0

**Every single evaluation exits at `bars=0`.** Not one symbol reaches the breakout arithmetic, and
not one promotion fires in the whole window. Of 40 sampled skipped symbols, 23 have a
`loaded N 1Hour bars for SYM` line in the same run — the bars exist, they are simply not in the
`price_history` map handed to the scorer.

## Why this is the answer to the whole investigation

`_finalize_scores` sets `fresh_score = 0` for any name with no LLM sentiment and no graph path.
The one mechanism that can rescue such a name on price action alone is the breakout boost, and
`buy_threshold` is 0.3 while a single 5-week-high hit contributes exactly +0.3 — it should fire
readily for a sustained mover.

It never fires. So for a name with no news and no graph edge there is **no path to a buy at all**,
regardless of how large its move is. That is the 103 -> 19 collapse, and it is structural rather
than a threshold being slightly wrong:

| window | moved >=30% | ever a buy intent | bought |
|---|---:|---:|---:|
| W0 | 103 | 19 | 7 |
| W3 | 65 | 16 | 1 |

## What is now established, and what is not

Established: the boost is enabled in doc 193, is reached on every bar for hundreds of symbols, and
exits immediately because `prices_history.get(sym)` is empty. `price_history` is built from
`symbols_for_data` (`= list(symbols or [])`) while the scorer iterates the expanded discovery
universe, so newly discovered names — precisely the movers this objective is about — are absent.

Not established: whether populating it would improve returns. That is a real trading change and
needs the full protocol (paired arms, >=3 windows, one OOS, one non-semiconductor, bear as veto,
+/-4.94pp floor, turnover disqualifying, measured against SPY per window). Prior sessions shipped
five levers that logged correctly and changed nothing; this one has the opposite failure mode and
deserves the same scepticism.

Also unchanged: the granularity mismatch recorded in `breakout-window-granularity.md`. The 25- and
252-bar windows are daily-bar constants running on hourly bars. If the history map is ever
populated, those constants are calibrated for the wrong bar size and must be revisited **before**
any return claim.

## Cost

Four backtests total this session against a $15 cap, three of them killed as soon as they had
answered. The prime suspect going in (`breakout_min_history_bars=25`) was falsified for free from
existing logs — all 103 W0 movers carry >=111 bars, median 702.

`pit_mode=research` (lookahead), not promotion-eligible.

## Correction: "frozen at startup" was wrong, and the fix is not yet known

The section above attributes the empty history map to `symbols_for_data` being set once at startup.
That is not accurate. Three sites (broker.py ~14001, ~14051, ~14112) do exactly the maintenance it
claims is missing:

```
for loaded_sym in loaded_syms:
    if loaded_sym not in symbols_for_data:
        symbols_for_data.append(loaded_sym)
        _rebuild_ph = True
if _rebuild_ph and price_history is not None:
    price_history = get_price_history_up_to_current(data, symbols_for_data, current_time)
```

So the universe *is* maintained. Two facts still have to be reconciled with the measurement:

1. **Ordering.** The strategy scoring call is at ~13663, before all three rebuild sites. On the tick
   a symbol is first discovered and its bars written into `data`, the scorer has already run
   without it. That explains a *first* miss per symbol, not a persistent one.
2. **Persistence.** 2,922 skips across 396 symbols is ~7.4 skips each, so these names are missing
   from the map on many ticks, not once. Ordering alone does not account for that.

Candidate explanations, none yet tested: the rebuild is gated on `price_history is not None` and
may be skipped when it is None; the `loaded_syms` paths may not cover the expansion path that logs
`Backtest symbol expansion: loaded N bars` and writes `data[sym] = bars` at ~1874; or the rebuild
runs on a different branch from the one the reference window takes.

**No fix is proposed here.** A patch that widens the history map to `data.keys()` was drafted and
deliberately not applied, because it would paper over whichever of the above is actually true and
could be redundant with machinery that already exists. The measurement (100% `bars=0`, zero
promotions) stands and is reproducible; the causal story behind it does not yet, and shipping a
trading change on a half-understood cause is how the five previously-inert levers were shipped.

Next step is diagnostic, not corrective: log which rebuild branch is taken and whether
`price_history` is None at the scoring call, for a symbol known to be skipped.
