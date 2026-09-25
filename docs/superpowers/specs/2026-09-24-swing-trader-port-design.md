# swing-trader port — design

**Date:** 2026-09-24 · **Branch:** `research/swing-trader-port` · **Status:** approved by the operator 2026-09-24
**Source repo:** github.com/tmasters2876/swing-trader (commit c2afa71), referred to below as **ST**. ST file references use the repo's own paths (`paper_trader.py:293`).
**Research:** `docs/superpowers/research/2026-09-24-swing-trader-port-research.md` (the operator read the verdict and chose to port anyway).

## 1. Goal

Port ST's swing strategy, options wheel and AI conviction layer into IntelliStock as new strategy files that run on IntelliStock's engine:

- Backtests of the swing strategy run in IntelliStock's backtest engine.
- Live trading runs on a separate Alpaca **paper** account.
- Wherever IntelliStock lacks a capability the strategies need, the engine gains it: equity bracket orders, options trading, and the reference data (VIX, S&P membership, GICS sectors).

**Success:**

1. `strategy_swing` backtests on a lab document and produces trades whose entries and exits follow ST's live rules.
2. On the paper instance, the swing lane places GTC bracket orders at 09:15 ET, and the wheel lane sells, monitors and buys back cash-secured puts. Every leg fill, expiry and assignment reconciles without making the account's positions unhealthy.
3. AI scores come from a model the operator links in the strategy editor. Scores of 50–74 wait in a queue the operator approves or rejects on web or iOS, and an approval places the order within seconds.
4. EB on doc 200 (alpaca-main, real money) takes byte-identical code paths.

**Non-goals:**

- Backtesting the wheel. There is no historical option data, and the OPRA agreement is unsigned.
- Porting ST's `backtester.py`, `dashboard.py`, Render and cron files, or its React Native app.
- Live (real-money) trading of either lane. That comes later, as its own decision.
- Improving the strategy. ST's rules and constants are kept, apart from the fixes in §9.

## 2. Operator decisions (2026-09-24)

| Decision | Choice |
|---|---|
| Account | A separate Alpaca paper account, linked as its own brokerage. Never alpaca-main. |
| Fidelity | ST's exact rules, plus fixes for the order bugs (§9, items 1–10). |
| Backtests | IntelliStock's engine, not ST's backtester. |
| Architecture | New strategy files. The engine itself learns options and brackets (option "b"), so option orders pass the same order path and checks as stock orders. |
| Brackets | Engine bracket orders. Live, they are Alpaca GTC brackets. In backtests, the legs trigger off each bar's high and low. |
| Swing cadence | Scan at 09:15 ET and submit market brackets that Alpaca queues for the open, as ST does. |
| AI model | IntelliStock's models framework: `conviction_llm_model_id`, linked in the UI. |
| Approvals | Web and iOS, acting within seconds of the click. |
| Delivery | Autonomous: spec, plans, TDD build, parallel bug sweep, then push the branch. Merging to `main` waits for the operator. |

## 3. Architecture

```
                          backend/swing_trader/   (ported ST logic, pure where possible)
                          indicators · signals · regime · sectors · universe ·
                          wheel_rules · ai_analyst · market_data · iv · calibration
                                   │                         │
            backend/strategies/strategy_swing.py      backend/strategies/strategy_wheel.py
            (StrategySwing.run_once)                  (StrategyWheel.run_once; inert in backtests)
                                   │                         │
      ┌────────────────────────────┴───────────┐             │ _nexus_option_orders
      │ decisions + _nexus_position_sizes[sym] │             │
      │   {"buy_cash", "bracket", "fill_at_next_open"}      │
      ▼                                        ▼             ▼
  BACKTEST: broker.py → PortfolioEmulator     LIVE/PAPER: broker.py → LiveOrderService
            → NextEventExecutionSimulator       → UnifiedOrderGate (+ options branch)
              (+ bracket legs on bar high/low,  → AlpacaAdapter (+ bracket, + options,
               + next-open fills)                  + activities, + legs)
                                                ← reconcile (+ legs, + short options,
                                                   + OPASN/OPEXP/OPEXC)
  API: /instances/{id}/swing/* ──► SwingSignals table ──► LiveCommands submit_order (approvals)
  UI:  web InstanceDetailView + LiveTradingView · mobile features/swing · notifications
```

