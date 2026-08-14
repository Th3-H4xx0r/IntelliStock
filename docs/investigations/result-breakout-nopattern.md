# Why the breakout rescue finds nothing on names that doubled

bt 778288, `BREAKOUT NOPATTERN` instrumentation, 1,678 samples.

## The numbers

```
BREAKOUT NOPATTERN: AAOI bars=104 last=37.01 h25=41.00(-9.7%)  h252=41.00(-9.7%)  vol=1.6x gap=+3.7%
BREAKOUT NOPATTERN: LITE bars=104 last=343.08 h25=397.17(-13.6%) h252=397.17(-13.6%) vol=1.0x gap=+1.5%
BREAKOUT NOPATTERN: SNDK bars=99  last=334.44 h25=353.84(-5.5%)  h252=353.84(-5.5%)  vol=1.9x gap=+1.8%
BREAKOUT NOPATTERN: VICR bars=102 last=138.45 h25=142.07(-2.5%)  h252=142.07(-2.5%)  vol=0.8x gap=-0.1%
```

Three facts, all measured:

**1. The history is live and correct.** Across AAOI's evaluations `bars` advances 94 -> 104 and
`last` takes 11 distinct values. The point-in-time filter is working; the bars are not stale. Both
candidate explanations from the previous document - stale bars, and thresholds untested against the
data - are now answered, and the answer is neither.

**2. `h25` and `h252` are identical.** With ~104 bars available, `closes[-252:]` is just all of
them, so the "52-week high" is really a five-month high and the 52w test can never be stricter than
the 5w test. The `breakout_52w_boost` of +0.5 is unreachable as a separate signal - it fires only
when the 5w test already has.

**3. The movers are not at highs when they are evaluated.** `AAOI` sits 9.7% below its own 25-bar
high, `LITE` 13.6% below, `SNDK` 5.5%, `VICR` 2.5%. The promotion test requires
`current_close >= 0.99 * high_25` - within 1% of the high. None of them qualify at the moments they
are scored.

## What that means, and it is not a bug

The scorer is behaving exactly as written. The mismatch is upstream, and it lines up precisely with
blocker (1) in the objective:

* Momentum discovery finds a name *after* it has run - `Discovered stock (momentum): AAOI
  (20d=+33.9%, 60d=+3.1%)`. Discovery is triggered by the move having already happened.
* By the time the name is in `symbols_list` and evaluated, it is typically consolidating off its
  high, not printing a fresh one.
* The breakout rescue is a "**at a new high right now**" test. A name discovered on the strength of
  a completed 20-day run is, at that moment, usually 2-14% below its high.

So the two mechanisms are pointed at different instants: discovery selects on the move *having
happened*, promotion requires the move to be *happening now*. A name can rarely satisfy both, and
that - not missing history, not calibration - is why 7,340 evaluations produced almost no
promotions.

## What follows, and what does not

This does **not** license widening the promotion test. Relaxing `0.99` to catch names 10% off their
highs would buy consolidating names on price alone, and the entry-extension gate exists precisely
because that was measured at -7.95%. The objective lists loosening it under DO NOT RETRY.

The honest reading is that the objective's own framing is the right one: *enter while the move still
has room*. The system currently discovers after the run and then asks for a fresh high, which is the
narrowest possible intersection. Any real fix is about **when a name enters the candidate set**, not
about the threshold it faces once there.

That is a design question for the operator, not a parameter to tune. Nothing further is proposed
here.

## Status

`breakout_history_fallback_enabled` reverted to OFF; `breakout_diagnostics_enabled` is log-only and
its behavioural invariance is now under test. doc 193 untouched at 580 keys. No return claim rests
on any of this.

## The qualifying days precede the evaluations

Reconstructing each mover's daily series from the run's own quote stream and asking how often it
sits within 1% of its trailing 25-day high:

| | count |
|---|---:|
| movers with enough observations to test | 30 |
| **ever within 1% of their trailing 25-day high** | **17 (57%)** |
| never | 13 |

