# Strategy EB ("Efficient Beta") — design

Date: 2026-08-27. Research basis:
`docs/superpowers/research/2026-08-27-all-regime-research.md`.
Operator decisions: direction C (efficient-beta core with a BIL dial); raise
the live leveraged-ETF cap to the strategy's max weight; go live on a $6k
Alpaca account; Nexus only if evidence supports it (it does not — dropped).

## 1. What it is

A vol-targeted leveraged Nasdaq core with the remainder in SPY, rebalanced
once a week on a fixed weekday, with every weight quantized and banded so it
trades rarely. One lever, `remainder_bil_fraction`, moves the de-levered
remainder from SPY toward T-bills, trading CAGR for drawdown.

It is a **risk transform, not an alpha**. Nine pre-registered signal tests
found no alpha in this universe; the one construction that survived —
leverage efficiency — adds ~+2.6pp CAGR at matched drawdown over a static
levered blend (49/52 configs, same sign in both halves of 2010-2026). The
strategy makes no directional prediction. It holds *less of the same
position when it is more dangerous*.

What it deliberately does NOT contain, each with measured evidence:
no MA200 gate (below the always-long base rate), no inverse leg (bottom
detector), no binary drawdown halt, no slow/fast trend gate (a wash:
−0.4pp CAGR, +99%/yr turnover), no commodity/managed-futures/gold sleeve,
no Nexus sleeve (untestable in any bear; zero measured signal).

## 2. Signal and sizing (pure, `backend/strategy_eb.py`)

Inputs: point-in-time daily closes of the reference index (`QQQ`) from
`strategy_x.pit_daily_observations(bars, as_of)` — strictly-earlier NY
sessions only.

```
rv      = max( stdev(ret, 20) , stdev(ret, 60) ) * sqrt(252)      # QQQ
w_raw   = target_vol / (leverage * rv)
w       = floor( clamp(w_raw, 0, core_max_weight) / step ) * step
```

- `target_vol` 0.20, `leverage` 3.0 (TQQQ) or 2.0 (QLD), `core_max_weight`
  0.65, `step` 0.05. Flooring means quantization only ever holds less.
- Fail closed: fewer than `min_history_bars` (70) observations, a non-finite
  vol, or `rv <= 0` → the strategy returns `{}` and logs red. A cold start
  must never silently lever up.

Targets (weights of NAV, `Q=6` decimals):

```
core   = w
bil    = (1 - w) * remainder_bil_fraction
spy    = 1 - core - bil
```

`remainder_bil_fraction` default 0.0 (approach A). Setting it to 1.0 is
approach B. Any value in between is the dial.

## 3. Cadence, band, tranches

- **Decision day**: the strategy computes targets every session but only
  *trades* on sessions whose NY weekday is in `rebalance_weekdays`
  (default `[2]`, Wednesday). Weekday comes from the observation's session
  date via `_session_ordinal` (days since 1970), never from a call count.
  `strategy_cache["_eb_last_rebalance_session"]` prevents a second trade
  on the same session at intraday granularity.
- **Band**: trade only if `|w - w_held| >= core_rebalance_band` (0.10)
  where `w_held` is the core's current weight from positions × price / NAV.
  SPY/BIL follow whenever the core trades. An exit to `w = 0` is always
  executed (unconditional exits, as in `targets_to_orders`).
- **Tranches** (`rebalance_weekdays` with more than one entry, e.g.
  `[1, 3]`): on each listed weekday move the core `1/len` of the way
  from held to target. Default is a single tranche; the option exists
  because rebalance-timing luck is >100 bp/yr and 1/N tranching removes
  it. Not enabled by default — it doubles order count on a $6k account.

Expected turnover ≈ 250-300%/yr one-way (measured 207-299%/yr for weekly
configs in the local sweep).

## 4. Orders (wrapper, `backend/strategies/strategy_eb.py`, class `StrategyEb`)

- `# INTELLISTOCK_SCHEMA:` header on line 1 (`execution_scope: run_once`,
  `decision_phase: pre`, `execution_position: 10`, `config: DEFAULTS`),
  `# INTELLISTOCK_DESCRIPTION:` on line 2, synced by
  `scripts/strategy_eb_sync_schema.py` (copy of the X variant, which also
  re-injects `broker_max_single_position_pct` and
  `honour_single_position_cap`).
