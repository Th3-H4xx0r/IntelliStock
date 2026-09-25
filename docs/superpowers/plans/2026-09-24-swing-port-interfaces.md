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
- Notification types: `swing_entry`, `swing_pending_review`, `swing_exit`, `swing_run_summary`, `wheel_put_placed`, `wheel_pending_review`, `wheel_position_alert`, `wheel_assignment`.
- API routes:
  - `GET /instances/{instance_id}/swing/signals?status=`
  - `POST /instances/{instance_id}/swing/signals/{signal_id}/decision` with body `{"decision", "reason"}`
  - `GET /instances/{instance_id}/wheel`
  - `GET /instances/{instance_id}/swing/calibration`


## 9. UI-consumed shapes (pinned by plan C; plan B must return exactly these)

1. `GET /instances/{id}/swing/signals?status=pending` returns `{"signals": [<SwingSignals doc>, ...]}`. The UI also accepts a bare list, and drops rows whose `status` is not `pending`.
2. `POST .../decision` answers:
   - any 2xx on success (the body is ignored);
   - **400** when the signal is not pending (`approvals.decide` raises `ValueError`, which `api/main.py:_run` maps to 400);
   - **404** for an unknown signal id, or one that belongs to another instance;
   - 409 (optional) for a lost race;
   - 401 or 403 from auth;
   - 422 for a malformed body.
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
