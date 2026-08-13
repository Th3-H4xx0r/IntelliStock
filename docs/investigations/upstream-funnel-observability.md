# The largest funnel stage is the one that is not instrumented

Date: 2026-08-13. Source: bt **873929** (W0 control). No credits spent.

## The funnel, W0

| stage | count | share of movers |
|---|---:|---:|
| names moving >=30% in-window | 103 | 100% |
| ever given a BUY intent | 19 | 18% |
| reached the broker buy gate | 9 | 9% |
| bought | 7 | 7% |

Of the 84 with no buy intent, **52 never appear in any scoring, ranking, watchlist or backfill-queue
line** in 39,748 log lines. 32 do appear and were still held.

## What those 52 actually look like

`AEHR` and `CIEN` are typical, and they are not undiscovered:

    Discovered stock (momentum): AEHR (Nd=+N%, Nd=+N%)
    Nexus discovered: expanding symbols with N new tickers: ...
    AEHR @ 2026-.. ($..): hold action_intent=hold (weighted scores from 1 strategies)   x11

So momentum discovery fires, the name is added to the universe and quoted every bar, a decision is
produced — and that decision is `hold`, every bar, for the rest of the window. `AEHR` went on to
move >=30%.

## The honest conclusion, and the limit of it

It is tempting to say "these were never scored". That overstates what the log supports: the
decision line says `weighted scores from 1 strategies`, so a score existed. What is missing is the
**value**. No `raw=` is printed for a name whose decision is `hold`, so there is no way to tell
from the log whether a held mover scored 0.2 or 1.49 against the 1.50 conviction threshold.

That is the finding: **the largest stage of the funnel — 84 of 103 movers, half of them with no
scoring trace at all — is the one stage with no diagnostic output.** Every mechanism claim in this
thread has been settled by grepping a log signature. This stage has none.

## Why that matters more than another lever

Blockers (1)-(3) and everything built today (conviction ordering, displacement) operate on the
19->7 step. That step loses 12 names. The 103->19 step loses 84. Work continues to concentrate on
the smaller stage because it is the only one that can be observed.

The cheap, zero-risk next step is therefore **not** another trading lever. It is to log the
conviction score and the binding reason on `hold` decisions for names above a move threshold, so
the 84 can be split into "correctly declined" and "wrongly declined". Until that exists, any claim
about why the winners are missed is speculation about the majority of cases.

This is default-OFF-able telemetry, changes no trading behaviour, and would make the next
experiment falsifiable rather than suggestive.

## Caveats

One window, one run, `pit_mode=research` (lookahead). The 52/84 count comes from a keyword scan
over scoring/queue lines and is approximate at the margins; the two names inspected by hand
(`AEHR`, `CIEN`) confirm the pattern but do not prove the exact count. Two earlier counts in this
investigation were wrong in the direction that flattered the thesis, which is why the conclusion
above is stated as an observability gap rather than a mechanism.
