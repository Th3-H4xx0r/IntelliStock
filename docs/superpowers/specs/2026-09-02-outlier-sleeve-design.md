# Outlier Sleeve — design

**Date:** 2026-09-02 · **Status:** approved for planning · **Owner:** strategy research

## 1. Goal

A second run-once lane, `outlier_sleeve`, that sits beside the `strategy_eb`
core (bil25 champion) in a NEW Strategies document and captures the
power-law tail of single-stock returns — the SNDK / SMCI / CLS / NVDA class of
multi-baggers — by buying 52-week-high breakouts with top-decile relative
strength, sizing each slice small, **never rebalancing a winner down**, and
exiting only on a slow trend break.

The champion (doc 200, paper instance `strategy-eb`) is untouched. The new
strategy is **backtest-only** until its engine test passes and the operator
reviews it; it gets its own doc and its own lab instance, which also ends the
practice of mutating the live doc for batteries.

Decisions taken by the operator on 2026-09-02: sleeve **15% of NAV**; single
winner may grow to **30% of NAV** before trimming; **backtest-only** deployment.

## 2. Evidence (offline spike, Alpaca IEX daily bars, 2021-01 → 2026-08)

Universe: US equities on NASDAQ/NYSE/ARCA/AMEX, active AND inactive (survivorship
guard), ADV ≥ $10M and price ≥ $3 → 807 names. Costs 10 bps per fill. Sleeve
15% of NAV, remainder idle cash. Scratchpad: `outlier_spike.py`,
`outlier_grid.py`, `outlier_confirm.py`, `outlier_blend.py`.

### 2.1 The exit rule is the whole game

| Exit | 6 slots | 10 slots |
|---|---|---|
| 3 closes < SMA-100 | +17–19% | +13–20% |
| 5 closes < SMA-150 | +33–34% | +32–38% |
| **5 closes < SMA-200** | +36–82% | **+42–53%** |

Fast exits sold SMCI at +126%, NVDA at +48%, CLS at +92% and SNDK at +53% —
before their 10×–60× runs. The SMA-200 exit held CLS to +1109%, SMCI to +662%,
RKLB to +506%, NVDA to +320%. Biweekly screening beats monthly at slow exits.
Max drawdown of the sleeve path is flat across the grid (−14% to −16% of NAV).

Chosen: **10 slots × 1.5% NAV, 5 closes below SMA-200, biweekly screen**
(+53.4%, −14.6%). The 6-slot +81.5% is one name (CLS) on a larger slice —
higher variance, not a better rule.

### 2.2 Nexus confirmation (peer breadth) — small, positive, noisy

On the chosen base, requiring that a fraction of a candidate's Nexus peers
(`COMPETES_WITH` ∪ `SUPPLIER_OF` ∪ `STRATEGIC_PARTNER` ∪ `PARENT_OF` ∪
`CONTROLS`, which are SIC-group competitor sets in practice) sit in the
universe's top RS quartile:

| Confirmation | Sleeve contribution | Blended DD |
|---|---|---|
| off | +54.3pp | −20.7% |
| **≥25% of ≥5 peers** | **+61.4pp** | **−20.1%** |
| ≥35% of ≥5 peers | +56.2pp | −19.6% |
| ≥50% of ≥5 peers | +65.6pp | −19.6% |
| ≥25% of ≥10 peers | +54.2pp | −19.5% |

Non-monotonic → treat as a modest filter, not a signal. Default ON at
**≥25% of ≥5 peers**; the 50% point is left to the engine A/B rather than
adopted on a 75-trade sample.

### 2.3 Blended with the EB core's ENGINE path (bt 785201, bil25)

| Portfolio | Cycle 2021-11 → 2026-08 | Max DD |
|---|---|---|
| EB core alone (engine) | +197.8% | −21.1% |
| 85% EB + 15% sleeve, no confirmation | +222.4% | −20.7% |
| 85% EB + 15% sleeve, ≥25% peers | **+229.5%** | **−20.1%** |

The sleeve adds ~25–35pp of cycle return without deepening drawdown. This is
an offline estimate with no cross-sleeve cash interaction; **the engine test in
§9 is the verdict.**

### 2.4 Why no LLM and no news

