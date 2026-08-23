# Strategy X — leveraged core with a de-lever filter

**Status:** implemented (`backend/strategy_x.py`, `backend/strategies/strategy_x.py`).
**Date:** 2026-08-23
**Supersedes:** the "Regime Council" design of the same date, killed by the
pre-registered study in §3. That version is not in git; §3 records what it was
and why it died, so it is not re-proposed.

---

## 1. What it does

One position at a time.

| condition | holding |
|---|---|
| QQQ above its 200-day MA **and** 20-day realised vol below 1.2x its median | TQQQ at `core_weight` (0.90), remainder SPY |
| anything else | SPY |

The filter reads QQQ. The traded leg is TQQQ. Those are deliberately different
symbols: the signal comes from the unlevered index, the exposure from the
levered one.

**It does not predict direction.** It decides only whether to be levered. That
distinction is the whole design, and §3 is why.

## 2. Measured behaviour

Numbers below come from **replaying this module bar by bar**
(`scripts/_strategy_x_replay.py`) over 15.7 years of real closes: next-bar
fills, point-in-time filtering, NY session grouping, 2 bps one-way. That is the
honest figure. A vectorised study of the same rule
(`scripts/_strategy_x_final.py`) reports ~5pp higher because it fills at the
close it just observed — prefer the replay.

| config | CAGR | max DD | Sharpe | yrs >=100% |
|---|---|---|---|---|
| **shipped default** | **33.97** | **-48.50** | **0.88** | 4 |
| tight vol gate (1.2) | 28.87 | -45.68 | 0.87 | 2 |
| vol gate off | 31.66 | -71.08 | 0.83 | 3 |
| **bear leg ON (SQQQ)** | **-4.15** | **-88.17** | 0.20 | 1 |
| satellite 20% ON | 29.95 | -32.74 | 1.00 | 1 |
| chop = cash not SPY | 26.78 | -42.27 | 0.89 | 1 |
| TQQQ buy & hold | 40.55 | -81.66 | 0.87 | 5 |
| SPY buy & hold | 14.58 | -33.72 | 0.89 | 0 |

Final multiple **99.6x** against SPY's 8.5x, at 4.8 leg changes a year.

Both tuned parameters sit on **plateaus, not peaks** — chosen as the middle of a
flat region rather than its maximum:

- filter length: MA150/200/250 → 43.2 / 37.9 / 34.9 CAGR (vectorised), 200 taken.
- vol gate: 2.00 / 2.25 / 2.50 → 33.4 / 34.0 / 34.8 CAGR at ~-48.5% DD, with 3.00
  falling to -63.4% DD. 2.25 taken.

The vol gate must be **loose**. It exists to refuse leverage in a disordered
tape, not to trade ordinary chop: at 1.2 it fires 8.3 times a year, de-levers
into recoveries, and costs 5pp of CAGR for 3pp of drawdown. Tighten it only if
2022 matters more than the average year (-18.2% there at 1.2, -36.5% at 2.25).

## 3. What was killed, and by what

The original design scored five voters into a signed conviction that chose
between TQQQ and SQQQ. Before writing it, `scripts/_voter_hitrate_study.py`
measured every voter against **non-overlapping** 5-day forward QQQ returns, with
the threshold pre-registered at p >= 0.58:

```
fraction of 5d windows that are UP    0.6045   <- the real bar
above MA200                           0.5827   [0.544, 0.620]
trend (repo's own regime rule)        0.5560   [0.512, 0.599]
news_breadth (n=84)                   0.6071   [0.500, 0.705]
vol                                   0.4904   [0.439, 0.542]
macro_llm                             0.4762   [0.373, 0.582]
```

**Not one voter beats always voting "up".** Beating buy-and-hold TQQQ requires
accuracy above the up-fraction — 60.45%, not 50% — because every wrong call
costs a levered round trip. The repo's own 45-episode audit of its leveraged
short measured 40%.

Three independent lines of evidence agreed, so this is not one bad study:

- **Arithmetic.** ~5 false flips converts a +100% year to 0%; the Nasdaq offers
  ~70 three-percent reversals a year.
