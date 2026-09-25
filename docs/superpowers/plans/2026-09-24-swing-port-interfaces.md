# swing-trader port — shared interface contract

Every plan for the swing-trader port (A-live, A-backtest, B, C) uses these names and shapes **exactly**. If a plan needs a name that is not here, it adds it here first.

**Spec:** `docs/superpowers/specs/2026-09-24-swing-trader-port-design.md`

## 1. What a strategy returns from `run_once` (strategies → engine)

```python
{
  "AAPL": 1,            # decisions: {sym: 1 | 0 | -1}
  "_nexus_position_sizes": {
      "_cash_reserve_floor_pct": 0.0,
      # swing entry (backtest AND live):
      "AAPL": {"buy_cash": 1250.0,
               "bracket": {"take_profit_price": 218.00, "stop_loss_price": 188.00},
               "whole_shares": True,
               "fill_at_next_open": True},        # backtest-only; live ignores it
      # swing exit (RSI cross):
      "MSFT": {"sell_fraction": 1.0, "fill_at_next_open": True},
  },
  "_nexus_discovered": ["AAPL", "MSFT"],          # every symbol the lane emits
  "_nexus_executable_buys": ["AAPL"],
  "_nexus_sell_enforcement": ["MSFT"],
  "_nexus_action_intents": {"AAPL": "swing_entry", "MSFT": "swing_rsi_exit"},  # str labels ONLY
  "_nexus_option_orders": [                       # wheel lane only; live only
      {"signal_id": "9f1c…" or None,
       "underlying": "APH",
       "contract": "APH261002P00130000",          # OCC symbol from Alpaca contract data
       "option_type": "put",                      # "put" | "call"
       "strike": 130.0,
       "expiry": "2026-10-02",                    # ISO date
       "position_intent": "sell_to_open",         # sell_to_open | buy_to_close | sell_to_close | buy_to_open
       "qty": 1,                                  # int contracts, >= 1
       "order_type": "limit",                     # "limit" | "market"
       "limit_price": 1.23,                       # None for market
       "tif": "day",
       "reason": "wheel_sto_put"},                # also the action-intent label
  ],
}
```

Intent labels, all plain strings: `swing_entry`, `swing_rsi_exit`, `swing_defensive_entry`, `wheel_sto_put`, `wheel_btc_itm`, `wheel_btc_2x`, `wheel_btc_expiry`, `wheel_sto_call`.

## 2. `OrderIntent` additions (`backend/live_orders/types.py`)

These are optional dataclass fields with defaults. **A field is included in the identity dict only when it differs from its default.**

```python
asset_class: str = "us_equity"          # "us_equity" | "us_option"
order_class: Optional[str] = None       # None | "bracket"
take_profit_price: Optional[float] = None
stop_loss_price: Optional[float] = None
position_intent: Optional[str] = None   # "buy_to_open" | "buy_to_close" | "sell_to_open" | "sell_to_close"
contract_multiplier: int = 1            # 100 for options
underlying: Optional[str] = None
option_type: Optional[str] = None       # "put" | "call"
strike: Optional[float] = None
expiry: Optional[str] = None            # "YYYY-MM-DD"
```

New lifecycle source constant: `BRACKET_LEG = "bracket_leg"`. New statuses: `held` and `pending_cancel`.

## 3. Broker adapter additions (`backend/broker_adapters/base.py`)

DTOs are frozen dataclasses:

```python
@dataclass(frozen=True)
class OptionContractDTO:
    symbol: str; underlying: str; option_type: str; strike: float
    expiration: str; open_interest: Optional[int]; close_price: Optional[float]

@dataclass(frozen=True)
class OptionSnapshotDTO:
    symbol: str; bid: Optional[float]; ask: Optional[float]; last: Optional[float]
    iv: Optional[float]; delta: Optional[float]; gamma: Optional[float]
    theta: Optional[float]; vega: Optional[float]; quote_ts: Optional[str]

@dataclass(frozen=True)
class OptionPositionDTO:
    symbol: str; underlying: str; option_type: str; strike: float; expiry: str
    qty: int                      # signed; negative = short
    avg_entry_price: float        # per-share premium
    current_price: Optional[float]; market_value: Optional[float]
    unrealized_pl: Optional[float]; multiplier: int = 100

@dataclass(frozen=True)
class OptionActivityDTO:
    id: str; activity_type: str   # "OPASN" | "OPEXP" | "OPEXC"
    symbol: str; qty: float; date: str; price: Optional[float]
```

`BrokerAdapter` methods are **non-abstract** and raise `NotImplementedError` in the base class:

```python
def get_option_contracts(self, underlying: str, *, option_type: Optional[str] = None,
                         expiration_gte: Optional[str] = None, expiration_lte: Optional[str] = None,
                         strike_gte: Optional[float] = None, strike_lte: Optional[float] = None
                         ) -> list[OptionContractDTO]: ...          # paginates all pages
def get_option_snapshots(self, contracts: list[str]) -> dict[str, OptionSnapshotDTO]: ...
def list_option_positions(self) -> list[OptionPositionDTO]: ...
def get_account_options(self) -> dict: ...
    # {"options_trading_level", "options_approved_level", "options_buying_power",
    #  "non_marginable_buying_power", "cash", "equity"}
def get_option_activities(self, types: tuple = ("OPASN", "OPEXP", "OPEXC"),
                          after: Optional[str] = None) -> list[OptionActivityDTO]: ...
def get_order_with_legs(self, order_id: str) -> "OrderRef": ...  # OrderRef.legs filled
def cancel_orders_confirmed(self, order_ids: list[str], timeout_s: float = 10.0) -> bool: ...
def list_closed_orders(self, symbols: list[str], after: str) -> list["OrderRef"]: ...
def get_daily_bars(self, symbols: list[str], days: int) -> dict[str, list[dict]]: ...
    # bars: [{"t","o","h","l","c","v"}], oldest first, SIP, adjustment=all, batches <= 100
def get_latest_trades(self, symbols: list[str]) -> dict[str, tuple[float, str]]: ...
```

`OrderRef` gains the fields `order_class: Optional[str] = None`, `legs: tuple = ()`, `position_intent: Optional[str] = None` and `asset_class: Optional[str] = None`.
`AccountDTO` gains `options_trading_level: Optional[int] = None`, `options_approved_level: Optional[int] = None`, `options_buying_power: Optional[float] = None` and `non_marginable_buying_power: Optional[float] = None`.

`AlpacaAdapter.submit_order(...)` gains the keyword-only arguments `order_class=None`, `take_profit=None` (a float limit price), `stop_loss=None` (a float stop price), `position_intent=None` and `asset_class=None`. When all of them are None, the request it builds is byte-identical to today's.

The adapter's option positions live in `self._option_positions: dict[str, OptionPositionDTO]`, which `refresh_positions()` fills. The equity `self._positions` is unchanged.

## 4. Backtest simulator additions

- `SimulationOrder` gains `bracket: Optional[dict] = None` (`{"take_profit_price": float, "stop_loss_price": float}`), `whole_shares: bool = False` and `fill_at_next_open: bool = False`.
- `PortfolioEmulator.execute_signal(..., bracket=None, whole_shares=False, fill_at_next_open=False)` is forwarded only when set.
- `PortfolioEmulator.has_bracket_legs() -> bool`
- `PortfolioEmulator.process_bar_events(bars_by_symbol: dict[str, list[dict]], clock) -> list[SimulationFill]`, returning fill objects (per plan A-backtest).
- `PortfolioEmulator.has_next_open_orders() -> bool`. The broker bar-hook guard is `has_bracket_legs() or has_next_open_orders()`.
- For daily bars, `SimulationBarEvent.bar_ts` is the NYSE session OPEN of that bar. Alpaca labels daily bars at midnight ET.
- New summary counter `next_open_expired_order_count`. A next-open order gets one shot and is not counted in `unfilled_order_count`.
- `NextEventExecutionSimulator.on_bar(event: SimulationBarEvent) -> list[fill]`
- `@dataclass(frozen=True) class SimulationBarEvent: symbol: str; open: float; high: float; low: float; close: float; bar_ts: datetime; available_at: datetime`
- Fill `source` strings: `bracket_sl:<parent_id>`, `bracket_sl_gap:<parent_id>`, `bracket_tp:<parent_id>`, `bracket_tp_gap:<parent_id>`.

### 4a. Additions from plan A-backtest (verbatim from its "Contract additions")

1. **`SimulationBarEvent.bar_ts`**: the instant the bar's first trade could print, in aware UTC. For a daily equity bar it is the NYSE session open (09:30 ET); for an intraday bar it is the bar's label. `available_at` is when the whole bar is known: the session close for a daily bar, label + interval for an intraday bar.
2. **`PortfolioEmulator.process_bar_events(bars_by_symbol, clock)`**:
   - It returns `list[SimulationFill]`, not `list[dict]` as §4 says. broker.py logs `fill.side` and `fill.source`, and passes each fill to `_apply_backtest_confirmed_fill_state`, exactly as it does with `process_price_events`' fills.
   - The bar dicts are `{"t", "o", "h", "l", "c", "bar_ts", "available_at"}`: `collect_bar_events`' output.
   - A bar whose `available_at` is after `clock` raises `ValueError` (look-ahead).