- Logging through `intellistock_logger` (the XS wrapper used
  `utils.log_message` and its lines never reached the sink).
- `run_once`: return `{}` unless `strategy_eb_enabled` and a
  `portfolio_emulator` exist; build bars for `QQQ`, `core`, `SPY`, `BIL`
  from `data` (list or `{"bars": [...]}` shapes); patch `prices` for
  declared legs from the last visible close; compute `w`; apply cadence
  and band; call `strategy_x.targets_to_orders(targets, nav=, positions=,
  prices=, cash=, config=cfg, owned=set(universe))`; emit
  `_nexus_position_sizes`, `_nexus_discovered`, `_nexus_executable_buys`,
  `_nexus_sell_enforcement` (only symbols this strategy owns),
  `_nexus_action_intents = {sym: "etf_sell"}`.
- Own `_f/_i/_s` parsers (strategy_x's fail open on `inf` and resolve
  missing defaults against *its* DEFAULTS).

## 5. Broker wiring (`backend/broker.py`)

1. `_strategy_eb_universe_symbols(cached_strategies)` beside the XS one
   (`:4348-4376`): match `{"strategy_eb","strategyeb"}`, require
   `strategy_eb_enabled`, return `strategy_eb_universe(cfg)` =
   `[QQQ, core, SPY, BIL]` de-duplicated.
2. **Fetch site** (`:10173-10186`): third loop appending EB symbols to
   `symbols_for_fetch`.
3. **Price site** (`_strategy_x_prepare`, `:4402-4409`): union EB symbols
   into `_declared` inside its **own** try/except.
4. **Live daily-bar carrier** — new module `backend/live_equity_bars.py`,
   `build_live_equity_data(symbols, api_key, api_secret, db_conn, feed,
   lookback_days=400) -> {SYM: [bar dicts]}` via
   `fetch_alpaca_historical_bars(..., timeframe="1Day")`, keeping the
   last-good result per symbol (never blind-exit on a fetch failure —
   return the previous bars, log yellow). Hooked in the live equity branch
   next to `_rr_data` (`:14121-14181`) when any strategy_eb lane is
   enabled; today `data` is `None` live and both X and XS refuse to trade.
5. Tests AST-extract the two functions and count
   `_strategy_eb_universe_symbols(` ≥ 3 in the source, mirroring
   `test_strategy_xs_broker_wiring.py`.

## 6. Engine: symbol-tiered execution cost model

The engine charges a flat 23.2 bps one-way on every symbol
(`simulated_execution.py:116-121`, notional-weighted spread of small-cap
Nexus fills) and `equity_total_cost_bps` can only stress *up* (25/50).
Strategy X was mis-measured by ~20pp because of this. To measure an ETF
book honestly:

- `TieredExecutionCostModel(default, tiers: {frozenset[str]: model},
  version)` in `simulated_execution.py` with `model_for(symbol)`; a single
  composite `version` string (e.g. `equity-tiered-v1[etf-liquid]`) is
  stamped on every fill so `assert_execution_provenance_promotable`'s
  all-fills-same-version rule holds.
- `NextEventExecutionSimulator` reads `self._model_for(order.symbol)` at
  the three bare `self.cost_model` reads; `PortfolioEmulator._equity_fill`
  gains `symbol=None`. With no tiers the object graph is unchanged →
  existing runs stay byte-identical.
- One preset, `etf-liquid`: `{SPY, QQQ, TQQQ, QLD, SQQQ, BIL, GLD, IWM}`
  at `spread_bps=8.0, slippage_bps=0.1, fee_bps=0.3` (4.4 bps one-way);
  everything else stays at the measured 23.2. **The 8 bps ETF spread is
  an assumption, not a measurement** — it is conservative for SPY/QQQ
  (~1 bp) and roughly right for TQQQ; the first live fills must be priced
  against SIP NBBO the way the original 61 were, and the preset updated.