`AAOI` qualifies on **5 of 8** testable days, `LITE` 5 of 11, `SNDK` 2 of 12, `VICR` 2 of 9. So the
promotion test is not impossible for these names - they do print fresh highs.

And yet in the run they never qualify. The reason is visible in the instrumented output: `AAOI` is
evaluated 11 times with `h25` pinned at **41.00** while `last` ranges 34.84 to 37.01. That 41.00 is
`AAOI`'s own peak - the same peak that produces the +87.7% figure in the mover table. The name is
only ever scored on the way *down* from it.

**The qualifying days happen before the name becomes evaluable.** Discovery fires on a completed
20-day run, and by then the fresh highs that would have promoted it are in the past.

This is blocker (1) - entry timing - measured at the mechanism level rather than inferred from fill
prices. It also explains the two facts that looked contradictory all session: the movers are found
(35 of 36 discovered), and they are never promoted (7,340 evaluations, near-zero promotions).

## The consequence for the objective

The objective's arithmetic - four names at ~10-15% of NAV each capturing half of a 60% move - needs
names to be *bought while the move has room*. Sizing is solved at 14.00% and capture on names
actually bought is 40-100%. What is missing is candidacy at the right moment, and no downstream
lever can create it: ordering, conviction reserve, displacement and the history fallback all act on
names that have already become candidates.

The lever that would matter is upstream of everything tested here: **shorten the lag between a move
starting and the name becoming scoreable**. That is a change to discovery cadence or criteria, it
has a real cost (more candidates, and turnover is the known leak at ~290%/mo against ~50%
break-even), and it is a design decision rather than a parameter. It is not proposed here, and
nothing in this session licenses picking a value for it.

## How late is discovery, in days and in percent

Measured across the 50 movers with a resolvable move start and a later peak, using each run's own
quote stream:

| quantity | value |
|---|---|
| median days from a name first being +10% to its peak | **19 days** |
| median 20-day return already booked when discovery fires | **+24.2%** |

Examples:

| name | +10% on | peak on | days of run | 20d return at discovery |
|---|---|---|---:|---:|
| SNDK | 2026-01-02 | 2026-02-23 | 52 | +15.6% |
| VICR | 2026-01-05 | 2026-02-25 | 51 | +20.4% |
| MRNA | 2026-01-06 | 2026-02-23 | 48 | +37.0% |
| AAOI | 2026-02-09 | 2026-02-27 | 18 | +33.9% |
| LITE | 2026-02-02 | 2026-02-25 | 23 | +21.7% |

Two readings, and they point the same way.

**There is room.** The median mover runs for 19 days between first being up 10% and topping out, and
several of the largest run for 48-52 days. The objective's requirement - *enter while the move still
has room* - is achievable on this data; the raw material is genuinely there, as the objective says.

**Discovery consumes a quarter of it.** The momentum screen fires on a 20-day lookback, so by
construction it cannot see a name until roughly a month of move exists, and the median name is
already +24.2% on that lookback when it appears. `SNDK` is the clearest case: 52 days of run
available, discovered with +15.6% booked, and filled 94.9% of the way through.

## What this quantifies for the operator

The gap between "the move is detectable" and "the name is a candidate" is the single largest
remaining loss, and it is now measured rather than asserted: about **19 days of available run**, of
which discovery's own lookback consumes the first portion, and after which the breakout promotion
demands a fresh high the name has usually stopped making.

Three levers exist, all upstream, all with real costs, none tested:

1. **Shorten the discovery lookback** - sees names earlier, admits more noise, and turnover is the
   known leak at ~290%/mo against ~50% break-even.
2. **Promote on the pullback rather than the high** - directly contradicts the entry-extension gate,
   which was measured at -7.95% and is listed under DO NOT RETRY.
3. **Evaluate discovered names more often** - cheap in principle, but the measurement above shows
   the qualifying highs mostly precede discovery, so this alone would not have caught `AAOI`.

Option 1 is the only one not already falsified, and it trades directly against the constraint the
objective names as the known leak. That trade is the operator's to make; this document does not make
it.


## Full-run data revises the previous section

