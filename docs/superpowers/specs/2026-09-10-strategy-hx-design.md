# Strategy HX — fast-in, slow-out bear hedge on a volatility-targeted core

Date: 2026-09-10. Status: approved design, pre-registered gate.

## Why this exists

The user's standing bar is: beat SPY in every regime (bear, bull, chop), make
money in every bear window, and beat SPY by as much as possible. Two of the
three canonical bear windows are two months long (SPY −5.8% and −11.4%). No
slow trend filter can be positive inside a two-month bear, which is why the
Strategy EB inverse-ETF line, gated on a 25-session weekly filter, has not
passed. HX exists to test the one construction that can mechanically react in
days: a fast entry into a bear book, and a slow, hysteresis-gated exit.

A 34-claim audit of published "unorthodox" edges (2026-09-10) found nothing
retail-viable that the engine can express, so HX contains no exotic signal.
It is a risk transform built from the only construction that survived prior
tests on this engine: a volatility-targeted leveraged index core.

## Gate (frozen before the first run)

All verdicts come from the API engine, one job at a time, daily stepping,
`etf-liquid` cost preset, starting cash equal to prior EB runs. Local harness
numbers never count.

1. Each canonical bear window ends with positive P&L:
   rb1 2022-01-01→2022-06-30 (SPY −19.40%), rb2 2026-02-01→2026-04-01
   (SPY −5.84%), rb3 2025-02-15→2025-04-15 (SPY −11.40%).
2. Beats SPY total return in every canonical window listed in
   `scripts/outlier_engine_test.py:33-46` (bear, bull, chop) and over the
   cycle 2021-11-01→2026-08-27 (SPY +77.11%).
3. Max drawdown in each window no worse than SPY's in that window.
4. Any run reaching 25% drawdown is stopped and rejected.

Order of runs: rb3, rb2, rb1 (bear first, cheapest kill), then chop
(rc1, rc2, rc3), then bull (ru1, ru3), then cycle. A failed bear window ends
the variant; the next preregistered bear book is tried. If every variant
fails, HX is killed and the fallback (below) is built.

## Signal

State machine over QQQ daily closes, evaluated every session. Three states.

- BULL → BEAR (fast). Either: QQQ close is below the minimum close of the
  prior 20 sessions AND below its 50-session simple moving average; or QQQ has
  fallen at least 7% from its highest close of the prior 20 sessions within the
  last 10 sessions.
- BEAR → BULL (slow). QQQ close above its 50-session average for 5
  consecutive sessions. The counter resets on any close below.
- CHOP. Not BEAR, and either QQQ close is below its 50-session average, or the
  50-session average has moved less than 1.5% over the last 20 sessions.
- BULL. Everything else.

All thresholds are config keys with the defaults above.

## Books

- BULL: core weight from `eb_core_weight` in `backend/strategy_eb.py`
  (TQQQ, target vol 0.20, cap 0.65, unchanged), remainder in QQQ.
- CHOP: core weight × `chop_core_damp` (0.5), remainder in BIL.
- BEAR: `bear_book`, a dict of symbol → fraction of NAV. Default
  `{"PSQ": 0.60, "BIL": 0.40}`.

Preregistered bear-book variants, run in this order, each a config change
only:

| variant | bear_book | engine cost tier |
|---|---|---|
| V1 | PSQ 0.60 / BIL 0.40 | PSQ 23.2 bps |
| V2 | SQQQ 0.25 / BIL 0.75 | SQQQ 4.4 bps |
| V3 | SH 0.60 / BIL 0.40 | SH 23.2 bps |
| V4 | GLD 0.30 / BIL 0.70 | GLD 4.4 bps |
| V5 | PSQ 0.40 / GLD 0.20 / BIL 0.40 | mixed |

The engine's cheap-ETF list (`ETF_LIQUID_SYMBOLS`) is not widened for PSQ or
SH. Their 23.2 bps tier is conservative and stays.

## Orders

Targets are converted with `targets_to_orders` from `backend/strategy_x.py`,
so HX only sells what it owns and returns `{symbol: 1|0|-1}` with
`_nexus_position_sizes` and `_nexus_action_intents`. A state change rebalances
the full book immediately. Within a state, weights are banded
(`rebalance_band`, 0.10 of NAV) so the book trades rarely.

## Failure handling

- Fewer than `min_history_bars` (70) QQQ closes: state UNKNOWN, book is 100%
  BIL. Never more leverage on unknown.
- Missing QQQ bars this session: state unchanged, log once, no orders.
- NaN or non-positive close: treated as missing.
- Missing price for a leg: last visible close; if none, the leg is skipped and
  its weight goes to BIL.

## Files

New:
- `backend/strategy_hx.py` — DEFAULTS, `hx_state`, `hx_targets`,
  `strategy_hx_universe`. Pure functions, no I/O.
- `backend/strategies/strategy_hx.py` — `# INTELLISTOCK_SCHEMA` header line 1,
  `StrategyHx.run_once`, cache state, order emission.
- `scripts/strategy_hx_sync_schema.py` — header ↔ DEFAULTS.
- `scripts/hx_lab_setup.py` — creates the HX strategy doc and the
  `strategy-hx-lab` instance. Docs 200 and 201 are never touched.
- `scripts/hx_run_native_api.py` — sequential API controller: refuses if any
  job is running, posts one window, polls, stops at 25% drawdown, archives
  summary/logs/graph under `output/research/hx-<date>/`.
- `backend/tests/test_strategy_hx.py`, `test_strategy_hx_run_once.py`,
  `test_strategy_hx_broker_wiring.py`.

Touched:
- `backend/broker.py` — one added branch in the universe fetch loop
  (`broker.py:10449-10471`) for `_strategy_hx_universe_symbols`. Impact
  analysis before the edit.
- `scripts/check_deployed_code.py` — add both HX files to `FILES`.

## Testing

Tests are written before code. Unit: fast trigger fires within the crash
window on a synthetic series; a 4-session rally does not exit BEAR, a
5-session one does; chop damp halves the core; short history yields BIL only;
universe lists exactly the configured legs; the class resolves as
`StrategyHx`. Wiring: the broker fetch loop includes HX legs. Review: ECC
python-reviewer, silent-failure-hunter, security-reviewer, code-reviewer
before push. Deploy: push to main outside the 08:30 CDT ±30 min window and
with no job running; wait; `scripts/check_deployed_code.py` exits 0 before
the first backtest.

## Fallback

If every HX variant fails the bear gate, build a three-state regime classifier
with one fixed book per state (bull: vol-targeted core; bear: inverse book;
chop: damped core plus BIL) using slow moving-average rules only, and run the
same battery. Recorded here so the second attempt is pre-registered too.

## Out of scope

Live adoption. Options. Borrow. Changes to Strategy EB, docs 200/201, the
engine cost model, or the outlier sleeve.