Both lanes live on one strategy document attached to one equities instance (`swing-paper`), linked to the paper brokerage.

## 4. Ported logic: `backend/swing_trader/`

Each module carries a header naming the ST file and line range it came from. Functions are copied verbatim wherever the code is pure. Only I/O changes: files become `db.store`, Pushover becomes `notifications`, and Anthropic SDK calls become `llm_utils`.

| Module | Ported from ST | Contents |
|---|---|---|
| `constants.py` | `paper_trader.py:63-117`, `wheel_trader.py:76-120`, `ai_analyst.py:38-43`, `iv_collector.py:48-52`, `calibration.py:20-25` | Every constant with ST's value. These are the defaults in both strategy headers. |
| `indicators.py` | `paper_trader.py:156-201` (RSI, MACD, SMA, volume average, ADX, including the NaN-close guard), `wheel_trader.py:720-741` (`_rsi`, `_sma`, `_atr`) | Pure pandas |
| `signals.py` | `paper_trader.py:293-336` (`entry_signal`, `exit_signal`, `sector_conflict`), `:143-149`, `:600-621` (bear mode) | Pure |
| `regime.py` | `paper_trader.py:478-480` and `app.py` `fetch_regime` decision logic | SPY above SMA200 × 1.03 and VIX ≤ 25 |
| `universe.py` | `sp500_symbols.py`, the defensive universe, the wheel universe (`wheel_trader.py:46-80`) | The live universe is today's list, as in ST. Backtests read `SwingIndexMembership` (§7). |
| `sectors.py` | `paper_trader.py:103-117` (`get_symbol_sector`, `_SECTOR_OVERRIDES`) | Reads `SwingSectorMap`. yfinance is only a fallback for unmapped symbols. |
| `wheel_rules.py` | `wheel_trader.py:137-227` (delta pick), `:257-274` (2× premium buy-back), `:477-491` (covered-call strike), `:576-670` (limit-price ladder and caps), `:765-834` (`next_friday`), `:839-953` (`get_candidates`), `:1220-1252` (Tuesday fallback) | Pure. The monitor rules come from `app.py:1309-1357`. |
| `ai_analyst.py` | ST `ai_analyst.py` (the prompts, `_handle_result` validation, the thresholds) and `wheel_trader.py:958-1072` (the wheel scorer) | Calls go through `llm_utils` (§8) |
| `market_data.py` | ST `market_data.py` | Batched SIP daily bars (≤100 per request, `adjustment=all`) and the IEX latest trade, via alpaca-py, using the credentials the engine passes to strategies |
| `iv.py` | ST `iv_collector.py` | Dual-write IV snapshots into `SwingIvSnapshots` for the 15 fallback names. `_load_iv_rank` is kept but gates nothing, as in ST. |
| `calibration.py` | ST `calibration.py` | Reads `SwingSignals` joined to outcomes |
| `approvals.py` | ST `app.py` approve/reject logic | A pure state machine (pending → approved, approved_half or rejected; decisions are final), plus order rebuild at the live price (`app.py:1049-1058` equivalent) |

ST's tests for this logic are ported next to it (`tests/test_swing_*.py`): `test_indicators_nan`, `test_regime_logic`, `test_collateral_cap`, `test_assignment_detection`, `test_wheel_scan_guard`, `test_calibration`, and `test_market_data` with mocked clients.

## 5. Strategy files

### 5.1 `strategy_swing` (`StrategySwing`)

**Header config.** Every ST swing constant appears under the same meaning:

- `rsi_period=14`, `rsi_entry_max=50`, `rsi_overbought=70`
- `sma_long=200`, `macd_fast/slow/signal=12/26/9`, `vol_avg_period=20`
- `adx_period=14`, `adx_min=15`
- `spy_buffer=1.03`, `vix_max=25.0`
- `position_size_pct=0.125`, `max_positions=8`, `max_per_sector=1`
- `profit_target=0.09`, `stop_loss=0.06`
- `bear_regime_days=10`, `defensive_universe=[XLP, XLU, XLV, GLD, SHY]`
- `earnings_hard_block_days=5`
- `ai_gate_enabled=true`, `ai_approve_threshold=75`, `ai_review_threshold=50`
- `conviction_llm_model_id=""`, `scan_time_et="09:15"`