- **Decay.** A 3x fund's drag is `-3*sigma^2` (and `-6*sigma^2` inverse). 2018
  validates it: NDX ~0%, TQQQ -20.8%, SQQQ -22%. In a flat year *both* legs lose
  ~20%.
- **Direct simulation.** The SQQQ leg loses at every filter length tested
  (MA50 -15.2, MA100 -16.1, MA150 +13.9, MA200 +6.3, MA250 +3.8, MA300 -6.6 CAGR)
  and makes drawdown worse, not better.

`backend/core_sleeve.py` had already reached the same verdict independently:
the inverse leg "needed SIX independent suppressors before it stopped losing
money".

## 4. The two levers that default OFF

`core_bear_symbol = ""` — the inverse leg. Configurable so it can be re-tested,
not because it is expected to work. Enabling it turned 99.8x into **0.52x**.

`satellite_pct = 0.0` — the graph/LLM-ranked stock sleeve. Costs 4.8pp of CAGR
and drops years-above-100% from 3 to 1. When enabled it consumes
`data["conviction_scores"]`, the same channel `index_core_tilt` uses, so the
graph and LLM pipeline can feed it without further plumbing.

## 5. Execution

The core is a normal run_once position, **not** a sleeve leg. That was a
deliberate reversal: a three-occupant sleeve was costed at 20 edit sites across
4 files, of which 4 are structural rewrites — `_residual_sleeve_release` hard-
returns on an occupant that is not `residual_sleeve_symbol`, so the book could
enter a position it can never exit.

The cost of staying in the allocator lane is `BROKER_MAX_SINGLE_POSITION_PCT`
(env, default 0.15), which clips every buy to 15% of equity in backtest and live
alike. `index_core_tilt.py` documents hitting exactly this — it asked for $6,000
of SPY and got $900.

So `backend/engines/backtest_engine.py` now reads
`broker_max_single_position_pct` from the instance's own strategy document and
injects it into that run's container. The backtest engine is one shared service
process, so forwarding its own env would have been global; reading per-instance
keeps it scoped. An instance that does not declare the key keeps 0.15, so
`alpaca-main` is untouched.

**Live is not enabled by this.** `live_risk_state.DEFAULT_MAX_LEVERAGED_FRACTION`
caps TQQQ/SQQQ/SPXU/UPRO/SOXL/SOXS at 10% of equity on the live order path, and
`UnifiedOrderGate` **blocks** rather than clips, so an oversized deploy fills
zero. There is no env or config override — raising it is a source edit to a
real-money risk module. Live deployment is a separate, deliberate decision.

## 6. Point-in-time

`pit_daily_closes(bars, as_of)` is the boundary. It compares full timestamps,
not dates: at the 15m/1h cadence these backtests run, "today's daily bar" is
that session's 16:00 close, ~6h in the future of a 09:45 decision. Comparing on
date alone is the most common lookahead in this codebase.

This matters because the surrounding machinery does not protect us. Every
equities backtest defaults to `pit_mode="research"`, which builds
`PointInTimeContext(strict=False)` and calls `run_once` directly —
`run_historical` and its four snapshot loaders are never invoked. The broker's
own comment: *"That is real lookahead bias… stamped legacy_unverified."*

Strategy X reads **only** price bars, so it sidesteps the contaminated paths
(live Neo4j reads, `IN_SECTOR`/`COMPETES_WITH` edges backdated to 2000-01-01,
same-bar outcome backfill, present-day market caps). That narrowness is a
feature: it is the one input in this system whose point-in-time behaviour is
verified, and `test_future_bars_are_not_visible_to_the_filter` pins it with a
mutation-tested assertion.

## 7. Known measurement defects in the engine

These bias any in-engine backtest of this strategy and are **not** fixed here:

1. **The fee model is wrong for these instruments.** `simulated_execution.py`
   uses `spread_bps=45.6`, measured on 61 microcap fills. TQQQ/SPY/QQQ quote
   ~0.5-2 bps. There is one cost model for every symbol, so an in-engine
   whipsaw cost will read ~20-40x too high. The offline study uses 2 bps.
