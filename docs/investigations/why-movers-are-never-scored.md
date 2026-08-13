# The default is zero, and the built-in rescue for it is not firing

Date: 2026-08-13. Code: `backend/strategies/graph_nexus_analysis.py::_finalize_scores`.
Run evidence: bt **180796** (W0, hold diagnostics on). No extra credits — this is code reading
plus the log already pulled.

## The mechanism, in the source

```python
for sym in symbols_list:
    fresh_score = 0                                              # default
    if sym in sentiment_data and sentiment_data[sym]["sentiment"] != 0:
        ...                                                      # 1: direct LLM sentiment
    elif sym in propagated:
        ...                                                      # 2: graph-propagated score
    # otherwise fresh_score stays 0
```

A symbol that has **no LLM sentiment** and **no graph path** keeps `fresh_score = 0` and is held.
The zero is an initialisation default, not a judgement. This is what the hold diagnostics measured
from the other side: 1,859 holds, every one `up=0.00 flat=1.00 down=0.00`, raw score absent.

That closes the question left open in `hold-diagnostics-result.md`. For names with neither news nor
a graph edge, "hold" is *uncomputed*, not *considered*.

## The rescue path already exists and is effectively idle

Immediately below, the code has exactly the mechanism these names need — promotion on price action
alone, no news required:

```python
# V31 Section 4.1: per-stock breakout score boost ...
# PROMOTE a previously-neutral stock to score=1 even without LLM news.
if bool(config.get("breakout_score_boost_enabled", True)):
    _bk_boost, _ = _compute_breakout_score_boost(sym, price_history, config)
    if fresh_score <= 0 and _existing_raw + _bk_boost >= buy_thresh:
        fresh_score = 1
```

doc 193 has `breakout_score_boost_enabled = True` and `buy_threshold = 0.3`. Yet in 18,177 log
lines of bt 180796 only **11** mention breakout at all, and 84 of 103 movers still ended the window
at a flat vote. The lever that is supposed to catch a big price move with no news is on, and is
not reaching them.

`_compute_breakout_score_boost` returns `(0.0, "")` on several early guards, one of which is
`breakout_min_history_bars` (default 25). Newly discovered names — exactly the small, fast movers
this objective is about — may not have 25 bars of history when the move starts.

## Why this is the right next thing, and what it is not

The objective says validate what is built rather than rebuild. This is built, enabled, and inert.
It sits at the 103 -> 19 stage, which loses 84 names against the 12 lost downstream where all of
this thread's work has gone.

It is **not** on the do-not-retry list: that list covers the entry-extension gate, turnover
exemptions, `max_positions`, bull-alpha entry gates, the asymmetric-objective search, and the
"ranking is noise" claim. Breakout promotion is none of those.

It is also **not** yet a proposal to change a threshold. The next step is one diagnostic run that
logs which guard inside `_compute_breakout_score_boost` returns early, per symbol. Only then is it
knowable whether the boost is blocked by history length, by the threshold, or by something else.
Changing `breakout_min_history_bars` before that would be a guess, and config-based predictions in
this repo have been wrong more often than right.

## Caveats

One window, one run, `pit_mode=research` (lookahead), not promotion-eligible. The count of 11
breakout mentions is a keyword scan and may undercount silent early returns — which is precisely
why the instrument, not the knob, is the next step.