It also carries these keys:

- `strategy_swing_enabled=false`
- the live envelope, in the same form as EB's: `live_max_order_fraction=0.2`, `live_max_symbol_fraction=0.2`, drawdown rungs
- `honour_single_position_cap=true` with `broker_max_single_position_pct=0.2`

**Backtest** (`data is not None`). Each daily tick at 05:00 PT sees close(D−1).

1. Compute indicators incrementally, caching them per symbol in `strategy_cache`.
2. Apply the regime from SPY bars and `SwingMacroDaily` VIX. The VIX value is the latest dated strictly before the NY trading date.
3. Restrict entry candidates to point-in-time S&P members. Look up sectors in `SwingSectorMap`.
4. Emit entries as `decision=1` with `_nexus_position_sizes[sym] = {"buy_cash": equity×0.125, "bracket": {"take_profit_price": round(close×1.09, 2), "stop_loss_price": round(close×0.94, 2)}, "whole_shares": true, "fill_at_next_open": true}`. These are the same absolute, prior-close-anchored leg prices live uses.
5. Emit the RSI-cross exit as `decision=-1` with `{"sell_fraction": 1.0, "fill_at_next_open": true}`.
6. The AI gate and the earnings block are off, as in ST's backtester.

**Live/paper** (`data is None`):

- **Swing scan.** Runs once per NY session, at the first tick at or after `scan_time_et`.
  - It fetches its own daily bars through `swing_trader.market_data`, and VIX and earnings from yfinance, as in ST.
  - It computes the same decisions and routes each candidate through the AI gate (§8).
  - Auto-approved entries are emitted with the same sizing hint as in backtests: absolute leg prices anchored on the prior close, as in ST (`paper_trader.py:669-670`).
  - The shares are `int(equity × 0.125 × size_adjustment / close)`.
  - The session latch is stamped when the scan completes, not when it is emitted.
  - An entry the gate refuses with `quote.stale` is re-emitted once, at the first regular-hours tick.
- **Bear-mode counter.** Advances once per NY session. This is fix 11.
- **Exits.** The RSI-cross exit runs in the scan. The engine cancels the bracket legs before selling (§6.1).

### 5.2 `strategy_wheel` (`StrategyWheel`)

**Header config.** Every ST wheel constant:

- `rsi_min=30`, `rsi_max=60`, `sma_trend=50`, `atr_period=14`
- `strike_atr_mult=0.5`, `min_premium_pct=0.005`, `target_delta=0.25`, `days_to_expiry=7`
- `max_collateral_pct=0.25`, `max_per_sector=2`
- `auto_covered_call=false`
- `approve_threshold=75`, `review_threshold=50`
- `earnings_block_days=7`, `limit_bid_mult=0.95`
- `scan_weekday=0`, `scan_time_et="10:30"`, `monitor_time_et="15:45"`
- `conviction_llm_model_id=""`, `strategy_wheel_enabled=false`

**Backtest:** returns `{}`, and logs once that the wheel is live-only.

**Live/paper:**

- **Weekly scan.** Runs Monday at or after 10:30 ET. If Monday's scan did not complete, it runs Tuesday. Completion is marked in `strategy_cache`, distinct from the start marker, as in ST's completion marker.
  - Screen with `get_candidates` and score with the AI.
  - Pick the strike closest to the 0.25 delta from the full chain.
  - Price the order at live bid × 0.95, then ST's fallback ladder.
  - The order goes into `_nexus_option_orders` as `{"underlying", "contract", "type": "put", "strike", "expiry", "position_intent": "sell_to_open", "qty", "limit_price", "tif": "day", "signal_id"}`.
- **The Monday 2× premium check** (ST `check_open_wheel_positions`). A short put whose price has reached 2× its entry premium gets `{"position_intent": "buy_to_close", "type": "market"}`.
- **Daily monitor at or after 15:45 ET** (ST `app.py:1341-1357`). Buy to close at market when:
  - the put is ≥ 10% ITM;
  - or it is ≥ 5% ITM with 2 or fewer days to expiry;
  - or it is ITM at all on expiry day.

  Notifications follow ST's priority levels.