3. **`NextEventExecutionSimulator.on_bar(event, *, accept_fill=None, cash_budget=None, position_of=None) -> list[SimulationFill]`**. The keywords mirror `on_quote`. `position_of(symbol)` caps a leg's sale at the shares actually held.
4. **New `NextEventExecutionSimulator` members:**
   - `cancel(order_id) -> bool`
   - properties `has_bracket_legs -> bool`, `has_next_open_orders -> bool` and `bracket_legs -> tuple[dict, ...]`. Each dict is `{"parent_id", "symbol", "qty", "stop_loss_price", "take_profit_price", "armed_from_bar_ts"}`.
   - `bar_event_requirements() -> dict[str, datetime]`
   - `bar_event_priority(event) -> int`
   - `on_quote(..., _next_open_only=False)`, a private keyword used only by `on_bar`.
5. **New `PortfolioEmulator` methods:** `has_next_open_orders() -> bool` and `bar_event_requirements() -> dict[str, datetime]`, alongside the contract's `has_bracket_legs()`. The broker guard is `has_bracket_legs() or has_next_open_orders()`. A next-open order needs the bar hook before any leg exists; §6.2 names only `has_bracket_legs()`.
6. **`SimulationFill.exit_reason: Optional[str] = None`**, with the values `"stop_loss"` and `"take_profit"` (a gap fill has the same reason; its `source` says `_gap`).
   - Left out of `as_dict()` when `None`.
   - Emulator trade rows carry `"exit_reason"` only when it is set.
7. **Leg fill order ids:** `<parent_id>:sl` and `<parent_id>:tp`. Sources are exactly §4's four.
8. **Summary keys**, present only on a run that used the feature:
   - `next_open_order_count` and `next_open_expired_order_count`, when a next-open order was submitted;
   - `bracket_order_count`, `bracket_open_leg_count`, `bracket_exit_counts` (`{"stop_loss", "stop_loss_gap", "take_profit", "take_profit_gap"}`) and `cancelled_order_count`, when a bracket was submitted.
9. **New module `backend/backtest_bar_events.py`:**
   - `execution_hint_kwargs(nexus_hint, decision) -> dict`
   - `collect_bar_events(data, requirements, clock, *, bar_time_to_datetime, bar_available_at, bar_open_at) -> dict[str, list[dict]]`
   - `make_bar_open_resolver(*, interval, session_open_resolver=None)`
   - `equity_daily_session_open(bar_start)`
   - `reset_label_cache()`
10. **broker.py:**
    - `_submit_portfolio_signal(..., execution_hints=None)`
    - `_backtest_bar_open_resolver()`
    - `_process_backtest_bar_events(portfolio, data, prices, current_time)`
11. **Semantics §6.2 leaves open, fixed here:**
    - A whole-share buy is a *quantity* order with no `notional_limit`, as live Alpaca fills a qty order. An opening gap changes its cost, not its count; the cash budget still clamps it.
    - A next-open order has one shot: its first eligible open. Whatever that open does not fill is dropped and counted in `next_open_expired_order_count`, never in `unfilled_order_count`.
    - Intrabar leg fills (rules 3–5) are stamped at the bar's `available_at`, the first instant the range is known. Gap fills (rules 1–2) are stamped at `bar_ts`.
    - Bars that share a `bar_ts` run in this order: sells at the open (a next-open sell, or a leg the open gaps through), then buys at the open, then the rest.

## 5. Tables (`backend/db/schema.py`)

| Table | Primary key | Secondary index |
|---|---|---|
| `SwingSignals` | `id` (uuid hex) | `instance_id` |
| `SwingWheelScans` | `id` (uuid hex) | `instance_id` |
| `SwingIvSnapshots` | `id` = `SYMBOL\|YYYY-MM-DD` | — |
| `SwingMacroDaily` | `id` = `VIX\|YYYY-MM-DD` | — |
| `SwingIndexMembership` | `id` = `SPX\|YYYY-MM-DD` | — |
| `SwingSectorMap` | `id` = `SYMBOL` | — |

The `SwingSignals` document:

```python
{"id", "instance_id", "lane": "swing" | "wheel", "symbol", "session": "YYYY-MM-DD",
 "created_at": iso, "score": int, "recommendation": "APPROVE"|"REVIEW"|"REJECT",
 "reasoning": str, "key_risks": [str], "size_adjustment": 1.0|0.5|0.25,
 "proposal": {  # swing: {"entry", "stop", "target", "shares"}
                # wheel: {"contract", "strike", "expiry", "qty", "limit_price", "premium_est", "delta"}
 },
 "status": "auto_approved"|"pending"|"ai_rejected"|"approved"|"approved_half"|"rejected"|"submitted"|"failed",
 "decided_by": str|None, "decided_at": iso|None, "decision_reason": str|None,
 "order_client_id": str|None, "outcome": dict|None}
```