- Selected by a new evidence option `equity_cost_tiers: "etf-liquid"`
  (allow-list + validator in `backtest_evidence_options.py`; request
  field in `api/main.py`; threaded to `create_backtest_emulator(...,
  cost_model_tiers=)` at `broker.py:10338-10349`). Absent → unchanged.

## 7. Live path

- **Risk caps per strategy document.** `live_risk_state` gains
  `RiskLimits(max_order_fraction, max_symbol_fraction,
  max_leveraged_fraction, soft, hard, kill)` read from the enabled
  `strategy_eb` lane's config keys `live_max_order_fraction`,
  `live_max_symbol_fraction`, `live_max_leveraged_fraction`,
  `live_soft_drawdown`, `live_hard_drawdown`, `live_kill_drawdown`;
  threaded into both `initialize_risk_state` and `evaluate_drawdown` (the
  latter re-derives fractions from module constants every refresh today,
  so an override in only one place is overwritten each tick). The gate
  keeps **blocking** (never clipping) — the cap is simply set to what the
  strategy asks for. EB defaults: order 0.70, symbol 0.70, leveraged
  0.70, soft/hard/kill 0.25/0.35/0.45 — a strategy designed to ride a
  −30% drawdown cannot live under a 5% buy-freeze. The module defaults
  for every other document are untouched.
- The inline leveraged-symbol set at `broker.py:9236-9241` is replaced by
  `live_risk_state.DEFAULT_LEVERAGED_SYMBOLS`, which gains `QLD`.
- Orders stay market-in-RTH via the existing `AlpacaAdapter` style hook.
  At $6k and ~300%/yr, ETF spread cost is ~$10-40/yr; passive live
  execution is out of scope for v1.

## 8. Single-position cap