- **Assignment.** The engine reports assigned shares (§6.1, activities). With `auto_covered_call=false`, the covered-call candidate is logged only (dry run), as in ST.
- **IV snapshot.** Once per session at or after 09:15 ET.

## 6. Engine changes

**Isolation rule.** A new branch runs only when an order or intent carries a new field. EB never sets one, so its code paths stay byte-identical. That is proven by the tests in §11.

### 6.1 Live and paper order path

**`live_orders/types.py`: `OrderIntent`**
- Adds optional fields: `asset_class` (`"us_equity"` or `"us_option"`), `order_class` (`None` or `"bracket"`), `take_profit_price`, `stop_loss_price`, `position_intent` (`buy_to_open`, `buy_to_close`, `sell_to_open`, `sell_to_close`), `contract_multiplier` (1 or 100), `underlying`, `option_type`, `strike`, `expiry`.
- The identity dict includes a field only when it is not the default, so stored EB rows keep their keys. The store's row round-trip carries the fields, defaulting when absent.
- `DependencySnapshot` accepts a negative `position_quantity` only when `asset_class == "us_option"`.

**`live_orders/gate.py`: `UnifiedOrderGate`**
- Adds an options branch, entered only when `asset_class == "us_option"`:
  - regular hours only (09:30–16:00 ET, NYSE calendar);
  - `sell_to_open` puts must be integer contracts, and `strike×100×qty` must be ≤ cash − the collateral of open short puts − pending sell-to-open collateral;
  - a per-underlying cap: 25% of equity, counting existing puts on that underlying (fix 3);
  - `buy_to_close` needs a short position of at least `qty`;
  - notional and the max-order check use `qty × price × 100`;
  - option quotes come from the options snapshot, and a DECISION mark older than 60 s is refreshed over REST before the check.
- The direction rules stay unchanged for equities.
- A bracket BUY is gated like any market BUY on the parent quantity. The legs are reduce-only children and are not gated.

**`live_orders/service.py`**
- Passes the new fields to the transport only when set.
- `ConfirmedFill` applies `contract_multiplier` to the cash and notional deltas.
- Registers bracket legs as lifecycle rows (source `BRACKET_LEG`, parent client order id) from the parent order's `legs`, read back with `nested=True` right after submission. Leg fills therefore resolve to known records.

**`live_orders/reconcile.py`, `store.py`**
- Adds the statuses `held` and `pending_cancel`.
- A short option position with lifecycle lineage is owned, not external.
- Negative broker quantities are allowed only for `us_option`.

**`broker_adapters/base.py`**
- Adds non-abstract methods that raise `NotImplementedError`, so Binance.US is unaffected:
  `get_option_chain`, `get_option_contracts` (paginated), `get_option_snapshots`, `list_option_positions`, `get_account_options`, `get_option_activities(types, after)`, `get_order_with_legs`, `cancel_orders_confirmed(ids, timeout_s)`, `list_closed_orders(symbols, after)`, `get_daily_bars(symbols, days)` (batched), `get_latest_trades`.
- New DTOs: `OptionContractDTO`, `OptionSnapshotDTO`, `OptionPositionDTO` (signed qty, multiplier), `OptionActivityDTO`.
- `AccountDTO` gains the options fields. `OrderRef` gains `order_class`, `legs`, `position_intent`, `asset_class`.

**`broker_adapters/alpaca.py`**
- Implements those methods against alpaca-py 0.43.5:
  - `get_option_contracts` pages through `next_page_token`;
  - snapshots use `feed=indicative`;
  - activities use `self._client.get("/account/activities/{type}")`.
- `submit_order` gains `order_class`, `take_profit`, `stop_loss` and `position_intent`, plus option-symbol handling:
  - integer qty, no notional, no extended hours, no fractional floor-and-retry;
  - `_preflight_buy` is skipped for `buy_to_close`, because PDT must not block risk reduction.
- `refresh_positions` keeps option positions, including shorts, in a separate `_option_positions` map. The existing equity `_positions` filter (qty > 0) is untouched.
- Option rejections map to an `OptionsNotPermitted` error class rather than `FractionalNotAllowed`.
- At boot, the adapter checks `options_trading_level ≥ 1` when the document has an enabled wheel lane. Below that it refuses the wheel lane, logs in red and sends an alert.

