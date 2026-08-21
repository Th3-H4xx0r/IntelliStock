# Preregistration: max_positions 6→4 (cold pair, window d)

Date: 2026-08-21. Registered BEFORE the runs.

## Hypothesis
The objective's stated mechanism is "four names at ~10-15% each". The satellite book currently
runs `max_positions=6` and per-name weight has measured mean 6.75% / median 4.73% of NAV.
Cutting the cap 6→4 forces the same capital into fewer, larger positions.

## Lever
`max_positions: 6 → 4` on doc 195, treatment arm only. (LOWERING — the DO-NOT-RETRY entry is
about RAISING it, which latches breach auto-heal; not this direction.)

## Protocol
`scripts/run_paired_experiment.py --instance v2-conv-trt --doc 195 --start 2026-04-01
--end 2026-06-01 --cash 6000 --granularity 3600 --treatment max_positions=4`
(clear → attest cold → run, per arm; VOID unless comparable). Doc restored to
`max_positions=6` immediately after the pair.

## Endpoints (in order; the primary decides)
1. **PRIMARY (mechanism):** per-name satellite entry weight rises materially — median entry
   notional as % of NAV up ≥25% relative vs control. If weight does not rise, the lever failed
   its own mechanism regardless of return, and is REJECTED.
2. **SECONDARY:** return delta vs 0.5pp cold floor (`assess_pair(..., cold_start=True)`).
3. **GUARDS (any breach = not adopted):**
   - SQQQ BUY notional not reduced (bear leg is the one validated edge).
   - Turnover not up >10% relative (turnover is the known leak).
   - No breach auto-heal / forced-liquidation lines in the treatment log.

## Decision rule
Adopt iff PRIMARY fires AND no guard breached AND secondary ≥ −0.5pp. Anything else: reject and
record what the mechanism actually did.

## Amendment (2026-08-21, before the second pair)
Second window = **c (2026-02-01..2026-04-01, chop→bear)**, not f, chosen BEFORE launch: the SQQQ
guard is vacuous in window d (no bear deploy); window c is where the one validated edge lives and
where a lower base cap could plausibly constrain the bear book. It also satisfies the objective's
"leadership not semiconductors" requirement. Window f runs third if time permits.

## Result window d (bt 760962 control / bt 978281 treatment)
- START: IDENTICAL_COLD (both attested cold=True). Overlap 62% (READABLE — book-size change is
  the mechanism).
- **PRIMARY: FIRED.** Single-name entry notional median $629 (10.5% NAV) → $810-839 (~14% NAV),
  **+33% relative** (bar was ≥25%). Trades 12 → 8; treatment book held ≤4 satellites.
- GUARDS: no auto-heal/forced-liquidation lines; buy notional ≈ equal ($6.68k vs $6.67k) with
  fewer trades → turnover not up; SQQQ untouched in both (vacuous in d).
- SECONDARY: control +10.16% (replicates cold bt 479057 +10.15%), treatment **+11.05%**,
  delta **+0.89pp** (≥ −0.5pp required; above the 0.5pp cold floor, though book divergence means
  the floor is a lower bound on noise here).
- Treatment entered DELL (+190% mover over the window) at 13.5% NAV and kept it: the objective's
  stated mechanism (fewer, larger) working as designed in one window.
- Window d verdict: adoption criteria met, PENDING window c.

## Result window c (bt 712452 control / bt 808739 treatment)
- START: IDENTICAL_COLD. Overlap 78%.
- PRIMARY: **mostly vacuous here** — entry notionals barely move (mean $744 → $797, +7%): the
  bear phase liquidates the book, so the cap seldom binds (the gate log still shows cap=4 on
  163 evaluations, held ≤4 throughout — the cap is LIVE, just rarely the binding constraint).
- **GUARD (the reason this window was chosen): SQQQ BUY notional $3,302 → $3,358 — NOT
  reduced.** The bear edge is untouched by the lower cap. The 3 "auto-heal freed 0 / blocking
  new-ticker buys" lines appear identically in BOTH arms (bear-cap transition, not
  treatment-induced). 0 refusal lines either arm.
- SECONDARY: control +2.97%, treatment **+4.11%**, delta **+1.14pp**.
- Window c verdict: no guard breach, positive delta, mechanism inert-but-armed. Safe in the
  bear-edge window.

## Result window f (bt 209809 control / bt 904667 treatment)
- START: IDENTICAL_COLD — yet **BOOK OVERLAP 27% → VOID.** A cold start does NOT guarantee
  comparable books in chop: the cap change perturbs early buys, discovery cascades down a
  different path (control drew CPER/EEM/COPA/BLK; treatment COPJ/COPX/CSCO/GCMG). All
  divergence is caused by the lever (cold A/A is byte-identical), but the return delta mixes
  the lever's effect with which names the perturbed discovery drew — unattributable, and the
  validity gate correctly refuses it. Nominal returns (control −1.22%, treatment −3.77%) are
  NOT quotable as a delta per protocol.
- Mechanism: entry notional mean $700 → $803 (+15%, below the 25% bar). Guards: auto-heal
  lines 2 = 2 (equal), no SQQQ deploy either arm (3 bear bars — consistent with prior finding).

## FINAL VERDICT: NOT ADOPTED (promising; blocked on chop measurability)
The prereg rule requires secondary ≥ −0.5pp across the tested windows; window f is VOID, so the
rule cannot be satisfied. Adopting on windows d+c while the chop window is unreadable — with a
nominally worse chop outcome — would be adopting on the surviving half of a split endpoint,
the exact anti-pattern this project preregisters against. Chop is also the strategy's one known
losing regime.

What IS established:
- In bull (window d) the mechanism does exactly what the objective asks: fewer, larger
  positions (+33% entry weight), caught DELL (+190%) at 13.5% NAV, +0.89pp.
- In the bear-edge window (c) it is safe: SQQQ BUY notional untouched, +1.14pp.
- In chop (f) its return effect is UNMEASURABLE by cold backtest — structurally, not by bad luck.

Disposition: `max_positions` stays 6 on doc 195. **max_positions=4 is the first candidate for a
paper-era A/B** — forward paper is the only instrument that can read its chop behaviour.
