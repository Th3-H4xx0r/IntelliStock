# swing-trader port — shared interface contract

Every plan for the swing-trader port (A-live, A-backtest, B, C) uses these names and shapes **exactly**. If a plan needs a name that is not here, it adds it here first.

**Brought in line with the merged code on 2026-09-25** (fix wave FW1-FW3, the seams review's 22 drift items, and the final fix round). Text marked **Fix wave** records what the fix wave built; **Final round** marks the last round's items (seams I-1, I-2, m1-m6).

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
               "fill_at_next_open": True},        # entry: backtest-only; a live entry ignores it
      # swing exit (RSI cross; also the close-based stop and target):
      "MSFT": {"sell_fraction": 1.0, "fill_at_next_open": True},  # live: a market DAY sell
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

Intent labels, all plain strings: `swing_entry`, `swing_rsi_exit`, `swing_defensive_entry`, `wheel_sto_put`, `wheel_btc_itm`, `wheel_btc_2x`, `wheel_btc_expiry`, `wheel_sto_call` (and §11's `swing_stop_exit`, `swing_target_exit`).

**Fix wave (FW-str-I1; seams drift 1): a live swing EXIT reads `fill_at_next_open`.**
- The live broker treats a sell as a swing exit when its hint sets `fill_at_next_open` (exactly `True`) or its action intents name `swing_rsi_exit`, `swing_stop_exit` or `swing_target_exit` (`broker._SWING_EXIT_INTENTS`, read by `_swing_next_open_exit(hint, intents)`). A re-sent pending exit carries its intent but not the hint.
- A swing exit is a plain market DAY sell, `_build_strategy_stock_intent(..., next_open_sell=True)`, never the extended-hours limit the session style makes before 09:30 ET. Alpaca queues it for the open.
- It goes out through `_submit_swing_sell`: its bracket legs are cancelled only after the gate allows the sell, inside the service's `before_submit` hook (§10 item 11), so a refused sell leaves its stop working.
- Between the close and 20:00 ET it is held (§10 item 15). A live swing ENTRY still ignores `fill_at_next_open`. EB's hints and intents carry neither.

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

`ConfirmedFill` (same module) gains `asset_class: str = "us_equity"` and `contract_multiplier: int = 1` (§10 item 5). **Fix wave (FW1 follow-up)** adds four optional option fields, all `None` by default:

```python
underlying: Optional[str] = None
option_type: Optional[str] = None      # "put" | "call"
strike: Optional[Decimal] = None
expiry: Optional[str] = None           # "YYYY-MM-DD"
```

They carry the contract of a fill on an option order IntelliStock placed, taken from its intent, so the adapter's option map can type a brand-new position row at once. They are `None` on every equity fill (EB's included) and on a fill of unknown origin.

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
def cancel_orders_confirmed(self, order_ids: list[str], timeout_s: float = 10.0, *,
                            booked_fills: Optional[dict] = None) -> bool: ...
    # booked_fills: {order_id: quantity already booked} (fix wave FW1 item 8; §10 item 10)
def option_positions_health(self) -> dict: ...
    # {"complete": bool, "stale_since": Optional[float]}  (plan B G7 review I-1)
def list_closed_orders(self, symbols: list[str], after: str) -> list["OrderRef"]: ...
def get_daily_bars(self, symbols: list[str], days: int) -> dict[str, list[dict]]: ...
    # bars: [{"t","o","h","l","c","v"}], oldest first, SIP, adjustment=all, batches <= 100
def get_latest_trades(self, symbols: list[str]) -> dict[str, tuple[float, str]]: ...
```

`OrderRef` gains the fields `order_class: Optional[str] = None`, `legs: tuple = ()`, `position_intent: Optional[str] = None` and `asset_class: Optional[str] = None`.
`AccountDTO` gains `options_trading_level: Optional[int] = None`, `options_approved_level: Optional[int] = None`, `options_buying_power: Optional[float] = None` and `non_marginable_buying_power: Optional[float] = None`.

`AlpacaAdapter.submit_order(...)` gains the keyword-only arguments `order_class=None`, `take_profit=None` (a float limit price), `stop_loss=None` (a float stop price), `position_intent=None` and `asset_class=None`. When all of them are None, the request it builds is byte-identical to today's.

The adapter's option positions live in `self._option_positions: dict[str, OptionPositionDTO]`, which `refresh_positions()` fills. The equity `self._positions` is unchanged.

**Seams drift 4: `option_positions_health()`.**
- `AlpacaAdapter.option_positions_health()` returns a fresh `{"complete": bool, "stale_since": Optional[float]}` on every call.
  - `complete` is `_option_positions_complete`. It is False until a positions refresh has read every option row, and §10 item 10 lists what else makes it False.
  - `stale_since` is `_positions_stale_since`: the epoch second the REST refresh started failing, None while refreshes succeed.
- The base class raises `NotImplementedError`, which every caller reads as "unknown", never as a complete, empty book.
- `swing_trader.account.positions_health` fails closed on a refusal, on a raise, on an answer that is not a dict, and on a dict with no `stale_since` key (plan B G8b minor 1). A dict without `complete: True` reads incomplete.

## 4. Backtest simulator additions

- `SimulationOrder` gains `bracket: Optional[dict] = None` (`{"take_profit_price": float, "stop_loss_price": float}`), `whole_shares: bool = False` and `fill_at_next_open: bool = False`.
- `PortfolioEmulator.execute_signal(..., bracket=None, whole_shares=False, fill_at_next_open=False)` is forwarded only when set.
- `PortfolioEmulator.has_bracket_legs() -> bool`
- `PortfolioEmulator.process_bar_events(bars_by_symbol: dict[str, list[dict]], clock) -> list[SimulationFill]`, returning fill objects (per plan A-backtest).
- `PortfolioEmulator.has_next_open_orders() -> bool`. The broker bar-hook guard is `has_bracket_legs() or has_next_open_orders()`.
- For daily bars, `SimulationBarEvent.bar_ts` is the NYSE session OPEN of that bar. Alpaca labels daily bars at midnight ET.
- New summary counter `next_open_expired_order_count`. A next-open order gets one shot and is not counted in `unfilled_order_count`. It also expires when its symbol prints no bar for `NEXT_OPEN_MAX_SESSIONS` sessions (§4a item 12).
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
   - **Fix wave:** `expire_next_open_orders(is_stale) -> tuple[SimulationOrder, ...]` (item 12).
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
   - **Fix wave:** `completed_sessions_after(after, now) -> int` (item 12).
10. **broker.py:**
    - `_submit_portfolio_signal(..., execution_hints=None)`
    - `_backtest_bar_open_resolver()`
    - `_process_backtest_bar_events(portfolio, data, prices, current_time)`
11. **Semantics §6.2 leaves open, fixed here:**
    - A whole-share buy is a *quantity* order with no `notional_limit`, as live Alpaca fills a qty order. An opening gap changes its cost, not its count; the cash budget still clamps it.
    - A next-open order has one shot: its first eligible open. Whatever that open does not fill is dropped and counted in `next_open_expired_order_count`, never in `unfilled_order_count`. Item 12 adds the one other way it ends: no bar at all.
    - Intrabar leg fills (rules 3–5) are stamped at the bar's `available_at`, the first instant the range is known. Gap fills (rules 1–2) are stamped at `bar_ts`.
    - Bars that share a `bar_ts` run in this order: sells at the open (a next-open sell, or a leg the open gaps through), then buys at the open, then the rest.
12. **Fix wave (FW-bt minors 1 and 2; seams drift 16):**
    - A next-open order whose symbol prints no bar for `portfolio_emulator.NEXT_OPEN_MAX_SESSIONS` (5) completed NYSE sessions after its decision (a delisting, an acquisition, a data gap) is cancelled. Its reservation (a buy's cash, a sell's shares) is freed, a yellow `NEXT-OPEN EXPIRED` line is logged, and it is counted in `next_open_expired_order_count`.
      - The emulator judges this on its own clock, inside `bar_event_requirements()`.
      - It counts sessions with `backtest_bar_events.completed_sessions_after(after, now)`: NYSE sessions that opened after `after` and had closed by `now`. The fallback without the calendar library is weekdays 09:30-16:00 ET.
      - It drops the orders through `NextEventExecutionSimulator.expire_next_open_orders(is_stale)`. A shorter halt still fills at the reopen.
    - `collect_bar_events` skips, with one yellow warning per (symbol, bar), a bar `SimulationBarEvent` would refuse: an open, high, low or close that is NaN, infinite, zero or negative; a high below its low; or an `available_at` before its `bar_ts`. Raising would abort the run on every retry, since the bar never changes. The symbol's later bars still arrive. `reset_label_cache()` also forgets the warnings.

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
 "order_client_id": str|None, "outcome": dict|None,
 # written by the approval handler (§10 item 17), absent until then:
 "claimed_at": iso,             # when the handler claimed approved -> submitted (fix wave FW1 item 9)
 "submitted_order": dict}       # the order build_approved_order rebuilt at the live price (§6)
```