2. **TQQQ/SQQQ accrue no dividends.** `DEFAULT_EQUITY_DIVIDEND_YIELDS` seeds SPY
   at 1.25% and deliberately omits leveraged ETFs, saying "set them explicitly
   per run" — but the emulator factory never passes `dividend_yields=` and no
   caller in the repo does. So SPY is credited and TQQQ is not, biasing against
   exactly the risk-on state.
3. **LLM caches are cleared by nothing**, so an A/A byte-identity check proves
   cache replay, not reproducibility. The repo's own
   `test_backtest_determinism.py` records four replicates of one window at
   +15.80 / +7.05 / -0.67 / -0.92 percent — a 16.73pp spread. Strategy X does
   not call an LLM, so it is immune; the point is that in-engine numbers from
   LLM strategies are not comparable to these.

The offline study is therefore the *more* trustworthy evidence for this
strategy, and the in-engine backtest is a cross-check on plumbing — does it
trade, at the right size, in the right direction — not on edge.

## 8. Config

```
strategy_x_enabled              false    master switch
core_bull_symbol                TQQQ
core_chop_symbol                SPY      never cash: cash costs 8pp of CAGR
core_bear_symbol                ""       OFF - see §4
core_weight                     0.90
core_band_pct                   0.05     no order inside this drift band
core_filter_symbol              QQQ      the tape read, not the leg traded
core_filter_ma_bars             200
core_vol_bars                   20
core_vol_gate_mult              2.25     0 disables; keep it LOOSE
core_once_per_session           true     the engine ticks ~60x/session
core_vol_median_bars            252
core_vol_median_min_samples     60
satellite_pct                   0.0      OFF - see §4
satellite_max_names             6
min_order_usd                   50.0
cost_haircut_pct                0.006
broker_max_single_position_pct  0.95     read by the backtest engine, §5
```

The instance's ticker universe must contain **QQQ, TQQQ and SPY** — QQQ for the
filter, the other two to trade. `price_history` is built only for instance
tickers, and `prices` only for `symbols`, so a missing entry silently yields no
orders. Both cases now log RED.

**Granularity must be 86400.** The broker's warmup is `700 * increment` clamped
to a 90-day floor, so at any sub-daily granularity the run gets ~65 daily closes
and the 200-day MA never forms — the strategy then sits 100% in the chop
occupant for the entire window and reads as "no edge" when it simply never ran.
That false negative now logs RED too, but daily granularity is the fix.

## 9. Validation plan

The offline study is the primary evidence. The in-engine backtests answer a
different, narrower question: does the wiring work?

1. **Plumbing** — one window, confirm it buys TQQQ at ~90% of NAV (proving the
   position cap injection works), logs the filter decision every bar, and
   rotates to SPY on a downtrend.
2. **Regime windows** — bull, bear, chop, and both transitions, each cold.
3. **Controls** — `strategy_x_enabled=false`; `core_weight=0` (SPY only);
   `core_bear_symbol=SQQQ` (expected to be much worse — it is a falsification
   check on the offline result, run in the engine to confirm the two agree).
4. **The primary control is buy-and-hold TQQQ over the same window.** If the
   filter cannot beat holding, it is not earning its complexity.

## 10. Open risks

1. **The goal as stated is a risk level, not a strategy.** ~100%/yr from the
   Nasdaq means holding 3x through a year where NDX returns >=28%, which
   happened 4 times in 12 years. The same distribution carries 2022: TQQQ
   -79.7%. This design accepts -38% drawdowns to keep most of the upside; it
   does not make +100% repeatable, and nothing does.
2. **Regime dependence.** The whole sample is one secular Nasdaq bull. The
   filter helps in 2022 and hurts in 2011 and 2016 (whipsaw years). A decade
   like 2000-2010 is not in the sample and would look very different.
3. **Live exposure caps** (§5) mean live behaviour is not the backtested
   behaviour until they are addressed.
4. **The engine's fee and dividend defects** (§7) mean in-engine returns are not
   directly comparable to the offline study.
