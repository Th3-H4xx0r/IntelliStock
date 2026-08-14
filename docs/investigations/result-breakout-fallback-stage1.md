# Stage 1 probe: the fallback is inert

bt 179977, doc 195, `breakout_history_fallback_enabled=true`,
`breakout_diagnostics_enabled=true`, killed early once it had answered.

## Result against the preregistered criteria

| criterion | required | observed | |
|---|---|---|---|
| skip rate at `bars=0` | well under 100% | **318 / 318 = 100.0%** | FAIL |
| evaluations with `bars>0` | >= 1 | **0** | FAIL |
| breakout promotions | >= 1 | **0** | FAIL |
| `BREAKOUT FALLBACK ERROR` lines | 0 | 0 | pass |

**The lever is inert.** It is not throwing - it is silently supplying nothing.

Deployment was verified before drawing that conclusion: `check_deployed_code.py` with no argument
reports all six files matching the working tree, `graph_nexus_analysis.py` at `bec43e8420c5` local
and deployed. The code under test is the code that was written.

## This is the fifth inert lever, and the first one caught cheaply

Five levers have shipped in this project that ran and changed nothing. This is the sixth, but the
first to be caught by a **~$2 mechanism probe instead of a P&L pair**. Under the old habit it would
have been eight paired runs producing eight results inside the noise band, with no way to tell
inertness from a real null.

The preregistration required stage 1 to pass before stage 2 was allowed. It did not pass, so no
pair was run. That is the protocol working.

## Why the run cannot say *which* failure it is

The fallback returns `{}` for two very different reasons - `_overlay_bars_raw` absent from the
`strategy_cache` that `_finalize_scores` receives, or present but filtered to nothing by
`_visible_overlay_bars` - and it logged neither. That is my omission: this project's own rule is
**loud failure over silent inertness**, which caught 1,625 hidden `AttributeError`s in the
displacement work, and I applied it only to the throwing path, not to the empty-result path.

The most likely cause on reading: momentum discovery reads `_overlay_bars_raw` at
`graph_nexus_analysis.py:14154`, which sits **outside** `run_once` (lines 24970-33184). The
`strategy_cache` visible there may simply not be the object `run_once` forwards into
`_finalize_scores`. That is a hypothesis, and four hypotheses in the previous document were
confidently wrong, so it is not a basis for a fix.

## Shipped in response, default-OFF

`_breakout_history_fallback` now emits, when `breakout_diagnostics_enabled` is on:

```
BREAKOUT FALLBACK: cache=N symbols, requested=M, supplied=K
BREAKOUT FALLBACK: cache missing or malformed (type=..., cache_keys=N)
```

`cache=0` means the cache is not reachable from that scope. `cache>0` with `supplied=0` means the
point-in-time filter is emptying it. One short probe now separates those, which is what this run
should have been able to do.

## Status

`breakout_history_fallback_enabled` stays default-OFF and is **not** enabled on doc 193. No P&L
claim is made or implied. The root-cause analysis in
`result-displacement-pair-and-noise-floor.md` is unaffected: the scored set and the history map are
still disjoint across 7,156 evaluations. What is refuted is my chosen *repair*, not the diagnosis.