`SwingMacroDaily`: `{"id", "series": "VIX", "date": "YYYY-MM-DD", "close": float, "source": "cboe"|"fred"}`
`SwingIndexMembership`: `{"id", "index": "SPX", "date": "YYYY-MM-DD", "members": [sym, ...]}`, with the full member list as of that change date.
`SwingSectorMap`: `{"id", "symbol", "sector": str, "as_of": "YYYY-MM-DD", "source": "yfinance"|"override"}`
`SwingIvSnapshots`: `{"id", "symbol", "date", "iv30": float|None, "iv30_alpaca": float|None}`
`SwingWheelScans`: `{"id", "instance_id", "session", "created_at", "symbol", "stock_price", "strike", "expiry", "premium_est", "score", "recommendation", "reasoning", "status": "placed"|"pending"|"rejected"|"skipped", "skip_reason"}`

## 6. Package `backend/swing_trader/` (public functions other plans call)

```python
# approvals.py
def decide(signal: dict, decision: str, user: str, reason: Optional[str], now_iso: str) -> dict
    # decision: "approve" | "approve_half" | "reject". Raises ValueError unless status == "pending".
def build_approved_order(signal: dict, *, live_price: float, equity: float, cfg: dict,
                         adapter=None, today: Optional[date] = None) -> dict
    # swing -> {"kind": "equity_bracket", "symbol", "qty", "take_profit_price", "stop_loss_price"}
    # wheel -> {"kind": "option", **option order dict (section 1 shape)}
# signals_store.py
def insert_signal(doc: dict) -> str
def list_signals(instance_id: str, status: Optional[str] = None, limit: int = 100) -> list[dict]
def get_signal(signal_id: str) -> Optional[dict]
def update_signal(signal_id: str, patch: dict) -> None
```

## 7. Engine hooks for approvals (live)

The `LiveCommands` type `submit_order` payload is `{"source": "swing_approval", "signal_id": str}`. The broker handler loads the signal, calls `swing_trader.approvals.build_approved_order(...)`, builds an `OrderIntent` (equity bracket or option), submits it through the `LiveOrderService`, then writes `status` `"submitted"` or `"failed"` and `order_client_id` back onto the signal.

## 8. Strategy ids and configuration

- Strategy ids and classes: `strategy_swing` → `StrategySwing` in `backend/strategies/strategy_swing.py`, and `strategy_wheel` → `StrategyWheel` in `backend/strategies/strategy_wheel.py`.
- Enable flags: `strategy_swing_enabled` and `strategy_wheel_enabled`.
- Model key in both: `conviction_llm_model_id`. The resolver injects `conviction_llm_provider`, `conviction_llm_model` and `conviction_llm_api_key`.
- Notification types: `swing_entry`, `swing_pending_review`, `swing_exit`, `swing_run_summary`, `wheel_put_placed`, `wheel_pending_review`, `wheel_position_alert`, `wheel_assignment`, `swing_approval_failed` (added by the plan C final review: an approved order the broker refused; push on by default; sender `notify_swing_approval_failed(instance_id, *, symbol, lane, reason)`, called by A-live Task 15).
- API routes:
  - `GET /instances/{instance_id}/swing/signals?status=`
  - `POST /instances/{instance_id}/swing/signals/{signal_id}/decision` with body `{"decision", "reason"}`
  - `GET /instances/{instance_id}/wheel`
  - `GET /instances/{instance_id}/swing/calibration`


## 9. UI-consumed shapes (pinned by plan C; plan B must return exactly these)

1. `GET /instances/{id}/swing/signals?status=pending` returns `{"signals": [<SwingSignals doc>, ...]}`. The UI also accepts a bare list, and drops rows whose `status` is not `pending`.
2. `POST .../decision` answers:
   - any 2xx on success. **200** is `{"signal", "command_id"}`;
   - **202** (fix wave FW-api-I1) when the approval is recorded but the command-queue write raised and the re-read shows the command queued, the signal already moved on, or nothing readable. The body is `{"signal", "command_id" (or null), "uncertain": true, "detail"}`. `detail` is exactly "Approval received, but its delivery to the broker could not be confirmed. Do NOT place this order by hand — it may still be queued. The card will show submitted or failed shortly." ("Re-send received, …" from the re-send route). It carries no exception text; the server logs that;
   - **400** when the signal is not pending (`approvals.decide` raises `ValueError`, which `api/main.py:_run` maps to 400);
   - **404** for an unknown signal id, or one that belongs to another instance;
   - 409 (optional) for a lost race;
   - **503** only when the approval provably did not reach the broker: the instance is not running or has crashed, or the command was not queued and the signal was put back to pending ("not queued — try again");
   - 401 or 403 from auth;
   - 422 for a malformed body.
