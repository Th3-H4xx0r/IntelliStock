# Kalshi Self-Improving Training Pipeline — Design

**Status:** SP1 (learning-loop backbone) specced for build; SP2/SP3 outlined.
**Date:** 2026-07-03

## Goal
Make the Kalshi soccer model *learn from its own settled results continuously*, so
its probabilities get better-calibrated over time and the sharp/model blend earns
its weight — instead of a frozen, hand-tuned model. Approach A: a versioned model
**registry** + a **training worker** that refits on a schedule and on each
settlement; the trading engine just loads the current champion + calibrator.

## Why now (environment facts, verified)
- `calibration.py` is a complete, tested isotonic + shrink calibrator, explicitly
  "data-blocked, made ready" — nothing wires it in. The engine applies **no**
  calibration today.
- Every settled Kalshi WC market yields a **free** final score (ground truth), so
  the loop trains on real data even in this simulated environment.
- External historical data (`soccerdata`) is **not installed** and the sim blocks
  real historical scrapes — so SP3 (feature enrichment) is built degrade-safe and
  validated only in a real deployment. SP1/SP2 do not depend on it.

## Decomposition (each ships working software)
- **SP1 — Learning-loop backbone (THIS SPEC):** registry + training worker + wire
  the isotonic calibrator onto the existing physical model + evaluation harness.
- **SP2 — Learned model + ensemble:** feature vector + learned classifier +
  ensemble blend + rank physical/learned/ensemble → auto-champion.
- **SP3 — External-data enrichment:** `soccerdata` → feature store (form, xG,
  lineups, player availability), degrade-safe.

---

# SP1 — Learning-loop backbone

## Components (each a focused, testable unit)

### 1. Model registry (`kalshi/db.py` + new table `KalshiModelRegistry`)
Versioned rows, pk `id`:
```
{ id, instance_id, created_at, kind: "calibrator",
  calibrator: [[pred, calibrated], ...] | null,   # isotonic breakpoints, or null=shrink-only
  shrink_strength: float,                          # fallback when data-thin
  n_samples: int, method: "isotonic"|"shrink",
  metrics: { raw_logloss, cal_logloss, raw_brier, cal_brier, n_eval },
  is_champion: bool }
```
Writers: `save_model_version(conn, doc)`, `get_champion(conn, instance_id, kind)`,
`set_champion(conn, id)`. Registry is **per-instance** (scoped by `instance_id`,
falling back to a global `"__default__"` champion when an instance has none).

### 2. Sample gathering + calibration fit (`kalshi/training.py`, pure where possible)
- `gather_samples(decisions_rows) -> list[(pred, outcome01)]`: from settled placed
  decisions (`decision=="placed"`, `outcome in {win,loss}`, `fused_fair` present)
  build `(fused_fair, 1.0 if outcome=="win" else 0.0)`.
- `gather_samples_from_settled(fixtures, model_fn) -> list[(pred, outcome01)]`: the
  RICHER signal — for every settled fixture (not just bet ones), one sample **per
  side** `(model_prob[side], 1 if side==result else 0)`. This is the primary
  training set (all games, all sides), no trading required.
- `fit_calibrator(samples, *, min_total=100) -> dict`: wraps `calibration.calibrate`
  logic → returns a registry-shaped doc (`calibrator` breakpoints or shrink).
- `evaluate(samples, calibrator) -> dict`: held-out raw-vs-calibrated logloss+brier.

### 3. Training worker (`kalshi/training_worker.py`)
Background loop (mirrors `backtest_worker` self-heal pattern from PR #84):
- On boot + every `train_refresh_secs` (default 3600) + on a `kalshi_decisions`
  settlement changefeed tick:
  1. pull settled fixtures (cached final scores) + build `model_fn`,
  2. `gather_samples_from_settled` → split train/test by fixture (out-of-sample),
  3. `fit_calibrator(train)`, `evaluate(test)`,
  4. persist a registry version; promote to champion **only if** calibrated test
     logloss ≤ raw test logloss (never ship a worse calibrator),
  5. log + notify on refit/promotion; reconnect+continue on DB drop.

### 4. Engine integration (`kalshi/engine.py` + `orchestrator.py`)
- On boot + every N ticks, load the champion calibrator from the registry into
  engine state (`_calibrator`), degrade to `None` (identity) on any failure.
- In `orchestrator.plan_and_allocate`, AFTER building `fused` and the market
  anchor, apply the calibrator to `fused["winner"]` per side
  (`calibration.apply_isotonic`), then renormalize the group. No calibrator →
  no-op. This is the ONLY behavioural change to live pricing, and it is
  monotone + bounded (safe).

### 5. API + minimal UI (`api/main.py`)
- `GET /brokerages/{bid}/kalshi/instances/{id}/model` → champion doc + metrics +
  reliability points (predicted-vs-actual buckets) for a small card. (UI card is
  nice-to-have; the endpoint is required so the loop is observable.)

## Data flow
settled Kalshi scores → `gather_samples_from_settled` → `fit_calibrator` →
registry (champion) → engine loads → `apply_isotonic` on fused fair → edge/trade →
settlement → (next refit).

## Error handling / safety (hard requirements)
- **Degrade-safe everywhere:** no samples / thin data → shrink or identity; any
  fit/DB/network failure → keep the last champion (or identity), never crash the
  engine or worker (reuse the reconnect + self-heal from PR #84).
- **Never ship a worse model:** promotion gated on held-out logloss not regressing.
- **Bounded effect:** calibration is monotone in [0,1]; renormalize after applying.
- **No look-ahead:** train/test split by fixture; the champion used live was fit on
  PRIOR settlements only (the worker only ever sees already-settled games).

## Testing
- `calibration.py` already unit-tested. New pure units get direct tests:
  `gather_samples*`, `fit_calibrator` (isotonic vs shrink selection), `evaluate`
  (logloss/brier), registry writers (fake conn), promotion gate (worse calibrator
  rejected), engine apply (calibrator changes fair monotonically; None = identity).
- Worker loop: self-heal + refit tested with a fake conn/changefeed (mirror
  `test_kalshi_backtest_worker`).

## Out of scope for SP1 (→ SP2/SP3)
Learned ML model, ensemble, feature vector beyond current model, `soccerdata`
ingestion, live shadow A/B. SP1 ships a continuously self-calibrating **physical**
model + the registry/worker/eval backbone they all plug into.
