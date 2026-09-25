# Swing Port A-live (live and paper order path) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach IntelliStock's live/paper order path (types, gate, service, reconcile, Alpaca adapter, broker.py) to place, track and reconcile equity bracket orders and cash-secured-put option orders, while every code path that strategy EB on doc 200 (alpaca-main, real money) takes stays byte-identical.

**Architecture:** Every new behaviour hangs off a field that only the swing and wheel lanes set: `OrderIntent.asset_class/order_class/position_intent/...`, the `_nexus_option_orders` side channel, and a `bracket` sizing hint. When those fields hold their defaults, each touched function takes its old branch. Pin tests (keys, stored rows, transport kwargs, Alpaca request payloads and a reconcile evidence hash, all computed from the pre-change code on 2026-09-24) prove it. Bracket legs become first-class lifecycle rows (source `bracket_leg`, keyed by the broker-minted leg client order id), so leg fills reconcile. Short option positions live in the adapter's separate `_option_positions` map, and the gate gets a separate options branch.

**Tech Stack:** Python 3.14, alpaca-py 0.43.5 (`alpaca.trading`, `alpaca.data`), pytest, PostgreSQL through `from db import store` (only through the existing lifecycle/WAL backends; this plan adds no direct DB access).

**Spec:** `docs/superpowers/specs/2026-09-24-swing-trader-port-design.md` (this plan implements section 6.1, plus the engine side of section 9 items 1, 3, 6, 8, 9 and 10, and sections 11/12 for the live path). **Shared contract:** `docs/superpowers/plans/2026-09-24-swing-port-interfaces.md`. Executors read both.

## Contract additions

These names are not in the interfaces doc yet. **Task 1 Step 0 adds this list to `docs/superpowers/plans/2026-09-24-swing-port-interfaces.md` under a new heading "10. Additions from plan A-live" (section 9 is already the UI-consumed shapes)**, so plans A-backtest, B and C see them.

1. `OrderIntent.parent_client_order_id: Optional[str] = None` and `OrderIntent.broker_client_order_id: Optional[str] = None`. When `broker_client_order_id` is set, it IS the `idempotency_key`; the hash is not used. Only sources `bracket_leg` and `option_activity` may set it.
2. Identity rules the contract left open:
   - `take_profit_price` and `stop_loss_price` never join the identity dict. They are sizing, like `limit_price`, and a re-emitted entry must not re-key.
   - Every other new field joins the identity only when it differs from its default.
   - A `us_option` SELL is keyed on the session: no `decision_minute`, `quantity` or `reduce_only`. This is spec section 9 fix 1: a rerun cannot mint a second sell-to-open of the same contract in one session.
   - `take_profit_price`, `stop_loss_price` and `strike` are normalized to `Decimal` on construction (callers may pass floats), like `limit_price`.
3. `OrderSource.BRACKET_LEG = "bracket_leg"` (the contract's `BRACKET_LEG`, also exported as the module constant `live_orders.BRACKET_LEG`) and `OrderSource.OPTION_ACTIVITY = "option_activity"` (option assignment fills).
4. `DependencySnapshot` gains `asset_class: str = "us_equity"`, `regular_session_open: Optional[bool] = None`, `account_equity: Optional[Decimal] = None`, `open_short_put_collateral: Optional[Decimal] = None` (None = unknown), `pending_sell_to_open_collateral: Decimal = 0`, `underlying_put_collateral: Optional[Decimal] = None` (existing short puts plus pending sell-to-open puts on the intent's underlying, this intent excluded) and `max_underlying_collateral_fraction: Decimal = 0.25`.
5. `ConfirmedFill` gains `asset_class: str = "us_equity"` and `contract_multiplier: int = 1`.
6. `OrderRef` also gains `order_type: Optional[str] = None`, `limit_price: Optional[float] = None` and `stop_price: Optional[float] = None`. That is how a take-profit leg (limit) is told from a stop-loss leg (stop).
7. `PositionDTO` gains `asset_class`, `side`, `unrealized_pl`, `unrealized_plpc`, `current_price`, `underlying`, `strike` and `expiry` (all `Optional`, default None) and `multiplier: int = 1`. They are filled only for option rows.
8. `broker_adapters.base`:
   - `BrokerAdapter.get_option_chain(underlying, *, option_type=None, expiration_gte=None, expiration_lte=None, strike_gte=None, strike_lte=None) -> dict[str, OptionSnapshotDTO]`. Spec 6.1 lists it; the contract omitted it.
   - The pure helpers `is_bracket_child_order(order) -> bool` and `is_risk_reducing_order(order) -> bool`.
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
13. Strategy-cache keys that the engine writes under `_strategy_cache["strategy_wheel"]`:
    - `_engine_option_activity_cursor`, shaped `{"after": "YYYY-MM-DD", "seen": [ids]}`
    - `_engine_wheel_assignments`, a list of `{"activity_id", "contract", "underlying", "shares", "side", "strike", "date"}`. The wheel lane (plan B) reads this list to know which shares it owns.
    - The assignment notification goes through plan B's `swing_trader.notify.notify_wheel_assignment(instance_id, *, symbol=<underlying>, qty=<shares>, price=<strike>, date="YYYY-MM-DD")` when that module is importable, and through `notifications.notify(category="wheel_assignment", ...)` when it is not.
14. For every option order that carries a `signal_id`, the engine writes `{"status": "submitted"|"failed", "order_client_id"}` back through `swing_trader.signals_store.update_signal`. This covers strategy-emitted orders, not only approvals.
15. Engine refusal note for plan B: the engine never retries an exit it deferred. That happens when a bracket-leg cancel did not confirm, or a sell floored to zero. **The swing lane must re-emit its RSI exit on later ticks while the position is still held.**
16. Live-state position rows (interfaces section 9, item 4; the coordinator pinned this on 2026-09-24):
    - **Option rows** written by `broker.py`'s LiveState snapshot, and **every row** served by `live_broker_fetch`, carry `asset_class`, `side` (`"long"` or `"short"`), `multiplier` (100 for options, 1 for equities), `underlying`, `strike` and `expiry`. `qty` is signed.
    - `last_price`, `market_value`, `unrealized_pnl` and `unrealized_pnl_pct` are `null` when Alpaca gives no value. They are never invented as 0.
    - **Equity rows written by `broker.py` keep their exact pre-change shape**, with none of these keys, because that code runs inside EB's real-money process every few seconds. The UI must read a missing `asset_class` as `"us_equity"`, a missing `multiplier` as 1, and a missing `side` from the sign of `qty`.
    - `live_broker_fetch` `recent_trades` rows gain `asset_class`. Rows written by `broker.py` still carry none, so the UI keeps its OCC-shape fallback for them.
    - `close_position` refuses option contracts with a clear message ("option contracts are closed by buy-to-close, not close_position").


## Global Constraints

- EB on doc 200 (alpaca-main, real money) must take byte-identical code paths. Every new branch triggers only when an intent or order carries a new field (`asset_class != "us_equity"`, `order_class`, `position_intent`, `broker_client_order_id`), when a document has an enabled `strategy_swing` / `strategy_wheel` lane, or when the `_nexus_option_orders` list is non-empty.
- Tests pin EB's `idempotency_key`, stored intent row, transport kwargs, Alpaca request payloads and a reconcile evidence hash byte-for-byte. The pins were computed from the pre-change code on 2026-09-24. **Never edit a pin to make a test pass. A moved pin is the regression.**
- `broker.py` cannot be imported under pytest (it argparses and SystemExits). Broker tests AST-extract functions through `backend/tests/swing_broker_harness.py` (Task 8), which also asserts that every free name a function reads is provided. When a task adds a helper call inside an existing extracted function, it must also update every harness that extracts that function. `test_strategy_x_broker_coexistence.py::_DISPATCHER_NAMES` is the sentinel for `run_run_once_strategies`; this plan adds no helper call there.
- Existing source-window assertions in `backend/tests/test_strategy_eb_live_submit.py` must stay green. Inserting code has these limits:
  - Nothing new may go between the first `_submission.accepted` in `broker.py` and the `outcome="uncertain"` that follows it (the window has 76 characters of slack today).
  - The literals `outcome="blocked"`, `_submission.accepted`, `execute_signal raised` and `execute_signal hard-timeout` must not appear anywhere new in `broker.py`.
  - `raise ValueError("computed order quantity <= 0")` must stay within the first 3000 characters after `def _build_strategy_stock_intent(` (it sits at 1173 today).
- `test_manual_order_gate.py::test_broker_has_no_direct_submit_order_transport_calls` forbids any `.submit_order(` call in `broker.py`. All orders go through `LiveOrderService.submit` / `enqueue`.
- Tests run from the repo root: `python3 -m pytest backend/tests/<file> -q -p no:cacheprovider`. The full-suite baseline is 7,565 passed with EXACTLY 19 pre-existing failures: `test_adv_exit_discipline_findings` 11, `test_core_sleeve_adversarial` 7, `test_zz_adversarial_sweep` 1. The target is no new failures.
- DB access only through `from db import store`. This plan adds none directly; lifecycle rows go through the existing `OrderLifecycleStore` backend.
- Per CLAUDE.md, every task starts by running GitNexus impact on each symbol it modifies. GitNexus currently answers `risk: UNKNOWN, 0 callers` for every live-order symbol (checked 2026-09-24; the index was 3 commits behind), because the callers live in `broker.py`, which GitNexus does not index, or arrive as injected bound methods. So, in every task:
  - run `npx gitnexus analyze` first;
  - then `mcp__gitnexus__impact({target, direction: "upstream"})`;
  - then the `grep -n` given in the task;
  - **treat every symbol on the live order path as HIGH risk** and tell the user so before editing.
- Before each commit, run `mcp__gitnexus__detect_changes()` and confirm the changed symbols are only the ones the task names.
- Commit messages have no backticks in their bodies, and the footer lines are exactly:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`
  `Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS`
- Never stage `AGENTS.md` or `CLAUDE.md`, which are dirty in this worktree. Stage files by explicit path only.
- **No merge to `main`** without the operator: `main` auto-deploys to the real-money server. Any merge happens outside market hours, and never on a Thursday or Friday EB rebalance or sweep morning before fills (spec section 12). After the deploy, run `python3 scripts/check_deployed_code.py`.
- Paper only. No task in this plan may run against alpaca-main credentials, and no test may reach the network. Adapter tests inject `_test_client`, and data-client tests set `_rest_quote_client` / `_option_rest_client`.

## Review Focus

1. **A bracket leg's fill can arrive before the leg is registered.** A gap-up open can fill the take-profit milliseconds after the parent, and `apply_broker_event` raises `LifecycleConflict` for an unknown cid. The expected outcome: the stream event is lost, but `ensure_bracket_legs` plus the next reconcile record that fill exactly once, and the account ends healthy with no `lineage_position_missing_broker`. Pinned in Task 7 (`test_leg_fill_before_registration_is_recovered_exactly_once`).
2. **A bracket parent can partially fill.** Alpaca's legs carry the parent's full quantity. The expected outcome: reconcile owns only the filled shares and stays healthy, and a later leg fill for the filled quantity nets lineage to zero. Pinned in Task 7 (`test_partial_parent_owns_only_filled_shares`).
3. **A leg can fill while its cancel is being confirmed.** The expected outcome: `cancel_orders_confirmed` returns False, the strategy SELL is deferred a tick (no oversell, no accidental short on a margin paper account), and the next tick sees broker truth. Pinned in Task 3 (`test_a_leg_that_fills_during_the_cancel_is_not_confirmed`) and Task 9 (`test_unconfirmed_leg_cancel_defers_the_sell`).
4. **The sign of a short option's quantity must survive every refresh.** The expected outcome: `_option_positions[OCC].qty == -1` after each `refresh_positions`, the OCC symbol never enters the long-only equity `_positions`, a buy-to-close fill moves it to 0 and removes it, and a broker refresh without it empties the map. Pinned in Task 5 (`test_short_option_sign_survives_refresh_and_fills`).
5. **Cash can exactly equal the put collateral.** The expected outcome: `strike x 100 x qty == cash - open collateral - pending collateral` is allowed, and one cent less is refused with `option.collateral_insufficient`. Pinned in Task 6 (`test_cash_exactly_equal_to_collateral_is_allowed_one_cent_short_is_not`).

## File map

| File | Responsibility | Tasks |
|---|---|---|
| `backend/live_orders/types.py` | `OrderIntent` optional fields, identity, `DependencySnapshot` option fields, `ConfirmedFill` multiplier, new `OrderSource` members | 1 |
| `backend/live_orders/store.py` | Row round-trip of the new fields; new keys written only when non-default | 1 |
| `backend/live_orders/__init__.py` | Export `BRACKET_LEG` | 1 |
| `backend/broker_adapters/base.py` | New DTOs, non-abstract option/bracket methods, `OrderRef`/`AccountDTO`/`PositionDTO` fields, `is_bracket_child_order`, `is_risk_reducing_order` | 2 |
| `backend/broker_adapters/errors.py` | `OptionsNotPermitted` | 2 |
| `backend/broker_adapters/alpaca.py` | Bracket submit, legs read, confirmed cancel, closed orders, multi-leg tolerance (3); option data (4); option orders, option positions, option fills (5); `refresh_orders_today` guard (11) | 3, 4, 5, 11 |
| `backend/live_orders/gate.py` | Options branch | 6 |
| `backend/live_orders/service.py` | Transport extras, multiplier cash, leg registration, external fills | 7 |
| `backend/live_orders/reconcile.py` | `held` / `pending_cancel`, short-option ownership | 7 |
| `backend/broker.py` | Dispatcher pop, lane registration (8); bracket intents and leg cancel (9); option execution and snapshot (10); guards and halt (11); activities (12); options-level check (13); P&L rows (14); approvals (15) | 8-15 |
| `backend/live_pending_orders.py` | Pending guard ignores bracket children | 11 |
| `backend/live_risk_state.py` | Kill-level cancel skips risk-reducing orders | 11 |
| `backend/live_broker_fetch.py` | Alpaca P&L fields, option fields | 14 |
| `scripts/check_deployed_code.py`, `backend/api/main.py` | Deploy fingerprint lists | 16 |
| `backend/tests/swing_alpaca_fakes.py` | Network-free alpaca-py fakes (created in 3, extended in 4) | 3, 4 |
| `backend/tests/swing_live_fixtures.py` | Option/bracket intent and snapshot builders | 6 |
| `backend/tests/swing_broker_harness.py` | AST extraction with the free-name sentinel | 8 |

**Decision recorded here, not a task:** `broker_adapters/_classifier.py:262-271` quarantines every negative quantity as long-only. It is **not changed**. It runs only on the non-deferred legacy path (`alpaca.py:353-375`), and the Alpaca runtime always passes `defer_ownership_reconciliation=True` (`broker.py:11178`, `_is_alpaca_runtime`). Short-option ownership is decided by `live_orders/reconcile.py` (Task 7). Changing the legacy classifier would move a code path that no live instance runs, for no benefit. Task 7 adds a source pin, `test_the_alpaca_runtime_defers_to_the_lifecycle_reconciler`, so that if this ever changes, the test fails and someone revisits it.

**Execution order:** Tasks 1-16 in order. Tasks 1-7 touch no `broker.py` line. Task 16 runs LAST, after plans A-backtest, B and C have landed on the branch; its Step 1 verifies that each listed file exists.

---
### Task 1: OrderIntent optional fields, identity, stored row, DependencySnapshot and ConfirmedFill

**Files:**
- Modify: `backend/live_orders/types.py:5-13` (imports), `:28-33` (`OrderSource`), `:91-103` (append helpers after `_session_date`), `:106-282` (`OrderIntent`), `:298-450` (`DependencySnapshot`), `:582-607` (`ConfirmedFill`)
- Modify: `backend/live_orders/store.py:12-19` (imports), `:124-177` (`_intent_to_row`, `_intent_from_row`)
- Modify: `backend/live_orders/__init__.py:28-71` (export `BRACKET_LEG`)
- Modify: `docs/superpowers/plans/2026-09-24-swing-port-interfaces.md` (append section 10, "Additions from plan A-live")
- Test: `backend/tests/test_swing_live_types.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `OrderIntent(..., asset_class="us_equity", order_class=None, take_profit_price=None, stop_loss_price=None, position_intent=None, contract_multiplier=1, underlying=None, option_type=None, strike=None, expiry=None, parent_client_order_id=None, broker_client_order_id=None)`; price-like fields are stored as `Decimal`.
  - `OrderSource.BRACKET_LEG`, `OrderSource.OPTION_ACTIVITY`, and `live_orders.BRACKET_LEG == "bracket_leg"`.
  - `live_orders.types.ASSET_CLASSES`, `POSITION_INTENTS` and `SWING_ROW_DEFAULTS` (a tuple of `(field, default)`).
  - The `DependencySnapshot` option fields and the `ConfirmedFill.asset_class` / `contract_multiplier` fields, exactly as listed in Contract additions 4 and 5.

- [ ] **Step 0: Record the contract additions and run impact analysis**

Append the "Contract additions" list from the top of this plan to `docs/superpowers/plans/2026-09-24-swing-port-interfaces.md` as `## 10. Additions from plan A-live`, copied verbatim (section 9 is already taken by the UI-consumed shapes; do not renumber it). Then run:

```bash
npx gitnexus analyze
grep -rn "OrderIntent(\|_intent_from_row\|_intent_to_row\|DependencySnapshot(\|ConfirmedFill(\|OrderSource\." backend --include=*.py | grep -v "/tests/"
```

Run `mcp__gitnexus__impact` (`direction: "upstream"`) on `OrderIntent`, `DependencySnapshot`, `ConfirmedFill`, `_intent_from_row` and `_intent_to_row`. Expected: GitNexus reports UNKNOWN. The grep shows the real callers: `broker.py:5322, 5740, 9937, 10053, 10155`, `store.py:153` and `service.py`. Report the risk to the user as **HIGH**: every EB order is an `OrderIntent`, and a changed identity dict would re-key stored EB rows so that `_live_identity` denies them with `idempotency.identity_drift`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_live_types.py`:

```python
"""swing-port Task 1: OrderIntent's optional fields, identity and stored row.

EB (doc 200, alpaca-main, real money) must keep every key and row it has
today. The pins below were computed from the PRE-change code on 2026-09-24.
Never edit a pin to make a test pass: a moved pin IS the regression.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from live_orders import (
    BRACKET_LEG,
    ConfirmedFill,
    LifecycleState,
    OrderIntent,
    OrderSide,
    OrderSource,
)
from live_orders.store import _intent_from_row, _intent_to_row
from live_order_task8_helpers import event, intent as helper_intent, snapshot


BUY_AT = datetime(2026, 9, 24, 13, 31, 5, 123456, tzinfo=timezone.utc)
SELL_AT = datetime(2026, 9, 24, 13, 31, 42, 654321, tzinfo=timezone.utc)
MONDAY_1031_ET = datetime(2026, 9, 28, 14, 31, tzinfo=timezone.utc)


def eb_buy(**changes):
    values = dict(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        source=OrderSource.STRATEGY, reason="eb_rebalance", symbol="TQQQ",
        side=OrderSide.BUY, quantity=Decimal("12.34567891"), reduce_only=False,
        decision_at=BUY_AT, quote_at=BUY_AT,
        risk_snapshot_id="risk-state:41:2026-09-24T13:31:00+00:00",
        order_type="market", limit_price=None, tif="day",
        extended_hours=False, reference_price=Decimal("88.12"),
    )
    values.update(changes)
    return OrderIntent(**values)


def eb_sell(**changes):
    values = dict(
        side=OrderSide.SELL, symbol="GLD", quantity=Decimal("4.00021400"),
        reduce_only=True, reason="eb_rotation_trim", decision_at=SELL_AT,
        quote_at=SELL_AT, reference_price=Decimal("331.07"),
    )
    values.update(changes)
    return eb_buy(**values)


EB_KEY_PINS = [
    ("buy", lambda: eb_buy(),
     "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0"),
    ("sell", lambda: eb_sell(),
     "alpacama-13d7255096507c7b1e595f1f7d4e26176b707-0"),
    ("risk_exit_sell", lambda: eb_sell(
        source=OrderSource.RISK_EXIT, reason="risk exit", symbol="TQQQ",
        quantity=Decimal("30.5")),
     "alpacama-12e87dd619816d7a76cca48aee87885a2e3e1-0"),
    ("ext_hours_sell", lambda: eb_sell(
        order_type="limit", limit_price=Decimal("329.42"),
        extended_hours=True, quantity=Decimal("4")),
     "alpacama-f6fe0e571a021062fc2ad71af1e5194a440b9-0"),
    ("retry1_buy", lambda: eb_buy(retry_ordinal=1),
     "alpacama-07837bd72b2f938c1f561f661bf19b31b5805-1"),
    ("manual_close", lambda: eb_sell(
        source=OrderSource.MANUAL, reason="operator close position",
        symbol="XLE", quantity=Decimal("7"), reference_price=None),
     "alpacama-f5367b13b448f0607f52740c00d96444c1ef2-0"),
]

EB_BUY_PAYLOAD = (
    '{"account_id":"brk-alpaca-main","instance_id":"alpaca-main",'
    '"retry_ordinal":0,"session_date":"2026-09-24","side":"buy",'
    '"source":"strategy","symbol":"TQQQ"}'
)

EB_BUY_ROW = {
    "account_id": "brk-alpaca-main", "instance_id": "alpaca-main",
    "source": "strategy", "reason": "eb_rebalance", "symbol": "TQQQ",
    "side": "buy", "quantity": "12.34567891", "reduce_only": False,
    "decision_at": "2026-09-24T13:31:05.123456+00:00",
    "quote_at": "2026-09-24T13:31:05.123456+00:00",
    "risk_snapshot_id": "risk-state:41:2026-09-24T13:31:00+00:00",
    "retry_ordinal": 0, "order_type": "market", "limit_price": None,
    "tif": "day", "extended_hours": False, "reference_price": "88.12",
}


def sto(**changes):
    values = dict(
        account_id="brk-paper", instance_id="swing-paper",
        source=OrderSource.STRATEGY, reason="wheel_sto_put",
        symbol="APH261002P00130000", side=OrderSide.SELL,
        quantity=Decimal("1"), reduce_only=False,
        decision_at=MONDAY_1031_ET, quote_at=MONDAY_1031_ET,
        risk_snapshot_id="risk-1", order_type="limit",
        limit_price=Decimal("1.23"), tif="day", asset_class="us_option",
        position_intent="sell_to_open", contract_multiplier=100,
        underlying="aph", option_type="PUT", strike=130.0,
        expiry="2026-10-02",
    )
    values.update(changes)
    return OrderIntent(**values)


def bracket(**changes):
    values = dict(
        account_id="brk-paper", instance_id="swing-paper",
        source=OrderSource.STRATEGY, reason="swing_entry", symbol="AAPL",
        side=OrderSide.BUY, quantity=Decimal("5"), reduce_only=False,
        decision_at=MONDAY_1031_ET, quote_at=MONDAY_1031_ET,
        risk_snapshot_id="risk-1", order_type="market", tif="gtc",
        reference_price=Decimal("200"), order_class="bracket",
        take_profit_price=218.0, stop_loss_price=188.0,
    )
    values.update(changes)
    return OrderIntent(**values)


# --- EB invariance ----------------------------------------------------------

@pytest.mark.parametrize("name,build,key", EB_KEY_PINS,
                         ids=[p[0] for p in EB_KEY_PINS])
def test_eb_idempotency_keys_are_pinned(name, build, key):
    assert build().idempotency_key == key


def test_eb_buy_identity_payload_is_pinned():
    assert eb_buy().identity_payload == EB_BUY_PAYLOAD


def test_explicit_defaults_do_not_move_an_eb_key():
    assert eb_buy(asset_class="us_equity", contract_multiplier=1,
                  order_class=None).idempotency_key == EB_KEY_PINS[0][2]


def test_eb_row_keeps_its_exact_shape_and_round_trips():
    assert _intent_to_row(eb_buy()) == EB_BUY_ROW
    loaded = _intent_from_row(dict(EB_BUY_ROW))
    assert loaded.idempotency_key == EB_KEY_PINS[0][2]
    assert loaded == eb_buy()


# --- new fields -------------------------------------------------------------

def test_bracket_is_its_own_order_but_leg_prices_are_not_identity():
    plain = bracket(order_class=None, take_profit_price=None,
                    stop_loss_price=None, tif="day")
    entry = bracket()
    drifted = bracket(take_profit_price=219.0, stop_loss_price=187.0,
                      quantity=Decimal("6"))
    assert entry.idempotency_key != plain.idempotency_key
    assert drifted.idempotency_key == entry.idempotency_key
    assert entry.take_profit_price == Decimal("218.0")
    assert entry.stop_loss_price == Decimal("188.0")


def test_a_sell_to_open_rerun_keeps_one_identity_within_the_session():
    """Spec section 9 fix 1: a rerun cannot sell a duplicate put."""
    first = sto()
    later = first.decision_at + timedelta(minutes=7)
    rerun = sto(decision_at=later, quote_at=later, quantity=Decimal("2"),
                limit_price=Decimal("1.10"), risk_snapshot_id="risk-2")
    assert rerun.idempotency_key == first.idempotency_key
    assert rerun.same_identity(first)
    other = sto(symbol="APH261002P00125000", strike=125.0)
    assert other.idempotency_key != first.idempotency_key
    tomorrow = first.decision_at + timedelta(days=1)
    assert sto(decision_at=tomorrow, quote_at=tomorrow).idempotency_key \
        != first.idempotency_key


def test_option_fields_are_normalized():
    order = sto()
    assert order.underlying == "APH"
    assert order.option_type == "put"
    assert order.strike == Decimal("130.0")
    assert order.contract_multiplier == 100
    assert '"asset_class":"us_option"' in order.identity_payload
    assert '"strike":"130"' in order.identity_payload
    assert "decision_minute" not in order.identity_payload


def test_a_bracket_leg_is_keyed_by_the_broker_minted_client_order_id():
    parent = bracket()
    leg = OrderIntent(
        account_id="brk-paper", instance_id="swing-paper",
        source=OrderSource.BRACKET_LEG, reason="bracket_take_profit",
        symbol="AAPL", side=OrderSide.SELL, quantity=Decimal("5"),
        reduce_only=True, decision_at=MONDAY_1031_ET,
        quote_at=MONDAY_1031_ET, risk_snapshot_id="risk-1",
        order_type="limit", limit_price=Decimal("218"), tif="gtc",
        take_profit_price=218.0, stop_loss_price=188.0,
        parent_client_order_id=parent.idempotency_key,
        broker_client_order_id="7d1e0a2c-5b8f-4c1e-9f7a-leg-tp",
    )
    assert leg.idempotency_key == "7d1e0a2c-5b8f-4c1e-9f7a-leg-tp"
    assert BRACKET_LEG == "bracket_leg" == OrderSource.BRACKET_LEG.value
    assert _intent_from_row(_intent_to_row(leg)) == leg
    with pytest.raises(ValueError, match="reserved"):
        eb_buy(broker_client_order_id="alpacama-forged-0")


@pytest.mark.parametrize("build", [sto, bracket])
def test_new_field_rows_round_trip_exactly(build):
    order = build()
    row = _intent_to_row(order)
    assert _intent_from_row(row) == order
    assert _intent_from_row(row).idempotency_key == order.idempotency_key


@pytest.mark.parametrize("changes", [
    {"side": OrderSide.SELL, "reduce_only": True},
    {"quantity": Decimal("5.5")},
    {"take_profit_price": 180.0},
    {"stop_loss_price": None},
    {"tif": "day"},
    {"order_type": "limit", "limit_price": Decimal("200")},
])
def test_a_malformed_bracket_fails_at_construction(changes):
    with pytest.raises((TypeError, ValueError)):
        bracket(**changes)


@pytest.mark.parametrize("changes", [
    {"side": OrderSide.BUY},
    {"contract_multiplier": 1},
    {"quantity": Decimal("1.5")},
    {"order_type": "limit", "extended_hours": True},
    {"strike": None},
    {"option_type": "straddle"},
    {"expiry": "10/02/2026"},
    {"underlying": ""},
    {"position_intent": "sell_short"},
    {"contract_multiplier": True},
])
def test_a_malformed_option_intent_fails_at_construction(changes):
    with pytest.raises((TypeError, ValueError)):
        sto(**changes)


def test_option_fields_on_an_equity_intent_fail_closed():
    with pytest.raises(ValueError):
        eb_buy(strike=130.0)
    with pytest.raises(ValueError):
        eb_buy(position_intent="buy_to_open")
    with pytest.raises(ValueError):
        eb_buy(contract_multiplier=100)


# --- DependencySnapshot / ConfirmedFill ---------------------------------------

def test_only_an_option_snapshot_may_carry_a_short_position():
    order = helper_intent()
    with pytest.raises(ValueError, match="position_quantity"):
        snapshot(order, position_quantity=Decimal("-1"))
    short = snapshot(
        order, asset_class="us_option", position_quantity=Decimal("-1"),
        account_equity=50000, open_short_put_collateral="13000",
        underlying_put_collateral=13000, regular_session_open=1)
    assert short.position_quantity == Decimal("-1")
    assert short.account_equity == Decimal("50000")
    assert short.open_short_put_collateral == Decimal("13000")
    assert short.regular_session_open is True
    plain = snapshot(order)
    assert plain.asset_class == "us_equity"
    assert plain.open_short_put_collateral is None
    assert plain.pending_sell_to_open_collateral == Decimal("0")
    assert plain.max_underlying_collateral_fraction == Decimal("0.25")
    with pytest.raises(ValueError):
        snapshot(order, asset_class="us_option",
                 max_underlying_collateral_fraction=Decimal("1.5"))


def test_confirmed_fill_defaults_to_one_share_per_unit():
    order = helper_intent()
    filled = event(order, state=LifecycleState.FILLED,
                   cumulative=Decimal("5"), average=Decimal("100"))
    fill = ConfirmedFill(
        event=filled, incremental_quantity=Decimal("5"),
        incremental_price=Decimal("100"), incremental_fees=Decimal("0"),
        position_delta=Decimal("5"), cash_delta=Decimal("-500"))
    assert fill.asset_class == "us_equity"
    assert fill.contract_multiplier == 1
    option_fill = replace(fill, asset_class="us_option",
                          contract_multiplier=100)
    assert option_fill.contract_multiplier == 100
    with pytest.raises(ValueError):
        replace(fill, contract_multiplier=0)
    with pytest.raises(ValueError):
        replace(fill, asset_class="crypto")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_live_types.py -q -p no:cacheprovider`
Expected: a collection error, `ImportError: cannot import name 'BRACKET_LEG' from 'live_orders'`.

- [ ] **Step 3: Write the minimal implementation**

In `backend/live_orders/types.py`:

(a) Change the import line `from datetime import datetime, timedelta, timezone` to:

```python
from datetime import date, datetime, timedelta, timezone
```

(b) Replace the `OrderSource` class (lines 28-32) with the following, and add the constants right after it:

```python
class OrderSource(str, Enum):
    STRATEGY = "strategy"
    MANUAL = "manual"
    RISK_EXIT = "risk_exit"
    RESIDUAL_SLEEVE = "residual_sleeve"
    # swing-port: a reduce-only child leg of a bracket parent, registered from
    # the parent's nested legs so a leg fill resolves to a known record.
    BRACKET_LEG = "bracket_leg"
    # swing-port: a broker-originated fill with no order behind it (an option
    # assignment adds or removes the underlying's shares).
    OPTION_ACTIVITY = "option_activity"


#: The interfaces contract's name for the bracket-leg lifecycle source.
BRACKET_LEG = OrderSource.BRACKET_LEG.value

#: swing-port value sets.
ASSET_CLASSES = ("us_equity", "us_option")
POSITION_INTENTS = ("buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close")
_POSITION_INTENT_SIDE = {
    "buy_to_open": "buy",
    "buy_to_close": "buy",
    "sell_to_open": "sell",
    "sell_to_close": "sell",
}
#: Sources whose rows are keyed by an id the BROKER minted, not by our hash.
_BROKER_KEYED_SOURCES = frozenset({"bracket_leg", "option_activity"})
#: (field, default). A field joins the identity dict only when it differs from
#: this default -- which is what keeps every stored EB row on its existing key.
_SWING_IDENTITY_DEFAULTS = (
    ("asset_class", "us_equity"),
    ("order_class", None),
    ("position_intent", None),
    ("contract_multiplier", 1),
    ("underlying", None),
    ("option_type", None),
    ("strike", None),
    ("expiry", None),
    ("parent_client_order_id", None),
    ("broker_client_order_id", None),
)
#: The stored row carries the identity fields plus the two leg prices. The leg
#: prices are row-only: they are sizing, like limit_price, and must never
#: re-key a re-emitted entry.
SWING_ROW_DEFAULTS = _SWING_IDENTITY_DEFAULTS + (
    ("take_profit_price", None),
    ("stop_loss_price", None),
)
```

(c) Directly after `_session_date` (ends at line 103), add:

```python
def _optional_text(value, *, case: str = "") -> Optional[str]:
    """A stripped string, or None for None or blank; ``case`` folds it."""
    if value is None:
        return None
    text = str(value).strip()
    if case == "upper":
        text = text.upper()
    elif case == "lower":
        text = text.lower()
    return text or None


def _swing_fields(
    intent, *, source, side, quantity, order_type, tif, extended_hours
) -> dict:
    """Normalize and validate the swing-port fields of one ``OrderIntent``.

    Defaults pass through untouched, so an EB intent comes back exactly as the
    defaults and nothing about its identity or its stored row changes.
    """
    asset_class = _optional_text(intent.asset_class, case="lower") or "us_equity"
    if asset_class not in ASSET_CLASSES:
        raise ValueError(f"unsupported asset_class: {intent.asset_class!r}")
    order_class = _optional_text(intent.order_class, case="lower")
    if order_class not in (None, "bracket"):
        raise ValueError(f"unsupported order_class: {intent.order_class!r}")
    take_profit = _optional_decimal(intent.take_profit_price, "take_profit_price")
    stop_loss = _optional_decimal(intent.stop_loss_price, "stop_loss_price")
    for name, value in (
        ("take_profit_price", take_profit),
        ("stop_loss_price", stop_loss),
    ):
        if value is not None and value <= 0:
            raise ValueError(f"{name} must be > 0")
    position_intent = _optional_text(intent.position_intent, case="lower")
    if position_intent is not None and position_intent not in POSITION_INTENTS:
        raise ValueError(f"unsupported position_intent: {intent.position_intent!r}")
    raw_multiplier = intent.contract_multiplier
    if isinstance(raw_multiplier, bool):
        raise TypeError("contract_multiplier must be an integer")
    try:
        multiplier = int(raw_multiplier)
    except (TypeError, ValueError) as exc:
        raise TypeError("contract_multiplier must be an integer") from exc
    if multiplier != raw_multiplier:
        raise ValueError("contract_multiplier must be an integer")
    underlying = _optional_text(intent.underlying, case="upper")
    option_type = _optional_text(intent.option_type, case="lower")
    strike = _optional_decimal(intent.strike, "strike")
    expiry = _optional_text(intent.expiry)
    parent = _optional_text(intent.parent_client_order_id)
    broker_key = _optional_text(intent.broker_client_order_id)
    whole = quantity == quantity.to_integral_value()

    if order_class == "bracket":
        if side is not OrderSide.BUY:
            raise ValueError("a bracket parent must be a BUY")
        if order_type != "market" or tif != "gtc" or extended_hours:
            raise ValueError("a bracket parent is a regular-hours market GTC order")
        if not whole:
            raise ValueError("a bracket parent needs whole shares")
        if take_profit is None or stop_loss is None:
            raise ValueError("a bracket needs take_profit_price and stop_loss_price")
        if stop_loss >= take_profit:
            raise ValueError("stop_loss_price must be below take_profit_price")
    if asset_class == "us_option":
        if position_intent is None:
            raise ValueError("an option order needs position_intent")
        if _POSITION_INTENT_SIDE[position_intent] != side.value:
            raise ValueError("position_intent contradicts side")
        if multiplier != 100:
            raise ValueError("an option contract_multiplier must be 100")
        if not whole:
            raise ValueError("options trade whole contracts")
        if extended_hours:
            raise ValueError("options do not trade extended hours")
        if order_class is not None:
            raise ValueError("an option order cannot be a bracket")
        if underlying is None:
            raise ValueError("an option order needs underlying")
        if option_type not in ("put", "call"):
            raise ValueError("option_type must be put or call")
        if strike is None or strike <= 0:
            raise ValueError("an option order needs strike > 0")
        try:
            date.fromisoformat(expiry or "")
        except ValueError as exc:
            raise ValueError("expiry must be YYYY-MM-DD") from exc
    else:
        if position_intent is not None:
            raise ValueError("position_intent is for options only")
        if multiplier != 1:
            raise ValueError("an equity contract_multiplier must be 1")
        if any(value is not None for value in (underlying, option_type, strike, expiry)):
            raise ValueError("underlying/option_type/strike/expiry are for options only")
    if broker_key is not None and source.value not in _BROKER_KEYED_SOURCES:
        raise ValueError(
            "broker_client_order_id is reserved for bracket legs and option activity"
        )
    return {
        "asset_class": asset_class,
        "order_class": order_class,
        "take_profit_price": take_profit,
        "stop_loss_price": stop_loss,
        "position_intent": position_intent,
        "contract_multiplier": multiplier,
        "underlying": underlying,
        "option_type": option_type,
        "strike": strike,
        "expiry": expiry,
        "parent_client_order_id": parent,
        "broker_client_order_id": broker_key,
    }
```

(d) In `OrderIntent`, replace:

```python
    reference_price: Optional[Decimal] = None
    identity_payload: str = field(init=False)
```

with:

```python
    reference_price: Optional[Decimal] = None
    # swing-port (interfaces doc sections 2 and 9). Each field joins the
    # identity only when it differs from its default, so stored EB rows keep
    # their keys; take_profit_price and stop_loss_price never join it.
    asset_class: str = "us_equity"
    order_class: Optional[str] = None
    take_profit_price: Optional[Decimal] = None
    stop_loss_price: Optional[Decimal] = None
    position_intent: Optional[str] = None
    contract_multiplier: int = 1
    underlying: Optional[str] = None
    option_type: Optional[str] = None
    strike: Optional[Decimal] = None
    expiry: Optional[str] = None
    parent_client_order_id: Optional[str] = None
    broker_client_order_id: Optional[str] = None
    identity_payload: str = field(init=False)
```

(e) In `OrderIntent.__post_init__`, directly after the two lines

```python
        if reference_price is not None and reference_price <= 0:
            raise ValueError("reference_price must be > 0")
```

insert:

```python
        swing = _swing_fields(
            self,
            source=source,
            side=side,
            quantity=quantity,
            order_type=order_type,
            tif=tif,
            extended_hours=extended_hours,
        )
```

In the `for name, value in (...)` setattr tuple, add `*swing.items(),` as the last element, after `("reference_price", reference_price),`.

Replace:

```python
        if side is not OrderSide.BUY:
            identity["decision_minute"] = decision_at.replace(
```

with:

```python
        # swing-port: an option SELL (sell_to_open) is keyed on the SESSION like
        # a buy, never on the minute: spec section 9 fix 1, a rerun must not
        # mint a second put of the same contract. Equity sells are unchanged.
        if side is not OrderSide.BUY and swing["asset_class"] == "us_equity":
            identity["decision_minute"] = decision_at.replace(
```

Replace the block that starts at `payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))` and ends at `key = f"{instance_prefix}-{digest[:digest_room]}{retry_suffix}"` with:

```python
        for name, default in _SWING_IDENTITY_DEFAULTS:
            value = swing[name]
            if value != default:
                identity[name] = (
                    _normalized_decimal(value)
                    if isinstance(value, Decimal)
                    else value
                )
        payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if swing["broker_client_order_id"] is not None:
            # A bracket leg's client order id is minted by Alpaca, and an
            # assignment has no order at all: the row must carry the id the
            # broker reports, or no event could ever resolve to it.
            key = swing["broker_client_order_id"]
        else:
            # Preserve the existing clean-room classifier contract: every
            # strategy-owned WAL row begins with this instance's 8-char prefix.
            instance_prefix = re.sub(r"[^A-Za-z0-9]", "", instance_id)[:8] or "x"
            retry_suffix = f"-{retry_ordinal}"
            digest_room = 48 - len(instance_prefix) - 1 - len(retry_suffix)
            key = f"{instance_prefix}-{digest[:digest_room]}{retry_suffix}"
```

(f) In `DependencySnapshot`, add after `max_reference_price_deviation: Decimal = Decimal("0.001")`:

```python
    # swing-port (options branch of the gate). Every default is the equity
    # snapshot EB builds today; only an option snapshot sets these.
    asset_class: str = "us_equity"
    regular_session_open: Optional[bool] = None
    account_equity: Optional[Decimal] = None
    open_short_put_collateral: Optional[Decimal] = None
    pending_sell_to_open_collateral: Decimal = Decimal("0")
    underlying_put_collateral: Optional[Decimal] = None
    max_underlying_collateral_fraction: Decimal = Decimal("0.25")
```

In `DependencySnapshot.__post_init__`, replace:

```python
        if position_quantity < 0:
            raise ValueError("position_quantity must be >= 0")
```

with:

```python
        asset_class = _optional_text(self.asset_class, case="lower") or "us_equity"
        if asset_class not in ASSET_CLASSES:
            raise ValueError(f"unsupported asset_class: {self.asset_class!r}")
        # swing-port: a short option is a negative position. Nothing else may
        # be one; the equity book is long-only.
        if position_quantity < 0 and asset_class != "us_option":
            raise ValueError("position_quantity must be >= 0")
        account_equity = _optional_decimal(self.account_equity, "account_equity")
        open_short_put_collateral = _optional_decimal(
            self.open_short_put_collateral, "open_short_put_collateral"
        )
        pending_sell_to_open_collateral = _decimal(
            self.pending_sell_to_open_collateral,
            "pending_sell_to_open_collateral",
        )
        underlying_put_collateral = _optional_decimal(
            self.underlying_put_collateral, "underlying_put_collateral"
        )
        max_underlying_collateral_fraction = _decimal(
            self.max_underlying_collateral_fraction,
            "max_underlying_collateral_fraction",
        )
        for name, value in (
            ("open_short_put_collateral", open_short_put_collateral),
            ("pending_sell_to_open_collateral", pending_sell_to_open_collateral),
            ("underlying_put_collateral", underlying_put_collateral),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be >= 0")
        if not Decimal("0") < max_underlying_collateral_fraction <= Decimal("1"):
            raise ValueError("max_underlying_collateral_fraction must be in (0, 1]")
        regular_session_open = (
            None
            if self.regular_session_open is None
            else bool(self.regular_session_open)
        )
```

In the setattr tuple at the end of `DependencySnapshot.__post_init__`, add after `("authorized_sources", sources),`:

```python
            ("asset_class", asset_class),
            ("regular_session_open", regular_session_open),
            ("account_equity", account_equity),
            ("open_short_put_collateral", open_short_put_collateral),
            ("pending_sell_to_open_collateral", pending_sell_to_open_collateral),
            ("underlying_put_collateral", underlying_put_collateral),
            (
                "max_underlying_collateral_fraction",
                max_underlying_collateral_fraction,
            ),
```

(g) In `ConfirmedFill`, add after `cash_delta: Decimal`:

```python
    # swing-port: a contract fill moves `quantity x price x multiplier` cash.
    asset_class: str = "us_equity"
    contract_multiplier: int = 1
```

At the end of `ConfirmedFill.__post_init__`, append:

```python
        asset_class = _optional_text(self.asset_class, case="lower") or "us_equity"
        if asset_class not in ASSET_CLASSES:
            raise ValueError(f"unsupported asset_class: {self.asset_class!r}")
        if isinstance(self.contract_multiplier, bool):
            raise TypeError("contract_multiplier must be an integer")
        multiplier = int(self.contract_multiplier)
        if multiplier != self.contract_multiplier or multiplier < 1:
            raise ValueError("contract_multiplier must be an integer >= 1")
        object.__setattr__(self, "asset_class", asset_class)
        object.__setattr__(self, "contract_multiplier", multiplier)
```

In `backend/live_orders/store.py`, change the `from .types import (...)` block to add `SWING_ROW_DEFAULTS,`. Replace `_intent_to_row` and `_intent_from_row` (lines 124-177) with:

```python
def _intent_to_row(intent: OrderIntent) -> dict:
    row = {
        "account_id": intent.account_id,
        "instance_id": intent.instance_id,
        "source": intent.source.value,
        "reason": intent.reason,
        "symbol": intent.symbol,
        "side": intent.side.value,
        "quantity": str(intent.quantity),
        "reduce_only": intent.reduce_only,
        "decision_at": intent.decision_at.isoformat(),
        "quote_at": intent.quote_at.isoformat(),
        "risk_snapshot_id": intent.risk_snapshot_id,
        "retry_ordinal": intent.retry_ordinal,
        "order_type": intent.order_type,
        "limit_price": (
            str(intent.limit_price) if intent.limit_price is not None else None
        ),
        "tif": intent.tif,
        "extended_hours": intent.extended_hours,
        "reference_price": (
            str(intent.reference_price)
            if intent.reference_price is not None
            else None
        ),
    }
    # swing-port: a new field is written only when it differs from its
    # default, so an EB row keeps exactly the keys above (pinned in
    # tests/test_swing_live_types.py).
    for name, default in SWING_ROW_DEFAULTS:
        value = getattr(intent, name)
        if value != default:
            row[name] = str(value) if isinstance(value, Decimal) else value
    return row


def _row_decimal(row: Mapping, name: str) -> Optional[Decimal]:
    value = row.get(name)
    return Decimal(str(value)) if value is not None else None


def _intent_from_row(row: Mapping) -> OrderIntent:
    return OrderIntent(
        account_id=row["account_id"],
        instance_id=row["instance_id"],
        source=OrderSource(row["source"]),
        reason=row["reason"],
        symbol=row["symbol"],
        side=OrderSide(row["side"]),
        quantity=Decimal(row["quantity"]),
        reduce_only=bool(row["reduce_only"]),
        decision_at=datetime.fromisoformat(row["decision_at"]),
        quote_at=datetime.fromisoformat(row["quote_at"]),
        risk_snapshot_id=row["risk_snapshot_id"],
        retry_ordinal=int(row.get("retry_ordinal", 0)),
        order_type=row.get("order_type", "market"),
        limit_price=(
            Decimal(row["limit_price"]) if row.get("limit_price") is not None else None
        ),
        tif=row.get("tif", "day"),
        extended_hours=bool(row.get("extended_hours", False)),
        reference_price=(
            Decimal(row["reference_price"])
            if row.get("reference_price") is not None
            else None
        ),
        # swing-port: absent keys are the defaults an EB row was written with.
        asset_class=row.get("asset_class", "us_equity"),
        order_class=row.get("order_class"),
        take_profit_price=_row_decimal(row, "take_profit_price"),
        stop_loss_price=_row_decimal(row, "stop_loss_price"),
        position_intent=row.get("position_intent"),
        contract_multiplier=int(row.get("contract_multiplier", 1)),
        underlying=row.get("underlying"),
        option_type=row.get("option_type"),
        strike=_row_decimal(row, "strike"),
        expiry=row.get("expiry"),
        parent_client_order_id=row.get("parent_client_order_id"),
        broker_client_order_id=row.get("broker_client_order_id"),
    )
```

In `backend/live_orders/__init__.py`, add `BRACKET_LEG,` to the `from .types import (...)` block and `"BRACKET_LEG",` to `__all__`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_live_types.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Run the live-order regression suites**

Run: `python3 -m pytest backend/tests/test_live_order_types.py backend/tests/test_live_order_store.py backend/tests/test_live_order_service.py backend/tests/test_live_order_gate.py backend/tests/test_live_order_recovery.py backend/tests/test_live_order_partial_fills.py backend/tests/test_live_order_retry.py backend/tests/test_live_order_crash_matrix.py backend/tests/test_live_order_chaos.py backend/tests/test_live_order_silent_swallows.py backend/tests/test_live_order_wal.py backend/tests/test_live_dependency_freshness.py backend/tests/test_manual_order_gate.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 6: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()`. Expected changed symbols: only `OrderSource`, `_optional_text`, `_swing_fields`, `OrderIntent`, `DependencySnapshot`, `ConfirmedFill`, `_intent_to_row`, `_row_decimal` and `_intent_from_row`.

```bash
git add backend/live_orders/types.py backend/live_orders/store.py backend/live_orders/__init__.py backend/tests/test_swing_live_types.py docs/superpowers/plans/2026-09-24-swing-port-interfaces.md
git commit -F - <<'EOF'
feat(live-orders): optional bracket and option fields on OrderIntent

Adds the swing-port fields (asset_class, order_class, leg prices,
position_intent, contract multiplier, contract fields, parent and
broker-minted client order ids). A field joins the identity and the
stored row only when it differs from its default, so every EB key and
stored row is unchanged; pinned against keys computed from the pre-change
code. Option sells are keyed on the session (spec section 9 fix 1).
DependencySnapshot accepts a negative position only for us_option and
gains the collateral fields the options gate reads; ConfirmedFill carries
the contract multiplier.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 2: Adapter contract: DTOs, non-abstract methods, OrderRef/AccountDTO/PositionDTO fields, OptionsNotPermitted

**Files:**
- Modify: `backend/broker_adapters/base.py:17-58` (dataclasses), `:69-214` (`BrokerAdapter`); append helpers at the end of the file
- Modify: `backend/broker_adapters/errors.py:55-76`
- Test: `backend/tests/test_swing_adapter_contract.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - The `OptionContractDTO`, `OptionSnapshotDTO`, `OptionPositionDTO` and `OptionActivityDTO` DTOs (frozen, exactly as in the interfaces doc, section 3).
  - `OrderRef` gains `order_class`, `legs: tuple`, `position_intent`, `asset_class`, `order_type`, `limit_price` and `stop_price`.
  - `AccountDTO` gains the four options fields.
  - `PositionDTO` gains the option display fields from Contract additions 7.
  - `BrokerAdapter` gains the non-abstract methods `get_option_chain`, `get_option_contracts`, `get_option_snapshots`, `list_option_positions`, `get_account_options`, `get_option_activities`, `get_order_with_legs`, `cancel_orders_confirmed`, `list_closed_orders`, `get_daily_bars` and `get_latest_trades`; each raises `NotImplementedError`.
  - The pure helpers `is_bracket_child_order(order) -> bool` and `is_risk_reducing_order(order) -> bool`.
  - `errors.OptionsNotPermitted`, which is definitive and in `NON_RETRYABLE`.

- [ ] **Step 0: Impact analysis**

```bash
grep -rn "OrderRef(\|PositionDTO(\|AccountDTO(\|NON_RETRYABLE\|BrokerAdapter)" backend --include=*.py | grep -v "/tests/"
```

Run `mcp__gitnexus__impact` upstream on `OrderRef`, `PositionDTO`, `AccountDTO` and `BrokerAdapter`. Expected callers: `alpaca.py`, `binanceus.py` and `kalshi/client.py` (a separate OrderRef-like). Risk: **HIGH** (the adapter DTOs are on EB's path), mitigated because every new field has a default and every new method is non-abstract.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_adapter_contract.py`:

```python
"""swing-port Task 2: the adapter contract grows without touching Binance.US
or the abstract surface every adapter must implement."""
import dataclasses
from types import SimpleNamespace

import pytest

from broker_adapters.base import (
    AccountDTO,
    BrokerAdapter,
    OptionActivityDTO,
    OptionContractDTO,
    OptionPositionDTO,
    OptionSnapshotDTO,
    OrderRef,
    PositionDTO,
    is_bracket_child_order,
    is_risk_reducing_order,
)
from broker_adapters.binanceus import BinanceUSAdapter
from broker_adapters.errors import (
    BrokerError,
    FractionalNotAllowed,
    NON_RETRYABLE,
    OptionsNotPermitted,
)


#: The abstract surface as it stood on 2026-09-24. New methods must be
#: non-abstract, or Binance.US (and every test double) stops instantiating.
ABSTRACT_2026_09_24 = {
    "submit_order", "cancel_order", "get_order", "get_order_by_client_id",
    "list_open_orders", "refresh_positions", "refresh_cash",
    "refresh_account", "is_market_open", "health_check", "buy", "sell",
    "execute_signal", "get_positions", "get_positions_value",
    "get_portfolio_value", "get_trade_history", "get_portfolio_history",
    "get_cash", "get_available_cash", "get_initial_value",
    "save_portfolio_snapshot", "print_portfolio",
}

NEW_METHODS = {
    "get_option_chain": (("APH",), {}),
    "get_option_contracts": (("APH",), {}),
    "get_option_snapshots": ((["APH261002P00130000"],), {}),
    "list_option_positions": ((), {}),
    "get_account_options": ((), {}),
    "get_option_activities": ((), {}),
    "get_order_with_legs": (("broker-1",), {}),
    "cancel_orders_confirmed": ((["broker-1"],), {}),
    "list_closed_orders": ((["AAPL"], "2026-09-01"), {}),
    "get_daily_bars": ((["AAPL"], 30), {}),
    "get_latest_trades": ((["AAPL"],), {}),
}


def _concrete():
    body = {name: (lambda self, *a, **k: None)
            for name in BrokerAdapter.__abstractmethods__}
    return type("Concrete", (BrokerAdapter,), body)()


def test_the_abstract_surface_is_unchanged():
    assert set(BrokerAdapter.__abstractmethods__) == ABSTRACT_2026_09_24


@pytest.mark.parametrize("name", sorted(NEW_METHODS))
def test_new_methods_are_non_abstract_and_refuse_loudly(name):
    args, kwargs = NEW_METHODS[name]
    with pytest.raises(NotImplementedError, match="Concrete"):
        getattr(_concrete(), name)(*args, **kwargs)


@pytest.mark.parametrize("name", sorted(NEW_METHODS))
def test_binanceus_inherits_the_refusals_untouched(name):
    assert getattr(BinanceUSAdapter, name) is getattr(BrokerAdapter, name)


def test_order_ref_defaults_leave_old_callers_unchanged():
    ref = OrderRef("b-1", "c-1", "AAPL", "buy", 5.0, "new")
    assert (ref.order_class, ref.legs, ref.position_intent, ref.asset_class,
            ref.order_type, ref.limit_price, ref.stop_price) == (
        None, (), None, None, None, None, None)


def test_account_and_position_dtos_gain_optional_fields():
    account = AccountDTO(equity=1.0, pattern_day_trader=False,
                         daytrade_count=0, account_blocked=False,
                         trading_blocked=False)
    assert account.options_trading_level is None
    assert account.options_buying_power is None
    position = PositionDTO("AAPL", 1.0, 100.0, 100.0)
    assert position.asset_class is None and position.multiplier == 1


def test_option_dtos_are_frozen():
    contract = OptionContractDTO("APH261002P00130000", "APH", "put", 130.0,
                                 "2026-10-02", 120, 1.1)
    position = OptionPositionDTO("APH261002P00130000", "APH", "put", 130.0,
                                 "2026-10-02", -1, 1.23, 1.1, -110.0, 13.0)
    snap = OptionSnapshotDTO("APH261002P00130000", 1.0, 1.2, 1.1, 0.3,
                             -0.25, 0.05, -0.04, 0.1, None)
    activity = OptionActivityDTO("a-1", "OPASN", "APH261002P00130000",
                                 -1.0, "2026-10-02", None)
    assert position.multiplier == 100
    for dto in (contract, position, snap, activity):
        with pytest.raises(dataclasses.FrozenInstanceError):
            dto.symbol = "X"


@pytest.mark.parametrize("order,child,reducing", [
    (SimpleNamespace(order_class="bracket", side="sell", status="new"), True, True),
    (SimpleNamespace(order_class=SimpleNamespace(value="bracket"),
                     side=SimpleNamespace(value="sell"),
                     status=SimpleNamespace(value="held")), True, True),
    (SimpleNamespace(order_class="bracket", side="buy", status="filled"), False, False),
    (SimpleNamespace(order_class="oco", side="sell", status="new"), True, True),
    (SimpleNamespace(order_class="simple", side="sell", status="new"), False, False),
    (SimpleNamespace(order_class="simple", side="buy", status="held"), False, False),
    (SimpleNamespace(side="sell", status="new"), False, False),
    (SimpleNamespace(order_class="simple", side="buy", status="new",
                     position_intent="buy_to_close"), False, True),
    (SimpleNamespace(order_class="simple", side="sell", status="new",
                     position_intent=SimpleNamespace(value="sell_to_open")), False, False),
])
def test_bracket_child_and_risk_reducing_classification(order, child, reducing):
    assert is_bracket_child_order(order) is child
    assert is_risk_reducing_order(order) is reducing


def test_options_not_permitted_is_definitive_and_never_retried():
    assert issubclass(OptionsNotPermitted, BrokerError)
    assert not issubclass(OptionsNotPermitted, FractionalNotAllowed)
    assert OptionsNotPermitted.broker_definitive_rejection is True
    assert OptionsNotPermitted in NON_RETRYABLE
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_adapter_contract.py -q -p no:cacheprovider`
Expected: a collection error, `ImportError: cannot import name 'OptionActivityDTO' from 'broker_adapters.base'`.

- [ ] **Step 3: Write the minimal implementation**

In `backend/broker_adapters/base.py`, replace the `OrderRef`, `PositionDTO` and `AccountDTO` dataclasses with:

```python
@dataclass
class OrderRef:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: str
    qty: float
    status: str
    filled_qty: float = 0.0
    filled_avg_price: Optional[float] = None
    submitted_at_utc: Optional[datetime] = None
    # swing-port (interfaces doc section 3). Defaults keep every old caller.
    order_class: Optional[str] = None
    legs: tuple = ()
    position_intent: Optional[str] = None
    asset_class: Optional[str] = None
    # swing-port (plan A-live contract additions): tell a take-profit leg
    # (limit) from a stop-loss leg (stop), and carry their prices.
    order_type: Optional[str] = None
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None


@dataclass
class PositionDTO:
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    created_at_utc: Optional[datetime] = None
    # swing-port: filled only for option rows (refresh_positions), so every
    # equity row is exactly what it was.
    asset_class: Optional[str] = None
    side: Optional[str] = None
    unrealized_pl: Optional[float] = None
    unrealized_plpc: Optional[float] = None
    current_price: Optional[float] = None
    multiplier: int = 1
    underlying: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None
```

Keep `CashDTO` as it is. Replace `AccountDTO` with:

```python
@dataclass
class AccountDTO:
    equity: float
    pattern_day_trader: bool
    daytrade_count: int
    account_blocked: bool
    trading_blocked: bool
    # Added 2026-04-21 so LiveState mirrors the Alpaca UI (Buying Power +
    # authoritative Daily Change). Optional/default to 0 for older callers.
    buying_power: float = 0.0
    last_equity: float = 0.0
    cash: float = 0.0
    # swing-port: options approval and buying power (interfaces doc section 3).
    options_trading_level: Optional[int] = None
    options_approved_level: Optional[int] = None
    options_buying_power: Optional[float] = None
    non_marginable_buying_power: Optional[float] = None


@dataclass(frozen=True)
class OptionContractDTO:
    symbol: str
    underlying: str
    option_type: str
    strike: float
    expiration: str
    open_interest: Optional[int]
    close_price: Optional[float]


@dataclass(frozen=True)
class OptionSnapshotDTO:
    symbol: str
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    quote_ts: Optional[str]


@dataclass(frozen=True)
class OptionPositionDTO:
    symbol: str
    underlying: str
    option_type: str
    strike: float
    expiry: str
    qty: int                      # signed; negative = short
    avg_entry_price: float        # per-share premium
    current_price: Optional[float]
    market_value: Optional[float]
    unrealized_pl: Optional[float]
    multiplier: int = 100


@dataclass(frozen=True)
class OptionActivityDTO:
    id: str
    activity_type: str            # "OPASN" | "OPEXP" | "OPEXC"
    symbol: str
    qty: float
    date: str
    price: Optional[float]
```

At the end of the `BrokerAdapter` class (after `print_portfolio`), add:

```python
    # --- swing-port: options, brackets, reference data ------------------------
    # Non-abstract on purpose: an adapter that does not trade options
    # (Binance.US, every test double) inherits these and refuses loudly only
    # if something actually asks it to.
    def get_option_chain(self, underlying, *, option_type=None,
                         expiration_gte=None, expiration_lte=None,
                         strike_gte=None, strike_lte=None) -> dict:
        raise NotImplementedError(f"{type(self).__name__} does not support get_option_chain")

    def get_option_contracts(self, underlying, *, option_type=None,
                             expiration_gte=None, expiration_lte=None,
                             strike_gte=None, strike_lte=None) -> list:
        raise NotImplementedError(f"{type(self).__name__} does not support get_option_contracts")

    def get_option_snapshots(self, contracts) -> dict:
        raise NotImplementedError(f"{type(self).__name__} does not support get_option_snapshots")

    def list_option_positions(self) -> list:
        raise NotImplementedError(f"{type(self).__name__} does not support list_option_positions")

    def get_account_options(self) -> dict:
        raise NotImplementedError(f"{type(self).__name__} does not support get_account_options")

    def get_option_activities(self, types=("OPASN", "OPEXP", "OPEXC"),
                              after=None) -> list:
        raise NotImplementedError(f"{type(self).__name__} does not support get_option_activities")

    def get_order_with_legs(self, order_id) -> "OrderRef":
        raise NotImplementedError(f"{type(self).__name__} does not support get_order_with_legs")

    def cancel_orders_confirmed(self, order_ids, timeout_s: float = 10.0) -> bool:
        raise NotImplementedError(f"{type(self).__name__} does not support cancel_orders_confirmed")

    def list_closed_orders(self, symbols, after) -> list:
        raise NotImplementedError(f"{type(self).__name__} does not support list_closed_orders")

    def get_daily_bars(self, symbols, days) -> dict:
        raise NotImplementedError(f"{type(self).__name__} does not support get_daily_bars")

    def get_latest_trades(self, symbols) -> dict:
        raise NotImplementedError(f"{type(self).__name__} does not support get_latest_trades")
```

At the end of `base.py`, add:

```python
#: Order classes whose exit legs are conditional children of another order.
_MULTI_LEG_CLASSES = frozenset({"bracket", "oco", "oto"})
_CLOSING_POSITION_INTENTS = frozenset({"buy_to_close", "sell_to_close"})


def _enum_text(value) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def is_bracket_child_order(order) -> bool:
    """True for a conditional exit leg of a multi-leg equity order.

    Alpaca reports the take-profit and stop-loss legs of a bracket as separate
    orders sharing ``order_class == "bracket"``; the stop waits in ``held``.
    IntelliStock submits only BUY-entry brackets (the gate refuses equity sells
    that are not reduce-only), so inside a multi-leg class every SELL, and
    every ``held`` order, is a child exit. A ``held`` order OUTSIDE a multi-leg
    class, which Alpaca does not produce, is NOT ignored: guards stay closed
    on what they cannot explain.
    """
    if _enum_text(getattr(order, "order_class", None)) not in _MULTI_LEG_CLASSES:
        return False
    return (
        _enum_text(getattr(order, "status", None)) == "held"
        or _enum_text(getattr(order, "side", None)) == "sell"
    )


def is_risk_reducing_order(order) -> bool:
    """True for an order that only ever REDUCES exposure: a bracket leg, or an
    option buy/sell-to-close. Kill-level and halt cancellation leave these."""
    return (
        is_bracket_child_order(order)
        or _enum_text(getattr(order, "position_intent", None))
        in _CLOSING_POSITION_INTENTS
    )
```

In `backend/broker_adapters/errors.py`, add after `FractionalNotAllowed`:

```python
class OptionsNotPermitted(BrokerError):
    """The account may not trade this option order (options level, contract
    eligibility). Raised only for us_option orders; never a fractional-share
    problem, so it must not reach the whole-share retry."""

    broker_definitive_rejection = True
```

and replace `NON_RETRYABLE` with:

```python
NON_RETRYABLE = (
    AssetHalted,
    PDTRestricted,
    WashSale,
    AssetNotTradable,
    FractionalNotAllowed,
    OptionsNotPermitted,
)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_adapter_contract.py backend/tests/test_broker_adapter_base.py backend/tests/test_binanceus_adapter.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()`. Expected changes: only `base.py` DTOs, `BrokerAdapter`, `is_bracket_child_order`, `is_risk_reducing_order`, `_enum_text`, `OptionsNotPermitted` and `NON_RETRYABLE`.

```bash
git add backend/broker_adapters/base.py backend/broker_adapters/errors.py backend/tests/test_swing_adapter_contract.py
git commit -F - <<'EOF'
feat(adapters): option and bracket DTOs on the broker adapter contract

New frozen DTOs for option contracts, snapshots, positions and
activities; non-abstract adapter methods that raise NotImplementedError
so Binance.US is unaffected; OrderRef, AccountDTO and PositionDTO gain
defaulted fields; pure helpers that recognise bracket child legs and
risk-reducing orders; OptionsNotPermitted as a definitive, non-retryable
rejection.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---
### Task 3: AlpacaAdapter brackets: submit, legs, confirmed cancel, closed orders, multi-leg tolerance

**Files:**
- Modify: `backend/broker_adapters/alpaca.py`: `submit_order` (849-1233); `_to_orderref` (1299-1323); `_walk_orders` (1325-1386); `capture_reconciliation_snapshot` order loop (1466-1513); `_normalized_trade_update` (2110-2190). Add module helpers before `_as_positive_float` (3019).
- Create: `backend/tests/swing_alpaca_fakes.py`
- Test: `backend/tests/test_swing_alpaca_bracket.py` (create)

**Interfaces:**
- Consumes: `OrderRef` fields from Task 2.
- Produces:
  - `AlpacaAdapter.submit_order(symbol, side, qty, notional, order_type, limit_price, tif, extended_hours, client_order_id, *, order_class=None, take_profit=None, stop_loss=None, position_intent=None, asset_class=None) -> OrderRef`. Task 3 implements `order_class="bracket"`; Task 5 implements `asset_class="us_option"`.
  - `get_order_with_legs(order_id) -> OrderRef` (with `.legs`).
  - `cancel_orders_confirmed(order_ids, timeout_s=10.0, *, poll_interval_s=0.5, sleep=time.sleep, clock=time.monotonic) -> bool`.
  - `list_closed_orders(symbols, after) -> list[OrderRef]`.
  - `_walk_orders(..., symbols=None)`.
  - Module helpers `_enum_value(value) -> Optional[str]` and `_side_from_position_intent(value) -> Optional[str]`.
  - Test module `swing_alpaca_fakes` with `FakeTradingClient`, `make_adapter`, `order_row`, `enum`, `account`, `request_json` and `T0`.

- [ ] **Step 0: Impact analysis**

```bash
grep -n "submit_order\|_to_orderref\|_walk_orders\|capture_reconciliation_snapshot\|_normalized_trade_update" backend/broker_adapters/alpaca.py backend/broker.py backend/live_orders/*.py
```

Run `mcp__gitnexus__impact` upstream on `submit_order`, `_to_orderref`, `_walk_orders`, `capture_reconciliation_snapshot` and `_normalized_trade_update` (file hint `backend/broker_adapters/alpaca.py`). Callers:
- `broker.py:11215` injects `submit_order` as EB's order transport.
- `alpaca.py:2668, 2724` (legacy `buy()` / `sell()`).
- `_to_orderref` is used by every order read.
- `broker.py:9438` reads `capture_reconciliation_snapshot`.

**Risk: HIGH.** This is the call that places EB's real-money orders. Tell the user before editing.

- [ ] **Step 1: Write the failing test and the shared fakes**

Create `backend/tests/swing_alpaca_fakes.py`:

```python
"""Network-free alpaca-py doubles for the swing-port adapter tests.

Nothing here opens a socket: the adapter is built with ``_test_client`` and
its market-data clients are replaced before any call reaches them.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from broker_adapters._wal import InMemoryStore, LiveOrderWAL
from broker_adapters.alpaca import AlpacaAdapter

#: Monday 2026-10-05 09:15 ET.
T0 = datetime(2026, 10, 5, 13, 15, tzinfo=timezone.utc)


def enum(value):
    return SimpleNamespace(value=value)


def account(**changes):
    values = {
        "cash": "10000", "buying_power": "10000",
        "daytrading_buying_power": "10000", "equity": "10000",
        "last_equity": "10000", "pattern_day_trader": False,
        "daytrade_count": 0, "account_blocked": False,
        "trading_blocked": False, "non_marginable_buying_power": "9000",
        "options_buying_power": "8000", "options_approved_level": 1,
        "options_trading_level": 1,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def order_row(**changes):
    values = {
        "id": "broker-1", "client_order_id": "cid-1", "symbol": "AAPL",
        "side": enum("buy"), "qty": "5", "status": enum("accepted"),
        "filled_qty": "0", "filled_avg_price": None, "filled_fees": "0",
        "order_class": enum("simple"), "type": enum("market"),
        "limit_price": None, "stop_price": None, "position_intent": None,
        "asset_class": enum("us_equity"), "legs": None,
        "submitted_at": T0, "updated_at": T0,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def request_json(request) -> str:
    """One alpaca-py request as sorted JSON with enums reduced to values."""
    return json.dumps(request.to_request_fields(), sort_keys=True,
                      default=lambda value: getattr(value, "value", str(value)))


class FakeTradingClient:
    """Records every call; answers from the rows it was given."""

    def __init__(self, *, account_row=None, positions=(), orders=(),
                 contracts_by_symbol=None, submit_error=None):
        self._session = None
        self._account = account_row or account()
        self.positions = list(positions)
        self.orders = {str(row.id): row for row in orders}
        self.contracts_by_symbol = dict(contracts_by_symbol or {})
        self.contract_lookups = []
        self.submit_error = submit_error
        self.submitted = []
        self.cancelled = []
        self.order_requests = []
        self.by_id_requests = []
        #: broker id -> [(status, filled_qty), ...] answered in turn by
        #: get_order_by_id; the last entry repeats.
        self.status_script = {}

    def get_account(self):
        return self._account

    def get_all_positions(self):
        return list(self.positions)

    def get_order_by_client_id(self, client_order_id):
        raise Exception("404 not found")

    def submit_order(self, order_data=None):
        self.submitted.append(order_data)
        if self.submit_error is not None:
            raise self.submit_error
        return order_row(
            id=f"broker-{len(self.submitted)}",
            client_order_id=order_data.client_order_id,
            symbol=order_data.symbol, side=order_data.side,
            qty=order_data.qty, status=enum("accepted"),
            order_class=order_data.order_class or enum("simple"),
            position_intent=order_data.position_intent)

    def get_order_by_id(self, order_id, filter=None):
        self.by_id_requests.append((str(order_id), filter))
        row = self.orders[str(order_id)]
        script = self.status_script.get(str(order_id))
        if script:
            status, filled = script.pop(0) if len(script) > 1 else script[0]
            row = SimpleNamespace(**{**vars(row), "status": enum(status),
                                     "filled_qty": filled})
        return row

    def cancel_order_by_id(self, order_id):
        self.cancelled.append(str(order_id))

    def get_orders(self, filter=None):
        self.order_requests.append(filter)
        return list(self.orders.values())

    def get_option_contract(self, symbol):
        self.contract_lookups.append(symbol)
        if symbol not in self.contracts_by_symbol:
            raise Exception("404 not found")
        return self.contracts_by_symbol[symbol]


def make_adapter(client, *, instance_id="swing-paper", clean_room=False):
    adapter = AlpacaAdapter(
        api_key="k", api_secret="s", paper=True, instance_id=instance_id,
        wal=LiveOrderWAL(InMemoryStore()), initial_value=10000,
        seed_trades_from_broker=False, clean_room_mode=clean_room,
        defer_ownership_reconciliation=clean_room, _test_client=client)
    # Alerts reach the Discord outbox (a DB write); tests never page anyone.
    adapter._alert_submit = adapter._alert_fill = None
    adapter._alert_reject = adapter._alert_retry = None
    return adapter
```

Create `backend/tests/test_swing_alpaca_bracket.py`:

```python
"""swing-port Task 3: Alpaca bracket orders, their legs, confirmed cancels,
and multi-leg rows that must not break reconciliation.

The two EB payloads below were produced by the PRE-change adapter on
2026-09-24. Never edit them to make a test pass."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from broker_adapters.errors import BrokerPreflightBlocked, FractionalNotAllowed
from live_orders import LifecycleState
from swing_alpaca_fakes import (
    FakeTradingClient,
    enum,
    make_adapter,
    order_row,
    request_json,
)

EB_MARKET_BUY = (
    '{"client_order_id": "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0", '
    '"extended_hours": false, "qty": 12.34567891, "side": "buy", '
    '"symbol": "TQQQ", "time_in_force": "day", "type": "market"}')
EB_EXTENDED_LIMIT_SELL = (
    '{"client_order_id": "alpacama-f6fe0e571a021062fc2ad71af1e5194a440b9-0", '
    '"extended_hours": true, "limit_price": 329.42, "qty": 4.0, '
    '"side": "sell", "symbol": "GLD", "time_in_force": "day", '
    '"type": "limit"}')
BRACKET = (
    '{"client_order_id": "swingpap-bracket-0", "extended_hours": false, '
    '"order_class": "bracket", "qty": 5.0, "side": "buy", '
    '"stop_loss": {"stop_price": 188.0}, "symbol": "AAPL", '
    '"take_profit": {"limit_price": 218.0}, "time_in_force": "gtc", '
    '"type": "market"}')


class _ApiError(Exception):
    status_code = 403


def _eb_adapter():
    client = FakeTradingClient()
    return client, make_adapter(client, instance_id="alpaca-main")


def test_eb_requests_are_byte_identical():
    client, adapter = _eb_adapter()
    adapter.submit_order(
        "TQQQ", "buy", 12.34567891, None, "market", None, "day", False,
        "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0")
    adapter.submit_order(
        "GLD", "sell", 4.0, None, "limit", 329.42, "day", True,
        "alpacama-f6fe0e571a021062fc2ad71af1e5194a440b9-0")
    assert [type(r).__name__ for r in client.submitted] == [
        "MarketOrderRequest", "LimitOrderRequest"]
    assert request_json(client.submitted[0]) == EB_MARKET_BUY
    assert request_json(client.submitted[1]) == EB_EXTENDED_LIMIT_SELL


def test_explicit_none_keywords_are_the_eb_request_too():
    client, adapter = _eb_adapter()
    adapter.submit_order(
        "TQQQ", "buy", 12.34567891, None, "market", None, "day", False,
        "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0",
        order_class=None, take_profit=None, stop_loss=None,
        position_intent=None, asset_class=None)
    assert request_json(client.submitted[0]) == EB_MARKET_BUY


def test_a_bracket_is_a_whole_share_gtc_market_bracket():
    client = FakeTradingClient()
    adapter = make_adapter(client)
    ref = adapter.submit_order(
        "AAPL", "buy", 5.0, None, "market", None, "gtc", False,
        "swingpap-bracket-0", order_class="bracket", take_profit=218.0,
        stop_loss=188.0)
    assert request_json(client.submitted[0]) == BRACKET
    assert ref.order_class == "bracket"
    assert adapter._wal.get("swingpap-bracket-0") is not None


@pytest.mark.parametrize("args,kwargs", [
    (("AAPL", "buy", 5.5, None, "market", None, "gtc", False), {}),
    (("AAPL", "buy", None, 1000.0, "market", None, "gtc", False), {}),
    (("AAPL", "buy", 5.0, None, "limit", 200.0, "gtc", False), {}),
    (("AAPL", "buy", 5.0, None, "market", None, "gtc", True), {}),
    (("AAPL", "sell", 5.0, None, "market", None, "gtc", False), {}),
    (("AAPL", "buy", 5.0, None, "market", None, "ioc", False), {}),
    (("AAPL", "buy", 5.0, None, "market", None, "gtc", False),
     {"take_profit": None}),
    (("AAPL", "buy", 5.0, None, "market", None, "gtc", False),
     {"stop_loss": 220.0}),
])
def test_a_malformed_bracket_is_refused_before_anything_is_recorded(args, kwargs):
    client = FakeTradingClient()
    adapter = make_adapter(client)
    legs = {"take_profit": 218.0, "stop_loss": 188.0}
    legs.update(kwargs)
    with pytest.raises(BrokerPreflightBlocked):
        adapter.submit_order(*args, "swingpap-bad-0", order_class="bracket",
                             **legs)
    assert client.submitted == []
    assert adapter._wal.get("swingpap-bad-0") is None


def test_a_bracket_rejection_never_takes_the_fractional_retry():
    client = FakeTradingClient(submit_error=_ApiError(
        'asset "AAPL" is not fractionable 40310000'))
    adapter = make_adapter(client)
    with pytest.raises(FractionalNotAllowed):
        adapter.submit_order(
            "AAPL", "buy", 5.0, None, "market", None, "gtc", False,
            "swingpap-bracket-0", order_class="bracket", take_profit=218.0,
            stop_loss=188.0)
    assert len(client.submitted) == 1


def _bracket_with_legs():
    tp = order_row(id="leg-tp", client_order_id="7d1e-tp", side=enum("sell"),
                   status=enum("new"), order_class=enum("bracket"),
                   type=enum("limit"), limit_price="218")
    sl = order_row(id="leg-sl", client_order_id="7d1e-sl", side=enum("sell"),
                   status=enum("held"), order_class=enum("bracket"),
                   type=enum("stop"), stop_price="188")
    parent = order_row(id="parent-1", client_order_id="swingpap-bracket-0",
                       status=enum("filled"), filled_qty="5",
                       filled_avg_price="200", order_class=enum("bracket"),
                       legs=[tp, sl])
    return parent, tp, sl


def test_get_order_with_legs_reads_nested_and_classifies_each_leg():
    parent, tp, sl = _bracket_with_legs()
    client = FakeTradingClient(orders=[parent])
    adapter = make_adapter(client)
    ref = adapter.get_order_with_legs("parent-1")
    order_id, request = client.by_id_requests[0]
    assert order_id == "parent-1" and request.nested is True
    assert ref.order_class == "bracket" and ref.filled_qty == 5.0
    assert [(leg.client_order_id, leg.order_type, leg.status) for leg in ref.legs] \
        == [("7d1e-tp", "limit", "new"), ("7d1e-sl", "stop", "held")]
    assert ref.legs[0].limit_price == 218.0
    assert ref.legs[1].stop_price == 188.0


def test_an_eb_order_ref_reads_as_a_simple_order_with_no_legs():
    client, adapter = _eb_adapter()
    ref = adapter._to_orderref(order_row())
    assert ref.order_class == "simple" and ref.legs == ()
    assert ref.position_intent is None and ref.order_type == "market"


def _clock(step=0.5):
    now = [0.0]

    def clock():
        value = now[0]
        now[0] += step
        return value

    return clock


def _leg_client():
    parent, tp, sl = _bracket_with_legs()
    return FakeTradingClient(orders=[parent, tp, sl])


def test_cancel_is_confirmed_when_every_leg_reports_canceled():
    client = _leg_client()
    client.status_script = {"leg-tp": [("canceled", "0")],
                            "leg-sl": [("held", "0"), ("canceled", "0")]}
    adapter = make_adapter(client)
    assert adapter.cancel_orders_confirmed(
        ["leg-tp", "leg-sl"], timeout_s=5.0, poll_interval_s=0.0,
        sleep=lambda _s: None, clock=_clock()) is True
    assert client.cancelled == ["leg-tp", "leg-sl"]


def test_an_unconfirmed_cancel_times_out_false():
    client = _leg_client()
    client.status_script = {"leg-sl": [("held", "0")]}
    adapter = make_adapter(client)
    assert adapter.cancel_orders_confirmed(
        ["leg-sl"], timeout_s=1.0, poll_interval_s=0.0,
        sleep=lambda _s: None, clock=_clock()) is False


def test_a_leg_that_fills_during_the_cancel_is_not_confirmed():
    """Review Focus 3: the position changed under the caller, so a sell sized
    off the old position must wait for the next tick's broker truth."""
    client = _leg_client()
    client.status_script = {"leg-tp": [("filled", "5")],
                            "leg-sl": [("canceled", "0")]}
    adapter = make_adapter(client)
    assert adapter.cancel_orders_confirmed(
        ["leg-tp", "leg-sl"], timeout_s=5.0, poll_interval_s=0.0,
        sleep=lambda _s: None, clock=_clock()) is False


def test_nothing_to_cancel_is_confirmed_without_a_call():
    client = _leg_client()
    adapter = make_adapter(client)
    assert adapter.cancel_orders_confirmed([], timeout_s=1.0) is True
    assert client.cancelled == [] and client.by_id_requests == []


def test_list_closed_orders_filters_by_symbol_and_date():
    parent, _tp, _sl = _bracket_with_legs()
    client = FakeTradingClient(orders=[parent])
    adapter = make_adapter(client)
    refs = adapter.list_closed_orders(["aapl"], "2026-09-01")
    request = client.order_requests[-1]
    assert request.symbols == ["AAPL"]
    assert str(getattr(request.status, "value", request.status)) == "closed"
    assert request.after == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert [ref.broker_order_id for ref in refs] == ["parent-1"]


def test_a_multi_leg_order_does_not_make_the_account_unavailable():
    mleg = order_row(id="mleg-1", client_order_id="ui-mleg", symbol=None,
                     side=None, order_class=enum("mleg"), status=enum("filled"))
    sideless = order_row(id="opt-1", client_order_id="ui-opt",
                         symbol="APH261002P00130000", side=None,
                         position_intent=enum("sell_to_open"),
                         order_class=enum("simple"), status=enum("filled"),
                         filled_qty="1", filled_avg_price="1.2")
    eb = order_row(id="eb-1", client_order_id="alpacama-x-0", symbol="TQQQ",
                   status=enum("filled"), filled_qty="5",
                   filled_avg_price="88")
    client = FakeTradingClient(orders=[mleg, sideless, eb])
    adapter = make_adapter(client)
    snap = adapter.capture_reconciliation_snapshot(account_id="acct-1")
    assert snap.broker_available is True
    assert sorted(o.broker_order_id for o in snap.orders) == ["eb-1", "opt-1"]
    assert {o.broker_order_id: o.side.value for o in snap.orders}["opt-1"] == "sell"


def test_stream_events_held_and_pending_cancel_are_acknowledged():
    client, adapter = _eb_adapter()
    adapter._order_event_account_id = "acct-1"
    for kind in ("held", "pending_cancel"):
        event = adapter._normalized_trade_update(SimpleNamespace(
            event=kind, order=order_row(side=enum("sell")), message=""))
        assert event.state is LifecycleState.ACKNOWLEDGED


def test_a_sideless_stream_event_is_dropped_not_raised():
    client, adapter = _eb_adapter()
    adapter._order_event_account_id = "acct-1"
    event = adapter._normalized_trade_update(SimpleNamespace(
        event="fill", order=order_row(side=None, order_class=enum("mleg")),
        message=""))
    assert event is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_alpaca_bracket.py -q -p no:cacheprovider`
Expected: FAIL. The first failure is `TypeError: AlpacaAdapter.submit_order() got an unexpected keyword argument 'order_class'`. The two EB payload tests PASS already, which is correct: they pin today's behaviour.

- [ ] **Step 3: Write the minimal implementation**

In `backend/broker_adapters/alpaca.py`:

(a) Replace the `submit_order` signature (lines 849-860) with:

```python
    def submit_order(
        self,
        symbol: str,
        side: str,
        qty: Optional[float],
        notional: Optional[float],
        order_type: str,
        limit_price: Optional[float],
        tif: str,
        extended_hours: bool,
        client_order_id: str,
        *,
        order_class: Optional[str] = None,
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        position_intent: Optional[str] = None,
        asset_class: Optional[str] = None,
    ) -> OrderRef:
```

(b) Directly after the idempotency lookup block, which ends with

```python
        except BrokerError as lookup_error:
            raise BrokerError(
                f"pre-submit reconciliation unavailable for {client_order_id}"
            ) from lookup_error
```

insert:

```python
        # swing-port: a bracket is admitted only in its one legal shape, and
        # the check runs before the WAL row exists, so a refusal is a definite
        # non-submission. No EB call passes order_class; this is skipped.
        _is_bracket = str(order_class or "").strip().lower() == "bracket"
        if _is_bracket:
            qty = _checked_bracket_order(
                symbol=symbol, side=side, qty=qty, notional=notional,
                order_type=order_type, tif=tif,
                extended_hours=extended_hours, take_profit=take_profit,
                stop_loss=stop_loss)
```

(c) Directly after

```python
        if notional is not None and qty is None:
            kw["notional"] = notional
```

insert:

```python
        if _is_bracket:
            from alpaca.trading.enums import OrderClass
            from alpaca.trading.requests import StopLossRequest, TakeProfitRequest

            kw["order_class"] = OrderClass.BRACKET
            kw["take_profit"] = TakeProfitRequest(
                limit_price=round(float(take_profit), 2))
            kw["stop_loss"] = StopLossRequest(
                stop_price=round(float(stop_loss), 2))
            _alog(
                "BROKER",
                f"Alpaca SUBMIT bracket legs: {symbol} "
                f"take_profit=${float(take_profit):.2f} "
                f"stop_loss=${float(stop_loss):.2f} cid={client_order_id[:24]}",
                "cyan",
            )
```

(d) Replace `_to_orderref` (1299-1323) with:

```python
    def _to_orderref(self, o: Any) -> OrderRef:
        side = str(getattr(o.side, "value", o.side) if getattr(o, "side", None) else "")
        status = str(getattr(o.status, "value", o.status) if getattr(o, "status", None) else "")
        qty = 0.0
        if getattr(o, "qty", None) is not None:
            try:
                qty = float(o.qty)
            except (TypeError, ValueError):
                qty = 0.0
        elif getattr(o, "notional", None) is not None:
            try:
                qty = float(o.notional)
            except (TypeError, ValueError):
                qty = 0.0
        return OrderRef(
            broker_order_id=str(o.id),
            client_order_id=str(o.client_order_id or ""),
            symbol=str(o.symbol),
            side=side,
            qty=qty,
            status=status,
            filled_qty=float(o.filled_qty or 0.0),
            filled_avg_price=float(o.filled_avg_price) if o.filled_avg_price else None,
            submitted_at_utc=getattr(o, "submitted_at", None),
            # swing-port: multi-leg and option fields, read with getattr so
            # older doubles and every EB order simply read as simple orders.
            order_class=_enum_value(getattr(o, "order_class", None)),
            legs=tuple(
                self._to_orderref(leg) for leg in (getattr(o, "legs", None) or ())
            ),
            position_intent=_enum_value(getattr(o, "position_intent", None)),
            asset_class=_enum_value(getattr(o, "asset_class", None)),
            order_type=_enum_value(
                getattr(o, "type", None) or getattr(o, "order_type", None)
            ),
            limit_price=_as_positive_float(getattr(o, "limit_price", None)),
            stop_price=_as_positive_float(getattr(o, "stop_price", None)),
        )

    def get_order_with_legs(self, order_id) -> OrderRef:
        """The order with its child legs, read with ``nested=True`` so a
        bracket's take-profit and stop-loss come back under ``legs``."""
        from alpaca.trading.requests import GetOrderByIdRequest

        raw = self._client.get_order_by_id(
            str(order_id), filter=GetOrderByIdRequest(nested=True)
        )
        return self._to_orderref(raw)

    def _order_status(self, order_id: str) -> tuple[str, float]:
        raw = self._client.get_order_by_id(str(order_id))
        return (
            _enum_value(getattr(raw, "status", None)) or "",
            float(getattr(raw, "filled_qty", 0) or 0),
        )

    def cancel_orders_confirmed(
        self,
        order_ids,
        timeout_s: float = 10.0,
        *,
        poll_interval_s: float = 0.5,
        sleep=time.sleep,
        clock=time.monotonic,
    ) -> bool:
        """Cancel ``order_ids`` and wait until Alpaca confirms every one.

        True only when each order ended cancelled (or was already dead) with
        nothing filled. False when any is still working at the timeout, and
        False when any FILLED, even partly, while we waited: the position
        changed under the caller, and a sell sized off the old position would
        oversell -- on a margin paper account, into a short. The caller defers
        its sell a tick and re-reads broker truth.
        """
        ids = [
            str(value).strip()
            for value in (order_ids or ())
            if str(value or "").strip()
        ]
        if not ids:
            return True
        for order_id in ids:
            try:
                self._client.cancel_order_by_id(order_id)
            except Exception as exc:
                # "not cancelable" is routine for an order that already
                # finished; its status below says which way it finished.
                _alog(
                    "BROKER",
                    f"cancel {order_id} raised {type(exc).__name__}: {exc}; "
                    "confirming by status",
                    "yellow",
                )
        deadline = clock() + max(0.0, float(timeout_s))
        pending = list(ids)
        while True:
            still_working = []
            for order_id in pending:
                try:
                    status, filled = self._order_status(order_id)
                except Exception:
                    still_working.append(order_id)
                    continue
                if filled > 0 or status in ("filled", "partially_filled"):
                    _alog(
                        "BROKER",
                        f"order {order_id} filled ({filled}) while being "
                        "cancelled; the caller must re-read positions",
                        "yellow",
                    )
                    return False
                if status not in _CANCEL_CONFIRMED_STATES:
                    still_working.append(order_id)
            if not still_working:
                return True
            if clock() >= deadline:
                _alog(
                    "BROKER",
                    f"cancel of {still_working} not confirmed within "
                    f"{float(timeout_s):.0f}s",
                    "yellow",
                )
                return False
            pending = still_working
            sleep(poll_interval_s)

    def list_closed_orders(self, symbols, after) -> list[OrderRef]:
        """Every CLOSED order for ``symbols`` submitted after ``after`` (an ISO
        date or a datetime). Raises rather than return a truncated history."""
        from alpaca.trading.enums import QueryOrderStatus

        if isinstance(after, datetime):
            after_dt = after
        else:
            after_dt = datetime.fromisoformat(str(after).replace("Z", "+00:00"))
        if after_dt.tzinfo is None:
            after_dt = after_dt.replace(tzinfo=timezone.utc)
        wanted = sorted(
            {str(s).strip().upper() for s in (symbols or ()) if str(s).strip()}
        )
        rows, complete = self._walk_orders(
            status=QueryOrderStatus.CLOSED, since=after_dt, symbols=wanted or None
        )
        if not complete:
            raise BrokerError(f"closed-order history for {wanted} is truncated")
        return [self._to_orderref(row) for row in rows]
```

(e) In `_walk_orders`, change the signature to

```python
    def _walk_orders(
        self,
        *,
        status,
        since: Optional[datetime] = None,
        page_size: int = 500,
        max_pages: int = 40,
        symbols: Optional[list] = None,
    ) -> tuple[list, bool]:
```

and directly after

```python
            if until is not None:
                kwargs["until"] = until
```

insert:

```python
            if symbols:
                kwargs["symbols"] = list(symbols)
```

(f) In `capture_reconciliation_snapshot`, replace

```python
            for raw in raw_orders:
                side_value = getattr(getattr(raw, "side", None), "value", None)
                if side_value is None:
                    side_value = getattr(raw, "side", "")
```

with:

```python
            for raw in raw_orders:
                # swing-port: a multi-leg (mleg) option order's top level has
                # no side and no symbol. IntelliStock never submits one, and
                # letting it raise here marked the whole account
                # broker-unavailable for the 30-day history window.
                if _enum_value(getattr(raw, "order_class", None)) == "mleg":
                    continue
                side_value = getattr(getattr(raw, "side", None), "value", None)
                if side_value is None:
                    side_value = getattr(raw, "side", "")
                if not side_value:
                    side_value = _side_from_position_intent(
                        getattr(raw, "position_intent", None))
                    if side_value is None:
                        continue
```

(g) In `_normalized_trade_update`, add to the event map, after `"done_for_day": LifecycleState.EXPIRED,`:

```python
            # swing-port: a bracket's stop leg waits in "held"; a leg being
            # cancelled before a strategy sell passes through "pending_cancel".
            # Both are still working orders.
            "held": LifecycleState.ACKNOWLEDGED,
            "pending_cancel": LifecycleState.ACKNOWLEDGED,
```

Also replace

```python
        _raw_side = getattr(order, "side", "") or ""
        side = OrderSide(
```

with:

```python
        _raw_side = getattr(order, "side", "") or ""
        if not _raw_side:
            # swing-port: an mleg top level carries no side. It is not an
            # order this instance placed; dropping it beats raising inside
            # the stream callback.
            _raw_side = _side_from_position_intent(
                getattr(order, "position_intent", None)) or ""
            if not _raw_side:
                return None
        side = OrderSide(
```

(h) Directly before `def _as_positive_float(value):` (line 3019), add:

```python
#: Order states in which a cancel is confirmed and nothing more can fill.
_CANCEL_CONFIRMED_STATES = frozenset(
    {"canceled", "cancelled", "expired", "rejected", "done_for_day"}
)


def _enum_value(value) -> Optional[str]:
    """An alpaca-py enum or a plain string as lower-case text; None if empty."""
    text = str(getattr(value, "value", value) or "").strip().lower()
    return text or None


def _side_from_position_intent(value) -> Optional[str]:
    """buy_*/sell_* position intents imply the side of an order that has none."""
    text = _enum_value(value) or ""
    if text.startswith("buy_"):
        return "buy"
    if text.startswith("sell_"):
        return "sell"
    return None


def _checked_bracket_order(
    *, symbol, side, qty, notional, order_type, tif, extended_hours,
    take_profit, stop_loss,
) -> int:
    """The one bracket shape IntelliStock sends (spec section 6.1): a BUY
    market parent, whole shares, day or GTC, regular hours, both leg prices
    with the stop below the target. Anything else is refused before the WAL
    row exists."""
    def refuse(why: str):
        raise BrokerPreflightBlocked(
            f"{symbol} bracket refused before submission: {why}"
        )

    if str(side).strip().lower() != "buy":
        refuse("only BUY-entry brackets are supported")
    if notional is not None or qty is None:
        refuse("a bracket needs a whole-share qty, not a notional")
    if float(qty) != int(float(qty)) or int(float(qty)) < 1:
        refuse(f"qty={qty} is not a whole number of shares >= 1")
    if str(order_type).strip().lower() != "market":
        refuse("the parent must be a market order")
    if str(tif).strip().lower() not in ("day", "gtc"):
        refuse(f"tif={tif} (bracket legs need day or gtc)")
    if extended_hours:
        refuse("brackets are regular-hours orders")
    if take_profit is None or stop_loss is None:
        refuse("take_profit and stop_loss are both required")
    if not 0 < float(stop_loss) < float(take_profit):
        refuse(f"stop_loss={stop_loss} must be below take_profit={take_profit}")
    return int(float(qty))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_alpaca_bracket.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Run the adapter regression suites**

Run: `python3 -m pytest backend/tests/test_alpaca_submit_guards.py backend/tests/test_alpaca_market_marks.py backend/tests/test_alpaca_strict_consumers.py backend/tests/test_alpaca_secret_boundaries.py backend/tests/test_live_order_service.py backend/tests/test_live_order_recovery.py backend/tests/test_clean_room_adapter_init.py backend/tests/test_live_pending_orders.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 6: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()`. Expected symbols: `submit_order`, `_to_orderref`, `get_order_with_legs`, `_order_status`, `cancel_orders_confirmed`, `list_closed_orders`, `_walk_orders`, `capture_reconciliation_snapshot`, `_normalized_trade_update`, `_enum_value`, `_side_from_position_intent` and `_checked_bracket_order`.

```bash
git add backend/broker_adapters/alpaca.py backend/tests/swing_alpaca_fakes.py backend/tests/test_swing_alpaca_bracket.py
git commit -F - <<'EOF'
feat(alpaca): GTC market bracket orders and their legs

submit_order gains keyword-only bracket arguments; with none of them
set the request is byte-identical (pinned against payloads from the
pre-change adapter). A bracket is refused before the WAL row unless it
is a whole-share BUY market parent with both legs. Adds a nested legs
read, a cancel that waits for Alpaca to confirm and reports a leg that
filled meanwhile, and a closed-order history read. Multi-leg option rows
without a side no longer make the reconciliation snapshot unavailable,
and the stream maps held and pending_cancel to acknowledged.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 4: AlpacaAdapter options and reference data (contracts, snapshots, chain, account options, activities, daily bars, latest trades)

**Files:**
- Modify: `backend/broker_adapters/alpaca.py`: imports (34-41); new methods after `fetch_rest_quote_marks` (ends 2044); module helpers before `_as_positive_float`.
- Modify: `backend/tests/swing_alpaca_fakes.py` (append only)
- Test: `backend/tests/test_swing_alpaca_options_data.py` (create)

**Interfaces:**
- Consumes: the DTOs from Task 2, and `_enum_value` from Task 3.
- Produces these `AlpacaAdapter` methods:
  - `get_option_contracts(underlying, *, option_type=None, expiration_gte=None, expiration_lte=None, strike_gte=None, strike_lte=None, page_limit=1000, max_pages=50) -> list[OptionContractDTO]`
  - `get_option_snapshots(contracts) -> dict[str, OptionSnapshotDTO]`
  - `get_option_chain(underlying, **filters) -> dict[str, OptionSnapshotDTO]`
  - `get_account_options() -> dict`
  - `get_option_activities(types=("OPASN","OPEXP","OPEXC"), after=None, *, page_size=100, max_pages=20) -> list[OptionActivityDTO]`
  - `get_daily_bars(symbols, days) -> dict[str, list[dict]]`
  - `get_latest_trades(symbols) -> dict[str, tuple[float, str]]`
  - `_option_data_client()`
- Also produces the module helpers `_option_contract_dto(raw)`, `_option_snapshot_dto(symbol, raw)` and `_option_activity_dto(kind, row)`.

- [ ] **Step 0: Impact analysis**

These are new methods, but they sit on the class that places EB's orders. Run `mcp__gitnexus__impact` upstream on `AlpacaAdapter` and `_rest_quote_data_client`. Run `grep -n "_rest_quote_client\|_rest_quote_data_client" backend/broker_adapters/alpaca.py backend/broker.py`. Risk: **MEDIUM**. `_rest_quote_data_client` is shared with EB's REST quote rescue; this task only reads it, it does not change it.

- [ ] **Step 1: Write the failing test and extend the fakes**

Append to `backend/tests/swing_alpaca_fakes.py`:

```python
# --- appended by swing-port Task 4 -------------------------------------------

def contract_row(symbol="APH261002P00130000", *, underlying="APH", kind="put",
                 strike=130.0, expiration="2026-10-02", open_interest="120",
                 close_price="1.10"):
    return SimpleNamespace(
        symbol=symbol, underlying_symbol=underlying, type=enum(kind),
        strike_price=strike, expiration_date=date.fromisoformat(expiration),
        open_interest=open_interest, close_price=close_price)


def option_position(symbol="APH261002P00130000", qty="-1", **changes):
    values = {
        "symbol": symbol, "qty": qty, "asset_class": enum("us_option"),
        "side": enum("short"), "avg_entry_price": "1.23",
        "market_value": "-110", "current_price": "1.10",
        "unrealized_pl": "13", "unrealized_plpc": "0.1057",
    }
    values.update(changes)
    return SimpleNamespace(**values)


def option_snapshot_row(*, bid=1.0, ask=1.2, last=1.1, iv=0.31, delta=-0.24,
                        stamp=T0):
    return SimpleNamespace(
        latest_quote=SimpleNamespace(bid_price=bid, ask_price=ask,
                                     timestamp=stamp),
        latest_trade=SimpleNamespace(price=last, timestamp=stamp),
        implied_volatility=iv,
        greeks=SimpleNamespace(delta=delta, gamma=0.05, rho=0.01,
                               theta=-0.04, vega=0.1))


def bar_row(day, close):
    return SimpleNamespace(
        timestamp=datetime(2026, 9, day, 4, tzinfo=timezone.utc),
        open=close - 1, high=close + 1, low=close - 2, close=close,
        volume=1000.0)


def _wanted(request):
    wanted = request.symbol_or_symbols
    return [wanted] if isinstance(wanted, str) else list(wanted)


class FakeOptionsTradingClient(FakeTradingClient):
    """Adds the options-contract and account-activities endpoints."""

    def __init__(self, *, contract_pages=(), activities=None, **kwargs):
        super().__init__(**kwargs)
        self.contract_pages = list(contract_pages)
        self.contract_requests = []
        self.activities = {k: list(v) for k, v in (activities or {}).items()}
        self.get_calls = []

    def get_option_contracts(self, request):
        self.contract_requests.append(request)
        contracts, token = self.contract_pages.pop(0)
        return SimpleNamespace(option_contracts=list(contracts),
                               next_page_token=token)

    def get(self, path, data=None):
        params = dict(data or {})
        self.get_calls.append((path, params))
        rows = self.activities.get(path.rsplit("/", 1)[-1], [])
        start = 0
        if params.get("page_token"):
            start = next(i for i, row in enumerate(rows)
                         if row["id"] == params["page_token"]) + 1
        return rows[start:start + int(params.get("page_size", 100))]


class FakeOptionDataClient:
    def __init__(self, snapshots):
        self.snapshots = dict(snapshots)
        self.requests = []

    def get_option_snapshot(self, request):
        self.requests.append(request)
        return {s: self.snapshots[s] for s in _wanted(request)
                if s in self.snapshots}


class FakeStockDataClient:
    def __init__(self, bars=None, trades=None):
        self.bars = dict(bars or {})
        self.trades = dict(trades or {})
        self.bar_requests = []
        self.trade_requests = []

    def get_stock_bars(self, request):
        self.bar_requests.append(request)
        return SimpleNamespace(data={s: list(self.bars[s])
                                     for s in _wanted(request)
                                     if s in self.bars})

    def get_stock_latest_trade(self, request):
        self.trade_requests.append(request)
        return {s: self.trades[s] for s in _wanted(request)
                if s in self.trades}
```

Create `backend/tests/test_swing_alpaca_options_data.py`:

```python
"""swing-port Task 4: options and reference data through alpaca-py 0.43.5.
Spec section 9 fix 9: the option chain is read in full, all pages."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from broker_adapters.base import OptionContractDTO
from broker_adapters.errors import BrokerError
from swing_alpaca_fakes import (
    T0,
    FakeOptionDataClient,
    FakeOptionsTradingClient,
    FakeStockDataClient,
    account,
    bar_row,
    contract_row,
    make_adapter,
    option_snapshot_row,
)

OCC = "APH261002P00130000"


def _adapter(**kwargs):
    client = FakeOptionsTradingClient(**kwargs)
    return client, make_adapter(client)


def test_option_contracts_read_every_page_with_string_strikes():
    c1 = contract_row("APH261002P00125000", strike=125.0)
    c2 = contract_row(OCC, strike=130.0)
    c3 = contract_row("APH261002P00135000", strike=135.0, open_interest=None)
    client, adapter = _adapter(contract_pages=[([c1, c2], "t1"), ([c3], None)])
    out = adapter.get_option_contracts(
        "aph", option_type="put", expiration_gte="2026-10-01",
        expiration_lte="2026-10-09", strike_gte=120, strike_lte=140.5)
    assert [c.symbol for c in out] == [
        "APH261002P00125000", OCC, "APH261002P00135000"]
    assert out[1] == OptionContractDTO(OCC, "APH", "put", 130.0,
                                       "2026-10-02", 120, 1.1)
    assert out[2].open_interest is None
    first, second = client.contract_requests
    assert first.underlying_symbols == ["APH"]
    assert first.strike_price_gte == "120.00"
    assert first.strike_price_lte == "140.50"
    assert first.type.value == "put"
    assert first.limit == 1000
    assert first.page_token is None and second.page_token == "t1"


def test_a_repeated_page_token_fails_closed():
    c1 = contract_row()
    client, adapter = _adapter(contract_pages=[([c1], "t1"), ([c1], "t1")])
    with pytest.raises(BrokerError, match="repeated"):
        adapter.get_option_contracts("APH")


def test_the_page_cap_refuses_a_partial_chain():
    c1 = contract_row()
    client, adapter = _adapter(contract_pages=[([c1], "t1"), ([c1], "t2")])
    with pytest.raises(BrokerError, match="exceeded"):
        adapter.get_option_contracts("APH", max_pages=2)


def test_option_snapshots_use_the_indicative_feed_in_batches_of_100():
    symbols = [f"APH261002P{str(i).zfill(8)}" for i in range(150)]
    data = FakeOptionDataClient({s: option_snapshot_row() for s in symbols})
    client, adapter = _adapter()
    adapter._option_rest_client = data
    out = adapter.get_option_snapshots(symbols + [symbols[0].lower()])
    assert len(data.requests) == 2
    assert all(r.feed.value == "indicative" for r in data.requests)
    assert max(len(r.symbol_or_symbols) for r in data.requests) == 100
    snap = out[symbols[0]]
    assert (snap.bid, snap.ask, snap.last, snap.iv, snap.delta) == (
        1.0, 1.2, 1.1, 0.31, -0.24)
    assert snap.quote_ts == T0.isoformat()


def test_the_option_chain_joins_every_contract_to_its_snapshot():
    client, adapter = _adapter(contract_pages=[([contract_row()], None)])
    adapter._option_rest_client = FakeOptionDataClient(
        {OCC: option_snapshot_row(bid=0.9, ask=1.1)})
    chain = adapter.get_option_chain("APH", option_type="put")
    assert list(chain) == [OCC] and chain[OCC].bid == 0.9


def test_account_options_are_read_from_trade_account_fields():
    client, adapter = _adapter(account_row=account(
        options_trading_level=1, options_approved_level=2,
        options_buying_power="7500.5", non_marginable_buying_power="7000",
        cash="8000", equity="10000"))
    assert adapter.get_account_options() == {
        "options_trading_level": 1, "options_approved_level": 2,
        "options_buying_power": 7500.5, "non_marginable_buying_power": 7000.0,
        "cash": 8000.0, "equity": 10000.0}


def test_missing_options_fields_read_as_none():
    client, adapter = _adapter(account_row=SimpleNamespace(
        cash="1", buying_power="1", daytrading_buying_power="1", equity="1",
        last_equity="1", pattern_day_trader=False, daytrade_count=0,
        account_blocked=False, trading_blocked=False))
    options = adapter.get_account_options()
    assert options["options_trading_level"] is None
    assert options["options_buying_power"] is None


def test_option_activities_page_through_each_type_and_sort():
    opasn = [{"id": f"a{i}", "activity_type": "OPASN", "symbol": OCC,
              "qty": "-1", "date": "2026-10-02"} for i in range(3)]
    opexp = [{"id": "e1", "activity_type": "OPEXP", "symbol": OCC,
              "qty": "-1", "date": "2026-10-01", "price": "0"}]
    client, adapter = _adapter(activities={"OPASN": opasn, "OPEXP": opexp})
    out = adapter.get_option_activities(after="2026-09-25", page_size=2)
    assert [a.id for a in out] == ["e1", "a0", "a1", "a2"]
    paths = [path for path, _ in client.get_calls]
    assert paths == ["/account/activities/OPASN", "/account/activities/OPASN",
                     "/account/activities/OPEXP", "/account/activities/OPEXC"]
    assert client.get_calls[1][1]["page_token"] == "a1"
    assert all(params["after"] == "2026-09-25" for _, params in client.get_calls)
    assert out[1].qty == -1.0 and out[0].price == 0.0


def test_an_unknown_activity_type_is_refused():
    client, adapter = _adapter()
    with pytest.raises(ValueError):
        adapter.get_option_activities(types=("FILL",))


def test_daily_bars_batch_sip_all_adjusted_oldest_first():
    symbols = [f"S{i:03d}" for i in range(150)]
    stock = FakeStockDataClient(bars={"S000": [bar_row(3, 101.0),
                                               bar_row(2, 100.0)]})
    client, adapter = _adapter()
    adapter._rest_quote_client = stock
    before = datetime.now(timezone.utc)
    bars = adapter.get_daily_bars(symbols, 400)
    assert len(stock.bar_requests) == 2
    request = stock.bar_requests[0]
    assert len(request.symbol_or_symbols) == 100
    assert request.timeframe.unit.value == "Day"
    assert request.adjustment.value == "all" and request.feed.value == "sip"
    # alpaca-py stores request datetimes as naive UTC.
    end = request.end.replace(tzinfo=request.end.tzinfo or timezone.utc)
    start = request.start.replace(tzinfo=request.start.tzinfo or timezone.utc)
    assert end <= before - timedelta(minutes=15)
    assert start <= before - timedelta(days=399)
    assert [b["c"] for b in bars["S000"]] == [100.0, 101.0]
    assert set(bars["S000"][0]) == {"t", "o", "h", "l", "c", "v"}
    assert bars["S149"] == []


def test_latest_trades_read_the_iex_feed():
    stock = FakeStockDataClient(trades={"AAPL": SimpleNamespace(price=201.5,
                                                                timestamp=T0)})
    client, adapter = _adapter()
    adapter._rest_quote_client = stock
    assert adapter.get_latest_trades(["aapl", "MSFT"]) == {
        "AAPL": (201.5, T0.isoformat())}
    assert stock.trade_requests[0].feed.value == "iex"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_alpaca_options_data.py -q -p no:cacheprovider`
Expected: FAIL. The first failure is `NotImplementedError: AlpacaAdapter does not support get_option_contracts` (inherited from the base class in Task 2).

- [ ] **Step 3: Write the minimal implementation**

In `backend/broker_adapters/alpaca.py`, extend the base import:

```python
from broker_adapters.base import (
    BrokerAdapter,
    OrderRef,
    PositionDTO,
    CashDTO,
    AccountDTO,
    HealthStatus,
    OptionActivityDTO,
    OptionContractDTO,
    OptionPositionDTO,
    OptionSnapshotDTO,
)
```

Directly after `fetch_rest_quote_marks` (it ends with `return tuple(marked)`), add these methods:

```python
    # --- swing-port: options and reference data -------------------------------

    def _option_data_client(self):
        """Lazily built options market-data client (cached on the adapter)."""
        client = getattr(self, "_option_rest_client", None)
        if client is not None:
            return client
        from alpaca.data.historical import OptionHistoricalDataClient

        client = OptionHistoricalDataClient(self._api_key, self._api_secret)
        self._option_rest_client = client
        return client

    def get_option_contracts(
        self,
        underlying,
        *,
        option_type=None,
        expiration_gte=None,
        expiration_lte=None,
        strike_gte=None,
        strike_lte=None,
        page_limit: int = 1000,
        max_pages: int = 50,
    ) -> list[OptionContractDTO]:
        """Every contract matching the filters, all pages (spec section 9
        fix 9). alpaca-py does NOT follow ``next_page_token`` itself; this
        loops until it is empty and refuses a partial chain rather than
        returning one."""
        from alpaca.trading.enums import ContractType
        from alpaca.trading.requests import GetOptionContractsRequest

        base = {
            "underlying_symbols": [str(underlying).strip().upper()],
            "limit": int(page_limit),
        }
        if option_type:
            base["type"] = ContractType(str(option_type).strip().lower())
        if expiration_gte:
            base["expiration_date_gte"] = str(expiration_gte)
        if expiration_lte:
            base["expiration_date_lte"] = str(expiration_lte)
        # alpaca-py types the strike bounds as strings.
        if strike_gte is not None:
            base["strike_price_gte"] = f"{float(strike_gte):.2f}"
        if strike_lte is not None:
            base["strike_price_lte"] = f"{float(strike_lte):.2f}"
        out: list[OptionContractDTO] = []
        seen_tokens: set[str] = set()
        token = None
        for _page in range(max(1, int(max_pages))):
            request = GetOptionContractsRequest(
                **base, **({"page_token": token} if token else {})
            )
            response = self._client.get_option_contracts(request)
            for raw in getattr(response, "option_contracts", None) or ():
                out.append(_option_contract_dto(raw))
            token = getattr(response, "next_page_token", None)
            if not token:
                return out
            if token in seen_tokens:
                raise BrokerError(
                    f"option contract pagination repeated page token {token!r} "
                    f"for {underlying}"
                )
            seen_tokens.add(token)
        raise BrokerError(
            f"option chain for {underlying} exceeded {max_pages} pages; "
            "refusing a partial chain"
        )

    def get_option_snapshots(self, contracts) -> dict[str, OptionSnapshotDTO]:
        """Latest quote, trade, IV and greeks per contract (indicative feed;
        the OPRA agreement is unsigned), requested 100 symbols at a time."""
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.requests import OptionSnapshotRequest

        wanted = sorted(
            {str(c).strip().upper() for c in (contracts or ()) if str(c).strip()}
        )
        out: dict[str, OptionSnapshotDTO] = {}
        if not wanted:
            return out
        client = self._option_data_client()
        for start in range(0, len(wanted), 100):
            batch = wanted[start:start + 100]
            raw = client.get_option_snapshot(
                OptionSnapshotRequest(
                    symbol_or_symbols=batch, feed=OptionsFeed.INDICATIVE
                )
            ) or {}
            for symbol in batch:
                snap = raw.get(symbol)
                if snap is not None:
                    out[symbol] = _option_snapshot_dto(symbol, snap)
        return out

    def get_option_chain(
        self,
        underlying,
        *,
        option_type=None,
        expiration_gte=None,
        expiration_lte=None,
        strike_gte=None,
        strike_lte=None,
    ) -> dict[str, OptionSnapshotDTO]:
        """Every matching contract (all pages) joined to its snapshot."""
        contracts = self.get_option_contracts(
            underlying,
            option_type=option_type,
            expiration_gte=expiration_gte,
            expiration_lte=expiration_lte,
            strike_gte=strike_gte,
            strike_lte=strike_lte,
        )
        return self.get_option_snapshots([c.symbol for c in contracts])

    def get_account_options(self) -> dict:
        """Options approval and buying power from the trade account."""
        acct = self._client.get_account()

        def number(name, cast):
            value = getattr(acct, name, None)
            if value in (None, ""):
                return None
            try:
                return cast(value)
            except (TypeError, ValueError):
                return None

        return {
            "options_trading_level": number("options_trading_level", int),
            "options_approved_level": number("options_approved_level", int),
            "options_buying_power": number("options_buying_power", float),
            "non_marginable_buying_power": number(
                "non_marginable_buying_power", float
            ),
            "cash": number("cash", float),
            "equity": number("equity", float),
        }

    def get_option_activities(
        self,
        types=("OPASN", "OPEXP", "OPEXC"),
        after=None,
        *,
        page_size: int = 100,
        max_pages: int = 20,
    ) -> list[OptionActivityDTO]:
        """Assignment, expiry and exercise activities since ``after``.

        TradingClient has no activities method in alpaca-py 0.43.5; its
        RESTClient.get builds ``base + /v2 + path`` with the account's own
        credentials, which is exactly GET /v2/account/activities/{type}.
        Pages by ``page_token`` (the last row's id) and refuses to return a
        truncated history.
        """
        out: list[OptionActivityDTO] = []
        for kind in types:
            kind = str(kind).strip().upper()
            if kind not in ("OPASN", "OPEXP", "OPEXC"):
                raise ValueError(f"unsupported option activity type {kind!r}")
            token = None
            for _page in range(max(1, int(max_pages))):
                params = {"direction": "asc", "page_size": int(page_size)}
                if after:
                    params["after"] = str(after)
                if token:
                    params["page_token"] = token
                raw = self._client.get(f"/account/activities/{kind}", params)
                rows = raw if isinstance(raw, list) else []
                for row in rows:
                    out.append(_option_activity_dto(kind, row))
                if len(rows) < int(page_size):
                    break
                token = str((rows[-1] or {}).get("id") or "")
                if not token:
                    break
            else:
                raise BrokerError(f"{kind} activities exceeded {max_pages} pages")
        out.sort(key=_activity_sort_key)
        return out

    def get_daily_bars(self, symbols, days) -> dict[str, list[dict]]:
        """Split- and dividend-adjusted SIP daily bars, oldest first, for a
        ``days`` CALENDAR-day lookback, 100 symbols per request. The window
        ends 16 minutes ago: SIP data newer than 15 minutes needs a paid
        subscription and the free tier answers everything older."""
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        wanted = sorted(
            {str(s).strip().upper() for s in (symbols or ()) if str(s).strip()}
        )
        out: dict[str, list[dict]] = {symbol: [] for symbol in wanted}
        if not wanted:
            return out
        client = self._rest_quote_data_client()
        if client is None:
            raise BrokerError("stock market-data client unavailable")
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=max(1, int(days)))
        end = now - timedelta(minutes=16)
        for index in range(0, len(wanted), 100):
            batch = wanted[index:index + 100]
            barset = client.get_stock_bars(
                StockBarsRequest(
                    symbol_or_symbols=batch,
                    timeframe=TimeFrame.Day,
                    start=start,
                    end=end,
                    adjustment=Adjustment.ALL,
                    feed=DataFeed.SIP,
                )
            )
            data = getattr(barset, "data", None) or {}
            for symbol in batch:
                rows = sorted(data.get(symbol) or (), key=_bar_time)
                out[symbol] = [
                    {
                        "t": bar.timestamp.isoformat(),
                        "o": float(bar.open),
                        "h": float(bar.high),
                        "l": float(bar.low),
                        "c": float(bar.close),
                        "v": float(bar.volume),
                    }
                    for bar in rows
                ]
        return out

    def get_latest_trades(self, symbols) -> dict[str, tuple[float, str]]:
        """IEX latest trade per symbol as (price, ISO timestamp)."""
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockLatestTradeRequest

        wanted = sorted(
            {str(s).strip().upper() for s in (symbols or ()) if str(s).strip()}
        )
        out: dict[str, tuple[float, str]] = {}
        if not wanted:
            return out
        client = self._rest_quote_data_client()
        if client is None:
            raise BrokerError("stock market-data client unavailable")
        for index in range(0, len(wanted), 100):
            batch = wanted[index:index + 100]
            raw = client.get_stock_latest_trade(
                StockLatestTradeRequest(symbol_or_symbols=batch, feed=DataFeed.IEX)
            ) or {}
            for symbol in batch:
                trade = raw.get(symbol)
                price = _as_positive_float(getattr(trade, "price", None))
                stamp = getattr(trade, "timestamp", None)
                if price is not None and hasattr(stamp, "isoformat"):
                    out[symbol] = (price, stamp.isoformat())
        return out
```

Directly before `def _as_positive_float(value):`, add:

```python
def _option_contract_dto(raw) -> OptionContractDTO:
    """One alpaca-py OptionContract as the adapter DTO (spec section 9 fix
    10: contract fields come from Alpaca, never from parsing the OCC symbol)."""
    expiration = getattr(raw, "expiration_date", None)
    open_interest = getattr(raw, "open_interest", None)
    close_price = getattr(raw, "close_price", None)
    return OptionContractDTO(
        symbol=str(raw.symbol).strip().upper(),
        underlying=str(getattr(raw, "underlying_symbol", "") or "").strip().upper(),
        option_type=_enum_value(getattr(raw, "type", None)) or "",
        strike=float(raw.strike_price),
        expiration=(
            expiration.isoformat()
            if hasattr(expiration, "isoformat")
            else str(expiration or "")
        ),
        open_interest=(
            int(float(open_interest)) if open_interest not in (None, "") else None
        ),
        close_price=float(close_price) if close_price not in (None, "") else None,
    )


def _option_snapshot_dto(symbol: str, raw) -> OptionSnapshotDTO:
    quote = getattr(raw, "latest_quote", None)
    trade = getattr(raw, "latest_trade", None)
    greeks = getattr(raw, "greeks", None)
    stamp = getattr(quote, "timestamp", None) or getattr(trade, "timestamp", None)

    def greek(name):
        value = getattr(greeks, name, None) if greeks is not None else None
        return float(value) if value is not None else None

    iv = getattr(raw, "implied_volatility", None)
    return OptionSnapshotDTO(
        symbol=symbol,
        bid=_as_positive_float(getattr(quote, "bid_price", None)),
        ask=_as_positive_float(getattr(quote, "ask_price", None)),
        last=_as_positive_float(getattr(trade, "price", None)),
        iv=float(iv) if iv is not None else None,
        delta=greek("delta"),
        gamma=greek("gamma"),
        theta=greek("theta"),
        vega=greek("vega"),
        quote_ts=stamp.isoformat() if hasattr(stamp, "isoformat") else None,
    )


def _option_activity_dto(kind: str, row) -> OptionActivityDTO:
    row = dict(row or {})
    price = row.get("price", row.get("per_share_amount"))
    return OptionActivityDTO(
        id=str(row.get("id") or ""),
        activity_type=str(row.get("activity_type") or kind).strip().upper(),
        symbol=str(row.get("symbol") or "").strip().upper(),
        qty=float(row.get("qty") or 0),
        date=str(row.get("date") or row.get("transaction_time") or ""),
        price=float(price) if price not in (None, "") else None,
    )


def _activity_sort_key(activity: OptionActivityDTO):
    return (activity.date, activity.id)


def _bar_time(bar):
    return bar.timestamp
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_alpaca_options_data.py backend/tests/test_swing_alpaca_bracket.py backend/tests/test_alpaca_market_marks.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()`. Expected: only the new methods, the new module helpers and the import block.

```bash
git add backend/broker_adapters/alpaca.py backend/tests/swing_alpaca_fakes.py backend/tests/test_swing_alpaca_options_data.py
git commit -F - <<'EOF'
feat(alpaca): option contracts, snapshots, activities and daily bars

Option contracts are read across every page and a repeated token or an
over-long chain fails closed (spec section 9 fix 9). Snapshots use the
indicative feed in batches of 100. Account options fields, option
assignment, expiry and exercise activities through the REST client,
batched SIP daily bars with all adjustments, and IEX latest trades.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 5: AlpacaAdapter option orders, option positions and option fills

**Files:**
- Modify: `backend/broker_adapters/alpaca.py`:
  - errors import (49-59)
  - `__init__`: insert before `# Seed from Alpaca` (304)
  - `submit_order`: after the bracket check (Task 3), the preflight (907-914), the kw block, and after `parsed = _parse_error(e)` (1049)
  - `refresh_positions` (1694-1796)
  - `apply_lifecycle_event` (2192-2289)
  - new methods near `get_account_options`
  - module helpers before `_as_positive_float`
- Test: `backend/tests/test_swing_alpaca_option_orders.py` (create)

**Interfaces:**
- Consumes: Task 2 DTOs and `OptionsNotPermitted`; Task 3 `_checked_bracket_order` placement and `_enum_value`; Task 4 `_option_contract_dto`; Task 1 `ConfirmedFill.asset_class`.
- Produces:
  - `submit_order(..., asset_class="us_option", position_intent=...)`
  - `self._option_positions: dict[str, OptionPositionDTO]`, `self._option_positions_complete: bool`
  - `option_contract_meta(symbol) -> Optional[OptionContractDTO]`
  - `list_option_positions() -> list[OptionPositionDTO]`
  - `refresh_positions()` returns option rows as `PositionDTO(asset_class="us_option", ...)`
  - `apply_lifecycle_event` routes option fills to `_option_positions`

- [ ] **Step 0: Impact analysis**

```bash
grep -n "refresh_positions\|apply_lifecycle_event\|_preflight_buy\|_parse_error" backend/broker_adapters/alpaca.py backend/broker.py
```

Run `mcp__gitnexus__impact` upstream on `refresh_positions`, `apply_lifecycle_event`, `submit_order` and `_parse_error`. Callers:
- `broker.py` calls `refresh_positions` from the snapshot worker (every tick) and before submits (15022).
- `apply_lifecycle_event` is EB's `event_handler` (`broker.py:11221`).

**Risk: HIGH.** EB's position mirror and cash are updated here. The EB branch must stay textually the same.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_alpaca_option_orders.py`:

```python
"""swing-port Task 5: option orders, option positions (signed) and option
fills, kept apart from the long-only equity mirror."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from broker_adapters.errors import (
    BrokerPreflightBlocked,
    FractionalNotAllowed,
    OptionsNotPermitted,
)
from live_orders import BrokerOrderEvent, ConfirmedFill, LifecycleState, OrderSide
from swing_alpaca_fakes import (
    T0,
    FakeOptionsTradingClient,
    FakeTradingClient,
    contract_row,
    make_adapter,
    option_position,
    request_json,
)

OCC = "APH261002P00130000"
STO = (
    '{"client_order_id": "swingpap-sto-0", "extended_hours": false, '
    '"limit_price": 1.23, "position_intent": "sell_to_open", "qty": 1.0, '
    '"side": "sell", "symbol": "APH261002P00130000", "time_in_force": "day", '
    '"type": "limit"}')
BTC = (
    '{"client_order_id": "swingpap-btc-0", "extended_hours": false, '
    '"position_intent": "buy_to_close", "qty": 1.0, "side": "buy", '
    '"symbol": "APH261002P00130000", "time_in_force": "day", '
    '"type": "market"}')


class _ApiError(Exception):
    status_code = 403


def _sto(adapter, cid="swingpap-sto-0", **changes):
    args = dict(symbol=OCC, side="sell", qty=1.0, notional=None,
                order_type="limit", limit_price=1.23, tif="day",
                extended_hours=False, client_order_id=cid)
    kwargs = dict(asset_class="us_option", position_intent="sell_to_open")
    for key, value in changes.items():
        (kwargs if key in kwargs else args)[key] = value
    return adapter.submit_order(**args, **kwargs)


def test_sell_to_open_request_shape():
    client = FakeTradingClient()
    adapter = make_adapter(client)
    _sto(adapter)
    assert request_json(client.submitted[0]) == STO


def test_buy_to_close_skips_the_pdt_preflight_buy_to_open_does_not():
    client = FakeTradingClient()
    adapter = make_adapter(client)
    calls = []
    adapter._preflight_buy = lambda **kw: calls.append(kw)
    adapter.submit_order(OCC, "buy", 1, None, "market", None, "day", False,
                         "swingpap-btc-0", asset_class="us_option",
                         position_intent="buy_to_close")
    assert calls == []
    assert request_json(client.submitted[0]) == BTC
    adapter.submit_order(OCC, "buy", 1, None, "limit", 0.5, "day", False,
                         "swingpap-bto-0", asset_class="us_option",
                         position_intent="buy_to_open")
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"qty": 1.5},
    {"qty": None, "notional": 123.0},
    {"extended_hours": True},
    {"position_intent": "buy_to_open"},
    {"position_intent": None},
    {"order_type": "limit", "limit_price": None},
    {"order_type": "stop"},
    {"tif": "ioc"},
])
def test_a_malformed_option_order_is_refused_before_the_wal(changes):
    client = FakeTradingClient()
    adapter = make_adapter(client)
    with pytest.raises(BrokerPreflightBlocked):
        _sto(adapter, cid="swingpap-bad-0", **changes)
    assert client.submitted == []
    assert adapter._wal.get("swingpap-bad-0") is None


def test_an_option_rejection_is_options_not_permitted_not_fractional():
    client = FakeTradingClient(submit_error=_ApiError(
        '{"code":40310000,"message":"account not eligible to trade '
        'uncovered option contracts"}'))
    adapter = make_adapter(client)
    with pytest.raises(OptionsNotPermitted) as info:
        _sto(adapter)
    assert info.value.broker_definitive_rejection is True
    assert len(client.submitted) == 1


def test_an_equity_fractional_rejection_still_takes_the_whole_share_retry():
    client = FakeTradingClient(submit_error=_ApiError(
        'asset "BBGI" is not fractionable 40310000'))
    adapter = make_adapter(client, instance_id="alpaca-main")
    with pytest.raises(FractionalNotAllowed):
        adapter.submit_order("BBGI", "buy", 2.5, None, "market", None, "day",
                             False, "alpacama-bbgi-0")
    assert len(client.submitted) == 2
    assert client.submitted[1].qty == 2.0


def _fill(symbol, side, position_delta, cash_delta, price):
    event = BrokerOrderEvent(
        event_id=f"{symbol}-{side}", account_id="acct-1",
        instance_id="swing-paper", client_order_id=f"cid-{side}",
        broker_order_id=f"b-{side}", symbol=symbol,
        side=OrderSide(side), state=LifecycleState.FILLED,
        cumulative_quantity=Decimal("1"),
        cumulative_average_price=Decimal(price),
        cumulative_fees=Decimal("0"), occurred_at=T0)
    return ConfirmedFill(
        event=event, incremental_quantity=Decimal("1"),
        incremental_price=Decimal(price), incremental_fees=Decimal("0"),
        position_delta=Decimal(position_delta), cash_delta=Decimal(cash_delta),
        asset_class="us_option", contract_multiplier=100)


def test_short_option_sign_survives_refresh_and_fills():
    """Review Focus 4."""
    client = FakeOptionsTradingClient(
        positions=[option_position()],
        contracts_by_symbol={OCC: contract_row()})
    adapter = make_adapter(client, clean_room=True)
    assert OCC not in adapter._positions
    held = adapter._option_positions[OCC]
    assert (held.qty, held.underlying, held.option_type, held.strike,
            held.expiry, held.multiplier) == (-1, "APH", "put", 130.0,
                                              "2026-10-02", 100)
    assert adapter._option_positions_complete is True
    # After a healthy reconcile the clean-room filter still never adopts it.
    adapter._reconciliation_healthy = True
    adapter._external_positions = {}
    rows = adapter.refresh_positions()
    assert OCC not in adapter._positions
    assert adapter._option_positions[OCC].qty == -1
    row = next(r for r in rows if r.symbol == OCC)
    assert (row.asset_class, row.side, row.multiplier, row.unrealized_pl,
            row.underlying, row.strike) == ("us_option", "short", 100, 13.0,
                                            "APH", 130.0)
    assert adapter.list_option_positions() == [adapter._option_positions[OCC]]
    # A buy-to-close fill moves -1 to 0 and removes it; cash moves x100.
    cash = adapter._cash
    fill = _fill(OCC, "buy", "1", "-40", "0.40")
    adapter.apply_lifecycle_event(fill.event, fill)
    assert OCC not in adapter._option_positions
    assert adapter._cash == pytest.approx(cash - 40)
    assert OCC not in adapter._positions
    # A sell-to-open fill re-creates the short from the cached contract.
    fill = _fill(OCC, "sell", "-1", "123", "1.23")
    adapter.apply_lifecycle_event(fill.event, fill)
    assert adapter._option_positions[OCC].qty == -1
    assert adapter._option_positions[OCC].underlying == "APH"
    assert OCC not in adapter._positions
    # Broker truth without the position empties the map.
    client.positions = []
    adapter.refresh_positions()
    assert adapter._option_positions == {}


def test_unknown_contract_fields_keep_the_short_but_mark_collateral_unknown():
    client = FakeOptionsTradingClient(positions=[option_position()])
    adapter = make_adapter(client, clean_room=True)
    held = adapter._option_positions[OCC]
    assert held.qty == -1 and held.strike == 0.0 and held.underlying == ""
    assert adapter._option_positions_complete is False
    lookups = len(client.contract_lookups)
    adapter.refresh_positions()
    assert len(client.contract_lookups) == lookups   # negative-cached 60 s


def test_an_equity_only_account_leaves_the_option_map_empty():
    equity = SimpleNamespace(symbol="TQQQ", qty="10", market_value="880",
                             avg_entry_price="80")
    client = FakeTradingClient(positions=[equity])
    adapter = make_adapter(client, instance_id="alpaca-main")
    assert adapter._option_positions == {}
    assert adapter._option_positions_complete is True
    assert adapter._positions == {"TQQQ": 10.0}
    row = adapter.refresh_positions()[0]
    assert row.asset_class is None and row.multiplier == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_alpaca_option_orders.py -q -p no:cacheprovider`
Expected: FAIL. The first failure is `AssertionError` in `test_sell_to_open_request_shape`: the payload lacks `position_intent`, because `asset_class` is accepted but ignored until this task.

- [ ] **Step 3: Write the minimal implementation**

In `backend/broker_adapters/alpaca.py`:

(a) Add `OptionsNotPermitted,` to the `from broker_adapters.errors import (...)` list.

(b) In `__init__`, directly before `# Seed from Alpaca`, insert:

```python
        # swing-port: option positions (signed; a short put is negative) live
        # apart from the equity mirror, which stays long-only. refresh_positions
        # fills them; EB's account holds none, so both stay empty there.
        self._option_positions: dict[str, OptionPositionDTO] = {}
        self._option_positions_complete = True
        self._option_contract_cache: dict[str, OptionContractDTO] = {}
        self._option_contract_misses: dict[str, float] = {}
        self._last_option_misses: frozenset = frozenset()
```

(c) In `submit_order`, directly after the Task 3 bracket check (`qty = _checked_bracket_order(...)`), insert:

```python
        # swing-port: an option order is admitted only as whole contracts,
        # regular hours, a legal position_intent and a market or limit type.
        _is_option = str(asset_class or "").strip().lower() == "us_option"
        if _is_option:
            qty = _checked_option_order(
                symbol=symbol, side=side, qty=qty, notional=notional,
                order_type=order_type, limit_price=limit_price, tif=tif,
                extended_hours=extended_hours, position_intent=position_intent)
```

Replace:

```python
        if side.lower() == "buy":
            self._preflight_buy(
```

with:

```python
        # swing-port: a buy-to-close REDUCES risk; the PDT rule must never keep
        # the account short a put it is trying to close.
        if side.lower() == "buy" and not (
            _is_option
            and str(position_intent or "").strip().lower() == "buy_to_close"
        ):
            self._preflight_buy(
```

After the Task 3 `if _is_bracket:` kw block, insert:

```python
        if _is_option:
            from alpaca.trading.enums import PositionIntent

            kw["position_intent"] = PositionIntent(
                str(position_intent).strip().lower()
            )
```

Replace the single line `            parsed = _parse_error(e)` (the one directly after the `except BrokerError:` ambiguity block, currently line 1049) with:

```python
            parsed = _parse_error(e)
            if _is_option:
                # swing-port: Alpaca answers an option order the account may
                # not place with 40310000, the code the equity path reads as
                # "not fractionable". Mapped here, before the definitive flag
                # is set, so it never reaches the whole-share retry.
                parsed = _option_order_error(e, parsed)
```

(d) Replace the body of `refresh_positions` from `new_positions: dict[str, float] = {}` down to the end of the `for p in positions:` loop with:

```python
        new_positions: dict[str, float] = {}
        # 2026-04-22 CRITICAL: derive per-share price = market_value / qty so
        # downstream MTM math sees real prices instead of $0 marks. Consumed
        # below under the lock to seed _last_prices for any symbol that
        # doesn't have a fill-priced entry yet.
        new_last_prices: dict[str, float] = {}
        out: list[PositionDTO] = []
        new_option_positions: dict[str, OptionPositionDTO] = {}
        option_misses: list[str] = []
        for p in positions:
            try:
                sym = str(p.symbol)
                qty = float(p.qty)
                mv = float(p.market_value or 0.0)
                new_positions[sym] = qty
                if _is_us_option_position(p):
                    # swing-port: an option row keeps its broker quantity in
                    # new_positions (the clean-room filter below still decides
                    # what the equity mirror adopts, and it never adopts a
                    # short) and is described from Alpaca's contract fields,
                    # not an OCC-symbol parse (spec section 9 fix 10).
                    out.append(
                        self._collect_option_position(
                            p, new_option_positions, option_misses
                        )
                    )
                    continue
                if qty > 0 and mv > 0:
                    new_last_prices[sym] = mv / qty
                out.append(PositionDTO(
                    symbol=sym,
                    qty=qty,
                    avg_entry_price=float(p.avg_entry_price or 0.0),
                    market_value=mv,
                ))
            except (TypeError, ValueError, ZeroDivisionError):
                continue
```

Then, inside the `with self._lock:` block that follows, directly before the comment `# Successful REST refresh — clear the staleness flag set by`, insert:

```python
            self._option_positions = new_option_positions
            self._option_positions_complete = not option_misses
```

After the `with self._lock:` block (directly before `return out`), insert:

```python
        if frozenset(option_misses) != self._last_option_misses:
            self._last_option_misses = frozenset(option_misses)
            if option_misses:
                _alog(
                    "BROKER",
                    f"option contract fields unavailable for "
                    f"{sorted(option_misses)}; sell-to-open collateral is "
                    "unknown (new puts refused) until they resolve",
                    "red",
                )
```

(e) Add these methods after `get_account_options` (Task 4):

```python
    def option_contract_meta(self, symbol) -> Optional[OptionContractDTO]:
        """Alpaca's contract fields for one OCC symbol, cached for the process;
        a failed lookup is not retried for 60 s."""
        sym = str(symbol or "").strip().upper()
        with self._lock:
            cached = self._option_contract_cache.get(sym)
            retry_at = self._option_contract_misses.get(sym, 0.0)
        if cached is not None:
            return cached
        if time.time() < retry_at:
            return None
        try:
            dto = _option_contract_dto(self._client.get_option_contract(sym))
        except Exception as exc:
            with self._lock:
                self._option_contract_misses[sym] = time.time() + 60.0
            _alog(
                "BROKER",
                f"option contract lookup failed for {sym}: "
                f"{type(exc).__name__}: {exc}",
                "yellow",
            )
            return None
        with self._lock:
            self._option_contract_cache[sym] = dto
            self._option_contract_misses.pop(sym, None)
        return dto

    def list_option_positions(self) -> list[OptionPositionDTO]:
        with self._lock:
            return list(self._option_positions.values())

    def _collect_option_position(self, raw, sink, misses) -> PositionDTO:
        """Record one broker option row in ``sink`` and return its display row.
        Unknown contract fields keep the signed quantity (a buy-to-close needs
        only that) but mark the map incomplete, which refuses new puts."""
        symbol = str(raw.symbol).strip().upper()
        qty = int(float(raw.qty))
        meta = self.option_contract_meta(symbol)
        if meta is None:
            misses.append(symbol)
        current = _as_float(getattr(raw, "current_price", None))
        market_value = _as_float(getattr(raw, "market_value", None))
        unrealized = _as_float(getattr(raw, "unrealized_pl", None))
        avg_entry = float(getattr(raw, "avg_entry_price", 0) or 0)
        sink[symbol] = OptionPositionDTO(
            symbol=symbol,
            underlying=meta.underlying if meta else "",
            option_type=meta.option_type if meta else "",
            strike=meta.strike if meta else 0.0,
            expiry=meta.expiration if meta else "",
            qty=qty,
            avg_entry_price=avg_entry,
            current_price=current,
            market_value=market_value,
            unrealized_pl=unrealized,
            multiplier=100,
        )
        return PositionDTO(
            symbol=symbol,
            qty=float(qty),
            avg_entry_price=avg_entry,
            market_value=market_value or 0.0,
            asset_class="us_option",
            side=_enum_value(getattr(raw, "side", None))
            or ("short" if qty < 0 else "long"),
            unrealized_pl=unrealized,
            unrealized_plpc=_as_float(getattr(raw, "unrealized_plpc", None)),
            current_price=current,
            multiplier=100,
            underlying=meta.underlying if meta else None,
            strike=meta.strike if meta else None,
            expiry=meta.expiration if meta else None,
        )

    def _apply_option_fill_locked(self, event, fill) -> None:
        """One confirmed option fill: the signed contract count moves by
        position_delta, cash by the service's x100 cash_delta. Caller holds
        self._lock. The next refresh_positions rebinds from broker truth."""
        symbol = event.symbol
        current = self._option_positions.get(symbol)
        new_qty = (current.qty if current is not None else 0) + int(
            fill.position_delta
        )
        self._cash += float(fill.cash_delta)
        if new_qty == 0:
            self._option_positions.pop(symbol, None)
            return
        if current is not None:
            self._option_positions[symbol] = replace(current, qty=new_qty)
            return
        meta = self._option_contract_cache.get(symbol)
        self._option_positions[symbol] = OptionPositionDTO(
            symbol=symbol,
            underlying=meta.underlying if meta else "",
            option_type=meta.option_type if meta else "",
            strike=meta.strike if meta else 0.0,
            expiry=meta.expiration if meta else "",
            qty=new_qty,
            avg_entry_price=float(fill.incremental_price),
            current_price=None,
            market_value=None,
            unrealized_pl=None,
            multiplier=100,
        )
```

Add `from dataclasses import replace` to the module imports (after `import copy`).

(f) In `apply_lifecycle_event`, replace:

```python
            if fill is not None:
                symbol = event.symbol
```

with:

```python
            # swing-port: an option fill moves the option map and cash only;
            # the long-only equity mirror and _trades never see a contract.
            _option_fill = (
                fill is not None
                and getattr(fill, "asset_class", "us_equity") == "us_option"
            )
            if _option_fill:
                self._apply_option_fill_locked(event, fill)
            if fill is not None and not _option_fill:
                symbol = event.symbol
```

(g) Directly before `def _as_positive_float(value):`, add:

```python
def _as_float(value) -> Optional[float]:
    """Float, or None for anything unusable. Negatives stay (short values)."""
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _is_us_option_position(raw) -> bool:
    return _enum_value(getattr(raw, "asset_class", None)) == "us_option"


def _checked_option_order(
    *, symbol, side, qty, notional, order_type, limit_price, tif,
    extended_hours, position_intent,
) -> int:
    """Whole contracts, regular hours, a position_intent that agrees with the
    side, a market or priced limit order (spec section 6.1)."""
    def refuse(why: str):
        raise BrokerPreflightBlocked(
            f"{symbol} option order refused before submission: {why}"
        )

    intent = str(position_intent or "").strip().lower()
    expected = {
        "buy_to_open": "buy",
        "buy_to_close": "buy",
        "sell_to_open": "sell",
        "sell_to_close": "sell",
    }.get(intent)
    if expected is None:
        refuse(f"position_intent={position_intent!r}")
    if str(side).strip().lower() != expected:
        refuse(f"side={side} contradicts position_intent={intent}")
    if notional is not None or qty is None:
        refuse("options trade whole contracts, not a notional")
    if float(qty) != int(float(qty)) or int(float(qty)) < 1:
        refuse(f"qty={qty} is not a whole number of contracts >= 1")
    if extended_hours:
        refuse("options trade in regular hours only")
    if str(tif).strip().lower() not in ("day", "gtc"):
        refuse(f"tif={tif}")
    kind = str(order_type).strip().lower()
    if kind not in ("market", "limit"):
        refuse(f"order_type={order_type}")
    if kind == "limit" and (limit_price is None or float(limit_price) <= 0):
        refuse("a limit order needs limit_price > 0")
    return int(float(qty))


def _option_order_error(exc, parsed):
    """Alpaca's answer to an option order the account may not place."""
    if isinstance(parsed, (InsufficientBuyingPower, BrokerRateLimited)):
        return parsed
    message = str(exc).lower()
    if (
        isinstance(parsed, FractionalNotAllowed)
        or "40310000" in message
        or "option" in message
    ):
        return OptionsNotPermitted(str(exc))
    return parsed
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_alpaca_option_orders.py backend/tests/test_swing_alpaca_bracket.py backend/tests/test_swing_alpaca_options_data.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Run the adapter regression suites**

Run: `python3 -m pytest backend/tests/test_alpaca_submit_guards.py backend/tests/test_alpaca_market_marks.py backend/tests/test_alpaca_strict_consumers.py backend/tests/test_live_order_service.py backend/tests/test_clean_room_adapter_init.py backend/tests/test_broker_classifier.py backend/tests/test_split_reconcile.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 6: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()`. Expected symbols: `__init__`, `submit_order`, `refresh_positions`, `apply_lifecycle_event`, `option_contract_meta`, `list_option_positions`, `_collect_option_position`, `_apply_option_fill_locked`, `_as_float`, `_is_us_option_position`, `_checked_option_order` and `_option_order_error`.

```bash
git add backend/broker_adapters/alpaca.py backend/tests/test_swing_alpaca_option_orders.py
git commit -F - <<'EOF'
feat(alpaca): option orders, signed option positions and option fills

Option orders carry position_intent and whole contracts, never extended
hours, and skip the PDT preflight on buy-to-close. A 40310000 answer to
an option order maps to OptionsNotPermitted instead of the fractional
retry. refresh_positions fills a separate signed option map from
Alpaca's contract fields, leaving the equity filter untouched, and marks
it incomplete when contract fields are unknown. Option fills move the
option map and cash, never the equity mirror.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---
### Task 6: UnifiedOrderGate options branch

**Files:**
- Modify: `backend/live_orders/gate.py:16-205`. Add the class constant, a one-line dispatch at the top of `evaluate`, and two new methods. The equity body stays as it is.
- Create: `backend/tests/swing_live_fixtures.py`
- Test: `backend/tests/test_swing_option_gate.py` (create)

**Interfaces:**
- Consumes: the Task 1 `OrderIntent` option fields and the `DependencySnapshot` option fields.
- Produces:
  - `UnifiedOrderGate._evaluate_option(intent, snapshot) -> GateDecision`, reached only when `intent.asset_class == "us_option"`.
  - Reason codes: `option.snapshot_asset_class_mismatch`, `option.intent_side_mismatch`, `market.regular_hours_required`, `option.short_position_insufficient`, `option.long_position_insufficient`, `option.already_short_contract`, `option.sell_to_open_requires_put`, `option.collateral_unknown`, `option.collateral_insufficient`, `option.underlying_cap_unknown`, `option.underlying_cap`. It also reuses the existing `cash.insufficient`, `exposure.max_order_notional` and dependency/quote/positions codes.
  - Test fixtures `swing_live_fixtures`: `RTH`, `OCC`, `option_intent`, `buy_to_close`, `option_snapshot`, `bracket_intent`, `equity_snapshot`, `leg_ref`, `parent_ref`.

- [ ] **Step 0: Impact analysis**

```bash
grep -rn "UnifiedOrderGate\|\.evaluate(" backend --include=*.py | grep -v "/tests/"
```

Run `mcp__gitnexus__impact` upstream on `UnifiedOrderGate` and `evaluate` (file `backend/live_orders/gate.py`). The only production caller is `LiveOrderService.submit` (`service.py:399`), which gates every EB order. **Risk: HIGH.** Mitigation: the equity body is not edited; only a first-line dispatch keyed on a field EB never sets is added.

- [ ] **Step 1: Write the failing test and the shared fixtures**

Create `backend/tests/swing_live_fixtures.py`:

```python
"""Intent and snapshot builders for the swing-port option and bracket tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from live_orders import (
    DependencySnapshot,
    Health,
    OrderIntent,
    OrderSide,
    OrderSource,
)

#: Monday 2026-10-05 11:00 ET, a regular session.
RTH = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)
OCC = "APH261009P00130000"

_HEALTH = ("kill_switch", "quote", "cash", "positions", "calendar",
           "persistence", "risk_state", "watchdog")
_STAMPS = ("kill_switch_at", "cash_at", "calendar_at", "persistence_at",
           "risk_state_at", "watchdog_at")


def option_intent(**changes) -> OrderIntent:
    values = dict(
        account_id="acct-1", instance_id="instance-1",
        source=OrderSource.STRATEGY, reason="wheel_sto_put", symbol=OCC,
        side=OrderSide.SELL, quantity=Decimal("1"), reduce_only=False,
        decision_at=RTH, quote_at=RTH, risk_snapshot_id="risk-1",
        order_type="limit", limit_price=Decimal("1.20"), tif="day",
        asset_class="us_option", position_intent="sell_to_open",
        contract_multiplier=100, underlying="APH", option_type="put",
        strike=Decimal("130"), expiry="2026-10-09",
    )
    values.update(changes)
    return OrderIntent(**values)


def buy_to_close(**changes) -> OrderIntent:
    values = dict(side=OrderSide.BUY, reduce_only=True,
                  position_intent="buy_to_close", reason="wheel_btc_itm",
                  source=OrderSource.RISK_EXIT, order_type="market",
                  limit_price=None)
    values.update(changes)
    return option_intent(**values)


def option_snapshot(order, **changes) -> DependencySnapshot:
    values = dict(
        account_id=order.account_id, instance_id=order.instance_id,
        observed_at=RTH, armed=True,
        quote_symbol=order.symbol, quote_price=Decimal("1.20"),
        quote_at=order.quote_at, position_symbol=order.symbol,
        position_quantity=Decimal("0"), positions_at=RTH,
        available_cash=Decimal("20000"), market_open=True,
        risk_snapshot_id=order.risk_snapshot_id,
        max_order_notional=Decimal("10000"), max_position_quantity=None,
        max_quote_age=timedelta(seconds=60),
        asset_class="us_option", regular_session_open=True,
        account_equity=Decimal("60000"),
        open_short_put_collateral=Decimal("0"),
        pending_sell_to_open_collateral=Decimal("0"),
        underlying_put_collateral=Decimal("0"),
    )
    values.update({name: Health.HEALTHY for name in _HEALTH})
    values.update({name: RTH for name in _STAMPS})
    values.update(changes)
    return DependencySnapshot(**values)


def bracket_intent(**changes) -> OrderIntent:
    values = dict(
        account_id="acct-1", instance_id="instance-1",
        source=OrderSource.STRATEGY, reason="swing_entry", symbol="AAPL",
        side=OrderSide.BUY, quantity=Decimal("5"), reduce_only=False,
        decision_at=RTH, quote_at=RTH, risk_snapshot_id="risk-1",
        order_type="market", tif="gtc", reference_price=Decimal("100"),
        order_class="bracket", take_profit_price=Decimal("109"),
        stop_loss_price=Decimal("94"),
    )
    values.update(changes)
    return OrderIntent(**values)


def equity_snapshot(order, **changes) -> DependencySnapshot:
    values = dict(
        account_id=order.account_id, instance_id=order.instance_id,
        observed_at=RTH, armed=True, quote_symbol=order.symbol,
        quote_price=Decimal("100"), quote_at=order.quote_at,
        position_symbol=order.symbol, position_quantity=Decimal("0"),
        positions_at=RTH, available_cash=Decimal("10000"), market_open=True,
        risk_snapshot_id=order.risk_snapshot_id,
        max_order_notional=Decimal("10000"),
        max_position_quantity=Decimal("1000"),
    )
    values.update({name: Health.HEALTHY for name in _HEALTH})
    values.update({name: RTH for name in _STAMPS})
    values.update(changes)
    return DependencySnapshot(**values)


def leg_ref(cid, kind, *, status="held", filled_qty="0",
            filled_avg_price=None, qty="5", symbol="AAPL"):
    """One bracket child leg as the adapter's OrderRef shape."""
    return SimpleNamespace(
        broker_order_id=f"broker-{cid}", id=f"broker-{cid}",
        client_order_id=cid, symbol=symbol, side="sell", qty=qty,
        status=status, filled_qty=filled_qty,
        filled_avg_price=filled_avg_price, order_type=kind,
        order_class="bracket",
        limit_price=109.0 if kind == "limit" else None,
        stop_price=94.0 if kind == "stop" else None)


def parent_ref(order, *, status="accepted", filled_qty="0",
               filled_avg_price=None):
    return SimpleNamespace(
        client_order_id=order.idempotency_key,
        broker_order_id="broker-parent", id="broker-parent",
        symbol=order.symbol, side=order.side.value,
        qty=float(order.quantity), status=status, filled_qty=filled_qty,
        filled_avg_price=filled_avg_price, order_class="bracket")
```

Create `backend/tests/test_swing_option_gate.py`:

```python
"""swing-port Task 6: the options branch of UnifiedOrderGate. It is entered
only for asset_class == "us_option"; EB's equity branch is untouched."""
from datetime import timedelta
from decimal import Decimal

import pytest

from live_orders import Health, OrderSide, UnifiedOrderGate
from live_order_task8_helpers import intent as equity_intent
from live_order_task8_helpers import snapshot as equity_helper_snapshot
from swing_live_fixtures import (
    RTH,
    buy_to_close,
    option_intent,
    option_snapshot,
)


def evaluate(order, snap):
    return UnifiedOrderGate().evaluate(order, snap)


def test_an_equity_intent_never_enters_the_option_branch(monkeypatch):
    def refuse(self, intent, snapshot):
        raise AssertionError("an equity intent entered the options branch")

    monkeypatch.setattr(UnifiedOrderGate, "_evaluate_option", refuse)
    order = equity_intent(side=OrderSide.SELL, reduce_only=False,
                          quantity=Decimal("1"))
    decision = evaluate(order, equity_helper_snapshot(order))
    assert decision.allowed is False
    assert "positions.sell_must_be_reduce_only" in decision.reason_codes


def test_a_cash_secured_put_within_cash_and_cap_is_allowed():
    order = option_intent()
    decision = evaluate(order, option_snapshot(order))
    assert decision.allowed is True, decision.reason_codes
    assert decision.approved_quantity == Decimal("1")


def test_cash_exactly_equal_to_collateral_is_allowed_one_cent_short_is_not():
    """Review Focus 5. 130 x 100 x 1 = 13,000 against 20,000 - 4,000 - 3,000."""
    order = option_intent()
    exact = option_snapshot(
        order, available_cash=Decimal("20000"),
        open_short_put_collateral=Decimal("4000"),
        pending_sell_to_open_collateral=Decimal("3000"))
    assert evaluate(order, exact).allowed is True
    short = option_snapshot(
        order, available_cash=Decimal("19999.99"),
        open_short_put_collateral=Decimal("4000"),
        pending_sell_to_open_collateral=Decimal("3000"))
    decision = evaluate(order, short)
    assert decision.allowed is False
    assert "option.collateral_insufficient" in decision.reason_codes


def test_the_underlying_cap_counts_existing_puts():
    """Spec section 9 fix 3: 25% of equity per underlying, puts included."""
    order = option_intent()
    assert evaluate(order, option_snapshot(
        order, account_equity=Decimal("52000"))).allowed is True
    over = evaluate(order, option_snapshot(
        order, account_equity=Decimal("52000"),
        underlying_put_collateral=Decimal("0.01")))
    assert "option.underlying_cap" in over.reason_codes
    small = evaluate(order, option_snapshot(
        order, account_equity=Decimal("50000")))
    assert "option.underlying_cap" in small.reason_codes


@pytest.mark.parametrize("changes,code", [
    ({"open_short_put_collateral": None}, "option.collateral_unknown"),
    ({"account_equity": None}, "option.underlying_cap_unknown"),
    ({"underlying_put_collateral": None}, "option.underlying_cap_unknown"),
])
def test_unknown_collateral_inputs_refuse_a_new_put(changes, code):
    order = option_intent()
    decision = evaluate(order, option_snapshot(order, **changes))
    assert decision.allowed is False and code in decision.reason_codes


def test_options_trade_regular_hours_only_even_inside_the_extended_window():
    order = option_intent()
    extended = evaluate(order, option_snapshot(
        order, market_open=True, regular_session_open=False))
    assert "market.regular_hours_required" in extended.reason_codes
    unknown = evaluate(order, option_snapshot(order, regular_session_open=None))
    assert "market.regular_hours_required" in unknown.reason_codes


def test_buy_to_close_needs_a_short_at_least_as_large():
    order = buy_to_close()
    assert evaluate(order, option_snapshot(
        order, position_quantity=Decimal("-1"))).allowed is True
    flat = evaluate(order, option_snapshot(order, position_quantity=Decimal("0")))
    assert "option.short_position_insufficient" in flat.reason_codes
    two = buy_to_close(quantity=Decimal("2"))
    assert "option.short_position_insufficient" in evaluate(
        two, option_snapshot(two, position_quantity=Decimal("-1"))).reason_codes


def test_a_kill_level_blocks_a_new_put_but_never_a_buy_to_close():
    closing = buy_to_close()
    snap = option_snapshot(closing, position_quantity=Decimal("-1"),
                           risk_state=Health.UNHEALTHY, cash=Health.UNKNOWN)
    assert evaluate(closing, snap).allowed is True
    opening = option_intent()
    decision = evaluate(opening, option_snapshot(
        opening, risk_state=Health.UNHEALTHY))
    assert "dependency.risk_state.unhealthy" in decision.reason_codes


def test_notional_is_quantity_times_price_times_one_hundred():
    order = buy_to_close()
    poor = option_snapshot(order, position_quantity=Decimal("-1"),
                           quote_price=Decimal("2.50"),
                           available_cash=Decimal("249.99"))
    assert "cash.insufficient" in evaluate(order, poor).reason_codes
    enough = option_snapshot(order, position_quantity=Decimal("-1"),
                             quote_price=Decimal("2.50"),
                             available_cash=Decimal("250"))
    assert evaluate(order, enough).allowed is True
    opening = option_intent()
    capped = option_snapshot(opening, max_order_notional=Decimal("119.99"))
    assert "exposure.max_order_notional" in evaluate(opening, capped).reason_codes
    closing_cap = option_snapshot(order, position_quantity=Decimal("-1"),
                                  quote_price=Decimal("2.50"),
                                  max_order_notional=Decimal("1"))
    assert evaluate(order, closing_cap).allowed is True


def test_a_second_put_on_a_contract_already_short_is_refused():
    """Spec section 9 fix 1: open puts are checked."""
    order = option_intent()
    decision = evaluate(order, option_snapshot(
        order, position_quantity=Decimal("-1")))
    assert "option.already_short_contract" in decision.reason_codes


def test_a_sell_to_open_call_is_refused():
    order = option_intent(symbol="APH261009C00130000", option_type="call")
    assert "option.sell_to_open_requires_put" in evaluate(
        order, option_snapshot(order)).reason_codes


def test_an_equity_snapshot_cannot_approve_an_option():
    order = option_intent()
    decision = evaluate(order, option_snapshot(order, asset_class="us_equity"))
    assert "option.snapshot_asset_class_mismatch" in decision.reason_codes


def test_identity_quote_and_position_checks_still_apply():
    order = option_intent()
    decision = evaluate(order, option_snapshot(
        order, quote_symbol="APH", positions_at=RTH - timedelta(minutes=5),
        armed=False))
    assert {"identity.not_armed", "quote.symbol_mismatch",
            "positions.stale"} <= set(decision.reason_codes)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_option_gate.py -q -p no:cacheprovider`
Expected: FAIL. `test_an_equity_intent_never_enters_the_option_branch` errors with `AttributeError: <class 'live_orders.gate.UnifiedOrderGate'> has no attribute '_evaluate_option'`, and the option tests fail with `positions.sell_must_be_reduce_only` among their reason codes.

- [ ] **Step 3: Write the minimal implementation**

In `backend/live_orders/gate.py`, add after `_DEPENDENCIES`:

```python
    #: The side each option position intent must carry.
    _OPTION_INTENT_SIDES = {
        "sell_to_open": OrderSide.SELL,
        "sell_to_close": OrderSide.SELL,
        "buy_to_open": OrderSide.BUY,
        "buy_to_close": OrderSide.BUY,
    }
```

Make the first statement of `evaluate` (before `blockers: list[str] = []`) this:

```python
        # swing-port: options take their own branch, entered ONLY for an
        # intent that says asset_class == "us_option". Every EB intent is
        # us_equity and runs the unchanged body below.
        if intent.asset_class == "us_option":
            return self._evaluate_option(intent, snapshot)
```

Append these methods to the class, after `evaluate`:

```python
    def _dependency_blockers(self, required, snapshot) -> list[str]:
        """Step 2 of ``evaluate`` (health, then freshness) for the options
        branch. A copy rather than a refactor, so EB's gate body stays
        literally the lines it was."""
        blockers: list[str] = []
        for name in required:
            health = getattr(snapshot, name)
            if health is Health.UNKNOWN:
                blockers.append(f"dependency.{name}.unknown")
            elif health is Health.UNHEALTHY:
                blockers.append(f"dependency.{name}.unhealthy")
        freshness = {
            "kill_switch": (snapshot.kill_switch_at, snapshot.max_control_age),
            "cash": (snapshot.cash_at, snapshot.max_cash_age),
            "calendar": (snapshot.calendar_at, snapshot.max_calendar_age),
            "persistence": (snapshot.persistence_at, snapshot.max_control_age),
            "risk_state": (snapshot.risk_state_at, snapshot.max_control_age),
            "watchdog": (snapshot.watchdog_at, snapshot.max_control_age),
        }
        for name in required:
            if name in ("quote", "positions"):
                continue
            timestamp, max_age = freshness[name]
            if timestamp is None:
                blockers.append(f"dependency.{name}.stale")
            elif timestamp - snapshot.observed_at > snapshot.max_clock_skew:
                blockers.append(f"dependency.{name}.clock_skew")
            elif snapshot.observed_at - timestamp > max_age:
                blockers.append(f"dependency.{name}.stale")
        return blockers

    def _evaluate_option(
        self, intent: OrderIntent, snapshot: DependencySnapshot
    ) -> GateDecision:
        """Spec section 6.1, options branch.

        Regular hours only. A sell-to-open put is cash-secured against cash
        less the collateral of open short puts and of pending sells-to-open
        (spec section 9 fix 8), and capped at 25% of equity per underlying
        with existing puts counted (fix 3). A contract already short is not
        sold again (fix 1). A buy-to-close needs a short at least as large.
        Notional and the order cap use quantity x price x multiplier; the
        order cap binds only opening orders. Whole contracts and the x100
        multiplier are enforced by OrderIntent itself.
        """
        blockers: list[str] = []
        notices: list[str] = []
        approved = intent.quantity
        multiplier = Decimal(intent.contract_multiplier)
        opening = intent.position_intent in ("sell_to_open", "buy_to_open")

        if intent.account_id != snapshot.account_id:
            blockers.append("identity.account_mismatch")
        if intent.instance_id != snapshot.instance_id:
            blockers.append("identity.instance_mismatch")
        if not snapshot.armed:
            blockers.append("identity.not_armed")
        if snapshot.asset_class != "us_option":
            blockers.append("option.snapshot_asset_class_mismatch")

        required = (
            self._DEPENDENCIES if opening else ("quote", "positions", "persistence")
        )
        blockers.extend(self._dependency_blockers(required, snapshot))

        if self._OPTION_INTENT_SIDES.get(intent.position_intent) is not intent.side:
            blockers.append("option.intent_side_mismatch")
        if snapshot.regular_session_open is not True:
            blockers.append("market.regular_hours_required")

        if snapshot.quote_symbol != intent.symbol:
            blockers.append("quote.symbol_mismatch")
        if snapshot.quote_at != intent.quote_at:
            blockers.append("quote.timestamp_mismatch")
        if snapshot.quote_at - snapshot.observed_at > snapshot.max_clock_skew:
            blockers.append("quote.clock_skew")
        elif snapshot.observed_at - snapshot.quote_at > snapshot.max_quote_age:
            blockers.append("quote.stale")
        if snapshot.quote_price <= 0:
            blockers.append("quote.invalid_price")
        if snapshot.position_symbol != intent.symbol:
            blockers.append("positions.symbol_mismatch")
        if snapshot.positions_at - snapshot.observed_at > snapshot.max_clock_skew:
            blockers.append("positions.clock_skew")
        elif (
            snapshot.observed_at - snapshot.positions_at
            > snapshot.max_positions_age
        ):
            blockers.append("positions.stale")
        if snapshot.risk_snapshot_id != intent.risk_snapshot_id:
            blockers.append("risk.snapshot_mismatch")

        held = snapshot.position_quantity
        if intent.position_intent == "buy_to_close":
            if -held < intent.quantity:
                blockers.append("option.short_position_insufficient")
        elif intent.position_intent == "sell_to_close":
            if held < intent.quantity:
                blockers.append("option.long_position_insufficient")
        elif intent.position_intent == "sell_to_open":
            if held < 0:
                blockers.append("option.already_short_contract")
            if intent.option_type != "put":
                blockers.append("option.sell_to_open_requires_put")
            else:
                collateral = intent.strike * multiplier * intent.quantity
                if snapshot.open_short_put_collateral is None:
                    blockers.append("option.collateral_unknown")
                elif collateral > (
                    snapshot.available_cash
                    - snapshot.open_short_put_collateral
                    - snapshot.pending_sell_to_open_collateral
                ):
                    blockers.append("option.collateral_insufficient")
                if (
                    snapshot.account_equity is None
                    or snapshot.underlying_put_collateral is None
                ):
                    blockers.append("option.underlying_cap_unknown")
                elif (
                    snapshot.underlying_put_collateral + collateral
                    > snapshot.account_equity
                    * snapshot.max_underlying_collateral_fraction
                ):
                    blockers.append("option.underlying_cap")

        notional = approved * snapshot.quote_price * multiplier
        if intent.side is OrderSide.BUY and notional > snapshot.available_cash:
            blockers.append("cash.insufficient")
        if (
            opening
            and snapshot.max_order_notional is not None
            and notional > snapshot.max_order_notional
        ):
            blockers.append("exposure.max_order_notional")

        if intent.idempotency_key in snapshot.open_order_idempotency_keys:
            blockers.append("idempotency.open_order_exists")
        if intent.source not in snapshot.authorized_sources:
            blockers.append("authorization.source_denied")

        allowed = not blockers
        return GateDecision(
            allowed=allowed,
            approved_quantity=approved if allowed else Decimal("0"),
            reason_codes=tuple(blockers + notices),
            idempotency_key=intent.idempotency_key,
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_option_gate.py backend/tests/test_live_order_gate.py backend/tests/test_live_dependency_freshness.py backend/tests/test_strategy_eb_gate.py backend/tests/test_gate_refusal_log.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()`. Expected symbols: `UnifiedOrderGate.evaluate` (one dispatch line), `_dependency_blockers` and `_evaluate_option`.

```bash
git add backend/live_orders/gate.py backend/tests/swing_live_fixtures.py backend/tests/test_swing_option_gate.py
git commit -F - <<'EOF'
feat(gate): options branch of the unified order gate

Entered only for asset_class us_option. Regular hours only; a
cash-secured put must fit in cash less open short-put and pending
sell-to-open collateral, and under 25 percent of equity per underlying
with existing puts counted; a contract already short is not sold again;
buy-to-close needs an equal or larger short and survives the kill
level; notional is quantity times price times 100. The equity body is
unedited.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 7: LiveOrderService transport extras, multiplier cash, bracket-leg rows, external fills; reconcile statuses and short-option ownership

**Files:**
- Modify: `backend/live_orders/service.py`:
  - imports (18-27)
  - `LiveOrderService.__init__` (128-165)
  - `submit` (381-483)
  - `apply_broker_event` (533-548)
  - new methods after `apply_broker_event`
  - module functions after `new_retry_intent`
- Modify: `backend/live_orders/reconcile.py:156-168` (`_STATUS_STATES`) and `:501-519` (positions ownership loop)
- Test: `backend/tests/test_swing_live_service.py` (create)

**Interfaces:**
- Consumes: Task 1 (`OrderSource.BRACKET_LEG`, `OPTION_ACTIVITY`, new intent fields, `ConfirmedFill` fields), Task 6 fixtures, and the Task 2 `OrderRef` leg fields (`client_order_id`, `order_type`, `limit_price`, `stop_price`, `qty`, `status`).
- Produces:
  - `LiveOrderService(..., legs_lookup: Optional[Callable[[str], Any]] = None)`
  - `LiveOrderService.register_bracket_legs(parent_intent, parent_reference) -> tuple[LifecycleRecord, ...]`
  - `LiveOrderService.ensure_bracket_legs() -> int`
  - `LiveOrderService.record_external_fill(intent, *, broker_order_id, quantity, price, occurred_at, reason="") -> EventApplication`
  - `live_orders.service.bracket_leg_intent(parent, leg) -> OrderIntent`
  - the `live_orders.service._transport_extras(intent) -> dict` helper

- [ ] **Step 0: Impact analysis**

```bash
grep -rn "LiveOrderService(\|apply_broker_event\|StartupReconciler(\|_STATUS_STATES" backend --include=*.py | grep -v "/tests/"
```

Run `mcp__gitnexus__impact` upstream on `LiveOrderService`, `submit`, `apply_broker_event` and `StartupReconciler.reconcile`. Callers:
- `broker.py:11207` builds EB's service.
- `broker.py:9450` runs the reconciler every 60 s and before submits.
- `apply_broker_event` is also the stream sink (`broker.py:11228`).

**Risk: HIGH.** Two changes are visible to EB:
- a `pending_cancel` order status now reconciles as acknowledged, where before it was an `unsupported_broker_status` issue that made one reconcile unhealthy;
- the same event on the stream now dedupes against the existing ACK.

Both are strictly less blocking. Tell the user.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_live_service.py`:

```python
"""swing-port Task 7: service transport extras, contract-multiplier cash,
bracket-leg lifecycle rows and external fills; reconcile's held and
pending_cancel statuses and short-option ownership.

EB pins computed from the PRE-change code on 2026-09-24. Never edit them."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from live_orders import (
    AuthoritativeBrokerSnapshot,
    BrokerOrderEvent,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    InMemoryLifecycleBackend,
    LifecycleConflict,
    LifecycleState,
    LiveOrderService,
    OrderIntent,
    OrderLifecycleStore,
    OrderSide,
    OrderSource,
    StartupReconciler,
)
from live_orders.service import bracket_leg_intent
from live_order_task8_helpers import broker_ref, event, intent, snapshot
from swing_live_fixtures import (
    OCC,
    RTH,
    bracket_intent,
    equity_snapshot,
    leg_ref,
    option_intent,
    option_snapshot,
    parent_ref,
)

EB_BUY_KWARGS = {
    "symbol": "TQQQ", "side": "buy", "qty": 5.0, "notional": None,
    "order_type": "market", "limit_price": None, "tif": "day",
    "extended_hours": False,
    "client_order_id": "instance-efac83a02fb2f5054a31ae56985e212a45729-0",
}
EB_SELL_KWARGS = {
    "symbol": "TQQQ", "side": "sell", "qty": 3.0, "notional": None,
    "order_type": "limit", "limit_price": 99.5, "tif": "day",
    "extended_hours": True,
    "client_order_id": "instance-f66835a1faf331e09b98f197e71b558826058-0",
}
EB_RECONCILE_HASH = (
    "a416811574d648504d6c40c999308365a8c8b43801e790fa349b52308173834b")


def _service(order, *, transport=None, provider=None, store=None, **kwargs):
    return LiveOrderService(
        account_id=order.account_id, instance_id=order.instance_id,
        snapshot_provider=provider, transport=transport,
        lifecycle_store=store or OrderLifecycleStore(InMemoryLifecycleBackend()),
        **kwargs)


def _recording(order):
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        return broker_ref(order)

    return calls, transport


def _legs_lookup(*legs, fail=None):
    state = {"fail": fail}

    def lookup(order_id):
        if state["fail"]:
            raise RuntimeError(state["fail"])
        return SimpleNamespace(broker_order_id=order_id, legs=tuple(legs))

    return state, lookup


def _leg_event(order, cid, state, cumulative="0", average=None):
    return BrokerOrderEvent(
        event_id=f"{cid}:{state.value}:{cumulative}",
        account_id=order.account_id, instance_id=order.instance_id,
        client_order_id=cid, broker_order_id=f"broker-{cid}",
        symbol=order.symbol, side=OrderSide.SELL, state=state,
        cumulative_quantity=Decimal(cumulative),
        cumulative_average_price=Decimal(average) if average else None,
        cumulative_fees=Decimal("0"), occurred_at=RTH)


def _broker_order(cid, broker_id, *, symbol="AAPL", side="buy", qty="5",
                  status, filled="0", avg=None):
    return BrokerOrderSnapshot(
        client_order_id=cid, broker_order_id=broker_id, symbol=symbol,
        side=OrderSide(side), requested_quantity=Decimal(qty), status=status,
        cumulative_quantity=Decimal(filled),
        cumulative_average_price=Decimal(avg) if avg else None,
        cumulative_fees=Decimal("0"), updated_at=RTH)


def _snapshot(order, positions, orders):
    return AuthoritativeBrokerSnapshot(
        account_id=order.account_id, instance_id=order.instance_id,
        observed_at=RTH,
        positions=tuple(BrokerPositionSnapshot(symbol=s, quantity=Decimal(q))
                        for s, q in positions),
        orders=tuple(orders), broker_available=True, positions_stable=True,
        orders_complete=True)


def _reconcile(service, snap):
    return StartupReconciler(
        lifecycle_store=service.lifecycle_store,
        event_applier=service.apply_broker_event,
        cid_prefix="instance-").reconcile(snap)


# --- EB invariance ------------------------------------------------------------

def test_eb_transport_kwargs_are_byte_identical():
    buy = intent(symbol="TQQQ", reason="eb_rebalance")
    calls, transport = _recording(buy)
    _service(buy, transport=transport, provider=snapshot).submit(buy)
    assert calls == [EB_BUY_KWARGS]
    sell = intent(symbol="TQQQ", reason="eb_rotation_trim",
                  side=OrderSide.SELL, quantity=Decimal("3"),
                  reduce_only=True, order_type="limit",
                  limit_price=Decimal("99.5"), extended_hours=True)
    calls, transport = _recording(sell)
    _service(sell, transport=transport, provider=snapshot).submit(sell)
    assert calls == [EB_SELL_KWARGS]


def test_an_eb_shaped_reconcile_is_byte_identical():
    at = datetime(2026, 9, 24, 13, 31, 5, tzinfo=timezone.utc)
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    buy = OrderIntent(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        source=OrderSource.STRATEGY, reason="eb_rebalance", symbol="TQQQ",
        side=OrderSide.BUY, quantity=Decimal("10"), reduce_only=False,
        decision_at=at, quote_at=at, risk_snapshot_id="risk-1",
        reference_price=Decimal("88"))
    store.create_intent(buy)
    service = LiveOrderService(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        snapshot_provider=None, transport=None, lifecycle_store=store)
    service.apply_broker_event(BrokerOrderEvent(
        event_id="ack-1", account_id="brk-alpaca-main",
        instance_id="alpaca-main", client_order_id=buy.idempotency_key,
        broker_order_id="b-1", symbol="TQQQ", side=OrderSide.BUY,
        state=LifecycleState.ACKNOWLEDGED, cumulative_quantity=Decimal("0"),
        cumulative_average_price=None, cumulative_fees=Decimal("0"),
        occurred_at=at))
    orders = (
        BrokerOrderSnapshot(
            client_order_id=buy.idempotency_key, broker_order_id="b-1",
            symbol="TQQQ", side=OrderSide.BUY,
            requested_quantity=Decimal("10"), status="filled",
            cumulative_quantity=Decimal("10"),
            cumulative_average_price=Decimal("88"),
            cumulative_fees=Decimal("0"), updated_at=at),
        BrokerOrderSnapshot(
            client_order_id="manual-xyz", broker_order_id="b-2",
            symbol="SPY", side=OrderSide.BUY, requested_quantity=Decimal("2"),
            status="filled", cumulative_quantity=Decimal("2"),
            cumulative_average_price=Decimal("500"),
            cumulative_fees=Decimal("0"), updated_at=at),
    )
    snap = AuthoritativeBrokerSnapshot(
        account_id="brk-alpaca-main", instance_id="alpaca-main",
        observed_at=at,
        positions=(
            BrokerPositionSnapshot(symbol="TQQQ", quantity=Decimal("10"),
                                   market_value=Decimal("880")),
            BrokerPositionSnapshot(symbol="SPY", quantity=Decimal("2"),
                                   market_value=Decimal("1000"))),
        orders=orders, broker_available=True, positions_stable=True,
        orders_complete=True)
    result = StartupReconciler(
        lifecycle_store=store, event_applier=service.apply_broker_event,
        cid_prefix="alpacama-").reconcile(snap)
    assert dict(result.owned) == {"TQQQ": Decimal("10")}
    assert dict(result.external) == {"SPY": Decimal("2")}
    assert result.healthy is True and result.issues == ()
    assert result.evidence_hash == EB_RECONCILE_HASH


def test_the_alpaca_runtime_defers_to_the_lifecycle_reconciler():
    """The legacy _classifier.py quarantines every short; it is safe to leave
    only while no Alpaca runtime takes that path."""
    source = (Path(__file__).resolve().parents[1] / "broker.py").read_text()
    assert "defer_ownership_reconciliation=_is_alpaca_runtime" in source


# --- transport extras and multiplier cash -------------------------------------

def test_bracket_kwargs_carry_the_leg_prices_and_nothing_else():
    order = bracket_intent()
    calls, transport = _recording(order)
    assert _service(order, transport=transport,
                    provider=equity_snapshot).submit(order).accepted
    extras = {k: v for k, v in calls[0].items() if k not in EB_BUY_KWARGS}
    assert extras == {"order_class": "bracket", "take_profit": 109.0,
                      "stop_loss": 94.0}
    assert (calls[0]["tif"], calls[0]["qty"], calls[0]["order_type"]) == (
        "gtc", 5.0, "market")


def test_option_kwargs_and_contract_multiplier_cash():
    order = option_intent()
    calls, transport = _recording(order)
    fills = []
    service = _service(order, transport=transport, provider=option_snapshot,
                       confirmed_fill_handler=fills.append)
    assert service.submit(order).accepted
    assert calls[0]["asset_class"] == "us_option"
    assert calls[0]["position_intent"] == "sell_to_open"
    assert calls[0]["qty"] == 1.0 and "order_class" not in calls[0]
    assert service.reservation_for(order.idempotency_key).remaining_notional \
        == Decimal("120")
    applied = service.apply_broker_event(event(
        order, state=LifecycleState.FILLED, cumulative=Decimal("1"),
        average=Decimal("1.23"), fees=Decimal("0.65")))
    assert applied.fill.cash_delta == Decimal("122.35")
    assert applied.fill.position_delta == Decimal("-1")
    assert (applied.fill.asset_class, applied.fill.contract_multiplier) == (
        "us_option", 100)
    assert fills == [applied.fill]


# --- bracket legs -------------------------------------------------------------

def _bracket_service(order, *, transport, lookup, fills=None):
    return _service(order, transport=transport, provider=equity_snapshot,
                    legs_lookup=lookup,
                    confirmed_fill_handler=(fills.append if fills is not None
                                            else None))


def test_a_bracket_submit_registers_both_legs_as_lifecycle_rows():
    order = bracket_intent()
    _state, lookup = _legs_lookup(leg_ref("leg-tp", "limit", status="new"),
                                  leg_ref("leg-sl", "stop"))
    service = _bracket_service(order, transport=lambda **kw: parent_ref(order),
                               lookup=lookup)
    assert service.submit(order).accepted
    store = service.lifecycle_store
    tp, sl = store.require("leg-tp"), store.require("leg-sl")
    for leg in (tp, sl):
        assert leg.intent.source is OrderSource.BRACKET_LEG
        assert leg.intent.parent_client_order_id == order.idempotency_key
        assert (leg.intent.side, leg.intent.reduce_only) == (OrderSide.SELL, True)
        assert leg.state is LifecycleState.ACKNOWLEDGED
    assert (tp.intent.order_type, tp.intent.limit_price) == ("limit", Decimal("109.0"))
    assert (sl.intent.order_type, sl.intent.stop_loss_price) == ("market", Decimal("94"))
    assert tp.broker_order_id == "broker-leg-tp"
    assert service.ensure_bracket_legs() == 0


def test_a_leg_fill_resolves_to_its_row_and_credits_the_sale():
    order = bracket_intent()
    fills = []
    _state, lookup = _legs_lookup(leg_ref("leg-tp", "limit", status="new"),
                                  leg_ref("leg-sl", "stop"))
    service = _bracket_service(
        order, transport=lambda **kw: parent_ref(
            order, status="filled", filled_qty="5", filled_avg_price="100"),
        lookup=lookup, fills=fills)
    service.submit(order)
    applied = service.apply_broker_event(
        _leg_event(order, "leg-tp", LifecycleState.FILLED, "5", "109"))
    assert applied.applied is True
    assert applied.fill.position_delta == Decimal("-5")
    assert applied.fill.cash_delta == Decimal("545")
    assert [f.event.side.value for f in fills] == ["buy", "sell"]


def test_leg_fill_before_registration_is_recovered_exactly_once():
    """Review Focus 1."""
    order = bracket_intent()
    fills = []
    state, lookup = _legs_lookup(
        leg_ref("leg-tp", "limit", status="filled", filled_qty="5",
                filled_avg_price="109"),
        leg_ref("leg-sl", "stop", status="canceled"),
        fail="nested read timed out")
    service = _bracket_service(
        order, transport=lambda **kw: parent_ref(
            order, status="filled", filled_qty="5", filled_avg_price="100"),
        lookup=lookup, fills=fills)
    assert service.submit(order).accepted is True
    with pytest.raises(LifecycleConflict):
        service.apply_broker_event(
            _leg_event(order, "leg-tp", LifecycleState.FILLED, "5", "109"))
    state["fail"] = None
    assert service.ensure_bracket_legs() == 2
    assert service.lifecycle_store.require("leg-tp").state is LifecycleState.FILLED
    parent = _broker_order(order.idempotency_key, "broker-parent",
                           status="filled", filled="5", avg="100")
    tp = _broker_order("leg-tp", "broker-leg-tp", side="sell",
                       status="filled", filled="5", avg="109")
    sl = _broker_order("leg-sl", "broker-leg-sl", side="sell",
                       status="canceled")
    result = _reconcile(service, _snapshot(order, [], [parent, tp, sl]))
    assert result.healthy is True and result.issues == ()
    assert [f.event.side.value for f in fills] == ["buy", "sell"]
    assert service.ensure_bracket_legs() == 0


def test_partial_parent_owns_only_filled_shares():
    """Review Focus 2."""
    order = bracket_intent()
    fills = []
    _state, lookup = _legs_lookup(leg_ref("leg-tp", "limit"),
                                  leg_ref("leg-sl", "stop"))
    service = _bracket_service(
        order, transport=lambda **kw: parent_ref(
            order, status="partially_filled", filled_qty="3",
            filled_avg_price="100"),
        lookup=lookup, fills=fills)
    assert service.submit(order).accepted
    parent = _broker_order(order.idempotency_key, "broker-parent",
                           status="partially_filled", filled="3", avg="100")
    working = [
        _broker_order("leg-tp", "broker-leg-tp", side="sell", status="new"),
        _broker_order("leg-sl", "broker-leg-sl", side="sell", status="held"),
    ]
    result = _reconcile(service, _snapshot(order, [("AAPL", "3")],
                                           [parent, *working]))
    assert result.healthy is True, result.issues
    assert dict(result.owned) == {"AAPL": Decimal("3")}
    service.apply_broker_event(
        _leg_event(order, "leg-tp", LifecycleState.FILLED, "3", "109"))
    done = [
        _broker_order("leg-tp", "broker-leg-tp", side="sell", status="filled",
                      filled="3", avg="109"),
        _broker_order("leg-sl", "broker-leg-sl", side="sell",
                      status="canceled"),
    ]
    result = _reconcile(service, _snapshot(order, [], [parent, *done]))
    assert result.healthy is True, result.issues
    assert [f.event.side.value for f in fills] == ["buy", "sell"]


def test_held_and_pending_cancel_legs_are_working_orders_not_issues():
    order = bracket_intent()
    _state, lookup = _legs_lookup(leg_ref("leg-tp", "limit", status="new"),
                                  leg_ref("leg-sl", "stop"))
    service = _bracket_service(
        order, transport=lambda **kw: parent_ref(
            order, status="filled", filled_qty="5", filled_avg_price="100"),
        lookup=lookup)
    service.submit(order)
    orders = [
        _broker_order(order.idempotency_key, "broker-parent", status="filled",
                      filled="5", avg="100"),
        _broker_order("leg-tp", "broker-leg-tp", side="sell",
                      status="pending_cancel"),
        _broker_order("leg-sl", "broker-leg-sl", side="sell", status="held"),
    ]
    result = _reconcile(service, _snapshot(order, [("AAPL", "5")], orders))
    assert result.healthy is True and result.issues == ()
    assert dict(result.owned) == {"AAPL": Decimal("5")}


def test_a_leg_without_a_client_order_id_cannot_be_tracked():
    with pytest.raises(ValueError):
        bracket_leg_intent(bracket_intent(), leg_ref("", "limit"))


# --- external fills (assignments) ---------------------------------------------

def test_record_external_fill_is_exactly_once():
    fills = []
    assignment = OrderIntent(
        account_id="acct-1", instance_id="instance-1",
        source=OrderSource.OPTION_ACTIVITY, reason=f"wheel_assignment:{OCC}",
        symbol="APH", side=OrderSide.BUY, quantity=Decimal("100"),
        reduce_only=False, decision_at=RTH, quote_at=RTH,
        risk_snapshot_id="option-activity", reference_price=Decimal("130"),
        broker_client_order_id="opasn-abc")
    service = _service(assignment, confirmed_fill_handler=fills.append)
    kwargs = dict(broker_order_id="opasn-abc", quantity=Decimal("100"),
                  price=Decimal("130"), occurred_at=RTH,
                  reason="option assignment")
    first = service.record_external_fill(assignment, **kwargs)
    again = service.record_external_fill(assignment, **kwargs)
    assert first.applied is True and first.fill.cash_delta == Decimal("-13000")
    assert again.applied is False and len(fills) == 1


# --- reconcile: short options -------------------------------------------------

def _short_put_service(*, filled=True):
    order = option_intent()
    service = _service(order, transport=lambda **kw: broker_ref(order),
                       provider=option_snapshot)
    assert service.submit(order).accepted
    if filled:
        service.apply_broker_event(event(
            order, state=LifecycleState.FILLED, cumulative=Decimal("1"),
            average=Decimal("1.20")))
    return order, service


def _sto_row(order):
    return _broker_order(order.idempotency_key, "broker-1", symbol=OCC,
                         side="sell", qty="1", status="filled", filled="1",
                         avg="1.20")


def test_a_short_option_with_lifecycle_lineage_is_owned():
    order, service = _short_put_service()
    result = _reconcile(service, _snapshot(order, [(OCC, "-1")],
                                           [_sto_row(order)]))
    assert dict(result.owned) == {OCC: Decimal("-1")}
    assert dict(result.external) == {}
    assert result.healthy is True and result.issues == ()


def test_a_short_without_option_lineage_stays_external():
    order = option_intent()
    service = _service(order)
    result = _reconcile(service, _snapshot(
        order, [(OCC, "-1"), ("AAPL", "-3")], []))
    assert dict(result.external) == {OCC: Decimal("-1"), "AAPL": Decimal("-3")}
    assert dict(result.owned) == {} and result.healthy is True


def test_an_expired_or_assigned_short_put_leaves_no_issue():
    order, service = _short_put_service()
    result = _reconcile(service, _snapshot(order, [], [_sto_row(order)]))
    assert result.healthy is True and result.issues == ()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_live_service.py -q -p no:cacheprovider`
Expected: a collection error, `ImportError: cannot import name 'bracket_leg_intent' from 'live_orders.service'`.

- [ ] **Step 3: Write the minimal implementation**

In `backend/live_orders/service.py`, add `OrderSource,` to the `from .types import (...)` block.

After `new_retry_intent`, add:

```python
def _transport_extras(intent: OrderIntent) -> dict:
    """Transport keywords only a new-field intent carries. {} for every EB
    intent, so its transport call is byte-identical (pinned in
    tests/test_swing_live_service.py)."""
    extras: dict = {}
    if intent.order_class is not None:
        extras["order_class"] = intent.order_class
        extras["take_profit"] = float(intent.take_profit_price)
        extras["stop_loss"] = float(intent.stop_loss_price)
    if intent.asset_class != "us_equity":
        extras["asset_class"] = intent.asset_class
    if intent.position_intent is not None:
        extras["position_intent"] = intent.position_intent
    return extras


def bracket_leg_intent(parent: OrderIntent, leg) -> OrderIntent:
    """The lifecycle intent for one child leg of a bracket parent.

    Keyed by the client order id Alpaca minted for the leg, so a leg fill,
    from the stream or from reconciliation, resolves to this row. The
    take-profit leg is a limit order; the stop-loss leg is recorded as a
    market exit that carries its stop price.
    """
    leg_cid = str(getattr(leg, "client_order_id", "") or "").strip()
    if not leg_cid:
        raise ValueError("a bracket leg without a client order id cannot be tracked")
    leg_type = str(
        getattr(getattr(leg, "order_type", None), "value", getattr(leg, "order_type", None))
        or ""
    ).strip().lower()
    take_profit = leg_type == "limit"
    quantity = Decimal(str(getattr(leg, "qty", 0) or 0))
    if quantity <= 0:
        quantity = parent.quantity
    limit_price = None
    if take_profit:
        raw_limit = getattr(leg, "limit_price", None)
        limit_price = (
            Decimal(str(raw_limit))
            if raw_limit not in (None, 0, "0")
            else parent.take_profit_price
        )
    return OrderIntent(
        account_id=parent.account_id,
        instance_id=parent.instance_id,
        source=OrderSource.BRACKET_LEG,
        reason="bracket_take_profit" if take_profit else "bracket_stop_loss",
        symbol=parent.symbol,
        side=OrderSide.SELL,
        quantity=quantity,
        reduce_only=True,
        decision_at=parent.decision_at,
        quote_at=parent.quote_at,
        risk_snapshot_id=parent.risk_snapshot_id,
        order_type="limit" if take_profit else "market",
        limit_price=limit_price,
        tif="gtc",
        take_profit_price=parent.take_profit_price,
        stop_loss_price=parent.stop_loss_price,
        parent_client_order_id=parent.idempotency_key,
        broker_client_order_id=leg_cid,
    )
```

In `LiveOrderService.__init__`, add the parameter `legs_lookup: Optional[Callable[[str], Any]] = None,` directly after `lookup_by_client_id: ...,`, and add `self._legs_lookup = legs_lookup` directly after `self._lookup_by_client_id = lookup_by_client_id`.

In `submit`, replace:

```python
        self._reservations[intent.idempotency_key] = Reservation(
            client_order_id=intent.idempotency_key,
            side=intent.side,
            remaining_quantity=decision.approved_quantity,
            remaining_notional=decision.approved_quantity * snapshot.quote_price,
        )
```

with:

```python
        reserved_notional = decision.approved_quantity * snapshot.quote_price
        if intent.contract_multiplier != 1:
            reserved_notional = reserved_notional * intent.contract_multiplier
        self._reservations[intent.idempotency_key] = Reservation(
            client_order_id=intent.idempotency_key,
            side=intent.side,
            remaining_quantity=decision.approved_quantity,
            remaining_notional=reserved_notional,
        )
```

In the transport call, add `**_transport_extras(intent),` as the last keyword, after `client_order_id=intent.idempotency_key,`.

Replace:

```python
            if reference is not None:
                self.apply_broker_event(self._event_from_reference(intent, reference))
                return OrderSubmission(decision=decision, reference=reference)
```

with:

```python
            if reference is not None:
                self.apply_broker_event(self._event_from_reference(intent, reference))
                self._register_legs_quietly(intent, reference)
                return OrderSubmission(decision=decision, reference=reference)
```

Replace the last two lines of `submit`:

```python
        self.apply_broker_event(self._event_from_reference(intent, reference))
        return OrderSubmission(decision=decision, reference=reference)
```

with:

```python
        self.apply_broker_event(self._event_from_reference(intent, reference))
        self._register_legs_quietly(intent, reference)
        return OrderSubmission(decision=decision, reference=reference)
```

In `apply_broker_event`, replace:

```python
        if delta > 0 and incremental_price is not None:
            notional = delta * incremental_price
```

with:

```python
        if delta > 0 and incremental_price is not None:
            notional = delta * incremental_price
            multiplier = record.intent.contract_multiplier
            if multiplier != 1:
                # swing-port: a contract fill moves quantity x price x 100.
                notional = notional * multiplier
```

and in the `ConfirmedFill(...)` construction add after `cash_delta=cash_delta,`:

```python
                asset_class=record.intent.asset_class,
                contract_multiplier=multiplier,
```

Append these methods to `LiveOrderService`:

```python
    def _register_legs_quietly(self, intent: OrderIntent, reference: Any) -> None:
        """Bracket parents only: read the legs back and record them. A failure
        never turns an accepted order into a refusal; ensure_bracket_legs
        retries it on the next reconcile."""
        if intent.order_class != "bracket" or self._legs_lookup is None:
            return
        try:
            self.register_bracket_legs(intent, reference)
        except Exception as exc:
            self._report_swallowed(intent, "bracket.legs.unregistered", exc)

    def register_bracket_legs(
        self, parent_intent: OrderIntent, parent_reference: Any
    ) -> tuple[LifecycleRecord, ...]:
        """Record each child leg of a submitted bracket as its own lifecycle
        row (source bracket_leg, keyed by Alpaca's leg client order id), so a
        leg fill resolves to a known record (spec section 6.1)."""
        broker_order_id = str(
            getattr(parent_reference, "broker_order_id", None)
            or getattr(parent_reference, "id", None)
            or ""
        ).strip()
        if not broker_order_id:
            raise LifecycleConflict(
                "bracket parent has no broker order id; legs cannot be read"
            )
        return self._register_legs_for(parent_intent, broker_order_id)

    def _register_legs_for(
        self, parent_intent: OrderIntent, broker_order_id: str
    ) -> tuple[LifecycleRecord, ...]:
        if self._legs_lookup is None or parent_intent.order_class != "bracket":
            return ()
        nested = self._legs_lookup(broker_order_id)
        records = []
        for leg in tuple(getattr(nested, "legs", ()) or ()):
            leg_intent = bracket_leg_intent(parent_intent, leg)
            if self.lifecycle_store.get(leg_intent.idempotency_key) is None:
                self.lifecycle_store.create_intent(leg_intent)
            self.apply_broker_event(self._event_from_reference(leg_intent, leg))
            records.append(self.lifecycle_store.require(leg_intent.idempotency_key))
        return tuple(records)

    def ensure_bracket_legs(self) -> int:
        """Register the legs of every bracket parent that has none recorded:
        a nested read that failed right after submission, or a restart.
        Returns the number of leg rows registered. Idempotent."""
        if self._legs_lookup is None:
            return 0
        records = self.lifecycle_store.list_for_instance(self.instance_id)
        with_legs = {
            record.intent.parent_client_order_id
            for record in records
            if record.intent.source is OrderSource.BRACKET_LEG
        }
        registered = 0
        for record in records:
            intent = record.intent
            if intent.order_class != "bracket":
                continue
            if record.client_order_id in with_legs or not record.broker_order_id:
                continue
            if record.terminal and record.cumulative_quantity <= 0:
                continue
            try:
                registered += len(
                    self._register_legs_for(intent, record.broker_order_id)
                )
            except Exception as exc:
                self._report_swallowed(intent, "bracket.legs.unregistered", exc)
        return registered

    def record_external_fill(
        self,
        intent: OrderIntent,
        *,
        broker_order_id: str,
        quantity,
        price,
        occurred_at: datetime,
        reason: str = "",
    ) -> EventApplication:
        """Record a fill no order produced (an option assignment) as a FILLED
        lifecycle row. Exactly once: a second call finds the row terminal and
        applies nothing, so no fill is counted or announced twice."""
        if self.lifecycle_store.get(intent.idempotency_key) is None:
            self.lifecycle_store.create_intent(intent)
        quantity = Decimal(str(quantity))
        price = Decimal(str(price))
        filled = BrokerOrderEvent(
            event_id=_event_id(
                intent.idempotency_key,
                broker_order_id,
                LifecycleState.FILLED,
                quantity,
                Decimal("0"),
            ),
            account_id=intent.account_id,
            instance_id=intent.instance_id,
            client_order_id=intent.idempotency_key,
            broker_order_id=broker_order_id,
            symbol=intent.symbol,
            side=intent.side,
            state=LifecycleState.FILLED,
            cumulative_quantity=quantity,
            cumulative_average_price=price,
            cumulative_fees=Decimal("0"),
            occurred_at=occurred_at,
            reason=reason,
        )
        return self.apply_broker_event(filled)
```

In `backend/live_orders/reconcile.py`, add to `_STATUS_STATES`, after `"done_for_day": LifecycleState.EXPIRED,`:

```python
    # swing-port: a bracket's stop leg waits in "held"; an order being
    # cancelled is still working until Alpaca confirms "canceled".
    "held": LifecycleState.ACKNOWLEDGED,
    "pending_cancel": LifecycleState.ACKNOWLEDGED,
```

In `StartupReconciler.reconcile`, replace:

```python
        owned: dict[str, Decimal] = {}
        external: dict[str, Decimal] = {}
        for symbol, broker_quantity in positions.items():
            if broker_quantity < 0:
                external[symbol] = broker_quantity
                continue
```

with:

```python
        # swing-port: a short option that this instance's lifecycle sold is
        # owned (negative), not external. A negative broker quantity with no
        # option lineage (an equity short, a manual option) stays external.
        option_symbols = {
            record.intent.symbol
            for record in records.values()
            if record.intent.asset_class == "us_option"
        }
        owned: dict[str, Decimal] = {}
        external: dict[str, Decimal] = {}
        for symbol, broker_quantity in positions.items():
            if broker_quantity < 0:
                proven_short = min(Decimal("0"), lineage.get(symbol, Decimal("0")))
                if symbol in option_symbols and proven_short < 0:
                    owned_short = max(broker_quantity, proven_short)
                    owned[symbol] = owned_short
                    if broker_quantity < owned_short:
                        external[symbol] = broker_quantity - owned_short
                    continue
                external[symbol] = broker_quantity
                continue
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_live_service.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Run the live-order regression suites**

Run: `python3 -m pytest backend/tests/test_live_order_service.py backend/tests/test_live_order_recovery.py backend/tests/test_live_order_partial_fills.py backend/tests/test_live_order_retry.py backend/tests/test_live_order_crash_matrix.py backend/tests/test_live_order_chaos.py backend/tests/test_live_order_silent_swallows.py backend/tests/test_split_reconcile.py backend/tests/test_kalshi_reconcile.py backend/tests/test_swing_live_types.py backend/tests/test_swing_option_gate.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 6: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()`. Expected symbols: `LiveOrderService.__init__`, `submit`, `apply_broker_event`, `_register_legs_quietly`, `register_bracket_legs`, `_register_legs_for`, `ensure_bracket_legs`, `record_external_fill`, `_transport_extras`, `bracket_leg_intent`, `_STATUS_STATES` and `StartupReconciler.reconcile`.

```bash
git add backend/live_orders/service.py backend/live_orders/reconcile.py backend/tests/test_swing_live_service.py
git commit -F - <<'EOF'
feat(live-orders): bracket leg rows, contract cash and short-option ownership

The service passes bracket and option fields to the transport only when
the intent carries them (EB kwargs pinned), charges option fills times
the contract multiplier, and registers each bracket leg as its own
lifecycle row keyed by the broker's leg id, retrying via
ensure_bracket_legs. An external fill (option assignment) is recorded
exactly once. Reconcile treats held and pending_cancel as working
orders and owns a short option that its own lifecycle sold; the EB-shaped
reconcile evidence hash is pinned.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---
### Task 8: broker.py dispatcher side channel, per-tick merge, lane registration and `_lane_enabled`

**Files:**
- Modify: `backend/broker.py`:
  - `_LANE_ENABLE_FLAGS` (4398-4409)
  - add `_lane_enabled` directly before `def _strategy_eb_risk_limits(` (4458)
  - `defaults_by_lane` in `_strategy_eb_risk_limits` (4506-4508)
  - `run_run_once_strategies` pops (7545-7547)
  - per-tick merge (15755-15794)
- Create: `backend/tests/swing_broker_harness.py`
- Test: `backend/tests/test_swing_broker_dispatch.py` (create)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `_lane_enabled(cached_strategies, lane) -> bool`
  - metadata key `_nexus_option_orders: list[dict]`, present only when non-empty
  - main-loop local `nexus_option_orders: list` (every spec's orders concatenated)
  - `_LANE_ENABLE_FLAGS` gains `strategy_swing` / `strategyswing` → `strategy_swing_enabled` and `strategy_wheel` / `strategywheel` → `strategy_wheel_enabled`
  - the test harness `swing_broker_harness.extract(functions, *, assigns=(), namespace=None, check=None)`, plus `free_names`, `module_assign` and `source`

- [ ] **Step 0: Impact analysis (GitNexus does not index broker.py)**

```bash
grep -n "_LANE_ENABLE_FLAGS\|defaults_by_lane\|nexus_executable_buys = raw.pop\|nexus_max_positions = None" backend/broker.py
grep -rln "_LANE_ENABLE_FLAGS\|run_run_once_strategies" backend/tests
```

Readers:
- `_LANE_ENABLE_FLAGS` is read by `_strategy_eb_single_position_pct` and `_strategy_eb_risk_limits`, EB's live envelope. It is pinned by `test_live_risk_limits.py` and `test_strategy_hx_broker_wiring.py`.
- `run_run_once_strategies` runs every EB tick.

**Risk: HIGH.** The whole document's envelope KeyErrors if a registered lane has no defaults row (D5). Tell the user.

- [ ] **Step 1: Write the failing test and the harness**

Create `backend/tests/swing_broker_harness.py`:

```python
"""AST extraction of broker.py functions for the swing-port tests.

broker.py argparses at import and SystemExits under pytest, so its functions
are lifted out of the syntax tree and run in a namespace the test provides.
``extract`` refuses to hand back a function whose free names the namespace
does not bind: a NameError inside a blanket ``except`` reads as a quiet
wrong answer (15 failures in test_strategy_x_broker_coexistence once).
"""
from __future__ import annotations

import ast
import builtins
import os

BROKER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "broker.py")
_TREE = None


def source() -> str:
    with open(BROKER_PATH, encoding="utf-8") as handle:
        return handle.read()


def tree():
    global _TREE
    if _TREE is None:
        _TREE = ast.parse(source())
    return _TREE


def _assigned(node) -> set:
    """Names a module-level ``x = ...`` or ``x: T = ...`` statement binds."""
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def module_assign(name):
    """A module-level literal assignment, evaluated."""
    for node in tree().body:
        if name in _assigned(node) and node.value is not None:
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found at broker.py module scope")


def function_source(name) -> str:
    node = _function(name)
    return ast.get_source_segment(source(), node)


def _function(name):
    for node in tree().body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in broker.py")


def free_names(function_name) -> set:
    """Module-scope names the function reads but never binds."""
    fn = _function(function_name)
    bound = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.arguments):
            for arg in node.posonlyargs + node.args + node.kwonlyargs:
                bound.add(arg.arg)
            for slot in (node.vararg, node.kwarg):
                if slot is not None:
                    bound.add(slot.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    loads = {n.id for n in ast.walk(fn)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return loads - bound - set(dir(builtins))


def extract(functions, *, assigns=(), namespace=None, check=None):
    """Execute the named top-level functions (and module assignments) of
    broker.py in ``namespace``. Every function in ``check`` (default: all of
    ``functions``) must have all of its free names provided."""
    wanted = set(functions)
    wanted_assigns = set(assigns)
    nodes = [
        node for node in tree().body
        if (isinstance(node, ast.FunctionDef) and node.name in wanted)
        or (_assigned(node) & wanted_assigns)
    ]
    found = {n.name for n in nodes if isinstance(n, ast.FunctionDef)}
    for node in nodes:
        found |= _assigned(node)
    missing = (wanted | wanted_assigns) - found
    assert not missing, f"missing from broker.py: {sorted(missing)}"
    ns = {"__name__": "broker_extract"}
    ns.update(namespace or {})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), BROKER_PATH, "exec"), ns)
    unbound = {}
    for name in (check if check is not None else wanted):
        gaps = sorted(free_names(name) - set(ns))
        if gaps:
            unbound[name] = gaps
    assert not unbound, f"harness does not provide: {unbound}"
    return ns
```

Create `backend/tests/test_swing_broker_dispatch.py`:

```python
"""swing-port Task 8: the _nexus_option_orders side channel, the per-tick
merge, and registering the swing and wheel lanes."""
import os
from contextlib import nullcontext
from datetime import datetime, timezone

import pytest

from live_risk_state import RiskLimits
from swing_broker_harness import extract, module_assign, source

DISPATCHER = (
    "run_run_once_strategies", "_residual_sleeve_config",
    "_eb_live_portfolio_view", "_eb_decision_outside_rth",
    "_merged_strategy_settings", "_llm_resolution_is_fatal",
    "_resolve_nexus_runtime_identity", "_run_graph_nexus_with_point_in_time",
)
ORDER = {"signal_id": None, "underlying": "APH",
         "contract": "APH261009P00130000", "option_type": "put",
         "strike": 130.0, "expiry": "2026-10-09",
         "position_intent": "sell_to_open", "qty": 1, "order_type": "limit",
         "limit_price": 1.2, "tif": "day", "reason": "wheel_sto_put"}


def _dispatch(returned, name="strategy_wheel"):
    lines = []

    class Lane:
        def run_once(self, *args, **kwargs):
            return dict(returned)

    ns = extract(DISPATCHER, check=("run_run_once_strategies",), namespace={
        "MODE_BACKTEST": "backtest", "MODE_LIVE": "live", "mode": "backtest",
        "os": os, "_strategy_cache": {}, "_strategy_class_cache": {name: Lane},
        "_log": lambda message, color="white": lines.append((message, color)),
        "_load_strategy_class": lambda _name: Lane,
        "_apply_regime_profile": lambda config, regime: dict(config),
        "_apply_live_overrides": lambda config: dict(config),
        "_instance_kind_and_crypto_config": lambda: ("stock", {}),
        "instance_id": "swing-paper", "backtest_row_id": "bt-1",
        "telemetry_llm_call_context": lambda **kwargs: nullcontext(),
        "get_conn": lambda: pytest.fail("model resolution should not run"),
        "resolve_model_refs_in_config": lambda conn, config: config,
        "_partial_trim_syms": lambda sizes: set(),
        "_chop_ret20_cfg": lambda config: None,
    })
    results = ns["run_run_once_strategies"](
        [{"strategy": name, "weight": 1.0, "config": {}}], ["APH"],
        {"APH": 127.0}, datetime(2026, 10, 5, 14, 30, tzinfo=timezone.utc),
        data={}, portfolio_emulator=object(), strategy_caches={},
        mode="backtest")
    assert results, f"dispatcher swallowed an error: {lines}"
    return results


def test_option_orders_ride_the_metadata_not_the_scores():
    ((_spec, scores, _reasons, metadata),) = _dispatch(
        {"APH": 0, "_nexus_option_orders": [ORDER],
         "_nexus_discovered": ["APH"]})
    assert scores == {"APH": 0}
    assert metadata["_nexus_option_orders"] == [ORDER]


def test_a_lane_without_option_orders_leaves_the_metadata_as_it_was():
    ((_spec, scores, _reasons, metadata),) = _dispatch(
        {"TQQQ": 1, "_nexus_executable_buys": ["TQQQ"]}, name="strategy_eb")
    assert "_nexus_option_orders" not in metadata
    assert metadata == {"_nexus_executable_buys": ["TQQQ"]}


def test_the_tick_merges_every_specs_option_orders():
    """A source assertion: the merge lives in the module-level main loop."""
    text = source()
    declared = text.index("nexus_option_orders: list = []")
    merged = text.index(
        'nexus_option_orders.extend(meta.get("_nexus_option_orders") or [])')
    loop = text.index("for _spec_r, _scores_r, _reasons_r, *_meta_r in "
                      "run_once_results:", declared)
    assert declared < loop < merged


def test_the_swing_and_wheel_lanes_are_registered():
    flags = module_assign("_LANE_ENABLE_FLAGS")
    assert flags["strategy_swing"] == flags["strategyswing"] == \
        "strategy_swing_enabled"
    assert flags["strategy_wheel"] == flags["strategywheel"] == \
        "strategy_wheel_enabled"


def _lanes():
    return extract(("_lane_enabled", "_truthy", "_merged_strategy_settings"),
                   assigns=("_LANE_ENABLE_FLAGS",))


@pytest.mark.parametrize("spec,lane,expected", [
    ({"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}},
     "strategy_wheel", True),
    ({"strategy": "StrategyWheel", "conditions": {"strategy_wheel_enabled": "true"},
      "config": {}}, "strategy_wheel", True),
    ({"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": "false"}},
     "strategy_wheel", False),
    ({"strategy": "strategy_swing", "config": {"strategy_swing_enabled": True}},
     "strategy_wheel", False),
    ({"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True}},
     "strategy_swing", False),
    ({"strategy": "strategy_swing", "config": {"strategy_swing_enabled": True}},
     "not_a_lane", False),
])
def test_lane_enabled(spec, lane, expected):
    assert _lanes()["_lane_enabled"]([spec], lane) is expected


def test_lane_enabled_survives_junk():
    fn = _lanes()["_lane_enabled"]
    for junk in (None, [], [None], ["strategy_wheel"], [{"strategy": None}]):
        assert fn(junk, "strategy_wheel") is False


def _limits():
    return extract(
        ("_strategy_eb_risk_limits", "_strategy_eb_single_position_pct",
         "_truthy", "_merged_strategy_settings"),
        assigns=("_LANE_ENABLE_FLAGS", "_live_risk_limits_last_reason"),
        namespace={"_log": lambda *a, **k: None})


SWING = {"strategy": "strategy_swing",
         "config": {"strategy_swing_enabled": True,
                    "honour_single_position_cap": True,
                    "broker_max_single_position_pct": 0.2}}
WHEEL = {"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}}
EB = {"strategy": "strategy_eb", "config": {"strategy_eb_enabled": True}}


def test_the_swing_lane_declares_its_live_envelope():
    limits = _limits()["_strategy_eb_risk_limits"]([SWING, WHEEL])
    assert limits == RiskLimits(max_order_fraction=0.2, max_symbol_fraction=0.2,
                                max_leveraged_fraction=0.2, soft=0.25,
                                hard=0.35, kill=0.45)


def test_a_wheel_only_document_keeps_the_module_defaults():
    assert _limits()["_strategy_eb_risk_limits"]([WHEEL]) is None


def test_eb_alone_is_unchanged_by_the_new_rows():
    fn = _limits()["_strategy_eb_risk_limits"]
    disabled_swing = {"strategy": "strategy_swing",
                      "config": {"strategy_swing_enabled": False}}
    assert fn([EB, disabled_swing]) == fn([EB])


def test_the_swing_single_position_cap_is_honoured_live():
    assert _limits()["_strategy_eb_single_position_pct"]([SWING]) == 0.2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_broker_dispatch.py -q -p no:cacheprovider`
Expected: FAIL. `test_option_orders_ride_the_metadata_not_the_scores` fails with `KeyError: '_nexus_option_orders'`, the lane tests with `AssertionError: missing from broker.py: ['_lane_enabled']`, and the registration test with `KeyError: 'strategy_swing'`.

- [ ] **Step 3: Write the minimal implementation**

In `backend/broker.py`:

(a) In `_LANE_ENABLE_FLAGS`, after the `"strategy_hx": ..., "strategyhx": ...` line, add:

```python
    # swing-port: the swing lane widens the envelope to its 20% order/symbol
    # caps and honours its 20% single-position cap live; the wheel lane
    # declares no live_* keys and is skipped exactly as HX is. Both have rows
    # in `_strategy_eb_risk_limits`' defaults_by_lane (D5).
    "strategy_swing": "strategy_swing_enabled", "strategyswing": "strategy_swing_enabled",
    "strategy_wheel": "strategy_wheel_enabled", "strategywheel": "strategy_wheel_enabled",
```

(b) Directly before `def _strategy_eb_risk_limits(cached_strategies):` (that is, right after `_strategy_eb_single_position_pct` ends), add:

```python
def _lane_enabled(cached_strategies, lane) -> bool:
    """True when the document carries an ENABLED spec for ``lane`` (a key of
    ``_LANE_ENABLE_FLAGS``), with the strategies' own enabled-flag semantics
    and `conditions` UNION `config`. Every swing-port branch in the live loop
    is gated on this, so a document without the lane (doc 200) never enters
    one."""
    flag = _LANE_ENABLE_FLAGS.get(str(lane or "").strip().lower())
    if flag is None:
        return False
    for spec in (cached_strategies or []):
        try:
            name = str((spec or {}).get("strategy", "")).strip().lower()
            if _LANE_ENABLE_FLAGS.get(name) != flag:
                continue
            if _truthy(_merged_strategy_settings(spec).get(flag, False)):
                return True
        except Exception:
            continue
    return False
```

(c) In `_strategy_eb_risk_limits`, replace:

```python
    defaults_by_lane = {"strategy_eb": _EB_DEFAULTS, "strategyeb": _EB_DEFAULTS,
                        "outlier_sleeve": _OS_DEFAULTS, "outliersleeve": _OS_DEFAULTS,
                        "strategy_hx": _HX_DEFAULTS, "strategyhx": _HX_DEFAULTS}
```

with:

```python
    # swing-port: the swing lane's live envelope (spec section 5.1): 20% per
    # order and per symbol, EB's drawdown rungs. Inline, not a module
    # constant, because the envelope tests extract this function with only
    # the tables it already reads. The wheel declares none (like HX).
    _SW_DEFAULTS = {
        "live_max_order_fraction": 0.2, "live_max_symbol_fraction": 0.2,
        "live_max_leveraged_fraction": 0.2, "live_soft_drawdown": 0.25,
        "live_hard_drawdown": 0.35, "live_kill_drawdown": 0.45,
    }
    defaults_by_lane = {"strategy_eb": _EB_DEFAULTS, "strategyeb": _EB_DEFAULTS,
                        "outlier_sleeve": _OS_DEFAULTS, "outliersleeve": _OS_DEFAULTS,
                        "strategy_hx": _HX_DEFAULTS, "strategyhx": _HX_DEFAULTS,
                        "strategy_swing": _SW_DEFAULTS, "strategyswing": _SW_DEFAULTS,
                        "strategy_wheel": {}, "strategywheel": {}}
```

(d) In `run_run_once_strategies`, directly after

```python
                nexus_executable_buys = raw.pop("_nexus_executable_buys", [])
```

insert:

```python
                # swing-port: the wheel lane's option orders ride their own
                # side channel (interfaces doc section 1). Popped so it is
                # never mistaken for a ticker; carried only when non-empty,
                # so EB's metadata is exactly what it was.
                nexus_option_orders = raw.pop("_nexus_option_orders", [])
                if nexus_option_orders:
                    metadata["_nexus_option_orders"] = list(nexus_option_orders)
```

(e) In the per-tick metadata extraction (main loop), replace:

```python
            nexus_max_positions = None
            for _spec_r, _scores_r, _reasons_r, *_meta_r in run_once_results:
                meta = _meta_r[0] if _meta_r else {}
                _nmp = meta.get("_nexus_max_positions")
```

with:

```python
            nexus_max_positions = None
            # swing-port (spec 6.1, broker item 1): every spec's option orders,
            # executed after the stock loop. Empty on every EB tick.
            nexus_option_orders: list = []
            for _spec_r, _scores_r, _reasons_r, *_meta_r in run_once_results:
                meta = _meta_r[0] if _meta_r else {}
                _nmp = meta.get("_nexus_max_positions")
```

and, in the same loop, directly after

```python
                for sym, intent in (meta.get("_nexus_action_intents") or {}).items():
                    nexus_action_intents_merged[sym] = intent
```

insert:

```python
                nexus_option_orders.extend(meta.get("_nexus_option_orders") or [])
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_broker_dispatch.py backend/tests/test_live_risk_limits.py backend/tests/test_strategy_hx_broker_wiring.py backend/tests/test_strategy_x_broker_coexistence.py -q -p no:cacheprovider`
Expected: all pass. `test_every_registered_lane_has_a_defaults_row` covers the four new names.

- [ ] **Step 5: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()`. broker.py is unindexed, so confirm with `git diff --stat` that only `broker.py` and the two new test files changed, and that `git diff backend/broker.py` touches only these five places.

```bash
git add backend/broker.py backend/tests/swing_broker_harness.py backend/tests/test_swing_broker_dispatch.py
git commit -F - <<'EOF'
feat(broker): option-order side channel and swing/wheel lane registration

run_run_once_strategies pops _nexus_option_orders into the metadata only
when a lane emits them, and the tick concatenates them across specs.
strategy_swing and strategy_wheel join the lane registry with
defaults_by_lane rows (swing: 20 percent order and symbol caps, EB's
drawdown rungs; wheel: none). Adds _lane_enabled, the gate every
swing-port live branch uses, and an AST harness that refuses an
extracted function whose free names the test does not provide.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 9: broker.py bracket entries, leg cancel before a sell, leg registration wiring

**Files:**
- Modify: `backend/broker.py`:
  - `_build_strategy_stock_intent` (9847-9958): new keyword and an early branch
  - add `_build_bracket_intent` and `_cancel_bracket_legs_confirmed` after it
  - `_reconcile_alpaca_ownership` (9431-9484)
  - live service construction (11207-11226)
  - live submit block (18858-18930): the leg-cancel guard and the `bracket=` keyword at the `_build_strategy_stock_intent(` call
- Test: `backend/tests/test_swing_broker_bracket.py` (create)

**Interfaces:**
- Consumes:
  - Task 1 bracket fields
  - Task 3 `AlpacaAdapter.get_order_with_legs` and `cancel_orders_confirmed`
  - Task 2 `is_bracket_child_order`
  - Task 7 `LiveOrderService(legs_lookup=...)`, `ensure_bracket_legs` and `register_bracket_legs`
  - Task 8 `_lane_enabled`
  - the strategy hint `nexus_position_sizes[sym]["bracket"] = {"take_profit_price", "stop_loss_price"}` (interfaces doc section 1)
- Produces:
  - `_build_strategy_stock_intent(..., bracket=None)`
  - `_build_bracket_intent(order_service, *, symbol, price, decision_at, bracket, risk_snapshot_id, quote_at, cash_to_use=None, quantity=None, action_intents=(), source=None, reason=None) -> OrderIntent`
  - `_cancel_bracket_legs_confirmed(adapter, order_service, symbol, *, timeout_s=10.0, log=None) -> bool`

- [ ] **Step 0: Impact analysis (grep; broker.py is unindexed)**

```bash
grep -n "_build_strategy_stock_intent\|_reconcile_alpaca_ownership\|lookup_by_client_id=live_adapter\|if _is_alpaca_stock_gate:" backend/broker.py
python3 -c "s=open('backend/broker.py').read(); b=s.split('def _build_strategy_stock_intent(',1)[1]; print(b.find('raise ValueError(\"computed order quantity <= 0\")'))"
```

Every EB live order passes through `_build_strategy_stock_intent` and the submit block. **Risk: HIGH.** The EB source-window invariants in the Global Constraints apply; the second command must print a value below 3000 after this task.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_broker_bracket.py`:

```python
"""swing-port Task 9: bracket entries through the strategy intent builder,
cancelling a position's bracket legs before a sell, and the wiring that
registers legs. The EB key pin was computed from the PRE-change code."""
import datetime as datetime_module
from decimal import Decimal
from types import SimpleNamespace

import pytest

from live_orders import (
    InMemoryLifecycleBackend,
    LiveOrderService,
    OrderLifecycleStore,
)
from swing_broker_harness import extract, function_source, source
from swing_live_fixtures import RTH, bracket_intent, equity_snapshot, leg_ref, parent_ref

EB_BUY_KEY = "alpacama-26078e450b92fad1c9e11db27d9425f41505f-0"
BUY_AT = datetime_module.datetime(2026, 9, 24, 13, 31, 5, 123456,
                                  tzinfo=datetime_module.timezone.utc)


def _builders():
    return extract(("_build_strategy_stock_intent", "_build_bracket_intent"),
                   namespace={"datetime": datetime_module})


class _NoStylePortfolio:
    """A bracket must never ask for the extended-hours style."""
    _positions = {"AAPL": 5}

    def _order_style_for_now(self, *args, **kwargs):
        raise AssertionError("bracket entry asked for _order_style_for_now")


EB_SERVICE = SimpleNamespace(account_id="brk-alpaca-main",
                             instance_id="alpaca-main")
PAPER = SimpleNamespace(account_id="acct-1", instance_id="instance-1")


def test_the_eb_intent_is_unchanged_without_a_bracket():
    build = _builders()["_build_strategy_stock_intent"]
    order = build(
        EB_SERVICE, SimpleNamespace(_positions={}), symbol="TQQQ", decision=1,
        price=88.12, current_time=BUY_AT, cash_to_use=1087.93,
        sell_fraction=1.0, action_intents={"eb_rebalance"},
        is_risk_exit=False,
        risk_snapshot_id="risk-state:41:2026-09-24T13:31:00+00:00",
        quote_at=BUY_AT)
    assert order.idempotency_key == EB_BUY_KEY
    assert (order.order_class, order.tif, order.asset_class) == (
        None, "day", "us_equity")
    assert order.quantity == (Decimal("1087.93") / Decimal("88.12")).quantize(
        Decimal("0.00000001"))


def test_a_bracket_hint_builds_a_whole_share_gtc_market_bracket():
    build = _builders()["_build_strategy_stock_intent"]
    order = build(
        PAPER, _NoStylePortfolio(), symbol="AAPL", decision=1, price=200.5,
        current_time=RTH, cash_to_use=1250, sell_fraction=1.0,
        action_intents={"swing_entry"}, is_risk_exit=False,
        risk_snapshot_id="risk-1", quote_at=RTH,
        bracket={"take_profit_price": 218.555, "stop_loss_price": 188.0})
    assert order.quantity == Decimal("6")
    assert (order.order_class, order.tif, order.order_type,
            order.extended_hours) == ("bracket", "gtc", "market", False)
    assert order.take_profit_price == Decimal("218.56")
    assert order.stop_loss_price == Decimal("188.00")
    assert order.reason == "swing_entry"
    assert order.reference_price == Decimal("200.5")


def test_a_bracket_that_floors_to_zero_shares_is_a_definite_refusal():
    build = _builders()["_build_strategy_stock_intent"]
    with pytest.raises(ValueError, match=r"^computed order quantity <= 0$"):
        build(PAPER, _NoStylePortfolio(), symbol="AAPL", decision=1,
              price=200.0, current_time=RTH, cash_to_use=150,
              sell_fraction=1.0, action_intents=set(), is_risk_exit=False,
              risk_snapshot_id="risk-1", quote_at=RTH,
              bracket={"take_profit_price": 218, "stop_loss_price": 188})


@pytest.mark.parametrize("bracket", [
    {"take_profit_price": 218, "stop_loss_price": 205},
    {"take_profit_price": 199, "stop_loss_price": 188},
    {"take_profit_price": 218},
])
def test_legs_that_do_not_straddle_the_price_are_refused(bracket):
    build = _builders()["_build_bracket_intent"]
    with pytest.raises(ValueError, match="bracket refused"):
        build(PAPER, symbol="AAPL", price=200.0, decision_at=RTH,
              cash_to_use=2000, bracket=bracket, risk_snapshot_id="risk-1",
              quote_at=RTH)


def test_a_sell_ignores_a_bracket_hint():
    build = _builders()["_build_strategy_stock_intent"]
    order = build(
        PAPER, SimpleNamespace(_positions={"AAPL": 5}), symbol="AAPL",
        decision=-1, price=200.0, current_time=RTH, cash_to_use=0,
        sell_fraction=1.0, action_intents={"swing_rsi_exit"},
        is_risk_exit=False, risk_snapshot_id="risk-1", quote_at=RTH,
        bracket={"take_profit_price": 218, "stop_loss_price": 188})
    assert order.order_class is None and order.quantity == Decimal("5")


def test_the_quantity_refusal_stays_inside_the_eb_source_window():
    body = source().split("def _build_strategy_stock_intent(", 1)[1][:3000]
    assert 'raise ValueError("computed order quantity <= 0")' in body


# --- cancelling bracket legs before a sell ------------------------------------

def _cancel():
    return extract(("_cancel_bracket_legs_confirmed",))[
        "_cancel_bracket_legs_confirmed"]


def _service_with_legs():
    order = bracket_intent()
    store = OrderLifecycleStore(InMemoryLifecycleBackend())
    service = LiveOrderService(
        account_id=order.account_id, instance_id=order.instance_id,
        snapshot_provider=equity_snapshot,
        transport=lambda **kw: parent_ref(order, status="filled",
                                          filled_qty="5",
                                          filled_avg_price="100"),
        lifecycle_store=store,
        legs_lookup=lambda _id: SimpleNamespace(legs=(
            leg_ref("leg-tp", "limit", status="new"),
            leg_ref("leg-sl", "stop"))))
    assert service.submit(order).accepted
    return service


class _Adapter:
    def __init__(self, *, working=(), confirmed=True, readable=True):
        self.working = list(working)
        self.confirmed = confirmed
        self.readable = readable
        self.cancel_calls = []

    def list_open_orders_strict(self, limit=200):
        if not self.readable:
            raise RuntimeError("orders endpoint unreachable")
        return list(self.working)

    def cancel_orders_confirmed(self, order_ids, timeout_s=10.0):
        self.cancel_calls.append((list(order_ids), timeout_s))
        return self.confirmed


def test_nothing_to_cancel_lets_the_sell_go_without_a_cancel():
    adapter = _Adapter()
    empty = SimpleNamespace(
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        instance_id="instance-1")
    assert _cancel()(adapter, empty, "AAPL") is True
    assert adapter.cancel_calls == []


def test_store_legs_and_broker_legs_are_all_cancelled():
    service = _service_with_legs()
    stray = SimpleNamespace(broker_order_id="broker-stray", symbol="AAPL",
                            side="sell", status="new", order_class="bracket")
    other = SimpleNamespace(broker_order_id="broker-msft", symbol="MSFT",
                            side="sell", status="new", order_class="bracket")
    adapter = _Adapter(working=[stray, other])
    assert _cancel()(adapter, service, "aapl", timeout_s=10.0) is True
    ((ids, timeout),) = adapter.cancel_calls
    assert sorted(ids) == ["broker-leg-sl", "broker-leg-tp", "broker-stray"]
    assert timeout == 10.0


def test_unconfirmed_leg_cancel_defers_the_sell():
    """Review Focus 3, the broker half."""
    service = _service_with_legs()
    assert _cancel()(_Adapter(confirmed=False), service, "AAPL") is False


def test_an_unreadable_book_defers_the_sell_without_cancelling():
    service = _service_with_legs()
    adapter = _Adapter(readable=False)
    assert _cancel()(adapter, service, "AAPL") is False
    assert adapter.cancel_calls == []


# --- wiring (source assertions: the loop and boot are module-level code) --------

def test_the_submit_block_cancels_legs_before_building_a_sell():
    text = source()
    gate = text.index("if _is_alpaca_stock_gate:")
    guard = text.index("_cancel_bracket_legs_confirmed(", gate)
    build = text.index("_build_strategy_stock_intent(", gate)
    assert guard < build
    window = text[gate:build]
    assert "decision == -1" in window
    assert '_lane_enabled(_cached_strategies, "strategy_swing")' in window
    assert 'f"order deferred: {symbol} bracket ' in window


def test_the_call_site_feeds_the_bracket_hint_for_buys_only():
    text = source()
    call = text.split("_build_strategy_stock_intent(\n", 2)[2][:2500]
    assert 'nexus_hint.get("bracket")' in call
    assert "if decision == 1" in call


def test_the_live_service_reads_legs_back_and_reconcile_retries_them():
    text = source()
    assert "legs_lookup=live_adapter.get_order_with_legs," in text
    reconcile = function_source("_reconcile_alpaca_ownership")
    assert "order_service.ensure_bracket_legs()" in reconcile
    assert '"strategy_swing"' in reconcile
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_broker_bracket.py -q -p no:cacheprovider`
Expected: FAIL. `AssertionError: missing from broker.py: ['_build_bracket_intent']`, then `['_cancel_bracket_legs_confirmed']`, and the source assertions fail. `test_the_eb_intent_is_unchanged_without_a_bracket` also errors in extraction until `_build_bracket_intent` exists.

- [ ] **Step 3: Write the minimal implementation**

In `backend/broker.py`:

(a) Change the `_build_strategy_stock_intent` signature's last line from `        risk_snapshot_id, quote_at):` to:

```python
        risk_snapshot_id, quote_at, bracket=None):
```

Directly after `    from live_orders import OrderIntent, OrderSide, OrderSource` (the first line of its body after the docstring), insert:

```python
    if bracket is not None and decision == 1:
        # swing-port (spec 6.1 broker item 3): a bracket entry.
        return _build_bracket_intent(
            order_service, symbol=symbol, price=price,
            decision_at=current_time, cash_to_use=cash_to_use,
            bracket=bracket, action_intents=action_intents,
            risk_snapshot_id=risk_snapshot_id, quote_at=quote_at)
```

(b) Directly after `_build_strategy_stock_intent` (its last line is `        reference_price=Decimal(str(price)),\n    )`), add:

```python
def _build_bracket_intent(
        order_service, *, symbol, price, decision_at, bracket,
        risk_snapshot_id, quote_at, cash_to_use=None, quantity=None,
        action_intents=(), source=None, reason=None):
    """One swing entry as an Alpaca GTC market bracket (spec section 6.1,
    broker item 3): whole shares, tif=gtc, order_class=bracket, and the
    absolute leg prices the lane anchored on the prior close.

    It never calls _order_style_for_now. That conversion would turn a 09:15 ET
    entry into an extended-hours limit DAY order; a plain market bracket is
    what Alpaca queues for the open, which is ST's behaviour.
    """
    from decimal import ROUND_FLOOR, Decimal
    from live_orders import OrderIntent, OrderSide, OrderSource

    if not isinstance(decision_at, datetime.datetime):
        decision_at = datetime.datetime.now(datetime.timezone.utc)
    elif decision_at.tzinfo is None:
        decision_at = decision_at.replace(tzinfo=datetime.timezone.utc)
    price_d = Decimal(str(price))
    if quantity is not None:
        shares = Decimal(int(quantity))
    elif price_d > 0:
        shares = (Decimal(str(cash_to_use or 0)) / price_d).to_integral_value(
            rounding=ROUND_FLOOR)
    else:
        shares = Decimal("0")
    if shares <= 0:
        raise ValueError("computed order quantity <= 0")
    legs = bracket if isinstance(bracket, dict) else {}
    if legs.get("take_profit_price") is None or legs.get("stop_loss_price") is None:
        raise ValueError(f"bracket refused: {symbol} hint lacks a leg price")
    cent = Decimal("0.01")
    take_profit = Decimal(str(legs["take_profit_price"])).quantize(cent)
    stop_loss = Decimal(str(legs["stop_loss_price"])).quantize(cent)
    if not Decimal("0") < stop_loss < price_d < take_profit:
        raise ValueError(
            f"bracket refused: stop {stop_loss} and target {take_profit} do "
            f"not straddle {price_d} for {symbol}")
    return OrderIntent(
        account_id=order_service.account_id,
        instance_id=order_service.instance_id,
        source=source or OrderSource.STRATEGY,
        reason=reason or (
            ",".join(sorted(action_intents)) if action_intents
            else "strategy signal"),
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=shares,
        reduce_only=False,
        decision_at=decision_at,
        quote_at=quote_at,
        risk_snapshot_id=risk_snapshot_id,
        order_type="market",
        limit_price=None,
        tif="gtc",
        extended_hours=False,
        reference_price=price_d,
        order_class="bracket",
        take_profit_price=take_profit,
        stop_loss_price=stop_loss,
    )


def _cancel_bracket_legs_confirmed(adapter, order_service, symbol, *,
                                   timeout_s=10.0, log=None):
    """Cancel every working bracket leg on ``symbol`` and wait for Alpaca to
    confirm (spec 6.1 broker item 4). True when nothing is left working and the
    sell may go; False defers the sell a tick.

    Leg ids come from two places, because each can miss one: the lifecycle
    store (a held stop leg is not always listed as open) and the broker's
    open orders (a leg whose registration has not happened yet).
    """
    from broker_adapters.base import is_bracket_child_order
    from live_orders import OrderSource

    def say(message, color="yellow"):
        if log is not None:
            try:
                log(message, color)
            except Exception:
                pass

    wanted = str(symbol or "").strip().upper()
    leg_ids = []
    try:
        for record in order_service.lifecycle_store.list_for_instance(
                order_service.instance_id):
            if record.terminal or record.intent.source is not OrderSource.BRACKET_LEG:
                continue
            if record.intent.symbol == wanted and record.broker_order_id:
                leg_ids.append(record.broker_order_id)
    except Exception as exc:
        say(f"[swing] {wanted} sell deferred: the lifecycle store is "
            f"unreadable ({type(exc).__name__}: {exc}), so its bracket legs "
            "cannot be ruled out", "red")
        return False
    try:
        working = adapter.list_open_orders_strict()
    except Exception as exc:
        say(f"[swing] {wanted} sell deferred: the open-order book is "
            f"unreachable ({type(exc).__name__}: {exc})", "red")
        return False
    for ref in (working or ()):
        ref_id = str(getattr(ref, "broker_order_id", "") or "")
        if (ref_id and ref_id not in leg_ids and is_bracket_child_order(ref)
                and str(getattr(ref, "symbol", "") or "").strip().upper() == wanted):
            leg_ids.append(ref_id)
    if not leg_ids:
        return True
    try:
        confirmed = bool(adapter.cancel_orders_confirmed(leg_ids, timeout_s=timeout_s))
    except Exception as exc:
        say(f"[swing] {wanted} bracket leg cancel raised "
            f"{type(exc).__name__}: {exc}", "red")
        return False
    say(f"[swing] {wanted} bracket legs {leg_ids} cancel "
        f"{'confirmed' if confirmed else 'NOT confirmed; the sell waits a tick'}",
        "cyan" if confirmed else "yellow")
    return confirmed
```

(c) In `_reconcile_alpaca_ownership`, directly before `        result = StartupReconciler(`, insert:

```python
        # swing-port: re-read any bracket whose legs are not recorded yet (a
        # nested read that failed after submit, or a restart), so a leg fill
        # resolves before ownership is judged. Swing documents only.
        if _lane_enabled(globals().get("_cached_strategies"), "strategy_swing"):
            try:
                order_service.ensure_bracket_legs()
            except Exception as _legs_exc:
                _log(f"[swing] bracket leg registration failed "
                     f"({type(_legs_exc).__name__}: {_legs_exc}); retried next "
                     "reconcile", "yellow")
```

(d) In the live service construction, directly after `                    lookup_by_client_id=live_adapter.get_order_by_client_id,`, add:

```python
                    # swing-port: a bracket parent's legs are read back with
                    # nested=True and become lifecycle rows. Only an intent
                    # with order_class="bracket" ever calls it.
                    legs_lookup=live_adapter.get_order_with_legs,
```

(e) In the live submit block, directly after the line `                                    if _is_alpaca_stock_gate:`, insert (keep the indentation):

```python
                                        # swing-port (spec 6.1 broker item 4):
                                        # a SELL of a bracketed position first
                                        # cancels its legs and waits for Alpaca
                                        # to confirm; unconfirmed, the sell
                                        # waits a tick. Only a document with an
                                        # enabled swing lane holds legs.
                                        if (
                                            decision == -1
                                            and _lane_enabled(_cached_strategies, "strategy_swing")
                                            and not _cancel_bracket_legs_confirmed(
                                                live_adapter,
                                                _live_stock_order_service,
                                                symbol,
                                                timeout_s=10.0,
                                                log=_log,
                                            )
                                        ):
                                            raise ValueError(
                                                f"order deferred: {symbol} bracket "
                                                "legs did not confirm cancelled "
                                                "within 10s; the sell waits a tick"
                                            )
```

In the same block, in the `_build_strategy_stock_intent(` call, directly after

```python
                                                quote_at=(
                                                    _authoritative_quote_at
                                                ),
```

add:

```python
                                                bracket=(
                                                    nexus_hint.get("bracket")
                                                    if decision == 1
                                                    and isinstance(nexus_hint, dict)
                                                    else None
                                                ),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_broker_bracket.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Run the EB source-window and manual-gate suites**

Run: `python3 -m pytest backend/tests/test_strategy_eb_live_submit.py backend/tests/test_manual_order_gate.py backend/tests/test_live_dependency_freshness.py backend/tests/test_strategy_eb_pending_guard.py backend/tests/test_strategy_eb_broker_wiring.py backend/tests/test_swing_broker_dispatch.py -q -p no:cacheprovider`
Expected: all pass. If `test_the_re_arm_policy_reaches_all_three_call_sites` fails, code was inserted after the first `_submission.accepted`. Move it above `_stock_intent = (`.

- [ ] **Step 6: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()` and `git diff --stat`. Only `broker.py` and the new test file may change.

```bash
git add backend/broker.py backend/tests/test_swing_broker_bracket.py
git commit -F - <<'EOF'
feat(broker): GTC bracket entries and confirmed leg cancels before a sell

A swing entry whose sizing hint carries a bracket becomes a whole-share
GTC market bracket with absolute leg prices, built without the
extended-hours conversion so a 09:15 ET entry is queued for the open.
On a document with an enabled swing lane, a sell first cancels the
symbol's bracket legs and waits up to 10 s for Alpaca to confirm; an
unconfirmed cancel defers the sell a tick. The live service reads legs
back after submission and the reconcile pass retries missing ones.
EB's intent key and the source windows its tests pin are unchanged.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 10: broker.py option execution and the option dependency snapshot

**Files:**
- Modify: `backend/broker.py`:
  - `_live_order_dependency_snapshot` (9738): a first-line dispatch
  - new module state and functions directly before `def _live_order_dependency_snapshot(` (9738)
  - main loop: the call site immediately before `## Save portfolio snapshot every loop` (19239)
- Test: `backend/tests/test_swing_broker_options.py` (create)

**Interfaces:**
- Consumes:
  - Task 1 option fields and the `DependencySnapshot` option fields
  - Task 4 `get_option_snapshots` (`OptionSnapshotDTO`)
  - Task 5 `_option_positions` and `_option_positions_complete`
  - Task 6 gate options branch
  - Task 8 `nexus_option_orders` and `_lane_enabled`
- Produces these broker.py names:
  - `_live_option_quotes: dict`
  - `_refresh_option_quote(adapter, contract, now_utc, *, max_age_s=60.0) -> Optional[dict]`
  - `_build_option_intent(order_service, order, *, quote_at, decision_at, risk_snapshot_id, source=None) -> OrderIntent`
  - `_pending_sell_to_open_collateral(order_service, *, exclude_key, underlying) -> tuple[Decimal, Decimal]`
  - `_live_option_dependency_snapshot(adapter, intent, *, now_utc=None) -> DependencySnapshot`
  - `_execute_option_intents(option_orders, *, order_service, adapter, now_utc, risk_snapshot_id, refused_reason="", log=None) -> list[dict]`

  Each dict `_execute_option_intents` returns holds `{"contract", "position_intent", "signal_id", "status", "reason_codes", "client_order_id"}`, where status is one of `submitted`, `blocked`, `uncertain`, `refused`, `invalid`, `no_quote` or `error`.

- [ ] **Step 0: Impact analysis (grep)**

```bash
grep -n "def _live_order_dependency_snapshot\|_live_order_dependency_snapshot(\|## Save portfolio snapshot every loop" backend/broker.py
```

`_live_order_dependency_snapshot` is the snapshot provider for every EB order (`broker.py:11210`). **Risk: HIGH.** Its only change is a first-line dispatch on `asset_class`, which EB intents never set. `test_live_dependency_freshness.py` pins its source (no `intent.reference_price`, no `_last_prices`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_broker_options.py`:

```python
"""swing-port Task 10: the wheel lane's option orders go through
LiveOrderService with a REST options quote and an options-aware dependency
snapshot. Spec section 6.1 broker item 2; section 9 fixes 1, 3 and 8."""
import datetime as datetime_module
import sys
import threading
import types
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from broker_adapters.base import OptionPositionDTO, OptionSnapshotDTO
from live_orders import (
    DependencySnapshot,
    Health,
    InMemoryLifecycleBackend,
    LiveOrderService,
    OrderLifecycleStore,
    OrderSide,
    OrderSource,
    UnifiedOrderGate,
)
from live_order_task8_helpers import broker_ref, intent as equity_intent
from swing_broker_harness import extract, source
from swing_live_fixtures import OCC, RTH, option_intent, option_snapshot

ORDER = {"signal_id": None, "underlying": "APH", "contract": OCC,
         "option_type": "put", "strike": 130.0, "expiry": "2026-10-09",
         "position_intent": "sell_to_open", "qty": 1, "order_type": "limit",
         "limit_price": 1.2, "tif": "day", "reason": "wheel_sto_put"}


def _snap(symbol, *, bid=1.1, ask=1.3, last=1.2, stamp=RTH):
    return OptionSnapshotDTO(symbol, bid, ask, last, 0.3, -0.25, 0.05, -0.04,
                             0.1, stamp.isoformat() if stamp else None)


class _Quotes:
    def __init__(self, **overrides):
        self.overrides = overrides
        self.calls = []

    def get_option_snapshots(self, contracts):
        self.calls.append(list(contracts))
        return {c: _snap(c, **self.overrides) for c in contracts}


def _executor():
    ns = extract(("_execute_option_intents", "_build_option_intent",
                  "_refresh_option_quote"),
                 assigns=("_live_option_quotes",),
                 namespace={"datetime": datetime_module})
    return ns


def _service(provider=option_snapshot):
    calls = []
    order = option_intent()
    service = LiveOrderService(
        account_id=order.account_id, instance_id=order.instance_id,
        snapshot_provider=provider,
        transport=lambda **kw: calls.append(kw) or SimpleNamespace(
            status="accepted", broker_order_id=f"b-{len(calls)}",
            id=f"b-{len(calls)}", filled_qty=0, filled_avg_price=None),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    return service, calls


def _lines():
    lines = []
    return lines, lambda message, color="white": lines.append((message, color))


def test_a_sell_to_open_goes_through_the_service_with_a_rest_quote():
    ns = _executor()
    service, calls = _service()
    lines, log = _lines()
    results = ns["_execute_option_intents"](
        [ORDER], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", log=log)
    assert results[0]["status"] == "submitted", (results, lines)
    assert calls[0]["asset_class"] == "us_option"
    assert calls[0]["position_intent"] == "sell_to_open"
    assert (calls[0]["symbol"], calls[0]["qty"], calls[0]["limit_price"]) == (
        OCC, 1.0, 1.2)
    assert calls[0]["client_order_id"] == results[0]["client_order_id"]
    assert ns["_live_option_quotes"][OCC]["price"] == Decimal("1.2")


def test_a_rerun_minutes_later_is_deduped_not_resold():
    """Spec section 9 fix 1, end to end: one put per contract per session."""
    ns = _executor()
    service, calls = _service()
    execute = ns["_execute_option_intents"]
    execute([ORDER], order_service=service, adapter=_Quotes(), now_utc=RTH,
            risk_snapshot_id="risk-1")
    later = RTH + timedelta(minutes=7)
    rerun = execute([dict(ORDER, qty=2, limit_price=1.1)],
                    order_service=service,
                    adapter=_Quotes(stamp=later), now_utc=later,
                    risk_snapshot_id="risk-1")
    assert len(calls) == 1
    assert rerun[0]["status"] == "blocked"
    assert "idempotency.open_order_exists" in rerun[0]["reason_codes"]


def test_a_refused_lane_places_nothing():
    ns = _executor()
    service, calls = _service()
    lines, log = _lines()
    results = ns["_execute_option_intents"](
        [ORDER], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", refused_reason="options_trading_level=0",
        log=log)
    assert results[0]["status"] == "refused" and calls == []
    assert any(color == "red" and "options_trading_level=0" in message
               for message, color in lines)


@pytest.mark.parametrize("bad", [
    dict(ORDER, qty=1.5), dict(ORDER, qty=True), dict(ORDER, qty=0),
    dict(ORDER, position_intent="sell_short"), dict(ORDER, expiry="soon"),
    "not-a-dict",
])
def test_a_malformed_order_is_dropped_loudly(bad):
    ns = _executor()
    service, calls = _service()
    lines, log = _lines()
    results = ns["_execute_option_intents"](
        [bad], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", log=log)
    assert results[0]["status"] in ("invalid", "no_quote") and calls == []
    assert any(color == "red" for _message, color in lines)


def test_no_usable_quote_places_nothing():
    ns = _executor()
    service, calls = _service()
    results = ns["_execute_option_intents"](
        [ORDER], order_service=service,
        adapter=_Quotes(bid=None, ask=None, last=None), now_utc=RTH,
        risk_snapshot_id="risk-1")
    assert results[0]["status"] == "no_quote" and calls == []


def test_a_gate_refusal_is_logged_with_its_reasons():
    ns = _executor()
    service, calls = _service(
        provider=lambda cur: option_snapshot(cur, available_cash=Decimal("100")))
    lines, log = _lines()
    results = ns["_execute_option_intents"](
        [ORDER], order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1", log=log)
    assert results[0]["status"] == "blocked" and calls == []
    assert "option.collateral_insufficient" in results[0]["reason_codes"]
    assert any("ORDER GATE BLOCKED" in m and "option.collateral_insufficient"
               in m for m, c in lines if c == "red")


def test_signal_ids_are_written_back(monkeypatch):
    updates = []
    store = types.ModuleType("swing_trader.signals_store")
    store.update_signal = lambda signal_id, patch: updates.append(
        (signal_id, patch))
    package = types.ModuleType("swing_trader")
    package.__path__ = []
    package.signals_store = store
    monkeypatch.setitem(sys.modules, "swing_trader", package)
    monkeypatch.setitem(sys.modules, "swing_trader.signals_store", store)
    ns = _executor()
    service, _calls = _service()
    results = ns["_execute_option_intents"](
        [dict(ORDER, signal_id="sig-1"),
         dict(ORDER, signal_id="sig-2", contract="APH261009P00125000",
              strike=125.0, qty=1.5)],
        order_service=service, adapter=_Quotes(), now_utc=RTH,
        risk_snapshot_id="risk-1")
    assert updates[0] == ("sig-1", {"status": "submitted",
                                    "order_client_id": results[0]["client_order_id"]})
    assert updates[1] == ("sig-2", {"status": "failed", "order_client_id": None})


def test_a_quote_is_reused_for_60_seconds_then_refreshed():
    ns = _executor()
    refresh = ns["_refresh_option_quote"]
    adapter = _Quotes()
    first = refresh(adapter, OCC, RTH)
    assert refresh(adapter, OCC, RTH + timedelta(seconds=60)) is first
    refresh(adapter, OCC, RTH + timedelta(seconds=61))
    assert len(adapter.calls) == 2
    assert first["price"] == Decimal("1.2") and first["quote_at"] == RTH


def test_a_one_sided_book_falls_back_to_the_last_trade():
    ns = _executor()
    quote = ns["_refresh_option_quote"](_Quotes(bid=None, last=0.95), OCC, RTH)
    assert quote["price"] == Decimal("0.95")


def test_buy_to_close_intents_are_risk_exits():
    ns = _executor()
    service, _calls = _service()
    order = ns["_build_option_intent"](
        service, dict(ORDER, position_intent="buy_to_close",
                      order_type="market", limit_price=None,
                      reason="wheel_btc_itm"),
        quote_at=RTH, decision_at=RTH, risk_snapshot_id="risk-1")
    assert (order.side, order.reduce_only, order.source, order.limit_price,
            order.contract_multiplier) == (OrderSide.BUY, True,
                                           OrderSource.RISK_EXIT, None, 100)


# --- the options dependency snapshot ------------------------------------------

def _position(symbol, underlying, strike, qty=-1):
    return OptionPositionDTO(symbol, underlying, "put", strike, "2026-10-09",
                             qty, 1.2, 1.1, -110.0, 10.0)


def _snapshot_ns(service, *, state=None):
    now = RTH
    base = {name: "healthy" for name in (
        "kill_switch", "cash", "positions", "persistence", "risk_state",
        "watchdog")}
    base.update({f"{name}_at": now for name in (
        "kill_switch", "cash", "positions", "persistence", "risk_state",
        "watchdog")})
    base["risk_snapshot_id"] = "risk-1"
    base.update(state or {})
    return extract(
        ("_live_option_dependency_snapshot", "_pending_sell_to_open_collateral",
         "_open_order_idempotency_keys"),
        assigns=("_live_option_quotes",),
        namespace={
            "datetime": datetime_module,
            "_live_order_dependency_lock": threading.Lock(),
            "_live_order_dependency_state": base,
            "_live_stock_order_service": service,
            "_live_risk_state": SimpleNamespace(
                max_order_notional=Decimal("10000")),
            "instance_id": "instance-1", "MODE_LIVE": "live", "mode": "live",
            "live_broker_type": "alpaca", "live_brokerage_id": "acct-1",
        })


def _adapter(**changes):
    values = dict(
        _option_positions={
            "APH261009P00130000": _position("APH261009P00130000", "APH", 130.0),
            "MSFT261009P00400000": _position("MSFT261009P00400000", "MSFT", 400.0),
        },
        _option_positions_complete=True, _cash=50000.0,
        _account_equity=100000.0, _instance_id="instance-1",
        _positions_stale_since=None)
    values.update(changes)
    return SimpleNamespace(**values)


def _pending_service():
    pending = option_intent(symbol="APH261009P00125000", strike=Decimal("125"))
    service = LiveOrderService(
        account_id=pending.account_id, instance_id=pending.instance_id,
        snapshot_provider=option_snapshot,
        transport=lambda **kw: broker_ref(pending),
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()))
    assert service.submit(pending).accepted
    return service


def test_the_option_snapshot_counts_open_and_pending_put_collateral():
    service = _pending_service()
    ns = _snapshot_ns(service)
    order = option_intent(symbol="APH261009P00120000", strike=Decimal("120"))
    ns["_live_option_quotes"][order.symbol] = {
        "price": Decimal("1.10"), "quote_at": RTH, "fetched_at": RTH}
    snap = ns["_live_option_dependency_snapshot"](_adapter(), order, now_utc=RTH)
    assert isinstance(snap, DependencySnapshot)
    assert (snap.asset_class, snap.regular_session_open, snap.market_open) == (
        "us_option", True, True)
    assert snap.open_short_put_collateral == Decimal("53000")
    assert snap.pending_sell_to_open_collateral == Decimal("12500")
    assert snap.underlying_put_collateral == Decimal("25500")
    assert snap.account_equity == Decimal("100000")
    assert (snap.quote_price, snap.quote, snap.calendar) == (
        Decimal("1.10"), Health.HEALTHY, Health.HEALTHY)
    assert snap.position_quantity == Decimal("0")
    decision = UnifiedOrderGate().evaluate(order, snap)
    assert "option.collateral_insufficient" in decision.reason_codes


def test_a_held_short_is_the_signed_position_and_premarket_is_not_rth():
    ns = _snapshot_ns(_pending_service())
    order = option_intent(symbol="APH261009P00130000")
    premarket = RTH - timedelta(hours=1, minutes=45)       # 09:15 ET
    ns["_live_option_quotes"][order.symbol] = {
        "price": Decimal("1.10"), "quote_at": premarket, "fetched_at": premarket}
    snap = ns["_live_option_dependency_snapshot"](_adapter(), order,
                                                  now_utc=premarket)
    assert snap.position_quantity == Decimal("-1")
    assert snap.regular_session_open is False and snap.market_open is True


def test_incomplete_contract_fields_make_collateral_unknown():
    ns = _snapshot_ns(_pending_service())
    order = option_intent()
    snap = ns["_live_option_dependency_snapshot"](
        _adapter(_option_positions_complete=False), order, now_utc=RTH)
    assert snap.open_short_put_collateral is None
    assert snap.underlying_put_collateral is None
    assert snap.quote is Health.UNKNOWN


def test_a_stale_option_quote_is_unhealthy():
    ns = _snapshot_ns(_pending_service())
    order = option_intent()
    ns["_live_option_quotes"][order.symbol] = {
        "price": Decimal("1.10"), "quote_at": RTH - timedelta(seconds=61),
        "fetched_at": RTH}
    snap = ns["_live_option_dependency_snapshot"](_adapter(), order, now_utc=RTH)
    assert snap.quote is Health.UNHEALTHY


def test_the_equity_provider_dispatches_only_option_intents():
    marker = object()
    ns = extract(
        ("_live_order_dependency_snapshot", "_open_order_idempotency_keys"),
        namespace={
            "datetime": datetime_module,
            "_live_order_dependency_lock": threading.Lock(),
            "_live_order_dependency_state": {"risk_snapshot_id": "risk-1"},
            "_live_risk_state": None, "MODE_LIVE": "live",
            "_live_stock_order_service": None, "instance_id": "instance-1",
            "_live_option_dependency_snapshot":
                lambda adapter, intent: (marker, intent),
        })
    provider = ns["_live_order_dependency_snapshot"]
    order = option_intent()
    assert provider(SimpleNamespace(), order) == (marker, order)
    equity = provider(SimpleNamespace(_positions={"AAPL": 5}, _market_marks=None,
                                      _positions_stale_since=None, _cash=1000.0,
                                      _instance_id="instance-1"),
                      equity_intent())
    assert isinstance(equity, DependencySnapshot)
    assert equity.asset_class == "us_equity"


def test_the_loop_runs_option_orders_after_the_stock_loop():
    text = source()
    stock_loop = text.index("for symbol in _exec_order:")
    call = text.index("_execute_option_intents(\n", stock_loop)
    snapshot = text.index("## Save portfolio snapshot every loop", stock_loop)
    assert stock_loop < call < snapshot
    guard = text[text.rindex("if (", stock_loop, call):call]
    assert "nexus_option_orders" in guard and "MODE_LIVE" in guard
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_broker_options.py -q -p no:cacheprovider`
Expected: FAIL with `AssertionError: missing from broker.py: ['_build_option_intent', '_execute_option_intents', '_live_option_quotes', '_refresh_option_quote']`, and the snapshot tests with the same message for `_live_option_dependency_snapshot`.

- [ ] **Step 3: Write the minimal implementation**

In `backend/broker.py`, directly before `def _live_order_dependency_snapshot(adapter, intent):` (that is, right after `_open_order_idempotency_keys` ends), add:

```python
#: swing-port: the latest REST options snapshot per contract. Shared by the
#: intent builder and the gate's snapshot provider so both read ONE quote:
#: the gate refuses a quote_at that differs from the intent's.
_live_option_quotes: dict = {}


def _refresh_option_quote(adapter, contract, now_utc, *, max_age_s=60.0):
    """A decision price for one option contract (spec 6.1: option quotes come
    from the options snapshot, and one older than 60 s is refreshed over REST
    before the check). Returns {"price", "quote_at", "fetched_at"} or None."""
    from decimal import Decimal

    symbol = str(contract or "").strip().upper()
    cached = _live_option_quotes.get(symbol)
    if (cached is not None
            and (now_utc - cached["fetched_at"]).total_seconds() <= max_age_s):
        return cached
    snap = (adapter.get_option_snapshots([symbol]) or {}).get(symbol)
    if snap is None:
        return None
    if snap.bid and snap.ask and snap.bid > 0 and snap.ask > 0:
        price = (snap.bid + snap.ask) / 2.0
    elif snap.last and snap.last > 0:
        price = snap.last
    else:
        return None
    quote_at = now_utc
    if snap.quote_ts:
        try:
            parsed = datetime.datetime.fromisoformat(
                str(snap.quote_ts).replace("Z", "+00:00"))
            quote_at = (parsed if parsed.tzinfo is not None
                        else parsed.replace(tzinfo=datetime.timezone.utc))
        except ValueError:
            quote_at = now_utc
    entry = {"price": Decimal(str(round(price, 4))), "quote_at": quote_at,
             "fetched_at": now_utc}
    _live_option_quotes[symbol] = entry
    return entry


def _build_option_intent(order_service, order, *, quote_at, decision_at,
                         risk_snapshot_id, source=None):
    """One `_nexus_option_orders` entry (interfaces doc section 1) as an
    OrderIntent. Raises ValueError on a malformed entry; nothing is sent."""
    from decimal import Decimal
    from live_orders import OrderIntent, OrderSide, OrderSource

    if not isinstance(order, dict):
        raise ValueError("an option order must be a dict")
    position_intent = str(order.get("position_intent") or "").strip().lower()
    if position_intent not in ("buy_to_open", "buy_to_close",
                               "sell_to_open", "sell_to_close"):
        raise ValueError(
            f"unsupported position_intent {order.get('position_intent')!r}")
    raw_qty = order.get("qty")
    if (isinstance(raw_qty, bool) or not isinstance(raw_qty, (int, float))
            or int(raw_qty) != raw_qty or int(raw_qty) < 1):
        raise ValueError(
            f"option qty must be a whole number of contracts >= 1, got {raw_qty!r}")
    order_type = str(order.get("order_type") or "limit").strip().lower()
    limit_price = order.get("limit_price")
    closing = position_intent.endswith("_to_close")
    if source is None:
        source = OrderSource.RISK_EXIT if closing else OrderSource.STRATEGY
    return OrderIntent(
        account_id=order_service.account_id,
        instance_id=order_service.instance_id,
        source=source,
        reason=str(order.get("reason") or position_intent),
        symbol=str(order.get("contract") or "").strip().upper(),
        side=OrderSide.SELL if position_intent.startswith("sell") else OrderSide.BUY,
        quantity=Decimal(int(raw_qty)),
        reduce_only=closing,
        decision_at=decision_at,
        quote_at=quote_at,
        risk_snapshot_id=risk_snapshot_id,
        order_type=order_type,
        limit_price=(Decimal(str(limit_price))
                     if order_type == "limit" and limit_price is not None
                     else None),
        tif=str(order.get("tif") or "day").strip().lower(),
        extended_hours=False,
        asset_class="us_option",
        position_intent=position_intent,
        contract_multiplier=100,
        underlying=order.get("underlying"),
        option_type=order.get("option_type"),
        strike=order.get("strike"),
        expiry=order.get("expiry"),
    )


def _pending_sell_to_open_collateral(order_service, *, exclude_key, underlying):
    """(all, on-underlying) collateral of sell-to-open puts still working at
    the broker, the intent being judged excluded. Raises when the lifecycle
    store is unreadable: the caller must not read unknown as zero."""
    from decimal import Decimal

    total = Decimal("0")
    mine = Decimal("0")
    wanted = str(underlying or "").strip().upper()
    for record in order_service.lifecycle_store.list_for_instance(
            order_service.instance_id):
        intent = record.intent
        if (record.terminal or record.client_order_id == exclude_key
                or intent.asset_class != "us_option"
                or intent.position_intent != "sell_to_open"
                or intent.option_type != "put"):
            continue
        remaining = max(Decimal("0"), intent.quantity - record.cumulative_quantity)
        collateral = intent.strike * intent.contract_multiplier * remaining
        total += collateral
        if intent.underlying == wanted:
            mine += collateral
    return total, mine


def _live_option_dependency_snapshot(adapter, intent, *, now_utc=None):
    """The gate's dependency view for one us_option intent (spec 6.1 options
    branch). The quote is the one _execute_option_intents fetched for this
    intent; the calendar is read here, so regular hours are known even on a
    tick whose stock loop had nothing to price."""
    from decimal import Decimal
    from live_orders import DependencySnapshot, Health, OrderSource

    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc)
    with _live_order_dependency_lock:
        state = dict(_live_order_dependency_state)
    symbol = str(intent.symbol).strip().upper()
    underlying = str(intent.underlying or "").strip().upper()
    positions = dict(getattr(adapter, "_option_positions", {}) or {})
    held = positions.get(symbol)
    position_quantity = Decimal(int(held.qty)) if held is not None else Decimal("0")
    quote = _live_option_quotes.get(symbol)
    if quote is None:
        quote_price = Decimal("0")
        quote_at = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)
        quote_health = Health.UNKNOWN
    else:
        quote_price = quote["price"]
        quote_at = quote["quote_at"]
        quote_health = (Health.HEALTHY
                        if (now_utc - quote_at).total_seconds() <= 60
                        else Health.UNHEALTHY)
    try:
        from live_calendar import is_nyse_open, is_nyse_open_extended
        regular_session_open = bool(is_nyse_open(now_utc))
        market_open = bool(is_nyse_open_extended(now_utc))
        calendar, calendar_at = Health.HEALTHY, now_utc
    except Exception:
        regular_session_open, market_open = False, False
        calendar, calendar_at = Health.UNKNOWN, None
    open_short = Decimal("0")
    open_short_on_underlying = Decimal("0")
    for row in positions.values():
        if int(row.qty) < 0 and str(row.option_type).lower() == "put":
            collateral = (Decimal(str(row.strike)) * int(row.multiplier)
                          * abs(int(row.qty)))
            open_short += collateral
            if str(row.underlying).upper() == underlying:
                open_short_on_underlying += collateral
    try:
        pending_total, pending_underlying = _pending_sell_to_open_collateral(
            _live_stock_order_service, exclude_key=intent.idempotency_key,
            underlying=underlying)
        pending_known = True
    except Exception:
        pending_total, pending_underlying = Decimal("0"), Decimal("0")
        pending_known = False
    known = bool(getattr(adapter, "_option_positions_complete", False)) and pending_known
    equity = getattr(adapter, "_account_equity", None)
    positions_health = state.get("positions", "unknown")
    if getattr(adapter, "_positions_stale_since", None) is not None:
        positions_health = "unhealthy"
    positions_at = state.get("positions_at")
    if not isinstance(positions_at, datetime.datetime):
        positions_at = datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

    def stamp(name):
        value = state.get(name)
        return value if isinstance(value, datetime.datetime) else None

    instance_identity = str(getattr(adapter, "_instance_id", "") or instance_id)
    return DependencySnapshot(
        account_id=str(globals().get("live_brokerage_id")
                       or getattr(adapter, "_account_id", None)
                       or getattr(adapter, "_instance_id", "")),
        instance_id=instance_identity,
        observed_at=now_utc,
        armed=bool(globals().get("mode") == MODE_LIVE
                   and str(globals().get("live_broker_type") or "").lower() == "alpaca"
                   and _live_stock_order_service is not None),
        kill_switch=Health(state.get("kill_switch", "unknown")),
        quote=quote_health,
        cash=Health(state.get("cash", "unknown")),
        positions=Health(positions_health),
        calendar=calendar,
        persistence=Health(state.get("persistence", "unknown")),
        risk_state=Health(state.get("risk_state", "unknown")),
        watchdog=Health(state.get("watchdog", "unknown")),
        quote_symbol=symbol,
        quote_price=quote_price,
        quote_at=quote_at,
        position_symbol=symbol,
        position_quantity=position_quantity,
        positions_at=positions_at,
        available_cash=Decimal(str(getattr(adapter, "_cash", 0) or 0)),
        market_open=market_open,
        risk_snapshot_id=str(state.get("risk_snapshot_id") or intent.risk_snapshot_id),
        kill_switch_at=stamp("kill_switch_at"),
        cash_at=stamp("cash_at"),
        calendar_at=calendar_at,
        persistence_at=stamp("persistence_at"),
        risk_state_at=stamp("risk_state_at"),
        watchdog_at=stamp("watchdog_at"),
        max_order_notional=(_live_risk_state.max_order_notional
                            if _live_risk_state is not None else None),
        max_position_quantity=None,
        max_quote_age=datetime.timedelta(seconds=60),
        open_order_idempotency_keys=_open_order_idempotency_keys(instance_identity),
        authorized_sources=frozenset(OrderSource),
        asset_class="us_option",
        regular_session_open=regular_session_open,
        account_equity=Decimal(str(equity)) if equity is not None else None,
        open_short_put_collateral=open_short if known else None,
        pending_sell_to_open_collateral=pending_total,
        underlying_put_collateral=(
            open_short_on_underlying + pending_underlying if known else None),
    )


def _execute_option_intents(option_orders, *, order_service, adapter, now_utc,
                            risk_snapshot_id, refused_reason="", log=None):
    """Submit the wheel lane's option orders through the unified order path
    (spec 6.1 broker item 2). Every refusal is logged with its reason (spec
    section 9 item 16); an order carrying a signal_id has its outcome written
    back to the signal. Returns one result dict per order."""
    def say(message, color="white"):
        if log is not None:
            try:
                log(message, color)
            except Exception:
                pass

    def place(entry, result):
        contract = result["contract"]
        if refused_reason:
            result["status"] = "refused"
            say(f"[wheel] {contract} {entry.get('position_intent')} NOT placed: "
                f"the wheel lane is refused ({refused_reason})", "red")
            return
        try:
            quote = _refresh_option_quote(adapter, contract, now_utc) if contract else None
        except Exception as exc:
            quote = None
            say(f"[wheel] {contract} options snapshot failed "
                f"({type(exc).__name__}: {exc})", "yellow")
        if quote is None:
            result["status"] = "no_quote"
            say(f"[wheel] {contract or entry!r} NOT placed: no usable options "
                "snapshot", "red")
            return
        try:
            intent = _build_option_intent(
                order_service, entry, quote_at=quote["quote_at"],
                decision_at=now_utc, risk_snapshot_id=risk_snapshot_id)
        except (TypeError, ValueError) as exc:
            result["status"] = "invalid"
            say(f"[wheel] malformed option order dropped ({exc}): {entry!r}", "red")
            return
        result["client_order_id"] = intent.idempotency_key
        try:
            submission = order_service.submit(intent)
        except Exception as exc:
            result["status"] = "error"
            say(f"[wheel] {contract} submit raised {type(exc).__name__}: {exc}", "red")
            return
        result["reason_codes"] = tuple(submission.decision.reason_codes)
        if submission.accepted:
            result["status"] = "submitted"
            say(f"[wheel] {intent.position_intent} {intent.quantity} {contract} "
                f"submitted ({intent.idempotency_key})", "green")
        elif not submission.decision.allowed:
            result["status"] = "blocked"
            say(f"[wheel] ORDER GATE BLOCKED {intent.position_intent} {contract}: "
                f"{','.join(submission.decision.reason_codes)}", "red")
        else:
            result["status"] = "uncertain"
            say(f"[wheel] {contract} outcome unknown "
                f"({','.join(submission.decision.reason_codes) or 'transport'}); "
                "the next reconcile resolves it", "red")

    results = []
    for order in list(option_orders or ()):
        entry = order if isinstance(order, dict) else {}
        result = {
            "contract": str(entry.get("contract") or "").strip().upper(),
            "position_intent": entry.get("position_intent"),
            "signal_id": entry.get("signal_id"),
            "status": "",
            "reason_codes": (),
            "client_order_id": None,
        }
        results.append(result)
        if not isinstance(order, dict):
            result["status"] = "invalid"
            say(f"[wheel] malformed option order dropped: {order!r}", "red")
            continue
        place(entry, result)
        signal_id = entry.get("signal_id")
        if signal_id and result["status"] != "uncertain":
            try:
                from swing_trader.signals_store import update_signal
                update_signal(str(signal_id), {
                    "status": "submitted" if result["status"] == "submitted" else "failed",
                    "order_client_id": result["client_order_id"],
                })
            except Exception as exc:
                say(f"[wheel] signal {signal_id} write-back failed "
                    f"({type(exc).__name__}: {exc})", "yellow")
    return results
```

At the top of `_live_order_dependency_snapshot`'s body (the first statement after its docstring), insert:

```python
    # swing-port: an option intent gets the options view (collateral, regular
    # hours, REST option quote). Every EB intent is us_equity and reads on.
    if getattr(intent, "asset_class", "us_equity") == "us_option":
        return _live_option_dependency_snapshot(adapter, intent)
```

In the main loop, directly before the block that starts `            ###################################\n            ## Save portfolio snapshot every loop`, insert (indentation 12 spaces):

```python
            # swing-port (spec 6.1 broker item 2): the wheel lane's option
            # orders, after the stock loop, through the same LiveOrderService.
            # Only a tick whose lanes emitted option orders gets here; EB emits
            # none, so doc 200 never enters.
            if (
                nexus_option_orders
                and mode == MODE_LIVE
                and str(live_broker_type or "").strip().lower() == "alpaca"
                and _live_stock_order_service is not None
                and live_adapter is not None
            ):
                with _live_order_dependency_lock:
                    _opt_risk_id = str(
                        _live_order_dependency_state.get("risk_snapshot_id")
                        or "risk:unavailable"
                    )
                try:
                    _execute_option_intents(
                        nexus_option_orders,
                        order_service=_live_stock_order_service,
                        adapter=live_adapter,
                        now_utc=datetime.datetime.now(datetime.timezone.utc),
                        risk_snapshot_id=_opt_risk_id,
                        refused_reason="",
                        log=_log,
                    )
                except Exception as _opt_exc:
                    _log(f"[wheel] option order execution crashed "
                         f"({type(_opt_exc).__name__}: {_opt_exc}); nothing "
                         "further this tick", "red")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_broker_options.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Run the regression suites**

Run: `python3 -m pytest backend/tests/test_live_dependency_freshness.py backend/tests/test_strategy_eb_live_submit.py backend/tests/test_manual_order_gate.py backend/tests/test_swing_broker_bracket.py backend/tests/test_swing_broker_dispatch.py backend/tests/test_swing_option_gate.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 6: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()` and `git diff --stat`.

```bash
git add backend/broker.py backend/tests/test_swing_broker_options.py
git commit -F - <<'EOF'
feat(broker): wheel option orders through the unified order path

After the stock loop, a tick that carries _nexus_option_orders submits
each through LiveOrderService: a REST options snapshot (refreshed when
older than 60 s) fixes the quote the intent and the gate share, and an
options dependency snapshot supplies regular-hours status, the signed
position, open and pending short-put collateral and equity. Each refusal
is logged with its reason and written back to its signal. A rerun of the
same contract in a session is deduped (spec section 9 fix 1). The equity
snapshot provider only dispatches on asset_class.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---
### Task 11: Guards: ordered_today, the pending guard, EB working-order helpers, kill-level cancel and halt

**Files:**
- Modify: `backend/broker_adapters/alpaca.py`: base import (34-41) and `refresh_orders_today` (636-650)
- Modify: `backend/live_pending_orders.py:1-23` (import) and `:72-86`
- Modify: `backend/live_risk_state.py:546-590` (`cancel_open_buy_orders`)
- Modify: `backend/broker.py`: `_core_sell_may_be_working` (13423), `_eb_buy_may_be_working`, `_eb_order_may_be_working`, `_eb_sell_leg_may_be_working` (13460-13553), and the halt loop in `_execute_live_command` (~10000-10008)
- Test: `backend/tests/test_swing_order_guards.py` (create)

**Interfaces:**
- Consumes: Task 2 `is_bracket_child_order` and `is_risk_reducing_order`; Task 3 `OrderRef.order_class` / `position_intent` from `_to_orderref`.
- Produces: no new names. The guards ignore bracket child legs; halt and the kill-level cancel skip risk-reducing orders (spec 6.1 broker items 5 and 6).

- [ ] **Step 0: Impact analysis**

```bash
grep -rn "refresh_orders_today\|ordered_today\|live_pending_symbols\|cancel_open_buy_orders\|_eb_order_may_be_working\|_eb_buy_may_be_working\|_eb_sell_leg_may_be_working\|_core_sell_may_be_working" backend --include=*.py | grep -v "/tests/"
```

Run `mcp__gitnexus__impact` upstream on `live_pending_symbols`, `cancel_open_buy_orders` and `refresh_orders_today`. Every one of these runs on alpaca-main: `ordered_today` guards each EB order, the four EB helpers decide EB's re-arms, and `cancel_open_buy_orders` is EB's kill rung. **Risk: HIGH.** Mitigation: every change is a skip keyed on `order_class` in `{bracket, oco, oto}` or on a closing `position_intent`. alpaca-main has no such orders, so every EB answer is unchanged. Do NOT touch the `_ny`/`_now` NameError inside `refresh_orders_today`; it is what keeps the cache refetching.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_order_guards.py`:

```python
"""swing-port Task 11: bracket legs are exits, not pending or working
orders; the kill rung and halt leave risk-reducing orders working."""
import datetime as datetime_module
import sys
from types import SimpleNamespace

from broker_adapters.base import OrderRef
from live_pending_orders import live_pending_symbols
from live_risk_state import cancel_open_buy_orders
from swing_alpaca_fakes import FakeTradingClient, enum, make_adapter, order_row
from swing_broker_harness import extract

OCC = "APH261009P00130000"


def _bracket_rows():
    parent = order_row(id="p", client_order_id="swingpap-p-0", symbol="AAPL",
                       status=enum("filled"), order_class=enum("bracket"))
    tp = order_row(id="tp", client_order_id="leg-tp", symbol="AAPL",
                   side=enum("sell"), status=enum("new"),
                   order_class=enum("bracket"), type=enum("limit"))
    sl = order_row(id="sl", client_order_id="leg-sl", symbol="AAPL",
                   side=enum("sell"), status=enum("held"),
                   order_class=enum("bracket"), type=enum("stop"))
    return parent, tp, sl


def test_ordered_today_ignores_bracket_legs_but_keeps_the_parent():
    parent, tp, sl = _bracket_rows()
    eb_sell = order_row(id="e", client_order_id="alpacama-e-0", symbol="GLD",
                        side=enum("sell"), status=enum("new"))
    adapter = make_adapter(FakeTradingClient(orders=[parent, tp, sl, eb_sell]))
    assert adapter.refresh_orders_today() == {"AAPL": {"buy"}, "GLD": {"sell"}}
    assert adapter.ordered_today("AAPL", "buy") is True
    assert adapter.ordered_today("AAPL", "sell") is False


def test_the_pending_guard_ignores_legs_but_not_a_held_simple_order():
    tp = OrderRef("tp", "leg-tp", "AAPL", "sell", 5.0, "new", order_class="bracket")
    sl = OrderRef("sl", "leg-sl", "AAPL", "sell", 5.0, "held", order_class="bracket")
    odd = OrderRef("x", "c-x", "MSFT", "buy", 1.0, "held", order_class="simple")
    eb = OrderRef("e", "c-e", "TQQQ", "buy", 1.0, "new")
    adapter = SimpleNamespace(list_open_orders_strict=lambda: [tp, sl, odd, eb])
    assert live_pending_symbols(adapter) == ("MSFT", "TQQQ")


LEG = SimpleNamespace(broker_order_id="tp", symbol="TQQQ", side="sell",
                      status="new", order_class="bracket")
HELD = SimpleNamespace(broker_order_id="sl", symbol="TQQQ", side="sell",
                       status="held", order_class="bracket")
PLAIN = SimpleNamespace(broker_order_id="s", symbol="TQQQ", side="sell",
                        status="new", order_class="simple")


def _helpers():
    return extract(("_core_sell_may_be_working", "_eb_buy_may_be_working",
                    "_eb_order_may_be_working", "_eb_sell_leg_may_be_working"))


def _book(*refs):
    return SimpleNamespace(list_open_orders_strict=lambda limit=200: list(refs))


def _quiet(*_args, **_kwargs):
    return None


def test_bracket_legs_are_not_working_orders_for_the_eb_helpers():
    ns = _helpers()
    book = _book(LEG, HELD)
    assert ns["_core_sell_may_be_working"](book, "TQQQ", _quiet) is False
    assert ns["_eb_order_may_be_working"](book, _quiet) is False
    assert ns["_eb_sell_leg_may_be_working"](book, "TQQQ", _quiet) is False
    assert ns["_eb_buy_may_be_working"](book, _quiet) is False


def test_eb_shaped_orders_still_count_as_working():
    ns = _helpers()
    book = _book(PLAIN)
    assert ns["_core_sell_may_be_working"](book, "TQQQ", _quiet) is True
    assert ns["_eb_order_may_be_working"](book, _quiet) is True
    assert ns["_eb_sell_leg_may_be_working"](book, "TQQQ", _quiet) is True


def test_the_kill_rung_cancels_opening_buys_but_not_a_buy_to_close():
    btc = SimpleNamespace(broker_order_id="btc", symbol=OCC, side="buy",
                          status="new", order_class="simple",
                          position_intent="buy_to_close")
    parent = SimpleNamespace(broker_order_id="parent", symbol="AAPL",
                             side="buy", status="new", order_class="bracket")
    plain = SimpleNamespace(broker_order_id="plain", symbol="TQQQ",
                            side="buy", status="new")
    cancelled = []
    adapter = SimpleNamespace(
        list_open_orders_strict=lambda: [btc, parent, plain],
        cancel_order=lambda oid: cancelled.append(oid) or True)
    assert cancel_open_buy_orders(adapter, log=_quiet) == 2
    assert cancelled == ["parent", "plain"]


def test_halt_leaves_risk_reducing_orders_working(monkeypatch):
    monkeypatch.setitem(sys.modules, "live_alerts",
                        SimpleNamespace(alert_halt=lambda **kw: None))
    ns = extract(("_execute_live_command",), check=(), namespace={
        "datetime": datetime_module, "instance_id": "swing-paper",
        "get_conn_retry": lambda **kw: None,
        "r": SimpleNamespace(update=lambda *a, **k: None),
        "time": SimpleNamespace(sleep=lambda seconds: None),
    })
    leg = SimpleNamespace(broker_order_id="tp", symbol="AAPL", side="sell",
                          status="new", order_class="bracket")
    btc = SimpleNamespace(broker_order_id="btc", symbol=OCC, side="buy",
                          status="new", position_intent="buy_to_close")
    buy = SimpleNamespace(broker_order_id="buy", symbol="MSFT", side="buy",
                          status="new")
    cancelled = []
    adapter = SimpleNamespace(
        list_open_orders=lambda limit=500: [leg, btc, buy],
        cancel_order=lambda oid: cancelled.append(oid) or True)
    ok, _error, result = ns["_execute_live_command"](
        adapter, {"type": "halt", "payload": {"reason": "test"}})
    assert ok is True
    assert set(cancelled) == {"buy"} and result["orders_canceled"] == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_order_guards.py -q -p no:cacheprovider`
Expected: FAIL in five places:
- `refresh_orders_today` returns `{"AAPL": {"buy", "sell"}, ...}`;
- the pending guard includes `AAPL`;
- the EB helpers see the legs;
- the kill rung cancels `btc`;
- halt cancels all three.

`test_eb_shaped_orders_still_count_as_working` passes already, which is correct.

- [ ] **Step 3: Write the minimal implementation**

(a) `backend/broker_adapters/alpaca.py`: add `is_bracket_child_order,` to the `from broker_adapters.base import (...)` list. In `refresh_orders_today`, directly after

```python
                if status in ("rejected", "canceled", "expired", "denied"):
                    continue
```

insert:

```python
                # swing-port: a bracket's take-profit and stop-loss legs are
                # exits the parent created; counting them as "sell ordered
                # today" would skip the swing lane's own exit. The parent BUY
                # still counts, so a duplicate entry is still refused.
                if is_bracket_child_order(o):
                    continue
```

(b) `backend/live_pending_orders.py`: after `from __future__ import annotations`, add `from broker_adapters.base import is_bracket_child_order`. In `live_pending_symbols`, directly before `        if status.strip().lower() in TERMINAL_ORDER_STATES:`, insert:

```python
        # swing-port: a bracket's legs are exits, not pending buys; counting
        # them would block every re-entry while a position is protected. A
        # held order OUTSIDE a multi-leg class still counts: the guard stays
        # closed on what it cannot explain.
        if is_bracket_child_order(order):
            continue
```

(c) `backend/live_risk_state.py`, in `cancel_open_buy_orders`: add `from broker_adapters.base import is_risk_reducing_order` as the first line of the function body (after the docstring). In the loop, directly after

```python
        if str(getattr(ref, "side", "") or "").strip().lower() != "buy":
            continue
```

insert:

```python
        # swing-port (spec 6.1 broker item 6): a buy-to-close REDUCES risk;
        # the kill rung exists to stop new exposure, not to strand a short put.
        if is_risk_reducing_order(ref):
            continue
```

(d) `backend/broker.py`, the four EB helpers. Add `    from broker_adapters.base import is_bracket_child_order` as the first line after each docstring of `_core_sell_may_be_working`, `_eb_buy_may_be_working`, `_eb_order_may_be_working` and `_eb_sell_leg_may_be_working`.

In `_core_sell_may_be_working`, `_eb_buy_may_be_working` and `_eb_sell_leg_may_be_working`, make these two lines (indented 8 spaces, the loop body) the first statement inside `    for ref in (working or []):`:

```python
        if is_bracket_child_order(ref):
            continue
```

In `_eb_order_may_be_working`, replace the line `    if working:` (indented 4 spaces) with:

```python
    # swing-port: bracket legs are exits a parent created, not working
    # orders a re-plan could duplicate. alpaca-main has none.
    working = [ref for ref in (working or []) if not is_bracket_child_order(ref)]
    if working:
```

(e) `backend/broker.py`, the halt branch of `_execute_live_command`: directly before `                orders_canceled = 0`, insert

```python
                # swing-port (spec 6.1 broker item 6): halt leaves risk-reducing
                # orders working (bracket legs, option buy-to-close).
                from broker_adapters.base import is_risk_reducing_order
```

and in the inner loop replace

```python
                            try:
                                if adapter.cancel_order(od.broker_order_id):
```

with

```python
                            try:
                                if is_risk_reducing_order(od):
                                    continue
                                if adapter.cancel_order(od.broker_order_id):
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_order_guards.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Run the EB guard suites**

Run: `python3 -m pytest backend/tests/test_strategy_eb_live_submit.py backend/tests/test_strategy_eb_pending_guard.py backend/tests/test_live_pending_orders.py backend/tests/test_live_risk_ladder.py backend/tests/test_live_risk_state.py backend/tests/test_manual_order_gate.py backend/tests/test_alpaca_submit_guards.py backend/tests/test_strategy_eb_broker_wiring.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 6: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()`. Expected symbols: `refresh_orders_today`, `live_pending_symbols`, `cancel_open_buy_orders`, plus the four broker helpers and `_execute_live_command` (broker ones are visible only in `git diff`).

```bash
git add backend/broker_adapters/alpaca.py backend/live_pending_orders.py backend/live_risk_state.py backend/broker.py backend/tests/test_swing_order_guards.py
git commit -F - <<'EOF'
fix(live): guards ignore bracket legs; halt and kill keep risk reducers

ordered_today, the pending-buy guard and EB's working-order helpers skip
bracket child legs (and held multi-leg children), which are exits the
parent created. The kill rung's buy cancel and the halt command leave
bracket legs and option buy-to-close orders working. alpaca-main holds
no multi-leg or option orders, so every EB answer is unchanged.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 12: Option activities poller (assignment, expiry, exercise)

**Files:**
- Modify: `backend/broker.py`: add module state and `_poll_option_activities` directly after `_execute_option_intents` (Task 10), which is directly before `def _live_order_dependency_snapshot(`; in the main loop, add the poll call directly before the Task 10 option-execution block.
- Test: `backend/tests/test_swing_option_activities.py` (create)

**Interfaces:**
- Consumes:
  - Task 4 `get_option_activities`, and `option_contract_meta` / `refresh_positions` from Task 5
  - Task 7 `record_external_fill`
  - Task 1 `OrderSource.OPTION_ACTIVITY`
  - Task 8 `_lane_enabled`
  - plan B's `swing_trader.notify.notify_wheel_assignment(instance_id, *, symbol, qty, price=None, date=None)`, with a fallback to `notifications.notify(category="wheel_assignment", ...)` when plan B's module is absent
- Produces:
  - `_OPTION_ACTIVITY_TYPES`
  - `_option_activity_last_poll: dict`
  - `_poll_option_activities(adapter, order_service, wheel_cache, *, now_utc, log=None, notify=None, min_interval_s=300.0, monotonic=None) -> list[OptionActivityDTO]`
  - strategy-cache keys `_engine_option_activity_cursor` and `_engine_wheel_assignments`

- [ ] **Step 0: Impact analysis**

```bash
grep -n "_execute_option_intents(\|_strategy_cache.setdefault\|_reconcile_alpaca_ownership(" backend/broker.py
```

The new call runs only when `_lane_enabled(_cached_strategies, "strategy_wheel")`. **Risk: LOW** for EB (doc 200 has no wheel lane). **Risk: MEDIUM** for paper: an assignment changes cash and shares.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_option_activities.py`:

```python
"""swing-port Task 12: OPASN/OPEXP/OPEXC become lifecycle facts, exactly
once, under a cursor kept in the wheel lane's strategy cache."""
import datetime as datetime_module
from decimal import Decimal

from broker_adapters.base import OptionActivityDTO, OptionPositionDTO
from live_orders import (
    InMemoryLifecycleBackend,
    LiveOrderService,
    OrderLifecycleStore,
    OrderSide,
    OrderSource,
)
from swing_broker_harness import extract, source
from swing_live_fixtures import OCC, RTH

CALL = "APH261009C00140000"


def _poller():
    return extract(("_poll_option_activities",),
                   assigns=("_OPTION_ACTIVITY_TYPES",
                            "_option_activity_last_poll"),
                   namespace={"datetime": datetime_module})[
        "_poll_option_activities"]


def _put(symbol=OCC, option_type="put", strike=130.0):
    return OptionPositionDTO(symbol, "APH", option_type, strike, "2026-10-09",
                             -1, 1.2, 3.5, -350.0, -230.0)


class _Account:
    def __init__(self, activities, *, positions=None, contracts=None,
                 fail=False):
        self.activities = list(activities)
        self.calls = []
        self._option_positions = dict(positions or {})
        self.contracts = dict(contracts or {})
        self.fail = fail
        self.refreshes = 0

    def get_option_activities(self, types=("OPASN", "OPEXP", "OPEXC"),
                              after=None):
        self.calls.append((tuple(types), after))
        if self.fail:
            raise RuntimeError("activities endpoint unreachable")
        return list(self.activities)

    def option_contract_meta(self, symbol):
        return self.contracts.get(symbol)

    def refresh_positions(self):
        self.refreshes += 1
        return []


def _service():
    fills = []
    service = LiveOrderService(
        account_id="acct-1", instance_id="instance-1", snapshot_provider=None,
        transport=None,
        lifecycle_store=OrderLifecycleStore(InMemoryLifecycleBackend()),
        confirmed_fill_handler=fills.append)
    return service, fills


def _assigned(activity_id="act-1", symbol=OCC, qty=-1.0, day="2026-10-09"):
    return OptionActivityDTO(activity_id, "OPASN", symbol, qty, day, None)


def _collect():
    sent = []
    return sent, lambda instance_id, **fields: sent.append(
        {"instance_id": instance_id, **fields})


def _ignore(*_args, **_kwargs):
    return None


def test_a_put_assignment_adds_shares_owned_by_the_wheel_once():
    poll = _poller()
    service, fills = _service()
    account = _Account([_assigned()], positions={OCC: _put()})
    cache, (sent, notify) = {}, _collect()
    done = poll(account, service, cache, now_utc=RTH, notify=notify,
                min_interval_s=0)
    assert [a.id for a in done] == ["act-1"]
    (fill,) = fills
    assert (fill.event.symbol, fill.event.side, fill.incremental_quantity,
            fill.incremental_price, fill.cash_delta) == (
        "APH", OrderSide.BUY, Decimal("100"), Decimal("130"), Decimal("-13000"))
    record = next(r for r in service.lifecycle_store.list_for_instance(
        "instance-1"))
    assert record.intent.source is OrderSource.OPTION_ACTIVITY
    assert cache["_engine_wheel_assignments"] == [{
        "activity_id": "act-1", "contract": OCC, "underlying": "APH",
        "shares": 100, "side": "buy", "strike": 130.0, "date": "2026-10-09"}]
    assert cache["_engine_option_activity_cursor"] == {
        "after": "2026-10-08", "seen": ["act-1"]}
    assert sent == [{"instance_id": "instance-1", "symbol": "APH", "qty": 100,
                     "price": 130.0, "date": "2026-10-09"}]
    assert account.refreshes == 1


def test_a_replay_with_a_lost_cursor_records_and_announces_nothing_twice():
    poll = _poller()
    service, fills = _service()
    account = _Account([_assigned()], positions={OCC: _put()})
    sent, notify = _collect()
    poll(account, service, {}, now_utc=RTH, notify=notify, min_interval_s=0)
    poll(account, service, {}, now_utc=RTH, notify=notify, min_interval_s=0)
    assert len(fills) == 1 and len(sent) == 1


def test_a_seen_activity_is_skipped_by_id():
    poll = _poller()
    service, fills = _service()
    cache = {"_engine_option_activity_cursor": {"after": "2026-10-08",
                                                "seen": ["act-1"]}}
    account = _Account([_assigned()], positions={OCC: _put()})
    assert poll(account, service, cache, now_utc=RTH, min_interval_s=0) == []
    assert account.calls[0][1] == "2026-10-08" and fills == []


def test_an_unresolvable_contract_is_retried_not_marked_seen():
    poll = _poller()
    service, fills = _service()
    lines = []
    account = _Account([_assigned()])
    cache = {}
    assert poll(account, service, cache, now_utc=RTH, min_interval_s=0,
                log=lambda m, c="white": lines.append((m, c))) == []
    assert fills == [] and "_engine_option_activity_cursor" not in cache
    assert any(c == "red" and "NOT recorded" in m for m, c in lines)
    account.contracts[OCC] = _put()
    poll(account, service, cache, now_utc=RTH, min_interval_s=0,
         notify=_ignore)
    assert len(fills) == 1


def test_a_short_call_assignment_sells_the_shares():
    poll = _poller()
    service, fills = _service()
    account = _Account([_assigned(symbol=CALL)],
                       positions={CALL: _put(CALL, "call", 140.0)})
    poll(account, service, {}, now_utc=RTH, notify=_ignore, min_interval_s=0)
    (fill,) = fills
    assert (fill.event.side, fill.incremental_quantity, fill.cash_delta) == (
        OrderSide.SELL, Decimal("100"), Decimal("14000"))


def test_an_expiry_is_logged_and_refreshed_but_records_no_fill():
    poll = _poller()
    service, fills = _service()
    expiry = OptionActivityDTO("exp-1", "OPEXP", OCC, -1.0, "2026-10-09", 0.0)
    account = _Account([expiry], positions={OCC: _put()})
    cache, (sent, notify) = {}, _collect()
    done = poll(account, service, cache, now_utc=RTH, notify=notify,
                min_interval_s=0)
    assert [a.id for a in done] == ["exp-1"] and fills == [] and sent == []
    assert cache["_engine_option_activity_cursor"]["seen"] == ["exp-1"]
    assert account.refreshes == 1


def test_polls_are_throttled_and_a_read_failure_changes_nothing():
    poll = _poller()
    service, _fills = _service()
    ticks = [1000.0]
    account = _Account([], fail=True)
    cache = {}
    assert poll(account, service, cache, now_utc=RTH,
                monotonic=lambda: ticks[0]) == []
    assert poll(account, service, cache, now_utc=RTH,
                monotonic=lambda: ticks[0]) == []
    assert len(account.calls) == 1 and cache == {}
    ticks[0] += 301.0
    poll(account, service, cache, now_utc=RTH, monotonic=lambda: ticks[0])
    assert len(account.calls) == 2
    assert account.calls[0][1] == "2026-09-28"


def test_the_loop_polls_only_for_an_enabled_wheel_lane_before_orders():
    text = source()
    stock_loop = text.index("for symbol in _exec_order:")
    poll = text.index("_poll_option_activities(\n", stock_loop)
    execute = text.index("_execute_option_intents(\n", stock_loop)
    assert poll < execute
    guard = text[text.rindex("if (", stock_loop, poll):poll]
    assert '_lane_enabled(_cached_strategies, "strategy_wheel")' in guard
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_option_activities.py -q -p no:cacheprovider`
Expected: FAIL with `AssertionError: missing from broker.py: ['_OPTION_ACTIVITY_TYPES', '_option_activity_last_poll', '_poll_option_activities']`.

- [ ] **Step 3: Write the minimal implementation**

In `backend/broker.py`, directly after `_execute_option_intents` ends, which is directly before `def _live_order_dependency_snapshot(adapter, intent):` (Task 10 put its functions there), add:

```python
#: swing-port: the option activity types a wheel position ends in.
_OPTION_ACTIVITY_TYPES = ("OPASN", "OPEXP", "OPEXC")
#: Monotonic time of the last activities poll (a throttle, not the cursor).
_option_activity_last_poll: dict = {"at": None}


def _poll_option_activities(adapter, order_service, wheel_cache, *, now_utc,
                            log=None, notify=None, min_interval_s=300.0,
                            monotonic=None):
    """Turn option assignment, expiry and exercise activities into lifecycle
    facts (spec 6.1 broker item 8). Returns the activities processed.

    The cursor lives in the wheel lane's strategy cache
    (_engine_option_activity_cursor: `after` one day before the newest date
    seen, plus the ids handled), so a restart re-reads at most a day and
    dedupes by id. An assignment is recorded through record_external_fill
    under a key derived from the activity id, exactly once even if the
    cursor is lost: a put assignment adds the shares (a call assignment
    removes them) under this instance's lineage, appends to
    _engine_wheel_assignments so the wheel lane knows the shares are its
    own, and sends one wheel_assignment notification (through plan B's
    swing_trader.notify.notify_wheel_assignment when present). An expiry or
    exercise is logged; broker truth drops the option on the refresh that follows.
    An assignment whose contract cannot be resolved stays unseen and is
    retried on the next poll.
    """
    import hashlib
    import time as _time
    from decimal import Decimal
    from live_orders import OrderIntent, OrderSide, OrderSource

    def say(message, color="white"):
        if log is not None:
            try:
                log(message, color)
            except Exception:
                pass

    def default_notify(instance_id_value, **fields):
        # Plan B's sender when it is deployed (it knows the category's push
        # routing and priority); the bare notification otherwise.
        try:
            from swing_trader.notify import notify_wheel_assignment
        except Exception:
            notify_wheel_assignment = None
        if notify_wheel_assignment is not None:
            notify_wheel_assignment(instance_id_value, **fields)
            return
        import os as _os
        from notifications import notify as _notify
        _notify(category="wheel_assignment", instance_id=instance_id_value,
                title=f"Wheel assignment: {fields['symbol']}",
                body=(f"{fields['symbol']}: assigned {fields['qty']} shares @ "
                      f"${fields['price']:.2f} on {fields['date']}"),
                discord_channel=_os.environ.get("LIVE_ALERTS_CHANNEL", "trades"))

    def when(text):
        raw = str(text or "")
        try:
            if len(raw) == 10:
                day = datetime.date.fromisoformat(raw)
                return datetime.datetime(day.year, day.month, day.day, 20, 0,
                                         tzinfo=datetime.timezone.utc)
            parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo is not None
                    else parsed.replace(tzinfo=datetime.timezone.utc))
        except ValueError:
            return now_utc

    clock = monotonic or _time.monotonic
    last = _option_activity_last_poll.get("at")
    if last is not None and clock() - last < float(min_interval_s):
        return []
    _option_activity_last_poll["at"] = clock()
    cursor = dict(wheel_cache.get("_engine_option_activity_cursor") or {})
    seen = list(cursor.get("seen") or [])
    after = cursor.get("after") or (
        now_utc.date() - datetime.timedelta(days=7)).isoformat()
    try:
        activities = adapter.get_option_activities(
            types=_OPTION_ACTIVITY_TYPES, after=after)
    except Exception as exc:
        say(f"[wheel] option activities unreadable ({type(exc).__name__}: "
            f"{exc}); retried next poll", "yellow")
        return []
    processed = []
    newest = None
    for activity in activities or ():
        if not activity.id or activity.id in seen:
            continue
        symbol = activity.symbol
        if activity.activity_type == "OPASN":
            meta = (getattr(adapter, "_option_positions", {}) or {}).get(symbol)
            if (meta is None or not getattr(meta, "underlying", "")
                    or not getattr(meta, "strike", 0)):
                meta = adapter.option_contract_meta(symbol)
            if meta is None or not meta.underlying:
                say(f"[wheel] assignment {activity.id} on {symbol} NOT "
                    "recorded: its contract fields are unknown; retried next "
                    "poll", "red")
                continue
            contracts = max(1, abs(int(round(float(activity.qty or 0)))))
            shares = contracts * 100
            side = (OrderSide.BUY if str(meta.option_type).lower() == "put"
                    else OrderSide.SELL)
            strike = Decimal(str(meta.strike))
            occurred = when(activity.date)
            key = "opasn-" + hashlib.sha256(
                activity.id.encode("utf-8")).hexdigest()[:32]
            intent = OrderIntent(
                account_id=order_service.account_id,
                instance_id=order_service.instance_id,
                source=OrderSource.OPTION_ACTIVITY,
                reason=f"wheel_assignment:{symbol}",
                symbol=meta.underlying,
                side=side,
                quantity=Decimal(shares),
                reduce_only=side is OrderSide.SELL,
                decision_at=occurred,
                quote_at=occurred,
                risk_snapshot_id="option-activity",
                reference_price=strike,
                broker_client_order_id=key,
            )
            try:
                applied = order_service.record_external_fill(
                    intent, broker_order_id=key, quantity=Decimal(shares),
                    price=strike, occurred_at=occurred,
                    reason=f"option assignment {symbol}")
            except Exception as exc:
                say(f"[wheel] assignment {activity.id} on {symbol} NOT "
                    f"recorded ({type(exc).__name__}: {exc}); retried next "
                    "poll", "red")
                continue
            if applied.applied:
                wheel_cache.setdefault("_engine_wheel_assignments", []).append({
                    "activity_id": activity.id, "contract": symbol,
                    "underlying": meta.underlying, "shares": shares,
                    "side": side.value, "strike": float(strike),
                    "date": str(activity.date)[:10],
                })
                say(f"[wheel] ASSIGNED {symbol}: {side.value} {shares} "
                    f"{meta.underlying} at {strike}", "yellow")
                try:
                    (notify or default_notify)(
                        str(order_service.instance_id),
                        symbol=meta.underlying, qty=shares,
                        price=float(strike), date=str(activity.date)[:10])
                except Exception as exc:
                    say(f"[wheel] assignment notification failed "
                        f"({type(exc).__name__}: {exc})", "yellow")
        else:
            say(f"[wheel] {activity.activity_type} {symbol} qty {activity.qty} "
                f"on {activity.date}", "cyan")
        processed.append(activity)
        seen.append(activity.id)
        day = str(activity.date or "")[:10]
        if day and (newest is None or day > newest):
            newest = day
    if processed:
        if newest is not None:
            try:
                after = (datetime.date.fromisoformat(newest)
                         - datetime.timedelta(days=1)).isoformat()
            except ValueError:
                pass
        wheel_cache["_engine_option_activity_cursor"] = {
            "after": after, "seen": seen[-500:]}
        try:
            adapter.refresh_positions()
        except Exception as exc:
            say(f"[wheel] position refresh after activities failed "
                f"({type(exc).__name__}: {exc})", "yellow")
    return processed
```

In the main loop, directly before the Task 10 block (`            # swing-port (spec 6.1 broker item 2): the wheel lane's option`), insert:

```python
            # swing-port (spec 6.1 broker item 8): option assignments, expiries
            # and exercises, polled only for a document with an enabled wheel
            # lane. An assignment moves shares, so ownership is re-derived.
            if (
                mode == MODE_LIVE
                and str(live_broker_type or "").strip().lower() == "alpaca"
                and _live_stock_order_service is not None
                and live_adapter is not None
                and _lane_enabled(_cached_strategies, "strategy_wheel")
            ):
                try:
                    if _poll_option_activities(
                        live_adapter,
                        _live_stock_order_service,
                        _strategy_cache.setdefault("strategy_wheel", {}),
                        now_utc=datetime.datetime.now(datetime.timezone.utc),
                        log=_log,
                    ):
                        _reconcile_alpaca_ownership(
                            live_adapter, _live_stock_order_service)
                except Exception as _act_exc:
                    _log(f"[wheel] option activities poll crashed "
                         f"({type(_act_exc).__name__}: {_act_exc})", "red")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_option_activities.py backend/tests/test_swing_broker_options.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()` and `git diff --stat`.

```bash
git add backend/broker.py backend/tests/test_swing_option_activities.py
git commit -F - <<'EOF'
feat(broker): option assignment, expiry and exercise activities poller

On a document with an enabled wheel lane, the live tick polls OPASN,
OPEXP and OPEXC activities at most every five minutes since a cursor kept
in the wheel lane's strategy cache. An assignment is recorded exactly
once as a lifecycle fill of the underlying's shares under the wheel
lane, listed for the lane, announced with a wheel_assignment
notification, and followed by a reconcile; expiries and exercises are
logged and broker truth drops the option.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 13: Options-level check for the wheel lane

**Files:**
- Modify: `backend/broker.py`: add `_wheel_options_check` and `_wheel_options_refusal` after `_poll_option_activities`; in the Task 10 call site, replace `refused_reason=""`.
- Test: `backend/tests/test_swing_options_level.py` (create)

**Interfaces:**
- Consumes: Task 4 `get_account_options`, Task 8 `_lane_enabled`, Task 10 `_execute_option_intents(refused_reason=...)`.
- Produces: `_wheel_options_check: dict`, and `_wheel_options_refusal(adapter, cached_strategies, *, log=None, alert=None) -> str`, which returns `""` when the lane may trade.

- [ ] **Step 0: Impact analysis**

`grep -n 'refused_reason=""' backend/broker.py`. There must be exactly one match: the Task 10 call site. **Risk: LOW** (wheel documents only).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_options_level.py`:

```python
"""swing-port Task 13: the wheel lane trades only on an account approved for
options level 1 (cash-secured puts); below that it is refused, in red, with
one alert."""
from types import SimpleNamespace

from swing_broker_harness import extract, source

WHEEL = [{"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}}]


def _check():
    return extract(("_wheel_options_refusal", "_lane_enabled", "_truthy",
                    "_merged_strategy_settings"),
                   assigns=("_wheel_options_check", "_LANE_ENABLE_FLAGS"))[
        "_wheel_options_refusal"]


class _Account:
    def __init__(self, level=1, fail=False):
        self.level = level
        self.fail = fail
        self.reads = 0
        self._instance_id = "swing-paper"

    def get_account_options(self):
        self.reads += 1
        if self.fail:
            raise RuntimeError("account endpoint unreachable")
        return {"options_trading_level": self.level}


def _sink():
    lines, alerts = [], []
    return (lines, alerts, lambda m, c="white": lines.append((m, c)),
            lambda **kw: alerts.append(kw))


def test_no_wheel_lane_means_no_check():
    check = _check()
    account = _Account(level=0)
    assert check(account, [{"strategy": "strategy_eb",
                            "config": {"strategy_eb_enabled": True}}]) == ""
    assert account.reads == 0


def test_level_one_permits_the_lane_and_is_read_once():
    check = _check()
    account = _Account(level=1)
    assert check(account, WHEEL) == "" and check(account, WHEEL) == ""
    assert account.reads == 1


def test_level_zero_refuses_the_lane_in_red_with_one_alert():
    check = _check()
    account = _Account(level=0)
    lines, alerts, log, alert = _sink()
    refusal = check(account, WHEEL, log=log, alert=alert)
    assert "options_trading_level=0" in refusal
    assert check(account, WHEEL, log=log, alert=alert) == refusal
    assert account.reads == 1 and len(alerts) == 1
    assert alerts[0]["tag"] == "wheel-options-level"
    assert any(c == "red" and "REFUSED" in m for m, c in lines)


def test_an_unknown_level_refuses():
    check = _check()
    assert check(_Account(level=None), WHEEL, alert=lambda **kw: None) != ""


def test_an_unreadable_account_refuses_this_tick_and_asks_again():
    check = _check()
    account = _Account(fail=True)
    assert check(account, WHEEL) != ""
    account.fail = False
    assert check(account, WHEEL) == ""
    assert account.reads == 2


def test_the_option_call_site_passes_the_refusal():
    text = source()
    call = " ".join(text.split("_execute_option_intents(\n", 1)[1][:900].split())
    assert ("refused_reason=_wheel_options_refusal( live_adapter, "
            "_cached_strategies, log=_log)") in call
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_options_level.py -q -p no:cacheprovider`
Expected: FAIL with `AssertionError: missing from broker.py: ['_wheel_options_check', '_wheel_options_refusal']`.

- [ ] **Step 3: Write the minimal implementation**

In `backend/broker.py`, directly after `_poll_option_activities` ends, which is directly before `def _live_order_dependency_snapshot(adapter, intent):`, add:

```python
#: swing-port: this process's one options-level verdict for the wheel lane.
_wheel_options_check: dict = {"refusal": None}


def _wheel_options_refusal(adapter, cached_strategies, *, log=None, alert=None):
    """"" when the wheel lane may trade options, else why not.

    Spec 6.1: the adapter checks options_trading_level >= 1 when the
    document has an enabled wheel lane; below that the lane is refused, in
    red, with an alert. The check runs on the first live tick that carries
    wheel orders (at boot the strategy document is not loaded yet) and the
    verdict is kept for the process; an unreadable account refuses THIS tick
    only and is asked again.
    """
    if not _lane_enabled(cached_strategies, "strategy_wheel"):
        return ""
    cached = _wheel_options_check.get("refusal")
    if cached is not None:
        return cached

    def say(message, color):
        if log is not None:
            try:
                log(message, color)
            except Exception:
                pass

    def default_alert(**kwargs):
        from live_alerts import alert_strategy_error
        alert_strategy_error(**kwargs)

    try:
        options = adapter.get_account_options() or {}
    except Exception as exc:
        say(f"[wheel] options level unreadable ({type(exc).__name__}: {exc}); "
            "wheel orders refused this tick", "yellow")
        return f"options level unreadable ({type(exc).__name__})"
    level = options.get("options_trading_level")
    try:
        level_value = int(level) if level is not None else None
    except (TypeError, ValueError):
        level_value = None
    if level_value is not None and level_value >= 1:
        _wheel_options_check["refusal"] = ""
        say(f"[wheel] options_trading_level={level_value}: cash-secured puts "
            "permitted", "green")
        return ""
    refusal = f"options_trading_level={level} is below 1"
    _wheel_options_check["refusal"] = refusal
    say(f"[wheel] wheel lane REFUSED: {refusal}. No option order will be "
        "placed; approve options level 1 on the paper account and restart "
        "the instance.", "red")
    try:
        (alert or default_alert)(
            instance_id=str(getattr(adapter, "_instance_id", "") or ""),
            tag="wheel-options-level",
            message=f"Wheel lane refused: {refusal}")
    except Exception:
        pass
    return refusal
```

In the Task 10 call site, replace `                        refused_reason="",` with:

```python
                        refused_reason=_wheel_options_refusal(
                            live_adapter, _cached_strategies, log=_log),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_options_level.py backend/tests/test_swing_broker_options.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Detect changes and commit**

```bash
git add backend/broker.py backend/tests/test_swing_options_level.py
git commit -F - <<'EOF'
feat(broker): refuse the wheel lane below options level 1

The first live tick that carries wheel option orders reads the paper
account's options_trading_level. Level 1 or higher permits cash-secured
puts for the life of the process; anything lower refuses every wheel
order with a red line and one alert. An unreadable account refuses that
tick only.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 14: Live-state option rows, Alpaca P&L fields, close_position refusal

**Files:**
- Modify: `backend/live_broker_fetch.py`: `_alpaca_recent_trades` (245-273) and the `_fetch_alpaca` positions loop (286-307); add helpers before `_fetch_alpaca`.
- Modify: `backend/broker.py`:
  - add `_option_position_payload` directly before `_compute_live_state_snapshot` (8839)
  - its `live_position_dtos` loop (~8946-8972)
  - the `close_position` branch of `_execute_live_command` (~10025)
- Test: `backend/tests/test_swing_live_pnl.py` (create)

**Interfaces:**
- Consumes: Task 5 `PositionDTO` option rows (`asset_class="us_option"`, `side`, `multiplier`, `unrealized_pl`, `unrealized_plpc`, `current_price`, `underlying`, `strike`, `expiry`).
- Produces: the live-state row shape in Contract additions 16 (interfaces doc section 9, item 4). `broker.py` gains `_option_position_payload(p) -> dict`; `live_broker_fetch` gains `_alpaca_position_payload(client, p) -> dict` and `_option_meta(client, symbol) -> dict`.

- [ ] **Step 0: Impact analysis**

```bash
grep -rn "_fetch_alpaca\|_alpaca_recent_trades\|_compute_live_state_snapshot" backend --include=*.py | grep -v "/tests/"
```

Run `mcp__gitnexus__impact` upstream on `_fetch_alpaca` and `fetch_broker_live_state` (callers: `interactive_utils.py`, the API). Risk:
- **MEDIUM** for `live_broker_fetch`. Its rows for alpaca-main gain keys, and their P&L now comes from Alpaca's own fields. This is display only and happens in the API process.
- **LOW** for `broker.py`. EB's equity rows are built by the unchanged branch.

Tell the user that alpaca-main's UI P&L source changes to Alpaca's own numbers.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_live_pnl.py`:

```python
"""swing-port Task 14: live-state rows carry option fields and Alpaca's own
P&L (interfaces doc section 9 item 4); close_position refuses contracts."""
import datetime as datetime_module
from datetime import date
from types import SimpleNamespace

import live_broker_fetch as fetch
from swing_broker_harness import extract, function_source

OCC = "APH261009P00130000"


def _enum(value):
    return SimpleNamespace(value=value)


class _Client:
    def __init__(self):
        self.lookups = []

    def get_option_contract(self, symbol):
        self.lookups.append(symbol)
        return SimpleNamespace(underlying_symbol="APH", strike_price=130.0,
                               expiration_date=date(2026, 10, 9))


def test_an_alpaca_option_row_carries_contract_fields_and_alpaca_pnl():
    fetch._OPTION_META_CACHE.clear()
    client = _Client()
    row = fetch._alpaca_position_payload(client, SimpleNamespace(
        symbol=OCC, qty="-1", asset_class=_enum("us_option"),
        side=_enum("short"), avg_entry_price="1.23", market_value="-110",
        current_price="1.10", unrealized_pl="13", unrealized_plpc="0.1057"))
    assert row == {
        "symbol": OCC, "qty": -1.0, "avg_entry_price": 1.23,
        "last_price": 1.10, "market_value": -110.0, "unrealized_pnl": 13.0,
        "unrealized_pnl_pct": 10.57, "asset_class": "us_option",
        "side": "short", "multiplier": 100, "underlying": "APH",
        "strike": 130.0, "expiry": "2026-10-09"}
    fetch._alpaca_position_payload(client, SimpleNamespace(
        symbol=OCC, qty="-1", asset_class="us_option"))
    assert client.lookups == [OCC]


def test_an_option_row_without_alpaca_prices_is_null_not_zero():
    fetch._OPTION_META_CACHE.clear()
    row = fetch._alpaca_position_payload(_Client(), SimpleNamespace(
        symbol=OCC, qty="-1", asset_class="us_option", avg_entry_price="1.23"))
    assert (row["last_price"], row["market_value"], row["unrealized_pnl"],
            row["unrealized_pnl_pct"]) == (None, None, None, None)


def test_an_equity_row_prefers_alpaca_and_falls_back_to_the_old_derivation():
    alpaca = fetch._alpaca_position_payload(_Client(), SimpleNamespace(
        symbol="tqqq", qty="10", avg_entry_price="80", market_value="880",
        current_price="88.5", unrealized_pl="85", unrealized_plpc="0.10625"))
    assert (alpaca["last_price"], alpaca["unrealized_pnl"],
            alpaca["asset_class"], alpaca["side"], alpaca["multiplier"]) == (
        88.5, 85.0, "us_equity", "long", 1)
    assert abs(alpaca["unrealized_pnl_pct"] - 10.625) < 1e-9
    legacy = fetch._alpaca_position_payload(_Client(), SimpleNamespace(
        symbol="TQQQ", qty="10", avg_entry_price="80", market_value="880"))
    assert legacy["last_price"] == 88.0 and legacy["unrealized_pnl"] == 80.0
    assert abs(legacy["unrealized_pnl_pct"] - 10.0) < 1e-9


def test_recent_trades_carry_their_asset_class():
    filled = SimpleNamespace(
        status=_enum("filled"), side=_enum("sell"), symbol=OCC,
        filled_at=None, submitted_at=None, filled_qty="1",
        filled_avg_price="1.23", id="o-1", asset_class=_enum("us_option"))
    legacy = SimpleNamespace(
        status="filled", side="buy", symbol="TQQQ", filled_at=None,
        submitted_at=None, filled_qty="2", filled_avg_price="88", id="o-2")
    client = SimpleNamespace(get_orders=lambda filter=None: [filled, legacy])
    rows = fetch._alpaca_recent_trades(client)
    assert [r["asset_class"] for r in rows] == ["us_option", "us_equity"]


def test_the_broker_snapshot_builds_option_rows_from_alpaca_fields():
    payload = extract(("_option_position_payload",))["_option_position_payload"]
    row = payload(SimpleNamespace(
        symbol=OCC, qty=-1.0, avg_entry_price=1.23, market_value=-110.0,
        current_price=1.10, unrealized_pl=13.0, unrealized_plpc=0.1057,
        asset_class="us_option", side="short", multiplier=100,
        underlying="APH", strike=130.0, expiry="2026-10-09"))
    assert row["unrealized_pnl"] == 13.0 and row["multiplier"] == 100
    assert abs(row["unrealized_pnl_pct"] - 10.57) < 1e-9
    assert (row["side"], row["underlying"], row["strike"], row["expiry"]) == (
        "short", "APH", 130.0, "2026-10-09")
    blank = payload(SimpleNamespace(symbol=OCC, qty=-1.0, avg_entry_price=1.23,
                                    market_value=0.0, current_price=None,
                                    unrealized_pl=None, unrealized_plpc=None,
                                    asset_class="us_option", side="short",
                                    multiplier=100, underlying=None,
                                    strike=None, expiry=None))
    assert (blank["last_price"], blank["market_value"],
            blank["unrealized_pnl"], blank["unrealized_pnl_pct"]) == (
        None, None, None, None)


def test_the_snapshot_loop_diverts_only_option_rows():
    body = function_source("_compute_live_state_snapshot")
    divert = body.index('if getattr(p, "asset_class", None) == "us_option":')
    equity = body.index('sym = getattr(p, "symbol", "") or ""')
    assert divert < equity
    assert "_option_position_payload(p)" in body


def test_close_position_refuses_an_option_contract():
    ns = extract(("_execute_live_command",), check=(), namespace={
        "datetime": datetime_module, "instance_id": "swing-paper",
        "get_conn_retry": lambda **kw: None})

    class _Service:
        account_id = "acct-1"
        instance_id = "swing-paper"

        def enqueue(self, intent):
            raise AssertionError("an option contract reached the order service")

    adapter = SimpleNamespace(_positions={}, _option_positions={OCC: object()},
                              _last_prices={}, _market_marks=None)
    ok, error, _result = ns["_execute_live_command"](
        adapter, {"type": "close_position", "payload": {"symbol": OCC}},
        _Service())
    assert ok is False and "buy-to-close" in error
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_live_pnl.py -q -p no:cacheprovider`
Expected: FAIL. `AttributeError: module 'live_broker_fetch' has no attribute '_OPTION_META_CACHE'`, `KeyError: 'asset_class'`, `missing from broker.py: ['_option_position_payload']`, and the close_position error reads "no open long position".

- [ ] **Step 3: Write the minimal implementation**

(a) `backend/live_broker_fetch.py`. Directly before `def _fetch_alpaca(`, add:

```python
#: swing-port: contract fields per OCC symbol, cached for the process.
_OPTION_META_CACHE: dict[str, dict] = {}


def _num_or_none(value) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _enum_text(value, default: str = "") -> str:
    text = str(getattr(value, "value", value) or "").strip().lower()
    return text or default


def _option_meta(client, symbol: str) -> dict:
    """Contract fields for one OCC symbol from Alpaca (spec section 9 fix 10),
    cached for the process; {} when the lookup fails."""
    if symbol in _OPTION_META_CACHE:
        return _OPTION_META_CACHE[symbol]
    try:
        contract = client.get_option_contract(symbol)
    except Exception:
        return {}
    expiration = getattr(contract, "expiration_date", None)
    meta = {
        "underlying": str(getattr(contract, "underlying_symbol", "") or "").upper() or None,
        "strike": _num_or_none(getattr(contract, "strike_price", None)),
        "expiry": (expiration.isoformat() if hasattr(expiration, "isoformat")
                   else (str(expiration) if expiration else None)),
    }
    _OPTION_META_CACHE[symbol] = meta
    return meta


def _alpaca_position_payload(client, p) -> dict:
    """One live-state position row (interfaces doc section 9 item 4).

    P&L is Alpaca's own unrealized_pl / unrealized_plpc / current_price. When
    Alpaca omits one, an equity row falls back to the pre-port derivation
    and an option row reports null, never an invented 0.
    """
    symbol = str(getattr(p, "symbol", "") or "").upper()
    qty = float(getattr(p, "qty", 0.0) or 0.0)
    asset_class = _enum_text(getattr(p, "asset_class", None), "us_equity")
    is_option = asset_class == "us_option"
    avg_entry = _num_or_none(getattr(p, "avg_entry_price", None))
    market_value = _num_or_none(getattr(p, "market_value", None))
    price = _num_or_none(getattr(p, "current_price", None))
    unrealized = _num_or_none(getattr(p, "unrealized_pl", None))
    plpc = _num_or_none(getattr(p, "unrealized_plpc", None))
    unrealized_pct = plpc * 100.0 if plpc is not None else None
    if not is_option:
        if price is None and market_value is not None and qty:
            price = market_value / qty
        if unrealized is None and price and avg_entry:
            unrealized = (price - avg_entry) * qty
        if unrealized_pct is None and price and avg_entry:
            unrealized_pct = ((price / avg_entry) - 1.0) * 100.0
    row = {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": avg_entry if avg_entry else None,
        "last_price": price,
        "market_value": market_value,
        "unrealized_pnl": unrealized,
        "unrealized_pnl_pct": unrealized_pct,
        "asset_class": asset_class,
        "side": _enum_text(getattr(p, "side", None), "short" if qty < 0 else "long"),
        "multiplier": 100 if is_option else 1,
        "underlying": None,
        "strike": None,
        "expiry": None,
    }
    if is_option:
        row.update(_option_meta(client, symbol))
    return row
```

Replace the positions loop in `_fetch_alpaca` (from `    for p in positions:` through its `            continue`) with:

```python
    for p in positions:
        try:
            positions_payload.append(_alpaca_position_payload(client, p))
        except Exception:
            continue
```

In `_alpaca_recent_trades`, add to the appended dict after `"order_id": ...,`:

```python
                "asset_class": _enum_text(getattr(o, "asset_class", None), "us_equity"),
```

(b) `backend/broker.py`. Directly before `def _compute_live_state_snapshot(`, add:

```python
def _option_position_payload(p) -> dict:
    """One option row for the LiveState snapshot (interfaces doc section 9
    item 4): Alpaca's own P&L fields, null when Alpaca has no current price,
    never an invented 0. Equity rows never come through here."""
    def number(name):
        value = getattr(p, name, None)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    qty = float(getattr(p, "qty", 0.0) or 0.0)
    price = number("current_price")
    plpc = number("unrealized_plpc")
    avg = number("avg_entry_price")
    return {
        "symbol": getattr(p, "symbol", "") or "",
        "qty": qty,
        "avg_entry_price": avg if avg else None,
        "last_price": price,
        "market_value": number("market_value") if price is not None else None,
        "unrealized_pnl": number("unrealized_pl"),
        "unrealized_pnl_pct": plpc * 100.0 if plpc is not None else None,
        "asset_class": "us_option",
        "side": getattr(p, "side", None) or ("short" if qty < 0 else "long"),
        "multiplier": int(getattr(p, "multiplier", 100) or 100),
        "underlying": getattr(p, "underlying", None),
        "strike": getattr(p, "strike", None),
        "expiry": getattr(p, "expiry", None),
    }
```

In `_compute_live_state_snapshot`, replace

```python
            for p in live_position_dtos:
                sym = getattr(p, "symbol", "") or ""
```

with

```python
            for p in live_position_dtos:
                # swing-port: an option row carries its contract fields and
                # Alpaca's own P&L; every equity row is built exactly as before.
                if getattr(p, "asset_class", None) == "us_option":
                    if alpaca_equity <= 0:
                        equity += float(getattr(p, "market_value", 0.0) or 0.0)
                    positions_payload.append(_option_position_payload(p))
                    continue
                sym = getattr(p, "symbol", "") or ""
```

In the `close_position` branch of `_execute_live_command`, directly after

```python
            if not symbol:
                return (False, "close_position requires payload.symbol", {})
```

insert:

```python
            # swing-port: an option contract is closed by the wheel lane's
            # buy-to-close, never sold like a stock.
            if symbol in (getattr(adapter, "_option_positions", {}) or {}):
                return (False, f"{symbol} is an option contract; option "
                               "contracts are closed by buy-to-close, not "
                               "close_position", {})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_live_pnl.py backend/tests/test_alpaca_strict_consumers.py backend/tests/test_live_actions_survive_postgres_port.py backend/tests/test_manual_order_gate.py backend/tests/test_widget_accounts.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()`. Expected symbols: `_alpaca_recent_trades`, `_fetch_alpaca`, `_option_meta`, `_alpaca_position_payload`, `_num_or_none` and `_enum_text`, plus the broker functions in `git diff`.

```bash
git add backend/live_broker_fetch.py backend/broker.py backend/tests/test_swing_live_pnl.py
git commit -F - <<'EOF'
feat(live-state): option position rows with Alpaca's own P&L

Live-state rows served by the API carry asset_class, side, multiplier
and, for options, the underlying, strike and expiry from Alpaca's
contract data; P&L comes from Alpaca's unrealized_pl, unrealized_plpc
and current_price, with the old derivation as the equity fallback and
null, never zero, for an unpriced option. The broker's snapshot diverts
only option rows; EB's equity rows are built as before. Recent trades
carry their asset class, and close_position refuses option contracts.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 15: Approval command handler (LiveCommands submit_order from a swing approval)

**Files:**
- Modify: `backend/broker.py`: add `_lane_config`, `_approval_live_price` and `_execute_swing_approval` after `_wheel_options_refusal`; add a first-line route in the `submit_order` branch of `_execute_live_command` (~10096).
- Test: `backend/tests/test_swing_approval_command.py` (create)

**Interfaces:**
- Consumes:
  - plan B's `swing_trader.approvals.build_approved_order(signal, *, live_price, equity, cfg, adapter=None, today=None)`, and `swing_trader.signals_store.get_signal` / `update_signal` (interfaces doc section 6)
  - Task 9 `_build_bracket_intent`, Task 10 `_build_option_intent` and `_refresh_option_quote`
  - the existing `AlpacaAdapter.fetch_rest_quote_marks`, and Task 4 `get_latest_trades`
- Produces:
  - `_execute_swing_approval(adapter, payload, order_service, *, cached_strategies=None, now_utc=None, log=None) -> tuple[bool, str, dict]`
  - `_lane_config(cached_strategies, lane) -> dict`
  - `_approval_live_price(adapter, symbol) -> Optional[tuple[float, datetime]]`
  - interfaces doc section 7 behaviour: signal `status` becomes `submitted` or `failed` and `order_client_id` is written.

- [ ] **Step 0: Impact analysis**

```bash
grep -n 'if ctype == "submit_order":' backend/broker.py
```

`_execute_live_command` serves every operator command on alpaca-main. **Risk: MEDIUM.** The new route is entered only for `payload["source"] == "swing_approval"`, which nothing sends for EB. The existing manual `submit_order` tests pin the unchanged path.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_swing_approval_command.py`:

```python
"""swing-port Task 15: an approval rebuilds the order at the live price and
submits it through the order service (spec 6.1 broker item 9; interfaces
doc section 7). swing_trader (plan B) is stubbed here."""
import datetime as datetime_module
import sys
import threading
import types
from decimal import Decimal
from types import SimpleNamespace

import pytest

from broker_adapters.base import OptionSnapshotDTO
from live_orders import GateDecision, OrderSide, OrderSource
from live_orders.service import OrderSubmission
from swing_broker_harness import extract
from swing_live_fixtures import OCC, RTH

LANES = [{"strategy": "strategy_swing", "config": {"strategy_swing_enabled": True}},
         {"strategy": "strategy_wheel", "config": {"strategy_wheel_enabled": True}}]


@pytest.fixture
def swing(monkeypatch):
    signals, updates, built = {}, [], []

    def build_approved_order(signal, *, live_price, equity, cfg, adapter=None,
                             today=None):
        built.append({"live_price": live_price, "equity": equity,
                      "today": today, "cfg": cfg})
        if signal.get("explode"):
            raise ValueError("stale proposal")
        if signal["lane"] == "swing":
            return {"kind": "equity_bracket", "symbol": signal["symbol"],
                    "qty": 6, "take_profit_price": 109.0,
                    "stop_loss_price": 94.0}
        return {"kind": "option", "underlying": "APH", "contract": OCC,
                "option_type": "put", "strike": 130.0, "expiry": "2026-10-09",
                "position_intent": "sell_to_open", "qty": 1,
                "order_type": "limit", "limit_price": 1.2, "tif": "day",
                "reason": "wheel_sto_put", "signal_id": signal["id"]}

    approvals = types.ModuleType("swing_trader.approvals")
    approvals.build_approved_order = build_approved_order
    store = types.ModuleType("swing_trader.signals_store")
    store.get_signal = lambda signal_id: signals.get(signal_id)
    store.update_signal = lambda signal_id, patch: updates.append(
        (signal_id, patch))
    package = types.ModuleType("swing_trader")
    package.__path__ = []
    package.approvals = approvals
    package.signals_store = store
    monkeypatch.setitem(sys.modules, "swing_trader", package)
    monkeypatch.setitem(sys.modules, "swing_trader.approvals", approvals)
    monkeypatch.setitem(sys.modules, "swing_trader.signals_store", store)
    return SimpleNamespace(signals=signals, updates=updates, built=built)


def _signal(**changes):
    values = {"id": "sig-1", "instance_id": "instance-1", "lane": "swing",
              "symbol": "AAPL", "status": "approved"}
    values.update(changes)
    return values


class _Service:
    account_id = "acct-1"
    instance_id = "instance-1"

    def __init__(self, allowed=True):
        self.allowed = allowed
        self.intents = []

    def enqueue(self, intent):
        self.intents.append(intent)
        decision = GateDecision(
            allowed=self.allowed,
            approved_quantity=intent.quantity if self.allowed else Decimal("0"),
            reason_codes=() if self.allowed else ("quote.stale",),
            idempotency_key=intent.idempotency_key)
        reference = SimpleNamespace(broker_order_id="b-1") if self.allowed else None
        return OrderSubmission(decision=decision, reference=reference)


class _Adapter:
    _account_equity = 60000.0

    def __init__(self):
        self.rest = []
        self._market_marks = SimpleNamespace(
            get=lambda symbol: SimpleNamespace(price=100.5, observed_at=RTH))

    def fetch_rest_quote_marks(self, symbols):
        self.rest.append(list(symbols))
        return tuple(symbols)

    def get_latest_trades(self, symbols):
        return {}

    def get_option_snapshots(self, contracts):
        return {c: OptionSnapshotDTO(c, 1.1, 1.3, 1.2, None, None, None, None,
                                     None, RTH.isoformat()) for c in contracts}


def _approve():
    return extract(
        ("_execute_swing_approval", "_lane_config", "_approval_live_price",
         "_build_bracket_intent", "_build_option_intent",
         "_refresh_option_quote", "_truthy", "_merged_strategy_settings"),
        assigns=("_live_option_quotes", "_LANE_ENABLE_FLAGS"),
        namespace={"datetime": datetime_module,
                   "_live_order_dependency_lock": threading.Lock(),
                   "_live_order_dependency_state": {"risk_snapshot_id": "risk-9"}},
        check=("_execute_swing_approval", "_lane_config",
               "_approval_live_price"))["_execute_swing_approval"]


def _run(service=None, *, lanes=LANES, payload=None):
    service = service or _Service()
    result = _approve()(_Adapter(), payload or {"source": "swing_approval",
                                                "signal_id": "sig-1"},
                        service, cached_strategies=lanes, now_utc=RTH)
    return service, result


def test_a_swing_approval_places_a_manual_bracket_at_the_live_price(swing):
    swing.signals["sig-1"] = _signal()
    service, (ok, error, result) = _run()
    assert ok is True and error == ""
    (intent,) = service.intents
    assert (intent.source, intent.side, intent.quantity, intent.order_class,
            intent.tif) == (OrderSource.MANUAL, OrderSide.BUY, Decimal("6"),
                            "bracket", "gtc")
    assert (intent.take_profit_price, intent.stop_loss_price) == (
        Decimal("109.00"), Decimal("94.00"))
    assert intent.reason == "swing_approval:sig-1" and intent.quote_at == RTH
    assert swing.built[0]["live_price"] == 100.5
    assert swing.built[0]["today"] == datetime_module.date(2026, 10, 5)
    assert swing.updates == [("sig-1", {"status": "submitted",
                                        "order_client_id": intent.idempotency_key})]
    assert result["client_order_id"] == intent.idempotency_key


def test_a_wheel_approval_places_a_manual_sell_to_open(swing):
    swing.signals["sig-1"] = _signal(lane="wheel", symbol="APH")
    service, (ok, _error, _result) = _run()
    assert ok is True
    (intent,) = service.intents
    assert (intent.symbol, intent.position_intent, intent.source,
            intent.contract_multiplier) == (OCC, "sell_to_open",
                                            OrderSource.MANUAL, 100)


@pytest.mark.parametrize("signal,lanes,needle", [
    (_signal(status="pending"), LANES, "not approved"),
    (_signal(status="submitted"), LANES, "not approved"),
    (_signal(instance_id="alpaca-main"), LANES, "another instance"),
    (_signal(lane="scalp"), LANES, "unknown lane"),
    (_signal(), [], "not enabled"),
])
def test_an_unapprovable_signal_places_nothing(swing, signal, lanes, needle):
    swing.signals["sig-1"] = signal
    service, (ok, error, _result) = _run(lanes=lanes)
    assert ok is False and needle in error
    assert service.intents == [] and swing.updates == []


def test_an_unknown_signal_places_nothing(swing):
    service, (ok, error, _result) = _run()
    assert ok is False and "unknown signal" in error and service.intents == []


def test_a_gate_refusal_marks_the_signal_failed(swing):
    swing.signals["sig-1"] = _signal()
    service, (ok, error, result) = _run(_Service(allowed=False))
    assert ok is False and error == "order gate blocked: quote.stale"
    assert swing.updates[0][1]["status"] == "failed"


def test_a_rebuild_failure_marks_the_signal_failed(swing):
    swing.signals["sig-1"] = _signal(explode=True)
    service, (ok, error, _result) = _run()
    assert ok is False and "stale proposal" in error and service.intents == []
    assert swing.updates == [("sig-1", {"status": "failed",
                                        "order_client_id": None})]


def test_the_live_command_routes_swing_approvals_first():
    seen = []
    ns = extract(("_execute_live_command",), check=(), namespace={
        "datetime": datetime_module, "instance_id": "instance-1",
        "get_conn_retry": lambda **kw: None,
        "_execute_swing_approval":
            lambda adapter, payload, order_service, **kw: seen.append(payload)
            or (True, "", {"routed": True}),
    })
    ok, _error, result = ns["_execute_live_command"](
        object(), {"type": "submit_order",
                   "payload": {"source": "swing_approval", "signal_id": "s"}},
        _Service())
    assert ok is True and result == {"routed": True}
    assert seen == [{"source": "swing_approval", "signal_id": "s"}]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_approval_command.py -q -p no:cacheprovider`
Expected: FAIL with `AssertionError: missing from broker.py: ['_approval_live_price', '_execute_swing_approval', '_lane_config']`. The route test fails with "submit_order: side must be buy or sell".

- [ ] **Step 3: Write the minimal implementation**

In `backend/broker.py`, directly after `_wheel_options_refusal` ends, which is directly before `def _live_order_dependency_snapshot(adapter, intent):`, add:

```python
def _lane_config(cached_strategies, lane) -> dict:
    """The merged settings (conditions UNION config) of the first ENABLED
    spec for ``lane``, or {}."""
    flag = _LANE_ENABLE_FLAGS.get(str(lane or "").strip().lower())
    if flag is None:
        return {}
    for spec in (cached_strategies or []):
        try:
            name = str((spec or {}).get("strategy", "")).strip().lower()
            if _LANE_ENABLE_FLAGS.get(name) != flag:
                continue
            merged = _merged_strategy_settings(spec)
            if _truthy(merged.get(flag, False)):
                return dict(merged)
        except Exception:
            continue
    return {}


def _approval_live_price(adapter, symbol):
    """(price, quote_at) for an approval, fresh: a REST quote mark first (the
    gate trusts that source), then the IEX latest trade. None when neither."""
    wanted = str(symbol or "").strip().upper()
    if not wanted:
        return None
    try:
        adapter.fetch_rest_quote_marks([wanted])
    except Exception:
        pass
    book = getattr(adapter, "_market_marks", None)
    mark = book.get(wanted) if book is not None else None
    if mark is not None and float(getattr(mark, "price", 0) or 0) > 0:
        return float(mark.price), mark.observed_at
    try:
        trades = adapter.get_latest_trades([wanted]) or {}
    except Exception:
        return None
    hit = trades.get(wanted)
    if not hit:
        return None
    price, stamp = hit
    try:
        stamp_dt = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp_dt.tzinfo is None:
        stamp_dt = stamp_dt.replace(tzinfo=datetime.timezone.utc)
    return float(price), stamp_dt


def _execute_swing_approval(adapter, payload, order_service, *,
                            cached_strategies=None, now_utc=None, log=None):
    """A LiveCommands submit_order carrying {"source": "swing_approval",
    "signal_id"} (spec 6.1 broker item 9; interfaces doc section 7).

    Only an approved signal of this instance, on an enabled lane, is placed.
    The order is rebuilt at the live price by swing_trader.approvals (stop,
    target and shares for swing; expiry and strike for the wheel, spec
    section 9 fix 2), built as a MANUAL-source intent and sent through the
    unified order service. The signal is then marked submitted or failed with
    the client order id, so a repeated command finds it no longer approved.
    """
    from zoneinfo import ZoneInfo
    from live_orders import OrderSource

    def say(message, color="white"):
        if log is not None:
            try:
                log(message, color)
            except Exception:
                pass

    signal_id = str((payload or {}).get("signal_id") or "").strip()
    if not signal_id:
        return (False, "swing_approval requires signal_id", {})
    if order_service is None:
        return (False, "unified live order service unavailable", {})
    try:
        from swing_trader import approvals, signals_store
    except Exception as exc:
        return (False, f"swing_trader unavailable: {type(exc).__name__}: {exc}", {})
    signal = signals_store.get_signal(signal_id)
    if not signal:
        return (False, f"unknown signal {signal_id}", {})
    if str(signal.get("instance_id") or "") != str(order_service.instance_id):
        return (False, f"signal {signal_id} belongs to another instance", {})
    status = str(signal.get("status") or "")
    if status not in ("approved", "approved_half"):
        return (False, f"signal {signal_id} is {status or 'unset'}, not approved", {})
    lane_name = {"swing": "strategy_swing", "wheel": "strategy_wheel"}.get(
        str(signal.get("lane") or "").strip().lower())
    if lane_name is None:
        return (False, f"signal {signal_id} has an unknown lane "
                       f"{signal.get('lane')!r}", {})
    cfg = _lane_config(cached_strategies, lane_name)
    if not cfg:
        return (False, f"the {lane_name} lane is not enabled on this document", {})
    now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc)
    symbol = str(signal.get("symbol") or "").strip().upper()
    live = _approval_live_price(adapter, symbol)
    if live is None:
        return (False, f"no live price for {symbol}", {})
    live_price, quote_at = live
    equity = getattr(adapter, "_account_equity", None)
    if equity is None:
        try:
            equity = adapter.refresh_account().equity
        except Exception as exc:
            return (False, f"account equity unavailable ({type(exc).__name__}: {exc})", {})
    with _live_order_dependency_lock:
        risk_id = str(_live_order_dependency_state.get("risk_snapshot_id")
                      or "risk:unavailable")
    intent = None
    try:
        order = approvals.build_approved_order(
            signal, live_price=float(live_price), equity=float(equity), cfg=cfg,
            adapter=adapter,
            today=now_utc.astimezone(ZoneInfo("America/New_York")).date())
        kind = str((order or {}).get("kind") or "")
        if kind == "equity_bracket":
            intent = _build_bracket_intent(
                order_service, symbol=str(order["symbol"]).strip().upper(),
                price=live_price, decision_at=now_utc, quantity=order["qty"],
                bracket={"take_profit_price": order["take_profit_price"],
                         "stop_loss_price": order["stop_loss_price"]},
                risk_snapshot_id=risk_id, quote_at=quote_at,
                source=OrderSource.MANUAL, reason=f"swing_approval:{signal_id}")
        elif kind == "option":
            quote = _refresh_option_quote(adapter, order.get("contract"), now_utc)
            if quote is None:
                raise ValueError(
                    f"no usable options snapshot for {order.get('contract')}")
            intent = _build_option_intent(
                order_service, order, quote_at=quote["quote_at"],
                decision_at=now_utc, risk_snapshot_id=risk_id,
                source=OrderSource.MANUAL)
        else:
            raise ValueError(f"unknown approved order kind {kind!r}")
        submission = order_service.enqueue(intent)
    except Exception as exc:
        try:
            signals_store.update_signal(signal_id, {
                "status": "failed",
                "order_client_id": getattr(intent, "idempotency_key", None)})
        except Exception:
            pass
        say(f"[swing] approval {signal_id} failed: {type(exc).__name__}: {exc}", "red")
        return (False, f"swing approval failed: {type(exc).__name__}: {exc}", {})
    allowed = bool(submission.decision.allowed)
    try:
        signals_store.update_signal(signal_id, {
            "status": "submitted" if allowed else "failed",
            "order_client_id": intent.idempotency_key})
    except Exception as exc:
        say(f"[swing] approval {signal_id}: signal write-back failed "
            f"({type(exc).__name__}: {exc})", "yellow")
    result = {
        "signal_id": signal_id,
        "client_order_id": intent.idempotency_key,
        "order_id": getattr(submission.reference, "broker_order_id", None),
        "reason_codes": list(submission.decision.reason_codes),
    }
    if not allowed:
        return (False, "order gate blocked: "
                + ",".join(submission.decision.reason_codes), result)
    if not submission.accepted:
        return (False, "order outcome unknown; the next reconcile resolves it", result)
    return (True, "", result)
```

In `_execute_live_command`, directly before `        if ctype == "submit_order":`, insert:

```python
        if ctype == "submit_order" and str(payload.get("source") or "") == "swing_approval":
            # swing-port (spec 6.1 broker item 9; interfaces doc section 7):
            # an operator approval from web or iOS. Nothing else sends this
            # source, so every other submit_order takes the manual path below.
            return _execute_swing_approval(
                adapter, payload, order_service,
                cached_strategies=globals().get("_cached_strategies"),
                log=globals().get("_log"))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_approval_command.py backend/tests/test_manual_order_gate.py -q -p no:cacheprovider`
Expected: all pass.

- [ ] **Step 5: Detect changes and commit**

```bash
git add backend/broker.py backend/tests/test_swing_approval_command.py
git commit -F - <<'EOF'
feat(broker): approval commands place swing and wheel orders

A LiveCommands submit_order carrying a swing_approval source loads the
signal, refuses anything not approved, not this instance's or on a
disabled lane, rebuilds the order at the live price through
swing_trader.approvals, and submits it as a manual-source bracket or
option intent through the unified order service. The signal is marked
submitted or failed with its client order id. Every other submit_order
takes the unchanged manual path.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---

### Task 16 (run LAST, after plans A-backtest, B and C have landed): deploy fingerprint lists and the full suite

**Files:**
- Modify: `scripts/check_deployed_code.py:27-47` (`FILES`)
- Modify: `backend/api/main.py:1061-1107` (`_CODE_FINGERPRINT_FILES`)
- Test: `backend/tests/test_swing_deploy_check.py` (create)

**Interfaces:**
- Consumes: every file from every swing-port plan.
- Produces: fingerprinted paths. `python3 scripts/check_deployed_code.py` then proves that a deploy carries the swing port.

- [ ] **Step 1: Establish the real file list**

```bash
git log --oneline main..HEAD
git diff --name-only main...HEAD -- backend | grep '\.py$' | grep -v '^backend/tests/' | sort
```

The expected list is below. Reconcile it with the command's output:
- a changed backend `.py` file missing from `EXPECTED` gets added to `EXPECTED` and to both lists;
- a file in `EXPECTED` that does not exist on the branch is removed and named in the task report as a gap in its plan.

**If plans A-backtest, B and C have not landed yet** (`backend/swing_trader/` is absent), STOP and report. Listing a file that does not exist makes `scripts/check_deployed_code.py` crash on `open()`.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_swing_deploy_check.py`:

```python
"""swing-port Task 16: every backend file the swing port adds or changes is
fingerprinted on BOTH sides of the deploy check (spec section 12). A push
that changed only one of these must not read as deployed."""
import ast
import os

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ROOT = os.path.dirname(_BACKEND)

EXPECTED = (
    # plan A-live
    "backend/live_orders/types.py",
    "backend/live_orders/store.py",
    "backend/live_orders/gate.py",
    "backend/live_orders/service.py",
    "backend/live_orders/reconcile.py",
    "backend/broker_adapters/base.py",
    "backend/broker_adapters/errors.py",
    "backend/live_pending_orders.py",
    "backend/live_risk_state.py",
    "backend/live_broker_fetch.py",
    # plan A-backtest (its own Task adds these three to both lists)
    "backend/simulated_execution.py",
    "backend/portfolio_emulator.py",
    "backend/backtest_bar_events.py",
    # plan B
    "backend/strategies/strategy_swing.py",
    "backend/strategies/strategy_wheel.py",
    "backend/swing_trader/__init__.py",
    "backend/swing_trader/constants.py",
    "backend/swing_trader/indicators.py",
    "backend/swing_trader/signals.py",
    "backend/swing_trader/regime.py",
    "backend/swing_trader/universe.py",
    "backend/swing_trader/sectors.py",
    "backend/swing_trader/wheel_rules.py",
    "backend/swing_trader/ai_analyst.py",
    "backend/swing_trader/market_data.py",
    "backend/swing_trader/iv.py",
    "backend/swing_trader/calibration.py",
    "backend/swing_trader/approvals.py",
    "backend/swing_trader/signals_store.py",
    "backend/db/schema.py",
    "backend/notification_types.py",
    # plan B's API routes (plan C changes no backend file)
    "backend/interactive_utils.py",
)


def _literal(path, name):
    for node in ast.parse(open(path).read()).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found in {path}")


def _checked():
    return _literal(os.path.join(_ROOT, "scripts", "check_deployed_code.py"),
                    "FILES")


def _served():
    return _literal(os.path.join(_BACKEND, "api", "main.py"),
                    "_CODE_FINGERPRINT_FILES")


def test_every_swing_port_backend_file_is_fingerprinted():
    checked, served = set(_checked()), set(_served())
    missing = [p for p in EXPECTED
               if p not in checked or p[len("backend/"):] not in served]
    assert not missing, missing


def test_every_fingerprinted_file_exists():
    absent = [p for p in _checked() if not os.path.exists(os.path.join(_ROOT, p))]
    assert not absent, absent
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python3 -m pytest backend/tests/test_swing_deploy_check.py -q -p no:cacheprovider`
Expected: FAIL. `test_every_swing_port_backend_file_is_fingerprinted` lists all of `EXPECTED`.

- [ ] **Step 4: Write the minimal implementation**

In `scripts/check_deployed_code.py`, append to `FILES` (before the closing parenthesis) every `EXPECTED` path that is not already listed. Plan A-backtest's own task already adds `simulated_execution.py`, `portfolio_emulator.py` and `backtest_bar_events.py` to both lists, so they are left out below. A duplicate entry would widen both keys to full paths, which is harmless but noisy. As of 2026-09-24 the paths to add are:

```python
    # 2026-09-24 swing port: the live and paper order path (types, gate,
    # service, reconcile, adapter contract, guards), both strategies with
    # their ported package, the new tables and notification types, and the
    # approval API. (The backtest simulator files are plan A-backtest's.) A push that changed only one
    # of these must not read as deployed.
    "backend/live_orders/types.py",
    "backend/live_orders/store.py",
    "backend/live_orders/gate.py",
    "backend/live_orders/service.py",
    "backend/live_orders/reconcile.py",
    "backend/broker_adapters/base.py",
    "backend/broker_adapters/errors.py",
    "backend/live_pending_orders.py",
    "backend/live_risk_state.py",
    "backend/live_broker_fetch.py",
    "backend/strategies/strategy_swing.py",
    "backend/strategies/strategy_wheel.py",
    "backend/swing_trader/__init__.py",
    "backend/swing_trader/constants.py",
    "backend/swing_trader/indicators.py",
    "backend/swing_trader/signals.py",
    "backend/swing_trader/regime.py",
    "backend/swing_trader/universe.py",
    "backend/swing_trader/sectors.py",
    "backend/swing_trader/wheel_rules.py",
    "backend/swing_trader/ai_analyst.py",
    "backend/swing_trader/market_data.py",
    "backend/swing_trader/iv.py",
    "backend/swing_trader/calibration.py",
    "backend/swing_trader/approvals.py",
    "backend/swing_trader/signals_store.py",
    "backend/db/schema.py",
    "backend/notification_types.py",
    "backend/interactive_utils.py",
```

In `backend/api/main.py`, append the same paths without the `backend/` prefix to `_CODE_FINGERPRINT_FILES` (before its closing parenthesis), with the same comment. Here too, skip any path that is already listed:

```python
    # 2026-09-24 swing port: see scripts/check_deployed_code.py. Both lists
    # must stay identical up to the backend/ prefix.
    "live_orders/types.py",
    "live_orders/store.py",
    "live_orders/gate.py",
    "live_orders/service.py",
    "live_orders/reconcile.py",
    "broker_adapters/base.py",
    "broker_adapters/errors.py",
    "live_pending_orders.py",
    "live_risk_state.py",
    "live_broker_fetch.py",
    "strategies/strategy_swing.py",
    "strategies/strategy_wheel.py",
    "swing_trader/__init__.py",
    "swing_trader/constants.py",
    "swing_trader/indicators.py",
    "swing_trader/signals.py",
    "swing_trader/regime.py",
    "swing_trader/universe.py",
    "swing_trader/sectors.py",
    "swing_trader/wheel_rules.py",
    "swing_trader/ai_analyst.py",
    "swing_trader/market_data.py",
    "swing_trader/iv.py",
    "swing_trader/calibration.py",
    "swing_trader/approvals.py",
    "swing_trader/signals_store.py",
    "db/schema.py",
    "notification_types.py",
    "interactive_utils.py",
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python3 -m pytest backend/tests/test_swing_deploy_check.py backend/tests/test_strategy_hx_broker_wiring.py -q -p no:cacheprovider && python3 -c "import sys; sys.argv=['x']; sys.path.insert(0,'scripts'); import check_deployed_code as c; print(len(c.local_hashes()))"`
Expected: the tests pass (`test_the_health_fingerprint_and_the_deploy_check_list_the_same_files` included), and the one-liner prints the file count without an exception.

- [ ] **Step 6: Run the full suite against the baseline**

Run: `python3 -m pytest backend/tests -q -p no:cacheprovider 2>&1 | tail -30`
Expected: every new swing-port test passes. The failures are EXACTLY the 19 pre-existing ones: `test_adv_exit_discipline_findings` 11, `test_core_sleeve_adversarial` 7, `test_zz_adversarial_sweep` 1. The pass count is 7,565 plus the tests the swing-port plans added. Any other failure is a regression: stop and fix it before committing.

- [ ] **Step 7: Detect changes and commit**

Run `mcp__gitnexus__detect_changes()`. Expected: `_CODE_FINGERPRINT_FILES` and `FILES` only.

```bash
git add scripts/check_deployed_code.py backend/api/main.py backend/tests/test_swing_deploy_check.py
git commit -F - <<'EOF'
chore(deploy-check): fingerprint every swing-port backend file

The deploy check and the health fingerprint both list the files the
swing port adds or changes across the live order path, the backtest
simulator, both strategies and their package, the new tables and
notification types, and the approval API, so a push that changed only
one of them cannot read as deployed.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_016ztXjLWXu61Dpp4XMU2jsS
EOF
```

---
## Self-review against the spec

### 1. Coverage of spec section 6.1

| Spec 6.1 requirement | Task |
|---|---|
| `OrderIntent` optional fields; identity includes a field only when non-default; store row round-trip defaults absent keys | 1 (EB keys, payload and stored row pinned) |
| `DependencySnapshot` negative `position_quantity` only for `us_option` | 1 |
| Gate options branch: regular hours only (09:30-16:00 ET, NYSE calendar) | 6 (rule), 10 (calendar read in the option snapshot) |
| Gate: sell-to-open puts in integer contracts, collateral `strike x 100 x qty <= cash - open short puts - pending STO` | 1 (whole contracts enforced by `OrderIntent`), 6 |
| Gate: per-underlying cap of 25% of equity with existing puts counted (fix 3) | 6, 10 |
| Gate: buy-to-close needs a short of at least `qty` | 6 |
| Gate: notional and max-order use `qty x price x 100` | 6 |
| Gate: option quotes from the options snapshot; a mark older than 60 s refreshed over REST | 10 (`_refresh_option_quote`) |
| Gate: equity direction rules unchanged | 6 (`test_an_equity_intent_never_enters_the_option_branch`) |
| Bracket BUY gated as a market BUY on the parent quantity; legs not gated | 7 and 9 (the parent goes through `submit`; legs are registered, never submitted) |
| Service: new fields reach the transport only when set | 7 (EB kwargs pinned) |
| `ConfirmedFill` applies `contract_multiplier` | 1, 7 |
| Leg lifecycle rows (source `bracket_leg`, parent cid) read back with `nested=True` | 3 (`get_order_with_legs`), 7, 9 (wiring and reconcile retry) |
| Reconcile/store: `held` and `pending_cancel` | 3 (stream), 7 (reconcile) |
| Short option with lineage owned; negative broker qty only for options | 7 |
| `base.py` non-abstract methods, DTOs, `AccountDTO` / `OrderRef` fields | 2 |
| alpaca: paginated contracts, indicative snapshots, activities through `_client.get` | 4 |
| alpaca: `submit_order` bracket and option kwargs; integer qty, no notional, no extended hours, no fractional retry; `_preflight_buy` skipped for buy-to-close | 3, 5 |
| alpaca: `_option_positions` kept separately; equity filter untouched | 5 |
| alpaca: `OptionsNotPermitted` rather than `FractionalNotAllowed` | 2, 5 |
| Options-level check (`>= 1`, red log, alert) | 13. **Deviation:** it runs on the first live tick that carries wheel orders, not at boot, because the strategy document is not loaded at boot (`broker.py:10930` runs before `load_strategies_from_db`). |
| broker item 1: `_nexus_option_orders` in metadata | 8 |
| broker item 2: `_execute_option_intents` after the stock loop | 10 |
| broker item 3: `_build_strategy_stock_intent(bracket=...)`: whole shares, gtc, bracket, no extended-hours conversion | 9 |
| broker item 4: `_cancel_bracket_legs_confirmed`, 10 s, then defer | 3 (confirm semantics), 9 |
| broker item 5: `ordered_today`, pending guard and EB helpers ignore held and child orders | 11. **Refinement:** `held` is ignored inside multi-leg classes, which is the only place Alpaca produces it. A `held` simple order still counts, so guards stay closed. |
| broker item 6: kill-level cancel and halt skip bracket legs and buy-to-close | 11 |
| broker item 7: lane registration with `defaults_by_lane` rows | 8 |
| broker item 8: OPASN/OPEXP/OPEXC poller, persisted cursor, assignment shares, `wheel_assignment` notification | 12 |
| broker item 9: `submit_order` approval commands | 15 |
| `live_broker_fetch` P&L and position fields | 14 (also the coordinator's section 9 item 4 shape) |
| Section 12: `check_deployed_code.py` FILES | 16 |

### 2. Engine side of spec section 9

| Item | Where it is implemented |
|---|---|
| **Fix 1** (no duplicate put) | Task 1 keys an option sell on contract and session. Task 6 refuses `option.already_short_contract`. Task 10 pins the rerun end to end (`test_a_rerun_minutes_later_is_deduped_not_resold`). Plan B still checks open puts before emitting. |
| **Fix 3** (collateral counts existing puts on the underlying) | Tasks 6 and 10. |
| **Fix 6** (monitor checks the contract type) | The monitor is plan B's. The engine carries `option_type` from Alpaca's contract fields (Task 5), and Task 6 refuses a sell-to-open call. |
| **Fix 8** (cash, not margin buying power) | Task 6 uses `available_cash`, and Task 10 feeds it from `adapter._cash`, never `_buying_power`. |
| **Fix 9** (full chain, all pages) | Task 4. A repeated token or the page cap raises rather than returning a partial chain. |
| **Fix 10** (Alpaca contract fields, not an OCC regex) | Task 4 (`_option_contract_dto`), Task 5 (`option_contract_meta` for positions), Task 14 (display rows). |

### 3. Placeholder scan

No "TBD", "TODO", "similar to Task N" or undefined helper. Every function a test extracts is defined in an earlier or the same task:
- `_build_bracket_intent`: Task 9
- `_refresh_option_quote`, `_build_option_intent`: Task 10
- `_lane_enabled`: Task 8
- `_lane_config`: Task 15

### 4. Type consistency

These signatures are used identically across tasks:
- `_execute_option_intents(option_orders, *, order_service, adapter, now_utc, risk_snapshot_id, refused_reason="", log=None)`
- `_build_option_intent(order_service, order, *, quote_at, decision_at, risk_snapshot_id, source=None)`
- `_refresh_option_quote(adapter, contract, now_utc, *, max_age_s=60.0)`
- `register_bracket_legs(parent_intent, parent_reference)`
- `cancel_orders_confirmed(order_ids, timeout_s=10.0, ...)`
- `record_external_fill(intent, *, broker_order_id, quantity, price, occurred_at, reason="")`
- `_wheel_options_refusal(adapter, cached_strategies, *, log=None, alert=None)`
- `get_option_activities(types, after)`

The `OptionPositionDTO` field names (`underlying`, `option_type`, `strike`, `expiry`, `qty`, `multiplier`) match the interfaces doc. The broker reads `.expiration` only from `OptionContractDTO`.

### 5. Review Focus coverage

| Review Focus | Test |
|---|---|
| 1 | Task 7, `test_leg_fill_before_registration_is_recovered_exactly_once` |
| 2 | Task 7, `test_partial_parent_owns_only_filled_shares` |
| 3 | Task 3, `test_a_leg_that_fills_during_the_cancel_is_not_confirmed`; Task 9, `test_unconfirmed_leg_cancel_defers_the_sell` |
| 4 | Task 5, `test_short_option_sign_survives_refresh_and_fills` |
| 5 | Task 6, `test_cash_exactly_equal_to_collateral_is_allowed_one_cent_short_is_not` |

### 6. Known limits, for the operator

1. **`live_kill_switch.py` still cancels every order**, bracket legs included, through Alpaca's cancel-all. That is the operator's emergency stop, not the drawdown "kill level" in spec 6.1 item 6, and it is out of this plan's scope. After using it on `swing-paper`, positions are unprotected until the swing lane re-enters.
2. **Registering legs assumes Alpaca gives every bracket leg a non-empty `client_order_id`.** This cannot be verified offline. Spec section 12 step 4 ("one bracket buy at 09:15 that fills at the open, with its legs visible") is the check. If a leg has no id, `bracket_leg_intent` raises, the parent still counts as accepted, and reconcile reports `lineage_position_missing_broker` after the leg fills. That shows up loudly as a red, unhealthy reconcile, never as silent drift.
3. **Changes that reach EB.** These are the only EB-reachable behaviour changes, and each is strictly less blocking or display-only:
   - a `pending_cancel` order in the reconcile snapshot now reconciles as acknowledged instead of raising `unsupported_broker_status`;
   - a `pending_cancel` stream event now dedupes against the existing ACK;
   - `OrderRef` objects carry `order_class="simple"`;
   - `refresh_positions` makes one extra `asset_class` check per row;
   - alpaca-main's API-served live-state rows gain keys, and their P&L numbers now come from Alpaca's own fields.
4. **Cross-plan dependencies:**
   - Plan B must re-emit a deferred exit (Contract additions 15).
   - Plan B must read `_engine_wheel_assignments`.
   - Plan B must register the `wheel_assignment` notification type.
   - Plan B must provide `swing_trader.approvals` and `signals_store` with the section 6 signatures.
   - Plan C's UI must default missing `asset_class`, `multiplier` and `side` on rows written by `broker.py`.

### 7. Dry run of this plan (2026-09-24)

Tasks 1-15 were applied to a scratch copy of `backend/` (not the repo) by a script that reads this document's own code blocks and anchors, one task at a time, in order.
- Every anchor this plan quotes exists exactly once in the current source.
- The patched files parse.
- The 237 new swing-port tests all pass.
- The EB and broker regression suites named in the tasks all pass.
- The full backend suite on the patched copy fails exactly the 19 pre-existing tests (`test_adv_exit_discipline_findings` 11, `test_core_sleeve_adversarial` 7, `test_zz_adversarial_sweep` 1). The only other failures were 6 caused by the copy itself: files it did not include (`.env.example`, `install.sh`, `install.ps1`, `.gitignore`).

Task 16 was not dry-run, because it depends on files that plans A-backtest and B create.