`submitted_order` is written when the order is accepted, or when the outcome is unknown. `claimed_at` is the stale-row sweep's age stamp (§10 item 19). `order_client_id` is written only once the broker was sent the order (accepted, or an answer with no broker reference). The UIs settle a waiting card on `submitted` only when the row carries it (§9 item 2). A lane may also write `context`, and the wheel lane writes `error` on a failed row (§11).

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
    # BOTH lanes need `adapter` (a swing entry reads its budget through it; Final round, I-1)
class BookUnreadable(ValueError)              # the working-order book or the wheel's option map: transient
class OptionBookUnreadable(BookUnreadable)    # Final round, I-1: a swing entry's put collateral is unknown
# signals_store.py
def insert_signal(doc: dict) -> str
def list_signals(instance_id: str, status: Optional[str] = None, limit: int = 100) -> list[dict]
def get_signal(signal_id: str) -> Optional[dict]
def update_signal(signal_id: str, patch: dict) -> None
```

**Final round (seams I-1): an approved swing entry spends the lane's budget.**
- `build_approved_order` caps a swing entry's share count at `floor(budget / live_price)`. The budget is `swing_trader.account.swing_live_budget(adapter, book)`, the rule the lane's own entries use (§11), and `book` is the strict working-order read.
- The rebuilt count is therefore `min(ST's equity × position_size_pct × size_adjustment [÷ 2 for approve_half], the budget)` in whole shares at the live price, and the cash securing the wheel's short puts stays unspent.
- A working-order book that cannot be read raises `BookUnreadable`. An option book that is unreadable, incomplete or stale, or that holds a short with an unknown type, underlying or strike, raises `OptionBookUnreadable`. Both are transient (§10 item 17).
- A budget that buys no whole share (for example negative cash) is a definite `ValueError`: "the swing budget $X buys no whole share of SYM at $P".
- The shared equity gate is unchanged.

## 7. Engine hooks for approvals (live)

The `LiveCommands` type `submit_order` payload is `{"source": "swing_approval", "signal_id": str}`. The broker handler loads the signal, calls `swing_trader.approvals.build_approved_order(...)`, builds an `OrderIntent` (equity bracket or option), submits it through the `LiveOrderService`, then writes `status` `"submitted"` or `"failed"` and `order_client_id` back onto the signal. Section 10 item 17 gives the full handler as built:
- the day rule and the claim;
- the after-close hold;
- the control re-read;
- the write-back, including `submitted_order` and the reset to `pending` on a transient failure.

## 8. Strategy ids and configuration

- Strategy ids and classes: `strategy_swing` → `StrategySwing` in `backend/strategies/strategy_swing.py`, and `strategy_wheel` → `StrategyWheel` in `backend/strategies/strategy_wheel.py`.
- Enable flags: `strategy_swing_enabled` and `strategy_wheel_enabled`.
- Model key in both: `conviction_llm_model_id`. The resolver injects `conviction_llm_provider`, `conviction_llm_model` and `conviction_llm_api_key`.
- Notification types: `swing_entry`, `swing_pending_review`, `swing_exit`, `swing_run_summary`, `wheel_put_placed`, `wheel_pending_review`, `wheel_position_alert`, `wheel_assignment`, `swing_approval_failed`.
  - `swing_approval_failed` was added by the plan C final review for an approved order the broker refused. It pushes by default. Its label and description, "Approved order refused or unconfirmed" and "A swing or wheel order you approved was not sent, may not have been placed, or WAS placed though its signal reads failed", were widened in the final round (seams m5). It carries three senders, all in `swing_trader.notify`:
    - `notify_swing_approval_failed(instance_id, *, symbol, lane, reason)` (A-live Task 15), priority 1: a refusal, and every "— approve again" reset;
    - `notify_swing_approval_unconfirmed(instance_id, *, symbol, lane, detail)` (fix wave FW1 item 9), priority 1: "Approved <lane> order unconfirmed" — may not have been placed, check open orders;
    - `notify_swing_order_placed_for_failed(instance_id, *, symbol, lane, client_order_id)` (fix wave round 2, minor 3), priority 2: "Approved <lane> order WAS placed" — do not place it by hand.
  - `swing_exit` also carries the broker's priority-2 exit alerts, "EXIT NOT PLACED", "EXIT OUTCOME UNKNOWN" and "position may be unprotected" (§10 item 15). Its description now says so: "The swing lane sold a position; or an exit was not placed or its outcome is unknown, so the position may be unprotected" (seams m5).
  - `wheel_assignment` pushes by default (fix wave item 4).
  - The web (`frontend/src/utils/notificationFallback.js`) and mobile (`notification_prefs.dart`) fallback lists match `notification_types.py` in key, order, label and description.
- API routes:
  - `GET /instances/{instance_id}/swing/signals?status=`
  - `POST /instances/{instance_id}/swing/signals/{signal_id}/decision` with body `{"decision", "reason"}`
  - `POST /instances/{instance_id}/swing/signals/{signal_id}/resend`, with no body (fix wave item 3; §9 item 2a)
  - `GET /instances/{instance_id}/wheel`
  - `GET /instances/{instance_id}/swing/calibration`
- Lab document (`scripts/swing_lab_setup.py`; seams drift 22):
  - The lab's swing lane sets `backtest_credit_sell_proceeds_enabled: true` beside `backtest_credit_pending_sell_proceeds: true`. The first lets the broker's buy gate count a same-tick exit's proceeds (FW-bt-I1: without it the gate clamps the entry to raw cash); the second lets the emulator count them.
  - `swing_lab_setup` never links a live brokerage. A new lab instance takes strategy-eb's brokerage only when that is provably a paper Alpaca account that is not alpaca-main's, the proof `--paper` demands; otherwise it is created with no brokerage and the reason is printed. An existing lab instance keeps its brokerage; one that is not provably paper is flagged, never changed.


## 9. UI-consumed shapes (pinned by plan C; plan B must return exactly these)

1. `GET /instances/{id}/swing/signals?status=pending` returns `{"signals": [<SwingSignals doc>, ...]}`. The UI also accepts a bare list, and drops rows whose `status` is not `pending`.
   - **Seams drift 17: before a lane has created its tables.** The list route answers `{"signals": []}`, the wheel route answers with `recent_scans: []`, and calibration returns an empty report (`calibration_report(rows=[])`). Decision and re-send answer 404, as for an unknown signal. Any other store failure still raises (`interactive_utils._swing_read`).
2. `POST .../decision` answers:
   - any 2xx on success. **200** is `{"signal", "command_id"}`;
   - **202** (fix wave FW-api-I1) when the approval is recorded but the command-queue write raised and the re-read shows the command queued, the signal already moved on, or nothing readable. The body is `{"signal", "command_id" (or null), "uncertain": true, "detail"}`. `detail` is exactly "Approval received, but its delivery to the broker could not be confirmed. Do NOT place this order by hand — it may still be queued. The card will show submitted or failed shortly." ("Re-send received, …" from the re-send route). It carries no exception text; the server logs that. The UIs keep the signal on a "Waiting for the broker" card with an "uncertain — waiting for the broker" badge and that text. The rules for that card:
     - While a card waits, each poll also reads `?status=submitted` and `?status=failed`. Only a poll begun after the 202 may settle it.
     - Pending: the card goes, and the pending card is back.
     - Failed: the badge says failed until Dismiss.
     - **Final round (seams I-2):** submitted settles the card only when the row carries `order_client_id`. The broker writes `submitted` when it CLAIMS the approval, before it sends anything, and writes the key only once the order was sent. A claim can still go back to `pending` (a transient refusal) or be swept to `failed`. A `submitted` row without the key keeps the card waiting, and a later poll can still find it pending or failed. A settled `submitted` card reads "The broker submitted it. Check open orders for the fill." until Dismiss. Web: `swing.js` `foldUncertain`. iOS: `PendingSignalsNotifier._foldUncertain` in `swing_controller.dart`, with `SwingSignal.orderClientId`, where an empty key reads as null.
     - **Round 3 FU-1 (seams drift 18):** a card that still reads approved more than 2 minutes (`STUCK_AFTER_MS` / `stuckAfter`) after its 202 leaves the waiting state and joins the stuck list, where Re-send and Dismiss are. A joined card stays listed while it reads approved, whatever its server-clock age. A waiting card offers Dismiss after 2 minutes in any case, and a settled one at once;
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
   - **409** when the signal is not approved, when it was approved on a day other than today in New York (its `decided_at` converted to the New York date, not its `session`, which for a wheel signal is the weekly scan day: "this approval was made on <date>; approve a fresh signal instead"), or when a command for it is still pending or running;
   - **503** when the instance is not running or has crashed, the queue cannot be read, or the command provably was not queued ("not queued — try again");
   - 401 from auth.

   The web and iOS cards offer "Re-send" on a signal approved today (the same New York `decided_at` rule; the server alone decides the 409) that has read approved for more than 2 minutes (from `decided_at`, or from this device's last re-send); an older one shows the reason instead. They read those signals with `?status=approved` and `?status=approved_half`, alongside the `?status=pending` list.
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
   - `recent_scans` is newest first, at most 20 rows, and `[]` before the wheel lane has created its table (item 1).
4. Live-state positions after plan A-live:
   - `qty` is signed, negative for a short option.
   - `side` is `"long"` or `"short"`.
   - `multiplier` is 100 for options.
   - `last_price`, `market_value`, `unrealized_pnl` and `unrealized_pnl_pct` may be `null` when Alpaca has no `current_price`.
   - `recent_trades` rows carry **no** `asset_class`, so the UI falls back to the OCC symbol shape for them.


## 10. Additions from plan A-live

Copied from plan A-live's "Contract additions" (Task 1 Step 0). Three items carry the controller's pre-flight rulings of 2026-09-24, marked **Ruling F1**, **Ruling F4** and **Ruling F17**. Text marked **Drift (T16)** was added on 2026-09-25 to record what A-live built beyond the plan (Task 15 review, M-4). Text marked **Fix wave** or **Final round** records the later work (see the header); items 18 and 19 are new. Everything else is verbatim.

1. `OrderIntent.parent_client_order_id: Optional[str] = None` and `OrderIntent.broker_client_order_id: Optional[str] = None`. When `broker_client_order_id` is set, it IS the `idempotency_key`; the hash is not used. Only sources `bracket_leg` and `option_activity` may set it.
2. Identity rules the contract left open:
   - `take_profit_price` and `stop_loss_price` never join the identity dict. They are sizing, like `limit_price`, and a re-emitted entry must not re-key.
   - Every other new field joins the identity only when it differs from its default.
   - A `us_option` SELL is keyed on the session: no `decision_minute`, `quantity` or `reduce_only`. This is spec section 9 fix 1: a rerun cannot mint a second sell-to-open of the same contract in one session.
   - `take_profit_price`, `stop_loss_price` and `strike` are normalized to `Decimal` on construction (callers may pass floats), like `limit_price`.
3. `OrderSource.BRACKET_LEG = "bracket_leg"` (the contract's `BRACKET_LEG`, also exported as the module constant `live_orders.BRACKET_LEG`) and `OrderSource.OPTION_ACTIVITY = "option_activity"` (option assignment fills).
4. `DependencySnapshot` gains `asset_class: str = "us_equity"`, `regular_session_open: Optional[bool] = None`, `account_equity: Optional[Decimal] = None`, `open_short_put_collateral: Optional[Decimal] = None` (None = unknown), `pending_sell_to_open_collateral: Optional[Decimal] = None` (None = unknown, which the options gate refuses; L3 review M3), `underlying_put_collateral: Optional[Decimal] = None` (existing short puts plus pending sell-to-open puts on the intent's underlying, this intent excluded) and `max_underlying_collateral_fraction: Decimal = 0.25`.
   - **Fix wave (FW-lo-I5; seams drift 7): negative cash.**
     - The equity snapshot (`_live_order_dependency_snapshot`) reads negative cash as 0 only for a reduce-only intent on a document with an enabled swing or wheel lane. Every other case still raises, EB's included, since doc 200 enables neither lane.
     - The option snapshot (`_live_option_dependency_snapshot`) reads it as 0 for any reduce-only option intent. An opening order still fails closed.
     - The option gate no longer applies `cash.insufficient` to a `buy_to_close`: it is risk-reducing (Ruling F1), and Alpaca enforces its buying power.
   - **Fix wave (FW-lo-I3):** the option snapshot's collateral is unknown (`None`) while the adapter's map is incomplete, while pending sell-to-opens cannot be read, or while any short row's type, underlying or strike is unknown (`meta_known`). `swing_trader.account.put_collateral` follows the same rule (§11; Final round, m3).
5. `ConfirmedFill` gains `asset_class: str = "us_equity"` and `contract_multiplier: int = 1`. The fix wave adds `underlying`, `option_type`, `strike` and `expiry`, all optional (§2).
6. `OrderRef` also gains `order_type: Optional[str] = None`, `limit_price: Optional[float] = None` and `stop_price: Optional[float] = None`. That is how a take-profit leg (limit) is told from a stop-loss leg (stop).
7. `PositionDTO` gains `asset_class`, `side`, `unrealized_pl`, `unrealized_plpc`, `current_price`, `underlying`, `option_type`, `strike` and `expiry` (all `Optional`, default None) and `multiplier: int = 1`. They are filled only for option rows. **Ruling F4:** `option_type` (`"put"` or `"call"`, from Alpaca's contract fields, never an OCC-symbol parse) is on every option position: `OptionPositionDTO.option_type` (section 3) and `PositionDTO.option_type`.
8. `broker_adapters.base`:
   - `BrokerAdapter.get_option_chain(underlying, *, option_type=None, expiration_gte=None, expiration_lte=None, strike_gte=None, strike_lte=None) -> dict[str, OptionSnapshotDTO]`. Spec 6.1 lists it; the contract omitted it.
   - The pure helpers `is_bracket_child_order(order) -> bool` and `is_risk_reducing_order(order) -> bool`.
     **Ruling F1:** `is_risk_reducing_order` is True for a bracket child leg (`is_bracket_child_order`: a multi-leg `order_class`) or for an order whose `asset_class` is `us_option` AND whose `position_intent` is `buy_to_close` or `sell_to_close`. A stock order's `position_intent` is ignored: Alpaca may tag stock orders with one, and a halt or kill rung on alpaca-main must keep cancelling EB's working sells exactly as today.
   - `get_daily_bars(symbols, days)` takes `days` as a **calendar-day lookback**.
   - **Fix wave (FW-lo-I4; seams drift 5):** the pure helper `is_opening_option_sell(order) -> bool`. It is True for an order whose `asset_class` is `us_option` AND whose `position_intent` is `sell_to_open`. The kill rung, `live_risk_state.cancel_open_buy_orders`, now also cancels working option sell-to-opens, each of which opens a strike × 100 obligation. Ruling F1 still holds: an EB stock sell is never one, whatever Alpaca tags it with, so the rung keeps EB's working sells exactly as today.
9. `broker_adapters.errors.OptionsNotPermitted(BrokerError)`, which is definitive and non-retryable. **Fix wave (FW1 item 8; seams drift 6):** `AlpacaAdapter` maps only an HTTP answer to it: Alpaca's code 40310000, or wording that says the account is not eligible, approved, enabled, permitted, authorized or allowed, or that names its options (trading) level (`_OPTIONS_REFUSAL`). A server error (5xx) never maps to it. Neither does a transport failure with no response, nor an answer that merely names the contract.
10. `AlpacaAdapter` gains `option_contract_meta(symbol) -> Optional[OptionContractDTO]` (cached contract lookup) and `_option_positions_complete: bool`. It also gains `cancel_orders_confirmed(order_ids, timeout_s=10.0, *, poll_interval_s=0.5, sleep=time.sleep, clock=time.monotonic, booked_fills=None)`; `poll_interval_s`, `sleep` and `clock` are test hooks.
    - It returns True only when every order ended cancelled (or was already dead). It returns False when any is still working at the timeout, and False when any shows a fill (the position changed under the caller), unless that order is dead with no more filled than the caller booked (next bullet).
    - **Fix wave (FW1 item 8; seams drift 3):** `booked_fills` maps an order id to the quantity the caller has already booked for it. `broker._cancel_bracket_legs_confirmed` passes each leg's lifecycle `cumulative_quantity`. A dead leg whose fill is no more than that is confirmed: its partial fill is already in the position the sell was sized from.
    - **Fix wave: what else makes `_option_positions_complete` False** (and so `option_positions_health()["complete"]`), besides a refresh that failed or a contract lookup that missed:
      - a short option fill of unknown origin, with no cached contract meta and no intent meta on its fill (FW-lo-I3), until the next refresh reads the meta;
      - a stream fill landing between a positions refresh's GET and its rebind that the refresh cannot settle (fix wave round 2). Every option fill is journaled in `_option_fill_seq` / `_option_fill_journal` (the newest `_OPTION_FILL_JOURNAL_MAX` = 256 entries), and the refresh settles any fill with a sequence number past the mark it took.
11. `LiveOrderService(..., legs_lookup=None)` plus:
    - **Fix wave:** `submit(intent, *, snapshot_overlay: Optional[dict] = None, before_submit: Optional[Callable[[OrderIntent, GateDecision], None]] = None) -> OrderSubmission` (`enqueue` is the same method).
      - `snapshot_overlay` (FW-lo-I1) replaces fields of the provider's snapshot for this one evaluation. The approval handler passes its control re-read. An overlay the snapshot rejects is `dependency.snapshot.invalid`.
      - `before_submit` (FW-str-I1) runs once the gate has allowed the intent and before anything exists: no lifecycle row, no reservation, no broker call. A swing exit cancels its bracket legs there. It refuses by raising, and nothing was created.
      - Every EB call passes neither and takes exactly the old path.
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
    - **Fix wave (seams drift 9):**
      - `_approval_control_overlay(adapter, *, instance_key, now_utc=None)` (item 17);
      - `_sweep_stale_submitted_signals(order_service, *, now_utc=None, log=None, max_age_s=600.0)` (item 19);
      - `_submit_swing_sell(adapter, order_service, intent, *, log=None, timeout_s=10.0)`;
      - `_swing_next_open_exit(hint, intents)` and `_SWING_EXIT_INTENTS` (§1);
      - `_swing_exit_held_after_close(at)` (item 15), also read by the approval handler (item 17);
      - `_swing_unprotected_alert(instance_id, symbol, detail, *, kind="unprotected", now_utc=None)`;
      - `_cancel_bracket_legs_confirmed(adapter, order_service, symbol, *, timeout_s=10.0, log=None, cancelled=None, attempted=None)`. `cancelled` receives the leg ids whose cancel Alpaca confirmed, and `attempted` every leg id a cancel was SENT for.
      - state: `_swing_approvals_in_flight` (a set of the signal ids a handler in this process is placing, claim to outcome; the sweep skips them) and `_swing_unprotected_alerts` (`{"session", "sent"}`, one alert per symbol and kind per New York session).
      - `_live_option_quotes[contract]` keeps the entry a refresh replaced one level deep, under `"previous"` (`{"price", "quote_at", "fetched_at"}`). The option gate uses it when the intent's `quote_at` matches it, because the loop and the approval thread share the cache.
13. Strategy-cache keys that the engine writes under `_strategy_cache["strategy_wheel"]`:
    - `_engine_option_activity_cursor`, shaped `{"after": "YYYY-MM-DD", "seen": [ids]}`. `after` is one day before the newest activity handled, and never later than one day before an assignment still to be retried.
    - `_engine_wheel_assignments`, a list of `{"activity_id", "contract", "underlying", "shares", "side", "strike", "date"}`. The wheel lane (plan B) reads this list to know which shares it owns. An entry is added, once per `activity_id`, whenever the assignment's lifecycle row is FILLED, so a replay after a lost cache lists it again; `side` is `"buy"` for a put and `"sell"` for a call.
    - The assignment is recorded through `record_external_fill` with source `option_activity`, Alpaca's activity id as `broker_order_id`, and the key `opasn-<sha256(activity id)[:32]>`.
    - The assignment notification goes through plan B's `swing_trader.notify.notify_wheel_assignment(instance_id, *, symbol=<underlying>, qty=<shares>, price=<strike>, date="YYYY-MM-DD")` when that module is importable, and through `notifications.notify(category="wheel_assignment", ...)` when it is not.
    - **Drift (T16).** `_engine_option_activity_owed`: a list of Alpaca activity ids whose one `wheel_assignment` notice is still owed, capped at the newest 500. An id joins it when the poll that recorded the assignment (`record_external_fill`) cannot read the lifecycle row back. The store read failed, so FILLED is unknown, never "not filled". That activity is held: the cursor does not move past it, it is not added to `seen`, and no notice is sent. The later poll that finds the row FILLED lists the assignment in `_engine_wheel_assignments`, sends the notice once, and drops the id. A poll that only re-reads a row it did not record never adds an id. The key is absent until an id is first owed. After that it is rewritten on every poll that reads activities, as `[]` when nothing is owed.
14. For every option order that carries a `signal_id`, the engine writes `{"status": "submitted"|"failed", "order_client_id"}` back through `swing_trader.signals_store.update_signal`. This covers strategy-emitted orders, not only approvals. **Drift (T16):** a duplicate, and an outcome that is neither submitted nor a definite refusal, write nothing. The approval handler writes more than this; see item 17.
15. Engine refusal note for plan B: the engine never retries an exit it deferred. That happens when a bracket-leg cancel did not confirm, or a sell floored to zero. **The swing lane must re-emit its RSI exit on later ticks while the position is still held.**
    - **Fix wave (round 2, minor 2; seams drift 2):** a third deferral, the after-close hold. A swing exit decided between the regular close (16:00 ET, 13:00 on a half day, or all afternoon on a holiday; `is_nyse_open`) and 20:00 ET raises "order deferred: <SYM> swing exit held until the next pre-market or regular-hours tick", which is routed as `deferred`. Alpaca rejects a non-extended-hours market order in that window. Pre-market is not held: the order queues for the open. After 20:00 the gate's own `market.closed` refuses. Without a calendar the window is 16:00-20:00 ET.
    - **Fix wave: exit alerts** through the `swing_exit` category, at priority 2, once per symbol, kind and New York session (`_swing_unprotected_alert`):
      - "EXIT NOT PLACED — <SYM> may be unprotected": a sell that did not go out after a cancel was sent for its bracket legs;
      - "EXIT OUTCOME UNKNOWN — check open orders — <SYM>": the submit raised, so the sell may or may not have been placed.
      - Both bodies say the position may be unprotected, and the lane re-sends the exit next tick.
16. Live-state position rows (interfaces section 9, item 4; the coordinator pinned this on 2026-09-24):
    - **Option rows** written by `broker.py`'s LiveState snapshot, and **every row** served by `live_broker_fetch`, carry `asset_class`, `side` (`"long"` or `"short"`), `multiplier` (100 for options, 1 for equities), `underlying`, `strike` and `expiry`. `qty` is signed. **Ruling F4:** option rows also carry `option_type` (`"put"` or `"call"`), so the UI reads the contract fields instead of parsing the OCC symbol.
    - `last_price`, `market_value`, `unrealized_pnl` and `unrealized_pnl_pct` are `null` when Alpaca gives no value. They are never invented as 0.
    - **Equity rows written by `broker.py` keep their exact pre-change shape**, with none of these keys, because that code runs inside EB's real-money process every few seconds. The UI must read a missing `asset_class` as `"us_equity"`, a missing `multiplier` as 1, and a missing `side` from the sign of `qty`.
    - `live_broker_fetch` `recent_trades` rows gain `asset_class`. Rows written by `broker.py` still carry none, so the UI keeps its OCC-shape fallback for them. **Ruling F17:** this refines section 9 item 4, whose "`recent_trades` rows carry no `asset_class`" now holds only for the rows `broker.py` writes.
    - `close_position` refuses option contracts with a clear message ("<symbol> is an option contract; option contracts are closed by buy-to-close, not close_position"): a contract in the adapter's option book, or any OCC-shaped symbol.
    - L5 additions. `live_broker_fetch` equity rows carry `option_type`, `underlying`, `strike` and `expiry` as `null`, so every served row has one key set. When Alpaca's positions cannot be read (the call raises, the answer is not a list, or an option row is unreadable) the fetch fails with `broker_fetch_error` = `"positions_unavailable: <cause>"` and serves no `positions`; the API then serves the container's row and marks the state `stale`. It is never an empty book (plan B G8a re-review: the wheel page must not read an outage as "no open puts"). An unreadable equity row is skipped as before.
    - L5 addition. During a positions outage the `broker.py` snapshot also carries each contract from the adapter's last-known option book, with `last_price`, `market_value`, `unrealized_pnl` and `unrealized_pnl_pct` set to `null`.
17. **Drift (T16), as merged after the fix wave and the final round.** The approval handler `broker._execute_swing_approval` (section 7; A-live Task 15, its fix rounds 1 and 1b, fix wave FW1, and the final round) runs these steps in order.
    - **1. Read the signal.** It is read three times about 1 s apart. This comes BEFORE any claim. If every read fails, the handler writes nothing and logs red. It sends a `swing_approval_failed` notice naming the signal id: symbol "signal <id>", lane "swing or wheel", reason "the approved signal could not be read (<Exc>); nothing was sent". The signal still reads `approved`.
      - An unknown signal, one of another instance, or one that is not `approved`/`approved_half` is refused. Nothing is written and no notice is sent; a redelivery is not a failure.
    - **2. The day rule (Final round, seams m6).** The New York date of `decided_at` must be today's New York date, the re-send route's rule (§9 item 2a).
      - If it is not, or cannot be read, the row is compare-and-swapped from the approved status just read to `{"status": "failed", "order_client_id": None}` BEFORE the claim. The operator is told "approval from <YYYY-MM-DD | an unknown date> — approve a fresh signal", which is also the command error.
      - Nothing is built, re-read or sent, so an original command queued across a day is never placed at a later day's price.
      - A lost compare-and-swap places and tells nothing. One that raises places nothing, logs red and tells the operator that the signal could not be marked failed and still reads approved.
    - **3. The claim.** The signal id joins `_swing_approvals_in_flight` until the handler returns. The row is then claimed `approved`/`approved_half` → `submitted` by a compare-and-swap (`signals_store.cas_signal`), BEFORE anything is sent. The claimed row carries `claimed_at` (ISO, the stale-row sweep's age stamp; fix wave FW1 item 9).
      - A lost claim writes nothing and places nothing, so a redelivered command cannot place a second order.
      - **A claim that raises** writes nothing and logs red. It sends the notice "the approval could not be claimed (<Exc>); nothing was sent". The command error is "signal <id> could not be claimed: <Exc>: <msg>".
      - Every write below starts from `submitted`.
    - **4. Build.**
      - The lane must be enabled.
      - **Final round (seams m4):** a SWING approval handled between the close (16:00 ET, 13:00 on a half day) and 20:00 ET (`_swing_exit_held_after_close`, FW1's exit hold) is transient: "after the close — approve again after 20:00 ET or pre-market". This is checked before the control re-read, and nothing is built or sent. Wheel approvals are not held here: the option gate refuses them outside regular hours with the transient `market.regular_hours_required`.
      - **Fix wave (FW-lo-I1):** the gate's control inputs are re-read NOW by `_approval_control_overlay`: the kill switch, the watchdog, cash (`refresh_account()`), the calendar and the durable risk state, evaluated but not saved. This runs BEFORE the price read. The result is passed as the one evaluation's `snapshot_overlay` (item 11; an option intent drops its calendar fields, since its snapshot reads the calendar itself). A failed re-read is transient.
      - The live price is read, then the equity (`adapter._account_equity`, else `refresh_account().equity`).
      - `approvals.build_approved_order` rebuilds the order (§6; Final round, I-1 caps a swing entry at the lane's budget).
      - The intent is a MANUAL-source bracket (`_build_bracket_intent`) or option intent (`_build_option_intent`, after `_refresh_option_quote`). **Both carry the reason `swing_approval:<signal id>`.** The wheel's reason was added in fix wave FW1 item 9, so the stale-row sweep can find its intent.
    - **5. Send** through `order_service.enqueue(intent, snapshot_overlay=controls)`.
    - **Accepted:** the write-back is a compare-and-swap FROM THIS CLAIM'S ROW (status `submitted` and this claim's `claimed_at`; round 2, minor 3), never a plain `update_signal`. It writes `{"status": "submitted", "order_client_id": <key>, "submitted_order": <order>}`.
      - `<key>` is the key the service used, which can be an escalated retry key.
      - `<order>` is the dict `approvals.build_approved_order` rebuilt at the live price (section 6): `equity_bracket` or `option`. Calibration scores the contract actually sold, because `swing_trader.calibration` reads `submitted_order.contract` before `proposal.contract`.
      - A write-back that finds the row `failed` (the sweep marked it meanwhile) logs red. It sends `notify_swing_order_placed_for_failed(instance_id, *, symbol, lane, client_order_id)` at priority 2: the order WAS placed, do not place it by hand. Any other miss logs red. The command still succeeds.
    - **Outcome unknown** (plain `update_signal` patches):
      - The service answered without a broker reference: it writes `{"order_client_id": <key>, "submitted_order": <order>}`.
      - `enqueue` raised: it writes `{"submitted_order": <order>}` only. The command result carries the pre-submit key, because an escalated key cannot be known (M-5).
      - In both cases the status stays `submitted`, a red line is logged, and the next reconcile resolves the order.
    - **Transient failure:** it writes `{"status": "pending", "decided_by": None, "decided_at": None, "decision_reason": None}`. Nothing reached the broker, and the decision is undone so the operator can approve again.
      - The command error and the `swing_approval_failed` notice carry the reason `"<why> — approve again<when>"`. The command error also appends ` (<detail>)` when there is one. The transient `<why>`s, as merged:
        - `after the close` (Final round, m4), with `<when>` = ` after 20:00 ET or pre-market`;
        - `the order gate's controls could not be re-read` (fix wave FW-lo-I1), with the exception as detail;
        - `no live price for <SYM>`;
        - `account equity unreadable (<Exc>: <msg>)`, when `refresh_account` raised, and `account equity unreadable (the broker returned none)`, when it answered with no equity (fix wave FW1 item 3);
        - `no usable options snapshot for <contract>`, or `... (<Exc>)` when the refresh raised;
        - `broker order book unreadable` (`approvals.BookUnreadable`), with its text as detail;
        - `option book unreadable` (`approvals.OptionBookUnreadable`; Final round, I-1), with its text as detail;
        - `order gate blocked: <codes, comma-joined>`, when the gate refusal's codes are ALL transient. Transient codes: any `dependency.*`; `quote.stale`; `positions.stale`; `market.closed` and `market.regular_hours_required` (fix round 1b); and the pure races `quote.timestamp_mismatch`, `quote.reference_price_mismatch` and `risk.snapshot_mismatch` (fix wave FW1 item 3).
      - `<when>` is ` after the open` for no live price, no options snapshot, and a gate code of `quote.stale`, `market.closed`, `market.regular_hours_required` or `dependency.quote.*`. Otherwise it is empty, except for the after-close hold.
      - A transient code beside a lasting one is a definite failure; for example `market.closed` with `exposure.max_order_notional`. A gate refusal returns before the service creates a lifecycle record, so the re-approval places exactly one order.
      - The notice is sent only when the reset was written (M-1). If the write fails, the handler logs red, and the notice and the command error read `"<why> — nothing was sent, and the signal could not be put back to pending (it may still read submitted)"`. They never say "approve again" (fix wave FW1 item 3).
    - **Definite failure:** it writes `{"status": "failed", "order_client_id": <key, or None before the gate>}` and sends a `swing_approval_failed` notice. This covers:
      - an unknown or disabled lane;
      - a rebuild error, including a swing budget that buys no whole share (I-1);
      - a refused bracket;
      - every other refusal the service reports: risk caps, `idempotency.*` duplicates, and a broker refusal, which the service returns as the gate code `broker.rejected.<exception>`.
      - The day rule's `failed` (step 2) comes before the claim.
18. **Fix wave (FW-eb-m2; seams drift 8): the reconcile snapshot fails closed on an order it cannot read**, as on main.
    - An `mleg` order, or one with no side that no `position_intent` explains, makes `AlpacaAdapter`'s reconciliation snapshot broker-unavailable (`BrokerError`). The adapter logs one red line per such order per process (`_say_unreadable_order_once`).
    - A single-leg option order with no side stays readable through its intent.
    - **Operator constraint:** IntelliStock never submits an mleg order. But one placed by hand in the Alpaca UI on alpaca-main, while it is in the 30-day history window, stops the reconcile and so stops EB trading.
19. **Fix wave (FW1 item 9; round 2, minor 3): the stale-submitted sweep**, `_sweep_stale_submitted_signals(order_service, *, now_utc=None, log=None, max_age_s=600.0)`.
    - It runs from the live loop tick only in live mode on Alpaca with the unified service, and only on a document with an enabled swing or wheel lane. Doc 200 never enters.
    - It reads the `submitted` swing/wheel rows of this instance that carry no `order_client_id`. It skips any row whose id is in `_swing_approvals_in_flight`, because its handler is still placing it.
    - A row is stale once it is `max_age_s` (10 minutes) past its claim: `claimed_at`, else `decided_at`, else `created_at`.
    - A stale row is left to reconcile when an intent exists for it: the reason `swing_approval:<id>`, or, for a wheel row claimed before wheel intents named their signal, a MANUAL `us_option` intent on its underlying decided since the claim.
    - Otherwise it is compare-and-swapped from `submitted` to `{"status": "failed", "order_client_id": None}`. A red line is logged and `notify_swing_approval_unconfirmed` is sent: the order may not have been placed, so check open orders.
    - A lifecycle store that cannot be read raises: nothing is marked on a guess.

## 11. Additions from plan B

- `swing_trader.constants.SWING_DEFAULTS` and `WHEEL_DEFAULTS`: the header defaults. The six swing `live_*` values equal A-live's `_SW_DEFAULTS` row (0.2 / 0.2 / 0.2 / 0.25 / 0.35 / 0.45).
- Intent labels `swing_stop_exit` and `swing_target_exit` (ST `exit_signal` close-based stop and target).
- The live swing entry hint has no share count: `buy_cash = max(min(equity × 0.125, budget) × size_adjustment, prior close)`; the engine buys `floor(buy_cash / live price)`. Approval orders carry `qty` (`approvals.build_approved_order`), capped at the same budget (§6; Final round, I-1).
  - **Fix wave (FW-lo-I5; seams drift 13): the budget is collateral-net** (`account.swing_live_budget`, below). It is buying power when no short put is open or working. Otherwise it is `max(0, min(buying power, cash) − open and pending short-put collateral)`. Margin buying power cannot lift it, since the puts are cash-secured. It is 0 when cash is negative (Final round, m2).
  - The entry scan still skips a candidate when the budget is below half its allocation, as ST did.
  - **Final round (seams m1):** when the collateral cannot be read, the scan is "not ready", like unreadable positions. Nothing is decided or latched, and it retries next tick. Exits wait for that retry too; pending exits are still re-sent (`_reemit_exits` runs first). Before the final round the lane planned no entries and still latched the session.
  - **Fix wave (FW-str minor a):** an approved entry whose `buy_cash` floors to 0 shares at the live trade price (`account.latest_trade_price`) is written `failed` with `decision_reason` ("the share count rounds to 0: ..."). It holds no slot, sector or budget, so a later candidate may take them.
- Swing strategy-cache key `_swing_pending_exits` (`{symbol: {"reason", "intent", "since"}}`): an emitted exit is re-emitted on every later tick while the stock is held with no working non-bracket sell (answers A-live addition 15).
- **Fix wave (FW-str minor d; seams drift 15):** swing strategy-cache key `_swing_scan_first_tick` = `{"session", "at": "HH:MM" ET, "late": bool}`. It is stamped on the first tick of a session at which the scan is due, whatever happens next. On a day whose first scan tick is at or after 09:30 ET (a missed pre-market scan), the lane plans no entries, because ST's cron ran only at 09:15. Exits still run.
- Option order dict key `"session": "YYYY-MM-DD"`, informational (A-live keys option sells on the session itself).
- The wheel lane reads `_engine_wheel_assignments` (A-live addition 13) from its strategy cache: only assigned shares are covered-call candidates.
- `SwingSignals` may carry `score: None` (AI gate off) and `context: dict`.
  - **Seams drift 14:** a `failed` row the WHEEL lane wrote carries `error`. The swing lane's zero-share skip writes `decision_reason` instead.
  - **Drift (T16):** A-live's approval handler never writes `error`. It writes `claimed_at`, `status`, `order_client_id` and `submitted_order` (the rebuilt order dict), or it resets `status` to `pending` and clears `decided_by`, `decided_at` and `decision_reason` on a transient failure (section 10 item 17).
  - Rows the stale-row sweep marks `failed` set `order_client_id: None` (section 10 item 19).
- `swing_trader.signals_store`: `signal_id_for(instance_id, lane, session, symbol) -> str`, `new_signal(**fields) -> dict`, `cas_signal(signal_id, *, expect_status, doc) -> bool`, `swing_owned_symbols(instance_id, held) -> set[str]`, `insert_wheel_scan(row) -> str`, `list_wheel_scans(instance_id, limit=50) -> list[dict]`, `all_signals(instance_id) -> list[dict]`, `ensure_tables() -> None`, `OPEN_STATUSES`.
- `swing_trader.approvals.BookUnreadable(ValueError)` and **Final round (I-1)** `OptionBookUnreadable(BookUnreadable)`: both transient in the approval handler (section 10 item 17).
- `swing_trader.approvals.SignalConflict(ValueError)`: a decision on a signal that is not pending; `_run` maps it to 400 per §9 item 2. A click that lost the compare-and-swap: `interactive_utils.SwingDecisionRaceError` → 409. An approval that provably cannot reach the broker, or an unreadable wheel book: `interactive_utils.SwingBrokerUnavailableError` → 503, and the signal is pending. A queue write that raised but may have landed is not a 503: it is the §9 item 2 202 `uncertain` answer (fix wave FW-api-I1). A refused re-send (§9 item 2a): `interactive_utils.SwingResendConflictError` → 409. Unknown or foreign signal id: `LookupError` → 404.
- `swing_trader.account`: read-only book views shared by both lanes: `equity_positions`, `option_symbols`, `live_equity`, `option_positions`, `open_orders`, `working_orders`, `account_options`, `spendable`, `pending_symbols`, `entry_price_from_trades`, `positions_health`, `option_book`.
  - **Seams drift 12:** `live_buying_power` no longer exists. Fix wave FW-lo-I5 replaced it with:
    - `swing_live_budget(emu, book) -> (budget, collateral, reason)`. It returns `(None, None, reason)` when the collateral is unknown, and a budget of 0 when cash is negative (Final round, m2).
    - `put_collateral(emu, book) -> (collateral, reason)`: every open short put plus the unfilled remainder of every working sell-to-open put, at strike × 100. It returns `(None, reason)` when the option book is not a complete, current map (`option_book`), and **Final round (m3)** when any short row has an unknown type, underlying or strike, mirroring the broker's `meta_known` rule. Such a row is never counted as 0.
    - `latest_trade_price(emu, symbol)`: the adapter's latest trade price, or None.
  - `book` is the strict working-order read (`working_orders`). The approval handler reads the budget the same way (§6).
- The approval command handler (§7) is plan A-live Task 15's `_execute_swing_approval`; plan B does not edit `broker.py`.
- `swing_trader.notify.send(category, instance_id, title, message, *, priority=0)` and `notify_wheel_assignment(instance_id, *, symbol, qty, price=None, date=None)` (A-live's activities poller calls the latter). The approval handler's senders are `notify_swing_approval_failed`, `notify_swing_approval_unconfirmed` and `notify_swing_order_placed_for_failed` (section 8).
- `llm_utils.call_llm_with_web_search(provider, api_key, model, prompt, *, max_output_tokens=300, max_uses=2, timeout_sec=None, provider_config=None) -> str`.
- What plan B relies on from the live engine:
  - (a) Option position type: A-live's live position rows carry `option_type` (A-live pre-flight F4 adds it) in `live_broker_fetch.fetch_broker_live_state`; B still falls back to the OCC symbol.
  - (b) Price marks: entries are price-marked by the existing broker helper `_ensure_live_candidate_marks` (A-live adds no marking).

- SwingSignals `outcome` for a swing entry that never filled within 2 NY sessions (plan B G8a, ruling 4): `{"unfilled": true, "as_of": "YYYY-MM-DD", "sessions_waited": int}`. The status is unchanged and no `pnl` is recorded, so it never counts as a round trip. Entries still working at the broker (GTC brackets) are never closed as unfilled.

### Deploy fingerprint hand-off (plan B Task 25 → A-live Task 16)

Source: `git diff --name-status $(git merge-base main HEAD)..HEAD -- backend`, test files excluded, at `c6b5771d` (2026-09-25). There are 41 changed backend `.py` files across plans A-backtest, A-live and B; plan C changes no backend file. Paths are backend-relative, as `_CODE_FINGERPRINT_FILES` lists them. `scripts/check_deployed_code.py`'s `FILES` takes the same paths prefixed with `backend/`. Scripts are not deployed.

Already fingerprinted on both sides (7), so no change: `broker.py`, `broker_adapters/alpaca.py`, `api/main.py`, `llm_utils.py`, `simulated_execution.py`, `portfolio_emulator.py`, `backtest_bar_events.py`.

**Seams drift 21: done.** f047a39a (`chore(deploy-check): fingerprint every swing-port backend file`) added all 34 below to both lists. The list is kept as the record of what was added.

Added to both lists (34):
- plan A-live (11): `broker_adapters/base.py`, `broker_adapters/errors.py`, `live_broker_fetch.py`, `live_orders/__init__.py`, `live_orders/types.py`, `live_orders/store.py`, `live_orders/gate.py`, `live_orders/service.py`, `live_orders/reconcile.py`, `live_pending_orders.py`, `live_risk_state.py`;
- plan B, strategies (2): `strategies/strategy_swing.py`, `strategies/strategy_wheel.py`;
- plan B, package (18): `swing_trader/__init__.py`, `swing_trader/account.py`, `swing_trader/ai_analyst.py`, `swing_trader/approvals.py`, `swing_trader/calibration.py`, `swing_trader/clock.py`, `swing_trader/constants.py`, `swing_trader/indicators.py`, `swing_trader/iv.py`, `swing_trader/market_data.py`, `swing_trader/notify.py`, `swing_trader/refdata.py`, `swing_trader/regime.py`, `swing_trader/sectors.py`, `swing_trader/signals.py`, `swing_trader/signals_store.py`, `swing_trader/universe.py`, `swing_trader/wheel_rules.py`;
- plan B, shared modules (3): `db/schema.py`, `interactive_utils.py`, `notification_types.py`.

When this hand-off was written, A-live Task 16's `EXPECTED` and its paste block lacked five entries: `swing_trader/account.py`, `clock.py`, `notify.py` and `refdata.py` (ruling F2), and `live_orders/__init__.py`, which A-live's own commit 786af5a2 changed. f047a39a added them, and `backend/tests/test_swing_deploy_check.py` pins all 41 on both sides. Two entries share the basename `__init__.py` (`live_orders/` and `swing_trader/`), so both sides key them by full path. The existing duplicate-basename rule already does that.
