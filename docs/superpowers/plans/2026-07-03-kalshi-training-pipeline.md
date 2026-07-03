# SP1 Learning-Loop Backbone — Implementation Plan

**Goal:** Wire a continuous, self-supervised isotonic-calibration loop onto the
existing physical model: settled outcomes → refit → registry champion → engine
applies it to fair value. Degrade-safe, never ships a worse model.

**Tech:** Python 3.14, RethinkDB, pytest. Reuse `calibration.py` (isotonic/shrink)
and the PR#84 reconnect/self-heal pattern.

## Global Constraints
- Degrade-safe: any failure → identity calibrator, never crash engine/worker.
- Promotion gated on held-out log-loss not regressing (never ship worse).
- No look-ahead: worker only ever sees already-settled games; train/test split by fixture.
- Calibration is monotone in [0,1]; renormalize the winner group after applying.

## Tasks

### Task 1: `kalshi/training.py` — pure sample/fit/eval core
- `gather_samples_from_settled(fixtures, model_fn)` → `[(pred, 0/1)]`, one per side of each settled fixture.
- `gather_samples_from_decisions(rows)` → `[(pred,0/1)]` from placed+settled decisions (fused_fair, outcome).
- `fit_calibrator(samples, *, min_total=100)` → registry doc `{method, calibrator|null, shrink_strength, n_samples}` via `calibration.calibrate`/`fit_isotonic`.
- `evaluate(samples, doc)` → `{raw_logloss, cal_logloss, raw_brier, cal_brier, n_eval}` (applies doc's calibrator).
- `apply(doc, p)` → calibrated prob (isotonic interp or shrink or identity).
- Tests: `tests/test_kalshi_training.py` — isotonic vs shrink selection, evaluate math, apply monotonic, empty/thin degrade to identity.

### Task 2: `kalshi/db.py` — registry table + writers
- Add `("KalshiModelRegistry", "id")` to `KALSHI_TABLES`.
- `save_model_version(conn, doc)`, `get_champion(conn, instance_id, kind="calibrator")` (instance → `__default__` fallback), `set_champion(conn, id, instance_id, kind)` (clears prior champion for that scope+kind).
- Tests: `tests/test_kalshi_model_registry.py` with a fake conn — save, champion get/set, fallback.

### Task 3: `kalshi/training_worker.py` — background refit loop
- `refit_once(conn, instance_id, *, provider, model_fn, min_total, promote=True)`: gather settled → split train/test by fixture → fit(train) → evaluate(test) → save version → promote iff `cal_logloss <= raw_logloss`. Returns the doc. Pure-ish (provider/model_fn injected).
- `start_worker(conn_factory, ...)`: self-heal loop (mirror `backtest_worker`): boot refit + periodic + `kalshi_decisions` settlement changefeed; reconnect on drop; notify on promotion via `notifications.notify` (best-effort).
- Tests: `tests/test_kalshi_training_worker.py` — `refit_once` promotes only on improvement (fake provider/model_fn/conn); worse calibrator rejected.

### Task 4: engine + orchestrator integration
- `orchestrator.plan_and_allocate`: new `calibrator: dict|None=None` kwarg; after market-anchor block, if calibrator, apply to each `fused["winner"][side]` then `renormalize_group`. None → no-op.
- `engine.run_instance`: load champion calibrator into `_calibrator` on boot + every 60 ticks (degrade to None on failure); pass to `plan_and_allocate`.
- `EngineConfig`: none needed (registry read by instance_id). Runner: start the training worker thread alongside the engine.
- Tests: orchestrator applies calibrator (fair shifts toward calibrated, renormalized); None = identical to before.

### Task 5: API endpoint
- `GET /brokerages/{bid}/kalshi/instances/{id}/model` → champion doc + reliability buckets (predicted vs actual from recent settled samples). Degrade to `{champion:null}`.
- Test: endpoint shape with a fake champion.

### Task 6: startup wiring + smoke
- Runner starts the training worker (daemon) using the same conn_factory pattern.
- Full backend test sweep green; `refit_once` smoke on the real cached settled WC games shows calibrated ≤ raw log-loss.
