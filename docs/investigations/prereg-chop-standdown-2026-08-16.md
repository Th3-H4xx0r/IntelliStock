# Preregistration: hold the index in chop

Written BEFORE either arm was launched. Session 2026-08-16.

**This is the first experiment in this project where RETURN is a legitimate endpoint.** The A/A
cold-start run (bt 479057 vs bt 193668) produced 100% traded-name overlap and a 0.012pp return
difference, so the noise floor is 0.5pp rather than 10pp. Every previous lever was measured
against a floor ten times larger than any effect it could plausibly have.

## The hypothesis, from measured P&L

**bt 325136** (window f, 2026-06-15..2026-08-01) is the only losing window, at −5.09% while SPY
returned **+0.69%**. Its P&L splits:

| sleeve | P&L |
|---|---:|
| satellite longs | **−$225.47** |
| SPY core | −$82.81 |

The satellite sleeve lost money buying momentum names in a flat tape: TSLA captured −25.72% of a
−23.45% move, CCL −12.53% of a −4.60% move, and **RCL −7.50% of a POSITIVE +8.10% move**. 35
trades on a six-name book in six weeks.

Why chop specifically, and why this is not curve-fitting: **window f classified 40 of 59 bars as
`chop` with only 3 `bear` bars**, so the SQQQ hedge never deployed. **Window c was ALSO
chop-dominated (46 chop) but had 19 bear bars** — the hedge fired, SQQQ captured 77% of a +16.98%
move for +$416.61, and that window BEAT SPY by 5.75pp. The difference between the two
chop-dominated windows is not the chop handling; it is whether a bear signal arrived. Window f
shows what chop does when it does not.

## The change

| key | control | treatment |
|---|---|---|
| `regime_profiles.chop.core_target_pct` | **0.35** | **0.85** |

ONE key. In chop the book holds the index instead of momentum satellites.

**It does not violate the objective's standing rule.** That rule forbids adding a `bear` profile,
because arming the core in a bear routes bear de-risk to cash and silently drops the SQQQ hedge.
Chop is not bear; doc 195 still has no `bear` profile, and `set_doc_config --set-path` now
refuses to create one.

## Design

Window **f** (2026-06-15..2026-08-01), instance `v2-conv-trt`, doc 195, 3600s, $6,000.
Each arm preceded by a full state clear attested `cold=True`. Arms strictly sequential.

## Endpoints, fixed in advance

1. **Return vs SPY (+0.69%).** Control is expected near −5%. Treatment PASSES if it beats the
   control by more than **0.5pp** (the cold floor) — and the honest bar is whether it closes the
   −5.78pp gap to SPY, not merely whether it is less bad.
2. **Comparability first.** If overlap < 60% or either arm is not cold, the run is VOID and no
   delta is quoted, exactly as before. A cold floor cannot launder a contaminated pair.
3. **Turnover must not rise.** The mechanism is "trade less in chop"; more trades would mean it
   did something else.
4. **The hedge must still work.** If the treatment reduces SQQQ deployment in any window, that is
   disqualifying regardless of return — the bear leg is the one validated edge this system has.

## CONTROL IN, and a prediction against my own hypothesis — recorded before the treatment

**bt 790588** (cold, window f): **−2.70%** against SPY +0.69%. 42 chop / 6 bear / 3 bull bars,
26 trades, SQQQ deployed (+$20.49, +0.93%).

| sleeve | P&L |
|---|---:|
| satellite | −$91.66 |
| SPY core | −$72.97 |

**The estimate in §"The hypothesis" is probably too optimistic, and here is why.** It assumed
that capital moved into the core earns SPY's +0.69%. It does not: **the core captured −1.41%
while SPY returned +0.69% — a 2.1pp drag**, the same drag measured in bt 333727 (+14.95% captured
of +16.66%) and bt 325136 (−1.55% of +0.69%).

So moving money from satellites into the core swaps one loss for a smaller one, not for a gain.
Rough arithmetic on this control: the satellite sleeve lost $91.66; routing most of it to a core
that itself bleeds ~2pp does not recover $91.66, it recovers some fraction.

**Recorded now, before the treatment lands, so it cannot be retrofitted.** If the treatment
comes back only slightly better than −2.70%, that is the core drag eating the benefit — and the
real lever is then the core's own execution, not the chop allocation.

## RESULT — DISQUALIFIED, despite the return improving

**bt 790588 (control) −2.70% vs bt 969796 (treatment) −1.53%.** Both cold-started and attested.

| endpoint | control | treatment | verdict |
|---|---:|---:|---|
| 2. comparability | — | — | **64% overlap — comparable** ✓ |
| 1. return | −2.70% | **−1.53%** | **+1.17pp, above the 0.5pp cold floor — READABLE** ✓ |
| 4. SQQQ deployment | $2,209.84 | **$3,410.31** | **+54% — hedge intact** ✓ |
| 3. turnover | **303% of NAV** | **361% of NAV** | **ROSE — DISQUALIFYING** ✗ |

**Verdict: the preregistered turnover rule fails it, and the rule stands.** The mechanism was
supposed to be "trade less in chop"; it traded MORE — 26→28 fills and $18,206→$21,631 of notional.
Whatever produced the +1.17pp, it is not the mechanism this document proposed, and accepting it
anyway would be choosing the endpoint after seeing the answer.

`regime_profiles.chop.core_target_pct` is reverted to 0.35.

**A measurement correction I made against myself:** I first read the drop in SQQQ *P&L*
($20.49 → $12.30) as reduced hedging and nearly failed endpoint 4 on it. P&L is not deployment —
BUY notional rose 54% on identical fill counts, so the hedge deployed MORE and simply exited at
different prices. The endpoint said "deployment"; measuring the proxy would have disqualified the
lever for the wrong reason.

**The prediction recorded before the run was right in direction.** The core drag did eat the
benefit: the treatment moved capital into a core that itself captured −1.41% against SPY's +0.69%.

### What this run actually taught, which is bigger than the lever

**Turnover is 303-361% of NAV over a SIX-WEEK window.** The objective's break-even is ~50%/month
— roughly 75% over this window. **Both arms run 4-5x break-even**, and the losing window's problem
may be less "which names" than "how often". That reframes the priority: the next lever should
attack turnover and its cost, not allocation. The core is the obvious suspect, since it is exempt
from the turnover budget, rebalances on a 5% band every 5 days, and gives away 1.7-2.2pp per
window against the index it is supposed to track.

## What I will not claim

- Not that one window generalises. Window d is bull-dominated and this lever barely binds there;
  window c is the one where it could COST money (its chop bars held XOM at +15.13% captured), and
  that is the follow-up run, not an afterthought.
- Not that a smaller loss is an edge. Turning −5.09% into −1% still trails SPY.