2a. `POST /instances/{id}/swing/signals/{signal_id}/resend` (fix wave item 3) re-sends a stuck approval. It takes no body and needs a session like its neighbours. It queues the approval's own submit_order payload, `{"source": "swing_approval", "signal_id"}`, again for a signal that reads `approved` or `approved_half` with no `pending` or `running` command for it. The signal row is not changed. The broker claims approved → submitted before it sends anything, so a second copy places nothing. It answers:
   - **200** `{"signal", "command_id"}` when queued;
   - **202** with the §9 item 2 `uncertain` body when the queue write raised but may have landed;
   - **404** for an unknown signal id, or one that belongs to another instance (or an unknown instance);
   - **409** when the signal is not approved, or a command for it is still pending or running;
   - **503** when the instance is not running or has crashed, the queue cannot be read, or the command provably was not queued ("not queued — try again");
   - 401 from auth.

   The web and iOS cards offer "Re-send" on a signal that has read approved for more than 2 minutes (from `decided_at`, or from this device's last re-send). They read those signals with `?status=approved` and `?status=approved_half`, alongside the `?status=pending` list.
3. `GET /instances/{id}/wheel` returns:
   ```json
   {"open_puts": [{"contract": "APH261002P00130000", "underlying": "APH", "strike": 130.0,
                   "expiry": "2026-10-02", "qty": 1, "avg_entry_price": 1.23,
                   "current_price": 0.85, "underlying_price": 127.4,
                   "itm_pct": 2.0, "dte": 8, "collateral": 13000.0, "unrealized_pl": 38.0}],
    "collateral_total": 13000.0,
    "cash": 25000.0,
    "recent_scans": [<SwingWheelScans doc>, ...]}
   ```
   - `qty` is the count of contracts held short, as a positive integer.
   - `itm_pct = (strike − underlying_price) / strike × 100`. It is **> 0 when the put is in the money.**
   - `current_price`, `underlying_price`, `itm_pct` and `unrealized_pl` may be `null` when there is no quote.
   - `recent_scans` is newest first, at most 20 rows.
4. Live-state positions after plan A-live:
   - `qty` is signed, negative for a short option.
   - `side` is `"long"` or `"short"`.
   - `multiplier` is 100 for options.
   - `last_price`, `market_value`, `unrealized_pnl` and `unrealized_pnl_pct` may be `null` when Alpaca has no `current_price`.
   - `recent_trades` rows carry **no** `asset_class`, so the UI falls back to the OCC symbol shape for them.


## 10. Additions from plan A-live

Copied from plan A-live's "Contract additions" (Task 1 Step 0). Three items carry the controller's pre-flight rulings of 2026-09-24, marked **Ruling F1**, **Ruling F4** and **Ruling F17**; everything else is verbatim.

1. `OrderIntent.parent_client_order_id: Optional[str] = None` and `OrderIntent.broker_client_order_id: Optional[str] = None`. When `broker_client_order_id` is set, it IS the `idempotency_key`; the hash is not used. Only sources `bracket_leg` and `option_activity` may set it.
2. Identity rules the contract left open:
   - `take_profit_price` and `stop_loss_price` never join the identity dict. They are sizing, like `limit_price`, and a re-emitted entry must not re-key.
   - Every other new field joins the identity only when it differs from its default.
   - A `us_option` SELL is keyed on the session: no `decision_minute`, `quantity` or `reduce_only`. This is spec section 9 fix 1: a rerun cannot mint a second sell-to-open of the same contract in one session.
   - `take_profit_price`, `stop_loss_price` and `strike` are normalized to `Decimal` on construction (callers may pass floats), like `limit_price`.
3. `OrderSource.BRACKET_LEG = "bracket_leg"` (the contract's `BRACKET_LEG`, also exported as the module constant `live_orders.BRACKET_LEG`) and `OrderSource.OPTION_ACTIVITY = "option_activity"` (option assignment fills).
4. `DependencySnapshot` gains `asset_class: str = "us_equity"`, `regular_session_open: Optional[bool] = None`, `account_equity: Optional[Decimal] = None`, `open_short_put_collateral: Optional[Decimal] = None` (None = unknown), `pending_sell_to_open_collateral: Optional[Decimal] = None` (None = unknown, which the options gate refuses; L3 review M3), `underlying_put_collateral: Optional[Decimal] = None` (existing short puts plus pending sell-to-open puts on the intent's underlying, this intent excluded) and `max_underlying_collateral_fraction: Decimal = 0.25`.
5. `ConfirmedFill` gains `asset_class: str = "us_equity"` and `contract_multiplier: int = 1`.
6. `OrderRef` also gains `order_type: Optional[str] = None`, `limit_price: Optional[float] = None` and `stop_price: Optional[float] = None`. That is how a take-profit leg (limit) is told from a stop-loss leg (stop).
7. `PositionDTO` gains `asset_class`, `side`, `unrealized_pl`, `unrealized_plpc`, `current_price`, `underlying`, `option_type`, `strike` and `expiry` (all `Optional`, default None) and `multiplier: int = 1`. They are filled only for option rows. **Ruling F4:** `option_type` (`"put"` or `"call"`, from Alpaca's contract fields, never an OCC-symbol parse) is on every option position: `OptionPositionDTO.option_type` (section 3) and `PositionDTO.option_type`.
8. `broker_adapters.base`:
   - `BrokerAdapter.get_option_chain(underlying, *, option_type=None, expiration_gte=None, expiration_lte=None, strike_gte=None, strike_lte=None) -> dict[str, OptionSnapshotDTO]`. Spec 6.1 lists it; the contract omitted it.
   - The pure helpers `is_bracket_child_order(order) -> bool` and `is_risk_reducing_order(order) -> bool`.
     **Ruling F1:** `is_risk_reducing_order` is True for a bracket child leg (`is_bracket_child_order`: a multi-leg `order_class`) or for an order whose `asset_class` is `us_option` AND whose `position_intent` is `buy_to_close` or `sell_to_close`. A stock order's `position_intent` is ignored: Alpaca may tag stock orders with one, and a halt or kill rung on alpaca-main must keep cancelling EB's working sells exactly as today.
   - `get_daily_bars(symbols, days)` takes `days` as a **calendar-day lookback**.
9. `broker_adapters.errors.OptionsNotPermitted(BrokerError)`, which is definitive and non-retryable.
10. `AlpacaAdapter` gains `option_contract_meta(symbol) -> Optional[OptionContractDTO]` (cached contract lookup) and `_option_positions_complete: bool`. It also gains `cancel_orders_confirmed(order_ids, timeout_s=10.0, *, poll_interval_s=0.5, sleep=time.sleep, clock=time.monotonic)`; the extra keywords are test hooks. It returns **False if any order ends filled or partially filled**, because the position changed under the caller.
11. `LiveOrderService(..., legs_lookup=None)` plus:
    - `register_bracket_legs(parent_intent, parent_reference) -> tuple[LifecycleRecord, ...]`
    - `ensure_bracket_legs() -> int`
    - `record_external_fill(intent, *, broker_order_id, quantity, price, occurred_at, reason="") -> EventApplication`
    - the module function `live_orders.service.bracket_leg_intent(parent, leg) -> OrderIntent`
12. `broker.py` module functions:
    - `_lane_enabled`, `_build_bracket_intent`, `_cancel_bracket_legs_confirmed`
    - `_build_option_intent`, `_refresh_option_quote`, `_live_option_quotes` (dict)
    - `_live_option_dependency_snapshot`, `_pending_sell_to_open_collateral`, `_execute_option_intents`
    - `_poll_option_activities`, `_wheel_options_refusal`, `_execute_swing_approval`, `_approval_live_price`, `_lane_config`, `_option_position_payload`
    - module state (L5): `_OPTION_ACTIVITY_TYPES`, `_option_activity_last_poll` (the activities throttle, 300 s), `_wheel_options_check` (the process's one options-level verdict) and `_auto_close_alerts` (close-failure alerts sent this New York session, keyed contract and kind)
13. Strategy-cache keys that the engine writes under `_strategy_cache["strategy_wheel"]`:
    - `_engine_option_activity_cursor`, shaped `{"after": "YYYY-MM-DD", "seen": [ids]}`. `after` is one day before the newest activity handled, and never later than one day before an assignment still to be retried.
    - `_engine_wheel_assignments`, a list of `{"activity_id", "contract", "underlying", "shares", "side", "strike", "date"}`. The wheel lane (plan B) reads this list to know which shares it owns. An entry is added, once per `activity_id`, whenever the assignment's lifecycle row is FILLED, so a replay after a lost cache lists it again; `side` is `"buy"` for a put and `"sell"` for a call.
    - The assignment is recorded through `record_external_fill` with source `option_activity`, Alpaca's activity id as `broker_order_id`, and the key `opasn-<sha256(activity id)[:32]>`.
    - The assignment notification goes through plan B's `swing_trader.notify.notify_wheel_assignment(instance_id, *, symbol=<underlying>, qty=<shares>, price=<strike>, date="YYYY-MM-DD")` when that module is importable, and through `notifications.notify(category="wheel_assignment", ...)` when it is not.
14. For every option order that carries a `signal_id`, the engine writes `{"status": "submitted"|"failed", "order_client_id"}` back through `swing_trader.signals_store.update_signal`. This covers strategy-emitted orders, not only approvals.
15. Engine refusal note for plan B: the engine never retries an exit it deferred. That happens when a bracket-leg cancel did not confirm, or a sell floored to zero. **The swing lane must re-emit its RSI exit on later ticks while the position is still held.**
16. Live-state position rows (interfaces section 9, item 4; the coordinator pinned this on 2026-09-24):
    - **Option rows** written by `broker.py`'s LiveState snapshot, and **every row** served by `live_broker_fetch`, carry `asset_class`, `side` (`"long"` or `"short"`), `multiplier` (100 for options, 1 for equities), `underlying`, `strike` and `expiry`. `qty` is signed. **Ruling F4:** option rows also carry `option_type` (`"put"` or `"call"`), so the UI reads the contract fields instead of parsing the OCC symbol.
    - `last_price`, `market_value`, `unrealized_pnl` and `unrealized_pnl_pct` are `null` when Alpaca gives no value. They are never invented as 0.
    - **Equity rows written by `broker.py` keep their exact pre-change shape**, with none of these keys, because that code runs inside EB's real-money process every few seconds. The UI must read a missing `asset_class` as `"us_equity"`, a missing `multiplier` as 1, and a missing `side` from the sign of `qty`.
    - `live_broker_fetch` `recent_trades` rows gain `asset_class`. Rows written by `broker.py` still carry none, so the UI keeps its OCC-shape fallback for them. **Ruling F17:** this refines section 9 item 4, whose "`recent_trades` rows carry no `asset_class`" now holds only for the rows `broker.py` writes.
    - `close_position` refuses option contracts with a clear message ("<symbol> is an option contract; option contracts are closed by buy-to-close, not close_position"): a contract in the adapter's option book, or any OCC-shaped symbol.
    - L5 additions. `live_broker_fetch` equity rows carry `option_type`, `underlying`, `strike` and `expiry` as `null`, so every served row has one key set. When Alpaca's positions cannot be read (the call raises, the answer is not a list, or an option row is unreadable) the fetch fails with `broker_fetch_error` = `"positions_unavailable: <cause>"` and serves no `positions`; the API then serves the container's row and marks the state `stale`. It is never an empty book (plan B G8a re-review: the wheel page must not read an outage as "no open puts"). An unreadable equity row is skipped as before.
    - L5 addition. During a positions outage the `broker.py` snapshot also carries each contract from the adapter's last-known option book, with `last_price`, `market_value`, `unrealized_pnl` and `unrealized_pnl_pct` set to `null`.

## 11. Additions from plan B

- `swing_trader.constants.SWING_DEFAULTS` and `WHEEL_DEFAULTS`: the header defaults. The six swing `live_*` values equal A-live's `_SW_DEFAULTS` row (0.2 / 0.2 / 0.2 / 0.25 / 0.35 / 0.45).
- Intent labels `swing_stop_exit` and `swing_target_exit` (ST `exit_signal` close-based stop and target).
- The live swing entry hint has no share count: `buy_cash = max(min(equity × 0.125, buying power) × size_adjustment, prior close)`; the engine buys `floor(buy_cash / live price)`. Approval orders carry `qty` (`approvals.build_approved_order`).
- Swing strategy-cache key `_swing_pending_exits` (`{symbol: {"reason", "intent", "since"}}`): an emitted exit is re-emitted on every later tick while the stock is held with no working non-bracket sell (answers A-live addition 15).
- Option order dict key `"session": "YYYY-MM-DD"`, informational (A-live keys option sells on the session itself).
- The wheel lane reads `_engine_wheel_assignments` (A-live addition 13) from its strategy cache: only assigned shares are covered-call candidates.
- `SwingSignals` may carry `score: None` (AI gate off), `context: dict`, and `error` on a `failed` row the lane wrote (A-live's approval handler writes only `status` and `order_client_id`).
- `swing_trader.signals_store`: `signal_id_for(instance_id, lane, session, symbol) -> str`, `new_signal(**fields) -> dict`, `cas_signal(signal_id, *, expect_status, doc) -> bool`, `swing_owned_symbols(instance_id, held) -> set[str]`, `insert_wheel_scan(row) -> str`, `list_wheel_scans(instance_id, limit=50) -> list[dict]`, `all_signals(instance_id) -> list[dict]`, `ensure_tables() -> None`, `OPEN_STATUSES`.
- `swing_trader.approvals.SignalConflict(ValueError)`: a decision on a signal that is not pending; `_run` maps it to 400 per §9 item 2. A click that lost the compare-and-swap: `interactive_utils.SwingDecisionRaceError` → 409. An approval that provably cannot reach the broker, or an unreadable wheel book: `interactive_utils.SwingBrokerUnavailableError` → 503, and the signal is pending. A queue write that raised but may have landed is not a 503: it is the §9 item 2 202 `uncertain` answer (fix wave FW-api-I1). A refused re-send (§9 item 2a): `interactive_utils.SwingResendConflictError` → 409. Unknown or foreign signal id: `LookupError` → 404.
- `swing_trader.account`: read-only book views shared by both lanes (`equity_positions`, `option_symbols`, `live_equity`, `live_buying_power`, `option_positions`, `open_orders`, `account_options`, `spendable`, `pending_symbols`, `entry_price_from_trades`).
- The approval command handler (§7) is plan A-live Task 15's `_execute_swing_approval`; plan B does not edit `broker.py`.
- `swing_trader.notify.send(category, instance_id, title, message, *, priority=0)` and `notify_wheel_assignment(instance_id, *, symbol, qty, price=None, date=None)` (A-live's activities poller calls the latter).
- `llm_utils.call_llm_with_web_search(provider, api_key, model, prompt, *, max_output_tokens=300, max_uses=2, timeout_sec=None, provider_config=None) -> str`.
- What plan B relies on from the live engine:
  - (a) Option position type: A-live's live position rows carry `option_type` (A-live pre-flight F4 adds it) in `live_broker_fetch.fetch_broker_live_state`; B still falls back to the OCC symbol.
  - (b) Price marks: entries are price-marked by the existing broker helper `_ensure_live_candidate_marks` (A-live adds no marking).

- SwingSignals `outcome` for a swing entry that never filled within 2 NY sessions (plan B G8a, ruling 4): `{"unfilled": true, "as_of": "YYYY-MM-DD", "sessions_waited": int}`. The status is unchanged and no `pnl` is recorded, so it never counts as a round trip. Entries still working at the broker (GTC brackets) are never closed as unfilled.

### Deploy fingerprint hand-off (plan B Task 25 → A-live Task 16)

Source: `git diff --name-status $(git merge-base main HEAD)..HEAD -- backend`, test files excluded, at `c6b5771d` (2026-09-25). There are 41 changed backend `.py` files across plans A-backtest, A-live and B; plan C changes no backend file. Paths are backend-relative, as `_CODE_FINGERPRINT_FILES` lists them. `scripts/check_deployed_code.py`'s `FILES` takes the same paths prefixed with `backend/`. Scripts are not deployed.

Already fingerprinted on both sides (7), so no change: `broker.py`, `broker_adapters/alpaca.py`, `api/main.py`, `llm_utils.py`, `simulated_execution.py`, `portfolio_emulator.py`, `backtest_bar_events.py`.

To add to both lists (34):
- plan A-live (11): `broker_adapters/base.py`, `broker_adapters/errors.py`, `live_broker_fetch.py`, `live_orders/__init__.py`, `live_orders/types.py`, `live_orders/store.py`, `live_orders/gate.py`, `live_orders/service.py`, `live_orders/reconcile.py`, `live_pending_orders.py`, `live_risk_state.py`;
- plan B, strategies (2): `strategies/strategy_swing.py`, `strategies/strategy_wheel.py`;
- plan B, package (18): `swing_trader/__init__.py`, `swing_trader/account.py`, `swing_trader/ai_analyst.py`, `swing_trader/approvals.py`, `swing_trader/calibration.py`, `swing_trader/clock.py`, `swing_trader/constants.py`, `swing_trader/indicators.py`, `swing_trader/iv.py`, `swing_trader/market_data.py`, `swing_trader/notify.py`, `swing_trader/refdata.py`, `swing_trader/regime.py`, `swing_trader/sectors.py`, `swing_trader/signals.py`, `swing_trader/signals_store.py`, `swing_trader/universe.py`, `swing_trader/wheel_rules.py`;
- plan B, shared modules (3): `db/schema.py`, `interactive_utils.py`, `notification_types.py`.

Compared with A-live Task 16's `EXPECTED` and its paste block, five entries are missing there: `swing_trader/account.py`, `clock.py`, `notify.py` and `refdata.py` (ruling F2), and `live_orders/__init__.py`, which A-live's own commit 786af5a2 changed. Two entries share the basename `__init__.py` (`live_orders/` and `swing_trader/`), so both sides key them by full path. The existing duplicate-basename rule already does that.