`backtest_engine._instance_single_position_pct` honours
`broker_max_single_position_pct` only when `strategy_x_enabled` is truthy
(XS's 0.65 is inert today). Generalise to: honour it when the same entry
has `honour_single_position_cap: true` **or** `strategy_x_enabled`. EB sets
`broker_max_single_position_pct: 0.95` and `honour_single_position_cap:
true`. The env var is process-wide: the doc for EB must contain no other
enabled lane.

## 9. Configuration (DEFAULTS)

```
strategy_eb_enabled        false
core_symbol                "TQQQ"      core_leverage 3.0   reference_symbol "QQQ"
off_symbol                 "SPY"       cash_symbol "BIL"
target_vol                 0.20        core_max_weight 0.65   weight_step 0.05
vol_fast_bars              20          vol_slow_bars 60       min_history_bars 70
core_rebalance_band        0.10        rebalance_weekdays [2]
remainder_bil_fraction     0.0
core_band_pct              0.03        min_order_usd 25.0    cost_haircut_pct 0.005
broker_max_single_position_pct 0.95    honour_single_position_cap true
live_max_order_fraction 0.70  live_max_symbol_fraction 0.70  live_max_leveraged_fraction 0.70
live_soft_drawdown 0.25  live_hard_drawdown 0.35  live_kill_drawdown 0.45
```
Every key is read by something (checked against `_DEAD_STRATEGY_CONFIG_KEYS`).

## 10. Deployment objects

- Strategy document **"Strategy EB"** with a single lane `strategy_eb`
  (weight 1.0, position 10). No graph lane (so no `max_positions` gate, no
  LLM cost, no 210-second sessions).
- Instance **`strategy-eb`**, equities, granularity `86400`, stocks
  `[TQQQ, SPY, BIL, QQQ]`, initial cash $6,000, linked to the doc.
- Created through the API (`scripts/strategy_eb_bootstrap.py`, following
  `_sx_doc198_patch.py`: write, re-fetch, verify every key round-trips).
- Rollback: set `strategy_eb_enabled=false`; delete the doc and instance.

## 11. Pre-registered acceptance gate (frozen now, before any engine run)

Engine, `strategy-eb`, 2021-11-01 → 2026-08-27, daily, $6,000,
`equity_cost_tiers="etf-liquid"`, defaults above. SPY benchmark from the
run's own `pv` price series. **Ship enabled only if ALL hold:**

| # | condition | rationale |
|---|---|---|
| G1 | CAGR ≥ SPY CAGR + 4pp | "beat SPY widely"; local sweep predicts +8-10pp, discount by half |
| G2 | max drawdown ≤ 1.2 × SPY's (SPY ≈ −25.4% → ≤ −30.5%) | the operator's drawdown bar with the measured local→engine DD fidelity |
| G3 | 2022 calendar return ≥ SPY 2022 − 12pp (≥ ≈ −31%) | the reachable bear bound with a SPY remainder |
| G4 | rolling 12-month windows beat SPY in ≥ 60% | full-cycle consistency |
| G5 | one-way turnover ≤ 400%/yr | the cost thesis |
| G6 | zero `would_block_in_phase2` sells; zero orders blocked by the position cap | the two silent failures that burned XS |

Local harness (`scripts/strategy_eb_matrix.py`, yfinance 2010-2026, cost
4.4 bps on ETF legs, turnover printed) is used only to check the
implementation reproduces the research sweep (CAGR ~24%, maxDD ~−40%,
weekly turnover ~250-300%) before the engine run. It is never the verdict.

If the gate fails, the strategy ships disabled with the numbers recorded
in `DEFAULTS` comments, per the XS precedent. It is not re-tuned to pass.

## 12. Live go-live sequence (after the gate)

1. Deploy, `scripts/check_deployed_code.py` proves prod runs the commit.
2. Paper instance first for one full weekly cycle: confirm the live bar
   carrier populates, one rebalance executes at the expected weights, the
   gate blocks nothing, `LiveOrderWAL` shows the fills.
3. Price the first live fills against SIP NBBO; update the `etf-liquid`
   preset if the assumption is off by more than 2x.
4. Flip `alpaca-main`'s instance (or a new live instance) to the EB doc
   with `strategy_eb_enabled=true`. Nothing else on that document.

## 13. Testing

- Pure: `backend/tests/test_strategy_eb.py` — vol/weight math (floor
  quantization, clamp, fail-closed on short history and `inf`), target
  construction for `remainder_bil_fraction` ∈ {0, 0.5, 1}, band logic
  incl. unconditional exit, weekday/tranche cadence from session ids,
  DEFAULTS ↔ schema header equality, class name derived exactly as
  `strategies_meta._module_to_class_name`.
- Wrapper: `test_strategy_eb_run_once.py` with a fake emulator — emits
  every `_nexus_*` key, sizes present for every decision, refuses on empty
  bars, patches prices for declared legs, does not trade twice in one
  session, does not sell symbols outside `owned`.
- Wiring: `test_strategy_eb_broker_wiring.py` (AST-extract + count ≥ 3).
- Cost model: `test_tiered_cost_model.py` — `model_for` routing, composite
  version on every fill, byte-identical fills when no tiers, validator
  rejects unknown presets, `assert_execution_provenance_promotable`
  passes on a tiered run.
- Live: `test_live_equity_bars.py` (last-good fallback, empty on total
  failure), `test_live_risk_limits.py` (per-doc override survives
  `evaluate_drawdown` refresh; other docs keep module defaults; `QLD` in
  the leveraged set; inline set removed from broker source).
- Engine gate: `test_backtest_engine_single_position_cap.py` for
  `honour_single_position_cap`.
- Command: `PYTHONPATH=.:backend python3 -m pytest -q backend/tests/test_strategy_eb*.py backend/tests/test_tiered_cost_model.py backend/tests/test_live_equity_bars.py backend/tests/test_live_risk_limits.py`, then the full suite.

## 14. Known limits

- The engine window (2021-11 → 2026-08) contains one bear. G3 is one
  observation. 2018Q4 and 2020 exist only in the local harness.
- 2022 cannot be flat-to-positive with a SPY remainder; the dial is the
  only honest answer, and the operator chose the SPY default.
- The ETF cost tier is an assumption until live fills are measured.
- Expense ratio and financing of the 3x fund are inside its traded price;
  at 4-5% bill rates that headwind is ~4-5%/yr on the levered leg and is
  already in every number above.
