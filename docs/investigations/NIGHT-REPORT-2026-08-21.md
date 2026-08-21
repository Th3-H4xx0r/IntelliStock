# Overnight report 2026-08-21: three experiments, one hard lesson about chop

Autonomous session on the handoff's steps 3/4/5. Six backtests, all cold-attested, all
preregistered. Every doc/instance restored; nothing adopted; nothing touches real money.

## 1. max_positions 6→4 — NOT adopted, but the best-characterized lever in the project

Full detail: `prereg-max-positions-4-2026-08-21.md`. Three cold pairs:

| window | regime | overlap | verdict | mechanism | delta |
|---|---|---|---|---|---|
| d | bull | 62% | READABLE | **fired** (+33% entry weight, caught DELL +190% at 13.5% NAV, trades 12→8) | **+0.89pp** |
| c | chop→bear | 78% | READABLE | armed, seldom binds | **+1.14pp**; SQQQ guard INTACT ($3,302→$3,358) |
| f | chop | **27%** | **VOID** | +15% entry weight | not quotable (nominal −1.22% vs −3.77%) |

**Not adopted** because the prereg rule cannot be satisfied while window f is VOID, chop is the
strategy's one losing regime, and the nominal chop outcome was worse. Adopting on d+c would be
the split-endpoint anti-pattern. Disposition: stays 6 on doc 195; **first candidate for a
paper-era A/B.**

## 2. The hard lesson: cold ≠ comparable in chop

Window f's arms started IDENTICAL_COLD and still shared 27% of their names. In chop, any lever
that perturbs an early buy cascades discovery down a different path. Since cold A/A is
byte-identical, ALL divergence is caused by the lever — but the return delta measures which
names the perturbed discovery drew, not the lever's value. **Chop return effects are
structurally unmeasurable by cold backtest.** This narrows the measurement doctrine again:
cold pairs adjudicate bull/bear levers; chop levers can only be judged on mechanism counts or
forward paper.

## 3. Displacement break #3 — inconclusive by vacuity (and a free determinism win)

Full detail: `prereg-displacement-break3-2026-08-21.md`. Enabled on THROWAWAY doc 196
(v2-conv-ctl, cold, window d): **zero DISPLACEMENT lines** — the trigger sits in the
funding-refused-buy branch and post-conversion-fix cold runs never refuse a buy (0 SKIP BUY).
Break #3 remains unconfirmed; a probe needs a jamming book (window f or ~$3000 cash).
Byproduct: bt 596938 was **byte-identical to the doc-195 control on a different instance and
different doc id** — cold determinism holds across instance AND document identity.

## 4. Passive execution — preregistered, not yet run

`prereg-passive-execution-2026-08-21.md`. The 2026-08-03 rejection was reasoning-only and
leaned on the now-dead 10pp noise floor; entry-lag risk is now measurable. Queued as the next
backtest pair (window f — noting §2, its PRIMARY endpoint is mechanism counts, which survive
VOID book overlap).

## 5. Production readiness — unchanged: 0/6, and only paper moves it

`assess_live_readiness.py`: 0/6, `paper_observation` BLOCKING, `paper_days` 0. Every other gate
(PIT provenance, sealed holdout, chaos rehearsals, watchdog) is real work measured honestly as
not-done. Nothing tonight changes the standing recommendation, it strengthens it:

- Cold backtests understate (no discovery pool), warm ones are contaminated, and §2 shows chop
  is cold-unmeasurable entirely. **Forward paper is the only instrument left** for the
  questions that remain (chop behaviour, warm-pool edge vs lookahead, max_positions=4).
- The one-command start exists; it is the operator's call, not mine.

**Do not fund this yet.** The objective is +12%/two-month; the best honest cold numbers are
mean +0.5pp vs SPY over three windows. The bear edge is real, bull mechanism now works, chop
still loses. Paper will say whether the warm-pool advantage (the +20.53% window) is real.

## State after this session
- doc 195: UNTOUCHED net of experiments (max_positions=6 verified restored twice, dwell=2,
  displacement OFF, no passive keys). alpaca-main: still points at 195, still STOPPED.
- doc 196: THROWAWAY (labelled), displacement ON — do not link anything to it except probes.
- v2-conv-ctl → 194 (restored). v2-conv-trt state: cleared/cold from last arm.
- Run ledger tonight: 760962/978281 (d pair), 712452/808739 (c pair), 209809/904667 (f pair,
  VOID), 596938 (displacement probe).