Graph Nexus is the platform's LLM-heavy strategy (trade overlay with
buy/sell blocks, analyst panel, sentiment and macro classifiers) and the one
strategy with measured negative live alpha; its live posture already caps LLM
calls at 4 per cycle and turns the panel off. The spike reached its result on
prices and graph edges alone. The only event this lane cannot see from prices
is listing age (spin-offs, IPOs), and the first-bar date is a sufficient proxy
(SNDK's first bar is 2025-02-13). No LLM key is required in the decision path.
`TickerDayFeatures` (news sentiment) is not consumed.

## 3. Architecture

```
offline builder (scripts/build_outlier_features.py)
   Alpaca daily bars (IEX, adjusted, active+inactive assets)
   → OutlierUniverseFeatures  (id "YYYY-MM-DD|SYM": close, hi252, ret126, adv20, sma200, first_bar, n_bars)
   → OutlierGraphPeers        (id "SYM": sector, industry, peers[])   ← exported ONCE from Neo4j

lane backend/strategies/outlier_sleeve.py  (wrapper) + backend/outlier_sleeve.py (pure)
   run_once → reads the PIT cross-section for the LAST VISIBLE session from OutlierUniverseFeatures
            → screen → confirmation → slot management → {sym: 1|0|-1}
            + _nexus_discovered / _nexus_executable_buys (universe admission)
            + _nexus_position_sizes {sym: {"buy_cash": $}}  (sell_fraction ONLY for the winner cap, §5)
            + _nexus_action_intents {sym: "etf_sell"} on exits

doc  "Strategy EB + Outlier Sleeve"  = strategy_eb lane (bil25, execution_position 10, weight 1.0)
                                     + outlier_sleeve lane (execution_position 20, weight 1.0)
instance "strategy-eb-lab" (backtest-only; stocks = EB universe; runCommand false)
```

Pure module / wrapper split mirrors `strategy_eb.py` ↔ `strategies/strategy_eb.py`:
everything testable (screen, confirmation, sizing, exits) is pure and clock-free.

## 4. Data model

### 4.1 `OutlierUniverseFeatures` (new table, registered in `backend/db/schema.py`)

`id = f"{date}|{symbol}"` (COLLATE "C" prefix scans give one date's cross-section
in a single `store.between(table, f"{date}|", f"{date}|~")`). Doc:

| key | meaning |
|---|---|
| `date` | session date (NY) |
| `symbol` | ticker |
| `close` | adjusted close |
| `hi252` | max close over the trailing 252 sessions **including** this one |
| `ret126` | close / close 126 sessions earlier − 1 (None if unavailable) |
| `adv20` | mean of close×volume over the trailing 20 sessions |
| `sma200` | 200-session SMA of close (None if < 180 sessions) |
| `first_bar` | first session date in our history for this symbol |
| `n_bars` | sessions of history up to and including this date |
| `rs_rank` | cross-sectional percentile of `ret126` among rows of the same date with `adv20 ≥ adv_min` (0–1) |

Rows are written by the builder only. Retention: none (≈ 807 × 1,430 ≈ 1.2M
rows for the cycle; the builder is idempotent and re-runnable).

### 4.2 `OutlierGraphPeers`

`id = symbol`; doc `{sector, industry, peers: [symbols]}` exported once from
Neo4j (`MATCH (a:Company)-[:COMPETES_WITH|SUPPLIER_OF|STRATEGIC_PARTNER|PARENT_OF|CONTROLS]-(b:Company)`).
Point-in-time honesty: edges are not dated in the graph export, so the peer set
is treated as static structure (industry membership), not as a dated signal.
The lane never queries Neo4j; strict-PIT backtests replay a recorded Cypher
ledger and fail hard on new queries.

## 5. Algorithm (pure module `backend/outlier_sleeve.py`)

All reads are for the **last visible session** `d` (strictly earlier than the
decision time — reuse `strategy_x.pit_daily_observations` semantics: a daily
row is visible only from the next session).

**Eligibility** (per symbol, date `d`):
- `adv20 ≥ adv_min_usd` (default 10,000,000) and `close ≥ price_min` (3.0)
- `n_bars ≥ min_history_bars` (120); for `n_bars < 252` the high is the all-time high since listing
- not held by the sibling lane (EB universe symbols are excluded outright: TQQQ, SPY, BIL, QQQ, GLD, GDX, XLE)

**Breakout**: `close ≥ hi252 × (1 − breakout_tolerance)` (0.02).

**Relative strength**: `rs_rank ≥ rs_decile_floor` (0.90 = top decile).

**Confirmation** (config `confirm_enabled`, default true): among the candidate's
peers present in the day's cross-section, at least `confirm_min_peers` (5)
exist and the fraction with `rs_rank ≥ 0.75` is ≥ `confirm_frac` (0.25).

**Ranking**: descending `ret126`. Fill free slots from the top.

**Screen cadence**: `screen_weekdays` semantics identical to
`strategy_eb.rebalance_weekdays` — decide on the first call that SEES the
close of a session whose NY weekday is in the list — plus `screen_every_n_weeks`
(2) keyed off `session_ordinal // 5`. Default: biweekly on Wednesdays' close.

**Sizing**: `slot_fraction` (0.015) × NAV per new slot, `max_slots` (10),
`sleeve_fraction` (0.15) caps total *new* deployment (positions that have grown
past 15% do not block new entries; only cash newly committed counts). Buys are
emitted as `_nexus_position_sizes[sym] = {"buy_cash": $}` sized off
`get_buying_power` like `strategy_eb`, and the symbol is listed in
`_nexus_discovered` and `_nexus_executable_buys` so the broker admits it.

**No downward rebalance**: the lane never emits a `sell_fraction` and never
re-targets a held name. The only sells are exits.

**Winner cap**: if a held name's market value exceeds `winner_cap_fraction`
(0.30) of NAV, emit a partial exit back to the cap (`_nexus_position_sizes[sym] =
{"sell_fraction": f}` is the broker's only partial channel; this is the single
sanctioned use).

**Exits** (evaluated every call, act at the next session):
- trend: `exit_below_sma_closes` (5) consecutive visible closes below `sma200`
- time stop: held ≥ `time_stop_sessions` (60) and never up ≥ `time_stop_gain` (0.15) since entry
- a full exit emits score −1 and `_nexus_action_intents[sym] = "etf_sell"`

**State** (`strategy_cache`): `{"slots": {sym: {"entry_px", "entry_ordinal", "proven": bool}}, "last_screen_ordinal": int}`.
Persisted live by the existing cache persistence (no `_llm_/_trace_/_prompt_` keys).

## 6. Config keys (INTELLISTOCK_SCHEMA of the wrapper — all defaults are the measured choices)

```
outlier_sleeve_enabled false, sleeve_fraction 0.15, slot_fraction 0.015, max_slots 10,
winner_cap_fraction 0.30, adv_min_usd 10000000, price_min 3.0, min_history_bars 120,
breakout_tolerance 0.02, rs_decile_floor 0.90, confirm_enabled true, confirm_min_peers 5,
confirm_frac 0.25, screen_weekdays [2], screen_every_n_weeks 2, exit_sma_bars 200,
exit_below_sma_closes 5, time_stop_sessions 60, time_stop_gain 0.15,
excluded_symbols ["TQQQ","SPY","BIL","QQQ","GLD","GDX","XLE"],
broker_max_single_position_pct 0.95, honour_single_position_cap true,
live_max_order_fraction 0.7, live_max_symbol_fraction 0.35, live_max_leveraged_fraction 0.7,
live_soft_drawdown 0.25, live_hard_drawdown 0.35, live_kill_drawdown 0.45
```

## 7. Broker changes (the minimum set; every one has a named site)

1. **Sole-lane guard** — `broker.py:14449-14481` and `live_equity_bars.other_enabled_run_once_lanes`
   (`:42`): whitelist `outlier_sleeve` so enabling it does not blind the EB lane's
   live bars. (Backtest-only for now, but the guard also decides `data` in engine
   runs where both lanes are enabled — verify in the test.)
2. **Per-lane single-position cap** — `broker.py:4395 _strategy_eb_single_position_pct`
   is name-matched to `strategy_eb`; generalise to "any enabled run-once lane
   declaring `broker_max_single_position_pct`", taking the max. The cap is
   buy-admission only (it never trims MTM drift — `broker.py:17455`), which is
   exactly the behaviour the sleeve needs.
3. **Per-document risk limits** — `broker.py:4425 _strategy_eb_risk_limits`:
   resolve from every enabled lane and take the widest `max_symbol_fraction`
   (0.35 for the sleeve) so the live gate does not refuse a winner's growth.
   Live only; no effect on the engine test.
4. **Universe admission** uses the existing `_nexus_discovered` /
   `_nexus_executable_buys` path (`broker.py:7229-7284`, `14926-15110`,
   `_ensure_backtest_history_for_symbols :1812`) — no change; this is how SNDK
   entered before.

Not touched: Graph Nexus peak-banking, trailing, circuit breakers, rotation,
min-hold, turnover budget — all GNA-scoped and inert on a GNA-free document.
`backtest_credit_pending_sell_proceeds` is GNA-config-only, so a sleeve exit's
cash funds the NEXT session's buys; the cadence tolerates that.

## 8. Offline builder (`scripts/build_outlier_features.py`)

- Assets: Alpaca `/v2/assets` active + inactive, exchanges NASDAQ/NYSE/ARCA/AMEX, alphabetic tickers ≤ 5 chars.
- Liquidity prefilter: last-90-session ADV ≥ $10M and close ≥ $3, OR (inactive) the same over 2023-H1.
- Bars: `/v2/stocks/bars` multi-symbol, `adjustment=all`, `feed=iex`, paged (measured ≈ 10k bars / 0.7s → full 5.5-year build ≈ 5 minutes for ~800 names).
- Writes features per (date, symbol) with `rs_rank` computed per date over eligible rows. Idempotent (`store.insert(..., conflict="replace")` semantics).
- `--start 2020-06-01 --end <today>`; a daily incremental mode appends the newest session only.
- Peers export: one Cypher, written to `OutlierGraphPeers`.

## 9. Engine test (pre-registered — thresholds frozen before the first run)

Doc: `strategy_eb` (bil25 config verbatim) + `outlier_sleeve` (defaults above),
instance `strategy-eb-lab`, granularity 86400, `equity_cost_tiers: etf-liquid`
for the ETF legs (single stocks pay the engine's default tiered cost).

Windows: `cyc` 2021-11-01→2026-08-27, `ny1` 2022-01-01→2023-12-31, `ny3`
2024-01-01→2026-08-27, `rb1` 2022-01-01→2022-06-30, `rb3` 2025-02-15→2025-04-15,
`nb4` 2026-01-15→2026-04-30, `nc3` 2022-03-01→2022-08-31.
Baselines are the bil25 engine numbers already on file.

Pass = all of:
1. `cyc` return ≥ bil25 + 15pp (≥ +212.8%)
2. `cyc` max drawdown ≤ bil25 + 3pp (≥ −24.1%)
3. no bear window (`rb1`, `rb3`, `nb4`) flips from ≥ 0 to < 0
4. attribution: ≥ 3 distinct names each contribute ≥ 5pp of the sleeve's gain
   (population effect, not one lucky name)

Fail on 1–3 → the sleeve is not adopted; report and stop. Fail on 4 alone →
adopt with the concentration caveat stated in the research doc.

A second arm, `confirm_frac 0.50`, runs after the default passes or fails, as
the one permitted variant (sequential; the lab doc is mutated, the champion doc
never).

## 10. Out of scope

- Live/paper deployment of the new doc (operator decision after the engine test).
- Any LLM or news input.
- Supplier lead-lag (207 `SUPPLIER_OF` edges in the graph — too sparse to matter).
- Short selling, options, intraday cadence.

## 11. Risks named up front

- **Concentration**: a 1.5% slice that reaches the 30% cap is a real single-name
  position; a −40% gap in that name is −12% of NAV. The core's job is to make
  that survivable; the drawdown criterion in §9 measures it.
- **Survivorship**: the offline universe includes inactive names, but Alpaca's
  history for delisted tickers is incomplete; the engine shares this bias.
- **Costs**: single stocks pay the engine's default tiers, not the ETF preset;
  turnover of the sleeve is low (≈ 75 trades over 5.8 years) so drag is small.
- **Path dependence**: results move with the screen day. The engine test runs
  Wednesdays only; the ±2-window replay noise measured for EB applies.