bt 778288 to completion: 2,965 `BREAKOUT NOPATTERN` samples across 361 symbols. The earlier reading
came from 11 evaluations in a truncated probe — the same mistake that produced a false "zero
promotions" verdict earlier in this investigation.

**First, a tautology avoided.** `NOPATTERN` fires only when the boost is zero, which *means* the 5w
test failed, which *means* the name was not within 1% of its high. "0 of 361 ever qualified" is
therefore circular and says nothing. The meaningful statistic is how *close* they came.

**Closest approach to the 25-bar high, per symbol, across the whole run:**

| group | median | best | worst |
|---|---:|---:|---:|
| the 45 movers ≥30% | **−2.6%** | −1.0% | −30.6% |
| all 361 evaluated symbols | −2.1% | −1.0% | — |

**18 of the 45 movers came within 2% of promoting**, several sitting exactly at the −1.0% boundary:
`ATI`, `NEXA`, `KMT`, `MU` at −1.0%; `TER`, `LITE`, `VIAV` at −1.1%; `AMAT` −1.3%; `RDW` −1.4%.

And `MU` **did** promote — it appears among the seven symbols that received a boost somewhere in the
run, two of which are ≥30% movers (`MU`, `URAA`).

## What this changes

My previous statement — that the qualifying highs simply precede evaluation — is **too strong**. The
movers are not far from the promotion test; they cluster **just below** it, a median 2.6% away, with
40% of them within 2% and several at the boundary. The mechanism is close to firing on exactly the
names the objective cares about, and occasionally does.

That is a materially more hopeful picture than the previous section implied, and it is also a more
dangerous one: a threshold that 18 of 45 movers approach within 2% is precisely the kind of dial
that looks free to loosen. It is not. Relaxing `0.99` to catch names 2% off their highs buys
consolidating names on price alone, which is what the entry-extension gate exists to prevent and
what was measured at −7.95%. The objective lists it under DO NOT RETRY.

The honest conclusion is narrower than either of my earlier ones: **the promotion path is
marginally, not structurally, out of reach for large movers** — and closing that margin safely is a
question about *when names become candidates*, not about the size of the threshold they meet.


## Evaluation coverage is sparse, which revives an option I dismissed

Caveat first: bt 778288's pulled log spans 2026-01-01..01-29 against a window ending 03-01, so it is
partial. The coverage ratios below are computed within that span and should be re-measured on a
complete run before being relied on.

Within it, movers are evaluated by the breakout scorer on a **median 43% of trading days**, and
**29 of 45 on fewer than half**:

| mover | evaluations / 21 days | coverage | closest approach |
|---|---:|---:|---:|
| AAOI | 18 | 86% | −3.4% |
| LITE | 15 | 71% | −1.1% |
| MRNA | 10 | 48% | −3.1% |
| RDW | 9 | 43% | −1.4% |
| VICR | 6 | 29% | −2.1% |
| SNDK | 3 | 14% | −5.1% |

Put beside the previous finding — 18 of 45 movers come within 2% of the promotion test, several at
the −1.0% boundary — this changes the picture again. A name that is repeatedly within 1-2% of
qualifying, and is only *looked at* on 43% of days, may be missing its qualifying moments rather
than never having them. `SNDK` is the extreme case: 3 evaluations in a month, closest −5.1%, while
its underlying series (from the quote stream) sits within 1% of its trailing 25-day high on 2 of 12
testable days.

Earlier in this investigation I dismissed "evaluate discovered names more often" on the grounds that
qualifying highs precede discovery. That was based on 11 evaluations from a truncated probe. On this
fuller data the dismissal does not hold: coverage is sparse enough that the two explanations —
missing the moment, versus the moment never arriving — are not distinguished by anything measured so
far.

**Distinguishing them is cheap and does not require a trading change**: log, for each mover on each
day, whether `current_close >= 0.99 * high_25` held *whether or not the scorer ran*. If qualifying
days exist on days the scorer skipped, cadence is the lever. If they do not, discovery latency is.
That measurement has not been built, and no lever should be chosen before it is.
