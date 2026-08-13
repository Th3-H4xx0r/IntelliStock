# Will this generalize, or am I fitting one window?

Date: 2026-08-12
Written BEFORE the remaining windows ran, deliberately.

## The conflict that has to be named

Two requests arrived together:

1. "fix that drop so it maintains the peak" (treatment 906181 peaked at $9,110 and ended $6,965);
2. "make sure this generalizes over ALL windows and makes money in the FUTURE".

**These conflict.** The $9,110 peak is only identifiable after the fact. Any drawdown-circuit
threshold chosen because it preserves that specific peak is fitted to one window, in one
regime, led by one name. It would look excellent in this backtest and carry no information
about the future. Satisfying (1) directly is the fastest way to fail (2).

So the peak is **not** being engineered. What follows is what can honestly be done instead.

## What is actually established so far

**Established (mechanism, not edge):** the conversion failure is real and structural. The
satellite budget is consumed chronologically, so ~5 ordinary buys exhaust the design share and
every later name is starved. `SNDK` was funded **$29** in the reference run and **$1,018**
after enabling `satellite_conviction_reserve_pct`. That is a design flaw and its correction is
causally motivated, not curve-fitted.

**Not established:** that this makes money. Specifically:

* only one window has a treatment result;
* the comparison used so far was against doc 193 with a **warm** history scope, which is not a
  valid control. The genuine cold-salt control (`873929`) was still running when this was written;
* `total_trades` rose 17 -> 34. Turnover is the known leak (~290%/mo live versus ~50%/mo
  break-even). The preregistration already calls a turnover rise disqualifying;
* every run is `pit_mode=research`, i.e. **lookahead-biased**, and by the project's own rule is
  not promotion-eligible.

## Why the giveback is not a bug to tune away

The log shows the sequence plainly:

```
Drawdown circuit KILL: -18.6% from peak ($9110 -> $7415)
Drawdown circuit KILL: liquidating 5 position(s): AGQ, APP, AVNT, COPX, SPY
Drawdown circuit KILL: liquidating 3 position(s): HYMC, SNDK, SPY
```

The circuit is a **reaction**. Positions fell 18.6% first; the circuit then liquidated the
entire book at the low — including `SNDK`, the name the whole thesis depends on — and the peak
re-based to $7,329.

There is a legitimate design question here, and it is *not* "what threshold preserves $9,110".
It is: **should a portfolio-level drawdown circuit liquidate a high-conviction winner that is
still in an intact uptrend?** The objective's own instruction is "exit on a real turn not
noise" and "hold". A blanket KILL is the opposite of that.

That question can be answered structurally, and it must then be tested out-of-sample like any
other change — not tuned until one curve looks right.

## The only protocol that can support a future-money claim

1. **Finish the real pair on W0.** Cold-salt control versus cold-salt treatment, one variable.
2. **Repeat on at least three more windows**, chosen in advance and not re-picked after seeing
   results:
   * `2026-03-30..2026-04-27` — out-of-sample bull
   * `2026-06-01..2026-07-01` — **not** led by semiconductors
   * `2026-03-02..2026-03-30` — bear, as a safety veto
3. **Accept only on the preregistered rules**: differences inside +/-4.94pp are noise; a return
   gain does not offset a drawdown worsening of 4.94pp or more; a turnover increase is
   disqualifying; the bear window is a veto, not a place to tune.
4. **Consistency over magnitude.** A lever that helps in 4/4 windows by a little is worth far
   more than one that wins hugely in the reference window and is flat or negative elsewhere.
   The latter is the signature of fitting.
5. **Remove lookahead before any real-money claim.** `pit_mode=research` cannot support one.
   Strict PIT requires frozen manifests that do not yet exist for these dates.
6. **Only then** paper-trade forward on unseen data. No historical result, however clean, is
   evidence about the future until it survives data that did not exist when the rule was written.

## What I will not do

* Not tune the drawdown circuit, the reserve value, or any threshold to improve a window whose
  result I have already seen.
* Not re-pick windows after seeing outcomes.
* Not report a single-window number as evidence of edge.
* Not ship anything to doc 179 / `alpaca-main` (real money) without explicit sign-off.

## Honest statement of limits

No backtest on historical data can establish that a strategy "will make money in the future".
It can only fail to disqualify a mechanism. The strongest claim available at the end of this
protocol is: *a structural allocation flaw was identified and corrected, and the correction did
not degrade results across four preregistered windows including one out-of-sample, one
non-semiconductor and one bear.* That is a licence to paper-trade forward, not a licence to
fund it.
