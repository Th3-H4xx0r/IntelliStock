# Every hold is a default, not a decision

Date: 2026-08-13. Run: bt **180796** (W0, doc 195, `hold_diagnostics_enabled=true`,
`satellite_displacement_enabled=true`). 18,177 lines, 1,859 HOLD DIAG lines, **0 errors**.

## Result

| measure | value |
|---|---|
| hold decisions logged | 1,859 across 214 symbols |
| of those with a nexus raw score | **0 (0%)** |
| restricted to names that moved >=30% | 360 holds across 35 symbols |
| of those with a raw score | **0 (0%)** |
| mean vote split on held movers | up=0.00, flat=1.00, down=0.00 |

Every hold in the window resolves to exactly one flat vote of weight 1.0, and no raw score is
attached. Not one held name — including 35 that went on to move >=30% — carries a non-zero opinion.

## What this answers

The objective asks: "ask why a name ranked #1 did not get bought." For the 84 of 103 movers that
never received a buy intent, the answer is not that a conviction threshold rejected them. **They
were never given an opinion at all.** The aggregator received a single flat vote and defaulted to
hold, every bar, for the whole window.

That reframes the funnel. The 103 -> 19 collapse is not selective rejection; it is absence of
evaluation. Conviction ordering, displacement, the satellite cap and the buy gate all operate
downstream of a stage that simply never fires for four out of five movers.

## The limit of this claim

`raw=absent` is read from the per-symbol sizing hint, which may only be populated for names that
became buy candidates. So absence there does not prove no score was computed anywhere. The vote
split is the harder evidence: `up=0.00 flat=1.00 down=0.00` is the aggregated decision itself, and
it says one strategy voted hold. Whether that flat vote is a considered "no" or an uncomputed
default cannot be separated from this log alone, and saying otherwise would repeat the
overclaiming corrected twice earlier in this investigation.

The next diagnostic that would separate them is to log, inside the nexus scorer, whether a symbol
was evaluated at all on that bar. That is again telemetry, not a trading change.

## Displacement, second observation

12 `DISPLACEMENT EXECUTE` lines, now one trim per holding per tick after the bt 511709
over-subscription fix:

    trimming 45% of TSM ($858.81) to free $364.96 for MBLY
    trimming 48% of TSLA ($802.55) to free $367.71 for OI
    trimming 42% of URA ($914.06) to free $361.72 for TTWO
    trimming 40% of URA ($973.14) to free $368.86 for SNDK

The lever now trades: it frees cash from the weakest holding for a materially stronger candidate,
including for `SNDK`, the name this whole investigation started from. Its P&L effect is **not**
established — this run shares a warm salt with four prior runs and is not a matched pair.

## Caveats

`pit_mode=research` (lookahead), one window, one run, not promotion-eligible.