**`broker.py`**
1. `run_run_once_strategies` pops a new `_nexus_option_orders` side channel and carries it in the metadata.
2. A new `_execute_option_intents(...)` runs after the per-symbol stock loop, only when that list is non-empty. It builds `OrderIntent`s and submits them through the same `LiveOrderService`.
3. `_build_strategy_stock_intent(..., bracket=None)` works as follows when `nexus_hint["bracket"]` is set:
   - whole shares;
   - `tif=gtc`, `order_class=bracket`, absolute leg prices;
   - it skips `_order_style_for_now`'s extended-hours conversion, so a 09:15 submission is a plain market bracket that Alpaca queues for the open.
4. **Selling a bracketed position.** Before any SELL of a symbol with open bracket legs, `_cancel_bracket_legs_confirmed` cancels the legs and waits up to 10 s for the cancel to confirm. If it doesn't confirm, the sell is deferred to the next tick.
5. **Guards.** `ordered_today`, the pending-order guard and the EB working-order helpers ignore orders in `held` status and orders that are bracket children. These helpers read the whole account, and no such orders exist on alpaca-main, so EB is unaffected.
6. **Kill level.** Kill-level cancellation and halt skip risk-reducing orders: bracket legs and `buy_to_close`.
7. **Lane registration.** `strategy_swing` and `strategy_wheel` go into `_LANE_ENABLE_FLAGS`, with `defaults_by_lane` rows.
8. **Activities poller.** Each live tick on a document with an enabled wheel lane, the broker polls `OPASN`, `OPEXP` and `OPEXC` since a persisted cursor. It turns them into lifecycle events:
   - an option position is removed;
   - on assignment, the shares are added under the wheel lane's ownership;
   - a `wheel_assignment` notification is sent.
9. **Approval commands.** The `submit_order` live command handler accepts `{"source": "swing_approval", "signal_id"}`. It rebuilds the order with `swing_trader.approvals` at the live price: stop, target and shares for swing; expiry and strike for the wheel (fix 2). It then submits through the order service.

**`live_broker_fetch.py`.** P&L uses Alpaca's `unrealized_pl`, `unrealized_plpc` and `current_price`. Each position gains `asset_class`, `side`, `multiplier`, `underlying`, `strike` and `expiry`.

### 6.2 Backtest simulator

Every new branch triggers only for orders that carry a bracket or `fill_at_next_open`.

- **Order fields.** `SimulationOrder` gains `bracket: dict | None`, `whole_shares: bool` and `fill_at_next_open: bool`.
- **Next-open fills.** A `fill_at_next_open` order fills at the OPEN of the first bar whose session starts after the decision time. It is stamped at that bar's open and charged the market-order cost model.
- **Bracket legs.** When a bracket parent fills, `_bracket_legs[parent_id]` records the symbol, the filled quantity, and the order's absolute `stop_loss_price` and `take_profit_price`. These are the same prices a live Alpaca bracket carries.
  - Legs are kept out of `_pending`, so `unfilled_order_count` and `pending_execution_symbols()` are unchanged.
  - Legs reserve no shares.
- **Leg checks.** `simulator.on_bar(SimulationBarEvent(o, h, l, c, bar_ts, available_at))` runs from `PortfolioEmulator.process_bar_events`. That is called from `broker.py` after the pending-fill block and before the strategy call, guarded by `if portfolio_emulator.has_bracket_legs():`.
  - The bar the parent filled in is included, because its whole range happens at or after the open fill.
- **Trigger order for each bar:**
  1. open ≤ stop → stop fills at the open;
  2. open ≥ target → target fills at the open;
  3. low ≤ stop and high ≥ target → the stop fills at the stop, because the path within a bar is unknown and the worse outcome is safer;
  4. low ≤ stop → stop at the stop price;
  5. high ≥ target → target at the target price.
- **Leg costs.** The stop is charged the market-sell spread and slippage around the trigger. The target fills like a passive limit (no spread). Both pay `fee_bps`.
- **One-cancels-other.** A leg fill deletes its sibling and cancels any pending strategy sell for that symbol. A strategy sell that fills shrinks or deletes the legs.
- **Fill records.** Fills carry `source = bracket_sl:<parent>`, `bracket_sl_gap:<parent>` or `bracket_tp:<parent>`, and `exit_reason`. Summary keys for brackets appear only when a bracket was submitted.

