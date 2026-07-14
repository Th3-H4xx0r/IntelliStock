# MeanRev crash-bear gate (`bear_gate_ma`) — 2026-07-14

## Problem

The shipped MeanRev (RSI dip-buy + per-coin SMA200h regime filter + vol
sizing) is positive in every mild bear (2024 chop, 2025-26 OOS/tgt/fullrec)
but loses ~-19% in a 2022-class crash-bear: the per-coin regime filter still
admits dip-buys during violent bear-market rallies, and those accumulate
losses all the way down.

An exhaustive autonomous search (2026-07-13/14, ~15 strategy classes, ~100
configs, plus a literature sweep: vol-targeting, TIPP/CPPI, drawdown-scaled
exposure, cross-sectional momentum, slow TSMOM 20d/100d, 4/8/12-week composite
momentum, MR+sleeve blends) re-confirmed the asymmetric bull objective (beat
B&H in every bull) is unreachable long-only, and that the ONLY reachable
improvement over the incumbent's 4/9 regime wins is making the 2022 bear
positive **without** losing the four mild-bear wins.

## Design

`bear_gate_ma` (int, default **0 = off**; recommended 1200 = 50 days of
hourly bars):

- Build the equal-weight basket of the visible universe: mean over coins of
  close normalized at a shared trailing anchor (last `bear_gate_ma + 100`
  bars).
- While `basket < mean(basket[-bear_gate_ma:])` (expanding-capped, no
  lookahead): **block NEW entries only**. Exits (`rsi_exit` recovery) and
  holds are untouched.
- **Fail-open**: with fewer than `max(600, bear_gate_ma // 2)` shared bars,
  or any degenerate input, the gate is off. Live short-data windows can never
  block trading; `_bear_gate_blocked` never raises.
- Stateless (no hysteresis) — no strategy_cache dependency; hysteresis 0-0.05
  scored identically in the sweep.

Also added `sizing`, `atr_period`, `bear_gate_ma` to broker
`_CRYPTO_STRATEGY_TUNABLES` so instances can set them via `crypto_config`
(closes the 2026-07-13 handoff follow-up; enables clean prod A/B).

## Validation (faithful: real Meanrev through real PortfolioEmulator, 0.02%)

| window | B&H | base | gated |
|---|---|---|---|
| 2021bull | +190.0 | +64.78 | +48.39 |
| **2022bear** | −67.1 | −19.30 | **+11.23** |
| 2023recov | +57.8 | +17.75 | +17.22 |
| 2324bull | +119.1 | +30.64 | +23.21 |
| 2024chop | −35.2 | +1.67 | **+6.52** |
| late24 | +74.3 | −13.33 | −11.15 |
| OOS | −36.4 | +8.84 | **+11.75** |
| tgt | −20.8 | +13.40 | +1.14 |
| fullrec | −50.0 | +25.79 | +13.03 |

Wins (bull: ≥B&H; bear: >0): base **4/9** → gated **5/9**; all 5 bears
positive — first config ever to survive the 2022 crash-bear faithfully.
Robust plateau: gate windows 1200–1680h and hysteresis 0–0.05 all score 5/9
in the fast harness (not a parameter spike).

## The honest trade-off

The gate is crash insurance, paid for out of mild-bear scalp profit: tgt
(the 2026 Q2/Q3 live quarter) drops +13.4 → +1.1 and fullrec +25.8 → +13.0,
because those profits come from dip-buys inside the below-50dMA zone the gate
suppresses. Depth- and slope-conditioned variants that tried to keep both
were knife-edge overfits (2022 win flips sign at neighbor parameters) and
were rejected. Mean across the 9 intervals: 14.5 → 13.5 (−1pp) for a worst
bear of +6.5 instead of −19.3.

**Hence default OFF.** Enabling it is a per-instance risk-appetite decision
(`crypto_config: {"bear_gate_ma": 1200}`).

## Verification tooling

- `scripts/verify_meanrev_bear_gate.py` — reproduces the table above through
  the shipped config path (`bear_gate_ma` in config, 1400-bar data windows).
- `backend/tests/test_crypto_meanrev_bear_gate.py` — 7 unit tests with
  self-validating fixtures (gate predicate asserted before behavior).

## Gotchas for future work

- Per-interval indicator warmup flatters backtests: slow signals NaN'd during
  the Jan-2022 crash made an MR+XS blend look 5/9 when it was honestly 1/9.
  Compute indicators full-range, evaluate intervals as sub-ranges.
- Backtest path feeds strategies full history (fetch starts 90 calendar days
  before backtest start → ~2160 hourly bars of warmup: enough for the gate).
- **Pre-existing live gap found while tracing (NOT introduced here):** the
  live main-loop dispatch (`broker.py` ~8198) passes `data=None` to ALL
  run_once strategies. With no bars, crypto strategies see an empty universe
  and `core.exit_blind_held` would risk-off-sell every held coin each tick.
  Live crypto validation is still an open item on PR #114 — whoever wires
  live crypto bars must feed windows ≥ regime_ma+16 (and ≥ bear_gate_ma+100
  for the gate; it fails open below that, never blocks).