## 7. Reference data (tables in `db/schema.py`, builders in `scripts/`)

| Table | Id | Source | Read rule |
|---|---|---|---|
| `SwingMacroDaily` | `VIX\|YYYY-MM-DD` | Cboe `VIX_History.csv`, with FRED `VIXCLS` as fallback | The latest date strictly before the NY trading date |
| `SwingIndexMembership` | `SPX\|YYYY-MM-DD` (change dates) | The fja05680 S&P 500 historical-components CSV plus a ticker-rename map | A change is visible once its date is before the NY trading date |
| `SwingSectorMap` | `SYMBOL` | yfinance `info.sector`, plus ST's overrides (`as_of` and `source` stored) | Static and labelled not point-in-time. Refreshed by rerunning the builder. |
| `SwingIvSnapshots` | `SYMBOL\|YYYY-MM-DD` | Written live by `iv.py` | — |
| `SwingSignals` | uuid | Written by both lanes | One row per AI-scored candidate, rejects included |
| `SwingWheelScans` | uuid | Written by the wheel scan | Replaces `wheel_trades.csv` |

Builders: `scripts/build_swing_reference_data.py` (VIX, membership, sectors; idempotent) and `scripts/swing_lab_setup.py`. The setup script creates the lab document and instance and refuses docs 200–203. The lab watchlist is the union of S&P members over the backtest window, plus SPY, QQQ and the defensive ETFs.

## 8. AI layer (the models framework)

- Both headers declare `conviction_llm_model_id`. `model_resolver` injects `conviction_llm_provider`, `conviction_llm_model` and `conviction_llm_api_key` (and the rest) on every call. `strategyConfig.js` gains a `KNOWN_LLM_ROLE_LABELS['conviction_']` entry and `STRATEGY_FIELD_META` labels for both strategies.
- **Scoring** uses `llm_utils.call_structured_llm_by_provider` with ST's prompt and a JSON schema: `conviction_score`, `recommendation`, `reasoning`, `position_size_adjustment`, `key_risks`. ST's `_handle_result` checks run afterwards:
  - the thresholds are re-applied in code;
  - the size adjustment is clamped to {1.0, 0.5, 0.25};
  - stop < entry × 0.99 and target > entry × 1.01.
- **News** uses a new `llm_utils.call_llm_with_web_search(provider, ...)`: Gemini goes through the existing grounding path, and Anthropic through its server-side web search tool, which is new. Any other provider skips news and logs one line. It never fails the scan.
- **Failure handling.** An LLM error on one candidate skips that candidate (fix 5).
- **When nothing is linked.** With no model linked and `ai_gate_enabled=true`, live entries are not placed. They are logged and alerted once per session. This mirrors the refusal the backtest applies when model resolution fails.
- **Recording.** Every scored candidate writes a `SwingSignals` row with its status: `auto_approved`, `pending`, `ai_rejected`, `approved`, `approved_half` or `rejected`. Calibration reads it (ST `GATE_TRADES=20`, `MIN_BUCKET_N=5`).

## 9. Deviations from ST (operator-approved)

Order-bug fixes:
1. A rerun cannot sell a duplicate put. Open puts and working orders are checked, and the idempotency key includes the contract and the session.
2. Approval recomputes the wheel expiry.
3. The collateral cap counts existing puts on the same underlying.
4. The sector set is updated after each buy within a scan.
5. One AI error skips its candidate instead of ending the scan.
6. The monitor checks the contract type, so calls are not treated as puts.
7. The trade log records the fill price.
8. Collateral is checked against cash, not margin buying power.
9. The option chain is read in full, all pages.
10. Positions are identified by Alpaca contract fields, not an OCC regex.

Platform differences:
11. The bear-mode counter counts sessions, not runs.
12. Scan times are fixed in ET: 09:15 swing, Monday 10:30 wheel. ST's UTC cron drifted an hour in winter.
13. Files become tables, Pushover becomes Discord and iOS push, and the Flask dashboard becomes IntelliStock's web and iOS screens.
14. The model comes from the models framework. News works with Anthropic and Gemini models only.
15. Sectors come from a stored map. yfinance is used only for unmapped symbols.
16. IntelliStock's order gate sits in front of every order. The lanes set their envelopes so it enforces safety limits only. Each refusal is logged with its reason, and a stale pre-market quote retries once at the open.
17. There is no manual "Run Now" button.

Backtests, compared with ST's `backtester.py`: they use the live rules (max 8 positions, a 1.03 buffer, equity sizing, the full sector map), point-in-time membership, the engine's cost model, and intraday bracket triggers. The earliest start is about mid-2021, because engine bars begin in 2020-07.

## 10. API, UI and notifications

**API** (`backend/api/main.py` routes, `interactive_utils.py` actions; auth required):
- `GET /instances/{id}/swing/signals?status=pending`
- `POST /instances/{id}/swing/signals/{signal_id}/decision` with `{decision: approve|approve_half|reject, reason?}`. It validates that the signal is pending, records who decided, and on approval enqueues a `submit_order` LiveCommand (§6.1, item 9).
- `GET /instances/{id}/wheel` returns open puts, collateral, ITM distance, days to expiry and recent scans.
- `GET /instances/{id}/swing/calibration`

**Web:**
- `components/swing/PendingSignalsCard.vue` and `components/swing/WheelPanel.vue` on `InstanceDetailView.vue`, shown when the document has a swing or wheel lane.
- `LiveTradingView.vue`: an option badge; "Contracts" instead of "Shares"; ×100 totals; Close hidden for short options; no price-history fetch for OCC symbols.

**Mobile:**
- `mobile/lib/features/swing/` (repository, controller, `pending_signals_section.dart`, `wheel_card.dart`) in `instance_detail_screen.dart`.
- `position_card.dart` and `live_state.dart` handle options.

**Notifications.** New `NOTIFICATION_TYPES`: `swing_entry`, `swing_pending_review`, `swing_exit`, `swing_run_summary`, `wheel_put_placed`, `wheel_pending_review`, `wheel_position_alert`, `wheel_assignment`. Push is on by default for pending reviews, entries, exits and position alerts. `test_notification_types.py` is extended.

## 11. Testing

- **TDD.** Every task starts with a failing test.
- **Parity tests.** The ported pure functions are compared with the ST originals on fixed fixtures. ST's own tests are ported.
- **EB invariance:**
  - `OrderIntent.idempotency_key` and the transport kwargs of representative EB intents are pinned byte-for-byte;
  - the simulator's fills and `execution_summary` for a non-bracket fixture are identical before and after the change;
  - `test_strategy_x_broker_coexistence` sentinel lists are updated.
- **Simulator.** Tests cover each trigger rule, one-cancels-other, next-open fills, whole shares, and legs being absent from pending counts.
- **Live path.** Adapter tests run with mocked alpaca-py: brackets, options, pagination, activities and error mapping. Gate tests cover the options branch. Reconcile tests cover leg fills and short options.
- **API, UI and notifications.** Route tests include the "every mutating route needs auth" inverse test. The frontend passes `npm run build`, and mobile passes `flutter analyze`.
- **Suite baseline.** 7,565 passed with exactly 19 pre-existing failures. The target is no new failures.

## 12. Delivery and safety

- All work happens on `research/swing-trader-port` and is pushed to origin. **No merge to `main`** without the operator, because `main` auto-deploys to the real-money server.
- **The merge must happen outside market hours,** and never on an EB rebalance or sweep morning (Thursday or Friday) before fills. After deploying, run `python3 scripts/check_deployed_code.py`, and add the new files to its `FILES` list.
- **Post-deploy verification by the operator and Claude:**
  1. an EB lab cold-start A/A backtest, byte-identical to its pre-merge result;
  2. link the paper brokerage and create `swing-paper` with `scripts/swing_lab_setup.py --paper`;
  3. link the model in the UI;
  4. on paper, confirm:
     - the options-level check;
     - one sell-to-open and buy-to-close cycle;
     - one bracket buy at 09:15 that fills at the open, with its legs visible;
     - one approval round trip from iOS.
- **Rollback:** revert the merge commit and push. The new tables are harmless if left in place.
